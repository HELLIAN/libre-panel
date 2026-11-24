//! LibreHardwareMonitor through Libre Panel's sensor service (sensors/lhm_service.py, Windows).
//!
//! Where the sensor service is installed, it reads LibreHardwareMonitor with the rights that
//! the CPU's, the mainboard's, the memory's and the drives' sensors need, and Libre Panel asks
//! it over a local pipe. `ServiceSource` is that: a thread of its own asks the service (twice
//! in each of its rounds) and keeps the latest readings.
//!
//! - Only what a theme on show reads is kept up to date: each request names the hardware with
//!   a sensor that the themes read (by raw key, or by the friendly key it makes). The first
//!   request of a connection asks for everything once, so that every sensor is known.
//! - While it does not answer it is asked again with growing pauses; after `wait_seconds`
//!   `on_failure` is told, and `on_ready` when the service answers again. Before its first
//!   answer it is waited for `start_wait_seconds`: it starts a while after Windows.

use std::collections::BTreeSet;
use std::sync::{Arc, Condvar, Mutex, RwLock};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use indexmap::{IndexMap, IndexSet};
use lp_py::{Dict, Value};

use crate::lhm::{readings_from_sensors, LhmSensor, Readings, Sources};
use crate::protocol::{self, hello_request, parse_hello, parse_readings, read_request, PROTOCOL};

/// without an answer for this long, the provider reads another way meanwhile
pub const WAIT_SECONDS: f64 = 20.0;
/// ... before the service's first answer since the app started
pub const START_WAIT_SECONDS: f64 = 180.0;
/// one request and its answer
pub const REQUEST_SECONDS: f64 = 2.0;
/// pauses between tries while it does not answer: doubling up to
pub const RETRY_SECONDS: (f64, f64) = (1.0, 10.0);
pub const SERVICE_NAME: &str = "LibrePanelSensors";
pub const PIPE_NAME: &str = r"\\.\pipe\LibrePanelSensors";

/// The keys (and patterns) that the themes on show read (sensors/base.py's want and wanted).
#[derive(Clone, Default)]
pub struct Wanted(Arc<RwLock<Arc<BTreeSet<String>>>>);

impl Wanted {
    pub fn set(&self, keys: impl IntoIterator<Item = String>) {
        let keys: BTreeSet<String> = keys.into_iter().collect();
        if let Ok(mut held) = self.0.write() {
            if **held != keys {
                *held = Arc::new(keys);
            }
        }
    }

    /// wanted_keys: every key (or pattern); two of them are compared to see whether that
    /// changed.
    pub fn keys(&self) -> Arc<BTreeSet<String>> {
        self.0.read().map(|held| held.clone()).unwrap_or_default()
    }
}

/// wanted: whether a key starting with `prefix` is among `keys` (or a pattern such as `net.*`
/// that takes it in).
pub fn wanted(keys: &BTreeSet<String>, prefix: &str) -> bool {
    keys.iter().any(|key| {
        if key.starts_with(prefix) {
            return true;
        }
        let head = key.split(['*', '?', '[']).next().unwrap_or("");
        head != key && prefix.starts_with(head)
    })
}

/// Why the service could not be asked; the text says it.
#[derive(Clone, Debug, PartialEq)]
pub struct Unavailable(pub String);

impl From<protocol::Error> for Unavailable {
    fn from(e: protocol::Error) -> Unavailable {
        Unavailable(e.to_string())
    }
}

/// One connection to the service: a request, its answer.
pub trait Link: Send {
    fn request(&mut self, message: &Value, timeout: f64) -> Result<Dict, Unavailable>;
}

pub type Open = Box<dyn FnMut() -> Result<Box<dyn Link>, Unavailable> + Send>;

/// How LibreHardwareMonitor is read, and what keeps its CPU sensors away.
#[derive(Clone, Debug, PartialEq, Default)]
pub struct LhmStatus {
    /// "service" (the sensor service) or "webserver"
    pub mode: String,
    /// it runs with administrator rights (service)
    pub elevated: Option<bool>,
    /// the CPU's sensors give real values; none: no such sensors, or unknown
    pub ring0: Option<bool>,
    /// version of the installed PawnIO driver ("" unknown), none: none
    pub pawnio: Option<String>,
    /// why it could not start, or does not answer
    pub error: String,
    /// why the installed sensor service is not what is read now
    pub service: String,
    pub timing: Option<protocol::Timing>,
    /// the sensor service's version, once it said hello
    pub version: String,
    pub memory: Option<protocol::ServiceMemory>,
}

impl LhmStatus {
    pub fn of(mode: &str) -> LhmStatus {
        LhmStatus {
            mode: mode.into(),
            ..LhmStatus::default()
        }
    }
}

/// status_of: the status of the source after an answer.
pub fn status_of(readings: &protocol::Readings, version: &str) -> LhmStatus {
    // the service runs with the system's rights: what keeps the CPU's sensors away is PawnIO
    LhmStatus {
        mode: "service".into(),
        elevated: Some(true),
        ring0: readings.ring0,
        pawnio: readings.pawnio.clone(),
        error: if readings.state == "failed" {
            readings.error.clone()
        } else {
            String::new()
        },
        service: String::new(),
        timing: readings.timing.clone(),
        version: version.into(),
        memory: readings.memory.clone(),
    }
}

pub type OnReady = Box<dyn Fn(&LhmStatus) + Send>;
pub type OnFailure = Box<dyn Fn(&str) + Send>;

/// How a ServiceSource asks.
pub struct Options {
    pub poll_seconds: f64,
    pub stale_seconds: f64,
    pub gpu: Option<String>,
    pub wait_seconds: f64,
    pub start_wait_seconds: f64,
    pub wanted: Wanted,
    pub on_ready: Option<OnReady>,
    pub on_failure: Option<OnFailure>,
    /// what the thread says for the log
    pub log: Option<OnFailure>,
}

impl Default for Options {
    fn default() -> Options {
        Options {
            poll_seconds: 1.0,
            stale_seconds: 5.0,
            gpu: None,
            wait_seconds: WAIT_SECONDS,
            start_wait_seconds: START_WAIT_SECONDS,
            wanted: Wanted::default(),
            on_ready: None,
            on_failure: None,
            log: None,
        }
    }
}

#[derive(Default)]
struct Latest {
    readings: Readings,
    /// when the readings shown were read
    beat: Option<Instant>,
    status: LhmStatus,
}

struct Shared {
    latest: Mutex<Latest>,
    stale_seconds: f64,
    /// (stop, done): the thread is to end; the first readings are in, or it gave up for now
    flags: Mutex<(bool, bool)>,
    changed: Condvar,
}

impl Shared {
    fn stopped(&self) -> bool {
        self.flags.lock().map(|flags| flags.0).unwrap_or(true)
    }

    /// Waits `seconds`, or until the source is closed: true then.
    fn wait(&self, seconds: f64) -> bool {
        let Ok(flags) = self.flags.lock() else {
            return true;
        };
        let wait = Duration::from_secs_f64(seconds.max(0.0));
        self.changed
            .wait_timeout_while(flags, wait, |flags| !flags.0)
            .map(|(flags, _)| flags.0)
            .unwrap_or(true)
    }

    fn done(&self) {
        if let Ok(mut flags) = self.flags.lock() {
            flags.1 = true;
        }
        self.changed.notify_all();
    }
}

/// What the themes read and how much is known of the hardware: what `needed` is made of.
type Known = (Arc<BTreeSet<String>>, usize);

struct Worker {
    shared: Arc<Shared>,
    options: Options,
    open: Open,
    link: Option<Box<dyn Link>>,
    /// the next request asks for every piece once
    everything: bool,
    /// hardware id -> the friendly keys its sensors make
    keys: IndexMap<String, IndexSet<String>>,
    /// grows with each key or piece of hardware learned
    learned: usize,
    needed: Option<(Known, Vec<String>)>,
    /// whether the service answered once since this source started
    answered: bool,
    /// on_failure was called and nothing answered since
    failed: bool,
    first: bool,
    version: String,
}

impl Worker {
    fn log(&self, line: &str) {
        if let Some(log) = &self.options.log {
            log(line);
        }
    }

    fn run(&mut self) {
        let mut since = Instant::now(); // when the service last answered (or the start)
        let mut pause = RETRY_SECONDS.0;
        while !self.shared.stopped() {
            let started = Instant::now();
            if let Err(Unavailable(why)) = self.ask() {
                self.lost(&why, since);
                self.shared.wait(pause);
                pause = (pause * 2.0).min(RETRY_SECONDS.1);
                continue;
            }
            since = Instant::now();
            pause = RETRY_SECONDS.0;
            if self.failed {
                self.failed = false;
                self.log("the sensor service answers again");
                if let Some(on_ready) = &self.options.on_ready {
                    let status = self
                        .shared
                        .latest
                        .lock()
                        .map(|latest| latest.status.clone())
                        .unwrap_or_default();
                    on_ready(&status);
                }
            }
            let rest = self.options.poll_seconds - started.elapsed().as_secs_f64();
            self.shared.wait(rest.max(0.05));
        }
        self.link = None;
    }

    /// No answer: the connection goes, the status says why, and once the service has been
    /// silent for `wait_seconds` (`start_wait_seconds` before its first answer) the provider
    /// is told.
    fn lost(&mut self, why: &str, since: Instant) {
        self.link = None;
        if let Ok(mut latest) = self.shared.latest.lock() {
            latest.status = LhmStatus {
                error: why.into(),
                version: self.version.clone(),
                ..LhmStatus::of("service")
            };
        }
        let wait = if self.answered {
            self.options.wait_seconds
        } else {
            self.options.start_wait_seconds
        };
        if !self.failed && since.elapsed().as_secs_f64() >= wait {
            self.failed = true;
            self.log(&format!("the sensor service does not answer: {why}"));
            if let Some(on_failure) = &self.options.on_failure {
                on_failure(why);
            }
            self.shared.done();
        }
    }

    /// One round: connect (and say hello) if need be, ask for the readings, keep them.
    fn ask(&mut self) -> Result<(), Unavailable> {
        if self.link.is_none() {
            let mut link = (self.open)()?;
            let hello = parse_hello(&link.request(&hello_request(), REQUEST_SECONDS)?)?;
            if hello.protocol != PROTOCOL {
                return Err(Unavailable(format!(
                    "the sensor service speaks protocol {}, this Libre Panel {PROTOCOL}: set it \
                     up again from this version",
                    hello.protocol
                )));
            }
            self.link = Some(link);
            self.version = hello.service;
            self.everything = true;
        }
        let request = read_request(&self.needed(), self.everything);
        let Some(link) = self.link.as_mut() else {
            return Err(Unavailable("no connection".into()));
        };
        let answer = parse_readings(&link.request(&request, REQUEST_SECONDS)?)?;
        self.everything = false;
        self.answered = true; // (also while LibreHardwareMonitor starts in it: it is there)
        if answer.state == "failed" {
            return Err(Unavailable(format!(
                "LibreHardwareMonitor did not start in the service: {}",
                answer.error
            )));
        }
        self.take(&answer);
        Ok(())
    }

    fn take(&mut self, answer: &protocol::Readings) {
        let mut sources = Sources::new();
        let readings = readings_from_sensors(
            &answer.sensors,
            self.options.gpu.as_deref(),
            Some(&mut sources),
        );
        self.learn(&answer.sensors, &sources);
        // what the service read last may be old (nobody asked for a while): it reads anew now
        let stale = self.shared.stale_seconds;
        let fresh = answer.age.is_some_and(|age| stale <= 0.0 || age <= stale);
        let status = status_of(answer, &self.version);
        if let Ok(mut latest) = self.shared.latest.lock() {
            latest.status = status.clone();
            if fresh {
                latest.readings = readings;
                let age = Duration::from_secs_f64(answer.age.unwrap_or(0.0).max(0.0));
                latest.beat = Some(Instant::now().checked_sub(age).unwrap_or_else(Instant::now));
            }
        }
        if fresh && self.first {
            self.first = false;
            self.log(&format!(
                "LibreHardwareMonitor is read through the sensor service ({} sensors)",
                answer.sensors.len()
            ));
            if status.ring0 == Some(false) {
                self.log("the sensor service cannot read the CPU's sensors: PawnIO is missing");
            }
            if let Some(on_ready) = &self.options.on_ready {
                on_ready(&status);
            }
            self.shared.done();
        }
    }

    /// Which piece of hardware makes which friendly key; never forgotten (a sensor without a
    /// value now still belongs to its hardware).
    fn learn(&mut self, sensors: &[LhmSensor], sources: &Sources) {
        let mut hardware_of: IndexMap<&str, &str> = IndexMap::new();
        for sensor in sensors {
            hardware_of.insert(&sensor.sensor_id, &sensor.hardware_id);
            if !self.keys.contains_key(&sensor.hardware_id) {
                self.keys
                    .insert(sensor.hardware_id.clone(), IndexSet::new());
                self.learned += 1;
            }
        }
        for (key, ids) in sources {
            if key.starts_with("lhm:") {
                continue;
            }
            for id in ids {
                let Some(hardware) = hardware_of.get(id.as_str()) else {
                    continue;
                };
                if let Some(keys) = self.keys.get_mut(*hardware) {
                    if keys.insert(key.clone()) {
                        self.learned += 1;
                    }
                }
            }
        }
    }

    /// The hardware with a sensor that a theme on show reads (worked out again only when what
    /// the themes read, or what is known, has changed).
    fn needed(&mut self) -> Vec<String> {
        let state = (self.options.wanted.keys(), self.learned);
        match &self.needed {
            Some((held, found)) if Arc::ptr_eq(&held.0, &state.0) && held.1 == state.1 => {
                found.clone()
            }
            _ => {
                let found: Vec<String> = self
                    .keys
                    .iter()
                    .filter(|(hardware, keys)| {
                        wanted(&state.0, &format!("lhm:{hardware}/"))
                            || keys.iter().any(|key| wanted(&state.0, key))
                    })
                    .map(|(hardware, _)| hardware.clone())
                    .collect();
                self.needed = Some((state, found.clone()));
                found
            }
        }
    }
}

/// The sensor service, asked by a thread of its own.
pub struct ServiceSource {
    shared: Arc<Shared>,
    thread: Option<JoinHandle<()>>,
}

impl ServiceSource {
    /// The source started: `open` makes a connection to the service.
    pub fn start(mut options: Options, open: Open) -> ServiceSource {
        options.poll_seconds = options.poll_seconds.max(0.1);
        let stale = if options.stale_seconds > 0.0 {
            options.stale_seconds.max(3.0 * options.poll_seconds)
        } else {
            0.0
        };
        options.start_wait_seconds = options.start_wait_seconds.max(options.wait_seconds);
        let shared = Arc::new(Shared {
            latest: Mutex::new(Latest {
                status: LhmStatus::of("service"),
                ..Latest::default()
            }),
            stale_seconds: stale,
            flags: Mutex::new((false, false)),
            changed: Condvar::new(),
        });
        let mut worker = Worker {
            shared: shared.clone(),
            options,
            open,
            link: None,
            everything: true,
            keys: IndexMap::new(),
            learned: 0,
            needed: None,
            answered: false,
            failed: false,
            first: true,
            version: String::new(),
        };
        let thread = std::thread::Builder::new()
            .name("lhm-service".into())
            .spawn(move || worker.run())
            .ok();
        ServiceSource { shared, thread }
    }

    /// The latest readings; none where it was not read yet, or not lately (no numbers that
    /// stand still).
    pub fn read(&self) -> Readings {
        let Ok(latest) = self.shared.latest.lock() else {
            return Readings::new();
        };
        let stale = self.shared.stale_seconds;
        match latest.beat {
            Some(beat) if stale <= 0.0 || beat.elapsed().as_secs_f64() <= stale => {
                latest.readings.clone()
            }
            _ => Readings::new(),
        }
    }

    /// Whether the service gives readings now.
    pub fn live(&self) -> bool {
        !self.read().is_empty()
    }

    pub fn status(&self) -> LhmStatus {
        self.shared
            .latest
            .lock()
            .map(|latest| latest.status.clone())
            .unwrap_or_default()
    }

    /// Whether the first readings are in (or it gave up for now) within `timeout` seconds.
    pub fn wait_ready(&self, timeout: f64) -> bool {
        let Ok(flags) = self.shared.flags.lock() else {
            return false;
        };
        let wait = Duration::from_secs_f64(timeout.max(0.0));
        self.shared
            .changed
            .wait_timeout_while(flags, wait, |flags| !flags.1 && !flags.0)
            .map(|(flags, _)| flags.1 || flags.0)
            .unwrap_or(false)
    }

    /// Ends the thread; from the thread itself (a callback) it ends when the callback returns.
    pub fn close(&mut self) {
        if let Ok(mut flags) = self.shared.flags.lock() {
            flags.0 = true;
        }
        self.shared.changed.notify_all();
        if let Some(thread) = self.thread.take() {
            if thread.thread().id() != std::thread::current().id() {
                let _ = thread.join();
            }
        }
        self.shared.done();
    }
}

impl Drop for ServiceSource {
    fn drop(&mut self) {
        self.close();
    }
}

/// round_line: what the service's rounds take, in a line (`sensors --check-service`).
pub fn round_line(timing: Option<&protocol::Timing>) -> String {
    let Some(timing) = timing else {
        return "round time: not reported by this service version".into();
    };
    let mut said = Vec::new();
    for (what, done) in [("last round", &timing.last), ("all hardware", &timing.full)] {
        if let Some(done) = done {
            let parts = done.parts.len();
            said.push(format!(
                "{what} {:.1} ms ({parts} part{})",
                done.seconds() * 1000.0,
                if parts == 1 { "" } else { "s" }
            ));
        }
    }
    if said.is_empty() {
        "round time: no round yet".into()
    } else {
        format!("round time: {}", said.join(", "))
    }
}

/// check: whether the sensor service answers this account with readings, and what it says:
/// (worked, lines to print). `sleep` waits between two tries.
pub fn check(open: &mut Open, seconds: f64, sleep: &dyn Fn(f64)) -> (bool, Vec<String>) {
    let mut link = match open() {
        Ok(link) => link,
        Err(Unavailable(why)) => {
            return (
                false,
                vec![format!("The sensor service does not answer: {why}")],
            )
        }
    };
    let mut lines = Vec::new();
    let mut asked = || -> Result<protocol::Readings, Unavailable> {
        let hello = parse_hello(&link.request(&hello_request(), REQUEST_SECONDS)?)?;
        lines.push(format!(
            "sensor service {}, protocol {}",
            hello.service, hello.protocol
        ));
        let all = read_request(&[], true);
        let mut answer = parse_readings(&link.request(&all, REQUEST_SECONDS)?)?;
        let mut waited = 0.0;
        while answer.state == "starting" || answer.age.is_none_or(|age| age > 2.0) {
            if waited >= seconds {
                break;
            }
            sleep(0.5);
            waited += 0.5;
            answer = parse_readings(&link.request(&all, REQUEST_SECONDS)?)?;
        }
        Ok(answer)
    };
    let answer = match asked() {
        Ok(answer) => answer,
        Err(Unavailable(why)) => {
            return (
                false,
                vec![format!(
                    "The sensor service does not answer properly: {why}"
                )],
            )
        }
    };
    if answer.state == "failed" {
        lines.push(format!(
            "LibreHardwareMonitor did not start in it: {}",
            answer.error
        ));
        return (false, lines);
    }
    let readings = readings_from_sensors(&answer.sensors, None, None);
    let parts: IndexSet<&str> = answer
        .sensors
        .iter()
        .map(|s| s.hardware_id.as_str())
        .collect();
    let state = match answer.ring0 {
        Some(true) => "available",
        Some(false) => "not available",
        None => "no such sensors",
    };
    let pawnio = match answer.pawnio.as_deref() {
        None => "not installed",
        Some("") => "installed",
        Some(version) => version,
    };
    lines.push(format!(
        "read through the sensor service: {} sensors of {} parts",
        answer.sensors.len(),
        parts.len()
    ));
    lines.push(format!("CPU temperature, power and clock: {state}"));
    lines.push(format!("PawnIO driver: {pawnio}"));
    lines.push(round_line(answer.timing.as_ref()));
    for key in ["cpu.temp", "cpu.power", "gpu.temp", "mem.temp"] {
        if let Some(reading) = readings.get(key) {
            if let Value::Float(value) = reading.value {
                let line = format!("{key}: {value:.1} {}", reading.unit);
                lines.push(line.trim_end().to_string());
            }
        }
    }
    (!answer.sensors.is_empty(), lines)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A sensor service as the tests make it up: up or not, its protocol, and its answers to
    /// `read` in turn (the last one again and again).
    struct Service {
        up: bool,
        protocol: i64,
        answers: Vec<String>,
        /// (the hardware asked for, every piece) of each read
        asked: Vec<(Vec<String>, bool)>,
    }

    type Made = Arc<Mutex<Service>>;

    struct FakeLink(Made);

    impl Link for FakeLink {
        fn request(&mut self, message: &Value, _timeout: f64) -> Result<Dict, Unavailable> {
            let mut service = self.0.lock().unwrap();
            if !service.up {
                return Err(Unavailable("the pipe is gone".into()));
            }
            let Value::Dict(message) = message else {
                return Err(Unavailable("not a message".into()));
            };
            let text = match message.get("op").and_then(Value::as_str) {
                Some("hello") => format!(
                    r#"{{"v": 1, "ok": true, "service": "1.4.0", "protocol": {}}}"#,
                    service.protocol
                ),
                _ => {
                    let hardware = message.get("hardware").and_then(Value::as_list);
                    let hardware = hardware.unwrap_or(&[]).iter();
                    let ids = hardware.filter_map(|id| id.as_str().map(String::from));
                    let all = matches!(message.get("all"), Some(Value::Bool(true)));
                    service.asked.push((ids.collect(), all));
                    match service.answers.len() {
                        1 => service.answers[0].clone(),
                        _ => service.answers.remove(0),
                    }
                }
            };
            Ok(protocol::decode(text.as_bytes())?)
        }
    }

    fn service(answers: &[String]) -> Made {
        Arc::new(Mutex::new(Service {
            up: true,
            protocol: PROTOCOL,
            answers: answers.to_vec(),
            asked: Vec::new(),
        }))
    }

    fn opener(made: &Made) -> Open {
        let made = made.clone();
        Box::new(move || match made.lock().unwrap().up {
            true => Ok(Box::new(FakeLink(made.clone())) as Box<dyn Link>),
            false => Err(Unavailable("the service does not run".into())),
        })
    }

    const SENSORS: &str = r#"[["/amdcpu/0/temperature/2", "/amdcpu/0", "AMD Ryzen 7 7800X3D",
        "Temperature", "Core (Tctl/Tdie)", 51.5, "°C"], ["/gpu-nvidia/0/temperature/0",
        "/gpu-nvidia/0", "NVIDIA GeForce RTX 4070", "Temperature", "GPU Core", 48.0, "°C"]]"#;

    fn running(age: f64, ring0: &str, pawnio: &str, more: &str) -> String {
        format!(
            r#"{{"v": 1, "ok": true, "state": "running", "age": {age}, "ring0": {ring0},
            "pawnio": {pawnio}, "sensors": {SENSORS}{more}}}"#
        )
    }

    /// What the source said: its log, and what it told the provider.
    type Said = Arc<Mutex<Vec<String>>>;

    fn options(wanted: &[&str], wait: f64, start_wait: f64, said: &Said) -> Options {
        let keys = Wanted::default();
        keys.set(wanted.iter().map(|key| key.to_string()));
        let (log, failed, ready) = (said.clone(), said.clone(), said.clone());
        Options {
            poll_seconds: 0.1,
            stale_seconds: 0.3,
            wait_seconds: wait,
            start_wait_seconds: start_wait,
            wanted: keys,
            log: Some(Box::new(move |line| {
                log.lock().unwrap().push(line.to_string())
            })),
            on_failure: Some(Box::new(move |why| {
                failed.lock().unwrap().push(format!("failure: {why}"));
            })),
            on_ready: Some(Box::new(move |status| {
                ready
                    .lock()
                    .unwrap()
                    .push(format!("ready: {}", status.mode));
            })),
            ..Options::default()
        }
    }

    fn until(seconds: f64, done: impl Fn() -> bool) -> bool {
        let end = Instant::now() + Duration::from_secs_f64(seconds);
        while !done() {
            if Instant::now() > end {
                return false;
            }
            std::thread::sleep(Duration::from_millis(10));
        }
        true
    }

    fn value(readings: &Readings, key: &str) -> Option<f64> {
        match readings.get(key).map(|reading| &reading.value) {
            Some(Value::Float(value)) => Some(*value),
            _ => None,
        }
    }

    #[test]
    fn the_readings_come_through_the_service_and_only_the_hardware_a_theme_reads_is_asked_for() {
        let made = service(&[running(0.1, "true", r#""2.0.1""#, "")]);
        let said = Said::default();
        let options = options(&["cpu.temp"], 20.0, 180.0, &said);
        let wanted = options.wanted.clone();
        let mut source = ServiceSource::start(options, opener(&made));
        assert!(source.wait_ready(5.0));
        let readings = source.read();
        assert_eq!(value(&readings, "cpu.temp"), Some(51.5));
        assert_eq!(value(&readings, "gpu.temp"), Some(48.0));
        assert_eq!(value(&readings, "lhm:/amdcpu/0/temperature/2"), Some(51.5));
        let status = source.status();
        assert_eq!(
            (status.mode.as_str(), status.version.as_str()),
            ("service", "1.4.0")
        );
        assert_eq!((status.elevated, status.ring0), (Some(true), Some(true)));
        assert_eq!(
            (status.pawnio.as_deref(), status.error.as_str()),
            (Some("2.0.1"), "")
        );
        let first = "LibreHardwareMonitor is read through the sensor service (2 sensors)";
        assert_eq!(said.lock().unwrap().clone(), [first, "ready: service"]);
        // every piece once, then only the processor the theme reads; then the card
        let counted = made.clone();
        let asked = |n: usize| {
            let made = counted.clone();
            move || made.lock().unwrap().asked.len() >= n
        };
        assert!(until(5.0, asked(3)));
        let cpu = (vec!["/amdcpu/0".to_string()], false);
        assert_eq!(
            made.lock().unwrap().asked[..3],
            [(Vec::new(), true), cpu.clone(), cpu]
        );
        wanted.set(["gpu.temp".to_string()]);
        let card = (vec!["/gpu-nvidia/0".to_string()], false);
        assert!(until(5.0, || made.lock().unwrap().asked.last() == Some(&card)));
        source.close();
    }

    #[test]
    fn what_the_service_read_long_ago_is_not_shown_and_readings_that_stop_coming_go() {
        let made = service(&[running(60.0, "true", "null", "")]);
        let said = Said::default();
        let source = ServiceSource::start(options(&[], 20.0, 180.0, &said), opener(&made));
        assert!(until(5.0, || made.lock().unwrap().asked.len() >= 3));
        assert!(source.read().is_empty() && !source.live()); // (it reads anew now)
        made.lock().unwrap().answers = vec![running(0.05, "true", "null", "")];
        assert!(until(5.0, || source.live()));
        made.lock().unwrap().up = false;
        assert!(until(5.0, || source.read().is_empty())); // (no numbers that stand still)
        assert_eq!(source.status().error, "the pipe is gone");
        assert_eq!(source.status().version, "1.4.0"); // (the version it said is kept)
    }

    #[test]
    fn a_service_of_another_protocol_or_that_answers_wrongly_or_failed_is_not_used() {
        let cases = [
            (
                2,
                running(0.1, "true", "null", ""),
                "the sensor service speaks protocol 2, \
              this Libre Panel 1: set it up again from this version",
            ),
            (
                1,
                r#"{"v": 1, "ok": true, "state": "running", "sensors": "none"}"#.to_string(),
                "sensors is a list of at most 10000",
            ),
            (
                1,
                r#"{"v": 1, "ok": true, "state": "failed", "error": "no .NET", "sensors": []}"#
                    .to_string(),
                "LibreHardwareMonitor did not start in the service: no .NET",
            ),
        ]; // fmt: skip
        for (number, answer, why) in cases {
            let made = service(&[answer]);
            made.lock().unwrap().protocol = number;
            let said = Said::default();
            let source = ServiceSource::start(options(&[], 0.2, 0.2, &said), opener(&made));
            assert!(source.wait_ready(5.0)); // (it gave up for now)
            assert!(source.read().is_empty());
            assert_eq!(source.status().error, why);
            let told = said.lock().unwrap().clone();
            let failed = format!("the sensor service does not answer: {why}");
            assert_eq!(told, [failed, format!("failure: {why}")]);
        }
    }

    #[test]
    fn a_service_not_answered_yet_is_waited_for_longer_than_one_that_went_silent() {
        // never answered: given up after start_wait_seconds (it starts a while after Windows)
        let made = service(&[running(0.1, "true", "null", "")]);
        made.lock().unwrap().up = false;
        let said = Said::default();
        let began = Instant::now();
        let source = ServiceSource::start(options(&[], 0.2, 2.5, &said), opener(&made));
        assert!(source.wait_ready(10.0));
        assert!(began.elapsed().as_secs_f64() >= 2.5);
        let why = "the service does not run";
        let failed = format!("the sensor service does not answer: {why}");
        assert_eq!(
            said.lock().unwrap().clone(),
            [failed, format!("failure: {why}")]
        );
        // taken back when it answers
        made.lock().unwrap().up = true;
        let back = || {
            said.lock()
                .unwrap()
                .iter()
                .any(|line| line == "ready: service")
        };
        assert!(until(15.0, back));
        assert!(source.live());
        // once it answered, silence is given up on after wait_seconds
        let said = Said::default();
        let made = service(&[running(0.1, "true", "null", "")]);
        let source = ServiceSource::start(options(&[], 0.2, 2.5, &said), opener(&made));
        assert!(source.wait_ready(5.0));
        let silent = Instant::now();
        made.lock().unwrap().up = false;
        let given_up = || {
            said.lock()
                .unwrap()
                .iter()
                .any(|line| line.starts_with("failure"))
        };
        assert!(until(10.0, given_up));
        assert!(
            silent.elapsed().as_secs_f64() < 2.5,
            "{:?}",
            silent.elapsed()
        );
    }

    #[test]
    fn without_pawnio_the_cpu_is_out_of_reach_and_the_status_says_why() {
        let timing = r#", "timing": {"last": {"full": false, "parts": [["/amdcpu/0",
            "AMD Ryzen 7 7800X3D", "Cpu", 0.0042]]}, "full": {"full": true, "parts": [["/amdcpu/0",
            "AMD Ryzen 7 7800X3D", "Cpu", 0.004], ["/lpc/nct6798d", "Nuvoton NCT6798D",
            "SuperIO", 0.0121]]}}"#;
        let made = service(&[running(0.1, "false", "null", timing)]);
        let said = Said::default();
        let source = ServiceSource::start(options(&[], 20.0, 180.0, &said), opener(&made));
        assert!(source.wait_ready(5.0));
        let status = source.status();
        assert_eq!((status.ring0, status.pawnio), (Some(false), None));
        let missing = "the sensor service cannot read the CPU's sensors: PawnIO is missing";
        assert!(said.lock().unwrap().iter().any(|line| line == missing));
        let line = "round time: last round 4.2 ms (1 part), all hardware 16.1 ms (2 parts)";
        assert_eq!(round_line(status.timing.as_ref()), line);
        let older = "round time: not reported by this service version";
        assert_eq!(round_line(None), older);
        assert_eq!(
            round_line(Some(&protocol::Timing::default())),
            "round time: no round yet"
        );
    }

    #[test]
    fn the_check_waits_for_fresh_readings_and_says_what_it_found() {
        let starting = r#"{"v": 1, "ok": true, "state": "starting", "sensors": []}"#;
        let made = service(&[
            starting.to_string(),
            running(9.0, "true", r#""2.0.1""#, ""),
            running(0.4, "true", r#""2.0.1""#, ""),
        ]); // fmt: skip
        let slept = Arc::new(Mutex::new(0.0));
        let sleep = |seconds: f64| *slept.lock().unwrap() += seconds;
        let (worked, lines) = check(&mut opener(&made), 15.0, &sleep);
        assert!(worked);
        assert_eq!(*slept.lock().unwrap(), 1.0);
        assert_eq!(
            lines,
            [
                "sensor service 1.4.0, protocol 1",
                "read through the sensor service: 2 sensors of 2 parts",
                "CPU temperature, power and clock: available",
                "PawnIO driver: 2.0.1",
                "round time: not reported by this service version",
                "cpu.temp: 51.5 °C",
                "gpu.temp: 48.0 °C",
            ]
        ); // fmt: skip
        made.lock().unwrap().up = false;
        let (worked, lines) = check(&mut opener(&made), 15.0, &sleep);
        assert!(!worked);
        assert_eq!(
            lines,
            ["The sensor service does not answer: the service does not run"]
        );
    }
}

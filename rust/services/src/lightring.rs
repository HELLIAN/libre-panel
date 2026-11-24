//! The light ring of Lian Li's Universal Screen 8.8" (lp-devices' lianli_ring): its 60 LEDs in
//! a colour, breathing, as a rainbow or a light running round, or coloured by a reading (the
//! processor's temperature from green to red), at the brightness and speed set.
//!
//! `[services.lightring]`:
//!
//! ```toml
//! mode = "rainbow"     # "off", "color", "breathing", "rainbow", "spin" or "reading"
//! color = "#3C8CFF"    # the colour of "color", "breathing" and "spin"
//! brightness = 100     # %
//! speed = 50           # % (how fast "breathing", "rainbow" and "spin" move)
//! reverse = false      # "rainbow" and "spin" the other way round
//! reading = "cpu.temp" # the reading of "reading": green at `low`, red at `high`
//! low = 40.0
//! high = 85.0
//! dark_on_quit = false # dark when Libre Panel quits (else the ring keeps its last colours)
//! ```
//!
//! The ring keeps what it was last sent, and has no effects of its own: what moves is sent 30
//! times a second (three packets of 64 bytes each time), what stands still once and again every
//! few seconds (so a ring plugged in again, or woken, gets its colours back). Only one program
//! can have the ring: while L-Connect or OpenRGB has it, the service says so and looks again.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use lp_devices::lianli_ring::{Frame, Ring, Unavailable, LEDS};
use lp_py::{Dict, Value};

use crate::host::ServiceHost;
use crate::manager::{Part, Schema, Service};

/// how long a search that found no ring (or a busy one) waits before the next
pub const RETRY_S: f64 = 5.0;
/// frames a second of what moves
pub const FRAME_RATE: f64 = 30.0;
/// how often what stands still is sent again
pub const STILL_S: f64 = 5.0;
/// how often "reading" looks at its reading
pub const READING_S: f64 = 1.0;

/// What the ring shows.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Mode {
    Off,
    Color,
    Breathing,
    Rainbow,
    Spin,
    Reading,
}

impl Mode {
    pub const ALL: [(&'static str, Mode); 6] = [
        ("off", Mode::Off),
        ("color", Mode::Color),
        ("breathing", Mode::Breathing),
        ("rainbow", Mode::Rainbow),
        ("spin", Mode::Spin),
        ("reading", Mode::Reading),
    ];

    fn moves(self) -> bool {
        matches!(self, Mode::Breathing | Mode::Rainbow | Mode::Spin)
    }
}

/// The service's options, read.
#[derive(Clone, Debug, PartialEq)]
pub struct Settings {
    pub mode: Mode,
    pub color: [u8; 3],
    /// 0 to 1
    pub brightness: f64,
    /// 0 to 1
    pub speed: f64,
    pub reverse: bool,
    pub reading: String,
    pub low: f64,
    pub high: f64,
    pub dark_on_quit: bool,
}

const COLOR: [u8; 3] = [0x3C, 0x8C, 0xFF];

/// color_of: "#RRGGBB" (or without the "#") as its three values; None for another text.
pub fn color_of(text: &str) -> Option<[u8; 3]> {
    let hex = text.trim().strip_prefix('#').unwrap_or(text.trim());
    if hex.len() != 6 || !hex.bytes().all(|b| b.is_ascii_hexdigit()) {
        return None;
    }
    let part = |at: usize| u8::from_str_radix(&hex[at..at + 2], 16).ok();
    Some([part(0)?, part(2)?, part(4)?])
}

impl Settings {
    /// of: the settings `options` give (as apply_options made them: each of its type), and
    /// what they hold that cannot be used, for the log.
    pub fn of(options: &Dict) -> (Settings, Vec<String>) {
        let mut notes = Vec::new();
        let text = |key: &str| match options.get(key) {
            Some(Value::Str(text)) => text.clone(),
            _ => String::new(),
        };
        let number = |key: &str, default: f64| {
            options
                .get(key)
                .and_then(Value::to_f64)
                .filter(|n| n.is_finite())
                .unwrap_or(default)
        };
        let flag = |key: &str| matches!(options.get(key), Some(Value::Bool(true)));
        let named = text("mode");
        let mode = match Mode::ALL.iter().find(|(name, _)| *name == named.trim()) {
            Some((_, mode)) => *mode,
            None => {
                notes.push(format!(
                    "light ring: unknown mode {}: the rainbow",
                    lp_py::repr(&named)
                ));
                Mode::Rainbow
            }
        };
        let color = match color_of(&text("color")) {
            Some(color) => color,
            None => {
                let given = lp_py::repr(&text("color"));
                notes.push(format!(
                    "light ring: {given} is no colour (#RRGGBB): #3C8CFF"
                ));
                COLOR
            }
        };
        let (low, high) = (number("low", 40.0), number("high", 85.0));
        let settings = Settings {
            mode,
            color,
            brightness: number("brightness", 100.0).clamp(0.0, 100.0) / 100.0,
            speed: number("speed", 50.0).clamp(0.0, 100.0) / 100.0,
            reverse: flag("reverse"),
            reading: text("reading").trim().to_string(),
            low: low.min(high),
            high: high.max(low),
            dark_on_quit: flag("dark_on_quit"),
        };
        (settings, notes)
    }

    /// The time a round of what moves takes at the speed set: from `slow` at 0 % to `fast` at
    /// 100 %, the same step for each percent the eye sees (by the ratio, not the difference).
    fn period(&self, slow: f64, fast: f64) -> f64 {
        slow * (fast / slow).powf(self.speed)
    }
}

fn scaled(color: [u8; 3], level: f64) -> [u8; 3] {
    let level = level.clamp(0.0, 1.0);
    color.map(|c| (f64::from(c) * level).round() as u8)
}

/// hsv: a hue (0 to 1, from red round to red), at full saturation and value.
fn hsv(hue: f64) -> [u8; 3] {
    let h = hue.rem_euclid(1.0) * 6.0;
    let x = 1.0 - (h % 2.0 - 1.0).abs();
    let (r, g, b) = match h as u32 {
        0 => (1.0, x, 0.0),
        1 => (x, 1.0, 0.0),
        2 => (0.0, 1.0, x),
        3 => (0.0, x, 1.0),
        4 => (x, 0.0, 1.0),
        _ => (1.0, 0.0, x),
    };
    [r, g, b].map(|c: f64| (c * 255.0).round() as u8)
}

/// frame: what the ring shows at `t` seconds after the service began; `value`: the reading of
/// "reading" now (None: none).
pub fn frame(settings: &Settings, t: f64, value: Option<f64>) -> Frame {
    let bright = settings.brightness;
    let turn = if settings.reverse { -1.0 } else { 1.0 };
    match settings.mode {
        Mode::Off => [[0; 3]; LEDS],
        Mode::Color => [scaled(settings.color, bright); LEDS],
        Mode::Breathing => {
            // (from a glow to the full colour and back, smoothly)
            let phase = t / settings.period(8.0, 1.5);
            let level = 0.06 + 0.94 * (0.5 - 0.5 * (std::f64::consts::TAU * phase).cos());
            [scaled(settings.color, bright * level); LEDS]
        }
        Mode::Rainbow => {
            // (how many LEDs on the colours have moved: a whole round is the same frame again)
            let shift = turn * t / settings.period(20.0, 2.0) * LEDS as f64;
            let ring = LEDS as f64;
            std::array::from_fn(|i| scaled(hsv((i as f64 - shift).rem_euclid(ring) / ring), bright))
        }
        Mode::Spin => {
            // (a light running round with a tail a fifth of the ring long)
            let head = (turn * t / settings.period(6.0, 0.8)).rem_euclid(1.0) * LEDS as f64;
            let tail = LEDS as f64 / 5.0;
            std::array::from_fn(|i| {
                let behind = (turn * (head - i as f64)).rem_euclid(LEDS as f64);
                let level = (1.0 - behind / tail).max(0.0).powi(2);
                scaled(settings.color, bright * level)
            })
        }
        Mode::Reading => {
            let color = match value {
                // (green at `low`, through yellow, red at `high`)
                Some(value) => {
                    let span = (settings.high - settings.low).max(f64::EPSILON);
                    let share = ((value - settings.low) / span).clamp(0.0, 1.0);
                    hsv((1.0 - share) / 3.0)
                }
                None => settings.color,
            };
            [scaled(color, bright); LEDS]
        }
    }
}

/// What the service tells its card: the ring's state, its firmware, why it has none.
#[derive(Default)]
struct Said {
    state: String,
    version: String,
    reason: String,
}

struct Shared {
    host: ServiceHost,
    settings: Settings,
    /// how long a search that found no ring waits (RETRY_S)
    retry_s: f64,
    open: Box<dyn Fn() -> Result<Ring, Unavailable> + Send + Sync>,
    ended: AtomicBool,
    quitting: AtomicBool,
    wake: (Mutex<()>, Condvar),
    said: Mutex<Said>,
}

impl Shared {
    fn ended(&self) -> bool {
        self.ended.load(Ordering::Relaxed)
    }

    /// Waits `seconds`, or less where the service ends.
    fn wait(&self, seconds: f64) {
        if let Ok(asleep) = self.wake.0.lock() {
            let wait = Duration::from_secs_f64(seconds.max(0.0));
            let _woken = (self.wake.1).wait_timeout_while(asleep, wait, |()| !self.ended());
        }
    }

    fn say(&self, state: &str, version: &str, reason: &str) {
        if let Ok(mut said) = self.said.lock() {
            if said.state != state || said.reason != reason {
                let note = match reason.is_empty() {
                    true => format!("light ring: {state}"),
                    false => format!("light ring: {state}: {reason}"),
                };
                self.host.note(&note);
            }
            *said = Said {
                state: state.into(),
                version: version.into(),
                reason: reason.into(),
            };
        }
    }

    fn value(&self) -> Option<f64> {
        let snapshot = self.host.snapshot()?;
        snapshot
            .value(&self.settings.reading)
            .to_f64()
            .filter(|v| v.is_finite())
    }

    fn run(&self) {
        let began = Instant::now();
        let settings = &self.settings;
        let mut ring: Option<Ring> = None;
        let mut sent: Option<(Frame, Instant)> = None;
        while !self.ended() {
            let Some(shown) = ring.as_mut() else {
                match (self.open)() {
                    Ok(opened) => {
                        self.say("connected", &opened.version, "");
                        ring = Some(opened);
                        sent = None;
                    }
                    Err(why) => {
                        let state = match why {
                            Unavailable::Absent => "absent",
                            Unavailable::Busy => "busy",
                            Unavailable::Denied => "denied",
                            Unavailable::Unlike(_) | Unavailable::Failed(_) => "failed",
                        };
                        self.say(state, "", &why.to_string());
                        self.wait(self.retry_s);
                    }
                }
                continue;
            };
            let value = (settings.mode == Mode::Reading)
                .then(|| self.value())
                .flatten();
            let now = frame(settings, began.elapsed().as_secs_f64(), value);
            let due = match &sent {
                Some((last, at)) => *last != now || at.elapsed().as_secs_f64() >= STILL_S,
                None => true,
            };
            if due {
                if let Err(why) = shown.show(&now) {
                    ring = None;
                    self.say("lost", "", &why.to_string());
                    self.wait(self.retry_s.min(1.0));
                    continue;
                }
                sent = Some((now, Instant::now()));
            }
            self.wait(match settings.mode {
                mode if mode.moves() => 1.0 / FRAME_RATE,
                Mode::Reading => READING_S,
                _ => STILL_S,
            });
        }
        if let Some(ring) = ring.as_mut() {
            if settings.dark_on_quit && self.quitting.load(Ordering::Relaxed) {
                let _ = ring.show(&[[0; 3]; LEDS]);
            }
        }
    }
}

/// The light ring of Lian Li's Universal Screen 8.8", as a kind of service.
pub struct LightRing {
    shared: Arc<Shared>,
    thread: Option<JoinHandle<()>>,
}

fn options() -> Schema {
    vec![
        ("mode", "string", Value::Str("rainbow".into())),
        ("color", "string", Value::Str("#3C8CFF".into())),
        ("brightness", "int", Value::int(100)),
        ("speed", "int", Value::int(50)),
        ("reverse", "bool", Value::Bool(false)),
        ("reading", "string", Value::Str("cpu.temp".into())),
        ("low", "number", Value::Float(40.0)),
        ("high", "number", Value::Float(85.0)),
        ("dark_on_quit", "bool", Value::Bool(false)),
    ]
}

/// make: the service of `options`, its ring opened by `open`.
pub fn make(
    host: ServiceHost,
    options: &Dict,
    open: Box<dyn Fn() -> Result<Ring, Unavailable> + Send + Sync>,
) -> LightRing {
    let (settings, notes) = Settings::of(options);
    for note in notes {
        host.note(&note);
    }
    LightRing {
        shared: Arc::new(Shared {
            host,
            settings,
            retry_s: RETRY_S,
            open,
            ended: AtomicBool::new(false),
            quitting: AtomicBool::new(false),
            wake: (Mutex::new(()), Condvar::new()),
            said: Mutex::new(Said {
                state: "looking".into(),
                ..Said::default()
            }),
        }),
        thread: None,
    }
}

pub fn part() -> Part {
    Part {
        options,
        make: |host, options| Ok(Box::new(make(host, &options, Box::new(Ring::open)))),
    }
}

impl Service for LightRing {
    fn start(&mut self) -> Result<(), String> {
        let shared = &self.shared;
        if shared.settings.mode == Mode::Reading && !shared.settings.reading.is_empty() {
            shared.host.want(&[shared.settings.reading.as_str()]);
        }
        let quitting = Arc::clone(shared);
        let heard = Box::new(move |_: &crate::host::Event| {
            quitting.quitting.store(true, Ordering::Relaxed);
        });
        shared.host.on("quit", heard)?;
        shared.ended.store(false, Ordering::Relaxed);
        let running = Arc::clone(shared);
        let thread = std::thread::Builder::new().name("lightring".into());
        self.thread = Some(
            thread
                .spawn(move || running.run())
                .map_err(|why| format!("the light ring's thread did not start: {why}"))?,
        );
        Ok(())
    }

    fn stop(&mut self) {
        self.shared.ended.store(true, Ordering::Relaxed);
        if let Ok(_asleep) = self.shared.wake.0.lock() {
            self.shared.wake.1.notify_all();
        }
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }

    fn summary(&self) -> Option<Value> {
        let said = self.shared.said.lock().ok()?;
        let mut summary = Dict::default();
        summary.insert("state".into(), Value::Str(said.state.clone()));
        summary.insert("version".into(), Value::Str(said.version.clone()));
        summary.insert("reason".into(), Value::Str(said.reason.clone()));
        Some(Value::Dict(summary))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn settings(pairs: &[(&str, Value)]) -> Settings {
        let schema = options();
        let mut given: Dict = schema
            .iter()
            .map(|(k, _, v)| ((*k).to_string(), v.clone()))
            .collect();
        for (key, value) in pairs {
            given.insert((*key).to_string(), value.clone());
        }
        Settings::of(&given).0
    }

    #[test]
    fn colours_are_read_with_or_without_the_hash_and_others_are_refused() {
        assert_eq!(color_of("#3C8CFF"), Some([0x3C, 0x8C, 0xFF]));
        assert_eq!(color_of(" ff0000 "), Some([255, 0, 0]));
        for wrong in ["", "#fff", "#gg0000", "#ff00000", "red"] {
            assert_eq!(color_of(wrong), None, "{wrong}");
        }
        let mut given = Dict::default();
        given.insert("mode".into(), Value::Str("disco".into()));
        given.insert("color".into(), Value::Str("red".into()));
        let (read, notes) = Settings::of(&given);
        assert_eq!((read.mode, read.color), (Mode::Rainbow, COLOR));
        assert_eq!(notes.len(), 2, "{notes:?}");
    }

    #[test]
    fn off_and_a_colour_are_still_and_the_brightness_scales_them() {
        let off = settings(&[("mode", Value::Str("off".into()))]);
        assert_eq!(frame(&off, 3.0, None), [[0; 3]; LEDS]);
        let half = settings(&[
            ("mode", Value::Str("color".into())),
            ("color", Value::Str("#FF8000".into())),
            ("brightness", Value::int(50)),
        ]);
        assert_eq!(frame(&half, 0.0, None), [[128, 64, 0]; LEDS]);
        assert_eq!(frame(&half, 0.0, None), frame(&half, 99.0, None));
    }

    /// Two colours a level apart at most in each of their values (what falls on a half in one
    /// sum and on the other side of it in another).
    fn close(a: [u8; 3], b: [u8; 3]) -> bool {
        a.iter().zip(b).all(|(x, y)| x.abs_diff(y) <= 1)
    }

    #[test]
    fn the_rainbow_goes_round_once_and_the_other_way_when_reversed() {
        let rainbow = settings(&[("speed", Value::int(100))]);
        let at = |t| frame(&rainbow, t, None);
        // (all the hues on the ring at once; a round later the same frame)
        assert_eq!(at(0.0)[0], [255, 0, 0]);
        assert_eq!(at(0.0)[20], [0, 255, 0]);
        assert_eq!(at(0.0)[40], [0, 0, 255]);
        assert_eq!(at(0.0), at(2.0));
        // (a sixtieth of a round on: each LED shows what the one before it showed)
        let step = 2.0 / LEDS as f64;
        let (now, next) = (at(0.0), at(step));
        assert!((1..LEDS).all(|i| close(next[i], now[i - 1])));
        let back = settings(&[("speed", Value::int(100)), ("reverse", Value::Bool(true))]);
        let next = frame(&back, step, None);
        assert!((0..LEDS - 1).all(|i| close(next[i], now[i + 1])));
    }

    #[test]
    fn breathing_glows_between_a_glimmer_and_the_full_colour() {
        let breathing = settings(&[
            ("mode", Value::Str("breathing".into())),
            ("color", Value::Str("#FFFFFF".into())),
            ("speed", Value::int(0)),
        ]);
        let level = |t: f64| frame(&breathing, t, None)[0][0];
        assert_eq!(level(0.0), 15); // (6 % of 255, rounded)
        assert_eq!(level(4.0), 255); // (half of the 8-second breath at 0 %)
        assert!(level(2.0) > level(1.0));
    }

    #[test]
    fn the_spinning_light_has_its_head_brightest_and_a_tail_behind_it() {
        let spin = settings(&[
            ("mode", Value::Str("spin".into())),
            ("color", Value::Str("#FFFFFF".into())),
        ]);
        let now = frame(&spin, 0.0, None);
        assert_eq!(now[0], [255; 3]);
        assert!(now[LEDS - 1][0] > now[LEDS - 6][0] && now[LEDS - 6][0] > 0);
        assert_eq!(now[1], [0; 3]); // (ahead of the head: dark)
        assert_eq!(now[LEDS - 12], [0; 3]); // (beyond the tail: dark)
    }

    #[test]
    fn a_reading_goes_from_green_through_yellow_to_red() {
        let reading = settings(&[("mode", Value::Str("reading".into()))]);
        assert_eq!(frame(&reading, 0.0, Some(30.0))[0], [0, 255, 0]);
        assert_eq!(frame(&reading, 0.0, Some(62.5))[0], [255, 255, 0]);
        assert_eq!(frame(&reading, 0.0, Some(95.0))[0], [255, 0, 0]);
        assert_eq!(frame(&reading, 0.0, None)[0], COLOR); // (none yet: the colour set)
        let turned = settings(&[("low", Value::Float(90.0)), ("high", Value::Float(10.0))]);
        assert!(turned.low <= turned.high);
    }

    /// A ring the tests make up: the frames it was sent (each the three packets' colours), and
    /// whether it fails from a given packet on.
    #[derive(Clone, Default)]
    struct Bench {
        packets: Arc<Mutex<Vec<Vec<u8>>>>,
        fail_after: Option<usize>,
    }

    impl lp_devices::usb_link::UsbIo for Bench {
        fn product(&mut self) -> String {
            String::new()
        }
        fn interfaces(&self) -> Vec<lp_devices::usb_link::InterfaceInfo> {
            use lp_devices::usb_link::{EndpointInfo, InterfaceInfo};
            let endpoint = |address| EndpointInfo {
                address,
                max_packet: 64,
                interrupt: false,
            };
            vec![InterfaceInfo {
                number: 0,
                class: 0xFF,
                endpoints: vec![endpoint(0x81), endpoint(0x01)],
            }]
        }
        fn claim(&mut self, _: &[u8]) -> lp_devices::usb_link::UsbResult<()> {
            Ok(())
        }
        fn write(&mut self, _: u8, data: &[u8], _: u64) -> lp_devices::usb_link::UsbResult<()> {
            let mut packets = self.packets.lock().unwrap();
            if self.fail_after.is_some_and(|after| packets.len() >= after) {
                return Err(lp_devices::usb_link::UsbError::Other("gone".into()));
            }
            packets.push(data.to_vec());
            Ok(())
        }
        fn read(&mut self, _: u8, _: usize, _: u64) -> lp_devices::usb_link::UsbResult<Vec<u8>> {
            Err(lp_devices::usb_link::UsbError::Timeout)
        }
        fn control_out(
            &mut self,
            _: u8,
            _: u8,
            _: u16,
            _: u16,
            _: &[u8],
            _: u64,
        ) -> lp_devices::usb_link::UsbResult<()> {
            unreachable!()
        }
        fn control_in(
            &mut self,
            _: u8,
            _: u8,
            _: u16,
            _: u16,
            _: u16,
            _: u64,
        ) -> lp_devices::usb_link::UsbResult<Vec<u8>> {
            unreachable!()
        }
        fn release(&mut self) {}
    }

    fn waited(what: impl Fn() -> bool) -> bool {
        let until = Instant::now() + Duration::from_secs(5);
        while Instant::now() < until {
            if what() {
                return true;
            }
            std::thread::sleep(Duration::from_millis(5));
        }
        false
    }

    fn state(service: &LightRing) -> String {
        match service.summary() {
            Some(Value::Dict(said)) => said.get("state").map(Value::str).unwrap_or_default(),
            _ => String::new(),
        }
    }

    /// The service finds the ring busy, then has it: a colour goes as three packets of SetLeds
    /// (after the version's question), and goes again when the ring went away and came back;
    /// on quitting with dark_on_quit, the last frame is black.
    #[test]
    fn the_service_waits_for_the_ring_shows_its_colour_and_darkens_it_on_quitting() {
        let hub = crate::host::PluginHost::new(Box::new(|| 0.0));
        let host = ServiceHost::new(&hub, "lightring");
        let bench = Bench::default();
        let tries = Arc::new(Mutex::new(0usize));
        let (made, counted) = (bench.clone(), Arc::clone(&tries));
        let open: Box<dyn Fn() -> Result<Ring, Unavailable> + Send + Sync> = Box::new(move || {
            let mut tries = counted.lock().unwrap();
            *tries += 1;
            match *tries {
                1 => Err(Unavailable::Busy),
                // (the second time it is unplugged amid its first frame)
                2 => Ring::with(Box::new(Bench {
                    fail_after: Some(2),
                    ..made.clone()
                })),
                _ => Ring::with(Box::new(made.clone())),
            }
        });
        let mut options = Dict::default();
        for (key, value) in [
            ("mode", Value::Str("color".into())),
            ("color", Value::Str("#102030".into())),
            ("dark_on_quit", Value::Bool(true)),
        ] {
            options.insert(key.into(), value);
        }
        let mut service = make(host, &options, open);
        // (no wait between tries in a test)
        let shared = Arc::get_mut(&mut service.shared).unwrap();
        shared.retry_s = 0.01;
        service.start().unwrap();
        assert!(waited(|| *tries.lock().unwrap() >= 3), "it looks again");
        assert!(waited(|| state(&service) == "connected"));
        let colours = |packets: &[Vec<u8>]| -> Vec<Vec<u8>> {
            packets
                .iter()
                .filter(|p| p[0] == 0x11)
                .map(|p| p[4..7].to_vec())
                .collect()
        };
        assert!(waited(|| colours(&bench.packets.lock().unwrap()).len() >= 4));
        hub.emit(crate::host::Event::Quit, true);
        service.stop();
        let packets = bench.packets.lock().unwrap();
        assert!(packets
            .iter()
            .all(|p| p.len() == 64 && matches!(p[0], 0x10 | 0x11)));
        let shown = colours(&packets);
        assert_eq!(shown[0], vec![0x10, 0x20, 0x30]);
        assert_eq!(shown[shown.len() - 3..], vec![vec![0, 0, 0]; 3]);
        assert!(
            hub.notes().iter().any(|note| note.contains("busy")),
            "{:?}",
            hub.notes()
        );
    }

    /// A frame of what moves costs a few microseconds: 30 a second are nothing to the CPU.
    #[test]
    fn a_frame_is_cheap() {
        let rainbow = settings(&[]);
        let began = Instant::now();
        for n in 0..3000 {
            std::hint::black_box(frame(&rainbow, f64::from(n) / FRAME_RATE, None));
        }
        let each = began.elapsed().as_secs_f64() / 3000.0;
        assert!(each < 0.0005, "{each}");
    }
}

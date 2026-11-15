//! `libre-panel start-screen`: a TURZX USB panel's own screens from the terminal, for a panel
//! that restarts over and over after its start screen was written (issue 52): on the USB for
//! a moment, gone again, without end.
//!
//! What that panel's log told: the folder that holds the start screen (`usr/data`) holds the
//! panel's own settings too (`app.cfg`), and it is small, about 0.7 MB on that 9.2". A
//! standby clip of 706,912 bytes filled it to the last byte; the settings were saved into
//! the full folder (125), and the panel did not get through its next start. So the clip is
//! taken off, which makes room.
//!
//! **Which panels.** The vendor id 1CBE is shared by panels of several makers with protocols
//! of their own (Lian Li's are no TURZX panels), and the TURZX family's models and firmwares
//! may differ in what a command does. So: only a panel of the TURZX USB family is taken
//! (lp_devices::turzx_direct), the sync is the first thing it is sent and its answer must be
//! a TURZX panel's (`0A C8`, `turzx_`), and a step is sent only to a model it was seen to
//! work on (start_screen::MEASURED: the 9.2"). Every command sent here is one the 9.2" of
//! issue 52 had taken from Libre Panel before, by its log (10, 42, 98, 99, 111, 112, 125),
//! but for one value: 125's start mode 0, which its log does not have (it had 2). That it
//! is the panel's default comes from other projects' notes on other models of the family
//! and from SPUR II's reading of the 8.8"'s firmware, so it is sent only where it is asked
//! for (`default-start`), or where the panel's settings file is there and empty.
//!
//! The panel is taken in this process (no engine to start first, nothing read away) the
//! moment it is on the bus; a packet waits until the panel takes it. Then what the panel
//! holds is asked, and how long it stays is watched. Every line says the milliseconds since
//! the panel came, so that a run tells how long it is there and how far it gets. A step the
//! panel accepted is not sent again the next time it comes; the rest is.
//!
//! What is sent, in this order whatever the order asked in:
//!
//! - 10, until it is answered once.
//! - `remove-clip`: 42 `usr/data/standby.h264`.
//! - `remove-logo`: 42 `usr/data/boot.jpg` (as *Factory logo again*).
//! - 111: the clip the panel may be playing stopped.
//! - 98 for the files taken off (a file is off when the panel says it has none) and for
//!   `usr/data/app.cfg`.
//! - 125 with start mode 0 (the settings saved with the start as the panel came), once the
//!   clip is off, never into a folder it still fills: with `default-start`, or after
//!   `remove-clip` where `app.cfg` is there and empty.
//!
//! Then, to see: 98 for the other files, 112 and the folder's listing (99). Nothing is
//! written to the panel's files.

use std::path::PathBuf;
use std::time::{Duration, Instant};

use lp_devices::turzx::{brightness_arg, firmware_of};
use lp_devices::turzx_direct::{self as direct, Direct, Other, Trouble};

use crate::start_screen::{
    checked_name, file_args, names_in, BOOT_LOGO, FOLDER, MEASURED, STANDBY_CLIP,
};

/// The panel's own settings in its folder, and the file beside them (as a 9.2" lists them).
const SETTINGS: &str = "usr/data/app.cfg";
const BOOT_SIGN: &str = "usr/data/boot.sign";

const CMD_SYNC: u8 = 10;
const CMD_DELETE_FILE: u8 = 42;
const CMD_FILE_SIZE: u8 = 98;
const CMD_LIST: u8 = 99;
const CMD_STOP_LOCAL: u8 = 111;
const CMD_LOCAL_PLAYING: u8 = 112;
const CMD_SAVE_SETTINGS: u8 = 125;
const ACK: u8 = 0xC8;
const LIST_PIECES: usize = 20;
const PIECE: usize = 512;

/// How long a packet may wait for the panel to take it (one that is starting takes it late).
const TAKE_MS: u64 = 8000;
/// How long the first reply is waited for, a step's (a file taken off, the settings saved:
/// the panel writes), and each other one.
const FIRST_REPLY_MS: u64 = 4000;
const STEP_REPLY_MS: u64 = 4000;
const REPLY_MS: u64 = 1500;
/// How often the bus is looked at while the panel is waited for, and while it is watched.
/// (A look asks the system about every USB device: often enough for a panel that is there
/// for two seconds, and no more.)
const LOOK: Duration = Duration::from_millis(15);
const WATCH: Duration = Duration::from_millis(50);
/// How long the panel is tried to be opened each time it is on the bus.
const OPEN_FOR: Duration = Duration::from_secs(6);
/// How long the panel is watched after the steps at least: one that is still on the bus then
/// stays (longer where it was on the bus for longer before it left: `stay_for`).
const STAY: Duration = Duration::from_secs(20);
/// How often a panel that still leaves is met again once no step is open any more.
const MORE_VISITS: u32 = 5;
/// After so many visits in a row without one answer the panel is given up.
const FRUITLESS: u32 = 12;
/// How often a step is sent at most without the panel accepting it.
const SENDS: u32 = 6;
/// How often a file may still be there after the panel accepted its taking off.
const KEPT: u32 = 2;
/// How often a panel that is on the bus and cannot be opened is tried.
const NOT_OPENED: u32 = 3;
/// How often a panel is taken anew at once, each time it is on the bus, after a transfer
/// that failed while it stayed there.
const TAKEN_ANEW: u32 = 5;
/// How long a transfer that failed may be the panel leaving (the system lists it a moment
/// longer than it is there).
const LEAVING: Duration = Duration::from_millis(150);
/// How many lines the other USB devices' coming and going may take.
const DIARY_LINES: u32 = 40;
/// The makers whose devices are named at the start of a wait where they are no panel Libre
/// Panel knows: TURZX's own id, and the panels' processor in its loader (Allwinner, FEL).
const RELATED: [u16; 2] = [0x1CBE, 0x1F3A];

/// What `libre-panel start-screen` can be asked to do on the panel.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Action {
    /// its standby clip taken off (42): the room it took is the folder's again. Where the
    /// panel's settings file is there and empty afterwards, the settings are saved anew.
    RemoveClip,
    /// its boot logo taken off, the factory logo again (42, as *Factory logo again*)
    RemoveLogo,
    /// its settings saved with the start as it came (125 with start mode 0), once no clip
    /// is on it. The one value here that no 9.2" was seen to take (see the module's text).
    DefaultStart,
}

impl Action {
    fn word(self) -> &'static str {
        match self {
            Action::RemoveClip => "remove-clip",
            Action::RemoveLogo => "remove-logo",
            Action::DefaultStart => "default-start",
        }
    }
}

/// The actions a word of the command line names: one each, and `factory` all three (the
/// panel's own screens as it came).
pub fn actions_named(word: &str) -> Option<&'static [Action]> {
    match word {
        "remove-clip" => Some(&[Action::RemoveClip]),
        "remove-logo" => Some(&[Action::RemoveLogo]),
        "default-start" => Some(&[Action::DefaultStart]),
        "factory" => Some(&[Action::RemoveClip, Action::RemoveLogo, Action::DefaultStart]),
        _ => None,
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum State {
    /// to be sent (again)
    Open,
    /// the panel accepted it (and, for a file, no longer has it), or it is not needed
    Done,
    /// sent in vain too often: not sent again
    GivenUp,
}

struct Step {
    action: Action,
    /// the file it takes off (None: it saves the settings)
    file: Option<&'static str>,
    /// the settings' step: sent only where the settings file is there and empty (false: it
    /// was asked for)
    if_empty: bool,
    cmd: u8,
    args: Vec<u8>,
    said: String,
    state: State,
    /// how often it was sent
    sent: u32,
    /// how often the panel accepted it and still had the file
    kept: u32,
}

/// The steps for `actions`, in the order they are sent: the files first (the clip, the big
/// one, before the logo), then the settings.
fn plan(actions: &[Action], brightness: i64) -> Result<Vec<Step>, String> {
    let step = |action, file, cmd, args, said: String| Step {
        action,
        file,
        if_empty: false,
        cmd,
        args,
        said,
        state: State::Open,
        sent: 0,
        kept: 0,
    };
    let off = |action, name: &'static str, what: &str| -> Result<Step, String> {
        let said = format!("take the {what} off ({CMD_DELETE_FILE} {name})");
        let args = file_args(&checked_name(name)?, 0);
        Ok(step(action, Some(name), CMD_DELETE_FILE, args, said))
    };
    let asked = |action| actions.contains(&action);
    let mut steps = Vec::new();
    if asked(Action::RemoveClip) {
        steps.push(off(Action::RemoveClip, STANDBY_CLIP, "standby clip")?);
    }
    if asked(Action::RemoveLogo) {
        steps.push(off(Action::RemoveLogo, BOOT_LOGO, "boot logo")?);
    }
    if asked(Action::DefaultStart) || asked(Action::RemoveClip) {
        let args = vec![brightness_arg(brightness), 0, 0, 0, 0, 0];
        let said = format!(
            "save the settings with the start as the panel came ({CMD_SAVE_SETTINGS}, start \
             mode 0)"
        );
        let action = match asked(Action::DefaultStart) {
            true => Action::DefaultStart,
            false => Action::RemoveClip,
        };
        let mut saved = step(action, None, CMD_SAVE_SETTINGS, args, said);
        saved.if_empty = !asked(Action::DefaultStart);
        steps.push(saved);
    }
    Ok(steps)
}

/// The words of the actions with a step the panel did not accept; None where there is none.
fn undone(steps: &[Step]) -> Option<String> {
    let mut words: Vec<&str> = Vec::new();
    for step in steps.iter().filter(|step| step.state != State::Done) {
        if !words.contains(&step.action.word()) {
            words.push(step.action.word());
        }
    }
    (!words.is_empty()).then(|| words.join(", "))
}

/// Where the panel is looked for: the USB bus, or the tests' simulated panel.
enum Bus {
    Usb,
    Test {
        link: String,
        /// a file that is there while the simulated panel is on the bus (None: always)
        present: Option<PathBuf>,
        /// a file of the other devices on the simulated bus, a line each: `vid:pid|place|name`
        others: Option<PathBuf>,
        /// how long the simulated panel is watched before it is said to stay (None: `STAY`)
        stay: Option<Duration>,
        /// the model the simulated panel is (the 9.2" where none is named)
        model: String,
    },
}

enum Found {
    Usb(direct::Seen),
    Test,
}

impl Bus {
    fn new() -> Bus {
        let env = |name: &str| std::env::var(name).ok().filter(|value| !value.is_empty());
        match env("LP_ENGINE_LINK") {
            Some(link) => Bus::Test {
                link,
                present: env("LP_ENGINE_PRESENT").map(PathBuf::from),
                others: env("LP_ENGINE_OTHERS").map(PathBuf::from),
                stay: env("LP_ENGINE_STAY_MS")
                    .and_then(|ms| ms.parse().ok())
                    .map(Duration::from_millis),
                model: env("LP_ENGINE_MODEL").unwrap_or_else(|| "turing-9.2-usb".to_string()),
            },
            None => Bus::Usb,
        }
    }

    fn there(&self) -> Option<Found> {
        match self {
            Bus::Usb => direct::seen().map(Found::Usb),
            Bus::Test { present, .. } => present
                .as_ref()
                .is_none_or(|file| file.exists())
                .then_some(Found::Test),
        }
    }

    /// The panel, and every other device on the bus, in one look.
    fn scan(&self) -> (Option<Found>, Vec<Other>) {
        match self {
            Bus::Usb => {
                let (panel, others) = direct::scan();
                (panel.map(Found::Usb), others)
            }
            Bus::Test { others, .. } => {
                let text = others
                    .as_ref()
                    .and_then(|file| std::fs::read_to_string(file).ok())
                    .unwrap_or_default();
                let others = text.lines().filter_map(other_of).collect();
                (self.there(), others)
            }
        }
    }

    /// The model the panel is, by the catalogue's id, and as it is called.
    fn model(&self, found: &Found) -> Option<(String, String)> {
        let model = match (self, found) {
            (Bus::Test { model, .. }, _) => lp_devices::models::find_model(model)?,
            (Bus::Usb, Found::Usb(seen)) => seen.model()?,
            (Bus::Usb, Found::Test) => return None,
        };
        Some((model.id.to_string(), model.label()))
    }

    fn open(&self, found: &Found) -> Result<Direct, String> {
        match (self, found) {
            (Bus::Test { link, .. }, _) => Direct::test(link),
            (Bus::Usb, Found::Usb(seen)) => seen.open(),
            (Bus::Usb, Found::Test) => Err("no panel".into()),
        }
    }
}

/// A line of the simulated bus's other devices: `vid:pid|place|name`.
fn other_of(line: &str) -> Option<Other> {
    let mut parts = line.splitn(3, '|');
    let (vid, pid) = parts.next()?.split_once(':')?;
    Some(Other {
        vid: u16::from_str_radix(vid, 16).ok()?,
        pid: u16::from_str_radix(pid, 16).ok()?,
        place: parts.next()?.to_string(),
        name: parts.next().unwrap_or_default().to_string(),
    })
}

fn named(found: &Found) -> String {
    match found {
        Found::Usb(seen) => {
            let name = seen.model().map_or_else(
                || format!("TURZX USB panel {:04x}", seen.pid()),
                |model| model.label(),
            );
            format!("{name} ({})", seen.place())
        }
        Found::Test => "the simulated panel".to_string(),
    }
}

fn hex(reply: &[u8], n: usize) -> String {
    let bytes: Vec<String> = reply.iter().take(n).map(|b| format!("{b:02x}")).collect();
    bytes.join(" ")
}

/// Whether a reply says the command was accepted: the mark after the command's number (or,
/// as some replies have it, at byte 8).
fn accepted(reply: &[u8]) -> bool {
    reply.get(1) == Some(&ACK) || reply.get(8) == Some(&ACK)
}

fn how(reply: &[u8]) -> String {
    match accepted(reply) {
        true => "accepted".to_string(),
        false => "answered".to_string(),
    }
}

/// What a 98's reply says the panel has of a file.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Has {
    /// no such file
    None,
    /// so many bytes (0: a file that is there and empty; some panels may say it of one
    /// they have not)
    Bytes(u64),
}

impl Has {
    /// Whether the file takes no room: none, or empty.
    fn off(self) -> bool {
        matches!(self, Has::None | Has::Bytes(0))
    }
}

/// What a 98's reply names; None for a reply too short to say.
fn has_in(reply: &[u8]) -> Option<Has> {
    let bytes = reply.get(8..12)?;
    Some(
        match u32::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]) {
            u32::MAX => Has::None,
            size => Has::Bytes(u64::from(size)),
        },
    )
}

fn has_said(reply: &[u8]) -> String {
    match has_in(reply) {
        Some(Has::None) => "none".to_string(),
        Some(Has::Bytes(size)) => format!("{size} bytes"),
        None => "not said".to_string(),
    }
}

fn firmware_said(reply: &[u8]) -> String {
    match firmware_of(reply) {
        firmware if firmware.is_empty() => "no firmware named".to_string(),
        firmware => format!("firmware {firmware}"),
    }
}

/// What came of a command.
enum Said {
    /// its reply
    Reply(Vec<u8>),
    /// the panel took the packet and answered nothing in time
    Quiet,
    /// the panel did not take the packet (it is not reading)
    NotTaken,
    /// a transfer failed: the panel has left the bus, most likely
    Broke,
}

/// What the panel has said of itself since its files last changed: asked once, not at every
/// visit (a panel that comes every few seconds would fill pages with the same lines).
#[derive(Default)]
struct Known {
    /// it answered the sync as a TURZX panel does
    spoken: bool,
    clip: Option<Has>,
    logo: Option<Has>,
    settings: Option<Has>,
    sign: Option<Has>,
    playing: bool,
    /// the names in its folder
    names: Option<Vec<String>>,
}

impl Known {
    /// A step was accepted: what the panel holds is asked anew.
    fn changed(&mut self) {
        *self = Known {
            spoken: self.spoken,
            ..Known::default()
        };
    }

    fn of(&mut self, name: &str) -> &mut Option<Has> {
        match name {
            STANDBY_CLIP => &mut self.clip,
            BOOT_LOGO => &mut self.logo,
            SETTINGS => &mut self.settings,
            _ => &mut self.sign,
        }
    }
}

/// How a visit of the panel ended.
#[derive(Debug, PartialEq, Eq)]
enum Outcome {
    /// it was still on the bus, and answering, when the watch ended
    Stayed,
    /// asked once, without a watch (no `--wait`)
    Asked,
    /// it left the bus so many ms after it came
    Left(u128),
    /// a transfer failed so many ms after it came, and it is on the bus: taken anew at once
    Lost(u128),
    /// it is on the bus and takes no packets, or answers none any more
    Mute,
    /// it was on the bus but could not be opened
    NotOpened(String),
    /// it is no panel the steps are known on, or does not answer as a TURZX panel: nothing
    /// was changed on it
    NotThis(String),
}

struct Visited {
    outcome: Outcome,
    /// how many answers came
    replies: u32,
    /// why it could not be opened (None: it was)
    unopened: Option<String>,
}

/// One visit: the panel on the bus, from the moment it was seen.
struct Visit<'a> {
    number: u32,
    since: Instant,
    say: &'a mut dyn FnMut(&str),
    /// how many answers have come in this visit (the first is waited for longer)
    replies: u32,
    /// answers that came to a command no longer waited for
    late: Vec<Vec<u8>>,
}

impl Visit<'_> {
    fn ms(&self) -> u128 {
        self.since.elapsed().as_millis()
    }

    fn line(&mut self, text: &str) {
        let line = format!("[{}] +{} ms: {text}", self.number, self.ms());
        (self.say)(&line);
    }

    /// A command sent and its reply read: the reply that begins with the command (others,
    /// left over, are kept in `late`), or how it went wrong. Says what the reply tells
    /// (`tell`) and how long the panel took to take the packet and to answer.
    fn command(
        &mut self,
        link: &mut Direct,
        cmd: u8,
        args: &[u8],
        what: &str,
        tell: &dyn Fn(&[u8]) -> String,
        patience: u64,
    ) -> Said {
        let began = Instant::now();
        if let Err(trouble) = link.send(cmd, args, TAKE_MS) {
            return match trouble {
                Trouble::Silent => {
                    self.line(&format!(
                        "{what}: the panel did not take the packet in {TAKE_MS} ms"
                    ));
                    Said::NotTaken
                }
                Trouble::Failed(why) => {
                    self.line(&format!("{what}: not sent ({why})"));
                    Said::Broke
                }
            };
        }
        let taken = began.elapsed().as_millis();
        let wait = match self.replies {
            0 => patience.max(FIRST_REPLY_MS),
            _ => patience,
        };
        let until = Instant::now() + Duration::from_millis(wait);
        loop {
            let left = until.saturating_duration_since(Instant::now()).as_millis() as u64;
            let got = match left {
                0 => Err(Trouble::Silent),
                left => link.read(left),
            };
            match got {
                Ok(reply) if reply.first() == Some(&cmd) => {
                    self.replies += 1;
                    let answered = began.elapsed().as_millis();
                    self.line(&format!(
                        "{what}: {} [{}] (taken after {taken} ms, answered after {answered} ms)",
                        tell(&reply),
                        hex(&reply, 12)
                    ));
                    return Said::Reply(reply);
                }
                Ok(other) => {
                    self.replies += 1;
                    self.line(&format!(
                        "{what}: an answer to something else came first [{}]",
                        hex(&other, 12)
                    ));
                    self.late.push(other);
                }
                Err(Trouble::Silent) => {
                    self.line(&format!(
                        "{what}: taken after {taken} ms, no answer in {wait} ms"
                    ));
                    return Said::Quiet;
                }
                Err(Trouble::Failed(why)) => {
                    self.line(&format!(
                        "{what}: taken after {taken} ms, then no answer ({why})"
                    ));
                    return Said::Broke;
                }
            }
        }
    }

    /// One piece of the folder's listing: the panel sends it as it is, without the command's
    /// number first, so whatever comes next is the piece.
    fn piece(&mut self, link: &mut Direct, n: usize) -> Said {
        let args = file_args(FOLDER.as_bytes(), 0);
        let what = format!("{FOLDER}: piece {} of {LIST_PIECES} of its listing", n + 1);
        if let Err(trouble) = link.send(CMD_LIST, &args, TAKE_MS) {
            return match trouble {
                Trouble::Silent => {
                    self.line(&format!("{what}: the panel did not take the packet"));
                    Said::NotTaken
                }
                Trouble::Failed(why) => {
                    self.line(&format!("{what}: not sent ({why})"));
                    Said::Broke
                }
            };
        }
        match link.read(REPLY_MS) {
            Ok(reply) => {
                self.replies += 1;
                Said::Reply(reply)
            }
            Err(Trouble::Silent) => {
                self.line(&format!("{what}: no answer in {REPLY_MS} ms"));
                Said::Quiet
            }
            Err(Trouble::Failed(why)) => {
                self.line(&format!("{what}: no answer ({why})"));
                Said::Broke
            }
        }
    }
}

/// The names in the listing's pieces (a piece as it is, or after the command's number and
/// the mark where they come first).
fn listed(pieces: &[Vec<u8>]) -> Vec<String> {
    let mut all = Vec::new();
    for reply in pieces {
        let header = reply.first() == Some(&CMD_LIST) && reply.get(1) == Some(&ACK);
        let piece = match header {
            true => reply.get(8..).unwrap_or_default(),
            false => &reply[..],
        };
        all.extend_from_slice(&piece[..piece.len().min(PIECE)]);
    }
    names_in(&all)
}

/// The other USB devices' coming and going while the panel is waited for: a panel that does
/// not come back as itself (its processor's loader, a device Windows cannot read) shows here.
struct Diary {
    began: Instant,
    /// the devices at the last look (None: not looked yet)
    seen: Option<Vec<Other>>,
    lines: u32,
}

fn device_said(device: &Other) -> String {
    let name = match device.name.is_empty() {
        true => String::new(),
        false => format!(" \"{}\"", device.name),
    };
    format!(
        "{:04x}:{:04x}{name} ({})",
        device.vid, device.pid, device.place
    )
}

impl Diary {
    fn note(&mut self, now: Vec<Other>, say: &mut dyn FnMut(&str)) {
        let mut lines = Vec::new();
        match &self.seen {
            // the first look: only what the makers of these panels have on the bus
            None => {
                for device in now.iter().filter(|device| RELATED.contains(&device.vid)) {
                    lines.push(format!("{} is on the bus", device_said(device)));
                }
            }
            Some(before) if *before == now => return,
            Some(before) => {
                for device in before.iter().filter(|device| !now.contains(device)) {
                    lines.push(format!("{} left", device_said(device)));
                }
                for device in now.iter().filter(|device| !before.contains(device)) {
                    lines.push(format!("{} came", device_said(device)));
                }
            }
        }
        self.seen = Some(now);
        let at = self.began.elapsed().as_secs_f64();
        for line in lines {
            self.lines += 1;
            match self.lines.cmp(&(DIARY_LINES + 1)) {
                std::cmp::Ordering::Less => say(&format!("USB +{at:.1} s: {line}")),
                std::cmp::Ordering::Equal => {
                    say("USB: more devices came and went; not listed any further");
                }
                std::cmp::Ordering::Greater => {}
            }
        }
    }
}

/// The 1 ms timer and a high priority for the waits of this run (Windows: 15.6 ms otherwise,
/// a long time for a panel that is there for moments).
#[cfg(windows)]
fn in_a_hurry(on: bool) {
    use windows_sys::Win32::Media::{timeBeginPeriod, timeEndPeriod};
    use windows_sys::Win32::System::Threading::{
        GetCurrentProcess, SetPriorityClass, HIGH_PRIORITY_CLASS, NORMAL_PRIORITY_CLASS,
    };
    // SAFETY: plain calls on this process; the timer's begin is ended.
    unsafe {
        match on {
            true => {
                timeBeginPeriod(1);
                SetPriorityClass(GetCurrentProcess(), HIGH_PRIORITY_CLASS);
            }
            false => {
                SetPriorityClass(GetCurrentProcess(), NORMAL_PRIORITY_CLASS);
                timeEndPeriod(1);
            }
        }
    }
}

#[cfg(not(windows))]
fn in_a_hurry(_on: bool) {}

/// `libre-panel start-screen`: `actions` on the TURZX USB panel, then what it holds. Without
/// `wait`, on the panel that is there, once. With it, the panel is waited for that long and
/// met each time it comes, until every step is accepted and it stays, or it has left
/// `MORE_VISITS` times after that. `brightness` is what the settings are saved with. `say`
/// gets each line.
pub fn run(
    actions: &[Action],
    wait: Option<Duration>,
    brightness: i64,
    say: &mut dyn FnMut(&str),
) -> Result<(), String> {
    let bus = Bus::new();
    let mut steps = plan(actions, brightness)?;
    // (a packet is stamped with the local time: the zone is read now, not when the panel
    // comes; reading it took tens of milliseconds of the first packet's time)
    let _ = lp_devices::turzx_packet::now_stamp();
    let mut known = Known::default();
    let Some(limit) = wait else {
        let Some(found) = bus.there() else {
            return Err(lp_i18n::t("No TURZX USB panel found"));
        };
        let came = Came {
            number: 1,
            since: Instant::now(),
            waited: None,
            again: false,
        };
        let visited = visit(&bus, found, &came, &mut steps, &mut known, None, say);
        return match visited.outcome {
            Outcome::NotOpened(why) | Outcome::NotThis(why) => Err(why),
            Outcome::Left(ms) | Outcome::Lost(ms) => {
                Err(format!("the panel left the bus after {ms} ms"))
            }
            Outcome::Mute => Err("the panel is on the bus and does not answer".into()),
            Outcome::Stayed | Outcome::Asked => match undone(&steps) {
                None => Ok(()),
                Some(words) => Err(format!("the panel did not accept: {words}")),
            },
        };
    };
    say(&format!(
        "waiting up to {} s for the panel: plug it in, or let it come back",
        limit.as_secs()
    ));
    in_a_hurry(true);
    let result = wait_for(&bus, limit, &mut steps, &mut known, say);
    in_a_hurry(false);
    result
}

/// How long a panel is watched before it is said to stay: `STAY`, and well longer than it
/// was on the bus before it left (twice that and 5 s); the tests' simulated panel as they
/// say.
fn stay_for(bus: &Bus, lives: &[u128]) -> Duration {
    if let Bus::Test {
        stay: Some(stay), ..
    } = bus
    {
        return *stay;
    }
    let longest = lives.iter().max().copied().unwrap_or(0).min(600_000) as u64;
    STAY.max(Duration::from_millis(longest * 2 + 5000))
}

/// One stay of the panel on the bus, from when it was first seen until it left.
struct Stay {
    number: u32,
    since: Instant,
    /// the answers it gave in this stay
    answers: u32,
    /// how often a transfer failed in this stay while it was on the bus
    lost: u32,
}

/// When a run that waits has seen enough.
#[derive(Default)]
struct Tally {
    /// stays since no step is open any more
    settled: u32,
    /// stays in a row without an answer
    fruitless: u32,
}

impl Tally {
    /// A stay is over: whether the panel is waited for once more.
    fn goes_on(&mut self, answers: u32, steps: &[Step]) -> bool {
        if steps.iter().all(|step| step.state != State::Open) {
            self.settled += 1;
            self.settled <= MORE_VISITS
        } else if answers == 0 {
            self.fruitless += 1;
            self.fruitless < FRUITLESS
        } else {
            self.fruitless = 0;
            true
        }
    }
}

fn wait_for(
    bus: &Bus,
    limit: Duration,
    steps: &mut [Step],
    known: &mut Known,
    say: &mut dyn FnMut(&str),
) -> Result<(), String> {
    let began = Instant::now();
    let deadline = began + limit;
    let mut diary = Diary {
        began,
        seen: None,
        lines: 0,
    };
    let mut number = 0; // (how often the panel came)
    let mut stay: Option<Stay> = None;
    let mut lives: Vec<u128> = Vec::new(); // (how long it was on the bus, each time it left)
    let mut replies = 0; // (the answers of all stays)
    let mut tally = Tally::default();
    let mut unopened = 0;
    let mut never_opened = None; // (why, as long as it could not be opened once)
    let mut opened = false;
    let mut mute = false; // (on the bus without an answer, the last time it was met)
    loop {
        let (found, others) = bus.scan();
        diary.note(others, say);
        let Some(found) = found else {
            // (it left between two tries: that stay is over)
            if let Some(over) = stay.take() {
                lives.push(over.since.elapsed().as_millis());
                if !tally.goes_on(over.answers, steps) {
                    break;
                }
            }
            mute = false;
            if Instant::now() >= deadline {
                break;
            }
            std::thread::sleep(LOOK);
            continue;
        };
        let again = stay.is_some();
        let now = stay.get_or_insert_with(|| {
            number += 1;
            Stay {
                number,
                since: Instant::now(),
                answers: 0,
                lost: 0,
            }
        });
        let came = Came {
            number: now.number,
            since: now.since,
            waited: Some(now.since.duration_since(began)),
            again,
        };
        let watch = stay_for(bus, &lives);
        let visited = visit(bus, found, &came, steps, known, Some(watch), say);
        replies += visited.replies;
        now.answers += visited.replies;
        match visited.unopened {
            Some(why) if !opened => never_opened = Some(why),
            Some(_) => {}
            None => (opened, never_opened) = (true, None),
        }
        // (how long a panel that is still on the bus is left alone before it is met again)
        let mut hold = None;
        mute = false;
        match visited.outcome {
            Outcome::Stayed | Outcome::Asked => {
                say(&format!(
                    "the panel stays for now: it was on the bus and answering {:.0} s after \
                     it came. Unplug it for 10 seconds and plug it in again: if it starts \
                     and stays on, it is back.",
                    watch.as_secs_f64()
                ));
                return match undone(steps) {
                    None => Ok(()),
                    Some(words) => Err(format!("the panel stays, but did not accept: {words}")),
                };
            }
            Outcome::Left(ms) => {
                lives.push(ms);
                let answers = now.answers;
                stay = None;
                if !tally.goes_on(answers, steps) {
                    break;
                }
            }
            Outcome::Lost(_) => {
                now.lost += 1;
                match now.lost >= TAKEN_ANEW {
                    true => hold = Some(Duration::from_secs(10)),
                    false => std::thread::sleep(Duration::from_millis(2)),
                }
            }
            Outcome::Mute => {
                mute = true;
                hold = Some(Duration::from_secs(30));
            }
            Outcome::NotThis(why) => return Err(why),
            Outcome::NotOpened(why) => {
                // (on the bus all that time and not to be opened: no panel that comes and
                // goes, but one another program has, or one this user may not use)
                unopened += 1;
                if unopened >= NOT_OPENED {
                    return Err(format!(
                        "the panel is on the bus but cannot be opened ({why}). Is another \
                         program using it (the TURZX app, Libre Panel)?"
                    ));
                }
                hold = Some(Duration::from_secs(2));
            }
        }
        if let Some(hold) = hold {
            let until = (Instant::now() + hold).min(deadline);
            while bus.there().is_some() && Instant::now() < until {
                std::thread::sleep(WATCH);
            }
        }
        if Instant::now() >= deadline {
            break;
        }
    }
    if number == 0 {
        return Err(format!(
            "the panel was not on the bus in {} s",
            limit.as_secs()
        ));
    }
    let mut said = verdict(number, &lives, replies, mute, steps);
    if let Some(why) = never_opened {
        said += &format!(
            "; it could not be opened once ({why}): the system did not have it ready in time"
        );
    }
    Err(said)
}

/// What is said of a panel that came and did not stay, or stays without an answer.
fn verdict(number: u32, lives: &[u128], replies: u32, mute: bool, steps: &[Step]) -> String {
    let times = match number {
        1 => "once".to_string(),
        number => format!("{number} times"),
    };
    let mut said = match mute {
        true => "the panel is on the bus and does not answer".to_string(),
        false => format!("the panel came {times} and did not stay"),
    };
    if let (Some(least), Some(most)) = (lives.iter().min(), lives.iter().max()) {
        said += &match least == most {
            true => format!(" (on the bus for {least} ms)"),
            false => format!(" (on the bus for {least} to {most} ms)"),
        };
    }
    if replies == 0 {
        said += "; it answered nothing";
    }
    if !steps.is_empty() {
        let done = (steps.iter())
            .filter(|step| step.state == State::Done)
            .count();
        said += &format!("; {done} of {} steps are done", steps.len());
        if let Some(words) = undone(steps) {
            said += &format!(" (not: {words})");
        }
    }
    said
}

/// A file's step against what the panel says it holds: taken off where it has none; open
/// again where it accepted the step and still has the file (given up at the `KEPT`th time).
/// Returns whether a step is open again.
fn settle(steps: &mut [Step], known: &mut Known, at: &mut Visit<'_>) -> bool {
    let mut again = false;
    for step in steps.iter_mut() {
        let Some(name) = step.file else {
            continue;
        };
        match (step.state, *known.of(name)) {
            (State::Done, Some(Has::Bytes(size))) if size > 0 => {
                step.kept += 1;
                let given_up = step.kept >= KEPT;
                at.line(&format!(
                    "{}: accepted, but the panel still has the file ({size} bytes){}",
                    step.said,
                    match given_up {
                        true => "; not sent again",
                        false => "",
                    }
                ));
                step.state = match given_up {
                    true => State::GivenUp,
                    false => State::Open,
                };
                if !given_up {
                    again = true;
                    *known.of(name) = None; // (asked anew once it is sent again)
                }
            }
            (State::Open, Some(has)) if has.off() => step.state = State::Done,
            _ => {}
        }
    }
    again
}

/// The settings are not saved while the clip is on the panel: where its step is given up,
/// theirs is too.
fn give_up_what_waits(steps: &mut [Step], at: &mut Visit<'_>) {
    let clip_stays =
        (steps.iter()).any(|step| step.file == Some(STANDBY_CLIP) && step.state == State::GivenUp);
    for step in steps.iter_mut() {
        if clip_stays && step.file.is_none() && step.state == State::Open {
            step.state = State::GivenUp;
            at.line(&format!(
                "{}: not sent, the clip is still on the panel",
                step.said
            ));
        }
    }
}

/// When the panel came, for a visit's lines.
struct Came {
    number: u32,
    since: Instant,
    /// how long it had been waited for (None: no `--wait`)
    waited: Option<Duration>,
    /// it is taken anew in the same stay (after a transfer that failed)
    again: bool,
}

/// One visit of the panel that is on the bus since `came.since`: opened, the steps not yet
/// accepted sent, what it holds asked, and (with `watch`) watched for that long.
fn visit(
    bus: &Bus,
    found: Found,
    came: &Came,
    steps: &mut [Step],
    known: &mut Known,
    watch: Option<Duration>,
    say: &mut dyn FnMut(&str),
) -> Visited {
    let mut found = found;
    let mut at = Visit {
        number: came.number,
        since: came.since,
        say,
        replies: 0,
        late: Vec::new(),
    };
    if !came.again {
        let waited = came.waited.map_or_else(String::new, |waited| {
            format!(", {:.1} s into the wait", waited.as_secs_f64())
        });
        at.line(&format!("on the bus: {}{waited}", named(&found)));
    }
    // a step only to a model it was seen to work on: the family's other models, and other
    // makers' panels in it, may take the command for something else
    if !steps.is_empty() {
        let model = bus.model(&found);
        if !model
            .as_ref()
            .is_some_and(|(id, _)| MEASURED.contains(&id.as_str()))
        {
            let this = model.map_or_else(|| "not known".to_string(), |m| format!("a {}", m.1));
            let why = format!(
                "these steps are known on the Turing 9.2\" only; this panel is {this}: \
                 nothing was sent to it"
            );
            return Visited {
                outcome: Outcome::NotThis(why),
                replies: 0,
                unopened: None,
            };
        }
    }
    // opened: tried again at once while it is there (Windows lists a device before its
    // driver has started)
    let mut tries = 0u32;
    let mut failed = String::new();
    let opening = Instant::now();
    let mut link = loop {
        tries += 1;
        match bus.open(&found) {
            Ok(link) => break link,
            Err(why) => failed = why,
        }
        if tries.is_multiple_of(16) {
            match bus.there() {
                Some(now) => found = now, // (as the system has it now)
                None => {
                    let ms = at.ms();
                    at.line(&format!(
                        "left the bus before it could be opened ({tries} tries; {failed})"
                    ));
                    return Visited {
                        outcome: Outcome::Left(ms),
                        replies: 0,
                        unopened: Some(failed),
                    };
                }
            }
        }
        if opening.elapsed() >= OPEN_FOR {
            at.line(&format!("not opened in {} s: {failed}", OPEN_FOR.as_secs()));
            return Visited {
                outcome: Outcome::NotOpened(failed.clone()),
                replies: 0,
                unopened: Some(failed),
            };
        }
        std::thread::yield_now();
    };
    match tries {
        1 => at.line("open"),
        tries => at.line(&format!(
            "open at try {tries}: {} (before: {failed})",
            named(&found)
        )),
    }
    // the sync first, until the panel has answered it once as a TURZX panel does; then what
    // was asked for: the files taken off (room in the panel's folder), the clip it may be
    // playing stopped, the files' sizes asked, and the settings saved where that is asked
    // for or needed. Then what the panel holds (asked once: `known`). A transfer that fails
    // ends the visit: the panel has left the bus, or is taken anew. Once no step is open, a
    // visit sends nothing of this: it watches whether the panel stays by itself.
    let mut reading = true; // (false: the panel takes no packets; nothing more is sent)
    'talk: {
        macro_rules! told {
            ($said:expr) => {
                match $said {
                    Said::Reply(reply) => Some(reply),
                    Said::Quiet => None,
                    Said::NotTaken => {
                        reading = false;
                        break 'talk;
                    }
                    Said::Broke => {
                        let outcome = broke(bus, &mut at);
                        return Visited {
                            outcome,
                            replies: at.replies,
                            unopened: None,
                        };
                    }
                }
            };
        }
        // what the panel has of a file, asked where it is not known
        macro_rules! size {
            ($name:expr) => {
                if known.of($name).is_none() {
                    if let Ok(raw) = checked_name($name) {
                        let (args, what) = (file_args(&raw, 0), format!("{} (98)", $name));
                        let asked =
                            at.command(&mut link, CMD_FILE_SIZE, &args, &what, &has_said, REPLY_MS);
                        if let Some(reply) = told!(asked) {
                            *known.of($name) = has_in(&reply);
                        }
                    }
                }
            };
        }
        // the names in its folder, fetched where they are not known
        macro_rules! names {
            () => {
                if known.names.is_none() {
                    let began = Instant::now();
                    let mut pieces = Vec::new();
                    for n in 0..LIST_PIECES {
                        match told!(at.piece(&mut link, n)) {
                            Some(piece) => pieces.push(piece),
                            None => break,
                        }
                    }
                    if pieces.len() == LIST_PIECES {
                        let names = listed(&pieces);
                        let holds = match names.is_empty() {
                            true => "lists nothing".to_string(),
                            false => format!("holds {}", names.join(", ")),
                        };
                        at.line(&format!(
                            "{FOLDER} {holds} (its listing came in {} ms)",
                            began.elapsed().as_millis()
                        ));
                        known.names = Some(names);
                    }
                }
            };
        }
        if !known.spoken {
            let asked = at.command(
                &mut link,
                CMD_SYNC,
                &[],
                "sync (10)",
                &firmware_said,
                REPLY_MS,
            );
            let Some(reply) = told!(asked) else {
                break 'talk; // (not answered: nothing else is sent this time)
            };
            if !accepted(&reply) || firmware_of(&reply).is_empty() {
                let why = format!(
                    "the panel does not answer the sync as a TURZX panel does ({}): nothing \
                     was changed on it",
                    hex(&reply, 16)
                );
                return Visited {
                    outcome: Outcome::NotThis(why),
                    replies: at.replies,
                    unopened: None,
                };
            }
            known.spoken = true;
        }
        settle(steps, known, &mut at); // (a file the panel said it has not: nothing to send)
        let open = steps.iter().any(|step| step.state == State::Open);
        for round in 0..2 {
            for step in steps.iter_mut().filter(|step| step.file.is_some()) {
                if step.state != State::Open {
                    continue;
                }
                if step.sent >= SENDS {
                    step.state = State::GivenUp;
                    at.line(&format!(
                        "{}: sent {SENDS} times without an answer; not sent again",
                        step.said
                    ));
                    continue;
                }
                let asked = at.command(
                    &mut link,
                    step.cmd,
                    &step.args,
                    &step.said,
                    &how,
                    STEP_REPLY_MS,
                );
                // (counted where the panel took the packet)
                step.sent += u32::from(matches!(asked, Said::Reply(_) | Said::Quiet));
                if told!(asked).is_some_and(|reply| accepted(&reply)) {
                    step.state = State::Done;
                    known.changed();
                }
            }
            give_up_what_waits(steps, &mut at);
            let open = open && steps.iter().any(|step| step.state == State::Open);
            if open && round == 0 {
                let what = "stop the clip it may be playing (111)";
                told!(at.command(&mut link, CMD_STOP_LOCAL, &[], what, &how, REPLY_MS));
            }
            // (the sizes of the files that are taken off: a file is off when the panel says so)
            for name in [STANDBY_CLIP, BOOT_LOGO] {
                if steps.iter().any(|step| step.file == Some(name)) {
                    size!(name);
                }
            }
            let again = settle(steps, known, &mut at);
            give_up_what_waits(steps, &mut at);
            // the settings: saved only into the room the clip made, never into a folder it
            // still fills; and, where they were not asked for, only where the panel's
            // settings file is there and empty
            for n in 0..steps.len() {
                if steps[n].file.is_some() || steps[n].state != State::Open {
                    continue;
                }
                // (the clip's own step is open still: the settings wait for it)
                let clip_open = (steps.iter())
                    .any(|step| step.file == Some(STANDBY_CLIP) && step.state == State::Open);
                if clip_open {
                    continue;
                }
                if steps.iter().any(|step| step.file == Some(STANDBY_CLIP)) || !steps[n].if_empty {
                    size!(STANDBY_CLIP);
                }
                if !known.clip.is_some_and(Has::off) {
                    if let Some(Has::Bytes(size)) = known.clip {
                        let asked_for = !steps.iter().any(|step| step.file == Some(STANDBY_CLIP));
                        if asked_for {
                            steps[n].state = State::GivenUp;
                            at.line(&format!(
                                "{}: not sent, the standby clip is on the panel ({size} bytes; \
                                 remove-clip takes it off)",
                                steps[n].said
                            ));
                        }
                    }
                    continue;
                }
                if steps[n].if_empty {
                    size!(SETTINGS);
                    let left = match known.settings {
                        None => continue, // (not said: asked the next time)
                        Some(Has::Bytes(size)) if size > 0 => {
                            Some(format!("the panel's settings file has {size} bytes"))
                        }
                        Some(Has::None) => Some(format!("the panel has no {SETTINGS}")),
                        Some(Has::Bytes(_)) => {
                            // (a panel may say 0 bytes of a file it has not: its folder tells)
                            names!();
                            let file = SETTINGS.rsplit('/').next().unwrap_or(SETTINGS);
                            match &known.names {
                                None => continue,
                                Some(names) if names.iter().any(|name| name == file) => None,
                                Some(_) => Some(format!("the panel's folder has no {file}")),
                            }
                        }
                    };
                    if let Some(why) = left {
                        steps[n].state = State::Done;
                        at.line(&format!(
                            "{why}: its settings are left as they are (default-start saves \
                             them anew)"
                        ));
                        continue;
                    }
                    at.line("the panel's settings file is there and empty: saved anew");
                }
                if steps[n].sent >= SENDS {
                    steps[n].state = State::GivenUp;
                    at.line(&format!(
                        "{}: sent {SENDS} times without an answer; not sent again",
                        steps[n].said
                    ));
                    continue;
                }
                let asked = at.command(
                    &mut link,
                    steps[n].cmd,
                    &steps[n].args,
                    &steps[n].said,
                    &how,
                    STEP_REPLY_MS,
                );
                steps[n].sent += u32::from(matches!(asked, Said::Reply(_) | Said::Quiet));
                if told!(asked).is_some_and(|reply| accepted(&reply)) {
                    steps[n].state = State::Done;
                    known.settings = None; // (asked anew: what the panel has of them now)
                }
            }
            if !again {
                break;
            }
        }
        // (an answer that came late, to the settings' step no longer waited for)
        let late = (at.late.iter())
            .any(|reply| reply.first() == Some(&CMD_SAVE_SETTINGS) && accepted(reply));
        for step in steps.iter_mut().filter(|step| step.file.is_none()) {
            if step.state == State::Open && step.sent > 0 && late {
                step.state = State::Done;
            }
        }
        for name in [STANDBY_CLIP, BOOT_LOGO, SETTINGS, BOOT_SIGN] {
            size!(name);
        }
        if !known.playing {
            let what = "is its own clip playing (112)";
            let asked = at.command(&mut link, CMD_LOCAL_PLAYING, &[], what, &how, REPLY_MS);
            known.playing = told!(asked).is_some();
        }
        names!();
    }
    let Some(stay) = watch else {
        let outcome = match reading {
            true => Outcome::Asked,
            false => Outcome::Mute,
        };
        return Visited {
            outcome,
            replies: at.replies,
            unopened: None,
        };
    };
    // watched, without a word to it: is it still on the bus?
    let until = Instant::now() + stay;
    while Instant::now() < until {
        if bus.there().is_none() {
            let ms = at.ms();
            at.line(&format!("left the bus (it was there for {ms} ms)"));
            return Visited {
                outcome: Outcome::Left(ms),
                replies: at.replies,
                unopened: None,
            };
        }
        std::thread::sleep(WATCH);
    }
    if !reading {
        at.line("still on the bus, but it takes no packets");
        return Visited {
            outcome: Outcome::Mute,
            replies: at.replies,
            unopened: None,
        };
    }
    let what = "still there: is its own clip playing (112)";
    let outcome = match at.command(&mut link, CMD_LOCAL_PLAYING, &[], what, &how, REPLY_MS) {
        Said::Reply(_) => Outcome::Stayed,
        Said::Broke => broke(bus, &mut at),
        Said::Quiet | Said::NotTaken => {
            at.line("on the bus, but it does not answer any more");
            Outcome::Mute
        }
    };
    Visited {
        outcome,
        replies: at.replies,
        unopened: None,
    }
}

/// A transfer failed: the panel has left the bus (the system may list it a moment longer),
/// or it is still there and is taken anew.
fn broke(bus: &Bus, at: &mut Visit<'_>) -> Outcome {
    let ms = at.ms();
    let until = Instant::now() + LEAVING;
    loop {
        if bus.there().is_none() {
            at.line(&format!("left the bus (it was there for {ms} ms)"));
            return Outcome::Left(ms);
        }
        if Instant::now() >= until {
            at.line("the transfer failed and the panel is on the bus: taken anew");
            return Outcome::Lost(ms);
        }
        std::thread::sleep(Duration::from_millis(1));
    }
}

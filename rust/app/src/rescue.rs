//! `libre-panel start-screen`: a TURZX USB panel's own screens from the terminal, for a panel
//! that restarts over and over after its start screen was written (issue 52): on the USB for
//! a moment, gone again, without end.
//!
//! What that panel's log told (a 9.2", firmware `turzx_0001_0015`): the start screen's
//! files go to `usr/data`, where the panel's own settings are too (`app.cfg`). On that
//! panel the room there ran out: a boot logo ended at 16,384 bytes, later files were not
//! taken at all, and a clip written over the one it had ended at 704,512 bytes. 0.9.1 then
//! took the old clip away and wrote the new one again; the panel said it had all 706,912
//! bytes, its settings were saved (125), and from its next start it has been on the USB for
//! about 2 s and gone for 1, over and over. Why is not known. Two things fit: its settings
//! went into a store without room; or the clip's end is not on the panel as it was sent,
//! and the panel fails where its player gets there (the clip is 66 pictures, 2.2 s at the
//! panel's 30 a second: about as long as the panel stays). Either way the clip is what is
//! taken off. On other panels, a 9.2" and a 12.3" among them, the same files were written
//! for months without trouble: what is different on that panel is not known either.
//!
//! **What to expect.** The app (0.9.1) opened that panel thirteen times while it came and
//! went, and its sync was not answered once; of its looks every five seconds in the hours
//! after, not one ended with a panel in use. So the panel was not seen to take a command in
//! its two seconds, and the chance that it takes one here is small. What this command has
//! over the app's tries: it is there the moment the panel comes (the app started its engine
//! first), it goes on for as long as it is given, and it says to the millisecond how far
//! each coming gets: whether the panel can be opened, whether it takes what is sent (an
//! endpoint can take a packet or two by itself; the ones after only where something on the
//! panel reads), and whether it answers.
//!
//! **Which panels.** The vendor id 1CBE is shared by panels of several makers with protocols
//! of their own (Lian Li's are no TURZX panels), and the TURZX family's models and firmwares
//! may differ in what a command does. So: only a panel of the TURZX USB family is taken
//! (lp_devices::turzx_direct), the sync is the first thing it is sent and its answer must be
//! a TURZX panel's (`0A C8`, `turzx_`), and for a step only a model it was seen to work on
//! is taken (start_screen::MEASURED: the 9.2"). With several of the family's panels on one
//! PC the 9.2" is the one taken, or the model named (`--model`); another panel on the bus is
//! told in the run's lines and sent nothing. Every command sent here is one the 9.2" of
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
//! panel accepted is not sent again the next time it comes; the rest is. A wait goes on
//! until its time is over, however often the panel comes without an answer.
//!
//! What is sent, in this order whatever the order asked in:
//!
//! - 10, until it is answered once: sent again every 300 ms while it is not.
//! - `remove-clip`: 111, the clip the panel may be playing stopped (as the app does at each
//!   of its starts), then 42 `usr/data/standby.h264`.
//! - `remove-logo`: 42 `usr/data/boot.jpg` (as *Factory logo again*).
//! - 98 for the files taken off (a file is off when the panel says it has none; one that is
//!   still there is taken off once more) and for `usr/data/app.cfg`.
//! - 125 with start mode 0 (the settings saved with the start as the panel came), once the
//!   clip is off, never into a folder it still fills: with `default-start`, or after
//!   `remove-clip` where `app.cfg` is there and empty.
//!
//! Then, to see: 98 for the other files, 112 and the folder's listing (99). Nothing is
//! written to the panel's files.

use std::path::PathBuf;
use std::time::{Duration, Instant};

use lp_devices::turzx::{brightness_arg, firmware_of};
use lp_devices::turzx_direct::{self as direct, Direct, Other, Trouble, Which};

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
/// How long the sync's answer is waited for before the sync is sent again (within the first
/// reply's time): whether the panel takes the next packets tells whether anything on it
/// reads what is sent, which one packet cannot tell (an endpoint can take one by itself).
const SYNC_AGAIN_MS: u64 = 300;
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
/// How often a step is sent at most without the panel accepting it.
const SENDS: u32 = 6;
/// How often a file may still be there after the panel accepted its taking off.
const KEPT: u32 = 2;
/// How long a file the panel still has is left before it is taken off once more (a player
/// that was just stopped may hold its file a moment longer).
const AGAIN_AFTER: Duration = Duration::from_millis(200);
/// How long an opening that failed is left before the next: each one asks for the panel's
/// descriptors, which the hub may ask of the panel itself, and the panel is starting.
const OPEN_AGAIN: Duration = Duration::from_millis(5);
/// How many stays in a row without an answer (the panel left before it could be opened, or
/// was opened and said nothing) are told of line by line; after that, one line for every
/// `TOLD_EVERY` of them. (The panel is waited for all the same, until the time given is
/// over: a system that has to set it up anew may need many of its comings, and nobody knows
/// that a panel which said nothing a dozen times says nothing the next time.)
const TOLD_EACH: u32 = 8;
const TOLD_EVERY: u32 = 20;
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
    Usb {
        /// which panel is taken of several
        which: Which,
    },
    Test {
        which: Which,
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
    /// (boxed: what the system tells of a device is large on Windows, and Test has nothing)
    Usb(Box<direct::Seen>),
    Test,
}

impl Found {
    fn usb(seen: direct::Seen) -> Found {
        Found::Usb(Box::new(seen))
    }
}

impl Bus {
    fn new(which: Which) -> Bus {
        let env = |name: &str| std::env::var(name).ok().filter(|value| !value.is_empty());
        match env("LP_ENGINE_LINK") {
            Some(link) => Bus::Test {
                which,
                link,
                present: env("LP_ENGINE_PRESENT").map(PathBuf::from),
                others: env("LP_ENGINE_OTHERS").map(PathBuf::from),
                stay: env("LP_ENGINE_STAY_MS")
                    .and_then(|ms| ms.parse().ok())
                    .map(Duration::from_millis),
                model: env("LP_ENGINE_MODEL").unwrap_or_else(|| "turing-9.2-usb".to_string()),
            },
            None => Bus::Usb { which },
        }
    }

    fn there(&self) -> Option<Found> {
        match self {
            Bus::Usb { which } => direct::seen(which).map(Found::usb),
            Bus::Test { .. } => self.scan().0,
        }
    }

    /// A TURZX USB panel that is on the bus and is not the one taken, as it is called.
    fn another(&self) -> Option<String> {
        let (_, others) = self.scan();
        (others.into_iter().find(|other| other.panel)).map(|other| other.name)
    }

    /// The panel, and every other device on the bus, in one look.
    fn scan(&self) -> (Option<Found>, Vec<Other>) {
        match self {
            Bus::Usb { which } => {
                let (panel, others) = direct::scan(which);
                (panel.map(Found::usb), others)
            }
            Bus::Test {
                which,
                present,
                others,
                model,
                ..
            } => {
                let text = others
                    .as_ref()
                    .and_then(|file| std::fs::read_to_string(file).ok())
                    .unwrap_or_default();
                let mut others: Vec<Other> = text.lines().filter_map(other_of).collect();
                // (the simulated panel is taken as one on the bus is: its model first where
                // it is asked for, any model where no other is ruled out)
                let on_bus = present.as_ref().is_none_or(|file| file.exists());
                let taken = !which.only || which.models.contains(&model.as_str());
                if on_bus && !taken {
                    let pid = lp_devices::turzx::usb_models()
                        .find(|(_, known)| known.id == model.as_str())
                        .map(|(pid, _)| pid);
                    if let Some(pid) = pid {
                        let place = "simulated".to_string();
                        others.push(Other::new(
                            lp_devices::turzx::VENDOR_ID,
                            pid,
                            place,
                            String::new(),
                        ));
                    }
                }
                ((on_bus && taken).then_some(Found::Test), others)
            }
        }
    }

    /// The model the panel is, by the catalogue's id, and as it is called.
    fn model(&self, found: &Found) -> Option<(String, String)> {
        let model = match (self, found) {
            (Bus::Test { model, .. }, _) => lp_devices::models::find_model(model)?,
            (Bus::Usb { .. }, Found::Usb(seen)) => seen.model()?,
            (Bus::Usb { .. }, Found::Test) => return None,
        };
        Some((model.id.to_string(), model.label()))
    }

    fn open(&self, found: &Found) -> Result<Direct, String> {
        match (self, found) {
            (Bus::Test { link, .. }, _) => Direct::test(link),
            (Bus::Usb { .. }, Found::Usb(seen)) => seen.open(),
            (Bus::Usb { .. }, Found::Test) => Err("no panel".into()),
        }
    }
}

/// A line of the simulated bus's other devices: `vid:pid|place|name`.
fn other_of(line: &str) -> Option<Other> {
    let mut parts = line.splitn(3, '|');
    let (vid, pid) = parts.next()?.split_once(':')?;
    Some(Other::new(
        u16::from_str_radix(vid, 16).ok()?,
        u16::from_str_radix(pid, 16).ok()?,
        parts.next()?.to_string(),
        parts.next().unwrap_or_default().to_string(),
    ))
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
    /// its lines that were kept back and not said (None: they were said)
    kept: Option<Vec<String>>,
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
    /// its lines kept back until the panel answers (one that came many times without an
    /// answer: a visit is told once it brings one); None: each line is said as it comes
    kept: Option<Vec<String>>,
    /// answers still to come to syncs that were sent again before the first was answered
    owed: u32,
}

impl Visit<'_> {
    fn ms(&self) -> u128 {
        self.since.elapsed().as_millis()
    }

    fn line(&mut self, text: &str) {
        self.line_at(self.ms(), text);
    }

    /// A line for the moment `ms` after the panel came.
    fn line_at(&mut self, ms: u128, text: &str) {
        let line = format!("[{}] +{ms} ms: {text}", self.number);
        match &mut self.kept {
            Some(kept) => kept.push(line),
            None => (self.say)(&line),
        }
    }

    /// An answer came: counted, and what was kept back is said.
    fn answered(&mut self) {
        self.replies += 1;
        for line in self.kept.take().unwrap_or_default() {
            (self.say)(&line);
        }
    }

    /// The visit is over (`unopened`: why the panel could not be opened in it).
    fn ended(&mut self, outcome: Outcome, unopened: Option<String>) -> Visited {
        Visited {
            outcome,
            replies: self.replies,
            unopened,
            kept: self.kept.take(),
        }
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
            // (the time it really took: the system ends a transfer early where the panel
            // is leaving)
            let ms = began.elapsed().as_millis();
            return match trouble {
                Trouble::Silent => {
                    self.line(&format!(
                        "{what}: the panel did not take the packet in {ms} ms"
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
        let asked = Instant::now();
        let until = asked + Duration::from_millis(wait);
        loop {
            let left = until.saturating_duration_since(Instant::now()).as_millis() as u64;
            let got = match left {
                0 => Err(Trouble::Silent),
                left => link.read(left),
            };
            match got {
                Ok(reply) if reply.first() == Some(&cmd) => {
                    self.answered();
                    let answered = began.elapsed().as_millis();
                    self.line(&format!(
                        "{what}: {} [{}] (taken after {taken} ms, answered after {answered} ms)",
                        tell(&reply),
                        hex(&reply, 12)
                    ));
                    return Said::Reply(reply);
                }
                // (the answer to a sync that was sent again: nothing new)
                Ok(other) if self.owed > 0 && other.first() == Some(&CMD_SYNC) => {
                    self.owed -= 1;
                }
                Ok(other) => {
                    self.answered();
                    self.line(&format!(
                        "{what}: an answer to something else came first [{}]",
                        hex(&other, 12)
                    ));
                    self.late.push(other);
                }
                Err(Trouble::Silent) => {
                    let waited = asked.elapsed().as_millis();
                    self.line(&format!(
                        "{what}: taken after {taken} ms, no answer in {waited} ms"
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

    /// The sync, until the panel answers it: sent again every `SYNC_AGAIN_MS` while no
    /// answer comes, for `patience` in all. A panel whose program is not reading takes
    /// at most a packet or two into its endpoint: so whether it goes on taking them tells
    /// whether anything on it reads what is sent. (The app's own sync was not answered once
    /// in thirteen openings of the panel of issue 52 after it began to restart.)
    fn greet(&mut self, link: &mut Direct, patience: Duration) -> Said {
        let what = "sync (10)";
        let began = Instant::now();
        let until = began + patience;
        let mut taken: Vec<u128> = Vec::new(); // (how long the panel took to take each)
        loop {
            let sending = Instant::now();
            if let Err(trouble) = link.send(CMD_SYNC, &[], TAKE_MS) {
                let ms = sending.elapsed().as_millis();
                let said = match (&trouble, taken.is_empty()) {
                    (Trouble::Silent, true) => {
                        format!("the panel did not take the packet in {ms} ms")
                    }
                    (Trouble::Failed(why), true) => format!("not sent ({why})"),
                    (Trouble::Silent, false) => format!(
                        "{}, not answered; the next was not taken in {ms} ms",
                        sent_said(&taken)
                    ),
                    (Trouble::Failed(why), false) => format!(
                        "{}, not answered; the next was not taken: the transfer failed \
                         after {ms} ms ({why})",
                        sent_said(&taken)
                    ),
                };
                self.line(&format!("{what}: {said}"));
                return match trouble {
                    Trouble::Silent => Said::NotTaken,
                    Trouble::Failed(_) => Said::Broke,
                };
            }
            taken.push(sending.elapsed().as_millis());
            let again = (Instant::now() + Duration::from_millis(SYNC_AGAIN_MS)).min(until);
            loop {
                let left = again.saturating_duration_since(Instant::now()).as_millis() as u64;
                let got = match left {
                    0 => Err(Trouble::Silent),
                    left => link.read(left),
                };
                match got {
                    Ok(reply) if reply.first() == Some(&CMD_SYNC) => {
                        self.answered();
                        self.owed = taken.len() as u32 - 1;
                        let answered = began.elapsed().as_millis();
                        let how = match taken.as_slice() {
                            [taken] => {
                                format!("taken after {taken} ms, answered after {answered} ms")
                            }
                            _ => format!("{}; answered after {answered} ms", sent_said(&taken)),
                        };
                        self.line(&format!(
                            "{what}: {} [{}] ({how})",
                            firmware_said(&reply),
                            hex(&reply, 12)
                        ));
                        return Said::Reply(reply);
                    }
                    Ok(other) => {
                        self.answered();
                        self.line(&format!(
                            "{what}: an answer to something else came first [{}]",
                            hex(&other, 12)
                        ));
                        self.late.push(other);
                    }
                    Err(Trouble::Silent) => {
                        // (a read the system ended early: the next sync waits its time all
                        // the same)
                        std::thread::sleep(again.saturating_duration_since(Instant::now()));
                        break;
                    }
                    Err(Trouble::Failed(why)) => {
                        self.line(&format!(
                            "{what}: {}, then no answer ({why})",
                            sent_said(&taken)
                        ));
                        return Said::Broke;
                    }
                }
            }
            if Instant::now() >= until {
                self.line(&format!(
                    "{what}: {}, no answer in {} ms",
                    sent_said(&taken),
                    began.elapsed().as_millis()
                ));
                return Said::Quiet;
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
                self.answered();
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

/// How the panel took the syncs sent to it: "taken after 2 ms", or "sent 5 times, each
/// taken after 0 to 2 ms".
fn sent_said(taken: &[u128]) -> String {
    let (least, most) = (
        taken.iter().min().copied().unwrap_or(0),
        taken.iter().max().copied().unwrap_or(0),
    );
    match taken.len() {
        0 | 1 => format!("taken after {least} ms"),
        count if least == most => format!("sent {count} times, each taken after {least} ms"),
        count => format!("sent {count} times, each taken after {least} to {most} ms"),
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
    /// a TURZX USB panel that was on the bus and was not the one taken, as it is called
    another: Option<String>,
}

fn device_said(device: &Other) -> String {
    let name = match (device.name.is_empty(), device.panel) {
        (true, _) => String::new(),
        (false, true) => format!(" {}", device.name), // (its model: Turing 12.3")
        (false, false) => format!(" \"{}\"", device.name),
    };
    format!(
        "{:04x}:{:04x}{name} ({})",
        device.vid, device.pid, device.place
    )
}

/// What is said of a panel on the bus that is not the one taken.
const LEFT_ALONE: &str = "another panel, nothing is sent to it";

impl Diary {
    fn note(&mut self, now: Vec<Other>, say: &mut dyn FnMut(&str)) {
        if self.another.is_none() {
            self.another = (now.iter().find(|device| device.panel)).map(|d| d.name.clone());
        }
        let mut lines = Vec::new();
        match &self.seen {
            // the first look: only what the makers of these panels have on the bus
            None => {
                for device in now.iter().filter(|device| RELATED.contains(&device.vid)) {
                    lines.push(match device.panel {
                        true => format!("{} is on the bus: {LEFT_ALONE}", device_said(device)),
                        false => format!("{} is on the bus", device_said(device)),
                    });
                }
            }
            Some(before) if *before == now => return,
            Some(before) => {
                for device in before.iter().filter(|device| !now.contains(device)) {
                    lines.push(format!("{} left", device_said(device)));
                }
                for device in now.iter().filter(|device| !before.contains(device)) {
                    lines.push(match device.panel {
                        true => format!("{} came: {LEFT_ALONE}", device_said(device)),
                        false => format!("{} came", device_said(device)),
                    });
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
    model: Option<&'static str>,
    say: &mut dyn FnMut(&str),
) -> Result<(), String> {
    let mut steps = plan(actions, brightness)?;
    let called = |id: &str| {
        lp_devices::models::find_model(id).map_or_else(|| id.to_string(), |model| model.label())
    };
    if let Some(id) = model.filter(|id| !steps.is_empty() && !MEASURED.contains(id)) {
        return Err(format!(
            "these steps are known on the Turing 9.2\" only: nothing is sent to a {}",
            called(id)
        ));
    }
    // which panel of several: the model asked for and no other; else the 9.2", and for a
    // step no other (the family's other models, and other makers' panels in it, may take
    // the command for something else)
    let bus = Bus::new(match model {
        Some(id) => Which {
            models: vec![id],
            only: true,
        },
        None => Which {
            models: MEASURED.to_vec(),
            only: !steps.is_empty(),
        },
    });
    // (a packet is stamped with the local time: the zone is read now, not when the panel
    // comes; reading it took tens of milliseconds of the first packet's time)
    let _ = lp_devices::turzx_packet::now_stamp();
    let mut known = Known::default();
    let Some(limit) = wait else {
        let Some(found) = bus.there() else {
            return Err(match (model, bus.another()) {
                (Some(id), Some(other)) => format!(
                    "no {} is on the bus (a {other} is: another panel, nothing was sent to it)",
                    called(id)
                ),
                (Some(id), None) => format!("no {} is on the bus", called(id)),
                (None, Some(other)) => format!(
                    "these steps are known on the Turing 9.2\" only; this panel is a {other}: \
                     nothing was sent to it"
                ),
                (None, None) => lp_i18n::t("No TURZX USB panel found"),
            });
        };
        let came = Came {
            number: 1,
            since: Instant::now(),
            waited: None,
            again: false,
            hush: Hush::No,
            carried: Vec::new(),
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
    /// it was opened in this stay
    opened: bool,
    /// why it could not be opened, the last time that was tried
    unopened: String,
    /// the lines of its visits that are kept back until it answers
    kept: Vec<String>,
}

/// Which of a visit's lines are kept back, for a panel that has come many times in a row
/// without an answer.
#[derive(Clone, Copy, PartialEq, Eq)]
enum Hush {
    /// none: each line is said as it comes
    No,
    /// its coming is said once it is opened (it left before that every time so far)
    UntilOpen,
    /// all of them are said once the panel answers, and not at all where it does not
    UntilAnswer,
}

/// The stays in a row that brought no answer (the panel left before it could be opened, or
/// was opened and said nothing): told of line by line at first, then once in a while. They
/// do not end a wait: the system may need many of the panel's comings until it has it ready,
/// and nobody knows that a panel which said nothing a dozen times says nothing the next
/// time. The panel is waited for until the time given is over.
#[derive(Default)]
struct Quiet {
    /// such stays in a row
    run: u32,
    /// of them, those in which the panel was opened
    opened: u32,
    /// since the last line that told of them: those it could not be opened in (and why, the
    /// last time), those it was opened in, and how long it was on the bus in these
    shut: u32,
    why: String,
    open: u32,
    lives: Option<(u128, u128)>,
}

impl Quiet {
    /// Which lines of the panel's next visit are kept back.
    fn hush(&self) -> Hush {
        match (self.run >= TOLD_EACH, self.opened) {
            (false, _) => Hush::No,
            (true, 0) => Hush::UntilOpen,
            (true, _) => Hush::UntilAnswer,
        }
    }

    /// A stay without an answer is over: the panel was on the bus for `ms`; `shut`: why it
    /// could not be opened (None: it was).
    fn once_more(&mut self, number: u32, ms: u128, shut: Option<&str>, say: &mut dyn FnMut(&str)) {
        self.run += 1;
        self.opened += u32::from(shut.is_none());
        if self.run < TOLD_EACH {
            return;
        }
        if self.run == TOLD_EACH {
            say(&match self.opened {
                0 => format!(
                    "the panel keeps leaving before the system has it ready to be opened: \
                     from here on one line for every {TOLD_EVERY} times it comes"
                ),
                _ => format!(
                    "the panel keeps leaving without an answer: from here on one line for \
                     every {TOLD_EVERY} times it comes, until the time is over (Ctrl+C ends \
                     it sooner)"
                ),
            });
            return;
        }
        match shut {
            Some(why) => {
                self.shut += 1;
                self.why = why.to_string();
            }
            None => {
                self.open += 1;
                self.lives = Some(match self.lives {
                    None => (ms, ms),
                    Some((least, most)) => (least.min(ms), most.max(ms)),
                });
            }
        }
        if !(self.run - TOLD_EACH).is_multiple_of(TOLD_EVERY) {
            return;
        }
        let lives = match self.lives {
            None => String::new(),
            Some((least, most)) if least == most => format!(" (on the bus for {least} ms)"),
            Some((least, most)) => format!(" (on the bus for {least} to {most} ms)"),
        };
        say(&match (self.shut, self.open) {
            (_, 0) => format!(
                "[{number}] not opened yet: it came {TOLD_EVERY} more times and left before \
                 it could be opened ({})",
                self.why
            ),
            (0, _) => format!(
                "[{number}] no answer yet: it came {TOLD_EVERY} more times, was opened and \
                 left without an answer{lives}"
            ),
            (shut, open) => format!(
                "[{number}] no answer yet: it came {TOLD_EVERY} more times: {} it left \
                 before it could be opened ({}), {} it was opened and left without an \
                 answer{lives}",
                times(shut),
                self.why,
                times(open)
            ),
        });
        (self.shut, self.open, self.lives) = (0, 0, None);
    }
}

/// "once", "3 times".
fn times(count: u32) -> String {
    match count {
        1 => "once".to_string(),
        count => format!("{count} times"),
    }
}

/// When a run that waits has seen enough.
#[derive(Default)]
struct Tally {
    /// stays since no step is open any more
    settled: u32,
}

impl Tally {
    /// A stay in which the panel was opened is over: whether the panel is waited for once
    /// more. One with a step still open is, until the time is over, whatever it said; one
    /// with none is watched `MORE_VISITS` times more.
    fn goes_on(&mut self, steps: &[Step]) -> bool {
        if steps.iter().any(|step| step.state == State::Open) {
            return true;
        }
        self.settled += 1;
        self.settled <= MORE_VISITS
    }
}

/// A stay is over, `ms` after the panel came: told of where it brought no answer; whether
/// the panel is waited for once more.
fn stay_over(
    over: &Stay,
    ms: u128,
    quiet: &mut Quiet,
    tally: &mut Tally,
    steps: &[Step],
    say: &mut dyn FnMut(&str),
) -> bool {
    match over.answers {
        0 => {
            let shut = (!over.opened).then_some(over.unopened.as_str());
            quiet.once_more(over.number, ms, shut, say);
        }
        _ => *quiet = Quiet::default(),
    }
    // (a stay in which it could not be opened is no visit: the system may need many)
    !over.opened || tally.goes_on(steps)
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
        another: None,
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
    let mut quiet = Quiet::default();
    loop {
        let (found, others) = bus.scan();
        diary.note(others, say);
        let Some(found) = found else {
            // (it left between two tries: that stay is over)
            if let Some(over) = stay.take() {
                let ms = over.since.elapsed().as_millis();
                lives.push(ms);
                if !stay_over(&over, ms, &mut quiet, &mut tally, steps, say) {
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
                opened: false,
                unopened: String::new(),
                kept: Vec::new(),
            }
        });
        let came = Came {
            number: now.number,
            since: now.since,
            waited: Some(now.since.duration_since(began)),
            again,
            // (a stay that has answered is told as it goes, whatever came before it)
            hush: match now.answers {
                0 => quiet.hush(),
                _ => Hush::No,
            },
            carried: std::mem::take(&mut now.kept),
        };
        let watch = stay_for(bus, &lives);
        let visited = visit(bus, found, &came, steps, known, Some(watch), say);
        replies += visited.replies;
        now.answers += visited.replies;
        // (lines kept back: said with the stay's first answer; never where the panel leaves
        // without one; at once where it stays on the bus and says nothing, cannot be opened
        // while it is there, or is no panel for this)
        match (&visited.outcome, visited.kept) {
            (Outcome::Left(_) | Outcome::Lost(_), Some(kept)) => now.kept = kept,
            (_, Some(kept)) => {
                for line in &kept {
                    say(line);
                }
            }
            (_, None) => {}
        }
        match &visited.unopened {
            Some(why) => now.unopened.clone_from(why),
            None => now.opened = true,
        }
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
                if let Some(over) = stay.take() {
                    if !stay_over(&over, ms, &mut quiet, &mut tally, steps, say) {
                        break;
                    }
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
        let mut said = format!("the panel was not on the bus in {} s", limit.as_secs());
        if let Some(other) = diary.another {
            said += &format!(" (a {other} was: another panel, nothing was sent to it)");
        }
        return Err(said);
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
    let mut said = match mute {
        true => "the panel is on the bus and does not answer".to_string(),
        false => format!("the panel came {} and did not stay", times(number)),
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
    /// which of its lines are kept back (it came many times in a row without an answer)
    hush: Hush,
    /// the lines of this stay's earlier visits that are still kept back
    carried: Vec<String>,
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
        kept: (came.hush == Hush::UntilAnswer).then(|| came.carried.clone()),
        owed: 0,
    };
    // (its coming: said at once, or only once it is opened where it has left before that
    // many times in a row)
    let mut held = None;
    if !came.again {
        let waited = came.waited.map_or_else(String::new, |waited| {
            format!(", {:.1} s into the wait", waited.as_secs_f64())
        });
        let line = format!("on the bus: {}{waited}", named(&found));
        match came.hush {
            Hush::UntilOpen => held = Some((at.ms(), line)),
            Hush::No | Hush::UntilAnswer => at.line(&line),
        }
    }
    // a step only to a model it was seen to work on: the family's other models, and other
    // makers' panels in it, may take the command for something else. (The bus takes no
    // other panel for a step; this is the last word before anything is sent.)
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
            return at.ended(Outcome::NotThis(why), None);
        }
    }
    // opened: tried again every few milliseconds while it is there (Windows lists a device
    // before its driver has started)
    let mut tries = 0u32;
    let mut failed = String::new();
    let opening = Instant::now();
    let mut link = loop {
        tries += 1;
        match bus.open(&found) {
            Ok(link) => break link,
            Err(why) => failed = why,
        }
        if tries.is_multiple_of(8) {
            match bus.there() {
                Some(now) => found = now, // (as the system has it now)
                None => {
                    let ms = at.ms();
                    if held.is_none() {
                        at.line(&format!(
                            "left the bus before it could be opened ({tries} tries; {failed})"
                        ));
                    }
                    return at.ended(Outcome::Left(ms), Some(failed));
                }
            }
        }
        if opening.elapsed() >= OPEN_FOR {
            if let Some((ms, line)) = held.take() {
                at.line_at(ms, &line);
            }
            at.line(&format!("not opened in {} s: {failed}", OPEN_FOR.as_secs()));
            return at.ended(Outcome::NotOpened(failed.clone()), Some(failed));
        }
        std::thread::sleep(OPEN_AGAIN);
    };
    if let Some((ms, line)) = held.take() {
        at.line_at(ms, &line);
    }
    match tries {
        1 => at.line("open"),
        tries => at.line(&format!(
            "open at try {tries}: {} (before: {failed})",
            named(&found)
        )),
    }
    // the sync first, until the panel has answered it once as a TURZX panel does; then what
    // was asked for: the clip it may be playing stopped, the files taken off, their sizes
    // asked, and the settings saved where that is asked for or needed. Then what the panel
    // holds (asked once: `known`). A transfer that fails ends the visit: the panel has left
    // the bus, or is taken anew. Once no step is open, a visit sends nothing of this: it
    // watches whether the panel stays by itself.
    let mut reading = true; // (false: the panel takes no packets; nothing more is sent)
    let mut silent = false; // (the sync was not answered in all the time it had)
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
                        return at.ended(outcome, None);
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
            // (in a wait, for as long as the panel is watched: one that stays longer than
            // the first answer's time and answers late in its stay is heard)
            let first = Duration::from_millis(FIRST_REPLY_MS);
            let patience = watch.map_or(first, |stay| stay.max(first));
            let Some(reply) = told!(at.greet(&mut link, patience)) else {
                silent = true;
                break 'talk; // (not answered: nothing else is sent this time)
            };
            if !accepted(&reply) || firmware_of(&reply).is_empty() {
                let why = format!(
                    "the panel does not answer the sync as a TURZX panel does ({}): nothing \
                     was changed on it",
                    hex(&reply, 16)
                );
                return at.ended(Outcome::NotThis(why), None);
            }
            known.spoken = true;
        }
        settle(steps, known, &mut at); // (a file the panel said it has not: nothing to send)
        for round in 0..2 {
            // the clip the panel may be playing is stopped before its file is taken off: a
            // file was only ever seen taken off a panel that was not playing it (the app
            // stops the clip at each of its starts, 110 then 111), and a panel that fails
            // at its clip's end stays up once the clip has stopped
            let clip_open = (steps.iter())
                .any(|step| step.file == Some(STANDBY_CLIP) && step.state == State::Open);
            if clip_open && round == 0 {
                let what = "stop the clip it may be playing (111)";
                told!(at.command(&mut link, CMD_STOP_LOCAL, &[], what, &how, REPLY_MS));
            }
            if round > 0 {
                std::thread::sleep(AGAIN_AFTER);
            }
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
        // (asked once: a panel that took what was sent and said nothing has not answered)
        let outcome = match reading && at.replies > 0 {
            true => Outcome::Asked,
            false => Outcome::Mute,
        };
        return at.ended(outcome, None);
    };
    if silent {
        // (it was on the bus all the time it would be watched, and said nothing)
        at.line("still on the bus, and it has not answered");
        return at.ended(Outcome::Mute, None);
    }
    // watched, without a word to it: is it still on the bus?
    let until = Instant::now() + stay;
    while Instant::now() < until {
        if bus.there().is_none() {
            let ms = at.ms();
            at.line(&format!("left the bus (it was there for {ms} ms)"));
            return at.ended(Outcome::Left(ms), None);
        }
        std::thread::sleep(WATCH);
    }
    if !reading {
        at.line("still on the bus, but it takes no packets");
        return at.ended(Outcome::Mute, None);
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
    at.ended(outcome, None)
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

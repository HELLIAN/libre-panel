//! The panel's own start screen from the app (editor/server.py `_start_screen*`,
//! devices/panel_files.py): the TURZX USB panels whose boot logo and standby clip Libre Panel
//! makes, the app's pictures of them, and the files as the panel takes them.

use std::collections::VecDeque;
use std::sync::Mutex;

use lp_devices::models::PanelModel;
use lp_pixels::{Image, Mode};
use lp_py::{Dict, Value};
use lp_render::boot;

/// The boot logo's file on the panel, and the standby clip's (the vendor app's name).
pub const BOOT_LOGO: &str = "usr/data/boot.jpg";
pub const STANDBY_CLIP: &str = "usr/data/standby.h264";
/// the panels the boot picture and the standby clip were measured on (panel_files.py
/// MEASURED: SPUR II on the 9.2"); the other USB panels share its firmware, untried
pub const MEASURED: &[&str] = &["turing-9.2-usb"];
/// The only files written or taken off.
const OURS: [&str; 2] = [BOOT_LOGO, STANDBY_CLIP];
/// A file on the panel is at most this large (what the command takes).
pub const MAX_FILE: usize = 1024 * 1024;
/// The boot logo's JPEG: its quality in turn until the file fits its room, every pixel with
/// its own colour; every whole quality from 95 down (panel_files.py JPEG_STEPS, since 0.9.5).
/// A look that would need less than the last would show it: not made.
const JPEG_STEPS: [i64; 11] = [95, 94, 93, 92, 91, 90, 89, 88, 87, 86, 85];
/// The standby clip's x264 crf: the finest first, then coarser until the clip fits its room,
/// up to the coarsest that does not show; a look that needs more is not made.
const CLIP_CRF: i64 = 20;
const CLIP_CRF_MAX: i64 = 26;
/// Below a whole step that fits, where the next finer one does not: these quarters of a step,
/// made at once (each encoder has one thread), and the finest that fits is the clip
/// (panel_files.py CLIP_QUARTERS, since 0.9.5: a whole step left up to 13 % of the room
/// unused).
const CLIP_QUARTERS: [f64; 3] = [0.75, 0.5, 0.25];
/// How much smaller a clip gets with each step of the crf, in thousandths: the first guess at
/// the crf that fits.
const CLIP_STEP: usize = 870;
/// How much Libre Panel puts on a panel: the most bytes of the standby clip and of the boot
/// logo (panel_files.py ROOM; issue 52). Both go where the panel keeps its own settings; how
/// much room there is cannot be asked, and one 9.2" was full at about 718,000 bytes of these
/// files. So the two together stay well below what a panel of the model is known to hold: on
/// the 9.2" two thirds of that; on the 12.3" four fifths of the 817,587 one has held for weeks.
/// The 8.8" gets the 9.2"'s: its maker sells the two as one, with the same framebuffer, and
/// the panel's own start screen was first read from the 8.8"'s firmware (SPUR II). (The first
/// is the 9.2"'s: a panel of another model gets it by its pixels.)
pub const ROOM: [(&str, (usize, usize)); 3] = [
    ("turing-9.2-usb", (360_000, 120_000)),
    ("turing-8.8-usb", (360_000, 120_000)),
    ("turing-12.3-usb", (500_000, 150_000)),
];
/// The looks that do not fit that room finely on a model, known before anything is made
/// (panel_files.py UNFIT): the card says so and offers no writing. For a model no one has
/// tried, measured in its room by the pixels, both files and both ways mounted.
pub const UNFIT: [(&str, &[&str]); 10] = [
    ("turing-1.6-usb", &["aurora", "circuit", "levels"]),
    ("turing-2.1-usb", &["aurora", "levels"]),
    ("turing-2.8-usb", &["aurora", "levels"]),
    ("turing-4.6-usb", &["aurora", "pulse", "levels"]),
    ("turing-5.2-usb", &["levels"]),
    ("turing-8-usb", &["levels"]),
    ("turing-8.8-usb", &["levels"]),
    ("turing-9.2-usb", &["levels"]),
    ("turing-12.3-usb", &["levels"]),
    ("jonsbo-tf3-pump", &["aurora", "levels"]),
];
/// A write that happens takes its time (the 9.2" of issue 52: 1.2 to 2.9 s for 700 KB; a sound
/// 9.2" and 12.3" wrote 45 to 485 KB at 0.94 MB a second at the most, 0.9.4rc1), one the panel
/// does not take is over when the transfer is (30 to 50 ms for 700 KB, 14 MB a second and
/// more): a file of some size that was answered faster than this many bytes a second was not
/// written.
const WRITE_RATE_MOST: usize = 4_000_000;
/// (below it the transfer's own time says nothing. On the 9.2" and the 12.3" every clip is
/// larger: the smallest, Dark's on the 9.2", has about 44,000 bytes; only Dark's boot logos
/// are smaller, and after a logo nothing is saved on the panel)
const WRITE_TIMED_FROM: usize = 32 * 1024;
/// Files are written to the models it has been done on (ROOM). Another is written to only
/// where its owner agrees to try it (the app asks each time: `untested`), or where this is set
/// in the environment (panel_files.py WRITES_ENV): the files are then made for the 9.2"'s
/// room, by the pixels.
pub const WRITES_ENV: &str = "LIBRE_PANEL_PANEL_WRITES";
pub const WRITES_WORD: &str = "at-my-own-risk";
/// The models whose start screens have been written on a panel of theirs and seen at its next
/// start; the 8.8" is written as the 9.2" without that yet (panel_files.py CONFIRMED).
pub const CONFIRMED: [&str; 2] = ["turing-9.2-usb", "turing-12.3-usb"];
/// Where an owner who tried it says how it went: the start screen's issue form, which the app
/// fills in with the model, the version, the look and what it wrote.
pub const REPORT_FORM: &str =
    "https://github.com/syntensa/libre-panel/issues/new?template=start_screen.yml";

/// tried: whether start screens are written to panels of `model` by what is known of its room
/// (ROOM: the models it has been done on, and the 8.8" as the 9.2").
pub fn tried(model: Option<&PanelModel>) -> bool {
    model.is_some_and(|model| ROOM.iter().any(|(id, _)| *id == model.id))
}

/// room: the most bytes of the standby clip and of the boot logo on a panel of `model`: what
/// ROOM says for the models written to; for another (and for none), the 9.2"'s for as many
/// pixels as it has, and never more.
pub fn room(model: Option<&PanelModel>) -> (usize, usize) {
    let (clip, logo) = ROOM[0].1;
    let Some(model) = model else {
        return (clip, logo);
    };
    if let Some((_, known)) = ROOM.iter().find(|(id, _)| *id == model.id) {
        return *known;
    }
    let most = 1920 * 480;
    let pixels = usize::try_from(model.native_width * model.native_height).unwrap_or(0);
    let share = pixels.min(most);
    (clip * share / most, logo * share / most)
}

/// writes_on: whether files are written to a panel of `model`: to the models it has been
/// done on; to another where its owner agreed to try it (`untested`: the app asked, naming
/// what may happen); and to any for who asks for it in the environment.
pub fn writes_on(model: Option<&PanelModel>, untested: bool) -> bool {
    tried(model) || untested || std::env::var(WRITES_ENV).is_ok_and(|word| word == WRITES_WORD)
}

/// writes_off: why nothing is written to a panel of another model without its owner's word,
/// as the app says it.
pub fn writes_off() -> String {
    lp_i18n::t(
        "No one has tried start screens on this panel yet: they are written to it only where \
         you agree to try it, at your own risk, in Settings → Panel → Start screen.",
    )
}

/// too_fine: why a look is not made for a panel: it would have to be coarser than shows.
fn too_fine() -> String {
    lp_i18n::t(
        "This look has more detail than fits in the room Libre Panel uses on this panel, and \
         it is not written coarser: choose another look.",
    )
}

/// kilobytes: a file's size as the app says it.
fn kilobytes(size: i64) -> String {
    format!("{} KB", ((size + 512) / 1024).max(1))
}

/// A message of the app's with its values filled in.
fn said(text: &str, values: &[(&str, Value)]) -> String {
    lp_i18n::t_format(text, values).unwrap_or_else(|why| why.msg)
}

/// no_room: why a file is not sent: with the other one that is on the panel it would be more
/// than Libre Panel puts there (`other`: that one's bytes; it is one of before 0.9.4).
fn no_room(name: &str, other: i64) -> String {
    let size = [("size", Value::Str(kilobytes(other)))];
    if name == BOOT_LOGO {
        return said(
            "Not written: with the standby clip that is on the panel ({size}), the boot logo \
             would be more than Libre Panel puts on this panel. Write the standby clip again \
             first: it becomes smaller.",
            &size,
        );
    }
    said(
        "Not written: with the boot logo that is on the panel ({size}), the standby clip \
         would be more than Libre Panel puts on this panel. Write the boot logo again first: \
         it becomes smaller.",
        &size,
    )
}

/// not_taken: what is said of a file the panel did not take.
fn not_taken() -> String {
    lp_i18n::t(
        "The panel did not take the file: it has what it had before, and nothing was changed. \
         One panel did that when it had no room left.",
    )
}

/// cut_off: what is said of a file that ended early and was taken off again.
fn cut_off(name: &str, written: i64, size: i64) -> String {
    let values = [("written", whole(written)), ("size", whole(size))];
    if name == BOOT_LOGO {
        return said(
            "The boot logo did not fit: the panel took {written} of {size} bytes. It was taken \
             off again, so that the panel is not left full; the factory logo shows at the next \
             power-on.",
            &values,
        );
    }
    said(
        "The standby clip did not fit: the panel took {written} of {size} bytes. It was taken \
         off again, so that the panel is not left full: the panel has no standby clip now. \
         Dark is the smallest look.",
        &values,
    )
}

/// still_there: what is said of a file that ended early and could not be taken off again.
fn still_there(name: &str, written: i64, size: i64, why: &str) -> String {
    said(
        "{file} did not fit (the panel took {written} of {size} bytes) and could not be taken \
         off again: {why}",
        &[
            ("file", Value::Str(name.into())),
            ("written", whole(written)),
            ("size", whole(size)),
            ("why", Value::Str(why.into())),
        ],
    )
}

/// its_room: a file of `size` bytes that is larger than its room on the model is not sent at
/// all (the files made here never are: nothing else gets through).
fn its_room(name: &str, size: usize, model: Option<&PanelModel>) -> Result<(), String> {
    let (clip, logo) = room(model);
    let most = if name == BOOT_LOGO { logo } else { clip };
    if size > most {
        return Err(format!(
            "{name}: {size} bytes are more than the {most} it gets"
        ));
    }
    Ok(())
}

/// unwritten: whether a file of `size` bytes that the panel answered after `took` cannot
/// have been written: faster than any write is.
fn unwritten(size: usize, took: std::time::Duration) -> bool {
    size >= WRITE_TIMED_FROM && took.as_secs_f64() * (WRITE_RATE_MOST as f64) < size as f64
}

/// How many of the app's pictures and of the files are kept (lru_cache's maxsize).
const PICTURES_KEPT: usize = 16;
const FILES_KEPT: usize = 4;

/// models: the panels this works on: the TURZX USB ones.
pub fn models() -> impl Iterator<Item = &'static PanelModel> {
    lp_devices::models::MODELS
        .iter()
        .filter(|model| model.protocol == "usb-turing")
}

/// model_of: a TURZX USB panel by its id.
pub fn model_of(id: &str) -> Option<&'static PanelModel> {
    models().find(|model| model.id == id)
}

/// design_of: a look of the panel's own screens by its name (Libre Panel's own for none).
pub fn design_of(name: &str) -> Option<&'static str> {
    let name = if name.is_empty() { boot::DEFAULT } else { name };
    boot::DESIGNS.iter().copied().find(|design| *design == name)
}

/// The app's pictures and the files made, the newest last.
#[derive(Default)]
pub struct Kept {
    pictures: Mutex<VecDeque<(String, Vec<u8>)>>,
    files: Mutex<VecDeque<(String, Vec<u8>)>>,
}

fn kept_or(
    kept: &Mutex<VecDeque<(String, Vec<u8>)>>,
    most: usize,
    key: String,
    make: impl FnOnce() -> Result<Vec<u8>, String>,
) -> Result<Vec<u8>, String> {
    {
        let mut list = kept.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(at) = list.iter().position(|(k, _)| *k == key) {
            let found = list.remove(at).expect("(there)");
            list.push_back(found.clone());
            return Ok(found.1);
        }
    }
    let made = make()?;
    let mut list = kept.lock().unwrap_or_else(|e| e.into_inner());
    list.push_back((key, made.clone()));
    while list.len() > most {
        list.pop_front();
    }
    Ok(made)
}

impl Kept {
    /// Every picture and file kept let go (light mode: the app is closed).
    pub fn forget(&self) {
        for kept in [&self.pictures, &self.files] {
            *kept.lock().unwrap_or_else(|e| e.into_inner()) = VecDeque::new();
        }
    }

    /// cached_preview: the app's picture of the boot logo (PNG) or of the standby clip (an
    /// animated WebP), `width` px wide.
    pub fn preview(
        &self,
        env: &lp_theme::Env,
        model: &PanelModel,
        what: &str,
        width: i64,
        way: &str,
        design: &str,
    ) -> Result<Vec<u8>, String> {
        let key = format!("{}|{what}|{width}|{way}|{design}", model.id);
        kept_or(&self.pictures, PICTURES_KEPT, key, || match what {
            "clip" => preview_clip(env, model, width, way, design),
            _ => preview_png(env, model, width, way, design),
        })
    }

    /// cached_file: the boot logo's JPEG or the standby clip as the panel takes them (in full
    /// range with `full`; turned round for a panel mounted upside down, `turned`, as they are
    /// written); the clip needs ffmpeg.
    #[allow(clippy::too_many_arguments)]
    pub fn file(
        &self,
        env: &lp_theme::Env,
        model: &PanelModel,
        what: &str,
        way: &str,
        turned: bool,
        design: &str,
        full: bool,
    ) -> Result<Vec<u8>, String> {
        let key = format!("{}|{what}|{way}|{turned}|{design}|{full}", model.id);
        kept_or(&self.files, FILES_KEPT, key, || match what {
            "clip" => {
                let ffmpeg = lp_devices::h264::find_ffmpeg("")?;
                let frames =
                    boot::standby_frames(env, model, None, way, design, 1.0).map_err(|e| e.0)?;
                let frames: Vec<Image> = (frames.into_iter())
                    .map(|frame| as_mounted(frame, turned))
                    .collect();
                clip_h264(&ffmpeg, &frames, full, room(Some(model)).0)
            }
            _ => {
                let picture = boot::boot_logo(env, model, way, design, 1.0).map_err(|e| e.0)?;
                logo_jpeg(&as_mounted(picture, turned), room(Some(model)).1)
            }
        })
    }
}

/// _preview_scale: how much smaller the app's picture is drawn than the panel's (at most as
/// large).
fn preview_scale(model: &PanelModel, width: i64, way: &str) -> f64 {
    (width as f64 / model.size(way).0 as f64).min(1.0)
}

/// preview_png: the boot logo as a PNG for the app, drawn at that size.
fn preview_png(
    env: &lp_theme::Env,
    model: &PanelModel,
    width: i64,
    way: &str,
    design: &str,
) -> Result<Vec<u8>, String> {
    let scale = preview_scale(model, width, way);
    let picture = boot::boot_logo(env, model, way, design, scale).map_err(|e| e.0)?;
    lp_devices::png::encode_optimized(&picture)
}

/// preview_clip: the standby clip as an animated WebP for the app, at its rate, drawn at
/// that size (quality 90, method 4, looping).
fn preview_clip(
    env: &lp_theme::Env,
    model: &PanelModel,
    width: i64,
    way: &str,
    design: &str,
) -> Result<Vec<u8>, String> {
    let scale = preview_scale(model, width, way);
    let frames = boot::standby_frames(env, model, None, way, design, scale).map_err(|e| e.0)?;
    let duration = (1000.0 / f64::from(boot::CLIP_FPS)).round_ties_even() as i64;
    lp_decode::save_webp_animation(&frames, duration, 0, 90.0, 4).map_err(|e| e.to_string())
}

/// picture_type: the media type of a picture of [`Kept::preview`].
pub fn picture_type(data: &[u8]) -> &'static str {
    if data.starts_with(b"RIFF") && data.get(8..12) == Some(b"WEBP") {
        "image/webp"
    } else {
        "image/png"
    }
}

fn native_rgb(picture: &Image) -> Result<Image, String> {
    let native = lp_devices::turzx::to_native(picture);
    match native.mode() {
        Mode::RGB => Ok(native),
        _ => native.convert(Mode::RGB).map_err(|e| e.0),
    }
}

/// logo_jpeg: the boot logo as the JPEG the panel shows: turned into its framebuffer, with
/// every pixel's colour, at the finest quality of JPEG_STEPS that fits the logo's room
/// (`most` bytes). A picture that would have to be coarser than the last is not made.
fn logo_jpeg(picture: &Image, most: usize) -> Result<Vec<u8>, String> {
    let native = native_rgb(picture)?;
    for quality in JPEG_STEPS {
        let data = lp_decode::save_jpeg_options(&native, quality, true, true)
            .map_err(|e| e.to_string())?;
        if data.len() <= most {
            return Ok(data);
        }
    }
    Err(too_fine())
}

/// clip_command: ffmpeg for the standby clip as the panel's decoder plays it: one slice a
/// picture, a sequence every 11 pictures, baseline, no B pictures; in full range with `full`;
/// the dark parts as smooth as the light ones (aq-mode 3).
pub fn clip_command(
    ffmpeg: &std::path::Path,
    size: (usize, usize),
    crf: f64,
    full: bool,
) -> Vec<String> {
    use lp_devices::h264::{colour_tags, stamp, FULL_RANGE};
    let convert = if full {
        format!("{FULL_RANGE},format=yuv420p")
    } else {
        "format=yuv420p".into()
    };
    let mut command: Vec<String> = vec![ffmpeg.display().to_string()];
    command.extend(
        [
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
        ]
        .map(String::from),
    );
    command.push(format!("{}x{}", size.0, size.1));
    command.push("-framerate".into());
    command.push(boot::CLIP_FPS.to_string());
    command.extend(["-i", "-", "-an", "-vf"].map(String::from));
    command.push(convert);
    command.extend(
        [
            "-c:v",
            "libx264",
            "-profile:v",
            "baseline",
            "-preset",
            "slow",
            "-threads",
            "1",
            "-crf",
        ]
        .map(String::from),
    );
    command.push(crf.to_string()); // (as Python's "{:g}": 20, 24.25)
    command.extend(colour_tags(full));
    command.push("-x264-params".into());
    command.push(
        "sliced-threads=0:bframes=0:scenecut=0:keyint=11:min-keyint=11:repeat-headers=1\
         :aq-mode=3"
            .into(),
    );
    command.extend(stamp(full));
    command.extend(["-f", "h264", "-"].map(String::from));
    command
}

/// standby_clip: the standby clip of `model` (landscape, Libre Panel's own look, its range) as
/// the app writes it to the panel, made with `ffmpeg`, and how many pictures went into it:
/// `libre-panel tools clip`, the check of the ffmpeg a download carries.
pub fn standby_clip(
    env: &lp_theme::Env,
    model: &PanelModel,
    ffmpeg: &std::path::Path,
) -> Result<(Vec<u8>, usize), String> {
    let frames = boot::standby_frames(env, model, None, "landscape", "", 1.0).map_err(|e| e.0)?;
    let full = lp_devices::turzx::full_range("auto", Some(model));
    let clip = clip_h264(ffmpeg, &frames, full, room(Some(model)).0)?;
    Ok((clip, frames.len()))
}

/// clip_h264: the standby clip's pictures turned into the framebuffer and encoded as finely
/// as fits the clip's room (`most` bytes): at crf CLIP_CRF, coarser where that is too much,
/// up to CLIP_CRF_MAX, and then a quarter of a step at a time finer where that still fits
/// (CLIP_QUARTERS). A clip that would have to be coarser is not made.
fn clip_h264(
    ffmpeg: &std::path::Path,
    frames: &[Image],
    full: bool,
    most: usize,
) -> Result<Vec<u8>, String> {
    let native: Vec<Image> = frames.iter().map(native_rgb).collect::<Result<_, _>>()?;
    let Some(first) = native.first() else {
        return Err("the standby clip has no pictures".into());
    };
    let size = first.size();
    let raw: Vec<u8> = native
        .iter()
        .flat_map(|f| f.as_bytes().iter().copied())
        .collect();
    let raw = raw.as_slice();
    let encoded = |crf: f64| -> Result<Vec<u8>, String> {
        let command = clip_command(ffmpeg, size, crf, full);
        let failed = |why: String| format!("ffmpeg could not make the standby clip: {why}");
        let mut child = crate::capture::quiet(&mut std::process::Command::new(&command[0]))
            .args(&command[1..])
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .spawn()
            .map_err(|e| failed(e.to_string()))?;
        let mut stdin = child.stdin.take().expect("(piped)");
        // (the pictures are written while their clip is read: neither pipe fills up)
        let done = std::thread::scope(|scope| {
            scope.spawn(move || {
                use std::io::Write;
                let _ = stdin.write_all(raw);
            });
            child.wait_with_output()
        })
        .map_err(|e| failed(e.to_string()))?;
        if !done.status.success() || done.stdout.is_empty() {
            let tail = &done.stderr[done.stderr.len().saturating_sub(300)..];
            return Err(failed(bytes_repr(tail)));
        }
        Ok(done.stdout)
    };
    let mut crf = CLIP_CRF;
    let mut data = encoded(crf as f64)?;
    if data.len() > most {
        let mut guess = data.len();
        while guess > most && crf < CLIP_CRF_MAX {
            // (about 13 % less with each step)
            crf += 1;
            guess = guess * CLIP_STEP / 1000;
        }
        data = encoded(crf as f64)?;
        if data.len() > most {
            // the guess was too fine: coarser, a step at a time
            while data.len() > most && crf < CLIP_CRF_MAX {
                crf += 1;
                data = encoded(crf as f64)?;
            }
            if data.len() > most {
                return Err(too_fine());
            }
        } else {
            // or too coarse: the finest that fits
            while crf - 1 > CLIP_CRF {
                let finer = encoded((crf - 1) as f64)?;
                if finer.len() > most {
                    break;
                }
                crf -= 1;
                data = finer;
            }
        }
        // (the whole step finer is too large) the finest quarter between the two that fits
        let made = std::thread::scope(|scope| {
            let encodes: Vec<_> = (CLIP_QUARTERS.iter())
                .map(|quarter| scope.spawn(move || encoded(crf as f64 - quarter)))
                .collect();
            (encodes.into_iter())
                .map(|encode| encode.join().expect("(an encode does not panic)"))
                .collect::<Result<Vec<_>, String>>()
        })?;
        if let Some(finer) = made.into_iter().find(|finer| finer.len() <= most) {
            data = finer;
        }
    }
    if lp_devices::h264::slice_count(&data) != native.len() {
        return Err("the standby clip came out with more than one slice a picture".into());
    }
    Ok(data)
}

// -- on the panel -------------------------------------------------------------------------------

pub(crate) const FOLDER: &str = "usr/data";
const LIST_PIECES: usize = 20;
const PIECE: usize = 512;
const ACK: u8 = 0xC8;
const CMD_WRITE_FILE: u8 = 40;
const CMD_DELETE_FILE: u8 = 42;
const CMD_FILE_SIZE: u8 = 98;
const CMD_LIST: u8 = 99;
const CMD_PLAY_LOCAL: u8 = 110;
const CMD_SHOW_JPEG: u8 = 113;
const CMD_SAVE_SETTINGS: u8 = 125;
const CMD_FRAME_RATE: u8 = 15;
/// the settings' start mode: play the clip they name
const START_WITH_CLIP: u8 = 2;
/// restart, halt, and the other save of the settings: never sent
const NEVER: [u8; 3] = [11, 12, 13];

fn note(message: &str) {
    crate::logs::log(
        crate::logs::Level::Info,
        "libre_panel.devices.panel_files",
        message,
    );
}

fn warn(message: &str) {
    crate::logs::log(
        crate::logs::Level::Warning,
        "libre_panel.devices.panel_files",
        message,
    );
}

/// checked_name: a file name as the panel takes it: relative, below `usr/data/`, no `..`.
pub fn checked_name(name: &str) -> Result<Vec<u8>, String> {
    let shown = lp_py::repr(name);
    if !name.starts_with(&format!("{FOLDER}/")) || name.starts_with('/') {
        return Err(format!("{shown}: only files below {FOLDER}/ are written"));
    }
    let plain =
        name.split('/').all(|part| !matches!(part, "" | "." | "..")) && !name.contains('\\');
    let raw = name.as_bytes();
    if !plain || !name.is_ascii() || raw.len() > 200 || !raw.iter().all(|b| (33..127).contains(b)) {
        return Err(format!("{shown}: not a plain file name"));
    }
    Ok(raw.to_vec())
}

pub(crate) fn file_args(name: &[u8], size: usize) -> Vec<u8> {
    let mut args = (name.len() as u32).to_be_bytes().to_vec();
    args.extend_from_slice(&(size as u32).to_be_bytes());
    args.extend_from_slice(name);
    args
}

/// names_in: the file names in a folder's listing as the panel gives it: the plain names in
/// the text, in order, once each (re.findall of `[A-Za-z0-9_][A-Za-z0-9_.\-]*\.[A-Za-z0-9]+`).
pub fn names_in(listing: &[u8]) -> Vec<String> {
    let text: Vec<u8> = listing
        .iter()
        .map(|&b| if b == 0 { b'\n' } else { b })
        .collect();
    let first = |b: u8| b.is_ascii_alphanumeric() || b == b'_';
    let inner = |b: u8| first(b) || b == b'.' || b == b'-';
    let mut found: Vec<String> = Vec::new();
    let mut i = 0;
    while i < text.len() {
        if !first(text[i]) {
            i += 1;
            continue;
        }
        let run_end = (i + 1..text.len())
            .find(|&k| !inner(text[k]))
            .unwrap_or(text.len());
        // the last dot of the run followed by a letter or digit: the greedy match's end
        let matched = (i + 1..run_end)
            .rev()
            .find(|&j| text[j] == b'.' && text.get(j + 1).is_some_and(u8::is_ascii_alphanumeric))
            .map(|j| {
                (j + 1..text.len())
                    .find(|&k| !text[k].is_ascii_alphanumeric())
                    .unwrap_or(text.len())
            });
        match matched {
            Some(end) => {
                let token: String = text[i..end].iter().map(|&b| char::from(b)).collect();
                if !found.contains(&token) {
                    found.push(token);
                }
                i = end;
            }
            None => i += 1,
        }
    }
    found
}

/// PanelFiles: the file commands on an open panel (`model`: the panel's, which says how much
/// room its files get).
struct PanelFiles<'a> {
    link: &'a lp_devices::turzx::FileLink,
    model: Option<&'static PanelModel>,
    /// its owner agreed to try a model that has not been (writes_on)
    untested: bool,
}

impl PanelFiles<'_> {
    fn command(&self, cmd: u8, args: &[u8], payload: &[u8], timeout_ms: u32) -> Result<(), String> {
        if NEVER.contains(&cmd) {
            return Err(format!("command {cmd} is never sent"));
        }
        self.link
            .command(cmd, args, payload, timeout_ms)
            .map(drop)
            .map_err(|e| e.to_string())
    }

    /// listing: the names in usr/data, as the panel lists them (all 20 pieces are fetched).
    fn listing(&self) -> Result<Vec<String>, String> {
        let mut pieces = Vec::new();
        for n in 0..LIST_PIECES {
            let reply = self
                .link
                .ask(CMD_LIST, &file_args(FOLDER.as_bytes(), 0), false)
                .map_err(|e| e.to_string())?;
            if n == 0 {
                let head: Vec<String> = reply.iter().take(32).map(|b| format!("{b:02x}")).collect();
                crate::logs::log(
                    crate::logs::Level::Debug,
                    "libre_panel.devices.panel_files",
                    &format!("start screen: listing {FOLDER} begins {}", head.join(" ")),
                );
            }
            let header = reply.first() == Some(&CMD_LIST) && reply.get(1) == Some(&ACK);
            let piece = if header {
                reply.get(8..).unwrap_or_default()
            } else {
                &reply[..]
            };
            pieces.extend_from_slice(&piece[..piece.len().min(PIECE)]);
        }
        Ok(names_in(&pieces))
    }

    /// noted_listing: the listing before a file is written, for the log; one that the panel
    /// does not give does not stop the writing.
    fn noted_listing(&self) -> Option<Vec<String>> {
        match self.listing() {
            Ok(names) => {
                let said = if names.is_empty() {
                    "nothing listed".to_string()
                } else {
                    names.join(", ")
                };
                note(&format!("start screen: {FOLDER} holds {said}"));
                Some(names)
            }
            Err(why) => {
                warn(&format!(
                    "start screen: the panel's files could not be listed: {why}"
                ));
                self.link.drain();
                None
            }
        }
    }

    /// size: the size of a file of the panel's (0: none).
    fn size(&self, name: &str) -> Result<i64, String> {
        let reply = self
            .link
            .ask(CMD_FILE_SIZE, &file_args(&checked_name(name)?, 0), true)
            .map_err(|e| e.to_string())?;
        let Some(bytes) = reply.get(8..12) else {
            return Err("unpack requires a buffer of 4 bytes".into());
        };
        let size = u32::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]);
        Ok(if size == u32::MAX { 0 } else { i64::from(size) })
    }

    /// write: the boot logo or the standby clip written, checked before and after (issue
    /// 52: a 9.2" had no room left for one, and 0.9.1 forced it in).
    ///
    /// Before: a file larger than its room on the model is not sent at all; then what the
    /// panel has of this file and of the other one. A file larger than the one it replaces
    /// is not sent where both together would be more than the room Libre Panel uses on the
    /// model. After: the size again. A file the panel did not take (it has
    /// what it had: a panel without room answers at once and changes nothing) is said so. A
    /// file that ended early is taken off again, so that the panel is not left full, and
    /// said so. Nothing is ever taken away in order to write it a second time.
    fn write(&self, name: &str, data: &[u8]) -> Result<(), String> {
        if !writes_on(self.model, self.untested) {
            return Err(writes_off());
        }
        if !tried(self.model) {
            crate::logs::log(
                crate::logs::Level::Warning,
                "libre_panel.devices.panel_files",
                &format!(
                    "start screen: writing {name} to a {}, which has not been tried, as its \
                     owner chose",
                    self.model.map_or("panel", |model| model.id)
                ),
            );
        }
        let raw = checked_name(name)?;
        if !OURS.contains(&name) {
            return Err(format!(
                "{name}: only the boot logo and the standby clip are written"
            ));
        }
        if data.is_empty() || data.len() > MAX_FILE {
            return Err(format!(
                "{name}: a file is 1 byte to 1 MiB, not {}",
                data.len()
            ));
        }
        its_room(name, data.len(), self.model)?;
        let beside = if name == BOOT_LOGO {
            STANDBY_CLIP
        } else {
            BOOT_LOGO
        };
        let (before, other) = (self.size(name)?, self.size(beside)?);
        let size = data.len() as i64;
        let (clip, logo) = room(self.model);
        if size > before && size + other > (clip + logo) as i64 {
            warn(&format!(
                "start screen: {name} ({size} bytes) not sent: {beside} on the panel has {other}"
            ));
            return Err(no_room(name, other));
        }
        let began = std::time::Instant::now();
        self.command(CMD_WRITE_FILE, &file_args(&raw, data.len()), data, 20000)?;
        let took = began.elapsed();
        let after = self.size(name)?;
        note(&format!(
            "start screen: {name}: {size} bytes sent, answered after {} ms; the panel had \
             {before} bytes of it and has {after}",
            took.as_millis()
        ));
        if after == size && !(before == after && unwritten(data.len(), took)) {
            return Ok(());
        }
        if after == before {
            return Err(not_taken());
        }
        // it ended early: off again, so that the panel is not left full
        match self.delete(name).and_then(|()| self.size(name)) {
            Err(why) => Err(still_there(name, after, size, &why)),
            Ok(left) if left != 0 => Err(still_there(
                name,
                after,
                size,
                &format!("{left} bytes of it stay"),
            )),
            Ok(_) => {
                warn(&format!(
                    "start screen: {name} ended at {after} of {size} bytes: taken off again"
                ));
                Err(cut_off(name, after, size))
            }
        }
    }

    fn delete(&self, name: &str) -> Result<(), String> {
        let raw = checked_name(name)?;
        if !OURS.contains(&name) {
            return Err(format!(
                "{name}: only the boot logo and the standby clip are taken off"
            ));
        }
        self.command(CMD_DELETE_FILE, &file_args(&raw, 0), &[], 4000)
    }

    fn show_jpeg(&self, name: &str) -> Result<(), String> {
        self.command(
            CMD_SHOW_JPEG,
            &file_args(&checked_name(name)?, 0),
            &[],
            4000,
        )
    }

    /// play: a clip of the panel's played at the rate of a cold start; its name goes into the
    /// panel's settings (in memory, until save_start).
    fn play(&self, name: &str) -> Result<(), String> {
        let raw = checked_name(name)?;
        self.command(CMD_FRAME_RATE, &[boot::CLIP_FPS as u8], &[], 4000)?;
        self.command(CMD_PLAY_LOCAL, &file_args(&raw, 0), &[], 4000)
    }

    /// save_start: the panel's settings saved: its brightness, to start with the clip `play`
    /// named, unturned, and to dim when the PC has been gone for 10 s.
    fn save_start(&self, brightness: i64) -> Result<(), String> {
        let arg = lp_devices::turzx::brightness_arg(brightness);
        self.command(
            CMD_SAVE_SETTINGS,
            &[arg, START_WITH_CLIP, 0, 0, 0, 0],
            &[],
            4000,
        )
    }
}

/// Which panel: the models the other panels name, and how many alike to pass over.
pub struct Where<'a> {
    pub beside: &'a [String],
    pub skip: usize,
}

/// _with_panel: `job` on the panel opened (closed again afterwards).
fn with_panel(
    model: Option<&'static PanelModel>,
    place: &Where,
    untested: bool,
    job: impl FnOnce(&PanelFiles, &'static PanelModel) -> Result<Dict, String>,
) -> Result<Dict, String> {
    let setup = crate::display::files_setup(model, place.beside)?;
    let link = lp_devices::turzx::FileLink::open(&setup, place.skip).map_err(|e| e.to_string())?;
    let Some(found) = link.model() else {
        return Err(lp_i18n::t("No TURZX USB panel found"));
    };
    let files = PanelFiles {
        link: &link,
        model: Some(found),
        untested,
    };
    job(&files, found)
}

fn told(pairs: Vec<(&str, Value)>) -> Dict {
    pairs.into_iter().map(|(k, v)| (k.to_string(), v)).collect()
}

fn whole(n: i64) -> Value {
    Value::Int(n.into())
}

/// The room a model's files get, as the app is told it: {"clip": bytes, "logo": bytes}.
fn room_told(model: &PanelModel) -> Value {
    let (clip, logo) = room(Some(model));
    Value::Dict(told(vec![
        ("clip", whole(clip as i64)),
        ("logo", whole(logo as i64)),
    ]))
}

/// StartScreen.state: what is on the panel.
pub fn panel_state(model: Option<&'static PanelModel>, place: &Where) -> Result<Dict, String> {
    with_panel(model, place, false, |files, found| {
        let names = files.noted_listing().map_or(Value::None, |names| {
            Value::List(names.into_iter().map(Value::Str).collect())
        });
        Ok(told(vec![
            ("model", Value::Str(found.id.into())),
            ("files", names),
            ("boot_logo", whole(files.size(BOOT_LOGO)?)),
            ("standby_clip", whole(files.size(STANDBY_CLIP)?)),
            // (what Libre Panel writes at most: files of before 0.9.4 may be larger)
            ("room", room_told(found)),
        ]))
    })
}

/// The picture as a panel mounted upside down (`turned`: rotate = 180) shows it upright: turned
/// round, as its frames go to it.
fn as_mounted(picture: Image, turned: bool) -> Image {
    match turned {
        true => picture.transpose(lp_pixels::Transpose::Rotate180),
        false => picture,
    }
}

/// StartScreen.write_logo: the boot logo written and shown (it shows at the next cold start);
/// turned round for a panel mounted upside down (`turned`), as its frames are; to a model not
/// tried only where its owner agreed (`untested`).
pub fn write_logo(
    env: &lp_theme::Env,
    model: Option<&'static PanelModel>,
    way: &str,
    turned: bool,
    place: &Where,
    design: &str,
    untested: bool,
) -> Result<Dict, String> {
    if model.is_some() && !writes_on(model, untested) {
        return Err(writes_off()); // (said before the panel is opened)
    }
    with_panel(model, place, untested, |files, found| {
        if !writes_on(Some(found), untested) {
            return Err(writes_off());
        }
        let picture = boot::boot_logo(env, found, way, design, 1.0).map_err(|e| e.0)?;
        let data = logo_jpeg(&as_mounted(picture, turned), room(Some(found)).1)?;
        files.noted_listing();
        files.write(BOOT_LOGO, &data)?;
        files.show_jpeg(BOOT_LOGO)?;
        Ok(told(vec![
            ("model", Value::Str(found.id.into())),
            ("boot_logo", whole(data.len() as i64)),
            ("design", Value::Str(design.into())),
        ]))
    })
}

/// StartScreen.remove_logo: the boot logo taken away (the factory logo shows again).
pub fn remove_logo(model: Option<&'static PanelModel>, place: &Where) -> Result<Dict, String> {
    with_panel(model, place, false, |files, found| {
        files.delete(BOOT_LOGO)?;
        Ok(told(vec![
            ("model", Value::Str(found.id.into())),
            ("boot_logo", whole(files.size(BOOT_LOGO)?)),
        ]))
    })
}

/// StartScreen.write_clip: the standby clip written, played and saved as what the panel
/// starts with; in the range of values its decoder shows; turned round for a panel mounted
/// upside down (`turned`), as its frames are; to a model not tried only where its owner
/// agreed (`untested`).
#[allow(clippy::too_many_arguments)]
pub fn write_clip(
    env: &lp_theme::Env,
    brightness: i64,
    model: Option<&'static PanelModel>,
    way: &str,
    turned: bool,
    place: &Where,
    design: &str,
    video_range: &str,
    untested: bool,
) -> Result<Dict, String> {
    if model.is_some() && !writes_on(model, untested) {
        return Err(writes_off()); // (said before the panel is opened)
    }
    let ffmpeg = lp_devices::h264::find_ffmpeg("")?;
    with_panel(model, place, untested, |files, found| {
        if !writes_on(Some(found), untested) {
            return Err(writes_off());
        }
        let frames = boot::standby_frames(env, found, None, way, design, 1.0).map_err(|e| e.0)?;
        let frames: Vec<Image> = (frames.into_iter())
            .map(|frame| as_mounted(frame, turned))
            .collect();
        let full = lp_devices::turzx::full_range(video_range, Some(found));
        let data = clip_h264(&ffmpeg, &frames, full, room(Some(found)).0)?;
        files.noted_listing();
        files.write(STANDBY_CLIP, &data)?;
        files.play(STANDBY_CLIP)?;
        files.save_start(brightness)?;
        Ok(told(vec![
            ("model", Value::Str(found.id.into())),
            ("standby_clip", whole(data.len() as i64)),
            ("name", Value::Str(STANDBY_CLIP.into())),
            ("design", Value::Str(design.into())),
        ]))
    })
}

/// repr() of Python bytes.
fn bytes_repr(data: &[u8]) -> String {
    let quote = if data.contains(&b'\'') && !data.contains(&b'"') {
        '"'
    } else {
        '\''
    };
    let mut out = format!("b{quote}");
    for &b in data {
        match b {
            b'\\' => out.push_str("\\\\"),
            b'\t' => out.push_str("\\t"),
            b'\n' => out.push_str("\\n"),
            b'\r' => out.push_str("\\r"),
            _ if char::from(b) == quote => {
                out.push('\\');
                out.push(quote);
            }
            32..=126 => out.push(char::from(b)),
            _ => out.push_str(&format!("\\x{b:02x}")),
        }
    }
    out.push(quote);
    out
}

/// What `GET /api/start-screen` answers (`_start_screen`).
pub struct Told<'a> {
    pub panel: i64,
    pub orientation: &'a str,
    pub model: Option<&'static str>,
    pub runs_panel: bool,
}

/// _start_screen's answer: the models, the one at hand, the panel, how it is mounted, whether
/// this app runs the panel, the files' names and the looks (Libre Panel's own first).
pub fn state(told: &Told) -> Value {
    let text = |t: &str| Value::Str(t.to_string());
    let listed: Vec<Value> = models()
        .map(|m| {
            let (w, h) = m.size(told.orientation);
            let mut one = Dict::default();
            one.insert("id".into(), text(m.id));
            one.insert("label".into(), Value::Str(m.label()));
            one.insert(
                "size".into(),
                Value::List(vec![Value::Int(w.into()), Value::Int(h.into())]),
            );
            one.insert("measured".into(), Value::Bool(MEASURED.contains(&m.id)));
            // whether its files are written (the models it has been done on), and how large
            // they are at most there
            one.insert("writes".into(), Value::Bool(writes_on(Some(m), false)));
            // (another is written to where its owner agrees to try it; one that has been seen
            // to start with them is confirmed)
            one.insert("tried".into(), Value::Bool(tried(Some(m))));
            one.insert("confirmed".into(), Value::Bool(CONFIRMED.contains(&m.id)));
            one.insert("room".into(), room_told(m));
            // (the looks known not to fit there finely: not offered for writing)
            let unfit = UNFIT.iter().find(|(id, _)| *id == m.id);
            let unfit = unfit.map_or(&[][..], |(_, looks)| *looks);
            one.insert(
                "unfit".into(),
                Value::List(unfit.iter().map(|look| text(look)).collect()),
            );
            Value::Dict(one)
        })
        .collect();
    let mut names = Dict::default();
    names.insert("logo".into(), text(BOOT_LOGO));
    names.insert("clip".into(), text(STANDBY_CLIP));
    let mut answer = Dict::default();
    answer.insert("models".into(), Value::List(listed));
    answer.insert("model".into(), told.model.map_or(Value::None, text));
    answer.insert("panel".into(), Value::Int(told.panel.into()));
    answer.insert("orientation".into(), text(told.orientation));
    answer.insert("runs_panel".into(), Value::Bool(told.runs_panel));
    answer.insert("names".into(), Value::Dict(names));
    answer.insert(
        "designs".into(),
        Value::List(boot::DESIGNS.iter().map(|d| text(d)).collect()),
    );
    // (where an owner who tried a model says how it went, and this version)
    answer.insert("report".into(), text(REPORT_FORM));
    answer.insert("version".into(), text(lp_py::VERSION));
    Value::Dict(answer)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bytes_are_said_as_python_says_them() {
        assert_eq!(bytes_repr(b"a'b\n\x01\\"), "b\"a'b\\n\\x01\\\\\"");
        assert_eq!(bytes_repr(b"x"), "b'x'");
    }

    #[test]
    fn the_clips_command_is_panel_files_pys() {
        let command = clip_command(std::path::Path::new("ffmpeg"), (480, 1920), 20.0, true);
        let joined = command.join(" ");
        assert!(joined.starts_with("ffmpeg -hide_banner -loglevel error -f rawvideo"));
        assert!(joined.contains("-s 480x1920 -framerate 30 -i - -an -vf scale=out_range=full"));
        assert!(joined.contains("-crf 20 -colorspace smpte170m"));
        assert!(joined.ends_with("matrix_coefficients=6 -f h264 -"));
    }

    #[test]
    fn the_room_is_panel_files_pys() {
        let nine = model_of("turing-9.2-usb").expect("the 9.2\"");
        let twelve = model_of("turing-12.3-usb").expect("the 12.3\"");
        let eight = model_of("turing-8.8-usb").expect("the 8.8\"");
        assert_eq!(room(Some(nine)), (360_000, 120_000));
        assert_eq!(room(Some(twelve)), (500_000, 150_000));
        assert_eq!(room(Some(eight)), room(Some(nine))); // (the 9.2"'s sibling)
        assert_eq!(room(None), (360_000, 120_000));
        assert!(tried(Some(nine)) && tried(Some(twelve)) && tried(Some(eight)) && !tried(None));
        let written: Vec<&str> = models().filter(|m| tried(Some(m))).map(|m| m.id).collect();
        assert_eq!(
            written,
            ["turing-8.8-usb", "turing-9.2-usb", "turing-12.3-usb"]
        );
        for (id, _) in ROOM {
            assert!(UNFIT.iter().any(|(unfit, _)| *unfit == id), "{id}");
        }
        for model in models() {
            let (clip, logo) = room(Some(model));
            // no panel gets more than the 12.3", and none that was not tried more than the 9.2"
            assert!(clip <= 500_000 && logo <= 150_000, "{}", model.id);
            assert!(
                tried(Some(model)) || (clip <= 360_000 && logo <= 120_000),
                "{}",
                model.id
            );
            assert!(clip > 0 && logo > 0, "{}", model.id);
        }
        let small = model_of("turing-2.8-usb").expect("the 2.8\"");
        assert_eq!(room(Some(small)), (90_000, 30_000)); // (480 x 480: a quarter of the 9.2")
        assert!(!tried(Some(small)));
    }

    #[test]
    fn a_file_larger_than_its_room_is_not_sent() {
        let nine = model_of("turing-9.2-usb").expect("the 9.2\"");
        let twelve = model_of("turing-12.3-usb").expect("the 12.3\"");
        assert_eq!(its_room(STANDBY_CLIP, 360_000, Some(nine)), Ok(()));
        assert_eq!(its_room(BOOT_LOGO, 120_000, Some(nine)), Ok(()));
        assert_eq!(
            its_room(STANDBY_CLIP, 360_001, Some(nine)),
            Err("usr/data/standby.h264: 360001 bytes are more than the 360000 it gets".into())
        );
        assert_eq!(
            its_room(BOOT_LOGO, 120_001, Some(nine)),
            Err("usr/data/boot.jpg: 120001 bytes are more than the 120000 it gets".into())
        );
        // (a clip that fits the 12.3" is too much for the 9.2", and for a panel not named)
        assert_eq!(its_room(STANDBY_CLIP, 492_450, Some(twelve)), Ok(()));
        assert!(its_room(STANDBY_CLIP, 492_450, Some(nine)).is_err());
        assert!(its_room(STANDBY_CLIP, 492_450, None).is_err());
        assert!(its_room(BOOT_LOGO, 150_001, Some(twelve)).is_err());
    }

    #[test]
    fn a_write_that_was_answered_too_fast_did_not_happen() {
        use std::time::Duration;
        // (a 9.2" answered 30 to 50 ms after 700 KB it did not take, and after 1.2 s or more
        // when it wrote them)
        assert!(unwritten(706_912, Duration::from_millis(50)));
        assert!(!unwritten(706_912, Duration::from_millis(1250)));
        assert!(unwritten(360_000, Duration::from_millis(40)));
        assert!(!unwritten(360_000, Duration::from_millis(600)));
        // (the smallest clip, Dark's on a 9.2" on end, turned round: told too)
        assert!(unwritten(44_206, Duration::from_millis(3)));
        assert!(!unwritten(44_206, Duration::from_millis(48)));
        // (each write of 0.9.4rc1 on a sound 9.2" and 12.3", and how long the panel took: all
        // written, the quickest, 44,931 bytes in 48 ms, at under a quarter of the rate)
        let measured = [
            (319_122, 568),
            (113_393, 209),
            (44_931, 1764),
            (44_931, 48),
            (113_393, 195),
            (319_122, 564),
            (485_481, 1306),
            (485_481, 1115),
            (141_864, 321),
            (141_864, 157),
        ];
        for (size, ms) in measured {
            assert!(
                !unwritten(size, Duration::from_millis(ms)),
                "{size} in {ms} ms"
            );
        }
        assert!(unwritten(44_931, Duration::from_millis(11)));
        assert!(unwritten(141_864, Duration::from_millis(35)));
        // (the fastest write in that panel's log: 149 KB in under 220 ms)
        assert!(!unwritten(149_040, Duration::from_millis(220)));
        // (a small file's transfer says nothing: Dark's boot logo)
        assert!(!unwritten(14_289, Duration::from_millis(1)));
        assert!(!unwritten(21_952, Duration::from_millis(1)));
        assert_eq!(kilobytes(593_754), "580 KB");
        assert_eq!(kilobytes(100), "1 KB");
    }
}

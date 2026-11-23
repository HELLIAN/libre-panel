//! command: `libre-panel clip [--panels]`: the panels' loops run frame by frame on a clock of
//! their own, for the clips of the README (tools/media_panel.py): the same clip on every run,
//! whatever the machine (lp_render::clock).
//!
//! The settings folder (LIBRE_PANEL_HOME) holds the clip's config.toml and themes. Each line on
//! the standard input is a frame, as JSON:
//!
//! - `t`: seconds on the loops' clock; `wall`: the time of day (seconds since 1970);
//! - `readings`: every reading the sources give then, key to `[value, unit, label]`, each value
//!   typed as lp-check takes them (`{"f": bits}`, `{"i": "12"}`, `{"s": ...}`, ...); those of
//!   the volume (`audio.volume`, `audio.muted`) follow on every frame, as the audio source's do;
//! - `do`: what happens just before the frame: `["theme", name|null, by, transition|null,
//!   priority]` (a service asks for a theme), `["mode", name|null, by, transition|null,
//!   params|null]`, `["publish", key, value, unit, label, owner]`, `["slot", seconds|null,
//!   way|null]` (the cards of the first card slot take turns of so many seconds from now on,
//!   `way` their transition; null: as it is), `["leave", seconds]` (music's page stays so long
//!   after a track stops), `["config"]` (config.toml, which the clip changed, is read again
//!   for the frame), `["pace", unit]` (the seconds of config.toml's screens in turn are `unit`
//!   seconds each: 0.1 for steps of tenths);
//! - `prefill`: the readings are only taken, into the graphs' history (before the clip begins);
//!   else the loops draw the frame, and with `show` its picture goes to the standard output: a
//!   line `frame W H` and W x H x 3 bytes of RGB. That is the first panel's frame, or with
//!   `--panels MODEL` the picture of all the panels (`[[panels]]` beside the first) where they
//!   stand (*Picture of all panels*), each of them a panel of that model there.
//!
//! The loops show each theme as it is made, at its own size (the clip's themes are made for
//! it): config.toml names no model (`auto`) and the clip's panels say none.
//!
//! The panels are the clip's own (a recorder of the frames each loop shows, as a panel that
//! takes video); no panel of this computer is looked for, no source of it read.
//!
//! `libre-panel clip --start MODEL SCALE LOOK...`: the panel's own screens instead
//! (lp_render::boot), as the panel of the model shows them (landscape, at the scale): a line
//! `rate N` (the standby clip's pictures a second), a line `looks NAME...` (every look there is,
//! the panel's own first), the boot logo as a frame, then for each look asked for a line
//! `look NAME COUNT` and the pictures of its standby clip, one loop of them.

use std::io::{BufRead, Write};
use std::sync::{Arc, Mutex};

use indexmap::IndexMap;
use lp_devices::base::{Counters, Result as DeviceResult};
use lp_devices::driver::{Driver, Region};
use lp_devices::models::PanelModel;
use lp_pixels::{Image, Mode};
use lp_py::{Dict, Value};
use lp_render::snapshot::Reading;
use lp_services::host::{Scope, Transition};

use crate::home::Home;
use crate::mainloop::{Asked, Beside, MainLoop};
use crate::sensors::{Given, Readings};

/// What a panel of the clip shows: each frame the loop shows, kept for the clip.
struct Recorder {
    shown: Arc<Mutex<Option<Image>>>,
    model: Option<&'static PanelModel>,
    fps: i64,
}

impl Driver for Recorder {
    fn model(&self) -> Option<&'static PanelModel> {
        self.model
    }
    fn describe(&self) -> String {
        "clip recorder".into()
    }
    fn show(&mut self, frame: &Image, _region: Option<Region>) -> DeviceResult<()> {
        if let Ok(mut shown) = self.shown.lock() {
            *shown = Some(frame.clone());
        }
        Ok(())
    }
    fn set_brightness(&mut self, _percent: i64) -> DeviceResult<()> {
        Ok(())
    }
    fn counters(&self) -> Counters {
        Counters::default()
    }
    fn streaming(&self) -> bool {
        true
    }
    fn stream_fps(&self) -> i64 {
        self.fps
    }
    fn close(&mut self) {}
}

/// A value as lp-check takes them (`{"f": bits}`, `{"i": digits}`, ...): Python's types kept.
fn typed(v: &Value) -> Value {
    let Value::Dict(told) = v else {
        return Value::None;
    };
    let (Some((kind, inner)), 1) = (told.iter().next(), told.len()) else {
        return Value::None;
    };
    match (kind.as_str(), inner) {
        ("f", Value::Str(bits)) => {
            Value::Float(f64::from_bits(u64::from_str_radix(bits, 16).unwrap_or(0)))
        }
        ("i", Value::Str(digits)) => Value::Int(digits.parse().unwrap_or_default()),
        ("s", Value::Str(text)) => Value::Str(text.clone()),
        ("b", Value::Bool(b)) => Value::Bool(*b),
        ("l", Value::List(items)) => Value::List(items.iter().map(typed).collect()),
        ("d", Value::Dict(items)) => {
            Value::Dict(items.iter().map(|(k, v)| (k.clone(), typed(v))).collect())
        }
        _ => Value::None,
    }
}

/// The `n`th of a list (None past its end, or of what is no list).
fn nth(v: &Value, n: usize) -> &Value {
    static NONE: Value = Value::None;
    match v {
        Value::List(items) => items.get(n).unwrap_or(&NONE),
        _ => &NONE,
    }
}

/// `key` of a dict (None where there is none).
fn get<'a>(v: &'a Value, key: &str) -> &'a Value {
    static NONE: Value = Value::None;
    match v {
        Value::Dict(items) => items.get(key).unwrap_or(&NONE),
        _ => &NONE,
    }
}

fn text(v: &Value) -> Option<&str> {
    match v {
        Value::Str(text) => Some(text),
        _ => None,
    }
}

fn number(v: &Value) -> Option<f64> {
    match v {
        Value::Float(f) => Some(*f),
        Value::Int(n) => lp_py::num::int_to_float(n).ok(),
        _ => None,
    }
}

fn reading(told: &Value) -> Reading {
    let said = |n: usize| text(nth(told, n)).unwrap_or("").to_string();
    Reading {
        unit: said(1),
        label: said(2),
        ..Reading::of(typed(nth(told, 0)))
    }
}

fn transition(name: &Value, params: &Value) -> Option<Transition> {
    let name = text(name)?;
    let params = match typed(params) {
        Value::Dict(params) => Some(params),
        _ => None::<Dict>,
    };
    Some(Transition {
        name: name.to_string(),
        params,
    })
}

/// One of the frame's `do`.
fn act(first: &mut MainLoop, action: &Value) -> Result<(), String> {
    let part = |n: usize| nth(action, n);
    let host = first.host();
    match text(part(0)) {
        Some("theme") => host.request_theme(
            text(part(1)),
            text(part(2)).unwrap_or("clip"),
            transition(part(3), &Value::None),
            number(part(4)).map_or(0, |n| n as i64),
            Scope::Any,
        ),
        Some("mode") => host.set_mode(text(part(1)), text(part(2)), transition(part(3), part(4))),
        Some("publish") => {
            let told = Value::List(vec![part(2).clone(), part(3).clone(), part(4).clone()]);
            host.publish(text(part(1)).unwrap_or(""), reading(&told), text(part(5)));
        }
        Some("slot") => first.clip_slot(number(part(1)), text(part(2))),
        Some("leave") => first.clip_pages(number(part(1))),
        Some("config") => first.clip_config(),
        Some("pace") => first.clip_pace(number(part(1)).unwrap_or(1.0)),
        _ => return Err(format!("clip: what is {}?", action.repr())),
    }
    Ok(())
}

/// A picture on the standard output: a line `frame W H`, then its RGB.
fn put(out: &mut impl Write, picture: &Image) -> Result<(), String> {
    let picture = picture.convert(Mode::RGB).map_err(|e| e.0)?;
    let header = format!("frame {} {}\n", picture.width(), picture.height());
    out.write_all(header.as_bytes())
        .and_then(|()| out.write_all(picture.as_bytes()))
        .and_then(|()| out.flush())
        .map_err(|e| e.to_string())
}

/// `clip --start MODEL SCALE LOOK...`: the boot logo, and each look's standby clip.
fn start_screens(args: &[String]) -> Result<i32, String> {
    use lp_render::boot;
    let [model, scale, looks @ ..] = args else {
        return Err("clip: usage: clip --start MODEL SCALE LOOK...".into());
    };
    let model = lp_devices::models::find_model(model)
        .ok_or_else(|| format!("clip: no panel model {}", lp_py::repr(model)))?;
    let scale: f64 = (scale.parse().ok())
        .filter(|s: &f64| *s > 0.0)
        .ok_or_else(|| format!("clip: {scale} is no scale"))?;
    let home = Home::find()?;
    let env = lp_theme::Env {
        fonts_dir: home.fonts(),
    };
    let mut out = std::io::stdout().lock();
    let said = |out: &mut std::io::StdoutLock, line: String| {
        out.write_all(line.as_bytes()).map_err(|e| e.to_string())
    };
    said(&mut out, format!("rate {}\n", boot::CLIP_FPS))?;
    let mut every = vec![boot::DEFAULT];
    every.extend(boot::DESIGNS.iter().filter(|look| **look != boot::DEFAULT));
    said(&mut out, format!("looks {}\n", every.join(" ")))?;
    let logo = boot::boot_logo(&env, model, "landscape", boot::DEFAULT, scale);
    put(&mut out, &logo.map_err(|e| e.0)?)?;
    for look in looks {
        let frames = boot::standby_frames(&env, model, None, "landscape", look, scale);
        let frames = frames.map_err(|e| e.0)?;
        said(&mut out, format!("look {look} {}\n", frames.len()))?;
        for picture in &frames {
            put(&mut out, picture)?;
        }
    }
    Ok(0)
}

pub fn command(args: &[String]) -> Result<i32, String> {
    let all = match args {
        [] => None,
        [first, model] if first == "--panels" => Some(
            lp_devices::models::find_model(model)
                .ok_or_else(|| format!("clip: no panel model {}", lp_py::repr(model)))?,
        ),
        [first, rest @ ..] if first == "--start" => return start_screens(rest),
        _ => {
            return Err(
                "clip: usage: clip [--panels MODEL] (frames as JSON lines on stdin), \
                 or clip --start MODEL SCALE LOOK..."
                    .into(),
            )
        }
    };
    let stdin = std::io::stdin();
    let mut lines = stdin.lock().lines();
    let mut next = || -> Result<Option<Value>, String> {
        let Some(line) = lines.next() else {
            return Ok(None);
        };
        let line = line.map_err(|e| e.to_string())?;
        lp_py::json::loads(&line)
            .map(Some)
            .map_err(|e| format!("clip: {e}: {line}"))
    };
    // (the clock is the clip's before anything is made: the loops count from its moment)
    let Some(mut frame) = next()? else {
        return Ok(0);
    };
    let at = |frame: &Value| {
        (
            number(get(frame, "t")).unwrap_or(0.0),
            number(get(frame, "wall")).unwrap_or(0.0),
        )
    };
    let (t, wall) = at(&frame);
    lp_render::clock::set_clip(t, wall);
    let home = Home::find()?;
    let config =
        lp_config::load_config(&home.config_file(), lp_config::platform()).map_err(|e| e.msg)?;
    let fps = config.fps.max(1);
    let given: Arc<Mutex<Readings>> = Arc::default();
    let env = lp_theme::Env {
        fonts_dir: home.fonts(),
    };
    let pictures = lp_services::demo::pictures(&env.fonts_dir).unwrap_or_default();
    // (no panel of this computer: the loop's own count of frames keeps it off the bus, and
    // the panels beside are this command's, not threads of their own)
    let asked = Asked {
        frames: Some(u64::MAX),
        clip: true,
        ..Asked::default()
    };
    let mut first = MainLoop::new(home.clone(), asked.clone())?;
    first.clip_sources(vec![
        Box::new(Given::new(Arc::clone(&given), true, IndexMap::new())),
        Box::new(Given::new(Arc::clone(&given), false, pictures)),
    ]);
    let recorder = |model: &str| {
        let shown: Arc<Mutex<Option<Image>>> = Arc::default();
        let driver = Recorder {
            shown: Arc::clone(&shown),
            model: lp_devices::models::find_model(model),
            fps,
        };
        (shown, crate::display::Display::of_driver(Box::new(driver)))
    };
    let (shown, display) = recorder(&config.device.model);
    first.set_display(display);
    first.clip_pages(None);
    let mut panels = vec![(shown, config.device.clone())];
    let mut beside = Vec::new();
    if all.is_some() {
        let mut statuses = Vec::new();
        for (n, panel) in config.panels.iter().enumerate().filter(|(_, p)| p.on) {
            let status: Arc<Mutex<crate::mainloop::RunStatus>> = Arc::default();
            statuses.push(Arc::clone(&status));
            let mut made = MainLoop::beside(
                home.clone(),
                asked.clone(),
                first.company(),
                first.shared_hub(),
                Beside {
                    n,
                    first: first.status(),
                },
                status,
            )?;
            let (shown, display) = recorder(&panel.device.model);
            made.set_display(display);
            made.clip_pages(None);
            beside.push(made);
            panels.push((shown, panel.device.clone()));
        }
        // (the first panel's pair version while a companion is beside it: it sees them)
        first.clip_beside(statuses);
    }
    let mut out = std::io::stdout().lock();
    loop {
        let (t, wall) = at(&frame);
        lp_render::clock::set_clip(t, wall);
        if let (Value::Dict(told), Ok(mut readings)) = (get(&frame, "readings"), given.lock()) {
            *readings = told.iter().map(|(k, v)| (k.clone(), reading(v))).collect();
        }
        if matches!(get(&frame, "prefill"), Value::Bool(true)) {
            first.prefill();
        } else {
            if let Value::List(actions) = get(&frame, "do") {
                for action in actions {
                    act(&mut first, action)?;
                }
            }
            first.step();
            for made in &mut beside {
                made.step();
            }
            if matches!(get(&frame, "show"), Value::Bool(true)) {
                put(&mut out, &picture(&panels, &config, &env, all)?)?;
            }
        }
        frame = match next()? {
            Some(frame) => frame,
            None => break,
        };
    }
    for made in &mut beside {
        made.close(crate::mainloop::Ending::No);
    }
    first.close(crate::mainloop::Ending::No);
    Ok(0)
}

/// The frame of the first panel, or the picture of all the panels where they stand, each a
/// panel of the model `all` names.
fn picture(
    panels: &[(Arc<Mutex<Option<Image>>>, lp_config::DeviceConfig)],
    config: &lp_config::Config,
    env: &lp_theme::Env,
    all: Option<&PanelModel>,
) -> Result<Image, String> {
    let shown = |n: usize| panels[n].0.lock().ok().and_then(|shown| shown.clone());
    let Some(model) = all else {
        let frame = shown(0).ok_or("clip: the panel showed no frame")?;
        return frame.convert(Mode::RGB).map_err(|e| e.0);
    };
    let told = crate::arrangement::Told {
        model: model.id.to_string(),
        size: None,
    };
    let rects = crate::arrangement::laid_out(config, &vec![Some(told); panels.len()]);
    let placed: Vec<lp_render::photo::Placed> = (rects.into_iter().enumerate())
        .filter_map(|(n, rect)| {
            Some(lp_render::photo::Placed {
                rect: rect?,
                frame: shown(n),
                round: model.shape == "round",
                number: n + 1,
            })
        })
        .collect();
    let picture = lp_render::photo::photo(env, &placed).map_err(|e| e.0)?;
    let picture = picture.ok_or("clip: the panels showed no frame")?;
    picture.convert(Mode::RGB).map_err(|e| e.0)
}

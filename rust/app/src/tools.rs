//! command: `libre-panel tools`, the programs Libre Panel uses beside itself and where it
//! finds them, looked for as it looks for them when it runs: ffmpeg (videos, the screen, the
//! standby clip), its engine (a TURZX panel's video), PresentMon (a game's frames) and
//! PawnIO's installer (the sensors' driver: only the file that was fetched), as JSON, each
//! null where there is none. `tools clip OUT [--model ID]`: the standby clip of a TURZX USB
//! panel as the app makes it, with the ffmpeg it finds (how many pictures went in, as JSON):
//! what packaging/check_tools.py checks of what a download carries.

use std::path::{Path, PathBuf};

use lp_py::{Dict, Value};

use crate::home::Home;

/// The panel whose standby clip `tools clip` makes where none is named: one of the panels
/// it is written to most.
const CLIP_MODEL: &str = "turing-8.8-usb";

fn path_or_none(found: Option<PathBuf>) -> Value {
    found.map_or(Value::None, |path| Value::Str(path.display().to_string()))
}

fn json(told: Dict) -> String {
    let dumps = lp_py::json::Dumps {
        ensure_ascii: false,
        ..Default::default()
    };
    lp_py::json::dumps(&Value::Dict(told), &dumps)
}

/// found: where the app finds each of its programs.
fn found() -> Dict {
    let mut told = Dict::default();
    told.insert(
        "ffmpeg".into(),
        path_or_none(lp_devices::h264::find_ffmpeg("").ok()),
    );
    told.insert(
        "engine".into(),
        path_or_none(lp_devices::engine::find_engine()),
    );
    told.insert(
        "presentmon".into(),
        path_or_none(lp_sensors::presentmon::find_presentmon("", &mut Vec::new())),
    );
    told.insert(
        "pawnio".into(),
        path_or_none(crate::system_setup::pawnio_installer()),
    );
    told
}

pub fn command(args: &[String]) -> Result<i32, String> {
    match args {
        [] => {
            println!("{}", json(found()));
            Ok(0)
        }
        [what, out, rest @ ..] if what == "clip" => {
            let id = match rest {
                [] => CLIP_MODEL,
                [option, id] if option == "--model" => id,
                _ => return Err("tools clip: usage: tools clip OUT [--model ID]".into()),
            };
            let model = crate::start_screen::model_of(id)
                .ok_or_else(|| format!("tools clip: --model: {id} is no TURZX USB panel"))?;
            let home = Home::find()?;
            let env = lp_theme::Env {
                fonts_dir: home.fonts(),
            };
            let ffmpeg = lp_devices::h264::find_ffmpeg("")?;
            let (clip, pictures) = crate::start_screen::standby_clip(&env, model, &ffmpeg)?;
            std::fs::write(Path::new(out), &clip).map_err(|e| format!("{out}: {e}"))?;
            let mut told = Dict::default();
            told.insert("model".into(), Value::from(model.id));
            told.insert("ffmpeg".into(), Value::Str(ffmpeg.display().to_string()));
            told.insert("pictures".into(), Value::int(pictures as i64));
            told.insert("bytes".into(), Value::int(clip.len() as i64));
            println!("{}", json(told));
            Ok(0)
        }
        _ => Err("tools: usage: tools | tools clip OUT [--model ID]".into()),
    }
}

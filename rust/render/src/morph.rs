//! The morph's parts (render/morph.py): what each widget shows, so that the same thing is found
//! on the next screen; a frame as it was composed (Renderer::parting) taken apart into the frame
//! without what flies and each thing that flies, alone; and a thing laid where its flight has
//! got to. The same steps as Python's, in the same order: the same pixels.

use std::collections::{HashMap, HashSet};
use std::rc::Rc;

use lp_pixels::{Filter, Image, Mode};
use lp_py::{Dict, Value};

use crate::compose;
use crate::light::whole;
use crate::piece::{Failed, Piece, Result};

/// TIME_FIELDS: the strftime fields of a time of day (a clock with one shows the time).
const TIME_FIELDS: &str = "HIklMSpPXRTrc";
/// TOGETHER: a thing's pieces lie together where their box is at most this much larger.
const TOGETHER: f64 = 1.5;

fn failed(e: lp_pixels::Error) -> Failed {
    Failed(e.0)
}

fn round_int(x: f64) -> Result<i64> {
    whole(x, f64::round_ties_even)
}

/// shown_as: what `w` shows, for the morph to find it on the next screen; None for what is the
/// same on any screen or what this cannot tell.
pub fn shown_as(w: &Dict) -> Option<String> {
    let Some(Value::Str(kind)) = w.get("type") else {
        return None;
    };
    let said = |key: &str| w.get(key).map_or(String::new(), Value::str);
    let kind = kind.as_str();
    match kind {
        "clock" => {
            let form = match w.get("format") {
                Some(v) if v.truthy() => v.str(),
                _ => "%H:%M".to_string(),
            };
            let time = TIME_FIELDS.chars().any(|c| form.contains(&format!("%{c}")));
            Some(if time { "time" } else { "date" }.to_string())
        }
        "analog" => Some("time".to_string()),
        "moon" | "earth" | "sunpath" | "calendar" | "download" => Some(kind.to_string()),
        "countdown" => Some(format!("countdown:{}", said("target"))),
        "weather" => Some(format!("weather:{}", said("field"))),
        "image" => match w.get("src") {
            Some(Value::Str(src)) if src.starts_with('@') => Some(format!("picture:{}", &src[1..])),
            _ => None,
        },
        _ => {
            let form = match kind {
                "gauge" => "ring",
                "bar" => "bar",
                "graph" => "graph",
                "metric" => "number",
                "fan" => "fan",
                "agent" => "sign",
                "symbol" => "symbol",
                _ => return None,
            };
            match w.get("sensor") {
                Some(Value::Str(sensor)) if !sensor.is_empty() => Some(format!("{form}:{sensor}")),
                _ => None,
            }
        }
    }
}

/// Parting: a frame as it was composed: its ground (the panel's size, in the frame's mode), its
/// pieces in their order, what each widget on it shows (shown_as) and the frame they made (None:
/// made here from them where it is asked for).
#[derive(Clone)]
pub struct Parting {
    pub ground: Image,
    pub pieces: Vec<(String, Rc<Piece>)>,
    pub shows: HashMap<String, String>,
    pub opaque: bool,
    pub frame: Option<Image>,
}

impl Parting {
    pub fn size(&self) -> (usize, usize) {
        (self.ground.width(), self.ground.height())
    }
}

/// Part: a thing that flies: its box on its panel (x, y, w, h), its picture alone (RGBA) and
/// the light its pieces add (RGB), if any.
pub struct Part {
    pub bounds: (i64, i64, i64, i64),
    pub body: Image,
    pub light: Option<Image>,
}

/// A thing on a frame: its pieces (their places in the frame's order) and the box round them
/// (x0, y0, x1, y1).
pub type Found = (Vec<usize>, (i64, i64, i64, i64));

/// things: each thing the frame shows once, together; what is shown in pieces apart is none,
/// and what covers all of the panel (a cover's glow behind everything) is the ground.
pub fn things(parting: &Parting) -> HashMap<String, Found> {
    let size = parting.size();
    let whole = Some((0, 0, size.0 as i64, size.1 as i64));
    let mut found: Vec<(String, Vec<usize>)> = Vec::new();
    for (i, (wid, piece)) in parting.pieces.iter().enumerate() {
        let id = wid.split('\0').next().unwrap_or("");
        let Some(key) = parting.shows.get(id) else {
            continue;
        };
        if compose::reach(piece, size) == whole {
            continue;
        }
        match found.iter_mut().find(|(k, _)| k == key) {
            Some((_, places)) => places.push(i),
            None => found.push((key.clone(), vec![i])),
        }
    }
    let mut together = HashMap::new();
    for (key, places) in found {
        let boxes: Vec<_> = (places.iter())
            .filter_map(|&i| compose::reach(&parting.pieces[i].1, size))
            .collect();
        if boxes.is_empty() {
            continue;
        }
        let bounds = (
            boxes.iter().map(|b| b.0).min().unwrap_or(0),
            boxes.iter().map(|b| b.1).min().unwrap_or(0),
            boxes.iter().map(|b| b.2).max().unwrap_or(0),
            boxes.iter().map(|b| b.3).max().unwrap_or(0),
        );
        let own: i64 = boxes.iter().map(|b| (b.2 - b.0) * (b.3 - b.1)).sum();
        let area = (bounds.2 - bounds.0) * (bounds.3 - bounds.1);
        if area as f64 <= TOGETHER * own as f64 {
            together.insert(key, (places, bounds));
        }
    }
    together
}

/// take_apart: the frame without the pieces `taken` (in RGB), and each thing taken alone: its
/// pieces laid on nothing in their order (a light added apart).
pub fn take_apart(
    parting: &Parting,
    taken: &[(String, Found)],
) -> Result<(Image, HashMap<String, Part>)> {
    let gone: HashSet<usize> = (taken.iter())
        .flat_map(|(_, (places, _))| places.iter().copied())
        .collect();
    let size = parting.size();
    let whole = (0, 0, size.0 as i64, size.1 as i64);
    let (mut canvas, regions) = match &parting.frame {
        // (the frame from its ground up, without them)
        None => (parting.ground.clone(), vec![whole]),
        // (the frame as it was, laid again only where they lay)
        Some(frame) => (frame.clone(), laid_again(parting, &gone)),
    };
    for region in regions {
        let (x0, y0, x1, y1) = region;
        let mut part = parting.ground.crop(x0, y0, x1, y1);
        for (i, (_, piece)) in parting.pieces.iter().enumerate() {
            let meets = compose::reach(piece, size).is_some_and(|r| meet(r, region));
            if !gone.contains(&i) && meets {
                // (one broken widget must not stop the morph: as the frame)
                let _ = compose::composite(&mut part, piece, (x0, y0), parting.opaque);
            }
        }
        canvas.paste(&part, x0, y0, None).map_err(failed)?;
    }
    let without = if canvas.mode() == Mode::RGB {
        canvas
    } else {
        canvas.convert(Mode::RGB).map_err(failed)?
    };
    let mut parts = HashMap::new();
    for (key, (places, (x0, y0, x1, y1))) in taken {
        let (w, h) = ((x1 - x0) as usize, (y1 - y0) as usize);
        let mut body = Image::blank(Mode::RGBA, w, h);
        let mut light = Image::blank(Mode::RGB, w, h);
        let mut lit = false;
        for &i in places {
            let piece = &parting.pieces[i].1;
            let mut laid = || -> Result<()> {
                if piece.add {
                    compose::add_light(&mut light, &piece.whole(), piece.x - x0, piece.y - y0)?;
                    lit = true;
                    return Ok(());
                }
                compose::paste(&mut body, &piece.whole(), piece.x - x0, piece.y - y0, false)?;
                if let Some(glow) = &piece.light {
                    compose::add_light(&mut light, &glow.whole(), glow.x - x0, glow.y - y0)?;
                    lit = true;
                }
                Ok(())
            };
            let _ = laid();
        }
        let part = Part {
            bounds: (*x0, *y0, w as i64, h as i64),
            body,
            light: lit.then_some(light),
        };
        parts.insert(key.clone(), part);
    }
    Ok((without, parts))
}

fn meet(a: (i64, i64, i64, i64), b: (i64, i64, i64, i64)) -> bool {
    a.0 < b.2 && b.0 < a.2 && a.1 < b.3 && b.1 < a.3
}

/// _where: where the pieces `gone` left their pixels, one box each, each grown over the whole
/// box of every frosted backdrop it touches (its blur takes all of the box under it), until
/// none grows: what is laid again without them.
fn laid_again(parting: &Parting, gone: &HashSet<usize>) -> Vec<(i64, i64, i64, i64)> {
    let size = parting.size();
    let mut sorted: Vec<usize> = gone.iter().copied().collect();
    sorted.sort_unstable();
    let mut regions: Vec<_> = (sorted.iter())
        .filter_map(|&i| compose::reach(&parting.pieces[i].1, size))
        .collect();
    let (w, h) = (size.0 as i64, size.1 as i64);
    let frosted: Vec<_> = (parting.pieces.iter())
        .filter_map(|(_, piece)| {
            let [x, y, bw, bh] = piece.hit?;
            piece.backdrop.as_ref()?;
            let b = (x.max(0), y.max(0), (x + bw).min(w), (y + bh).min(h));
            (b.2 > b.0 && b.3 > b.1).then_some(b)
        })
        .collect();
    let mut grown = true;
    while grown {
        grown = false;
        for r in regions.iter_mut() {
            for &b in &frosted {
                let inside = b.0 >= r.0 && b.1 >= r.1 && b.2 <= r.2 && b.3 <= r.3;
                if meet(*r, b) && !inside {
                    *r = (r.0.min(b.0), r.1.min(b.1), r.2.max(b.2), r.3.max(b.3));
                    grown = true;
                }
            }
        }
    }
    regions
}

/// faded: a table that scales each level by `levels` (0 to 1), rounded as Python rounds.
pub fn faded(levels: f64) -> Vec<i64> {
    (0..256)
        .map(|v| (v as f64 * levels).round_ties_even() as i64)
        .collect()
}

/// lay: `part` laid on `out` (RGB) at `alpha`: as large as fits `room` (its own shape kept),
/// its middle at `middle`, its light added.
pub fn lay(
    out: &mut Image,
    part: &Part,
    middle: (f64, f64),
    room: (f64, f64),
    alpha: f64,
) -> Result<()> {
    if alpha <= 0.0 {
        return Ok(());
    }
    let (bw, bh) = part.body.size();
    let ratio = bw as f64 / bh as f64;
    let (mut dw, mut dh) = (room.0, room.0 / ratio);
    if dh > room.1 {
        (dw, dh) = (room.1 * ratio, room.1);
    }
    let size = (
        round_int(dw)?.max(1) as usize,
        round_int(dh)?.max(1) as usize,
    );
    let (x, y) = (
        round_int(middle.0 - dw / 2.0)?,
        round_int(middle.1 - dh / 2.0)?,
    );
    let mut body = if size != part.body.size() {
        (part
            .body
            .resize(size.0, size.1, Filter::Bicubic, None, None))
        .map_err(failed)?
    } else {
        part.body.clone()
    };
    if alpha < 1.0 {
        let a = body.getchannel(3).map_err(failed)?;
        let a = a.point(&faded(alpha)).map_err(failed)?;
        body.putalpha(&a).map_err(failed)?;
    }
    out.paste(&body, x, y, Some(&body)).map_err(failed)?;
    if let Some(light) = &part.light {
        let mut light = if size != light.size() {
            (light.resize(size.0, size.1, Filter::Bicubic, None, None)).map_err(failed)?
        } else {
            light.clone()
        };
        if alpha < 1.0 {
            light = light.point(&faded(alpha).repeat(3)).map_err(failed)?;
        }
        compose::add_light(out, &light, x, y)?;
    }
    Ok(())
}

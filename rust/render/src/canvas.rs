//! One transition across all the panels, as they stand (render/canvas.py): a slide, a wipe or
//! the line of light runs over them as over one surface, through the gap between them, and
//! each panel draws its own part of it, from the same moment on. The transition is told where
//! this panel stands and where all of them do (`params["canvas"]`: `{"own": [x, y, w, h],
//! "whole": [x, y, w, h]}`, in millimetres); without that it plays on its panel alone.

use lp_pixels::{chops, Filter, Image, Mode};
use lp_py::{Dict, Value};

use crate::light::{py_max, py_min, smooth, whole};
use crate::piece::{Failed, Result};

/// how long each takes across the panels (CANVAS_DURATIONS)
pub fn canvas_duration(name: &str) -> Option<f64> {
    match name {
        "slide" | "slide-right" | "slide-up" | "slide-down" | "wipe" => Some(1.25),
        "light" => Some(1.4),
        _ => None,
    }
}

/// SLIDE_WAYS: where the content of a slide goes
pub fn slide_way(name: &str) -> Option<(i64, i64)> {
    match name {
        "slide" => Some((-1, 0)),
        "slide-right" => Some((1, 0)),
        "slide-up" => Some((0, -1)),
        "slide-down" => Some((0, 1)),
        _ => None,
    }
}

const SHADE_MM: f64 = 6.0;
const SHADE: f64 = 0.45;
const LIGHT_MARGIN_MM: f64 = 12.0;

/// x, y, width, height in millimetres
pub type Rect = (f64, f64, f64, f64);

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Canvas {
    pub own: Rect,
    pub whole: Rect,
}

fn round_int(x: f64) -> Result<i64> {
    whole(x, f64::round_ties_even)
}

fn failed(e: lp_pixels::Error) -> Failed {
    Failed(e.0)
}

/// _rect: four numbers (no bools), finite, with a width and height above 0
fn rect(value: Option<&Value>) -> Option<Rect> {
    let Some(Value::List(items)) = value else {
        return None;
    };
    if items.len() != 4 {
        return None;
    }
    let mut v = [0.0; 4];
    for (slot, item) in v.iter_mut().zip(items) {
        *slot = match item {
            Value::Int(n) => lp_py::num::int_to_float(n).ok()?,
            Value::Float(f) => *f,
            _ => return None,
        };
    }
    if !v.iter().all(|x| x.is_finite()) || v[2] <= 0.0 || v[3] <= 0.0 {
        return None;
    }
    Some((v[0], v[1], v[2], v[3]))
}

/// canvas_of: the canvas a transition's parameters give, or None.
pub fn canvas_of(params: &Dict) -> Option<Canvas> {
    let Some(Value::Dict(spec)) = params.get("canvas") else {
        return None;
    };
    Some(Canvas {
        own: rect(spec.get("own"))?,
        whole: rect(spec.get("whole"))?,
    })
}

/// span: where `rect` begins and ends along `way`.
pub fn span((x, y, w, h): Rect, (dx, dy): (i64, i64)) -> (f64, f64) {
    let (dx, dy) = (dx as f64, dy as f64);
    let ends = [
        x * dx + y * dy,
        (x + w) * dx + y * dy,
        x * dx + (y + h) * dy,
        (x + w) * dx + (y + h) * dy,
    ];
    let low = ends[1..].iter().fold(ends[0], |a, &b| py_min(a, b));
    let high = ends[1..].iter().fold(ends[0], |a, &b| py_max(a, b));
    (low, high)
}

/// seam: where the front that runs along `way` over the whole canvas is on this panel, `t` of
/// the way: in its pixels across (or down), 0 to `size`.
pub fn seam(canvas: &Canvas, way: (i64, i64), t: f64, size: i64) -> Result<i64> {
    let (a, b) = span(canvas.whole, way);
    let front = a + smooth(t) * (b - a);
    let (x, y, w, h) = canvas.own;
    let at = if way.0 != 0 {
        (front * way.0 as f64 - x) * size as f64 / w
    } else {
        (front * way.1 as f64 - y) * size as f64 / h
    };
    Ok(size.min(0.max(round_int(at)?)))
}

/// slide: the new screen pushes the old one out along `way`, its seam where the front over
/// all the panels is, a soft shadow on the old one beside it.
pub fn slide(old: &Image, new: &Image, t: f64, canvas: &Canvas, way: (i64, i64)) -> Result<Image> {
    let (w, h) = (new.width() as i64, new.height() as i64);
    let across = way.0 != 0;
    let size = if across { w } else { h };
    let at = seam(canvas, way, t, size)?;
    let ahead = way.0 + way.1 > 0;
    let mut out = Image::blank(Mode::RGB, new.width(), new.height());
    let (o, n) = if ahead {
        (at, at - size)
    } else {
        (at - size, at)
    };
    let rgb = |im: &Image| -> Result<Image> {
        match im.mode() {
            Mode::RGB => Ok(im.clone()),
            _ => im.convert(Mode::RGB).map_err(failed),
        }
    };
    let (ox, oy) = if across { (o, 0) } else { (0, o) };
    let (nx, ny) = if across { (n, 0) } else { (0, n) };
    out.paste(&rgb(old)?, ox, oy, None).map_err(failed)?;
    out.paste(&rgb(new)?, nx, ny, None).map_err(failed)?;
    if 0 < at && at < size {
        shade(&mut out, at, ahead, across, canvas)?;
    }
    Ok(out)
}

/// _shade: the shadow at the seam `at`, on the old screen's side.
fn shade(out: &mut Image, at: i64, ahead: bool, across: bool, canvas: &Canvas) -> Result<()> {
    let (w, h) = (out.width() as i64, out.height() as i64);
    let size = if across { w } else { h };
    let mm = if across { canvas.own.2 } else { canvas.own.3 };
    let deep = 1.max(round_int(SHADE_MM * size as f64 / mm)?);
    let mut ramp = (0..deep)
        .map(|i| {
            let v = 255.0 * SHADE * (1.0 - (i as f64 + 0.5) / deep as f64);
            round_int(v).map(|v| v as u8)
        })
        .collect::<Result<Vec<u8>>>()?;
    let first = if ahead { at } else { at - deep };
    if !ahead {
        ramp.reverse();
    }
    let (lo, hi) = (0.max(first), size.min(first + deep));
    if hi <= lo {
        return Ok(());
    }
    let ramp = ramp[(lo - first) as usize..(hi - first) as usize].to_vec();
    let n = (hi - lo) as usize;
    let (mask, box_) = if across {
        let m = Image::from_bytes(Mode::L, n, 1, ramp)
            .and_then(|m| m.resize(n, h as usize, Filter::Nearest, None, None))
            .map_err(failed)?;
        (m, (lo, 0, hi, h))
    } else {
        let m = Image::from_bytes(Mode::L, 1, n, ramp)
            .and_then(|m| m.resize(w as usize, n, Filter::Nearest, None, None))
            .map_err(failed)?;
        (m, (0, lo, w, hi))
    };
    let part = out.crop(box_.0, box_.1, box_.2, box_.3);
    let dark = Image::blank(Mode::RGB, part.width(), part.height());
    let shaded = chops::composite(&dark, &part, &mask).map_err(failed)?;
    out.paste(&shaded, box_.0, box_.1, None).map_err(failed)
}

/// wipe_map: the wipe's map on this panel, its part of one that goes from 0 at the left of the
/// whole canvas to 255 at its right.
pub fn wipe_map(canvas: &Canvas, w: usize, h: usize) -> Result<Image> {
    let (x, _, mw, _) = canvas.own;
    let (left, _, width, _) = canvas.whole;
    let row = (0..w)
        .map(|i| {
            let v = 255.0 * (x + (i as f64 + 0.5) * mw / w as f64 - left) / width;
            round_int(v).map(|v| v.clamp(0, 255) as u8)
        })
        .collect::<Result<Vec<u8>>>()?;
    Image::from_bytes(Mode::L, w, 1, row)
        .and_then(|r| r.resize(w, h, Filter::Nearest, None, None))
        .map_err(failed)
}

/// light_at: where the line of light is on this panel, `t` of the way across all of them, in
/// its pixels (it may be beyond the panel).
pub fn light_at(canvas: &Canvas, t: f64, w: i64) -> Result<i64> {
    let (x, _, mw, _) = canvas.own;
    let (left, _, width, _) = canvas.whole;
    let front = left - LIGHT_MARGIN_MM + (width + 2.0 * LIGHT_MARGIN_MM) * smooth(t);
    round_int((front - x) * w as f64 / mw)
}

//! One transition across all the panels, as they stand (render/canvas.py): a slide, a wipe,
//! the line of light, the iris, the shatter, the cascade, the comet or the portal runs over
//! them as over one surface, through the gap between them, and each panel draws its own part
//! of it, from the same moment on. The transition is told where this panel stands and where all
//! of them do (`params["canvas"]`: `{"own": [x, y, w, h], "whole": [x, y, w, h]}`, in
//! millimetres); without that it plays on its panel alone.

use lp_pixels::{chops, Filter, Image, Mode};
use lp_py::{Dict, Value};

use crate::light::{clamp, py_max, py_min, smooth, whole};
use crate::piece::{Failed, Result};

/// how long each takes across the panels (CANVAS_DURATIONS)
pub fn canvas_duration(name: &str) -> Option<f64> {
    match name {
        "slide" | "slide-right" | "slide-up" | "slide-down" | "wipe" => Some(1.25),
        "light" => Some(1.4),
        "iris" => Some(1.3),
        "shatter" => Some(1.8),
        "cascade" => Some(1.9),
        "comet" => Some(1.7),
        "portal" => Some(1.5),
        "morph" => Some(2.4),
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
/// a panel by itself, for one that is drawn in millimetres alone too: its shorter side taken
/// for this, the 9.2" bar's height (ALONE_MM)
const ALONE_MM: f64 = 56.68;

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
    // (each in RGB: as it is, not a copy, where it is so already)
    let rgb = |im: &Image| -> Result<Option<Image>> {
        match im.mode() {
            Mode::RGB => Ok(None),
            _ => im.convert(Mode::RGB).map(Some).map_err(failed),
        }
    };
    let (ox, oy) = if across { (o, 0) } else { (0, o) };
    let (nx, ny) = if across { (n, 0) } else { (0, n) };
    let (old_rgb, new_rgb) = (rgb(old)?, rgb(new)?);
    out.paste(old_rgb.as_ref().unwrap_or(old), ox, oy, None)
        .map_err(failed)?;
    out.paste(new_rgb.as_ref().unwrap_or(new), nx, ny, None)
        .map_err(failed)?;
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

// -- in millimetres: a panel by itself or over all of them ---------------------------------------

/// alone: a panel by itself as a canvas of its own.
pub fn alone((w, h): (usize, usize)) -> Canvas {
    let mm = ALONE_MM / py_min(w as f64, h as f64);
    let rect = (0.0, 0.0, w as f64 * mm, h as f64 * mm);
    Canvas {
        own: rect,
        whole: rect,
    }
}

/// way_of: along the longer side of all the panels, right, or down where they stand taller
/// than wide.
pub fn way_of(canvas: &Canvas) -> (i64, i64) {
    if canvas.whole.2 >= canvas.whole.3 {
        (1, 0)
    } else {
        (0, 1)
    }
}

pub fn middle((x, y, w, h): Rect) -> (f64, f64) {
    (x + w / 2.0, y + h / 2.0)
}

/// reach: how far the farthest corner of `rect` is from `at`.
pub fn reach((x, y, w, h): Rect, at: (f64, f64)) -> f64 {
    let corners = [(x, y), (x + w, y), (x, y + h), (x + w, y + h)];
    let far = |(cx, cy): (f64, f64)| ((cx - at.0) * (cx - at.0) + (cy - at.1) * (cy - at.1)).sqrt();
    corners[1..]
        .iter()
        .fold(far(corners[0]), |a, &c| py_max(a, far(c)))
}

/// per_mm: this panel's pixels per millimetre, across and down.
pub fn per_mm(canvas: &Canvas, (w, h): (usize, usize)) -> (f64, f64) {
    (w as f64 / canvas.own.2, h as f64 / canvas.own.3)
}

/// pixel: where the point `at` (millimetres) is on this panel, in its pixels.
pub fn pixel(canvas: &Canvas, size: (usize, usize), at: (f64, f64)) -> (f64, f64) {
    let (sx, sy) = per_mm(canvas, size);
    ((at.0 - canvas.own.0) * sx, (at.1 - canvas.own.1) * sy)
}

/// centres: where the middles of this panel's columns and rows are, in millimetres.
pub fn centres(canvas: &Canvas, size: (usize, usize)) -> (Vec<f64>, Vec<f64>) {
    let (sx, sy) = per_mm(canvas, size);
    (
        (0..size.0)
            .map(|i| canvas.own.0 + (i as f64 + 0.5) / sx)
            .collect(),
        (0..size.1)
            .map(|i| canvas.own.1 + (i as f64 + 0.5) / sy)
            .collect(),
    )
}

/// levels, of one value: rounded to the nearest level (an even one at a half), within 0 and
/// 255; what is no number at all, 0.
pub fn level(v: f64) -> u8 {
    if v.is_nan() {
        0
    } else {
        v.round_ties_even().clamp(0.0, 255.0) as u8
    }
}

/// distances, of one pixel: how far its middle is from a point, the offsets given.
pub fn distance(dx: f64, dy: f64) -> f64 {
    (dy * dy + dx * dx).sqrt()
}

/// radial_map: a map of how far each pixel of this panel is from `at`: 0 there, 255 `far`
/// away.
pub fn radial_map(
    canvas: &Canvas,
    size: (usize, usize),
    at: (f64, f64),
    far: f64,
) -> Result<Image> {
    let (xs, ys) = centres(canvas, size);
    let mut bytes = Vec::with_capacity(size.0 * size.1);
    for &y in &ys {
        let dy = y - at.1;
        bytes.extend(
            xs.iter()
                .map(|&x| level(255.0 * distance(x - at.0, dy) / far)),
        );
    }
    Image::from_bytes(Mode::L, size.0, size.1, bytes).map_err(failed)
}

/// linear_map: a map of how far along `u` (a unit vector) each pixel of this panel is: 0 at
/// `lo` millimetres, 255 at `hi`.
pub fn linear_map(
    canvas: &Canvas,
    size: (usize, usize),
    u: (f64, f64),
    lo: f64,
    hi: f64,
) -> Result<Image> {
    let (xs, ys) = centres(canvas, size);
    let mut bytes = Vec::with_capacity(size.0 * size.1);
    for &y in &ys {
        let down = y * u.1;
        bytes.extend(
            xs.iter()
                .map(|&x| level(255.0 * (down + x * u.0 - lo) / (hi - lo))),
        );
    }
    Image::from_bytes(Mode::L, size.0, size.1, bytes).map_err(failed)
}

/// threshold: the mask of a map below `level`: 255 where a value is `soft` below it or more, 0
/// above it, a ramp in between.
pub fn threshold(level: f64, soft: f64) -> Result<Vec<i64>> {
    (0..256)
        .map(|v| {
            let v = v as f64;
            if v <= level - soft {
                Ok(255)
            } else if v > level {
                Ok(0)
            } else {
                round_int(255.0 * (level - v) / soft)
            }
        })
        .collect()
}

/// in_out: slow, fast, slow (cubic).
pub fn in_out(u: f64) -> f64 {
    let u = clamp(u);
    if u < 0.5 {
        4.0 * u * u * u
    } else {
        1.0 - (-2.0 * u + 2.0).powf(3.0) / 2.0
    }
}

pub fn in_out_sine(u: f64) -> f64 {
    -((std::f64::consts::PI * clamp(u)).cos() - 1.0) / 2.0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_level_is_the_nearest_an_even_one_at_a_half_within_0_and_255() {
        // (canvas.levels: numpy's rint of each value, NaN as 0, clipped; what Python gives)
        let cases = [
            (2.5, 2),
            (3.5, 4),
            (0.5, 0),
            (1.5, 2),
            (254.5, 254),
            (255.5, 255),
            (127.49999999999999, 127),
            (-0.5, 0),
            (-3.0, 0),
            (300.0, 255),
            (f64::NAN, 0),
            (f64::INFINITY, 255),
            (f64::NEG_INFINITY, 0),
        ];
        for (v, want) in cases {
            assert_eq!(level(v), want, "{v}");
        }
    }

    #[test]
    fn in_out_holds_still_before_and_after_its_way() {
        // (canvas.in_out clamps its way first: what Python gives)
        assert_eq!(in_out(-0.5), 0.0);
        assert_eq!(in_out(1.5), 1.0);
        assert_eq!(in_out(0.25), 0.0625);
        assert_eq!(in_out(0.75), 0.9375);
    }
}

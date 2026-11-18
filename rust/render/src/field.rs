//! render/field.py: a light field under a theme, soft pools of light that drift slowly over the
//! panels that stand together, as one; each panel shows its part of it, where it stands (in
//! millimetres). Made at an eighth of the panel's pixels, a point in the middle of each block
//! of 8 by 8, and drawn up between them in whole numbers ([`drawn_up`]), the same numbers as
//! Python's: the C library's exp, sin and cos, the products and sums in its order.

use std::f64::consts::PI;

use lp_pixels::{Image, Mode};

/// The panel's pixels a point of the field stands for, across and down.
pub const STEP: usize = 8;
/// Fields a second (the panel's frames between are the same: the encoder skips them).
pub const RATE: f64 = 6.25;

/// x, y, width, height in millimetres.
pub type Rect = (f64, f64, f64, f64);

/// A pool of light: its colour at rest and under load, how far it drifts as a share of the
/// canvas across and down, in how many seconds it goes there and back each way, where in its
/// round it starts (radians), and how wide it is (its Gaussian's sigma, millimetres).
#[derive(Clone, Copy, Debug)]
pub struct Pool {
    pub cool: [f64; 3],
    pub warm: [f64; 3],
    pub ax: f64,
    pub ay: f64,
    pub px: f64,
    pub py: f64,
    pub phase: f64,
    pub radius: f64,
}

const fn pool(
    cool: [f64; 3],
    warm: [f64; 3],
    drift: (f64, f64),
    round: (f64, f64),
    phase: f64,
    radius: f64,
) -> Pool {
    Pool {
        cool,
        warm,
        ax: drift.0,
        ay: drift.1,
        px: round.0,
        py: round.1,
        phase,
        radius,
    }
}

/// Lumen's: its CPU, GPU and memory colours at rest, amber, coral and rose under load.
#[rustfmt::skip]
pub const LUMEN: [Pool; 3] = [
    pool([46.0, 230.0, 214.0], [245.0, 168.0, 84.0], (0.46, 0.36), (330.0, 247.0), 0.4, 64.0),
    pool([127.0, 156.0, 255.0], [248.0, 113.0, 113.0], (0.44, 0.40), (390.0, 281.0), 2.5, 72.0),
    pool([199.0, 146.0, 255.0], [255.0, 150.0, 120.0], (0.42, 0.38), (450.0, 223.0), 4.4, 58.0),
];
/// The light's strength at rest, and what full load adds.
const STRENGTH: (f64, f64) = (0.19, 0.15);
/// The light swells and ebbs once in this long.
const BREATH_S: f64 = 9.0;
/// By this share at rest, and what full load adds.
const BREATH: (f64, f64) = (0.04, 0.10);
/// How far toward its warm colour a pool goes at full load.
const WARMTH: f64 = 0.85;

/// The pools of the look `name`; None for a look not known.
pub fn look(name: &str) -> Option<&'static [Pool]> {
    match name {
        "lumen" => Some(&LUMEN),
        _ => None,
    }
}

/// stepped: the moment of the field shown at `t` (wall clock, seconds): the last of its steps.
pub fn stepped(t: f64) -> f64 {
    (t * RATE).floor() / RATE
}

/// small: the field at an eighth of the panel (`size` its pixels) as levels, RGB row by row:
/// `own` where the panel stands and `whole` the canvas of all the panels standing together
/// (millimetres), at `t` (seconds) under `load` (0 to 1), its light on `base` (the theme's
/// background colour); its width and height first. Each point in the middle of its block of
/// the panel's pixels, the last one's too where the panel ends within it.
pub fn small(
    size: (usize, usize),
    own: Rect,
    whole: Rect,
    t: f64,
    load: f64,
    pools: &[Pool],
    base: [u8; 3],
) -> (usize, usize, Vec<u8>) {
    let load = load.clamp(0.0, 1.0);
    let (fw, fh) = (size.0.div_ceil(STEP), size.1.div_ceil(STEP));
    let half = STEP as f64 / 2.0;
    let (across, down) = (own.2 / size.0 as f64, own.3 / size.1 as f64);
    let xs: Vec<f64> = (0..fw)
        .map(|i| own.0 + ((i * STEP) as f64 + half) * across)
        .collect();
    let ys: Vec<f64> = (0..fh)
        .map(|i| own.1 + ((i * STEP) as f64 + half) * down)
        .collect();
    let strength = STRENGTH.0 + STRENGTH.1 * load;
    let breathe = 1.0 + (BREATH.0 + BREATH.1 * load) * (2.0 * PI * t / BREATH_S).sin();
    let mix = (load * WARMTH).min(1.0);
    let mut acc: Vec<f64> = base
        .iter()
        .map(|&v| f64::from(v))
        .cycle()
        .take(fw * fh * 3)
        .collect();
    for p in pools {
        let cx = whole.0 + whole.2 * (0.5 + p.ax * (2.0 * PI * t / p.px + p.phase).sin());
        let cy = whole.1 + whole.3 * (0.5 + p.ay * (2.0 * PI * t / p.py + p.phase * 1.3).cos());
        let inv = 1.0 / (2.0 * p.radius * p.radius);
        let across: Vec<f64> = xs
            .iter()
            .map(|&x| (-((x - cx) * (x - cx)) * inv).exp())
            .collect();
        let down: Vec<f64> = ys
            .iter()
            .map(|&y| (-((y - cy) * (y - cy)) * inv).exp())
            .collect();
        let colour: [f64; 3] = std::array::from_fn(|c| {
            (p.cool[c] + (p.warm[c] - p.cool[c]) * mix) * strength * breathe
        });
        for (row, &d) in down.iter().enumerate() {
            for (col, &a) in across.iter().enumerate() {
                let weight = d * a;
                let at = (row * fw + col) * 3;
                for c in 0..3 {
                    acc[at + c] += weight * colour[c];
                }
            }
        }
    }
    let levels = acc
        .iter()
        .map(|&v| (v + 0.5).floor().clamp(0.0, 255.0) as u8)
        .collect();
    (fw, fh, levels)
}

/// field: the panel's part of the field (RGB, `size`): [`small`] drawn up.
pub fn field(
    size: (usize, usize),
    own: Rect,
    whole: Rect,
    t: f64,
    load: f64,
    pools: &[Pool],
    base: [u8; 3],
) -> lp_pixels::Result<Image> {
    let (fw, fh, levels) = small(size, own, whole, t, load, pools, base);
    Image::from_bytes(Mode::RGB, size.0, size.1, drawn_up(&levels, fw, fh, size))
}

/// For each of `n` pixels in a row, the two points of the field it lies between and how near
/// the second, in sixteenths (render/field.py's _between): the pixel's middle is
/// `(2x - 7) / 16` of a block from the first point's.
fn between(n: usize, points: usize) -> Vec<(usize, usize, u16)> {
    let last = points as i64 - 1;
    (0..n as i64)
        .map(|x| {
            let sixteenths = 2 * x - (STEP as i64 - 1);
            let first = sixteenths.div_euclid(16);
            let near = (sixteenths - 16 * first) as u16;
            let at = |i: i64| i.clamp(0, last) as usize;
            (at(first), at(first + 1), near)
        })
        .collect()
}

/// drawn_up: the field's levels (`fw` x `fh`, RGB) drawn up to `size` as render/field.py's
/// drawn_up and the engine's: each pixel between the four points nearest its middle, weighted
/// in sixteenths each way, the sum rounded once; beyond the first and the last points, as they
/// are.
pub fn drawn_up(levels: &[u8], fw: usize, fh: usize, size: (usize, usize)) -> Vec<u8> {
    let (w, h) = size;
    let taps = between(w, fw);
    let across: Vec<Vec<u16>> = levels
        .chunks_exact(fw * 3)
        .take(fh)
        .map(|row| {
            let mut out = Vec::with_capacity(w * 3);
            for &(a, b, f) in &taps {
                for c in 0..3 {
                    let (va, vb) = (u16::from(row[a * 3 + c]), u16::from(row[b * 3 + c]));
                    out.push(va * (16 - f) + vb * f);
                }
            }
            out
        })
        .collect();
    let mut out = vec![0u8; w * h * 3];
    for ((a, b, f), line) in between(h, fh).into_iter().zip(out.chunks_exact_mut(w * 3)) {
        let (first, second, g) = (&across[a], &across[b], 16 - f);
        for ((o, &u), &v) in line.iter_mut().zip(first).zip(second) {
            *o = ((u * g + v * f + 128) >> 8) as u8;
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_field_runs_on_from_one_panel_to_the_next() {
        // two panels side by side as one canvas: where they meet, the light is the same
        let whole = (0.0, 0.0, 200.0, 50.0);
        let left = small(
            (800, 400),
            (0.0, 0.0, 100.0, 50.0),
            whole,
            37.0,
            0.4,
            &LUMEN,
            [0, 0, 0],
        );
        let right = small(
            (800, 400),
            (100.0, 0.0, 100.0, 50.0),
            whole,
            37.0,
            0.4,
            &LUMEN,
            [0, 0, 0],
        );
        let both = small((1600, 400), whole, whole, 37.0, 0.4, &LUMEN, [0, 0, 0]);
        let (w, h) = (both.0, both.1);
        for row in 0..h {
            for col in 0..w {
                let (part, x) = if col < w / 2 {
                    (&left, col)
                } else {
                    (&right, col - w / 2)
                };
                for c in 0..3 {
                    assert_eq!(
                        part.2[(row * part.0 + x) * 3 + c],
                        both.2[(row * w + col) * 3 + c]
                    );
                }
            }
        }
    }

    #[test]
    fn load_warms_and_brightens_it() {
        let own = (0.0, 0.0, 100.0, 50.0);
        let sum = |load| {
            small((800, 400), own, own, 0.0, load, &LUMEN, [0, 0, 0])
                .2
                .iter()
                .map(|&v| v as u64)
                .sum::<u64>()
        };
        assert!(sum(1.0) > sum(0.0) * 3 / 2, "{} {}", sum(1.0), sum(0.0));
        // (a field each 0.16 s: the panels' frames between it and the next are the same)
        assert_eq!(stepped(1.159), 1.12);
        assert_eq!(stepped(1.119), 0.96);
    }
}

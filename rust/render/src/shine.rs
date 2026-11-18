//! Light that moves over what a panel shows, on a panel that moves (render/shine.py): a ring's
//! end that breathes and a sweep along it, a graph's newest point that beats, a cover that
//! glows (steady) while music plays (with the bars after "Playing" and the progress's light), a
//! download's sheen and the sun's glow and glint. Each light is a layer of light added onto
//! the frame, made once for a look in `LEVELS` strengths; where it stands as it stood the frame
//! before it is the same piece. Made of sums and products of floats in the order Python's are,
//! so the bytes are the same.

use std::cell::RefCell;
use std::collections::{HashMap, HashSet};
use std::rc::Rc;

use lp_pixels::{Image, Mode};
use lp_py::{Dict, Value};

use crate::kept::Kept;
use crate::piece::{Piece, Result};
use crate::renderer::Renderer;
use crate::shapes::Rgba;
use crate::snapshot::Snapshot;
use crate::widget;

/// The kinds of moving light, as config.toml's [shine] and a theme's animation.shine name them.
pub const KINDS: [&str; 5] = ["rings", "graphs", "music", "downloads", "sun"];

const LEVELS: i64 = 12;
const REACH: f64 = 2.25;
const CORE: f64 = 3.0;
const BREATH_S: f64 = 4.0;
const SWEEP_S: f64 = 4.0;
const SWEEP_RUN_S: f64 = 1.3;
const SWEEP_NEXT_S: f64 = 0.4;
const HOT: f64 = 0.8;
const BEAT_RUN_S: f64 = 0.8;
const BEAT_STEPS: i64 = 10;
const SHEEN_S: f64 = 2.0;
const SHEEN_RUN_S: f64 = 1.2;
const GLINT_S: f64 = 4.0;
const GLINT_RUN_S: f64 = 2.2;
const GLINT_AFTER_S: f64 = 1.0;
const DANCE_HZ: f64 = 12.5;
const FADE_S: f64 = 0.6;
const COVER_GAIN: f64 = 1.9;
const COVER_SHARE: f64 = 0.6;
const LOOK_AHEAD_S: f64 = 0.5;
const SPRITE_BYTES: usize = 6 << 20;

// -- time -------------------------------------------------------------------------------------

/// phase: where `now` stands in a cycle of `period` s begun at `offset`: 0 to below 1.
pub fn phase(now: f64, period: f64, offset: f64) -> f64 {
    let u = (now - offset) / period;
    u - u.floor()
}

/// wave: a breath over a cycle, 0 at its ends, 1 halfway, smooth at both.
pub fn wave(u: f64) -> f64 {
    let tri = 1.0 - (2.0 * u - 1.0).abs();
    tri * tri * (3.0 - 2.0 * tri)
}

/// ease: 0 to 1, slow at both ends.
pub fn ease(u: f64) -> f64 {
    let u = clamp01(u);
    u * u * (3.0 - 2.0 * u)
}

fn clamp01(u: f64) -> f64 {
    // (min(1, max(0, u)) as Python takes them)
    let u = if u > 0.0 { u } else { 0.0 };
    if u < 1.0 {
        u
    } else {
        1.0
    }
}

/// hump: 0 at 0 and 1, 1 halfway.
pub fn hump(u: f64) -> f64 {
    4.0 * u * (1.0 - u)
}

/// level: the step of LEVELS a strength from 0 to 1 is made in.
pub fn level(strength: f64) -> i64 {
    (clamp01(strength) * LEVELS as f64 + 0.5).floor() as i64
}

fn round_half_up(v: f64) -> i64 {
    (v + 0.5).floor() as i64
}

/// _next_step: when `of` (a light's look at a time) next differs from its look at `now`,
/// within LOOK_AHEAD_S; None: not as soon.
fn next_step<K: PartialEq>(mut of: impl FnMut(f64) -> K, now: f64) -> Option<f64> {
    let first = of(now);
    let step = 1.0 / 50.0;
    let (mut before, mut at) = (now, now + step);
    while at - now <= LOOK_AHEAD_S {
        if of(at) != first {
            for _ in 0..8 {
                let middle = (before + at) / 2.0;
                if of(middle) != first {
                    at = middle;
                } else {
                    before = middle;
                }
            }
            return Some(at);
        }
        (before, at) = (at, at + step);
    }
    None
}

// -- the lights -------------------------------------------------------------------------------

/// A part of a light: a round light falling off from its middle with a white core of `extra`
/// (a spot), or a ring of light `extra` px either side of its radius.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Part {
    pub ring: bool,
    pub radius: f64,
    pub colour: [i64; 3],
    pub extra: f64,
    pub gain: f64,
}

fn spot(radius: f64, colour: [i64; 3], core: f64, gain: f64) -> Part {
    Part {
        ring: false,
        radius,
        colour,
        extra: core,
        gain,
    }
}

fn ring(radius: f64, colour: [i64; 3], width: f64, gain: f64) -> Part {
    Part {
        ring: true,
        radius,
        colour,
        extra: width,
        gain,
    }
}

/// _half: half the side of the square the parts' light fills.
fn half_of(parts: &[Part]) -> i64 {
    let mut reach: f64 = 0.0;
    for p in parts {
        let r = if p.ring {
            p.radius + p.extra
        } else {
            p.radius * REACH
        };
        reach = if r > reach { r } else { reach };
    }
    (reach.ceil() as i64).max(1)
}

/// make: the light of `parts` at `strength` of LEVELS (RGB, its middle at the middle pixel).
pub fn make(parts: &[Part], strength: i64) -> Image {
    let half = half_of(parts);
    let side = (2 * half + 1) as usize;
    let mut total = vec![[0.0f64; 3]; side * side];
    for p in parts {
        let colour = [p.colour[0] as f64, p.colour[1] as f64, p.colour[2] as f64];
        for row in 0..side {
            let dy = row as f64 - half as f64;
            for col in 0..side {
                let dx = col as f64 - half as f64;
                let q = dx * dx + dy * dy;
                let cell = &mut total[row * side + col];
                if p.ring {
                    let e = (q.sqrt() - p.radius) / p.extra;
                    let t = (1.0 - e * e).max(0.0);
                    let fall = t * t;
                    for ch in 0..3 {
                        cell[ch] += colour[ch] * fall * p.gain;
                    }
                } else {
                    let reach = p.radius * REACH;
                    let t = (1.0 - q / (reach * reach)).max(0.0);
                    let t2 = t * t;
                    let fall = t2 * t2;
                    let core = (p.extra > 0.0).then(|| {
                        let inner = reach / CORE;
                        let c = (1.0 - q / (inner * inner)).max(0.0);
                        let c2 = c * c;
                        c2 * c2
                    });
                    for ch in 0..3 {
                        let mut v = colour[ch] * fall;
                        if let Some(core) = core {
                            v += 255.0 * p.extra * core;
                        }
                        cell[ch] += v * p.gain;
                    }
                }
            }
        }
    }
    let s = strength as f64 / LEVELS as f64;
    let mut data = Vec::with_capacity(side * side * 3);
    for cell in &total {
        for v in cell {
            data.push(((v * s).min(255.0) + 0.5).floor() as u8);
        }
    }
    Image::from_bytes(Mode::RGB, side, side, data).expect("a light's size")
}

fn rgb(colour: Rgba) -> [i64; 3] {
    [colour[0], colour[1], colour[2]]
}

/// _lerp3: a colour `t` of the way from `a` to `b` (Python's round).
fn lerp3(a: [i64; 3], b: [i64; 3], t: f64) -> [i64; 3] {
    let t = clamp01(t);
    let mut out = [0; 3];
    for i in 0..3 {
        out[i] = lp_py::round(a[i] as f64 + (b[i] - a[i]) as f64 * t) as i64;
    }
    out
}

fn parts_key(parts: &[Part], strength: i64) -> Vec<u64> {
    let mut key = vec![strength as u64];
    for p in parts {
        key.push(u64::from(p.ring));
        key.push(p.radius.to_bits());
        key.extend(p.colour.iter().map(|c| *c as u64));
        key.push(p.extra.to_bits());
        key.push(p.gain.to_bits());
    }
    key
}

/// A cover's glow at a strength: its four bands, each with where its corner lies from the
/// cover's.
type Bands = Vec<(Option<Rc<Image>>, i64, i64)>;

/// A cover's glow: made at full strength for its piece (kept with it, so its address stays its
/// own), and the strength asked last scaled from it (so a glow that stands is the same bands).
struct CoverGlow {
    piece: Rc<Piece>,
    full: Bands,
    last: RefCell<(i64, Rc<Bands>)>,
}

/// The covers' glows kept: by the cover's layer, its box and the radius.
type Covers = Kept<(usize, [i64; 4], u64), Rc<CoverGlow>>;

// -- the shine of a renderer ------------------------------------------------------------------

/// The moving light of one renderer's frames.
#[derive(Default)]
pub struct Shine {
    kinds: [bool; 5],
    sprites: Option<Kept<Vec<u64>, Rc<Image>>>,
    covers: Option<Covers>,
    laid: HashMap<String, (Rc<Image>, i64, i64, Rc<Piece>)>,
    used: HashSet<String>,
    rings: i64,
    fades: HashMap<String, (bool, f64)>,
    runs: HashMap<String, (f64, f64)>,
}

/// What a widget's lights are: (name, piece or None).
pub type Lights = Vec<(&'static str, Option<Rc<Piece>>)>;

impl Shine {
    /// Which kinds shine (the app's: config.toml's [shine] and the theme's).
    pub fn set_kinds<'a>(&mut self, kinds: impl IntoIterator<Item = &'a str>) {
        self.kinds = [false; 5];
        for kind in kinds {
            if let Some(i) = KINDS.iter().position(|k| *k == kind) {
                self.kinds[i] = true;
            }
        }
    }

    /// The kinds that shine.
    pub fn kinds(&self) -> Vec<&'static str> {
        KINDS
            .iter()
            .zip(self.kinds)
            .filter(|(_, on)| *on)
            .map(|(k, _)| *k)
            .collect()
    }

    fn on(&self, kind: &str) -> bool {
        KINDS
            .iter()
            .position(|k| *k == kind)
            .is_some_and(|i| self.kinds[i])
    }

    /// Whether any kind shines.
    pub fn any(&self) -> bool {
        self.kinds.iter().any(|on| *on)
    }

    /// A frame begins.
    pub fn begin(&mut self) {
        self.rings = 0;
        self.used.clear();
    }

    /// A frame is laid: the lights of widgets no longer on show go.
    pub fn end(&mut self) {
        let used = &self.used;
        self.laid.retain(|name, _| used.contains(name));
    }

    /// The bytes of the lights kept.
    pub fn bytes(&self) -> usize {
        self.sprites.as_ref().map_or(0, Kept::bytes) + self.covers.as_ref().map_or(0, Kept::bytes)
    }

    fn sprite(&mut self, parts: &[Part], strength: i64) -> Option<Rc<Image>> {
        if strength <= 0 {
            return None;
        }
        let kept = self.sprites.get_or_insert_with(|| Kept::new(SPRITE_BYTES));
        let key = parts_key(parts, strength);
        if let Some(hit) = kept.get(&key) {
            return Some(hit);
        }
        let made = Rc::new(make(parts, strength));
        let size = made.width() * made.height() * 4 + 128;
        kept.put(key, Rc::clone(&made), size);
        Some(made)
    }

    /// _lay: `sprite` (light) with its middle at (cx, cy).
    fn lay(
        &mut self,
        name: &str,
        sprite: Option<Rc<Image>>,
        cx: f64,
        cy: f64,
    ) -> Option<Rc<Piece>> {
        let Some(sprite) = sprite else {
            self.laid.remove(name);
            return None;
        };
        let half = (sprite.width() / 2) as i64;
        Some(self.place(
            name,
            sprite,
            round_half_up(cx) - half,
            round_half_up(cy) - half,
            true,
        ))
    }

    /// _place: `sprite` with its corner at (x, y): the piece laid there before where the same.
    fn place(&mut self, name: &str, sprite: Rc<Image>, x: i64, y: i64, add: bool) -> Rc<Piece> {
        self.used.insert(name.to_string());
        if let Some((was, wx, wy, piece)) = self.laid.get(name) {
            if Rc::ptr_eq(was, &sprite) && *wx == x && *wy == y {
                return Rc::clone(piece);
            }
        }
        let mut piece = Piece::new((*sprite).clone(), x, y);
        piece.add = add;
        let piece = Rc::new(piece);
        self.laid
            .insert(name.to_string(), (sprite, x, y, Rc::clone(&piece)));
        piece
    }

    fn none(&mut self, name: &str) -> Option<Rc<Piece>> {
        self.laid.remove(name);
        None
    }

    fn strength(w: &Dict, share: f64) -> i64 {
        level(share * widget::number(w, "opacity", 1.0))
    }

    fn rest_until<K: PartialEq>(r: &mut Renderer, of: impl FnMut(f64) -> K, now: f64) {
        if let Some(at) = next_step(of, now) {
            r.due_at(at);
        }
    }

    /// over: the lights over widget `w` (drawn as `piece`) at `now`.
    pub fn over(
        &mut self,
        r: &mut Renderer,
        w: &Dict,
        piece: &Rc<Piece>,
        snapshot: &Snapshot,
        now: f64,
    ) -> Result<Lights> {
        let kind = widget::text(w, "type");
        match kind {
            "gauge" if self.on("rings") => return self.ring(r, w, snapshot, now),
            "graph" if self.on("graphs") => return self.beat(r, w, snapshot, now),
            "downloads" if self.on("downloads") => return self.download(r, w, snapshot, now),
            "sunpath" if self.on("sun") => return self.sun(r, w, snapshot, now),
            "gauge" | "graph" | "downloads" | "sunpath" => return Ok(Vec::new()),
            _ => {}
        }
        if !self.on("music") {
            return Ok(Vec::new());
        }
        let blurred = match w.get("blur") {
            None | Some(Value::None) => false,
            Some(v) => v.truthy(),
        };
        if kind == "image" && widget::text(w, "src") == "@media.cover" && !blurred {
            return self.cover(r, w, piece, snapshot, now);
        }
        if kind == "metric" && widget::text(w, "sensor") == "media.state" {
            return self.dance(r, w, piece, snapshot, now);
        }
        if kind == "bar" && widget::text(w, "sensor") == "media.progress" {
            return self.progress(r, w, snapshot, now);
        }
        Ok(Vec::new())
    }

    // -- rings -------------------------------------------------------------------------------

    fn ring(
        &mut self,
        r: &mut Renderer,
        w: &Dict,
        snapshot: &Snapshot,
        now: f64,
    ) -> Result<Lights> {
        let wid = widget::text(w, "id").to_string();
        let names = [
            format!("{wid}\0head"),
            format!("{wid}\0hot"),
            format!("{wid}\0sweep"),
        ];
        let k = self.rings;
        self.rings += 1;
        let (value, frac) = r.gauge_fraction(w, snapshot)?;
        if value.is_none() || frac <= 0.0 {
            return Ok(vec![
                ("head", self.none(&names[0])),
                ("hot", self.none(&names[1])),
                ("sweep", self.none(&names[2])),
            ]);
        }
        let rules = crate::metric::rules_of(w).unwrap_or_default();
        let (a, b) = r.rule_paint(w, value, &rules)?;
        let colour = match b {
            None => rgb(a),
            Some(b) => lerp3(rgb(a), rgb(b), frac),
        };
        let th = widget::number(w, "thickness", 12.0).max(1.0);
        let (gw, gh) = (widget::int(w, "w", 120), widget::int(w, "h", 120));
        let cx = widget::int(w, "x", 0) as f64 + (gw - 1) as f64 / 2.0;
        let cy = widget::int(w, "y", 0) as f64 + (gh - 1) as f64 / 2.0;
        let (rx, ry) = ((gw as f64 - th) / 2.0, (gh as f64 - th) / 2.0);
        let start = widget::number(w, "start_angle", 135.0);
        let sweep = widget::number(w, "end_angle", 405.0) - start;
        let end = (start + sweep * frac).to_radians();
        let (ex, ey) = (cx + rx * end.cos(), cy + ry * end.sin());
        let head_parts = [
            spot(0.9 * th, colour, 0.9, 1.0),
            spot(2.2 * th, colour, 0.0, 0.55),
        ];
        let breath = |at: f64| 0.72 + 0.28 * wave(phase(at, BREATH_S, 0.0));
        let head = self.sprite(&head_parts, Self::strength(w, breath(now)));
        let mut out = vec![("head", self.lay(&names[0], head, ex, ey))];
        let heat = (frac - HOT) / (1.0 - HOT);
        let hot = if heat > 0.0 {
            let hot_parts = [spot(3.4 * th, colour, 0.0, 0.5)];
            self.sprite(&hot_parts, Self::strength(w, heat * breath(now)))
        } else {
            None
        };
        out.push(("hot", self.lay(&names[1], hot, ex, ey)));
        let offset = k as f64 * SWEEP_NEXT_S;
        let u = phase(now, SWEEP_S, offset) * SWEEP_S / SWEEP_RUN_S;
        let (mut sx, mut sy, mut swept) = (0.0, 0.0, None);
        if u < 1.0 {
            let angle = (start + sweep * frac * ease(u)).to_radians();
            (sx, sy) = (cx + rx * angle.cos(), cy + ry * angle.sin());
            let run_parts = [spot(0.7 * th, colour, 0.6, 1.0)];
            swept = self.sprite(&run_parts, Self::strength(w, 0.9 * hump(u)));
            r.moving = true;
        } else {
            r.due_at(now + (1.0 - phase(now, SWEEP_S, offset)) * SWEEP_S);
        }
        out.push(("sweep", self.lay(&names[2], swept, sx, sy)));
        Self::rest_until(
            r,
            |at| {
                (
                    Self::strength(w, breath(at)),
                    if heat > 0.0 {
                        Self::strength(w, heat * breath(at))
                    } else {
                        0
                    },
                )
            },
            now,
        );
        Ok(out)
    }

    // -- graphs ------------------------------------------------------------------------------

    fn beat(
        &mut self,
        r: &mut Renderer,
        w: &Dict,
        snapshot: &Snapshot,
        now: f64,
    ) -> Result<Lights> {
        let name = format!("{}\0beat", widget::text(w, "id"));
        let end = r.graph_end(w, snapshot)?;
        let colour = r.colour_of(w, "color")?;
        let (Some(end), Some(colour)) = (end, colour) else {
            return Ok(vec![("beat", self.none(&name))]);
        };
        let lw = widget::number(w, "line_width", 2.0).max(1.0);
        let piece = self.beating(r, &name, rgb(colour), lw, end, w, now)?;
        Ok(vec![("beat", piece)])
    }

    /// _beating: a curve's end at `end` beating once a reading.
    #[allow(clippy::too_many_arguments)]
    fn beating(
        &mut self,
        r: &mut Renderer,
        name: &str,
        colour: [i64; 3],
        lw: f64,
        end: (f64, f64),
        w: &Dict,
        now: f64,
    ) -> Result<Option<Rc<Piece>>> {
        // (the theme's refresh_ms / 1000.0, as Python divides an int of any size)
        let refresh =
            lp_py::num::to_float(&r.theme.refresh_ms).map_err(|e| crate::piece::Failed(e.msg))?;
        let period = (refresh / 1000.0).max(0.25);
        let run = BEAT_RUN_S.min(0.8 * period);
        let step = |at: f64| {
            let b = phase(at, period, 0.0) * period / run;
            if b < 1.0 {
                BEAT_STEPS.min((b * BEAT_STEPS as f64).floor() as i64)
            } else {
                BEAT_STEPS
            }
        };
        let s = step(now);
        let bq = s as f64 / BEAT_STEPS as f64;
        let mut parts = vec![
            spot(2.5 * lw, colour, 1.0, 1.0),
            spot(8.0 * lw, colour, 0.0, 0.6 * (0.55 + 0.45 * (1.0 - bq))),
        ];
        if s < BEAT_STEPS {
            let fade = (1.0 - bq) * (1.0 - bq);
            parts.push(ring(
                2.0 * lw + 11.0 * lw * ease(bq),
                colour,
                1.2 * lw,
                0.8 * fade,
            ));
        }
        let sprite = self.sprite(&parts, Self::strength(w, 1.0));
        Self::rest_until(r, step, now);
        Ok(self.lay(name, sprite, end.0, end.1))
    }

    // -- music -------------------------------------------------------------------------------

    /// _playing: how far the light of something that plays has come (0 to 1).
    fn playing(&mut self, r: &mut Renderer, key: &str, snapshot: &Snapshot, now: f64) -> f64 {
        let on = is_playing(snapshot);
        let Some(&was) = self.fades.get(key) else {
            self.fades.insert(key.to_string(), (on, now - FADE_S));
            return if on { 1.0 } else { 0.0 };
        };
        let mut was = was;
        if was.0 != on {
            let gone = FADE_S.min(now - was.1);
            was = (on, now - (FADE_S - gone));
            self.fades.insert(key.to_string(), was);
        }
        let u = ease((now - was.1) / FADE_S);
        if u < 1.0 {
            r.moving = true;
        }
        if on {
            u
        } else {
            1.0 - u
        }
    }

    fn cover(
        &mut self,
        r: &mut Renderer,
        w: &Dict,
        piece: &Rc<Piece>,
        snapshot: &Snapshot,
        now: f64,
    ) -> Result<Lights> {
        const NAMES: [&str; 4] = ["glow0", "glow1", "glow2", "glow3"];
        let wid = widget::text(w, "id").to_string();
        let names: Vec<String> = NAMES.iter().map(|n| format!("{wid}\0{n}")).collect();
        let share = self.playing(r, &wid, snapshot, now);
        let (x, y) = (widget::int(w, "x", 0), widget::int(w, "y", 0));
        let (gw, gh) = (widget::int(w, "w", 100), widget::int(w, "h", 100));
        let layer = &piece.layer;
        let bx = [x - piece.x, y - piece.y, x - piece.x + gw, y - piece.y + gh];
        let fits = bx[0] >= 0
            && bx[1] >= 0
            && bx[2] <= layer.width() as i64
            && bx[3] <= layer.height() as i64;
        if share <= 0.0 || gw.min(gh) < 16 || piece.left_out.is_some() || !fits {
            return Ok((0..4).map(|i| (NAMES[i], self.none(&names[i]))).collect());
        }
        let s = Self::strength(w, share * COVER_SHARE);
        let radius = widget::number(w, "radius", 0.0);
        let glows = self.glows(piece, bx, s, radius);
        let mut out = Vec::with_capacity(4);
        for (i, (sprite, dx, dy)) in glows.iter().enumerate() {
            match sprite {
                None => out.push((NAMES[i], self.none(&names[i]))),
                Some(sprite) => {
                    let placed = self.place(&names[i], Rc::clone(sprite), x + dx, y + dy, true);
                    out.push((NAMES[i], Some(placed)));
                }
            }
        }
        Ok(out)
    }

    /// _glows: the glow round the cover in `bx` of the piece's layer at `strength`: made at
    /// full strength once for the cover's picture, the strength asked last scaled from it.
    fn glows(&mut self, piece: &Rc<Piece>, bx: [i64; 4], strength: i64, radius: f64) -> Rc<Bands> {
        if strength <= 0 {
            return Rc::new(vec![(None, 0, 0); 4]);
        }
        let kept = self.covers.get_or_insert_with(|| Kept::new(SPRITE_BYTES));
        let key = (Rc::as_ptr(piece) as usize, bx, radius.to_bits());
        let glow = match kept.get(&key) {
            Some(had) if Rc::ptr_eq(&had.piece, piece) => had,
            _ => {
                let layer = &piece.layer;
                let whole = bx == [0, 0, layer.width() as i64, layer.height() as i64];
                let cover = if whole {
                    layer.clone()
                } else {
                    layer.crop(bx[0], bx[1], bx[2], bx[3])
                };
                let full: Bands = cover_glow(&cover, LEVELS, radius)
                    .into_iter()
                    .map(|(image, x, y)| (image.map(Rc::new), x, y))
                    .collect();
                let size: usize = full
                    .iter()
                    .map(|(b, _, _)| b.as_ref().map_or(0, |b| b.width() * b.height() * 4))
                    .sum();
                let glow = Rc::new(CoverGlow {
                    piece: Rc::clone(piece),
                    last: RefCell::new((LEVELS, Rc::new(full.clone()))),
                    full,
                });
                kept.put(key, Rc::clone(&glow), 2 * size);
                glow
            }
        };
        let mut last = glow.last.borrow_mut();
        if last.0 != strength {
            let bands = glow.full.iter().map(|(band, x, y)| {
                let band = band.as_ref().and_then(|b| scale(b, strength).map(Rc::new));
                (band, *x, *y)
            });
            *last = (strength, Rc::new(bands.collect()));
        }
        Rc::clone(&last.1)
    }

    fn dance(
        &mut self,
        r: &mut Renderer,
        w: &Dict,
        piece: &Rc<Piece>,
        snapshot: &Snapshot,
        now: f64,
    ) -> Result<Lights> {
        let wid = widget::text(w, "id").to_string();
        let name = format!("{wid}\0dance");
        let share = self.playing(r, &name, snapshot, now);
        let colour = r.colour_of(w, "color")?;
        let (Some(bx), Some(colour)) = (piece.hit, colour) else {
            return Ok(vec![("dance", self.none(&name))]);
        };
        if share <= 0.0 {
            return Ok(vec![("dance", self.none(&name))]);
        }
        let size = widget::number(w, "font_size", 20.0);
        let tick = (now * DANCE_HZ).floor() / DANCE_HZ;
        let bars = dance(tick, size, share);
        let colour = rgb(colour);
        let kept = self.sprites.get_or_insert_with(|| Kept::new(SPRITE_BYTES));
        let mut key = vec![u64::MAX, size.to_bits()];
        key.extend(bars.iter().map(|b| *b as u64));
        key.extend(colour.iter().map(|c| *c as u64));
        let sprite = match kept.get(&key) {
            Some(hit) => hit,
            None => {
                let made = Rc::new(dance_bars(&bars, size, colour));
                kept.put(
                    key,
                    Rc::clone(&made),
                    made.width() * made.height() * 4 + 128,
                );
                made
            }
        };
        let x = bx[0] + bx[2] + round_half_up(size * 0.45);
        let y = bx[1] + bx[3] - sprite.height() as i64;
        r.due_at(((now * DANCE_HZ).floor() + 1.0) / DANCE_HZ);
        Ok(vec![(
            "dance",
            Some(self.place(&name, sprite, x, y, false)),
        )])
    }

    fn progress(
        &mut self,
        r: &mut Renderer,
        w: &Dict,
        snapshot: &Snapshot,
        now: f64,
    ) -> Result<Lights> {
        let wid = widget::text(w, "id").to_string();
        let names = [format!("{wid}\0head"), format!("{wid}\0sheen")];
        let share = self.playing(r, &wid, snapshot, now);
        let (value, frac) = r.bar_fraction(w, snapshot)?;
        let direction = match w.get("direction") {
            Some(Value::Str(d)) => d.as_str(),
            _ => "right",
        };
        if share <= 0.0 || value.is_none() || direction != "right" {
            return Ok(vec![
                ("head", self.none(&names[0])),
                ("sheen", self.none(&names[1])),
            ]);
        }
        let (x, y) = (widget::int(w, "x", 0) as f64, widget::int(w, "y", 0) as f64);
        let (bw, bh) = (widget::int(w, "w", 200), widget::int(w, "h", 12));
        let rules = crate::metric::rules_of(w).unwrap_or_default();
        let (start, end) = r.rule_paint(w, value, &rules)?;
        let colour = rgb(end.unwrap_or(start));
        let th = bh.max(2) as f64;
        let (hx, hy) = (x + frac * bw as f64 - 0.5, y + bh as f64 / 2.0);
        let head_parts = [
            spot(0.75 * th, colour, 0.9, 1.0),
            spot(2.0 * th, colour, 0.0, 0.5),
        ];
        let strength =
            |at: f64| Self::strength(w, share * (0.85 + 0.15 * wave(phase(at, 1.0, 0.0))));
        let head = self.sprite(&head_parts, strength(now));
        let mut out = vec![("head", self.lay(&names[0], head, hx, hy))];
        let u = phase(now, SHEEN_S, 0.0) * SHEEN_S / SHEEN_RUN_S;
        let (mut sx, mut sheen) = (0.0, None);
        if u < 1.0 && frac * bw as f64 >= 2.0 * th {
            sx = x + (hx - x) * ease(u);
            sheen = self.sprite(
                &[spot(1.4 * th, colour, 0.3, 0.9)],
                Self::strength(w, share * hump(u)),
            );
            r.moving = true;
        } else {
            r.due_at(now + (1.0 - phase(now, SHEEN_S, 0.0)) * SHEEN_S);
        }
        out.push(("sheen", self.lay(&names[1], sheen, sx, hy)));
        Self::rest_until(r, strength, now);
        Ok(out)
    }

    // -- downloads ---------------------------------------------------------------------------

    fn download(
        &mut self,
        r: &mut Renderer,
        w: &Dict,
        snapshot: &Snapshot,
        now: f64,
    ) -> Result<Lights> {
        use crate::download;
        let wid = widget::text(w, "id").to_string();
        let names = [
            format!("{wid}\0head"),
            format!("{wid}\0sheen"),
            format!("{wid}\0sheen2"),
            format!("{wid}\0spark"),
        ];
        let state = match snapshot.state(widget::text(w, "state")) {
            Ok(Some(Value::Dict(state))) => Some(state),
            _ => None,
        };
        let facts = match &state {
            Some(state) => download::read(state).ok(),
            None => None,
        };
        let running = facts
            .as_ref()
            .and_then(|f| (f.phase == "downloading").then_some(f.share).flatten());
        let (Some(facts), Some(share), Some(state)) = (&facts, running, &state) else {
            self.runs.remove(&wid);
            return Ok(vec![
                ("head", self.none(&names[0])),
                ("sheen", self.none(&names[1])),
                ("sheen2", self.none(&names[2])),
                ("spark", self.none(&names[3])),
            ]);
        };
        let lay = download::layout(widget::int(w, "w", 596), widget::int(w, "h", 130));
        let (x0, y0, x1, y1) = download::rail(&lay);
        let length = 2.max(lp_py::round(x1 - x0) as i64);
        let thick = 2.max(lp_py::round(y1 - y0) as i64);
        let frac = lp_py::round(share * length as f64) / length as f64;
        let bx = (widget::int(w, "x", 0) + lp_py::round(x0) as i64) as f64;
        let by = (widget::int(w, "y", 0) + lp_py::round(y0) as i64) as f64 + thick as f64 / 2.0;
        let hx = bx + frac * length as f64 - 0.5;
        let colour = rgb(r.download_accent(w, &facts.phase)?);
        let th = thick as f64;
        let head_parts = [
            spot(0.8 * th, colour, 0.9, 1.0),
            spot(2.4 * th, colour, 0.0, 0.5),
        ];
        let head = self.sprite(
            &head_parts,
            Self::strength(w, 0.7 + 0.3 * wave(phase(now, 0.5, 0.0))),
        );
        let mut out = vec![("head", self.lay(&names[0], head, hx, by))];
        // (float() of a number that is finite, a bool too; one past a float's range raises)
        let rate = match state.get("rate") {
            Some(Value::Bool(on)) => f64::from(u8::from(*on)),
            Some(Value::Int(n)) => match num_traits_f64(n) {
                Some(f) => f,
                None => {
                    return Err(crate::piece::Failed(
                        "int too large to convert to float".into(),
                    ))
                }
            },
            Some(Value::Float(f)) if f.is_finite() => *f,
            _ => 0.0,
        };
        let pace = clamp01((rate.max(1e5) / 1e5).log10() / 3.0);
        let period = 1.8 - 1.2 * pace;
        let at = match self.runs.get(&wid) {
            Some(&(when, where_)) if !(now < when || now - when > 1.0) => {
                let at = where_ + (now - when) / period;
                at - at.floor()
            }
            _ => phase(now, period, 0.0),
        };
        self.runs.insert(wid.clone(), (now, at));
        for (i, name) in names[1..3].iter().enumerate() {
            let u = at + 0.5 * i as f64;
            let u = u - u.floor();
            let sprite = if frac * length as f64 >= 2.0 * th {
                self.sprite(
                    &[spot(1.6 * th, colour, 0.25, 0.8)],
                    Self::strength(w, hump(u)),
                )
            } else {
                None
            };
            let label = if i == 0 { "sheen" } else { "sheen2" };
            out.push((label, self.lay(name, sprite, bx + (hx - bx) * u, by)));
        }
        let spark = match lay.graph {
            Some(graph) if facts.history.len() >= 2 => spark_of(graph, &facts.history),
            _ => None,
        };
        let spark = match spark {
            None => self.none(&names[3]),
            Some((sx, sy)) => {
                let at = (
                    widget::int(w, "x", 0) as f64 + sx,
                    widget::int(w, "y", 0) as f64 + sy,
                );
                self.beating(r, &names[3], colour, 2.0 * lay.g, at, w, now)?
            }
        };
        out.push(("spark", spark));
        r.moving = true;
        Ok(out)
    }

    // -- the sun -----------------------------------------------------------------------------

    fn sun(&mut self, r: &mut Renderer, w: &Dict, snapshot: &Snapshot, now: f64) -> Result<Lights> {
        use crate::sunpath;
        let wid = widget::text(w, "id").to_string();
        let names = [format!("{wid}\0body"), format!("{wid}\0glint")];
        let at = sunpath::step(&snapshot.value(widget::text(w, "sensor")))?;
        let up = snapshot.value("sun.up");
        if at.is_none() && up == Value::None {
            return Ok(vec![
                ("body", self.none(&names[0])),
                ("glint", self.none(&names[1])),
            ]);
        }
        let at = at
            .or_else(|| (crate::snapshot::number(&up) == Some(1.0)).then_some(sunpath::STEPS / 2));
        let (sw, sh) = (
            widget::int(w, "w", 160).max(8),
            widget::int(w, "h", 40).max(6),
        );
        let way = sunpath::Way::new(sw, sh, widget::number(w, "thickness", 2.0).max(1.0));
        let (x, y) = (widget::int(w, "x", 0) as f64, widget::int(w, "y", 0) as f64);
        let (colour, (px, py), radius, low, high) = match at {
            None => (
                rgb(r.colour_of(w, "night")?.unwrap_or([201, 211, 222, 255])),
                way.at(0.5),
                way.sun * 1.05,
                0.4,
                0.7,
            ),
            Some(step) => (
                rgb(r.colour_of(w, "color")?.unwrap_or([245, 196, 107, 255])),
                way.at(step as f64 / sunpath::STEPS as f64),
                way.sun,
                0.6,
                1.0,
            ),
        };
        let parts = [
            spot(2.2 * radius, colour, 0.0, 0.55),
            spot(0.75 * radius, colour, 0.6, 0.6),
        ];
        let strength =
            |t: f64| Self::strength(w, low + (high - low) * wave(phase(t, BREATH_S, 0.0)));
        let body = self.sprite(&parts, strength(now));
        let mut out = vec![("body", self.lay(&names[0], body, x + px, y + py))];
        let u = phase(now, GLINT_S, GLINT_AFTER_S) * GLINT_S / GLINT_RUN_S;
        let (mut gx, mut gy, mut glint) = (0.0, 0.0, None);
        match at {
            Some(step) if step > 0 && u < 1.0 => {
                (gx, gy) = way.at(step as f64 / sunpath::STEPS as f64 * ease(u));
                let white = lerp3(colour, [255, 255, 255], 0.6);
                glint = self.sprite(
                    &[spot(0.9 * radius, white, 0.4, 1.0)],
                    Self::strength(w, 0.8 * hump(u)),
                );
                r.moving = true;
            }
            Some(step) if step > 0 => {
                r.due_at(now + (1.0 - phase(now, GLINT_S, GLINT_AFTER_S)) * GLINT_S);
            }
            _ => {}
        }
        out.push(("glint", self.lay(&names[1], glint, x + gx, y + gy)));
        Self::rest_until(r, strength, now);
        Ok(out)
    }
}

/// A whole number as a float, as Python's float() of an int takes it (None: past a float's).
fn num_traits_f64(n: &num_bigint::BigInt) -> Option<f64> {
    use num_traits::ToPrimitive;
    n.to_f64().filter(|f| f.is_finite())
}

/// is_playing: whether something plays: `media.state` says "Playing" (else `media.playing`
/// is 1) (services/readings.py's).
fn is_playing(snapshot: &Snapshot) -> bool {
    match snapshot.value("media.state") {
        Value::Str(state) => lp_py::lower(&state) == "playing",
        _ => snapshot.value("media.playing").truthy(),
    }
}

/// _spark: where the download's speed curve ends (px from the widget's corner), as the card
/// draws it.
fn spark_of(graph: (f64, f64, f64, f64), history: &[f64]) -> Option<(f64, f64)> {
    let (x0, y0, x1, y1) = graph;
    let gw = 1.max(lp_py::round(x1 - x0) as i64);
    let gh = 1.max(lp_py::round(y1 - y0) as i64);
    if gw < 4 || gh < 4 {
        return None;
    }
    let step = (gw as f64 / (crate::download::HISTORY - 1) as f64).max(1.0);
    let count = 2.max((gw as f64 / step) as i64 + 1) as usize;
    let shown = &history[history.len().saturating_sub(count)..];
    let most = shown
        .iter()
        .copied()
        .fold(f64::NEG_INFINITY, |a, b| if b > a { b } else { a });
    let peak = crate::download::MB.max(most * 1.1);
    let (a, b) = (shown[shown.len() - 2], shown[shown.len() - 1]);
    let last = 0.25 * a + 0.5 * b + 0.25 * b;
    let y = (gh - 1) as f64 - clamp01(last / peak) * (gh - 1) as f64;
    Some((lp_py::round(x0) + gw as f64 - 1.0, lp_py::round(y0) + y))
}

// -- the cover's glow and the dancing bars ----------------------------------------------------

/// scaled: a band of light at full strength at `strength` of LEVELS: each byte times the
/// strength over LEVELS, to the nearest (whole numbers: Python's the same); None where none is
/// left.
fn scale(band: &Image, strength: i64) -> Option<Image> {
    if strength >= LEVELS {
        return Some(band.clone());
    }
    let (s, l) = (2 * strength.max(0) as u32, LEVELS as u32);
    let data: Vec<u8> = band
        .as_bytes()
        .iter()
        .map(|&v| ((u32::from(v) * s + l) / (2 * l)) as u8)
        .collect();
    if data.iter().all(|v| *v == 0) {
        return None;
    }
    Image::from_bytes(Mode::RGB, band.width(), band.height(), data).ok()
}

/// _soften: a grid (rows, columns, 3) softened once along each axis by (1, 2, 1) / 4, its
/// edges repeated.
fn soften(grid: &[[f64; 3]], gh: usize, gw: usize) -> Vec<[f64; 3]> {
    let mut rows = vec![[0.0; 3]; gh * gw];
    for j in 0..gh {
        let (up, down) = (j.saturating_sub(1), (j + 1).min(gh - 1));
        for i in 0..gw {
            for ch in 0..3 {
                rows[j * gw + i][ch] =
                    (grid[up * gw + i][ch] + 2.0 * grid[j * gw + i][ch] + grid[down * gw + i][ch])
                        / 4.0;
            }
        }
    }
    let mut out = vec![[0.0; 3]; gh * gw];
    for j in 0..gh {
        for i in 0..gw {
            let (left, right) = (i.saturating_sub(1), (i + 1).min(gw - 1));
            for ch in 0..3 {
                out[j * gw + i][ch] = (rows[j * gw + left][ch]
                    + 2.0 * rows[j * gw + i][ch]
                    + rows[j * gw + right][ch])
                    / 4.0;
            }
        }
    }
    out
}

/// cover_glow: the glow round a cover (RGBA, its corners rounded by `radius`) in its own
/// colours at `strength` of LEVELS: in four bands round it (above and below with the rows of
/// its rounded corners, left and right between), each with where its corner lies from the
/// cover's (RGB, light to add; None: a band with no light).
pub fn cover_glow(layer: &Image, strength: i64, radius: f64) -> Vec<(Option<Image>, i64, i64)> {
    let (w, h) = (layer.width() as i64, layer.height() as i64);
    let side = w.min(h);
    let m = 4.max(round_half_up(0.23 * side as f64));
    let r = radius.max(0.0).min(w as f64 / 2.0).min(h as f64 / 2.0);
    let ri = r.ceil() as i64;
    let block = 1.max(round_half_up(side as f64 / 12.0));
    let rgb = layer.convert(Mode::RGB).unwrap_or_else(|_| layer.clone());
    let (gw, gh) = (
        ((w + block - 1) / block) as usize,
        ((h + block - 1) / block) as usize,
    );
    let mut grid = vec![[0.0f64; 3]; gw * gh];
    for j in 0..gh {
        let (ya, yb) = (j as i64 * block, ((j as i64 + 1) * block).min(h));
        for i in 0..gw {
            let (xa, xb) = (i as i64 * block, ((i as i64 + 1) * block).min(w));
            let count = (yb - ya) * (xb - xa);
            let mut sums = [0i64; 3];
            for yy in ya..yb {
                let row = rgb.row(yy as usize);
                for xx in xa..xb {
                    for ch in 0..3 {
                        sums[ch] += i64::from(row[xx as usize * 3 + ch]);
                    }
                }
            }
            for ch in 0..3 {
                grid[j * gw + i][ch] = ((sums[ch] * 2 + count).div_euclid(2 * count)) as f64;
            }
        }
    }
    let grid = soften(&soften(&grid, gh, gw), gh, gw);
    let (big_w, big_h) = (w + 2 * m, h + 2 * m);
    let gain = COVER_GAIN;
    let (mf, wf, hf) = (m as f64, w as f64, h as f64);
    let mut pixels = vec![0u8; (big_w * big_h * 3) as usize];
    let axis = |v: f64, size: f64, cells: usize| -> (f64, usize, usize, f64) {
        // the distance outside along this axis; the grid's cell and the share of the next
        let q = (mf + r - v).max(0.0).max(v - (mf + size - 1.0 - r));
        let s = (v - mf).max(0.0).min(size - 1.0);
        let g = ((s + 0.5) / block as f64 - 0.5)
            .max(0.0)
            .min(cells as f64 - 1.0);
        let i0 = g.floor();
        (q, i0 as usize, ((i0 as usize) + 1).min(cells - 1), g - i0)
    };
    for yy in 0..big_h {
        let (qy, iy0, iy1, fy) = axis(yy as f64, hf, gh);
        for xx in 0..big_w {
            let (qx, ix0, ix1, fx) = axis(xx as f64, wf, gw);
            let d = (qy * qy + qx * qx).sqrt() - r;
            let t = (1.0 - d / mf).max(0.0);
            let fall = if d > 0.0 { 0.5 * (t * t) } else { 0.0 };
            let at = ((yy * big_w + xx) * 3) as usize;
            for ch in 0..3 {
                let c00 = grid[iy0 * gw + ix0][ch];
                let c01 = grid[iy0 * gw + ix1][ch];
                let c10 = grid[iy1 * gw + ix0][ch];
                let c11 = grid[iy1 * gw + ix1][ch];
                let top = c00 + (c01 - c00) * fx;
                let bottom = c10 + (c11 - c10) * fx;
                let v = (top + (bottom - top) * fy) * fall * gain;
                pixels[at + ch] = (v.min(255.0) + 0.5).floor() as u8;
            }
        }
    }
    let band = |x0: i64, y0: i64, x1: i64, y1: i64| -> Option<Image> {
        if x1 <= x0 || y1 <= y0 {
            return None;
        }
        let mut data = Vec::with_capacity(((x1 - x0) * (y1 - y0) * 3) as usize);
        for yy in y0..y1 {
            let at = ((yy * big_w + x0) * 3) as usize;
            data.extend_from_slice(&pixels[at..at + ((x1 - x0) * 3) as usize]);
        }
        if data.iter().all(|v| *v == 0) {
            return None;
        }
        Image::from_bytes(Mode::RGB, (x1 - x0) as usize, (y1 - y0) as usize, data).ok()
    };
    // (made at full strength, scaled to the one asked)
    [
        (band(0, 0, big_w, (m + ri).min(big_h)), -m, -m),
        (band(0, (m + h - ri).max(0), big_w, big_h), -m, h - ri),
        (band(0, m + ri, m, m + h - ri), -m, ri),
        (band(m + w, m + ri, big_w, m + h - ri), w, ri),
    ]
    .into_iter()
    .map(|(b, x, y)| (b.and_then(|b| scale(&b, strength)), x, y))
    .collect()
}

/// The bars after "Playing": each one's cycle (s) and where it starts.
const DANCE: [(f64, f64); 3] = [(0.52, 0.0), (0.41, 0.13), (0.67, 0.29)];

/// dance: the heights of the three bars after "Playing" at `at`, for a text of `size`.
pub fn dance(at: f64, size: f64, share: f64) -> Vec<i64> {
    let low = 2.max(round_half_up(size * 0.2));
    let high = 4.max(round_half_up(size * 0.72));
    DANCE
        .iter()
        .map(|(period, offset)| {
            low + round_half_up((high - low) as f64 * share * wave(phase(at, *period, *offset)))
        })
        .collect()
}

/// dance_bars: the bars of `bars` heights side by side, standing on the bottom edge (RGBA).
pub fn dance_bars(bars: &[i64], size: f64, colour: [i64; 3]) -> Image {
    let width = 2.max(round_half_up(size * 0.16)) as usize;
    let gap = 1.max(round_half_up(size * 0.13)) as usize;
    let high = 4.max(round_half_up(size * 0.72)) as usize;
    let n = bars.len();
    let iw = n * width + n.saturating_sub(1) * gap;
    let mut data = vec![0u8; iw * high * 4];
    let ink = [colour[0] as u8, colour[1] as u8, colour[2] as u8, 255];
    for (i, bar) in bars.iter().enumerate() {
        let x = i * (width + gap);
        let from = high.saturating_sub((*bar).max(0) as usize);
        for row in from..high {
            for col in x..x + width {
                data[(row * iw + col) * 4..(row * iw + col) * 4 + 4].copy_from_slice(&ink);
            }
        }
    }
    Image::from_bytes(Mode::RGBA, iw, high, data).expect("the bars' size")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_breath_and_the_steps_of_light() {
        assert_eq!(wave(0.0), 0.0);
        assert_eq!(wave(0.5), 1.0);
        assert_eq!(level(1.0), LEVELS);
        assert_eq!(level(-1.0), 0);
        assert_eq!(phase(5.0, 4.0, 0.0), 0.25);
        assert_eq!(hump(0.5), 1.0);
    }

    #[test]
    fn the_next_step_is_found_within_the_look_ahead() {
        let at = next_step(|t: f64| (t * 4.0).floor() as i64, 0.1).expect("a step");
        assert!((at - 0.25).abs() < 0.001, "{at}");
        assert_eq!(next_step(|_t: f64| 1, 0.0), None);
    }

    #[test]
    fn a_light_is_brightest_in_its_middle_and_black_at_its_edge() {
        let image = make(&[spot(4.0, [100, 200, 50], 0.5, 1.0)], LEVELS);
        let half = image.width() / 2;
        let middle = &image.row(half)[half * 3..half * 3 + 3];
        let edge = &image.row(0)[0..3];
        assert!(middle[1] > 200);
        assert_eq!(edge, &[0, 0, 0]);
    }
}

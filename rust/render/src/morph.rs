//! The morph's parts (render/morph.py): what each widget shows, so that the same thing is found
//! on the next screen; a frame as it was composed (Renderer::parting) taken apart into the frame
//! without what flies and each thing that flies, alone; and a thing laid where its flight has
//! got to. The same steps as Python's, in the same order: the same pixels.

use std::borrow::Cow;
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::rc::Rc;
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Instant;

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
pub fn laid_again(parting: &Parting, gone: &HashSet<usize>) -> Vec<(i64, i64, i64, i64)> {
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
    // (a fade the levels of its alpha and of its light, in place: as point() gives them; the
    // part itself where it is laid as it is)
    let fade: Option<Vec<u8>> = (alpha < 1.0).then(|| {
        faded(alpha)
            .iter()
            .map(|&v| v.clamp(0, 255) as u8)
            .collect()
    });
    // (only what lies on `out` made of it: a window of a resize is the resize cut to it)
    let (sw, sh) = (size.0 as i64, size.1 as i64);
    let (ow, oh) = (out.width() as i64, out.height() as i64);
    let window = (
        (-x).clamp(0, sw),
        (-y).clamp(0, sh),
        (ow - x).clamp(0, sw),
        (oh - y).clamp(0, sh),
    );
    if window.0 >= window.2 || window.1 >= window.3 {
        return Ok(()); // (none of it on `out`)
    }
    let (mut body, at) = sized(&part.body, size, window, (x, y))?;
    if let Some(fade) = &fade {
        let body = body.to_mut();
        if body.mode() == Mode::RGBA {
            for px in body.as_bytes_mut().chunks_exact_mut(4) {
                px[3] = fade[usize::from(px[3])];
            }
        } else {
            let a = body.getchannel(3).map_err(failed)?;
            let a = a.point(&faded(alpha)).map_err(failed)?;
            body.putalpha(&a).map_err(failed)?;
        }
    }
    out.paste(&body, at.0, at.1, Some(&body)).map_err(failed)?;
    if let Some(light) = &part.light {
        let (mut light, at) = sized(light, size, window, (x, y))?;
        if let Some(fade) = &fade {
            for v in light.to_mut().as_bytes_mut() {
                *v = fade[usize::from(*v)];
            }
        }
        compose::add_light(out, &light, at.0, at.1)?;
    }
    Ok(())
}

/// `image` at `size` (bicubic), made only within `window` of it where that is not all of it:
/// the picture and where its top left corner goes, `at` being the whole one's.
fn sized(
    image: &Image,
    size: (usize, usize),
    window: (i64, i64, i64, i64),
    at: (i64, i64),
) -> Result<(Cow<'_, Image>, (i64, i64))> {
    if size == image.size() {
        return Ok((Cow::Borrowed(image), at));
    }
    let (x0, y0, x1, y1) = window;
    let made = if window == (0, 0, size.0 as i64, size.1 as i64) {
        image.resize(size.0, size.1, Filter::Bicubic, None, None)
    } else {
        let window = (x0 as usize, y0 as usize, x1 as usize, y1 as usize);
        image.resize_window(size.0, size.1, Filter::Bicubic, window)
    };
    Ok((Cow::Owned(made.map_err(failed)?), (at.0 + x0, at.1 + y0)))
}

// -- over all the panels as one (render/canvas.py): what one shows flies to where another does --

/// BOARD_KEEP_S: a switch's screens are forgotten this long after they were first told.
const BOARD_KEEP_S: f64 = 60.0;

/// Seen: a panel's part in a switch over all the panels: where it stands (millimetres, the
/// canvas's own), its frames' size and what its old screen and its new one show (things, in its
/// own pixels).
pub struct Seen {
    pub own: crate::canvas::Rect,
    pub size: (usize, usize),
    pub old: HashMap<String, Found>,
    pub new: HashMap<String, Found>,
}

impl Seen {
    /// mm: a box of this panel's (x0, y0, x1, y1, pixels) on all the panels: x, y, w, h in
    /// millimetres.
    pub fn mm(&self, b: (i64, i64, i64, i64)) -> crate::canvas::Rect {
        let sx = self.size.0 as f64 / self.own.2;
        let sy = self.size.1 as f64 / self.own.3;
        (
            self.own.0 + b.0 as f64 / sx,
            self.own.1 + b.1 as f64 / sy,
            (b.2 - b.0) as f64 / sx,
            (b.3 - b.1) as f64 / sy,
        )
    }
}

/// A panel's parts of what flies, by thing and screen (false: the old one, true: the new one).
pub type Given = HashMap<(String, bool), Arc<Part>>;

/// _Switch: one switch's screens on the Board.
struct Switch {
    at: Instant,
    seen: BTreeMap<i64, Arc<Seen>>,
    parts: BTreeMap<i64, Arc<Given>>,
    done: BTreeSet<i64>,
}

/// Board: the panels' screens at one switch over all of them, told one another (each panel's
/// loop draws its own part, in its own thread): first what each panel's two screens show where
/// (Seen), then each panel's parts of what flies from it or to it (take_apart's, by thing and
/// screen), so that every panel lays every flight that crosses it. A switch is forgotten once
/// every panel has laid out its flights, or BOARD_KEEP_S after it was first told.
pub struct Board {
    switches: Mutex<HashMap<String, Switch>>,
}

/// BOARD: the app's one board, which all its panels share.
pub fn board() -> &'static Board {
    static BOARD: OnceLock<Board> = OnceLock::new();
    BOARD.get_or_init(|| Board {
        switches: Mutex::new(HashMap::new()),
    })
}

impl Board {
    fn switches(&self) -> std::sync::MutexGuard<'_, HashMap<String, Switch>> {
        self.switches
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    /// tell: panel `panel` says what its screens show at the switch `key`.
    pub fn tell(&self, key: &str, panel: i64, seen: Arc<Seen>) {
        let now = Instant::now();
        let mut switches = self.switches();
        switches.retain(|_, s| now.duration_since(s.at).as_secs_f64() <= BOARD_KEEP_S);
        let switch = switches.entry(key.to_string()).or_insert_with(|| Switch {
            at: now,
            seen: BTreeMap::new(),
            parts: BTreeMap::new(),
            done: BTreeSet::new(),
        });
        switch.seen.insert(panel, seen);
    }

    /// give: panel `panel`'s parts of what flies.
    pub fn give(&self, key: &str, panel: i64, parts: Given) {
        if let Some(switch) = self.switches().get_mut(key) {
            switch.parts.insert(panel, Arc::new(parts));
        }
    }

    /// done: panel `panel` has its flights; the switch goes once all `panels` have.
    pub fn done(&self, key: &str, panel: i64, panels: i64) {
        let mut switches = self.switches();
        if let Some(switch) = switches.get_mut(key) {
            switch.done.insert(panel);
            if switch.done.len() as i64 >= panels {
                switches.remove(key);
            }
        }
    }

    pub fn seen(&self, key: &str) -> BTreeMap<i64, Arc<Seen>> {
        self.switches()
            .get(key)
            .map(|s| s.seen.clone())
            .unwrap_or_default()
    }

    pub fn parts(&self, key: &str) -> BTreeMap<i64, Arc<Given>> {
        self.switches()
            .get(key)
            .map(|s| s.parts.clone())
            .unwrap_or_default()
    }
}

/// paired: what flies over all the panels: each thing (its key) from the panel whose old screen
/// shows it to the one whose new screen does; on its own panel where both of that panel's
/// screens show it, else from the one panel left that showed it to the one left that shows it
/// (none where more are left); the largest first (on the panels, in square millimetres), the
/// rest by key and panels.
pub fn paired(seen: &BTreeMap<i64, Arc<Seen>>) -> Vec<(String, i64, i64)> {
    let olds: BTreeSet<&String> = seen.values().flat_map(|s| s.old.keys()).collect();
    let news: BTreeSet<&String> = seen.values().flat_map(|s| s.new.keys()).collect();
    let mut flights = Vec::new();
    for &key in olds.intersection(&news) {
        let was: Vec<i64> = (seen.iter())
            .filter(|(_, s)| s.old.contains_key(key))
            .map(|(&i, _)| i)
            .collect();
        let comes: Vec<i64> = (seen.iter())
            .filter(|(_, s)| s.new.contains_key(key))
            .map(|(&i, _)| i)
            .collect();
        let both: Vec<i64> = was.iter().copied().filter(|i| comes.contains(i)).collect();
        flights.extend(both.iter().map(|&i| (key.clone(), i, i)));
        let left: Vec<i64> = was.iter().copied().filter(|i| !both.contains(i)).collect();
        let right: Vec<i64> = comes
            .iter()
            .copied()
            .filter(|i| !both.contains(i))
            .collect();
        if let ([from], [to]) = (left.as_slice(), right.as_slice()) {
            flights.push((key.clone(), *from, *to));
        }
    }
    let area = |f: &(String, i64, i64)| {
        let s = &seen[&f.1];
        let (_, _, w, h) = s.mm(s.old[&f.0].1);
        w * h
    };
    flights.sort_by(|a, b| {
        (area(b).partial_cmp(&area(a)))
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| a.cmp(b))
    });
    flights
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Things a screen shows, by key: each its box (x0, y0, x1, y1, pixels).
    type Shown<'a> = [(&'a str, (i64, i64, i64, i64))];

    /// A panel's Seen: where it stands, and its things' boxes (each of one piece).
    fn told(own: crate::canvas::Rect, old: &Shown, new: &Shown) -> Arc<Seen> {
        let found = |things: &Shown| {
            (things.iter())
                .map(|(k, b)| (k.to_string(), (vec![0], *b)))
                .collect()
        };
        Arc::new(Seen {
            own,
            size: (100, 50),
            old: found(old),
            new: found(new),
        })
    }

    #[test]
    fn over_all_the_panels_a_thing_stays_on_its_panel_or_flies_to_the_one_left() {
        let (a, b) = ((0.0, 0.0, 100.0, 50.0), (110.0, 0.0, 100.0, 50.0));
        let mut seen = BTreeMap::new();
        let old = [
            ("time", (0, 0, 40, 20)),
            ("picture:x", (0, 0, 50, 50)),
            ("ring:y", (0, 0, 9, 9)),
        ];
        seen.insert(0, told(a, &old, &[("time", (50, 0, 90, 20))]));
        let new = [
            ("picture:x", (10, 0, 60, 50)),
            ("ring:y", (1, 1, 9, 9)),
            ("number:z", (0, 0, 5, 5)),
        ];
        seen.insert(1, told(b, &[("ring:y", (0, 0, 9, 9))], &new));
        // (the largest first: the picture 50 x 50 mm, the time 40 x 20, the ring 9 x 9)
        let named = |f: &[(&str, i64, i64)]| -> Vec<(String, i64, i64)> {
            f.iter().map(|(k, i, j)| (k.to_string(), *i, *j)).collect()
        };
        assert_eq!(
            paired(&seen),
            named(&[("picture:x", 0, 1), ("time", 0, 0), ("ring:y", 1, 1)])
        );
        // (two panels left with it and one to come to: it stays where it is, with its panel)
        seen.insert(
            2,
            told(
                (220.0, 0.0, 100.0, 50.0),
                &[("picture:x", (0, 0, 10, 10))],
                &[],
            ),
        );
        assert!(!paired(&seen).contains(&("picture:x".to_string(), 0, 1)));
        assert_eq!(seen[&1].mm((10, 0, 60, 50)), (120.0, 0.0, 50.0, 50.0)); // (its pixels, mm here)
    }

    #[test]
    fn the_board_forgets_a_switch_once_every_panel_has_its_flights() {
        let own = (0.0, 0.0, 100.0, 50.0);
        let key = "the board forgets";
        board().give(key, 0, Given::new()); // (nothing told yet: nothing kept)
        assert!(board().parts(key).is_empty());
        board().tell(key, 0, told(own, &[], &[]));
        board().tell(key, 1, told(own, &[], &[]));
        assert_eq!(
            board().seen(key).keys().copied().collect::<Vec<_>>(),
            [0, 1]
        );
        board().give(key, 1, Given::new());
        assert_eq!(board().parts(key).keys().copied().collect::<Vec<_>>(), [1]);
        board().done(key, 0, 2);
        assert_eq!(board().seen(key).len(), 2); // (the other one not yet)
        board().done(key, 1, 2);
        assert!(board().seen(key).is_empty() && board().parts(key).is_empty());
    }

    #[test]
    fn a_part_laid_over_the_edge_is_the_part_laid_on_more_cut_to_the_frame() {
        // (only the window on the frame is made of it: the same pixels, its light too)
        let mut seed = 0x2545_f491_4f6c_dd1d_u64;
        let mut next = || {
            seed ^= seed << 13;
            seed ^= seed >> 7;
            seed ^= seed << 17;
            (seed >> 16) as u8
        };
        let mut body = Image::blank(Mode::RGBA, 37, 23);
        body.as_bytes_mut().iter_mut().for_each(|v| *v = next());
        let mut light = Image::blank(Mode::RGB, 37, 23);
        light
            .as_bytes_mut()
            .iter_mut()
            .for_each(|v| *v = next() / 4);
        let part = Part {
            bounds: (0, 0, 37, 23),
            body,
            light: Some(light),
        };
        let pad = 64;
        let mut ground = Image::blank(Mode::RGB, 64 + 2 * pad, 40 + 2 * pad);
        ground.as_bytes_mut().iter_mut().for_each(|v| *v = next());
        let cut =
            |image: &Image| image.crop(pad as i64, pad as i64, pad as i64 + 64, pad as i64 + 40);
        let frame = cut(&ground);
        for (middle, room, alpha) in [
            ((2.3, 5.0), (50.0, 31.0), 1.0),
            ((61.7, 38.2), (29.0, 40.0), 0.6),
            ((30.0, -3.4), (74.0, 47.0), 0.35),
            ((-9.0, 20.0), (37.0, 23.0), 0.8), // (its own size: laid as it is)
            ((-40.0, 20.0), (30.0, 30.0), 1.0), // (none of it on the frame)
        ] {
            let mut on = frame.clone();
            lay(&mut on, &part, middle, room, alpha).unwrap();
            let mut more = ground.clone();
            let moved = (middle.0 + pad as f64, middle.1 + pad as f64);
            lay(&mut more, &part, moved, room, alpha).unwrap();
            assert!(on == cut(&more), "{middle:?} {room:?} {alpha}");
            assert!(
                on != frame || middle.0 < 0.0 && room.0 == 30.0,
                "{middle:?}"
            );
        }
    }
}

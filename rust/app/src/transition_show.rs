//! A transition shown on the panel, to see it (transition_show.py; Settings → Transitions →
//! Show on the panel): the screen goes over to a card with the transition's name, stays a
//! moment, and comes back, with the transition both ways, at the speed set. Every panel shows
//! it at once (the panels beside the first run in this process and see the same request); a
//! calm panel just shows the card.

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;
use std::time::Instant;

use lp_pixels::Image;
use lp_render::transitions::Transition;
use lp_render::Renderer;

/// the card stays this long between the two ways
const HOLD_S: f64 = 1.2;
/// a request not taken up by then is forgotten
const GIVE_UP_S: f64 = 30.0;
/// A morph shown to see it turns the screen into another of Libre Panel's themes and back, as
/// a switch between two screens does (the card shows nothing the screen shows): Lumen's and
/// Arena's share the time, the rings and the readings, and each turns into the other.
pub const MORPH_PAIR: (&str, &str) = ("lumen", "arena");

/// morph_partner: the theme a morph shown to see it turns the screen `shown` into: Arena's
/// version of the same kind from Lumen's ("arena-pair" from "lumen-pair-1920x720"), Lumen's
/// from Arena's, Lumen from any other (its version for the panel: theme_for).
pub fn morph_partner(shown: &str, places: &lp_theme::versions::Places) -> String {
    let stem = lp_theme::versions::version_stem(shown);
    let (lumen, arena) = MORPH_PAIR;
    for (one, other) in [(lumen, arena), (arena, lumen)] {
        if stem == one || stem.starts_with(&format!("{one}-")) {
            let kind = format!("{other}{}", &stem[one.len()..]);
            return match lp_theme::versions::find_theme(&kind, places) {
                Ok(_) => kind,
                Err(_) => other.to_string(),
            };
        }
    }
    lumen.to_string()
}

/// The request on now: its number, the transition's name, its words for the card, when.
static ASKED: Mutex<Option<(u64, String, String, Instant)>> = Mutex::new(None);
static NUMBERS: AtomicU64 = AtomicU64::new(1);

/// show: show the transition `name` (its words for the card: `label`, else its name) on the
/// panels; the request's number.
pub fn show(name: &str, label: &str) -> u64 {
    let number = NUMBERS.fetch_add(1, Ordering::Relaxed);
    let label = if label.is_empty() { name } else { label };
    if let Ok(mut asked) = ASKED.lock() {
        *asked = Some((number, name.to_string(), label.to_string(), Instant::now()));
    }
    number
}

/// asked: the request on now, (number, name, label); None for none, or one asked too long ago.
pub fn asked() -> Option<(u64, String, String)> {
    let asked = ASKED.lock().ok()?;
    let (number, name, label, when) = asked.as_ref()?;
    if when.elapsed().as_secs_f64() > GIVE_UP_S {
        return None;
    }
    Some((*number, name.clone(), label.clone()))
}

/// a show over all the panels as one begins this long after it was asked for, on each
pub const LEAD_S: f64 = 0.4;

/// lead: how long from now a show over all the panels as one begins (each panel takes the
/// request in its own time): [`LEAD_S`] after it was asked for; 0 for one not asked.
pub fn lead(number: u64) -> f64 {
    let Ok(asked) = ASKED.lock() else {
        return 0.0;
    };
    match asked.as_ref() {
        Some((n, _, _, when)) if *n == number => (LEAD_S - when.elapsed().as_secs_f64()).max(0.0),
        _ => 0.0,
    }
}

/// done: the panel has shown request `number`: it ends (a newer one stays).
pub fn done(number: u64) {
    if let Ok(mut asked) = ASKED.lock() {
        if asked.as_ref().is_some_and(|(n, ..)| *n == number) {
            *asked = None;
        }
    }
}

/// A show on one panel: the frame it began on, held; the card; the transition each way (None
/// for a cut).
pub struct Show {
    pub number: u64,
    held: Image,
    card: Image,
    out: Option<Transition>,
    back: Option<Transition>,
    began: Option<f64>,
}

impl Show {
    pub fn new(
        number: u64,
        frame: &Image,
        card: Image,
        out: Option<Transition>,
        back: Option<Transition>,
    ) -> Show {
        Show {
            number,
            held: frame.clone(),
            card,
            out,
            back,
            began: None,
        }
    }

    /// Whether it goes over all the panels as one (render/canvas.py).
    pub fn on_canvas(&self) -> bool {
        self.out.as_ref().is_some_and(Transition::on_canvas)
    }

    /// It begins at `at` (on the loop's clock): every panel from the same moment.
    pub fn begin_at(&mut self, at: f64) {
        self.began = Some(at);
    }

    /// How long a way takes (seconds; 0 for a cut).
    fn duration(&self) -> f64 {
        self.out.as_ref().map_or(0.0, |out| out.duration)
    }

    /// What the panel shows at `now` while the show goes on (`live`: the theme's frame);
    /// None once it is over. An error where a transition failed.
    pub fn frame(
        &mut self,
        live: &Image,
        now: f64,
        renderer: &Renderer,
    ) -> Result<Option<Image>, String> {
        let began = *self.began.get_or_insert(now);
        let (s, d) = (now - began, self.duration());
        if self.card.size() != live.size() {
            return Ok(None);
        }
        // (the frames each way laid from are told apart by the show's number)
        let key = self.number.wrapping_mul(2);
        if s < d {
            let out = self.out.as_mut().expect("a way of some duration");
            let made = out.frame(&self.held, key, &self.card, s / d, renderer);
            return made.map(Some).map_err(|why| why.0);
        }
        if s < d + HOLD_S {
            return Ok(Some(self.card.clone()));
        }
        if d != 0.0 && s < 2.0 * d + HOLD_S {
            let Some(back) = self.back.as_mut() else {
                return Ok(None);
            };
            let t = (s - d - HOLD_S) / d;
            let made = back.frame(&self.card, key + 1, live, t, renderer);
            return made.map(Some).map_err(|why| why.0);
        }
        Ok(None)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_show_over_all_the_panels_begins_on_each_from_when_it_was_asked() {
        let number = show("wipe", "Wipe");
        let wait = lead(number);
        assert!((wait - LEAD_S).abs() < 0.05, "{wait}");
        assert_eq!(lead(number + 1), 0.0);
        done(number);
        assert_eq!(lead(number), 0.0);
    }

    #[test]
    fn a_morph_shown_turns_lumen_into_arena_and_any_other_into_lumen() {
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        let places = lp_theme::versions::Places {
            user: root.join("no-such-folder"),
            plugins: Vec::new(),
            builtin: root.join("src/libre_panel/themes"),
        };
        for (shown, partner) in [
            ("lumen-1920x720", "arena"),
            ("lumen", "arena"),
            ("lumen-pair-1920x720", "arena-pair"),
            ("arena-vertical", "lumen"),
            ("arena-companion-1920x720", "lumen-companion"),
            ("lumen-clock-640x180", "arena"),
            ("encore-1920x720", "lumen"),
            ("", "lumen"),
        ] {
            assert_eq!(morph_partner(shown, &places), partner, "{shown}");
        }
    }
}

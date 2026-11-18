//! The ways from one screen to the next, as Python draws them: render/overlays.py's cut, fade
//! and slide; render/transitions.py's tube and entrance, drawn with light; render/handovers.py's
//! wipe, iris, blinds, dissolve, the slides from each side, zoom, the line of light, the glitch,
//! the tiles, the shatter, the cascade, the comet and the portal. Each is made for a switch
//! with the new theme's colours and the
//! parameters a service passed (overlays.transition), and draws its frame of the old screen,
//! the new one and how far it has gone (0 to 1), what it keeps from frame to frame kept as
//! Python keeps it: a map or a strip of light for the frame's size, the logo or title with its
//! halo, the pieces of the old screen.

use std::borrow::Cow;

use lp_pixels::{chops, Filter, Image, Mode, Transpose};
use lp_py::random::Random;
use lp_py::{Dict, Value};

use crate::canvas::{
    alone, canvas_duration, canvas_of, centres, distance, in_out, in_out_sine, level, light_at,
    linear_map, middle, per_mm, pixel, radial_map, reach, slide, slide_way, span, threshold,
    way_of, wipe_map, Canvas,
};
use crate::light::{
    add_light, add_line, clamp, draw_tracked, ease_in, ease_out, face, mix, phase, py_max, py_min,
    role_colour, scaled, smooth, tracked_width, whole, Look,
};
use crate::piece::{Failed, Result};
use crate::renderer::Renderer;

fn failed(e: lp_pixels::Error) -> Failed {
    Failed(e.0)
}

/// round(x) of a float as an int (ValueError and OverflowError of NaN and infinity).
fn round_int(x: f64) -> Result<i64> {
    whole(x, f64::round_ties_even)
}

/// int(x) of a float: towards zero.
fn trunc_int(x: f64) -> Result<i64> {
    whole(x, f64::trunc)
}

/// math.ceil(x) of a float.
fn ceil_int(x: f64) -> Result<i64> {
    whole(x, f64::ceil)
}

/// math.floor(x) of a float.
fn floor_int(x: f64) -> Result<i64> {
    whole(x, f64::floor)
}

/// _rgb: the picture as RGB (itself where it is).
fn rgb(image: &Image) -> Result<Cow<'_, Image>> {
    if image.mode() == Mode::RGB {
        Ok(Cow::Borrowed(image))
    } else {
        image.convert(Mode::RGB).map(Cow::Owned).map_err(failed)
    }
}

/// A box of `image`, as Image.crop takes it.
fn crop(image: &Image, (x0, y0, x1, y1): (i64, i64, i64, i64)) -> Image {
    image.crop(x0, y0, x1, y1)
}

fn ink(colour: [i64; 3]) -> [u8; 4] {
    [colour[0], colour[1], colour[2], 255].map(|v| v.clamp(0, 255) as u8)
}

fn colour_of(colour: [i64; 3]) -> [u8; 3] {
    colour.map(|v| v.clamp(0, 255) as u8)
}

/// A transition as overlays.transition makes it, and what it keeps while it plays.
pub struct Transition {
    /// its name, the duration in seconds and what a switch and toasts do while it plays
    pub name: &'static str,
    pub duration: f64,
    pub switch: &'static str,
    pub toasts: &'static str,
    pub from_shown: bool,
    /// the new theme's palette, as its context took it (RenderContext.palette)
    palette: Dict,
    kind: Kind,
    /// where this panel and all of them stand, for one that goes over all the panels as one
    /// (render/canvas.py take_canvas: a slide, the wipe or the line of light told so)
    canvas: Option<Canvas>,
}

enum Kind {
    Cut,
    Fade,
    Slide,
    Tube,
    Entrance(Box<Entrance>),
    Masked(Masked),
    SlideFrom(Side),
    Zoom,
    LightLine(Option<((usize, usize), Image)>),
    Glitch,
    Tiles,
    /// its pieces, and its tiles across the panels
    Shatter(Option<(u64, Vec<Shard>)>, Option<(u64, Vec<Tile>)>),
    Cascade(Option<Laid>),
    Comet(Option<Box<Flown>>),
    Portal(Option<Box<Opening>>),
    Morph(Box<Morph>),
    /// a plugin's (render/plugin.rs)
    Plugin(Box<dyn crate::plugin::PluginTransition>),
}

#[derive(Clone, Copy)]
enum Side {
    Left,
    Bottom,
    Top,
}

/// The maps of _Masked: what each is drawn as.
#[derive(Clone, Copy, PartialEq)]
enum Map {
    Wipe,
    Iris,
    Blinds,
    Dissolve,
}

struct Masked {
    map: Map,
    soft: f64,
    made: Option<((usize, usize), Image)>,
    /// the iris across the panels: the middle of all of them and how far it opens
    across: Option<((f64, f64), f64)>,
}

/// A piece of the old screen as Shatter breaks it: its picture, place, speed and start.
struct Shard {
    picture: Image,
    x: i64,
    y: i64,
    vx: f64,
    vy: f64,
    start: f64,
}

impl Transition {
    /// overlays.transition((name, params), context): None for none ("" or "cut"), one this
    /// does not know (a plugin's: later) and one that fails to start (an entrance's kind that
    /// is no key).
    pub fn new(name: &str, params: &Dict, renderer: &Renderer) -> Option<Transition> {
        let (duration, switch, toasts, from_shown, kind) = match name {
            "" | "cut" => return None,
            "fade" => (0.6, "wait", "wait", false, Kind::Fade),
            "slide" => (0.5, "wait", "wait", false, Kind::Slide),
            "tube" => (0.9, "restart", "over", true, Kind::Tube),
            "entrance" => (
                3.2,
                "follow",
                "hide",
                true,
                Kind::Entrance(Box::new(Entrance::new(params)?)),
            ),
            "wipe" => (0.7, "wait", "over", false, Kind::masked(Map::Wipe, 40.0)),
            "iris" => (0.8, "wait", "over", false, Kind::masked(Map::Iris, 18.0)),
            "blinds" => (0.9, "wait", "over", false, Kind::masked(Map::Blinds, 30.0)),
            "dissolve" => (0.8, "wait", "over", false, Kind::masked(Map::Dissolve, 6.0)),
            "slide-right" => (0.6, "wait", "wait", false, Kind::SlideFrom(Side::Left)),
            "slide-up" => (0.6, "wait", "wait", false, Kind::SlideFrom(Side::Bottom)),
            "slide-down" => (0.6, "wait", "wait", false, Kind::SlideFrom(Side::Top)),
            "zoom" => (0.8, "wait", "over", false, Kind::Zoom),
            "light" => (1.1, "wait", "over", true, Kind::LightLine(None)),
            "glitch" => (0.7, "wait", "over", false, Kind::Glitch),
            "tiles" => (1.2, "wait", "over", false, Kind::Tiles),
            "shatter" => (1.3, "wait", "over", false, Kind::Shatter(None, None)),
            "cascade" => (1.9, "wait", "over", false, Kind::Cascade(None)),
            "comet" => (1.7, "wait", "over", false, Kind::Comet(None)),
            "portal" => (1.5, "wait", "over", false, Kind::Portal(None)),
            "morph" => (2.1, "wait", "over", false, Kind::Morph(Box::default())),
            _ => return Transition::plugin(name, params, renderer),
        };
        let name = BUILT_IN.iter().copied().find(|n| *n == name)?;
        // take_canvas: across the panels, at its own pace there
        let canvas = canvas_duration(name).zip(canvas_of(params));
        let mut kind = kind;
        if let (Kind::Masked(masked), Some((_, c))) = (&mut kind, &canvas) {
            if masked.map == Map::Iris {
                // (it reaches the farthest corner at 255, its edge beyond)
                let at = middle(c.whole);
                let far = reach(c.whole, at) + IRIS_EDGE_MM;
                masked.soft = 255.0 * IRIS_EDGE_MM / far;
                masked.across = Some((at, far));
            }
        }
        Some(Transition {
            name,
            duration: canvas.map_or(duration, |(d, _)| d),
            switch,
            toasts,
            from_shown,
            palette: renderer.theme.palette.clone(),
            kind,
            canvas: canvas.map(|(_, c)| c),
        })
    }

    /// The old screen as its renderer composed it last (Renderer::parting), for the morph
    /// (RenderContext.before): what it takes apart; the others take nothing of it.
    pub fn set_before(&mut self, before: Option<crate::morph::Parting>) {
        if let Kind::Morph(morph) = &mut self.kind {
            morph.before = before;
        }
    }

    /// A plugin's transition (overlays.transition: the registry's), from what draws the plugins
    /// for `renderer`; None where none has it or it failed to start (with a warning).
    fn plugin(name: &str, params: &Dict, renderer: &Renderer) -> Option<Transition> {
        let started = renderer.plugins.as_ref()?.transition(name, params)?;
        let (info, played) = match started {
            Ok(started) => started,
            Err(why) => {
                renderer.warn_of(format!(
                    "transition {} failed to start; switching without one: {why}",
                    lp_py::repr(name)
                ));
                return None;
            }
        };
        Some(Transition {
            name: Box::leak(name.to_string().into_boxed_str()),
            duration: info.duration,
            switch: policy(&info.switch),
            toasts: match info.toasts.as_str() {
                "over" => "over",
                "restart" => "restart",
                "hide" => "hide",
                _ => "wait",
            },
            from_shown: info.from_shown,
            palette: renderer.theme.palette.clone(),
            kind: Kind::Plugin(played),
            canvas: None,
        })
    }

    /// A cut, as the built-in table has it (overlays.transition makes none for it).
    pub fn cut(renderer: &Renderer) -> Transition {
        Transition {
            name: "cut",
            duration: 0.0,
            switch: "wait",
            toasts: "wait",
            from_shown: false,
            palette: renderer.theme.palette.clone(),
            kind: Kind::Cut,
            canvas: None,
        }
    }

    /// on_switch (switch_policy): what a switch to `theme` in `mode` does while it plays
    /// ("wait", "follow" or "restart"); the entrance restarts where the mode turned back, and
    /// else reveals the screen asked for; a plugin's says (one that fails: "wait", the error
    /// given back to be said once).
    pub fn on_switch(
        &mut self,
        theme: &str,
        mode: Option<&str>,
        mode_changed: bool,
    ) -> (&'static str, Option<String>) {
        match &mut self.kind {
            Kind::Entrance(_) if mode_changed => ("restart", None),
            Kind::Entrance(_) => ("follow", None),
            Kind::Plugin(played) => match played.on_switch(theme, mode, mode_changed) {
                Ok(said) => (policy(&said), None),
                Err(why) => ("wait", Some(why)),
            },
            _ => (self.switch, None),
        }
    }

    /// Whether it goes over all the panels as one (render/canvas.py: told where they stand).
    pub fn on_canvas(&self) -> bool {
        self.canvas.is_some()
    }

    fn colour(&self, role: &str) -> [i64; 3] {
        role_colour(&self.palette, role)
    }

    /// frame(old, new, t): the frame it shows `t` of the way (0 to 1) from `old` to `new`;
    /// `old_key` says which old frame it is (Shatter breaks each anew, as Python does by its
    /// id). An error where Python's raises.
    pub fn frame(
        &mut self,
        old: &Image,
        old_key: u64,
        new: &Image,
        t: f64,
        renderer: &Renderer,
    ) -> Result<Image> {
        let colours = Colours {
            void: self.colour("void"),
            info: self.colour("info"),
            text: self.colour("text"),
            muted: self.colour("muted"),
        };
        if let (Some(canvas), Some(way)) = (&self.canvas, slide_way(self.name)) {
            // (_Slide and _SlideFrom across the panels)
            return if t >= 1.0 {
                Ok(new.clone())
            } else {
                slide(old, new, t, canvas, way)
            };
        }
        match &mut self.kind {
            Kind::Cut => Ok(new.clone()),
            Kind::Fade => chops::blend(old, new, (t * t * (3.0 - 2.0 * t)) as f32).map_err(failed),
            Kind::Slide => {
                let eased = 1.0 - (1.0 - t).powf(3.0);
                let shift = round_int(new.width() as f64 * eased)?;
                let mut out = Image::blank(new.mode(), new.width(), new.height());
                out.paste(old, -shift, 0, None).map_err(failed)?;
                out.paste(new, new.width() as i64 - shift, 0, None)
                    .map_err(failed)?;
                Ok(out)
            }
            Kind::Tube => tube(old, new, t, &colours),
            Kind::Entrance(entrance) => entrance.frame(old, new, t, &colours, renderer),
            Kind::Masked(masked) => masked.frame(old, new, t, self.canvas.as_ref()),
            Kind::SlideFrom(side) => slide_from(old, new, t, *side),
            Kind::Zoom => zoom(old, new, t, &colours),
            Kind::LightLine(glow) => light_line(glow, old, new, t, &colours, self.canvas.as_ref()),
            Kind::Glitch => glitch(old, new, t),
            Kind::Tiles => tiles(old, new, t, &colours),
            Kind::Shatter(pieces, tiles) => match &self.canvas {
                Some(canvas) => falling(tiles, old, old_key, new, t, canvas, &colours),
                None => shatter(pieces, old, old_key, new, t, 1.3),
            },
            Kind::Cascade(laid) => cascade(laid, old, new, t, self.canvas.as_ref(), renderer),
            Kind::Morph(morph) => morph.frame(old, new, t, renderer),
            Kind::Comet(flown) => comet(flown, old, new, t, self.canvas.as_ref(), &colours),
            Kind::Portal(opening) => portal(opening, old, new, t, self.canvas.as_ref(), &colours),
            Kind::Plugin(played) => played.frame(old, new, t).map_err(Failed),
        }
    }
}

/// A switch policy as switch_policy keeps it: one of SWITCH_POLICIES, else "wait".
fn policy(said: &str) -> &'static str {
    match said {
        "follow" => "follow",
        "restart" => "restart",
        _ => "wait",
    }
}

/// The built-in names (overlays.BUILT_IN_TRANSITIONS).
pub const BUILT_IN: [&str; 21] = [
    "cut",
    "fade",
    "slide",
    "tube",
    "entrance",
    "wipe",
    "iris",
    "blinds",
    "dissolve",
    "slide-right",
    "slide-up",
    "slide-down",
    "zoom",
    "light",
    "glitch",
    "tiles",
    "shatter",
    "cascade",
    "comet",
    "portal",
    "morph",
];

impl Kind {
    fn masked(map: Map, soft: f64) -> Kind {
        Kind::Masked(Masked {
            map,
            soft,
            made: None,
            across: None,
        })
    }
}

/// The theme's colours a transition draws with (role_color).
struct Colours {
    void: [i64; 3],
    info: [i64; 3],
    text: [i64; 3],
    muted: [i64; 3],
}

// -- the tube and the entrance (render/transitions.py) ------------------------------------------

/// Tube: a line ignites at mid-height and opens into two edges; between them the new screen
/// is already live.
fn tube(old: &Image, new: &Image, t: f64, c: &Colours) -> Result<Image> {
    if t >= 1.0 {
        return Ok(new.clone());
    }
    let (w, h) = (new.width() as f64, new.height() as f64);
    let u = py_min(w, h) / 480.0;
    let mut out = rgb(old)?.into_owned();
    let middle = h / 2.0;
    let wide = ease_out(phase(t, 0.0, 0.3));
    let opening = smooth(phase(t, 0.15, 1.0));
    let beyond = 40.0 * u; // (the edges leave the frame before they go out)
    let top = middle - (middle + beyond) * opening;
    let bottom = middle + (h - middle + beyond) * opening;
    if opening > 0.0 {
        let (a, b) = (
            trunc_int(top)?.max(0),
            (new.height() as i64).min(ceil_int(bottom)?),
        );
        if b > a {
            let part = crop(new, (0, a, new.width() as i64, b));
            let part = rgb(&part)?;
            out.paste(&part, 0, a, None).map_err(failed)?;
        }
    }
    let bright = 1.0 - phase(t, 0.7, 1.0);
    if wide > 0.0 && bright > 0.0 {
        let look = Look {
            unit: u,
            soft: 140.0 * w / 1920.0,
            halo: 13.0,
            share: 0.55,
            colour: c.info,
            white: c.text,
        };
        let reach = wide * w / 2.0;
        let edges = if opening <= 0.0 {
            vec![middle]
        } else {
            vec![top, bottom]
        };
        for y in edges {
            add_line(
                &mut out,
                (w / 2.0 - reach, w / 2.0 + reach, y),
                bright,
                &look,
            )?;
        }
    }
    Ok(out)
}

/// Entrance: into a mode and out of it (a game, music, the PC at rest): the old frame closes
/// to a line, a band opens with a logo or a title while a text types in below it, then the
/// band closes and the line opens onto the new screen.
struct Entrance {
    title: String,
    text: String,
    /// the parameters as given: the logo's key and the kind
    logo: Option<Value>,
    kind: Option<Value>,
    /// _figure: the frame size it was made for, the picture, its halo and margin
    figure: Option<((usize, usize), Image, Image, i64)>,
}

/// The entrance's times (TIMELINE_S): the times below are seconds of it.
const TIMELINE_S: f64 = 3.2;

impl Entrance {
    /// Entrance(context, params): None where a kind is no key (Python's TypeError).
    fn new(params: &Dict) -> Option<Entrance> {
        let kind = params.get("kind").cloned();
        let default = match &kind {
            Some(Value::List(_)) | Some(Value::Dict(_)) => return None,
            Some(Value::Str(k)) => match k.as_str() {
                "start" => lp_i18n::t("Game mode"),
                "end" => lp_i18n::t("Game ended"),
                "media" => lp_i18n::t("Now playing"),
                "media-end" => lp_i18n::t("Music ended"),
                "idle" => lp_i18n::t("Taking a break"),
                "idle-end" => lp_i18n::t("Welcome back"),
                _ => String::new(),
            },
            _ => String::new(),
        };
        let text_of = |key: &str, otherwise: String| match params.get(key) {
            Some(v) if v.truthy() => v.str(),
            _ => otherwise,
        };
        Some(Entrance {
            title: text_of("title", String::new()),
            text: lp_py::upper(&text_of("text", default)),
            logo: params.get("logo").cloned(),
            kind,
            figure: None,
        })
    }

    /// _logo: the picture a service published under the logo's key, None for none.
    fn logo<'a>(&self, renderer: &'a Renderer) -> Option<&'a Image> {
        match &self.logo {
            Some(Value::Str(key)) if !key.is_empty() => renderer.image(key),
            _ => None,
        }
    }

    /// _picture: the logo (or the title in big letters) and its halo, made when the band first
    /// opens (a logo published with the switch is there by then), anew for another size.
    fn picture(
        &mut self,
        (w, h): (usize, usize),
        u: f64,
        white: [i64; 3],
        renderer: &Renderer,
    ) -> Result<(Image, Image, i64)> {
        if let Some((size, picture, halo, margin)) = &self.figure {
            if *size == (w, h) {
                return Ok((picture.clone(), halo.clone(), *margin));
            }
        }
        let room_w = 16.max((round_int(900.0 * u)?).min(w as i64 - round_int(80.0 * u)?));
        let room_h = 8.max(round_int(150.0 * u)?);
        let picture = match self.logo(renderer) {
            Some(logo) => {
                let media = matches!(&self.kind, Some(Value::Str(k)) if k == "media");
                if !self.title.is_empty() && media {
                    // (the cover: the track below it)
                    let joined = format!("{} · {}", self.text, lp_py::upper(&self.title));
                    self.text = joined.chars().take(64).collect();
                }
                let picture = logo.convert(Mode::RGBA).map_err(failed)?;
                let (pw, ph) = (picture.width() as f64, picture.height() as f64);
                let scale = py_min(py_min(room_w as f64 / pw, room_h as f64 / ph), 2.0); // (no blur)
                let size = (
                    1.max(round_int(pw * scale)?) as usize,
                    1.max(round_int(ph * scale)?) as usize,
                );
                picture
                    .resize(size.0, size.1, Filter::Lanczos, None, None)
                    .map_err(failed)?
            }
            None => {
                let title = lp_py::upper(if self.title.is_empty() {
                    &self.text
                } else {
                    &self.title
                });
                if self.title.is_empty() {
                    self.text = String::new(); // (the text stands as the title; nothing to type)
                }
                lettering(
                    &title,
                    room_w,
                    round_int(130.0 * u)?,
                    u,
                    white,
                    &renderer.env,
                )?
            }
        };
        let margin = round_int(60.0 * u)?;
        let mut halo = Image::blank(
            Mode::L,
            (picture.width() as i64 + 2 * margin).max(0) as usize,
            (picture.height() as i64 + 2 * margin).max(0) as usize,
        );
        halo.paste(
            &picture.getchannel(3).map_err(failed)?,
            margin,
            margin,
            None,
        )
        .map_err(failed)?;
        let radius = py_max(1.0, 18.0 * u) as f32;
        let halo = halo.gaussian_blur(radius, radius).map_err(failed)?;
        self.figure = Some(((w, h), picture.clone(), halo.clone(), margin));
        Ok((picture, halo, margin))
    }

    fn frame(
        &mut self,
        old: &Image,
        new: &Image,
        t: f64,
        c: &Colours,
        renderer: &Renderer,
    ) -> Result<Image> {
        if t >= 1.0 {
            return Ok(new.clone());
        }
        let (wi, hi) = (new.width() as i64, new.height() as i64);
        let (w, h) = (wi as f64, hi as f64);
        let u = py_min(w, h) / 480.0;
        let s = t * TIMELINE_S;
        let mut out = Image::new(Mode::RGB, new.width(), new.height(), &colour_of(c.void));
        let middle = round_int(h / 2.0 - 20.0 * u)?;
        let mid = middle as f64;
        let beyond = 40.0 * u;
        let line = Look {
            unit: u,
            soft: 1.0,
            halo: 13.0,
            share: 0.55,
            colour: c.info,
            white: c.text,
        };

        // The old frame closes as a tube: two edges run onto the middle line.
        let closing = smooth(phase(s, 0.0, 0.6));
        if closing < 1.0 {
            let top = -beyond + (mid + beyond) * closing;
            let bottom = h + beyond - (h + beyond - mid) * closing;
            let (a, b) = (trunc_int(top)?.max(0), hi.min(ceil_int(bottom)?));
            if b > a {
                let part = crop(old, (0, a, wi, b));
                out.paste(&*rgb(&part)?, 0, a, None).map_err(failed)?;
            }
            for y in [top, bottom] {
                add_line(
                    &mut out,
                    (0.0, w, y),
                    0.9 * py_min(1.0, 4.0 * closing),
                    &line,
                )?;
            }
        }

        // The line opens to a band around the logo or title, which lights up; later the band
        // closes again, and out of that line the new screen opens.
        let shut = smooth(phase(s, 2.30, 2.65));
        let opening = smooth(phase(s, 0.80, 1.30)) * (1.0 - shut);
        let mut half = 0.0;
        if s >= 0.80 {
            let (picture, halo, margin) =
                self.picture((new.width(), new.height()), u, c.text, renderer)?;
            let half_i = (picture.height() as i64).div_euclid(2) + round_int(34.0 * u)?;
            half = half_i as f64;
            let (a, b) = (
                trunc_int(mid - half * opening)?,
                ceil_int(mid + half * opening)?,
            );
            if opening > 0.0 && b > a {
                let mut stage =
                    Image::new(Mode::RGB, new.width(), (b - a) as usize, &colour_of(c.void));
                let px = (wi - picture.width() as i64).div_euclid(2);
                let py = middle - (picture.height() as i64).div_euclid(2) - a;
                let mut glow = ease_out(phase(s, 0.90, 1.30));
                if s >= 1.30 {
                    // (it dims to a steady glow)
                    glow *= 0.35 + 0.65 * (-(s - 1.30) * 3.0).exp();
                }
                if glow > 0.004 {
                    add_light(
                        &mut stage,
                        (px - margin, py - margin),
                        &scaled(halo, glow)?,
                        c.info,
                    )?;
                }
                stage
                    .paste(&picture, px, py, Some(&picture))
                    .map_err(failed)?;
                out.paste(&stage, 0, a, None).map_err(failed)?;
            }
            if !self.text.is_empty() && 1.25 < s && shut < 1.0 {
                // (the text types in below)
                let letters = self.text.chars().count();
                let shown_n = trunc_int(letters as f64 * phase(s, 1.30, 1.70) + 0.001)?;
                let shown: String = self.text.chars().take(shown_n.max(0) as usize).collect();
                let font = face(&renderer.env, "Barlow-Medium", 7.max(round_int(26.0 * u)?))?;
                let mut track = 12.0 * u;
                while track > 0.0 && tracked_width(&font, &self.text, track)? > w - 20.0 * u {
                    track -= u; // (tighter on a narrow panel)
                }
                let x = (w - tracked_width(&font, &self.text, track)?) / 2.0;
                draw_tracked(
                    &mut out,
                    (x, mid + half + 46.0 * u),
                    &shown,
                    &font,
                    ink(mix(c.muted, c.void, 1.0 - shut)),
                    track,
                )?;
            }
        }

        // The new screen opens, live, out of the closed line.
        let reveal = smooth(phase(s, 2.65, TIMELINE_S));
        let (mut top, mut bottom) = (mid, mid);
        if reveal > 0.0 {
            top = mid - (mid + beyond) * reveal;
            bottom = mid + (h - mid + beyond) * reveal;
            let (a, b) = (ceil_int(top)?.max(0), hi.min(trunc_int(bottom)?));
            if b > a {
                let part = crop(new, (0, a, wi, b));
                out.paste(&*rgb(&part)?, 0, a, None).map_err(failed)?;
            }
        }

        // One light over all of it: bright where edges meet, calm while the band stands, and it
        // goes out as it moves out. Nothing ignites a second time.
        if closing >= 1.0 {
            let bright = if reveal > 0.0 {
                1.0 - smooth(phase(s, 2.85, TIMELINE_S))
            } else if shut > 0.0 {
                0.3 + 0.7 * shut
            } else {
                1.0 - 0.7 * smooth(phase(s, 0.80, 1.30))
            };
            let edges = if reveal > 0.0 {
                vec![top, bottom]
            } else if opening > 0.0 {
                vec![mid - half * opening, mid + half * opening]
            } else {
                vec![mid]
            };
            for y in edges {
                add_line(&mut out, (0.0, w, y), bright, &line)?;
            }
        }
        Ok(out)
    }
}

/// transition_show.card: the card in the middle of a transition shown to see it: the
/// transition's name, large, in the theme's text colour on its dark (`label` as the person
/// reads it, made upper case), the panel's size.
pub fn show_card(label: &str, (w, h): (usize, usize), renderer: &Renderer) -> Result<Image> {
    let palette = &renderer.theme.palette;
    let (void, ink) = (role_colour(palette, "void"), role_colour(palette, "text"));
    let u = w.min(h) as f64 / 480.0;
    let c = colour_of(void);
    let mut out = Image::new(Mode::RGB, w, h, &c);
    let width = 16.max(w as i64 - round_int(120.0 * u)?);
    let letters = lettering(
        &lp_py::upper(label),
        width,
        round_int(110.0 * u)?,
        u,
        ink,
        &renderer.env,
    )?;
    let x = (w as i64 - letters.width() as i64).div_euclid(2);
    let y = (h as i64 - letters.height() as i64).div_euclid(2);
    out.paste(&letters, x, y, Some(&letters)).map_err(failed)?;
    Ok(out)
}

/// _lettering: `text` in big, spaced letters on a clear picture `height` high, as narrow as it
/// is (it gets smaller to fit `width`).
fn lettering(
    text: &str,
    width: i64,
    height: i64,
    u: f64,
    colour: [i64; 3],
    env: &lp_theme::Env,
) -> Result<Image> {
    let (mut size, mut track) = (round_int(96.0 * u)?, 10.0 * u);
    let mut font = face(env, "Barlow-Bold", size)?;
    while size > 8 && tracked_width(&font, text, track)? + 20.0 * u > width as f64 {
        size = 8.max(round_int(size as f64 * 0.92)?);
        font = face(env, "Barlow-Bold", size)?;
        track *= 0.92;
    }
    let span = width.min(1.max(round_int(tracked_width(&font, text, track)? + 20.0 * u)?));
    let mut mask = Image::blank(Mode::L, span.max(0) as usize, height.max(1) as usize);
    let baseline = round_int(height as f64 * 100.0 / 130.0)? as f64;
    draw_tracked(
        &mut mask,
        (10.0 * u, baseline),
        text,
        &font,
        [255, 0, 0, 0],
        track,
    )?;
    let c = colour_of(colour);
    let mut picture = Image::new(
        Mode::RGBA,
        mask.width(),
        mask.height(),
        &[c[0], c[1], c[2], 0],
    );
    picture.putalpha(&mask).map_err(failed)?;
    Ok(picture)
}

// -- the plain handovers (render/handovers.py): the new screen where a map lies below a rising
// threshold -------------------------------------------------------------------------------------

impl Masked {
    /// `canvas`: the wipe's and the iris's across all the panels (Wipe._map: wipe_map; Iris:
    /// from the middle of all of them, in_out)
    fn frame(
        &mut self,
        old: &Image,
        new: &Image,
        t: f64,
        canvas: Option<&Canvas>,
    ) -> Result<Image> {
        if t >= 1.0 {
            return Ok(new.clone());
        }
        let size = (new.width(), new.height());
        if self.made.as_ref().is_none_or(|(s, _)| *s != size) {
            let made = match (canvas, self.across) {
                (Some(canvas), _) if self.map == Map::Wipe => wipe_map(canvas, size.0, size.1)?,
                (Some(canvas), Some((at, far))) => radial_map(canvas, size, at, far)?,
                _ => map_of(self.map, size.0, size.1)?,
            };
            self.made = Some((size, made));
        }
        let level = match (canvas, self.across) {
            (Some(_), Some(_)) => 255.0 * in_out(t),
            _ => smooth(t) * (255.0 + self.soft),
        };
        let lut = threshold(level, self.soft)?;
        let made = &self.made.as_ref().expect("made").1;
        let mask = made.point(&lut).map_err(failed)?;
        chops::composite(&*rgb(new)?, &*rgb(old)?, &mask).map_err(failed)
    }
}

/// _row: a map that changes across, the same down every column.
fn row(values: Vec<u8>, w: usize, h: usize) -> Result<Image> {
    let n = values.len();
    Image::from_bytes(Mode::L, n, 1, values)
        .and_then(|r| r.resize(w, h, Filter::Bilinear, None, None))
        .map_err(failed)
}

/// _map: the map (L) a masked handover makes once for a size.
fn map_of(map: Map, w: usize, h: usize) -> Result<Image> {
    let (wf, hf) = (w as f64, h as f64);
    let u = py_min(wf, hf) / 480.0;
    match map {
        // a soft edge goes across, from the left
        Map::Wipe => row((0..=255).collect(), w, h),
        // a circle opens from the middle (it reaches the corners at 255)
        Map::Iris => {
            let side = ceil_int(lp_py::math::hypot(wf, hf))?;
            let circle = Image::radial_gradient()
                .resize(side as usize, side as usize, Filter::Bilinear, None, None)
                .map_err(failed)?;
            let (left, top) = (
                (side - w as i64).div_euclid(2),
                (side - h as i64).div_euclid(2),
            );
            Ok(circle.crop(left, top, left + w as i64, top + h as i64))
        }
        // upright slats turn one after another, from the left, each from its own left edge
        Map::Blinds => {
            let slats = 4.max(round_int(wf / (150.0 * u))?);
            let width = wf / slats as f64;
            let mut values = Vec::with_capacity(w);
            for x in 0..w {
                let (k, inside) = lp_py::num::float_div_mod(x as f64, width);
                let v = 255.0 * (0.45 * k / 1.max(slats - 1) as f64 + 0.55 * inside / width);
                values.push(255.min(round_int(v)?).clamp(0, 255) as u8);
            }
            Image::from_bytes(Mode::L, w, 1, values)
                .and_then(|r| r.resize(w, h, Filter::Nearest, None, None))
                .map_err(failed)
        }
        // the new screen comes through in small squares, here and there (the same squares
        // every time: calm, not noise)
        Map::Dissolve => {
            let block = 2.max(round_int(8.0 * u)?);
            let cols = ceil_int(wf / block as f64)?.max(0) as usize;
            let rows = ceil_int(hf / block as f64)?.max(0) as usize;
            let mut chance = Random::new(0x5EED);
            let bytes = (0..cols * rows)
                .map(|_| chance.randbelow(256) as u8)
                .collect();
            let noise = Image::from_bytes(Mode::L, cols, rows, bytes).map_err(failed)?;
            let b = block as usize;
            Ok(noise
                .resize(cols * b, rows * b, Filter::Nearest, None, None)
                .map_err(failed)?
                .crop(0, 0, w as i64, h as i64))
        }
    }
}

/// _SlideFrom: the new screen pushes the old one out, from one side.
fn slide_from(old: &Image, new: &Image, t: f64, side: Side) -> Result<Image> {
    if t >= 1.0 {
        return Ok(new.clone());
    }
    let (w, h) = (new.width() as i64, new.height() as i64);
    let eased = 1.0 - (1.0 - t).powf(3.0);
    let (dx, dy) = match side {
        Side::Left => (1, 0),
        Side::Bottom => (0, -1),
        Side::Top => (0, 1),
    };
    let (sx, sy) = (
        round_int((dx * w) as f64 * eased)?,
        round_int((dy * h) as f64 * eased)?,
    );
    let mut out = Image::blank(Mode::RGB, new.width(), new.height());
    out.paste(&*rgb(old)?, sx, sy, None).map_err(failed)?;
    out.paste(&*rgb(new)?, sx - dx * w, sy - dy * h, None)
        .map_err(failed)?;
    Ok(out)
}

/// Zoom: the old screen draws back into the dark, the new one comes forward out of it.
fn zoom(old: &Image, new: &Image, t: f64, c: &Colours) -> Result<Image> {
    if t >= 1.0 {
        return Ok(new.clone());
    }
    const DEPTH: f64 = 0.12; // (how far back a screen stands at its farthest)
    let (w, h) = (new.width(), new.height());
    let going = t < 0.5;
    let u = if going {
        smooth(t / 0.5)
    } else {
        1.0 - smooth((t - 0.5) / 0.5)
    }; // (0 near, 1 far)
    let scale = 1.0 - DEPTH * u;
    let picture = rgb(if going { old } else { new })?;
    let size = (
        1.max(round_int(w as f64 * scale)?) as usize,
        1.max(round_int(h as f64 * scale)?) as usize,
    );
    let dark = colour_of(c.void);
    let mut out = Image::new(Mode::RGB, w, h, &dark);
    let small = picture
        .resize(size.0, size.1, Filter::Bilinear, None, None)
        .map_err(failed)?;
    let small = chops::blend(
        &small,
        &Image::new(Mode::RGB, size.0, size.1, &dark),
        py_min(1.0, 0.9 * u) as f32,
    )
    .map_err(failed)?;
    out.paste(
        &small,
        (w as i64 - size.0 as i64).div_euclid(2),
        (h as i64 - size.1 as i64).div_euclid(2),
        None,
    )
    .map_err(failed)?;
    Ok(out)
}

// -- the elaborate ones -------------------------------------------------------------------------

/// LightLine: a line of light runs across, the old screen ahead of it and the new one behind,
/// and goes out as it leaves.
fn light_line(
    kept: &mut Option<((usize, usize), Image)>,
    old: &Image,
    new: &Image,
    t: f64,
    c: &Colours,
    canvas: Option<&Canvas>,
) -> Result<Image> {
    if t >= 1.0 {
        return Ok(new.clone());
    }
    let (w, h) = (new.width() as i64, new.height() as i64);
    if kept
        .as_ref()
        .is_none_or(|(size, _)| *size != (new.width(), new.height()))
    {
        // _strip: the line's light, upright, made once: a horizontal line drawn along a strip
        // as long as the frame is high, turned
        let u = py_min(w as f64, h as f64) / 480.0;
        let half = 3.max(round_int(32.0 * u)?);
        let mut strip = Image::blank(Mode::RGB, (h + 2 * half) as usize, (2 * half) as usize);
        let look = Look {
            unit: u,
            soft: 1.0,
            halo: 13.0,
            share: 0.55,
            colour: c.info,
            white: c.text,
        };
        add_line(
            &mut strip,
            (-half as f64, (h + 3 * half) as f64, half as f64),
            1.0,
            &look,
        )?;
        let turned = strip
            .crop(half, 0, half + h, 2 * half)
            .transpose(Transpose::Rotate90);
        *kept = Some(((new.width(), new.height()), turned));
    }
    let glow = &kept.as_ref().expect("kept").1;
    let gw = glow.width() as i64;
    let x = match canvas {
        // where the line is on its way over all the panels
        Some(canvas) => light_at(canvas, t, w)?,
        None => round_int(-(gw as f64) + (w + 2 * gw) as f64 * smooth(t))?,
    };
    let mut out = rgb(old)?.into_owned();
    if x > 0 {
        let part = crop(&*rgb(new)?, (0, 0, w.min(x), h));
        out.paste(&part, 0, 0, None).map_err(failed)?;
    }
    let bright = ease_out(phase(t, 0.0, 0.15)) * (1.0 - ease_in(phase(t, 0.8, 1.0)));
    let (a, b) = (0.max(x - gw.div_euclid(2)), w.min(x + gw.div_euclid(2)));
    if b > a && bright > 0.004 {
        let at = x - gw.div_euclid(2);
        let light = glow.crop(a - at, 0, b - at, h);
        let light = if bright < 0.999 {
            light
                .point_with(|v| f64::from(v) * bright)
                .map_err(failed)?
        } else {
            light
        };
        let sum = chops::add(&out.crop(a, 0, b, h), &light, 1.0, 0).map_err(failed)?;
        out.paste(&sum, a, 0, None).map_err(failed)?;
    }
    Ok(out)
}

/// Glitch: a short digital fault: rows slip sideways, the colours split, bits of the other
/// screen flash in; at its height the new screen takes over and the fault fades.
fn glitch(old: &Image, new: &Image, t: f64) -> Result<Image> {
    if t >= 1.0 {
        return Ok(new.clone());
    }
    let (w, h) = (new.width() as i64, new.height() as i64);
    let u = py_min(w as f64, h as f64) / 480.0;
    let strength = 1.0 - (t - 0.5).abs() * 2.0; // (strongest in the middle)
    let (base, other) = if t < 0.5 {
        (rgb(old)?, rgb(new)?)
    } else {
        (rgb(new)?, rgb(old)?)
    };
    // (a new fault a few times a second, not every frame)
    let mut chance = Random::new(trunc_int(t * 40.0)?);
    let mut out = base.clone().into_owned();
    for _ in 0..round_int(10.0 * strength)?.saturating_add(1).max(0) {
        let top = chance
            .randrange(h)
            .ok_or_else(|| Failed("empty range for randrange()".into()))?;
        let tall = 2.max(round_int(chance.uniform(4.0, 40.0) * u)?);
        let shift = round_int(chance.uniform(-80.0, 80.0) * u * strength)?;
        let from = if chance.random() < 0.25 {
            &other
        } else {
            &base
        };
        let band = crop(from, (0, top, w, h.min(top + tall)));
        out.paste(&band.offset(shift, 0), 0, top, None)
            .map_err(failed)?;
    }
    let split = round_int(12.0 * u * strength)?;
    if split != 0 {
        out = colours_apart(&out, split)?;
    }
    Ok(out)
}

/// The red band moved `split` to the right and the blue one as far to the left, what leaves on
/// one side coming in on the other (Image.merge of the split bands, the red and the blue
/// through ImageChops.offset): in one pass, a row at a time.
fn colours_apart(out: &Image, split: i64) -> Result<Image> {
    let (w, h) = (out.width(), out.height());
    if w == 0 {
        return Ok(out.clone());
    }
    // (each row as it is, then its red from `s` to the left and its blue from `s` to the
    // right, round the row)
    let s = split.rem_euclid(w as i64) as usize * 3;
    let mut moved = out.as_bytes().to_vec();
    let row = w * 3;
    for (to, from) in moved
        .chunks_exact_mut(row)
        .zip(out.as_bytes().chunks_exact(row))
    {
        let band = |to: &mut [u8], from: &[u8], b: usize| {
            for (o, f) in to.chunks_exact_mut(3).zip(from.chunks_exact(3)) {
                o[b] = f[b];
            }
        };
        band(&mut to[s..], &from[..row - s], 0);
        band(&mut to[..s], &from[row - s..], 0);
        band(&mut to[..row - s], &from[s..], 2);
        band(&mut to[row - s..], &from[..s], 2);
    }
    Image::from_bytes(Mode::RGB, w, h, moved).map_err(failed)
}

/// Tiles: the screen turns over tile by tile, from one corner across, the new one on their
/// backs.
fn tiles(old: &Image, new: &Image, t: f64, c: &Colours) -> Result<Image> {
    if t >= 1.0 {
        return Ok(new.clone());
    }
    const SPREAD: f64 = 0.55; // (the share of the time over which the tiles begin)
    let (w, h) = (new.width() as i64, new.height() as i64);
    let u = py_min(w as f64, h as f64) / 480.0;
    let side = 16.max(round_int(120.0 * u)?);
    let cols = ceil_int(w as f64 / side as f64)?;
    let rows = ceil_int(h as f64 / side as f64)?;
    let dark = colour_of(c.void);
    let mut out = Image::new(Mode::RGB, new.width(), new.height(), &dark);
    let (old, new) = (rgb(old)?, rgb(new)?);
    let last = 1.max(cols + rows - 2);
    for row in 0..rows.max(0) {
        for col in 0..cols.max(0) {
            let bounds = (
                col * side,
                row * side,
                w.min((col + 1) * side),
                h.min((row + 1) * side),
            );
            let start = SPREAD * (col + row) as f64 / last as f64;
            let turn = smooth(phase(t, start, start + 1.0 - SPREAD));
            let width = (std::f64::consts::PI * turn).cos().abs();
            let face = crop(if turn < 0.5 { &old } else { &new }, bounds);
            if width >= 0.999 {
                out.paste(&face, bounds.0, bounds.1, None).map_err(failed)?;
                continue;
            }
            let size = (
                1.max(round_int(face.width() as f64 * width)?) as usize,
                face.height(),
            );
            if size.0 <= 1 {
                continue;
            }
            let edge = face
                .resize(size.0, size.1, Filter::Bilinear, None, None)
                .map_err(failed)?;
            let edge = chops::blend(
                &edge,
                &Image::new(Mode::RGB, size.0, size.1, &dark),
                (0.5 * (1.0 - width)) as f32,
            )
            .map_err(failed)?;
            let x = bounds.0 + (face.width() as i64 - size.0 as i64).div_euclid(2);
            out.paste(&edge, x, bounds.1, None).map_err(failed)?;
        }
    }
    Ok(out)
}

/// Shatter: the old screen breaks into pieces that fly apart from the middle and fall, the new
/// one already there behind them; the pieces made once for each old frame.
fn shatter(
    kept: &mut Option<(u64, Vec<Shard>)>,
    old: &Image,
    old_key: u64,
    new: &Image,
    t: f64,
    duration: f64,
) -> Result<Image> {
    if t >= 1.0 {
        return Ok(new.clone());
    }
    if kept.as_ref().is_none_or(|(key, _)| *key != old_key) {
        *kept = Some((old_key, broken(old)?));
    }
    let (w, h) = (new.width() as i64, new.height() as i64);
    let u = py_min(w as f64, h as f64) / 480.0;
    let mut out = rgb(new)?.into_owned();
    let seconds = t * duration;
    for shard in &kept.as_ref().expect("kept").1 {
        let s = py_max(0.0, seconds - shard.start * duration);
        let px = round_int(shard.x as f64 + shard.vx * s)?;
        let py = round_int(shard.y as f64 + shard.vy * s + 0.5 * 2400.0 * u * s * s)?; // (it falls)
        let (pw, ph) = (shard.picture.width() as i64, shard.picture.height() as i64);
        if -pw < px && px < w && -ph < py && py < h {
            out.paste(&shard.picture, px, py, None).map_err(failed)?;
        }
    }
    Ok(out)
}

/// _broken: the pieces of the old frame, each with its place, its speed and its start.
fn broken(old: &Image) -> Result<Vec<Shard>> {
    let (w, h) = (old.width() as i64, old.height() as i64);
    let u = py_min(w as f64, h as f64) / 480.0;
    let side = 12.max(round_int(60.0 * u)?);
    let mut chance = Random::new(0xB10C);
    let (cx, cy) = (w as f64 / 2.0, h as f64 / 2.0);
    let reach = lp_py::math::hypot(cx, cy);
    let picture = rgb(old)?;
    let mut pieces = Vec::new();
    let mut y = 0;
    while y < h {
        let mut x = 0;
        while x < w {
            let bounds = (x, y, w.min(x + side), h.min(y + side));
            let dx = bounds.0 as f64 + side as f64 / 2.0 - cx;
            let dy = bounds.1 as f64 + side as f64 / 2.0 - cy;
            let far = lp_py::math::hypot(dx, dy) / reach;
            let speed = (0.6 + chance.random()) * 900.0 * u;
            let angle = dy.atan2(dx) + chance.uniform(-0.4, 0.4);
            let (vx, vy) = (angle.cos() * speed, angle.sin() * speed - 300.0 * u);
            pieces.push(Shard {
                picture: crop(&picture, bounds),
                x: bounds.0,
                y: bounds.1,
                vx,
                vy,
                start: 0.45 * far,
            });
            x += side;
        }
        y += side;
    }
    Ok(pieces)
}

// -- across the panels and in millimetres (render/handovers.py): the iris's edge, the shatter's
// tiles, the cascade's cards, the comet, the portal's ring --------------------------------------

/// Iris.EDGE_MM: across the panels, the iris's soft edge
const IRIS_EDGE_MM: f64 = 11.0;

/// A tile of the old screen as the shatter across the panels breaks it (_Tile).
struct Tile {
    picture: Image,
    bounds: (i64, i64, i64, i64),
    drift: f64,
    turn: f64,
    start: f64,
}

/// Shatter.TILE_MM and SHADE
const TILE_MM: f64 = 19.0;
const TILE_SHADE: f64 = 0.35;

/// Shatter._tiles_of: the tiles of the old frame across the panels, each with its box, its own
/// chance (drift, turn) and when it begins, along all the panels.
fn tiles_of(old: &Image, canvas: &Canvas) -> Result<Vec<Tile>> {
    let (w, h) = (old.width() as i64, old.height() as i64);
    let (x, y, mw, mh) = canvas.own;
    // (no more of them than the panel has pixels)
    let cols = w.min(4.max(round_int(mw / TILE_MM)?));
    let rows = h.min(2.max(round_int(mh / TILE_MM)?));
    let way = way_of(canvas);
    let (a, b) = span(canvas.whole, way);
    // (each panel its own chance: two alike side by side do not break alike)
    let seed = 1009i64
        .wrapping_mul(round_int(x)?)
        .wrapping_add(round_int(y)?);
    let mut chance = Random::new(seed);
    let picture = rgb(old)?;
    let mut tiles = Vec::new();
    for row in 0..rows {
        for col in 0..cols {
            let (drift, turn, late) = (chance.random(), chance.random(), chance.random());
            let edge = |i: i64, n: i64, size: i64| round_int((i * size) as f64 / n as f64);
            let bounds = (
                edge(col, cols, w)?,
                edge(row, rows, h)?,
                edge(col + 1, cols, w)?,
                edge(row + 1, rows, h)?,
            );
            let cx = x + (col as f64 + 0.5) * mw / cols as f64;
            let cy = y + (row as f64 + 0.5) * mh / rows as f64;
            let q = (cx * way.0 as f64 + cy * way.1 as f64 - a) / py_max(0.001, b - a);
            tiles.push(Tile {
                picture: crop(&picture, bounds),
                bounds,
                drift,
                turn,
                start: q * 0.48 + late * 0.06,
            });
        }
    }
    Ok(tiles)
}

/// Shatter._falling: across the panels, the tiles drift, turn, shrink and fall one after
/// another along all of them, the new screen coming out of the shade behind them.
fn falling(
    kept: &mut Option<(u64, Vec<Tile>)>,
    old: &Image,
    old_key: u64,
    new: &Image,
    t: f64,
    canvas: &Canvas,
    c: &Colours,
) -> Result<Image> {
    if t >= 1.0 {
        return Ok(new.clone());
    }
    if kept.as_ref().is_none_or(|(key, _)| *key != old_key) {
        *kept = Some((old_key, tiles_of(old, canvas)?));
    }
    let dark = TILE_SHADE * (1.0 - smooth(t * 1.4));
    let base = rgb(new)?;
    let mut out = if dark > 0.0 {
        let black = Image::blank(Mode::RGB, base.width(), base.height());
        chops::blend(&base, &black, dark as f32).map_err(failed)?
    } else {
        base.into_owned()
    };
    let (sx, sy) = per_mm(canvas, (new.width(), new.height()));
    let way = way_of(canvas);
    let edge = colour_of(c.text);
    let pi = std::f64::consts::PI;
    for tile in &kept.as_ref().expect("kept").1 {
        let tt = clamp((t - tile.start) / 0.46);
        if tt >= 1.0 {
            continue; // (gone)
        }
        if tt <= 0.0 {
            // (not yet begun: where it was)
            out.paste(&tile.picture, tile.bounds.0, tile.bounds.1, None)
                .map_err(failed)?;
            continue;
        }
        let dx = (way.0 as f64 * 22.0 * tt + (tile.drift - 0.5) * 16.0 * tt) * sx;
        let dy = (way.1 as f64 * 22.0 * tt + 70.0 * tt * tt - 6.0 * (pi * tt).sin()) * sy;
        let lit = 0.22 * (pi * py_min(1.0, tt * 1.6)).sin();
        let turn = (tile.turn - 0.5) * 1.5 * tt;
        lay(
            &mut out,
            tile,
            (dx, dy),
            turn,
            1.0 - 0.18 * tt,
            1.0 - tt.powf(1.7),
            lit,
            edge,
        )?;
    }
    Ok(out)
}

/// _edged: `picture` with a line of `colour` along its edge, `strength` over it.
fn edged(picture: &Image, strength: f64, colour: [u8; 3]) -> Result<Image> {
    let mut face = picture.clone();
    let (w, h) = (face.width() as i64, face.height() as i64);
    for b in [
        (0, 0, w, 1),
        (0, h - 1, w, h),
        (0, 1, 1, h - 1),
        (w - 1, 1, w, h - 1),
    ] {
        if b.2 > b.0 && b.3 > b.1 {
            let part = crop(&face, b);
            let tint = Image::new(Mode::RGB, part.width(), part.height(), &colour);
            let lit = chops::blend(&part, &tint, strength as f32).map_err(failed)?;
            face.paste(&lit, b.0, b.1, None).map_err(failed)?;
        }
    }
    Ok(face)
}

/// _lay: lay `tile` onto `out`, moved by `by`, turned by `turn` (radians, clockwise), grown by
/// `size` about its middle, `alpha` of it seen, its edge lit.
#[allow(clippy::too_many_arguments)]
fn lay(
    out: &mut Image,
    tile: &Tile,
    by: (f64, f64),
    turn: f64,
    size: f64,
    alpha: f64,
    lit: f64,
    edge: [u8; 3],
) -> Result<()> {
    let face = if lit > 0.004 {
        Cow::Owned(edged(&tile.picture, lit, edge)?)
    } else {
        Cow::Borrowed(&tile.picture)
    };
    // (a clear pixel around it: soft edges once turned; held premultiplied, as the transform
    // takes an RGBA picture: its colours, all of alpha 255, and the clear ring are so already)
    let (pw, ph) = (face.width() + 2, face.height() + 2);
    let held = held_premultiplied(&face)?;
    let (x0, y0, x1, y1) = tile.bounds;
    let cx = (x0 + x1) as f64 / 2.0 + by.0;
    let cy = (y0 + y1) as f64 / 2.0 + by.1;
    let (c, s) = (turn.cos(), turn.sin());
    let (pwf, phf) = (pw as f64, ph as f64);
    let hx = size * (c.abs() * pwf + s.abs() * phf) / 2.0;
    let hy = size * (s.abs() * pwf + c.abs() * phf) / 2.0;
    let (ox, oy) = (floor_int(cx - hx)?, floor_int(cy - hy)?);
    let (ow, oh) = (ceil_int(cx + hx)? - ox, ceil_int(cy + hy)? - oy);
    let (w, h) = (out.width() as i64, out.height() as i64);
    if ow <= 0 || oh <= 0 || ox >= w || oy >= h || ox + ow <= 0 || oy + oh <= 0 {
        return Ok(());
    }
    // (where each pixel laid comes from in the tile: turned back and shrunk about its middle)
    let (mx, my) = (pwf / 2.0, phf / 2.0);
    let (dx, dy) = (ox as f64 - cx, oy as f64 - cy);
    let data = [
        c / size,
        s / size,
        mx + (c * dx + s * dy) / size,
        -s / size,
        c / size,
        my + (-s * dx + c * dy) / size,
    ];
    let laid = held
        .transform(
            (ow as usize, oh as usize),
            lp_pixels::Transform::Affine(data),
            Filter::Bilinear,
            None,
        )
        .map_err(failed)?;
    // (back to plain colours, its alpha `alpha` of itself, and laid through it: in one pass)
    let fade = if alpha < 0.999 {
        // (Python's round of each: where one is no number, the first is none either)
        round_int(0.0 * alpha)?;
        let mut lut = [0u8; 256];
        for (v, slot) in lut.iter_mut().enumerate() {
            *slot = (v as f64 * alpha).round_ties_even().clamp(0.0, 255.0) as u8;
        }
        Some(lut)
    } else {
        None
    };
    out.paste_unpremultiplied(&laid, ox, oy, fade.as_ref())
        .map_err(failed)
}

/// An RGB picture in RGBa with a clear pixel round it: what pasting it at (1, 1) into a blank
/// RGBa picture two pixels wider and taller gives (its colours of alpha 255), made row by row.
fn held_premultiplied(face: &Image) -> Result<Image> {
    let (w, h) = (face.width(), face.height());
    let (pw, ph) = (w + 2, h + 2);
    let mut bytes = Vec::with_capacity(pw * ph * 4);
    bytes.resize(pw * 4, 0);
    for row in face
        .as_bytes()
        .chunks_exact((w * 3).max(1))
        .take(h * usize::from(w > 0))
    {
        bytes.extend_from_slice(&[0; 4]);
        for p in row.chunks_exact(3) {
            bytes.extend_from_slice(&[p[0], p[1], p[2], 255]);
        }
        bytes.extend_from_slice(&[0; 4]);
    }
    bytes.resize(pw * ph * 4, 0);
    Image::from_bytes(Mode::RGBa, pw, ph, bytes).map_err(failed)
}

/// _joined: boxes ([x0, y0, x1, y1]) joined where they overlap, until none do: a screen's cards.
fn joined(boxes: &[[i64; 4]]) -> Vec<[i64; 4]> {
    let mut out = boxes.to_vec();
    'again: loop {
        for i in 0..out.len() {
            for j in i + 1..out.len() {
                let (a, b) = (out[i], out[j]);
                if a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3] {
                    out[i] = [
                        a[0].min(b[0]),
                        a[1].min(b[1]),
                        a[2].max(b[2]),
                        a[3].max(b[3]),
                    ];
                    out.remove(j);
                    continue 'again;
                }
            }
        }
        return out;
    }
}

/// _spaced: each card with a margin around it (its shadow, its glow), within the panel and
/// short of the cards beside it (to the middle of the gap between).
fn spaced(cards: &[[i64; 4]], mx: i64, my: i64, w: i64, h: i64) -> Vec<[i64; 4]> {
    cards
        .iter()
        .enumerate()
        .map(|(i, a)| {
            let mut e = [
                0.max(a[0] - mx),
                0.max(a[1] - my),
                w.min(a[2] + mx),
                h.min(a[3] + my),
            ];
            for (j, b) in cards.iter().enumerate() {
                if j == i || !(e[0] < b[2] && b[0] < e[2] && e[1] < b[3] && b[1] < e[3]) {
                    continue;
                }
                if b[0] >= a[2] {
                    e[2] = e[2].min(a[2].max((a[2] + b[0]).div_euclid(2))); // (to the right)
                } else if b[2] <= a[0] {
                    e[0] = e[0].max(a[0].min((b[2] + a[0] + 1).div_euclid(2))); // (to the left)
                } else if b[1] >= a[3] {
                    e[3] = e[3].min(a[3].max((a[3] + b[1]).div_euclid(2))); // (below)
                } else if b[3] <= a[1] {
                    e[1] = e[1].max(a[1].min((b[3] + a[1] + 1).div_euclid(2))); // (above)
                }
            }
            e
        })
        .collect()
}

/// What a cascade works out once for a frame's size: the new screen's ground and its cards
/// (each a box with its margin, and when along the way it comes, 0 to 1).
struct Laid {
    size: (usize, usize),
    ground: Image,
    cards: Vec<([i64; 4], f64)>,
}

/// Cascade's constants
const CASCADE_GROUND: f64 = 0.28;
const CASCADE_MARGIN_MM: f64 = 1.2;
const CASCADE_RISE_MM: f64 = 9.0;
const CASCADE_SPREAD: f64 = 0.42;
const CASCADE_COMES: f64 = 0.34;
const CASCADE_LAST: f64 = 0.84;

/// Cascade._cards: from what the new screen's renderer composed last.
fn laid_out(new: &Image, canvas: Option<&Canvas>, renderer: &Renderer) -> Result<Laid> {
    let size = (new.width(), new.height());
    let (ground, boxes) = match renderer.beneath()? {
        Some((ground, boxes)) if ground.size() == size => (ground, boxes),
        // (none known: it fades, as one)
        _ => (rgb(new)?.into_owned(), Vec::new()),
    };
    let (w, h) = (size.0 as i64, size.1 as i64);
    let canvas = canvas.copied().unwrap_or_else(|| alone(size));
    let (sx, sy) = per_mm(&canvas, size);
    let cards = joined(&boxes);
    let (mx, my) = (
        w.min(round_int(CASCADE_MARGIN_MM * sx)?),
        h.min(round_int(CASCADE_MARGIN_MM * sy)?),
    );
    let way = way_of(&canvas);
    let (a, b) = span(canvas.whole, way);
    let laid = cards
        .iter()
        .zip(spaced(&cards, mx, my, w, h))
        .map(|(card, bounds)| {
            let cx = canvas.own.0 + (card[0] + card[2]) as f64 / 2.0 / sx;
            let cy = canvas.own.1 + (card[1] + card[3]) as f64 / 2.0 / sy;
            let q = (cx * way.0 as f64 + cy * way.1 as f64 - a) / py_max(0.001, b - a);
            (bounds, q)
        })
        .collect();
    Ok(Laid {
        size,
        ground,
        cards: laid,
    })
}

/// Cascade: the ground changes, then the new screen's cards come in one after another, each
/// rising and growing into its place, as a wave along all the panels or this one; what lies on
/// no card comes with them at the end.
fn cascade(
    kept: &mut Option<Laid>,
    old: &Image,
    new: &Image,
    t: f64,
    canvas: Option<&Canvas>,
    renderer: &Renderer,
) -> Result<Image> {
    if t >= 1.0 {
        return Ok(new.clone());
    }
    let size = (new.width(), new.height());
    if kept.as_ref().is_none_or(|laid| laid.size != size) {
        *kept = Some(laid_out(new, canvas, renderer)?);
    }
    let laid = kept.as_ref().expect("laid");
    let changed = smooth(t / CASCADE_GROUND);
    let mut out = if changed >= 1.0 {
        laid.ground.clone()
    } else {
        chops::blend(&*rgb(old)?, &laid.ground, changed as f32).map_err(failed)?
    };
    let rise = CASCADE_RISE_MM * per_mm(&canvas.copied().unwrap_or_else(|| alone(size)), size).1;
    let picture = rgb(new)?;
    for &(bounds, q) in &laid.cards {
        let e = ease_out((t - (0.2 + q * CASCADE_SPREAD)) / CASCADE_COMES);
        if e <= 0.0 {
            continue;
        }
        let mut part = crop(&picture, (bounds[0], bounds[1], bounds[2], bounds[3]));
        let grown = 0.93 + 0.07 * e;
        let (pw, ph) = (
            1.max(round_int(part.width() as f64 * grown)?),
            1.max(round_int(part.height() as f64 * grown)?),
        );
        if (pw as usize, ph as usize) != part.size() {
            part = part
                .resize(pw as usize, ph as usize, Filter::Bilinear, None, None)
                .map_err(failed)?;
        }
        let x = round_int((bounds[0] + bounds[2]) as f64 / 2.0 - pw as f64 / 2.0)?;
        let y = (bounds[1] + bounds[3]) as f64 / 2.0 + (1.0 - e) * rise - ph as f64 / 2.0;
        let y = round_int(y)?;
        let seen = smooth(e * 1.25);
        if seen >= 1.0 {
            out.paste(&part, x, y, None).map_err(failed)?;
        } else {
            let v = round_int(255.0 * seen)?.clamp(0, 255) as u8;
            let mask = Image::new(Mode::L, part.width(), part.height(), &[v]);
            out.paste(&part, x, y, Some(&mask)).map_err(failed)?;
        }
    }
    let last = smooth((t - CASCADE_LAST) / (1.0 - CASCADE_LAST));
    if last > 0.0 {
        chops::blend(&out, &picture, last as f32).map_err(failed)
    } else {
        Ok(out)
    }
}

/// Comet's constants
const COMET_MARGIN_MM: f64 = 35.0;
const COMET_SLANT: f64 = 0.38;
const COMET_TAIL_MM: f64 = 90.0;
const COMET_SPOTS: usize = 44;
const COMET_SPARKS: usize = 22;
const COMET_HEAD_MM: f64 = 17.0;
const COMET_LAG_MM: f64 = 12.0;
const COMET_EDGE_MM: f64 = 45.0;
/// COMET_MOST: the most pixels its light may have (more: a canvas that says nothing sensible,
/// and it flies without it)
const COMET_MOST: i128 = 1 << 22;

/// What a comet works out once for a frame's size (_made_for): its way (where it begins, which
/// way it goes, how far), the map of where the new screen opens, its light and where its head
/// is in it.
struct Flown {
    size: (usize, usize),
    begin: (f64, f64),
    u: (f64, f64),
    length: f64,
    seen: Image,
    /// its light and where its head is in it (None: it flies without it)
    light: Option<(Image, i64, i64)>,
}

/// Comet._flight: its way over the canvas, from beyond one side to beyond the other, slanting
/// down, and on until its tail has left all the panels and the new screen is open on all.
fn flight(canvas: &Canvas) -> ((f64, f64), (f64, f64), f64) {
    let (x, y, w, h) = canvas.whole;
    let (cx, cy) = middle(canvas.whole);
    let m = COMET_MARGIN_MM;
    let (begin, end) = if way_of(canvas) == (1, 0) {
        (
            (x - m, cy - COMET_SLANT * h),
            (x + w + m, cy + COMET_SLANT * h),
        )
    } else {
        (
            (cx - COMET_SLANT * w, y - m),
            (cx + COMET_SLANT * w, y + h + m),
        )
    };
    let (dx, dy) = (end.0 - begin.0, end.1 - begin.1);
    let mut length = (dx * dx + dy * dy).sqrt();
    let u = (dx / length, dy / length);
    let corners = [(x, y), (x + w, y), (x, y + h), (x + w, y + h)];
    let along = |(px, py): (f64, f64)| px * u.0 + py * u.1;
    let farthest = corners[1..]
        .iter()
        .fold(along(corners[0]), |a, &c| py_max(a, along(c)));
    let behind = py_max(COMET_TAIL_MM + 1.0, COMET_LAG_MM + COMET_EDGE_MM);
    length += py_max(0.0, farthest + behind - (end.0 * u.0 + end.1 * u.1));
    (begin, u, length)
}

/// What the comet's light is made of, in the order it is added (_comet_light's parts).
#[derive(Clone, Copy, PartialEq)]
enum Part {
    Spot,
    Spark,
    Head,
}

/// A part of the comet's light: where its middle is from the head (millimetres), how far it
/// reaches, what it is and how bright.
type Lit = (f64, f64, f64, Part, f64);

/// _comet_light: the comet's light (RGB, added onto the frame), made once: its tail, spot by
/// spot in the accent, sparks of white behind its head and the head's halo, white in the
/// middle; and where its head is in it. None where it would be larger than any panel.
fn comet_light(
    accent: [i64; 3],
    white: [i64; 3],
    (sx, sy): (f64, f64),
    u: (f64, f64),
) -> Result<Option<(Image, i64, i64)>> {
    let n = (-u.1, u.0);
    let mut parts: Vec<Lit> = Vec::new();
    for i in (1..=COMET_SPOTS).rev() {
        let f = i as f64 / COMET_SPOTS as f64;
        parts.push((
            -u.0 * COMET_TAIL_MM * f,
            -u.1 * COMET_TAIL_MM * f,
            (0.42 * (1.0 - f) + 0.06) * 10.0,
            Part::Spot,
            0.32 * (1.0 - f).powf(1.4),
        ));
    }
    let mut chance = Random::new(0xC0E7);
    for _ in 0..COMET_SPARKS {
        let back = 4.0 + chance.random() * 70.0;
        let side = (chance.random() - 0.5) * 16.0 * (back / 70.0);
        let small = 0.3 + chance.random() * 0.7;
        parts.push((
            -u.0 * back + n.0 * side,
            -u.1 * back + n.1 * side,
            small,
            Part::Spark,
            0.75 * (1.0 - back / 74.0),
        ));
    }
    parts.push((0.0, 0.0, COMET_HEAD_MM, Part::Head, 1.0));
    let lowest =
        |f: &dyn Fn(&Lit) -> f64| parts[1..].iter().fold(f(&parts[0]), |a, p| py_min(a, f(p)));
    let highest =
        |f: &dyn Fn(&Lit) -> f64| parts[1..].iter().fold(f(&parts[0]), |a, p| py_max(a, f(p)));
    let left = floor_int(lowest(&|p| p.0 - p.2) * sx)? - 1;
    let top = floor_int(lowest(&|p| p.1 - p.2) * sy)? - 1;
    let right = ceil_int(highest(&|p| p.0 + p.2) * sx)? + 1;
    let bottom = ceil_int(highest(&|p| p.1 + p.2) * sy)? + 1;
    if (right as i128 - left as i128) * (bottom as i128 - top as i128) > COMET_MOST {
        return Ok(None);
    }
    let (hx, hy) = (-left, -top);
    let (lw, lh) = ((right - left) as usize, (bottom - top) as usize);
    let mut light = vec![0.0f64; lw * lh * 3];
    let accent = accent.map(|v| v as f64);
    let white = white.map(|v| v as f64);
    for &(cx, cy, r, kind, a) in &parts {
        let (px, py) = (hx as f64 + cx * sx, hy as f64 + cy * sy);
        let x0 = 0.max(floor_int(px - r * sx)?);
        let x1 = (lw as i64).min(ceil_int(px + r * sx)? + 1);
        let y0 = 0.max(floor_int(py - r * sy)?);
        let y1 = (lh as i64).min(ceil_int(py + r * sy)? + 1);
        for j in y0..y1 {
            let dy = (j as f64 + 0.5 - py) / sy;
            for i in x0..x1 {
                let dx = (i as f64 + 0.5 - px) / sx;
                let d = distance(dx, dy);
                let at = (j as usize * lw + i as usize) * 3;
                match kind {
                    Part::Spot => {
                        // (the accent, fading out to its rim)
                        let k = a * (1.0 - d / r).max(0.0);
                        for c in 0..3 {
                            light[at + c] += accent[c] * k;
                        }
                    }
                    Part::Spark => {
                        // (a small disc of white, its rim smooth)
                        let k = a * ((r - d) * sx + 0.5).clamp(0.0, 1.0);
                        for c in 0..3 {
                            light[at + c] += white[c] * k;
                        }
                    }
                    Part::Head => {
                        // (the halo: white in the middle, the accent, nothing at its rim)
                        let g = d / r;
                        for c in 0..3 {
                            let (v0, v1, v2) = (white[c] * 0.95, accent[c] * 0.9, accent[c] * 0.25);
                            light[at + c] += if g < 0.1 {
                                v0 + (v1 - v0) * (g / 0.1)
                            } else if g < 0.35 {
                                v1 + (v2 - v1) * ((g - 0.1) / 0.25)
                            } else if g < 1.0 {
                                v2 * (1.0 - (g - 0.35) / 0.65)
                            } else {
                                0.0
                            };
                        }
                    }
                }
            }
        }
    }
    let bytes = light.iter().map(|&v| level(v)).collect();
    let image = Image::from_bytes(Mode::RGB, lw, lh, bytes).map_err(failed)?;
    Ok(Some((image, hx, hy)))
}

/// _add: add `light` onto `out` with its top left at `x`, `y`, where it lies on it.
fn add_onto(out: &mut Image, light: &Image, x: i64, y: i64) -> Result<()> {
    let (w, h) = (out.width() as i64, out.height() as i64);
    let b = (
        0.max(x),
        0.max(y),
        w.min(x + light.width() as i64),
        h.min(y + light.height() as i64),
    );
    if b.2 > b.0 && b.3 > b.1 {
        let part = crop(light, (b.0 - x, b.1 - y, b.2 - x, b.3 - y));
        let lit = chops::add(&crop(out, b), &part, 1.0, 0).map_err(failed)?;
        out.paste(&lit, b.0, b.1, None).map_err(failed)?;
    }
    Ok(())
}

/// Comet: a comet flies across, from one panel into the next or across this one, and the new
/// screen opens behind it.
fn comet(
    kept: &mut Option<Box<Flown>>,
    old: &Image,
    new: &Image,
    t: f64,
    canvas: Option<&Canvas>,
    c: &Colours,
) -> Result<Image> {
    if t >= 1.0 {
        return Ok(new.clone());
    }
    let size = (new.width(), new.height());
    let canvas = canvas.copied().unwrap_or_else(|| alone(size));
    if kept.as_ref().is_none_or(|flown| flown.size != size) {
        let (begin, u, length) = flight(&canvas);
        let start = begin.0 * u.0 + begin.1 * u.1 - COMET_LAG_MM;
        let seen = linear_map(&canvas, size, u, start - COMET_EDGE_MM, start + length)?;
        let light = comet_light(c.info, c.text, per_mm(&canvas, size), u)?;
        *kept = Some(Box::new(Flown {
            size,
            begin,
            u,
            length,
            seen,
            light,
        }));
    }
    let flown = kept.as_ref().expect("flown");
    let far = flown.length + COMET_EDGE_MM;
    let s = in_out_sine(t) * flown.length;
    let level = 255.0 * (s + COMET_EDGE_MM) / far;
    let mask = flown
        .seen
        .point(&threshold(level, 255.0 * COMET_EDGE_MM / far)?)
        .map_err(failed)?;
    let mut out = chops::composite(&*rgb(new)?, &*rgb(old)?, &mask).map_err(failed)?;
    if let Some((light, hx, hy)) = &flown.light {
        let at = (flown.begin.0 + flown.u.0 * s, flown.begin.1 + flown.u.1 * s);
        let head = pixel(&canvas, size, at);
        let (x, y) = (round_int(head.0)? - hx, round_int(head.1)? - hy);
        add_onto(&mut out, light, x, y)?;
    }
    Ok(out)
}

/// Portal's constants
const PORTAL_EDGE_MM: f64 = 2.0;
const PORTAL_GROWN: f64 = 0.12;
const PORTAL_GLOW_MM: f64 = 14.0;
/// RING_STEPS: the ring's light worked out for every 64th of a millimetre beside it
const RING_STEPS: f64 = 64.0;

/// What a portal works out once for a frame's size (_Opening): how far it opens and its middle
/// (millimetres), the map of the opening, where the new screen grows about (pixels), the
/// middles of the panel's columns and rows (millimetres), the pixels per millimetre and the
/// ring's light beside it at its full strength (in the accent, in white).
struct Opening {
    size: (usize, usize),
    at: (f64, f64),
    far: f64,
    map: Image,
    near: (f64, f64),
    xs: Vec<f64>,
    ys: Vec<f64>,
    per_mm: f64,
    light: (Vec<f64>, Vec<f64>),
}

/// _ring_light: the ring's light beside it at its full strength, for each step from `-reach`
/// to `reach` (`spx` pixels a millimetre): in the accent, a faint wide band, its bright line and
/// the line's glow (blurred by a Gaussian of 4 mm, widened by the line); in white, a thin line in
/// its middle. A band's edge covers as much of a pixel as lies within it.
fn ring_light(reach: f64, spx: f64) -> Result<(Vec<f64>, Vec<f64>)> {
    let spread = 2.0 * 4.05 * 4.05;
    let steps = round_int(2.0 * reach * RING_STEPS)?.max(0);
    let mut tinted = Vec::with_capacity(steps as usize);
    let mut whitened = Vec::with_capacity(steps as usize);
    for k in 0..steps {
        let off = (k as f64 + 0.5) / RING_STEPS - reach;
        let away = off.abs();
        let band = |half: f64| py_min(1.0, py_max(0.0, (half - away) * spx + 0.5));
        let glow = 0.22 * (-off.powf(2.0) / spread).exp();
        tinted.push(0.18 * band(4.5) + glow + 0.85 * band(1.1));
        whitened.push(0.8 * band(0.25));
    }
    Ok((tinted, whitened))
}

fn opening(canvas: &Canvas, size: (usize, usize)) -> Result<Opening> {
    let at = middle(canvas.whole);
    let far = reach(canvas.whole, at) + PORTAL_EDGE_MM;
    let (x, y, w, h) = canvas.own;
    // (it grows about the middle, or the nearest point of this panel to it)
    let near = (
        py_min(py_max(at.0, x), x + w),
        py_min(py_max(at.1, y), y + h),
    );
    let (xs, ys) = centres(canvas, size);
    Ok(Opening {
        size,
        at,
        far,
        map: radial_map(canvas, size, at, far)?,
        near: pixel(canvas, size, near),
        xs,
        ys,
        per_mm: per_mm(canvas, size).0,
        light: ring_light(PORTAL_GLOW_MM, per_mm(canvas, size).0)?,
    })
}

/// Portal: a ring of light opens from the middle (of all the panels), and the new screen
/// grows through it into its place.
fn portal(
    kept: &mut Option<Box<Opening>>,
    old: &Image,
    new: &Image,
    t: f64,
    canvas: Option<&Canvas>,
    c: &Colours,
) -> Result<Image> {
    if t >= 1.0 {
        return Ok(new.clone());
    }
    let size = (new.width(), new.height());
    if kept.as_ref().is_none_or(|o| o.size != size) {
        let canvas = canvas.copied().unwrap_or_else(|| alone(size));
        *kept = Some(Box::new(opening(&canvas, size)?));
    }
    let o = kept.as_ref().expect("opening");
    let e = in_out_sine(t);
    let mask = o
        .map
        .point(&threshold(255.0 * e, 255.0 * PORTAL_EDGE_MM / o.far)?)
        .map_err(failed)?;
    let mut out = rgb(old)?.into_owned();
    if let Some((b0, b1, b2, b3)) = mask.getbbox(true) {
        // (the new screen there, grown about the middle)
        let (w, h) = (size.0 as f64, size.1 as f64);
        let z = 1.0 + PORTAL_GROWN * (1.0 - e);
        let (zx, zy) = o.near;
        let within = |v: f64, most: f64| py_min(most, py_max(0.0, v));
        let source = (
            within(zx + (b0 as f64 - zx) / z, w),
            within(zy + (b1 as f64 - zy) / z, h),
            within(zx + (b2 as f64 - zx) / z, w),
            within(zy + (b3 as f64 - zy) / z, h),
        );
        let grown = rgb(new)?
            .resize(b2 - b0, b3 - b1, Filter::Bilinear, Some(source), None)
            .map_err(failed)?;
        let part = mask.crop(b0 as i64, b1 as i64, b2 as i64, b3 as i64);
        out.paste(&grown, b0 as i64, b1 as i64, Some(&part))
            .map_err(failed)?;
    }
    let strength = (std::f64::consts::PI * clamp(t)).sin().powf(0.6);
    if strength > 0.004 {
        ring(&mut out, o, e * o.far, strength, c.info, c.text)?;
    }
    Ok(out)
}

/// _ring: the ring of light at `r` millimetres from the middle, `strength` of it, added onto
/// `out`: a faint wide band and a bright line with its glow in the accent, a thin white line
/// in its middle. Only the pixels as near it as its light reaches: in each row those within
/// reach of the circle (a pixel more either side), each then asked as Python asks it, and lit
/// as its step beside it.
fn ring(
    out: &mut Image,
    o: &Opening,
    r: f64,
    strength: f64,
    accent: [i64; 3],
    white: [i64; 3],
) -> Result<()> {
    let reach = PORTAL_GLOW_MM;
    let (w, bands) = (out.width(), out.bands());
    let slack = 1.0 / o.per_mm;
    // (each step's light in each colour, at this strength)
    let (tinted, whitened) = &o.light;
    let lit: Vec<[u8; 3]> = tinted
        .iter()
        .zip(whitened)
        .map(|(&t, &w)| {
            let (t, w) = (strength * t, strength * w);
            [0, 1, 2].map(|c| level(accent[c] as f64 * t + white[c] as f64 * w))
        })
        .collect();
    let outer = r + reach;
    let inner = r - reach;
    for (j, &y) in o.ys.iter().enumerate() {
        let dy = y - o.at.1;
        if dy.abs() > outer + slack {
            continue;
        }
        let wide = (outer * outer - dy * dy).max(0.0).sqrt() + slack;
        let hole = if inner > dy.abs() {
            (inner * inner - dy * dy).max(0.0).sqrt() - slack
        } else {
            -1.0
        };
        let first = o.xs.partition_point(|&x| x < o.at.0 - wide);
        let last = o.xs.partition_point(|&x| x <= o.at.0 + wide);
        for i in first..last {
            let dx = o.xs[i] - o.at.0;
            if dx.abs() < hole {
                continue;
            }
            let off = distance(dx, dy) - r;
            let near = off.abs() < reach; // (what is no number at all, not)
            if !near {
                continue;
            }
            // (a pixel a rounding short of its reach takes the last step, not one past it)
            let light = lit[(((off + reach) * RING_STEPS) as usize).min(lit.len() - 1)];
            let at = (j * w + i) * bands;
            let pixels = out.as_bytes_mut();
            for c in 0..3 {
                pixels[at + c] = pixels[at + c].saturating_add(light[c]);
            }
        }
    }
    Ok(())
}

// -- the morph: what both screens show flies to its new place ---------------------------------

/// Morph.OUT, IN, LAST, FLIGHT, TURN and LIFT_MM
const MORPH_OUT: f64 = 0.55;
const MORPH_IN: (f64, f64) = (0.35, 0.9);
const MORPH_LAST: f64 = 0.9;
const MORPH_FLIGHT: (f64, f64) = (0.06, 0.9);
const MORPH_TURN: (f64, f64) = (0.35, 0.65);
const MORPH_LIFT_MM: f64 = 7.0;

/// Morph: the old screen (its renderer's parting, until taken apart) and what was made of both.
#[derive(Default)]
pub struct Morph {
    before: Option<crate::morph::Parting>,
    made: Option<Flights>,
}

/// Morph._made: the size, the old screen without what flies, the new one without it, the
/// things that fly (each its old part and its new one, the largest first) and the dark.
struct Flights {
    size: (usize, usize),
    old_without: Image,
    new_without: Image,
    flights: Vec<(crate::morph::Part, crate::morph::Part)>,
    dark: Image,
}

impl Morph {
    /// _flights: worked out once both screens are known; None where there is nothing to take
    /// apart (the old screen unknown, or of another size): it fades.
    fn flights(&mut self, new: &Image, renderer: &Renderer) -> Result<Option<&Flights>> {
        use crate::morph::{take_apart, things};
        let size = (new.width(), new.height());
        if self.made.is_none() {
            let Some(before) = self.before.as_ref().filter(|b| b.size() == size) else {
                return Ok(None);
            };
            let Some(after) = renderer.parting()?.filter(|a| a.size() == size) else {
                return Ok(None); // (not drawn yet: asked again at the next frame)
            };
            let (old_things, new_things) = (things(before), things(&after));
            let area = |b: (i64, i64, i64, i64)| (b.2 - b.0) * (b.3 - b.1);
            let mut shared: Vec<&String> = (old_things.keys())
                .filter(|k| new_things.contains_key(*k))
                .collect();
            shared.sort_by(|a, b| {
                (area(old_things[*b].1).cmp(&area(old_things[*a].1))).then_with(|| a.cmp(b))
            });
            let taken = |found: &std::collections::HashMap<String, crate::morph::Found>| {
                (shared.iter())
                    .map(|k| ((*k).clone(), found[*k].clone()))
                    .collect::<Vec<_>>()
            };
            let (old_without, mut old_parts) = take_apart(before, &taken(&old_things))?;
            let (new_without, mut new_parts) = take_apart(&after, &taken(&new_things))?;
            let flights = (shared.iter())
                .filter_map(|k| Some((old_parts.remove(*k)?, new_parts.remove(*k)?)))
                .collect();
            let dark = Image::new(Mode::RGB, size.0, size.1, &[0, 0, 0]);
            self.made = Some(Flights {
                size,
                old_without,
                new_without,
                flights,
                dark,
            });
            self.before = None; // (what it held is in the parts now)
        }
        Ok(self.made.as_ref().filter(|m| m.size == size))
    }

    fn frame(&mut self, old: &Image, new: &Image, t: f64, renderer: &Renderer) -> Result<Image> {
        if t >= 1.0 {
            return Ok(new.clone());
        }
        let Some(made) = self.flights(new, renderer)? else {
            // (nothing to take apart: a fade)
            return chops::blend(&*rgb(old)?, &*rgb(new)?, smooth(t) as f32).map_err(failed);
        };
        let gone = 1.0 - smooth(t / MORPH_OUT);
        let mut out = if gone <= 0.0 || gone >= 1.0 {
            (if gone <= 0.0 {
                &made.dark
            } else {
                &made.old_without
            })
            .clone()
        } else {
            chops::blend(&made.dark, &made.old_without, gone as f32).map_err(failed)?
        };
        let come = smooth((t - MORPH_IN.0) / (MORPH_IN.1 - MORPH_IN.0));
        out = over(out, &made.new_without, come)?;
        let (a, b) = MORPH_FLIGHT;
        let e = in_out((t - a) / (b - a));
        let turned = smooth((e - MORPH_TURN.0) / (MORPH_TURN.1 - MORPH_TURN.0));
        let high = MORPH_LIFT_MM * per_mm(&alone(made.size), made.size).1;
        let lift = high * (std::f64::consts::PI * e).sin();
        for (was, comes) in &made.flights {
            let (ax, ay, aw, ah) = was.bounds;
            let (bx, by, bw, bh) = comes.bounds;
            let (ax, ay, aw, ah) = (ax as f64, ay as f64, aw as f64, ah as f64);
            let (bx, by, bw, bh) = (bx as f64, by as f64, bw as f64, bh as f64);
            // (up where it has room for its arc)
            let rise = if py_min(ay, by) >= high { -lift } else { lift };
            let middle = (
                ax + aw / 2.0 + (bx + bw / 2.0 - ax - aw / 2.0) * e,
                ay + ah / 2.0 + (by + bh / 2.0 - ay - ah / 2.0) * e + rise,
            );
            let room = (aw + (bw - aw) * e, ah + (bh - ah) * e);
            crate::morph::lay(&mut out, was, middle, room, 1.0 - turned)?;
            crate::morph::lay(&mut out, comes, middle, room, turned)?;
        }
        let last = smooth((t - MORPH_LAST) / (1.0 - MORPH_LAST));
        over(out, &*rgb(new)?, last)
    }
}

/// _mixed: `b` over `a` by `share`: `a` at none, `b` at all of it (a frame copied no more
/// than it must be).
fn over(a: Image, b: &Image, share: f64) -> Result<Image> {
    if share <= 0.0 {
        Ok(a)
    } else if share >= 1.0 {
        Ok(b.clone())
    } else {
        chops::blend(&a, b, share as f32).map_err(failed)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_rings_light_ends_at_its_reach() {
        // (a pixel a rounding short of the ring's reach takes the light's last step, not one
        // past it: the ring at radii where some pixel's offset is the float just below it)
        let size = (64, 64);
        let o = opening(&alone(size), size).unwrap();
        let steps = o.light.0.len();
        let mut edges = Vec::new();
        for &y in &o.ys {
            for &x in &o.xs {
                let d = distance(x - o.at.0, y - o.at.1);
                if d <= PORTAL_GLOW_MM + 2.0 {
                    continue; // (the ring then on the panel, and lit)
                }
                let base = d - PORTAL_GLOW_MM;
                for k in -40i64..40 {
                    let r = base + k as f64 * base.abs().max(1e-300) * f64::EPSILON;
                    let off = d - r;
                    if off.abs() < PORTAL_GLOW_MM
                        && ((off + PORTAL_GLOW_MM) * RING_STEPS) as usize >= steps
                    {
                        edges.push(r);
                    }
                }
            }
        }
        assert!(!edges.is_empty());
        for &r in edges.iter().take(20) {
            let mut out = Image::blank(Mode::RGB, size.0, size.1);
            ring(&mut out, &o, r, 1.0, [46, 230, 214], [255, 255, 255]).unwrap();
            assert!(out.as_bytes().iter().any(|&v| v > 0));
        }
    }
}

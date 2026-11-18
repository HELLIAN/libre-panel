"""Light that moves over what a panel shows, on a panel that moves (``[shine]`` in config.toml,
Settings → Panel → Moving light; a theme names the kinds it wants in ``animation.shine``):

- ``rings``: a ring gauge's end breathes in the ring's colour, and every :data:`SWEEP_S` a
  light runs along the lit arc to it (the rings of a panel one after the other); above
  :data:`HOT` of its scale the end glows wider, the more the fuller the ring.
- ``graphs``: a graph's newest point beats once a reading, a ring of light going out from it.
- ``music``: while a track plays, its cover glows round about in its own colours (steady:
  a glow that big breathing in steps would be seen to step, and it would be made and sent
  anew at each); three bars dance after its state ("Playing"); its progress has a light at
  its head and a sheen runs along it.
- ``downloads``: a sheen runs along a download's progress, the faster the faster it loads, and
  its head glows.
- ``sun``: the sun on its way (by night the moon) breathes in its light, and a glint runs along
  the way the day has gone.

What it costs: each light is a layer of light added onto the frame (``Piece.add``, as the card
slot's line of light), made once for a look in :data:`LEVELS` strengths and kept; where a light
stands as it stood the frame before it is the same piece, so nothing is composed or sent anew
(the engine lays it as it lays the line of light). What breathes changes in steps of strength,
a few times a second, and tells the main loop when (``Renderer.due``) so that a panel at rest
rests between; only what runs (a sweep, a sheen, a glint) moves a frame each tick, for a second
or so. Nothing of it is drawn on a calm panel (``Renderer.animate`` off), in the editor's
pictures, or for a widget that is turned.

The lights are made of a few sums and products of floats: Rust's ``shine.rs`` makes the same
bytes.
"""

from __future__ import annotations

import math
import weakref
from collections.abc import Callable
from typing import Any

import numpy as np
from PIL import Image

from libre_panel.render.kept import MB, Kept

KINDS = ("rings", "graphs", "music", "downloads", "sun")

LEVELS = 12  # the strengths a light is made in: a breath changes a step at a time
REACH = 2.25  # where a light of radius r ends: r times this
CORE = 3.0  # a light's white core: its reach this much smaller

BREATH_S = 4.0  # one breath, in and out (all breathe together: their steps fall together)
SWEEP_S, SWEEP_RUN_S, SWEEP_NEXT_S = 4.0, 1.3, 0.4  # a ring's sweep: each, its run, the next's
HOT = 0.8  # where a ring's end begins to glow wider
BEAT_RUN_S, BEAT_STEPS = 0.8, 10  # a graph's beat: its ring goes out in this, in these steps
SHEEN_S, SHEEN_RUN_S = 2.0, 1.2  # the progress's sheen
GLINT_S, GLINT_RUN_S, GLINT_AFTER_S = 4.0, 2.2, 1.0  # the sun's glint along its way
DANCE_HZ = 12.5  # the bars after "Playing" change this often
FADE_S = 0.6  # the cover's glow comes and goes in this
COVER_GAIN = 1.9  # the cover's glow: its colours this much brighter, at full strength
COVER_SHARE = 0.6  # the cover's glow while a track plays, of its full strength
LOOK_AHEAD_S = 0.5  # how far a light at rest looks for its next step

SPRITE_BYTES = 6 * MB  # the lights kept, made once in each strength


# -- time --------------------------------------------------------------------------------------


def phase(now: float, period: float, offset: float = 0.0) -> float:
    """Where ``now`` stands in a cycle of ``period`` seconds begun at ``offset``: 0 to below 1."""
    u = (now - offset) / period
    return u - math.floor(u)


def wave(u: float) -> float:
    """A breath over a cycle: 0 at its ends, 1 halfway, smooth at both."""
    tri = 1.0 - abs(2.0 * u - 1.0)
    return tri * tri * (3.0 - 2.0 * tri)


def ease(u: float) -> float:
    """0 to 1, slow at both ends."""
    u = min(1.0, max(0.0, u))
    return u * u * (3.0 - 2.0 * u)


def hump(u: float) -> float:
    """0 at 0 and 1, 1 halfway."""
    return 4.0 * u * (1.0 - u)


def level(strength: float) -> int:
    """The step of :data:`LEVELS` a strength from 0 to 1 is made in."""
    return math.floor(min(1.0, max(0.0, strength)) * LEVELS + 0.5)


def _next_step(of: Callable[[float], Any], now: float) -> float | None:
    """When ``of`` (a light's look at a time) next differs from its look at ``now``, within
    :data:`LOOK_AHEAD_S`; None: not as soon."""
    first = of(now)
    step = 1.0 / 50.0
    before, at = now, now + step
    while at - now <= LOOK_AHEAD_S:
        if of(at) != first:
            for _ in range(8):  # (to a fraction of a frame)
                middle = (before + at) / 2.0
                if of(middle) != first:
                    at = middle
                else:
                    before = middle
            return at
        before, at = at, at + step
    return None


# -- the lights --------------------------------------------------------------------------------

# a part of a light: ("spot", radius, (r, g, b), core, gain): a round light falling off from its
# middle, with a white core of ``core``; ("ring", radius, (r, g, b), width, gain): a ring of
# light ``width`` px either side of its radius
Part = tuple[str, float, tuple[int, int, int], float, float]


def _half(parts: tuple[Part, ...]) -> int:
    """Half the side of the square the parts' light fills (its middle pixel left out)."""
    reach = 0.0
    for kind, radius, _colour, extra, _gain in parts:
        reach = max(reach, radius * REACH if kind == "spot" else radius + extra)
    return max(1, math.ceil(reach))


def make(parts: tuple[Part, ...], strength: int) -> Image.Image:
    """The light of ``parts`` at ``strength`` of :data:`LEVELS` (RGB, its middle at the middle
    pixel): each part's light summed in turn, times the strength, to a byte."""
    half = _half(parts)
    d = np.arange(-half, half + 1, dtype=np.float64)
    q = d[None, :] * d[None, :] + d[:, None] * d[:, None]  # (whole numbers: exact)
    total = np.zeros((2 * half + 1, 2 * half + 1, 3), np.float64)
    for kind, radius, colour, extra, gain in parts:
        if kind == "spot":
            reach = radius * REACH
            t = np.maximum(1.0 - q / (reach * reach), 0.0)
            t2 = t * t
            fall = t2 * t2
            core = None
            if extra > 0.0:
                inner = reach / CORE
                c = np.maximum(1.0 - q / (inner * inner), 0.0)
                c2 = c * c
                core = c2 * c2
            for ch in range(3):
                v = float(colour[ch]) * fall
                if core is not None:
                    v = v + 255.0 * extra * core
                total[..., ch] = total[..., ch] + v * gain
        else:
            e = (np.sqrt(q) - radius) / extra
            t = np.maximum(1.0 - e * e, 0.0)
            fall = t * t
            for ch in range(3):
                total[..., ch] = total[..., ch] + float(colour[ch]) * fall * gain
    scaled = np.minimum(total * (strength / LEVELS), 255.0)
    return Image.fromarray(np.floor(scaled + 0.5).astype(np.uint8), "RGB")


def _rgb(colour: Any) -> tuple[int, int, int]:
    return (int(colour[0]), int(colour[1]), int(colour[2]))


def _lerp3(a: Any, b: Any, t: float) -> tuple[int, int, int]:
    t = min(1.0, max(0.0, t))
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))  # type: ignore[return-value]


def _round(v: float) -> int:
    return math.floor(v + 0.5)


# -- the shine of a renderer -------------------------------------------------------------------


class Shine:
    """The moving light of one renderer's frames: :meth:`over` gives a widget's lights, laid
    over it (named, so that a card slot can carry them with its card)."""

    def __init__(self, renderer: Any) -> None:
        self.renderer = weakref.proxy(renderer)  # (it holds the shine: no cycle)
        self.kinds: frozenset[str] = frozenset()  # what shines (the app's: config and theme)
        self._sprites = Kept("shine", SPRITE_BYTES)
        self._laid: dict[str, tuple[Image.Image, int, int, Any]] = {}  # name: the piece laid
        self._rings = 0  # the rings laid in this frame (each sweeps after the one before)
        self._used: set[str] = set()  # the lights laid in this frame
        self._fades: dict[str, tuple[bool, float]] = {}  # a cover's glow: on, since
        self._runs: dict[str, tuple[float, float]] = {}  # a download's sheen: when, where

    def begin(self) -> None:
        """A frame begins."""
        self._rings = 0
        self._used: set[str] = set()

    def end(self) -> None:
        """A frame is laid: the lights of widgets no longer on show go."""
        for name in [n for n in self._laid if n not in self._used]:
            del self._laid[name]

    def clear(self) -> None:
        """Forget every light made (a theme goes)."""
        self._sprites.clear()
        self._laid.clear()
        self._fades.clear()
        self._runs.clear()

    # -- laying ---------------------------------------------------------------------------------

    def _sprite(self, parts: tuple[Part, ...], strength: int) -> Image.Image | None:
        if strength <= 0:
            return None
        return self._sprites.make((parts, strength), lambda: make(parts, strength))

    def _lay(self, name: str, sprite: Image.Image | None, cx: float, cy: float) -> Any:
        """``sprite`` (light) with its middle at (``cx``, ``cy``)."""
        if sprite is None:
            self._laid.pop(name, None)
            return None
        half = sprite.width // 2
        return self._place(name, sprite, _round(cx) - half, _round(cy) - half, True)

    def _place(self, name: str, sprite: Image.Image, x: int, y: int, add: bool) -> Any:
        """``sprite`` with its corner at (``x``, ``y``): the piece laid there before where it is
        the same."""
        from libre_panel.render.renderer import Piece

        self._used.add(name)
        hit = self._laid.get(name)
        if hit is not None and hit[0] is sprite and hit[1] == x and hit[2] == y:
            return hit[3]
        piece = Piece(sprite, x, y, add=add)
        self._laid[name] = (sprite, x, y, piece)
        return piece

    def _moving(self) -> None:
        self.renderer.moving = True

    def _rest_until(self, of: Callable[[float], Any], now: float) -> None:
        at = _next_step(of, now)
        if at is not None:
            self.renderer._due_at(at)

    # -- a widget's lights ----------------------------------------------------------------------

    def over(
        self, widget: dict[str, Any], piece: Any, snapshot: Any, now: float
    ) -> list[tuple[str, Any]]:
        """The lights over ``widget`` (drawn as ``piece``) at ``now``: (name, piece or None)."""
        kind = widget["type"]
        kinds = self.kinds
        if kind == "gauge":
            return self._ring(widget, snapshot, now) if "rings" in kinds else []
        if kind == "graph":
            return self._beat(widget, snapshot, now) if "graphs" in kinds else []
        if kind == "downloads":
            return self._download(widget, snapshot, now) if "downloads" in kinds else []
        if kind == "sunpath":
            return self._sun(widget, snapshot, now) if "sun" in kinds else []
        if "music" not in kinds:
            return []
        if kind == "image" and widget.get("src") == "@media.cover" and not widget.get("blur"):
            return self._cover(widget, piece, snapshot, now)
        if kind == "metric" and widget.get("sensor") == "media.state":
            return self._dance(widget, piece, snapshot, now)
        if kind == "bar" and widget.get("sensor") == "media.progress":
            return self._progress(widget, snapshot, now)
        return []

    def _strength(self, widget: dict[str, Any], share: float) -> int:
        return level(share * float(widget.get("opacity", 1.0)))

    # -- rings -----------------------------------------------------------------------------------

    def _ring(self, widget: dict[str, Any], snapshot: Any, now: float) -> list[tuple[str, Any]]:
        renderer = self.renderer
        wid = widget["id"]
        k = self._rings
        self._rings += 1
        value, frac = renderer._gauge_fraction(widget, snapshot, now)
        if value is None or frac <= 0.0:
            return [("head", self._lay(f"{wid}\0head", None, 0, 0)),
                    ("hot", self._lay(f"{wid}\0hot", None, 0, 0)),
                    ("sweep", self._lay(f"{wid}\0sweep", None, 0, 0))]  # fmt: skip
        a, b = renderer._rule_paint(widget, value, now)
        colour = _rgb(a) if b is None else _lerp3(a, b, frac)
        th = float(max(1, widget["thickness"]))
        w, h = widget["w"], widget["h"]
        cx, cy = widget["x"] + (w - 1) / 2, widget["y"] + (h - 1) / 2
        rx, ry = (w - th) / 2, (h - th) / 2
        start, sweep = widget["start_angle"], widget["end_angle"] - widget["start_angle"]
        end = math.radians(start + sweep * frac)
        ex, ey = cx + rx * math.cos(end), cy + ry * math.sin(end)
        head_parts: tuple[Part, ...] = (
            ("spot", 0.9 * th, colour, 0.9, 1.0),
            ("spot", 2.2 * th, colour, 0.0, 0.55),
        )

        def breath(at: float) -> float:
            return 0.72 + 0.28 * wave(phase(at, BREATH_S))

        out = [("head", self._lay(f"{wid}\0head",
                                  self._sprite(head_parts, self._strength(widget, breath(now))),
                                  ex, ey))]  # fmt: skip
        heat = (frac - HOT) / (1.0 - HOT)
        hot = None
        if heat > 0.0:
            hot_parts: tuple[Part, ...] = (("spot", 3.4 * th, colour, 0.0, 0.5),)
            hot = self._sprite(hot_parts, self._strength(widget, heat * breath(now)))
        out.append(("hot", self._lay(f"{wid}\0hot", hot, ex, ey)))
        offset = k * SWEEP_NEXT_S
        u = phase(now, SWEEP_S, offset) * SWEEP_S / SWEEP_RUN_S
        swept = None
        sx = sy = 0.0
        if u < 1.0:
            angle = math.radians(start + sweep * frac * ease(u))
            sx, sy = cx + rx * math.cos(angle), cy + ry * math.sin(angle)
            run_parts: tuple[Part, ...] = (("spot", 0.7 * th, colour, 0.6, 1.0),)
            swept = self._sprite(run_parts, self._strength(widget, 0.9 * hump(u)))
            self._moving()
        else:
            self.renderer._due_at(now + (1.0 - phase(now, SWEEP_S, offset)) * SWEEP_S)
        out.append(("sweep", self._lay(f"{wid}\0sweep", swept, sx, sy)))
        strength = self._strength
        self._rest_until(lambda at: (strength(widget, breath(at)),
                                     strength(widget, heat * breath(at)) if heat > 0 else 0),
                         now)  # fmt: skip
        return out

    # -- graphs ----------------------------------------------------------------------------------

    def _beat(self, widget: dict[str, Any], snapshot: Any, now: float) -> list[tuple[str, Any]]:
        renderer = self.renderer
        name = f"{widget['id']}\0beat"
        end = renderer._graph_end(widget, snapshot, now)
        colour = renderer.color(widget["color"])
        if end is None or colour is None:
            return [("beat", self._lay(name, None, 0, 0))]
        lw = max(1.0, float(widget.get("line_width", 2)))
        return [("beat", self._beating(name, _rgb(colour), lw, end, widget, now))]

    def _beating(self, name: str, rgb: tuple[int, int, int], lw: float, end: tuple[float, float],
                 widget: dict[str, Any], now: float) -> Any:  # fmt: skip
        """A curve's end at ``end`` beating once a reading: a light that flares and a ring of
        light going out from it, for a line ``lw`` px wide."""
        period = max(0.25, self.renderer.theme.refresh_ms / 1000.0)
        run = min(BEAT_RUN_S, 0.8 * period)

        def step(at: float) -> int:
            b = phase(at, period) * period / run
            return min(BEAT_STEPS, math.floor(b * BEAT_STEPS)) if b < 1.0 else BEAT_STEPS

        s = step(now)
        bq = s / BEAT_STEPS
        parts: tuple[Part, ...] = (
            ("spot", 2.5 * lw, rgb, 1.0, 1.0),
            ("spot", 8.0 * lw, rgb, 0.0, 0.6 * (0.55 + 0.45 * (1.0 - bq))),
        )
        if s < BEAT_STEPS:
            fade = (1.0 - bq) * (1.0 - bq)
            parts += (("ring", 2.0 * lw + 11.0 * lw * ease(bq), rgb, 1.2 * lw, 0.8 * fade),)
        sprite = self._sprite(parts, self._strength(widget, 1.0))
        self._rest_until(step, now)
        return self._lay(name, sprite, *end)

    # -- music -----------------------------------------------------------------------------------

    def _playing(self, key: str, snapshot: Any, now: float) -> float:
        """How far the light of something that plays has come (0 to 1), as it comes and goes."""
        from libre_panel.services.readings import is_playing

        on = is_playing(snapshot)
        was = self._fades.get(key)
        if was is None:
            self._fades[key] = (on, now - FADE_S)  # (a first frame: as it is)
            return 1.0 if on else 0.0
        if was[0] != on:
            gone = min(FADE_S, now - was[1])  # (a fade cut short turns where it stood)
            self._fades[key] = was = (on, now - (FADE_S - gone))
        u = ease((now - was[1]) / FADE_S)
        if u < 1.0:
            self._moving()
        return u if on else 1.0 - u

    def _cover(
        self, widget: dict[str, Any], piece: Any, snapshot: Any, now: float
    ) -> list[tuple[str, Any]]:
        wid = widget["id"]
        names = [f"{wid}\0glow{i}" for i in range(4)]
        share = self._playing(wid, snapshot, now)
        layer = getattr(piece, "layer", None)
        x, y, w, h = widget["x"], widget["y"], widget["w"], widget["h"]
        if share <= 0.0 or layer is None or min(w, h) < 16:
            return [(f"glow{i}", self._lay(n, None, 0, 0)) for i, n in enumerate(names)]
        box = (x - piece.x, y - piece.y, x - piece.x + w, y - piece.y + h)
        if box[0] < 0 or box[1] < 0 or box[2] > layer.width or box[3] > layer.height:
            return [(f"glow{i}", self._lay(n, None, 0, 0)) for i, n in enumerate(names)]
        s = self._strength(widget, share * COVER_SHARE)
        glows = self._glows(layer, box, s, float(widget.get("radius") or 0))
        out = []
        for i, (sprite, dx, dy) in enumerate(glows):
            if sprite is None:
                self._laid.pop(names[i], None)
                out.append((f"glow{i}", None))
            else:
                out.append((f"glow{i}", self._place(names[i], sprite, x + dx, y + dy, True)))
        return out

    def _glows(
        self, layer: Image.Image, box: tuple[int, int, int, int], strength: int, radius: float
    ) -> list[tuple[Image.Image | None, int, int]]:
        """The glow round the cover in ``box`` of ``layer`` (its piece's: a shadow round it
        too) at ``strength``, in four bands (above, below, left, right, where (x, y) from the
        cover's corner): made at full strength once for the cover's picture, the strength
        asked last scaled from it and kept with it (so a glow that stands is the same bands)."""
        if strength <= 0:
            return [(None, 0, 0)] * 4
        key = ("cover", id(layer), box, radius)
        hit = self._sprites.get(key)
        if hit is None or hit[0] is not layer:
            cover = layer if box == (0, 0, *layer.size) else layer.crop(box)
            full = cover_glow(cover, LEVELS, radius)
            size = sum(b[0].width * b[0].height * 4 for b in full if b[0] is not None)
            hit = [layer, full, LEVELS, full]
            self._sprites.put(key, hit, 2 * size)
        if hit[2] != strength:
            hit[2], hit[3] = strength, scaled(hit[1], strength)
        return hit[3]

    def _dance(
        self, widget: dict[str, Any], piece: Any, snapshot: Any, now: float
    ) -> list[tuple[str, Any]]:
        name = f"{widget['id']}\0dance"
        box = getattr(piece, "box", None)
        share = self._playing(f"{widget['id']}\0dance", snapshot, now)
        colour = self.renderer.color(widget.get("color"))
        if share <= 0.0 or not box or colour is None:
            self._laid.pop(name, None)
            return [("dance", None)]
        size = float(widget.get("font_size", 20))
        tick = math.floor(now * DANCE_HZ) / DANCE_HZ
        bars = dance(tick, size, share)
        sprite = self._sprites.make(("dance", bars, _rgb(colour), round(size * 1000)),
                                    lambda: dance_bars(bars, size, _rgb(colour)))  # fmt: skip
        x = box[0] + box[2] + _round(size * 0.45)
        y = box[1] + box[3] - sprite.height
        self.renderer._due_at((math.floor(now * DANCE_HZ) + 1) / DANCE_HZ)
        return [("dance", self._place(name, sprite, x, y, False))]

    def _progress(self, widget: dict[str, Any], snapshot: Any, now: float) -> list[tuple[str, Any]]:
        renderer = self.renderer
        wid = widget["id"]
        share = self._playing(wid, snapshot, now)
        value, frac = renderer._bar_fraction(widget, snapshot, now)
        if share <= 0.0 or value is None or widget.get("direction", "right") != "right":
            return [("head", self._lay(f"{wid}\0head", None, 0, 0)),
                    ("sheen", self._lay(f"{wid}\0sheen", None, 0, 0))]  # fmt: skip
        x, y, w, h = widget["x"], widget["y"], widget["w"], widget["h"]
        start, end = renderer._rule_paint(widget, value, now)
        colour = _rgb(start if end is None else end)  # (its light in its fullest colour)
        th = float(max(2, h))
        hx, hy = x + frac * w - 0.5, y + h / 2
        head_parts: tuple[Part, ...] = (
            ("spot", 0.75 * th, colour, 0.9, 1.0),
            ("spot", 2.0 * th, colour, 0.0, 0.5),
        )

        def strength(at: float) -> int:
            return self._strength(widget, share * (0.85 + 0.15 * wave(phase(at, 1.0))))

        out = [("head", self._lay(f"{wid}\0head", self._sprite(head_parts, strength(now)),
                                  hx, hy))]  # fmt: skip
        u = phase(now, SHEEN_S) * SHEEN_S / SHEEN_RUN_S
        sheen = None
        sx = 0.0
        if u < 1.0 and frac * w >= 2 * th:
            sx = x + (hx - x) * ease(u)
            sheen = self._sprite((("spot", 1.4 * th, colour, 0.3, 0.9),),
                                 self._strength(widget, share * hump(u)))  # fmt: skip
            self._moving()
        else:
            self.renderer._due_at(now + (1.0 - phase(now, SHEEN_S)) * SHEEN_S)
        out.append(("sheen", self._lay(f"{wid}\0sheen", sheen, sx, hy)))
        self._rest_until(strength, now)
        return out

    # -- downloads -------------------------------------------------------------------------------

    def _download(self, widget: dict[str, Any], snapshot: Any, now: float) -> list[tuple[str, Any]]:
        from libre_panel.render import download

        wid = widget["id"]
        names = (f"{wid}\0head", f"{wid}\0sheen", f"{wid}\0sheen2")
        gone = [*names, f"{wid}\0spark"]
        state = snapshot.state(widget["state"])
        facts = download.read(state) if isinstance(state, dict) else None
        if facts is None or facts.phase != "downloading" or facts.share is None:
            self._runs.pop(wid, None)
            return [(n.split("\0")[1], self._lay(n, None, 0, 0)) for n in gone]
        lay = download.layout(widget["w"], widget["h"])
        x0, y0, x1, y1 = download.rail(lay)
        length, thick = max(2, round(x1 - x0)), max(2, round(y1 - y0))
        frac = round(facts.share * length) / length
        bx = widget["x"] + round(x0)
        by = widget["y"] + round(y0) + thick / 2
        hx = bx + frac * length - 0.5
        colour = _rgb(download.accent(self.renderer, widget, facts.phase))
        th = float(thick)
        head_parts: tuple[Part, ...] = (
            ("spot", 0.8 * th, colour, 0.9, 1.0),
            ("spot", 2.4 * th, colour, 0.0, 0.5),
        )
        out = [("head", self._lay(names[0], self._sprite(head_parts, self._strength(
            widget, 0.7 + 0.3 * wave(phase(now, 0.5)))), hx, by))]  # fmt: skip
        rate = state.get("rate")
        rate = float(rate) if isinstance(rate, int | float) and math.isfinite(rate) else 0.0
        pace = min(1.0, max(0.0, math.log10(max(rate, 1e5) / 1e5) / 3.0))
        period = 1.8 - 1.2 * pace
        run = self._runs.get(wid)
        if run is None or now < run[0] or now - run[0] > 1.0:
            where = phase(now, period)
        else:
            where = run[1] + (now - run[0]) / period
            where -= math.floor(where)
        self._runs[wid] = (now, where)
        for i, name in enumerate(names[1:]):
            u = where + 0.5 * i
            u -= math.floor(u)
            sprite = None
            if frac * length >= 2 * th:
                sprite = self._sprite((("spot", 1.6 * th, colour, 0.25, 0.8),),
                                      self._strength(widget, hump(u)))  # fmt: skip
            out.append((name.split("\0")[1], self._lay(name, sprite, bx + (hx - bx) * u, by)))
        spark = None
        if lay.graph is not None and len(facts.history) >= 2:
            spark = self._spark(lay, facts.history)
        if spark is None:
            out.append(("spark", self._lay(f"{wid}\0spark", None, 0, 0)))
        else:
            out.append(("spark", self._beating(f"{wid}\0spark", colour, 2.0 * lay.g,
                                               (widget["x"] + spark[0], widget["y"] + spark[1]),
                                               widget, now)))  # fmt: skip
        self._moving()
        return out

    @staticmethod
    def _spark(lay: Any, history: tuple[float, ...]) -> tuple[float, float] | None:
        """Where the download's speed curve ends (px from the widget's corner), as
        ``download._graph`` draws it."""
        from libre_panel.render import download

        x0, y0, x1, y1 = lay.graph
        gw, gh = max(1, round(x1 - x0)), max(1, round(y1 - y0))
        if gw < 4 or gh < 4:
            return None
        step = max(1.0, gw / (download.HISTORY - 1))
        shown = history[-max(2, int(gw / step) + 1) :]
        peak = max(download.MB, max(shown) * 1.1)
        last = 0.25 * shown[-2] + 0.5 * shown[-1] + 0.25 * shown[-1]
        y = (gh - 1) - min(1.0, max(0.0, last / peak)) * (gh - 1)
        return round(x0) + gw - 1.0, round(y0) + y

    # -- the sun ---------------------------------------------------------------------------------

    def _sun(self, widget: dict[str, Any], snapshot: Any, now: float) -> list[tuple[str, Any]]:
        from libre_panel.render import sunpath
        from libre_panel.render.renderer import _number

        renderer = self.renderer
        wid = widget["id"]
        at = sunpath.step(snapshot.value(widget["sensor"]))
        up = snapshot.value("sun.up")
        if at is None and up is None:
            return [("body", self._lay(f"{wid}\0body", None, 0, 0)),
                    ("glint", self._lay(f"{wid}\0glint", None, 0, 0))]  # fmt: skip
        if at is None and _number(up) == 1:
            at = sunpath.STEPS // 2
        w, h = max(8, widget["w"]), max(6, widget["h"])
        way = sunpath.Way(w, h, max(1.0, float(widget["thickness"])))
        x, y = widget["x"], widget["y"]
        if at is None:  # by night: the moon
            colour = _rgb(renderer.color(widget["night"]) or (201, 211, 222, 255))
            px, py = way.at(0.5)
            radius, low, high = way.sun * 1.05, 0.4, 0.7
        else:
            colour = _rgb(renderer.color(widget["color"]) or (245, 196, 107, 255))
            px, py = way.at(at / sunpath.STEPS)
            radius, low, high = way.sun, 0.6, 1.0
        parts: tuple[Part, ...] = (
            ("spot", 2.2 * radius, colour, 0.0, 0.55),
            ("spot", 0.75 * radius, colour, 0.6, 0.6),
        )

        def strength(t: float) -> int:
            return self._strength(widget, low + (high - low) * wave(phase(t, BREATH_S)))

        out = [("body", self._lay(f"{wid}\0body", self._sprite(parts, strength(now)),
                                  x + px, y + py))]  # fmt: skip
        u = phase(now, GLINT_S, GLINT_AFTER_S) * GLINT_S / GLINT_RUN_S
        glint = None
        gx = gy = 0.0
        if at is not None and at > 0 and u < 1.0:
            gx, gy = way.at(at / sunpath.STEPS * ease(u))
            white = _lerp3(colour, (255, 255, 255), 0.6)
            glint = self._sprite((("spot", 0.9 * radius, white, 0.4, 1.0),),
                                 self._strength(widget, 0.8 * hump(u)))  # fmt: skip
            self._moving()
        elif at is not None and at > 0:
            self.renderer._due_at(now + (1.0 - phase(now, GLINT_S, GLINT_AFTER_S)) * GLINT_S)
        out.append(("glint", self._lay(f"{wid}\0glint", glint, x + gx, y + gy)))
        self._rest_until(strength, now)
        return out


# -- the cover's glow and the dancing bars -----------------------------------------------------


def _soften(grid: np.ndarray) -> np.ndarray:
    """``grid`` (rows, columns, 3) softened once along each axis by (1, 2, 1) / 4, its edges
    repeated."""
    for axis in (0, 1):
        before = np.concatenate([grid.take([0], axis), grid.take(range(grid.shape[axis] - 1),
                                                                 axis)], axis)  # fmt: skip
        after = np.concatenate([grid.take(range(1, grid.shape[axis]), axis),
                                grid.take([grid.shape[axis] - 1], axis)], axis)  # fmt: skip
        grid = (before + 2.0 * grid + after) / 4.0
    return grid


def scaled(
    bands: list[tuple[Image.Image | None, int, int]], strength: int
) -> list[tuple[Image.Image | None, int, int]]:
    """``bands`` (light at full strength) at ``strength`` of :data:`LEVELS`: each byte times
    the strength over LEVELS, to the nearest (whole numbers: Rust's the same)."""
    out: list[tuple[Image.Image | None, int, int]] = []
    for band, x, y in bands:
        if band is None or strength >= LEVELS:
            out.append((band, x, y))
            continue
        v = np.asarray(band, np.uint16)
        p = ((v * (2 * strength) + LEVELS) // (2 * LEVELS)).astype(np.uint8)
        out.append((Image.fromarray(p, "RGB") if p.any() else None, x, y))
    return out


def cover_glow(
    layer: Image.Image, strength: int, radius: float = 0.0
) -> list[tuple[Image.Image | None, int, int]]:
    """The glow round a cover (``layer``, RGBA, its corners rounded by ``radius``) in its own
    colours at ``strength`` of :data:`LEVELS`, as a blurred cover's would be: its colours
    averaged in blocks and softened, spread outwards from its edge, half as strong there as at
    full and falling off over a margin; made at full strength and :func:`scaled` to the one
    asked. In four bands round it (above and below with the rows of its rounded corners, left
    and right between), each with where its corner lies from the cover's (RGB, light to add;
    None: a band with no light)."""
    w, h = layer.size
    side = min(w, h)
    m = max(4, _round(0.23 * side))
    r = min(max(0.0, float(radius)), w / 2, h / 2)
    ri = math.ceil(r)
    block = max(1, _round(side / 12))
    rgb = np.asarray(layer.convert("RGB"), dtype=np.int64)
    rows, cols = np.arange(0, h, block), np.arange(0, w, block)
    sums = np.add.reduceat(np.add.reduceat(rgb, rows, axis=0), cols, axis=1)
    counts = (np.minimum(rows + block, h) - rows)[:, None] * (np.minimum(cols + block, w) - cols)
    grid = ((sums * 2 + counts[..., None]) // (2 * counts[..., None])).astype(np.float64)
    grid = _soften(_soften(grid))
    gh, gw = grid.shape[:2]
    W, H = w + 2 * m, h + 2 * m
    xs = np.arange(W, dtype=np.float64)
    ys = np.arange(H, dtype=np.float64)
    # how far outside the cover's rounded edge each pixel lies
    qx = np.maximum(np.maximum(m + r - xs, 0.0), xs - (m + w - 1 - r))
    qy = np.maximum(np.maximum(m + r - ys, 0.0), ys - (m + h - 1 - r))
    d = np.sqrt(qy[:, None] * qy[:, None] + qx[None, :] * qx[None, :]) - r
    t = np.maximum(1.0 - d / m, 0.0)
    fall = np.where(d > 0.0, 0.5 * (t * t), 0.0)
    # the colour of the cover's edge nearest to it
    sx = np.minimum(np.maximum(xs - m, 0.0), w - 1.0)
    sy = np.minimum(np.maximum(ys - m, 0.0), h - 1.0)
    gx = np.minimum(np.maximum((sx + 0.5) / block - 0.5, 0.0), gw - 1.0)
    gy = np.minimum(np.maximum((sy + 0.5) / block - 0.5, 0.0), gh - 1.0)
    ix0 = np.floor(gx).astype(np.int64)
    iy0 = np.floor(gy).astype(np.int64)
    fx = (gx - ix0)[None, :]
    fy = (gy - iy0)[:, None]
    ix1 = np.minimum(ix0 + 1, gw - 1)
    iy1 = np.minimum(iy0 + 1, gh - 1)
    gain = COVER_GAIN
    out = np.zeros((H, W, 3), np.float64)
    for ch in range(3):
        c = grid[..., ch]
        c00, c01 = c[iy0[:, None], ix0[None, :]], c[iy0[:, None], ix1[None, :]]
        c10, c11 = c[iy1[:, None], ix0[None, :]], c[iy1[:, None], ix1[None, :]]
        top = c00 + (c01 - c00) * fx
        bottom = c10 + (c11 - c10) * fx
        out[..., ch] = (top + (bottom - top) * fy) * fall * gain
    pixels = np.floor(np.minimum(out, 255.0) + 0.5).astype(np.uint8)
    bands = (
        (pixels[: m + ri], -m, -m),  # above, with the rows of the rounded corners
        (pixels[m + h - ri :], -m, h - ri),  # below
        (pixels[m + ri : m + h - ri, :m], -m, ri),  # left, between
        (pixels[m + ri : m + h - ri, m + w :], w, ri),  # right
    )
    return scaled([
        (Image.fromarray(np.ascontiguousarray(p), "RGB") if p.size and p.any() else None, x, y)
        for p, x, y in bands
    ], strength)  # fmt: skip


DANCE = ((0.52, 0.0), (0.41, 0.13), (0.67, 0.29))  # each bar's cycle (s) and where it starts


def dance(at: float, size: float, share: float) -> tuple[int, ...]:
    """The heights of the three bars after "Playing" at ``at``, for a text of ``size``."""
    low, high = max(2, _round(size * 0.2)), max(4, _round(size * 0.72))
    return tuple(
        low + _round((high - low) * share * wave(phase(at, period, offset)))
        for period, offset in DANCE
    )


def dance_bars(bars: tuple[int, ...], size: float, colour: tuple[int, int, int]) -> Image.Image:
    """The bars of ``bars`` heights side by side, standing on the bottom edge (RGBA)."""
    width = max(2, _round(size * 0.16))
    gap = max(1, _round(size * 0.13))
    high = max(4, _round(size * 0.72))
    image = Image.new("RGBA", (len(bars) * width + (len(bars) - 1) * gap, high), (0, 0, 0, 0))
    pixels = np.zeros((high, image.width, 4), np.uint8)
    for i, bar in enumerate(bars):
        x = i * (width + gap)
        pixels[high - bar :, x : x + width] = (*colour, 255)
    return Image.fromarray(pixels, "RGBA")

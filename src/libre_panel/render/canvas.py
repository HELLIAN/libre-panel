"""One transition across all the panels, as they stand (Settings → Panels): a slide, a wipe,
the line of light, the iris, the shatter, the cascade, the comet or the portal runs over them as
over one surface, through the gap between them, and each panel draws its own part of it, from
the same moment on.

A transition is told where this panel stands and where all of them do: ``params["canvas"]`` is
``{"own": [x, y, w, h], "whole": [x, y, w, h]}``, in millimetres (arrangement.py). Without it,
or with one that says nothing sensible, it plays on its panel alone, as it always has. On the
canvas it takes as long as :data:`CANVAS_DURATIONS` says, the pace of Settings → Transitions
stretching it as any other.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image

from libre_panel.render.light import clamp, smooth

# how long each takes across the panels (the preview's, which was liked): longer than on one
# panel, as the way is longer
CANVAS_DURATIONS = {
    "slide": 1.25,
    "slide-right": 1.25,
    "slide-up": 1.25,
    "slide-down": 1.25,
    "wipe": 1.25,
    "light": 1.4,
    "iris": 1.3,
    "shatter": 1.8,
    "cascade": 1.9,
    "comet": 1.7,
    "portal": 1.5,
    "morph": 2.4,
}
# where the content of a slide goes (x, y): "slide" brings the new screen in from the right
SLIDE_WAYS = {"slide": (-1, 0), "slide-right": (1, 0), "slide-up": (0, -1), "slide-down": (0, 1)}
SHADE_MM = 6.0  # the shadow the new screen casts on the old one at a slide's seam
SHADE = 0.45  # its darkest, at the seam
LIGHT_MARGIN_MM = 12.0  # the line of light begins and ends this far beyond the panels
# a panel by itself, for one that is drawn in millimetres alone too (the cascade, the comet, the
# portal): its shorter side taken for this, the 9.2" bar's height, as the sizes of the others
# are for a panel 480 px high and scaled with the shorter side
ALONE_MM = 56.68

Rect = tuple[float, float, float, float]


@dataclass(frozen=True)
class Canvas:
    own: Rect  # where this panel stands, in millimetres
    whole: Rect  # around all of them


def _rect(value: Any) -> Rect | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):
        return None
    try:
        x, y, w, h = (float(v) for v in value)
    except OverflowError:  # (an int too large for a float)
        return None
    if not all(math.isfinite(v) for v in (x, y, w, h)) or w <= 0 or h <= 0:
        return None
    return x, y, w, h


def canvas_of(params: Any) -> Canvas | None:
    """The canvas a transition's ``params`` give, or None."""
    spec = params.get("canvas") if isinstance(params, dict) else None
    if not isinstance(spec, dict):
        return None
    own, whole = _rect(spec.get("own")), _rect(spec.get("whole"))
    if own is None or whole is None:
        return None
    return Canvas(own, whole)


def take_canvas(kind: Any) -> Canvas | None:
    """The canvas of a built-in transition ``kind`` (its ``params``), and its duration across
    the panels set; None where it plays on its panel alone."""
    canvas = canvas_of(kind.params)
    if canvas is not None and kind.name in CANVAS_DURATIONS:
        kind.duration = CANVAS_DURATIONS[kind.name]
        return canvas
    return None


def span(rect: Rect, way: tuple[int, int]) -> tuple[float, float]:
    """Where ``rect`` begins and ends along ``way``."""
    x, y, w, h = rect
    ends = (x * way[0] + y * way[1], (x + w) * way[0] + y * way[1],
            x * way[0] + (y + h) * way[1], (x + w) * way[0] + (y + h) * way[1])  # fmt: skip
    return min(ends), max(ends)


def seam(canvas: Canvas, way: tuple[int, int], t: float, size: int) -> int:
    """Where the front that runs along ``way`` over the whole canvas is on this panel, ``t``
    of the way: in its pixels across (or down, for a way up or down), 0 to ``size``."""
    a, b = span(canvas.whole, way)
    front = a + smooth(t) * (b - a)
    x, y, w, h = canvas.own
    if way[0]:
        at = (front * way[0] - x) * size / w
    else:
        at = (front * way[1] - y) * size / h
    return min(size, max(0, round(at)))


def slide(old: Image.Image, new: Image.Image, t: float, canvas: Canvas,
          way: tuple[int, int]) -> Image.Image:  # fmt: skip
    """The new screen pushes the old one out along ``way``, its seam where the front over all
    the panels is, a soft shadow on the old one beside it."""
    w, h = new.size
    across = way[0] != 0
    size = w if across else h
    at = seam(canvas, way, t, size)
    ahead = way[0] + way[1] > 0  # (the old screen goes right or down)
    out = Image.new("RGB", (w, h))
    o, n = (at, at - size) if ahead else (at - size, at)
    out.paste(old if old.mode == "RGB" else old.convert("RGB"), (o, 0) if across else (0, o))
    out.paste(new if new.mode == "RGB" else new.convert("RGB"), (n, 0) if across else (0, n))
    if 0 < at < size:
        _shade(out, at, ahead, across, canvas)
    return out


def _shade(out: Image.Image, at: int, ahead: bool, across: bool, canvas: Canvas) -> None:
    """The shadow at the seam ``at``, on the old screen's side."""
    w, h = out.size
    size = w if across else h
    mm = canvas.own[2] if across else canvas.own[3]
    deep = max(1, round(SHADE_MM * size / mm))
    ramp = bytes(round(255 * SHADE * (1 - (i + 0.5) / deep)) for i in range(deep))
    first = at if ahead else at - deep
    if not ahead:
        ramp = ramp[::-1]
    lo, hi = max(0, first), min(size, first + deep)
    if hi <= lo:
        return
    ramp = ramp[lo - first : hi - first]
    if across:
        mask = Image.frombytes("L", (hi - lo, 1), ramp).resize((hi - lo, h))
        box = (lo, 0, hi, h)
    else:
        mask = Image.frombytes("L", (1, hi - lo), ramp).resize((w, hi - lo))
        box = (0, lo, w, hi)
    part = out.crop(box)
    out.paste(Image.composite(Image.new("RGB", part.size), part, mask), box[:2])


def wipe_map(canvas: Canvas, w: int, h: int) -> Image.Image:
    """The wipe's map on this panel: its part of one that goes from 0 at the left of the
    whole canvas to 255 at its right."""
    x, _, mw, _ = canvas.own
    left, _, width, _ = canvas.whole
    row = bytes(
        min(255, max(0, round(255 * (x + (i + 0.5) * mw / w - left) / width))) for i in range(w)
    )
    return Image.frombytes("L", (w, 1), row).resize((w, h), Image.Resampling.NEAREST)


def light_at(canvas: Canvas, t: float, w: int) -> int:
    """Where the line of light is on this panel, ``t`` of the way across all of them, in its
    pixels (it may be beyond the panel)."""
    x, _, mw, _ = canvas.own
    left, _, width, _ = canvas.whole
    front = left - LIGHT_MARGIN_MM + (width + 2 * LIGHT_MARGIN_MM) * smooth(t)
    return round((front - x) * w / mw)


# -- in millimetres: a panel by itself or over all of them ---------------------------------------


def alone(size: tuple[int, int]) -> Canvas:
    """A panel by itself as a canvas of its own (:data:`ALONE_MM`)."""
    w, h = size
    mm = ALONE_MM / min(w, h)
    rect = (0.0, 0.0, w * mm, h * mm)
    return Canvas(rect, rect)


def way_of(canvas: Canvas) -> tuple[int, int]:
    """Along the longer side of all the panels: right, or down where they stand taller than
    wide."""
    return (1, 0) if canvas.whole[2] >= canvas.whole[3] else (0, 1)


def middle(rect: Rect) -> tuple[float, float]:
    x, y, w, h = rect
    return x + w / 2, y + h / 2


def reach(rect: Rect, at: tuple[float, float]) -> float:
    """How far the farthest corner of ``rect`` is from ``at``."""
    x, y, w, h = rect
    return max(math.sqrt((cx - at[0]) * (cx - at[0]) + (cy - at[1]) * (cy - at[1]))
               for cx, cy in ((x, y), (x + w, y), (x, y + h), (x + w, y + h)))  # fmt: skip


def per_mm(canvas: Canvas, size: tuple[int, int]) -> tuple[float, float]:
    """This panel's pixels per millimetre, across and down."""
    return size[0] / canvas.own[2], size[1] / canvas.own[3]


def pixel(canvas: Canvas, size: tuple[int, int], at: tuple[float, float]) -> tuple[float, float]:
    """Where the point ``at`` (millimetres) is on this panel, in its pixels (it may be off it)."""
    sx, sy = per_mm(canvas, size)
    return (at[0] - canvas.own[0]) * sx, (at[1] - canvas.own[1]) * sy


def centres(canvas: Canvas, size: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    """Where the middles of this panel's columns and rows are, in millimetres."""
    w, h = size
    sx, sy = per_mm(canvas, size)
    return (canvas.own[0] + (np.arange(w) + 0.5) / sx,
            canvas.own[1] + (np.arange(h) + 0.5) / sy)  # fmt: skip


def levels(values: np.ndarray) -> Image.Image:
    """A map (L) of ``values``, rounded to the nearest level (an even one at a half; what is
    no number at all, 0)."""
    rounded = np.clip(np.rint(np.where(np.isnan(values), 0.0, values)), 0, 255)
    return Image.fromarray(rounded.astype(np.uint8), "L")


def distances(canvas: Canvas, size: tuple[int, int], at: tuple[float, float]) -> np.ndarray:
    """How far the middle of each pixel of this panel is from ``at``, in millimetres."""
    xs, ys = centres(canvas, size)
    dx, dy = xs - at[0], ys - at[1]
    return np.sqrt(dy[:, None] * dy[:, None] + dx[None, :] * dx[None, :])


def radial_map(canvas: Canvas, size: tuple[int, int], at: tuple[float, float],
               far: float) -> Image.Image:  # fmt: skip
    """A map of how far each pixel of this panel is from ``at``: 0 there, 255 ``far`` away."""
    return levels(255 * distances(canvas, size, at) / far)


def linear_map(canvas: Canvas, size: tuple[int, int], u: tuple[float, float], lo: float,
               hi: float) -> Image.Image:  # fmt: skip
    """A map of how far along ``u`` (a unit vector) each pixel of this panel is: 0 at ``lo``
    millimetres, 255 at ``hi``."""
    xs, ys = centres(canvas, size)
    along = ys[:, None] * u[1] + xs[None, :] * u[0]
    return levels(255 * (along - lo) / (hi - lo))


def threshold(level: float, soft: float) -> list[int]:
    """The mask of a map below ``level``: 255 where a value is ``soft`` below it or more, 0
    above it, a ramp in between."""
    return [255 if v <= level - soft else 0 if v > level else round(255 * (level - v) / soft)
            for v in range(256)]  # fmt: skip


def in_out(u: float) -> float:
    """Slow, fast, slow (cubic)."""
    u = clamp(u)
    return 4 * u * u * u if u < 0.5 else 1 - (-2 * u + 2) ** 3 / 2


def in_out_sine(u: float) -> float:
    return -(math.cos(math.pi * clamp(u)) - 1) / 2

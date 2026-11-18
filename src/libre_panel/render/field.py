"""A light field under a theme: soft pools of light that drift slowly over the panels that
stand together, as one; each panel shows its part of it, where it stands (in millimetres, as
arrangement.py places the panels). It mirrors the load: calm and cool at rest, warmer and
brighter while the computer works.

The field is made at an eighth of the panel's pixels, a point in the middle of each block of
8 by 8, and drawn up between them (:func:`drawn_up`: so smooth a light shows no corners, in
whole numbers, as the engine draws it up with four pixels at a time), anew :data:`RATE` times a
second on the wall clock, so the panels side by side change together and the encoder has frames
between that are the same. The app makes the field's points (lp-render too:
rust/render/src/field.rs) and the engine draws them up (engine/src/scene.rs).

Over a field, a frame is composed as what lies in front of the light (:class:`Foreground`:
every piece laid in turn on a clear foreground, its colours premultiplied, eight bits each)
laid over the light once: a new field changes nothing in front of it, so the engine lays its
pieces once and the light anew under them. The same integer steps here, in lp-render and in
the engine, so all three give the same frame to the bit.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cached_property
from typing import Any

import numpy as np
from PIL import Image, ImageFilter

STEP = 8  # the panel's pixels a point of the field stands for, across and down
RATE = 6.25  # fields a second (the panel's frames between are the same: the encoder skips them)

Rect = tuple[float, float, float, float]  # x, y, width, height in millimetres


@dataclass(frozen=True)
class Pool:
    """A pool of light: its colour at rest and under load (RGB), how far it drifts as a share
    of the canvas across and down, in how many seconds it goes there and back each way, where
    in its round it starts (radians), and how wide it is (its Gaussian's sigma, millimetres)."""

    cool: tuple[float, float, float]
    warm: tuple[float, float, float]
    ax: float
    ay: float
    px: float
    py: float
    phase: float
    radius: float


LOOKS: dict[str, tuple[Pool, ...]] = {
    # Lumen's: its CPU, GPU and memory colours at rest, amber, coral and rose under load
    "lumen": (
        Pool((46, 230, 214), (245, 168, 84), 0.46, 0.36, 330.0, 247.0, 0.4, 64.0),
        Pool((127, 156, 255), (248, 113, 113), 0.44, 0.40, 390.0, 281.0, 2.5, 72.0),
        Pool((199, 146, 255), (255, 150, 120), 0.42, 0.38, 450.0, 223.0, 4.4, 58.0),
    ),
    # Arena's: its CPU, GPU and memory colours (the Arctic look's) at rest, warm as Lumen's
    "arena": (
        Pool((19, 229, 215), (245, 168, 84), 0.46, 0.36, 330.0, 247.0, 0.4, 64.0),
        Pool((163, 174, 255), (248, 113, 113), 0.44, 0.40, 390.0, 281.0, 2.5, 72.0),
        Pool((224, 168, 255), (255, 150, 120), 0.42, 0.38, 450.0, 223.0, 4.4, 58.0),
    ),
}
# A light in a picture's colours (background.field.colours: a cover's, render/cover.py's
# colours): each pool in one of them, at rest and under load alike (the load makes it brighter,
# not warmer), this much stronger, as it stands for the picture behind the whole screen ...
TINTED = 1.8
# ... and from one picture's colours to the next it glides this long (smoothstep), where the
# frames stream; elsewhere it takes them at once
TINT_S = 2.0
STRENGTH = (0.19, 0.15)  # the light's strength at rest, and what full load adds
BREATH_S = 9.0  # the light swells and ebbs once in this long
BREATH = (0.04, 0.10)  # by this share at rest, and what full load adds
WARMTH = 0.85  # how far toward its warm colour a pool goes at full load


def stepped(t: float) -> float:
    """The moment of the field shown at ``t`` (wall clock, seconds): the last of its steps."""
    return math.floor(t * RATE) / RATE


def tinted(pools: Sequence[Pool], colours: Sequence[tuple[int, int, int]]) -> tuple[Pool, ...]:
    """``pools`` in ``colours`` (one each, in turn): each in its colour at rest and under load,
    :data:`TINTED` stronger; where they drift as they do."""
    out = []
    for i, pool in enumerate(pools):
        c = tuple(v * TINTED for v in colours[i % len(colours)])
        out.append(Pool(c, c, pool.ax, pool.ay, pool.px, pool.py, pool.phase, pool.radius))
    return tuple(out)


def gliding(before: Sequence[Pool], after: Sequence[Pool], k: float) -> tuple[Pool, ...]:
    """``k`` (0 to 1, eased: smoothstep) of the way from the colours of ``before`` to those of
    ``after`` (the same pools: where they drift as ``after`` has it)."""
    t = min(1.0, max(0.0, k))
    e = t * t * (3.0 - 2.0 * t)
    if e >= 1.0:
        return tuple(after)
    out = []
    for a, b in zip(before, after, strict=True):
        cool = tuple(x + (y - x) * e for x, y in zip(a.cool, b.cool, strict=True))
        warm = tuple(x + (y - x) * e for x, y in zip(a.warm, b.warm, strict=True))
        out.append(Pool(cool, warm, b.ax, b.ay, b.px, b.py, b.phase, b.radius))
    return tuple(out)


def small(
    size: tuple[int, int],
    own: Rect,
    whole: Rect,
    t: float,
    load: float,
    look: str | Sequence[Pool] = "lumen",
    base: tuple[int, int, int] = (0, 0, 0),
) -> np.ndarray:
    """The field at an eighth of the panel (``size`` its pixels) as levels (uint8, rows by
    columns by RGB): ``own`` where the panel stands and ``whole`` the canvas of all the panels
    standing together (millimetres), at ``t`` (seconds) under ``load`` (0 to 1), its light on
    ``base`` (the theme's background colour); ``look``: one of :data:`LOOKS`, or its pools."""
    load = min(1.0, max(0.0, load))
    fw, fh = -(-size[0] // STEP), -(-size[1] // STEP)
    # (each in the middle of its block of the panel's pixels, the last one's too where the
    # panel ends within it)
    xs = own[0] + (np.arange(fw, dtype=np.float64) * STEP + STEP / 2) * (own[2] / size[0])
    ys = own[1] + (np.arange(fh, dtype=np.float64) * STEP + STEP / 2) * (own[3] / size[1])
    strength = STRENGTH[0] + STRENGTH[1] * load
    breathe = 1.0 + (BREATH[0] + BREATH[1] * load) * math.sin(2.0 * math.pi * t / BREATH_S)
    mix = min(1.0, load * WARMTH)
    acc = np.zeros((fh, fw, 3), np.float64)
    acc[:, :] = base
    for pool in LOOKS[look] if isinstance(look, str) else look:
        cx = whole[0] + whole[2] * (
            0.5 + pool.ax * math.sin(2.0 * math.pi * t / pool.px + pool.phase)
        )
        cy = whole[1] + whole[3] * (
            0.5 + pool.ay * math.cos(2.0 * math.pi * t / pool.py + pool.phase * 1.3)
        )
        inv = 1.0 / (2.0 * pool.radius * pool.radius)
        # (a Gaussian is the product of one across and one down: fw + fh of them, not fw * fh;
        # each by the C library's exp, as Rust's f64::exp takes it, for the same numbers)
        across = np.array([math.exp(-((x - cx) * (x - cx)) * inv) for x in xs.tolist()])
        down = np.array([math.exp(-((y - cy) * (y - cy)) * inv) for y in ys.tolist()])
        colour = [
            (c + (w - c) * mix) * strength * breathe
            for c, w in zip(pool.cool, pool.warm, strict=True)
        ]
        weight = down[:, None] * across[None, :]
        for channel in range(3):
            acc[:, :, channel] += weight * colour[channel]
    return np.clip(np.floor(acc + 0.5), 0, 255).astype(np.uint8)


def field(
    size: tuple[int, int],
    own: Rect,
    whole: Rect,
    t: float,
    load: float,
    look: str | Sequence[Pool] = "lumen",
    base: tuple[int, int, int] = (0, 0, 0),
) -> Image.Image:
    """The panel's part of the field (RGB, ``size``): :func:`small` drawn up."""
    return Image.fromarray(drawn_up(small(size, own, whole, t, load, look, base), size), "RGB")


def _between(n: int, points: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For each of ``n`` pixels in a row, the two points of the field it lies between (each in
    the middle of its block of :data:`STEP`) and how near the second it is, in sixteenths: the
    pixel's middle ``x + 0.5`` is ``(2x - 7) / 16`` of a block from the first point's."""
    sixteenths = 2 * np.arange(n, dtype=np.int64) - (STEP - 1)
    first = np.floor_divide(sixteenths, 16)
    near = sixteenths - 16 * first
    second = np.clip(first + 1, 0, points - 1)
    return np.clip(first, 0, points - 1), second, near


def drawn_up(levels: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """``levels`` (rows by columns by RGB, a point in the middle of each block of
    :data:`STEP` by :data:`STEP` pixels) drawn up to ``size``: each pixel between the four
    points nearest its middle, weighted by how near it is each way in sixteenths, the sum
    rounded once (``(sum + 128) >> 8``); beyond the first and the last points, as they are.
    Whole numbers throughout, as the engine draws it up (scene.rs's drawn_up)."""
    w, h = size
    fh, fw = levels.shape[:2]
    x0, x1, fx = _between(w, fw)
    y0, y1, fy = _between(h, fh)
    v = levels.astype(np.int64)
    across = v[:, x0] * (16 - fx)[None, :, None] + v[:, x1] * fx[None, :, None]
    down = across[y0] * (16 - fy)[:, None, None] + across[y1] * fy[:, None, None]
    return ((down + 128) >> 8).astype(np.uint8)


# a panel of a model not known: as dense as the 9.2" bar (arrangement.GUESS_MM_PER_PX)
GUESS_MM_PER_PX = 0.118


def alone(theme: Any) -> Rect:
    """Where a panel showing ``theme`` stands by itself (millimetres, at 0, 0): the size of its
    model's picture, else as dense as the 9.2" bar. Its light is as big on it as beside others."""
    from libre_panel.arrangement import millimetres
    from libre_panel.devices.models import find_model

    size = (theme.width, theme.height)
    mm = millimetres(find_model(theme.model), size)
    if mm is None:
        mm = (size[0] * GUESS_MM_PER_PX, size[1] * GUESS_MM_PER_PX)
    return 0.0, 0.0, float(mm[0]), float(mm[1])


# -- the light as a frame's ground -------------------------------------------------------------

# the readings whose load the light follows (the higher of them), and over how many of them
LOAD_SENSORS = ("cpu.load", "gpu.load")
LOAD_SAMPLES = 10


def load_of(history: Any, eased: float = 1.0) -> float:
    """The load the light follows (0 to 1): the higher of :data:`LOAD_SENSORS`' means over their
    last :data:`LOAD_SAMPLES` numbers in ``history`` (a snapshot's), ``eased`` (0 to 1) of the
    way from the mean before the newest number to it (smoothstep): the light warms over the
    second after a reading, not at once. 0 where none of them has a number."""
    t = min(1.0, max(0.0, eased))
    k = t * t * (3.0 - 2.0 * t)
    load = 0.0
    for key in LOAD_SENSORS:
        values: Sequence[float] = history.get(key) or ()
        if not values:
            continue
        now = values[-LOAD_SAMPLES:]
        before = values[-LOAD_SAMPLES - 1 : -1] or now
        a, b = sum(before) / len(before), sum(now) / len(now)
        load = max(load, a + (b - a) * k)
    return min(1.0, max(0.0, load / 100.0))


@dataclass(frozen=True, eq=False)
class Light:
    """A light field as a frame's ground: its levels at an eighth (``small``, rows by columns
    by RGB, uint8) for a frame of ``size``, and drawn up to it (``picture``, RGB: made the first
    time it is asked for, where the frame is composed here; the engine draws it up itself);
    ``key``: what it was made of."""

    small: np.ndarray
    size: tuple[int, int]
    key: tuple[Any, ...]

    @cached_property
    def picture(self) -> Image.Image:
        return Image.fromarray(drawn_up(self.small, self.size), "RGB")


def same(a: Light, b: Light) -> bool:
    """Whether two lights are the same light: the same levels for a frame of the same size."""
    return a.size == b.size and a.small.shape == b.small.shape and bool((a.small == b.small).all())


def light(size: tuple[int, int], own: Rect, whole: Rect, t: float, load: float,
          look: str | Sequence[Pool] = "lumen",
          base: tuple[int, int, int] = (0, 0, 0)) -> Light:  # fmt: skip
    """The light of :func:`field` as a frame's ground (``t`` as it is: :func:`stepped` first)."""
    pools = LOOKS[look] if isinstance(look, str) else tuple(look)
    levels = small(size, own, whole, t, load, pools, base)
    return Light(levels, size, (size, own, whole, t, load, pools, base))


# -- what lies in front of the light ------------------------------------------------------------


def div255(v: np.ndarray) -> np.ndarray:
    """``v`` / 255 rounded, as Pillow divides (exact for 0 to 65,025): int32 in, int32 out."""
    v = v + 128
    return (v + (v >> 8)) >> 8


class Foreground:
    """What lies in front of the light over a box ``size`` (width, height): every piece laid on
    it in turn as on a clear picture, its colours premultiplied by their cover, eight bits
    each (``rgba``, rows by columns by 4). Laid over the light (:meth:`on`), it is the frame.

    A piece over it (``a`` its alpha, ``s`` its colour): ``F = div255(s a + F (255 - a))``,
    ``A = a + div255(A (255 - a))``; light added: ``F = min(255, F + L)``, the cover as it
    was; an opaque piece: ``F = s``, ``A = 255``. Over the light ``g``:
    ``min(255, F + div255(g (255 - A)))``."""

    def __init__(self, size: tuple[int, int]) -> None:
        self.size = size
        self.rgba = np.zeros((size[1], size[0], 4), np.uint8)

    def _box(self, x: int, y: int, w: int, h: int) -> tuple[int, int, int, int] | None:
        x0, y0 = max(x, 0), max(y, 0)
        x1, y1 = min(x + w, self.size[0]), min(y + h, self.size[1])
        return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else None

    def over(self, colour: np.ndarray, alpha: np.ndarray, x: int, y: int) -> None:
        """``colour`` (rows by columns by RGB) through ``alpha`` (rows by columns), its top
        left corner at ``x``, ``y``: what of it lies on the box."""
        box = self._box(x, y, alpha.shape[1], alpha.shape[0])
        if box is None:
            return
        x0, y0, x1, y1 = box
        s = colour[y0 - y : y1 - y, x0 - x : x1 - x].astype(np.int32)
        a = alpha[y0 - y : y1 - y, x0 - x : x1 - x].astype(np.int32)[:, :, None]
        here = self.rgba[y0:y1, x0:x1]
        f, cover = here[:, :, :3].astype(np.int32), here[:, :, 3:].astype(np.int32)
        here[:, :, :3] = div255(s * a + f * (255 - a))
        here[:, :, 3:] = a + div255(cover * (255 - a))

    def solid(self, colour: np.ndarray, x: int, y: int) -> None:
        """``colour`` (rows by columns by RGB) as it is, opaque."""
        box = self._box(x, y, colour.shape[1], colour.shape[0])
        if box is None:
            return
        x0, y0, x1, y1 = box
        self.rgba[y0:y1, x0:x1, :3] = colour[y0 - y : y1 - y, x0 - x : x1 - x]
        self.rgba[y0:y1, x0:x1, 3] = 255

    def add(self, colour: np.ndarray, x: int, y: int) -> None:
        """Light (rows by columns by RGB) added, as light adds up."""
        box = self._box(x, y, colour.shape[1], colour.shape[0])
        if box is None:
            return
        x0, y0, x1, y1 = box
        here = self.rgba[y0:y1, x0:x1, :3]
        added = here.astype(np.int32) + colour[y0 - y : y1 - y, x0 - x : x1 - x]
        here[:] = np.minimum(added, 255)

    def on(self, ground: np.ndarray, box: tuple[int, int, int, int] | None = None) -> np.ndarray:
        """The foreground over ``ground`` (rows by columns by RGB, the box's size; or of
        ``box``, x0, y0, x1, y1 within it, ``ground`` that box's): RGB, uint8."""
        x0, y0, x1, y1 = box or (0, 0, *self.size)
        here = self.rgba[y0:y1, x0:x1]
        f, cover = here[:, :, :3].astype(np.int32), here[:, :, 3:].astype(np.int32)
        out = f + div255(ground.astype(np.int32) * (255 - cover))
        return np.minimum(out, 255).astype(np.uint8)


def lay(fore: Foreground, piece: Any, origin: tuple[int, int], light_box: np.ndarray) -> None:
    """``piece`` (renderer.Piece) laid on ``fore``, whose top left corner is ``origin`` on the
    panel, as Renderer._composite lays it on a frame: a frosted backdrop first (what shows
    beneath it at this moment, the light ``light_box`` of the box included, blurred, laid
    through its mask), then the piece (added light, an opaque picture as it is, an L through
    itself, else through its alpha), then the light it gives."""
    from libre_panel.render import moving

    ox, oy = origin
    w, h = fore.size
    if piece.backdrop is not None and piece.box is not None:
        bx, by, bw, bh = piece.box
        bx, by = bx - ox, by - oy
        box = (max(bx, 0), max(by, 0), min(bx + bw, w), min(by + bh, h))
        if box[2] > box[0] and box[3] > box[1]:
            beneath = fore.on(light_box[box[1] : box[3], box[0] : box[2]], box)
            blurred = Image.fromarray(beneath, "RGB").filter(
                ImageFilter.GaussianBlur(piece.backdrop_radius)
            )
            mask = piece.backdrop.crop((box[0] - bx, box[1] - by, box[2] - bx, box[3] - by))
            fore.over(np.asarray(blurred), np.asarray(mask), box[0], box[1])
    layer, x, y = piece.layer, piece.x - ox, piece.y - oy
    if piece.add:
        fore.add(np.asarray(layer if layer.mode == "RGB" else layer.convert("RGB")), x, y)
        return
    if moving.solid(layer) or layer.mode == "RGB":
        fore.solid(np.asarray(layer.convert("RGB")), x, y)
    elif layer.mode == "L":
        grey = np.asarray(layer)
        fore.over(np.repeat(grey[:, :, None], 3, axis=2), grey, x, y)
    else:
        rgba = np.asarray(layer if layer.mode == "RGBA" else layer.convert("RGBA"))
        fore.over(rgba[:, :, :3], rgba[:, :, 3], x, y)
    if piece.light is not None:
        glow = piece.light
        fore.add(np.asarray(glow.layer.convert("RGB")), glow.x - ox, glow.y - oy)


def compose(light: Light, pieces: Sequence[tuple[str, Any]],
            box: tuple[int, int, int, int] | None = None) -> Image.Image:  # fmt: skip
    """The frame of ``pieces`` over ``light`` (RGB): of ``box`` (x0, y0, x1, y1) on the panel,
    else all of it."""
    x0, y0, x1, y1 = box or (0, 0, *light.size)
    ground = np.asarray(light.picture.crop((x0, y0, x1, y1)))
    fore = Foreground((x1 - x0, y1 - y0))
    for _wid, piece in pieces:
        lay(fore, piece, (x0, y0), ground)
    return Image.fromarray(fore.on(ground), "RGB")

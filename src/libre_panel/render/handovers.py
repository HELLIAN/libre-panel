"""More ways from one screen to the next: plain ones (wipe, zoom, iris, blinds, dissolve, a
slide from each side) and elaborate ones (a line of light, a glitch, tiles that turn over, a
screen that falls apart, cards that come in one after another, a comet, a ring of light that
opens). Settings → Panel → Transitions chooses them for each occasion (render/overlays.py has
the list, app.py the choice).

Each draws frames of any size, either way round, sizes being for a panel 480 px high and
scaled with the shorter side, and keeps the work of a frame small at 30 to 50 frames a second
on a 1920x480 bar: a map made once per switch and a threshold over it (``point`` and
``composite``), a few crops and pastes, at most a scaled copy of the frame.
"""

from __future__ import annotations

import math
import random
from typing import Any

import numpy as np
from PIL import Image, ImageChops

from libre_panel.plugins.render import Transition
from libre_panel.render.canvas import (
    SLIDE_WAYS,
    Canvas,
    alone,
    distances,
    in_out,
    in_out_sine,
    levels,
    light_at,
    linear_map,
    middle,
    per_mm,
    pixel,
    radial_map,
    reach,
    slide,
    span,
    take_canvas,
    threshold,
    way_of,
    wipe_map,
)
from libre_panel.render.light import (
    add_line,
    clamp,
    ease_in,
    ease_out,
    phase,
    role_color,
    smooth,
)
from libre_panel.render.morph import lay, take_apart, things


def _rgb(image: Image.Image) -> Image.Image:
    return image if image.mode == "RGB" else image.convert("RGB")


# -- the plain ones: the new screen where a map lies below a rising threshold ------------------


class _Masked(Transition):
    """The new screen shows where the map (L, made once a switch for its size) is below a level
    that rises from 0 to 255 over the transition; ``soft`` grey levels blend at the edge."""

    api = 1
    soft = 24
    toasts = "over"

    def __init__(self, context: Any = None, params: dict[str, Any] | None = None) -> None:
        super().__init__(context, params)
        self._made: tuple[tuple[int, int], Image.Image] | None = None

    def _map(self, w: int, h: int) -> Image.Image:
        raise NotImplementedError

    def frame(self, old: Image.Image, new: Image.Image, t: float) -> Image.Image:
        if t >= 1.0:
            return new
        if self._made is None or self._made[0] != new.size:
            self._made = (new.size, self._map(*new.size))
        mask = self._made[1].point(threshold(self._level(t), self.soft))
        return Image.composite(_rgb(new), _rgb(old), mask)

    def _level(self, t: float) -> float:
        """How far the level has risen, ``t`` of the way."""
        return smooth(t) * (255 + self.soft)


def _row(values: bytes, w: int, h: int) -> Image.Image:
    """A map that changes across, the same down every column."""
    return Image.frombytes("L", (len(values), 1), values).resize((w, h), Image.Resampling.BILINEAR)


class Wipe(_Masked):
    """A soft edge goes across, from the left, and leaves the new screen behind it; across all
    the panels, as one (render/canvas.py), where it is told where they stand."""

    name = "wipe"
    duration = 0.7
    soft = 40

    def __init__(self, context: Any = None, params: dict[str, Any] | None = None) -> None:
        super().__init__(context, params)
        self.canvas = take_canvas(self)

    def _map(self, w: int, h: int) -> Image.Image:
        if self.canvas is not None:
            return wipe_map(self.canvas, w, h)
        return _row(bytes(range(256)), w, h)


class Iris(_Masked):
    """A circle opens from the middle onto the new screen; across all the panels, from the
    middle of all of them, slower and with a wider edge (render/canvas.py), where it is told
    where they stand."""

    name = "iris"
    duration = 0.8
    soft = 18
    EDGE_MM = 11.0  # across the panels: its soft edge

    def __init__(self, context: Any = None, params: dict[str, Any] | None = None) -> None:
        super().__init__(context, params)
        self.canvas = take_canvas(self)
        if self.canvas is not None:  # (it reaches the farthest corner at 255, its edge beyond)
            self._at = middle(self.canvas.whole)
            self._far = reach(self.canvas.whole, self._at) + self.EDGE_MM
            self.soft = 255 * self.EDGE_MM / self._far

    def _level(self, t: float) -> float:
        return 255 * in_out(t) if self.canvas is not None else super()._level(t)

    def _map(self, w: int, h: int) -> Image.Image:
        if self.canvas is not None:
            return radial_map(self.canvas, (w, h), self._at, self._far)
        side = math.ceil(math.hypot(w, h))  # the circle reaches the corners at 255
        circle = Image.radial_gradient("L").resize((side, side), Image.Resampling.BILINEAR)
        left, top = (side - w) // 2, (side - h) // 2
        return circle.crop((left, top, left + w, top + h))


class Blinds(_Masked):
    """Upright slats turn one after another, from the left, each from its own left edge."""

    name = "blinds"
    duration = 0.9
    soft = 30

    def _map(self, w: int, h: int) -> Image.Image:
        u = min(w, h) / 480
        slats = max(4, round(w / (150 * u)))
        width = w / slats
        values = bytearray()
        for x in range(w):
            k, inside = divmod(x, width)
            values.append(
                min(255, round(255 * (0.45 * k / max(1, slats - 1) + 0.55 * inside / width)))
            )
        return Image.frombytes("L", (w, 1), bytes(values)).resize((w, h), Image.Resampling.NEAREST)


class Dissolve(_Masked):
    """The new screen comes through in small squares, here and there."""

    name = "dissolve"
    duration = 0.8
    soft = 6

    def _map(self, w: int, h: int) -> Image.Image:
        u = min(w, h) / 480
        block = max(2, round(8 * u))
        cols, rows = math.ceil(w / block), math.ceil(h / block)
        chance = random.Random(0x5EED)  # the same squares every time: calm, not noise
        noise = Image.frombytes(
            "L", (cols, rows), bytes(chance.randrange(256) for _ in range(cols * rows))
        )
        return noise.resize((cols * block, rows * block), Image.Resampling.NEAREST).crop(
            (0, 0, w, h)
        )


class _SlideFrom(Transition):
    """The new screen pushes the old one out, from one side (``side``: where it comes from);
    across all the panels, as one (render/canvas.py), where it is told where they stand."""

    api = 1
    duration = 0.6
    side = "right"

    def __init__(self, context: Any = None, params: dict[str, Any] | None = None) -> None:
        super().__init__(context, params)
        self.canvas = take_canvas(self)

    def frame(self, old: Image.Image, new: Image.Image, t: float) -> Image.Image:
        if t >= 1.0:
            return new
        if self.canvas is not None:
            return slide(old, new, t, self.canvas, SLIDE_WAYS[self.name])
        w, h = new.size
        eased = 1 - (1 - t) ** 3
        dx, dy = {"right": (-1, 0), "left": (1, 0), "bottom": (0, -1), "top": (0, 1)}[self.side]
        sx, sy = round(dx * w * eased), round(dy * h * eased)
        out = Image.new("RGB", (w, h))
        out.paste(_rgb(old), (sx, sy))
        out.paste(_rgb(new), (sx - dx * w, sy - dy * h))
        return out


class SlideRight(_SlideFrom):
    name = "slide-right"
    side = "left"


class SlideUp(_SlideFrom):
    name = "slide-up"
    side = "bottom"


class SlideDown(_SlideFrom):
    name = "slide-down"
    side = "top"


class Zoom(Transition):
    """The old screen draws back into the dark, the new one comes forward out of it."""

    name = "zoom"
    api = 1
    duration = 0.8
    toasts = "over"
    DEPTH = 0.12  # how far back a screen stands at its farthest

    def frame(self, old: Image.Image, new: Image.Image, t: float) -> Image.Image:
        if t >= 1.0:
            return new
        w, h = new.size
        going = t < 0.5
        u = smooth(t / 0.5) if going else 1.0 - smooth((t - 0.5) / 0.5)  # 0 near, 1 far
        scale = 1.0 - self.DEPTH * u
        picture = _rgb(old if going else new)
        size = (max(1, round(w * scale)), max(1, round(h * scale)))
        dark = role_color(self.context, "void")
        out = Image.new("RGB", (w, h), dark)
        small = picture.resize(size, Image.Resampling.BILINEAR)
        small = Image.blend(small, Image.new("RGB", size, dark), min(1.0, 0.9 * u))
        out.paste(small, ((w - size[0]) // 2, (h - size[1]) // 2))
        return out


# -- the elaborate ones -------------------------------------------------------------------------


class LightLine(Transition):
    """A line of light runs across, the old screen ahead of it and the new one behind, and
    goes out as it leaves (after Lumen's lit rings)."""

    name = "light"
    api = 1
    duration = 1.1
    toasts = "over"
    from_shown = True

    def __init__(self, context: Any = None, params: dict[str, Any] | None = None) -> None:
        super().__init__(context, params)
        self._glow: tuple[tuple[int, int], Image.Image] | None = None
        self.canvas = take_canvas(self)  # (across the panels: render/canvas.py)

    def _strip(self, w: int, h: int) -> Image.Image:
        """The line's light, upright, made once: a horizontal line drawn along a strip as long
        as the frame is high, turned."""
        if self._glow is None or self._glow[0] != (w, h):
            u = min(w, h) / 480
            half = max(3, round(32 * u))
            strip = Image.new("RGB", (h + 2 * half, 2 * half))
            accent, white = role_color(self.context, "info"), role_color(self.context, "text")
            add_line(strip, -half, h + 3 * half, half, accent, 1.0, u, 1.0, white=white)
            self._glow = (
                (w, h),
                strip.crop((half, 0, half + h, 2 * half)).transpose(Image.Transpose.ROTATE_90),
            )
        return self._glow[1]

    def frame(self, old: Image.Image, new: Image.Image, t: float) -> Image.Image:
        if t >= 1.0:
            return new
        w, h = new.size
        glow = self._strip(w, h)
        if self.canvas is not None:  # where the line is on its way over all the panels
            x = light_at(self.canvas, t, w)
        else:
            x = round(-glow.width + (w + 2 * glow.width) * smooth(t))
        out = _rgb(old).copy()
        if x > 0:
            out.paste(_rgb(new).crop((0, 0, min(w, x), h)), (0, 0))
        bright = ease_out(phase(t, 0.0, 0.15)) * (1.0 - ease_in(phase(t, 0.8, 1.0)))
        a, b = max(0, x - glow.width // 2), min(w, x + glow.width // 2)
        if b > a and bright > 0.004:
            light = glow.crop((a - (x - glow.width // 2), 0, b - (x - glow.width // 2), h))
            if bright < 0.999:
                light = light.point(lambda v: round(v * bright))
            out.paste(ImageChops.add(out.crop((a, 0, b, h)), light), (a, 0))
        return out


class Glitch(Transition):
    """A short digital fault: rows slip sideways, the colours split, bits of the other screen
    flash in; at its height the new screen takes over and the fault fades."""

    name = "glitch"
    api = 1
    duration = 0.7
    toasts = "over"

    def frame(self, old: Image.Image, new: Image.Image, t: float) -> Image.Image:
        if t >= 1.0:
            return new
        w, h = new.size
        u = min(w, h) / 480
        strength = 1.0 - abs(t - 0.5) * 2  # strongest in the middle
        base, other = (_rgb(old), _rgb(new)) if t < 0.5 else (_rgb(new), _rgb(old))
        chance = random.Random(int(t * 40))  # a new fault a few times a second, not every frame
        out = base.copy()
        for _ in range(round(10 * strength) + 1):  # bands of rows, slipped
            top = chance.randrange(h)
            tall = max(2, round(chance.uniform(4, 40) * u))
            shift = round(chance.uniform(-80, 80) * u * strength)
            band = (other if chance.random() < 0.25 else base).crop((0, top, w, min(h, top + tall)))
            out.paste(ImageChops.offset(band, shift, 0), (0, top))
        split = round(12 * u * strength)
        if split:
            red, green, blue = out.split()
            out = Image.merge(
                "RGB", (ImageChops.offset(red, split, 0), green, ImageChops.offset(blue, -split, 0))
            )
        return out


class Tiles(Transition):
    """The screen turns over tile by tile, from one corner across, the new one on their backs."""

    name = "tiles"
    api = 1
    duration = 1.2
    toasts = "over"
    SPREAD = 0.55  # the share of the time over which the tiles begin, one after the other

    def frame(self, old: Image.Image, new: Image.Image, t: float) -> Image.Image:
        if t >= 1.0:
            return new
        w, h = new.size
        u = min(w, h) / 480
        side = max(16, round(120 * u))
        cols, rows = math.ceil(w / side), math.ceil(h / side)
        dark = role_color(self.context, "void")
        out = Image.new("RGB", (w, h), dark)
        old, new = _rgb(old), _rgb(new)
        last = max(1, cols + rows - 2)
        for row in range(rows):
            for col in range(cols):
                box = (col * side, row * side, min(w, (col + 1) * side), min(h, (row + 1) * side))
                start = self.SPREAD * (col + row) / last
                turn = smooth(phase(t, start, start + 1.0 - self.SPREAD))
                width = abs(math.cos(math.pi * turn))
                face = (old if turn < 0.5 else new).crop(box)
                if width >= 0.999:
                    out.paste(face, box[:2])
                    continue
                size = (max(1, round(face.width * width)), face.height)
                if size[0] <= 1:
                    continue
                edge = face.resize(size, Image.Resampling.BILINEAR)
                edge = Image.blend(edge, Image.new("RGB", size, dark), 0.5 * (1.0 - width))
                out.paste(edge, (box[0] + (face.width - size[0]) // 2, box[1]))
        return out


class Shatter(Transition):
    """The old screen breaks into pieces that fly apart from the middle and fall, the new one
    already there behind them. Across all the panels (render/canvas.py) it breaks into tiles
    that drift, turn, shrink and fall one after another along all of them, the new screen
    coming out of the shade behind them."""

    name = "shatter"
    api = 1
    duration = 1.3
    toasts = "over"
    TILE_MM = 19.0  # across the panels: about how large a tile is
    SHADE = 0.35  # how dark the new screen is behind the tiles at first

    def __init__(self, context: Any = None, params: dict[str, Any] | None = None) -> None:
        super().__init__(context, params)
        self._pieces: tuple[int, list[tuple[Image.Image, int, int, float, float, float]]] | None = (
            None
        )
        self.canvas = take_canvas(self)
        self._tiles: tuple[int, list[_Tile]] | None = None

    def _broken(self, old: Image.Image) -> list[tuple[Image.Image, int, int, float, float, float]]:
        """The pieces of the old frame, once: each with its place, its speed and its start."""
        if self._pieces is None or self._pieces[0] != id(old):
            w, h = old.size
            u = min(w, h) / 480
            side = max(12, round(60 * u))
            chance = random.Random(0xB10C)
            pieces = []
            cx, cy = w / 2, h / 2
            reach = math.hypot(cx, cy)
            picture = _rgb(old)
            for y in range(0, h, side):
                for x in range(0, w, side):
                    box = (x, y, min(w, x + side), min(h, y + side))
                    dx, dy = box[0] + side / 2 - cx, box[1] + side / 2 - cy
                    far = math.hypot(dx, dy) / reach
                    speed = (0.6 + chance.random()) * 900 * u
                    angle = math.atan2(dy, dx) + chance.uniform(-0.4, 0.4)
                    vx, vy = math.cos(angle) * speed, math.sin(angle) * speed - 300 * u
                    pieces.append((picture.crop(box), box[0], box[1], vx, vy, 0.45 * far))
            self._pieces = (id(old), pieces)
        return self._pieces[1]

    def frame(self, old: Image.Image, new: Image.Image, t: float) -> Image.Image:
        if t >= 1.0:
            return new
        if self.canvas is not None:
            return self._falling(old, new, t, self.canvas)
        w, h = new.size
        u = min(w, h) / 480
        out = _rgb(new).copy()
        seconds = t * self.duration
        for piece, x, y, vx, vy, start in self._broken(old):
            s = max(0.0, seconds - start * self.duration)
            px = round(x + vx * s)
            py = round(y + vy * s + 0.5 * 2400 * u * s * s)  # and it falls
            if -piece.width < px < w and -piece.height < py < h:
                out.paste(piece, (px, py))
        return out

    def _tiles_of(self, old: Image.Image, canvas: Canvas) -> list[_Tile]:
        """The tiles of the old frame across the panels, once: each with its box, its own
        chance (drift, turn) and when it begins, along all the panels."""
        if self._tiles is None or self._tiles[0] != id(old):
            w, h = old.size
            x, y, mw, mh = canvas.own
            # (no more of them than the panel has pixels)
            cols = min(w, max(4, round(mw / self.TILE_MM)))
            rows = min(h, max(2, round(mh / self.TILE_MM)))
            way = way_of(canvas)
            a, b = span(canvas.whole, way)
            # (each panel its own chance: two alike side by side do not break alike)
            chance = random.Random(abs(1009 * round(x) + round(y)))
            picture = _rgb(old)
            tiles = []
            for row in range(rows):
                for col in range(cols):
                    drift, turn, late = chance.random(), chance.random(), chance.random()
                    box = (round(col * w / cols), round(row * h / rows),
                           round((col + 1) * w / cols), round((row + 1) * h / rows))  # fmt: skip
                    cx, cy = x + (col + 0.5) * mw / cols, y + (row + 0.5) * mh / rows
                    q = (cx * way[0] + cy * way[1] - a) / max(0.001, b - a)
                    tiles.append(_Tile(picture.crop(box), box, drift, turn, q * 0.48 + late * 0.06))
            self._tiles = (id(old), tiles)
        return self._tiles[1]

    def _falling(self, old: Image.Image, new: Image.Image, t: float, canvas: Canvas) -> Image.Image:
        dark = self.SHADE * (1 - smooth(t * 1.4))
        out = _rgb(new)
        out = Image.blend(out, Image.new("RGB", out.size), dark) if dark > 0 else out.copy()
        sx, sy = per_mm(canvas, new.size)
        way = way_of(canvas)
        edge = role_color(self.context, "text")
        for tile in self._tiles_of(old, canvas):
            tt = clamp((t - tile.start) / 0.46)
            if tt >= 1:
                continue  # (gone)
            if tt <= 0:  # (not yet begun: where it was)
                out.paste(tile.picture, tile.box[:2])
                continue
            dx = (way[0] * 22 * tt + (tile.drift - 0.5) * 16 * tt) * sx
            dy = (way[1] * 22 * tt + 70 * tt * tt - 6 * math.sin(math.pi * tt)) * sy
            lit = 0.22 * math.sin(math.pi * min(1.0, tt * 1.6))
            _lay(out, tile, dx, dy, (tile.turn - 0.5) * 1.5 * tt, 1 - 0.18 * tt,
                 1 - tt**1.7, lit, edge)  # fmt: skip
        return out


class _Tile:
    """A tile of the old screen as the shatter across the panels breaks it."""

    __slots__ = ("box", "drift", "picture", "start", "turn")

    def __init__(self, picture: Image.Image, box: tuple[int, int, int, int], drift: float,
                 turn: float, start: float) -> None:  # fmt: skip
        self.picture, self.box, self.drift, self.turn, self.start = picture, box, drift, turn, start


def _edged(picture: Image.Image, strength: float, colour: tuple[int, int, int]) -> Image.Image:
    """``picture`` with a line of ``colour`` along its edge, ``strength`` over it."""
    face = picture.copy()
    w, h = face.size
    for box in ((0, 0, w, 1), (0, h - 1, w, h), (0, 1, 1, h - 1), (w - 1, 1, w, h - 1)):
        if box[2] > box[0] and box[3] > box[1]:
            part = face.crop(box)
            face.paste(Image.blend(part, Image.new("RGB", part.size, colour), strength), box[:2])
    return face


def _lay(out: Image.Image, tile: _Tile, dx: float, dy: float, turn: float, size: float,
         alpha: float, lit: float, edge: tuple[int, int, int]) -> None:  # fmt: skip
    """Lay ``tile`` onto ``out``: moved by ``dx``, ``dy``, turned by ``turn`` (radians,
    clockwise), grown by ``size`` about its middle, ``alpha`` of it seen, its edge lit."""
    face = _edged(tile.picture, lit, edge) if lit > 0.004 else tile.picture
    pw, ph = face.width + 2, face.height + 2  # (a clear pixel around it: soft edges once turned)
    held = Image.new("RGBA", (pw, ph))
    held.paste(face, (1, 1))
    x0, y0, x1, y1 = tile.box
    cx, cy = (x0 + x1) / 2 + dx, (y0 + y1) / 2 + dy
    c, s = math.cos(turn), math.sin(turn)
    hx = size * (abs(c) * pw + abs(s) * ph) / 2
    hy = size * (abs(s) * pw + abs(c) * ph) / 2
    ox, oy = math.floor(cx - hx), math.floor(cy - hy)
    ow, oh = math.ceil(cx + hx) - ox, math.ceil(cy + hy) - oy
    if ow <= 0 or oh <= 0 or ox >= out.width or oy >= out.height or ox + ow <= 0 or oy + oh <= 0:
        return
    # (where each pixel laid comes from in the tile: turned back and shrunk about its middle)
    mx, my = pw / 2, ph / 2
    data = (c / size, s / size, mx + (c * (ox - cx) + s * (oy - cy)) / size,
            -s / size, c / size, my + (-s * (ox - cx) + c * (oy - cy)) / size)  # fmt: skip
    laid = held.transform((ow, oh), Image.Transform.AFFINE, data, Image.Resampling.BILINEAR)
    if alpha < 0.999:
        laid.putalpha(laid.getchannel("A").point(lambda v: round(v * alpha)))
    out.paste(laid, (ox, oy), laid)


# -- cards, a comet, a ring: in millimetres, across all the panels or this one -----------------


def _joined(boxes: list[list[int]]) -> list[list[int]]:
    """Boxes ([x0, y0, x1, y1]) joined where they overlap, until none do: a screen's cards."""
    out = [b[:] for b in boxes]
    joined = True
    while joined:
        joined = False
        for i in range(len(out)):
            for j in range(i + 1, len(out)):
                a, b = out[i], out[j]
                if a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]:
                    out[i] = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
                    del out[j]
                    joined = True
                    break
            if joined:
                break
    return out


def _spaced(cards: list[list[int]], mx: int, my: int, w: int, h: int) -> list[list[int]]:
    """Each card with a margin around it (its shadow, its glow), within the panel and short of
    the cards beside it (to the middle of the gap between)."""
    out = []
    for i, a in enumerate(cards):
        e = [max(0, a[0] - mx), max(0, a[1] - my), min(w, a[2] + mx), min(h, a[3] + my)]
        for j, b in enumerate(cards):
            if j == i or not (e[0] < b[2] and b[0] < e[2] and e[1] < b[3] and b[1] < e[3]):
                continue
            if b[0] >= a[2]:  # (to the right)
                e[2] = min(e[2], max(a[2], (a[2] + b[0]) // 2))
            elif b[2] <= a[0]:  # (to the left)
                e[0] = max(e[0], min(a[0], (b[2] + a[0] + 1) // 2))
            elif b[1] >= a[3]:  # (below)
                e[3] = min(e[3], max(a[3], (a[3] + b[1]) // 2))
            elif b[3] <= a[1]:  # (above)
                e[1] = max(e[1], min(a[1], (b[3] + a[1] + 1) // 2))
        out.append(e)
    return out


class Cascade(Transition):
    """The ground changes, then the new screen's cards come in one after another, each rising
    and growing into its place, as a wave along all the panels (render/canvas.py) or along
    this one; what lies on no card comes with them at the end."""

    name = "cascade"
    api = 1
    duration = 1.9
    toasts = "over"
    GROUND = 0.28  # the share of the time the ground takes to change
    MARGIN_MM = 1.2  # around a card: its shadow, its glow
    RISE_MM = 9.0  # how far below its place a card begins
    SPREAD = 0.42  # the share of the time over which the cards begin, along the way
    COMES = 0.34  # the share a card takes to come in
    LAST = 0.84  # where what lies on no card fades in

    def __init__(self, context: Any = None, params: dict[str, Any] | None = None) -> None:
        super().__init__(context, params)
        self.canvas = take_canvas(self)
        self._laid: tuple[tuple[int, int], Image.Image, list[tuple[list[int], float]]] | None = None

    def _cards(self, new: Image.Image) -> tuple[Image.Image, list[tuple[list[int], float]]]:
        """The new screen's ground and its cards (each a box with its margin, and when along
        the way it comes, 0 to 1), worked out once, from what its renderer composed last."""
        if self._laid is None or self._laid[0] != new.size:
            renderer = getattr(self.context, "_renderer", None)
            found = getattr(renderer, "beneath", lambda: None)()
            if found is None or found[0].size != new.size:  # (none known: it fades, as one)
                ground, boxes = _rgb(new), []
            else:
                ground, boxes = found
            w, h = new.size
            canvas = self.canvas or alone(new.size)
            sx, sy = per_mm(canvas, new.size)
            cards = _joined(boxes)
            mx, my = min(w, round(self.MARGIN_MM * sx)), min(h, round(self.MARGIN_MM * sy))
            spaced = _spaced(cards, mx, my, w, h)
            way = way_of(canvas)
            a, b = span(canvas.whole, way)
            laid = []
            for card, box in zip(cards, spaced, strict=True):
                cx = canvas.own[0] + (card[0] + card[2]) / 2 / sx
                cy = canvas.own[1] + (card[1] + card[3]) / 2 / sy
                laid.append((box, (cx * way[0] + cy * way[1] - a) / max(0.001, b - a)))
            self._laid = (new.size, ground, laid)
        return self._laid[1], self._laid[2]

    def frame(self, old: Image.Image, new: Image.Image, t: float) -> Image.Image:
        if t >= 1.0:
            return new
        ground, cards = self._cards(new)
        changed = smooth(t / self.GROUND)
        out = ground.copy() if changed >= 1 else Image.blend(_rgb(old), ground, changed)
        rise = self.RISE_MM * per_mm(self.canvas or alone(new.size), new.size)[1]
        picture = _rgb(new)
        for box, q in cards:
            e = ease_out((t - (0.2 + q * self.SPREAD)) / self.COMES)
            if e <= 0:
                continue
            part = picture.crop(box)
            grown = 0.93 + 0.07 * e
            size = (max(1, round(part.width * grown)), max(1, round(part.height * grown)))
            if size != part.size:
                part = part.resize(size, Image.Resampling.BILINEAR)
            at = (round((box[0] + box[2]) / 2 - size[0] / 2),
                  round((box[1] + box[3]) / 2 + (1 - e) * rise - size[1] / 2))  # fmt: skip
            seen = smooth(e * 1.25)
            if seen >= 1:
                out.paste(part, at)
            else:
                out.paste(part, at, Image.new("L", size, round(255 * seen)))
        last = smooth((t - self.LAST) / (1 - self.LAST))
        return Image.blend(out, picture, last) if last > 0 else out


class Comet(Transition):
    """A comet flies across, from one panel into the next (render/canvas.py) or across this
    one, and the new screen opens behind it."""

    name = "comet"
    api = 1
    duration = 1.7
    toasts = "over"
    MARGIN_MM = 35.0  # it comes from this far beyond the panels
    SLANT = 0.38  # where it comes in and leaves: this share of the height above and below the
    # middle (of the width, going down)
    TAIL_MM = 90.0
    SPOTS = 44  # the tail's glow, spot by spot
    SPARKS = 22
    HEAD_MM = 17.0  # the halo around its head
    LAG_MM = 12.0  # how far behind the head the new screen opens
    EDGE_MM = 45.0  # how soft its edge is

    def __init__(self, context: Any = None, params: dict[str, Any] | None = None) -> None:
        super().__init__(context, params)
        self.canvas = take_canvas(self)
        self._made: tuple[tuple[int, int], _Flight, Image.Image, Any] | None = None

    def _flight(self, canvas: Canvas) -> _Flight:
        """Its way over the canvas: from beyond one side to beyond the other, slanting down,
        and on until its tail has left all the panels and the new screen is open on all."""
        x, y, w, h = canvas.whole
        cx, cy = middle(canvas.whole)
        m = self.MARGIN_MM
        if way_of(canvas) == (1, 0):
            begin, end = (x - m, cy - self.SLANT * h), (x + w + m, cy + self.SLANT * h)
        else:
            begin, end = (cx - self.SLANT * w, y - m), (cx + self.SLANT * w, y + h + m)
        dx, dy = end[0] - begin[0], end[1] - begin[1]
        length = math.sqrt(dx * dx + dy * dy)
        u = (dx / length, dy / length)
        farthest = max(px * u[0] + py * u[1] for px, py in
                       ((x, y), (x + w, y), (x, y + h), (x + w, y + h)))  # fmt: skip
        behind = max(self.TAIL_MM + 1, self.LAG_MM + self.EDGE_MM)
        length += max(0.0, farthest + behind - (end[0] * u[0] + end[1] * u[1]))
        return _Flight(begin, u, length)

    def _made_for(
        self, size: tuple[int, int]
    ) -> tuple[_Flight, Image.Image, tuple[Image.Image, int, int] | None]:
        """For a frame of ``size``: its way, the map of where the new screen opens, the comet's
        light and where its head is in it (None: it flies without it); once."""
        if self._made is None or self._made[0] != size:
            canvas = self.canvas or alone(size)
            flight = self._flight(canvas)
            start = flight.begin[0] * flight.u[0] + flight.begin[1] * flight.u[1] - self.LAG_MM
            seen = linear_map(canvas, size, flight.u, start - self.EDGE_MM, start + flight.length)
            sx, sy = per_mm(canvas, size)
            accent, white = role_color(self.context, "info"), role_color(self.context, "text")
            light = _comet_light(self, accent, white, sx, sy, flight.u)
            self._made = (size, flight, seen, light)
        return self._made[1:]

    def frame(self, old: Image.Image, new: Image.Image, t: float) -> Image.Image:
        if t >= 1.0:
            return new
        size = new.size
        flight, seen, light = self._made_for(size)
        canvas = self.canvas or alone(size)
        far = flight.length + self.EDGE_MM
        s = in_out_sine(t) * flight.length
        level = 255 * (s + self.EDGE_MM) / far
        mask = seen.point(threshold(level, 255 * self.EDGE_MM / far))
        out = Image.composite(_rgb(new), _rgb(old), mask)
        if light is not None:
            head = pixel(canvas, size, (flight.begin[0] + flight.u[0] * s,
                                        flight.begin[1] + flight.u[1] * s))  # fmt: skip
            _add(out, light[0], round(head[0]) - light[1], round(head[1]) - light[2])
        return out


class _Flight:
    """A comet's way: where it begins (millimetres), which way it goes (a unit vector) and how
    far."""

    __slots__ = ("begin", "length", "u")

    def __init__(self, begin: tuple[float, float], u: tuple[float, float], length: float) -> None:
        self.begin, self.u, self.length = begin, u, length


def _add(out: Image.Image, light: Image.Image, x: int, y: int) -> None:
    """Add ``light`` onto ``out`` with its top left at ``x``, ``y``, where it lies on it."""
    box = (max(0, x), max(0, y), min(out.width, x + light.width), min(out.height, y + light.height))
    if box[2] > box[0] and box[3] > box[1]:
        part = light.crop((box[0] - x, box[1] - y, box[2] - x, box[3] - y))
        out.paste(ImageChops.add(out.crop(box), part), box[:2])


RGB = tuple[int, int, int]
COMET_MOST = 1 << 22  # the most pixels its light may have (more: a canvas that says nothing
# sensible, and it flies without it)


def _comet_light(comet: Comet, accent: RGB, white: RGB, sx: float, sy: float,
                 u: tuple[float, float]) -> tuple[Image.Image, int, int] | None:  # fmt: skip
    """The comet's light (RGB, added onto the frame), made once: its tail, spot by spot in the
    accent, sparks of white behind its head and the head's halo, white in the middle; and where
    its head is in it. None where it would be larger than any panel."""
    n = (-u[1], u[0])
    # (what it is made of, in the order it is added: centre in millimetres from the head,
    # reach, what it is)
    parts: list[tuple[float, float, float, str, float]] = []
    for i in range(comet.SPOTS, 0, -1):
        f = i / comet.SPOTS
        parts.append((-u[0] * comet.TAIL_MM * f, -u[1] * comet.TAIL_MM * f,
                      (0.42 * (1 - f) + 0.06) * 10, "spot", 0.32 * (1 - f) ** 1.4))  # fmt: skip
    chance = random.Random(0xC0E7)
    for _ in range(comet.SPARKS):
        back = 4 + chance.random() * 70
        side = (chance.random() - 0.5) * 16 * (back / 70)
        small = 0.3 + chance.random() * 0.7
        parts.append((-u[0] * back + n[0] * side, -u[1] * back + n[1] * side, small, "spark",
                      0.75 * (1 - back / 74)))  # fmt: skip
    parts.append((0.0, 0.0, comet.HEAD_MM, "head", 1.0))
    left = math.floor(min(cx - r for cx, _, r, _, _ in parts) * sx) - 1
    top = math.floor(min(cy - r for _, cy, r, _, _ in parts) * sy) - 1
    right = math.ceil(max(cx + r for cx, _, r, _, _ in parts) * sx) + 1
    bottom = math.ceil(max(cy + r for _, cy, r, _, _ in parts) * sy) + 1
    if (right - left) * (bottom - top) > COMET_MOST:
        return None
    hx, hy = -left, -top
    light = np.zeros((bottom - top, right - left, 3))
    colours = np.array(accent, float), np.array(white, float)
    for cx, cy, r, kind, a in parts:
        px, py = hx + cx * sx, hy + cy * sy
        x0, x1 = max(0, math.floor(px - r * sx)), min(light.shape[1], math.ceil(px + r * sx) + 1)
        y0, y1 = max(0, math.floor(py - r * sy)), min(light.shape[0], math.ceil(py + r * sy) + 1)
        dx = (np.arange(x0, x1) + 0.5 - px) / sx
        dy = (np.arange(y0, y1) + 0.5 - py) / sy
        d = np.sqrt(dy[:, None] * dy[:, None] + dx[None, :] * dx[None, :])
        part = light[y0:y1, x0:x1]
        if kind == "spot":  # (the accent, fading out to its rim)
            k = a * np.maximum(0.0, 1 - d / r)
            for c in range(3):
                part[..., c] += colours[0][c] * k
        elif kind == "spark":  # (a small disc of white, its rim smooth)
            k = a * np.clip((r - d) * sx + 0.5, 0.0, 1.0)
            for c in range(3):
                part[..., c] += colours[1][c] * k
        else:  # (the halo: white in the middle, the accent, nothing at its rim)
            g = d / r
            for c in range(3):
                v0, v1, v2 = colours[1][c] * 0.95, colours[0][c] * 0.9, colours[0][c] * 0.25
                part[..., c] += np.where(
                    g < 0.1, v0 + (v1 - v0) * (g / 0.1),
                    np.where(g < 0.35, v1 + (v2 - v1) * ((g - 0.1) / 0.25),
                             np.where(g < 1, v2 * (1 - (g - 0.35) / 0.65), 0.0)))  # fmt: skip
    pixels = np.clip(np.rint(light), 0, 255).astype(np.uint8)
    return Image.fromarray(pixels, "RGB"), hx, hy


class Portal(Transition):
    """A ring of light opens from the middle (of all the panels, render/canvas.py), and the new
    screen grows through it into its place."""

    name = "portal"
    api = 1
    duration = 1.5
    toasts = "over"
    EDGE_MM = 2.0  # how soft the opening's edge is (the ring lies over it)
    GROWN = 0.12  # how much larger the new screen is at first
    GLOW_MM = 14.0  # how far the ring's light reaches to either side

    def __init__(self, context: Any = None, params: dict[str, Any] | None = None) -> None:
        super().__init__(context, params)
        self.canvas = take_canvas(self)
        self._made: tuple[tuple[int, int], _Opening] | None = None

    def _opening(self, size: tuple[int, int]) -> _Opening:
        if self._made is None or self._made[0] != size:
            opening = _Opening(self.canvas or alone(size), size, self.GLOW_MM, self.EDGE_MM)
            self._made = (size, opening)
        return self._made[1]

    def frame(self, old: Image.Image, new: Image.Image, t: float) -> Image.Image:
        if t >= 1.0:
            return new
        opening = self._opening(new.size)
        e = in_out_sine(t)
        mask = opening.map.point(threshold(255 * e, 255 * self.EDGE_MM / opening.far))
        out = _rgb(old).copy()
        box = mask.getbbox()
        if box is not None:  # (the new screen there, grown about the middle)
            w, h = new.size
            z = 1 + self.GROWN * (1 - e)
            zx, zy = opening.near
            x0, x1 = (min(w, max(0.0, zx + (box[i] - zx) / z)) for i in (0, 2))
            y0, y1 = (min(h, max(0.0, zy + (box[i] - zy) / z)) for i in (1, 3))
            grown = _rgb(new).resize(
                (box[2] - box[0], box[3] - box[1]), Image.Resampling.BILINEAR, box=(x0, y0, x1, y1)
            )
            out.paste(grown, box[:2], mask.crop(box))
        strength = math.sin(math.pi * clamp(t)) ** 0.6
        if strength > 0.004:
            accent, white = role_color(self.context, "info"), role_color(self.context, "text")
            _ring(out, opening, e * opening.far, strength, accent, white, self.GLOW_MM)
        return out


class _Opening:
    """What a portal works out once for a frame's size: how far it opens and its middle
    (millimetres), the map of the opening, where the new screen grows about (pixels), the
    panel's pixels by their level in the map, how far each of those is from the middle
    (millimetres) and where each level begins among them, the pixels per millimetre and the
    ring's light beside it."""

    __slots__ = ("at", "away", "by_level", "far", "light", "map", "near", "per_mm", "starts")

    def __init__(self, canvas: Canvas, size: tuple[int, int], reach_mm: float, edge_mm: float):
        self.at = middle(canvas.whole)
        self.far = reach(canvas.whole, self.at) + edge_mm
        x, y, w, h = canvas.own  # (it grows about the middle, or the nearest point of this
        near = (min(max(self.at[0], x), x + w), min(max(self.at[1], y), y + h))  # panel to it)
        self.near = pixel(canvas, size, near)
        away = distances(canvas, size, self.at)
        levelled = np.asarray(levels(255 * away / self.far))
        self.map = Image.fromarray(levelled, "L")
        flat = levelled.ravel()
        self.by_level = np.argsort(flat, kind="stable").astype(np.int32)
        self.away = away.ravel()[self.by_level]
        self.starts = np.concatenate(([0], np.cumsum(np.bincount(flat, minlength=256))))
        self.per_mm = per_mm(canvas, size)[0]
        self.light = _ring_light(reach_mm, self.per_mm)


RING_STEPS = 64  # the ring's light worked out for every 64th of a millimetre beside it


def _ring_light(reach_mm: float, spx: float) -> tuple[np.ndarray, np.ndarray]:
    """The ring's light beside it at its full strength, for each step from ``-reach_mm`` to
    ``reach_mm`` (``spx`` pixels a millimetre): in the accent, a faint wide band, its bright line
    and the line's glow (blurred by a Gaussian of 4 mm, widened by the line); in white, a thin
    line in its middle. A band's edge covers as much of a pixel as lies within it."""
    spread = 2 * 4.05 * 4.05
    tinted, whitened = [], []
    for k in range(round(2 * reach_mm * RING_STEPS)):
        off = (k + 0.5) / RING_STEPS - reach_mm
        away = abs(off)

        def band(half: float, away: float = away) -> float:
            return min(1.0, max(0.0, (half - away) * spx + 0.5))

        glow = 0.22 * math.exp(-(off**2) / spread)
        tinted.append(0.18 * band(4.5) + glow + 0.85 * band(1.1))
        whitened.append(0.8 * band(0.25))
    return np.array(tinted), np.array(whitened)


def _ring(out: Image.Image, opening: _Opening, r: float, strength: float, accent: RGB,
          white: RGB, reach_mm: float) -> None:  # fmt: skip
    """The ring of light at ``r`` millimetres from the middle, ``strength`` of it, added onto
    ``out``: a faint wide band and a bright line with its glow in the accent, a thin white line
    in its middle. Only the pixels near it are worked out: those whose level in the map is
    near its own, then those as near as its light reaches, each lit as its step beside it."""
    far = opening.far
    lo = max(0, math.floor(255 * (r - reach_mm) / far) - 1)
    hi = min(255, math.ceil(255 * (r + reach_mm) / far) + 1)
    among = slice(opening.starts[lo], opening.starts[hi + 1])
    off = opening.away[among] - r
    near = np.abs(off) < reach_mm
    if not near.any():
        return
    found, off = opening.by_level[among][near], off[near]
    # (a pixel a rounding short of its reach takes the last step, not one past it)
    step = np.minimum(((off + reach_mm) * RING_STEPS).astype(np.int64), len(opening.light[0]) - 1)
    tinted, whitened = strength * opening.light[0], strength * opening.light[1]
    planes = []
    for c in range(3):
        lit = np.clip(np.rint(accent[c] * tinted + white[c] * whitened), 0, 255).astype(np.uint8)
        plane = np.zeros(out.width * out.height, np.uint8)
        plane[found] = lit[step]
        planes.append(Image.fromarray(plane.reshape(out.height, out.width), "L"))
    out.paste(ImageChops.add(out, Image.merge("RGB", planes)))


class Morph(Transition):
    """What both screens show (the time, a reading in the same form, the album cover: see
    render/morph.py) flies from its old place to its new one, on an arc, growing or shrinking
    to its new size and turning into its new look on the way; the rest of the old screen fades
    out and the new one's fades in after it, so the things in flight stand out; at the end the
    new screen as it is drawn now."""

    name = "morph"
    api = 1
    duration = 2.1
    toasts = "over"
    OUT = 0.55  # the old screen fades out by then (without what flies)
    IN = (0.35, 0.9)  # and the new one in, between these (without what flies)
    LAST = 0.9  # from here the new screen as it is drawn now takes over
    FLIGHT = (0.06, 0.9)  # when the things fly (eased slow, fast, slow)
    TURN = (0.35, 0.65)  # where along its flight a thing turns into its new look
    LIFT_MM = 7.0  # how high a thing's arc rises at its middle (downward where it has no room)

    def __init__(self, context: Any = None, params: dict[str, Any] | None = None) -> None:
        super().__init__(context, params)
        self._before = getattr(context, "before", None)  # (the old screen: Renderer.parting)
        self._made: Any = None

    def set_before(self, before: Any) -> None:
        """The old screen as its renderer composed it last (Renderer.parting): what it takes
        apart (the app gives it once the transition is chosen)."""
        self._before = before

    def _flights(self, new: Image.Image) -> Any:
        """The old screen without what flies, the new one without it, the things that fly
        (each its old part and its new one, the largest first) and the dark under them; worked
        out once both screens are known. None where there is nothing to take apart (the old
        screen unknown, or of another size): it fades."""
        if self._made is not None:
            return self._made if self._made[0] == new.size else None
        before = self._before
        if before is None or before.size != new.size:
            return None
        renderer = getattr(self.context, "_renderer", None)
        after = getattr(renderer, "parting", lambda: None)()
        if after is None or after.size != new.size:
            return None  # (not drawn yet: asked again at the next frame)
        old_things, new_things = things(before), things(after)
        shared = sorted(old_things.keys() & new_things.keys(),
                        key=lambda k: (-_area(old_things[k][1]), k))  # fmt: skip
        old_without, old_parts = take_apart(before, {k: old_things[k] for k in shared})
        new_without, new_parts = take_apart(after, {k: new_things[k] for k in shared})
        flights = [(old_parts[k], new_parts[k]) for k in shared]
        self._made = (new.size, old_without, new_without, flights, Image.new("RGB", new.size))
        self._before = None  # (what it held is in the parts now)
        return self._made

    def frame(self, old: Image.Image, new: Image.Image, t: float) -> Image.Image:
        if t >= 1.0:
            return new
        made = self._flights(new)
        if made is None:  # (nothing to take apart: a fade)
            return Image.blend(_rgb(old), _rgb(new), smooth(t))
        _, old_without, new_without, flights, dark = made
        out = _mixed(dark, old_without, 1 - smooth(t / self.OUT))
        out = _mixed(out, new_without, smooth((t - self.IN[0]) / (self.IN[1] - self.IN[0])))
        if out is dark or out is old_without or out is new_without:
            out = out.copy()  # (the things are laid on it)
        a, b = self.FLIGHT
        e = in_out((t - a) / (b - a))
        turned = smooth((e - self.TURN[0]) / (self.TURN[1] - self.TURN[0]))
        high = self.LIFT_MM * per_mm(alone(new.size), new.size)[1]
        lift = high * math.sin(math.pi * e)
        for was, comes in flights:
            ax, ay, aw, ah = was.box
            bx, by, bw, bh = comes.box
            rise = -lift if min(ay, by) >= high else lift  # (up where it has room for its arc)
            middle = (ax + aw / 2 + (bx + bw / 2 - ax - aw / 2) * e,
                      ay + ah / 2 + (by + bh / 2 - ay - ah / 2) * e + rise)  # fmt: skip
            room = (aw + (bw - aw) * e, ah + (bh - ah) * e)
            lay(out, was, middle, room, 1 - turned)
            lay(out, comes, middle, room, turned)
        return _mixed(out, _rgb(new), smooth((t - self.LAST) / (1 - self.LAST)))


def _area(box: tuple[int, int, int, int]) -> int:
    return (box[2] - box[0]) * (box[3] - box[1])


def _mixed(a: Image.Image, b: Image.Image, share: float) -> Image.Image:
    """``b`` over ``a`` by ``share``: ``a`` itself at none, ``b`` itself at all of it."""
    if share <= 0:
        return a
    if share >= 1:
        return b
    return Image.blend(a, b, share)


# the transitions this module brings, by name (render/overlays.py adds them to the built-in ones)
HANDOVERS = {cls.name: cls for cls in (Wipe, Iris, Blinds, Dissolve, SlideRight, SlideUp, SlideDown,
                                       Zoom, LightLine, Glitch, Tiles, Shatter, Cascade, Comet,
                                       Portal, Morph)}  # fmt: skip

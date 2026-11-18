"""The morph's parts (render/handovers.py's Morph): what each widget shows, so that the same
thing is found on the next screen; a frame as it was composed (Renderer.parting) taken apart
into the frame without what flies and each thing that flies, alone; and a thing laid where its
flight has got to.

What the morph flies is what both screens show, once each and together: the time (a clock,
digital or a face; the seconds beside it go with it), the date, a reading in the same form (a
ring, a bar, a graph, a number, a fan, an agent's sign of the same sensor), a live picture (an
album cover), the moon, the earth, the sun's way, the month. What is the same on any screen (a
frame, a text, an icon) or shown more than once, apart, stays with its screen and fades.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PIL import Image

# the strftime fields of a time of day: a clock with one of them shows the time, else the date
TIME_FIELDS = "HIklMSpPXRTrc"
# a reading's form by the widget's type
FORMS = {"gauge": "ring", "bar": "bar", "graph": "graph", "metric": "number", "fan": "fan",
         "agent": "sign", "symbol": "symbol"}  # fmt: skip
# what is shown once, whatever reading: the type is the thing
ALONE = {"moon": "moon", "earth": "earth", "sunpath": "sunpath", "calendar": "calendar",
         "download": "download"}  # fmt: skip
# a thing's pieces lie together: their box is at most this much larger than they are
TOGETHER = 1.5


def shown_as(widget: dict[str, Any]) -> str | None:
    """What ``widget`` shows, for the morph to find it on the next screen; None for what is
    the same on any screen or what this cannot tell."""
    kind = widget.get("type")
    if not isinstance(kind, str):
        return None
    if kind == "clock":
        form = str(widget.get("format") or "%H:%M")
        return "time" if any(f"%{c}" in form for c in TIME_FIELDS) else "date"
    if kind == "analog":
        return "time"
    if kind in ALONE:
        return ALONE[kind]
    if kind == "countdown":
        return f"countdown:{widget.get('target', '')}"
    if kind == "weather":
        return f"weather:{widget.get('field', '')}"
    if kind == "image":
        src = widget.get("src")
        return f"picture:{src[1:]}" if isinstance(src, str) and src.startswith("@") else None
    sensor = widget.get("sensor")
    if kind in FORMS and isinstance(sensor, str) and sensor:
        return f"{FORMS[kind]}:{sensor}"
    return None


@dataclass(eq=False)
class Parting:
    """A frame as it was composed (Renderer.parting): its ground (the panel's size, in the
    frame's mode), its pieces in their order, what each widget on it shows (shown_as) and the
    frame they made (None: made here from them where it is asked for)."""

    ground: Image.Image
    pieces: list[tuple[str, Any]]
    shows: dict[str, str]
    opaque: bool
    frame: Image.Image | None = None

    @property
    def size(self) -> tuple[int, int]:
        return self.ground.size


@dataclass(eq=False)
class Part:
    """A thing that flies: its box on its panel ([x, y, w, h]), its picture alone (RGBA) and
    the light its pieces add (RGB), if any."""

    box: tuple[int, int, int, int]
    body: Image.Image
    light: Image.Image | None


def _extent(piece: Any, size: tuple[int, int]) -> tuple[int, int, int, int] | None:
    """What a piece covers on its panel (Renderer._extent), within it; None for nothing."""
    from libre_panel.render.renderer import Renderer

    x0, y0, x1, y1 = Renderer._extent(piece)
    x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, size[0]), min(y1, size[1])
    return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else None


def things(parting: Parting) -> dict[str, tuple[list[int], tuple[int, int, int, int]]]:
    """Each thing the frame shows once, together: its pieces (their places in the frame's
    order: a widget's own and its light's) and the box around them ([x0, y0, x1, y1]); what is
    shown in pieces apart (a reading as a number here and there) is none, and what covers all
    of the panel (a cover's glow behind everything) is the ground, not a thing."""
    whole = (0, 0, *parting.size)
    found: dict[str, list[int]] = {}
    for i, (wid, piece) in enumerate(parting.pieces):
        key = parting.shows.get(wid.split("\0", 1)[0])
        if key is not None and _extent(piece, parting.size) != whole:
            found.setdefault(key, []).append(i)
    together: dict[str, tuple[list[int], tuple[int, int, int, int]]] = {}
    for key, places in found.items():
        boxes = [b for b in (_extent(parting.pieces[i][1], parting.size) for i in places) if b]
        if not boxes:
            continue
        box = (min(b[0] for b in boxes), min(b[1] for b in boxes),
               max(b[2] for b in boxes), max(b[3] for b in boxes))  # fmt: skip
        own = sum((b[2] - b[0]) * (b[3] - b[1]) for b in boxes)
        if (box[2] - box[0]) * (box[3] - box[1]) <= TOGETHER * own:
            together[key] = (places, box)
    return together


def take_apart(
    parting: Parting, taken: dict[str, tuple[list[int], tuple[int, int, int, int]]]
) -> tuple[Image.Image, dict[str, Part]]:
    """The frame without the pieces ``taken`` (in RGB), and each thing taken alone: its pieces
    laid on nothing in their order (a light added apart)."""
    from libre_panel.render.renderer import Renderer

    gone = {i for places, _ in taken.values() for i in places}
    whole = (0, 0, *parting.size)
    if parting.frame is None:  # (the frame from its ground up, without them)
        canvas, regions = parting.ground.copy(), [whole]
    else:  # (the frame as it was, laid again only where they lay)
        canvas, regions = parting.frame.copy(), _where(parting, gone)
    for region in regions:
        part = parting.ground.crop(region)
        for i, (_wid, piece) in enumerate(parting.pieces):
            reach = _extent(piece, parting.size)
            if i in gone or reach is None or not _meet(reach, region):
                continue
            try:
                Renderer._composite(part, piece, region[:2], parting.opaque)
            except Exception:  # (one broken widget must not stop the morph: as the frame)
                continue
        canvas.paste(part, region[:2])
    without = canvas if canvas.mode == "RGB" else canvas.convert("RGB")
    parts: dict[str, Part] = {}
    for key, (places, (x0, y0, x1, y1)) in taken.items():
        size = (x1 - x0, y1 - y0)
        body = Image.new("RGBA", size, (0, 0, 0, 0))
        light = Image.new("RGB", size, (0, 0, 0))
        lit = False
        for i in places:
            piece = parting.pieces[i][1]
            try:
                if piece.add:
                    Renderer._add_light(light, piece.layer, piece.x - x0, piece.y - y0)
                    lit = True
                    continue
                Renderer._paste(body, piece.layer, piece.x - x0, piece.y - y0)
                if piece.light is not None:
                    glow = piece.light
                    Renderer._add_light(light, glow.layer, glow.x - x0, glow.y - y0)
                    lit = True
            except Exception:
                continue
        parts[key] = Part((x0, y0, size[0], size[1]), body, light if lit else None)
    return without, parts


def _meet(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _where(parting: Parting, gone: set[int]) -> list[tuple[int, int, int, int]]:
    """Where the pieces ``gone`` left their pixels ([x0, y0, x1, y1], one each), each grown over
    the whole box of every frosted backdrop it touches (its blur takes all of the box under
    it), until none grows: what is laid again without them."""
    regions = [r for r in (_extent(parting.pieces[i][1], parting.size) for i in sorted(gone)) if r]
    frosted = []
    for _wid, piece in parting.pieces:
        if piece.backdrop is not None and piece.box is not None:
            x, y, w, h = piece.box
            box = (max(x, 0), max(y, 0), min(x + w, parting.size[0]), min(y + h, parting.size[1]))
            if box[2] > box[0] and box[3] > box[1]:
                frosted.append(box)
    grown = True
    while grown:
        grown = False
        for n, r in enumerate(regions):
            for b in frosted:
                if _meet(r, b) and not (b[0] >= r[0] and b[1] >= r[1] and b[2] <= r[2]
                                        and b[3] <= r[3]):  # fmt: skip
                    r = (min(r[0], b[0]), min(r[1], b[1]), max(r[2], b[2]), max(r[3], b[3]))
                    regions[n], grown = r, True
    return regions


def faded(levels: float) -> list[int]:
    """A table that scales each level by ``levels`` (0 to 1), rounded as Python rounds."""
    return [round(v * levels) for v in range(256)]


def lay(
    out: Image.Image,
    part: Part,
    middle: tuple[float, float],
    room: tuple[float, float],
    alpha: float,
) -> None:
    """``part`` laid on ``out`` (RGB) at ``alpha``: as large as fits ``room`` (its own shape
    kept), its middle at ``middle``, its light added."""
    from libre_panel.render.renderer import Renderer

    if alpha <= 0:
        return
    bw, bh = part.body.size
    ratio = bw / bh
    dw, dh = room[0], room[0] / ratio
    if dh > room[1]:
        dw, dh = room[1] * ratio, room[1]
    size = (max(1, round(dw)), max(1, round(dh)))
    x, y = round(middle[0] - dw / 2), round(middle[1] - dh / 2)
    body = part.body
    if size != body.size:
        body = body.resize(size, Image.Resampling.BICUBIC)
    if alpha < 1:
        body = body.copy() if body is part.body else body
        body.putalpha(body.getchannel("A").point(faded(alpha)))
    out.paste(body, (x, y), body)
    if part.light is not None:
        light = part.light
        if size != light.size:
            light = light.resize(size, Image.Resampling.BICUBIC)
        if alpha < 1:
            light = light.point(faded(alpha) * 3)
        Renderer._add_light(out, light, x, y)

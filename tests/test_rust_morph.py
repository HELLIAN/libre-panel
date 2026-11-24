"""The morph's own judgements in Rust (rust/render/src/morph.rs) as the Python app's
render/morph.py made them: what a widget shows, which pieces of a frame are one thing that
flies, and where the frame is laid again without them. The frames themselves are compared in
test_rust_transitions.py; these are the choices before them, each of which the frames there
may not happen to meet. What morph.py answered is kept (golden.py): lp-check's answers are
checked against it."""

from __future__ import annotations

import base64
import random
import struct
from typing import Any

import pytest
from PIL import Image
from rust_check import run

pytestmark = pytest.mark.kept  # (what morph.py answered: tests/golden)


def _bits(x: float) -> str:
    return struct.pack(">d", x).hex()


def _typed(v: Any) -> Any:
    """A value as lp-check's typed() takes it."""
    if isinstance(v, bool):
        return {"b": v}
    if isinstance(v, int):
        return {"i": str(v)}
    if isinstance(v, float):
        return {"f": _bits(v)}
    if isinstance(v, str):
        return {"s": v}
    if isinstance(v, list):
        return {"l": [_typed(x) for x in v]}
    if isinstance(v, dict):
        return {"d": {k: _typed(x) for k, x in v.items()}}
    return None


def _things_case(rng: random.Random) -> dict[str, Any]:
    """A frame of blank pieces: widgets that show the same in pieces near together and apart,
    lights, frosted backdrops, pieces off the panel and over all of it."""
    w, h = rng.choice([(1920, 720), (1920, 480), (480, 480), (320, 170)])
    ids = [f"w{i}" for i in range(rng.randint(2, 8))]
    keys = ["time", "number:cpu.load", "ring:gpu.load", "picture:media.cover"]
    shows = {i: rng.choice(keys) for i in ids if rng.random() < 0.8}
    pieces = []
    for _ in range(rng.randint(2, 14)):
        wid = rng.choice(ids) + rng.choice(["", "", "\0glow", "\0x\0y"])
        if rng.random() < 0.07:
            x, y, pw, ph = 0, 0, w, h  # (over all of the panel: the ground)
        else:
            pw, ph = rng.randint(1, w // 3), rng.randint(1, h // 2)
            x, y = rng.randint(-pw - 20, w + 20), rng.randint(-ph - 20, h + 20)
        piece: dict[str, Any] = {"wid": wid, "x": x, "y": y, "w": pw, "h": ph,
                                 "add": rng.random() < 0.1}  # fmt: skip
        if rng.random() < 0.2:
            piece["light"] = [x - 6, y - 6, pw + 12, ph + 12]
        if rng.random() < 0.15:
            piece["frosted"] = [x + rng.randint(-40, 40), y + rng.randint(-40, 40),
                                rng.randint(1, w // 2), rng.randint(1, h // 2)]  # fmt: skip
        pieces.append(piece)
    gone = sorted(rng.sample(range(len(pieces)), rng.randint(0, len(pieces))))
    return {"size": [w, h], "pieces": pieces, "shows": shows, "gone": gone}


def _things_edges() -> list[dict[str, Any]]:
    """Two pieces of one thing whose box is just 1.5 times theirs, a little less and twice it,
    the first of them neither the farthest left nor the lowest; frosted boxes that only touch
    a region on its left, that lie inside it to its edges, and that reach beyond it."""
    out: list[dict[str, Any]] = []
    for gap, height in ((10, 10), (6, 10), (20, 10), (0, 30)):
        pieces = [{"wid": "a", "x": 30 + gap, "y": 40, "w": 10, "h": height},
                  {"wid": "a\0glow", "x": 20, "y": 40, "w": 10, "h": 10}]  # fmt: skip
        out.append({"size": [200, 100], "shows": {"a": "time"}, "gone": [1], "pieces": pieces})
    for frosted in ([0, 0, 20, 10], [20, 0, 10, 10], [22, 2, 6, 8], [25, 5, 30, 30],
                    [0, 0, 200, 100]):  # fmt: skip
        out.append({"size": [200, 100], "shows": {}, "gone": [0],
                    "pieces": [{"wid": "a", "x": 20, "y": 0, "w": 10, "h": 10},
                               {"wid": "b", "x": 60, "y": 60, "w": 5, "h": 5,
                                "frosted": frosted}]})  # fmt: skip
    return out


def test_what_the_morph_takes_as_one_thing_as_python_takes_it() -> None:
    """morph.things (each thing a frame shows once: its pieces near together, its box) and
    morph._where (where the frame is laid again without what flies, grown over the frosted
    backdrops it touches) for frames of blank pieces, as Python takes them."""
    rng = random.Random(0x7A16)
    cases = _things_edges() + [_things_case(rng) for _ in range(600)]
    for case in cases:
        for p in case["pieces"]:
            p.setdefault("add", False)
    got = run([{"op": "things", **case} for case in cases])
    # (the cases find things, and lay the frame again where pieces went)
    assert sum(len(g["things"]) for g in got) > 300
    assert sum(bool(g["again"]) for g in got) > 300


def test_what_each_widget_shows_as_python_says_it() -> None:
    """morph.shown_as, what a widget shows for the morph to find it on the next screen, as
    Python says it for every kind: each clock field alone and clocks of no format, the things
    shown once, countdowns and weather by what they count and show, pictures live and not,
    readings of every form, of no sensor and of an empty one, and values of other types."""
    widgets: list[dict[str, Any]] = [{"type": "clock", "format": f"%{c}"}
                                     for c in "aAbBcdHIjklmMpPRrSTXxyYZz%"]  # fmt: skip
    widgets += [{"type": "clock", "format": form}
                for form in ("", 0, False, None, "%d.%m.", "x%Sx", 5, "%%H")]  # fmt: skip
    widgets += [{"type": "clock"}, {"type": "analog"}, {"type": "analog", "sensor": "x"}]
    widgets += [{"type": kind} for kind in ("moon", "earth", "sunpath", "calendar",
                                            "download", "rect", "text", "icon", "", "Clock",
                                            "Moon")]  # fmt: skip
    for value in ("2027-01-01", "", 5, 1.5, True, None, [1, 2]):
        widgets.append({"type": "countdown", "target": value, "field": "days"})
        widgets.append({"type": "weather", "field": value, "target": "x"})
    widgets += [{"type": "countdown", "field": "x"}, {"type": "weather", "target": "x"}]
    widgets += [{"type": "image", "src": src}
                for src in ("@media.cover", "@", "assets/a.png", "", "media.cover", 5,
                            None)]  # fmt: skip
    widgets += [{"type": "image"}, {"type": "image", "sensor": "media.cover"}]
    for kind in ("gauge", "bar", "graph", "metric", "fan", "agent", "symbol", "rect", "text"):
        widgets.append({"type": kind})
        widgets += [{"type": kind, "sensor": sensor}
                    for sensor in ("cpu.load", "", None, 5, ["cpu.load"])]  # fmt: skip
    widgets += [{"type": kind, "sensor": "cpu.load"} for kind in (None, 5, True, ["clock"])]
    widgets.append({"sensor": "cpu.load"})
    [got] = run([{"op": "shown", "widgets": [_typed(w) for w in widgets]}])
    assert len(got["shown"]) == len(widgets)


def _noise(rng: random.Random, mode: str, size: tuple[int, int]) -> Image.Image:
    """A picture of random pixels (every level of alpha among them)."""
    return Image.frombytes(mode, size, rng.randbytes(size[0] * size[1] * len(mode)))


def _image(im: Image.Image) -> dict[str, Any]:
    return {"mode": im.mode, "size": list(im.size),
            "data": base64.b64encode(im.tobytes()).decode("ascii")}  # fmt: skip


def _apart_case(rng: random.Random) -> dict[str, Any]:
    """A frame of pieces of random pixels, lights among them and lights of their own, on its
    ground with the frame they made (any picture: what lies outside the pieces taken stays
    the frame's) or none; some of them taken as things, each laid at whole, half, a quarter,
    nothing and any alpha, as large as its own box and as large as any room."""
    w, h = rng.choice([(320, 170), (200, 120), (160, 160)])
    opaque = rng.random() < 0.8
    mode = "RGB" if opaque else "RGBA"
    ground = _noise(rng, mode, (w, h))
    frame = _noise(rng, mode, (w, h)) if rng.random() < 0.6 else None
    pieces, lie = [], []
    for i in range(rng.randint(2, 8)):
        pw, ph = rng.randint(1, w // 2), rng.randint(1, h // 2)
        x, y = rng.randint(-20, w + 10), rng.randint(-20, h + 10)
        layer = _noise(rng, "RGBA", (pw, ph))
        told: dict[str, Any] = {"wid": f"w{i}", "image": _image(layer), "x": x, "y": y,
                                "add": rng.random() < 0.15}  # fmt: skip
        if rng.random() < 0.25:
            glow = _noise(rng, "RGBA", (pw + 8, ph + 8))
            told["light"] = {"image": _image(glow), "x": x - 4, "y": y - 4}
        pieces.append(told)
        lie.append((x, y, x + pw, y + ph))
    order = list(range(len(pieces)))
    rng.shuffle(order)
    taken: dict[str, tuple[list[int], tuple[int, int, int, int]]] = {}
    for k in range(rng.randint(1, min(3, len(order)))):
        places = sorted(order[k::3]) or [order[k]]
        boxes = [lie[i] for i in places]
        box = (min(b[0] for b in boxes), min(b[1] for b in boxes),
               max(b[2] for b in boxes), max(b[3] for b in boxes))  # fmt: skip
        taken[f"thing{k}"] = (places, box)
    lays = []
    for alpha in (1.0, 0.5, 0.25, 0.0, rng.random()):
        lays.append([rng.uniform(-10, w + 10), rng.uniform(-10, h + 10),
                     rng.uniform(4, w), rng.uniform(4, h), alpha])  # fmt: skip
    case = {"op": "apart", "digest": True, "ground": _image(ground), "opaque": opaque,
            "pieces": pieces, "taken": [[k, p, list(b)] for k, (p, b) in taken.items()],
            "lays": lays, "own": True}  # fmt: skip
    if frame is not None:
        case["frame"] = _image(frame)
    return case


def test_what_flies_is_taken_apart_and_laid_as_python_does_it() -> None:
    """morph.take_apart (the frame without what flies, laid again where it lay; each thing
    alone, its lights added apart from its body) and morph.lay (a part laid as large as fits
    its room, faded to its alpha: the levels rounded half to even) for frames of random
    pixels, as Python makes them, to the bit."""
    rng = random.Random(0x7A17)
    cases = [_apart_case(rng) for _ in range(150)]
    got = run(cases)
    # (lights of their own and lights added apart, frames kept, faded halves)
    assert sum(1 for g in got for p in g["parts"].values() if p["light"]) > 60
    assert sum(1 for c in cases if "frame" in c) > 60


def _seen_case(rng: random.Random) -> list[dict[str, Any]]:
    """Two to four panels side by side and below, each showing some of a few things on its old
    screen and its new one: on the same panel, on one panel and another, on several."""
    keys = ["time", "ring:cpu.load", "picture:media.cover", "number:gpu.temp"]
    seen = []
    for panel in range(rng.randint(2, 4)):
        size = rng.choice([(1920, 720), (1920, 480), (480, 480)])
        own = [panel * 300.5 + rng.uniform(-5, 5), rng.choice([0.0, 120.0]),
               rng.uniform(100, 300), rng.uniform(40, 120)]  # fmt: skip
        told: dict[str, Any] = {"panel": panel, "own": own, "size": list(size)}
        for screen in ("old", "new"):
            things = {}
            for key in keys:
                if rng.random() < 0.45:
                    x0, y0 = rng.randrange(size[0] - 10), rng.randrange(size[1] - 10)
                    box = [x0, y0, rng.randint(x0 + 1, size[0]), rng.randint(y0 + 1, size[1])]
                    things[key] = [[rng.randrange(9)], box]
            told[screen] = things
        seen.append(told)
    return seen


def test_what_flies_from_panel_to_panel_as_python_pairs_it() -> None:
    """morph.paired over the panels: a thing on its own panel where both its screens show it,
    else from the one panel left that showed it to the one left that shows it (none where more
    are left on either side), the largest first, as Python pairs them for two to four
    panels."""
    rng = random.Random(0x7A18)
    cases = [_seen_case(rng) for _ in range(400)]
    got = run([{"op": "paired", "seen": case} for case in cases])
    # (things that stay on their panel, that cross, and that are left where more are left)
    flights = [f for g in got for f in g["flights"]]
    assert sum(1 for _k, a, b in flights if a == b) > 100
    assert sum(1 for _k, a, b in flights if a != b) > 50

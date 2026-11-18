"""The light field under a theme in Rust (rust/render/src/field.rs) as render/field.py makes
it, to the bit: its levels at an eighth of the panel and the picture drawn up, for panels of
every size where they stand in canvases of one to three panels, at moments over hours and under
every load; and that it runs on from one panel to the next."""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import random
import struct
from pathlib import Path
from typing import Any

import numpy as np
from rust_check import picture, run
from test_rust_render import (
    _card_or_text,
    _compare_moving,
    _demo,
    _frame_snapshot,
    _frame_theme,
    _snapshot_given,
)

from libre_panel import fonts
from libre_panel.render import cover, field
from libre_panel.render.canvas import Canvas
from libre_panel.render.renderer import Renderer
from libre_panel.sensors.base import Snapshot
from libre_panel.theme.model import ThemeError, load_theme


def _bits(x: float) -> str:
    return struct.pack(">d", x).hex()


def _case(rng: random.Random) -> dict[str, Any]:
    w, h = rng.choice([(1920, 720), (1920, 480), (480, 480), (320, 170), (800, 480), (101, 37)])
    per = rng.uniform(0.06, 0.2)  # (millimetres a pixel)
    own = [rng.uniform(-300, 300), rng.uniform(-100, 100), w * per, h * per]
    left, top = own[0] - rng.uniform(0, 400) * rng.random(), own[1] - rng.uniform(0, 60)
    whole = [left, top, own[0] + own[2] - left + rng.uniform(0, 400) * rng.random(),
             own[1] + own[3] - top + rng.uniform(0, 60)]  # fmt: skip
    t = rng.choice([0.0, 1e9 + rng.uniform(0, 86400), rng.uniform(0, 2000)])
    load = rng.choice([0.0, 1.0, rng.random(), -0.5, 1.7])
    base = rng.choice([[0, 0, 0], [10, 14, 19], [rng.randrange(256) for _ in range(3)]])
    look = rng.choice(sorted(field.LOOKS))
    return {"size": [w, h], "own": own, "whole": whole, "t": t, "load": load, "base": base,
            "look": look}  # fmt: skip


def _wired(case: dict[str, Any], digest: bool) -> dict[str, Any]:
    return {"op": "field", "look": case["look"], "size": case["size"], "digest": digest,
            "own": [_bits(v) for v in case["own"]], "whole": [_bits(v) for v in case["whole"]],
            "t": _bits(case["t"]), "load": _bits(case["load"]), "base": case["base"]}  # fmt: skip


def test_the_light_field_as_python_makes_it() -> None:
    rng = random.Random(0xF1E1D)
    cases = [_case(rng) for _ in range(120)]
    small = [c for c in cases if c["size"][0] * c["size"][1] < 500_000]
    got = run([_wired(c, digest=c not in small) for c in cases])
    checked = 0
    assert {c["look"] for c in cases} == set(field.LOOKS)
    for case, answer in zip(cases, got, strict=True):
        args = (tuple(case["size"]), tuple(case["own"]), tuple(case["whole"]), case["t"],
                case["load"], case["look"], tuple(case["base"]))  # fmt: skip
        levels = field.small(*args)
        fw, fh, data = answer["small"]
        assert (fw, fh) == (levels.shape[1], levels.shape[0]), case
        assert base64.b64decode(data) == levels.tobytes(), case
        made = field.field(*args)
        if case in small:
            assert picture(answer["field"]).tobytes() == made.tobytes(), case
        else:
            assert answer["field"]["sha256"] == hashlib.sha256(made.tobytes()).hexdigest(), case
        checked += 1
    assert checked == len(cases) and len(small) > 20


def test_the_field_runs_on_from_one_panel_to_the_next() -> None:
    """Two panels as one canvas show, point by point, what one panel as large as both shows; a
    step of the field lasts 0.16 s on the wall clock, the same for every panel."""
    whole = (0.0, 0.0, 200.0, 50.0)
    left = field.small((800, 400), (0.0, 0.0, 100.0, 50.0), whole, 37.0, 0.4)
    right = field.small((800, 400), (100.0, 0.0, 100.0, 50.0), whole, 37.0, 0.4)
    both = field.small((1600, 400), whole, whole, 37.0, 0.4)
    assert np.array_equal(np.concatenate([left, right], axis=1), both)
    assert field.stepped(1.159) == 1.12 and field.stepped(1.119) == 0.96
    idle = field.small((800, 400), whole, whole, 0.0, 0.0).astype(int).sum()
    busy = field.small((800, 400), whole, whole, 0.0, 1.0).astype(int).sum()
    assert busy > idle * 1.5


# -- frames over the light, step after step ---------------------------------------------------

LIGHT_SWITCHES = {"light_flows": "The light drifts", "light_load": "The light follows the load"}


def _lit(folder: Path, rng: random.Random, look: str | None = None) -> None:
    """The theme in `folder` with a light field under it: drifting or still, following the load
    or at rest, by a switch of the theme (on or off) or always; of any look, in the colours of
    the cover while there is one or not."""
    path = folder / "theme.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    spec: dict[str, Any] = {"look": look or rng.choice(sorted(field.LOOKS))}
    if rng.random() < 0.45:
        spec["colours"] = "@media.cover"
    for key, switch in (("moves", "light_flows"), ("load", "light_load")):
        spec[key] = rng.choice(["", f"switch.{switch}", f"!switch.{switch}"])
    data.setdefault("background", {})["field"] = spec
    switches = data.setdefault("switches", {})
    for name, label in LIGHT_SWITCHES.items():
        switches[name] = {"label": label, "on": rng.random() < 0.7}
    path.write_text(json.dumps(data), encoding="utf-8")


def _loads(rng: random.Random, n: int) -> list[float]:
    """A load's last `n` numbers: busy, idle, rising, none."""
    kind = rng.choice(["busy", "idle", "rising", "mixed"])
    if kind == "busy":
        return [rng.uniform(85, 100) for _ in range(n)]
    if kind == "idle":
        return [rng.uniform(0, 4) for _ in range(n)]
    if kind == "rising":
        return [100 * k / max(1, n - 1) for k in range(n)]
    return [rng.choice([0, 3.5, 47.0, 100, 120, -5]) for _ in range(n)]


def _covers() -> list[Any]:
    """Covers a track may have: the demo's, a warm and a cold one, one of greys (no colour of
    its own), a small dark one, and none."""
    from PIL import Image, ImageDraw

    from libre_panel.sensors.demo import demo_cover

    def made(a: tuple[int, int, int], b: tuple[int, int, int], size: int = 48) -> Image.Image:
        im = Image.new("RGB", (size, size), a)
        ImageDraw.Draw(im).ellipse((size // 4, size // 3, size, size), fill=b)
        return im

    return [
        demo_cover(64),
        made((200, 40, 30), (250, 180, 40)),
        made((20, 40, 120), (60, 200, 230)),
        made((90, 90, 90), (200, 200, 200)),
        made((40, 10, 30), (90, 20, 60), 20),
        None,
    ]


COVERS = _covers()


def _lit_steps(rng: random.Random, snapshot: Snapshot) -> tuple[list[Any], list[Any]]:
    """Frames of a panel one after another, as its loop draws them at 25 a second: a new
    reading now and then (its history of loads grown by one, the light easing to it over the
    second after; a new cover or none now and then, the light gliding to its colours), the
    frames a 25th to a second apart, now and then at the very step of the light or back in
    time. What Python's renderer and lp-check are each given."""
    now = rng.choice([1000.0, 1_790_000_000.0 + rng.uniform(0, 86400), rng.uniform(0, 5000)])
    history = {key: _loads(rng, rng.randrange(0, 14)) for key in field.LOAD_SENSORS
               if rng.random() < 0.8}  # fmt: skip
    images = dict(snapshot.images)
    images.pop("media.cover", None)
    cover = rng.choice(COVERS)
    python, given = [], []
    for _ in range(rng.randrange(6, 16)):
        sample = None
        if rng.random() < 0.35:  # (a reading came: its loads one longer)
            for values in history.values():
                values.append(rng.choice([0.0, 12.5, 64.0, 99.0, 100.0]))
            sample = (now, rng.choice([1.0, 0.5, 2.0]))
            if rng.random() < 0.5:
                cover = rng.choice(COVERS)
        shot = dataclasses.replace(
            snapshot, history={k: list(v) for k, v in history.items()},
            images={**images, **({} if cover is None else {"media.cover": cover})},
        )  # fmt: skip
        python.append((shot, now, sample))
        given.append({"snapshot": _snapshot_given(shot), "now": now,
                      "sample": None if sample is None else list(sample)})  # fmt: skip
        now += rng.choice([0.04, 0.04, 0.04, 0.12, 0.16, 0.2, 0.5, 1.0, 0.0, -0.08])
        if rng.random() < 0.15:  # (at a step of the light itself)
            now = field.stepped(now) + rng.choice([0.0, 1 / field.RATE])
    return python, given


def _python_lit(folder: Path, steps: list[Any], canvas: Canvas | None,
                native: bool | None = None,
                continuous: bool = True) -> list[dict[str, Any]] | None:  # fmt: skip
    """A panel's moving frames over its light, one after another by one renderer as the panels'
    loop draws them, each at its time, a new reading told where one came; None for a theme
    Python does not read. `native`: as a panel's engine gets them (as their layers where it
    takes them all, drawing the light up itself or not), and whether each went so.
    `continuous`: the frames stream to the panel (the light drifts and eases), else a panel
    sent a picture of all of it at each change (the light stands)."""
    from libre_panel.render.scene import SceneFrame

    try:
        theme = load_theme(folder)
    except ThemeError:
        return None
    renderer = Renderer(theme, animate=True)
    renderer.fps = 25
    renderer.continuous = continuous
    renderer.canvas = canvas
    if native is not None:
        renderer.native_lights = native
    out: list[dict[str, Any]] = []
    for snapshot, now, sample in steps:
        if sample is not None:
            renderer.new_sample(*sample)
        try:
            frame, boxes = renderer.render(snapshot, now=now, scene=native is not None)
            light = renderer._light
            out.append({"frame": np.asarray(frame), "boxes": boxes, "moving": renderer.moving,
                        "due": renderer.due, "layered": isinstance(frame, SceneFrame),
                        "pools": None if light is None else light.key[5]})  # fmt: skip
        except Exception as error:  # noqa: BLE001 (a frame Python cannot make)
            out.append({"failed": f"{type(error).__name__}: {error}"})
    return out


def _canvas(rng: random.Random, size: tuple[int, int]) -> Canvas | None:
    """Where a panel of `size` stands among others (millimetres), or None: by itself."""
    if rng.random() < 0.3:
        return None
    per = rng.uniform(0.06, 0.2)
    own = (rng.uniform(-200, 200), rng.uniform(-50, 50), size[0] * per, size[1] * per)
    whole = (own[0] - rng.uniform(0, 300), own[1] - rng.uniform(0, 40),
             own[2] + rng.uniform(0, 600), own[3] + rng.uniform(0, 80))  # fmt: skip
    return Canvas(own, whole)


def _frames_case(
    folder: Path, given: list[Any], canvas: Canvas | None, continuous: bool = True
) -> dict[str, Any]:
    return {
        "folder": str(folder),
        "dither": False,
        "animate": True,
        "preview": False,
        "fps": 25,
        "continuous": continuous,
        "steps": given,
        "light_canvas": None if canvas is None else [list(canvas.own), list(canvas.whole)],
    }


def test_frames_over_the_light_as_python_lays_them(tmp_path: Path) -> None:
    # (themes of cards and texts over a light field, drifting or not, following the load or
    # not: each frame to the bit, its boxes, whether it moves on and when it is due again (the
    # light's next step); where the panel stands among others or by itself; the light stepping
    # while the cards stand, the cards changing while the light stands, both; on a panel the
    # frames stream to, and on one sent whole pictures, where the light stands)
    rng = random.Random(0x11647)
    cases, expected = [], []
    for n in range(40):
        folder = tmp_path / f"t{n}"
        data = _frame_theme(rng, folder, _card_or_text)
        _lit(folder, rng)
        size = (data["display"]["width"], data["display"]["height"])
        python, given = _lit_steps(rng, _frame_snapshot(rng)[1])
        canvas, streams = _canvas(rng, size), rng.random() < 0.75
        cases.append(_frames_case(folder, given, canvas, streams))
        expected.append(_python_lit(folder, python, canvas, continuous=streams))
    drawn, _ = _compare_moving(cases, expected)
    made = [w for want in expected if want for w in want if "frame" in w]
    due = sum(w["due"] is not None for w in made)  # (the light steps on: a frame is due)
    lit = len({hashlib.sha256(w["frame"].tobytes()).digest() for w in made})
    assert drawn > 300 and due > 100 and lit > 150, (drawn, due, lit)
    # (lights in a cover's colours, some of them on their way from one cover's to the next)
    looks = list(field.LOOKS.values())
    tinted = [w["pools"] for w in made if w["pools"] is not None and w["pools"] not in looks]
    found = [cover.colours(c) for c in COVERS if c is not None]
    fixed = [field.tinted(look, colours) for look in looks for colours in found if colours]
    gliding = [p for p in tinted if p not in fixed]
    assert len(tinted) > 40 and len(gliding) > 10, (len(tinted), len(gliding))


def test_lumen_over_the_light_on_both_panels_as_python_lays_it(tmp_path: Path) -> None:
    # (Lumen on the 12.3" and its companion on end beside it, as the person's two panels stand,
    # each its part of one light; the demo's readings, the loads busy, idle or rising: within a
    # level or two where a lit ring, its light made in floating point to a level, lies over it)
    import shutil

    from libre_panel.theme.model import find_theme

    rng = random.Random(0x1A3E2)
    whole = (0.0, 0.0, 357.21, 226.72)  # (the 12.3" across, the 9.2" on end 8 mm to its right)
    places = {
        "lumen-1920x720": Canvas((0.0, 58.5, 292.53, 109.70), whole),
        "lumen-companion-vertical": Canvas((300.53, 0.0, 56.68, 226.72), whole),
    }
    cases, expected = [], []
    for n, (name, canvas) in enumerate(places.items()):
        folder = shutil.copytree(find_theme(name), tmp_path / f"{name}-{n}")
        _lit(folder, rng)
        python, given = _lit_steps(rng, _demo())
        cases.append({**_frames_case(folder, given, canvas), "bytes": True})
        expected.append(_python_lit(folder, python, canvas))
    drawn, _ = _compare_moving(cases, expected, level=True, within=2)
    assert drawn >= 12, drawn


def test_frames_of_layers_over_the_light_as_python_hands_them_out(tmp_path: Path) -> None:
    # (the same frames as a panel's engine gets them: as their layers over the light where the
    # engine draws the light up and takes every piece as it is, else composed here; each, made
    # into its picture as the engine composes it, to the bit, and gone the same way as Python's)
    rng = random.Random(0x5CE7E)
    cases, expected = [], []
    for n in range(30):
        folder = tmp_path / f"t{n}"
        data = _frame_theme(rng, folder, _card_or_text)
        _lit(folder, rng)
        size = (data["display"]["width"], data["display"]["height"])
        python, given = _lit_steps(rng, _frame_snapshot(rng)[1])
        canvas, native = _canvas(rng, size), rng.random() < 0.7
        streams = rng.random() < 0.75
        cases.append({**_frames_case(folder, given, canvas, streams), "scene": True,
                      "native_lights": native})  # fmt: skip
        expected.append(_python_lit(folder, python, canvas, native, streams))
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR)}])
    ways = {True: 0, False: 0}
    for n, (got, want) in enumerate(zip(ours["frames"], expected, strict=True)):
        if want is None:  # (a theme Python does not read: nor does Rust)
            assert "error" in got, n
            continue
        for i, (g, w) in enumerate(zip(got["steps"], want, strict=True)):
            assert "unported" not in g and "failed" not in w, (n, i, g, w)
            assert g["sha256"] == hashlib.sha256(w["frame"].tobytes()).hexdigest(), (n, i)
            assert (g["boxes"], g["due"], g["layered"]) == (w["boxes"], w["due"], w["layered"])
            assert not g["layered"] or cases[n]["native_lights"], (n, i)
            ways[g["layered"]] += 1
    assert ways[True] > 100 and ways[False] > 50, ways

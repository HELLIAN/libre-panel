"""rust/render's transitions against Python's (docs/RUST.md): overlays.transition for each
built-in name, with the parameters a service passes, the new theme's colours and language, and
the frames each draws from an old screen to the new one, the same to the bit; where Python's
raises, Rust's fails too. The cascade also on the built-in themes as their renderer drew them
last (their ground and their cards)."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import random
import shutil
import struct
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from rust_check import image, picture, run

from libre_panel import fonts, i18n
from libre_panel.plugins.render import RenderContext
from libre_panel.render import overlays
from libre_panel.render.renderer import Renderer
from libre_panel.sensors.base import Snapshot
from libre_panel.theme.model import find_theme, load_theme

THEME_FORMAT = "libre-panel-theme/1"
NAMES = ["fade", "slide", "tube", "entrance", "wipe", "iris", "blinds", "dissolve", "slide-right",
         "slide-up", "slide-down", "zoom", "light", "glitch", "tiles", "shatter", "cascade",
         "comet", "portal"]  # fmt: skip
# the ones that go over all the panels as one where told where they stand (render/canvas.py)
ACROSS = ["slide", "slide-right", "slide-up", "slide-down", "wipe", "light", "iris", "shatter",
          "cascade", "comet", "portal"]  # fmt: skip
OTHERS = ["cut", "", "nope", "Fade"]  # (none for these)
SIZES = [(1920, 480), (480, 1920), (800, 480), (480, 480), (1280, 720), (320, 240), (240, 320),
         (1920, 462), (180, 640), (64, 36), (16, 9), (3, 2), (1, 1), (1000, 31)]  # fmt: skip
# what a palette may say of the colours transitions draw with (void, accent, text, muted; a
# theme with a colour Pillow cannot read does not load)
COLOURS = ["#000000", "#2ee6d6", "#ff0000", "#12345678", "red", "rgb(1,2,3)", "#abc",
           "hsl(120, 50%, 50%)", "#FFFFFF", "lightgoldenrodyellow"]  # fmt: skip
ROLES = ["void", "accent", "primary", "text", "tx_1", "muted", "text3", "dim"]
LANGUAGES = ["en", "de", "fr", "ru", "ja", "zh", "pl"]
# the pictures a service may have published, and what an entrance names as its logo
PICTURES = {"media.cover": {"size": [120, 90], "mode": "RGB", "seed": 7},
            "app.icon": {"size": [48, 48], "mode": "RGBA", "seed": 11},
            "game.logo": {"size": [900, 120], "mode": "RGBA", "seed": 13},
            "tall.logo": {"size": [20, 400], "mode": "L", "seed": 5}}  # fmt: skip
LOGOS = [None, "media.cover", "app.icon", "game.logo", "tall.logo", "not.there", "", 7, ["x"]]
TITLES = [None, "", "Cyberpunk 2077", "Straße", "Dvořák · Symphony No. 9", "ゲーム", "Ünïcödé ß",
          "A" * 80, 0, 1, 2.5, True, ["a", 1]]  # fmt: skip
KINDS = [None, "start", "end", "media", "media-end", "idle", "idle-end", "other", "", 1, ["start"]]


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


def _float(bits: str) -> float:
    return struct.unpack(">d", bytes.fromhex(bits))[0]


def _noise(spec: dict[str, Any]) -> Image.Image:
    """A frame of noise as lp-check makes it: each byte the top 8 of the low 32 bits of its
    index times 2654435761 plus the seed."""
    (w, h), mode, seed = spec["size"], spec["mode"], spec["seed"]
    index = np.arange(w * h * len(mode), dtype=np.uint64)
    made = (index * np.uint64(2654435761) + np.uint64(seed)) & np.uint64(0xFFFFFFFF)
    return Image.frombytes(mode, (w, h), (made >> np.uint64(24)).astype(np.uint8).tobytes())


def _sha(im: Image.Image) -> str:
    return hashlib.sha256(im.tobytes()).hexdigest()


def _frame(size: tuple[int, int], mode: str, seed: int) -> dict[str, Any]:
    """A frame of noise, `seed` spread over 32 bits: frames of seeds a few apart would be the
    same picture (their sums differ in low bits only, and the noise is the top byte)."""
    return {"size": list(size), "mode": mode, "seed": (seed * 0x9E3779B9) & 0xFFFFFFFF}


# The moment a built-in theme is drawn at before its cascade: one of this century (Windows'
# local time knows nothing before 1970, where Python's clocks fail and Rust's frame is refused).
DRAWN_ON = datetime(2025, 11, 13, 14, 26, 40)


def _python(folder: Path, language: str, switches: list[dict[str, Any]], drawn: str | None = None,
            before: tuple[Path, str] | None = None, demo: bool = False) -> list[Any]:  # fmt: skip
    """Each switch's answer from Python, as lp-check gives Rust's (``drawn``: the moment the
    renderer draws its frame of first, a double's bits; ``before``: the old screen's theme and
    the moment it was drawn at, which the morph takes apart; ``demo``: both drawn with the
    demo's readings)."""

    def seen() -> Snapshot:  # (its clocks at DRAWN_ON, as lp-check's snapshot is told)
        pictures = {k: _noise(v) for k, v in PICTURES.items()}
        if demo:
            from test_rust_render import _demo

            return dataclasses.replace(_demo(), images={**_demo().images, **pictures})
        return Snapshot(images=pictures, now=DRAWN_ON)

    i18n.set_language(language)
    try:
        renderer = Renderer(load_theme(folder), preview=True)
        renderer.snapshot = Snapshot(images={k: _noise(v) for k, v in PICTURES.items()})
        if drawn is not None:
            renderer.render(seen(), now=_float(drawn))
        context = RenderContext(renderer)
        if before is not None:
            was = Renderer(load_theme(before[0]), preview=True)
            was.render(seen(), now=_float(before[1]))
            context.before = was.parting()  # type: ignore[attr-defined]
            was.close()
        olds: dict[str, Image.Image] = {}  # (the same spec, the same frame: its id stays)
        answers: list[Any] = []
        for switch in switches:
            made = overlays.transition((switch["name"], switch["raw"]), context)
            if made is None:
                answers.append({"made": False})
                continue
            steps = []
            for old_spec, new_spec, t in switch["steps"]:
                old = olds.setdefault(json.dumps(old_spec), _noise(old_spec))
                try:
                    out = made.frame(old, _noise(new_spec), _float(t))
                    steps.append({"size": list(out.size), "mode": out.mode, "sha256": _sha(out)})
                    if demo:  # (the glow of rings and bars: drawn by each within a level)
                        steps[-1]["picture"] = out
                except Exception:
                    steps.append({"error": True})
            answers.append({
                "made": True, "name": made.name, "duration": _bits(made.duration),
                "switch": made.switch,
                "on_switch": [made.on_switch("x", None, False), made.on_switch("x", None, True)],
                "toasts": made.toasts, "from_shown": made.from_shown, "steps": steps,
            })  # fmt: skip
        return answers
    finally:
        i18n.set_language("en")


def _theme(folder: Path, size: tuple[int, int], palette: dict[str, str]) -> None:
    folder.mkdir()
    display = {"width": max(16, size[0]), "height": max(16, size[1])}  # (the frames' own sizes)
    theme = {"format": THEME_FORMAT, "name": "transitions", "palette": palette, "widgets": [],
             "display": display}  # fmt: skip
    (folder / "theme.json").write_text(json.dumps(theme), encoding="utf-8")


def _canvas(rng: random.Random) -> dict[str, Any]:
    """Where a panel and all of them stand (render/canvas.py), as the app tells a transition:
    mostly two panels side by side or one above the other, this one either of them, now and
    then any rectangle in any other, or one that says nothing sensible."""
    w, h, gap = rng.choice([(218.0, 55.0), (55.0, 218.0), (292.0, 110.0), (60.0, 60.0)]) + (
        rng.choice([0.0, 4.0, 22.5]),
    )
    side = rng.random() < 0.5
    whole = [0.0, 0.0, 2 * w + gap, h] if side else [0.0, 0.0, w, 2 * h + gap]
    second = rng.random() < 0.5
    own = [(w + gap) * second, 0.0, w, h] if side else [0.0, (h + gap) * second, w, h]
    if rng.random() < 0.25:
        whole = [rng.uniform(-50, 50) for _ in "xy"] + [rng.uniform(1, 600) for _ in "wh"]
        own = [rng.uniform(-50, 600) for _ in "xy"] + [rng.uniform(1, 300) for _ in "wh"]
    if rng.random() < 0.2:
        own = [round(v) for v in own]  # (ints, as a hand-written config may give them)
    if rng.random() < 0.1:
        own = rng.choice([[0, 0, 0, 55], [True, 0, 10, 10], [0, 0, 10], [10**400, 0, 1, 1],
                          [0.0, 0.0, float("inf"), 1.0], [0.0, "a", 1.0, 1.0]])  # fmt: skip
    return {"own": own, "whole": whole}


def _params(rng: random.Random, name: str) -> dict[str, Any] | None:
    if name != "entrance":
        return rng.choice([None, {}, {"title": "ignored", "kind": "start"},
                           {"canvas": _canvas(rng)}])  # fmt: skip
    params: dict[str, Any] = {}
    for key, values in (("title", TITLES), ("text", TITLES), ("logo", LOGOS), ("kind", KINDS)):
        if rng.random() < 0.6:
            params[key] = rng.choice(values)
    return params


def _times(rng: random.Random) -> list[float]:
    """How far a transition has gone at each step: mostly on, now and then back or at its end."""
    kind = rng.random()
    if kind < 0.6:
        times = sorted(rng.random() for _ in range(rng.randrange(1, 9)))
    elif kind < 0.8:
        times = [rng.choice([0.0, 0.25, 0.5, 0.999, 1.0, 1.5]) for _ in range(rng.randrange(1, 5))]
    else:
        times = [rng.uniform(-0.1, 1.1) for _ in range(rng.randrange(1, 6))]
    return times


def _compare(tmp_path: Path, seed: int, count: int, scenarios: Any = ()) -> int:
    """`count` themes with switches drawn at random, then `scenarios`' own, in Rust and in
    Python: every answer alike. How many frames were compared."""
    rng = random.Random(seed)
    cases = []
    for _ in range(count):
        size = rng.choice(SIZES)
        palette = {role: rng.choice(COLOURS) for role in ROLES if rng.random() < 0.4}
        language = rng.choice(LANGUAGES)
        modes = rng.choice([("RGB", "RGB")] * 6 + [("RGBA", "RGBA"), ("L", "L"), ("RGB", "RGBA")])
        switches = []
        for _ in range(rng.randrange(1, 4)):
            name = rng.choice(NAMES * 3 + OTHERS)
            old = rng.randrange(1000)
            steps = []
            for t in _times(rng):
                if rng.random() < 0.08:  # (another old frame: a shatter breaks it anew)
                    old = rng.randrange(1000)
                new = _frame(size, modes[1], rng.randrange(1000))
                steps.append([_frame(size, modes[0], old), new, _bits(t)])
            switches.append({"name": name, "raw": _params(rng, name), "steps": steps})
        cases.append((size, palette, language, switches))
    for scenario in scenarios:
        cases.append(scenario())
    wired = []
    for n, (size, palette, language, switches, *drawn) in enumerate(cases):
        folder = tmp_path / f"t{n}"
        if drawn and isinstance(drawn[0]["theme"], dict):  # (a theme of its own, drawn first)
            folder.mkdir()
            (folder / "theme.json").write_text(json.dumps(drawn[0]["theme"]), encoding="utf-8")
        elif drawn:  # (a built-in theme, drawn at a moment first)
            shutil.copytree(find_theme(drawn[0]["theme"]), folder)
        else:
            _theme(folder, size, palette)
        wire = [{"name": s["name"], "steps": s["steps"],
                 "params": {k: _typed(v) for k, v in (s["raw"] or {}).items()}}
                for s in switches]  # fmt: skip
        wired.append({"op": "transition", "theme": str(folder), "fonts_dir": str(fonts.FONT_DIR),
                      "locale": str(i18n.LOCALE_DIR), "language": language,
                      "images": {k: image(_noise(v)) for k, v in PICTURES.items()},
                      "switches": wire})  # fmt: skip
        if drawn and drawn[0].get("demo"):  # (the demo's readings, pictures and moment)
            from test_rust_render import _demo, _snapshot_given

            wired[-1]["drawn"] = drawn[0]["at"]
            wired[-1]["snapshot"] = _snapshot_given(_demo())
            wired[-1]["pictures"] = True
        elif drawn:
            wired[-1]["drawn"] = drawn[0]["at"]
            wired[-1]["now"] = [DRAWN_ON.year, DRAWN_ON.month, DRAWN_ON.day, DRAWN_ON.hour,
                                DRAWN_ON.minute, DRAWN_ON.second, 0, 0]  # fmt: skip
        if drawn and "before" in drawn[0]:  # (the old screen, which the morph takes apart)
            old = drawn[0]["before"]
            wired[-1]["before"] = {"theme": str(find_theme(old["theme"])), "drawn": old["at"]}
    answers = run(wired)
    frames = 0
    for n, (got, (_, _, language, switches, *drawn)) in enumerate(zip(answers, cases, strict=True)):
        old = drawn[0].get("before") if drawn else None
        before = (find_theme(old["theme"]), old["at"]) if old else None
        want = _python(tmp_path / f"t{n}", language, switches, drawn[0]["at"] if drawn else None,
                       before, bool(drawn and drawn[0].get("demo")))  # fmt: skip
        assert len(got["switches"]) == len(want), n
        for k, (mine, theirs) in enumerate(zip(got["switches"], want, strict=True)):
            if theirs.get("made"):
                for j, (a, b) in enumerate(zip(mine["steps"], theirs["steps"], strict=True)):
                    if "picture" in b:  # (in the glow of rings and bars a level or two apart)
                        got, want = picture(a.pop("image")), b.pop("picture")
                        apart = np.abs(np.asarray(got, np.int16) - np.asarray(want, np.int16))
                        assert got.size == want.size and apart.max() <= 2, (n, k, j, apart.max())
                        a.pop("sha256"), b.pop("sha256")
                    assert a == b, (n, k, switches[k]["name"], switches[k]["raw"], j, a, b)
                    frames += "sha256" in b or "size" in b
            assert mine == theirs, (n, k, switches[k]["name"], mine, theirs)
    return frames


# -- scenarios: what the random switches seldom meet, every time ----------------------------


def _entrance_at_its_turns() -> tuple[Any, ...]:
    """Each kind of entrance drawn at the turns of its timeline (seconds of 3.2) and just
    either side: the old frame closing (its edges in the frame while their light grows), the
    band opening with the logo or the title, the text typing in, the band closing and the new
    screen opening."""
    size = (1920, 480)
    turns = [0.0, 0.1, 0.2, 0.6, 0.8, 0.9, 1.25, 1.3, 1.5, 1.7, 2.3, 2.5, 2.65, 2.85, 3.1, 3.2]
    times = sorted({max(0.0, s + d) / 3.2 for s in turns for d in (-0.001, 0.0, 0.001)})
    switches = []
    kinds = [{"kind": "start", "logo": "game.logo"},
             {"kind": "media", "title": "Song", "logo": "media.cover"}, {"kind": "idle"},
             {"kind": "end", "title": "Elden Ring"}, {"title": "Ünïcödé ß", "text": "straße"},
             {"kind": "media-end", "logo": "app.icon", "title": "x"},
             {"kind": "media", "logo": "app.icon",
              "title": "A track whose title goes on, and on, and on, past the band"},
             {"kind": "media", "logo": "media.cover"}]  # fmt: skip
    for k, params in enumerate(kinds):
        steps = [[_frame(size, "RGB", k), _frame(size, "RGB", 50 + k), _bits(t)] for t in times]
        switches.append({"name": "entrance", "raw": params, "steps": steps})
    return size, {"accent": "#ff8800", "void": "#101010"}, "de", switches


def _entrance_on_another_size() -> tuple[Any, ...]:
    """The music's entrance drawn at one size, then at another: its picture is made anew (and
    the track joined to its text again, as Python does)."""
    one, two = (800, 480), (480, 800)
    params = {"kind": "media", "title": "Track", "logo": "media.cover"}
    steps = [[_frame(one, "RGB", 1), _frame(one, "RGB", 2), _bits(0.4)],
             [_frame(two, "RGB", 3), _frame(two, "RGB", 4), _bits(0.45)],
             [_frame(one, "RGB", 1), _frame(one, "RGB", 2), _bits(0.5)]]  # fmt: skip
    return one, {}, "en", [{"name": "entrance", "raw": params, "steps": steps}]


def _every_one_at_its_ends() -> tuple[Any, ...]:
    """Each transition at its start, its middle, just before its end and at it, on a tall
    frame (and where the line of light is almost at its brightest, on its way up and down); a
    shatter whose old frame changes on the way."""
    size = (480, 1920)
    switches = []
    for k, name in enumerate(NAMES):
        steps = [[_frame(size, "RGB", k), _frame(size, "RGB", 99), _bits(t)]
                 for t in (0.0, 1e-9, 0.125, 0.15, 0.5, 0.7, 0.83, 0.999999, 1.0)]  # fmt: skip
        switches.append({"name": name, "raw": {}, "steps": steps})
    steps = [[_frame(size, "RGB", seed), _frame(size, "RGB", 98), _bits(t)]
             for seed, t in ((1, 0.2), (1, 0.4), (2, 0.5), (1, 0.6))]  # fmt: skip
    switches.append({"name": "shatter", "raw": {}, "steps": steps})
    return size, {}, "en", switches


def _kept_for_another_size() -> tuple[Any, ...]:
    """The handovers that keep a map or a strip of light for the frame's size, drawn at one
    size, then at another, then at the first again: made anew each time it changes."""
    one, two = (800, 480), (480, 800)
    switches = []
    for k, name in enumerate(["wipe", "iris", "blinds", "dissolve", "light"]):
        steps = [[_frame(size, "RGB", k), _frame(size, "RGB", 40 + k), _bits(t)]
                 for size, t in ((one, 0.3), (two, 0.5), (one, 0.7))]  # fmt: skip
        switches.append({"name": name, "raw": {}, "steps": steps})
    return one, {"accent": "#ff00ff"}, "en", switches


def _entrance_on_a_narrow_panel() -> tuple[Any, ...]:
    """An entrance on a panel narrower than its band would be: a wide logo and a long title
    made to fit the room it leaves at the sides."""
    size = (480, 800)
    switches = []
    kinds = [{"kind": "start", "logo": "game.logo"},
             {"title": "A title too long for the band of a narrow panel"},
             {"kind": "media", "logo": "tall.logo", "title": "Tall"}]  # fmt: skip
    for k, params in enumerate(kinds):
        steps = [[_frame(size, "RGB", 7 + k), _frame(size, "RGB", 70 + k), _bits(s / 3.2)]
                 for s in (1.0, 1.4, 1.8, 2.4)]  # fmt: skip
        switches.append({"name": "entrance", "raw": params, "steps": steps})
    return size, {}, "en", switches


def _the_line_almost_at_its_brightest() -> tuple[Any, ...]:
    """The line of light on a wide frame, its core in it, while its light is almost all there
    (from 0.99 to 0.999 of it: dimmed all the same), on its way up and on its way down."""
    size = (1920, 480)
    steps = [[_frame(size, "RGB", 3), _frame(size, "RGB", 4), _bits(t)]
             for t in (0.12, 0.13, 0.825, 0.835)]  # fmt: skip
    return size, {}, "en", [{"name": "light", "raw": {}, "steps": steps}]


def _across_the_panels() -> tuple[Any, ...]:
    """Slides, the wipe and the line of light told where the panels stand (render/canvas.py),
    on either of two panels side by side with a gap between them and of two one above the
    other, at the times their seams and the line are at the panels' edges, in the gap and
    beyond them; one the canvas does not touch (it keeps its own pace); canvases that say
    nothing sensible, on which each plays on its panel alone; the comet over a sliver of a panel
    and the shatter on one lower than a tile; and the wipe's map made anew for another size."""
    size = (240, 60)
    times = (-0.1, 0.0, 0.05, 0.3, 0.45, 0.5, 0.55, 0.7, 0.95, 0.999, 1.0)
    side = {"whole": [0.0, 0.0, 458.0, 55.0]}
    above = {"whole": [0.0, 0.0, 218.0, 132.0]}
    canvases = [{**side, "own": [0.0, 0.0, 218.0, 55.0]}, {**side, "own": [240, 0, 218, 55]},
                {**above, "own": [0.0, 0.0, 218.0, 55.0]},
                {**above, "own": [0.0, 77.0, 218.0, 55.0]}]  # fmt: skip
    inf, nan = float("inf"), float("nan")
    wrong = [{"own": [0, 0, 218, 55]}, {"own": [0, 0, -1, 55], **side}, {"own": None, **side},
             {"own": [False, 0, 218, 55], **side},
             {"own": [0, 0, 218, 55], "whole": "x"},
             # (a width or a height of 0, here or over all; one past every number)
             {"own": [0, 0, 0, 55], **side}, {"own": [0, 0, 218, 0], **side},
             {"own": [0, 0, 218, 55], "whole": [0.0, 0.0, 0.0, 55.0]},
             {"own": [0.0, 0.0, inf, 55.0], **side}, {"own": [inf, 0.0, 218.0, 55.0], **side},
             {"own": [0.0, 0.0, 218.0, 55.0], "whole": [0.0, 0.0, 458.0, inf]},
             {"own": [nan, 0.0, 218.0, 55.0], **side}]  # fmt: skip
    switches = []
    for k, name in enumerate(ACROSS):
        for j, canvas in enumerate(canvases + wrong):
            at = times if j < len(canvases) else (0.3, 0.55)  # (each wrong one plays alone)
            steps = [[_frame(size, "RGB", k), _frame(size, "RGB", 10 + j), _bits(t)] for t in at]
            switches.append({"name": name, "raw": {"canvas": canvas}, "steps": steps})
    once = [[_frame(size, "RGB", 1), _frame(size, "RGB", 2), _bits(0.4)]]
    switches.append({"name": "blinds", "raw": {"canvas": canvases[1]}, "steps": once})
    # (a panel a hundredth of a millimetre wide: the comet would be larger than any panel, and
    # flies without its light)
    thin = {"own": [5.0, 5.0, 0.01, 900.0], "whole": [0.0, 0.0, 20.0, 20.0]}
    steps = [[_frame(size, "RGB", 5), _frame(size, "RGB", 6), _bits(t)] for t in (0.2, 0.6)]
    switches.append({"name": "comet", "raw": {"canvas": thin}, "steps": steps})
    # (a panel lower than a tile and narrower than four: the shatter breaks it into two rows and
    # four columns at the fewest; at 0.21606493800981494 its first tile is 200.5 / 255 seen, to
    # the last bit, and its opaque pixels' 200.5 rounds to the even 200, as Python rounds it)
    low = {"own": [0.0, 0.0, 40.0, 20.0], "whole": [0.0, 0.0, 458.0, 20.0]}
    fallen = [[_frame(size, "RGB", 5), _frame(size, "RGB", 6), _bits(t)]
              for t in (0.2, 0.21606493800981494, 0.6)]  # fmt: skip
    switches.append({"name": "shatter", "raw": {"canvas": low}, "steps": fallen})
    switches.append({"name": "wipe", "raw": {"canvas": canvases[1]},
                     "steps": [[_frame(s, "RGB", 3), _frame(s, "RGB", 4), _bits(0.5)]
                               for s in (size, (60, 240), size)]})  # fmt: skip
    return size, {"info": "#00ccff"}, "en", switches


def _cards_coming_in(theme: str, size: tuple[int, int], seed: int) -> Any:
    """The cascade (and the others that go over all the panels) on a built-in theme as its
    renderer drew it last: its ground and the cards on it (some under a picture all over,
    modules, a portrait panel), by themselves and on either of two panels side by side or one
    above the other; then on frames of another size, where it knows no ground of its own."""

    def scenario() -> tuple[Any, ...]:
        times = (0.0, 0.1, 0.22, 0.3, 0.42, 0.55, 0.7, 0.85, 0.93, 0.999)
        w, h = size
        mm = (w * 0.1527, h * 0.1527)  # (the 12.3"'s pixels a millimetre)
        side = {"whole": [0.0, 0.0, 2 * mm[0] + 15.0, mm[1]]}
        above = {"whole": [0.0, 0.0, mm[0], 2 * mm[1] + 15.0]}
        canvases = [None, {**side, "own": [0.0, 0.0, *mm]},
                    {**side, "own": [mm[0] + 15.0, 0.0, *mm]},
                    {**above, "own": [0.0, mm[1] + 15.0, *mm]}]  # fmt: skip
        switches = []
        for j, canvas in enumerate(canvases):
            for k, name in enumerate(["cascade"] * 2 + ["comet", "portal", "shatter", "iris"]):
                at = times if k < 2 else times[3::3]
                steps = [[_frame(size, "RGB", seed + k), _frame(size, "RGB", seed + 10 + j),
                          _bits(t)] for t in at]  # fmt: skip
                raw = {} if canvas is None else {"canvas": canvas}
                switches.append({"name": name, "raw": raw, "steps": steps})
        other = (size[0] // 2, size[1] // 2)
        steps = [[_frame(other, "RGB", seed), _frame(other, "RGB", seed + 1), _bits(t)]
                 for t in (0.2, 0.5, 0.9)]  # fmt: skip
        switches.append({"name": "cascade", "raw": {}, "steps": steps})
        at = _bits(1_763_000_000.0 + seed)
        return size, {}, "en", switches, {"theme": theme, "at": at}

    return scenario


def _cards_with_the_demo(theme: str, size: tuple[int, int], seed: int) -> Any:
    """The cascade on a built-in theme drawn with the demo's readings, alone and on the second of
    two panels side by side: Encore's pair on a ground of two pieces that cover all of the panel
    (the cover's blurred light and the veil over it); Encore's square, whose cover light and veil
    lie above a smaller piece (the cards' ground then, no cards themselves); Contrast, whose
    lights reach beyond their widgets (laid with them, no cards themselves)."""

    def scenario() -> tuple[Any, ...]:
        mm = (size[0] * 0.1527, size[1] * 0.1527)
        side = {"whole": [0.0, 0.0, 2 * mm[0] + 15.0, mm[1]], "own": [mm[0] + 15.0, 0.0, *mm]}
        switches = []
        for j, canvas in enumerate([None, side]):
            steps = [[_frame(size, "RGB", seed), _frame(size, "RGB", seed + 1 + j), _bits(t)]
                     for t in (0.0, 0.22, 0.55, 0.93)]  # fmt: skip
            raw = {} if canvas is None else {"canvas": canvas}
            switches.append({"name": "cascade", "raw": raw, "steps": steps})
        at = _bits(1_763_000_000.0 + seed)
        return size, {}, "en", switches, {"theme": theme, "at": at, "demo": True}

    return scenario


def _a_veil_over_a_card() -> tuple[Any, ...]:
    """The cascade on a theme of its own: a card, and over it a veil that covers all of the
    panel (a widget whose box is the whole panel, above a smaller one: no card itself, nor the
    ground), alone and on the second of two panels side by side."""
    size = (320, 160)
    widgets = [{"type": "rect", "id": "card", "x": 40, "y": 30, "w": 90, "h": 50,
                "color": "#3A7BD5", "radius": 8},
               {"type": "rect", "id": "veil", "x": 0, "y": 0, "w": 320, "h": 160,
                "color": "#10203080"}]  # fmt: skip
    theme = {"format": THEME_FORMAT, "name": "veiled", "palette": {"bg": "#0B0F14"},
             "widgets": widgets, "display": {"width": size[0], "height": size[1]}}  # fmt: skip
    mm = (size[0] * 0.1527, size[1] * 0.1527)
    side = {"whole": [0.0, 0.0, 2 * mm[0] + 15.0, mm[1]], "own": [mm[0] + 15.0, 0.0, *mm]}
    switches = []
    for j, canvas in enumerate([None, side]):
        steps = [[_frame(size, "RGB", 25), _frame(size, "RGB", 26 + j), _bits(t)]
                 for t in (0.0, 0.22, 0.4, 0.55, 0.93)]  # fmt: skip
        switches.append({"name": "cascade", "raw": {} if canvas is None else {"canvas": canvas},
                         "steps": steps})  # fmt: skip
    return size, {}, "en", switches, {"theme": theme, "at": _bits(1_763_000_025.0)}


def _morphing(old: str, new: str, size: tuple[int, int], seed: int, demo: bool = True) -> Any:
    """The morph from a built-in theme to another as their renderers drew them, with the demo's
    readings or with none (the time, the rings, the numbers and the cover both show fly, their
    lights with them; the rest fades): at the turns of its timeline; and on frames of another
    size, where it has nothing to take apart and fades."""

    def scenario() -> tuple[Any, ...]:
        times = (0.0, 0.03, 0.06, 0.2, 0.35, 0.42, 0.5, 0.58, 0.65, 0.8, 0.9, 0.95, 0.999, 1.0)
        steps = [[_frame(size, "RGB", seed), _frame(size, "RGB", seed + 1), _bits(t)]
                 for t in times]  # fmt: skip
        other = (size[0] // 2, size[1] // 3)
        apart = [[_frame(other, "RGB", seed), _frame(other, "RGB", seed + 2), _bits(t)]
                 for t in (0.3, 0.7)]  # fmt: skip
        switches = [{"name": "morph", "raw": {}, "steps": steps},
                    {"name": "morph", "raw": {}, "steps": apart}]  # fmt: skip
        at = _bits(1_763_000_000.0 + seed)
        was = {"theme": old, "at": _bits(1_762_999_990.0 + seed)}
        return size, {}, "en", switches, {"theme": new, "at": at, "before": was, "demo": demo}

    return scenario


SCENARIOS = [_entrance_at_its_turns, _entrance_on_another_size, _every_one_at_its_ends,
             _kept_for_another_size, _entrance_on_a_narrow_panel,
             _the_line_almost_at_its_brightest, _across_the_panels,
             _cards_coming_in("lumen-1920x720", (1920, 720), 1),
             _cards_coming_in("encore-pair", (1920, 480), 2),
             _cards_coming_in("arena-480x480", (480, 480), 3),
             _cards_coming_in("terra-vertical", (480, 1920), 4),
             _cards_with_the_demo("encore-pair", (1920, 480), 16),
             _cards_with_the_demo("encore-480x480", (480, 480), 19),
             _cards_with_the_demo("contrast-1920x720", (1920, 720), 22),
             _a_veil_over_a_card,
             _morphing("lumen-1920x720", "arena-1920x720", (1920, 720), 5),
             _morphing("arena-1920x720", "lumen-1920x720", (1920, 720), 6),
             _morphing("encore-1920x720", "lumen-1920x720", (1920, 720), 7),
             _morphing("lumen-pair", "arena-pair", (1920, 480), 8),
             _morphing("arena-vertical", "lumen-vertical", (480, 1920), 9),
             _morphing("lumen-480x480", "arena-480x480", (480, 480), 10),
             _morphing("lumen-1920x720", "arena-1920x720", (1920, 720), 11, False)]  # fmt: skip


def test_transitions_as_python_draws_them(tmp_path: Path) -> None:
    frames = _compare(tmp_path, 0x7AA5, 120, scenarios=SCENARIOS)
    assert frames > 900, frames

"""The morph (render/handovers.py's Morph, render/morph.py): what both screens show flies from
its old place to its new one; what is the same thing on two screens; how a frame is taken apart;
and how the app hands the morph the screen it leaves."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from libre_panel import app
from libre_panel.plugins.render import RenderContext
from libre_panel.render import morph as M
from libre_panel.render.handovers import Morph
from libre_panel.render.light import smooth
from libre_panel.render.overlays import ELABORATE_TRANSITIONS, transition
from libre_panel.render.renderer import Piece, Renderer
from libre_panel.sensors.demo import demo_snapshot
from libre_panel.theme.model import find_theme, load_theme

WHEN = datetime(2026, 10, 9, 10, 8, 24).timestamp()


def drawn(name: str) -> Renderer:
    renderer = Renderer(load_theme(find_theme(name)), preview=True)
    renderer.render(demo_snapshot(fixed_time=1_760_000_000.0), now=WHEN)
    return renderer


def morph(old: str, new: str) -> tuple[Morph, Image.Image]:
    was = drawn(old)
    context = RenderContext(drawn(new))
    context.before = was.parting()  # type: ignore[attr-defined]
    was.close()
    made = transition(("morph", {}), context)
    assert isinstance(made, Morph)
    return made, Image.new("RGB", (context.width, context.height), (9, 9, 9))


@pytest.mark.parametrize(
    ("widget", "shows"),
    [
        ({"type": "clock", "format": "%H:%M"}, "time"),
        ({"type": "clock", "format": "%S"}, "time"),
        ({"type": "clock"}, "time"),
        ({"type": "clock", "format": "%A %d %B"}, "date"),
        ({"type": "analog"}, "time"),
        ({"type": "gauge", "sensor": "cpu.load"}, "ring:cpu.load"),
        ({"type": "metric", "sensor": "gpu.temp"}, "number:gpu.temp"),
        ({"type": "graph", "sensor": "cpu.load"}, "graph:cpu.load"),
        ({"type": "image", "src": "@media.cover"}, "picture:media.cover"),
        ({"type": "image", "src": "logo.png"}, None),
        ({"type": "moon"}, "moon"),
        ({"type": "countdown", "target": "2027-01-01"}, "countdown:2027-01-01"),
        ({"type": "metric", "sensor": ""}, None),
        ({"type": "text", "text": "CPU"}, None),
        ({"type": "rect"}, None),
        ({"type": ["clock"]}, None),
        ({}, None),
    ],
)
def test_a_widget_is_known_by_what_it_shows(widget, shows):
    assert M.shown_as(widget) == shows


def test_the_morph_is_offered_with_the_elaborate_transitions():
    assert "morph" in ELABORATE_TRANSITIONS


def test_what_lies_together_is_one_thing_and_what_lies_apart_none():
    ground = Image.new("RGB", (400, 200))
    dot = Image.new("RGBA", (20, 20), (255, 255, 255, 255))
    pieces = [("a", Piece(dot, 10, 10)), ("b", Piece(dot, 32, 10)),  # (side by side)
              ("c", Piece(dot, 10, 150)), ("d", Piece(dot, 370, 10)),  # (far apart)
              ("e", Piece(dot, 100, 100)), ("e\0light", Piece(dot, 110, 100))]  # fmt: skip
    shows = {"a": "time", "b": "time", "c": "ring:x", "d": "ring:x", "e": "number:y"}
    found = M.things(M.Parting(ground, pieces, shows, True))
    assert found["time"] == ([0, 1], (10, 10, 52, 30))
    assert "ring:x" not in found
    assert found["number:y"] == ([4, 5], (100, 100, 130, 120))  # (its light with it)


def test_a_frame_comes_apart_into_what_flies_and_the_rest():
    ground = Image.new("RGB", (100, 60), (10, 20, 30))
    red = Image.new("RGBA", (10, 10), (255, 0, 0, 255))
    glow = Image.new("RGB", (10, 10), (0, 40, 0))
    pieces = [("a", Piece(red, 5, 5)), ("b", Piece(red, 50, 20)),
              ("c", Piece(glow, 55, 22, add=True))]  # fmt: skip
    parting = M.Parting(ground, pieces, {"b": "time", "c": "time"}, True)
    taken = M.things(parting)
    without, parts = M.take_apart(parting, taken)
    assert without.getpixel((7, 7)) == (255, 0, 0)  # (what stays)
    assert without.getpixel((55, 25)) == (10, 20, 30)  # (what flies is gone from it)
    part = parts["time"]
    assert part.box == (50, 20, 15, 12)
    assert part.body.getpixel((5, 5)) == (255, 0, 0, 255)
    assert part.body.getpixel((12, 5))[3] == 0  # (the light is apart)
    assert part.light is not None and part.light.getpixel((12, 5)) == (0, 40, 0)


def test_a_thing_is_laid_as_large_as_fits_its_shape_kept():
    out = Image.new("RGB", (200, 100))
    body = Image.new("RGBA", (40, 20), (200, 100, 50, 255))
    M.lay(out, M.Part((0, 0, 40, 20), body, None), (100, 50), (80, 80), 1.0)
    assert out.getbbox() == (60, 30, 140, 70)  # (80 wide, so 40 high: its shape)
    faint = Image.new("RGB", (200, 100))
    M.lay(faint, M.Part((0, 0, 40, 20), body, None), (100, 50), (40, 20), 0.5)
    assert faint.getpixel((100, 50)) == (100, 50, 25)
    nothing = Image.new("RGB", (200, 100))
    M.lay(nothing, M.Part((0, 0, 40, 20), body, None), (100, 50), (40, 20), 0.0)
    assert nothing.getbbox() is None


def test_what_both_screens_show_flies_to_its_new_place():
    made, frame = morph("lumen-1920x720", "arena-1920x720")
    old, new = Image.new("RGB", frame.size, (30, 0, 0)), Image.new("RGB", frame.size, (0, 0, 30))
    made.frame(old, new, 0.0)
    flights = made._made[3]
    shared = {was.box: comes.box for was, comes in flights}
    assert len(flights) >= 5  # (the time, the rings, the numbers both show)
    assert all(a != b for a, b in shared.items())  # (each to another place)
    areas = [was.box[2] * was.box[3] for was, _ in flights]
    assert areas == sorted(areas, reverse=True)  # (the largest first, under the rest)
    assert made.frame(old, new, 1.0) is new
    middle = np.asarray(made.frame(old, new, 0.5))
    assert not np.array_equal(middle, np.asarray(new)) and middle.any()


def test_it_starts_from_the_old_screen_and_ends_on_the_new():
    made, frame = morph("arena-1920x720", "lumen-1920x720")
    old, new = Image.new("RGB", frame.size, (60, 0, 0)), Image.new("RGB", frame.size, (0, 0, 60))
    first = np.asarray(made.frame(old, new, 0.0), np.int16)
    was = np.asarray(made._made[1], np.int16)  # (the old screen without what flies)
    assert np.abs(first - was).max() > 0  # (what flies is on it, where it was)
    late = np.asarray(made.frame(old, new, 0.97), np.int16)
    assert np.abs(late - np.asarray(new, np.int16)).mean() < np.abs(first - 60).mean()


def test_without_the_old_screen_or_in_another_size_it_fades():
    context = RenderContext(drawn("arena-1920x720"))
    made = transition(("morph", {}), context)
    old, new = Image.new("RGB", (1920, 720), (200, 0, 0)), Image.new("RGB", (1920, 720), (0, 0, 9))
    want = Image.blend(old, new, smooth(0.4))
    assert made.frame(old, new, 0.4).tobytes() == want.tobytes()
    made, frame = morph("lumen-1920x720", "arena-1920x720")
    small = (Image.new("RGB", (64, 32), (200, 0, 0)), Image.new("RGB", (64, 32), (0, 0, 200)))
    assert made.frame(*small, 0.4).tobytes() == Image.blend(*small, smooth(0.4)).tobytes()


def test_the_app_hands_the_morph_the_screen_it_leaves():
    was = drawn("lumen-1920x720")
    made = Morph(RenderContext(drawn("arena-1920x720")), {})
    app._hand_before(made, was, demo_snapshot(fixed_time=1_760_000_000.0), WHEN)
    assert made._before is not None and made._before.size == (1920, 720)
    # (its last frame went to the engine as its layers: it is drawn here once for it)
    scened = Renderer(load_theme(find_theme("lumen-1920x720")), preview=True)
    scened._last = None
    other = Morph(RenderContext(drawn("arena-1920x720")), {})
    app._hand_before(other, scened, demo_snapshot(fixed_time=1_760_000_000.0), WHEN)
    assert other._before is not None
    # (another transition takes nothing of it)
    plain = SimpleNamespace(name="fade")
    app._hand_before(plain, was, None, WHEN)
    assert not hasattr(plain, "_before")

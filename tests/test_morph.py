"""The morph (render/handovers.py's Morph, render/morph.py): what both screens show flies from
its old place to its new one; what is the same thing on two screens; how a frame is taken apart;
and how the app hands the morph the screen it leaves."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from libre_panel import app, transition_show
from libre_panel.plugins.render import RenderContext
from libre_panel.render import morph as M
from libre_panel.render.handovers import HANDOVERS, Morph
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


@pytest.mark.parametrize(
    ("shown", "partner"),
    [
        ("lumen-1920x720", "arena"),
        ("lumen", "arena"),
        ("lumen-pair-1920x720", "arena-pair"),
        ("arena-vertical", "lumen"),
        ("arena-companion-1920x720", "lumen-companion"),
        ("lumen-clock-640x180", "arena"),  # (Arena has no clock of its own: Arena itself)
        ("encore-1920x720", "lumen"),
        ("", "lumen"),
    ],
)
def test_a_morph_shown_turns_lumen_into_arena_and_any_other_into_lumen(shown, partner):
    assert transition_show.morph_partner(shown) == partner


def test_a_morph_shown_turns_the_screen_into_another_theme_and_back():
    from libre_panel.config import Config
    from libre_panel.render.motion import Motion

    renderer = drawn("lumen-1920x720")
    loop = SimpleNamespace(
        snapshot=demo_snapshot(fixed_time=1_760_000_000.0),
        motion=Motion(),
        _dress=lambda config: (None, None),
        _dressed=app.MainLoop._dressed,
        _laid_out=lambda config, theme: theme,
        _look_source=lambda look: None,
        _backdrop=app.MainLoop._backdrop,
    )
    held = renderer.parting().frame.convert("RGB")
    card = transition_show.card("Morph", held.size, (0, 0, 0), (255, 255, 255))
    show = transition_show.Show(1, held, card, lambda: Morph(RenderContext(renderer), {}))
    app.MainLoop._morph_shown(loop, show, "lumen-1920x720", renderer, Config(), WHEN)
    assert show.card is not card and show.card.size == held.size  # (Arena, drawn now)
    show.out.frame(held, show.card, 0.5)
    show.back.frame(show.card, held, 0.5)
    assert len(show.out._made[3]) >= 5 and len(show.back._made[3]) >= 5  # (what both show flies)
    there = {was.box for was, _ in show.out._made[3]}
    back = {comes.box for _, comes in show.back._made[3]}
    assert there == back  # (and back to where it was)
    # (another transition, or no readings yet: the card as it was)
    plain = transition_show.Show(2, held, card, lambda: HANDOVERS["wipe"]())
    app.MainLoop._morph_shown(loop, plain, "lumen-1920x720", renderer, Config(), WHEN)
    assert plain.card is card
    loop.snapshot = None
    bare = transition_show.Show(3, held, card, lambda: Morph(RenderContext(renderer), {}))
    app.MainLoop._morph_shown(loop, bare, "lumen-1920x720", renderer, Config(), WHEN)
    assert bare.card is card


def _seen(own, old, new):
    return M.Seen(own, (100, 50), {k: ([0], box) for k, box in old.items()},
                  {k: ([0], box) for k, box in new.items()})  # fmt: skip


def test_over_all_the_panels_a_thing_stays_on_its_panel_or_flies_to_the_one_left():
    a, b = (0.0, 0.0, 100.0, 50.0), (110.0, 0.0, 100.0, 50.0)
    seen = {
        0: _seen(a, {"time": (0, 0, 40, 20), "picture:x": (0, 0, 50, 50), "ring:y": (0, 0, 9, 9)},
                 {"time": (50, 0, 90, 20)}),
        1: _seen(b, {"ring:y": (0, 0, 9, 9)},
                 {"picture:x": (10, 0, 60, 50), "ring:y": (1, 1, 9, 9), "number:z": (0, 0, 5, 5)}),
    }  # fmt: skip
    flights = M.paired(seen)
    # (the largest first: the picture 50 x 50 mm, the time 40 x 20, the ring 9 x 9)
    assert flights == [("picture:x", 0, 1), ("time", 0, 0), ("ring:y", 1, 1)]
    # (two panels left with it and one to come to: it stays where it is, with its panel)
    seen[2] = _seen((220.0, 0.0, 100.0, 50.0), {"picture:x": (0, 0, 10, 10)}, {})
    assert ("picture:x", 0, 1) not in M.paired(seen)
    assert seen[1].mm((10, 0, 60, 50)) == (120.0, 0.0, 50.0, 50.0)  # (its pixels, mm here)


def _across(pairs, rects, key):
    whole = [0.0, 0.0, max(r[0] + r[2] for r in rects), max(r[1] + r[3] for r in rects)]
    made = []
    for i, (old, new) in enumerate(pairs):
        was, now = drawn(old), drawn(new)
        context = RenderContext(now)
        context.before = was.parting()  # type: ignore[attr-defined]
        canvas = {"own": rects[i], "whole": whole, "panel": i, "panels": len(pairs), "key": key}
        frames = (was.parting().frame.convert("RGB"), now.parting().frame.convert("RGB"))
        made.append((transition(("morph", {"canvas": canvas}), context), *frames))
    return made


def test_over_all_the_panels_the_cover_flies_from_one_panel_to_the_other():
    # (music comes: Lumen's cover on the left panel is the companion's on the right one)
    rects = [[0.0, 0.0, 292.0, 109.5], [300.0, 0.0, 292.0, 109.5]]
    (left, l_old, l_new), (right, r_old, r_new) = _across(
        [("lumen-pair-1920x720", "encore-pair-1920x720"),
         ("lumen-companion-1920x720", "encore-companion-1920x720")], rects, "music")  # fmt: skip
    assert left.duration == right.duration == 2.4  # (longer over the panels)
    first = left.frame(l_old, l_new, 0.0)
    assert first.tobytes() == l_old.tobytes()  # (the other one not yet: the old screen)
    right.frame(r_old, r_new, 0.0)
    left.frame(l_old, l_new, 0.0)
    right.frame(r_old, r_new, 0.03)
    assert left._made is not None and right._made is not None
    flights = [(was is not comes and seen_was.own[0], seen_comes.own[0])
               for seen_was, was, seen_comes, comes in right._made[3]]  # fmt: skip
    assert (0.0, 300.0) in flights  # (the cover, from the left panel to the right one)
    # (on its way through the gap it shows on both: the right edge of one, the left of the other)
    mid_left = np.asarray(left.frame(l_old, l_new, 0.55), np.int16)
    mid_right = np.asarray(right.frame(r_old, r_new, 0.55), np.int16)
    assert mid_left[:, -40:].any() and mid_right[:, :40].any()
    assert right.frame(r_old, r_new, 1.0) is r_new


def test_over_all_the_panels_one_that_never_comes_is_left_out_once_they_fly():
    rects = [[0.0, 0.0, 292.0, 109.5], [300.0, 0.0, 292.0, 109.5]]
    (left, old, new), _ = _across(
        [("lumen-1920x720", "arena-1920x720"), ("lumen-companion-1920x720",
         "encore-companion-1920x720")], rects, "alone")  # fmt: skip
    waiting = left.frame(old, new, 0.03)
    assert left._made is None and waiting.size == old.size
    left.frame(old, new, 0.2)  # (the things fly: with the panels that came, this one)
    assert left._made is not None and len(left._made[3]) >= 5

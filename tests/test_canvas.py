"""One transition over all the panels as one (render/canvas.py): where each panel's part of a
slide, the wipe and the line of light lies, how the main loop tells a panel where it stands and
when all of them begin, and that nothing else changes for one panel alone."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from PIL import Image

from libre_panel import app, transition_show
from libre_panel.config import Config, TransitionsConfig
from libre_panel.render import canvas as C
from libre_panel.render.overlays import handover, transition

# a 12.3" (293 x 110 mm) and, 30 mm to its right, a 9.2" (227 x 57 mm), their middles level
BIG = [0.0, 0.0, 292.5, 109.7]
BAR = [322.5, 26.2, 226.7, 56.7]
WHOLE = [0.0, 0.0, 549.2, 109.7]
OLD, NEW = (200, 0, 0), (0, 0, 200)


def canvas(own: list[float]) -> dict[str, list[float]]:
    return {"own": list(own), "whole": list(WHOLE)}


def frames(size: tuple[int, int]) -> tuple[Image.Image, Image.Image]:
    return Image.new("RGB", size, OLD), Image.new("RGB", size, NEW)


def test_a_canvas_is_four_numbers_for_each_and_nothing_else():
    assert C.canvas_of({"canvas": canvas(BIG)}) == C.Canvas(tuple(BIG), tuple(WHOLE))
    assert C.canvas_of({"canvas": {"own": [0, 0, 3, 2], "whole": [0, 0, 5, 2]}}).own == (0, 0, 3, 2)
    for bad in (None, {}, {"canvas": None}, {"canvas": []}, {"canvas": {"own": BIG}},
                {"canvas": {"own": BIG, "whole": [0, 0, 1]}},
                {"canvas": {"own": [0, 0, True, 1], "whole": WHOLE}},
                {"canvas": {"own": [0, 0, "1", 1], "whole": WHOLE}},
                {"canvas": {"own": [0, 0, 0, 1], "whole": WHOLE}},
                {"canvas": {"own": [0, 0, 1, -1], "whole": WHOLE}},
                {"canvas": {"own": [0, 0, float("inf"), 1], "whole": WHOLE}},
                {"canvas": {"own": [0, 0, float("nan"), 1], "whole": WHOLE}},
                {"canvas": {"own": [0, 0, 10**400, 1], "whole": WHOLE}}, "canvas"):  # fmt: skip
        assert C.canvas_of(bad) is None, bad


def test_only_a_slide_the_wipe_and_the_line_of_light_go_over_the_panels():
    """Given where the panels stand, those take as long as across them all (the pace of
    Settings → Transitions stretching it); any other plays on its panel alone, as before."""
    for name, seconds in C.CANVAS_DURATIONS.items():
        kind = transition((name, {"canvas": canvas(BIG)}))
        assert kind.canvas is not None and kind.duration == seconds, name
        alone = transition(name)
        assert alone.canvas is None and alone.duration < seconds, name
    for name in ("fade", "tube", "iris", "zoom", "shatter", "tiles", "glitch"):
        kind = transition((name, {"canvas": canvas(BIG)}))
        assert getattr(kind, "canvas", None) is None, name
    assert transition(("wipe", {"canvas": {"own": BIG}})).canvas is None  # (half a canvas)


def test_handover_gives_the_canvas_to_the_ones_that_take_it_at_the_pace_set():
    config = Config(transition="wipe", transitions=TransitionsConfig(default="wipe", speed="slow"))
    kind = handover(config, "game", False, None, None, canvas=canvas(BAR))
    assert kind.name == "wipe" and kind.canvas == C.Canvas(tuple(BAR), tuple(WHOLE))
    assert kind.duration == pytest.approx(C.CANVAS_DURATIONS["wipe"] * 1.6)
    config = Config(transitions=TransitionsConfig(default="fade"))
    kind = handover(config, "game", False, None, None, canvas=canvas(BAR))
    assert kind.name == "fade" and kind.duration == pytest.approx(0.6)
    config = Config(
        transition="random", transitions=TransitionsConfig(default="random", random=["light"])
    )
    kind = handover(config, "game", False, None, None, pick=lambda options: options[0],
                    canvas=canvas(BAR))  # fmt: skip
    assert kind.name == "light" and kind.canvas is not None
    kind = handover(config, "game", False, None, None, pick=lambda options: options[0])
    assert kind.canvas is None and kind.duration == pytest.approx(1.1)


@pytest.mark.parametrize("name", ["slide", "slide-right", "slide-up", "slide-down"])
def test_a_slide_runs_over_the_panels_through_the_gap_between_them(name):
    """One front over both panels: on each it pushes the old screen out where the front is,
    before it the old screen, behind it the new one, with a soft shadow at the seam; while
    it is in the gap one panel is all old and the other all new."""
    way = C.SLIDE_WAYS[name]
    big = transition((name, {"canvas": canvas(BIG)}))
    bar = transition((name, {"canvas": canvas(BAR)}))
    old_big, new_big = frames((1920, 720))
    old_bar, new_bar = frames((1920, 480))
    for t in (0.0, 0.1, 0.3, 0.5, 0.7, 0.9, 0.999, 1.0):
        a = big.frame(old_big, new_big, t)
        b = bar.frame(old_bar, new_bar, t)
        assert a.size == (1920, 720) and b.size == (1920, 480)
        if t >= 1.0:
            assert a.getpixel((0, 0)) == NEW and b.getpixel((1919, 479)) == NEW
        if way[0]:
            # the front in millimetres, from each panel's seam: the same front on both
            span = C.span(tuple(WHOLE), way)
            front = span[0] + C.smooth(t) * (span[1] - span[0])
            x_mm = front * way[0]
            seam_big = C.seam(big.canvas, way, t, 1920)
            seam_bar = C.seam(bar.canvas, way, t, 1920)
            assert seam_big == min(1920, max(0, round(x_mm * 1920 / BIG[2])))
            assert seam_bar == min(1920, max(0, round((x_mm - BAR[0]) * 1920 / BAR[2])))
            if BIG[2] < x_mm < BAR[0]:  # in the gap
                ahead = way[0] > 0
                assert a.getpixel((960, 360)) == (NEW if ahead else OLD)
                assert b.getpixel((960, 240)) == (OLD if ahead else NEW)
        else:  # up or down: both panels at once, each by its own height
            assert C.seam(big.canvas, way, t, 720) in range(0, 721)
    # the shadow on the old screen beside the seam
    t = 0.2
    seam = C.seam(big.canvas, way, t, 1920 if way[0] else 720)
    shot = big.frame(old_big, new_big, t)
    if way[0] and 0 < seam < 1920:
        beside = seam if way[0] > 0 else seam - 1
        assert shot.getpixel((beside, 360))[0] < OLD[0]  # darker than the old screen
        far = seam + 200 if way[0] > 0 else seam - 200
        if 0 <= far < 1920:
            assert shot.getpixel((far, 360)) == OLD


def test_the_wipe_goes_on_across_the_panels_where_it_left_off():
    """Its map is the panel's part of one from 0 at the left of all of them to 255 at the
    right: the left panel's ends where the gap begins, the right one's goes on after it."""
    left = C.wipe_map(C.canvas_of({"canvas": canvas(BIG)}), 1920, 720)
    right = C.wipe_map(C.canvas_of({"canvas": canvas(BAR)}), 1920, 480)
    assert left.getpixel((0, 0)) == 0 and right.getpixel((1919, 0)) == 255
    assert left.getpixel((1919, 719)) == round(255 * BIG[2] / WHOLE[2])
    assert right.getpixel((0, 0)) == round(255 * BAR[0] / WHOLE[2])
    row = [left.getpixel((x, 0)) for x in range(1920)]
    assert row == sorted(row)
    wipe = transition(("wipe", {"canvas": canvas(BAR)}))
    old, new = frames((1920, 480))
    assert wipe.frame(old, new, 0.3).getpixel((960, 240)) == OLD  # (the edge is still left)
    assert wipe.frame(old, new, 0.999).getpixel((960, 240)) == NEW


def test_the_line_of_light_crosses_the_panels_as_one_line():
    line_big = C.canvas_of({"canvas": canvas(BIG)})
    line_bar = C.canvas_of({"canvas": canvas(BAR)})
    for t in (0.0, 0.2, 0.45, 0.6, 1.0):
        x_big, x_bar = C.light_at(line_big, t, 1920), C.light_at(line_bar, t, 1920)
        mm = (x_big * BIG[2] / 1920, BAR[0] + x_bar * BAR[2] / 1920)
        assert mm[0] == pytest.approx(mm[1], abs=0.2), t  # (the same place, to a pixel)
    assert C.light_at(line_big, 0.0, 1920) < 0 and C.light_at(line_bar, 1.0, 1920) > 1920
    light = transition(("light", {"canvas": canvas(BAR)}))
    old, new = frames((1920, 480))
    assert light.frame(old, new, 0.1).getpixel((1900, 240)) == OLD  # (it has not come yet)
    assert light.frame(old, new, 0.98).getpixel((200, 240))[2] >= 200  # (behind it: new)


@pytest.mark.parametrize("name", list(C.CANVAS_DURATIONS))
def test_every_frame_in_its_size_whatever_the_panels_and_their_place(name):
    """A panel of any size, either way round, placed anywhere (beyond the others, too): each
    frame in its size, RGB."""
    places = [(BIG, WHOLE), (BAR, WHOLE), ([-40, 500, 10, 300], WHOLE),
              ([0, 0, 1, 1], [0, 0, 1, 1]), ([5, 5, 0.01, 900], [0, 0, 20, 20])]  # fmt: skip
    for own, whole in places:
        kind = transition((name, {"canvas": {"own": own, "whole": whole}}))
        for size in ((1920, 480), (480, 1920), (3, 2), (1, 1), (320, 240)):
            old, new = frames(size)
            for t in (-0.1, 0.0, 0.01, 0.5, 0.99, 1.0, 1.4):
                out = kind.frame(old, new, t)
                assert out.size == size and out.mode == "RGB", (own, size, t)


def _loop(rects, panel=None, on=True):
    loop = SimpleNamespace(laid_of=lambda: rects, panel=panel)
    config = Config(transitions=TransitionsConfig(canvas=on))
    return (lambda: app.MainLoop._canvas(loop, config)), config


def test_a_panel_is_told_where_it_and_all_of_them_stand():
    rects = [tuple(BIG), None, tuple(BAR)]
    first, _ = _loop(rects)
    assert first() == {"own": BIG, "whole": WHOLE}
    beside, _ = _loop(rects, panel=1)  # [[panels]][1]: the third
    assert beside() == {"own": BAR, "whole": WHOLE}
    assert _loop(rects, panel=0)[0]() is None  # (one whose size is not known)
    assert _loop([tuple(BIG)])[0]() is None  # (one panel)
    assert _loop(rects, on=False)[0]() is None  # ([transitions] canvas = false)
    loop = SimpleNamespace(laid_of=None, panel=None)
    assert app.MainLoop._canvas(loop, Config()) is None


def test_every_panel_begins_its_part_at_the_same_moment():
    """A scene: from the moment config.toml was written; a game, music or the rest: from the
    moment the first panel to come to it did, the others taking it."""
    loop = SimpleNamespace()
    before, after = Config(scene="a"), Config(scene="b")
    after.stamp = int((time.time() - 0.3) * 1e9)
    late = app.MainLoop._together(loop, before, after, "manual", False)
    assert late == pytest.approx(app.SWEEP_LEAD_S - 0.3, abs=0.05)
    first = app.MainLoop._together(loop, Config(), Config(), "game", False)
    time.sleep(0.1)
    second = app.MainLoop._together(loop, Config(), Config(), "game", False)
    assert first == pytest.approx(app.SWEEP_LEAD_S, abs=0.05)
    assert second == pytest.approx(first - 0.1, abs=0.05)  # the same moment
    other = app.MainLoop._together(loop, Config(), Config(), "game", True)  # (the game ends)
    assert other == pytest.approx(app.SWEEP_LEAD_S, abs=0.05)
    assert set(app.TOGETHER) == {"game", "media", "idle"}


def test_a_show_on_all_the_panels_begins_on_each_from_when_it_was_asked():
    number = transition_show.show("wipe", "Wipe")
    try:
        assert transition_show.lead(number) == pytest.approx(transition_show.LEAD_S, abs=0.05)
        assert transition_show.lead(number + 1) == 0.0
    finally:
        transition_show.done(number)
    assert transition_show.lead(number) == 0.0

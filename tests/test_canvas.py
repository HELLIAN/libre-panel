"""One transition over all the panels as one (render/canvas.py): where each panel's part of a
slide, the wipe, the line of light, the iris, the shatter, the cascade, the comet and the portal
lies, how the main loop tells a panel where it stands and when all of them begin, and that
nothing else changes for one panel alone."""

from __future__ import annotations

import time
from types import SimpleNamespace

import numpy as np
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


def test_only_those_that_can_go_over_the_panels_do():
    """Given where the panels stand, the slides, the wipe, the line of light, the iris and the
    shatter take as long as across them all (the pace of Settings → Transitions stretching it),
    longer than alone; the cascade, the comet and the portal as long as alone, being as long
    there as in the preview that was liked; any other plays on its panel alone, as before."""
    for name, seconds in C.CANVAS_DURATIONS.items():
        kind = transition((name, {"canvas": canvas(BIG)}))
        assert kind.canvas is not None and kind.duration == seconds, name
        alone = transition(name)
        assert alone.canvas is None, name
        if name in ("cascade", "comet", "portal"):
            assert alone.duration == seconds, name
        else:
            assert alone.duration < seconds, name
    for name in ("fade", "tube", "entrance", "zoom", "blinds", "dissolve", "tiles", "glitch"):
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


def test_the_iris_opens_from_the_middle_of_all_the_panels():
    """Across the panels its map is how far each pixel is from the middle of all of them: the
    same on either side of the gap for the same distance; it opens there first."""
    big = C.canvas_of({"canvas": canvas(BIG)})
    bar = C.canvas_of({"canvas": canvas(BAR)})
    at = C.middle(big.whole)
    far = C.reach(big.whole, at) + 11.0
    left, right = C.radial_map(big, (1920, 720), at, far), C.radial_map(bar, (1920, 480), at, far)
    x, _ = C.pixel(big, (1920, 720), at)
    assert left.getpixel((min(1919, round(x)), 360)) <= 1  # (the middle: 0)
    edge_big = left.getpixel((1919, 360))  # (each side of the gap, as far as its way)
    edge_bar = right.getpixel((0, 240))
    gap = (BAR[0] - (BIG[0] + BIG[2])) * 255 / far
    assert edge_bar - edge_big == pytest.approx(gap, abs=2)
    iris = transition(("iris", {"canvas": canvas(BIG)}))
    old, new = frames((1920, 720))
    shot = iris.frame(old, new, 0.3)
    assert shot.getpixel((min(1919, round(x)), 360)) == NEW and shot.getpixel((0, 0)) == OLD
    assert iris.frame(old, new, 0.0) == old and iris.frame(old, new, 1.0) == new


def test_the_shatter_falls_along_all_the_panels():
    """The old screen whole at first; the tiles of the left panel go before those of the
    right one; at the end the new screen, out of the shade."""
    big = transition(("shatter", {"canvas": canvas(BIG)}))
    bar = transition(("shatter", {"canvas": canvas(BAR)}))
    big_old, big_new = frames((1920, 720))
    bar_old, bar_new = frames((1920, 480))
    assert big.frame(big_old, big_new, 0.0).tobytes() == big_old.tobytes()
    assert bar.frame(bar_old, bar_new, 0.0).tobytes() == bar_old.tobytes()
    t = 0.3
    assert big.frame(big_old, big_new, t).getpixel((20, 20)) != OLD  # (its first tiles fell)
    assert bar.frame(bar_old, bar_new, t).getpixel((1900, 460)) == OLD  # (the last not yet)
    last = bar.frame(bar_old, bar_new, 0.99)
    assert last.getpixel((960, 240)) == NEW


class _Drawn:
    """A renderer that drew its frame: a ground and the boxes on it (Renderer.beneath)."""

    def __init__(self, size, boxes):
        self.ground = Image.new("RGB", size, (0, 120, 0))
        self.boxes = boxes

    def beneath(self):
        return self.ground, [list(b) for b in self.boxes]


def test_the_cascade_brings_the_cards_in_on_their_ground_along_the_panels():
    """The ground first, then each card where it lies along the way: the left panel's before
    the right one's; cards that overlap come as one; at the end the new screen whole."""
    GROUND = (0, 120, 0)
    big_cards = [(40, 40, 600, 680), (500, 300, 700, 400), (1300, 40, 1880, 680)]
    contexts = {}
    for name, own, size, boxes in (("big", BIG, (1920, 720), big_cards),
                                   ("bar", BAR, (1920, 480), [(100, 40, 1800, 440)])):  # fmt: skip
        context = SimpleNamespace(palette={}, _renderer=_Drawn(size, boxes))
        contexts[name] = (transition(("cascade", {"canvas": canvas(own)}), context), size)
    (big, big_size), (bar, bar_size) = contexts["big"], contexts["bar"]
    big_old, big_new = frames(big_size)
    bar_old, bar_new = frames(bar_size)
    assert big.frame(big_old, big_new, 0.0).tobytes() == big_old.tobytes()
    early = big.frame(big_old, big_new, 0.29)  # (the ground there, the first card coming)
    assert early.getpixel((1910, 700)) == GROUND and early.getpixel((1600, 360)) == GROUND
    assert early.getpixel((300, 360)) != GROUND
    mid = big.frame(big_old, big_new, 0.5)
    assert mid.getpixel((300, 360)) == NEW and mid.getpixel((650, 350)) == NEW  # (as one)
    assert bar.frame(bar_old, bar_new, 0.5).getpixel((960, 240)) == GROUND  # (not yet)
    assert bar.frame(bar_old, bar_new, 0.8).getpixel((960, 240)) == NEW
    assert big.frame(big_old, big_new, 0.999).getpixel((1910, 700)) != GROUND  # (all of it)
    assert big.frame(big_old, big_new, 1.0) == big_new


def test_the_cascade_takes_its_ground_and_cards_from_how_a_theme_is_drawn():
    """What covers all of the panel at the bottom of it is the ground (Encore's picture of
    the cover all over, its veil); lights and a widget's own parts are no cards."""
    from libre_panel.render.renderer import Renderer
    from libre_panel.sensors.demo import demo_snapshot
    from libre_panel.theme.model import find_theme, load_theme

    renderer = Renderer(load_theme(find_theme("encore-pair")), preview=True)
    assert renderer.beneath() is None  # (nothing drawn yet)
    frame, _ = renderer.render(demo_snapshot(), now=1_763_000_000.0)
    ground, boxes = renderer.beneath()
    assert ground.size == frame.size and ground.mode == "RGB"
    plain = Image.new("RGB", frame.size, renderer.color(renderer.theme.background_color)[:3])
    assert ground.tobytes() != plain.tobytes()  # (the cover's picture and the veil in it)
    w, h = frame.size
    assert boxes and all(b != [0, 0, w, h] and 0 <= b[0] < b[2] <= w for b in boxes)
    renderer.close()


def test_the_comet_flies_from_panel_to_panel_and_leaves_the_new_screen_whole():
    """Its way is the same over both panels; it crosses the left one before the right one;
    by its end its tail has left them and the new screen is open everywhere."""
    big = transition(("comet", {"canvas": canvas(BIG)}))
    bar = transition(("comet", {"canvas": canvas(BAR)}))
    big_old, big_new = frames((1920, 720))
    bar_old, bar_new = frames((1920, 480))
    assert big.frame(big_old, big_new, 0.0).tobytes() == big_old.tobytes()
    assert bar.frame(bar_old, bar_new, 0.0).tobytes() == bar_old.tobytes()

    def lit(shot: Image.Image) -> int:  # (its light: pixels neither the old nor the new)
        pixels = np.asarray(shot).reshape(-1, 3)
        return int((~(pixels == OLD).all(1) & ~(pixels == NEW).all(1)).sum())

    assert lit(big.frame(big_old, big_new, 0.3)) > 0  # (it is over the left one)
    assert lit(bar.frame(bar_old, bar_new, 0.3)) == 0  # (not yet over the right)
    assert lit(bar.frame(bar_old, bar_new, 0.75)) > 0
    for kind, old, new in ((big, big_old, big_new), (bar, bar_old, bar_new)):
        end = np.asarray(kind.frame(old, new, 0.999)).astype(int)
        assert np.abs(end - np.array(NEW)).max() <= 3, kind


def test_the_portal_opens_from_the_middle_with_its_ring():
    """The new screen first where the middle of all the panels is; its ring of light lies at
    the opening's edge; at the end the new screen whole."""
    big = transition(("portal", {"canvas": canvas(BIG)}))
    old, new = frames((1920, 720))
    assert big.frame(old, new, 0.0).tobytes() == old.tobytes()
    x, _ = C.pixel(C.canvas_of({"canvas": canvas(BIG)}), (1920, 720), C.middle(tuple(WHOLE)))
    shot = big.frame(old, new, 0.2)
    assert shot.getpixel((min(1919, round(x)), 360)) == NEW and shot.getpixel((0, 0)) == OLD
    row = [shot.getpixel((i, 360)) for i in range(1920)]
    bright = [i for i, c in enumerate(row) if sum(c) > sum(NEW) + 60]  # (the ring's light)
    assert bright and min(bright) < x
    assert big.frame(old, new, 1.0) == new


def test_the_rings_light_ends_at_its_reach():
    """A pixel a rounding short of the ring's reach takes the light's last step, not one past
    it (the ring at a radius where its offset is the float just below the reach)."""
    import math

    from libre_panel.render.handovers import Portal, _Opening, _ring

    size, reach = (64, 64), Portal.GLOW_MM
    opening = _Opening(C.alone(size), size, reach, Portal.EDGE_MM)
    edges = []  # (of pixels farther than its reach: the ring then on the panel, and lit)
    for d in opening.away[opening.away > reach + 2][::50]:
        for k in range(-40, 40):
            r = float(d) - reach + k * math.ulp(float(d) - reach)
            off = float(d) - r
            if abs(off) < reach and int((off + reach) * 64) >= len(opening.light[0]):
                edges.append(r)
    assert edges  # (there are such radii)
    for r in edges[:20]:
        out = Image.new("RGB", size)
        _ring(out, opening, r, 1.0, (46, 230, 214), (255, 255, 255), reach)
        assert out.getbbox() is not None


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

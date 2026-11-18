"""The light that moves (render/shine.py): its curves and lights, where each lies, that a light
standing as before is the piece laid before, when a panel at rest is due again, that nothing
shines on a calm panel, in a preview, over a turned widget or of a kind that is off, and how
[shine] and a theme's ``animation.shine`` choose the kinds."""

import json
import math

import numpy as np
import pytest
from PIL import Image

from libre_panel import app
from libre_panel.config import ConfigError, parse_config
from libre_panel.render import shine
from libre_panel.render.renderer import Renderer
from libre_panel.sensors.base import Reading, Snapshot
from libre_panel.theme.model import ThemeError, parse_theme

GAUGE = {"type": "gauge", "id": "g", "x": 10, "y": 10, "w": 100, "h": 100, "sensor": "v",
         "thickness": 10, "color": "#40a0ff"}  # fmt: skip
GRAPH = {"type": "graph", "id": "h", "x": 130, "y": 10, "w": 200, "h": 80, "sensor": "v",
         "color": "#ffb040"}  # fmt: skip
SUN = {"type": "sunpath", "id": "s", "x": 10, "y": 130, "w": 200, "h": 50}

AT_REST = 1002.0  # (a ring's sweep has run: 2 s into its 4 s)
SWEEPING = 1000.5  # (a ring's sweep runs)


def theme_of(*widgets, animation=None):
    data = {
        "format": "libre-panel-theme/1",
        "display": {"width": 360, "height": 200},
        "background": {"color": "#0a1119"},
        "widgets": [dict(w) for w in widgets],
    }
    if animation is not None:
        data["animation"] = animation
    return parse_theme(data)


def snap(value=50.0, **more):
    readings = {"v": Reading("v", value, "%", "", "", 0.0)}
    for key, v in more.items():
        readings[key.replace("_", ".")] = Reading(key.replace("_", "."), v, "", "", "", 0.0)
    return Snapshot(readings=readings, history={"v": [20.0, 40.0, 30.0, value]})


def renderer_of(*widgets, kinds=shine.KINDS, animate=True, preview=False):
    renderer = Renderer(theme_of(*widgets), animate=animate, preview=preview)
    renderer.shine.kinds = frozenset(kinds)
    return renderer


def lights(renderer):
    """The lights of the frame drawn last: name (its widget's id and its own): piece."""
    return {wid.replace("\0shine-", ":"): piece
            for wid, piece in renderer._last[0] if "\0shine-" in wid}  # fmt: skip


# -- the curves and the lights -----------------------------------------------------------------


def test_the_curves_of_time_begin_and_end_where_they_should():
    assert shine.phase(10.0, 4.0) == 0.5 and shine.phase(-1.0, 4.0) == 0.75
    assert shine.phase(5.0, 4.0, 1.0) == 0.0
    assert shine.wave(0.0) == 0.0 and shine.wave(0.5) == 1.0 and shine.wave(1.0) == 0.0
    assert shine.wave(0.25) == pytest.approx(0.5) and shine.wave(0.1) == shine.wave(0.9)
    assert shine.ease(-1) == 0.0 and shine.ease(0.5) == 0.5 and shine.ease(2) == 1.0
    assert shine.hump(0.0) == 0.0 and shine.hump(0.5) == 1.0 and shine.hump(1.0) == 0.0
    assert [shine.level(s) for s in (-1, 0, 0.04, 0.05, 0.5, 1, 2)] == [0, 0, 0, 1, 6, 12, 12]


def test_a_light_is_round_brightest_in_its_middle_and_as_strong_as_its_step():
    parts = (("spot", 4.0, (64, 160, 255), 0.9, 1.0), ("spot", 9.0, (64, 160, 255), 0.0, 0.5))
    full = np.asarray(shine.make(parts, shine.LEVELS), np.int16)
    half = math.ceil(9.0 * shine.REACH)
    assert full.shape == (2 * half + 1, 2 * half + 1, 3)
    assert (full == full[::-1]).all() and (full == full[:, ::-1]).all()
    assert (full == full.transpose(1, 0, 2)).all()  # (round)
    assert tuple(full[half, half]) == (255, 255, 255)  # (its white core)
    assert not full[0].any() and not full[:, 0].any()  # (it ends inside its square)
    weaker = np.asarray(shine.make(parts, 6), np.int16)
    assert (weaker <= full).all() and weaker.sum() < full.sum() * 0.6
    ring = np.asarray(shine.make((("ring", 10.0, (255, 0, 0), 2.0, 1.0),), shine.LEVELS))
    assert ring[12, 22, 0] == 255 and ring[12, 12, 0] == 0  # (on the ring, not in its middle)


def test_the_cover_s_glow_lies_round_it_in_its_own_colours():
    cover = Image.new("RGBA", (64, 64), (200, 30, 30, 255))
    bands = shine.cover_glow(cover, shine.LEVELS)
    m = round(0.23 * 64)
    (above, ax, ay), (below, bx, by), (left, lx, ly), (right, rx, ry) = bands
    assert (ax, ay, bx, by) == (-m, -m, -m, 64) and (lx, ly, rx, ry) == (-m, 0, 64, 0)
    assert above.size == below.size == (64 + 2 * m, m) and left.size == right.size == (m, 64)
    near = np.asarray(left)[32, m - 1]  # (next to the cover's edge: its red, half as strong)
    assert near[0] > 3 * max(near[1], near[2]) and 80 < near[0] < 200
    assert not np.asarray(left)[:, 0].any()  # (gone at the margin's end)
    weak = np.asarray(shine.cover_glow(cover, 3)[2][0], np.int16)
    assert (weak <= np.asarray(left, np.int16)).all()
    assert all(band is None for band, _, _ in shine.cover_glow(cover, 0))
    rounded = shine.cover_glow(cover, shine.LEVELS, radius=12)
    assert rounded[0][0].size == (64 + 2 * m, m + 12) and rounded[2][0].size == (m, 64 - 24)


def test_the_cover_glows_steady_while_music_plays_made_at_full_strength_once(monkeypatch):
    made = []
    cover_glow = shine.cover_glow
    monkeypatch.setattr(shine, "cover_glow", lambda *a: made.append(a[1]) or cover_glow(*a))
    widget = {"type": "image", "id": "c", "x": 60, "y": 30, "w": 100, "h": 100,
              "src": "@media.cover", "radius": 10}  # fmt: skip
    renderer = renderer_of(widget, kinds={"music"})
    art = Image.new("RGB", (100, 100), (220, 40, 160))

    def playing(state, at):
        here = Snapshot(readings={"media.state": Reading("media.state", state, "", "", "", 0.0)},
                        images={"media.cover": art})  # fmt: skip
        renderer.render(here, at)
        return {k: v for k, v in lights(renderer).items() if k.startswith("c:glow")}

    first = playing("Playing", 1000.0)  # (a first frame: as it is)
    assert set(first) == {"c:glow0", "c:glow1", "c:glow2", "c:glow3"} and not renderer.moving
    for at in (1000.5, 1001.3, 1002.9, 1004.1):  # (steady: the same pieces, nothing due)
        assert playing("Playing", at) == first and renderer.due is None
    fading = []
    for k in range(8):  # (it goes in FADE_S, eased: 7, 7, 5, 4, 2, 1 of 12, then none)
        fading.append(playing("Paused", 1005.0 + k * 0.1))
        assert renderer.moving == (k < 6)
    assert fading[1]["c:glow0"] is first["c:glow0"]
    levels = [f["c:glow0"].layer.tobytes() for f in fading[2:6]]
    assert len(set(levels)) == 4 and first["c:glow0"].layer.tobytes() not in levels
    assert fading[6] == fading[7] == {}
    back = [playing("Playing", 1010.0 + k * 0.1) for k in range(8)]
    assert back[-1]["c:glow0"].layer.tobytes() == first["c:glow0"].layer.tobytes()
    assert made == [shine.LEVELS]  # (made once at full, each strength scaled from it)


def test_the_bars_after_playing_dance_within_their_heights():
    low, high = round(20 * 0.2), round(20 * 0.72)
    seen = set()
    for i in range(200):
        bars = shine.dance(i / shine.DANCE_HZ, 20.0, 1.0)
        assert len(bars) == 3 and all(low <= b <= high for b in bars)
        seen.add(bars)
    assert len(seen) > 20  # (they move, and not together)
    assert shine.dance(3.3, 20.0, 0.0) == (low, low, low)  # (still as it stops)
    image = shine.dance_bars((4, 14, 9), 20.0, (255, 255, 255))
    pixels = np.asarray(image)
    assert image.size == (3 * 3 + 2 * 3, 14) and image.mode == "RGBA"
    assert [int((pixels[:, x, 3] > 0).sum()) for x in (0, 6, 12)] == [4, 14, 9]
    assert not pixels[:, 3:6].any()  # (a gap between)


# -- on a panel --------------------------------------------------------------------------------


def test_a_ring_s_end_breathes_where_the_ring_ends_and_its_sweep_runs_along_it():
    renderer = renderer_of(GAUGE, kinds={"rings"})
    renderer.render(snap(50.0), AT_REST)
    laid = lights(renderer)
    assert set(laid) == {"g:head"} and laid["g:head"].add  # (light added onto the frame)
    head = laid["g:head"]
    # the end of half a ring from 135° to 405°: straight up, on the ring's middle line
    cx, cy = head.x + head.layer.width // 2, head.y + head.layer.height // 2
    assert (cx, cy) == (math.floor(10 + 49.5 + 0.5), math.floor(10 + 49.5 - 45 + 0.5))
    renderer.render(snap(50.0), SWEEPING)
    laid = lights(renderer)
    assert set(laid) == {"g:head", "g:sweep"} and renderer.moving


def test_a_ring_over_its_hot_mark_glows_wider_the_fuller_it_is():
    renderer = renderer_of(GAUGE, kinds={"rings"})
    renderer.render(snap(50.0), AT_REST)
    assert "g:hot" not in lights(renderer)
    renderer = renderer_of(GAUGE, kinds={"rings"})
    renderer.render(snap(90.0), AT_REST)
    hot = lights(renderer)["g:hot"].layer
    renderer = renderer_of(GAUGE, kinds={"rings"})
    renderer.render(snap(100.0), AT_REST)
    hotter = lights(renderer)["g:hot"].layer
    assert hot.size == hotter.size and np.asarray(hot).sum() < np.asarray(hotter).sum()


def test_a_light_that_stands_as_it_stood_is_the_piece_laid_before():
    renderer = renderer_of(GAUGE, GRAPH, SUN)
    renderer.render(snap(50.0, sun_progress=0.4), AT_REST)
    before = lights(renderer)
    assert {"g:head", "h:beat", "s:body"} <= set(before)
    renderer.render(snap(50.0, sun_progress=0.4), AT_REST + 0.001)
    after = lights(renderer)
    assert before["g:head"] is after["g:head"]  # (nothing made, composed or sent anew)
    assert before["s:body"] is after["s:body"]
    renderer.render(snap(60.0, sun_progress=0.4), AT_REST + shine.BREATH_S)  # (glided there)
    moved = lights(renderer)["g:head"]
    assert moved.layer is before["g:head"].layer and moved.x > before["g:head"].x  # (on)


def test_a_panel_at_rest_is_due_when_its_light_next_changes():
    renderer = renderer_of(GAUGE, kinds={"rings"})
    renderer.render(snap(50.0), AT_REST)
    assert not renderer.moving and renderer.due is not None
    due = renderer.due
    assert AT_REST < due <= AT_REST + shine.LOOK_AHEAD_S + 1e-9
    head = lights(renderer)["g:head"]
    renderer.render(snap(50.0), due - 0.002)
    assert lights(renderer)["g:head"] is head  # (still as it was a moment before)
    renderer.render(snap(50.0), due + 0.002)
    assert lights(renderer)["g:head"] is not head  # (a step of its breath on)


def test_a_graph_s_end_beats_once_a_reading():
    renderer = renderer_of(GRAPH, kinds={"graphs"})
    period = max(0.25, renderer.theme.refresh_ms / 1000.0)
    start = math.ceil(1000.0 / period) * period
    run = min(shine.BEAT_RUN_S, 0.8 * period)
    beats = []
    for k in range(shine.BEAT_STEPS + 2):
        renderer.render(snap(50.0), start + k * run / shine.BEAT_STEPS + 1e-6)
        beats.append(lights(renderer)["h:beat"].layer)
    assert len({beat.tobytes() for beat in beats}) == shine.BEAT_STEPS + 1  # (a step each)
    assert beats[-1] is beats[-2]  # (gone out: the end at rest till the next reading)
    rest = np.asarray(beats[-1], np.int16)
    ring = np.asarray(beats[shine.BEAT_STEPS // 2], np.int16)
    assert (ring > rest).any()  # (the ring of light going out)


def test_the_sun_breathes_on_its_way_and_the_moon_by_night():
    renderer = renderer_of(SUN, kinds={"sun"})
    renderer.render(snap(sun_progress=0.4), 1004.0)  # (3 s after its glint began: it has run)
    assert set(lights(renderer)) == {"s:body"} and not renderer.moving
    day = lights(renderer)["s:body"].layer
    renderer.render(snap(sun_progress=0.4), 1001.5)  # (its glint runs along the day gone)
    assert set(lights(renderer)) == {"s:body", "s:glint"} and renderer.moving
    renderer.render(snap(sun_up=0.0), AT_REST)
    night = lights(renderer)["s:body"].layer
    assert day.size != night.size or day.tobytes() != night.tobytes()
    renderer.render(Snapshot(), AT_REST)
    assert lights(renderer) == {}  # (nothing to show: no light)


def test_a_download_s_sheen_runs_the_faster_the_faster_it_loads():
    state = {"id": "steam:1", "source": "steam", "name": "Game", "phase": "downloading",
             "bytes": 9e9, "total": 24e9, "rate": 1e5, "remaining_s": 404.0, "elapsed_s": 252.0,
             "estimated": False, "history": [3e7 + i * 2000 for i in range(120)],
             "more": 0}  # fmt: skip
    widget = {"type": "downloads", "id": "d", "x": 20, "y": 10, "w": 320, "h": 130}

    def travel(rate):
        renderer = renderer_of(widget, kinds={"downloads"})
        here = Snapshot(readings={"downloads": Reading(
            "downloads", json.dumps({**state, "rate": rate}), "", "", "", 0.0)})  # fmt: skip
        renderer.render(here, 1000.0)
        a = lights(renderer)["d:sheen"].x
        renderer.render(here, 1000.1)
        assert renderer.moving
        return abs(lights(renderer)["d:sheen"].x - a)

    assert travel(1e8) > travel(1e5)


def test_nothing_shines_on_a_calm_panel_in_a_preview_turned_or_of_a_kind_that_is_off():
    for renderer in (renderer_of(GAUGE, GRAPH, animate=False),
                     renderer_of(GAUGE, GRAPH, preview=True),
                     renderer_of(GAUGE, GRAPH, kinds=()),
                     renderer_of({**GAUGE, "rotate": 90}, {**GRAPH, "rotate": 180})):  # fmt: skip
        renderer.render(snap(50.0), AT_REST)
        assert lights(renderer) == {}
    renderer = renderer_of(GAUGE, GRAPH, kinds={"graphs"})
    renderer.render(snap(50.0), AT_REST)
    assert set(lights(renderer)) == {"h:beat"}


def test_what_lay_under_pieces_gone_from_the_frame_is_not_kept():
    # (a light that moves over a ring whose value changes: the regions laid again keep what
    # lies under it, but never the ring's pictures of frames gone)
    renderer = renderer_of(GAUGE, GRAPH, kinds={"rings", "graphs"})
    seen = 0
    for k in range(60):  # (the ring stands while its sweep runs over it, then takes a value)
        renderer.render(snap(50.0 if k < 20 else 70.0), SWEEPING - 0.5 + k * 0.04)
        now = {id(piece) for _, piece in renderer._last[0]}
        kept = renderer._underlays.items()
        assert all(id(p) in now for _region, (under, _part) in kept for p in under), k
        seen = max(seen, len(kept))
    assert seen  # (and some are kept)


def test_the_lights_of_a_widget_no_longer_shown_go():
    renderer = renderer_of(GAUGE, kinds={"rings"})
    renderer.render(snap(50.0), AT_REST)
    assert "g\0head" in renderer.shine._laid
    renderer.render(Snapshot(), AT_REST + 1)
    assert lights(renderer) == {} and renderer.shine._laid == {}


# -- the kinds ---------------------------------------------------------------------------------


def test_shine_in_config_toml_turns_each_kind_on_or_off():
    assert parse_config({}).shine.kinds() == frozenset(shine.KINDS)
    config = parse_config({"shine": {"rings": False, "sun": False}})
    assert config.shine.kinds() == frozenset({"graphs", "music", "downloads"})
    for bad, said in (({"nope": True}, "shine.nope"), ({"music": 1}, "shine.music")):
        with pytest.raises(ConfigError, match=said):
            parse_config({"shine": bad})
    with pytest.raises(ConfigError):
        parse_config({"shine": False})


def test_a_theme_names_the_kinds_it_shows():
    assert theme_of(GAUGE).shine is None
    assert theme_of(GAUGE, animation={"shine": True}).shine is None
    assert theme_of(GAUGE, animation={"shine": False}).shine == ()
    chosen = theme_of(GAUGE, animation={"shine": ["sun", "rings", "sun"]})
    assert chosen.shine == ("rings", "sun")
    assert chosen.to_dict()["animation"]["shine"] == ["rings", "sun"]
    for bad in ("rings", ["rings", 1], ["glitter"]):
        with pytest.raises(ThemeError, match="animation.shine"):
            theme_of(GAUGE, animation={"shine": bad})


def test_a_panel_shows_the_kinds_both_config_and_theme_have_on():
    config = parse_config({"shine": {"rings": False}})
    assert app.shine_kinds(config, theme_of(GAUGE)) == frozenset(shine.KINDS) - {"rings"}
    theme = theme_of(GAUGE, animation={"shine": ["rings", "sun"]})
    assert app.shine_kinds(config, theme) == frozenset({"sun"})
    assert app.shine_kinds(config, theme_of(GAUGE, animation={"shine": False})) == frozenset()

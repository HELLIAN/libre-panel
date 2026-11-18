"""The light field under a theme (render/field.py): how a frame is laid in front of it, the
theme's ``background.field``, the renderer's light from step to step, and the engine drawing
the same frames from their layers."""

from __future__ import annotations

import copy
import json
import subprocess

import numpy as np
import pytest
from PIL import Image, ImageChops, ImageFilter

from libre_panel.devices import engine as engines
from libre_panel.devices import h264
from libre_panel.render import field
from libre_panel.render import scene as scenes
from libre_panel.render.canvas import Canvas
from libre_panel.render.renderer import Piece, Renderer
from libre_panel.sensors.demo import demo_snapshot
from libre_panel.theme.light_colours import light_theme
from libre_panel.theme.model import (
    FIELD_LOOKS,
    ThemeError,
    find_theme,
    parse_theme,
    with_look,
)

T0 = 1_795_000_000.0


def _rgba(colour, size=(4, 3)):
    return Image.new("RGBA", size, colour)


def test_a_piece_in_front_of_the_light_as_the_integer_steps_say():
    """Over a light ``g``: a piece of colour ``s`` and alpha ``a`` gives
    ``div255(s a) + div255(g (255 - a))``; an opaque one itself; light adds and stops at 255;
    two pieces one over the other as the foreground's steps say, whatever the light."""
    for g in (0, 17, 128, 255):
        light = np.full((3, 4, 3), g, np.uint8)
        for s, a in ((200, 128), (9, 1), (255, 255), (40, 0), (0, 254)):
            fore = field.Foreground((4, 3))
            rgba = np.asarray(_rgba((s, s, s, a)))
            fore.over(rgba[:, :, :3], rgba[:, :, 3], 0, 0)
            got = int(fore.on(light)[0, 0, 0])
            assert got == min(255, _div(s * a) + _div(g * (255 - a))), (g, s, a, got)
    fore = field.Foreground((4, 3))
    fore.solid(np.full((3, 4, 3), 77, np.uint8), 1, 1)
    out = fore.on(np.full((3, 4, 3), 200, np.uint8))
    assert out[1, 1, 0] == 77 and out[0, 0, 0] == 200  # (the light where nothing lies)
    fore.add(np.full((2, 2, 3), 250, np.uint8), 2, 1)
    assert fore.on(np.zeros((3, 4, 3), np.uint8))[1, 2, 0] == 255
    # (a piece hanging off the box: what of it lies on it)
    fore = field.Foreground((4, 3))
    rgba = np.asarray(_rgba((90, 60, 30, 200), (6, 6)))
    fore.over(rgba[:, :, :3], rgba[:, :, 3], -2, 1)
    assert (fore.rgba[0] == 0).all() and (fore.rgba[1:, :, 3] == 200).all()


def _div(v: int) -> int:
    v += 128
    return (v + (v >> 8)) >> 8


def test_div255_rounds_as_pillow_divides():
    v = np.arange(0, 255 * 255 + 1, dtype=np.int32)
    assert (field.div255(v) == np.floor(v / 255 + 0.5).astype(np.int32)).all()


def _field_theme(name="lumen-1920x720", **spec):
    folder = find_theme(name)
    data = json.loads((folder / "theme.json").read_text())
    data["background"] = {"color": "#000000", "field": {"look": "lumen", **spec}}
    data["style"]["card_opacity"] = 0.58
    data.setdefault("switches", {}).update({
        "light_flows": {"label": "The light drifts", "on": True},
        "light_load": {"label": "The light follows the load", "on": True}})  # fmt: skip
    return parse_theme(data, folder)


def test_the_theme_says_its_field_and_keeps_it():
    theme = _field_theme(moves="switch.light_flows", load="!switch.light_load")
    assert theme.background_field == {"look": "lumen", "moves": "switch.light_flows",
                                      "load": "!switch.light_load"}  # fmt: skip
    again = parse_theme(theme.to_dict(), theme.root)
    assert again.background_field == theme.background_field
    assert FIELD_LOOKS == tuple(field.LOOKS)
    for bad in ("lumen", {"look": "nova"}, {"look": "lumen", "moves": 5}):
        data = theme.to_dict()
        data["background"]["field"] = bad
        with pytest.raises(ThemeError, match="background.field"):
            parse_theme(data, theme.root)
    assert light_theme(theme).background_field is None  # (no light behind a white panel)
    # a theme in its look takes its light and the switches it goes by
    plain = parse_theme(json.loads((find_theme("lumen-companion") / "theme.json").read_text()),
                        find_theme("lumen-companion"))  # fmt: skip
    dressed = with_look(plain, theme)
    assert dressed.background_field == theme.background_field
    assert {"light_flows", "light_load"} <= set(dressed.switches)


def test_the_load_it_follows():
    assert field.load_of({}) == 0.0
    history = {"cpu.load": [10.0] * 10 + [60.0], "gpu.load": [5.0] * 11}
    before, now = 0.1, (10.0 * 9 + 60.0) / 1000.0
    assert field.load_of(history, 0.0) == pytest.approx(before)
    assert field.load_of(history, 1.0) == pytest.approx(now)
    middle = field.load_of(history, 0.5)
    assert middle == pytest.approx((before + now) / 2)  # (smoothstep: half at the half)
    assert field.load_of({"gpu.load": [250.0] * 3}) == 1.0


def test_the_renderer_lays_the_frame_in_front_of_the_light_and_moves_it_in_steps():
    theme = _field_theme()
    renderer = Renderer(theme, dither=True)
    renderer.continuous, renderer.fps = True, 25
    snap = demo_snapshot(fixed_time=T0)
    frame, _ = renderer.render(snap, T0)
    light = renderer._light
    assert light is not None and light.picture.size == (1920, 720)
    pieces = list(renderer._last[0])
    assert frame.tobytes() == field.compose(light, pieces).tobytes()
    assert renderer.due == pytest.approx(field.stepped(T0) + 1 / field.RATE)
    # within the step the light stays (only what changed is composed); at the next, all anew
    renderer.render(snap, T0 + 0.04)
    assert renderer._light is light and renderer.damage != [(0, 0, 1920, 720)]
    renderer.render(snap, T0 + 0.2)
    assert renderer._light is not light and renderer.damage is None
    # parts of the frame composed again are the same as the whole composed at once
    renderer.render(snap, T0 + 0.24)
    whole = field.compose(renderer._light, list(renderer._last[0]))
    assert renderer._last[1].tobytes() == whole.tobytes()
    renderer.close()


def test_the_switches_hold_the_light_still_and_at_rest():
    theme = _field_theme(moves="switch.light_flows", load="switch.light_load")
    renderer = Renderer(theme)
    renderer.switches = {"light_flows": False, "light_load": False}
    busy = demo_snapshot(fixed_time=T0)
    busy.history["cpu.load"] = [100.0] * 20
    renderer.render(busy, T0)
    first = renderer._light
    assert first.key[3] == 0.0 and first.key[4] == 0.0  # (at 0, at rest)
    renderer.render(busy, T0 + 7.3)
    assert renderer._light is first and renderer.due is None
    renderer.switches = {"light_flows": True, "light_load": True}
    renderer.render(busy, T0 + 7.3)
    assert renderer._light.key[4] == 1.0 and renderer._light.key[3] == field.stepped(T0 + 7.3)
    renderer.close()


def test_two_panels_side_by_side_meet_in_one_light():
    """Each panel lays its part of the canvas's light: where they meet, they go on."""
    theme = _field_theme("lumen-companion")
    whole = (0.0, 0.0, 2 * 226.7, 56.68)
    parts = []
    for own in ((0.0, 0.0, 226.7, 56.68), (226.7, 0.0, 226.7, 56.68)):
        renderer = Renderer(theme)
        renderer.canvas = Canvas(own, whole)
        renderer.render(demo_snapshot(fixed_time=T0), T0)
        parts.append(np.asarray(renderer._light.small, np.int32))
        renderer.close()
    assert np.abs(parts[0][:, -1] - parts[1][:, 0]).max() <= 2  # (a column of light apart)


@pytest.fixture
def engine():
    found = h264.find_engine()
    if not found or not engines.draws_lights(found):
        pytest.skip("needs libre-panel-engine that draws lights (cargo build --release)")
    return found


@pytest.mark.parametrize(("half", "made"), [(False, False), (True, False), (False, True)])
def test_the_engine_lays_the_frames_in_front_of_the_light_as_python_does(
    engine, tmp_path, half, made
):
    """Lumen in front of its light through steps of the field and new readings, its layers
    laid and cut by the engine where it lays them, every seventh frame the app's own: the
    engine's frames are Python's to the bit; with the lit rings and bars the engine makes
    (``made``: to a level, as they are over any ground), to a level, at few pixels."""
    theme = _field_theme()
    renderer = Renderer(theme, dither=True)
    renderer.continuous, renderer.fps = True, 25
    for what in ("views", "globes", "laid", "fans", "masks", "lights"):
        setattr(renderer, f"native_{what}", True)
    renderer.native_rings = renderer.native_bars = made
    renderer.fills = True
    out = tmp_path / "frames.rgb"
    proc = subprocess.Popen(
        [engine, "encode", "--size", "1920x720", "--turn", "0", "--fps", "25", "--crf", "20",
         "--kbit", "4000", "--keyint", "250", "--dump-rgb", str(out)],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
    )  # fmt: skip
    sender = engines.SceneSender(made, True, True, True, True, True, made, True, True, True)
    own, lights = [], set()
    try:
        snap = demo_snapshot(fixed_time=T0)
        for n in range(40):
            now = T0 + n / 25
            if n % 25 == 0:
                snap = demo_snapshot(fixed_time=T0 + n // 25)
                renderer.new_sample(now, 1.0)
            frame, _ = renderer.render(snap, now, scene=True)
            assert isinstance(frame, scenes.SceneFrame), n
            lights.add(id(frame.scene.ground))
            mine = scenes.compose(frame.scene)
            if half:
                mine, frame = mine.transpose(Image.Transpose.ROTATE_180), frame.turned()
            if n % 7 == 3:
                h264.engine_frame(mine, None, n)(proc.stdin)
            else:
                for part in sender.records(frame.scene, n, None):
                    proc.stdin.write(part)
            own.append(mine.tobytes())
    finally:
        proc.stdin.close()
        proc.wait(60)
        renderer.close()
    assert len(lights) >= 7  # (the light moved on, step by step)
    frames = out.read_bytes()
    size = 1920 * 720 * 3
    assert len(frames) == size * len(own)
    for n, mine in enumerate(own):
        got = frames[n * size : (n + 1) * size]
        if got == mine:
            continue
        diff = ImageChops.difference(Image.frombytes("RGB", (1920, 720), got),
                                     Image.frombytes("RGB", (1920, 720), mine))  # fmt: skip
        apart = diff.convert("L").point(lambda v: v > 0 and 255).histogram()[255]
        if not made or max(high for _, high in diff.getextrema()) > 1 or apart > 200:
            pytest.fail(f"frame {n}: {diff.getbbox()} {diff.getextrema()} at {apart} pixels")


def test_a_frame_with_frosted_glass_lays_what_shows_beneath_it_blurred():
    """A piece over frosted glass blurs what shows beneath it at that moment: the light and
    what lies in front of it; the frame as it would be with that blur laid there."""
    light = field.light((120, 60), (0, 0, 60, 30), (0, 0, 60, 30), 5.0, 0.5)
    card = Piece(_rgba((30, 40, 50, 160), (60, 30)), 10, 10)
    glass = Piece(_rgba((255, 255, 255, 40), (50, 26)), 20, 14)
    glass.box = (20, 14, 50, 26)
    glass.backdrop = Image.new("L", (50, 26), 255)
    glass.backdrop_radius = 6
    frame = field.compose(light, [("card", card), ("glass", glass)])
    beneath = field.compose(light, [("card", card)])
    blurred = beneath.crop((20, 14, 70, 40)).filter(ImageFilter.GaussianBlur(6))
    want = beneath.copy()
    want.paste(blurred, (20, 14))
    plain = Piece(copy.copy(glass.layer), 20, 14)
    fore = field.Foreground((120, 60))
    rgba = np.asarray(want.convert("RGBA"))
    fore.solid(rgba[:, :, :3], 0, 0)
    field.lay(fore, plain, (0, 0), np.asarray(light.picture))
    assert frame.tobytes() == Image.fromarray(fore.on(np.asarray(light.picture))).tobytes()

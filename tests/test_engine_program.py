"""Libre Panel's engine (engine/) as a program of its own, without an app: its tables of
Pillow's conversion, and the YUV it makes of the frames it is given, byte for byte as Pillow
defines it (BT.601 YCbCr, the colours of each two by two their average, in the panel's range,
turned into the panel's framebuffer). What the Rust app makes of the engine is
test_rust_app.py's (a simulated panel the engine has); the engine's own parts are its cargo
tests'."""

from __future__ import annotations

import random
import re
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
# the panel's limited range: light 16-235, colours 16-240 about 128 (Pillow's YCbCr is JPEG's,
# full range)
LIMITED_Y = [16 + round(v * 219 / 255) for v in range(256)]
LIMITED_C = [128 + round((v - 128) * 224 / 255) for v in range(256)]
TURNS = {270: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180}
# what x264 is told for drawn frames, as the app tells the engine (rust/devices turzx.rs)
DRAWN_X264 = "no-deblock=1:subme=0:deadzone-inter=6:deadzone-intra=6"


@pytest.fixture
def engine() -> str:
    from test_rust_app import _engine

    found = _engine()
    if not found:
        pytest.skip("needs libre-panel-engine (cargo build --release in engine/)")
    return found


def test_the_engine_s_tables_are_pillow_s_conversion() -> None:
    """engine/src/tables.rs (tools/engine_tables.py): Pillow's RGB to YCbCr on a million
    colours, and the tables of the limited range."""
    text = (ROOT / "engine" / "src" / "tables.rs").read_text()
    found = {
        name: np.array([int(x) for x in values.split(",") if x.strip()], np.int64)
        for name, values in re.findall(r"pub const (\w+): \[\w+; 256\] = \[([^\]]*)\];", text)
    }
    found["CR_R"] = found["CB_B"]
    sys.path.insert(0, str(ROOT / "tools"))
    import engine_tables

    tables = {name: found[name.upper()] for name in (*engine_tables.NAMES, "Cr_R")}
    rgb = np.random.default_rng(5).integers(0, 256, (1 << 20, 3), np.uint8)
    pillow = np.asarray(Image.fromarray(rgb.reshape(1024, 1024, 3), "RGB").convert("YCbCr"))
    assert np.array_equal(engine_tables.converted(tables, rgb), pillow.reshape(-1, 3))
    assert list(found["LIMITED_Y"]) == LIMITED_Y and list(found["LIMITED_C"]) == LIMITED_C
    assert (engine_tables.LIMITED_Y, engine_tables.LIMITED_C) == (LIMITED_Y, LIMITED_C)


def _yuv(frame: Image.Image, rotate: int | None, full: bool) -> bytes:
    """The YUV 4:2:0 of ``frame`` as Pillow defines it: turned, its YCbCr, the colours of each
    two by two their average (Image.reduce), in the panel's range."""
    turn = TURNS.get(rotate)  # type: ignore[arg-type]
    light, blue, red = (frame.transpose(turn) if turn else frame).convert("YCbCr").split()
    blue, red = blue.reduce(2), red.reduce(2)
    if not full:
        light, blue, red = light.point(LIMITED_Y), blue.point(LIMITED_C), red.point(LIMITED_C)
    return light.tobytes() + blue.tobytes() + red.tobytes()


def _record(
    frame: Image.Image, boxes: list[tuple[int, int, int, int]] | None, number: int
) -> bytes:
    """A frame as the engine takes it (engine/src/wire.rs, kind 1): the boxes that may have
    changed (None: all of it), grown to whole blocks of 16 (or 8), each with its pixels."""
    width, height = frame.size
    block = next((b for b in (16, 8) if width % b == 0 and height % b == 0), 1)
    grown = []
    for x0, y0, x1, y1 in boxes if boxes is not None else [(0, 0, width, height)]:
        x0, y0 = max(0, x0 - x0 % block), max(0, y0 - y0 % block)
        x1, y1 = min(width, x1 + (-x1) % block), min(height, y1 + (-y1) % block)
        if x1 > x0 and y1 > y0:
            grown.append((x0, y0, x1, y1))
    parts = [b"LPE1" + bytes((1, 0, 0, 0)) + struct.pack("<II", number, len(grown))]
    for box in grown:
        parts.append(struct.pack("<4H", *box) + frame.crop(box).tobytes())
    return b"".join(parts)


def _frames(size: tuple[int, int], count: int, seed: int):
    """A frame of rectangles, then ``count`` frames each with a few changed (and now and then
    a box said to change that did not), with the boxes that may have changed."""
    rng = random.Random(seed)
    image = Image.new("RGB", size, (10, 14, 19))
    draw = ImageDraw.Draw(image)
    for _ in range(30):
        x, y = rng.randrange(size[0]), rng.randrange(size[1])
        draw.rectangle([x, y, x + rng.randrange(2, 90), y + rng.randrange(2, 60)],
                       fill=tuple(rng.randrange(256) for _ in range(3)))  # fmt: skip
    yield image.copy(), None
    for _ in range(count):
        boxes = []
        for _ in range(rng.randrange(0, 4)):
            x0, y0 = rng.randrange(size[0] - 1), rng.randrange(size[1] - 1)
            x1 = min(size[0], x0 + rng.randrange(1, 90))
            y1 = min(size[1], y0 + rng.randrange(1, 60))
            if rng.random() < 0.8:
                ImageDraw.Draw(image).rectangle(
                    [x0, y0, x1 - 1, y1 - 1], fill=tuple(rng.randrange(256) for _ in range(3))
                )
            boxes.append((x0, y0, x1, y1))
        yield image.copy(), boxes


@pytest.mark.parametrize(("size", "rotate", "full"), [
    ((1920, 720), 270, False), ((1920, 480), 270, True), ((480, 1920), 180, False),
    ((1024, 600), 270, False), ((320, 240), None, False),
])  # fmt: skip
def test_the_engine_makes_the_yuv_pillow_defines(engine, tmp_path, size, rotate, full) -> None:
    """Byte for byte, frame by frame, of the boxes it is told changed: Pillow's YCbCr, the
    colours' averages, the panel's range, turned; blocks of 16 and of 8."""
    dump = tmp_path / "yuv"
    command = [engine, "encode", "--size", f"{size[0]}x{size[1]}", "--turn", str(rotate or 0),
               "--fps", "25", "--crf", "20", "--kbit", "2000", "--keyint", "250", "--preset",
               "superfast", "--profile", "high", "--x264", DRAWN_X264,
               *(["--full"] if full else []), "--dump-yuv", str(dump)]  # fmt: skip
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
    assert process.stdin is not None
    expected = []
    for n, (frame, boxes) in enumerate(_frames(size, 12, sum(size))):
        expected.append(_yuv(frame, rotate, full))
        process.stdin.write(_record(frame, boxes, n))
    process.stdin.close()
    assert process.wait(timeout=60) == 0
    data = dump.read_bytes()
    each = len(expected[0])
    assert len(data) == each * len(expected)
    for n, want in enumerate(expected):
        assert data[n * each : (n + 1) * each] == want, n


def test_the_engine_s_colours_are_swscale_s(engine, tmp_path) -> None:
    """Against ffmpeg's own conversion (swscale, BT.601, limited range), of the YUV the engine
    makes: the light within one level, the colours far within what an encoder changes."""
    import shutil

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("needs ffmpeg")
    rng = random.Random(3)  # (rectangles on a dark ground, as a theme's frame has them)
    frame = Image.new("RGB", (64, 32), (10, 14, 19))
    for _ in range(12):
        x, y = rng.randrange(64), rng.randrange(32)
        colour = tuple(rng.randrange(256) for _ in range(3))
        ImageDraw.Draw(frame).rectangle(
            [x, y, x + rng.randrange(4, 30), y + rng.randrange(4, 30)], fill=colour
        )
    dump = tmp_path / "yuv"
    command = [engine, "encode", "--size", "64x32", "--turn", "0", "--fps", "25", "--crf", "20",
               "--kbit", "2000", "--keyint", "250", "--preset", "superfast", "--profile", "high",
               "--x264", DRAWN_X264, "--dump-yuv", str(dump)]  # fmt: skip
    subprocess.run(command, input=_record(frame, None, 0), stdout=subprocess.DEVNULL, check=True,
                   timeout=60)  # fmt: skip
    theirs = np.frombuffer(subprocess.run(
        [ffmpeg, "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "64x32", "-i", "-",
         "-pix_fmt", "yuv420p", "-f", "rawvideo", "-"],
        input=frame.tobytes(), capture_output=True, check=True,
    ).stdout, np.uint8)  # fmt: skip
    ours = np.frombuffer(dump.read_bytes(), np.uint8)
    assert ours.size == theirs.size == 64 * 32 * 3 // 2
    light = np.abs(ours[: 64 * 32].astype(int) - theirs[: 64 * 32].astype(int))
    assert light.max() <= 1
    colours = np.abs(ours[64 * 32 :].astype(int) - theirs[64 * 32 :].astype(int))
    assert colours.mean() < 1.5

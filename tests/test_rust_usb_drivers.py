"""The drivers of the USB screens of other makers in Rust (rust/devices: udisp ...) as those
of the Python app's devices/ drove them, to the byte: each opened on a screen the test scripts
(its product string, its interfaces, what it answers each read), frames shown, the brightness
set, the JPEGs' layout changed, closed; what went to the screen, in order, and what the driver
says of itself. What the Python drivers did is kept (golden.py): lp-check's answers are checked
against it."""

from __future__ import annotations

import base64
import random
from typing import Any

import pytest
from PIL import Image
from rust_check import image, noise, run

pytestmark = pytest.mark.kept  # (what the Python drivers did: tests/golden)

# what the cases take from the drivers they were written against
TROFEO_MAGIC = b"\xda\xdb\xdc\xdd"  # (thermalright.py: what a Trofeo's answer begins with)
TROFEO_HID, TROFEO_BULK = 0x5302, 0x5408
KRAKEN_Z3, KRAKEN_2023 = 0x3008, 0x300E
# a ZC-360 controller's states, as it says them (DES under the key jonsbo_zc360.py knew)
ZC360_SAYS = {0x10: "e3de5a33bac84332", 0x62: "216c53d74131cd90", 0x60: "4a54915bdc1c9b3b",
              0x59: "234760717afc499a"}  # fmt: skip
# the reports ms9132.py started the chip with: 24, then its EDID four bytes at a time
MS9132_START: list[int | None] = [None] * 24 + list(range(0, 0x80, 4))


def _b64(data: bytes) -> str:
    return base64.b64encode(bytes(data)).decode("ascii")


def _rust_case(case: dict[str, Any]) -> dict[str, Any]:
    steps = [{**s, "show": image(s["show"])} if "show" in s else s for s in case["steps"]]
    return {"op": "usb_session", **case, "steps": steps}


def _sessions(cases: list[dict[str, Any]]) -> int:
    """Each case's session as the Rust driver leads it; how many opened."""
    answers = run([_rust_case(c) for c in cases])
    return sum("told" in got for got in answers)


def _frame(rng: random.Random, sizes: list[tuple[int, int]]) -> Image.Image:
    size = rng.choice(sizes)
    mode = rng.choice(["RGB", "RGB", "RGBA", "L"])
    if rng.random() < 0.3:
        return noise(rng, mode, size)
    out = Image.linear_gradient("L").resize(size).convert(mode)
    tint = Image.new(mode, size, tuple(rng.randrange(256) for _ in mode) if mode != "L" else 99)
    return Image.blend(out, tint, rng.random())


def _udisp_case(rng: random.Random) -> dict[str, Any]:
    product = rng.choice([
        "t113udisp1_R480x480_Ejpg5_Ergb16_Ergb32",
        "esp32s3_R480x480_Ergb16_Ejpg8_Fps20",
        "esp32s3_R480x480_Ergb32_Fps60",
        "esp32s3_R480x480_Ergb24",
        "esp32s3_R480x480_Ejpg5_Bl20",
        "esp32s3_R480x480_Ejpg5_Bl1",
        "esp32s3_R480x480",
        "esp32s3_R320x240_Ejpg5",
        "",
    ])  # fmt: skip
    endpoints = rng.choice([[[0x01, 512, False]], [[0x02, 64, False], [0x81, 64, False]],
                            [[0x81, 512, False]]])  # fmt: skip
    steps: list[dict[str, Any]] = []
    for _ in range(rng.randrange(1, 5)):
        roll = rng.random()
        if roll < 0.25:
            steps.append({"brightness": rng.choice([-5, 0, 37, 100, 130, rng.randrange(100)])})
        elif roll < 0.35:
            steps.append({"jpeg": rng.choice(["4:2:0", "4:4:4", "4:1:1"])})
        else:
            frame = _frame(rng, [(480, 480), (320, 240), (240, 320), (1, 1), (481, 479)])
            steps.append({"show": frame, "region": None})
    options = {"jpeg": rng.choice(["4:4:4", "4:2:0", "nope"]),
               "jpeg_quality": rng.choice([50, 75, 90, 100]),
               "fps": rng.choice([10, 30, 60])}  # fmt: skip
    fail = [[rng.randrange(1, 4), 32, "Pipe error"]] if rng.random() < 0.15 else []
    return {"driver": "udisp", "model": rng.choice([None, "waveshare-2.8-udisp"]),
            "product": product, "interfaces": [{"number": 0, "class": 255,
                                                 "endpoints": endpoints}],
            "options": options, "steps": steps, "fail_writes": fail}  # fmt: skip


def test_udisp_sessions_as_udisp_py_drives_them() -> None:
    rng = random.Random(0x0D15)
    assert _sessions([_udisp_case(rng) for _ in range(40)]) > 15


def _beada_info(rng: random.Random, code: int, size: tuple[int, int], top: int, link: int) -> bytes:
    info = bytearray(80)
    info[0:2] = rng.randrange(600, 800).to_bytes(2, "little")
    info[2], info[4], info[5], info[78] = link, rng.randrange(4), code, top
    info[70:74] = size[0].to_bytes(2, "little") + size[1].to_bytes(2, "little")
    header = (
        b"STATUS-LINK" + bytes((1, 1, 0)) + (0).to_bytes(2, "little") + (100).to_bytes(2, "little")
    )
    return header + b"\0\0" + bytes(info)


def _beada_case(rng: random.Random) -> dict[str, Any]:
    code, size = rng.choice([(17, (480, 480)), (3, (320, 480)), (99, (480, 800)), (98, (123, 45)),
                             (13, (0, 480))])  # fmt: skip
    roll = rng.random()
    if roll < 0.1:
        answers: list[Any] = [None]
    elif roll < 0.15:
        answers = [_b64(b"STATUS-LINK" + bytes(10))]
    else:
        info = _beada_info(rng, code, size, rng.choice([0, 100, 255, 37]), rng.choice([1, 2]))
        answers = [_b64(info)]
    steps: list[dict[str, Any]] = []
    for _ in range(rng.randrange(1, 4)):
        if rng.random() < 0.3:
            steps.append({"brightness": rng.choice([-5, 0, 37, 50, 100, 130])})
        else:
            steps.append({"show": _frame(rng, [size if all(size) else (8, 8), (480, 320), (9, 7)]),
                          "region": None})  # fmt: skip
    fail = [[rng.randrange(1, 7), 5, "Input/Output Error"]] if rng.random() < 0.15 else []
    return {"driver": "beada", "model": None, "product": "BeadaPanel",
            "interfaces": [{"number": 0, "class": 255,
                            "endpoints": [[0x01, 512, False], [0x02, 512, False],
                                          [0x82, 512, False]]}],
            "answers": answers, "options": {"brightness": rng.choice([0, 40, 100])},
            "steps": steps, "fail_writes": fail}  # fmt: skip


def test_beadapanel_sessions_as_beadapanel_py_drives_them() -> None:
    rng = random.Random(0xBEAD)
    assert _sessions([_beada_case(rng) for _ in range(30)]) > 10


def _thermaltake_case(rng: random.Random) -> dict[str, Any]:
    stale = [_b64(rng.randbytes(20)) for _ in range(rng.choice([0, 0, 2, 20]))]
    ok = [_b64(b"HTTP/1.1 200 OK"), _b64(b"200")]
    answers = [*stale, None, *(rng.choice([ok, ok, ok, [_b64(b"500")], [None]])), _b64(b"200")]
    endpoints = rng.choice([[[0x01, 1024, True], [0x81, 1024, True]], [[0x81, 1024, True]]])
    steps: list[dict[str, Any]] = []
    for _ in range(rng.randrange(1, 4)):
        if rng.random() < 0.3:
            steps.append({"brightness": rng.choice([0, 37, 100, 130])})
        else:
            steps.append({"show": _frame(rng, [(1480, 720), (720, 1480), (64, 48)]),
                          "region": None})  # fmt: skip
    fail = [[rng.randrange(3, 8), 19, "No such device"]] if rng.random() < 0.15 else []
    return {"driver": "thermaltake", "model": None, "product": "LCD",
            "interfaces": [{"number": 1, "class": 3, "endpoints": endpoints}],
            "answers": answers, "options": {}, "steps": steps, "fail_writes": fail}  # fmt: skip


def test_thermaltake_sessions_as_thermaltake_py_drives_them() -> None:
    rng = random.Random(0x7A7E)
    assert _sessions([_thermaltake_case(rng) for _ in range(24)]) > 8


def _lianli_case(rng: random.Random) -> dict[str, Any]:
    model = rng.choice(["lianli-hydroshift2-lcd", "lianli-hydroshift2-lcd", "lianli-universal-9.2"])
    reply = lambda level: _b64(bytes((101, 0xC8, 0, 0, 0, 0, 0, 0, level)) + rng.randbytes(4))  # noqa: E731
    answers: list[Any] = [_b64(b"stale"), None]  # (drained)
    answers += [_b64(rng.randbytes(16)) for _ in range(3)]  # 10, 123, 52
    answers += [rng.choice([None, reply(0)])]  # the PNG layer
    answers += [_b64(rng.randbytes(16))]  # 15
    size = (480, 480) if model.endswith("lcd") else (1920, 464)
    steps: list[dict[str, Any]] = []
    for _ in range(rng.randrange(1, 4)):
        if rng.random() < 0.3:
            steps.append({"brightness": rng.choice([-1, 0, 37, 100, 150])})
            answers.append(_b64(rng.randbytes(16)))
        else:
            steps.append({"show": _frame(rng, [size, (size[1], size[0]), (33, 21)]),
                          "region": None})  # fmt: skip
            level = rng.choice([None, 0, 2, 3, 4, 6])
            answers.append(None if level is None else reply(level))
            if level is not None and level > 3:  # asked with 122 until 2 or fewer wait
                answers += [_b64(bytes((122, 0, 0, 0, 0, 0, 0, 0, 5))), None,
                            _b64(bytes((122, 0, 0, 0, 0, 0, 0, 0, 1)))]  # fmt: skip
    if rng.random() < 0.1:
        answers = answers[:3]  # (it stops answering while it opens)
    fail = [[rng.randrange(2, 9), 32, "Pipe error"]] if rng.random() < 0.15 else []
    return {"driver": "lianli", "model": model, "product": "Lian Li",
            "interfaces": [{"number": 0, "class": 255,
                            "endpoints": [[0x01, 512, False], [0x81, 512, False]]}],
            "answers": answers, "options": {}, "steps": steps, "fail_writes": fail}  # fmt: skip


def test_lianli_sessions_as_lianli_usb_py_drives_them() -> None:
    rng = random.Random(0x11A1)
    assert _sessions([_lianli_case(rng) for _ in range(24)]) > 8


def _trofeo_case(rng: random.Random) -> dict[str, Any]:
    hid = rng.random() < 0.4
    steps: list[dict[str, Any]] = []
    answers: list[Any] = []
    if hid:
        said = bytearray(32)
        said[0:4], said[12] = TROFEO_MAGIC, 1
        said[5], said[4] = rng.choice([0x80, 0x80, 0x81]), rng.choice([1, 2])
        answers = rng.choice([[_b64(said)], [_b64(bytes(30)), _b64(said)], [None, _b64(said)],
                              [None, None]])  # fmt: skip
        interfaces = [{"number": 0, "class": 3, "endpoints": rng.choice(
            [[[0x01, 512, True], [0x81, 512, True]], [[0x81, 512, True]]])}]  # fmt: skip
        sizes = [(1280, 480), (480, 1280), (40, 30)]
    else:
        said = bytearray(512)
        said[0:2], said[8] = b"\x03\xff", 1
        said[20], said[22] = rng.choice([1, 3, 4, 5, 9]), rng.choice([0, 1, 3])
        answers = [rng.choice([_b64(said), _b64(said), None, _b64(bytes(30))])]
        interfaces = [{"number": 0, "class": 255,
                       "endpoints": [[0x01, 512, False], [0x81, 512, False]]}]  # fmt: skip
        sizes = [(1920, 462), (1920, 440), (462, 1920), (50, 20)]
    for _ in range(rng.randrange(1, 8)):
        if rng.random() < 0.25:
            steps.append({"brightness": rng.choice([0, 33, 100, 140])})
        else:
            steps.append({"show": _frame(rng, sizes), "region": None})
        if not hid:  # (the bar's answer to each picture)
            took = bytes((3, 0xFF, 0, 0, 0, 0, 0, 0, 1))
            answers.append(rng.choice([_b64(took), _b64(took), None]))
    fail = [[rng.randrange(2, 6), 19, "No such device"]] if rng.random() < 0.15 else []
    return {"driver": "thermalright", "model": None, "product": "Trofeo",
            "product_id": TROFEO_HID if hid else TROFEO_BULK,
            "interfaces": interfaces, "answers": answers, "options": {}, "steps": steps,
            "fail_writes": fail}  # fmt: skip


def test_trofeo_sessions_as_thermalright_py_drives_them() -> None:
    rng = random.Random(0x7709)
    assert _sessions([_trofeo_case(rng) for _ in range(40)]) > 12


def _kraken_answer(asked: tuple[int, int], **at: int) -> str:
    answer = bytearray(64)
    answer[0], answer[1] = asked[0] + 1, asked[1]
    for place, value in at.items():
        answer[int(place[1:])] = value
    return _b64(answer)


def _kraken_case(rng: random.Random) -> dict[str, Any]:
    product, firmware = rng.choice([(KRAKEN_Z3, 1), (KRAKEN_2023, 2),
                                    (KRAKEN_2023, 1), (0x3014, 1)])  # fmt: skip
    size = {KRAKEN_Z3: 320}.get(product, 240)
    direct = product == KRAKEN_2023 and firmware == 2
    status = _b64(bytes((0x75, 0x02)) + rng.randbytes(30))  # (the cooler's own report)

    def asked(report: tuple[int, int], **at: int) -> list[str]:
        return ([status] if rng.random() < 0.2 else []) + [_kraken_answer(report, **at)]

    answers: list[Any] = [status] * rng.randrange(3) + [None]  # (drained)
    answers += asked((0x10, 0x01), b17=firmware, b18=rng.randrange(9), b19=rng.randrange(9))
    answers += asked((0x30, 0x01), b26=rng.randrange(8))
    if not direct:
        answers += asked((0x38, 0x01)) + asked((0x32, 0x02)) + asked((0x32, 0x02))
    steps: list[dict[str, Any]] = []
    for _ in range(rng.randrange(1, 4)):
        if rng.random() < 0.3:
            steps.append({"brightness": rng.choice([-3, 0, 45, 100, 120])})
            continue
        steps.append({"show": _frame(rng, [(size, size), (64, 48), (size + 3, size)]),
                      "region": None})  # fmt: skip
        if direct:
            times = 2 if not any("show" in s for s in steps[:-1]) else 1
            answers += [rng.choice([status, None]) for _ in range(2 * times)]
        else:
            taken = rng.choice([1, 1, 0])
            answers += [rng.choice([status, None])]  # 36 03
            answers += asked((0x32, 0x02), b14=taken)
            if taken:
                answers += asked((0x32, 0x02), b14=0)
            answers += asked((0x32, 0x01), b14=rng.choice([1, 0]))
            answers += [rng.choice([status, None])]  # 36 01
            answers += asked((0x38, 0x01), b14=1)
    fail = [[rng.randrange(4, 12), 19, "No such device"]] if rng.random() < 0.15 else []
    endpoints = rng.choice([[[0x01, 64, True], [0x81, 64, True]], [[0x81, 64, True]]])
    return {"driver": "nzxt", "model": None, "product": "Kraken", "product_id": product,
            "interfaces": [{"number": 0, "class": 255, "endpoints": [[0x02, 512, False]]},
                           {"number": 1, "class": 3, "endpoints": endpoints}],
            "answers": answers, "options": {"brightness": rng.choice([0, 60, 100])},
            "steps": steps, "fail_writes": fail}  # fmt: skip


def test_kraken_sessions_as_nzxt_kraken_py_drives_them() -> None:
    rng = random.Random(0x2A3)
    assert _sessions([_kraken_case(rng) for _ in range(24)]) > 10


def _zc360_answer(state: int) -> str:
    """A controller's answer saying ``state``."""
    answer = bytearray(32)
    answer[2:10] = bytes.fromhex(ZC360_SAYS[state])
    return _b64(answer)


def _zc360_case(rng: random.Random) -> dict[str, Any]:
    single = rng.random() < 0.35
    count = rng.choice([3, 3, 3, 4, 2]) if not single else rng.choice([1, 2, 3])
    steps: list[dict[str, Any]] = []
    for _ in range(rng.randrange(1, 4)):
        if rng.random() < 0.25:
            steps.append({"brightness": rng.choice([-1, 0, 50, 100, 120])})
        else:
            sizes = (
                [(640, 180), (180, 640), (300, 90)]
                if single
                else [(1920, 180), (180, 1920), (960, 90)]
            )
            frame = _frame(rng, sizes)
            steps.append({"show": frame, "region": None})
            if rng.random() < 0.3:
                steps.append({"show": frame, "region": None})  # (the same again: not written)
    states = [0x62, 0x62, 0x60, 0x59, None]
    screens = []
    for n in range(count):
        answers = [rng.choice([_zc360_answer(0x10), None]) for _ in range(3)]
        answers += [None if (state := rng.choice(states)) is None else _zc360_answer(state)
                    for _ in range(40)]  # fmt: skip
        fail = [[rng.randrange(1, 60), 19, "No such device"]] if rng.random() < 0.08 else []
        screens.append({"product": "TURZX-338inch-r", "serial": rng.choice(["A1", "B2", "C3", ""]),
                        "address": 10 + n, "ports": [1, rng.randrange(1, 5)],
                        "interfaces": [{"number": 0, "class": 255,
                                        "endpoints": [[0x02, 512, False], [0x82, 512, False]]}],
                        "answers": answers, "fail_writes": fail})  # fmt: skip
    port = rng.choice(["", "2,1,3", "312", "1", "3", "4", "nope"])
    return {"driver": "zc360", "product": "ZC-360",
            "model": "jonsbo-zc360-screen" if single else rng.choice([None, "jonsbo-zc360"]),
            "screens": screens, "options": {"brightness": rng.choice([0, 70, 100]), "port": port},
            "steps": steps}  # fmt: skip


def test_zc360_sessions_as_jonsbo_zc360_py_drives_them() -> None:
    rng = random.Random(0x2C36)
    assert _sessions([_zc360_case(rng) for _ in range(30)]) > 12


def _edid(size: tuple[int, int] | None) -> bytes:
    edid = bytearray(128)
    if size is None:
        return bytes(edid)
    edid[:8] = b"\x00\xff\xff\xff\xff\xff\xff\x00"
    width, height = size
    edid[0x38], edid[0x3A] = width & 0xFF, (width >> 8) << 4
    edid[0x3B], edid[0x3D] = height & 0xFF, (height >> 8) << 4
    return bytes(edid)


def _ms9132_case(rng: random.Random) -> dict[str, Any]:
    size = rng.choice([(376, 960), (320, 960), (480, 480), (123, 456), None])
    edid = _edid(size)
    answers: list[Any] = []
    for at in MS9132_START:
        data = rng.randbytes(5)
        if at is not None:
            data = edid[at : at + 4] + b"\0"
        answers.append(_b64(rng.randbytes(3) + data))
    if rng.random() < 0.1:
        answers[rng.randrange(len(answers))] = None  # (a report the chip does not answer)
    answers += [_b64(bytes(8)) if rng.random() < 0.95 else None for _ in range(60)]
    model = rng.choice([None, "jonsbo-ds339"])
    shape = size if size in ((376, 960), (320, 960), (480, 480)) else (376, 960)
    steps: list[dict[str, Any]] = []
    for _ in range(rng.randrange(1, 5)):
        if rng.random() < 0.25:
            steps.append({"brightness": rng.choice([0, 40, 100, 130])})
        else:
            steps.append({"show": _frame(rng, [shape, (shape[1], shape[0]), (50, 40)]),
                          "region": None})  # fmt: skip
    fail = [[rng.randrange(1, 40), 19, "No such device"]] if rng.random() < 0.15 else []
    return {"driver": "ms9132", "model": model, "product": "MS9132",
            "interfaces": [{"number": 0, "class": 3, "endpoints": [[0x81, 8, True]]},
                           {"number": 3, "class": 255, "endpoints": [[0x04, 512, False]]}],
            "answers": answers, "options": {}, "steps": steps, "fail_writes": fail}  # fmt: skip


def test_ms9132_sessions_as_ms9132_py_drives_them() -> None:
    rng = random.Random(0x9132)
    assert _sessions([_ms9132_case(rng) for _ in range(24)]) > 10

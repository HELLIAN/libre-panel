"""The serial panels' drivers in Rust (rust/devices' serial_panel: Turing rev. A, XuanFang rev. B,
Kipye rev. D, WeAct) as those of the Python app's devices/ drove them, to the byte: each opened
on a port the test scripts (what the panel answers, read as the driver asks for it), frames
shown whole and in parts, of the panel's size and of another, turned, the brightness set,
closed; what went to the port, in order, and what the driver says of itself. What the Python
drivers did is kept (golden.py): lp-check's answers are checked against it."""

from __future__ import annotations

import random
from typing import Any

import pytest
from rust_check import image, run
from test_rust_usb_drivers import _b64, _frame

pytestmark = pytest.mark.kept  # (what the Python drivers did: tests/golden)

DRIVERS = ("rev_a", "rev_b", "rev_d", "weact", "ds916")  # (and rev_c, with cases of its own)


def _rust_case(case: dict[str, Any]) -> dict[str, Any]:
    steps = [{**s, "show": image(s["show"])} if "show" in s else s for s in case["steps"]]
    return {"op": "usb_session", **case, "steps": steps}


def _sessions(cases: list[dict[str, Any]]) -> int:
    """Each case's session as the Rust driver leads it; how many opened."""
    answers = run([_rust_case(c) for c in cases])
    return sum("told" in got for got in answers)


SIZES = {"turing-3.5": (320, 480), "usbpcmonitor-5": (480, 800), "usbpcmonitor-7": (600, 1024),
         "xuanfang-3.5": (320, 480), "kipye-3.5": (320, 480), "weact-3.5": (320, 480),
         "weact-0.96": (80, 160), "jonsbo-ds916": (462, 1920)}  # fmt: skip


def _case(rng: random.Random, driver: str) -> dict[str, Any]:
    port_model, answers = {
        "rev_a": (rng.choice(["turing-3.5", "usbpcmonitor-5"]),
                  [b"", bytes([1] * 6), bytes([2] * 6), bytes([3] * 6), b"\x07\x01"]),
        "rev_b": ("xuanfang-3.5", [b"", b"\xcaHELLO\x0a\x01\x00\xca", b"\xcaHELLO\x0a\x11\x00\xca",
                                   b"\xcaHELLO\x0a\x12\x00\xca", b"\x01\x02\x03"]),
        "rev_d": ("kipye-3.5", [b"", rng.randbytes(40)]),
        "weact": (rng.choice(["weact-3.5", "weact-0.96"]), [b"", rng.randbytes(19)]),
        "ds916": ("jonsbo-ds916", [b"", b"VMAXB160462*1920S0123456789",
                                   b"\0VMAXB16 0240*0320 S12\r\n", b"garbage" * 4,
                                   b"x" * 26 + b"VMAXB160480*1920S01234567",
                                   b"V\xc3\xa9MAXB0462*1920S"]),
    }[driver]  # fmt: skip
    configured = rng.random() < 0.5
    model = port_model if configured else None
    serial_number = rng.choice([None, "AB1234", "AD99", "XX"]) if driver == "weact" else None
    w, h = SIZES[port_model]
    steps: list[dict[str, Any]] = []
    for _ in range(rng.randrange(1, 5)):
        roll = rng.random()
        if roll < 0.25:
            steps.append({"brightness": rng.choice([-10, 0, 1, 37, 99, 100, 150])})
            continue
        size = rng.choice([(w, h), (h, w), (w, h), (w // 2 + 1, h // 3 + 1)])
        region = None
        if roll < 0.7:
            x0, y0 = rng.randrange(size[0]), rng.randrange(size[1])
            region = [x0, y0, rng.randrange(x0, size[0] + 1), rng.randrange(y0, size[1] + 1)]
        steps.append({"show": _frame(rng, [size]), "region": region})
    fail = [[rng.randrange(1, 12), "write timeout"]] if rng.random() < 0.15 else []
    return {"driver": driver, "model": model, "configured": configured,
            "found": {"device": "COM9", "model": port_model, "serial": serial_number},
            "incoming": _b64(rng.choice(answers)), "steps": steps,
            "fail_writes": fail}  # fmt: skip


@pytest.mark.parametrize("driver", DRIVERS)
def test_serial_sessions_as_the_python_drivers_drive_them(driver: str) -> None:
    rng = random.Random(f"serial-{driver}")
    assert _sessions([_case(rng, driver) for _ in range(20)]) > 5


def _rev_c_case(rng: random.Random) -> dict[str, Any]:
    model = rng.choice(["turing-2.1", "turing-5", "turing-8.8"])
    configured = rng.random() < 0.6
    hello = rng.choice([b"chs_5inch.dev1_rom1.87\0", b"chs_88inch.dev1_rom1.90",
                        b"\x01status:x|" + b"chs_21inch.dev1_rom1.88",
                        b"needReSend:0|renderCnt:3 " * 2 + b"chs_5inch.dev1_rom1.95",
                        b"nothing at all"])  # fmt: skip
    statuses = [rng.choice([b"needReSend:0|renderCnt:0", b"needReSend:1|renderCnt:7", b""])
                .ljust(rng.choice([0, 30, 1024]), b"\0") for _ in range(12)]  # fmt: skip
    w, h = {"turing-2.1": (480, 480), "turing-5": (480, 800), "turing-8.8": (480, 1920)}[model]
    steps: list[dict[str, Any]] = []
    for _ in range(rng.randrange(1, 5)):
        roll = rng.random()
        if roll < 0.2:
            steps.append({"brightness": rng.choice([0, 50, 100])})
            continue
        size = rng.choice([(w, h), (h, w)])
        region = None
        if roll < 0.75:
            x0, y0 = rng.randrange(size[0] - 1), rng.randrange(size[1] - 1)
            region = [x0, y0, min(size[0], x0 + rng.randrange(1, 90)),
                      min(size[1], y0 + rng.randrange(1, 90))]  # fmt: skip
        steps.append({"show": _frame(rng, [size]), "region": region})
    ports = [
        {"device": "/dev/ttyS0", "vid": None, "pid": None, "serial": None},
        {"device": "/dev/ttyACM3", "vid": 0x1D6B, "pid": 0x0121, "serial": "20080411"},
    ]
    fail = [[rng.randrange(2, 20), "write timeout"]] if rng.random() < 0.1 else []
    return {"driver": "rev_c", "model": model if configured else None, "configured": configured,
            "found": {"device": "/dev/ttyACM3", "model": "turing-5"}, "ports": ports,
            "incoming": _b64(hello + b"".join(statuses)), "steps": steps,
            "fail_writes": fail}  # fmt: skip


def test_rev_c_sessions_as_turing_rev_c_py_drives_them() -> None:
    rng = random.Random(0x7C)
    assert _sessions([_rev_c_case(rng) for _ in range(30)]) > 10

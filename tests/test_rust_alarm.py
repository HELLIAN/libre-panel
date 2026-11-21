"""rust/render's alarm edge against Python's (docs/RUST.md): render/alarm.py's AlarmEdge led
through scripts of the alarm's reading, frames drawn with the edge pulsing (or still), whether
it shows and how many masks it keeps (they go when it is over), every step the same; where
Python's raised (a colour Pillow cannot read), Rust's fails too. Python's answers are kept
(golden.py): lp-check's are checked against them."""

from __future__ import annotations

import itertools
import random
import struct
from typing import Any

import pytest
from rust_check import run

pytestmark = pytest.mark.kept  # (what Python answered: tests/golden)

SECONDS = 3.0  # (how long the edge pulses: render/alarm.py's, lp-render's alarm.rs)

SIZES = [(1920, 480), (480, 1920), (800, 480), (480, 480), (320, 240), (64, 36), (7, 5), (2, 9),
         (1, 1), (3, 3), (1920, 462)]  # fmt: skip
COLOURS = [None, None, None, "", "#ff0000", "red", "#12345678", "hsl(0, 100%, 50%)", "#FF3B5C",
           "#abc", "nonsense"]  # fmt: skip
MODES = ["RGB"] * 6 + ["RGBA", "L"]


def _bits(x: float) -> str:
    return struct.pack(">d", x).hex()


def _script(rng: random.Random) -> list[list[Any]]:
    """The alarm going on and off, frames drawn at its times (a second size and a step back
    in time now and then), and whether it shows."""
    size, mode = rng.choice(SIZES), rng.choice(MODES)
    colour, still = rng.choice(COLOURS), rng.random() < 0.3
    now, script = rng.uniform(-5, 1000), []
    for _ in range(rng.randrange(4, 40)):
        pick = rng.random()
        if pick < 0.25:
            script.append(["follow", rng.random() < 0.6, _bits(now)])
        elif pick < 0.35:
            script.append(["showing", _bits(now + rng.choice([0.0, SECONDS, -0.001]))])
        else:
            if rng.random() < 0.05:
                size = rng.choice(SIZES)
            frame = {"size": list(size), "mode": mode, "seed": rng.randrange(1000)}
            script.append(["draw", frame, _bits(now), colour, still])
        now += rng.choice([0.0, rng.uniform(0, 0.4), rng.uniform(0, 1.5), -rng.uniform(0, 0.2)])
    return script


def _a_whole_pulse() -> list[list[Any]]:
    """The alarm on at 10 s and drawn every 1/20 s from just before until just after its 3 s,
    moving and still (at 13 s exactly it is over: what it kept goes, and a frame of the clock
    put back shows none); then on again while it shows (it goes on), off, and on once it ended
    (a new edge)."""
    size = (800, 480)
    script: list[list[Any]] = [["follow", False, _bits(9.0)], ["follow", True, _bits(10.0)]]
    for k in range(-2, 66):
        at = 10.0 + k * 0.05
        script.append(["draw", {"size": list(size), "mode": "RGB", "seed": 10 + k}, _bits(at),
                       None, k % 7 == 0])  # fmt: skip
        script.append(["showing", _bits(at)])
        if k == 60:  # (13 s exactly, then the clock put back)
            script.append(["draw", {"size": list(size), "mode": "RGB", "seed": 9}, _bits(12.9),
                           None, False])  # fmt: skip

    def frame(seed: int, at: float, colour: str | None = None, of: Any = size) -> list[Any]:
        return ["draw", {"size": list(of), "mode": "RGB", "seed": seed}, _bits(at), colour, False]

    script += [["follow", True, _bits(11.0)], ["follow", False, _bits(11.5)],
               ["follow", True, _bits(12.0)], frame(1, 12.5, "#00ff00"), frame(2, 13.0),
               frame(3, 13.5, of=(480, 800)), ["showing", _bits(15.0)], frame(4, 15.0),
               ["follow", True, _bits(16.0)], frame(5, 16.2)]  # fmt: skip
    return script


def test_the_alarm_edge_as_python_draws_it() -> None:
    rng = random.Random(0xA1A7)
    scripts = [_script(rng) for _ in range(150)] + [_a_whole_pulse()]
    answers = run([{"op": "alarm", "script": s} for s in scripts])
    drawn = forgotten = 0
    for n, (got, script) in enumerate(zip(answers, scripts, strict=True)):
        assert len(got["steps"]) == len(script) == len(got["kept"]), n
        drawn += sum(isinstance(step, dict) and "sha256" in step for step in got["steps"])
        forgotten += sum(a > 0 and b == 0 for a, b in itertools.pairwise(got["kept"]))
    assert drawn > 800, drawn  # (930 when written)
    assert forgotten > 40, forgotten  # (63 when written)

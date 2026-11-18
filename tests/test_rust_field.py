"""The light field under a theme in Rust (rust/render/src/field.rs) as render/field.py makes
it, to the bit: its levels at an eighth of the panel and the picture drawn up, for panels of
every size where they stand in canvases of one to three panels, at moments over hours and under
every load; and that it runs on from one panel to the next."""

from __future__ import annotations

import base64
import hashlib
import random
import struct
from typing import Any

import numpy as np
from rust_check import picture, run

from libre_panel.render import field


def _bits(x: float) -> str:
    return struct.pack(">d", x).hex()


def _case(rng: random.Random) -> dict[str, Any]:
    w, h = rng.choice([(1920, 720), (1920, 480), (480, 480), (320, 170), (800, 480), (101, 37)])
    per = rng.uniform(0.06, 0.2)  # (millimetres a pixel)
    own = [rng.uniform(-300, 300), rng.uniform(-100, 100), w * per, h * per]
    left, top = own[0] - rng.uniform(0, 400) * rng.random(), own[1] - rng.uniform(0, 60)
    whole = [left, top, own[0] + own[2] - left + rng.uniform(0, 400) * rng.random(),
             own[1] + own[3] - top + rng.uniform(0, 60)]  # fmt: skip
    t = rng.choice([0.0, 1e9 + rng.uniform(0, 86400), rng.uniform(0, 2000)])
    load = rng.choice([0.0, 1.0, rng.random(), -0.5, 1.7])
    base = rng.choice([[0, 0, 0], [10, 14, 19], [rng.randrange(256) for _ in range(3)]])
    return {"size": [w, h], "own": own, "whole": whole, "t": t, "load": load, "base": base}


def _wired(case: dict[str, Any], digest: bool) -> dict[str, Any]:
    return {"op": "field", "look": "lumen", "size": case["size"], "digest": digest,
            "own": [_bits(v) for v in case["own"]], "whole": [_bits(v) for v in case["whole"]],
            "t": _bits(case["t"]), "load": _bits(case["load"]), "base": case["base"]}  # fmt: skip


def test_the_light_field_as_python_makes_it() -> None:
    rng = random.Random(0xF1E1D)
    cases = [_case(rng) for _ in range(120)]
    small = [c for c in cases if c["size"][0] * c["size"][1] < 500_000]
    got = run([_wired(c, digest=c not in small) for c in cases])
    checked = 0
    for case, answer in zip(cases, got, strict=True):
        args = (tuple(case["size"]), tuple(case["own"]), tuple(case["whole"]), case["t"],
                case["load"], "lumen", tuple(case["base"]))  # fmt: skip
        levels = field.small(*args)
        fw, fh, data = answer["small"]
        assert (fw, fh) == (levels.shape[1], levels.shape[0]), case
        assert base64.b64decode(data) == levels.tobytes(), case
        made = field.field(*args)
        if case in small:
            assert picture(answer["field"]).tobytes() == made.tobytes(), case
        else:
            assert answer["field"]["sha256"] == hashlib.sha256(made.tobytes()).hexdigest(), case
        checked += 1
    assert checked == len(cases) and len(small) > 20


def test_the_field_runs_on_from_one_panel_to_the_next() -> None:
    """Two panels as one canvas show, point by point, what one panel as large as both shows; a
    step of the field lasts 0.16 s on the wall clock, the same for every panel."""
    whole = (0.0, 0.0, 200.0, 50.0)
    left = field.small((800, 400), (0.0, 0.0, 100.0, 50.0), whole, 37.0, 0.4)
    right = field.small((800, 400), (100.0, 0.0, 100.0, 50.0), whole, 37.0, 0.4)
    both = field.small((1600, 400), whole, whole, 37.0, 0.4)
    assert np.array_equal(np.concatenate([left, right], axis=1), both)
    assert field.stepped(1.159) == 1.12 and field.stepped(1.119) == 0.96
    idle = field.small((800, 400), whole, whole, 0.0, 0.0).astype(int).sum()
    busy = field.small((800, 400), whole, whole, 0.0, 1.0).astype(int).sum()
    assert busy > idle * 1.5

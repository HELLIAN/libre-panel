"""For as long as the Python app is still here (docs/RUST.md, R12 the switch): what the
comparisons' cases take from it, asked of it once more and written down, so that tests/golden's
given/ holds it for each system. With ``LP_GIVEN_OUT`` naming a folder, golden.given and
golden.told ask the Python app (as the tests themselves did before they read what was kept) and
write what it says to <folder>/<system>/; the tests' answers are checked against the kept ones
all the same, which shows that what was written makes the cases the Python app answered. CI
keeps the folder as a file of its run. This module goes with the Python app."""

from __future__ import annotations

import base64
import io
import json
import os
import sys
from pathlib import Path
from typing import Any

import golden

OUT = os.environ.get("LP_GIVEN_OUT") or None
_tapes: dict[str, dict[str, dict[str, list[Any]]]] = {}  # by test file, test, what was asked
_made: dict[str, Any] = {}


def _folder() -> Path:
    assert OUT is not None
    folder = Path(OUT) / golden.FOLDER.name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _write(name: str, value: Any, indent: int | None = 1) -> None:
    text = json.dumps(value, indent=indent, ensure_ascii=False)
    (_folder() / f"{name}.json").write_text(text + "\n", encoding="utf-8")


# -- tables and readings ----------------------------------------------------------------------


def _looks() -> Any:
    from libre_panel import design

    return design.LOOKS


GIVERS = {"looks": _looks}


def given(name: str) -> Any:
    """golden.given, from the Python app itself: made, written, and given as it reads back."""
    if name not in _made:
        _write(name, GIVERS[name]())
        _made[name] = json.loads((_folder() / f"{name}.json").read_text(encoding="utf-8"))
    return _made[name]


# -- what it tells while cases are made -------------------------------------------------------

TELLERS: dict[str, Any] = {}


def told(what: str) -> Any:
    """golden.told, from the Python app itself: asked with what the test that asks has at hand
    (its local names), and written down by test file, test and what was asked."""
    value = TELLERS[what](sys._getframe(1).f_locals)
    value = json.loads(json.dumps(value))  # (as it reads back)
    file, name = golden._test["file"], golden._test["name"]
    tests = _tapes.setdefault(file, {})
    tests.setdefault(name, {}).setdefault(what, []).append(value)
    _write(file, dict(sorted(tests.items())), indent=None)
    return value


def install() -> None:
    golden.given = given  # type: ignore[assignment]
    golden.told = told  # type: ignore[assignment]
    golden.untold = lambda: []  # type: ignore[assignment]


def png(image: Any) -> str:
    out = io.BytesIO()
    image.save(out, "PNG", optimize=True)
    return base64.b64encode(out.getvalue()).decode("ascii")

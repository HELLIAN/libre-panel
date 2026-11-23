"""Libre Panel's own answers for the tools: lp-check, the program `cargo build --release` makes
in rust/ (LIBRE_PANEL_CHECK names another), asked one case a line, as tests/rust_check.py asks
it."""

from __future__ import annotations

import functools
import json
import os
import struct
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


def program() -> Path:
    named = os.environ.get("LIBRE_PANEL_CHECK")
    if named:
        return Path(named)
    name = "lp-check.exe" if os.name == "nt" else "lp-check"
    built = ROOT / "rust" / "target" / "release" / name
    if not built.is_file():
        raise SystemExit("needs lp-check: cargo build --release in rust/ (or LIBRE_PANEL_CHECK)")
    return built


def typed(value: Any) -> dict[str, Any]:
    """A value as lp-check takes a reading's: its type said, a float by its bits."""
    if isinstance(value, bool):
        return {"b": value}
    if isinstance(value, float):
        return {"f": struct.pack(">d", value).hex()}
    if isinstance(value, int):
        return {"i": str(value)}
    if isinstance(value, str):
        return {"s": value}
    if isinstance(value, list):
        return {"l": [typed(item) for item in value]}
    if isinstance(value, dict):
        return {"d": {str(key): typed(item) for key, item in value.items()}}
    return {"n": None}


def ask(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each case's answer, in order."""
    text = "".join(json.dumps(case) + "\n" for case in cases)
    done = subprocess.run([str(program())], input=text, capture_output=True, encoding="utf-8",
                          check=True)  # fmt: skip
    answers = [json.loads(line) for line in done.stdout.split("\n") if line]
    if len(answers) != len(cases):
        raise SystemExit(f"lp-check answered {len(answers)} of {len(cases)}: {done.stderr}")
    return answers


@functools.cache
def design() -> dict[str, Any]:
    """The design (rust/theme/src/design.rs): ``looks``, ``aliases`` (SPUR II's names for the
    roles), ``families`` (each built-in theme's, by the start of its name) and
    ``style_defaults`` (the cards where neither a look nor a theme says)."""
    (said,) = ask([{"op": "design"}])
    return json.loads(said["design"])


def family_of(theme: str) -> tuple[str, dict[str, Any]] | None:
    """The family a built-in theme belongs to, by its name (the longest that fits; None:
    none)."""
    families = design()["families"]
    fits = [name for name in families if theme == name or theme.startswith(name + "-")]
    if not fits:
        return None
    name = max(fits, key=len)
    return name, families[name]


def family_palette(family: dict[str, Any]) -> dict[str, str]:
    """The colours a family's themes wear: its look's, with its own over them."""
    return {**design()["looks"][family["look"]]["palette"], **family["colours"]}


def family_style(family: dict[str, Any]) -> dict[str, Any]:
    """The cards in full, as a theme of the family is drawn with them."""
    look = design()["looks"][family["look"]]
    return {**design()["style_defaults"], **look["style"], **family["cards"]}

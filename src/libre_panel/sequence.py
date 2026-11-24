"""Screens in turn: themes the panel shows one after another, each for as long as it says,
and from the top again after the last.

``config.toml``::

    [sequence]
    on = true
    [[sequence.steps]]
    theme = "almanac"
    minutes = 5
    [[sequence.steps]]
    theme = "nimbus"
    minutes = 5
    [[sequence.steps]]
    theme = "studio"
    minutes = 10

A step lasts its ``seconds``, ``minutes`` and ``hours`` together (``minutes = 1, seconds =
30``), at least :data:`SHORTEST_S`. The sequence is the panel's normal theme: a game's
mode, the autopilot or a service that shows a theme of its own go first, and the sequence
goes on underneath. It begins with its first step when the panel starts and whenever the
steps change (rust/config's sequence.rs); here the steps are read, as config.toml has them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SHORTEST_S = 5  # a step shorter than this would be little more than its transition
LONGEST_S = 7 * 24 * 3600
UNITS = (("hours", 3600), ("minutes", 60), ("seconds", 1))


class StepError(ValueError):
    """A step that cannot be shown: says which and why."""


@dataclass(frozen=True)
class Step:
    """A theme and how long it shows, in seconds."""

    theme: str
    seconds: int


def parse_step(raw: Any, where: str) -> Step:
    """A step of ``[[sequence.steps]]``, checked."""
    if not isinstance(raw, dict):
        raise StepError(f"{where}: expected a table with a theme and a time")
    theme = raw.get("theme")
    if not isinstance(theme, str) or not theme.strip():
        raise StepError(f"{where}: needs a theme")
    unknown = sorted(set(raw) - {"theme", *(unit for unit, _ in UNITS)})
    if unknown:
        raise StepError(f"{where}: unknown key {unknown[0]!r} (theme, seconds, minutes, hours)")
    total = 0.0
    for unit, size in UNITS:
        if unit not in raw:
            continue
        value = raw[unit]
        if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
            raise StepError(f"{where}.{unit}: expected a number of {unit}")
        total += value * size
    seconds = round(total)
    if not SHORTEST_S <= seconds <= LONGEST_S:
        raise StepError(
            f"{where}: shows {seconds} s; a step lasts {SHORTEST_S} s to {LONGEST_S // 86400} days"
        )
    return Step(theme.strip(), seconds)


def parse_steps(raw: Any, where: str = "sequence.steps") -> list[Step]:
    if not isinstance(raw, list):
        raise StepError(f"{where}: expected a list of steps ([[{where}]])")
    return [parse_step(entry, f"{where}[{n}]") for n, entry in enumerate(raw)]

"""The kinds of module a theme can have, and the cards of its card slots: their names, the
order and the change a slot shows them in, and how a line names one (a slot's ``items``,
config.toml's ``cards``: ``forecast for 5m``). Kept apart from theme/modules.py, which builds
the modules, for what reads config.toml without building any (the plugin host)."""

from __future__ import annotations

import math
from dataclasses import dataclass

MODULE_KINDS = (
    "clock", "date", "weather", "ring", "stat", "graph", "bars", "network", "system", "text",
    "temps", "cores", "drives", "processes", "netinfo", "battery", "values",
    "sun", "analog", "world", "countdown", "image", "music", "agenda", "game",
    "playback", "forecast", "download", "instrument", "cards", "netcard", "volume", "moon",
    "outlook", "ping", "timer", "power", "mic", "ups", "huge", "ai", "earth",
)  # fmt: skip
# Kinds that hold several cards and show one (render.slot); "netcard" is two: a download
# while one runs, else the network
SLOT_KINDS = ("cards", "netcard")

DEFAULT_CARDS = "playback\nai\nforecast\ndate"  # (config.DEFAULT_CARDS: the person's)
CARD_NAMES = tuple(k for k in MODULE_KINDS if k not in SLOT_KINDS) + ("net",)  # "net": the
# network as the Studio's NET section has it (UP and DOWN with the speed beside them)
# Which card a slot shows (its ``order``): the first that has something to show, or each in
# turn for its time, those with nothing to show passed over (render.slot)
CARD_ORDERS = ("first", "turns")
# How a slot goes from one card to the next (its ``transition``, render.slot): the old card
# rolls up into a line of light and the new one rolls down out of it (SPUR II's Studio), they
# fade, the new one pushes the old one out to the left or up, wipes it away from the left, or
# is there at once; "random" takes one of the moving ones, another each time
CARD_TRANSITIONS = ("light", "fade", "slide", "rise", "wipe", "cut", "random")
TURN_S = (5, 24 * 3600)  # a card's turn: the shortest and the longest, in seconds
_UNITS = {"s": 1, "sec": 1, "m": 60, "min": 60, "h": 3600}


@dataclass(frozen=True)
class SlotCard:
    """A card of a slot: its name, its rule (the conditions after ``if``, as a ``needs`` has
    them; "": the card's own) and how long its turn lasts in seconds (None: the slot's
    ``turn``)."""

    name: str
    rule: str = ""
    seconds: float | None = None


def card_time(text: str) -> float | None:
    """The time of a card's turn in seconds, as a line has it after ``for``: ``5m``, ``90s``,
    ``1h``, ``2.5 min`` (a bare number: minutes); None for what is no time, or out of
    :data:`TURN_S`."""
    text = text.strip().lower()
    number = text.rstrip("abcdefghijklmnopqrstuvwxyz").strip()
    unit = text[len(number) :].strip() or "min"
    try:
        seconds = float(number) * _UNITS[unit]
    except (KeyError, ValueError):
        return None
    if not math.isfinite(seconds) or not TURN_S[0] <= seconds <= TURN_S[1]:
        return None
    return seconds


def card_line(line: str) -> SlotCard | None:
    """A line of a slot's ``items``: ``name``, then ``for <time>`` if it has a turn of its own,
    then ``if <conditions>`` if it has a rule of its own (``forecast for 5m if
    weather.temperature``); a ``#`` begins a comment. None for a line that names no card."""
    head, _, rule = line.partition("#")[0].strip().partition(" if ")
    name, _, time = head.strip().partition(" for ")
    name = name.strip()
    if name not in CARD_NAMES:
        return None
    return SlotCard(name, rule.strip(), card_time(time) if time.strip() else None)

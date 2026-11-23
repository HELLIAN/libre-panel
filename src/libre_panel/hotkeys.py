"""Keyboard shortcuts for the whole PC as config.toml's ``[hotkeys]`` writes them,
``pause = "Ctrl+Alt+P"``: the combinations config.py checks. The Rust app takes the keys
(rust/app/src/hotkeys.rs)."""

from __future__ import annotations

from dataclasses import dataclass

from libre_panel.i18n import t

ACTIONS = (
    "pause", "brightness_up", "brightness_down", "open", "next_scene", "timer", "timer_reset",
    "mic_mute",
)  # fmt: skip

ORDER = ("Ctrl", "Alt", "Shift", "Win")  # how a combination is written
MODIFIERS = {
    "ctrl": "Ctrl", "control": "Ctrl", "strg": "Ctrl", "alt": "Alt", "shift": "Shift",
    "win": "Win", "super": "Win", "meta": "Win", "cmd": "Win",
}  # fmt: skip
NAMED = {
    "space": "Space", "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "home": "Home", "end": "End", "pageup": "PageUp", "pagedown": "PageDown",
    "insert": "Insert", "delete": "Delete", "pause": "Pause", "scrolllock": "ScrollLock",
}  # fmt: skip
ALONE = {"Pause", "ScrollLock", *(f"F{n}" for n in range(13, 25))}  # keys nothing types


@dataclass(frozen=True)
class Combo:
    mods: tuple[str, ...]  # in ORDER
    key: str  # "A" to "Z", "0" to "9", "F1" to "F24", or one of NAMED's

    def __str__(self) -> str:
        return "+".join((*self.mods, self.key))


def parse(text: str) -> Combo:
    """``"ctrl+alt+p"`` as a Combo (``Ctrl+Alt+P``); ValueError says what is wrong. A
    combination needs Ctrl, Alt or Win (Shift alone would take a letter from typing), but for
    the keys nothing types: F13 to F24, Pause, Scroll Lock."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError(t("a key combination such as Ctrl+Alt+P"))
    mods: set[str] = set()
    key = None
    for part in (p.strip() for p in text.split("+")):
        name = part.lower()
        if name in MODIFIERS:
            mods.add(MODIFIERS[name])
        elif key is not None or not part:
            raise ValueError(t("one key with Ctrl, Alt, Shift or Win: {keys}", keys=text))
        elif len(part) == 1 and part.isascii() and part.isalnum():
            key = part.upper()
        elif name[0] == "f" and name[1:].isdigit() and 1 <= int(name[1:]) <= 24:
            key = f"F{int(name[1:])}"
        elif name in NAMED:
            key = NAMED[name]
        else:
            raise ValueError(t("not a key Libre Panel can take: {part}", part=part))
    if key is None:
        raise ValueError(t("one key with Ctrl, Alt, Shift or Win: {keys}", keys=text))
    if not mods & {"Ctrl", "Alt", "Win"} and key not in ALONE:
        raise ValueError(t("{keys} needs Ctrl, Alt or Win", keys=text))
    return Combo(tuple(m for m in ORDER if m in mods), key)

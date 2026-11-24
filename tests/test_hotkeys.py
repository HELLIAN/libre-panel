"""Keyboard shortcuts for the whole PC (hotkeys.py): how a combination is written and checked,
and what config.toml takes."""

import pytest

from libre_panel.config import ConfigError, parse_config
from libre_panel.hotkeys import Combo, parse


def test_a_combination_is_written_one_way():
    assert str(parse("ctrl+alt+p")) == "Ctrl+Alt+P"
    assert str(parse("Shift + Strg + f5")) == "Ctrl+Shift+F5"
    assert str(parse("win+alt+pageup")) == "Alt+Win+PageUp"
    assert parse("F13") == Combo((), "F13") and parse("pause") == Combo((), "Pause")
    assert str(parse("Super+Space")) == "Win+Space"


@pytest.mark.parametrize(
    "text",
    ["", "P", "Shift+P", "Ctrl", "Ctrl+Alt", "Ctrl+P+Q", "Ctrl+Ä", "Ctrl+F25", "Ctrl+Tab", 5],
)
def test_what_is_no_shortcut_is_said(text):
    with pytest.raises(ValueError):
        parse(text)


def test_config_toml_takes_them_written_one_way_and_each_once():
    config = parse_config({"hotkeys": {"pause": "ctrl+alt+p", "open": "", "brightness_up": "F13"}})
    assert config.hotkeys == {"pause": "Ctrl+Alt+P", "brightness_up": "F13"}
    for wrong in (
        {"dance": "Ctrl+Alt+D"},
        {"pause": "P"},
        {"pause": 5},
        {"pause": "Ctrl+Alt+P", "open": "alt+ctrl+p"},
    ):
        with pytest.raises(ConfigError):
            parse_config({"hotkeys": wrong})
    assert parse_config({}).hotkeys == {}


# -- a real X server: the keys are pressed with XTest and arrive -----------------------------------


# -- Windows: the system registers it, and its message comes through the loop ----------------------

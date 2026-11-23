"""Screens in turn: themes the panel shows one after another, each for its seconds, minutes or
hours, and from the top again after the last ([sequence] in config.toml)."""

import tomllib

import pytest

from libre_panel.config import (
    ConfigError,
    SequenceConfig,
    edit_config,
    load_config,
    parse_config,
    write_default_config,
)
from libre_panel.sequence import SHORTEST_S, Step, StepError, parse_steps

STEPS = [Step("almanac", 300), Step("nimbus", 300), Step("studio", 600)]
# (as the app writes them into config.toml)
TABLES = [{"theme": "almanac", "minutes": 5}, {"theme": "nimbus", "minutes": 5},
          {"theme": "studio", "minutes": 10}]  # fmt: skip


# -- the steps ---------------------------------------------------------------------------------


def test_a_step_lasts_its_seconds_minutes_and_hours_together():
    steps = parse_steps([{"theme": "almanac", "minutes": 5}, {"theme": "nimbus", "seconds": 30},
                         {"theme": "studio", "hours": 1, "minutes": 30},
                         {"theme": "lumen", "minutes": 1.5}])  # fmt: skip
    assert [s.seconds for s in steps] == [300, 30, 5400, 90]


@pytest.mark.parametrize(
    "raw, says",
    [
        ({"minutes": 5}, "needs a theme"),
        ({"theme": "", "minutes": 5}, "needs a theme"),
        ({"theme": "pico"}, f"a step lasts {SHORTEST_S} s"),
        ({"theme": "pico", "seconds": 2}, f"a step lasts {SHORTEST_S} s"),
        ({"theme": "pico", "days": 2}, "unknown key 'days'"),
        ({"theme": "pico", "minutes": "5"}, "expected a number of minutes"),
        ({"theme": "pico", "minutes": True}, "expected a number of minutes"),
        ({"theme": "pico", "hours": 200}, "a step lasts"),
        ("pico", "expected a table"),
    ],
)
def test_a_step_that_cannot_be_shown_says_why(raw, says):
    with pytest.raises(StepError, match=says):
        parse_steps([raw])


# -- config.toml -------------------------------------------------------------------------------


def test_config_toml_has_the_screens_in_turn_on_or_off():
    data = tomllib.loads("""
[sequence]
[[sequence.steps]]
theme = "almanac"
minutes = 5
[[sequence.steps]]
theme = "studio"
minutes = 10
""")
    config = parse_config(data)
    assert config.sequence.on and config.sequence.active
    assert config.sequence.steps == [Step("almanac", 300), Step("studio", 600)]
    data["sequence"]["on"] = False
    assert not parse_config(data).sequence.active
    assert not parse_config({}).sequence.active  # none: the theme of config.toml
    with pytest.raises(ConfigError, match=r"sequence.steps\[0\]: needs a theme"):
        parse_config({"sequence": {"steps": [{"minutes": 5}]}})
    with pytest.raises(ConfigError, match="sequence.on"):
        parse_config({"sequence": {"on": "yes"}})


def test_the_app_writes_them_into_the_file_the_user_has(isolated_home):
    path = isolated_home / "config.toml"
    isolated_home.mkdir(parents=True)
    write_default_config(path)
    assert "# [sequence]" in path.read_text("utf-8")  # the default file says how
    tables = TABLES
    edit_config(("value", "sequence.on", True), ("tables", "sequence.steps", tables), path=path)
    assert load_config(path).sequence == SequenceConfig(True, STEPS)
    edit_config(("value", "sequence.on", False), ("tables", "sequence.steps", []), path=path)
    assert load_config(path).sequence == SequenceConfig(False, [])


# -- on the panel ------------------------------------------------------------------------------


# -- in the app --------------------------------------------------------------------------------


def test_a_theme_chosen_in_the_tray_stops_the_screens_in_turn_and_keeps_them(isolated_home):
    from libre_panel.config import set_active_theme

    path = isolated_home / "config.toml"
    isolated_home.mkdir(parents=True)
    write_default_config(path)
    tables = TABLES
    edit_config(("value", "sequence.on", True), ("tables", "sequence.steps", tables), path=path)
    set_active_theme("orbit", path)
    config = load_config(path)
    assert config.theme == "orbit" and not config.sequence.on and config.sequence.steps == STEPS

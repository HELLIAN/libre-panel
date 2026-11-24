"""Which sensor sources a config starts with (config.py), and what the config file it writes says
of them."""

import json
import sys
import tomllib

import pytest

from libre_panel import config
from libre_panel.config import (
    Config,
    default_config_toml,
    default_providers,
    load_config,
    parse_config,
    write_default_config,
)

WINDOWS = ["load", "audio", "librehardwaremonitor", "presentmon", "psutil"]
LINUX = ["load", "audio", "linux", "psutil"]
ELSEWHERE = ["load", "psutil"]  # no way to read the volume there


# -- the list a config starts with ----------------------------------------------------


def test_the_fast_sources_come_before_psutil_and_on_windows_so_does_the_hardware_one():
    assert default_providers("win32") == WINDOWS
    assert default_providers("linux") == LINUX
    assert default_providers() == default_providers(sys.platform)  # this machine's


@pytest.mark.parametrize("platform", ["darwin", "freebsd14", "cygwin"])
def test_the_volume_is_left_out_where_nothing_reads_it(platform):
    assert default_providers(platform) == ELSEWHERE


@pytest.mark.parametrize(
    ("platform", "expected"), [("win32", WINDOWS), ("linux", LINUX), ("darwin", ELSEWHERE)]
)
def test_a_new_config_file_names_the_list_of_its_platform(platform, expected):
    text = default_config_toml(platform)
    assert "@PROVIDERS@" not in text
    assert f"\nproviders = {json.dumps(expected)}\n" in text
    data = tomllib.loads(text)
    assert data["sensors"]["providers"] == expected
    assert parse_config(data).sensors.providers == expected
    assert config.DEFAULT_CONFIG_TOML == default_config_toml()  # for the machine running this


def test_the_generated_config_tells_what_librehardwaremonitor_needs():
    text = default_config_toml("win32")
    assert "Run it as administrator and turn on Options -> Remote Web Server -> Run." in text
    assert "# stale_seconds = 5" in text and "ten times a second" in text


def test_the_generated_config_tells_what_the_volume_source_is():
    text = default_config_toml("linux")
    words = " ".join(line.removeprefix("# ") for line in text.splitlines())
    assert "audio (the volume and mute of the default output: Windows, Linux with wpctl" in words
    assert "load and audio work only while a theme or a service shows their readings" in words
    assert f"\nproviders = {json.dumps(LINUX)}\n" in text


def test_the_written_config_file_has_the_list(isolated_home):
    path = write_default_config()
    assert load_config(path).sensors.providers == default_providers()
    assert f"providers = {json.dumps(default_providers())}" in path.read_text("utf-8")


def test_a_config_that_names_no_list_gets_the_default(isolated_home, monkeypatch):
    assert Config().sensors.providers == default_providers()  # no config file at all
    assert load_config().sensors.providers == default_providers()
    assert parse_config({}).sensors.providers == default_providers()
    path = isolated_home / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text('[sensors.librehardwaremonitor]\nurl = "http://nas:8085/data.json"\n')
    loaded = load_config(path)  # a [sensors] table without the list
    assert loaded.sensors.providers == default_providers()
    assert loaded.sensors.options["librehardwaremonitor"]["url"] == "http://nas:8085/data.json"
    # and on Windows: the default list is looked up when the config is made
    original = config.default_providers
    monkeypatch.setattr(config, "default_providers", lambda: original("win32"))
    assert Config().sensors.providers == WINDOWS
    assert parse_config({"theme": "slate"}).sensors.providers == WINDOWS
    assert load_config().sensors.providers == WINDOWS


@pytest.mark.parametrize(
    "providers",
    [["librehardwaremonitor", "psutil"], ["demo"], ["psutil", "load"], ["psutil", "demo"], []],
)
def test_a_list_in_the_users_config_is_left_alone(isolated_home, providers):
    assert parse_config({"sensors": {"providers": providers}}).sensors.providers == providers
    path = isolated_home / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text(f"[sensors]\nproviders = {json.dumps(providers)}\n")
    assert load_config(path).sensors.providers == providers


# -- `libre-panel sensors` ------------------------------------------------------------


# -- `libre-panel doctor` -------------------------------------------------------------

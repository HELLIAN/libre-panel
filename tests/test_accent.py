"""config.toml accent: every theme in Windows' accent colour or one of the person's."""

import pytest

from libre_panel import accent
from libre_panel.config import ConfigError, parse_config


def test_a_dark_accent_is_lightened_for_a_dark_panel_and_a_light_one_kept():
    assert accent.readable("#2EE6D6") == "#2EE6D6"
    dark = accent.readable("#0063B1")  # Windows' default blue
    assert dark != "#0063B1" and dark.startswith("#")
    import colorsys

    r, g, b = (int(dark[i : i + 2], 16) / 255 for i in (1, 3, 5))
    assert colorsys.rgb_to_hls(r, g, b)[1] >= accent.LIGHTEST_NEEDED - 0.01


def test_the_choice_stands_for_a_colour_or_for_the_theme_s_own(monkeypatch):
    assert accent.accent_of("") is None and accent.accent_of("#e0457b") == "#E0457B"
    monkeypatch.setattr(accent, "system_accent", lambda: "#FF8800")
    assert accent.accent_of("system") == "#FF8800"
    monkeypatch.setattr(accent, "system_accent", lambda: None)  # not Windows, or it says none
    assert accent.accent_of("system") is None


def test_windows_accent_colour_is_read_as_its_registry_keeps_it(monkeypatch):
    import sys
    import types

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    registry = types.SimpleNamespace(
        HKEY_CURRENT_USER=0,
        OpenKey=lambda root, path: Key(),
        QueryValueEx=lambda key, name: (0xFFD67800, 4),  # 0xAABBGGRR: red 00, green 78, blue D6
    )
    monkeypatch.setitem(sys.modules, "winreg", registry)
    monkeypatch.setattr(accent.sys, "platform", "win32")
    assert accent.system_accent() == accent.readable("#0078D6")


def test_the_setting_is_read_and_checked():
    assert parse_config({}).accent == "" and parse_config({"accent": "system"}).accent == "system"
    assert parse_config({"accent": " #E0457B "}).accent == "#E0457B"
    for bad in ("red", "#12345", 3):
        with pytest.raises(ConfigError):
            parse_config({"accent": bad})

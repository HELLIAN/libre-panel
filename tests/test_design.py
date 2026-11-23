"""One design for the built-in themes (libre_panel.design): every family wears its look, and the
pictures the app draws itself are in the Libre look."""

from pathlib import Path

from libre_panel import branding
from libre_panel.design import (
    BLOCK_COLOURS,
    LOOKS,
    STYLE_DEFAULTS,
    look_colour,
    rgb,
)
from libre_panel.editor.presets import TOKEN_DEFAULTS
from libre_panel.render import bootscreen
from libre_panel.render.light import ROLES as LIGHT_ROLES
from libre_panel.theme.model import builtin_themes_dir, load_theme
from libre_panel.theme.modules import BACKDROPS, CARDS, ROLES, module_info

ROOT = Path(__file__).resolve().parent.parent
THEMES = sorted(builtin_themes_dir().glob("*/theme.json"))


def test_every_look_has_a_colour_for_every_role_and_cards_the_modules_can_make():
    for key, look in LOOKS.items():
        assert set(ROLES) <= set(look["palette"]), key
        style = {**STYLE_DEFAULTS, **look["style"]}
        assert set(style) == set(STYLE_DEFAULTS), key
        assert style["card"] in CARDS and style["backdrop"] in BACKDROPS, key
        assert all(c.startswith("#") and len(c) == 7 for c in look["palette"].values()), key


def test_the_round_report_is_in_arena_s_design():
    """It follows a game on Arena: the same colours and cards, not the app's."""
    arena = load_theme(builtin_themes_dir() / "arena")
    for name in ("round-report", "round-report-1920x720", "round-report-480x480"):
        report = load_theme(builtin_themes_dir() / name)
        for role in ("bg", "surface", "text", "accent", "cpu", "gpu", "warn", "crit"):
            assert report.palette[role].upper() == arena.palette[role].upper(), (name, role)
        for key in ("card", "radius", "glow", "backdrop", "card_opacity"):
            assert report.style[key] == arena.style[key], (name, key)


def test_the_app_s_own_pictures_are_in_the_libre_look():
    libre = LOOKS["libre"]["palette"]
    assert bootscreen.BG == rgb(libre["bg"]) and bootscreen.BG2 == rgb(libre["bg2"])
    assert bootscreen.TEXT == rgb(libre["text"]) and bootscreen.ACCENT == rgb(libre["accent"])
    assert branding.ACCENT == rgb(look_colour("libre", "accent")) == (46, 230, 214)
    assert TOKEN_DEFAULTS == BLOCK_COLOURS
    assert TOKEN_DEFAULTS["accent"] == libre["accent"] and TOKEN_DEFAULTS["crit"] == libre["crit"]
    for role, theme_role in (("ground", "bg"), ("text", "text"), ("info", "accent"),
                             ("warning", "warn"), ("error", "crit")):  # fmt: skip
        assert LIGHT_ROLES[role][1] == libre[theme_role], role


def test_the_editor_offers_every_look_for_a_new_theme():
    looks = module_info()["looks"]
    assert list(looks) == list(LOOKS)
    assert looks["libre"]["palette"]["bg"] == "#0A0E13"
    assert looks["libre"]["style"]["radius"] == 14
    assert looks["contrast"]["name"] == "High Contrast"

"""German and English: complete catalog, dates, weather texts, language switch."""

import json
import re
from datetime import datetime

import pytest
from i18n_keys import all_messages, known_texts, tables

from libre_panel import i18n
from libre_panel.config import load_config
from libre_panel.i18n import format_date, t

CATALOG = json.loads((i18n.LOCALE_DIR / "de.json").read_text(encoding="utf-8"))
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def test_every_text_has_a_german_translation():
    missing = sorted(all_messages() - set(CATALOG["messages"]))
    assert missing == [], "add these to locale/de.json:\n" + "\n".join(missing)


def test_no_translation_is_empty():
    tables = [table for table in CATALOG.values() if isinstance(table, dict)]
    empty = sorted(text for table in tables for text, german in table.items() if german == "")
    assert empty == [], "translate these in locale/de.json:\n" + "\n".join(empty)


def test_the_catalog_has_no_stale_texts():
    unused = sorted(set(CATALOG["messages"]) - known_texts())
    assert unused == [], "no longer used, remove from locale/de.json:\n" + "\n".join(unused)


def test_the_catalog_has_each_text_once():
    """Two branches that translate the same text merge into a table that has it twice (each
    in its own place, so git sees no clash) and the later one wins unseen."""
    twice = []

    def one_of_each(pairs):
        names = [name for name, _ in pairs]
        twice.extend(sorted({name for name in names if names.count(name) > 1}))
        return dict(pairs)

    text = (i18n.LOCALE_DIR / "de.json").read_text(encoding="utf-8")
    json.loads(text, object_pairs_hook=one_of_each)
    assert twice == [], "in locale/de.json more than once, keep one:\n" + "\n".join(twice)


@pytest.mark.parametrize("table", ["fields", "widgets", "enums", "icons"])
def test_editor_tables_are_complete(table):
    assert set(CATALOG[table]) == tables()[table]


def test_a_long_german_widget_name_may_break_at_a_soft_hyphen():
    """The app's library shows the single widgets in three columns of 75 px: a word of more than
    11 letters does not fit and broke in the middle of a syllable ("Wettersymb-ol"). The catalog
    says where a compound may break (a soft hyphen, which is not seen where it does not)."""
    shy = "\u00ad"
    for key, name in CATALOG["widgets"].items():
        pieces = re.split(rf"[\s\-{shy}]+", name)
        assert all(len(piece) >= 3 for piece in pieces), (key, "a soft hyphen at an end")
        longest = max(len(piece) for piece in pieces)
        assert longest <= 11, f"{key}: {name!r} has a piece too long for a tile"
    assert CATALOG["widgets"]["symbol"].replace(shy, "") == "Wettersymbol"
    assert CATALOG["widgets"]["calendar"].replace(shy, "") == "Monatskalender"
    assert shy not in "".join(CATALOG["messages"].values())  # (the panel draws those: no box)


def test_placeholders_match():
    for english, german in CATALOG["messages"].items():
        assert set(PLACEHOLDER.findall(english)) == set(PLACEHOLDER.findall(german)), english
    assert all(german.strip() for german in CATALOG["messages"].values())


def test_names_of_days_and_months():
    assert len(CATALOG["days"]) == len(CATALOG["days_short"]) == 7
    assert len(CATALOG["months"]) == len(CATALOG["months_short"]) == 12


def test_translate_and_fall_back():
    i18n.set_language("de")
    assert t("Pause panel") == "Panel pausieren"
    assert t("Saved {id}", id="orbit") == "orbit gespeichert"
    assert t("not in the catalog") == "not in the catalog"
    assert i18n.field_label("font_size") == "Schriftgröße"
    i18n.set_language("en")
    assert t("Saved {id}", id="orbit") == "Saved orbit"
    assert i18n.field_label("font_size") == "font size"


def test_german_dates():
    moment = datetime(2026, 9, 27, 9, 5)  # a Sunday
    i18n.set_language("de")
    assert format_date(moment, "%A, %d %B") == "Sonntag, 27. September"
    assert format_date(moment, "%a %d %b") == "So 27. Sep"
    assert format_date(moment, "%a %d.%m.") == "So 27.09."
    assert format_date(moment, "%H:%M") == "09:05"
    assert format_date(datetime(2026, 3, 2), "%B") == "März"
    assert format_date(moment, "100%% %A") == "100% Sonntag"
    i18n.set_language("en")
    assert format_date(moment, "%A, %d %B") == "Sunday, 27 September"


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"LANG": "de_DE.UTF-8"}, "de"),
        ({"LANG": "de_AT.UTF-8"}, "de"),
        ({"LANG": "fr_FR.UTF-8"}, "fr"),
        ({"LANG": "pt_BR.UTF-8"}, "pt"),
        ({"LANG": "it_IT.UTF-8"}, "it"),
        ({"LANG": "ru_RU.UTF-8"}, "ru"),
        ({"LANG": "ja_JP.UTF-8"}, "ja"),
        ({"LANG": "ko_KR.UTF-8"}, "en"),
        ({"LC_ALL": "C", "LANG": "de_DE.UTF-8"}, "de"),
        ({}, "en"),
    ],
)
def test_system_language_from_the_environment(monkeypatch, env, expected):
    monkeypatch.undo()  # the real detection, not the fixture's English
    for key in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(i18n.sys, "platform", "linux")
    assert i18n.system_language() == expected


def test_language_setting(isolated_home):
    from libre_panel.config import ConfigError, parse_config

    assert load_config().language == "auto"
    assert parse_config({"language": "de"}).language == "de"
    assert parse_config({"language": "pl"}).language == "pl"
    assert parse_config({"language": "ja"}).language == "ja"
    with pytest.raises(ConfigError):
        parse_config({"language": "ko"})


def test_editor_scripts_have_no_untranslated_texts():
    """Visible texts in the editor's scripts and its built-in pages go through t();
    plain English literals are a slip."""
    from i18n_keys import script_files

    pattern = re.compile(
        r'(?:\b(?:text|title|placeholder|label|alt)|"aria-label"): *"[A-Za-z]'
        r'|\b(?:setStatus|confirm|prompt|alert)\("[A-Za-z]'
    )
    slips = [
        f"{path.name}: {line.strip()}"
        for path in script_files()
        for line in path.read_text(encoding="utf-8").splitlines()
        if pattern.search(line)
    ]
    assert slips == []

"""tools/design.py and tools/themes_v2.py: the design (rust/theme/src/design.rs, as lp-check
gives it) written into the built-in themes, and the v2 screens made of their first versions;
every built-in theme as they make it."""

import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
THEMES = sorted((ROOT / "data" / "themes").glob("*/theme.json"))


@pytest.fixture(scope="module")
def tools():
    from rust_check import find

    check = find()
    if check is None or not check.is_file():
        pytest.skip("needs lp-check (cargo build --release in rust/)")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("LIBRE_PANEL_CHECK", str(check))
        patch.syspath_prepend(str(ROOT / "tools"))
        import design
        import lp_check
        import themes_v2

        yield lp_check, design, themes_v2


def test_every_built_in_theme_belongs_to_a_family_and_every_family_has_themes(tools):
    lp_check, _, _ = tools
    names = [path.parent.name for path in THEMES]
    assert [n for n in names if lp_check.family_of(n) is None] == []
    worn = {lp_check.family_of(n)[0] for n in names}
    assert set(lp_check.design()["families"]) == worn
    assert lp_check.family_of("round-report-1920x720")[0] == "round-report"  # (with a dash)
    assert lp_check.family_of("lumen-1920x720")[0] == "lumen"
    assert lp_check.family_of("lumen-v2-1920x720")[0] == "lumen-v2"  # (the longest that fits)
    assert lp_check.family_of("lumenous") is None


def test_every_built_in_theme_wears_its_family_s_look(tools):
    """What tools/design.py would change: nothing. (A colour changed in the design: run the
    tool with --write.)"""
    _, design, _ = tools
    differs = []
    for path in THEMES:
        _, changes = design.designed(path.parent.name, json.loads(path.read_text("utf-8")))
        differs += changes
    assert differs == []


def test_the_tool_writes_a_look_into_a_theme_and_leaves_the_theme_s_own_colours(tools):
    lp_check, design, _ = tools
    data = {
        "palette": {"bg": "#000000", "bg2": "#111111", "cpu": "#123456", "base": "#000000",
                    "glow_cpu": "#ABCDEF"},
        "background": {"gradient": ["#000000", "#111111"]},
        "style": {"card": "flat", "radius": 3, "glow": 0.1, "backdrop": "flat"},
    }  # fmt: skip
    out, changes = design.designed("arena-640x480", data)
    arctic = lp_check.design()["looks"]["arctic"]["palette"]
    assert out["background"]["gradient"] == ["@bg", "@bg2"]  # it follows the palette
    assert out["palette"] == {"bg": arctic["bg"], "bg2": arctic["bg2"], "cpu": arctic["cpu"],
                              "base": arctic["bg"], "glow_cpu": "#ABCDEF"}  # fmt: skip
    assert out["style"]["card_opacity"] == 0.8 and out["style"]["radius"] == 18
    assert out["style"]["backdrop"] == "gradient"
    assert data["palette"]["bg"] == "#000000"  # (a copy)
    assert len(changes) == 2 + 4 + 5  # background, palette, cards
    _, changes = design.designed("tilt", {"palette": {}})
    assert changes == ["tilt: of no family in design.FAMILIES"]


def test_every_v2_screen_is_as_its_first_version_makes_it(tools, capsys):
    """What tools/themes_v2.py would write: nothing (a first version changed: run it with
    --write); and what it makes of one, its cards those of its family."""
    lp_check, _, themes_v2 = tools
    assert themes_v2.main([]) == 0, capsys.readouterr().out
    first = json.loads((ROOT / "data/themes/lumen/theme.json").read_text("utf-8"))
    made = themes_v2.made("lumen", first)
    assert made["name"].startswith("Lumen v2") and made["background"]["color"] == "#000000"
    cards = lp_check.design()["families"]["lumen-v2"]["cards"]
    assert {key: made["style"][key] for key in cards} == cards
    assert os.environ.get("LIBRE_PANEL_CHECK")  # (asked of the program found)

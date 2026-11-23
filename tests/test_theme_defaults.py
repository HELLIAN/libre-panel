"""The theme a panel shows when config.toml names none: Lumen on the 1920x480 bars, Libre
Default on every other panel. The rule, which panel a config is for, and config.toml."""

from libre_panel.config import Config, load_config

# -- the rule ------------------------------------------------------------------------------------


# -- which panel a config is for ------------------------------------------------------------------


# -- config.toml ----------------------------------------------------------------------------------


def test_a_theme_in_config_toml_is_given_and_none_is_the_panels_default(isolated_home):
    from libre_panel.config import parse_config

    assert parse_config({}).theme_given is False and parse_config({}).theme == "libre-default"
    named = parse_config({"theme": "libre-default"})  # what 0.4 wrote: kept, not migrated
    assert named.theme_given is True and named.theme == "libre-default"
    assert parse_config({"theme": "slate"}).theme_given is True
    assert load_config().theme_given is False  # no file at all
    assert Config().theme_given is False and Config().theme == "libre-default"
    assert Config(theme="slate").theme_given is True  # a config made in code that names one
    assert Config(theme="slate", theme_given=False).theme_given is False  # unless it says not


def test_the_template_names_no_theme_and_explains_the_default(isolated_home):
    import tomllib

    from libre_panel.config import DEFAULT_CONFIG_TOML, parse_config

    assert "theme" not in tomllib.loads(DEFAULT_CONFIG_TOML)
    lines = [line.strip() for line in DEFAULT_CONFIG_TOML.splitlines()]
    assert not [line for line in lines if line.startswith("theme")]  # no line that sets it
    assert any(line.startswith("# theme = ") for line in lines)  # how to set one
    text = DEFAULT_CONFIG_TOML
    assert "Lumen" in text and "Libre Default" in text and "1920x480" in text
    config = parse_config(tomllib.loads(DEFAULT_CONFIG_TOML))
    assert config.theme_given is False and config.theme == "libre-default"


def test_config_init_and_the_first_write_do_not_pin_a_theme(isolated_home):
    from libre_panel.config import set_brightness, theme_chosen, write_default_config

    path = write_default_config()  # libre-panel config init
    assert not theme_chosen(path) and load_config(path).theme_given is False
    path.unlink()
    set_brightness(30)  # the app's first write makes the file
    assert path.is_file() and load_config(path).device.brightness == 30
    assert not theme_chosen(path) and load_config(path).theme_given is False


def test_theme_chosen_means_that_config_toml_names_a_theme(isolated_home):
    from libre_panel.config import set_active_theme, theme_chosen, write_default_config

    assert theme_chosen() is False  # a first start: no file
    path = write_default_config()
    assert theme_chosen(path) is False  # a file without a theme line
    set_active_theme("lumen", path)  # a choice in the app, or from the tray
    assert theme_chosen(path) is True and load_config(path).theme_given is True
    assert load_config(path).theme == "lumen"
    path.write_text('theme = "libre-default"\n', encoding="utf-8")
    assert theme_chosen(path) is True  # 0.4's file: a choice, whatever it is
    path.write_text("this is not toml", encoding="utf-8")
    assert theme_chosen(path) is False
    path.write_text('# theme = "slate"\n', encoding="utf-8")
    assert theme_chosen(path) is False  # a comment names nothing


def test_a_setting_goes_under_the_commented_line_that_explains_it(isolated_home):
    from libre_panel.config import set_active_theme, set_config_value, write_default_config

    path = write_default_config()
    set_active_theme("lumen", path)  # a theme picked in the app
    set_config_value("video.local_clip", "usr/data/clip.h264", path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "# Libre Panel configuration"  # not above the title
    assert lines[lines.index('theme = "lumen"') - 1] == '# theme = "slate"'
    assert lines[lines.index('local_clip = "usr/data/clip.h264"') - 1] == '# local_clip = ""'
    assert lines.count('theme = "lumen"') == 1
    config = load_config(path)
    assert (config.theme, config.device.video.local_clip) == ("lumen", "usr/data/clip.h264")
    set_config_value("theme", "orbit", path)  # a second choice replaces the line in place
    assert [line for line in path.read_text().splitlines() if line.startswith("theme")] == [
        'theme = "orbit"'
    ]


def test_taking_the_theme_out_brings_the_panels_default_back(isolated_home):
    from libre_panel.config import edit_config, set_active_theme, write_default_config

    path = write_default_config()
    set_active_theme("slate", path)
    assert load_config(path).theme == "slate"
    edit_config(("unset", "theme", None), path=path)
    assert load_config(path).theme_given is False
    assert "# Libre Panel configuration" in path.read_text(encoding="utf-8")

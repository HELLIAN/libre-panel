"""The plugin host's own part (plugin_host.py, plugins/drawing.py): what it can load of Libre
Panel, and how it draws the plugins' widget types for the Rust app, which gives their pieces
the widgets' effects itself (lp-render's Renderer::effects; tests/test_rust_plugins.py draws
them against the Python app's)."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

import libre_panel
from libre_panel.plugins import WidgetType, drawing
from libre_panel.plugins.drawing import PluginRenderer, PluginTheme
from libre_panel.sensors.base import Reading, Snapshot

SRC = Path(libre_panel.__file__).resolve().parent.parent

# Where the plugin host starts: its program and the plugin API.
START = ("libre_panel.plugin_host", "libre_panel.plugins", "libre_panel.plugins.drawing",
         "libre_panel.plugins.host", "libre_panel.plugins.loader", "libre_panel.plugins.pages",
         "libre_panel.plugins.render")  # fmt: skip
# What it can load of Libre Panel by any import (at the top of a module or in a function): the
# plugin API and what that stands on, config.toml's Config among it (a sensor source follows
# it). The rest is the Python app's, which the Rust app replaces (docs/RUST.md); a module new
# here is one more to keep for plugins.
KEPT = frozenset({
    "libre_panel", "libre_panel.accent", "libre_panel.bundled", "libre_panel.cards",
    "libre_panel.config", "libre_panel.datadir", "libre_panel.devices",
    "libre_panel.devices.base",  # (only by the package's own names: the models load no driver)
    "libre_panel.devices.models", "libre_panel.fonts", "libre_panel.fonts.fallback",
    "libre_panel.hotkeys", "libre_panel.i18n", "libre_panel.plugin_host", "libre_panel.plugins",
    "libre_panel.plugins.drawing", "libre_panel.plugins.host", "libre_panel.plugins.loader",
    "libre_panel.plugins.pages", "libre_panel.plugins.render", "libre_panel.presence",
    "libre_panel.render", "libre_panel.render.cover", "libre_panel.rights", "libre_panel.sensors",
    "libre_panel.sensors.base", "libre_panel.sensors.sky", "libre_panel.sequence",
})  # fmt: skip


def _path(module: str) -> Path:
    path = SRC / module.replace(".", "/")
    return path / "__init__.py" if (path / "__init__.py").is_file() else path.with_suffix(".py")


def _imports(module: str) -> set[str]:
    """The modules of Libre Panel that ``module`` imports anywhere in it, with the packages on
    the way (an import runs them)."""
    named: set[str] = set()
    for node in ast.walk(ast.parse(_path(module).read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            named |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            named.add(node.module)
            named |= {f"{node.module}.{alias.name}" for alias in node.names}
    found = set()
    for name in named:
        parts = name.split(".")
        found |= {".".join(parts[:end]) for end in range(1, len(parts) + 1)}
    return {name for name in found if name.split(".")[0] == "libre_panel" and _path(name).is_file()}


def test_the_plugin_host_can_load_only_the_plugin_api_and_what_it_stands_on():
    reached, todo = set(START), list(START)
    while todo:
        for found in _imports(todo.pop()) - reached:
            reached.add(found)
            todo.append(found)
    assert not reached - KEPT, f"the plugin host now reaches {sorted(reached - KEPT)}"
    assert not KEPT - reached, f"no longer reached, so not kept: {sorted(KEPT - reached)}"


def test_the_drawing_s_numbers_and_files_are_the_python_renderer_s():
    """What plugins/drawing.py keeps of its own, as long as the Python app has it too."""
    from libre_panel.render.renderer import SUPERSAMPLE
    from libre_panel.theme.model import COVER_COLOR, ThemeError, resolve_asset

    assert drawing.SUPERSAMPLE == SUPERSAMPLE
    assert drawing.COVER_COLOR == COVER_COLOR
    root = SRC.parent / "data" / "themes" / "lumen"
    for rel in ["fonts/x.ttf", "theme.json", "", "/etc/passwd", "C:\\x.ttf", "\\x", "../x.ttf",
                "a/../../x.ttf", "a/./b.ttf"]:  # fmt: skip
        try:
            theirs: Any = resolve_asset(root, rel)
        except ThemeError as exc:
            theirs = ("refused", str(exc))
        try:
            ours: Any = drawing.theme_file(root, rel)
        except ValueError as exc:
            ours = ("refused", str(exc))
        assert ours == theirs, rel


class Box(WidgetType):
    type = "test.box"
    api = 1
    drawn = 0

    def key(self, widget: dict[str, Any], snapshot: Any, now: float) -> Any:
        return snapshot.value("cpu.load")

    def draw(self, widget: dict[str, Any], ctx: Any, snapshot: Any, now: float) -> Any:
        Box.drawn += 1
        if snapshot.value("cpu.load") is None:
            return None
        image = Image.new("RGB", (4, 3), ctx.color(widget.get("color", "@accent"))[:3])
        return (image, (1, 2)) if widget.get("placed") else image


class _Registry:
    def get(self, kind: str, name: str) -> Any:
        return Box if (kind, name) == ("widgets", "test.box") else None


@pytest.fixture
def renderer(monkeypatch) -> PluginRenderer:
    from libre_panel.plugins import loader

    monkeypatch.setattr(loader, "registry", lambda: _Registry())
    Box.drawn = 0
    return PluginRenderer(PluginTheme(name="t", width=64, height=32,
                                      palette={"accent": "#22d3ee"}))  # fmt: skip


def _snapshot(load: float | None) -> Snapshot:
    readings = {} if load is None else {"cpu.load": Reading("cpu.load", load)}
    return Snapshot(readings=readings)


def test_a_widget_type_s_piece_is_kept_while_its_widget_and_key_stay_the_same(renderer):
    """The same piece, the same object, as long as nothing it is drawn of changed: the host
    then tells the app "same", and the app keeps the piece with its effects."""
    widget = {"type": "test.box", "id": "w", "x": 5, "y": 6}
    first = renderer.widget_piece(widget, _snapshot(40.0), 0.0)
    layer, x, y = first
    assert (layer.mode, layer.size, x, y) == ("RGBA", (4, 3), 5, 6)
    assert layer.getpixel((0, 0)) == (0x22, 0xD3, 0xEE, 255)
    assert renderer.widget_piece(dict(widget), _snapshot(40.0), 1.0) is first
    assert Box.drawn == 1
    assert renderer.widget_piece(widget, _snapshot(41.0), 2.0) is not first  # the key
    moved = renderer.widget_piece({**widget, "color": "#ff0000"}, _snapshot(41.0), 3.0)
    assert moved[0].getpixel((0, 0)) == (255, 0, 0, 255)  # the widget
    assert renderer.widget_piece({**widget, "placed": True}, _snapshot(41.0), 4.0)[1:] == (1, 2)
    assert Box.drawn == 4
    assert renderer.widget_piece(widget, _snapshot(None), 5.0) is None  # it draws nothing
    assert "w" not in renderer._kept
    assert renderer.widget_piece({**widget, "type": "gone.box"}, _snapshot(40.0), 6.0) is None


def test_the_drawing_s_colours_and_fonts_are_the_theme_s(renderer, tmp_path):
    assert renderer.color("@accent") == (0x22, 0xD3, 0xEE, 255)
    assert renderer.color("@nothing") == (255, 0, 255, 255)
    assert renderer.color("#11223380", 0.5) == (0x11, 0x22, 0x33, 64)
    assert renderer.color("@cover") == (0x22, 0xD3, 0xEE, 255)  # no cover on show: the accent
    assert renderer.color(None) is None
    assert renderer.font("", 20) is renderer.font("builtin:Barlow-Medium", 20)
    assert renderer.font("", 2000).size == 512 and renderer.font("", 1).size == 4
    renderer.theme.root = tmp_path
    fallback = renderer.font("../outside.ttf", 20)
    assert fallback.size == 20  # (the default font)
    assert any("outside.ttf" in said for said in renderer.warnings)

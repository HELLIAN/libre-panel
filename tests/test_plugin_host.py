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
from without_app import KEPT, WHEEL

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
# it); a module new here is one more to keep for plugins (without_app.KEPT).


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


def test_the_package_holds_the_plugin_host_and_the_windows_command_alone():
    """What the Rust app replaced is gone from the package: its modules are those the plugin
    host reaches (KEPT) and the Windows wheel's command (WHEEL)."""
    held = set()
    for path in SRC.joinpath("libre_panel").rglob("*.py"):
        parts = path.relative_to(SRC).with_suffix("").parts
        held.add(".".join(parts[:-1] if parts[-1] == "__init__" else parts))
    assert held == KEPT | WHEEL, sorted(held ^ (KEPT | WHEEL))


# What plugins/drawing.py answers for a theme's file (the path in the theme's folder, or why it is
# refused), as the Python app's theme/model.py answered it.
THEME_FILES: dict[str, Any] = {
    "fonts/x.ttf": "fonts/x.ttf",
    "theme.json": "theme.json",
    "": ("refused", "empty asset path"),
    "/etc/passwd": ("refused", "asset path must be relative to the theme folder: '/etc/passwd'"),
    "C:\\x.ttf": ("refused", "asset path must be relative to the theme folder: 'C:\\\\x.ttf'"),
    "\\x": ("refused", "asset path must be relative to the theme folder: '\\\\x'"),
    "../x.ttf": ("refused", "asset path leaves the theme folder: '../x.ttf'"),
    "a/../../x.ttf": ("refused", "asset path leaves the theme folder: 'a/../../x.ttf'"),
    "a/./b.ttf": "a/b.ttf",
}


def _theme_file(find: Any, root: Path, rel: str, refused: type[Exception]) -> Any:
    try:
        return Path(find(root, rel)).relative_to(root).as_posix()
    except refused as exc:
        return ("refused", str(exc))


def test_the_drawing_s_numbers_and_files_are_the_python_renderer_s():
    """What plugins/drawing.py keeps of its own: the Python renderer's numbers and its way with
    a theme's files (as the Python app's theme/model.py had them)."""
    root = SRC.parent / "data" / "themes" / "lumen"
    assert (drawing.SUPERSAMPLE, drawing.COVER_COLOR) == (3, "@cover")
    for rel, answer in THEME_FILES.items():
        assert _theme_file(drawing.theme_file, root, rel, ValueError) == answer, rel


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


KEEP = """
from libre_panel.devices import Display, FrameError


class Keep(Display):
    name = "demo.keep"
    api = 1

    def open(self):
        self.got, self.refuse, self.closed = [], False, False

    def show(self, frame, region=None):
        if self.refuse:
            raise FrameError("demo: not this one")
        self.got.append((frame, region))

    def close(self):
        self.closed = True
"""


def test_a_plugins_driver_is_handed_whole_frames_made_of_the_parts_the_app_sends(
    plugin_folder, isolated_home
):
    """display.show: of a frame the app sends only the box that changed (``piece`` at ``at``),
    or all of it; the host lays the box on a copy of the frame before and hands the driver the
    whole frame, a new one each time, once for each part (as the Python app's loop handed it).
    After a frame the driver could not show there is no frame before: a part is refused then
    (the app sends a whole frame after it)."""
    from libre_panel.devices import DeviceError
    from libre_panel.plugin_host import Host

    plugin_folder(KEEP, {"libre_panel.devices": {"demo.keep": "Keep"}})
    host = Host(None, isolated_home / "plugins")  # type: ignore[arg-type]
    ident = host.display_open({"driver": "demo.keep", "device": {"port": "COM3"}}, b"")["display"]
    driver = host.displays[ident]
    assert driver.config.port == "COM3"
    red = Image.new("RGB", (8, 6), (200, 0, 0))
    whole = {"display": ident, "size": [8, 6], "mode": "RGB"}
    host.display_show({**whole, "regions": [None]}, red.tobytes())
    blue = Image.new("RGB", (3, 2), (0, 0, 200))
    part = {**whole, "at": [2, 1], "piece": [3, 2], "regions": [[2, 1, 4, 2], [4, 2, 5, 3]]}
    told = host.display_show(part, blue.tobytes())
    assert "skipped" not in told and told["describe"] == "demo.keep"
    want = red.copy()
    want.paste(blue, (2, 1))
    assert [(frame.tobytes() == want.tobytes(), region) for frame, region in driver.got[1:]] == [
        (True, (2, 1, 4, 2)),
        (True, (4, 2, 5, 3)),
    ]
    assert driver.got[0][0].tobytes() == red.tobytes()  # (the frame before as it was)
    assert driver.got[1][0] is driver.got[2][0] and driver.got[0][0] is not driver.got[1][0]
    driver.refuse = True
    assert host.display_show(part, blue.tobytes())["skipped"] == "demo: not this one"
    driver.refuse = False
    with pytest.raises(DeviceError):
        host.display_show(part, blue.tobytes())
    host.display_show({**whole, "regions": [[0, 0, 8, 6]]}, red.tobytes())
    assert driver.got[-1] == (driver.got[-1][0], (0, 0, 8, 6))
    host.display_close({"display": ident}, b"")
    assert driver.closed and ident not in host.displays and ident not in host.frames

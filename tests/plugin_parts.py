"""Plugins as the tests install them (conftest.py's plugin_folder): their sources and the parts
they declare, and a small theme to show them on. For the tests of the plugins themselves and
for those of the Rust app with its plugin host."""

from __future__ import annotations

SENSOR = """
from libre_panel.sensors import Reading, SensorProvider


class Fans(SensorProvider):
    name = "demo.fans"
    api = 1

    def read(self):
        return {"fan.front": Reading("fan.front", 1200.0, "RPM", "Front fan")}


class Old(SensorProvider):
    name = "demo.old"  # no api: written before plugin API 1


class Volume(SensorProvider):
    name = "demo.volume"
    api = 1
    every_frame = True
    reads = 0

    def read(self):
        Volume.reads += 1
        return {"audio.volume": Reading("audio.volume", float(Volume.reads), "%")}
"""


SENSOR_PARTS = {
    "libre_panel.sensors": {"demo.fans": "Fans", "demo.old": "Old", "demo.volume": "Volume"},
    "libre_panel.themes": {"demo": "{package}.themes"},
}


DRAWING = """
from PIL import Image, ImageDraw

from libre_panel.plugins import Screen, WidgetType

calls = {"screen": 0, "bar": 0, "closed": 0}


class Tint(Screen):
    name = "tint"
    api = 1
    label = {"en": "Tint", "de": "Tönung"}
    options = {"color": ("color", "#203040"), "animate": ("bool", False)}
    suppresses = {"music"}  # it shows what plays itself

    def __init__(self, context, options):
        super().__init__(context, options)
        self.moving = options["animate"]
        self._frame = Image.new("RGB", context.size, context.color(options["color"])[:3])

    def render(self, snapshot, now):
        calls["screen"] += 1
        return self._frame  # the same picture every time: nothing to compose again

    def close(self):
        calls["closed"] += 1


class Bar(WidgetType):
    type = "demo.bar"
    api = 1
    label = {"en": "Demo bar"}
    spec = {"w": ("int", 100), "h": ("int", 10), "sensor": ("sensor", "")}
    spec["color"] = ("color", "#ff0000")
    presets = [{"label": {"en": "CPU bar"}, "widget": {"sensor": "cpu.load", "w": 80}}]

    def draw(self, widget, ctx, snapshot, now):
        calls["bar"] += 1
        value = snapshot.value(widget["sensor"]) or 0
        image = Image.new("RGBA", (widget["w"], widget["h"]), (0, 0, 0, 0))
        filled = round(widget["w"] * min(max(value, 0), 100) / 100)
        box = [0, 0, filled - 1, widget["h"] - 1]
        ImageDraw.Draw(image).rectangle(box, fill=ctx.color(widget["color"]))
        return image


class Echo(Screen):
    # opaque, alone on the frame; notes what its context says
    name = "echo"
    api = 1
    seen = []
    toasts = []

    def __init__(self, context, options):
        super().__init__(context, options)
        self._frame = Image.new("RGB", context.size, "#123456")

    def render(self, snapshot, now):
        shown = self.context.shown
        Echo.seen.append((self.context.preview, shown.size if shown is not None else None))
        if self.context.toast is not None:
            Echo.toasts.append(self.context.toast[0].text)
        return self._frame


class Failing(Screen):
    name = "failing"
    api = 1

    def render(self, snapshot, now):
        raise RuntimeError("no light layers")


class NoDot(WidgetType):
    type = "bar"
    api = 1


class BadKind(WidgetType):
    type = "demo.bad"
    api = 1
    spec = {"size": ("float", 1.0)}


class Pathy(WidgetType):
    type = "demo.pathy"
    api = 1
    spec = {"picture": ("asset", "")}

    def draw(self, widget, ctx, snapshot, now):
        return None
"""


DRAWING_PARTS = {
    "libre_panel.screens": {"tint": "Tint", "echo": "Echo", "failing": "Failing"},
    "libre_panel.widgets": {
        "demo.bar": "Bar",
        "bar": "NoDot",
        "demo.bad": "BadKind",
        "demo.pathy": "Pathy",
    },
}


def theme_data(screen=None, widgets=()):
    data = {
        "format": "libre-panel-theme/1",
        "name": "plugin test",
        "display": {"model": "custom", "width": 200, "height": 100},
        "background": {"color": "#000000"},
        "widgets": list(widgets),
    }
    if screen is not None:
        data["screen"] = screen
    return data


TOAST_STYLE = """
from PIL import ImageDraw

from libre_panel.plugins import ToastStyle


class Band(ToastStyle):
    name = "demo.band"
    api = 1
    options = {"height": ("int", 40)}
    leave_s = 0.1

    def draw(self, frame, toast, age):
        out = frame.copy()
        color = toast.payload.get("color") or self.context.color("@accent")
        height = self.options["height"]
        ImageDraw.Draw(out).rectangle([0, 0, out.width - 1, height - 1], fill=color[:3])
        return out


class Broken(ToastStyle):
    name = "demo.broken"
    api = 1

    def draw(self, frame, toast, age):
        raise RuntimeError("no cover art")
"""


TOAST_PARTS = {"libre_panel.toasts": {"demo.band": "Band", "demo.broken": "Broken"}}


PAGE = """
from libre_panel.plugins import EditorPage


class Analysis(EditorPage):
    api = 1
    title = {"en": "Analysis", "de": "Analyse"}
    icon = "graph"

    def handle(self, method, path, query, body):
        if path == "status":
            service = self.context.service("counter")
            return 200, {"running": service is not None, "q": query.get("x", [""])[0]}
        if path == "echo" and method == "POST":
            return 201, {"got": body}
        if path == "boom":
            raise RuntimeError("the analysis failed")
        return 404, {"error": "no such thing"}
"""


PAGE_FILES = {
    "page/index.html": (
        '<!doctype html><html><head><link rel="stylesheet" href="/static/editor.css">'
        '<script type="module" src="app.js"></script></head>'
        '<body><h1 id="title">Analysis</h1><p id="out">…</p></body></html>'
    ),
    "page/app.js": (
        'import { api, loadTexts } from "/static/kit.js";\n'
        "await loadTexts();\n"
        'const status = await api("status?x=1");\n'
        'const echo = await api("echo", { method: "POST", body: { fan: 3 } });\n'
        'document.getElementById("out").textContent = `${status.q} ${echo.got.fan}`;\n'
    ),
    "page/secret.py": "print('not served')\n",
}


PAGE_PARTS = {"libre_panel.editor_pages": {"analysis": "Analysis"}}

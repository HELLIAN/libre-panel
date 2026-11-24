"""Plugins in the Rust app (rust/app's plugins, docs/RUST.md R10): the Python plugins run in a
process of their own, the plugin host (src/libre_panel/plugin_host.py), which the Rust app
starts where plugins are installed (folders, or packages beside the host). What the app says
of them and does with them, beside what the Python app says and does."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import golden
import pytest
from plugin_parts import SENSOR, SENSOR_PARTS, theme_data
from test_rust_app import DATA, RUST_ONLY_SERVICES, _libraries, _program, _run, python_s_services

# How long the first drawing, transition, toast style or page of a fresh plugin host may take:
# on a Windows runner its first imports took over 30 seconds while it still loaded Python's
# renderer (the app waits up to 120, plugins.rs OPENING_S), elsewhere a second or two.
HOST_S = 300 if sys.platform == "win32" else 60
HOST = {"LIBRE_PANEL_PLUGIN_HOST": sys.executable,
        "LIBRE_PANEL_PLUGIN_HOST_ARGS": "-m libre_panel.plugin_host",
        "LIBRE_PANEL_PLUGIN_TRACE": "1"}  # fmt: skip


# the display drivers the app has itself, which plugins cannot name theirs (plugins.rs)
OWN_DRIVERS = ("auto", "hongtai", "turzx", "virtual")


def _python_s_listing(text: str) -> str:
    """`libre-panel plugins` as the Python app's said it: its own services, the Rust app's
    alone left out, and without its drivers (each listed, built in)."""
    rows = text.splitlines(keepends=True)
    own = [f"services   {name:<24} ok     built in\n" for name in RUST_ONLY_SERVICES]
    own += [f"devices    {name:<24} ok     built in\n" for name in OWN_DRIVERS]
    assert all(row in rows for row in own), text
    return "".join(row for row in rows if row not in own)


def _plain(text: str, home: Path) -> str:
    """`text` without what is a run's own: the settings folder as <home>, and the plugins'
    packages (conftest.py's plugin_folder numbers them as they come) by the order in which the
    text names them."""
    for spelled in (str(home), str(home).replace("\\", "/"), json.dumps(str(home))[1:-1]):
        text = text.replace(spelled, "<home>")
    named: dict[str, str] = {}

    def numbered(found: re.Match[str]) -> str:
        return named.setdefault(found.group(0), f"lp_test_plugin_{chr(ord('a') + len(named))}")

    return re.sub(r"lp_test_plugin_\d+", numbered, text)


def _drain(stream: Any) -> tuple[threading.Thread, list[str]]:
    """``stream`` (a program's stdout or stderr) read as the program writes it, its lines kept:
    a pipe nobody reads stops the program once it is full (4 KB on Windows: the plugin host's
    log, which the app passes on to its stderr, filled it, and the app and its host stood
    still until the test gave up)."""
    lines: list[str] = []

    def read() -> None:
        try:
            for line in stream:
                lines.append(line)
        except (OSError, ValueError):  # (closed as the test ended)
            pass

    thread = threading.Thread(target=read, name="drain", daemon=True)
    thread.start()
    return thread, lines


def _ended(program: subprocess.Popen, said: tuple[threading.Thread, list[str]]) -> str:
    """``program`` asked to end (SIGINT; on Windows terminated) and waited for: what it wrote to
    its stderr, which ``said`` drained (and its exit code where it had ended by itself)."""
    before = program.poll()
    if os.name == "nt":
        program.terminate()
    else:
        program.send_signal(signal.SIGINT)
    program.wait(timeout=30)
    said[0].join(10)
    ended = "" if before is None else f"(it had ended by itself, with {before})\n"
    return ended + "".join(said[1])


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """The settings folder, as conftest.py's; with administrator rights (Windows runners have
    them) one in Program Files: the plugin host then loads plugin folders only from where
    nobody without those rights can change them (libre_panel.rights), as the downloads' check
    of the host does (packaging/check_plugin_host.py)."""
    if sys.platform == "win32":
        import ctypes

        if ctypes.windll.shell32.IsUserAnAdmin():
            made = tempfile.TemporaryDirectory(dir=os.environ["ProgramFiles"],
                                               ignore_cleanup_errors=True)  # fmt: skip
            with made as place:
                home = Path(place) / "home"
                monkeypatch.setenv("LIBRE_PANEL_HOME", str(home))
                yield home
            return
    home = tmp_path / "home"
    monkeypatch.setenv("LIBRE_PANEL_HOME", str(home))
    yield home


def test_the_plugins_are_listed_as_cli_py_lists_them(plugin_folder, isolated_home):
    data = theme_data()
    data["name"] = "Demo dash"
    files = {"themes/__init__.py": "", "themes/demo-dash/theme.json": json.dumps(data)}
    plugin_folder(SENSOR, SENSOR_PARTS, files=files)
    plugin_folder("x = 1\n", {"libre_panel.services": {"broken": "Missing"}})
    # (what the Python app's `plugins` said of them, and how it ended)
    want = golden.told("listing")
    done = _run(isolated_home, "plugins", **HOST)
    listed = _python_s_listing(_plain(done.stdout, isolated_home))
    assert [done.returncode, listed] == want, done.stderr
    assert want[0] == 1
    # the theme a plugin brings is one of the app's
    themes = _run(isolated_home, "themes", **HOST)
    assert "demo-dash" in themes.stdout.split()
    out = isolated_home / "frame.png"
    drawn = _run(isolated_home, "render", "--demo", "--theme", "demo-dash", "--output", str(out),
                 **HOST)  # fmt: skip
    assert drawn.returncode == 0 and "theme demo-dash" in drawn.stderr, drawn.stderr


def test_without_plugins_no_host_is_started(isolated_home):
    want = golden.told("listing")  # (what the Python app's `plugins` said without any)
    # (a host that cannot start would be said)
    done = _run(isolated_home, "plugins", LIBRE_PANEL_PLUGIN_HOST="/nonexistent")
    listed = _python_s_listing(_plain(done.stdout, isolated_home))
    assert [done.returncode, listed] == want, done.stderr
    assert want[0] == 0


def test_plugins_without_a_host_are_said(plugin_folder, isolated_home):
    plugin_folder(SENSOR, SENSOR_PARTS)
    done = _run(isolated_home, "plugins", LIBRE_PANEL_PLUGIN_HOST="/nonexistent")
    assert done.returncode == 1
    assert "error: the plugin host /nonexistent did not start" in done.stdout


SERVICE = """
import json

from libre_panel.plugins import Service
from libre_panel.sensors.base import Reading, SensorProvider


class Fans(SensorProvider):
    name = "demo.fans"
    api = 1

    def read(self):
        return {"fan.front": Reading("fan.front", 1200.0, "RPM", "Front")}


class Demo(Service):
    name = "demo"
    api = 1
    options = {"color": ("string", "red")}

    def start(self):
        self.done = False
        self.host.publish("demo.value", 42.0, "%", "Demo")
        self.host.publish_state("demo.round", {"n": 1})
        self.host.on("sample", self.sample)

    def sample(self, snapshot):
        fans, value = snapshot.value("fan.front"), snapshot.value("demo.value")
        if self.done or fans is None or value is None:
            return
        self.done = True
        delivery = self.host.notify("Hello from a plugin")
        heard = {"fans": fans, "value": value, "state": snapshot.state("demo.round"),
                 "delivery": [delivery.shown, delivery.reason]}
        (self.host.data_dir / "heard.json").write_text(json.dumps(heard))
        self.host.show_theme(self.options["color"])

    def summary(self):
        return {"busy": not self.done}
"""


def test_a_plugins_sensor_source_and_service_run_in_the_rust_app(plugin_folder, isolated_home):
    """A plugin's sensor source is read and its service runs, both in the plugin host: the
    service hears the samples (the source's readings and its own among them), publishes, shows
    a toast and asks for a theme, which the panel then shows."""
    plugin_folder(SERVICE, {"libre_panel.sensors": {"demo.fans": "Fans"},
                            "libre_panel.services": {"demo": "Demo"}})  # fmt: skip
    for name, colour in (("red", "#dc1e1e"), ("blue", "#1e1edc")):
        folder = isolated_home / "themes" / name
        folder.mkdir(parents=True)
        theme = {"format": "libre-panel-theme/1", "name": name,
                 "display": {"width": 800, "height": 480}, "palette": {"bg": colour},
                 "background": {"color": "@bg", "image": None}, "refresh_ms": 500,
                 "widgets": []}  # fmt: skip
        (folder / "theme.json").write_text(json.dumps(theme), encoding="utf-8")
    (isolated_home / "config.toml").write_text(
        'theme = "red"\nfps = 10\nrefresh_ms = 500\n[device]\ndriver = "virtual"\n'
        'output = "panel.png"\n[sensors]\nproviders = ["demo.fans"]\n'
        '[services]\nenabled = ["demo"]\n[services.demo]\ncolor = "blue"\n',
        encoding="utf-8",
    )
    env = {**os.environ, "LIBRE_PANEL_HOME": str(isolated_home), "LIBRE_PANEL_DATA": str(DATA),
           **_libraries(), **HOST}  # fmt: skip
    heard_file = isolated_home / "plugins-data" / "demo" / "heard.json"
    blue = False
    with subprocess.Popen([str(_program()), "run"], env=env, stderr=subprocess.PIPE,
                          encoding="utf-8") as program:  # fmt: skip
        err = _drain(program.stderr)
        try:
            until = time.monotonic() + HOST_S
            while time.monotonic() < until and not blue:
                try:
                    from PIL import Image

                    with Image.open(isolated_home / "panel.png") as frame:
                        blue = frame.convert("RGB").getpixel((400, 50)) == (30, 30, 220)
                except (OSError, SyntaxError, ValueError):
                    pass
                time.sleep(0.05)
        finally:
            said = _ended(program, err)
    assert blue, said
    heard = json.loads(heard_file.read_text("utf-8"))
    assert heard["fans"] == 1200.0 and heard["value"] == 42.0, heard
    assert heard["state"] == {"n": 1}
    assert heard["delivery"][0] is True, heard
    lines = [line.removeprefix("libre-panel: ") for line in said.splitlines()]
    assert "service 'demo' started" in lines, said
    assert "service demo: shows theme 'blue'" in lines, said
    assert "service 'demo': not installed" not in said


DRIVER = """
import json

from libre_panel.devices import Display, FrameError


class Panel(Display):
    name = "demo.panel"
    api = 1

    def open(self):
        self.shown = 0
        self.said("open", self.config.brightness, self.config.port, self.config.output)

    def said(self, *what):
        with open(self.config.output + ".log", "a", encoding="utf-8") as log:
            log.write(json.dumps(what) + "\\n")

    def set_brightness(self, percent):
        self.said("brightness", percent)

    def describe(self):
        return "the demo panel"

    def show(self, frame, region=None):
        self.shown += 1
        if self.shown == 2:
            raise FrameError("demo: not this one")
        frame.save(self.config.output + ".png", "PNG")
        self.counters.bytes += 3 * frame.width * frame.height
        self.said("show", frame.mode, list(frame.size), region)

    def close(self):
        self.said("close")
"""


def test_a_plugins_display_driver_shows_the_rust_apps_frames(plugin_folder, isolated_home):
    """A plugin's display driver (`[device] driver`) runs in the plugin host and is handed the
    app's frames as the Python app's loop handed them: made with the panel's `[device]`,
    opened, its brightness set, each frame that changed with the box that changed (the first
    whole); a frame it cannot show (FrameError) is skipped and the panel kept, the next one
    whole again; closed when the app ends. `libre-panel plugins` lists it, beside the app's own
    drivers."""
    plugin_folder(DRIVER, {"libre_panel.devices": {"demo.panel": "Panel"}})
    listed = _run(isolated_home, "plugins", **HOST)
    assert listed.returncode == 0, listed.stdout + listed.stderr
    rows = [row.split() for row in listed.stdout.splitlines()]
    assert ["devices", "demo.panel", "ok", "folder"] in [row[:4] for row in rows], listed.stdout
    assert [["devices", name, "ok", "built", "in"] for name in OWN_DRIVERS] == [
        row for row in rows if row[:1] == ["devices"] and row[-1] == "in"
    ]
    folder = isolated_home / "themes" / "red"
    folder.mkdir(parents=True)
    theme = {"format": "libre-panel-theme/1", "name": "red",
             "display": {"width": 800, "height": 480}, "palette": {"bg": "#dc1e1e"},
             "background": {"color": "@bg", "image": None}, "refresh_ms": 500,
             "widgets": [{"type": "clock", "x": 20, "y": 20, "format": "%H:%M:%S",
                          "font_size": 60, "color": "#ffffff"}]}  # fmt: skip
    (folder / "theme.json").write_text(json.dumps(theme), encoding="utf-8")
    output = isolated_home / "panel"
    (isolated_home / "config.toml").write_text(
        'theme = "red"\nfps = 10\n[device]\ndriver = "demo.panel"\nbrightness = 35\n'
        f'port = "COM9"\noutput = {json.dumps(str(output))}\ndark_when_away = false\n',
        encoding="utf-8",
    )
    env = {**os.environ, "LIBRE_PANEL_HOME": str(isolated_home), "LIBRE_PANEL_DATA": str(DATA),
           **_libraries(), **HOST}  # fmt: skip
    log = Path(f"{output}.log")

    def said() -> list[list[Any]]:
        try:
            return [json.loads(line) for line in log.read_text("utf-8").splitlines()]
        except (OSError, ValueError):
            return []

    with subprocess.Popen([str(_program()), "run"], env=env, stderr=subprocess.PIPE,
                          encoding="utf-8") as program:  # fmt: skip
        err = _drain(program.stderr)
        try:
            until = time.monotonic() + HOST_S
            while time.monotonic() < until and sum(s[0] == "show" for s in said()) < 3:
                time.sleep(0.05)
        finally:
            words = _ended(program, err)
    lines = said()
    shows = [line for line in lines if line[0] == "show"]
    assert len(shows) >= 3, words
    assert lines[0] == ["open", 35, "COM9", str(output)], lines
    assert ["brightness", 35] in lines, lines
    whole = ["show", "RGB", [800, 480], [0, 0, 800, 480]]
    assert shows[:2] == [whole, whole], lines  # (the second after the frame skipped)
    assert all(show[:3] == whole[:3] for show in shows), lines
    x0, y0, x1, y1 = shows[2][3]  # (the clock's seconds)
    assert 0 <= x0 < x1 < 400 and 0 <= y0 < y1 < 200, lines
    assert [line[0] for line in lines].count("open") == 1, lines  # (the skipped frame kept it)
    from PIL import Image

    with Image.open(f"{output}.png") as frame:
        assert frame.convert("RGB").getpixel((400, 240)) == (220, 30, 30)
    assert "frame skipped: its driver cannot show it: demo: not this one" in words, words
    assert "the demo panel" in words, words
    if os.name != "nt":  # (asked to end, not terminated)
        assert lines[-1] == ["close"], lines


@pytest.mark.skipif(sys.platform == "win32", reason="the host is started through a shell script")
def test_a_plugin_installed_with_pip_starts_the_host_without_a_folder(isolated_home, tmp_path):
    """A plugin installed with pip (or `pipx inject`) into the environment of the plugin host
    has no folder: the app finds its entry points in the environment's packages and starts the
    host for it (and still none where nothing brings a plugin)."""
    env_folder = tmp_path / "env"
    site = env_folder / "lib" / f"python{sys.version_info[0]}.{sys.version_info[1]}"
    site = site / "site-packages"
    (site / "lp_pip_demo").mkdir(parents=True)
    (site / "lp_pip_demo" / "__init__.py").write_text(SERVICE, encoding="utf-8")
    host = env_folder / "bin" / "python"
    host.parent.mkdir(parents=True)
    host.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    host.chmod(0o755)
    path = os.pathsep.join([str(site), os.environ.get("PYTHONPATH", "")])
    pip = {**HOST, "LIBRE_PANEL_PLUGIN_HOST": str(host), "PYTHONPATH": path}
    nothing = _run(isolated_home, "plugins", **pip)
    assert "no plugins installed" in nothing.stdout, nothing.stdout + nothing.stderr
    assert "plugin host: start" not in nothing.stderr, nothing.stderr
    meta = site / "lp_pip_demo-1.0.dist-info"
    meta.mkdir()
    (meta / "METADATA").write_text("Metadata-Version: 2.1\nName: lp-pip-demo\nVersion: 1.0\n",
                                   encoding="utf-8")  # fmt: skip
    (meta / "entry_points.txt").write_text("[libre_panel.services]\npipdemo = lp_pip_demo:Demo\n",
                                           encoding="utf-8")  # fmt: skip
    found = _run(isolated_home, "plugins", **pip)
    rows = [row.split() for row in found.stdout.splitlines()]
    assert ["services", "pipdemo", "ok", "package", "lp-pip-demo", "1.0"] in rows, found.stdout
    assert found.returncode == 0, found.stdout + found.stderr


def test_settings_list_the_plugins_as_settings_py_lists_them(plugin_folder, isolated_home):
    """Settings' catalog: the plugins' services (where each came from, why it does not load,
    the options it declares) among Libre Panel's own in their order, and their sensor sources
    after the built-in ones; an option of a plugin's service checked as it declares it."""
    from test_rust_app import _asked

    services = {"demo": "Demo", "aaa": "Missing"}
    plugin_folder(SERVICE + DRIVER, {"libre_panel.sensors": {"demo.fans": "Fans"},
                                     "libre_panel.services": services,
                                     "libre_panel.devices": {"demo.panel": "Panel"}})  # fmt: skip
    config = isolated_home / "config.toml"
    config.write_text("[sensors]\nproviders = []\n", encoding="utf-8")
    # (the catalog the Python app's settings.py made of them: its services and its sources)
    want = golden.told("catalog")
    env = {**os.environ, "LIBRE_PANEL_HOME": str(isolated_home), "LIBRE_PANEL_DATA": str(DATA),
           **_libraries(), **HOST}  # fmt: skip
    pipe = subprocess.PIPE
    ours = subprocess.Popen([str(_program()), "editor", "--port", "0"], env=env, stdout=pipe,
                            stderr=pipe, encoding="utf-8")  # fmt: skip
    err = _drain(ours.stderr)
    try:
        assert ours.stdout is not None
        address = ours.stdout.readline().split(" running at ")[1].split()[0]
        _drain(ours.stdout)
        port, token = int(address.split(":")[2].split("/")[0]), address.split("#token=")[1]

        def ask(method: str, path: str, body: str = "") -> tuple[str, dict]:
            request = (f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                       f"X-Libre-Panel: {token}\r\nContent-Type: application/json\r\n"
                       f"Content-Length: {len(body.encode())}\r\n\r\n{body}")  # fmt: skip
            status, _, answer = _asked(port, request.encode(), wait=HOST_S)
            return status, json.loads(answer)

        _, told = ask("GET", "/api/settings")
        got = json.loads(_plain(json.dumps(told["catalog"]), isolated_home))
        assert python_s_services(got["services"]) == want["services"]
        assert got["providers"] == want["providers"]
        # (the drivers to choose from: the app's, sorted with the plugin's among them)
        assert got["drivers"] == ["auto", "demo.panel", "turzx", "virtual"]
        status, _ = ask("POST", "/api/settings", json.dumps({"set": {"device.driver": "nope"}}))
        assert status == "HTTP/1.0 400 Bad Request"
        status, _ = ask("POST", "/api/settings",
                        json.dumps({"set": {"device.driver": "demo.panel"}}))  # fmt: skip
        assert status == "HTTP/1.0 200 OK", status
        assert 'driver = "demo.panel"' in config.read_text("utf-8")
        status, _ = ask("POST", "/api/settings", json.dumps({"set": {"services.demo.color": 5}}))
        assert status == "HTTP/1.0 400 Bad Request"
        status, _ = ask("POST", "/api/settings",
                        json.dumps({"set": {"services.demo.color": "green"}}))  # fmt: skip
        assert status == "HTTP/1.0 200 OK", status
        assert 'color = "green"' in config.read_text("utf-8")
    finally:
        print(_ended(ours, err))  # (the app's words, shown where the test fails)


def test_a_plugins_screen_and_widget_type_are_drawn_as_the_python_app_draws_them(
    plugin_folder, isolated_home, tmp_path
):
    import hashlib

    from PIL import Image
    from plugin_parts import DRAWING, DRAWING_PARTS

    plugin_folder(DRAWING, parts=DRAWING_PARTS)
    data = theme_data({"name": "tint", "options": {"color": "#102030"}}, [
        {"type": "demo.bar", "id": "bar", "x": 10, "y": 20, "w": 120, "h": 14,
         "sensor": "disk.load", "glow": 0.6, "color": "@accent"},
        {"type": "text", "id": "label", "x": 10, "y": 50, "text": "over the screen",
         "font_size": 16},
    ])  # fmt: skip
    data["palette"] = {"accent": "#40c0ff"}
    folder = isolated_home / "themes" / "plugged"
    folder.mkdir(parents=True)
    (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
    got = tmp_path / "rust.png"
    # (disk.load: a demo reading that holds still. The picture the Python app's `preview`
    # drew of the theme, by its pixels' digest)
    want = golden.told("preview")
    done = _run(isolated_home, "preview", "plugged", "--output", str(got), **HOST)
    assert done.returncode == 0, done.stderr
    with Image.open(got) as rust:
        assert rust.size == (200, 100)
        assert rust.getpixel((190, 90)) == (16, 32, 48)  # the screen under everything
        assert hashlib.sha256(rust.convert("RGB").tobytes()).hexdigest() == want
    # a screen no plugin has: no frame made in Python's place without it
    data["screen"] = {"name": "nowhere", "options": {}}
    (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
    done = _run(isolated_home, "preview", "plugged", "--output", str(got),
                LIBRE_PANEL_PLUGIN_HOST="/nonexistent")  # fmt: skip
    assert done.returncode != 0


CLOCKED = """
import os

import libre_panel
from PIL import Image

from libre_panel.plugins import Screen


class Clocked(Screen):
    name = "clocked"
    api = 1

    def __init__(self, context, options):
        super().__init__(context, options)
        self.moving = True
        self._frame = Image.new("RGB", context.size, (0, 0, 0))

    def render(self, snapshot, now):
        with open(os.environ["LP_TEST_CLOCKS"], "a", encoding="utf-8") as kept:
            kept.write(f"{now!r} {libre_panel.clock()!r}\\n")
        return self._frame
"""


def test_a_plugins_now_is_on_the_clock_of_libre_panel_clock(plugin_folder, isolated_home):
    """The ``now`` a plugin's screen is drawn with is a time on ``libre_panel.clock()``, as
    PLUGINS.md promises (so that ``now - snapshot.state_stamp(key)`` is an age, and a time
    the plugin took itself compares with it): in the plugin host that clock counts from where
    the app's began, though they are two processes."""
    plugin_folder(CLOCKED, {"libre_panel.screens": {"clocked": "Clocked"}})
    data = theme_data({"name": "clocked", "options": {}}, [])
    folder = isolated_home / "themes" / "clocked"
    folder.mkdir(parents=True)
    (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
    (isolated_home / "config.toml").write_text(
        'theme = "clocked"\nfps = 10\n[device]\ndriver = "virtual"\noutput = "panel.png"\n',
        encoding="utf-8",
    )
    clocks = isolated_home / "clocks.txt"
    env = {**os.environ, "LIBRE_PANEL_HOME": str(isolated_home), "LIBRE_PANEL_DATA": str(DATA),
           **_libraries(), **HOST, "LP_TEST_CLOCKS": str(clocks)}  # fmt: skip
    with subprocess.Popen([str(_program()), "run"], env=env, stderr=subprocess.PIPE,
                          encoding="utf-8") as program:  # fmt: skip
        err = _drain(program.stderr)
        try:
            until = time.monotonic() + HOST_S
            while time.monotonic() < until and (
                not clocks.exists() or len(clocks.read_text("utf-8").splitlines()) < 10
            ):
                time.sleep(0.05)
        finally:
            said = _ended(program, err)
    pairs = [tuple(map(float, line.split())) for line in clocks.read_text("utf-8").splitlines()]
    assert len(pairs) >= 10, said
    # (a frame is drawn a little before its time: at most a few frames at 10 a second)
    assert all(abs(now - clock) < 1.0 for now, clock in pairs), pairs
    assert [now for now, _ in pairs] == sorted(now for now, _ in pairs)


def test_the_editor_is_told_the_plugins_parts_as_drawings_py_tells_them(
    plugin_folder, isolated_home
):
    """/api/specs with plugins installed: their widget types with their fields and names, the
    building blocks they bring, their screens and toast styles, as drawings.py's plugin_specs
    tells them in the app's language, after Libre Panel's own."""
    from plugin_parts import DRAWING, DRAWING_PARTS
    from test_rust_app import _asked

    plugin_folder(DRAWING, parts=DRAWING_PARTS)
    plugin_folder(SWITCHING, {"libre_panel.toasts": {"demo.green": "Green"}})
    config = isolated_home / "config.toml"
    config.write_text('language = "de"\n[sensors]\nproviders = []\n', encoding="utf-8")
    # (what the Python app's drawings.py told of the plugins' parts, in German)
    want = golden.told("specs")
    assert want["screens"]["tint"]["label"] == "Tönung" and "demo.bar" in want["widgets"]
    assert want["presets"] and list(want["toasts"])[0] == "band" and len(want["toasts"]) > 1
    env = {**os.environ, "LIBRE_PANEL_HOME": str(isolated_home), "LIBRE_PANEL_DATA": str(DATA),
           **_libraries(), **HOST}  # fmt: skip
    pipe = subprocess.PIPE
    ours = subprocess.Popen([str(_program()), "editor", "--port", "0"], env=env, stdout=pipe,
                            stderr=pipe, encoding="utf-8")  # fmt: skip
    err = _drain(ours.stderr)
    try:
        assert ours.stdout is not None
        address = ours.stdout.readline().split(" running at ")[1].split()[0]
        _drain(ours.stdout)
        port, token = int(address.split(":")[2].split("/")[0]), address.split("#token=")[1]
        request = (f"GET /api/specs HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                   f"X-Libre-Panel: {token}\r\n\r\n")  # fmt: skip
        status, _, answer = _asked(port, request.encode(), wait=HOST_S)
        assert status == "HTTP/1.0 200 OK", (status, answer[:300])
        told = json.loads(_plain(answer.decode("utf-8"), isolated_home))
        for key in ("widget_labels", "screens", "toasts"):
            assert told[key] == want[key], key
            assert list(told[key]) == list(want[key]), key
        for name, fields in want["widgets"].items():
            assert told["widgets"][name] == fields, name
        assert list(told["widgets"])[-len(want["widgets"]) :] == list(want["widgets"])
        assert told["presets"][-len(want["presets"]) :] == want["presets"]
        assert set(told["finishes"]) and "demo.bar" not in told["finishes"]
    finally:
        print(_ended(ours, err))  # (the app's words, shown where the test fails)


SWITCHING = """
from PIL import Image, ImageDraw

from libre_panel.plugins import Service, ToastStyle, Transition


class Curtain(Transition):
    name = "demo.curtain"
    api = 1
    duration = 3.0

    def frame(self, old, new, t):
        out = old.copy()
        width = round(new.width * t)
        out.paste(new.crop((0, 0, width, new.height)), (0, 0))
        return out


class Green(ToastStyle):
    name = "demo.green"
    api = 1
    options = {"height": ("int", 40)}
    leave_s = 0.1

    def draw(self, frame, toast, age, previous=None):
        out = frame.copy()
        ImageDraw.Draw(out).rectangle([0, 0, out.width - 1, self.options["height"] - 1],
                                      fill=(0, 220, 0))
        return out


class Switcher(Service):
    name = "switcher"
    api = 1

    def start(self):
        self.done = False
        self.host.on("sample", self.sample)

    def sample(self, snapshot):
        if self.done:
            return
        self.done = True
        self.host.notify("a green band")
        self.host.show_theme("blue", transition=("demo.curtain", {}))
"""


def test_a_plugins_transition_and_toast_style_play_in_the_rust_app(plugin_folder, isolated_home):
    """A theme's toast style from a plugin draws the toasts, and a plugin's transition plays
    when a service switches the theme with it: both run in the plugin host."""
    from PIL import Image

    plugin_folder(SWITCHING, {"libre_panel.toasts": {"demo.green": "Green"},
                              "libre_panel.transitions": {"demo.curtain": "Curtain"},
                              "libre_panel.services": {"switcher": "Switcher"}})  # fmt: skip
    for name, colour in (("red", "#dc1e1e"), ("blue", "#1e1edc")):
        folder = isolated_home / "themes" / name
        folder.mkdir(parents=True)
        theme = {"format": "libre-panel-theme/1", "name": name,
                 "display": {"width": 800, "height": 480}, "palette": {"bg": colour},
                 "background": {"color": "@bg", "image": None}, "refresh_ms": 500,
                 "toast": {"style": "demo.green", "options": {"height": 30}},
                 "widgets": []}  # fmt: skip
        (folder / "theme.json").write_text(json.dumps(theme), encoding="utf-8")
    (isolated_home / "config.toml").write_text(
        'theme = "red"\nfps = 10\nrefresh_ms = 500\n[device]\ndriver = "virtual"\n'
        'output = "panel.png"\n[services]\nenabled = ["switcher"]\n',
        encoding="utf-8",
    )
    env = {**os.environ, "LIBRE_PANEL_HOME": str(isolated_home), "LIBRE_PANEL_DATA": str(DATA),
           **_libraries(), **HOST}  # fmt: skip
    seen = {"band": False, "curtain": False, "blue": False}
    red, blue, green = (220, 30, 30), (30, 30, 220), (0, 220, 0)
    with subprocess.Popen([str(_program()), "run"], env=env, stderr=subprocess.PIPE,
                          encoding="utf-8") as program:  # fmt: skip
        err = _drain(program.stderr)
        try:
            until = time.monotonic() + HOST_S
            while time.monotonic() < until and not all(seen.values()):
                try:
                    with Image.open(isolated_home / "panel.png") as frame:
                        frame = frame.convert("RGB")
                        top, left, right = (frame.getpixel(at) for at in
                                            ((400, 10), (40, 300), (760, 300)))  # fmt: skip
                except (OSError, SyntaxError, ValueError):
                    time.sleep(0.02)
                    continue
                seen["band"] |= top == green
                seen["curtain"] |= left == blue and right == red
                seen["blue"] |= left == blue and right == blue
                time.sleep(0.02)
        finally:
            said = _ended(program, err)
    assert all(seen.values()), (seen, said)
    assert "failed" not in said and "not installed" not in said, said


def test_a_plugins_editor_page_is_served_as_server_py_serves_it(plugin_folder, isolated_home):
    """A plugin's page among the app's own (its title in the app's language, its icon), its
    files from its folder and nothing beside them, and its API answered in the plugin host:
    a query, a body, a status of its own and its failure."""
    from plugin_parts import PAGE, PAGE_FILES, PAGE_PARTS
    from test_rust_app import _asked

    plugin_folder(PAGE, PAGE_PARTS, files=PAGE_FILES)
    (isolated_home / "config.toml").write_text(
        '[device]\ndriver = "virtual"\n[sensors]\nproviders = []\n', encoding="utf-8"
    )
    env = {**os.environ, "LIBRE_PANEL_HOME": str(isolated_home), "LIBRE_PANEL_DATA": str(DATA),
           **_libraries(), **HOST}  # fmt: skip
    with subprocess.Popen([str(_program()), "editor", "--port", "0"], env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          encoding="utf-8") as program:  # fmt: skip
        err = _drain(program.stderr)
        try:
            assert program.stdout is not None
            address = program.stdout.readline().split(" running at ")[1].split()[0]
            _drain(program.stdout)
            port, token = int(address.split(":")[2].split("/")[0]), address.split("#token=")[1]

            def ask(method: str, path: str, body: Any = None) -> tuple[int, Any]:
                data = json.dumps(body).encode() if body is not None else b""
                head = (f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                        f"X-Libre-Panel: {token}\r\nContent-Type: application/json\r\n"
                        f"Content-Length: {len(data)}\r\n\r\n")  # fmt: skip
                status, _, answer = _asked(port, head.encode() + data, wait=HOST_S)
                code = int(status.split()[1])
                try:
                    return code, json.loads(answer)
                except ValueError:
                    return code, answer

            assert ask("GET", "/api/plugins/pages") == (200, [
                {"id": "analysis", "title": "Analysis", "icon": "graph"},
                {"id": "autopilot", "title": "Autopilot", "icon": ""},
                {"id": "control", "title": "Control", "icon": ""},
            ])  # fmt: skip
            status, html = ask("GET", "/plugins/analysis/")
            assert status == 200 and b"Analysis" in html
            assert ask("GET", "/plugins/analysis/app.js")[0] == 200
            assert ask("GET", "/plugins/analysis/secret.py")[0] == 404
            assert ask("GET", "/plugins/analysis/../../plugin.toml")[0] == 404
            assert ask("GET", "/plugins/nothing/")[0] == 404
            assert ask("GET", "/api/plugins/analysis/status?x=7") == (
                200, {"running": False, "q": "7"})  # fmt: skip
            assert ask("POST", "/api/plugins/analysis/echo", {"a": 1}) == (201, {"got": {"a": 1}})
            status, data = ask("GET", "/api/plugins/analysis/boom")
            assert status == 500 and "the analysis failed" in data["error"]
            assert ask("GET", "/api/plugins/pages")[0] == 200
        finally:
            print(_ended(program, err))  # (the app's words, shown where the test fails)


WATCHING = """
import json
from pathlib import Path

from PIL import Image

from libre_panel.plugins import EditorPage, Screen, Service

NOTES = Path(__file__).parent / "seen.json"


class Watcher(Screen):
    name = "watcher"
    api = 1

    def render(self, snapshot, now):
        shown, toast = self.context.shown, self.context.toast
        NOTES.write_text(json.dumps({
            "shown": list(shown.size) if shown is not None else None,
            "corner": list(shown.getpixel((5, 400))[:3]) if shown is not None else None,
            "toast": toast[0].text if toast is not None else None,
        }))
        return Image.new("RGB", self.context.size, (40, 90, 160))


class Teller(Service):
    name = "teller"
    api = 1

    def start(self):
        self.host.on("sample", lambda snapshot: self.host.notify("hello there", seconds=30))


class Inspect(EditorPage):
    api = 1
    title = {"en": "Inspect"}

    def handle(self, method, path, query, body):
        ctx = self.context
        if path == "frame":
            frame = ctx.shown_frame()
            return 200, {"size": list(frame.size) if frame is not None else None,
                         "picture": list(ctx.shown_picture().size)
                         if ctx.shown_picture() is not None else None,
                         "second": ctx.shown_frame(2) is not None}
        if path == "snapshot":
            snapshot = ctx.snapshot()
            return 200, {"has": snapshot is not None and "moon.phase" in snapshot.readings}
        if path == "sources":
            return 200, {"names": [s["name"] for s in ctx.sources()]}
        if path == "notify":
            delivery = ctx.notify("from the page", payload={"image": Image.new("RGB", (8, 8))})
            return 200, {"shown": delivery.shown, "reason": delivery.reason}
        if path == "state":
            return 200, {"background": ctx.background,
                         "state": (ctx.status() or {}).get("state"),
                         "services": ctx.services()}
        return 404, {"error": "no"}
"""


def test_plugins_see_the_frame_shown_and_the_toast_and_pages_the_running_app(
    plugin_folder, isolated_home
):
    """A plugin's screen sees the frame the panel showed last and the toast on show (context
    .shown, context.toast), as the Python app's renderer gives them; a plugin's page in the
    background app reads the panel's frames, readings, sources and state and sends a message
    with a picture, through the app."""
    package = plugin_folder(WATCHING, {"libre_panel.screens": {"watcher": "Watcher"},
                                       "libre_panel.services": {"teller": "Teller"},
                                       "libre_panel.editor_pages": {"inspect": "Inspect"}},
                            files={"page/index.html": "<h1>Inspect</h1>"})  # fmt: skip
    folder = isolated_home / "themes" / "watched"
    folder.mkdir(parents=True)
    theme = {"format": "libre-panel-theme/1", "name": "watched",
             "display": {"width": 800, "height": 480}, "screen": {"name": "watcher"},
             "background": {"color": "#000000", "image": None}, "refresh_ms": 300,
             "widgets": []}  # fmt: skip
    (folder / "theme.json").write_text(json.dumps(theme), encoding="utf-8")
    (isolated_home / "config.toml").write_text(
        'theme = "watched"\nfps = 10\nrefresh_ms = 300\n[device]\ndriver = "virtual"\n'
        'output = "panel.png"\n[sensors]\nproviders = []\n[services]\nenabled = ["teller"]\n',
        encoding="utf-8",
    )
    notes = isolated_home / "plugins" / package / package / "seen.json"
    env = {**os.environ, "LIBRE_PANEL_HOME": str(isolated_home), "LIBRE_PANEL_DATA": str(DATA),
           **_libraries(), **HOST}  # fmt: skip
    from test_rust_app import _asked

    with subprocess.Popen([str(_program()), "start", "--port", "0", "--no-browser"], env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          encoding="utf-8") as program:  # fmt: skip
        err = _drain(program.stderr)
        try:
            assert program.stdout is not None
            address = program.stdout.readline().split(" running at ")[1].split()[0]
            _drain(program.stdout)
            port, token = int(address.split(":")[2].split("/")[0]), address.split("#token=")[1]

            def ask(path: str) -> Any:
                head = (f"GET /api/plugins/inspect/{path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                        f"X-Libre-Panel: {token}\r\n\r\n")  # fmt: skip
                status, _, answer = _asked(port, head.encode(), wait=HOST_S)
                assert status.split()[1] == "200", (status, answer)
                return json.loads(answer)

            seen: dict[str, Any] = {}
            until = time.monotonic() + HOST_S
            while time.monotonic() < until:
                try:
                    seen = json.loads(notes.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    seen = {}
                if seen.get("shown") and seen.get("toast"):
                    break
                time.sleep(0.1)
            assert seen.get("shown") == [800, 480] and seen.get("toast") == "hello there", seen
            assert seen["corner"] == [40, 90, 160]  # the screen's own colour, as it showed
            assert ask("frame") == {"size": [800, 480], "picture": [800, 480], "second": False}
            state = ask("state")
            assert state["background"] is True and state["state"] == "showing", state
            assert state["services"].get("teller") == "running", state
            assert ask("notify") == {"shown": True, "reason": ""}
            assert isinstance(ask("sources")["names"], list)
            assert ask("snapshot") == {"has": True}  # (the sky's readings are always there)
        finally:
            said = _ended(program, err)
    assert "panicked" not in said and "Traceback" not in said, said

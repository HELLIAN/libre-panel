"""Panel clips for tools/readme_media.py: Libre Panel's own main loop (``libre-panel clip``), on a
clock of its own.

A clip is the panel as it looks in video mode: the panel's loop driven frame by frame at 50 frames
a second, with the demo's values moving, a panel that keeps each frame it is shown and scripted
events (a notice, a theme switch, a game that starts, a card that changes). Nothing here waits for
the wall clock, so a clip is the same on every run, whatever the machine:

- the loop's clock is a number the recipe sets, frame by frame, and the demo's values follow it
  (lp-check's ``clip_demo``);
- the time of day on the panel stands still (as in the pictures of tools/readme_images.py), or,
  in a clip of a clock, runs with the loop's clock from that same moment;
- the loop makes a theme and the pieces of a frame in the frame that needs them, and the services
  answer in the frame they are told something in: when a thread would have finished does not
  come into it.

Each recipe is a :class:`PanelClip`: how long it runs, and a function that sets the stage (themes,
events, changes to the demo's values). Add one by adding it to ``CLIPS``. Several panels at once
are a :class:`PanelsClip` (``PANELS_CLIPS``). The panel's own start screen (``START_CLIPS``) is
not the main loop: its pictures are shown as the panel shows them. Nothing here runs the Python
app: the program is the one tools/lp_check.py finds (``LIBRE_PANEL_PROGRAM``, else the one built
in rust/).
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import shutil
import struct
import subprocess
import tempfile
import threading
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import IO, Any

import readme_images as images
from lp_check import app_program, ask, libraries, typed
from media_encode import FrameSink, Recorded, Settings, fade_loop
from media_themes import LAYOUTS, Grid, find_theme, theme_data, theme_folder
from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parent.parent
START = 1000.0  # the loop's clock at the first frame of a clip (the number is arbitrary)
WARMUP_S = (
    2.0  # frames drawn before the clip begins: the panel's own start (a sweep of light) is over
)
PREFILL_S = 120.0  # how much history the graphs have when the clip begins ...
PREFILL_MAX = 300  # ... in readings at most (a graph shows fewer, and a hub keeps 600)
FADE_S = 0.5  # a clip's end is blended into its start over this long
VOLUME_S = 30.0  # the volume (percent) when a clip begins
# The steps of a clip's screens in turn are told in tenths of a second: config.toml has whole
# seconds, at least five (the program takes them as tenths: its "pace").
TURN_UNIT = 0.1
DEMO_AT_ONCE = 64  # the demo's readings asked of lp-check for so many frames at a time
DEFAULT_LOOK = "libre-panel"  # the panel's own look of its start screens (lp_render::boot)


@dataclass
class Reading:
    """A reading as a source gives it."""

    value: Any
    unit: str = ""
    label: str = ""


Tweak = Callable[[dict[str, Reading], float], None]  # (readings, seconds into the clip)


def _plain(told: dict[str, Any]) -> Any:
    """A value as lp-check tells it (tools/lp_check.py's ``typed``), as Python has it."""
    ((kind, inner),) = told.items()
    if kind == "f":
        return struct.unpack(">d", bytes.fromhex(inner))[0]
    if kind == "i":
        return int(inner)
    if kind == "l":
        return [_plain(item) for item in inner]
    if kind == "d":
        return {key: _plain(item) for key, item in inner.items()}
    return inner  # (a text, a bool or None)


def _told(said: list[list[Any]]) -> dict[str, Reading]:
    return {key: Reading(_plain(value), unit, label) for key, value, unit, label in said}


def demo_report() -> dict[str, Reading]:
    """The round report the memory service tells after a game, at the moment of the README's
    pictures (made up, as the demo has it)."""
    (said,) = ask([{"op": "clip_demo", "when": images.WHEN, "seconds": [], "period": None}])
    return _told(said["report"])


class Demo:
    """The demo source on the clip's clock (lp-check's ``clip_demo``): its values move as real
    ones do, and a recipe's tweaks change them. The demo's waves are shaped like a load (the shape
    its static pictures have), not like a sine.

    With a ``period`` (the length of a clip that loops) the waves have it too: the wave a period on
    is blended in over the period, smoothly, so a value is the same when the clip ends as when it
    begins, and so is every graph that has the clip's values in it."""

    def __init__(self, tweaks: list[Tweak], period: float | None = None) -> None:
        self.tweaks, self.period = tweaks, period

    def read(self, seconds: Iterable[float]) -> Iterator[dict[str, Reading]]:
        """The readings at each of ``seconds`` into the clip, in turn."""
        seconds = list(seconds)
        for at in range(0, len(seconds), DEMO_AT_ONCE):
            part = seconds[at : at + DEMO_AT_ONCE]
            (said,) = ask([{"op": "clip_demo", "when": images.WHEN, "seconds": part,
                            "period": self.period}])  # fmt: skip
            for s, frame in zip(part, said["frames"], strict=True):
                readings = _told(frame)
                for tweak in self.tweaks:
                    tweak(readings, s)
                yield readings


class Speakers:
    """The volume of the default output as the ``audio`` source reads it: on every frame, so a
    bar on the panel follows the volume keys at once."""

    def __init__(self) -> None:
        self.percent = VOLUME_S
        self.muted = False

    def read(self) -> dict[str, Reading]:
        return {
            "audio.volume": Reading(0.0 if self.muted else float(self.percent), "%", "Volume"),
            "audio.muted": Reading(1 if self.muted else 0, "", "Muted"),
        }


# -- changes to the demo's values ---------------------------------------------------------------


def _smooth(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def bump(x: float, rise: float, hold: float, fall: float) -> float:
    """A bump ``x`` seconds after it began: up to 1 over ``rise`` seconds, 1 for ``hold``, down
    to 0 over ``fall``, and 0 before and after."""
    if x < 0:
        return 0.0
    if x < rise:
        return _smooth(x / rise)
    if x < rise + hold:
        return 1.0
    return 1.0 - _smooth((x - rise - hold) / fall)


def surge(
    changes: dict[str, float], at: float, rise: float, hold: float, fall: float,
    every: float | None = None,
) -> Tweak:  # fmt: skip
    """A burst of work: each reading in ``changes`` goes up by its amount and settles again.
    With ``every`` the burst comes again that often, before the clip as well: a clip that is as
    long as that starts where it ends, graphs included (a burst that came once would be in the
    graph at the end and not at the start)."""

    def tweak(readings: dict[str, Reading], seconds: float) -> None:
        x = seconds - at
        share = bump(x % every if every else x, rise, hold, fall)
        for key, amount in changes.items():
            reading = readings.get(key)
            if reading is None or not isinstance(reading.value, int | float):
                continue
            value = reading.value + amount * share
            value = min(100.0, value) if reading.unit == "%" else value
            readings[key] = replace(reading, value=max(0.0, value))

    return tweak


def silence(prefix: str, start: float, end: float) -> Tweak:
    """The readings that begin with ``prefix`` (what plays: ``"media."``) are gone from
    ``start`` to ``end`` seconds."""

    def tweak(readings: dict[str, Reading], seconds: float) -> None:
        if start <= seconds < end:
            for key in [k for k in readings if k.startswith(prefix)]:
                del readings[key]

    return tweak


def agents_story(steps: list[tuple[float, dict[str, dict[str, Any]]]]) -> Tweak:
    """What Claude Code and Codex do (``ai.*``): from each step's second on, what it says of
    each agent it names (``{"claude": {"state": "working", "tool": "Edit"}}``), on top of the
    steps before; ``busy`` and ``since`` go with the state, the tool only while it works. An
    agent that is ``off`` has no more than its state; one named nowhere keeps the demo's."""

    def tweak(readings: dict[str, Reading], seconds: float) -> None:
        said: dict[str, dict[str, Any]] = {}
        began: dict[str, float] = {}
        for at, agents in steps:
            if at > seconds:
                break
            for agent, values in agents.items():
                if "state" in values and values["state"] != said.get(agent, {}).get("state"):
                    began[agent] = at
                said[agent] = {**said.get(agent, {}), **values}
        for agent, values in said.items():
            state = values.get("state", "idle")
            parts = {
                "state": state,
                "busy": {"waiting": 2, "working": 1, "idle": 0, "off": -1}[state],
                "tool": values.get("tool", "") if state == "working" else "",
                "task": values.get("task"),
                "since": 60.0 + seconds - began.get(agent, seconds),
            }
            if state == "off":
                for key in [k for k in readings if k.startswith(f"ai.{agent}.")]:
                    del readings[key]
                parts = {"state": state, "busy": -1}
            for part, value in parts.items():
                key = f"ai.{agent}.{part}"
                if value is None:
                    readings.pop(key, None)
                    continue
                old = readings.get(key)
                readings[key] = replace(old, value=value) if old else Reading(value)

    return tweak


# -- what a clip is recorded in -----------------------------------------------------------------


@contextlib.contextmanager
def workshop() -> Iterator[Path]:
    """What a clip is recorded in: a settings folder of its own (none of the user's themes or
    config, no plugin installed in it: a mod would shadow what the clips show). Yields it."""
    with tempfile.TemporaryDirectory(prefix="readme-media-home-") as home:
        folder = Path(home)
        (folder / "bus").write_text("", encoding="utf-8")  # (the USB bus: nothing on it)
        yield folder


def _environment(home: Path) -> dict[str, str]:
    """The program's environment: the settings folder ``home``, the data in this repository,
    Pillow's libraries (the letters drawn as the downloads draw them), English, and nothing of
    this computer's desktop or panels."""
    english = {"LANG": "en_US.UTF-8", "LANGUAGE": "", "LC_ALL": "", "LC_MESSAGES": ""}
    env = {key: value for key, value in os.environ.items()
           if key not in ("DISPLAY", "WAYLAND_DISPLAY")}  # fmt: skip
    return {
        **env, **libraries(), **english,
        "LIBRE_PANEL_HOME": str(home), "LIBRE_PANEL_DATA": str(ROOT / "data"),
        "XDG_CONFIG_HOME": str(home / "xdg"), "LP_TEST_BUS": str(home / "bus"),
        "LP_LINUX_ROOT": "/no/such/root",
    }  # fmt: skip


class _Program:
    """``libre-panel clip`` (with ``args``) in the settings folder ``home``: what it is fed goes
    in from a thread of its own (so that neither pipe waits on the other), its pictures come out
    here."""

    def __init__(self, home: Path, args: list[str], feed: Iterable[str] = ()) -> None:
        self.process = subprocess.Popen(
            [str(app_program()), "clip", *args], env=_environment(home),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )  # fmt: skip
        self.said: list[bytes] = []
        self.failed: BaseException | None = None
        assert self.process.stdin and self.process.stdout and self.process.stderr
        self.out: IO[bytes] = self.process.stdout
        threading.Thread(target=self.said.extend, args=(self.process.stderr,),
                         daemon=True).start()  # fmt: skip
        self.feeder = threading.Thread(target=self._feed, args=(feed,), daemon=True)
        self.feeder.start()

    def _feed(self, feed: Iterable[str]) -> None:
        stdin = self.process.stdin
        assert stdin is not None
        try:
            for line in feed:
                stdin.write(line.encode("utf-8") + b"\n")
            stdin.close()
        except BrokenPipeError:
            pass  # (the program ended: what it said tells why)
        except BaseException as error:  # a recipe's mistake: told where the pictures stop
            self.failed = error
            with contextlib.suppress(OSError):
                stdin.close()

    def _gone(self) -> RuntimeError:
        if self.failed is not None:
            return RuntimeError(f"the clip's feed failed: {self.failed!r}")
        self.process.wait(30)
        said = b"".join(self.said).decode("utf-8", "replace").strip()
        return RuntimeError(f"libre-panel clip ended ({self.process.returncode}): {said}")

    def line(self) -> str:
        """The next line it says."""
        line = self.out.readline()
        if not line:
            raise self._gone()
        return line.decode("utf-8").rstrip("\n")

    def picture(self, line: str | None = None) -> Image.Image:
        """The next picture (after ``line``, its header, where that was read already)."""
        kind, width, height = (line if line is not None else self.line()).split()
        if kind != "frame":
            raise RuntimeError(f"libre-panel clip: a picture, not {line!r}")
        size = (int(width), int(height))
        data = self.out.read(size[0] * size[1] * 3)
        if len(data) != size[0] * size[1] * 3:
            raise self._gone()
        return Image.frombytes("RGB", size, data)

    def close(self) -> None:
        self.feeder.join(60)
        try:
            code = self.process.wait(60)
        except subprocess.TimeoutExpired:
            self.process.kill()
            raise
        if code != 0 or self.failed is not None:
            raise self._gone()


def _toml(value: Any) -> str:
    """A value as config.toml writes it (texts as JSON's are TOML's too)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, list | tuple):
        return "[" + ", ".join(_toml(item) for item in value) + "]"
    return json.dumps(str(value))


def adapted(data: dict[str, Any], factor: float) -> dict[str, Any]:
    """A theme scaled to ``factor`` times its size, as Libre Panel adapts one (lp-check's
    theme_adapt)."""
    grid = Grid(data)
    width = max(8, round(grid.width * factor))
    height = max(8, round(grid.height * factor))
    orientation = "landscape" if grid.width > grid.height else "portrait"
    (said,) = ask([{
        "op": "theme_adapt", "fonts_dir": str(ROOT / "data" / "fonts"),
        "builtin": str(ROOT / "data" / "themes"),
        "adapt": [{"data": json.dumps(data), "model": "custom", "orientation": orientation,
                   "mode": "scale", "width": width, "height": height}],
    }])  # fmt: skip
    (made,) = said["adapt"]
    if "dict" not in made:
        raise ValueError(f"the theme was not scaled: {made.get('error')}")
    return json.loads(made["dict"])


# -- the stage ----------------------------------------------------------------------------------


class Stage:
    """The panel of one clip: what its main loop shows and everything a recipe may do to it.

    A recipe sets it up before the first frame (:meth:`theme`, :meth:`show`, :meth:`at`,
    :meth:`tweak`); :meth:`frames` then runs the loop and gives the pictures, one at a time.
    """

    def __init__(
        self, home: Path, settings: Settings, zoom: int = 1, period: float | None = None
    ) -> None:
        self.home, self.settings = home, settings
        self.period = period  # the clip's length, if it loops: its values are the same by then
        self.factor = settings.scale * zoom  # the panel's pixels, against its own size
        self.speakers = Speakers()
        self.start = ""  # the theme the clip begins with
        self.modes: dict[str, str] = {}
        self.pages: dict[str, str] = {}
        self.turns: list[tuple[str, float]] = []
        self.transition = "fade"
        self.leave_s: float | None = None
        self.look = ""  # how the volume shows over every screen ("": as the theme's notices)
        # the panels beside the first, of a clip of several (theme, where, the alarm's theme)
        self.beside: list[tuple[str, tuple[int, int], str]] = []
        self.at_mm = (0, 0)  # where the first stands, then
        self.readings: dict[str, Reading] = {}  # what the sources gave for the last frame
        self._events: list[tuple[float, int, Callable[[Stage], None]]] = []
        self._tweaks: list[Tweak] = []
        self._doing: list[list[Any]] = []
        self._made = 0
        self._data: dict[str, dict[str, Any]] = {}

    # -- setting up

    def theme(self, source: str | dict[str, Any], name: str | None = None, **changes: Any) -> str:
        """The id to show a theme by in this clip. ``source`` is the id of a built-in theme, a
        folder with a theme in it, one of the :data:`LAYOUTS`, or theme data; ``changes`` replace
        keys of its data (``toast={...}``). It is scaled to the clip's pixels. A built-in theme
        that comes as it is, at its own size, is shown by its own id."""
        named = isinstance(source, str) and source not in LAYOUTS and theme_folder(source) is None
        if named and not changes and self.factor == 1:
            find_theme(source)  # (it is there: or this says it is not)
            return source  # type: ignore[return-value]
        data, folder = theme_data(source)
        if isinstance(source, str) and source in LAYOUTS and not changes and self.factor == 1:
            name = name or source
        data.update(changes)
        if self.factor != 1:
            data = adapted(data, self.factor)
        self._made += 1
        by_path = isinstance(source, str) and theme_folder(source) is not None
        name = name or (Path(source).name if by_path else f"clip-{self._made}")  # type: ignore[arg-type]
        made = self.home / "themes" / name
        if folder is not None:  # a theme with files of its own: they come along
            shutil.copytree(folder, made, dirs_exist_ok=True)
        made.mkdir(parents=True, exist_ok=True)
        (made / "theme.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
        self._data[name] = data
        return name

    def show(
        self,
        theme: str,
        modes: dict[str, str] | None = None,
        transition: str = "fade",
        turns: list[tuple[str, float]] | None = None,
        pages: dict[str, str] | None = None,
        leave_s: float | None = None,
    ) -> None:
        """The theme the clip begins with, the themes of the modes (``{"game": theme}``), the
        transition config.toml names for a switch, screens in turn (theme, seconds), and the
        pages the panel switches to by itself, switched on (``{"media": theme}``; music's page
        stays ``leave_s`` after a track stops)."""
        self.start, self.modes, self.transition = theme, dict(modes or {}), transition
        self.turns = list(turns or [])
        self.pages, self.leave_s = dict(pages or {}), leave_s

    def at(self, seconds: float, action: Callable[[Stage], None]) -> None:
        """Do ``action(stage)`` just before the frame at ``seconds`` is drawn."""
        self._events.append((seconds, len(self._events), action))

    def tweak(self, change: Tweak) -> None:
        """Change the demo's readings (see :func:`surge`, :func:`silence`)."""
        self._tweaks.append(change)

    # -- what a recipe does while the clip runs

    def turn_volume(self, percent: float) -> None:
        """The volume key: the volume changes now, and the notices service tells it as it tells
        a key pressed (the volume's own band, in the theme's toast style or in its own look)."""
        self.speakers.percent = percent

    def volume_look(self, look: str) -> None:
        """How the volume shows over every screen from now on (Look → Notices → Volume): config.toml
        says it, and the panel reads it again."""
        self.look = look
        self._write_config()
        self._doing.append(["config"])

    def ask_theme(self, theme: str | None, by: str = "clip", transition: str | None = None,
                  priority: int = 0) -> None:  # fmt: skip
        """A service asks for ``theme`` (None: it asks for none any more)."""
        self._doing.append(["theme", theme, by, transition, priority])

    def set_mode(self, mode: str | None, by: str, transition: str | None = None,
                 params: dict[str, Any] | None = None) -> None:  # fmt: skip
        """A service sets the panel's mode (None: back to the normal one)."""
        told = typed(params) if params is not None else None
        self._doing.append(["mode", mode, by, transition, told])

    def publish(self, key: str, value: Any, unit: str = "", label: str = "",
                owner: str | None = None) -> None:  # fmt: skip
        """A service publishes a reading."""
        self._doing.append(["publish", key, typed(value), unit, label, owner])

    def slot(self, seconds: float | None = None, way: str | None = None) -> None:
        """The cards of the theme's first card slot: each ``seconds`` from now on (None: as they
        are), changing ``way`` (a transition; None: as it is)."""
        self._doing.append(["slot", seconds, way])

    def value(self, key: str, default: Any = None) -> Any:
        """A reading as the sources gave it for the last frame (the name the demo gives its
        game, say)."""
        found = self.readings.get(key)
        return default if found is None or found.value is None else found.value

    # -- running

    def _refresh_s(self) -> float:
        """How often the first theme's readings are taken, in seconds."""
        data = self._data.get(self.start) or theme_data(self.start)[0]
        return int(data.get("refresh_ms") or 1000) / 1000

    def _config(self) -> str:
        """config.toml of the clip."""
        s = self.settings
        lines = [f"language = {_toml('en')}", f"theme = {_toml(self.start)}", f"fps = {s.fps}",
                 f"transition = {_toml(self.transition)}", "", "[device]",
                 'driver = "virtual"', 'model = "auto"']  # fmt: skip
        if self.beside:
            lines.append(f"at = {_toml(list(self.at_mm))}")
        lines += ["", "[sensors]", "providers = []", "", "[services]", 'enabled = ["notices"]',
                  "", "[services.notices]", "music = false", "restarted = false"]  # fmt: skip
        if self.look:
            lines.append(f"volume_look = {_toml(self.look)}")
        for name, theme in self.modes.items():
            lines += ["", f"[modes.{name}]", f"theme = {_toml(theme)}"]
        for name, theme in self.pages.items():
            lines += ["", f"[modes.{name}]", f"theme = {_toml(theme)}", "on = true"]
        if self.turns:
            lines += ["", "[sequence]", "on = true"]
            for theme, seconds in self.turns:
                lines += ["", "[[sequence.steps]]", f"theme = {_toml(theme)}",
                          f"seconds = {round(seconds / TURN_UNIT)}"]  # fmt: skip
        for theme, at, alarm in self.beside:
            lines += ["", "[[panels]]", 'driver = "virtual"', 'model = "auto"',
                      f"theme = {_toml(theme)}", f"at = {_toml(list(at))}"]  # fmt: skip
            if alarm:
                lines.append(f"alarm_theme = {_toml(alarm)}")
        return "\n".join(lines) + "\n"

    def _write_config(self) -> None:
        (self.home / "config.toml").write_text(self._config(), encoding="utf-8")

    def _feed(self, prefill: list[float], drawn: list[int], clock_runs: bool) -> Iterator[str]:
        """The lines the program is fed: the readings that fill the graphs' history, then each
        frame's, with what happens just before it."""
        fps = self.settings.fps
        events = sorted(self._events)
        done = 0
        times = [*prefill, *(n / fps for n in drawn)]
        demo = Demo(self._tweaks, self.period).read(times)
        for k, readings in enumerate(demo):
            line: dict[str, Any]
            if k < len(prefill):
                seconds, line = prefill[k], {"prefill": True}
            else:
                n = drawn[k - len(prefill)]
                seconds, line = n / fps, {"show": n >= 0}
                doing = self._doing = []
                if k == len(prefill):  # (the first frame: the loop as the clip has it)
                    doing.append(["pace", TURN_UNIT])
                    if self.leave_s is not None:
                        doing.append(["leave", self.leave_s])
                while n >= 0 and done < len(events) and events[done][0] <= n / fps + 1e-9:
                    events[done][2](self)
                    done += 1
                line["do"] = doing
            readings.update(self.speakers.read())
            self.readings = readings
            line["t"] = START + seconds
            line["wall"] = images.WHEN + (seconds if clock_runs else 0.0)
            line["readings"] = {key: [typed(r.value), r.unit, r.label]
                                for key, r in readings.items()}  # fmt: skip
            yield json.dumps(line)

    def frames(
        self, seconds: float, warmup: float = WARMUP_S, clock_runs: bool = False,
        panels: str | None = None,
    ) -> Iterator[Image.Image]:  # fmt: skip
        """The panel's frames for ``seconds``, after ``warmup`` seconds that are not given (with
        the history its graphs would have by then, taken before the loop draws its first frame,
        as the app takes the readings it has). With ``panels`` (a model): the picture of all the
        panels where they stand, each a panel of that model."""
        fps = self.settings.fps
        drawn = list(range(-round(warmup * fps), round(seconds * fps)))
        every = self._refresh_s()
        first = drawn[0] / fps
        prefill = [first - k * every
                   for k in range(min(PREFILL_MAX, round(PREFILL_S / every)), 0, -1)]  # fmt: skip
        self._write_config()
        program = _Program(self.home, ["--panels", panels] if panels else [],
                           self._feed(prefill, drawn, clock_runs))  # fmt: skip
        try:
            for n in drawn:
                if n >= 0:
                    yield program.picture()
            program.close()
        finally:
            if program.process.poll() is None:
                program.process.kill()
                program.process.wait()


# -- the picture of a clip ----------------------------------------------------------------------


class Bezel:
    """The device frame of tools/readme_images.py (bezel, soft shadow, glass, on its dark
    background) around a screen of one size, made once: a frame is pasted in. The pictures
    use ``device`` and ``background`` of that tool; what the glass shows of them is read off
    two probes (a black and a white screen) rather than copied."""

    def __init__(self, size: tuple[int, int]) -> None:
        sw, sh = size
        border = max(4, min(24, round(0.0375 * sh)))
        across, down = round(1.2 * border), round(1.8 * border)  # room round the bezel
        margin = images.shadow_margin(border)  # what device() leaves round it for the shadow
        dark = images.device(Image.new("RGB", size, (0, 0, 0)), border)
        white = images.device(Image.new("RGB", size, (255, 255, 255)), border)
        glass = (margin + border, margin + border, margin + border + sw, margin + border + sh)
        black_glass, white_glass = (d.crop(glass).getchannel("R") for d in (dark, white))
        # inside the glass a white screen shows white and a black one only the reflection
        self.mask = ImageChops.subtract(white_glass, black_glass).point(lambda v: 255 if v else 0)
        self.sheen = Image.composite(black_glass, Image.new("L", size, 0), self.mask)
        self.white = Image.new("RGB", size, (255, 255, 255))
        total = (sw + 2 * border + 2 * across, sh + 2 * border + 2 * down)
        canvas = images.background(*total, glow=(0.5, 1.0)).convert("RGBA")
        canvas.alpha_composite(dark, (across - margin, down - margin))
        self.base = canvas.convert("RGB")
        self.origin = (across + border, down + border)
        self.size = total

    def frame(self, screen: Image.Image) -> Image.Image:
        lit = Image.composite(self.white, screen.convert("RGB"), self.sheen)
        out = self.base.copy()
        out.paste(lit, self.origin, self.mask)
        return out


class Presenter:
    """From a frame of the panel to a picture of the clip: cropped (a close-up), then in the
    device frame, unless the clip is bare."""

    def __init__(self, size: tuple[int, int], crop: tuple[int, int, int, int] | None, bare: bool):
        self.box = (
            None if crop is None else (crop[0], crop[1], crop[0] + crop[2], crop[1] + crop[3])
        )
        shown = size if crop is None else (crop[2], crop[3])
        self.bezel = None if bare else Bezel(shown)

    def __call__(self, frame: Image.Image) -> Image.Image:
        if self.box is not None:
            frame = frame.crop(self.box)
        return frame if self.bezel is None else self.bezel.frame(frame)


# -- recipes ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Focus:
    """A close-up of what a theme has of some kinds of widget: the box around all the widgets
    of these ``kinds`` (a widget's type, or the module of a module), with this much room on each
    side. So a close-up finds its place in whatever theme it is made of."""

    kinds: tuple[str, ...]
    left: int = 0
    top: int = 0
    right: int = 0
    bottom: int = 0

    def box(self, data: dict[str, Any]) -> tuple[int, int, int, int] | None:
        """(x, y, width, height) in the panel's own pixels; None if the theme has none of them."""
        theme = grid = Grid(data)
        found = []
        for widget in data.get("widgets", []):
            kind = widget.get("module") if widget.get("type") == "module" else widget.get("type")
            if kind not in self.kinds:
                continue
            if widget.get("type") == "module":
                found.append(
                    grid.box(
                        widget["col"], widget["row"], widget.get("cols", 1), widget.get("rows", 1)
                    )
                )
            elif all(key in widget for key in ("x", "y", "w", "h")):
                found.append([widget["x"], widget["y"], widget["w"], widget["h"]])
        if not found:
            return None
        x0 = max(0, min(x for x, _y, _w, _h in found) - self.left)
        y0 = max(0, min(y for _x, y, _w, _h in found) - self.top)
        x1 = min(theme.width, max(x + w for x, _y, w, _h in found) + self.right)
        y1 = min(theme.height, max(y + h for _x, y, _w, h in found) + self.bottom)
        return x0, y0, x1 - x0, y1 - y0


@dataclass(frozen=True)
class PanelClip:
    """One clip: what it shows and for how long, and the function that sets the stage.

    ``themes`` are the themes it shows, by default: ids of built-in themes, or of the
    :data:`LAYOUTS`; ``record`` takes others in their place, and the recipe asks the stage for
    what it needs of them (a card slot, a graph) without naming one."""

    about: str
    themes: tuple[str, ...]
    seconds: float
    setup: Callable[[Stage, tuple[str, ...]], None]
    poster: float = 0.0  # the second the poster is taken at
    zoom: int = 1  # drawn at this many times the panel's size: a small panel or a close-up is sharp
    # of the panel's own pixels: (x, y, width, height), or a :class:`Focus` that finds the box in
    # the theme; a theme that has none of them is shown whole
    crop: tuple[int, int, int, int] | Focus | None = None
    framed: bool = True  # in the device frame (a close-up is a detail: it is not)
    loop: str = "fade"  # "fade": the end is blended into the start; "cut": it is seamless as it is
    # Whether the demo's values come round with the clip, so that its end leads on into its start
    # as it is. Only for a screen without graphs: one that shows much more time than the clip
    # would show the same stretch again and again. Else the end gives way to the start.
    periodic: bool = False
    warmup: float = WARMUP_S
    # Whether the time of day runs with the clip (the seconds roll, a second hand glides); else
    # it stands still at the moment of the README's pictures.
    clock_runs: bool = False

    @property
    def kind(self) -> str:
        return "panel"


def record(
    name: str,
    clip: PanelClip,
    settings: Settings,
    folder: Path,
    themes: tuple[str, ...] | None = None,
) -> Recorded:
    """Run ``clip`` (with ``themes`` instead of its own) and keep its frames in ``folder``."""
    themes = themes or clip.themes
    if len(themes) != len(clip.themes):
        raise ValueError(f"{name} shows {len(clip.themes)} theme(s), not {len(themes)}")
    box = clip.crop
    if isinstance(box, Focus):
        box = box.box(theme_data(themes[0])[0])
    whole = box is None and clip.crop is not None  # a close-up of what the theme lacks
    with workshop() as home:
        stage = Stage(home, settings, 1 if whole else clip.zoom,
                      clip.seconds if clip.periodic else None)  # fmt: skip
        clip.setup(stage, themes)
        fps = settings.fps
        overlap = round(FADE_S * fps) if clip.loop == "fade" else 0
        total = clip.seconds + overlap / fps
        sink = FrameSink(folder, fps)
        present: Presenter | None = None
        crop = tuple(round(v * stage.factor) for v in box) if box else None
        bare = settings.bare or not clip.framed
        for frame in stage.frames(total, clip.warmup, clip.clock_runs):
            if present is None:
                present = Presenter(frame.size, crop, bare)  # type: ignore[arg-type]
            sink.add(present(frame))
        frames = sink.frames()
    if overlap:
        frames = fade_loop(frames, overlap)
    return Recorded(frames, min(frames.count - 1, round(clip.poster * fps)))


def work_burst(stage: Stage, at: float = 2.0) -> None:
    """A burst of work: the loads, temperatures, power and clocks go up and settle, and the fans
    run faster (what a theme shows of them is its own business). In a clip that comes round, it
    comes round with it."""
    stage.tweak(surge({
        "cpu.load": 50, "cpu.temp": 15, "cpu.power": 42, "cpu.freq": 380,
        "gpu.load": 30, "gpu.temp": 9, "gpu.power": 90, "gpu.freq": 150,
        "fan.lpc.fan_1": 160, "fan.lpc.fan_2": 420, "fan.lpc.fan_3": 140,
        "fan.lpc.fan_4": 200, "gpu.fan": 520,
    }, at=at, rise=1.1, hold=0.8, fall=2.4, every=stage.period))  # fmt: skip


# Video mode: what is drawn on every frame. The history graphs scroll, the rings and numbers glide
# to each new reading, and the fans turn faster as they speed up.
def video_mode(stage: Stage, themes: tuple[str, ...]) -> None:
    stage.show(stage.theme(themes[0]))
    work_burst(stage)


# The volume key, twice as many presses up as down: the band rolls down from its glowing edge,
# the number counts and the bar glides.
def volume_band(stage: Stage, themes: tuple[str, ...]) -> None:
    toast = {"style": "band", "anchor": "bottom", "seconds": 2.0}
    stage.show(stage.theme(themes[0], toast=toast))
    start = stage.speakers.percent
    up = [(0.8 + 0.1 * k, start + 2 * (k + 1)) for k in range(12)]
    down = [(2.4 + 0.1 * k, start + 24 - 2 * (k + 1)) for k in range(12)]
    for at, percent in up + down:
        stage.at(at, lambda s, percent=percent: s.turn_volume(percent))


# The volume over a screen in each of its looks (Look → Notices → Volume): Bar, Levels, Ring and
# Large one after the other, each turned up and down a little and gone before the next comes.
VOLUME_LOOKS = ("bar", "levels", "ring", "big")
LOOK_EVERY_S = 2.5  # one look's turn: up, down, its hold of 1.2 s and its fade


def volume_looks(stage: Stage, themes: tuple[str, ...]) -> None:
    stage.show(stage.theme(themes[0], toast={"seconds": 1.2}))
    start = stage.speakers.percent
    for n, look in enumerate(VOLUME_LOOKS):
        begin = 0.5 + LOOK_EVERY_S * n
        stage.at(begin - 0.1, lambda s, look=look: s.volume_look(look))
        up = [(begin + 0.07 * k, start + 3 * (k + 1)) for k in range(6)]
        down = [(begin + 0.6 + 0.07 * k, start + 18 - 3 * (k + 1)) for k in range(4)]
        for at, percent in up + down:
            stage.at(at, lambda s, percent=percent: s.turn_volume(percent))


# Encore: what plays, the cover's colours behind it, the time rolling on, and the volume turned
# up and then down: its levels rise and fall (the screen shows the volume itself, so no notice).
def encore_screen(stage: Stage, themes: tuple[str, ...]) -> None:
    stage.show(stage.theme(themes[0]))
    start = stage.speakers.percent
    up = [(1.0 + 0.12 * k, start + 4 * (k + 1)) for k in range(10)]
    down = [(3.6 + 0.12 * k, start + 40 - 4 * (k + 1)) for k in range(10)]
    for at, percent in up + down:
        stage.at(at, lambda s, percent=percent: s.turn_volume(percent))


# A screen as it runs: its symbols move, its clock rolls, its second hand glides.
def screen(stage: Stage, themes: tuple[str, ...]) -> None:
    stage.show(stage.theme(themes[0]))


# Screens in turn: each shows for its time, then the next comes with the tube, by itself.
def screens_in_turn(stage: Stage, themes: tuple[str, ...]) -> None:
    names = [stage.theme(theme) for theme in themes]
    stage.show(names[0], transition="tube", turns=[(name, 2.6) for name in names])


# Libre Panel's own screens on the bar, one after the other as screens in turn go (a beam of
# light across): the README's tour of its themes. Each a moment, and round again. It begins in
# the first one's time (which runs through the warm-up too), so that the last beam, back to the
# first, is over before the clip ends, and its end is blended into its start.
TOUR_S = 2.6


def themes_tour(stage: Stage, themes: tuple[str, ...]) -> None:
    names = [stage.theme(theme) for theme in themes]
    turns = [(name, TOUR_S) for name in names]
    turns[0] = (names[0], WARMUP_S + TOUR_S * 0.5 - 0.2)
    stage.show(names[0], transition="tube", turns=turns)


@dataclass(frozen=True)
class StartClip:
    """The panel's own screens (lp_render::boot): the boot logo of a panel (by its model's id),
    then its standby clip, round and round, in the device frame; then the clips of the other
    ``looks``, ``look_s`` each, one fading into the next."""

    about: str
    themes: tuple[str, ...]  # the panel's model
    seconds: float
    logo_s: float = 1.6
    poster: float = 3.0
    looks: tuple[str, ...] = ()
    look_s: float = 2.2
    fade_s: float = 0.3

    @property
    def kind(self) -> str:
        return "panel"


@contextlib.contextmanager
def start_screens(
    model: str, scale: float, looks: Iterable[str]
) -> Iterator[tuple[int, list[str], Image.Image, Iterator[tuple[str, list[Image.Image]]]]]:
    """The panel's own screens as the panel of ``model`` shows them, at ``scale`` (``libre-panel
    clip --start``): the standby clips' pictures a second, every look there is (the panel's own
    first), the boot logo, and each of ``looks`` with the pictures of its standby clip (one loop
    of them, a look at a time)."""
    looks = list(looks)
    with workshop() as home:
        program = _Program(home, ["--start", model, repr(scale), *looks])
        try:
            kind, rate = program.line().split()
            every = program.line().split()
            if kind != "rate" or every[0] != "looks":
                raise RuntimeError(f"libre-panel clip --start: {kind} {every}")
            logo = program.picture()

            def clips() -> Iterator[tuple[str, list[Image.Image]]]:
                for look in looks:
                    kind, named, count = program.line().split()
                    if (kind, named) != ("look", look):
                        raise RuntimeError(f"libre-panel clip --start: {kind} {named}")
                    yield look, [program.picture() for _ in range(int(count))]
                program.close()

            yield int(rate), every[1:], logo, clips()
        finally:
            if program.process.poll() is None:
                program.process.kill()
                program.process.wait()


def record_start(
    name: str,
    clip: StartClip,
    settings: Settings,
    folder: Path,
    themes: tuple[str, ...] | None = None,
) -> Recorded:
    """The boot logo for ``logo_s``, then the standby clip at its own rate, as the panel shows
    them; then the other looks' clips."""
    model = (themes or clip.themes)[0]
    scale = min(1.0, settings.scale)  # (drawn at the clip's size: quicker than made smaller)
    fps = settings.fps
    sink = FrameSink(folder, fps)
    with start_screens(model, scale, (DEFAULT_LOOK, *clip.looks)) as (rate, _every, logo, clips):
        present = Presenter(logo.size, None, settings.bare)
        sink.add(present(logo), round(clip.logo_s * fps))
        own = clip.seconds - clip.logo_s - len(clip.looks) * clip.look_s
        seconds = {DEFAULT_LOOK: own, **{look: clip.look_s for look in clip.looks}}
        fade = round(clip.fade_s * fps)
        before: list[Image.Image] = []  # the last pictures of the look before, to fade from
        for look, loop in clips:
            count = round(seconds[look] * fps)
            for n in range(count):
                picture = loop[int(n / fps * rate) % len(loop)]
                if n < fade and before:
                    picture = Image.blend(before[n], picture, (n + 1) / (fade + 1))
                sink.add(present(picture))
            before = [loop[int((count + k) / fps * rate) % len(loop)] for k in range(fade)]
    frames = sink.frames()
    return Recorded(frames, min(frames.count - 1, round(clip.poster * fps)))


# Music's page: nothing plays, then a track starts and the panel opens Encore by itself (a line
# of light), the volume is turned up and down there, and when the track stops it goes back.
def music_page(stage: Stage, themes: tuple[str, ...]) -> None:
    home, music = (stage.theme(theme) for theme in themes)
    stage.show(home, transition="tube", pages={"media": music}, leave_s=0.6)
    stage.tweak(silence("media.", -1000.0, 1.0))  # nothing plays: the theme of every day
    stage.tweak(silence("media.", 7.0, 1000.0))  # the track stops
    stage.tweak(silence("ai.", -1000.0, 1000.0))  # (the demo's AI tools would take the card)
    start = stage.speakers.percent
    steps = [(3.2 + 0.1 * k, start + 4 * (k + 1)) for k in range(8)]
    steps += [(4.8 + 0.1 * k, start + 32 - 4 * (k + 1)) for k in range(5)]
    for at, percent in steps:
        stage.at(at, lambda s, percent=percent: s.turn_volume(percent))


# After a game: the memory service puts its round report on the panel (a line of light).
def round_report(stage: Stage, themes: tuple[str, ...]) -> None:
    home, report = (stage.theme(theme) for theme in themes)
    stage.show(home, transition="tube")
    finished = demo_report()  # the round that just ended, as the service tells it
    stage.tweak(lambda readings, seconds: readings.update(finished))
    stage.at(1.0, lambda s: s.ask_theme(report, by="memory", transition="tube", priority=10))


# One screen to the next, as an autopilot or a plugin switches them: a line ignites at
# mid-height and opens, the new screen already live between its edges; back again after.
def theme_tube(stage: Stage, themes: tuple[str, ...]) -> None:
    first, second = (stage.theme(theme) for theme in themes)
    stage.show(first, transition="tube")
    stage.at(1.0, lambda s: s.ask_theme(second, by="clip", transition="tube"))
    stage.at(3.9, lambda s: s.ask_theme(None, by="clip", transition="tube"))


# A game starts and ends: the entrance closes the screen to a line, opens a band with the game's
# name, and opens onto the screen of the game; at the end the other way round.
def game_entrance(stage: Stage, themes: tuple[str, ...]) -> None:
    home, game = (stage.theme(theme) for theme in themes)
    stage.show(home, modes={"game": game})

    def play(stage: Stage, kind: str) -> None:
        # the name the demo gives its game; a logo too, if a source publishes one
        params = {"title": stage.value("game.app", "Starfall"), "kind": kind, "logo": "game.logo"}
        stage.set_mode("game" if kind == "start" else None, by="game", transition="entrance",
                       params=params)  # fmt: skip

    stage.at(0.8, lambda s: play(s, "start"))
    stage.at(4.6, lambda s: play(s, "end"))


# A card slot: what plays, until nothing does; then the weather, with a line of light that
# ignites at the top edge between the two (and back again). What plays is taken at a reading,
# once a second: the card changes at the second after it stops.
def card_ignition(stage: Stage, themes: tuple[str, ...]) -> None:
    stage.show(stage.theme(themes[0]))
    stage.tweak(silence("media.", 2.0, 5.0))
    stage.tweak(silence("ai.", -1000.0, 1000.0))  # (the demo's AI tools would come first)


# Lumen's cards in turn, each a moment (2.3 s: in a clip the time of a card is cut short, a
# card's own is five seconds at least), and from one to the next another way each time: a fade,
# the new card pushing the old one out to the left, upwards, and a beam of light that wipes
# across. After four changes the first card is on show again (a card changes at the reading
# after its time is up), and the clip ends a moment later: it loops.
CARD_TURN_S = 2.3
CARD_WAYS = ("fade", "slide", "rise", "wipe")


def card_turns(stage: Stage, themes: tuple[str, ...]) -> None:
    data, _ = theme_data(themes[0])
    widgets = [
        {**w, "items": "playback\nforecast\nanalog\ndate", "order": "turns"}
        if w.get("type") == "module" and w.get("module") in ("cards",) else w
        for w in data["widgets"]
    ]  # fmt: skip
    stage.show(stage.theme(themes[0], widgets=widgets))
    stage.at(0.0, lambda s: s.slot(CARD_TURN_S, CARD_WAYS[0]))  # (the cards' time, from now on)
    for n, name in enumerate(CARD_WAYS[1:], start=1):
        stage.at(n * CARD_TURN_S + 0.3, lambda s, name=name: s.slot(None, name))


# AI activity on Lumen, without the AI screen: the weather, until Claude Code sets to work; then
# the AI card rolls down out of the line of light by itself, Claude Code alone on it (Codex does
# not run): its session, the tools at work, and at last waiting for you. Nothing plays.
def ai_card(stage: Stage, themes: tuple[str, ...]) -> None:
    stage.show(stage.theme(themes[0]))
    stage.tweak(silence("media.", -60.0, 60.0))
    stage.tweak(agents_story([
        (-60.0, {"claude": {"state": "idle", "task": "Cards on the panel"},
                 "codex": {"state": "off"}}),
        (1.0, {"claude": {"state": "working", "tool": "Read"}}),
        (3.2, {"claude": {"tool": "Edit"}}),
        (4.8, {"claude": {"tool": "Bash"}}),
        (6.4, {"claude": {"state": "waiting"}}),
    ]))  # fmt: skip


# The AI screen on its own: Claude Code and Codex side by side, each sign moving as it works or
# waits for you, the tools changing, Claude Code's session under them.
def agents_screen(stage: Stage, themes: tuple[str, ...]) -> None:
    stage.show(stage.theme(themes[0]))
    stage.tweak(agents_story([
        (-60.0, {"claude": {"state": "working", "tool": "Read", "task": "Cards on the panel"},
                 "codex": {"state": "working", "tool": "shell"}}),
        (1.4, {"claude": {"tool": "Edit"}}),
        (2.8, {"codex": {"state": "waiting"}}),
        (4.2, {"claude": {"tool": "Bash"}}),
        (5.8, {"claude": {"state": "waiting"},
               "codex": {"state": "working", "tool": "apply_patch"}}),
    ]))  # fmt: skip


# The small panel: a burst of work fills the rings, the history scrolls, and what plays in the
# corner gives way to the weather and comes back (a card on a small panel is small).
def small_panel(stage: Stage, themes: tuple[str, ...]) -> None:
    stage.show(stage.theme(themes[0]))
    work_burst(stage, at=0.6)
    stage.tweak(silence("media.", 4.0, 7.0))


# A ring that fills and a fan that runs up, close: drawn at twice the panel's size, cropped.
def close_up(stage: Stage, themes: tuple[str, ...]) -> None:
    stage.show(stage.theme(themes[0]))
    work_burst(stage, at=1.0)


# The clips show Libre Panel's own screens: Lumen on the bars, Libre Default on the small panel,
# Studio for the notice. Their graphs show a long stretch of time, so none of the clips loops on
# data that comes round (see ``periodic``): the end gives way to the start.
CLIPS: dict[str, PanelClip] = {
    "video-mode": PanelClip(
        "video mode: lit curves scrolling, rings and numbers gliding, fans running up",
        ("lumen",), 8.0, video_mode, poster=4.0,
    ),
    "card-ignition": PanelClip(
        "the card slot changes from playback to the weather and back, with its line of light",
        ("lumen",), 6.6, card_ignition, poster=3.4,
    ),
    "card-turns": PanelClip(
        "the card slot's cards in turn, each a moment, and four ways from one to the next",
        ("lumen",), 4 * CARD_TURN_S + 1.2, card_turns, poster=CARD_TURN_S * 1.5,
    ),
    "ai-card": PanelClip(
        "AI activity on Lumen by itself: Claude Code sets to work and its card rolls in",
        ("lumen",), 8.4, ai_card, poster=4.0,
    ),
    "agents": PanelClip(
        "the AI screen: Claude Code and Codex at work and waiting for you, side by side",
        ("agents",), 7.2, agents_screen, poster=3.6,
    ),
    "small-panel": PanelClip(
        "the small panel: lit rings filling, a history that scrolls, a card that changes",
        ("libre-default",), 8.0, small_panel, poster=1.6, zoom=2,
    ),
    "volume-band": PanelClip(
        "the volume key: a notice in the band style, its number counting and its bar gliding",
        ("studio",), 6.8, volume_band, poster=2.2,
    ),
    "volume-looks": PanelClip(
        "the volume over the screen in its four looks: Bar, Levels, Ring and Large",
        ("lumen",), 10.6, volume_looks, poster=5.6,
    ),
    "theme-tube": PanelClip(
        "one screen to the next with the tube, and back again",
        ("lumen", "studio"), 6.6, theme_tube, poster=1.5,
    ),
    "game-entrance": PanelClip(
        "a game starts and ends with the entrance: the screen closes to a line, a band opens",
        ("lumen", "arena"), 7.8, game_entrance, poster=4.2,
    ),
    "close-up-rings": PanelClip(
        "a close-up of the rings: lit arcs that fill, numbers that glide, at twice the size",
        ("lumen",), 6.0, close_up, poster=3.0, zoom=2, framed=False,
        crop=Focus(("instrument", "ring", "gauge"), 14, 14, 14, 32),
    ),
    "close-up-curves": PanelClip(
        "a close-up of the curves and the fans: a lit history that scrolls, fans that run up",
        ("lumen",), 6.0, close_up, poster=3.0, zoom=2, framed=False,
        crop=Focus(("graph", "fan"), 20, 44, 20, 22),
    ),
    "encore": PanelClip(
        "Encore: what plays, the cover's colours behind it, the time rolling, the volume rising",
        ("encore",), 7.0, encore_screen, poster=2.4, periodic=True, clock_runs=True,
    ),
    "nimbus": PanelClip(
        "Nimbus: the weather's symbols moving, the next days, the moon, a small clock rolling",
        ("nimbus",), 6.0, screen, poster=2.0, periodic=True, clock_runs=True,
    ),
    "terra": PanelClip(
        "Terra: the earth as it is now, turning, the line between day and night soft with dusk,"
        " the time in four other places",
        ("terra",), 8.0, screen, poster=3.0, clock_runs=True,
    ),
    "almanac": PanelClip(
        "Almanac to Almanac Analog and back: rolling digits, then a second hand that glides",
        ("almanac", "almanac-analog"), 6.6, theme_tube, poster=2.6, clock_runs=True,
    ),
    "music-page": PanelClip(
        "music's page: a track starts and Encore opens by itself; the volume; back when it stops",
        ("lumen", "encore"), 9.0, music_page, poster=4.2, clock_runs=True,
    ),
    "round-report": PanelClip(
        "after a game: the round report opens, in Arena's colours and cards",
        ("lumen", "round-report"), 6.0, round_report, poster=4.5, loop="cut",
    ),
    "screens-in-turn": PanelClip(
        "screens in turn: Almanac, Nimbus and Encore one after another, each for its time",
        ("almanac", "nimbus", "encore"), 7.8, screens_in_turn, poster=1.2, loop="cut",
        clock_runs=True,
    ),
    "themes-tour": PanelClip(
        "Libre Panel's own screens on the bar, one after another through the tube",
        ("lumen", "studio", "encore", "nimbus", "almanac", "terra", "arena"), 7 * TOUR_S,
        themes_tour,
        poster=TOUR_S * 0.25, clock_runs=True,
    ),
}  # fmt: skip

# -- several panels at once ---------------------------------------------------------------------


GAP_MM = 30  # between two panels of a clip, as more-panels.png has them


@dataclass(frozen=True)
class PanelsClip:
    """Several panels at once, where they stand: the first panel's loop and a loop for each panel
    beside it, on the first panel's readings and in its beat, as the app runs them, in the
    picture *Picture of all panels* saves.

    ``themes`` are the first panel's theme, then each other panel's; ``alarm`` the theme each
    panel beside the first shows while a sensor is at its critical limit ("": its own)."""

    about: str
    themes: tuple[str, ...]
    seconds: float
    setup: Callable[[Stage, tuple[str, ...]], None]
    # where each panel stands, by column and row: one panel's size and a gap of 30 mm apart
    places: tuple[tuple[int, int], ...]
    model: str = "turing-9.2-usb"
    alarm: tuple[str, ...] = ()
    poster: float = 0.0
    loop: str = "fade"
    warmup: float = WARMUP_S

    @property
    def kind(self) -> str:
        return "panels"


def alarm_at(stage: Stage, start: float, end: float) -> None:
    """A sensor at its critical limit from ``start`` to ``end`` seconds: the processor's
    temperature over 90 °C, and the notices service's ``alarm`` (as the app's would say it)."""
    stage.tweak(surge({"cpu.temp": 34, "cpu.load": 40}, at=start - 0.6, rise=0.6,
                      hold=end - start, fall=1.0))  # fmt: skip

    def say(on: int) -> Callable[[Stage], None]:
        return lambda s: s.publish("alarm", on, "", "Alarm", owner="notices")

    stage.at(start, say(1))
    stage.at(end, say(0))


def pair_version(theme: str) -> str | None:
    """The version of ``theme`` for a panel with a companion beside it, if it has one (lp-check's
    module_parts, as the app finds it)."""
    (said,) = ask([{"op": "module_parts", "layouts": [], "pairs": [theme],
                    "builtin": str(ROOT / "data" / "themes"),
                    "user": str(ROOT / "no-such-folder")}])  # fmt: skip
    return said["pairs"][theme]


# Two bars, one above the other: Lumen (its pair version), and Lumen Companion beside it. A burst
# of work goes up on both at once, the same fans turning in step; then the processor gets too hot:
# both edges pulse red, and the second shows High Contrast until it has cooled.
def more_panels(stage: Stage, themes: tuple[str, ...]) -> None:
    # as the app does while a companion runs beside it: the first panel's pair version (named
    # here: a theme made smaller for the clip has a name of its own, which the app does not pair)
    stage.show(stage.theme(pair_version(themes[0]) or themes[0]))
    work_burst(stage, at=0.8)
    alarm_at(stage, 3.6, 6.8)


def millimetres(model: str) -> tuple[float, float]:
    """The size of ``model``'s picture in millimetres, landscape (as the app has it)."""
    found = images.parts(((model, "landscape"),))["models"][model]
    width, height = found["native"][1], found["native"][0]  # (the longer side across)
    per = found["inch"] * 25.4 / math.hypot(width, height)
    return width * per, height * per


def record_panels(
    name: str,
    clip: PanelsClip,
    settings: Settings,
    folder: Path,
    themes: tuple[str, ...] | None = None,
) -> Recorded:
    """Run ``clip``'s panels in step, frame by frame, and keep their pictures in ``folder``."""
    themes = themes or clip.themes
    if len(themes) != len(clip.themes):
        raise ValueError(f"{name} shows {len(clip.themes)} theme(s), not {len(themes)}")
    w, h = millimetres(clip.model)

    def where(place: tuple[int, int]) -> tuple[int, int]:  # (in whole millimetres)
        return round(place[0] * (w + GAP_MM)), round(place[1] * (h + GAP_MM))

    with workshop() as home:
        stage = Stage(home, settings)
        clip.setup(stage, themes)
        stage.at_mm = where(clip.places[0])
        for n, theme in enumerate(themes[1:]):
            alarm = clip.alarm[n] if n < len(clip.alarm) else ""
            stage.beside.append((stage.theme(theme), where(clip.places[n + 1]),
                                 stage.theme(alarm) if alarm else ""))  # fmt: skip
        fps = settings.fps
        overlap = round(FADE_S * fps) if clip.loop == "fade" else 0
        sink = FrameSink(folder, fps)
        for picture in stage.frames(clip.seconds + overlap / fps, clip.warmup,
                                    panels=clip.model):  # fmt: skip
            sink.add(picture)
        recorded = sink.frames()
    if overlap:
        recorded = fade_loop(recorded, overlap)
    return Recorded(recorded, min(recorded.count - 1, round(clip.poster * fps)))


PANELS_CLIPS: dict[str, PanelsClip] = {
    "more-panels": PanelsClip(
        "two panels at once: the same beat on both, then an alarm that turns both edges red",
        ("lumen", "lumen-companion"), 9.0, more_panels, places=((0, 0), (0, 1)),
        alarm=("contrast",), poster=3.0,
    ),
}  # fmt: skip


# The panel's own screens: its boot logo, then its standby clip, as the panel shows them.
START_CLIPS: dict[str, StartClip] = {
    "start-screen": StartClip(
        "the panel's own screens: the boot logo, then the standby clip's light running out, then"
        " the clips of the other looks",
        ("turing-9.2-usb",), 5.0 + 6 * 2.2, poster=2.0,
        looks=("aurora", "pulse", "circuit", "night", "levels", "dark"),
    ),
}  # fmt: skip

"""For as long as the Python app is still here (docs/RUST.md, R12 the switch): what the
comparisons' cases take from it, asked of it once more and written down, so that tests/golden's
given/ holds it for each system. With ``LP_GIVEN_OUT`` naming a folder, golden.given and
golden.told ask the Python app (as the tests themselves did before they read what was kept) and
write what it says to <folder>/<system>/; the tests' answers are checked against the kept ones
all the same, which shows that what was written makes the cases the Python app answered. CI
keeps the folder as a file of its run. This module goes with the Python app."""

from __future__ import annotations

import base64
import io
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import golden

OUT = os.environ.get("LP_GIVEN_OUT") or None
_tapes: dict[str, dict[str, dict[str, list[Any]]]] = {}  # by test file, test, what was asked
_made: dict[str, Any] = {}


def _folder() -> Path:
    assert OUT is not None
    folder = Path(OUT) / golden.FOLDER.name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _write(name: str, value: Any, indent: int | None = 1) -> None:
    text = json.dumps(value, indent=indent, ensure_ascii=False)
    (_folder() / f"{name}.json").write_text(text + "\n", encoding="utf-8")


# -- tables and readings ----------------------------------------------------------------------


def _looks() -> Any:
    from libre_panel import design

    return design.LOOKS


def _icons() -> Any:
    from libre_panel import icons
    from libre_panel.render.lit import WEATHER_ICONS

    return {"names": list(icons.ICON_NAMES), "weather": list(WEATHER_ICONS)}


DEMO_HISTORY = 400  # (the numbers of each history kept: no graph of the app's themes shows more)


def _demo() -> Any:
    """sensors/demo.py's snapshot of the moment the comparisons draw it at: its readings in
    their order, the last numbers of each history, its pictures."""
    from libre_panel.sensors.demo import demo_snapshot

    made = demo_snapshot(fixed_time=1_760_000_000.0)
    assert all(r.stamp is None and not r.device for r in made.readings.values())
    now = made.now
    return {
        "now": [now.year, now.month, now.day, now.hour, now.minute, now.second, now.microsecond],
        "readings": [[key, r.value, r.unit, r.label, r.origin]
                     for key, r in made.readings.items()],
        "history": {key: [float(v) for v in list(values)[-DEMO_HISTORY:]]
                    for key, values in made.history.items()},
        "images": {key: [image.mode, png(image)] for key, image in made.images.items()},
    }  # fmt: skip


def _themes() -> Any:
    """What the app's own themes hold, as its renderer has their widgets: the sensors their
    graphs draw, and the themes with light blurred in floating point (a glow, a shadow, the lit
    finish's)."""
    from libre_panel.render.renderer import Renderer
    from libre_panel.theme.model import load_theme

    themes = Path(__file__).resolve().parent.parent / "data" / "themes"
    graphs: set[str] = set()
    soft = []
    for folder in sorted(p.parent for p in themes.glob("*/theme.json")):
        widgets = Renderer(load_theme(folder), preview=True).widgets
        assert all(w["history"] <= DEMO_HISTORY for w in widgets if w["type"] == "graph")
        graphs |= {w["sensor"] for w in widgets if w["type"] == "graph"}
        if any(w.get("glow") or w.get("shadow") or w.get("finish") == "lit" for w in widgets):
            soft.append(folder.name)
    return {"graphs": sorted(graphs), "soft": soft}


def _download_edges() -> Any:
    """Download cards whose texts just fit, found by download.py's own measures: of the
    smallest fonts, a source that just comes or goes beside the heading; long amounts beside the
    second reading's unit, a pixel or less off it or on it; names exactly as long as their
    room, wide after the heading and the source, and stacked. Each a width, a height and the
    download's state."""
    import tempfile

    from libre_panel.render import download, light
    from libre_panel.render.renderer import Renderer
    from libre_panel.theme.model import load_theme

    folder = Path(tempfile.mkdtemp())
    data = {"format": "libre-panel-theme/1", "name": "measures",
            "display": {"width": 64, "height": 48}, "widgets": []}  # fmt: skip
    (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
    renderer = Renderer(load_theme(folder), preview=True)

    def font(size: float) -> Any:
        return renderer.font("", max(6, round(size)))

    word = download.heading("downloading").upper()
    cases: list[tuple[int, int, dict[str, Any]]] = []
    base = {"phase": "downloading", "source": "steam", "rate": 2.5e6, "remaining_s": 75}
    for h in (10, 60):  # (the source: in 6 px fonts, where g is 0.15)
        cases += [(w, h, {**base, "name": "a.iso"}) for w in range(8, 121)]
    for total in (1e18, 2.5e18, 7e18, 4e20, 1e22, 3e24, 1e27, 5e29, 1e32, 7.7e33):
        amount = "≈ " + download.of_total(0.45 * total, total)
        for w in range(312, 1001):
            lay = download.layout(w, 130)
            g = lay.g
            unit, small = font(19 * g), font(20 * g)
            edge = download.VALUE_X * g + 6 * g + 14 * g
            off = lay.right - small.getlength(amount) - edge - unit.getlength("MB/s")
            if abs(off) <= 2 + abs(unit.getlength("MB/s") - unit.getlength("KB/s")) + 4 * g:
                cases.append((w, 130, {**base, "bytes": 0.45 * total, "total": total,
                                       "estimated": True, "name": "a.iso"}))  # fmt: skip
    small, unit = font(20), font(19)
    exact = []  # (long amounts that meet the unit to the bit, the card 130 high: g is 1)
    for k in range(18, 60):
        for m in range(10, 100):
            total = m * 10.0 ** (k - 1)
            for share, near in ((0.45, True), (0.13, False)):
                amount = ("≈ " if near else "") + download.of_total(share * total, total)
                length = small.getlength(amount) + unit.getlength("MB/s")
                if length == int(length) and 320 <= length <= 724:
                    exact.append((int(276 + length), 130, {**base, "bytes": share * total,
                                  "total": total, "estimated": near, "name": "a.iso"}))  # fmt: skip
    assert len(exact) >= 3
    cases += exact[:8]
    names = {}
    for a in range(61):
        for b in range(21):
            for c in range(4):
                name = "x" * a + "i" * b + "m" * c
                names.setdefault(font(22).getlength(name), name)
    wide = [w for w in range(596, 1000) if (download.layout(w, 130).right - download.MARGIN - (
        light.tracked_width(font(26), word, 3) + 14 + light.tracked_width(font(20), "STEAM", 2)
        + 20)) in names]  # fmt: skip
    for w in wide[:8]:
        room = (download.layout(w, 130).right - download.MARGIN - light.tracked_width(
            font(26), word, 3) - 14 - light.tracked_width(font(20), "STEAM", 2) - 20)  # fmt: skip
        cases.append((w, 130, {**base, "name": names[room]}))
    stacked = [w for w in range(330, 610) if w - 29 in names][:8]
    cases += [(w, 254, {**base, "name": names[w - 29]}) for w in stacked]
    assert len(wide) >= 3 and len(stacked) >= 3
    return [list(case) for case in cases]


def _demo_cover() -> Any:
    """The demo's album cover, small: a cover the light takes its colours from."""
    from libre_panel.sensors.demo import demo_cover

    cover = demo_cover(64)
    return [cover.mode, png(cover)]


def _theme_tables() -> Any:
    """The theme format's tables as theme/model.py and its neighbours hold them: what Rust's
    are held against, and what the comparisons of rust/theme draw their cases from. Without
    those of the modules that stay (the panel models, the fonts, the config's): they are held
    against as they are then."""
    from libre_panel import design, icons
    from libre_panel.render import finish, lit
    from libre_panel.render.overlays import BUILT_IN_STYLES
    from libre_panel.theme import model, modules

    for name in ("lit", "facet", "acrylic"):
        finish.get(name, "text")  # (the finishes register themselves as they load)
    finishes: dict[str, list[str]] = {}
    for name, widget_type in finish._REGISTERED:
        finishes.setdefault(name, []).append(widget_type)
    # (which types, not the order their modules happened to load in: both apps only ask
    # whether a type is among a finish's, specs.rs and server.py's _finish_types alike)
    finishes = {name: sorted(types) for name, types in sorted(finishes.items())}

    def fields(spec: dict[str, tuple[str, Any]]) -> dict[str, Any]:
        return {k: [kind, default] for k, (kind, default) in spec.items()}

    return {
        "COMMON": fields(model._COMMON),
        "WIDGET_SPECS": {t: fields(s) for t, s in model.WIDGET_SPECS.items()},
        "LOOK_SPECS": {t: fields(s) for t, s in model.LOOK_SPECS.items()},
        "TOAST_STYLES": {k: fields(c.options) for k, c in BUILT_IN_STYLES.items()},
        "PRIMARY_FIELDS": {k: list(v) for k, v in model.PRIMARY_FIELDS.items()},
        "FINISH_TYPES": finishes,
        "LOOK_RANGES": [[a, b, low, high] for (a, b), (low, high) in model.LOOK_RANGES.items()],
        "STYLE_DEFAULTS": design.STYLE_DEFAULTS,
        "TOAST_DEFAULTS": model.TOAST_DEFAULTS,
        "GRID_DEFAULTS": modules.GRID_DEFAULTS,
        "FINISHES": list(model.FINISHES),
        "EFFECT_FIELDS": list(model.EFFECT_FIELDS),
        "TOAST_ANCHORS": list(model.TOAST_ANCHORS),
        "GONE_LOOK": sorted(model.GONE_LOOK),
        "CARDS": list(modules.CARDS),
        "BACKDROPS": list(modules.BACKDROPS),
        "MODULE_SOURCES": list(modules.MODULE_SOURCES),
        "MODULE_FALLBACKS": list(modules.MODULE_FALLBACKS),
        "ICON_NAMES": list(icons.ICON_NAMES),
        "WEATHER_ICONS": list(lit.WEATHER_ICONS),
        "PART_TYPES": list(finish.PART_TYPES),
        "GONE_FIELDS": {k: sorted(v) for k, v in model.GONE_FIELDS.items()},
        "THEME_FORMAT": model.THEME_FORMAT,
        "THEME_FILENAME": model.THEME_FILENAME,
        "BLUR_MAX": model.BLUR_MAX,
        "MAX_SWITCHES": model.MAX_SWITCHES,
        "MAX_DETACHED": model.MAX_DETACHED,
        "CARD_OPACITY_MIN": model.CARD_OPACITY_MIN,
        "COVER_COLOR": model.COVER_COLOR,
        "AUTO": model.AUTO,
        "GRID_CELLS": modules.GRID_CELLS,
    }


def _theme_names() -> Any:
    """The themes that come with the app, by their folders' names."""
    themes = Path(__file__).resolve().parent.parent / "data" / "themes"
    return sorted(p.parent.name for p in themes.glob("*/theme.json"))


def _own_patterns() -> Any:
    """The patterns the app's sensors read names and numbers with (sensors/hwnames.py,
    sensors/librehardwaremonitor.py): each with whether it ignores case."""
    import re

    from libre_panel.sensors import hwnames
    from libre_panel.sensors import librehardwaremonitor as lhm

    real = [lhm._NUMBER, lhm._CORE_CLOCK, hwnames._MARKS, *hwnames._CPU_CUTS, hwnames._VENDOR,
            hwnames._GENUINE, hwnames._COMPANY]  # fmt: skip
    return [[p.pattern, bool(p.flags & re.IGNORECASE)] for p in real]


def _notices_defaults() -> Any:
    """services/notices.py's options, each as it is at first."""
    from libre_panel.services import notices

    return {name: default for name, (_, default) in notices.Notices.options.items()}


def _game_defaults() -> Any:
    """services/game.py's options, each as it is at first."""
    from libre_panel.services import game

    return {name: default for name, (_, default) in game.Game.options.items()}


def _memory_schema() -> Any:
    """The tables of the memory's database as services/memory_store.py makes them, and how
    many columns a row of the cooling has."""
    from libre_panel.services import memory, memory_store

    return {"schema": memory_store.SCHEMA, "cooling_columns": len(memory.COOLING_COLUMNS)}


def _demo_course() -> Any:
    """The course of a round's temperatures as sensors/demo.py makes one up."""
    from libre_panel.sensors.demo import demo_course

    return demo_course()


def _memory_defaults() -> Any:
    """services/memory.py's options, each as it is at first."""
    from libre_panel.services import memory

    return {key: default for key, (_kind, default) in memory.Memory.options.items()}


def _waveshare_keys() -> Any:
    """The readings services/waveshare.py feeds the Waveshare display, by their keys."""
    from libre_panel.services import waveshare

    return [key[2] for key in waveshare.KEYS]


GIVERS = {"looks": _looks, "icons": _icons, "demo": _demo, "themes": _themes,
          "download_edges": _download_edges, "demo_cover": _demo_cover,
          "theme_tables": _theme_tables, "theme_names": _theme_names,
          "own_patterns": _own_patterns, "notices_defaults": _notices_defaults,
          "game_defaults": _game_defaults, "memory_schema": _memory_schema,
          "waveshare_keys": _waveshare_keys, "memory_defaults": _memory_defaults,
          "demo_course": _demo_course}  # fmt: skip


def given(name: str) -> Any:
    """golden.given, from the Python app itself: made, written, and given as it reads back."""
    if name not in _made:
        _write(name, GIVERS[name]())
        _made[name] = json.loads((_folder() / f"{name}.json").read_text(encoding="utf-8"))
    return _made[name]


# -- what it tells while cases are made -------------------------------------------------------


def _refused(at: dict[str, Any]) -> bool:
    """Whether the Python app refuses the theme in the test's ``folder``."""
    from libre_panel.theme.model import ThemeError, load_theme

    try:
        load_theme(at["folder"])
    except ThemeError:
        return True
    return False


_measurer: list[Any] = []


def _width(at: dict[str, Any]) -> float:
    """The width the renderer's _fit holds a text widget's text against its max_width (the
    test's ``text``)."""
    import tempfile

    from libre_panel.fonts.fallback import cursive
    from libre_panel.render.renderer import Renderer
    from libre_panel.theme.model import load_theme

    if not _measurer:
        folder = Path(tempfile.mkdtemp())
        (folder / "theme.json").write_text(json.dumps({
            "format": "libre-panel-theme/1", "name": "measure",
            "display": {"width": 64, "height": 48}, "widgets": []}), encoding="utf-8")  # fmt: skip
        _measurer.append(Renderer(load_theme(folder), preview=True))
    renderer, widget = _measurer[0], at["text"]
    font = renderer.font(widget.get("font", ""), widget.get("font_size", 24))
    text, tabular = widget["text"], widget.get("tabular", False)
    if cursive(text):
        return float(font.getlength(text))
    cell = renderer._digit_cell(font) if tabular else 0
    advances = [cell if tabular and ch in "0123456789" else renderer._advance(font, ch)
                for ch in text]  # fmt: skip
    return sum(advances) + widget.get("letter_spacing", 0) * max(0, len(text) - 1)


def _soft(at: dict[str, Any]) -> bool:
    """Whether the theme in the test's ``folder`` holds light blurred in floating point (a glow,
    a shadow, the lit finish's), as the renderer has its widgets."""
    from libre_panel.render.renderer import Renderer
    from libre_panel.theme.model import load_theme

    widgets = Renderer(load_theme(at["folder"]), preview=True).widgets
    return any(w.get("glow") or w.get("shadow") or w.get("finish") == "lit" for w in widgets)


def _saved(at: dict[str, Any]) -> Any:
    """The test's ``saves`` saved by the Python app, one after another, into its own copy of
    the person's folder (``py_home``): what each answered (the folder as HOME), and every file
    there after them with its digest."""
    import hashlib
    import os

    from libre_panel.theme import model

    root, before = at["py_home"], os.environ.get("LIBRE_PANEL_HOME")
    os.environ["LIBRE_PANEL_HOME"] = str(root)
    try:
        answers = []
        for s in at["saves"]:
            try:
                path = model.save_theme(s["name"], json.loads(s["data"]), s["source"])
                answers.append({"path": str(path)})
            except Exception as error:  # noqa: BLE001 (every kind Python raises is kept)
                answers.append({"error": str(error), "kind": type(error).__name__})
    finally:
        if before is None:
            os.environ.pop("LIBRE_PANEL_HOME", None)
        else:
            os.environ["LIBRE_PANEL_HOME"] = before
    return {
        "saved": [{k: v.replace(str(root), "HOME") for k, v in a.items()} for a in answers],
        "files": {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sorted(root.rglob("*")) if p.is_file()},
    }  # fmt: skip


def _reach(at: dict[str, Any]) -> int:
    """How far render/dither.py's blur of the test's ``radius`` carries a pixel."""
    from libre_panel.render import dither

    return dither.reach(at["radius"])


def _good(at: dict[str, Any]) -> Any:
    """Answers as the sensor service gives them, made by sensorservice/protocol.py of the
    test's ``rows`` (sensors as LibreHardwareMonitor names them): its greeting, an error, and
    what it read: running with its times and memory, starting, failed."""
    from libre_panel.sensors import librehardwaremonitor as lhm
    from libre_panel.sensorservice import protocol

    sensors = [lhm.LhmSensor(*row) for row in at["rows"]]
    timing = lhm.Timing(
        lhm.RoundTime((lhm.PartTime("/amdcpu/0", "Ryzen", "Cpu", 0.0123),), False),
        lhm.RoundTime((lhm.PartTime("/amdcpu/0", "Ryzen", "Cpu", 0.02),
                       lhm.PartTime("/nvme/0", "", "Storage", 0.001)), True),
    )  # fmt: skip
    return [
        protocol.hello_answer("0.8.1"),
        protocol.error_answer("no such op"),
        protocol.read_answer(sensors, state="running", age=0.4, ring0=True, pawnio="2.0.1",
                             timing=timing, memory=lhm.ServiceMemory(88080384, 120586240, None)),
        protocol.read_answer([], state="starting", age=None, ring0=None, pawnio=None),
        protocol.read_answer(sensors[:3], state="failed", age=3.0, ring0=False, pawnio="",
                             error="no .NET", timing=lhm.Timing()),
    ]  # fmt: skip


def _memory_tables(at: dict[str, Any]) -> Any:
    """The database services/memory.py writes of the test's ``case`` (its options, the
    readings of its steps, what is asked between them) in the test's ``language``: each table
    as a digest, as the test takes those of the Rust app's."""
    import itertools

    from test_rust_services import _digests, _tables

    from libre_panel import i18n
    from libre_panel.sensors.base import Reading, Snapshot
    from libre_panel.services import memory, memory_store

    case = at["case"]
    i18n.set_language(at["language"])
    try:
        settings = memory.Settings.from_options(json.loads(case["options"]), lambda note: None)
        store = memory_store.Store(case["theirs"])
        store.start()
        stamps = itertools.count()
        recorder = memory.Recorder(store, settings, lambda report, stamp: None,
                                   lambda: next(stamps))  # fmt: skip
        reader = memory.Reader(settings)
        reader.names, reader.hidden = dict(case["names"]), set(case["hidden"])
        reader.sources = dict(case["sources"])
        for moment, rows, tell_round, tell_sensors in case["steps"]:
            snapshot = Snapshot(readings={
                row[0]: Reading(row[0], row[1], row[2], row[3], origin=row[4])
                for row in json.loads(rows)})  # fmt: skip
            if recorder.due(moment):
                recorder.add(reader.read(snapshot, moment))
            if tell_round and recorder.running is not None:
                recorder.running.totals(moment)
            if tell_sensors:
                reader.sensors(snapshot.readings)
        recorder.close(case["close"])
        store.stop(30)
    finally:
        i18n.set_language("en")
    made = _digests(_tables(case["theirs"]))
    for end in ("", "-wal", "-shm"):
        Path(case["theirs"] + end).unlink(missing_ok=True)
    return made


def _lines_of(readings: dict[str, Any]) -> list[str]:
    """Readings as `libre-panel sensors` lists them, a line each, in the order of their keys."""
    lines = []
    for key in sorted(readings):
        r = readings[key]
        value = f"{r.value:.2f}" if isinstance(r.value, float) else str(r.value)
        lines.append(f"{key:<40} {value:>14} {r.unit:<5} {r.label}")
    return lines


def _listed(at: dict[str, Any]) -> Any:
    """The readings sensors/librehardwaremonitor.py makes of the test's ``tree`` (the web
    server's), as `libre-panel sensors` lists them."""
    from libre_panel.sensors import librehardwaremonitor as lhm

    return _lines_of(lhm.readings_from_tree(at["tree"]))


def _kinds(at: dict[str, Any]) -> Any:
    """The kinds of readings sensors/psutil_provider.py gives on this computer (and, where the
    test's config.toml names the Linux source, sensors/linux.py of the kernel's own files): by
    the shape of their keys (a number in one as N), each one's unit, and its label where the
    key has no number (a drive's and an adapter's label is its own name)."""
    import re
    import time

    from libre_panel.sensors.psutil_provider import PsutilProvider

    provider = PsutilProvider({})
    try:
        provider.read()
        readings = provider.read()
    finally:
        provider.close()
    if '"linux"' in (at["tmp_path"] / "config.toml").read_text(encoding="utf-8"):
        from libre_panel.sensors import linux as source

        root, source.ROOT = source.ROOT, Path("/")  # (the tests' computer is empty: the real one)
        try:
            linux = source.LinuxProvider({})
            try:
                linux.read()
                linux.read_slow()
                time.sleep(0.3)  # (a power needs two readings of its counter, a while apart)
                linux.read_slow()
                readings = {**readings, **linux.read()}
            finally:
                linux.close()
        finally:
            source.ROOT = root
    kinds: dict[str, list[str]] = {}
    for key, reading in readings.items():
        shape = re.sub(r"\d+", "N", key)
        kinds[shape] = [reading.unit, reading.label if shape == key else ""]
    return dict(sorted(kinds.items()))


def _as_kept(readings: dict[str, Any]) -> dict[str, list[Any]]:
    """Readings as they are kept: each one's value, unit and label, in their order."""
    return {key: [r.value, r.unit, r.label] for key, r in readings.items()}


def _wanted(at: dict[str, Any]) -> Any:
    """What the Python app's source makes of what the running test gives the program: the
    readings test_rust_app.py holds the program's against."""
    name = golden._test["name"]
    if name.startswith("test_what_plays_is_read"):
        from libre_panel.sensors import media

        return _as_kept(media.parse_playerctl(at["line"] + "\n", 0.0).readings(0.0))
    if name.startswith("test_mqtt_messages"):
        from libre_panel.sensors import mqtt

        wanted: dict[str, Any] = {}
        for topic, payload in at["messages"]:
            wanted.update(mqtt.readings_from_message(topic, payload, at["units"]))
        return _as_kept(wanted)
    if name.startswith("test_home_assistant_and_json"):
        from libre_panel.sensors import homeassistant, json_source

        wanted = homeassistant.readings_from_states(at["states"], ["sensor.*", "climate.*"])
        for entry, data in zip(at["entries"][:2], (at["nas"], at["values"]), strict=True):
            wanted.update(json_source.Source(entry).readings(data))
        return _as_kept(wanted)
    raise AssertionError(f"{name}: no source is known for it")


def _asked(at: dict[str, Any]) -> Any:
    """What sensors/nut.py asks the test's upsd (its lines, as the test heard them) and the
    readings it makes of the answers."""
    from libre_panel.sensors import nut

    python = nut.NutProvider(at["options"], start=False)
    python.fetch()
    wanted = python.read()
    at["said_goodbye"]()
    return [list(at["heard"]), _as_kept(wanted)]


def _versions(at: dict[str, Any]) -> Any:
    """The versions of Lumen theme/model.py picks for the test's panels (``size`` the first
    one's, ``beside_size`` the other's): alone, with a panel beside it, and the companion."""
    from libre_panel.theme.model import companion_for, pair_version, theme_for

    alone = theme_for("lumen", "landscape", at["size"])
    paired = pair_version(alone) or alone
    return [alone, paired, theme_for(companion_for(paired) or "", "landscape", at["beside_size"])]


def _part(at: dict[str, Any]) -> Any:
    """The first panel's part of the picture across the panels of the test's config.toml, as
    arrangement.py cuts it, fitted to the panel: its pixels' digest."""
    import hashlib

    from PIL import ImageOps

    from libre_panel import arrangement
    from libre_panel.config import load_config

    config = load_config(at["tmp_path"] / "config.toml")
    places = arrangement.places(config, [None, None])
    loaded = arrangement.load(at["tmp_path"] / "across" / "picture.jpg")
    assert loaded is not None
    rounded = [None if r is None else tuple(round(v, 1) for v in r) for r in places]
    expected = ImageOps.fit(arrangement.part(loaded[0], rounded, 0), (1920, 480))
    return hashlib.sha256(expected.tobytes()).hexdigest()


def _listing(at: dict[str, Any]) -> Any:
    """What cli.py's `plugins` says of the plugins in the test's settings folder, and how it
    ends (test_rust_plugins._plain: without what is the run's own)."""
    import contextlib

    from test_rust_plugins import _plain

    from libre_panel import cli

    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        code = cli.main(["plugins"])
    return [code, _plain(said.getvalue(), at["isolated_home"])]


def _catalog(at: dict[str, Any]) -> Any:
    """Settings' catalog as editor/settings.py makes it of the test's config.toml: the
    services and the sensor sources, the plugins' among them."""
    from test_rust_plugins import _plain

    from libre_panel.editor.settings import Restart, Settings

    made = Settings(at["config"], None, Restart())._catalog()
    made = {key: made[key] for key in ("services", "providers")}
    return json.loads(_plain(json.dumps(made), at["isolated_home"]))


def _preview(at: dict[str, Any]) -> Any:
    """The picture cli.py's `preview` draws of the test's theme "plugged": its pixels' digest."""
    import hashlib

    from PIL import Image

    from libre_panel import cli

    want = at["tmp_path"] / "python.png"
    assert cli.main(["preview", "plugged", "--output", str(want)]) == 0
    with Image.open(want) as python:
        assert python.size == (200, 100)
        return hashlib.sha256(python.convert("RGB").tobytes()).hexdigest()


def _specs(at: dict[str, Any]) -> Any:
    """What editor/drawings.py tells the editor of the plugins' parts, in German."""
    from test_rust_plugins import _plain

    from libre_panel import i18n
    from libre_panel.editor.drawings import plugin_specs

    was = i18n.language()
    i18n.set_language("de")
    try:
        want = plugin_specs()
    finally:
        i18n.set_language(was)
    return json.loads(_plain(json.dumps(want), at["isolated_home"]))


def _folder_listed(at: dict[str, Any]) -> Any:
    """What sensors/linux.py reads of the machine laid out in the test's ``root``, as
    `libre-panel sensors` lists it."""
    from libre_panel.sensors.linux import LinuxProvider

    provider = LinuxProvider({}, root=at["root"])
    try:
        wanted = provider.read()
    finally:
        provider.close()
    return [f"{key:<40} {r.value:>14.2f} {r.unit:<5} {r.label}"
            for key, r in sorted(wanted.items())]  # fmt: skip


def _sha(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def _card(at: dict[str, Any]) -> Any:
    """The card transition_show.py makes of "Fade" in the colours of the theme in the test's
    ``folder``: its pixels' digest."""
    from libre_panel import transition_show
    from libre_panel.plugins.render import RenderContext
    from libre_panel.render.light import role_color
    from libre_panel.render.renderer import Renderer
    from libre_panel.theme import load_theme

    context = RenderContext(Renderer(load_theme(at["folder"]), preview=True))
    card = transition_show.card(
        "Fade", (800, 480), role_color(context, "void"), role_color(context, "text")
    )
    return _sha(card.tobytes())


def _encoded(at: dict[str, Any]) -> Any:
    """What control.py's encode() makes of the test's frame ``frames[n]`` at ``width`` as
    ``kind``: its kind as a browser is told, and the digest of its bytes (a JPEG) or its mode
    and the digest of its pixels (a PNG)."""
    from PIL import Image

    from libre_panel.pages import control

    made = control.encode(at["frames"][at["n"]], at["width"], at["kind"])
    if at["kind"] == "jpeg":
        return [control.PICTURES[at["kind"]], _sha(made)]
    with Image.open(io.BytesIO(made)) as picture:
        return [control.PICTURES[at["kind"]], picture.mode, _sha(picture.tobytes())]


def _meter(at: dict[str, Any]) -> Any:
    """meter.py's numbers for a PNG file at 2 frames a second: which there are, in their
    order, and which of them are none."""
    from libre_panel.app import RunStatus
    from libre_panel.meter import Meter

    status = RunStatus()
    meter = Meter(status, 0.0)
    meter.drew(0.01)
    meter.tick(1.0, 2, False)
    return {"keys": list(status.stats),
            "none": sorted(k for k, v in status.stats.items() if v is None)}  # fmt: skip


def _sources(at: dict[str, Any]) -> Any:
    """What control.py lists of the sources of the Python app's hub on the test's ``config``,
    with this computer's parts and readings (not the tests' empty machine, conftest.py)."""
    import importlib.util
    import time
    from types import SimpleNamespace

    from libre_panel.app import build_hub
    from libre_panel.config import load_config
    from libre_panel.pages import control
    from libre_panel.plugins import pages
    from libre_panel.plugins.host import PluginHost
    from libre_panel.sensors import hardware, linux

    monkeypatch = at["monkeypatch"]
    spec = importlib.util.find_spec("libre_panel.sensors.hardware")
    assert spec is not None and spec.loader is not None
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)
    monkeypatch.setattr(hardware, "_scan_machine", fresh._scan_machine)
    monkeypatch.setattr(linux, "ROOT", Path("/"))  # (its chips and cards too)
    hub = build_hub(load_config(at["config"]))
    hub.providers.append(PluginHost().provider())
    try:
        hub.snapshot()
        time.sleep(0.3)
        hub.snapshot()
        listed = [{"name": p.name, "readings": hub.readings_from(p),
                   "status": pages._status_of(p)} for p in hub.providers]  # fmt: skip
    finally:
        hub.close()
    context = SimpleNamespace(status=lambda: {"hint": ""}, sources=lambda: listed,
                              background=True)  # fmt: skip
    return {"background": True,
            "sources": control.Control._sources(SimpleNamespace(context=context))}  # fmt: skip


def _gone(at: dict[str, Any]) -> Any:
    """The theme of its own the Python app has for the test's ``model``, and what it says of
    each theme of the test's ``names`` that is not there."""
    from libre_panel.devices.models import find_model
    from libre_panel.theme.defaults import default_theme
    from libre_panel.theme.model import ThemeError, find_theme

    whys = []
    for name in at["names"]:
        try:
            find_theme(name)
        except ThemeError as error:
            whys.append(str(error))
    assert len(whys) == len(at["names"])
    return {"own": default_theme(find_model(at["model"])), "whys": whys}


def _update(at: dict[str, Any]) -> Any:
    """updates.py's: where a release's files are, and what Install tells while nothing goes
    on."""
    from libre_panel.editor import updates

    return {"files": updates.RELEASE_FILES, "idle": updates.IDLE}


def _restart(at: dict[str, Any]) -> Any:
    """updates.py's restart_command for the test's ``setup`` (its ``digest``), the app's
    ``program`` and its ``windowed`` one: in the test's words."""
    from test_rust_server import _restart_in_words

    from libre_panel.editor import updates

    pid, windowed, setup = at["program"].pid, at["windowed"], at["setup"]
    made = updates.restart_command("setup", pid, windowed, setup, at["digest"])
    return _restart_in_words(made, pid, windowed, setup)


class _Item:
    """pystray's MenuItem as tray.py makes it: what is read of it."""

    def __init__(
        self,
        text: Any,
        action: Any,
        checked: Any = None,
        radio: bool = False,
        default: bool = False,
        enabled: bool = True,
        visible: Any = True,
    ) -> None:
        self.args = (text, action, checked, radio, default, enabled, visible)


class _Menu:
    """pystray's Menu: its items, or what makes them as the menu opens."""

    SEPARATOR = object()

    def __init__(self, *items: Any) -> None:
        self.items = items

    def listed(self) -> list[Any]:
        made = self.items[0] if len(self.items) == 1 and callable(self.items[0]) else None
        return list(made()) if made is not None else list(self.items)


def _as_listed(menu: _Menu) -> list[dict[str, Any]]:
    """A menu tray.py made, as /api/tray lists one: the visible items, what each says, whether
    it is on, a choice, the default, and its own menu; separators as they are."""
    told: list[dict[str, Any]] = []
    for item in menu.listed():
        if item is _Menu.SEPARATOR:
            told.append({"separator": True})
            continue
        text, action, checked, radio, default, enabled, visible = item.args
        if not (visible(item) if callable(visible) else visible):
            continue
        told.append({
            "text": text(item) if callable(text) else text,
            "enabled": enabled,
            "checked": checked(item) if callable(checked) else checked,
            "radio": radio,
            "default": default,
            "items": _as_listed(action) if isinstance(action, _Menu) else None,
        })  # fmt: skip
    return told


def _exec(at: dict[str, Any]) -> Any:
    """autostart.py's Exec line of the test's ``command``, its program as a word."""
    from libre_panel import autostart

    command = at["command"]
    return autostart.desktop_exec(command).replace(command[0], "<program>")


def _panel_files(at: dict[str, Any]) -> Any:
    """panel_files.py's words and tables for the start screen's comparisons: the switch that
    lets files be written, the form to report with, the looks that do not fit each model, what
    it says of a file the panel did not take, of one that ended early (with the bytes as
    words), of no room for a boot logo beside a clip of 701 830 bytes and of a look too fine;
    and the brightness a TURZX panel is told for 60 %."""
    from libre_panel.devices import panel_files
    from libre_panel.devices.turzx_usb import brightness_arg

    took, size = 1_234_567, 7_654_321
    cut_off = panel_files.cut_off(panel_files.STANDBY_CLIP, took, size)
    assert cut_off.count(str(took)) == cut_off.count(str(size)) == 1 and "{" not in cut_off
    return {
        "writes_env": panel_files.WRITES_ENV, "report": panel_files.REPORT_FORM,
        "unfit": {name: list(looks) for name, looks in panel_files.UNFIT.items()},
        "not_taken": panel_files.not_taken(), "too_fine": panel_files.too_fine(),
        "no_room": panel_files.no_room(panel_files.BOOT_LOGO, 701_830),
        "cut_off": cut_off.replace(str(took), "{took}").replace(str(size), "{size}"),
        "brightness": brightness_arg(60),
    }  # fmt: skip


def _logo_file(at: dict[str, Any]) -> Any:
    """The boot logo panel_files.py makes for the test's ``made`` (a model, a look or None for
    the usual one, whether turned round for a panel upside down): its size, its digest and
    the room the model has for one."""
    from PIL import Image

    from libre_panel.devices import panel_files
    from libre_panel.render import bootscreen

    name, design, turned = at["made"]
    model = panel_files.model_of(name)
    if turned:
        logo = bootscreen.boot_logo(model, "landscape", design)
        data = panel_files.logo_jpeg(model, logo.transpose(Image.Transpose.ROTATE_180))
    elif design is None:
        data = panel_files.logo_jpeg(model)
    else:
        data = panel_files.logo_jpeg(model, design=design)
    return {"size": len(data), "sha256": _sha(data), "room": panel_files.room(model)[1]}


def _same_clip(at: dict[str, Any]) -> Any:
    """Whether the test's ``clip`` is the standby clip panel_files.py makes for its ``model``
    with this computer's ffmpeg (turned round where ``upside_down``), byte for byte."""
    import shutil

    from PIL import Image

    from libre_panel.devices import panel_files
    from libre_panel.render import bootscreen

    model, ffmpeg = panel_files.model_of(at["model"]), shutil.which("ffmpeg")
    if at["upside_down"]:
        frames = [frame.transpose(Image.Transpose.ROTATE_180)
                  for frame in bootscreen.standby_frames(model)]  # fmt: skip
        made = panel_files.clip_h264(model, ffmpeg, frames, full=True)
    else:
        made = panel_files.clip_h264(model, ffmpeg, full=True)
    return made == at["clip"]


def _shared(at: dict[str, Any]) -> Any:
    """Slate as share.py gives it to someone (a .zip, in base64), and the size it is made
    for."""
    from libre_panel.theme.model import find_theme, load_theme
    from libre_panel.theme.share import export_theme

    data, _name = export_theme("slate")
    theme = load_theme(find_theme("slate"))
    return {"zip": base64.b64encode(data).decode("ascii"),
            "size": f"{theme.width}x{theme.height}"}  # fmt: skip


def _capture(at: dict[str, Any]) -> Any:
    """The command capture.py and mirror.py make to show the test's part of the monitor
    HDMI-1 of its X11 desktop on a 1920x480 panel, at the test's ``rate``."""
    from libre_panel import capture, mirror

    monitor = capture.find_monitor("HDMI-1", "x11")
    found = capture.monitor_capture("ffmpeg", monitor, at["rate"], False, (100, 51, 801, 600),
                                    "x11")  # fmt: skip
    return " ".join(mirror.frames_command("ffmpeg", found, (1920, 480), "stretch", at["rate"]))


def _xrandr(at: dict[str, Any]) -> Any:
    """What extend.py tells xrandr to make a monitor of the panel's own (1920x480) and to take
    it off again, as the test's X11 desktop logs it (its ``x11``, read by its ``changes``)."""
    from libre_panel import extend

    at["x11"].write_text("", encoding="utf-8")
    extend.attach((1920, 480), "x11")
    extend.detach("x11")
    return at["changes"]()


def _setup(at: dict[str, Any]) -> Any:
    """What linux_setup.py's run_cli says with the test's ``flags`` of the computer laid out
    in its ``root``: how it ends, what it writes and what it warns of, in the test's words
    (its ``plain``)."""
    import argparse
    import contextlib
    import importlib.util

    import pytest

    from libre_panel import linux_setup

    # (the tests' linux_setup looks at no computer: a copy of the module of its own does)
    spec = importlib.util.find_spec("libre_panel.linux_setup")
    assert spec is not None and spec.loader is not None
    looking = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(looking)
    facts = looking.gather(at["root"])
    args = argparse.Namespace(**{"status": False, "rapl": False, "no_rapl": False,
                                 "remove": False, "dry_run": False, "json": False,
                                 **at["flags"]})  # fmt: skip
    out, err = io.StringIO(), io.StringIO()
    with contextlib.ExitStack() as stack:
        stack.enter_context(pytest.MonkeyPatch.context()).delenv("DISPLAY", raising=False)
        code = linux_setup.run_cli(args, facts=facts, root=False, stdout=out, stderr=err)
    return [code, at["plain"](out.getvalue()), at["plain"](err.getvalue())]


def _rights(at: dict[str, Any]) -> Any:
    """What linux_rights.py tells of the computer laid out in the test's ``root`` (without
    the job), the program that would start the setup said one way, in the test's words."""
    import importlib.util
    import shlex

    import pytest
    from test_rust_server import _one_program

    from libre_panel import linux_rights

    spec = importlib.util.find_spec("libre_panel.linux_setup")
    assert spec is not None and spec.loader is not None
    looking = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(looking)
    facts = looking.gather(at["root"])
    with pytest.MonkeyPatch.context() as patch:
        patch.delenv("DISPLAY", raising=False)
        told = linux_rights.status(facts)
    told.pop("job")
    itself = shlex.join(linux_rights.program() or [])
    return json.loads(at["plain"](_one_program(json.dumps(told), at["program"], itself)))


def _python(at: dict[str, Any]) -> Any:
    """How the Python app starts itself for the setup, as a command line."""
    import shlex

    from libre_panel import linux_rights

    return shlex.join(linux_rights.program() or [])


def _status(at: dict[str, Any]) -> Any:
    """What sensorservice/setup.py tells of this Windows computer: the sensor service, PawnIO,
    the group game frame rates need."""
    from libre_panel.sensorservice import setup

    return setup.status(setup.WindowsSystem())


def _steps(at: dict[str, Any]) -> Any:
    """The steps of experiment.py's video experiment on a computer whose graphics card has
    no encoder."""
    from libre_panel.editor import experiment

    return [{"id": s.id, "changes": s.changes, "seconds": s.seconds}
            for s in experiment.steps("")]  # fmt: skip


def _menu(at: dict[str, Any]) -> Any:
    """tray.py's menu, as /api/tray lists one, and its tooltip: of the Python app in the
    background on the test's settings (LIBRE_PANEL_HOME), once its panel shows."""
    import time
    import types

    from libre_panel.service import BackgroundApp
    from libre_panel.tray import Tray, tooltip

    app = BackgroundApp(port=0)
    app.start()
    try:
        until = time.monotonic() + 60
        while app.panel.state()["state"] != "showing":
            assert time.monotonic() < until, app.panel.state()
            time.sleep(0.1)
        tray = Tray(app, types.SimpleNamespace(Menu=_Menu, MenuItem=_Item), log_path=None)

        # (the settings folder, which the menu and the tooltip name, as a word: the tooltip is
        # cut where it is long, which the folder's name must not decide)
        def worded(told: Any) -> Any:
            return json.loads(json.dumps(told).replace(json.dumps(str(at["home"]))[1:-1], "<home>"))

        return {"menu": worded(_as_listed(tray.build_menu())),
                "tip": tooltip(worded(app.panel.state()))}  # fmt: skip
    finally:
        app.quit()
        app.shutdown()


def _logo(at: dict[str, Any]) -> Any:
    """branding.py's logo of a paused panel, 32 pixels: its pixels' digest."""
    from libre_panel.branding import logo

    return _sha(logo(32, "paused").tobytes())


class _Clock:
    """The time module with one moment for now."""

    def __init__(self, now: float) -> None:
        self._now = now

    def time(self) -> float:
        return self._now

    def localtime(self, seconds: float | None = None) -> Any:
        return time.localtime(self._now if seconds is None else seconds)

    def gmtime(self, seconds: float | None = None) -> Any:
        return time.gmtime(self._now if seconds is None else seconds)

    def strftime(self, form: str, moment: Any = None) -> str:
        return time.strftime(form, self.localtime() if moment is None else moment)

    def __getattr__(self, name: str) -> Any:
        return getattr(time, name)


class PythonServer:
    """editor/server.py for the comparisons of the Rust app's server (test_rust_server.py's
    ``servers``): on ``home``'s config.toml, in German, with the test's place search and its
    file of GitHub's releases; ``now`` the moment it takes for now in what it tells (as the
    Rust app's takes LP_NOW), in UTC."""

    def __init__(self, home: Path, token: str, search_url: str, releases: Path,
                 monkeypatch: Any, now: float) -> None:  # fmt: skip
        import datetime
        import threading
        import zipfile

        from libre_panel import backup, doctor, i18n
        from libre_panel.editor import drawings
        from libre_panel.editor import server as editor
        from libre_panel.pages import control
        from libre_panel.sensors import demo
        from libre_panel.weather import open_meteo

        monkeypatch.setenv("LIBRE_PANEL_HOME", str(home))
        self.zone = os.environ.get("TZ")
        os.environ["TZ"] = "UTC"
        if hasattr(time, "tzset"):
            time.tzset()
        now = float(now)  # (as the clock tells it)
        clock, snapshot = _Clock(now), demo.demo_snapshot

        def demo_now(fixed_time: float | None = None, *more: Any, **named: Any) -> Any:
            return snapshot(now if fixed_time is None else fixed_time, *more, **named)

        class Moment(datetime.datetime):
            @classmethod
            def now(cls, tz: Any = None) -> Any:
                return cls.fromtimestamp(now, tz)

        for module in (demo, drawings, editor):
            monkeypatch.setattr(module, "demo_snapshot", demo_now, raising=False)
        for module in (editor, backup, zipfile):
            monkeypatch.setattr(module, "time", clock)
        for module in (control, doctor):  # (a notice's time, a report's)
            monkeypatch.setattr(module, "datetime", Moment)
        i18n.set_language("de")
        self.server = editor.make_server(0, home / "config.toml", token=token)
        # (a connection that is gone again before it is accepted leaves accept() waiting for
        # the next one, for good: serve_forever never looked at its end then, and shutdown()
        # below waited for it. With a time to wait, accept() gives up and the loop goes on)
        self.server.socket.settimeout(1.0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        monkeypatch.setattr(open_meteo, "GEOCODING_URL", search_url)
        self.server.editor_state.updates.fetch = lambda: json.loads(
            releases.read_text(encoding="utf-8")
        )
        self.port, self.token = self.server.server_address[1], token

    def close(self) -> None:
        from libre_panel import i18n

        i18n.set_language("en")
        self.server.shutdown()
        self.server.server_close()
        self.server.editor_state.close()
        if self.zone is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self.zone
        if hasattr(time, "tzset"):
            time.tzset()


class Recorded:
    """What the Python app's server answers a test's requests, and what it writes into its
    settings folder on the way, as server_tape.py keeps and plays it."""

    def __init__(self, ask: Any, homes: list[Path], tmp: Path) -> None:
        import server_tape

        self.tape, self.ask, self.home, self.mine = server_tape, ask, homes[0], homes[1]
        self.theirs, self.ours = (
            server_tape.Places(homes[0], tmp),
            server_tape.Places(homes[1], tmp),
        )
        self.tmp, self.shared = tmp, []
        self.shared_files: dict[str, Any] = {}
        self.entries: list[dict[str, Any]] = []
        self.files: dict[str, Any] = {}
        self.forms: dict[str, Any] = {}
        self._changes()
        self.now: Any = None
        self.asked: Any = None
        self.changes: dict[str, Any] = {}
        self.shared_changes: dict[str, Any] = {}
        self.unlike: list[str] = []

    def shares(self, folder: Path) -> None:
        """A folder both servers write into: what the Python server writes there is kept."""
        self.shared.append(folder)
        self._shared_changes()

    def _shared_changes(self) -> dict[str, Any]:
        """The files of the shared folders that are others than at the last look, by their
        places in the test's folder, whole."""
        files: dict[str, Any] = {}
        for folder in self.shared:
            if folder.is_dir():
                at = folder.relative_to(self.tmp).as_posix()
                files[at + "/"] = None
                files.update(
                    {f"{at}/{name}": stat for name, stat in self.tape.tree(folder).items()}
                )
        changes: dict[str, Any] = dict.fromkeys(self.shared_files.keys() - files.keys())
        for name, stat in files.items():
            if name in self.shared_files and self.shared_files[name] == stat:
                continue
            if stat is None:
                changes[name] = ""
                continue
            try:
                data = (self.tmp / name).read_bytes()
            except OSError:
                continue
            changes[name] = self.tape.kept(self.theirs.plain(data, ports=False), whole=True)
        self.shared_files = files
        return dict(sorted(changes.items()))

    def _changes(self) -> dict[str, Any]:
        """The files of the Python server's folder that are others than at the last look."""
        files, changes = self.tape.tree(self.home), {}
        for name in self.files.keys() - files.keys():
            changes[name] = None
            self.forms.pop(name, None)
        for name, stat in files.items():
            if name in self.files and self.files[name] == stat:
                continue
            if stat is None:
                changes[name] = ""
                continue
            try:
                data = (self.home / name).read_bytes()
            except OSError:
                continue
            form = self.tape.kept(self.theirs.plain(data))
            if isinstance(form, dict) and "sha256" in form:
                # (a large file the Rust app's server has not likewise: whole, to look into)
                try:
                    mine = self.tape.kept(self.ours.plain((self.mine / name).read_bytes()))
                except OSError:
                    mine = None
                if mine != form:
                    form = self.tape.kept(self.theirs.plain(data), whole=True)
            if self.forms.get(name) != form:
                changes[name] = self.forms[name] = form
        self.files = files
        return dict(sorted(changes.items()))

    def before(self, request: str | bytes, done: bool, left: bool) -> None:
        self.changes = self._changes()
        self.asked = (request, done, left)
        self.now = self.ask(request, done, left)
        self.shared_changes = self._shared_changes()  # (its own: the Rust app's is not asked yet)

    def _portless(self, answer: Any) -> Any:
        """An answer without the ports of this computer's servers (a server's own is told
        apart by no test)."""
        status, headers, body = answer
        return status, headers, self.tape.PORT.sub(rb"\1<port>", body)

    def answer(self, ours: Any) -> Any:
        for whole in (False, True):
            made = self.tape.entry(self.theirs, *self.asked, self.now, whole)
            played = self.tape.played(json.loads(json.dumps(made)), self.theirs, self.ours, ours)
            if played == self.now:
                break
        else:  # (the Rust app's answer is another, which a test looks into: kept whole)
            if self._portless(played) != self._portless(self.now):
                self.unlike.append(f"answer {len(self.entries)}")  # (and not played as it was)
        if self.changes:
            made["before"] = self.changes
        if self.shared_changes:
            made["shared"] = self.shared_changes
        self._shared_changes()  # (what the Rust app's server wrote there is not the Python one's)
        changes = self._changes()
        if changes:
            made["home"] = changes
        self.entries.append(made)
        return self.now

    def left(self) -> int:
        return 0


def _answers(at: dict[str, Any]) -> Any:
    """What the Python app's server answered the test's requests (its ``tape``: Recorded)."""
    tape = at["tape"]
    if tape.unlike and OUT:
        with open(Path(OUT) / "unlike.txt", "a", encoding="utf-8") as out:
            out.write(f"{golden._test['name']}: {', '.join(tape.unlike)}\n")
    return tape.entries


TELLERS: dict[str, Any] = {"refused": _refused, "width": _width, "soft": _soft,
                           "saved": _saved, "reach": _reach, "good": _good,
                           "tables": _memory_tables, "listed": _listed,
                           "kinds": _kinds, "folder": _folder_listed, "wanted": _wanted,
                           "asked": _asked, "versions": _versions, "part": _part,
                           "listing": _listing, "catalog": _catalog, "preview": _preview,
                           "specs": _specs, "answers": _answers, "card": _card,
                           "encoded": _encoded, "meter": _meter, "sources": _sources,
                           "gone": _gone, "update": _update, "restart": _restart,
                           "menu": _menu, "logo": _logo, "steps": _steps, "exec": _exec,
                           "panel files": _panel_files, "logo file": _logo_file,
                           "same clip": _same_clip, "shared": _shared, "capture": _capture,
                           "xrandr": _xrandr, "setup": _setup, "rights": _rights,
                           "python": _python, "status": _status}  # fmt: skip


def told(what: str) -> Any:
    """golden.told, from the Python app itself: asked with what the test that asks has at hand
    (its local names), and written down by test file, test and what was asked (a test a line)."""
    value = TELLERS[what](sys._getframe(1).f_locals)
    value = json.loads(json.dumps(value))  # (as it reads back)
    file, name = golden._test["file"], golden._test["name"]
    tests = _tapes.setdefault(file, {})
    tests.setdefault(name, {}).setdefault(what, []).append(value)
    lines = [f"{json.dumps(test, ensure_ascii=False)}: {json.dumps(tape, ensure_ascii=False)}"
             for test, tape in sorted(tests.items())]  # fmt: skip
    text = "{\n" + ",\n".join(lines) + "\n}\n"
    (_folder() / f"{file}.json").write_text(text, encoding="utf-8")
    return value


def install() -> None:
    golden.given = given  # type: ignore[assignment]
    golden.told = told  # type: ignore[assignment]
    golden.untold = lambda: []  # type: ignore[assignment]


def png(image: Any) -> str:
    out = io.BytesIO()
    image.save(out, "PNG", optimize=True)
    return base64.b64encode(out.getvalue()).decode("ascii")

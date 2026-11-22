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
          "waveshare_keys": _waveshare_keys, "memory_defaults": _memory_defaults}  # fmt: skip


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


TELLERS: dict[str, Any] = {"refused": _refused, "width": _width, "soft": _soft,
                           "saved": _saved, "reach": _reach, "good": _good,
                           "tables": _memory_tables}  # fmt: skip


def told(what: str) -> Any:
    """golden.told, from the Python app itself: asked with what the test that asks has at hand
    (its local names), and written down by test file, test and what was asked."""
    value = TELLERS[what](sys._getframe(1).f_locals)
    value = json.loads(json.dumps(value))  # (as it reads back)
    file, name = golden._test["file"], golden._test["name"]
    tests = _tapes.setdefault(file, {})
    tests.setdefault(name, {}).setdefault(what, []).append(value)
    _write(file, dict(sorted(tests.items())), indent=None)
    return value


def install() -> None:
    golden.given = given  # type: ignore[assignment]
    golden.told = told  # type: ignore[assignment]
    golden.untold = lambda: []  # type: ignore[assignment]


def png(image: Any) -> str:
    out = io.BytesIO()
    image.save(out, "PNG", optimize=True)
    return base64.b64encode(out.getvalue()).decode("ascii")

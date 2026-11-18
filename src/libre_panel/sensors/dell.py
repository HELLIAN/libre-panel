"""Dell's fans and temperatures, as Dell Command | Monitor reports them (Windows): its WMI
namespace ``root\\dcim\\sysman`` holds a numeric sensor for each (CIM_NumericSensor, made
DCIM_NumericSensor by Dell), with its name, its reading and the unit of it. Many Dell PCs show
their fans to no other program; with Dell Command | Monitor installed they come as
``fan.dell.<name>`` (RPM) and ``temp.dell.<name>`` (°C), named as it names them ("Processor
Fan" is ``fan.dell.processor_fan``), where every fan and temperature of a PC is.

``[sensors.dell]``::

    update_s = 2   # how often it is asked

The app asks WMI itself, in a thread of its own; this one has PowerShell ask it, in one
PowerShell that stays (Python has no WMI of its own). A PC without Dell Command | Monitor is
said once and asked again only a minute later. A reading that does not come again keeps its
last value for three times ``update_s``, then it goes.
"""

from __future__ import annotations

import json
import logging
import math
import re
import subprocess
import sys
import threading
import time
from typing import Any

from libre_panel.sensors.base import Reading, SensorProvider

log = logging.getLogger(__name__)

NAMESPACE = r"root\dcim\sysman"
FIELDS = ("ElementName", "DeviceID", "SensorType", "BaseUnits", "UnitModifier", "CurrentReading")
ABSENT_S = 60.0  # a namespace that is not there is asked again after this long

# CIM_NumericSensor's SensorType: a temperature, a fan (its tachometer); CIM's BaseUnits:
# degrees Celsius, revolutions per minute
TEMPERATURE, TACHOMETER = 2, 5
DEGREES_C, RPM = 2, 19
_I64 = 1 << 63


def _number(row: dict[str, Any], key: str) -> int | None:
    value = row.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, float):
        return math.trunc(value) if math.isfinite(value) else None
    if isinstance(value, str):
        try:
            value = int(value)
        except ValueError:
            return None
    if isinstance(value, int) and -_I64 <= value < _I64:
        return value
    return None


def _text(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    return value.strip() if isinstance(value, str) else ""


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def readings_from_rows(rows: list[dict[str, Any]]) -> dict[str, Reading]:
    """Each fan as ``fan.dell.<name>`` (RPM) and each temperature as ``temp.dell.<name>``
    (°C), its reading times ten to the power of its UnitModifier; a sensor of another kind, or
    of no reading, is none. A name met again is numbered from its second on
    (``processor_fan_2``); a sensor of no name is named by its DeviceID's last part."""
    out: dict[str, Reading] = {}
    for row in rows:
        kind, units = _number(row, "SensorType"), _number(row, "BaseUnits")
        if kind == TACHOMETER or units == RPM:
            prefix, unit = "fan", "RPM"
        elif kind == TEMPERATURE or units == DEGREES_C:
            prefix, unit = "temp", "°C"
        else:
            continue
        reading = _number(row, "CurrentReading")
        if reading is None:
            continue
        power = max(-12, min(12, _number(row, "UnitModifier") or 0))
        if power >= 0:
            value = float(reading) * float(10**power)
        else:
            value = float(reading) / float(10**-power)
        label = _text(row, "ElementName")
        if not label:
            label = re.split(r"[/\\]", _text(row, "DeviceID"))[-1]
        name = _slug(label) or prefix
        key, nth = f"{prefix}.dell.{name}", 1
        while key in out:
            nth += 1
            key = f"{prefix}.dell.{name}_{nth}"
        out[key] = Reading(key, value, unit, label or name, device="Dell")
    return out


# (one PowerShell that asks every `every` seconds and says each answer on a line of its own)
_SCRIPT = (
    "$ErrorActionPreference='Stop';while($true){{try{{"
    "Get-CimInstance -Namespace '{namespace}' -ClassName CIM_NumericSensor|"
    "Select-Object {fields}|ConvertTo-Json -Compress -Depth 2}}"
    "catch{{'!'+$_.Exception.Message}};[Console]::Out.Flush();Start-Sleep -Milliseconds {ms}}}"
)


class DellProvider(SensorProvider):
    """``fan.dell.*`` and ``temp.dell.*`` from Dell Command | Monitor."""

    name = "dell"

    def __init__(self, options: dict[str, Any] | None = None, *, start: bool = True) -> None:
        super().__init__(options)
        self.every = max(1.0, float(self.options.get("update_s", 2)))
        self._got: tuple[float, dict[str, Reading]] | None = None
        self._failed = False  # said once, until it answers again
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._shell: subprocess.Popen[str] | None = None
        if start:
            threading.Thread(target=self._run, daemon=True, name="sensors-dell").start()

    def take(self, line: str) -> None:
        """One answer of PowerShell: the rows as JSON, or ``!`` and why there are none."""
        line = line.strip()
        if not line:
            return
        if line.startswith("!"):
            self._fail(line[1:])
            return
        try:
            rows = json.loads(line)
        except ValueError as exc:
            self._fail(f"not JSON: {exc}")
            return
        rows = [rows] if isinstance(rows, dict) else rows if isinstance(rows, list) else []
        if self._failed:
            self._failed = False
            log.info("dell: Dell Command | Monitor answers again")
        got = readings_from_rows([row for row in rows if isinstance(row, dict)])
        with self._lock:
            self._got = (time.monotonic(), got)

    def _fail(self, why: str) -> None:
        if not self._failed:
            self._failed = True
            log.warning("dell: no Dell Command | Monitor to ask (%s)", why)

    def _run(self) -> None:
        if sys.platform != "win32":
            self._fail("Dell Command | Monitor is a Windows program")
            return
        script = _SCRIPT.format(
            namespace=NAMESPACE, fields=",".join(FIELDS), ms=int(self.every * 1000)
        )
        while not self._stop.is_set():
            try:
                shell = subprocess.Popen(
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except OSError as exc:
                self._fail(str(exc))
                self._stop.wait(ABSENT_S)
                continue
            self._shell = shell
            assert shell.stdout is not None
            for line in shell.stdout:
                self.take(line)
                if self._stop.is_set():
                    break
                if self._failed:  # (not there: asked again a minute later)
                    break
            shell.kill()
            shell.wait()
            self._stop.wait(ABSENT_S if self._failed else self.every)

    def read(self) -> dict[str, Reading]:
        with self._lock:
            got = self._got
        if got is None or time.monotonic() - got[0] > 3 * self.every:
            return {}
        return dict(got[1])

    def close(self) -> None:
        self._stop.set()
        shell = self._shell
        if shell is not None and shell.poll() is None:
            shell.kill()

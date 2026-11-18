"""Dell's fans and temperatures from Dell Command | Monitor (sensors/dell.py): its sensors as
fan.dell.* and temp.dell.*, what PowerShell says of them, a PC without it said once."""

import json
import logging
import sys

import pytest

from libre_panel.sensors import dell
from libre_panel.sensors.base import available_providers

ROWS = [
    {"ElementName": "Processor Fan", "DeviceID": "Root/MainSystemChassis/FanObj",
     "SensorType": 5, "BaseUnits": 19, "UnitModifier": 0, "CurrentReading": 2345},
    {"ElementName": "System Fan", "SensorType": 1, "BaseUnits": 19, "CurrentReading": 812},
    {"ElementName": "CPU Temperature", "SensorType": 2, "BaseUnits": 2, "UnitModifier": -1,
     "CurrentReading": 523},
    {"ElementName": "CPU Voltage", "SensorType": 3, "BaseUnits": 5, "CurrentReading": 1100},
    {"ElementName": "Rear Fan", "SensorType": 5, "CurrentReading": None},
    {"ElementName": "Processor Fan", "SensorType": 5, "CurrentReading": "1200"},
    {"DeviceID": "Root/MainSystemChassis/Fan2Obj", "SensorType": 5, "CurrentReading": 900},
]  # fmt: skip


def test_fans_and_temperatures_as_dell_command_monitor_reports_them():
    got = dell.readings_from_rows(ROWS)
    assert [(k, r.value, r.unit, r.label) for k, r in got.items()] == [
        ("fan.dell.processor_fan", 2345.0, "RPM", "Processor Fan"),
        ("fan.dell.system_fan", 812.0, "RPM", "System Fan"),
        ("temp.dell.cpu_temperature", 52.3, "°C", "CPU Temperature"),
        ("fan.dell.processor_fan_2", 1200.0, "RPM", "Processor Fan"),
        ("fan.dell.fan2obj", 900.0, "RPM", "Fan2Obj"),
    ]
    assert all(r.device == "Dell" for r in got.values())
    assert "dell" in available_providers()


def test_what_powershell_says_is_read_and_a_missing_one_said_once(caplog, monkeypatch):
    provider = dell.DellProvider({"update_s": 2}, start=False)
    with caplog.at_level(logging.INFO, logger="libre_panel.sensors.dell"):
        provider.take("!Invalid namespace")
        provider.take("!Invalid namespace")
        assert provider.read() == {}
        provider.take(json.dumps(ROWS[0]))  # (one sensor: PowerShell gives no list)
        provider.take("")
    said = [r.getMessage() for r in caplog.records]
    assert said == ["dell: no Dell Command | Monitor to ask (Invalid namespace)",
                    "dell: Dell Command | Monitor answers again"]  # fmt: skip
    assert list(provider.read()) == ["fan.dell.processor_fan"]
    provider.take(json.dumps(ROWS))
    assert len(provider.read()) == 5
    # (what came last goes three times update_s after)
    now = dell.time.monotonic()
    monkeypatch.setattr(dell.time, "monotonic", lambda: now + 6.5)
    assert provider.read() == {}
    provider.take("not json")
    assert provider._failed


@pytest.mark.skipif(sys.platform == "win32", reason="Windows asks PowerShell")
def test_elsewhere_it_says_once_that_it_is_a_windows_program(caplog):
    with caplog.at_level(logging.WARNING, logger="libre_panel.sensors.dell"):
        provider = dell.DellProvider({}, start=False)
        provider._run()
    assert provider.read() == {} and provider.every == 2.0
    assert [r.getMessage() for r in caplog.records] == [
        "dell: no Dell Command | Monitor to ask (Dell Command | Monitor is a Windows program)"
    ]
    provider.close()

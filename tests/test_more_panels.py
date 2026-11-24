"""More panels at once ([[panels]]): what config.toml says of each, and the readings each panel's
hub takes from the sources (sensors/base.py)."""

import re
import threading
import time

import pytest

from libre_panel.config import ConfigError, parse_config
from libre_panel.sensors.base import HubView, Reading, SensorHub, SensorProvider


def test_more_panels_are_read_with_the_keys_of_device_and_a_theme():
    config = parse_config(
        {
            "theme": "studio",
            "device": {"model": "turing-12.3-usb", "brightness": 70},
            "video": {"mode": "on", "local_clip": "usr/data/clip.h264"},
            "sequence": {"steps": [{"theme": "lumen", "seconds": 30}]},
            "panels": [
                {"theme": "lumen", "model": "turing-3.5", "port": " COM5 ", "brightness": 40},
                {"on": False},
            ],
        }
    )
    first, second = config.panels
    assert (first.theme, first.on, first.device.model) == ("lumen", True, "turing-3.5")
    assert (first.device.port, first.device.brightness) == ("COM5", 40)
    # found as a panel, never drawn into a file meanwhile; a file of its own where it is
    assert first.device.driver == "turzx" and first.device.output == "libre-panel-frame-2.png"
    assert (second.on, second.theme, second.device.output) == (False, "", "libre-panel-frame-3.png")

    own = config.for_panel(0)
    assert own.device.model == "turing-3.5"
    # smooth motion is each panel's own, off to begin with: the first's clip goes to no other
    assert own.device.video.mode == "off" and config.device.video.mode == "on"
    assert own.device.video.local_clip == "" and own.device.video.fps == config.device.video.fps
    assert (own.theme, own.theme_given) == ("lumen", True)
    assert not own.sequence.active and own.panels == []  # no screens in turn, no more panels
    assert config.sequence.active and config.device.brightness == 70  # the first's as they were
    assert config.for_panel(1).theme_given is False  # no theme: the panel's own


@pytest.mark.parametrize(
    "panels, said",
    [
        ([{"brightness": 101}], "panels[0].brightness"),
        ([{}, {"rotate": 90}], "panels[1].rotate"),
        ([{"theme": 3}], "panels[0].theme"),
        ([{}] * 4, "at most 3"),
        ({"theme": "lumen"}, "panels"),
    ],
)
def test_a_wrong_panel_is_said_by_its_place(panels, said):
    with pytest.raises(ConfigError, match=re.escape(said)):
        parse_config({"panels": panels})


class Counting(SensorProvider):
    name = "counting"

    def __init__(self):
        super().__init__()
        self.reads = 0

    def read(self):
        self.reads += 1
        return {"cpu.temp": Reading("cpu.temp", 40.0 + self.reads, "°C")}


def test_another_panel_takes_the_readings_the_first_took_and_keeps_its_own_history(monkeypatch):
    source = Counting()
    hub = SensorHub([source])
    view = HubView(hub)
    hub.snapshot()
    snap = view.snapshot()  # just read: taken as they are
    assert source.reads == 1 and snap.value("cpu.temp") == 41.0
    assert snap.history["cpu.temp"] == [41.0]
    monkeypatch.setattr(HubView, "SHARE_S", 0.0)
    time.sleep(0.01)
    snap = view.snapshot()  # too old: read anew, for both
    assert source.reads == 2 and snap.history["cpu.temp"] == [41.0, 42.0]
    assert hub.snapshot().history["cpu.temp"] == [41.0, 43.0]  # the first's history, its own
    view.follow_config(None)
    view.close()  # nothing: the sources are the first panel's
    assert hub.providers == [source] and view.providers is hub.providers


def test_a_slow_source_holds_up_no_reading_of_every_frame():
    """The first panel takes a snapshot while a slow source (the hardware's) is read; the
    other panel's readings of every frame come from a quick one meanwhile, without waiting."""
    entered, release = threading.Event(), threading.Event()

    class Slow(SensorProvider):
        name = "slow"

        def read(self):
            entered.set()
            release.wait(5)
            return {"cpu.temp": Reading("cpu.temp", 50.0, "°C")}

    class Quick(SensorProvider):
        name = "quick"
        every_frame = True

        def read(self):
            return {"cpu.load": Reading("cpu.load", 12.0, "%")}

    hub = SensorHub([Slow(), Quick()])
    taking = threading.Thread(target=hub.snapshot)
    taking.start()
    try:
        assert entered.wait(5)
        began = time.monotonic()
        assert HubView(hub).fresh()["cpu.load"].value == 12.0
        # (a reading that waited for the slow source would take its 5 s: 3 s tell them apart,
        # and leave a slow runner room)
        assert time.monotonic() - began < 3.0
    finally:
        release.set()
        taking.join(5)


def test_a_panel_follows_the_first_one_s_brightness_with_a_difference_of_its_own():
    config = parse_config(
        {
            "device": {
                "brightness": 60,
                "night": True,
                "night_brightness": 0,
                "away_brightness": 20,
            },
            "panels": [{"brightness_offset": 15}, {"follow_brightness": False, "brightness": 30}],
        }
    )
    follows, own = config.for_panel(0).device, config.for_panel(1).device
    assert (follows.brightness, follows.night, follows.away_brightness) == (75, True, 35)
    assert follows.night_brightness == 0  # dark at night stays dark
    assert (own.brightness, own.night) == (30, False)  # its own
    with pytest.raises(ConfigError, match=re.escape("panels[0].brightness_offset")):
        parse_config({"panels": [{"brightness_offset": 70}]})
    assert parse_config({"device": {"brightness": 95}, "panels": [{"brightness_offset": 20}]}
                        ).for_panel(0).device.brightness == 100  # fmt: skip


def test_same_look_is_read_from_the_panel_s_table():
    config = parse_config({"panels": [{"theme": "lumen-companion", "same_look": True}, {}]})
    assert config.panels[0].same_look and config.for_panel(0).same_look
    assert not config.for_panel(1).same_look and not config.same_look
    with pytest.raises(ConfigError, match="same_look"):
        parse_config({"panels": [{"same_look": "yes"}]})


def test_a_panel_beside_the_first_has_what_follows_on_every_frame_as_it_is_now():
    class Services(SensorProvider):  # (as the services' readings come: an alarm)
        name, every_frame = "services", True

        def __init__(self):
            super().__init__()
            self.alarm = 0

        def read(self):
            return {"alarm": Reading("alarm", self.alarm)}

    services = Services()
    first = SensorHub([services])
    first.snapshot()  # the first panel's readings, a moment ago
    services.alarm = 1
    shared = HubView(first).snapshot()
    assert shared.value("alarm") == 1  # not as it was when the first panel took its readings


def test_each_panel_knows_the_models_the_others_name():
    config = parse_config({"device": {"model": "auto"},
                           "panels": [{"model": "turing-9.2-usb"}, {"model": "auto"}]})  # fmt: skip
    assert config.device.beside == ("turing-9.2-usb",)
    assert config.for_panel(0).device.beside == ()  # the first and the third are "auto"
    assert config.for_panel(1).device.beside == ("turing-9.2-usb",)

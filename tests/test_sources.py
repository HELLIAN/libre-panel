import pytest
from PIL import Image

from libre_panel.config import (
    Config,
)
from libre_panel.sensors import base
from libre_panel.sensors.base import (
    Reading,
    SensorHub,
    SensorProvider,
    available_providers,
)


class Fixed(SensorProvider):
    def __init__(self, values):
        super().__init__()
        self.values = values

    def read(self):
        return {k: Reading(k, v) for k, v in self.values.items()}


class Broken(SensorProvider):
    name = "broken"

    def read(self):
        raise RuntimeError("sensor died")


def test_hub_priority_history_and_resilience():
    hub = SensorHub(
        [Broken(), Fixed({"cpu.temp": 60.0}), Fixed({"cpu.temp": 99.0, "gpu.temp": 50.0})]
    )
    snap = hub.snapshot()
    assert snap.value("cpu.temp") == 60.0  # earlier provider wins
    assert snap.value("gpu.temp") == 50.0
    hub.snapshot()
    assert snap.value("missing") is None
    assert hub.snapshot().history["cpu.temp"] == [60.0, 60.0, 60.0]


def test_every_frame_providers_follow_between_snapshots():
    class Volume(Fixed):
        every_frame = True

    slow = Fixed({"cpu.temp": 60.0, "audio.volume": 10.0})  # first: its volume wins
    volume = Volume({"audio.volume": 50.0, "audio.muted": 0.0, "cpu.load": 5.0})
    hub = SensorHub([slow, volume])
    assert hub.every_frame and not SensorHub([slow]).every_frame
    snap = hub.snapshot()
    volume.values.update({"audio.volume": 80.0, "audio.muted": 1.0, "cpu.load": 7.0})
    slow.values["cpu.temp"] = 70.0
    fresh = hub.fresh()
    assert fresh == {"audio.muted": fresh["audio.muted"], "cpu.load": fresh["cpu.load"]}
    assert fresh["audio.muted"].value == 1.0  # its own keys follow at once
    assert "audio.volume" not in fresh  # the earlier provider's key stays its own
    assert snap.history["cpu.load"] == [5.0]  # no history between snapshots
    assert hub.snapshot().history["cpu.load"] == [5.0, 7.0]


def test_a_history_is_each_sensors_last_numbers_as_a_snapshot_saw_them():
    """The last HISTORY_LENGTH numbers of each sensor, as a deque of them was at the snapshot:
    also a snapshot drawn from while up to HISTORY_SLACK more came; one older still keeps the
    newest of its own, none it did not see."""
    import random
    from array import array
    from collections import deque

    from libre_panel.sensors.base import HISTORY_LENGTH, HISTORY_SLACK, Series

    random.seed(3)
    series, seen, taken = Series(), [], []
    for k in range(3 * Series.SIZE):
        seen.append(random.random())
        series.append(seen[-1])
        taken.append(series.total)
        for total in taken[-(HISTORY_SLACK + 20) :: 7]:
            want = list(deque(seen[:total], maxlen=HISTORY_LENGTH))
            got = series.last(total)
            if series.total - total < HISTORY_SLACK:
                assert got == want, (k, total)
            else:  # (overwritten since: its oldest go, the rest are its own)
                assert got == want[len(want) - len(got) :], (k, total)
    assert isinstance(series._ring, array) and len(series._ring) == Series.SIZE
    assert Series().last(0) == []


def test_a_snapshots_history_is_read_as_a_dict_its_lists_made_when_asked():
    from libre_panel.sensors.base import HistoryView

    values = {"cpu.temp": 60.0, "gpu.temp": 50.0, "name": "Ryzen"}  # (a text has no history)
    source = Fixed(values)
    hub = SensorHub([source])
    first = hub.snapshot()
    values["cpu.temp"] = 61.0
    second = hub.snapshot()
    history = second.history
    assert isinstance(history, HistoryView) and not history._lists  # (nothing asked yet)
    assert sorted(history) == ["cpu.temp", "gpu.temp"] and len(history) == 2
    assert "cpu.temp" in history and "name" not in history and "gone" not in history
    assert history["cpu.temp"] == [60.0, 61.0] and history.get("name", []) == []
    assert history["cpu.temp"] is history["cpu.temp"]  # (made once)
    assert list(history._lists) == ["cpu.temp"]  # (only what was asked for)
    assert first.history["cpu.temp"] == [60.0]  # (each snapshot's as it was)
    history["cpu.load"] = [5.0, 7.0]  # (the editor's, a test's: set here, it stands)
    history.update({"gpu.temp": [1.0]})
    assert history["cpu.load"] == [5.0, 7.0] and history["gpu.temp"] == [1.0]
    assert dict(history) == {"cpu.temp": [60.0, 61.0], "gpu.temp": [1.0], "cpu.load": [5.0, 7.0]}
    del history["cpu.load"]
    assert "cpu.load" not in history and len(history) == 2
    with pytest.raises(KeyError):
        del history["cpu.load"]


# -- sources with names of their own --------------------------------------------------------------


class Other(SensorProvider):
    """A source that has names of its own (LibreHardwareMonitor has two of them)."""

    name = "other"

    def __init__(self, **values):
        super().__init__()
        self.values = values

    def read(self):
        return {key: Reading(key, value, "", key) for key, value in self.values.items()}


def test_a_source_that_does_not_override_comes_second_as_ever():
    first, second = Other(**{"cpu.name": "first"}), Other(**{"cpu.name": "second"})
    assert SensorHub([first, second]).snapshot().value("cpu.name") == "first"
    assert SensorProvider.overrides == ()


def test_the_hub_tells_the_sources_the_config_and_a_broken_one_does_not_stop_the_rest(caplog):
    heard = []

    class Listens(Other):
        def follow_config(self, config):
            heard.append(config)

    class Breaks(Other):
        name = "breaks"

        def follow_config(self, config):
            raise RuntimeError("no")

    class Bare:  # written without the base class
        name = "bare"

        def read(self):
            return {}

    config = Config()
    SensorHub([Breaks(), Bare(), Listens()]).follow_config(config)
    assert heard == [config]
    assert "sensor provider breaks failed" in caplog.text
    assert SensorProvider().follow_config(config) is None  # a source with no settings of this kind


# -- pictures of the sources that follow every frame ----------------------------------------------


class Player(base.SensorProvider):
    """A source that follows on every frame, as the media source does, with its cover."""

    name = "player"
    every_frame = True

    def __init__(self):
        super().__init__({})
        self.title, self.cover = "Song A", Image.new("RGB", (4, 4), "red")

    def read(self):
        return {"media.title": base.Reading("media.title", self.title)}

    def images(self):
        return {"media.cover": self.cover} if self.cover is not None else {}


def test_the_hub_hands_on_the_pictures_of_the_sources_that_follow_every_frame():
    player = Player()
    hub = SensorHub([player])
    assert hub.fresh_images() == {"media.cover": player.cover}
    player.cover = None  # a track without one: gone, not the old one
    assert hub.fresh_images() == {"media.cover": None}
    assert hub.fresh_images() == {}


# -- sources to choose ----------------------------------------------------------------------------


def test_nut_is_a_source_to_choose():
    assert "nut" in available_providers()

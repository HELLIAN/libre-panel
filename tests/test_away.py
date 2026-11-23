"""[device] dark_when_away: the panels dim while the computer is locked or its monitor is off
(Windows tells the session window), and light up when the person is back."""

import pytest

from libre_panel import presence
from libre_panel.config import DeviceConfig, brightness_now


@pytest.fixture(autouse=True)
def nobody_away():
    presence.reset()
    yield
    presence.reset()


def test_the_person_is_away_while_one_reason_holds():
    before = presence.changes()
    presence.set_away(presence.LOCKED, True)
    presence.set_away(presence.DISPLAY_OFF, True)
    assert presence.away() and presence.changes() == before + 1  # (away once)
    presence.set_away(presence.LOCKED, False)
    assert presence.away()  # the monitor is still off
    presence.set_away(presence.DISPLAY_OFF, False)
    assert not presence.away() and presence.changes() == before + 2


def test_away_the_panel_takes_the_brightness_for_then_unless_told_not_to():
    device = DeviceConfig(brightness=60, away_brightness=0)
    assert brightness_now(device, away=True) == 0 and brightness_now(device, away=False) == 60
    assert brightness_now(DeviceConfig(brightness=60, dark_when_away=False), away=True) == 60
    presence.set_away(presence.LOCKED, True)
    assert brightness_now(DeviceConfig(brightness=60, away_brightness=15)) == 15  # as said

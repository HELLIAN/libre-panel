"""Sun and moon (sensors/sky.py): sunrise and sunset, polar night and midnight sun, the moon's
phases, and the sky's readings at a place."""

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from libre_panel.sensors.sky import SkyProvider, moon_name, moon_phase, sky_readings, sun_times


def local(t, hours):
    return datetime.fromtimestamp(t, timezone(timedelta(hours=hours)))


@pytest.mark.parametrize(
    ("day", "place", "hours", "rise", "down"),
    [
        (date(2026, 6, 21), (52.52, 13.405), 2, "04:43", "21:33"),  # Berlin, midsummer
        (date(2026, 12, 21), (40.71, -74.0), -5, "07:16", "16:31"),  # New York, midwinter
        (date(2026, 3, 20), (-33.87, 151.21), 11, "06:58", "19:07"),  # Sydney, equinox
    ],
)
def test_sunrise_and_sunset(day, place, hours, rise, down):
    def minutes(text):
        return int(text[:2]) * 60 + int(text[3:])

    sun = sun_times(day, *place)  # published times, to within two minutes
    assert abs(minutes(local(sun["rise"], hours).strftime("%H:%M")) - minutes(rise)) <= 2
    assert abs(minutes(local(sun["set"], hours).strftime("%H:%M")) - minutes(down)) <= 2


def test_polar_night_and_midnight_sun():
    assert sun_times(date(2026, 12, 21), 69.65, 18.96)["always"] == "night"
    assert sun_times(date(2026, 6, 21), 69.65, 18.96)["always"] == "day"
    readings = sky_readings(datetime(2026, 12, 21, 12, tzinfo=UTC).timestamp(), 69.65, 18.96)
    assert "sun.rise" not in readings and readings["sun.up"].value == 0


def test_moon_phases():
    full = datetime(2026, 10, 26, 4, 12, tzinfo=UTC).timestamp()  # a full moon
    assert moon_phase(full) == pytest.approx(0.5, abs=0.03)
    assert moon_name(moon_phase(full)) == "Full moon"
    assert moon_name(0.0) == moon_name(0.99) == "New moon"
    assert moon_name(0.25) == "First quarter" and moon_name(0.75) == "Last quarter"
    readings = sky_readings(full)
    assert readings["moon.illumination"].value > 99 and "sun.rise" not in readings  # no place
    assert readings["moon.full_in"].value == pytest.approx(0, abs=1) or (
        readings["moon.full_in"].value > 28
    )


def test_sky_readings_with_a_place():
    noon = datetime(2026, 6, 21, 11, tzinfo=UTC).timestamp()
    readings = SkyProvider({"latitude": 52.52, "longitude": 13.405}).read()
    assert {"sun.rise", "sun.set", "moon.phase"} <= set(readings)
    day = sky_readings(noon, 52.52, 13.405)
    assert day["sun.up"].value == 1 and 0.3 < day["sun.progress"].value < 0.7
    assert day["sun.elevation"].value > 55 and day["sun.daylight"].value > 16 * 3600
    night = sky_readings(noon + 12 * 3600, 52.52, 13.405)
    assert night["sun.up"].value == 0 and night["sun.progress"].value is None

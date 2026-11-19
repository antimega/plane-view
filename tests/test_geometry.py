import datetime as dt
import math

import numpy as np
import pytest

from planeview import geo, seat
from planeview.sources import archive


def test_distance_and_bearing():
    # London Heathrow to Paris CDG is 347-348 km on the great circle
    assert float(geo.dist_km(51.4706, -0.4619, 49.0097, 2.5479)) == pytest.approx(347.5, abs=1.5)
    assert float(geo.bearing(0, 0, 0, 10)) == pytest.approx(90)
    assert float(geo.bearing(0, 0, 10, 0)) == pytest.approx(0)
    la, lo = geo.destination(51.0, 0.0, 45.0, 100.0)
    assert float(geo.dist_km(51.0, 0.0, la, lo)) == pytest.approx(100, abs=0.01)
    assert float(geo.wrap180(350)) == pytest.approx(-10)


def test_horizon_dip_matches_closed_form():
    h = 11000.0                                    # metres, about 36,000 ft
    assert float(geo.dip_deg(h)) == pytest.approx(math.degrees(math.sqrt(2 * h / geo.R_EFF)), rel=1e-9)
    # with standard refraction the horizon from 11 km is about 400 km away and 3.1 degrees down
    assert float(geo.horizon_km(h)) == pytest.approx(401, abs=6)
    assert float(geo.dip_deg(h)) == pytest.approx(3.14, abs=0.05)
    assert float(geo.dip_deg(0)) == 0


def test_sun_position():
    # Greenwich, June solstice 2026, local noon: the sun is due south, 62 degrees up (90 - 51.48 + 23.44)
    t = dt.datetime(2026, 6, 21, 12, 2, tzinfo=dt.UTC).timestamp()
    az, el = geo.sun_az_el(t, 51.48, 0.0)
    assert float(el) == pytest.approx(61.96, abs=0.3)
    assert float(az) == pytest.approx(180, abs=1.5)
    # sunrise that day at Greenwich is 03:43 UTC: the centre of the sun is on the horizon within a degree
    t = dt.datetime(2026, 6, 21, 3, 43, tzinfo=dt.UTC).timestamp()
    assert abs(float(geo.sun_az_el(t, 51.48, 0.0)[1])) < 1.2
    # midnight: well below
    assert float(geo.sun_az_el(dt.datetime(2026, 6, 21, 0, 0, tzinfo=dt.UTC).timestamp(), 51.48, 0.0)[1]) < -10


def test_cross_track_sign():
    # flying east along the equator, a point to the south is on the right
    right = float(geo.cross_track_km(-1.0, 5.0, 0, 0, 0, 10))
    left = float(geo.cross_track_km(1.0, 5.0, 0, 0, 0, 10))
    assert right == pytest.approx(-left, rel=1e-6) and abs(right) == pytest.approx(111.2, abs=0.5)
    assert right > 0


@pytest.mark.parametrize("s, actype, side, window", [
    ("16A", "B738", "L", True), ("16F", "B738", "R", True), ("16C", "B738", "L", False), ("16D", "B738", "R", False),
    ("40K", "B789", "R", True), ("40A", "B789", "L", True), ("40E", "B789", None, False), ("40H", "B789", "R", False),
    ("23F", None, "R", True), ("23K", None, "R", True), ("L", None, "L", True), ("right", None, "R", True),
])
def test_seat_side(s, actype, side, window):
    got = seat.parse(s, actype)
    assert (got["side"], got["window"]) == (side, window)


def test_seat_wing_and_errors():
    assert seat.parse("14A", "B738")["wing"] == "over"
    assert seat.parse("22A", "B738")["wing"] == "behind"
    assert seat.parse("30A", "B738")["wing"] is None
    assert seat.parse(None)["side"] is None
    with pytest.raises(ValueError):
        seat.parse("window please")


def test_rdp_keeps_corners_and_drops_straights():
    x = np.linspace(0, 100, 101)
    P = np.column_stack([x, np.where(x <= 50, 0.0, x - 50.0)])     # straight, then a 45-degree turn at x = 50
    assert archive.rdp(P, 0.3).tolist() == [0, 50, 100]
    assert archive.rdp(P[:2], 0.3).tolist() == [0, 1]


def test_day_specs():
    d = dt.date
    assert archive.parse_days(["2026-11-16"]) == [d(2026, 11, 16)]
    assert archive.parse_days(["2026-10-30..2026-11-02"]) == [d(2026, 10, 30), d(2026, 10, 31), d(2026, 11, 1), d(2026, 11, 2)]
    assert archive.parse_days(["2026-11-01..2026-11-20/7", "2026-11-08"]) == [d(2026, 11, 1), d(2026, 11, 8), d(2026, 11, 15)]
    with pytest.raises(ValueError):
        archive.parse_days(["2026-11-05..2026-11-01"])
    assert archive.latest_days(3, today=d(2026, 11, 17)) == [d(2026, 11, 14), d(2026, 11, 15), d(2026, 11, 16)]

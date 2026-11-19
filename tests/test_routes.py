"""Trace splitting, route collection and joining, weights - on synthetic flights between real airports."""
import datetime as dt
import gzip
import json

import numpy as np
import pytest

from planeview import geo, route
from planeview.sources import archive, library

from conftest import make_leg

DAY = dt.datetime(2026, 11, 1, tzinfo=dt.UTC).timestamp()


def fly(a, b, t_start, n=80, cruise=36000, speed_kmh=800.0, f0=0.0, f1=1.0):
    """[t, lat, lon, alt, gs] along the great circle from airport a to b, between fractions f0 and f1 of the way."""
    km = float(geo.dist_km(a["lat"], a["lon"], b["lat"], b["lon"]))
    la, lo = geo.gc_points(a["lat"], a["lon"], b["lat"], b["lon"], 400)
    f = np.linspace(f0, f1, n)
    k = np.clip((f * 399).round().astype(int), 0, 399)
    alt = np.minimum(cruise, np.minimum(f, 1 - f) * km / 150 * cruise).round(-2)
    return [[t_start + float(x) * km / speed_kmh * 3600, float(la[i]), float(lo[i]), int(z), 440]
            for x, i, z in zip(f - f0, k, alt)]


def trace(points, hexid="400abc", callsign="TST12A"):
    t0 = points[0][0]
    rows = [[p[0] - t0, p[1], p[2], p[3], p[4], 90.0, 0, 0, {"flight": callsign + "  "}]
            for p in points]
    return gzip.compress(json.dumps(dict(icao=hexid, r="G-TEST", t="A320", timestamp=t0, trace=rows)).encode())


def test_trace_becomes_one_leg_between_its_airports(airports):
    archive._AP = airports
    lgw, ath = airports.get("LGW"), airports.get("ATH")
    legs = archive.legs_of(trace(fly(lgw, ath, DAY + 8 * 3600, n=300)), DAY)
    assert len(legs) == 1
    lg = legs[0]
    assert (lg["fr"], lg["to"], lg["cs"], lg["hex"]) == ("LGW", "ATH", "TST12A", "400abc")
    assert 5 < len(lg["pts"]) < 300                      # simplified, but the climb and descent keep their shape
    assert lg["pts"][0][0] == 0 and lg["max_alt"] == 36000
    assert library.unpack(library.pack(lg["pts"])) == lg["pts"]


def test_turnaround_splits_into_two_legs_and_light_aircraft_are_dropped(airports):
    archive._AP = airports
    lgw, opo = airports.get("LGW"), airports.get("OPO")
    out = fly(lgw, opo, DAY + 6 * 3600, n=200)
    back = fly(opo, lgw, out[-1][0] + 50 * 60, n=200)       # 50 minutes on the ground, unseen
    legs = archive.legs_of(trace(out + back), DAY)
    assert [(lg["fr"], lg["to"]) for lg in legs] == [("LGW", "OPO"), ("OPO", "LGW")]
    low = fly(lgw, opo, DAY + 6 * 3600, n=200, cruise=5000)
    assert archive.legs_of(trace(low), DAY) == []


def test_flight_crossing_midnight_is_kept_as_an_open_ended_piece(airports):
    archive._AP = airports
    lhr, phx = airports.get("LHR"), airports.get("PHX")
    pts = [p for p in fly(lhr, phx, DAY + 86400 - 4 * 3600, n=600) if p[0] < DAY + 86400 - 60]
    legs = archive.legs_of(trace(pts), DAY)
    assert len(legs) == 1 and legs[0]["fr"] == "LHR" and legs[0]["to"] is None and legs[0]["cont_next"]


def _store(tmp_days):
    con = library.connect()
    for day, legs in tmp_days.items():
        library.add_day(con, day, legs)
    con.close()


def _piece(a, b, t_start, f0, f1, fr, to, **kw):
    pts = fly(a, b, t_start, n=60, f0=f0, f1=f1)
    t0 = pts[0][0]
    return make_leg(fr, to, t0=t0, pts=[[int(p[0] - t0), round(p[1], 4), round(p[2], 4), p[3], p[4]] for p in pts], **kw)


def test_collect_direct_legs_and_leave_one_day_out(airports):
    lgw, ath = airports.get("LGW"), airports.get("ATH")
    _store({"2026-11-01": [_piece(lgw, ath, DAY + 3600, 0.0, 1.0, "LGW", "ATH", cs="SEH1AB")],
            "2026-11-02": [_piece(lgw, ath, DAY + 90000, 0.0, 1.0, "LGW", "ATH", cs="SEH1AB"),
                           _piece(ath, lgw, DAY + 99000, 0.0, 1.0, "ATH", "LGW", cs="SEH2CD")],
            "2026-11-03": [_piece(lgw, ath, DAY + 180000, 0.0, 1.0, "LGW", "ATH", cs="EZY88")]})
    legs = route.collect("LGW", "ATH", log=lambda m: None)
    assert [(lg["kind"], lg["day"], lg["cs"]) for lg in legs] == [
        ("direct", "2026-11-01", "SEH1AB"), ("direct", "2026-11-02", "SEH1AB"), ("direct", "2026-11-03", "EZY88")]
    held_out = route.collect("LGW", "ATH", exclude_days=["2026-11-02"], log=lambda m: None)
    assert [lg["day"] for lg in held_out if lg["kind"] == "direct"] == ["2026-11-01", "2026-11-03"]
    assert all("dep_rwy" in lg and "arr_rwy" in lg for lg in legs)


def test_runway_from_the_first_and_last_kilometres(airports):
    lgw = airports.get("LGW")
    rw = {e["ident"]: e for e in __import__("planeview.sources.airports", fromlist=["x"]).runways(lgw["icao"])}
    west = rw["26L"]
    # a departure rolling from the 26L threshold and climbing straight ahead on the runway heading
    km = np.array([0.0, 3.0, 8.0, 20.0, 60.0])
    la, lo = geo.destination(west["lat"], west["lon"], west["hdg"], km)
    dep = dict(lat=np.asarray(la), lon=np.asarray(lo))
    assert route.rwy_dir(route.runway_of(dep, lgw, "dep")) == "26"
    # an arrival flying the same line the other way round lands on 08
    arr = dict(lat=np.asarray(la)[::-1], lon=np.asarray(lo)[::-1])
    assert route.rwy_dir(route.runway_of(arr, lgw, "arr")) == "08"
    # a track that never comes near the airport gives no runway rather than a guess
    far = dict(lat=np.asarray(la) + 1.0, lon=np.asarray(lo))
    assert route.runway_of(far, lgw, "dep") is None


def test_ocean_gap_same_aircraft_is_joined(airports):
    lhr, phx = airports.get("LHR"), airports.get("PHX")
    t = DAY + 10 * 3600
    km = float(geo.dist_km(lhr["lat"], lhr["lon"], phx["lat"], phx["lon"]))
    head = _piece(lhr, phx, t, 0.0, 0.25, "LHR", None, cs="BAW9CG", hexid="406aaa")
    tail = _piece(lhr, phx, t + 0.6 * km / 800 * 3600, 0.6, 1.0, None, "PHX", cs="BAW9CG", hexid="406aaa")
    other = _piece(lhr, phx, t + 0.6 * km / 800 * 3600, 0.6, 1.0, None, "PHX", cs="AAL195", hexid="a00001")
    _store({"2026-11-01": [head, tail, other]})
    legs = route.collect("LHR", "PHX", log=lambda m: None)
    joined = [lg for lg in legs if lg["kind"] == "joined"]
    assert len(joined) == 1 and joined[0]["cs"] == "BAW9CG" and joined[0]["pieces"] == 2
    assert joined[0]["gaps"], "the unseen ocean stretch must be flagged"
    assert float(geo.dist_km(joined[0]["lat"][-1], joined[0]["lon"][-1], phx["lat"], phx["lon"])) < 5


def test_midnight_gap_is_spliced_with_the_same_callsign_from_another_day(airports):
    lhr, phx = airports.get("LHR"), airports.get("PHX")
    km = float(geo.dist_km(lhr["lat"], lhr["lon"], phx["lat"], phx["lon"]))
    # day 1: the departure piece, to 85% of the way; the next day was not sampled
    head = _piece(lhr, phx, DAY + 15 * 3600, 0.0, 0.85, "LHR", None, cs="BAW9CG", hexid="406aaa", cont_next=True)
    # a week later: only the arrival piece of the same callsign, another aircraft
    tail = _piece(lhr, phx, DAY + 7 * 86400 + 600, 0.8, 1.0, None, "PHX", cs="BAW9CG", hexid="406bbb", cont_prev=True)
    _store({"2026-11-01": [head], "2026-11-08": [tail]})
    legs = route.collect("LHR", "PHX", log=lambda m: None)
    spliced = [lg for lg in legs if lg["kind"] == "joined"]
    assert len(spliced) == 1 and "spliced" in spliced[0]["note"]
    assert np.all(np.diff(spliced[0]["t"]) > 0), "time must run forwards across the splice"
    total = float(np.sum(geo.dist_km(spliced[0]["lat"][:-1], spliced[0]["lon"][:-1], spliced[0]["lat"][1:], spliced[0]["lon"][1:])))
    assert total == pytest.approx(km, rel=0.03)


def test_no_history_falls_back_to_borrowed_then_great_circle(airports):
    library.connect().close()
    legs = route.collect("LGW", "ATH", log=lambda m: None)
    assert [lg["kind"] for lg in legs] == ["great circle"]
    lgw, ath, skg, stn = (airports.get(c) for c in ("LGW", "ATH", "SKG", "STN"))
    _store({"2026-11-01": [_piece(lgw, skg, DAY + 3600, 0.0, 1.0, "LGW", "SKG"),
                           _piece(stn, ath, DAY + 7200, 0.0, 1.0, "STN", "ATH")]})
    legs = route.collect("LGW", "ATH", log=lambda m: None)
    assert [lg["kind"] for lg in legs] == ["borrowed"] and "LGW-SKG" in legs[0]["note"] and "STN-ATH" in legs[0]["note"]


def test_weights_favour_callsign_airline_time_and_month():
    base = dict(kind="direct", takeoff=dt.datetime(2026, 11, 1, 9, 0, tzinfo=dt.UTC).timestamp(), tz="UTC")
    legs = [dict(base, cs="SEH1AB", day="2026-11-01"), dict(base, cs="SEH99", day="2026-11-01"),
            dict(base, cs="EZY1", day="2026-11-01"), dict(base, cs="EZY1", day="2026-06-01"),
            dict(base, cs="EZY1", day="2026-06-01", takeoff=base["takeoff"] + 8 * 3600),
            dict(base, cs=None, day="2026-06-01", kind="borrowed", takeoff=None)]
    w = route.weigh(legs, flight_cs="SEH1AB", airline="SEH", dep_local_min=9 * 60, date=dt.date(2026, 11, 20))
    assert w.sum() == pytest.approx(1)
    r = w / w[4]                       # against a leg that matches nothing
    assert r.tolist() == pytest.approx([3 * 1.5 * 1.3, 2 * 1.5 * 1.3, 1.5 * 1.3, 1.5, 1.0, 0.5])


def test_variants_are_sorted_most_likely_first(airports):
    lgw, ath = airports.get("LGW"), airports.get("ATH")
    east = [_piece(lgw, ath, DAY + 3600 * k, 0.0, 1.0, "LGW", "ATH") for k in range(1, 4)]
    _store({"2026-11-01": east})
    legs = route.collect("LGW", "ATH", log=lambda m: None)
    v = route.variants(legs, route.weigh(legs), "LGW", "ATH")
    assert v[0]["id"] == "V1" and sum(x["p"] for x in v) == pytest.approx(1, abs=0.01)
    assert all(a["p"] >= b["p"] for a, b in zip(v, v[1:]))


def test_a_landing_lost_on_the_approach_still_counts(airports):
    lgw, scq, lcg = airports.get("LGW"), airports.get("SCQ"), airports.get("LCG")
    km = float(geo.dist_km(lgw["lat"], lgw["lon"], scq["lat"], scq["lon"]))
    seen = _piece(lgw, scq, DAY + 3600, 0.0, 1.0, "LGW", "SCQ")
    # the receiver loses this one 14 km out, at 2,300 ft above the field: the scanner left its destination unknown
    lost = _piece(lgw, scq, DAY + 7200, 0.0, 1 - 14 / km, "LGW", None, hexid="aaa111")
    lost["pts"][-1][3] = int(scq["elev_ft"] + 2300)
    # this one is still at 31,000 ft when it vanishes 30 km out: not a landing at Santiago
    cruising = _piece(lgw, scq, DAY + 9000, 0.0, 1 - 30 / km, "LGW", None, hexid="bbb222")
    cruising["pts"][-1][3] = 31000
    # and this one is low, but nearer A Coruna than Santiago
    elsewhere = _piece(lgw, lcg, DAY + 9900, 0.0, 1 - 6 / float(geo.dist_km(lgw["lat"], lgw["lon"], lcg["lat"], lcg["lon"])),
                       "LGW", None, hexid="ccc333")
    elsewhere["pts"][-1][3] = 1500
    # cut by midnight rather than by coverage: left for the joiner
    midnight = dict(_piece(lgw, scq, DAY + 86000, 0.0, 1 - 14 / km, "LGW", None, hexid="ddd444"), cont_next=True)
    midnight["pts"][-1][3] = 2500
    _store({"2026-11-01": [seen, lost, cruising, elsewhere, midnight]})
    legs = route.collect("LGW", "SCQ", log=lambda m: None)
    assert sorted((lg["hex"], lg["kind"], lg.get("note")) for lg in legs) == [
        ("aaa111", "direct", "landing not seen by a receiver"), ("abc123", "direct", None)]


def test_a_takeoff_first_seen_in_the_climb_still_counts(airports):
    fao, stn = airports.get("FAO"), airports.get("STN")
    km = float(geo.dist_km(fao["lat"], fao["lon"], stn["lat"], stn["lon"]))
    late = _piece(fao, stn, DAY + 3600, 20 / km, 1.0, None, "STN", hexid="eee555")
    late["pts"][0][3] = 9000
    high = _piece(fao, stn, DAY + 7200, 30 / km, 1.0, None, "STN", hexid="fff666")
    high["pts"][0][3] = 36000          # an overflight that happens to pass Faro
    _store({"2026-11-01": [late, high]})
    legs = [lg for lg in route.collect("FAO", "STN", log=lambda m: None) if lg["kind"] == "direct"]
    assert [(lg["hex"], lg["note"]) for lg in legs] == [("eee555", "takeoff not seen by a receiver")]

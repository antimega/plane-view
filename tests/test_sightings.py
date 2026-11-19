"""What is seen and how it is listed: night, the unseen ends of past flights, terrain, and the timeline's tidying rules."""
import numpy as np

from planeview import predict, route
from planeview import visibility as V


def _feat(name, kind, lat, lon, **kw):
    f = dict(id=name, name=name, kind=kind, lat=lat, lon=lon, size_km=1.0, z=0.0, pop=0, fame=5)
    f.update(kw)
    f["pts"] = np.array([[lat, lon]])
    return f


def _northbound(lat0=51.3, lon=-0.30, n=40, alt_ft=15000):
    """A track flying north at 120 m/s, 15 s a step: things east of it are on the right."""
    t = np.arange(n) * 15.0
    lat = lat0 + t * 120 / 111_000
    return V.track_arrays(t, lat, np.full(n, lon), np.full(n, float(alt_ft)))


CITY = _feat("Big City", "settlement", 51.50, -0.12, pop=8_000_000, size_km=40.0)


def test_at_night_lit_landmarks_and_dark_gaps_in_a_city_are_seen():
    feats = [CITY,
             _feat("Stadium", "landmark", 51.55, -0.11, sub="STADIUM", size_km=0.3),
             _feat("Castle ruin", "landmark", 51.52, -0.10, sub="RUIN", size_km=0.3),
             _feat("City Park", "landmark", 51.51, -0.16, sub="PRK", size_km=1.5),
             _feat("Town Lake", "lake", 51.53, -0.15, size_km=1.5),
             _feat("Lonely Lake", "lake", 51.60, 0.60, size_km=1.5)]      # far outside the city's lights
    idx = V.FeatureIndex(feats)
    trk = _northbound()
    night = V.evaluate(idx, trk, np.full(len(trk["t"]), -12.0))
    seen = {feats[fi]["name"] for (fi, side) in night if side == "R"}
    assert {"Big City", "Stadium", "City Park", "Town Lake"} <= seen
    assert "Castle ruin" not in seen and "Lonely Lake" not in seen
    # by day the same things are all there for the looking
    day = {feats[fi]["name"] for (fi, side) in V.evaluate(idx, trk, np.full(len(trk["t"]), 30.0)) if side == "R"}
    assert {"Stadium", "Castle ruin", "City Park", "Town Lake"} <= day


def test_lights_come_up_through_dusk():
    feats = [CITY, _feat("Stadium", "landmark", 51.55, -0.11, sub="STADIUM", size_km=0.3)]
    idx = V.FeatureIndex(feats)
    trk = _northbound()
    q = []
    for el in (-0.5, -2.0, -4.0, -8.0):
        r = V.evaluate(idx, trk, np.full(len(trk["t"]), el))
        q.append(r.get((1, "R"), {}).get("q", 0.0))
    assert q[1] <= q[2] and q[2] > 0.2 and abs(q[2] - q[3]) < 1e-6      # full by -4 degrees, not before


def test_unseen_first_and_last_minutes_are_added_at_the_airports():
    o = dict(lat=37.0144, lon=-7.9659, elev_ft=24)
    d = dict(lat=51.885, lon=0.235, elev_ft=348)
    t = np.array([120.0, 600.0, 9000.0])            # first seen two minutes after takeoff, 13 km out
    lg = dict(kind="direct", t=t, lat=np.array([37.05, 37.6, 51.80]), lon=np.array([-8.11, -8.0, 0.10]),
              alt=np.array([4200.0, 20000.0, 3000.0]), gs=np.array([250.0, 420.0, 200.0]),
              takeoff=1000.0, landing=1000.0 + 9000 + 180)
    route._cap_ends(lg, o, d)
    assert lg["t"][0] == 0 and (lg["lat"][0], lg["lon"][0]) == (o["lat"], o["lon"]) and lg["alt"][0] == 24
    assert lg["t"][-1] == 9180 and (lg["lat"][-1], lg["lon"][-1]) == (d["lat"], d["lon"])
    assert len(lg["t"]) == 5 and len(lg["gs"]) == 5 and lg["capped_dep"] and lg["capped_arr"]
    # a flight seen from the runway keeps its own points; so does a spliced or great-circle leg
    full = dict(kind="direct", t=np.array([0.0, 600.0]), lat=np.array([37.0145, 37.6]), lon=np.array([-7.966, -8.0]),
                alt=np.array([30.0, 20000.0]), gs=np.array([150.0, 420.0]), takeoff=1000.0, landing=None)
    route._cap_ends(full, o, d)
    assert len(full["t"]) == 2
    spliced = dict(lg, kind="spliced", t=t.copy())
    route._cap_ends(spliced, o, d)
    assert len(spliced["t"]) == 3


def _ev(name, kind, t, score, lat=51.0, lon=0.0, **kw):
    e = dict(id=name, name=name, kind=kind, what=kind, t=t, t_lo=t - 1, t_hi=t + 1, score=score, alt_ft=30000,
             fa=20, dep=20, d_km=20.0, lat=lat, lon=lon, salience=2.0, fame=5, p_see=0.9, broad=False, pop=None,
             area_km2=None)
    e.update(kw)
    return e


def test_a_town_headlines_over_its_own_landmark():
    evs = [_ev("Town Stadium", "landmark", 10.0, 5.0, lat=51.11, lon=-0.19),
           _ev("Townsville", "settlement", 10.2, 3.0, lat=51.12, lon=-0.18, pop=110_000),
           _ev("Far Pier", "landmark", 31.0, 4.0, lat=50.82, lon=-0.14)]
    tl = predict.select(evs)
    heads = {h["name"]: [a["name"] for a in h["also"]] for h in tl}
    assert heads["Townsville"][0] == "Town Stadium" and "Town Stadium" not in heads
    assert "Far Pier" in heads                       # no town of its own in view: it stays a headline


def test_a_slight_far_feature_cannot_headline():
    evs = [_ev("Nameless Eyot", "island", 10.0, 5.0, fame=0, d_km=40.0),
           _ev("Real Island", "island", 30.0, 4.0, fame=0, d_km=40.0, area_km2=12.0),
           _ev("Close Eyot", "island", 50.0, 4.0, fame=0, d_km=4.0)]
    names = {h["name"] for h in predict.select(evs)}
    assert names == {"Real Island", "Close Eyot"}


class _Ridge(V.Terrain):
    """A valley floor at 600 m with a 3,000 m ridge running north-south just east of the track."""

    def __init__(self):
        super().__init__(allow_fetch=False)

    def sample(self, lat, lon):
        lon = np.asarray(lon, float)
        return np.where((lon >= -0.20) & (lon <= -0.17), 3000.0, 600.0).astype(np.float32)


def test_in_a_valley_peaks_are_looked_up_at_and_the_next_valley_is_hidden():
    feats = [_feat("Ridge Peak", "peak", 51.50, -0.199, z=3000.0, elev=3000, size_km=1.5),
             _feat("Valley Town", "settlement", 51.50, -0.24, pop=20_000, size_km=3.0),
             _feat("Hidden Town", "settlement", 51.50, -0.05, pop=20_000, size_km=3.0),
             _feat("Next Valley", "region", 51.50, -0.05, size_km=10.0)]
    trk = _northbound(alt_ft=6500)                    # about 2,000 m: below the ridge
    day = np.full(len(trk["t"]), 30.0)
    flat = {feats[fi]["name"] for (fi, sd) in V.evaluate(V.FeatureIndex(feats), trk, day) if sd == "R"}
    real = {feats[fi]["name"] for (fi, sd) in V.evaluate(V.FeatureIndex(feats), trk, day, terrain=_Ridge()) if sd == "R"}
    assert "Hidden Town" in flat and "Ridge Peak" not in flat          # sea-level Earth: wrong on both counts
    assert {"Ridge Peak", "Valley Town"} <= real and "Hidden Town" not in real
    assert "Next Valley" in real                      # an area name is not tested at its one listed point
    # from cruise height the ridge hides nothing and terrain is not consulted
    high = _northbound(alt_ft=36000)
    up = {feats[fi]["name"] for (fi, sd) in V.evaluate(V.FeatureIndex(feats), high, np.full(len(high["t"]), 30.0),
                                                       terrain=_Ridge()) if sd == "R"}
    assert "Hidden Town" in up


def test_companion_lists_are_not_all_one_kind():
    evs = [_ev("City", "settlement", 10.0, 9.0, pop=1_000_000)]
    evs += [_ev(f"Stadium {n}", "landmark", 10.0, 8.0 - n / 10) for n in range(10)]
    evs += [_ev(f"Suburb {n}", "settlement", 10.1, 3.0 - n / 10, pop=30_000) for n in range(12)]
    (head,) = [h for h in predict.select(evs) if h["name"] == "City"]
    kinds = [a["kind"] for a in head["also"]]
    assert kinds.count("landmark") == predict.SELECT["also_kind_max"] == kinds.count("settlement")

import gzip
import json
import random

import orjson
import pytest

from planeview.sources import library as L

from conftest import make_leg


def test_pack_round_trip_edge_cases():
    pts = [[0, 51.1234, -0.5, None, None], [7, 51.2, -0.6001, -125, 300], [4000, -33.9, 151.2, 43000, None],
           [4001, 89.9999, -179.9999, 0, 0], [90000, -89.9999, 179.9999, 60000, 700]]
    assert L.unpack(L.pack(pts)) == pts
    assert L.unpack(L.pack([])) == []


def test_pack_round_trip_random_tracks():
    rnd = random.Random(1)
    for _ in range(200):
        t, pts = 0, []
        for _ in range(rnd.randint(1, 80)):
            t += rnd.randint(0, 3000)
            pts.append([t, round(rnd.uniform(-90, 90), 4), round(rnd.uniform(-180, 180), 4),
                        rnd.choice([None, rnd.randrange(-1000, 45000, 25)]), rnd.choice([None, rnd.randint(0, 600)])])
        assert L.unpack(L.pack(pts)) == pts


def test_packed_track_is_small():
    pts = [[k * 30, round(51 + k * 0.03, 4), round(k * 0.05, 4), 30000 + 25 * (k % 3), 440 + k % 5] for k in range(60)]
    assert len(L.pack(pts)) < 10 * len(pts)          # about 8 bytes a point
    assert len(L.pack(pts)) < len(orjson.dumps(pts)) / 3


def test_corrupt_blob_is_refused():
    with pytest.raises(ValueError):
        L.unpack(L.pack([[0, 1.0, 2.0, 3, 4]])[:-1] + b"\x80")


def test_store_query_and_order(tmp_cache):
    con = L.connect()
    L.add_day(con, "2026-11-02", [make_leg("LGW", "ATH", t0=200.0), make_leg("LGW", None, t0=100.0)], dict(release="x"))
    L.add_day(con, "2026-11-01", [make_leg("LGW", "ATH", cs="TST2", t0=50.0), make_leg(None, None, hexid="ffffff")])
    assert L.days(con) == ["2026-11-01", "2026-11-02"]
    got = L.query("fr = ?", ("LGW",), con=con)
    assert [(g["day"], g["t0"]) for g in got] == [("2026-11-01", 50.0), ("2026-11-02", 100.0), ("2026-11-02", 200.0)]
    assert got[0] == dict(make_leg("LGW", "ATH", cs="TST2", t0=50.0), day="2026-11-01")
    assert len(L.query("fr = ?", ("LGW",), only_days=["2026-11-02"], con=con)) == 2
    assert [g["hex"] for g in L.query("fr IS NULL AND too IS NULL AND hex IN (?)", ("ffffff",), con=con)] == ["ffffff"]
    assert L.day_stats(con)[1]["release"] == "x" and L.day_stats(con)[1]["legs"] == 2
    assert L.pairs(con=con) == [("LGW", "ATH", 2)]
    assert L.callsign_routes("TST1", con=con) == [("LGW", "ATH", 1, "2026-11-02")]


def test_queries_use_the_partial_indexes(tmp_cache):
    con = L.connect()
    plan = lambda sql: " ".join(r[3] for r in con.execute("EXPLAIN QUERY PLAN " + sql))
    assert "i_fr" in plan("SELECT 1 FROM legs WHERE fr = 'LGW' AND too IS NULL")
    assert "i_to" in plan("SELECT 1 FROM legs WHERE too = 'ATH' AND fr IS NULL")
    assert "i_mid" in plan("SELECT 1 FROM legs WHERE fr IS NULL AND too IS NULL AND hex IN ('a')")
    assert "i_cs" in plan("SELECT fr, too FROM legs WHERE cs = 'X' AND fr IS NOT NULL AND too IS NOT NULL")


def test_a_cut_download_stores_nothing(tmp_cache):
    con = L.connect()
    L.add_day(con, "2026-11-01", [make_leg("LGW", "ATH")])

    def cut():
        yield make_leg("LGW", "OPO")
        raise RuntimeError("stream cut short")
    with pytest.raises(RuntimeError):
        L.add_day(con, "2026-11-02", cut())
    assert L.days(con) == ["2026-11-01"]
    assert L.pairs(con=con) == [("LGW", "ATH", 1)]


def test_adding_a_day_twice_replaces_it(tmp_cache):
    con = L.connect()
    L.add_day(con, "2026-11-01", [make_leg("LGW", "ATH"), make_leg("LGW", "OPO")])
    L.add_day(con, "2026-11-01", [make_leg("LGW", "ATH")])
    assert L.size()["legs"] == 1


def test_migration_from_day_files(tmp_cache):
    lib = tmp_cache / "library"
    lib.mkdir()
    legs = [make_leg("LGW", "ATH", t0=10.0),
            make_leg(None, "PHX", t0=20.0, pts=[[0, 40.0, -100.0, None, None], [900, 39.0, -105.0, 38000, 470]])]
    with gzip.open(lib / "2026-11-01.jsonl.gz", "wb") as fh:
        for lg in legs:
            fh.write(orjson.dumps(lg) + b"\n")
    (lib / "2026-11-01.json").write_text(json.dumps(dict(day="2026-11-01", release="r1", legs=2, watch=["A-B"])))
    (lib / "library.sqlite").write_bytes(b"old index")
    con = L.connect()                       # adopts the old files
    assert L.days(con) == ["2026-11-01"]
    assert L.query("1", con=con) == [dict(lg, day="2026-11-01") for lg in legs]
    assert not (lib / "2026-11-01.jsonl.gz").exists() and not (lib / "library.sqlite").exists()
    assert L.day_stats(con)[0]["release"] == "r1" and "watch" not in L.day_stats(con)[0]

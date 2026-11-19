"""Lookups and downloads with the network replaced: what happens when a source is slow, down or wrong."""
import json
import subprocess
import time

import pytest

from planeview import cache
from planeview.sources import flights, library, wikidata

from conftest import make_leg


class FakeCurl:
    """Stands in for subprocess.run(["curl", ...]): answers from a list of (returncode, body, http code)."""

    def __init__(self, answers):
        self.answers, self.calls = list(answers), 0

    def __call__(self, cmd, **kw):
        self.calls += 1
        rc, body, code = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return subprocess.CompletedProcess(cmd, rc, stdout=f"{body}\n{code}", stderr="timed out" if rc else "")


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)


def test_get_json_retries_then_succeeds(tmp_cache, monkeypatch, no_sleep):
    fake = FakeCurl([(28, "", "000"), (0, "busy", "503"), (0, '{"ok": 1}', "200")])
    monkeypatch.setattr(subprocess, "run", fake)
    assert cache.get_json("https://example.invalid/x", "t/x.json") == {"ok": 1}
    assert fake.calls == 3 and json.loads((tmp_cache / "t" / "x.json").read_text()) == {"ok": 1}


def test_get_json_serves_a_stale_copy_when_the_source_is_down(tmp_cache, monkeypatch, no_sleep):
    p = tmp_cache / "t" / "x.json"
    p.parent.mkdir()
    p.write_text('{"old": true}')
    monkeypatch.setattr(subprocess, "run", FakeCurl([(28, "", "000")]))
    assert cache.get_json("https://example.invalid/x", "t/x.json", max_age_days=0) == {"old": True}
    p.unlink()
    with pytest.raises(RuntimeError):
        cache.get_json("https://example.invalid/x", "t/x.json", max_age_days=0)


def test_get_json_passes_on_a_json_error_body_without_caching_it(tmp_cache, monkeypatch, no_sleep):
    monkeypatch.setattr(subprocess, "run", FakeCurl([(0, '{"response": "unknown callsign"}', "404")]))
    assert cache.get_json("https://example.invalid/x", "t/x.json") == {"response": "unknown callsign"}
    assert not (tmp_cache / "t" / "x.json").exists()


RYR = dict(airline=dict(name="Ryanair", icao="RYR", iata="FR"), callsign_icao="RYR4321",
           origin=dict(iata_code="FAO", icao_code="LPFR"), destination=dict(iata_code="STN", icao_code="EGSS"))


def test_flight_airports_come_from_the_library_first(tmp_cache, monkeypatch):
    con = library.connect()
    library.add_day(con, "2026-11-01", [make_leg("LTN", "FAO", cs="RYR1234"), make_leg("FAO", "STN", cs="RYR4321")])
    library.add_day(con, "2026-11-08", [make_leg("LTN", "FAO", cs="RYR1234"), make_leg("LTN", "AGP", cs="RYR1234", t0=5.0)])
    library.add_day(con, "2026-11-15", [make_leg("LTN", "FAO", cs="RYR1234")])
    con.close()
    said = []
    monkeypatch.setattr(flights, "iata_to_icao", lambda c: {"FR": "RYR"}.get(c))
    monkeypatch.setattr(flights, "_lookup", lambda cs: None)            # adsbdb is down
    monkeypatch.setattr(flights, "_standing", lambda: {"RYR1234": ["EDLV", "LCLK"], "RYR4321": ["LPFR", "EGSS"],
                                                       "RYR1": ["A", "B", "C"]})
    got = flights.resolve("FR1234", None, None, no_aeroapi=True, log=said.append)
    assert (got["orig"], got["dest"], got["callsign"], got["airline_icao"]) == ("LTN", "FAO", "RYR1234", "RYR")
    # flown once only: not enough to trust the library, so with adsbdb down the standing data answers
    got = flights.resolve("FR4321", None, None, no_aeroapi=True, log=said.append)
    assert (got["orig"], got["dest"]) == ("LPFR", "EGSS")
    with pytest.raises(ValueError):
        flights.resolve("FR1", None, None, no_aeroapi=True, log=said.append)       # multi-stop: ask the user
    got = flights.resolve("FR1", "stn", "dub", no_aeroapi=True, log=said.append)   # given airports always win
    assert (got["orig"], got["dest"]) == ("STN", "DUB")


def test_adsbdb_outranks_the_standing_data_and_a_disagreement_is_reported(tmp_cache, monkeypatch):
    library.connect().close()
    said = []
    monkeypatch.setattr(flights, "iata_to_icao", lambda c: {"FR": "RYR"}.get(c))
    monkeypatch.setattr(flights, "_lookup", lambda cs: RYR if cs == "FR4321" else None)
    monkeypatch.setattr(flights, "_standing", lambda: {"RYR4321": ["EDLV", "LCLK"]})
    got = flights.resolve("FR4321", None, None, no_aeroapi=True, log=said.append)
    assert (got["orig"], got["dest"], got["airline"]) == ("FAO", "STN", "Ryanair")
    assert any("sources disagree" in m for m in said)


def test_a_reused_airline_code_goes_to_the_airline_seen_flying(tmp_cache, monkeypatch):
    (tmp_cache / "openflights").mkdir()
    (tmp_cache / "openflights" / "airlines.dat").write_text(
        '1,"Formosa Airlines",\\N,"VY","FOS","FORMOSA","Taiwan","Y"\n'
        '2,"Vueling Airlines",\\N,"VY","VLG","VUELING","Spain","Y"\n'
        '3,"Old One",\\N,"ZZ","OLD","OLD","Nowhere","N"\n4,"New One",\\N,"ZZ","NEW","NEW","Nowhere","Y"\n')
    library.connect().close()
    assert flights.iata_to_icao("VY") == "FOS"           # no evidence: the first active entry, as before
    assert flights.iata_to_icao("ZZ") == "NEW"           # active beats inactive
    con = library.connect()
    library.add_day(con, "2026-11-01", [make_leg("SCQ", "LGW", cs="VLG1AB"), make_leg("LGW", "SCQ", cs="VLG1001")])
    con.close()
    assert flights.iata_to_icao("VY") == "VLG"           # Vueling is the one in the sky
    assert flights.iata_to_icao("GQ") == "SEH"           # the hand-corrected code still wins
    assert flights.iata_to_icao("??") is None


def test_a_wrong_airline_in_adsbdb_does_not_override_the_corrected_code(tmp_cache, monkeypatch):
    library.connect().close()
    monkeypatch.setattr(flights, "_standing", lambda: {})
    big_sky = dict(airline=dict(name="Big Sky Airlines", icao="BSY", iata="GQ"), callsign_icao="BSY101",
                   origin=dict(iata_code="BIL", icao_code="KBIL"), destination=dict(iata_code="GTF", icao_code="KGTF"))
    monkeypatch.setattr(flights, "_lookup", lambda cs: big_sky if cs == "GQ101" else None)
    got = flights.resolve("GQ101", "LGW", "ATH", no_aeroapi=True, log=lambda m: None)
    assert got["airline_icao"] == "SEH" and got["callsign"] == "SEH101"
    with pytest.raises(ValueError):                      # and its airports are not borrowed from Big Sky either
        flights.resolve("GQ101", None, None, no_aeroapi=True, log=lambda m: None)


def test_wikidata_cells_cover_the_corridor_once():
    cells = wikidata.cells_for([50.95, 50.95, 51.6], [-0.05, 0.05, 0.5], reach_km=40)
    assert len(cells) == len(set(cells))
    assert {(50, -1), (50, 0), (51, -1), (51, 0)} <= set(cells)
    assert (53, 0) not in cells
    assert wikidata.cells_for([0.0], [179.9], reach_km=40)[0][1] in (179, -180)


def test_wikidata_landmarks_offline_use_only_cached_cells(tmp_cache, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("offline must not query")
    monkeypatch.setattr(wikidata, "_sparql", boom)
    p = wikidata._cell_path(50, -1)
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps(dict(fetched="2026-11-17", items=[
        dict(q="Q1", name="Brighton Palace Pier", lat=50.8168, lon=-0.1367, k=1, links=30),
        dict(q="Q2", name="Arlington Reservoir", lat=50.85, lon=-0.18, k=4, links=4),
        dict(q="Q3", name="A Minor Castle", lat=50.86, lon=-0.2, k=9, links=4)])))      # under 2 x 3 articles: left out
    got = wikidata.landmarks([50.8], [-0.1], offline=True, log=lambda m: None)
    assert [(f["name"], f["kind"], f["what"], f["fame"], f["src"]) for f in got] == [
        ("Brighton Palace Pier", "landmark", "pier", 30, "Wikidata"), ("Arlington Reservoir", "lake", "reservoir", 4, "Wikidata")]
    assert wikidata.KINDS[9][1] == "castle"


def test_wikidata_cell_parsing_picks_the_most_specific_kind(tmp_cache, monkeypatch):
    E = "http://www.wikidata.org/entity/"
    rows = [dict(item=E + "Q10", loc="Point(-1.5917 50.6622)", c=E + "Qtower", links="12"),
            dict(item=E + "Q10", loc="Point(-1.5917 50.6622)", c=E + "Qlight", links="12"),
            dict(item=E + "Q11", loc="Point(-1.5 50.6)", c=E + "Qlight", links="2"),          # has no usable label
            dict(item=E + "Q12", loc="Point(-1.4 50.6)", c=E + "Qtower", links="2"),          # too obscure a tower
            dict(item=E + "Q13", loc="Point(-1.4 50.7)", c=E + "Qvillage", links="40")]       # not a landmark kind
    names = [dict(item=E + "Q10", itemLabel="Needles Lighthouse"), dict(item=E + "Q11", itemLabel="Q11")]
    asked = []

    def fake(q, timeout=60):
        asked.append(q)
        return names if "VALUES ?item" in q else rows
    monkeypatch.setattr(wikidata, "_sparql", fake)
    tower = next(i for i, k in enumerate(wikidata.KINDS) if k[1] == "tower")
    items = wikidata._fetch_cell(50, -2, {"Qlight": 0, "Qtower": tower})
    assert [(i["name"], wikidata.KINDS[i["k"]][1]) for i in items] == [("Needles Lighthouse", "lighthouse")]
    assert len(asked) == 2 and "wd:Q10 wd:Q11" in asked[1] and "Q12" not in asked[1]
    assert json.loads(wikidata._cell_path(50, -2).read_text())["items"] == items


def test_a_wikidata_answer_cut_short_counts_as_no_answer(monkeypatch, no_sleep):
    ok = "item,loc\r\nhttp://www.wikidata.org/entity/Q1,Point(1 2)\r\n"
    cut = ok + "java.util.concurrent.TimeoutException\r\n"
    monkeypatch.setattr(subprocess, "run", FakeCurl([(0, cut, "200"), (0, ok, "200")]))
    assert wikidata._sparql("SELECT") == [dict(item="http://www.wikidata.org/entity/Q1", loc="Point(1 2)")]
    monkeypatch.setattr(subprocess, "run", FakeCurl([(0, cut, "200")]))
    assert wikidata._sparql("SELECT") is None

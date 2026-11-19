"""Tests run against a throwaway cache folder: nothing here touches the real library or the network.

Two reference files are borrowed from the real cache when present (OurAirports' airports and runways, public domain,
17 MB): tests that need them are skipped on a machine that has never downloaded them.
"""
import shutil
from pathlib import Path

import pytest

import os

# where the real downloads live (before any test swaps the cache folder for a throwaway one)
REAL_CACHE = Path(os.environ.get("PLANEVIEW_CACHE") or Path(os.environ.get("PLANEVIEW_HOME") or Path(__file__).resolve().parent.parent) / "cache").expanduser()


@pytest.fixture
def tmp_cache(tmp_path, monkeypatch):
    from planeview import cache
    from planeview.sources import library
    monkeypatch.setattr(cache, "CACHE", tmp_path)
    monkeypatch.setattr(library, "LIB", tmp_path / "library")
    monkeypatch.setattr(library, "DB", tmp_path / "library" / "legs.sqlite")
    return tmp_path


@pytest.fixture
def airports(tmp_cache):
    """The real airport tables in the throwaway cache (skips when they were never downloaded)."""
    src = REAL_CACHE / "ourairports"
    if not (src / "airports.csv").exists() or not (src / "runways.csv").exists():
        pytest.skip("OurAirports tables not downloaded yet")
    (tmp_cache / "ourairports").mkdir()
    for f in ("airports.csv", "runways.csv"):
        shutil.copy(src / f, tmp_cache / "ourairports" / f)
    from planeview.sources import airports as AP
    AP.airports.cache_clear(); AP._runways.cache_clear()
    yield AP.airports()
    AP.airports.cache_clear(); AP._runways.cache_clear()


def make_leg(fr, to, cs="TST1", hexid="abc123", t0=1_700_000_000.0, pts=None, **kw):
    pts = pts or [[0, 51.0, 0.0, 1000, 150], [600, 51.5, 0.5, 30000, 440], [1200, 52.0, 1.0, 2000, 160]]
    return dict(dict(hex=hexid, reg="G-TEST", type="A320", cs=cs, fr=fr, to=to, fr_km=1.0 if fr else None,
                     to_km=1.0 if to else None, t0=t0, t1=t0 + pts[-1][0], s_alt=pts[0][3], e_alt=pts[-1][3],
                     cont_prev=False, cont_next=False, max_alt=max(p[3] or 0 for p in pts), gap_max=600, pts=pts), **kw)

"""Airports, runways and navaids from OurAirports (public domain, davidmegginson.github.io/ourairports-data),
plus a time zone for any point from the nearest GeoNames town (cities1000, CC BY 4.0)."""
from __future__ import annotations

import csv
import io
import zipfile
from functools import lru_cache

import numpy as np
from scipy.spatial import cKDTree

from .. import cache

OA = "https://davidmegginson.github.io/ourairports-data/"
R_KM = 6371.0088


def _csv(name: str):
    p = cache.fetch(f"ourairports/{name}", OA + name)
    with open(p, newline="", encoding="utf-8") as fh:
        yield from csv.DictReader(fh)


def unit(lat, lon):
    la, lo = np.radians(lat), np.radians(lon)
    return np.stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)], axis=-1)


class Airports:
    """Large and medium airports with an IATA code: the ones airline flights use."""

    def __init__(self):
        rows = []
        for r in _csv("airports.csv"):
            if r["type"] not in ("large_airport", "medium_airport"):
                continue
            if not r["iata_code"] and r["type"] == "medium_airport":
                continue
            try:
                lat, lon = float(r["latitude_deg"]), float(r["longitude_deg"])
            except ValueError:
                continue
            rows.append(dict(icao=r["ident"] if len(r["ident"]) == 4 else (r["gps_code"] or r["ident"]),
                             iata=r["iata_code"], name=r["name"], city=r["municipality"], cc=r["iso_country"],
                             lat=lat, lon=lon, elev_ft=float(r["elevation_ft"] or 0), type=r["type"],
                             scheduled=r["scheduled_service"] == "yes"))
        self.rows = rows
        self.lat = np.array([r["lat"] for r in rows])
        self.lon = np.array([r["lon"] for r in rows])
        self.tree = cKDTree(unit(self.lat, self.lon))
        self.by = {}
        for i, r in enumerate(rows):
            for k in (r["iata"], r["icao"]):
                if k and (k not in self.by or rows[self.by[k]]["type"] != "large_airport"):
                    self.by[k] = i

    def get(self, code: str) -> dict:
        i = self.by.get(code.upper())
        if i is None:
            raise KeyError(f"unknown airport {code}")
        return self.rows[i]

    def code(self, i: int) -> str:
        r = self.rows[i]
        return r["iata"] or r["icao"]

    def nearest(self, lat, lon, max_km=10.0):
        """Index of the nearest airport within max_km for each point (-1 if none)."""
        d, i = self.tree.query(unit(np.asarray(lat, float), np.asarray(lon, float)))
        km = 2 * R_KM * np.arcsin(np.clip(d / 2, 0, 1))
        return np.where(km <= max_km, i, -1), km


def display_city(a: dict) -> str:
    """'Athens' for ATH (municipality Spata-Artemida), 'London Gatwick', 'Tokyo Haneda', 'Faro'."""
    import re
    city = (a.get("city") or "").split(",")[0].strip()
    words = re.sub(r"\b(International|Airport|Intl)\b", " ", a["name"]).replace(" - ", " ").split()
    if not words:
        return city or a.get("iata") or a["icao"]
    first = words[0]
    if first != city and first[0].isupper():
        from .. import ne
        from .. import geo
        for rec, _ in ne.layer("ne_10m_populated_places_simple", geometry=False):
            if rec["name"] == first and rec["pop_max"] >= 100000 and \
                    geo.dist_km(rec["latitude"], rec["longitude"], a["lat"], a["lon"]) < 60:
                city = first
                break
    if len(words) == 2 and words[0] == city:
        return f"{city} {words[1]}"
    return city or first


@lru_cache(maxsize=1)
def airports() -> Airports:
    return Airports()


@lru_cache(maxsize=1)
def _runways():
    out = {}
    for r in _csv("runways.csv"):
        if r["closed"] == "1":
            continue
        ends = []
        for e, o in (("le", "he"), ("he", "le")):
            try:
                lat, lon = float(r[f"{e}_latitude_deg"]), float(r[f"{e}_longitude_deg"])
            except ValueError:
                lat = lon = None
            hdg = r[f"{e}_heading_degT"]
            ends.append(dict(ident=r[f"{e}_ident"], lat=lat, lon=lon, hdg=float(hdg) if hdg else None,
                             length_ft=float(r["length_ft"] or 0)))
        # fill a missing true heading from the designator (magnetic, close enough to cluster on)
        for e in ends:
            if e["hdg"] is None and e["ident"][:2].isdigit():
                e["hdg"] = int(e["ident"][:2]) * 10.0
        out.setdefault(r["airport_ident"], []).extend(ends)
    return out


def runways(icao: str) -> list[dict]:
    """Runway ends of an airport (ident, threshold lat/lon, true heading), longest runways first."""
    rw = [e for e in _runways().get(icao, []) if e["hdg"] is not None]
    return sorted(rw, key=lambda e: -e["length_ft"])


@lru_cache(maxsize=1)
def navaids() -> dict:
    """VOR/NDB/DME identifiers -> list of (lat, lon, type). Identifiers repeat worldwide; callers pick the nearest."""
    out = {}
    for r in _csv("navaids.csv"):
        try:
            out.setdefault(r["ident"], []).append((float(r["latitude_deg"]), float(r["longitude_deg"]), r["type"]))
        except ValueError:
            pass
    return out


@lru_cache(maxsize=1)
def _towns():
    p = cache.fetch("geonames/cities1000.zip", "https://download.geonames.org/export/dump/cities1000.zip")
    lat, lon, tz = [], [], []
    with zipfile.ZipFile(p).open("cities1000.txt") as fh:
        for row in csv.reader(io.TextIOWrapper(fh, "utf-8"), delimiter="\t", quoting=csv.QUOTE_NONE):
            lat.append(float(row[4])); lon.append(float(row[5])); tz.append(row[17])
    return cKDTree(unit(np.array(lat), np.array(lon))), tz


def timezone(lat: float, lon: float) -> str:
    """IANA zone of the nearest town of 1,000+ people (good for airports; at sea it is only indicative)."""
    tree, tz = _towns()
    _, i = tree.query(unit(np.array(lat), np.array(lon)))
    return tz[int(i)] or "UTC"


def gc_km(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp, dl = p2 - p1, np.radians(np.asarray(lon2) - np.asarray(lon1))
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * R_KM * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def bearing(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dl = np.radians(np.asarray(lon2) - np.asarray(lon1))
    y = np.sin(dl) * np.cos(p2)
    x = np.cos(p1) * np.sin(p2) - np.sin(p1) * np.cos(p2) * np.cos(dl)
    return (np.degrees(np.arctan2(y, x)) + 360) % 360


if __name__ == "__main__":
    A = airports()
    for c in ("LGW", "LTN", "FAO", "STN", "LHR", "PHX", "HND", "SCQ", "OPO", "ATH"):
        a = A.get(c)
        print(c, a["icao"], a["name"], round(a["lat"], 3), round(a["lon"], 3), timezone(a["lat"], a["lon"]),
              [e["ident"] for e in runways(a["icao"])])
    print(len(A.rows), "airports")

"""Landmarks along the whole route from Wikidata (CC0, query.wikidata.org).

The things people photograph and name from a window that the other sources lack - piers, lighthouses, castles, bridges, towers, stadiums,
dams, wind farms, power stations, reservoirs, lagoons and estuaries - with
the number of Wikipedia editions that have an article, which is the same fame measure the gazetteer already uses.

Why Wikidata and not OpenStreetMap's Overpass: the public Overpass servers refuse or time out from many networks and
turn away all but tiny queries, so landmarks could only be fetched near airports, by hand, when a mirror was quiet.
Wikidata answers a one-degree cell in two or three seconds, anywhere on the route.

How: one query per 1 x 1 degree cell asks for everything with coordinates, a Wikipedia article and an "instance of";
the classes are matched here against lists of each kind's subclasses (fetched once), and a second small query names
the few that matched. Asking the query service to walk the subclass tree, or to label every row, inside the cell
query takes 15 s or times out; this way a cell takes about 3 s. Cells are cached in cache/wikidata/cells/ for
REFRESH_DAYS; a cell that fails is simply tried again next time.
"""
from __future__ import annotations

import csv
import io
import json
import re
import subprocess
import time

from .. import cache

ENDPOINT = "https://query.wikidata.org/sparql"
REFRESH_DAYS = 180
MAX_NEW_CELLS = 120      # per run: a first long-haul prediction fills its cells over a few runs, nearest airports first
PAUSE_S = 0.6            # between queries: the service allows about a minute of query time a minute for one client
FAME_FACTOR = 2          # a landmark is used when it has this many times its kind's minimum of Wikipedia articles.
# Cells keep everything from the minimum up, so this can be changed without fetching again. Set against six
# flights' window photographs: with every cached landmark, minor castles and bridges crowd towns out of the
# timeline (prominent photographed places listed: 70 of 142); at 2x, 76 of 142 and more of all named places (158 of
# 337 against 135); without Wikidata, 68. Six flights is a small sample: treat the factor as a first setting.

# (Wikidata class, what to call it, kind, size km, fewest Wikipedia articles to count). First match wins, so the
# specific kinds come before "tower" and "fortification", which have very wide subclass trees.
KINDS = [
    ("Q39715", "lighthouse", "landmark", 0.08, 1),
    ("Q863454", "pier", "landmark", 0.5, 1),
    ("Q194356", "wind farm", "landmark", 5.0, 1),
    ("Q12323", "dam", "landmark", 0.4, 2),
    ("Q131681", "reservoir", "lake", 1.5, 2),
    ("Q187223", "lagoon", "lake", 2.0, 2),
    ("Q47053", "estuary", "sea", 2.0, 2),
    ("Q483110", "stadium", "landmark", 0.3, 3),
    ("Q12280", "bridge", "landmark", 0.6, 3),
    ("Q23413", "castle", "landmark", 0.25, 3),
    ("Q159719", "power station", "landmark", 1.0, 3),
    ("Q44782", "port", "landmark", 1.0, 4),
    ("Q12518", "tower", "landmark", 0.1, 5),
    ("Q57821", "fort", "landmark", 0.25, 5),
]
_CELL_Q = """SELECT ?item ?loc ?c ?links WHERE {{
  SERVICE wikibase:box {{ ?item wdt:P625 ?loc .
    bd:serviceParam wikibase:cornerSouthWest "Point({w} {s})"^^geo:wktLiteral ;
                    wikibase:cornerNorthEast "Point({e} {n})"^^geo:wktLiteral . }}
  ?item wdt:P31 ?c ; wikibase:sitelinks ?links .
  FILTER(?links >= 1)
}}"""
_LABEL_Q = """SELECT ?item ?itemLabel WHERE {{ VALUES ?item {{ {items} }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,mul,fr,de,es,it,pt,nl" . }} }}"""
_CLASS_Q = "SELECT ?root ?c WHERE {{ VALUES ?root {{ {roots} }} ?c wdt:P279* ?root }}"
_POINT = re.compile(r"Point\(([-\d.eE]+) ([-\d.eE]+)\)")


def _sparql(query: str, timeout: int = 60) -> list[dict] | None:
    """Rows of a SELECT as dicts, or None when the service did not answer (timeouts, 429 and 5xx are tried twice)."""
    for attempt in range(2):
        r = subprocess.run(["curl", "-sS", "--compressed", "-A", cache.UA, "--connect-timeout", "20",
                            "--max-time", str(timeout), "-H", "Accept: text/csv", "--data-urlencode",
                            "query=" + query, "-w", "\n%{http_code}", ENDPOINT], capture_output=True, text=True)
        body, _, code = r.stdout.rpartition("\n")
        if r.returncode == 0 and code.strip() == "200":
            rows = list(csv.DictReader(io.StringIO(body)))
            # the service streams its answer: a query that runs out of time still says 200, then ends with a Java
            # error where a row should be. A short row means the answer is cut, so it counts as no answer.
            if all(None not in row.values() and None not in row for row in rows):
                return rows
        time.sleep(5 + 10 * attempt)
    return None


def _qid(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


def classes() -> dict[str, int]:
    """Wikidata class -> index into KINDS (the first kind whose subclass tree holds it)."""
    p = cache.CACHE / "wikidata" / "classes.json"
    want = [k[0] for k in KINDS]
    if p.exists():
        d = json.loads(p.read_text())
        if d.get("roots") == want and (time.time() - p.stat().st_mtime) / 86400 < REFRESH_DAYS:
            return d["classes"]
    rows = _sparql(_CLASS_Q.format(roots=" ".join("wd:" + q for q in want)), timeout=120)
    if rows is None:
        if p.exists():
            return json.loads(p.read_text())["classes"]   # stale lists still work
        raise RuntimeError("Wikidata did not answer the class query")
    out: dict[str, int] = {}
    for r in rows:
        k = want.index(_qid(r["root"]))
        c = _qid(r["c"])
        if k < out.get(c, 99):
            out[c] = k
    cache.write_atomic(p, json.dumps(dict(roots=want, classes=out)))
    return out


def _cell_path(lat0: int, lon0: int):
    return cache.CACHE / "wikidata" / "cells" / f"{'N' if lat0 >= 0 else 'S'}{abs(lat0):02d}" \
                                                f"{'E' if lon0 >= 0 else 'W'}{abs(lon0):03d}.json"


def _fetch_cell(lat0: int, lon0: int, cls: dict[str, int]) -> list[dict] | None:
    rows = _sparql(_CELL_Q.format(w=lon0, s=lat0, e=lon0 + 1, n=lat0 + 1))
    if rows is None:
        return None
    best: dict[str, dict] = {}
    for r in rows:
        k = cls.get(_qid(r["c"]))
        m = _POINT.match(r["loc"] or "")
        if k is None or not m:
            continue
        links = int(float(r["links"] or 0))
        if links < KINDS[k][4]:
            continue
        q = _qid(r["item"])
        if q not in best or k < best[q]["k"]:
            best[q] = dict(q=q, lat=round(float(m.group(2)), 5), lon=round(float(m.group(1)), 5), k=k, links=links)
    qs = sorted(best)
    for i in range(0, len(qs), 250):
        named = _sparql(_LABEL_Q.format(items=" ".join("wd:" + q for q in qs[i:i + 250])))
        if named is None:
            return None
        for r in named:
            name = (r.get("itemLabel") or "").strip()
            if name and not re.fullmatch(r"Q\d+", name):     # no label in any language we can show
                best[_qid(r["item"])]["name"] = name
    items = [dict(q=q, name=best[q]["name"], lat=best[q]["lat"], lon=best[q]["lon"], k=best[q]["k"],
                  links=best[q]["links"]) for q in qs if best[q].get("name")]
    cache.write_atomic(_cell_path(lat0, lon0), json.dumps(dict(fetched=time.strftime("%Y-%m-%d"), items=items)))
    return items


def cells_for(lat, lon, reach_km: float = 40.0) -> list[tuple[int, int]]:
    """1-degree cells within reach_km of any of the points, in the order the points first touch them. Every point
    counts (there may be hundreds of thousands): thinning them would step over cells."""
    import numpy as np
    lat, lon = np.asarray(lat, float), np.asarray(lon, float)
    if not len(lat):
        return []
    dla = reach_km / 111.0
    dlo = dla / np.maximum(0.2, np.cos(np.radians(lat)))
    keys, order = [], []
    n = len(lat)
    for j, (a, b) in enumerate((a, b) for a in (-1, 0, 1) for b in (-1, 0, 1)):
        la0 = np.floor(lat + a * dla).astype(np.int64)
        lo0 = np.floor((lon + b * dlo + 180) % 360 - 180).astype(np.int64)
        ok = (la0 >= -90) & (la0 < 90)
        keys.append((la0[ok] + 90) * 360 + (lo0[ok] + 180))
        order.append(np.flatnonzero(ok) * 9 + j)          # point order first, then the nine offsets
    keys, order = np.concatenate(keys), np.concatenate(order)
    uniq, first = np.unique(keys, return_index=True)
    uniq = uniq[np.argsort(order[first], kind="stable")]
    return [(int(k // 360 - 90), int(k % 360 - 180)) for k in uniq]


def landmarks(lat, lon, has_land=None, reach_km: float = 40.0, offline: bool = False, log=print,
              max_new: int | None = None) -> list[dict]:
    """Gazetteer features for the cells along a route. lat/lon: route points in flight order (so that, when there are
    more new cells than one run may fetch, the ends of the flight - where you are low - are filled first).
    has_land(lat0, lon0) -> bool lets the caller skip open-sea cells without asking."""
    max_new = MAX_NEW_CELLS if max_new is None else max_new
    cells = cells_for(lat, lon, reach_km)
    # ends first: interleave from the departure and the arrival
    order = [c for pair in zip(cells, reversed(cells)) for c in pair]
    order = list(dict.fromkeys(order + cells))
    cls = None
    feats, fetched, failed, skipped, stale = [], 0, 0, 0, 0
    for lat0, lon0 in order:
        p = _cell_path(lat0, lon0)
        items = None
        fresh = p.exists() and (time.time() - p.stat().st_mtime) / 86400 < REFRESH_DAYS
        if fresh:
            items = json.loads(p.read_text())["items"]
        elif not offline and failed < 3 and fetched < max_new and (has_land is None or has_land(lat0, lon0)):
            if not fetched and not failed:
                log(f"  Wikidata landmarks: fetching the map cells along the route that are not cached yet "
                    f"(at most {max_new} in one run, a few seconds each)")
            try:
                cls = cls or classes()
                items = _fetch_cell(lat0, lon0, cls)
            except RuntimeError:
                items = None
            if items is None:
                failed += 1
            else:
                fetched += 1
                if fetched % 20 == 0:
                    log(f"    {fetched} cells fetched")
                time.sleep(PAUSE_S)
        elif has_land is not None and not p.exists() and not has_land(lat0, lon0):
            continue
        if items is None and p.exists():
            items = json.loads(p.read_text())["items"]   # past its refresh date, but better than nothing
            stale += 1
        if items is None:
            skipped += 1
            continue
        for it in items:
            _, what, kind, size, least = KINDS[it["k"]]
            if it["links"] < FAME_FACTOR * least:
                continue
            feats.append(dict(id=f"wd:{it['q']}", name=it["name"], local=it["name"], kind=kind, sub=what.upper(),
                              what=what, lat=it["lat"], lon=it["lon"], size_km=size, z=0.0, fame=it["links"],
                              qid=it["q"], src="Wikidata"))
    note = f"  Wikidata landmarks: {len(feats)} in {len(order)} cells"
    if fetched:
        note += f", {fetched} cells fetched"
    if skipped or failed:
        note += f", {skipped} cells not available yet" + (" (the service is not answering)" if failed >= 3 else
                                                           " (run again, or `planeview landmarks`, to fetch more)"
                                                           if not offline else "")
    log(note)
    return feats

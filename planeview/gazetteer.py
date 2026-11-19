"""Named things that can be seen from an aircraft, gathered along a route corridor.

Sources
  GeoNames per-country dumps (CC BY 4.0): settlements, peaks, lakes, islands, capes, bays, glaciers, canyons, and a few
      landmark types (bridges, dams, lighthouses, stadiums, castles, piers). English names and *fame* (the number of
      Wikipedia language links per place) come from the alternatenames files.
  Natural Earth 1:10m (public domain): big cities with urban-area polygons (real city size), lakes with outlines,
      rivers, mountain ranges / deserts / plateaus / peninsulas, seas and bays, island areas, countries.

Each feature: {id, name, kind, sub, lat, lon, pts (N,2) sample points, z (m), size_km, pop, elev, area_km2, fame,
src, cc, outline (optional, for drawing)}.
Multi-point features (rivers, ranges, seas) are tested at every sample point.
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import math
import re
import unicodedata
import zipfile
from functools import lru_cache

import numpy as np
import orjson
from scipy.spatial import cKDTree
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.strtree import STRtree

from . import cache, geo, ne

GN_URL = "https://download.geonames.org/export/dump/"
KINDS = {
    "settlement": {"PPL", "PPLA", "PPLA2", "PPLA3", "PPLA4", "PPLA5", "PPLC", "PPLG", "PPLS", "PPLF", "PPLR"},
    "peak": {"PK", "MT", "VLC", "HLL", "PKS"},
    "lake": {"LK", "RSV", "LGN", "LKS", "RSVT", "LKN", "LKC", "LKO", "RSVI"},
    "island": {"ISL", "ISLS", "ISLET", "ISLT", "ATOL", "ISLM"},
    "coast": {"CAPE", "PEN", "PT", "PTS", "ISTH", "SPIT", "BCH"},
    "sea": {"BAY", "BAYS", "GULF", "SEA", "STRT", "SD", "INLT", "CHN", "FJD", "ESTY", "LGN", "HBR", "LGNS"},
    "region": {"MTS", "VAL", "PLAT", "PLN", "GLCR", "DSRT", "CNYN", "GRGE", "UPLD", "RDGE", "DLTA"},
    "landmark": {"BDG", "DAM", "LTHSE", "STDM", "CSTL", "PIER", "TOWR", "PS", "PSN", "PAL", "MNMT", "AIRP",
                 "BDGQ", "OBS", "CTRS", "RSRT", "MALL", "RLG", "PRK"},
}
CODE2KIND = {c: k for k, cs in KINDS.items() for c in cs}
CODE2KIND["LGN"] = "lake"
# Kept only when Wikipedia knows them (there are tens of thousands of each): the least number of language links.
# Harbours, beaches, lagoons and parks are things people do look at and name from a window (a harbour's shape, a
# park as a dark gap in a city's lights). (The same test does not work for villages under 500 people: nearly every
# French and Italian commune has an article - 110,000 features on a London-Athens route.)
# GeoNames usually records one Wikipedia link per place (the English article), so the test is "has an article".
MIN_LINKS = {"HBR": 1, "LGNS": 1, "BCH": 1, "PRK": 1}
LANDMARK_LINKS = {"AIRP": 1, "PRK": 1}   # other landmark kinds need 3
COLS_VERSION = 4          # bump when the rules above change: the per-country column files are rebuilt


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "", s)


# ------------------------------------------------------------------ GeoNames, cached as compact column files
def _gn_zip(rel: str):
    return cache.fetch(f"geonames/{rel}", GN_URL + rel, log_key=rel)


@lru_cache(maxsize=None)
def geonames(cc: str) -> dict:
    """Columns for one country's features of our kinds (cached as cache/geonames/cols/<CC>.json.gz)."""
    out_p = cache.CACHE / "geonames" / f"cols{COLS_VERSION}" / f"{cc}.json.gz"
    if out_p.exists():
        return orjson.loads(gzip.decompress(out_p.read_bytes()))
    try:
        z = zipfile.ZipFile(_gn_zip(f"{cc}.zip"))
    except RuntimeError:
        return {}
    cols = {k: [] for k in ("gid", "name", "lat", "lon", "code", "pop", "elev")}
    with z.open(f"{cc}.txt") as fh:
        for r in csv.reader(io.TextIOWrapper(fh, "utf-8"), delimiter="\t", quoting=csv.QUOTE_NONE):
            kind = CODE2KIND.get(r[7])
            if not kind:
                continue
            pop = int(r[14] or 0)
            if kind == "settlement" and pop < 500 and r[7] in ("PPL", "PPLF", "PPLR", "PPLS"):
                continue
            elev = r[15] or r[16]
            elev = None if elev in ("", "-9999") else int(float(elev))
            cols["gid"].append(int(r[0])); cols["name"].append(r[1]); cols["lat"].append(float(r[4]))
            cols["lon"].append(float(r[5])); cols["code"].append(r[7]); cols["pop"].append(pop)
            cols["elev"].append(elev)
    keep = set(cols["gid"])
    en, links, qid = _alt(cc, keep)
    # the kinds in MIN_LINKS stay only if Wikipedia has them
    ok = [links.get(g, 0) >= MIN_LINKS.get(c, 0) for g, c in zip(cols["gid"], cols["code"])]
    cols = {k: [v for v, o in zip(vs, ok) if o] for k, vs in cols.items()}
    cols["en"] = [en.get(g) for g in cols["gid"]]
    cols["links"] = [links.get(g, 0) for g in cols["gid"]]
    cols["qid"] = [qid.get(g) for g in cols["gid"]]
    cols["cc"] = cc
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_bytes(gzip.compress(orjson.dumps(cols)))
    return cols


def _alt(cc: str, keep: set):
    """English names (preferred first), Wikipedia link counts and Wikidata ids from alternatenames/<CC>.zip."""
    try:
        z = zipfile.ZipFile(_gn_zip(f"alternatenames/{cc}.zip"))
    except RuntimeError:
        return {}, {}, {}
    pref, first, links, qid = {}, {}, {}, {}
    with z.open(f"{cc}.txt") as fh:
        for r in csv.reader(io.TextIOWrapper(fh, "utf-8"), delimiter="\t", quoting=csv.QUOTE_NONE):
            if len(r) < 4:
                continue
            g = int(r[1])
            if g not in keep:
                continue
            lang = r[2]
            if lang == "link":
                if "wikipedia.org" in r[3]:
                    links[g] = links.get(g, 0) + 1
            elif lang == "wkdt":
                qid[g] = r[3]
            elif lang == "en" and not r[3].startswith("http"):
                if len(r) > 4 and r[4] == "1":
                    pref[g] = r[3]
                first.setdefault(g, r[3])
    return {**first, **pref}, links, qid


# ------------------------------------------------------------------ Natural Earth helpers
@lru_cache(maxsize=1)
def countries():
    out = []
    for rec, g in ne.layer("ne_10m_admin_0_countries"):
        if g is None:
            continue
        cc = rec.get("iso_a2_eh") or rec.get("iso_a2")
        if not cc or cc == "-99":
            cc = rec.get("wb_a2") or ""
        out.append(dict(cc=cc, name=rec.get("name_en") or rec.get("name"), geom=g))
    return out


@lru_cache(maxsize=1)
def _country_tree():
    cs = countries()
    return STRtree([c["geom"] for c in cs]), cs


def country_at(lat, lon) -> dict | None:
    tree, cs = _country_tree()
    p = Point(lon, lat)
    for i in tree.query(p, predicate="intersects"):
        return cs[int(i)]
    return None


def nearest_country(lat, lon) -> str:
    tree, cs = _country_tree()
    i = tree.nearest(Point(lon, lat))
    return cs[int(i)]["name"] if i is not None else ""


@lru_cache(maxsize=1)
def _urban():
    polys = [(rec["area_sqkm"], g) for rec, g in ne.layer("ne_10m_urban_areas") if g is not None]
    return STRtree([g for _, g in polys]), [a for a, _ in polys], [g for _, g in polys]


@lru_cache(maxsize=1)
def _land_parts():
    """Every land polygon (continents and islands) with its area in km2, for island sizes and coastline tests."""
    parts = []
    for name in ("ne_10m_land", "ne_10m_minor_islands"):
        try:
            lay = ne.layer(name)
        except Exception:
            continue
        for _, g in lay:
            if g is None:
                continue
            for p in (g.geoms if isinstance(g, MultiPolygon) else [g]):
                c = p.representative_point()
                area = p.area * (111.32 ** 2) * math.cos(math.radians(c.y))
                parts.append((p, area))
    return STRtree([p for p, _ in parts]), parts


def land_area_at(lat, lon, fame=0) -> float | None:
    """Area (km2) of the land polygon an island point names, or None when the point is an islet that Natural Earth
    folds into a bigger island or coast (it lies inside, but far from that polygon's centre)."""
    tree, parts = _land_parts()
    pt = Point(lon, lat)
    inside = [parts[int(i)] for i in tree.query(pt, predicate="intersects")]
    if not inside:
        return None
    poly, area = min(inside, key=lambda pa: pa[1])
    if area > 50000:
        return None
    c = poly.centroid
    r_km = math.sqrt(area / math.pi)
    off = float(geo.dist_km(lat, lon, c.y, c.x))
    if off > 0.6 * r_km + 2 and fame < 8:
        return None
    return area


def on_land(lat, lon) -> np.ndarray:
    tree, parts = _land_parts()
    import shapely
    pts = shapely.points(np.asarray(lon), np.asarray(lat))
    idx = tree.query(pts, predicate="intersects")
    out = np.zeros(len(pts), bool)
    out[idx[0]] = True
    return out


def _poly_samples(g, step_km=15.0, max_n=400):
    """Interior sample points of a (multi)polygon, roughly every step_km."""
    import shapely
    minx, miny, maxx, maxy = g.bounds
    lat0 = (miny + maxy) / 2
    for _ in range(6):
        dy = step_km / 111.32
        dx = dy / max(0.15, math.cos(math.radians(lat0)))
        xs, ys = np.arange(minx + dx / 2, maxx, dx), np.arange(miny + dy / 2, maxy, dy)
        if len(xs) * len(ys) <= max_n * 4:
            break
        step_km *= 1.6
    X, Y = np.meshgrid(xs, ys)
    pts = shapely.points(X.ravel(), Y.ravel())
    inside = shapely.contains(g, pts)
    P = np.column_stack([Y.ravel()[inside], X.ravel()[inside]])
    if len(P) > max_n:
        P = P[np.linspace(0, len(P) - 1, max_n).astype(int)]
    if len(P) == 0:
        c = g.representative_point()
        P = np.array([[c.y, c.x]])
    return P


def _line_samples(g, step_km=5.0):
    out = []
    for ln in (g.geoms if hasattr(g, "geoms") else [g]):
        c = np.asarray(ln.coords)
        if len(c) < 2:
            continue
        seg = geo.dist_km(c[:-1, 1], c[:-1, 0], c[1:, 1], c[1:, 0])
        s = np.r_[0, np.cumsum(seg)]
        n = max(2, int(s[-1] / step_km) + 1)
        si = np.linspace(0, s[-1], n)
        out.append(np.column_stack([np.interp(si, s, c[:, 1]), np.interp(si, s, c[:, 0])]))
    return np.vstack(out) if out else np.zeros((0, 2))


def _outline(g, tol=0.01, max_pts=400):
    """Simplified outline as [[lat, lon], ...] rings for drawing."""
    g = g.simplify(tol, preserve_topology=False)
    rings = []
    for p in (g.geoms if hasattr(g, "geoms") else [g]):
        if isinstance(p, Polygon):
            c = np.asarray(p.exterior.coords)
        elif isinstance(p, LineString):
            c = np.asarray(p.coords)
        else:
            continue
        if len(c) > max_pts:
            c = c[np.linspace(0, len(c) - 1, max_pts).astype(int)]
        rings.append([[round(y, 4), round(x, 4)] for x, y in c])
    return rings


# ------------------------------------------------------------------ the corridor
class Corridor:
    """Route sample points (from every candidate track) with a KD-tree for distance-to-route queries."""

    FAR_KM = 500.0   # beyond this a point is just "far": no test in the gazetteer reaches further than 220 km

    def __init__(self, lat, lon):
        self.lat, self.lon = np.asarray(lat, float), np.asarray(lon, float)
        self.tree = cKDTree(geo.xyz(self.lat, self.lon))
        self._far = 2 * math.sin(self.FAR_KM / geo.R_KM / 2)   # the same distance as a chord of the unit sphere

    def dist_km(self, lat, lon):
        """Distance from each point to the nearest route point; exact up to FAR_KM, 20,000 km (half-way round)
        beyond it. The bound is what makes this fast: a whole country's place names are tested against some
        90,000 route points, and without it every far-away place searches the entire tree (2 min of a 4 min
        prediction; with it and all cores, a second or two)."""
        d, _ = self.tree.query(geo.xyz(np.asarray(lat, float), np.asarray(lon, float)),
                               distance_upper_bound=self._far, workers=-1)
        return 2 * geo.R_KM * np.arcsin(np.clip(d / 2, 0, 1))

    def countries(self, buffer_km=150):
        """Every country (incl. small island territories such as Guernsey) with any part within buffer_km of the route:
        each country's simplified outline vertices are tested against the corridor."""
        tree, cs = _country_tree()
        out = set()
        la0, la1 = self.lat.min() - 3, self.lat.max() + 3
        for c in cs:
            g = c["geom"]
            b = g.bounds
            if b[3] < la0 or b[1] > la1:
                continue
            pts = []
            for p in (g.geoms if hasattr(g, "geoms") else [g]):
                ring = np.asarray(p.simplify(0.05).exterior.coords)
                pts.append(ring[:: max(1, len(ring) // 400)])
                rp = p.representative_point()
                pts.append(np.array([[rp.x, rp.y]]))
            P = np.vstack(pts)
            if float(self.dist_km(P[:, 1], P[:, 0]).min()) <= buffer_km:
                out.add(c["cc"])
        return sorted(cc for cc in out if cc and len(cc) == 2)


def settlement_size_km(pop, lat=None, lon=None):
    """Rough built-up diameter; replaced by the Natural Earth urban polygon when one contains the place."""
    return 0.02 * math.sqrt(max(pop, 100)) + 0.3


def _cell_has_land(lat0: int, lon0: int) -> bool:
    """Any land in a 1-degree cell (so open-sea cells are never asked about)."""
    from shapely.geometry import box
    tree, _ = _land_parts()
    return len(tree.query(box(lon0, lat0, lon0 + 1, lat0 + 1), predicate="intersects")) > 0


def build(corridor: Corridor, max_km=220.0, log=print, airports=(), landmarks: bool | str = True) -> list[dict]:
    """airports: [(icao, lat, lon)] of the two ends (kept for callers; not used at present).
    landmarks: True fetches Wikidata landmarks along the corridor (cached per cell), "offline" uses only cached
    cells, False leaves them out."""
    feats: list[dict] = []
    if landmarks:
        from .sources import wikidata
        try:
            found = wikidata.landmarks(corridor.lat, corridor.lon, has_land=_cell_has_land,
                                       offline=landmarks == "offline", log=log)
        except Exception as e:  # landmarks are an extra: never fail a prediction over them
            log(f"  Wikidata landmarks skipped: {e}")
            found = []
        if found:
            dk = corridor.dist_km([f["lat"] for f in found], [f["lon"] for f in found])
            for f, d in zip(found, dk):
                if d <= 40:
                    f["route_km"] = round(float(d), 1)
                    feats.append(f)
    ccs = corridor.countries()
    log(f"  gazetteer: countries {' '.join(ccs)}")
    utree, uarea, upoly = _urban()

    # ---- GeoNames, by country
    for cc in ccs:
        cols = geonames(cc)
        if not cols:
            continue
        lat, lon = np.array(cols["lat"]), np.array(cols["lon"])
        d = corridor.dist_km(lat, lon)
        pop = np.array(cols["pop"])
        links = np.array(cols["links"])
        code = cols["code"]
        kind = np.array([CODE2KIND[c] for c in code])
        elev = np.array([e if e is not None else -1 for e in cols["elev"]], float)
        # how far from the route each feature is worth considering
        lim = np.full(len(lat), 60.0)
        s = kind == "settlement"
        lim[s] = np.where(pop[s] >= 200000, 200, np.where(pop[s] >= 50000, 120, np.where(pop[s] >= 10000, 70,
                          np.where(pop[s] >= 2000, 40, 20))))
        lim[s & (links >= 20)] = np.maximum(lim[s & (links >= 20)], 90)
        pk = kind == "peak"
        lim[pk] = np.where(elev[pk] >= 3000, 180, np.where(elev[pk] >= 1500, 110, 50))
        need = np.array([LANDMARK_LINKS.get(c, 3) for c in code])
        lim[kind == "landmark"] = np.where(links[kind == "landmark"] >= need[kind == "landmark"], 40, 0)
        for k in ("lake", "island", "coast", "sea"):
            lim[kind == k] = 100
        lim[kind == "region"] = 120
        sel = np.flatnonzero(d <= np.minimum(lim, max_km))
        for i in sel:
            k = str(kind[i])
            if k == "peak" and elev[i] < 300 and links[i] == 0:
                continue
            if k == "landmark" and (links[i] < need[i] or (code[i] == "AIRP" and "erminal" in cols["name"][i])):
                continue
            name = cols["en"][i] or cols["name"][i]
            f = dict(id=f"gn:{cols['gid'][i]}", name=name, local=cols["name"][i], kind=k, sub=code[i],
                     lat=float(lat[i]), lon=float(lon[i]), pop=int(pop[i]), elev=None if elev[i] < 0 else int(elev[i]),
                     fame=int(links[i]), qid=cols["qid"][i], cc=cc, src="GeoNames", route_km=round(float(d[i]), 1))
            if k == "settlement":
                f["size_km"] = settlement_size_km(pop[i])
                hit = utree.query(Point(lon[i], lat[i]), predicate="intersects")
                if len(hit):
                    a = max(uarea[int(j)] for j in hit)
                    if pop[i] >= 20000 or a < 200:  # a village inside a metro polygon is not the metro
                        f["size_km"] = max(f["size_km"], 2 * math.sqrt(a / math.pi))
            elif k == "island":
                a = land_area_at(lat[i], lon[i], int(links[i]))
                if a is not None:
                    f["area_km2"] = round(a, 2)
                    f["size_km"] = 2 * math.sqrt(a / math.pi)
                else:
                    f["size_km"] = 0.6
            elif k == "peak":
                f["size_km"] = 1.5
            elif k == "lake":
                f["size_km"] = 1.0
            else:
                f["size_km"] = {"AIRP": 2.5, "PRK": 1.2, "HBR": 3.0, "BCH": 1.5}.get(
                    code[i], {"coast": 2.0, "sea": 5.0, "region": 10.0, "landmark": 0.3}[k])
            f["z"] = float(elev[i]) if k == "peak" and elev[i] > 0 else 0.0
            feats.append(f)
    log(f"  gazetteer: {len(feats)} GeoNames features within range")

    # ---- Natural Earth big features
    nf = []
    for rec, g in ne.layer("ne_10m_populated_places_simple"):
        la, lo = rec["latitude"], rec["longitude"]
        nf.append(dict(id=f"ne:place:{rec['ne_id']}", name=rec["nameascii"] if not rec["name"] else rec["name"],
                       kind="settlement", sub=rec["featurecla"], lat=la, lon=lo, pop=int(rec["pop_max"] or 0),
                       fame=max(0, 12 - int(rec["scalerank"])) * 3, cc=rec.get("iso_a2"), src="Natural Earth",
                       scalerank=int(rec["scalerank"]), z=0.0))
    nf = [f for f in nf if f["pop"] >= 50000]
    if nf:
        d = corridor.dist_km([f["lat"] for f in nf], [f["lon"] for f in nf])
        for f, dk in zip(nf, d):
            if dk <= min(max_km, 250):
                f["route_km"] = round(float(dk), 1)
                f["size_km"] = settlement_size_km(f["pop"])
                hit = utree.query(Point(f["lon"], f["lat"]), predicate="intersects")
                if len(hit):
                    f["size_km"] = max(f["size_km"], 2 * math.sqrt(max(uarea[int(j)] for j in hit) / math.pi))
                feats.append(f)

    for rec, g in (x for name in ne.LAKE_LAYERS for x in ne.layer(name)):
        if g is None or not (rec.get("name") or "").strip():
            continue
        c = g.representative_point()
        pts = _poly_samples(g, 6.0, 200)
        dk = corridor.dist_km(pts[:, 0], pts[:, 1])
        if dk.min() > 120:
            continue
        area = g.area * 111.32 ** 2 * math.cos(math.radians(c.y))
        feats.append(dict(id=f"ne:lake:{rec.get('ne_id') or rec['name']}", name=ne.en(rec) if rec.get("name_en") else rec["name"],
                          kind="lake", sub=rec["featurecla"], lat=c.y, lon=c.x, pts=pts, area_km2=round(area, 1),
                          size_km=2 * math.sqrt(area / math.pi), fame=max(0, 12 - int(rec.get("scalerank") or 10)) * 2,
                          qid=rec.get("wikidataid"), src="Natural Earth", z=0.0, route_km=round(float(dk.min()), 1),
                          outline=_outline(g, 0.005)))

    for name in ne.RIVER_LAYERS:
        try:
            lay = ne.layer(name)
        except Exception:
            continue
        for rec, g in lay:
            nm = (rec.get("name_en") or rec.get("name") or "").strip()
            if g is None or not nm or rec.get("featurecla") == "Lake Centerline":
                continue
            pts = _line_samples(g, 4.0)
            if not len(pts):
                continue
            dk = corridor.dist_km(pts[:, 0], pts[:, 1])
            near = dk <= 70
            if not near.any():
                continue
            pts = pts[near]
            sr = int(rec.get("scalerank") or 10)
            feats.append(dict(id=f"ne:river:{rec.get('ne_id')}:{nm}", name=nm if "river" in nm.lower() else nm,
                              kind="river", sub=rec.get("featurecla"), lat=float(pts[len(pts) // 2, 0]),
                              lon=float(pts[len(pts) // 2, 1]), pts=pts,
                              size_km=0.6 if sr <= 3 else 0.35 if sr <= 5 else 0.22 if sr <= 7 else 0.12,
                              fame=max(0, 12 - sr) * 1.5, qid=rec.get("wikidataid"), src="Natural Earth", z=0.0,
                              route_km=round(float(dk.min()), 1), outline=_outline(g, 0.01)))

    for rec, g in ne.layer("ne_10m_geography_regions_polys"):
        if g is None:
            continue
        fc = rec.get("featurecla") or ""
        pts = _poly_samples(g, 12.0, 300)
        dk = corridor.dist_km(pts[:, 0], pts[:, 1])
        if dk.min() > 120:
            continue
        if fc in ("Continent", "Ocean") or (fc in ("Island", "Island group") and g.area > 30):
            continue  # too big to be a sight
        kind = {"Island": "island", "Island group": "island", "Range/mtn": "range", "Peninsula": "coast",
                "Isthmus": "coast", "Desert": "region", "Plateau": "region", "Plain": "region", "Basin": "region",
                "Delta": "region", "Valley": "region", "Lowland": "region", "Tundra": "region", "Gorge": "region",
                "Wetlands": "region", "Geoarea": "region", "Coast": "coast", "Foothills": "range",
                "Depression": "region", "Pen/cape": "coast", "Dragons-be-here": None}.get(fc, "region")
        if kind is None:
            continue
        c = g.representative_point()
        area = g.area * 111.32 ** 2 * math.cos(math.radians(c.y))
        feats.append(dict(id=f"ne:region:{rec.get('ne_id')}", name=ne.en(rec), kind=kind, sub=fc, lat=c.y, lon=c.x,
                          pts=pts, area_km2=round(area), size_km=2 * math.sqrt(area / math.pi),
                          fame=max(0, 10 - int(rec.get("scalerank") or 9)) * 4, qid=rec.get("wikidataid"),
                          src="Natural Earth", z=0.0, route_km=round(float(dk.min()), 1), broad=True,
                          outline=_outline(g, 0.02, 300)))

    for rec, g in ne.layer("ne_10m_geography_regions_points"):
        la, lo = rec.get("lat_y"), rec.get("long_x")
        if la is None:
            continue
        dk = float(corridor.dist_km([la], [lo])[0])
        if dk > 150:
            continue
        fc = (rec.get("featurecla") or "").lower()
        kind = "peak" if fc in ("mountain", "volcano") else "coast" if fc in ("cape", "pen/cape") else "region"
        feats.append(dict(id=f"ne:point:{rec.get('ne_id')}", name=ne.en(rec), kind=kind, sub=fc, lat=la, lon=lo,
                          size_km=3.0, fame=max(0, 10 - int(rec.get("scalerank") or 9)) * 4,
                          qid=rec.get("wikidataid"), src="Natural Earth", z=0.0, route_km=round(dk, 1)))

    for rec, g in ne.layer("ne_10m_geography_marine_polys"):
        if g is None or (rec.get("featurecla") or "").lower() == "ocean":
            continue
        pts = _poly_samples(g, 15.0, 300)
        dk = corridor.dist_km(pts[:, 0], pts[:, 1])
        if dk.min() > 60:
            continue
        c = g.representative_point()
        area = g.area * 111.32 ** 2 * math.cos(math.radians(c.y))
        feats.append(dict(id=f"ne:marine:{rec.get('ne_id')}", name=ne.en(rec), kind="sea", sub=rec.get("featurecla"),
                          lat=c.y, lon=c.x, pts=pts, area_km2=round(area), size_km=2 * math.sqrt(area / math.pi),
                          fame=max(0, 10 - int(rec.get("scalerank") or 9)) * 4, qid=rec.get("wikidataid"),
                          src="Natural Earth", z=0.0, route_km=round(float(dk.min()), 1), broad=True,
                          outline=_outline(g, 0.03, 300)))

    feats = _dedupe(feats)
    feats = _dedupe_settlements(feats)
    for f in feats:
        if "pts" not in f:
            f["pts"] = np.array([[f["lat"], f["lon"]]])
        else:
            f["pts"] = np.asarray(f["pts"], float)
    log(f"  gazetteer: {len(feats)} features after merging")
    return feats


def _dedupe(feats):
    """Merge the same place from GeoNames and Natural Earth (name match within a few km); keep GeoNames ids, take the
    larger size/fame, NE outline and area."""
    by = {}
    for f in feats:
        by.setdefault((norm(f["name"])[:12], f["kind"] if f["kind"] != "range" else "region"), []).append(f)
    drop = set()
    for (_, kind), fs in by.items():
        if len(fs) < 2:
            continue
        fs.sort(key=lambda f: (f["src"] != "GeoNames", -f.get("fame", 0)))
        for i, a in enumerate(fs):
            if id(a) in drop:
                continue
            for b in fs[i + 1:]:
                if id(b) in drop:
                    continue
                tol = max(8.0, 0.5 * max(a.get("size_km", 1), b.get("size_km", 1)))
                if geo.dist_km(a["lat"], a["lon"], b["lat"], b["lon"]) <= tol:
                    for k in ("outline", "area_km2", "pts", "broad"):
                        if k in b and k not in a:
                            a[k] = b[k]
                    a["size_km"] = max(a.get("size_km", 0), b.get("size_km", 0))
                    a["fame"] = max(a.get("fame", 0), b.get("fame", 0))
                    if b.get("pop", 0) > a.get("pop", 0):
                        a["pop"] = b["pop"]
                    drop.add(id(b))
    return [f for f in feats if id(f) not in drop]


def _dedupe_settlements(feats):
    """Chalkida / Chalcis, Ioannina / Ioanina: one place listed twice. Keep the biggest, most famous within 3 km."""
    st = [f for f in feats if f["kind"] == "settlement"]
    if len(st) < 2:
        return feats
    st.sort(key=lambda f: (-(f.get("pop") or 0), -(f.get("fame") or 0)))
    tree = cKDTree(geo.xyz([f["lat"] for f in st], [f["lon"] for f in st]))
    drop = set()
    for i, f in enumerate(st):
        if id(f) in drop:
            continue
        r = max(2.0, min(6.0, 0.25 * f.get("size_km", 1)))
        for j in tree.query_ball_point(geo.xyz(f["lat"], f["lon"]), 2 * math.sin(r / geo.R_KM / 2)):
            g = st[j]
            if g is f or id(g) in drop:
                continue
            same = norm(g["name"])[:4] == norm(f["name"])[:4] or (g.get("pop") or 0) < 0.2 * max(1, f.get("pop") or 0)
            if same and (g.get("pop") or 0) <= (f.get("pop") or 0):
                f["fame"] = max(f.get("fame", 0), g.get("fame", 0))
                if g.get("seen") and not f.get("seen"):
                    f["seen"] = g["seen"]
                drop.add(id(g))
    return [f for f in feats if id(f) not in drop]

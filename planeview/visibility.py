"""What can be seen from a window seat, along one track.

The view model is calibrated on window photographs from six flights (231 photos with a solved camera direction, 967
named things in them): people photograph from about 25 deg behind abeam to 55 deg ahead of it (median 21 deg ahead) and 0-50 deg
below the horizon (median 20 deg); named things were identified out to a median 17 km (settlements), 40 km (peaks),
23 km (coast), with a 90th percentile of ~12 km per 10,000 ft in climb and cruise and ~24 km per 10,000 ft in descent.
Every number below is in VIEW so it travels with each prediction (the companion app can re-evaluate live with it).
"""
from __future__ import annotations

import numpy as np
from scipy.spatial import cKDTree

from . import geo

VIEW = dict(
    # window cone, degrees: forward of abeam (+ = towards the nose), and below the horizontal
    fa_min=-25.0, fa_max=55.0, fa_best=20.0, fa_spread=40.0,
    dep_max=50.0, dep_best=18.0, dep_spread=22.0, dep_floor=-0.5,   # dep_floor: relative to the sea-horizon dip
    # haze: visual range in km, by height (boundary-layer haze below ~3 km, clearer air above)
    haze_low_km=45.0, haze_high_km=110.0, haze_h1_m=1500.0, haze_h2_m=6000.0,
    # smallest apparent size (deg) at which a thing of each kind is recognisable
    min_deg=dict(settlement=0.35, lake=0.30, island=0.30, coast=0.6, sea=1.0, region=2.0, range=1.5, river=0.25,
                 peak=0.0, landmark=0.25),
    # peaks: recognisable range grows with height above ~1,000 m (snow, skyline)
    peak_km_per_100m=4.5, peak_max_km=180.0,
    # night: lit settlements, brightness from population; lit landmarks (NIGHT_LIT); and dark shapes - parks, water -
    # where they interrupt a big city's lights
    night_pop_min=15000, night_range_km=160.0, night_lit_km=70.0, night_dark_km=45.0, night_dark_city_pop=100000,
    q_min=0.12,   # below this a sighting is not counted
    # over the wing: the view down is blocked below about this angle (behind abeam it is blocked further)
    wing_dep_max=16.0,
)

SEARCH_KM = (25, 50, 90, 140, 200, 260)

# Terrain. Below LOS_ALT_M the aircraft is among the mountains, not above them: on an approach to Innsbruck
# what is seen is Patscherkofel and Glungezer above the wing and the towns on the valley floor - not, as a
# sea-level Earth would have it, reservoirs two ridges away. So below this height (a) every target
# takes its real height from the DEM, not sea level, (b) a peak may be looked up at, and (c) the sight line is
# tested against the ground in between.
LOS_ALT_M = 7000.0
LOS_STEP_KM = 1.2
LOS_MARGIN_M = 40.0
UP_MAX_DEG = 20.0       # how far above the horizontal a mountain may stand and still be in the window


class Terrain:
    """Heights from the Copernicus DEM tiles the window views already use (~360 m cells), nearest cell.
    Tiles are fetched on first use (4 MB each, 0.25 MB kept); the sea and a failed download count as 0 m."""

    def __init__(self, allow_fetch=True, log=None):
        self.allow_fetch, self.log, self.tiles, self.fetched = allow_fetch, log, {}, 0

    def _tile(self, a, b):
        key = (a, b)
        if key not in self.tiles:
            from . import cache, views
            name = f"{'N' if a >= 0 else 'S'}{abs(a):02d}{'E' if b >= 0 else 'W'}{abs(b):03d}"
            have = (cache.CACHE / "dem_lo" / f"{name}.npy").exists()
            t = None
            if have or self.allow_fetch:
                try:
                    t = views.dem_lo(a, b)
                except Exception:
                    t = None
                self.fetched += 0 if have else 1
            self.tiles[key] = t
        return self.tiles[key]

    def sample(self, lat, lon):
        lat, lon = np.asarray(lat, float), np.asarray(lon, float)
        z = np.zeros(lat.shape, np.float32)
        la0, lo0 = np.floor(lat).astype(np.int64), np.floor(lon).astype(np.int64)
        key = la0 * 1000 + lo0
        for k in np.unique(key):
            m = key == k
            a, b = int(la0[m].flat[0]), int(lo0[m].flat[0])
            t = self._tile(a, b)
            if t is None:
                continue
            n = t.shape[0]
            r = np.clip(((a + 1 - lat[m]) * n).astype(np.int64), 0, n - 1)
            c = np.clip(((lon[m] - b) * n).astype(np.int64), 0, n - 1)
            z[m] = t[r, c]
        return np.maximum(z, 0.0)

# How bright a landmark is after dark (0-1), by GeoNames code or Wikidata kind. Over a big city at
# night what stands out is floodlit stadiums, landmarks and airport aprons - and parks, rivers and reservoirs as
# black gaps in the street lights (over London: Hyde Park, Hampstead Heath, the Thames, the Lea reservoirs).
NIGHT_LIT = {"AIRP": 1.0, "STDM": 0.8, "STADIUM": 0.8, "PORT": 0.6, "MALL": 0.4, "TOWR": 0.5, "TOWER": 0.5,
             "BDG": 0.5, "BDGQ": 0.5, "BRIDGE": 0.5, "PIER": 0.5, "PS": 0.5, "PSN": 0.5, "POWER STATION": 0.5,
             "PAL": 0.3, "CSTL": 0.3, "CASTLE": 0.3, "FORT": 0.3, "MNMT": 0.3, "LTHSE": 0.3, "LIGHTHOUSE": 0.3}
NIGHT_DARK_KINDS = ("lake", "river", "sea")       # and parks (landmark, PRK)


def _range_km(f):
    """Largest distance worth testing for a feature (km)."""
    k = f["kind"]
    if k == "settlement":
        p = f.get("pop", 0)
        return 200 if p >= 100000 else 140 if p >= 20000 else 90 if p >= 5000 else 50
    if k == "peak":
        e = f.get("elev") or 0
        return min(VIEW["peak_max_km"], max(25.0, (e - 800) / 100 * VIEW["peak_km_per_100m"]))
    if k in ("sea", "region", "range"):
        return 140
    if k in ("lake", "island", "coast"):
        return 90 if f.get("size_km", 1) < 5 else 140
    if k == "river":
        return 60
    return 30


class FeatureIndex:
    """Flat arrays of every feature sample point, one KD-tree per search radius class."""

    def __init__(self, feats):
        self.feats = feats
        owner, lat, lon, z, rng = [], [], [], [], []
        for i, f in enumerate(feats):
            P = f["pts"]
            owner.append(np.full(len(P), i))
            lat.append(P[:, 0]); lon.append(P[:, 1])
            z.append(np.full(len(P), f.get("z", 0.0)))
            rng.append(np.full(len(P), _range_km(f)))
        self.owner = np.concatenate(owner); self.lat = np.concatenate(lat); self.lon = np.concatenate(lon)
        self.z = np.concatenate(z); self.rng = np.concatenate(rng)
        self.size = np.array([f.get("size_km", 1.0) for f in feats])
        self.kind = np.array([f["kind"] for f in feats])
        self.pop = np.array([f.get("pop", 0) or 0 for f in feats])
        self.elev = np.array([f.get("elev") or 0 for f in feats], float)
        self.min_deg = np.array([VIEW["min_deg"].get(k, 0.3) for k in self.kind])
        self.zdem = np.full(len(self.lat), np.nan)      # ground height at each sample point, filled when needed
        X = geo.xyz(self.lat, self.lon)
        self.lit = np.array([NIGHT_LIT.get(f.get("sub") or "", 0.0) if f["kind"] == "landmark" else 0.0 for f in feats])
        # sample points of water and parks that lie inside a big city's lights (within its built-up radius)
        can = np.array([(f["kind"] in NIGHT_DARK_KINDS and not f.get("broad")) or f.get("sub") == "PRK" for f in feats])
        self.pt_dark = np.zeros(len(self.lat), bool)
        big = [f for f in feats if f["kind"] == "settlement" and (f.get("pop") or 0) >= VIEW["night_dark_city_pop"]]
        sel = np.flatnonzero(can[self.owner])
        if big and len(sel):
            tree = cKDTree(X[sel])
            for c in big:
                r = min(30.0, max(5.0, 0.5 * c.get("size_km", 1.0)))
                hit = tree.query_ball_point(geo.xyz(c["lat"], c["lon"]), 2 * np.sin(r / geo.R_KM / 2))
                self.pt_dark[sel[hit]] = True
        self.cls = []
        lo = 0
        for r in SEARCH_KM:
            sel = np.flatnonzero((self.rng > lo) & (self.rng <= r))
            if len(sel):
                self.cls.append((r, sel, cKDTree(X[sel])))
            lo = r


def haze_km(h_m):
    a = np.clip((np.asarray(h_m, float) - VIEW["haze_h1_m"]) / (VIEW["haze_h2_m"] - VIEW["haze_h1_m"]), 0, 1)
    return VIEW["haze_low_km"] + a * (VIEW["haze_high_km"] - VIEW["haze_low_km"])


def pairs(idx: FeatureIndex, lat, lon):
    """(sample index, feature point index, ground km) for every point within its class radius of a sample."""
    S = cKDTree(geo.xyz(lat, lon))
    out_i, out_j, out_d = [], [], []
    for r, sel, tree in idx.cls:
        chord = 2 * np.sin(r / geo.R_KM / 2)
        M = S.sparse_distance_matrix(tree, chord, output_type="ndarray")
        if len(M):
            out_i.append(M["i"]); out_j.append(sel[M["j"]])
            out_d.append(2 * geo.R_KM * np.arcsin(np.clip(M["v"] / 2, 0, 1)))
    if not out_i:
        return np.zeros(0, int), np.zeros(0, int), np.zeros(0)
    i, j, d = np.concatenate(out_i), np.concatenate(out_j), np.concatenate(out_d)
    keep = d <= idx.rng[j]
    return i[keep], j[keep], d[keep]


def evaluate(idx: FeatureIndex, trk: dict, sun_el=None, wing: str | None = None, terrain: Terrain | None = None):
    """Per (feature, side): best moment on this track and the visible window.

    trk: arrays t (s after takeoff), lat, lon, alt_m, course.  sun_el: sun elevation per sample (deg), or None = day.
    Returns dict {(feature index, 'L'|'R'): {...}}."""
    lat, lon, h, crs = trk["lat"], trk["lon"], np.maximum(trk["alt_m"], 30.0), trk["course"]
    i, j, d = pairs(idx, lat, lon)
    if not len(i):
        return {}
    fo = idx.owner[j]
    brg = geo.bearing(lat[i], lon[i], idx.lat[j], idx.lon[j])
    rel = geo.wrap180(brg - crs[i])
    side = np.where(rel >= 0, "R", "L")
    fa = 90 - np.abs(rel)
    zt = idx.z[j]
    low = h[i] < LOS_ALT_M if terrain is not None else np.zeros(len(i), bool)
    if low.any():
        jl = j[low]
        need = np.unique(jl[np.isnan(idx.zdem[jl])])
        if len(need):
            idx.zdem[need] = terrain.sample(idx.lat[need], idx.lon[need])
        zt = zt.copy()
        zt[low] = np.maximum(zt[low], idx.zdem[jl])
    dep = geo.depression_deg(h[i], zt, d)
    dip = geo.dip_deg(h[i])
    # over the horizon (curvature): the target must clear the sea horizon
    clear = d <= geo.horizon_km(h[i]) + geo.horizon_km(np.maximum(zt, 0)) * 0.95
    # the lower edge of the sky: the sea horizon - or, for ground that stands above the aircraft, UP_MAX_DEG up
    floor = np.where(low & (zt > h[i]), -UP_MAX_DEG, dip + VIEW["dep_floor"])
    inside = (fa >= VIEW["fa_min"]) & (fa <= VIEW["fa_max"]) & (dep >= floor) & \
             (dep <= VIEW["dep_max"]) & clear
    if wing == "over":
        inside &= (dep <= VIEW["wing_dep_max"]) | (fa > 25)
    elif wing == "behind":
        inside &= ~((fa > 30) & (dep > 25))
    cone = np.exp(-((fa - VIEW["fa_best"]) / VIEW["fa_spread"]) ** 2) * \
        np.exp(-((dep - VIEW["dep_best"]) / VIEW["dep_spread"]) ** 2)
    cone = 0.25 + 0.75 * cone
    # recognisability: apparent size against the kind's minimum, and haze over the slant range
    slant = np.hypot(d, (h[i] - zt) / 1000)
    size = idx.size[fo]
    ang = np.degrees(size / np.maximum(slant, 0.2))
    k = idx.kind[fo]
    det = np.clip(ang / np.maximum(idx.min_deg[fo], 1e-3), 0, 1.5) / 1.5
    pk = k == "peak"
    if pk.any():
        rng_pk = np.maximum(25.0, (idx.elev[fo[pk]] - 800) / 100 * VIEW["peak_km_per_100m"])
        det[pk] = np.clip(1.2 - slant[pk] / rng_pk, 0, 1)
    vis_km = haze_km(np.minimum(h[i], 9000))
    det0 = det
    det = det * np.exp(-(slant / vis_km) ** 2)
    q = cone * det
    light = np.ones(len(i))
    if sun_el is not None:
        se = sun_el[i]
        light = np.where(se >= 4, 1.0, np.where(se >= 0, 0.75, np.where(se >= -6, 0.35, 0.0)))
        # lights come up through dusk: nothing at sunset, full by the end of civil-ish twilight (-4 deg)
        dusk = np.clip(-se / 4.0, 0.0, 1.0)
        city = (k == "settlement") & (idx.pop[fo] >= VIEW["night_pop_min"])
        qn = cone * np.clip((np.log10(np.maximum(idx.pop[fo], 1)) - 4.0) / 2.0, 0, 1) * \
            np.exp(-(slant / VIEW["night_range_km"]) ** 2)
        qn = np.where(city, qn, 0.0)
        qn = np.maximum(qn, cone * idx.lit[fo] * np.exp(-(slant / VIEW["night_lit_km"]) ** 2))
        qn = np.maximum(qn, np.where(idx.pt_dark[j], 0.7 * cone * det0 * np.exp(-(slant / VIEW["night_dark_km"]) ** 2), 0.0))
        q = np.maximum(q * light, qn * dusk)
    ok = inside & (q >= VIEW["q_min"])
    # (a range, valley or sea is a name over an area: its one listed point being hidden says nothing)
    test = np.flatnonzero(ok & low & (d > 3.0) & ~np.isin(k, ("region", "range", "sea")))
    # sight line against the ground: the straight ray (less the Earth's curve) against the DEM at points about
    # LOS_STEP_KM apart - from 1.5 km out, since nearer than that a 360 m terrain cell is coarser than the height
    # above the runway. Rays are grouped by length so that a long one is not sampled too thinly to meet a ridge.
    for lo_km, hi_km in ((0, 12), (12, 30), (30, 70), (70, 1e9)):
        tb = test[(d[test] >= lo_km) & (d[test] < hi_km)]
        if not len(tb):
            continue
        n = int(np.clip(np.ceil(min(hi_km, d[tb].max()) / LOS_STEP_KM), 6, 60))
        f0 = np.clip(1.5 / d[tb], 0.03, 0.9)[:, None]
        f = f0 + (0.94 - f0) * np.linspace(0, 1, n)[None, :]
        a_la, a_lo, t_la, t_lo = lat[i[tb]][:, None], lon[i[tb]][:, None], idx.lat[j[tb]][:, None], idx.lon[j[tb]][:, None]
        ground = terrain.sample(a_la + (t_la - a_la) * f, a_lo + geo.wrap180(t_lo - a_lo) * f)
        dm = d[tb][:, None] * 1000
        ray = h[i[tb]][:, None] * (1 - f) + (zt[tb][:, None] + 15.0) * f - dm * dm * f * (1 - f) / (2 * geo.R_EFF)
        ok[tb[(ground > ray + LOS_MARGIN_M).any(1)]] = False
    if not ok.any():
        return {}
    i, fo, side, fa, dep, d, q, j = i[ok], fo[ok], side[ok], fa[ok], dep[ok], d[ok], q[ok], j[ok]
    out = {}
    # group by (feature, side)
    key = fo * 2 + (side == "R")
    order = np.lexsort((-q, key))
    key_s = key[order]
    starts = np.flatnonzero(np.r_[True, key_s[1:] != key_s[:-1]])
    ends = np.r_[starts[1:], len(order)]
    for a, b in zip(starts, ends):
        sel = order[a:b]
        best = sel[0]
        si = i[sel]
        out[(int(fo[best]), "R" if key[best] % 2 else "L")] = dict(
            i=int(i[best]), q=float(q[best]), fa=float(fa[best]), dep=float(dep[best]), d=float(d[best]),
            plat=float(idx.lat[j[best]]), plon=float(idx.lon[j[best]]),
            i0=int(si.min()), i1=int(si.max()), n=int(len(np.unique(si))))
    return out


def track_arrays(t, lat, lon, alt_ft):
    """Resample a track to the evaluation step (15 s below 15,000 ft, 30 s above) with course over ground."""
    t, lat, lon, alt = (np.asarray(a, float) for a in (t, lat, lon, alt_ft))
    alt = np.where(np.isnan(alt), np.interp(t, t[~np.isnan(alt)], alt[~np.isnan(alt)]), alt)
    ts = [t[0]]
    while ts[-1] < t[-1]:
        a = np.interp(ts[-1], t, alt)
        ts.append(ts[-1] + (15.0 if a < 15000 else 30.0))
    ts = np.array(ts[:-1] + [t[-1]])
    la, lo = geo.slerp_path(lat, lon, t, ts)
    al = np.interp(ts, t, alt)
    # course from positions 20 s either side
    la_a, lo_a = geo.slerp_path(lat, lon, t, np.clip(ts - 20, t[0], t[-1]))
    la_b, lo_b = geo.slerp_path(lat, lon, t, np.clip(ts + 20, t[0], t[-1]))
    crs = geo.bearing(la_a, lo_a, la_b, lo_b)
    return dict(t=ts, lat=la, lon=lo, alt_ft=al, alt_m=al * geo.FT, course=crs)

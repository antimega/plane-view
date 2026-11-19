"""A flight pack for the iPhone and iPad app: one file, out/packs/<id>.planeview, that holds everything the app
needs with no network - the flight is spent in flight mode.

The app does not replay the forecast. It works out where the aircraft is (GPS when it has it, the plan when it has
not) and evaluates the view from there itself, with the same model as visibility.py. So the pack is a model of the
flight and of the ground around it, not a list of sightings:

  pack.json       the flight; every likely route and every past flight as timed tracks (the app matches the live
                  position against all of them); the corridor outlines; the view model's numbers; the forecast's
                  headline sightings with their picture overlays; context (coasts, borders, top of descent)
  features.json   every named thing that could be in view from anywhere within `reach_km` of any past flight -
                  not only what the forecast listed - with the share of past flights that saw it from each side
  views/, aerial/ the rendered window view and the turned overhead image of each headline sighting
  tiles/z/x/y.jpg an offline satellite map of the corridor (EOxCloudless, as the overhead images)
  dem/*.i16       terrain (the ~360 m cells visibility.py uses) around the low parts of the flight, for sight lines
  relief/*.i16    coarse terrain (~1.4 km cells) for the whole corridor and 200 km beyond, for the app's 3D view
                  (land cells not yet cached are fetched, unless fetch_relief is off)

It is an ordinary ZIP archive: AirDrop it, open it from Files or iCloud Drive, or drop it in the app's folder.

  build(pred) -> Path
"""
from __future__ import annotations

import datetime as dt
import io
import json
import math
import os
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import LineString
from shapely.ops import unary_union

from . import aerial, cache, gazetteer as G, geo, predict as PR, visibility as V
from .output import basemap as BM

SCHEMA = "plane-view/pack/1"
REACH_KM = 60.0        # how far outside every past flight the pack still knows the ground
FLOWN_KM = 6.0         # the band drawn as "where past flights went"
ZOOM = (5, 10)         # map tiles: the whole reach to zoom 9, the flown band (+35 km) at the top zoom
LOW_ZOOM_FT = 12000    # one zoom more where the likely routes are below this, within LOW_ZOOM_KM
LOW_ZOOM_KM = 30.0
DEM_ALT_FT = 26000     # terrain is carried where any track is below this (the app uses it below 7,000 m)
DEM_KM = 130.0
RELIEF_N = 75          # coarse relief for the 3D view: cells per degree (the fine terrain is 300)
RELIEF_FINE_N = 300    # ... and for a high-detail pack, the forecaster's own ~360 m cells throughout
RELIEF_KM = 200.0      # ... carried this far beyond the reach
ICLOUD = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/PlaneView"


def _rings(poly, proj, holes=False):
    out = []
    for g in (poly.geoms if hasattr(poly, "geoms") else [poly]):
        for ring in [g.exterior] + (list(g.interiors) if holes else []):
            c = np.asarray(ring.coords)
            la, lo = proj.inv(c[:, 0], c[:, 1])
            out.append([[round(float(a), 4), round(float(b), 4)] for a, b in zip(la, lo)])
    return out


def _grid(poly, proj, step_km):
    x0, y0, x1, y1 = poly.bounds
    gx, gy = np.meshgrid(np.arange(x0, x1 + step_km, step_km), np.arange(y0, y1 + step_km, step_km))
    m = shapely.contains_xy(poly, gx.ravel(), gy.ravel())
    return proj.inv(gx.ravel()[m], gy.ravel()[m])


def _tiles(lat, lon, z):
    n = 2 ** z
    lr = np.radians(np.clip(lat, -85, 85))
    tx = np.floor((np.asarray(lon) + 180) / 360 * n).astype(int) % n
    ty = np.clip(np.floor((1 - np.log(np.tan(lr) + 1 / np.cos(lr)) / math.pi) / 2 * n).astype(int), 0, n - 1)
    return set(zip(tx.tolist(), ty.tolist()))


def _tile_file(key):
    z, x, y = key
    p = cache.CACHE / "s2" / aerial.LAYER / str(z) / str(x) / f"{y}.jpg"
    if not p.exists():
        if cache.curl(aerial.URL.format(layer=aerial.LAYER, z=z, x=x, y=y), p, timeout=30) != 200:
            return key, None
    return key, p


def _feature(f, lit, dark_pts, prior):
    d = {"i": f["id"], "n": f["name"], "k": f["kind"], "la": round(float(f["lat"]), 5), "lo": round(float(f["lon"]), 5),
         "sz": round(float(f.get("size_km", 1.0)), 2), "sal": round(PR.salience(f), 2), "r": round(float(V._range_km(f)), 1),
         "w": PR.describe(f)}
    if f.get("local") and f["local"] != f["name"]:
        d["l"] = f["local"]
    if f.get("sub"):
        d["s"] = f["sub"]
    if f.get("z"):
        d["z"] = round(float(f["z"]))
    if f.get("pop"):
        d["pop"] = int(f["pop"])
    if f.get("elev"):
        d["el"] = int(f["elev"])
    if f.get("area_km2"):
        d["ar"] = round(float(f["area_km2"]), 2)
    if f.get("fame"):
        d["fm"] = round(float(f["fame"]), 1)
    if lit:
        d["lit"] = float(lit)
    if f.get("broad"):
        d["br"] = 1
    P = f["pts"]
    if len(P) > 1:
        d["p"] = [round(float(v), 4) for v in P.ravel()]
        if dark_pts.any():
            d["pd"] = "".join("1" if b else "0" for b in dark_pts)
    elif dark_pts.any():
        d["dk"] = 1
    for sd in ("L", "R"):
        if prior.get((f["id"], sd)):
            d["p" + sd] = prior[(f["id"], sd)]
    return d


def build(pred: dict, out_path: Path | None = None, reach_km: float = REACH_KM, zoom=ZOOM, site_dir: Path | None = None,
          with_map: bool = True, landmarks: str = "offline", fetch_relief: bool = True, detail: str = "standard",
          log=print) -> Path:
    """detail "high" (for an iPad, or a sharper 3D view): the map one zoom deeper everywhere, the forecast's
    pictures at twice the size, and the fine terrain for the whole corridor. About three times the size."""
    if detail not in ("standard", "high"):
        raise ValueError("detail must be standard or high")
    fid = pred["id"]
    if detail == "high":
        zoom = (zoom[0], zoom[1] + 1)
    relief_n = RELIEF_FINE_N if detail == "high" else RELIEF_N
    out_path = out_path or cache.OUT / "packs" / f"{fid}.planeview"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fl = pred["flight"]
    variants = [np.asarray(v["track"], float) for v in pred["variants"]]
    history = [np.asarray(h["track"], float) for h in pred["history"]]
    tracks = variants + history
    v1 = variants[0] if variants else history[0]

    # ---- the corridor: where past flights went, and the wider area the pack covers
    proj = BM.for_route(v1[:, 1], v1[:, 2])
    lines = [LineString(np.c_[proj.fwd(t[:, 1], t[:, 2])]) for t in tracks]
    flown = unary_union([ln.simplify(1.0).buffer(FLOWN_KM, quad_segs=3) for ln in lines]).simplify(1.5)
    reach = unary_union([ln.simplify(1.5).buffer(reach_km, quad_segs=4) for ln in lines]).simplify(2.0)
    corridor = dict(reach_km=reach_km, flown=_rings(flown, proj, holes=True), reach=_rings(reach, proj))

    # ---- everything that could be seen from anywhere in it
    gla, glo = _grid(reach, proj, 15.0)
    C = G.Corridor(np.concatenate([t[:, 1] for t in tracks] + [gla]), np.concatenate([t[:, 2] for t in tracks] + [glo]))
    o, d = fl["orig"], fl["dest"]
    feats = G.build(C, log=log, airports=[(o["icao"], o["lat"], o["lon"]), (d["icao"], d["lat"], d["lon"])],
                    landmarks={"on": True, "offline": "offline", "off": False}[landmarks])
    idx = V.FeatureIndex(feats)
    prior = {(e["id"], sd): round(float(e["p_see"]), 2) for sd, evs in pred.get("candidates", {}).items() for e in evs}
    starts = np.r_[0, np.cumsum([len(f["pts"]) for f in feats])]
    frecs = [_feature(f, idx.lit[i], idx.pt_dark[starts[i]:starts[i + 1]], prior) for i, f in enumerate(feats)]
    log(f"  pack: {len(frecs)} features within reach ({reach_km:.0f} km beyond past flights); "
        f"{sum(1 for r in frecs if 'pL' in r or 'pR' in r)} of them seen on a past flight")

    # ---- routes
    paths = []
    for v, tr in zip(pred["variants"], variants):
        rw = " / ".join(x for x in (f"departs {v['dep_rwy']}" if v.get("dep_rwy") else None,
                                    f"lands {v['arr_rwy']}" if v.get("arr_rwy") else None) if x)
        paths.append(dict(id=v["id"], kind="variant", p=v["p"], label=rw or None, dep_rwy=v.get("dep_rwy"),
                          arr_rwy=v.get("arr_rwy"), spread_km=v.get("spread_km"), track=_track(tr)))
    for k, (h, tr) in enumerate(zip(pred["history"], history)):
        paths.append(dict(id=f"H{k + 1}", kind="history", p=h["w"], label=" ".join(x for x in (h.get("cs"), h.get("day")) if x),
                          dep_rwy=h.get("dep_rwy"), arr_rwy=h.get("arr_rwy"), track=_track(tr)))
    # ---- context, placed on the main route
    context = []
    for c in pred.get("context", []):
        c = dict(c)
        if "lat" not in c:
            c["lat"] = round(float(np.interp(c["t"], v1[:, 0], v1[:, 1])), 3)
            c["lon"] = round(float(np.interp(c["t"], v1[:, 0], v1[:, 2])), 3)
        context.append(c)

    with zipfile.ZipFile(out_path.with_suffix(".tmp"), "w") as z:
        # ---- headline sightings and their pictures: the built page's, or for a high-detail pack rendered afresh
        # at twice the size (the page's own stay small, so the site stays light to host and to load)
        site_dir = site_dir or cache.SITE / "f" / fid.lower()
        page = json.loads((site_dir / "data.json").read_text()) if (site_dir / "data.json").exists() else None
        from PIL import Image
        pics = {}       # side -> kind -> (sheet image, columns, tile w, tile h, overlay per sighting)
        if detail == "high":
            import importlib
            import tempfile
            from . import views
            os.environ["PLANEVIEW_PICTURE_SCALE"] = "2"
            # a long render that should not take the machine over: each worker single-threaded, half the cores,
            # and low priority (flat out it starves everything else running on the machine)
            for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS"):
                os.environ.setdefault(var, "1")
            try:
                os.nice(15)
            except OSError:
                pass
            importlib.reload(aerial); importlib.reload(views)
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    vv = views.render_all(pred, Path(tmp), workers=max(2, (os.cpu_count() or 4) // 2), log=log)
                    aa = aerial.render_all(pred, Path(tmp), log=log)
                    for sd in pred["timeline"]:
                        pics[sd] = {}
                        for kind, res in (("views", vv), ("aerial", aa)):
                            im = Image.open(Path(tmp) / f"{kind}-{sd}.jpg").convert("RGB")
                            im.load()
                            c, rws = res[sd]["cols"], res[sd]["rows"]
                            pics[sd][kind] = (im, c, im.width // c, im.height // rws, res[sd]["overlays"])
            finally:
                os.environ.pop("PLANEVIEW_PICTURE_SCALE", None)
                importlib.reload(aerial); importlib.reload(views)
        elif page:
            for sd, evs in pred["timeline"].items():
                pev = page.get("events", {}).get(sd, [])
                pics[sd] = {}
                for kind, key in (("views", "view"), ("aerial", "aerial")):
                    meta = page.get(kind, {}).get(sd)
                    if meta and (site_dir / meta["file"]).exists():
                        im = Image.open(site_dir / meta["file"]).convert("RGB")
                        ovs = [pev[k].get(key) if k < len(pev) and pev[k].get("id") == e["id"] else None for k, e in enumerate(evs)]
                        pics[sd][kind] = (im, meta["cols"], im.width // meta["cols"], im.height // meta["rows"], ovs)
        timeline, n_pic = {}, 0
        for sd, evs in pred["timeline"].items():
            rows = []
            for k, e in enumerate(evs):
                r = {key: e.get(key) for key in ("id", "name", "local", "kind", "what", "side", "t", "t_lo", "t_hi",
                                                 "t_end", "fa", "dep", "d_km", "alt_ft", "p_see", "confidence", "light",
                                                 "ac", "brg", "lat", "lon")}
                r["also"] = [{key: a.get(key) for key in ("name", "kind", "what", "lat", "lon")} for a in e.get("also", [])]
                for kind, key in (("views", "view"), ("aerial", "aerial")):
                    if kind not in pics.get(sd, {}):
                        continue
                    im, cols, w, h, ovs = pics[sd][kind]
                    ov = ovs[k] if k < len(ovs) else None
                    if not ov or ov.get("error"):
                        continue
                    t = ov["tile"]
                    buf = io.BytesIO()
                    im.crop(((t % cols) * w, (t // cols) * h, (t % cols + 1) * w, (t // cols + 1) * h)).save(
                        buf, "JPEG", quality=86, optimize=True)
                    name = f"{kind}/{sd}/{k:03d}.jpg"
                    z.writestr(zipfile.ZipInfo(name), buf.getvalue(), zipfile.ZIP_STORED)
                    r[key] = dict({a: b for a, b in ov.items() if a not in ("tile", "error", "skip")}, file=name)
                    n_pic += 1
                rows.append({a: b for a, b in r.items() if b is not None})
            timeline[sd] = rows
        log(f"  pack: {sum(len(v) for v in timeline.values())} headline sightings, {n_pic} pictures"
            + (" at twice the size" if detail == "high" else "")
            + ("" if pics else " (no built page for this flight: run `planeview build` first for pictures)"))

        # ---- offline map
        map_info = None
        if with_map:
            need = set()
            la9, lo9 = _grid(reach.buffer(20), proj, 3.0)
            for zz in range(zoom[0], zoom[1]):
                need |= {(zz, x, y) for x, y in _tiles(la9, lo9, zz)}
            laT, loT = _grid(flown.buffer(35 - FLOWN_KM), proj, 3.0)
            need |= {(zoom[1], x, y) for x, y in _tiles(laT, loT, zoom[1])}
            low = [LineString(np.c_[proj.fwd(s[:, 1], s[:, 2])]) for tr in variants
                   for s in _runs(tr, tr[:, 3] < LOW_ZOOM_FT) if len(s) > 1]
            if low:
                laL, loL = _grid(unary_union([ln.buffer(LOW_ZOOM_KM, quad_segs=3) for ln in low]), proj, 2.0)
                need |= {(zoom[1] + 1, x, y) for x, y in _tiles(laL, loL, zoom[1] + 1)}
            with ThreadPoolExecutor(4) as ex:
                got = list(ex.map(_tile_file, sorted(need)))
            n_t, size = 0, 0
            for (zz, x, y), p in got:
                if p is None:
                    continue
                b = p.read_bytes()
                z.writestr(zipfile.ZipInfo(f"tiles/{zz}/{x}/{y}.jpg"), b, zipfile.ZIP_STORED)
                n_t += 1
                size += len(b)
            lat_all = np.concatenate([np.asarray(r)[:, 0] for r in corridor["reach"]])
            lon_all = np.concatenate([np.asarray(r)[:, 1] for r in corridor["reach"]])
            map_info = dict(min_z=zoom[0], max_z=zoom[1] + (1 if low else 0), attribution=aerial.ATTRIBUTION, tiles=n_t,
                            bounds=[round(float(lat_all.min()), 3), round(float(lon_all.min()), 3),
                                    round(float(lat_all.max()), 3), round(float(lon_all.max()), 3)])
            log(f"  pack: {n_t} of {len(need)} map tiles ({size / 1e6:.1f} MB), zoom {map_info['min_z']}-{map_info['max_z']}")

        # ---- terrain around the low parts of the flight
        from . import views
        cells = set()
        for tr in tracks:
            lowp = tr[tr[:, 3] < DEM_ALT_FT][::2]
            for la, lo in lowp[:, 1:3]:
                dla = DEM_KM / 111.0
                dlo = DEM_KM / (111.0 * max(0.2, math.cos(math.radians(la))))
                for a in range(math.floor(la - dla), math.floor(la + dla) + 1):
                    for b in range(math.floor(lo - dlo), math.floor(lo + dlo) + 1):
                        cells.add((a, b))
        names, n_dem = [], None
        for a, b in sorted(cells):
            try:
                t = views.dem_lo(a, b)
            except Exception:
                t = None
            if t is None or t.shape[0] != t.shape[1] or (n_dem is not None and t.shape[0] != n_dem):
                continue
            n_dem = t.shape[0]
            name = f"{'N' if a >= 0 else 'S'}{abs(a):02d}{'E' if b >= 0 else 'W'}{abs(b):03d}"
            z.writestr(zipfile.ZipInfo(f"dem/{name}.i16"), np.clip(np.rint(t), -32768, 32767).astype("<i2").tobytes(),
                       zipfile.ZIP_DEFLATED)
            names.append(name)
        log(f"  pack: {len(names)} terrain tiles of {len(cells)} cells (the rest are sea)")

        # ---- coarse relief for the whole corridor (~1.4 km cells), for the app's 3D view: the shape of the land out
        # to where the window can see. Land cells the forecaster never needed are fetched now (4 MB each from the
        # Copernicus bucket, 0.25 MB kept); open sea is not asked for.
        rla, rlo = _grid(reach.buffer(RELIEF_KM), proj, 25.0)
        rcells = sorted({(math.floor(a), math.floor(b)) for a, b in zip(rla, rlo)})
        rnames, fetched, lacking = [], 0, 0
        for a, b in rcells:
            name = f"{'N' if a >= 0 else 'S'}{abs(a):02d}{'E' if b >= 0 else 'W'}{abs(b):03d}"
            f = cache.CACHE / "dem_lo" / f"{name}.npy"
            t = None
            if f.exists():
                t = np.load(f)
            elif G._cell_has_land(a, b):
                if fetch_relief:
                    try:
                        t = views.dem_lo(a, b)          # None when there is no tile after all (logged as missing)
                        fetched += t is not None
                    except Exception:
                        t = None
                        lacking += 1
                else:
                    lacking += 1
            if t is None or t.ndim != 2 or t.shape[0] != t.shape[1] or t.shape[0] % relief_n:
                continue
            k = t.shape[0] // relief_n
            lo_ = np.maximum(t, 0).reshape(relief_n, k, relief_n, k).mean(axis=(1, 3))
            z.writestr(zipfile.ZipInfo(f"relief/{name}.i16"), np.clip(np.rint(lo_), 0, 32767).astype("<i2").tobytes(),
                       zipfile.ZIP_DEFLATED)
            rnames.append(name)
        log(f"  pack: {len(rnames)} {'fine' if detail == 'high' else 'coarse'} relief tiles of {len(rcells)} cells within {RELIEF_KM:.0f} km of the corridor"
            + (f", {fetched} fetched now" if fetched else "")
            + (f"; {lacking} land cells without terrain" + ("" if fetch_relief else " (not fetched: --relief cached)") if lacking else ""))

        T0 = dt.datetime.fromisoformat(fl["takeoff_utc"]).timestamp()
        manifest = dict(
            schema=SCHEMA, id=fid, generated=dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            flight=dict(fl, takeoff_unix=T0),
            view=dict(V.VIEW, los_alt_m=V.LOS_ALT_M, los_step_km=V.LOS_STEP_KM, los_margin_m=V.LOS_MARGIN_M,
                      up_max_deg=V.UP_MAX_DEG),
            paths=paths, corridor=corridor, context=context, timeline=timeline, map=map_info,
            dem=dict(n=n_dem, tiles=names) if names else None,
            relief=dict(n=relief_n, tiles=rnames) if rnames else None, detail=detail,
            sources=dict({k: str(v) for k, v in pred.get("sources", {}).items() if isinstance(v, str)},
                         terrain=views.CREDIT, imagery=aerial.ATTRIBUTION + ", CC BY-NC-SA 4.0"),
            counts=dict(features=len(frecs), paths=len(paths), legs=len(history)))
        z.writestr(zipfile.ZipInfo("pack.json"), json.dumps(manifest, separators=(",", ":"), default=str), zipfile.ZIP_DEFLATED)
        z.writestr(zipfile.ZipInfo("features.json"), json.dumps(dict(features=frecs), separators=(",", ":"), ensure_ascii=False),
                   zipfile.ZIP_DEFLATED)
    out_path.with_suffix(".tmp").replace(out_path)
    log(f"  pack: {out_path} ({out_path.stat().st_size / 1e6:.1f} MB)")
    return out_path


def _track(tr):
    return [[round(float(p[0]), 2), round(float(p[1]), 4), round(float(p[2]), 4), int(p[3])] for p in tr]


def _runs(tr, mask):
    """The stretches of a track where mask holds."""
    out, cur = [], []
    for p, m in zip(tr, mask):
        if m:
            cur.append(p)
        elif cur:
            out.append(np.array(cur)); cur = []
    if cur:
        out.append(np.array(cur))
    return out


def to_icloud(path: Path, log=print) -> Path | None:
    """Copy a pack to iCloud Drive/PlaneView, where the phone's Files app (and the app's Import) will find it."""
    import shutil
    if not ICLOUD.parent.exists():
        log("  pack: iCloud Drive is not set up on this Mac")
        return None
    ICLOUD.mkdir(exist_ok=True)
    dst = ICLOUD / path.name
    shutil.copy2(path, dst)
    log(f"  pack: copied to iCloud Drive/PlaneView/{path.name}")
    return dst

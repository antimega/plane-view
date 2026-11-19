"""What the window will show: a rendered view for each sighting.

From the aircraft's position, height and the sun at the predicted moment, every pixel of a window-shaped view is traced
to the ground: the first terrain the ray meets on the Copernicus DEM (so ridges hide what lies behind them and the
skyline is the real one), or the sea surface (with Earth curvature and refraction). The ground is then coloured from
the satellite picture of that spot - EOxCloudless, the Sentinel-2 cloudless mosaic the overhead images use, at a zoom
matched to how far away the pixel is - shaded by the slope, with snow above the season's snowline and a little haze
with distance. It is the same construction as a tilted Google Earth view. Where no imagery can be had the old flat
colours stand in (sea, land, lakes, towns from Natural Earth); at night only lit towns glow. Over that go *outlines*
- coastline, lake shores, rivers, borders, at their real heights - and labels for the sighting and its neighbours,
as vectors so they stay sharp.

  render(event, feats_near, sun, month) -> (RGB uint8 image, overlay dict with lines and labels in 0-100 % coordinates)
"""
from __future__ import annotations

import math
import os
from functools import lru_cache

import numpy as np
import shapely
from PIL import Image
from scipy.ndimage import map_coordinates

from . import cache, geo, ne
from .gazetteer import _land_parts, _urban

# PLANEVIEW_PICTURE_SCALE=2 renders at twice the size (four times the work): the iPhone app's high-detail packs.
# It is read here, at import, so that the worker processes of render_all see the same size.
SCALE = max(1, int(os.environ.get("PLANEVIEW_PICTURE_SCALE", "1")))
CREDIT = ("Copernicus DEM GLO-90 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018, provided under "
          "COPERNICUS by the European Union and ESA")
W, H = 320 * SCALE, 400 * SCALE   # tile, portrait like a window
HFOV, VFOV = 56.0, 70.0       # degrees: what fills the window when you lean close to it
TEX_ZOOM = (6, 14)            # imagery zoom range for the ground texture (14 is about 6 m a pixel at 50 N: only
                              # the ground within 2-3 km on an approach asks for it; from cruise height it is 10-11)
FETCH_ALT_M = 7000.0          # terrain tiles are fetched for mountains and below this height; elsewhere cached ones


# ------------------------------------------------------------------ terrain
@lru_cache(maxsize=512)
def dem_lo(lat0: int, lon0: int):
    """Copernicus GLO-90 tile downsampled 4x (~360 m), cached as .npy. None for sea / unavailable."""
    from .gazetteer import geo as _g  # noqa: F401  (keeps import order simple)
    ns = f"{'N' if lat0 >= 0 else 'S'}{abs(lat0):02d}"
    ew = f"{'E' if lon0 >= 0 else 'W'}{abs(lon0):03d}"
    name = ns + ew
    p = cache.CACHE / "dem_lo" / f"{name}.npy"
    if p.exists():
        a = np.load(p)
        return None if a.size == 1 else a
    miss = cache.CACHE / "dem_lo" / "missing.txt"
    if miss.exists() and name in miss.read_text().split():
        return None
    src = cache.find(f"dem/{name}.tif")
    downloaded = None
    if src is None:
        # the full tile (~4 MB) is only needed long enough to downsample it: 16x less to keep
        cop = f"Copernicus_DSM_COG_30_{ns}_00_{ew}_00_DEM"
        dst = cache.CACHE / "dem_lo" / f"{name}.tif.tmp"
        code = cache.curl(f"https://copernicus-dem-90m.s3.amazonaws.com/{cop}/{cop}.tif", dst, timeout=120)
        if code == 200:
            cache.record(cache.CACHE / "dem_lo" / "fetched.json", name)
            src = downloaded = dst
        else:
            if code in (403, 404):  # no tile: sea
                miss.parent.mkdir(parents=True, exist_ok=True)
                with open(miss, "a") as fh:
                    fh.write(name + "\n")
            return None
    import tifffile
    a = tifffile.imread(src).astype(np.float32)
    if downloaded:
        downloaded.unlink(missing_ok=True)
    if a.shape[1] != a.shape[0]:
        x = np.linspace(0, a.shape[1] - 1, a.shape[0])
        a = np.stack([np.interp(x, np.arange(a.shape[1]), row) for row in a])
    lo = a[::4, ::4].copy()
    p.parent.mkdir(parents=True, exist_ok=True)
    np.save(p, lo)
    return lo


def dem_sample(lat, lon, allow_fetch=True):  # noqa: D401
    """Heights (m) at points; 0 where no tile (sea)."""
    lat, lon = np.asarray(lat, float), np.asarray(lon, float)
    z = np.zeros(lat.shape, np.float32)
    la0 = np.floor(lat).astype(int)
    lo0 = np.floor(lon).astype(int)
    keys = set(zip(la0.ravel().tolist(), lo0.ravel().tolist()))
    for (a, b) in keys:
        name = f"{'N' if a >= 0 else 'S'}{abs(a):02d}{'E' if b >= 0 else 'W'}{abs(b):03d}"
        t = dem_lo(a, b) if allow_fetch or cache.find(f"dem_lo/{name}.npy") or cache.find(f"dem/{name}.tif") else None
        if t is None:
            continue
        m = (la0 == a) & (lo0 == b)
        n = t.shape[0]
        r = (a + 1 - lat[m]) * n
        c = (lon[m] - b) * n
        z[m] = map_coordinates(t, [np.clip(r, 0, n - 1), np.clip(c, 0, n - 1)], order=1, mode="nearest")
    return z


# ------------------------------------------------------------------ land, water, towns
def classify(lat, lon):
    """0 sea, 1 land, 2 lake, 3 town for ground points."""
    lat, lon = np.asarray(lat, float), np.asarray(lon, float)
    out = np.zeros(lat.shape, np.uint8)
    bb = shapely.box(float(np.min(lon)) - 0.05, float(np.min(lat)) - 0.05, float(np.max(lon)) + 0.05,
                     float(np.max(lat)) + 0.05)
    tree, parts = _land_parts()
    for i in tree.query(bb, predicate="intersects"):
        poly = parts[int(i)][0]
        shapely.prepare(poly)
        out[shapely.contains_xy(poly, lon, lat)] = 1
    lt, lpolys = _lakes()
    for i in lt.query(bb, predicate="intersects"):
        poly = lpolys[int(i)]
        shapely.prepare(poly)
        out[shapely.contains_xy(poly, lon, lat)] = 2
    ut, _, upolys = _urban()
    for i in ut.query(bb, predicate="intersects"):
        poly = upolys[int(i)]
        shapely.prepare(poly)
        out[(out == 1) & shapely.contains_xy(poly, lon, lat)] = 3
    return out


@lru_cache(maxsize=1)
def _lakes():
    polys = [g for name in ne.LAKE_LAYERS for rec, g in ne.layer(name) if g is not None]
    return shapely.STRtree(polys), polys


@lru_cache(maxsize=1)
def _relief():
    import tifffile
    return tifffile.imread(ne.relief_tif())


def relief_at(lat, lon):
    R = _relief()
    rows = np.clip(((90 - np.asarray(lat)) / 180 * R.shape[0]).astype(int), 0, R.shape[0] - 1)
    cols = np.clip(((np.asarray(lon) + 180) / 360 * R.shape[1]).astype(int), 0, R.shape[1] - 1)
    return R[rows, cols].astype(np.float32) / 255


# ------------------------------------------------------------------ rays
def view_frame(ev):
    """Look direction: centred on the sighting, tilted so the horizon stays in the window when it can."""
    h = ev["alt_ft"] * geo.FT
    dip = float(geo.dip_deg(h))
    el_c = -(0.6 * ev["dep"] + 0.4 * dip) - 8
    return ev["brg"], el_c, h, dip


def rays(az_c, el_c, w, h):
    u = (np.arange(w) + 0.5) / w - 0.5
    v = (np.arange(h) + 0.5) / h - 0.5
    U, V = np.meshgrid(u, v)
    return az_c + U * HFOV, el_c - V * VFOV


def ground_flat(h_m, dep):
    """Ground distance (km) where a ray dep degrees below horizontal meets the sea surface; nan above the horizon."""
    t = np.tan(np.radians(dep))
    disc = t * t - 2 * h_m / geo.R_EFF
    d = geo.R_EFF * (t - np.sqrt(np.where(disc >= 0, disc, np.nan)))
    return np.where((dep > 0) & (disc >= 0), d / 1000, np.nan)


def ground_terrain(lat, lon, h_m, AZ, EL, d_max=260.0, allow_fetch=True):
    """First terrain hit along each ray (km) by marching over the DEM, then closing in on it by halving the last
    step (far steps are kilometres long: without this, distant slopes come out as stairs); nan for sky.
    Returns (d, z)."""
    shape = AZ.shape
    az, el = AZ.ravel(), EL.ravel()
    d_flat = ground_flat(h_m, -el)
    ds = [0.3]
    while ds[-1] < d_max:
        ds.append(ds[-1] * 1.035 + 0.25)
    ds = np.array(ds)
    hit_d = np.full(az.shape, np.nan)
    hit_z = np.zeros(az.shape)
    t = np.tan(np.radians(-el))
    below = lambda idx, d: h_m - d * 1000 * t[idx] - (d * 1000) ** 2 / (2 * geo.R_EFF)   # ray height at range d
    for k, d in enumerate(ds):
        live = np.isnan(hit_d)
        if not live.any():
            break
        idx = np.flatnonzero(live)
        la, lo = geo.destination(lat, lon, az[idx], d)
        zt = dem_sample(la, lo, allow_fetch)
        zr = below(idx, d)
        hit = (zr <= zt) | (zr <= 0)      # ground, or sea level reached first (no tile, or the ray dips below 0 m)
        hidx = idx[hit]
        if not len(hidx):
            continue
        a = np.full(len(hidx), ds[k - 1] if k else 0.0)
        b = np.full(len(hidx), d)
        zb = np.maximum(zt[hit], 0)
        for _ in range(5):
            m = (a + b) / 2
            la, lo = geo.destination(lat, lon, az[hidx], m)
            zm = np.maximum(dem_sample(la, lo, allow_fetch), 0)
            under = below(hidx, m) <= zm
            b = np.where(under, m, b); zb = np.where(under, zm, zb)
            a = np.where(under, a, m)
        hit_d[hidx] = b
        hit_z[hidx] = zb
    sky = np.isnan(hit_d) & np.isnan(d_flat)
    hit_d = np.where(np.isnan(hit_d) & ~sky, d_flat, hit_d)
    return hit_d.reshape(shape), hit_z.reshape(shape)


def mountainous(ev, feats_near) -> bool:
    return any(f["kind"] in ("peak", "range") and (f.get("elev") or 2000) >= 1200 for f in feats_near) or \
        ev.get("kind") == "peak"


# ------------------------------------------------------------------ render
SKY_TOP, SKY_HOR = np.array([118, 160, 204]), np.array([205, 222, 236])
SEA, LAND, LAKE, TOWN = np.array([84, 122, 152]), np.array([164, 160, 120]), np.array([92, 132, 160]), np.array([150, 142, 134])
HAZE = np.array([206, 218, 228])
SNOW = np.array([238, 240, 244])


def snowline_m(month, lat):
    base = {12: 1500, 1: 1300, 2: 1300, 3: 1600, 4: 2000, 5: 2500, 6: 2900, 7: 3200, 8: 3300, 9: 3100, 10: 2600,
            11: 2100}[month]
    return base + (45 - abs(lat)) * 60


@lru_cache(maxsize=160)
def _tex_tile(z, x, y):
    """One imagery tile as an array (None when it cannot be had); shared with the overhead images' cache."""
    from . import aerial
    im = aerial._tile(z, x, y)
    return None if im is None else np.asarray(im, np.float32)


def texture(la, lo, d_km):
    """Satellite colour (0-255, nan where missing) of ground points la, lo seen from d_km away. The zoom follows
    the distance, as a map on a screen does: about one imagery pixel to one view pixel, within TEX_ZOOM."""
    la, lo, d_km = (np.asarray(a, float) for a in (la, lo, d_km))
    out = np.full((len(la), 3), np.nan)
    if not len(la):
        return out
    foot = np.maximum(d_km, 0.3) * 1000 * math.radians(HFOV / W) * 1.3            # metres across one view pixel
    z = np.clip(np.floor(np.log2(156543.03 * np.cos(np.radians(la)) / foot)), *TEX_ZOOM).astype(int)
    lam = np.radians(np.clip(la, -85, 85))
    yy = (1 - np.log(np.tan(lam) + 1 / np.cos(lam)) / math.pi) / 2
    xx = ((lo + 180) / 360) % 1
    for zz in np.unique(z):
        m = np.flatnonzero(z == zz)
        n = 2 ** int(zz) * 256
        px, py = xx[m] * n, yy[m] * n
        tx, ty = (px // 256).astype(np.int64), (py // 256).astype(np.int64)
        key = tx * (1 << 20) + ty
        for k in np.unique(key):
            sel = key == k
            t = _tex_tile(int(zz), int(tx[sel][0]), int(ty[sel][0]))
            if t is None:
                continue
            # between the four nearest imagery pixels (within the tile), so close ground is smooth, not blocks
            fx, fy = np.clip(px[sel] % 256 - 0.5, 0, 255), np.clip(py[sel] % 256 - 0.5, 0, 255)
            x0, y0 = np.minimum(fx.astype(int), 254), np.minimum(fy.astype(int), 254)
            ax, ay = (fx - x0)[:, None], (fy - y0)[:, None]
            out[m[sel]] = (t[y0, x0] * (1 - ax) + t[y0, x0 + 1] * ax) * (1 - ay) + \
                          (t[y0 + 1, x0] * (1 - ax) + t[y0 + 1, x0 + 1] * ax) * ay
    return out


def render(ev, feats_near, sun_az, sun_el, month):
    """RGB image (H, W, 3) and the overlay (lines, labels) for one sighting."""
    az_c, el_c, h_m, dip = view_frame(ev)
    lat, lon = ev["ac"][0], ev["ac"][1]
    fetch = mountainous(ev, feats_near) or h_m < FETCH_ALT_M
    AZ, EL = rays(az_c, el_c, W, H)
    d, zg = ground_terrain(lat, lon, h_m, AZ, EL, allow_fetch=fetch)
    sky = np.isnan(d)
    g = ~sky
    img = np.zeros((H, W, 3), float)
    # sky gradient
    tsky = np.clip((EL - (-dip)) / 25, 0, 1)[..., None]
    img[:] = SKY_HOR * (1 - tsky) + SKY_TOP * tsky
    cgrid = np.full(d.shape, 255, np.uint8)
    dark = sun_el < -6
    if g.any():
        la, lo = geo.destination(lat, lon, AZ[g], d[g])
        z0 = zg[g]
        col = np.full((int(g.sum()), 3), np.nan) if dark else texture(la, lo, d[g]) * 1.12
        miss = np.isnan(col[:, 0])
        cls = None
        if miss.any() or dark:
            # no imagery (or night, when only the towns show): flat colours by what the ground is
            cls = classify(la, lo)
            cgrid[g] = cls
            flat = np.zeros((len(cls), 3))
            flat[cls == 0] = SEA
            flat[cls == 2] = LAKE
            land = (cls == 1) | (cls == 3)
            flat[land] = LAND * np.clip(0.55 + 0.6 * relief_at(la[land], lo[land]), 0, 1.15)[:, None]
            flat[cls == 3] = flat[cls == 3] * 0.35 + TOWN * 0.65
            col[miss] = flat[miss]
        # the lie of the land: slope shading from the DEM, and snow above the season's snowline
        up = z0 > 1.0
        if up.any():
            e = 0.004
            zx = dem_sample(la[up], lo[up] + e, False) - dem_sample(la[up], lo[up] - e, False)
            zy = dem_sample(la[up] + e, lo[up], False) - dem_sample(la[up] - e, lo[up], False)
            dx = 2 * e * 111320 * np.cos(np.radians(la[up])); dy = 2 * e * 110540
            nx, ny = -zx / dx, -zy / dy
            sa, se = math.radians(sun_az), math.radians(max(sun_el, 25))
            sun = np.array([math.sin(sa) * math.cos(se), math.cos(sa) * math.cos(se), math.sin(se)])
            lam = np.clip((nx * sun[0] + ny * sun[1] + sun[2]) / np.sqrt(nx * nx + ny * ny + 1), 0, 1)
            rel = np.clip(lam / math.sin(se), 0.35, 1.5) ** 0.7          # 1 on level ground
            shaded = col[up] * rel[:, None]
            # snow lies on open ground; under trees it does not show from above, so dark (wooded) ground keeps
            # most of its colour - which is what gives winter hillsides their piebald look
            open_ = np.clip((col[up].mean(1) - 45) / 55, 0.2, 1)
            snow = (np.clip((z0[up] - snowline_m(month, lat)) / 400, 0, 1) * 0.9 * open_)[:, None]
            col[up] = shaded * (1 - snow) + SNOW * (0.45 + 0.6 * lam[:, None]) * snow
        # haze: thicker low down, and lighter than the eye would have it - the picture is for finding things
        vis = 70 + 110 * min(1, max(0, (h_m - 1500) / 6000))
        f = (1 - np.exp(-d[g] / vis))[:, None] * 0.7
        col = col * (1 - f) + HAZE * f
        img[g] = col
    # light
    if dark:
        img = night(img, cgrid, d, h_m)
    elif sun_el < 2:
        k = 0.45 + 0.55 * (sun_el + 6) / 8
        img = img * k + np.array([60, 40, 70]) * (1 - k) * 0.4
    im = np.clip(img, 0, 255).astype(np.uint8)
    overlay = outlines(ev, az_c, el_c, h_m, dip, d, True)
    overlay["labels"] = labels(ev, feats_near, az_c, el_c, h_m, d)
    overlay["horizon"] = round(50 - ((-dip) - el_c) / VFOV * 100, 1)
    return im, overlay


def night(img, cgrid, d, h_m):
    """Dark ground and sea, a faint sky, and the towns lit (sodium-orange, fading with distance)."""
    out = img * 0.07
    out[cgrid == 255] = np.array([14, 18, 34])
    town = cgrid == 3
    if town.any():
        f = np.exp(-np.nan_to_num(d, nan=999)[town] / 160)[:, None]
        rng = np.random.default_rng(int(h_m))
        sparkle = 0.6 + 0.4 * rng.random((int(town.sum()), 1))
        out[town] = np.array([255, 196, 110]) * (0.35 + 0.65 * f) * sparkle
    return out


def _proj(az_c, el_c, az, dep):
    x = 50 + geo.wrap180(az - az_c) / HFOV * 100
    y = 50 - (-dep - el_c) / VFOV * 100
    return x, y


def outlines(ev, az_c, el_c, h_m, dip, dgrid, terrain):
    """Coastline, lake shores, rivers and borders projected into the window (lines in % coordinates)."""
    lat, lon = ev["ac"][0], ev["ac"][1]
    r_km = min(260.0, float(geo.horizon_km(h_m)) + 30)
    dlat = r_km / 111.0
    dlon = r_km / (111.0 * max(0.2, math.cos(math.radians(lat))))
    bb = shapely.box(lon - dlon, lat - dlat, lon + dlon, lat + dlat)
    out = {}
    layers = [("coast", "ne_10m_coastline", 1.5)] + [("lakes", n, 1.0) for n in ne.LAKE_LAYERS] + \
        [("rivers", n, 2.0) for n in ne.RIVER_LAYERS] + [("borders", "ne_10m_admin_0_boundary_lines_land", 2.0)]
    for key, layer, step in layers:
        try:
            tree, geoms = _layer_tree(layer)
        except Exception:
            continue
        lines = []
        for i in tree.query(bb, predicate="intersects"):
            gm = geoms[int(i)].intersection(bb)
            for ln in (gm.geoms if hasattr(gm, "geoms") else [gm]):
                if ln.is_empty:
                    continue
                if ln.geom_type == "Polygon":
                    ln = ln.exterior
                if ln.geom_type not in ("LineString", "LinearRing"):
                    continue
                c = np.asarray(ln.coords)
                if len(c) < 2:
                    continue
                c = _densify(c, step)
                dk = geo.dist_km(lat, lon, c[:, 1], c[:, 0])
                brg = geo.bearing(lat, lon, c[:, 1], c[:, 0])
                # a lake shore or river in the hills is drawn at its own height, not at sea level
                zc = np.zeros(len(c)) if key == "coast" else dem_sample(c[:, 1], c[:, 0], False)
                dep = geo.depression_deg(h_m, zc, dk)
                x, y = _proj(az_c, el_c, brg, dep)
                vis = (x >= -2) & (x <= 102) & (y >= -2) & (y <= 102) & \
                    (dk <= geo.horizon_km(h_m) + geo.horizon_km(zc)) & ((dep >= dip) | (zc > 0))
                if terrain:
                    xi = np.clip((x / 100 * W).astype(int), 0, W - 1)
                    yi = np.clip((y / 100 * H).astype(int), 0, H - 1)
                    hit = dgrid[yi, xi]
                    vis &= ~(np.isfinite(hit) & (hit < dk * 0.97 - 1.0))
                seg = []
                for k in range(len(x)):
                    if vis[k]:
                        seg.append([round(float(x[k]), 1), round(float(y[k]), 1)])
                    elif len(seg) > 1:
                        lines.append(seg); seg = []
                    else:
                        seg = []
                if len(seg) > 1:
                    lines.append(seg)
        if lines:
            out.setdefault(key, []).extend(_thin(lines))
    return out


def _densify(c, step_km):
    out = [c[0]]
    for a, b in zip(c[:-1], c[1:]):
        dk = float(geo.dist_km(a[1], a[0], b[1], b[0]))
        n = int(dk / step_km)
        for k in range(1, n + 1):
            f = k / (n + 1)
            out.append(a + (b - a) * f)
        out.append(b)
    return np.array(out)


def _thin(lines, tol=0.8):
    res = []
    for ln in lines:
        keep = [ln[0]]
        for p in ln[1:-1]:
            if abs(p[0] - keep[-1][0]) + abs(p[1] - keep[-1][1]) >= tol:
                keep.append(p)
        keep.append(ln[-1])
        if len(keep) > 1:
            res.append(keep)
    return res


@lru_cache(maxsize=None)
def _layer_tree(name):
    geoms = [g for rec, g in ne.layer(name) if g is not None]
    return shapely.STRtree(geoms), geoms


def labels(ev, feats_near, az_c, el_c, h_m, dgrid, max_n=5):
    """The sighting and up to five neighbours that fall in the window and are not behind a ridge."""
    lat, lon = ev["ac"][0], ev["ac"][1]
    out = []
    cand = [dict(name=ev["name"], lat=ev["lat"], lon=ev["lon"], z=(ev.get("elev") or 0) if ev["kind"] == "peak" else 0,
                 kind=ev["kind"], main=True, s=1e9)]
    for f in feats_near:
        cand.append(dict(name=f["name"], lat=f["lat"], lon=f["lon"], z=f.get("z", 0), kind=f["kind"], main=False,
                         s=f.get("_s", 0)))
    cand.sort(key=lambda c: -c["s"])
    seen = set()
    for c in cand:
        key = c["name"].lower()
        if key in seen:
            continue
        dk = float(geo.dist_km(lat, lon, c["lat"], c["lon"]))
        brg = float(geo.bearing(lat, lon, c["lat"], c["lon"]))
        zc = max(float(c["z"] or 0), float(dem_sample(np.array([c["lat"]]), np.array([c["lon"]]), False)[0]))
        dep = float(geo.depression_deg(h_m, zc, dk))
        x, y = _proj(az_c, el_c, brg, dep)
        x, y = float(x), float(y)
        if not (3 <= x <= 97 and 4 <= y <= 97):
            continue
        xi, yi = int(x / 100 * (W - 1)), int(y / 100 * (H - 1))
        hit = dgrid[yi, xi]
        if not c["main"] and np.isfinite(hit) and hit < dk * 0.95 - 1.5:
            continue  # behind terrain
        if not c["main"] and (y < 14 or any(abs(o["y"] - y) < 9 and abs(o["x"] - x) < 45 for o in out)):
            continue  # keep clear of the NOSE tag and of other labels
        out.append(dict(n=c["name"], x=round(x, 1), y=round(y, 1), main=c["main"], k=c["kind"]))
        seen.add(key)
        if len(out) >= max_n:
            break
    return out


# ------------------------------------------------------------------ all sightings of a prediction
def _one(args):
    ev, near, sun_az, sun_el, month = args
    try:
        im, ov = render(ev, near, sun_az, sun_el, month)
        return im, ov
    except Exception as e:  # a view is a nice-to-have: never fail the prediction over one
        return None, dict(error=str(e)[:200])


def render_all(pred, out_dir, workers=6, log=print):
    """Render every timeline sighting; write views-<side>.jpg sprites; return {side: [overlay with tile index]}."""
    import datetime as dt
    from concurrent.futures import ProcessPoolExecutor
    from scipy.spatial import cKDTree
    corr = [dict(f, _s=f.get("salience", 0)) for f in pred.get("corridor", [])
            if f["kind"] in ("settlement", "peak", "lake", "island", "coast", "landmark", "range")]
    tree = cKDTree(geo.xyz([f["lat"] for f in corr], [f["lon"] for f in corr])) if corr else None
    T0 = dt.datetime.fromisoformat(pred["flight"]["takeoff_utc"]).timestamp()
    month = int(pred["flight"]["date"][5:7])
    res = {}
    for sd, evs in pred["timeline"].items():
        jobs = []
        for e in evs:
            near = []
            if tree is not None:
                r = min(260.0, float(geo.horizon_km(e["alt_ft"] * geo.FT)) + 20)
                for j in tree.query_ball_point(geo.xyz(e["ac"][0], e["ac"][1]), 2 * math.sin(r / geo.R_KM / 2)):
                    near.append(corr[j])
            t = T0 + e.get("t_best", e["t"]) * 60
            saz, sel = geo.sun_az_el(t, e["ac"][0], e["ac"][1])
            jobs.append((e, near, float(saz), float(sel), month))
        with ProcessPoolExecutor(workers) as ex:
            out = list(ex.map(_one, jobs, chunksize=2))
        cols = 10
        rows = max(1, math.ceil(len(out) / cols))
        sheet = Image.new("RGB", (cols * W, rows * H), (200, 210, 220))
        ovs = []
        for k, (im, ov) in enumerate(out):
            if im is not None:
                sheet.paste(Image.fromarray(im), ((k % cols) * W, (k // cols) * H))
            ov["tile"] = k
            ovs.append(ov)
        sheet.save(out_dir / f"views-{sd}.jpg", quality=78, optimize=True, progressive=True)
        res[sd] = dict(cols=cols, rows=rows, w=W, h=H, overlays=ovs)
        log(f"  views: {len(out)} rendered for the {'left' if sd == 'L' else 'right'} side")
    return res

"""Basemap for a route: relief image + vectors in a route-centred azimuthal equidistant projection (km).

The page must work offline and from a plain file, so it loads no map tiles: the map is Natural Earth's shaded relief
(SR_HR, 1 arc-minute) resampled into the projection and tinted land/sea, plus Natural Earth vectors. The projection is
centred on the route, so long and polar routes keep their shape; the page uses Leaflet's CRS.Simple with these km
coordinates.
"""
from __future__ import annotations

import math
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from shapely.geometry import box

from .. import geo, ne
from ..gazetteer import _land_parts


class AEQD:
    """Azimuthal equidistant projection about (lat0, lon0); x east, y north, in km. Rotated so the route runs
    left -> right when `rot` (deg) is given."""

    def __init__(self, lat0, lon0, rot=0.0):
        self.lat0, self.lon0, self.rot = lat0, lon0, rot
        self.c, self.s = math.cos(math.radians(rot)), math.sin(math.radians(rot))

    def fwd(self, lat, lon):
        lat, lon = np.asarray(lat, float), np.asarray(lon, float)
        d = geo.dist_km(self.lat0, self.lon0, lat, lon)
        b = np.radians(geo.bearing(self.lat0, self.lon0, lat, lon))
        x, y = d * np.sin(b), d * np.cos(b)
        return x * self.c - y * self.s, x * self.s + y * self.c

    def inv(self, x, y):
        x, y = np.asarray(x, float), np.asarray(y, float)
        xr, yr = x * self.c + y * self.s, -x * self.s + y * self.c
        d = np.hypot(xr, yr)
        b = np.degrees(np.arctan2(xr, yr))
        return geo.destination(self.lat0, self.lon0, b, d)


def for_route(lat, lon):
    """Projection centred on the route's middle, north up (a map read upside down on westbound flights is no help;
    the strip chart gives the along-route view)."""
    lat, lon = np.asarray(lat), np.asarray(lon)
    clat, clon = geo.latlon(geo.xyz(lat, lon).mean(0))
    return AEQD(float(clat), float(clon))


@lru_cache(maxsize=1)
def _relief():
    import tifffile
    tif = ne.relief_tif()
    return tifffile.memmap(tif) if hasattr(tifffile, "memmap") else tifffile.imread(tif)


def build(proj: AEQD, bounds, px=1600, dark=False):
    """Relief JPEG (PIL image) and vector layers for bounds = (x0, y0, x1, y1) km."""
    x0, y0, x1, y1 = bounds
    W = px
    H = max(200, int(px * (y1 - y0) / (x1 - x0)))
    xs = np.linspace(x0, x1, W)
    ys = np.linspace(y1, y0, H)
    X, Y = np.meshgrid(xs, ys)
    la, lo = proj.inv(X.ravel(), Y.ravel())
    R = _relief()
    rows = np.clip(((90 - la) / 180 * R.shape[0]).astype(int), 0, R.shape[0] - 1)
    cols = np.clip(((lo + 180) / 360 * R.shape[1]).astype(int), 0, R.shape[1] - 1)
    shade = (np.asarray(R[rows, cols], float) / 255).reshape(H, W)
    # land mask: rasterise land polygons in pixel space
    land = Image.new("L", (W, H), 0)
    dr = ImageDraw.Draw(land)
    tree, parts = _land_parts()
    bl0, bl1 = la.min(), la.max()
    bo0, bo1 = lo.min(), lo.max()
    wrap = bo1 - bo0 > 300
    q = box(-180, bl0 - 1, 180, bl1 + 1) if wrap else box(bo0 - 1, bl0 - 1, bo1 + 1, bl1 + 1)
    for i in tree.query(q, predicate="intersects"):
        clipped = parts[int(i)][0].intersection(q)  # whole continents would wrap round the projection
        for poly in (clipped.geoms if hasattr(clipped, "geoms") else [clipped]):
          if poly.geom_type != "Polygon" or poly.is_empty:
            continue
          for ring_i, ring in enumerate([poly.exterior] + list(poly.interiors)):
              c = np.asarray(ring.coords)
              if len(c) > 6000:
                  c = c[:: len(c) // 6000 + 1]
              px_, py_ = proj.fwd(c[:, 1], c[:, 0])
              pts = list(zip((px_ - x0) / (x1 - x0) * W, (y1 - py_) / (y1 - y0) * H))
              if len(pts) >= 3:
                  dr.polygon(pts, fill=255 if ring_i == 0 else 0)
    m = np.asarray(land.filter(ImageFilter.GaussianBlur(0.7)), float) / 255
    s3 = shade[..., None]
    land_c = np.array([226, 222, 205]) * np.clip(0.35 + 0.85 * s3, 0, 1.15)
    sea_c = np.array([176, 196, 214]) * (0.92 + 0.08 * s3)
    img = m[..., None] * land_c + (1 - m[..., None]) * sea_c
    im = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
    return im, (W, H)


def vectors(proj: AEQD, bounds, tol_km=1.5):
    """Coastlines, borders, lakes, rivers and big-place labels, projected and clipped to the bounds."""
    x0, y0, x1, y1 = bounds
    pad = 0.05 * max(x1 - x0, y1 - y0)
    out = dict(coast=[], borders=[], lakes=[], rivers=[], places=[])

    def proj_lines(g):
        lines = []
        for ln in (g.geoms if hasattr(g, "geoms") else [g]):
            if ln.geom_type == "Polygon":
                ln = ln.exterior
            c = np.asarray(ln.coords)
            if len(c) < 2:
                continue
            x, y = proj.fwd(c[:, 1], c[:, 0])
            inb = (x > x0 - pad) & (x < x1 + pad) & (y > y0 - pad) & (y < y1 + pad)
            if not inb.any():
                continue
            # keep runs inside the bounds; thin to ~tol
            seg = []
            for k in range(len(x)):
                if inb[k]:
                    if not seg or math.hypot(x[k] - seg[-1][0], y[k] - seg[-1][1]) >= tol_km:
                        seg.append((round(float(x[k]), 1), round(float(y[k]), 1)))
                elif len(seg) > 1:
                    lines.append(seg); seg = []
                else:
                    seg = []
            if len(seg) > 1:
                lines.append(seg)
        return lines

    for layer, key in (("ne_10m_coastline", "coast"), ("ne_10m_admin_0_boundary_lines_land", "borders")):
        for _, g in ne.layer(layer):
            if g is not None:
                out[key] += proj_lines(g)
    for rec, g in ne.layer("ne_10m_lakes"):
        if g is not None and int(rec.get("scalerank") or 9) <= 8:
            out["lakes"] += proj_lines(g)
    for rec, g in ne.layer("ne_10m_rivers_lake_centerlines"):
        if g is not None and int(rec.get("scalerank") or 9) <= 7 and rec.get("featurecla") != "Lake Centerline":
            out["rivers"] += proj_lines(g)
    for rec, _ in ne.layer("ne_10m_populated_places_simple"):
        if int(rec["scalerank"]) > 6:
            continue
        x, y = proj.fwd(rec["latitude"], rec["longitude"])
        if x0 < x < x1 and y0 < y < y1:
            out["places"].append(dict(n=rec["name"], x=round(float(x), 1), y=round(float(y), 1),
                                      r=int(rec["scalerank"]), cap=bool(rec.get("adm0cap"))))
    return out

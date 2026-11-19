"""Natural Earth 1:10m layers (public domain, naturalearthdata.com).

Layers are fetched on first use from the NACIS CDN (with the S3 bucket as a mirror) into cache/naturalearth.
"""
from __future__ import annotations

import zipfile
from functools import lru_cache

import shapefile
from shapely.geometry import shape

from . import cache

# the NACIS CDN, then Natural Earth's own S3 bucket (same files)
URLS = ("https://naciscdn.org/naturalearth/10m/{theme}/{name}.zip",
        "https://naturalearth.s3.amazonaws.com/10m_{theme}/{name}.zip")
THEME = {"ne_10m_admin_0_countries": "cultural", "ne_10m_minor_islands": "physical",
         "ne_10m_populated_places_simple": "cultural", "ne_10m_urban_areas": "cultural",
         "ne_10m_admin_0_boundary_lines_land": "cultural", "ne_10m_lakes_europe": "physical",
         "ne_10m_lakes_north_america": "physical", "ne_10m_rivers_north_america": "physical"}

LAKE_LAYERS = ("ne_10m_lakes", "ne_10m_lakes_europe", "ne_10m_lakes_north_america")
RIVER_LAYERS = ("ne_10m_rivers_lake_centerlines", "ne_10m_rivers_europe", "ne_10m_rivers_north_america")


def _dir(name: str):
    p = cache.find(f"naturalearth/{name}")
    if p and any(p.glob("*.shp")):
        return p
    theme = THEME.get(name, "physical")
    z = cache.fetch_any(f"naturalearth/{name}.zip", [u.format(theme=theme, name=name) for u in URLS])
    out = cache.CACHE / "naturalearth" / name
    out.mkdir(parents=True, exist_ok=True)
    zipfile.ZipFile(z).extractall(out)
    return out


def relief_tif():
    """Natural Earth's shaded relief raster (SR_HR, 21,600 x 10,800 grey; a 300 MB download, once)."""
    p = cache.find("naturalearth/SR_HR")
    if not (p and any(p.glob("*.tif"))):
        z = cache.fetch_any("naturalearth/SR_HR.zip", [u.format(theme="raster", name="SR_HR") for u in URLS])
        p = cache.CACHE / "naturalearth" / "SR_HR"
        p.mkdir(parents=True, exist_ok=True)
        zipfile.ZipFile(z).extractall(p)
        z.unlink()   # the zip is as big as the raster inside it
    tifs = sorted(p.rglob("*.tif"))
    if not tifs:
        raise RuntimeError(f"no raster in {p}")
    return tifs[0]


@lru_cache(maxsize=None)
def layer(name: str, geometry: bool = True):
    """[(record dict with lower-case keys, shapely geometry or None)]."""
    shp = next(_dir(name).glob("*.shp"))
    r = shapefile.Reader(str(shp), encoding="utf-8", encodingErrors="replace")
    keys = [f[0].lower() for f in r.fields[1:]]
    out = []
    for sr in r.iterShapeRecords():
        rec = dict(zip(keys, sr.record))
        g = None
        if geometry and sr.shape.shapeType != 0 and sr.shape.points:
            try:
                g = shape(sr.shape.__geo_interface__)
                if not g.is_valid:
                    g = g.buffer(0)
            except Exception:
                g = None
        out.append((rec, g))
    return out


def en(rec: dict) -> str:
    """English display name of a Natural Earth record."""
    for k in ("name_en", "label", "name"):
        v = (rec.get(k) or "").strip()
        if v:
            return v.title() if v.isupper() else v
    return ""

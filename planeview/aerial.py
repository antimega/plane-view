"""Straight-down imagery of each sighting: a crop of EOxCloudless (Sentinel-2 cloudless mosaic, 10 m), sized to the
feature, so a town's shape against its coast or river can be recognised from the window. Each crop is turned the way
you will be looking: the aircraft is below the bottom edge and what lies beyond the place is at the top, so left and
right in the picture are left and right in the window. (The route map stays north-up; a small arrow in each picture
shows where north went.)

Imagery: "EOxCloudless https://cloudless.eox.at by EOX IT Services GmbH (Contains modified Copernicus Sentinel data
2024)", CC BY-NC-SA 4.0 (personal, non-commercial use with attribution). Tiles are Web Mercator JPEGs from the public
WMTS, fetched politely (a few at a time, identified user agent) and cached in cache/s2/ so nothing is fetched twice.

  render_all(pred, out_dir) -> {side: {file, cols, rows, size, overlays: [{tile, km, route, look, labels}]}}
"""
from __future__ import annotations

import math
import os
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image

from . import cache, geo

LAYER = "s2cloudless-2024_3857"
URL = "https://tiles.maps.eox.at/wmts/1.0.0/{layer}/default/g/{z}/{y}/{x}.jpg"
ATTRIBUTION = ("EOxCloudless https://cloudless.eox.at by EOX IT Services GmbH "
               "(Contains modified Copernicus Sentinel data 2024)")
# output square, px (shown at up to ~230 css px); doubled by PLANEVIEW_PICTURE_SCALE=2 for high-detail app packs
S = 320 * max(1, int(os.environ.get("PLANEVIEW_PICTURE_SCALE", "1")))
COMPASS = ("north", "north-north-east", "north-east", "east-north-east", "east", "east-south-east", "south-east",
           "south-south-east", "south", "south-south-west", "south-west", "west-south-west", "west",
           "west-north-west", "north-west", "north-north-west")
SKIP = {"peak", "region", "range"}  # from above these say little about what the window shows


def _merc(lat, lon, z):
    n = 2 ** z * 256
    x = (lon + 180) / 360 * n
    la = math.radians(max(-85.0, min(85.0, lat)))
    y = (1 - math.log(math.tan(la) + 1 / math.cos(la)) / math.pi) / 2 * n
    return x, y


def _tile(z, x, y):
    n = 2 ** z
    x %= n
    if not 0 <= y < n:
        return None
    p = cache.CACHE / "s2" / LAYER / str(z) / str(x) / f"{y}.jpg"
    if not p.exists():
        if cache.curl(URL.format(layer=LAYER, z=z, x=x, y=y), p, timeout=30) != 200:
            return None
    try:
        return Image.open(p).convert("RGB")
    except Exception:
        p.unlink(missing_ok=True)
        return None


def footprint_km(ev) -> float:
    k = ev["kind"]
    size = {"settlement": 0.02 * math.sqrt(max(ev.get("pop") or 500, 500)) + 0.3}.get(k, 3.0)
    if ev.get("area_km2"):
        size = 2 * math.sqrt(ev["area_km2"] / math.pi)
    if k in ("coast", "sea", "river"):
        size = max(size, 8)
    if k == "peak":
        size = 6
    return float(np.clip(1.5 * size + 2.5, 4, 36))


def crop(lat, lon, km, up=0.0):
    """S x S image centred on (lat, lon) covering about km x km with compass bearing `up` at the top (0 = north up),
    and the function that places a (lat, lon) in it, in percent."""
    if up % 360:
        # a north-up crop wide enough to turn inside, turned, and cut back to the middle
        big = math.ceil(S * 1.42 / 2) * 2
        im, to_n = _crop_north(lat, lon, km * big / S, big)
        im = im.rotate(up, resample=Image.BICUBIC)          # PIL turns anticlockwise: bearing `up` comes to the top
        o = (big - S) // 2
        im = im.crop((o, o, o + S, o + S))
        c, s_ = math.cos(math.radians(up)), math.sin(math.radians(up))

        def to_px(la, lo):
            x, y = to_n(la, lo)
            x, y = (x - 50) * big / S, (y - 50) * big / S
            return 50 + x * c + y * s_, 50 - x * s_ + y * c
        return im, to_px
    return _crop_north(lat, lon, km, S)


def _crop_north(lat, lon, km, S):
    mpp = km * 1000 / S
    z = int(np.clip(math.floor(math.log2(156543.03 * math.cos(math.radians(lat)) / mpp)) + 1, 3, 14))
    scale = 156543.03 * math.cos(math.radians(lat)) / 2 ** z / mpp  # source px -> output px
    cx, cy = _merc(lat, lon, z)
    half = S / 2 / scale
    x0, y0, x1, y1 = cx - half, cy - half, cx + half, cy + half
    tx0, ty0, tx1, ty1 = int(x0 // 256), int(y0 // 256), int(x1 // 256), int(y1 // 256)
    mosaic = Image.new("RGB", ((tx1 - tx0 + 1) * 256, (ty1 - ty0 + 1) * 256), (40, 52, 64))
    keys = [(tx, ty) for ty in range(ty0, ty1 + 1) for tx in range(tx0, tx1 + 1)]
    with ThreadPoolExecutor(4) as ex:
        tiles = list(ex.map(lambda k: _tile(z, k[0], k[1]), keys))
    for (tx, ty), t in zip(keys, tiles):
        if t is not None:
            mosaic.paste(t, ((tx - tx0) * 256, (ty - ty0) * 256))
    box = (x0 - tx0 * 256, y0 - ty0 * 256, x1 - tx0 * 256, y1 - ty0 * 256)
    im = mosaic.resize((S, S), Image.LANCZOS, box=box)

    def to_px(la, lo):
        x, y = _merc(la, lo, z)
        return (x - x0) * scale / S * 100, (y - y0) * scale / S * 100
    return im, to_px


def render(ev, v1):
    if ev["kind"] in SKIP:
        return None, dict(skip=True)
    km = footprint_km(ev)
    # the way you look: from the aircraft, at the moment of the best view, towards the place
    up = float(ev.get("brg") if ev.get("brg") is not None else geo.bearing(ev["ac"][0], ev["ac"][1], ev["lat"], ev["lon"]))
    im, to_px = crop(ev["lat"], ev["lon"], km, up)
    # the likely route through the frame
    route = []
    if v1 is not None:
        seg = []
        for t, la, lo, *_ in v1:
            x, y = to_px(la, lo)
            if -20 <= x <= 120 and -20 <= y <= 120:
                seg.append([round(x, 1), round(y, 1)])
            elif len(seg) > 1:
                route.append(seg); seg = []
            else:
                seg = []
        if len(seg) > 1:
            route.append(seg)
    # you look from the bottom edge upwards; `up` is that direction as a compass bearing, for the north arrow
    look = dict(up=round(up), x=50.0, y=94.0, compass=COMPASS[int((up % 360) / 22.5 + 0.5) % 16])
    labels = [dict(n=ev["name"], x=50.0, y=50.0, main=True)]
    for a in (ev.get("also") or [])[:4]:
        x, y = to_px(a["lat"], a["lon"])
        if 6 <= x <= 94 and 6 <= y <= 94 and all(abs(l["y"] - y) > 6 or abs(l["x"] - x) > 30 for l in labels):
            labels.append(dict(n=a["name"], x=round(x, 1), y=round(y, 1), main=False))
    return im, dict(km=round(km), route=route, look=look, labels=labels)


def render_all(pred, out_dir, log=print):
    import datetime as dt  # noqa: F401
    v1 = pred["variants"][0]["track"] if pred["variants"] else None
    res = {}
    for sd, evs in pred["timeline"].items():
        ovs, ims = [], []
        for e in evs:
            try:
                im, ov = render(e, v1)
            except Exception as ex:  # imagery is optional
                im, ov = None, dict(error=str(ex)[:200])
            if im is None:
                ov.setdefault("error", "skipped")
            ims.append(im)
            ovs.append(ov)
        cols = 10
        rows = max(1, math.ceil(len(ims) / cols))
        sheet = Image.new("RGB", (cols * S, rows * S), (40, 52, 64))
        for k, im in enumerate(ims):
            if im is not None:
                sheet.paste(im, ((k % cols) * S, (k // cols) * S))
            ovs[k]["tile"] = k
        sheet.save(out_dir / f"aerial-{sd}.jpg", quality=74, optimize=True, progressive=True)
        res[sd] = dict(file=f"aerial-{sd}.jpg", cols=cols, rows=rows, size=S, overlays=ovs)
        log(f"  aerial: {sum(1 for o in ovs if 'error' not in o)} of {len(ovs)} sightings have an overhead image")
    return res

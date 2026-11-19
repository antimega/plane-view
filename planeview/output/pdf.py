"""Phone-sized PDF window guide, one per side: print-CSS HTML rendered by headless Google Chrome.

Pages are 100 x 178 mm (a phone's 9:16), readable on a phone in flight and printable two to an A4 sheet.
  cover      flight, side, route map with the numbered highlights, light and cloud outlook, the top ten
  timeline   by half hour: a strip map (what lies off your side of the route, with the coastline) then each sighting:
             minutes after takeoff and clock time, a window showing where to look, what it is, distance, chance
  back page  how to read it, sources
"""
from __future__ import annotations

import datetime as dt
import html
import json
import math
import subprocess
from pathlib import Path

import numpy as np




def chrome() -> str | None:
    """A Chrome-family browser that can print to PDF headless: PLANEVIEW_CHROME, then the usual places."""
    import os
    import shutil
    env = os.environ.get("PLANEVIEW_CHROME")
    if env:
        return env if Path(env).exists() or shutil.which(env) else None
    for p in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
              "/Applications/Chromium.app/Contents/MacOS/Chromium",
              "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
              "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
              r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"):
        if Path(p).exists():
            return p
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge", "brave-browser"):
        if shutil.which(name):
            return shutil.which(name)
    return None

E = html.escape


def _land(e):
    """'-17 to land' under the time, for sightings nearer the landing than the takeoff."""
    if e.get("anchor") != "landing" or e.get("t_end") is None:
        return ""
    return f"<span>{round(e['t_end'])} to land</span>"


def _clock(T0, tmin, tz):
    from zoneinfo import ZoneInfo
    return dt.datetime.fromtimestamp(T0 + tmin * 60, ZoneInfo(tz)).strftime("%H:%M")


_SPRITES = {}


# Pictures are embedded at the size they print, not the size the page shows them. At full size the fifteen-flight site
# was 298 MB, over the 256 MB a published page may hold; at 170 dpi it is 249 MB.
PRINT_DPI = 170


def _tile_uri(path, k, cols, w, h, w_mm, q=68):
    """One tile of a sprite sheet as a data URI, scaled down to PRINT_DPI at w_mm wide."""
    import base64
    from io import BytesIO
    from PIL import Image
    im = _SPRITES.get(path)
    if im is None:
        im = _SPRITES[path] = Image.open(path).convert("RGB")
    x, y = (k % cols) * w, (k // cols) * h
    b = BytesIO()
    t = im.crop((x, y, x + w, y + h))
    pw = round(w_mm / 25.4 * PRINT_DPI)
    if pw < w:
        t = t.resize((pw, round(h * pw / w)), Image.LANCZOS)
    t.save(b, "JPEG", quality=q, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()


def view_html(data, e, w_mm=27):
    """The rendered window view (sprite tile) with outlines and labels, sized for the PDF."""
    v, V = e.get("view"), (data.get("views") or {}).get(e["side"])
    if not v or not V or v.get("error"):
        return f"<div class='view' style='width:{w_mm}mm'></div>"
    uri = _tile_uri(data["_dir"] / V["file"], v["tile"], V["cols"], V["w"], V["h"], w_mm)
    tile = f"<img class='tile' src='{uri}' alt=''>"
    P = lambda ln: " ".join(f"{x},{y}" for x, y in ln)
    lines = ""
    for key, c, w, da in (("coast", "#FFFFFF", 0.5, ""), ("lakes", "#D7ECFF", 0.45, ""), ("rivers", "#A9D3F5", 0.4, ""),
                          ("borders", "#FFE08A", 0.45, "2 1.5")):
        for ln in v.get(key, []):
            lines += (f"<polyline points='{P(ln)}' fill='none' stroke='{c}' stroke-width='{w}' "
                      f"{('stroke-dasharray=' + chr(39) + da + chr(39)) if da else ''} opacity='0.9'/>")
    marks = "".join(f"<circle cx='{lb['x']}' cy='{lb['y']}' r='{2.2 if lb['main'] else 1.0}' "
                    f"fill='{'none' if lb['main'] else '#fff'}' stroke='{'#fff' if lb['main'] else 'none'}' stroke-width='0.9'/>"
                    for lb in v.get("labels", []))
    lbs = [lb for lb in v.get("labels", []) if lb["main"]] + [lb for lb in v.get("labels", []) if not lb["main"]][:2]
    lbls = "".join(f"<span class='vl{' main ' + e['side'] if lb['main'] else ''}' style='left:{min(78, max(22, lb['x']))}%;"
                   f"top:{min(90, lb['y'] + 2.4)}%'>{E(lb['n'])}</span>" for lb in lbs)
    nose = "<span class='nose' style='right:9%'>NOSE ›</span>" if e["side"] == "L" else \
        "<span class='nose' style='left:9%'>‹ NOSE</span>"
    return (f"<div class='view' style='width:{w_mm}mm'>{tile}<svg viewBox='0 0 100 100' preserveAspectRatio='none'>"
            f"{lines}{marks}</svg>{lbls}{nose}</div>")


def aerial_html(data, e, w_mm=22):
    a, A = e.get("aerial"), (data.get("aerial") or {}).get(e["side"])
    if not a or not A or a.get("error"):
        return ""
    from ..aerial import S
    uri = _tile_uri(data["_dir"] / A["file"], a["tile"], A["cols"], S, S, w_mm)
    tile = f"<img class='tile' src='{uri}' alt=''>"
    route = "".join("<polyline points='" + " ".join(f"{x},{y}" for x, y in ln) +
                    "' fill='none' stroke='#FF5AAE' stroke-width='1.1' stroke-dasharray='3 2'/>" for ln in a.get("route", []))
    lk = a["look"]
    eye = (f"<g transform='translate({lk['x']},{lk['y']})'><path d='M0,-6 L4,3 L0,1 L-4,3 z' "
           f"fill='#fff' stroke='#000' stroke-width='0.4'/></g>")
    svg = (f"<svg viewBox='0 0 100 100'>{route}<circle cx='50' cy='50' r='4' fill='none' stroke='#fff' stroke-width='1'/>"
           f"{eye}<g transform='translate(86,15)'><circle r='8' fill='rgba(0,0,0,.35)'/>"
           f"<text y='2.4' text-anchor='middle' style='font:700 6.5px sans-serif;fill:#fff'>N</text>"
           f"<g transform='rotate({-lk['up']})'><path d='M0,-13 L2.3,-6.2 L0,-7.6 L-2.3,-6.2 z' fill='#fff' stroke='#000' "
           f"stroke-width='0.4'/></g></g></svg>")
    return (f"<div class='aer' style='width:{w_mm}mm'>{tile}{svg}<span class='cap'>as you face it · {a['km']} km"
            f"</span></div>")


def overview_svg(data, side, nums):
    x0, y0, x1, y1 = data["bounds"]
    W = 400
    H = W * (y1 - y0) / (x1 - x0)
    sx = lambda x: (x - x0) / (x1 - x0) * W
    sy = lambda y: (y1 - y) / (y1 - y0) * H
    path = lambda pts: "M" + "L".join(f"{sx(p[0]):.1f},{sy(p[1]):.1f}" for p in pts)
    out = [f'<svg class="map" viewBox="0 0 {W} {H:.0f}" xmlns="http://www.w3.org/2000/svg">',
           f'<rect width="{W}" height="{H:.0f}" fill="#E9EEF2"/>',
           f'<image href="relief.jpg" x="0" y="0" width="{W}" height="{H:.0f}" preserveAspectRatio="none"/>']
    for ln in data["vec"]["coast"]:
        out.append(f'<path d="{path(ln)}" fill="none" stroke="#2B689B" stroke-width="0.7"/>')
    for ln in data["vec"]["borders"]:
        out.append(f'<path d="{path(ln)}" fill="none" stroke="#8A949E" stroke-width="0.5" stroke-dasharray="3 2"/>')
    for v in data["variants"][1:]:
        out.append(f'<path d="{path(v["xy"])}" fill="none" stroke="#B5126B" stroke-width="1" stroke-dasharray="4 3" opacity="0.6"/>')
    if data["variants"]:
        out.append(f'<path d="{path(data["variants"][0]["xy"])}" fill="none" stroke="#B5126B" stroke-width="2.2"/>')
    col = "#C2271F" if side == "L" else "#13804A"
    for n, e in nums:
        out.append(f'<circle cx="{sx(e["x"]):.1f}" cy="{sy(e["y"]):.1f}" r="6" fill="{col}"/>'
                   f'<text x="{sx(e["x"]):.1f}" y="{sy(e["y"]) + 3:.1f}" text-anchor="middle" font-size="8" '
                   f'font-family="IBM Plex Mono, monospace" fill="#fff">{n}</text>')
    for p in data["vec"]["places"]:
        if p["r"] <= 3:
            out.append(f'<text x="{sx(p["x"]) + 3:.1f}" y="{sy(p["y"]) + 3:.1f}" font-size="7" fill="#59646F" '
                       f'font-family="Barlow, sans-serif">{E(p["n"])}</text>')
    f = data["flight"]
    for code, (xx, yy) in ((f["orig"]["code"], data["variants"][0]["xy"][0][:2]),
                           (f["dest"]["code"], data["variants"][0]["xy"][-1][:2])):
        out.append(f'<text x="{sx(xx):.1f}" y="{sy(yy) - 6:.1f}" text-anchor="middle" font-size="9" font-weight="600" '
                   f'font-family="Barlow Condensed, sans-serif" fill="#17202A">{E(code)}</text>')
    out.append("</svg>")
    return "".join(out)


def segment_map(data, side, t0, t1, evs, V1, variants_ll):
    """North-up map of one stretch: land and sea, lakes, borders, the likely route with minute ticks, numbered sightings."""
    from shapely.geometry import box
    from ..gazetteer import _land_parts
    from .basemap import AEQD
    T = np.asarray(V1, float)
    seg = T[(T[:, 0] >= t0 - 1) & (T[:, 0] <= t1 + 1)]
    if len(seg) < 2:
        return ""
    mid = seg[len(seg) // 2]
    pj = AEQD(mid[1], mid[2])
    pts_lat = list(seg[:, 1]) + [e["lat"] for _, e in evs]
    pts_lon = list(seg[:, 2]) + [e["lon"] for _, e in evs]
    x, y = pj.fwd(pts_lat, pts_lon)
    x0, x1, y0, y1 = x.min(), x.max(), y.min(), y.max()
    span = max(x1 - x0, (y1 - y0) * 1.75, 60)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    w = span * 1.18
    h = max((y1 - y0) * 1.25, w / 1.75)
    X0, X1, Y0, Y1 = cx - w / 2, cx + w / 2, cy - h / 2, cy + h / 2
    Wp, Hp = 360, 360 * h / w
    sx = lambda xx: (xx - X0) / (X1 - X0) * Wp
    sy = lambda yy: (Y1 - yy) / (Y1 - Y0) * Hp
    la_c, lo_c = pj.inv(np.array([X0, X1, X0, X1, cx]), np.array([Y0, Y0, Y1, Y1, cy]))
    bb = box(float(lo_c.min()) - 1, float(la_c.min()) - 1, float(lo_c.max()) + 1, float(la_c.max()) + 1)
    out = [f"<svg class='seg' viewBox='0 0 {Wp} {Hp:.0f}' xmlns='http://www.w3.org/2000/svg'>",
           f"<rect width='{Wp}' height='{Hp:.0f}' fill='#DCE7EF'/>"]

    def ring_path(c):
        px, py = pj.fwd(c[:, 1], c[:, 0])
        return "M" + "L".join(f"{sx(a):.1f},{sy(b):.1f}" for a, b in zip(px, py)) + "Z"

    tree, parts = _land_parts()
    for i in tree.query(bb, predicate="intersects"):
        g = parts[int(i)][0].intersection(bb)
        for p in (g.geoms if hasattr(g, "geoms") else [g]):
            if p.geom_type != "Polygon" or p.is_empty:
                continue
            d = ring_path(np.asarray(p.exterior.coords)) + "".join(ring_path(np.asarray(r.coords)) for r in p.interiors)
            out.append(f"<path d='{d}' fill='#EEE9DC' fill-rule='evenodd' stroke='#2B689B' stroke-width='0.6'/>")
    from .. import ne as NE
    for name in NE.LAKE_LAYERS:
        for rec, g in NE.layer(name):
            if g is None or not g.intersects(bb):
                continue
            for p in (g.geoms if hasattr(g, "geoms") else [g]):
                if p.geom_type == "Polygon":
                    out.append(f"<path d='{ring_path(np.asarray(p.exterior.coords))}' fill='#BFD6E6' stroke='#2B689B' "
                               f"stroke-width='0.4'/>")
    for rec, g in NE.layer("ne_10m_admin_0_boundary_lines_land"):
        if g is None or not g.intersects(bb):
            continue
        for ln in (g.geoms if hasattr(g, "geoms") else [g]):
            c = np.asarray(ln.coords)
            px, py = pj.fwd(c[:, 1], c[:, 0])
            out.append("<path d='M" + "L".join(f"{sx(a):.1f},{sy(b):.1f}" for a, b in zip(px, py)) +
                       "' fill='none' stroke='#8A949E' stroke-width='0.5' stroke-dasharray='3 2'/>")
    for vl in variants_ll[1:]:
        A = np.asarray(vl, float)
        A = A[(A[:, 0] >= t0 - 3) & (A[:, 0] <= t1 + 3)]
        if len(A) > 1:
            px, py = pj.fwd(A[:, 1], A[:, 2])
            out.append("<path d='M" + "L".join(f"{sx(a):.1f},{sy(b):.1f}" for a, b in zip(px, py)) +
                       "' fill='none' stroke='#B5126B' stroke-width='0.9' stroke-dasharray='4 3' opacity='0.55'/>")
    px, py = pj.fwd(seg[:, 1], seg[:, 2])
    out.append("<path d='M" + "L".join(f"{sx(a):.1f},{sy(b):.1f}" for a, b in zip(px, py)) +
               "' fill='none' stroke='#B5126B' stroke-width='2.4'/>")
    for t in range(int(math.ceil(t0 / 10) * 10), int(t1) + 1, 10):
        k = int(np.argmin(np.abs(seg[:, 0] - t)))
        out.append(f"<circle cx='{sx(px[k]):.1f}' cy='{sy(py[k]):.1f}' r='2' fill='#fff' stroke='#B5126B' stroke-width='1'/>"
                   f"<text x='{sx(px[k]) + 3:.1f}' y='{sy(py[k]) - 3:.1f}' font-size='7.5' font-family='IBM Plex Mono, monospace' "
                   f"fill='#B5126B'>+{t}</text>")
    # direction of travel
    k = len(px) - 1
    ang = math.degrees(math.atan2(-(py[k] - py[max(0, k - 3)]), px[k] - px[max(0, k - 3)]))
    out.append(f"<path d='M0,-3.5 L7,0 L0,3.5 z' fill='#B5126B' transform='translate({sx(px[k]):.1f},{sy(py[k]):.1f}) "
               f"rotate({ang:.0f})'/>")
    col = "#C2271F" if side == "L" else "#13804A"
    for n, e in evs:
        ex, ey = pj.fwd(e["lat"], e["lon"])
        out.append(f"<circle cx='{sx(ex):.1f}' cy='{sy(ey):.1f}' r='5.6' fill='{col}' stroke='#fff' stroke-width='0.8'/>"
                   f"<text x='{sx(ex):.1f}' y='{sy(ey) + 2.6:.1f}' text-anchor='middle' font-size='7' "
                   f"font-family='IBM Plex Mono, monospace' fill='#fff'>{n}</text>")
    # north arrow and scale
    out.append(f"<g transform='translate({Wp - 14},16)'><path d='M0,-9 L4,3 L0,0 L-4,3 z' fill='#17202A'/>"
               f"<text x='0' y='12' text-anchor='middle' font-size='7' font-family='Barlow, sans-serif' fill='#17202A'>N</text></g>")
    km = 10 ** math.floor(math.log10(max(w / 4, 1)))
    km = km * 5 if w / 4 >= km * 5 else km * 2 if w / 4 >= km * 2 else km
    L = km / (X1 - X0) * Wp
    out.append(f"<g transform='translate(8,{Hp - 8:.0f})'><path d='M0,0 h{L:.1f}' stroke='#17202A' stroke-width='1.2'/>"
               f"<text x='{L + 3:.1f}' y='3' font-size='7' font-family='IBM Plex Mono, monospace' fill='#17202A'>{km:g} km</text></g>")
    out.append("</svg>")
    return "".join(out)


CSS = """
@page { size: 100mm 178mm; margin: 5mm 5mm 6mm; }
:root { --port: #C2271F; --stbd: #13804A; --ink: #17202A; --muted: #59646F; --line: #D8DCD8; --track: #B5126B; }
* { box-sizing: border-box; }
body { margin: 0; font: 400 9.4pt/1.32 "Barlow", system-ui, sans-serif; color: var(--ink); -webkit-print-color-adjust: exact; print-color-adjust: exact; }
h1, h2, h3 { font-family: "Barlow Condensed", "Avenir Next Condensed", "Roboto Condensed", "Arial Narrow", sans-serif; font-weight: 600; margin: 0; }
h1 { font-size: 19pt; line-height: 1.02; }
h2 { font-size: 13pt; margin: 3mm 0 1.5mm; }
.mono { font-family: "IBM Plex Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace; font-variant-numeric: tabular-nums; }
.side { display: inline-block; color: #fff; font: 600 11pt/1 "Barlow Condensed", sans-serif; letter-spacing: .04em; padding: 1.2mm 2.5mm; border-radius: 1mm; margin: 2mm 0; }
.side.L { background: var(--port); } .side.R { background: var(--stbd); }
.facts { display: grid; grid-template-columns: auto 1fr; gap: .4mm 3mm; font-size: 8.2pt; margin: 1mm 0; }
.facts dt { color: var(--muted); } .facts dd { margin: 0; }
svg.map { display: block; height: 56mm; width: auto; max-width: 100%; border: .3mm solid var(--line); border-radius: 1mm; margin: 1.5mm auto; }
svg.seg { width: 100%; height: auto; max-height: 70mm; border: .3mm solid var(--line); border-radius: 1mm; margin: 1mm 0 1.5mm; }
.view { position: relative; aspect-ratio: 4 / 5; border-radius: 36% / 29%; overflow: hidden; background: #E7EAE4; margin: .8mm; box-shadow: 0 0 0 .8mm #E7EAE4, 0 0 0 1mm #C9CEC8; }
.view .tile, .aer .tile { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }
.view svg { position: absolute; inset: 0; width: 100%; height: 100%; }
.view .vl { position: absolute; transform: translate(-50%, 0); font: 600 6.4pt/1.05 "Barlow", sans-serif; color: #fff; white-space: nowrap; text-shadow: 0 0 1.5px #000, 0 0 2px #000; max-width: 94%; overflow: hidden; }
.view .vl.main { font-size: 7pt; padding: .2mm .8mm; border-radius: .6mm; text-shadow: none; }
.view .vl.main.L { background: var(--port); } .view .vl.main.R { background: var(--stbd); }
.pics { grid-column: 2; display: flex; gap: 2.5mm; align-items: start; }
.aer { position: relative; aspect-ratio: 1; border-radius: .8mm; overflow: hidden; background: #28343F; margin-top: .8mm; }

.aer svg { position: absolute; inset: 0; width: 100%; height: 100%; }
.aer .cap { position: absolute; left: .8mm; top: .6mm; font: 500 5.6pt/1 "IBM Plex Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace; color: #fff; text-shadow: 0 0 1.5px #000; }
.view .nose { position: absolute; top: 6%; font: 600 4.8pt/1 "Barlow", sans-serif; color: #fff; text-shadow: 0 0 1.5px #000; }
.note { font-size: 8.2pt; color: var(--muted); margin: 1mm 0; }
ol.top { margin: 1mm 0 0; padding-left: 6mm; font-size: 8.2pt; columns: 2; column-gap: 6mm; }
ol.top li { margin: .3mm 0; break-inside: avoid; } ol.top li .note { display: none; }
.cover { break-after: page; }
.block { break-before: auto; }
.block h2 { break-after: avoid; display: flex; justify-content: space-between; align-items: baseline; border-bottom: .4mm solid var(--track); padding-bottom: .6mm; }
.block h2 span { font: 400 8pt "IBM Plex Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace; color: var(--muted); }
.ev { display: grid; grid-template-columns: 11mm 1fr; gap: 1mm 2mm; padding: 1.4mm 0; border-bottom: .25mm solid var(--line); break-inside: avoid; }
.ev .t { text-align: right; line-height: 1.1; }
.ev .t b { font: 500 13pt/1 "IBM Plex Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace; display: block; }
.ev .t span { font: 400 7.2pt "IBM Plex Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace; color: var(--muted); white-space: nowrap; letter-spacing: -.02em; }
.ev .n { display: inline-block; min-width: 4.2mm; height: 4.2mm; border-radius: 50%; color: #fff; font: 600 7pt/4.2mm "IBM Plex Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace; text-align: center; margin-right: 1.2mm; vertical-align: 1px; }
.ev .n.L { background: var(--port); } .ev .n.R { background: var(--stbd); }
.ev h3 { font-size: 11.5pt; line-height: 1.08; }
.ev .m { font-size: 8.3pt; color: var(--muted); }
.ev .m b { color: var(--ink); font-weight: 600; }
.ev .a { font-size: 8pt; color: var(--muted); }
.conf { font: 500 6.6pt/1 "IBM Plex Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace; text-transform: uppercase; padding: .5mm 1mm; border-radius: .6mm; }
.conf.likely { color: #2D7A4C; background: #E2EFE6; } .conf.possible { color: #9A6A12; background: #F3EBDD; } .conf.lucky { color: #59646F; background: #ECEEEC; }
.ctx { font-size: 8.4pt; color: var(--muted); padding: .9mm 0 .9mm 14.5mm; border-bottom: .25mm solid var(--line); break-inside: avoid; }
.ctx b { color: var(--ink); font-weight: 500; }
.back { break-before: page; font-size: 8.4pt; }
.back p { margin: 0 0 2mm; }
"""


def render(data: dict, side: str, out_pdf: Path) -> Path:
    f = data["flight"]
    T0 = dt.datetime.fromisoformat(f["takeoff_utc"]).timestamp()
    tz = f["orig"]["tz"]
    # the guide keeps likely and possible sightings (the page lists the 'if lucky' ones too)
    evs = [e for e in data["events"].get(side, []) if e["p_see"] >= 0.25]
    numbered = [(k + 1, e) for k, e in enumerate(evs)]
    top = sorted(numbered, key=lambda ne_: -ne_[1]["score"])[:10]
    sname = "Left-hand window" if side == "L" else "Right-hand window"
    sun_side = next((c["text"] for c in data["context"] if c["type"] == "sun_side"), "")
    seat_note = (f.get("seat") or {}).get("note") or ""
    _d = dt.date.fromisoformat(f["date"])
    date_s = f"{_d:%a} {_d.day} {_d:%b %Y}"
    parts = [f"<!doctype html><html><head><meta charset='utf-8'><title>{E(f.get('number') or '')} window guide</title>",
             f"<style>{CSS}</style></head><body>"]
    # cover
    parts.append("<section class='cover'>")
    parts.append(f"<h1>{E(f.get('number') or '')} {E(f['orig']['city'] or f['orig']['code'])} → "
                 f"{E(f['dest']['city'] or f['dest']['code'])}</h1>")
    parts.append(f"<div class='side {side}'>{sname}</div>")
    parts.append("<dl class='facts'>" + "".join(f"<dt>{k}</dt><dd>{E(str(v))}</dd>" for k, v in [
        ("Date", date_s), ("Takeoff", f"≈ {f['takeoff_local']} {f['orig']['code']} time"),
        ("Flight time", f"≈ {f['duration_min'] // 60} h {f['duration_min'] % 60:02d}"),
        ("Aircraft", f.get("aircraft_name") or "—"), ("Seat", seat_note or "—"),
        ("Cloud", data["sources"].get("weather") or "clear view assumed"),
    ]) + "</dl>")
    parts.append(overview_svg(data, side, top))
    if sun_side:
        parts.append(f"<p class='note'>{E(sun_side)}.</p>")
    parts.append("<h2>Ten to look for</h2><ol class='top'>" + "".join(
        f"<li value='{n}'><b>{E(e['name'])}</b> <span class='mono'>+{round(e['t'])}</span> "
        f"<span class='note'>{E(e['what'])}</span></li>" for n, e in sorted(top)) + "</ol>")
    parts.append("<p class='note'>Times are minutes after takeoff. Clock times assume takeoff at the time above. "
                 "Each window is a view rendered from where the aircraft should be: coastlines, lakes and rivers are outlined, "
                 "the sighting is labelled in colour, and NOSE marks the way you are flying.</p>")
    parts.append("</section>")
    # timeline by half hour
    dur = f["duration_min"]
    v1 = data["variants"][0]["xy"] if data["variants"] else []
    from .basemap import AEQD
    pj = data["proj"]
    proj = AEQD(pj["lat0"], pj["lon0"], pj["rot"])
    V1 = None
    if v1:
        a = np.asarray(v1, float)
        la, lo = proj.inv(a[:, 0], a[:, 1])
        V1 = [[float(t), float(x), float(y), float(al)] for t, x, y, al in zip(a[:, 2], la, lo, a[:, 3])]
    VLL = []
    for v in data["variants"]:
        a = np.asarray(v["xy"], float)
        la, lo = proj.inv(a[:, 0], a[:, 1])
        VLL.append([[float(t), float(x), float(y)] for t, x, y in zip(a[:, 2], la, lo)])
    ctx = [c for c in data["context"] if c["type"] not in ("sun_side",) and (not c.get("side") or c["side"] == side)]
    step = 30 if dur <= 300 else 60
    for b0 in range(0, int(dur) + 1, step):
        b1 = b0 + step
        bev = [(n, e) for n, e in numbered if b0 <= e["t"] < b1]
        bctx = [c for c in ctx if b0 <= c["t"] < b1]
        if not bev and not bctx:
            continue
        parts.append(f"<section class='block'><h2>+{b0} to +{min(b1, dur)} min<span>≈ {_clock(T0, b0, tz)}–"
                     f"{_clock(T0, min(b1, dur), tz)}</span></h2>")
        if bev and V1:
            parts.append(segment_map(data, side, b0, b1, bev, V1, VLL))
        items = [(e["t"], 1, n, e) for n, e in bev] + [(c["t"], 0, None, c) for c in bctx]
        for t, kind, n, e in sorted(items, key=lambda x: (x[0], x[1])):
            if kind == 0:
                parts.append(f"<div class='ctx'><span class='mono'>+{round(t)}</span> <b>{E(e['text'])}</b></div>")
                continue
            conf = "likely" if e["confidence"] == "likely" else "possible" if e["confidence"] == "possible" else "lucky"
            also = ", ".join(a["name"] + ("" if a["kind"] == "settlement" else f" ({a['what'].split(',')[0]})")
                             for a in e.get("also", [])[:8])
            parts.append(
                f"<div class='ev'><div class='t'><b>+{round(e['t'])}</b><span>≈ {_clock(T0, e['t'], tz)}</span>{_land(e)}</div>"
                f"<div><h3><span class='n {side}'>{n}</span>{E(e['name'])}</h3>"
                f"<div class='m'>{E(e['what'])} · <b>{round(e['d_km'])} km</b> · {E(e['where'])} · <b>{E(e['clock'])}</b></div>"
                + (f"<div class='a'>Also: {E(also)}</div>" if also else "")
                + f"<div><span class='conf {conf}'>{E(e['confidence'])} {round(100 * e['p_see'])}%</span>"
                + (" <span class='conf lucky'>lights</span>" if e.get("light") == "night" else "")
                + "</div></div>"
                + f"<div class='pics'>{view_html(data, e, 31)}{aerial_html(data, e, 39)}</div></div>")
        parts.append("</section>")
    from ..views import CREDIT as TERRAIN_CREDIT
    # back page
    parts.append("<section class='back'><h2>How this was made</h2>"
                 f"<p>Routes: {E(data['sources']['route'])} on {len(data['sources']['days'])} sampled days "
                 f"(adsb.lol ADS-B archive, ODbL). Filed routes: {E(data['sources']['filed'])}.</p>"
                 f"<p>What can be seen: GeoNames and Natural Earth, tested from every point of every past flight against a "
                 f"window view calibrated on window photographs (25° behind to 55° ahead of abeam, down to 50° below the "
                 f"horizon). Chance = share of past flights it was in view and recognisable from, with daylight; "
                 f"a clear view is assumed ({E(data['sources']['weather'] or '')}).</p>"
                 f"<p>Likely: 55% or more. Possible: 25–55%. If lucky: less. Takeoff time: {E(f['takeoff_source'])}.</p>"
                 + (f"<p class='note'>Overhead imagery: {E(data.get('imagery_attribution') or '')}, CC BY-NC-SA 4.0.</p>"
                    if data.get('imagery_attribution') else "")
                 + f"<p class='note'>Terrain: {E(TERRAIN_CREDIT)}.</p>"
                 + f"<p class='note'>Generated {E(dt.date.today().isoformat())} by plane-view.</p></section>")
    parts.append("</body></html>")
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    src = out_pdf.with_suffix(".html")
    src.write_text("".join(parts))
    r = subprocess.run([chrome(), "--headless=new", "--disable-gpu", "--no-pdf-header-footer", "--hide-scrollbars",
                        "--virtual-time-budget=8000", f"--print-to-pdf={out_pdf}", src.as_uri()],
                       capture_output=True, text=True, timeout=180)
    if not out_pdf.exists():
        raise RuntimeError(f"Chrome did not write the PDF: {r.stderr[-500:]}")
    src.unlink()
    return out_pdf


def render_all(site_dir: Path):
    if not chrome():
        print("  pdf: no Chrome, Chromium, Edge or Brave found (set PLANEVIEW_CHROME) - PDFs skipped", flush=True)
        return []
    data = json.loads((site_dir / "data.json").read_text())
    data["_dir"] = site_dir
    _SPRITES.clear()
    return [render(data, sd, site_dir / f"guide-{sd}.pdf") for sd in data["flight"]["sides"]]

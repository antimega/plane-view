"""Per-flight page data: projected route variants, history, events per side, strip coordinates, sun and cloud bands.

site/f/<id>/data.json  - everything the page draws (km coordinates in the route projection); data.js is the same,
                         as a script the page can load from a plain folder
site/f/<id>/relief.jpg - shaded relief in the same projection
site/f/<id>/guide-L.pdf, guide-R.pdf - the window guides (output/pdf.py)
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .. import cache, geo
from . import basemap as BM

SITE = cache.SITE


def strip_coords(track, lat, lon):
    """(minutes, cross-track km; + = right of track) of points against a track [[t, lat, lon, alt], ...]."""
    T = np.asarray(track, float)
    lat, lon = np.atleast_1d(np.asarray(lat, float)), np.atleast_1d(np.asarray(lon, float))
    d = geo.dist_km(lat[:, None], lon[:, None], T[None, :, 1], T[None, :, 2])
    k = d.argmin(1)
    k2 = np.clip(k + 1, 0, len(T) - 1)
    k1 = np.where(k2 == k, np.maximum(k - 1, 0), k)
    k2 = np.where(k2 == k1, np.minimum(k1 + 1, len(T) - 1), k2)
    c = geo.cross_track_km(lat, lon, T[k1, 1], T[k1, 2], T[k2, 1], T[k2, 2])
    return T[k, 0], np.where(np.isfinite(c), c, 0.0)


def build(pred: dict, out_dir: Path | None = None, px=1500, render_views=True) -> Path:
    fid = pred["id"].lower()
    out = out_dir or SITE / "f" / fid
    out.mkdir(parents=True, exist_ok=True)
    v1 = pred["variants"][0]["track"] if pred["variants"] else pred["history"][0]["track"]
    allpts = np.array([p for v in pred["variants"] for p in v["track"]] +
                      [p for h in pred["history"] for p in h["track"]], float)
    proj = BM.for_route(np.array([p[1] for p in v1]), np.array([p[2] for p in v1]))
    X, Y = proj.fwd(allpts[:, 1], allpts[:, 2])
    # the view fits the routes; the relief reaches further so there is context to pan into
    pad = 0.08 * max(X.max() - X.min(), Y.max() - Y.min()) + 30
    fit = (X.min() - pad, Y.min() - pad, X.max() + pad, Y.max() + pad)
    fw, fh = fit[2] - fit[0], fit[3] - fit[1]
    cx, cy = (fit[0] + fit[2]) / 2, (fit[1] + fit[3]) / 2
    w, h = fw * 1.5, fh * 1.5
    w, h = max(w, h * 0.75), max(h, w * 0.6)
    bounds = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
    img, _ = BM.build(proj, bounds, px=px)
    img.save(out / "relief.jpg", quality=80, optimize=True, progressive=True)
    vec = BM.vectors(proj, bounds, tol_km=max(1.0, w / 1500))

    def xy_track(tr, step=1):
        a = np.asarray(tr, float)[::step]
        x, y = proj.fwd(a[:, 1], a[:, 2])
        return [[round(float(xx), 1), round(float(yy), 1), round(float(t), 1), int(al)]
                for xx, yy, t, al in zip(x, y, a[:, 0], a[:, 3])]

    def ev(e):
        x, y = proj.fwd(e["lat"], e["lon"])
        ax, ay = proj.fwd(e["at"][0], e["at"][1])
        t_s, c_s = strip_coords(v1, e["lat"], e["lon"])
        d = {k: e.get(k) for k in ("id", "name", "local", "kind", "what", "side", "t", "t_lo", "t_hi", "span_min",
                                    "fa", "dep", "d_km", "where", "clock", "alt_ft", "p_see", "p_geom", "p_clear",
                                    "confidence", "score", "pop", "elev", "area_km2", "light", "sun_el", "t_end", "anchor", "night_as",
                                    "ac", "brg", "lat", "lon")}
        d.update(x=round(float(x), 1), y=round(float(y), 1), ax=round(float(ax), 1), ay=round(float(ay), 1),
                 st=round(float(t_s[0]), 1), sc=round(float(c_s[0]), 1))
        als = []
        for a in e.get("also", []):
            xa, ya = proj.fwd(a["lat"], a["lon"])
            ta, ca = strip_coords(v1, a["lat"], a["lon"])
            als.append(dict(a, x=round(float(xa), 1), y=round(float(ya), 1), st=round(float(ta[0]), 1),
                            sc=round(float(ca[0]), 1)))
        d["also"] = als
        return d

    # sun and cloud bands along the main variant
    import datetime as dt
    T0 = dt.datetime.fromisoformat(pred["flight"]["takeoff_utc"]).timestamp()
    V1 = np.asarray(v1, float)
    _, sun_el = geo.sun_az_el(T0 + V1[:, 0] * 60, V1[:, 1], V1[:, 2])
    sun_el = sun_el + geo.dip_deg(V1[:, 3] * geo.FT)
    sun = [[round(float(t), 1), round(float(e), 1)] for t, e in zip(V1[::4, 0], sun_el[::4])]
    clouds = []
    for sd in pred["flight"]["sides"]:
        for e in pred["timeline"].get(sd, []):
            clouds.append([e["t"], e["p_clear"]])
    clouds.sort()

    data = dict(
        id=fid, flight=pred["flight"], sources=pred["sources"], stats=pred["stats"],
        bounds=[round(b, 1) for b in bounds], fit=[round(b, 1) for b in fit],
        proj=dict(lat0=proj.lat0, lon0=proj.lon0, rot=proj.rot),
        variants=[dict({k: v[k] for k in ("id", "p", "dep_rwy", "arr_rwy", "n", "spread_km", "medoid")},
                       xy=xy_track(v["track"], 2)) for v in pred["variants"]],
        history=[dict(cs=hh["cs"], day=hh["day"], kind=hh["kind"], w=hh["w"], dep_rwy=hh["dep_rwy"],
                      arr_rwy=hh["arr_rwy"], xy=[p[:2] for p in xy_track(hh["track"], 2)]) for hh in pred["history"]],
        events={sd: [ev(e) for e in pred["timeline"][sd]] for sd in pred["timeline"]},
        context=pred["context"], sun=sun, cloud=clouds, vec=vec,
        v1_strip=[[round(float(p[0]), 1), int(p[3])] for p in v1[::2]],
        pdfs={sd: f"guide-{sd}.pdf" for sd in pred["flight"]["sides"]},
    )
    if render_views:
        from .. import views as VW
        vv = VW.render_all(pred, out)
        for sd, v in vv.items():
            for e, ov in zip(data["events"][sd], v["overlays"]):
                e["view"] = ov
            data.setdefault("views", {})[sd] = dict(file=f"views-{sd}.jpg", cols=v["cols"], rows=v["rows"],
                                                    w=v["w"], h=v["h"])
    if render_views:
        from .. import aerial as AE
        aa = AE.render_all(pred, out)
        for sd, v in aa.items():
            for e, ov in zip(data["events"][sd], v["overlays"]):
                e["aerial"] = ov
            data.setdefault("aerial", {})[sd] = dict(file=v["file"], cols=v["cols"], rows=v["rows"])
        data["imagery_attribution"] = AE.ATTRIBUTION
    (out / "data.json").write_text(json.dumps(data, separators=(",", ":"), default=str))
    from .site import write_data_js
    write_data_js(out, data)
    return out

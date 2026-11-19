"""Filed routes (AeroAPI, optional) as route skeletons; archived ADS-B legs as how those routes are actually flown.

  skeletons(orig, dest)  routes airlines filed between the airports over the past year (AeroAPI
                         /airports/{o}/routes/{d}), decoded to coordinates
  reweight(legs, w, sk)  each archived leg is assigned to its nearest skeleton (mean distance); leg weights are rescaled
                         so the ensemble's share of each skeleton matches its share of filings, and filed routes that no
                         archived leg followed become spliced legs of their own
Without a key both return their input unchanged: everything still works on ADS-B alone.

Coverage (checked November 2026): AeroAPI's routes-between-airports data comes from the US FAA, so it exists only for
flights to or from the US, and then only for the part inside US airspace (EGLL-KPHX gives the Phoenix arrivals, e.g.
"RSK GUP EAGUL6"; EGGW-LPFR gives nothing). It is therefore only requested when one end is in the US, and partial
routes are matched over the stretch they cover.

Decoding a route string: lat/lon tokens (5130N00200W, 52N020W, 5220N/01530W) are parsed; fixes and navaids are looked up
in a local fix cache learned from AeroAPI-decoded routes (/flights/{id}/route gives every fix with coordinates), then
OurAirports navaids (nearest to the great circle when an identifier repeats). Airways (UL612, N864), DCT, SID/STAR names
and speed/level groups are skipped: consecutive fixes are joined straight, which follows an airway closely enough at
this scale.
"""
from __future__ import annotations

import json
import re

import numpy as np

from . import cache, geo
from .sources import aeroapi
from .sources import airports as AP

FIXES = cache.CACHE / "aeroapi" / "fixes.json"
LATLON = [
    re.compile(r"^(\d{2})(\d{2})?([NS])(\d{3})(\d{2})?([EW])$"),     # 5130N00200W, 52N020W
    re.compile(r"^(\d{2})(\d{2})?([NS])/?(\d{3})(\d{2})?([EW])$"),
]
DMS = re.compile(r"^(\d{2})(\d{2})(\d{2})([NS])/?(\d{3})(\d{2})(\d{2})([EW])$")  # 660000N/0200000W (FAA style)
NAT_SHORT = re.compile(r"^(\d{2})/(\d{2,3})$")                      # 52/20 = 52N 020W
SKIP = re.compile(r"^(DCT|[A-Z]{1,2}\d{1,4}[A-Z]?|[NMK]\d{3,4}[FAS]\d{3,4}|.*\d[A-Z]$)$")


def _fix_cache() -> dict:
    return json.loads(FIXES.read_text()) if FIXES.exists() else {}


def _us(icao: str | None) -> bool:
    return bool(icao) and (icao[0] == "K" or icao[:2] in ("PA", "PH", "PG", "TJ"))


def learn_fixes(ident: str, orig: str | None = None, dest: str | None = None, max_decode: int = 2, log=print) -> int:
    """Add the fixes of recent flights of `ident` (decoded by AeroAPI) to the local fix cache.

    Only worth paying for on routes with a US end (filed routes are FAA data); pass orig/dest (IATA or ICAO) to skip
    the call otherwise. Only flights whose filed route is known are decoded, at most max_decode of them ($0.01 each)."""
    if orig and dest:
        A = AP.airports()
        try:
            if "US" not in (A.get(orig).get("cc"), A.get(dest).get("cc")):
                return 0
        except KeyError:
            pass
    flights = aeroapi.recent_flights(ident) or []
    fx = _fix_cache()
    n0 = len(fx)
    done = 0
    for f in flights:
        if done >= max_decode:
            break
        o_ = (f.get("origin") or {}).get("code_icao")
        d_ = (f.get("destination") or {}).get("code_icao")
        if not f.get("route") or not (_us(o_) or _us(d_)):
            continue  # no filed route to decode (e.g. intra-European): don't pay for an empty answer
        fixes = aeroapi.decoded_route(f.get("fa_flight_id", "")) or []
        done += 1
        for p in fixes:
            if p.get("latitude") is not None and p.get("name"):
                fx[p["name"]] = [p["latitude"], p["longitude"]]
    if len(fx) > n0:
        FIXES.parent.mkdir(parents=True, exist_ok=True)
        FIXES.write_text(json.dumps(fx))
    return len(fx) - n0


def _ll(tok):
    m = DMS.match(tok)
    if m:
        la = int(m.group(1)) + int(m.group(2)) / 60 + int(m.group(3)) / 3600
        lo = int(m.group(5)) + int(m.group(6)) / 60 + int(m.group(7)) / 3600
        return (-la if m.group(4) == "S" else la), (-lo if m.group(8) == "W" else lo)
    for rx in LATLON:
        m = rx.match(tok)
        if m:
            la = int(m.group(1)) + int(m.group(2) or 0) / 60
            lo = int(m.group(4)) + int(m.group(5) or 0) / 60
            return (-la if m.group(3) == "S" else la), (-lo if m.group(6) == "W" else lo)
    m = NAT_SHORT.match(tok)
    if m:
        return float(m.group(1)), -float(m.group(2))
    return None


def decode(route: str, o: dict, d: dict):
    """[(name, lat, lon)] in order, and the share of tokens that could be placed."""
    fx = _fix_cache()
    nav = AP.navaids()
    pts, tried = [], 0
    prev = (o["lat"], o["lon"])
    for tok in route.split():
        p = _ll(tok)  # whole token first: lat/lon forms contain '/'
        if not p and "/" in tok and not NAT_SHORT.match(tok):
            tok = tok.split("/")[0]  # FIX/N0480F350: drop the speed and level
            p = _ll(tok)
        if p:
            pts.append((tok, *p)); tried += 1
            prev = p
            continue
        if SKIP.match(tok) or len(tok) < 2:
            continue
        tried += 1
        if tok in fx:
            pts.append((tok, *fx[tok]))
            prev = tuple(fx[tok])
        elif tok in nav:
            # identifiers repeat worldwide: take the one that makes the smallest detour from the previous point
            # towards the destination
            def detour(c):
                return float(geo.dist_km(prev[0], prev[1], c[0], c[1]) + geo.dist_km(c[0], c[1], d["lat"], d["lon"])
                             - geo.dist_km(prev[0], prev[1], d["lat"], d["lon"]))
            best = min(nav[tok], key=detour)
            if detour(best) < 1500:
                pts.append((tok, best[0], best[1]))
                prev = (best[0], best[1])
    return pts, (len(pts) / tried if tried else 0.0)


def skeletons(orig: str, dest: str, log=print):
    A = AP.airports()
    o, d = A.get(orig), A.get(dest)
    if "US" not in (o.get("cc"), d.get("cc")):
        return []  # FAA-sourced: nothing outside US airspace, so don't pay for an empty answer
    routes = aeroapi.filed_routes(o["icao"], d["icao"])
    if not routes:
        return []
    out = []
    for r in routes:
        pts, frac = decode(r.get("route") or "", o, d)
        if len(pts) < 2:
            continue
        # anchor an end at its airport only when the decoded route starts/ends near it (FAA routes are often just
        # the US part, e.g. a Phoenix arrival); a partial route is matched over its own stretch
        near_o = float(geo.dist_km(o["lat"], o["lon"], pts[0][1], pts[0][2])) < 400
        near_d = float(geo.dist_km(d["lat"], d["lon"], pts[-1][1], pts[-1][2])) < 400
        lat = ([o["lat"]] if near_o else []) + [p[1] for p in pts] + ([d["lat"]] if near_d else [])
        lon = ([o["lon"]] if near_o else []) + [p[2] for p in pts] + ([d["lon"]] if near_d else [])
        out.append(dict(route=r["route"], count=r.get("count", 1), types=sorted(set(r.get("aircraft_types") or [])),
                        partial=not (near_o and near_d),
                        alt=[r.get("filed_altitude_min"), r.get("filed_altitude_max")], lat=lat, lon=lon,
                        resolved=round(frac, 2), last=r.get("last_departure_time")))
    log(f"  filed routes {orig}-{dest}: {len(out)} decoded (of {len(routes)}), "
        f"{sum(s['count'] for s in out)} filings")
    return out


def _dense(lat, lon, step_km=20):
    la, lo = [], []
    for a, b, c, e in zip(lat[:-1], lon[:-1], lat[1:], lon[1:]):
        n = max(2, int(float(geo.dist_km(a, b, c, e)) / step_km))
        x, y = geo.gc_points(a, b, c, e, n)
        la += list(x[:-1]); lo += list(y[:-1])
    return np.array(la + [lat[-1]]), np.array(lo + [lon[-1]])


def reweight(legs, w, sks, orig, dest, log=print):
    """Match legs to filed skeletons and rescale weights to the filing shares; add spliced legs for filed routes that no
    archived leg followed. Returns (legs, w, note)."""
    if not sks:
        return legs, w, None
    from .route import splice
    dense = [_dense(s["lat"], s["lon"]) for s in sks]
    assign = []
    for lg in legs:
        best, bk = 1e9, None
        for k, (la, lo) in enumerate(dense):
            # how far the skeleton's points lie from the leg: works for partial skeletons too
            dk = geo.dist_km(la[:, None], lo[:, None], lg["lat"][None, ::3], lg["lon"][None, ::3]).min(1)
            m = float(np.mean(dk))
            if m < best:
                best, bk = m, k
        assign.append(bk if best < 60 else None)
    tot = sum(s["count"] for s in sks)
    share = {k: s["count"] / tot for k, s in enumerate(sks)}
    w = np.array(w, float)
    have = {}
    for k, a in enumerate(assign):
        if a is not None:
            have[a] = have.get(a, 0) + w[k]
    for k, a in enumerate(assign):
        if a is not None and have[a] > 0:
            w[k] = 0.5 * w[k] + 0.5 * share[a] * w[k] / have[a]
    new = []
    dep = next((lg for lg in legs if lg["kind"] in ("direct", "joined")), None)
    for k, s in enumerate(sks):
        if k in have or share[k] < 0.08 or s.get("partial"):  # only whole routes can stand in for a flight
            continue
        sp = splice(orig, dest, dep, dep)
        la, lo = _dense(s["lat"], s["lon"])
        sp.update(kind="filed", note=f"filed route, not seen in the archive: {s['route'][:80]}",
                  lat=la, lon=lo, t=np.linspace(0, float(sp["t"][-1]), len(la)),
                  alt=np.interp(np.linspace(0, 1, len(la)), [0, .1, .9, 1],
                                [0, (s["alt"][1] or 360) * 100, (s["alt"][1] or 360) * 100, 0]))
        sp["gs"] = np.full(len(la), np.nan)
        sp["gaps"] = []
        new.append((sp, share[k]))
    if new:
        legs = legs + [n for n, _ in new]
        w = np.concatenate([w * (1 - 0.5 * sum(p for _, p in new)), [0.5 * p for _, p in new]])
    w = w / w.sum()
    note = (f"{len(sks)} filed routes ({tot} filings); {sum(1 for a in assign if a is not None)} of {len(assign)} "
            f"archived flights match one; {len(new)} filed routes added that the archive did not show")
    log("  " + note)
    return legs, w, note

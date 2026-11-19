"""Likely routes for a future flight, from the flights actually flown between the same airports.

The route library (sources/archive.py) holds every leg seen on the sampled days. For an airport pair this module
collects:
  direct legs         takeoff at the origin, landing at the destination - including those whose last (or first)
                      few minutes no receiver saw: the scanner only names an airport when a track ends within 12 km
                      of it, and a straight-in approach is often lost 12-20 km out at 2,000 ft. Such a leg is counted
                      when its open end is low, close and on a plausible approach or climb to that airport and no
                      other airport is nearer (`_open_end`); Gatwick-Santiago goes from 11 past flights to about 30;
  joined legs         long-haul flights that the archive splits where ADS-B coverage stops (oceans, Siberia,
                      midnight UTC): an origin piece and a destination piece of the same aircraft, joined along the great
                      circle (the joined stretch is flagged as uncertain);
  borrowed legs       only when fewer than 3 of the above: departures from the origin to airports near the destination
                      and arrivals from airports near the origin, spliced with a great circle;
  great circle        the last resort, with a generic climb and descent.
Each leg is one plausible flight. Probabilities of seeing something are weighted shares of these legs; route *variants*
(for the map) are clusters of legs by departure runway, arrival runway and en-route path, drawn as their medoid leg.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict

import numpy as np

from . import geo
from .sources import airports as AP
from .sources import library

DEP_KM, ARR_KM = 150.0, 250.0


def library_days():
    return library.days()


def _arrays(lg):
    P = np.array([[p[0], p[1], p[2], np.nan if p[3] is None else p[3], np.nan if p[4] is None else p[4]]
                  for p in lg["pts"]], float)
    return P


def _takeoff_offset(lg):
    """Seconds between the takeoff and the first point we have (coverage often starts a little after takeoff)."""
    a = lg.get("s_alt")
    if a is None or a <= 300:
        return 0.0
    return min(600.0, a / 2200 * 60)  # ~2,200 ft/min initial climb


def _landing_offset(lg):
    a = lg.get("e_alt")
    if a is None or a <= 300:
        return 0.0
    return min(600.0, a / 1000 * 60)  # ~1,000 ft/min final descent


def make_leg(lg, kind="direct", parts=None):
    P = _arrays(lg) if parts is None else parts
    off = _takeoff_offset(lg) if kind in ("direct", "joined") else 0.0
    t = P[:, 0] + off
    alt = P[:, 3]
    if np.isnan(alt).all():
        alt = np.full(len(t), 30000.0)
    alt = np.where(np.isnan(alt), np.interp(t, t[~np.isnan(alt)], alt[~np.isnan(alt)]), alt)
    return dict(kind=kind, day=lg.get("day"), hex=lg.get("hex"), cs=lg.get("cs"), type=lg.get("type"),
                reg=lg.get("reg"), fr=lg.get("fr"), to=lg.get("to"), takeoff=float(lg["t0"]) - off,
                t=t, lat=P[:, 1], lon=P[:, 2], alt=alt, gs=P[:, 4], gaps=_gap_mask(t),
                landing=float(lg["t0"]) - off + t[-1] + _landing_offset(lg))


def _gap_mask(t):
    """Start indices of stretches longer than 10 minutes with no position (filled along the great circle)."""
    return [int(i) for i in np.flatnonzero(np.diff(t) > 600)]


def _join(pieces):
    """Origin piece + any middle pieces + destination piece of the same aircraft; gaps are filled along the great
    circle when the track is resampled."""
    a = pieces[0]
    P = []
    for pc in pieces:
        A = _arrays(pc).copy()
        A[:, 0] += pc["t0"] - a["t0"]
        P.append(A)
    P = np.vstack(P)
    b = pieces[-1]
    lg = dict(a, to=b["to"], e_alt=b.get("e_alt"), t1=b["t1"])
    out = make_leg(lg, "joined", P)
    out["pieces"] = len(pieces)
    return out


OPEN_KM = 35.0   # how far from the airport an unseen landing or takeoff may begin


def _open_end(lg, A, code, which) -> bool:
    """True when a leg whose arrival (which='arr') or departure ('dep') airport is unknown clearly used `code`:
    its end is within OPEN_KM, no higher above the field than an approach (about 320 ft per km; twice that allowed)
    or a climb (up to 900 ft per km) would be at that distance, and no other airport is nearer."""
    if lg.get("cont_next" if which == "arr" else "cont_prev"):
        return False            # cut by midnight, not by coverage: the joiner deals with those
    p = lg["pts"][-1] if which == "arr" else lg["pts"][0]
    if p[3] is None:
        return False
    ap = A.get(code)
    km = float(geo.dist_km(p[1], p[2], ap["lat"], ap["lon"]))
    above = p[3] - ap["elev_ft"]
    if km > OPEN_KM or above > (650 if which == "arr" else 900) * km + 1500:
        return False
    i, _ = A.nearest([p[1]], [p[2]], OPEN_KM)
    return int(i[0]) == A.by[code]


def collect(orig: str, dest: str, days=None, exclude_days=(), log=print):
    """All legs for orig -> dest from the library."""
    A = AP.airports()
    o, d = A.get(orig), A.get(dest)
    oc, dc = A.code(A.by[orig.upper()]), A.code(A.by[dest.upper()])
    con = library.connect()
    all_days = library.days(con)
    days = [x for x in (days or all_days) if x not in set(exclude_days)]
    only = None if days == all_days else days   # no day filter when the whole library is wanted
    # complete legs on the pair, departures from the origin that end in mid-air, arrivals that begin in mid-air
    raw = library.query("(fr = ? AND (too = ? OR too IS NULL)) OR (too = ? AND fr IS NULL)", (oc, dc, dc), only, con)
    direct = [make_leg(lg) for lg in raw if lg["fr"] == oc and lg["to"] == dc]
    # one end unseen but unmistakable: lost on the approach to the destination, or first seen climbing out of the origin
    n_open = 0
    for lg in raw:
        which = "arr" if lg["fr"] == oc and lg["to"] is None else "dep" if lg["fr"] is None and lg["to"] == dc else None
        if which and _open_end(lg, A, dc if which == "arr" else oc, which):
            m = make_leg(dict(lg, fr=oc, to=dc))
            m["note"] = "landing not seen by a receiver" if which == "arr" else "takeoff not seen by a receiver"
            direct.append(m)
            lg["_used"] = True
            n_open += 1
    direct.sort(key=lambda m: (m["day"], m["takeoff"], m["hex"] or ""))
    # pieces of long flights
    heads = [lg for lg in raw if lg["fr"] == oc and lg["to"] is None and not lg.get("_used")]
    tails = [lg for lg in raw if lg["fr"] is None and lg["to"] == dc and not lg.get("_used")]
    by_hex = defaultdict(list)
    for tl in tails:
        by_hex[tl["hex"]].append(tl)
    joined = []
    gc_total = float(geo.dist_km(o["lat"], o["lon"], d["lat"], d["lon"]))
    middles = defaultdict(list)
    if heads:
        hexes = sorted({h["hex"] for h in heads})
        for k in range(0, len(hexes), 500):
            part = hexes[k:k + 500]
            for lg in library.query(f"fr IS NULL AND too IS NULL AND hex IN ({','.join('?' * len(part))})", part,
                                    only, con):
                middles[lg["hex"]].append(lg)
    for h in heads:
        last = h["pts"][-1]
        rem_h = float(geo.dist_km(last[1], last[2], d["lat"], d["lon"]))
        if rem_h > gc_total * 1.05:  # heading somewhere else
            continue
        cands = []
        for tl in by_hex.get(h["hex"], []):
            gap = tl["t0"] - h["t1"]
            first = tl["pts"][0]
            rem_t = float(geo.dist_km(first[1], first[2], d["lat"], d["lon"]))
            if 0 < gap < 14 * 3600 and rem_t < rem_h:
                # the gap must be flyable at airliner speed (< 1,100 km/h)
                if geo.dist_km(last[1], last[2], first[1], first[2]) / max(gap / 3600, 0.1) < 1100:
                    cands.append((gap, tl))
        if cands:
            tl = min(cands, key=lambda c: c[0])[1]
            mids = sorted((m for m in middles.get(h["hex"], []) if h["t1"] < m["t0"] and m["t1"] < tl["t0"]),
                          key=lambda m: m["t0"])
            # keep middle pieces that make progress towards the destination
            chain, rem = [], rem_h
            for m in mids:
                r = float(geo.dist_km(m["pts"][-1][1], m["pts"][-1][2], d["lat"], d["lon"]))
                if r < rem:
                    chain.append(m); rem = r
            joined.append(_join([h, *chain, tl]))
            h["_joined"] = True
    # Departure pieces with no same-aircraft continuation (the next day was not sampled - long flights cross midnight
    # UTC): splice each with an arrival piece of the same callsign from another day. The departure piece usually covers
    # all but the last hour, so the splice only bridges a short great-circle gap.
    tails_cs = defaultdict(list)
    for tl in tails:
        if tl.get("cs"):
            tails_cs[tl["cs"]].append(tl)
    for h in heads:
        if h.get("_joined") or not h.get("cs") or h["cs"] not in tails_cs:
            continue
        last = h["pts"][-1]
        rem_h = float(geo.dist_km(last[1], last[2], d["lat"], d["lon"]))
        if rem_h > gc_total * 1.05:
            continue
        # the same aircraft's later pieces that day (ADS-B picks it up again after the ocean)
        chain, end = [], h
        for m in sorted((m for m in middles.get(h["hex"], []) if m["day"] == h["day"] and m["t0"] > h["t1"]),
                        key=lambda m: m["t0"]):
            r = float(geo.dist_km(m["pts"][-1][1], m["pts"][-1][2], d["lat"], d["lon"]))
            if r < rem_h and m["t0"] - end["t1"] < 6 * 3600:
                chain.append(m); end = m; rem_h = r
        last = end["pts"][-1]
        best = None
        for tl in tails_cs[h["cs"]]:
            # keep only the part of the arrival piece that is nearer the destination than the departure piece's end
            P = tl["pts"]
            rem = geo.dist_km(np.array([p[1] for p in P]), np.array([p[2] for p in P]), d["lat"], d["lon"])
            k = int(np.argmax(rem < rem_h - 20)) if (rem < rem_h - 20).any() else None
            if k is None or len(P) - k < 3:
                continue
            first = P[k]
            gap_km = float(geo.dist_km(last[1], last[2], first[1], first[2]))
            if gap_km < 600 and (best is None or gap_km < best[0]):
                best = (gap_km, tl, k)
        if best:
            gap_km, tl, k = best
            t_off = tl["pts"][k][0]
            tl2 = dict(tl, t0=end["t1"] + gap_km / 850 * 3600,
                       pts=[[p[0] - t_off, *p[1:]] for p in tl["pts"][k:]])
            lg = _join([h, *chain, tl2])
            lg["kind"] = "joined"
            lg["note"] = f"spliced with the {tl['day']} arrival of {tl['cs']} ({gap_km:.0f} km gap)"
            joined.append(lg)
    legs = direct + joined
    log(f"  routes {oc}-{dc}: {len(direct)} direct ({n_open} with an end unseen), {len(joined)} joined "
        f"from {len(days)} day{'' if len(days) == 1 else 's'}")
    if len(legs) < 3:
        # other flights out of the origin and into the destination, to borrow a departure and an arrival from
        near = library.query("(fr = ? AND too IS NOT NULL AND too != ?) OR (too = ? AND fr IS NOT NULL AND fr != ?)",
                             (oc, dc, dc, oc), only, con)
        legs += borrowed(oc, dc, days, near, log=log)
    con.close()
    if not legs:
        legs = [great_circle(oc, dc)]
        log("  routes: no history - great circle with a generic profile")
    for lg in legs:
        lg["dep_rwy"], lg["arr_rwy"] = runway_of(lg, A.get(oc), "dep"), runway_of(lg, A.get(dc), "arr")
        _cap_ends(lg, A.get(oc), A.get(dc))      # after the runways: they are read from the points really received
    return legs


CAP_KM = 1.5


def _cap_ends(lg, o, d):
    """Give a past flight its first and last minutes when no receiver saw them: a point on the airport at the
    estimated takeoff (t = 0) and landing times, joined to the first / last point received. Without them nothing
    beside the airport is ever 'seen' from that flight - out of Faro, where coverage starts 10 km out at 4,000 ft,
    that hides the resorts beside the runway from more than half the past flights. The join
    is a straight line, not the runway heading and turn actually flown."""
    if lg.get("kind") not in ("direct", "joined"):
        return
    t, la, lo, alt, gs = lg["t"], lg["lat"], lg["lon"], lg["alt"], lg["gs"]
    if t[0] > 5 and geo.dist_km(o["lat"], o["lon"], la[0], lo[0]) > CAP_KM:
        t, la, lo = np.r_[0.0, t], np.r_[o["lat"], la], np.r_[o["lon"], lo]
        alt, gs = np.r_[float(o.get("elev_ft") or 0), alt], np.r_[np.nan, gs]
        lg["capped_dep"] = True
    end = (lg["landing"] - lg["takeoff"]) if lg.get("landing") and lg.get("takeoff") else None
    if end is not None and end > t[-1] + 5 and geo.dist_km(d["lat"], d["lon"], la[-1], lo[-1]) > CAP_KM:
        t, la, lo = np.r_[t, end], np.r_[la, d["lat"]], np.r_[lo, d["lon"]]
        alt, gs = np.r_[alt, float(d.get("elev_ft") or 0)], np.r_[gs, np.nan]
        lg["capped_arr"] = True
    lg.update(t=t, lat=la, lon=lo, alt=alt, gs=gs, gaps=_gap_mask(t))


def borrowed(oc, dc, days, raw, log=print):
    """Departures from oc towards dc's area and arrivals into dc from oc's area, spliced along the great circle."""
    A = AP.airports()
    o, d = A.get(oc), A.get(dc)
    near_d = lambda code: code and float(geo.dist_km(A.get(code)["lat"], A.get(code)["lon"], d["lat"], d["lon"])) < 450
    near_o = lambda code: code and float(geo.dist_km(A.get(code)["lat"], A.get(code)["lon"], o["lat"], o["lon"])) < 450
    deps = [make_leg(lg, "dep") for lg in raw if lg["fr"] == oc and lg["to"] and lg["to"] != dc and near_d(lg["to"])]
    arrs = [make_leg(lg, "arr") for lg in raw if lg["to"] == dc and lg["fr"] and lg["fr"] != oc and near_o(lg["fr"])]
    out = []
    deps, arrs = deps[:6], arrs[:6]
    if not deps and not arrs:
        return out
    for dl in deps or [None]:
        for al in arrs or [None]:
            out.append(splice(oc, dc, dl, al))
            if len(out) >= 12:
                break
    log(f"  routes: borrowed {len(deps)} departures and {len(arrs)} arrivals from nearby airports -> {len(out)} legs")
    return out


def _cut(lg, km_from_start=None, km_from_end=None):
    s = np.r_[0, np.cumsum(geo.dist_km(lg["lat"][:-1], lg["lon"][:-1], lg["lat"][1:], lg["lon"][1:]))]
    if km_from_start is not None:
        k = np.searchsorted(s, km_from_start) + 1
        sl = slice(0, max(2, k))
    else:
        k = np.searchsorted(s, s[-1] - km_from_end)
        sl = slice(min(k, len(s) - 2), len(s))
    return {key: lg[key][sl] for key in ("t", "lat", "lon", "alt")}


def splice(oc, dc, dep=None, arr=None, cruise_ft=36000.0):
    A = AP.airports()
    o, d = A.get(oc), A.get(dc)
    parts = []
    if dep is not None:
        parts.append(_cut(dep, km_from_start=DEP_KM))
    else:
        parts.append(_generic_climb(o, d))
    tail = _cut(arr, km_from_end=ARR_KM) if arr is not None else _generic_descent(o, d)
    a_lat, a_lon, a_t, a_alt = parts[0]["lat"][-1], parts[0]["lon"][-1], parts[0]["t"][-1], parts[0]["alt"][-1]
    b_lat, b_lon = tail["lat"][0], tail["lon"][0]
    gap_km = float(geo.dist_km(a_lat, a_lon, b_lat, b_lon))
    n = max(2, int(gap_km / 20))
    mla, mlo = geo.gc_points(a_lat, a_lon, b_lat, b_lon, n)
    v_kmh = 820.0
    mt = a_t + np.linspace(0, gap_km / v_kmh * 3600, n)
    malt = np.interp(np.linspace(0, 1, n), [0, 0.15, 0.85, 1], [a_alt, cruise_ft, cruise_ft, tail["alt"][0]])
    tt = tail["t"] - tail["t"][0] + mt[-1] + 20
    t = np.r_[parts[0]["t"], mt[1:-1], tt]
    lat = np.r_[parts[0]["lat"], mla[1:-1], tail["lat"]]
    lon = np.r_[parts[0]["lon"], mlo[1:-1], tail["lon"]]
    alt = np.r_[parts[0]["alt"], malt[1:-1], tail["alt"]]
    src = dep or arr or {}
    return dict(kind="borrowed", day=src.get("day"), hex=None, cs=None, type=src.get("type"), reg=None, fr=oc, to=dc,
                takeoff=None, t=t, lat=lat, lon=lon, alt=alt, gs=np.full(len(t), np.nan),
                gaps=[len(parts[0]["t"]) - 1], landing=None,
                note=f"spliced: {dep['fr'] + '-' + dep['to'] if dep else 'generic climb'} + "
                     f"{arr['fr'] + '-' + arr['to'] if arr else 'generic descent'}")


def _generic_climb(o, d, cruise_ft=36000.0):
    brg = float(geo.bearing(o["lat"], o["lon"], d["lat"], d["lon"]))
    km = np.linspace(0, DEP_KM, 31)
    la, lo = geo.destination(o["lat"], o["lon"], brg, km)
    t = km / 520 * 3600  # ~280 kt average over the climb
    alt = np.minimum(cruise_ft, o["elev_ft"] + t / 60 * 2100)
    return dict(t=t, lat=la, lon=lo, alt=alt)


def _generic_descent(o, d, cruise_ft=36000.0):
    brg = float(geo.bearing(d["lat"], d["lon"], o["lat"], o["lon"]))
    km = np.linspace(ARR_KM, 0, 41)
    la, lo = geo.destination(d["lat"], d["lon"], brg, km)
    t = (ARR_KM - km) / 560 * 3600
    alt = np.minimum(cruise_ft, d["elev_ft"] + km * 1000 / 3.0 * 0.0524 / geo.FT)  # 3 deg path
    return dict(t=t, lat=la, lon=lo, alt=alt)


def great_circle(oc, dc):
    lg = splice(oc, dc)
    lg.update(kind="great circle", note="no flights on this route in the library: great circle, generic climb/descent")
    return lg


RWY_KM = 25


def runway_of(lg, ap, which):
    """Runway end used: track heading over the first (dep) or last (arr) 2-25 km against the runway headings (within
    25 deg); among parallel runways the one whose threshold (dep) or far end (arr) is nearest the track's end point.

    25 km, not less: simplified library tracks reduce a straight final approach to one segment, and some receivers
    lose arrivals below ~4,000 ft (Phoenix: the point before touchdown is 15-25 km out). A gap that hides a turn onto
    final gives a heading far from any runway and is left unknown rather than guessed."""
    rws = AP.runways(ap["icao"])
    if not rws:
        return None
    la, lo = lg["lat"], lg["lon"]
    if which == "dep":
        a = 0
        if geo.dist_km(ap["lat"], ap["lon"], la[a], lo[a]) > 8:
            return None
        dk = geo.dist_km(la[a], lo[a], la, lo)
        nxt = np.flatnonzero((dk >= 2) & (dk <= RWY_KM))
        if not len(nxt):
            return None
        b = nxt[0]
        hdg = float(geo.bearing(la[a], lo[a], la[b], lo[b]))
        end_lat, end_lon = la[a], lo[a]
    else:
        b = len(la) - 1
        km_out = float(geo.dist_km(ap["lat"], ap["lon"], la[b], lo[b]))
        if km_out > 12:
            # a track lost on the approach still tells the runway if it stops on the glide path (about 320 ft per
            # km): established on final, not on a downwind leg the other way
            alt = lg.get("alt")
            above = None if alt is None else float(alt[b]) - ap["elev_ft"]
            if km_out > 22 or above is None or not 150 * km_out <= above <= 450 * km_out:
                return None
        dk = geo.dist_km(la[b], lo[b], la, lo)
        prv = np.flatnonzero((dk >= 2) & (dk <= RWY_KM))
        if not len(prv):
            return None
        a = prv[-1]
        hdg = float(geo.bearing(la[a], lo[a], la[b], lo[b]))
        end_lat, end_lon = la[b], lo[b]
    cands = [r for r in rws if abs(float(geo.wrap180(r["hdg"] - hdg))) <= 25]
    if not cands:
        return None
    def near(r):
        if r["lat"] is None:
            return 99.0
        return float(geo.dist_km(r["lat"], r["lon"], end_lat, end_lon))
    # the threshold of the runway end in use lies behind a departure's first point and ahead of an arrival's last
    return min(cands, key=near)["ident"]


def rwy_dir(ident):
    """'26L' -> '26' (parallel runways share a direction)."""
    return ident[:2] if ident and ident[:2].isdigit() else ident


# ------------------------------------------------------------------ weighting and variants
def weigh(legs, flight_cs=None, airline=None, dep_local_min=None, date=None, rwy_prob=None):
    """Weight of each leg as a sample of the future flight. Returns normalised weights."""
    w = np.ones(len(legs))
    for k, lg in enumerate(legs):
        if lg["kind"] == "borrowed":
            w[k] *= 0.5
        if lg["kind"] == "great circle":
            w[k] *= 0.3
        cs = lg.get("cs") or ""
        if flight_cs and cs == flight_cs:
            w[k] *= 3.0
        elif airline and cs.startswith(airline):
            w[k] *= 2.0
        if dep_local_min is not None and lg.get("takeoff"):
            tz = lg.get("tz")
            if tz:
                lm = _local_minutes(lg["takeoff"], tz)
                diff = abs((lm - dep_local_min + 720) % 1440 - 720)
                w[k] *= 1.5 if diff <= 120 else 1.0
        if date is not None and lg.get("day"):
            if lg["day"][5:7] == date.strftime("%m"):
                w[k] *= 1.3
        if rwy_prob:
            for key, which in (("dep", "dep_rwy"), ("arr", "arr_rwy")):
                pr = rwy_prob.get(key)
                if pr and lg.get(which):
                    w[k] *= max(0.05, pr.get(rwy_dir(lg[which]), 0.05)) / max(pr.values())
    return w / w.sum()


def _local_minutes(t, tz):
    from zoneinfo import ZoneInfo
    x = dt.datetime.fromtimestamp(t, dt.UTC).astimezone(ZoneInfo(tz))
    return x.hour * 60 + x.minute


def resample_frac(lg, n=60):
    s = np.r_[0, np.cumsum(geo.dist_km(lg["lat"][:-1], lg["lon"][:-1], lg["lat"][1:], lg["lon"][1:]))]
    f = np.linspace(0, s[-1], n)
    return np.interp(f, s, lg["lat"]), np.interp(f, s, lg["lon"]), s[-1]


def variants(legs, w, orig, dest, max_n=6):
    """Cluster legs into route variants: same runway directions, then en-route paths within ~30 km."""
    A = AP.airports()
    o, d = A.get(orig), A.get(dest)
    offs = []
    for lg in legs:
        la, lo, _ = resample_frac(lg)
        offs.append(geo.cross_track_km(la, lo, o["lat"], o["lon"], d["lat"], d["lon"]))
    offs = np.array(offs)
    groups = defaultdict(list)
    for k, lg in enumerate(legs):
        groups[(rwy_dir(lg.get("dep_rwy")), rwy_dir(lg.get("arr_rwy")))].append(k)
    clusters = []
    for key, members in groups.items():
        rest = list(members)
        while rest:
            seed = max(rest, key=lambda k: w[k])
            close = [k for k in rest if np.mean(np.abs(offs[k] - offs[seed])) < 30]
            clusters.append(dict(key=key, members=close))
            rest = [k for k in rest if k not in close]
    for c in clusters:
        c["w"] = float(sum(w[k] for k in c["members"]))
    clusters.sort(key=lambda c: -c["w"])
    while len(clusters) > max_n:  # merge the smallest into the most similar
        small = clusters.pop()
        mo = offs[small["members"]].mean(0)
        tgt = min(clusters, key=lambda c: np.mean(np.abs(offs[c["members"]].mean(0) - mo)))
        tgt["members"] += small["members"]
        tgt["w"] += small["w"]
    clusters.sort(key=lambda c: -c["w"])  # merging can reorder them: V1 must be the most likely
    out = []
    for n, c in enumerate(clusters):
        M = c["members"]
        mo = offs[M].mean(0)
        medoid = min(M, key=lambda k: np.mean(np.abs(offs[k] - mo)) - 1e-6 * w[k])
        dep_r = Counter(legs[k].get("dep_rwy") for k in M).most_common(1)[0][0]
        arr_r = Counter(legs[k].get("arr_rwy") for k in M).most_common(1)[0][0]
        out.append(dict(id=f"V{n + 1}", p=round(c["w"], 3), legs=M, medoid=medoid, dep_rwy=dep_r, arr_rwy=arr_r,
                        n=len(M), spread_km=round(float(np.percentile(np.abs(offs[M] - mo), 90)), 1) if len(M) > 1 else None))
    tot = sum(v["p"] for v in out)
    for v in out:
        v["p"] = round(v["p"] / tot, 3)
    return out

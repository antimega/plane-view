"""Predict what will be visible from each side of a future flight.

  predict(date, flight=..., orig=..., dest=..., dep=..., seat=...) -> prediction dict (schema v1, see output/schema.py)

Pipeline: flight identity -> past legs on the route (route.collect) -> weights and variants -> corridor gazetteer ->
visibility on every leg (visibility.evaluate) with sun and seat -> cloud (weather.Cloud) -> events per side with
probability, time window, where to look -> salience ranking and thinning -> context (coast, borders, seas, sunrise...).
"""
from __future__ import annotations

import datetime as dt
import math
import os
import time
from collections import Counter, defaultdict
from zoneinfo import ZoneInfo

import numpy as np

from . import gazetteer as G
from . import geo, route, seat as SEAT, visibility as V, weather
from .sources import airports as AP
from .sources import flights as FL

SCHEMA = "plane-view/prediction/1"
TAXI_MIN = 15


# ------------------------------------------------------------------ salience
LANDMARK_WORD = {"BDG": "bridge", "BDGQ": "bridge", "DAM": "dam", "LTHSE": "lighthouse", "STDM": "stadium",
                 "CSTL": "castle", "PIER": "pier", "TOWR": "tower", "PS": "power station", "PSN": "nuclear power station",
                 "PAL": "palace", "MNMT": "monument", "AIRP": "airport", "HBR": "harbour", "OBS": "observatory",
                 "RSRT": "resort", "MALL": "shopping centre", "RLG": "religious site", "CTRS": "space centre", "PRK": "park"}


def salience(f) -> float:
    k = f["kind"]
    fame = f.get("fame", 0) or 0
    area = f.get("area_km2")
    if k == "settlement":
        base = 0.9 * math.log10(max(f.get("pop", 0) or 0, 500)) - 2.2
    elif k == "peak":
        base = min(3.5, max(0.2, ((f.get("elev") or 0) - 1000) / 800))
    elif k == "lake":
        base = 1.2 + 0.6 * math.log10(1 + area) if area else 1.0
    elif k == "island":
        base = 1.5 + 0.5 * math.log10(1 + area) if area else 1.2
    elif k == "coast":
        base = 1.3
    elif k == "sea":
        base = 1.0 + (0.3 * math.log10(1 + area) if area else 0)
    elif k == "river":
        base = 1.2
    elif k == "range":
        base = 1.8
    elif k == "landmark":
        base = 1.6
    else:
        base = 0.6
    return max(0.05, base + 0.45 * math.log2(1 + fame))


def describe(f) -> str:
    if f.get("src") in ("OpenStreetMap", "Wikidata") and f.get("what"):
        return f["what"]
    k, sub = f["kind"], f.get("sub") or ""
    pop, elev, area = f.get("pop") or 0, f.get("elev"), f.get("area_km2")
    if k == "settlement":
        if pop >= 1_000_000:
            return f"city of {pop / 1e6:.1f} million"
        if pop >= 100_000:
            return f"city of {round(pop, -3):,.0f}"
        if pop >= 15_000:
            return f"town of {round(pop, -3):,.0f}"
        return f"village or small town ({pop:,})" if pop else "settlement"
    if k == "peak":
        return f"peak, {elev:,} m" if elev else "peak"
    if k == "lake":
        word = "reservoir" if sub.startswith("RSV") or sub == "Reservoir" else "lagoon" if sub == "LGN" else "lake"
        return f"{word}, {area:,.0f} km²" if area and area >= 1 else word
    if k == "island":
        return f"island, {area:,.0f} km²" if area and area >= 1 else "island"
    if k == "coast":
        return {"CAPE": "cape", "PT": "headland", "PEN": "peninsula", "SPIT": "spit", "ISTH": "isthmus", "BCH": "beach"}.get(sub, "coast")
    if k == "sea":
        return {"BAY": "bay", "GULF": "gulf", "STRT": "strait", "SD": "sound", "INLT": "inlet", "FJD": "fjord",
                "ESTY": "estuary", "HBR": "harbour", "LGNS": "lagoons"}.get(sub, (sub or "sea").lower())
    if k == "river":
        return "river"
    if k == "range":
        return "mountain range"
    if k == "landmark":
        return LANDMARK_WORD.get(sub, "landmark")
    return (sub or "region").lower()


def where_words(side, fa, dep):
    ahead = "well ahead" if fa > 35 else "ahead" if fa > 10 else "straight out" if fa >= -10 else "behind"
    down = "near the horizon" if dep < 7 else "a little below the horizon" if dep < 18 else \
        "well below the horizon" if dep < 32 else "steeply down"
    rel = 90 - fa  # degrees from the nose
    hour = round(rel / 30) if side == "R" else 12 - round(rel / 30)
    hour = (hour - 1) % 12 + 1
    return f"{'right' if side == 'R' else 'left'}, {ahead}, {down}", f"{hour} o'clock"


# ------------------------------------------------------------------ main
def predict(date: dt.date, flight: str | None = None, orig: str | None = None, dest: str | None = None,
            dep: str | None = None, takeoff_utc: dt.datetime | None = None, seat: str | None = None,
            actype: str | None = None, exclude_days=(), no_aeroapi: bool = False, today: dt.date | None = None,
            log=print, id: str | None = None, cloud_mode: str = "clear", landmarks: str | None = None) -> dict:
    """cloud_mode 'clear' (default): assume a clear view; a forecast, when the date is within range, is only reported
    as an outlook. 'full': chances are scaled by the forecast or, further out, an ERA5 climatology.
    landmarks 'on' (default): Wikidata landmarks along the route, fetched per cell and cached; 'offline': cached cells
    only; 'off': none. The default can be set with PLANEVIEW_LANDMARKS."""
    import os
    landmarks = landmarks or os.environ.get("PLANEVIEW_LANDMARKS", "on")
    if landmarks not in ("on", "offline", "off"):
        raise ValueError("landmarks must be on, offline or off")
    T_start = time.time()
    info = FL.resolve(flight, orig, dest, no_aeroapi=no_aeroapi, log=log)
    A = AP.airports()
    o, d = A.get(info["orig"]), A.get(info["dest"])
    oc, dc = A.code(A.by[info["orig"]]), A.code(A.by[info["dest"]])
    o_tz, d_tz = AP.timezone(o["lat"], o["lon"]), AP.timezone(d["lat"], d["lon"])
    log(f"{flight or ''} {oc} -> {dc} on {date}")

    legs = route.collect(oc, dc, exclude_days=exclude_days, log=log)
    for lg in legs:
        lg["tz"] = o_tz
    fcs = info.get("callsign")
    airline = info.get("airline_icao")
    from .sources import aeroapi
    keyed = (not no_aeroapi) and bool(aeroapi.key())
    if keyed and flight and takeoff_utc is None and not dep:
        sch = aeroapi.schedule(date, o["icao"], d["icao"], airline, flight)
        if sch:
            info.update(sch)
            if not actype and sch.get("sched_type"):
                actype = sch["sched_type"]
            log(f"  AeroAPI schedule: departs {dt.datetime.fromtimestamp(sch['sched_dep_utc'], dt.UTC):%H:%M} UTC")
    own = [lg for lg in legs if fcs and lg.get("cs") == fcs and lg.get("takeoff")]
    # aircraft type
    if not actype:
        pool = own or [lg for lg in legs if airline and (lg.get("cs") or "").startswith(airline)] or legs
        types = Counter(lg.get("type") for lg in pool if lg.get("type"))
        actype = types.most_common(1)[0][0] if types else None
    # takeoff time
    t_src = None
    if takeoff_utc is not None:
        T0 = takeoff_utc.timestamp()
        t_src = "given (actual takeoff)"
    elif dep:
        hh, mm = map(int, dep.split(":"))
        local = dt.datetime(date.year, date.month, date.day, hh, mm, tzinfo=ZoneInfo(o_tz))
        T0 = local.timestamp() + TAXI_MIN * 60
        t_src = f"scheduled {dep} {o_tz} + {TAXI_MIN} min taxi"
    elif info.get("sched_dep_utc"):
        T0 = info["sched_dep_utc"] + TAXI_MIN * 60
        t_src = "AeroAPI schedule + taxi"
    elif own or _one_daily(legs, airline):
        pool = own or _one_daily(legs, airline)
        lm = sorted(route._local_minutes(lg["takeoff"], o_tz) for lg in pool)
        m = lm[len(lm) // 2]
        local = dt.datetime(date.year, date.month, date.day, m // 60, m % 60, tzinfo=ZoneInfo(o_tz))
        T0 = local.timestamp()
        who = fcs if own else f"{airline} on this route (flown as {pool[0].get('cs')})"
        t_src = f"usual takeoff time of {who} on {len(pool)} past days"
    else:
        raise ValueError(f"No departure time: give --dep HH:MM (local time at {oc})")
    dep_min = route._local_minutes(T0, o_tz)
    # airlines often fly a number under another, alphanumeric callsign: the airline's usual callsign at this
    # takeoff time stands in for the flight's own past days
    flown_as = None
    if airline and not own:
        near = Counter(lg["cs"] for lg in legs if (lg.get("cs") or "").startswith(airline) and lg.get("takeoff")
                       and abs((route._local_minutes(lg["takeoff"], o_tz) - dep_min + 720) % 1440 - 720) <= 45)
        if near and near.most_common(1)[0][1] >= 3:
            flown_as, n_as = near.most_common(1)[0]
            log(f"  probably flown as {flown_as}: {n_as} past days take off within 45 min of {dep_min // 60:02d}:{dep_min % 60:02d}")

    w = route.weigh(legs, flight_cs=flown_as or fcs, airline=airline, dep_local_min=dep_min, date=date)
    filed_note = None
    if keyed:
        from . import filed
        try:
            if fcs:
                filed.learn_fixes(fcs, orig=oc, dest=dc, log=log)
            sks = filed.skeletons(oc, dc, log=log)
            legs, w, filed_note = filed.reweight(legs, w, sks, oc, dc, log=log)
            for lg in legs:
                lg.setdefault("tz", o_tz)
                lg.setdefault("dep_rwy", None); lg.setdefault("arr_rwy", None)
        except Exception as e:  # filed routes are a refinement: never fail the prediction over them
            log(f"  filed routes skipped: {e}")
    vars_ = route.variants(legs, w, oc, dc)
    s = SEAT.parse(seat, actype)
    sides = [s["side"]] if s["side"] else ["L", "R"]

    # tracks at evaluation resolution
    trks = [V.track_arrays(lg["t"], lg["lat"], lg["lon"], lg["alt"]) for lg in legs]
    C = G.Corridor(np.concatenate([t["lat"] for t in trks]), np.concatenate([t["lon"] for t in trks]))
    feats = G.build(C, log=log, airports=[(o["icao"], o["lat"], o["lon"]), (d["icao"], d["lat"], d["lon"])],
                    landmarks={"on": True, "offline": "offline", "off": False}[landmarks])
    idx = V.FeatureIndex(feats)

    cloud = weather.Cloud(date, today=today, log=log)
    use_cloud = cloud_mode == "full"
    if use_cloud:
        sl = np.concatenate([t["lat"][::8] for t in trks]); so = np.concatenate([t["lon"][::8] for t in trks])
        cloud.prepare(sl, so)

    # minutes from takeoff to landing on each past flight (with the unseen last seconds of an approach estimated)
    durs = np.array([(lg["landing"] - lg["takeoff"]) / 60 if lg.get("landing") and lg.get("takeoff")
                     else trk["t"][-1] / 60 for lg, trk in zip(legs, trks)])
    terrain = V.Terrain() if os.environ.get("PLANEVIEW_TERRAIN", "on") != "off" else None
    per = defaultdict(list)  # (fi, side) -> [(leg k, res)]
    for k, (lg, trk) in enumerate(zip(legs, trks)):
        sun_el = geo.sun_az_el(T0 + trk["t"], trk["lat"], trk["lon"])[1]
        res = V.evaluate(idx, trk, sun_el, wing=s["wing"], terrain=terrain)
        for key, r in res.items():
            if key[1] in sides:
                per[key].append((k, r))
    log(f"  visibility: {len(per)} feature-sides seen on at least one of {len(legs)} past flights"
        + (f" ({terrain.fetched} terrain tiles fetched)" if terrain is not None and terrain.fetched else ""))

    events = {sd: [] for sd in sides}
    for (fi, sd), hits in per.items():
        f = feats[fi]
        ws = np.array([w[k] for k, _ in hits])
        tt = np.array([trks[k]["t"][r["i"]] for k, r in hits]) / 60
        q = np.array([r["q"] for _, r in hits])
        alt = np.array([trks[k]["alt_ft"][r["i"]] for k, r in hits])
        pc = cloud.at([r["plat"] for _, r in hits], [r["plon"] for _, r in hits], T0 + tt * 60, alt) if use_cloud \
            else np.ones(len(hits))
        p_geom = float(ws.sum())
        p_see = float(np.sum(ws * pc * np.clip(q / 0.45, 0, 1)))
        if p_see < 0.03:
            continue
        o_ = np.argsort(tt)
        cw = np.cumsum(ws[o_]) / ws.sum()
        pick = lambda p: float(tt[o_][min(len(o_) - 1, np.searchsorted(cw, p))])
        t_med, t_lo, t_hi = pick(0.5), pick(0.1), pick(0.9)
        # the same sighting counted back from landing. A slow cruise (wind, routing) shifts everything after it:
        # on one Faro-London flight the English coast came 13 min later than forecast from takeoff, 2 min from
        # landing. A hold on arrival does the opposite (a coastal town 1 min out from takeoff, 10 from landing).
        # So a sighting in the second half is given from landing only when past flights agree better
        # from that end than from takeoff.
        te = np.array([durs[k] for k, _ in hits]) - tt
        oe = np.argsort(te)
        cwe = np.cumsum(ws[oe]) / ws.sum()
        picke = lambda p: float(te[oe][min(len(oe) - 1, np.searchsorted(cwe, p))])
        e_med, e_lo, e_hi = picke(0.5), picke(0.1), picke(0.9)
        kb = int(np.argmax(ws * q))
        best = hits[kb][1]
        btr = trks[hits[kb][0]]
        ac = [round(float(btr["lat"][best["i"]]), 4), round(float(btr["lon"][best["i"]]), 4),
              int(btr["alt_ft"][best["i"]]), round(float(btr["course"][best["i"]]), 1)]
        brg = round(float(geo.bearing(ac[0], ac[1], best["plat"], best["plon"])), 1)
        span = (trks[hits[kb][0]]["t"][best["i1"]] - trks[hits[kb][0]]["t"][best["i0"]]) / 60
        S = salience(f)
        E = S * p_see * (0.5 + float(np.average(q, weights=ws)))
        wh, clock = where_words(sd, best["fa"], best["dep"])
        events[sd].append(dict(
            fi=fi, id=f["id"], name=f["name"], local=f.get("local") if f.get("local") != f["name"] else None,
            kind=f["kind"], what=describe(f), lat=round(f["lat"], 5), lon=round(f["lon"], 5),
            at=[round(best["plat"], 4), round(best["plon"], 4)], ac=ac, brg=brg, t_best=round(float(btr["t"][best["i"]]) / 60, 1),
            side=sd, t=round(t_med, 1), t_end=round(e_med, 1), t_end_lo=round(e_lo, 1), t_end_hi=round(e_hi, 1),
            anchor="landing" if e_med < t_med and (e_hi - e_lo) < (t_hi - t_lo) else "takeoff",
            night_as="lights" if f["kind"] == "settlement" or idx.lit[fi] > 0 else "dark",
            t_lo=round(t_lo, 1), t_hi=round(t_hi, 1), span_min=round(span, 1),
            fa=round(best["fa"]), dep=round(best["dep"]), d_km=round(best["d"], 1), where=wh, clock=clock,
            alt_ft=int(trks[hits[kb][0]]["alt_ft"][best["i"]]), p_geom=round(p_geom, 3), p_see=round(p_see, 3),
            p_clear=round(float(np.average(pc, weights=ws)), 2), q=round(float(np.average(q, weights=ws)), 3),
            salience=round(S, 2), score=round(E, 3), legs=len(hits), pop=f.get("pop"), elev=f.get("elev"),
            area_km2=f.get("area_km2"), fame=f.get("fame"), src=f.get("src"),
            broad=bool(f.get("broad")), qid=f.get("qid")))

    # light at each event (on the leg nearest the median)
    for sd in sides:
        for e in events[sd]:
            _, el = geo.sun_az_el(T0 + e["t"] * 60, e["at"][0], e["at"][1])
            e["sun_el"] = round(float(el), 1)
            e["light"] = "day" if el >= 0 else "twilight" if el >= -6 else "night"

    timeline = {sd: select(events[sd], legs, trks, w) for sd in sides}
    med = legs[vars_[0]["medoid"]] if vars_ else legs[0]
    mtrk = trks[vars_[0]["medoid"]] if vars_ else trks[0]
    ctx = context(mtrk, T0, o_tz, feats, sides)

    dur = float(np.average(durs, weights=w))
    if use_cloud:
        cloud_note = cloud.source
    elif cloud.mode == "forecast":
        try:
            k = np.arange(0, len(mtrk["t"]), 12)
            cloud.prepare(mtrk["lat"][k], mtrk["lon"][k])
            pcl = cloud.at(mtrk["lat"][k], mtrk["lon"][k], T0 + mtrk["t"][k], mtrk["alt_ft"][k])
            cloud_note = f"clear view assumed; Open-Meteo forecast outlook: {round(100 * float(np.mean(pcl)))}% chance " \
                         f"of seeing the ground along the route"
        except Exception:
            cloud_note = "clear view assumed (forecast unavailable)"
    else:
        cloud_note = "clear view assumed (too far ahead for a forecast)"
    pred = dict(
        schema=SCHEMA, id=id or f"{date.isoformat()}_{(flight or oc + '-' + dc).upper()}",
        generated=dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        flight=dict(number=flight, callsign=fcs, flown_as=flown_as, airline=info.get("airline"), airline_icao=airline,
                    date=date.isoformat(), orig=_ap(o, oc, o_tz), dest=_ap(d, dc, d_tz),
                    aircraft=actype, aircraft_name=SEAT.type_name(actype),
                    takeoff_utc=dt.datetime.fromtimestamp(T0, dt.UTC).isoformat(timespec="minutes"),
                    takeoff_local=dt.datetime.fromtimestamp(T0, ZoneInfo(o_tz)).strftime("%H:%M"),
                    takeoff_source=t_src, duration_min=round(dur), seat=s, sides=sides),
        sources=dict(route=_route_source(legs), filed=filed_note or ("not used (--no-aeroapi)" if no_aeroapi else info.get("filed_note") or "none"),
                     weather=cloud_note, gazetteer="GeoNames (CC BY 4.0), Natural Earth (public domain), Wikidata (CC0)",
                     tracks="adsb.lol GitHub archives (ODbL)",
                     days=sorted({lg["day"] for lg in legs if lg.get("day")}),
                     excluded_days=list(exclude_days)),
        view=V.VIEW,
        variants=[dict(v, legs=None, track=_track_json(trks[v["medoid"]], legs[v["medoid"]]),
                       medoid=dict(cs=legs[v["medoid"]].get("cs"), day=legs[v["medoid"]].get("day"),
                                   kind=legs[v["medoid"]]["kind"])) for v in vars_],
        history=[dict(cs=lg.get("cs"), day=lg.get("day"), kind=lg["kind"], w=round(float(w[k]), 4),
                      dep_rwy=lg.get("dep_rwy"), arr_rwy=lg.get("arr_rwy"), type=lg.get("type"),
                      track=_track_json(trks[k], lg, thin=True)) for k, lg in enumerate(legs)],
        timeline=timeline, context=ctx,
        candidates={sd: [{k: v for k, v in e.items() if k != "fi"}
                         for e in sorted(events[sd], key=lambda e: -e["score"]) if e["p_see"] >= 0.05]
                    for sd in sides},
        corridor=_bundle(feats, events, sides),
        stats=dict(features=len(feats), legs=len(legs), seconds=round(time.time() - T_start)),
    )
    for v in pred["variants"]:
        v.pop("legs", None)
    return pred


def _one_daily(legs, airline):
    """The airline's legs on this route when they look like one daily flight (one per day, takeoffs within 2 h)."""
    if not airline:
        return []
    pool = [lg for lg in legs if (lg.get("cs") or "").startswith(airline) and lg.get("takeoff")]
    days = Counter(lg["day"] for lg in pool)
    if not pool or max(days.values()) > 1:
        return []
    tod = sorted((lg["takeoff"] % 86400) / 60 for lg in pool)
    return pool if tod[-1] - tod[0] <= 120 else []


def _ap(a, code, tz):
    return dict(code=code, icao=a["icao"], name=a["name"], city=AP.display_city(a), municipality=a["city"],
                lat=a["lat"], lon=a["lon"], tz=tz)


def _route_source(legs):
    c = Counter(lg["kind"] for lg in legs)
    return ", ".join(f"{n} {k}" for k, n in c.items()) + " past flights"


def _track_json(trk, lg, thin=False):
    step = 4 if thin else 1
    pts = [[round(float(trk["t"][i]) / 60, 2), round(float(trk["lat"][i]), 4), round(float(trk["lon"][i]), 4),
            int(trk["alt_ft"][i])] for i in range(0, len(trk["t"]), step)]
    if thin and pts[-1][0] != round(float(trk["t"][-1]) / 60, 2):
        i = len(trk["t"]) - 1
        pts.append([round(float(trk["t"][i]) / 60, 2), round(float(trk["lat"][i]), 4),
                    round(float(trk["lon"][i]), 4), int(trk["alt_ft"][i])])
    return pts


def _slim(e):
    return {k: e[k] for k in ("id", "name", "kind", "what", "t", "t_lo", "t_hi", "fa", "dep", "d_km", "p_see",
                              "score", "lat", "lon") if k in e}


def _bundle(feats, events, sides):
    """The features worth carrying to a companion app: everything that reached the candidate lists."""
    keep = {e["fi"] for sd in sides for e in events[sd]}
    out = []
    for fi in sorted(keep):
        f = feats[fi]
        out.append(dict(id=f["id"], name=f["name"], kind=f["kind"], sub=f.get("sub"), lat=round(f["lat"], 5),
                        lon=round(f["lon"], 5), z=f.get("z", 0), size_km=round(f.get("size_km", 1), 2),
                        pop=f.get("pop"), elev=f.get("elev"), area_km2=f.get("area_km2"), fame=f.get("fame"),
                        salience=round(salience(f), 2),
                        pts=[[round(a, 4), round(b, 4)] for a, b in f["pts"][:: max(1, len(f["pts"]) // 60)]]
                        if len(f["pts"]) > 1 else None, outline=f.get("outline")))
    return out


# ------------------------------------------------------------------ selection
# Tuned against six flights' window photographs: the best recall of the prominent places named in them within about
# 24 sightings an hour.
# town_km / town_pop and _minor are not tuned: they stop a stadium headlining over its own town, and nameless river
# islets 40 km away headlining a slot.
# also_max: with parks, harbours and airports in the gazetteer, lists of 12 fill up and certain sightings fall off
# the end; 16 is a compromise with reading it.
# also_kind_max: no more than this many of one kind among a headline's companions (in an Alpine valley the lists
# would otherwise be sixteen summits).
SELECT = dict(also_kind_max=8, town_km=6.0, town_pop=10000, slot_low=3.0, slot_high=5.0, rich_pct=80, cap_rich=2, cap_rich_high=True, also_max=16,
              also_ratio=0.1, also_fa=35.0, also_sal=0.9, also_fame=2, also_distinct_p=0.3, distinct_bonus=1.0)


# peaks are among them: with summits above the aircraft in view, a valley approach would otherwise headline
# whichever minor summits happen to be nearest
P_MINOR_KINDS = ("island", "coast", "lake", "sea", "landmark", "peak")


def _minor(e) -> bool:
    """Too slight to headline a slot unless close by: a small island, point, bay, pond or landmark that Wikipedia
    barely knows. It can still be listed as 'also'."""
    return e["kind"] in P_MINOR_KINDS and (e.get("fame") or 0) < 2 and \
        (e.get("area_km2") or 0) < 1 and e["d_km"] > 10


def select(evs, legs=None, trks=None, w=None, P=None):
    """Thin the candidates into a readable timeline. One headline per time slot (slot_low minutes below 15,000 ft,
    slot_high above), two where the view is rich (both in the top fifth by score); the rest of a slot become 'also
    visible' companions of the nearest headline in time and direction. Parameters are in SELECT."""
    P = dict(SELECT, **(P or {}))
    evs = [dict(e) for e in evs if not e["broad"]]
    distinct_kinds = ("coast", "island", "lake", "sea", "landmark", "river")
    for e in evs:
        e["_score"] = e["score"] * (P["distinct_bonus"] if e["kind"] in distinct_kinds else 1.0)
    evs.sort(key=lambda e: -e["_score"])
    if not evs:
        return []
    top = evs[0]["_score"]
    rich = float(np.percentile([e["_score"] for e in evs], P["rich_pct"])) if len(evs) >= 10 else top
    heads = []
    slots = defaultdict(list)
    towns = [e for e in evs if e["kind"] == "settlement" and (e.get("pop") or 0) >= P["town_pop"]]
    for e in evs:
        if e["_score"] < 0.02 * top and e["_score"] < 0.3:
            break
        low = e["alt_ft"] < 15000
        slot_len = P["slot_low"] if low else P["slot_high"]
        sk = (slot_len, int(e["t"] // slot_len))
        if any(G.norm(h["name"]) == G.norm(e["name"]) and abs(h["t"] - e["t"]) < 10 for h in heads):
            continue
        is_rich = e["_score"] >= rich and all(h["_score"] >= rich for h in slots[sk])
        cap = P["cap_rich"] if is_rich and (low or P["cap_rich_high"]) else 1
        if len(slots[sk]) < cap and not _minor(e):
            # a landmark inside a town that is itself in view: the town is the headline and the landmark its first
            # companion ("Crawley - also: Broadfield Stadium", not the other way round)
            town = None
            if e["kind"] == "landmark":
                near_t = [s for s in towns if abs(s["t"] - e["t"]) <= slot_len and
                          geo.dist_km(s["lat"], s["lon"], e["lat"], e["lon"]) <= P["town_km"]]
                if near_t:
                    town = max(near_t, key=lambda s: s["_score"])
            if town is not None:
                h = next((x for x in heads if x["id"] == town["id"]), None)
                if h is None:
                    h = dict(town, also=[])
                    slots[sk].append(h)
                    heads.append(h)
                if G.norm(e["name"]) not in {G.norm(a["name"]) for a in h["also"]} | {G.norm(h["name"])}:
                    h["also"].insert(0, {k: e[k] for k in ("name", "kind", "what", "d_km", "fa", "dep", "lat", "lon")})
                continue
            h = dict(e, also=[])
            slots[sk].append(h)
            heads.append(h)
            continue
        near = [h for h in heads if abs(h["t"] - e["t"]) <= slot_len and abs(h["fa"] - e["fa"]) < P["also_fa"]]
        distinct = e["kind"] in distinct_kinds
        if near and e["_score"] >= P["also_ratio"] * min(h["_score"] for h in near) and \
                (e["salience"] >= P["also_sal"] or (e.get("fame") or 0) >= P["also_fame"] or
                 (distinct and e["p_see"] >= P["also_distinct_p"])):
            h = min(near, key=lambda h: abs(h["t"] - e["t"]) + abs(h["fa"] - e["fa"]) / 10)
            names = {G.norm(h["name"])} | {G.norm(a["name"]) for a in h["also"]}
            if sum(1 for a in h["also"] if a["kind"] == e["kind"]) >= P["also_kind_max"]:
                continue
            if len(h["also"]) < P["also_max"] and G.norm(e["name"]) not in names:
                h["also"].append({k: e[k] for k in ("name", "kind", "what", "d_km", "fa", "dep", "lat", "lon")})
    heads.sort(key=lambda e: e["t"])
    for h in heads:
        h.pop("fi", None)
        h.pop("_score", None)
        h["confidence"] = "likely" if h["p_see"] >= 0.55 else "possible" if h["p_see"] >= 0.25 else "if lucky"
    return heads


# ------------------------------------------------------------------ context along the main variant
def context(trk, T0, o_tz, feats, sides):
    out = []
    t = trk["t"] / 60
    lat, lon, alt = trk["lat"], trk["lon"], trk["alt_ft"]
    # top of climb / top of descent
    amax = alt.max()
    hi = np.flatnonzero(alt >= amax - 3000)
    if len(hi):
        out.append(dict(type="toc", t=round(float(t[hi[0]]), 1), text=f"Top of climb, about {round(amax, -3):,.0f} ft"))
        out.append(dict(type="tod", t=round(float(t[hi[-1]]), 1), text="Top of descent"))
    # coast crossings
    land = G.on_land(lat, lon)
    ch = np.flatnonzero(land[1:] != land[:-1]) + 1
    last_t = -99
    for i in ch:
        if t[i] - last_t < 8:
            continue
        last_t = t[i]
        c = G.country_at(lat[min(i + 2, len(lat) - 1)], lon[min(i + 2, len(lon) - 1)]) if land[i] else \
            G.country_at(lat[max(i - 2, 0)], lon[max(i - 2, 0)])
        nm = c["name"] if c else G.nearest_country(lat[i], lon[i])
        out.append(dict(type="coast_in" if land[i] else "coast_out", t=round(float(t[i]), 1),
                        lat=round(float(lat[i]), 3), lon=round(float(lon[i]), 3),
                        text=(f"Cross the coast into {nm}" if land[i] else f"Leave the coast of {nm}").strip()))
    # countries
    prev = None
    for i in range(0, len(t), 4):
        c = G.country_at(lat[i], lon[i]) if land[i] else None
        nm = c["name"] if c else None
        if nm and nm != prev:
            if prev is not None or i > 0:
                out.append(dict(type="country", t=round(float(t[i]), 1), text=f"Over {nm}"))
            prev = nm
    # sunrise / sunset at the aircraft
    _, el = geo.sun_az_el(T0 + trk["t"], lat, lon)
    el_h = el + geo.dip_deg(alt * geo.FT)  # the sun sets later seen from altitude
    for i in np.flatnonzero(np.sign(el_h[1:]) != np.sign(el_h[:-1])) + 1:
        az, _ = geo.sun_az_el(T0 + trk["t"][i], lat[i], lon[i])
        rel = float(geo.wrap180(az - trk["course"][i]))
        out.append(dict(type="sunrise" if el_h[i] > 0 else "sunset", t=round(float(t[i]), 1),
                        side="R" if rel > 0 else "L",
                        text=f"{'Sunrise' if el_h[i] > 0 else 'Sunset'} on the {'right' if rel > 0 else 'left'}"))
    if el_h[0] < 0 and not any(o_["type"] == "sunrise" for o_ in out):
        out.append(dict(type="night", t=0.0, text="Night flight: look for city lights"))
    # broad features: seas, regions, ranges the route passes over or beside
    for f in feats:
        if not f.get("broad") or len(f["pts"]) < 2:
            continue
        if f["kind"] != "sea" and (f.get("area_km2") or 0) > 150000:
            continue  # the Balkans, Scandinavia: too broad to point at
        dk = geo.dist_km(lat[:, None], lon[:, None], f["pts"][None, :, 0], f["pts"][None, :, 1]).min(1)
        over = np.flatnonzero(dk < (20 if f["kind"] == "sea" else 35))
        if len(over) < 3:
            continue
        out.append(dict(type=f["kind"], t=round(float(t[over[0]]), 1), t_end=round(float(t[over[-1]]), 1),
                        text=f"{'Over' if dk.min() < 8 else 'Beside'} the {f['name']}" if f["kind"] == "sea" else
                        f"{f['name']} ({describe(f)})", name=f["name"]))
    # sun side for the whole flight (glare / glories)
    az, el = geo.sun_az_el(T0 + trk["t"], lat, lon)
    rel = geo.wrap180(az - trk["course"])
    day = el > 5
    if day.any():
        right = float(np.mean(rel[day] > 0))
        out.append(dict(type="sun_side", t=0.0, right_share=round(right, 2),
                        text=("The sun is mostly on the right: glare there, glories and the shadow on the left"
                              if right > 0.65 else "The sun is mostly on the left: glare there, glories and the shadow "
                              "on the right" if right < 0.35 else "The sun moves between sides during the flight")))
    out.sort(key=lambda e: e["t"])
    return out

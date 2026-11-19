"""plane-view command line.

  planeview doctor                             check tools and data sources on this machine (run this first)
  planeview history update [N]                 add whichever of the last N days (default 7) are missing from the library
  planeview history add 2026-11-09 ...         add chosen archive days (also A..B, and A..B/7 = one day a week)
  planeview history status [days]              what the route library holds and how big it is
  planeview pairs [--min 5]                    airport pairs in the library (to pick routes to try)
  planeview predict --date 2027-03-14 --from BRU --to AGP --dep 10:20 [--seat 23F] [--flight SN3733]
                    [--aircraft A320] [--cloud full] [--no-aeroapi] [--exclude-days ...] [--no-pdf]
  planeview build out/<id> ...                 rebuild page, views, imagery and PDFs from saved predictions
  planeview site                               rebuild site/index.html from every built flight
  planeview serve [PORT]                       serve site/ at http://localhost:8765/
  planeview pack out/<id> ... [--detail high] [--icloud]   flight packs for the iPhone and iPad app
  planeview landmarks --from LHR --to PHX      fetch every landmark cell for a route now (long-haul: about an hour)
  planeview aeroapi                            AeroAPI spend this month against the budget (optional service)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time

from . import cache


def _log(msg):
    print(msg, flush=True)


def cmd_predict(a):
    from .predict import predict
    from .output import pdf, site, web
    pred = predict(dt.date.fromisoformat(a.date), flight=a.flight, orig=a.orig, dest=a.dest, dep=a.dep,
                   seat=a.seat, actype=a.aircraft, exclude_days=a.exclude_days or (), no_aeroapi=a.no_aeroapi,
                   log=_log, id=a.id, cloud_mode=a.cloud, landmarks=a.landmarks)
    out = cache.OUT / pred["id"]
    out.mkdir(parents=True, exist_ok=True)
    (out / "prediction.json").write_text(json.dumps(pred, default=_np, indent=None))
    d = web.build(pred)
    if not a.no_pdf:
        for p in pdf.render_all(d):
            _log(f"  pdf: {p}")
    site.build(log=_log)
    _summary(pred)
    _log(f"  prediction: {out / 'prediction.json'}")
    _log(f"  page:       {cache.SITE / 'index.html'}  (open it in a browser, or run: planeview serve)")


def _np(o):
    import numpy as np
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    return str(o)


def _summary(pred):
    for sd, tl in pred["timeline"].items():
        n = len(tl)
        nl = sum(1 for e in tl if e["confidence"] == "likely")
        top = sorted(tl, key=lambda e: -e["score"])[:6]
        _log(f"  {'left' if sd == 'L' else 'right'}: {n} sightings, {nl} likely; top: " +
             ", ".join(f"{e['name']} +{round(e['t'])}" for e in top))


def cmd_build(a):
    """Rebuild page data, views, imagery and PDFs from saved predictions (no re-prediction)."""
    from pathlib import Path
    from .output import pdf, site, web
    for p in a.predictions:
        pp = Path(p)
        pp = pp / "prediction.json" if pp.is_dir() else pp
        pred = json.loads(pp.read_text())
        if a.pdf_only:
            from .output.web import SITE
            d = SITE / "f" / pred["id"].lower()
        else:
            d = web.build(pred)
        if not a.no_pdf:
            for f in pdf.render_all(d):
                _log(f"  pdf: {f}")
    site.build(log=_log)


def cmd_pack(a):
    """Flight packs for the iPhone app, from saved predictions (pictures come from the built page)."""
    from pathlib import Path
    from . import pack
    for p in a.predictions:
        pp = Path(p)
        pp = pp / "prediction.json" if pp.is_dir() else pp
        pred = json.loads(pp.read_text())
        _log(f"{pred['id']}")
        out = pack.build(pred, reach_km=a.reach, with_map=not a.no_map, landmarks=a.landmarks,
                         fetch_relief=a.relief == "fetch", detail=a.detail,
                         out_path=(cache.OUT / "packs" / f"{pred['id']}-hd.planeview") if a.detail == "high" else None, log=_log)
        if a.icloud:
            pack.to_icloud(out, log=_log)


def cmd_history(a):
    """The route library: add days, catch up with the newest ones, or report what is held."""
    from .sources import archive, library

    def log(msg):
        print(time.strftime("%H:%M:%S"), msg, flush=True)

    if a.action == "status":
        con = library.connect()
        st = library.day_stats(con)
        con.close()
        sz = library.size()
        if not st:
            print("The route library is empty. Add days with: planeview history update 14")
            return
        ds = [dt.date.fromisoformat(s["day"]) for s in st]
        gaps = sorted({(b - a_).days for a_, b in zip(ds, ds[1:])})
        if len(ds) == 1:
            print(f"1 day, {ds[0]}")
        else:
            print(f"{sz['days']} days, {ds[0]} to {ds[-1]} (gaps between days: {gaps[0]}-{gaps[-1]} days)")
        print(f"{sz['legs']:,} legs, {sz['file_bytes'] / 2**30:.2f} GiB on disk "
              f"({sz['file_bytes'] / max(1, sz['legs']):.0f} bytes a leg) in {library.DB}")
        if a.days:
            for s in st:
                print(f"  {s['day']}  {s.get('legs', 0):>7,} legs  {s.get('release', '')}")
        return
    if a.action == "update":
        n = int(a.days[0]) if a.days else 7
        days = [d for d in archive.latest_days(n) if d.isoformat() not in set(library.days())]
        if not days:
            log(f"the last {n} days are already in the library")
            return
    else:
        if not a.days:
            sys.exit("history add: give days (YYYY-MM-DD, A..B, or A..B/7 for weekly)")
        days = archive.parse_days(a.days)
    failed = archive.add_days(days, a.workers or 6, log=log)
    if failed:
        log(f"not added: {' '.join(failed)}" + (" (today's and yesterday's archives may not be published yet)"
                                                 if a.action == "update" else ""))
        if a.action == "add":
            sys.exit(1)


def cmd_aeroapi(a):
    """AeroAPI spend this month: the local ledger and FlightAware's own figure, against the budget."""
    from .sources import aeroapi
    if not aeroapi.key():
        print("No AeroAPI key (Keychain service plane-view-aeroapi, or AEROAPI_KEY).")
        return
    u = aeroapi.usage()
    fa = "unavailable" if u["flightaware"] is None else f"${u['flightaware']:.3f}"
    print(f"{u['month']}: ledger ${u['ledger']:.3f} over {u['calls']} calls; FlightAware reports {fa}; "
          f"budget ${u['budget']:.2f} (free credit ${u['free_credit']:.2f})")


def cmd_doctor(a):
    """What this machine has and which sources answer: the first thing to run on a new install."""
    import shutil
    import subprocess

    from .output import pdf
    from .sources import aeroapi, library
    ok = lambda b: "ok     " if b else "MISSING"
    print(f"home    {cache.HOME}\ncache   {cache.CACHE}")
    print(f"{ok(shutil.which('curl'))} curl (every download goes through it)")
    ch = pdf.chrome()
    print(f"{ok(ch)} Chrome-family browser for the PDF guides{': ' + ch if ch else ' (optional; set PLANEVIEW_CHROME)'}")
    try:
        sz = library.size()
        ds = library.days()
        print(f"{ok(sz['days'])} route library: {sz['days']} days" + (f" ({ds[0]} to {ds[-1]}), {sz['legs']:,} legs, "
              f"{sz['file_bytes'] / 2**30:.2f} GiB" if ds else " - start with: planeview history update 14"))
    except Exception as e:
        print(f"MISSING route library: {e}")
    print(f"{'ok     ' if aeroapi.key() else 'absent '} AeroAPI key (optional: schedules, US filed routes)")
    if a.offline:
        return
    today = dt.datetime.now(dt.UTC).date() - dt.timedelta(days=3)
    tag = f"v{today:%Y.%m.%d}-planes-readsb-prod-0"
    checks = [
        ("adsb.lol archive on GitHub (past routes)", f"https://github.com/adsblol/globe_history_{today.year}/releases/download/{tag}/{tag}.tar.aa"),
        ("OurAirports (airports, runways)", "https://davidmegginson.github.io/ourairports-data/airports.csv"),
        ("standing data (flight number -> airports)", "https://vrs-standing-data.adsb.lol/routes.csv.gz"),
        ("adsbdb (airline names; optional)", "https://api.adsbdb.com/v0/callsign/DLH400"),
        ("GeoNames (place names)", "https://download.geonames.org/export/dump/GG.zip"),
        ("Natural Earth, NACIS", "https://naciscdn.org/naturalearth/10m/physical/ne_10m_lakes.zip"),
        ("Natural Earth, S3 mirror", "https://naturalearth.s3.amazonaws.com/10m_physical/ne_10m_lakes.zip"),
        ("Wikidata query service (landmarks)", "https://query.wikidata.org/sparql?query=ASK%7B%7D&format=json"),
        ("Copernicus DEM on AWS (terrain)", "https://copernicus-dem-90m.s3.amazonaws.com/Copernicus_DSM_COG_30_N46_00_E007_00_DEM/Copernicus_DSM_COG_30_N46_00_E007_00_DEM.tif"),
        ("EOxCloudless tiles (overhead images)", "https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2024_3857/default/g/3/2/4.jpg"),
        ("Open-Meteo (cloud outlook; optional)", "https://api.open-meteo.com/v1/forecast?latitude=51&longitude=0&hourly=cloud_cover_low&forecast_days=1"),
    ]
    for name, url in checks:
        r = subprocess.run(["curl", "-sS", "-L", "-o", "/dev/null", "-A", cache.UA, "-r", "0-1023", "--max-time", "20",
                            "-w", "%{http_code} %{time_total}", url], capture_output=True, text=True)
        code, _, secs = r.stdout.partition(" ")
        good = code in ("200", "206")
        print(f"{'ok     ' if good else 'FAILED '} {name}: HTTP {code or '000'} in {float(secs or 0):.1f}s")


def cmd_landmarks(a):
    """Fetch every Wikidata landmark cell along a route now, rather than 120 a prediction. A long-haul route crosses
    a thousand or more cells (about an hour at the service's pace); a prediction made before they are all in simply
    has landmarks near the two ends only."""
    import numpy as np

    from . import gazetteer as G
    from . import route
    from .sources import airports as AP
    from .sources import wikidata
    A = AP.airports()
    oc, dc = A.code(A.by[a.orig.upper()]), A.code(A.by[a.dest.upper()])
    legs = route.collect(oc, dc, log=_log)
    # route points are sparse where the track is straight: fill in so no cell on the way is stepped over
    la, lo = [], []
    for lg in legs:
        d = np.r_[0, np.cumsum(G.geo.dist_km(lg["lat"][:-1], lg["lon"][:-1], lg["lat"][1:], lg["lon"][1:]))]
        x = np.arange(0, d[-1], 25.0)
        la.append(np.interp(x, d, lg["lat"])); lo.append(np.interp(x, d, lg["lon"]))
    lat, lon = np.concatenate(la), np.concatenate(lo)
    found = wikidata.landmarks(lat, lon, has_land=G._cell_has_land, log=_log, max_new=a.max)
    _log(f"  {len(found)} landmarks cached for {oc}-{dc}")


def cmd_site(a):
    from .output import site
    site.build(log=_log)


def cmd_serve(a):
    from .output import site
    site.build(log=_log)
    site.serve(a.port, log=_log)


def cmd_pairs(a):
    from .sources import library
    for fr, to, n in library.pairs(a.min)[:a.top]:
        print(f"{fr}-{to}\t{n}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="planeview", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("predict")
    p.add_argument("--date", required=True)
    p.add_argument("--flight")
    p.add_argument("--from", dest="orig")
    p.add_argument("--to", dest="dest")
    p.add_argument("--dep", help="scheduled departure, local time at the origin, HH:MM")
    p.add_argument("--seat", help="e.g. 16A, 23F, L or R; omit for both sides")
    p.add_argument("--aircraft", help="ICAO type, e.g. B38M, A20N, B789")
    p.add_argument("--exclude-days", nargs="*")
    p.add_argument("--no-aeroapi", action="store_true")
    p.add_argument("--no-pdf", action="store_true")
    p.add_argument("--cloud", choices=["clear", "full"], default="clear",
                   help="clear (default): assume a clear view; full: scale chances by forecast/climatology")
    p.add_argument("--landmarks", choices=["on", "offline", "off"],
                   help="Wikidata landmarks along the route: on (default, fetched and cached), offline (cached only), off")
    p.add_argument("--id")
    p.set_defaults(fn=cmd_predict)
    b = sub.add_parser("build")
    b.add_argument("predictions", nargs="+", help="out/<id> folders or prediction.json files")
    b.add_argument("--no-pdf", action="store_true")
    b.add_argument("--pdf-only", action="store_true", help="only re-render the PDFs from the built page data")
    b.set_defaults(fn=cmd_build)
    pk = sub.add_parser("pack", help="flight packs for the iPhone app: out/packs/<id>.planeview")
    pk.add_argument("predictions", nargs="+", help="out/<id> folders or prediction.json files")
    pk.add_argument("--reach", type=float, default=60.0,
                    help="how far outside every past flight the pack still knows the ground, km (60)")
    pk.add_argument("--no-map", action="store_true", help="leave the offline satellite map out")
    pk.add_argument("--landmarks", choices=["on", "offline", "off"], default="offline",
                    help="Wikidata landmarks in the wider corridor: cached cells only (default), fetch, or none")
    pk.add_argument("--relief", choices=["fetch", "cached"], default="fetch",
                    help="terrain for the 3D view: fetch land cells not yet cached (default; 4 MB each, once), or "
                         "use only what is cached")
    pk.add_argument("--detail", choices=["standard", "high"], default="standard",
                    help="high: for an iPad or a sharper 3D view - the map one zoom deeper, pictures at twice the size, "
                         "fine terrain throughout; about three times the size, written as <id>-hd.planeview")
    pk.add_argument("--icloud", action="store_true", help="also copy each pack to iCloud Drive/PlaneView")
    pk.set_defaults(fn=cmd_pack)
    h = sub.add_parser("history")
    h.add_argument("action", choices=["add", "update", "status"])
    h.add_argument("days", nargs="*", help="add: YYYY-MM-DD, A..B or A..B/7; update: how many recent days (7); "
                                           "status: 'days' lists each day")
    h.add_argument("--workers", type=int)
    h.set_defaults(fn=cmd_history)
    q = sub.add_parser("aeroapi", help="AeroAPI spend this month against the budget")
    q.set_defaults(fn=cmd_aeroapi)
    dr = sub.add_parser("doctor", help="check tools, the library and every data source")
    dr.add_argument("--offline", action="store_true", help="skip the network checks")
    dr.set_defaults(fn=cmd_doctor)
    lm = sub.add_parser("landmarks", help="fetch all Wikidata landmark cells for a route in one go")
    lm.add_argument("--from", dest="orig", required=True)
    lm.add_argument("--to", dest="dest", required=True)
    lm.add_argument("--max", type=int, default=5000, help="most cells to fetch in this run")
    lm.set_defaults(fn=cmd_landmarks)
    s = sub.add_parser("site", help="rebuild site/index.html from every built flight")
    s.set_defaults(fn=cmd_site)
    sv = sub.add_parser("serve", help="serve site/ on this machine")
    sv.add_argument("port", nargs="?", type=int, default=8765)
    sv.set_defaults(fn=cmd_serve)
    pr = sub.add_parser("pairs")
    pr.add_argument("--min", type=int, default=5)
    pr.add_argument("--top", type=int, default=200)
    pr.set_defaults(fn=cmd_pairs)
    a = ap.parse_args(argv)
    try:
        a.fn(a)
    except (ValueError, KeyError, FileNotFoundError) as e:
        # things that can be put right by whoever ran it (no departure time, an unknown airport): say so
        # plainly; set PLANEVIEW_DEBUG=1 for the traceback
        import os
        if os.environ.get("PLANEVIEW_DEBUG"):
            raise
        sys.exit(f"planeview: {e.args[0] if e.args else e}")


if __name__ == "__main__":
    main()

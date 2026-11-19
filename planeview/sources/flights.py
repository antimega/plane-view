"""Flight number -> airline, ATC callsign and airports.

Airline flight numbers use IATA codes (LH400); ATC callsigns use ICAO (DLH400), and airlines often fly a number under
an alphanumeric callsign (a Vueling VY number as VLG1AB, say), so past flights are found by airport pair, and the
callsign only adds weight to the legs that used it.

Which airports a flight serves is asked of three sources, most trustworthy first, and only when --from/--to were not
given:
  1. the route library itself: the pair that callsign actually flew on the sampled days (no network, never stale);
  2. adsbdb.com (a live API, free, no key) - also the source of the airline's name. It is asked once per flight and
     cached for a month; if it is down, an older answer or none is used and the prediction carries on;
  3. VRS standing data as republished daily by adsb.lol (one 4.5 MB file of every known callsign's route, CC0; kept
     for two weeks, and an older copy is used if a refresh fails) - the fallback that needs no live API.
Neither outside source is reliably current (each has been seen to give a flight number a route it no longer flies),
so a disagreement between them is printed, and --from/--to always win.
"""
from __future__ import annotations

import csv
import gzip
import re
import time
from functools import lru_cache

from .. import cache

ADSBDB = "https://api.adsbdb.com/v0/callsign/"
STANDING = "https://vrs-standing-data.adsb.lol/routes.csv.gz"
STANDING_DAYS = 14


def _lookup(cs: str):
    try:
        d = cache.get_json(ADSBDB + cs, f"adsbdb/{cs}.json", max_age_days=30, timeout=20, retries=1)
    except Exception:
        return None
    r = d.get("response") if isinstance(d, dict) else None
    return r.get("flightroute") if isinstance(r, dict) else None


# IATA codes that OpenFlights and adsbdb both still give to a defunct airline (checked November 2026)
IATA_FIX = {"GQ": "SEH"}  # Sky Express (Greece); both sources say Big Sky Airlines (BSY, US, ceased 2008)


def iata_to_icao(code: str) -> str | None:
    """Airline IATA -> ICAO designator. OpenFlights' airlines.dat (ODbL) is no longer maintained and IATA codes are
    reused, so one code often lists several airlines (VY: Vueling and a defunct one, both marked active). The one
    whose callsigns actually appear in the route library wins; with no library evidence, the first active one."""
    import csv
    if code in IATA_FIX:
        return IATA_FIX[code]
    try:
        p = cache.fetch("openflights/airlines.dat",
                        "https://raw.githubusercontent.com/jpatokal/openflights/master/data/airlines.dat")
    except RuntimeError:
        return None
    cands = []
    with open(p, newline="", encoding="utf-8", errors="replace") as fh:
        for r in csv.reader(fh):
            if len(r) > 7 and r[3] == code and len(r[4]) == 3 and r[4].isalpha():
                cands.append((r[7] != "Y", r[4]))          # active first, file order otherwise
    cands = list(dict.fromkeys(c for _, c in sorted(cands, key=lambda x: x[0])))
    if len(cands) > 1:
        seen = {c: _flown(c) for c in cands}
        if max(seen.values()) > 0:
            return max(cands, key=lambda c: seen[c])
    return cands[0] if cands else None


def _flown(icao: str) -> int:
    """How many complete legs in the library flew under this airline's callsigns (0 if there is no library)."""
    from . import library
    try:
        con = library.connect(readonly=True)
        try:
            return con.execute("SELECT COUNT(*) FROM legs WHERE cs >= ? AND cs < ? AND fr IS NOT NULL AND "
                               "too IS NOT NULL", (icao, icao + "~")).fetchone()[0]
        finally:
            con.close()
    except Exception:
        return 0


@lru_cache(maxsize=1)
def _standing() -> dict[str, list[str]]:
    """callsign -> [ICAO airports in order] from the standing-data routes file ({} if it cannot be had)."""
    p = cache.CACHE / "standing-data" / "routes.csv.gz"
    old = not p.exists() or (time.time() - p.stat().st_mtime) / 86400 > STANDING_DAYS
    if old:
        tmp = p.with_name("routes.new.csv.gz")
        if cache.curl(STANDING, tmp, timeout=120) == 200:
            tmp.replace(p)
            cache.record(p.parent / "fetched.json", p.name)
        else:
            tmp.unlink(missing_ok=True)
    if not p.exists():
        return {}
    out = {}
    try:
        with gzip.open(p, "rt", encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                out[r["Callsign"]] = r["AirportCodes"].split("-")
    except (OSError, KeyError, EOFError):
        return {}
    return out


def from_library(cs: str):
    """(from, to, flights, total) for the pair a callsign flew most in the library, or None if it is not clear-cut."""
    from . import library
    try:
        rows = library.callsign_routes(cs)
    except Exception:
        return None
    if not rows:
        return None
    total = sum(r[2] for r in rows)
    fr, to, n, _ = rows[0]
    return (fr, to, n, total) if n >= 2 and n >= 0.6 * total else None


def airports_of(callsigns: list[str], adsbdb=None, log=print):
    """(from, to, source) for a flight: the library's own record, then adsbdb's answer if there is one, then the
    standing data. The two outside sources are both sometimes out of date, so a disagreement is reported."""
    for cs in callsigns:
        hit = from_library(cs)
        if hit:
            return hit[0], hit[1], f"the route library ({cs} flew it on {hit[2]} of its {hit[3]} sampled flights)"
    st = _standing()
    standing = None
    for cs in callsigns:
        stops = st.get(cs)
        if stops and len(stops) == 2:
            standing = (stops[0], stops[1], f"the standing data ({cs})")
            break
        if stops:
            log(f"  {cs} is a multi-stop flight in the standing data ({'-'.join(stops)})")
    if adsbdb:
        a = (adsbdb["origin"]["icao_code"], adsbdb["destination"]["icao_code"])
        if standing and standing[:2] != a:
            log(f"  sources disagree: adsbdb says {a[0]}-{a[1]}, the standing data {standing[0]}-{standing[1]}; "
                f"using adsbdb - give --from and --to to be sure")
        return (adsbdb["origin"]["iata_code"] or a[0], adsbdb["destination"]["iata_code"] or a[1], "adsbdb")
    return standing


def resolve(flight: str | None, orig: str | None, dest: str | None, no_aeroapi=False, log=print) -> dict:
    out = dict(orig=orig.upper() if orig else None, dest=dest.upper() if dest else None)
    if flight:
        fn = flight.upper().replace(" ", "")
        m = re.fullmatch(r"([A-Z0-9]{2})(\d{1,4}[A-Z]?)", fn)
        icao = iata_to_icao(m.group(1)) if m else None
        cands = ([icao + m.group(2)] if icao else []) + [fn]
        if icao:
            out.update(airline_icao=icao, airline_iata=m.group(1), callsign=icao + m.group(2))
        elif re.fullmatch(r"[A-Z]{3}\d[A-Z0-9]{0,4}", fn):
            out.update(airline_icao=fn[:3], callsign=fn)
        # adsbdb: the airline's name, and a second opinion on the airports. Optional throughout.
        fr = _lookup(fn)
        if fr is None and icao:
            fr = _lookup(icao + m.group(2))
        if fr:
            al = fr.get("airline") or {}
            if m and m.group(1) in IATA_FIX and al.get("icao") != icao:
                fr = None   # adsbdb has the airline this code used to belong to (GQ: Big Sky, not Sky Express)
            else:
                out.update(airline=al.get("name"), airline_icao=al.get("icao") or out.get("airline_icao"),
                           airline_iata=al.get("iata") or out.get("airline_iata"),
                           callsign=fr.get("callsign_icao") or out.get("callsign"))
                if out["callsign"] not in cands:
                    cands.insert(0, out["callsign"])
        elif not out.get("callsign"):
            log(f"  {fn}: airline not recognised - past flights of this number get no extra weight")
        if not (out["orig"] and out["dest"]):
            hit = airports_of(cands, adsbdb=fr, log=log)
            if hit:
                out["orig"], out["dest"] = out["orig"] or hit[0], out["dest"] or hit[1]
                log(f"  {fn}: {hit[0]}-{hit[1]} from {hit[2]}")
        elif fr:
            listed = (fr["origin"]["iata_code"] or fr["origin"]["icao_code"],
                      fr["destination"]["iata_code"] or fr["destination"]["icao_code"])
            if listed != (out["orig"], out["dest"]):
                log(f"  note: adsbdb lists {fn} as {listed[0]}-{listed[1]}; using {out['orig']}-{out['dest']} as given")
    if not out["orig"] or not out["dest"]:
        raise ValueError("Airports unknown: give --from and --to")
    if not no_aeroapi:
        from . import aeroapi
        extra = aeroapi.flight_info(flight, out, log=log)
        if extra:
            out.update(extra)
    return out

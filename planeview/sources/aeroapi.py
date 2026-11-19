"""FlightAware AeroAPI v4 - optional. Everything here returns None (never raises) when there is no key, no credit, a
rate limit or no network, and the prediction carries on from the archived ADS-B tracks.

The key is read from the macOS Keychain (service "plane-view-aeroapi"), put there with
    security add-generic-password -a "$USER" -s plane-view-aeroapi -w
or from the AEROAPI_KEY environment variable. It is never written to disk or logged.

Endpoints used (Personal tier; $5 free credit a month; 10 result sets a minute):
  GET /airports/{orig}/routes/{dest}?max_file_age=1 year   routes airlines filed between the airports, with counts
  GET /schedules/{start}/{end}?origin=&destination=&airline=&flight_number=   scheduled departures, up to a year ahead
  GET /flights/{ident}                                     recent and upcoming instances; `route` once filed
  GET /flights/{fa_flight_id}/route                        the filed route decoded to fixes with lat/lon
Responses are cached in cache/aeroapi/ (routes and schedules for a day, decoded routes permanently).

Spending guard (the rule: never go over the free $5 a month):
  * every endpoint has its list price per result set (15 records) below; an endpoint not in PRICES is never called;
  * before a call, the month's spend is the larger of the local ledger (cache/aeroapi/usage-YYYY-MM.json, every
    billable call recorded with its pages) and FlightAware's own figure from GET /account/usage (free), refreshed
    every 10 minutes; if spend + this call's worst case would pass BUDGET_USD ($4.00 by default, leaving a margin for
    usage FlightAware has not reported yet), the call is skipped and the prediction carries on without AeroAPI;
  * one process (one prediction or test run) may spend at most PER_RUN_USD ($1.50);
  * `uv run planeview aeroapi` shows the month's spend from both sources.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse

from .. import cache

BASE = "https://aeroapi.flightaware.com/aeroapi"
_KEY = None
_LAST = [0.0]

BUDGET_USD = float(os.environ.get("PLANEVIEW_AEROAPI_BUDGET", "4.00"))   # a month; the free credit is $5
PER_RUN_USD = float(os.environ.get("PLANEVIEW_AEROAPI_PER_RUN", "1.50"))
# list price per result set (flightaware.com/commercial/aeroapi, checked November 2026)
PRICES = [
    (re.compile(r"^/account/usage$"), 0.0),
    (re.compile(r"^/flights/[^/]+/route$"), 0.010),
    (re.compile(r"^/flights/[^/]+$"), 0.005),
    (re.compile(r"^/airports/[^/]+/routes/[^/]+$"), 0.020),
    (re.compile(r"^/schedules/[^/]+/[^/]+$"), 0.020),
]
_RUN = [0.0]
_REMOTE = [0.0, None]   # (fetched at, total_cost this month)
_WARNED = set()


def _warn(msg):
    if msg not in _WARNED:
        _WARNED.add(msg)
        print(f"  AeroAPI: {msg}", file=sys.stderr, flush=True)


def price(path: str):
    for rx, p in PRICES:
        if rx.match(path):
            return p
    return None


def _month():
    return dt.datetime.now(dt.UTC).strftime("%Y-%m")


def _ledger_path():
    return cache.CACHE / "aeroapi" / f"usage-{_month()}.json"


def ledger() -> dict:
    p = _ledger_path()
    return json.loads(p.read_text()) if p.exists() else dict(month=_month(), spent=0.0, calls=[])


def _record(path, pages, cost):
    led = ledger()
    led["spent"] = round(led["spent"] + cost, 4)
    led["calls"].append([dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), path, pages, round(cost, 4)])
    p = _ledger_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(led, indent=1))
    _RUN[0] += cost


def _curl(url: str, k: str):
    """GET through curl with the key passed on stdin (a config line), so it never appears in the process list."""
    cfg = f'header = "x-apikey: {k}"\n'
    r = subprocess.run(["curl", "-sS", "--max-time", "60", "-K", "-", "-w", "\n%{http_code}", url],
                       input=cfg, capture_output=True, text=True)
    if r.returncode:
        return None, 0
    body, _, code = r.stdout.rpartition("\n")
    try:
        return body, int(code.strip() or 0)
    except ValueError:
        return body, 0


def remote_spent(force=False):
    """This month's total_cost according to FlightAware (GET /account/usage, free); None if unavailable."""
    k = key()
    if not k:
        return None
    if not force and time.time() - _REMOTE[0] < 600:
        return _REMOTE[1]
    start = dt.datetime.now(dt.UTC).strftime("%Y-%m-01T00:00:00Z")
    wait = 6.5 - (time.time() - _LAST[0])  # the per-minute rate limit counts this call too
    if wait > 0:
        time.sleep(wait)
    _LAST[0] = time.time()
    body, code = _curl(BASE + "/account/usage?" + urllib.parse.urlencode({"start": start}), k)
    val = None
    if code == 200:
        try:
            val = float(json.loads(body).get("total_cost") or 0.0)
        except (ValueError, AttributeError):
            val = None
    _REMOTE[0], _REMOTE[1] = time.time(), val
    return val


def spent_this_month() -> float:
    r = remote_spent()
    return max(ledger()["spent"], r or 0.0)


def usage() -> dict:
    return dict(month=_month(), ledger=ledger()["spent"], flightaware=remote_spent(force=True), budget=BUDGET_USD,
                calls=len(ledger()["calls"]), free_credit=5.0)


def key() -> str | None:
    global _KEY
    if _KEY is not None:
        return _KEY or None
    k = os.environ.get("AEROAPI_KEY", "")
    if not k and sys.platform == "darwin":   # the macOS Keychain; elsewhere the environment variable is the way
        try:
            r = subprocess.run(["security", "find-generic-password", "-s", "plane-view-aeroapi", "-w"],
                               capture_output=True, text=True, timeout=20)
            k = r.stdout.strip() if r.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired):
            k = ""
    _KEY = k
    return k or None


def get(path: str, params: dict | None = None, cache_rel: str | None = None, max_age_days: float | None = 1.0):
    """GET with the key, cached; None on any failure or when the call would break the budget."""
    k = key()
    if not k:
        return None
    if cache_rel:
        p = cache.CACHE / "aeroapi" / cache_rel
        if p.exists() and (max_age_days is None or (time.time() - p.stat().st_mtime) / 86400 < max_age_days):
            return json.loads(p.read_text())
    unit = price(path)
    if unit is None:
        _warn(f"{path} has no known price - not called")
        return None
    pages = int((params or {}).get("max_pages", 1))
    worst = unit * pages
    if worst > 0:
        spent = spent_this_month()
        if spent + worst > BUDGET_USD:
            _warn(f"monthly budget reached (${spent:.2f} of ${BUDGET_USD:.2f}) - carrying on without AeroAPI")
            return None
        if _RUN[0] + worst > PER_RUN_USD:
            _warn(f"this run's limit reached (${_RUN[0]:.2f} of ${PER_RUN_USD:.2f}) - carrying on without AeroAPI")
            return None
    url = BASE + path + ("?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote) if params else "")
    wait = 6.5 - (time.time() - _LAST[0])  # Personal tier: 10 result sets a minute
    if wait > 0:
        time.sleep(wait)
    _LAST[0] = time.time()
    body, code = _curl(url, k)
    if body is None:
        return None
    d = None
    if code == 200:
        try:
            d = json.loads(body)
        except ValueError:
            d = None
    if worst > 0 and code not in (0, 401, 403, 429):
        used = int(d.get("num_pages") or 1) if isinstance(d, dict) else pages
        _record(path, used, unit * max(1, min(used, pages)))
    if d is None:
        return None
    if cache_rel:
        p = cache.CACHE / "aeroapi" / cache_rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(d))
    return d


def flight_info(flight: str | None, info: dict, log=print) -> dict | None:
    """Scheduled departure for the date is added later by `schedule`; here only whether a key exists."""
    if not key():
        return dict(filed_note="none (no AeroAPI key)")
    return dict(filed_note="AeroAPI key present")


def schedule(date: dt.date, orig: str, dest: str, airline_icao: str | None, number: str | None):
    """Scheduled departure (unix UTC) and aircraft type of a flight on a date, from /schedules."""
    if not key() or not number:
        return None
    digits = "".join(c for c in number if c.isdigit())
    d0, d1 = date.isoformat(), (date + dt.timedelta(days=1)).isoformat()
    params = dict(origin=orig, destination=dest, flight_number=digits)
    if airline_icao:
        params["airline"] = airline_icao
    d = get(f"/schedules/{d0}/{d1}", params, f"schedules/{orig}-{dest}-{digits}-{d0}.json")
    for s in (d or {}).get("scheduled", []):
        try:
            t = dt.datetime.fromisoformat(s["scheduled_out"].replace("Z", "+00:00")).timestamp()
        except (KeyError, ValueError, AttributeError):
            continue
        return dict(sched_dep_utc=t, sched_type=s.get("aircraft_type"))
    return None


def filed_routes(orig_icao: str, dest_icao: str):
    """[{route, count, aircraft_types, filed_altitude_min/max, last_departure_time, route_distance}] or None."""
    d = get(f"/airports/{orig_icao}/routes/{dest_icao}", {"max_file_age": "1 year", "max_pages": 2},
            f"routes/{orig_icao}-{dest_icao}.json", max_age_days=7)
    return (d or {}).get("routes")


def recent_flights(ident: str):
    d = get(f"/flights/{ident}", None, f"flights/{ident}-{dt.date.today().isoformat()}.json", max_age_days=0.1)
    return (d or {}).get("flights")


def decoded_route(fa_flight_id: str):
    d = get(f"/flights/{urllib.parse.quote(fa_flight_id)}/route", None, f"decoded/{fa_flight_id}.json",
            max_age_days=None)
    return (d or {}).get("fixes")

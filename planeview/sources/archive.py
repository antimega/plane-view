"""Route library from adsb.lol's daily GitHub archives (ODbL; github.com/adsblol/globe_history_YYYY).

Each day's release is a tar (split into .tar.aa/.ab) of readsb's globe_history folder; `traces/xx/trace_full_<hex>.json`
holds the full trace of every aircraft seen that day (gzip JSON, format: readsb README-json.md#trace-jsons). The tar is
streamed through curl and never stored; every trace is split into legs, each leg's ends are matched to airports, and the
leg is kept as a compact track simplified to ~0.3 km, with times and altitudes. The result is a library of all
airline flights of the sampled days, held in one SQLite file (sources/library.py: cache/library/legs.sqlite).

adsb.lol's own replay host (adsb.lol/globe_history/...) turns automated clients away; the GitHub releases are the
published bulk channel and are what this uses.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import subprocess
import sys
import tarfile
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait

import numpy as np
import orjson

from .. import cache
from . import library

LIB = library.LIB
R_KM = 6371.0088
LOW_FT = 4000          # an end below this, near an airport, is a takeoff/landing there
END_KM = 12.0          # how far from the airport reference point that end may be
SIMPLIFY_KM = 0.3


# ------------------------------------------------------------------ releases
def _probe(url: str) -> int:
    """Size in bytes of a release download (HEAD, following GitHub's redirect); 0 if it does not exist."""
    for attempt in range(3):
        r = subprocess.run(["curl", "-sS", "-I", "-L", "-A", cache.UA, "--max-time", "30", "-o", "/dev/null",
                            "-w", "%{http_code} %header{content-length}", url], capture_output=True, text=True)
        try:
            code, size = r.stdout.split()[:2]
        except ValueError:
            code, size = "000", "0"
        if code == "200":
            return int(size)
        if code == "404":
            return 0
        time.sleep(3 * (attempt + 1))   # no answer, 429 or 5xx: not the same as "does not exist"
    raise ConnectionError(f"GitHub did not answer for {url.rsplit('/', 1)[-1]} (HTTP {code})")


def release(day: dt.date) -> tuple[str, list[str], int]:
    """(tag, part URLs in order, total bytes) - prod unless staging is clearly bigger (prod was down).

    The download links are probed directly (no GitHub API: its 60 requests an hour run out quickly when many days are
    scanned); the API is only the fallback."""
    best = None
    for kind in ("prod", "staging"):
        tag = f"v{day:%Y.%m.%d}-planes-readsb-{kind}-0"
        base = f"https://github.com/adsblol/globe_history_{day.year}/releases/download/{tag}/{tag}.tar"
        urls, size = [], 0
        for suffix in ("aa", "ab", "ac", "ad", "ae", "af"):
            n = _probe(f"{base}.{suffix}")
            if not n:
                break
            urls.append(f"{base}.{suffix}"); size += n
        if not urls:
            n = _probe(base)
            if n:
                urls, size = [base], n
        if urls and (best is None or size > best[2] * 1.15):
            best = (tag, urls, size)
    if best is None:
        for kind in ("prod", "staging"):
            tag = f"v{day:%Y.%m.%d}-planes-readsb-{kind}-0"
            try:
                r = cache.get_json(f"https://api.github.com/repos/adsblol/globe_history_{day.year}/releases/tags/{tag}")
            except Exception:
                continue
            assets = sorted(r.get("assets", []), key=lambda a: a["name"])
            if assets:
                size = sum(a["size"] for a in assets)
                if best is None or size > best[2] * 1.15:
                    best = (tag, [a["browser_download_url"] for a in assets], size)
    if best is None:
        raise RuntimeError(f"no archive release for {day}")
    return best


# ------------------------------------------------------------------ geometry
def _xyz(lat, lon):
    la, lo = np.radians(lat), np.radians(lon)
    return np.stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)], axis=-1) * R_KM


def rdp(P: np.ndarray, tol: float) -> np.ndarray:
    """Ramer-Douglas-Peucker on rows of P (any dimension); returns the indices kept."""
    n = len(P)
    if n < 3:
        return np.arange(n)
    keep = np.zeros(n, bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        seg = P[b] - P[a]
        L2 = float(seg @ seg)
        Q = P[a + 1:b] - P[a]
        if L2 == 0:
            d = np.linalg.norm(Q, axis=1)
        else:
            u = np.clip(Q @ seg / L2, 0, 1)
            d = np.linalg.norm(Q - u[:, None] * seg, axis=1)
        i = int(np.argmax(d))
        if d[i] > tol:
            k = a + 1 + i
            keep[k] = True
            stack += [(a, k), (k, b)]
    return np.flatnonzero(keep)


# ------------------------------------------------------------------ per-trace work (runs in worker processes)
_AP = None


def _init():
    global _AP
    from .airports import airports
    _AP = airports()


def _end_airport(lat, lon, alt, ground):
    if not (ground or (alt == alt and alt <= LOW_FT)):
        return None, None
    i, km = _AP.nearest(np.array([lat]), np.array([lon]), END_KM)
    if i[0] < 0:
        return None, None
    return _AP.code(int(i[0])), round(float(km[0]), 1)


def legs_of(raw: bytes, day_start: float) -> list[dict]:
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    d = orjson.loads(raw)
    tr = d.get("trace") or []
    if len(tr) < 30:
        return []
    t0 = d["timestamp"]
    n = len(tr)
    t = np.empty(n); lat = np.empty(n); lon = np.empty(n); alt = np.empty(n); gs = np.empty(n)
    ground = np.zeros(n, bool); newleg = np.zeros(n, bool)
    cs = [None] * n
    for k, p in enumerate(tr):
        t[k] = t0 + p[0]; lat[k] = p[1]; lon[k] = p[2]
        a = p[3]
        if a == "ground":
            ground[k] = True; alt[k] = 0.0
        else:
            alt[k] = a if a is not None else np.nan
        gs[k] = p[4] if p[4] is not None else np.nan
        newleg[k] = bool((p[6] or 0) & 2)
        if len(p) > 8 and isinstance(p[8], dict):
            f = p[8].get("flight")
            if f:
                cs[k] = f.strip()
    if not np.any(alt[~ground] >= 8000):
        return []  # light aircraft, helicopters, vehicles
    # split: readsb's leg flag, a ground stop, or a long gap that is not at cruise level (oceans leave cruise gaps)
    cut = newleg.copy()
    gap = np.diff(t)
    low = (alt < 12000) | ground
    cut[1:] |= (gap > 2700) & (low[:-1] | low[1:])
    # a turnaround at an airport without coverage: low on both sides of a shorter gap
    vlow = (alt < 15000) | ground
    cut[1:] |= (gap > 900) & vlow[:-1] & vlow[1:]
    cut[1:] |= gap > 4 * 3600
    gnd_change = np.flatnonzero(ground[:-1] & ~ground[1:]) + 1  # ground -> air: a takeoff starts a leg
    cut[gnd_change] = True
    starts = np.flatnonzero(cut)
    bounds = list(zip(np.r_[0, starts], np.r_[starts, n]))
    out = []
    for a, b in bounds:
        if b - a < 20:
            continue
        air = np.flatnonzero(~ground[a:b]) + a
        if len(air) < 20:
            continue
        i0, i1 = air[0], air[-1]
        dur = t[i1] - t[i0]
        if dur < 15 * 60:
            continue
        # takeoff end: last ground point before the first airborne one, else the first airborne point
        s_lat, s_lon, s_alt, s_g = (lat[i0 - 1], lon[i0 - 1], 0.0, True) if i0 > a and ground[i0 - 1] else \
            (lat[i0], lon[i0], alt[i0], False)
        e_lat, e_lon, e_alt, e_g = (lat[i1 + 1], lon[i1 + 1], 0.0, True) if i1 + 1 < b and ground[i1 + 1] else \
            (lat[i1], lon[i1], alt[i1], False)
        fr, fr_km = _end_airport(s_lat, s_lon, s_alt, s_g)
        to, to_km = _end_airport(e_lat, e_lon, e_alt, e_g)
        cont_prev = fr is None and t[i0] - day_start < 300
        cont_next = to is None and day_start + 86400 - t[i1] < 300
        if fr is None and to is None and not (cont_prev or cont_next):
            continue
        if fr is not None and fr == to:
            continue  # circuits, test flights, or two flights merged across an unseen turnaround
        sl = slice(i0, i1 + 1)
        if not np.isfinite(alt[sl]).any():
            continue  # no height reported at all: nothing to build a profile from
        A = np.nan_to_num(alt[sl], nan=np.nanmedian(alt[sl]))
        P = np.column_stack([_xyz(lat[sl], lon[sl]), A * 0.0003048 * 3])  # altitude weighted x3 so climbs keep shape
        keep = rdp(P, SIMPLIFY_KM) + i0
        calls = [c for c in cs[a:b] if c and not c.startswith("@")]
        callsign = max(set(calls), key=calls.count) if calls else None
        gaps = np.diff(t[sl])
        leg = dict(hex=d.get("icao"), reg=d.get("r"), type=d.get("t"), cs=callsign,
                   fr=fr, to=to, fr_km=fr_km, to_km=to_km, t0=round(float(t[i0]), 1), t1=round(float(t[i1]), 1),
                   s_alt=None if s_g or s_alt != s_alt else int(s_alt),
                   e_alt=None if e_g or e_alt != e_alt else int(e_alt),
                   cont_prev=bool(cont_prev), cont_next=bool(cont_next),
                   max_alt=int(np.nanmax(alt[sl])), gap_max=int(gaps.max()) if len(gaps) else 0,
                   pts=[[int(round(t[k] - t[i0])), round(float(lat[k]), 4), round(float(lon[k]), 4),
                         int(alt[k]) if alt[k] == alt[k] else None, int(gs[k]) if gs[k] == gs[k] else None]
                        for k in keep])
        out.append(leg)
    return out


def _batch(items, day_start):
    res, bad = [], 0
    for name, raw in items:
        try:
            res += legs_of(raw, day_start)
        except Exception:
            bad += 1
    return res, len(items), bad


# ------------------------------------------------------------------ one day
def _scan(day: dt.date, urls, workers: int, limit: int | None, log, counts: dict):
    """Yield every leg of a day's archive, streaming the tar through curl. Raises if the stream ends early."""
    day_start = dt.datetime(day.year, day.month, day.day, tzinfo=dt.UTC).timestamp()
    proc = subprocess.Popen(["curl", "-sSfL", "-A", cache.UA, *urls], stdout=subprocess.PIPE, bufsize=8 << 20)
    t_start = time.time()
    pending = set()
    batch = []
    n_legs = 0

    def drain(block):
        done, _ = wait(pending, return_when=FIRST_COMPLETED) if block else ({f for f in pending if f.done()}, None)
        out = []
        for f in done:
            pending.discard(f)
            legs, m, bad = f.result()
            counts["traces"] += m; counts["bad"] += bad
            out += legs
        return out

    try:
        with ProcessPoolExecutor(workers, initializer=_init) as ex, \
                tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
            seen = 0
            for m in tar:
                if not m.isfile() or "/traces/" not in "/" + m.name or "trace_full" not in m.name:
                    continue
                batch.append((m.name, tar.extractfile(m).read()))
                seen += 1
                if len(batch) >= 300:
                    pending.add(ex.submit(_batch, batch, day_start)); batch = []
                    ready = []
                    while len(pending) >= workers * 3:
                        ready += drain(True)
                    ready += drain(False)
                    n_legs += len(ready)
                    yield from ready
                if seen % 20000 == 0:
                    log(f"  {day}: {seen} traces read, {n_legs} legs, {time.time() - t_start:.0f}s")
                if limit and seen >= limit:
                    break
            if batch:
                pending.add(ex.submit(_batch, batch, day_start))
            while pending:
                yield from drain(True)
    finally:
        proc.kill()
        proc.wait()
    if not limit and counts["traces"] < 10000:
        raise RuntimeError(f"{day}: only {counts['traces']} traces - stream cut short?")


def scan_day(day: dt.date, workers: int = 6, limit: int | None = None, log=print) -> dict:
    """Add one day to the library (nothing is stored unless the whole day arrives). `limit` is a dry run."""
    con = library.connect()
    try:
        if day.isoformat() in library.days(con) and not limit:
            log(f"{day}: already in the library")
            return {}
        tag, urls, size = release(day)
        log(f"{day}: {tag}, {size / 2**30:.2f} GiB in {len(urls)} part(s)")
        t_start = time.time()
        counts = dict(traces=0, bad=0)
        legs = _scan(day, urls, workers, limit, log, counts)

        def stats():
            return dict(release=tag, bytes=size, traces=counts["traces"], bad=counts["bad"],
                        seconds=round(time.time() - t_start), simplify_km=SIMPLIFY_KM,
                        scanned=dt.datetime.now(dt.UTC).isoformat(timespec="seconds"))
        if limit:
            n = sum(1 for _ in legs)
            log(f"{day}: dry run, {counts['traces']} traces -> {n} legs (not stored)")
            return dict(stats(), day=day.isoformat(), legs=n, limit=limit)
        n = library.add_day(con, day.isoformat(), legs, stats)
        st = dict(stats(), day=day.isoformat(), legs=n)
        log(f"{day}: {counts['traces']} traces -> {n} legs ({counts['bad']} unreadable) in {st['seconds']}s")
        return st
    finally:
        con.close()


def parse_days(specs: list[str]) -> list[dt.date]:
    """'2026-11-16', or a range '2026-11-01..2026-11-07' (inclusive), optionally with a step: '...07/7' = weekly."""
    out = []
    for s in specs:
        s, _, step = s.partition("/")
        a, _, b = s.partition("..")
        d0 = dt.date.fromisoformat(a)
        d1 = dt.date.fromisoformat(b) if b else d0
        if d1 < d0:
            raise ValueError(f"range runs backwards: {s}")
        out += [d0 + dt.timedelta(days=k) for k in range(0, (d1 - d0).days + 1, int(step or 1))]
    return sorted(set(out))


def latest_days(n: int, today: dt.date | None = None) -> list[dt.date]:
    """The n most recent days that should have an archive (a day is published the morning after, UTC)."""
    last = (today or dt.datetime.now(dt.UTC).date()) - dt.timedelta(days=1)
    return [last - dt.timedelta(days=k) for k in range(n - 1, -1, -1)]


def add_days(days: list[dt.date], workers: int = 6, limit: int | None = None, log=print) -> list[str]:
    """Scan each day, three attempts apiece; returns the days that failed."""
    failed = []
    for day in days:
        for attempt in range(3):
            try:
                scan_day(day, workers, limit, log)
                break
            except Exception as e:  # network cut: start the day again (nothing of it was stored)
                log(f"{day}: attempt {attempt + 1} failed: {e}")
                if "no archive release" in str(e):
                    failed.append(day.isoformat())
                    break
                time.sleep(30)
        else:
            failed.append(day.isoformat())
    return failed


def main(argv=None):
    ap = argparse.ArgumentParser(description="Add days to the route library from adsb.lol's GitHub archives")
    ap.add_argument("days", nargs="+", help="YYYY-MM-DD, or a range A..B, or A..B/7 for every 7th day")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, help="dry run: stop after this many traces, store nothing")
    a = ap.parse_args(argv)

    def log(msg):
        print(time.strftime("%H:%M:%S"), msg, flush=True)

    failed = add_days(parse_days(a.days), a.workers, a.limit, log)
    if failed:
        log(f"FAILED: {' '.join(failed)}")
        sys.exit(1)


if __name__ == "__main__":
    main()

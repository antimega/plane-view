"""Where things live, and downloads.

Everything downloaded goes under one cache folder and everything generated under `out/` and `site/`. By default these
sit beside the code; set PLANEVIEW_HOME to put all three somewhere else, or PLANEVIEW_CACHE for the cache alone (it is
the big one). Every download records its date in a `fetched.json` beside it, so a later run can say which snapshot it
used.

Web assets (the page template, Leaflet) ship inside the package, in planeview/web/.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = Path(os.environ.get("PLANEVIEW_HOME") or ROOT).expanduser()
CACHE = Path(os.environ.get("PLANEVIEW_CACHE") or HOME / "cache").expanduser()
OUT = HOME / "out"
SITE = HOME / "site"
WEB = Path(__file__).resolve().parent / "web"
UA = "plane-view/0.3 (personal use)"
RETRY_CODES = {0, 408, 425, 429, 500, 502, 503, 504}   # 0 = no answer at all


def find(rel: str) -> Path | None:
    """The cached copy of a cache-relative path, if there is one."""
    p = CACHE / rel
    return p if p.exists() else None


def record(log: Path, key: str, value=None):
    log.parent.mkdir(parents=True, exist_ok=True)
    d = json.loads(log.read_text()) if log.exists() else {}
    d[key] = value if value is not None else dt.date.today().isoformat()
    write_atomic(log, json.dumps(d, indent=1, sort_keys=True))


def write_atomic(p: Path, text: str | bytes):
    """Write through a temporary file, so a crash or a second process never leaves half a file behind."""
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.part")
    tmp.write_bytes(text.encode() if isinstance(text, str) else text)
    tmp.replace(p)


def curl(url: str, dst: Path, timeout: int = 600, retries: int = 2) -> int:
    """Download to dst atomically. Returns the HTTP status (0 = network failure, no response).

    A dropped connection, a rate limit (429) or a server error is tried again after a pause; a plain 403/404 is not.
    adsb.lol and Wikidata refuse Python's urllib, so everything goes through curl."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_name(f"{dst.name}.{os.getpid()}.part")
    code = 0
    for attempt in range(retries + 1):
        r = subprocess.run(["curl", "-sSL", "--compressed", "-A", UA, "--connect-timeout", "20",
                            "--max-time", str(timeout), "-o", str(part), "-w", "%{http_code}", url],
                           capture_output=True, text=True)
        try:
            code = int(r.stdout.strip() or 0)
        except ValueError:
            code = 0
        if r.returncode:
            code = 0 if code == 200 else code   # cut off part-way: the status alone would look like success
        if code == 200:
            part.replace(dst)
            return 200
        part.unlink(missing_ok=True)
        if code not in RETRY_CODES or attempt == retries:
            break
        time.sleep(4 * 2 ** attempt)
    return code


def fetch(rel: str, url: str, log_key: str | None = None) -> Path:
    """Cached download: return the cached file, downloading it into our cache if no copy exists."""
    p = find(rel)
    if p:
        return p
    p = CACHE / rel
    code = curl(url, p)
    if code != 200:
        raise RuntimeError(f"download failed ({code}): {url}")
    record(p.parent / "fetched.json", log_key or p.name)
    return p


def fetch_any(rel: str, urls: list[str], log_key: str | None = None) -> Path:
    """`fetch` with mirrors: the first URL that answers wins."""
    p = find(rel)
    if p:
        return p
    p = CACHE / rel
    codes = []
    for url in urls:
        code = curl(url, p)
        if code == 200:
            record(p.parent / "fetched.json", log_key or p.name, f"{dt.date.today().isoformat()} {url}")
            return p
        codes.append(code)
    raise RuntimeError(f"download failed ({', '.join(map(str, codes))}): {' | '.join(urls)}")


def get_json(url: str, rel: str | None = None, max_age_days: float | None = None, timeout: int = 60,
             retries: int = 2):
    """GET a JSON document through curl, optionally cached at cache/<rel>.

    Network failures, 429 and 5xx are retried with a growing pause. If the source still does not answer and an older
    cached copy exists, that copy is returned (stale beats nothing: a flight's airports do not change by the week).
    A JSON body is returned whatever its status, because several sources explain an error in JSON (adsbdb's 404
    "unknown callsign", Open-Meteo's rate-limit message)."""
    p = CACHE / rel if rel else None
    if p is not None and p.exists():
        age = (dt.datetime.now().timestamp() - p.stat().st_mtime) / 86400
        if max_age_days is None or age < max_age_days:
            return json.loads(p.read_text())
    err = "no response"
    for attempt in range(retries + 1):
        r = subprocess.run(["curl", "-sSL", "--compressed", "-A", UA, "--connect-timeout", "20",
                            "--max-time", str(timeout), "-w", "\n%{http_code}", url], capture_output=True, text=True)
        body, _, code = r.stdout.rpartition("\n")
        code = int(code) if code.strip().isdigit() else 0
        d = None
        if r.returncode == 0 and body.strip():
            try:
                d = json.loads(body)
            except ValueError:
                d = None
        if d is not None and code not in RETRY_CODES:
            if p is not None and code == 200:
                write_atomic(p, json.dumps(d))
            return d
        err = f"HTTP {code}" if code else (r.stderr.strip() or "no response")
        if attempt < retries:
            time.sleep(3 * 2 ** attempt)
        elif d is not None:
            return d            # the source's own JSON error, for the caller to read
    if p is not None and p.exists():
        return json.loads(p.read_text())   # stale, but the source is down
    raise RuntimeError(f"GET failed: {url}: {err}")

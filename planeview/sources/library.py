"""The route library's one store: cache/library/legs.sqlite.

Every leg of every sampled day is one row. The searchable facts (airports, callsign, aircraft, times) are columns; the
track is a packed blob: the five per-point values (seconds from the first point, latitude and longitude in 1e-4 degrees,
altitude in feet, ground speed in knots) are delta-coded column by column and written as zigzag varints, about 8 bytes
a point. That is lossless for what the scanner keeps and less than half the size of the same points as compressed JSON.

Earlier versions kept each day twice: a `<day>.jsonl.gz` file and a copy of every line in `library.sqlite`
(8.2 GB for 64 days). `migrate()` moves those into this store, checking every leg decodes to what was read, and only
then lets the old files go (2.6 GB for the same 64 days).

Indexes are partial, so they only hold the rows a query can ask for:
  i_fr   (fr, d)   legs with a known origin
  i_to   (too, d)  legs with a known destination
  i_mid  (hex, d)  mid-flight pieces (neither end known), found by aircraft
  i_cs   (cs)      complete legs by callsign: which airports a flight number serves, with no network
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import sqlite3

import orjson

from .. import cache

LIB = cache.CACHE / "library"
DB = LIB / "legs.sqlite"
VERSION = 2
NO_ALT, NO_GS = -9999, -1      # stand-ins for a missing altitude / ground speed inside a packed track

_COLS = ("d", "fr", "too", "hex", "cs", "reg", "type", "t0", "t1", "fr_km", "to_km", "s_alt", "e_alt", "flags",
         "max_alt", "gap_max", "pts")
_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS legs (d INTEGER, fr TEXT, too TEXT, hex TEXT, cs TEXT, reg TEXT, type TEXT, t0 REAL,
    t1 REAL, fr_km REAL, to_km REAL, s_alt INTEGER, e_alt INTEGER, flags INTEGER, max_alt INTEGER, gap_max INTEGER,
    pts BLOB);
CREATE TABLE IF NOT EXISTS days (d INTEGER PRIMARY KEY, day TEXT, stats TEXT);
CREATE INDEX IF NOT EXISTS i_fr ON legs(fr, d) WHERE fr IS NOT NULL;
CREATE INDEX IF NOT EXISTS i_to ON legs(too, d) WHERE too IS NOT NULL;
CREATE INDEX IF NOT EXISTS i_mid ON legs(hex, d) WHERE fr IS NULL AND too IS NULL;
CREATE INDEX IF NOT EXISTS i_cs ON legs(cs) WHERE fr IS NOT NULL AND too IS NOT NULL;
PRAGMA user_version = {VERSION};
"""


# ------------------------------------------------------------------ packed tracks
def pack(pts) -> bytes:
    """[[t, lat, lon, alt|None, gs|None], ...] -> bytes."""
    cols = ([int(p[0]) for p in pts], [round(p[1] * 1e4) for p in pts], [round(p[2] * 1e4) for p in pts],
            [NO_ALT if p[3] is None else int(p[3]) for p in pts], [NO_GS if p[4] is None else int(p[4]) for p in pts])
    out = bytearray()
    for col in cols:
        prev = 0
        for v in col:
            z = v - prev
            prev = v
            z = (z << 1) ^ (z >> 63)          # zigzag: small magnitudes of either sign become small numbers
            while z >= 0x80:
                out.append((z & 0x7F) | 0x80)
                z >>= 7
            out.append(z)
    return bytes(out)


def unpack(blob: bytes) -> list[list]:
    vals, cur, shift = [], 0, 0
    for b in blob:
        cur |= (b & 0x7F) << shift
        if b & 0x80:
            shift += 7
        else:
            vals.append((cur >> 1) ^ -(cur & 1))
            cur = shift = 0
    n, rem = divmod(len(vals), 5)
    if rem or shift:
        raise ValueError("corrupt packed track")
    cols = []
    for c in range(5):
        acc, col = 0, []
        for v in vals[c * n:(c + 1) * n]:
            acc += v
            col.append(acc)
        cols.append(col)
    t, la, lo, al, gs = cols
    return [[t[k], la[k] / 1e4, lo[k] / 1e4, None if al[k] == NO_ALT else al[k], None if gs[k] == NO_GS else gs[k]]
            for k in range(n)]


# ------------------------------------------------------------------ rows
def _ordinal(day: str | dt.date) -> int:
    return (day if isinstance(day, dt.date) else dt.date.fromisoformat(day)).toordinal()


def _row(d: int, lg: dict) -> tuple:
    return (d, lg["fr"], lg["to"], lg["hex"], lg["cs"], lg.get("reg"), lg.get("type"), lg["t0"], lg["t1"],
            lg.get("fr_km"), lg.get("to_km"), lg.get("s_alt"), lg.get("e_alt"),
            int(bool(lg.get("cont_prev"))) | int(bool(lg.get("cont_next"))) << 1,
            lg.get("max_alt"), lg.get("gap_max"), pack(lg["pts"]))


def _leg(r: tuple) -> dict:
    d = dict(zip(_COLS, r))
    return dict(hex=d["hex"], reg=d["reg"], type=d["type"], cs=d["cs"], fr=d["fr"], to=d["too"], fr_km=d["fr_km"],
                to_km=d["to_km"], t0=d["t0"], t1=d["t1"], s_alt=d["s_alt"], e_alt=d["e_alt"],
                cont_prev=bool(d["flags"] & 1), cont_next=bool(d["flags"] & 2), max_alt=d["max_alt"],
                gap_max=d["gap_max"], pts=unpack(d["pts"]), day=dt.date.fromordinal(d["d"]).isoformat())


# ------------------------------------------------------------------ the store
def connect(readonly: bool = False) -> sqlite3.Connection:
    """Open the store, creating it (and adopting any older day files) on first use."""
    LIB.mkdir(parents=True, exist_ok=True)
    fresh = not DB.exists()
    con = sqlite3.connect(DB, timeout=120)
    if fresh or con.execute("PRAGMA user_version").fetchone()[0] != VERSION:
        con.executescript(_SCHEMA)
        con.commit()
    if not readonly and con.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
        con.execute("PRAGMA journal_mode=WAL")   # a day being added never blocks a prediction being read
    if not readonly and legacy_days():
        migrate(con)
    return con


def days(con: sqlite3.Connection | None = None) -> list[str]:
    """ISO dates in the library, oldest first."""
    own = con is None
    con = con or connect()
    try:
        return [r[0] for r in con.execute("SELECT day FROM days ORDER BY d")]
    finally:
        if own:
            con.close()


def day_stats(con: sqlite3.Connection | None = None) -> list[dict]:
    own = con is None
    con = con or connect()
    try:
        return [json.loads(r[0]) for r in con.execute("SELECT stats FROM days ORDER BY d")]
    finally:
        if own:
            con.close()


def add_day(con: sqlite3.Connection, day: str, legs, stats: dict | None = None) -> int:
    """Store one day's legs in a single transaction (all or nothing; replaces the day if it is already there)."""
    d = _ordinal(day)
    n = 0
    with con:   # commits on success, rolls back if `legs` raises part-way (a cut download leaves no half day)
        con.execute("DELETE FROM legs WHERE d = ?", (d,))
        con.execute("DELETE FROM days WHERE d = ?", (d,))
        batch = []
        for lg in legs:
            batch.append(_row(d, lg))
            n += 1
            if len(batch) >= 5000:
                con.executemany(f"INSERT INTO legs VALUES ({','.join('?' * len(_COLS))})", batch)
                batch = []
        if batch:
            con.executemany(f"INSERT INTO legs VALUES ({','.join('?' * len(_COLS))})", batch)
        st = dict(stats() if callable(stats) else stats or {}, day=day, legs=n)
        con.execute("INSERT INTO days VALUES (?, ?, ?)", (d, day, json.dumps(st)))
    return n


def remove_day(con: sqlite3.Connection, day: str):
    with con:
        con.execute("DELETE FROM legs WHERE d = ?", (_ordinal(day),))
        con.execute("DELETE FROM days WHERE d = ?", (_ordinal(day),))


def query(where: str, args=(), only_days=None, con: sqlite3.Connection | None = None) -> list[dict]:
    """Legs matching `where` (over the columns fr, too, hex, cs, ...), oldest first. The order is fixed (day, takeoff,
    aircraft) so the same library always gives the same prediction."""
    own = con is None
    con = con or connect()
    sql = f"SELECT {', '.join(_COLS)} FROM legs WHERE ({where})"
    args = list(args)
    if only_days is not None:
        ds = [_ordinal(x) for x in only_days]
        sql += f" AND d IN ({','.join('?' * len(ds))})"
        args += ds
    try:
        return [_leg(r) for r in con.execute(sql + " ORDER BY d, t0, hex", args)]
    finally:
        if own:
            con.close()


def callsign_routes(cs: str, con: sqlite3.Connection | None = None) -> list[tuple[str, str, int, str]]:
    """Airport pairs a callsign has flown in the library: [(from, to, flights, last day)], most flown first."""
    own = con is None
    con = con or connect()
    try:
        rows = con.execute("SELECT fr, too, COUNT(*), MAX(d) FROM legs WHERE cs = ? AND fr IS NOT NULL AND "
                           "too IS NOT NULL GROUP BY fr, too ORDER BY COUNT(*) DESC, MAX(d) DESC", (cs,)).fetchall()
    finally:
        if own:
            con.close()
    return [(fr, to, n, dt.date.fromordinal(d).isoformat()) for fr, to, n, d in rows]


def pairs(min_n: int = 1, con: sqlite3.Connection | None = None) -> list[tuple[str, str, int]]:
    """[(from, to, flights)] for every airport pair with at least min_n complete legs, busiest first."""
    own = con is None
    con = con or connect()
    try:
        return con.execute("SELECT fr, too, COUNT(*) FROM legs WHERE fr IS NOT NULL AND too IS NOT NULL "
                           "GROUP BY fr, too HAVING COUNT(*) >= ? ORDER BY COUNT(*) DESC, fr, too", (min_n,)).fetchall()
    finally:
        if own:
            con.close()


def size() -> dict:
    con = connect(readonly=True)
    try:
        n, pts = con.execute("SELECT COUNT(*), SUM(LENGTH(pts)) FROM legs").fetchone()
        return dict(days=len(days(con)), legs=n or 0, track_bytes=pts or 0,
                    file_bytes=DB.stat().st_size if DB.exists() else 0)
    finally:
        con.close()


# ------------------------------------------------------------------ the older layout
def legacy_days() -> list[str]:
    return sorted(p.name[:10] for p in LIB.glob("????-??-??.jsonl.gz"))


def _same(a: dict, b: dict) -> bool:
    keys = ("hex", "reg", "type", "cs", "fr", "to", "fr_km", "to_km", "t0", "t1", "s_alt", "e_alt", "cont_prev",
            "cont_next", "max_alt", "gap_max")
    return all(a.get(k) == b.get(k) for k in keys) and a["pts"] == b["pts"]


def migrate(con: sqlite3.Connection, keep_old: bool = False, log=print) -> int:
    """Move `<day>.jsonl.gz` (+ `<day>.json` stats) into the store. Each leg is packed, unpacked again and compared
    with what was read; a day's old files are removed only after its rows are committed and its count matches."""
    have = set(days(con))
    moved = 0
    for day in legacy_days():
        src, st_p = LIB / f"{day}.jsonl.gz", LIB / f"{day}.json"
        if day not in have:
            def legs():
                with gzip.open(src, "rb") as fh:
                    for line in fh:
                        lg = orjson.loads(line)
                        back = _leg(_row(0 + _ordinal(day), lg))
                        if not _same(lg, back):
                            raise ValueError(f"{day}: a leg of {lg.get('hex')} does not survive packing")
                        yield lg
            stats = json.loads(st_p.read_text()) if st_p.exists() else {}
            stats.pop("watch", None); stats.pop("watched_full", None)
            n = add_day(con, day, legs(), stats)
            if stats.get("legs") not in (None, n):
                log(f"  {day}: {n} legs stored, the old stats file said {stats['legs']}")
            log(f"  library: {day} moved into {DB.name} ({n} legs, verified)")
            moved += 1
        if not keep_old:
            src.unlink()
            st_p.unlink(missing_ok=True)
    old = LIB / "library.sqlite"
    if not keep_old and old.exists() and not legacy_days():
        old.unlink()
        log("  library: old index library.sqlite removed")
    if moved:
        con.execute("PRAGMA optimize")
    return moved

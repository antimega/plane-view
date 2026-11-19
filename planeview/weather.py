"""Chance that the ground is visible: cloud from Open-Meteo (free, no key; open-meteo.com, CC BY 4.0).

Up to 15 days ahead the hourly forecast is used; further out (and for dates in the past) a climatology: the
same hour, +-3 days, over the 10 previous years of the ERA5 reanalysis (archive-api.open-meteo.com).
Cells are 0.5 deg; every request is cached in cache/weather/.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json

import numpy as np

from . import cache

FORECAST = "https://api.open-meteo.com/v1/forecast"
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
VARS = "cloud_cover_low,cloud_cover_mid,cloud_cover_high"
CELL = 0.5
BATCH = 60


def p_clear(low, mid, high, alt_ft=None):
    """Probability of a view of the ground through the cloud layers (fractions 0-1).
    Below ~7,000 ft the aircraft is usually under the mid deck and often under the low one."""
    low, mid, high = (np.asarray(x, float) / 100 for x in (low, mid, high))
    p = (1 - low) * (1 - 0.75 * mid) * (1 - 0.2 * high)
    if alt_ft is not None:
        under = np.asarray(alt_ft, float) < 7000
        p = np.where(under, (1 - 0.5 * low), p)
    return np.clip(p, 0, 1)


def _get(url: str, params: dict, permanent: bool):
    """GET with retries: Open-Meteo's free tier limits requests per minute, so a 'limit exceeded' answer waits a minute
    and tries again (up to three times) instead of giving up on the weather."""
    import time
    q = "&".join(f"{k}={v}" for k, v in params.items())
    key = hashlib.sha1((url + "?" + q).encode()).hexdigest()[:20]
    rel = f"weather/{key}.json"
    for attempt in range(4):
        d = cache.get_json(url + "?" + q, None if permanent else rel, None if permanent else 0.25, timeout=150)
        if isinstance(d, dict) and d.get("error"):
            reason = str(d.get("reason"))
            p = cache.CACHE / rel
            p.unlink(missing_ok=True)
            if "limit" in reason.lower() and attempt < 3:
                time.sleep(62)
                continue
            raise RuntimeError(reason)
        return d if isinstance(d, list) else [d]
    raise RuntimeError("rate limited")


class Cloud:
    """P(clear) at (lat, lon, unix time), from a forecast or a climatology chosen by the flight date."""

    def __init__(self, date: dt.date, today: dt.date | None = None, log=print):
        self.date = date
        today = today or dt.date.today()
        self.mode = "forecast" if 0 <= (date - today).days <= 15 else "climatology"
        self.series = {}
        self.log = log
        self.source = None

    def prepare(self, lat, lon):
        cells = sorted({(round(float(a) / CELL) * CELL, round(float(b) / CELL) * CELL) for a, b in zip(lat, lon)})
        todo = [c for c in cells if c not in self.series]
        for k in range(0, len(todo), BATCH):
            chunk = todo[k:k + BATCH]
            try:
                if self.mode == "forecast":
                    self._forecast(chunk)
                else:
                    self._clim(chunk)
            except Exception as e:  # weather is optional: carry on without it
                self.log(f"  weather: {self.mode} unavailable ({e}); assuming 60% clear")
                for c in chunk:
                    self.series[c] = None
        n_ok = sum(1 for c in cells if self.series.get(c) is not None)
        self.source = (f"Open-Meteo {'forecast' if self.mode == 'forecast' else 'ERA5 climatology (8 years, ±3 days)'}"
                       f", {n_ok} cells")

    def _forecast(self, chunk):
        today = dt.date.today()
        d0 = max(self.date - dt.timedelta(days=1), today)
        d1 = min(self.date + dt.timedelta(days=1), today + dt.timedelta(days=15))
        params = dict(latitude=",".join(f"{a:.2f}" for a, _ in chunk), longitude=",".join(f"{b:.2f}" for _, b in chunk),
                      hourly=VARS, start_date=d0.isoformat(), end_date=d1.isoformat(), timezone="GMT")
        res = _get(FORECAST, params, permanent=False)
        for c, r in zip(chunk, res):
            h = r["hourly"]
            t = np.array([dt.datetime.fromisoformat(x).replace(tzinfo=dt.UTC).timestamp() for x in h["time"]])
            pc = p_clear(np.nan_to_num(h["cloud_cover_low"], nan=50), np.nan_to_num(h["cloud_cover_mid"], nan=50),
                         np.nan_to_num(h["cloud_cover_high"], nan=50))
            self.series[c] = ("f", t, pc)

    def _clim(self, chunk):
        """Hour-of-day profile of P(clear) per cell over 8 past years, +-3 days; cached per cell and year."""
        y = self.date.year
        years = [yy for yy in range(y - 8, y) if dt.date(yy + 1, 1, 10) < dt.date.today()][-8:]
        acc = {c: [] for c in chunk}
        for yy in years:
            try:
                mid = self.date.replace(year=yy)
            except ValueError:
                mid = self.date.replace(year=yy, day=28)
            d0, d1 = (mid - dt.timedelta(days=3)).isoformat(), (mid + dt.timedelta(days=3)).isoformat()
            need = []
            for c in chunk:
                p = cache.CACHE / "weather" / "era5" / f"{c[0]:.1f}_{c[1]:.1f}_{d0}.json"
                if p.exists():
                    acc[c].append(json.loads(p.read_text()))
                else:
                    need.append(c)
            for k in range(0, len(need), 20):
                sub = need[k:k + 20]
                params = dict(latitude=",".join(f"{a:.2f}" for a, _ in sub),
                              longitude=",".join(f"{b:.2f}" for _, b in sub), hourly=VARS,
                              start_date=d0, end_date=d1, timezone="GMT")
                res = None
                for attempt in range(3):
                    try:
                        res = _get(ARCHIVE, params, permanent=True)
                        break
                    except Exception:
                        if attempt == 2:
                            raise
                for c, r in zip(sub, res):
                    h = r["hourly"]
                    rec = dict(hours=[int(x[11:13]) for x in h["time"]],
                               p=[float(v) for v in p_clear(np.nan_to_num(np.array(h["cloud_cover_low"], float), nan=50),
                                                            np.nan_to_num(np.array(h["cloud_cover_mid"], float), nan=50),
                                                            np.nan_to_num(np.array(h["cloud_cover_high"], float), nan=50))])
                    p = cache.CACHE / "weather" / "era5" / f"{c[0]:.1f}_{c[1]:.1f}_{d0}.json"
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(json.dumps(rec))
                    acc[c].append(rec)
        for c in chunk:
            hrs = np.concatenate([np.array(a["hours"]) for a in acc[c]]) if acc[c] else np.zeros(0)
            pcs = np.concatenate([np.array(a["p"]) for a in acc[c]]) if acc[c] else np.zeros(0)
            prof = np.array([pcs[np.abs((hrs - hh + 12) % 24 - 12) <= 1].mean() if len(pcs) else 0.6
                             for hh in range(24)])
            self.series[c] = ("c", None, prof)

    def at(self, lat, lon, t, alt_ft=None):
        lat, lon, t = np.atleast_1d(lat), np.atleast_1d(lon), np.atleast_1d(t)
        out = np.full(len(lat), 0.6)
        for k in range(len(lat)):
            c = (round(float(lat[k]) / CELL) * CELL, round(float(lon[k]) / CELL) * CELL)
            s = self.series.get(c)
            if s is None:
                continue
            if s[0] == "f":
                out[k] = float(np.interp(t[k], s[1], s[2]))
            else:
                hh = dt.datetime.fromtimestamp(float(t[k]), dt.UTC).hour
                out[k] = float(s[2][hh])
        if alt_ft is not None:
            low = np.asarray(alt_ft, float) < 7000
            out = np.where(low, np.maximum(out, 0.5 + 0.5 * out), out)
        return out

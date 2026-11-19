"""Assemble the site from every built flight folder (site/f/<id>/data.json).

  site/index.html   the whole page: a complete HTML document with its styles inline
  site/leaflet.js   the map library, copied from the package (nothing is loaded from the network)
  site/flights.js   the flight picker's list, soonest first
  site/f/<id>/      one folder per flight: data.js, relief.jpg, the picture sheets and the PDF guides

The page loads its data with script tags, not fetch(), so it works when index.html is opened straight from the
folder as well as from any web server. Copy the folder anywhere to publish it.
"""
from __future__ import annotations

import datetime as dt
import json
import shutil

from .. import cache
from .web import SITE

WEB = cache.WEB


def build(log=print):
    flights = []
    fdir = SITE / "f"
    for d in sorted(fdir.iterdir()) if fdir.is_dir() else []:
        p = d / "data.json"
        if not p.exists():
            continue
        D = json.loads(p.read_text())
        f = D["flight"]
        date = dt.date.fromisoformat(f["date"])
        flights.append(dict(id=D["id"], hash=D["id"].replace("_", "-"), number=f.get("number"), date=f["date"],
                            date_label=f"{date.day} {date:%b %Y}", route=f"{f['orig']['code']}–{f['dest']['code']}"))
        js = d / "data.js"
        if not js.exists() or js.stat().st_mtime < p.stat().st_mtime:
            write_data_js(d, D)
    flights.sort(key=lambda x: (x["date"], x["id"]))
    SITE.mkdir(parents=True, exist_ok=True)
    (SITE / "flights.js").write_text("window.PV_FLIGHTS = " + json.dumps(flights, ensure_ascii=False) + ";\n")
    page = (WEB / "index.template.html").read_text().replace("/*LEAFLET_CSS*/", (WEB / "leaflet.css").read_text())
    (SITE / "index.html").write_text(page)
    shutil.copyfile(WEB / "leaflet.js", SITE / "leaflet.js")
    files = [p for p in SITE.rglob("*") if p.is_file() and not p.name.startswith(("_", "."))]
    size = sum(p.stat().st_size for p in files)
    log(f"site: {len(flights)} flight{'' if len(flights) == 1 else 's'}, {len(files)} files, {size / 1e6:.1f} MB -> {SITE / 'index.html'}")
    return flights


def write_data_js(folder, data):
    """The page's copy of a flight's data: the same JSON, as a script that hands it to the page."""
    (folder / "data.js").write_text("(window.PV_DATA = window.PV_DATA || {})[" + json.dumps(data["id"]) + "] = "
                                    + json.dumps(data, separators=(",", ":"), default=str) + ";\n")


def serve(port=8765, log=print):
    """Serve site/ on this machine (the page also opens straight from the folder; this is for other devices on the
    same network, or browsers that restrict file:// pages)."""
    import functools
    import http.server

    class Handler(http.server.SimpleHTTPRequestHandler):
        def end_headers(self):
            self.send_header("Cache-Control", "no-store")       # the files change on every rebuild
            super().end_headers()

        def log_message(self, *a):
            pass

    httpd = http.server.ThreadingHTTPServer(("0.0.0.0", port), functools.partial(Handler, directory=str(SITE)))
    log(f"serving {SITE} at http://localhost:{port}/  (Ctrl-C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass

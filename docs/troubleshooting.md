# Troubleshooting

Start with:

```bash
uv run planeview doctor
```

It prints what is installed, what your route library holds, and whether each data source answers. Most problems
show up there.

## Installing

**`uv: command not found`**
Install uv: <https://docs.astral.sh/uv/getting-started/installation/>. Open a new terminal afterwards.

**`uv sync` cannot find a Python**
plane-view needs Python 3.12 or 3.13. uv downloads one by itself unless that is turned off; `uv python install 3.13`
does it by hand.

**`planeview: command not found`**
Run it through uv, from the repository folder: `uv run planeview ...`.

## History

**`history update` is slow**
Each day is a 3 GB download. On a fast connection it takes about four minutes; on a slow one, much longer.
Nothing is lost if you stop it: a day is stored whole or not at all, and the next run carries on with the days
still missing.

**"no archive release for 2026-11-18"**
The newest day in the archive is usually yesterday or the day before, and an occasional day is missing
upstream. Choose another day.

**`history add` fails part-way with a network error**
Run it again. Days already stored are skipped.

**How much do I need?**
`uv run planeview pairs --min 5` lists the airport pairs you have at least five flights for. For one particular
route, a week of days gives a busy route dozens of flights. For a route flown once a day, or to cover summer and
winter routings, add one day a week across several months: `history add 2026-06-01..2026-10-31/7`.

## Forecasts

**"Airports unknown: give --from and --to"**
The flight number is not in your history and the lookup services do not know it either. Give the airports and
the time: `--from BRU --to AGP --dep 10:20`.

**"No departure time: give --dep HH:MM"**
The callsign is not in your history, so there is no usual departure time to go by. Add `--dep HH:MM`, in local
time at the departure airport.

**The route is a straight line**
No past flights were found between those airports, so the great circle was used. Check the codes, then
`uv run planeview pairs --min 1 --top 100000 | grep BRU` to see what you hold. More history fixes it.

**Few landmarks on a long flight**
Landmarks are fetched 120 map cells at a time, the ends of the flight first. Run
`uv run planeview landmarks --from LHR --to PHX` once to fetch the whole route (an hour or more), then forecast
again.

**A download fails (GeoNames, Natural Earth, Wikidata, imagery)**
These are public servers and are sometimes down or busy. Wait and run the same command again: everything already
fetched is kept. If one source never answers from your network, `doctor` will say which. Natural Earth has a
mirror that is tried by itself. `--landmarks offline` forecasts without asking Wikidata.

**The first forecast takes a long time**
It is downloading: place names for every country on the route, coastlines and relief (0.4 GB, once), terrain,
and imagery. The second forecast on the same route takes a few minutes, most of it drawing the pictures.

**It uses a lot of processor**
The window views are rendered on all cores for a minute or two. `nice uv run planeview predict ...` keeps the
computer responsive.

## The web page

**The page says "No flights yet"**
Nothing has been built into `site/`. Run a `predict`, or `uv run planeview site` if the folders in `site/f/` are
there.

**The page is blank, or the picker is empty, when opened from the folder**
Open `site/index.html` itself, with the rest of the `site/` folder beside it: the page loads `flights.js`,
`leaflet.js` and `f/<id>/data.js` from the same place. If your browser is set to block scripts in local files,
use `uv run planeview serve` instead.

**No PDF guides**
They need Chrome, Chromium, Edge or Brave. `doctor` says whether one was found. If yours is in an unusual place:
`export PLANEVIEW_CHROME=/path/to/chrome`. Then `uv run planeview build out/<id> --pdf-only`.

**`serve` says the address is in use**
Something else has that port. Use another: `uv run planeview serve 9000`.

## The app

**Xcode: "Signing for PlaneView requires a development team"**
Create `ios/Config/Local.xcconfig` as described in [app.md](app.md#building-it), then close and reopen the
project.

**Xcode: "Failed to register bundle identifier" or "not available"**
Someone else has that identifier. Change `PLANEVIEW_BUNDLE_PREFIX` in `ios/Config/Local.xcconfig` to something
that is yours.

**"Untrusted Developer" on the device**
Settings › General › VPN & Device Management › your Apple ID › Trust.

**The app stops opening after a week**
Apps signed with a free Apple ID last seven days. Build and run it again from Xcode; your flights stay.

**A pack does not appear in the app**
Tap **+** and choose the file, or check that it is in Files › On My iPhone › Plane View and still ends in
`.planeview` (some transfers add `.zip`: rename it back).

**No GPS in the air**
Hold the device close to the window for a minute or two. If it still has none, use *Wheels up now* at takeoff or
*We're here*, and the app carries the position along the route.

**The 3D view is flat**
The pack was made with `--relief cached` before any terrain was cached, or without a network. Make it again
without that option.

**Simulator apps quit while testing**
Do not make a high-detail pack or run a forecast at the same time, and launch with `-liveActivity NO`.

## Disk space

`uv run planeview history status` shows the size of the route library. To free space, in order of how easily
each is rebuilt:

| Folder | What | Rebuilt by |
|---|---|---|
| `cache/s2/` | satellite tiles | the next forecast or pack that needs them |
| `cache/dem_lo/` | terrain | the same |
| `out/`, `site/` | your forecasts, pages and packs | `predict`, `build`, `pack` |
| `cache/library/` | the route library | `history add`: four minutes a day |

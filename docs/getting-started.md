# Getting started

This takes you from nothing to a forecast for a flight of your own. Allow about half an hour the first time,
most of it downloads.

## What you need

- **macOS or Linux.** Windows is untested (it should work under WSL).
- **[uv](https://docs.astral.sh/uv/)**, which installs the right Python (3.12 or 3.13) and the libraries for you.
- **curl**. Every download goes through it. It is already on macOS and almost every Linux.
- **Disk space**: about 0.6 GB for a first European flight, plus about 40 MB for every day of flight history you
  keep. A well-fed install (ten weeks of history, several routes) is 4-5 GB.
- Optional: **Chrome, Chromium, Edge or Brave**, to print the PDF window guides. Without one they are skipped.
- Optional: **Xcode**, for the iPhone and iPad app. See [app.md](app.md).

No account, API key or paid service is needed.

## 1. Install

```bash
git clone https://github.com/antimega/plane-view.git
cd plane-view
uv sync
uv run planeview doctor
```

`doctor` lists what is installed and checks that each data source answers from your network. Everything should
say `ok` except the route library (empty so far) and the optional pieces.

## 2. Get some flight history

Forecast routes come from real past flights, taken from [adsb.lol](https://adsb.lol)'s open archive of aircraft
positions. You choose which days to keep:

```bash
uv run planeview history update 3        # the last 3 days that are available
```

Each day is a 3 GB download that is read as it streams and not stored; about 40 MB of it is kept, and it takes
around four minutes on a fast connection. The newest day available is usually yesterday or the day before.

Three days is enough to try things. For a route flown several times a day it already gives a fair picture; for a
route flown once a day, or to see the rarer routings, keep more:

```bash
uv run planeview history update 14                     # the last two weeks
uv run planeview history add 2026-06-01..2026-10-31/7  # one day a week across a season
uv run planeview history status                        # what you hold
uv run planeview pairs --min 5                         # airport pairs with at least 5 past flights
```

With no history at all a forecast still runs, along the great circle with a generic climb and descent.

## 3. Forecast a flight

By airports and departure time (local time at the departure airport):

```bash
uv run planeview predict --date 2027-03-14 --from BRU --to AGP --dep 10:20
```

Or by flight number, which looks the airports up:

```bash
uv run planeview predict --date 2027-03-14 --flight SN3733 --dep 10:20 --seat 23F
```

Past flights are recorded under their radio callsign, which for many airlines is not the flight number. When
your history has the flight under its number, `--dep` can be left out and the usual time is used; when it has
not, plane-view asks for it. Giving the flight number also weights the forecast towards that airline's flights.

Useful options:

| Option | What it does |
|---|---|
| `--seat 23F` | Only that side of the aircraft, and allows for the wing if the row is over or behind it. `L` or `R` also work. Without it, both sides are forecast. |
| `--dep 10:20` | Scheduled departure, local time. Needed when the flight number is not in your history. |
| `--aircraft A20N` | ICAO aircraft type, for seat letters and wing rows. Guessed from past flights otherwise. |
| `--cloud full` | Scale the chances by the cloud forecast (or, further ahead, the climate). By default a clear view is assumed and the cloud outlook is only reported. |
| `--no-pdf` | Skip the PDF guides. |
| `--landmarks offline` | Do not ask Wikidata for landmarks; use only what is already cached. |

The first forecast downloads place names, coastlines, terrain and imagery for the route and takes around a
quarter of an hour. Later ones on the same route take a few minutes, most of it drawing the pictures.

## 4. Look at it

```
out/2027-03-14_BRU-AGP/prediction.json     everything, as data
site/index.html                            the web page, for all your forecasts
site/f/2027-03-14_bru-agp/guide-L.pdf      phone-sized guide for the left-hand windows
site/f/2027-03-14_bru-agp/guide-R.pdf      ... and the right
```

Open `site/index.html` in a browser, or:

```bash
uv run planeview serve          # http://localhost:8765/
```

The page works offline and can be copied to any web host: see [web-page.md](web-page.md).

## 5. Take it on the flight

Print or save the PDF for your side, or put the flight on an iPhone or iPad:

```bash
uv run planeview pack out/2027-03-14_BRU-AGP      # out/packs/2027-03-14_BRU-AGP.planeview
```

and load that file into the Plane View app: [app.md](app.md).

## Where things are kept

Everything lives beside the code unless you say otherwise:

| Folder | What | Set with |
|---|---|---|
| `cache/` | everything downloaded (the big one) | `PLANEVIEW_CACHE` |
| `out/` | forecasts and flight packs | `PLANEVIEW_HOME` (moves all three) |
| `site/` | the web page | `PLANEVIEW_HOME` |

Other settings, all optional environment variables:

| Variable | What |
|---|---|
| `PLANEVIEW_CHROME` | the browser that prints the PDFs, if it is not found by itself |
| `PLANEVIEW_LANDMARKS` | `on` (default), `offline` (cached only) or `off` |
| `PLANEVIEW_TERRAIN` | `off` ignores terrain when working out what is in view (faster, worse in mountains) |
| `AEROAPI_KEY` | a FlightAware AeroAPI key: see [data-sources.md](data-sources.md#flightaware-aeroapi-optional) |

## Checking your install

```bash
uv run pytest          # unit tests: no network needed
```

If something does not work, see [troubleshooting.md](troubleshooting.md).

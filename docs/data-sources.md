# Data sources and licences

plane-view ships no data. It downloads what a forecast needs, the first time it needs it, from the open sources
below, and keeps it in `cache/`. All of them are free and none needs an account or a key.

`uv run planeview doctor` checks that each one answers from your network.

## The sources

| Source | Used for | Licence | Notes |
|---|---|---|---|
| [adsb.lol](https://adsb.lol) daily archives ([github.com/adsblol](https://github.com/adsblol), `globe_history_YYYY`) | past flights: the routes | ODbL 1.0 | about 3 GB a day, streamed and not stored; back to 2023; a day or two behind |
| Virtual Radar Server standing data, as republished by adsb.lol | flight number → airports, when your history has not seen the callsign | CC0 | one 4.5 MB file, kept two weeks |
| [adsbdb.com](https://www.adsbdb.com) | airline names; airports as a last resort | free API, no key | asked once per flight, cached a month, never required |
| [OurAirports](https://ourairports.com/data/) | airports, runways | public domain | |
| [OpenFlights](https://openflights.org/data) airlines | airline two-letter → three-letter codes | ODbL 1.0 | |
| [GeoNames](https://www.geonames.org) | place names, populations, time zones | CC BY 4.0 | one file per country on the route |
| [Natural Earth](https://www.naturalearthdata.com) 1:10m | coastlines, borders, lakes, rivers, cities, ranges, the maps' relief | public domain | about 0.4 GB, fetched once |
| [Wikidata](https://www.wikidata.org) query service | landmarks along the route | CC0 | one 1° cell at a time with a pause between, each kept six months |
| [Copernicus DEM](https://dataspace.copernicus.eu/explore-data/data-collections/copernicus-contributing-missions/collections-description/COP-DEM) GLO-90, from AWS open data | terrain: sight lines and the window views | free to use and share with attribution (below) | each 1° tile is reduced to a quarter of its resolution and the original is not kept |
| [EOxCloudless](https://cloudless.eox.at) 2024 (Sentinel-2 cloudless) | satellite imagery: window views, images from above, the app's map | **CC BY-NC-SA 4.0** | tiles are cached in `cache/s2/` |
| [Open-Meteo](https://open-meteo.com) forecast and ERA5 archive | the cloud outlook | CC BY 4.0; free for non-commercial use | optional: a failure only loses the outlook |
| [FlightAware AeroAPI](https://www.flightaware.com/commercial/aeroapi/) | schedules and filed routes | commercial, your own key | optional and off unless you set a key: see below |

The page also includes [Leaflet](https://leafletjs.com) 1.9.4 (BSD 2-Clause), which is in this repository at
`planeview/web/`.

## What that means for what you make

The **code** is MIT licensed. What it **produces** (web pages, PDF guides, flight packs) contains data from the
sources above and carries their terms:

- **Non-commercial only.** The satellite imagery is CC BY-NC-SA 4.0. Pages, PDFs and packs that include it may
  be used and shared for non-commercial purposes, with attribution, under the same licence.
- **Attribution.** The page, the PDFs and the packs carry these credits already. Keep them when you share:
  - Imagery: *EOxCloudless https://cloudless.eox.at by EOX IT Services GmbH (Contains modified Copernicus
    Sentinel data 2024)*
  - Terrain: *Copernicus DEM GLO-90 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018, provided
    under COPERNICUS by the European Union and ESA*
  - Routes: *adsb.lol (ODbL)*. Places: *GeoNames (CC BY 4.0), Natural Earth, Wikidata*. Weather: *Open-Meteo*.
- **Share-alike databases.** adsb.lol's data is under the ODbL. If you publish your route library itself (the
  SQLite file), it stays under the ODbL.

This is a summary to help, not legal advice. The licences themselves are at the links above.

## Being a good guest

- Every download identifies itself as `plane-view/<version>`, is cached, and is not asked for twice.
- Wikidata is queried one cell at a time with a pause, within its published limits. A forecast fetches at most
  120 new cells.
- adsb.lol and its feeders give this data away. If you use it a lot, consider
  [feeding](https://www.adsb.lol/docs/get-started/introduction/) or supporting them.
- Dropped connections, rate limits and server errors are retried with a growing pause, files are written whole
  or not at all, and an older cached copy is used when a source stays down.

## What is not available

- **The route an airline files for a flight** is not public in bulk. Routes here are what aircraft flew.
- **Mid-ocean and remote positions.** Receivers are on land. Long-haul routes are joined along the great circle
  where nothing was heard, and marked as estimated.
- **Live positions.** Nothing here tracks a flight in progress. In the air, the app uses the device's own GPS.

## FlightAware AeroAPI (optional)

Without it, everything works. With it, a forecast can look up the scheduled departure time and aircraft type of
a flight number your history has not seen, and, for flights to or from the United States, the filed route.

It is a paid service with a monthly free allowance on the personal tier. To use it, set your key:

```bash
export AEROAPI_KEY=your-key
```

or, on macOS, keep it in the Keychain, where it is found by itself:

```bash
security add-generic-password -a "$USER" -s plane-view-aeroapi -w
```

**Spending guard.** Every call is priced before it is made, and refused if it would take the month's spend past
a budget, or one run's past a per-run limit. The month's spend is taken as the larger of plane-view's own ledger
and FlightAware's account figure.

| Variable | Default | What |
|---|---|---|
| `PLANEVIEW_AEROAPI_BUDGET` | `4.00` | most to spend in a calendar month, US dollars |
| `PLANEVIEW_AEROAPI_PER_RUN` | `1.50` | most one command may spend |

```bash
uv run planeview aeroapi          # this month's spend against the budget
```

`--no-aeroapi` on `predict` leaves it out for one run even when a key is set.

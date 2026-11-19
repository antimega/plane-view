# How the forecast is made

A forecast answers one question for every minute of a flight: from this seat, what named thing could a person
pick out of the window, and how likely is it?

It is built in six steps. Each is one or two modules in `planeview/`.

```
past flights  ->  likely routes  ->  what is on the ground  ->  what is in view  ->  timeline  ->  pictures, page, PDF, pack
 (adsb.lol)        (route.py)         (gazetteer.py)            (visibility.py)     (predict.py)   (views, aerial, output/, pack)
```

## 1. A library of past flights

`sources/archive.py`, `sources/library.py`

[adsb.lol](https://adsb.lol) publishes every aircraft position its volunteers' receivers heard, one archive per
day. `planeview history` streams a day (about 3 GB, never stored), cuts each aircraft's trace into flights, keeps
every flight that climbed above 8,000 ft, and matches each end to an airport. A flight is stored as its airports,
callsign, aircraft type and a track simplified to 0.3 km: about 470 bytes. A day is about 85,000 flights and
40 MB, all in one SQLite file, `cache/library/legs.sqlite`. A day is stored whole or not at all.

## 2. The routes this flight is likely to take

`route.py`, `sources/flights.py`, `sources/airports.py`

Every past flight between the two airports is one way this flight could go. They are weighted: the same callsign
counts three times, the same airline twice, the same time of day half as much again, the same month a little
more. They are then grouped into **variants** by runway and path, and each variant gets a probability. The map
shows each variant's most typical flight; the first is the "likely route".

Special cases:

- **Fewer than three past flights**: departures from the origin and arrivals at the destination to and from
  nearby airports are spliced together.
- **None**: a great circle with a generic climb and descent.
- **Long haul**: receivers do not reach mid-ocean or most of Siberia, so the pieces either side are joined along
  the great circle, across midnight if need be. Those stretches are marked as estimated.
- **Ends nobody heard**: a flight whose first or last minutes no receiver caught is extended in a straight line
  to the runway, so that places beside the airport are seen from it.

A flight number is turned into airports from the library's own record of that callsign, then a bulk routes file,
then a lookup service. The departure time is the usual one for that callsign unless `--dep` gives it.

The same library always gives the same forecast: nothing is drawn at random.

## 3. What is on the ground

`gazetteer.py`, `sources/wikidata.py`, `ne.py`

For a corridor around all the routes:

- **GeoNames**: towns, peaks, capes, islands, harbours, beaches, parks, per country. How well known a place is
  comes from how many Wikipedia languages have an article on it.
- **Natural Earth**: cities with the outline of their built-up area, lakes, rivers, mountain ranges, seas,
  islands, coastlines, borders.
- **Wikidata**: landmarks within 40 km of the route: piers, lighthouses, castles, bridges, towers, stadiums, dams,
  wind farms, power stations, ports, reservoirs, lagoons, estuaries.

Wikidata is asked one 1° cell at a time and each answer is kept for six months. A forecast fetches at most 120 new
cells, the ends of the flight first, so a short flight is complete after one or two runs. A long-haul route
crosses one or two thousand cells: `planeview landmarks --from LHR --to PHX` fetches them all in one go (an hour
or more), and until then landmarks appear only near the two airports.

## 4. What is in view

`visibility.py`

From every point of every past flight, for every feature in range:

- distance, and bearing from the nose;
- angle below the horizon, with the Earth's curvature and atmospheric refraction;
- apparent size, and haze with distance;
- whether it falls in the **window cone**: from 25° behind straight-out to 55° ahead, and 0° to 50° below the
  horizon, less whatever the wing hides for that seat row;
- below 7,000 m, whether the **terrain** is in the way: the sight line is tested against the Copernicus elevation
  model, a target takes its real height, and a summit above the aircraft can be looked up at. In a valley
  approach this lists the peaks over the wing and not the next valley;
- **after dark**: lit towns, floodlit landmarks (stadiums, airports, bridges, towers, piers) and the dark gaps
  that parks and water make in a big city's lights, coming up through dusk.

The cone and the ranges at which things are recognisable were calibrated on window photographs from six flights:
231 photographs with a solved camera direction and 967 named features. People look from 25° behind to 55° ahead
(median 21° ahead) and 0° to 50° down (median 20°). Named settlements were a median 17 km away, peaks 40 km,
coast 23 km. The 90th percentile is about 12 km per 10,000 ft of height in climb and cruise, and about 24 km in
descent.

## 5. The timeline

`predict.py`, `weather.py`, `seat.py`

**Chance of seeing it** is the weighted share of past flights from which the feature was in view and
recognisable. *Likely* is 55% or more, *possible* 25% to 55%, *if lucky* below that.

Not everything in view is worth a line. Features are ranked by salience (size, fame, how distinctive the kind of
thing is), and the flight is cut into slots: three minutes long below 15,000 ft, five above. Each slot gets one
headline (two where the view is rich) and up to 16 "also in view" companions, with no more than 8 of one kind. A
town headlines over a landmark inside it, and a small distant feature cannot headline.

Each sighting carries: minutes after takeoff with its spread across past flights, an estimated clock time, the
side, the clock direction counted from the nose ("2 o'clock"), the angle below the horizon, the distance, the
height, and the light (day, dusk, night). Sightings in the second half also carry minutes before landing, which
hold better than minutes after takeoff when the cruise runs fast or slow.

Around the sightings goes **context**: coast and border crossings, seas, ranges, top of climb and descent,
sunrise and sunset, and which side the sun is on.

**Cloud.** A clear view is assumed. Within 15 days the Open-Meteo forecast is shown as an outlook and changes
nothing. `--cloud full` scales the chances by that forecast or, further ahead, by eight years of the same week
and hour from the ERA5 archive.

**Seat.** `--seat 23F` picks the side from the letter and the aircraft's seat layout, and narrows the view where
the row is over or just behind the wing.

## 6. Pictures and outputs

**Window views** (`views.py`). For each headline sighting, a picture from where the aircraft is predicted to be.
Every pixel is traced to the first ground it meets on the elevation model, or to the sea surface, so the skyline
is real and ridges hide what is behind them. It is then coloured from a cloud-free satellite mosaic of that spot,
shaded by slope, with snow above the season's snowline and a little haze. At night only the towns glow.
Coastlines, lake shores, rivers and borders are drawn on top, and the sighting is labelled. The mosaic is a
summer one, so fields and woods are not in the day's colours.

**From above** (`aerial.py`). A satellite image of the place from straight overhead, sized to it and turned to
the way you will be looking: you are below the bottom edge, so left and right in the picture are left and right
in the window. A dashed line is the likely route and an N marker shows north.

**Outputs** (`output/`, `pack.py`):

| Output | Where | What |
|---|---|---|
| Data | `out/<id>/prediction.json` | everything above, as JSON |
| Web page | `site/` | route map, flight profile, timeline with both pictures per sighting: [web-page.md](web-page.md) |
| PDF guides | `site/f/<id>/guide-L.pdf`, `guide-R.pdf` | phone-sized, one per side: a cover with the route and the top ten, then a page per half hour |
| Flight pack | `out/packs/<id>.planeview` | one file for the app: [flight-packs.md](flight-packs.md) |

## Limits worth knowing

- **It forecasts the usual, not today.** Air traffic control, weather and runway direction on the day decide the
  actual route. The variants and their probabilities are the honest answer; the app follows whichever one
  happens.
- **Cloud is not forecast by default**, and never beyond 15 days.
- **Thin history means a thin forecast.** A route with three past flights is a guess at the route with real
  geography along it. `planeview pairs` shows what you hold.
- **Oceans and deserts are quiet.** Where there is nothing named, the timeline says so.
- **The pictures are a model.** Terrain is about 360 m a cell and the imagery 10 m a pixel at best: mountains and
  coasts look right, a single building does not exist.
- **Left and right assume a normal flight.** A hold or a go-around turns the aircraft; the forecast does not
  know.

# PolyU Campus Navigator

**COMP4431 Artificial Intelligence (2026/27) — Group Project · Group 21**

A chatbot that finds walking routes across the PolyU campus, points you at the facilities
you need — recycling points, AEDs, water stations, accessible toilets, canteens, clinics,
faculty offices — and quotes their published opening hours, or says plainly that it does not
know. Ask in English or Chinese; the answer is spoken aloud and the route draws itself on a
2.5D campus map.

## Run it

```bash
python -m campus_nav.web --open      # web app  ->  http://127.0.0.1:8000
python -m campus_nav.cli --demo      # the same three turns in a terminal
```

Python 3.10+. The standard library only — nothing to install, no build step, no network.

Without an API key the app runs on its grammar parser and template replies, which is the
mode to fall back on if the demo Wi-Fi fails. With a key in `.env` (see `.env.example`) the
model fills the slots and phrases the sentence; it never chooses the route and never states
a fact that was not computed.

## Group members

> Fill in before submitting — the specification asks for every member's student ID and name.

| Student ID | English name | Main responsibility |
|---|---|---|
| _to fill in_ | _to fill in_ | _to fill in_ |
| _to fill in_ | _to fill in_ | _to fill in_ |
| _to fill in_ | _to fill in_ | _to fill in_ |

## What it does

**Map** — extruded building blocks in PolyU red with grey roofs, paved squares and lawns
painted flat, doorways, facility pins, and the walkway network. A red route appears only
while you are navigating. The legend in the bottom-left corner switches each layer on.

**Chat** — asks in plain language for a route between two places, re-plans when you report a
link closed, lists the nearest facility of a kind, quotes opening hours from the published
table, and abstains when the data has nothing.

```
you> how do I get from the main entrance to the library?
bot> Route: Main Entrance -> Pao Yue-kong Library
     Total: 230 m over 1 leg, 2 flights of steps
```

## Data

Four authored inputs, one generated file the app reads:

| File | Holds |
|---|---|
| `data/authored/campus_buildings.geojson` | the structure polygons — the 2.5D map |
| `data/authored/campus_paths.geojson`  | the walkways, one LineString per stretch |
| `data/authored/campus_nodes.geojson`  | doors and facilities traced from OpenStreetMap |
| `data/authored/campus_pois.geojson`   | every POI the PolyU campus map publishes |
| `data/authored/green_nodes.geojson`   | the GreenMap@PolyU recycling points |
| `data/processed_data/campus_graph.json` | **generated** — the only file the app reads |

```bash
python tools/build.py graph          # rebuild after editing any GeoJSON
python tools/build.py gaps           # where the walkway network does not join up
python tools/build.py audit          # what is wrong with the structures file
python tools/build.py names          # place names that will read badly
python -m pytest tests -q            # 91 tests
```

`tools/build.py` is deterministic arithmetic on what you drew. It measures along your lines,
never estimates, and stops with a message naming the place when something is missing rather
than inventing a route. To change the map: edit a GeoJSON in `data/authored/`, run
`python tools/build.py graph`, reload the page.

## Layout

| Path | What |
|---|---|
| `campus_nav/` | the application: graph, A*/UCS search, dialogue, NLU, knowledge base, web server |
| `campus_nav/static/` | the interface — HTML, CSS, one JavaScript file, no framework |
| `tools/build.py` | the only tool: build, audit, gaps, name report |
| `data/raw_data/` | what Overpass and the PolyU sites published, untouched |
| `data/authored/` | the GeoJSON you draw and edit |
| `tests/` | 91 unittest-style tests, no network |

## Known limitations

- **Opening hours are not linked to the map yet.** The knowledge base holds the published
  tables, but the join to the OSM-authored nodes was severed when the node set was rebuilt,
  so hours questions currently answer abstain. 19 of the 91 tests encode that gap.
- **No indoor geometry.** A facility inside a building is reached through the building; the
  reply gives the floor, not a corridor. Lifts and internal stairs are not modelled.
- **The walkway network has gaps.** Footways that pass metres apart without a shared node
  force detours; `python tools/build.py gaps` lists them, priced.
- **Positions are published positions, not a survey.** One campus-map coordinate per building
  is shared by every POI inside it — the floor field is what distinguishes them.
- **Distances are modelled.** Edge length is measured along the drawn walkway; the last few
  metres from a door to the path is a straight connector.

## Attribution

- Walkway geometry: © OpenStreetMap contributors, ODbL.
- Place names, positions and opening hours: the PolyU campus map and GreenMap@PolyU.
- Building outlines derived from the same OpenStreetMap data, edited for this project.


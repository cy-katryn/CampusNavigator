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

Python 3.10+ to run it — the standard library only, nothing to install, no network.
The interface ships as a committed bundle, so no build step is needed to demo it.

To change the interface (Node 18+), the source lives in `web/` and builds into
`campus_nav/static/`:

```bash
cd web && npm install
npm run build      # writes campus_nav/static/ — commit the result
npm run dev        # hot reload, proxying /api to the Python app on :8000
```

Components are styled with Tailwind utilities and take their colours from the palette
block in `web/src/styles.css`; icons come from [Lucide](https://lucide.dev). That
stylesheet keeps only what a utility cannot express: the palette, the avatar's paint and
state animations, and the map layers the drawing code creates by class name.

Without an API key the app runs on its grammar parser and template replies, which is the
mode to fall back on if the demo Wi-Fi fails. With a key in `.env` (see `.env.example`) the
model fills the slots and phrases the sentence; it never chooses the route and never states
a fact that was not computed.

## Group members

> Fill in before submitting — the specification asks for every member's student ID and name.

| Student ID | English name | Main responsibility |
|---|---|---|
| _to fill in_ | _to fill in_ | _to fill in_ |
| 23118556d | KWAN Kai Man | _to fill in_ |
| _to fill in_ | _to fill in_ | _to fill in_ |

## What it does

**Map** — extruded building blocks in PolyU red with grey roofs, paved squares and lawns
painted flat, doorways, facility pins, and the walkway network. Pins, letters and line
weights are drawn in map units, so they grow with the map as you zoom in, exactly as the
buildings do. Routes appear only while you are navigating: up to three ways there, each in
its own colour, with the chosen one drawn solid over the others. The legend in the
bottom-left corner switches each layer on.

**Chat** — asks in plain language for a route between two places, re-plans when you report a
link closed, lists the nearest facility of a kind, quotes opening hours from the published
table, and abstains when the data has nothing. When there is more than one way there, the
reply lists them — pick one and the map draws it.

```
you> how do I get from the main entrance to the library?
bot> Route: Main Entrance -> Pao Yue-kong Library
     Total: 230 m over 1 leg, 2 flights of steps
     There are 2 other ways there:
       2. 246 m over 1 leg, 1 flight of steps
       3. 254 m over 1 leg, 1 flight of steps
```

## Using voice navigation

Choose English, 普通话 or 廣東話 and press **Speak**. After dictating a route, review
the transcript and the origin and destination. To correct a place, type its name
in the corresponding field and choose a campus match. **Send** plans the route
with the confirmed places and any spoken restrictions. For example, try
“从图书馆到赛马会创新楼，不走楼梯” or “邊度有飲水機？”.
With **Auto-read** on, the navigator reads replies in the selected language.
**Replay** repeats the latest reply. Playback requires an appropriate system voice.

For offline recognition, install the local recognizer once on the computer running
the Python server. Setup requires Git, CMake, curl and a C/C++ compiler. It builds
whisper.cpp and downloads and verifies a roughly 574 MB multilingual model.

```bash
python tools/setup_voice.py
python -m campus_nav.web --open
```

Open the page on localhost or HTTPS and allow microphone access. Each recording
can last up to 30 seconds. Without the local model, voice input uses the browser's
speech recognition when supported, which may require internet access. To reuse
an installation, set `WHISPER_CLI` and `WHISPER_MODEL` to its executable and
model paths before starting the server.

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
python -m unittest discover -s tests # run the tests
```

`tools/build.py` is deterministic arithmetic on what you drew. It measures along your lines,
never estimates, and stops with a message naming the place when something is missing rather
than inventing a route. To change the map: edit a GeoJSON in `data/authored/`, run
`python tools/build.py graph`, reload the page.

## Layout

| Path | What |
|---|---|
| `campus_nav/` | the application: graph, A*/UCS search, dialogue, NLU, knowledge base, web server |
| `web/` | the interface source: React 18 + TypeScript, styled with Tailwind, icons from Lucide, built by Vite |
| `campus_nav/static/` | **generated** — the built interface the server serves |
| `tools/build.py` | build, audit, gaps, name report |
| `data/raw_data/` | what Overpass and the PolyU sites published, untouched |
| `data/authored/` | the GeoJSON you draw and edit |
| `tests/` | unittest suite, including voice and route regressions |

## Known limitations

- **Opening hours are not linked to the map yet.** The knowledge base holds the published
  tables, but the join to the OSM-authored nodes was severed when the node set was rebuilt,
  so hours questions currently answer abstain. Some opening-hours tests still expose this gap.
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


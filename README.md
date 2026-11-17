# PolyU Campus Navigator

**COMP4431 Artificial Intelligence (2026/27) — Group Project · Group 21**

A chatbot that guides you through navigating around PolyU campus, and points you to facilities
you might need (e.g. AEDs, water stations, accessible toilets, canteens, or clinics). You can
communicate with it in three language: English, Mandarin or Cantonese. It also supports speech
recognition allowing you to ask for directions hands-free while walking around campus.

## Run it

```bash
python -m campus_nav.web --open      # web app  ->  http://127.0.0.1:8000
```

Python 3.10+ to run it — the Python code uses the standard library only, so there is
nothing to `pip install`. **It runs with an empty `.env`**: no keys means the
deterministic grammar parses and the built-in scorer resolves place names. Copy
`.env.example` and add keys to switch individual layers to their model-backed path:

```bash
python tools/build.py embed --recreate   # fill the vector index with every place name
```

The interface ships as a committed bundle, so no build step is needed to demo it.

### The layers, and what happens without a key

Each layer has a deterministic twin, so a missing key or a dead network costs coverage,
never the application. The server prints which path is live at startup, and
`GET /api/status` exposes the same under `places.mode`.

| layer | primary | fallback | without it |
|---|---|---|---|
| understanding | LLM → JSON slots (`nlu.parse_with_llm`) | regex grammar (`nlu.parse_with_grammar`) | fewer phrasings understood; nothing breaks |
| name identity | embeddings + vector index (`place_index.py`) | built-in word scorer (`lexical.py`) | exact-ish names still resolve; no paraphrases |
| facts | BM25 over 561 campus-map records (`kb.py`) | — (always local) | — |
| routing | A* + UCS over the walkway graph (`search.py`) | — (always local, never a model) | — |
| wording | guarded LLM narration (`narrate.py`) | templates in `dialogue.py` | plainer sentences |

Two rules make this safe rather than optimistic. The model may *report* a place name but
never create one: `nlu._validated()` accepts a slot only if the resolver recognises it.
And a model sentence containing a number the engine did not compute is discarded by
`narrate.grounded_numbers()`. Prompts can be ignored; these two cannot.

The graph is read from `data/processed_data/campus_graph.json` **once, at startup**: after
`python tools/build.py graph` you must restart the server, or the browser keeps being served
the graph the old process is holding in memory. (A graph built before the `category` field
existed looks like a broken legend — every chip matches nothing, and every place falls into
"main locations".)

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

With a DeepSeek key in `.env` the model parses every message into slots
(intent, origin, destination, closures, stair restrictions) and phrases the reply; it
never chooses the route and never states a fact that was not computed. Without a key the
deterministic grammar does the same job for the phrasings it knows, and the app behaves
identically — it simply understands fewer ways of asking.

Place words are matched by embedding the phrase and reading the nearest place names back
from the vector index (`campus_nav/place_index.py`); when that index is unconfigured or
unreachable, `campus_nav/lexical.py` scores names directly against the graph instead.

## Group members

> Fill in before submitting — the specification asks for every member's student ID and name.

| Student ID | English name | Main responsibility |
|---|---|---|
| _to fill in_ | _to fill in_ | _to fill in_ |
| 23118556d | KWAN Kai Man | _to fill in_ |
| _to fill in_ | _to fill in_ | _to fill in_ |

## What it does

**Map** — extruded building blocks in PolyU red with grey roofs, paved squares and lawns
painted flat, plus the place markers and facility pins that the walkways connect. Pins,
letters and line weights are drawn in map units, so they grow with the map as you zoom in,
exactly as the buildings do. Routes appear only while you are navigating: up to three ways
there, each in its own colour, with the chosen one drawn solid over the others. The legend
in the bottom-left corner switches each layer on, grouped the way the campus map publishes
its places: **Places** (blocks & buildings, main locations, and the walkway links
themselves), **Amenities** (public transport, lecture theatres, AEDs, water stations,
toilets, catering, the clinic, lactation and prayer rooms) and **Offices** (faculties &
schools, administrative offices).

**Chat** — asks in plain language for a route between two places, re-plans when you report a
link closed, lists the nearest facility of a kind, quotes opening hours from the published
table, and abstains when the data has nothing. When there is more than one way there, the
reply lists them — pick one and the map draws it.

```
you> how do I get from Block V to the library?
bot> Route: Block V (Jockey Club Innovation Tower) -> Pao Yue-Kong Library
     Total: 206 m over 1 leg, 1 flight of steps
     Search (astar): expanded 63 nodes, peak frontier 35, 0.32 ms
     There are 2 other ways there:
       2. 221 m over 1 leg, 1 flight of steps
       3. 251 m over 1 leg, 2 flights of steps
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

Three authored inputs, one generated file the app reads:

| File | Holds |
|---|---|
| `data/authored/campus_buildings.geojson` | the structure polygons — the 2.5D map |
| `data/authored/campus_paths.geojson`  | the walkways, one LineString per stretch |
| `data/authored/campus_pois.geojson`   | every POI the PolyU campus map publishes |
| `data/processed_data/campus_graph.json` | **generated** — the only file the app reads |

```bash
python tools/build.py graph          # rebuild after editing any GeoJSON
python tools/build.py embed --recreate   # embed every place name into the vector index
```

`tools/build.py` is deterministic arithmetic on what you drew. It measures along your lines,
never estimates, and stops with a message naming the place when something is missing rather
than inventing a route. To change the map: edit a GeoJSON in `data/authored/`, run
`python tools/build.py graph`, reload the page.

## Layout

| Path | What |
|---|---|
| `campus_nav/` | the application: graph, A*/UCS search, dialogue, NLU, knowledge base, place index (embeddings + vector store) and its offline scorer, web server |
| `web/` | the interface source: React 18 + TypeScript, styled with Tailwind, icons from Lucide, built by Vite |
| `campus_nav/static/` | **generated** — the built interface the server serves |
| `tools/build.py` | build the graph, embed the place names |
| `data/raw_data/` | the scraped JSON file the knowledge base reads |
| `data/authored/` | the GeoJSON you draw and edit |

## Known limitations

- **Opening hours are joined by name, not by id.** The knowledge base holds the published
  tables (33 of 561 records have one), but the id join between the scraped records and the
  graph nodes does not match, so hours are found by place name and three records the map
  cannot name are unreachable. Fixing the key is the next data-plane change.
- **No indoor geometry.** A facility inside a building is reached through the building; the
  reply gives the floor, not a corridor. Lifts and internal stairs are not modelled.
- **The walkway network has gaps.** Footways that pass metres apart without a shared node
  force detours, and a place standing more than 120 m from any walkway is attached by one
  straight connector rather than a path.
- **The fallback tiers are weaker than the models they replace.** Without an LLM the app
  understands only phrasings the grammar enumerates; without the vector index a place must
  be named roughly as the map publishes it. Both are stated in the reply when they matter
  (`/api/status` reports which mode is live) rather than silently degrading.
- **Some published places are not on the walkway network.** Halls, the West Kowloon campus
  and a few schools on other sites sit 150–1600 m from the nearest drawn walkway. They stay
  in the graph — drawn with a dashed ring — and the reply says plainly that no walking route
  can reach them, instead of failing as if the search had broken.
- **Positions are published positions, not a survey.** One campus-map coordinate per building
  is shared by every POI inside it — the floor field is what distinguishes them.
- **Distances are modelled.** Edge length is measured along the drawn walkway; the last few
  metres from a door to the path is a straight connector.

## Attribution

- Walkway geometry: © OpenStreetMap contributors, ODbL.
- Place names, positions and opening hours: the PolyU campus map.
- Building outlines derived from the same OpenStreetMap data, edited for this project.
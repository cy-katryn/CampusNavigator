# PolyU Campus Navigator

**COMP4431 Artificial Intelligence (2026/27) — Group Project · Group 21**

A chatbot that walks you around the PolyU campus in plain language, points you at the
facilities you might need (AEDs, water stations, accessible toilets, canteens, clinics) and
quotes their opening hours — or says plainly that it does not know. It answers in **English,
Mandarin and Cantonese**, in text or by voice, and draws the route it recommends on a 2.5D map.

## What you need

- **Python 3.10+**. The application is standard library only — there is nothing to `pip install`.
- **Node 18+** only if you want to change the interface; the built bundle is committed.
- **Three API keys**, below. `LLM_API_KEY` is **required** — there is no offline parser: without
  a model the assistant says so instead of guessing. The other two degrade gracefully.

## Setup: the three keys

Copy `.env.example` to `.env` and fill in these. Each key switches one layer to its
model-backed path.

| key | description | where to get it | without it |
|---|---|---|---|
| **`LLM_API_KEY`** | the tool-calling assistant: it reads any phrasing, chooses the steps and phrases the answer (`campus_nav/agent.py`). **Any OpenAI-compatible endpoint works** — this key plus `LLM_MODEL`, and `LLM_BASE_URL` if it is not DeepSeek | [DeepSeek](https://platform.deepseek.com/api_keys) (default), or your own `LLM_BASE_URL` (`https://api.openai.com/v1`, a local server, …) | **required**: every reply becomes "I can't reach the language model right now" |
| **`EMBEDDINGS_API_KEY`** | multilingual name matching: a phrase is embedded and matched against every place's published names in both scripts (`campus_nav/place_index.py`) | any OpenAI-compatible `/embeddings` endpoint; the default is Alibaba Cloud Model Studio (DashScope) | the built-in word scorer resolves names instead — exact-ish names still work, paraphrases do not |
| **`PINECONE_API_KEY`** | the vector index that stores those 329 place vectors | [app.pinecone.io](https://app.pinecone.io) — create an index named `campus-navigator-places` | same as above: no index, no semantic matching |

```bash
cp .env.example .env          # then paste the three keys into .env
python tools/build.py embed --recreate    # fill the index with every place name (once)
python -m campus_nav.web --open           # -> http://127.0.0.1:8000
```

`--recreate` is needed whenever the embedding model — and therefore the vector dimension —
changes. The server prints which path is live at startup, and `GET /api/status` repeats it
(`places.mode` is `dense` or `lexical`).

**Optional — voice.** Speech *output* uses the browser. Speech *input* can run offline with a
local Whisper (needs Git, CMake, curl and a C/C++ compiler; downloads a ~574 MB model):

```bash
python tools/setup_voice.py
```

Check `GET /api/speech-status` for `"available": true`. The installer now copies the C++
runtime DLLs a MinGW build needs and then **runs the installed binary**, so a broken install
fails at install time instead of at demo time. If the status reports *"cannot start"*, re-run
the installer; if it names a `lib*.dll`, copy that DLL from your compiler's `bin` directory
into `.voice/bin/`. Without a working local model, the browser's own speech recognition is
used where supported.

## Try these

```
how do I get from Block V to the library?          -> route, alternatives, landmarks, walk time
I am at the library, nearest canteen right now?   -> the nearest canteen, with hours
also pass through Core F                          -> re-plans the same walk through Core F
stair-free please, I am in a wheelchair           -> the step-free alternative, or an honest refusal
is the catering area open right now?              -> today's hours, and whether it is open
when does the library open on Tuesday?            -> "the map publishes no opening hours for ..."
is it about 400 metres from Block V to the library?  -> "not quite — it's 206 m" (corrected from tool data)
我想去Z座，我现在在图书馆。                          -> Mandarin, same engine
邊度有洗手間？                                     -> Cantonese
```

## What it does

**Map** — extruded buildings in PolyU red over the walkway network, squares and lawns painted
flat, facility pins on top. Up to three routes are drawn at once, the chosen one solid, with
landmarks marked at the metre where you pass them. The legend groups places the way the campus
map publishes them: places, amenities, offices.

**Chat** — routes between named places, multi-stop walks, closures that re-plan, nearest
facility of a kind, opening hours quoted from the published table, and abstention when the data
has nothing. Each browser keeps its own conversation; the transcript survives a refresh.

**Voice** — press Speak in English, 普通话 or 廣東話; the transcript lands in the composer,
where a misheard block code can be corrected before you send it.

**Under the hood** — A*/UCS routing with an admissible Euclidean heuristic, embeddings for name
identity, BM25 for the facts. The model reads the message and chooses the steps; every number,
place id and timetable comes from the engine, and the model is given no place catalogue at all.
If the model cannot be reached the assistant says so rather than guessing.
Details, layer map and vocabulary tables: **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.

## Tests

```bash
python -m pytest tests -q                          # 78 tests: no network, no keys, no server
python tools/replay.py --live --diff golden.json    # 25 prompts x 3 languages, whole-turn diff (needs a key)
```

## Data

| File | Holds |
|---|---|
| `data/authored/campus_buildings.geojson` | structure polygons — the 2.5D map |
| `data/authored/campus_paths.geojson` | the walkways, one LineString per stretch |
| `data/authored/campus_pois.geojson` | every POI the PolyU campus map publishes |
| `data/processed_data/campus_graph.json` | **generated** — the only file the app reads at runtime |

```bash
python tools/build.py graph              # rebuild after editing any GeoJSON, then restart the server
python tools/build.py embed --recreate   # re-embed every place name
```

`tools/build.py` is deterministic arithmetic on what you drew: it measures along your lines and
stops with a message naming the place when something is missing, rather than inventing a route.

## Group members

| Student ID | English name | Main responsibility |
|---|---|---|
| 23100195D | CAO Wei | _to fill in_ |
| 23118556d | KWAN Kai Man | _to fill in_ |
| 23105016d | TAN Paula Katryn W. | _to fill in_ |

## Attribution

- Walkway geometry: © OpenStreetMap contributors, ODbL.
- Place names, positions and opening hours: the PolyU campus map.
- Building outlines derived from the same OpenStreetMap data, edited for this project.

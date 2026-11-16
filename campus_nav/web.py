"""A self-hosted web interface for the navigator -- standard library only.

    python -m campus_nav.web               # http://127.0.0.1:8000
    python -m campus_nav.web --port 9000   # somewhere else
    python -m campus_nav.web --open        # also launch the browser

Why http.server and not FastAPI/Flask
-------------------------------------
The whole project installs nothing, and a live demo is the worst possible moment to
discover that a virtualenv is broken. http.server ships with Python, so
"unplug the network, run one command, show the app" works on any machine with
Python 3.10+. The API is four endpoints and about 200 lines, which is smaller than
the dependency-management risk it replaces.

The pages are hand-written HTML/CSS/JS in campus_nav/static/. No build step, no CDN,
no npm -- so the demo also works offline, which matters because the speech-recognition
fallback and the map must both keep working when the lecture-room Wi-Fi does not.

Endpoints
---------
    GET  /                 the application shell
    GET  /static/<file>    its assets
    GET  /api/status       engine mode, graph source, knowledge-base coverage
    GET  /api/graph        nodes + edges, for drawing the map
    POST /api/chat         {"text": "..."} -> one reply, structured for the UI
    POST /api/reset        clear the conversation state
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import threading
import webbrowser
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from .config import (
    ENV_PATH,
    GRAPH_FILE,
    RAW_DIR,
    get_settings,
    graph_path,
    rel,
    shapes_path,
)
from .dialogue import Dialogue
from .graph import CampusGraph
from .kb import KnowledgeBase
from .llm import OpenRouterClient
from .narrate import Narrator
from .places import feature_height, shape_kind

STATIC_DIR = Path(__file__).resolve().parent / "static"


def load_shapes(path) -> list[dict]:
    """Read the map geometry you drew in geojson.io.

    data/authored/campus_buildings1.geojson is the whole 2.5D layer: every polygon you
    keep is drawn, every polygon you delete is gone. Nothing is clipped, filtered or
    guessed afterwards -- if it is in the file, it is deliberate.

        {"type": "Feature",
         "properties": {"name": "Block Z", "height": 28},
         "geometry": {"type": "Polygon",
                      "coordinates": [[[114.1781, 22.3050], [114.1783, 22.3050], ...]]}}

    Two things worth remembering: GeoJSON lists coordinates as [longitude, latitude]
    (the opposite of how they are usually spoken), and the first and last point of a
    ring must be identical.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))

    def rings_of(geometry: dict) -> list[dict]:
        """Polygons AND lines. A drawn path (LineString) is a shape: it renders as a
        polyline instead of being silently ignored."""
        kind = (geometry or {}).get("type")
        coords = (geometry or {}).get("coordinates") or []
        if kind == "Polygon":
            return [{"ring": coords[0], "line": False}] if coords else []
        if kind == "MultiPolygon":
            return [{"ring": polygon[0], "line": False} for polygon in coords if polygon]
        if kind == "LineString":
            return [{"ring": coords, "line": True}] if len(coords) >= 2 else []
        if kind == "MultiLineString":
            return [{"ring": line, "line": True} for line in coords if len(line) >= 2]
        return []

    rings = []
    broken: list[str] = []
    for index, feature in enumerate(raw.get("features", [])):
        props = feature.get("properties") or {}
        for shape in rings_of(feature.get("geometry") or {}):
            ring, is_line = shape["ring"], shape["line"]
            points = [[float(point[1]), float(point[0])] for point in ring if len(point) >= 2]
            if len(points) < (2 if is_line else 3):
                broken.append(str(props.get("id") or props.get("name") or f"feature #{index}"))
                continue
            entry = {
                "id": str(props.get("id") or f"drawn-{index}"),
                "ring": points,
                # English first, because a Chinese-first name sorts and reads worse in
                # the tooltip: "賽馬會創新樓 Jockey Club Innovation Tower"
                "name": str(props.get("name:en") or props.get("name") or ""),
                # "plaza" / "green" / "pitch" / "water" are drawn FLAT: a square, a lawn
                # or a pool has no height to extrude, and saying otherwise would be a lie
                "kind": str(props.get("kind") or shape_kind(props, is_line)),
                "line": is_line,
            }
            height = feature_height(props)
            if height:
                entry["height"] = height
            # "tone": "grey" is the one way a block may be drawn differently from the
            # rest of the campus; the value is passed through untouched.
            if props.get("tone"):
                entry["tone"] = str(props["tone"])
            # The letter to print on this structure: an explicit "display" column if you
            # ever add one, otherwise the campus block code already in "ref" -- L for the
            # library, VA for Shaw Amenities, Y for the Lee Shau Kee Building, V for the
            # Innovation Tower. No ref and no display means no letter at all: the map
            # must never abbreviate a name into a code of its own.
            label = (str(props.get("display") or "").strip()
                     or str(props.get("ref") or "").strip())
            if label:
                entry["display"] = label
            rings.append(entry)
    if broken:
        print(f"[map] {len(broken)} feature(s) in {Path(path).name} cannot be drawn "
              f"(too few points): {', '.join(broken[:6])}")
    return rings


# One dialogue object serves one conversation. The server is a demo, not a service:
# a lock keeps two browser tabs from interleaving their state, and /api/reset starts
# a fresh conversation. A production deployment would key state by session cookie.
LOCK = threading.Lock()


# --------------------------------------------------------------------- payloads
def route_payload(graph: CampusGraph, route) -> dict | None:
    """The route, with coordinates, so the browser can draw it without guessing."""
    if route is None:
        return None
    # one dot per PLACE. A junction is where the walkway bends, not a stop the user
    # should see a pin on; the drawn line still follows the junctions exactly,
    # because it comes from the segments below.
    path = [
        {
            "id": node_id,
            "name": graph.node(node_id).name,
            "lat": graph.node(node_id).lat,
            "lng": graph.node(node_id).lng,
        }
        for node_id in route.path
        if node_id in graph.nodes and graph.node(node_id).kind != "waypoint"
    ]
    # Each segment's geometry is walked in the direction the route travels it. An edge
    # stores its polyline one fixed way (u -> v); appending them in that order whatever
    # the travel throws a straight chord across every bend taken backwards, which is
    # what drew long straight lines through the middle of a route.
    segments = []
    cursor = None
    if route.path:
        start = graph.node(route.path[0])
        cursor = [start.lat, start.lng]
    for seg in route.segments:
        points = [list(point) for point in (seg.path or [])]
        if len(points) < 2:
            points = [[graph.node(seg.u).lat, graph.node(seg.u).lng],
                      [graph.node(seg.v).lat, graph.node(seg.v).lng]]
        if cursor is not None:
            head = (points[0][0] - cursor[0]) ** 2 + (points[0][1] - cursor[1]) ** 2
            tail = (points[-1][0] - cursor[0]) ** 2 + (points[-1][1] - cursor[1]) ** 2
            if tail < head:
                points.reverse()
        cursor = points[-1]
        segments.append({
            "from": seg.u,
            "to": seg.v,
            "metres": round(seg.metres, 1),
            "path": points,
        })

    return {
        "reachable": route.reachable,
        "metres": round(route.total_metres, 1) if route.reachable else 0.0,
        "path": path,
        "segments": segments,
        "stats": (
            {
                "algorithm": route.stats.algorithm,
                "nodes_expanded": route.stats.nodes_expanded,
                "frontier_peak": route.stats.frontier_peak,
                "ms": round(route.stats.elapsed_ms, 3),
            }
            if route.stats
            else None
        ),
        "explain": route.explain[:8],
    }


def spoken_summary(reply, graph: CampusGraph) -> str:
    """A sentence meant for ears, built only from computed facts.

    The screen shows the segment table; reading that table aloud would be noise. This
    is what the text-to-speech layer says, and it is assembled from the same numbers
    the search produced, so it cannot drift from them.
    """
    explicit = getattr(reply, "spoken", "") or ""
    if explicit:
        # the handler said what to read out; the route summary would only contradict it
        return explicit

    route = getattr(reply, "route", None)
    if route is not None and route.reachable and route.path:
        origin = graph.node(route.path[0]).name
        destination = graph.node(route.path[-1]).name
        legs = max(sum(1 for node in route.path if graph.node(node).kind != "waypoint") - 1, 1)
        flights = sum(1 for seg in route.segments if seg.stairs)
        extra = (
            f", including {flights} flight{'s' if flights != 1 else ''} of steps"
            if flights
            else ""
        )
        return (
            f"From {origin} to {destination} is about {route.total_metres:.0f} metres, "
            f"in {legs} leg{'s' if legs != 1 else ''}{extra}."
        )
    if route is not None and not route.reachable:
        return "I could not find a route with those restrictions. The options are on screen."
    facilities = getattr(reply, "facilities", None) or []
    if facilities:
        best = facilities[0]
        where = ", ".join(p for p in (best.get("building"), best.get("floor")) if p)
        metres = best.get("metres")
        parts = [f"The nearest match is {best.get('name')}"]
        if where:
            parts.append(f"at {where}")
        if metres is not None:
            parts.append(f"about {metres} metres away")
        spoken = " ".join(parts) + "."
        if len(facilities) > 1:
            spoken += f" There are {len(facilities)} on the map; the others are listed on screen."
        return spoken[:320]

    text = (reply.text or "").strip()
    first = text.split("\n\n")[0]
    lines = [line.strip(" -") for line in first.splitlines() if line.strip()]
    short = " ".join(lines[:3])
    return short[:320]


def chat_payload(graph: CampusGraph, reply) -> dict:
    speech = spoken_summary(reply, graph)
    # A reply that carries a route or a list of places has a short, friendly summary
    # (the same sentence that gets spoken). The UI leads with that and puts the
    # segment table / A* statistics behind a details toggle, so the interface does not
    # hand the user a receipt when a key is missing and a sentence when one is present.
    structural = bool(getattr(reply, "spoken", "")
                      or getattr(reply, "route", None) is not None
                      or (getattr(reply, "facilities", None) or []))
    return {
        "text": reply.text,
        "lead": speech if structural else "",
        "speech": speech,
        "narrated": bool(getattr(reply, "narrated", False)),
        "notes": list(getattr(reply, "notes", []) or []),
        "intent": getattr(getattr(reply, "slots", None), "intent", ""),
        # which NLU implementation produced these slots: "llm" or "grammar"
        "parser": getattr(getattr(reply, "slots", None), "source", ""),
        "route": route_payload(graph, getattr(reply, "route", None)),
        "facilities": list(getattr(reply, "facilities", []) or []),
    }


# ------------------------------------------------------------------------ env file
def write_env(updates: dict[str, str], path: Path = ENV_PATH) -> None:
    """Create or update .env, preserving comments and unrelated keys.

    The browser posts a key here, so this is the one place in the project that
    writes a secret to disk. It writes only to .env, which .gitignore covers, and
    never echoes the value back.
    """
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    if not existing:
        existing = ["# Local settings. Never commit this file -- it is in .gitignore.", ""]

    remaining = dict(updates)
    out: list[str] = []
    for line in existing:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in remaining:
                out.append(f"{key}={remaining.pop(key)}")
                continue
        out.append(line)
    out.extend(f"{key}={value}" for key, value in remaining.items())
    path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


# ----------------------------------------------------------------------- app state
class App:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.graph = CampusGraph.load(graph_path())
        self.knowledge = KnowledgeBase.from_raw(RAW_DIR, self.graph)
        self.footprints = load_shapes(shapes_path())
        client = OpenRouterClient(self.settings) if self.settings.llm_available else None
        narrator = Narrator(client) if self.settings.narration_available else None
        self.dialogue = Dialogue(self.graph, client, narrator, knowledge=self.knowledge)

    def apply_settings(self, api_key: str | None = None, model: str | None = None) -> dict:
        """Persist new model settings and use them immediately, without a restart."""
        updates: dict[str, str] = {}
        changes: dict[str, str] = {}
        if api_key is not None:
            updates["OPENROUTER_API_KEY"] = api_key.strip()
            changes["api_key"] = api_key.strip()
        if model is not None and model.strip():
            updates["OPENROUTER_MODEL"] = model.strip()
            changes["model"] = model.strip()
        if not updates:
            return self.status()

        write_env(updates)
        for key, value in updates.items():
            os.environ[key] = value          # keep the process in step with the file
        self.settings = replace(self.settings, **changes)

        client = OpenRouterClient(self.settings) if self.settings.llm_available else None
        narrator = Narrator(client) if self.settings.narration_available else None
        self.dialogue.client = client
        self.dialogue.narrator = narrator
        return self.status()

    def status(self) -> dict:
        return {
            "mode": self.settings.mode_label,
            "model": self.settings.model if self.settings.llm_available else "",
            "api_key_present": bool(self.settings.api_key),
            "graph_source": rel(GRAPH_FILE),
            "nodes": len(self.graph.nodes),
            "edges": len(self.graph.edges),
            "knowledge": self.knowledge.coverage(),
            "footprints": len(self.footprints),
            "map_meta": {
                k: self.graph.meta.get(k)
                for k in ("title", "generated_at", "inputs", "real_data", "derived", "counts")
                if self.graph.meta.get(k)
            },
        }

    def graph_payload(self) -> dict:
        return {
            "nodes": [
                {
                    "id": n.id,
                    "name": n.name,
                    "lat": n.lat,
                    "lng": n.lng,
                    "kind": n.kind,
                    "parent": n.parent,
                    "building": n.building,
                    "service": n.service,
                    "accepts": list(n.accepts),
                    "building": n.building,
                    "floor": n.floor,
                }
                for n in self.graph.nodes.values()
            ],
            "footprints": self.footprints,
            "edges": [
                {
                    "id": e.id,
                    "u": e.u,
                    "v": e.v,
                    "metres": e.metres,
                    "covered": e.covered,
                    "stairs": e.stairs,
                    "path": [list(point) for point in e.path],
                }
                for e in self.graph.edges
            ],
        }


# ----------------------------------------------------------------------- web server
class Handler(BaseHTTPRequestHandler):
    app: App  # injected by serve()

    server_version = "CampusNavigator/1.0"

    # -- helpers -----------------------------------------------------------
    def _send(self, code: int, body: bytes, content_type: str = "application/json; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: dict, code: int = 200) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def log_message(self, fmt: str, *args) -> None:  # quieter console
        return

    # -- routes ------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - http.server naming
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self._serve_static("index.html")
        if path.startswith("/static/"):
            return self._serve_static(path[len("/static/"):])
        if path == "/api/status":
            return self._json(self.app.status())
        if path == "/api/graph":
            return self._json(self.app.graph_payload())
        return self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/chat":
            payload = self._read_json()
            text = str(payload.get("text") or "").strip()
            if not text:
                return self._json({"error": "empty message"}, 400)
            with LOCK:
                reply = self.app.dialogue.handle(text)
            return self._json(chat_payload(self.app.graph, reply))
        if path == "/api/settings":
            payload = self._read_json()
            api_key = payload.get("api_key")
            model = payload.get("model")
            if api_key is not None:
                api_key = str(api_key).strip()
                if len(api_key) > 200 or "\n" in api_key or "\r" in api_key:
                    return self._json({"error": "that does not look like an API key"}, 400)
            if model is not None:
                model = str(model).strip()
                if len(model) > 120 or "\n" in model or "\r" in model:
                    return self._json({"error": "that does not look like a model id"}, 400)
            with LOCK:
                status = self.app.apply_settings(api_key, model)
            return self._json({"ok": True, "status": status, "env": str(ENV_PATH.name)})
        if path == "/api/reset":
            with LOCK:
                self.app.dialogue.state = type(self.app.dialogue.state)()
            return self._json({"ok": True})
        return self._json({"error": "not found"}, 404)

    def _serve_static(self, name: str) -> None:
        target = (STATIC_DIR / unquote(name)).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.is_file():
            return self._json({"error": "not found"}, 404)
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in {"application/javascript", "image/svg+xml"}:
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)


def serve(port: int = 8000, host: str = "127.0.0.1", open_browser: bool = False) -> int:
    app = App()
    Handler.app = app
    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/"
    print("=" * 66)
    print("  COMP4431 Group 21 - Campus Navigator (web)")
    print(f"  map    : {app.graph.summary()}")
    print(f"  graph  : {rel(GRAPH_FILE)}")
    print(f"  parser : {app.settings.mode_label}")
    print(f"  facts  : {app.knowledge.coverage()}")
    print(f"  open   : {url}")
    print("  ctrl-c to stop")
    print("=" * 66)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the Campus Navigator web interface")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--open", action="store_true", help="open the browser once it is up")
    args = parser.parse_args(argv)
    return serve(args.port, args.host, args.open)


if __name__ == "__main__":
    raise SystemExit(main())

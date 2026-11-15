"""Configuration, folder layout, and the single graph file the app reads.

Two jobs:
  1. load .env by hand (no third-party package needed) and expose the settings
  2. say where the data lives, and where the one routing graph is

Folder layout
-------------
    data/raw_data/       what Overpass and the PolyU websites published, untouched
    data/authored/       the GeoJSON you draw and edit in geojson.io
    data/processed_data/ campus_graph.json -- built from data/authored/, read by the app

One graph, no fallback. If campus_graph.json or campus_shapes.geojson is missing, the
app stops and names the file to create. A navigator that silently routes on invented
geometry is worse than one that refuses to start.

Precedence for every setting: real environment variable > .env file > default.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw_data"
AUTHORED_DIR = DATA_DIR / "authored"
PROCESSED_DIR = DATA_DIR / "processed_data"
ENV_PATH = PROJECT_ROOT / ".env"

# Inputs you create:
#   campus_nodes.geojson    Points    -> the places the bot can talk about
#   campus_paths.geojson    Lines     -> the walkways, one feature per stretch
#   campus_shapes.geojson   Polygons  -> the 2.5D map on the right of the screen
# and the one file the app reads, which tools/build.py produces:
#   campus_graph.json
OSM_FILE = RAW_DIR / "osm_orig.osm"                    # or the .geojson of the same export
NODES_FILE = AUTHORED_DIR / "campus_nodes.geojson"     # Points: entrances and facilities
GREEN_NODES_FILE = AUTHORED_DIR / "green_nodes.geojson"  # Points: GreenMap recycling / food waste
CAMPUS_POIS_FILE = AUTHORED_DIR / "campus_pois.geojson"  # Points: PolyU campus-map amenities

SHAPES_FILE = AUTHORED_DIR / "campus_buildings.geojson"  # Polygons: the structures
PATHS_FILE = AUTHORED_DIR / "campus_paths.geojson"     # Lines: the walkways
GRAPH_FILE = PROCESSED_DIR / "campus_graph.json"

# Every *_nodes.geojson in data/authored/ is a node file. Splitting by source is
# allowed (one file per dataset, verified one at a time); the graph merges them.
NODE_FILES = (NODES_FILE, GREEN_NODES_FILE, CAMPUS_POIS_FILE)

DEFAULT_MODEL = "google/gemma-4-26b-a4b-it:free"


def rel(path: Path) -> str:
    """Path relative to the project root, for messages that a person reads."""
    try:
        return str(path.relative_to(PROJECT_ROOT)).replace(chr(92), "/")
    except ValueError:
        return str(path)


# ------- settings
def load_env(path: Path = ENV_PATH) -> None:
    """Minimal .env reader: KEY=VALUE lines, # comments, optional quotes.

    Values already present in the real environment are never overwritten.
    """
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    api_key: str
    model: str
    app_name: str
    llm_enabled: bool
    timeout_s: int
    narrate: bool = True
    base_url: str = "https://openrouter.ai/api/v1"

    @property
    def llm_available(self) -> bool:
        """True only when the LLM layer is switched on AND a key is configured."""
        return self.llm_enabled and bool(self.api_key)

    @property
    def narration_available(self) -> bool:
        """True when the model is allowed to write the reply (Level B)."""
        return self.llm_available and self.narrate

    @property
    def mode_label(self) -> str:
        if self.llm_available and self.narrate:
            return "LLM understands and narrates (Level B), grammar fallback"
        if self.llm_available:
            return "LLM understands, template replies (Level A)"
        return "grammar only (no API key)"


def get_settings() -> Settings:
    load_env()
    return Settings(
        api_key=os.environ.get("OPENROUTER_API_KEY", "").strip(),
        model=os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        app_name=os.environ.get("OPENROUTER_APP_NAME", "COMP4431-Group21").strip(),
        llm_enabled=_flag("LLM_ENABLED", True),
        timeout_s=_int("LLM_TIMEOUT", 20),
        narrate=_flag("LLM_NARRATE", True),
    )


# ------- the one graph
def graph_path() -> Path:
    """The routing graph, or a stop with the exact command that creates it."""
    if not GRAPH_FILE.exists():
        raise FileNotFoundError(
            "No routing graph.\n"
            f"  expected: {rel(GRAPH_FILE)}\n"
            "  create it:  python tools/build.py graph"
        )
    return GRAPH_FILE


def shapes_path() -> Path:
    """The 2.5D map geometry, or a stop saying how to make one."""
    if not SHAPES_FILE.exists():
        raise FileNotFoundError(
            "No map geometry.\n"
            f"  expected: {rel(SHAPES_FILE)}\n"
            "  draw the buildings in geojson.io and save them there, or start from\n"
            "  the Overpass extract:  python tools/build.py shapes"
        )
    return SHAPES_FILE


"""Campus graph: nodes are places, edges are walkable links with access attributes.

The JSON file is the single source of truth for the demo map:

    {
      "meta": {"demo": true, "note": "..."},
      "nodes": [{"id": "VA", "name": "Shaw Amenities Building", "lat": 22.30, "lng": 114.17,
                 "kind": "building", "aliases": ["VA", "student canteen"]}],
      "edges": [{"id": "E01", "u": "VA", "v": "Z", "metres": 62.0,
                 "covered": true, "open": true}]
    }

Two invariants are enforced at load time, and they are the reason A* is allowed to
use straight-line distance as its heuristic:

  1. every edge length >= the straight-line distance between its endpoints
     -> h(n) never overestimates -> admissible -> A* is optimal
  2. h(goal) == 0

If either fails we raise instead of silently producing a wrong route.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

EARTH_RADIUS_M = 6_371_000.0

# Words that carry no discriminating information, so a name search must not require
# them to appear in a candidate. Without this, "the library is closed today" matches
# nothing at all because no place name contains the word "the".
STOPWORDS = frozenset({
    "the", "a", "an", "is", "are", "am", "was", "were", "be", "to", "of", "at", "in",
    "on", "for", "from", "near", "next", "by", "with", "and", "or", "but", "me", "my",
    "i", "you", "it", "its", "please", "can", "could", "would", "how", "do", "does",
    "get", "walk", "go", "going", "where", "which", "what", "when", "today", "now",
    "closed", "open", "there", "here", "this", "that",
})


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


@dataclass(frozen=True)
class Node:
    id: str
    name: str
    lat: float
    lng: float
    kind: str = "place"          # building | entrance | service | waypoint
    building: str = ""
    floor: str = ""
    aliases: tuple[str, ...] = ()
    parent: str = ""             # the building this thing stands inside, if any
    service: str = ""            # e.g. "Rechargeable Battery Collection"
    accepts: tuple[str, ...] = ()  # canonical item categories this point accepts
    source: str = ""             # "campus-map" | "greenmap" -- provenance of the place
    ref: str = ""                # that source's own record id (poi_id / uid)

    def distance_to(self, other: "Node") -> float:
        return haversine_m(self.lat, self.lng, other.lat, other.lng)


@dataclass(frozen=True)
class Edge:
    id: str
    u: str
    v: str
    metres: float
    covered: bool = False
    open: bool = True
    stairs: bool = False         # a flight of steps: drawn as rungs, not a plain line
    # The real shape of the walkway, as (lat, lng) points between u and v. Empty
    # means "we only know the endpoints", and the renderer falls back to a straight
    # line. When it IS present, metros is the length measured along it, so the
    # straight-line heuristic stays admissible (a curve is never shorter).
    path: tuple[tuple[float, float], ...] = ()

    def other(self, node_id: str) -> str:
        return self.v if node_id == self.u else self.u


@dataclass
class GraphViolation:
    edge_id: str
    detail: str


@dataclass
class CampusGraph:
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    # -- construction ------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path) -> "CampusGraph":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        nodes = {
            n["id"]: Node(
                id=n["id"],
                name=n.get("name", n["id"]),
                lat=float(n["lat"]),
                lng=float(n["lng"]),
                kind=n.get("kind", "place"),
                building=n.get("building", ""),
                floor=n.get("floor", ""),
                parent=n.get("parent", ""),
                aliases=tuple(n.get("aliases", ())),
                service=n.get("service", ""),
                accepts=tuple(n.get("accepts", ())),
                source=n.get("source", ""),
                ref=str(n.get("ref", "")),
            )
            for n in raw["nodes"]
        }
        edges = [
            Edge(
                id=e["id"],
                u=e["u"],
                v=e["v"],
                metres=float(e["metres"]),
                covered=bool(e.get("covered", False)),
                open=bool(e.get("open", True)),
                stairs=bool(e.get("stairs", False)),
                path=tuple(
                    (float(point[0]), float(point[1])) for point in e.get("path", ())
                ),
            )
            for e in raw["edges"]
        ]
        graph = cls(nodes=nodes, edges=edges, meta=raw.get("meta", {}))
        graph.validate()
        return graph

    # -- validation --------------------------------------------------------
    def validate(self, tolerance_m: float = 0.5) -> list[GraphViolation]:
        """Check referential integrity and the admissibility invariants."""
        problems: list[GraphViolation] = []
        seen: set[str] = set()
        for e in self.edges:
            if e.id in seen:
                problems.append(GraphViolation(e.id, "duplicate edge id"))
            seen.add(e.id)
            for endpoint in (e.u, e.v):
                if endpoint not in self.nodes:
                    problems.append(GraphViolation(e.id, f"unknown node {endpoint!r}"))
            if e.u not in self.nodes or e.v not in self.nodes:
                continue
            straight = self.nodes[e.u].distance_to(self.nodes[e.v])
            if e.metres + tolerance_m < straight:
                problems.append(
                    GraphViolation(
                        e.id,
                        f"length {e.metres:.1f} m < straight-line {straight:.1f} m "
                        "-> heuristic would overestimate",
                    )
                )
            if e.metres <= 0:
                problems.append(GraphViolation(e.id, "non-positive length"))
            if len(e.path) >= 2:
                walked = sum(
                    haversine_m(a[0], a[1], b[0], b[1])
                    for a, b in zip(e.path, e.path[1:])
                )
                if e.metres + tolerance_m < walked:
                    problems.append(
                        GraphViolation(
                            e.id,
                            f"length {e.metres:.1f} m < its own walkway geometry "
                            f"{walked:.1f} m",
                        )
                    )
        if problems:
            detail = "\n".join(f"  [{p.edge_id}] {p.detail}" for p in problems)
            raise ValueError(f"graph failed validation ({len(problems)} problems):\n{detail}")
        return problems

    # -- queries -----------------------------------------------------------
    def node(self, node_id: str) -> Node:
        return self.nodes[node_id]

    def heuristic(self, node_id: str, goal_id: str) -> float:
        """Straight-line metres. Admissible by construction (see validate)."""
        return self.nodes[node_id].distance_to(self.nodes[goal_id])

    def usable_edges(
        self,
        closed_edges: set[str] | None = None,
        closed_nodes: set[str] | None = None,
        *, avoid_stairs: bool = False,
    ) -> list[Edge]:
        """Exclude closures and, when requested, all links marked as stairs."""
        closed_edges = closed_edges or set()
        closed_nodes = closed_nodes or set()
        out = []
        for e in self.edges:
            if not e.open or e.id in closed_edges or (avoid_stairs and e.stairs):
                continue
            if (e.u in closed_nodes) or (e.v in closed_nodes):
                continue
            out.append(e)
        return out

    def adjacency(
        self,
        closed_edges: set[str] | None = None,
        closed_nodes: set[str] | None = None,
        *, avoid_stairs: bool = False,
    ) -> dict[str, list[tuple[str, float, Edge]]]:
        adj: dict[str, list[tuple[str, float, Edge]]] = {n: [] for n in self.nodes}
        for e in self.usable_edges(closed_edges, closed_nodes, avoid_stairs=avoid_stairs):
            adj[e.u].append((e.v, e.metres, e))
            adj[e.v].append((e.u, e.metres, e))
        return adj

    # -- name resolution ---------------------------------------------------
    def resolve(self, text: str) -> list[Node]:
        """Lookup by id, name or alias, with word-boundary matching.

        Three confidence levels, highest first:

          100  the user typed the name exactly
           60  the user's phrase is one of the node's names/aliases (whole words)
           40  a significant word inside the user's phrase appears in a node name
               -- this is what makes "how do I get to the library" find
               "Block L (Pao Yue-Kong Library)" without hand-written aliases

        Guards learned the hard way: a short alias such as "N" must never match
        inside another word ("e-N-trance"), so substring matching requires 3+
        characters on the candidate and 4+ on the user's word.
        """
        if not text:
            return []
        needle = " ".join(text.lower().replace("_", " ").split())
        if not needle:
            return []

        def has_words(short: str, long: str) -> bool:
            return re.search(rf"(?<![a-z0-9]){re.escape(short)}(?![a-z0-9])", long) is not None

        needle_words = [w for w in re.findall(r"[a-z0-9\u3400-\u9fff]{4,}", needle)]

        scored: list[tuple[int, Node]] = []
        for node in self.nodes.values():
            # A waypoint is a bend in a walkway, not a place. It has no name worth
            # saying and must never be offered as a destination.
            if node.kind == "waypoint" or not node.name:
                continue
            candidates = {node.id.lower(), node.name.lower(), *(a.lower() for a in node.aliases)}
            candidates = {c.strip() for c in candidates if c and c.strip()}
            best = 0
            for cand in candidates:
                if needle == cand:
                    best = max(best, 100)
                    continue
                if len(cand) >= 3 and (has_words(cand, needle) or has_words(needle, cand)):
                    # Score by how much of the candidate the needle accounts for, not
                    # by raw length: a long grouped name such as
                    # "entrance (pao yue-kong library)" must not outrank the building
                    # it belongs to when the user just said "library".
                    coverage = min(len(needle), len(cand)) / max(len(needle), len(cand))
                    best = max(best, 60 + round(30 * coverage))
            if best < 60:
                # The loose tier must not ignore the part of the name that discriminates:
                # "Core Z" is not Core A merely because both say "core", so every short
                # token in the query has to appear in the candidate too.
                short_tokens = [
                    token for token in re.findall(r"[a-z0-9]+", needle)
                    if len(token) < 4 and token not in STOPWORDS
                ]
                for cand in candidates:
                    if len(cand) < 3:
                        continue
                    if any(not has_words(token, cand) for token in short_tokens):
                        continue
                    for word in needle_words:
                        if has_words(word, cand):
                            best = max(best, 40 + min(len(word), 25))
                            break
            if best:
                scored.append((best, node))
        scored.sort(key=lambda t: (-t[0], t[1].name))
        return [n for _, n in scored]

    def summary(self) -> str:
        closed = sum(1 for e in self.edges if not e.open)
        return (
            f"{len(self.nodes)} nodes, {len(self.edges)} edges"
            + (f", {closed} closed" if closed else "")
        )

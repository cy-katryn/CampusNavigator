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


_BRACKETED = re.compile(r"\(([^)]*)\)")


def name_parts(name: str) -> tuple[str, str]:
    """Split a name into what a place IS and where it IS.

    "Toilet (Block Y)" -> ("toilet", "block y"). The bracket is a location qualifier, so
    a match on it must never outrank a match on the part that says what the thing is.
    """
    tail = " ".join(_BRACKETED.findall(name or "")).lower()
    head = " ".join(_BRACKETED.sub(" ", name or "").split()).lower()
    return head, tail


@dataclass(frozen=True)
class Node:
    id: str
    name: str
    lat: float
    lng: float
    kind: str = "place"          # building | entrance | service | waypoint
    building: str = ""
    floor: str = ""
    name_zh: str = ""            # the Chinese name, the only other name a node has
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
                name_zh=str(n.get("name_zh", "")),
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
    ) -> list[Edge]:
        """Filter first, then search -- exactly as the plan specifies.

        The only constraint a link can carry is whether it is currently open. A
        closure is reported by the user ("the link between C and D is closed"), so
        it is a runtime fact rather than an attribute we had to invent.
        """
        closed_edges = closed_edges or set()
        closed_nodes = closed_nodes or set()
        out = []
        for e in self.edges:
            if not e.open or e.id in closed_edges:
                continue
            if (e.u in closed_nodes) or (e.v in closed_nodes):
                continue
            out.append(e)
        return out

    def adjacency(
        self,
        closed_edges: set[str] | None = None,
        closed_nodes: set[str] | None = None,
    ) -> dict[str, list[tuple[str, float, Edge]]]:
        adj: dict[str, list[tuple[str, float, Edge]]] = {n: [] for n in self.nodes}
        for e in self.usable_edges(closed_edges, closed_nodes):
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
        return [node for _, node in self.resolve_scored(text)]

    def resolve_scored(self, text: str) -> list[tuple[int, "Node"]]:
        """Every node the phrase could mean, best first, with the score kept.

        A name has two halves: what the place IS and, in brackets, where it is --
        "Toilet (Block Y)" versus "Block Y (Lee Shau Kee Building)". A phrase that
        matches the first half wins outright; the same words inside the brackets are
        worth a full tier less, so "block y" finds the block and not the toilet in it.
        """
        if not text:
            return []
        needle = " ".join(text.lower().replace("_", " ").split())
        if not needle:
            return []

        def has_words(short: str, long: str) -> bool:
            return re.search(rf"(?<![a-z0-9]){re.escape(short)}(?![a-z0-9])", long) is not None

        needle_words = [w for w in re.findall(r"[a-z0-9\u3400-\u9fff]{4,}", needle)]
        short_tokens = [
            token for token in re.findall(r"[a-z0-9]+", needle)
            if len(token) < 4 and token not in STOPWORDS
        ]

        def score(cand: str) -> int:
            """100 exact, 60-90 whole words by coverage, 40-65 loose single words."""
            if not cand:
                return 0
            if needle == cand:
                return 100
            if len(cand) >= 3 and (has_words(cand, needle) or has_words(needle, cand)):
                return 60 + round(30 * min(len(needle), len(cand))
                                  / max(len(needle), len(cand)))
            if any(not has_words(token, cand) for token in short_tokens):
                return 0
            for word in needle_words:
                if has_words(word, cand):
                    return 40 + min(len(word), 25)
            return 0

        scored: list[tuple[int, Node]] = []
        for node in self.nodes.values():
            # A waypoint is a bend in a walkway, not a place: no name worth saying, and
            # never a destination.
            if node.kind == "waypoint" or not node.name:
                continue
            head, tail = name_parts(node.name)
            best = max(score(node.id.lower()), score(head))
            if node.name_zh:
                best = max(best, score(node.name_zh.lower()))
            if needle == node.name.lower():
                best = max(best, 96)          # typed out in full, brackets and all
            if best == 0 and tail:
                # The phrase only names WHERE this thing is, not what it is: "the library"
                # must find the Library, not the AED standing in it. Anything that matches
                # on what the place IS -- even loosely -- outranks this. No match in the
                # brackets means no match at all: an empty score must stay zero.
                bracketed = score(tail)
                if bracketed:
                    best = 20 + min(19, bracketed // 4)
            if best > 0:
                scored.append((best, node))
        scored.sort(key=lambda t: (-t[0], t[1].name))
        return scored

    def summary(self) -> str:
        closed = sum(1 for e in self.edges if not e.open)
        return (
            f"{len(self.nodes)} nodes, {len(self.edges)} edges"
            + (f", {closed} closed" if closed else "")
        )

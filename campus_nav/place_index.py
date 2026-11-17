"""Place-name resolution: embed the phrase, read the nearest names back from Pinecone.

This replaces the hand-written matchers (shared-word scoring, bracket tiers, prefix
rules, spelling tables). The contract the rest of the app depends on is one method:

    PlaceIndex.resolve(text) -> list[Node]

best first, empty when nothing is close enough. Several results mean the phrase is
genuinely ambiguous ("library" may name the building and the branches inside it), which
is what drives the existing "which one did you mean?" reply.

The index is built once, offline, by `python tools/build.py embed`; at runtime the graph
is still the source of truth, and a metadata id that is not in the graph is dropped
rather than trusted.
"""
from __future__ import annotations

from collections import OrderedDict

from .config import Settings, get_settings
from .embeddings import EmbeddingClient
from .graph import CampusGraph, Node
from .pinecone import PineconeStore

CACHE_SIZE = 256


class PlaceIndexError(RuntimeError):
    """The place index is not configured, empty, or does not match the graph."""


def indexable_nodes(graph: CampusGraph) -> list[Node]:
    """Every node a person could name. Waypoints are bends in a walkway, not places."""
    return [
        node for node in graph.nodes.values()
        if node.kind != "waypoint" and node.name
    ]


def document_text(node: Node) -> str:
    """The string embedded for one place: every name it is published under, plus where.

    Both scripts and the block code are included because a user may type any of them;
    the category and service words give the vector something to match a paraphrase
    against ("recycling point" -> "Rechargeable Battery Collection").
    """
    parts = [node.name]
    for name in (node.name_zh, node.name_zh_cn):
        if name and name not in parts:
            parts.append(name)
    if node.id and node.id != node.name:
        parts.append(node.id)
    for value in (node.category, node.service):
        if value:
            parts.append(value)
    if node.building:
        parts.append(f"in {node.building}")
    if node.floor:
        parts.append(f"floor {node.floor}")
    return " | ".join(parts)


class PlaceIndex:
    def __init__(self, graph: CampusGraph, settings: Settings | None = None,
                 *, embedder: EmbeddingClient | None = None,
                 store: PineconeStore | None = None) -> None:
        self.graph = graph
        self.settings = settings or get_settings()
        self.embedder = embedder or EmbeddingClient(self.settings)
        self.store = store or PineconeStore(self.settings)
        self._cache: OrderedDict[str, list[tuple[float, Node]]] = OrderedDict()

    # -- readiness ---------------------------------------------------------
    def check(self) -> dict:
        """Fail early and concretely when the index is missing or empty."""
        if not self.settings.vectors_available:
            raise PlaceIndexError(
                "Place-name matching needs EMBEDDINGS_API_KEY (or GITHUB_TOKEN) and "
                "PINECONE_API_KEY in .env."
            )
        stats = self.store.stats()
        namespaces = stats.get("namespaces") or {}
        if namespaces:
            total = sum(int(ns.get("vectorCount") or 0) for ns in namespaces.values())
        else:
            total = int(stats.get("totalVectorCount") or 0)
        if total <= 0:
            raise PlaceIndexError(
                f"Pinecone index {self.settings.pinecone_index!r} is empty. "
                "Fill it with: python tools/build.py embed"
            )
        return {"index": self.settings.pinecone_index, "vectors": total,
                "dimension": stats.get("dimension")}

    # -- retrieval ---------------------------------------------------------
    def scored(self, text: str, k: int | None = None) -> list[tuple[float, Node]]:
        """Raw nearest neighbours above the similarity floor, best first."""
        phrase = " ".join((text or "").split())
        if not phrase:
            return []
        key = phrase.casefold()
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            return cached

        vector = self.embedder.embed_one(phrase)
        hits = self.store.query(vector, k or self.settings.place_top_k)
        found: list[tuple[float, Node]] = []
        seen: set[str] = set()
        for score, meta in hits:
            node_id = str(meta.get("node_id") or "")
            node = self.graph.nodes.get(node_id)
            if node is None or node.kind == "waypoint" or node.id in seen:
                continue
            if score < self.settings.place_min_similarity:
                continue
            seen.add(node.id)
            found.append((score, node))

        self._cache[key] = found
        if len(self._cache) > CACHE_SIZE:
            self._cache.popitem(last=False)
        return found

    def resolve(self, text: str) -> list[Node]:
        """The place(s) the phrase means, best first; empty when it means nothing."""
        hits = self.scored(text)
        if not hits:
            return []

        # A phrase that names a building also matches the facilities standing inside it,
        # because their names contain the building label. Drop those followers; the
        # building is the answer, and they remain reachable by naming them directly.
        ids = {node.id for _, node in hits}
        grouped = [
            (score, node) for score, node in hits
            if not (node.kind == "facility" and node.parent and node.parent in ids)
        ]
        hits = grouped or hits

        # Only the top similarity band is an answer. A weaker neighbour is a worse
        # guess, not an alternative: "library" must not also offer "Block M".
        best = hits[0][0]
        floor = max(self.settings.place_min_similarity,
                    best - self.settings.place_ambiguity_band)
        return [node for score, node in hits if score >= floor]

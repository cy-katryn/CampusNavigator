"""Place-name resolution: which node does a phrase name?

Two resolvers, one contract
--------------------------
    PlaceIndex.resolve(text) -> list[Node]      best first, empty when nothing matches

1. **dense** (primary): embed the phrase, read the nearest place names back from the
   vector index. Semantic, so "somewhere to fill a bottle" finds a Water Station.
   Costs two network round-trips -- one embeddings call, one vector query.
2. **lexical** (fallback): word-boundary scoring over the graph's own names, in
   `campus_nav/lexical.py`. Needs nothing but the graph.

The mode is chosen once, at startup, by check(): if the keys are missing or the index
is empty, the index starts lexical and says so. If a *later* call fails (quota, expired
key, Wi-Fi gone), scored() degrades to lexical for the rest of the session instead of
raising into the dialogue layer. A chatbot that cannot find a place because a vector
database blinked is a worse product than one that finds it slightly differently.

The index is built once by `python tools/build.py embed`; at runtime the graph is still
the source of truth, and a metadata id that is not in the graph is dropped rather than
trusted.
"""
from __future__ import annotations

import re
import sys
from collections import OrderedDict

from . import lexical
from .config import Settings, get_settings
from .embeddings import EmbeddingClient, EmbeddingError
from .graph import CampusGraph, Node
from .pinecone import PineconeError, PineconeStore

CACHE_SIZE = 256

MODE_DENSE = "dense"
MODE_LEXICAL = "lexical"

# The lexical scorer returns coarse confidence tiers (0.40 / 0.44 / 0.70 / 1.00), so a
# cosine-sized band would keep one candidate out of a family that belongs together: at
# 0.04, "canteen" answered with a single canteen. Fifteen points keeps a tier together.
LEXICAL_BAND = 0.15


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
    against ("somewhere to fill a bottle" -> "Water Station").
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
        self.mode = MODE_LEXICAL          # until check() proves the dense index usable
        self.warning = ""                 # why it is lexical, for the operator
        self._cache: OrderedDict[str, list[tuple[float, Node]]] = OrderedDict()

    # -- readiness ---------------------------------------------------------
    def check(self) -> dict:
        """Probe the dense index once. NEVER fatal: report, then fall back.

        Returns the line the server prints at startup and exposes through /api/status.
        """
        info: dict = {
            "index": "-", "vectors": 0, "dimension": None,
            "mode": MODE_LEXICAL, "warning": "",
        }
        if not self.settings.vectors_available:
            self.warning = (
                "no EMBEDDINGS_API_KEY and PINECONE_API_KEY -- using the built-in "
                "name scorer"
            )
        else:
            try:
                stats = self.store.stats()
                namespaces = stats.get("namespaces") or {}
                total = (
                    sum(int(ns.get("vectorCount") or 0) for ns in namespaces.values())
                    if namespaces else int(stats.get("totalVectorCount") or 0)
                )
                if total > 0:
                    self.mode = MODE_DENSE
                    self.warning = ""
                    info.update(
                        index=self.settings.pinecone_index,
                        vectors=total,
                        dimension=stats.get("dimension"),
                        mode=MODE_DENSE,
                    )
                else:
                    self.warning = (
                        f"vector index {self.settings.pinecone_index!r} is empty -- run "
                        "python tools/build.py embed; using the built-in name scorer"
                    )
            except (PineconeError, EmbeddingError) as exc:
                self.warning = (
                    f"vector store unreachable ({exc}) -- using the built-in name scorer"
                )
        info["mode"] = self.mode
        info["warning"] = self.warning
        return info

    def degrade(self, reason: object) -> None:
        """Give up on the dense index for this session and say why, once."""
        if self.mode == MODE_LEXICAL:
            return
        self.mode = MODE_LEXICAL
        self.warning = f"fell back to the built-in name scorer: {reason}"
        self._cache.clear()
        print(f"  places : {self.warning}", file=sys.stderr)

    # -- retrieval ---------------------------------------------------------
    def scored(self, text: str, k: int | None = None) -> list[tuple[float, Node]]:
        """Candidate nodes for the phrase, best first, scores in 0.0-1.0."""
        phrase = " ".join((text or "").split())
        if not phrase:
            return []
        key = phrase.casefold()
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            return cached

        if self.mode == MODE_DENSE:
            try:
                found = self._dense_scored(phrase, k)
            except (EmbeddingError, PineconeError, PlaceIndexError) as exc:
                self.degrade(exc)
                found = self._lexical_scored(phrase, k)
        else:
            found = self._lexical_scored(phrase, k)

        self._cache[key] = found
        if len(self._cache) > CACHE_SIZE:
            self._cache.popitem(last=False)
        return found

    def _dense_scored(self, phrase: str, k: int | None) -> list[tuple[float, Node]]:
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
        return found

    def _lexical_scored(self, phrase: str, k: int | None) -> list[tuple[float, Node]]:
        found = lexical.resolve_scored(self.graph, phrase)
        if found and k:
            found = found[:k]
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

        # Only the top band is an answer. A weaker neighbour is a worse guess, not an
        # alternative: "library" must not also offer "Block M". Dense scores are cosines
        # and keep the published floor; lexical scores are tiers, so they get a tier.
        best = hits[0][0]
        if self.mode == MODE_DENSE:
            floor = max(self.settings.place_min_similarity,
                        best - self.settings.place_ambiguity_band)
        else:
            floor = best - LEXICAL_BAND
        top = [(score, node) for score, node in hits if score >= floor]

        # "library" matches "Pao Yue-Kong Library" (what the place IS) and, one tier
        # down, "Block L (Pao Yue-Kong Library)" (where it is). When a phrase matches
        # what a place IS, the same words inside a bracket are not an alternative --
        # they are the same answer written the long way.
        if self.mode == MODE_LEXICAL and len(top) > 1:
            # The same words the scorer used: "the library is closed" must test against
            # "library", not against "library" and "closed" -- no place is named "closed",
            # so requiring both silently switched this preference off.
            words = lexical.content_words(text)
            if words:
                by_head = [
                    pair for pair in top
                    if all(re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])",
                                     lexical.name_parts(pair[1].name)[0])
                           for w in words)
                ]
                if by_head:
                    top = by_head
            # One answer per name. "Pao Yue-Kong Library" (a place) and "Pao Yue-kong
            # Library (LIB)" (a facility with the same published name) are one
            # destination written twice; keep the place. Facilities that merely share a
            # service name -- the twenty-five toilets -- are distinct places and stay.
            owned = {
                lexical.name_parts(node.name)[0]
                for _, node in top if node.kind != "facility"
            }
            if owned:
                top = [
                    pair for pair in top
                    if pair[1].kind != "facility"
                    or lexical.name_parts(pair[1].name)[0] not in owned
                ] or top
        return [node for _, node in top]

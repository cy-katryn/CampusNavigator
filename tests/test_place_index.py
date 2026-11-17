"""Place-name resolution: banding, grouping and the no-match contract.

The network clients are replaced by stubs that return chosen similarities, so these
tests pin the policy -- what counts as an answer, and what counts as "I do not know" --
without embedding a single string.
"""
from __future__ import annotations

import unittest

from campus_nav.config import Settings
from campus_nav.graph import CampusGraph, Node
from campus_nav.place_index import PlaceIndex, PlaceIndexError, document_text

SETTINGS = Settings(
    api_key="", model="test", llm_enabled=False, timeout_s=1,
    place_min_similarity=0.30, place_ambiguity_band=0.05, place_top_k=5,
)


class StubEmbedder:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def embed_one(self, text: str) -> list[float]:
        self.texts.append(text)
        return [0.0]


class StubStore:
    def __init__(self, matches: list[tuple[float, str]]) -> None:
        self.matches = matches

    def query(self, vector: list[float], top_k: int) -> list[tuple[float, dict]]:
        return [(score, {"node_id": node_id}) for score, node_id in self.matches[:top_k]]


def make_graph() -> CampusGraph:
    nodes = {
        "L": Node(id="L", name="Pao Yue-Kong Library", lat=0.0, lng=0.0, kind="place"),
        "AED": Node(id="AED", name="AED (Library)", lat=0.0, lng=0.0, kind="facility",
                    parent="L"),
        "X": Node(id="X", name="Elsewhere", lat=0.0, lng=0.0, kind="place"),
        "W": Node(id="W", name="", lat=0.0, lng=0.0, kind="waypoint"),
    }
    return CampusGraph(nodes=nodes, edges=[], meta={})


def make_index(matches: list[tuple[float, str]]) -> tuple[PlaceIndex, StubEmbedder]:
    embedder = StubEmbedder()
    index = PlaceIndex(make_graph(), SETTINGS, embedder=embedder, store=StubStore(matches))
    return index, embedder


class PlaceIndexTests(unittest.TestCase):
    def test_only_the_top_band_is_an_answer(self) -> None:
        index, _ = make_index([(0.90, "L"), (0.50, "X")])
        self.assertEqual([node.id for node in index.resolve("the library")], ["L"])

    def test_close_scores_are_reported_as_ambiguous(self) -> None:
        index, _ = make_index([(0.90, "L"), (0.88, "X")])
        self.assertEqual([node.id for node in index.resolve("library")], ["L", "X"])

    def test_facility_inside_a_named_building_is_dropped(self) -> None:
        # The AED's own label contains the building name, so it scores alongside it.
        index, _ = make_index([(0.90, "AED"), (0.88, "L")])
        self.assertEqual([node.id for node in index.resolve("library")], ["L"])

    def test_nothing_above_the_floor_is_no_match(self) -> None:
        index, _ = make_index([(0.20, "L")])
        self.assertEqual(index.resolve("library"), [])

    def test_unknown_and_waypoint_ids_are_ignored(self) -> None:
        index, _ = make_index([(0.99, "missing"), (0.98, "W"), (0.50, "L")])
        self.assertEqual([node.id for node in index.resolve("library")], ["L"])

    def test_repeated_phrase_embeds_once(self) -> None:
        index, embedder = make_index([(0.90, "L")])
        index.resolve("library")
        index.resolve("library")
        self.assertEqual(len(embedder.texts), 1)

    def test_document_text_carries_both_scripts(self) -> None:
        node = Node(id="L", name="Pao Yue-Kong Library", lat=0.0, lng=0.0,
                    name_zh="包玉剛圖書館", name_zh_cn="包玉刚图书馆", kind="place")
        text = document_text(node)
        self.assertIn("Pao Yue-Kong Library", text)
        self.assertIn("包玉剛圖書館", text)
        self.assertIn("包玉刚图书馆", text)

    def test_missing_keys_fail_loudly(self) -> None:
        index = PlaceIndex(make_graph(), SETTINGS, embedder=StubEmbedder(),
                           store=StubStore([]))
        with self.assertRaises(PlaceIndexError):
            index.check()


if __name__ == "__main__":
    unittest.main()

"""Tests for the graph and the search algorithms.

Run with either:  python -m unittest discover -s tests -v
                  python -m pytest -q          (if pytest is installed)

No network, no API key, no third-party packages. Node ids are looked up by name
rather than hard-coded, so rebuilding the graph from the upstream campus map does
not break the suite.
"""
from __future__ import annotations

import heapq
import itertools
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from campus_nav.graph import CampusGraph  # noqa: E402
from campus_nav.search import astar, compare, search, ucs  # noqa: E402

from campus_nav.config import graph_path  # noqa: E402

GRAPH_PATH = graph_path()
# Scratch files go inside the repo: some sandboxes deny access outside the workspace,
# and a failing test should be about the code, not the temp directory.
TMP_DIR = Path(__file__).resolve().parent / "_tmp"


def find_id(graph: CampusGraph, needle: str, kind: str | None = None) -> str:
    needle = needle.lower()
    for node in graph.nodes.values():
        if kind and node.kind != kind:
            continue
        if needle in node.name.lower() or needle == node.id.lower():
            return node.id
    raise AssertionError(f"no node matching {needle!r}")


def reference_dijkstra(graph: CampusGraph, start: str, goal: str, **kwargs) -> float | None:
    """Independent implementation used to check A* optimality.

    Deliberately written without the heuristic so a bug shared between A* and UCS
    cannot hide: this is plain Dijkstra over the same filtered edges.
    """
    adj = graph.adjacency(kwargs.get("closed_edges"), kwargs.get("closed_nodes"))
    dist = {start: 0.0}
    pq = [(0.0, next(itertools.count()), start)]
    done = set()
    while pq:
        d, _, node = heapq.heappop(pq)
        if node in done:
            continue
        if node == goal:
            return d
        done.add(node)
        for neighbour, weight, _edge in adj.get(node, ()):
            nd = d + weight
            if nd < dist.get(neighbour, float("inf")):
                dist[neighbour] = nd
                heapq.heappush(pq, (nd, next(itertools.count()), neighbour))
    return None


def write_scratch(name: str, payload: dict) -> Path:
    TMP_DIR.mkdir(exist_ok=True)
    path = TMP_DIR / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class TestGraphIntegrity(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.graph = CampusGraph.load(GRAPH_PATH)
        cls.entrance = find_id(cls.graph, "main entrance")
        cls.library = find_id(cls.graph, "library")

    def test_graph_loads_and_validates(self):
        self.assertGreater(len(self.graph.nodes), 20)
        self.assertGreater(len(self.graph.edges), 20)
        # validate() raises on any violation; re-check so the reason is visible.
        self.assertEqual(self.graph.validate(), [])

    def test_every_edge_is_at_least_the_straight_line_distance(self):
        for edge in self.graph.edges:
            straight = self.graph.node(edge.u).distance_to(self.graph.node(edge.v))
            self.assertGreaterEqual(
                edge.metres + 0.5, straight,
                f"{edge.id} is {edge.metres} m but the straight line is {straight:.1f} m",
            )

    def test_heuristic_is_zero_at_the_goal(self):
        for node_id in self.graph.nodes:
            self.assertAlmostEqual(self.graph.heuristic(node_id, node_id), 0.0)

    def test_heuristic_never_exceeds_the_true_cost(self):
        """Admissibility spot-check: h(n) <= cheapest route from n to the goal."""
        goal = self.library
        for node_id in list(self.graph.nodes)[:30]:
            true_cost = reference_dijkstra(self.graph, node_id, goal)
            if true_cost is None:
                continue
            self.assertLessEqual(self.graph.heuristic(node_id, goal), true_cost + 1e-6)

    def test_corrupt_geometry_is_rejected(self):
        bad = {
            "nodes": [
                {"id": "A", "name": "A", "lat": 22.30, "lng": 114.17},
                {"id": "B", "name": "B", "lat": 22.31, "lng": 114.18},
            ],
            # ~1.5 km apart in reality, declared as 5 m -> the heuristic would overestimate
            "edges": [{"id": "E1", "u": "A", "v": "B", "metres": 5.0}],
        }
        with self.assertRaises(ValueError):
            CampusGraph.load(write_scratch("bad_geometry.json", bad))

    def test_unknown_endpoint_is_rejected(self):
        bad = {
            "nodes": [{"id": "A", "name": "A", "lat": 22.30, "lng": 114.17}],
            "edges": [{"id": "E1", "u": "A", "v": "GHOST", "metres": 10.0}],
        }
        with self.assertRaises(ValueError):
            CampusGraph.load(write_scratch("bad_endpoint.json", bad))

    def test_meta_records_where_the_graph_came_from(self):
        meta = self.graph.meta
        for key in ("inputs", "real_data", "derived", "counts"):
            self.assertIn(key, meta)
        self.assertGreater(meta["counts"]["places"], 100)
        self.assertEqual(
            meta["counts"]["nodes"], meta["counts"]["places"] + meta["counts"]["waypoints"]
        )


class TestSearchOptimality(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.graph = CampusGraph.load(GRAPH_PATH)
        cls.entrance = find_id(cls.graph, "main entrance")
        cls.library = find_id(cls.graph, "library")

    def _pairs(self, limit: int = 20):
        buildings = [n.id for n in self.graph.nodes.values() if n.kind == "place"][:6]
        return [(a, b) for a in buildings for b in buildings if a != b][:limit]

    def test_astar_matches_dijkstra(self):
        for start, goal in self._pairs():
            with self.subTest(start=start, goal=goal):
                expected = reference_dijkstra(self.graph, start, goal)
                result = astar(self.graph, start, goal)
                if expected is None:
                    self.assertFalse(result.reachable)
                else:
                    self.assertTrue(result.reachable)
                    self.assertAlmostEqual(result.total_metres, expected, places=3)

    def test_astar_and_ucs_agree_on_cost(self):
        for start, goal in self._pairs():
            with self.subTest(start=start, goal=goal):
                a, u = astar(self.graph, start, goal), ucs(self.graph, start, goal)
                self.assertEqual(a.reachable, u.reachable)
                if a.reachable:
                    self.assertAlmostEqual(a.total_metres, u.total_metres, places=3)

    def test_astar_never_expands_more_than_ucs(self):
        for start, goal in self._pairs():
            with self.subTest(start=start, goal=goal):
                a, u = astar(self.graph, start, goal), ucs(self.graph, start, goal)
                self.assertLessEqual(a.stats.nodes_expanded, u.stats.nodes_expanded)

    def test_path_is_connected_and_costs_add_up(self):
        result = astar(self.graph, self.entrance, self.library)
        self.assertTrue(result.reachable)
        self.assertEqual(result.path[0], self.entrance)
        self.assertEqual(result.path[-1], self.library)
        self.assertAlmostEqual(sum(s.metres for s in result.segments),
                               result.total_metres, places=3)
        for prev, nxt in zip(result.segments, result.segments[1:]):
            self.assertEqual(prev.v, nxt.u)

    def test_same_start_and_goal(self):
        result = astar(self.graph, self.library, self.library)
        self.assertTrue(result.reachable)
        self.assertEqual(result.total_metres, 0.0)
        self.assertEqual(result.path, [self.library])

    def test_unknown_node_raises(self):
        with self.assertRaises(KeyError):
            astar(self.graph, self.entrance, "NOT_A_PLACE")


class TestConstraintsAndDemoNarrative(unittest.TestCase):
    """The presentation turns, expressed as properties rather than fixed numbers."""

    @classmethod
    def setUpClass(cls):
        cls.graph = CampusGraph.load(graph_path())
        # Doorways are not in this node set; any named place serves the same purpose.
        cls.entrance = next(n.id for n in cls.graph.nodes.values() if n.kind == "place" and n.name)
        cls.library = next(n.id for n in cls.graph.nodes.values() if "library" in n.name.lower())
        cls.base = astar(cls.graph, cls.entrance, cls.library)
        cls.route_edges = [s.edge_id for s in cls.base.segments]

    def test_turn1_plain_route_is_reachable(self):
        self.assertTrue(self.base.reachable)
        self.assertGreater(self.base.total_metres, 0)

    def test_turn2_closing_a_link_on_the_chain_blocks_the_route(self):
        """At least one link on the route must be a cut edge, or turn 2 has no story."""
        severed = [
            eid for eid in self.route_edges
            if not astar(self.graph, self.entrance, self.library,
                         closed_edges={eid}).reachable
        ]
        self.assertTrue(severed, "every link on the route is redundant: the closure demo cannot fire")
        blocked = astar(self.graph, self.entrance, self.library, closed_edges={severed[0]})
        self.assertFalse(blocked.reachable)
        self.assertEqual(sorted(blocked.blocking["closed_edges"]), [severed[0]])

    def test_turn3_reopening_restores_the_original_route(self):
        reopened = astar(self.graph, self.entrance, self.library, closed_edges=set())
        self.assertTrue(reopened.reachable)
        self.assertAlmostEqual(reopened.total_metres, self.base.total_metres, places=1)

    def test_a_closure_never_shortens_the_route(self):
        for eid in self.route_edges:
            alt = astar(self.graph, self.entrance, self.library, closed_edges={eid})
            if alt.reachable:
                self.assertGreaterEqual(alt.total_metres, self.base.total_metres - 0.5)

    def test_closing_a_node_removes_it_from_the_path(self):
        middle = self.base.path[len(self.base.path) // 2]
        route = astar(self.graph, self.entrance, self.library, closed_nodes={middle})
        if route.reachable:
            self.assertNotIn(middle, route.path)

    def test_compare_reports_identical_cost(self):
        data = compare(self.graph, self.entrance, self.library)
        self.assertTrue(data["same_cost"])

    def test_service_nodes_accept_the_documented_materials(self):
        service = [n for n in self.graph.nodes.values() if n.kind == "facility" and n.accepts]
        self.assertTrue(service, "no service nodes carry a capability")
        for node in service[:50]:
            self.assertTrue(all(isinstance(a, str) and a for a in node.accepts))



"""A stair preference removes mapped edges in both directions, across turns."""
import unittest

from campus_nav.config import graph_path
from campus_nav.dialogue import Dialogue
from campus_nav.graph import CampusGraph, Edge, Node
from campus_nav.nlu import parse_with_grammar
from campus_nav.search import search


def campus_with_doors():
    # Both entrances lead into Block Z. Its anchor also has an edge: reaching
    # the anchor alone must not magically provide step-free access to a door.
    nodes = {
        "D": Node("D", "Core D", 0, 0),
        "Z": Node("Z", "Block Z", 0, 0),
        "near": Node("near", "East entrance", 0, 0, kind="entrance", parent="Z"),
        "far": Node("far", "West entrance", 0, 0, kind="entrance", parent="Z"),
    }
    edges = [Edge("east", "D", "near", 10, stairs=True),
             Edge("west", "D", "far", 25), Edge("centre", "D", "Z", 5)]
    return CampusGraph(nodes, edges)


class TestStairAvoidance(unittest.TestCase):
    def test_real_routes_in_three_languages(self):
        graph = CampusGraph.load(graph_path())
        for query in ("From Pao Yue-Kong Library to Jockey Club Innovation Tower without stairs",
                      "How do I get to Jockey Club Innovation Tower from Pao Yue-Kong Library, avoiding stairs?",
                      "从 Pao Yue-Kong Library 到 Jockey Club Innovation Tower，不走楼梯",
                      "由 Pao Yue-Kong Library 去 Jockey Club Innovation Tower，唔好行樓梯",
                      "From Pao Yue-Kong Library to Jockey Club Innovation Tower but I'd like to avoid all stairs",
                      "由 Pao Yue-Kong Library 去 Jockey Club Innovation Tower，途中盡量唔好行樓梯",
                      "从 Pao Yue-Kong Library 到 Jockey Club Innovation Tower，中途不要走楼梯"):
            with self.subTest(query=query):
                reply = Dialogue(graph).handle(query)
                self.assertEqual("Jockey Club Innovation Tower", reply.slots.destination_text)
                self.assertTrue(reply.route.reachable)
                self.assertTrue(reply.route.blocking["avoid_stairs"])
                self.assertFalse(any(segment.stairs for segment in reply.route.segments))
                self.assertEqual("Pao Yue-Kong Library", graph.node(reply.route.path[0]).name)
                self.assertIn("Jockey Club Innovation Tower", graph.node(reply.route.path[-1]).name)
                for alternative in reply.routes:
                    self.assertFalse(any(segment.stairs for segment in alternative.segments))

    def test_replan_can_switch_doors_and_reset_or_allow_stairs(self):
        graph = campus_with_doors()
        for enable in ("stairs are fine", "取消不走楼梯的限制", "可以行樓梯"):
            with self.subTest(enable=enable):
                dialogue = Dialogue(graph)
                self.assertEqual("near", dialogue.handle("from Core D to Block Z").route.path[-1])
                reply = dialogue.handle("No stairs, please.")
                self.assertEqual("far", reply.route.path[-1])
                distance = dialogue.handle("How far is it?")
                self.assertEqual(25, distance.comparison["ucs"]["metres"])
                self.assertEqual(25, distance.route.total_metres)
                self.assertEqual("near", dialogue.handle(enable).route.path[-1])
                dialogue.handle("No stairs")
                dialogue.handle("reset")
                self.assertFalse(dialogue.state.avoid_stairs)

    def test_mandarin_transcription_homophone(self):
        graph = CampusGraph.load(graph_path())
        dialogue = Dialogue(graph)
        expected = dialogue.handle("从图书馆到赛马会创新楼，不走楼梯。").route
        reply = Dialogue(graph).handle("从图书馆到赛马会创新楼,步走楼梯。")
        self.assertTrue(reply.route.reachable)
        self.assertEqual(expected.path, reply.route.path)
        self.assertTrue(reply.route.blocking["avoid_stairs"])
        self.assertFalse(any(seg.stairs for seg in reply.route.segments))
        for phrase in ("走楼梯", "徒步走楼梯", "步走楼梯时有什么要注意的"):
            self.assertIsNone(parse_with_grammar(phrase, graph).avoid_stairs)

    def test_missing_endpoint_keeps_preference(self):
        dialogue = Dialogue(campus_with_doors())
        self.assertEqual("origin", dialogue.handle("Go to Block Z without stairs").clarification.role)
        self.assertEqual("origin", dialogue.handle("我不想走楼梯").clarification.role)
        self.assertEqual("far", dialogue.handle("Core D").route.path[-1])

    def test_no_route_with_all_real_doors_blocked(self):
        dialogue = Dialogue(campus_with_doors())
        dialogue.state.closed_edges.add("west")
        reply = dialogue.handle("from Core D to Block Z, avoid stairs")
        self.assertFalse(reply.route.reachable)
        self.assertTrue(reply.route.blocking["avoid_stairs"])
        self.assertEqual([], reply.route.path)
        self.assertIsNone(dialogue.state.last_route)
        self.assertNotIn("re-open", reply.text)
        self.assertTrue(dialogue.handle("allow stairs").route.reachable)

    def test_filters_reverse_edges_and_zero_length_routes(self):
        graph = campus_with_doors()
        for algo in ("astar", "ucs"):
            blocked = search(graph, "near", "D", algorithm=algo, avoid_stairs=True)
            self.assertFalse(blocked.reachable)
            same = search(graph, "D", "D", algorithm=algo, avoid_stairs=True)
            self.assertTrue(same.reachable)
            self.assertTrue(same.blocking["avoid_stairs"])

    def test_mentioning_stairs_is_not_a_preference(self):
        slots = parse_with_grammar("Are there stairs near the library?", campus_with_doors())
        self.assertIsNone(slots.avoid_stairs)

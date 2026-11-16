"""Check that the navigator reads the same route and distance it draws."""
import unittest

from campus_nav.config import graph_path
from campus_nav.dialogue import Dialogue, Reply
from campus_nav.graph import CampusGraph
from campus_nav.search import RouteResult
from campus_nav.web import chat_payload, route_payload


class TestVoiceSummary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = CampusGraph.load(graph_path())

    def test_map_segments_follow_the_walk_from_block_z_to_the_library(self):
        route = Dialogue(self.graph).handle("From Block Z to the library").route
        segments = route_payload(self.graph, route)["segments"]
        for seg in segments:
            for node_id, point in [(seg["from"], seg["path"][0]), (seg["to"], seg["path"][-1])]:
                node = self.graph.node(node_id)
                self.assertAlmostEqual(node.lat, point[0], places=5)
                self.assertAlmostEqual(node.lng, point[1], places=5)

    def test_map_facts_in_all_three_languages(self):
        reply = Dialogue(self.graph, client=None).handle("from Core D to Block Z")
        self.assertTrue(any(segment.stairs for segment in reply.route.segments))
        for lang, stairs in [("en-US", "stairs"), ("zh-HK", "行樓梯"), ("zh-CN", "走楼梯")]:
            with self.subTest(lang=lang):
                payload = chat_payload(self.graph, reply, lang)
                route = payload["route"]
                speech = payload["speech"]
                self.assertEqual(payload["speech_lang"], lang)
                self.assertEqual(payload["lead"], speech)
                self.assertIn(str(int(route["metres"] + 0.5)), speech)
                self.assertLess(speech.index(route["path"][0]["name"]),
                                speech.index(route["path"][-1]["name"]))
                self.assertIn(stairs, speech)
                self.assertNotIn("leg", speech)

    def test_unreachable_route_and_english_fallback(self):
        reply = Reply("No route found.", route=RouteResult(False, "astar"))
        result = chat_payload(self.graph, reply, "zh-HK")
        self.assertIn("搵唔到路線", result["speech"])
        self.assertEqual("zh-HK", result["speech_lang"])
        self.assertEqual(chat_payload(self.graph, reply), chat_payload(self.graph, reply, "fr-FR"))

        facility = Reply("Details", spoken="The nearest AED is at the library.")
        result = chat_payload(self.graph, facility, "zh-CN")
        self.assertEqual(facility.spoken, result["speech"])
        self.assertEqual("en-US", result["speech_lang"])

    def test_clarification_names_the_unknown_and_confirmed_places(self):
        reply = Dialogue(self.graph, client=None).handle("from JO to Block Z")
        for lang, question in [("zh-HK", "邊度出發"), ("zh-CN", "哪里出发")]:
            with self.subTest(lang=lang):
                result = chat_payload(self.graph, reply, lang)
                self.assertIsNone(result["route"])
                self.assertIn("Z座", result["speech"])
                self.assertIn("jo", result["speech"].lower())
                self.assertIn(question, result["speech"])
                self.assertEqual(result["speech_lang"], lang)

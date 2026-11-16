"""New spoken directions must not inherit a stale endpoint from the previous trip."""
import unittest

from campus_nav.config import graph_path
from campus_nav.dialogue import Dialogue
from campus_nav.graph import CampusGraph


class TestEndpointOrder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = CampusGraph.load(graph_path())

    def existing_trip(self):
        dialogue = Dialogue(self.graph)
        self.assertTrue(dialogue.handle("From Core P to Block X").route.reachable)
        return dialogue

    def test_named_entrance_remains_the_destination_after_replanning(self):
        dialogue = self.existing_trip()
        entrance = "Stair Entrance (Block Z)"
        for text in ("How do I get to Stair Entrance (Block Z) from Core P?", "avoid stairs"):
            route = dialogue.handle(text).route
            self.assertTrue(route.reachable)
            self.assertEqual("Core P", self.graph.node(route.path[0]).name)
            self.assertEqual(entrance, self.graph.node(route.path[-1]).name)

    def test_destination_before_origin_replaces_previous_trip(self):
        reference = Dialogue(self.graph).handle("From Block Z to the library").route
        for query in (
            "How do I get to the library from Block Z?",
            "To the library, starting from Block Z.",
            "我想去图书馆，我现在在 Block Z。",
            "我想去圖書館，我而家喺 Block Z。",
            "去圖書館，由 Block Z 出發。",
        ):
            with self.subTest(query=query):
                reply = self.existing_trip().handle(query)
                self.assertEqual(reply.route.path, reference.path)

    def test_new_distance_endpoints_in_either_order(self):
        reference = Dialogue(self.graph).handle("From Block Z to the library").route
        for query in (
            "How far is the library from Block Z?",
            "What is the distance between Block Z and the library?",
            "從 Block Z 到圖書館有多遠？",
            "图书馆离 Block Z 有多远？",
            "由 Block Z 去圖書館有幾遠？",
        ):
            with self.subTest(query=query):
                reply = self.existing_trip().handle(query)
                self.assertEqual(reply.slots.intent, "distance")
                self.assertEqual(reply.route.path, reference.path)
                self.assertEqual(reply.route.total_metres, reference.total_metres)

    def test_unknown_new_place_is_clarified_with_correct_counterpart(self):
        dialogue = self.existing_trip()
        reply = dialogue.handle("How far is XYZ from Block Z?")
        self.assertIsNone(reply.route)
        self.assertEqual(reply.clarification.query, "XYZ")
        self.assertEqual(reply.clarification.role, "destination")
        self.assertEqual(reply.clarification.known_place, "Block Z")
        answer = dialogue.handle("图书馆")
        expected = Dialogue(self.graph).handle("From Block Z to the library")
        self.assertEqual(answer.route.path, expected.route.path)

    def test_unparsed_distance_question_does_not_return_the_previous_trip(self):
        dialogue = self.existing_trip()
        reference = dialogue.state.last_route
        self.assertEqual(dialogue.handle("How far is it?").route.path, reference.path)
        reply = dialogue.handle("What would be the distance near XYZ?")
        self.assertIsNone(reply.route)
        self.assertIsNotNone(reply.clarification)
        self.assertIsNone(dialogue.state.last_route)
        self.assertFalse(dialogue.state.destination or dialogue.state.origin)

    def test_location_statement_and_compact_chinese_routes(self):
        for canonical, questions in (
            ("From Core D to the library", (
                "I'm at Core D, I want to go to the library.",
                "我在 Core D，我想去图书馆。",
                "我而家喺 Core D，我想去圖書館。",
            )),
            ("From the library to Jockey Club Innovation Tower", (
                "图书馆到赛马会创新楼", "圖書館去賽馬會創新樓",
            )),
        ):
            reference = Dialogue(self.graph).handle(canonical).route
            for question in questions:
                with self.subTest(question=question):
                    self.assertEqual(self.existing_trip().handle(question).route.path, reference.path)

    def test_explicit_word_order_corrects_swapped_model_ids_and_wrong_intent(self):
        from unittest.mock import Mock
        from campus_nav.nlu import parse

        client = Mock()
        for intent in ("route", "facility_query", "close"):
            client.chat.return_value.json.return_value = {
                "intent": intent, "origin": "BLOCK_Z", "destination": "CORE_D",
            }
            with self.subTest(intent=intent):
                slots = parse("Can you help me get to Block Z from Core D without using stairs?",
                              self.graph, client)
                self.assertEqual((slots.intent, slots.origin_text, slots.destination_text),
                                 ("route", "Core D", "Block Z"))
                self.assertTrue(slots.avoid_stairs)

    def test_named_endpoints_do_not_override_a_facility_or_closure_intent(self):
        from campus_nav.nlu import parse_with_grammar
        for query, intent in (
            ("Where is the nearest AED on the route from Core D to Block Z?", "facility_query"),
            ("Where can I recycle batteries on the route from Core D to Block Z?", "green_query"),
            ("The walkway from Core D to Block Z is closed.", "close"),
        ):
            with self.subTest(query=query):
                self.assertEqual(parse_with_grammar(query, self.graph).intent, intent)

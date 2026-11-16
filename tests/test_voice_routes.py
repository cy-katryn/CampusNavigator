"""End-to-end dialogue cases from the campus voice demo, using the shipped map."""
import unittest

from campus_nav.config import graph_path
from campus_nav.dialogue import Dialogue
from campus_nav.graph import CampusGraph


class TestVoiceRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = CampusGraph.load(graph_path())

    def test_three_languages_use_the_same_endpoints_and_walk(self):
        examples = (
            (
                "From Core D to Block Z",
                "由 Core D 去 Block Z 點行？",
                "从 Core D 到 Block Z 怎么走？",
            ),
            (
                "From the library to Block Z",
                "圖書館去 Z 座點樣行？",
                "我想从图书馆到 Z 座怎么走？",
            ),
        )
        for english, cantonese, mandarin in examples:
            reference = Dialogue(self.graph).handle(english).route
            self.assertTrue(reference.reachable)
            for utterance in (cantonese, mandarin):
                with self.subTest(utterance=utterance):
                    route = Dialogue(self.graph).handle(utterance).route
                    self.assertIsNotNone(route)
                    self.assertEqual(route.path, reference.path)
                    self.assertEqual(route.total_metres, reference.total_metres)

    def test_unknown_origin_does_not_reuse_previous_destination(self):
        dialogue = Dialogue(self.graph)
        dialogue.handle("From Core P to the library")
        question = dialogue.handle("From JO to Block Z")
        self.assertIsNone(question.route)
        self.assertEqual(question.clarification.role, "origin")
        self.assertEqual(question.clarification.known_place, "Block Z")
        self.assertIsNone(dialogue.state.last_route)
        # Both names are supplied again, this time without an introductory "from".
        ambiguous = dialogue.handle("Jockey of Innovation to Block Z")
        self.assertIsNone(ambiguous.route)
        self.assertEqual(ambiguous.clarification.role, "origin")
        self.assertIn("Jockey Club Innovation Tower", ambiguous.clarification.candidates)
        answer = dialogue.handle("Jockey Club Innovation Tower")
        reference = Dialogue(self.graph).handle("From Jockey Club Innovation Tower to Block Z")
        self.assertTrue(answer.route.reachable)
        self.assertEqual(answer.route.path, reference.route.path)

    def test_new_destination_can_be_clarified_after_old_route(self):
        dialogue = Dialogue(self.graph)
        dialogue.handle("From Core P to Block Z")
        question = dialogue.handle("由 Core D 去 JO 點行？")
        self.assertIsNone(question.route)
        self.assertEqual(question.clarification.role, "destination")
        self.assertEqual(question.clarification.known_place, "Core D")
        answer = dialogue.handle("图书馆")
        reference = Dialogue(self.graph).handle("From Core D to the library")
        self.assertEqual(answer.route.path, reference.route.path)

    def test_unknown_destination_only_clears_the_previous_destination(self):
        dialogue = Dialogue(self.graph)
        dialogue.handle("From Core D to the library")
        question = dialogue.handle("Take me to XYZ")
        self.assertIsNone(question.route)
        self.assertEqual(question.clarification.query, "XYZ")
        self.assertEqual(question.clarification.role, "destination")
        self.assertEqual(question.clarification.known_place, "Core D")

    def test_missing_model_slot_is_clarified_but_provided_slots_take_precedence(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from campus_nav.nlu import parse

        client = Mock(settings=SimpleNamespace(llm_available=True))
        payload = {"intent": "route", "origin": None, "destination": "BLOCK_Z"}
        client.chat.return_value.json.return_value = payload
        slots = parse("From JO to Block Z", self.graph, client)
        self.assertEqual((slots.origin_text, slots.destination_text), ("JO", "BLOCK_Z"))
        payload["origin"] = "CORE_D"
        slots = parse("From Core D to Block Z avoiding stairs", self.graph, client)
        self.assertEqual((slots.origin_text, slots.destination_text), ("CORE_D", "BLOCK_Z"))
        self.assertEqual(slots.source, "llm")

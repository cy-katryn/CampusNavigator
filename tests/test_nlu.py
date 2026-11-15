"""Tests for the language layer: grammar parsing, name resolution, dialogue state.

These run with no API key. When LLM_ENABLED=0 (or no key is set) the grammar parser
must carry the whole demo -- that is the point of the fallback, so it gets tested
as a first-class path rather than as an afterthought.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["LLM_ENABLED"] = "0"  # force the offline path for every test

from campus_nav.dialogue import Dialogue  # noqa: E402
from campus_nav.graph import CampusGraph  # noqa: E402
from campus_nav.llm import LLMResult, extract_json  # noqa: E402
from campus_nav.nlu import parse_with_grammar, parse_with_llm  # noqa: E402

from campus_nav.config import graph_path  # noqa: E402

GRAPH_PATH = graph_path()


class TestExtractJson(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(extract_json('{"a": 1}'), {"a": 1})

    def test_fenced_json(self):
        self.assertEqual(extract_json('```json\n{"a": 2}\n```'), {"a": 2})

    def test_json_with_chatter(self):
        text = 'Sure! Here you go:\n{"intent": "route", "origin": null}\nHope that helps.'
        self.assertEqual(extract_json(text), {"intent": "route", "origin": None})

    def test_no_json(self):
        self.assertIsNone(extract_json("I cannot help with that."))

    def test_braces_inside_strings_do_not_break_it(self):
        self.assertEqual(extract_json('{"note": "a } b"}'), {"note": "a } b"})


class TestNameResolution(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.graph = CampusGraph.load(GRAPH_PATH)

    def test_alias_resolves_to_the_building(self):
        hits = self.graph.resolve("library")
        self.assertTrue(hits)
        self.assertIn("library", hits[0].name.lower())

    def test_short_alias_does_not_match_inside_another_word(self):
        """Regression: the alias "N" used to match inside "e-N-trance"."""
        hits = self.graph.resolve("entrance")
        self.assertTrue(hits)
        self.assertIn("entrance", hits[0].name.lower())
        # nothing one character long may be returned for a word like this
        self.assertTrue(all(len(n.id) > 3 for n in hits))

    def test_unknown_place_returns_empty(self):
        self.assertEqual(self.graph.resolve("the moon"), [])

    def test_chinese_alias_resolves(self):
        hits = self.graph.resolve("圖書館")
        self.assertTrue(hits, "Traditional Chinese alias should resolve")
        self.assertIn("library", hits[0].name.lower())


class TestGrammarParser(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.graph = CampusGraph.load(GRAPH_PATH)

    def test_demo_sentence_1_routes(self):
        slots = parse_with_grammar("How do I get from the entrance to the library?", self.graph)
        self.assertEqual(slots.intent, "route")
        self.assertIn("entrance", slots.origin_text.lower())
        self.assertIn("library", slots.destination_text.lower())

    def test_demo_sentence_2_detects_a_closed_link(self):
        slots = parse_with_grammar(
            "The link between Core D and the bus station is closed.", self.graph)
        self.assertEqual(slots.intent, "close")
        self.assertIn("closed", slots.closed_text.lower())
        # a closure moves the map, not the route request: the endpoints are reused
        self.assertFalse(slots.mentions_places)

    def test_a_closure_statement_is_not_a_timetable_question(self):
        statement = parse_with_grammar("The library is closed today.", self.graph)
        question = parse_with_grammar("is the canteen closed on Sunday?", self.graph)
        self.assertEqual(statement.intent, "close")
        self.assertEqual(question.intent, "hours_query")

    def test_reopen_is_understood(self):
        slots = parse_with_grammar("re-open everything", self.graph)
        self.assertEqual(slots.intent, "close")
        self.assertIn("re-open", slots.closed_text.lower())

    def test_chinese_question_is_detected(self):
        slots = parse_with_grammar("從入口到圖書館怎麼走？", self.graph)
        self.assertEqual(slots.language, "zh")

    def test_reset_keyword(self):
        self.assertEqual(parse_with_grammar("reset", self.graph).intent, "reset")

    def test_help_keyword(self):
        self.assertEqual(parse_with_grammar("what can you do", self.graph).intent, "help")


class TestDialogue(unittest.TestCase):
    def setUp(self) -> None:
        self.graph = CampusGraph.load(GRAPH_PATH)
        self.dialogue = Dialogue(self.graph, client=None)

    def test_three_turn_presentation_script(self):
        first = self.dialogue.handle("How do I get from the entrance to the library?")
        self.assertIsNotNone(first.route)
        self.assertTrue(first.route.reachable)
        baseline = first.route.total_metres
        self.assertGreater(baseline, 0)

        second = self.dialogue.handle("The library is closed today.")
        self.assertIn("Closed Block L", second.text)
        self.assertFalse(second.route.reachable)
        self.assertIn("Blocking", second.text)
        self.assertIn("re-open what is closed", second.text)

        third = self.dialogue.handle("re-open everything")
        self.assertIn("Re-opened", third.text)
        self.assertTrue(third.route.reachable)
        self.assertAlmostEqual(third.route.total_metres, baseline, places=1)

    def test_missing_origin_triggers_a_question(self):
        reply = self.dialogue.handle("How do I get to the library?")
        self.assertIsNone(reply.route)
        self.assertIn("starting from", reply.text.lower() + " starting from")

    def test_answers_the_follow_up_question(self):
        self.dialogue.handle("How do I get to the library?")
        reply = self.dialogue.handle("the entrance")
        self.assertIsNotNone(reply.route)
        self.assertTrue(reply.route.reachable)

    def test_out_of_scope_is_declined(self):
        reply = self.dialogue.handle("What is the capital of France?")
        self.assertIsNone(reply.route)
        self.assertIn("PolyU campus", reply.text)

    def test_reset_clears_state(self):
        self.dialogue.handle("How do I get from the entrance to the library?")
        self.assertIsNotNone(self.dialogue.state.origin)
        self.dialogue.handle("reset")
        self.assertIsNone(self.dialogue.state.origin)

    def test_green_query_lists_facilities(self):
        reply = self.dialogue.handle("where can I recycle batteries?")
        self.assertIsNone(reply.route)
        self.assertIn("battery", reply.text.lower())
        self.assertIn("-", reply.text)

    def test_green_query_with_unknown_item_admits_it(self):
        """The abstention must name the gap, not answer 'probably'."""
        reply = self.dialogue.handle("where do I recycle my old sofa?")
        self.assertIn("do not have enough information", reply.text.lower())
        self.assertIn("old sofa", reply.text.lower())
        self.assertIsNone(reply.route)

    # -- opening hours and facilities (knowledge base) ---------------------
    def test_hours_question_is_recognised(self):
        reply = self.dialogue.handle("is the 7-eleven open?")
        self.assertIn("monday", reply.text.lower())
        self.assertIn("right now", reply.text.lower())
        self.assertTrue(reply.facilities or "7-eleven" in reply.text.lower())

    def test_hours_answer_quotes_the_published_table(self):
        reply = self.dialogue.handle("what are the opening hours of the dental clinic")
        self.assertIn("dental clinic", reply.text.lower())
        # the source publishes 0900-1300 / 1400-1800; the reply must not invent
        self.assertIn("09:00-13:00", reply.text)
        self.assertIn("polyu campus map", reply.text.lower())

    def test_place_without_published_hours_is_not_guessed(self):
        reply = self.dialogue.handle("what time does the bookstore close")
        self.assertIn("does not publish opening hours", reply.text)

    def test_facility_query_lists_places_with_distance(self):
        reply = self.dialogue.handle("where is the nearest AED")
        self.assertIn("aed", reply.text.lower())
        self.assertTrue(reply.facilities)
        self.assertIn("node_id", reply.facilities[0])

    def test_route_request_still_beats_a_hours_word(self):
        """'how do I get to X' must plan a route even if the sentence says 'open'."""
        reply = self.dialogue.handle("how do I get from the entrance to the library, is it open?")
        self.assertIsNotNone(reply.route)
        self.assertTrue(reply.route.reachable)


class StubClient:
    """A stand-in for the OpenRouter client: returns one canned model reply."""

    class settings:            # narrate.py and nlu.py both look at this
        llm_available = True
        model = "stub"

    def __init__(self, text: str):
        self.text = text

    def chat(self, *args, **kwargs):
        return LLMResult(ok=True, text=self.text, model="stub", attempts=1)


class TestLlmSlots(unittest.TestCase):
    """The model may report what the user said. It may not create a place."""

    @classmethod
    def setUpClass(cls):
        cls.graph = CampusGraph.load(graph_path())

    def test_free_text_place_survives_the_model(self):
        client = StubClient('{"intent":"route","origin":"the entrance","destination":"library"}')
        slots = parse_with_llm("from the entrance to the library", self.graph, client)
        self.assertIsNotNone(slots)
        self.assertEqual(slots.source, "llm")
        self.assertEqual(slots.origin_text, "the entrance")
        # ... and the dialogue layer can resolve that wording to a real node
        self.assertEqual(len(Dialogue(self.graph)._preferred_matches(slots.origin_text)), 1)

    def test_an_invented_place_is_not_a_node(self):
        client = StubClient('{"intent":"route","destination":"Hogwarts"}')
        slots = parse_with_llm("take me to hogwarts", self.graph, client)
        self.assertNotIn(slots.destination_text, self.graph.nodes)

    def test_unknown_intent_falls_through_to_the_grammar(self):
        client = StubClient('{"intent":"book a flight"}')
        self.assertIsNone(parse_with_llm("book me a flight", self.graph, client))

    def test_the_place_catalogue_lists_every_building_but_not_every_bin(self):
        from campus_nav.nlu import _place_catalogue

        lines = _place_catalogue(self.graph).splitlines()
        buildings = [
            n for n in self.graph.nodes.values()
            if n.kind not in ("service", "waypoint") and n.name
        ]
        self.assertLessEqual(len(buildings), len(lines))
        self.assertLess(len(lines), len(self.graph.nodes), "the catalogue must stay shorter than the map")


if __name__ == "__main__":
    unittest.main(verbosity=2)

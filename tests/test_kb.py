"""Tests for the knowledge base: retrieval, opening-hours parsing, and abstention.

These are the tests that protect the honesty guarantees, so they do not merely check
that a string appears -- they pin the behaviour that matters:

    * the three notations PolyU publishes for opening hours all parse
    * "open now" is computed only from a published table, never guessed
    * a question the base cannot answer produces an explicit refusal
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from campus_nav.config import RAW_DIR, graph_path  # noqa: E402
from campus_nav.dialogue import Dialogue  # noqa: E402
from campus_nav.graph import CampusGraph  # noqa: E402
from campus_nav.kb import (  # noqa: E402
    HKT,
    KnowledgeBase,
    hours_summary,
    open_state,
    parse_slots,
    today_hours,
    tokenize,
)


def a_monday(hour: int = 10, minute: int = 0) -> datetime:
    """A Monday at a fixed time in Hong Kong -- so 'open now' tests never flake."""
    base = datetime(2026, 11, 9, hour, minute, tzinfo=HKT)
    return base + timedelta(days=(0 - base.weekday()) % 7)


class TestTokenize(unittest.TestCase):
    def test_splits_on_punctuation(self):
        self.assertEqual(tokenize("7-Eleven, G/F"), ["7", "eleven", "g", "f"])

    def test_chinese_becomes_characters_and_bigrams(self):
        tokens = tokenize("圖書館")
        self.assertIn("圖", tokens)
        self.assertIn("書館", tokens)

    def test_empty_input(self):
        self.assertEqual(tokenize(""), [])


class TestHoursParsing(unittest.TestCase):
    def test_colon_notation(self):
        self.assertEqual(parse_slots(["07:00 - 23:00"]), [("07:00", "23:00")])

    def test_dot_notation(self):
        self.assertEqual(parse_slots(["11.00 - 22.30"]), [("11:00", "22:30")])

    def test_compact_notation_used_by_the_clinics(self):
        self.assertEqual(parse_slots(["0900 - 1300"]), [("09:00", "13:00")])

    def test_multiple_slots_per_day(self):
        parsed = parse_slots(["0900 - 1300", "1400 - 1800"])
        self.assertEqual(parsed, [("09:00", "13:00"), ("14:00", "18:00")])

    def test_closed_is_not_a_time(self):
        self.assertEqual(parse_slots(["CLOSED"]), [])

    def test_garbage_is_dropped_not_guessed(self):
        self.assertEqual(parse_slots(["by appointment"]), [])

    def test_summary_quotes_the_source(self):
        summary = hours_summary({"Mon": ["0900 - 1300"], "Sun": ["CLOSED"]})
        self.assertIn("Monday 09:00-13:00", summary)
        self.assertIn("Sunday closed", summary)


class TestOpenState(unittest.TestCase):
    HOURS = {"Mon": ["09:00 - 13:00", "14:00 - 18:00"], "Sun": ["CLOSED"]}

    def test_open_during_a_published_slot(self):
        is_open, why = open_state(self.HOURS, a_monday(10))
        self.assertTrue(is_open)
        self.assertIn("until 13:00", why)

    def test_closed_between_slots(self):
        is_open, _ = open_state(self.HOURS, a_monday(13, 30))
        self.assertFalse(is_open)

    def test_no_table_is_unknown_not_closed(self):
        is_open, why = open_state({}, a_monday(10))
        self.assertIsNone(is_open)
        self.assertIn("no opening hours are published", why)

    def test_day_missing_from_the_table_is_unknown(self):
        is_open, why = open_state({"Tue": ["09:00 - 17:00"]}, a_monday(10))
        self.assertIsNone(is_open)
        self.assertIn("no usable hours", why)

    def test_today_hours_reports_closed(self):
        sunday = a_monday() + timedelta(days=6)
        self.assertEqual(today_hours(self.HOURS, sunday), "closed")


class TestKnowledgeBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = CampusGraph.load(graph_path())
        cls.kb = KnowledgeBase.from_raw(RAW_DIR, cls.graph)

    def test_builds_documents_from_both_sources(self):
        coverage = self.kb.coverage()
        self.assertGreater(coverage["documents"], 800)
        self.assertEqual(set(coverage["by_source"]), {"campus-map", "greenmap"})

    def test_retrieves_a_named_place(self):
        hits = self.kb.search("7-Eleven")
        self.assertTrue(hits)
        self.assertEqual(hits[0][1].title, "7-Eleven")

    def test_retrieves_a_facility_by_category(self):
        hits = self.kb.search("rechargeable battery collection")
        self.assertTrue(hits)
        self.assertIn("attery", hits[0][1].title)

    def test_nonsense_retrieves_nothing(self):
        """An empty result is the signal to abstain -- so it must really be empty."""
        self.assertEqual(self.kb.search("zzzzqqqq unparseable"), [])

    def test_hours_are_joined_to_routable_nodes(self):
        with_hours = [n for n in self.graph.nodes.values() if n.ref][:50]
        linked = [n for n in with_hours if self.kb.for_node(n.id)]
        self.assertTrue(linked, "no graph node could be joined to a knowledge document")

    def test_only_some_places_publish_hours(self):
        coverage = self.kb.coverage()
        self.assertLess(coverage["with_opening_hours"], coverage["documents"])


class TestDialogueUsesTheKnowledgeBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = CampusGraph.load(graph_path())

    def setUp(self):
        self.dialogue = Dialogue(self.graph)

    def test_hours_question_answers_from_the_table(self):
        reply = self.dialogue.handle("what are the opening hours of the dental clinic")
        self.assertIn("09:00-13:00", reply.text)
        self.assertIn("PolyU campus map", reply.text)
        self.assertIsNone(reply.route)
        self.assertTrue(reply.facilities)

    def test_unknown_item_is_refused_not_guessed(self):
        reply = self.dialogue.handle("where do I recycle my old sofa")
        self.assertIn("do not have enough information", reply.text.lower())
        self.assertNotIn("here is", reply.text.lower())

    def test_place_without_hours_is_not_invented(self):
        reply = self.dialogue.handle("what time does the bookstore close")
        self.assertIn("does not publish opening hours", reply.text)

    def test_facility_listing_carries_structured_cards(self):
        reply = self.dialogue.handle("where is the nearest AED")
        self.assertTrue(reply.facilities)
        card = reply.facilities[0]
        for key in ("node_id", "name", "lat", "lng", "metres"):
            self.assertIn(key, card)

    def test_every_card_points_at_a_real_node(self):
        reply = self.dialogue.handle("where can I find a water station")
        for card in reply.facilities:
            self.assertIn(card["node_id"], self.graph.nodes)


if __name__ == "__main__":
    unittest.main(verbosity=2)

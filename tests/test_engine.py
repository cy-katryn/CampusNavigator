"""The engine, driven the way the agent's tools drive it.

These replaced `test_dialogue.py` when the grammar fallback was removed. They call the same
four entry points the tools call -- `_route_reply`, `_facilities`, `_hours`, `_close` -- with
explicit `Slots`, so they need no model, no network and no parser. They are also the
deterministic net: every figure asserted here is measured by the engine, so a change in
routing, annotation, timetables or closure handling fails here instead of in a demo.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from campus_nav.config import Settings, graph_path
from campus_nav.dialogue import Dialogue, walk_minutes
from campus_nav.format import DAYS, requested_day
from campus_nav.graph import CampusGraph
from campus_nav.kb import HKT
from campus_nav.nlu import Slots
from campus_nav.place_index import PlaceIndex

OFFLINE = Settings(api_key="", model="test", llm_enabled=False, timeout_s=1)


class EngineCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.graph = CampusGraph.load(graph_path())
        cls.places = PlaceIndex(cls.graph, OFFLINE)

    def talk(self) -> Dialogue:
        return Dialogue(self.graph, client=None, places=self.places)

    def endpoint(self, phrase: str) -> str:
        matches = self.places.resolve(phrase)
        self.assertEqual(len(matches), 1, f"{phrase!r} resolved to {[n.name for n in matches]}")
        return matches[0].id

    def route(self, origin: str, destination: str, vias: list[str] | None = None):
        talk = self.talk()
        slots = Slots(intent="route", origin_text=self.endpoint(origin),
                      destination_text=self.endpoint(destination),
                      vias=[self.endpoint(v) for v in (vias or [])])
        return talk, talk._route_reply(slots)

    def facilities(self, dial, **kw):
        slots = Slots(intent="facility_query", **kw)
        return dial._facilities(slots)

    def hours(self, dial, subject: str, when: str = ""):
        return dial._hours(Slots(intent="hours_query", subject=subject, when=when))


class EngineTests(EngineCase):
    def test_walk_time_is_distance_over_an_easy_pace(self) -> None:
        self.assertEqual(walk_minutes(75.0), 1)
        self.assertEqual(walk_minutes(206.0), 3)
        self.assertEqual(walk_minutes(750.0), 10)
        self.assertEqual(walk_minutes(0.0), 1, "a walk is never zero minutes")

    def test_a_route_receipt_states_metres_minutes_and_alternatives(self) -> None:
        _talk, reply = self.route("Block V", "library")
        self.assertAlmostEqual(reply.route.total_metres, 206, delta=2)
        self.assertIn("easy pace", reply.text)
        self.assertEqual(len(reply.routes), 3, "three ways there")

    def test_a_stop_extends_the_walk_and_is_remembered(self) -> None:
        _direct_talk, direct = self.route("Block V", "library")
        talk, via = self.route("Block V", "library", vias=["Core F"])
        self.assertGreater(via.route.total_metres, direct.route.total_metres)
        self.assertTrue(talk.state.stops, "the stop was not remembered")

    def test_the_journey_line_reports_the_endpoints_as_facts(self) -> None:
        talk, _reply = self.route("Block V", "library")
        digest = talk._context_digest()
        self.assertIsNotNone(digest)
        self.assertIn("Block L (Pao Yue-Kong Library)", digest["content"])

    def test_an_ambiguous_closure_names_the_candidates_and_changes_nothing(self) -> None:
        talk = self.talk()
        note = talk._close("the canteen is closed")
        self.assertIn("several", note)
        self.assertEqual(len(talk.state.closed_nodes), 0)

    def test_a_link_closure_closes_the_stretch(self) -> None:
        talk = self.talk()
        note = talk._close("the link between Core C and Core D is closed")
        self.assertTrue(talk.state.closed_edges, note)
        self.assertIn("stretch", note)

    def test_a_named_closure_can_be_re_opened(self) -> None:
        talk = self.talk()
        self.assertIn("Closed", talk._close("the bookshop is closed"))
        self.assertEqual(len(talk.state.closed_nodes), 1)
        self.assertIn("Re-opened", talk._close("re-open it"))
        self.assertEqual(len(talk.state.closed_nodes), 0)

    def test_a_facility_answer_counts_carries_distance_and_hours(self) -> None:
        reply = self.facilities(self.talk(), subject="toilet", item="toilet")
        self.assertEqual(reply.facility_result.total, 25, "25 toilets on the map")
        nearest = reply.facilities[0]
        self.assertIsNotNone(nearest["metres"])
        self.assertTrue(reply.spoken)

    def test_a_named_place_is_answered_with_that_place_not_a_search(self) -> None:
        reply = self.facilities(self.talk(), subject="library")
        self.assertEqual([card["name"] for card in reply.facilities],
                         ["Block L (Pao Yue-Kong Library)"])

    def test_hours_are_quoted_or_declined_never_guessed(self) -> None:
        published = self.hours(self.talk(), "LibCafé")
        self.assertRegex(published.text, r"\d\d:\d\d", "a published table was not quoted")
        missing = self.hours(self.talk(), "Pao Yue-Kong Library")
        self.assertIn("does not publish opening hours", missing.text)

    def test_a_named_day_is_read_in_both_scripts(self) -> None:
        self.assertEqual(requested_day("on Tuesday"), "Tue")
        self.assertEqual(requested_day("sunday"), "Sun")
        self.assertEqual(requested_day("公眾假期"), "PublicHoliday")
        tomorrow = DAYS[(datetime.now(HKT) + timedelta(days=1)).weekday()]
        self.assertEqual(requested_day("聽日"), tomorrow)


class NoModelTests(EngineCase):
    """There is no parser without a model, so the answer says so instead of guessing."""

    def test_a_message_with_no_model_gets_an_honest_reply(self) -> None:
        for lang, marker in (("en-US", "language model"), ("zh-HK", "語言模型"),
                            ("zh-CN", "语言模型")):
            reply = self.talk().handle("how do I get to the library?", language=lang)
            self.assertIn(marker, reply.spoken, lang)
            self.assertIn("no model configured", reply.notes)
            self.assertIsNone(reply.route)

    def test_the_exchange_is_still_remembered_and_can_be_reset(self) -> None:
        talk = self.talk()
        talk.handle("hello", language="en-US")
        self.assertEqual(len(talk.history), 2)
        talk.reset()
        self.assertEqual(talk.history, [])
        self.assertIsNone(talk.state.origin)


if __name__ == "__main__":
    unittest.main()


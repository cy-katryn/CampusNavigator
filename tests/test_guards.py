"""The three checks a sentence must pass before it is spoken.

The narration *fallback* -- a second model call that phrased a Facts object -- was removed:
the assistant writes its own sentence inside the tool loop, so a second call would ask the
same model about its own answer. What survives is the part that mattered, and it now runs on
the sentence about to be spoken, against the payloads the tools actually returned.
"""
from __future__ import annotations

import unittest

from campus_nav.narrate import (claims_unknown_location, grounded_numbers, in_language,
                                problems, supplied_numbers)

ROUTE = {"ok": True, "distance_m": 206, "walk_minutes": 3, "flights_of_stairs": 1,
         "src": "Block V (Jockey Club Innovation Tower)",
         "dest": "Block L (Pao Yue-Kong Library)", "via": [], "alternative_routes": 2}
PLACES = {"ok": True, "count": 24,
          "places": [{"id": "POI_1", "name": "LibCafé", "metres": 120, "floor": "3/F",
                      "hours_today": "08:00 - 23:00"}]}


class NumberTests(unittest.TestCase):
    def test_a_number_the_tools_returned_is_allowed(self) -> None:
        allowed = supplied_numbers([ROUTE])
        self.assertEqual(grounded_numbers("It is 206 m, about 3 minutes.", allowed), [])

    def test_a_number_nobody_returned_is_reported(self) -> None:
        allowed = supplied_numbers([ROUTE])
        self.assertEqual(grounded_numbers("It is about 400 m.", allowed), ["400"])

    def test_a_digit_inside_a_name_belongs_to_the_name(self) -> None:
        payload = dict(ROUTE, dest="7-Eleven")
        allowed = supplied_numbers([payload])
        self.assertEqual(grounded_numbers("The 7-Eleven is 206 m away.", allowed), [])

    def test_every_number_in_every_payload_is_allowed(self) -> None:
        allowed = supplied_numbers([ROUTE, PLACES])
        for token in ("206", "3", "1", "2", "24", "120", "08", "23"):
            self.assertIn(float(token), allowed, token)

    def test_opening_hours_inside_a_facility_payload_are_allowed(self) -> None:
        allowed = supplied_numbers([PLACES])
        self.assertEqual(grounded_numbers("LibCafé is open 08:00-23:00, 120 m away.", allowed), [])


class LanguageTests(unittest.TestCase):
    def test_a_sentence_in_the_other_script_is_refused(self) -> None:
        self.assertFalse(in_language("It is about 206 metres.", "zh-HK"))
        self.assertFalse(in_language("大約 206 米。", "en-US"))

    def test_a_sentence_in_the_asked_language_passes(self) -> None:
        self.assertTrue(in_language("大約 206 米。", "zh-HK"))
        self.assertTrue(in_language("It is about 206 metres.", "en-US"))


class LocationTests(unittest.TestCase):
    def test_a_claim_about_where_the_user_is_is_detected(self) -> None:
        for text in ("The water station is right where you are.",
                     "离你最近的是 Core P 的饮水机。",
                     "距離你 0 米，就喺你附近。"):
            self.assertTrue(claims_unknown_location(text), text)

    def test_an_ordinary_sentence_is_not_a_claim(self) -> None:
        for text in ("From Block V to Block L is 206 m.",
                     "LibCafé is 120 m from Core P."):
            self.assertFalse(claims_unknown_location(text), text)


class ProblemTests(unittest.TestCase):
    """One entry point: what the agent calls before it speaks."""

    def test_a_good_sentence_has_no_problems(self) -> None:
        self.assertEqual(problems("It is 206 m, about 3 minutes.",
                                  [ROUTE], "en-US", location_known=False), [])

    def test_each_violation_is_named(self) -> None:
        self.assertIn("not written in zh-HK",
                      problems("It is 206 m.", [ROUTE], "zh-HK", location_known=False)[0])
        self.assertTrue(any("ungrounded" in r for r in
                            problems("It is about 400 m.", [ROUTE], "en-US", location_known=False)))
        self.assertTrue(any("location" in r for r in
                            problems("The station is right where you are.", [ROUTE],
                                     "en-US", location_known=False)))

    def test_a_known_location_may_be_mentioned(self) -> None:
        self.assertEqual(problems("The nearest one is 120 m from you.", [PLACES],
                                  "en-US", location_known=True), [])

    def test_an_empty_sentence_is_a_problem(self) -> None:
        self.assertTrue(problems("   ", [ROUTE], "en-US", location_known=False))


if __name__ == "__main__":
    unittest.main()


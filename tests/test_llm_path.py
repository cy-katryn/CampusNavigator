"""The model layer: which language it is asked for, and what it may claim.

The narration guards live here. The slot-parsing tests that used to sit in this file went
with the code they tested: `nlu.parse_with_llm` and `_validated` were unreachable once the
agent front-end landed (`dialogue.handle` always calls the grammar with client=None), so
they are gone. The properties they protected -- "the model may not create a place", "an
endpoint is not inherited without a deictic word" -- are now enforced by the tools:
`search()` only returns real ids and `path()` refuses the rest. See `tests/test_agent.py`.
"""
from __future__ import annotations

import types
import unittest

from campus_nav.narrate import (SYSTEM_PROMPTS, Facts, Narrator, grounded_numbers,
                                in_language)


class _ChatClient:
    """A model that says one canned sentence, and remembers what it was asked."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.settings = types.SimpleNamespace(llm_available=True)
        self.seen: list[list[dict]] = []

    def chat(self, messages, **_kwargs):
        self.seen.append(messages)
        return types.SimpleNamespace(ok=True, text=self.text, model="stub",
                                     latency_ms=1, error="")


class NarrationLanguageTests(unittest.TestCase):
    """The model is told which language to answer in -- and is not trusted about it."""

    def facts(self, language: str) -> Facts:
        return Facts(kind="route", origin="Block V (Jockey Club Innovation Tower)",
                     destination="Block L (Pao Yue-Kong Library)", reachable=True,
                     total_metres=206.0, walk_minutes=3, language=language)

    def test_the_language_is_part_of_what_the_model_is_given(self) -> None:
        self.assertEqual(self.facts("zh-HK").as_prompt_json()["language"], "zh-HK")

    def test_the_system_prompt_follows_the_language(self) -> None:
        client = _ChatClient("由V座行去L座，大約 206 米，行樓梯。")
        narrator = Narrator(client)
        narration = narrator.narrate(self.facts("zh-HK"))
        self.assertTrue(narration.ok, narration.reason)
        system = client.seen[0][0]["content"]
        self.assertEqual(system, SYSTEM_PROMPTS["zh-HK"])
        self.assertIn("廣東話", system)

    def test_an_english_sentence_is_refused_for_a_chinese_question(self) -> None:
        # The reason the Chinese path used to ignore the model entirely. A wrong-script
        # sentence now costs the phrasing, not the answer: the catalogue still speaks.
        client = _ChatClient("It is about 206 metres from Block V to Block L.")
        narration = Narrator(client).narrate(self.facts("zh-HK"))
        self.assertFalse(narration.ok)
        self.assertIn("not written in zh-HK", narration.reason)

    def test_a_chinese_sentence_is_refused_for_an_english_question(self) -> None:
        client = _ChatClient("由V座行去L座，大約 206 米。")
        self.assertFalse(Narrator(client).narrate(self.facts("en-US")).ok)

    def test_the_numbers_guard_still_applies_in_chinese(self) -> None:
        client = _ChatClient("由V座行去L座，大約 400 米。")
        narration = Narrator(client).narrate(self.facts("zh-HK"))
        self.assertFalse(narration.ok)
        self.assertEqual(narration.invented, ["400"])

    def test_a_claim_about_the_users_location_is_refused(self) -> None:
        # Observed live: asked for the nearest water station, the model wrote "离你最近的是…"
        # although nobody had said where the user was. The engine had measured from Core P.
        from campus_nav.narrate import claims_unknown_location
        cases = [
            ("离你最近的是 Core P 的饮水机。", "zh-CN"),
            ("距離你 0 米，就喺你附近。", "zh-HK"),
            ("The water station is right where you are.", "en-US"),
        ]
        for text, language in cases:
            facts = Facts(kind="places", origin="", destination="", reachable=True,
                          need="water", match_count=17, reference="Core P",
                          user_location_known=False,
                          best={"name": "Water Station (Core P)", "metres": 0},
                          language=language)
            self.assertTrue(claims_unknown_location(text), text)
            narration = Narrator(_ChatClient(text)).narrate(facts)
            self.assertFalse(narration.ok, text)
            self.assertIn("location", narration.reason)

    def test_the_same_sentence_is_fine_when_the_user_said_where_they_are(self) -> None:
        facts = Facts(kind="places", origin="", destination="", reachable=True,
                      need="water", match_count=17, reference="Core P",
                      user_location_known=True,
                      best={"name": "Water Station (Core P)", "metres": 0},
                      language="en-US")
        narration = Narrator(_ChatClient("The nearest water station is right where you are.")).narrate(facts)
        self.assertTrue(narration.ok, narration.reason)

    def test_the_guard_reads_the_script_only(self) -> None:
        self.assertTrue(in_language("大約 206 米。", "zh-HK"))
        self.assertFalse(in_language("about 206 metres", "zh-HK"))
        self.assertTrue(in_language("about 206 metres", "en-US"))


class GroundedNumberTests(unittest.TestCase):
    def facts(self) -> Facts:
        return Facts(kind="route", origin="Block V", destination="7-Eleven",
                     reachable=True, total_metres=85.0, walk_minutes=1)

    def test_a_digit_inside_a_place_name_is_not_an_invented_number(self) -> None:
        text = "It is 85 metres to the 7-Eleven, about a minute away."
        self.assertEqual(grounded_numbers(text, self.facts().grounded_numbers()), [])

    def test_a_number_the_engine_did_not_compute_is_reported(self) -> None:
        text = "It is about 400 metres to the 7-Eleven."
        self.assertEqual(grounded_numbers(text, self.facts().grounded_numbers()), ["400"])


if __name__ == "__main__":
    unittest.main()

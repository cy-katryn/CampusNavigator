"""Tests for Level B narration.

The model's wording is never trusted blindly: `grounded_numbers()` rejects any reply
that mentions a figure the route calculation did not produce. These tests use a stub
client, so they prove the guard works without needing an API key or the network.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["LLM_ENABLED"] = "0"


from campus_nav.config import graph_path  # noqa: E402
from campus_nav.dialogue import Dialogue  # noqa: E402
from campus_nav.graph import CampusGraph  # noqa: E402
from campus_nav.llm import LLMResult  # noqa: E402
from campus_nav.narrate import Facts, Narrator, grounded_numbers  # noqa: E402

GRAPH_PATH = graph_path()


class StubSettings:
    llm_available = True
    model = "stub-model"


class StubClient:
    """Pretends to be DeepSeekClient: returns whatever text we hand it."""

    def __init__(self, reply: str, ok: bool = True) -> None:
        self.settings = StubSettings()
        self._reply = reply
        self._ok = ok
        self.calls = 0

    def chat(self, messages, **kwargs) -> LLMResult:
        self.calls += 1
        return LLMResult(ok=self._ok, text=self._reply if self._ok else "",
                         error="" if self._ok else "HTTP 429: rate limited",
                         model="stub-model", latency_ms=12, attempts=1)


def route_facts() -> Facts:
    return Facts(
        kind="route",
        origin="Main Entrance",
        destination="Library",
        reachable=True,
        total_metres=419.4,
        segment_lines=["Main Entrance to Core B: 64 m", "Core B to Library: 355 m"],
        segment_metres=[64.0, 355.0],
        previous_metres=501.3,
    )


class TestGroundedNumbers(unittest.TestCase):
    def test_accepts_only_supplied_numbers(self):
        allowed = route_facts().grounded_numbers()
        good = "It is about 419 m from the main entrance, 2 legs in all."
        self.assertEqual(grounded_numbers(good, allowed), [])

    def test_flags_an_invented_distance(self):
        allowed = route_facts().grounded_numbers()
        bad = "It is about 350 m from the main entrance."
        self.assertEqual(grounded_numbers(bad, allowed), ["350"])

    def test_flags_an_invented_leg_count(self):
        allowed = route_facts().grounded_numbers()
        bad = "It takes 12 separate legs to get there."
        self.assertEqual(grounded_numbers(bad, allowed), ["12"])

    def test_relative_difference_is_allowed_but_a_wrong_one_is_not(self):
        allowed = route_facts().grounded_numbers()
        # 501.3 - 419.4 = 81.9, so 82 is a legitimate rounding of the real difference
        self.assertEqual(grounded_numbers("That is 82 m shorter.", allowed), [])
        self.assertEqual(grounded_numbers("That is 120 m shorter.", allowed), ["120"])

    def test_number_within_rounding_tolerance_passes(self):
        allowed = route_facts().grounded_numbers()
        self.assertEqual(grounded_numbers("Roughly 419 metres.", allowed), [])
        self.assertEqual(grounded_numbers("Roughly 419.4 metres.", allowed), [])


class TestNarrator(unittest.TestCase):
    def test_unavailable_without_a_client(self):
        narrator = Narrator(None)
        self.assertFalse(narrator.available)
        result = narrator.narrate(route_facts())
        self.assertFalse(result.ok)
        self.assertIn("no LLM configured", result.reason)

    def test_accepts_a_grounded_reply(self):
        narrator = Narrator(StubClient("It is 419 m to the library, and 2 legs."))
        result = narrator.narrate(route_facts())
        self.assertTrue(result.ok, result.reason)
        self.assertIn("419", result.text)

    def test_rejects_a_reply_with_an_invented_number(self):
        narrator = Narrator(StubClient("It is about 350 m to the library."))
        result = narrator.narrate(route_facts())
        self.assertFalse(result.ok)
        self.assertIn("ungrounded", result.reason)
        self.assertEqual(result.invented, ["350"])

    def test_rejects_a_failed_api_call(self):
        narrator = Narrator(StubClient("", ok=False))
        result = narrator.narrate(route_facts())
        self.assertFalse(result.ok)
        self.assertIn("429", result.reason)

    def test_rejects_an_empty_or_huge_reply(self):
        self.assertFalse(Narrator(StubClient("ok")).narrate(route_facts()).ok)
        self.assertFalse(Narrator(StubClient("419 " * 300)).narrate(route_facts()).ok)


class TestDialogueUsesNarration(unittest.TestCase):
    def setUp(self) -> None:
        self.graph = CampusGraph.load(GRAPH_PATH)

    def test_without_a_narrator_the_template_is_used(self):
        dialogue = Dialogue(self.graph, client=None, narrator=None)
        reply = dialogue.handle("How do I get from the entrance to the library?")
        self.assertFalse(reply.narrated)
        self.assertIn("Total:", reply.text)

    def test_good_narration_replaces_the_prose_but_keeps_the_numbers(self):
        graph = self.graph
        plain = Dialogue(graph, client=None).handle("How do I get from the entrance to the library?")
        metres = round(plain.route.total_metres)

        dialogue = Dialogue(graph, client=None,
                            narrator=Narrator(StubClient(f"That is {metres} m to the library.")))
        reply = dialogue.handle("How do I get from the entrance to the library?")
        self.assertTrue(reply.narrated, reply.notes)
        self.assertIn(f"{metres} m to the library", reply.text)
        # the computed detail is still present, so nothing is hidden from the user
        self.assertIn("Total:", reply.text)

    def test_bad_narration_falls_back_to_the_template(self):
        dialogue = Dialogue(self.graph, client=None,
                            narrator=Narrator(StubClient("It is roughly 900 m away.")))
        reply = dialogue.handle("How do I get from the entrance to the library?")
        self.assertFalse(reply.narrated)
        self.assertTrue(any("Template reply used" in n for n in reply.notes), reply.notes)
        self.assertIn("Total:", reply.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)

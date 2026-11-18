"""The tool-calling front-end: it may only speak about what the tools returned.

These replace the slot-parsing tests that went with `nlu.parse_with_llm`. The properties are
the same ones, enforced in the new place: the model cannot create a place (search returns
real ids), it cannot route to one (path refuses ids the graph does not have), it can stop and
ask, and it cannot loop forever.
"""
from __future__ import annotations

import json
import types
import unittest

from campus_nav.agent import MAX_STEPS
from campus_nav.config import Settings, graph_path
from campus_nav.dialogue import Dialogue
from campus_nav.graph import CampusGraph
from campus_nav.place_index import PlaceIndex

OFFLINE = Settings(api_key="", model="test", llm_enabled=False, timeout_s=1)


def _call(tool: str, **arguments) -> dict:
    return {"id": f"call_{tool}", "type": "function",
            "function": {"name": tool, "arguments": json.dumps(arguments)}}


class ScriptedClient:
    """A model that emits a fixed list of tool calls, then one sentence."""

    def __init__(self, batches: list[list[dict]], text: str = "Here is your answer.") -> None:
        self.batches = list(batches)
        self.text = text
        self.settings = types.SimpleNamespace(llm_available=True)
        self.turns = 0
        self.seen: list[list[dict]] = []

    def chat(self, messages, **_kwargs):
        self.turns += 1
        self.seen.append([dict(m) for m in messages])
        batch = self.batches.pop(0) if self.batches else []
        return types.SimpleNamespace(ok=True, text="" if batch else self.text,
                                     tool_calls=batch, model="stub", latency_ms=1,
                                     error="")


class AgentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.graph = CampusGraph.load(graph_path())
        cls.places = PlaceIndex(cls.graph, OFFLINE)

    def talk(self, client: ScriptedClient) -> Dialogue:
        return Dialogue(self.graph, client=client, narrator=None, places=self.places)

    def tool_payloads(self, client: ScriptedClient) -> list[dict]:
        out = []
        for messages in client.seen:
            for message in messages:
                if message.get("role") == "tool":
                    out.append(json.loads(message["content"]))
        return out

    def test_search_returns_only_ids_that_exist(self) -> None:
        client = ScriptedClient([[_call("search", name="library")]])
        self.talk(client).handle("where is the library?", language="en-US")
        matches = self.tool_payloads(client)[0]["matches"]
        self.assertTrue(matches, "search returned nothing")
        for match in matches:
            self.assertIn(match["id"], self.graph.nodes)

    def test_path_refuses_an_id_the_graph_does_not_have(self) -> None:
        client = ScriptedClient([[_call("path", src="Block V", dest="POI_nope")]])
        reply = self.talk(client).handle("route me", language="en-US")
        payload = self.tool_payloads(client)[0]
        self.assertFalse(payload["ok"])
        self.assertIn("unknown place id", payload["error"])
        self.assertIsNone(reply.route, "a route was drawn from an id search never returned")

    def test_ask_ends_the_turn_with_one_question(self) -> None:
        client = ScriptedClient([[_call("ask", question="Which library?")]])
        reply = self.talk(client).handle("the library", language="en-US")
        self.assertEqual(reply.text, "Which library?")
        self.assertEqual(getattr(reply.slots, "intent", ""), "ask")
        self.assertEqual(client.turns, 1, "the model was asked again after asking the user")

    def test_a_model_that_loops_is_stopped(self) -> None:
        loop = [[_call("search", name="library")] for _ in range(MAX_STEPS + 5)]
        client = ScriptedClient(loop)
        reply = self.talk(client).handle("library library library", language="en-US")
        self.assertLessEqual(client.turns, MAX_STEPS)
        self.assertIsNotNone(reply)


if __name__ == "__main__":
    unittest.main()


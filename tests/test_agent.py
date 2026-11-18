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
        return Dialogue(self.graph, client=client, places=self.places)


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

    def test_an_invented_number_is_replaced_by_the_engines_own_sentence(self) -> None:
        # The assistant writes the prose, so the guard runs on its sentence: the route it was
        # given is 206 m, and "about 400 metres" may not reach the user's ears.
        library = self.places.resolve("Pao Yue-Kong Library")[0].id
        core_p = self.places.resolve("Core P")[0].id
        client = ScriptedClient([[_call("path", src=core_p, dest=library)]],
                               text="It is about 400 metres, roughly 8 minutes.")
        reply = self.talk(client).handle("how far is it?", language="en-US")
        self.assertNotIn("400", reply.spoken)
        self.assertTrue(any("sentence refused" in note for note in reply.notes), reply.notes)
        self.assertIn("128", reply.spoken, "the engine's own distance was not used")

    def test_a_sentence_in_the_wrong_language_is_replaced(self) -> None:
        # With engine text to fall back on, the sentence is replaced by the engine's own.
        client = ScriptedClient([[_call("facilities", item="toilet")]],
                               text="The nearest toilet is in Core P.")
        reply = self.talk(client).handle("邊度有洗手間？", language="zh-HK")
        self.assertNotIn("The nearest toilet", reply.spoken)
        self.assertTrue(any("sentence refused" in note for note in reply.notes), reply.notes)

    def test_a_refused_sentence_with_nothing_to_fall_back_on_is_flagged_not_trusted(self) -> None:
        # A search-only answer has no engine sentence to substitute, so the model's words are
        # kept but not trusted: the reply is not marked narrated and the note says why.
        client = ScriptedClient([[_call("search", name="圖書館")]],
                               text="The library is Block L.")
        reply = self.talk(client).handle("圖書館喺邊？", language="zh-HK")
        self.assertFalse(getattr(reply, "narrated", False))
        self.assertTrue(any("sentence refused" in note for note in reply.notes), reply.notes)


if __name__ == "__main__":

    unittest.main()



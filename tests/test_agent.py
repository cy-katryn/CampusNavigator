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
        self.assertTrue(any("sentence refused" in note for note in reply.review), reply.review)
        self.assertEqual(reply.notes, [], "a refusal is a review note, not a visible tag")
        self.assertIn("128", reply.spoken, "the engine's own distance was not used")

    def test_a_sentence_in_the_wrong_language_is_replaced(self) -> None:
        # With engine text to fall back on, the sentence is replaced by the engine's own.
        client = ScriptedClient([[_call("facilities", item="toilet")]],
                               text="The nearest toilet is in Core P.")
        reply = self.talk(client).handle("邊度有洗手間？", language="zh-HK")
        self.assertNotIn("The nearest toilet", reply.spoken)
        self.assertTrue(any("sentence refused" in note for note in reply.review), reply.review)

    def test_step_free_wording_reaches_the_router(self) -> None:
        # The tool's avoid_stairs argument used to write only the slots, and the router reads
        # state.avoid_stairs -- so "step-free please" routed through the steps and the model
        # then claimed no step-free route existed. The user's own words now set the state.
        library = self.places.resolve("Pao Yue-Kong Library")[0].id
        block_x = self.places.resolve("Block X")[0].id
        client = ScriptedClient([[_call("path", src=block_x, dest=library, avoid_stairs=False)]],
                                text="Here is the route.")
        talk = self.talk(client)
        talk.handle("from Block X to the library, step-free please", language="en-US")
        self.assertTrue(talk.state.avoid_stairs, "the user's words did not reach the router")
        route = talk.state.last_route
        self.assertIsNotNone(route)
        self.assertEqual(sum(1 for segment in route.segments if segment.stairs), 0,
                         "a step-free request still routed through steps")

    def test_a_repeated_question_may_reuse_the_numbers_it_was_already_given(self) -> None:
        # Observed in a demo: the same question asked twice (zh-CN, then zh-HK) was answered
        # the second time from the conversation above with no tool call, and a *correct*
        # sentence was refused as "ungrounded number(s): 419, 6, 499, 492". Those numbers had
        # come out of the engine one turn earlier, so they are recall, not invention.
        library = self.places.resolve("Pao Yue-Kong Library")[0].id
        block_z = self.places.resolve("Block Z")[0].id
        first = self.talk(ScriptedClient([[_call("path", src=library, dest=block_z)]],
                                        text="从图书馆去 Block Z 大约 419 米，步行约 6 分钟。"))
        started = first.handle("圖書館去Block Z點行", language="zh-CN")
        self.assertEqual(started.review, [], started.review)
        # Second turn: no tool call at all, same numbers.
        talk = self.talk(ScriptedClient([], text="由圖書館去 Block Z，大約 419 米，行約 6 分鐘。"))
        talk.history = list(first.history)
        talk.state = first.state
        reply = talk.handle("圖書館去Block Z點行", language="zh-HK")
        self.assertEqual(reply.review, [], reply.review)
        self.assertTrue(reply.narrated, "a recalled-but-correct sentence should be used")
        self.assertIn("419", reply.spoken)

    def test_a_refused_sentence_with_nothing_to_fall_back_on_is_flagged_not_trusted(self) -> None:
        client = ScriptedClient([[_call("search", name="圖書館")]],
                               text="The library is Block L.")
        reply = self.talk(client).handle("圖書館喺邊？", language="zh-HK")
        self.assertFalse(getattr(reply, "narrated", False))
        self.assertTrue(any("sentence refused" in note for note in reply.review), reply.review)
        self.assertEqual(reply.notes, [], "a refusal is not a visible tag")


if __name__ == "__main__":

    unittest.main()



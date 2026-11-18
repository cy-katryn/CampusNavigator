"""The LLM path: what the model returns, and what the guards do with it.

This is the only layer that no other test touches, and both bugs that reached the demo
lived here -- a place silently nulled because it was missing from the prompt's catalogue,
and a correct sentence thrown away because "7-Eleven" contains a digit. The client is
stubbed, so these run offline and pin the contract rather than the provider.
"""
from __future__ import annotations

import types
import unittest

from campus_nav.config import Settings, graph_path
from campus_nav.graph import CampusGraph
from campus_nav.narrate import Facts, grounded_numbers
from campus_nav.nlu import SYSTEM_PROMPT, parse, parse_with_llm
from campus_nav.place_index import PlaceIndex

# No vector keys: the index answers with the built-in scorer, so nothing hits the network.
OFFLINE = Settings(api_key="", model="test", llm_enabled=False, timeout_s=1)


class _Result:
    def __init__(self, payload) -> None:
        self._payload = payload

    def json(self):
        return self._payload


class StubClient:
    """A model that returns exactly the JSON a test hands it."""

    def __init__(self, payload) -> None:
        self.payload = payload
        self.settings = types.SimpleNamespace(llm_available=True)
        self.seen: list[list[dict]] = []

    def chat(self, messages, **_kwargs):
        self.seen.append(messages)
        return _Result(self.payload)


class LlmPathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.graph = CampusGraph.load(graph_path())
        cls.places = PlaceIndex(cls.graph, OFFLINE)

    def ids_by_name(self, name: str) -> str:
        for node in self.graph.nodes.values():
            if node.name == name:
                return node.id
        raise AssertionError(f"no node named {name!r}")

    def test_the_prompt_carries_no_map_vocabulary_at_all(self) -> None:
        # The place catalogue went first: 98 lines and ~1,400 tokens a turn, stale by
        # construction, and the reason a named place came back as null. The amenity list went
        # next: handed a list of services and told to choose one, the model turned "a swim"
        # into "water station". Both are vocabularies only the engine can see.
        self.assertNotIn("{places}", SYSTEM_PROMPT)
        self.assertNotIn("{facilities}", SYSTEM_PROMPT)

    def test_the_model_reports_words_and_the_engine_names_the_amenity(self) -> None:
        # A label the model volunteers is ignored outright -- choosing from a vocabulary it
        # cannot see is the one thing it cannot do.
        client = StubClient({"intent": "facility_query", "need": "a swim",
                             "item": "water station"})
        slots = parse("I want to have a swim", self.graph, client, self.places)
        self.assertEqual(slots.source, "llm")
        self.assertEqual(slots.need, "a swim")
        self.assertEqual(slots.item, "")

    def test_a_well_formed_answer_is_used_as_parsed(self) -> None:
        client = StubClient({"intent": "route", "origin": "Block V",
                             "destination": "7-Eleven"})
        slots = parse("from Block V to the 7-Eleven", self.graph, client, self.places)
        self.assertEqual(slots.source, "llm")
        self.assertEqual(slots.destination_text, "7-Eleven")
        # the model is sent this message and nothing else: no transcript, no catalogue
        self.assertEqual(len(client.seen[0]), 2)

    def test_a_node_id_comes_back_as_the_place_name(self) -> None:
        # The router and the resolver work on names; an id is not a name, and embedding
        # one scored "Core B" for the library.
        node_id = self.ids_by_name("Block V (Jockey Club Innovation Tower)")
        client = StubClient({"intent": "route", "destination": node_id})
        slots = parse_with_llm("take me there", self.graph, client)
        self.assertEqual(slots.destination_text, "Block V (Jockey Club Innovation Tower)")

    def test_a_place_the_user_named_is_kept_even_if_the_map_cannot_find_it(self) -> None:
        # Dropping it is how "Core Y" quietly became a question about a different core.
        # Kept, the dialogue can answer "I do not know Core Y" and suggest.
        client = StubClient({"intent": "route", "origin": "Core Y", "destination": "Core A"})
        slots = parse("from Core Y to Core A", self.graph, client, self.places)
        self.assertEqual(slots.source, "llm")
        self.assertEqual(slots.origin_text, "Core Y")

    def test_several_stops_are_kept_in_order(self) -> None:
        client = StubClient({"intent": "route", "origin": "Block Y", "destination": "Core A",
                             "vias": ["Core P", "Block L (Pao Yue-Kong Library)"]})
        slots = parse("from Block Y through Core P and the library to Core A", self.graph,
                      client, self.places)
        self.assertEqual(slots.vias, ["Core P", "Block L (Pao Yue-Kong Library)"])

    def test_a_route_with_only_a_stop_is_a_replan_not_a_new_journey(self) -> None:
        # "make sure I pass through Core P on the way": the endpoints are in the session.
        client = StubClient({"intent": "route", "via": "Core P"})
        slots = parse("make sure I pass through Core P on the way", self.graph, client,
                      self.places)
        self.assertEqual(slots.source, "llm")
        self.assertEqual(slots.vias, ["Core P"])
        self.assertEqual(slots.destination_text, "")

    def test_a_stop_that_names_nothing_is_dropped_not_fatal(self) -> None:
        client = StubClient({"intent": "route", "origin": "Block V",
                             "destination": "7-Eleven", "via": "qqqq nowhere"})
        slots = parse("from Block V via qqqq to the 7-Eleven", self.graph, client,
                      self.places)
        self.assertEqual(slots.source, "llm")
        self.assertEqual(slots.vias, [])

    def test_a_place_the_user_never_said_is_thrown_away(self) -> None:
        # Observed live: "where is the library?" came back as a route from Block V, because
        # "Block V" is the example in the prompt and the library was nowhere in it.
        client = StubClient({"intent": "route", "origin": "Block V",
                             "destination": "Block L (Pao Yue-Kong Library)"})
        slots = parse("where is the library?", self.graph, client, self.places)
        self.assertEqual(slots.origin_text, "")
        self.assertIn("Library", slots.destination_text)

    def test_a_closure_the_user_never_mentioned_does_not_block_the_route(self) -> None:
        # A closure blocks every route through that place, so an invented one is not a
        # detail: it refuses the walk the user asked for.
        client = StubClient({"intent": "route", "origin": "Block V",
                             "destination": "the library",
                             "closed": "the library is closed"})
        slots = parse("how do I get from Block V to the library?", self.graph, client,
                      self.places)
        self.assertEqual(slots.source, "llm")
        self.assertEqual(slots.closed_text, "")

    def test_a_closure_the_user_did_mention_is_kept(self) -> None:
        client = StubClient({"intent": "close", "closed": "the canteen is closed"})
        slots = parse("the canteen is closed today", self.graph, client, self.places)
        self.assertEqual(slots.source, "llm")
        self.assertIn("canteen", slots.closed_text)

    def test_a_misspelt_place_still_reaches_the_model_answer(self) -> None:
        # The tolerance is for spelling: fuzzy input is exactly where a model beats a
        # word list, so the guard must not take that away.
        client = StubClient({"intent": "route",
                             "destination": "Block L (Pao Yue-Kong Library)"})
        slots = parse("where is the libarry?", self.graph, client, self.places)
        self.assertEqual(slots.source, "llm")
        self.assertIn("Library", slots.destination_text)

    def test_the_model_may_not_call_a_campus_question_out_of_scope(self) -> None:
        # "re-open it" is this app's own recovery command and the grammar knows it.
        client = StubClient({"intent": "out_of_scope"})
        slots = parse("re-open it", self.graph, client, self.places)
        self.assertEqual(slots.source, "grammar")
        self.assertEqual(slots.intent, "close")

    def test_a_closure_with_no_place_named_keeps_the_sentence(self) -> None:
        # The model got the intent right and the place wrong; the resolver can still find
        # "canteen" in the sentence, which is what the grammar would have handed over.
        client = StubClient({"intent": "close"})
        slots = parse("the canteen is closed", self.graph, client, self.places)
        self.assertEqual(slots.intent, "close")
        self.assertIn("canteen", slots.closed_text)

    def test_the_fallback_says_why_the_model_was_not_the_one_answering(self) -> None:
        # "understood by grammar" alone cannot be diagnosed: no key, a dead API, the model
        # declining and the model being wrong all look identical in the transcript.
        abstained = parse("hi there", self.graph,
                          StubClient({"intent": "out_of_scope"}), self.places)
        self.assertEqual(abstained.source, "grammar")
        self.assertEqual(abstained.parse_note, "model abstained")

        invented = parse("where is the library?", self.graph,
                         StubClient({"intent": "route", "destination": "qqqq"}),
                         self.places)
        self.assertEqual(invented.parse_note, "model slots rejected")

        from campus_nav.nlu import parse as parse_without_model
        offline = parse_without_model("where is the library?", self.graph, None, self.places)
        self.assertEqual(offline.parse_note, "no model configured")
        self.assertEqual(offline.source, "grammar")

    def test_junk_json_falls_back_to_the_grammar(self) -> None:
        client = StubClient({})
        slots = parse("how do I get from Block V to the library?", self.graph, client,
                      self.places)
        self.assertEqual(slots.source, "grammar")


class ConversationContextTests(unittest.TestCase):
    """The parser sees the last turns; the guard decides what it may do with them."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.graph = CampusGraph.load(graph_path())
        cls.places = PlaceIndex(cls.graph, OFFLINE)

    def node(self, name: str) -> str:
        for candidate in self.graph.nodes.values():
            if candidate.name == name:
                return candidate.id
        raise AssertionError(name)

    def test_the_parser_is_given_the_previous_turns(self) -> None:
        # A stateless parser cannot read "the nearest one": it has nothing to point at.
        client = StubClient({"intent": "route", "origin": "library", "destination": "LibCafé"})
        history = [{"role": "user", "content": "I want to grab lunch, where can I go?"},
                   {"role": "assistant", "content": "The nearest lunch spot is LibCafé."}]
        parse("the nearest one", self.graph, client, self.places, history)
        sent = client.seen[0]
        self.assertEqual(len(sent), 1 + len(history) + 1)
        self.assertIn("LibCafé", sent[-2]["content"])

    def test_a_reference_may_take_its_place_from_the_conversation(self) -> None:
        # "just show me the path to the nearsest from here": the place comes from the
        # previous answer, so evidence has to be allowed to come from the conversation.
        client = StubClient({"intent": "route", "origin": "library", "destination": "LibCafé"})
        history = [{"role": "user", "content": "I want to grab lunch, where can I go?"},
                   {"role": "assistant", "content": "The nearest lunch spot is LibCafé."},
                   {"role": "user", "content": "I am at library"}]
        slots = parse("just show me the path to the nearsest from here", self.graph, client,
                      self.places, history)
        self.assertEqual(slots.source, "llm")
        self.assertEqual(slots.destination_text, "LibCafé")
        self.assertEqual(slots.origin_text, "library")

    def test_a_message_with_no_reference_may_not_inherit_an_old_endpoint(self) -> None:
        # The failure this guard exists for: "where is the library?" answered as a route
        # from Block V because an earlier turn mentioned it. History must not revive it.
        client = StubClient({"intent": "route", "origin": "Block V",
                             "destination": "the library"})
        history = [{"role": "assistant", "content": "You can walk from Block V to Core A."}]
        slots = parse("where is the library?", self.graph, client, self.places, history)
        self.assertEqual(slots.origin_text, "")

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

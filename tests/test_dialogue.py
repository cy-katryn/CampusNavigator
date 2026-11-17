"""The dialogue layer's own decisions: no model, no network, no server.

These are the behaviours the demo leans on and that no other test touches -- the walk time
on a receipt, a closure that names its candidates instead of guessing, and the escape hatch
that re-opens everything.
"""
from __future__ import annotations

import unittest

from campus_nav.config import Settings, graph_path
from campus_nav.dialogue import Dialogue, place_legs, walk_minutes
from campus_nav.graph import CampusGraph
from campus_nav.place_index import PlaceIndex

# No vector keys and no model: the index answers from the graph and the grammar parses.
OFFLINE = Settings(api_key="", model="test", llm_enabled=False, timeout_s=1)


class WalkTimeTests(unittest.TestCase):
    def test_distance_is_divided_by_the_pace(self) -> None:
        # 1.25 m/s is the easy walking pace this project quotes: 75 m a minute.
        self.assertEqual(walk_minutes(75.0), 1)
        self.assertEqual(walk_minutes(206.0), 3)
        self.assertEqual(walk_minutes(750.0), 10)

    def test_a_walk_is_never_zero_minutes(self) -> None:
        self.assertEqual(walk_minutes(0.0), 1)
        self.assertEqual(walk_minutes(4.0), 1)


class _NothingResolves:
    """A resolver that accepts nothing, so the zero-match path runs offline.

    The dense index nearly always returns *some* neighbour, so the "nothing matched"
    branch is hard to reach through the real one — but it is the branch a user hits with a
    typo or a place that is not on this campus, and it must suggest rather than shrug.
    """

    def __init__(self, places: PlaceIndex) -> None:
        self._places = places

    def resolve(self, _text: str) -> list:
        return []

    def coded_siblings(self, _text: str) -> list:
        return []

    def scored(self, text: str):
        return self._places.scored(text)


class DialogueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.graph = CampusGraph.load(graph_path())
        cls.places = PlaceIndex(cls.graph, OFFLINE)

    def dialogue(self) -> Dialogue:
        return Dialogue(self.graph, client=None, narrator=None, places=self.places)

    def test_a_route_receipt_states_the_walk_time(self) -> None:
        reply = self.dialogue().handle("how do I get from Block V to the library?")
        self.assertIn("easy pace", reply.text)

    def test_a_code_that_does_not_exist_suggests_the_same_letter_elsewhere(self) -> None:
        # "Core Y" is not a place; "Block Y" is. The scorer offers Core T, Core C, Core J.
        talk = self.dialogue()
        reply = talk.handle("from Core Y to Core A")
        self.assertIn("I do not know", reply.text)
        self.assertIn("Block Y", reply.text)

    def test_a_greeting_is_answered_not_guessed_at(self) -> None:
        # "Hi" reached the resolver, which found the HTI department and asked which of
        # three places the user meant.
        for word in ("Hi", "hello", "hey!", "thanks"):
            reply = self.dialogue().handle(word)
            self.assertIn("greeting", reply.notes, word)
            self.assertNotIn("could be several places", reply.text, word)

    def test_a_misspelt_place_gets_a_suggestion(self) -> None:
        # Offline the scorer scores an unknown word zero -- by design, so that a phrase
        # matching nothing returns nothing. "Did you mean" then has to come from spelling.
        talk = Dialogue(self.graph, places=_NothingResolves(self.places))
        reply = talk.handle("how do I get to the libarry?")
        self.assertIn("Did you mean", reply.text)
        self.assertIn("Library", reply.text)

    def test_an_ambiguous_closure_names_the_candidates_and_changes_nothing(self) -> None:
        # This campus has three canteens: closing "the canteen" would be a guess, and a
        # wrong closure silently invalidates every later route.
        talk = self.dialogue()
        reply = talk.handle("the canteen is closed")
        self.assertIn("could be several places", reply.text)
        self.assertEqual(len(talk.state.closed_nodes), 0)

    def test_closing_the_link_between_two_places_closes_the_stretch(self) -> None:
        # Core C and Core D are five links and 55 m apart. "The link between them" is that
        # stretch; requiring one drawn link meant the most natural way to describe a
        # closure in this app closed nothing at all.
        talk = self.dialogue()
        reply = talk.handle("the link between Core C and Core D is closed")
        self.assertTrue(talk.state.closed_edges, reply.text)
        self.assertIn("stretch", reply.text)

    def test_a_named_closure_is_closed_and_can_be_re_opened(self) -> None:
        talk = self.dialogue()
        closed = talk.handle("the bookshop is closed")
        self.assertIn("Closed", closed.text)
        self.assertEqual(len(talk.state.closed_nodes), 1)
        reopened = talk.handle("re-open it")
        self.assertIn("Re-opened", reopened.text)
        self.assertEqual(len(talk.state.closed_nodes), 0)


class StopRoutingTests(unittest.TestCase):
    """A walk through stops, with alternatives that are real searches."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.graph = CampusGraph.load(graph_path())
        cls.places = PlaceIndex(cls.graph, OFFLINE)

    def dialogue(self) -> Dialogue:
        return Dialogue(self.graph, client=None, narrator=None, places=self.places)

    def node(self, name: str) -> str:
        for candidate in self.graph.nodes.values():
            if candidate.name == name:
                return candidate.id
        raise AssertionError(name)

    def test_one_stop_still_offers_alternatives(self) -> None:
        # It used to return exactly one route: "the second best way through Core P is not a
        # question anybody asked". It is, as soon as somebody wants to choose.
        routes = self.dialogue()._routed_via(
            self.node("Block Y (Lee Shau Kee Building)"),
            [self.node("Core P")],
            self.node("Core A"),
        )
        self.assertGreater(len(routes), 1)

    def test_every_option_goes_through_every_stop_in_order(self) -> None:
        stops = [self.node("Core P"), self.node("Block L (Pao Yue-Kong Library)")]
        routes = self.dialogue()._routed_via(
            self.node("Block Y (Lee Shau Kee Building)"), stops, self.node("Core A")
        )
        for route in routes:
            positions = [route.path.index(stop) for stop in stops]
            self.assertEqual(positions, sorted(positions), route.path)

    def test_a_stop_walk_is_measured_as_one_walk(self) -> None:
        # The total is the sum of the legs, and the legs in the reply come from the joined
        # path -- three named places means two legs, not one.
        stop = self.node("Core P")
        routes = self.dialogue()._routed_via(
            self.node("Block Y (Lee Shau Kee Building)"), [stop], self.node("Core A")
        )
        walk = routes[0]
        self.assertEqual(walk.path.count(stop), 1)
        self.assertEqual(len(place_legs(self.graph, walk)), 2)
        self.assertAlmostEqual(
            walk.total_metres, sum(segment.metres for segment in walk.segments), places=3
        )

    def test_a_blocked_leg_returns_one_answer_that_explains_it(self) -> None:
        talk = self.dialogue()
        stop = self.node("Core P")
        talk.state.closed_nodes.add(stop)
        routes = talk._routed_via(
            self.node("Block Y (Lee Shau Kee Building)"), [stop], self.node("Core A")
        )
        self.assertEqual(len(routes), 1)
        self.assertFalse(routes[0].reachable)

if __name__ == "__main__":
    unittest.main()

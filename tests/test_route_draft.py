"""Reviewing and correcting a transcript must not send it or lose its restrictions."""
from copy import deepcopy
import unittest

from campus_nav.config import graph_path
from campus_nav.dialogue import Dialogue
from campus_nav.graph import CampusGraph


class TestRouteDraft(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = CampusGraph.load(graph_path())

    def setUp(self):
        self.dialogue = Dialogue(self.graph)

    def test_preview_is_read_only_and_selections_replace_only_the_endpoints(self):
        self.dialogue.handle("From Core P to Block X")
        state = deepcopy(self.dialogue.state)
        text = "From Cordy to Block C, without stairs"
        draft = self.dialogue.draft_endpoints(text)
        self.assertEqual(draft["origin"], {"text": "Cordy", "id": None})
        self.assertEqual(draft["destination"], {"text": "Block C", "id": None})
        self.assertEqual(state, self.dialogue.state)

        reply = self.dialogue.handle(text, endpoints={"origin": "POI_5bcd371552_67", "destination": "POI_5bcd371552_19"})
        expected = Dialogue(self.graph).handle("from the library to Jockey Club Innovation Tower without stairs").route
        self.assertEqual(reply.route.path, expected.path)
        self.assertTrue(reply.route.blocking["avoid_stairs"])
        self.assertFalse(any(s.stairs for s in reply.route.segments))

    def test_answer_to_missing_origin_can_be_reviewed_without_changing_the_destination(self):
        self.dialogue.handle("Go to Block Z without stairs.")
        self.assertEqual(self.dialogue.state.pending, "origin")
        draft = self.dialogue.draft_endpoints("Core D")
        self.assertEqual(draft["origin"]["id"], "POI_5bcd371552_4")
        self.assertEqual(draft["destination"]["id"], "POI_5bcd371552_26")
        reply = self.dialogue.handle("Core D", endpoints={"origin": "POI_5ca31aa481"})
        self.assertEqual(self.dialogue.state.origin, "POI_5ca31aa481")
        self.assertFalse(reply.route.reachable)
        self.assertTrue(reply.route.blocking["avoid_stairs"])

    def test_facility_question_and_invalid_ids_do_not_commit_a_route(self):
        query = "Where is the nearest toilet?"
        self.assertIsNone(self.dialogue.draft_endpoints(query))
        state = deepcopy(self.dialogue.state)
        for text, endpoints in [(query, {"origin": "POI_5bcd371552_4"}),
                                ("From Core D to Block Z", {"origin": "POI_5ca31aa481", "destination": "unknown"})]:
            with self.assertRaises(ValueError):
                self.dialogue.handle(text, endpoints=endpoints)
            self.assertEqual(state, self.dialogue.state)

    def test_review_can_change_a_destination_referred_to_by_list_position(self):
        first = self.dialogue.handle("圖書館附近有冇飲水機？").facilities[0]
        text = "帶我去第一個"
        draft = self.dialogue.draft_endpoints(text)
        self.assertEqual(draft["origin"]["id"], "POI_5bcd371552_67")
        self.assertEqual(draft["destination"]["id"], first["node_id"])
        answer = self.dialogue.handle(text, endpoints={"destination": "POI_5bcd371552_26"})
        expected = Dialogue(self.graph).handle("From the library to Block Z").route
        self.assertEqual(answer.route.path, expected.path)

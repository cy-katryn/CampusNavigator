"""Facility queries from a spoken transcript to map cards and the answer read aloud."""
import unittest

from campus_nav.config import graph_path
from campus_nav.dialogue import Dialogue
from campus_nav.facility_speech import facility_details, facility_summary, local_hours
from campus_nav.graph import CampusGraph
from campus_nav.local_names import place_name


class ChineseFacilities(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = CampusGraph.load(graph_path())

    def setUp(self):
        self.dialogue = Dialogue(self.graph)

    def test_nearest_is_straight_line_from_actual_reference(self):
        for question, lang in [("离我最近的饮水机在哪里？", "zh-CN"), ("邊度有飲水機？", "zh-HK")]:
            with self.subTest(question=question):
                result = self.dialogue.handle(question)
                nearest = result.facilities[0]
                ref = self.graph.node(result.facility_result.reference)
                candidates = [n for n in self.graph.nodes.values() if "water_refill" in n.accepts]
                self.assertEqual(nearest["metres"], round(min(ref.distance_to(n) for n in candidates)))
                spoken = facility_summary(result, self.graph, lang)
                self.assertIn(place_name(self.graph, ref.id, lang), spoken)
                self.assertIn("同一個位置" if lang == "zh-HK" else "同一位置", spoken)
                self.assertNotIn("0 米", spoken)
                self.assertIn(str(result.facility_result.total), facility_details(result, self.graph, lang))
                self.assertIsNone(facility_summary(result, self.graph, "en-US"))

    def test_nearest_reports_positive_straight_line_from_named_reference(self):
        result = self.dialogue.handle("圖書館附近有冇飲水機？")
        card = result.facilities[0]
        self.assertGreater(card["metres"], 0)
        for lang, label in (("zh-HK", "直線距離"), ("zh-CN", "直线距离")):
            speech = facility_summary(result, self.graph, lang)
            self.assertIn(label, speech)
            self.assertIn(str(card["metres"]), speech)

    def test_cantonese_followup_routes_to_selected_card(self):
        listing = self.dialogue.handle("圖書館附近有冇飲水機？")
        target = listing.facilities[1]["node_id"]
        self.assertEqual(place_name(self.graph, listing.facility_result.reference, "zh-HK"), "包玉剛圖書館")
        result = self.dialogue.handle("帶我去第二個")
        self.assertTrue(result.route.reachable)
        self.assertEqual(self.dialogue.state.destination, target)
        self.assertEqual(self.dialogue.state.origin, listing.facility_result.reference)

    def test_hours_followup_uses_same_facility(self):
        listing = self.dialogue.handle("咩地方可以裝水？")
        second = self.dialogue.handle("第二個呢")
        hours = self.dialogue.handle("佢幾點開門？")
        self.assertEqual(hours.facility_result.kind, "hours")
        self.assertEqual(listing.facilities[1]["node_id"], second.facilities[0]["node_id"])
        self.assertEqual(second.facilities[0]["node_id"], hours.facilities[0]["node_id"])
        self.assertIn("未有公布", facility_summary(hours, self.graph, "zh-HK"))

    def test_nearest_only_followup_and_variant_toilet_spelling(self):
        prompt = self.dialogue.handle("最近的在哪里？")
        self.assertIn("哪类设施", facility_summary(prompt, self.graph, "zh-CN"))
        for question in ("哪里有饮水机？", "附近有厠所嗎"):
            with self.subTest(question=question):
                listing = self.dialogue.handle(question)
                nearest = self.dialogue.handle("最近的在哪里？")
                self.assertEqual(nearest.facilities[0]["node_id"], listing.facilities[0]["node_id"])

    def test_hours_from_named_published_record_and_missing_table(self):
        for question, lang, expected in [
            ("牙科诊所星期一几点开门？", "zh-CN", "星期一09:00至13:00、14:00至18:00"),
            ("牙科診所禮拜日有冇開？", "zh-HK", "星期日休息"),
            ("图书馆几点开门？", "zh-CN", "未公布开放时间"),
        ]:
            with self.subTest(question=question):
                result = self.dialogue.handle(question)
                self.assertIn(expected, facility_summary(result, self.graph, lang))
                self.assertIn(expected, facility_details(result, self.graph, lang))
        unknown = self.dialogue.handle("龍尾診所幾時開門？")
        self.assertEqual(unknown.facility_result.kind, "no_match")

    def test_hours_options_honor_user_choice(self):
        choice = self.dialogue.handle("食堂营业时间")
        self.assertEqual(choice.facility_result.kind, "hours_options")
        self.assertIn("哪个", facility_summary(choice, self.graph, "zh-CN"))
        next_reply = self.dialogue.handle("第二个呢")
        self.assertEqual(next_reply.facility_result.rows[0]["name"], choice.facility_result.rows[1]["name"])

    def test_overview_missing_and_valid_categories(self):
        overview = self.dialogue.handle("校園有咩設施？")
        self.assertIn("洗手間", facility_details(overview, self.graph, "zh-HK"))
        missing = self.dialogue.handle("最近的游泳池在哪里？")
        self.assertFalse(missing.facilities)
        self.assertIn("游泳池", facility_summary(missing, self.graph, "zh-CN"))
        aed = self.dialogue.handle("最近的 AED 在哪里？")
        self.assertTrue(all("aed" in c["name"].lower() for c in aed.facilities))
        self.assertGreater(aed.facility_result.total, 0)
        accessible = self.dialogue.handle("附近有无障碍厕所吗？")
        self.assertTrue(accessible.facilities)
        self.assertTrue(all(c["name"].lower().startswith("accessible toilet") for c in accessible.facilities))

    def test_invalid_reference_and_inactive_choice(self):
        result = self.dialogue.handle("月球基地附近有饮水机吗")
        self.assertIn("月球基地", facility_summary(result, self.graph, "zh-CN"))
        self.assertFalse(result.facilities)
        self.assertIn("哪个设施", facility_summary(self.dialogue.handle("第二个"), self.graph, "zh-CN"))

    def test_hours_text_preserves_unknown_values(self):
        self.assertEqual(local_hours("", "zh-CN"), "")
        self.assertEqual(local_hours("closed", "zh-HK"), "休息")
        self.assertEqual(local_hours("09:00-13:00, 14:00-18:00", "zh-CN"), "09:00至13:00、14:00至18:00")
        self.assertEqual(local_hours("by appointment", "zh-CN"), "by appointment")


if __name__ == "__main__":
    unittest.main()

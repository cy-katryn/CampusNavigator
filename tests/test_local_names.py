import unittest

from campus_nav.config import graph_path
from campus_nav.graph import CampusGraph
from campus_nav.local_names import building_name, floor_name, localize_label, place_name


class LocalNamesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = CampusGraph.load(graph_path())

    def test_official_campus_names_and_concrete_endpoints(self):
        for ident, en, hk, cn in (
            ("CORE_D", "Core D", "D棟", "D栋"),
            ("BLOCK_Z", "Block Z", "Z座", "Z座"),
            ("PAO_YUE_KONG_LIBRARY", "Pao Yue-kong Library", "包玉剛圖書館", "包玉刚图书馆"),
            ("node/13097960102", "East Entrance (Shaw Amenities Building)", "邵逸夫樓東入口", "邵逸夫楼东入口"),
            ("node/13097959906", "Stair Entrance (Block Z)", "Z座樓梯入口", "Z座楼梯入口"),
            ("node/13097959989", "Emergency Exit (Jockey Club Innovation Tower)", "賽馬會創新樓緊急出口", "赛马会创新楼紧急出口"),
            ("POI_5c0f712873", "Lecture Theatre (PQ305)", "演講廳 (PQ305)", "演讲厅 (PQ305)"),
        ):
            with self.subTest(ident=ident):
                self.assertEqual(place_name(self.graph, ident, "en-US"), en)
                self.assertEqual(place_name(self.graph, ident, "zh-HK"), hk)
                self.assertEqual(place_name(self.graph, ident, "zh-CN"), cn)
                self.assertEqual(localize_label(self.graph, en, "zh-CN"), cn)

    def test_student_hub_matches_its_bilingual_building_footprint(self):
        for ident in ("WONG_MAN_AND_TANG_KIT_WAH_GLOBAL_STUDENT_HUB", "POI_629da382f2"):
            self.assertEqual(place_name(self.graph, ident, "zh-HK"), "黃民鄧潔華伉儷環球學生薈")
            self.assertEqual(place_name(self.graph, ident, "zh-CN"), "黄民邓洁华伉俪环球学生荟")

    def test_greenmap_uses_literal_address_not_the_nearest_graph_building(self):
        self.assertEqual(self.graph.node("SVC_177").parent, "SHAW_SPORTS_COMPLEX")
        self.assertEqual(place_name(self.graph, "SVC_177", "zh-HK"), "H棟的UniRe 回收點")
        self.assertEqual(place_name(self.graph, "SVC_64", "zh-CN"), "Z座2楼的玻璃瓶回收点")
        self.assertEqual(building_name(self.graph, "Outside Core B", "zh-HK"), "B棟外")
        self.assertEqual(building_name(self.graph, "Lee Shau Kee Building (Y)", "zh-CN"), "李兆基楼（Y）")

    def test_campus_floor_notation(self):
        for floor, hk, cn in (
            ("G/F", "地下", "地面层"),
            ("P/F", "平台層", "平台层"),
            ("1/F", "1樓", "1楼"),
            ("-2", "地庫2樓", "地下2层"),
            ("B1", "地庫1樓", "地下1层"),
            ("", "", ""),
        ):
            with self.subTest(floor=floor):
                self.assertEqual(floor_name(floor, "en-US"), floor)
                self.assertEqual(floor_name(floor, "zh-HK"), hk)
                self.assertEqual(floor_name(floor, "zh-CN"), cn)

    def test_unknown_places_and_brand_names_keep_their_identity(self):
        self.assertEqual(place_name(self.graph, "node/13097960126", "zh-CN"), "Gourmet Shop")
        self.assertEqual(place_name(self.graph, "node/13097960123", "zh-HK"), "VA 學生飯堂")
        self.assertEqual(place_name(self.graph, "POI_5bcd371452_75", "zh-CN"), "VA 小吃亭（SUBWAY）")
        self.assertEqual(building_name(self.graph, "Example Hall", "zh-CN"), "Example Hall")
        self.assertEqual(localize_label(self.graph, "unknown at Core D", "zh-CN"), "unknown at Core D")


if __name__ == "__main__":
    unittest.main()

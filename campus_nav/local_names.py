"""Offline display names for the English, Cantonese and Mandarin campus guides."""
from __future__ import annotations

import json
import re
from functools import lru_cache

from .config import AUTHORED_DIR, NODES_FILE, RAW_DIR
from .graph import CampusGraph


# These generic labels come from the graph builder. Proper names come from the
# campus map's language fields or OSM's name:zh, never brand/operator aliases.
_LABELS = {
    "Entrance": ("入口", "入口"), "Main Entrance": ("正門", "正门"),
    "East Entrance": ("東入口", "东入口"), "West Entrance": ("西入口", "西入口"),
    "Side Entrance": ("側門", "侧门"), "Service Entrance": ("服務入口", "服务入口"),
    "Stair Entrance": ("樓梯入口", "楼梯入口"), "Emergency Exit": ("緊急出口", "紧急出口"),
    "Car Park Entrance": ("停車場入口", "停车场入口"), "Lift": ("升降機", "电梯"),
    "Toilet": ("洗手間", "洗手间"), "Water Station": ("飲水機", "饮水机"),
    "Security Office": ("保安室", "保安室"), "Flagpole": ("旗桿", "旗杆"),
    "Canteen food waste": ("飯堂廚餘收集點", "食堂厨余收集点"),
    "Pantry food waste": ("茶水間廚餘收集點", "茶水间厨余收集点"),
    "Glass bottle recycling": ("玻璃樽回收點", "玻璃瓶回收点"),
    "Battery recycling": ("充電池回收點", "充电电池回收点"),
    "Reverse vending": ("智能飲品膠樽回收機", "饮料瓶回收机"),
    "UniRe recycling": ("UniRe 回收點", "UniRe 回收点"),
    "Food and Snacks": ("食品及小食", "食品及小吃"),
    "Canteen": ("飯堂", "食堂"), "Student Canteen": ("學生飯堂", "学生食堂"),
    "Café": ("咖啡店", "咖啡店"), "Kiosk": ("小食亭", "小吃亭"),
}
# For OSM features with only a traditional name. Official bilingual records
# use their own supplied spelling, including differences beyond script.
_OSM_SIMPLIFIED = str.maketrans(
    "棟樓圖書館陳麗賽馬會創綜藝鍾體育廳務國銀團聯學醫療處結診視復劇廚廠華鮑瑩樹潔環黃鄧儷薈偉張講愛繼匯溫傑葉呂鴻屬業門鐵輪對維園賢鮮鐘場廣牆誠剛舊",
    "栋楼图书馆陈丽赛马会创综艺钟体育厅务国银团联学医疗处结诊视复剧厨厂华鲍莹树洁环黄邓俪荟伟张讲爱继汇温杰叶吕鸿属业门铁轮对维园贤鲜钟场广墙诚刚旧",
)
_CHINESE = {"zh-HK": 0, "zh-CN": 1}
_HAN = re.compile(r"[\u3400-\u9fff]")
_PARENS = re.compile(r"^(.*?)\s*[(（]([^()（）]+)[)）]$")


def _key(label: str) -> str:
    return " ".join(label.casefold().replace(".", " ").split())


@lru_cache(maxsize=1)
def _campus_names() -> dict[str, tuple[str, str]]:
    result = {}
    path = RAW_DIR / "campus_map_pois.json"
    if not path.is_file():
        return result
    records = json.loads(path.read_text(encoding="utf-8"))["records"]
    for record in records:
        names = record.get("name") or {}
        en, hk, cn = (names.get(code, "").strip() for code in ("en_US", "zh_HK", "zh_CN"))
        if not (en and hk and cn):
            continue
        result[_key(en)] = (hk, cn)
        # The map publishes forms such as "Block V (Jockey Club Innovation Tower)"
        # with the same structure in both translations. Keep both constituent names.
        if record["category"] == "Building" and re.match(r"(?:Core|Block|Wing) [A-Z]+\s*\(", en):
            parts = [_PARENS.fullmatch(value) for value in (en, hk, cn)]
            if all(parts):
                for group in (1, 2):
                    english, cantonese, mandarin = [p[group].strip() for p in parts]
                    result.setdefault(_key(english), (cantonese, mandarin))
    # The campus export corrupts the last character of the student hub to 薪 / 荥.
    # The bilingual building footprint (way/1492140378) identifies it as 學生薈.
    result[_key("Wong Man and Tang Kit Wah Global Student Hub")] = (
        "黃民鄧潔華伉儷環球學生薈", "黄民邓洁华伉俪环球学生荟"
    )
    return result


@lru_cache(maxsize=1)
def _osm_names() -> dict[str, str]:
    names = {}
    for path in (NODES_FILE, AUTHORED_DIR / "campus_buildings.geojson"):
        if path.is_file():
            for feature in json.loads(path.read_text(encoding="utf-8"))["features"]:
                props = feature["properties"]
                if props.get("name:zh") and props.get("id"):
                    names[props["id"]] = props["name:zh"]
    return names


def floor_name(floor: str, lang: str) -> str:
    floor = str(floor) if floor is not None else ""
    if lang not in _CHINESE or not floor:
        return floor
    level = floor.strip().upper()
    if level in ("G", "GF", "G/F"):
        return "地下" if lang == "zh-HK" else "地面层"
    if level in ("P", "PF", "P/F"):
        return "平台層" if lang == "zh-HK" else "平台层"
    basement = re.fullmatch(r"(?:B|-)(\d+)(?:/F)?", level)
    if basement:
        return f"地庫{basement[1]}樓" if lang == "zh-HK" else f"地下{basement[1]}层"
    numbered = re.fullmatch(r"(\d+)(?:/?F)?", level)
    if numbered:
        return numbered[1] + ("樓" if lang == "zh-HK" else "楼")
    return floor


def building_name(graph: CampusGraph, label: str, lang: str) -> str:
    """Localize a building without replacing an unrecognized address by a nearby node."""
    if not label:
        return ""
    node = graph.nodes.get(label)
    if node and node.kind == "building":
        return place_name(graph, node.id, lang)
    if lang not in _CHINESE:
        return label
    idx = _CHINESE[lang]
    label = label.strip()
    official = _campus_names().get(_key(label))
    if official:
        return official[idx]
    label_key = _key(label)
    for node in graph.nodes.values():
        if node.kind == "building" and _key(node.name) == label_key:
            return place_name(graph, node.id, lang)

    # GreenMap uses literal addresses such as "Outside Core B" and building
    # codes in brackets. Its graph parent can refer to a different building.
    if "/" in label:
        return "／".join(building_name(graph, part.strip(), lang) for part in label.split("/"))
    if label.startswith("Outside "):
        return building_name(graph, label[8:], lang) + "外"
    code = re.fullmatch(r"(.+?)\s+\(([A-Z]+)\)", label)
    if code:
        return f"{building_name(graph, code[1], lang)}（{code[2]}）"
    block = re.fullmatch(r"(Core|Block|Wing) ([A-Z]+)", label)
    if block:
        return block[2] + {"Core": ("棟", "栋")[idx], "Block": "座", "Wing": "翼"}[block[1]]
    suffix = re.fullmatch(r"([A-Z]+) (Wing|Courtyard)", label)
    if suffix:
        return suffix[1] + ("翼" if suffix[2] == "Wing" else "庭院")
    return label


def place_name(graph: CampusGraph, node_id: str, lang: str) -> str:
    """Render the actual endpoint or facility, including the particular entrance."""
    node = graph.nodes.get(node_id)
    if node is None:
        return node_id
    label = node.name
    if lang not in _CHINESE:
        return label or node_id
    idx = _CHINESE[lang]
    official = _campus_names().get(_key(label))
    if official and _HAN.search(official[idx]):
        return official[idx]
    if node.source == "osm":
        zh = _osm_names().get(node.ref)
        if zh:
            return zh if idx == 0 else zh.translate(_OSM_SIMPLIFIED)
    if node.kind == "building" or node.source == "campus-map":
        # Graph-only distributions may omit the original map files. These two
        # node sources include a place-name alias (not an operator or brand).
        zh = node.name_zh if _HAN.search(node.name_zh) else ""
        if zh:
            return zh if idx == 0 else zh.translate(_OSM_SIMPLIFIED)
    if node.source == "greenmap":
        parts = label.split(" · ")
        if len(parts) >= 2 and parts[0] in _LABELS:
            location = building_name(graph, parts[1], lang)
            floor = floor_name(parts[2], lang) if len(parts) > 2 else ""
            return f"{location}{floor}的{_LABELS[parts[0]][idx]}"
    qualified = _PARENS.fullmatch(label)
    if qualified and qualified[1] in _LABELS:
        return building_name(graph, qualified[2], lang) + _LABELS[qualified[1]][idx]
    kiosk = re.fullmatch(r"([A-Z]+) (Student Canteen|Canteen|Caf[ée]|Kiosk)(?: \((.+)\))?", label)
    if kiosk:
        category = _LABELS["Café" if kiosk[2] == "Cafe" else kiosk[2]][idx]
        brand = f"（{kiosk[3]}）" if kiosk[3] else ""
        return f"{kiosk[1]} {category}{brand}"
    if label in _LABELS:
        return _LABELS[label][idx]
    if node.kind == "waypoint" and not label:
        return ("路徑點", "路径点")[idx]
    return official[idx] if official else (label or node_id)


def localize_label(graph: CampusGraph, label: str, lang: str) -> str:
    """Localize a matched display label; leave unknown clarification text intact."""
    if label in graph.nodes:
        return place_name(graph, label, lang)
    if lang not in _CHINESE or not label:
        return label
    key = _key(label)
    for node in graph.nodes.values():
        if node.name and _key(node.name) == key:
            return place_name(graph, node.id, lang)
    return label

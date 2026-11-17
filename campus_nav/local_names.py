"""Display names for the English, Cantonese and Mandarin campus guides.

Every name this module renders is already in the graph. campus_graph.json carries the
place name in both scripts (name_zh, name_zh_cn) and the building label each place
stands in, also in both scripts, because tools/build.py resolves them once when the
graph is written.

Nothing here reads the published scrape, and nothing here converts one script to the
other: a conversion table maintained by hand drifts from the source it was copied from,
and this module used to keep one.

The two script names reach the graph through the authored files, not through this
module. data/authored/campus_pois.geojson and campus_buildings.geojson carry
name:zh-Hant and name:zh-Hans, copied from the campus map's own bilingual record
(561 places it publishes in both scripts); tools/build.py copies them onto the node.
A place the campus map publishes in Latin only -- a shopfront, a brand -- keeps that
spelling in both columns, which is what the sign says.
"""
from __future__ import annotations

import re

from .graph import CampusGraph
from .places import label_key as _key


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
    "Food and Snacks": ("食品及小食", "食品及小吃"),
    "Canteen": ("飯堂", "食堂"), "Student Canteen": ("學生飯堂", "学生食堂"),
    "Café": ("咖啡店", "咖啡店"), "Kiosk": ("小食亭", "小吃亭"),
}
_CHINESE = {"zh-HK": 0, "zh-CN": 1}
_HAN = re.compile(r"[\u3400-\u9fff]")
_PARENS = re.compile(r"^(.*?)\s*[(（]([^()（）]+)[)）]$")

# Building labels the campus map lists but never publishes as a place of their own, so
# the builder has no place name to copy from. Kept here, small and explicit, rather
# than inferred from a scraped file at run time.
_BUILDING_LABELS = {
    "Wing QT": ("QT翼", "QT翼"),
}


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
    if node and node.kind == "place":
        return place_name(graph, node.id, lang)
    if lang not in _CHINESE:
        return label
    idx = _CHINESE[lang]
    label = label.strip()
    key = _key(label)

    # A place with this exact name is the authoritative answer.
    for node in graph.nodes.values():
        if node.name and _key(node.name) == key:
            return place_name(graph, node.id, lang)

    # "Jockey Club Innovation Tower" on its own is the second half of "Block V (Jockey
    # Club Innovation Tower)", so a bracketed place name answers for either half -- with
    # that half of its Chinese name, not the whole thing.
    for node in graph.nodes.values():
        qualified = _PARENS.fullmatch(node.name or "")
        if not qualified:
            continue
        written = (node.name_zh, node.name_zh_cn)[idx]
        chinese = _PARENS.fullmatch(written) if written else None
        if not chinese:
            continue
        if _key(qualified[1]) == key:
            return chinese[1].strip()
        if _key(qualified[2]) == key:
            return chinese[2].strip()

    # Otherwise the builder already resolved this label when it wrote the graph, for
    # the labels whose own place is not in the graph at all, and stored both scripts.
    resolved = graph.meta.get("building_names", {}).get(key) or []
    if idx < len(resolved) and resolved[idx]:
        return resolved[idx]
    declared = _BUILDING_LABELS.get(label)
    if declared:
        return declared[idx]

    # The campus map publishes literal addresses such as "Outside Core B" and
    # building codes in brackets. The graph parent can refer to a different one.
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

    # The graph carries the name in both scripts, so the right one is a lookup. A brand
    # or a shopfront has no Chinese name of its own, and is described from its parts.
    written = (node.name_zh, node.name_zh_cn)[idx]
    if written and _HAN.search(written):
        return written

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
    # The campus map sometimes publishes a Latin name in its Chinese field, and that
    # spelling is the one on the sign: "U.Green", not "U Green (Staff Club)".
    return node.name_zh or label or node_id


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

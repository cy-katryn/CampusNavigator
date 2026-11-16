"""Localised facility replies built from the map selection and published hours."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .kb import HKT, parse_slots
from .local_names import building_name, floor_name, place_name


@dataclass
class FacilityResult:
    kind: str
    subject: str = ""
    total: int = 0
    reference: str = ""
    counts: dict[str, int] = field(default_factory=dict)
    rows: list[dict] = field(default_factory=list)
    day: str = ""
    route_origin: str = ""


# Left is Mandarin, right is Hong Kong Cantonese. Category labels describe
# the dataset, including the distinction between toilets and accessible toilets.
_LABELS = {
    "water station": ("饮水机", "飲水機"), "toilet": ("洗手间", "洗手間"),
    "accessible toilet": ("无障碍洗手间", "暢通易達洗手間"), "aed": ("AED", "AED"),
    "canteen": ("食堂", "飯堂"), "cafe": ("咖啡厅", "咖啡店"), "clinic": ("诊所", "診所"),
    "bank": ("银行", "銀行"), "bookstore": ("书店", "書店"), "postbox": ("邮筒", "郵筒"),
    "lactation room": ("哺乳室", "集乳室"), "prayer room": ("祈祷室", "祈禱室"),
    "recycling": ("回收点", "回收點"), "battery": ("电池回收点", "電池回收點"),
    "glass bottle": ("玻璃瓶回收点", "玻璃樽回收點"), "plastic": ("塑料回收点", "塑膠回收點"),
    "paper": ("废纸回收点", "廢紙回收點"), "metal": ("金属回收点", "金屬回收點"),
    "computer": ("电脑回收点", "電腦回收點"), "food waste": ("厨余回收点", "廚餘回收點"),
    "rechargeable battery collection": ("充电电池回收点", "充電池回收點"),
    "glass bottle collection": ("玻璃瓶回收点", "玻璃樽回收點"),
    "reverse vending machine": ("智能回收机", "智能回收機"),
    "canteen food waste collection": ("食堂厨余回收点", "飯堂廚餘回收點"),
    "pantry food waste collection": ("茶水间厨余回收点", "茶水間廚餘回收點"),
    "unire© (university / universal recycling facilities)": ("UniRe 回收点", "UniRe 回收點"),
    "bus stop": ("巴士站", "巴士站"), "lift": ("电梯", "升降機"),
    "faculty / department": ("院系", "學院及學系"), "office": ("办公室", "辦公室"),
    "lecture theatre": ("演讲厅", "演講廳"), "shop": ("商店", "商店"),
    "research office": ("研究办公室", "研究辦公室"), "club room": ("活动室", "活動室"),
    "theatre": ("剧院", "劇院"), "mtr service station": ("港铁服务站", "港鐵服務站"),
    "car park entrance": ("停车场入口", "停車場入口"), "security office": ("保安室", "保安室"),
    "flagpole": ("旗杆", "旗桿"), "clock": ("时钟", "時鐘"), "": ("其他地点", "其他地點"),
}
_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def requested_day(question: str) -> str:
    if re.search(r"公[眾众]假(?:期|日)|公共假期", question):
        return "PublicHoliday"
    match = re.search(r"(?:星期|禮拜|礼拜|[週周])([一二三四五六日天])", question)
    if match:
        return _DAYS["一二三四五六日".index(match.group(1).replace("天", "日"))]
    if re.search(r"明[天日]|聽日|听日", question):
        return _DAYS[(datetime.now(HKT) + timedelta(days=1)).weekday()]
    return ""


def _label(text, hk):
    pair = _LABELS.get(text.lower())
    return pair[int(hk)] if pair else text


def _name(card, graph, lang):
    hk = lang == "zh-HK"
    node_id = card.get("node_id")
    name = place_name(graph, node_id, lang) if node_id in graph.nodes else ""
    original = card.get("name", "")
    if not name or name == original:
        name = (card.get("name_zh") if hk else card.get("name_zh_cn")) or card.get("name_zh") or name or original
        # Generic labels with location suffixes can be reconstructed from
        # the node's own structured category, building and floor.
        service = card.get("service", "")
        if name == original and service and (" (" in name or " · " in name):
            if service.lower() in _LABELS:
                name = _label(service, hk)
    building = building_name(graph, card.get("building", ""), lang) if card.get("building") else ""
    floor = floor_name(card["floor"], lang) if card.get("floor") else ""
    location = " ".join(v for v in (building, floor) if v and v not in name)
    return f"{name}（{location}）" if location else name


def _day_name(day, hk):
    if not day:
        return "今日" if hk else "今天"
    if day == "PublicHoliday":
        return "公眾假期" if hk else "公众假期"
    return "星期" + "一二三四五六日"[_DAYS.index(day)]


def local_hours(hours_today, lang):
    value = hours_today or ""
    if not value or lang not in ("zh-HK", "zh-CN"):
        return value
    if value.lower() == "closed":
        return "休息" if lang == "zh-HK" else "不开放"
    return re.sub(r"(\d\d:\d\d)\s*-\s*(\d\d:\d\d)", r"\1至\2", value).replace(", ", "、")


def _timetable(card, day, hk):
    if not day:
        value = card.get("hours_today") or ""
    else:
        raw = (card.get("opening_hours") or {}).get(day) or []
        closed = any(str(v).upper() == "CLOSED" for v in ([raw] if isinstance(raw, str) else raw))
        value = ", ".join(f"{a}-{b}" for a, b in parse_slots(raw)) or ("closed" if closed else "")
    label = _day_name(day, hk)
    if value == "closed":
        return label + ("休息" if hk else "不开放")
    if not value:
        if card.get("hours_summary"):
            return label + ("嘅開放時間未有公布" if hk else "的开放时间未公布")
        return "校園地圖未有公布開放時間" if hk else "校园地图未公布开放时间"
    times = local_hours(value, "zh-HK" if hk else "zh-CN")
    return f"{label}{times}"


def facility_summary(reply, graph, lang) -> str | None:
    info = getattr(reply, "facility_result", None)
    if lang not in ("zh-HK", "zh-CN") or info is None:
        return None
    hk = lang == "zh-HK"
    rows = info.rows or reply.facilities
    subject = _label(info.subject, hk)
    if info.kind == "overview":
        top = sorted((k for k in info.counts if k), key=info.counts.get, reverse=True)[:4]
        categories = "、".join(_label(k, hk) for k in top)
        if not categories:
            return "地圖上暫時冇設施記錄。" if hk else "地图上暂无设施记录。"
        return f"地圖上有{categories}等設施。你想搵邊一類？" if hk else f"地图上有{categories}等设施。你想找哪一类？"
    if info.kind == "no_match":
        return f"校園地圖搵唔到「{subject}」嘅記錄。" if hk else f"校园地图上没有找到「{subject}」的记录。"
    if info.kind == "unknown_reference":
        return f"未能確認你講嘅「{subject}」。你想由邊度開始搵？" if hk else f"没能确认你说的「{subject}」。你想从哪里开始找？"
    if info.kind == "choice_missing":
        return "你指邊個設施？請講個名，或者揀畫面上嘅編號。" if hk else "你指哪个设施？请说出名称，或选择列表中的编号。"
    if info.kind == "ask_category":
        return "你想搵邊類設施？例如飲水機或者洗手間。" if hk else "你想找哪类设施？比如饮水机或洗手间。"
    if not rows:
        return None
    first = rows[0]
    name = _name(first, graph, lang)
    if info.kind == "no_route":
        return f"地圖未有去{name}嘅路線。" if hk else f"地图上没有通往{name}的路线。"
    if info.kind == "hours":
        schedule = _timetable(first, info.day, hk)
        state = first.get("open_now")
        current = ""
        if not info.day and state is not None and first.get("hours_today") != "closed":
            current = ("，按時間表而家開放" if hk else "，按时间表现在开放") if state else ("，按時間表而家休息" if hk else "，按时间表现在不开放")
        return f"{name}，{schedule}{current}。"
    if info.kind == "hours_options":
        labels = "、".join(_name(row, graph, lang) for row in rows[:2])
        return (f"有 {info.total} 個符合條件嘅地點，包括{labels}。你想查邊個嘅開放時間？" if hk else
                f"有 {info.total} 个符合条件的地点，包括{labels}。你想查哪个的开放时间？")
    if info.kind in ("list", "selected"):
        reference = place_name(graph, info.reference, lang) if info.reference in graph.nodes else ""
        if reference and first.get("metres") is not None:
            distance = first["metres"]
            nearest = ("最近嘅係" if hk else "最近的是") if info.kind == "list" else ""
            if distance == 0:
                origin = graph.node(info.reference)
                if (origin.lat, origin.lng) == (first.get("lat"), first.get("lng")):
                    return (f"{nearest}{name}，地圖上同{reference}標喺同一個位置。" if hk else
                            f"{nearest}{name}，地图上与{reference}标在同一位置。")
                return (f"{name}同{reference}嘅直線距離唔夠一米。" if hk else
                        f"{name}与{reference}的直线距离不到一米。")
            return (f"由{reference}起計，{nearest}{name}，直線距離大約 {distance} 米。" if hk else
                    f"从{reference}算起，{nearest}{name}，直线距离约 {distance} 米。")
        return f"地圖上搵到{name}。" if hk else f"地图上找到了{name}。"
    return None


def facility_details(reply, graph, lang) -> str | None:
    summary = facility_summary(reply, graph, lang)
    if not summary:
        return None
    info = reply.facility_result
    hk = lang == "zh-HK"
    if info.kind == "overview":
        counts = sorted(info.counts.items(), key=lambda pair: -pair[1])
        return "\n".join(f"{_label(k, hk)} · {v}" for k, v in counts)
    rows = info.rows or reply.facilities
    if info.kind == "hours" and rows:
        raw = rows[0].get("opening_hours") or {}
        days = [d for d in (*_DAYS, "PublicHoliday") if raw.get(d)]
        if days:
            return "\n".join([_name(rows[0], graph, lang)] + [_timetable(rows[0], d, hk) for d in days])
    if info.kind in ("list", "hours_options"):
        lines = []
        for i, card in enumerate(rows, 1):
            label = _name(card, graph, lang)
            if info.kind == "hours_options":
                label += " · " + _timetable(card, info.day, hk)
            elif card.get("metres") is not None:
                label += f' · {card["metres"]} 米'
            lines.append(f"{i}. {label}")
        if info.total > len(rows):
            lines.append((f"顯示頭 {len(rows)} 個，共 {info.total} 個。" if hk else
                          f"显示前 {len(rows)} 个，共 {info.total} 个。"))
        return "\n".join(lines)
    return summary

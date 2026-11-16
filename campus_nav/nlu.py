"""Turn a sentence into slots.

Two parsers share one output type:

  * `parse_with_llm`   -- asks the model for strict JSON, then validates it.
                          Any failure (no key, timeout, bad JSON, invented place)
                          returns None and the caller falls through.
  * `parse_with_grammar` -- deterministic regex rules. This is the guaranteed
                          path: the live demo can run with the network unplugged.

The LLM never decides a route and never invents a place id: unknown place text is
kept as raw text and resolved against the graph afterwards. That is a deliberate
architectural boundary, not a limitation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field     

from .graph import CampusGraph, Node
from .llm import OpenRouterClient

INTENTS = (
    "route",          # find a way from A to B
    "distance",       # how far is A from B
    "close",          # this link or place is closed
    "green_query",    # where can I recycle X / what green facilities are there
    "facility_query", # where is the nearest AED / toilet / water station / canteen
    "hours_query",    # is the canteen open now / what are the clinic's hours
    "reset",
    "help",
    "out_of_scope",
)

SYSTEM_PROMPT = """You convert a user's campus-navigation message into JSON slots.

Available places (use the id when you are confident, otherwise null):
{places}

Return ONLY a JSON object with exactly these keys:
{{
  "intent": one of {intents},
  "origin": place id or null,
  "destination": place id or null,
  "closed": the place or link the user says is closed, as free text, or null,
  "item": short lowercase item or facility name ("batteries", "aed") or null,
  "language": "en" or "zh"
}}

Rules:
- Never invent a place id that is not in the list. If the user names a place that
  is not listed, put null and keep intent "route".
- "is the canteen open now", "opening hours of the clinic" -> intent "hours_query".
- "where is the nearest AED / toilet / water station" -> intent "facility_query"
  with item set to that facility ("aed", "toilet", "water station").
- Output JSON only, no prose, no code fences."""


@dataclass
class Slots:
    intent: str = "out_of_scope"
    origin_text: str = ""
    destination_text: str = ""
    closed_text: str = ""    # the raw words around a closure ("the library is closed")
    item: str = ""
    subject: str = ""      # the raw words the question is *about* (hours/facility queries)
    language: str = "en"
    source: str = "grammar"
    raw: dict = field(default_factory=dict)
    avoid_stairs: bool | None = None  # None keeps the current route preference
    facility_origin: str = ""
    facility_target: str = ""
    facility_choice: int | None = None  # zero-based position in the last displayed list

    @property
    def mentions_places(self) -> bool:
        return bool(self.origin_text or self.destination_text)


def _place_catalogue(graph: CampusGraph, limit: int = 90) -> str:
    """The places the model is shown, in the order it is most likely to need them.

    Every building is listed, plus ONE representative of each service type: 192 nodes
    would blow the prompt budget, and the model does not need the id of a particular
    recycling bin to understand "where can I recycle batteries".
    """
    buildings = [
        n for n in graph.nodes.values()
        if n.kind not in ("service", "waypoint") and n.name
    ]
    representatives: dict[str, object] = {}
    for node in graph.nodes.values():
        if node.kind == "service" and node.service not in representatives:
            representatives[node.service] = node

    rows = []
    for node in buildings[:limit]:
        extra = f" ({', '.join(node.aliases[:3])})" if node.aliases else ""
        rows.append(f"- {node.id}: {node.name}{extra}")
    for node in representatives.values():
        rows.append(f"- {node.id}: {node.name} [one of the '{node.service}' places]")
    return "\n".join(rows)


def parse_with_llm(text: str, graph: CampusGraph, client: OpenRouterClient) -> Slots | None:
    prompt = SYSTEM_PROMPT.format(
        places=_place_catalogue(graph),
        intents=", ".join(f'"{i}"' for i in INTENTS),
    )
    result = client.chat(
        [
            {"role": "system", "content": prompt},
            {"role": "user", "content": text},
        ],
        max_tokens=220,
        retries=2,
    )
    payload = result.json()
    if not payload:
        return None

    intent = str(payload.get("intent") or "").strip()
    if intent not in INTENTS:
        return None

    def as_id(value) -> str:
        """Keep a valid node id; keep the user's own wording for anything else.

        An id the model invented is never trusted, but the *text* is not thrown away:
        it is resolved against the graph afterwards by Dialogue._preferred_matches().
        This is the boundary the module docstring describes -- the model may report
        what the user said, it may not create a place.
        """
        if not isinstance(value, str) or not value.strip():
            return ""
        candidate = value.strip()
        return candidate   # an id resolves directly; free text resolves by name later

    closed = payload.get("closed")
    closed_text = closed.strip() if isinstance(closed, str) else ""

    return Slots(
        intent=intent,
        origin_text=as_id(payload.get("origin")),
        destination_text=as_id(payload.get("destination")),
        closed_text=closed_text,
        item=str(payload.get("item") or "").strip().lower(),
        subject=text.strip() if intent in ("hours_query", "facility_query", "green_query") else "",
        language=str(payload.get("language") or "en"),
        source="llm",
        raw=payload,
    )


# Only an explicit preference changes the route. Merely asking whether a route
# contains stairs must not remove those edges from the search.
_STAIR_REQUEST = re.compile(
    # These words belong to the preference clause, never to the preceding POI.
    r"(?:(?:\b(?:and|but|while|with|please|on the way|if possible|"
    r"I(?:['’]d like to| would like to| want to| need to)?|could you|can you)\b|"
    r"中途|路上|途中|全程|最好|盡量|尽量|請|请|麻煩|麻烦|但係|但是|但|記得|记得|我想|我)\s*)*"
    r"(?:(?P<allow>\b(?:no need to|don['’]t need to|do not need to) avoid (?:the )?(?:stairs|steps)\b|"
    r"\b(?:remove|cancel|clear) (?:the )?(?:no[- ]stairs|stair|step[- ]free) "
    r"(?:restriction|preference|filter)\b|"
    r"取消(?:不走|唔行|避[開开]|無|无)?(?:樓梯|楼梯)(?:的|嘅)?(?:限制|要求)|"
    r"\b(?:allow|permit|include|can (?:take|use)|(?:do not|don['’]t) avoid) "
    r"(?:the )?(?:stairs|steps)\b|\b(?:stairs|steps) (?:are )?(?:fine|ok(?:ay)?|allowed)\b|"
    r"(?:允許|允许|可以|可用|不必避[开開]|不用避[开開])(?:行|走|用|爬|使用)?(?:樓梯|楼梯|台阶|台階)|"
    r"(?:樓梯|楼梯)(?:都得|也可以))|"
    r"(?P<avoid>\b(?:avoid(?:ing|s)?|without(?: (?:taking|using|climbing))?|no|skip|"
    r"(?:do not|don['’]t|cannot|can['’]t)(?: want to)? (?:take|use|climb|allow)) (?:(?:any|all|the) )?(?:stairs|steps)\b|"
    r"\b(?:stair|step)[ -]free\b|"
    # Observed Mandarin ASR homophone: 不走楼梯 -> 步走楼梯. Accept the
    # full anomalous clause only, so 徒步走楼梯 (walk up stairs) is unaffected.
    r"(?<![\u3400-\u9fff])步走[楼樓]梯(?=$|[，。；！？,.!?;\s])|"
    r"(?:唔(?:好|使|要|想|可以)?|不(?:要|用|想|能)?|别|別|避[开開]|避免|繞[過开開]|绕[过开]|"
    r"無|无|沒有|没有|冇)\s*(?:再|經過|经过)?(?:行|走|用|爬|上落|上下|上|下)?\s*(?:所有|任何)?\s*"
    r"(?:樓梯|楼梯|台阶|台階|梯級)))",
    re.I,
)


def stair_request(text: str) -> tuple[str, bool | None]:
    """Remove preference words before parsing the origin and destination."""
    matches = list(_STAIR_REQUEST.finditer(text))
    if not matches:
        return text.strip(), None
    clean = _STAIR_REQUEST.sub(" ", text)
    clean = re.sub(r"\bplease[.!? ]*$|(?:的|嘅)(?:路线|路線)[。！？ ]*$", "", clean, flags=re.I)
    clean = re.sub(r"\b(?:and|while|with)\s*[.,!?]*$", "", clean, flags=re.I)
    return clean.strip(" ，,。.!?！？"), matches[-1].lastgroup == "avoid"


# --- grammar fallback -----------------------------------------------------
# "re-open it" lifts every closure, so a demo can recover from its own failure.
REOPEN = re.compile(
    r"\b(re-?open|open (?:it|that|them|everything) again|restore|unblock|"
    r"clear the closures?|remove the closures?)\b",
    re.I,
)
_CLOSE = re.compile(
    r"\b(close[ds]?|closing|shut|blocked|out of service|under maintenance|"
    r"no longer (?:open|available))\b",
    re.I,
)
_FROM_TO = re.compile(r"\bfrom\s+(?P<o>.+?)\s+\bto\s+(?P<d>.+?)(?:[?.!,]|$)", re.I)
# "How do I get to D from O?" and "How far is D from O?" put D first.
_AT_AND_GO = re.compile(
    r"\bi(?: am|['’]m)\s+(?:currently\s+|now\s+)?at\s+(?P<o>.+?)"
    r"[,.;]?\s+(?:and\s+)?(?:i\s+)?(?:want\s+to\s+|would\s+like\s+to\s+)?"
    r"(?:go|get|walk|head)\s+to\s+(?P<d>.+?)(?:[?!.]|$)", re.I,
)
_TO_FROM = re.compile(
    r"(?:^to|\b(?:get|go|walk|head|navigate|directions|route|take me)\s+to|\bhow far is)\s+(?P<d>.+?)\s*[,.;]?\s+"
    r"(?:(?:starting\s+)?from|i(?: am|['’]m)\s+(?:currently\s+|now\s+)?at)\s+"
    r"(?P<o>.+?)(?:[?!.]|$)", re.I,
)
_DISTANCE_BETWEEN = re.compile(
    r"\bdistance\s+between\s+(?P<o>.+?)\s+and\s+(?P<d>.+?)(?:[?!.]|$)", re.I,
)
# Spoken requests often omit "from": "Core D to Block Z". Keep commands
# such as "take me to the library" as destination-only requests.
_PLACE_TO_PLACE = re.compile(r"^(?P<o>.+?)\s+to\s+(?P<d>.+?)(?:[?.!,]|$)", re.I)
_EN_COMMAND = re.compile(
    r"^(?:how|where|what|when|why|which|can|could|would|is|are|i|we|please|"
    r"take|go|get|walk|head|navigate|directions?|route|the link)\b", re.I,
)
# Both scripts are accepted. Keep each campus place intact, including English
# names embedded in a Chinese utterance ("由 Core D 去 Block Z 點行？").
_ZH_PAIR = re.compile(
    r"(?:[从從由]|我(?:而家|依家|現在|现在)?(?:喺|在))\s*(?P<o>.+?)"
    r"\s*[,，。;；]?\s*(?:出[发發]\s*)?(?:我)?(?:想|要)?(?:前往|去|到|往)\s*(?P<d>.+)"
)
_ZH_TO_FROM = re.compile(
    r"(?:去|到|前往)\s*(?P<d>.+?)\s*[,，。;；]?\s*"
    r"(?:我(?:而家|依家|現在|现在)?(?:喺|在)|[从從由])\s*(?P<o>.+)"
)
_ZH_DISTANCE_FROM = re.compile(r"^(?P<d>.+?)(?:距[离離]|[离離])(?P<o>.+)")
_ZH_SHORT_PAIR = re.compile(r"^(?P<o>.+?)\s*(?:去|到)\s*(?P<d>.+)")
_ZH_TO = re.compile(r"^(?:[请請](?:問|问)?\s*)?(?:我(?:想|要)|帶我|带我|點樣|怎麼|怎么)?"
                    r"(?:前往|去|到)\s*(?P<d>.+)")
_ZH_ENDING = re.compile(r"[,，\s]*(?:[点點](?:樣|样)?(?:行|走|去)|怎[么麼样樣](?:走|去)|"
                        r"怎樣走|如何(?:走|去)|該怎麼走|该怎么走)[呢啊呀嗎吗]?$")
# "to X" needs an actual "to": a bare "^" alternative swallows whole sentences.
_TO_ONLY = re.compile(
    r"(?:\bto\s+|\b(?:get|go|walk|head|take\s+me)\s+to\s+)"
    r"(?P<d>[a-z0-9][\w &'\-/().]*?)(?:[?.!,]|$)",
    re.I,
)
# If a travel verb or leading "to" is explicit, preserve an unknown
# destination as written so the dialogue can ask for its name.
_DEST_REQUEST = re.compile(
    r"^to\s+|\b(?:get|go|walk|head|navigate|directions|take me)\s+to\s+", re.I,
)
_ZH_DISTANCE_END = re.compile(r"(?:大[概約约])?(?:有|要)?(?:多[遠远]|[幾几][遠远]|多少米|[幾几]多米)[呢啊呀嗎吗]?$")
_HOW_FAR = re.compile(r"\bhow\s+far\b|\bdistance\b|多[遠远]|[幾几][遠远]|多少米|[幾几]多米", re.I)
_DISTANCE_SUBJECT = re.compile(r"^how far (?:away )?is (?:it to )?(?P<d>.+?)[?!.]*$", re.I)
_DISTANCE_FOLLOWUP = re.compile(
    r"how far(?: is (?:it|that|this|the route))?|(?:what(?:['’]s| is) )?(?:the )?distance|"
    r"(?:有)?(?:多[遠远]|[幾几][遠远])", re.I,
)

_GREEN = re.compile(
    r"\b(recycle|recycling|recyclables?|bin|dispose|disposal|throw|dump|"
    r"battery|batteries|glass|plastic|paper|weee|electronic|canteen|food waste|"
    r"water|refill|solar|green)\b",
    re.I,
)
_ITEM = re.compile(
    r"\b(batteries|battery|glass bottle|bottle|plastic|paper|cardboard|"
    r"fluorescent|lamp|tube|weee|appliance|laptop|computer|phone|food waste|leftovers)\b",
    re.I,
)
# Catch items we have never heard of, so the bot can say "I don't cover that"
# instead of silently listing every facility it knows.
_ITEM_UNKNOWN = re.compile(
    r"\b(?:recycle|recycling|dispose\s+of|throw\s+away|throw|dump|bin|get\s+rid\s+of)\s+"
    r"(?:my|this|that|these|the|an?|some|old|broken|used|unwanted)*\s*"
    r"([a-z][a-z\-]{2,}(?:\s+[a-z][a-z\-]{2,})?)",
    re.I,
)
# Opening-hours questions. Deliberately checked BEFORE the closure rule, because
# "is the canteen closed on Sunday?" is a question about a timetable, not an
# instruction to close a passage.
_HOURS_EN = re.compile(
    r"\b(opening hours?|opening times?|business hours?|what time|which time|"
    r"until when|how late|when (?:is|does|do)\b[^?]*\b(?:open|close)|"
    r"(?:is|are)\b[^?]*\b(?:open|closed)(?![a-z])|still open|open (?:today|now|tonight|"
    r"on (?:mon|tue|wed|thu|fri|sat|sun))|close[sd]? at|hours? of)\b",
    re.I,
)
_HOURS_ZH = re.compile(r"(幾點|幾時|開放|營業|開門|關門|時間|關了|開了)")

# Vocabulary for the categories actually searched. Keep the user's place or item
# wording separately, so an unknown service never turns into a generic list.
_ZH_SERVICES = {
    "accessible toilet": r"無障礙廁所|无障碍厕所|暢通易達洗手間|畅通易达洗手间|傷健洗手間",
    "toilet": r"洗手[間间]|[廁厕厠]所|[衛卫]生[間间]",
    "water station": r"[飲饮]水(?:[機机站點点]|的?地方)?|[裝装斟添補补]水(?:[機机站]|的?地方)?",
    "aed": r"(?:自[動动](?:體外|体外)?)?(?:去|除)[顫颤]器|心[臟脏]除[顫颤]器|AED",
    "canteen": r"食堂|[飯饭]堂|[餐膳][廳厅堂]",
    "clinic": r"[診诊]所|[醫医][務务]室", "bank": r"[銀银]行|ATM",
    "postbox": r"郵筒|邮筒|郵箱|邮箱", "bookstore": r"[書书]店|[書书]局",
    "lactation room": r"集乳室|哺乳室|母[嬰婴]室", "prayer room": r"祈[禱祷]室",
    "recycling": r"回收[站點点箱桶]", "battery": r"[電电]池", "glass bottle": r"玻璃[樽瓶]",
    "plastic": r"塑[料膠胶]", "paper": r"[紙纸](?:皮|張|张)?", "metal": r"金[屬属]|[鋁铝]罐",
    "food waste": r"[廚厨][餘余]", "computer": r"[電电][腦脑]",
}
_ZH_SERVICE = re.compile("|".join(f"(?P<s{i}>{words})" for i, words in enumerate(_ZH_SERVICES.values())), re.I)
_ZH_TIMETABLE = re.compile(
    r"[開开](?:放|門|门|館|馆|到|咗|了|唔[開开]|不[開开])|[關关][門门館馆]|"
    r"[營营][業业]|[幾几][點点時时]|什[麼么][時时]候|收[幾几][點点]|有冇[開开]|(?:有|[仲還还])[開开]|[開开][嗎吗未]"
)
_ZH_WHERE = re.compile(r"最近|附近|哪[裏里儿]|[哪邊边]度|[邊边]個|有[冇沒没]|在哪|有哪|[搵找]|有[咩乜]|有什[么麼]")
_ZH_OVERVIEW = re.compile(r"(?:有|列[出舉举]|看看|[顯显]示).*(?:[設设]施|[設设][備备])|[設设]施(?:列表|清[單单])")
_ZH_CHOICE = re.compile(r"第\s*([一二兩两三四五六七八九十1-9])\s*(?:[個个處处項项間间家]|座)|最近(?:的|嗰|那)[個个間间家]|[那嗰這这](?:一)?[個个間间家]")
_ZH_ROUTE_ACTION = re.compile(r"[帶带]我|怎[么麼]去|[點点]去|去第|去[那嗰]|去最近|[導导]航|怎[么麼]走|[點点]行")


def _chinese_facility(text: str, graph: CampusGraph) -> Slots | None:
    if not re.search(r"[\u3400-\u9fff]", text):
        return None
    phrase = text.strip(" ?？!！.。，")
    match = _ZH_SERVICE.search(phrase)
    item = list(_ZH_SERVICES)[int(match.lastgroup[1:])] if match else ""
    choice = _ZH_CHOICE.search(phrase)
    hours = _ZH_TIMETABLE.search(phrase)
    place_words = re.sub(r"^(?:[請请][問问](?:一下)?|[請请]|想[問问](?:一下)?)", "", phrase).strip()
    slots = Slots(language="zh", subject=phrase, source="grammar", item=item)
    if not item and re.fullmatch(r"最近(?:的|嘅|嗰個|那[个個])?(?:[喺在]?[哪邊边](?:[裡里裏度儿兒])?)[呀啊呢]?", phrase):
        slots.intent, slots.facility_choice = "facility_query", 0
        return slots
    if choice and not item:
        index = choice.group(1) or "一"
        slots.facility_choice = ("一二三四五六七八九十".index(index.replace("兩", "二").replace("两", "二"))
                                 if not index.isascii() else int(index) - 1)
        slots.intent = "hours_query" if hours else ("route" if _ZH_ROUTE_ACTION.search(phrase) else "facility_query")
        return slots
    if not item and re.fullmatch(r"(?:它|佢|[那咁]麼?|[那咁])?(?:的|嘅)?(?:[營营][業业]|[開开]放)?[時时][間间][呢呀啊]?", phrase):
        slots.intent, slots.facility_choice = "hours_query", 0
        return slots
    if hours:
        slots.intent = "hours_query"
        if not item and (re.match(r"^(?:它|佢|嗰度|那里|那裡)", place_words) or
                         re.fullmatch(r"(?:今日|今天|而家|現在|现在)?(?:[幾几][點点時时]|什[麼么][時时]候)?"
                                      r"(?:[開开關关][門门]?|[營营][業业]|[開开]放)[吗嗎呢呀啊未]?", place_words)):
            slots.facility_choice = 0
        slots.facility_target = place_words
        return slots
    explicit_route = names_destination(phrase, graph)
    if explicit_route and not (_ZH_WHERE.search(phrase) and not _ZH_ROUTE_ACTION.search(phrase)):
        return None
    if item and (_ZH_WHERE.search(phrase) or item == "recycling" or not explicit_route):
        slots.intent = "facility_query"
        slots.facility_target = match.group()
        reference = (re.search(r"(?:距[離离]|[離离])\s*(.+?)\s*最近", place_words)
                     or re.search(r"^我(?:而家|依家|現在|现在)?[喺在]\s*([^，,。]+?)(?:[，,。]|附近|最近)", place_words)
                     or re.search(r"^(.+?)(?:附近|旁[边邊])", place_words))
        if reference:
            slots.facility_origin = reference.group(1).strip(" ，,的嘅")
            if re.fullmatch(r"我(?:[這这哪呢]?[裡里裏度邊边兒儿]|所[在處处]|(?:目前|[現现]在)?的位置)?|"
                            r"[當当]前位置|[這这呢][裡里裏度兒儿]|身[邊边]", slots.facility_origin):
                slots.facility_origin = ""
        return slots
    if _ZH_OVERVIEW.search(phrase):
        slots.intent = "facility_query"
        return slots
    unknown = (re.search(r"回收(?:我的|旧的|舊嘅)?([^？?，,。]+?)(?:[嗎吗呢呀啊]|$)", phrase)
               or re.search(r"最近(?:的|嘅)?(.+?)(?:[喺在]?[哪邊边][里裡裏度儿]|在哪)", phrase)
               or re.search(r"(?:附近|校[園园])有(?:冇|[沒没]有)?(.+?)(?:[嗎吗呢呀啊]|$)", phrase))
    if unknown:
        slots.intent = "facility_query"
        slots.item = slots.facility_target = unknown.group(1)
        return slots
    return None
# "where is the nearest AED / toilet / water station" -- not a recyclable item
# A sentence that names a physical link is talking about the map, not a timetable,
# even when it contains the word "closed": "the link to Core D is closed" is an
# instruction, while "is the canteen closed on Sunday" is a question about hours.
_PASSAGE = re.compile(
    r"\b(passage|link|walkway|corridor|connection|route|entrance|exit)\b", re.I
)

_FACILITY = re.compile(
    r"\b(aed|defibrillators?|toilets?|washrooms?|restrooms?|bathrooms?|"
    r"water stations?|water fountains?|drinking water|refill stations?|"
    r"canteens?|cafeterias?|clinics?|atms?|banks?|post ?box(?:es)?|"
    r"lactation rooms?|nursing rooms?|prayer rooms?|book ?stores?|book ?shops?)\b",
    re.I,
)

# "what facilities are there / what can I find here" -> a facilities overview
_FACILITY_ALL = re.compile(
    r"\b(facilit(?:y|ies)|amenities|what places|what can i find|what is here|"
    r"what's here|what is available|what services)\b", re.I)

_RESET = re.compile(r"^\s*(reset|restart|start over|clear|new route)\s*$", re.I)
_HELP = re.compile(r"\b(help|what can you do|how do you work)\b", re.I)

_FILLER = {
    "the", "a", "an", "please", "now", "me", "campus", "polyu", "building",
    "from", "to", "at", "in", "on", "is", "it", "that", "this",
}


def _clean_place(text: str) -> str:
    """Drop filler words, but never the letter in a building name.

    "a", "an", "the" are filler -- except in "Core A", "Block A", "Wing A", where the
    single letter IS the place. Stripping it turned "Core A" into "Core", which matches
    eight buildings and forces a disambiguation question, so no route was ever drawn.
    """
    words = [w for w in re.split(r"[\s,.?!]+", text.strip()) if w]
    kept = []
    for index, word in enumerate(words):
        if word.lower() in _FILLER:
            previous = words[index - 1].lower() if index else ""
            if previous in {"core", "block", "wing", "tower"}:
                kept.append(word)          # "Core A" -- keep the A
            continue
        kept.append(word)
    return " ".join(kept or words).strip()


def refers_to_last_distance(text: str) -> bool:
    """An elliptical follow-up explicitly asks about the current trip."""
    route_text, _ = stair_request(text)
    return bool(_DISTANCE_FOLLOWUP.fullmatch(route_text.rstrip("？！。?!. ")))


_CAMPUS_TRADITIONAL = str.maketrans("图书馆赛马会创楼栋", "圖書館賽馬會創樓棟")


def map_spelling(text: str) -> str:
    """Normalize common campus names to the map's traditional Chinese aliases."""
    return re.sub(r"([A-Za-z]+)\s+(座|棟)", r"\1\2", text.translate(_CAMPUS_TRADITIONAL))


def _named_by(node: Node, phrase: str) -> bool:
    labels = (node.name, node.id, *node.aliases)
    if any(phrase == label.casefold() for label in labels):
        return True
    # Abbreviated Chinese place names, e.g. 圖書館 in 包玉剛圖書館.
    return bool(re.fullmatch(r"[\u3400-\u9fff]{2,}", phrase) and
                any(label.endswith(phrase) for label in labels))


def _route_pair(text: str, graph: CampusGraph) -> re.Match | None:
    pair = (_FROM_TO.search(text) or _AT_AND_GO.search(text) or _TO_FROM.search(text) or _DISTANCE_BETWEEN.search(text)
            or _ZH_TO_FROM.search(text) or _ZH_PAIR.search(text))
    chinese_clause = text.rstrip(" 。！？?!")
    if pair is None and _ZH_DISTANCE_END.search(chinese_clause):
        pair = _ZH_DISTANCE_FROM.match(_ZH_DISTANCE_END.sub("", chinese_clause).strip())
    if pair is None and not _ZH_TO.match(text):
        short = _ZH_SHORT_PAIR.match(text)
        if short and (_ZH_ENDING.search(chinese_clause) or _ZH_DISTANCE_END.search(chinese_clause)
                      or re.match(r"^(?:core|block)\s+\w+", text, re.I)):
            pair = short
        elif short:
            # Bare "图书馆到赛马会创新楼" has no other instruction words. Only
            # accept this shorthand if both sides are names on the campus map.
            names = (map_spelling(name).strip().casefold() for name in short.group("o", "d"))
            if all(any(_named_by(node, name) for node in graph.nodes.values()) for name in names):
                pair = short
    if pair is None and not _EN_COMMAND.search(text):
        pair = _PLACE_TO_PLACE.match(text)
    return pair


def _clean_endpoint(text: str) -> str:
    text = text.strip(" ，,。！？?!")
    text = _ZH_DISTANCE_END.sub("", _ZH_ENDING.sub("", text)).strip(" ，,。！？?!")
    return _clean_place(re.sub(r"出[發发]$", "", text).strip())


def names_destination(text: str, graph: CampusGraph) -> bool:
    """A directed request must not be mistaken for a bare clarification."""
    return bool(_TO_ONLY.search(text) or _ZH_TO.match(text.strip()) or _route_pair(text.strip(), graph))


def parse_with_grammar(text: str, graph: CampusGraph) -> Slots:
    lowered, avoid_stairs = stair_request(text)

    facility = _chinese_facility(lowered, graph)
    if facility:
        facility.avoid_stairs = avoid_stairs
        return facility

    if _RESET.match(lowered):
        return Slots(intent="reset", source="grammar")
    if _HELP.search(lowered) and not names_destination(lowered, graph):
        return Slots(intent="help", source="grammar")
    if REOPEN.search(lowered):
        return Slots(intent="close", closed_text=lowered.strip(), source="grammar")
    if refers_to_last_distance(lowered):
        return Slots(intent="distance", avoid_stairs=avoid_stairs, source="grammar",
                     language="zh" if re.search(r"[\u4e00-\u9fff]", lowered) else "en")

    origin_text = destination_text = ""
    # "explicit route" means the sentence really said from/to. A bare place name
    # ("is the 7-Eleven open") must not be read as a route request.
    explicit_route = False
    m = _route_pair(lowered, graph)
    if m:
        origin_text = _clean_endpoint(m.group("o"))
        destination_text = _clean_endpoint(m.group("d"))
        explicit_route = True
    else:
        m2 = _ZH_TO.match(lowered) or _TO_ONLY.search(lowered)
        if m2:
            cand = _clean_endpoint(m2.group("d"))
            if m2.re is _ZH_TO or _DEST_REQUEST.search(lowered) or graph.resolve(cand):
                destination_text = cand
                explicit_route = True

    # "How far is JO?" still names a new destination, even if unknown.
    if not m and not destination_text:
        subject = _DISTANCE_SUBJECT.match(lowered)
        if subject:
            destination_text = _clean_endpoint(subject.group("d"))

    # A place mentioned without "to/from" (e.g. "the library?") still counts.
    if not destination_text:
        hits = graph.resolve(lowered)
        if len(hits) == 1:
            destination_text = hits[0].name

    closed_text = lowered.strip() if _CLOSE.search(lowered) else ""

    item = ""
    mi = _ITEM.search(lowered)
    if mi:
        item = mi.group(1).lower()
    else:
        mu = _ITEM_UNKNOWN.search(lowered)
        if mu:
            item = mu.group(1).lower().strip()

    facility_hit = _FACILITY.search(lowered)
    wants_hours = bool(_HOURS_EN.search(lowered) or _HOURS_ZH.search(lowered))
    # "the link to Core D is closed" and "the library is closed today" are reports
    # about the map; "is the canteen closed on Sunday?" and "what time does the
    # bookshop close" are questions about a timetable. Two signals separate them:
    # naming a physical link, and whether the sentence is phrased as a question.
    asked_as_question = text.strip().endswith("?")
    closed_adjective = re.search(r"\bclosed\b", lowered) is not None
    if closed_adjective and (_PASSAGE.search(lowered) or not asked_as_question):
        wants_hours = False
    # "how do I get to X" outranks "is X open": a route request is never a timetable
    # request, even when the sentence happens to contain the word "open".
    asks_for_route = explicit_route

    asks_for_hours = re.match(r"^(?:what time|when|is|are)\b", lowered, re.I)
    seeks_nearest = re.match(
        r"^(?:where|which|what|find|show|locate)\b.*?\b(?:closest|nearest)\b", lowered, re.I,
    )
    asks_about_recycling = re.match(
        r"^where (?:can|should|do) (?:i|we) (?:recycle|dispose|throw|dump)\b", lowered, re.I,
    )
    if wants_hours and (not asks_for_route or asks_for_hours):
        intent = "hours_query"
        origin_text = destination_text = ""   # a timetable question is not a route
    elif closed_text and not wants_hours:
        intent = "close"
    elif asks_about_recycling:
        intent = "green_query"
    elif m and not (facility_hit and seeks_nearest):
        intent = "distance" if _HOW_FAR.search(lowered) else "route"
    elif facility_hit and not _ITEM.search(lowered):
        intent = "facility_query"
    elif _FACILITY_ALL.search(lowered) and not (origin_text or destination_text):
        intent = "facility_query"   # no subject -> the overview reply lists what exists
    elif item or (_GREEN.search(lowered) and not (origin_text or destination_text)):
        intent = "green_query"
    elif _HOW_FAR.search(lowered):
        intent = "distance"
    elif origin_text or destination_text or avoid_stairs is not None:
        intent = "route"
    else:
        intent = "out_of_scope"

    subject = ""
    if intent in ("facility_query", "hours_query"):
        if not item and facility_hit:
            item = facility_hit.group(1).lower()
        subject = text.strip()   # the knowledge base searches the user's own words

    return Slots(
        intent=intent,
        origin_text=origin_text,
        destination_text=destination_text,
        closed_text=closed_text,
        item=item,
        subject=subject,
        language="zh" if re.search(r"[\u4e00-\u9fff]", text) else "en",
        avoid_stairs=avoid_stairs,
        source="grammar",
    )


def parse(text: str, graph: CampusGraph, client: OpenRouterClient | None) -> Slots:
    """Ask the model first, retaining explicitly named endpoints when omitted."""
    route_text, avoid_stairs = stair_request(text)
    if client is not None and client.settings.llm_available:
        facility = _chinese_facility(route_text, graph)
        if facility:
            facility.avoid_stairs = avoid_stairs
            return facility
    if (not route_text or client is None or not client.settings.llm_available
            or refers_to_last_distance(route_text)):
        return parse_with_grammar(text, graph)
    llm = parse_with_llm(route_text, graph, client)
    if llm is None:
        return parse_with_grammar(text, graph)
    llm.avoid_stairs = avoid_stairs
    if avoid_stairs is not None and llm.intent == "out_of_scope":
        return parse_with_grammar(text, graph)

    if names_destination(route_text, graph):
        grammar = parse_with_grammar(text, graph)
        if grammar.intent in ("route", "distance"):
            if grammar.origin_text and grammar.destination_text:
                # The user's word order determines the roles. Accept a model's
                # canonical ID only in the slot whose words actually name it.
                for field in ("origin_text", "destination_text"):
                    phrase = getattr(grammar, field)
                    value = getattr(llm, field)
                    node = graph.nodes.get(value)
                    if node and _named_by(node, map_spelling(phrase).casefold()):
                        setattr(grammar, field, value)
                if (llm.intent == grammar.intent and not llm.closed_text
                        and llm.origin_text == grammar.origin_text
                        and llm.destination_text == grammar.destination_text):
                    return llm
                return grammar
            if llm.intent == "out_of_scope":
                return grammar
            if llm.intent in ("route", "distance"):
                llm.origin_text = llm.origin_text or grammar.origin_text
                llm.destination_text = llm.destination_text or grammar.destination_text
    return llm

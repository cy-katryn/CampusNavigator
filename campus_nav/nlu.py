"""Turn a sentence into slots.

Two parsers share one output type, and `parse` runs them in this order:

  * `parse_with_llm`     -- asks the model for strict JSON, then validates it. This
                           is the primary parser: it reads any phrasing, not only
                           the sentence shapes somebody enumerated. Every failure
                           -- no key, timeout, bad JSON, unknown intent, an
                           endpoint the resolver cannot name -- returns None and
                           the grammar answers instead.
  * `parse_with_grammar` -- deterministic regex rules. The fallback: the mode the
                           app runs in with no key and no network, and the safety
                           net when a model turn is unusable.

The LLM never decides a route and never invents a place: it reports what the user
said, and `_validated()` accepts a place phrase only if the resolver recognises it.
That boundary is enforced in code, not requested in a prompt.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from . import lexical
from .graph import CampusGraph
from .llm import DeepSeekClient

# The resolver every grammar rule consults: PlaceIndex.resolve(text) -> list[Node].
# It is passed in rather than imported so the rules stay testable without a network.
PlaceResolver = Any

INTENTS = (
    "route",          # find a way from A to B
    "distance",       # how far is A from B
    "close",          # this link or place is closed

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
  "via": a place to walk through on the way, or null,
  "closed": the place or link the user says is closed, as free text, or null,
  "item": the facility this question needs, from the list below, or null,
  "language": "en" or "zh"
}}

The facilities this map carries (use these words for "item"):
{facilities}

Rules:
- Never invent a place id that is not in the list. If the user names a place that is
  not listed, put their words there verbatim ("destination": "the 7-Eleven") and keep
  the intent -- the app resolves names itself, and it knows places this short list
  leaves out. Never answer null for a place the user actually named: that silently
  turns their question into "where do you want to go?".
- "is the canteen open now", "opening hours of the clinic" -> intent "hours_query".
- "where is the nearest AED / toilet / water station" -> intent "facility_query"
  with item set to that facility ("aed", "toilet", "water station").
- A need rather than a facility name still belongs in "item": map it to the closest
  amenity in the list above. Examples: "art supplies", "a printer", "printing",
  "photocopy", "paper", "stationery" -> bookstore; "cash", "money", "withdraw" -> bank; "something to eat", "lunch" ->
  catering outlets; "a doctor", "medicine", "feel unwell" -> clinics; "a room to pray"
  -> prayer room; "fill my bottle", "drinking water" -> water station; "a bus" ->
  bus stop. Use the label exactly as written in the list.
  Never answer a need with a housekeeping label; pick the amenity a person would walk to.
  If no amenity in the list is a reasonable answer, leave "item" null.
- "from A, pass by C, then B" -> origin A, via C, destination B.
- Output JSON only, no prose, no code fences."""


@dataclass
class Slots:
    intent: str = "out_of_scope"
    origin_text: str = ""
    destination_text: str = ""
    closed_text: str = ""    # the raw words around a closure ("the library is closed")
    item: str = ""
    via_text: str = ""     # a place to walk through on the way ("pass by Core P")
    subject: str = ""      # the raw words the question is *about* (hours/facility queries)
    language: str = "en"
    source: str = "grammar"
    avoid_stairs: bool | None = None  # None keeps the current route preference
    facility_origin: str = ""
    facility_target: str = ""
    facility_choice: int | None = None  # zero-based position in the last displayed list

    @property
    def mentions_places(self) -> bool:
        return bool(self.origin_text or self.destination_text)


# Labels that answer "where can I get X". The directory labels are excluded: offered as
# options, a model asked for cash answers "office", which is true of 45 places and useful
# for none. Everything else the map publishes is an amenity somebody would walk to.
_DIRECTORY_LABELS = frozenset({
    "office", "faculty / department", "lecture theatre", "building", "sports facilities",
    "facility", "",
})


def _facility_words(graph: CampusGraph) -> list[str]:
    """The amenity labels this map actually carries, for the prompt.

    Read from the graph rather than written into the prompt, so a facility the map does
    not have can never be offered to the model as an option.
    """
    labels = {
        node.service.strip().lower() for node in graph.nodes.values()
        if node.kind == "facility" and node.service
    }
    amenities = labels - _DIRECTORY_LABELS
    return sorted(amenities or labels)


def _place_catalogue(graph: CampusGraph, limit: int = 90) -> str:
    """The places the model is shown, in the order it is most likely to need them.

    Every building is listed, plus ONE representative of each service type: 192 nodes
    would blow the prompt budget, and the model does not need the id of one particular
    water station to understand "where can I fill my bottle".
    """
    buildings = [
        n for n in graph.nodes.values()
        if n.kind not in ("facility", "waypoint") and n.name
    ]
    representatives: dict[str, object] = {}
    for node in graph.nodes.values():
        if node.kind == "facility" and node.service not in representatives:
            representatives[node.service] = node

    rows = []
    for node in buildings[:limit]:
        extra = f" ({node.name_zh})" if node.name_zh else ""
        rows.append(f"- {node.id}: {node.name}{extra}")
    for node in representatives.values():
        rows.append(f"- {node.id}: {node.name} [one of the '{node.service}' places]")
    return "\n".join(rows)


def parse_with_llm(text: str, graph: CampusGraph, client: DeepSeekClient) -> Slots | None:
    prompt = SYSTEM_PROMPT.format(
        places=_place_catalogue(graph),
        intents=", ".join(f'"{i}"' for i in INTENTS),
        facilities=", ".join(_facility_words(graph)),
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
        """Turn what the model reported into the words this app resolves places by.

        A real node id becomes that node's *name*, because the rest of the pipeline
        resolves place names through the index -- and an id is not a name. Embedding
        "POI_5bcd371552_67" lands between unrelated neighbours (it scored 0.59 for Core B
        and 0.55 for Block Z, while the id belonged to Pao Yue-Kong Library), so the
        model's correct answer arrived as "which Core B did you mean?".

        Free text is kept as written: it is resolved by name later, and an id the model
        invented matches no node and stays text. This is the boundary the module
        docstring describes -- the model may report a place, it may not create one.
        """
        if not isinstance(value, str) or not value.strip():
            return ""
        candidate = value.strip()
        node = graph.nodes.get(candidate)
        if node is not None and node.kind != "waypoint":
            return node.name
        return candidate

    closed = payload.get("closed")
    closed_text = closed.strip() if isinstance(closed, str) else ""

    return Slots(
        intent=intent,
        origin_text=as_id(payload.get("origin")),
        destination_text=as_id(payload.get("destination")),
        closed_text=closed_text,
        item=str(payload.get("item") or "").strip().lower(),
        via_text=as_id(payload.get("via")),
        subject=text.strip() if intent in ("hours_query", "facility_query") else "",
        language=str(payload.get("language") or "en"),
        source="llm",
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
# Every rule below is a *signal*: it answers one yes/no question about the sentence, and
# parse_with_grammar() decides the intent from those answers in a fixed order. Keeping
# the decision in one place is what stops a new phrasing from quietly changing an old
# one -- the order is the specification.

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
# A word list, not a pattern: the sentence opens with a command or a question. Used as
# a guard only -- "Core D to Block Z" is a route request, "the link to Core D" is not.
_COMMAND_WORDS = frozenset({
    "how", "where", "what", "when", "why", "which", "can", "could", "would", "is",
    "are", "i", "we", "please", "take", "go", "get", "walk", "head", "navigate",
    "direction", "directions", "route", "who", "will", "do", "does", "tell", "show",
    "need", "want", "to", "from", "at", "of", "in", "on", "and",
})
_COMMAND_PHRASES = ("the link",)
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

# "Is this a timetable question?" -- one signal, both languages. They were three
# patterns (English words, Chinese 幾點-class words, Chinese 開/關-class words) that were
# only ever tested for truth, and their union is what the decision actually used.
# Checked BEFORE the closure rule: "is the canteen closed on Sunday?" is a timetable
# question, while "the link to Core D is closed" is an instruction.
_HOURS = re.compile(
    r"\b(opening hours?|opening times?|business hours?|what time|which time|"
    r"until when|how late|when (?:is|does|do)\b[^?]*\b(?:open|close)|"
    r"(?:is|are)\b[^?]*\b(?:open|closed)(?![a-z])|still open|open (?:today|now|tonight|"
    r"on (?:mon|tue|wed|thu|fri|sat|sun))|close[sd]? at|hours? of)\b"
    r"|幾點|幾時|開放|營業|開門|關門|時間|關了|開了"
    r"|[開开](?:放|門|门|館|馆|到|咗|了|唔[開开]|不[開开])|[關关][門门館馆]"
    r"|[營营][業业]|[幾几][點点時时]|什[麼么][時时]候|收[幾几][點点]|有冇[開开]"
    r"|(?:有|[仲還还])[開开]|[開开][嗎吗未]",
    re.I,
)

# Where the question points, and how it is phrased. Named so that the decision in
# parse_with_grammar() reads as prose instead of an anonymous re.match().
_PASSAGE = re.compile(r"\b(passage|link|walkway|corridor|connection|route|entrance|exit)\b", re.I)
_ASKS_AS_HOURS = re.compile(r"^(?:what time|when|is|are)\b", re.I)
_SEEKS_NEAREST = re.compile(r"^(?:where|which|what|find|show|locate)\b.*?\b(?:closest|nearest)\b", re.I)
_CLOSED_WORD = re.compile(r"\bclosed\b", re.I)
_HAS_CJK = re.compile(r"[\u3400-\u9fff]")

# --- facility: which service does the question name, if any --------------------
# One pattern, not two. These are the words a person uses for the things this map
# carries; the captured group is the item the dialogue then looks up.


# --- meta: the two commands that are about the conversation, not the campus ------
_RESET = re.compile(r"^\s*(reset|restart|start over|clear|new route)\s*$", re.I)
_HELP = re.compile(r"\b(help|what can you do|how do you work)\b", re.I)

# --- Chinese: the same eight intents, written differently -----------------------
# The lexicon is data: a service word (or several) per facility. Everything else the
# Chinese rules need is a small named pattern, gathered here rather than interleaved
# with the English ones.
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
}
_ZH_SERVICE = re.compile("|".join(f"(?P<s{i}>{words})" for i, words in enumerate(_ZH_SERVICES.values())), re.I)
_ZH_WHERE = re.compile(r"最近|附近|哪[裏里儿]|[哪邊边]度|[邊边]個|有[冇沒没]|在哪|有哪|[搵找]|有[咩乜]|有什[么麼]")
_ZH_OVERVIEW = re.compile(r"(?:有|列[出舉举]|看看|[顯显]示).*(?:[設设]施|[設设][備备])|[設设]施(?:列表|清[單单])")
_ZH_CHOICE = re.compile(r"第\s*([一二兩两三四五六七八九十1-9])\s*(?:[個个處处項项間间家]|座)|最近(?:的|嗰|那)[個个間间家]|[那嗰這这](?:一)?[個个間间家]")
_ZH_ROUTE_ACTION = re.compile(r"[帶带]我|怎[么麼]去|[點点]去|去第|去[那嗰]|去最近|[導导]航|怎[么麼]走|[點点]行")

# The shapes that carry no place of their own: a bare "where is the nearest one", a bare
# "what time", and the three ways a question says where it is measured from.
_ZH_NEAREST_ONLY = re.compile(r"最近(?:的|嘅|嗰個|那[个個])?(?:[喺在]?[哪邊边](?:[裡里裏度儿兒])?)[呀啊呢]?")
_ZH_TIME_ONLY = re.compile(r"(?:它|佢|[那咁]麼?|[那咁])?(?:的|嘅)?(?:[營营][業业]|[開开]放)?[時时][間间][呢呀啊]?")
_ZH_POINTS_AT_PLACE = re.compile(r"^(?:它|佢|嗰度|那里|那裡)")
_ZH_TIME_QUESTION = re.compile(
    r"(?:今日|今天|而家|現在|现在)?(?:[幾几][點点時时]|什[麼么][時时]候)?"
    r"(?:[開开關关][門门]?|[營营][業业]|[開开]放)[吗嗎呢呀啊未]?"
)
_ZH_REFERENCE_PATTERNS = (
    re.compile(r"(?:距[離离]|[離离])\s*(.+?)\s*最近"),
    re.compile(r"^我(?:而家|依家|現在|现在)?[喺在]\s*([^，,。]+?)(?:[，,。]|附近|最近)"),
    re.compile(r"^(.+?)(?:附近|旁[边邊])"),
)
_ZH_IS_HERE = re.compile(
    r"我(?:[這这哪呢]?[裡里裏度邊边兒儿]|所[在處处]|(?:目前|[現现]在)?的位置)?|"
    r"[當当]前位置|[這这呢][裡里裏度兒儿]|身[邊边]"
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

def _clean_place(text: str) -> str:
    """Drop the verb and the filler, but never the letter in a building name.

    "a", "an", "the" are filler -- except in "Core A", "Block A", "Wing A", where the
    single letter IS the place. Stripping it turned "Core A" into "Core", which matches
    eight buildings and forces a disambiguation question, so no route was ever drawn.

    The filler list itself lives in campus_nav/lexical.py: the resolver and the parser
    have to agree on what a phrase is about, and when they each had their own list a
    closure sentence resolved its place to nothing.
    """
    # "go to Block Y" -> "Block Y": a verb in front of the place is not part of its name
    text = re.sub(r"^(?:get|go|walk|head|take\s+me)\s+(?:to\s+)?", "", text.strip(), flags=re.I)
    return lexical.drop_filler(text)


def refers_to_last_distance(text: str) -> bool:
    """An elliptical follow-up explicitly asks about the current trip."""
    route_text, _ = stair_request(text)
    return bool(_DISTANCE_FOLLOWUP.fullmatch(route_text.rstrip("？！。?!. ")))


def opens_with_a_request(text: str) -> bool:
    """Does the sentence start with a command or question word?"""
    lowered = (text or "").lstrip().lower()
    if lowered.startswith(_COMMAND_PHRASES):
        return True
    words = re.findall(r"[a-z]+", lowered)
    return bool(words) and words[0] in _COMMAND_WORDS


# The shapes that name a pair, in priority order. Data, not a chain of ors: the order is
# the tie-break rule when a sentence fits two shapes, so it belongs somewhere readable.
_PAIR_PATTERNS = (_FROM_TO, _AT_AND_GO, _TO_FROM, _DISTANCE_BETWEEN, _ZH_TO_FROM, _ZH_PAIR)


def _route_pair(text: str, places: PlaceResolver) -> re.Match | None:
    pair = next((found for pattern in _PAIR_PATTERNS
                 if (found := pattern.search(text))), None)
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
            # accept this shorthand if both sides name a place in the index.
            if all(places.resolve(name) for name in short.group("o", "d")):
                pair = short
    if pair is None and not opens_with_a_request(text):
        pair = _PLACE_TO_PLACE.match(text)
    return pair


def _clean_endpoint(text: str) -> str:
    text = text.strip(" ，,。！？?!")
    text = _ZH_DISTANCE_END.sub("", _ZH_ENDING.sub("", text)).strip(" ，,。！？?!")
    return _clean_place(re.sub(r"出[發发]$", "", text).strip())


def names_destination(text: str, places: PlaceResolver) -> bool:
    """A directed request must not be mistaken for a bare clarification."""
    return bool(_TO_ONLY.search(text) or _ZH_TO.match(text.strip()) or _route_pair(text.strip(), places))


def _chinese_facility(text: str, places: PlaceResolver) -> Slots | None:
    if not _HAS_CJK.search(text):
        return None
    phrase = text.strip(" ?？!！.。，")
    match = _ZH_SERVICE.search(phrase)
    item = list(_ZH_SERVICES)[int(match.lastgroup[1:])] if match else ""
    choice = _ZH_CHOICE.search(phrase)
    hours = _HOURS.search(phrase)
    place_words = re.sub(r"^(?:[請请][問问](?:一下)?|[請请]|想[問问](?:一下)?)", "", phrase).strip()
    slots = Slots(language="zh", subject=phrase, source="grammar", item=item)
    if not item and _ZH_NEAREST_ONLY.fullmatch(phrase):
        slots.intent, slots.facility_choice = "facility_query", 0
        return slots
    if choice and not item:
        index = choice.group(1) or "一"
        slots.facility_choice = ("一二三四五六七八九十".index(index.replace("兩", "二").replace("两", "二"))
                                 if not index.isascii() else int(index) - 1)
        slots.intent = "hours_query" if hours else ("route" if _ZH_ROUTE_ACTION.search(phrase) else "facility_query")
        return slots
    if not item and _ZH_TIME_ONLY.fullmatch(phrase):
        slots.intent, slots.facility_choice = "hours_query", 0
        return slots
    if hours:
        slots.intent = "hours_query"
        if not item and (_ZH_POINTS_AT_PLACE.match(place_words)
                         or _ZH_TIME_QUESTION.fullmatch(place_words)):
            slots.facility_choice = 0
        slots.facility_target = place_words
        return slots
    explicit_route = names_destination(phrase, places)
    if explicit_route and not (_ZH_WHERE.search(phrase) and not _ZH_ROUTE_ACTION.search(phrase)):
        return None
    if item and (_ZH_WHERE.search(phrase) or not explicit_route):
        slots.intent = "facility_query"
        slots.facility_target = match.group()
        reference = next((found for pattern in _ZH_REFERENCE_PATTERNS
                          if (found := pattern.search(place_words))), None)
        if reference:
            slots.facility_origin = reference.group(1).strip(" ，,的嘅")
            if _ZH_IS_HERE.fullmatch(slots.facility_origin):
                slots.facility_origin = ""
        return slots
    if _ZH_OVERVIEW.search(phrase):
        slots.intent = "facility_query"
        return slots
    return None
def parse_with_grammar(text: str, places: PlaceResolver) -> Slots:
    lowered, avoid_stairs = stair_request(text)

    facility = _chinese_facility(lowered, places)
    if facility:
        facility.avoid_stairs = avoid_stairs
        return facility

    if _RESET.match(lowered):
        return Slots(intent="reset", source="grammar")
    if _HELP.search(lowered) and not names_destination(lowered, places):
        return Slots(intent="help", source="grammar")
    if REOPEN.search(lowered):
        return Slots(intent="close", closed_text=lowered.strip(), source="grammar")
    if refers_to_last_distance(lowered):
        return Slots(intent="distance", avoid_stairs=avoid_stairs, source="grammar",
                     language="zh" if _HAS_CJK.search(lowered) else "en")

    origin_text = destination_text = ""
    # A stop ("pass by Core P") is read by the model, not by these patterns: cutting a
    # clause out of the sentence moved the endpoints ("Block X" became the destination)
    # far more often than it helped. The grammar keeps its two-slot contract.
    # "explicit route" means the sentence really said from/to. A bare place name
    # ("is the 7-Eleven open") must not be read as a route request.
    explicit_route = False
    m = _route_pair(lowered, places)
    if m:
        origin_text = _clean_endpoint(m.group("o"))
        destination_text = _clean_endpoint(m.group("d"))
        explicit_route = True
    else:
        m2 = _ZH_TO.match(lowered) or _TO_ONLY.search(lowered)
        if m2:
            cand = _clean_endpoint(m2.group("d"))
            if m2.re is _ZH_TO or _DEST_REQUEST.search(lowered) or places.resolve(cand):
                destination_text = cand
                explicit_route = True

    # "How far is JO?" still names a new destination, even if unknown.
    if not m and not destination_text:
        subject = _DISTANCE_SUBJECT.match(lowered)
        if subject:
            destination_text = _clean_endpoint(subject.group("d"))

    # A place mentioned without "to/from" (e.g. "the library?") still counts.
    if not destination_text:
        hits = places.resolve(lowered)
        if len(hits) == 1:
            destination_text = hits[0].name

    closed_text = lowered.strip() if _CLOSE.search(lowered) else ""

    facility_hit = _FACILITY.search(lowered)
    item = facility_hit.group(1).lower() if facility_hit else ""
    wants_hours = bool(_HOURS.search(lowered))
    # "the link to Core D is closed" and "the library is closed today" are reports
    # about the map; "is the canteen closed on Sunday?" and "what time does the
    # bookshop close" are questions about a timetable. Two signals separate them:
    # naming a physical link, and whether the sentence is phrased as a question.
    asked_as_question = text.strip().endswith("?")
    closed_adjective = _CLOSED_WORD.search(lowered) is not None
    if closed_adjective and (_PASSAGE.search(lowered) or not asked_as_question):
        wants_hours = False
    # "how do I get to X" outranks "is X open": a route request is never a timetable
    # request, even when the sentence happens to contain the word "open".
    asks_for_route = explicit_route

    asks_for_hours = _ASKS_AS_HOURS.match(lowered)
    seeks_nearest = _SEEKS_NEAREST.match(lowered)
    if wants_hours and (not asks_for_route or asks_for_hours):
        intent = "hours_query"
        origin_text = destination_text = ""   # a timetable question is not a route
    elif closed_text and not wants_hours:
        intent = "close"
    elif asks_for_route and not (facility_hit and seeks_nearest):
        intent = "distance" if _HOW_FAR.search(lowered) else "route"
    elif facility_hit:
        intent = "facility_query"
    elif _FACILITY_ALL.search(lowered) and not (origin_text or destination_text):
        intent = "facility_query"   # no subject -> the overview reply lists what exists
    elif _HOW_FAR.search(lowered):
        intent = "distance"
    elif origin_text or destination_text or avoid_stairs is not None:
        intent = "route"
    else:
        intent = "out_of_scope"

    subject = ""
    if intent in ("facility_query", "hours_query"):
        subject = text.strip()   # the knowledge base searches the user's own words

    return Slots(
        intent=intent,
        origin_text=origin_text,
        destination_text=destination_text,
        closed_text=closed_text,
        item=item,
        subject=subject,
        language="zh" if _HAS_CJK.search(text) else "en",
        avoid_stairs=avoid_stairs,
        source="grammar",
    )


def _validated(slots: Slots, places: PlaceResolver) -> Slots | None:
    """Accept a model parse only if the map can back it up.

    Two cheap checks. The intent must be one this dialogue layer implements
    (parse_with_llm already filters that, so this is belt and braces). And every place
    phrase the model reports must be one the resolver recognises: the model may report
    what the user said, it may never create a place. An endpoint that resolves to
    nothing is dropped, and a route left with no endpoint is refused outright so the
    grammar gets its turn instead.
    """
    if slots.intent not in INTENTS:
        return None
    # A stop is optional: an unnameable one is dropped so the plain route still runs.
    if slots.via_text and not places.resolve(slots.via_text):
        slots.via_text = ""
    for name in ("origin_text", "destination_text"):
        phrase = getattr(slots, name)
        if not phrase:
            continue
        if places.resolve(phrase):
            continue
        setattr(slots, name, "")            # an unnameable endpoint is not evidence
        if slots.intent in ("route", "distance"):
            return None
    if slots.intent == "route" and not (slots.origin_text or slots.destination_text):
        return None
    return slots


def parse(text: str, graph: CampusGraph, client: DeepSeekClient | None,
          places: PlaceResolver) -> Slots:
    """Ask the model first; fall back to the deterministic grammar.

    The model is the parser whenever it is available, because it reads any phrasing and
    not only the sentence shapes somebody enumerated. Its answer is validated by
    _validated() before anything trusts it, and every failure path -- no key, timeout,
    bad JSON, unknown intent, unnameable endpoint -- ends in the same place: the
    grammar answers. A bad model turn therefore costs coverage, never correctness.
    """
    if client is not None and client.settings.llm_available:
        route_text, avoid_stairs = stair_request(text)
        candidate = parse_with_llm(route_text or text, graph, client)
        if candidate is not None:
            accepted = _validated(candidate, places)
            if accepted is not None:
                accepted.avoid_stairs = (
                    avoid_stairs if avoid_stairs is not None else accepted.avoid_stairs
                )
                return accepted
    return parse_with_grammar(text, places)

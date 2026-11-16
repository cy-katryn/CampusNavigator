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

from .graph import CampusGraph
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
        extra = f" ({node.name_zh})" if node.name_zh else ""
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
# "to X" needs an actual "to": a bare "^" alternative swallows whole sentences.
_TO_ONLY = re.compile(
    r"(?:\bto\s+|\b(?:get|go|walk|head|take\s+me)\s+to\s+)"
    r"(?P<d>[a-z0-9][\w &'\-/().]*?)(?:[?.!,]|$)",
    re.I,
)
# "Block Y to Block L": an origin and a destination with no "from" at all. Only used
# when the sentence starts with the origin, so "how do I get to X" is never read as a
# journey from "how do i get".
_A_TO_B = re.compile(
    r"^(?P<o>[a-z0-9][\w &'\-/().]*?)\s+\bto\s+"
    r"(?P<d>[a-z0-9][\w &'\-/().]*?)(?:[?.!,]|$)",
    re.I,
)
# A lead-in verb or question word means the left half is not a place.
_NOT_A_PLACE_START = re.compile(
    r"^(?:i|im|i'm|we|how|what|where|when|which|why|who|can|could|would|will|do|does|"
    r"is|are|please|get|go|walk|head|take|route|directions?|tell|show|need|want|"
    r"to|from|at|of|in|on|and|the|a|an)\b",
    re.I,
)
_HOW_FAR = re.compile(r"\bhow\s+far\b|\bdistance\b", re.I)
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


def known(graph: CampusGraph, text: str) -> bool:
    """Does the graph recognise this as a name, with at least whole-word confidence?"""
    return any(score >= 60 for score, _ in graph.resolve_scored(text))


def _clean_place(text: str) -> str:
    """Drop filler words, but never the letter in a building name.

    "a", "an", "the" are filler -- except in "Core A", "Block A", "Wing A", where the
    single letter IS the place. Stripping it turned "Core A" into "Core", which matches
    eight buildings and forces a disambiguation question, so no route was ever drawn.
    """
    # "go to Block Y" -> "Block Y": a verb in front of the place is not part of its name
    text = re.sub(r"^(?:get|go|walk|head|take\s+me)\s+(?:to\s+)?", "", text.strip(), flags=re.I)
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


def parse_with_grammar(text: str, graph: CampusGraph) -> Slots:
    lowered = text.strip()

    if _RESET.match(lowered):
        return Slots(intent="reset", source="grammar")
    if _HELP.search(lowered):
        return Slots(intent="help", source="grammar")
    if REOPEN.search(lowered):
        return Slots(intent="close", closed_text=lowered.strip(), source="grammar")

    origin_text = destination_text = ""
    # "explicit route" means the sentence really said from/to. A bare place name
    # ("is the 7-Eleven open") must not be read as a route request.
    explicit_route = False
    m = _FROM_TO.search(lowered)
    if m:
        origin_text = _clean_place(m.group("o"))
        destination_text = _clean_place(m.group("d"))
        explicit_route = True
    else:
        both = _A_TO_B.match(lowered)
        if both and not _NOT_A_PLACE_START.match(both.group("o").strip()):
            start = _clean_place(both.group("o"))
            end = _clean_place(both.group("d"))
            # Both halves have to be recognised by name, not merely matched loosely:
            # "to Taxi Drop-off Point to Core A" must not become a trip from "to".
            if known(graph, start) and known(graph, end):
                origin_text, destination_text = start, end
                explicit_route = True
        if not explicit_route:
            m2 = _TO_ONLY.search(lowered)
            if m2:
                cand = _clean_place(m2.group("d"))
                if graph.resolve(cand):
                    destination_text = cand
                    explicit_route = True

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

    if wants_hours and not asks_for_route:
        intent = "hours_query"
        origin_text = destination_text = ""   # a timetable question is not a route
    elif closed_text and not wants_hours:
        intent = "close"
    elif facility_hit and not _ITEM.search(lowered) and not asks_for_route:
        # "from toilet (block y) to the library" names a facility but is still a route:
        # an explicit from/to outranks the facility keyword.
        intent = "facility_query"
    elif _FACILITY_ALL.search(lowered) and not (origin_text or destination_text):
        intent = "facility_query"   # no subject -> the overview reply lists what exists
    elif item or (_GREEN.search(lowered) and not (origin_text or destination_text)):
        intent = "green_query"
    elif _HOW_FAR.search(lowered):
        intent = "distance"
    elif origin_text or destination_text:
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
        language="zh" if re.search(r"[\u4e00-\u9fff]", lowered) else "en",
        source="grammar",
    )


def parse(text: str, graph: CampusGraph, client: OpenRouterClient | None) -> Slots:
    """LLM first (if available), grammar as the guaranteed fallback."""
    if client is not None and client.settings.llm_available:
        llm = parse_with_llm(text, graph, client)
        if llm is not None:
            return llm
    return parse_with_grammar(text, graph)


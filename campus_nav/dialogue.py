"""Multi-turn dialogue: remembers places, accumulates constraints, explains failures.

The behaviours below are the ones the marking scheme actually looks at, so they
are implemented explicitly rather than left to the language model:

  * destination known, origin missing      -> ask for the origin, keep destination
  * "the link between C and D is closed"    -> close it, re-route, report the delta
  * "the library is closed"                 -> close the place, re-route, offer options
  * no path exists                         -> name every blocking constraint and offer
                                              concrete ways forward, never a blank refusal
  * off-domain question                    -> say what the bot does cover
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .config import RAW_DIR
from .graph import CampusGraph, Node
from .kb import KnowledgeBase, hours_summary, open_state, parse_slots, today_hours
from .llm import OpenRouterClient
from .narrate import Facts, Narration, Narrator
from .nlu import REOPEN, Slots, parse
from .places import (
    SERVICE_WORDS,
    canonical_category,
    facility_capability,
    human_category,
)
from .search import RouteResult, compare, search

@dataclass
class Reply:
    text: str
    route: RouteResult | None = None
    slots: Slots | None = None
    notes: list[str] = field(default_factory=list)
    comparison: dict | None = None
    narrated: bool = False        # True when the model wrote the prose
    # Structured places the reply is talking about, so a map or a card view can
    # render them without re-parsing the prose.
    facilities: list[dict] = field(default_factory=list)
    # Every route worth offering, cheapest first. `route` is routes[0]; the rest are
    # real alternatives the user can pick between.
    routes: list[RouteResult] = field(default_factory=list)
    # What a text-to-speech engine should say. Set explicitly by the handler, because
    # reading the printed table aloud is unlistenable and guessing here would let the
    # audio drift from the screen.
    spoken: str = ""


@dataclass
class SessionState:
    origin: str | None = None
    destination: str | None = None
    closed_edges: set[str] = field(default_factory=set)
    closed_nodes: set[str] = field(default_factory=set)
    last_route: RouteResult | None = None
    pending: str | None = None  # "origin" | "destination" | None

    def remember(self, route: RouteResult) -> None:
        if route.reachable and route.path:
            self.origin = route.path[0]
            self.destination = route.path[-1]
            self.last_route = route


def fmt_route(graph: CampusGraph, route: RouteResult) -> str:
    """The route as a person reads it: legs between PLACES.

    The walkway network contributes junctions, and a junction has no name. Listing
    fifteen anonymous hops tells the reader nothing, so hops through junctions are
    folded into the leg that leads to the next real place.
    """
    if not route.reachable:
        return "No route found."

    def is_junction(node_id: str) -> bool:
        return graph.node(node_id).kind == "waypoint"

    def label(node_id: str) -> str:
        return graph.node(node_id).name or "the walkway"

    places = [node for node in route.path if not is_junction(node)]
    lines = [f"Route: {' -> '.join(label(node) for node in places)}"]

    last = route.path[0]
    carried = 0.0
    for seg in route.segments:
        carried += seg.metres
        if is_junction(seg.v):
            continue
        steps = " (steps)" if seg.stairs else ""
        lines.append(f"  - {label(last)} -> {label(seg.v)}: {carried:.0f} m{steps}")
        last = seg.v
        carried = 0.0

    stats = route.stats
    flights = sum(1 for seg in route.segments if seg.stairs)
    legs = max(len(places) - 1, 1)
    extras = (
        f", {flights} flight{'s' if flights != 1 else ''} of steps" if flights else ""
    )
    lines.append(
        f"Total: {route.total_metres:.0f} m over {legs} leg{'s' if legs != 1 else ''}{extras}"
    )
    if stats:
        lines.append(
            f"Search ({stats.algorithm}): expanded {stats.nodes_expanded} nodes, "
            f"peak frontier {stats.frontier_peak}, {stats.elapsed_ms:.2f} ms, "
            f"heuristic={stats.heuristic}"
        )
    return "\n".join(lines)


# Words that mean "can I get there without using stairs?" -- a question this map
# deliberately cannot answer, so it must answer it honestly rather than ignore it.
_STEP_FREE = re.compile(
    r"\b(stairs?|stairway|steps|step[- ]free|wheelchair|barrier[- ]free|"
    r"lift|lifts|elevator|escalator|accessible route|trolley)\b",
    re.I,
)


class Dialogue:
    def __init__(self, graph: CampusGraph, client: OpenRouterClient | None = None,
                 narrator: Narrator | None = None,
                 knowledge: KnowledgeBase | None = None) -> None:
        self.graph = graph
        self.client = client
        self.narrator = narrator
        self.state = SessionState()
        # Built lazily: 876 documents cost about a tenth of a second, which is too
        # much to spend on a /help or /quit, and nothing for a real question.
        self._knowledge = knowledge

    @property
    def knowledge(self) -> KnowledgeBase | None:
        """The facts the bot may state: opening hours and facility details."""
        if self._knowledge is None:
            try:
                self._knowledge = KnowledgeBase.from_raw(RAW_DIR, self.graph)
            except (OSError, ValueError, KeyError):
                # A missing or unreadable raw file must never break routing.
                self._knowledge = KnowledgeBase([])
        return self._knowledge

    # -- public ------------------------------------------------------------
    def handle(self, text: str) -> Reply:
        slots = parse(text, self.graph, self.client)
        intent = slots.intent

        if intent == "help":
            return self._help()
        if intent == "reset":
            self.state = SessionState()
            return Reply("Cleared. Where would you like to go?")

        if intent == "green_query":
            return self._facilities(slots)
        if intent == "facility_query":
            return self._facilities(slots)
        if intent == "hours_query":
            return self._hours(slots)

        # If the previous turn left a question open (e.g. "where are you starting
        # from?"), a bare place name is the answer to that question.
        if self.state.pending:
            hits = self._preferred_matches(text)
            if len(hits) == 1:
                if self.state.pending == "origin":
                    slots.origin_text, slots.destination_text = text, ""
                else:
                    slots.destination_text, slots.origin_text = text, ""
                intent = "route"
                self.state.pending = None

        if intent == "close" or slots.closed_text:
            closed_note = self._close(slots.closed_text)
            if not (self.state.origin and self.state.destination):
                return Reply(
                    f"{closed_note}\nTell me your start and destination and I will check "
                    "whether a route still exists.",
                    slots=slots,
                )
            return self._route_reply(slots, prefix=closed_note)

        if intent in ("route", "distance"):
            return self._route_reply(slots)

        # Somebody will ask about stairs, lifts or wheelchair access at Open Day. Two
        # thirds of that answer is now known and one third is not, so say exactly
        # which: outdoor flights of steps ARE mapped (OpenStreetMap tags them), lifts
        # and indoor stairs are not, in any source this project can cite.
        if _STEP_FREE.search(text):
            return Reply(
                "I mark flights of steps -- they are drawn as rungs on the map, and a "
                "route that uses one says so. What I do not have is lifts or the stairs "
                "inside a building, so I cannot promise a step-free route.\n"
                "What I can do: find the nearest accessible toilet, water station or AED, "
                "quote opening hours, and re-plan if you tell me a link is closed.",
                slots=slots,
            )

        return Reply(
            "I can find walking routes on the PolyU campus, point you to facilities "
            "(AEDs, water, accessible toilets, recycling) and quote their opening hours. "
            "Try: \"how do I get from the entrance to the library?\" or "
            "\"where can I recycle batteries?\"",
            slots=slots,
        )

    # -- pieces ------------------------------------------------------------
    def _help(self) -> Reply:
        return Reply(
            "I can do three things:\n"
            "  1. Route between campus places, e.g. \"from the entrance to the library\"\n"
            "  2. Re-plan around closures: \"the link between Core C and Core D is closed\"\n"
            "  3. Find facilities and their opening hours: \"where do I recycle glass bottles?\"\n"
            "Ask in English or Chinese. If I do not know something, I will say so.",
            notes=[f"graph: {self.graph.summary()}"],
        )

    def _preferred_matches(self, text: str) -> list[Node]:
        """Resolve a place name, preferring buildings over the bins inside them.

        "library" matches both Pao Yue-kong Library and the four facilities in it;
        "battery bin" should pick the facility. SERVICE_WORDS decides which.
        """
        matches = self.graph.resolve(text)
        if not matches:
            return []
        # A child is not an alternative to its own parent: ""the library"" must not
        # offer ""Entrance (Pao Yue-kong Library)"" as a second choice just because the
        # building's name is part of the child's grouped name.
        ids = {n.id for n in matches}
        grouped = [n for n in matches if not (n.parent and n.parent in ids)]
        if grouped:
            matches = grouped
        # An exact name match wins outright. "Core A" also matches the building whose
        # address alias is "Core A (G/F)", and asking the user which of the two they
        # meant is a dead end -- they typed the name of the first one.
        needle = " ".join((text or "").lower().split())
        exact = [n for n in matches if n.name.lower() == needle]
        if exact:
            return exact
        # "Block L" is a prefix of "Block L (Pao Yue-Kong Library)" but only of that
        # one -- "Block M" and "Block N" share the word "block", not the phrase.
        prefix = [
            n for n in matches
            if re.match(rf"^{re.escape(needle)}(?![a-z0-9])", n.name.lower())
        ]
        if prefix:
            return prefix
        wants_service = any(w in text.lower() for w in SERVICE_WORDS)
        if wants_service:
            return [n for n in matches if n.kind == "service"] or matches
        return [n for n in matches if n.kind != "service"] or matches

    def _resolve_or_ask(self, slots: Slots, previous: str | None, role: str) -> tuple[str | None, Reply | None]:
        """Resolve a place mention, or produce the clarifying question."""
        text = slots.origin_text if role == "origin" else slots.destination_text
        if not text:
            if previous:
                return previous, None
            self.state.pending = role
            other = "destination" if role == "origin" else "origin"
            known = self.graph.node(previous).name if previous else None
            if known and role == "origin":
                return None, Reply(
                    f"Where are you starting from? I will take you to {known}."
                )
            if known and role == "destination":
                return None, Reply(f"Where would you like to go from {known}?")
            return None, Reply(f"Where is your {role}? (e.g. \"the entrance\", \"the library\")")

        preferred = self._preferred_matches(text)
        if not preferred:
            self.state.pending = role
            sample = ", ".join(n.name for n in list(self.graph.nodes.values())[:5])
            return None, Reply(
                f"I do not know \"{text}\". Known places include: {sample}. "
                f"Which {role} do you mean?"
            )
        if len(preferred) > 1:
            names = ", ".join(n.name for n in preferred[:4])
            return None, Reply(f"\"{text}\" could be several places: {names}. Which one?")
        return preferred[0].id, None

    def _close(self, closed_text: str) -> str:
        """Close something the user says is closed: a named link, or a whole place.

        Three ways to be understood, in order of precision:
          1. an edge id            "close E123"
          2. two places            "the link between Core C and Core D is closed"
          3. one place             "the library is closed today"
        Anything vaguer gets a question instead of a guess -- closing the wrong
        part of the map would silently invalidate every later answer.
        """
        text = (closed_text or "").strip()
        if not text:
            return "I could not tell what is closed, so nothing changed."

        # 0) "re-open it" -- the escape hatch from a dead end
        if REOPEN.search(text):
            had = len(self.state.closed_edges) + len(self.state.closed_nodes)
            self.state.closed_edges.clear()
            self.state.closed_nodes.clear()
            if not had:
                return "Nothing is closed, so there was nothing to re-open."
            return "Re-opened everything that was closed."

        # 1) an explicit edge id, exactly as the map tooltip shows it
        for edge in self.graph.edges:
            if edge.id.lower() in text.lower():
                self.state.closed_edges.add(edge.id)
                return (
                    f"Closed link {edge.id} "
                    f"({self.graph.node(edge.u).name} - {self.graph.node(edge.v).name})."
                )

        # 2) two places -> the link that joins them
        places = [n for n in self._preferred_matches(text) if n.kind != "service"]
        if len(places) >= 2:
            ids = {
                e.id for e in self.graph.edges
                if {e.u, e.v} == {places[0].id, places[1].id}
            }
            if ids:
                self.state.closed_edges |= ids
                return (
                    f"Closed the link between {places[0].name} and {places[1].name} "
                    f"({', '.join(sorted(ids))})."
                )

        # 3) one place -> close the place itself
        if len(places) == 1:
            self.state.closed_nodes.add(places[0].id)
            return f"Closed {places[0].name}."

        return "I could not tell which place or link you mean, so nothing changed."

    def _absorb_places(self, slots: Slots) -> Reply | None:
        """Record any place named this turn. Returns a Reply only if we must ask.

        Both endpoints are absorbed before either is questioned, so a destination
        given in turn 1 survives a clarifying question about the origin.
        """
        for role, text in (("origin", slots.origin_text), ("destination", slots.destination_text)):
            if not text:
                continue
            preferred = self._preferred_matches(text)
            if len(preferred) == 1:
                setattr(self.state, role, preferred[0].id)
                continue
            self.state.pending = role
            if len(preferred) > 1:
                names = ", ".join(n.name for n in preferred[:4])
                return Reply(f"\"{text}\" could be several places: {names}. Which one?")
            sample = ", ".join(n.name for n in list(self.graph.nodes.values())[:5])
            return Reply(
                f"I do not know \"{text}\". Known places include: {sample}. "
                f"Which {role} do you mean?"
            )
        return None

    def _indoor_text(self, node_id: str) -> str:
        """What to say when the destination is inside a building.

        The route is drawn to the building because that is the last piece of geometry
        anyone published. The floor is the part that actually gets you to the thing.
        """
        node = self.graph.node(node_id)
        building = node.building or "the building"
        floor = f" on {node.floor}" if node.floor else ""
        return (
            f"{node.name} is inside {building}{floor}. The walk above ends at the "
            f"building -- I have no indoor route, so the last part is the entrance, "
            f"the lift and the floor number."
        )

    def _blocked(self) -> set[str]:
        """Closed places, plus the doors of anything closed.

        Closing a building has to close its entrances too, or "the library is closed"
        would leave every way into the library open.
        """
        blocked = set(self.state.closed_nodes)
        for node_id in list(blocked):
            blocked.update(self._doors_of(node_id))
        return blocked

    def _doors_of(self, node_id: str) -> list[str]:
        """The entrances grouped under a building."""
        return [
            node.id for node in self.graph.nodes.values()
            if node.parent == node_id and node.kind == "entrance"
        ]

    def _best_route(self, origin: str, destination: str, avoid: set[str] | None = None):
        """Route to the cheapest way IN, not to the middle of the building.

        Every door of the destination is a candidate goal. The shortest reachable
        route wins, so "take me to Shaw Amenities Building" arrives at whichever
        entrance is actually nearest from where you are. A building without doors
        falls back to its own anchor. `avoid` adds link ids to close for this search
        only, which is how the alternatives are generated.
        """
        doors = self._doors_of(destination)
        goals = doors if doors else [destination]
        blocked = self._blocked()
        closed_edges = set(self.state.closed_edges) | set(avoid or ())
        best = None
        for goal in goals:
            candidate = search(
                self.graph, origin, goal,
                algorithm="astar",
                closed_edges=closed_edges,
                closed_nodes=blocked,
            )
            if not candidate.reachable:
                continue
            if best is None or candidate.total_metres < best.total_metres:
                best = candidate
        if best is not None:
            return best
        # No door is reachable: report the building itself, so the reply still says
        # which closure is blocking the way rather than claiming the place vanished.
        return search(
            self.graph, origin, destination,
            algorithm="astar",
            closed_edges=closed_edges,
            closed_nodes=blocked,
        )

    def _route_options(self, origin: str, destination: str, limit: int = 3) -> list[RouteResult]:
        """The cheapest route, plus genuinely different ways of getting there.

        Each alternative is a real A* result with one leg of the best route closed for
        that search: no path is smoothed, spliced or invented, and a candidate that
        comes back identical is dropped because that leg was never the only way
        through. The longest leg goes first -- removing it changes the most.
        """
        primary = self._best_route(origin, destination)
        if not primary.reachable:
            return [primary]
        options = [primary]
        seen = {tuple(primary.path)}
        for segment in sorted(primary.segments, key=lambda s: s.metres, reverse=True):
            candidate = self._best_route(origin, destination, {segment.edge_id})
            if not candidate.reachable:
                continue
            key = tuple(candidate.path)
            if key in seen:
                continue
            seen.add(key)
            options.append(candidate)
            if len(options) >= limit:
                break
        return options

    def _describe_alternatives(self, routes: list[RouteResult]) -> str:
        """One line per alternative, in the text, for the terminal and for search."""
        lines = []
        for index, route in enumerate(routes[1:], start=2):
            places = [n for n in route.path if self.graph.node(n).kind != "waypoint"]
            legs = max(len(places) - 1, 1)
            flights = sum(1 for seg in route.segments if seg.stairs)
            bits = f"{route.total_metres:.0f} m over {legs} leg{'s' if legs != 1 else ''}"
            if flights:
                bits += f", {flights} flight{'s' if flights != 1 else ''} of steps"
            lines.append(f"  {index}. {bits}")
        count = len(lines)
        head = f"There {'is' if count == 1 else 'are'} {count} other way{'s' if count != 1 else ''} there:"
        return head + "\n" + "\n".join(lines)

    def _route_reply(self, slots: Slots, prefix: str = "") -> Reply:
        ask = self._absorb_places(slots)
        if ask:
            return Reply((prefix + "\n" if prefix else "") + ask.text, slots=slots)

        # Ask for whatever is still missing, naming the half we already know.
        if not self.state.destination and not self.state.origin:
            self.state.pending = "destination"
            return Reply(
                (prefix + "\n" if prefix else "")
                + "Where would you like to go? For example \"from the entrance to the library\".",
                slots=slots,
            )
        if not self.state.destination:
            self.state.pending = "destination"
            origin_name = self.graph.node(self.state.origin).name
            return Reply(
                (prefix + "\n" if prefix else "") + f"Where would you like to go from {origin_name}?",
                slots=slots,
            )
        if not self.state.origin:
            self.state.pending = "origin"
            dest_name = self.graph.node(self.state.destination).name
            return Reply(
                (prefix + "\n" if prefix else "")
                + f"Where are you starting from? I will take you to {dest_name}.",
                slots=slots,
            )

        self.state.pending = None
        origin, destination = self.state.origin, self.state.destination

        previous_total = self.state.last_route.total_metres if self.state.last_route else None

        # A building anchor sits at the polygon's centre, so a route to it ends with a
        # straight hop through its own wall. When the building has doors, the honest
        # destination is a door -- and A* decides which door is cheapest from here.
        # A facility inside a building is reached by walking to the building. The walk
        # ends there (or at its door) and the reply says which floor to look on -- we
        # have no indoor geometry, so pretending the last 40 m is a corridor would be
        # an invented route.
        indoor_note = ""
        goal = destination
        parent = self.graph.node(destination).parent
        if parent and parent in self.graph.nodes:
            indoor_note = self._indoor_text(destination)
            goal = parent
        routes = self._route_options(origin, goal)
        route = routes[0]

        notes: list[str] = []
        constraints = []
        if self.state.closed_edges:
            constraints.append(f"closed: {', '.join(sorted(self.state.closed_edges))}")
        if constraints:
            notes.append("Constraints: " + "; ".join(constraints))

        if not route.reachable:
            suggestions = self._suggestions(origin, destination)
            body = [
                f"I cannot find a route from {self.graph.node(origin).name} to "
                f"{self.graph.node(destination).name} with the current restrictions.",
                f"Blocking: {self._describe_blocking()}",
                "Options:",
            ]
            body += [f"  - {s}" for s in suggestions]
            body.append("Which would you like?")
            template = "\n".join(body)
            facts = self._facts_from_route(route, origin, destination, constraints, previous_total)
            narration = self._narrate(facts)
            text = (narration.text + "\n\n" + template) if narration.ok else template
            reply = Reply((prefix + "\n" if prefix else "") + text, route=route, slots=slots,
                          notes=notes, routes=routes)
            self._attach_narration(reply, narration)
            return reply

        text = fmt_route(self.graph, route)
        if len(routes) > 1:
            text += "\n" + self._describe_alternatives(routes)
        if indoor_note:
            text += "\n" + indoor_note
        if previous_total is not None:
            delta = route.total_metres - previous_total
            if abs(delta) >= 0.5:
                sign = "+" if delta > 0 else ""
                text += (
                    f"\nCompared with the previous route ({previous_total:.0f} m): "
                    f"{sign}{delta:.0f} m."
                )
        self.state.remember(route)
        comparison = None
        if slots.intent == "distance":
            comparison = compare(
                self.graph,
                origin,
                destination,
                closed_edges=self.state.closed_edges,
            )
            notes.append(
                f"A* expanded {comparison['astar']['expanded']} nodes vs UCS "
                f"{comparison['ucs']['expanded']} for the same {comparison['astar']['metres']:.0f} m route."
            )

        # Level B: the model phrases the answer, the calculation supplies the facts.
        facts = self._facts_from_route(route, origin, destination, constraints, previous_total)
        narration = self._narrate(facts)
        body = (narration.text + "\n\n" + text) if narration.ok else text
        reply = Reply((prefix + "\n" if prefix else "") + body, route=route, slots=slots,
                      notes=notes, comparison=comparison, routes=routes)
        self._attach_narration(reply, narration)
        return reply

    # -- narration helpers -------------------------------------------------
    def _facts_from_route(self, route, origin: str, destination: str,
                          constraints: list[str], previous_total: float | None) -> Facts:
        """Collect exactly what the model may talk about."""
        segments = [
            f"{self.graph.node(s.u).name} to {self.graph.node(s.v).name}: "
            f"{s.metres:.0f} m"
            for s in route.segments
        ]
        options, option_metres = [], []
        if not route.reachable:
            for suggestion in self._suggestions(origin, destination):
                options.append(suggestion)
                match = re.search(r"\((\d+)\s*m\)", suggestion)
                if match:
                    option_metres.append(float(match.group(1)))
        return Facts(
            kind="route" if route.reachable else "no_route",
            origin=self.graph.node(origin).name,
            destination=self.graph.node(destination).name,
            reachable=route.reachable,
            total_metres=route.total_metres if route.reachable else None,
            segment_lines=segments,
            segment_metres=[s.metres for s in route.segments],
            constraints=constraints,
            previous_metres=previous_total,
            blocking=[self._describe_blocking()] if not route.reachable else [],
            options=options,
            option_metres=option_metres,
        )

    def _narrate(self, facts: Facts) -> Narration:
        if self.narrator is None:
            return Narration(False, reason="no narrator configured")
        return self.narrator.narrate(facts)

    @staticmethod
    def _attach_narration(reply: "Reply", narration: Narration) -> None:
        reply.narrated = narration.ok
        if narration.ok:
            reply.notes.append(f"Narrated by the model ({narration.reason})")
        elif narration.reason not in {"no LLM configured", "no narrator configured"}:
            # a configured model that failed is worth reporting; an absent one is not
            reply.notes.append(f"Template reply used ({narration.reason})")

    def _describe_blocking(self) -> str:
        parts = []
        if self.state.closed_edges:
            parts.append(f"closed passages: {', '.join(sorted(self.state.closed_edges))}")
        if self.state.closed_nodes:
            parts.append(f"closed places: {', '.join(sorted(self.state.closed_nodes))}")
        return "; ".join(parts) or "no usable path in the demo map"

    def _suggestions(self, origin: str, destination: str) -> list[str]:
        """Priced ways out of a dead end. Every figure below is a real search."""
        out = []
        if self.state.closed_edges or self.state.closed_nodes:
            relaxed = search(
                self.graph, origin, destination,
                algorithm="astar", closed_edges=set(), closed_nodes=set(),
            )
            if relaxed.reachable:
                out.append(f"re-open what is closed ({relaxed.total_metres:.0f} m)")
            out.append("keep the closure and choose a different destination")
        if not out:
            out.append("choose a different destination")
        return out

    # -- places, facilities and published facts ---------------------------
    def _service_matches(self, slots: Slots, text: str | None = None) -> list[Node]:
        """Service nodes the question is about: 'aed', 'water station', 'batteries'."""
        raw = (slots.item or "").strip()
        if not raw and not text:
            return []
        words = [raw] if raw else []
        words += [w for w in SERVICE_WORDS if text and w in (text or "").lower()]

        wanted_items = {canonical_category(w) for w in words if w}
        wanted_caps = {facility_capability(w) for w in words if w}

        matches: list[Node] = []
        for node in self.graph.nodes.values():
            if node.kind != "service":
                continue
            if wanted_items & set(node.accepts) or wanted_caps & set(node.accepts):
                matches.append(node)
            elif any(cap and cap in node.service.lower() for cap in wanted_caps):
                matches.append(node)
        return matches

    @property
    def _entrance(self) -> str | None:
        for node in self.graph.nodes.values():
            if node.kind == "entrance":
                return node.id
        return None

    def _distance_from(self, node: Node) -> float | None:
        """Metres from where the user is standing.

        With no origin in the conversation yet, distances are measured from the Main
        Entrance, and the reply says so -- a number must never imply knowledge the
        bot does not have.
        """
        reference = (self.state.origin
                     or (self.state.last_route.path[0] if self.state.last_route else None)
                     or self._entrance)
        if not reference or reference not in self.graph.nodes:
            return None
        return self.graph.node(reference).distance_to(node)

    def _card(self, node: Node) -> dict:
        """Structured facts about one place, for the reply and for a map or a card view."""
        doc = self.knowledge.for_node(node.id) if self.knowledge else None
        hours = doc.hours if doc else {}
        is_open, why = open_state(hours) if hours else (None, "")
        metres = self._distance_from(node)
        return {
            "node_id": node.id,
            "name": node.name,
            "service": node.service,
            "building": node.building,
            "floor": node.floor,
            "lat": node.lat,
            "lng": node.lng,
            "accepts": list(node.accepts),
            "hours_today": today_hours(hours),
            "open_now": is_open,
            "open_reason": why,
            "hours_summary": hours_summary(hours),
            "address": (doc.fields.get("address") if doc else "") or "",
            "metres": round(metres) if metres is not None else None,
            "source": node.source,
        }

    def _facilities(self, slots: Slots) -> Reply:
        """'where do I recycle glass', 'where is the nearest AED', 'what is there'."""
        raw = (slots.item or "").strip().lower()
        wants_service = any(w in raw for w in SERVICE_WORDS) or bool(facility_capability(raw) != raw)

        if not raw:
            # No subject named: summarise what exists instead of dumping every bin.
            nodes = [n for n in self.graph.nodes.values() if n.kind == "service"]
            counts: dict[str, int] = {}
            for node in nodes:
                counts[node.service] = counts.get(node.service, 0) + 1
            lines = ["Places I can guide you to on this map:"]
            for name, count in sorted(counts.items(), key=lambda kv: -kv[1]):
                lines.append(f"  - {name} ({count})")
            lines.append(
                'Ask with an item or a facility, e.g. "where do I recycle batteries?", '
                '"where is the nearest AED?", "is the canteen open?".')
            return Reply("\n".join(lines), slots=slots)

        matching = self._service_matches(slots)
        if not matching:
            return self._dont_know(
                raw,
                why=("no facility on the map accepts that item"
                     if not wants_service else "I have no such facility on the map"))

        matching.sort(key=lambda n: (self._distance_from(n) is None,
                                     self._distance_from(n) or 0.0))
        cards = [self._card(n) for n in matching[:5]]
        label = human_category(canonical_category(raw)) if canonical_category(raw) else raw
        head = f"{len(matching)} place(s) for {label}:"
        lines = [head]
        for card in cards:
            bits = [card["name"]]
            where = ", ".join(b for b in (card["building"], card["floor"]) if b)
            if where:
                bits.append(where)
            if card["metres"] is not None:
                bits.append(f'{card["metres"]} m away')
            openness = ("open now" if card["open_now"] else
                        "closed now" if card["open_now"] is False else "")
            hours = card["hours_today"]
            if hours and openness:
                bits.append(f"{openness} ({hours})")
            lines.append("  - " + " - ".join(bits))
        if len(matching) > 5:
            lines.append(f"  ... and {len(matching) - 5} more.")
        if self.state.origin is None:
            lines.append("(Distances are measured from the Main Entrance until you tell me where you are.)")
        lines.append("Name one and I will draw the walking route.")
        best = cards[0]
        where = ", ".join(p for p in (best["building"], best["floor"]) if p)
        spoken = f'The nearest one is {best["name"]}' + (f" at {where}" if where else "")
        if best["metres"] is not None:
            spoken += f", about {best['metres']} metres away"
        spoken += "."
        if len(matching) > 1:
            spoken += f" There are {len(matching)} on the map; the others are on screen."
        return Reply("\n".join(lines), slots=slots, facilities=cards, spoken=spoken)

    def _hours(self, slots: Slots) -> Reply:
        """Opening hours. Quotes the published table, or says there is not one."""
        kb = self.knowledge
        if kb is None:
            return self._dont_know(slots.subject or slots.item, why="no knowledge base is loaded")

        # 1) the question names something the map can resolve
        matching = self._service_matches(slots, slots.subject)
        # 1a) ... and if it names one specific place ("the dental clinic"), retrieval
        #     picks that one out of the group before anything is printed.
        if len(matching) > 1 and slots.subject:
            for _score, doc in kb.search(slots.subject, k=6, min_score=1.0):
                narrowed = [n for n in matching if doc.node_id and n.id == doc.node_id]
                if narrowed:
                    matching = narrowed
                    break
        if len(matching) > 1:
            # prefer the candidates that actually publish a table
            documented = [n for n in matching if (kb.for_node(n.id) and kb.for_node(n.id).hours)]
            if documented:
                matching = documented
        if len(matching) > 1:
            matching.sort(key=lambda n: (self._distance_from(n) or 0.0))
            cards = [self._card(n) for n in matching[:5]]
            lines = [f"{len(matching)} places match; hours published for today:"]
            for card in cards:
                if card["hours_today"]:
                    hours = card["hours_today"]
                elif card["hours_summary"]:
                    hours = "no hours published for today (other days are)"
                else:
                    hours = "hours not published"
                lines.append(f'  - {card["name"]}: {hours}')
            lines.append("Which one do you mean?")
            first = cards[0]
            spoken = (
                f"{len(matching)} places match. The nearest is {first['name']}, "
                f"today {first['hours_today'] or 'closed'}. The rest are on screen."
            )
            return Reply("\n".join(lines), slots=slots, facilities=cards, spoken=spoken)

        if len(matching) == 1:
            node = matching[0]
            card = self._card(node)
            if card["hours_summary"]:
                text = (
                    f'{card["name"]}'
                    + (f' ({card["address"]})' if card["address"] else "")
                    + f':\n  {card["hours_summary"]}'
                    + f'\n  Right now: {card["open_reason"]}.'
                    + "\nSource: PolyU campus map. Hours change on public holidays, which"
                      " I do not have a calendar for."
                )
                reason = card["open_reason"]
                spoken = (
                    f'{card["name"]}. Today: {card["hours_today"] or "closed"}. '
                    f'{reason[:1].upper()}{reason[1:]}.'
                )
                return Reply(text, slots=slots, facilities=[card], spoken=spoken)
            return Reply(
                f'The campus map does not publish opening hours for {card["name"]}. '
                f'I can tell you where it is'
                + (f' ({card["address"]})' if card["address"] else "")
                + " and draw a route, but I will not guess its hours.",
                slots=slots, facilities=[card],
                spoken=f'I do not have opening hours for {card["name"]}. '
                       f'I can show you where it is and draw a route.')

        # 2) retrieval: the question may name a place the service index does not cover
        query = slots.subject or slots.item
        hits = kb.search(query, k=6, min_score=1.0) if query else []

        # If the question names a place the map knows, answer about THAT place.
        # "When does the library open?" must not be answered with whichever document
        # happened to score highest -- quoting a table that belongs to somewhere else
        # is worse than admitting the table does not exist.
        named = [node for node in self._preferred_matches(query)] if query else []
        if named:
            labels = [named[0].name.lower()]
            inner = named[0].name.split("(")[-1].rstrip(")").strip()
            if inner and inner.lower() != labels[0]:
                labels.append(inner.lower())
            narrowed = [
                (score, doc) for score, doc in hits
                if any(label in doc.title.lower() for label in labels)
            ]
            if narrowed:
                hits = narrowed
            elif hits:
                return Reply(
                    f'The campus map does not publish opening hours for "{named[0].name}". '
                    "I can tell you where it is or draw a route, but I will not guess its "
                    "hours.",
                    slots=slots,
                )

        for score, doc in hits:
            if doc.hours:
                _is_open, why = open_state(doc.hours)
                text = (
                    f'{doc.title}:\n  {hours_summary(doc.hours)}'
                    + f'\n  Right now: {why}.'
                    + "\nSource: PolyU campus map."
                )
                spoken = f'{doc.title}. Today: {today_hours(doc.hours) or "closed"}. {why[:1].upper()}{why[1:]}.'
                return Reply(text, slots=slots, spoken=spoken)
        if hits:
            doc = hits[0][1]
            return Reply(
                f'The campus map does not publish opening hours for "{doc.title}". '
                "I can give you its address or draw a route instead.",
                slots=slots,
            )
        return self._dont_know(query, why="nothing in my knowledge base matches that")

    def _dont_know(self, subject: str, why: str = "") -> Reply:
        """The honest answer. Names the gap and what the bot does cover."""
        coverage = self.knowledge.coverage() if self.knowledge else {}
        documents = coverage.get("documents", 0)
        with_hours = coverage.get("with_opening_hours", 0)
        lines = [
            f'I do not have enough information to answer that'
            + (f' ("{subject}")' if subject else "") + ".",
        ]
        if why:
            lines.append(f"Reason: {why}.")
        known = sorted({a for n in self.graph.nodes.values() for a in n.accepts})
        if known:
            lines.append(
                "What I do cover: walking routes between campus places, and these green "
                f"facilities: {', '.join(human_category(k) for k in known)}."
            )
        if documents:
            lines.append(
                f"My knowledge base holds {documents} records from the PolyU campus map and "
                f"GreenMap, but only {with_hours} of them publish opening hours."
            )
        lines.append("I would rather say so than invent an answer.")
        return Reply("\n".join(lines))

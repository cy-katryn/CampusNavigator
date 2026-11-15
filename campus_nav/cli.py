"""Interactive terminal demo.

    python -m campus_nav.cli               # chat
    python -m campus_nav.cli --demo        # run the 3-turn presentation script
    python -m campus_nav.cli --graph       # dump the loaded map

Slash commands inside the chat: /help  /graph  /compare  /state  /reset  /quit
"""
from __future__ import annotations

import sys

from .config import get_settings, graph_path, rel
from .dialogue import Dialogue
from .graph import CampusGraph
from .llm import OpenRouterClient
from .narrate import Narrator
from .search import compare

DEMO_SCRIPT = [
    "How do I get from the entrance to the library?",
    "The library is closed today.",
    "Re-open everything.",
]


def banner(settings, graph: CampusGraph, source_label: str) -> str:
    return (
        "=" * 70 + "\n"
        "  COMP4431 Group 21 - Campus Navigator\n"
        f"  map    : {graph.summary()}\n"
        f"  source : {source_label}\n"
        f"  parser : {settings.mode_label}\n"
        f"  model  : {settings.model}\n"
        "  type /help for commands, /quit to exit\n"
        + "=" * 70
    )


def print_reply(reply) -> None:
    print()
    print(reply.text)
    for note in reply.notes:
        print(f"  [{note}]")
    if getattr(reply, "narrated", False):
        print("  [prose: model | numbers: A*]")


def print_explain(reply, limit: int = 5) -> None:
    """Show the first few A* expansions: g, h and f for each."""
    route = reply.route
    if not route or not route.reachable or not route.explain:
        return
    parts = []
    for step in route.explain[:limit]:
        # a junction has no name: it is a bend in the walkway, not a place
        label = (step["name"] or "junction")[:18]
        if step["h"] is None:
            parts.append(f"{label}(g{step['g']:.0f})")
        else:
            parts.append(f"{label}(g{step['g']:.0f}/h{step['h']:.0f}/f{step['f']:.0f})")
    print("  [A* order] " + " -> ".join(parts))


def run_demo(dialogue: Dialogue) -> int:
    print("\n--- presentation script: one condition changes each turn ---")
    for index, line in enumerate(DEMO_SCRIPT, 1):
        print(f"\n[user {index}] {line}")
        reply = dialogue.handle(line)
        print_reply(reply)
        print_explain(reply)
    return 0


def dump_graph(graph: CampusGraph) -> int:
    print(graph.summary())
    for node in graph.nodes.values():
        tag = node.service or node.kind
        print(f"  {node.id:<22} {node.name[:46]:<46} {node.lat:.5f},{node.lng:.5f}  {tag}")
    print()
    for edge in graph.edges:
        state = "closed" if not edge.open else ("covered" if edge.covered else "")
        print(f"  {edge.id:<6} {edge.u:<22} {edge.v:<22} {edge.metres:>6.0f} m  {state}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    settings = get_settings()

    try:
        path = graph_path()
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    graph = CampusGraph.load(path)

    if "--graph" in argv:
        return dump_graph(graph)

    client = OpenRouterClient(settings) if settings.llm_available else None
    narrator = Narrator(client) if settings.narration_available else None
    dialogue = Dialogue(graph, client, narrator)

    print(banner(settings, graph, rel(path)))
    if not settings.llm_available:
        print("  note: no OPENROUTER_API_KEY found - running on the grammar parser only.\n")

    if "--demo" in argv:
        return run_demo(dialogue)

    while True:
        try:
            line = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line in {"/quit", "/exit", ":q"}:
            return 0
        if line == "/reset":
            dialogue.state = type(dialogue.state)()
            print("state cleared.")
            continue
        if line == "/state":
            s = dialogue.state
            print(f"  origin={s.origin} destination={s.destination} "
                  f"closed={sorted(s.closed_edges)}")
            continue
        if line == "/graph":
            print("  " + graph.summary())
            continue
        if line == "/compare":
            s = dialogue.state
            if not (s.origin and s.destination):
                print("  ask for a route first.")
                continue
            data = compare(graph, s.origin, s.destination,
                           closed_edges=s.closed_edges)
            print(f"  A* : {data['astar']['expanded']:>3} expanded, "
                  f"{data['astar']['metres']:.0f} m, {data['astar']['ms']:.2f} ms")
            print(f"  UCS: {data['ucs']['expanded']:>3} expanded, "
                  f"{data['ucs']['metres']:.0f} m, {data['ucs']['ms']:.2f} ms")
            print(f"  same cost: {data['same_cost']}  |  nodes saved by A*: {data['saving']}")
            continue

        reply = dialogue.handle(line)
        print_reply(reply)
        print_explain(reply)


if __name__ == "__main__":
    raise SystemExit(main())

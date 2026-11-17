"""COMP4431 Group 21 -- Campus Navigator.

A domain-specific chatbot that finds accessible walking routes across the PolyU
campus and points you at its green facilities.

Package layout (deliberately flat so `python -m campus_nav.cli` just works):
    config.py    environment + model settings, .env loading
    llm.py       DeepSeek client (stdlib urllib, retries, JSON extraction)
    graph.py     campus graph: load, validate, filter by access constraints
    search.py    A* and Uniform-Cost Search with comparable statistics
    places.py    the one vocabulary shared by the data pipeline and the dialogue
    kb.py        knowledge base: 876 facts, BM25 retrieval, opening-hours logic
    nlu.py       turns free text into slots -- LLM first, grammar fallback
    narrate.py   Level B: the model phrases the reply, the numbers stay computed
    dialogue.py  multi-turn state: follow-ups, constraints, facilities, hours
    cli.py       interactive terminal client
    web.py       the web application (http.server + JSON API)
    static/      its HTML/CSS/JS: chat, SVG map, speech, avatar
"""

__version__ = "0.1.0"

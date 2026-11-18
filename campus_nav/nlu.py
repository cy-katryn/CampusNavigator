"""The vocabulary the engine still owns, and the `Slots` contract it works in.

There is no parser here any more. `parse_with_grammar`, its intent order and its 38 symbol
tables went when the agent became the only front-end (docs/ARCHITECTURE.md says why), so nothing
in this file turns a sentence into a plan. What is left is the shared vocabulary -- the amenity
words, the Chinese service table, the day words, the "re-open it" phrase, the asking-frame test
-- plus `Slots`, the shape both the model's tool calls and the engine's own readings are
written into. There is no place catalogue here and none in any prompt: the resolver names
places.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

@dataclass
class Slots:
    intent: str = "out_of_scope"
    origin_text: str = ""
    destination_text: str = ""
    closed_text: str = ""    # the raw words around a closure ("the library is closed")
    # The thing the user wants to find, in their own words: "a swim", "the nearest AED".
    # The model reports it; the engine decides what kind of thing it is (see places.
    # amenity_in_text). This replaced a slot the model had to fill with a label from a list
    # of amenities, which is how "I want to have a swim" came back as "water station".
    need: str = ""
    item: str = ""           # the amenity this map offers, decided by the engine
    # The places to walk through, in the order the user said them: "through Core P, then
    # Block X" is two. A list rather than one string because that is what the request is --
    # the walk visits each stop in turn, so the order is part of the answer.
    vias: list[str] = field(default_factory=list)
    subject: str = ""      # the phrase the question is *about* (hours/facility queries)
    # The day the question asks about, exactly as it was said ("Tuesday", "聽日"). It is
    # kept apart from "subject" because taking the asking frame off the phrase takes the
    # day with it, and the timetable still has to answer for that day.
    when: str = ""
    language: str = "en"
    avoid_stairs: bool | None = None  # None keeps the current route preference
    clear_stops: bool = False         # "no stops" / "the direct route"
    along_the_way: bool = False       # the wish is about the walk in progress, not a new one
    unknown_origin: bool = False      # a distance answer measured from the map reference
    facility_origin: str = ""
    facility_target: str = ""
    facility_choice: int | None = None  # zero-based position in the last displayed list

    @property
    def mentions_places(self) -> bool:
        return bool(self.origin_text or self.destination_text)


# --- the words the engine still reads for itself --------------------------
# Every rule below is a *signal*: it answers one yes/no question about the text, and the
# engine acts on the answers where the model's own reading has been observed to fail (see
# agent._STEP_FREE, which exists for exactly that reason).

# "re-open it" lifts every closure, so a demo can recover from its own failure.
REOPEN = re.compile(
    r"\b(re-?open|open (?:it|that|them|everything) again|restore|unblock|"
    r"clear the closures?|remove the closures?)\b",
    re.I,
)
_HAS_CJK = re.compile(r"[\u3400-\u9fff]")


def has_cjk(text: str) -> bool:
    """Whether these words are written in Chinese -- which decides the display language."""
    return bool(_HAS_CJK.search(text or ""))

# --- facility: which service does the question name, if any --------------------
# One pattern, not two. These are the words a person uses for the things this map
# carries; the captured group is the item the dialogue then looks up.


# (There was a second amenity regex here. It listed the same words as
# places.FACILITY_WORDS, in a different form, so the two could drift -- one knew "canteen"
# and the other "catering", and a need understood by one was invisible to the other. The
# table is the single source; amenity_in_text() reads it on word boundaries.)

# "what facilities are there / what can I find here" -> a facilities overview
_FACILITY_ALL = re.compile(
    r"\b(facilit(?:y|ies)|amenities|what places|what can i find|what is here|"
    r"what's here|what is available|what services)\b", re.I)

def asks_for_everything(text: str) -> bool:
    """Is this a request for the catalogue rather than for one place?

    "What facilities are there?" and "where can I sit outside and study?" both arrive with
    no amenity item and no place name. The first wants the list; the second wants a place
    found by meaning. The words are the only thing that separates them.
    """
    return bool(_FACILITY_ALL.search(text or ""))

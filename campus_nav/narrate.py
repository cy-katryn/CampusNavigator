"""The three checks a sentence must pass before it is spoken.

The model writes the prose; these decide whether that prose may be used. They are pure
functions over the text and the facts the engine actually supplied, so they can be tested
without a model, a network or a route.

The model used to get a *second* call to phrase the answer (a "narrator" over a Facts object).
That is gone: the assistant writes its own sentence inside the tool loop, so a second call
would ask the same model about its own answer. What survives is the part that mattered -- the
checks -- and they now run on the sentence the assistant is about to say.
"""
from __future__ import annotations

import re

from .nlu import has_cjk

NUMBER = re.compile(r"\d+(?:\.\d+)?")

# Phrases that assert proximity to the user. Legitimate only when the user said where they
# are; otherwise the engine measured from a map reference and the sentence is a claim about
# a fact that does not exist. Short and literal on purpose: a false positive costs the
# sentence, never a fact.
_LOCATION_CLAIM = re.compile(
    r"\b(?:right where you are|where you are standing|you(?:'re| are) (?:at|in|near|standing)|"
    r"nearest to you|closest to you|near you|around you|your (?:current )?location|"
    r"from where you (?:are|stand))\b|"
    r"離你最近|离你最近|就在你|你附近|在你附近|你所在|你的位置|你嘅位置|"
    r"距離你|距离你|你而家喺|你現在在|你现在在|由你出發|从你出发",
    re.IGNORECASE,
)


def claims_unknown_location(text: str) -> bool:
    """Does this sentence say where the user is, or how far something is from them?"""
    return bool(_LOCATION_CLAIM.search(text or ""))


def in_language(text: str, language: str) -> bool:
    """Was this sentence written in the language that was asked for?

    Coarse on purpose -- one Chinese character decides Chinese, and the absence of any
    decides English -- because the real content checks are the numbers and the place names.
    This one only stops a fluent sentence in the wrong script.
    """
    chinese = language in ("zh-HK", "zh-CN")
    return has_cjk(text) if chinese else not has_cjk(text)


def supplied_numbers(payloads: list) -> set[float]:
    """Every number the model was shown: the tool results, and nothing else."""
    allowed: set[float] = {0.0, 1.0}
    def add(value) -> None:
        if value is None:
            return
        try:
            allowed.add(round(float(value), 1))
        except (TypeError, ValueError):
            return
    def sweep(value) -> None:
        if isinstance(value, bool):
            return
        if isinstance(value, (int, float)):
            add(value)
        elif isinstance(value, str):
            # A numeral inside a published name belongs to the name, not to the answer:
            # "7-Eleven" once made a correct sentence look like an invented figure.
            for token in NUMBER.findall(value):
                add(token)
        elif isinstance(value, dict):
            for item in value.values():
                sweep(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                sweep(item)
    sweep(payloads)
    return allowed


def grounded_numbers(reply: str, allowed: set[float], tolerance: float = 1.0) -> list[str]:
    """Return the numbers in `reply` that were NOT supplied to the model."""
    offenders = []
    for token in NUMBER.findall(reply or ""):
        try:
            value = float(token)
        except ValueError:
            continue
        if not any(abs(value - candidate) <= tolerance for candidate in allowed):
            offenders.append(token)
    return offenders


def problems(sentence: str, payloads: list, language: str,
             location_known: bool) -> list[str]:
    """Why this sentence may not be spoken, or an empty list when it may."""
    reasons = []
    if not sentence.strip():
        return ["the assistant wrote nothing"]
    if not in_language(sentence, language):
        reasons.append(f"not written in {language}")
    invented = grounded_numbers(sentence, supplied_numbers(payloads))
    if invented:
        reasons.append("ungrounded number(s): " + ", ".join(invented[:4]))
    if not location_known and claims_unknown_location(sentence):
        reasons.append("claims the user's location, which is unknown")
    return reasons

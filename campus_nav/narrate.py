"""Level B: let the model write the reply, while the numbers stay computed.

The problem this solves
-----------------------
Level A (before this file) printed a template:

    Route: Block Y (Lee Shau Kee Building) -> Core A  Total: 500 m, 1 leg

Correct, but it reads like a receipt and no part of the sentence came from the model.
Level B hands the *finished* route back to the model and asks it to phrase it:

    "It's about 500 m from Block Y to Core A - the quickest way, and the steps come
     at the end."

Every word comes from the API. The numbers still come from A*, because the model is
only describing facts it was given.

Why the model cannot lie about the numbers
------------------------------------------
`grounded_numbers()` compares every number in the model's reply against the set of
numbers that were actually supplied. One invented figure ("about 400 m", "200 steps")
and the reply is thrown away in favour of the deterministic template. This is a real,
demonstrable guard, not a promise in the prompt - prompts can be ignored, code cannot.

Nothing here routes anything. If the model fails, is slow, or invents a number, the
caller keeps the template and the demo continues.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .llm import LLMResult, DeepSeekClient

NUMBER = re.compile(r"\d+(?:\.\d+)?")

SYSTEM_PROMPT = """You are the voice of a campus navigation assistant at the Hong Kong
Polytechnic University. You are given the RESULT OF A ROUTE CALCULATION as JSON.

Write the answer to the user in at most 2 short sentences, plain English, friendly
and concrete.

Hard rules:
- Use ONLY numbers that appear in the JSON. Never estimate, round to a new figure, or
  invent a number. If a number is not in the JSON, do not mention numbers at all.
- Use only the place names that appear in the JSON.
- If "reachable" is false, say clearly that no route exists and mention the single most
  relevant restriction, then stop. Do not promise a route.
- "walk_minutes", when present, is how long the walk takes at an easy pace; you may say it.
- If a field is absent, do not talk about it.
- No lists, no headings, no markdown. Just the sentence(s).
"""


@dataclass
class Facts:
    """Everything the model is allowed to talk about. Built from the computed route."""

    kind: str                                  # "route" | "no_route"
    origin: str
    destination: str
    reachable: bool
    total_metres: float | None = None
    segment_lines: list[str] = field(default_factory=list)
    segment_metres: list[float] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    previous_metres: float | None = None
    walk_minutes: int | None = None       # at an easy pace, computed from total_metres
    blocking: list[str] = field(default_factory=list)
    options: list[str] = field(default_factory=list)
    option_metres: list[float] = field(default_factory=list)

    # -- what the model sees ------------------------------------------------
    def as_prompt_json(self) -> dict:
        payload = {
            "kind": self.kind,
            "from": self.origin,
            "to": self.destination,
            "reachable": self.reachable,
        }
        if self.reachable and self.total_metres is not None:
            payload["total_metres"] = round(self.total_metres)
            payload["legs"] = len(self.segment_lines)
            if self.walk_minutes is not None:
                payload["walk_minutes"] = self.walk_minutes
            payload["legs_detail"] = self.segment_lines
            if self.previous_metres is not None:
                payload["previous_route_metres"] = round(self.previous_metres)
                payload["difference_metres"] = round(self.total_metres - self.previous_metres)
        else:
            payload["blocking"] = self.blocking
            payload["options"] = self.options
        if self.constraints:
            payload["constraints"] = self.constraints
        return payload

    # -- what the model is allowed to say -----------------------------------
    def grounded_numbers(self) -> set[float]:
        """Every number that legitimately appears in this answer."""
        allowed: set[float] = {0.0, 1.0}
        def add(value) -> None:
            if value is None:
                return
            try:
                allowed.add(round(float(value), 1))
            except (TypeError, ValueError):
                return
        add(self.total_metres)
        add(self.previous_metres)
        add(self.walk_minutes)
        if self.total_metres is not None and self.previous_metres is not None:
            add(abs(self.total_metres - self.previous_metres))
        for metres in self.segment_metres:
            add(metres)
        for metres in self.option_metres:
            add(metres)
        for count in (len(self.segment_lines), len(self.options), len(self.blocking)):
            add(count)
        # A numeral inside a published place name belongs to the name, not to the answer:
        # "7-Eleven" made every correctly phrased narration look like an invented figure
        # (rejected with "ungrounded number(s): 7"), and the template replied instead.
        named = [self.origin, self.destination, *self.segment_lines, *self.options,
                 *self.blocking]
        for name in named:
            for token in NUMBER.findall(str(name or "")):
                add(token)
        return allowed


@dataclass
class Narration:
    ok: bool
    text: str = ""
    reason: str = ""
    invented: list[str] = field(default_factory=list)


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


class Narrator:
    """Turns a computed route into a sentence. Falls back silently on any problem."""

    def __init__(self, client: DeepSeekClient | None, max_tokens: int = 160) -> None:
        self.client = client
        self.max_tokens = max_tokens

    @property
    def available(self) -> bool:
        return self.client is not None and bool(
            getattr(getattr(self.client, "settings", None), "llm_available", False)
        )

    def narrate(self, facts: Facts) -> Narration:
        if not self.available:
            return Narration(False, reason="no LLM configured")

        result: LLMResult = self.client.chat(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"RESULT JSON:\n{facts.as_prompt_json()}"},
            ],
            temperature=0.2,          # a little warmth for phrasing, still stable
            max_tokens=self.max_tokens,
            retries=1,                # a demo cannot wait: one try, then the template
        )
        if not result.ok:
            return Narration(False, reason=f"api: {result.error[:80]}")

        text = " ".join(result.text.split())
        if len(text) < 12:
            return Narration(False, reason="reply too short")
        if len(text) > 600:
            return Narration(False, reason="reply too long")

        invented = grounded_numbers(text, facts.grounded_numbers())
        if invented:
            # the model made up a figure: refuse the wording, keep the calculation
            return Narration(False, reason=f"ungrounded number(s): {', '.join(invented[:4])}",
                             invented=invented)

        return Narration(True, text=text, reason=f"{result.model} {result.latency_ms} ms")

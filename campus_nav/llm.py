"""OpenRouter chat client -- standard library only.

Why no `openai` package: OpenRouter speaks the OpenAI-compatible protocol, so a
single urllib POST is enough. Fewer dependencies means the demo runs on any
machine without a network install step.

Design notes that matter for the report
---------------------------------------
* Gemma models on OpenRouter have **no native tool-calling**, so we ask for JSON
  in the prompt and parse it defensively (`extract_json`).
* Free tiers rate-limit aggressively, so 429/5xx are retried with exponential
  backoff, and every failure is returned as a `LLMResult(ok=False)` instead of
  raising -- the dialogue layer always has a grammar fallback to fall back to.
* Nothing here ever routes or answers a factual question. The model's only job is
  to turn a sentence into slots. The router decides routes; the knowledge base
  supplies facts. That separation is deliberate and worth saying in Q&A.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .config import Settings


@dataclass
class LLMResult:
    ok: bool
    text: str = ""
    error: str = ""
    model: str = ""
    latency_ms: int = 0
    attempts: int = 0
    usage: dict = field(default_factory=dict)

    def json(self) -> dict | None:
        return extract_json(self.text) if self.ok else None


def extract_json(text: str) -> dict | None:
    """Pull the first JSON object out of a model reply.

    Models wrap JSON in prose or code fences even when told not to, so this is
    intentionally forgiving: try the whole string, then fenced blocks, then the
    first balanced {...} span.
    """
    if not text:
        return None
    candidates = [text.strip()]

    for fence in ("```json", "```"):
        if fence in text:
            for part in text.split(fence)[1::2]:
                candidates.append(part.strip())

    start = text.find("{")
    if start != -1:
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start : i + 1])
                    break

    for cand in candidates:
        cand = cand.strip().rstrip(".").strip()
        if not cand.startswith("{"):
            continue
        try:
            parsed = json.loads(cand)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    return None


class OpenRouterClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    # -- low level ---------------------------------------------------------
    def chat(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.0,
        max_tokens: int = 400,
        retries: int = 3,
    ) -> LLMResult:
        if not self.settings.llm_available:
            return LLMResult(ok=False, error="LLM disabled or no OPENROUTER_API_KEY set")

        url = f"{self.settings.base_url}/chat/completions"
        payload = {
            "model": self.settings.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
            # OpenRouter uses these two for attribution on your dashboard.
            "HTTP-Referer": "https://www.polyu.edu.hk",
            "X-Title": self.settings.app_name,
        }

        started = time.monotonic()
        last_error = ""
        for attempt in range(1, retries + 1):
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.settings.timeout_s) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                choice = (body.get("choices") or [{}])[0]
                text = (choice.get("message") or {}).get("content") or ""
                return LLMResult(
                    ok=bool(text.strip()),
                    text=text,
                    model=body.get("model", self.settings.model),
                    latency_ms=int((time.monotonic() - started) * 1000),
                    attempts=attempt,
                    usage=body.get("usage") or {},
                    error="" if text.strip() else "empty completion",
                )
            except urllib.error.HTTPError as exc:
                detail = ""
                try:
                    detail = exc.read().decode("utf-8", "replace")[:200]
                except Exception:  # noqa: BLE001
                    pass
                last_error = f"HTTP {exc.code}: {detail}"
                # 4xx other than rate limiting will not fix themselves.
                if exc.code not in (408, 429, 500, 502, 503, 504):
                    break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"

            if attempt < retries:
                time.sleep(min(2 ** attempt * 0.5, 6.0))

        return LLMResult(
            ok=False,
            error=last_error or "unknown error",
            latency_ms=int((time.monotonic() - started) * 1000),
            attempts=retries,
        )

    # -- convenience -------------------------------------------------------
    def health(self) -> LLMResult:
        """One tiny round-trip so you can confirm the key and model work."""
        return self.chat(
            [{"role": "user", "content": "Reply with exactly this JSON: {\"ok\": true}"}],
            max_tokens=30,
            retries=2,
        )

"""The DeepSeek client talks to the right endpoint, and only to that endpoint."""
from __future__ import annotations

import json
import urllib.request

from campus_nav import llm
from campus_nav.config import DEFAULT_MODEL, Settings, get_settings


def _settings(**overrides) -> Settings:
    values = {"api_key": "sk-test", "model": DEFAULT_MODEL, "llm_enabled": True, "timeout_s": 5}
    values.update(overrides)
    return Settings(**values)


class _FakeResponse:
    def __init__(self, body: dict) -> None:
        self._body = body

    def read(self) -> bytes:
        return json.dumps(self._body).encode("utf-8")

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def test_chat_posts_to_deepseek_without_openrouter_headers(monkeypatch):
    seen: dict = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["headers"] = {k.lower(): v for k, v in req.header_items()}
        seen["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResponse(
            {"model": "deepseek-chat", "choices": [{"message": {"content": '{"ok": true}'}}]}
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    result = llm.DeepSeekClient(_settings()).chat([{"role": "user", "content": "hi"}])

    assert result.ok is True, result.error
    assert result.text == '{"ok": true}'
    assert seen["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert seen["headers"]["authorization"] == "Bearer sk-test"
    assert "http-referer" not in seen["headers"]
    assert "x-title" not in seen["headers"]
    assert seen["body"]["model"] == DEFAULT_MODEL


def test_missing_key_names_the_deepseek_variable(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")

    settings = get_settings()
    result = llm.DeepSeekClient(settings).chat([{"role": "user", "content": "hi"}])

    assert settings.llm_available is False
    assert result.ok is False
    assert "DEEPSEEK_API_KEY" in result.error


def test_settings_come_from_deepseek_environment(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-reasoner")

    settings = get_settings()

    assert settings.api_key == "sk-env"
    assert settings.model == "deepseek-reasoner"
    assert settings.llm_available is True
    assert settings.base_url == "https://api.deepseek.com/v1"

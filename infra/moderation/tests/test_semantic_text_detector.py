from __future__ import annotations

import json
from types import SimpleNamespace

from app import semantic_text_detector as detector


def _settings():
    return SimpleNamespace(
        max_text_items=16,
        max_text_chars=20000,
        semantic_text_url="http://text-safety:8000/internal/moderation/classify-text",
        semantic_text_secret="secret",
        semantic_text_allowed_hosts=("text-safety",),
        semantic_text_timeout_seconds=5,
        environment="test",
    )


def test_adapter_normalizes_provider_signals(monkeypatch):
    class Response:
        content = b"{}"
        def raise_for_status(self): return None
        def json(self):
            return {
                "status": "ready",
                "model": "multilingual-MiniLMv2-L6-mnli-xnli",
                "model_version": "minilm-nli-v3",
                "signals": [{"field": "comment", "category": "pornography", "severity": "critical", "confidence": 0.94}],
            }
    monkeypatch.setattr(detector.requests, "post", lambda *a, **k: Response())
    signals, versions = detector.classify_text(items=[{"field": "comment", "text": "sample"}], settings=_settings())
    assert signals[0]["category"] == "pornography"
    assert signals[0]["source"] == "text"
    assert signals[0]["reason"] == "semantic_text_classifier"
    assert versions["text_semantic"].endswith(":minilm-nli-v3")


def test_adapter_omits_empty_structured_json_without_calling_provider(monkeypatch):
    monkeypatch.setattr(
        detector.requests,
        "post",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("provider must not be called")),
    )
    signals, versions = detector.classify_text(
        items=[{"field": "hashtags", "text": "[]", "content_type": "application/json"}],
        settings=_settings(),
    )
    assert signals == []
    assert versions == {}


def test_adapter_flattens_non_empty_structured_json_to_natural_text(monkeypatch):
    captured = {}

    class Response:
        content = b"{}"

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "ready",
                "model": "semantic-test",
                "model_version": "3",
                "signals": [],
            }

    def fake_post(_url, *, data, **_kwargs):
        captured.update(json.loads(data.decode("utf-8")))
        return Response()

    monkeypatch.setattr(detector.requests, "post", fake_post)
    signals, versions = detector.classify_text(
        items=[{
            "field": "hashtags",
            "text": '["travel","beach","travel"]',
            "content_type": "application/json",
        }],
        settings=_settings(),
    )
    assert captured == {"items": [{"field": "hashtags", "text": "travel beach"}]}
    assert signals == []
    assert versions == {"text_semantic": "semantic-test:3"}


def test_adapter_rejects_malformed_structured_json_before_provider(monkeypatch):
    monkeypatch.setattr(
        detector.requests,
        "post",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("provider must not be called")),
    )
    with __import__("pytest").raises(detector.SemanticTextDetectorError, match="Malformed structured"):
        detector.classify_text(
            items=[{"field": "hashtags", "text": "[broken", "content_type": "application/json"}],
            settings=_settings(),
        )

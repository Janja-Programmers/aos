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
                "model_version": "minilm-nli-v2",
                "signals": [{"field": "comment", "category": "pornography", "severity": "critical", "confidence": 0.94}],
            }
    monkeypatch.setattr(detector.requests, "post", lambda *a, **k: Response())
    signals, versions = detector.classify_text(items=[{"field": "comment", "text": "sample"}], settings=_settings())
    assert signals[0]["category"] == "pornography"
    assert signals[0]["source"] == "text"
    assert signals[0]["reason"] == "semantic_text_classifier"
    assert versions["text_semantic"].endswith(":minilm-nli-v2")

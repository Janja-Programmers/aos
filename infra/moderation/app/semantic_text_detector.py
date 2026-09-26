from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import urlparse

import requests


class SemanticTextDetectorError(RuntimeError):
    pass


def _signature(secret: str, payload: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _validated_url(url: str, *, allowed_hosts: tuple[str, ...], environment: str) -> str:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise SemanticTextDetectorError("Invalid semantic text provider URL")
    allowed = {str(host).strip().lower() for host in allowed_hosts if str(host).strip()}
    if environment.lower() in {"production", "staging"} and allowed and parsed.hostname.lower() not in allowed:
        raise SemanticTextDetectorError("Semantic text provider host is not allowed")
    return parsed.geturl()


def classify_text(*, items: list[dict[str, Any]], settings: Any) -> tuple[list[dict[str, Any]], dict[str, str]]:
    selected: list[dict[str, str]] = []
    for item in items[: int(settings.max_text_items)]:
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        selected.append({
            "field": str(item.get("field") or "text")[:80],
            "text": text[: int(settings.max_text_chars)],
        })
    if not selected:
        return [], {}
    if not str(settings.semantic_text_url or "").strip() or not str(settings.semantic_text_secret or "").strip():
        raise SemanticTextDetectorError("Semantic text moderation provider is not configured")

    url = _validated_url(
        settings.semantic_text_url,
        allowed_hosts=tuple(settings.semantic_text_allowed_hosts or ()),
        environment=settings.environment,
    )
    body = json.dumps({"items": selected}, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    timestamp = str(int(time.time()))
    signed = timestamp.encode("ascii") + b"." + body
    try:
        response = requests.post(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-AOS-Timestamp": timestamp,
                "X-AOS-Signature": _signature(settings.semantic_text_secret, signed),
            },
            timeout=float(settings.semantic_text_timeout_seconds),
        )
        response.raise_for_status()
        data = response.json() if response.content else {}
    except requests.RequestException as exc:
        raise SemanticTextDetectorError("Semantic text moderation request failed") from exc
    except ValueError as exc:
        raise SemanticTextDetectorError("Semantic text moderation returned invalid JSON") from exc

    if str(data.get("status") or "") != "ready" or not isinstance(data.get("signals"), list):
        raise SemanticTextDetectorError("Malformed semantic text moderation response")
    model = str(data.get("model") or "unknown")[:140]
    version = str(data.get("model_version") or "unknown")[:140]
    signals: list[dict[str, Any]] = []
    for row in data["signals"][:64]:
        if not isinstance(row, dict):
            continue
        try:
            confidence = max(0.0, min(float(row.get("confidence") or 0), 1.0))
        except (TypeError, ValueError):
            continue
        category = str(row.get("category") or "other")[:80]
        signals.append({
            "category": category,
            "severity": str(row.get("severity") or "medium")[:20],
            "confidence": confidence,
            "source": "text",
            "field": str(row.get("field") or "text")[:80],
            "detector": model,
            "detector_version": version,
            "reason": "semantic_text_classifier",
        })
    return signals, {"text_semantic": f"{model}:{version}"}

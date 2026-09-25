from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import urlparse

import requests
from minio import Minio


class VisionDetectorError(RuntimeError):
    pass


def _signature(secret: str, payload: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _validated_url(url: str, *, allowed_hosts: tuple[str, ...], environment: str) -> str:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise VisionDetectorError("Invalid vision provider URL")
    if environment.lower() in {"production", "staging"} and allowed_hosts and parsed.hostname.lower() not in allowed_hosts:
        raise VisionDetectorError("Vision provider host is not allowed")
    return parsed.geturl()


def _load_image_bytes(client: Minio, item: dict[str, Any], *, max_bytes: int) -> bytes:
    bucket = str(item.get("bucket") or "").strip().strip("/")
    object_key = str(item.get("object_key") or "").strip().strip("/")
    if not bucket or not object_key:
        raise VisionDetectorError("Media storage reference is missing")
    response = None
    try:
        response = client.get_object(bucket, object_key)
        data = response.read(max_bytes + 1)
        if not data or len(data) > max_bytes:
            raise VisionDetectorError("Image exceeds moderation inference limit")
        return data
    finally:
        if response is not None:
            response.close()
            response.release_conn()


def classify_images(
    *,
    client: Minio,
    items: list[dict[str, Any]],
    settings: Any,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    images = [item for item in items if str(item.get("content_type") or "").lower().startswith("image/")]
    if not images:
        return [], {}
    if not str(settings.vision_url or "").strip() or not str(settings.vision_secret or "").strip():
        raise VisionDetectorError("Vision moderation provider is not configured")

    url = _validated_url(
        settings.vision_url,
        allowed_hosts=tuple(settings.vision_allowed_hosts or ()),
        environment=settings.environment,
    )
    encoded = [base64.b64encode(_load_image_bytes(client, item, max_bytes=settings.max_media_bytes)).decode("ascii") for item in images[: settings.max_images]]
    body = json.dumps({"images": encoded}, separators=(",", ":"), sort_keys=True).encode("utf-8")
    timestamp = str(int(time.time()))
    signed = timestamp.encode("ascii") + b"." + body
    response = requests.post(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-AOS-Timestamp": timestamp,
            "X-AOS-Signature": _signature(settings.vision_secret, signed),
        },
        timeout=settings.vision_timeout_seconds,
    )
    response.raise_for_status()
    data = response.json() if response.content else {}
    if str(data.get("status") or "") != "ready" or not isinstance(data.get("signals"), list):
        raise VisionDetectorError("Malformed vision moderation response")

    model = str(data.get("model") or "unknown")[:140]
    version = str(data.get("model_version") or "unknown")[:140]
    try:
        safe_confidence = max(0.0, min(float(data.get("safe_confidence") or 0.0), 1.0))
    except (TypeError, ValueError):
        raise VisionDetectorError("Malformed vision moderation confidence")
    signals: list[dict[str, Any]] = []
    for row in data["signals"][:32]:
        if not isinstance(row, dict):
            continue
        try:
            confidence = max(0.0, min(float(row.get("confidence") or 0), 1.0))
        except (TypeError, ValueError):
            continue
        signals.append(
            {
                "category": str(row.get("category") or "other")[:80],
                "severity": str(row.get("severity") or "medium")[:20],
                "confidence": confidence,
                "source": "image",
                "detector": model,
                "detector_version": version,
            }
        )
    if safe_confidence < 0.55 and not any(float(signal.get("confidence") or 0) >= 0.45 for signal in signals):
        signals.append(
            {
                "category": "other",
                "severity": "medium",
                "confidence": 0.50,
                "source": "image",
                "detector": model,
                "detector_version": version,
                "reason": "vision_model_uncertain",
            }
        )
    return signals, {"vision": f"{model}:{version}"}

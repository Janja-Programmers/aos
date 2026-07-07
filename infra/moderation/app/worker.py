from __future__ import annotations

import io
import json
import time
import re
from typing import Any

import requests
from minio import Minio
from PIL import Image

from app.config import get_settings
from app.security import build_signature


class ModerationProcessingError(Exception):
    pass


def _minio_client() -> Minio:
    settings = get_settings()
    if not settings.minio_access_key or not settings.minio_secret_key:
        raise ModerationProcessingError("MinIO credentials are missing")
    return Minio(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _contains_term(text: str, terms: tuple[str, ...]) -> list[str]:
    found = []
    for term in terms:
        term = str(term or "").strip().lower()
        if not term:
            continue
        if term in text:
            found.append(term)
    return found


def _inspect_image_media(client: Minio, item: dict[str, Any], labels: set[str], reasons: list[str], scores: dict[str, float]) -> None:
    settings = get_settings()
    size_bytes = int(item.get("size_bytes") or 0)
    content_type = str(item.get("content_type") or "").lower()

    if size_bytes and size_bytes > settings.max_media_bytes:
        labels.add("media_too_large_for_moderation")
        reasons.append(f"Media {item.get('media_id')} exceeds moderation inspection limit.")
        scores["media_too_large_for_moderation"] = 0.60
        return

    if not content_type.startswith("image/"):
        if content_type.startswith("video/"):
            labels.add("video_present")
            scores.setdefault("video_present", 0.10)
        return

    if not settings.inspect_media:
        labels.add("image_present")
        scores.setdefault("image_present", 0.05)
        return

    bucket = str(item.get("bucket") or "").strip().strip("/")
    object_key = str(item.get("object_key") or "").strip().strip("/")
    if not bucket or not object_key:
        labels.add("media_missing_storage_reference")
        reasons.append("Media storage reference is missing.")
        scores["media_missing_storage_reference"] = 0.60
        return

    response = None
    try:
        response = client.get_object(bucket, object_key)
        data = response.read(settings.max_media_bytes + 1)
        if len(data) > settings.max_media_bytes:
            labels.add("media_too_large_for_moderation")
            reasons.append(f"Media {item.get('media_id')} exceeds moderation inspection limit.")
            scores["media_too_large_for_moderation"] = 0.60
            return
        with Image.open(io.BytesIO(data)) as img:
            width, height = int(img.width or 0), int(img.height or 0)
            if width < 80 or height < 80:
                labels.add("low_resolution_image")
                reasons.append("Image is too small for reliable automated moderation.")
                scores["low_resolution_image"] = 0.55
            else:
                labels.add("image_inspected")
                scores.setdefault("image_inspected", 0.05)
    except Exception as exc:
        labels.add("media_inspection_failed")
        reasons.append(f"Media inspection failed: {exc}")
        scores["media_inspection_failed"] = 0.65
    finally:
        if response is not None:
            response.close()
            response.release_conn()


def _moderate(payload: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    labels: set[str] = set()
    reasons: list[str] = []
    scores: dict[str, float] = {}

    combined_text = " ".join(
        _normalize_text(str(item.get("text") or ""))[: settings.max_text_chars]
        for item in payload.get("text_items") or []
    )

    if combined_text:
        labels.add("text_present")
        reject_hits = _contains_term(combined_text, settings.reject_terms)
        review_hits = _contains_term(combined_text, settings.review_terms)
        if reject_hits:
            labels.add("blocked_text_term")
            reasons.append("Blocked text terms detected: " + ", ".join(sorted(set(reject_hits))))
            scores["blocked_text_term"] = 0.98
        if review_hits:
            labels.add("sensitive_text_term")
            reasons.append("Sensitive text terms detected: " + ", ".join(sorted(set(review_hits))))
            scores["sensitive_text_term"] = max(scores.get("sensitive_text_term", 0), 0.72)
    else:
        labels.add("no_text")

    media_items = payload.get("media_items") or []
    if media_items:
        labels.add("media_present")
        client = _minio_client()
        for item in media_items:
            _inspect_image_media(client, item, labels, reasons, scores)

    risk_score = max(scores.values()) if scores else 0.0

    if "blocked_text_term" in labels:
        decision = "reject"
    elif any(label in labels for label in {"sensitive_text_term", "media_inspection_failed", "media_missing_storage_reference", "media_too_large_for_moderation", "low_resolution_image"}):
        decision = "review"
    else:
        decision = "allow"

    return {
        "decision": decision,
        "labels": sorted(labels),
        "scores": scores,
        "reasons": reasons,
        "risk_score": risk_score,
    }


def _callback(callback_url: str, payload: dict[str, Any]) -> None:
    settings = get_settings()
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str).encode("utf-8")
    timestamp = str(int(time.time()))
    signed_payload = timestamp.encode("utf-8") + b"." + body
    response = requests.post(
        callback_url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-AOS-Callback-Timestamp": timestamp,
            "X-AOS-Moderation-Callback-Signature": build_signature(settings.callback_secret, signed_payload),
        },
        timeout=30,
    )
    response.raise_for_status()


def process_moderation_job(payload: dict[str, Any]) -> dict[str, Any]:
    job_id = str(payload.get("job_id") or "").strip()
    callback_url = str(payload.get("callback_url") or "").strip()
    if not job_id or not callback_url:
        raise ModerationProcessingError("job_id and callback_url are required")

    try:
        result = _moderate(payload)
        callback_payload = {
            "job_id": job_id,
            "status": "completed",
            "target": payload.get("target") or {},
            **result,
        }
        _callback(callback_url, callback_payload)
        return callback_payload
    except Exception as exc:
        callback_payload = {
            "job_id": job_id,
            "status": "failed",
            "error": str(exc) or "Moderation processing failed",
        }
        try:
            _callback(callback_url, callback_payload)
        finally:
            raise

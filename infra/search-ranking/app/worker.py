from __future__ import annotations

import json
from typing import Any

import requests

from app.config import get_settings
from app.queue import get_redis
from app.security import build_signature
from app.store import delete_ad, delete_short, upsert_ad, upsert_short


class SearchRankingError(Exception):
    pass


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str).encode("utf-8")


def _callback(callback_url: str, payload: dict[str, Any]) -> None:
    if not callback_url:
        return
    settings = get_settings()
    body = _json_bytes(payload)
    headers = {
        "Content-Type": "application/json",
        "X-AOS-Search-Callback-Signature": build_signature(settings.callback_secret, body),
    }
    response = requests.post(callback_url, data=body, headers=headers, timeout=20)
    response.raise_for_status()


def process_search_ranking_job(payload: dict[str, Any]) -> dict[str, Any]:
    job_id = str(payload.get("job_id") or "").strip()
    callback_url = str(payload.get("callback_url") or "").strip()
    target = payload.get("target") if isinstance(payload.get("target"), dict) else {}
    action = str(payload.get("action") or "upsert").strip().lower()
    document = payload.get("document") if isinstance(payload.get("document"), dict) else {}
    doctype = str(target.get("doctype") or "").strip()
    name = str(target.get("name") or "").strip()

    if not job_id:
        raise SearchRankingError("job_id is required")
    if not doctype or not name:
        raise SearchRankingError("target is required")

    status_payload: dict[str, Any]
    try:
        redis = get_redis()
        if doctype == "AOS Ad":
            if action == "delete":
                delete_ad(redis, name)
            else:
                upsert_ad(redis, document)
        elif doctype == "AOS Short":
            if action == "delete":
                delete_short(redis, name)
            else:
                upsert_short(redis, document)
        else:
            raise SearchRankingError(f"Unsupported target doctype: {doctype}")

        status_payload = {
            "job_id": job_id,
            "service_job_id": job_id,
            "status": "completed",
            "action": action,
            "target": target,
            "indexed": action != "delete",
        }
        _callback(callback_url, status_payload)
        return status_payload

    except Exception as exc:
        status_payload = {
            "job_id": job_id,
            "service_job_id": job_id,
            "status": "failed",
            "action": action,
            "target": target,
            "error": str(exc) or "Search/ranking indexing failed",
        }
        try:
            _callback(callback_url, status_payload)
        finally:
            raise

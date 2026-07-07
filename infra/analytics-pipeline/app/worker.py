from __future__ import annotations

import datetime as _dt
import json
import time
from typing import Any

import requests

from app.config import get_settings
from app.queue import get_redis
from app.security import build_signature


class AnalyticsPipelineError(Exception):
    pass


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str).encode("utf-8")


def _callback(callback_url: str, payload: dict[str, Any]) -> None:
    if not callback_url:
        return
    settings = get_settings()
    body = _json_bytes(payload)
    timestamp = str(int(time.time()))
    signed_payload = timestamp.encode("utf-8") + b"." + body
    headers = {
        "Content-Type": "application/json",
        "X-AOS-Callback-Timestamp": timestamp,
        "X-AOS-Analytics-Callback-Signature": build_signature(settings.callback_secret, signed_payload),
    }
    response = requests.post(callback_url, data=body, headers=headers, timeout=20)
    response.raise_for_status()


def _clean(value: Any, *, max_len: int = 180) -> str:
    text = str(value or "").strip()
    if len(text) > max_len:
        text = text[:max_len]
    return text


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _parse_date(value: Any) -> str:
    text = _clean(value, max_len=40)
    if text:
        try:
            # Accept ISO datetime/date strings and normalize to date.
            return _dt.datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
        except Exception:
            try:
                return _dt.date.fromisoformat(text[:10]).isoformat()
            except Exception:
                pass
    return _dt.datetime.utcnow().date().isoformat()


def _normalize_event(raw: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None

    event_type = _clean(raw.get("event_type"), max_len=120)
    if not event_type:
        return None

    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    metrics = raw.get("metrics") if isinstance(raw.get("metrics"), dict) else {}

    occurred_at = _clean(raw.get("occurred_at"), max_len=80)
    event_date = _parse_date(occurred_at or raw.get("event_date"))

    return {
        "event_type": event_type,
        "event_group": _clean(raw.get("event_group") or raw.get("group"), max_len=80),
        "event_date": event_date,
        "occurred_at": occurred_at or _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "user": _clean(raw.get("user"), max_len=180),
        "session_id": _clean(raw.get("session_id"), max_len=180),
        "source": _clean(raw.get("source"), max_len=120),
        "platform": _clean(raw.get("platform"), max_len=40),
        "country": _clean(raw.get("country"), max_len=80),
        "target_doctype": _clean(raw.get("target_doctype"), max_len=120),
        "target_name": _clean(raw.get("target_name"), max_len=180),
        "route_type": _clean(raw.get("route_type"), max_len=80),
        "route_id": _clean(raw.get("route_id"), max_len=180),
        "metadata": metadata,
        "metrics": metrics,
    }


def _increment_counter(redis, key: str, field: str, amount: int = 1) -> None:
    if key and field:
        redis.hincrby(key, field, amount)


def _store_event(redis, event: dict[str, Any]) -> None:
    settings = get_settings()
    event_date = event["event_date"]
    event_type = event["event_type"]
    event_group = event.get("event_group") or "other"

    stream_payload = {
        "event_type": event_type,
        "event_group": event_group,
        "event_date": event_date,
        "occurred_at": event.get("occurred_at") or "",
        "user": event.get("user") or "",
        "session_id": event.get("session_id") or "",
        "source": event.get("source") or "",
        "platform": event.get("platform") or "",
        "country": event.get("country") or "",
        "target_doctype": event.get("target_doctype") or "",
        "target_name": event.get("target_name") or "",
        "route_type": event.get("route_type") or "",
        "route_id": event.get("route_id") or "",
        "metadata": json.dumps(event.get("metadata") or {}, separators=(",", ":"), default=str),
        "metrics": json.dumps(event.get("metrics") or {}, separators=(",", ":"), default=str),
    }

    redis.xadd(
        "aos:analytics:events",
        stream_payload,
        maxlen=settings.stream_max_len,
        approximate=True,
    )

    _increment_counter(redis, f"aos:analytics:day:{event_date}", event_type)
    _increment_counter(redis, f"aos:analytics:group:{event_group}:{event_date}", event_type)

    country = event.get("country")
    if country:
        _increment_counter(redis, f"aos:analytics:country:{country}:{event_date}", event_type)

    user = event.get("user")
    if user:
        _increment_counter(redis, f"aos:analytics:user:{user}:{event_date}", event_type)

    target_doctype = event.get("target_doctype")
    target_name = event.get("target_name")
    if target_doctype and target_name:
        _increment_counter(redis, f"aos:analytics:target:{target_doctype}:{target_name}:{event_date}", event_type)

    for metric_name, metric_value in (event.get("metrics") or {}).items():
        metric_key = _clean(metric_name, max_len=80)
        amount = _safe_int(metric_value, 0)
        if metric_key and amount:
            _increment_counter(redis, f"aos:analytics:metric:{event_date}", metric_key, amount)


def process_analytics_ingest_job(payload: dict[str, Any]) -> dict[str, Any]:
    job_id = _clean(payload.get("job_id"), max_len=120)
    callback_url = _clean(payload.get("callback_url"), max_len=500)

    if not job_id:
        raise AnalyticsPipelineError("job_id is required")

    try:
        raw_events = payload.get("events") if isinstance(payload.get("events"), list) else []
        events = []
        skipped_count = 0

        max_events = max(1, get_settings().max_events_per_job)
        for raw in raw_events[:max_events]:
            event = _normalize_event(raw)
            if event:
                events.append(event)
            else:
                skipped_count += 1

        if not events:
            result = {
                "job_id": job_id,
                "service_job_id": job_id,
                "status": "skipped",
                "event_count": len(raw_events),
                "ingested_count": 0,
                "skipped_count": len(raw_events),
                "counters": {},
                "error": None,
            }
            _callback(callback_url, result)
            return result

        redis = get_redis()
        counters: dict[str, int] = {}
        for event in events:
            _store_event(redis, event)
            counters[event["event_type"]] = counters.get(event["event_type"], 0) + 1

        result = {
            "job_id": job_id,
            "service_job_id": job_id,
            "status": "ingested",
            "event_count": len(raw_events),
            "ingested_count": len(events),
            "skipped_count": skipped_count,
            "counters": counters,
            "error": None,
        }
        _callback(callback_url, result)
        return result

    except Exception as exc:
        result = {
            "job_id": job_id,
            "service_job_id": job_id,
            "status": "failed",
            "event_count": len(payload.get("events") or []),
            "ingested_count": 0,
            "skipped_count": 0,
            "counters": {},
            "error": str(exc) or exc.__class__.__name__,
        }
        try:
            _callback(callback_url, result)
        finally:
            raise

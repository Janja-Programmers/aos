from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import time
from typing import Any

import requests

from app.config import get_settings
from app.durable_lifecycle import deliver_callback, execute_work_job
from app.queue import get_queue, get_redis
from app.security import build_signature

logger = logging.getLogger(__name__)


class AnalyticsPipelineError(Exception):
	pass


def _json_bytes(payload: dict[str, Any]) -> bytes:
	return json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str).encode("utf-8")


def _callback(callback_url: str, payload: dict[str, Any]) -> Any:
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
	return response


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


def _event_identity(stable_id: str, index: int, event: dict[str, Any], raw: dict[str, Any]) -> str:
	explicit = _clean(raw.get("event_id"), max_len=200)
	if explicit:
		# Explicit event IDs are global within their event type and must dedupe
		# across different work jobs and batch positions.
		canonical = f"explicit\x1f{event.get('event_type') or ''}\x1f{explicit}"
	else:
		# Fallback identity is stable for retries of the same work item. Use the
		# original immutable payload, not normalized generated timestamps.
		original = dict(raw)
		original.pop("occurred_at", None) if not raw.get("occurred_at") else None
		canonical_payload = json.dumps(original, separators=(",", ":"), sort_keys=True, default=str)
		canonical = f"fallback\x1f{stable_id}\x1f{index}\x1f{canonical_payload}"
	canonical_bytes = canonical.encode("utf-8")
	return hashlib.sha256(canonical_bytes).hexdigest()


_ANALYTICS_DEDUPE_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 1 then
  return 0
end
redis.call('SET', KEYS[1], '1', 'EX', ARGV[1])
redis.call('XADD', KEYS[2], 'MAXLEN', '~', ARGV[2], '*', 'event_type', ARGV[3], 'payload', ARGV[4])
for i = 3, 7 do
  if KEYS[i] ~= '-' then
    redis.call('HINCRBY', KEYS[i], ARGV[3], 1)
  end
end
local metric_count = tonumber(ARGV[5]) or 0
local offset = 6
for i = 1, metric_count do
  redis.call('HINCRBY', KEYS[8], ARGV[offset], tonumber(ARGV[offset + 1]))
  offset = offset + 2
end
return 1
"""


def _store_event(redis: Any, event: dict[str, Any], event_identity: str) -> bool:
	settings = get_settings()
	event_date = event["event_date"]
	event_type = event["event_type"]
	event_group = event.get("event_group") or "other"
	country = event.get("country")
	user = event.get("user")
	target_doctype = event.get("target_doctype")
	target_name = event.get("target_name")

	stream_payload = {
		"event_type": event_type,
		"event_group": event_group,
		"event_date": event_date,
		"occurred_at": event.get("occurred_at") or "",
		"user": user or "",
		"session_id": event.get("session_id") or "",
		"source": event.get("source") or "",
		"platform": event.get("platform") or "",
		"country": country or "",
		"target_doctype": target_doctype or "",
		"target_name": target_name or "",
		"route_type": event.get("route_type") or "",
		"route_id": event.get("route_id") or "",
		"metadata": event.get("metadata") or {},
		"metrics": event.get("metrics") or {},
	}
	metric_args: list[str | int] = []
	for metric_name, metric_value in sorted((event.get("metrics") or {}).items()):
		metric_key = _clean(metric_name, max_len=80)
		amount = _safe_int(metric_value, 0)
		if metric_key and amount:
			metric_args.extend([metric_key, amount])

	keys = [
		f"aos:analytics:processed:{event_identity}",
		"aos:analytics:events",
		f"aos:analytics:day:{event_date}",
		f"aos:analytics:group:{event_group}:{event_date}",
		f"aos:analytics:country:{country}:{event_date}" if country else "-",
		f"aos:analytics:user:{user}:{event_date}" if user else "-",
		f"aos:analytics:target:{target_doctype}:{target_name}:{event_date}"
		if target_doctype and target_name
		else "-",
		f"aos:analytics:metric:{event_date}",
	]
	result = redis.eval(
		_ANALYTICS_DEDUPE_SCRIPT,
		len(keys),
		*keys,
		max(3600, settings.event_dedupe_ttl_seconds),
		settings.stream_max_len,
		event_type,
		json.dumps(stream_payload, separators=(",", ":"), sort_keys=True, default=str),
		len(metric_args) // 2,
		*metric_args,
	)
	return bool(int(result or 0))


def _perform_analytics_work(payload: dict[str, Any]) -> dict[str, Any]:
	job_id = _clean(payload.get("job_id"), max_len=120)
	if not job_id:
		raise AnalyticsPipelineError("job_id is required")

	try:
		raw_events = payload.get("events") if isinstance(payload.get("events"), list) else []
		events = []
		skipped_count = 0

		max_events = max(1, get_settings().max_events_per_job)
		for index, raw in enumerate(raw_events[:max_events]):
			event = _normalize_event(raw)
			if event:
				events.append((index, raw, event))
			else:
				skipped_count += 1

		if not events:
			result = {
				"job_id": job_id,
				"idempotency_key": payload.get("idempotency_key"),
				"dispatch_id": payload.get("dispatch_id"),
				"dispatch_generation": payload.get("dispatch_generation"),
				"dispatch_token": payload.get("dispatch_token"),
				"service_job_id": job_id,
				"status": "skipped",
				"event_count": len(raw_events),
				"ingested_count": 0,
				"skipped_count": len(raw_events),
				"counters": {},
				"error": None,
			}
			return result

		redis = get_redis()
		counters: dict[str, int] = {}
		deduplicated_count = 0
		stable_id = _clean(payload.get("idempotency_key") or job_id, max_len=200)
		for index, raw, event in events:
			identity = _event_identity(stable_id, index, event, raw)
			if _store_event(redis, event, identity):
				counters[event["event_type"]] = counters.get(event["event_type"], 0) + 1
			else:
				deduplicated_count += 1
				redis.hincrby("aos:worker:metrics:analytics_ingestion", "side_effect_dedupe_hit", 1)

		result = {
			"job_id": job_id,
			"idempotency_key": payload.get("idempotency_key"),
			"dispatch_id": payload.get("dispatch_id"),
			"dispatch_generation": payload.get("dispatch_generation"),
			"dispatch_token": payload.get("dispatch_token"),
			"service_job_id": job_id,
			"status": "ingested",
			"event_count": len(raw_events),
			"ingested_count": len(events) - deduplicated_count,
			"skipped_count": skipped_count,
			"deduplicated_count": deduplicated_count,
			"counters": counters,
			"error": None,
		}
		return result

	except Exception:
		logger.exception("Analytics ingestion job failed")
		raise


def _analytics_failure_payload(payload: dict[str, Any], _error: str) -> dict[str, Any]:
	return {
		"job_id": payload.get("job_id"),
		"idempotency_key": payload.get("idempotency_key"),
		"dispatch_id": payload.get("dispatch_id"),
		"dispatch_generation": payload.get("dispatch_generation"),
		"dispatch_token": payload.get("dispatch_token"),
		"service_job_id": payload.get("job_id"),
		"status": "failed",
		"event_count": len(payload.get("events") or []),
		"ingested_count": 0,
		"skipped_count": 0,
		"counters": {},
		"error": "ANALYTICS_INGESTION_FAILED",
	}


def process_analytics_ingest_job(payload: dict[str, Any]) -> dict[str, Any]:
	settings = get_settings()
	return execute_work_job(
		redis=get_redis(),
		queue=get_queue(),
		service_type="analytics_ingestion",
		payload=payload,
		perform_work=_perform_analytics_work,
		failure_payload=_analytics_failure_payload,
		callback_worker_method="app.worker.deliver_callback_job",
		result_ttl_seconds=int(getattr(settings, "durable_result_ttl_seconds", 604800)),
		failure_ttl_seconds=int(getattr(settings, "failure_ttl_seconds", 604800)),
		work_lock_seconds=int(getattr(settings, "job_timeout_seconds", 600)) + 300,
		callback_timeout_seconds=int(getattr(settings, "callback_job_timeout_seconds", 120)),
		callback_max_attempts=int(getattr(settings, "callback_max_attempts", 8)),
	)


def deliver_callback_job(stable_id: str) -> dict[str, Any]:
	settings = get_settings()
	return deliver_callback(
		redis=get_redis(),
		service_type="analytics_ingestion",
		stable_id=stable_id,
		send_callback=_callback,
		result_ttl_seconds=int(getattr(settings, "durable_result_ttl_seconds", 604800)),
		callback_max_attempts=int(getattr(settings, "callback_max_attempts", 8)),
	)


def replay_callback(_callback_url: str, payload: dict[str, Any]) -> dict[str, Any]:
	"""Compatibility entry point: replay from the durable result, never from RQ result data."""
	stable_id = str(payload.get("idempotency_key") or payload.get("job_id") or "").strip()
	return deliver_callback_job(stable_id)


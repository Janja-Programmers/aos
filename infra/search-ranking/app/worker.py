from __future__ import annotations

import json
import logging
import time
from typing import Any

import requests

from app.config import get_settings
from app.durable_lifecycle import deliver_callback, execute_work_job
from app.queue import get_queue, get_redis
from app.security import build_signature
from app.store import delete_ad, delete_short, upsert_ad, upsert_short

logger = logging.getLogger(__name__)


class SearchRankingError(Exception):
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
		"X-AOS-Search-Callback-Signature": build_signature(settings.callback_secret, signed_payload),
	}
	response = requests.post(callback_url, data=body, headers=headers, timeout=20)
	return response


def _perform_search_work(payload: dict[str, Any]) -> dict[str, Any]:
	job_id = str(payload.get("job_id") or "").strip()
	callback_url = str(payload.get("callback_url") or "").strip()
	target = payload.get("target") if isinstance(payload.get("target"), dict) else {}
	action = str(payload.get("action") or "upsert").strip().lower()
	document = payload.get("document") if isinstance(payload.get("document"), dict) else {}
	doctype = str(target.get("doctype") or "").strip()
	name = str(target.get("name") or "").strip()
	index_id = str(target.get("index_id") or document.get("id") or name).strip()

	if not job_id:
		raise SearchRankingError("job_id is required")
	if not doctype or not name:
		raise SearchRankingError("target is required")

	status_payload: dict[str, Any]
	try:
		redis = get_redis()
		if doctype == "AOS Ad":
			if action == "delete":
				delete_ad(redis, index_id)
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
			"idempotency_key": payload.get("idempotency_key"),
			"dispatch_id": payload.get("dispatch_id"),
			"dispatch_generation": payload.get("dispatch_generation"),
			"dispatch_token": payload.get("dispatch_token"),
			"service_job_id": job_id,
			"status": "completed",
			"action": action,
			"target": target,
			"indexed": action != "delete",
		}
		return status_payload

	except Exception:
		logger.exception("Search/ranking indexing job failed")
		raise


def _search_failure_payload(payload: dict[str, Any], _error: str) -> dict[str, Any]:
	return {
		"job_id": payload.get("job_id"),
		"idempotency_key": payload.get("idempotency_key"),
		"dispatch_id": payload.get("dispatch_id"),
		"dispatch_generation": payload.get("dispatch_generation"),
		"dispatch_token": payload.get("dispatch_token"),
		"service_job_id": payload.get("job_id"),
		"status": "failed",
		"action": payload.get("action") or "upsert",
		"target": payload.get("target") or {},
		"error": "SEARCH_INDEXING_FAILED",
	}


def process_search_ranking_job(payload: dict[str, Any]) -> dict[str, Any]:
	settings = get_settings()
	return execute_work_job(
		redis=get_redis(),
		queue=get_queue(),
		service_type="search_indexing",
		payload=payload,
		perform_work=_perform_search_work,
		failure_payload=_search_failure_payload,
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
		service_type="search_indexing",
		stable_id=stable_id,
		send_callback=_callback,
		result_ttl_seconds=int(getattr(settings, "durable_result_ttl_seconds", 604800)),
		callback_max_attempts=int(getattr(settings, "callback_max_attempts", 8)),
	)


def replay_callback(_callback_url: str, payload: dict[str, Any]) -> dict[str, Any]:
	"""Compatibility entry point: replay from the durable result, never from RQ result data."""
	stable_id = str(payload.get("idempotency_key") or payload.get("job_id") or "").strip()
	return deliver_callback_job(stable_id)


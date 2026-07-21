from __future__ import annotations

import datetime
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

import requests

try:
	import firebase_admin
	from firebase_admin import credentials, messaging
except Exception:  # pragma: no cover - import failure handled at runtime
	firebase_admin = None
	credentials = None
	messaging = None

from app.config import get_settings
from app.durable_lifecycle import deliver_callback, execute_work_job
from app.queue import get_queue, get_redis
from app.security import build_signature

logger = logging.getLogger(__name__)


class NotificationDeliveryError(Exception):
	pass


_FIREBASE_INITIALIZED = False


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
		"X-AOS-Notification-Callback-Signature": build_signature(settings.callback_secret, signed_payload),
	}
	response = requests.post(callback_url, data=body, headers=headers, timeout=20)
	return response


def _stringify_data(data: dict | None) -> dict[str, str]:
	return {str(k): str(v) for k, v in (data or {}).items() if v is not None}


def _chunk(records: list[dict[str, Any]], size: int):
	for idx in range(0, len(records), size):
		yield records[idx : idx + size]


def _normalize_android_priority(priority: str | None) -> str | None:
	value = str(priority or "").strip().lower()
	return value if value in {"high", "normal"} else None


def _normalize_android_notification_priority(priority: str | None) -> str | None:
	value = str(priority or "").strip().lower()
	return value if value in {"min", "low", "default", "high", "max"} else None


def _build_android_config(options: dict[str, Any] | None):
	if not messaging:
		return None
	options = options or {}
	priority = _normalize_android_priority(options.get("priority"))
	notification_priority = _normalize_android_notification_priority(
		options.get("android_notification_priority")
	)
	android_channel_id = options.get("android_channel_id")
	ttl_seconds = options.get("ttl_seconds")

	if not any([priority, notification_priority, android_channel_id, ttl_seconds is not None]):
		return None

	ttl = None
	if ttl_seconds is not None:
		try:
			ttl = datetime.timedelta(seconds=max(0, int(ttl_seconds)))
		except Exception:
			ttl = None

	android_notification = None
	if android_channel_id or notification_priority:
		android_notification = messaging.AndroidNotification(
			channel_id=android_channel_id,
			priority=notification_priority,
		)

	return messaging.AndroidConfig(
		priority=priority,
		ttl=ttl,
		notification=android_notification,
	)


def _init_firebase() -> None:
	global _FIREBASE_INITIALIZED
	settings = get_settings()

	if settings.dry_run:
		return
	if _FIREBASE_INITIALIZED:
		return
	if any(module is None for module in (firebase_admin, credentials)):
		raise NotificationDeliveryError("firebase-admin is not installed")

	service_account_path = Path(settings.firebase_service_account_path)
	if not service_account_path.exists():
		raise NotificationDeliveryError(f"Firebase service account not found: {service_account_path}")

	if not firebase_admin._apps:
		cred = credentials.Certificate(str(service_account_path))
		firebase_admin.initialize_app(cred)

	_FIREBASE_INITIALIZED = True


def _exception_detail(exc: Exception | None) -> dict[str, Any]:
	"""Return bounded provider diagnostics without raw exception text."""
	if exc is None:
		return {"error_class": "UnknownProviderError"}

	detail: dict[str, Any] = {"error_class": exc.__class__.__name__[:80]}
	for attr in ("code", "error_code"):
		value = getattr(exc, attr, None)
		if value is not None:
			safe = str(value).strip()[:120]
			if safe and all(ch.isalnum() or ch in "._-" for ch in safe):
				detail[attr] = safe

	http_response = getattr(exc, "http_response", None)
	status_code = getattr(http_response, "status_code", None) if http_response is not None else None
	if isinstance(status_code, int):
		detail["http_status"] = status_code
	return detail


def _is_inactive_token_error(detail: dict[str, Any] | str) -> bool:
	if isinstance(detail, dict):
		text = " ".join(str(value or "") for value in detail.values())
	else:
		text = str(detail or "")
	text_lower = text.lower()

	needles = [
		"registration-token-not-registered",
		"invalid-registration-token",
		"requested entity was not found",
		"the registration token is not a valid fcm registration token",
		"unregistered",
		"notregistered",
		"invalid_argument",
		"invalid-argument",
		"sender_id_mismatch",
		"sender-id-mismatch",
		"mismatched-credential",
		"mismatched sender",
		"sender id mismatch",
	]
	return any(needle in text_lower for needle in needles)


def _send_push(payload: dict[str, Any]) -> dict[str, Any]:
	settings = get_settings()
	tokens = payload.get("tokens") if isinstance(payload.get("tokens"), list) else []
	tokens = [token for token in tokens if isinstance(token, dict) and token.get("token")]

	if not tokens:
		return {
			"status": "skipped",
			"success_count": 0,
			"failure_count": 0,
			"inactive_token_hashes": [],
			"provider_responses": [],
			"error": None,
		}

	if settings.dry_run:
		return {
			"status": "delivered",
			"success_count": len(tokens),
			"failure_count": 0,
			"inactive_token_hashes": [],
			"provider_responses": [{"dry_run": True, "count": len(tokens)}],
			"error": None,
		}

	_init_firebase()

	success_count = 0
	failure_count = 0
	inactive_hashes: list[str] = []
	provider_responses: list[dict[str, Any]] = []
	first_error: str | None = None

	data_payload = _stringify_data(payload.get("data") if isinstance(payload.get("data"), dict) else {})
	android_config = _build_android_config(
		payload.get("options") if isinstance(payload.get("options"), dict) else {}
	)

	for chunk_index, chunk in enumerate(_chunk(tokens, max(1, min(settings.max_tokens_per_multicast, 500)))):
		token_values = [row["token"] for row in chunk if row.get("token")]
		if not token_values:
			continue

		message = messaging.MulticastMessage(
			notification=messaging.Notification(
				title=str(payload.get("title") or ""),
				body=str(payload.get("body") or ""),
			),
			data=data_payload,
			tokens=token_values,
			android=android_config,
		)

		response = messaging.send_each_for_multicast(message)
		success_count += int(response.success_count or 0)
		failure_count += int(response.failure_count or 0)

		chunk_errors: list[dict[str, Any]] = []
		provider_acceptance_ids: list[str] = []
		for idx, item in enumerate(response.responses):
			if item.success:
				message_id = str(getattr(item, "message_id", "") or "").strip()
				if message_id:
					provider_acceptance_ids.append(hashlib.sha256(message_id.encode("utf-8")).hexdigest()[:24])
				continue

			token_meta = chunk[idx] if idx < len(chunk) else {}
			inactive = _is_inactive_token_error(item.exception)
			error_detail = _exception_detail(item.exception)
			error_detail["error_category"] = "inactive_token" if inactive else "provider_failure"
			token_hash = str(token_meta.get("token_hash") or "").strip()

			if inactive and token_hash:
				inactive_hashes.append(token_hash)

			if not first_error:
				code = (
					error_detail.get("code")
					or error_detail.get("error_code")
					or error_detail.get("error_class")
				)
				first_error = (
					f"{error_detail.get('error_category')}:{code}"
					if code
					else str(error_detail.get("error_category"))
				)

			chunk_errors.append(
				{
					"token_hash": token_hash,
					"device_type": str(token_meta.get("device_type") or ""),
					"inactive": inactive,
					**error_detail,
				}
			)

		provider_responses.append(
			{
				"chunk_index": chunk_index,
				"success_count": response.success_count,
				"failure_count": response.failure_count,
				"provider_acceptance_ids": provider_acceptance_ids[:500],
				"errors": chunk_errors,
			}
		)

	return {
		"status": "delivered" if success_count > 0 or failure_count == 0 else "failed",
		"success_count": success_count,
		"failure_count": failure_count,
		"inactive_token_hashes": sorted(set(inactive_hashes)),
		"provider_responses": provider_responses,
		"error": first_error,
	}


def _perform_notification_work(payload: dict[str, Any]) -> dict[str, Any]:
	job_id = str(payload.get("job_id") or "").strip()
	callback_url = str(payload.get("callback_url") or "").strip()

	if not job_id:
		raise NotificationDeliveryError("job_id is required")

	try:
		result = _send_push(payload)
		status_payload = {
			"job_id": job_id,
			"idempotency_key": payload.get("idempotency_key"),
			"dispatch_id": payload.get("dispatch_id"),
			"dispatch_generation": payload.get("dispatch_generation"),
			"dispatch_token": payload.get("dispatch_token"),
			"service_job_id": job_id,
			"delivery_identity": hashlib.sha256(
				str(payload.get("idempotency_key") or job_id).encode("utf-8")
			).hexdigest()[:32],
			"status": result["status"],
			"channel": payload.get("channel") or "push",
			"token_count": len(payload.get("tokens") or []),
			"success_count": result["success_count"],
			"failure_count": result["failure_count"],
			"inactive_token_hashes": result["inactive_token_hashes"],
			"provider_responses": result["provider_responses"],
			"error": result.get("error"),
		}
		return status_payload

	except Exception:
		logger.exception("Notification delivery job failed")
		raise


def _notification_failure_payload(payload: dict[str, Any], error_category: str) -> dict[str, Any]:
	token_count = len(payload.get("tokens") or [])
	uncertain = error_category in {"WORK_TRANSPORT_TIMEOUT", "WORK_TRANSPORT_UNCERTAIN"}
	return {
		"job_id": payload.get("job_id"),
		"idempotency_key": payload.get("idempotency_key"),
		"dispatch_id": payload.get("dispatch_id"),
		"dispatch_generation": payload.get("dispatch_generation"),
		"dispatch_token": payload.get("dispatch_token"),
		"service_job_id": payload.get("job_id"),
		"status": "delivery_uncertain" if uncertain else "failed",
		"channel": payload.get("channel") or "push",
		"token_count": token_count,
		"success_count": 0,
		"failure_count": token_count,
		"inactive_token_hashes": [],
		"provider_responses": [],
		"error": "NOTIFICATION_PROVIDER_OUTCOME_UNCERTAIN" if uncertain else "NOTIFICATION_DELIVERY_FAILED",
	}


def process_notification_delivery_job(payload: dict[str, Any]) -> dict[str, Any]:
	settings = get_settings()
	return execute_work_job(
		redis=get_redis(),
		queue=get_queue(),
		service_type="notification_delivery",
		payload=payload,
		perform_work=_perform_notification_work,
		failure_payload=_notification_failure_payload,
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
		service_type="notification_delivery",
		stable_id=stable_id,
		send_callback=_callback,
		result_ttl_seconds=int(getattr(settings, "durable_result_ttl_seconds", 604800)),
		callback_max_attempts=int(getattr(settings, "callback_max_attempts", 8)),
	)


def replay_callback(_callback_url: str, payload: dict[str, Any]) -> dict[str, Any]:
	"""Compatibility entry point: replay from the durable result, never from RQ result data."""
	stable_id = str(payload.get("idempotency_key") or payload.get("job_id") or "").strip()
	return deliver_callback_job(stable_id)


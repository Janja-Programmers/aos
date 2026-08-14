"""Frappe-side orchestration for external notification delivery.

Frappe owns notification records, push tokens, permissions, and delivery-job
metadata. The external notification-delivery service owns provider calls,
retries inside its own queue, and per-token provider response handling.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any

import frappe
import requests
from frappe.utils import now_datetime

from aos.api.shared.account_status import get_account_state
from aos.api.shared.blocking import is_blocked_between
from aos.api.shared.db import is_duplicate_entry_error
from aos.services.accounts.identity import public_account_id_for_user
from aos.services.notifications.contracts import canonical_event, contract_for
from aos.services.notifications.devices import (
	PushDeviceValidationError,
	get_token_hash,
	normalize_device_type,
	normalize_push_token,
)
from aos.services.notifications.observability import notification_log

from aos.services.transactional_outbox import (
	OutboxConflictError,
	complete_outbox_without_callback,
	current_outbox_dispatch_context,
	ensure_outbox_for_job,
	mark_outbox_callback,
	outbox_dispatch_context,
	record_companion_dispatch_outcome,
	sanitized_dispatch_error,
	stable_idempotency_key,
	validate_callback_idempotency,
)
from aos.utils.aos_config import clean_url, get_env, get_env_bool, get_env_int, get_first_env


class NotificationDeliveryError(RuntimeError):
	"""Raised when notification-delivery orchestration fails."""


VALID_DELIVERY_KINDS = frozenset({"persistent", "transient"})
VALID_PUSH_PRIORITIES = frozenset({"", "high", "normal"})
VALID_ANDROID_NOTIFICATION_PRIORITIES = frozenset({"", "min", "low", "default", "high", "max"})
TRANSIENT_INCOMING_CALL_EVENT = "aos_incoming_call"
MAX_DELIVERY_DATA_BYTES = 16 * 1024
MAX_STORED_CALLBACK_BYTES = 64 * 1024


@dataclass(frozen=True)
class NotificationDeliveryConfig:
	service_url: str
	service_secret: str
	callback_secret: str
	callback_url: str
	request_timeout_seconds: int
	max_attempts: int
	queue: str
	dispatcher_timeout_seconds: int
	enabled: bool
	fail_open: bool


def get_notification_delivery_config() -> NotificationDeliveryConfig:
	service_url = clean_url(
		get_first_env(
			"NOTIFICATION_SERVICE_URL",
			default=f"http://127.0.0.1:{get_env('NOTIFICATION_SERVICE_PORT', '8160')}",
		),
		default="http://127.0.0.1:8160",
	)
	callback_url = clean_url(get_env("NOTIFICATION_CALLBACK_URL"))
	if not callback_url:
		domain = get_env("AOS_API_DOMAIN")
		if domain:
			callback_url = f"https://{domain}/api/method/aos.api.v1.notification_delivery.handle_callback"
		else:
			callback_url = "http://127.0.0.1:8000/api/method/aos.api.v1.notification_delivery.handle_callback"

	return NotificationDeliveryConfig(
		service_url=service_url,
		service_secret=get_env("NOTIFICATION_SERVICE_SECRET", "") or "",
		callback_secret=get_env("NOTIFICATION_SERVICE_CALLBACK_SECRET", "") or "",
		callback_url=callback_url,
		request_timeout_seconds=get_env_int(
			"NOTIFICATION_SERVICE_REQUEST_TIMEOUT_SECONDS", 20, min_value=5, max_value=120
		),
		max_attempts=get_env_int("NOTIFICATION_MAX_RETRIES", 3, min_value=1, max_value=10),
		queue=get_env("NOTIFICATION_FRAPPE_QUEUE", "long") or "long",
		dispatcher_timeout_seconds=get_env_int(
			"NOTIFICATION_DISPATCHER_TIMEOUT_SECONDS", 300, min_value=60, max_value=1800
		),
		enabled=get_env_bool("NOTIFICATION_DELIVERY_ENABLED", True),
		fail_open=get_env_bool("NOTIFICATION_DELIVERY_FAIL_OPEN", True),
	)


def _json_bytes(payload: dict[str, Any]) -> bytes:
	return json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str).encode("utf-8")


def build_signature(secret: str, payload: bytes) -> str:
	digest = hmac.new(str(secret or "").encode("utf-8"), payload, hashlib.sha256).hexdigest()
	return f"sha256={digest}"


def verify_signature(secret: str, payload: bytes, signature: str | None) -> bool:
	if not str(secret or "").strip():
		return False
	if not signature:
		return False
	return hmac.compare_digest(build_signature(secret, payload), str(signature).strip())


def _json_dumps(value: Any) -> str:
	return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)


def _json_loads(value: str | None, default: Any):
	if not value:
		return default
	try:
		return json.loads(value)
	except Exception:
		return default


def _clean(value: Any) -> str:
	return str(value or "").strip()


def _stringify_data(data: dict | None) -> dict[str, str]:
	return {str(k): str(v) for k, v in (data or {}).items() if v is not None}


def _get_active_push_tokens(user: str) -> list[dict[str, str]]:
	if not user:
		return []

	rows = frappe.get_all(
		"AOS Push Token",
		filters={"user": user, "is_active": 1},
		fields=["token", "token_hash", "device_type"],
	)

	deduped: dict[str, dict[str, str]] = {}
	for row in rows:
		try:
			token = normalize_push_token(row.get("token"))
			device_type = normalize_device_type(row.get("device_type"))
		except PushDeviceValidationError:
			notification_log(
				"notification.device_registration_skipped",
				platform=_clean(row.get("device_type"))[:20],
				outcome="skipped",
				reason="invalid_registration",
			)
			continue
		token_hash = _clean(row.get("token_hash")).lower()
		if token_hash != get_token_hash(token):
			notification_log(
				"notification.device_registration_skipped",
				platform=device_type,
				outcome="skipped",
				reason="token_hash_mismatch",
			)
			continue
		deduped[token_hash] = {
			"token": token,
			"token_hash": token_hash,
			"device_type": device_type,
		}

	return list(deduped.values())


def _sanitize_payload(payload: dict[str, Any]) -> dict[str, Any]:
	"""Store request diagnostics without duplicating notification body/data or raw tokens."""
	tokens = payload.get("tokens") or []
	return {
		"job_id": _clean(payload.get("job_id"))[:200],
		"notification_id": _clean(payload.get("notification_id"))[:180] or None,
		"delivery_kind": _clean(payload.get("delivery_kind"))[:40],
		"channel": _clean(payload.get("channel"))[:40],
		"event": _clean(payload.get("event"))[:80],
		"options": payload.get("options") if isinstance(payload.get("options"), dict) else {},
		"tokens": [
			{
				"token_hash": _clean(token.get("token_hash"))[:64],
				"device_type": _clean(token.get("device_type"))[:20],
			}
			for token in tokens[:5000]
			if isinstance(token, dict) and _clean(token.get("token_hash"))
		],
	}



def _bounded_json_object(value: Any, *, max_bytes: int = MAX_DELIVERY_DATA_BYTES) -> dict[str, Any]:
	if value is None:
		value = {}
	if not isinstance(value, dict):
		raise NotificationDeliveryError("Notification delivery payload must be an object")
	encoded = json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")
	if len(encoded) > max_bytes:
		raise NotificationDeliveryError("Notification delivery payload is too large")
	return value


def _normalize_job_input(
	*,
	user: str,
	event: str,
	title: str,
	body: str,
	delivery_kind: str,
	priority: str | None,
	ttl_seconds: int | None,
	android_channel_id: str | None,
	android_notification_priority: str | None,
) -> dict[str, Any]:
	user = _clean(user)
	event = _clean(event)
	title = _clean(title)
	body = _clean(body)
	delivery_kind = _clean(delivery_kind).lower() or "persistent"
	priority = _clean(priority).lower() or None
	android_channel_id = _clean(android_channel_id) or None
	android_notification_priority = _clean(android_notification_priority).lower() or None
	if not user or not frappe.db.exists("User", user):
		raise NotificationDeliveryError("Delivery user is required")
	if not event or len(event) > 80:
		raise NotificationDeliveryError("Invalid notification delivery event")
	if not title or len(title) > 140:
		raise NotificationDeliveryError("Invalid notification delivery title")
	if not body or len(body) > 500:
		raise NotificationDeliveryError("Invalid notification delivery body")
	if delivery_kind not in VALID_DELIVERY_KINDS:
		raise NotificationDeliveryError("Invalid notification delivery kind")
	if (priority or "") not in VALID_PUSH_PRIORITIES:
		raise NotificationDeliveryError("Invalid notification delivery priority")
	if (android_notification_priority or "") not in VALID_ANDROID_NOTIFICATION_PRIORITIES:
		raise NotificationDeliveryError("Invalid Android notification priority")
	if android_channel_id and len(android_channel_id) > 100:
		raise NotificationDeliveryError("Invalid Android notification channel")
	if ttl_seconds is not None:
		try:
			ttl_seconds = int(ttl_seconds)
		except (TypeError, ValueError) as exc:
			raise NotificationDeliveryError("Invalid notification delivery TTL") from exc
		if ttl_seconds < 0 or ttl_seconds > 86400:
			raise NotificationDeliveryError("Invalid notification delivery TTL")
	return {
		"user": user,
		"event": event,
		"title": title,
		"body": body,
		"delivery_kind": delivery_kind,
		"priority": priority,
		"ttl_seconds": ttl_seconds,
		"android_channel_id": android_channel_id,
		"android_notification_priority": android_notification_priority,
	}


def _stable_delivery_idempotency_key(
	*, notification_id: str | None, delivery_kind: str, event: str, user: str, explicit_key: str | None
) -> str:
	if explicit_key:
		return stable_idempotency_key("notification_delivery", "explicit", explicit_key)
	if notification_id:
		return stable_idempotency_key("notification_delivery", "notification", notification_id)
	return stable_idempotency_key("notification_delivery", delivery_kind, event, user)


def _recipient_delivery_suppression_reason(user: str) -> str | None:
	if not user or not frappe.db.exists("User", user):
		return "recipient_missing"
	try:
		enabled = frappe.db.get_value("User", user, "enabled")
		if enabled is not None and not bool(int(enabled or 0)):
			return "recipient_disabled"
	except Exception:
		return "recipient_unavailable"
	try:
		state = get_account_state(user)
		if state.get("exists") and (
			state.get("is_deleted") or state.get("is_deactivated") or state.get("is_suspended")
		):
			return "recipient_inactive"
	except Exception:
		return "recipient_unavailable"
	return None


def _delivery_suppression_reason(job) -> str | None:
	reason = _recipient_delivery_suppression_reason(_clean(job.user))
	if reason:
		return reason

	if _clean(job.delivery_kind).lower() == "persistent":
		if not job.notification or not frappe.db.exists("AOS Notification", job.notification):
			return "notification_missing"
		row = frappe.db.get_value(
			"AOS Notification", job.notification, ["user", "type", "actor"], as_dict=True
		) or {}
		if _clean(row.get("user")) != _clean(job.user):
			return "notification_owner_mismatch"
		try:
			contract = contract_for(_clean(row.get("type")))
			if canonical_event(_clean(row.get("type"))) != _clean(job.event):
				return "notification_contract_mismatch"
		except Exception:
			return "notification_contract_mismatch"
		actor = _clean(row.get("actor")) or None
		if actor and contract.actor_scoped:
			actor_reason = _recipient_delivery_suppression_reason(actor)
			if actor_reason:
				return "actor_unavailable"
			try:
				if is_blocked_between(job.user, actor):
					return "blocked_relationship"
			except Exception:
				# Policy uncertainty is privacy-sensitive. Suppress this delivery
				# without mutating or failing the owning business domain.
				return "relationship_unavailable"
		return None

	if _clean(job.event) != TRANSIENT_INCOMING_CALL_EVENT:
		# Existing/internal transient jobs are an established durable-delivery
		# compatibility surface (including transactional-outbox recovery). Public
		# creation remains restricted to aos_incoming_call by
		# create_notification_delivery_job(); only Calls receives the additional
		# domain-specific stale-call/block checks below.
		return None
	data = _json_loads(job.payload_json, {})
	call_id = _clean(data.get("call_id") or data.get("id")) if isinstance(data, dict) else ""
	if not call_id or not frappe.db.exists("AOS Call", call_id):
		return "call_missing"
	call = frappe.db.get_value("AOS Call", call_id, ["caller", "receiver", "status"], as_dict=True) or {}
	if _clean(call.get("receiver")) != _clean(job.user):
		return "call_recipient_mismatch"
	if _clean(call.get("status")).lower() not in {"initiated", "ringing"}:
		return "call_not_ringing"
	caller = _clean(call.get("caller"))
	if not caller:
		return "call_actor_missing"
	if _recipient_delivery_suppression_reason(caller):
		return "actor_unavailable"
	try:
		if is_blocked_between(job.user, caller):
			return "blocked_relationship"
	except Exception:
		return "relationship_unavailable"
	return None


def _safe_callback_reason(value: Any, *, fallback: str | None = None) -> str | None:
	"""Return only bounded code-like callback diagnostics.

	The companion is authenticated, but provider/library exception text may still
	contain payload fragments or other sensitive values. Persist only identifiers
	and short reason codes; arbitrary text is replaced by the supplied fallback.
	"""
	clean = _clean(value)[:240]
	if clean and all(ch.isalnum() or ch in "._:-" for ch in clean):
		return clean
	return fallback


def _safe_callback_int(value: Any, *, maximum: int = 5000) -> int:
	try:
		number = int(value or 0)
	except (TypeError, ValueError):
		return 0
	return max(0, min(number, maximum))


def _safe_acceptance_id(value: Any) -> str | None:
	clean = _clean(value).lower()
	if len(clean) == 24 and all(ch in "0123456789abcdef" for ch in clean):
		return clean
	return None


def _sanitize_callback_payload(payload: dict[str, Any]) -> dict[str, Any]:
	"""Persist bounded provider diagnostics without tokens or arbitrary provider payloads."""
	provider_responses = payload.get("provider_responses")
	if not isinstance(provider_responses, list):
		provider_responses = []
	clean_responses: list[dict[str, Any]] = []
	for response in provider_responses[:50]:
		if not isinstance(response, dict):
			continue
		raw_errors = response.get("errors")
		if not isinstance(raw_errors, list):
			raw_errors = []
		clean_errors: list[dict[str, Any]] = []
		for error in raw_errors[:100]:
			if not isinstance(error, dict):
				continue
			token_hash = _clean(error.get("token_hash")).lower()
			if len(token_hash) != 64 or any(ch not in "0123456789abcdef" for ch in token_hash):
				token_hash = ""
			device_type = _clean(error.get("device_type")).lower()
			if device_type not in {"android", "ios", "web"}:
				device_type = ""
			clean_errors.append(
				{
					"token_hash": token_hash or None,
					"device_type": device_type or None,
					"inactive": bool(error.get("inactive")),
					"error_class": _safe_callback_reason(error.get("error_class")),
					"code": _safe_callback_reason(error.get("code")),
					"error_code": _safe_callback_reason(error.get("error_code")),
					"http_status": _safe_callback_int(error.get("http_status"), maximum=599) or None,
					"error_category": _safe_callback_reason(error.get("error_category")),
				}
			)
		raw_acceptance_ids = response.get("provider_acceptance_ids")
		if not isinstance(raw_acceptance_ids, list):
			raw_acceptance_ids = []
		acceptance_ids = [
			clean
			for value in raw_acceptance_ids[:500]
			if (clean := _safe_acceptance_id(value))
		]
		clean_responses.append(
			{
				"chunk_index": _safe_callback_int(response.get("chunk_index"), maximum=100000),
				"delivery_mode": _safe_callback_reason(response.get("delivery_mode")),
				"success_count": _safe_callback_int(response.get("success_count")),
				"failure_count": _safe_callback_int(response.get("failure_count")),
				"provider_acceptance_ids": acceptance_ids,
				"errors": clean_errors,
			}
		)
	raw_inactive = payload.get("inactive_token_hashes")
	if not isinstance(raw_inactive, list):
		raw_inactive = []
	inactive_hashes = []
	for value in raw_inactive[:2000]:
		token_hash = _clean(value).lower()
		if len(token_hash) == 64 and all(ch in "0123456789abcdef" for ch in token_hash):
			inactive_hashes.append(token_hash)
	clean = {
		"job_id": _clean(payload.get("job_id"))[:200],
		"status": _safe_callback_reason(payload.get("status")),
		"channel": _safe_callback_reason(payload.get("channel")),
		"token_count": _safe_callback_int(payload.get("token_count")),
		"success_count": _safe_callback_int(payload.get("success_count")),
		"failure_count": _safe_callback_int(payload.get("failure_count")),
		"inactive_token_hashes": sorted(set(inactive_hashes)),
		"provider_responses": clean_responses,
		"error": _safe_callback_reason(payload.get("error")),
	}
	encoded = json.dumps(clean, ensure_ascii=False, default=str).encode("utf-8")
	if len(encoded) <= MAX_STORED_CALLBACK_BYTES:
		return clean
	clean["provider_responses"] = []
	return clean


def create_notification_delivery_job(
	*,
	user: str,
	event: str,
	title: str,
	body: str,
	payload: dict | None = None,
	notification_id: str | None = None,
	delivery_kind: str = "persistent",
	priority: str | None = None,
	ttl_seconds: int | None = None,
	android_channel_id: str | None = None,
	android_notification_priority: str | None = None,
	idempotency_key: str | None = None,
	enqueue: bool = True,
) -> object | None:
	config = get_notification_delivery_config()
	if not config.enabled:
		return None

	values = _normalize_job_input(
		user=user,
		event=event,
		title=title,
		body=body,
		delivery_kind=delivery_kind,
		priority=priority,
		ttl_seconds=ttl_seconds,
		android_channel_id=android_channel_id,
		android_notification_priority=android_notification_priority,
	)
	notification_id = _clean(notification_id) or None
	if values["delivery_kind"] == "persistent":
		if not notification_id or not frappe.db.exists("AOS Notification", notification_id):
			raise NotificationDeliveryError("Persistent delivery requires an existing notification")
		notification = frappe.db.get_value(
			"AOS Notification", notification_id, ["user", "type"], as_dict=True
		) or {}
		if _clean(notification.get("user")) != values["user"]:
			raise NotificationDeliveryError("Notification delivery owner mismatch")
		try:
			if canonical_event(_clean(notification.get("type"))) != values["event"]:
				raise NotificationDeliveryError("Notification delivery event mismatch")
		except NotificationDeliveryError:
			raise
		except Exception as exc:
			raise NotificationDeliveryError("Unsupported notification delivery type") from exc
	else:
		if notification_id:
			raise NotificationDeliveryError("Transient delivery cannot reference an inbox notification")
		if values["event"] != TRANSIENT_INCOMING_CALL_EVENT:
			raise NotificationDeliveryError("Unsupported transient notification event")

	data_payload = _bounded_json_object(dict(payload or {}))
	data_payload["event"] = values["event"]
	data_payload = _bounded_json_object(_stringify_data(data_payload))
	stable_key = _stable_delivery_idempotency_key(
		notification_id=notification_id,
		delivery_kind=values["delivery_kind"],
		event=values["event"],
		user=values["user"],
		explicit_key=_clean(idempotency_key) or None,
	)

	job = frappe.get_doc(
		{
			"doctype": "AOS Notification Delivery Job",
			"user": values["user"],
			"notification": notification_id,
			"delivery_kind": values["delivery_kind"],
			"channel": "push",
			"event": values["event"],
			"title": values["title"],
			"body": values["body"],
			"priority": values["priority"],
			"ttl_seconds": values["ttl_seconds"],
			"android_channel_id": values["android_channel_id"],
			"android_notification_priority": values["android_notification_priority"],
			"status": "Queued",
			"attempt_count": 0,
			"max_attempts": config.max_attempts,
			"idempotency_key": stable_key,
			"payload_json": _json_dumps(data_payload),
		}
	)
	try:
		job.insert(ignore_permissions=True)
	except Exception as exc:
		if not is_duplicate_entry_error(exc):
			raise
		existing_name = frappe.db.get_value(
			"AOS Notification Delivery Job", {"idempotency_key": stable_key}, "name"
		)
		if not existing_name:
			raise
		job = frappe.get_doc("AOS Notification Delivery Job", existing_name)
		notification_log(
			"notification.delivery_job_deduplicated",
			job_id=job.name,
			delivery_kind=job.delivery_kind,
			outcome="deduplicated",
		)

	if enqueue:
		enqueue_notification_delivery_dispatch(job.name)
	return job

def enqueue_notification_delivery_dispatch(delivery_job_id: str) -> object:
	config = get_notification_delivery_config()
	job = frappe.get_doc("AOS Notification Delivery Job", delivery_job_id)
	return ensure_outbox_for_job(
		service_type="notification_delivery",
		job=job,
		queue=config.queue,
		timeout_seconds=config.dispatcher_timeout_seconds,
		aggregate_doctype="AOS Notification" if job.notification else "User",
		aggregate_name=job.notification or job.user,
		max_attempts=job.max_attempts,
	)


def build_notification_delivery_payload(job) -> dict[str, Any]:
	dispatch_context = outbox_dispatch_context(job_doctype="AOS Notification Delivery Job", job_name=job.name)
	tokens = _get_active_push_tokens(job.user)
	return {
		**dispatch_context,
		"job_id": job.name,
		"idempotency_key": job.idempotency_key,
		"notification_id": job.notification,
		"delivery_kind": job.delivery_kind,
		"channel": job.channel,
		"user": public_account_id_for_user(job.user) or "recipient",
		"event": job.event,
		"title": job.title,
		"body": job.body,
		"data": _json_loads(job.payload_json, {}),
		"options": {
			"priority": job.priority,
			"ttl_seconds": job.ttl_seconds,
			"android_channel_id": job.android_channel_id,
			"android_notification_priority": job.android_notification_priority,
		},
		"tokens": tokens,
		"callback_url": get_notification_delivery_config().callback_url,
	}


def dispatch_notification_delivery_job(delivery_job_id: str) -> object:
	job = frappe.get_doc("AOS Notification Delivery Job", delivery_job_id)
	dispatch_context = current_outbox_dispatch_context(
		job_doctype="AOS Notification Delivery Job", job_name=job.name
	)
	if job.status in {"Delivered", "Skipped", "Cancelled"}:
		return job
	if (
		job.status == "Processing"
		and getattr(job, "service_job_id", None)
		and not (dispatch_context and dispatch_context.recovery_dispatch)
	):
		return job

	config = get_notification_delivery_config()
	suppression_reason = _delivery_suppression_reason(job)
	if suppression_reason:
		job.status = "Skipped"
		job.last_error = suppression_reason
		job.completed_at = now_datetime()
		job.save(ignore_permissions=True)
		complete_outbox_without_callback(
			job_doctype="AOS Notification Delivery Job",
			job_name=job.name,
			status="skipped",
		)
		frappe.db.commit()
		notification_log(
			"notification.delivery_suppressed",
			job_id=job.name,
			delivery_kind=job.delivery_kind,
			outcome="skipped",
			reason=suppression_reason,
		)
		return job
	if not config.enabled:
		job.status = "Cancelled"
		job.last_error = "Notification delivery is disabled"
		job.completed_at = now_datetime()
		job.save(ignore_permissions=True)
		complete_outbox_without_callback(
			job_doctype="AOS Notification Delivery Job",
			job_name=job.name,
			status="cancelled",
		)
		frappe.db.commit()
		return job

	previous_work_attempt_count = int(job.attempt_count or 0)
	job.status = "Dispatching"
	job.last_error = None
	job.dispatched_at = now_datetime()
	job.save(ignore_permissions=True)
	frappe.db.commit()

	payload = build_notification_delivery_payload(job)
	token_count = len(payload.get("tokens") or [])
	job.token_count = token_count
	job.request_payload = json.dumps(_sanitize_payload(payload), ensure_ascii=False, default=str)
	job.save(ignore_permissions=True)
	frappe.db.commit()

	if token_count <= 0:
		job.status = "Skipped"
		job.success_count = 0
		job.failure_count = 0
		job.inactive_count = 0
		job.completed_at = now_datetime()
		job.last_error = None
		job.save(ignore_permissions=True)
		complete_outbox_without_callback(
			job_doctype="AOS Notification Delivery Job",
			job_name=job.name,
			status="skipped",
		)
		frappe.db.commit()
		return job

	body = _json_bytes(payload)
	headers = {
		"Content-Type": "application/json",
		"X-AOS-Notification-Signature": build_signature(config.service_secret, body),
		"Idempotency-Key": job.idempotency_key,
	}

	try:
		response = requests.post(
			f"{config.service_url}/jobs",
			data=body,
			headers=headers,
			timeout=config.request_timeout_seconds,
		)
		response.raise_for_status()
		data = response.json() if response.content else {}
		dispatch_action = record_companion_dispatch_outcome(str(data.get("dispatch_action") or ""), data)
		job.reload()
		if dispatch_action in {"enqueued", "stale_generation_replaced"}:
			job.attempt_count = previous_work_attempt_count + 1
		else:
			job.attempt_count = previous_work_attempt_count
		job.status = "Processing"
		job.service_job_id = str(data.get("service_job_id") or data.get("job_id") or job.service_job_id or "")
		job.started_at = now_datetime()
		job.save(ignore_permissions=True)
		frappe.db.commit()
		return job
	except OutboxConflictError as exc:
		job.reload()
		job.status = "Processing"
		job.last_error = exc.error_code
		job.save(ignore_permissions=True)
		frappe.db.commit()
		raise
	except Exception as exc:
		error_code = sanitized_dispatch_error(exc)
		frappe.log_error(frappe.get_traceback(), f"Notification dispatch failed: {error_code}")
		job.reload()
		# A transport error may occur after the companion accepted the stable job.
		# Keep business work nonterminal; the outbox reconciles by stable identity.
		job.status = "Processing"
		job.last_error = error_code
		job.save(ignore_permissions=True)
		frappe.db.commit()
		raise


def _callback_count(payload: dict[str, Any], field: str, *, default: int = 0) -> int:
	try:
		value = int(payload.get(field) if payload.get(field) is not None else default)
	except (TypeError, ValueError) as exc:
		raise NotificationDeliveryError("Invalid notification delivery callback count") from exc
	if value < 0 or value > 5000:
		raise NotificationDeliveryError("Invalid notification delivery callback count")
	return value


def _callback_token_hashes(payload: dict[str, Any]) -> list[str]:
	raw = payload.get("inactive_token_hashes") or []
	if not isinstance(raw, list) or len(raw) > 2000:
		raise NotificationDeliveryError("Invalid inactive token hashes")
	values: list[str] = []
	for item in raw:
		value = _clean(item).lower()
		if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
			raise NotificationDeliveryError("Invalid inactive token hashes")
		values.append(value)
	return sorted(set(values))


def handle_notification_delivery_callback(payload: dict[str, Any]) -> object:
	job_id = _clean(payload.get("job_id"))
	if not job_id:
		raise NotificationDeliveryError("job_id is required")
	if not frappe.db.exists("AOS Notification Delivery Job", job_id):
		raise NotificationDeliveryError("Notification delivery job not found")

	job = frappe.get_doc("AOS Notification Delivery Job", job_id)
	incoming_status = _clean(payload.get("status")).lower()
	canonical_status = {"completed": "delivered", "ready": "delivered"}.get(incoming_status, incoming_status)
	if canonical_status not in {"delivered", "skipped", "failed"}:
		raise NotificationDeliveryError("Invalid notification delivery callback status")
	validation = validate_callback_idempotency(job, payload, callback_status=canonical_status)
	if validation.duplicate:
		return job

	terminal_statuses = {"Delivered", "Skipped", "Failed", "Cancelled"}
	if job.status in terminal_statuses:
		if job.status == "Cancelled":
			return job
		if job.status == "Delivered" and incoming_status in {"delivered", "completed", "ready"}:
			mark_outbox_callback(
				job_doctype="AOS Notification Delivery Job",
				job_name=job.name,
				callback_status="delivered",
				success=True,
			)
			return job
		if job.status == "Skipped" and incoming_status == "skipped":
			mark_outbox_callback(
				job_doctype="AOS Notification Delivery Job",
				job_name=job.name,
				callback_status="skipped",
				success=True,
			)
			return job
		if job.status == "Failed" and incoming_status == "failed":
			mark_outbox_callback(
				job_doctype="AOS Notification Delivery Job",
				job_name=job.name,
				callback_status="failed",
				success=False,
				error=_safe_callback_reason(
					payload.get("error"), fallback="notification_delivery_failed"
				),
			)
			return job
		raise NotificationDeliveryError(f"Notification delivery job is already {job.status}")

	job.response_payload = json.dumps(_sanitize_callback_payload(payload), ensure_ascii=False, default=str)
	job.callback_received_at = now_datetime()
	job.success_count = _callback_count(payload, "success_count")
	job.failure_count = _callback_count(payload, "failure_count")
	job.token_count = _callback_count(payload, "token_count", default=int(job.token_count or 0))
	if job.success_count + job.failure_count > job.token_count:
		raise NotificationDeliveryError("Invalid notification delivery callback counts")

	inactive_hashes = _callback_token_hashes(payload)
	job.inactive_count = len(inactive_hashes)
	job.inactive_token_hashes = json.dumps(inactive_hashes, ensure_ascii=False)

	_deactivate_inactive_tokens(inactive_hashes)

	if incoming_status in {"delivered", "completed", "ready"}:
		job.status = "Delivered"
		job.completed_at = now_datetime()
		job.last_error = None
		job.save(ignore_permissions=True)
		mark_outbox_callback(
			job_doctype="AOS Notification Delivery Job",
			job_name=job.name,
			callback_status="delivered",
			success=True,
		)
		return job

	if incoming_status == "skipped":
		job.status = "Skipped"
		job.completed_at = now_datetime()
		job.last_error = _safe_callback_reason(payload.get("message"))
		job.save(ignore_permissions=True)
		mark_outbox_callback(
			job_doctype="AOS Notification Delivery Job",
			job_name=job.name,
			callback_status="skipped",
			success=True,
		)
		return job

	if incoming_status == "failed":
		job.save(ignore_permissions=True)
		return mark_notification_delivery_job_failed(
			job.name,
			_safe_callback_reason(payload.get("error"), fallback="notification_delivery_failed")
			or "notification_delivery_failed",
			commit=False,
		)

	raise NotificationDeliveryError("Invalid notification delivery callback status")


def _deactivate_inactive_tokens(token_hashes: list[str]) -> None:
	for token_hash in token_hashes:
		if not token_hash:
			continue
		frappe.db.set_value(
			"AOS Push Token",
			{"token_hash": token_hash},
			{
				"is_active": 0,
				"active_device_key": None,
				"last_used_at": now_datetime(),
			},
			update_modified=False,
		)


def mark_notification_delivery_job_failed(
	job_id: str, error: str, *, commit: bool = True, dispatch_failure: bool = False
) -> object:
	job = frappe.get_doc("AOS Notification Delivery Job", job_id)
	job.status = "Failed"
	job.completed_at = now_datetime()
	job.last_error = str(error or "Notification delivery failed")[:1000]
	job.save(ignore_permissions=True)
	if not dispatch_failure:
		mark_outbox_callback(
			job_doctype="AOS Notification Delivery Job",
			job_name=job.name,
			callback_status="failed",
			success=False,
			error=job.last_error,
		)
	if commit:
		frappe.db.commit()
	return job


def retry_queued_notification_delivery_jobs(limit: int = 100) -> dict[str, int]:
	config = get_notification_delivery_config()
	if not config.enabled:
		return {"queued": 0, "failed": 0, "skipped": 0}

	rows = frappe.get_all(
		"AOS Notification Delivery Job",
		filters={
			"status": "Queued",
			"attempt_count": ["<", config.max_attempts],
		},
		pluck="name",
		order_by="creation asc",
		limit=limit,
	)

	queued = 0
	failed = 0
	for name in rows:
		try:
			enqueue_notification_delivery_dispatch(name)
			queued += 1
		except Exception:
			failed += 1
			frappe.log_error(frappe.get_traceback(), f"Failed to enqueue notification delivery retry {name}")

	return {"queued": queued, "failed": failed, "skipped": 0}

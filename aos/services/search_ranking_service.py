"""Frappe-side orchestration for external search/ranking/recommendation.

Frappe owns business truth, permissions, visibility, and hydration.
The external search-ranking service owns candidate retrieval, fast text matching,
feed scoring, and Redis-backed index state.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from typing import Any

import frappe
import requests
from frappe.utils import now_datetime

from aos.services.transactional_outbox import (
	OutboxConflictError,
	complete_outbox_without_callback,
	current_outbox_dispatch_context,
	ensure_outbox_for_job,
	mark_outbox_callback,
	outbox_dispatch_context,
	record_companion_dispatch_outcome,
	sanitized_dispatch_error,
	validate_callback_idempotency,
)
from aos.utils.aos_config import clean_url, get_env, get_env_bool, get_env_int, get_first_env


class SearchRankingError(RuntimeError):
	"""Raised when search/ranking orchestration fails."""


@dataclass(frozen=True)
class SearchRankingConfig:
	service_url: str
	service_secret: str = field(repr=False)
	callback_secret: str = field(repr=False)
	callback_url: str
	request_timeout_seconds: int
	max_attempts: int
	queue: str
	dispatcher_timeout_seconds: int
	enabled: bool
	fail_open: bool
	use_ads_search: bool
	use_shorts_feed: bool


def get_search_ranking_config() -> SearchRankingConfig:
	service_url = clean_url(
		get_first_env(
			"SEARCH_RANKING_SERVICE_URL",
			default=f"http://127.0.0.1:{get_env('SEARCH_RANKING_SERVICE_PORT', '8150')}",
		),
		default="http://127.0.0.1:8150",
	)
	callback_url = clean_url(get_env("SEARCH_RANKING_CALLBACK_URL"))
	if not callback_url:
		domain = get_env("AOS_API_DOMAIN")
		if domain:
			callback_url = f"https://{domain}/api/method/aos.api.v1.search_ranking.handle_callback"
		else:
			callback_url = "http://127.0.0.1:8000/api/method/aos.api.v1.search_ranking.handle_callback"
	return SearchRankingConfig(
		service_url=service_url,
		service_secret=get_env("SEARCH_RANKING_SERVICE_SECRET", "") or "",
		callback_secret=get_env("SEARCH_RANKING_SERVICE_CALLBACK_SECRET", "") or "",
		callback_url=callback_url,
		request_timeout_seconds=get_env_int(
			"SEARCH_RANKING_SERVICE_REQUEST_TIMEOUT_SECONDS", 20, min_value=5, max_value=120
		),
		max_attempts=get_env_int("SEARCH_RANKING_MAX_RETRIES", 3, min_value=1, max_value=10),
		queue=get_env("SEARCH_RANKING_FRAPPE_QUEUE", "long") or "long",
		dispatcher_timeout_seconds=get_env_int(
			"SEARCH_RANKING_DISPATCHER_TIMEOUT_SECONDS", 300, min_value=60, max_value=1800
		),
		enabled=get_env_bool("SEARCH_RANKING_ENABLED", True),
		fail_open=get_env_bool("SEARCH_RANKING_FAIL_OPEN", True),
		use_ads_search=get_env_bool("SEARCH_RANKING_USE_ADS_SEARCH", True),
		use_shorts_feed=get_env_bool("SEARCH_RANKING_USE_SHORTS_FEED", True),
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


def _target_exists(job: Any) -> bool:
	"""Return whether the durable search target still exists.

	Search-index jobs intentionally outlive their aggregate so delete work can
	remove stale companion index entries. Missing targets are therefore a valid
	lifecycle state for delete jobs, not a link-validation failure.
	"""
	target_doctype = _clean(getattr(job, "target_doctype", None))
	target_name = _clean(getattr(job, "target_name", None))
	return bool(target_doctype and target_name and frappe.db.exists(target_doctype, target_name))


def _prepare_missing_target_delete(job: Any) -> str:
	"""Classify how a missing aggregate should be removed from the index.

	An undispatched upsert can safely become a delete under the same durable
	correlation. Once the companion may have accepted the upsert, that
	correlation is immutable and a separate delete job is required.
	"""
	if _target_exists(job):
		return "present"

	job.flags.ignore_links = True
	if _clean(getattr(job, "action", None)).lower() == "delete":
		return "delete"

	if _clean(getattr(job, "service_job_id", None)) or int(getattr(job, "attempt_count", 0) or 0) > 0:
		return "replacement_required"

	job.action = "delete"
	job.document_json = _json_dumps({})
	job.indexed = 0
	job.last_error = None
	return "converted"


def _cancel_stale_upsert_and_enqueue_delete(job: Any) -> object:
	"""Close an accepted stale upsert and create one fresh delete correlation."""
	job.status = "Cancelled"
	job.completed_at = now_datetime()
	job.last_error = "TARGET_DELETED"
	_save_search_index_job(job)
	complete_outbox_without_callback(
		job_doctype="AOS Search Index Job",
		job_name=job.name,
		status="cancelled",
	)

	existing_delete = frappe.db.sql(
		"""
		SELECT name
		FROM `tabAOS Search Index Job`
		WHERE target_doctype = %s
		  AND target_name = %s
		  AND action = 'delete'
		  AND name != %s
		  AND status NOT IN ('Failed', 'Cancelled')
		ORDER BY creation DESC, name DESC
		LIMIT 1
		""",
		(job.target_doctype, job.target_name, job.name),
	)
	if not existing_delete:
		create_search_index_job(
			target_doctype=job.target_doctype,
			target_name=job.target_name,
			target_owner=job.target_owner,
			index_kind=job.index_kind,
			action="delete",
			source="missing_target_reconciliation",
			document={},
			enqueue=True,
		)
	return job


def _save_search_index_job(job: Any) -> None:
	"""Save a durable search job even after its Dynamic Link target is deleted."""
	if not _target_exists(job):
		job.flags.ignore_links = True
	job.save(ignore_permissions=True)


def _search_job_idempotency_key(
	*, target_doctype: str, target_name: str, index_kind: str, action: str, document: dict[str, Any]
) -> str:
	generation = _clean(document.get("generation") or document.get("modified") or document.get("id"))
	material = "\0".join([target_doctype, target_name, index_kind, action, generation])
	return hashlib.sha256(material.encode("utf-8")).hexdigest()


def create_search_index_job(
	*,
	target_doctype: str,
	target_name: str,
	index_kind: str,
	action: str = "upsert",
	source: str = "manual",
	document: dict[str, Any] | None = None,
	target_owner: str | None = None,
	enqueue: bool = True,
) -> object | None:
	config = get_search_ranking_config()
	if not config.enabled:
		return None
	target_doctype = _clean(target_doctype)
	target_name = _clean(target_name)
	index_kind = _clean(index_kind).lower()
	action = _clean(action).lower() or "upsert"
	if action not in {"upsert", "delete"}:
		raise SearchRankingError("Invalid search index action")
	if not target_doctype or not target_name:
		raise SearchRankingError("Search index target is required")
	if action != "delete" and not frappe.db.exists(target_doctype, target_name):
		raise SearchRankingError("Search index target does not exist")

	idempotency_key = _search_job_idempotency_key(
		target_doctype=target_doctype, target_name=target_name, index_kind=index_kind, action=action, document=document or {}
	)
	existing = frappe.db.get_value("AOS Search Index Job", {"idempotency_key": idempotency_key}, "name")
	if existing:
		job = frappe.get_doc("AOS Search Index Job", existing)
		if enqueue and job.status in {"Queued", "Failed"}:
			enqueue_search_index_dispatch(job.name)
		return job

	job = frappe.get_doc(
		{
			"doctype": "AOS Search Index Job",
			"target_doctype": target_doctype,
			"target_name": target_name,
			"target_owner": target_owner,
			"index_kind": index_kind,
			"action": action,
			"source": source,
			"status": "Queued",
			"attempt_count": 0,
			"max_attempts": config.max_attempts,
			"idempotency_key": idempotency_key,
			"document_json": _json_dumps(document or {}),
		}
	)
	if action == "delete" and not frappe.db.exists(target_doctype, target_name):
		job.flags.ignore_links = True
	job.insert(ignore_permissions=True)
	if enqueue:
		enqueue_search_index_dispatch(job.name)
	return job


def enqueue_search_index_dispatch(search_job_id: str) -> object:
	config = get_search_ranking_config()
	job = frappe.get_doc("AOS Search Index Job", search_job_id)
	target_exists = _target_exists(job)
	return ensure_outbox_for_job(
		service_type="search_indexing",
		job=job,
		queue=config.queue,
		timeout_seconds=config.dispatcher_timeout_seconds,
		aggregate_doctype=job.target_doctype if target_exists else None,
		aggregate_name=job.target_name if target_exists else None,
		max_attempts=job.max_attempts,
	)


def build_search_index_job_payload(job) -> dict[str, Any]:
	dispatch_context = outbox_dispatch_context(job_doctype="AOS Search Index Job", job_name=job.name)
	document = _json_loads(job.document_json, {})
	return {
		**dispatch_context,
		"job_id": job.name,
		"idempotency_key": job.idempotency_key,
		"action": job.action,
		"target": {
			"doctype": job.target_doctype,
			"name": job.target_name,
			"index_id": _clean(document.get("id")) or job.target_name,
			"owner": job.target_owner,
			"index_kind": job.index_kind,
			"source": job.source,
		},
		"document": document,
		"callback_url": get_search_ranking_config().callback_url,
	}


def dispatch_search_index_job(search_job_id: str) -> object:
	job = frappe.get_doc("AOS Search Index Job", search_job_id)
	dispatch_context = current_outbox_dispatch_context(job_doctype="AOS Search Index Job", job_name=job.name)
	if job.status in {"Indexed", "Deleted", "Cancelled"}:
		return job
	if (
		job.status == "Processing"
		and getattr(job, "service_job_id", None)
		and not (dispatch_context and dispatch_context.recovery_dispatch)
	):
		return job

	target_state = _prepare_missing_target_delete(job)
	if target_state == "replacement_required":
		return _cancel_stale_upsert_and_enqueue_delete(job)

	config = get_search_ranking_config()
	if not config.enabled:
		job.status = "Cancelled"
		job.last_error = "Search/ranking is disabled"
		job.completed_at = now_datetime()
		_save_search_index_job(job)
		complete_outbox_without_callback(
			job_doctype="AOS Search Index Job",
			job_name=job.name,
			status="cancelled",
		)
		frappe.db.commit()
		return job

	previous_work_attempt_count = int(job.attempt_count or 0)
	job.status = "Dispatching"
	job.last_error = None
	job.dispatched_at = now_datetime()
	_save_search_index_job(job)
	frappe.db.commit()

	payload = build_search_index_job_payload(job)
	job.request_payload = json.dumps(payload, ensure_ascii=False, default=str)
	_save_search_index_job(job)
	frappe.db.commit()

	body = _json_bytes(payload)
	headers = {
		"Content-Type": "application/json",
		"X-AOS-Search-Signature": build_signature(config.service_secret, body),
		"Idempotency-Key": job.idempotency_key,
	}
	try:
		response = requests.post(
			f"{config.service_url}/jobs", data=body, headers=headers, timeout=config.request_timeout_seconds
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
		_save_search_index_job(job)
		frappe.db.commit()
		return job
	except OutboxConflictError as exc:
		job.reload()
		job.status = "Processing"
		job.last_error = exc.error_code
		_save_search_index_job(job)
		frappe.db.commit()
		raise
	except Exception as exc:
		error_code = sanitized_dispatch_error(exc)
		frappe.log_error(frappe.get_traceback(), f"Search/ranking dispatch failed: {error_code}")
		job.reload()
		# A transport error may occur after the companion accepted the stable job.
		# Keep business work nonterminal; the outbox reconciles by stable identity.
		job.status = "Processing"
		job.last_error = error_code
		_save_search_index_job(job)
		frappe.db.commit()
		raise


def handle_search_index_callback(payload: dict[str, Any]) -> object:
	job_id = _clean(payload.get("job_id"))
	if not job_id:
		raise SearchRankingError("job_id is required")
	if not frappe.db.exists("AOS Search Index Job", job_id):
		raise SearchRankingError("Search index job not found")
	job = frappe.get_doc("AOS Search Index Job", job_id, for_update=True)
	incoming_status = _clean(payload.get("status")).lower()
	expected_success_status = "Deleted" if job.action == "delete" else "Indexed"
	canonical_status = (
		expected_success_status.lower() if incoming_status in {"completed", "ready"} else incoming_status
	)
	validation = validate_callback_idempotency(job, payload, callback_status=canonical_status)
	if validation.duplicate:
		return job
	terminal_statuses = {"Indexed", "Deleted", "Failed"}
	if job.status in terminal_statuses:
		if job.status == expected_success_status and incoming_status in {"completed", "ready"}:
			mark_outbox_callback(
				job_doctype="AOS Search Index Job",
				job_name=job.name,
				callback_status=expected_success_status.lower(),
				success=True,
			)
			return job
		if job.status == "Failed" and incoming_status == "failed":
			mark_outbox_callback(
				job_doctype="AOS Search Index Job",
				job_name=job.name,
				callback_status="failed",
				success=False,
				error=_clean(payload.get("error")) or "Search/ranking job failed",
			)
			return job
		raise SearchRankingError(f"Search/ranking job is already {job.status}")
	if incoming_status in {"completed", "ready"}:
		job.status = expected_success_status
		job.indexed = 1 if job.action != "delete" else 0
		job.score = float(payload.get("score") or 0)
		job.response_payload = json.dumps(payload, ensure_ascii=False, default=str)
		job.callback_received_at = now_datetime()
		job.completed_at = now_datetime()
		job.last_error = None
		_save_search_index_job(job)
		mark_outbox_callback(
			job_doctype="AOS Search Index Job",
			job_name=job.name,
			callback_status=expected_success_status.lower(),
			success=True,
		)
		return job
	if incoming_status == "failed":
		return mark_search_index_job_failed(
			job.name, _clean(payload.get("error")) or "Search/ranking job failed", commit=False
		)
	raise SearchRankingError("Invalid search/ranking callback status")


def mark_search_index_job_failed(
	job_id: str, error: str, *, commit: bool = True, dispatch_failure: bool = False
) -> object:
	job = frappe.get_doc("AOS Search Index Job", job_id)
	job.status = "Failed"
	job.completed_at = now_datetime()
	job.last_error = str(error or "Search/ranking job failed")[:1000]
	_save_search_index_job(job)
	if not dispatch_failure:
		mark_outbox_callback(
			job_doctype="AOS Search Index Job",
			job_name=job.name,
			callback_status="failed",
			success=False,
			error=job.last_error,
		)
	if commit:
		frappe.db.commit()
	return job


def build_ad_index_document(ad_id: str) -> dict[str, Any]:
	row = frappe.db.sql(
		"""
		SELECT a.name, a.public_id, a.title, a.description, a.status, a.country, a.location,
		       a.category, a.seller, a.price_type, a.price, a.currency, a.average_rating,
		       a.total_reviews, a.view_count, a.wishlist_count, a.expires_on, a.creation, a.modified,
		       s.status AS seller_status, s.user AS seller_user, s.shop_name AS seller_name,
		       u.enabled AS user_enabled, p.account_status, p.is_verified AS seller_verified
		FROM `tabAOS Ad` a
		LEFT JOIN `tabAOS Seller` s ON s.name=a.seller
		LEFT JOIN `tabUser` u ON u.name=s.user
		LEFT JOIN `tabAOS Profile` p ON p.user=s.user
		WHERE a.name=%s
		LIMIT 1
		""",
		(ad_id,),
		as_dict=True,
	)
	if not row:
		raise SearchRankingError("Ad not found")
	ad = row[0]
	public_id = _clean(ad.public_id)
	if not public_id:
		raise SearchRankingError("Ad public id is missing")
	attributes = frappe.get_all(
		"AOS Ad Attribute Value",
		filters={"parent": ad_id, "parenttype": "AOS Ad"},
		fields=["attribute", "value_text", "value_json"],
		order_by="idx asc",
		limit=100,
	)
	attribute_values: dict[str, Any] = {}
	keywords: list[str] = []
	for value in [ad.title, ad.category, ad.location, ad.country, ad.seller_name]:
		if _clean(value):
			keywords.append(_clean(value))
	for item in attributes:
		key = _clean(item.attribute)
		if not key:
			continue
		value: Any = _clean(item.value_text)
		if not value and item.value_json:
			value = _json_loads(item.value_json, [])
		attribute_values[key] = value
		keywords.append(key)
		if isinstance(value, list):
			keywords.extend(_clean(v) for v in value if _clean(v))
		elif _clean(value):
			keywords.append(_clean(value))

	from frappe.utils import getdate, nowdate
	eligible = (
		_clean(ad.status) == "Active"
		and _clean(ad.seller_status) == "Active"
		and int(ad.user_enabled or 0) == 1
		and (_clean(ad.account_status) or "Active") == "Active"
		and (not ad.expires_on or getdate(ad.expires_on) >= getdate(nowdate()))
	)
	return {
		"id": public_id,
		"title": ad.title or "",
		"description": ad.description or "",
		"status": ad.status or "",
		"eligible": bool(eligible),
		"country": ad.country or "",
		"location": ad.location or "",
		"category": ad.category or "",
		"seller": ad.seller or "",
		"seller_status": ad.seller_status or "",
		"seller_name": ad.seller_name or ad.seller_user or "",
		"seller_verified": int(ad.seller_verified or 0),
		"attributes": attribute_values,
		"price_type": ad.price_type or "",
		"price": float(ad.price or 0),
		"currency": ad.currency or "",
		"average_rating": float(ad.average_rating or 0),
		"total_reviews": int(ad.total_reviews or 0),
		"view_count": int(ad.view_count or 0),
		"wishlist_count": int(ad.wishlist_count or 0),
		"creation": str(ad.creation or ""),
		"modified": str(ad.modified or ""),
		"generation": str(ad.modified or ""),
		"keywords": keywords,
	}


def build_short_index_document(short_id: str) -> dict[str, Any]:
	short = frappe.get_doc("AOS Short", short_id)
	return {
		"id": short.name,
		"name": short.name,
		"owner": getattr(short, "owner", "") or "",
		"status": getattr(short, "status", "") or "",
		"visibility_status": getattr(short, "visibility_status", "") or "",
		"approval_status": getattr(short, "approval_status", "") or "",
		"content_mode": getattr(short, "content_mode", "") or "",
		"audience": getattr(short, "audience", "") or "",
		"country": getattr(short, "country", "") or "",
		"seller": getattr(short, "seller", "") or "",
		"ad": getattr(short, "ad", "") or "",
		"caption": getattr(short, "caption", "") or "",
		"hashtags": getattr(short, "hashtags", "") or "",
		"ranking_score": float(getattr(short, "ranking_score", 0) or 0),
		"view_count": int(getattr(short, "view_count", 0) or 0),
		"like_count": int(getattr(short, "like_count", 0) or 0),
		"comment_count": int(getattr(short, "comment_count", 0) or 0),
		"share_count": int(getattr(short, "share_count", 0) or 0),
		"save_count": int(getattr(short, "save_count", 0) or 0),
		"repost_count": int(getattr(short, "repost_count", 0) or 0),
		"creation": str(getattr(short, "creation", "") or ""),
		"modified": str(getattr(short, "modified", "") or ""),
	}


def enqueue_ad_search_delete(
	ad_id: str,
	*,
	public_id: str,
	generation: str | None = None,
	source: str = "ad_delete",
	enqueue: bool = True,
) -> object | None:
	"""Persist a derived-index delete while the aggregate may be disappearing.

	The canonical public ID is captured by the caller before deletion so a hard
	delete can never fall back to a Frappe document name in the companion index.
	"""
	clean_public_id = _clean(public_id)
	if not clean_public_id:
		raise SearchRankingError("Ad public ID is required for index deletion")
	document = {
		"id": clean_public_id,
		"generation": _clean(generation) or f"delete:{clean_public_id}",
	}
	return create_search_index_job(
		target_doctype="AOS Ad",
		target_name=_clean(ad_id),
		index_kind="ad",
		action="delete",
		source=source,
		document=document,
		enqueue=enqueue,
	)


def enqueue_ad_search_index(ad_id: str, *, source: str = "ad_update", enqueue: bool = True) -> object | None:
	action = "upsert"
	document: dict[str, Any] = {}
	target_owner = None
	if not frappe.db.exists("AOS Ad", ad_id):
		raise SearchRankingError("Missing Ads require enqueue_ad_search_delete with a captured public ID")
	document = build_ad_index_document(ad_id)
	target_owner = frappe.db.get_value("AOS Seller", document.get("seller"), "user") or document.get(
		"seller"
	)
	if not document.get("eligible"):
		action = "delete"
	return create_search_index_job(
		target_doctype="AOS Ad",
		target_name=ad_id,
		target_owner=target_owner,
		index_kind="ad",
		action=action,
		source=source,
		document=document,
		enqueue=enqueue,
	)


def enqueue_short_search_index(
	short_id: str, *, source: str = "short_update", enqueue: bool = True
) -> object | None:
	action = "upsert"
	document: dict[str, Any] = {}
	target_owner = None
	if frappe.db.exists("AOS Short", short_id):
		document = build_short_index_document(short_id)
		target_owner = document.get("owner")
		if document.get("status") != "ready" or document.get("visibility_status") != "visible":
			action = "delete"
	else:
		action = "delete"
	return create_search_index_job(
		target_doctype="AOS Short",
		target_name=short_id,
		target_owner=target_owner,
		index_kind="short",
		action=action,
		source=source,
		document=document,
		enqueue=enqueue,
	)


def _request_json(path: str, payload: dict[str, Any]) -> dict[str, Any]:
	config = get_search_ranking_config()
	body = _json_bytes(payload)
	headers = {
		"Content-Type": "application/json",
		"X-AOS-Search-Signature": build_signature(config.service_secret, body),
	}
	response = requests.post(
		f"{config.service_url}/{path.lstrip('/')}",
		data=body,
		headers=headers,
		timeout=config.request_timeout_seconds,
	)
	response.raise_for_status()
	data = response.json() if response.content else {}
	if not isinstance(data, dict) or data.get("ok") is not True:
		raise SearchRankingError(
			str(data.get("message") or data.get("error") or "Search/ranking service failed")
		)
	return data


def search_ad_candidates(
	*, q: str = "", filters: dict[str, Any] | None = None, limit: int = 20, offset: int = 0
) -> list[str]:
	config = get_search_ranking_config()
	if not config.enabled or not config.use_ads_search:
		raise SearchRankingError("Search/ranking ads search is disabled")
	data = _request_json("/ads/search", {"q": q, "filters": filters or {}, "limit": limit, "offset": offset})
	return [_clean(row.get("id")) for row in data.get("items") or [] if _clean(row.get("id"))]


def short_feed_candidates(
	*,
	viewer: str | None = None,
	content_mode: str | None = None,
	country: str | None = None,
	limit: int = 20,
	offset: int = 0,
) -> list[str]:
	config = get_search_ranking_config()
	if not config.enabled or not config.use_shorts_feed:
		raise SearchRankingError("Search/ranking shorts feed is disabled")
	data = _request_json(
		"/shorts/feed",
		{
			"viewer": viewer,
			"content_mode": content_mode,
			"country": country,
			"limit": limit,
			"offset": offset,
		},
	)
	return [_clean(row.get("id")) for row in data.get("items") or [] if _clean(row.get("id"))]


def related_ad_candidates(*, ad_id: str, limit: int = 20) -> list[str]:
	config = get_search_ranking_config()
	if not config.enabled:
		raise SearchRankingError("Search/ranking service is disabled")
	data = _request_json("/ads/related", {"ad_id": ad_id, "limit": limit})
	return [_clean(row.get("id")) for row in data.get("items") or [] if _clean(row.get("id"))]


def reindex_active_ads(limit: int = 1000, enqueue: bool = True) -> dict[str, Any]:
	rows = frappe.get_all(
		"AOS Ad",
		filters={"status": "Active"},
		fields=["name"],
		limit=max(1, int(limit)),
		order_by="modified desc",
	)
	queued = 0
	failed = 0
	for row in rows:
		try:
			enqueue_ad_search_index(row.name, source="manual_reindex", enqueue=enqueue)
			queued += 1
		except Exception:
			failed += 1
			frappe.log_error(frappe.get_traceback(), f"Search/ranking ad reindex failed: {row.name}")
	frappe.db.commit()
	return {"ok": failed == 0, "target": "ads", "scanned": len(rows), "queued": queued, "failed": failed}


def reindex_visible_shorts(limit: int = 1000, enqueue: bool = True) -> dict[str, Any]:
	rows = frappe.get_all(
		"AOS Short",
		filters={"status": "ready", "visibility_status": "visible"},
		fields=["name"],
		limit=max(1, int(limit)),
		order_by="modified desc",
	)
	queued = 0
	failed = 0
	for row in rows:
		try:
			enqueue_short_search_index(row.name, source="manual_reindex", enqueue=enqueue)
			queued += 1
		except Exception:
			failed += 1
			frappe.log_error(frappe.get_traceback(), f"Search/ranking short reindex failed: {row.name}")
	frappe.db.commit()
	return {"ok": failed == 0, "target": "shorts", "scanned": len(rows), "queued": queued, "failed": failed}

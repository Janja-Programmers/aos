from __future__ import annotations

import io
import json
import logging
import re
import time
from typing import Any

import requests
from minio import Minio
from PIL import Image

from app.config import get_settings
from app.durable_lifecycle import deliver_callback, execute_work_job
from app.queue import get_queue, get_redis
from app.security import build_signature

logger = logging.getLogger(__name__)


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


def _inspect_image_media(
	client: Minio, item: dict[str, Any], labels: set[str], reasons: list[str], scores: dict[str, float]
) -> None:
	settings = get_settings()
	size_bytes = int(item.get("size_bytes") or 0)
	content_type = str(item.get("content_type") or "").lower()

	# This worker performs byte-bounded image inspection only. Videos are
	# accepted according to the canonical Media purpose policy and are not
	# downloaded into memory here, so the image inspection cap must never
	# reject an otherwise valid ad video.
	if content_type.startswith("video/"):
		labels.add("video_present")
		scores.setdefault("video_present", 0.10)
		return

	if not content_type.startswith("image/"):
		return

	if size_bytes and size_bytes > settings.max_media_bytes:
		labels.add("media_too_large_for_moderation")
		reasons.append(f"Media {item.get('media_id')} exceeds moderation inspection limit.")
		scores["media_too_large_for_moderation"] = 0.60
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
	except Exception:
		logger.exception("Moderation media inspection failed")
		labels.add("media_inspection_failed")
		reasons.append("Media inspection failed.")
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
	elif any(
		label in labels
		for label in {
			"sensitive_text_term",
			"media_inspection_failed",
			"media_missing_storage_reference",
			"media_too_large_for_moderation",
			"low_resolution_image",
		}
	):
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


def _callback(callback_url: str, payload: dict[str, Any]) -> Any:
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
	return response


def _perform_moderation_work(payload: dict[str, Any]) -> dict[str, Any]:
	job_id = str(payload.get("job_id") or "").strip()
	callback_url = str(payload.get("callback_url") or "").strip()
	if not job_id or not callback_url:
		raise ModerationProcessingError("job_id and callback_url are required")

	try:
		result = _moderate(payload)
		callback_payload = {
			"job_id": job_id,
			"idempotency_key": payload.get("idempotency_key"),
			"dispatch_id": payload.get("dispatch_id"),
			"dispatch_generation": payload.get("dispatch_generation"),
			"dispatch_token": payload.get("dispatch_token"),
			"status": "completed",
			"target": payload.get("target") or {},
			**result,
		}
		return callback_payload
	except Exception:
		logger.exception("Moderation processing job failed")
		raise


def _moderation_failure_payload(payload: dict[str, Any], _error: str) -> dict[str, Any]:
	return {
		"job_id": payload.get("job_id"),
		"idempotency_key": payload.get("idempotency_key"),
		"dispatch_id": payload.get("dispatch_id"),
		"dispatch_generation": payload.get("dispatch_generation"),
		"dispatch_token": payload.get("dispatch_token"),
		"status": "failed",
		"error": "MODERATION_PROCESSING_FAILED",
	}


def process_moderation_job(payload: dict[str, Any]) -> dict[str, Any]:
	settings = get_settings()
	return execute_work_job(
		redis=get_redis(),
		queue=get_queue(),
		service_type="moderation",
		payload=payload,
		perform_work=_perform_moderation_work,
		failure_payload=_moderation_failure_payload,
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
		service_type="moderation",
		stable_id=stable_id,
		send_callback=_callback,
		result_ttl_seconds=int(getattr(settings, "durable_result_ttl_seconds", 604800)),
		callback_max_attempts=int(getattr(settings, "callback_max_attempts", 8)),
	)


def replay_callback(_callback_url: str, payload: dict[str, Any]) -> dict[str, Any]:
	"""Compatibility entry point: replay from the durable result, never from RQ result data."""
	stable_id = str(payload.get("idempotency_key") or payload.get("job_id") or "").strip()
	return deliver_callback_job(stable_id)


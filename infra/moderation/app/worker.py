from __future__ import annotations

import json
import logging
import time
from urllib.parse import urlparse
from typing import Any

import requests
from minio import Minio

from app.config import get_settings
from app.durable_lifecycle import deliver_callback, execute_work_job
from app.queue import get_queue, get_redis
from app.security import build_signature
from app.policy import POLICY_VERSION, evaluate_policy
from app.text_detector import detect_text
from app.vision_detector import VisionDetectorError, classify_images

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


def _moderate(payload: dict[str, Any]) -> dict[str, Any]:
	settings = get_settings()
	text_items = list(payload.get("text_items") or [])[: settings.max_text_items]
	media_items = list(payload.get("media_items") or [])[: settings.max_images + 4]
	signals, detector_failures = detect_text(text_items, max_chars=settings.max_text_chars)
	model_versions: dict[str, str] = {"text": "aos_text_rules:2"}
	missing_required_evidence: list[str] = []

	image_items = [item for item in media_items if str(item.get("content_type") or "").lower().startswith("image/")]
	video_items = [item for item in media_items if str(item.get("content_type") or "").lower().startswith("video/")]
	if image_items:
		if not settings.inspect_media:
			missing_required_evidence.append("image")
		else:
			try:
				vision_signals, versions = classify_images(client=_minio_client(), items=image_items, settings=settings)
				signals.extend(vision_signals)
				model_versions.update(versions)
			except Exception as exc:
				logger.warning("Vision moderation unavailable category=%s", exc.__class__.__name__)
				detector_failures.append("image")

	# Moderation never downloads/processes videos independently. Shorts can provide
	# representative storyboard/poster frames produced by hardened Video Processing.
	# A video without such authoritative visual evidence is held for human review.
	if video_items:
		fields = {str(item.get("field") or "").lower() for item in image_items}
		if not fields.intersection({"storyboard", "poster", "cover", "video_frame"}):
			missing_required_evidence.append("video_visual")
		context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
		if bool(context.get("transcript_expected")) and not bool(context.get("transcript_supplied")):
			missing_required_evidence.append("audio_transcript")

	result = evaluate_policy(
		signals,
		missing_required_evidence=missing_required_evidence,
		detector_failures=detector_failures,
	)
	category_scores: dict[str, float] = {}
	for signal in signals[:64]:
		category = str(signal.get("category") or "other")
		try:
			confidence = max(0.0, min(float(signal.get("confidence") or 0), 1.0))
		except (TypeError, ValueError):
			continue
		category_scores[category] = max(category_scores.get(category, 0.0), confidence)
	return {
		"decision": result.decision,
		"signals": signals[:64],
		"labels": list(result.categories),
		"scores": category_scores,
		"reasons": list(result.reasons),
		"risk_score": result.risk_score,
		"policy_version": POLICY_VERSION,
		"model_versions": model_versions,
		"missing_evidence": sorted(set(missing_required_evidence)),
	}


def _validate_callback_url(callback_url: str) -> str:
	settings = get_settings()
	parsed = urlparse(str(callback_url or "").strip())
	if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
		raise ModerationProcessingError("Invalid callback URL")
	if settings.environment.lower() in {"production", "staging"}:
		allowed = {str(host).strip().lower() for host in settings.callback_allowed_hosts if str(host).strip()}
		if parsed.scheme != "https" or not allowed or parsed.hostname.lower() not in allowed:
			raise ModerationProcessingError("Invalid callback URL")
	return parsed.geturl()


def _callback(callback_url: str, payload: dict[str, Any]) -> Any:
	settings = get_settings()
	callback_url = _validate_callback_url(callback_url)
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
		if str(payload.get("policy_version") or "").strip() != POLICY_VERSION:
			raise ModerationProcessingError("Moderation policy version mismatch")
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


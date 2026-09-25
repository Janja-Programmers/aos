from __future__ import annotations

from typing import Any

import requests

from fastapi import FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.durable_lifecycle import authorize_work_replay, job_status, replay_callback_delivery
from app.idempotent_dispatch import dispatch_details
from app.observability import dependency_ready, instrument_app, readiness_error
from app.queue import get_queue, get_redis
from app.security import verify_signature


class ModerationTarget(BaseModel):
	model_config = ConfigDict(extra="forbid")
	doctype: str = Field(min_length=1, max_length=140)
	name: str = Field(min_length=1, max_length=180)
	owner: str | None = Field(default=None, max_length=180)
	content_kind: str = Field(min_length=1, max_length=80)
	source: str = Field(default="", max_length=140)


class ModerationTextItem(BaseModel):
	model_config = ConfigDict(extra="forbid")
	field: str = Field(min_length=1, max_length=80)
	text: str = Field(min_length=1, max_length=100000)
	content_type: str = Field(default="text/plain", max_length=120)


class ModerationMediaItem(BaseModel):
	model_config = ConfigDict(extra="forbid")
	field: str = Field(min_length=1, max_length=80)
	media_id: str = Field(min_length=1, max_length=180)
	purpose: str = Field(default="", max_length=120)
	bucket: str = Field(min_length=1, max_length=180)
	object_key: str = Field(min_length=1, max_length=1024)
	content_type: str = Field(min_length=1, max_length=160)
	size_bytes: int = Field(default=0, ge=0, le=2_147_483_647)
	visibility: str = Field(default="", max_length=80)
	width: int = Field(default=0, ge=0, le=100000)
	height: int = Field(default=0, ge=0, le=100000)
	duration_seconds: float = Field(default=0, ge=0, le=86400)


class ModerationJobRequest(BaseModel):
	model_config = ConfigDict(extra="forbid")
	job_id: str = Field(min_length=1, max_length=180)
	idempotency_key: str | None = Field(default=None, min_length=8, max_length=200)
	dispatch_id: str | None = Field(default=None, min_length=8, max_length=200)
	dispatch_generation: int = Field(default=0, ge=0, le=1000)
	dispatch_token: str | None = Field(default=None, min_length=16, max_length=140, repr=False)
	target: ModerationTarget
	text_items: list[ModerationTextItem] = Field(default_factory=list, max_length=32)
	media_items: list[ModerationMediaItem] = Field(default_factory=list, max_length=20)
	context: dict[str, Any] = Field(default_factory=dict)
	content_version: str = Field(min_length=1, max_length=180)
	content_fingerprint: str = Field(min_length=32, max_length=128)
	policy_version: str = Field(min_length=1, max_length=140)
	callback_url: str = Field(min_length=1, max_length=2048)


class InternalJobLookupRequest(BaseModel):
	job_id: str = Field(min_length=1, max_length=200)
	idempotency_key: str | None = Field(default=None, min_length=8, max_length=200)


app = FastAPI(title="AOS Content Moderation Service", version="1.0.0")
instrument_app(app, "aos-moderation")


@app.get("/health")
def health():
	settings = get_settings()
	return {"ok": True, "service": settings.service_name, "environment": settings.environment}


@app.get("/ready")
def ready():
	settings = get_settings()
	try:
		get_redis().ping()
		dependency_ready("redis")
		if settings.inspect_media:
			if not settings.vision_secret or not settings.vision_ready_url:
				raise RuntimeError("vision moderation is not configured")
			response = requests.get(settings.vision_ready_url, timeout=min(settings.vision_timeout_seconds, 10))
			response.raise_for_status()
			data = response.json() if response.content else {}
			if not bool(data.get("ready", data.get("ok"))):
				raise RuntimeError("vision moderation provider is not ready")
			dependency_ready("vision")
		return {"ok": True, "ready": True}
	except Exception as exc:
		return readiness_error("moderation_dependencies", exc)


async def _verified_json(request: Request, signature: str | None) -> bytes:
	settings = get_settings()
	body = await request.body()
	if len(body) > settings.max_request_bytes:
		raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Request too large")
	if not verify_signature(settings.request_secret, body, signature):
		raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")
	return body


@app.post("/jobs", status_code=status.HTTP_202_ACCEPTED)
async def create_job(
	request: Request,
	x_aos_moderation_signature: str | None = Header(default=None),
):
	settings = get_settings()
	raw_body = await _verified_json(request, x_aos_moderation_signature)
	payload = ModerationJobRequest.model_validate_json(raw_body)
	queue = get_queue()
	decision = dispatch_details(payload.model_dump(), queue)
	rq_job = decision["job"]
	service_job_id = getattr(rq_job, "id", None) or payload.idempotency_key or payload.job_id
	return {
		"ok": True,
		"message": "Moderation job accepted.",
		"job_id": payload.job_id,
		"service_job_id": service_job_id,
		"dispatch_action": decision["dispatch_action"],
		"authoritative_generation": decision["authoritative_generation"],
		"work_state": decision["work_state"],
		"callback_state": decision["callback_state"],
		"terminal_result_type": decision.get("terminal_result_type") or None,
		"result_digest": decision.get("result_digest") or None,
		"queue": settings.queue_name,
	}


@app.post("/internal/jobs/status")
async def internal_job_status(request: Request, x_aos_moderation_signature: str | None = Header(default=None)):
	raw_body = await _verified_json(request, x_aos_moderation_signature)
	payload = InternalJobLookupRequest.model_validate_json(raw_body)
	stable_id = payload.idempotency_key or payload.job_id
	return job_status(get_redis(), "moderation", stable_id, get_queue())


@app.post("/internal/jobs/callback/replay", status_code=status.HTTP_202_ACCEPTED)
async def internal_callback_replay(request: Request, x_aos_moderation_signature: str | None = Header(default=None)):
	raw_body = await _verified_json(request, x_aos_moderation_signature)
	payload = InternalJobLookupRequest.model_validate_json(raw_body)
	stable_id = payload.idempotency_key or payload.job_id
	settings = get_settings()
	return replay_callback_delivery(
		redis=get_redis(),
		queue=get_queue(),
		service_type="moderation",
		stable_id=stable_id,
		callback_worker_method="app.worker.deliver_callback_job",
		callback_timeout_seconds=settings.callback_job_timeout_seconds,
		callback_max_attempts=settings.callback_max_attempts,
		result_ttl_seconds=settings.durable_result_ttl_seconds,
		failure_ttl_seconds=settings.failure_ttl_seconds,
	)


@app.post("/internal/jobs/work/replay", status_code=status.HTTP_202_ACCEPTED)
async def internal_work_replay(request: Request, x_aos_moderation_signature: str | None = Header(default=None)):
	raw_body = await _verified_json(request, x_aos_moderation_signature)
	payload = InternalJobLookupRequest.model_validate_json(raw_body)
	stable_id = payload.idempotency_key or payload.job_id
	settings = get_settings()
	return authorize_work_replay(
		redis=get_redis(),
		queue=get_queue(),
		service_type="moderation",
		stable_id=stable_id,
		result_ttl_seconds=settings.durable_result_ttl_seconds,
	)

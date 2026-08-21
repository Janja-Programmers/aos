from __future__ import annotations

import json
from urllib.parse import urlparse

from fastapi import FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import get_settings
from app.durable_lifecycle import authorize_work_replay, job_status, replay_callback_delivery
from app.idempotent_dispatch import dispatch_details
from app.observability import dependency_ready, instrument_app, readiness_error
from app.queue import get_queue, get_redis
from app.security import verify_signature


class StrictModel(BaseModel):
	model_config = ConfigDict(extra="forbid")


class ObjectInput(StrictModel):
	media_id: str | None = Field(default=None, max_length=140)
	bucket: str = Field(min_length=1, max_length=128)
	object_key: str = Field(min_length=1, max_length=1024)
	content_type: str | None = Field(default=None, max_length=255)
	size_bytes: int = Field(default=0, ge=0)
	filename: str | None = Field(default=None, max_length=255)

	@field_validator("bucket", "object_key")
	@classmethod
	def validate_storage_component(cls, value: str) -> str:
		clean = value.strip().strip("/")
		if not clean or ".." in clean.split("/") or "\\" in clean:
			raise ValueError("Invalid storage location")
		return clean


class SoundInput(ObjectInput):
	sound_id: str | None = Field(default=None, max_length=140)
	start_ms: int = Field(default=0, ge=0)
	duration_ms: int = Field(default=0, ge=0)
	volume: float = Field(default=1.0, ge=0.0, le=1.0)


class OutputConfig(StrictModel):
	output_bucket: str = Field(min_length=1, max_length=128)
	output_base_path: str = Field(min_length=1, max_length=512)
	thumbnail_bucket: str = Field(min_length=1, max_length=128)
	thumbnail_base_path: str = Field(min_length=1, max_length=512)
	# Optional for compatibility with dispatchers that predate reusable original
	# Sounds. When supplied, these are where the worker stores the extracted
	# creator audio that becomes an AOS Sound.
	sound_bucket: str | None = Field(default=None, min_length=1, max_length=128)
	sound_base_path: str | None = Field(default=None, min_length=1, max_length=512)
	max_duration_seconds: int = Field(ge=1, le=3600)

	@field_validator(
		"output_bucket",
		"output_base_path",
		"thumbnail_bucket",
		"thumbnail_base_path",
		"sound_bucket",
		"sound_base_path",
	)
	@classmethod
	def validate_output_component(cls, value: str | None) -> str | None:
		if value is None:
			return None
		clean = value.strip().strip("/")
		if not clean or ".." in clean.split("/") or "\\" in clean:
			raise ValueError("Invalid output location")
		return clean


class VideoJobRequest(StrictModel):
	job_id: str = Field(min_length=1, max_length=140)
	idempotency_key: str | None = Field(default=None, min_length=8, max_length=200)
	dispatch_id: str | None = Field(default=None, min_length=8, max_length=200)
	dispatch_generation: int = Field(default=0, ge=0, le=1000)
	dispatch_token: str | None = Field(default=None, min_length=16, max_length=140, repr=False)
	job_generation: int = Field(default=1, ge=1, le=1000000)
	short_id: str = Field(pattern=r"^SHORT-[0-9]{4}-[0-9]{5,}$")
	force: bool = False
	reason: str = Field(default="short_upload", pattern=r"^[a-z][a-z0-9_]{0,63}$")
	callback_url: str = Field(min_length=1, max_length=2048)
	raw_video: ObjectInput
	sound: SoundInput | None = None
	output: OutputConfig

	@field_validator("callback_url")
	@classmethod
	def validate_callback_url(cls, value: str) -> str:
		parsed = urlparse(value)
		if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
			raise ValueError("Invalid callback URL")
		settings = get_settings()
		if settings.environment.lower() in {"production", "staging"}:
			if parsed.scheme != "https":
				raise ValueError("Callback URL must use HTTPS")
			if not settings.callback_allowed_hosts:
				raise ValueError("Callback host allowlist is not configured")
		if settings.callback_allowed_hosts and parsed.hostname.lower() not in settings.callback_allowed_hosts:
			raise ValueError("Callback host is not allowed")
		return value


class InternalJobLookupRequest(StrictModel):
	job_id: str = Field(min_length=1, max_length=200)
	idempotency_key: str | None = Field(default=None, min_length=8, max_length=200)

app = FastAPI(title="AOS Video Processing Service", version="1.0.0")
instrument_app(app, "aos-video-processing")


@app.get("/health")
def health():
	settings = get_settings()
	return {
		"ok": True,
		"service": settings.service_name,
		"environment": settings.environment,
	}


@app.get("/ready")
def ready():
	try:
		redis = get_redis()
		redis.ping()
		dependency_ready("redis")
		return {"ok": True, "ready": True}
	except Exception as exc:
		return readiness_error("redis", exc)


async def _verified_json(request: Request, signature: str | None) -> bytes:
	body = await request.body()
	if not verify_signature(get_settings().request_secret, body, signature):
		raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")
	return body


@app.post("/jobs", status_code=status.HTTP_202_ACCEPTED)
async def create_job(
	request: Request,
	x_aos_signature: str | None = Header(default=None),
):
	settings = get_settings()
	raw_body = await request.body()
	if not verify_signature(settings.request_secret, raw_body, x_aos_signature):
		raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")
	payload = VideoJobRequest.model_validate_json(raw_body)
	queue = get_queue()
	decision = dispatch_details(payload.model_dump(), queue)
	rq_job = decision["job"]
	service_job_id = getattr(rq_job, "id", None) or payload.idempotency_key or payload.job_id
	return {
		"ok": True,
		"message": "Video job accepted.",
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
async def internal_job_status(request: Request, x_aos_signature: str | None = Header(default=None)):
	raw_body = await request.body()
	if not verify_signature(get_settings().request_secret, raw_body, x_aos_signature):
		raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")
	payload = InternalJobLookupRequest.model_validate_json(raw_body)
	stable_id = payload.idempotency_key or payload.job_id
	return job_status(get_redis(), "video_processing", stable_id, get_queue())


@app.post("/internal/jobs/callback/replay", status_code=status.HTTP_202_ACCEPTED)
async def internal_callback_replay(request: Request, x_aos_signature: str | None = Header(default=None)):
	raw_body = await request.body()
	if not verify_signature(get_settings().request_secret, raw_body, x_aos_signature):
		raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")
	payload = InternalJobLookupRequest.model_validate_json(raw_body)
	stable_id = payload.idempotency_key or payload.job_id
	settings = get_settings()
	return replay_callback_delivery(
		redis=get_redis(),
		queue=get_queue(),
		service_type="video_processing",
		stable_id=stable_id,
		callback_worker_method="app.worker.deliver_callback_job",
		callback_timeout_seconds=settings.callback_job_timeout_seconds,
		callback_max_attempts=settings.callback_max_attempts,
		result_ttl_seconds=settings.durable_result_ttl_seconds,
		failure_ttl_seconds=settings.failure_ttl_seconds,
	)


@app.post("/internal/jobs/work/replay", status_code=status.HTTP_202_ACCEPTED)
async def internal_work_replay(request: Request, x_aos_signature: str | None = Header(default=None)):
	raw_body = await _verified_json(request, x_aos_signature)
	payload = InternalJobLookupRequest.model_validate_json(raw_body)
	stable_id = payload.idempotency_key or payload.job_id
	settings = get_settings()
	return authorize_work_replay(
		redis=get_redis(),
		queue=get_queue(),
		service_type="video_processing",
		stable_id=stable_id,
		result_ttl_seconds=settings.durable_result_ttl_seconds,
	)

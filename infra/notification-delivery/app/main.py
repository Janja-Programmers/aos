from __future__ import annotations

import json
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.config import get_settings, validate_firebase_configuration
from app.durable_lifecycle import (
	authorize_work_replay,
	job_status,
	replay_callback_delivery,
	resolve_uncertain_outcome,
)
from app.idempotent_dispatch import dispatch_details
from app.observability import dependency_ready, instrument_app, readiness_error
from app.queue import get_queue, get_redis
from app.security import verify_signature


class _StrictModel(BaseModel):
	model_config = ConfigDict(extra="forbid")


class PushToken(_StrictModel):
	token: str = Field(min_length=20, max_length=4096, repr=False)
	token_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
	device_type: Literal["android", "ios", "web"]
	registration_kind: Literal["token", "fid"]

	@field_validator("token")
	@classmethod
	def _validate_token(cls, value: str) -> str:
		if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in value):
			raise ValueError("invalid token format")
		return value


class PushOptions(_StrictModel):
	priority: Literal["high", "normal"] | None = None
	ttl_seconds: int | None = Field(default=None, ge=0, le=86400)
	android_channel_id: str | None = Field(default=None, min_length=1, max_length=100)
	android_notification_priority: Literal["min", "low", "default", "high", "max"] | None = None


class NotificationDeliveryJobRequest(_StrictModel):
	job_id: str = Field(min_length=1, max_length=200)
	idempotency_key: str | None = Field(default=None, min_length=8, max_length=200)
	dispatch_id: str | None = Field(default=None, min_length=8, max_length=240)
	dispatch_generation: int = Field(default=0, ge=0, le=1000)
	dispatch_token: str | None = Field(default=None, min_length=16, max_length=180, repr=False)
	notification_id: str | None = Field(default=None, max_length=180)
	delivery_kind: Literal["persistent", "transient"] = "persistent"
	channel: Literal["push"] = "push"
	user: str = Field(min_length=1, max_length=180)
	event: str = Field(min_length=1, max_length=80)
	title: str = Field(min_length=1, max_length=140)
	body: str = Field(min_length=1, max_length=500)
	data: dict[str, str] = Field(default_factory=dict)
	options: PushOptions = Field(default_factory=PushOptions)
	tokens: list[PushToken] = Field(default_factory=list, max_length=500)
	callback_url: str = Field(min_length=1, max_length=1000)

	@field_validator("callback_url")
	@classmethod
	def _validate_callback_url(cls, value: str) -> str:
		parsed = urlsplit(value)
		if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
			raise ValueError("invalid callback URL")
		return value

	@model_validator(mode="after")
	def _validate_contract(self):
		if self.delivery_kind == "persistent" and not self.notification_id:
			raise ValueError("persistent delivery requires notification_id")
		if self.delivery_kind == "transient":
			if self.notification_id:
				raise ValueError("transient delivery cannot reference notification_id")
			if self.event != "aos_incoming_call":
				raise ValueError("unsupported transient event")
		encoded = json.dumps(self.data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
		if len(encoded) > 3500:
			raise ValueError("notification data is too large")
		estimated = json.dumps(
			{
				"notification": {"title": self.title, "body": self.body},
				"data": self.data,
			},
			separators=(",", ":"),
			ensure_ascii=False,
		).encode("utf-8")
		if len(estimated) > 3500:
			raise ValueError("notification envelope is too large")
		return self


class InternalJobLookupRequest(_StrictModel):
	job_id: str = Field(min_length=1, max_length=200)
	idempotency_key: str | None = Field(default=None, min_length=8, max_length=200)


class UncertaintyResolutionRequest(InternalJobLookupRequest):
	resolution: str = Field(pattern="^(confirmed_accepted|confirmed_failed|approved_resend|permanently_unresolved)$")
	terminal_payload: dict[str, Any] | None = None
	operator_reference: str | None = Field(default=None, max_length=200)


app = FastAPI(title="AOS Notifications Delivery Companion", version="1.0.0")
instrument_app(app, "aos-notification-delivery")


@app.get("/health")
def health():
	settings = get_settings()
	return {"ok": True, "service": settings.service_name, "environment": settings.environment}


@app.get("/ready")
def ready():
	try:
		redis = get_redis()
		redis.ping()
		dependency_ready("redis")
	except Exception as exc:
		return readiness_error("redis", exc)
	try:
		validate_firebase_configuration(get_settings())
		dependency_ready("firebase_config")
	except Exception as exc:
		return readiness_error("firebase_config", exc)
	return {"ok": True, "ready": True}


async def _verified_json(request: Request, signature: str | None) -> bytes:
	settings = get_settings()
	body = await request.body()
	if not verify_signature(settings.request_secret, body, signature):
		raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")
	return body


@app.post("/jobs", status_code=status.HTTP_202_ACCEPTED)
async def create_job(
	request: Request,
	x_aos_notification_signature: str | None = Header(default=None),
):
	raw_body = await _verified_json(request, x_aos_notification_signature)
	payload = NotificationDeliveryJobRequest.model_validate_json(raw_body)
	queue = get_queue()
	decision = dispatch_details(payload.model_dump(), queue)
	rq_job = decision["job"]
	service_job_id = getattr(rq_job, "id", None) or payload.idempotency_key or payload.job_id
	return {
		"ok": True,
		"message": "Notification delivery job accepted.",
		"job_id": payload.job_id,
		"service_job_id": service_job_id,
		"dispatch_action": decision["dispatch_action"],
		"authoritative_generation": decision["authoritative_generation"],
		"work_state": decision["work_state"],
		"callback_state": decision["callback_state"],
		"terminal_result_type": decision.get("terminal_result_type") or None,
		"result_digest": decision.get("result_digest") or None,
	}


@app.post("/internal/jobs/status")
async def internal_job_status(request: Request, x_aos_notification_signature: str | None = Header(default=None)):
	raw_body = await _verified_json(request, x_aos_notification_signature)
	payload = InternalJobLookupRequest.model_validate_json(raw_body)
	stable_id = payload.idempotency_key or payload.job_id
	return job_status(get_redis(), "notification_delivery", stable_id, get_queue())


@app.post("/internal/jobs/callback/replay", status_code=status.HTTP_202_ACCEPTED)
async def internal_callback_replay(request: Request, x_aos_notification_signature: str | None = Header(default=None)):
	raw_body = await _verified_json(request, x_aos_notification_signature)
	payload = InternalJobLookupRequest.model_validate_json(raw_body)
	stable_id = payload.idempotency_key or payload.job_id
	settings = get_settings()
	return replay_callback_delivery(
		redis=get_redis(),
		queue=get_queue(),
		service_type="notification_delivery",
		stable_id=stable_id,
		callback_worker_method="app.worker.deliver_callback_job",
		callback_timeout_seconds=settings.callback_job_timeout_seconds,
		callback_max_attempts=settings.callback_max_attempts,
		result_ttl_seconds=settings.durable_result_ttl_seconds,
		failure_ttl_seconds=settings.failure_ttl_seconds,
	)


@app.post("/internal/jobs/uncertainty/resolve", status_code=status.HTTP_202_ACCEPTED)
async def internal_uncertainty_resolve(
	request: Request,
	x_aos_notification_signature: str | None = Header(default=None),
):
	raw_body = await _verified_json(request, x_aos_notification_signature)
	payload = UncertaintyResolutionRequest.model_validate_json(raw_body)
	stable_id = payload.idempotency_key or payload.job_id
	settings = get_settings()
	return resolve_uncertain_outcome(
		redis=get_redis(),
		queue=get_queue(),
		service_type="notification_delivery",
		stable_id=stable_id,
		resolution=payload.resolution,
		terminal_payload=payload.terminal_payload,
		operator_reference=payload.operator_reference,
		callback_worker_method="app.worker.deliver_callback_job",
		callback_timeout_seconds=settings.callback_job_timeout_seconds,
		callback_max_attempts=settings.callback_max_attempts,
		result_ttl_seconds=settings.durable_result_ttl_seconds,
		failure_ttl_seconds=settings.failure_ttl_seconds,
	)


@app.post("/internal/jobs/work/replay", status_code=status.HTTP_202_ACCEPTED)
async def internal_work_replay(request: Request, x_aos_notification_signature: str | None = Header(default=None)):
	raw_body = await _verified_json(request, x_aos_notification_signature)
	payload = InternalJobLookupRequest.model_validate_json(raw_body)
	stable_id = payload.idempotency_key or payload.job_id
	settings = get_settings()
	return authorize_work_replay(
		redis=get_redis(),
		queue=get_queue(),
		service_type="notification_delivery",
		stable_id=stable_id,
		result_ttl_seconds=settings.durable_result_ttl_seconds,
	)

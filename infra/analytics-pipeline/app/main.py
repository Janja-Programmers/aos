from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.config import get_settings
from app.durable_lifecycle import authorize_work_replay, job_status, replay_callback_delivery
from app.idempotent_dispatch import dispatch_details
from app.observability import dependency_ready, instrument_app, readiness_error
from app.queue import get_queue, get_redis
from app.security import verify_signature


class AnalyticsEvent(BaseModel):
	event_id: str | None = Field(default=None, min_length=8, max_length=200)
	event_type: str = Field(min_length=1, max_length=120)
	event_group: str | None = None
	user: str | None = None
	session_id: str | None = Field(default=None, repr=False)
	source: str | None = None
	platform: str | None = None
	country: str | None = None
	target_doctype: str | None = None
	target_name: str | None = None
	route_type: str | None = None
	route_id: str | None = None
	occurred_at: str | None = None
	event_date: str | None = None
	metadata: dict[str, Any] | None = None
	metrics: dict[str, Any] | None = None


class AnalyticsIngestJobRequest(BaseModel):
	job_id: str = Field(min_length=1)
	idempotency_key: str | None = Field(default=None, min_length=8, max_length=200)
	dispatch_id: str | None = Field(default=None, min_length=8, max_length=200)
	dispatch_generation: int = Field(default=0, ge=0, le=1000)
	dispatch_token: str | None = Field(default=None, min_length=16, max_length=140, repr=False)
	source: str | None = None
	events: list[AnalyticsEvent] = Field(default_factory=list)
	callback_url: str = Field(min_length=1)


class InternalJobLookupRequest(BaseModel):
	job_id: str = Field(min_length=1, max_length=200)
	idempotency_key: str | None = Field(default=None, min_length=8, max_length=200)


app = FastAPI(title="AOS Analytics Pipeline Service", version="1.0.0")
instrument_app(app, "aos-analytics-pipeline")


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
		return {"ok": True, "ready": True}
	except Exception as exc:
		return readiness_error("redis", exc)


async def _verified_json(request: Request, signature: str | None) -> bytes:
	settings = get_settings()
	body = await request.body()
	if not verify_signature(settings.request_secret, body, signature):
		raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")
	return body


@app.post("/events", status_code=status.HTTP_202_ACCEPTED)
async def create_events_job(
	request: Request,
	x_aos_analytics_signature: str | None = Header(default=None),
):
	settings = get_settings()
	raw_body = await _verified_json(request, x_aos_analytics_signature)
	payload = AnalyticsIngestJobRequest.model_validate_json(raw_body)
	if len(payload.events) > settings.max_events_per_job:
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail=f"Too many events. Maximum is {settings.max_events_per_job}.",
		)
	queue = get_queue()
	decision = dispatch_details(payload.model_dump(), queue)
	rq_job = decision["job"]
	service_job_id = getattr(rq_job, "id", None) or payload.idempotency_key or payload.job_id
	return {
		"ok": True,
		"message": "Analytics ingest job accepted.",
		"job_id": payload.job_id,
		"service_job_id": service_job_id,
		"dispatch_action": decision["dispatch_action"],
		"authoritative_generation": decision["authoritative_generation"],
		"work_state": decision["work_state"],
		"callback_state": decision["callback_state"],
		"terminal_result_type": decision.get("terminal_result_type") or None,
		"result_digest": decision.get("result_digest") or None,

	}

@app.get("/metrics/summary")
def metrics_summary(
	date: str = Query(..., min_length=10, max_length=10),
	target_doctype: str | None = Query(default=None),
	target_name: str | None = Query(default=None),
):
	redis = get_redis()
	if target_doctype and target_name:
		key = f"aos:analytics:target:{target_doctype}:{target_name}:{date}"
	else:
		key = f"aos:analytics:day:{date}"

	raw = redis.hgetall(key)
	counters = {(k.decode("utf-8") if isinstance(k, bytes) else str(k)): int(v) for k, v in raw.items()}
	return {"ok": True, "date": date, "key": key, "counters": counters}


@app.post("/internal/jobs/status")
async def internal_job_status(request: Request, x_aos_analytics_signature: str | None = Header(default=None)):
	raw_body = await _verified_json(request, x_aos_analytics_signature)
	payload = InternalJobLookupRequest.model_validate_json(raw_body)
	stable_id = payload.idempotency_key or payload.job_id
	return job_status(get_redis(), "analytics_ingestion", stable_id, get_queue())


@app.post("/internal/jobs/callback/replay", status_code=status.HTTP_202_ACCEPTED)
async def internal_callback_replay(request: Request, x_aos_analytics_signature: str | None = Header(default=None)):
	raw_body = await _verified_json(request, x_aos_analytics_signature)
	payload = InternalJobLookupRequest.model_validate_json(raw_body)
	stable_id = payload.idempotency_key or payload.job_id
	settings = get_settings()
	return replay_callback_delivery(
		redis=get_redis(),
		queue=get_queue(),
		service_type="analytics_ingestion",
		stable_id=stable_id,
		callback_worker_method="app.worker.deliver_callback_job",
		callback_timeout_seconds=settings.callback_job_timeout_seconds,
		callback_max_attempts=settings.callback_max_attempts,
		result_ttl_seconds=settings.durable_result_ttl_seconds,
		failure_ttl_seconds=settings.failure_ttl_seconds,
	)


@app.post("/internal/jobs/work/replay", status_code=status.HTTP_202_ACCEPTED)
async def internal_work_replay(request: Request, x_aos_analytics_signature: str | None = Header(default=None)):
	raw_body = await _verified_json(request, x_aos_analytics_signature)
	payload = InternalJobLookupRequest.model_validate_json(raw_body)
	stable_id = payload.idempotency_key or payload.job_id
	settings = get_settings()
	return authorize_work_replay(
		redis=get_redis(),
		queue=get_queue(),
		service_type="analytics_ingestion",
		stable_id=stable_id,
		result_ttl_seconds=settings.durable_result_ttl_seconds,
	)

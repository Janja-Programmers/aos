from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from rq import Retry

from app.config import get_settings
from app.queue import get_queue, get_redis
from app.security import verify_signature


class AnalyticsEvent(BaseModel):
    event_type: str = Field(min_length=1, max_length=120)
    event_group: str | None = None
    user: str | None = None
    session_id: str | None = None
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
    source: str | None = None
    events: list[AnalyticsEvent] = Field(default_factory=list)
    callback_url: str = Field(min_length=1)


app = FastAPI(title="AOS Analytics Pipeline Service", version="1.0.0")


@app.get("/health")
def health():
    settings = get_settings()
    return {"ok": True, "service": settings.service_name, "environment": settings.environment}


@app.get("/ready")
def ready():
    try:
        redis = get_redis()
        redis.ping()
        return {"ok": True, "ready": True}
    except Exception as exc:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"ok": False, "ready": False, "error": str(exc)},
        )


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
    rq_job = queue.enqueue(
        "app.worker.process_analytics_ingest_job",
        payload.model_dump(),
        job_id=payload.job_id,
        job_timeout=settings.job_timeout_seconds,
        result_ttl=settings.result_ttl_seconds,
        failure_ttl=settings.failure_ttl_seconds,
        retry=Retry(max=3, interval=[60, 300, 900]),
    )
    return {
        "ok": True,
        "message": "Analytics ingest job accepted.",
        "job_id": payload.job_id,
        "service_job_id": rq_job.id,
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
    counters = {
        (k.decode("utf-8") if isinstance(k, bytes) else str(k)): int(v)
        for k, v in raw.items()
    }
    return {"ok": True, "date": date, "key": key, "counters": counters}

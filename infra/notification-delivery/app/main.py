from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from rq import Retry

from app.config import get_settings
from app.queue import get_queue, get_redis
from app.security import verify_signature


class NotificationDeliveryJobRequest(BaseModel):
    job_id: str = Field(min_length=1)
    notification_id: str | None = None
    delivery_kind: str = "persistent"
    channel: str = "push"
    user: str = Field(min_length=1)
    event: str | None = None
    title: str = ""
    body: str = ""
    data: dict[str, Any] | None = None
    options: dict[str, Any] | None = None
    tokens: list[dict[str, Any]] = Field(default_factory=list)
    callback_url: str = Field(min_length=1)


app = FastAPI(title="AOS Notification Delivery Service", version="1.0.0")


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


@app.post("/jobs", status_code=status.HTTP_202_ACCEPTED)
async def create_job(
    request: Request,
    x_aos_notification_signature: str | None = Header(default=None),
):
    settings = get_settings()
    raw_body = await _verified_json(request, x_aos_notification_signature)
    payload = NotificationDeliveryJobRequest.model_validate_json(raw_body)
    queue = get_queue()
    rq_job = queue.enqueue(
        "app.worker.process_notification_delivery_job",
        payload.model_dump(),
        job_id=payload.job_id,
        job_timeout=settings.job_timeout_seconds,
        result_ttl=settings.result_ttl_seconds,
        failure_ttl=settings.failure_ttl_seconds,
        retry=Retry(max=3, interval=[60, 300, 900]),
    )
    return {
        "ok": True,
        "message": "Notification delivery job accepted.",
        "job_id": payload.job_id,
        "service_job_id": rq_job.id,
    }

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from rq import Retry

from app.config import get_settings
from app.queue import get_queue, get_redis
from app.security import verify_signature


class VideoJobRequest(BaseModel):
    job_id: str = Field(min_length=1)
    short_id: str = Field(min_length=1)
    force: bool = False
    callback_url: str = Field(min_length=1)
    raw_video: dict[str, Any]
    sound: dict[str, Any] | None = None
    output: dict[str, Any] | None = None


app = FastAPI(title="AOS Video Processing Service", version="1.0.0")


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
        return {"ok": True, "ready": True}
    except Exception as exc:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"ok": False, "ready": False, "error": str(exc)},
        )


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
    rq_job = queue.enqueue(
        "app.worker.process_video_job",
        payload.model_dump(),
        job_id=payload.job_id,
        job_timeout=settings.job_timeout_seconds,
        result_ttl=settings.result_ttl_seconds,
        failure_ttl=settings.failure_ttl_seconds,
        retry=Retry(max=3, interval=[60, 300, 900]),
    )

    return {
        "ok": True,
        "message": "Video job accepted.",
        "job_id": payload.job_id,
        "service_job_id": rq_job.id,
        "queue": settings.queue_name,
    }

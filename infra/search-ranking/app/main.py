from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from rq import Retry

from app.config import get_settings
from app.queue import get_queue, get_redis
from app.security import verify_signature
from app.store import feed_shorts, related_ads, search_ads


class SearchRankingJobRequest(BaseModel):
    job_id: str = Field(min_length=1)
    action: str = "upsert"
    target: dict[str, Any]
    document: dict[str, Any] | None = None
    callback_url: str = Field(min_length=1)


app = FastAPI(title="AOS Search Ranking Service", version="1.0.0")


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
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"ok": False, "ready": False, "error": str(exc)})


async def _verified_json(request: Request, signature: str | None) -> bytes:
    settings = get_settings()
    body = await request.body()
    if not verify_signature(settings.request_secret, body, signature):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")
    return body


@app.post("/jobs", status_code=status.HTTP_202_ACCEPTED)
async def create_job(request: Request, x_aos_search_signature: str | None = Header(default=None)):
    settings = get_settings()
    raw_body = await _verified_json(request, x_aos_search_signature)
    payload = SearchRankingJobRequest.model_validate_json(raw_body)
    queue = get_queue()
    rq_job = queue.enqueue(
        "app.worker.process_search_ranking_job",
        payload.model_dump(),
        job_id=payload.job_id,
        job_timeout=settings.job_timeout_seconds,
        result_ttl=settings.result_ttl_seconds,
        failure_ttl=settings.failure_ttl_seconds,
        retry=Retry(max=3, interval=[60, 300, 900]),
    )
    return {"ok": True, "message": "Search/ranking job accepted.", "job_id": payload.job_id, "service_job_id": rq_job.id}


@app.post("/ads/search")
async def ads_search(request: Request, x_aos_search_signature: str | None = Header(default=None)):
    raw_body = await _verified_json(request, x_aos_search_signature)
    try:
        import json
        payload = json.loads(raw_body.decode("utf-8") or "{}")
    except Exception:
        payload = {}
    return search_ads(get_redis(), payload)


@app.post("/ads/related")
async def ads_related(request: Request, x_aos_search_signature: str | None = Header(default=None)):
    raw_body = await _verified_json(request, x_aos_search_signature)
    try:
        import json
        payload = json.loads(raw_body.decode("utf-8") or "{}")
    except Exception:
        payload = {}
    return related_ads(get_redis(), payload)


@app.post("/shorts/feed")
async def shorts_feed(request: Request, x_aos_search_signature: str | None = Header(default=None)):
    raw_body = await _verified_json(request, x_aos_search_signature)
    try:
        import json
        payload = json.loads(raw_body.decode("utf-8") or "{}")
    except Exception:
        payload = {}
    return feed_shorts(get_redis(), payload)

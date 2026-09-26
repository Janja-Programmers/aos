from __future__ import annotations

import time
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .config import get_settings
from .runtime import TextSafetyBusyError, TextSafetyError, get_runtime
from .security import verify_signature


class TextItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=20000)


class ClassificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[TextItem] = Field(min_length=1, max_length=32)


class Signal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str = Field(min_length=1, max_length=80)
    category: str = Field(min_length=1, max_length=80)
    severity: Literal["low", "medium", "high", "critical"]
    confidence: float = Field(ge=0.0, le=1.0)


class ClassificationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ready"]
    signals: list[Signal] = Field(default_factory=list, max_length=64)
    model: str = Field(min_length=1, max_length=140)
    model_version: str = Field(min_length=1, max_length=140)


app = FastAPI(title="AOS Text Safety", version="1.0.0")


@app.get("/health")
def health():
    settings = get_settings()
    return {"ok": True, "service": settings.service_name, "environment": settings.environment}


@app.get("/ready")
def ready():
    settings = get_settings()
    if not settings.internal_secret:
        raise HTTPException(status_code=503, detail="Internal authentication is not configured")
    try:
        runtime = get_runtime()
        runtime.load()
        return {
            "ok": True,
            "ready": True,
            "service": settings.service_name,
            "model": settings.model_name,
            "model_version": settings.model_version,
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Text safety model is unavailable") from exc


@app.post("/internal/moderation/classify-text", response_model=ClassificationResponse, include_in_schema=False)
async def classify_text(
    request: Request,
    x_aos_timestamp: str | None = Header(default=None),
    x_aos_signature: str | None = Header(default=None),
):
    settings = get_settings()
    body = await request.body()
    try:
        timestamp = int(str(x_aos_timestamp or "").strip())
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Invalid signature") from exc
    if abs(int(time.time()) - timestamp) > 300:
        raise HTTPException(status_code=401, detail="Invalid signature")
    signed = str(timestamp).encode("ascii") + b"." + body
    if not verify_signature(settings.internal_secret, signed, x_aos_signature):
        raise HTTPException(status_code=401, detail="Invalid signature")
    try:
        payload = ClassificationRequest.model_validate_json(body)
        items = [item.model_dump() for item in payload.items[: settings.max_items]]
        return get_runtime().classify(items)
    except TextSafetyBusyError as exc:
        raise HTTPException(status_code=503, detail="Text safety service is busy") from exc
    except TextSafetyError as exc:
        raise HTTPException(status_code=503, detail="Text safety classification unavailable") from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Invalid text safety request") from exc

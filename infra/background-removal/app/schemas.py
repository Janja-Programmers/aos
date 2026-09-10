from __future__ import annotations

from pydantic import BaseModel


class HealthResponse(BaseModel):
    ok: bool
    service: str
    mode: str
    processor_loaded: bool = False
    config: dict


class ReadyResponse(BaseModel):
    ok: bool
    service: str
    mode: str
    ready: bool
    processor_loaded: bool
    config: dict

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class ImageReference(BaseModel):
    image_url: str | HttpUrl = Field(..., min_length=1)
    is_primary: bool = False
    sort_order: int = Field(default=0, ge=0)

    @field_validator("image_url")
    @classmethod
    def validate_image_url(cls, value):
        value = str(value or "").strip()
        if not value:
            raise ValueError("image_url is required")
        return value


class ReplaceImagesRequest(BaseModel):
    images: list[ImageReference] = Field(default_factory=list)


class ReplaceImagesResponse(BaseModel):
    ok: bool
    ad_id: str
    indexed_count: int = 0
    failed_count: int = 0
    message: str | None = None


class DeleteVectorsResponse(BaseModel):
    ok: bool
    ad_id: str
    deleted: bool
    message: str | None = None


class ImageMatch(BaseModel):
    ad_id: str
    score: float
    matched_image_url: str | None = None
    is_primary: bool = False


class ImageSearchResponse(BaseModel):
    ok: bool
    items: list[ImageMatch] = Field(default_factory=list)
    message: str | None = None


class HealthResponse(BaseModel):
    ok: bool
    service: str
    mode: str
    model_loaded: bool = False
    vector_store_ready: bool = False
    config: dict


class ReadyResponse(BaseModel):
    ok: bool
    service: str
    mode: str
    ready: bool
    model_loaded: bool
    vector_store_ready: bool
    config: dict


class ShortFrameClassificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    frames: list[str] = Field(min_length=1, max_length=8)

    @field_validator("frames")
    @classmethod
    def validate_encoded_frames(cls, frames: list[str]) -> list[str]:
        total = 0
        for frame in frames:
            if not isinstance(frame, str) or not frame:
                raise ValueError("Frame is required")
            # 1 MiB decoded is at most ~1.4 MiB base64; allow bounded headroom
            # for configurable deployments while rejecting memory-amplification.
            if len(frame) > 7_000_000:
                raise ValueError("Encoded frame is too large")
            total += len(frame)
            if total > 12_000_000:
                raise ValueError("Encoded frames are too large")
        return frames


class ShortFrameClassificationScores(BaseModel):
    model_config = ConfigDict(extra="forbid")
    shop: float = Field(ge=0.0, le=1.0)
    geo: float = Field(ge=0.0, le=1.0)
    vibes: float = Field(ge=0.0, le=1.0)
    learn: float = Field(ge=0.0, le=1.0)


class ShortFrameClassificationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ready"]
    mode: Literal["shop", "geo", "vibes", "learn"]
    confidence: float = Field(ge=0.0, le=1.0)
    margin: float = Field(ge=0.0, le=1.0)
    scores: ShortFrameClassificationScores
    model: str = Field(min_length=1, max_length=140)
    model_version: str = Field(min_length=1, max_length=140)
    frame_count: int = Field(ge=1, le=8)

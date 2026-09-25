from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class ImageReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    media_id: str = Field(..., min_length=1, max_length=180)
    image_url: str | HttpUrl = Field(..., min_length=1)
    is_primary: bool = False
    sort_order: int = Field(default=0, ge=0, le=999)

    @field_validator("media_id", "image_url")
    @classmethod
    def validate_required_text(cls, value):
        value = str(value or "").strip()
        if not value:
            raise ValueError("value is required")
        return value


class ReplaceImagesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generation: str = Field(..., min_length=1, max_length=180)
    images: list[ImageReference] = Field(default_factory=list, max_length=16)


class ReplaceImagesResponse(BaseModel):
    ok: bool
    ad_id: str
    generation: str
    embedding_version: str
    indexed_count: int = 0
    message: str | None = None


class DeleteVectorsResponse(BaseModel):
    ok: bool
    ad_id: str
    deleted: bool
    message: str | None = None


class ImageMatch(BaseModel):
    ad_id: str
    score: float
    matched_media_id: str | None = None
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

class SafetyImageClassificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    images: list[str] = Field(min_length=1, max_length=8)

    @field_validator("images")
    @classmethod
    def validate_encoded_images(cls, images: list[str]) -> list[str]:
        total = 0
        for image in images:
            if not isinstance(image, str) or not image:
                raise ValueError("Image is required")
            if len(image) > 7_000_000:
                raise ValueError("Encoded image is too large")
            total += len(image)
            if total > 12_000_000:
                raise ValueError("Encoded images are too large")
        return images


class SafetySignal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: str = Field(min_length=1, max_length=80)
    confidence: float = Field(ge=0.0, le=1.0)
    severity: Literal["low", "medium", "high", "critical"]


class SafetyImageClassificationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ready"]
    signals: list[SafetySignal] = Field(default_factory=list, max_length=32)
    safe_confidence: float = Field(ge=0.0, le=1.0)
    top_category: str = Field(min_length=1, max_length=80)
    top_confidence: float = Field(ge=0.0, le=1.0)
    margin: float = Field(ge=0.0, le=1.0)
    model: str = Field(min_length=1, max_length=140)
    model_version: str = Field(min_length=1, max_length=140)
    image_count: int = Field(ge=1, le=8)

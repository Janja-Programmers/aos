from __future__ import annotations

from pydantic import BaseModel, Field, HttpUrl, field_validator


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

from __future__ import annotations

import traceback

from fastapi import FastAPI, File, Form, HTTPException, Path, UploadFile

from .config import get_settings
from .image_loader import ImageLoadError
from .embedding import EmbeddingError
from .qdrant_store import VectorStoreError
from .schemas import (
    DeleteVectorsResponse,
    HealthResponse,
    ImageSearchResponse,
    ReadyResponse,
    ReplaceImagesRequest,
    ReplaceImagesResponse,
)
from .service import get_service


app = FastAPI(
    title="AOS Image Search Service",
    version="1.0.0",
)


def _clean_ad_id(ad_id: str) -> str:
    ad_id = str(ad_id or "").strip()
    if not ad_id:
        raise HTTPException(status_code=400, detail="ad_id is required")
    return ad_id


def _handle_known_error(exc: Exception) -> None:
    if isinstance(exc, ImageLoadError):
        raise HTTPException(status_code=400, detail=str(exc))

    if isinstance(exc, EmbeddingError):
        raise HTTPException(status_code=503, detail=str(exc))

    if isinstance(exc, VectorStoreError):
        raise HTTPException(status_code=503, detail=str(exc))

    traceback.print_exc()
    raise HTTPException(status_code=500, detail=f"Image search service error: {exc}")


@app.get("/health", response_model=HealthResponse)
def health():
    return get_service().health()


@app.get("/ready", response_model=ReadyResponse)
def ready():
    try:
        return get_service().ready()
    except Exception as exc:
        _handle_known_error(exc)


@app.post(
    "/ads/{ad_id}/replace-images",
    response_model=ReplaceImagesResponse,
)
def replace_ad_images(
    payload: ReplaceImagesRequest,
    ad_id: str = Path(..., min_length=1),
):
    try:
        return get_service().replace_ad_images(
            ad_id=_clean_ad_id(ad_id),
            images=payload.images,
        )
    except Exception as exc:
        _handle_known_error(exc)


@app.delete(
    "/ads/{ad_id}/vectors",
    response_model=DeleteVectorsResponse,
)
def delete_ad_vectors(ad_id: str = Path(..., min_length=1)):
    try:
        return get_service().delete_ad_vectors(ad_id=_clean_ad_id(ad_id))
    except Exception as exc:
        _handle_known_error(exc)


@app.post("/search/image", response_model=ImageSearchResponse)
def search_by_image(
    image: UploadFile = File(...),
    limit: int | None = Form(None),
):
    settings = get_settings()

    if not image.filename:
        raise HTTPException(status_code=400, detail="Image filename is required")

    if image.content_type and not image.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Uploaded file must be an image")

    try:
        return get_service().search_by_image(
            image_file=image.file,
            filename=image.filename,
            content_type=image.content_type,
            limit=settings.clamp_limit(limit),
        )
    except Exception as exc:
        _handle_known_error(exc)

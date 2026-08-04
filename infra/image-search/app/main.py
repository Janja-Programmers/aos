from __future__ import annotations

import base64
import binascii
import logging
import time
from io import BytesIO

from fastapi import FastAPI, File, Form, Header, HTTPException, Path, Request, UploadFile

from .config import get_settings
from .embedding import EmbeddingError
from .image_loader import ImageLoadError, load_image_from_file
from .observability import instrument_app, readiness_error
from .qdrant_store import VectorStoreError
from .schemas import (
	DeleteVectorsResponse,
	HealthResponse,
	ImageSearchResponse,
	ReadyResponse,
	ReplaceImagesRequest,
	ReplaceImagesResponse,
	ShortFrameClassificationRequest,
	ShortFrameClassificationResponse,
)
from .service import get_service
from .security import verify_signature
from .short_classification import ShortClassificationError, get_short_classifier

app = FastAPI(
	title="AOS Image Search Service",
	version="1.0.0",
)
instrument_app(app, "aos-image-search")
LOGGER = logging.getLogger(__name__)


def _clean_ad_id(ad_id: str) -> str:
	ad_id = str(ad_id or "").strip()
	if not ad_id:
		raise HTTPException(status_code=400, detail="ad_id is required")
	return ad_id


def _handle_known_error(exc: Exception) -> None:
	if isinstance(exc, ImageLoadError):
		raise HTTPException(status_code=400, detail="The supplied image is invalid.")

	if isinstance(exc, EmbeddingError):
		raise HTTPException(status_code=503, detail="Image embedding is temporarily unavailable.")

	if isinstance(exc, VectorStoreError):
		raise HTTPException(status_code=503, detail="Vector storage is temporarily unavailable.")

	LOGGER.exception("Image search request failed")
	raise HTTPException(status_code=500, detail="Image search service error.")


@app.get("/health", response_model=HealthResponse)
def health():
	return get_service().health()


@app.get("/ready", response_model=ReadyResponse)
def ready():
	try:
		return get_service().ready()
	except Exception as exc:
		return readiness_error("image_search_dependencies", exc)


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


@app.post(
	"/internal/shorts/classify-frames",
	response_model=ShortFrameClassificationResponse,
	include_in_schema=False,
)
async def classify_short_frames(
	request: Request,
	x_aos_signature: str | None = Header(default=None),
	x_aos_timestamp: str | None = Header(default=None),
):
	settings = get_settings()
	raw_body = await request.body()
	try:
		timestamp = int(str(x_aos_timestamp or "").strip())
	except (TypeError, ValueError) as exc:
		raise HTTPException(status_code=401, detail="Invalid signature") from exc
	if abs(int(time.time()) - timestamp) > 300:
		raise HTTPException(status_code=401, detail="Invalid signature")
	signed_payload = str(timestamp).encode("ascii") + b"." + raw_body
	if not verify_signature(settings.internal_secret or "", signed_payload, x_aos_signature):
		raise HTTPException(status_code=401, detail="Invalid signature")

	try:
		payload = ShortFrameClassificationRequest.model_validate_json(raw_body)
	except Exception as exc:
		raise HTTPException(status_code=422, detail="Invalid classification request") from exc

	if len(payload.frames) > settings.short_classification_max_frames:
		raise HTTPException(status_code=413, detail="Too many frames")

	images = []
	total_bytes = 0
	for index, encoded in enumerate(payload.frames):
		try:
			raw = base64.b64decode(str(encoded or ""), validate=True)
		except (binascii.Error, ValueError) as exc:
			raise HTTPException(status_code=400, detail="Invalid frame encoding") from exc
		if not raw or len(raw) > settings.short_classification_max_frame_bytes:
			raise HTTPException(status_code=413, detail="Frame exceeds configured limit")
		total_bytes += len(raw)
		if total_bytes > settings.short_classification_max_total_bytes:
			raise HTTPException(status_code=413, detail="Frames exceed configured limit")
		try:
			images.append(
				load_image_from_file(
					BytesIO(raw),
					settings=settings,
					source_label=f"short-frame-{index}",
				)
			)
		except ImageLoadError as exc:
			raise HTTPException(status_code=400, detail="Invalid frame image") from exc

	try:
		return get_short_classifier().classify(images)
	except (EmbeddingError, ShortClassificationError) as exc:
		raise HTTPException(status_code=503, detail="Short classification is unavailable") from exc

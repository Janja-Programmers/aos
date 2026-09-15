from __future__ import annotations

import asyncio
import hmac
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from .config import get_settings
from .image_loader import ImageLoadError
from .observability import instrument_app, readiness_error
from .processor import (
    BackgroundRemovalBusyError,
    BackgroundRemovalProcessorError,
    BackgroundRemovalRuntimeError,
)
from .schemas import HealthResponse, ReadyResponse
from .service import get_service

LOGGER = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Warm the immutable model before this replica becomes ready."""
    try:
        await asyncio.to_thread(get_service().warmup)
    except Exception:
        # Keep liveness available for diagnostics. /ready remains fail-closed
        # and retries model initialization on subsequent probes.
        LOGGER.exception("Background-removal startup warmup failed")
    yield


app = FastAPI(
    title="AOS Background Removal Service",
    version="1.0.0",
    lifespan=lifespan,
)
instrument_app(app, "aos-background-removal")


def _json_error(
    status_code: int,
    *,
    message: str,
    code: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"ok": False, "message": message, "code": code},
        headers=headers,
    )


def _handle_known_error(exc: Exception) -> JSONResponse:
    if isinstance(exc, ImageLoadError):
        return _json_error(400, message="The supplied image is invalid.", code="INVALID_IMAGE")
    if isinstance(exc, BackgroundRemovalBusyError):
        return _json_error(
            503,
            message="Background removal is temporarily busy.",
            code="BACKGROUND_REMOVAL_BUSY",
            headers={"Retry-After": "1"},
        )
    if isinstance(exc, BackgroundRemovalRuntimeError):
        return _json_error(
            503,
            message="Background removal is temporarily unavailable.",
            code="BACKGROUND_REMOVAL_UNAVAILABLE",
        )
    if isinstance(exc, BackgroundRemovalProcessorError):
        return _json_error(
            422,
            message="Background removal could not process the image.",
            code="BACKGROUND_REMOVAL_FAILED",
        )
    LOGGER.exception("Background removal request failed")
    return _json_error(
        500,
        message="Background removal service error.",
        code="BACKGROUND_REMOVAL_SERVICE_ERROR",
    )


def _authorize_processor_request(request: Request) -> JSONResponse | None:
    expected = str(get_settings().service_secret or "").strip()
    if not expected:
        return _json_error(
            503,
            message="Background removal is temporarily unavailable.",
            code="BACKGROUND_REMOVAL_UNAVAILABLE",
        )
    supplied = str(request.headers.get("authorization") or "").strip()
    prefix = "Bearer "
    token = supplied[len(prefix):].strip() if supplied.startswith(prefix) else ""
    if not token or not hmac.compare_digest(token, expected):
        return _json_error(401, message="Authentication is required.", code="AUTH_REQUIRED")
    return None


@app.middleware("http")
async def protect_processor_boundary(request: Request, call_next):
    """Reject untrusted/obviously oversized inference requests before body parsing."""
    if request.url.path == "/remove-background":
        denied = _authorize_processor_request(request)
        if denied is not None:
            return denied

        raw_length = str(request.headers.get("content-length") or "").strip()
        if raw_length:
            try:
                content_length = int(raw_length)
            except ValueError:
                return _json_error(400, message="Invalid request metadata.", code="VALIDATION_ERROR")
            request_limit = get_settings().max_image_bytes + 1024 * 1024
            if content_length < 0 or content_length > request_limit:
                return _json_error(
                    413,
                    message="The supplied image is too large.",
                    code="FILE_TOO_LARGE",
                )

    return await call_next(request)


@app.get("/health", response_model=HealthResponse)
def health():
    return get_service().health()


@app.get("/ready", response_model=ReadyResponse)
def ready():
    try:
        if not get_settings().service_secret:
            raise RuntimeError("processor authentication is not configured")
        return get_service().ready()
    except Exception as exc:
        return readiness_error("model_runtime", exc)


@app.post("/remove-background")
def remove_background(image: UploadFile = File(...)):
    if not image.filename:
        raise HTTPException(status_code=400, detail="Image filename is required")
    if image.content_type and not image.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Uploaded file must be an image")
    try:
        output_png = get_service().remove_background(
            image_file=image.file,
            filename=image.filename,
        )
    except Exception as exc:
        return _handle_known_error(exc)
    return Response(
        content=output_png,
        media_type="image/png",
        headers={
            "Content-Disposition": 'inline; filename="background-removed.png"',
            "X-AOS-Output-Format": "png",
        },
    )

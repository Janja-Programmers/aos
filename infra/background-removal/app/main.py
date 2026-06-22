from __future__ import annotations

import traceback

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response

from .image_loader import ImageLoadError
from .processor import BackgroundRemovalProcessorError, BackgroundRemovalRuntimeError
from .schemas import HealthResponse, ReadyResponse
from .service import get_service


app = FastAPI(
    title="AOS Background Removal Service",
    version="1.0.0",
)


def _json_error(status_code: int, *, message: str, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "ok": False,
            "message": message,
            "code": code,
        },
    )


def _handle_known_error(exc: Exception) -> JSONResponse:
    if isinstance(exc, ImageLoadError):
        return _json_error(400, message=str(exc), code="INVALID_IMAGE")

    if isinstance(exc, BackgroundRemovalRuntimeError):
        return _json_error(
            503,
            message="Background removal is temporarily unavailable.",
            code="BACKGROUND_REMOVAL_UNAVAILABLE",
        )

    if isinstance(exc, BackgroundRemovalProcessorError):
        return _json_error(
            422,
            message=str(exc),
            code="BACKGROUND_REMOVAL_FAILED",
        )

    traceback.print_exc()
    return _json_error(
        500,
        message="Background removal service error.",
        code="BACKGROUND_REMOVAL_SERVICE_ERROR",
    )


@app.get("/health", response_model=HealthResponse)
def health():
    return get_service().health()


@app.get("/ready", response_model=ReadyResponse)
def ready():
    try:
        return get_service().ready()
    except Exception as exc:
        return _handle_known_error(exc)


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
            content_type=image.content_type,
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

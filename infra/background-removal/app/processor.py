from __future__ import annotations

import os
from io import BytesIO
from typing import Any

from PIL import Image, UnidentifiedImageError

from .config import Settings


class BackgroundRemovalProcessorError(RuntimeError):
    pass


class BackgroundRemovalRuntimeError(BackgroundRemovalProcessorError):
    pass


class BackgroundRemovalProcessor:
    """Background-removal processor boundary.

    This module owns rembg/ONNX runtime details so model code stays outside the
    Frappe business backend and outside FastAPI route handlers.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._session: Any | None = None
        self._loaded = False

    @property
    def loaded(self) -> bool:
        return self._loaded

    def ready(self) -> bool:
        self.ensure_loaded()
        return self.loaded

    def ensure_loaded(self) -> None:
        if self._loaded and self._session is not None:
            return

        if self.settings.model_path:
            # rembg/onnxruntime uses U2NET_HOME for model cache/discovery.
            # This lets ops mount a persistent model directory later without
            # changing application code.
            os.environ["U2NET_HOME"] = self.settings.model_path

        try:
            from rembg import new_session

            providers = ["CPUExecutionProvider"]
            try:
                self._session = new_session(
                    self.settings.model_name,
                    providers=providers,
                )
            except TypeError:
                # Older/newer rembg versions may not expose the providers kwarg.
                self._session = new_session(self.settings.model_name)

            self._loaded = True
        except Exception as exc:  # pragma: no cover - runtime dependency path
            raise BackgroundRemovalRuntimeError(
                f"Failed to initialize background-removal model '{self.settings.model_name}'."
            ) from exc

    def remove_background(self, image_bytes: bytes) -> bytes:
        if not image_bytes:
            raise BackgroundRemovalProcessorError("Image bytes are empty.")

        self.ensure_loaded()

        try:
            from rembg import remove

            try:
                output = remove(
                    image_bytes,
                    session=self._session,
                    force_return_bytes=True,
                )
            except TypeError:
                output = remove(image_bytes, session=self._session)

            return _normalize_png_bytes(output)
        except BackgroundRemovalProcessorError:
            raise
        except Exception as exc:  # pragma: no cover - model/runtime failure path
            raise BackgroundRemovalRuntimeError(
                "Failed to remove image background."
            ) from exc


def _normalize_png_bytes(output: Any) -> bytes:
    """Normalize rembg output to RGBA PNG bytes."""
    if isinstance(output, Image.Image):
        image = output
    elif isinstance(output, (bytes, bytearray)):
        try:
            image = Image.open(BytesIO(bytes(output)))
            image.load()
        except UnidentifiedImageError as exc:
            raise BackgroundRemovalProcessorError(
                "Background-removal output was not a valid image."
            ) from exc
    else:
        raise BackgroundRemovalProcessorError(
            f"Unsupported background-removal output type: {type(output).__name__}"
        )

    buffer = BytesIO()
    image.convert("RGBA").save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()

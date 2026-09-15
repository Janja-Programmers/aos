from __future__ import annotations

import os
import threading
from io import BytesIO
from typing import Any

from PIL import Image, UnidentifiedImageError

from .config import Settings
from .model_artifact import verify_model_artifact


class BackgroundRemovalProcessorError(RuntimeError):
    pass


class BackgroundRemovalRuntimeError(BackgroundRemovalProcessorError):
    pass


class BackgroundRemovalBusyError(BackgroundRemovalProcessorError):
    pass


class BackgroundRemovalProcessor:
    """Owns the immutable rembg/ONNX runtime boundary for this service."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._session: Any | None = None
        self._loaded = False
        self._load_lock = threading.Lock()
        self._inference_slots = threading.BoundedSemaphore(settings.max_concurrent_inferences)

    @property
    def loaded(self) -> bool:
        return self._loaded

    def ready(self) -> bool:
        self.ensure_loaded()
        return self.loaded

    def ensure_loaded(self) -> None:
        if self._loaded and self._session is not None:
            return

        with self._load_lock:
            if self._loaded and self._session is not None:
                return
            try:
                model_path = verify_model_artifact()
                os.environ["REMBG_HOME"] = str(self.settings.rembg_home)

                from rembg import new_session

                self._session = new_session(
                    self.settings.model_name,
                    providers=["CPUExecutionProvider"],
                )
                self._loaded = True
            except Exception as exc:  # pragma: no cover - runtime dependency path
                self._session = None
                self._loaded = False
                raise BackgroundRemovalRuntimeError(
                    f"Failed to initialize background-removal model '{self.settings.model_name}'."
                ) from exc

            # Defensive invariant: rembg must have consumed the exact file that
            # AOS verified. Its current U2Net session resolves from REMBG_HOME.
            if not model_path.is_file():  # pragma: no cover - filesystem race defense
                self._session = None
                self._loaded = False
                raise BackgroundRemovalRuntimeError("Verified background-removal model disappeared during startup.")

    def remove_background(self, image_bytes: bytes) -> bytes:
        if not image_bytes:
            raise BackgroundRemovalProcessorError("Image bytes are empty.")

        acquired = self._inference_slots.acquire(
            timeout=self.settings.inference_acquire_timeout_seconds
        )
        if not acquired:
            raise BackgroundRemovalBusyError("Background-removal inference capacity is saturated.")

        try:
            self.ensure_loaded()
            from rembg import remove

            output = remove(
                image_bytes,
                session=self._session,
                force_return_bytes=True,
            )
            return _normalize_png_bytes(output)
        except BackgroundRemovalProcessorError:
            raise
        except Exception as exc:  # pragma: no cover - model/runtime failure path
            raise BackgroundRemovalRuntimeError(
                "Failed to remove image background."
            ) from exc
        finally:
            self._inference_slots.release()


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

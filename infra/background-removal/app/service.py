from __future__ import annotations

from functools import lru_cache
from typing import BinaryIO

from .config import get_settings
from .image_loader import load_image_bytes, validate_image_bytes
from .processor import BackgroundRemovalProcessor


class BackgroundRemovalService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.processor = BackgroundRemovalProcessor(self.settings)

    def health(self) -> dict:
        return {
            "ok": True,
            "service": "aos-background-removal",
            "mode": "ai",
            "processor_loaded": self.processor.loaded,
            "config": self.settings.public_dict(),
        }

    def ready(self) -> dict:
        ready = self.processor.ready()
        return {
            "ok": True,
            "service": "aos-background-removal",
            "mode": "ai",
            "ready": ready,
            "processor_loaded": self.processor.loaded,
            "config": self.settings.public_dict(),
        }

    def remove_background(
        self,
        *,
        image_file: BinaryIO,
        filename: str,
    ) -> bytes:
        source_label = filename or "uploaded image"
        raw = load_image_bytes(
            image_file,
            settings=self.settings,
            source_label=source_label,
        )
        validate_image_bytes(raw, source_label=source_label, settings=self.settings)
        return self.processor.remove_background(raw)


@lru_cache(maxsize=1)
def get_service() -> BackgroundRemovalService:
    return BackgroundRemovalService()

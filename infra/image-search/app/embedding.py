from __future__ import annotations

import threading
from typing import Any

from PIL import Image

from .config import Settings, get_settings


class EmbeddingError(RuntimeError):
    pass


class EmbeddingRuntime:
    """Lazy OpenCLIP runtime.

    Model loading is intentionally owned by this external service, not by the
    Frappe business backend.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._model: Any | None = None
        self._preprocess: Any | None = None
        self._tokenizer: Any | None = None
        self._resolved_device: str | None = None
        self._lock = threading.Lock()

    @property
    def is_loaded(self) -> bool:
        return self._model is not None and self._preprocess is not None

    @property
    def device(self) -> str:
        return self._resolved_device or self.settings.device

    def load(self) -> None:
        with self._lock:
            if self.is_loaded:
                return

            try:
                import open_clip
                import torch
            except Exception as exc:
                raise EmbeddingError(
                    "OpenCLIP/Torch dependencies are not installed in image-search service."
                ) from exc

            requested_device = (self.settings.device or "cpu").strip().lower()
            if requested_device == "auto":
                requested_device = "cuda" if torch.cuda.is_available() else "cpu"

            if requested_device.startswith("cuda") and not torch.cuda.is_available():
                raise EmbeddingError(
                    "IMAGE_SEARCH_DEVICE is set to CUDA, but CUDA is not available."
                )

            try:
                model, _, preprocess = open_clip.create_model_and_transforms(
                    self.settings.model_name,
                    pretrained=self.settings.pretrained,
                )
                model = model.to(requested_device)
                model.eval()

                self._model = model
                self._preprocess = preprocess
                self._resolved_device = requested_device
            except Exception as exc:
                raise EmbeddingError("Failed to load OpenCLIP image model.") from exc

    def generate_image_embedding(self, image: Image.Image) -> list[float]:
        self.load()

        assert self._model is not None
        assert self._preprocess is not None

        try:
            import torch

            image_input = self._preprocess(image).unsqueeze(0).to(self.device)

            with torch.no_grad():
                image_features = self._model.encode_image(image_input)
                image_features = image_features / image_features.norm(
                    dim=-1,
                    keepdim=True,
                )

            vector = image_features.squeeze(0).detach().cpu().tolist()

            if not vector:
                raise EmbeddingError("Generated image embedding is empty.")

            return [float(v) for v in vector]

        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError("Failed to generate image embedding.") from exc

    def generate_text_embedding(self, text: str) -> list[float]:
        """Reserved for future hybrid search.

        Kept here because the old backend had this helper, but no current AOS
        business endpoint should call it directly.
        """
        clean_text = str(text or "").strip()
        if not clean_text:
            raise EmbeddingError("Text is required.")

        self.load()

        assert self._model is not None

        try:
            import open_clip
            import torch

            if self._tokenizer is None:
                self._tokenizer = open_clip.get_tokenizer(self.settings.model_name)

            text_tokens = self._tokenizer([clean_text]).to(self.device)

            with torch.no_grad():
                text_features = self._model.encode_text(text_tokens)
                text_features = text_features / text_features.norm(
                    dim=-1,
                    keepdim=True,
                )

            vector = text_features.squeeze(0).detach().cpu().tolist()

            if not vector:
                raise EmbeddingError("Generated text embedding is empty.")

            return [float(v) for v in vector]

        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError("Failed to generate text embedding.") from exc


_runtime: EmbeddingRuntime | None = None
_runtime_lock = threading.Lock()


def get_embedding_runtime() -> EmbeddingRuntime:
    global _runtime

    if _runtime is None:
        with _runtime_lock:
            if _runtime is None:
                _runtime = EmbeddingRuntime(get_settings())

    return _runtime

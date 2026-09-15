from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import BinaryIO

from .config import Settings, get_settings
from .embedding import EmbeddingRuntime, get_embedding_runtime
from .image_loader import load_image_from_file, load_image_from_url
from .qdrant_store import ImageVector, QdrantImageStore, VectorSearchHit, get_store
from .schemas import DeleteVectorsResponse, ImageMatch, ImageReference, ImageSearchResponse, ReplaceImagesResponse


@dataclass
class _RankedMatch:
    ad_id: str
    score: float
    matched_media_id: str | None
    is_primary: bool


logger = logging.getLogger(__name__)


class ImageSearchService:
    """Derived visual index. Frappe owns all Ad eligibility and public projection."""

    def __init__(
        self,
        settings: Settings | None = None,
        embedding_runtime: EmbeddingRuntime | None = None,
        store: QdrantImageStore | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.embedding_runtime = embedding_runtime or get_embedding_runtime()
        self.store = store or get_store()

    def health(self) -> dict:
        return {
            "ok": True,
            "service": self.settings.service_name,
            "mode": "ai",
            "model_loaded": self.embedding_runtime.is_loaded,
            "vector_store_ready": False,
            "config": self.settings.public_dict(),
        }

    def ready(self) -> dict:
        self.embedding_runtime.load()
        self.store.ensure_collection()
        return {
            "ok": True,
            "service": self.settings.service_name,
            "mode": "ai",
            "ready": True,
            "model_loaded": self.embedding_runtime.is_loaded,
            "vector_store_ready": True,
            "config": self.settings.public_dict(),
        }

    def replace_ad_images(self, *, ad_id: str, generation: str, images: list[ImageReference]) -> ReplaceImagesResponse:
        clean_ad_id = self._required(ad_id, "ad_id")
        clean_generation = self._required(generation, "generation")
        vectors: list[ImageVector] = []
        # Build the whole new generation before touching Qdrant. A single failed
        # image leaves the previous generation intact and can be retried safely.
        for image_ref in images or []:
            image = load_image_from_url(str(image_ref.image_url).strip(), settings=self.settings)
            vector = self.embedding_runtime.generate_image_embedding(image)
            vectors.append(
                ImageVector(
                    ad_id=clean_ad_id,
                    media_id=str(image_ref.media_id).strip(),
                    generation=clean_generation,
                    embedding_version=self.settings.embedding_version,
                    vector=vector,
                    is_primary=bool(image_ref.is_primary),
                    sort_order=int(image_ref.sort_order or 0),
                )
            )
        indexed_count = self.store.replace_ad_vectors(
            ad_id=clean_ad_id,
            generation=clean_generation,
            vectors=vectors,
        )
        return ReplaceImagesResponse(
            ok=True,
            ad_id=clean_ad_id,
            generation=clean_generation,
            embedding_version=self.settings.embedding_version,
            indexed_count=indexed_count,
            message="Image vectors replaced.",
        )

    def delete_ad_vectors(self, *, ad_id: str) -> DeleteVectorsResponse:
        clean_ad_id = self._required(ad_id, "ad_id")
        self.store.delete_ad_vectors(ad_id=clean_ad_id)
        return DeleteVectorsResponse(ok=True, ad_id=clean_ad_id, deleted=True, message="Vectors deleted.")

    def search_by_image(
        self,
        *,
        image_file: BinaryIO,
        filename: str,
        content_type: str | None,
        limit: int | None,
    ) -> ImageSearchResponse:
        search_limit = self.settings.clamp_limit(limit)
        image = load_image_from_file(image_file, settings=self.settings, source_label=filename or "uploaded image")
        vector = self.embedding_runtime.generate_image_embedding(image)
        # Headroom is bounded. Frappe will remove stale/ineligible candidates
        # and perform final geographic reranking.
        hits = self.store.search(vector=vector, limit=min(self.settings.max_limit, max(search_limit * 3, search_limit)))
        ranked = self._group_and_rank(hits)
        return ImageSearchResponse(
            ok=True,
            items=[
                ImageMatch(
                    ad_id=item.ad_id,
                    score=item.score,
                    matched_media_id=item.matched_media_id,
                    is_primary=item.is_primary,
                )
                for item in ranked[:search_limit]
            ],
            message="Search successful.",
        )

    def _group_and_rank(self, hits: list[VectorSearchHit]) -> list[_RankedMatch]:
        best_by_ad: dict[str, _RankedMatch] = {}
        for hit in hits or []:
            if not hit.ad_id:
                continue
            raw_score = float(hit.score or 0)
            if raw_score < self.settings.score_threshold:
                continue
            adjusted_score = raw_score + (self.settings.primary_boost if hit.is_primary else 0.0)
            current = best_by_ad.get(hit.ad_id)
            if current is None or adjusted_score > current.score:
                best_by_ad[hit.ad_id] = _RankedMatch(
                    ad_id=hit.ad_id,
                    score=round(adjusted_score, 6),
                    matched_media_id=hit.media_id,
                    is_primary=hit.is_primary,
                )
        return sorted(best_by_ad.values(), key=lambda item: (-item.score, item.ad_id))

    @staticmethod
    def _required(value: str, field: str) -> str:
        clean = str(value or "").strip()
        if not clean:
            raise ValueError(f"{field} is required")
        return clean


_service: ImageSearchService | None = None


def get_service() -> ImageSearchService:
    global _service
    if _service is None:
        _service = ImageSearchService()
    return _service

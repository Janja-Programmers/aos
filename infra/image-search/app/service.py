from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import BinaryIO

from .config import Settings, get_settings
from .embedding import EmbeddingRuntime, get_embedding_runtime
from .image_loader import load_image_from_file, load_image_from_url
from .qdrant_store import ImageVector, QdrantImageStore, VectorSearchHit, get_store
from .schemas import (
	DeleteVectorsResponse,
	ImageMatch,
	ImageReference,
	ImageSearchResponse,
	ReplaceImagesResponse,
)


@dataclass
class _RankedMatch:
	ad_id: str
	score: float
	matched_image_url: str | None
	is_primary: bool


logger = logging.getLogger(__name__)


class ImageSearchService:
	"""Application service for visual similarity search.

	The Frappe backend owns ads and permissions. This service owns only the AI
	image embeddings and vector search index.
	"""

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

	def replace_ad_images(
		self,
		*,
		ad_id: str,
		images: list[ImageReference],
	) -> ReplaceImagesResponse:
		clean_ad_id = self._clean_ad_id(ad_id)

		vectors: list[ImageVector] = []
		failed_count = 0

		for image_ref in images or []:
			try:
				image_url = str(image_ref.image_url).strip()
				image = load_image_from_url(image_url, settings=self.settings)
				vector = self.embedding_runtime.generate_image_embedding(image)

				vectors.append(
					ImageVector(
						ad_id=clean_ad_id,
						image_url=image_url,
						vector=vector,
						is_primary=bool(image_ref.is_primary),
						sort_order=int(image_ref.sort_order or 0),
					)
				)
			except Exception as exc:
				# Per-image failures should not prevent good images from being indexed.
				failed_count += 1
				logger.warning(
					"Failed to index image for ad_id=%s image_url=%s: %s",
					clean_ad_id,
					getattr(image_ref, "image_url", None),
					exc,
				)

		indexed_count = self.store.replace_ad_vectors(
			ad_id=clean_ad_id,
			vectors=vectors,
		)

		message = None
		if failed_count:
			message = f"{failed_count} image(s) failed to index."

		return ReplaceImagesResponse(
			ok=True,
			ad_id=clean_ad_id,
			indexed_count=indexed_count,
			failed_count=failed_count,
			message=message,
		)

	def delete_ad_vectors(self, *, ad_id: str) -> DeleteVectorsResponse:
		clean_ad_id = self._clean_ad_id(ad_id)
		self.store.delete_ad_vectors(ad_id=clean_ad_id)

		return DeleteVectorsResponse(
			ok=True,
			ad_id=clean_ad_id,
			deleted=True,
			message="Vectors deleted.",
		)

	def search_by_image(
		self,
		*,
		image_file: BinaryIO,
		filename: str,
		content_type: str | None,
		limit: int | None,
	) -> ImageSearchResponse:
		search_limit = self.settings.clamp_limit(limit)
		image = load_image_from_file(
			image_file,
			settings=self.settings,
			source_label=filename or "uploaded image",
		)
		vector = self.embedding_runtime.generate_image_embedding(image)
		hits = self.store.search(vector=vector, limit=search_limit)
		ranked = self._group_and_rank(hits)

		return ImageSearchResponse(
			ok=True,
			items=[
				ImageMatch(
					ad_id=item.ad_id,
					score=item.score,
					matched_image_url=item.matched_image_url,
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

			adjusted_score = raw_score
			if hit.is_primary:
				adjusted_score += self.settings.primary_boost

			current = best_by_ad.get(hit.ad_id)
			if current is None or adjusted_score > current.score:
				best_by_ad[hit.ad_id] = _RankedMatch(
					ad_id=hit.ad_id,
					score=round(adjusted_score, 6),
					matched_image_url=hit.image_url,
					is_primary=hit.is_primary,
				)

		return sorted(
			best_by_ad.values(),
			key=lambda item: item.score,
			reverse=True,
		)

	def _clean_ad_id(self, ad_id: str) -> str:
		clean = str(ad_id or "").strip()
		if not clean:
			raise ValueError("ad_id is required")
		return clean


_service: ImageSearchService | None = None


def get_service() -> ImageSearchService:
	global _service

	if _service is None:
		_service = ImageSearchService()

	return _service

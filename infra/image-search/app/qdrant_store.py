from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

from .config import Settings, get_settings


class VectorStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class ImageVector:
    ad_id: str
    media_id: str
    generation: str
    embedding_version: str
    vector: list[float]
    is_primary: bool = False
    sort_order: int = 0


@dataclass(frozen=True)
class VectorSearchHit:
    ad_id: str
    media_id: str | None
    is_primary: bool
    sort_order: int
    score: float
    payload: dict[str, Any]


class QdrantImageStore:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._client: QdrantClient | None = None
        self._collection_ready = False

    @property
    def client(self) -> QdrantClient:
        if self._client is None:
            self._client = QdrantClient(
                url=self.settings.qdrant_url,
                api_key=self.settings.qdrant_api_key,
                timeout=self.settings.request_timeout_seconds,
            )
        return self._client

    def is_ready(self) -> bool:
        try:
            self.ensure_collection()
            return True
        except Exception:
            return False

    def ensure_collection(self) -> None:
        if self._collection_ready:
            return
        try:
            collections = self.client.get_collections().collections
            existing = {collection.name for collection in collections}
            if self.settings.collection not in existing:
                self.client.create_collection(
                    collection_name=self.settings.collection,
                    vectors_config=VectorParams(size=self.settings.vector_size, distance=Distance.COSINE),
                )
            else:
                info = self.client.get_collection(self.settings.collection)
                vectors = getattr(getattr(getattr(info, "config", None), "params", None), "vectors", None)
                size = getattr(vectors, "size", None)
                distance = getattr(vectors, "distance", None)
                if size is not None and int(size) != int(self.settings.vector_size):
                    raise VectorStoreError("Qdrant collection vector size does not match configured embedding model.")
                if distance is not None and str(distance).lower().split(".")[-1] != "cosine":
                    raise VectorStoreError("Qdrant collection distance does not match canonical schema.")
            for field in ("ad_id", "media_id", "generation", "embedding_version", "schema_version"):
                try:
                    self.client.create_payload_index(
                        collection_name=self.settings.collection,
                        field_name=field,
                        field_schema=PayloadSchemaType.KEYWORD,
                        wait=True,
                    )
                except Exception:
                    # Qdrant returns an error if an equivalent payload index already exists.
                    pass
            self._collection_ready = True
        except VectorStoreError:
            raise
        except Exception as exc:
            raise VectorStoreError("Failed to ensure Qdrant collection.") from exc


    def _generations_for_ad(self, ad_id: str) -> set[str]:
        """Return the small set of generations currently indexed for an Ad."""
        self.ensure_collection()
        try:
            points, _next = self.client.scroll(
                collection_name=self.settings.collection,
                scroll_filter=Filter(must=[FieldCondition(key="ad_id", match=MatchValue(value=ad_id))]),
                limit=64,
                with_payload=["generation"],
                with_vectors=False,
            )
        except Exception as exc:
            raise VectorStoreError("Failed to inspect image vector generations.") from exc
        return {
            str((getattr(point, "payload", None) or {}).get("generation") or "").strip()
            for point in (points or [])
            if str((getattr(point, "payload", None) or {}).get("generation") or "").strip()
        }

    def _delete_generation(self, *, ad_id: str, generation: str) -> None:
        try:
            self.client.delete(
                collection_name=self.settings.collection,
                points_selector=FilterSelector(
                    filter=Filter(
                        must=[
                            FieldCondition(key="ad_id", match=MatchValue(value=ad_id)),
                            FieldCondition(key="generation", match=MatchValue(value=generation)),
                        ]
                    )
                ),
                wait=True,
            )
        except Exception as exc:
            raise VectorStoreError("Failed to delete stale image vector generation.") from exc

    def replace_ad_vectors(self, *, ad_id: str, generation: str, vectors: list[ImageVector]) -> int:
        """Promote one deterministic generation without allowing stale retries to win.

        Generations begin with a fixed-width source modification timestamp. The
        service checks existing generations before and after upsert so an older
        delayed job cannot delete a newer generation during concurrent retries.
        """
        self.ensure_collection()
        if not vectors:
            self.delete_ad_vectors(ad_id=ad_id)
            return 0
        existing = self._generations_for_ad(ad_id)
        if existing and max(existing) > generation:
            return 0
        points = [
            PointStruct(
                id=self._stable_point_id(item),
                vector=item.vector,
                payload={
                    "ad_id": item.ad_id,
                    "media_id": item.media_id,
                    "generation": item.generation,
                    "embedding_version": item.embedding_version,
                    "schema_version": self.settings.collection_schema_version,
                    "is_primary": bool(item.is_primary),
                    "sort_order": int(item.sort_order or 0),
                },
            )
            for item in vectors
        ]
        try:
            self.client.upsert(collection_name=self.settings.collection, points=points, wait=True)
            generations = self._generations_for_ad(ad_id)
            newest = max(generations) if generations else generation
            if newest > generation:
                self._delete_generation(ad_id=ad_id, generation=generation)
                return 0
            self.client.delete(
                collection_name=self.settings.collection,
                points_selector=FilterSelector(
                    filter=Filter(
                        must=[FieldCondition(key="ad_id", match=MatchValue(value=ad_id))],
                        must_not=[FieldCondition(key="generation", match=MatchValue(value=generation))],
                    )
                ),
                wait=True,
            )
            return len(points)
        except VectorStoreError:
            raise
        except Exception as exc:
            raise VectorStoreError("Failed to replace image vectors.") from exc

    def delete_ad_vectors(self, *, ad_id: str) -> None:
        self.ensure_collection()
        try:
            self.client.delete(
                collection_name=self.settings.collection,
                points_selector=FilterSelector(
                    filter=Filter(must=[FieldCondition(key="ad_id", match=MatchValue(value=ad_id))])
                ),
                wait=True,
            )
        except Exception as exc:
            raise VectorStoreError("Failed to delete ad image vectors.") from exc

    def search(self, *, vector: list[float], limit: int) -> list[VectorSearchHit]:
        self.ensure_collection()
        query_filter = Filter(
            must=[
                FieldCondition(key="embedding_version", match=MatchValue(value=self.settings.embedding_version)),
                FieldCondition(key="schema_version", match=MatchValue(value=self.settings.collection_schema_version)),
            ]
        )
        try:
            if hasattr(self.client, "query_points"):
                result = self.client.query_points(
                    collection_name=self.settings.collection,
                    query=vector,
                    query_filter=query_filter,
                    limit=limit,
                    with_payload=True,
                )
                points = getattr(result, "points", result)
            else:
                points = self.client.search(
                    collection_name=self.settings.collection,
                    query_vector=vector,
                    query_filter=query_filter,
                    limit=limit,
                    with_payload=True,
                )
            return [self._to_hit(point) for point in (points or [])]
        except Exception as exc:
            raise VectorStoreError("Failed to search image vectors.") from exc

    def _to_hit(self, point: Any) -> VectorSearchHit:
        payload = getattr(point, "payload", None) or {}
        return VectorSearchHit(
            ad_id=str(payload.get("ad_id") or ""),
            media_id=str(payload.get("media_id") or "") or None,
            is_primary=bool(payload.get("is_primary")),
            sort_order=int(payload.get("sort_order") or 0),
            score=float(getattr(point, "score", 0) or 0),
            payload=dict(payload),
        )

    def _stable_point_id(self, item: ImageVector) -> str:
        key = ":".join(
            [
                self.settings.collection_schema_version,
                item.embedding_version,
                item.ad_id,
                item.media_id,
                item.generation,
            ]
        )
        return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


_store: QdrantImageStore | None = None


def get_store() -> QdrantImageStore:
    global _store
    if _store is None:
        _store = QdrantImageStore(get_settings())
    return _store

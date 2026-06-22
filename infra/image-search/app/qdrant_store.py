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
    PointStruct,
    VectorParams,
)

from .config import Settings, get_settings


class VectorStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class ImageVector:
    ad_id: str
    image_url: str
    vector: list[float]
    is_primary: bool = False
    sort_order: int = 0


@dataclass(frozen=True)
class VectorSearchHit:
    ad_id: str
    image_url: str | None
    is_primary: bool
    sort_order: int
    score: float
    payload: dict[str, Any]


class QdrantImageStore:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._client: QdrantClient | None = None

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
        try:
            collections = self.client.get_collections().collections
            existing = {collection.name for collection in collections}

            if self.settings.collection in existing:
                return

            self.client.create_collection(
                collection_name=self.settings.collection,
                vectors_config=VectorParams(
                    size=self.settings.vector_size,
                    distance=Distance.COSINE,
                ),
            )
        except Exception as exc:
            raise VectorStoreError("Failed to ensure Qdrant collection.") from exc

    def replace_ad_vectors(self, *, ad_id: str, vectors: list[ImageVector]) -> int:
        self.ensure_collection()
        self.delete_ad_vectors(ad_id=ad_id)

        points: list[PointStruct] = []
        for item in vectors:
            points.append(
                PointStruct(
                    id=self._stable_point_id(
                        ad_id=item.ad_id,
                        image_url=item.image_url,
                        sort_order=item.sort_order,
                    ),
                    vector=item.vector,
                    payload={
                        "ad_id": item.ad_id,
                        "image_url": item.image_url,
                        "is_primary": bool(item.is_primary),
                        "sort_order": int(item.sort_order or 0),
                    },
                )
            )

        if not points:
            return 0

        try:
            self.client.upsert(
                collection_name=self.settings.collection,
                points=points,
                wait=True,
            )
            return len(points)
        except Exception as exc:
            raise VectorStoreError("Failed to upsert image vectors.") from exc

    def delete_ad_vectors(self, *, ad_id: str) -> None:
        self.ensure_collection()

        try:
            self.client.delete(
                collection_name=self.settings.collection,
                points_selector=FilterSelector(
                    filter=Filter(
                        must=[
                            FieldCondition(
                                key="ad_id",
                                match=MatchValue(value=ad_id),
                            )
                        ]
                    )
                ),
                wait=True,
            )
        except Exception as exc:
            raise VectorStoreError("Failed to delete ad image vectors.") from exc

    def search(self, *, vector: list[float], limit: int) -> list[VectorSearchHit]:
        self.ensure_collection()

        try:
            # qdrant-client 1.10+ supports query_points. Older versions use search.
            if hasattr(self.client, "query_points"):
                result = self.client.query_points(
                    collection_name=self.settings.collection,
                    query=vector,
                    limit=limit,
                    with_payload=True,
                )
                points = getattr(result, "points", result)
            else:
                points = self.client.search(
                    collection_name=self.settings.collection,
                    query_vector=vector,
                    limit=limit,
                    with_payload=True,
                )

            return [self._to_hit(point) for point in (points or [])]

        except Exception as exc:
            raise VectorStoreError("Failed to search image vectors.") from exc

    def _to_hit(self, point: Any) -> VectorSearchHit:
        payload = getattr(point, "payload", None) or {}
        score = float(getattr(point, "score", 0) or 0)

        return VectorSearchHit(
            ad_id=str(payload.get("ad_id") or ""),
            image_url=payload.get("image_url"),
            is_primary=bool(payload.get("is_primary")),
            sort_order=int(payload.get("sort_order") or 0),
            score=score,
            payload=dict(payload),
        )

    def _stable_point_id(self, *, ad_id: str, image_url: str, sort_order: int) -> str:
        key = f"{ad_id}:{image_url}:{int(sort_order or 0)}"
        return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


_store: QdrantImageStore | None = None


def get_store() -> QdrantImageStore:
    global _store

    if _store is None:
        _store = QdrantImageStore(get_settings())

    return _store

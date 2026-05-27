"""
Image Search Service.

Handles:
- Image loading (Frappe FS + URLs)
- Embedding generation
- Qdrant vector search
- Grouping results → ad-level ranking
"""

from __future__ import annotations

from typing import List, Dict, Any
from io import BytesIO
import os
import uuid

import frappe
import requests
from PIL import Image

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchAny,
    MatchValue,
    PointStruct,
    VectorParams,
)

from aos.services.embedding_service import generate_image_embedding
from aos.utils.aos_settings import get_aos_settings_snapshot


# CLIENT
_qdrant_client: QdrantClient | None = None
_cached_cfg: dict | None = None


# SETTINGS
def _get_settings():
    cfg = get_aos_settings_snapshot()

    return {
        "host": cfg.qdrant_host or "localhost",
        "port": cfg.qdrant_port or 6333,
        "collection": cfg.qdrant_collection or "ads",
        "api_key": cfg.qdrant_api_key or None,
        "https": bool(cfg.qdrant_https),
        "limit": cfg.image_search_limit or 50,
        "threshold": cfg.score_threshold or 0.0,
    }


# QDRANT CLIENT
def _get_qdrant_client() -> QdrantClient:
    global _qdrant_client, _cached_cfg

    cfg = _get_settings()

    if _qdrant_client is None or _cached_cfg != cfg:
        _cached_cfg = cfg
        _qdrant_client = QdrantClient(
            host=cfg["host"],
            port=cfg["port"],
            api_key=cfg["api_key"],
            https=cfg["https"],
        )

    return _qdrant_client


def _get_collection_name() -> str:
    return _get_settings()["collection"]


def _get_search_limit() -> int:
    return int(_get_settings()["limit"] or 50)


def _get_score_threshold() -> float:
    return float(_get_settings()["threshold"] or 0.0)


# COLLECTION SETUP
def _ensure_collection_exists():
    client = _get_qdrant_client()
    collection = _get_collection_name()

    try:
        existing = {c.name for c in client.get_collections().collections}

        if collection in existing:
            return

        client.create_collection(
            collection_name=collection,
            vectors_config=VectorParams(
                size=512,
                distance=Distance.COSINE,
            ),
        )

        frappe.logger().info(f"Qdrant collection created: {collection}")

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "Failed to ensure Qdrant collection",
        )


# IMAGE LOADING
def _load_image(source) -> Image.Image:
    if hasattr(source, "read"):
        return Image.open(source).convert("RGB")

    source = str(source or "").strip()

    if not source:
        raise ValueError("Invalid image source")

    if source.startswith("http"):
        resp = requests.get(source, timeout=10)
        resp.raise_for_status()
        return Image.open(BytesIO(resp.content)).convert("RGB")

    if source.startswith("/files/") or source.startswith("/private/files/"):
        site_path = frappe.get_site_path()

        candidate_paths = [
            os.path.join(site_path, "public", source.lstrip("/")),
            os.path.join(site_path, source.lstrip("/")),
        ]

        for file_path in candidate_paths:
            if os.path.exists(file_path):
                return Image.open(file_path).convert("RGB")

        raise FileNotFoundError(f"File not found: {source}")

    raise ValueError(f"Unsupported image source: {source}")


# VECTOR NORMALIZATION
def _normalize_vector(vector):
    try:
        import numpy as np

        v = np.array(vector, dtype=float)
        norm = np.linalg.norm(v)

        if norm == 0:
            return v.tolist()

        return (v / norm).tolist()

    except Exception:
        return vector


# SEARCH CORE
def _search_qdrant(vector: List[float]) -> List[Any]:
    _ensure_collection_exists()
    client = _get_qdrant_client()

    result = client.query_points(
        collection_name=_get_collection_name(),
        query=vector,
        limit=_get_search_limit(),
        with_payload=True,
    )

    return result.points or []


def _group_and_rank(results: List[Any]) -> List[str]:
    ad_scores: Dict[str, float] = {}
    threshold = _get_score_threshold()

    for r in results:
        payload = r.payload or {}
        ad_id = payload.get("ad_id")

        if not ad_id:
            continue

        score = float(r.score or 0)

        if score < threshold:
            continue

        if payload.get("is_primary"):
            score += 0.02

        if ad_id not in ad_scores or score > ad_scores[ad_id]:
            ad_scores[ad_id] = score

    sorted_ads = sorted(
        ad_scores.items(),
        key=lambda x: x[1],
        reverse=True,
    )

    return [ad_id for ad_id, _ in sorted_ads]


# PUBLIC SEARCH
def search_similar_ads(image_file) -> List[str]:
    try:
        image = _load_image(image_file)
        vector = generate_image_embedding(image)

        if not vector:
            return []

        vector = _normalize_vector(vector)
        results = _search_qdrant(vector)

        if not results:
            return []

        return _group_and_rank(results)

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "Image search failed",
        )
        return []


# INDEXING
def index_ad_images(ad_id: str, images: List[Dict[str, Any]]):
    _ensure_collection_exists()
    client = _get_qdrant_client()
    collection = _get_collection_name()

    points: List[PointStruct] = []

    for img in images or []:
        image_url = img.get("image")

        if not image_url:
            continue

        try:
            image = _load_image(image_url)
            vector = generate_image_embedding(image)

            if not vector:
                continue

            vector = _normalize_vector(vector)

            points.append(
                PointStruct(
                    id=str(uuid.uuid4()),
                    vector=vector,
                    payload={
                        "ad_id": ad_id,
                        "image_url": image_url,
                        "is_primary": int(img.get("is_primary") or 0),
                    },
                )
            )

        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"Index image failed: {image_url}",
            )

    if not points:
        return

    client.upsert(
        collection_name=collection,
        points=points,
    )


def delete_ad_images(ad_id: str, image_urls: List[str] | None = None):
    _ensure_collection_exists()
    client = _get_qdrant_client()
    collection = _get_collection_name()

    try:
        must = [
            FieldCondition(
                key="ad_id",
                match=MatchValue(value=ad_id),
            )
        ]

        clean_urls = [
            url for url in (image_urls or [])
            if url
        ]

        if clean_urls:
            must.append(
                FieldCondition(
                    key="image_url",
                    match=MatchAny(any=clean_urls),
                )
            )

        client.delete(
            collection_name=collection,
            points_selector=FilterSelector(
                filter=Filter(must=must),
            ),
        )
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "Delete images failed",
        )


def sync_ad_images(
    ad_id: str,
    old_images: List[str],
    new_images: List[Dict[str, Any]],
):
    old_set = {
        url for url in (old_images or [])
        if url
    }

    new_set = {
        img.get("image")
        for img in (new_images or [])
        if img.get("image")
    }

    to_add = new_set - old_set
    to_remove = old_set - new_set

    if to_add:
        index_ad_images(
            ad_id,
            [
                {
                    "image": url,
                    "is_primary": 0,
                }
                for url in to_add
            ],
        )

    if to_remove:
        delete_ad_images(
            ad_id,
            list(to_remove),
        )

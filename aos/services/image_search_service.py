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

import requests
from PIL import Image

import frappe

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct

from aos.services.embedding_service import generate_image_embedding
from aos.utils.aos_settings import get_aos_settings_snapshot


# CLIENT (singleton-style)
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
    return _get_settings()["limit"]


def _get_score_threshold() -> float:
    return _get_settings()["threshold"]


# COLLECTION SETUP
def _ensure_collection_exists():
    client = _get_qdrant_client()
    collection = _get_collection_name()

    try:
        existing = {c.name for c in client.get_collections().collections}

        if collection in existing:
            return

        VECTOR_SIZE = 512

        client.create_collection(
            collection_name=collection,
            vectors_config=VectorParams(
                size=VECTOR_SIZE,
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

        file_path = os.path.join(site_path, "public", source.lstrip("/"))

        if not os.path.exists(file_path):
            file_path = os.path.join(site_path, source.lstrip("/"))

        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {source}")

        return Image.open(file_path).convert("RGB")

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

    for img in images:
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

    if points:
        client.upsert(
            collection_name=collection,
            points=points,
        )


def delete_ad_images(ad_id: str, image_urls: List[str]):
    client = _get_qdrant_client()
    collection = _get_collection_name()

    try:
        client.delete(
            collection_name=collection,
            points_selector={
                "filter": {
                    "must": [
                        {"key": "ad_id", "match": {"value": ad_id}}
                    ]
                }
            },
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
    old_set = set(old_images)
    new_set = {
        img.get("image")
        for img in new_images
        if img.get("image")
    }

    to_add = new_set - old_set
    to_remove = old_set - new_set

    if to_add:
        index_ad_images(
            ad_id,
            [{"image": url, "is_primary": 0} for url in to_add],
        )

    if to_remove:
        delete_ad_images(ad_id, list(to_remove))

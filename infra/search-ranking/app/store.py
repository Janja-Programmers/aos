from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from typing import Any

from redis import Redis

from app.config import get_settings

AD_ACTIVE_ZSET = "sr:ads:active"
SHORT_VISIBLE_ZSET = "sr:shorts:visible"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    try:
        return bool(int(value or 0))
    except Exception:
        return str(value or "").strip().lower() in {"true", "yes", "on"}


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0)
    except Exception:
        return float(default)


def _timestamp(value: Any) -> float:
    text = _clean(value)
    if not text:
        return 0.0
    try:
        text = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return float(dt.timestamp())
    except Exception:
        return 0.0


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)


def _load(raw: bytes | str | None, default: Any = None) -> Any:
    if raw is None:
        return default
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="ignore")
    try:
        return json.loads(raw)
    except Exception:
        return default


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) >= 2]


def _text_score(query: str, doc: dict[str, Any]) -> float:
    q_tokens = _tokens(query)
    if not q_tokens:
        return 0.0
    fields = [
        _clean(doc.get("title")),
        _clean(doc.get("description")),
        _clean(doc.get("category")),
        _clean(doc.get("seller_name")),
        " ".join(str(x) for x in doc.get("keywords") or []),
    ]
    haystack = " ".join(fields).lower()
    if not haystack:
        return 0.0
    score = 0.0
    for token in q_tokens:
        if token in haystack:
            score += 1.0
        if _clean(doc.get("title")).lower().startswith(token):
            score += 1.5
    return score / max(len(q_tokens), 1)


def ad_key(ad_id: str) -> str:
    return f"sr:ad:{ad_id}"


def short_key(short_id: str) -> str:
    return f"sr:short:{short_id}"


def _ad_base_score(doc: dict[str, Any]) -> float:
    rating = _float(doc.get("average_rating"))
    reviews = _float(doc.get("total_reviews"))
    views = _float(doc.get("view_count"))
    wishlists = _float(doc.get("wishlist_count"))
    verified = 20.0 if _bool(doc.get("seller_verified")) else 0.0
    created = _timestamp(doc.get("creation"))
    recency = created / 1_000_000_000.0
    return rating * 20.0 + math.log1p(reviews) * 10.0 + math.log1p(views) * 2.0 + math.log1p(wishlists) * 4.0 + verified + recency


def _short_base_score(doc: dict[str, Any]) -> float:
    ranking = _float(doc.get("ranking_score"))
    created = _timestamp(doc.get("creation"))
    return ranking + created / 1_000_000_000.0


def upsert_ad(redis: Redis, doc: dict[str, Any]) -> None:
    ad_id = _clean(doc.get("id") or doc.get("name"))
    if not ad_id:
        raise ValueError("ad id is required")
    status = _clean(doc.get("status"))
    seller_status = _clean(doc.get("seller_status"))
    if status != "Active" or seller_status != "Active":
        delete_ad(redis, ad_id)
        return
    redis.hset(ad_key(ad_id), mapping={
        "id": ad_id,
        "doc": _json(doc),
        "status": status,
        "country": _clean(doc.get("country")),
        "location": _clean(doc.get("location")),
        "category": _clean(doc.get("category")),
        "seller": _clean(doc.get("seller")),
        "updated_at": _clean(doc.get("modified") or doc.get("creation")),
    })
    redis.zadd(AD_ACTIVE_ZSET, {ad_id: _ad_base_score(doc)})


def delete_ad(redis: Redis, ad_id: str) -> None:
    clean = _clean(ad_id)
    if not clean:
        return
    redis.delete(ad_key(clean))
    redis.zrem(AD_ACTIVE_ZSET, clean)


def upsert_short(redis: Redis, doc: dict[str, Any]) -> None:
    short_id = _clean(doc.get("id") or doc.get("name"))
    if not short_id:
        raise ValueError("short id is required")
    if _clean(doc.get("status")) != "ready" or _clean(doc.get("visibility_status")) != "visible":
        delete_short(redis, short_id)
        return
    redis.hset(short_key(short_id), mapping={
        "id": short_id,
        "doc": _json(doc),
        "status": _clean(doc.get("status")),
        "visibility_status": _clean(doc.get("visibility_status")),
        "content_mode": _clean(doc.get("content_mode")),
        "country": _clean(doc.get("country")),
        "owner": _clean(doc.get("owner")),
        "updated_at": _clean(doc.get("modified") or doc.get("creation")),
    })
    redis.zadd(SHORT_VISIBLE_ZSET, {short_id: _short_base_score(doc)})


def delete_short(redis: Redis, short_id: str) -> None:
    clean = _clean(short_id)
    if not clean:
        return
    redis.delete(short_key(clean))
    redis.zrem(SHORT_VISIBLE_ZSET, clean)


def _load_ad(redis: Redis, ad_id: str) -> dict[str, Any] | None:
    raw = redis.hget(ad_key(ad_id), "doc")
    return _load(raw, None)


def _load_short(redis: Redis, short_id: str) -> dict[str, Any] | None:
    raw = redis.hget(short_key(short_id), "doc")
    return _load(raw, None)


def search_ads(redis: Redis, payload: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    query = _clean(payload.get("q") or payload.get("query"))
    limit = max(1, min(int(payload.get("limit") or 20), 100))
    offset = max(0, int(payload.get("offset") or 0))
    filters = payload.get("filters") if isinstance(payload.get("filters"), dict) else {}
    country = _clean(filters.get("country"))
    location = _clean(filters.get("location"))
    category = _clean(filters.get("category"))
    seller = _clean(filters.get("seller"))

    scan_count = max(limit + offset + 100, settings.max_ad_candidates)
    ids = [x.decode() if isinstance(x, bytes) else str(x) for x in redis.zrevrange(AD_ACTIVE_ZSET, 0, scan_count - 1)]
    ranked: list[dict[str, Any]] = []
    for ad_id in ids:
        doc = _load_ad(redis, ad_id)
        if not doc:
            continue
        if country and _clean(doc.get("country")) != country:
            continue
        if location and _clean(doc.get("location")) != location:
            continue
        if category and _clean(doc.get("category")) != category:
            continue
        if seller and _clean(doc.get("seller")) != seller:
            continue
        text_score = _text_score(query, doc)
        if query and text_score <= 0:
            continue
        base_score = _ad_base_score(doc)
        score = base_score + text_score * settings.text_match_weight
        ranked.append({"id": ad_id, "score": score, "match_score": text_score})
    ranked.sort(key=lambda x: (x["score"], x["id"]), reverse=True)
    page = ranked[offset:offset + limit]
    return {"ok": True, "items": page, "total_candidates": len(ranked)}


def feed_shorts(redis: Redis, payload: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    limit = max(1, min(int(payload.get("limit") or 20), 100))
    offset = max(0, int(payload.get("offset") or 0))
    content_mode = _clean(payload.get("content_mode") or payload.get("mode"))
    country = _clean(payload.get("country"))
    scan_count = max(limit + offset + 100, settings.max_short_candidates)
    ids = [x.decode() if isinstance(x, bytes) else str(x) for x in redis.zrevrange(SHORT_VISIBLE_ZSET, 0, scan_count - 1)]
    ranked: list[dict[str, Any]] = []
    for short_id in ids:
        doc = _load_short(redis, short_id)
        if not doc:
            continue
        if content_mode and content_mode.lower() != "all" and _clean(doc.get("content_mode")) != content_mode:
            continue
        if country and _clean(doc.get("country")) and _clean(doc.get("country")) != country:
            continue
        ranked.append({"id": short_id, "score": _short_base_score(doc)})
    ranked.sort(key=lambda x: (x["score"], x["id"]), reverse=True)
    page = ranked[offset:offset + limit]
    return {"ok": True, "items": page, "total_candidates": len(ranked)}


def related_ads(redis: Redis, payload: dict[str, Any]) -> dict[str, Any]:
    ad_id = _clean(payload.get("ad_id"))
    limit = max(1, min(int(payload.get("limit") or 20), 100))
    source = _load_ad(redis, ad_id)
    if not source:
        return {"ok": True, "items": [], "total_candidates": 0}
    q = " ".join([_clean(source.get("title")), _clean(source.get("category"))])
    result = search_ads(redis, {"q": q, "limit": limit + 1, "offset": 0, "filters": {"country": source.get("country")}})
    items = [row for row in result.get("items") or [] if row.get("id") != ad_id][:limit]
    return {"ok": True, "items": items, "total_candidates": len(items)}

from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime, timezone
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
			dt = dt.replace(tzinfo=UTC)
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
	wishlists = _float(doc.get("wishlist_count"))
	verified = 20.0 if _bool(doc.get("seller_verified")) else 0.0
	created = _timestamp(doc.get("creation"))
	recency = created / 1_000_000_000.0
	return (
		rating * 20.0
		+ math.log1p(reviews) * 10.0
		+ math.log1p(wishlists) * 4.0
		+ verified
		+ recency
	)


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
	if status != "Active" or seller_status != "Active" or not _bool(doc.get("eligible")):
		delete_ad(redis, ad_id)
		return
	redis.hset(
		ad_key(ad_id),
		mapping={
			"id": ad_id,
			"doc": _json(doc),
			"status": status,
			"country": _clean(doc.get("country")),
			"location": _clean(doc.get("location")),
			"category": _clean(doc.get("category")),
			"seller": _clean(doc.get("seller")),
			"updated_at": _clean(doc.get("modified") or doc.get("creation")),
		},
	)
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
	redis.hset(
		short_key(short_id),
		mapping={
			"id": short_id,
			"doc": _json(doc),
			"status": _clean(doc.get("status")),
			"visibility_status": _clean(doc.get("visibility_status")),
			"content_mode": _clean(doc.get("content_mode")),
			"country": _clean(doc.get("country")),
			"owner": _clean(doc.get("owner")),
			"updated_at": _clean(doc.get("modified") or doc.get("creation")),
		},
	)
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


def _attribute_match(actual: Any, expected: Any) -> bool:
	actual_values = actual if isinstance(actual, list) else [actual]
	expected_values = expected if isinstance(expected, list) else [expected]
	actual_clean = {_clean(v).lower() for v in actual_values if _clean(v)}
	expected_clean = {_clean(v).lower() for v in expected_values if _clean(v)}
	return bool(expected_clean) and expected_clean.issubset(actual_clean)


def _matches_ad_filters(doc: dict[str, Any], filters: dict[str, Any]) -> bool:
	categories = filters.get("categories") or filters.get("category") or []
	if not isinstance(categories, list):
		categories = [categories]
	category_values = {_clean(value) for value in categories if _clean(value)}
	if category_values and _clean(doc.get("category")) not in category_values:
		return False
	seller = _clean(filters.get("seller"))
	if seller and _clean(doc.get("seller")) != seller:
		return False
	attributes = filters.get("attributes") if isinstance(filters.get("attributes"), dict) else {}
	doc_attributes = doc.get("attributes") if isinstance(doc.get("attributes"), dict) else {}
	for key, expected in attributes.items():
		if not _attribute_match(doc_attributes.get(str(key)), expected):
			return False
	return True


def search_ads(redis: Redis, payload: dict[str, Any]) -> dict[str, Any]:
	settings = get_settings()
	query = _clean(payload.get("q") or payload.get("query"))
	limit = max(1, min(int(payload.get("limit") or 20), 100))
	offset = max(0, min(int(payload.get("offset") or 0), 5000))
	filters = payload.get("filters") if isinstance(payload.get("filters"), dict) else {}

	# Geography is deliberately not accepted here. Frappe performs the final
	# exact-location -> same-country -> global rerank after authoritative
	# eligibility filtering.
	scan_count = min(max(limit + offset + 100, settings.max_ad_candidates), 5000)
	ids = [
		x.decode() if isinstance(x, bytes) else str(x)
		for x in redis.zrevrange(AD_ACTIVE_ZSET, 0, scan_count - 1)
	]
	ranked: list[dict[str, Any]] = []
	for ad_id in ids:
		doc = _load_ad(redis, ad_id)
		if not doc or not _bool(doc.get("eligible")):
			continue
		if not _matches_ad_filters(doc, filters):
			continue
		text_score = _text_score(query, doc)
		if query and text_score <= 0:
			continue
		base_score = _ad_base_score(doc)
		score = base_score + text_score * settings.text_match_weight
		ranked.append({"id": ad_id, "score": score, "match_score": text_score})
	ranked.sort(key=lambda x: (-float(x["score"]), str(x["id"])))
	page = ranked[offset : offset + limit]
	return {"ok": True, "items": page, "total_candidates": len(ranked)}


def feed_shorts(redis: Redis, payload: dict[str, Any]) -> dict[str, Any]:
	settings = get_settings()
	limit = max(1, min(int(payload.get("limit") or 20), 100))
	offset = max(0, int(payload.get("offset") or 0))
	content_mode = _clean(payload.get("content_mode") or payload.get("mode"))
	country = _clean(payload.get("country"))
	scan_count = max(limit + offset + 100, settings.max_short_candidates)
	ids = [
		x.decode() if isinstance(x, bytes) else str(x)
		for x in redis.zrevrange(SHORT_VISIBLE_ZSET, 0, scan_count - 1)
	]
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
	page = ranked[offset : offset + limit]
	return {"ok": True, "items": page, "total_candidates": len(ranked)}


def related_ads(redis: Redis, payload: dict[str, Any]) -> dict[str, Any]:
	settings = get_settings()
	ad_id = _clean(payload.get("ad_id"))
	limit = max(1, min(int(payload.get("limit") or 20), 100))
	source = _load_ad(redis, ad_id)
	if not source or not _bool(source.get("eligible")):
		return {"ok": True, "items": [], "total_candidates": 0}
	ids = [
		x.decode() if isinstance(x, bytes) else str(x)
		for x in redis.zrevrange(AD_ACTIVE_ZSET, 0, min(settings.max_ad_candidates, 5000) - 1)
	]
	source_attrs = source.get("attributes") if isinstance(source.get("attributes"), dict) else {}
	source_price = _float(source.get("price"))
	ranked: list[dict[str, Any]] = []
	for candidate_id in ids:
		if candidate_id == ad_id:
			continue
		doc = _load_ad(redis, candidate_id)
		if not doc or not _bool(doc.get("eligible")):
			continue
		if _clean(doc.get("category")) != _clean(source.get("category")):
			continue
		score = _ad_base_score(doc)
		candidate_attrs = doc.get("attributes") if isinstance(doc.get("attributes"), dict) else {}
		shared = sum(1 for key, value in source_attrs.items() if key in candidate_attrs and _attribute_match(candidate_attrs[key], value))
		score += shared * 35.0
		text_score = _text_score(" ".join([_clean(source.get("title")), _clean(source.get("category"))]), doc)
		score += text_score * settings.text_match_weight
		if source_price > 0 and _clean(doc.get("currency")) == _clean(source.get("currency")):
			price = _float(doc.get("price"))
			if price > 0:
				score += max(0.0, 20.0 - abs(price - source_price) / source_price * 20.0)
		ranked.append({"id": candidate_id, "score": score})
	ranked.sort(key=lambda x: (-float(x["score"]), str(x["id"])))
	items = ranked[:limit]
	return {"ok": True, "items": items, "total_candidates": len(ranked)}

"""Strict Wishlist request validation and opaque cursor handling."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime
from decimal import Decimal
from typing import Any, Mapping

from aos.services.ads.constants import (
    ALLOWED_PRICE_TYPES,
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    MAX_SEARCH_QUERY_LENGTH,
)
from aos.services.ads.errors import AdsValidationError
from aos.services.ads.validation import (
    decimal_storage,
    normalize_decimal,
    normalize_flag,
    normalize_identifier,
    normalize_int,
    normalize_text,
)

WISHLIST_MUTATION_FIELDS = frozenset({"ad_id"})
WISHLIST_LIST_FIELDS = frozenset(
    {
        "country",
        "currency",
        "location",
        "category",
        "seller",
        "q",
        "price_type",
        "price_min",
        "price_max",
        "rating_min",
        "verified_seller",
        "sort",
        "limit",
        "cursor",
    }
)
WISHLIST_LIST_SORTS = frozenset(
    {"saved_recent", "saved_oldest", "price_low", "price_high", "rating_high"}
)


def _ensure_wishlist_fields(payload: Mapping[str, Any], allowed: frozenset[str]) -> None:
    if not isinstance(payload, Mapping):
        raise AdsValidationError("Invalid wishlist request.", code="INVALID_WISHLIST_REQUEST")
    if any(str(key) not in allowed for key in payload):
        raise AdsValidationError("Invalid wishlist request.", code="INVALID_WISHLIST_REQUEST")


def normalize_wishlist_mutation(payload: Mapping[str, Any]) -> str:
    _ensure_wishlist_fields(payload, WISHLIST_MUTATION_FIELDS)
    try:
        return normalize_identifier(payload.get("ad_id"), field="ad_id", required=True)
    except AdsValidationError as exc:
        raise AdsValidationError("Invalid wishlist request.", code="INVALID_WISHLIST_REQUEST") from exc


def normalize_wishlist_list_request(payload: Mapping[str, Any]) -> dict[str, Any]:
    _ensure_wishlist_fields(payload, WISHLIST_LIST_FIELDS)
    sort = normalize_text(payload.get("sort") or "saved_recent", field="sort", max_length=32, required=True)
    if sort not in WISHLIST_LIST_SORTS:
        raise AdsValidationError("Invalid wishlist sort.", code="INVALID_WISHLIST_REQUEST")
    q = normalize_text(payload.get("q"), field="q", max_length=MAX_SEARCH_QUERY_LENGTH)
    if q and len(q) < 2:
        raise AdsValidationError("Search query must contain at least two characters.", code="INVALID_SEARCH_QUERY")
    price_type = normalize_text(payload.get("price_type"), field="price_type", max_length=40)
    if price_type and price_type not in ALLOWED_PRICE_TYPES:
        raise AdsValidationError("Invalid wishlist request.", code="INVALID_WISHLIST_REQUEST")
    price_min = normalize_decimal(payload.get("price_min"), field="price_min", minimum=Decimal("0"))
    price_max = normalize_decimal(payload.get("price_max"), field="price_max", minimum=Decimal("0"))
    if price_min is not None and price_max is not None and price_min > price_max:
        raise AdsValidationError("price_min cannot be greater than price_max.", code="INVALID_WISHLIST_REQUEST")
    rating_min = normalize_decimal(payload.get("rating_min"), field="rating_min", minimum=Decimal("0"), maximum=Decimal("5"))
    return {
        "country": normalize_identifier(payload.get("country"), field="country"),
        "currency": normalize_identifier(payload.get("currency"), field="currency"),
        "location": normalize_identifier(payload.get("location"), field="location"),
        "category": normalize_identifier(payload.get("category"), field="category"),
        "seller": normalize_identifier(payload.get("seller"), field="seller"),
        "q": q,
        "price_type": price_type,
        "price_min": decimal_storage(price_min),
        "price_max": decimal_storage(price_max),
        "rating_min": decimal_storage(rating_min),
        "verified_seller": normalize_flag(payload.get("verified_seller"), field="verified_seller", default=0),
        "sort": sort,
        "limit": normalize_int(payload.get("limit"), field="limit", default=DEFAULT_PAGE_SIZE, minimum=1, maximum=MAX_PAGE_SIZE),
        "cursor": normalize_text(payload.get("cursor"), field="cursor", max_length=1000),
    }


def wishlist_query_scope(request: Mapping[str, Any]) -> str:
    material = {
        key: request.get(key)
        for key in (
            "country", "currency", "location", "category", "seller", "q",
            "price_type", "price_min", "price_max", "rating_min", "verified_seller", "sort",
        )
    }
    raw = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def encode_wishlist_cursor(*, request: Mapping[str, Any], keys: Mapping[str, Any]) -> str:
    payload = {
        "v": 2,
        "scope": "wishlist",
        "query": wishlist_query_scope(request),
        "sort": request.get("sort") or "saved_recent",
        "keys": {str(key): str(value if value is not None else "") for key, value in keys.items()},
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_wishlist_cursor(value: Any, *, request: Mapping[str, Any]) -> dict[str, str]:
    token = normalize_text(value, field="cursor", max_length=1000, required=True)
    try:
        padded = token + "=" * (-len(token) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except Exception:
        raise AdsValidationError("Invalid wishlist pagination cursor.", code="INVALID_WISHLIST_CURSOR") from None
    if (
        not isinstance(payload, dict)
        or payload.get("v") != 2
        or payload.get("scope") != "wishlist"
        or payload.get("sort") != request.get("sort")
        or payload.get("query") != wishlist_query_scope(request)
        or not isinstance(payload.get("keys"), dict)
    ):
        raise AdsValidationError("Invalid wishlist pagination cursor.", code="INVALID_WISHLIST_CURSOR")
    keys = {str(key): str(val) for key, val in payload["keys"].items()}
    saved_on = keys.get("saved_on")
    if saved_on:
        try:
            parsed = datetime.fromisoformat(saved_on)
        except ValueError:
            raise AdsValidationError("Invalid wishlist pagination cursor.", code="INVALID_WISHLIST_CURSOR") from None
        if parsed.tzinfo is not None:
            raise AdsValidationError("Invalid wishlist pagination cursor.", code="INVALID_WISHLIST_CURSOR")
    if not keys.get("name"):
        raise AdsValidationError("Invalid wishlist pagination cursor.", code="INVALID_WISHLIST_CURSOR")
    return keys

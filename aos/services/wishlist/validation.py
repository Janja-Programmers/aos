"""Strict Wishlist request validation and opaque cursor handling."""

from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Any, Mapping

from aos.services.ads.constants import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from aos.services.ads.errors import AdsValidationError
from aos.services.ads.validation import (
    normalize_identifier,
    normalize_int,
    normalize_text,
)

WISHLIST_MUTATION_FIELDS = frozenset({"ad_id"})
WISHLIST_LIST_FIELDS = frozenset({"country", "currency", "limit", "cursor"})
WISHLIST_LIST_SCAN_MULTIPLIER = 5
WISHLIST_LIST_MAX_SCAN = MAX_PAGE_SIZE * WISHLIST_LIST_SCAN_MULTIPLIER


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
    return {
        "country": normalize_identifier(payload.get("country"), field="country"),
        "currency": normalize_identifier(payload.get("currency"), field="currency"),
        "limit": normalize_int(
            payload.get("limit"),
            field="limit",
            default=DEFAULT_PAGE_SIZE,
            minimum=1,
            maximum=MAX_PAGE_SIZE,
        ),
        "cursor": normalize_text(payload.get("cursor"), field="cursor", max_length=500),
    }


def encode_wishlist_cursor(*, saved_on: Any, name: Any) -> str:
    payload = {
        "v": 1,
        "scope": "wishlist",
        "saved_on": str(saved_on or ""),
        "name": str(name or ""),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_wishlist_cursor(value: Any) -> tuple[str, str]:
    token = normalize_text(value, field="cursor", max_length=500, required=True)
    try:
        padded = token + "=" * (-len(token) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except Exception:
        raise AdsValidationError(
            "Invalid wishlist pagination cursor.",
            code="INVALID_WISHLIST_CURSOR",
        ) from None

    if not isinstance(payload, dict) or payload.get("v") != 1 or payload.get("scope") != "wishlist":
        raise AdsValidationError(
            "Invalid wishlist pagination cursor.",
            code="INVALID_WISHLIST_CURSOR",
        )

    saved_on = normalize_text(payload.get("saved_on"), field="cursor", max_length=64, required=True)
    try:
        saved_on_value = datetime.fromisoformat(saved_on)
    except ValueError:
        raise AdsValidationError(
            "Invalid wishlist pagination cursor.",
            code="INVALID_WISHLIST_CURSOR",
        ) from None
    if saved_on_value.tzinfo is not None:
        raise AdsValidationError(
            "Invalid wishlist pagination cursor.",
            code="INVALID_WISHLIST_CURSOR",
        )

    name = normalize_identifier(payload.get("name"), field="cursor", required=True)
    return str(saved_on_value), name

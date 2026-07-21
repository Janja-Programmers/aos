"""Schema-safe normalization for Pydantic/FastAPI validation locations."""

from __future__ import annotations

import re
from typing import Any

_MAX_COMPONENT_LENGTH = 64
_SAFE_COMPONENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DANGEROUS = re.compile(
	r"(?i)(https?://|file://|bearer\s+|eyJ[A-Za-z0-9_-]{8,}\.|[/\\]|[\x00-\x1f\x7f]|\s{2,}|[^A-Za-z0-9_])"
)
_IGNORED_ROOTS = {"body", "query", "path", "header", "cookie"}
_APPROVED_COMPONENTS = {
	"request",
	"payload",
	"items",
	"item",
	"field",
	"events",
	"event",
	"tokens",
	"token",
	"job_id",
	"idempotency_key",
	"dispatch_id",
	"dispatch_generation",
	"dispatch_token",
	"short_id",
	"force",
	"callback_url",
	"raw_video",
	"sound",
	"output",
	"target",
	"doctype",
	"name",
	"owner",
	"content_kind",
	"source",
	"text_items",
	"media_items",
	"context",
	"document",
	"action",
	"index_kind",
	"notification",
	"title",
	"body",
	"data",
	"device_type",
	"registration_token",
	"url",
	"bucket",
	"object_key",
	"content_type",
	"size_bytes",
	"filename",
	"status",
	"message",
	"metadata",
	"counters",
	"language",
	"text",
	"texts",
	"image",
	"images",
	"query_text",
	"limit",
	"offset",
	"model",
	"options",
	"width",
	"height",
	"duration_ms",
	"start_ms",
	"volume",
}
_DYNAMIC_MAP_PARENTS = {"payload", "context", "document", "metadata", "counters", "options"}


def _safe_component(value: Any, *, previous: str | None) -> str:
	if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
		return "[]"
	text = str(value or "")
	if (
		not text
		or len(text) > _MAX_COMPONENT_LENGTH
		or not _SAFE_COMPONENT.fullmatch(text)
		or _DANGEROUS.search(text)
	):
		return "item" if previous in _DYNAMIC_MAP_PARENTS else "field"
	if text in _APPROVED_COMPONENTS:
		return text
	return "item" if previous in _DYNAMIC_MAP_PARENTS else "field"


def normalize_validation_location(location: Any) -> str:
	parts: list[str] = []
	for raw in tuple(location or ())[:24]:
		if str(raw) in _IGNORED_ROOTS:
			continue
		previous = parts[-1].removesuffix("[]") if parts else None
		component = _safe_component(raw, previous=previous)
		if component == "[]":
			if parts:
				parts[-1] = f"{parts[-1].removesuffix('[]')}[]"
			else:
				parts.append("items[]")
			continue
		parts.append(component)
	return ".".join(parts[:16]) or "request"


def validation_fields(errors: list[dict[str, Any]]) -> list[dict[str, str]]:
	fields: list[dict[str, str]] = []
	for error in errors[:50]:
		error_type = str(error.get("type") or "invalid").lower()
		if "missing" in error_type:
			reason = "field is required"
		elif "length" in error_type or "too_short" in error_type or "too_long" in error_type:
			reason = "invalid length"
		elif "json" in error_type:
			reason = "invalid JSON"
		else:
			reason = "invalid value"
		fields.append({"field": normalize_validation_location(error.get("loc")), "reason": reason})
	return fields

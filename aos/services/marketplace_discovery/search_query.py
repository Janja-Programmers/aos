"""Canonical Marketplace Discovery search intent.

Saved Searches persist exactly this frontend-supported intent. Ads search uses
the same normalizer, so Catalog/location/price/filter semantics cannot drift.
Pagination is execution state and is deliberately excluded from persisted intent.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

import frappe

from aos.services.ads.errors import AdsValidationError
from aos.services.ads.validation import normalize_flag, normalize_identifier, normalize_text
from aos.services.catalog.schema_resolver import resolve_attributes
from aos.services.catalog.service import CatalogService
from aos.services.localization import validate_country, validate_currency
from aos.services.sellers.identity import resolve_public_seller_id

_INTENT_FIELDS = frozenset({
    "q", "country", "currency", "location", "category", "seller", "attributes",
    "price_type", "promotion_type", "price_min", "price_max", "rating_min",
    "verified_seller", "sort",
})


def _json_object(value: Any) -> dict[str, Any]:
    if value in (None, ""):
        return {}
    parsed = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            raise AdsValidationError("Invalid search attributes.", code="SEARCH_INVALID_FILTERS") from None
    if not isinstance(parsed, dict) or len(parsed) > 30:
        raise AdsValidationError("Invalid search attributes.", code="SEARCH_INVALID_FILTERS")
    return dict(parsed)


def normalize_search_attributes(value: Any, *, category: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw = _json_object(value)
    if not raw:
        return [], {}
    if not category:
        raise AdsValidationError("Attribute filters require a category.", code="SEARCH_INVALID_FILTERS")
    chain = CatalogService().get_sellable_category_chain(category, for_update=False)
    schema = resolve_attributes(chain, include_dependency_map=True)
    by_key = {str(row.get("key") or ""): row for row in schema}
    by_id = {str(row.get("id") or ""): row for row in schema}
    normalized: list[dict[str, Any]] = []
    public: dict[str, Any] = {}
    selected_text: dict[str, str] = {}

    for raw_key in sorted(raw):
        key = normalize_identifier(raw_key, field="attribute", required=True, max_length=140)
        item = by_key.get(key) or by_id.get(key)
        if not item:
            raise AdsValidationError("Invalid category attribute filter.", code="SEARCH_INVALID_FILTERS")
        attr_id = str(item["id"])
        attr_key = str(item.get("key") or attr_id)
        attr_type = str(item.get("type") or "Text")
        options = {str(option) for option in (item.get("options") or [])}
        incoming = raw[raw_key]
        value_out: Any
        if attr_type == "MultiSelect":
            values = incoming if isinstance(incoming, list) else [incoming]
            if not values or len(values) > 20:
                raise AdsValidationError("Invalid multi-select filter.", code="SEARCH_INVALID_FILTERS")
            clean_values: list[str] = []
            for part in values:
                clean = normalize_text(part, field=attr_key, max_length=120, required=True)
                if options and clean not in options:
                    raise AdsValidationError("Invalid category attribute option.", code="SEARCH_INVALID_FILTERS")
                if clean not in clean_values:
                    clean_values.append(clean)
            value_out = clean_values
        elif attr_type in {"Text", "Textarea", "Select", "Year"}:
            clean = normalize_text(incoming, field=attr_key, max_length=500, required=True)
            if attr_type == "Select" and options and clean not in options:
                raise AdsValidationError("Invalid category attribute option.", code="SEARCH_INVALID_FILTERS")
            if attr_type == "Year" and (not clean.isdigit() or not 1000 <= int(clean) <= 9999):
                raise AdsValidationError("Invalid year filter.", code="SEARCH_INVALID_FILTERS")
            value_out = clean
            selected_text[attr_id] = clean
        elif attr_type == "Number":
            if isinstance(incoming, bool):
                raise AdsValidationError("Invalid number filter.", code="SEARCH_INVALID_FILTERS")
            try:
                value_out = float(incoming)
            except Exception:
                raise AdsValidationError("Invalid number filter.", code="SEARCH_INVALID_FILTERS") from None
        elif attr_type == "Boolean":
            value_out = bool(normalize_flag(incoming, field=attr_key))
        elif attr_type == "Date":
            value_out = normalize_text(incoming, field=attr_key, max_length=10, required=True)
            from frappe.utils import getdate
            try:
                value_out = getdate(value_out).isoformat()
            except Exception:
                raise AdsValidationError("Invalid date filter.", code="SEARCH_INVALID_FILTERS") from None
        else:
            raise AdsValidationError("Unsupported category attribute filter.", code="SEARCH_INVALID_FILTERS")
        normalized.append({"attribute": attr_id, "key": attr_key, "type": attr_type, "value": value_out})
        public[attr_key] = value_out

    for item in schema:
        dependency = item.get("depends_on")
        child_id = str(item.get("id") or "")
        child = next((row for row in normalized if row["attribute"] == child_id), None)
        if not child or not dependency:
            continue
        parent_id = str(dependency.get("id") or "")
        parent_value = selected_text.get(parent_id)
        if not parent_value:
            raise AdsValidationError("Dependent attribute filter requires its parent.", code="SEARCH_INVALID_FILTERS")
        if child["type"] == "Select":
            allowed = set((item.get("_dependency_options") or {}).get(parent_value, []))
            if str(child["value"]) not in allowed:
                raise AdsValidationError("Invalid dependent attribute filter.", code="SEARCH_INVALID_FILTERS")
    return normalized, public


def _validate_reference_filters(result: dict[str, Any]) -> None:
    """Validate current canonical references without taking ownership of them."""
    country=str(result.get("country") or "").strip()
    currency=str(result.get("currency") or "").strip()
    category=str(result.get("category") or "").strip()
    location=str(result.get("location") or "").strip()
    seller=str(result.get("seller") or "").strip()

    if country:
        resolved,error=validate_country(country)
        if error or not resolved:
            raise AdsValidationError("Invalid search country.", code="SEARCH_INVALID_FILTERS")
        result["country"]=resolved
    if currency:
        resolved,error=validate_currency(currency)
        if error or not resolved:
            raise AdsValidationError("Invalid search currency.", code="SEARCH_INVALID_FILTERS")
        result["currency"]=resolved
    if category and not CatalogService().resolve_filter_values(category):
        raise AdsValidationError("Invalid search category.", code="SEARCH_INVALID_FILTERS")
    if seller and not resolve_public_seller_id(seller):
        raise AdsValidationError("Invalid search seller.", code="SEARCH_INVALID_FILTERS")
    if location:
        row=frappe.db.get_value("AOS Location", location, ["country","is_active"], as_dict=True)
        if not row or int(row.is_active or 0) != 1:
            raise AdsValidationError("Invalid search location.", code="SEARCH_INVALID_FILTERS")
        if country and str(row.country or "").strip() != str(result.get("country") or "").strip():
            raise AdsValidationError("Search location does not belong to country.", code="SEARCH_INVALID_FILTERS")


def canonicalize_search_intent(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise AdsValidationError("Invalid search intent.", code="SEARCH_INVALID_FILTERS")
    unknown = sorted(str(key) for key in payload if str(key) not in _INTENT_FIELDS)
    if unknown:
        raise AdsValidationError("Unknown search field.", code="SEARCH_INVALID_FILTERS")

    # Reuse Ads' canonical filter normalization by adding execution defaults,
    # then deliberately strip pagination/cursor from the persisted intent.
    from aos.services.ads.validation import normalize_public_list_filters
    execution = normalize_public_list_filters(dict(payload))
    result = {k: v for k, v in execution.items() if k not in {"limit", "offset", "cursor"}}
    _validate_reference_filters(result)
    attrs_internal, attrs_public = normalize_search_attributes(payload.get("attributes"), category=result.get("category") or "")
    result["attributes"] = attrs_public
    # Internal form is used by the query builder but never persisted by Saved Search.
    result["_attributes"] = attrs_internal
    return result


def persisted_search_intent(payload: Mapping[str, Any]) -> dict[str, Any]:
    value = canonicalize_search_intent(payload)
    value.pop("_attributes", None)
    return {key: item for key, item in value.items() if item not in (None, "", [], {}, 0) or key == "verified_seller"}

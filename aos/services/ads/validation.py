"""Central Ads normalization and validation.

All client-controlled Ads values pass through this module before they reach a
Frappe document. The module rejects structured input for scalar fields,
normalizes Unicode and whitespace, uses Decimal for monetary values, enforces
Catalog-owned schemas, and emits deterministic child rows.
"""

from __future__ import annotations

import base64
import json
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Iterable, Mapping

from frappe.utils import getdate

from aos.services.catalog.errors import CatalogError, CatalogValidationError
from aos.services.catalog.service import CatalogService, attribute_key, resolve_attributes, resolve_pricing

from .constants import (
    ACTIVE_UPDATE_FIELDS,
    ALLOWED_PRICE_TYPES,
    CREATE_FIELDS,
    DEFAULT_PAGE_SIZE,
    DRAFT_UPSERT_FIELDS,
    MAX_AD_ATTRIBUTES,
    MAX_ATTRIBUTE_JSON_LENGTH,
    MAX_ATTRIBUTE_TEXTAREA_LENGTH,
    MAX_ATTRIBUTE_TEXT_LENGTH,
    MAX_DESCRIPTION_LENGTH,
    MAX_DRAFT_PAYLOAD_BYTES,
    MAX_DRAFT_STEP,
    MAX_IMAGES,
    MAX_OFFSET,
    MAX_PAGE_SIZE,
    MAX_PRICE_UNIT_LENGTH,
    MAX_REPORT_DETAILS_LENGTH,
    MAX_SEARCH_QUERY_LENGTH,
    MAX_SORT_ORDER,
    MAX_TITLE_LENGTH,
    MIN_DESCRIPTION_LENGTH,
    MIN_TITLE_LENGTH,
    MONEY_DECIMAL_PLACES,
    MONEY_MAX_DIGITS,
    PRICE_TYPES_REQUIRING_AMOUNT,
    PRICE_TYPES_WITHOUT_AMOUNT,
    PUBLIC_LIST_SORTS,
    PUBLIC_PROMOTION_TYPES,
)
from .errors import AdsValidationError

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SPACE_RE = re.compile(r"[ \t]+")
_YEAR_RE = re.compile(r"^\d{4}$")
_DETAIL_KEYS = frozenset(
    {
        "attribute",
        "value_text",
        "value_number",
        "value_date",
        "value_bool",
        "value_json",
    }
)
_IMAGE_KEYS = frozenset({"media", "media_id", "id", "is_primary", "sort_order", "image", "url"})
_DRAFT_PAYLOAD_FIELDS = CREATE_FIELDS
_MONEY_QUANTUM = Decimal(1).scaleb(-MONEY_DECIMAL_PLACES)
_MONEY_MAX_ABS = Decimal(10) ** (MONEY_MAX_DIGITS - MONEY_DECIMAL_PLACES) - _MONEY_QUANTUM


def persisted_offer_value(price_type: Any, value: Any) -> Any:
    """Translate a persisted Currency zero sentinel to an absent offer.

    Public payload validation must continue to reject an explicitly supplied
    zero offer. This helper is only for values reloaded from Frappe/MariaDB,
    where an unset non-null Currency column is represented as numeric zero.
    Non-fixed price types can never retain offer metadata. Invalid non-zero
    persisted values are returned unchanged so normal validation still fails
    closed.
    """

    if str(price_type or "").strip() != "Fixed" or value in (None, ""):
        return None
    try:
        return None if Decimal(str(value)) == 0 else value
    except (InvalidOperation, TypeError, ValueError):
        return value


def ensure_known_fields(payload: Mapping[str, Any], allowed: Iterable[str], *, aliases: Iterable[str] = ()) -> None:
    if not isinstance(payload, Mapping):
        raise AdsValidationError("Invalid request payload.", code="INVALID_AD_INPUT")
    allowed_set = set(allowed) | set(aliases)
    unknown = sorted(str(key) for key in payload if str(key) not in allowed_set)
    if unknown:
        raise AdsValidationError(
            "Unknown request field.",
            code="INVALID_AD_INPUT",
        )


def normalize_text(
    value: Any,
    *,
    field: str,
    max_length: int,
    required: bool = False,
    multiline: bool = False,
    min_length: int = 0,
) -> str:
    if isinstance(value, (dict, list, tuple, set)):
        raise AdsValidationError(f"Invalid {field}.", code="INVALID_AD_INPUT")
    if value is None:
        text = ""
    elif isinstance(value, str):
        text = value
    else:
        raise AdsValidationError(f"Invalid {field}.", code="INVALID_AD_INPUT")
    text = unicodedata.normalize("NFC", text)
    if _CONTROL_RE.search(text):
        raise AdsValidationError(f"Invalid {field}.", code="INVALID_AD_INPUT")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if multiline:
        lines = [_SPACE_RE.sub(" ", line.strip()) for line in text.splitlines()]
        text = "\n".join(line for line in lines if line)
    else:
        text = " ".join(text.split())
    if required and not text:
        raise AdsValidationError(f"{field.replace('_', ' ').title()} is required.", code="INVALID_AD_INPUT")
    if text and len(text) < min_length:
        raise AdsValidationError(f"{field.replace('_', ' ').title()} is too short.", code="INVALID_AD_INPUT")
    if len(text) > max_length:
        raise AdsValidationError(f"{field.replace('_', ' ').title()} is too long.", code="INVALID_AD_INPUT")
    return text


def normalize_identifier(value: Any, *, field: str, required: bool = False, max_length: int = 140) -> str:
    return normalize_text(value, field=field, max_length=max_length, required=required)


def normalize_flag(value: Any, *, field: str, default: int | None = None) -> int:
    if value is None and default is not None:
        return default
    if value in (0, False, "0"):
        return 0
    if value in (1, True, "1"):
        return 1
    raise AdsValidationError(f"Invalid {field}.", code="INVALID_AD_INPUT")


def normalize_int(
    value: Any,
    *,
    field: str,
    default: int | None = None,
    minimum: int = 0,
    maximum: int = MAX_SORT_ORDER,
) -> int:
    if value in (None, ""):
        if default is None:
            raise AdsValidationError(f"{field} is required.", code="INVALID_AD_INPUT")
        return default
    if isinstance(value, bool) or isinstance(value, (dict, list, tuple, set, float)):
        raise AdsValidationError(f"Invalid {field}.", code="INVALID_AD_INPUT")
    if isinstance(value, int):
        result = value
    elif isinstance(value, str) and value.strip().lstrip("+").isdigit():
        result = int(value.strip())
    else:
        raise AdsValidationError(f"Invalid {field}.", code="INVALID_AD_INPUT")
    if result < minimum or result > maximum:
        raise AdsValidationError(f"Invalid {field}.", code="INVALID_AD_INPUT")
    return result


def normalize_decimal(
    value: Any,
    *,
    field: str,
    required: bool = False,
    minimum: Decimal | None = None,
    maximum: Decimal | None = None,
    allow_zero: bool = True,
) -> Decimal | None:
    if value in (None, ""):
        if required:
            raise AdsValidationError(f"{field.replace('_', ' ').title()} is required.", code="INVALID_AD_INPUT")
        return None
    if isinstance(value, bool) or isinstance(value, (dict, list, tuple, set)):
        raise AdsValidationError(f"Invalid {field}.", code="INVALID_AD_INPUT")
    try:
        # str(float) preserves the JSON-visible decimal rather than importing
        # the binary floating-point representation into persisted money.
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        raise AdsValidationError(f"Invalid {field}.", code="INVALID_AD_INPUT") from None
    if not result.is_finite():
        raise AdsValidationError(f"Invalid {field}.", code="INVALID_AD_INPUT")
    result = result.quantize(_MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    if abs(result) > _MONEY_MAX_ABS:
        raise AdsValidationError(f"Invalid {field}.", code="INVALID_AD_INPUT")
    if not allow_zero and result == 0:
        raise AdsValidationError(f"{field.replace('_', ' ').title()} must be greater than zero.", code="INVALID_AD_INPUT")
    if minimum is not None and result < minimum:
        raise AdsValidationError(f"Invalid {field}.", code="INVALID_AD_INPUT")
    if maximum is not None and result > maximum:
        raise AdsValidationError(f"Invalid {field}.", code="INVALID_AD_INPUT")
    return result


def decimal_storage(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, f".{MONEY_DECIMAL_PLACES}f")


def normalize_date(value: Any, *, field: str, required: bool = False) -> date | None:
    if value in (None, ""):
        if required:
            raise AdsValidationError(f"{field.replace('_', ' ').title()} is required.", code="INVALID_AD_INPUT")
        return None
    if isinstance(value, (dict, list, tuple, set, bool)):
        raise AdsValidationError(f"Invalid {field}.", code="INVALID_AD_INPUT")
    try:
        return getdate(value)
    except Exception:
        raise AdsValidationError(f"Invalid {field}.", code="INVALID_AD_INPUT") from None


def normalize_json_list(value: Any, *, field: str, max_items: int) -> list[dict[str, Any]]:
    if value in (None, ""):
        return []
    parsed = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            raise AdsValidationError(f"Invalid {field}.", code="INVALID_AD_INPUT") from None
    if not isinstance(parsed, list):
        raise AdsValidationError(f"Invalid {field}.", code="INVALID_AD_INPUT")
    if len(parsed) > max_items:
        raise AdsValidationError(f"Too many {field} items.", code="INVALID_AD_INPUT")
    if any(not isinstance(item, dict) for item in parsed):
        raise AdsValidationError(f"Invalid {field}.", code="INVALID_AD_INPUT")
    return [dict(item) for item in parsed]


def _catalog_schema(category: Any) -> tuple[str, list[dict[str, Any]], dict[str, Any], bool]:
    try:
        chain = CatalogService().get_sellable_category_chain(category, for_update=True)
        leaf = chain[0]
        return (
            str(leaf["name"]),
            resolve_attributes(chain, include_dependency_map=True),
            resolve_pricing(chain),
            bool(leaf.get("is_service")),
        )
    except CatalogError:
        raise
    except Exception as exc:
        raise CatalogValidationError("Invalid category schema.", code="INVALID_CATEGORY_SCHEMA") from exc


def normalize_details(details: Any, *, category: Any) -> list[dict[str, Any]]:
    category_id, attributes, _pricing, _is_service = _catalog_schema(category)
    del category_id
    if len(attributes) > MAX_AD_ATTRIBUTES:
        raise AdsValidationError("Category schema has too many attributes.", code="INVALID_CATEGORY_SCHEMA")
    rows = normalize_json_list(details, field="details", max_items=MAX_AD_ATTRIBUTES)
    by_id = {str(item["id"]): item for item in attributes}
    by_key = {str(item["key"]): item for item in attributes}
    required = {str(item["id"]) for item in attributes if int(item.get("required") or 0)}
    seen: set[str] = set()
    provided: set[str] = set()
    result: list[dict[str, Any]] = []

    for raw in rows:
        ensure_known_fields(raw, _DETAIL_KEYS)
        raw_attribute = normalize_identifier(raw.get("attribute"), field="attribute", required=True, max_length=140)
        schema = by_id.get(raw_attribute) or by_key.get(raw_attribute)
        if not schema:
            raise AdsValidationError("Invalid category attribute.", code="INVALID_CATEGORY_SCHEMA")
        attribute_id = str(schema["id"])
        if attribute_id in seen:
            raise AdsValidationError("Duplicate category attribute.", code="DUPLICATE_AD_ATTRIBUTE")
        seen.add(attribute_id)
        field_type = str(schema.get("type") or "Text")
        options = [str(option) for option in (schema.get("options") or [])]
        row: dict[str, Any] = {
            "attribute": attribute_id,
            "attribute_key": str(schema.get("key") or attribute_key(attribute_id)),
            "attribute_label": str(schema.get("label") or attribute_id)[:140],
            "attribute_type": field_type,
            "attribute_unit": str(schema.get("unit") or "")[:80],
            "value_text": None,
            "value_number": None,
            "value_date": None,
            "value_bool": 0,
            "value_json": None,
        }
        has_value = False
        if field_type in {"Text", "Textarea", "Select"}:
            max_length = MAX_ATTRIBUTE_TEXTAREA_LENGTH if field_type == "Textarea" else MAX_ATTRIBUTE_TEXT_LENGTH
            value = normalize_text(
                raw.get("value_text"),
                field=str(schema.get("label") or attribute_id),
                max_length=max_length,
                required=attribute_id in required,
                multiline=field_type == "Textarea",
            )
            if value:
                if field_type == "Select" and options and value not in options:
                    raise AdsValidationError("Invalid category attribute option.", code="INVALID_CATEGORY_SCHEMA")
                row["value_text"] = value
                has_value = True
        elif field_type == "Number":
            value = normalize_decimal(
                raw.get("value_number"),
                field=str(schema.get("label") or attribute_id),
                required=attribute_id in required,
            )
            if value is not None:
                row["value_number"] = decimal_storage(value)
                has_value = True
        elif field_type == "Boolean":
            if raw.get("value_bool") in (None, "") and attribute_id not in required:
                has_value = False
            else:
                row["value_bool"] = normalize_flag(raw.get("value_bool"), field=attribute_id)
                has_value = True
        elif field_type == "Date":
            value = normalize_date(raw.get("value_date"), field=attribute_id, required=attribute_id in required)
            if value is not None:
                row["value_date"] = value.isoformat()
                has_value = True
        elif field_type == "Year":
            raw_year = raw.get("value_text") if raw.get("value_text") not in (None, "") else raw.get("value_number")
            if raw_year in (None, "") and attribute_id not in required:
                year = None
            else:
                year = normalize_int(
                    raw_year,
                    field=attribute_id,
                    minimum=1000,
                    maximum=9999,
                )
            if year is not None:
                value = str(year)
                if not _YEAR_RE.fullmatch(value):
                    raise AdsValidationError("Invalid year attribute.", code="INVALID_CATEGORY_SCHEMA")
                row["value_text"] = value
                has_value = True
        elif field_type == "MultiSelect":
            raw_value = raw.get("value_json")
            if raw_value in (None, ""):
                values: list[Any] = []
            elif isinstance(raw_value, str):
                try:
                    values = json.loads(raw_value)
                except Exception:
                    raise AdsValidationError("Invalid multi-select attribute.", code="INVALID_CATEGORY_SCHEMA") from None
            else:
                values = raw_value
            if not isinstance(values, list) or len(values) > 100:
                raise AdsValidationError("Invalid multi-select attribute.", code="INVALID_CATEGORY_SCHEMA")
            clean_values: list[str] = []
            seen_values: set[str] = set()
            for item in values:
                clean = normalize_text(item, field=attribute_id, max_length=120, required=True)
                if options and clean not in options:
                    raise AdsValidationError("Invalid category attribute option.", code="INVALID_CATEGORY_SCHEMA")
                if clean not in seen_values:
                    seen_values.add(clean)
                    clean_values.append(clean)
            if clean_values:
                serialized = json.dumps(clean_values, separators=(",", ":"), ensure_ascii=False)
                if len(serialized.encode("utf-8")) > MAX_ATTRIBUTE_JSON_LENGTH:
                    raise AdsValidationError("Category attribute value is too large.", code="INVALID_CATEGORY_SCHEMA")
                row["value_json"] = serialized
                has_value = True
            elif attribute_id in required:
                raise AdsValidationError("Required category attribute is missing.", code="INVALID_CATEGORY_SCHEMA")
        else:
            raise AdsValidationError("Invalid category attribute type.", code="INVALID_CATEGORY_SCHEMA")

        if has_value:
            provided.add(attribute_id)
            result.append(row)

    missing = sorted(required.difference(provided))
    if missing:
        raise AdsValidationError("Required category details are missing.", code="INVALID_CATEGORY_SCHEMA")

    selected_values = {
        str(row["attribute"]): str(row.get("value_text") or "")
        for row in result
        if row.get("value_text") not in (None, "")
    }
    for schema in attributes:
        dependency = schema.get("depends_on")
        if not dependency:
            continue
        attribute_id = str(schema["id"])
        child_value = selected_values.get(attribute_id)
        if not child_value:
            continue
        parent_id = str(dependency.get("id") or "")
        parent_value = selected_values.get(parent_id)
        if not parent_value:
            raise AdsValidationError(
                "Dependent category attribute requires its parent attribute.",
                code="INVALID_CATEGORY_SCHEMA",
            )
        allowed = set((schema.get("_dependency_options") or {}).get(parent_value, []))
        if child_value not in allowed:
            raise AdsValidationError(
                "Category attribute option is not valid for the selected parent option.",
                code="INVALID_CATEGORY_SCHEMA",
            )
    return result


def normalize_images(
    images: Any,
    *,
    require_images: bool = True,
    require_primary: bool | None = None,
) -> list[dict[str, Any]]:
    rows = normalize_json_list(images, field="images", max_items=MAX_IMAGES)
    if require_primary is None:
        require_primary = require_images
    if require_images and not rows:
        raise AdsValidationError("At least one image is required.", code="AD_MEDIA_REQUIRED")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    primary_count = 0
    for index, raw in enumerate(rows):
        ensure_known_fields(raw, _IMAGE_KEYS)
        media_value = raw.get("media") or raw.get("media_id") or raw.get("id")
        if isinstance(media_value, dict):
            media_value = media_value.get("media_id") or media_value.get("id") or media_value.get("name")
        media_id = normalize_identifier(media_value, field="image media", required=True, max_length=140)
        if media_id in seen:
            raise AdsValidationError("Duplicate image selected.", code="DUPLICATE_AD_MEDIA")
        seen.add(media_id)
        is_primary = normalize_flag(raw.get("is_primary"), field="is_primary", default=0)
        primary_count += is_primary
        sort_order = normalize_int(
            raw.get("sort_order"),
            field="sort_order",
            default=index,
            minimum=0,
            maximum=MAX_SORT_ORDER,
        )
        result.append({"media": media_id, "media_id": media_id, "is_primary": is_primary, "sort_order": sort_order})
    if primary_count > 1 or (require_primary and primary_count != 1):
        raise AdsValidationError("Exactly one primary image is required.", code="AD_PRIMARY_IMAGE_REQUIRED")
    return result


def normalize_pricing(payload: Mapping[str, Any], *, category: Any) -> dict[str, Any]:
    _category_id, _attributes, pricing, is_service = _catalog_schema(category)
    requirement = str(pricing.get("pricing_requirement") or "Optional")
    allowed_types = set(pricing.get("allowed_price_types") or ALLOWED_PRICE_TYPES)
    allowed_units = set(pricing.get("allowed_price_units") or [])

    price_type = normalize_text(payload.get("price_type"), field="price_type", max_length=40, required=requirement == "Required")
    price_unit = normalize_text(payload.get("price_unit"), field="price_unit", max_length=MAX_PRICE_UNIT_LENGTH)
    price = normalize_decimal(payload.get("price"), field="price")
    offer_price = normalize_decimal(payload.get("offer_price"), field="offer_price")
    offer_start = normalize_date(payload.get("offer_start_date"), field="offer_start_date")
    offer_end = normalize_date(payload.get("offer_end_date"), field="offer_end_date")

    if requirement == "Hidden":
        if price_type or price_unit or price is not None or offer_price is not None or offer_start or offer_end:
            raise AdsValidationError("Pricing is not allowed for this category.", code="INVALID_AD_INPUT")
        return {
            "price_type": "",
            "price": None,
            "price_unit": "",
            "offer_price": None,
            "offer_start_date": None,
            "offer_end_date": None,
        }
    if price_type and price_type not in ALLOWED_PRICE_TYPES:
        raise AdsValidationError("Invalid price type.", code="INVALID_AD_INPUT")
    if price_type and allowed_types and price_type not in allowed_types:
        raise AdsValidationError("Price type is not allowed for this category.", code="INVALID_CATEGORY_SCHEMA")
    if price_type in PRICE_TYPES_REQUIRING_AMOUNT:
        if price is None or price <= 0:
            raise AdsValidationError("Price must be greater than zero.", code="AD_PRICE_REQUIRED")
        if is_service:
            if not price_unit:
                raise AdsValidationError("Price unit is required for services.", code="INVALID_AD_INPUT")
            if allowed_units and price_unit not in allowed_units:
                raise AdsValidationError("Invalid price unit.", code="INVALID_CATEGORY_SCHEMA")
        elif price_unit:
            raise AdsValidationError("Price unit is only valid for services.", code="INVALID_AD_INPUT")
    elif price_type in PRICE_TYPES_WITHOUT_AMOUNT:
        if price not in (None, Decimal("0").quantize(_MONEY_QUANTUM)):
            raise AdsValidationError("Numeric price is not allowed for this price type.", code="INVALID_AD_INPUT")
        if price_unit:
            raise AdsValidationError("Price unit is not valid for this price type.", code="INVALID_AD_INPUT")
        price = None
    elif requirement == "Required":
        raise AdsValidationError("Price type is required.", code="AD_PRICE_REQUIRED")

    if offer_price is not None:
        if price_type != "Fixed":
            raise AdsValidationError("Offers are only valid for fixed-price ads.", code="INVALID_AD_INPUT")
        if offer_price <= 0:
            raise AdsValidationError("Offer price must be greater than zero.", code="INVALID_AD_INPUT")
        if price is None or offer_price >= price:
            raise AdsValidationError("Offer price must be lower than price.", code="INVALID_AD_INPUT")
    else:
        offer_start = None
        offer_end = None
    if offer_start and offer_end and offer_start > offer_end:
        raise AdsValidationError("Offer start date cannot be after offer end date.", code="INVALID_AD_INPUT")

    return {
        "price_type": price_type,
        "price": decimal_storage(price),
        "price_unit": price_unit,
        "offer_price": decimal_storage(offer_price),
        "offer_start_date": offer_start.isoformat() if offer_start else None,
        "offer_end_date": offer_end.isoformat() if offer_end else None,
    }


def normalize_full_ad_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    ensure_known_fields(payload, CREATE_FIELDS)
    title = normalize_text(
        payload.get("title"),
        field="title",
        max_length=MAX_TITLE_LENGTH,
        min_length=MIN_TITLE_LENGTH,
        required=True,
    )
    description = normalize_text(
        payload.get("description"),
        field="description",
        max_length=MAX_DESCRIPTION_LENGTH,
        min_length=MIN_DESCRIPTION_LENGTH,
        required=True,
        multiline=True,
    )
    category = normalize_identifier(payload.get("category"), field="category", required=True)
    category_id = CatalogService().assert_sellable_category(category, for_update=True)
    location = normalize_identifier(payload.get("location"), field="location", required=True)
    details = normalize_details(payload.get("details"), category=category_id)
    images = normalize_images(payload.get("images"), require_images=True)
    video_media = normalize_identifier(
        payload.get("video_media") or payload.get("video_media_id") or payload.get("video"),
        field="video_media",
        required=False,
    )
    pricing = normalize_pricing(payload, category=category_id)
    return {
        "title": title,
        "description": description,
        "category": category_id,
        "location": location,
        "details": details,
        "images": images,
        "video_media": video_media or None,
        **pricing,
    }


def normalize_active_update(payload: Mapping[str, Any], *, existing: Mapping[str, Any]) -> dict[str, Any]:
    ensure_known_fields(payload, ACTIVE_UPDATE_FIELDS)
    updates: dict[str, Any] = {}
    if "title" in payload:
        updates["title"] = normalize_text(
            payload.get("title"),
            field="title",
            max_length=MAX_TITLE_LENGTH,
            min_length=MIN_TITLE_LENGTH,
            required=True,
        )
    if "description" in payload:
        updates["description"] = normalize_text(
            payload.get("description"),
            field="description",
            max_length=MAX_DESCRIPTION_LENGTH,
            min_length=MIN_DESCRIPTION_LENGTH,
            required=True,
            multiline=True,
        )
    pricing_fields = {
        "price_type",
        "price",
        "price_unit",
        "offer_price",
        "offer_start_date",
        "offer_end_date",
    }
    if any(field in payload for field in pricing_fields):
        merged = {field: existing.get(field) for field in pricing_fields}
        merged.update({field: payload.get(field) for field in pricing_fields if field in payload})
        updates.update(normalize_pricing(merged, category=existing.get("category")))
    if not updates:
        raise AdsValidationError("No editable fields were provided.", code="INVALID_AD_INPUT")
    return updates


def normalize_pagination(payload: Mapping[str, Any]) -> tuple[int, int]:
    limit = normalize_int(payload.get("limit"), field="limit", default=DEFAULT_PAGE_SIZE, minimum=1, maximum=MAX_PAGE_SIZE)
    offset = normalize_int(payload.get("offset"), field="offset", default=0, minimum=0, maximum=MAX_OFFSET)
    return limit, offset


def _normalize_listing_filters(
    payload: Mapping[str, Any],
    *,
    allowed_fields: Iterable[str],
    include_cursor: bool,
    default_sort: str = "rating_high",
) -> dict[str, Any]:
    ensure_known_fields(payload, allowed_fields)
    limit, offset = normalize_pagination(payload)
    try:
        sort = normalize_text(
            payload.get("sort") or default_sort,
            field="sort",
            max_length=32,
            required=True,
        )
    except AdsValidationError as exc:
        raise AdsValidationError("Invalid sort.", code="VALIDATION_ERROR") from exc
    if sort not in PUBLIC_LIST_SORTS:
        raise AdsValidationError("Invalid sort.", code="VALIDATION_ERROR")
    promotion = normalize_text(payload.get("promotion_type"), field="promotion_type", max_length=32)
    if promotion and promotion not in PUBLIC_PROMOTION_TYPES:
        raise AdsValidationError("Invalid promotion type.", code="INVALID_AD_INPUT")
    price_type = normalize_text(payload.get("price_type"), field="price_type", max_length=40)
    if price_type and price_type not in ALLOWED_PRICE_TYPES:
        raise AdsValidationError("Invalid price type.", code="INVALID_AD_INPUT")
    price_min = normalize_decimal(payload.get("price_min"), field="price_min", minimum=Decimal("0"))
    price_max = normalize_decimal(payload.get("price_max"), field="price_max", minimum=Decimal("0"))
    if price_min is not None and price_max is not None and price_min > price_max:
        raise AdsValidationError("price_min cannot be greater than price_max.", code="INVALID_AD_INPUT")
    rating_min = normalize_decimal(payload.get("rating_min"), field="rating_min", minimum=Decimal("0"), maximum=Decimal("5"))
    return {
        "country": normalize_identifier(payload.get("country"), field="country"),
        "currency": normalize_identifier(payload.get("currency"), field="currency"),
        "location": normalize_identifier(payload.get("location"), field="location"),
        "category": normalize_identifier(payload.get("category"), field="category"),
        "seller": normalize_identifier(payload.get("seller"), field="seller"),
        "q": normalize_text(payload.get("q"), field="q", max_length=MAX_SEARCH_QUERY_LENGTH),
        "price_type": price_type,
        "promotion_type": promotion,
        "price_min": decimal_storage(price_min),
        "price_max": decimal_storage(price_max),
        "rating_min": decimal_storage(rating_min),
        "verified_seller": normalize_flag(payload.get("verified_seller"), field="verified_seller", default=0),
        "sort": sort,
        "limit": limit,
        "offset": offset,
        "cursor": (
            normalize_text(payload.get("cursor"), field="cursor", max_length=500)
            if include_cursor
            else ""
        ),
    }


def normalize_public_list_filters(payload: Mapping[str, Any]) -> dict[str, Any]:
    from .constants import PUBLIC_LIST_FIELDS

    return _normalize_listing_filters(
        payload,
        allowed_fields=PUBLIC_LIST_FIELDS,
        include_cursor=True,
    )


def normalize_wishlist_list_filters(payload: Mapping[str, Any]) -> dict[str, Any]:
    from .constants import WISHLIST_LIST_FIELDS

    return _normalize_listing_filters(
        payload,
        allowed_fields=WISHLIST_LIST_FIELDS,
        include_cursor=True,
        default_sort="recent",
    )



def encode_wishlist_cursor(*, saved_on: Any, name: Any) -> str:
    payload = {
        "v": 1,
        "scope": "wishlist",
        "sort": "recent",
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
    if (
        not isinstance(payload, dict)
        or payload.get("v") != 1
        or payload.get("scope") != "wishlist"
        or payload.get("sort") != "recent"
    ):
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

def encode_recent_cursor(*, creation: Any, name: Any) -> str:
    payload = {"v": 1, "sort": "recent", "creation": str(creation or ""), "name": str(name or "")}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_recent_cursor(value: Any) -> tuple[str, str]:
    token = normalize_text(value, field="cursor", max_length=500, required=True)
    try:
        padded = token + "=" * (-len(token) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except Exception:
        raise AdsValidationError("Invalid pagination cursor.", code="INVALID_AD_CURSOR") from None
    if not isinstance(payload, dict) or payload.get("v") != 1 or payload.get("sort") != "recent":
        raise AdsValidationError("Invalid pagination cursor.", code="INVALID_AD_CURSOR")
    creation = normalize_text(payload.get("creation"), field="cursor", max_length=64, required=True)
    name = normalize_identifier(payload.get("name"), field="cursor", required=True)
    return creation, name


def _json_safe(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        raise AdsValidationError("Draft payload is too deeply nested.", code="INVALID_AD_INPUT")
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, str):
            normalized = unicodedata.normalize("NFC", value)
            if _CONTROL_RE.search(normalized):
                raise AdsValidationError("Invalid draft payload.", code="INVALID_AD_INPUT")
            return normalized
        return value
    if isinstance(value, list):
        if len(value) > 200:
            raise AdsValidationError("Draft payload is too large.", code="INVALID_AD_INPUT")
        return [_json_safe(item, depth=depth + 1) for item in value]
    if isinstance(value, dict):
        if len(value) > 100:
            raise AdsValidationError("Draft payload is too large.", code="INVALID_AD_INPUT")
        return {normalize_text(str(key), field="draft key", max_length=80, required=True): _json_safe(item, depth=depth + 1) for key, item in value.items()}
    raise AdsValidationError("Invalid draft payload.", code="INVALID_AD_INPUT")


def normalize_draft_payload(value: Any) -> dict[str, Any]:
    parsed = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            raise AdsValidationError("Invalid draft payload.", code="INVALID_AD_INPUT") from None
    if not isinstance(parsed, dict):
        raise AdsValidationError("Invalid draft payload.", code="INVALID_AD_INPUT")
    ensure_known_fields(parsed, _DRAFT_PAYLOAD_FIELDS)
    safe = _json_safe(parsed)
    for field, max_length, multiline in (
        ("title", MAX_TITLE_LENGTH, False),
        ("description", MAX_DESCRIPTION_LENGTH, True),
        ("category", 140, False),
        ("location", 140, False),
        ("price_type", 40, False),
        ("price_unit", MAX_PRICE_UNIT_LENGTH, False),
    ):
        if field in safe:
            safe[field] = normalize_text(safe.get(field), field=field, max_length=max_length, multiline=multiline)
    if "images" in safe:
        # Incomplete drafts may have zero images, but supplied rows must be valid.
        safe["images"] = normalize_images(
            safe.get("images"),
            require_images=False,
            require_primary=False,
        )
    if "details" in safe:
        raw_details = safe.get("details")
        if not isinstance(raw_details, list) or any(not isinstance(row, dict) for row in raw_details):
            raise AdsValidationError("Invalid draft details.", code="INVALID_AD_INPUT")
        if len(raw_details) > MAX_AD_ATTRIBUTES:
            raise AdsValidationError("Too many draft details.", code="INVALID_AD_INPUT")
        for row in raw_details:
            ensure_known_fields(row, _DETAIL_KEYS)
    serialized = json.dumps(safe, separators=(",", ":"), ensure_ascii=False)
    if len(serialized.encode("utf-8")) > MAX_DRAFT_PAYLOAD_BYTES:
        raise AdsValidationError("Draft payload is too large.", code="INVALID_AD_INPUT")
    return safe


def normalize_draft_request(payload: Mapping[str, Any]) -> tuple[str, dict[str, Any], int]:
    ensure_known_fields(payload, DRAFT_UPSERT_FIELDS)
    draft_id = normalize_identifier(payload.get("draft_id") or payload.get("id"), field="draft_id")
    draft_payload = normalize_draft_payload(payload.get("payload") if "payload" in payload else payload.get("payload_json"))
    last_step = normalize_int(payload.get("last_step"), field="last_step", default=1, minimum=1, maximum=MAX_DRAFT_STEP)
    return draft_id, draft_payload, last_step


def normalize_report_details(value: Any) -> str:
    return normalize_text(value, field="details", max_length=MAX_REPORT_DETAILS_LENGTH, multiline=True)

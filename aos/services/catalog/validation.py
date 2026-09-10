"""Central Catalog normalization and validation."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable
from typing import Any

import frappe

from .constants import (
    ALLOWED_ATTRIBUTE_TYPES,
    ALLOWED_PRICE_TYPES,
    ALLOWED_PRICING_REQUIREMENTS,
    ATTRIBUTE_HELP_TEXT_MAX_LENGTH,
    ATTRIBUTE_LABEL_MAX_LENGTH,
    ATTRIBUTE_UNIT_MAX_LENGTH,
    CATEGORY_ID_MAX_LENGTH,
    CATEGORY_NAME_MAX_LENGTH,
    MAX_ATTRIBUTE_OPTIONS,
    MAX_CATEGORY_ATTRIBUTES,
    MAX_OPTIONS,
    MAX_SORT_ORDER,
    OPTION_MAX_LENGTH,
    SELECT_ATTRIBUTE_TYPES,
)
from .errors import CatalogValidationError

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SPACE_RE = re.compile(r"[ \t]+")


def normalize_text(
    value: Any,
    *,
    field: str,
    max_length: int,
    required: bool = False,
    multiline: bool = False,
) -> str:
    """Normalize a scalar text value while rejecting structured input."""

    if isinstance(value, (dict, list, tuple, set)):
        raise CatalogValidationError(f"Invalid {field}.", code="INVALID_CATALOG_INPUT")
    if value is None:
        text = ""
    elif isinstance(value, str):
        text = value
    else:
        raise CatalogValidationError(f"Invalid {field}.", code="INVALID_CATALOG_INPUT")

    text = unicodedata.normalize("NFC", text)
    if _CONTROL_RE.search(text):
        raise CatalogValidationError(f"Invalid {field}.", code="INVALID_CATALOG_INPUT")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if multiline:
        lines = [_SPACE_RE.sub(" ", line.strip()) for line in text.splitlines()]
        text = "\n".join(line for line in lines if line)
    else:
        text = " ".join(text.split())
    if required and not text:
        raise CatalogValidationError(
            f"{field.replace('_', ' ').title()} is required.",
            code="INVALID_CATALOG_INPUT",
        )
    if len(text) > max_length:
        raise CatalogValidationError(
            f"{field.replace('_', ' ').title()} is too long.",
            code="INVALID_CATALOG_INPUT",
        )
    return text


def normalize_category_id(value: Any, *, required: bool = True) -> str:
    return normalize_text(
        value,
        field="category",
        max_length=CATEGORY_ID_MAX_LENGTH,
        required=required,
    )


def normalize_flag(value: Any, *, field: str) -> int:
    if value in (None, "", 0, False, "0"):
        return 0
    if value in (1, True, "1"):
        return 1
    raise CatalogValidationError(f"Invalid {field}.", code="INVALID_CATEGORY_SCHEMA")


def normalize_sort_order(value: Any) -> int:
    if value in (None, ""):
        return 0
    if isinstance(value, bool) or isinstance(value, (dict, list, tuple, set, float)):
        raise CatalogValidationError("Invalid sort order.", code="INVALID_CATEGORY_SCHEMA")
    if isinstance(value, int):
        result = value
    elif isinstance(value, str) and value.strip().isdigit():
        result = int(value.strip())
    else:
        raise CatalogValidationError("Invalid sort order.", code="INVALID_CATEGORY_SCHEMA")
    if result < 0 or result > MAX_SORT_ORDER:
        raise CatalogValidationError("Invalid sort order.", code="INVALID_CATEGORY_SCHEMA")
    return result


def split_choices(
    value: Any,
    *,
    field: str,
    allowed: Iterable[str] | None = None,
    max_items: int = MAX_OPTIONS,
) -> list[str]:
    """Normalize newline-delimited choices with stable de-duplication."""

    if value in (None, ""):
        return []
    if not isinstance(value, str):
        raise CatalogValidationError(f"Invalid {field}.", code="INVALID_CATEGORY_SCHEMA")
    raw_items = value.splitlines()

    allowed_set = set(allowed or ())
    result: list[str] = []
    seen: set[str] = set()
    for raw in raw_items:
        item = normalize_text(raw, field=field, max_length=OPTION_MAX_LENGTH, required=True)
        key = item.casefold()
        if key in seen:
            continue
        if allowed_set and item not in allowed_set:
            raise CatalogValidationError(f"Invalid {field} value.", code="INVALID_CATEGORY_SCHEMA")
        seen.add(key)
        result.append(item)
        if len(result) > max_items:
            raise CatalogValidationError(f"Too many {field} values.", code="INVALID_CATEGORY_SCHEMA")
    return result



def canonical_attribute_key(label: Any) -> str:
    """Build a stable client key from the immutable attribute identity label."""

    normalized = normalize_text(
        label,
        field="attribute_label",
        max_length=ATTRIBUTE_LABEL_MAX_LENGTH,
        required=True,
    )
    decomposed = unicodedata.normalize("NFKD", normalized)
    ascii_text = decomposed.encode("ascii", "ignore").decode("ascii").lower()
    key = re.sub(r"[^a-z0-9]+", "_", ascii_text).strip("_")
    if not key:
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
        key = f"attribute_{digest}"
    return key[:80].rstrip("_")



def reject_unknown_fields(payload: dict[str, Any], *, allowed: set[str]) -> None:
    unknown = sorted(str(key) for key in payload if key not in allowed)
    if unknown:
        raise CatalogValidationError("Unknown Catalog request field.", code="INVALID_CATALOG_INPUT")

def validate_category_document(doc: Any) -> None:
    """Normalize and enforce the repository's two-level category tree contract."""

    doc.category_name = normalize_text(
        getattr(doc, "category_name", None),
        field="category_name",
        max_length=CATEGORY_NAME_MAX_LENGTH,
        required=True,
    )
    doc.sort_order = normalize_sort_order(getattr(doc, "sort_order", 0))
    doc.is_group = normalize_flag(getattr(doc, "is_group", 0), field="is_group")
    doc.is_active = normalize_flag(getattr(doc, "is_active", 0), field="is_active")
    doc.is_service = normalize_flag(getattr(doc, "is_service", 0), field="is_service")

    parent = normalize_category_id(getattr(doc, "parent_aos_category", None), required=False)
    current_name = str(getattr(doc, "name", "") or doc.category_name).strip()
    if parent and parent in {current_name, doc.category_name}:
        raise CatalogValidationError("A category cannot be its own parent.", code="INVALID_CATEGORY_TREE")
    if doc.is_group and parent:
        raise CatalogValidationError("Category groups must be root categories.", code="INVALID_CATEGORY_TREE")
    if parent:
        parent_row = frappe.db.get_value(
            "AOS Category",
            parent,
            ["name", "is_group", "parent_aos_category"],
            as_dict=True,
        )
        if not parent_row:
            raise CatalogValidationError("Parent category was not found.", code="INVALID_CATEGORY_TREE")
        if not int(parent_row.get("is_group") or 0) or parent_row.get("parent_aos_category"):
            raise CatalogValidationError("Parent category must be a root group.", code="INVALID_CATEGORY_TREE")
    doc.parent_aos_category = parent or None

    requirement = normalize_text(
        getattr(doc, "pricing_requirement", None) or "Optional",
        field="pricing_requirement",
        max_length=16,
        required=True,
    )
    if requirement not in ALLOWED_PRICING_REQUIREMENTS:
        raise CatalogValidationError("Invalid pricing requirement.", code="INVALID_CATEGORY_SCHEMA")
    price_types = split_choices(
        getattr(doc, "allowed_price_types", None),
        field="price_type",
        allowed=ALLOWED_PRICE_TYPES,
    )
    price_units = split_choices(
        getattr(doc, "allowed_price_units", None),
        field="price_unit",
        max_items=50,
    )
    if requirement == "Hidden":
        price_types = []
        price_units = []
    elif requirement == "Required" and not price_types:
        raise CatalogValidationError("Required pricing needs at least one price type.", code="INVALID_CATEGORY_SCHEMA")
    if not doc.is_service and price_units:
        raise CatalogValidationError("Price units are only valid for service categories.", code="INVALID_CATEGORY_SCHEMA")
    doc.pricing_requirement = requirement
    doc.allowed_price_types = "\n".join(price_types)
    doc.allowed_price_units = "\n".join(price_units)

    _validate_category_attribute_rows(doc)


def _validate_category_attribute_rows(doc: Any) -> None:
    rows = list(getattr(doc, "attributes", None) or [])
    if len(rows) > MAX_CATEGORY_ATTRIBUTES:
        raise CatalogValidationError("Too many category attributes.", code="INVALID_CATEGORY_SCHEMA")

    attribute_names: list[str] = []
    seen: set[str] = set()
    for row in rows:
        attribute = normalize_text(
            getattr(row, "attribute", None),
            field="attribute",
            max_length=ATTRIBUTE_LABEL_MAX_LENGTH,
            required=True,
        )
        key = attribute.casefold()
        if key in seen:
            raise CatalogValidationError("Duplicate category attribute.", code="INVALID_CATEGORY_SCHEMA")
        seen.add(key)
        attribute_names.append(attribute)
        row.attribute = attribute
        row.sort_order = normalize_sort_order(getattr(row, "sort_order", 0))
        row.is_active = normalize_flag(getattr(row, "is_active", 0), field="attribute is_active")
        row.is_required = normalize_flag(getattr(row, "is_required", 0), field="attribute is_required")
        if not row.is_active and row.is_required:
            raise CatalogValidationError("Inactive attributes cannot be required.", code="INVALID_CATEGORY_SCHEMA")

    if not attribute_names:
        return
    attribute_docs = {
        row["name"]: row
        for row in frappe.get_all(
            "AOS Ad Attribute",
            filters={"name": ["in", attribute_names]},
            fields=["name", "field_type", "options", "is_active"],
            limit=MAX_CATEGORY_ATTRIBUTES,
        )
    }
    if len(attribute_docs) != len(attribute_names):
        raise CatalogValidationError("Category attribute was not found.", code="INVALID_CATEGORY_SCHEMA")

    for row in rows:
        definition = attribute_docs[row.attribute]
        if row.is_active and not int(definition.get("is_active") or 0):
            raise CatalogValidationError("Inactive attribute cannot be enabled.", code="INVALID_CATEGORY_SCHEMA")
        field_type = str(definition.get("field_type") or "Text").strip()
        override = split_choices(
            getattr(row, "options_override", None),
            field="attribute_option",
            max_items=MAX_ATTRIBUTE_OPTIONS,
        )
        if override and field_type not in SELECT_ATTRIBUTE_TYPES:
            raise CatalogValidationError(
                "Only select attributes support option overrides.",
                code="INVALID_CATEGORY_SCHEMA",
            )
        split_choices(
            definition.get("options"),
            field="attribute_option",
            max_items=MAX_ATTRIBUTE_OPTIONS,
        )
        row.options_override = "\n".join(override)


def validate_attribute_document(doc: Any) -> None:
    doc.label = normalize_text(
        getattr(doc, "label", None),
        field="attribute_label",
        max_length=ATTRIBUTE_LABEL_MAX_LENGTH,
        required=True,
    )
    if bool(doc.is_new()):
        doc.attribute_key = canonical_attribute_key(doc.label)
    else:
        existing_key = normalize_text(
            getattr(doc, "attribute_key", None),
            field="attribute_key",
            max_length=80,
            required=True,
        )
        doc.attribute_key = existing_key
    field_type = normalize_text(
        getattr(doc, "field_type", None),
        field="attribute_type",
        max_length=32,
        required=True,
    )
    if field_type not in ALLOWED_ATTRIBUTE_TYPES:
        raise CatalogValidationError("Invalid attribute type.", code="INVALID_CATEGORY_SCHEMA")
    unit = normalize_text(
        getattr(doc, "unit", None),
        field="attribute_unit",
        max_length=ATTRIBUTE_UNIT_MAX_LENGTH,
    )
    help_text = normalize_text(
        getattr(doc, "help_text", None),
        field="attribute_help_text",
        max_length=ATTRIBUTE_HELP_TEXT_MAX_LENGTH,
        multiline=True,
    )
    options = split_choices(
        getattr(doc, "options", None),
        field="attribute_option",
        max_items=MAX_ATTRIBUTE_OPTIONS,
    )
    if field_type not in SELECT_ATTRIBUTE_TYPES and options:
        raise CatalogValidationError("Only select attributes support options.", code="INVALID_CATEGORY_SCHEMA")
    doc.field_type = field_type
    doc.unit = unit
    doc.help_text = help_text
    doc.options = "\n".join(options)
    doc.is_active = normalize_flag(getattr(doc, "is_active", 0), field="is_active")

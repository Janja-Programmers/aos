from __future__ import annotations

from typing import Any, Dict, List, Set

import frappe
from frappe.model.document import Document

from aos.api.attributes.schema import _get_category_chain, _resolve_attributes, _resolve_pricing


_PRICE_TYPES_REQUIRING_AMOUNT = {"Fixed", "Negotiable"}
_PRICE_TYPES_NO_AMOUNT = {"Contact for price", "Free"}

# Media limits
_MAX_IMAGES = 4
_MAX_VIDEO_MB = 20
_ALLOWED_VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".webm")


def _norm(val: Any) -> str:
    return (str(val or "").strip())


def _to_float(val: Any) -> float | None:
    if val in (None, ""):
        return None
    try:
        return float(val)
    except Exception:
        return None


def _to_int(val: Any) -> int | None:
    if val in (None, ""):
        return None
    try:
        return int(val)
    except Exception:
        return None


def _has_value(val: Any) -> bool:
    if val is None:
        return False
    if isinstance(val, str):
        return bool(val.strip())
    return True


def _split_multiselect(val: Any) -> List[str]:
    """Accept JSON list-ish, newline, or comma-separated multiselect values."""
    if val in (None, ""):
        return []

    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]

    s = str(val).strip()
    if not s:
        return []

    try:
        import json

        parsed = json.loads(s)
        if isinstance(parsed, list):
            return [str(x).strip() for x in parsed if str(x).strip()]
    except Exception:
        pass

    if "\n" in s:
        return [x.strip() for x in s.splitlines() if x.strip()]
    return [x.strip() for x in s.split(",") if x.strip()]


def _get_detail_value_for_type(row: Any, field_type: str):
    """Pick the correct value_* field from Ad Attribute Value row.

    Child table fields:
    - value_text, value_number, value_date, value_bool, value_json
    """
    ft = (field_type or "Text").strip()

    if ft in ("Text", "Textarea", "Select"):
        return getattr(row, "value_text", None)

    if ft == "Number":
        return getattr(row, "value_number", None)

    if ft == "Boolean":
        return getattr(row, "value_bool", None)

    if ft == "Date":
        return getattr(row, "value_date", None)

    if ft == "Year":
        return getattr(row, "value_text", None) or getattr(row, "value_number", None)

    if ft in ("MultiSelect", "Json"):
        return getattr(row, "value_json", None)

    return getattr(row, "value_text", None)


class AOSAd(Document):
    def validate(self):
        self._validate_details()
        self._validate_pricing()
        self._validate_media()

    def _validate_details(self) -> None:
        if not getattr(self, "category", None):
            return

        chain = _get_category_chain(self.category)
        allowed_attrs = _resolve_attributes(chain)

        allowed_by_id: Dict[str, Dict[str, Any]] = {a["id"]: a for a in allowed_attrs}
        required_ids: Set[str] = {a["id"] for a in allowed_attrs if int(a.get("required") or 0) == 1}

        rows = list(getattr(self, "details", []) or [])

        if required_ids and not rows:
            frappe.throw("Please fill all required details for the selected category.")

        seen_attr_ids: Set[str] = set()
        provided_required_ids: Set[str] = set()

        for row in rows:
            attr_id = _norm(getattr(row, "attribute", None))
            if not attr_id:
                frappe.throw("Each detail row must have an Attribute selected.")

            if attr_id in seen_attr_ids:
                frappe.throw(f"Duplicate attribute '{attr_id}' in details.")
            seen_attr_ids.add(attr_id)

            if attr_id not in allowed_by_id:
                frappe.throw(f"Attribute '{attr_id}' is not allowed for this category.")

            schema = allowed_by_id[attr_id]
            label = schema.get("label") or attr_id
            field_type = _norm(schema.get("type") or "Text")
            options = list(schema.get("options") or [])

            value = _get_detail_value_for_type(row, field_type)

            if attr_id in required_ids and not _has_value(value):
                frappe.throw(f"'{label}' is required.")

            if not _has_value(value):
                continue

            if field_type in ("Text", "Textarea"):
                pass

            elif field_type == "Select":
                v = _norm(value)
                if options and v not in options:
                    frappe.throw(
                        f"Invalid value '{v}' for '{label}'. Allowed: {', '.join(options)}"
                    )

            elif field_type == "Number":
                if _to_float(value) is None:
                    frappe.throw(f"'{label}' must be a number.")

            elif field_type == "Boolean":
                if int(value or 0) not in (0, 1):
                    frappe.throw(f"'{label}' must be Yes/No.")

            elif field_type == "Date":
                try:
                    frappe.utils.getdate(value)
                except Exception:
                    frappe.throw(f"'{label}' must be a valid date.")

            elif field_type == "Year":
                yr = _to_int(value)
                if yr is None:
                    # may come as float in value_number
                    yr = _to_int(_to_float(value))
                if yr is None or yr < 1900 or yr > 2100:
                    frappe.throw(f"'{label}' must be a valid year.")

            elif field_type == "MultiSelect":
                vals = _split_multiselect(value)
                if not vals:
                    frappe.throw(f"'{label}' must have at least one value.")
                if options:
                    invalid = [v for v in vals if v not in options]
                    if invalid:
                        frappe.throw(
                            f"Invalid value(s) {', '.join(invalid)} for '{label}'. "
                            f"Allowed: {', '.join(options)}"
                        )

            if attr_id in required_ids:
                provided_required_ids.add(attr_id)

        missing_required = [
            (allowed_by_id[aid].get("label") or aid)
            for aid in required_ids
            if aid not in provided_required_ids
        ]
        if missing_required:
            frappe.throw(f"Missing required details: {', '.join(missing_required)}")

    def _validate_pricing(self) -> None:
        if not getattr(self, "category", None):
            return

        chain = _get_category_chain(self.category)
        pricing = _resolve_pricing(chain)

        requirement = _norm(pricing.get("pricing_requirement") or "Optional")
        allowed_types: List[str] = list(pricing.get("allowed_price_types") or [])
        allowed_units: List[str] = list(pricing.get("allowed_price_units") or [])
        is_service = int(pricing.get("is_service") or 0)

        price_type = _norm(getattr(self, "price_type", None))
        price_unit = _norm(getattr(self, "price_unit", None))
        price_val = _to_float(getattr(self, "price", None))

        if requirement.lower() == "hidden":
            if price_type or price_unit or (price_val not in (None, 0.0)):
                frappe.throw("Pricing is not allowed for this category.")
            return

        if requirement.lower() == "optional" and not price_type:
            if price_unit or (price_val not in (None, 0.0)):
                frappe.throw("Please select a Price Type or clear the price fields.")
            return

        if requirement.lower() == "required" and not price_type:
            frappe.throw("Price Type is required for this category.")

        if price_type and allowed_types and price_type not in allowed_types:
            frappe.throw(
                f"Invalid Price Type '{price_type}' for this category. "
                f"Allowed: {', '.join(allowed_types)}"
            )

        if price_type in _PRICE_TYPES_REQUIRING_AMOUNT:
            if price_val is None or price_val <= 0:
                frappe.throw("Price must be greater than 0 for Fixed/Negotiable.")

            if is_service:
                if not price_unit:
                    frappe.throw("Price Unit is required for services.")
                if allowed_units and price_unit not in allowed_units:
                    frappe.throw(
                        f"Invalid Price Unit '{price_unit}' for this category. "
                        f"Allowed: {', '.join(allowed_units)}"
                    )
            else:
                if price_unit:
                    frappe.throw("Price Unit is only applicable for services.")

        elif price_type in _PRICE_TYPES_NO_AMOUNT:
            if price_val not in (None, 0.0):
                frappe.throw("Do not provide a numeric price for this Price Type.")
            if price_unit:
                frappe.throw("Price Unit is not applicable for this Price Type.")

        else:
            if price_type:
                frappe.throw("Invalid Price Type.")

    def _validate_media(self) -> None:
        images = list(getattr(self, "images", []) or [])

        if len(images) < 1:
            frappe.throw("Please upload at least 1 image.")

        if len(images) > _MAX_IMAGES:
            frappe.throw(f"You can upload a maximum of {_MAX_IMAGES} images.")

        primary_count = 0
        seen_urls: Set[str] = set()

        for idx, row in enumerate(images, start=1):
            img_url = _norm(getattr(row, "image", None))
            if not img_url:
                frappe.throw(f"Image is required on row {idx}.")

            if img_url in seen_urls:
                frappe.throw("You have selected the same image more than once.")
            seen_urls.add(img_url)

            if int(getattr(row, "is_primary", 0) or 0) == 1:
                primary_count += 1

        if primary_count != 1:
            frappe.throw("Please set exactly 1 primary image.")

        video_url = _norm(getattr(self, "video", None))
        if not video_url:
            return

        file_doc = frappe.db.get_value(
            "File",
            {"file_url": video_url},
            ["name", "file_size", "file_name"],
            as_dict=True,
        )

        if not file_doc:
            frappe.throw("Invalid video attachment. Please upload the video again.")
            return

        size_bytes = int(file_doc.file_size or 0)
        max_bytes = _MAX_VIDEO_MB * 1024 * 1024

        if size_bytes > max_bytes:
            frappe.throw(f"Video is too large. Maximum allowed is {_MAX_VIDEO_MB}MB.")

        fname = (file_doc.file_name or "").lower()
        if fname and not fname.endswith(_ALLOWED_VIDEO_EXTS):
            frappe.throw("Unsupported video format. Please upload MP4/MOV/WebM.")

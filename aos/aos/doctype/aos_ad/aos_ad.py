"""AOS Ad DocType server-side logic.

This enforces backend validations for Step 4 (Pricing) in a Jiji-style flow.
Category defines:
  - pricing_requirement: Required / Optional / Hidden
  - allowed_price_types: newline separated list
  - allowed_price_units: newline separated list (services)
  - is_service: whether price_unit is applicable

Important: "pricing required" means the user must make a *pricing decision*
(choose price_type) — NOT necessarily provide a numeric amount.
"""

from __future__ import annotations

from typing import Any, List

import frappe
from frappe.model.document import Document

from aos.api.attributes.schema import _get_category_chain, _resolve_pricing, _split_lines


_PRICE_TYPES_REQUIRING_AMOUNT = {"Fixed", "Negotiable"}
_PRICE_TYPES_NO_AMOUNT = {"Contact for price", "Free"}


def _norm(val: Any) -> str:
    return (str(val or "").strip())


def _to_float(val: Any) -> float | None:
    if val in (None, ""):
        return None
    try:
        return float(val)
    except Exception:
        return None


class AOSAd(Document):
    def validate(self):
        self._validate_pricing()

    # ---------------------------------------------------------------------
    # Pricing validations
    # ---------------------------------------------------------------------
    def _validate_pricing(self) -> None:
        if not getattr(self, "category", None):
            # Category is required earlier in the flow, but be defensive.
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

        # Hidden: pricing not allowed on this category
        if requirement.lower() == "hidden":
            if price_type or price_unit or (price_val not in (None, 0.0)):
                frappe.throw("Pricing is not allowed for this category.")
            return

        # If pricing is optional and user hasn't chosen a price type, accept
        if requirement.lower() == "optional" and not price_type:
            # Ensure no stray values
            if price_unit or (price_val not in (None, 0.0)):
                frappe.throw("Please select a Price Type or clear the price fields.")
            return

        # Required: user must choose price_type
        if requirement.lower() == "required" and not price_type:
            frappe.throw("Price Type is required for this category.")

        # If provided, price_type must be allowed
        if price_type and allowed_types and price_type not in allowed_types:
            frappe.throw(
                f"Invalid Price Type '{price_type}' for this category. "
                f"Allowed: {', '.join(allowed_types)}"
            )

        # Validate by price_type
        if price_type in _PRICE_TYPES_REQUIRING_AMOUNT:
            if price_val is None or price_val <= 0:
                frappe.throw("Price must be greater than 0 for Fixed/Negotiable.")

            # Services: unit required
            if is_service:
                if not price_unit:
                    frappe.throw("Price Unit is required for services.")
                if allowed_units and price_unit not in allowed_units:
                    frappe.throw(
                        f"Invalid Price Unit '{price_unit}' for this category. "
                        f"Allowed: {', '.join(allowed_units)}"
                    )
            else:
                # Goods: unit should not be set (keep data clean)
                if price_unit:
                    frappe.throw("Price Unit is only applicable for services.")

        elif price_type in _PRICE_TYPES_NO_AMOUNT:
            # For Contact for price / Free, amount should be empty/0 and unit empty
            if price_val not in (None, 0.0):
                frappe.throw("Do not provide a numeric price for this Price Type.")
            if price_unit:
                frappe.throw("Price Unit is not applicable for this Price Type.")

        else:
            # Unknown price type (or custom). Be strict.
            if price_type:
                frappe.throw("Invalid Price Type.")


from __future__ import annotations

from typing import Any, Dict, List, Set

import frappe
from frappe.model.document import Document
from frappe.utils import add_days, getdate, now

from aos.api.catalog.schema import (
    _get_category_chain,
    _resolve_attributes,
    _resolve_pricing,
)


_PRICE_TYPES_REQUIRING_AMOUNT = {
    "Fixed",
    "Negotiable",
}

_PRICE_TYPES_NO_AMOUNT = {
    "Contact for price",
    "Free",
}

# Media limits
_MAX_IMAGES = 4
_MAX_VIDEO_MB = 200
_ALLOWED_VIDEO_EXTS = (
    ".mp4",
    ".mov",
    ".m4v",
    ".webm",
)


# HELPERS
def _norm(val: Any) -> str:
    return str(val or "").strip()


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
    if val in (None, ""):
        return []

    if isinstance(val, list):
        return [
            str(x).strip()
            for x in val
            if str(x).strip()
        ]

    s = str(val).strip()

    if not s:
        return []

    try:
        import json

        parsed = json.loads(s)

        if isinstance(parsed, list):
            return [
                str(x).strip()
                for x in parsed
                if str(x).strip()
            ]

    except Exception:
        pass

    if "\n" in s:
        return [
            x.strip()
            for x in s.splitlines()
            if x.strip()
        ]

    return [
        x.strip()
        for x in s.split(",")
        if x.strip()
    ]


def _get_detail_value_for_type(
    row: Any,
    field_type: str,
):
    ft = (field_type or "Text").strip()

    if ft in (
        "Text",
        "Textarea",
        "Select",
    ):
        return getattr(
            row,
            "value_text",
            None,
        )

    if ft == "Number":
        return getattr(
            row,
            "value_number",
            None,
        )

    if ft == "Boolean":
        return getattr(
            row,
            "value_bool",
            None,
        )

    if ft == "Date":
        return getattr(
            row,
            "value_date",
            None,
        )

    if ft == "Year":
        return (
            getattr(
                row,
                "value_text",
                None,
            )
            or getattr(
                row,
                "value_number",
                None,
            )
        )

    if ft in (
        "MultiSelect",
        "Json",
    ):
        return getattr(
            row,
            "value_json",
            None,
        )

    return getattr(
        row,
        "value_text",
        None,
    )


class AOSAd(Document):
    def before_insert(self):
        if not self.expires_on:
            self.expires_on = add_days(
                now(),
                30,
            )

    def validate(self):
        self._normalize_fields()
        self._validate_seller_owner()
        self._validate_seller_active()
        self._validate_edit_permissions()
        self._validate_market_integrity()
        self._validate_location()
        self._validate_media()
        self._validate_details()
        self._validate_pricing()
        self._validate_offer()
        self._validate_content_quality()

    def before_save(self):
        self._stamp_review_metadata()

    def after_insert(self):
        if not self.seller:
            return

        frappe.db.sql(
            """
            UPDATE `tabAOS Seller`
            SET total_ads = total_ads + 1
            WHERE name = %s
            """,
            (self.seller,),
        )

    def on_trash(self):
        if not self.seller:
            return

        frappe.db.sql(
            """
            UPDATE `tabAOS Seller`
            SET total_ads = GREATEST(total_ads - 1, 0)
            WHERE name = %s
            """,
            (self.seller,),
        )

    # NORMALIZATION
    def _normalize_fields(self):
        if self.title:
            self.title = self.title.strip()

        if self.description:
            self.description = self.description.strip()

    # SELLER VALIDATION
    def _validate_seller_owner(self):
        if frappe.session.user == "Guest":
            return

        roles = frappe.get_roles(
            frappe.session.user
        )

        if (
            "System Manager" in roles
            or "AOS Moderator" in roles
        ):
            return

        seller_user = frappe.db.get_value(
            "AOS Seller",
            self.seller,
            "user",
        )

        if seller_user != frappe.session.user:
            frappe.throw(
                "You cannot modify ads belonging to another seller."
            )

    def _validate_seller_active(self):
        seller = frappe.db.get_value(
            "AOS Seller",
            self.seller,
            ["status"],
            as_dict=True,
        )

        if not seller:
            frappe.throw("Invalid seller.")

        if seller.status != "Active":
            frappe.throw(
                "Seller account is not active."
            )

    # EDIT PERMISSIONS
    def _validate_edit_permissions(self):
        """
        Protect ads that must not be edited while allowing trusted lifecycle
        transitions from the set_ad_status endpoint.

        Trusted status actions are supplied through:

            doc.flags.aos_status_action

        Supported trusted transitions:
        - mark_sold:      Active -> Sold
        - mark_available: Sold -> Active
        - renew:          Expired -> Active
        - delete:         Reviewing/Declined/Sold/Expired -> Deleted
        """

        if self.is_new():
            return

        previous = self.get_doc_before_save()

        old_status = _norm(
            previous.status
            if previous
            else frappe.db.get_value(
                "AOS Ad",
                self.name,
                "status",
            )
        )

        new_status = _norm(
            self.status
        )

        status_action = _norm(
            self.flags.get(
                "aos_status_action"
            )
        ).lower()

        # Deleted and suspended ads remain permanently locked.
        if old_status in (
            "Deleted",
            "Suspended",
        ):
            frappe.throw(
                "This ad cannot be modified."
            )

        # Trusted status transition from set_ad_status_impl.
        if status_action:
            valid_transitions = {
                "mark_sold": (
                    "Active",
                    "Sold",
                ),
                "mark_available": (
                    "Sold",
                    "Active",
                ),
                "renew": (
                    "Expired",
                    "Active",
                ),
            }

            if status_action == "delete":
                deletable_statuses = {
                    "Reviewing",
                    "Declined",
                    "Sold",
                    "Expired",
                }

                if (
                    old_status not in deletable_statuses
                    or new_status != "Deleted"
                ):
                    frappe.throw(
                        "Invalid ad deletion transition."
                    )

                return

            expected_transition = valid_transitions.get(
                status_action
            )

            if not expected_transition:
                frappe.throw(
                    "Invalid ad status action."
                )

            (
                expected_old_status,
                expected_new_status,
            ) = expected_transition

            if (
                old_status != expected_old_status
                or new_status != expected_new_status
            ):
                frappe.throw(
                    "Invalid ad status transition: "
                    f"{old_status} -> {new_status}."
                )

            return

        # Normal editing of a sold ad remains prohibited.
        if old_status == "Sold":
            frappe.throw(
                "Sold ads cannot be edited."
            )

    # REVIEW METADATA
    def _stamp_review_metadata(self):
        """
        Update review metadata only when status changes.

        Ensures seller edits do not overwrite moderation info.
        Also triggers notifications for moderation actions.
        """

        if frappe.session.user == "Guest":
            return

        previous = self.get_doc_before_save()

        if not previous:
            return

        if previous.status == self.status:
            return

        self.reviewed_by = frappe.session.user
        self.reviewed_on = now()

        try:
            from aos.services.notification_service import (
                NotificationService,
            )

            if self.status == "Active":
                NotificationService.notify_ad_approved(
                    user=self.seller,
                    ad_id=self.name,
                    title=self.title,
                )

            elif self.status == "Rejected":
                NotificationService.notify_ad_rejected(
                    user=self.seller,
                    ad_id=self.name,
                    title=self.title,
                )

        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                "AOS Ad Notification Failed",
            )

    # MARKET VALIDATION
    def _validate_market_integrity(self):
        user = frappe.db.get_value(
            "AOS Seller",
            self.seller,
            "user",
        )

        pref = frappe.db.get_value(
            "AOS User Preference",
            {"user": user},
            ["country"],
            as_dict=True,
        )

        if not pref:
            frappe.throw(
                "User preference not configured."
            )

        pref_country = _norm(
            pref.get("country")
        )

        ad_country = _norm(
            getattr(
                self,
                "country",
                None,
            )
        )

        if (
            pref_country
            and ad_country
            and pref_country != ad_country
        ):
            frappe.throw(
                "Ad country must match your market preference."
            )

    # LOCATION VALIDATION
    def _validate_location(self):
        if not getattr(
            self,
            "location",
            None,
        ):
            return

        location = _norm(
            self.location
        )

        loc = frappe.db.get_value(
            "AOS Location",
            location,
            [
                "country",
                "is_active",
            ],
            as_dict=True,
        )

        if not loc:
            frappe.throw(
                "Invalid location."
            )

        if int(loc.is_active or 0) != 1:
            frappe.throw(
                "This location is not available."
            )

        if (
            loc.country
            and self.country
            and loc.country != self.country
        ):
            frappe.throw(
                "Location does not belong to selected country."
            )

    # CONTENT VALIDATION
    def _validate_content_quality(self):
        if len(self.title or "") < 5:
            frappe.throw(
                "Title is too short."
            )

        if len(self.description or "") < 20:
            frappe.throw(
                "Description is too short."
            )

    # OFFER VALIDATION
    def _validate_offer(self):
        offer_price = _to_float(
            self.offer_price
        )

        price_val = _to_float(
            self.price
        )

        price_type = _norm(
            self.price_type
        )

        if offer_price in (
            None,
            0.0,
        ):
            self.offer_percent = 0
            self.offer_start_date = None
            self.offer_end_date = None
            return

        if price_type != "Fixed":
            frappe.throw(
                "Offers are only allowed for Fixed price ads."
            )

        if (
            price_val is None
            or price_val <= 0
        ):
            frappe.throw(
                "Set valid price before adding offer."
            )

        if offer_price >= price_val:
            frappe.throw(
                "Offer must be lower than price."
            )

        start = (
            getdate(self.offer_start_date)
            if self.offer_start_date
            else None
        )

        end = (
            getdate(self.offer_end_date)
            if self.offer_end_date
            else None
        )

        if (
            start
            and end
            and start > end
        ):
            frappe.throw(
                "Offer start date cannot be after end date."
            )

        self.offer_percent = round(
            (
                (
                    price_val
                    - offer_price
                )
                / price_val
            )
            * 100,
            2,
        )

    # MEDIA VALIDATION
    def _validate_media(self):
        images = list(
            self.images or []
        )

        if len(images) < 1:
            frappe.throw(
                "Upload at least 1 image."
            )

        if len(images) > _MAX_IMAGES:
            frappe.throw(
                f"Maximum {_MAX_IMAGES} images allowed."
            )

        primary_count = 0
        seen_media: Set[str] = set()

        for idx, row in enumerate(
            images,
            start=1,
        ):
            media_id = _norm(
                getattr(row, "media", None)
            )
            img = _norm(
                getattr(row, "image", None)
            )

            if not media_id and not img:
                frappe.throw(
                    f"Image media required on row {idx}."
                )

            unique_key = media_id or img

            if unique_key in seen_media:
                frappe.throw(
                    "Duplicate image selected."
                )

            seen_media.add(unique_key)

            if media_id:
                media = frappe.db.get_value(
                    "AOS Media Object",
                    media_id,
                    ["purpose", "status", "visibility"],
                    as_dict=True,
                )

                if not media:
                    frappe.throw(
                        f"Invalid image media on row {idx}."
                    )

                if media.purpose != "ad_image":
                    frappe.throw(
                        f"Invalid image media purpose on row {idx}."
                    )

                if media.visibility != "Public":
                    frappe.throw(
                        f"Image media must be public on row {idx}."
                    )

                if media.status not in {"Uploaded", "Attached"}:
                    frappe.throw(
                        f"Image media is not ready on row {idx}."
                    )

            if int(row.is_primary or 0) == 1:
                primary_count += 1

        if primary_count != 1:
            frappe.throw(
                "Exactly one primary image required."
            )

        video_media = _norm(
            getattr(self, "video_media", None)
        )

        if not video_media:
            return

        media = frappe.db.get_value(
            "AOS Media Object",
            video_media,
            ["purpose", "status", "visibility", "size_bytes", "content_type"],
            as_dict=True,
        )

        if not media:
            frappe.throw(
                "Invalid video media."
            )

        if media.purpose != "ad_video":
            frappe.throw(
                "Invalid video media purpose."
            )

        if media.visibility != "Public":
            frappe.throw(
                "Video media must be public."
            )

        if media.status not in {"Uploaded", "Attached"}:
            frappe.throw(
                "Video media is not ready."
            )

        size_bytes = int(
            media.size_bytes or 0
        )

        max_bytes = (
            _MAX_VIDEO_MB
            * 1024
            * 1024
        )

        if size_bytes > max_bytes:
            frappe.throw(
                f"Video exceeds {_MAX_VIDEO_MB}MB."
            )

        content_type = _norm(
            media.content_type
        ).lower()

        if content_type and not content_type.startswith("video/"):
            frappe.throw(
                "Unsupported video format."
            )

    # DETAILS VALIDATION
    def _validate_details(self):
        if not getattr(
            self,
            "category",
            None,
        ):
            return

        chain = _get_category_chain(
            self.category
        )

        allowed_attrs = _resolve_attributes(
            chain
        )

        allowed_by_id = {
            a["id"]: a
            for a in allowed_attrs
        }

        required_ids = {
            a["id"]
            for a in allowed_attrs
            if int(
                a.get("required") or 0
            ) == 1
        }

        rows = list(
            self.details or []
        )

        if required_ids and not rows:
            frappe.throw(
                "Please fill required details."
            )

        seen_attr_ids = set()
        provided_required_ids = set()

        for row in rows:
            attr_id = _norm(
                row.attribute
            )

            if not attr_id:
                frappe.throw(
                    "Each detail must have attribute."
                )

            if attr_id in seen_attr_ids:
                frappe.throw(
                    f"Duplicate attribute '{attr_id}'."
                )

            seen_attr_ids.add(
                attr_id
            )

            if attr_id not in allowed_by_id:
                frappe.throw(
                    f"Attribute '{attr_id}' not allowed."
                )

            schema = allowed_by_id[
                attr_id
            ]

            label = (
                schema.get("label")
                or attr_id
            )

            field_type = _norm(
                schema.get("type")
                or "Text"
            )

            options = list(
                schema.get("options")
                or []
            )

            value = _get_detail_value_for_type(
                row,
                field_type,
            )

            if (
                attr_id in required_ids
                and not _has_value(value)
            ):
                frappe.throw(
                    f"{label} is required."
                )

            if not _has_value(value):
                continue

            if field_type == "Number":
                if _to_float(value) is None:
                    frappe.throw(
                        f"{label} must be numeric."
                    )

            if attr_id in required_ids:
                provided_required_ids.add(
                    attr_id
                )

        missing = [
            (
                allowed_by_id[aid].get(
                    "label"
                )
                or aid
            )
            for aid in required_ids
            if aid not in provided_required_ids
        ]

        if missing:
            frappe.throw(
                "Missing required details: "
                f"{', '.join(missing)}"
            )

    # PRICING VALIDATION
    def _validate_pricing(self):
        if not getattr(
            self,
            "category",
            None,
        ):
            return

        chain = _get_category_chain(
            self.category
        )

        pricing = _resolve_pricing(
            chain
        )

        requirement = _norm(
            pricing.get(
                "pricing_requirement"
            )
            or "Optional"
        )

        allowed_types = list(
            pricing.get(
                "allowed_price_types"
            )
            or []
        )

        allowed_units = list(
            pricing.get(
                "allowed_price_units"
            )
            or []
        )

        leaf = chain[0]

        is_service = int(
            leaf.get("is_service")
            or 0
        )

        price_type = _norm(
            self.price_type
        )

        price_unit = _norm(
            self.price_unit
        )

        price_val = _to_float(
            self.price
        )

        if requirement.lower() == "hidden":
            if (
                price_type
                or price_unit
                or price_val
            ):
                frappe.throw(
                    "Pricing not allowed."
                )

            return

        if (
            requirement.lower() == "required"
            and not price_type
        ):
            frappe.throw(
                "Price Type required."
            )

        if (
            price_type
            and allowed_types
            and price_type not in allowed_types
        ):
            frappe.throw(
                f"Invalid Price Type '{price_type}'."
            )

        if price_type in _PRICE_TYPES_REQUIRING_AMOUNT:
            if (
                price_val is None
                or price_val <= 0
            ):
                frappe.throw(
                    "Price must be greater than 0."
                )

            if is_service:
                if not price_unit:
                    frappe.throw(
                        "Price Unit required for services."
                    )

                if (
                    allowed_units
                    and price_unit not in allowed_units
                ):
                    frappe.throw(
                        f"Invalid Price Unit '{price_unit}'."
                    )

            else:
                if price_unit:
                    frappe.throw(
                        "Price Unit only for services."
                    )

        elif price_type in _PRICE_TYPES_NO_AMOUNT:
            if price_val not in (
                None,
                0.0,
            ):
                frappe.throw(
                    "Numeric price not allowed."
                )

            if price_unit:
                frappe.throw(
                    "Price Unit not applicable."
                )

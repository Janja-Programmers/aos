from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import frappe
from frappe.model.document import Document
from frappe.utils import add_days, now_datetime, today

from aos.services.ads.constants import STATUS_REVIEWING
from aos.services.ads.errors import AdsError
from aos.services.ads.lifecycle import normalize_status, validate_status_transition
from aos.services.ads.constants import (
    MAX_DESCRIPTION_LENGTH,
    MAX_TITLE_LENGTH,
    MIN_DESCRIPTION_LENGTH,
    MIN_TITLE_LENGTH,
)
from aos.services.ads.validation import (
    normalize_full_ad_payload,
    normalize_pricing,
    normalize_text,
    persisted_offer_value,
)
from aos.services.catalog.errors import CatalogError
from aos.services.media.media_service import MediaError, MediaService
from aos.utils.aos_settings import get_aos_settings_snapshot
from aos.utils.doctype_permissions import has_doctype_permission

_SYSTEM_ACTIONS = frozenset({"moderation_allow", "moderation_reject", "moderation_review", "expire", "suspend"})


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _throw_domain(exc: Exception) -> None:
    message = str(exc or "Invalid ad.").strip() or "Invalid ad."
    frappe.throw(message, exc=frappe.ValidationError)


class AOSAd(Document):
    """Ads aggregate root.

    Public API services own request normalization and orchestration. This
    controller remains the fail-closed database boundary for ownership,
    lifecycle, market, Catalog, Media, and persisted-value integrity.
    """

    def before_insert(self):
        if not self.expires_on:
            self.expires_on = add_days(today(), get_aos_settings_snapshot().ad_expiry_days)
        if not self.status:
            self.status = STATUS_REVIEWING
        if self.meta.has_field("status_changed_on") and not self.status_changed_on:
            self.status_changed_on = now_datetime()

    def validate(self):
        try:
            self._normalize_content()
            self._validate_owner_boundary()
            self._validate_lifecycle()
            self._validate_seller_state()
            action = self._status_action()
            lifecycle_only = action in {"expire", "suspend"} and not self._content_requires_full_validation()
            if not lifecycle_only:
                self._validate_market()
                self._validate_location()
            self._validate_mutable_content()
        except (AdsError, CatalogError, MediaError) as exc:
            _throw_domain(exc)

    def before_save(self):
        self._stamp_status_metadata()

    def after_insert(self):
        # Seller.total_ads is the count of publicly Active ads, not drafts or
        # moderation-pending records.
        if self.seller and self.status == "Active":
            self._adjust_seller_active_ad_count(self.seller, 1)

    def on_update(self):
        previous = self.get_doc_before_save()
        if not previous:
            return
        old_seller = _clean(previous.seller)
        new_seller = _clean(self.seller)
        old_active = _clean(previous.status) == "Active"
        new_active = _clean(self.status) == "Active"
        if old_seller == new_seller:
            if old_active != new_active and new_seller:
                self._adjust_seller_active_ad_count(new_seller, 1 if new_active else -1)
            return
        if old_seller and old_active:
            self._adjust_seller_active_ad_count(old_seller, -1)
        if new_seller and new_active:
            self._adjust_seller_active_ad_count(new_seller, 1)

    def on_trash(self):
        if self.seller and self.status == "Active":
            self._adjust_seller_active_ad_count(self.seller, -1)

    @staticmethod
    def _adjust_seller_active_ad_count(seller: str, delta: int) -> None:
        frappe.db.sql(
            """
            UPDATE `tabAOS Seller`
            SET total_ads = GREATEST(COALESCE(total_ads, 0) + %s, 0)
            WHERE name = %s
            """,
            (int(delta), seller),
        )

    def _normalize_content(self) -> None:
        self.title = " ".join(_clean(self.title).split())
        self.description = "\n".join(
            " ".join(line.strip().split())
            for line in str(self.description or "").replace("\r\n", "\n").replace("\r", "\n").splitlines()
            if line.strip()
        )
        self.price_type = _clean(self.price_type)
        self.price_unit = _clean(self.price_unit)
        self.country = _clean(self.country)
        self.currency = _clean(self.currency)

    def _is_privileged(self) -> bool:
        user = _clean(getattr(frappe.session, "user", ""))
        return has_doctype_permission(
            user=user,
            doctype=self.doctype,
            ptype="write",
        )

    def _seller_user(self) -> str:
        return _clean(frappe.db.get_value("AOS Seller", self.seller, "user"))

    def _status_action(self) -> str:
        return _clean(self.flags.get("aos_status_action")).lower()

    def _validate_owner_boundary(self) -> None:
        user = _clean(getattr(frappe.session, "user", ""))
        if not user or user == "Guest" or self._is_privileged():
            return
        if self._seller_user() != user:
            frappe.throw("You cannot modify this ad.", exc=frappe.PermissionError)

    def _validate_lifecycle(self) -> None:
        action = self._status_action()
        if self.is_new():
            normalize_status(self.status)
            if action == "create":
                if self.status != STATUS_REVIEWING:
                    frappe.throw("New ads must enter the Reviewing state.", exc=frappe.ValidationError)
                return
            if action in {"import", "migration"}:
                if not self._is_privileged():
                    frappe.throw("Only privileged system operations may import ads.", exc=frappe.PermissionError)
                return
            frappe.throw("New ads require an explicit lifecycle action.", exc=frappe.ValidationError)

        previous = self.get_doc_before_save()
        if not previous:
            return
        old_status = _clean(previous.status)
        new_status = _clean(self.status)
        if old_status != new_status:
            validate_status_transition(old_status, new_status, action=action)
        elif old_status in {"Deleted", "Suspended"} and action not in {"migration"}:
            frappe.throw("This ad cannot be modified.", exc=frappe.ValidationError)

    def _validate_seller_state(self) -> None:
        row = frappe.db.get_value("AOS Seller", self.seller, ["name", "status"], as_dict=True)
        if not row:
            frappe.throw("Invalid seller.", exc=frappe.ValidationError)
        action = self._status_action()
        # Expiry/suspension/removal must still work after seller restriction;
        # all seller mutations and moderation activation fail closed.
        if row.status != "Active" and action not in {"expire", "suspend", "delete", "migration"}:
            frappe.throw("Seller account is not active.", exc=frappe.PermissionError)

    def _market_requires_validation(self) -> bool:
        """Validate the seller market only when the persisted market is being set or changed.

        An Ad's stored country/currency is historical business data and remains
        authoritative after creation. A later account-preference change must not
        make unrelated edits or lifecycle actions (for example mark_sold, delete,
        renew, or mark_available) impossible. Direct attempts to change seller,
        country, or currency still fail closed against the current preference.
        """
        if self.is_new():
            return True
        return any(self.has_value_changed(field) for field in ("seller", "country", "currency"))

    def _validate_market(self) -> None:
        if not self._market_requires_validation():
            return
        seller_user = self._seller_user()
        preference = frappe.db.get_value(
            "AOS User Preference",
            {"user": seller_user},
            ["country", "currency"],
            as_dict=True,
        )
        if not preference:
            frappe.throw("User preference is not configured.", exc=frappe.ValidationError)
        if _clean(preference.country) and _clean(preference.country) != self.country:
            frappe.throw("Ad country must match the seller market.", exc=frappe.ValidationError)
        if _clean(preference.currency) and _clean(preference.currency) != self.currency:
            frappe.throw("Ad currency must match the seller preference.", exc=frappe.ValidationError)

    def _validate_location(self) -> None:
        location = frappe.db.get_value(
            "AOS Location",
            self.location,
            ["country", "is_active"],
            as_dict=True,
        )
        if not location or int(location.is_active or 0) != 1:
            frappe.throw("Invalid location.", exc=frappe.ValidationError)
        if _clean(location.country) != self.country:
            frappe.throw("Location does not belong to the ad market.", exc=frappe.ValidationError)

    def _content_requires_full_validation(self) -> bool:
        if self.is_new():
            return True
        previous = self.get_doc_before_save()
        if not previous:
            return False
        if self._status_action() == "seller_resubmit":
            return True
        return any(
            self.has_value_changed(field)
            for field in (
                "category",
                "location",
                "country",
                "currency",
                "details",
                "images",
                "video_media",
            )
        )

    def _validation_payload(self) -> dict[str, Any]:
        details: list[dict[str, Any]] = []
        for row in self.details or []:
            details.append(
                {
                    "attribute": row.attribute,
                    "value_text": row.value_text,
                    "value_number": row.value_number,
                    "value_date": row.value_date,
                    "value_bool": row.value_bool,
                    "value_json": row.value_json,
                }
            )
        images = [
            {
                "media": row.media,
                "is_primary": row.is_primary,
                "sort_order": row.sort_order,
            }
            for row in (self.images or [])
        ]
        offer_price = persisted_offer_value(self.price_type, self.offer_price)
        return {
            "title": self.title,
            "description": self.description,
            "category": self.category,
            "location": self.location,
            "details": details,
            "images": images,
            "price_type": self.price_type,
            "price": self.price,
            "price_unit": self.price_unit,
            "offer_price": offer_price,
            "offer_start_date": self.offer_start_date if offer_price is not None else None,
            "offer_end_date": self.offer_end_date if offer_price is not None else None,
            "video_media": self.video_media,
        }

    def _validate_mutable_content(self) -> None:
        if self._status_action() in _SYSTEM_ACTIONS and not self._content_requires_full_validation():
            return

        full_validation = self._content_requires_full_validation()
        pricing_fields = (
            "price_type",
            "price",
            "price_unit",
            "offer_price",
            "offer_start_date",
            "offer_end_date",
        )
        pricing_changed = full_validation or any(self.has_value_changed(field) for field in pricing_fields)
        media_changed = full_validation or self.has_value_changed("images") or self.has_value_changed("video_media")

        if full_validation:
            normalized = normalize_full_ad_payload(self._validation_payload())
            self.category = normalized["category"]
            self.location = normalized["location"]
            self.set("details", [])
            for row in normalized["details"]:
                self.append("details", row)
            self.title = normalized["title"]
            self.description = normalized["description"]
        else:
            if self.has_value_changed("title"):
                self.title = normalize_text(
                    self.title,
                    field="title",
                    max_length=MAX_TITLE_LENGTH,
                    min_length=MIN_TITLE_LENGTH,
                    required=True,
                )
            if self.has_value_changed("description"):
                self.description = normalize_text(
                    self.description,
                    field="description",
                    max_length=MAX_DESCRIPTION_LENGTH,
                    min_length=MIN_DESCRIPTION_LENGTH,
                    required=True,
                    multiline=True,
                )

        if pricing_changed:
            pricing = (
                {key: normalized[key] for key in pricing_fields}
                if full_validation
                else normalize_pricing(self._validation_payload(), category=self.category)
            )
            for field in pricing_fields:
                setattr(self, field, pricing[field])

        if media_changed:
            owner = self._seller_user()
            service = MediaService()
            seen: set[str] = set()
            primary_count = 0
            for row in self.images or []:
                media_id = _clean(row.media)
                if media_id in seen:
                    frappe.throw("Duplicate ad media.", exc=frappe.ValidationError)
                seen.add(media_id)
                primary_count += int(row.is_primary or 0)
                service.validate_media_for_use(
                    media_id=media_id,
                    user=owner,
                    purpose="ad_image",
                    attached_doctype="AOS Ad" if not self.is_new() else None,
                    attached_name=self.name if not self.is_new() else None,
                )
            if len(seen) > 4 or not seen or primary_count != 1:
                frappe.throw("Ads require one to four images and exactly one primary image.", exc=frappe.ValidationError)
            if self.video_media:
                service.validate_media_for_use(
                    media_id=self.video_media,
                    user=owner,
                    purpose="ad_video",
                    attached_doctype="AOS Ad" if not self.is_new() else None,
                    attached_name=self.name if not self.is_new() else None,
                )

        if pricing_changed:
            price = Decimal(str(self.price or 0))
            offer = Decimal(str(self.offer_price or 0))
            if price > 0 and offer > 0:
                self.offer_percent = ((price - offer) / price * Decimal("100")).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
            else:
                self.offer_percent = 0

    def _stamp_status_metadata(self) -> None:
        if self.is_new():
            return
        previous = self.get_doc_before_save()
        if not previous or previous.status == self.status:
            return
        now = now_datetime()
        if self.meta.has_field("status_changed_on"):
            self.status_changed_on = now
        action = self._status_action()
        if action.startswith("moderation_"):
            self.reviewed_by = _clean(getattr(frappe.session, "user", "")) or "Administrator"
            self.reviewed_on = now
        if self.status == "Active" and self.meta.has_field("published_on") and not self.published_on:
            self.published_on = now
        if self.status == "Sold" and self.meta.has_field("sold_on"):
            self.sold_on = now
        if self.status == "Expired" and self.meta.has_field("expired_on"):
            self.expired_on = now
        if self.status == "Deleted" and self.meta.has_field("deleted_on"):
            self.deleted_on = now

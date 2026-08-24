from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.aos.doctype.aos_ad.aos_ad import AOSAd
from aos.services.ads.errors import AdsConflictError, AdsValidationError
from aos.services.ads.lifecycle import transition_for_action, validate_status_transition
from aos.services.ads.validation import (
    decode_recent_cursor,
    encode_recent_cursor,
    normalize_decimal,
    normalize_details,
    normalize_draft_payload,
    normalize_images,
    normalize_pricing,
    normalize_public_list_filters,
    normalize_text,
)


class TestAdsDomainValidation(FrappeTestCase):
    def test_text_rejects_structured_input_and_control_characters(self):
        for value in ({"title": "x"}, ["x"], "valid\x00hidden"):
            with self.subTest(value=value), self.assertRaises(AdsValidationError):
                normalize_text(value, field="title", max_length=140, required=True)

    def test_money_uses_decimal_and_fixed_precision(self):
        self.assertEqual(normalize_decimal("0.1", field="price"), Decimal("0.100000"))
        self.assertEqual(normalize_decimal(0.1, field="price"), Decimal("0.100000"))
        with self.assertRaises(AdsValidationError):
            normalize_decimal("NaN", field="price")

    def test_images_require_uniqueness_and_one_primary(self):
        rows = normalize_images(
            [
                {"media_id": "MEDIA-1", "is_primary": 1, "sort_order": 1},
                {"media_id": "MEDIA-2", "is_primary": 0, "sort_order": 2},
            ]
        )
        self.assertEqual([row["media"] for row in rows], ["MEDIA-1", "MEDIA-2"])
        with self.assertRaises(AdsValidationError):
            normalize_images(
                [
                    {"media_id": "MEDIA-1", "is_primary": 1},
                    {"media_id": "MEDIA-1", "is_primary": 0},
                ]
            )
        with self.assertRaises(AdsValidationError):
            normalize_images([{"media_id": "MEDIA-1", "is_primary": 0}])

    def test_category_attributes_are_typed_snapshotted_and_unique(self):
        schema = [
            {
                "id": "Colour",
                "key": "colour",
                "label": "Colour",
                "type": "Select",
                "required": 1,
                "options": ["Black", "White"],
                "unit": "",
            },
            {
                "id": "Year",
                "key": "year",
                "label": "Year",
                "type": "Year",
                "required": 0,
                "options": [],
                "unit": "",
            },
        ]
        with patch("aos.services.ads.validation._catalog_schema", return_value=("CAT-1", schema, {}, False)):
            rows = normalize_details(
                [
                    {"attribute": "colour", "value_text": "Black"},
                    {"attribute": "year", "value_number": 2026},
                ],
                category="CAT-1",
            )
            self.assertEqual(rows[0]["attribute_key"], "colour")
            self.assertEqual(rows[0]["attribute_label"], "Colour")
            self.assertEqual(rows[0]["attribute_type"], "Select")
            with self.assertRaises(AdsValidationError):
                normalize_details(
                    [
                        {"attribute": "colour", "value_text": "Black"},
                        {"attribute": "Colour", "value_text": "White"},
                    ],
                    category="CAT-1",
                )

    def test_pricing_rejects_float_unsafe_and_inconsistent_offers(self):
        pricing = {
            "pricing_requirement": "Required",
            "allowed_price_types": ["Fixed", "Negotiable"],
            "allowed_price_units": [],
        }
        with patch(
            "aos.services.ads.validation._catalog_schema",
            return_value=("CAT-1", [], pricing, False),
        ):
            result = normalize_pricing(
                {"price_type": "Fixed", "price": "100.125", "offer_price": "90"},
                category="CAT-1",
            )
            self.assertEqual(result["price"], "100.125000")
            with self.assertRaises(AdsValidationError):
                normalize_pricing(
                    {"price_type": "Fixed", "price": "100", "offer_price": "100"},
                    category="CAT-1",
                )

    def test_list_contract_rejects_unknown_fields_and_invalid_ranges(self):
        with self.assertRaises(AdsValidationError):
            normalize_public_list_filters({"unexpected": "x"})
        with self.assertRaises(AdsValidationError):
            normalize_public_list_filters({"price_min": "20", "price_max": "10"})
        parsed = normalize_public_list_filters({"limit": "50", "offset": "0", "sort": "recent"})
        self.assertEqual(parsed["limit"], 50)
        self.assertEqual(parsed["sort"], "recent")

    def test_recent_cursor_round_trip_and_tamper_rejection(self):
        cursor = encode_recent_cursor(creation="2026-07-24 01:02:03.123456", name="AD-2026-00001")
        self.assertEqual(
            decode_recent_cursor(cursor),
            ("2026-07-24 01:02:03.123456", "AD-2026-00001"),
        )
        with self.assertRaises(AdsValidationError):
            decode_recent_cursor("not-a-valid-cursor")

    def test_draft_payload_is_bounded_and_strict(self):
        result = normalize_draft_payload(
            {"title": "Draft", "images": [{"media_id": "MEDIA-1", "is_primary": 0}]}
        )
        self.assertEqual(result["title"], "Draft")
        self.assertEqual(result["images"][0]["media_id"], "MEDIA-1")
        with self.assertRaises(AdsValidationError):
            normalize_draft_payload({"owner": "attacker@example.com"})
        with self.assertRaises(AdsValidationError):
            normalize_draft_payload(json.dumps({"description": "x" * 70_000}))

    def test_existing_ad_keeps_persisted_market_after_preference_change(self):
        class ExistingAd:
            country = "Kenya"
            currency = "KES"

            @staticmethod
            def is_new():
                return False

            @staticmethod
            def has_value_changed(field):
                return False

            @staticmethod
            def _seller_user():
                return "seller@example.com"

            _market_requires_validation = AOSAd._market_requires_validation

        with patch("aos.aos.doctype.aos_ad.aos_ad.frappe.db.get_value") as get_value:
            AOSAd._validate_market(ExistingAd())

        get_value.assert_not_called()

    def test_direct_market_change_still_validates_current_preference(self):
        class ChangedAd:
            country = "Kenya"
            currency = "USD"

            @staticmethod
            def is_new():
                return False

            @staticmethod
            def has_value_changed(field):
                return field == "currency"

            @staticmethod
            def _seller_user():
                return "seller@example.com"

            _market_requires_validation = AOSAd._market_requires_validation

        preference = type("Preference", (), {"country": "Kenya", "currency": "KES"})()
        with (
            patch(
                "aos.aos.doctype.aos_ad.aos_ad.frappe.db.get_value",
                return_value=preference,
            ) as get_value,
            self.assertRaises(frappe.ValidationError),
        ):
            AOSAd._validate_market(ChangedAd())

        get_value.assert_called_once()

    def test_lifecycle_is_explicit_and_idempotent(self):
        transition = transition_for_action("mark_sold", "Active")
        self.assertEqual((transition.old_status, transition.new_status), ("Active", "Sold"))
        self.assertFalse(transition_for_action("mark_sold", "Sold").changed)
        with self.assertRaises(AdsConflictError):
            transition_for_action("renew", "Active")
        with self.assertRaises(AdsConflictError):
            validate_status_transition("Active", "Deleted", action="mark_sold")

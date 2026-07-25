from __future__ import annotations

from decimal import Decimal
from unittest import TestCase
from unittest.mock import patch

from aos.aos.doctype.aos_ad.aos_ad import _persisted_offer_value
from aos.patches.v1_0 import normalize_ads_offer_fields


class TestAdsOfferPersistence(TestCase):
    def test_currency_zero_sentinel_is_absent_during_document_revalidation(self):
        for value in (None, "", 0, 0.0, "0", "0.000000", Decimal("0.000000")):
            with self.subTest(value=value):
                self.assertIsNone(_persisted_offer_value("Fixed", value))

        self.assertEqual(_persisted_offer_value("Fixed", "12.500000"), "12.500000")
        self.assertIsNone(_persisted_offer_value("Negotiable", "12.500000"))
        self.assertIsNone(_persisted_offer_value("Contact for price", "12.500000"))

    def test_offer_cleanup_patch_is_install_safe_and_does_not_commit(self):
        with (
            patch.object(normalize_ads_offer_fields.frappe.db, "table_exists", return_value=True),
            patch.object(normalize_ads_offer_fields.frappe.db, "has_column", return_value=True),
            patch.object(normalize_ads_offer_fields.frappe.db, "sql") as sql,
            patch.object(normalize_ads_offer_fields.frappe.db, "commit", create=True) as commit,
            patch.object(normalize_ads_offer_fields.frappe, "logger") as logger,
        ):
            normalize_ads_offer_fields.execute()

        sql.assert_called_once()
        statement = " ".join(str(sql.call_args.args[0]).split()).upper()
        self.assertIn("UPDATE `TABAOS AD`", statement)
        self.assertIn("COALESCE(PRICE_TYPE, '') != 'FIXED'", statement)
        self.assertIn("COALESCE(OFFER_PRICE, 0) <= 0", statement)
        commit.assert_not_called()
        logger.assert_called_once_with("aos.ads", allow_site=True)

    def test_offer_cleanup_patch_is_noop_without_ad_table(self):
        with (
            patch.object(normalize_ads_offer_fields.frappe.db, "table_exists", return_value=False),
            patch.object(normalize_ads_offer_fields.frappe.db, "sql") as sql,
        ):
            normalize_ads_offer_fields.execute()

        sql.assert_not_called()

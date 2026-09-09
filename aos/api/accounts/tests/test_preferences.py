from __future__ import annotations

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from aos.api.accounts.get_my_preference import get_my_preference_impl
from aos.api.accounts.update_my_preference import update_my_preference_impl
from aos.services.localization.preferences import (
    get_user_preference,
    get_user_preference_for_update,
    update_user_preference,
)
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class AccountPreferenceIntegrationTests(AOSFeatureTestMixin, IntegrationTestCase):
    def setUp(self):
        self.prefix = self.make_prefix("account-preference")
        self.created_users: list[str] = []
        frappe.set_user("Administrator")
        self.configure_test_localization_defaults()

    def tearDown(self):
        self.cleanup_feature_rows()
        frappe.set_user("Administrator")

    def _other_country(self, current: str) -> str:
        names = frappe.get_all("Country", pluck="name", limit=0)
        other = next((str(name) for name in names if str(name) != str(current)), None)
        if not other:
            self.skipTest("A second Country fixture is required")
        return other

    @patch("aos.services.localization.preferences.get_user_preference_for_update")
    def test_partial_update_preserves_unrelated_fields(self, get_locked):
        get_locked.return_value = frappe._dict(
            name="PREF-1",
            user="user@example.com",
            country="Kenya",
            currency="KES",
            language="en",
            location="",
        )
        doc = MagicMock(country="Kenya", currency="KES", language="en", location="")
        with (
            patch("aos.services.localization.preferences.validate_language", return_value=("sw", None)),
            patch("aos.services.localization.preferences.frappe.get_doc", return_value=doc),
        ):
            updated, error = update_user_preference("user@example.com", language="sw")
        self.assertIsNone(error)
        self.assertEqual(updated.country, "Kenya")
        self.assertEqual(updated.currency, "KES")
        self.assertEqual(updated.language, "sw")
        doc.save.assert_called_once_with(ignore_permissions=True)

    def test_preference_read_uses_current_schema_without_metadata_probe(self):
        user = self.make_user("schema-hot-path")
        expected = frappe._dict(
            name=user,
            user=user,
            country="Kenya",
            currency="KES",
            language="en-US",
            location="",
        )
        cache = MagicMock()
        with (
            patch("aos.services.localization.preferences.frappe.db.get_value", return_value=expected),
            patch("aos.services.localization.preferences.frappe.cache", return_value=cache),
            patch("aos.services.localization.preferences.frappe.get_meta", create=True) as get_meta,
        ):
            pref = get_user_preference(user, use_cache=False)
        self.assertIsNotNone(pref)
        self.assertEqual(pref.user, user)
        get_meta.assert_not_called()

    def test_get_preference_rejects_unknown_fields(self):
        user = self.make_user("strict-read")
        frappe.set_user(user)
        with patch("aos.api.accounts.get_my_preference.rate_limit", return_value=None):
            response = get_my_preference_impl(legacy=True)
        self.assertEqual(response["error"], "PREFERENCE_UNKNOWN_FIELD")

    def test_update_preference_rejects_unknown_fields(self):
        user = self.make_user("strict-update")
        frappe.set_user(user)
        with patch("aos.api.accounts.update_my_preference.rate_limit", return_value=None):
            response = update_my_preference_impl(country=self.preference_defaults()[0], seller=True)
        self.assertEqual(response["error"], "PREFERENCE_UNKNOWN_FIELD")
        self.assertEqual(response["data"]["fields"], ["seller"])

    @patch("aos.services.localization.preferences.get_user_preference_for_update")
    def test_explicit_null_location_clears_location(self, get_locked):
        get_locked.return_value = frappe._dict(
            name="PREF-1",
            user="user@example.com",
            country="Kenya",
            currency="KES",
            language="en",
            location="LOC-1",
        )
        doc = MagicMock(country="Kenya", currency="KES", language="en", location="LOC-1")
        with patch("aos.services.localization.preferences.frappe.get_doc", return_value=doc):
            updated, error = update_user_preference("user@example.com", location=None)
        self.assertIsNone(error)
        self.assertEqual(updated.location, "")
        doc.save.assert_called_once_with(ignore_permissions=True)

    def test_location_can_be_updated_within_selected_country(self):
        user = self.make_user("location-update")
        country = self.preference_defaults()[0]
        location = self.make_location(country=country)
        frappe.set_user(user)
        with patch("aos.api.accounts.update_my_preference.rate_limit", return_value=None):
            response = update_my_preference_impl(location=location)
        self.assertTrue(response.get("ok"), response)
        self.assertEqual(response["data"]["location"], location)

    def test_preference_update_lock_uses_select_for_update(self):
        with patch("aos.services.localization.preferences.frappe.db.sql", return_value=[]) as sql:
            get_user_preference_for_update("user@example.com")
        query = sql.call_args.args[0]
        self.assertIn("FOR UPDATE", query.upper())
        self.assertIn("WHERE user = %s", query)

    def test_get_missing_preference_does_not_create_state(self):
        user = self.make_user("missing-read", with_preference=False)
        frappe.set_user(user)
        with patch("aos.api.accounts.get_my_preference.rate_limit", return_value=None):
            response = get_my_preference_impl()
        self.assertEqual(response["error"], "PREFERENCE_MISSING")
        self.assertFalse(frappe.db.exists("AOS User Preference", {"user": user}))

    def test_update_missing_preference_does_not_bootstrap_state(self):
        user = self.make_user("missing-update", with_preference=False)
        frappe.set_user(user)
        with patch("aos.api.accounts.update_my_preference.rate_limit", return_value=None):
            response = update_my_preference_impl(language=self.preference_defaults()[1])
        self.assertEqual(response["error"], "PREFERENCE_MISSING")
        self.assertFalse(frappe.db.exists("AOS User Preference", {"user": user}))

    def test_country_is_mutable_for_seller_and_existing_ad_is_unchanged(self):
        user = self.make_user("seller-market")
        current_country, _language, _currency = self.preference_defaults()
        location = self.make_location(country=current_country)
        pref = frappe.get_doc("AOS User Preference", user)
        pref.location = location
        pref.save(ignore_permissions=True)
        self.make_seller(user)
        ad = self.make_ad(seller_user=user)
        before = frappe.db.get_value("AOS Ad", ad.name, ["country", "location"], as_dict=True)
        other_country = self._other_country(current_country)

        frappe.set_user(user)
        with patch("aos.api.accounts.update_my_preference.rate_limit", return_value=None):
            response = update_my_preference_impl(country=other_country)

        self.assertTrue(response.get("ok"), response)
        self.assertEqual(response["data"]["country"], other_country)
        self.assertIsNone(response["data"]["location"])
        self.assertEqual(set(response["data"]), {"country", "currency", "language", "location"})
        after = frappe.db.get_value("AOS Ad", ad.name, ["country", "location"], as_dict=True)
        self.assertEqual(dict(after), dict(before))

    def test_existing_ad_remains_valid_after_browsing_country_changes(self):
        user = self.make_user("ad-save")
        current_country = self.preference_defaults()[0]
        ad = self.make_ad(seller_user=user)
        other_country = self._other_country(current_country)
        updated, error = update_user_preference(user, country=other_country)
        self.assertIsNone(error)
        self.assertEqual(updated.country, other_country)

        frappe.set_user("Administrator")
        ad.reload()
        ad.flags.aos_status_action = "migration"
        ad.save(ignore_permissions=True)
        self.assertEqual(ad.country, current_country)

    def test_repeated_identical_update_is_retry_safe_noop(self):
        user = self.make_user("idempotent")
        pref = get_user_preference(user, use_cache=False)
        with patch("aos.services.localization.preferences.frappe.get_doc") as get_doc:
            updated, error = update_user_preference(user, country=pref.country)
        self.assertIsNone(error)
        self.assertEqual(updated.country, pref.country)
        get_doc.assert_not_called()

    def test_preference_update_observability_records_localization_field_names(self):
        user = self.make_user("observability")
        frappe.set_user(user)
        currency = self.preference_defaults()[2]
        with (
            patch("aos.api.accounts.update_my_preference.rate_limit", return_value=None),
            patch("aos.api.accounts.update_my_preference.account_log") as account_log,
        ):
            response = update_my_preference_impl(currency=currency)
        self.assertTrue(response.get("ok"), response)
        self.assertEqual(account_log.call_args.kwargs["changed_fields"], ["currency"])

    def test_unexpected_update_failure_rolls_back_partial_write(self):
        user = self.make_user("rollback")
        preference_name = frappe.db.get_value("AOS User Preference", {"user": user}, "name")
        original_currency = frappe.db.get_value("AOS User Preference", preference_name, "currency")
        frappe.set_user(user)

        def mutate_then_fail(_user: str, **_kwargs):
            frappe.db.set_value(
                "AOS User Preference",
                preference_name,
                "currency",
                "ROLLBACK_TEST",
                update_modified=False,
            )
            raise RuntimeError("simulated mid-operation failure")

        with (
            patch("aos.api.accounts.update_my_preference.rate_limit", return_value=None),
            patch("aos.api.accounts.update_my_preference.update_user_preference", side_effect=mutate_then_fail),
            patch("aos.api.accounts.update_my_preference.frappe.log_error"),
        ):
            response = update_my_preference_impl(currency=original_currency)

        self.assertEqual(response["error"], "INTERNAL_ERROR")
        self.assertEqual(frappe.db.get_value("AOS User Preference", preference_name, "currency"), original_currency)

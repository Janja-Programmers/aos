"""Frappe runtime checks for the canonical finalized-feature transport boundary."""

from __future__ import annotations

from unittest.mock import patch

from frappe.tests import IntegrationTestCase

from aos.api.v1 import accounts, auth, catalog, localization, media


class TestFinalizedAPITransportRuntime(IntegrationTestCase):
    def test_each_finalized_feature_strips_cmd_and_preserves_real_client_fields(self):
        cases = (
            (auth, "login", "_login_impl"),
            (accounts, "get_profile", "_get_profile_impl"),
            (localization, "get_locations", "_get_locations_impl"),
            (media, "get_media_url", "_get_media_url_impl"),
            (catalog, "get_category_schema", "_get_category_schema_impl"),
        )

        for module, endpoint_name, handler_name in cases:
            with self.subTest(module=module.__name__, endpoint=endpoint_name):
                endpoint = getattr(module, endpoint_name)
                with patch.object(module, handler_name, return_value={"ok": True}) as handler:
                    response = endpoint(
                        cmd=f"{module.__name__}.{endpoint_name}",
                        canonical_field="value",
                        legacy="must-remain-visible",
                    )
                self.assertEqual(response, {"ok": True})
                handler.assert_called_once_with(
                    canonical_field="value",
                    legacy="must-remain-visible",
                )

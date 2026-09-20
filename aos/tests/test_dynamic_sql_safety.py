from __future__ import annotations
from unittest.mock import patch
import frappe
from frappe.tests.utils import FrappeTestCase
import importlib
ads_list = importlib.import_module('aos.api.ads.list_ads')
from aos.api.shared.sql_safety import clean_safe_docnames, require_dotted_sql_identifier, safe_like_contains, safe_like_prefix
list_sellers = importlib.import_module('aos.api.sellers.list_sellers')
seller_service = importlib.import_module('aos.services.sellers.service')
wishlist_list = importlib.import_module('aos.api.wishlist.list')

class TestDynamicSqlSafety(FrappeTestCase):
    """Focused checks for SQL fragments that cannot be parameter-bound."""

    def setUp(self):
        frappe.local.response = {}

    def test_like_helpers_escape_wildcards(self):
        self.assertEqual(safe_like_contains('50%_off\\sale'), '%50\\%\\_off\\\\sale%')
        self.assertEqual(safe_like_prefix('50%_off\\sale'), '50\\%\\_off\\\\sale%')

    def test_ads_invalid_sort_is_rejected_before_sql(self):
        with patch.object(ads_list, 'rate_limit', return_value=None), patch.object(ads_list, 'request_ip', return_value='127.0.0.1'), patch.object(ads_list, 'resolve_market_context', return_value=('Kenya', 'KES', None)), patch.object(ads_list, 'current_user', return_value='Guest'), patch.object(ads_list.frappe.db, 'sql', side_effect=AssertionError('SQL should not run')):
            response = ads_list.list_ads_impl(sort='recent; DROP TABLE `tabAOS Ad`; --')
        self.assertFalse(response.get('ok'))
        self.assertEqual(response.get('error'), 'VALIDATION_ERROR')
        self.assertEqual(frappe.local.response.get('http_status_code'), 422)

    def test_ads_invalid_cursor_is_rejected_before_sql(self):
        with patch.object(ads_list, 'rate_limit', return_value=None), patch.object(ads_list, 'request_ip', return_value='127.0.0.1'), patch.object(ads_list, 'resolve_market_context', return_value=('Kenya', 'KES', None)), patch.object(ads_list, 'current_user', return_value='Guest'), patch.object(ads_list.frappe.db, 'sql', side_effect=AssertionError('SQL should not run')):
            response = ads_list.list_ads_impl(cursor='not-a-valid-cursor')
        self.assertFalse(response.get('ok'))
        self.assertEqual(response.get('error'), 'INVALID_AD_CURSOR')
        self.assertEqual(frappe.local.response.get('http_status_code'), 422)

    def test_wishlist_legacy_sort_parameter_is_rejected_before_sql(self):
        with patch.object(wishlist_list, 'rate_limit', return_value=None), patch.object(wishlist_list, 'request_ip', return_value='127.0.0.1'), patch.object(wishlist_list, 'require_login', return_value=('sql-test@example.com', None)), patch.object(wishlist_list, 'resolve_market_context', return_value=('Kenya', 'KES', None)), patch.object(wishlist_list.frappe.db, 'sql', side_effect=AssertionError('SQL should not run')):
            response = wishlist_list.list_wishlist_impl(sort='recent; DROP TABLE `tabAOS Wishlist`; --')
        self.assertFalse(response.get('ok'))
        self.assertEqual(response.get('error'), 'INVALID_WISHLIST_REQUEST')
        self.assertEqual(frappe.local.response.get('http_status_code'), 422)

    def test_wishlist_invalid_cursor_is_rejected_before_listing_sql(self):
        with patch.object(wishlist_list, 'rate_limit', return_value=None), patch.object(wishlist_list, 'request_ip', return_value='127.0.0.1'), patch.object(wishlist_list, 'require_login', return_value=('sql-test@example.com', None)), patch.object(wishlist_list, 'resolve_market_context', return_value=('Kenya', 'KES', None)), patch.object(wishlist_list.frappe.db, 'sql', side_effect=AssertionError('SQL should not run')):
            response = wishlist_list.list_wishlist_impl(cursor='not-a-valid-cursor')
        self.assertFalse(response.get('ok'))
        self.assertEqual(response.get('error'), 'INVALID_WISHLIST_CURSOR')
        self.assertEqual(frappe.local.response.get('http_status_code'), 422)

    def test_seller_search_escapes_like_wildcards(self):
        captured = {}

        def fake_sql(query, params=None, **kwargs):
            captured['query'] = query
            captured['params'] = tuple(params or ())
            return []
        with patch.object(list_sellers, 'rate_limit', return_value=None), patch.object(list_sellers, 'request_ip', return_value='127.0.0.1'), patch.object(list_sellers, 'current_user', return_value='Guest'), patch.object(seller_service.frappe.db, 'sql', side_effect=fake_sql):
            response = list_sellers.list_sellers_impl(search='50%_off')
        self.assertTrue(response.get('ok'), response)
        self.assertIn("ESCAPE '\\\\'", captured['query'])
        self.assertNotIn("ESCAPE '\\'", captured['query'])
        self.assertIn('%50\\%\\_off%', captured['params'])

    def test_seller_invalid_sort_is_rejected_before_sql(self):
        with patch.object(list_sellers, 'rate_limit', return_value=None), patch.object(list_sellers, 'request_ip', return_value='127.0.0.1'), patch.object(list_sellers, 'current_user', return_value='Guest'), patch.object(seller_service.frappe.db, 'sql', side_effect=AssertionError('SQL should not run')):
            response = list_sellers.list_sellers_impl(sort='rating; DROP TABLE `tabAOS Seller`; --')
        self.assertFalse(response.get('ok'))
        self.assertEqual(response.get('error'), 'INVALID_SELLER_SORT')
        self.assertEqual(frappe.local.response.get('http_status_code'), 422)

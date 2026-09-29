from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.health import liveness, readiness


class TestInfrastructureHealthApi(FrappeTestCase):
	def setUp(self):
		frappe.local.response = {}

	def test_liveness_exposes_only_minimal_state(self):
		response = liveness()
		self.assertEqual(response, {"ok": True, "status": "healthy"})
		self.assertEqual(frappe.local.response.get("http_status_code"), 200)

	def test_readiness_returns_503_without_exposing_check_details(self):
		with patch("aos.api.health.validate_readiness", return_value={"ready": False, "status": "unhealthy", "checks": [{"message": "secret"}] }):
			response = readiness()
		self.assertEqual(response, {"ok": False, "status": "unhealthy"})
		self.assertEqual(frappe.local.response.get("http_status_code"), 503)
		self.assertNotIn("checks", response)
		self.assertNotIn("secret", str(response))

	def test_readiness_failure_is_sanitized(self):
		with patch("aos.api.health.validate_readiness", side_effect=RuntimeError("password=should-not-leak")):
			response = readiness()
		self.assertEqual(response, {"ok": False, "status": "unhealthy"})
		self.assertNotIn("should-not-leak", str(response))

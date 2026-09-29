from __future__ import annotations

from frappe.tests.utils import FrappeTestCase

from aos.utils.health_model import (
	AVAILABLE,
	DISABLED,
	DISABLED_CONDITION,
	HEALTHY,
	OPTIONAL,
	REQUIRED,
	UNAVAILABLE,
	UNHEALTHY,
	UNKNOWN,
	UNKNOWN_CONDITION,
	health_check,
	summarize_health,
)


class TestHealthModel(FrappeTestCase):
	def test_optional_failure_degrades_without_blocking_readiness(self):
		report = summarize_health([
			health_check(name="db", category="database", status=HEALTHY, requirement=REQUIRED, condition=AVAILABLE, message="ok"),
			health_check(name="maps", category="maps", status=UNHEALTHY, requirement=OPTIONAL, condition=UNAVAILABLE, message="down"),
		])
		self.assertTrue(report["ready"])
		self.assertEqual(report["status"], "degraded")
		self.assertEqual(report["summary"]["optional"]["impaired"], 1)

	def test_required_failure_blocks_readiness(self):
		report = summarize_health([
			health_check(name="db", category="database", status=UNHEALTHY, requirement=REQUIRED, condition=UNAVAILABLE, message="down")
		])
		self.assertFalse(report["ready"])
		self.assertEqual(report["status"], "unhealthy")

	def test_disabled_optional_service_is_neutral(self):
		report = summarize_health([
			health_check(name="moderation", category="service", status=DISABLED, requirement=OPTIONAL, condition=DISABLED_CONDITION, message="disabled")
		])
		self.assertTrue(report["ready"])
		self.assertEqual(report["status"], "healthy")
		self.assertEqual(report["summary"]["optional"]["disabled"], 1)


class TestHealthUnknownSemantics(FrappeTestCase):
	def test_required_unknown_blocks_readiness(self):
		report = summarize_health([
			health_check(
				name="database",
				category="database",
				status=UNKNOWN,
				requirement=REQUIRED,
				condition=UNKNOWN_CONDITION,
				message="inspection unavailable",
			)
		])
		self.assertFalse(report["ready"])
		self.assertEqual(report["status"], "unhealthy")

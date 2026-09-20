from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from unittest.mock import Mock, patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from aos.api.analytics_pipeline import callback as analytics_api
from aos.api.moderation import callback as moderation_api
from aos.api.notifications import callback as notification_api
from aos.api.search_ranking import callback as search_api
from aos.services import video_processing_callback as video_api
from aos.services.transactional_outbox import (
	OUTBOX_DOCTYPE,
	_claim_one,
	authorize_terminal_work_replay,
	mark_outbox_callback,
)
from aos.tests.outbox_fixtures import cleanup_committed_records, create_durable_job, create_outbox


@dataclass(frozen=True)
class CallbackAdapter:
	service_type: str
	api_module: Any
	handler_name: str
	success_status: str
	failed_status: str
	endpoint_impl_name: str = "handle_callback_impl"


_ADAPTERS = (
	CallbackAdapter(
		"video_processing",
		video_api,
		"handle_video_processing_callback",
		"Ready",
		"Failed",
	),
	CallbackAdapter(
		"moderation",
		moderation_api,
		"handle_moderation_callback",
		"Review Required",
		"Failed",
	),
	CallbackAdapter(
		"search_indexing",
		search_api,
		"handle_search_index_callback",
		"Indexed",
		"Failed",
	),
	CallbackAdapter(
		"notification_delivery",
		notification_api,
		"handle_notification_delivery_callback",
		"Delivered",
		"Failed",
		"handle_delivery_callback_impl",
	),
	CallbackAdapter(
		"analytics_ingestion",
		analytics_api,
		"handle_analytics_ingest_callback",
		"Ingested",
		"Failed",
	),
)

_TEST_CALLBACK_SECRET = "callback-test-secret"


def _test_dispatch_secret(service: str) -> str:
	"""Return a deterministic, service-specific synthetic signing value."""
	return f"{service}-dispatch-{_TEST_CALLBACK_SECRET}"


_ENV = {
	"MODERATION_ENABLED": "true",
	"MODERATION_FAIL_OPEN": "false",
	"SEARCH_RANKING_ENABLED": "true",
	"NOTIFICATION_DELIVERY_ENABLED": "true",
	"ANALYTICS_PIPELINE_ENABLED": "true",
	"VIDEO_SERVICE_URL": "http://127.0.0.1:18130",
	"VIDEO_SERVICE_SECRET": _test_dispatch_secret("video"),
	"VIDEO_SERVICE_CALLBACK_SECRET": _TEST_CALLBACK_SECRET,
	"MINIO_ENDPOINT": "127.0.0.1:19000",
	"MINIO_ACCESS_KEY": "test-access-key",
	"MINIO_SECRET_KEY": "test-secret-key",
	"MINIO_PUBLIC_BASE_URL": "http://127.0.0.1:19100",
	"MODERATION_SERVICE_URL": "http://127.0.0.1:18140",
	"MODERATION_SERVICE_SECRET": _test_dispatch_secret("moderation"),
	"MODERATION_SERVICE_CALLBACK_SECRET": _TEST_CALLBACK_SECRET,
	"SEARCH_RANKING_SERVICE_URL": "http://127.0.0.1:18150",
	"SEARCH_RANKING_SERVICE_SECRET": _test_dispatch_secret("search_ranking"),
	"SEARCH_RANKING_SERVICE_CALLBACK_SECRET": _TEST_CALLBACK_SECRET,
	"NOTIFICATION_SERVICE_URL": "http://127.0.0.1:18160",
	"NOTIFICATION_SERVICE_SECRET": _test_dispatch_secret("notification"),
	"NOTIFICATION_SERVICE_CALLBACK_SECRET": _TEST_CALLBACK_SECRET,
	"ANALYTICS_SERVICE_URL": "http://127.0.0.1:18170",
	"ANALYTICS_SERVICE_SECRET": _test_dispatch_secret("analytics"),
	"ANALYTICS_SERVICE_CALLBACK_SECRET": _TEST_CALLBACK_SECRET,
}


class TestCallbackAtomicityAllServices(FrappeTestCase):
	"""Database-backed endpoint tests for callback correlation and rollback."""

	def _setup(self, adapter: CallbackAdapter):
		fixture = create_durable_job(adapter.service_type, status="Processing")
		fixture.job.service_job_id = fixture.job.idempotency_key
		fixture.job.attempt_count = 1
		fixture.job.save(ignore_permissions=True)
		with patch("aos.services.transactional_outbox.register_after_commit_publish"):
			outbox = create_outbox(fixture, max_attempts=3)
		token = uuid.uuid4().hex
		outbox.status = "Published"
		outbox.attempt_count = 1
		outbox.dispatch_generation = 1
		outbox.current_dispatch_token = token
		outbox.published_at = now_datetime()
		outbox.callback_deadline_at = add_to_date(now_datetime(), seconds=300, as_datetime=True)
		outbox.save(ignore_permissions=True)
		cleanup = getattr(self, "_scenario_cleanup_records", None)
		if cleanup is not None:
			cleanup.extend(((OUTBOX_DOCTYPE, outbox.name), *fixture.cleanup_records))
		# Callback atomicity is tested against durable pre-existing state. A
		# committed baseline also prevents an inner rollback from erasing the
		# fixture when another full-suite test has changed transaction state.
		frappe.db.commit()
		return fixture, outbox, token

	def _base_payload(self, fixture, outbox, token: str) -> dict[str, Any]:
		return {
			"job_id": fixture.job.name,
			"idempotency_key": fixture.job.idempotency_key,
			"dispatch_id": outbox.idempotency_key,
			"dispatch_generation": int(outbox.dispatch_generation),
			"dispatch_token": token,
		}

	def _success_payload(self, adapter: CallbackAdapter, fixture, outbox, token: str) -> dict[str, Any]:
		base = self._base_payload(fixture, outbox, token)
		if adapter.service_type == "video_processing":
			return {
				**base,
				"status": "ready",
				"job_generation": int(getattr(fixture.job, "generation", 1) or 1),
				"duration_seconds": 4.0,
				"outputs": {
					"playback": {"bucket": "aos-public", "object_key": "shorts/playback/test/final.mp4", "content_type": "video/mp4"},
					"manifest": {"bucket": "aos-public", "object_key": "shorts/playback/test/master.m3u8", "content_type": "application/vnd.apple.mpegurl"},
					"poster": {"bucket": "aos-public", "object_key": "shorts/posters/test/poster.jpg", "content_type": "image/jpeg"},
				},
			}
		if adapter.service_type == "moderation":
			return {
				**base,
				"status": "completed",
				"decision": "review",
				"reasons": ["manual review"],
				"labels": [],
				"scores": {},
			}
		if adapter.service_type == "search_indexing":
			return {**base, "status": "completed", "score": 1.0}
		if adapter.service_type == "notification_delivery":
			return {
				**base,
				"status": "delivered",
				"success_count": 1,
				"failure_count": 0,
				"token_count": 1,
				"inactive_token_hashes": [],
			}
		return {
			**base,
			"status": "ingested",
			"ingested_count": 0,
			"skipped_count": 0,
			"counters": {},
		}

	def _failure_payload(self, fixture, outbox, token: str) -> dict[str, Any]:
		return {
			**self._base_payload(fixture, outbox, token),
			"status": "failed",
			"error": "SANITIZED_DOWNSTREAM_FAILURE",
		}

	def _invoke(self, adapter: CallbackAdapter, payload: dict[str, Any]) -> dict[str, Any]:
		with patch.object(adapter.api_module, "read_signed_json_callback_payload", return_value=payload):
			endpoint_impl = getattr(adapter.api_module, adapter.endpoint_impl_name)
			if adapter.service_type != "video_processing" or str(payload.get("status") or "").lower() == "failed":
				return endpoint_impl()
			with (
				patch("aos.services.video_processing_service._register_asset", side_effect=["MEDIA-PLAY", "MEDIA-MANIFEST", "MEDIA-POSTER", "MEDIA-STORY", "MEDIA-STORY-MANIFEST"]),
				patch("aos.services.video_processing_service._ensure_original_sound"),
				patch("aos.services.video_processing_service.reclassify_short"),
				patch("aos.services.moderation_service.enqueue_short_moderation"),
			):
				return endpoint_impl()

	def _snapshot(self, fixture, outbox) -> dict[str, Any]:
		job = frappe.db.get_value(
			fixture.doctype,
			fixture.job.name,
			["status", "last_error", "completed_at", "callback_received_at", "response_payload"],
			as_dict=True,
		)
		outbox_state = frappe.db.get_value(
			OUTBOX_DOCTYPE,
			outbox.name,
			[
				"status",
				"callback_status",
				"callback_received_at",
				"completed_at",
				"completed_dispatch_generation",
				"last_callback_dispatch_token",
				"last_error",
			],
			as_dict=True,
		)
		domain = None
		if fixture.domain_doctype and fixture.domain_doctype != "User":
			fields = ["status"]
			meta = frappe.get_meta(fixture.domain_doctype)
			for field in ("lifecycle_status", "processing_status", "moderation_status", "processing_error", "moderation_reason"):
				if meta.has_field(field):
					fields.append(field)
			domain = frappe.db.get_value(
				fixture.domain_doctype,
				fixture.domain_name,
				fields,
				as_dict=True,
			)
		return {"job": dict(job or {}), "outbox": dict(outbox_state or {}), "domain": dict(domain or {})}

	def _run_isolated(self, operation: Callable[[], None]) -> None:
		self._scenario_cleanup_records: list[tuple[str, str]] = []
		previous_user = frappe.session.user or "Guest"
		try:
			operation()
		finally:
			try:
				frappe.set_user("Administrator")
				records = list(self._scenario_cleanup_records)
				self._scenario_cleanup_records = []
				cleanup_committed_records(records)
			finally:
				frappe.set_user(previous_user)

	def test_valid_success_and_duplicate_success_are_atomic_and_idempotent(self):
		with patch.dict(os.environ, _ENV, clear=False):
			for adapter in _ADAPTERS:
				with self.subTest(service_type=adapter.service_type):

					def scenario() -> None:
						fixture, outbox, token = self._setup(adapter)
						payload = self._success_payload(adapter, fixture, outbox, token)
						first = self._invoke(adapter, payload)
						self.assertTrue(first["ok"], first)
						fixture.job.reload()
						outbox.reload()
						self.assertEqual(fixture.job.status, adapter.success_status)
						self.assertEqual(outbox.status, "Completed")
						first_snapshot = self._snapshot(fixture, outbox)

						duplicate = self._invoke(adapter, payload)
						self.assertTrue(duplicate["ok"], duplicate)
						self.assertEqual(self._snapshot(fixture, outbox), first_snapshot)

					self._run_isolated(scenario)

	def test_valid_failure_and_duplicate_failure_are_atomic_and_idempotent(self):
		with patch.dict(os.environ, _ENV, clear=False):
			for adapter in _ADAPTERS:
				with self.subTest(service_type=adapter.service_type):

					def scenario() -> None:
						fixture, outbox, token = self._setup(adapter)
						payload = self._failure_payload(fixture, outbox, token)
						first = self._invoke(adapter, payload)
						self.assertTrue(first["ok"], first)
						fixture.job.reload()
						outbox.reload()
						self.assertEqual(fixture.job.status, adapter.failed_status)
						self.assertEqual(outbox.status, "Completed With Failure")
						first_snapshot = self._snapshot(fixture, outbox)

						duplicate = self._invoke(adapter, payload)
						self.assertTrue(duplicate["ok"], duplicate)
						self.assertEqual(self._snapshot(fixture, outbox), first_snapshot)

					self._run_isolated(scenario)

	def test_conflicting_late_wrong_token_old_generation_and_dead_letter_change_nothing(self):
		with patch.dict(os.environ, _ENV, clear=False):
			for adapter in _ADAPTERS:
				with self.subTest(service_type=adapter.service_type):

					def scenario() -> None:
						fixture, outbox, token = self._setup(adapter)
						success = self._success_payload(adapter, fixture, outbox, token)
						self.assertTrue(self._invoke(adapter, success)["ok"])
						terminal = self._snapshot(fixture, outbox)

						late_failure = self._failure_payload(fixture, outbox, token)
						self.assertFalse(self._invoke(adapter, late_failure)["ok"])
						self.assertEqual(self._snapshot(fixture, outbox), terminal)

						failed_fixture, failed_outbox, failed_token = self._setup(adapter)
						failure = self._failure_payload(failed_fixture, failed_outbox, failed_token)
						self.assertTrue(self._invoke(adapter, failure)["ok"])
						failed_terminal = self._snapshot(failed_fixture, failed_outbox)
						late_success = self._success_payload(
							adapter, failed_fixture, failed_outbox, failed_token
						)
						self.assertFalse(self._invoke(adapter, late_success)["ok"])
						self.assertEqual(self._snapshot(failed_fixture, failed_outbox), failed_terminal)

						# A fresh active generation rejects old generations and wrong tokens before mutation.
						fixture2, outbox2, token2 = self._setup(adapter)
						active = self._snapshot(fixture2, outbox2)
						old = self._success_payload(adapter, fixture2, outbox2, token2)
						old["dispatch_generation"] = 0
						old_result = self._invoke(adapter, old)
						self.assertFalse(old_result["ok"])
						self.assertEqual(old_result["error"], "OLD_GENERATION_CALLBACK")
						self.assertEqual(self._snapshot(fixture2, outbox2), active)
						self.assertEqual(
							int(
								frappe.db.get_value(
									OUTBOX_DOCTYPE, outbox2.name, "old_generation_rejection_count"
								)
								or 0
							),
							1,
						)

						wrong = self._success_payload(adapter, fixture2, outbox2, token2)
						wrong["dispatch_token"] = uuid.uuid4().hex
						wrong_result = self._invoke(adapter, wrong)
						self.assertFalse(wrong_result["ok"])
						self.assertEqual(wrong_result["error"], "CALLBACK_TOKEN_MISMATCH")
						self.assertEqual(self._snapshot(fixture2, outbox2), active)
						self.assertEqual(
							int(
								frappe.db.get_value(
									OUTBOX_DOCTYPE, outbox2.name, "token_mismatch_count"
								)
								or 0
							),
							1,
						)

						outbox2.status = "Dead Letter"
						outbox2.completed_at = now_datetime()
						outbox2.save(ignore_permissions=True)
						dead_letter = self._snapshot(fixture2, outbox2)
						self.assertFalse(
							self._invoke(adapter, self._success_payload(adapter, fixture2, outbox2, token2))[
								"ok"
							]
						)
						self.assertEqual(self._snapshot(fixture2, outbox2), dead_letter)

					self._run_isolated(scenario)

	def test_service_job_save_and_outbox_save_failures_roll_back_every_service(self):
		with patch.dict(os.environ, _ENV, clear=False):
			for adapter in _ADAPTERS:
				with self.subTest(service_type=adapter.service_type):

					def service_job_scenario() -> None:
						fixture, outbox, token = self._setup(adapter)
						before = self._snapshot(fixture, outbox)
						payload = self._success_payload(adapter, fixture, outbox, token)
						job_class = fixture.job.__class__
						original_save = job_class.save

						def failing_job_save(doc, *args, **kwargs):
							if doc.name == fixture.job.name and doc.status != "Processing":
								raise RuntimeError("secret SQL /srv/private token=https://internal.invalid")
							return original_save(doc, *args, **kwargs)

						with (
							patch.object(job_class, "save", new=failing_job_save),
							patch.object(adapter.api_module.frappe, "log_error"),
						):
							response = self._invoke(adapter, payload)
						self.assertFalse(response["ok"], response)
						self.assertEqual(self._snapshot(fixture, outbox), before)
						self.assertGreaterEqual(
							int(
								frappe.db.get_value(
									OUTBOX_DOCTYPE, outbox.name, "transaction_rollback_count"
								)
								or 0
							),
							1,
						)
						serialized = str(response)
						for forbidden in ("secret", "/srv/private", "internal.invalid", "SQL", "traceback"):
							self.assertNotIn(forbidden, serialized)

					self._run_isolated(service_job_scenario)

					def outbox_scenario() -> None:
						fixture, outbox, token = self._setup(adapter)
						before = self._snapshot(fixture, outbox)
						payload = self._success_payload(adapter, fixture, outbox, token)
						outbox_class = outbox.__class__
						original_save = outbox_class.save

						def failing_outbox_save(doc, *args, **kwargs):
							if doc.name == outbox.name:
								raise RuntimeError("private token file:///srv/secret database SQL")
							return original_save(doc, *args, **kwargs)

						with (
							patch.object(outbox_class, "save", new=failing_outbox_save),
							patch.object(adapter.api_module.frappe, "log_error"),
						):
							response = self._invoke(adapter, payload)
						self.assertFalse(response["ok"], response)
						self.assertEqual(self._snapshot(fixture, outbox), before)

					self._run_isolated(outbox_scenario)

	def test_domain_save_failure_rolls_back_prior_job_changes_for_domain_mutating_callbacks(self):
		with patch.dict(os.environ, _ENV, clear=False):
			for adapter in (
				item for item in _ADAPTERS if item.service_type in {"video_processing", "moderation"}
			):
				with self.subTest(service_type=adapter.service_type):

					def scenario() -> None:
						fixture, outbox, token = self._setup(adapter)
						before = self._snapshot(fixture, outbox)
						payload = self._success_payload(adapter, fixture, outbox, token)
						domain = frappe.get_doc(fixture.domain_doctype, fixture.domain_name)
						domain_class = domain.__class__
						original_save = domain_class.save

						def failing_domain_save(doc, *args, **kwargs):
							if doc.name == fixture.domain_name:
								raise RuntimeError("domain write failed with secret token")
							return original_save(doc, *args, **kwargs)

						with (
							patch.object(domain_class, "save", new=failing_domain_save),
							patch.object(adapter.api_module.frappe, "log_error"),
						):
							response = self._invoke(adapter, payload)
						self.assertFalse(response["ok"], response)
						self.assertEqual(self._snapshot(fixture, outbox), before)

					self._run_isolated(scenario)

	def test_operator_work_replay_changes_generation_and_rejects_old_callbacks(self):
		with patch.dict(os.environ, _ENV, clear=False):
			for adapter in _ADAPTERS:
				with self.subTest(service_type=adapter.service_type):

					def scenario() -> None:
						fixture, outbox, old_token = self._setup(adapter)
						outbox.attempt_count = 3
						outbox.max_attempts = 3
						outbox.save(ignore_permissions=True)
						old_payload = self._failure_payload(fixture, outbox, old_token)
						# Endpoint failure callbacks and their domain side effects are covered
						# independently above. This scenario begins from the exact durable
						# terminal state required by operator replay so one service-specific
						# domain policy cannot make the shared generation test order-dependent.
						fixture.job.status = adapter.failed_status
						if fixture.job.meta.has_field("last_error"):
							fixture.job.last_error = old_payload["error"]
						if fixture.job.meta.has_field("completed_at"):
							fixture.job.completed_at = now_datetime()
						fixture.job.save(ignore_permissions=True)
						mark_outbox_callback(
							job_doctype=fixture.doctype,
							job_name=fixture.job.name,
							callback_status="failed",
							success=False,
							error=old_payload["error"],
							dispatch_token=old_token,
							dispatch_generation=int(outbox.dispatch_generation),
						)
						outbox.reload()
						self.assertEqual(outbox.status, "Completed With Failure")

						response = Mock()
						response.content = b"{}"
						response.raise_for_status.return_value = None
						response.json.return_value = {"outcome": "work_replay_authorized"}
						with (
							patch("aos.services.transactional_outbox.frappe.only_for"),
							patch("aos.services.transactional_outbox.frappe.db.commit"),
							patch("aos.services.transactional_outbox.requests.post", return_value=response),
						):
							authorize_terminal_work_replay(
								outbox_name=outbox.name,
								expected_idempotency_key=outbox.idempotency_key,
								additional_attempts=2,
							)

						claim = _claim_one(
							owner="operator-replay-test",
							lease_seconds=60,
							now=now_datetime(),
							outbox_name=outbox.name,
						)
						self.assertIsNotNone(claim)
						# Simulate the companion accepting the proposed replay correlation.
						outbox.reload()
						outbox.status = "Published"
						outbox.published_at = now_datetime()
						outbox.callback_deadline_at = add_to_date(
							now_datetime(), seconds=300, as_datetime=True
						)
						outbox.dispatch_generation = int(claim["dispatch_generation"])
						outbox.current_dispatch_token = str(claim["dispatch_token"])
						outbox.proposed_dispatch_generation = 0
						outbox.proposed_dispatch_token = None
						outbox.claim_token = None
						outbox.claimed_by = None
						outbox.claimed_at = None
						outbox.lease_expires_at = None
						outbox.save(ignore_permissions=True)
						# Production operator replay commits the reopened job before the
						# companion dispatch begins. Persist this accepted generation as the
						# test baseline too, so a callback rollback cannot expose transaction
						# state left behind by an earlier full-suite test.
						frappe.db.commit()
						self.assertGreater(int(outbox.dispatch_generation), 1)
						self.assertNotEqual(outbox.current_dispatch_token, old_token)
						before = self._snapshot(fixture, outbox)
						self.assertFalse(self._invoke(adapter, old_payload)["ok"])
						self.assertEqual(self._snapshot(fixture, outbox), before)

					self._run_isolated(scenario)

	def test_notification_token_deactivation_rolls_back_when_outbox_completion_fails(self):
		adapter = next(item for item in _ADAPTERS if item.service_type == "notification_delivery")
		with patch.dict(os.environ, _ENV, clear=False):

			def scenario() -> None:
				fixture, outbox, token = self._setup(adapter)
				token_name = next(
					name for doctype, name in fixture.cleanup_records if doctype == "AOS Push Token"
				)
				token_hash = frappe.db.get_value("AOS Push Token", token_name, "token_hash")
				payload = self._success_payload(adapter, fixture, outbox, token)
				payload["inactive_token_hashes"] = [token_hash]
				outbox_class = outbox.__class__
				original_save = outbox_class.save

				def failing_outbox_save(doc, *args, **kwargs):
					if doc.name == outbox.name:
						raise RuntimeError("forced outbox completion failure")
					return original_save(doc, *args, **kwargs)

				with (
					patch.object(outbox_class, "save", new=failing_outbox_save),
					patch.object(adapter.api_module.frappe, "log_error") as log_error,
				):
					response = self._invoke(adapter, payload)
				self.assertFalse(response["ok"], response)
				log_error.assert_called_once()
				self.assertEqual(frappe.db.get_value("AOS Push Token", token_name, "is_active"), 1)
				fixture.job.reload()
				outbox.reload()
				self.assertEqual(fixture.job.status, "Processing")
				self.assertEqual(outbox.status, "Published")

			self._run_isolated(scenario)

	def test_failed_callback_restores_in_memory_after_commit_callbacks(self):
		manager = getattr(frappe.db, "after_commit", None)
		functions = getattr(manager, "_functions", None)
		if not isinstance(functions, list):
			self.skipTest("Frappe callback manager does not expose a restorable function list")
		before = list(functions)
		flags = getattr(frappe.local, "flags", None)
		flag_before = bool(getattr(flags, "aos_outbox_after_commit_registered", False)) if flags else False

		def operation() -> None:
			manager.add(lambda: None)
			if flags is not None:
				flags.aos_outbox_after_commit_registered = not flag_before
			raise RuntimeError("force savepoint rollback")

		with self.assertRaises(RuntimeError):
			from aos.api.shared.callback_transaction import execute_callback_atomically

			execute_callback_atomically(service_type="analytics_ingestion", operation=operation)
		self.assertEqual(list(functions), before)
		if flags is not None:
			self.assertEqual(bool(flags.aos_outbox_after_commit_registered), flag_before)

	def test_callback_api_failure_response_is_sanitized_for_all_families(self):
		forbidden = "traceback SQL token=abc https://internal.invalid /srv/private/key"
		with patch.dict(os.environ, _ENV, clear=False):
			for adapter in _ADAPTERS:
				with self.subTest(service_type=adapter.service_type):

					def scenario() -> None:
						fixture, outbox, token = self._setup(adapter)
						payload = self._success_payload(adapter, fixture, outbox, token)
						with patch.object(
							adapter.api_module,
							adapter.handler_name,
							side_effect=RuntimeError(forbidden),
						), patch("frappe.log_error") as log_error:
							response = self._invoke(adapter, payload)
						self.assertFalse(response["ok"], response)
						log_error.assert_called_once()
						serialized = str(response)
						for value in ("traceback", "SQL", "token=abc", "internal.invalid", "/srv/private"):
							self.assertNotIn(value, serialized)

					self._run_isolated(scenario)

from __future__ import annotations

import json
import os
import uuid
from contextlib import suppress
from typing import ClassVar
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from aos.api.shared.callback_transaction import execute_callback_atomically
from aos.services import (
	analytics_pipeline_service,
	moderation_service,
	notification_delivery_service,
	search_ranking_service,
	video_processing_service,
)
from aos.services.transactional_outbox import (
	OUTBOX_DOCTYPE,
	OutboxError,
	_claim_one,
	_mark_enqueue_accepted,
	dispatch_claimed_outbox,
	recover_overdue_published,
	recover_stale_claims,
)
from aos.tests.outbox_fixtures import create_durable_job, create_outbox


class _AcceptedResponse:
	def __init__(
		self,
		*,
		service_job_id: str,
		dispatch_action: str,
		authoritative_generation: int = 0,
		work_state: str = "",
		callback_state: str = "",
	) -> None:
		self.content = b"{}"
		self._payload = {
			"ok": True,
			"service_job_id": service_job_id,
			"dispatch_action": dispatch_action,
			"authoritative_generation": authoritative_generation,
			"work_state": work_state,
			"callback_state": callback_state,
		}

	def raise_for_status(self) -> None:
		return None

	def json(self) -> dict[str, object]:
		return dict(self._payload)


_SERVICE_MODULES = {
	"video_processing": video_processing_service,
	"moderation": moderation_service,
	"search_indexing": search_ranking_service,
	"notification_delivery": notification_delivery_service,
	"analytics_ingestion": analytics_pipeline_service,
}

_DISPATCH_METHODS = {
	"video_processing": video_processing_service.dispatch_video_processing_job,
	"moderation": moderation_service.dispatch_moderation_job,
	"search_indexing": search_ranking_service.dispatch_search_index_job,
	"notification_delivery": notification_delivery_service.dispatch_notification_delivery_job,
	"analytics_ingestion": analytics_pipeline_service.dispatch_analytics_ingest_job,
}

_ENV = {
	"VIDEO_SERVICE_URL": "http://127.0.0.1:18130",
	"VIDEO_SERVICE_SECRET": "test-video-dispatch-secret",
	"VIDEO_SERVICE_CALLBACK_SECRET": "test-video-callback-secret",
	"VIDEO_CALLBACK_URL": "http://127.0.0.1:8000/video-callback",
	"MODERATION_SERVICE_URL": "http://127.0.0.1:18140",
	"MODERATION_SERVICE_SECRET": "test-moderation-dispatch-secret",
	"MODERATION_SERVICE_CALLBACK_SECRET": "test-moderation-callback-secret",
	"MODERATION_CALLBACK_URL": "http://127.0.0.1:8000/moderation-callback",
	"MODERATION_ENABLED": "true",
	"SEARCH_RANKING_SERVICE_URL": "http://127.0.0.1:18150",
	"SEARCH_RANKING_SERVICE_SECRET": "test-search-dispatch-secret",
	"SEARCH_RANKING_SERVICE_CALLBACK_SECRET": "test-search-callback-secret",
	"SEARCH_RANKING_CALLBACK_URL": "http://127.0.0.1:8000/search-callback",
	"SEARCH_RANKING_ENABLED": "true",
	"NOTIFICATION_SERVICE_URL": "http://127.0.0.1:18160",
	"NOTIFICATION_SERVICE_SECRET": "test-notification-dispatch-secret",
	"NOTIFICATION_SERVICE_CALLBACK_SECRET": "test-notification-callback-secret",
	"NOTIFICATION_CALLBACK_URL": "http://127.0.0.1:8000/notification-callback",
	"NOTIFICATION_DELIVERY_ENABLED": "true",
	"ANALYTICS_SERVICE_URL": "http://127.0.0.1:18170",
	"ANALYTICS_SERVICE_SECRET": "test-analytics-dispatch-secret",
	"ANALYTICS_SERVICE_CALLBACK_SECRET": "test-analytics-callback-secret",
	"ANALYTICS_CALLBACK_URL": "http://127.0.0.1:8000/analytics-callback",
	"ANALYTICS_PIPELINE_ENABLED": "true",
	"MINIO_ENDPOINT": "127.0.0.1:19000",
	"MINIO_ACCESS_KEY": "test-access-key",
	"MINIO_SECRET_KEY": "test-secret-key",
	"MINIO_PUBLIC_BASE_URL": "http://127.0.0.1:19100",
	"AOS_MINIO_BUCKET": "shorts",
	"AOS_PUBLIC_BUCKET": "aos-public",
	"AOS_PRIVATE_BUCKET": "aos-private",
}


class TestOutboxRecoveryDispatchAllServices(FrappeTestCase):
	"""Exercise real Frappe dispatcher logic while mocking only companion HTTP."""

	committed_records: ClassVar[list[tuple[str, str]]] = []

	def tearDown(self) -> None:
		for doctype, name in reversed(self.committed_records):
			with suppress(Exception):
				frappe.db.delete(doctype, {"name": name})
		if self.committed_records:
			frappe.db.commit()
		self.committed_records.clear()
		super().tearDown()

	def _track(self, fixture, outbox) -> None:
		self.committed_records.append((OUTBOX_DOCTYPE, outbox.name))
		self.committed_records.extend(fixture.cleanup_records)

	def _publish_as_lost_callback(self, fixture, outbox, *, attempt: int = 1) -> str:
		token = uuid.uuid4().hex
		fixture.job.status = "Processing"
		fixture.job.service_job_id = fixture.job.service_job_id or fixture.job.idempotency_key
		fixture.job.save(ignore_permissions=True)
		outbox.status = "Published"
		outbox.attempt_count = attempt
		outbox.dispatch_generation = attempt
		outbox.current_dispatch_token = token
		outbox.published_at = add_to_date(now_datetime(), seconds=-120, as_datetime=True)
		outbox.callback_deadline_at = add_to_date(now_datetime(), seconds=-60, as_datetime=True)
		outbox.save(ignore_permissions=True)
		frappe.db.commit()
		return token

	def _claim_recovery(self, outbox) -> dict[str, object]:
		with patch(
			"aos.services.transactional_outbox.query_companion_job_status",
			return_value={
				"state": "callback_pending",
				"work_state": "work_complete",
				"callback_state": "pending",
				"dispatch_generation": int(outbox.dispatch_generation or 0),
				"heartbeat_at": "",
				"phase": "callback_pending",
			},
		):
			result = recover_overdue_published(now=now_datetime(), limit=10, outbox_name=outbox.name)
		self.assertEqual(result["requeued"], 1)
		frappe.db.set_value(
			OUTBOX_DOCTYPE,
			outbox.name,
			"next_attempt_at",
			now_datetime(),
			update_modified=False,
		)
		claim = _claim_one(
			owner="recovery-test",
			lease_seconds=60,
			now=now_datetime(),
			outbox_name=outbox.name,
		)
		self.assertIsNotNone(claim)
		self.assertEqual(claim["name"], outbox.name)
		_mark_enqueue_accepted(outbox.name, str(claim["claim_token"]))
		frappe.db.commit()
		return claim

	def _success_payload(self, service_type: str, fixture, outbox) -> dict[str, object]:
		base: dict[str, object] = {
			"job_id": fixture.job.name,
			"idempotency_key": fixture.job.idempotency_key,
			"dispatch_id": outbox.idempotency_key,
			"dispatch_generation": int(outbox.dispatch_generation),
			"dispatch_token": outbox.current_dispatch_token,
		}
		if service_type == "video_processing":
			return {
				**base,
				"status": "ready",
				"duration_seconds": 3.5,
				"playback_url": "https://files.invalid/master.m3u8",
				"processed_file_url": "https://files.invalid/final.mp4",
				"processed_file_key": "tests/final.mp4",
			}
		if service_type == "moderation":
			return {**base, "status": "completed", "decision": "review", "reasons": ["test"]}
		if service_type == "search_indexing":
			return {**base, "status": "completed", "score": 1.0}
		if service_type == "notification_delivery":
			return {
				**base,
				"status": "delivered",
				"success_count": 1,
				"failure_count": 0,
				"token_count": 1,
				"inactive_token_hashes": [],
			}
		return {**base, "status": "ingested", "ingested_count": 0, "skipped_count": 0}

	def _handle_success(self, service_type: str, payload: dict[str, object]):
		handlers = {
			"video_processing": video_processing_service.handle_video_processing_callback,
			"moderation": moderation_service.handle_moderation_callback,
			"search_indexing": search_ranking_service.handle_search_index_callback,
			"notification_delivery": notification_delivery_service.handle_notification_delivery_callback,
			"analytics_ingestion": analytics_pipeline_service.handle_analytics_ingest_callback,
		}
		return execute_callback_atomically(
			service_type=service_type,
			operation=lambda: handlers[service_type](payload),
		)

	def test_callback_timeout_redispatches_all_five_services_and_replay_completes(self):
		with patch.dict(os.environ, _ENV, clear=False):
			for service_type, module in _SERVICE_MODULES.items():
				with self.subTest(service_type=service_type):
					fixture = create_durable_job(service_type)
					with patch("aos.services.transactional_outbox.register_after_commit_publish"):
						outbox = create_outbox(fixture, max_attempts=3)
					self._track(fixture, outbox)
					original_token = self._publish_as_lost_callback(fixture, outbox)
					stable_outbox_id = outbox.idempotency_key
					stable_job_id = fixture.job.idempotency_key
					original_job_name = fixture.job.name
					original_job_count = frappe.db.count(fixture.doctype)

					first_claim = self._claim_recovery(outbox)
					outbox.reload()
					self.assertEqual(outbox.dispatch_generation, 1)
					self.assertEqual(outbox.current_dispatch_token, original_token)
					self.assertEqual(outbox.proposed_dispatch_generation, 2)
					self.assertNotEqual(outbox.proposed_dispatch_token, original_token)

					responses = [
						_AcceptedResponse(
							service_job_id=stable_job_id,
							dispatch_action="stale_generation_replaced",
						),
						_AcceptedResponse(
							service_job_id=stable_job_id,
							dispatch_action="callback_replay_scheduled",
						),
					]
					with patch.object(module.requests, "post", side_effect=responses) as post:
						dispatch_claimed_outbox(outbox.name, str(first_claim["claim_token"]))
						outbox.reload()
						self.assertEqual(outbox.status, "Published")
						self.assertEqual(outbox.redispatch_accepted_count, 1)
						generation_two_token = outbox.current_dispatch_token

						first_request = json.loads(post.call_args_list[0].kwargs["data"])
						self.assertEqual(first_request["idempotency_key"], stable_job_id)
						self.assertEqual(first_request["dispatch_id"], stable_outbox_id)
						self.assertEqual(first_request["dispatch_generation"], 2)
						self.assertEqual(first_request["dispatch_token"], generation_two_token)

						outbox.callback_deadline_at = add_to_date(
							now_datetime(), seconds=-1, as_datetime=True
						)
						outbox.save(ignore_permissions=True)
						frappe.db.commit()
						second_claim = self._claim_recovery(outbox)
						outbox.reload()
						self.assertEqual(outbox.dispatch_generation, 2)
						self.assertEqual(outbox.current_dispatch_token, generation_two_token)
						self.assertEqual(outbox.proposed_dispatch_generation, 3)
						self.assertNotEqual(outbox.proposed_dispatch_token, generation_two_token)
						dispatch_claimed_outbox(outbox.name, str(second_claim["claim_token"]))

					outbox.reload()
					fixture.job.reload()
					self.assertEqual(post.call_count, 2)
					second_request = json.loads(post.call_args_list[1].kwargs["data"])
					self.assertEqual(second_request["idempotency_key"], stable_job_id)
					self.assertEqual(second_request["dispatch_id"], stable_outbox_id)
					self.assertEqual(second_request["dispatch_generation"], 3)
					self.assertEqual(second_request["dispatch_token"], outbox.current_dispatch_token)
					self.assertEqual(outbox.status, "Published")
					self.assertEqual(outbox.dispatch_generation, 3)
					self.assertEqual(outbox.callback_replay_count, 1)
					self.assertEqual(fixture.job.name, original_job_name)
					self.assertEqual(fixture.job.service_job_id, stable_job_id)
					self.assertEqual(frappe.db.count(fixture.doctype), original_job_count)

					payload = self._success_payload(service_type, fixture, outbox)
					self._handle_success(service_type, payload)
					frappe.db.commit()
					outbox.reload()
					self.assertEqual(outbox.status, "Completed")

	def test_ambiguous_dispatch_timeout_accepts_matching_late_callback_for_all_services(self):
		with patch.dict(os.environ, _ENV, clear=False):
			for service_type, module in _SERVICE_MODULES.items():
				with self.subTest(service_type=service_type):
					fixture = create_durable_job(service_type)
					with patch("aos.services.transactional_outbox.register_after_commit_publish"):
						outbox = create_outbox(fixture, max_attempts=3)
					self._track(fixture, outbox)
					claim = _claim_one(
						owner="uncertain-dispatch-test",
						lease_seconds=60,
						now=now_datetime(),
						outbox_name=outbox.name,
					)
					self.assertIsNotNone(claim)
					_mark_enqueue_accepted(outbox.name, str(claim["claim_token"]))
					frappe.db.commit()
					with patch.object(module.requests, "post", side_effect=TimeoutError("response lost")):
						with self.assertRaises(TimeoutError):
							dispatch_claimed_outbox(outbox.name, str(claim["claim_token"]))
					outbox.reload()
					fixture.job.reload()
					self.assertEqual(outbox.status, "Dispatch Uncertain")
					self.assertEqual(fixture.job.status, "Processing")
					self.assertEqual(outbox.dispatch_generation, 0)
					self.assertEqual(outbox.proposed_dispatch_generation, 1)
					self.assertTrue(outbox.proposed_dispatch_token)
					payload = self._success_payload(service_type, fixture, outbox)
					payload["dispatch_generation"] = int(outbox.proposed_dispatch_generation)
					payload["dispatch_token"] = outbox.proposed_dispatch_token
					self._handle_success(service_type, payload)
					frappe.db.commit()
					outbox.reload()
					self.assertEqual(outbox.status, "Completed")
					self.assertEqual(outbox.completed_dispatch_generation, 1)

	def test_processing_duplicate_guard_is_bypassed_only_by_private_recovery_context(self):
		with patch.dict(os.environ, _ENV, clear=False):
			for service_type, module in _SERVICE_MODULES.items():
				with self.subTest(service_type=service_type):
					savepoint = f"duplicate_guard_{uuid.uuid4().hex[:12]}"
					frappe.db.savepoint(savepoint)
					try:
						fixture = create_durable_job(service_type, status="Processing")
						fixture.job.service_job_id = fixture.job.idempotency_key
						fixture.job.save(ignore_permissions=True)
						with patch.object(module.requests, "post") as post:
							result = _DISPATCH_METHODS[service_type](fixture.job.name)
						self.assertEqual(result.name, fixture.job.name)
						post.assert_not_called()
					finally:
						frappe.db.rollback(save_point=savepoint)

	def test_recovery_requires_an_explicit_accepted_http_outcome_for_every_service(self):
		with patch.dict(os.environ, _ENV, clear=False):
			for service_type, module in _SERVICE_MODULES.items():
				with self.subTest(service_type=service_type):
					fixture = create_durable_job(service_type)
					with patch("aos.services.transactional_outbox.register_after_commit_publish"):
						outbox = create_outbox(fixture, max_attempts=3)
					self._track(fixture, outbox)
					self._publish_as_lost_callback(fixture, outbox)
					claim = self._claim_recovery(outbox)
					response = _AcceptedResponse(
						service_job_id=fixture.job.idempotency_key,
						dispatch_action="unsupported_noop",
					)
					with patch.object(module.requests, "post", return_value=response) as post:
						with self.assertRaises(OutboxError):
							dispatch_claimed_outbox(outbox.name, str(claim["claim_token"]))
					post.assert_called_once()
					outbox.reload()
					fixture.job.reload()
					self.assertEqual(outbox.status, "Failed")
					self.assertEqual(outbox.last_error, "DOWNSTREAM_DISPATCH_FAILED")
					self.assertEqual(fixture.job.last_error, "DOWNSTREAM_DISPATCH_FAILED")
					self.assertNotEqual(outbox.status, "Published")

	def test_expired_publisher_lease_for_processing_job_redispatches_all_services(self):
		with patch.dict(os.environ, _ENV, clear=False):
			for service_type, module in _SERVICE_MODULES.items():
				with self.subTest(service_type=service_type):
					fixture = create_durable_job(service_type, status="Processing")
					fixture.job.service_job_id = fixture.job.idempotency_key
					fixture.job.save(ignore_permissions=True)
					with patch("aos.services.transactional_outbox.register_after_commit_publish"):
						outbox = create_outbox(fixture, max_attempts=3)
					self._track(fixture, outbox)
					outbox.status = "Dispatched"
					outbox.attempt_count = 1
					outbox.dispatch_generation = 1
					outbox.current_dispatch_token = uuid.uuid4().hex
					outbox.claim_token = uuid.uuid4().hex
					outbox.claimed_by = "crashed-publisher"
					outbox.lease_expires_at = add_to_date(now_datetime(), seconds=-60, as_datetime=True)
					outbox.save(ignore_permissions=True)
					frappe.db.commit()
					self.assertEqual(
						recover_stale_claims(now=now_datetime(), outbox_name=outbox.name), 1
					)
					claim = _claim_one(
						owner="lease-recovery-test",
						lease_seconds=60,
						now=now_datetime(),
						outbox_name=outbox.name,
					)
					self.assertIsNotNone(claim)
					self.assertEqual(claim["dispatch_reason"], "publisher_lease_expired")
					_mark_enqueue_accepted(outbox.name, str(claim["claim_token"]))
					frappe.db.commit()
					response = _AcceptedResponse(
						service_job_id=fixture.job.idempotency_key,
						dispatch_action="duplicate_active",
					)
					with patch.object(module.requests, "post", return_value=response) as post:
						dispatch_claimed_outbox(outbox.name, str(claim["claim_token"]))
					post.assert_called_once()
					outbox.reload()
					self.assertEqual(outbox.status, "Published")
					self.assertEqual(outbox.dispatch_generation, 1)
					self.assertEqual(outbox.duplicate_active_dispatch_count, 1)

	def test_duplicate_active_promotes_confirmed_initial_uncertain_generation_for_all_services(self):
		with patch.dict(os.environ, _ENV, clear=False):
			for service_type, module in _SERVICE_MODULES.items():
				with self.subTest(service_type=service_type):
					fixture = create_durable_job(service_type, status="Processing")
					fixture.job.service_job_id = fixture.job.idempotency_key
					fixture.job.save(ignore_permissions=True)
					with patch("aos.services.transactional_outbox.register_after_commit_publish"):
						outbox = create_outbox(fixture, max_attempts=3)
					self._track(fixture, outbox)
					proposal_token = uuid.uuid4().hex
					outbox.status = "Dispatch Uncertain"
					outbox.attempt_count = 1
					outbox.dispatch_generation = 0
					outbox.current_dispatch_token = None
					outbox.proposed_dispatch_generation = 1
					outbox.proposed_dispatch_token = proposal_token
					outbox.pending_dispatch_reason = "dispatch_uncertain"
					outbox.next_attempt_at = now_datetime()
					outbox.save(ignore_permissions=True)
					frappe.db.commit()
					claim = _claim_one(
						owner="uncertain-duplicate-test",
						lease_seconds=60,
						now=now_datetime(),
						outbox_name=outbox.name,
					)
					self.assertIsNotNone(claim)
					self.assertEqual(claim["dispatch_generation"], 1)
					self.assertEqual(claim["dispatch_token"], proposal_token)
					_mark_enqueue_accepted(outbox.name, str(claim["claim_token"]))
					frappe.db.commit()
					response = _AcceptedResponse(
						service_job_id=fixture.job.idempotency_key,
						dispatch_action="duplicate_active",
						authoritative_generation=1,
						work_state="started",
						callback_state="not_ready",
					)
					with patch.object(module.requests, "post", return_value=response):
						dispatch_claimed_outbox(outbox.name, str(claim["claim_token"]))
					outbox.reload()
					self.assertEqual(outbox.status, "Published")
					self.assertEqual(outbox.dispatch_generation, 1)
					self.assertEqual(outbox.current_dispatch_token, proposal_token)
					self.assertEqual(int(outbox.proposed_dispatch_generation or 0), 0)
					self.assertFalse(outbox.proposed_dispatch_token)

	def test_callback_timeout_exhaustion_enters_manual_review_for_every_service_type(self):
		with patch.dict(os.environ, _ENV, clear=False):
			for service_type in _SERVICE_MODULES:
				with self.subTest(service_type=service_type):
					fixture = create_durable_job(service_type)
					with patch("aos.services.transactional_outbox.register_after_commit_publish"):
						outbox = create_outbox(fixture, max_attempts=1)
					self._track(fixture, outbox)
					self._publish_as_lost_callback(fixture, outbox, attempt=1)
					outbox.max_attempts = 1
					outbox.reconciliation_max_attempts = 1
					outbox.save(ignore_permissions=True)
					with patch(
						"aos.services.transactional_outbox.query_companion_job_status",
						return_value={
							"state": "callback_pending",
							"work_state": "work_complete",
							"callback_state": "dead_letter",
							"dispatch_generation": int(outbox.dispatch_generation or 0),
						},
					):
						result = recover_overdue_published(
							now=now_datetime(), limit=10, outbox_name=outbox.name
						)
					frappe.db.commit()
					outbox.reload()
					self.assertEqual(result["manual_review"], 1)
					self.assertEqual(result["dead_lettered"], 0)
					self.assertEqual(outbox.status, "Manual Review")
					self.assertEqual(outbox.last_error, "DOWNSTREAM_CALLBACK_DEADLINE_EXCEEDED")

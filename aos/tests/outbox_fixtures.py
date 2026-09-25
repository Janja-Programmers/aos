from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

import frappe

from aos.services.transactional_outbox import ensure_outbox_for_job


_OUTBOX_FIXTURE_DISPATCH = {
	"video_processing": ("long", 3600),
	"moderation": ("long", 900),
	"search_indexing": ("short", 600),
	"notification_delivery": ("short", 600),
	"analytics_ingestion": ("short", 600),
}


@dataclass(frozen=True)
class DurableJobFixture:
	service_type: str
	doctype: str
	job: Any
	domain_doctype: str | None = None
	domain_name: str | None = None
	cleanup_records: tuple[tuple[str, str], ...] = ()


def cleanup_committed_records(
	records: list[tuple[str, str]] | tuple[tuple[str, str], ...],
	*,
	max_attempts: int = 5,
	base_delay_seconds: float = 0.1,
) -> None:
	"""Delete committed test fixtures with bounded deadlock retries."""
	# Discard every mutation made after the intentionally committed fixture
	# baseline before performing compensating deletes. Without this rollback,
	# cleanup's final commit could accidentally persist untracked retry jobs,
	# outbox rows, notifications, or domain mutations created by the test.
	frappe.db.rollback()

	unique_records: list[tuple[str, str]] = []
	seen: set[tuple[str, str]] = set()
	for record in records:
		if record in seen:
			continue
		seen.add(record)
		unique_records.append(record)

	attempts = max(1, int(max_attempts or 1))
	for attempt in range(attempts):
		try:
			for doctype, name in unique_records:
				frappe.db.delete(doctype, {"name": name})
			frappe.db.commit()
			return
		except frappe.QueryDeadlockError:
			frappe.db.rollback()
			if attempt == attempts - 1:
				raise
			time.sleep(base_delay_seconds * (2**attempt))
		except Exception:
			frappe.db.rollback()
			raise


def _insert(doc: dict[str, Any], *, ignore_links: bool = False) -> Any:
	value = frappe.get_doc(doc)
	if ignore_links:
		value.flags.ignore_links = True
	return value.insert(ignore_permissions=True)


def _media_and_short(suffix: str) -> tuple[Any, Any]:
	media = _insert(
		{
			"doctype": "AOS Media Object",
			"owner_user": "Administrator",
			"purpose": "short_video_raw",
			"status": "Uploaded",
			"visibility": "Private",
			"bucket": "test-private",
			"object_key": f"tests/{suffix}/raw.mp4",
			"original_filename": "raw.mp4",
			"content_type": "video/mp4",
			"size_bytes": 1024,
			"etag": hashlib.sha256(suffix.encode()).hexdigest(),
		}
	)
	short = _insert(
		{
			"doctype": "AOS Short",
			"content_type": "Video",
			"lifecycle_status": "Processing",
			"processing_status": "Processing",
			"moderation_status": "Draft",
			"processing_generation": 1,
			"raw_video_media": media.name,
			"caption": "",
			"audience": "everyone",
		}
	)
	# The production upload flow attaches the ready raw video before dispatching
	# processing. Keep this shared fixture aligned with that lifecycle so Media
	# completion validation is exercised rather than bypassed.
	media.status = "Attached"
	media.attached_doctype = short.doctype
	media.attached_name = short.name
	media.attached_field = "raw_video_media"
	media.attached_at = frappe.utils.now_datetime()
	media.processing_started_at = frappe.utils.now_datetime()
	media.save(ignore_permissions=True)
	return media, short


def create_durable_job(
	service_type: str, *, status: str = "Queued", malformed_payload: bool = False
) -> DurableJobFixture:
	suffix = uuid.uuid4().hex[:12]
	idempotency_key = f"test-{service_type}-{suffix}"
	request_payload = "{malformed" if malformed_payload else json.dumps({"source": "test"})

	if service_type == "video_processing":
		media, short = _media_and_short(suffix)
		job = _insert(
			{
				"doctype": "AOS Video Processing Job",
				"short": short.name,
				"operation": "Process",
				"raw_video_media": short.raw_video_media,
				"status": status,
				"generation": 1,
				"attempt_count": 0,
				"max_attempts": 3,
				"idempotency_key": idempotency_key,
				"active_key": f"{short.name}:Process:1",
				"request_payload": request_payload,
			}
		)
		return DurableJobFixture(
			service_type,
			job.doctype,
			job,
			short.doctype,
			short.name,
			((job.doctype, job.name), (short.doctype, short.name), (media.doctype, media.name)),
		)

	if service_type == "moderation":
		media, short = _media_and_short(suffix)
		# Moderation callbacks mutate only the exact pending Short revision and
		# generation. Build the durable fixture in that real production state so
		# rollback tests exercise the domain save rather than a stale-callback no-op.
		short.lifecycle_status = "Pending Review"
		short.processing_status = "Ready"
		short.moderation_status = "Pending"
		short.moderation_generation = max(1, int(short.moderation_generation or 0))
		short.revision = max(1, int(short.revision or 0))
		short.save(ignore_permissions=True)
		moderation_context = json.dumps({
			"moderation_generation": int(short.moderation_generation),
			"revision": int(short.revision),
			"was_visible": False,
		})
		job = _insert(
			{
				"doctype": "AOS Moderation Job",
				"target_doctype": short.doctype,
				"target_name": short.name,
				"target_owner": "Administrator",
				"content_kind": "short",
				"source": "test",
				"status": status,
				"decision": "pending",
				"evaluation_key": idempotency_key,
				"content_fingerprint": idempotency_key,
				"content_version": f"{int(short.revision)}:{int(short.moderation_generation)}",
				"policy_version": "aos-safety-2026-09-25-v2",
				"attempt_count": 0,
				"max_attempts": 3,
				"idempotency_key": idempotency_key,
				"text_items_json": "[]",
				"media_items_json": "[]",
				"context_json": moderation_context,
			}
		)
		return DurableJobFixture(
			service_type,
			job.doctype,
			job,
			short.doctype,
			short.name,
			((job.doctype, job.name), (short.doctype, short.name), (media.doctype, media.name)),
		)

	if service_type == "search_indexing":
		job = _insert(
			{
				"doctype": "AOS Search Index Job",
				"target_doctype": "User",
				"target_name": "Administrator",
				"target_owner": "Administrator",
				"index_kind": "user",
				"action": "upsert",
				"source": "test",
				"status": status,
				"attempt_count": 0,
				"max_attempts": 3,
				"idempotency_key": idempotency_key,
				"request_payload": request_payload,
				"document_json": '{"name":"Administrator"}',
			}
		)
		return DurableJobFixture(
			service_type,
			job.doctype,
			job,
			"User",
			"Administrator",
			((job.doctype, job.name),),
		)

	if service_type == "notification_delivery":
		# Use a real persistent notification contract so durable-outbox recovery
		# exercises companion HTTP dispatch. Unknown transient events are
		# intentionally suppressed by production policy and are not a valid fixture.
		notification = _insert(
			{
				"doctype": "AOS Notification",
				"user": "Administrator",
				"type": "ad_approved",
				"title": "Test",
				"body": "Test body",
				"payload": {"ad_id": f"test-ad-{suffix}"},
				"dedupe_key": f"test:notification-delivery:{suffix}",
			}
		)
		token = _insert(
			{
				"doctype": "AOS Push Token",
				"user": "Administrator",
				"token": f"test-token-{suffix}",
				"device_type": "web",
				"device_id": f"test-{suffix}",
				"registration_kind": "token",
				"is_active": 1,
			}
		)
		job = _insert(
			{
				"doctype": "AOS Notification Delivery Job",
				"naming_series": "NTF-JOB-.YYYY.-.#####",
				"user": "Administrator",
				"notification": notification.name,
				"delivery_kind": "persistent",
				"channel": "push",
				"event": "aos_ad_approved",
				"title": "Test",
				"body": "Test body",
				"payload_json": json.dumps({"ad_id": f"test-ad-{suffix}"}),
				"status": status,
				"attempt_count": 0,
				"max_attempts": 3,
				"idempotency_key": idempotency_key,
				"request_payload": request_payload,
			}
		)
		return DurableJobFixture(
			service_type,
			job.doctype,
			job,
			cleanup_records=(
				(job.doctype, job.name),
				(token.doctype, token.name),
				(notification.doctype, notification.name),
			),
		)

	if service_type == "analytics_ingestion":
		job = _insert(
			{
				"doctype": "AOS Analytics Ingest Job",
				"naming_series": "ANLY-JOB-.YYYY.-.#####",
				"source": "test",
				"status": status,
				"events_json": "[]",
				"attempt_count": 0,
				"max_attempts": 3,
				"idempotency_key": idempotency_key,
				"request_payload": request_payload,
			},
			ignore_links=True,
		)
		return DurableJobFixture(
			service_type,
			job.doctype,
			job,
			cleanup_records=((job.doctype, job.name),),
		)

	raise AssertionError(f"Unsupported fixture service: {service_type}")


def create_outbox(fixture: DurableJobFixture, *, max_attempts: int = 3) -> Any:
	queue, timeout_seconds = _OUTBOX_FIXTURE_DISPATCH[fixture.service_type]
	return ensure_outbox_for_job(
		service_type=fixture.service_type,
		job=fixture.job,
		queue=queue,
		timeout_seconds=timeout_seconds,
		aggregate_doctype=fixture.domain_doctype,
		aggregate_name=fixture.domain_name,
		max_attempts=max_attempts,
	)

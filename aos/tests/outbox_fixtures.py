from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

import frappe

from aos.patches.v1_0.backfill_transactional_outbox import SPECS, LegacyJobSpec
from aos.services.transactional_outbox import ensure_outbox_for_job


@dataclass(frozen=True)
class DurableJobFixture:
	service_type: str
	doctype: str
	job: Any
	domain_doctype: str | None = None
	domain_name: str | None = None
	cleanup_records: tuple[tuple[str, str], ...] = ()


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
			"naming_series": "SHORT-.YYYY.-.#####",
			"status": "processing",
			"visibility_status": "hidden",
			"file_key": f"tests/{suffix}/raw.mp4",
			"raw_video_media": media.name,
			"caption": "",
			"hashtags": "[]",
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
				"naming_series": "VIDEO-JOB-.YYYY.-.#####",
				"short": short.name,
				"raw_video_media": short.raw_video_media,
				"status": status,
				"reason": "short_upload",
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
			short.doctype,
			short.name,
			((job.doctype, job.name), (short.doctype, short.name), (media.doctype, media.name)),
		)

	if service_type == "moderation":
		media, short = _media_and_short(suffix)
		job = _insert(
			{
				"doctype": "AOS Moderation Job",
				"naming_series": "MOD-JOB-.YYYY.-.#####",
				"target_doctype": short.doctype,
				"target_name": short.name,
				"target_owner": "Administrator",
				"content_kind": "short",
				"source": "test",
				"status": status,
				"decision": "pending",
				"attempt_count": 0,
				"max_attempts": 3,
				"idempotency_key": idempotency_key,
				"request_payload": request_payload,
				"text_items_json": "[]",
				"media_items_json": "[]",
				"context_json": "{}",
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
				"naming_series": "SRCH-JOB-.YYYY.-.#####",
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
		token = _insert(
			{
				"doctype": "AOS Push Token",
				"user": "Administrator",
				"token": f"test-token-{suffix}",
				"token_hash": hashlib.sha256(suffix.encode()).hexdigest(),
				"device_type": "web",
				"device_id": f"test-{suffix}",
				"is_active": 1,
			}
		)
		job = _insert(
			{
				"doctype": "AOS Notification Delivery Job",
				"naming_series": "NTF-JOB-.YYYY.-.#####",
				"user": "Administrator",
				"delivery_kind": "transient",
				"channel": "push",
				"event": "test_event",
				"title": "Test",
				"body": "Test body",
				"payload_json": "{}",
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
			cleanup_records=((job.doctype, job.name), (token.doctype, token.name)),
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
	spec = next(item for item in SPECS if item.service_type == fixture.service_type)
	return ensure_outbox_for_job(
		service_type=fixture.service_type,
		job=fixture.job,
		queue=spec.queue,
		timeout_seconds=spec.timeout_seconds,
		aggregate_doctype=fixture.domain_doctype,
		aggregate_name=fixture.domain_name,
		max_attempts=max_attempts,
	)


def legacy_spec(service_type: str) -> LegacyJobSpec:
	return next(item for item in SPECS if item.service_type == service_type)

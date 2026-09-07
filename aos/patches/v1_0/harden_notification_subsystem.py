"""Data-only reconciliation for the Notification subsystem.

Runs post-model-sync before Notification-specific indexes are installed. The
patch is idempotent and intentionally does not invent notification history or
business events that cannot be reconstructed safely.
"""

from __future__ import annotations

import hashlib
import json

import frappe
from frappe.utils import now_datetime

_BATCH_SIZE = 250
_TERMINAL_JOB_STATUSES = {"Delivered", "Skipped", "Failed", "Cancelled"}


def execute() -> None:
    if not frappe.db.table_exists("AOS Notification"):
        return
    normalized_notifications = _normalize_notification_rows()
    tokens_deactivated = _normalize_push_tokens()
    jobs_cancelled = _cancel_undeliverable_jobs()
    request_payloads_redacted = _redact_legacy_request_payloads()
    response_payloads_redacted = _redact_legacy_response_payloads()
    frappe.logger("aos.notifications", allow_site=True).info(
        "notification_data_hardening_complete normalized_notifications=%s tokens_deactivated=%s "
        "jobs_cancelled=%s request_payloads_redacted=%s response_payloads_redacted=%s",
        normalized_notifications,
        tokens_deactivated,
        jobs_cancelled,
        request_payloads_redacted,
        response_payloads_redacted,
    )


def _normalize_notification_rows() -> int:
    rows = frappe.db.sql(
        """
        SELECT COUNT(*) AS count
        FROM `tabAOS Notification`
        WHERE COALESCE(is_read, 0) NOT IN (0, 1)
        """,
        as_dict=True,
    )
    count = int(rows[0].count or 0) if rows else 0
    if count:
        frappe.db.sql(
            """
            UPDATE `tabAOS Notification`
            SET is_read = CASE WHEN COALESCE(is_read, 0) = 0 THEN 0 ELSE 1 END
            WHERE COALESCE(is_read, 0) NOT IN (0, 1)
            """
        )
    return count


def _normalize_push_tokens() -> int:
    if not frappe.db.table_exists("AOS Push Token"):
        return 0
    # Inactive rows must never occupy the active-device uniqueness slot.
    frappe.db.sql(
        """
        UPDATE `tabAOS Push Token`
        SET active_device_key = NULL
        WHERE COALESCE(is_active, 0) = 0 AND active_device_key IS NOT NULL
        """
    )

    profile_exists = frappe.db.table_exists("AOS Profile")
    profile_join = "LEFT JOIN `tabAOS Profile` p ON p.user = pt.user" if profile_exists else ""
    profile_clause = (
        "OR COALESCE(p.account_status, 'Active') IN ('Deleted', 'Suspended')"
        if profile_exists
        else ""
    )
    total = 0
    while True:
        rows = frappe.db.sql(
            f"""
            SELECT pt.name
            FROM `tabAOS Push Token` pt
            LEFT JOIN `tabUser` u ON u.name = pt.user
            {profile_join}
            WHERE pt.is_active = 1
              AND (
                u.name IS NULL OR COALESCE(u.enabled, 0) = 0
                {profile_clause}
              )
            ORDER BY pt.name
            LIMIT %s
            """,
            (_BATCH_SIZE,),
            as_dict=True,
        )
        names = [row.name for row in rows if row.name]
        if not names:
            break
        frappe.db.sql(
            """
            UPDATE `tabAOS Push Token`
            SET is_active = 0, active_device_key = NULL, last_used_at = %s
            WHERE name IN %s AND is_active = 1
            """,
            (now_datetime(), tuple(names)),
        )
        total += len(names)

    # Future writes are controller-validated; reconcile malformed legacy active
    # provider registrations so one bad row cannot poison an entire delivery job.
    last_name = ""
    while True:
        rows = frappe.db.sql(
            """
            SELECT name, token, token_hash, device_type
            FROM `tabAOS Push Token`
            WHERE name > %s AND is_active = 1
            ORDER BY name
            LIMIT %s
            """,
            (last_name, _BATCH_SIZE),
            as_dict=True,
        )
        if not rows:
            break
        invalid_names = []
        for row in rows:
            token = str(row.token or "").strip()
            token_hash = str(row.token_hash or "").strip().lower()
            device_type = str(row.device_type or "").strip().lower()
            invalid_token = (
                len(token) < 20
                or len(token) > 4096
                or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in token)
            )
            valid_hash = (
                len(token_hash) == 64
                and all(ch in "0123456789abcdef" for ch in token_hash)
                and token_hash == hashlib.sha256(token.encode("utf-8")).hexdigest()
            )
            if invalid_token or not valid_hash or device_type not in {"android", "ios", "web"}:
                invalid_names.append(row.name)
        if invalid_names:
            frappe.db.sql(
                """
                UPDATE `tabAOS Push Token`
                SET is_active = 0, active_device_key = NULL, last_used_at = %s
                WHERE name IN %s AND is_active = 1
                """,
                (now_datetime(), tuple(invalid_names)),
            )
            total += len(invalid_names)
        last_name = rows[-1].name
    return total


def _cancel_undeliverable_jobs() -> int:
    if not frappe.db.table_exists("AOS Notification Delivery Job"):
        return 0

    profile_exists = frappe.db.table_exists("AOS Profile")
    profile_join = "LEFT JOIN `tabAOS Profile` p ON p.user = j.user" if profile_exists else ""
    profile_clause = (
        "OR COALESCE(p.account_status, 'Active') IN ('Deleted', 'Suspended')"
        if profile_exists
        else ""
    )
    total = 0
    while True:
        rows = frappe.db.sql(
            f"""
            SELECT j.name
            FROM `tabAOS Notification Delivery Job` j
            LEFT JOIN `tabUser` u ON u.name = j.user
            {profile_join}
            LEFT JOIN `tabAOS Notification` n ON n.name = j.notification
            WHERE j.status IN ('Queued', 'Dispatching', 'Processing')
              AND (
                u.name IS NULL OR COALESCE(u.enabled, 0) = 0
                {profile_clause}
                OR (j.delivery_kind = 'persistent' AND n.name IS NULL)
              )
            ORDER BY j.creation, j.name
            LIMIT %s
            """,
            (_BATCH_SIZE,),
            as_dict=True,
        )
        names = [row.name for row in rows if row.name]
        if not names:
            return total
        now = now_datetime()
        frappe.db.sql(
            """
            UPDATE `tabAOS Notification Delivery Job`
            SET status = 'Cancelled', completed_at = %s, last_error = 'recipient_or_notification_unavailable',
                request_payload = NULL
            WHERE name IN %s AND status IN ('Queued', 'Dispatching', 'Processing')
            """,
            (now, tuple(names)),
        )
        if frappe.db.table_exists("AOS Transactional Outbox"):
            frappe.db.sql(
                """
                UPDATE `tabAOS Transactional Outbox`
                SET status = 'Cancelled', completed_at = %s, next_attempt_at = NULL,
                    claimed_by = NULL, claim_token = NULL, claimed_at = NULL, lease_expires_at = NULL,
                    last_error = 'recipient_or_notification_unavailable'
                WHERE service_type = 'notification_delivery'
                  AND job_doctype = 'AOS Notification Delivery Job'
                  AND job_name IN %s
                  AND status NOT IN ('Completed', 'Completed With Failure', 'Failed', 'Dead Letter', 'Cancelled')
                """,
                (now, tuple(names)),
            )
        total += len(names)


def _redact_request_payload(value: str | None) -> str | None:
    if not value:
        return value
    try:
        payload = json.loads(value)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    tokens = payload.get("tokens") if isinstance(payload.get("tokens"), list) else []
    safe_tokens = []
    for token in tokens[:5000]:
        if not isinstance(token, dict):
            continue
        token_hash = str(token.get("token_hash") or "").strip().lower()
        if len(token_hash) != 64 or any(ch not in "0123456789abcdef" for ch in token_hash):
            continue
        safe_tokens.append(
            {
                "token_hash": token_hash,
                "device_type": str(token.get("device_type") or "").strip().lower()[:20],
            }
        )
    raw_options = payload.get("options") if isinstance(payload.get("options"), dict) else {}
    safe_options = {
        key: raw_options.get(key)
        for key in (
            "priority",
            "ttl_seconds",
            "android_channel_id",
            "android_notification_priority",
        )
        if raw_options.get(key) is not None
    }
    clean = {
        "job_id": str(payload.get("job_id") or "").strip()[:200],
        "notification_id": str(payload.get("notification_id") or "").strip()[:180] or None,
        "delivery_kind": str(payload.get("delivery_kind") or "").strip()[:40],
        "channel": str(payload.get("channel") or "").strip()[:40],
        "event": str(payload.get("event") or "").strip()[:80],
        "options": safe_options,
        "tokens": safe_tokens,
    }
    return json.dumps(clean, ensure_ascii=False, default=str)


def _redact_legacy_request_payloads() -> int:
    if not frappe.db.table_exists("AOS Notification Delivery Job"):
        return 0
    updated = 0
    last_name = ""
    while True:
        rows = frappe.db.sql(
            """
            SELECT name, request_payload
            FROM `tabAOS Notification Delivery Job`
            WHERE name > %s AND request_payload IS NOT NULL AND request_payload != ''
            ORDER BY name
            LIMIT %s
            """,
            (last_name, _BATCH_SIZE),
            as_dict=True,
        )
        if not rows:
            break
        for row in rows:
            redacted = _redact_request_payload(row.request_payload)
            if redacted != row.request_payload:
                frappe.db.set_value(
                    "AOS Notification Delivery Job",
                    row.name,
                    "request_payload",
                    redacted,
                    update_modified=False,
                )
                updated += 1
        last_name = rows[-1].name
    return updated


def _safe_nonnegative_int(value, *, maximum: int = 5000) -> int:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, min(number, maximum))




def _safe_reason(value, *, maximum: int = 120) -> str | None:
    clean = str(value or "").strip()[:maximum]
    if clean and all(ch.isalnum() or ch in "._:-" for ch in clean):
        return clean
    return None

def _redact_response_payload(value: str | None) -> str | None:
    if not value:
        return value
    try:
        payload = json.loads(value)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None

    inactive_hashes = []
    for raw in (payload.get("inactive_token_hashes") or [])[:2000]:
        token_hash = str(raw or "").strip().lower()
        if len(token_hash) == 64 and all(ch in "0123456789abcdef" for ch in token_hash):
            inactive_hashes.append(token_hash)

    safe_responses = []
    responses = payload.get("provider_responses")
    if isinstance(responses, list):
        for response in responses[:50]:
            if not isinstance(response, dict):
                continue
            safe_errors = []
            errors = response.get("errors")
            if isinstance(errors, list):
                for error in errors[:100]:
                    if not isinstance(error, dict):
                        continue
                    token_hash = str(error.get("token_hash") or "").strip().lower()
                    if len(token_hash) != 64 or any(ch not in "0123456789abcdef" for ch in token_hash):
                        token_hash = ""
                    safe_errors.append(
                        {
                            "token_hash": token_hash or None,
                            "device_type": _safe_reason(error.get("device_type"), maximum=20),
                            "inactive": bool(error.get("inactive")),
                            "error_class": _safe_reason(error.get("error_class"), maximum=80),
                            "code": _safe_reason(error.get("code"), maximum=120),
                            "error_code": _safe_reason(error.get("error_code"), maximum=120),
                            "http_status": _safe_nonnegative_int(error.get("http_status"), maximum=599) or None,
                            "error_category": _safe_reason(error.get("error_category"), maximum=80),
                        }
                    )
            acceptance_ids = []
            raw_acceptance = response.get("provider_acceptance_ids")
            if isinstance(raw_acceptance, list):
                acceptance_ids = [
                    value
                    for item in raw_acceptance[:500]
                    if (value := _safe_reason(item, maximum=24))
                ]
            safe_responses.append(
                {
                    "chunk_index": _safe_nonnegative_int(response.get("chunk_index"), maximum=100000),
                    "delivery_mode": _safe_reason(response.get("delivery_mode"), maximum=40),
                    "success_count": _safe_nonnegative_int(response.get("success_count")),
                    "failure_count": _safe_nonnegative_int(response.get("failure_count")),
                    "provider_acceptance_ids": acceptance_ids,
                    "errors": safe_errors,
                }
            )

    error = _safe_reason(payload.get("error"), maximum=120)
    clean = {
        "job_id": str(payload.get("job_id") or "").strip()[:200],
        "status": str(payload.get("status") or "").strip()[:40],
        "channel": str(payload.get("channel") or "").strip()[:40],
        "token_count": _safe_nonnegative_int(payload.get("token_count")),
        "success_count": _safe_nonnegative_int(payload.get("success_count")),
        "failure_count": _safe_nonnegative_int(payload.get("failure_count")),
        "inactive_token_hashes": inactive_hashes,
        "provider_responses": safe_responses,
        "error": error,
    }
    return json.dumps(clean, ensure_ascii=False, default=str)


def _redact_legacy_response_payloads() -> int:
    if not frappe.db.table_exists("AOS Notification Delivery Job"):
        return 0
    updated = 0
    last_name = ""
    while True:
        rows = frappe.db.sql(
            """
            SELECT name, response_payload
            FROM `tabAOS Notification Delivery Job`
            WHERE name > %s AND response_payload IS NOT NULL AND response_payload != ''
            ORDER BY name
            LIMIT %s
            """,
            (last_name, _BATCH_SIZE),
            as_dict=True,
        )
        if not rows:
            break
        for row in rows:
            redacted = _redact_response_payload(row.response_payload)
            if redacted != row.response_payload:
                frappe.db.set_value(
                    "AOS Notification Delivery Job",
                    row.name,
                    "response_payload",
                    redacted,
                    update_modified=False,
                )
                updated += 1
        last_name = rows[-1].name
    return updated


from __future__ import annotations

import json

import frappe
from frappe.model.document import Document


VALID_STATUSES = frozenset(
    {"Queued", "Dispatching", "Processing", "Delivered", "Skipped", "Failed", "Cancelled"}
)
VALID_DELIVERY_KINDS = frozenset({"persistent", "transient"})
VALID_CHANNELS = frozenset({"push"})
VALID_PRIORITIES = frozenset({"", "high", "normal"})
VALID_ANDROID_NOTIFICATION_PRIORITIES = frozenset({"", "min", "low", "default", "high", "max"})
MAX_PAYLOAD_BYTES = 16 * 1024
MAX_ERROR_LENGTH = 1000


class AOSNotificationDeliveryJob(Document):
    def validate(self):
        self.status = str(self.status or "Queued").strip()
        self.channel = str(self.channel or "push").strip().lower()
        self.delivery_kind = str(self.delivery_kind or "persistent").strip().lower()
        self.event = str(self.event or "").strip()
        self.user = str(self.user or "").strip()
        self.title = str(self.title or "").strip()
        self.body = str(self.body or "").strip()
        self.priority = str(self.priority or "").strip().lower() or None
        self.android_notification_priority = (
            str(self.android_notification_priority or "").strip().lower() or None
        )
        self.android_channel_id = str(self.android_channel_id or "").strip() or None
        self.idempotency_key = str(self.idempotency_key or "").strip()

        if self.status not in VALID_STATUSES:
            frappe.throw("Invalid notification delivery status.")
        if self.delivery_kind not in VALID_DELIVERY_KINDS:
            frappe.throw("Invalid notification delivery kind.")
        if self.channel not in VALID_CHANNELS:
            frappe.throw("Unsupported notification delivery channel.")
        if not self.user or not frappe.db.exists("User", self.user):
            frappe.throw("Notification delivery user is required.")
        if not self.event or len(self.event) > 80:
            frappe.throw("Invalid notification delivery event.")
        if not self.title or len(self.title) > 140:
            frappe.throw("Invalid notification delivery title.")
        if not self.body or len(self.body) > 500:
            frappe.throw("Invalid notification delivery body.")
        if not self.idempotency_key or len(self.idempotency_key) > 200:
            frappe.throw("Invalid notification delivery idempotency key.")
        if (self.priority or "") not in VALID_PRIORITIES:
            frappe.throw("Invalid notification delivery priority.")
        if (self.android_notification_priority or "") not in VALID_ANDROID_NOTIFICATION_PRIORITIES:
            frappe.throw("Invalid Android notification priority.")
        if self.android_channel_id and len(self.android_channel_id) > 100:
            frappe.throw("Invalid Android notification channel.")
        if self.ttl_seconds is not None and not (0 <= int(self.ttl_seconds) <= 86400):
            frappe.throw("Invalid notification TTL.")

        self.attempt_count = max(0, int(self.attempt_count or 0))
        self.max_attempts = int(self.max_attempts or 3)
        if not (1 <= self.max_attempts <= 10):
            frappe.throw("Invalid notification delivery max attempts.")
        for fieldname in ("success_count", "failure_count", "inactive_count", "token_count"):
            setattr(self, fieldname, max(0, int(getattr(self, fieldname, 0) or 0)))
        if self.last_error:
            self.last_error = str(self.last_error)[:MAX_ERROR_LENGTH]

        raw_payload = str(self.payload_json or "{}").strip() or "{}"
        try:
            payload = json.loads(raw_payload)
        except Exception:
            frappe.throw("Invalid notification delivery payload.")
        if not isinstance(payload, dict):
            frappe.throw("Notification delivery payload must be an object.")
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")
        if len(encoded) > MAX_PAYLOAD_BYTES:
            frappe.throw("Notification delivery payload is too large.")
        self.payload_json = json.dumps(payload, ensure_ascii=False, default=str)

        if self.delivery_kind == "persistent":
            if not self.notification:
                frappe.throw("Persistent notification delivery requires a notification.")
        elif self.notification:
            frappe.throw("Transient notification delivery cannot reference an inbox notification.")

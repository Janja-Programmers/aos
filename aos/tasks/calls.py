"""
Call background tasks.

Handles:
- missed call detection
"""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime, add_to_date

from aos.api.calls.utils import upsert_call_system_message
from aos.api.calls.realtime import publish_call_not_answered
from aos.services.notification_service import NotificationService


# CONSTANTS
MISSED_CALL_TIMEOUT_SECONDS = 30
BATCH_SIZE = 100


# TASK: HANDLE MISSED CALLS
def handle_missed_calls():
    """
    Mark calls as missed if not answered within timeout.
    """
    try:
        now = now_datetime()
        cutoff = add_to_date(now, seconds=-MISSED_CALL_TIMEOUT_SECONDS)

        while True:
            calls = frappe.get_all(
                "AOS Call",
                filters={
                    "status": ["in", ["initiated", "ringing"]],
                    "is_active": 1,
                    "creation": ["<", cutoff],
                },
                fields=["name", "conversation", "caller", "receiver"],
                limit=BATCH_SIZE,
            )

            if not calls:
                break

            for c in calls:
                # Update call
                frappe.db.set_value(
                    "AOS Call",
                    c.name,
                    {
                        "status": "missed",
                        "ended_at": now,
                        "is_active": 0,
                    },
                    update_modified=False,
                )

                # Insert system message
                upsert_call_system_message(
                    call_id=c.name,
                    conversation_id=c.conversation,
                    content="📞 Missed call",
                )

                # Realtime event
                publish_call_not_answered(c)

                # Notify receiver
                NotificationService.notify_missed_call(
                    user=c.receiver,
                    caller=c.caller,
                    call_id=c.name,
                )

        frappe.db.commit()

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Handle Missed Calls Failed",
        )

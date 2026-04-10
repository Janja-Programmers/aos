"""
Call background tasks.

Handles:
- missed call detection
"""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime, add_to_date

from aos.api.calls.utils import upsert_call_system_message


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
                fields=["name", "conversation"],
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

        frappe.db.commit()

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Handle Missed Calls Failed",
        )

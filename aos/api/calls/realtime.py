"""
Realtime events for calls.
"""

from __future__ import annotations

import frappe


# EVENTS
def publish_incoming_call(call, receiver: str):
    frappe.publish_realtime(
        event="aos_incoming_call",
        message={
            "call_id": call.name,
            "conversation_id": call.conversation,
            "caller": call.caller,
            "call_type": call.call_type,
            "room_name": call.room_name,
        },
        user=receiver,
    )


def publish_call_accepted(call):
    frappe.publish_realtime(
        event="aos_call_accepted",
        message={
            "call_id": call.name,
            "room_name": call.room_name,
        },
        user=call.caller,
    )


def publish_call_rejected(call):
    frappe.publish_realtime(
        event="aos_call_rejected",
        message={
            "call_id": call.name,
        },
        user=call.caller,
    )


def publish_call_ended(call):
    users = [call.caller, call.receiver]

    for user in users:
        frappe.publish_realtime(
            event="aos_call_ended",
            message={
                "call_id": call.name,
            },
            user=user,
        )


def publish_call_not_answered(call):
    users = [call.caller, call.receiver]

    for user in users:
        frappe.publish_realtime(
            event="aos_call_not_answered",
            message={
                "call_id": call.name,
            },
            user=user,
        )

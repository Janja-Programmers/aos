"""
Seller background tasks.

Handles:
- scheduled reconciliation of seller response metrics
- full seller response-metrics rebuild
"""

from __future__ import annotations

import frappe
from frappe.utils import add_days, now_datetime

from aos.services.seller_response_metrics import (
    RESPONSE_METRICS_WINDOW_DAYS,
    enqueue_seller_response_metrics_refresh,
)


def refresh_recent_seller_response_metrics() -> int:
    """
    Queue refreshes for active sellers with recent eligible chat activity.

    Intended to run hourly through hooks.py.

    Returns:
        Number of seller refresh jobs requested.
    """

    try:
        cutoff = add_days(
            now_datetime(),
            -RESPONSE_METRICS_WINDOW_DAYS,
        )

        rows = frappe.db.sql(
            """
            SELECT DISTINCT
                seller.user

            FROM `tabAOS Seller` seller

            INNER JOIN `tabAOS Conversation` conversation
                ON (
                    conversation.participant_1 = seller.user
                    OR conversation.participant_2 = seller.user
                )

            INNER JOIN `tabAOS Message` message
                ON message.conversation = conversation.name

            WHERE
                seller.status = 'Active'
                AND message.creation >= %(cutoff)s
                AND message.message_type IN (
                    'text',
                    'media',
                    'ad',
                    'mixed'
                )
                AND IFNULL(message.deleted_for_everyone, 0) = 0
                AND (
                    message.call_id IS NULL
                    OR message.call_id = ''
                )
            """,
            {
                "cutoff": cutoff,
            },
            as_dict=True,
        )

        queued = 0

        for row in rows:
            if enqueue_seller_response_metrics_refresh(
                row.get("user")
            ):
                queued += 1

        frappe.logger("aos").info(
            "[AOS] Seller response metrics reconciliation: "
            f"queued {queued} refresh jobs."
        )

        return queued

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Refresh Recent Seller Response Metrics Failed",
        )
        return 0


def rebuild_all_seller_response_metrics() -> int:
    """
    Queue response-metric refreshes for every active seller.

    Useful for:
    - initial deployment backfill
    - changes to response metric rules
    - repairing stored seller metrics

    Returns:
        Number of seller refresh jobs requested.
    """

    try:
        sellers = frappe.get_all(
            "AOS Seller",
            filters={
                "status": "Active",
            },
            pluck="user",
        )

        queued = 0

        for user in sellers:
            if enqueue_seller_response_metrics_refresh(user):
                queued += 1

        frappe.logger("aos").info(
            "[AOS] Seller response metrics rebuild: "
            f"queued {queued} refresh jobs."
        )

        return queued

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Rebuild Seller Response Metrics Failed",
        )
        return 0

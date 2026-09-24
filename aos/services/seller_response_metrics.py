"""
Seller response metrics service.

Calculates, stores, formats, and queues response-metric refreshes for
individual sellers.

Scheduled and bulk orchestration belongs in:
    aos/tasks/sellers.py

Version 1 rules:
- Chat messages only.
- Uses the most recent 90 days.
- Excludes system messages.
- Excludes call-generated messages.
- Excludes messages deleted for everyone.
- Consecutive incoming messages before a seller reply form one request.
- Response time starts at the first unanswered incoming message.
- Uses median response time.
- A reply must occur within 7 days to count as a successful response.
- Requires at least 3 successful samples before showing response-time text.
- Queue failures are logged and never allowed to break chat sending.
"""

from __future__ import annotations

from datetime import datetime
from statistics import median
from typing import Any

import frappe
from frappe.utils import add_days, get_datetime, now_datetime


RESPONSE_METRICS_WINDOW_DAYS = 90
MAX_RESPONSE_TIME_DAYS = 7
MAX_RESPONSE_TIME_SECONDS = (
    MAX_RESPONSE_TIME_DAYS
    * 24
    * 60
    * 60
)
MIN_RESPONSE_SAMPLE_SIZE = 3


def calculate_chat_response_metrics(
    user: str,
    *,
    window_days: int = RESPONSE_METRICS_WINDOW_DAYS,
) -> dict:
    """
    Calculate chat response metrics for a user.

    A response request begins when another participant sends the first
    eligible message while no unanswered request is pending.

    Additional incoming messages before the seller replies remain part of
    the same request.

    The request closes when the seller sends the next eligible message.

    A seller reply only counts as a successful response when it occurs
    within MAX_RESPONSE_TIME_SECONDS. Replies after that limit do not enter
    the response-time sample and do not increase the response rate.

    Returns:
        {
            "response_time_seconds": int,
            "response_rate": float,
            "response_sample_size": int,
            "response_requests": int,
        }
    """

    normalized_user = _normalize_required_string(user)

    if not normalized_user:
        return _empty_metrics()

    normalized_window_days = _normalize_window_days(
        window_days
    )

    cutoff = add_days(
        now_datetime(),
        -normalized_window_days,
    )

    messages = _get_eligible_messages(
        user=normalized_user,
        cutoff=cutoff,
    )

    if not messages:
        return _empty_metrics()

    response_samples: list[int] = []
    total_requests = 0
    responded_requests = 0

    current_conversation: str | None = None
    pending_incoming_started_at: datetime | None = None

    for message in messages:
        conversation = message.get("conversation")

        if conversation != current_conversation:
            current_conversation = conversation
            pending_incoming_started_at = None

        sender = message.get("sender")
        created_at = _to_datetime(
            message.get("creation")
        )

        if not sender or not created_at:
            continue

        if sender != normalized_user:
            # Start one unanswered incoming period.
            #
            # Additional incoming messages before the seller replies belong
            # to this same request and do not reset the timer.
            if pending_incoming_started_at is None:
                pending_incoming_started_at = created_at
                total_requests += 1

            continue

        # A seller message is only a possible response when an incoming
        # request is currently awaiting a reply.
        if pending_incoming_started_at is None:
            continue

        response_seconds = int(
            (
                created_at
                - pending_incoming_started_at
            ).total_seconds()
        )

        # Only replies within the allowed response window count as
        # successful responses.
        #
        # Replies after the maximum window close the old pending period,
        # but the request remains unanswered for response-rate purposes.
        if (
            0
            <= response_seconds
            <= MAX_RESPONSE_TIME_SECONDS
        ):
            response_samples.append(
                response_seconds
            )
            responded_requests += 1

        pending_incoming_started_at = None

    response_sample_size = len(
        response_samples
    )

    response_time_seconds = (
        int(median(response_samples))
        if response_samples
        else 0
    )

    response_rate = (
        responded_requests
        / total_requests
        * 100
        if total_requests > 0
        else 0.0
    )

    return {
        "response_time_seconds": response_time_seconds,
        "response_rate": round(
            response_rate,
            2,
        ),
        "response_sample_size": response_sample_size,
        "response_requests": total_requests,
    }


def refresh_seller_response_metrics(
    user: str,
) -> dict:
    """
    Recalculate and persist response metrics for one seller.

    This function is safe to run as a background job.

    If the supplied user does not own an AOS Seller record, no database
    update is performed and an empty result is returned.
    """

    normalized_user = _normalize_required_string(
        user
    )

    if not normalized_user:
        return _empty_metrics()

    seller = frappe.db.get_value(
        "AOS Seller",
        {
            "user": normalized_user,
        },
        "name",
    )

    if not seller:
        return _empty_metrics()

    metrics = calculate_chat_response_metrics(
        normalized_user
    )

    updated_at = now_datetime()

    frappe.db.set_value(
        "AOS Seller",
        seller,
        {
            "chat_response_time_seconds": metrics[
                "response_time_seconds"
            ],
            "chat_response_rate": metrics[
                "response_rate"
            ],
            "chat_response_sample_size": metrics[
                "response_sample_size"
            ],
            "chat_response_requests": metrics[
                "response_requests"
            ],
            "response_metrics_updated_at": updated_at,
        },
        update_modified=False,
    )

    return {
        **metrics,
        "seller": seller,
        "user": normalized_user,
        "response_metrics_updated_at": updated_at,
    }


def enqueue_seller_response_metrics_refresh(
    user: str | None,
) -> bool:
    """
    Queue a response-metrics refresh for one active seller.

    Queue failures are logged and must never break message sending.

    Returns:
        True when the refresh job was queued.
        False when the user is invalid, is not an active seller,
        or the job could not be queued.
    """

    normalized_user = _normalize_required_string(
        user
    )

    if not normalized_user:
        return False

    seller_exists = frappe.db.exists(
        "AOS Seller",
        {
            "user": normalized_user,
            "status": "Active",
        },
    )

    if not seller_exists:
        return False

    try:
        frappe.enqueue(
            "aos.services.seller_response_metrics."
            "refresh_seller_response_metrics",
            queue="short",
            user=normalized_user,
            enqueue_after_commit=True,
            job_id=(
                "seller-response-metrics:"
                f"{normalized_user}"
            ),
        )

        return True

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Queue Seller Response Metrics Failed",
        )
        return False


def enqueue_conversation_response_metrics_refresh(
    *,
    participant_1: str | None,
    participant_2: str | None,
) -> int:
    """
    Queue metric refreshes for seller participants in a conversation.

    Both participants are checked because:
    - a seller sender may have just replied;
    - a seller receiver may have received a new unanswered request.

    Non-sellers and suspended sellers are ignored.

    Returns:
        Number of seller refresh jobs requested.
    """

    users: set[str] = set()

    for user in (
        participant_1,
        participant_2,
    ):
        normalized_user = _normalize_required_string(
            user
        )

        if normalized_user:
            users.add(
                normalized_user
            )

    queued = 0

    for user in users:
        if enqueue_seller_response_metrics_refresh(
            user
        ):
            queued += 1

    return queued


def format_response_time(
    seconds: Any,
    *,
    sample_size: Any = 0,
) -> str | None:
    """
    Convert stored response seconds into public storefront text.

    The label is hidden until at least three successful response samples
    have been recorded.
    """

    normalized_seconds = _to_non_negative_int(
        seconds
    )
    normalized_sample_size = _to_non_negative_int(
        sample_size
    )

    if (
        normalized_seconds <= 0
        or normalized_sample_size
        < MIN_RESPONSE_SAMPLE_SIZE
    ):
        return None

    if normalized_seconds <= 5 * 60:
        return "Responds in a few minutes"

    if normalized_seconds <= 15 * 60:
        return "Responds within 15 minutes"

    if normalized_seconds <= 30 * 60:
        return "Responds within 30 minutes"

    if normalized_seconds <= 60 * 60:
        return "Responds within 1 hour"

    if normalized_seconds <= 2 * 60 * 60:
        return "Responds within 2 hours"

    if normalized_seconds <= 6 * 60 * 60:
        return "Responds within a few hours"

    if normalized_seconds <= 12 * 60 * 60:
        return "Responds within 12 hours"

    if normalized_seconds <= 24 * 60 * 60:
        return "Responds within a day"

    if normalized_seconds <= 48 * 60 * 60:
        return "Responds within 2 days"

    return "Usually responds in a few days"


def format_response_rate(
    response_rate: Any,
    *,
    response_requests: Any = 0,
) -> str | None:
    """
    Convert a response rate into public display text.

    The label is hidden until at least three response requests have been
    observed.
    """

    normalized_requests = _to_non_negative_int(
        response_requests
    )

    if (
        normalized_requests
        < MIN_RESPONSE_SAMPLE_SIZE
    ):
        return None

    normalized_rate = _clamp_percentage(
        response_rate
    )

    return (
        f"{round(normalized_rate)}% response rate"
    )


def _get_eligible_messages(
    *,
    user: str,
    cutoff: datetime,
) -> list[frappe._dict]:
    """
    Fetch eligible chat messages involving the supplied user.

    Ordering by conversation and creation allows the state machine to
    process each conversation chronologically.
    """

    return frappe.db.sql(
        """
        SELECT
            m.name,
            m.conversation,
            m.sender,
            m.message_type,
            m.creation

        FROM `tabAOS Message` m

        INNER JOIN `tabAOS Conversation` c ON c.name = m.conversation
        INNER JOIN `tabAOS Conversation Participant` cp
            ON cp.conversation=c.name AND cp.user=%(user)s AND cp.status='active'

        WHERE
            c.conversation_type = 'direct' 
            AND m.creation >= %(cutoff)s
            AND m.message_type IN (
                'text',
                'media',
                'ad',
                'mixed'
            )
            AND IFNULL(
                m.deleted_for_everyone,
                0
            ) = 0
            AND (
                m.call_id IS NULL
                OR m.call_id = ''
            )

        ORDER BY
            m.conversation ASC,
            m.creation ASC,
            m.name ASC
        """,
        {
            "user": user,
            "cutoff": cutoff,
        },
        as_dict=True,
    )


def _empty_metrics() -> dict:
    """Return the standard empty metric result."""

    return {
        "response_time_seconds": 0,
        "response_rate": 0.0,
        "response_sample_size": 0,
        "response_requests": 0,
    }


def _normalize_required_string(
    value: Any,
) -> str | None:
    """Trim a string and normalize empty values to None."""

    if value is None:
        return None

    normalized = str(value).strip()

    return normalized or None


def _normalize_window_days(
    value: Any,
) -> int:
    """Normalize the response-metric window to a positive number of days."""

    try:
        normalized = int(value)
    except (TypeError, ValueError):
        normalized = (
            RESPONSE_METRICS_WINDOW_DAYS
        )

    if normalized <= 0:
        return RESPONSE_METRICS_WINDOW_DAYS

    return normalized


def _to_datetime(
    value: Any,
) -> datetime | None:
    """Safely normalize a database datetime value."""

    if value is None:
        return None

    try:
        return get_datetime(value)
    except (TypeError, ValueError):
        return None


def _to_non_negative_int(
    value: Any,
) -> int:
    """Safely normalize a value into a non-negative integer."""

    try:
        normalized = int(value or 0)
    except (TypeError, ValueError):
        normalized = 0

    return max(
        normalized,
        0,
    )


def _clamp_percentage(
    value: Any,
) -> float:
    """Normalize a percentage into the inclusive range 0–100."""

    try:
        normalized = float(value or 0)
    except (TypeError, ValueError):
        normalized = 0.0

    return max(
        0.0,
        min(
            normalized,
            100.0,
        ),
    )

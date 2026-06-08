"""
Live Co-Host APIs (implementation).

Handles:
- invite_live_cohost
- request_live_cohost
- respond_live_cohost
- cancel_live_cohost
- activate_live_cohost
- end_live_cohost
- get_live_cohost
- list_live_cohosts

Workflow:
- Host invites an active viewer.
- An active viewer requests to become co-host.
- The receiving party accepts or rejects.
- Accepted workflows reserve a co-host slot.
- The accepted candidate receives a co-host LiveKit token separately.
- The candidate activates co-hosting after connecting successfully.
- The co-host may leave, or the host may remove them.
- Private workflow events are delivered only to involved users.
- Public start/end events are published to the live room.

Status lifecycle:
    pending -> accepted -> active -> ended
    pending -> rejected
    pending -> cancelled
    pending -> expired
    accepted -> cancelled
    accepted -> expired

Privacy:
- Session IDs and LiveKit identities are never published to the live room.
- Public co-host payloads always use include_internal=False.
- Private payloads are sent only to the live host and co-host candidate.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import get_datetime, now_datetime

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import fail, ok
from aos.api.shared.validators import require_id

from .constants import (
    ACTIVATE_LIVE_COHOST_LIMIT_PER_MINUTE_PER_USER,
    CANCEL_LIVE_COHOST_LIMIT_PER_MINUTE_PER_USER,
    END_LIVE_COHOST_LIMIT_PER_MINUTE_PER_USER,
    GET_LIVE_COHOST_LIMIT_PER_MINUTE_PER_IP,
    INVITE_LIVE_COHOST_LIMIT_PER_MINUTE_PER_USER,
    LIST_LIVE_COHOSTS_LIMIT_PER_MINUTE_PER_IP,
    REQUEST_LIVE_COHOST_LIMIT_PER_MINUTE_PER_USER,
    RESPOND_LIVE_COHOST_LIMIT_PER_MINUTE_PER_USER,
)
from .messages import create_live_cohost_message
from .realtime import (
    publish_cohost_accepted,
    publish_cohost_activated,
    publish_cohost_cancelled,
    publish_cohost_ended,
    publish_cohost_invited,
    publish_cohost_rejected,
    publish_cohost_request_received,
    publish_cohost_started,
    publish_live_message_to_users,
)
from .serializers import (
    get_user_display,
    live_cohost_fields,
    serialize_live_cohost,
    serialize_live_cohosts,
)
from .validators import (
    COHOST_REQUEST_TYPE_HOST_INVITE,
    COHOST_REQUEST_TYPE_VIEWER_REQUEST,
    COHOST_STATUS_ACCEPTED,
    COHOST_STATUS_ACTIVE,
    COHOST_STATUS_CANCELLED,
    COHOST_STATUS_ENDED,
    COHOST_STATUS_EXPIRED,
    COHOST_STATUS_PENDING,
    COHOST_STATUS_REJECTED,
    normalize_session_id,
    validate_available_cohost_slot,
    validate_cohost_accepted,
    validate_cohost_active,
    validate_cohost_exists,
    validate_cohost_pending,
    validate_cohost_request_not_expired,
    validate_live_active,
    validate_live_exists,
    validate_no_duplicate_cohost_workflow,
    validate_user_can_activate_cohost,
    validate_user_can_cancel_cohost,
    validate_user_can_end_cohost,
    validate_user_can_respond_to_cohost,
    validate_user_is_cohost_candidate,
    validate_user_is_host,
)


LIVE_COHOST_DOCTYPE = "AOS Live CoHost"

COHOST_MESSAGE_INVITED = "cohost_invited"
COHOST_MESSAGE_REQUEST_SENT = "cohost_request_sent"
COHOST_MESSAGE_REQUEST_ACCEPTED = "cohost_request_accepted"
COHOST_MESSAGE_REQUEST_REJECTED = "cohost_request_rejected"
COHOST_MESSAGE_STARTED = "cohost_started"
COHOST_MESSAGE_ENDED = "cohost_ended"

COHOST_ACTION_ACCEPT = "accept"
COHOST_ACTION_REJECT = "reject"

VALID_RESPONSE_ACTIONS = {
    COHOST_ACTION_ACCEPT,
    COHOST_ACTION_REJECT,
}

VALID_LIST_STATUSES = {
    COHOST_STATUS_PENDING,
    COHOST_STATUS_ACCEPTED,
    COHOST_STATUS_REJECTED,
    COHOST_STATUS_CANCELLED,
    COHOST_STATUS_ACTIVE,
    COHOST_STATUS_ENDED,
    COHOST_STATUS_EXPIRED,
}

CANCELLABLE_STATUSES = {
    COHOST_STATUS_PENDING,
    COHOST_STATUS_ACCEPTED,
}

DEFAULT_LIST_LIMIT = 20
MAX_LIST_LIMIT = 50


# GENERIC HELPERS
def _normalize_text(
    value,
) -> str | None:
    text = str(
        value or ""
    ).strip()

    return text or None


def _normalize_status(
    value,
) -> str | None:
    status = str(
        value or ""
    ).strip().lower()

    return status or None


def _normalize_response_action(
    value,
) -> str:
    return str(
        value or ""
    ).strip().lower()


def _parse_pagination(
    kwargs,
) -> tuple[int, int]:
    limit = int(
        kwargs.get("limit")
        or DEFAULT_LIST_LIMIT
    )

    limit = max(
        1,
        min(
            limit,
            MAX_LIST_LIMIT,
        ),
    )

    start = int(
        kwargs.get("start")
        or 0
    )

    start = max(
        0,
        start,
    )

    return start, limit


def _workflow_users(
    *,
    live,
    cohost,
) -> list[str]:
    """
    Return unique workflow participants.

    The host and candidate may receive internal co-host payloads.
    """
    return list(
        dict.fromkeys(
            user
            for user in [
                live.host_user,
                cohost.user,
            ]
            if user
        )
    )


# DATABASE LOCKING
def _lock_live_row(
    live_id: str,
):
    """
    Lock the live stream for the current transaction.

    This protects the single co-host slot from simultaneous acceptance or
    activation requests.
    """
    frappe.db.sql(
        """
        SELECT name
        FROM `tabAOS Live Stream`
        WHERE name = %s
        FOR UPDATE
        """,
        (live_id,),
    )


def _lock_cohost_row(
    cohost_id: str,
):
    """
    Lock one co-host workflow for the current transaction.
    """
    frappe.db.sql(
        """
        SELECT name
        FROM `tabAOS Live CoHost`
        WHERE name = %s
        FOR UPDATE
        """,
        (cohost_id,),
    )


def _get_cohost_doc(
    cohost_id: str,
):
    return frappe.get_doc(
        LIVE_COHOST_DOCTYPE,
        cohost_id,
    )


# ACCESS HELPERS
def _is_live_host(
    *,
    live,
    user: str,
) -> bool:
    return bool(
        user
        and live.host_user == user
    )


def _is_cohost_candidate(
    *,
    cohost,
    user: str,
) -> bool:
    return bool(
        user
        and cohost.user == user
    )


# EXPIRY HELPERS
def _is_pending_expired(
    cohost,
) -> bool:
    if cohost.status != COHOST_STATUS_PENDING:
        return False

    if not cohost.expires_at:
        return False

    return bool(
        get_datetime(
            cohost.expires_at
        )
        <= now_datetime()
    )


def _mark_cohost_expired(
    cohost,
):
    """
    Mark a stale pending workflow as expired.

    This operation is idempotent.
    """
    if cohost.status == COHOST_STATUS_EXPIRED:
        return

    if cohost.status != COHOST_STATUS_PENDING:
        return

    cohost.status = COHOST_STATUS_EXPIRED
    cohost.is_active = 0

    cohost.save(
        ignore_permissions=True
    )


def _expire_stale_pending_for_user(
    *,
    live_id: str,
    user: str,
):
    """
    Expire stale pending workflows before duplicate validation.
    """
    rows = frappe.get_all(
        LIVE_COHOST_DOCTYPE,
        filters={
            "live_stream": live_id,
            "user": user,
            "status": COHOST_STATUS_PENDING,
            "expires_at": [
                "<=",
                now_datetime(),
            ],
        },
        fields=[
            "name",
        ],
    )

    for row in rows:
        cohost = frappe.get_doc(
            LIVE_COHOST_DOCTYPE,
            row.name,
        )

        _mark_cohost_expired(
            cohost
        )


# RECORD CREATION
def _create_pending_cohost(
    *,
    live,
    user: str,
    session_id: str,
    request_type: str,
    requested_by: str,
):
    cohost = frappe.new_doc(
        LIVE_COHOST_DOCTYPE
    )

    cohost.live_stream = live.name
    cohost.user = user
    cohost.session_id = session_id
    cohost.request_type = request_type
    cohost.status = COHOST_STATUS_PENDING
    cohost.requested_by = requested_by

    cohost.insert(
        ignore_permissions=True
    )

    return cohost


# HOST INVITES VIEWER
def invite_live_cohost_impl(**kwargs):
    host, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=(
            f"aos:live:cohost:invite:"
            f"user:{host}"
        ),
        ttl_seconds=60,
        limit=(
            INVITE_LIVE_COHOST_LIMIT_PER_MINUTE_PER_USER
        ),
        message="Too many co-host invitations.",
    )
    if rl:
        return rl

    live_id, err = require_id(
        kwargs.get("live_id"),
        "live_id",
    )
    if err:
        return err

    target_user, err = require_id(
        kwargs.get("target_user"),
        "target_user",
    )
    if err:
        return err

    session_id = normalize_session_id(
        kwargs.get("session_id")
    )

    try:
        live, err = validate_live_exists(
            live_id
        )
        if err:
            return err

        err = validate_live_active(
            live
        )
        if err:
            return err

        err = validate_user_is_host(
            live,
            host,
        )
        if err:
            return err

        _, err = validate_user_is_cohost_candidate(
            live=live,
            user=target_user,
            session_id=session_id,
        )
        if err:
            return err

        _lock_live_row(
            live_id
        )

        _expire_stale_pending_for_user(
            live_id=live_id,
            user=target_user,
        )

        _, err = validate_no_duplicate_cohost_workflow(
            live_id=live_id,
            user=target_user,
        )
        if err:
            return err

        err = validate_available_cohost_slot(
            live_id=live_id,
        )
        if err:
            return err

        cohost = _create_pending_cohost(
            live=live,
            user=target_user,
            session_id=session_id,
            request_type=(
                COHOST_REQUEST_TYPE_HOST_INVITE
            ),
            requested_by=host,
        )

        internal_payload = serialize_live_cohost(
            cohost,
            include_internal=True,
        )

        host_display = get_user_display(
            host
        )

        display_name = (
            host_display.get(
                "display_name"
            )
            or host
        )

        message = create_live_cohost_message(
            live_id=live_id,
            message_type=(
                COHOST_MESSAGE_INVITED
            ),
            content=(
                f"{display_name} invited you to co-host."
            ),
            user=host,
            target_user=target_user,
            metadata={
                "cohost_id": cohost.name,
                "request_type": (
                    cohost.request_type
                ),
                "status": cohost.status,
                "expires_at": (
                    cohost.expires_at
                ),
            },
            visible_to_host=False,
            visible_to_viewers=False,
            publish=False,
        )

        publish_live_message_to_users(
            users=[
                target_user,
            ],
            live_id=live_id,
            message=message,
        )

        publish_cohost_invited(
            user=target_user,
            live_id=live_id,
            cohost=internal_payload,
        )

        return ok(
            "Co-host invitation sent.",
            data={
                "cohost": internal_payload,
                "message": message,
            },
        )

    except frappe.ValidationError as ex:
        frappe.db.rollback()

        return fail(
            str(ex),
            code="VALIDATION_ERROR",
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "Invite Live CoHost Failed",
        )
        frappe.db.rollback()

        return fail(
            "Failed to invite co-host.",
            code="INTERNAL_ERROR",
        )


# VIEWER REQUESTS CO-HOSTING
def request_live_cohost_impl(**kwargs):
    viewer, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=(
            f"aos:live:cohost:request:"
            f"user:{viewer}"
        ),
        ttl_seconds=60,
        limit=(
            REQUEST_LIVE_COHOST_LIMIT_PER_MINUTE_PER_USER
        ),
        message="Too many co-host requests.",
    )
    if rl:
        return rl

    live_id, err = require_id(
        kwargs.get("live_id"),
        "live_id",
    )
    if err:
        return err

    session_id = normalize_session_id(
        kwargs.get("session_id")
    )

    try:
        live, err = validate_live_exists(
            live_id
        )
        if err:
            return err

        err = validate_live_active(
            live
        )
        if err:
            return err

        _, err = validate_user_is_cohost_candidate(
            live=live,
            user=viewer,
            session_id=session_id,
        )
        if err:
            return err

        _lock_live_row(
            live_id
        )

        _expire_stale_pending_for_user(
            live_id=live_id,
            user=viewer,
        )

        _, err = validate_no_duplicate_cohost_workflow(
            live_id=live_id,
            user=viewer,
        )
        if err:
            return err

        err = validate_available_cohost_slot(
            live_id=live_id,
        )
        if err:
            return err

        cohost = _create_pending_cohost(
            live=live,
            user=viewer,
            session_id=session_id,
            request_type=(
                COHOST_REQUEST_TYPE_VIEWER_REQUEST
            ),
            requested_by=viewer,
        )

        internal_payload = serialize_live_cohost(
            cohost,
            include_internal=True,
        )

        viewer_display = get_user_display(
            viewer
        )

        display_name = (
            viewer_display.get(
                "display_name"
            )
            or viewer
        )

        message = create_live_cohost_message(
            live_id=live_id,
            message_type=(
                COHOST_MESSAGE_REQUEST_SENT
            ),
            content=(
                f"{display_name} requested to co-host."
            ),
            user=viewer,
            target_user=live.host_user,
            metadata={
                "cohost_id": cohost.name,
                "request_type": (
                    cohost.request_type
                ),
                "status": cohost.status,
                "expires_at": (
                    cohost.expires_at
                ),
            },
            visible_to_host=True,
            visible_to_viewers=False,
            publish=False,
        )

        publish_live_message_to_users(
            users=[
                live.host_user,
            ],
            live_id=live_id,
            message=message,
        )

        publish_cohost_request_received(
            host_user=live.host_user,
            live_id=live_id,
            cohost=internal_payload,
        )

        return ok(
            "Co-host request sent.",
            data={
                "cohost": internal_payload,
                "message": message,
            },
        )

    except frappe.ValidationError as ex:
        frappe.db.rollback()

        return fail(
            str(ex),
            code="VALIDATION_ERROR",
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "Request Live CoHost Failed",
        )
        frappe.db.rollback()

        return fail(
            "Failed to request co-host access.",
            code="INTERNAL_ERROR",
        )


# RESPOND TO INVITATION OR REQUEST
def respond_live_cohost_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=(
            f"aos:live:cohost:respond:"
            f"user:{user}"
        ),
        ttl_seconds=60,
        limit=(
            RESPOND_LIVE_COHOST_LIMIT_PER_MINUTE_PER_USER
        ),
        message="Too many co-host responses.",
    )
    if rl:
        return rl

    cohost_id, err = require_id(
        kwargs.get("cohost_id"),
        "cohost_id",
    )
    if err:
        return err

    action = _normalize_response_action(
        kwargs.get("action")
    )

    if action not in VALID_RESPONSE_ACTIONS:
        return fail(
            "action must be accept or reject.",
            code="VALIDATION_ERROR",
        )

    reason = _normalize_text(
        kwargs.get("reason")
    )

    try:
        cohost, err = validate_cohost_exists(
            cohost_id
        )
        if err:
            return err

        live, err = validate_live_exists(
            cohost.live_stream
        )
        if err:
            return err

        err = validate_live_active(
            live
        )
        if err:
            return err

        _lock_live_row(
            live.name
        )

        _lock_cohost_row(
            cohost_id
        )

        cohost = _get_cohost_doc(
            cohost_id
        )

        if _is_pending_expired(
            cohost
        ):
            _mark_cohost_expired(
                cohost
            )

            return fail(
                "Co-host request has expired.",
                code="EXPIRED",
            )

        err = validate_cohost_pending(
            cohost
        )
        if err:
            return err

        err = validate_cohost_request_not_expired(
            cohost
        )
        if err:
            return err

        err = validate_user_can_respond_to_cohost(
            cohost=cohost,
            live=live,
            user=user,
        )
        if err:
            return err

        if action == COHOST_ACTION_ACCEPT:
            err = validate_available_cohost_slot(
                live_id=live.name,
                exclude_cohost_id=cohost.name,
            )
            if err:
                return err

            cohost.status = (
                COHOST_STATUS_ACCEPTED
            )

        else:
            cohost.status = (
                COHOST_STATUS_REJECTED
            )

        response_time = now_datetime()

        cohost.responded_by = user
        cohost.responded_at = response_time
        cohost.response_reason = reason

        if action == COHOST_ACTION_ACCEPT:
            cohost.accepted_at = response_time

        cohost.save(
            ignore_permissions=True
        )

        internal_payload = serialize_live_cohost(
            cohost,
            include_internal=True,
        )

        recipients = _workflow_users(
            live=live,
            cohost=cohost,
        )

        if action == COHOST_ACTION_ACCEPT:
            message_type = (
                COHOST_MESSAGE_REQUEST_ACCEPTED
            )
            content = "Co-host request accepted."
            response_message = (
                "Co-host request accepted."
            )

        else:
            message_type = (
                COHOST_MESSAGE_REQUEST_REJECTED
            )
            content = "Co-host request rejected."
            response_message = (
                "Co-host request rejected."
            )

        message = create_live_cohost_message(
            live_id=live.name,
            message_type=message_type,
            content=content,
            user=user,
            target_user=cohost.user,
            metadata={
                "cohost_id": cohost.name,
                "request_type": (
                    cohost.request_type
                ),
                "status": cohost.status,
                "reason": reason,
            },
            visible_to_host=True,
            visible_to_viewers=False,
            publish=False,
        )

        publish_live_message_to_users(
            users=recipients,
            live_id=live.name,
            message=message,
        )

        if action == COHOST_ACTION_ACCEPT:
            publish_cohost_accepted(
                users=recipients,
                live_id=live.name,
                cohost=internal_payload,
            )

        else:
            publish_cohost_rejected(
                users=recipients,
                live_id=live.name,
                cohost=internal_payload,
            )

        return ok(
            response_message,
            data={
                "cohost": internal_payload,
                "message": message,
            },
        )

    except frappe.ValidationError as ex:
        frappe.db.rollback()

        return fail(
            str(ex),
            code="VALIDATION_ERROR",
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "Respond Live CoHost Failed",
        )
        frappe.db.rollback()

        return fail(
            "Failed to respond to co-host request.",
            code="INTERNAL_ERROR",
        )


# CANCEL INVITATION OR REQUEST
def cancel_live_cohost_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=(
            f"aos:live:cohost:cancel:"
            f"user:{user}"
        ),
        ttl_seconds=60,
        limit=(
            CANCEL_LIVE_COHOST_LIMIT_PER_MINUTE_PER_USER
        ),
        message="Too many cancellation requests.",
    )
    if rl:
        return rl

    cohost_id, err = require_id(
        kwargs.get("cohost_id"),
        "cohost_id",
    )
    if err:
        return err

    reason = _normalize_text(
        kwargs.get("reason")
    )

    try:
        cohost, err = validate_cohost_exists(
            cohost_id
        )
        if err:
            return err

        live, err = validate_live_exists(
            cohost.live_stream
        )
        if err:
            return err

        _lock_live_row(
            live.name
        )

        _lock_cohost_row(
            cohost_id
        )

        cohost = _get_cohost_doc(
            cohost_id
        )

        if cohost.status == COHOST_STATUS_CANCELLED:
            return ok(
                "Co-host workflow already cancelled.",
                data={
                    "cohost": serialize_live_cohost(
                        cohost,
                        include_internal=True,
                    ),
                },
            )

        if cohost.status not in CANCELLABLE_STATUSES:
            return fail(
                "This co-host workflow cannot be cancelled.",
                code="INVALID_STATE",
            )

        err = validate_user_can_cancel_cohost(
            cohost=cohost,
            live=live,
            user=user,
        )
        if err:
            return err

        cohost.status = (
            COHOST_STATUS_CANCELLED
        )
        cohost.responded_by = user
        cohost.responded_at = now_datetime()
        cohost.response_reason = reason

        cohost.save(
            ignore_permissions=True
        )

        internal_payload = serialize_live_cohost(
            cohost,
            include_internal=True,
        )

        publish_cohost_cancelled(
            users=_workflow_users(
                live=live,
                cohost=cohost,
            ),
            live_id=live.name,
            cohost=internal_payload,
        )

        return ok(
            "Co-host workflow cancelled.",
            data={
                "cohost": internal_payload,
            },
        )

    except frappe.ValidationError as ex:
        frappe.db.rollback()

        return fail(
            str(ex),
            code="VALIDATION_ERROR",
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "Cancel Live CoHost Failed",
        )
        frappe.db.rollback()

        return fail(
            "Failed to cancel co-host workflow.",
            code="INTERNAL_ERROR",
        )


# ACTIVATE ACCEPTED CO-HOST
def activate_live_cohost_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=(
            f"aos:live:cohost:activate:"
            f"user:{user}"
        ),
        ttl_seconds=60,
        limit=(
            ACTIVATE_LIVE_COHOST_LIMIT_PER_MINUTE_PER_USER
        ),
        message="Too many co-host activation attempts.",
    )
    if rl:
        return rl

    cohost_id, err = require_id(
        kwargs.get("cohost_id"),
        "cohost_id",
    )
    if err:
        return err

    session_id = normalize_session_id(
        kwargs.get("session_id")
    )

    try:
        cohost, err = validate_cohost_exists(
            cohost_id
        )
        if err:
            return err

        live, err = validate_live_exists(
            cohost.live_stream
        )
        if err:
            return err

        err = validate_live_active(
            live
        )
        if err:
            return err

        _lock_live_row(
            live.name
        )

        _lock_cohost_row(
            cohost_id
        )

        cohost = _get_cohost_doc(
            cohost_id
        )

        if (
            cohost.status == COHOST_STATUS_ACTIVE
            and bool(cohost.is_active)
        ):
            return ok(
                "Co-host session already active.",
                data={
                    "cohost": serialize_live_cohost(
                        cohost,
                        include_internal=True,
                    ),
                },
            )

        err = validate_cohost_accepted(
            cohost
        )
        if err:
            return err

        err = validate_user_can_activate_cohost(
            cohost=cohost,
            user=user,
        )
        if err:
            return err

        if not session_id:
            return fail(
                "session_id is required.",
                code="VALIDATION_ERROR",
            )

        if cohost.session_id != session_id:
            return fail(
                "Invalid co-host session.",
                code="PERMISSION_DENIED",
            )

        _, err = validate_user_is_cohost_candidate(
            live=live,
            user=user,
            session_id=session_id,
        )
        if err:
            return err

        err = validate_available_cohost_slot(
            live_id=live.name,
            exclude_cohost_id=cohost.name,
        )
        if err:
            return err

        cohost.status = COHOST_STATUS_ACTIVE
        cohost.started_at = now_datetime()
        cohost.is_active = 1

        cohost.save(
            ignore_permissions=True
        )

        private_payload = serialize_live_cohost(
            cohost,
            include_internal=True,
        )

        public_payload = serialize_live_cohost(
            cohost,
            include_internal=False,
        )

        display = get_user_display(
            cohost.user
        )

        display_name = (
            display.get(
                "display_name"
            )
            or cohost.user
        )

        public_message = create_live_cohost_message(
            live_id=live.name,
            message_type=(
                COHOST_MESSAGE_STARTED
            ),
            content=(
                f"{display_name} joined as co-host."
            ),
            user=cohost.user,
            metadata={
                "cohost_id": cohost.name,
                "status": cohost.status,
            },
            visible_to_host=True,
            visible_to_viewers=True,
            publish=True,
        )

        publish_cohost_started(
            live_id=live.name,
            cohost=public_payload,
        )

        publish_cohost_activated(
            users=_workflow_users(
                live=live,
                cohost=cohost,
            ),
            live_id=live.name,
            cohost=private_payload,
        )

        return ok(
            "Co-host session activated.",
            data={
                "cohost": private_payload,
                "message": public_message,
            },
        )

    except frappe.ValidationError as ex:
        frappe.db.rollback()

        return fail(
            str(ex),
            code="VALIDATION_ERROR",
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "Activate Live CoHost Failed",
        )
        frappe.db.rollback()

        return fail(
            "Failed to activate co-host session.",
            code="INTERNAL_ERROR",
        )


# END ACTIVE CO-HOST
def end_live_cohost_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=(
            f"aos:live:cohost:end:"
            f"user:{user}"
        ),
        ttl_seconds=60,
        limit=(
            END_LIVE_COHOST_LIMIT_PER_MINUTE_PER_USER
        ),
        message="Too many co-host end requests.",
    )
    if rl:
        return rl

    cohost_id, err = require_id(
        kwargs.get("cohost_id"),
        "cohost_id",
    )
    if err:
        return err

    requested_reason = _normalize_text(
        kwargs.get("reason")
    )

    try:
        cohost, err = validate_cohost_exists(
            cohost_id
        )
        if err:
            return err

        live, err = validate_live_exists(
            cohost.live_stream
        )
        if err:
            return err

        _lock_live_row(
            live.name
        )

        _lock_cohost_row(
            cohost_id
        )

        cohost = _get_cohost_doc(
            cohost_id
        )

        if cohost.status == COHOST_STATUS_ENDED:
            return ok(
                "Co-host session already ended.",
                data={
                    "cohost": serialize_live_cohost(
                        cohost,
                        include_internal=True,
                    ),
                },
            )

        err = validate_cohost_active(
            cohost
        )
        if err:
            return err

        err = validate_user_can_end_cohost(
            cohost=cohost,
            live=live,
            user=user,
        )
        if err:
            return err

        if user == cohost.user:
            end_reason = "left"
        else:
            end_reason = "removed_by_host"

        cohost.status = COHOST_STATUS_ENDED
        cohost.is_active = 0
        cohost.ended_at = now_datetime()
        cohost.ended_by = user
        cohost.end_reason = end_reason
        cohost.response_reason = (
            requested_reason
        )

        cohost.save(
            ignore_permissions=True
        )

        private_payload = serialize_live_cohost(
            cohost,
            include_internal=True,
        )

        public_payload = serialize_live_cohost(
            cohost,
            include_internal=False,
        )

        display = get_user_display(
            cohost.user
        )

        display_name = (
            display.get(
                "display_name"
            )
            or cohost.user
        )

        public_message = create_live_cohost_message(
            live_id=live.name,
            message_type=(
                COHOST_MESSAGE_ENDED
            ),
            content=(
                f"{display_name} is no longer co-hosting."
            ),
            user=user,
            target_user=cohost.user,
            metadata={
                "cohost_id": cohost.name,
                "status": cohost.status,
                "end_reason": end_reason,
                "reason": requested_reason,
            },
            visible_to_host=True,
            visible_to_viewers=True,
            publish=True,
        )

        publish_cohost_ended(
            live_id=live.name,
            cohost=public_payload,
            private_users=_workflow_users(
                live=live,
                cohost=cohost,
            ),
            private_cohost=private_payload,
        )

        return ok(
            "Co-host session ended.",
            data={
                "cohost": private_payload,
                "message": public_message,
            },
        )

    except frappe.ValidationError as ex:
        frappe.db.rollback()

        return fail(
            str(ex),
            code="VALIDATION_ERROR",
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "End Live CoHost Failed",
        )
        frappe.db.rollback()

        return fail(
            "Failed to end co-host session.",
            code="INTERNAL_ERROR",
        )


# GET ONE CO-HOST WORKFLOW
def get_live_cohost_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=(
            f"aos:live:cohost:get:"
            f"ip:{request_ip()}"
        ),
        ttl_seconds=60,
        limit=(
            GET_LIVE_COHOST_LIMIT_PER_MINUTE_PER_IP
        ),
        message="Too many requests.",
    )
    if rl:
        return rl

    cohost_id, err = require_id(
        kwargs.get("cohost_id"),
        "cohost_id",
    )
    if err:
        return err

    try:
        cohost, err = validate_cohost_exists(
            cohost_id
        )
        if err:
            return err

        live, err = validate_live_exists(
            cohost.live_stream
        )
        if err:
            return err

        is_authorized = bool(
            _is_live_host(
                live=live,
                user=user,
            )
            or _is_cohost_candidate(
                cohost=cohost,
                user=user,
            )
        )

        if not is_authorized:
            return fail(
                "You are not allowed to view this co-host workflow.",
                code="PERMISSION_DENIED",
            )

        if _is_pending_expired(
            cohost
        ):
            _mark_cohost_expired(
                cohost
            )

        return ok(
            "Co-host workflow fetched.",
            data={
                "cohost": serialize_live_cohost(
                    cohost,
                    include_internal=True,
                ),
            },
        )

    except frappe.ValidationError as ex:
        frappe.db.rollback()

        return fail(
            str(ex),
            code="VALIDATION_ERROR",
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "Get Live CoHost Failed",
        )

        return fail(
            "Failed to fetch co-host workflow.",
            code="INTERNAL_ERROR",
        )


# LIST CO-HOST WORKFLOWS
def list_live_cohosts_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=(
            f"aos:live:cohost:list:"
            f"ip:{request_ip()}"
        ),
        ttl_seconds=60,
        limit=(
            LIST_LIVE_COHOSTS_LIMIT_PER_MINUTE_PER_IP
        ),
        message="Too many requests.",
    )
    if rl:
        return rl

    live_id, err = require_id(
        kwargs.get("live_id"),
        "live_id",
    )
    if err:
        return err

    status = _normalize_status(
        kwargs.get("status")
    )

    if (
        status
        and status not in VALID_LIST_STATUSES
    ):
        return fail(
            "Invalid co-host status filter.",
            code="VALIDATION_ERROR",
        )

    try:
        live, err = validate_live_exists(
            live_id
        )
        if err:
            return err

        start, limit = _parse_pagination(
            kwargs
        )

        is_host = _is_live_host(
            live=live,
            user=user,
        )

        filters: dict[str, Any] = {
            "live_stream": live_id,
        }

        if not is_host:
            filters["user"] = user

        if status:
            filters["status"] = status

        rows = frappe.get_all(
            LIVE_COHOST_DOCTYPE,
            filters=filters,
            fields=live_cohost_fields(),
            order_by="creation desc",
            limit_start=start,
            limit_page_length=limit,
        )

        return ok(
            "Co-host workflows fetched.",
            data={
                "items": serialize_live_cohosts(
                    rows,
                    include_internal=True,
                ),
                "pagination": {
                    "start": start,
                    "limit": limit,
                    "count": len(rows),
                    "has_more": (
                        len(rows) == limit
                    ),
                },
            },
        )

    except ValueError:
        return fail(
            "Invalid pagination values.",
            code="VALIDATION_ERROR",
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "List Live CoHosts Failed",
        )

        return fail(
            "Failed to fetch co-host workflows.",
            code="INTERNAL_ERROR",
        )

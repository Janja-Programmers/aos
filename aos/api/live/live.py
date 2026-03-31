"""
Live Stream APIs (implementation).

Handles:
- start_live
- join_live
- end_live
- get_live
- list_live_streams
"""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime

from aos.api.shared.auth import require_login, current_user
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import ok, fail
from aos.api.shared.validators import require_id

from aos.services.livekit_service import LiveKitService

from .constants import (
    START_LIVE_LIMIT_PER_MINUTE_PER_USER,
    JOIN_LIVE_LIMIT_PER_MINUTE_PER_USER,
    END_LIVE_LIMIT_PER_MINUTE_PER_USER,
    GET_LIVE_LIMIT_PER_MINUTE_PER_IP,
    LIST_LIVE_STREAMS_LIMIT_PER_MINUTE_PER_IP,
)

from .validators import (
    validate_live_exists,
    validate_live_active,
    validate_user_is_seller,
    validate_seller_can_go_live,
)

from .realtime import (
    publish_live_started,
    publish_live_ended,
)


# START LIVE
def start_live_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:live:start:user:{user}",
        ttl_seconds=60,
        limit=START_LIVE_LIMIT_PER_MINUTE_PER_USER,
        message="Too many live start attempts.",
    )
    if rl:
        return rl

    title = (kwargs.get("title") or "").strip()
    cover_image = kwargs.get("cover_image")

    if not title:
        return fail("title is required.", code="VALIDATION_ERROR")

    seller, err = validate_seller_can_go_live(user)
    if err:
        return err

    try:
        live = frappe.new_doc("AOS Live Stream")
        live.seller = user
        live.title = title
        live.cover_image = cover_image
        live.status = "live"
        live.insert(ignore_permissions=True)

        token = LiveKitService.generate_live_token(
            user=user,
            room_name=live.room_name,
            role="host",
            metadata=LiveKitService.build_metadata(
                user=user,
                role="host",
            ),
        )

        publish_live_started(live)

        return ok(
            "Live started.",
            data={
                "live_id": live.name,
                "room_name": live.room_name,
                "token": token,
                "ws_url": LiveKitService.get_ws_url(),
            },
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "Start Live Failed")
        frappe.db.rollback()
        return fail("Failed to start live.", code="INTERNAL_ERROR")


# JOIN LIVE
def join_live_impl(**kwargs):
    user = current_user()  # may be guest

    rl = rate_limit(
        key=f"aos:live:join:user:{user or request_ip()}",
        ttl_seconds=60,
        limit=JOIN_LIVE_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests.",
    )
    if rl:
        return rl

    live_id, err = require_id(kwargs.get("live_id"), "live_id")
    if err:
        return err

    try:
        live, err = validate_live_exists(live_id)
        if err:
            return err

        err = validate_live_active(live)
        if err:
            return err

        role = "host" if live.seller == user else "viewer"

        token = LiveKitService.generate_live_token(
            user=user or request_ip(),
            room_name=live.room_name,
            role=role,
            metadata=LiveKitService.build_metadata(
                user=user,
                role=role,
            ),
        )

        return ok(
            "Joined live.",
            data={
                "live_id": live.name,
                "room_name": live.room_name,
                "token": token,
                "ws_url": LiveKitService.get_ws_url(),
                "role": role,
            },
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "Join Live Failed")
        frappe.db.rollback()
        return fail("Failed to join live.", code="INTERNAL_ERROR")


# END LIVE
def end_live_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:live:end:user:{user}",
        ttl_seconds=60,
        limit=END_LIVE_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests.",
    )
    if rl:
        return rl

    live_id, err = require_id(kwargs.get("live_id"), "live_id")
    if err:
        return err

    try:
        live, err = validate_live_exists(live_id)
        if err:
            return err

        err = validate_user_is_seller(live, user)
        if err:
            return err

        now = now_datetime()

        frappe.db.set_value(
            "AOS Live Stream",
            live_id,
            {
                "status": "ended",
                "ended_at": now,
                "is_active": 0,
            },
            update_modified=False,
        )

        publish_live_ended(live)

        return ok("Live ended.")

    except Exception:
        frappe.log_error(frappe.get_traceback(), "End Live Failed")
        frappe.db.rollback()
        return fail("Failed to end live.", code="INTERNAL_ERROR")


# GET LIVE
def get_live_impl(**kwargs):
    ip = request_ip()

    rl = rate_limit(
        key=f"aos:live:get:ip:{ip}",
        ttl_seconds=60,
        limit=GET_LIVE_LIMIT_PER_MINUTE_PER_IP,
        message="Too many requests.",
    )
    if rl:
        return rl

    live_id, err = require_id(kwargs.get("live_id"), "live_id")
    if err:
        return err

    try:
        live = frappe.db.get_value(
            "AOS Live Stream",
            live_id,
            [
                "name",
                "title",
                "seller",
                "status",
                "viewer_count",
                "total_views",
                "like_count",
                "reaction_count",
                "comment_count",
                "cover_image",
                "room_name",
            ],
            as_dict=True,
        )

        if not live:
            return fail("Live not found.", code="NOT_FOUND")

        return ok("Live fetched.", data=live)

    except Exception:
        frappe.log_error(frappe.get_traceback(), "Get Live Failed")
        return fail("Failed to fetch live.", code="INTERNAL_ERROR")


# LIST LIVE STREAMS
def list_live_streams_impl(**kwargs):
    ip = request_ip()

    rl = rate_limit(
        key=f"aos:live:list:ip:{ip}",
        ttl_seconds=60,
        limit=LIST_LIVE_STREAMS_LIMIT_PER_MINUTE_PER_IP,
        message="Too many requests.",
    )
    if rl:
        return rl

    try:
        lives = frappe.get_all(
            "AOS Live Stream",
            filters={"status": "live"},
            fields=[
                "name",
                "title",
                "seller",
                "viewer_count",
                "cover_image",
            ],
            order_by="creation desc",
            limit_page_length=20,
        )

        return ok(
            "Live streams fetched.",
            data={"items": lives},
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "List Live Failed")
        return fail("Failed to fetch live streams.", code="INTERNAL_ERROR")

from __future__ import annotations

import hashlib
import frappe
from frappe.utils import now_datetime

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok, fail
from aos.api.shared.db import is_duplicate_entry_error

from .constants import (
    REGISTER_PUSH_TOKEN_LIMIT_PER_MINUTE_PER_USER,
    DEACTIVATE_PUSH_TOKEN_LIMIT_PER_MINUTE_PER_USER,
)


VALID_DEVICE_TYPES = {"android", "ios", "web"}


def get_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _active_device_key(*, user: str, device_id: str | None) -> str | None:
    device_id = str(device_id or "").strip()
    if not device_id:
        return None
    return f"{user}|{device_id}"


def _update_push_token(
    *,
    name: str,
    user: str,
    token: str,
    token_hash: str,
    device_type: str,
    device_id: str,
):
    frappe.db.set_value(
        "AOS Push Token",
        name,
        {
            "user": user,
            "token": token,
            "token_hash": token_hash,
            "device_type": device_type,
            "device_id": device_id,
            "is_active": 1,
            "active_device_key": _active_device_key(user=user, device_id=device_id),
            "last_used_at": now_datetime(),
        },
        update_modified=False,
    )


def _find_existing_token(
    *,
    token: str,
    token_hash: str,
):
    """
    Token field is unique in DB, so check both token and token_hash.

    token_hash is preferred for lookup, but token protects us from older
    rows or partial legacy data where token_hash may be missing/stale.
    """
    existing = frappe.db.get_value(
        "AOS Push Token",
        {"token_hash": token_hash},
        "name",
    )

    if existing:
        return existing

    return frappe.db.get_value(
        "AOS Push Token",
        {"token": token},
        "name",
    )


def _deactivate_other_tokens_for_device(
    *,
    user: str,
    device_id: str,
    keep_name: str,
):
    if not device_id:
        return

    frappe.db.sql(
        """
        UPDATE `tabAOS Push Token`
        SET
            is_active = 0,
            active_device_key = NULL,
            last_used_at = %s
        WHERE
            user = %s
            AND device_id = %s
            AND name != %s
        """,
        (
            now_datetime(),
            user,
            device_id,
            keep_name,
        ),
    )


# REGISTER TOKEN
def register_push_token_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:push:register:user:{current_user}",
        ttl_seconds=60,
        limit=REGISTER_PUSH_TOKEN_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    token = (kwargs.get("token") or "").strip()
    device_type = (kwargs.get("device_type") or "").strip().lower()
    device_id = (kwargs.get("device_id") or "").strip()

    # VALIDATION
    if not token:
        return fail("token is required.", error="VALIDATION_ERROR")

    if not device_type:
        return fail("device_type is required.", error="VALIDATION_ERROR")

    if device_type not in VALID_DEVICE_TYPES:
        return fail("Invalid device_type.", error="VALIDATION_ERROR")

    try:
        token_hash = get_token_hash(token)

        # Token-level upsert first.
        # This prevents duplicate unique-token insert errors when the same FCM
        # token is re-registered with a new device_id/user session.
        existing_token = _find_existing_token(
            token=token,
            token_hash=token_hash,
        )

        if existing_token:
            _update_push_token(
                name=existing_token,
                user=current_user,
                token=token,
                token_hash=token_hash,
                device_type=device_type,
                device_id=device_id,
            )

            _deactivate_other_tokens_for_device(
                user=current_user,
                device_id=device_id,
                keep_name=existing_token,
            )

            frappe.db.commit()

            return ok(
                "Push token updated.",
                data={"id": existing_token},
            )

        # Device-level upsert.
        # A device may receive a refreshed token. Update the device row instead
        # of creating many active tokens for one device.
        existing_device = None

        if device_id:
            existing_device = frappe.db.get_value(
                "AOS Push Token",
                {
                    "user": current_user,
                    "device_id": device_id,
                },
                "name",
            )

        if existing_device:
            _update_push_token(
                name=existing_device,
                user=current_user,
                token=token,
                token_hash=token_hash,
                device_type=device_type,
                device_id=device_id,
            )

            frappe.db.commit()

            return ok(
                "Push token updated.",
                data={"id": existing_device},
            )

        # Create new token
        doc = frappe.get_doc(
            {
                "doctype": "AOS Push Token",
                "user": current_user,
                "token": token,
                "token_hash": token_hash,
                "device_type": device_type,
                "device_id": device_id,
                "is_active": 1,
                "active_device_key": _active_device_key(user=current_user, device_id=device_id),
                "last_used_at": now_datetime(),
            }
        )

        doc.insert(ignore_permissions=True)
        frappe.db.commit()

        return ok(
            "Push token registered.",
            data={"id": doc.name},
        )

    except Exception as ex:
        if not is_duplicate_entry_error(ex):
            frappe.log_error(
                frappe.get_traceback(),
                f"AOS Register Push Token Failed: {ex}",
            )
            frappe.db.rollback()

            return fail(
                "Failed to register push token.",
                error="INTERNAL_ERROR",
            )

        frappe.db.rollback()

        try:
            token_hash = get_token_hash(token)

            existing_token = _find_existing_token(
                token=token,
                token_hash=token_hash,
            )

            if existing_token:
                _update_push_token(
                    name=existing_token,
                    user=current_user,
                    token=token,
                    token_hash=token_hash,
                    device_type=device_type,
                    device_id=device_id,
                )

                _deactivate_other_tokens_for_device(
                    user=current_user,
                    device_id=device_id,
                    keep_name=existing_token,
                )

                frappe.db.commit()

                return ok(
                    "Push token updated.",
                    data={"id": existing_token},
                )

            existing_device = None
            if device_id:
                existing_device = frappe.db.get_value(
                    "AOS Push Token",
                    {
                        "user": current_user,
                        "device_id": device_id,
                    },
                    "name",
                )

            if existing_device:
                _update_push_token(
                    name=existing_device,
                    user=current_user,
                    token=token,
                    token_hash=token_hash,
                    device_type=device_type,
                    device_id=device_id,
                )

                frappe.db.commit()

                return ok(
                    "Push token updated.",
                    data={"id": existing_device},
                )

        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                "AOS Push Token Duplicate Recovery Failed",
            )

        return fail(
            "Failed to register push token.",
            error="INTERNAL_ERROR",
        )



# DEACTIVATE TOKEN
def deactivate_push_token_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:push:deactivate:user:{current_user}",
        ttl_seconds=60,
        limit=DEACTIVATE_PUSH_TOKEN_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    token = (kwargs.get("token") or "").strip()

    if not token:
        return fail("token is required.", error="VALIDATION_ERROR")

    try:
        token_hash = get_token_hash(token)

        existing = _find_existing_token(
            token=token,
            token_hash=token_hash,
        )

        if not existing:
            return ok("Token not found or already inactive.")

        owner = frappe.db.get_value(
            "AOS Push Token",
            existing,
            "user",
        )

        if owner != current_user:
            return ok("Token not found or already inactive.")

        frappe.db.set_value(
            "AOS Push Token",
            existing,
            {
                "is_active": 0,
                "active_device_key": None,
                "last_used_at": now_datetime(),
            },
            update_modified=False,
        )

        frappe.db.commit()

        return ok("Push token deactivated.")

    except Exception as e:
        frappe.log_error(
            frappe.get_traceback(),
            f"AOS Deactivate Push Token Failed: {e}",
        )
        frappe.db.rollback()

        return fail(
            "Failed to deactivate push token.",
            error="INTERNAL_ERROR",
        )

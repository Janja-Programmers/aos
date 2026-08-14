"""Authenticated push-token registration endpoints."""

from __future__ import annotations

import uuid

import frappe
from frappe.utils import now_datetime

from aos.api.shared.auth import require_login
from aos.api.shared.db import is_duplicate_entry_error
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok
from aos.services.accounts.http import set_private_no_store
from aos.services.accounts.identity import public_account_id_for_user
from aos.services.notifications.devices import (
    PushDeviceValidationError,
    get_token_hash,
    normalize_device_id,
    normalize_device_type,
    normalize_push_token,
    token_fingerprint,
)
from aos.services.notifications.observability import notification_log
from aos.services.notifications.validation import NotificationInputError, reject_unknown_fields

from .constants import (
    DEACTIVATE_PUSH_TOKEN_LIMIT_PER_MINUTE_PER_USER,
    REGISTER_PUSH_TOKEN_LIMIT_PER_MINUTE_PER_USER,
)


def _active_device_key(*, user: str, device_id: str | None) -> str | None:
    device_id = str(device_id or "").strip()
    return f"{user}|{device_id}" if device_id else None


def _rollback_savepoint(savepoint: str) -> None:
    try:
        frappe.db.rollback(save_point=savepoint)
    except Exception:
        pass


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


def _find_existing_token(*, token: str, token_hash: str):
    rows = frappe.db.sql(
        """
        SELECT name
        FROM `tabAOS Push Token`
        WHERE token_hash = %s
        ORDER BY is_active DESC, modified DESC, name DESC
        LIMIT 1
        FOR UPDATE
        """,
        (token_hash,),
        as_dict=True,
    )
    if rows:
        return rows[0].name
    # Compatibility for pre-hash legacy rows. Lock the winner before ownership
    # transfer so concurrent registrations cannot race through a stale read.
    rows = frappe.db.sql(
        """
        SELECT name
        FROM `tabAOS Push Token`
        WHERE token = %s
        ORDER BY is_active DESC, modified DESC, name DESC
        LIMIT 1
        FOR UPDATE
        """,
        (token,),
        as_dict=True,
    )
    return rows[0].name if rows else None


def _find_existing_device(*, user: str, device_id: str) -> str | None:
    if not device_id:
        return None
    rows = frappe.db.sql(
        """
        SELECT name
        FROM `tabAOS Push Token`
        WHERE user = %s AND device_id = %s
        ORDER BY is_active DESC, modified DESC, name DESC
        LIMIT 1
        FOR UPDATE
        """,
        (user, device_id),
        as_dict=True,
    )
    return rows[0].name if rows else None


def _deactivate_other_tokens_for_device(*, device_id: str, keep_name: str):
    """A claimed device belongs to one signed-in account at a time.

    Token strings rotate. Deactivating all other registrations for the same
    modeled device prevents a signed-out/previous account from continuing to
    receive pushes after a different account claims that device.
    """
    if not device_id:
        return
    frappe.db.sql(
        """
        UPDATE `tabAOS Push Token`
        SET is_active = 0, active_device_key = NULL, last_used_at = %s
        WHERE device_id = %s AND name != %s AND is_active = 1
        """,
        (now_datetime(), device_id, keep_name),
    )


def _register_once(*, user: str, token: str, device_type: str, device_id: str):
    token_hash = get_token_hash(token)
    existing_token = _find_existing_token(token=token, token_hash=token_hash)
    if existing_token:
        # Clear any competing active modeled-device row first. The existing
        # token row may be moving across accounts, and updating its
        # active_device_key before this step can hit the uniqueness constraint.
        _deactivate_other_tokens_for_device(device_id=device_id, keep_name=existing_token)
        _update_push_token(
            name=existing_token,
            user=user,
            token=token,
            token_hash=token_hash,
            device_type=device_type,
            device_id=device_id,
        )
        return existing_token, "updated"

    existing_device = _find_existing_device(user=user, device_id=device_id)
    if existing_device:
        _update_push_token(
            name=existing_device,
            user=user,
            token=token,
            token_hash=token_hash,
            device_type=device_type,
            device_id=device_id,
        )
        _deactivate_other_tokens_for_device(device_id=device_id, keep_name=existing_device)
        return existing_device, "updated"

    doc = frappe.get_doc(
        {
            "doctype": "AOS Push Token",
            "user": user,
            "token": token,
            "token_hash": token_hash,
            "device_type": device_type,
            "device_id": device_id,
            "is_active": 1,
            "active_device_key": _active_device_key(user=user, device_id=device_id),
            "last_used_at": now_datetime(),
        }
    )
    doc.insert(ignore_permissions=True)
    _deactivate_other_tokens_for_device(device_id=device_id, keep_name=doc.name)
    return doc.name, "registered"


def register_push_token_impl(**kwargs):
    set_private_no_store()
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
    try:
        reject_unknown_fields(kwargs, allowed={"token", "device_type", "device_id"})
        token = normalize_push_token(kwargs.get("token"))
        device_type = normalize_device_type(kwargs.get("device_type"))
        device_id = normalize_device_id(kwargs.get("device_id"))
    except (NotificationInputError, PushDeviceValidationError):
        return fail("Invalid push token registration request.", error="VALIDATION_ERROR")

    savepoint = f"aos_push_register_{uuid.uuid4().hex[:10]}"
    frappe.db.savepoint(savepoint)
    try:
        name, action = _register_once(
            user=current_user,
            token=token,
            device_type=device_type,
            device_id=device_id,
        )
    except Exception as exc:
        if not is_duplicate_entry_error(exc):
            _rollback_savepoint(savepoint)
            frappe.log_error(frappe.get_traceback(), "AOS Register Push Token Failed")
            notification_log(
                "notification.device_registration_failed",
                account_id=public_account_id_for_user(current_user),
                platform=device_type,
                outcome="failed",
                reason=exc.__class__.__name__,
                token_fingerprint=token_fingerprint(token=token),
            )
            return fail("Failed to register push token.", error="INTERNAL_ERROR")

        # Unique constraints arbitrate concurrent token/device claims. Roll back
        # only this endpoint's work and resolve the winner deterministically.
        _rollback_savepoint(savepoint)
        recovery = f"aos_push_register_recovery_{uuid.uuid4().hex[:10]}"
        frappe.db.savepoint(recovery)
        try:
            name, action = _register_once(
                user=current_user,
                token=token,
                device_type=device_type,
                device_id=device_id,
            )
        except Exception:
            _rollback_savepoint(recovery)
            frappe.log_error(frappe.get_traceback(), "AOS Push Token Duplicate Recovery Failed")
            return fail("Failed to register push token.", error="INTERNAL_ERROR")

    notification_log(
        "notification.device_registered",
        account_id=public_account_id_for_user(current_user),
        platform=device_type,
        outcome=action,
        token_fingerprint=token_fingerprint(token=token),
    )
    return ok(
        "Push token registered." if action == "registered" else "Push token updated.",
        data={"id": name},
    )


def deactivate_push_token_impl(**kwargs):
    set_private_no_store()
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
    try:
        reject_unknown_fields(kwargs, allowed={"token"})
        token = normalize_push_token(kwargs.get("token"))
    except (NotificationInputError, PushDeviceValidationError):
        return fail("Invalid push token deactivation request.", error="VALIDATION_ERROR")

    token_hash = get_token_hash(token)
    savepoint = f"aos_push_deactivate_{uuid.uuid4().hex[:10]}"
    frappe.db.savepoint(savepoint)
    try:
        existing = _find_existing_token(token=token, token_hash=token_hash)
        if not existing:
            return ok("Token not found or already inactive.")
        owner = frappe.db.get_value("AOS Push Token", existing, "user")
        if owner != current_user:
            # Do not reveal whether a provider token belongs to another account.
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
    except Exception as exc:
        _rollback_savepoint(savepoint)
        frappe.log_error(frappe.get_traceback(), "AOS Deactivate Push Token Failed")
        notification_log(
            "notification.device_deactivation_failed",
            account_id=public_account_id_for_user(current_user),
            outcome="failed",
            reason=exc.__class__.__name__,
            token_fingerprint=token_fingerprint(token_hash=token_hash),
        )
        return fail("Failed to deactivate push token.", error="INTERNAL_ERROR")

    notification_log(
        "notification.device_deactivated",
        account_id=public_account_id_for_user(current_user),
        outcome="deactivated",
        token_fingerprint=token_fingerprint(token_hash=token_hash),
    )
    return ok("Push token deactivated.")


# Compatibility export retained for internal imports/tests.
VALID_DEVICE_TYPES = frozenset({"android", "ios", "web"})

"""Authenticated push-token registration endpoints."""

from __future__ import annotations

import time
import uuid

import frappe
from frappe.utils import now_datetime

from aos.api.shared.auth import require_login
from aos.api.shared.db import (
    is_duplicate_entry_error,
    rollback_deadlocked_transaction,
)
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
    normalize_registration_kind,
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
    registration_kind: str,
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
            "registration_kind": registration_kind,
            "is_active": 1,
            "active_device_key": _active_device_key(user=user, device_id=device_id),
            "last_used_at": now_datetime(),
        },
        update_modified=False,
    )


def _lock_push_token_row(name: str):
    rows = frappe.db.sql(
        """
        SELECT name, user, token, token_hash, device_id
        FROM `tabAOS Push Token`
        WHERE name = %s
        LIMIT 1
        FOR UPDATE
        """,
        (name,),
        as_dict=True,
    )
    return rows[0] if rows else None


def _find_existing_token(*, token: str, token_hash: str):
    # Candidate discovery must stay non-locking. Locking an absent secondary
    # key with SELECT ... FOR UPDATE creates InnoDB gap locks; two simultaneous
    # first-time registrations can then deadlock when they both proceed toward
    # an insert. Discover first, then lock only the candidate primary-key row.
    rows = frappe.db.sql(
        """
        SELECT name
        FROM `tabAOS Push Token`
        WHERE token_hash = %s
        ORDER BY is_active DESC, modified DESC, name DESC
        LIMIT 1
        """,
        (token_hash,),
        as_dict=True,
    )
    if rows:
        locked = _lock_push_token_row(rows[0].name)
        if locked and str(locked.token_hash or "").strip().lower() == token_hash:
            return locked.name

    # Compatibility only for genuinely pre-hash legacy rows. ``token`` is a
    # Long Text field and therefore has no useful equality index. A locking
    # scan here used to make every first-time token registration take broad
    # InnoDB row/gap locks, creating avoidable 1213 deadlocks under concurrent
    # app startup. Discover a possible legacy row without locks, then acquire a
    # single primary-key lock and revalidate it before ownership transfer.
    legacy_rows = frappe.db.sql(
        """
        SELECT name
        FROM `tabAOS Push Token`
        WHERE token = %s
          AND COALESCE(token_hash, '') = ''
        ORDER BY is_active DESC, modified DESC, name DESC
        LIMIT 1
        """,
        (token,),
        as_dict=True,
    )
    if not legacy_rows:
        return None

    locked = _lock_push_token_row(legacy_rows[0].name)
    if not locked:
        return None
    locked_token = str(locked.token or "").strip()
    locked_hash = str(locked.token_hash or "").strip().lower()
    if locked_token != token or (locked_hash and locked_hash != token_hash):
        return None
    return locked.name


def _find_existing_device(*, user: str, device_id: str) -> str | None:
    if not device_id:
        return None

    # As with token lookup, avoid a gap lock when the modeled device has no
    # registration yet. The active_device_key unique constraint remains the
    # final arbiter for concurrent inserts; duplicate recovery handles its race.
    rows = frappe.db.sql(
        """
        SELECT name
        FROM `tabAOS Push Token`
        WHERE user = %s AND device_id = %s
        ORDER BY is_active DESC, modified DESC, name DESC
        LIMIT 1
        """,
        (user, device_id),
        as_dict=True,
    )
    if not rows:
        return None

    locked = _lock_push_token_row(rows[0].name)
    if not locked:
        return None
    if str(locked.user or "").strip() != user:
        return None
    if str(locked.device_id or "").strip() != device_id:
        return None
    return locked.name


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


def _register_once(
    *, user: str, token: str, device_type: str, device_id: str, registration_kind: str
):
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
            registration_kind=registration_kind,
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
            registration_kind=registration_kind,
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
            "registration_kind": registration_kind,
            "is_active": 1,
            "active_device_key": _active_device_key(user=user, device_id=device_id),
            "last_used_at": now_datetime(),
        }
    )
    doc.insert(ignore_permissions=True)
    _deactivate_other_tokens_for_device(device_id=device_id, keep_name=doc.name)
    return doc.name, "registered"


_REGISTER_DEADLOCK_ATTEMPTS = 3
_REGISTER_DEADLOCK_BACKOFF_SECONDS = 0.05


def _register_attempt(
    *, user: str, token: str, device_type: str, device_id: str, registration_kind: str
):
    """Run one savepoint-isolated registration with duplicate arbitration."""

    savepoint = f"aos_push_register_{uuid.uuid4().hex[:10]}"
    frappe.db.savepoint(savepoint)
    try:
        return _register_once(
            user=user,
            token=token,
            device_type=device_type,
            device_id=device_id,
            registration_kind=registration_kind,
        )
    except frappe.QueryDeadlockError:
        # MariaDB already aborted the full transaction. A savepoint rollback is
        # no longer meaningful; the bounded outer retry resets DB state.
        raise
    except Exception as exc:
        if not is_duplicate_entry_error(exc):
            _rollback_savepoint(savepoint)
            raise

        # Unique constraints arbitrate concurrent token/device claims. Roll back
        # only this endpoint's work and resolve the winner deterministically.
        _rollback_savepoint(savepoint)
        recovery = f"aos_push_register_recovery_{uuid.uuid4().hex[:10]}"
        frappe.db.savepoint(recovery)
        try:
            return _register_once(
                user=user,
                token=token,
                device_type=device_type,
                device_id=device_id,
                registration_kind=registration_kind,
            )
        except frappe.QueryDeadlockError:
            raise
        except Exception:
            _rollback_savepoint(recovery)
            raise


def _register_with_deadlock_retry(
    *, user: str, token: str, device_type: str, device_id: str, registration_kind: str
):
    """Retry only transient MariaDB deadlock victims, with bounded backoff."""

    for attempt in range(_REGISTER_DEADLOCK_ATTEMPTS):
        try:
            return _register_attempt(
                user=user,
                token=token,
                device_type=device_type,
                device_id=device_id,
                registration_kind=registration_kind,
            )
        except frappe.QueryDeadlockError:
            rollback_deadlocked_transaction()
            if attempt >= _REGISTER_DEADLOCK_ATTEMPTS - 1:
                raise
            time.sleep(_REGISTER_DEADLOCK_BACKOFF_SECONDS * (attempt + 1))

    raise RuntimeError("Push token registration deadlock retry loop exited unexpectedly.")


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
        reject_unknown_fields(
            kwargs, allowed={"token", "device_type", "device_id", "registration_kind"}
        )
        token = normalize_push_token(kwargs.get("token"))
        device_type = normalize_device_type(kwargs.get("device_type"))
        device_id = normalize_device_id(kwargs.get("device_id"))
        registration_kind = normalize_registration_kind(kwargs.get("registration_kind"))
    except (NotificationInputError, PushDeviceValidationError):
        return fail("Invalid push token registration request.", error="VALIDATION_ERROR")

    try:
        name, action = _register_with_deadlock_retry(
            user=current_user,
            token=token,
            device_type=device_type,
            device_id=device_id,
            registration_kind=registration_kind,
        )
    except Exception as exc:
        frappe.log_error(frappe.get_traceback(), "AOS Register Push Token Failed")
        notification_log(
            "notification.device_registration_failed",
            account_id=public_account_id_for_user(current_user),
            platform=device_type,
            registration_kind=registration_kind,
            outcome="failed",
            reason=exc.__class__.__name__,
            token_fingerprint=token_fingerprint(token=token),
        )
        return fail("Failed to register push token.", error="INTERNAL_ERROR")

    notification_log(
        "notification.device_registered",
        account_id=public_account_id_for_user(current_user),
        platform=device_type,
        registration_kind=registration_kind,
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
        reject_unknown_fields(kwargs, allowed={"token", "registration_kind"})
        token = normalize_push_token(kwargs.get("token"))
        registration_kind = normalize_registration_kind(kwargs.get("registration_kind"))
    except (NotificationInputError, PushDeviceValidationError):
        return fail("Invalid push token deactivation request.", error="VALIDATION_ERROR")

    token_hash = get_token_hash(token)
    savepoint = f"aos_push_deactivate_{uuid.uuid4().hex[:10]}"
    frappe.db.savepoint(savepoint)
    try:
        existing = _find_existing_token(token=token, token_hash=token_hash)
        if not existing:
            return ok("Token not found or already inactive.")
        row = frappe.db.get_value(
            "AOS Push Token", existing, ["user", "registration_kind"], as_dict=True
        )
        owner = row.user if row else None
        stored_kind = normalize_registration_kind(row.registration_kind if row else None)
        if owner != current_user or stored_kind != registration_kind:
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
            registration_kind=registration_kind,
        )
        return fail("Failed to deactivate push token.", error="INTERNAL_ERROR")

    notification_log(
        "notification.device_deactivated",
        account_id=public_account_id_for_user(current_user),
        outcome="deactivated",
        token_fingerprint=token_fingerprint(token_hash=token_hash),
        registration_kind=registration_kind,
    )
    return ok("Push token deactivated.")


# Compatibility export retained for internal imports/tests.
VALID_DEVICE_TYPES = frozenset({"android", "ios", "web"})

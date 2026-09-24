"""Public Chat Lock use cases."""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import ok
from aos.services.chat.lock import authorize_locked_conversation, credential_state, remove_secret, set_secret, token_valid, verify_secret
from aos.services.chat.membership import require_active_membership

SET_LOCK_LIMIT_PER_MINUTE = 60
SECRET_CONFIG_LIMIT_PER_MINUTE = 10
SECRET_VERIFY_LIMIT_PER_MINUTE = 20


def _limit(name: str, user: str, limit: int):
    return rate_limit(
        key=rate_limit_key("chat", name, user),
        ttl_seconds=60,
        limit=limit,
        message="Too many Chat Lock requests. Please try again shortly.",
    )


def set_conversation_lock_impl(**kwargs):
    user, err = require_login()
    if err:
        return err
    if limited := _limit("set_conversation_lock", user, SET_LOCK_LIMIT_PER_MINUTE):
        return limited
    conv_id = kwargs.get("conversation_id")
    # A hidden locked chat cannot be unlocked merely by knowing its opaque ID.
    # The same short-lived secret-derived token used to open it is required.
    authorize_locked_conversation(user=user, conversation_id=conv_id, lock_token=kwargs.get("lock_token"))
    _conversation, membership = require_active_membership(conv_id, user, for_update=True)
    locked = bool(int(kwargs.get("locked") or 0))
    frappe.db.set_value(
        "AOS Conversation Participant",
        membership.name,
        {"is_locked": 1 if locked else 0, "lock_changed_at": now_datetime()},
        update_modified=True,
    )
    return ok(
        "Conversation lock updated.",
        data={"conversation_id": conv_id, "is_locked": locked, **credential_state(user)},
    )


def configure_chat_lock_secret_impl(**kwargs):
    user, err = require_login()
    if err:
        return err
    if limited := _limit("configure_chat_lock_secret", user, SECRET_CONFIG_LIMIT_PER_MINUTE):
        return limited
    result = set_secret(
        user=user,
        secret=kwargs.get("secret"),
        current_secret=kwargs.get("current_secret"),
        hide_locked_chats=bool(int(kwargs.get("hide_locked_chats") or 0)),
    )
    return ok("Chat Lock secret configured.", data=result)


def verify_chat_lock_secret_impl(**kwargs):
    user, err = require_login()
    if err:
        return err
    if limited := _limit("verify_chat_lock_secret", user, SECRET_VERIFY_LIMIT_PER_MINUTE):
        return limited
    return ok("Chat Lock unlocked.", data=verify_secret(user=user, secret=kwargs.get("secret")))


def remove_chat_lock_secret_impl(**kwargs):
    user, err = require_login()
    if err:
        return err
    if limited := _limit("remove_chat_lock_secret", user, SECRET_CONFIG_LIMIT_PER_MINUTE):
        return limited
    return ok("Chat Lock secret removed.", data=remove_secret(user=user, current_secret=kwargs.get("current_secret")))


def get_chat_lock_state_impl(**kwargs):
    user, err = require_login()
    if err:
        return err
    state = credential_state(user)
    authorized = not state["hide_locked_chats"] or token_valid(user=user, token=kwargs.get("lock_token"))
    locked_count = None
    if authorized:
        locked_count = int(
            frappe.db.count(
                "AOS Conversation Participant",
                {"user": user, "status": "active", "is_locked": 1},
            )
            or 0
        )
    return ok(
        "Chat Lock state loaded.",
        data={**state, "locked_conversation_count": locked_count, "hidden": bool(state["hide_locked_chats"] and not authorized)},
    )

"""Private, cached, authorization-safe Chat message translation."""
from __future__ import annotations

import hashlib
from typing import Any

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import fail, ok
from aos.integrations.ai.translation_client import (
    TranslationUnavailableError,
    TranslationValidationError,
    translate_text,
)
from aos.services.chat.constants import TRANSLATE_MESSAGE_LIMIT_PER_MINUTE_PER_USER
from aos.services.chat.lock import authorize_locked_conversation
from aos.services.chat.membership import require_active_membership
from aos.services.chat.observability import chat_log, chat_timing

TRANSLATABLE_MESSAGE_TYPES = frozenset({"text", "mixed"})
DEFAULT_SOURCE_REQUEST_KEY = "__default__"


def _hash(content: str) -> str:
    return hashlib.sha256(content.strip().encode("utf-8")).hexdigest()


def _request_language_key(value: str | None, *, default_marker: bool = False) -> str:
    clean = str(value or "").strip().lower().replace("_", "-")
    return clean or (DEFAULT_SOURCE_REQUEST_KEY if default_marker else "")


def _public_id(message_id: str, source: str, target: str, content_hash: str) -> str:
    material = f"{message_id}\x1f{source}\x1f{target}\x1f{content_hash}"
    return "TRN-" + hashlib.sha256(material.encode()).hexdigest()[:24].upper()


def _message(message_id: str):
    return frappe.db.get_value(
        "AOS Message",
        message_id,
        ["name", "conversation", "sender", "content", "message_type", "deleted_for_everyone", "creation"],
        as_dict=True,
    )


def _visible(msg, user: str) -> bool:
    if (
        not msg
        or int(msg.deleted_for_everyone or 0)
        or msg.message_type not in TRANSLATABLE_MESSAGE_TYPES
        or not str(msg.content or "").strip()
    ):
        return False
    _conversation, membership = require_active_membership(msg.conversation, user)
    if msg.creation < membership.visible_from:
        return False
    if membership.cleared_before and msg.creation <= membership.cleared_before:
        return False
    return not bool(
        frappe.db.get_value(
            "AOS Message User State",
            {"message": msg.name, "user": user},
            "hidden_at",
        )
    )


def _cache(message_id: str, request_source: str, request_target: str, content_hash: str):
    return frappe.db.get_value(
        "AOS Message Translation",
        {
            "message": message_id,
            "request_source_language": request_source,
            "request_target_language": request_target,
            "original_content_hash": content_hash,
        },
        [
            "name",
            "message",
            "conversation",
            "source_language",
            "source_language_label",
            "request_source_language",
            "target_language",
            "target_language_label",
            "request_target_language",
            "original_content_hash",
            "translated_content",
            "provider",
            "model_name",
            "creation",
            "modified",
        ],
        as_dict=True,
    )


def _serialize(row, content: str, *, cached: bool, refreshed: bool = False) -> dict[str, Any]:
    return {
        "id": _public_id(row.message, row.source_language, row.target_language, row.original_content_hash),
        "message_id": row.message,
        "conversation_id": row.conversation,
        "source_language": row.source_language,
        "source_language_label": row.source_language_label,
        "target_language": row.target_language,
        "target_language_label": row.target_language_label,
        "original_content": content,
        "original_content_hash": row.original_content_hash,
        "translated_content": row.translated_content,
        "provider": row.provider,
        "model_name": getattr(row, "model_name", None),
        "translated_at": row.modified if refreshed else row.creation,
        "cached": cached,
        "refreshed": refreshed,
    }


def translate_message_impl(**kwargs):
    user, err = require_login()
    if err:
        return err
    limited = rate_limit(
        key=rate_limit_key("chat", "translate_message", user),
        ttl_seconds=60,
        limit=TRANSLATE_MESSAGE_LIMIT_PER_MINUTE_PER_USER,
        message="Too many translation requests. Please slow down.",
    )
    if limited:
        return limited

    message_id = kwargs.get("message_id")
    target = str(kwargs.get("target_language") or "").strip()
    source = str(kwargs.get("source_language") or "").strip() or None
    request_source = _request_language_key(source, default_marker=True)
    request_target = _request_language_key(target)
    message = _message(message_id)
    if not message:
        return fail("Message not found.", error="CHAT_NOT_FOUND", http_status=404)

    authorize_locked_conversation(
        user=user,
        conversation_id=message.conversation,
        lock_token=kwargs.get("lock_token"),
    )
    if not _visible(message, user):
        return fail("Message not found.", error="CHAT_NOT_FOUND", http_status=404)

    content = str(message.content).strip()
    content_hash = _hash(content)
    force = bool(int(kwargs.get("force_refresh") or 0))
    if not force:
        cached = _cache(message_id, request_source, request_target, content_hash)
        if cached:
            return ok("Message translation fetched.", data=_serialize(cached, content, cached=True))

    timing = {"latency_ms": 0}
    try:
        with chat_timing("translate_provider") as timing:
            translated = translate_text(text=content, source_language=source, target_language=target)
    except TranslationValidationError:
        chat_log("translate_provider", outcome="rejected", reason="validation", latency_ms=timing.get("latency_ms", 0))
        return fail("Invalid translation request.", error="CHAT_INVALID_REQUEST", http_status=422)
    except TranslationUnavailableError:
        chat_log("translate_provider", outcome="failure", reason="dependency", latency_ms=timing.get("latency_ms", 0))
        return fail("Translation service is unavailable.", error="TRANSLATION_UNAVAILABLE", http_status=503)

    existing = _cache(message_id, request_source, request_target, content_hash)
    values = {
        "source_language": translated["source_language"],
        "source_language_label": translated.get("source_language_label"),
        "target_language": translated["target_language"],
        "target_language_label": translated.get("target_language_label"),
        "translated_content": translated["translated_content"],
        "provider": translated.get("provider"),
        "model_name": translated.get("model_name"),
        "translated_by": user,
    }
    if existing:
        if force:
            frappe.db.set_value("AOS Message Translation", existing.name, values, update_modified=True)
        fresh = _cache(message_id, request_source, request_target, content_hash)
        return ok(
            "Message translation refreshed." if force else "Message translation fetched.",
            data=_serialize(fresh, content, cached=not force, refreshed=force),
        )

    doc = frappe.new_doc("AOS Message Translation")
    doc.message = message_id
    doc.conversation = message.conversation
    doc.source_language = translated["source_language"]
    doc.source_language_label = translated.get("source_language_label")
    doc.request_source_language = request_source
    doc.target_language = translated["target_language"]
    doc.target_language_label = translated.get("target_language_label")
    doc.request_target_language = request_target
    doc.original_content_hash = content_hash
    doc.translated_content = translated["translated_content"]
    doc.provider = translated.get("provider")
    doc.model_name = translated.get("model_name")
    doc.translated_by = user
    try:
        doc.insert(ignore_permissions=True)
    except frappe.DuplicateEntryError:
        fresh = _cache(message_id, request_source, request_target, content_hash)
        if fresh:
            return ok("Message translation fetched.", data=_serialize(fresh, content, cached=True))
        raise
    return ok("Message translated.", data=_serialize(doc, content, cached=False))

"""
Translate message API (implementation).

Handles:
- translate_message

Rules:
- Translation is on-demand.
- Translation is private to the requesting user.
- Translation does not update AOS Message.content.
- Translation does not affect conversation preview, unread counts, or realtime.
- Cached translations are stored in AOS Message Translation.
- Cache key is message + target_language + original_content_hash.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import ok, fail
from aos.api.shared.public_errors import safe_fail_from_exception
from aos.services.accounts.identity import public_account_id_for_user
from aos.services.chat.observability import chat_log, chat_timing

from aos.integrations.ai.translation_client import (
	TranslationUnavailableError,
	TranslationValidationError,
	translate_text,
)

from .constants import TRANSLATE_MESSAGE_LIMIT_PER_MINUTE_PER_USER
from .message import _is_deleted_for_everyone
from .visibility import get_deleted_for_user_field


TRANSLATABLE_MESSAGE_TYPES = {
	"text",
	"ad",
	"mixed",
}


def _clean_text(value: str | None) -> str:
	return (value or "").strip()


def _hash_content(content: str) -> str:
	return hashlib.sha256(content.strip().encode("utf-8")).hexdigest()


def _public_translation_id(*, message_id: str, target_language: str, content_hash: str) -> str:
	material = "\x1f".join([str(message_id), str(target_language), str(content_hash)])
	digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:24].upper()
	return f"TRN-{digest}"


def _get_message_for_translation(message_id: str):
	"""
	Fetch message together with conversation participants and visibility fields.
	"""

	rows = frappe.db.sql(
		"""
		SELECT
			m.name,
			m.conversation,
			m.sender,
			m.content,
			m.message_type,
			m.ad,
			m.has_attachments,

			m.deleted_for_everyone,
			m.deleted_for_everyone_at,
			m.deleted_for_1,
			m.deleted_for_1_at,
			m.deleted_for_2,
			m.deleted_for_2_at,

			m.creation,

			c.participant_1,
			c.participant_2
		FROM `tabAOS Message` m
		INNER JOIN `tabAOS Conversation` c
			ON c.name = m.conversation
		WHERE m.name = %(message_id)s
		LIMIT 1
		""",
		{"message_id": message_id},
		as_dict=True,
	)

	return rows[0] if rows else None


def _validate_message_can_be_translated(msg, current_user: str):
	if current_user not in (msg.participant_1, msg.participant_2):
		return fail("Not allowed.", error="PERMISSION_DENIED")

	if _is_deleted_for_everyone(msg):
		return fail(
			"Deleted messages cannot be translated.",
			error="VALIDATION_ERROR",
		)

	delete_field = get_deleted_for_user_field(msg, current_user)

	if bool(getattr(msg, delete_field, 0)):
		return fail(
			"You cannot translate a message deleted for you.",
			error="VALIDATION_ERROR",
		)

	if msg.message_type not in TRANSLATABLE_MESSAGE_TYPES:
		return fail(
			"This message type cannot be translated.",
			error="VALIDATION_ERROR",
		)

	content = _clean_text(msg.content)

	if not content:
		return fail(
			"This message has no text to translate.",
			error="VALIDATION_ERROR",
		)

	return None


def _get_cached_translation(
	*,
	message_id: str,
	target_language: str,
	original_content_hash: str,
):
	return frappe.db.get_value(
		"AOS Message Translation",
		{
			"message": message_id,
			"target_language": target_language,
			"original_content_hash": original_content_hash,
		},
		[
			"name",
			"message",
			"conversation",
			"source_language",
			"source_language_label",
			"target_language",
			"target_language_label",
			"original_content_hash",
			"translated_content",
			"provider",
			"model_name",
			"translated_by",
			"creation",
		],
		as_dict=True,
	)


def _serialize_cached_translation(
	row, *, original_content: str, cached: bool = True, refreshed: bool = False
) -> Dict[str, Any]:
	return {
		"id": _public_translation_id(
			message_id=row.message,
			target_language=row.target_language,
			content_hash=row.original_content_hash,
		),
		"message_id": row.message,
		"conversation_id": row.conversation,
		"source_language": row.source_language,
		"source_language_label": row.source_language_label,
		"target_language": row.target_language,
		"target_language_label": row.target_language_label,
		"original_content": original_content,
		"original_content_hash": row.original_content_hash,
		"translated_content": row.translated_content,
		"provider": row.provider,
		"model_name": getattr(row, "model_name", None),
		"translated_by": public_account_id_for_user(row.translated_by) if row.translated_by else None,
		"translated_at": row.creation,
		"cached": bool(cached),
		"refreshed": bool(refreshed),
	}


def _create_translation_cache(
	*,
	message_id: str,
	conversation_id: str,
	current_user: str,
	original_content_hash: str,
	translation: Dict[str, Any],
):
	doc = frappe.new_doc("AOS Message Translation")
	doc.message = message_id
	doc.conversation = conversation_id
	doc.source_language = translation["source_language"]
	doc.source_language_label = translation.get("source_language_label")
	doc.target_language = translation["target_language"]
	doc.target_language_label = translation.get("target_language_label")
	doc.original_content_hash = original_content_hash
	doc.translated_content = translation["translated_content"]
	doc.provider = translation.get("provider")

	if hasattr(doc, "model_name"):
		doc.model_name = translation.get("model_name")

	doc.translated_by = current_user
	doc.insert(ignore_permissions=True)

	return doc


def _serialize_new_translation(
	doc,
	*,
	original_content: str,
) -> Dict[str, Any]:
	return {
		"id": _public_translation_id(
			message_id=doc.message,
			target_language=doc.target_language,
			content_hash=doc.original_content_hash,
		),
		"message_id": doc.message,
		"conversation_id": doc.conversation,
		"source_language": doc.source_language,
		"source_language_label": doc.source_language_label,
		"target_language": doc.target_language,
		"target_language_label": doc.target_language_label,
		"original_content": original_content,
		"original_content_hash": doc.original_content_hash,
		"translated_content": doc.translated_content,
		"provider": doc.provider,
		"model_name": getattr(doc, "model_name", None),
		"translated_by": public_account_id_for_user(doc.translated_by) if doc.translated_by else None,
		"translated_at": doc.creation,
		"cached": False,
		"refreshed": False,
	}


def translate_message_impl(**kwargs):
	current_user, err = require_login()
	if err:
		return err

	rl = rate_limit(
		key=rate_limit_key("chat", "translate_message", current_user),
		ttl_seconds=60,
		limit=TRANSLATE_MESSAGE_LIMIT_PER_MINUTE_PER_USER,
		message="Too many translation requests. Please slow down.",
	)
	if rl:
		return rl

	message_id = kwargs.get("message_id")
	target_language = _clean_text(kwargs.get("target_language"))
	source_language = _clean_text(kwargs.get("source_language"))
	force_refresh = bool(int(kwargs.get("force_refresh") or 0))

	if not message_id:
		return fail("message_id is required.", error="VALIDATION_ERROR")

	if not target_language:
		return fail("target_language is required.", error="VALIDATION_ERROR")

	try:
		msg = _get_message_for_translation(message_id)

		if not msg:
			return fail("Message not found.", error="NOT_FOUND")

		validation_error = _validate_message_can_be_translated(msg, current_user)
		if validation_error:
			return validation_error

		content = _clean_text(msg.content)
		content_hash = _hash_content(content)

		if not force_refresh:
			cached = _get_cached_translation(
				message_id=msg.name,
				target_language=target_language,
				original_content_hash=content_hash,
			)

			if cached:
				return ok(
					"Message translation fetched.",
					data=_serialize_cached_translation(
						cached,
						original_content=content,
					),
				)

		provider_timing = {"latency_ms": 0}
		try:
			with chat_timing("translate_provider") as provider_timing:
				translation = translate_text(
					text=content,
					source_language=source_language or None,
					target_language=target_language,
				)
		except TranslationValidationError:
			chat_log(
				"translate_provider",
				outcome="rejected",
				reason="validation",
				latency_ms=provider_timing.get("latency_ms", 0),
			)
			raise
		except TranslationUnavailableError:
			chat_log(
				"translate_provider",
				outcome="failure",
				reason="dependency",
				latency_ms=provider_timing.get("latency_ms", 0),
			)
			raise
		chat_log(
			"translate_provider",
			outcome="success",
			reason="none",
			latency_ms=provider_timing.get("latency_ms", 0),
		)

		# After provider normalization, cache using the normalized returned target language.
		# This means "sw" and "swh_Latn" share the same cache.
		normalized_target_language = translation["target_language"]

		cached_after_normalization = _get_cached_translation(
			message_id=msg.name,
			target_language=normalized_target_language,
			original_content_hash=content_hash,
		)

		if cached_after_normalization and not force_refresh:
			return ok(
				"Message translation fetched.",
				data=_serialize_cached_translation(
					cached_after_normalization,
					original_content=content,
				),
			)

		if cached_after_normalization and force_refresh:
			updates = {
				"source_language": translation["source_language"],
				"source_language_label": translation.get("source_language_label"),
				"target_language_label": translation.get("target_language_label"),
				"translated_content": translation["translated_content"],
				"provider": translation.get("provider"),
				"translated_by": current_user,
			}
			if frappe.get_meta("AOS Message Translation").has_field("model_name"):
				updates["model_name"] = translation.get("model_name")
			frappe.db.set_value(
				"AOS Message Translation",
				cached_after_normalization.name,
				updates,
				update_modified=True,
			)
			refreshed = _get_cached_translation(
				message_id=msg.name,
				target_language=normalized_target_language,
				original_content_hash=content_hash,
			)
			return ok(
				"Message translation refreshed.",
				data=_serialize_cached_translation(
					refreshed,
					original_content=content,
					cached=False,
					refreshed=True,
				),
			)

		doc = _create_translation_cache(
			message_id=msg.name,
			conversation_id=msg.conversation,
			current_user=current_user,
			original_content_hash=content_hash,
			translation=translation,
		)

		return ok(
			"Message translated.",
			data=_serialize_new_translation(
				doc,
				original_content=content,
			),
		)

	except frappe.DuplicateEntryError:
		# Another request won the unique cache race. Never invoke the external
		# provider a second time from this recovery path.
		msg = _get_message_for_translation(message_id)
		content = _clean_text(msg.content) if msg else ""
		content_hash = _hash_content(content) if content else ""
		normalized_target = locals().get("normalized_target_language") or target_language
		cached = (
			_get_cached_translation(
				message_id=message_id,
				target_language=normalized_target,
				original_content_hash=content_hash,
			)
			if content_hash
			else None
		)
		if cached:
			return ok(
				"Message translation fetched.",
				data=_serialize_cached_translation(cached, original_content=content),
			)
		return fail("Failed to cache translation. Please try again.", error="CONFLICT")

	except TranslationValidationError as ex:
		return safe_fail_from_exception(ex, fallback="Invalid request.", error="VALIDATION_ERROR")

	except TranslationUnavailableError as ex:
		return safe_fail_from_exception(ex, fallback="Translation service is unavailable.", error="TRANSLATION_UNAVAILABLE")

	except frappe.ValidationError as ex:
		return safe_fail_from_exception(ex, fallback="Invalid request.", error="VALIDATION_ERROR")

	except Exception:
		frappe.log_error("Chat operation failed.", "AOS Translate Message Failed")
		return fail("Failed to translate message.", error="INTERNAL_ERROR")

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
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok, fail

from aos.services.translation_service import (
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
		return fail("Not allowed.", code="PERMISSION_DENIED")

	if _is_deleted_for_everyone(msg):
		return fail(
			"Deleted messages cannot be translated.",
			code="VALIDATION_ERROR",
		)

	delete_field = get_deleted_for_user_field(msg, current_user)

	if bool(getattr(msg, delete_field, 0)):
		return fail(
			"You cannot translate a message deleted for you.",
			code="VALIDATION_ERROR",
		)

	if msg.message_type not in TRANSLATABLE_MESSAGE_TYPES:
		return fail(
			"This message type cannot be translated.",
			code="VALIDATION_ERROR",
		)

	content = _clean_text(msg.content)

	if not content:
		return fail(
			"This message has no text to translate.",
			code="VALIDATION_ERROR",
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


def _serialize_cached_translation(row, *, original_content: str) -> Dict[str, Any]:
	return {
		"id": row.name,
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
		"translated_by": row.translated_by,
		"translated_at": row.creation,
		"cached": True,
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
		"id": doc.name,
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
		"translated_by": doc.translated_by,
		"translated_at": doc.creation,
		"cached": False,
	}


def translate_message_impl(**kwargs):
	current_user, err = require_login()
	if err:
		return err

	rl = rate_limit(
		key=f"aos:chat:translate:user:{current_user}",
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
		return fail("message_id is required.", code="VALIDATION_ERROR")

	if not target_language:
		return fail("target_language is required.", code="VALIDATION_ERROR")

	try:
		msg = _get_message_for_translation(message_id)

		if not msg:
			return fail("Message not found.", code="NOT_FOUND")

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

		translation = translate_text(
			text=content,
			source_language=source_language or None,
			target_language=target_language,
		)

		# After provider normalization, cache using the normalized returned target language.
		# This means "sw" and "swh_Latn" share the same cache.
		normalized_target_language = translation["target_language"]

		if not force_refresh:
			cached_after_normalization = _get_cached_translation(
				message_id=msg.name,
				target_language=normalized_target_language,
				original_content_hash=content_hash,
			)

			if cached_after_normalization:
				return ok(
					"Message translation fetched.",
					data=_serialize_cached_translation(
						cached_after_normalization,
						original_content=content,
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
		frappe.db.rollback()

		# Race-safe fallback: another request created the cache first.
		try:
			msg = _get_message_for_translation(message_id)
			content = _clean_text(msg.content) if msg else ""
			content_hash = _hash_content(content)

			translation = translate_text(
				text=content,
				source_language=source_language or None,
				target_language=target_language,
			)

			cached = _get_cached_translation(
				message_id=message_id,
				target_language=translation["target_language"],
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

		except Exception:
			pass

		return fail(
			"Failed to cache translation. Please try again.",
			code="INTERNAL_ERROR",
		)

	except TranslationValidationError as ex:
		return fail(str(ex), code="VALIDATION_ERROR")

	except TranslationUnavailableError as ex:
		return fail(str(ex), code="SERVICE_UNAVAILABLE")

	except frappe.ValidationError as ex:
		frappe.db.rollback()
		return fail(str(ex), code="VALIDATION_ERROR")

	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			"AOS Translate Message Failed",
		)
		frappe.db.rollback()
		return fail("Failed to translate message.", code="INTERNAL_ERROR")

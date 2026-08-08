"""Canonical Chat domain mutations shared by Chat and feature-owned adapters."""

from __future__ import annotations

from typing import Any

from aos.api.chat.message import send_message_for_user

from .errors import ChatError


_ERROR_MAP = {
    # Legacy implementation codes (the canonical service is also called
    # directly by feature-owned adapters, bypassing the public v1 wrapper).
    "NOT_FOUND": ("Chat resource not found.", "CHAT_NOT_FOUND", 404),
    "RESOURCE_NOT_FOUND": ("Chat resource not found.", "CHAT_NOT_FOUND", 404),
    "PERMISSION_DENIED": ("Chat action is not allowed.", "CHAT_ACCESS_DENIED", 403),
    "FORBIDDEN": ("Chat action is not allowed.", "CHAT_ACCESS_DENIED", 403),
    "USER_BLOCKED": ("Chat action is not allowed.", "CHAT_ACCESS_DENIED", 403),
    "ACCOUNT_DISABLED": ("Chat action is not allowed.", "CHAT_ACCESS_DENIED", 403),
    "ACCOUNT_SUSPENDED": ("Chat action is not allowed.", "CHAT_ACCESS_DENIED", 403),
    "ACCOUNT_DEACTIVATED": ("Chat action is not allowed.", "CHAT_ACCESS_DENIED", 403),
    "ACCOUNT_DELETED": ("Chat action is not allowed.", "CHAT_ACCESS_DENIED", 403),
    "ACCOUNT_DELETED_RESTORABLE": ("Chat action is not allowed.", "CHAT_ACCESS_DENIED", 403),
    "VALIDATION_ERROR": ("Invalid Chat request.", "CHAT_INVALID_REQUEST", 422),
    "CONFLICT": ("Chat state changed.", "CHAT_CONFLICT", 409),
    "INVALID_STATE": ("Chat state changed.", "CHAT_INVALID_STATE", 409),
    "RATE_LIMIT": ("Too many Chat requests.", "CHAT_RATE_LIMITED", 429),
    "RATE_LIMITED": ("Too many Chat requests.", "CHAT_RATE_LIMITED", 429),
    "INTERNAL_ERROR": ("Chat operation failed.", "CHAT_INTERNAL_ERROR", 500),
    # Stable Chat codes returned by a hardened implementation path.
    "CHAT_NOT_FOUND": ("Chat resource not found.", "CHAT_NOT_FOUND", 404),
    "CHAT_ACCESS_DENIED": ("Chat action is not allowed.", "CHAT_ACCESS_DENIED", 403),
    "CHAT_INVALID_REQUEST": ("Invalid Chat request.", "CHAT_INVALID_REQUEST", 422),
    "CHAT_UNKNOWN_FIELD": ("Invalid Chat request.", "CHAT_UNKNOWN_FIELD", 422),
    "CHAT_ALIAS_CONFLICT": ("Invalid Chat request.", "CHAT_ALIAS_CONFLICT", 422),
    "CHAT_INVALID_IDENTIFIER": ("Invalid Chat request.", "CHAT_INVALID_IDENTIFIER", 422),
    "CHAT_INPUT_TOO_LARGE": ("Chat request is too large.", "CHAT_INPUT_TOO_LARGE", 413),
    "CHAT_CONFLICT": ("Chat state changed.", "CHAT_CONFLICT", 409),
    "CHAT_INVALID_STATE": ("Chat state changed.", "CHAT_INVALID_STATE", 409),
    "CHAT_RATE_LIMITED": ("Too many Chat requests.", "CHAT_RATE_LIMITED", 429),
    "CHAT_DEPENDENCY_UNAVAILABLE": ("Chat dependency is unavailable.", "CHAT_DEPENDENCY_UNAVAILABLE", 503),
    "CHAT_INTERNAL_ERROR": ("Chat operation failed.", "CHAT_INTERNAL_ERROR", 500),
}


class ChatService:
    """Feature-independent mutation service.

    The established Chat implementation remains the canonical persistence core
    while public endpoint wrappers and feature-owned share endpoints provide
    their own authentication/rate-limit boundaries.
    """

    def send_reference(
        self,
        *,
        sender: str,
        conversation_id: str,
        reference_type: str,
        reference_id: str,
        content: str | None = None,
        idempotency_key: str | None = None,
        require_live_active: bool = False,
    ) -> dict[str, Any]:
        reference_type = str(reference_type or "").strip().lower()
        if reference_type not in {"short", "live", "ad"}:
            raise ChatError("Unsupported shared object.", code="CHAT_INVALID_REQUEST", http_status=422)

        kwargs: dict[str, Any] = {
            "conversation_id": conversation_id,
            "content": content or "",
            reference_type: reference_id,
            "idempotency_key": idempotency_key,
        }
        response = send_message_for_user(
            current_user=sender,
            require_live_active=bool(require_live_active and reference_type == "live"),
            **kwargs,
        )
        if response.get("ok"):
            return dict(response.get("data") or {})

        error = str(response.get("error") or "INTERNAL_ERROR").strip().upper()
        message, code, status = _ERROR_MAP.get(
            error,
            ("Chat operation failed.", "CHAT_INTERNAL_ERROR", 500),
        )
        # Never promote an arbitrary implementation error/message into a
        # public service contract. Stable CHAT_* codes have explicit mappings
        # above; otherwise use the sanitized category fallback.
        raise ChatError(message, code=code, http_status=status, data=response.get("data"))

    def send_live_reference(
        self,
        *,
        sender: str,
        conversation_id: str,
        live_id: str,
        content: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self.send_reference(
            sender=sender,
            conversation_id=conversation_id,
            reference_type="live",
            reference_id=live_id,
            content=content,
            idempotency_key=idempotency_key,
            require_live_active=True,
        )

    def send_short_reference(
        self,
        *,
        sender: str,
        conversation_id: str,
        short_id: str,
        content: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self.send_reference(
            sender=sender,
            conversation_id=conversation_id,
            reference_type="short",
            reference_id=short_id,
            content=content,
            idempotency_key=idempotency_key,
        )

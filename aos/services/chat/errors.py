"""Stable public Chat domain errors."""

from __future__ import annotations

from typing import Any


class ChatError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str = "CHAT_INVALID_REQUEST",
        http_status: int = 422,
        data: dict[str, Any] | None = None,
    ) -> None:
        public_message = str(message or "Invalid Chat request.").strip() or "Invalid Chat request."
        super().__init__(public_message)
        self.public_message = public_message
        self.code = str(code or "CHAT_INVALID_REQUEST").strip().upper()
        self.http_status = int(http_status)
        self.data = dict(data or {})

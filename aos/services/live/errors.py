"""Stable public Live domain errors."""

from __future__ import annotations

from typing import Any


class LiveError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str = "LIVE_INVALID_REQUEST",
        http_status: int = 422,
        data: dict[str, Any] | None = None,
    ) -> None:
        public_message = (
            str(message or "Invalid Live request.").strip()
            or "Invalid Live request."
        )
        super().__init__(public_message)
        # Domain errors are constructed from server-owned messages. Exposing an
        # explicit property prevents API handlers from serializing arbitrary
        # exception text with ``str(exc)``.
        self.public_message = public_message
        self.code = str(code or "LIVE_INVALID_REQUEST").strip().upper()
        self.http_status = int(http_status)
        self.data = dict(data or {})

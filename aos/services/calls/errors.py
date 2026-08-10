"""Stable Calls-domain public errors."""

from __future__ import annotations

from typing import Any


class CallError(Exception):
    def __init__(
        self,
        public_message: str,
        *,
        code: str = "CALL_INVALID_REQUEST",
        http_status: int = 400,
        data: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(public_message)
        self.public_message = public_message
        self.code = code
        self.http_status = int(http_status)
        self.data = data

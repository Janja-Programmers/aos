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
        super().__init__(message)
        self.code = str(code or "LIVE_INVALID_REQUEST").strip().upper()
        self.http_status = int(http_status)
        self.data = dict(data or {})

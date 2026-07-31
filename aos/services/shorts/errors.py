"""Stable Shorts domain errors."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(eq=False)
class ShortsError(RuntimeError):
    message: str
    code: str = "SHORTS_INVALID_REQUEST"
    http_status: int = 422
    data: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__init__(self.message)

    def __str__(self) -> str:
        return self.message


class ShortsNotFoundError(ShortsError):
    def __init__(self, message: str = "Short is unavailable.") -> None:
        super().__init__(message, code="SHORTS_NOT_FOUND", http_status=404)


class ShortsPermissionError(ShortsError):
    def __init__(self, message: str = "Short is unavailable.") -> None:
        super().__init__(message, code="SHORTS_ACCESS_DENIED", http_status=403)


class ShortsConflictError(ShortsError):
    def __init__(self, message: str, *, code: str = "SHORTS_CONFLICT", data: dict[str, Any] | None = None) -> None:
        super().__init__(message, code=code, http_status=409, data=data or {})


class ShortsCursorError(ShortsError):
    def __init__(self, message: str = "Invalid or expired cursor.") -> None:
        super().__init__(message, code="SHORTS_INVALID_CURSOR", http_status=422)

"""Public-safe Maps domain exceptions with stable machine codes."""

from __future__ import annotations


class MapsError(Exception):
    code = "MAPS_ERROR"
    http_status = 400

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        http_status: int | None = None,
        data: dict | None = None,
    ):
        super().__init__(message)
        self.code = str(code or self.code)
        self.http_status = int(http_status or self.http_status)
        self.data = dict(data or {})


class MapsValidationError(MapsError, ValueError):
    code = "INVALID_MAP_REQUEST"
    http_status = 422


class MapsNotFoundError(MapsError, FileNotFoundError):
    code = "MAP_LOCATION_NOT_FOUND"
    http_status = 404


class MapsPermissionError(MapsError, PermissionError):
    code = "MAP_ACCESS_DENIED"
    http_status = 403


class MapsConflictError(MapsError):
    code = "MAP_LOCATION_VERSION_CONFLICT"
    http_status = 409


class MapsDependencyError(MapsError):
    code = "MAP_SERVICE_ERROR"
    http_status = 503

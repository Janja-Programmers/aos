"""Safe Maps API response and transaction boundary."""

from __future__ import annotations

import uuid
from typing import Any, Callable

import frappe

from aos.api.maps.clients.nominatim_client import NominatimClientError
from aos.api.maps.clients.photon_client import PhotonClientError
from aos.api.maps.clients.valhalla_client import ValhallaClientError
from aos.api.shared.responses import fail
from aos.services.sellers.errors import SellerError

from .errors import MapsDependencyError, MapsError

_CALLBACK_MANAGER_NAMES = (
    "before_commit",
    "after_commit",
    "before_rollback",
    "after_rollback",
)


def maps_fail(exc: Exception, *, fallback: str = "Maps request failed.") -> dict[str, Any]:
    if isinstance(exc, MapsError):
        return fail(str(exc), error=exc.code, data=exc.data, http_status=exc.http_status)
    if isinstance(exc, SellerError):
        return fail(str(exc), error=exc.code, data=exc.data, http_status=exc.http_status)
    if isinstance(exc, (NominatimClientError, PhotonClientError, ValhallaClientError)):
        dependency = MapsDependencyError("Map service is temporarily unavailable.")
        return fail(
            str(dependency),
            error=dependency.code,
            data=dependency.data,
            http_status=dependency.http_status,
        )
    return fail(fallback, error="INTERNAL_ERROR", http_status=500)


def run_maps_api(
    operation: Callable[[], dict[str, Any]],
    *,
    fallback: str,
    log_title: str,
    transactional: bool = False,
) -> dict[str, Any]:
    """Run one Maps operation without taking ownership of the outer request transaction."""

    savepoint = f"aos_maps_{uuid.uuid4().hex[:16]}" if transactional else None
    callbacks = _snapshot_callbacks() if transactional else {}
    outbox_flag = _outbox_flag() if transactional else None
    if savepoint:
        frappe.db.savepoint(savepoint)

    def rollback_operation() -> None:
        if not savepoint:
            return
        try:
            frappe.db.rollback(save_point=savepoint)
        except Exception:
            frappe.db.rollback()
        finally:
            _restore_callbacks(callbacks)
            _restore_outbox_flag(outbox_flag)

    try:
        return operation()
    except (MapsError, SellerError, NominatimClientError, PhotonClientError, ValhallaClientError) as exc:
        rollback_operation()
        return maps_fail(exc, fallback=fallback)
    except frappe.DoesNotExistError:
        rollback_operation()
        return fail("Map resource not found.", error="MAP_LOCATION_NOT_FOUND", http_status=404)
    except Exception:
        rollback_operation()
        frappe.log_error(frappe.get_traceback(), log_title)
        return fail(fallback, error="INTERNAL_ERROR", http_status=500)


def _snapshot_callbacks() -> dict[str, list[Any]]:
    snapshot: dict[str, list[Any]] = {}
    for name in _CALLBACK_MANAGER_NAMES:
        manager = getattr(frappe.db, name, None)
        functions = getattr(manager, "_functions", None)
        if isinstance(functions, list):
            snapshot[name] = list(functions)
    return snapshot


def _restore_callbacks(snapshot: dict[str, list[Any]]) -> None:
    for name, functions in snapshot.items():
        manager = getattr(frappe.db, name, None)
        current = getattr(manager, "_functions", None)
        if isinstance(current, list):
            current[:] = functions


def _outbox_flag() -> bool | None:
    flags = getattr(getattr(frappe, "local", None), "flags", None)
    if flags is None:
        return None
    return bool(getattr(flags, "aos_outbox_after_commit_registered", False))


def _restore_outbox_flag(value: bool | None) -> None:
    flags = getattr(getattr(frappe, "local", None), "flags", None)
    if flags is not None and value is not None:
        flags.aos_outbox_after_commit_registered = value

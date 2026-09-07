"""Canonical request-contract helpers for Authentication endpoints."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

import frappe

from aos.api.shared.responses import fail


def reject_unknown_fields(kwargs: dict[str, Any], allowed: Iterable[str]):
    """Reject request aliases and accidental compatibility fields.

    Authentication has one current contract. Unknown keys are never silently
    accepted because that can preserve deprecated frontend behavior unnoticed.
    """
    allowed_set = set(allowed)
    unknown = sorted(str(key) for key in kwargs if key not in allowed_set)
    if not unknown:
        return None
    return fail(
        "Unknown authentication request field.",
        error="AUTH_UNKNOWN_FIELD",
        data={"fields": unknown},
    )


def execute_auth_endpoint(handler: Callable[..., dict[str, Any]], kwargs: dict[str, Any]):
    """Fail closed at the public Authentication boundary without leaking internals.

    Implementations intentionally return stable expected errors themselves. This
    catches only unexpected failures that escaped them, rolls back pending writes,
    logs only secret-safe operation/exception classification, and returns one
    safe availability error.
    """
    # Frappe RPC dispatch injects its method selector into form_dict. It is
    # transport metadata, not part of the Authentication request contract.
    clean_kwargs = dict(kwargs)
    clean_kwargs.pop("cmd", None)
    try:
        return handler(**clean_kwargs)
    except Exception as exc:
        try:
            frappe.db.rollback()
        except Exception:
            # Preserve the original failure; the response still fails closed.
            pass
        from .observability import log_auth_exception

        log_auth_exception(
            f"AOS Authentication Endpoint Failed: {getattr(handler, '__name__', 'unknown')}",
            exc,
            operation=getattr(handler, "__name__", "unknown"),
        )
        return fail(
            "Authentication service temporarily unavailable.",
            error="SERVICE_UNAVAILABLE",
        )

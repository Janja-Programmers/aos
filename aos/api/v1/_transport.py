"""Helpers for separating Frappe transport metadata from client payloads."""

from __future__ import annotations

from typing import Any, Mapping


_FRAPPE_TRANSPORT_FIELDS = frozenset({"cmd"})


def client_kwargs(kwargs: Mapping[str, Any]) -> dict[str, Any]:
    """Return only client-controlled arguments for strict domain validation.

    Frappe resolves whitelisted methods from ``form_dict`` and forwards the
    framework-owned ``cmd`` routing value alongside client fields. Strict API
    validators must still reject genuinely unknown client fields, so only
    known transport metadata is removed at the public v1 boundary.
    """

    return {
        key: value
        for key, value in kwargs.items()
        if key not in _FRAPPE_TRANSPORT_FIELDS
    }

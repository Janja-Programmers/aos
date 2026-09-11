"""Canonical public HTTP transport boundary for AOS API wrappers.

The boundary is version-neutral and intentionally small: it removes only
Frappe-owned RPC metadata before invoking strict domain handlers. Features may
optionally supply an unexpected-exception policy without reimplementing
transport filtering.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, TypeVar


_FRAPPE_TRANSPORT_FIELDS = frozenset({"cmd"})
_Result = TypeVar("_Result")
UnexpectedExceptionHandler = Callable[[Callable[..., Any], Exception], Any]


def client_kwargs(kwargs: Mapping[str, Any]) -> dict[str, Any]:
    """Return only client-controlled arguments for strict domain validation.

    Frappe resolves whitelisted methods from ``form_dict`` and forwards the
    framework-owned ``cmd`` routing value alongside client fields. Unknown
    client fields must remain visible so each domain can reject them according
    to its canonical request contract.
    """

    return {
        key: value
        for key, value in kwargs.items()
        if key not in _FRAPPE_TRANSPORT_FIELDS
    }


def execute_endpoint(
    handler: Callable[..., _Result],
    kwargs: Mapping[str, Any],
    *,
    on_unexpected_exception: UnexpectedExceptionHandler | None = None,
) -> _Result:
    """Invoke a domain handler through the canonical AOS transport boundary.

    Every production-hardened public v1 wrapper should delegate through this
    function. It strips only known Frappe transport metadata, preserving real
    client fields for strict domain validation. Most domains let unexpected
    exceptions propagate to their existing error handling. A domain with a
    deliberate fail-closed policy (Authentication) may provide
    ``on_unexpected_exception`` without creating a second transport boundary.
    """

    clean_kwargs = client_kwargs(kwargs)
    if on_unexpected_exception is None:
        return handler(**clean_kwargs)

    try:
        return handler(**clean_kwargs)
    except Exception as exc:
        return on_unexpected_exception(handler, exc)

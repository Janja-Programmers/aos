"""Canonical internal moderation target contract.

Only features with real hardened automatic moderation adapters belong here.
Adding a value requires an implemented enqueue/apply lifecycle, tests, and Desk
review behavior; this is intentionally not a roadmap/future-feature enum.
"""

from __future__ import annotations

CONTENT_KIND_TARGET_DOCTYPES: dict[str, str] = {
    "ad": "AOS Ad",
    "review": "AOS Review",
    "short": "AOS Short",
}

CONTENT_KINDS = frozenset(CONTENT_KIND_TARGET_DOCTYPES)


def validate_moderation_target(*, content_kind: str, target_doctype: str) -> tuple[str, str]:
    clean_kind = str(content_kind or "").strip().lower()
    clean_doctype = str(target_doctype or "").strip()
    expected = CONTENT_KIND_TARGET_DOCTYPES.get(clean_kind)
    if expected is None:
        raise ValueError("Unsupported moderation content kind")
    if clean_doctype != expected:
        raise ValueError("Moderation content kind does not match target DocType")
    return clean_kind, clean_doctype

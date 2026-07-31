"""Strict public-request and identifier validation for Shorts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from .errors import ShortsError

SHORT_ID_RE = re.compile(r"^SHORT-\d{4}-\d{5}$")
MEDIA_ID_RE = re.compile(r"^MEDIA-\d{4}-\d{5}$")
SOUND_ID_RE = re.compile(r"^SOUND-\d{4}-\d{5}$")
ACCOUNT_ID_RE = re.compile(r"^ACC-[A-Z2-7]{20}$")
CONVERSATION_ID_RE = re.compile(r"^CONV-\d{4}-\d{5}$")


@dataclass(frozen=True)
class EndpointSpec:
    fields: frozenset[str]
    aliases: tuple[tuple[str, ...], ...] = ()
    id_fields: tuple[tuple[str, re.Pattern[str]], ...] = ()


def _normalized(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip()
    return value


def validate_public_kwargs(kwargs: Mapping[str, Any], spec: EndpointSpec) -> dict[str, Any]:
    clean = {key: value for key, value in kwargs.items() if key != "cmd"}
    unknown = sorted(set(clean) - set(spec.fields))
    if unknown:
        raise ShortsError(
            "Unsupported Shorts request field.",
            code="SHORTS_UNKNOWN_FIELD",
            data={"fields": unknown},
        )

    for aliases in spec.aliases:
        supplied = [(name, _normalized(clean.get(name))) for name in aliases if clean.get(name) not in (None, "")]
        if len(supplied) <= 1:
            continue
        values = {str(value) for _, value in supplied}
        if len(values) > 1:
            raise ShortsError(
                "Conflicting Shorts request aliases.",
                code="SHORTS_ALIAS_CONFLICT",
                data={"fields": [name for name, _ in supplied]},
            )

    if clean.get("cursor") not in (None, ""):
        from aos.api.shorts.utils import decode_cursor

        decode_cursor(clean.get("cursor"))

    for field, pattern in spec.id_fields:
        value = clean.get(field)
        if value in (None, ""):
            continue
        if not isinstance(value, str) or not pattern.fullmatch(value.strip()):
            raise ShortsError(
                "Invalid public identifier.",
                code="SHORTS_INVALID_IDENTIFIER",
                data={"field": field},
            )
    return clean


def require_short_id(value: Any, field: str = "short_id") -> str:
    if not isinstance(value, str) or not SHORT_ID_RE.fullmatch(value.strip()):
        raise ShortsError(
            "Invalid Short identifier.",
            code="SHORTS_INVALID_IDENTIFIER",
            data={"field": field},
        )
    return value.strip()

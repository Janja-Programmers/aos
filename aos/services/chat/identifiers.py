"""Canonical opaque identifiers for the Chat domain."""

from __future__ import annotations

import re
from typing import Any

from aos.utils.identifiers import new_prefixed_name

CONVERSATION_ID_RE = re.compile(r"^CONV-[0-9a-f]{32}$")
MESSAGE_ID_RE = re.compile(r"^MSG-[0-9a-f]{32}$")
ATTACHMENT_ID_RE = re.compile(r"^CMA-[0-9a-f]{32}$")
PARTICIPANT_ID_RE = re.compile(r"^CP-[0-9a-f]{32}$")
MESSAGE_STATE_ID_RE = re.compile(r"^MUS-[0-9a-f]{32}$")
LOCK_CREDENTIAL_ID_RE = re.compile(r"^CLC-[0-9a-f]{32}$")


def generate_conversation_id() -> str:
    return new_prefixed_name("CONV")


def generate_message_id() -> str:
    return new_prefixed_name("MSG")


def generate_attachment_id() -> str:
    return new_prefixed_name("CMA")


def generate_participant_id() -> str:
    return new_prefixed_name("CP")


def generate_message_state_id() -> str:
    return new_prefixed_name("MUS")


def generate_lock_credential_id() -> str:
    return new_prefixed_name("CLC")


def normalize_conversation_id(value: Any) -> str:
    candidate = str(value or "").strip().lower()
    if candidate.startswith("conv-"):
        candidate = "CONV-" + candidate[5:]
    return candidate if CONVERSATION_ID_RE.fullmatch(candidate) else ""


def normalize_message_id(value: Any) -> str:
    candidate = str(value or "").strip().lower()
    if candidate.startswith("msg-"):
        candidate = "MSG-" + candidate[4:]
    return candidate if MESSAGE_ID_RE.fullmatch(candidate) else ""

"""Bounded server-only LiveKit room administration shared by Live and Calls."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from aos.services.livekit_service import LiveKitService
from .constants import (
    ADMIN_RETRY_ATTEMPTS,
    ADMIN_TIMEOUT_SECONDS,
    MAX_ADMIN_PARTICIPANTS,
    MAX_ADMIN_ROOM_NAME_LENGTH,
    MAX_PARTICIPANT_IDENTITY_LENGTH,
)


@dataclass(frozen=True)
class RoomAdminResult:
    ok: bool
    category: str
    participants: tuple[str, ...] = ()


def _http_url(value: str) -> str:
    parsed = urlsplit(str(value or "").strip())
    scheme = {"wss": "https", "ws": "http"}.get(parsed.scheme, parsed.scheme)
    if scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError("invalid_livekit_endpoint")
    return urlunsplit((scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _category(exc: BaseException) -> str:
    text = str(exc).lower()
    if "not found" in text or "not_found" in text or "404" in text:
        return "not_found"
    if "already exists" in text or "already_exists" in text or "409" in text:
        return "already_exists"
    if "timeout" in text or isinstance(exc, TimeoutError):
        return "timeout"
    if "unauth" in text or "permission" in text or "403" in text or "401" in text:
        return "authentication"
    return "unavailable"


async def _with_client(operation):
    from livekit import api

    api_key, api_secret = LiveKitService._get_credentials()
    client = api.LiveKitAPI(_http_url(LiveKitService.get_admin_url()), api_key, api_secret)
    try:
        return await operation(client, api)
    finally:
        await client.aclose()


async def _retry(operation) -> RoomAdminResult:
    attempts = max(1, min(int(ADMIN_RETRY_ATTEMPTS), 5))
    timeout = max(1, min(int(ADMIN_TIMEOUT_SECONDS), 30))
    last_category = "unavailable"
    for attempt in range(attempts):
        try:
            return await asyncio.wait_for(_with_client(operation), timeout=timeout)
        except Exception as exc:
            last_category = _category(exc)
            if attempt + 1 < attempts and last_category not in {"authentication"}:
                await asyncio.sleep(min(0.25 * (2**attempt), 2.0))
    return RoomAdminResult(False, last_category)


def _run(operation) -> RoomAdminResult:
    return asyncio.run(_retry(operation))


def ensure_room(room_name: str) -> RoomAdminResult:
    room = str(room_name or "").strip()
    if not room or len(room) > MAX_ADMIN_ROOM_NAME_LENGTH:
        return RoomAdminResult(False, "invalid_room")

    async def operation(client, api):
        try:
            await client.room.create_room(api.CreateRoomRequest(name=room, empty_timeout=300))
            return RoomAdminResult(True, "created")
        except Exception as exc:
            if _category(exc) == "already_exists":
                return RoomAdminResult(True, "already_exists")
            raise

    return _run(operation)


def delete_room(room_name: str) -> RoomAdminResult:
    room = str(room_name or "").strip()
    if not room or len(room) > MAX_ADMIN_ROOM_NAME_LENGTH:
        return RoomAdminResult(False, "invalid_room")

    async def operation(client, api):
        try:
            await client.room.delete_room(api.DeleteRoomRequest(room=room))
            return RoomAdminResult(True, "deleted")
        except Exception as exc:
            if _category(exc) == "not_found":
                return RoomAdminResult(True, "not_found")
            raise

    return _run(operation)


def list_participants(room_name: str) -> RoomAdminResult:
    room = str(room_name or "").strip()
    if not room or len(room) > MAX_ADMIN_ROOM_NAME_LENGTH:
        return RoomAdminResult(False, "invalid_room")

    async def operation(client, api):
        try:
            response = await client.room.list_participants(api.ListParticipantsRequest(room=room))
            identities = tuple(
                sorted(
                    {
                        str(getattr(item, "identity", "") or "").strip()
                        for item in getattr(response, "participants", ())
                        if str(getattr(item, "identity", "") or "").strip()
                    }
                )
            )
            return RoomAdminResult(True, "listed", identities[:MAX_ADMIN_PARTICIPANTS])
        except Exception as exc:
            if _category(exc) == "not_found":
                return RoomAdminResult(True, "not_found", ())
            raise

    return _run(operation)


def remove_participant(room_name: str, identity: str) -> RoomAdminResult:
    room = str(room_name or "").strip()
    participant_identity = str(identity or "").strip()
    if (
        not room
        or len(room) > MAX_ADMIN_ROOM_NAME_LENGTH
        or not participant_identity
        or len(participant_identity) > MAX_PARTICIPANT_IDENTITY_LENGTH
    ):
        return RoomAdminResult(False, "invalid_participant")

    async def operation(client, api):
        try:
            await client.room.remove_participant(
                api.RoomParticipantIdentity(room=room, identity=participant_identity)
            )
            return RoomAdminResult(True, "removed")
        except Exception as exc:
            if _category(exc) == "not_found":
                # The authoritative application state is already closed. A missing
                # participant is therefore an idempotent success.
                return RoomAdminResult(True, "not_found")
            raise

    return _run(operation)

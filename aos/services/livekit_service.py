"""
LiveKit Service for AOS.

Responsibilities:
- Generate secure LiveKit join tokens.
- Centralize LiveKit configuration access.
- Keep API secrets out of cached settings snapshots.
- Keep participant identity stable while allowing display metadata.
- Apply role-based room permissions for calls and live streams.

Live roles:
- host:
    Publish media, subscribe, and publish data.
- cohost:
    Publish media, subscribe, and publish data.
- viewer:
    Subscribe only.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import frappe
from frappe import _
from livekit import api

from aos.utils.aos_config import get_livekit_config
from aos.utils.aos_settings import get_aos_settings_snapshot


LIVE_ROLE_HOST = "host"
LIVE_ROLE_COHOST = "cohost"
LIVE_ROLE_VIEWER = "viewer"

VALID_LIVE_ROLES = {
    LIVE_ROLE_HOST,
    LIVE_ROLE_COHOST,
    LIVE_ROLE_VIEWER,
}

LIVE_ROLE_GRANTS = {
    LIVE_ROLE_HOST: {
        "can_publish": True,
        "can_subscribe": True,
        "can_publish_data": True,
    },
    LIVE_ROLE_COHOST: {
        "can_publish": True,
        "can_subscribe": True,
        "can_publish_data": True,
    },
    LIVE_ROLE_VIEWER: {
        "can_publish": False,
        "can_subscribe": True,
        "can_publish_data": False,
    },
}


class LiveKitService:
    # PUBLIC CONFIGURATION

    @classmethod
    def get_ws_url(cls) -> str:
        """
        Return the configured LiveKit WebSocket endpoint.
        """
        config = get_livekit_config()
        endpoint = str(config.endpoint or "").strip()

        if not endpoint:
            frappe.throw(
                _("LiveKit endpoint is not configured in environment variables.")
            )

        return endpoint

    # CALL TOKENS
    @classmethod
    def generate_call_token(
        cls,
        *,
        user: str,
        room_name: str,
        participant_name: str | None = None,
        metadata: str | None = None,
    ) -> str:
        """
        Generate a token for a call participant.

        Call participants may:
        - publish media
        - subscribe to media
        - publish data
        """
        return cls._generate_token(
            identity=user,
            room_name=room_name,
            participant_name=participant_name,
            metadata=metadata,
            can_publish=True,
            can_subscribe=True,
            can_publish_data=True,
            token_ttl=cls._get_token_ttl(),
        )

    # LIVE TOKENS
    @classmethod
    def generate_live_token(
        cls,
        *,
        user: str,
        room_name: str,
        role: str,
        participant_name: str | None = None,
        metadata: str | None = None,
    ) -> str:
        """
        Generate a role-based token for a live stream.

        Roles:

        host:
        - may publish media
        - may subscribe
        - may publish data

        cohost:
        - may publish media
        - may subscribe
        - may publish data

        viewer:
        - may not publish media
        - may subscribe
        - may not publish data
        """
        normalized_role = cls.normalize_live_role(
            role
        )

        grants = LIVE_ROLE_GRANTS[
            normalized_role
        ]

        return cls._generate_token(
            identity=user,
            room_name=room_name,
            participant_name=participant_name,
            metadata=metadata,
            can_publish=grants[
                "can_publish"
            ],
            can_subscribe=grants[
                "can_subscribe"
            ],
            can_publish_data=grants[
                "can_publish_data"
            ],
            token_ttl=cls._get_live_token_ttl(),
        )

    @classmethod
    def normalize_live_role(
        cls,
        role: str,
    ) -> str:
        """
        Normalize and validate a LiveKit live-stream role.
        """
        normalized_role = str(
            role or ""
        ).strip().lower()

        if normalized_role not in VALID_LIVE_ROLES:
            frappe.throw(
                _("Invalid LiveKit role.")
            )

        return normalized_role

    # PARTICIPANT METADATA
    @classmethod
    def build_metadata(
        cls,
        *,
        user: str,
        role: str,
        conversation: str | None = None,
        call_id: str | None = None,
        call_type: str | None = None,
        display_name: str | None = None,
        avatar: str | None = None,
        is_guest: bool | None = None,
        session_id: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> str:
        """
        Build the JSON metadata stored on the LiveKit participant.

        Identity rules:
        - `user` should match the stable LiveKit participant identity.
        - Authenticated live users normally use their AOS user ID.
        - Guest viewers use a generated session-scoped identity.
        - Display fields are metadata only and must never be treated as
          authentication or participant identity.
        """
        identity = str(
            user or ""
        ).strip()

        normalized_role = str(
            role or ""
        ).strip().lower()

        if not identity:
            frappe.throw(
                _("LiveKit metadata user is required.")
            )

        if not normalized_role:
            frappe.throw(
                _("LiveKit metadata role is required.")
            )

        if (
            extra is not None
            and not isinstance(extra, dict)
        ):
            frappe.throw(
                _("LiveKit metadata extra must be a dictionary.")
            )

        payload: dict[str, Any] = {
            "user": identity,
            "role": normalized_role,
        }

        if conversation:
            payload["conversation"] = (
                conversation
            )

        if call_id:
            payload["call_id"] = call_id

        if call_type:
            payload["call_type"] = (
                call_type
            )

        if display_name:
            payload["display_name"] = (
                display_name
            )

        if avatar:
            payload["avatar"] = avatar

        if is_guest is not None:
            payload["is_guest"] = bool(
                is_guest
            )

        if session_id:
            payload["session_id"] = (
                session_id
            )

        if extra:
            payload.update(
                extra
            )

        return frappe.as_json(
            payload
        )

    # INTERNAL CONFIGURATION
    @classmethod
    def _get_credentials(
        cls,
    ) -> tuple[str, str]:
        """
        Read LiveKit credentials from environment variables.

        Supported:
        - LIVEKIT_API_KEY + LIVEKIT_API_SECRET
        - LIVEKIT_KEYS="api-key:api-secret"
        """
        config = get_livekit_config()

        api_key = str(config.api_key or "").strip()
        api_secret = str(config.api_secret or "").strip()

        if not api_key:
            frappe.throw(
                _("LiveKit API key is not configured in environment variables.")
            )

        if not api_secret:
            frappe.throw(
                _("LiveKit API secret is not configured in environment variables.")
            )

        return api_key, api_secret

    @classmethod
    def _get_token_ttl(
        cls,
    ) -> timedelta:
        """
        Return the configured positive token lifetime.
        """
        settings = get_aos_settings_snapshot()

        try:
            ttl_minutes = int(
                settings.livekit_token_ttl_minutes
                or 5
            )
        except (TypeError, ValueError):
            ttl_minutes = 5

        # Calls tokens are reconnect credentials, not long-lived sessions.
        # Preserve the existing setting but impose a Calls-specific ceiling.
        ttl_minutes = max(1, min(ttl_minutes, 5))

        return timedelta(
            minutes=ttl_minutes
        )

    @classmethod
    def _get_live_token_ttl(cls) -> timedelta:
        """Return a short bounded lifetime for Live publish/view tokens."""
        settings = get_aos_settings_snapshot()
        raw = settings.livekit_live_token_ttl_minutes
        try:
            minutes = int(raw)
        except (TypeError, ValueError):
            minutes = 15
        return timedelta(minutes=max(1, min(minutes, 30)))

    # TOKEN GENERATION
    @classmethod
    def _generate_token(
        cls,
        *,
        identity: str,
        room_name: str,
        participant_name: str | None,
        metadata: str | None,
        can_publish: bool,
        can_subscribe: bool,
        can_publish_data: bool,
        token_ttl: timedelta,
    ) -> str:
        """
        Generate and sign one LiveKit room token.
        """
        normalized_identity = str(
            identity or ""
        ).strip()

        normalized_room_name = str(
            room_name or ""
        ).strip()

        if not normalized_identity:
            frappe.throw(
                _("LiveKit identity is required.")
            )

        if not normalized_room_name:
            frappe.throw(
                _("LiveKit room name is required.")
            )

        api_key, api_secret = (
            cls._get_credentials()
        )

        token = api.AccessToken(
            api_key,
            api_secret,
        ).with_identity(
            normalized_identity
        )

        normalized_participant_name = str(
            participant_name or ""
        ).strip()

        if normalized_participant_name:
            token = token.with_name(
                normalized_participant_name
            )

        if metadata:
            token = token.with_metadata(
                metadata
            )

        token = token.with_grants(
            api.VideoGrants(
                room_join=True,
                room=normalized_room_name,
                can_publish=bool(
                    can_publish
                ),
                can_subscribe=bool(
                    can_subscribe
                ),
                can_publish_data=bool(
                    can_publish_data
                ),
            )
        )

        token = token.with_ttl(token_ttl)

        return token.to_jwt()

"""
LiveKit Service for AOS

Responsibilities:
- Generate secure join tokens
- Centralize LiveKit config access
- Keep secrets out of snapshot usage
"""

from __future__ import annotations

from datetime import timedelta

import frappe
from frappe import _

from aos.utils.aos_settings import get_aos_settings_snapshot
from livekit import api


class LiveKitService:
    # PUBLIC API

    @classmethod
    def get_ws_url(cls) -> str:
        """Return LiveKit WebSocket endpoint."""
        settings = get_aos_settings_snapshot()

        if not settings.livekit_endpoint:
            frappe.throw(_("LiveKit endpoint is not configured."))

        return settings.livekit_endpoint

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
        Generate token for 1:1 call participant.

        Both users:
        - can publish
        - can subscribe
        """
        return cls._generate_token(
            identity=user,
            room_name=room_name,
            participant_name=participant_name,
            metadata=metadata,
            can_publish=True,
            can_subscribe=True,
            can_publish_data=True,
        )

    @classmethod
    def generate_live_token(
        cls,
        *,
        user: str,
        room_name: str,
        role: str,  # "host" | "viewer"
        participant_name: str | None = None,
        metadata: str | None = None,
    ) -> str:
        """
        Generate token for live streaming.
        """
        if role == "host":
            can_publish = True
            can_subscribe = True
            can_publish_data = True
        elif role == "viewer":
            can_publish = False
            can_subscribe = True
            can_publish_data = False
        else:
            frappe.throw(_("Invalid LiveKit role"))

        return cls._generate_token(
            identity=user,
            room_name=room_name,
            participant_name=participant_name,
            metadata=metadata,
            can_publish=can_publish,
            can_subscribe=can_subscribe,
            can_publish_data=can_publish_data,
        )

    @classmethod
    def build_metadata(
        cls,
        *,
        user: str,
        role: str,
        conversation: str | None = None,
        call_id: str | None = None,
        call_type: str | None = None,
    ) -> str:
        """
        Build JSON metadata string for token.
        """
        payload = {
            "user": user,
            "role": role,
        }

        if conversation:
            payload["conversation"] = conversation

        if call_id:
            payload["call_id"] = call_id

        if call_type:
            payload["call_type"] = call_type

        return frappe.as_json(payload)

    # INTERNALS

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
    ) -> str:
        if not identity:
            frappe.throw(_("LiveKit identity is required"))

        if not room_name:
            frappe.throw(_("LiveKit room_name is required"))

        settings = get_aos_settings_snapshot()
        doc = frappe.get_single("AOS Settings")

        api_key = doc.livekit_api_key
        api_secret = doc.get_password("livekit_api_secret")

        if not api_key:
            frappe.throw(_("LiveKit API key is not configured"))

        if not api_secret:
            frappe.throw(_("LiveKit API secret is not configured"))

        ttl_minutes = settings.livekit_token_ttl_minutes or 60

        token = api.AccessToken(api_key, api_secret)

        token = token.with_identity(identity)

        if participant_name:
            token = token.with_name(participant_name)

        if metadata:
            token = token.with_metadata(metadata)

        token = token.with_grants(
            api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=can_publish,
                can_subscribe=can_subscribe,
                can_publish_data=can_publish_data,
            )
        )

        token = token.with_ttl(timedelta(minutes=ttl_minutes))

        return token.to_jwt()

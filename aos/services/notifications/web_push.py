"""Safe Firebase Web Messaging bootstrap configuration.

Firebase web client configuration and the Web Push VAPID *public* key are not
secrets. AOS still keeps them server-owned so web clients do not hard-code a
second configuration source. Private service-account credentials never cross
this boundary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from aos.utils.aos_config import get_env, get_env_bool

_SAFE_VALUE = re.compile(r"^[A-Za-z0-9._:@/+=\-]+$")
_PROJECT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{2,127}$")
_SENDER_ID = re.compile(r"^[0-9]{5,30}$")
_VAPID_PUBLIC_KEY = re.compile(r"^[A-Za-z0-9_-]{40,220}$")


class WebPushConfigurationError(RuntimeError):
    """Raised when web push is enabled with an incomplete public configuration."""


@dataclass(frozen=True)
class WebPushConfig:
    enabled: bool
    api_key: str = field(default="", repr=False)
    auth_domain: str = ""
    project_id: str = ""
    storage_bucket: str = ""
    messaging_sender_id: str = ""
    app_id: str = ""
    measurement_id: str = ""
    vapid_public_key: str = ""

    def public_payload(self) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False}
        firebase = {
            "apiKey": self.api_key,
            "projectId": self.project_id,
            "messagingSenderId": self.messaging_sender_id,
            "appId": self.app_id,
        }
        if self.auth_domain:
            firebase["authDomain"] = self.auth_domain
        if self.storage_bucket:
            firebase["storageBucket"] = self.storage_bucket
        if self.measurement_id:
            firebase["measurementId"] = self.measurement_id
        return {
            "enabled": True,
            "firebase": firebase,
            "vapidPublicKey": self.vapid_public_key,
        }


def _clean(name: str, *, maximum: int = 300) -> str:
    return str(get_env(name, "") or "").strip()[:maximum]


def _safe_public_value(value: str, *, minimum: int = 1, maximum: int = 300) -> bool:
    return minimum <= len(value) <= maximum and bool(_SAFE_VALUE.fullmatch(value))


def get_web_push_config() -> WebPushConfig:
    enabled = get_env_bool("NOTIFICATION_WEB_PUSH_ENABLED", False)
    if not enabled:
        return WebPushConfig(enabled=False)

    values = {
        "api_key": _clean("NOTIFICATION_FIREBASE_WEB_API_KEY"),
        "auth_domain": _clean("NOTIFICATION_FIREBASE_WEB_AUTH_DOMAIN"),
        "project_id": _clean("NOTIFICATION_FIREBASE_WEB_PROJECT_ID"),
        "storage_bucket": _clean("NOTIFICATION_FIREBASE_WEB_STORAGE_BUCKET"),
        "messaging_sender_id": _clean("NOTIFICATION_FIREBASE_WEB_MESSAGING_SENDER_ID"),
        "app_id": _clean("NOTIFICATION_FIREBASE_WEB_APP_ID"),
        "measurement_id": _clean("NOTIFICATION_FIREBASE_WEB_MEASUREMENT_ID"),
        "vapid_public_key": _clean("NOTIFICATION_FIREBASE_WEB_VAPID_PUBLIC_KEY"),
    }

    if not _safe_public_value(values["api_key"], minimum=10, maximum=200):
        raise WebPushConfigurationError("Firebase web API key is not configured safely.")
    if not _PROJECT_ID.fullmatch(values["project_id"]):
        raise WebPushConfigurationError("Firebase web project ID is not configured safely.")
    if not _SENDER_ID.fullmatch(values["messaging_sender_id"]):
        raise WebPushConfigurationError("Firebase web messaging sender ID is invalid.")
    if not _safe_public_value(values["app_id"], minimum=8, maximum=200):
        raise WebPushConfigurationError("Firebase web app ID is not configured safely.")
    if not _VAPID_PUBLIC_KEY.fullmatch(values["vapid_public_key"]):
        raise WebPushConfigurationError("Firebase web VAPID public key is invalid.")
    for optional in ("auth_domain", "storage_bucket", "measurement_id"):
        if values[optional] and not _safe_public_value(values[optional], maximum=250):
            raise WebPushConfigurationError("Firebase optional web configuration is invalid.")

    return WebPushConfig(enabled=True, **values)

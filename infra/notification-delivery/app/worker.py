from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any

import requests

try:
    import firebase_admin
    from firebase_admin import credentials, messaging
except Exception:  # pragma: no cover - import failure handled at runtime
    firebase_admin = None
    credentials = None
    messaging = None

from app.config import get_settings
from app.security import build_signature


class NotificationDeliveryError(Exception):
    pass


_FIREBASE_INITIALIZED = False


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str).encode("utf-8")


def _callback(callback_url: str, payload: dict[str, Any]) -> None:
    if not callback_url:
        return
    settings = get_settings()
    body = _json_bytes(payload)
    headers = {
        "Content-Type": "application/json",
        "X-AOS-Notification-Callback-Signature": build_signature(settings.callback_secret, body),
    }
    response = requests.post(callback_url, data=body, headers=headers, timeout=20)
    response.raise_for_status()


def _stringify_data(data: dict | None) -> dict[str, str]:
    return {str(k): str(v) for k, v in (data or {}).items() if v is not None}


def _chunk(records: list[dict[str, Any]], size: int):
    for idx in range(0, len(records), size):
        yield records[idx : idx + size]


def _normalize_android_priority(priority: str | None) -> str | None:
    value = str(priority or "").strip().lower()
    return value if value in {"high", "normal"} else None


def _normalize_android_notification_priority(priority: str | None) -> str | None:
    value = str(priority or "").strip().lower()
    return value if value in {"min", "low", "default", "high", "max"} else None


def _build_android_config(options: dict[str, Any] | None):
    if not messaging:
        return None
    options = options or {}
    priority = _normalize_android_priority(options.get("priority"))
    notification_priority = _normalize_android_notification_priority(options.get("android_notification_priority"))
    android_channel_id = options.get("android_channel_id")
    ttl_seconds = options.get("ttl_seconds")

    if not any([priority, notification_priority, android_channel_id, ttl_seconds is not None]):
        return None

    ttl = None
    if ttl_seconds is not None:
        try:
            ttl = datetime.timedelta(seconds=max(0, int(ttl_seconds)))
        except Exception:
            ttl = None

    android_notification = None
    if android_channel_id or notification_priority:
        android_notification = messaging.AndroidNotification(
            channel_id=android_channel_id,
            priority=notification_priority,
        )

    return messaging.AndroidConfig(
        priority=priority,
        ttl=ttl,
        notification=android_notification,
    )


def _init_firebase() -> None:
    global _FIREBASE_INITIALIZED
    settings = get_settings()

    if settings.dry_run:
        return
    if _FIREBASE_INITIALIZED:
        return
    if firebase_admin is None or credentials is None:
        raise NotificationDeliveryError("firebase-admin is not installed")

    service_account_path = Path(settings.firebase_service_account_path)
    if not service_account_path.exists():
        raise NotificationDeliveryError(f"Firebase service account not found: {service_account_path}")

    if not firebase_admin._apps:
        cred = credentials.Certificate(str(service_account_path))
        firebase_admin.initialize_app(cred)

    _FIREBASE_INITIALIZED = True


def _is_inactive_token_error(error: str) -> bool:
    needles = [
        "registration-token-not-registered",
        "invalid-registration-token",
        "Requested entity was not found",
        "The registration token is not a valid FCM registration token",
        "UNREGISTERED",
        "INVALID_ARGUMENT",
    ]
    return any(needle in error for needle in needles)


def _send_push(payload: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    tokens = payload.get("tokens") if isinstance(payload.get("tokens"), list) else []
    tokens = [token for token in tokens if isinstance(token, dict) and token.get("token")]

    if not tokens:
        return {
            "status": "skipped",
            "success_count": 0,
            "failure_count": 0,
            "inactive_token_hashes": [],
            "provider_responses": [],
        }

    if settings.dry_run:
        return {
            "status": "delivered",
            "success_count": len(tokens),
            "failure_count": 0,
            "inactive_token_hashes": [],
            "provider_responses": [{"dry_run": True, "count": len(tokens)}],
        }

    _init_firebase()

    success_count = 0
    failure_count = 0
    inactive_hashes: list[str] = []
    provider_responses: list[dict[str, Any]] = []

    data_payload = _stringify_data(payload.get("data") if isinstance(payload.get("data"), dict) else {})
    android_config = _build_android_config(payload.get("options") if isinstance(payload.get("options"), dict) else {})

    for chunk in _chunk(tokens, max(1, min(settings.max_tokens_per_multicast, 500))):
        token_values = [row["token"] for row in chunk if row.get("token")]
        if not token_values:
            continue

        message = messaging.MulticastMessage(
            notification=messaging.Notification(
                title=str(payload.get("title") or ""),
                body=str(payload.get("body") or ""),
            ),
            data=data_payload,
            tokens=token_values,
            android=android_config,
        )

        response = messaging.send_each_for_multicast(message)
        success_count += int(response.success_count or 0)
        failure_count += int(response.failure_count or 0)
        provider_responses.append({"success_count": response.success_count, "failure_count": response.failure_count})

        for idx, item in enumerate(response.responses):
            if item.success:
                continue
            error = str(item.exception or "")
            if _is_inactive_token_error(error):
                token_hash = str(chunk[idx].get("token_hash") or "").strip()
                if token_hash:
                    inactive_hashes.append(token_hash)

    return {
        "status": "delivered" if success_count > 0 or failure_count == 0 else "failed",
        "success_count": success_count,
        "failure_count": failure_count,
        "inactive_token_hashes": sorted(set(inactive_hashes)),
        "provider_responses": provider_responses,
    }


def process_notification_delivery_job(payload: dict[str, Any]) -> dict[str, Any]:
    job_id = str(payload.get("job_id") or "").strip()
    callback_url = str(payload.get("callback_url") or "").strip()

    if not job_id:
        raise NotificationDeliveryError("job_id is required")

    try:
        result = _send_push(payload)
        status_payload = {
            "job_id": job_id,
            "service_job_id": job_id,
            "status": result["status"],
            "channel": payload.get("channel") or "push",
            "token_count": len(payload.get("tokens") or []),
            "success_count": result["success_count"],
            "failure_count": result["failure_count"],
            "inactive_token_hashes": result["inactive_token_hashes"],
            "provider_responses": result["provider_responses"],
        }
        _callback(callback_url, status_payload)
        return status_payload

    except Exception as exc:
        status_payload = {
            "job_id": job_id,
            "service_job_id": job_id,
            "status": "failed",
            "channel": payload.get("channel") or "push",
            "token_count": len(payload.get("tokens") or []),
            "success_count": 0,
            "failure_count": len(payload.get("tokens") or []),
            "inactive_token_hashes": [],
            "error": str(exc) or "Notification delivery failed",
        }
        try:
            _callback(callback_url, status_payload)
        finally:
            raise

"""Production-readiness configuration validation for AOS.

This module is intentionally read-only. It validates that deployment-time
configuration is explicit, non-placeholder, and safe enough for a production
release without exposing secret values in the returned report.

Bench usage:

    bench --site <site> execute aos.utils.production_config.validate_production_config
    bench --site <site> execute aos.utils.production_config.assert_production_config_ready
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

import frappe

from aos.utils import aos_config


MIN_SECRET_LENGTH = 24

_PLACEHOLDER_PATTERNS = (
    "change-this",
    "replace-with",
    "replace_",
    "replace-",
    "example.com",
    "example.org",
    "example.net",
    "your-domain",
    "your_",
    "todo",
    "dummy",
    "test-secret",
    "secret-key",
    "minioadmin",
    "password",
)

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}

_EXTERNAL_WORKER_SERVICES: tuple[dict[str, Any], ...] = (
    {
        "name": "video_processing",
        "category": "video",
        "service_url_keys": ("VIDEO_SERVICE_URL",),
        "service_secret_keys": ("VIDEO_SERVICE_SECRET",),
        "callback_secret_keys": ("VIDEO_SERVICE_CALLBACK_SECRET",),
        "callback_url_keys": ("VIDEO_CALLBACK_URL",),
        "callback_method": "aos.api.v1.video_processing.handle_callback",
        "enabled_keys": (),
        "enabled_default": True,
    },
    {
        "name": "moderation",
        "category": "moderation",
        "service_url_keys": ("MODERATION_SERVICE_URL",),
        "service_secret_keys": ("MODERATION_SERVICE_SECRET",),
        "callback_secret_keys": ("MODERATION_SERVICE_CALLBACK_SECRET",),
        "callback_url_keys": ("MODERATION_CALLBACK_URL",),
        "callback_method": "aos.api.v1.moderation.handle_callback",
        "enabled_keys": ("MODERATION_ENABLED",),
        "enabled_default": True,
    },
    {
        "name": "search_ranking",
        "category": "search_ranking",
        "service_url_keys": ("SEARCH_RANKING_SERVICE_URL",),
        "service_secret_keys": ("SEARCH_RANKING_SERVICE_SECRET",),
        "callback_secret_keys": ("SEARCH_RANKING_SERVICE_CALLBACK_SECRET",),
        "callback_url_keys": ("SEARCH_RANKING_CALLBACK_URL",),
        "callback_method": "aos.api.v1.search_ranking.handle_callback",
        "enabled_keys": ("SEARCH_RANKING_ENABLED",),
        "enabled_default": True,
    },
    {
        "name": "analytics_pipeline",
        "category": "analytics",
        "service_url_keys": ("ANALYTICS_SERVICE_URL",),
        "service_secret_keys": ("ANALYTICS_SERVICE_SECRET",),
        "callback_secret_keys": ("ANALYTICS_SERVICE_CALLBACK_SECRET",),
        "callback_url_keys": ("ANALYTICS_CALLBACK_URL",),
        "callback_method": "aos.api.v1.analytics_pipeline.handle_callback",
        "enabled_keys": ("ANALYTICS_PIPELINE_ENABLED",),
        "enabled_default": True,
    },
    {
        "name": "notification_delivery",
        "category": "notifications",
        "service_url_keys": ("NOTIFICATION_SERVICE_URL",),
        "service_secret_keys": ("NOTIFICATION_SERVICE_SECRET",),
        "callback_secret_keys": ("NOTIFICATION_SERVICE_CALLBACK_SECRET",),
        "callback_url_keys": ("NOTIFICATION_CALLBACK_URL",),
        "callback_method": "aos.api.v1.notification_delivery.handle_callback",
        "enabled_keys": ("NOTIFICATION_DELIVERY_ENABLED",),
        "enabled_default": True,
    },
)

_PRIVATE_SERVICE_URLS: tuple[dict[str, Any], ...] = (
    {
        "category": "ai_ml",
        "name": "translation",
        "keys": ("TRANSLATION_SERVICE_URL",),
    },
    {
        "category": "ai_ml",
        "name": "image_search",
        "keys": ("IMAGE_SEARCH_SERVICE_URL",),
    },
    {
        "category": "ai_ml",
        "name": "background_removal",
        "keys": ("BACKGROUND_REMOVAL_SERVICE_URL",),
    },
)

_MAP_SITE_CONFIG_URLS: tuple[dict[str, str], ...] = (
    {"key": "photon_base_url", "name": "Photon base URL"},
    {"key": "nominatim_base_url", "name": "Nominatim base URL"},
    {"key": "valhalla_base_url", "name": "Valhalla base URL"},
)


class ProductionConfigError(RuntimeError):
    """Raised when production configuration validation fails."""


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _env_value(env: Mapping[str, Any] | None, *keys: str) -> tuple[str, str | None]:
    for key in keys:
        if env is not None:
            value = _clean(env.get(key))
        else:
            value = _clean(aos_config.get_env(key))
        if value:
            return value, key
    return "", None


def _site_value(site_config: Mapping[str, Any] | None, key: str) -> str:
    if site_config is not None:
        return _clean(site_config.get(key))
    try:
        return _clean(frappe.conf.get(key))
    except Exception:
        return ""


def _bool_env(env: Mapping[str, Any] | None, keys: tuple[str, ...], default: bool) -> bool:
    value, _ = _env_value(env, *keys)
    if not value:
        return bool(default)
    return value.lower() in {"1", "true", "yes", "y", "on"}


def _is_placeholder(value: str) -> bool:
    lowered = _clean(value).lower()
    if not lowered:
        return False
    return any(pattern in lowered for pattern in _PLACEHOLDER_PATTERNS)


def _is_probably_secret_placeholder(value: str) -> bool:
    lowered = _clean(value).lower()
    if not lowered:
        return False
    if _is_placeholder(lowered):
        return True
    if lowered in {"secret", "password", "changeme", "admin", "123456", "12345678"}:
        return True
    return False


def _host(url: str) -> str:
    parsed = urlparse(url if "://" in url else f"//{url}")
    return (parsed.hostname or "").strip().lower()


def _is_local_url(url: str) -> bool:
    host = _host(url)
    return host in _LOCAL_HOSTS


def _is_valid_url(url: str, *, require_scheme: bool = True) -> bool:
    value = _clean(url)
    if not value:
        return False
    parsed = urlparse(value)
    if require_scheme and parsed.scheme not in {"http", "https", "ws", "wss"}:
        return False
    if require_scheme and not parsed.netloc:
        return False
    return True


def _public_url_is_safe(url: str, *, require_https: bool = True) -> bool:
    value = _clean(url)
    if not _is_valid_url(value):
        return False
    parsed = urlparse(value)
    if require_https and parsed.scheme not in {"https", "wss"}:
        return False
    if _is_local_url(value):
        return False
    if _is_placeholder(value):
        return False
    return True


def _redacted_issue(
    issues: list[dict[str, Any]],
    *,
    severity: str,
    category: str,
    key: str,
    message: str,
    remediation: str,
) -> None:
    issues.append(
        {
            "severity": severity,
            "category": category,
            "key": key,
            "message": message,
            "remediation": remediation,
        }
    )


def _check_required_value(
    issues: list[dict[str, Any]],
    *,
    env: Mapping[str, Any] | None,
    category: str,
    keys: tuple[str, ...],
    label: str,
    secret: bool = False,
    min_length: int = MIN_SECRET_LENGTH,
) -> str:
    value, source_key = _env_value(env, *keys)
    display_key = source_key or "/".join(keys)

    if not value:
        _redacted_issue(
            issues,
            severity="error",
            category=category,
            key=display_key,
            message=f"{label} is missing.",
            remediation=f"Set {display_key} to a production value.",
        )
        return ""

    if secret:
        if len(value) < min_length:
            _redacted_issue(
                issues,
                severity="error",
                category=category,
                key=display_key,
                message=f"{label} is too short for production.",
                remediation=f"Set {display_key} to a long random value of at least {min_length} characters.",
            )
        if _is_probably_secret_placeholder(value):
            _redacted_issue(
                issues,
                severity="error",
                category=category,
                key=display_key,
                message=f"{label} still looks like a placeholder/default value.",
                remediation=f"Rotate {display_key} to a unique production secret.",
            )
    elif _is_placeholder(value):
        _redacted_issue(
            issues,
            severity="error",
            category=category,
            key=display_key,
            message=f"{label} still looks like a placeholder/default value.",
            remediation=f"Set {display_key} to a real production value.",
        )

    return value


def _check_required_private_url(
    issues: list[dict[str, Any]],
    *,
    env: Mapping[str, Any] | None,
    category: str,
    keys: tuple[str, ...],
    label: str,
) -> str:
    value = _check_required_value(
        issues,
        env=env,
        category=category,
        keys=keys,
        label=label,
    )
    display_key = _env_value(env, *keys)[1] or "/".join(keys)
    if value and not _is_valid_url(value):
        _redacted_issue(
            issues,
            severity="error",
            category=category,
            key=display_key,
            message=f"{label} must be an HTTP(S) URL.",
            remediation=f"Set {display_key} to a valid internal service URL.",
        )
    return value


def _check_required_public_url(
    issues: list[dict[str, Any]],
    *,
    env: Mapping[str, Any] | None,
    category: str,
    keys: tuple[str, ...],
    label: str,
    require_https: bool = True,
) -> str:
    value = _check_required_value(
        issues,
        env=env,
        category=category,
        keys=keys,
        label=label,
    )
    display_key = _env_value(env, *keys)[1] or "/".join(keys)
    if value and not _public_url_is_safe(value, require_https=require_https):
        scheme_text = "HTTPS/WSS" if require_https else "HTTP(S)"
        _redacted_issue(
            issues,
            severity="error",
            category=category,
            key=display_key,
            message=f"{label} must be a real public {scheme_text} URL/domain and must not be localhost or an example value.",
            remediation=f"Set {display_key} to the production public endpoint.",
        )
    return value


def _callback_url_or_domain(
    issues: list[dict[str, Any]],
    *,
    env: Mapping[str, Any] | None,
    category: str,
    service_name: str,
    callback_url_keys: tuple[str, ...],
    callback_method: str,
) -> None:
    callback_url, callback_key = _env_value(env, *callback_url_keys)
    if callback_url:
        key = callback_key or "/".join(callback_url_keys)
        if callback_method not in callback_url:
            _redacted_issue(
                issues,
                severity="warning",
                category=category,
                key=key,
                message=f"{service_name} callback URL does not contain the expected callback method path.",
                remediation=f"Confirm {key} points to {callback_method}.",
            )
        if not _public_url_is_safe(callback_url, require_https=True):
            _redacted_issue(
                issues,
                severity="error",
                category=category,
                key=key,
                message=f"{service_name} callback URL must be a real public HTTPS URL.",
                remediation=f"Set {key} to the production callback URL.",
            )
        return

    domain, domain_key = _env_value(env, "AOS_API_DOMAIN")
    if not domain:
        _redacted_issue(
            issues,
            severity="error",
            category=category,
            key="/".join((*callback_url_keys, "AOS_API_DOMAIN")),
            message=f"{service_name} callback URL cannot be derived because no callback URL or AOS API domain is configured.",
            remediation=f"Set {callback_url_keys[0]} or AOS_API_DOMAIN.",
        )
        return

    domain_url = f"https://{domain}"
    if not _public_url_is_safe(domain_url, require_https=True):
        _redacted_issue(
            issues,
            severity="error",
            category=category,
            key=domain_key or "AOS_API_DOMAIN",
            message=f"AOS API domain used to derive {service_name} callback URL is not production-safe.",
            remediation="Set AOS_API_DOMAIN to the real production API domain.",
        )


def _check_worker_services(issues: list[dict[str, Any]], env: Mapping[str, Any] | None) -> None:
    for service in _EXTERNAL_WORKER_SERVICES:
        enabled = _bool_env(
            env,
            tuple(service["enabled_keys"]),
            bool(service["enabled_default"]),
        )
        if not enabled:
            _redacted_issue(
                issues,
                severity="warning",
                category=str(service["category"]),
                key=str(service["enabled_keys"][0]) if service["enabled_keys"] else str(service["name"]),
                message=f"{service['name']} is disabled.",
                remediation="Confirm this is intentional before production release.",
            )
            continue

        _check_required_private_url(
            issues,
            env=env,
            category=str(service["category"]),
            keys=tuple(service["service_url_keys"]),
            label=f"{service['name']} service URL",
        )
        _check_required_value(
            issues,
            env=env,
            category=str(service["category"]),
            keys=tuple(service["service_secret_keys"]),
            label=f"{service['name']} dispatch secret",
            secret=True,
        )
        _check_required_value(
            issues,
            env=env,
            category=str(service["category"]),
            keys=tuple(service["callback_secret_keys"]),
            label=f"{service['name']} callback secret",
            secret=True,
        )
        _callback_url_or_domain(
            issues,
            env=env,
            category=str(service["category"]),
            service_name=str(service["name"]),
            callback_url_keys=tuple(service["callback_url_keys"]),
            callback_method=str(service["callback_method"]),
        )


def _check_storage(issues: list[dict[str, Any]], env: Mapping[str, Any] | None) -> None:
    endpoint = _check_required_value(
        issues,
        env=env,
        category="storage",
        keys=("MINIO_ENDPOINT", "AOS_MINIO_ENDPOINT"),
        label="MinIO endpoint",
    )
    if endpoint:
        try:
            aos_config.clean_endpoint(endpoint)
        except Exception:
            _redacted_issue(
                issues,
                severity="error",
                category="storage",
                key=_env_value(env, "MINIO_ENDPOINT", "AOS_MINIO_ENDPOINT")[1] or "MINIO_ENDPOINT/AOS_MINIO_ENDPOINT",
                message="MinIO endpoint must be host[:port] without scheme, bucket, query, or path.",
                remediation="Move public URL/path settings to MINIO_PUBLIC_BASE_URL and keep MINIO_ENDPOINT as host[:port].",
            )

    _check_required_value(
        issues,
        env=env,
        category="storage",
        keys=("MINIO_ACCESS_KEY", "MINIO_ROOT_USER"),
        label="MinIO access key",
        secret=True,
        min_length=8,
    )
    _check_required_value(
        issues,
        env=env,
        category="storage",
        keys=("MINIO_SECRET_KEY", "MINIO_ROOT_PASSWORD"),
        label="MinIO secret key",
        secret=True,
    )
    _check_required_public_url(
        issues,
        env=env,
        category="storage",
        keys=("MINIO_PUBLIC_BASE_URL", "MINIO_PUBLIC_URL", "AOS_MINIO_PUBLIC_BASE_URL"),
        label="MinIO public base URL",
        require_https=True,
    )
    for key, label in (
        ("AOS_PUBLIC_BUCKET", "public media bucket"),
        ("AOS_PRIVATE_BUCKET", "private media bucket"),
        ("AOS_MINIO_BUCKET", "Shorts output bucket"),
    ):
        value = _check_required_value(
            issues,
            env=env,
            category="storage",
            keys=(key,),
            label=label,
        )
        if value and "/" in value:
            _redacted_issue(
                issues,
                severity="error",
                category="storage",
                key=key,
                message=f"{label} must be a bucket name, not a path.",
                remediation=f"Remove slashes from {key} and use AOS_MINIO_BASE_PATH for paths.",
            )


def _check_livekit(issues: list[dict[str, Any]], env: Mapping[str, Any] | None) -> None:
    endpoint, endpoint_key = _env_value(env, "LIVEKIT_ENDPOINT", "AOS_LIVEKIT_ENDPOINT")
    if not endpoint:
        domain, domain_key = _env_value(env, "AOS_LIVEKIT_DOMAIN")
        if domain:
            endpoint = f"wss://{domain}"
            endpoint_key = domain_key
    if not endpoint:
        _redacted_issue(
            issues,
            severity="error",
            category="livekit",
            key="LIVEKIT_ENDPOINT/AOS_LIVEKIT_ENDPOINT/AOS_LIVEKIT_DOMAIN",
            message="LiveKit public endpoint is missing.",
            remediation="Set LIVEKIT_ENDPOINT to the production WSS endpoint or set AOS_LIVEKIT_DOMAIN.",
        )
    elif not _public_url_is_safe(endpoint, require_https=True) or urlparse(endpoint).scheme != "wss":
        _redacted_issue(
            issues,
            severity="error",
            category="livekit",
            key=endpoint_key or "LIVEKIT_ENDPOINT",
            message="LiveKit endpoint must be a real public WSS URL.",
            remediation="Set LIVEKIT_ENDPOINT to wss://<production-livekit-domain>.",
        )

    livekit_keys, _ = _env_value(env, "LIVEKIT_KEYS")
    api_key, _ = _env_value(env, "LIVEKIT_API_KEY")
    api_secret, _ = _env_value(env, "LIVEKIT_API_SECRET")

    if livekit_keys and ":" in livekit_keys:
        split_key, split_secret = livekit_keys.split(":", 1)
        api_key = api_key or split_key.strip()
        api_secret = api_secret or split_secret.strip()

    if not api_key:
        _redacted_issue(
            issues,
            severity="error",
            category="livekit",
            key="LIVEKIT_API_KEY/LIVEKIT_KEYS",
            message="LiveKit API key is missing.",
            remediation="Set LIVEKIT_API_KEY or LIVEKIT_KEYS.",
        )
    elif _is_placeholder(api_key):
        _redacted_issue(
            issues,
            severity="error",
            category="livekit",
            key="LIVEKIT_API_KEY/LIVEKIT_KEYS",
            message="LiveKit API key still looks like a placeholder/default value.",
            remediation="Set a real production LiveKit API key.",
        )

    if not api_secret:
        _redacted_issue(
            issues,
            severity="error",
            category="livekit",
            key="LIVEKIT_API_SECRET/LIVEKIT_KEYS",
            message="LiveKit API secret is missing.",
            remediation="Set LIVEKIT_API_SECRET or LIVEKIT_KEYS.",
        )
    elif len(api_secret) < MIN_SECRET_LENGTH or _is_probably_secret_placeholder(api_secret):
        _redacted_issue(
            issues,
            severity="error",
            category="livekit",
            key="LIVEKIT_API_SECRET/LIVEKIT_KEYS",
            message="LiveKit API secret is too weak or still looks like a placeholder/default value.",
            remediation="Set a long random production LiveKit API secret.",
        )


def _check_public_domains(issues: list[dict[str, Any]], env: Mapping[str, Any] | None) -> None:
    for key, label in (
        ("AOS_API_DOMAIN", "AOS API domain"),
        ("AOS_MAPS_DOMAIN", "AOS maps domain"),
        ("AOS_MINIO_DOMAIN", "AOS files domain"),
    ):
        value = _check_required_value(
            issues,
            env=env,
            category="domains",
            keys=(key,),
            label=label,
        )
        if value:
            url_value = value if "://" in value else f"https://{value}"
            if not _public_url_is_safe(url_value, require_https=True):
                _redacted_issue(
                    issues,
                    severity="error",
                    category="domains",
                    key=key,
                    message=f"{label} must be a real production domain, not localhost or an example value.",
                    remediation=f"Set {key} to the real production domain.",
                )


def _check_firebase(issues: list[dict[str, Any]], env: Mapping[str, Any] | None) -> None:
    notification_enabled = _bool_env(env, ("NOTIFICATION_DELIVERY_ENABLED",), True)
    dry_run = _bool_env(env, ("NOTIFICATION_DRY_RUN",), False)
    if notification_enabled and dry_run:
        _redacted_issue(
            issues,
            severity="error",
            category="notifications",
            key="NOTIFICATION_DRY_RUN",
            message="Notification dry-run mode is enabled.",
            remediation="Set NOTIFICATION_DRY_RUN=false before production release.",
        )

    if not notification_enabled:
        _redacted_issue(
            issues,
            severity="warning",
            category="notifications",
            key="NOTIFICATION_DELIVERY_ENABLED",
            message="Notification delivery is disabled.",
            remediation="Confirm this is intentional before production release.",
        )
        return

    value, key = _env_value(
        env,
        "NOTIFICATION_FIREBASE_SERVICE_ACCOUNT_PATH",
        "FIREBASE_SERVICE_ACCOUNT_PATH",
    )
    if not value:
        value, key = _env_value(env, "NOTIFICATION_FIREBASE_SERVICE_ACCOUNT_HOST_PATH")
    if not value:
        _redacted_issue(
            issues,
            severity="error",
            category="notifications",
            key="NOTIFICATION_FIREBASE_SERVICE_ACCOUNT_PATH",
            message="Firebase service account path is missing.",
            remediation="Set NOTIFICATION_FIREBASE_SERVICE_ACCOUNT_PATH for the worker container or NOTIFICATION_FIREBASE_SERVICE_ACCOUNT_HOST_PATH for deployment checks.",
        )
    elif _is_placeholder(value):
        _redacted_issue(
            issues,
            severity="error",
            category="notifications",
            key=key or "NOTIFICATION_FIREBASE_SERVICE_ACCOUNT_PATH",
            message="Firebase service account path still looks like a placeholder/default value.",
            remediation="Set the Firebase service account path to the production secret file location.",
        )


def _check_ai_services(issues: list[dict[str, Any]], env: Mapping[str, Any] | None) -> None:
    for service in _PRIVATE_SERVICE_URLS:
        _check_required_private_url(
            issues,
            env=env,
            category=str(service["category"]),
            keys=tuple(service["keys"]),
            label=f"{service['name']} service URL",
        )

    qdrant_url = _check_required_private_url(
        issues,
        env=env,
        category="ai_ml",
        keys=("IMAGE_SEARCH_QDRANT_URL", "QDRANT_URL"),
        label="image-search Qdrant URL",
    )
    if qdrant_url and _is_placeholder(qdrant_url):
        _redacted_issue(
            issues,
            severity="error",
            category="ai_ml",
            key="IMAGE_SEARCH_QDRANT_URL/QDRANT_URL",
            message="Qdrant URL still looks like a placeholder/default value.",
            remediation="Set the image-search Qdrant URL to the production internal Qdrant endpoint.",
        )


def _check_maps(
    issues: list[dict[str, Any]],
    env: Mapping[str, Any] | None,
    site_config: Mapping[str, Any] | None,
) -> None:
    _check_required_public_url(
        issues,
        env=env,
        category="maps",
        keys=("TILESERVER_PUBLIC_URL",),
        label="TileServer public URL",
        require_https=True,
    )

    for item in _MAP_SITE_CONFIG_URLS:
        value = _site_value(site_config, item["key"])
        if not value:
            _redacted_issue(
                issues,
                severity="error",
                category="maps",
                key=item["key"],
                message=f"{item['name']} is missing from site config.",
                remediation=f"Set {item['key']} with bench set-config.",
            )
        elif not _is_valid_url(value):
            _redacted_issue(
                issues,
                severity="error",
                category="maps",
                key=item["key"],
                message=f"{item['name']} must be an HTTP(S) URL.",
                remediation=f"Set {item['key']} to the production internal maps service URL.",
            )
        elif _is_placeholder(value):
            _redacted_issue(
                issues,
                severity="error",
                category="maps",
                key=item["key"],
                message=f"{item['name']} still looks like a placeholder/default value.",
                remediation=f"Set {item['key']} to the production internal maps service URL.",
            )


def _dedupe_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str, str]] = set()
    result: list[dict[str, Any]] = []
    for issue in issues:
        key = (
            str(issue.get("severity") or ""),
            str(issue.get("category") or ""),
            str(issue.get("key") or ""),
            str(issue.get("message") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(issue)
    return result


def validate_production_config(
    *,
    env: Mapping[str, Any] | None = None,
    site_config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a redacted production-readiness config report.

    The returned report never includes secret values. It includes only variable
    names, issue categories, and remediation text.
    """

    issues: list[dict[str, Any]] = []

    _check_public_domains(issues, env)
    _check_storage(issues, env)
    _check_livekit(issues, env)
    _check_worker_services(issues, env)
    _check_ai_services(issues, env)
    _check_firebase(issues, env)
    _check_maps(issues, env, site_config)

    issues = _dedupe_issues(issues)
    errors = [issue for issue in issues if issue.get("severity") == "error"]
    warnings = [issue for issue in issues if issue.get("severity") == "warning"]

    return {
        "ready": not errors,
        "summary": {
            "errors": len(errors),
            "warnings": len(warnings),
            "checks": 8,
        },
        "errors": errors,
        "warnings": warnings,
    }


def assert_production_config_ready() -> dict[str, Any]:
    """Bench-executable guard that raises when production config is not ready."""

    report = validate_production_config()
    if not report.get("ready"):
        count = report.get("summary", {}).get("errors", 0)
        raise ProductionConfigError(
            f"AOS production configuration is not ready: {count} error(s). "
            "Run validate_production_config for the redacted report."
        )
    return report


def production_config_summary() -> dict[str, Any]:
    """Bench-friendly lightweight summary with no secrets."""

    report = validate_production_config()
    return {
        "ready": report.get("ready"),
        "summary": report.get("summary"),
        "errors": report.get("errors"),
        "warnings": report.get("warnings"),
    }


def report_contains_secret_value(report: Mapping[str, Any], secret_value: str) -> bool:
    """Test helper: ensure a redacted report does not leak known secret values."""

    needle = _clean(secret_value)
    if not needle:
        return False
    return bool(re.search(re.escape(needle), repr(report)))

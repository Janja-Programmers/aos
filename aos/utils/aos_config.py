"""Runtime infrastructure configuration for AOS.

Reads deployment/runtime values from environment variables. These values are not
business settings and should not live in the AOS Settings DocType.

Rule of thumb:
- aos_settings.py: admin-editable product rules/defaults from AOS Settings.
- aos_config.py: secrets, endpoints, buckets, and internal service URLs from .env.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse


def _clean(value: object | None) -> str | None:
    text = str(value or "").strip()
    return text or None


def _unquote_env_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


@lru_cache(maxsize=1)
def _dotenv_values() -> dict[str, str]:
    """Read the app-level .env as a fallback for host-based Bench processes.

    Docker Compose reads .env for interpolation, but a host Bench/Supervisor
    process does not automatically inherit those values. This fallback lets .env
    remain the single source of truth without requiring python-dotenv. Real
    process environment variables still take priority.
    """

    candidates: list[Path] = []

    # Explicit override wins. Useful for staging/production where the infra
    # clone owns .env and the Frappe app clone runs from frappe-bench.
    explicit_env_file = _clean(os.environ.get("AOS_ENV_FILE"))
    if explicit_env_file:
        candidates.append(Path(explicit_env_file).expanduser())

    try:
        candidates.append(Path.cwd() / ".env")
    except Exception:
        pass

    try:
        # App clone root: /home/aos/frappe-bench/apps/aos/.env in dev setups.
        app_root = Path(__file__).resolve().parents[2]
        candidates.append(app_root / ".env")
    except Exception:
        pass

    # Staging/production layout used by AOS:
    #   /home/aos/aos                 -> infra clone with docker-compose + .env
    #   /home/aos/frappe-bench/apps/aos -> Frappe app clone
    candidates.append(Path("/home/aos/aos/.env"))

    # Generic nearby fallback for single-clone/local development.
    candidates.append(Path.home() / "aos" / ".env")

    seen: set[str] = set()
    unique_candidates: list[Path] = []
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique_candidates.append(path)

    values: dict[str, str] = {}

    for path in unique_candidates:
        try:
            if not path.exists() or not path.is_file():
                continue

            for raw_line in path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue

                key, value = line.split("=", 1)
                key = key.strip()
                if not key or key.startswith("#"):
                    continue

                values.setdefault(key, _unquote_env_value(value))

            if values:
                break
        except Exception:
            continue

    return values


def get_env(name: str, default: str | None = None) -> str | None:
    return _clean(os.environ.get(name)) or _clean(_dotenv_values().get(name)) or default


def get_first_env(*names: str, default: str | None = None) -> str | None:
    for name in names:
        value = get_env(name)
        if value:
            return value
    return default


def get_required_env(*names: str) -> str:
    value = get_first_env(*names)
    if value:
        return value
    joined = ", ".join(names)
    raise RuntimeError(f"Missing required environment variable: {joined}")


def get_env_int(name: str, default: int, *, min_value: int | None = None, max_value: int | None = None) -> int:
    raw = get_env(name)
    if raw is None:
        value = int(default)
    else:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = int(default)

    if min_value is not None and value < min_value:
        return int(min_value)
    if max_value is not None and value > max_value:
        return int(max_value)
    return int(value)


def get_env_bool(name: str, default: bool = False) -> bool:
    raw = get_env(name)
    if raw is None:
        return bool(default)
    return raw.lower() in {"1", "true", "yes", "y", "on"}


def clean_url(value: str | None, *, default: str | None = None) -> str:
    url = _clean(value) or _clean(default) or ""
    return url.rstrip("/")


def clean_endpoint(value: str | None) -> str:
    """Normalize a MinIO SDK endpoint into host[:port]."""
    raw = _clean(value) or ""
    raw = raw.rstrip("/")

    if not raw:
        return ""

    if "://" in raw:
        parsed = urlparse(raw)
        endpoint = (parsed.netloc or "").strip()
        path = (parsed.path or "").strip("/")
        if path or parsed.params or parsed.query or parsed.fragment:
            raise RuntimeError(
                "Invalid MINIO_ENDPOINT. Use host[:port] only; put public paths "
                "in MINIO_PUBLIC_BASE_URL if needed."
            )
        return endpoint

    if "/" in raw:
        raise RuntimeError(
            "Invalid MINIO_ENDPOINT. Use host[:port] only, without bucket or path."
        )

    return raw


@dataclass(frozen=True)
class MinioConfig:
    endpoint: str
    access_key: str
    secret_key: str
    secure: bool
    public_base_url: str
    bucket: str
    public_bucket: str
    private_bucket: str
    base_path: str


def get_minio_config() -> MinioConfig:
    """Resolve MinIO config from .env/env.

    Generic media uses the public/private AOS media buckets. Processed Shorts
    output uses the configured Shorts output bucket/base path.
    """
    endpoint = clean_endpoint(
        get_first_env(
            "MINIO_ENDPOINT",
            "AOS_MINIO_ENDPOINT",
            default="minio:9000",
        )
    )

    access_key = get_required_env("MINIO_ACCESS_KEY", "MINIO_ROOT_USER")
    secret_key = get_required_env("MINIO_SECRET_KEY", "MINIO_ROOT_PASSWORD")

    public_base_url = clean_url(
        get_first_env(
            "MINIO_PUBLIC_BASE_URL",
            "MINIO_PUBLIC_URL",
            "AOS_MINIO_PUBLIC_BASE_URL",
            default="http://localhost:9100",
        )
    )

    shorts_output_bucket = get_first_env(
        "AOS_MINIO_BUCKET",
        "MINIO_BUCKET",
        default="shorts",
    ) or "shorts"

    return MinioConfig(
        endpoint=endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=get_env_bool("MINIO_SECURE", False),
        public_base_url=public_base_url,
        bucket=shorts_output_bucket.strip().strip("/"),
        public_bucket=(
            get_first_env("AOS_PUBLIC_BUCKET", "MINIO_PUBLIC_BUCKET", default="aos-public")
            or "aos-public"
        ).strip().strip("/"),
        private_bucket=(
            get_first_env("AOS_PRIVATE_BUCKET", "MINIO_PRIVATE_BUCKET", default="aos-private")
            or "aos-private"
        ).strip().strip("/"),
        base_path=(
            get_first_env("AOS_MINIO_BASE_PATH", "MINIO_BASE_PATH", default="shorts")
            or "shorts"
        ).strip().strip("/"),
    )


@dataclass(frozen=True)
class LiveKitConfig:
    endpoint: str
    api_key: str
    api_secret: str


def _split_livekit_keys(value: str | None) -> tuple[str | None, str | None]:
    raw = _clean(value)
    if not raw or ":" not in raw:
        return None, None
    api_key, api_secret = raw.split(":", 1)
    return _clean(api_key), _clean(api_secret)


def get_livekit_config() -> LiveKitConfig:
    keys_api_key, keys_api_secret = _split_livekit_keys(get_env("LIVEKIT_KEYS"))

    endpoint = get_first_env("LIVEKIT_ENDPOINT", "AOS_LIVEKIT_ENDPOINT")
    if not endpoint:
        domain = get_env("AOS_LIVEKIT_DOMAIN")
        endpoint = f"wss://{domain}" if domain else None

    return LiveKitConfig(
        endpoint=clean_url(endpoint),
        api_key=get_first_env("LIVEKIT_API_KEY", default=keys_api_key) or "",
        api_secret=get_first_env("LIVEKIT_API_SECRET", default=keys_api_secret) or "",
    )


def get_fx_api_key() -> str | None:
    return get_first_env("FX_API_KEY", "EXCHANGERATE_API_KEY", "EXCHANGERATE_HOST_API_KEY")


def get_image_search_service_url() -> str:
    return clean_url(
        get_first_env(
            "IMAGE_SEARCH_SERVICE_URL",
            default=f"http://127.0.0.1:{get_env('IMAGE_SEARCH_PORT', '8110')}",
        ),
        default="http://127.0.0.1:8110",
    )


def get_background_removal_service_url() -> str:
    return clean_url(
        get_first_env(
            "BACKGROUND_REMOVAL_SERVICE_URL",
            default=f"http://127.0.0.1:{get_env('BACKGROUND_REMOVAL_PORT', '8120')}",
        ),
        default="http://127.0.0.1:8120",
    )


def get_translation_service_url() -> str:
    return clean_url(
        get_first_env(
            "TRANSLATION_SERVICE_URL",
            default=f"http://127.0.0.1:{get_env('TRANSLATION_PORT', '8100')}",
        ),
        default="http://127.0.0.1:8100",
    )

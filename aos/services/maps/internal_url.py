"""SSRF-safe internal Maps service URL helpers."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit, urlunsplit


class InvalidInternalMapsURL(ValueError):
    pass


def normalize_internal_maps_url(value: object, *, service: str) -> str:
    text = str(value or "").strip().rstrip("/")
    if not text:
        raise InvalidInternalMapsURL(f"{service} service URL is not configured.")
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise InvalidInternalMapsURL(f"{service} service URL must use HTTP or HTTPS.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise InvalidInternalMapsURL(f"{service} service URL contains unsupported components.")
    host = parsed.hostname
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # Docker/cluster service names are intentionally supported. Public
        # FQDNs require explicit operator opt-in in site_config.
        if "." in host and host not in {"localhost"}:
            raise InvalidInternalMapsURL(f"{service} service URL must target an internal host.")
    else:
        if not (address.is_private or address.is_loopback or address.is_link_local):
            raise InvalidInternalMapsURL(f"{service} service URL must target an internal address.")
    netloc = parsed.netloc.lower()
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path.rstrip("/"), "", ""))


def build_internal_maps_url(base_url: str, endpoint: str) -> str:
    path = endpoint if str(endpoint).startswith("/") else f"/{endpoint}"
    if "?" in path or "#" in path or "\\" in path:
        raise InvalidInternalMapsURL("Maps service endpoint is invalid.")
    return f"{base_url}{path}"


def safe_provider_body(value: object, *, maximum: int = 1000) -> str:
    text = str(value or "")[: max(0, int(maximum))]
    return "".join(character if character in {"\n", "\t"} or ord(character) >= 32 else " " for character in text)

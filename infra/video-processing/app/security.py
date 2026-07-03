from __future__ import annotations

import hmac
import hashlib


def build_signature(secret: str, payload: bytes) -> str:
    digest = hmac.new(
        str(secret or "").encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


def verify_signature(secret: str, payload: bytes, signature: str | None) -> bool:
    if not secret:
        return True
    if not signature:
        return False
    expected = build_signature(secret, payload)
    return hmac.compare_digest(expected, str(signature).strip())

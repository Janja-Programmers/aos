from __future__ import annotations
import base64, hashlib, json
from typing import Any
from aos.services.ads.errors import AdsValidationError
from aos.services.ads.validation import normalize_identifier, normalize_int, normalize_text


def generate_fingerprint(params: dict[str, Any]) -> str:
    canonical=json.dumps(params,sort_keys=True,separators=(",",":"),ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def encode_cursor(*, modified: Any, public_id: str) -> str:
    raw=json.dumps({"v":1,"modified":str(modified or ""),"public_id":public_id},sort_keys=True,separators=(",",":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(value: Any) -> tuple[str,str]:
    token=normalize_text(value,field="cursor",max_length=500,required=True)
    try:
        data=json.loads(base64.urlsafe_b64decode((token+"="*(-len(token)%4)).encode()).decode())
    except Exception:
        raise AdsValidationError("Invalid saved search cursor.",code="SEARCH_INVALID_FILTERS") from None
    if not isinstance(data,dict) or data.get("v")!=1:
        raise AdsValidationError("Invalid saved search cursor.",code="SEARCH_INVALID_FILTERS")
    return normalize_text(data.get("modified"),field="cursor",max_length=64,required=True), normalize_identifier(data.get("public_id"),field="cursor",required=True)

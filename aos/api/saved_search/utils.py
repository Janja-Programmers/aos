from __future__ import annotations
from typing import Dict, Any
import json
import hashlib


def normalize_search_params(params: Dict[str, Any]) -> Dict[str, Any]:
    clean = {}

    for k, v in (params or {}).items():
        if v in (None, "", []):
            continue

        if isinstance(v, str):
            v = v.strip()

        if k == "q" and isinstance(v, str):
            v = v.lower()

        clean[k] = v

    return clean


def generate_fingerprint(params: Dict[str, Any]) -> str:
    normalized = normalize_search_params(params)
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()

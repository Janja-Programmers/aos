from __future__ import annotations
import base64, hashlib, hmac, json
import frappe
from .errors import ShortsCursorError

def _secret()->bytes:
    value=''
    try: value=str(getattr(frappe.local,'conf',{}).get('encryption_key') or '')
    except Exception: pass
    if not value:
        try: value=str((frappe.get_site_config() or {}).get('encryption_key') or '')
        except Exception: pass
    if not value: raise ShortsCursorError()
    return value.encode()

def encode_cursor(payload:dict)->str:
    raw=json.dumps(payload,separators=(',',':'),sort_keys=True,default=str).encode()
    sig=hmac.new(_secret(),raw,hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(sig+raw).decode().rstrip('=')

def decode_cursor(value)->dict:
    if not value: return {}
    try:
        token=str(value).strip(); raw=base64.urlsafe_b64decode(token+'='*(-len(token)%4)); sig,body=raw[:16],raw[16:]
        if not hmac.compare_digest(sig,hmac.new(_secret(),body,hashlib.sha256).digest()[:16]): raise ValueError
        data=json.loads(body.decode())
        if not isinstance(data,dict): raise ValueError
        return data
    except ShortsCursorError: raise
    except Exception as exc: raise ShortsCursorError() from exc

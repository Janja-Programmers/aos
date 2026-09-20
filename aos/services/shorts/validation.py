"""Strict public Shorts request validation."""
from __future__ import annotations
import json, re
from dataclasses import dataclass
from typing import Any, Mapping
from aos.services.media.identifiers import MEDIA_ID_RE
from aos.services.shorts.identity import SHORT_ID_RE, SOUND_ID_RE
from .errors import ShortsError

ACCOUNT_ID_RE=re.compile(r"^ACC-[A-Z2-7]{20}$")
COMMENT_ID_RE=re.compile(r"^SHC-[A-Z2-7]{20}$")

@dataclass(frozen=True)
class EndpointSpec:
    fields:frozenset[str]
    id_fields:tuple[tuple[str,re.Pattern[str]],...]=()

def validate_public_kwargs(kwargs:Mapping[str,Any],spec:EndpointSpec)->dict[str,Any]:
    clean={k:v for k,v in kwargs.items() if k!='cmd'}
    unknown=sorted(set(clean)-set(spec.fields))
    if unknown: raise ShortsError("Unsupported Shorts request field.",code="SHORTS_UNKNOWN_FIELD",data={"fields":unknown})
    for field,pattern in spec.id_fields:
        value=clean.get(field)
        if value in (None,""): continue
        if not isinstance(value,str) or not pattern.fullmatch(value.strip().upper()):
            raise ShortsError("Invalid public identifier.",code="SHORTS_INVALID_IDENTIFIER",data={"field":field})
        clean[field]=value.strip().upper()
    return clean

def parse_json_list(value:Any,*,field:str,max_items:int)->list[Any]:
    if value in (None,""): return []
    if isinstance(value,str):
        try: value=json.loads(value)
        except Exception as exc: raise ShortsError(f"Invalid {field}.",code="SHORTS_INVALID_REQUEST") from exc
    if not isinstance(value,list) or len(value)>max_items: raise ShortsError(f"Invalid {field}.",code="SHORTS_INVALID_REQUEST")
    return value

def parse_bool(value:Any,default:bool=False)->bool:
    if value in (None,""): return default
    if isinstance(value,bool): return value
    if isinstance(value,(int,float)): return bool(value)
    clean=str(value).strip().lower()
    if clean in {"1","true","yes","on"}: return True
    if clean in {"0","false","no","off"}: return False
    raise ShortsError("Invalid boolean value.",code="SHORTS_INVALID_REQUEST")

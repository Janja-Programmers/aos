"""Auditable automatic content-mode classification for Shorts.

Shop, Geo, Vibes, and Learn are semantic content categories derived from the
Short itself. They are not aliases for Ads, Localization, Maps, or location
metadata. Linked Ads remain optional commerce context and do not determine
mode membership.
"""
from __future__ import annotations
from typing import Any
import frappe
from frappe.utils import now_datetime
from .constants import CONTENT_MODES

_SHOP_TERMS={"shop","shopping","buy","price","product","products","review","unboxing","store","retail","deal","sale","fashion","beauty","electronics"}
_GEO_TERMS={"mountain","mountains","beach","ocean","travel","tourism","landmark","city","street","road","route","map","park","scenery","geography","geographic","country","landscape","nature"}
_LEARN_TERMS={"tutorial","how to","learn","lesson","guide","explain","education","recipe","diy","tips","course","science","technology","language","math","mathematics"}
_VIBES_TERMS={"music","dance","comedy","fashion","lifestyle","trend","fun","culture","vibes","performance"}

def normalize_scores(value:Any)->dict[str,float]:
    value=value if isinstance(value,dict) else {}
    out={}
    for mode in CONTENT_MODES:
        try: score=float(value.get(mode) or 0)
        except (TypeError,ValueError): score=0
        out[mode]=max(0.0,min(score,1.0))
    return out

def classify_short(short_id:str,*,visual_scores:dict[str,float]|None=None,model_version:str='rules-v2',source:str='combined')->list[str]:
    short=frappe.get_doc('AOS Short',short_id)
    text=' '.join([str(short.caption or '')]+[str(r.hashtag or '') for r in frappe.get_all('AOS Short Hashtag',filters={'short':short_id},fields=['hashtag'])]).lower()
    scores=normalize_scores(visual_scores or {})
    modes=set()
    if scores.get('shop',0)>=0.50 or any(term in text for term in _SHOP_TERMS): modes.add('shop')
    if scores.get('geo',0)>=0.50 or any(term in text for term in _GEO_TERMS): modes.add('geo')
    if scores.get('learn',0)>=0.55 or any(term in text for term in _LEARN_TERMS): modes.add('learn')
    if scores.get('vibes',0)>=0.45 or any(term in text for term in _VIBES_TERMS): modes.add('vibes')
    if not modes and source!='failed' and (text or visual_scores): modes.add('vibes')
    existing={r.mode:r.name for r in frappe.get_all('AOS Short Mode',filters={'short':short_id},fields=['name','mode'])}
    for mode,name in existing.items():
        if mode not in modes: frappe.delete_doc('AOS Short Mode',name,ignore_permissions=True)
    now=now_datetime()
    for mode in sorted(modes):
        confidence=float(scores.get(mode) or (0.70 if mode in modes else 0.0))
        if mode in existing:
            frappe.db.set_value('AOS Short Mode',existing[mode],{'classifier_version':model_version,'confidence':confidence,'source':source,'classified_at':now},update_modified=False)
        else:
            frappe.get_doc({'doctype':'AOS Short Mode','short':short_id,'mode':mode,'classifier_version':model_version,'confidence':confidence,'source':source,'classified_at':now}).insert(ignore_permissions=True)
    return sorted(modes)

def apply_visual_result(short_id:str,result:dict[str,Any]|None)->list[str]:
    result=result if isinstance(result,dict) else {}
    status=str(result.get('status') or '').lower()
    return classify_short(short_id,visual_scores=normalize_scores(result.get('scores')),model_version=str(result.get('model_version') or result.get('model') or 'visual-v1')[:140],source='video' if status=='ready' else 'failed')

def reclassify_short(short_id: str, *, visual_scores=None, source: str = "combined", model_version: str | None = None):
    return classify_short(short_id, visual_scores=visual_scores, model_version=model_version or source or "rules-v2", source=source)

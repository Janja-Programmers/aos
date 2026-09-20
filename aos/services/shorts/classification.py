"""Auditable automatic multi-mode classification for Shorts.

Classification is server-owned and may be rerun.  Shop and Geo have hard
trusted-context gates: model scores alone never create those memberships.
"""
from __future__ import annotations
from typing import Any
import frappe
from frappe.utils import now_datetime
from .constants import CONTENT_MODES

_LEARN_TERMS={"tutorial","how to","learn","lesson","guide","explain","education","recipe","diy","tips","course","science","technology","language"}
_VIBES_TERMS={"music","dance","comedy","fashion","lifestyle","trend","fun","culture","vibes","performance"}

def normalize_scores(value:Any)->dict[str,float]:
    value=value if isinstance(value,dict) else {}
    out={}
    for mode in CONTENT_MODES:
        try: score=float(value.get(mode) or 0)
        except (TypeError,ValueError): score=0
        out[mode]=max(0.0,min(score,1.0))
    return out

def _trusted_context(short_id:str)->tuple[bool,bool]:
    has_ad=bool(frappe.db.exists('AOS Short Ad',{'short':short_id}))
    place=frappe.db.get_value('AOS Short',short_id,'place')
    has_geo=bool(place and frappe.db.exists('AOS Location',{'name':place,'is_active':1}))
    return has_ad,has_geo

def classify_short(short_id:str,*,visual_scores:dict[str,float]|None=None,model_version:str='rules-v1',source:str='combined')->list[str]:
    short=frappe.get_doc('AOS Short',short_id)
    text=' '.join([str(short.caption or '')]+[str(r.hashtag or '') for r in frappe.get_all('AOS Short Hashtag',filters={'short':short_id},fields=['hashtag'])]).lower()
    scores=normalize_scores(visual_scores or {})
    modes=set()
    has_ad,has_geo=_trusted_context(short_id)
    if has_ad: modes.add('shop')
    if has_geo: modes.add('geo')
    if scores.get('learn',0)>=0.55 or any(term in text for term in _LEARN_TERMS): modes.add('learn')
    if scores.get('vibes',0)>=0.45 or any(term in text for term in _VIBES_TERMS): modes.add('vibes')
    # Broad creator content may be Vibes, but a complete classifier failure does
    # not need a fabricated mode merely to allow For You distribution.
    if not modes and source!='failed' and (text or visual_scores): modes.add('vibes')
    existing={r.mode:r.name for r in frappe.get_all('AOS Short Mode',filters={'short':short_id},fields=['name','mode'])}
    for mode,name in existing.items():
        if mode not in modes: frappe.delete_doc('AOS Short Mode',name,ignore_permissions=True)
    now=now_datetime()
    for mode in sorted(modes):
        confidence=1.0 if (mode=='shop' and has_ad) or (mode=='geo' and has_geo) else float(scores.get(mode) or (0.7 if mode in {'learn','vibes'} else 0))
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
    """Rerun server-owned classification for the current Short revision."""
    return classify_short(short_id, visual_scores=visual_scores, model_version=model_version or source or "rules-v1", source=source)

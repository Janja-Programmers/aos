"""Production-facing Shorts domain operations.

This module is the single implementation behind ``aos.api.v1.shorts``.  It
owns Shorts state and uses hardened Accounts, Social, Media, Maps, Ads,
Notifications and Moderation contracts instead of duplicating those domains.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any

import frappe
from frappe.utils import now_datetime

from aos.api.shared.responses import ok
from aos.api.shared.rate_limit import rate_limit, rate_limit_key, request_ip
from aos.services.accounts.identity import resolve_account_reference
from aos.services.accounts.serializers import serialize_internal_identity_map
from aos.services.media.media_service import MediaService
from aos.services.notifications.service import NotificationService
from aos.services.marketplace_discovery.ids import resolve_ad_name
from aos.services.social.repository import SocialRepository
from aos.services.localization import validate_location

from .classification import classify_short
from .constants import CAPTION_MAX_LENGTH, CONTENT_MODES, MAX_EVENT_BATCH, MAX_HASHTAGS, MAX_PHOTOS
from .cursor import decode_cursor, encode_cursor
from .errors import ShortsConflictError, ShortsError, ShortsNotFoundError, ShortsPermissionError
from .policy import can_download, can_reuse, can_view, filter_viewable_rows
from .serializers import PUBLIC_FIELDS, serialize_owner_short, serialize_short_rows
from .validation import parse_bool, parse_json_list

_HASHTAG_RE=re.compile(r"^[\w]{1,64}$",re.UNICODE)


def _user(*,required=True)->str:
    user=str(getattr(getattr(frappe,'session',None),'user','Guest') or 'Guest').strip()
    if required and user=='Guest': raise ShortsPermissionError('Authentication is required.')
    return user


def _limit(value,default=20,max_value=50)->int:
    try: n=int(value or default)
    except (TypeError,ValueError): raise ShortsError('Invalid limit.',code='SHORTS_INVALID_REQUEST')
    if n<1 or n>max_value: raise ShortsError('Invalid limit.',code='SHORTS_INVALID_REQUEST')
    return n


def _rate(scope:str,*,user:str|None=None,limit=60,ttl=60):
    response=rate_limit(rate_limit_key('shorts',scope,user or request_ip()),ttl,limit,'Too many Shorts requests. Please try again later.')
    if response: raise ShortsError(response['message'],code='RATE_LIMIT',http_status=429)


def _lock_short(short_id:str):
    rows=frappe.db.sql('SELECT name FROM `tabAOS Short` WHERE name=%s FOR UPDATE',(short_id,))
    if not rows: raise ShortsNotFoundError()
    return frappe.get_doc('AOS Short',short_id)


def _owned_short(short_id:str,user:str,*,lock=False):
    doc=_lock_short(short_id) if lock else (frappe.get_doc('AOS Short',short_id) if frappe.db.exists('AOS Short',short_id) else None)
    if not doc: raise ShortsNotFoundError()
    if str(doc.owner)!=user: raise ShortsPermissionError()
    return doc


def _check_version(doc,version):
    if version not in (None,'') and str(doc.modified or '')!=str(version).strip():
        raise ShortsConflictError('Short changed since it was loaded.',code='SHORTS_VERSION_CONFLICT',data={'version':str(doc.modified or '')})


def _insert_once(doctype:str,payload:dict[str,Any],unique_filters:dict[str,Any]):
    """Insert a unique relation/event and treat a concurrent winner as success."""
    try:
        row=frappe.get_doc({'doctype':doctype,**payload})
        row.insert(ignore_permissions=True)
        return row,True
    except Exception:
        existing=frappe.db.get_value(doctype,unique_filters,'name')
        if not existing:
            raise
        return frappe.get_doc(doctype,existing),False


def _delete_once(doctype:str,unique_filters:dict[str,Any])->bool:
    """Delete an idempotent unique relation without failing on a concurrent delete."""
    existing=frappe.db.get_value(doctype,unique_filters,'name')
    if not existing:
        return False
    try:
        frappe.delete_doc(doctype,existing,ignore_permissions=True)
        return True
    except Exception:
        if frappe.db.exists(doctype,unique_filters):
            raise
        return False


def _insert_short_event_once(payload:dict[str,Any],event_key:str)->bool:
    _,created=_insert_once('AOS Short Event',{**payload,'event_key':event_key},{'event_key':event_key})
    return created


def _normalize_caption(value)->str:
    text=str(value or '').strip()
    if len(text)>CAPTION_MAX_LENGTH: raise ShortsError('Caption is too long.',code='SHORTS_INVALID_REQUEST')
    return text


def _normalize_hashtags(value)->list[str]:
    values=parse_json_list(value,field='hashtags',max_items=MAX_HASHTAGS)
    out=[]
    for raw in values:
        tag=str(raw or '').strip().lstrip('#').casefold().replace(' ','')
        if not tag or not _HASHTAG_RE.fullmatch(tag): raise ShortsError('Invalid hashtag.',code='SHORTS_INVALID_REQUEST')
        if tag not in out: out.append(tag)
    return out


def _account_ids(value)->list[str]:
    vals=parse_json_list(value,field='mention_account_ids',max_items=20)
    out=[]
    for item in vals:
        account=str(item or '').strip().upper(); user=resolve_account_reference(account)
        if not user: raise ShortsError('Invalid mentioned account.',code='SHORTS_INVALID_REQUEST')
        if account not in out: out.append(account)
    return out


def _public_ad_names(value,user:str)->list[tuple[str,str]]:
    ids=parse_json_list(value,field='ad_ids',max_items=8); out=[]
    for public_id in ids:
        public_id=str(public_id or '').strip()
        try: name=resolve_ad_name(public_id)
        except Exception as exc: raise ShortsError('Invalid Ad.',code='SHORTS_INVALID_REQUEST') from exc
        row=frappe.db.get_value('AOS Ad',name,['status','seller','public_id'],as_dict=True)
        seller_user=frappe.db.get_value('AOS Seller',row.seller,'user') if row else None
        if not row or row.status!='Active' or seller_user!=user: raise ShortsPermissionError('Ad is not eligible for this Short.')
        out.append((str(name),str(row.public_id)))
    return out


def _validate_place(value)->str|None:
    place,error=validate_location(value,required=False)
    if error: raise ShortsError('Invalid location.',code='SHORTS_INVALID_REQUEST')
    return place


def _validate_sound(sound_id)->str|None:
    sid=str(sound_id or '').strip().upper()
    if not sid: return None
    row=frappe.db.get_value('AOS Sound',sid,['status','reuse_allowed','available_from','available_until'],as_dict=True)
    now=now_datetime()
    if not row or row.status!='active' or not int(row.reuse_allowed or 0): raise ShortsError('Sound is unavailable.',code='SHORTS_INVALID_REQUEST')
    if row.available_from and row.available_from>now: raise ShortsError('Sound is unavailable.',code='SHORTS_INVALID_REQUEST')
    if row.available_until and row.available_until<=now: raise ShortsError('Sound is unavailable.',code='SHORTS_INVALID_REQUEST')
    return sid


def _replace_hashtags(short_id:str,tags:list[str]):
    existing={r.hashtag:r.name for r in frappe.get_all('AOS Short Hashtag',filters={'short':short_id},fields=['name','hashtag'])}
    for tag,name in existing.items():
        if tag not in tags: frappe.delete_doc('AOS Short Hashtag',name,ignore_permissions=True)
    for tag in tags:
        if tag not in existing: frappe.get_doc({'doctype':'AOS Short Hashtag','short':short_id,'hashtag':tag}).insert(ignore_permissions=True)


def _replace_mentions(short_id:str,accounts:list[str],actor:str,*,comment_id=None,source_type='caption'):
    filters={'short':short_id,'source_type':source_type}
    if comment_id: filters['comment']=comment_id
    existing={r.mentioned_account:r.name for r in frappe.get_all('AOS Short Mention',filters=filters,fields=['name','mentioned_account'])}
    for account,name in existing.items():
        if account not in accounts: frappe.delete_doc('AOS Short Mention',name,ignore_permissions=True)
    for account in accounts:
        if account in existing: continue
        row=frappe.get_doc({'doctype':'AOS Short Mention','short':short_id,'comment':comment_id,'mentioned_account':account,'mentioned_by':actor,'source_type':source_type,'token':account})
        row.insert(ignore_permissions=True)
        target=resolve_account_reference(account)
        if target and target!=actor:
            try: NotificationService.notify_short_mention(user=target,actor=actor,short_id=short_id,comment_id=comment_id,source_type=source_type,event_identity=row.name)
            except Exception: frappe.log_error(frappe.get_traceback(),'Short mention notification failed')


def _replace_ads(short_id:str,ads:list[tuple[str,str]]):
    names=[name for name,_ in ads]
    existing={r.ad:r.name for r in frappe.get_all('AOS Short Ad',filters={'short':short_id},fields=['name','ad'])}
    for name,rowname in existing.items():
        if name not in names: frappe.delete_doc('AOS Short Ad',rowname,ignore_permissions=True)
    for pos,name in enumerate(names):
        if name in existing: frappe.db.set_value('AOS Short Ad',existing[name],'position',pos,update_modified=False)
        else: frappe.get_doc({'doctype':'AOS Short Ad','short':short_id,'ad':name,'position':pos}).insert(ignore_permissions=True)


def _replace_sound(short_id:str,sound_id:str|None):
    existing=frappe.db.get_value('AOS Short Sound',{'short':short_id},['name','sound'],as_dict=True)
    if existing and str(existing.sound)==str(sound_id or ''):
        return
    if existing:
        old_sound=str(existing.sound or '')
        frappe.delete_doc('AOS Short Sound',existing.name,ignore_permissions=True)
        if old_sound:
            frappe.db.sql("UPDATE `tabAOS Sound` SET usage_count=GREATEST(COALESCE(usage_count,0)-1,0) WHERE name=%s",(old_sound,))
    if not sound_id:
        return
    frappe.get_doc({'doctype':'AOS Short Sound','short':short_id,'sound':sound_id,'start_ms':0,'duration_ms':0,'volume':1,'is_original_audio':0}).insert(ignore_permissions=True)
    frappe.db.sql("UPDATE `tabAOS Sound` SET usage_count=COALESCE(usage_count,0)+1 WHERE name=%s",(sound_id,))


def _attach_video(short,user,media_id):
    media=MediaService(); media.validate_media_for_use(media_id=media_id,user=user,purpose='short_video_raw')
    attached=media.attach_media(media_id=media_id,user=user,purpose='short_video_raw',attached_doctype='AOS Short',attached_name=short.name,attached_field='raw_video_media')
    short.raw_video_media=attached.name


def _replace_photos(short,user,media_ids:list[str]):
    media=MediaService(); unique=[]
    for mid in media_ids:
        mid=str(mid or '').strip()
        if mid and mid not in unique: unique.append(mid)
    if not unique or len(unique)>MAX_PHOTOS: raise ShortsError('Photo Short requires 1-10 photos.',code='SHORTS_INVALID_REQUEST')
    current=frappe.get_all('AOS Short Photo',filters={'short':short.name},fields=['name','media','position'])
    current_by_media={str(r.media):r for r in current}
    for mid in unique: media.validate_media_for_use(media_id=mid,user=user,purpose='short_photo',attached_doctype='AOS Short',attached_name=short.name)
    for r in current:
        if str(r.media) not in unique:
            frappe.delete_doc('AOS Short Photo',r.name,ignore_permissions=True)
            media.release_media(media_id=r.media,user=user,attached_doctype='AOS Short',attached_name=short.name)
    for pos,mid in enumerate(unique):
        if mid in current_by_media: frappe.db.set_value('AOS Short Photo',current_by_media[mid].name,'position',pos,update_modified=False)
        else:
            media.attach_media(media_id=mid,user=user,purpose='short_photo',attached_doctype='AOS Short',attached_name=short.name,attached_field='photos')
            frappe.get_doc({'doctype':'AOS Short Photo','short':short.name,'media':mid,'position':pos}).insert(ignore_permissions=True)
    if not short.cover_media or short.cover_media not in unique: short.cover_media=unique[0]


def create_short(**kwargs):
    user=_user(); _rate('create',user=user,limit=20)
    content_type=str(kwargs.get('content_type') or 'Video').strip().title()
    if content_type not in {'Video','Photo'}: raise ShortsError('Invalid content type.')
    caption=_normalize_caption(kwargs.get('caption')); tags=_normalize_hashtags(kwargs.get('hashtags')); mentions=_account_ids(kwargs.get('mention_account_ids')); ads=_public_ad_names(kwargs.get('ad_ids'),user); place=_validate_place(kwargs.get('place_id')); sound=_validate_sound(kwargs.get('sound_id'))
    doc=frappe.get_doc({'doctype':'AOS Short','content_type':content_type,'lifecycle_status':'Draft','processing_status':'Queued' if content_type=='Video' else 'Not Required','moderation_status':'Draft','caption':caption,'audience':str(kwargs.get('audience') or 'everyone').strip().lower(),'allow_comments':1 if parse_bool(kwargs.get('allow_comments'),True) else 0,'allow_downloads':1 if parse_bool(kwargs.get('allow_downloads'),False) else 0,'allow_reuse':1 if parse_bool(kwargs.get('allow_reuse'),True) else 0,'allow_side_by_side':1 if parse_bool(kwargs.get('allow_side_by_side'),True) else 0,'allow_segment_reuse':1 if parse_bool(kwargs.get('allow_segment_reuse'),True) else 0,'place':place})
    doc.insert(ignore_permissions=True)
    if content_type=='Video':
        raw=str(kwargs.get('raw_video_media') or '').strip()
        if not raw: raise ShortsError('Video Short requires raw video Media.',code='SHORTS_INVALID_REQUEST')
        _attach_video(doc,user,raw)
    else: _replace_photos(doc,user,[str(x) for x in parse_json_list(kwargs.get('photo_media_ids'),field='photo_media_ids',max_items=MAX_PHOTOS)])
    _replace_hashtags(doc.name,tags); _replace_mentions(doc.name,mentions,user); _replace_ads(doc.name,ads); _replace_sound(doc.name,sound)
    doc.save(ignore_permissions=True); classify_short(doc.name,source='combined')
    return ok('Short draft created.',serialize_owner_short(doc,viewer=user))


def update_short(**kwargs):
    user=_user(); _rate('update',user=user,limit=40); doc=_owned_short(kwargs.get('short_id'),user,lock=True); _check_version(doc,kwargs.get('version'))
    if doc.lifecycle_status in {'Deleted','Rejected'}: raise ShortsConflictError('Short cannot be edited in its current state.',code='SHORTS_INVALID_STATE')
    changed=False; moderation_sensitive=False
    for field,normalizer in [('caption',_normalize_caption),('place_id',_validate_place)]:
        if field in kwargs:
            attr='place' if field=='place_id' else field
            value=normalizer(kwargs.get(field))
            if getattr(doc,attr,None)!=value:
                setattr(doc,attr,value); changed=True; moderation_sensitive=True
    if 'audience' in kwargs:
        value=str(kwargs.get('audience') or '').strip().lower()
        if doc.audience!=value: doc.audience=value; changed=True
    for arg,attr,default in [('allow_comments','allow_comments',True),('allow_downloads','allow_downloads',False),('allow_reuse','allow_reuse',True),('allow_side_by_side','allow_side_by_side',True),('allow_segment_reuse','allow_segment_reuse',True)]:
        if arg in kwargs:
            value=1 if parse_bool(kwargs.get(arg),default) else 0
            if int(getattr(doc,attr,0) or 0)!=value: setattr(doc,attr,value); changed=True
    if 'hashtags' in kwargs:
        new_tags=_normalize_hashtags(kwargs.get('hashtags'))
        old_tags=[str(r.hashtag) for r in frappe.get_all('AOS Short Hashtag',filters={'short':doc.name},fields=['hashtag'],order_by='hashtag asc')]
        if sorted(old_tags)!=sorted(new_tags): _replace_hashtags(doc.name,new_tags); changed=True; moderation_sensitive=True
    if 'mention_account_ids' in kwargs:
        new_mentions=_account_ids(kwargs.get('mention_account_ids'))
        old_mentions=[str(r.mentioned_account) for r in frappe.get_all('AOS Short Mention',filters={'short':doc.name,'source_type':'caption'},fields=['mentioned_account'])]
        if sorted(old_mentions)!=sorted(new_mentions): _replace_mentions(doc.name,new_mentions,user); changed=True; moderation_sensitive=True
    if 'ad_ids' in kwargs:
        new_ads=_public_ad_names(kwargs.get('ad_ids'),user); new_ad_names=[name for name,_ in new_ads]
        old_ads=[str(r.ad) for r in frappe.get_all('AOS Short Ad',filters={'short':doc.name},fields=['ad'],order_by='position asc,name asc')]
        if old_ads!=new_ad_names: _replace_ads(doc.name,new_ads); changed=True; moderation_sensitive=True
    if 'sound_id' in kwargs:
        if doc.content_type=='Video' and doc.lifecycle_status not in {'Draft','Failed'}: raise ShortsConflictError('Sound can only change before resubmission.',code='SHORTS_INVALID_STATE')
        before=frappe.db.get_value('AOS Short Sound',{'short':doc.name},'sound')
        after=_validate_sound(kwargs.get('sound_id'))
        if str(before or '')!=str(after or ''):
            _replace_sound(doc.name,after)
            if doc.content_type=='Video' and doc.processing_status=='Ready':
                doc.processing_generation=int(doc.processing_generation or 0)+1
                doc.processing_status='Queued'; doc.playback_media=None; doc.playback_manifest_media=None; doc.download_media=None; doc.storyboard_media=None; doc.storyboard_manifest_media=None
            changed=True; moderation_sensitive=True
    if 'photo_media_ids' in kwargs:
        if doc.content_type!='Photo' or doc.lifecycle_status not in {'Draft','Failed'}: raise ShortsConflictError('Photos cannot change now.',code='SHORTS_INVALID_STATE')
        _replace_photos(doc,user,[str(x) for x in parse_json_list(kwargs.get('photo_media_ids'),field='photo_media_ids',max_items=MAX_PHOTOS)]); changed=True; moderation_sensitive=True
    if 'cover_media_id' in kwargs:
        cover=str(kwargs.get('cover_media_id') or '').strip()
        allowed={str(r.media) for r in frappe.get_all('AOS Short Photo',filters={'short':doc.name},fields=['media'])} if doc.content_type=='Photo' else {str(doc.poster_media or '')}
        if cover not in allowed: raise ShortsError('Invalid Short cover.',code='SHORTS_INVALID_REQUEST')
        if str(doc.cover_media or '')!=cover: doc.cover_media=cover; changed=True; moderation_sensitive=True
    if changed:
        doc.revision=int(doc.revision or 0)+1
        if doc.lifecycle_status in {'Pending Review','Hidden','Failed'} or (doc.lifecycle_status=='Published' and moderation_sensitive):
            doc.lifecycle_status='Draft'; doc.moderation_status='Draft'; doc.moderation_reason=None; doc.moderation_generation=int(doc.moderation_generation or 0)+1
            doc.moderation_decided_by=None; doc.moderation_decided_at=None
        doc.save(ignore_permissions=True); classify_short(doc.name,source='combined')
    return ok('Short updated.',serialize_owner_short(doc,viewer=user))


def submit_short(**kwargs):
    user=_user(); _rate('submit',user=user,limit=15); doc=_owned_short(kwargs.get('short_id'),user,lock=True); _check_version(doc,kwargs.get('version'))
    if doc.lifecycle_status=='Published': return ok('Short is already published.',serialize_owner_short(doc,viewer=user))
    if doc.lifecycle_status=='Pending Review' and doc.moderation_status=='Pending': return ok('Short is already pending review.',serialize_owner_short(doc,viewer=user))
    if doc.lifecycle_status in {'Deleted','Rejected'}: raise ShortsConflictError('Short cannot be submitted.',code='SHORTS_INVALID_STATE')
    if doc.content_type=='Photo' and not frappe.db.exists('AOS Short Photo',{'short':doc.name}): raise ShortsConflictError('Photo Short is incomplete.',code='SHORTS_INVALID_STATE')
    classify_short(doc.name,source='combined')
    if doc.content_type=='Video' and doc.processing_status!='Ready':
        from .processing import enqueue_processing_job
        job=enqueue_processing_job(short=doc,operation='Process',idempotency_key=str(kwargs.get('idempotency_key') or '').strip() or None)
        doc.reload(); return ok('Short processing queued.',{'short':serialize_owner_short(doc,viewer=user),'processing_job_id':job.name})
    _begin_moderation(doc)
    return ok('Short submitted for review.',serialize_owner_short(doc,viewer=user))


def _begin_moderation(doc):
    doc.lifecycle_status='Pending Review'; doc.moderation_status='Pending'; doc.moderation_generation=int(doc.moderation_generation or 0)+1; doc.moderation_reason=None; doc.save(ignore_permissions=True)
    from aos.services.moderation_service import enqueue_short_moderation
    enqueue_short_moderation(doc.name,source='short_submit')


def delete_short(**kwargs):
    user=_user(); _rate('delete',user=user,limit=20); doc=_owned_short(kwargs.get('short_id'),user,lock=True); _check_version(doc,kwargs.get('version'))
    if doc.lifecycle_status!='Deleted':
        doc.lifecycle_status='Deleted'; doc.processing_status='Cancelled' if doc.processing_status in {'Queued','Processing','Retry Waiting'} else doc.processing_status; doc.moderation_status='Hidden'; doc.moderation_generation=int(doc.moderation_generation or 0)+1; doc.deleted_at=now_datetime(); doc.save(ignore_permissions=True)
    return ok('Short deleted.',{'id':doc.name,'lifecycle_status':'Deleted'})


def retry_processing(**kwargs):
    user=_user(); _rate('retry',user=user,limit=10); doc=_owned_short(kwargs.get('short_id'),user,lock=True)
    if doc.content_type!='Video' or doc.processing_status!='Failed' or doc.lifecycle_status=='Deleted': raise ShortsConflictError('Short is not eligible for retry.',code='SHORTS_INVALID_STATE')
    from .processing import enqueue_processing_job
    job=enqueue_processing_job(short=doc,operation='Process',idempotency_key=str(kwargs.get('idempotency_key') or '').strip() or None,force_new_generation=True)
    return ok('Processing retry queued.',{'short_id':doc.name,'processing_job_id':job.name})


def get_short(**kwargs):
    viewer=_user(required=False); sid=str(kwargs.get('short_id') or '')
    if not frappe.db.exists('AOS Short',sid): raise ShortsNotFoundError()
    doc=frappe.get_doc('AOS Short',sid)
    if viewer==doc.owner: return ok('Short loaded.',serialize_owner_short(doc,viewer=viewer))
    row={f:getattr(doc,f,None) for f in PUBLIC_FIELDS}
    if not can_view(row,viewer=viewer): raise ShortsNotFoundError()
    return ok('Short loaded.',serialize_short_rows([row],viewer=None if viewer=='Guest' else viewer)[0])


def my_shorts(**kwargs):
    user=_user(); limit=_limit(kwargs.get('limit')); status=str(kwargs.get('status') or '').strip(); cursor=decode_cursor(kwargs.get('cursor')); filters={'owner':user}
    if status: filters['lifecycle_status']=status
    if cursor.get('created') and cursor.get('id'):
        rows=frappe.db.sql('''SELECT * FROM `tabAOS Short` WHERE owner=%(user)s %(status)s AND (creation < %(created)s OR (creation=%(created)s AND name < %(id)s)) ORDER BY creation DESC,name DESC LIMIT %(limit)s'''.replace('%(status)s','AND lifecycle_status=%(lifecycle)s' if status else ''),{'user':user,'lifecycle':status,'created':cursor.get('created'),'id':cursor.get('id'),'limit':limit+1},as_dict=True)
    else:
        rows=frappe.get_all('AOS Short',filters=filters,fields=['*'],order_by='creation desc,name desc',limit=limit+1)
    more=len(rows)>limit; rows=rows[:limit]; items=[serialize_owner_short(frappe.get_doc('AOS Short',r.name),viewer=user) for r in rows]
    nxt=encode_cursor({'created':str(rows[-1].creation),'id':rows[-1].name}) if more and rows else None
    return ok('Shorts loaded.',{'items':items,'next_cursor':nxt})


def _candidate_rows(*,mode:str|None,viewer:str|None,pool=360):
    params={'limit':pool}; mode_clause=''
    if mode:
        mode_clause='AND EXISTS (SELECT 1 FROM `tabAOS Short Mode` sm WHERE sm.short=s.name AND sm.mode=%(mode)s)'
        if mode=='shop':
            mode_clause += " AND EXISTS (SELECT 1 FROM `tabAOS Short Ad` sa INNER JOIN `tabAOS Ad` a ON a.name=sa.ad WHERE sa.short=s.name AND a.status='Active')"
        elif mode=='geo':
            mode_clause += " AND s.place IS NOT NULL AND EXISTS (SELECT 1 FROM `tabAOS Location` loc WHERE loc.name=s.place AND loc.is_active=1)"
        params['mode']=mode
    feedback=''
    if viewer:
        feedback='AND NOT EXISTS (SELECT 1 FROM `tabAOS Short Feedback` sf WHERE sf.short=s.name AND sf.user=%(viewer)s AND sf.feedback_type=\'not_interested\')'; params['viewer']=viewer
    return frappe.db.sql(f'''SELECT s.* FROM `tabAOS Short` s WHERE s.lifecycle_status='Published' AND s.moderation_status='Approved' AND s.processing_status IN ('Ready','Not Required') {mode_clause} {feedback} ORDER BY s.ranking_score DESC,s.posted_on DESC,s.name DESC LIMIT %(limit)s''',params,as_dict=True)


def _personalize(rows,user):
    if not user: return rows
    affinity=frappe.db.sql('''SELECT s.owner,COUNT(*) n FROM `tabAOS Short Like` l INNER JOIN `tabAOS Short` s ON s.name=l.short WHERE l.user=%s GROUP BY s.owner ORDER BY n DESC LIMIT 40''',(user,),as_dict=True)
    creator_boost={str(r.owner):min(int(r.n or 0),5)*0.12 for r in affinity}
    scored=[]
    for r in rows:
        base=float(r.ranking_score or 0); rec=creator_boost.get(str(r.owner),0); scored.append((base+rec,r))
    scored.sort(key=lambda x:(x[0],str(x[1].posted_on or ''),str(x[1].name)),reverse=True)
    # bounded creator diversity: at most two consecutive items per creator.
    pending=[r for _,r in scored]; out=[]; last=None; streak=0
    while pending:
        pick=0
        if last and streak>=2:
            alt=next((i for i,r in enumerate(pending) if str(r.owner)!=last),None)
            if alt is not None: pick=alt
        r=pending.pop(pick); owner=str(r.owner); streak=streak+1 if owner==last else 1; last=owner; out.append(r)
    return out


def feed_for_you(**kwargs):
    viewer=_user(required=False); user=None if viewer=='Guest' else viewer; _rate('feed_foryou',user=user,limit=180)
    limit=_limit(kwargs.get('limit'),10,30); mode=str(kwargs.get('mode') or '').strip().lower() or None
    if mode and mode not in CONTENT_MODES: raise ShortsError('Invalid Content Mode.',code='SHORTS_INVALID_REQUEST')
    cursor=decode_cursor(kwargs.get('cursor')); cache=frappe.cache(); session=str(cursor.get('session') or kwargs.get('session_id') or uuid.uuid4().hex[:24]); key=f'aos:shorts:feed:{user or request_ip()}:{session}'
    ordered=cache.get_value(key)
    if not isinstance(ordered,list):
        candidates=filter_viewable_rows(_candidate_rows(mode=mode,viewer=user),viewer=user)
        ordered=[str(r.name) for r in _personalize(candidates,user)][:180]
        cache.set_value(key,ordered,expires_in_sec=20*60)
    after=str(cursor.get('after') or '')
    start=(ordered.index(after)+1) if after in ordered else 0
    ids=ordered[start:start+limit+1]; page_ids=ids[:limit]
    if not page_ids: return ok('For You feed loaded.',{'items':[],'next_cursor':None,'session_id':session})
    rows=frappe.get_all('AOS Short',filters={'name':['in',page_ids]},fields=['*'],limit=len(page_ids)); by={r.name:r for r in rows}; page=[by[x] for x in page_ids if x in by]
    nxt=encode_cursor({'kind':'fyp','session':session,'after':page[-1].name}) if len(ids)>limit and page else None
    return ok('For You feed loaded.',{'items':serialize_short_rows(page,viewer=user),'next_cursor':nxt,'session_id':session})


def feed_following(**kwargs):
    user=_user(); _rate('feed_following',user=user,limit=180); limit=_limit(kwargs.get('limit'),10,30); mode=str(kwargs.get('mode') or '').strip().lower() or None
    if mode and mode not in CONTENT_MODES: raise ShortsError('Invalid Content Mode.',code='SHORTS_INVALID_REQUEST')
    cur=decode_cursor(kwargs.get('cursor')); params={'user':user,'limit':limit*4+1}; clauses=[]
    if mode:
        mode_clause='EXISTS (SELECT 1 FROM `tabAOS Short Mode` sm WHERE sm.short=s.name AND sm.mode=%(mode)s)'
        if mode=='shop':
            mode_clause += " AND EXISTS (SELECT 1 FROM `tabAOS Short Ad` sa INNER JOIN `tabAOS Ad` a ON a.name=sa.ad WHERE sa.short=s.name AND a.status='Active')"
        elif mode=='geo':
            mode_clause += " AND s.place IS NOT NULL AND EXISTS (SELECT 1 FROM `tabAOS Location` loc WHERE loc.name=s.place AND loc.is_active=1)"
        clauses.append(mode_clause); params['mode']=mode
    if cur.get('posted') and cur.get('id'): clauses.append('(s.posted_on < %(posted)s OR (s.posted_on=%(posted)s AND s.name < %(id)s))'); params.update({'posted':cur['posted'],'id':cur['id']})
    extra=' AND '.join(clauses); extra=(' AND '+extra) if extra else ''
    rows=frappe.db.sql(f'''SELECT s.* FROM `tabAOS Short` s INNER JOIN `tabAOS Follow` f ON f.following_user=s.owner AND f.follower_user=%(user)s WHERE s.lifecycle_status='Published' AND s.moderation_status='Approved' AND s.processing_status IN ('Ready','Not Required'){extra} ORDER BY s.posted_on DESC,s.name DESC LIMIT %(limit)s''',params,as_dict=True)
    visible=filter_viewable_rows(rows,viewer=user,limit=limit+1); more=len(visible)>limit; page=visible[:limit]; nxt=encode_cursor({'posted':str(page[-1].posted_on),'id':page[-1].name}) if more and page else None
    return ok('Following feed loaded.',{'items':serialize_short_rows(page,viewer=user),'next_cursor':nxt})


def _reaction(doctype,counter,short_id,*,add:bool):
    user=_user(); _rate('reaction',user=user,limit=120); short=frappe.db.get_value('AOS Short',short_id,['name','owner','lifecycle_status','processing_status','moderation_status','audience'],as_dict=True)
    if not short or not can_view(short,viewer=user): raise ShortsNotFoundError()
    existing=frappe.db.get_value(doctype,{'short':short_id,'user':user},'name')
    changed=False
    if add and not existing:
        try: row=frappe.get_doc({'doctype':doctype,'short':short_id,'user':user}); row.insert(ignore_permissions=True); existing=row.name; changed=True
        except Exception:
            existing=frappe.db.get_value(doctype,{'short':short_id,'user':user},'name')
            if not existing: raise
    elif not add and existing: changed=_delete_once(doctype,{'short':short_id,'user':user})
    if doctype=='AOS Short Like' and add and changed and user!=short.owner:
        try: NotificationService.notify_short_like(user=short.owner,actor=user,short_id=short_id,event_identity=str(existing))
        except Exception: frappe.log_error(frappe.get_traceback(),'Short like notification failed')
    count=int(frappe.db.get_value('AOS Short',short_id,counter) or 0)
    return ok('Short reaction updated.',{'short_id':short_id,'active':bool(add),'changed':changed,'count':count})

def like_short(**kw): return _reaction('AOS Short Like','like_count',kw.get('short_id'),add=True)
def unlike_short(**kw): return _reaction('AOS Short Like','like_count',kw.get('short_id'),add=False)
def save_short(**kw): return _reaction('AOS Short Save','save_count',kw.get('short_id'),add=True)
def unsave_short(**kw): return _reaction('AOS Short Save','save_count',kw.get('short_id'),add=False)
def repost_short(**kw):
    response=_reaction('AOS Short Repost','repost_count',kw.get('short_id'),add=True)
    if response['data'].get('changed') and kw.get('note'): frappe.db.set_value('AOS Short Repost',{'short':kw.get('short_id'),'user':_user()},'note',str(kw.get('note'))[:500])
    return response
def undo_repost_short(**kw): return _reaction('AOS Short Repost','repost_count',kw.get('short_id'),add=False)


def saved_shorts(**kwargs):
    user=_user(); limit=_limit(kwargs.get('limit')); cur=decode_cursor(kwargs.get('cursor')); params={'user':user,'limit':limit*3+1}; clause=''
    if cur.get('created') and cur.get('id'): clause='AND (sv.creation < %(created)s OR (sv.creation=%(created)s AND sv.name < %(id)s))'; params.update({'created':cur['created'],'id':cur['id']})
    rows=frappe.db.sql(f'''SELECT s.*,sv.creation AS saved_at,sv.name AS saved_row FROM `tabAOS Short Save` sv INNER JOIN `tabAOS Short` s ON s.name=sv.short WHERE sv.user=%(user)s {clause} ORDER BY sv.creation DESC,sv.name DESC LIMIT %(limit)s''',params,as_dict=True)
    visible=filter_viewable_rows(rows,viewer=user,limit=limit+1); more=len(visible)>limit; page=visible[:limit]; nxt=encode_cursor({'created':str(page[-1].saved_at),'id':page[-1].saved_row}) if more and page else None
    return ok('Saved Shorts loaded.',{'items':serialize_short_rows(page,viewer=user),'next_cursor':nxt})


def not_interested(**kwargs):
    user=_user(); _rate('not_interested',user=user,limit=120); sid=kwargs.get('short_id'); short=frappe.db.get_value('AOS Short',sid,['name','owner','lifecycle_status','processing_status','moderation_status','audience'],as_dict=True)
    if not short or not can_view(short,viewer=user): raise ShortsNotFoundError()
    _insert_once('AOS Short Feedback',{'short':sid,'user':user,'feedback_type':'not_interested'},{'short':sid,'user':user,'feedback_type':'not_interested'})
    return ok('Feedback recorded.',{'short_id':sid,'feedback':'not_interested'})


def _serialize_comments(rows,viewer):
    users=sorted({str(r.user) for r in rows}); ids=serialize_internal_identity_map(users); comment_ids=[str(r.name) for r in rows]; liked=set()
    if viewer and viewer!='Guest': liked={str(x.comment) for x in frappe.get_all('AOS Short Comment Like',filters={'user':viewer,'comment':['in',comment_ids]},fields=['comment'],limit=max(1,len(comment_ids)))}
    return [{'id':r.name,'short_id':r.short,'comment':r.comment,'parent_comment_id':r.parent_comment or None,'root_comment_id':r.root_comment or None,'reply_count':int(r.reply_count or 0),'like_count':int(r.like_count or 0),'liked':r.name in liked,'created_at':r.creation,'author':{'account_id':ids.get(str(r.user),{}).get('account_id'),'display_name':ids.get(str(r.user),{}).get('display_name') or 'AOS User','avatar':ids.get(str(r.user),{}).get('avatar')}} for r in rows]


def create_comment(**kwargs):
    user=_user(); _rate('comment',user=user,limit=60); body=str(kwargs.get('comment') or '').strip(); parent=str(kwargs.get('parent_comment_id') or '').strip() or None
    if not body or len(body)>500: raise ShortsError('Invalid comment.',code='SHORTS_INVALID_REQUEST')
    row=frappe.get_doc({'doctype':'AOS Short Comment','short':kwargs.get('short_id'),'user':user,'parent_comment':parent,'comment':body,'status':'active'}); row.insert(ignore_permissions=True)
    short_owner=frappe.db.get_value('AOS Short',row.short,'owner')
    try:
        if parent:
            parent_user=frappe.db.get_value('AOS Short Comment',parent,'user')
            if parent_user and parent_user!=user: NotificationService.notify_comment_reply(user=parent_user,actor=user,comment_id=parent,short_id=row.short,content=body,event_identity=row.name)
        elif short_owner and short_owner!=user: NotificationService.notify_short_comment(user=short_owner,actor=user,short_id=row.short,content=body,event_identity=row.name)
    except Exception: frappe.log_error(frappe.get_traceback(),'Short comment notification failed')
    return ok('Comment created.',_serialize_comments([row],user)[0])


def delete_comment(**kwargs):
    user=_user(); _rate('comment_delete',user=user,limit=60); cid=str(kwargs.get('comment_id') or ''); row=frappe.get_doc('AOS Short Comment',cid) if frappe.db.exists('AOS Short Comment',cid) else None
    if not row: raise ShortsNotFoundError('Comment unavailable.')
    if row.user!=user and not frappe.has_permission('AOS Short Comment','delete',doc=row,user=user): raise ShortsPermissionError()
    row.soft_delete(); return ok('Comment deleted.',{'comment_id':cid})


def list_comments(**kwargs):
    viewer=_user(required=False); user=None if viewer=='Guest' else viewer; _rate('comment_list',user=user,limit=180); sid=kwargs.get('short_id'); short=frappe.db.get_value('AOS Short',sid,['name','owner','lifecycle_status','processing_status','moderation_status','audience'],as_dict=True)
    if not short or not can_view(short,viewer=user): raise ShortsNotFoundError()
    limit=_limit(kwargs.get('limit')); cur=decode_cursor(kwargs.get('cursor')); params={'short':sid,'limit':limit+1}; clause=''
    if cur.get('created') and cur.get('id'): clause='AND (creation < %(created)s OR (creation=%(created)s AND name < %(id)s))'; params.update({'created':cur['created'],'id':cur['id']})
    rows=frappe.db.sql(f'''SELECT * FROM `tabAOS Short Comment` WHERE short=%(short)s AND status='active' AND parent_comment IS NULL {clause} ORDER BY creation DESC,name DESC LIMIT %(limit)s''',params,as_dict=True); more=len(rows)>limit; page=rows[:limit]; nxt=encode_cursor({'created':str(page[-1].creation),'id':page[-1].name}) if more and page else None
    return ok('Comments loaded.',{'items':_serialize_comments(page,user),'next_cursor':nxt})


def list_comment_replies(**kwargs):
    viewer=_user(required=False); user=None if viewer=='Guest' else viewer; _rate('comment_list',user=user,limit=180); cid=str(kwargs.get('comment_id') or '').strip(); root=frappe.db.get_value('AOS Short Comment',cid,['name','short','root_comment','status'],as_dict=True)
    if not root or root.status!='active': raise ShortsNotFoundError('Comment unavailable.')
    root_id=str(root.root_comment or root.name)
    root_row=frappe.db.get_value('AOS Short Comment',root_id,['name','short','status'],as_dict=True)
    if not root_row or root_row.status!='active': raise ShortsNotFoundError('Comment unavailable.')
    short=frappe.db.get_value('AOS Short',root_row.short,['name','owner','lifecycle_status','processing_status','moderation_status','audience'],as_dict=True)
    if not short or not can_view(short,viewer=user): raise ShortsNotFoundError()
    limit=_limit(kwargs.get('limit')); cur=decode_cursor(kwargs.get('cursor')); params={'short':root_row.short,'root':root_id,'limit':limit+1}; clause=''
    if cur.get('created') and cur.get('id'): clause='AND (creation > %(created)s OR (creation=%(created)s AND name > %(id)s))'; params.update({'created':cur['created'],'id':cur['id']})
    rows=frappe.db.sql(f'''SELECT * FROM `tabAOS Short Comment` WHERE short=%(short)s AND root_comment=%(root)s AND name<>%(root)s AND status='active' {clause} ORDER BY creation ASC,name ASC LIMIT %(limit)s''',params,as_dict=True); more=len(rows)>limit; page=rows[:limit]; nxt=encode_cursor({'created':str(page[-1].creation),'id':page[-1].name}) if more and page else None
    return ok('Comment replies loaded.',{'root_comment_id':root_id,'items':_serialize_comments(page,user),'next_cursor':nxt})


def _comment_like(comment_id,add):
    user=_user(); _rate('comment_like',user=user,limit=120); existing=frappe.db.get_value('AOS Short Comment Like',{'comment':comment_id,'user':user},'name'); changed=False
    if add and not existing:
        try: row=frappe.get_doc({'doctype':'AOS Short Comment Like','comment':comment_id,'user':user}); row.insert(ignore_permissions=True); changed=True
        except Exception:
            if not frappe.db.exists('AOS Short Comment Like',{'comment':comment_id,'user':user}): raise
    elif not add and existing: changed=_delete_once('AOS Short Comment Like',{'comment':comment_id,'user':user})
    count=int(frappe.db.get_value('AOS Short Comment',comment_id,'like_count') or 0)
    return ok('Comment reaction updated.',{'comment_id':comment_id,'liked':add,'changed':changed,'like_count':count})
def like_comment(**kw): return _comment_like(str(kw.get('comment_id') or ''),True)
def unlike_comment(**kw): return _comment_like(str(kw.get('comment_id') or ''),False)


def record_events(**kwargs):
    viewer=_user(required=False); user=None if viewer=='Guest' else viewer; _rate('events',user=user,limit=240)
    events=parse_json_list(kwargs.get('events'),field='events',max_items=MAX_EVENT_BATCH); session=str(kwargs.get('session_id') or '').strip()[:128]
    if not user and not session: raise ShortsError('session_id is required for guest events.',code='SHORTS_INVALID_REQUEST')
    prepared=[]; ids=set()
    allowed_types={'impression','playback_start','qualified_view','watch','complete','rewatch','early_skip','follow_from_content'}
    for event in events:
        if not isinstance(event,dict): continue
        sid=str(event.get('short_id') or '').strip(); typ=str(event.get('type') or '').strip().lower(); event_id=str(event.get('event_id') or '').strip()[:128]
        if not sid or typ not in allowed_types or not event_id: continue
        ids.add(sid); prepared.append((sid,typ,event_id,event))
    if not prepared: return ok('Events accepted.',{'accepted':0})
    rows=frappe.get_all('AOS Short',filters={'name':['in',sorted(ids)]},fields=['name','owner','lifecycle_status','processing_status','moderation_status','audience'],limit=max(1,len(ids)))
    visible={str(row['name']) for row in filter_viewable_rows([dict(r) for r in rows],viewer=user)}
    cache=frappe.cache(); accepted=0; actor_key=user or f'guest:{session}'
    from .hot_metrics import record_signal
    durable={'qualified_view','complete','rewatch','early_skip','follow_from_content'}
    for sid,typ,event_id,event in prepared:
        if sid not in visible: continue
        dedupe=hashlib.sha256(f'{sid}|{actor_key}|{typ}|{event_id}'.encode()).hexdigest(); dedupe_key=f'aos:shorts:event:{dedupe}'
        if not cache.set(dedupe_key,'1',ex=7*86400,nx=True): continue
        try:
            watch=max(0,min(int(event.get('watch_ms') or 0),600000)); unique_new=False
            if typ in durable:
                created=_insert_short_event_once({'short':sid,'user':user,'session_id':session,'event_type':typ,'watch_ms':watch,'progress_ms':max(0,min(int(event.get('progress_ms') or 0),600000)),'source':str(event.get('source') or '')[:120],'metadata':event.get('metadata') if isinstance(event.get('metadata'),dict) else None},dedupe)
                if not created: continue
            if typ=='qualified_view':
                identity=hashlib.sha256(f'{sid}|{actor_key}'.encode()).hexdigest()
                existing=frappe.db.get_value('AOS Short View',{'short':sid,'identity_key':identity},'name')
                if not existing:
                    _,unique_new=_insert_once('AOS Short View',{'short':sid,'user':user,'session_id':session if not user else None,'view_date':now_datetime().date(),'qualified':1,'watch_ms':0,'last_seen_at':now_datetime(),'identity_key':identity},{'short':sid,'identity_key':identity})
                else:
                    frappe.db.set_value('AOS Short View',existing,'last_seen_at',now_datetime(),update_modified=False)
            record_signal(sid,typ,watch_ms=watch,unique_new=unique_new); accepted+=1
        except Exception:
            cache.delete_value(dedupe_key)
            raise
    return ok('Events accepted.',{'accepted':accepted})


def record_share(**kwargs):
    viewer=_user(required=False); user=None if viewer=='Guest' else viewer; _rate('share',user=user,limit=180); sid=kwargs.get('short_id'); event_id=str(kwargs.get('event_id') or '').strip()[:128]
    if not event_id: raise ShortsError('event_id is required.',code='SHORTS_INVALID_REQUEST')
    short=frappe.db.get_value('AOS Short',sid,['name','owner','lifecycle_status','processing_status','moderation_status','audience'],as_dict=True)
    if not short or not can_view(short,viewer=user): raise ShortsNotFoundError()
    actor=user or f'guest:{hashlib.sha256(request_ip().encode()).hexdigest()[:32]}'
    key=hashlib.sha256(f'share|{sid}|{actor}|{event_id}'.encode()).hexdigest()
    created=_insert_short_event_once({'short':sid,'user':user,'session_id':'' if user else actor,'event_type':'share','source':str(kwargs.get('channel') or '')[:120]},key)
    if created: frappe.db.sql('UPDATE `tabAOS Short` SET share_count=COALESCE(share_count,0)+1,last_engagement_at=NOW() WHERE name=%s',(sid,))
    return ok('Share recorded.',{'short_id':sid})


def _sound_payload(rows,viewer=None):
    mids=[str(r.sound_media) for r in rows if r.sound_media]; urls=MediaService().get_public_url_map(mids); favorite=set()
    if viewer: favorite={str(x.sound) for x in frappe.get_all('AOS Sound Favorite',filters={'user':viewer,'sound':['in',[r.name for r in rows]]},fields=['sound'],limit=max(1,len(rows)))}
    return [{'id':r.name,'title':r.title,'artist':r.artist or '','source_type':r.source_type,'duration_seconds':float(r.duration_seconds or 0),'url':urls.get(str(r.sound_media)),'reuse_allowed':bool(int(r.reuse_allowed or 0)),'usage_count':int(r.usage_count or 0),'favorite_count':int(r.favorite_count or 0),'favorited':r.name in favorite} for r in rows]

def _sound_available_clause(alias='s'):
    return f"{alias}.status='active' AND {alias}.reuse_allowed=1 AND ({alias}.available_from IS NULL OR {alias}.available_from<=NOW()) AND ({alias}.available_until IS NULL OR {alias}.available_until>NOW())"

def list_sounds(**kwargs):
    viewer=_user(required=False); user=None if viewer=='Guest' else viewer; _rate('sound_list',user=user,limit=120); limit=_limit(kwargs.get('limit')); cur=decode_cursor(kwargs.get('cursor')); params={'limit':limit+1}; clause=''
    if cur.get('created') and cur.get('id'): clause='AND (creation < %(created)s OR (creation=%(created)s AND name < %(id)s))'; params.update({'created':cur['created'],'id':cur['id']})
    rows=frappe.db.sql(f'''SELECT * FROM `tabAOS Sound` s WHERE {_sound_available_clause()} {clause} ORDER BY creation DESC,name DESC LIMIT %(limit)s''',params,as_dict=True); more=len(rows)>limit; page=rows[:limit]; nxt=encode_cursor({'created':str(page[-1].creation),'id':page[-1].name}) if more and page else None
    return ok('Sounds loaded.',{'items':_sound_payload(page,user),'next_cursor':nxt})

def search_sounds(**kwargs):
    viewer=_user(required=False); user=None if viewer=='Guest' else viewer; _rate('sound_search',user=user,limit=120); q=str(kwargs.get('q') or '').strip()[:100]; limit=_limit(kwargs.get('limit')); cur=decode_cursor(kwargs.get('cursor'))
    if not q: return ok('Sound search complete.',{'items':[],'next_cursor':None})
    params={'q':f'%{q}%','limit':limit+1}; clause=''
    if cur.get('usage') is not None and cur.get('created') and cur.get('id'):
        clause='AND (COALESCE(usage_count,0) < %(usage)s OR (COALESCE(usage_count,0)=%(usage)s AND (creation < %(created)s OR (creation=%(created)s AND name < %(id)s))))'; params.update({'usage':int(cur['usage']),'created':cur['created'],'id':cur['id']})
    rows=frappe.db.sql(f'''SELECT * FROM `tabAOS Sound` s WHERE {_sound_available_clause()} AND (title LIKE %(q)s OR artist LIKE %(q)s) {clause} ORDER BY COALESCE(usage_count,0) DESC,creation DESC,name DESC LIMIT %(limit)s''',params,as_dict=True); more=len(rows)>limit; page=rows[:limit]; nxt=encode_cursor({'usage':int(page[-1].usage_count or 0),'created':str(page[-1].creation),'id':page[-1].name}) if more and page else None
    return ok('Sound search complete.',{'items':_sound_payload(page,user),'next_cursor':nxt})

def get_sound(**kwargs):
    viewer=_user(required=False); sid=kwargs.get('sound_id'); row=frappe.db.sql(f'''SELECT * FROM `tabAOS Sound` s WHERE s.name=%s AND {_sound_available_clause()} LIMIT 1''',(sid,),as_dict=True)
    if not row: raise ShortsNotFoundError('Sound unavailable.')
    return ok('Sound loaded.',_sound_payload(row,None if viewer=='Guest' else viewer)[0])

def _sound_fav(sid,add):
    user=_user(); _rate('sound_favorite',user=user,limit=120)
    if not frappe.db.exists('AOS Sound',{'name':sid,'status':'active'}): raise ShortsNotFoundError('Sound unavailable.')
    existing=frappe.db.get_value('AOS Sound Favorite',{'sound':sid,'user':user},'name'); changed=False
    if add and not existing:
        try: frappe.get_doc({'doctype':'AOS Sound Favorite','sound':sid,'user':user}).insert(ignore_permissions=True); changed=True
        except Exception:
            if not frappe.db.exists('AOS Sound Favorite',{'sound':sid,'user':user}): raise
    elif not add and existing: changed=_delete_once('AOS Sound Favorite',{'sound':sid,'user':user})
    return ok('Sound favorite updated.',{'sound_id':sid,'favorited':add,'changed':changed})
def favorite_sound(**kw): return _sound_fav(kw.get('sound_id'),True)
def unfavorite_sound(**kw): return _sound_fav(kw.get('sound_id'),False)

def my_favorite_sounds(**kwargs):
    user=_user(); limit=_limit(kwargs.get('limit')); cur=decode_cursor(kwargs.get('cursor')); params={'user':user,'limit':limit+1}; clause=''
    if cur.get('created') and cur.get('id'): clause='AND (f.creation < %(created)s OR (f.creation=%(created)s AND f.name < %(id)s))'; params.update({'created':cur['created'],'id':cur['id']})
    rows=frappe.db.sql(f'''SELECT s.*,f.creation AS favorite_at,f.name AS favorite_row FROM `tabAOS Sound Favorite` f INNER JOIN `tabAOS Sound` s ON s.name=f.sound WHERE f.user=%(user)s AND {_sound_available_clause('s')} {clause} ORDER BY f.creation DESC,f.name DESC LIMIT %(limit)s''',params,as_dict=True); more=len(rows)>limit; page=rows[:limit]; nxt=encode_cursor({'created':str(page[-1].favorite_at),'id':page[-1].favorite_row}) if more and page else None
    return ok('Favorite sounds loaded.',{'items':_sound_payload(page,user),'next_cursor':nxt})

def sound_shorts(**kwargs): return _relation_feed('sound',kwargs.get('sound_id'),kwargs)
def hashtag_shorts(**kwargs):
    tag=str(kwargs.get('hashtag') or '').strip().lstrip('#').casefold();
    if not _HASHTAG_RE.fullmatch(tag): raise ShortsError('Invalid hashtag.')
    return _relation_feed('hashtag',tag,kwargs)

def _relation_feed(kind,value,kwargs):
    viewer=_user(required=False); user=None if viewer=='Guest' else viewer; limit=_limit(kwargs.get('limit')); cur=decode_cursor(kwargs.get('cursor')); params={'value':value,'limit':limit*4+1}; clause=''
    if cur.get('posted') and cur.get('id'): clause='AND (s.posted_on < %(posted)s OR (s.posted_on=%(posted)s AND s.name < %(id)s))'; params.update({'posted':cur['posted'],'id':cur['id']})
    join='INNER JOIN `tabAOS Short Sound` x ON x.short=s.name' if kind=='sound' else 'INNER JOIN `tabAOS Short Hashtag` x ON x.short=s.name'; field='x.sound' if kind=='sound' else 'x.hashtag'
    rows=frappe.db.sql(f'''SELECT s.* FROM `tabAOS Short` s {join} WHERE {field}=%(value)s AND s.lifecycle_status='Published' AND s.moderation_status='Approved' AND s.processing_status IN ('Ready','Not Required') {clause} ORDER BY s.posted_on DESC,s.name DESC LIMIT %(limit)s''',params,as_dict=True); visible=filter_viewable_rows(rows,viewer=user,limit=limit+1); more=len(visible)>limit; page=visible[:limit]; nxt=encode_cursor({'posted':str(page[-1].posted_on),'id':page[-1].name}) if more and page else None
    return ok('Shorts loaded.',{'items':serialize_short_rows(page,viewer=user),'next_cursor':nxt})


def download_short(**kwargs):
    user=_user(); _rate('download',user=user,limit=30); sid=kwargs.get('short_id'); event_id=str(kwargs.get('idempotency_key') or '').strip()[:128]
    if not event_id: raise ShortsError('idempotency_key is required.',code='SHORTS_INVALID_REQUEST')
    doc=frappe.get_doc('AOS Short',sid) if frappe.db.exists('AOS Short',sid) else None
    if not doc or not can_download(doc,viewer=user): raise ShortsPermissionError('Download is not allowed.')
    key=hashlib.sha256(f'download|{sid}|{user}|{event_id}'.encode()).hexdigest()
    created=_insert_short_event_once({'short':sid,'user':user,'session_id':'','event_type':'download','source':'download'},key)
    if created: frappe.db.sql('UPDATE `tabAOS Short` SET download_count=COALESCE(download_count,0)+1,last_engagement_at=NOW() WHERE name=%s',(sid,))
    if doc.content_type=='Photo':
        photos=frappe.get_all('AOS Short Photo',filters={'short':sid},fields=['media'],order_by='position asc'); service=MediaService(); urls=[service.get_url(media_id=r.media,user=user,expiry_minutes=10) for r in photos]; return ok('Photo download ready.',{'type':'photo','urls':urls})
    if doc.download_media:
        url=MediaService().get_url(media_id=doc.download_media,user=user,expiry_minutes=10); return ok('Download ready.',{'type':'video','status':'Ready','url':url})
    from .processing import enqueue_processing_job
    job=enqueue_processing_job(short=doc,operation='Download',idempotency_key=event_id)
    return ok('Download rendition queued.',{'type':'video','status':'Processing','processing_job_id':job.name})


def _reuse_draft(source_id,raw_media,caption,reuse_type,start=0,end=0):
    user=_user(); source=frappe.get_doc('AOS Short',source_id) if frappe.db.exists('AOS Short',source_id) else None
    if not source or not can_reuse(source,viewer=user,reuse_type=reuse_type): raise ShortsPermissionError('Source Short cannot be reused.')
    if reuse_type=='Segment':
        try: start=int(start or 0); end=int(end or 0)
        except Exception: raise ShortsError('Invalid source segment.')
        if start<0 or end<=start or end-start>60000 or (source.duration_seconds and end>int(float(source.duration_seconds)*1000)): raise ShortsError('Invalid source segment.')
    doc=frappe.get_doc({'doctype':'AOS Short','content_type':'Video','lifecycle_status':'Draft','processing_status':'Queued','moderation_status':'Draft','caption':_normalize_caption(caption),'audience':'everyone','allow_comments':1,'allow_downloads':0,'allow_reuse':1,'allow_side_by_side':1,'allow_segment_reuse':1,'source_short':source.name,'reuse_type':reuse_type,'source_start_ms':start,'source_end_ms':end}); doc.insert(ignore_permissions=True); _attach_video(doc,user,str(raw_media or '').strip()); doc.save(ignore_permissions=True)
    return ok('Reuse draft created.',serialize_owner_short(doc,viewer=user))
def create_side_by_side_draft(**kw): return _reuse_draft(kw.get('source_short_id'),kw.get('raw_video_media'),kw.get('caption'),'Side by Side')
def create_segment_reuse_draft(**kw): return _reuse_draft(kw.get('source_short_id'),kw.get('raw_video_media'),kw.get('caption'),'Segment',kw.get('source_start_ms'),kw.get('source_end_ms'))


def get_short_metrics(**kwargs):
    user=_user(); doc=_owned_short(kwargs.get('short_id'),user); return ok('Short metrics loaded.',{'short_id':doc.name,'impressions':int(doc.impression_count or 0),'views':int(doc.view_count or 0),'unique_viewers':int(doc.unique_viewer_count or 0),'watch_time_ms':int(doc.watch_time_ms or 0),'completion_count':int(doc.completion_count or 0),'rewatch_count':int(doc.rewatch_count or 0),'early_skip_count':int(doc.early_skip_count or 0),'likes':int(doc.like_count or 0),'comments':int(doc.comment_count or 0),'saves':int(doc.save_count or 0),'reposts':int(doc.repost_count or 0),'shares':int(doc.share_count or 0),'downloads':int(doc.download_count or 0)})

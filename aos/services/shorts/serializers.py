"""Bounded batch serializers for Shorts feeds and lists."""
from __future__ import annotations
from collections import defaultdict
from typing import Any
import frappe
from aos.services.accounts.serializers import serialize_internal_identity_map
from aos.services.social.repository import SocialRepository
from aos.services.social.serializers import relationship_map
from aos.services.media.media_service import MediaService

PUBLIC_FIELDS=("name","owner","content_type","lifecycle_status","processing_status","moderation_status","caption","audience","allow_comments","allow_downloads","allow_reuse","allow_side_by_side","allow_segment_reuse","source_short","reuse_type","source_start_ms","source_end_ms","posted_on","duration_seconds","playback_media","playback_manifest_media","poster_media","cover_media","storyboard_media","storyboard_manifest_media","view_count","like_count","comment_count","share_count","save_count","download_count","repost_count")

def _rowdict(row): return dict(row) if not isinstance(row,dict) else row

def serialize_short_rows(rows:list[dict[str,Any]],*,viewer:str|None)->list[dict[str,Any]]:
    if not rows: return []
    rows=[_rowdict(r) for r in rows]
    ids=[str(r['name']) for r in rows]
    owners=sorted({str(r.get('owner') or '') for r in rows if r.get('owner')})
    identities=serialize_internal_identity_map(owners)
    verified={str(r.user):bool(int(r.is_verified or 0)) for r in frappe.db.sql('SELECT user,is_verified FROM `tabAOS Profile` WHERE user IN %(users)s',{'users':tuple(owners)},as_dict=True)} if owners else {}
    relationships=relationship_map(repository=SocialRepository(),viewer=viewer,targets=owners,identities=identities) if viewer and viewer!='Guest' else {}

    photos=defaultdict(list)
    for r in frappe.db.sql('SELECT short,media,position FROM `tabAOS Short Photo` WHERE short IN %(ids)s ORDER BY short,position,name',{'ids':tuple(ids)},as_dict=True): photos[str(r.short)].append(r)
    modes=defaultdict(list)
    raw_modes=frappe.db.sql('SELECT short,mode FROM `tabAOS Short Mode` WHERE short IN %(ids)s ORDER BY short,mode',{'ids':tuple(ids)},as_dict=True)
    hashtags=defaultdict(list)
    for r in frappe.db.sql('SELECT short,hashtag FROM `tabAOS Short Hashtag` WHERE short IN %(ids)s ORDER BY short,hashtag',{'ids':tuple(ids)},as_dict=True): hashtags[str(r.short)].append(str(r.hashtag))
    ads=defaultdict(list)
    for r in frappe.db.sql('''SELECT sa.short,a.public_id,a.status FROM `tabAOS Short Ad` sa INNER JOIN `tabAOS Ad` a ON a.name=sa.ad WHERE sa.short IN %(ids)s ORDER BY sa.short,sa.position,sa.name''',{'ids':tuple(ids)},as_dict=True):
        if str(r.status)=='Active' and r.public_id: ads[str(r.short)].append(str(r.public_id))
    for r in raw_modes:
        sid=str(r.short); mode=str(r.mode)
        modes[sid].append(mode)
    sound_rows=frappe.db.sql('''SELECT ss.short,ss.sound,ss.start_ms,ss.duration_ms,ss.volume,ss.is_original_audio,s.title,s.artist,s.creator_account,s.source_type,s.status,s.reuse_allowed,s.sound_media FROM `tabAOS Short Sound` ss INNER JOIN `tabAOS Sound` s ON s.name=ss.sound WHERE ss.short IN %(ids)s''',{'ids':tuple(ids)},as_dict=True)
    sounds={str(r.short):r for r in sound_rows}

    media_ids=set()
    for r in rows:
        for k in ('playback_media','playback_manifest_media','poster_media','cover_media','storyboard_media','storyboard_manifest_media'):
            if r.get(k): media_ids.add(str(r[k]))
    for group in photos.values():
        for r in group: media_ids.add(str(r.media))
    for r in sound_rows:
        if r.sound_media: media_ids.add(str(r.sound_media))
    media_urls=MediaService().get_public_url_map(media_ids)

    liked=saved=reposted=set()
    if viewer and viewer!='Guest':
        def state(table): return {str(x.short) for x in frappe.db.sql(f'SELECT short FROM `tab{table}` WHERE user=%s AND short IN %(ids)s',{'ids':tuple(ids)},values=(viewer,),as_dict=True)}
        # Frappe's mixed named/positional params are not portable; use get_all for viewer state.
        liked={str(x.short) for x in frappe.get_all('AOS Short Like',filters={'user':viewer,'short':['in',ids]},fields=['short'],limit=max(1,len(ids)))}
        saved={str(x.short) for x in frappe.get_all('AOS Short Save',filters={'user':viewer,'short':['in',ids]},fields=['short'],limit=max(1,len(ids)))}
        reposted={str(x.short) for x in frappe.get_all('AOS Short Repost',filters={'user':viewer,'short':['in',ids]},fields=['short'],limit=max(1,len(ids)))}

    out=[]
    for r in rows:
        sid=str(r['name']); owner=str(r.get('owner') or ''); ident=identities.get(owner) or {}; rel=relationships.get(owner) or {}
        photo_payload=[{'media_id':str(p.media),'url':media_urls.get(str(p.media)),'position':int(p.position or 0)} for p in photos.get(sid,[]) if media_urls.get(str(p.media))]
        sr=sounds.get(sid); sound=None
        if sr:
            sound={'id':str(sr.sound),'title':str(sr.title or ''),'artist':str(sr.artist or ''),'source_type':str(sr.source_type or ''),'available':str(sr.status)=='active','reuse_allowed':bool(str(sr.status)=='active' and int(sr.reuse_allowed or 0)),'url':media_urls.get(str(sr.sound_media or '')),'start_ms':int(sr.start_ms or 0),'duration_ms':int(sr.duration_ms or 0),'volume':float(sr.volume or 1),'is_original':bool(int(sr.is_original_audio or 0))}
        payload={
            'id':sid,'content_type':r.get('content_type'),'caption':str(r.get('caption') or ''),'audience':r.get('audience'),
            'posted_on':r.get('posted_on'),'duration_seconds':float(r.get('duration_seconds') or 0),
            'creator':{'account_id':ident.get('account_id'),'display_name':ident.get('display_name') or 'AOS User','avatar':ident.get('avatar'),'is_verified':bool(verified.get(owner)),'is_following':bool(rel.get('is_following')),'is_friend':bool(rel.get('is_friend'))},
            'playback_url':media_urls.get(str(r.get('playback_media') or '')),'playback_manifest_url':media_urls.get(str(r.get('playback_manifest_media') or '')),'poster_url':media_urls.get(str(r.get('poster_media') or '')),'cover_url':media_urls.get(str(r.get('cover_media') or '')),'storyboard_url':media_urls.get(str(r.get('storyboard_media') or '')),'storyboard_manifest_url':media_urls.get(str(r.get('storyboard_manifest_media') or '')),
            'photos':photo_payload,'modes':modes.get(sid,[]),'hashtags':hashtags.get(sid,[]),'ad_ids':ads.get(sid,[]),'sound':sound,
            'permissions':{'comments_allowed':bool(int(r.get('allow_comments') or 0)),'downloads_allowed':bool(int(r.get('allow_downloads') or 0)),'reuse_allowed':bool(int(r.get('allow_reuse') or 0)),'side_by_side_allowed':bool(int(r.get('allow_side_by_side') or 0)),'segment_reuse_allowed':bool(int(r.get('allow_segment_reuse') or 0))},
            'counts':{k:int(r.get(f'{k}_count') or 0) for k in ('view','like','comment','share','save','download','repost')},
            'viewer':{'liked':sid in liked,'saved':sid in saved,'reposted':sid in reposted},
        }
        if r.get('source_short'): payload['reuse']={'source_short_id':r.get('source_short'),'type':r.get('reuse_type'),'source_start_ms':int(r.get('source_start_ms') or 0),'source_end_ms':int(r.get('source_end_ms') or 0)}
        out.append(payload)
    return out

def serialize_owner_short(doc,*,viewer:str)->dict[str,Any]:
    row={field:getattr(doc,field,None) for field in PUBLIC_FIELDS}
    public=serialize_short_rows([row],viewer=viewer)[0] if row.get('name') else {}
    public.update({'lifecycle_status':doc.lifecycle_status,'processing_status':doc.processing_status,'moderation_status':doc.moderation_status,'processing_error':doc.processing_error or None,'moderation_reason':doc.moderation_reason or None,'version':str(doc.modified or ''),'revision':int(doc.revision or 0)})
    return public

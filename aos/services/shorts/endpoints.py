"""Strict field allowlists for client-consumed Shorts v1 APIs."""
from .validation import EndpointSpec
from .identity import SHORT_ID_RE,SOUND_ID_RE,COMMENT_ID_RE

def S(fields,*ids): return EndpointSpec(frozenset(fields),tuple(ids))
ENDPOINT_SPECS={
"create_short":S({"content_type","raw_video_media","photo_media_ids","caption","hashtags","mention_account_ids","ad_ids","place_id","sound_id","audience","allow_comments","allow_downloads","allow_reuse","allow_side_by_side","allow_segment_reuse"}),
"update_short":S({"short_id","version","caption","hashtags","mention_account_ids","ad_ids","place_id","sound_id","audience","allow_comments","allow_downloads","allow_reuse","allow_side_by_side","allow_segment_reuse","photo_media_ids","cover_media_id"},("short_id",SHORT_ID_RE)),
"submit_short":S({"short_id","version","idempotency_key"},("short_id",SHORT_ID_RE)),
"get_short":S({"short_id"},("short_id",SHORT_ID_RE)),
"my_shorts":S({"status","limit","cursor"}),
"delete_short":S({"short_id","version"},("short_id",SHORT_ID_RE)),
"retry_processing":S({"short_id","idempotency_key"},("short_id",SHORT_ID_RE)),
"feed_for_you":S({"limit","cursor","mode","session_id"}),
"feed_following":S({"limit","cursor","mode"}),
"like_short":S({"short_id"},("short_id",SHORT_ID_RE)),"unlike_short":S({"short_id"},("short_id",SHORT_ID_RE)),
"save_short":S({"short_id"},("short_id",SHORT_ID_RE)),"unsave_short":S({"short_id"},("short_id",SHORT_ID_RE)),"saved_shorts":S({"limit","cursor"}),
"repost_short":S({"short_id","note"},("short_id",SHORT_ID_RE)),"undo_repost_short":S({"short_id"},("short_id",SHORT_ID_RE)),
"not_interested":S({"short_id"},("short_id",SHORT_ID_RE)),
"create_comment":S({"short_id","comment","parent_comment_id"},("short_id",SHORT_ID_RE),("parent_comment_id",COMMENT_ID_RE)),"delete_comment":S({"comment_id"},("comment_id",COMMENT_ID_RE)),"list_comments":S({"short_id","limit","cursor"},("short_id",SHORT_ID_RE)),"list_comment_replies":S({"comment_id","limit","cursor"},("comment_id",COMMENT_ID_RE)),"like_comment":S({"comment_id"},("comment_id",COMMENT_ID_RE)),"unlike_comment":S({"comment_id"},("comment_id",COMMENT_ID_RE)),
"record_events":S({"events","session_id"}),"record_share":S({"short_id","event_id","channel"},("short_id",SHORT_ID_RE)),
"download_short":S({"short_id","idempotency_key"},("short_id",SHORT_ID_RE)),
"list_sounds":S({"limit","cursor"}),"search_sounds":S({"q","limit","cursor"}),"get_sound":S({"sound_id"},("sound_id",SOUND_ID_RE)),"sound_shorts":S({"sound_id","limit","cursor"},("sound_id",SOUND_ID_RE)),
"favorite_sound":S({"sound_id"},("sound_id",SOUND_ID_RE)),"unfavorite_sound":S({"sound_id"},("sound_id",SOUND_ID_RE)),"my_favorite_sounds":S({"limit","cursor"}),
"hashtag_shorts":S({"hashtag","limit","cursor"}),
"create_side_by_side_draft":S({"source_short_id","raw_video_media","caption"},("source_short_id",SHORT_ID_RE)),
"create_segment_reuse_draft":S({"source_short_id","raw_video_media","source_start_ms","source_end_ms","caption"},("source_short_id",SHORT_ID_RE)),
"get_short_metrics":S({"short_id"},("short_id",SHORT_ID_RE)),
}
MUTATING_ENDPOINTS={k for k in ENDPOINT_SPECS if k not in {"get_short","my_shorts","feed_for_you","feed_following","saved_shorts","list_comments","list_comment_replies","list_sounds","search_sounds","get_sound","sound_shorts","my_favorite_sounds","hashtag_shorts","get_short_metrics"}}

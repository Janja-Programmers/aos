# Public API

Base method prefix: `aos.api.v1.shorts.`. Responses use `{ok, message, data}` or `{ok:false, message, error, data}`.

## Upload and management

- `create_short` POST: `raw_video_media|media_id|media`, optional audience/comment/download settings.
- `update_short_metadata` POST: `short_id`, caption, hashtags, audience, settings, optional `ad_id`, and sound metadata. Legacy `content_mode` is accepted but ignored; the response returns the automatically assigned mode and classification summary.
- `get_short`: `short_id`.
- `my_shorts`: bounded `limit`, signed `cursor`.
- `user_shorts`: `user|target_user`, optional mode, bounded `limit`, signed `cursor`.
- `delete_short` POST: `short_id`; idempotent soft delete.
- `retry_processing` POST: `short_id`; failed Shorts only, active generation reused.

## Feeds

- `feed_for_you`, `feed_following`, `feed_by_ad`.
- `saved_shorts`, `liked_shorts`, `reposted_shorts`, `sound_shorts`.

All cursor endpoints use an HMAC-signed cursor with a 24-hour maximum age.

## Interactions

- `toggle_like`, `toggle_save_short`, `toggle_repost`.
- `add_comment`, `reply_comment`, `list_comments`, `list_replies`, `delete_comment`, `toggle_comment_like`.
- `track_impression`, `track_view`, `track_share` accept optional `event_id` for replay deduplication.
- `download_short` accepts optional `event_id` and never returns an object key.
- `create_short_share_link` returns canonical link, playable URL and preview metadata.
- `share_short_to_chat` validates both conversation membership and recipient visibility, then delegates native Short message persistence/realtime/notification to the canonical Chat service.

## Sounds and analytics

- `create_sound`, `list_sounds`, `search_sounds`, `get_sound`, `favorite_sound`, `my_favorite_sounds`, `change_short_sound`, `remove_short_sound`.
- `get_short_analytics`, `my_shorts_analytics`, `user_short_analytics`, `general_short_analytics`.

## Stable Shorts errors

`SHORTS_UNKNOWN_FIELD`, `SHORTS_ALIAS_CONFLICT`, `SHORTS_INVALID_IDENTIFIER`, `SHORTS_INVALID_CURSOR`, `SHORTS_INVALID_REQUEST`, `SHORTS_INVALID_MEDIA_KEY`, `SHORTS_NOT_FOUND`, `SHORTS_ACCESS_DENIED`, `SHORTS_INVALID_STATE`, `SHORTS_CONFLICT`, `SHORTS_PROCESSING_FAILED`, `SHORTS_MEDIA_CONFIGURATION_ERROR`, `SHORTS_INTERNAL_ERROR`.

Known shared dependency errors such as `AUTH_REQUIRED`, `RATE_LIMITED`, `MEDIA_NOT_FOUND` and `STORAGE_UNAVAILABLE` remain unchanged.

## Classification response

Short payloads include `content_mode` and `classification: {status, source, confidence, model_version}`. Raw visual/text scores are never public. `shop` requires a validated owned active ad. `all` is accepted only as a feed filter and is not a Short mode.

### Audio remix fields

Publishing or changing a selected sound returns the durable remix state:

```json
{
  "audio_mix_status": "pending",
  "audio_mix_job_id": "VIDEO-JOB-2026-00001",
  "audio_mix_job_status": "Queued"
}
```

Clients should poll the canonical Short read endpoint while the mix status is
`pending` or `processing`. Publication is complete with sound only when the
status becomes `ready`. A `failed` state includes a bounded public-safe error and
leaves the previous playable rendition available.

# Native Chat sharing

Shared objects are stored by canonical object ID, not by a web URL. This is required for Flutter and other native clients to route internally.

## Live -> Chat

Preferred endpoint:

`POST /api/method/aos.api.v1.live.share_live_to_chat`

```json
{
  "live_id":"LIVE-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "conversation_id":"CONV-2026-00001",
  "message":"Come watch this",
  "idempotency_key":"device-operation-id"
}
```

`message` and `content` are compatible aliases and may not conflict. The Live must be active and the sender plus recipient must be authorized to view it. Live delegates persistence to the canonical Chat service.

Success contains `live_id`, `conversation_id` and the native serialized Chat message. The message contains `live`, `live_preview` and `live_unavailable`.

Clients navigate using `live`/`live_id`:

- Web: `/live/<LIVE-ID>`
- Flutter: native Live route with the `LIVE-*` identifier
- No client should parse a text URL to recover the Live ID.

An ended Live already present in history remains representable when privacy permits. Removed/inaccessible/blocked Lives keep the historical reference but serialize with `live_preview: null` and `live_unavailable: true` so private state is not leaked.

A **new** share attempt for an ended, missing, removed, or otherwise inaccessible Live returns the same non-enumerating `LIVE_CHAT_TARGET_NOT_FOUND` response. Clients must not infer Live lifecycle or privacy state from this error.

Live share-specific errors are `LIVE_CHAT_TARGET_NOT_FOUND`, `LIVE_CHAT_SHARE_FORBIDDEN`, `LIVE_CHAT_SHARE_INVALID` and `LIVE_CHAT_SHARE_FAILED`.

## Shorts -> Chat

`aos.api.v1.shorts.share_short_to_chat` remains the feature-owned Short share endpoint. It now delegates message creation to the same Chat service. Chat revalidates Short visibility for both participants and serializes `short_preview` / `short_unavailable`.

## Ads

Native Ad references remain supported by generic Chat send and serializer behavior. Ad business rules remain owned by Ads.

## External sharing

Chat native object references are unrelated to OS/web external-share URLs. Feature frontends may also produce canonical public URLs for external apps, but those URLs are not the Chat storage contract.

## Ad -> Chat

The established native `ad` message reference remains supported. Chat now revalidates the public marketplace policy both when an Ad is sent/forwarded and whenever history is serialized. An inactive, expired, unavailable-account or block-inaccessible Ad cannot be newly shared and does not leak a historical preview.

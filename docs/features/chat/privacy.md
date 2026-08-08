# Chat privacy and authorization

## Conversation access

Only the two participants may read or mutate a conversation. `open_conversation` resolves the target account, requires a usable account state, rejects self-chat and checks canonical bidirectional Social blocking.

Historical conversations may remain visible after a block so the user does not lose their own history. New sends/opens/forwards are denied. Presence, typing, Live-state indicators and new message reactions are suppressed/denied across the block. Private history actions such as delete-for-me/star remain available.

## Account state

New messaging rejects disabled, suspended, deleted, deactivated/restricted recipients according to canonical account policy. Account deletion deactivates the deleted participant's conversation side and removes that user's private stars, reactions and translation-cache ownership without deleting the other participant's legitimate message history.

## Shared objects

- Short: canonical Shorts visibility/audience/block policy is applied in batch for history serialization and directly for send/forward authorization.
- Live: canonical Live policy is applied. New shares require active Live; history can show ended Lives when still accessible.
- Inaccessible shared objects return an unavailable preview rather than private metadata.

## Public payloads

Public account identity should be `ACC-*` plus display-safe name/avatar. Chat notification payloads use canonical public sender account IDs. Native Live previews omit room names, tokens, private sessions and moderation internals. Short previews no longer expose raw Short owner User values.

### Ads

Native Ad references are evaluated with the public marketplace eligibility rules when sent, forwarded and serialized: the Ad must be Active and unexpired, the Seller must be Active, the seller account must be enabled/active/not deleted, and neither side may block the other. Historical messages retain the canonical `AD-*` reference but return `ad_preview: null` and `ad_unavailable: true` when the listing later expires, is suspended/deleted, the seller becomes unavailable, or the viewer is blocked. This prevents Chat history from bypassing Ads visibility policy.

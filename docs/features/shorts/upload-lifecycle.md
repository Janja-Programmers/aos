# Upload lifecycle

1. Media initialization creates an owned private `AOS Media Object` for `short_video_raw` and returns a bounded-expiry signed PUT URL and required headers.
2. The client uploads directly to MinIO. Frappe never proxies the large body.
3. Confirmation validates owner, purpose, bucket/key, expected size, content metadata and object existence through the Media service.
4. Short creation attaches that exact media record. Client-supplied owner, state, counters, bucket or arbitrary URL are never accepted.
5. One active `AOS Video Processing Job` is allowed per Short. Repeated confirmation/retry reuses it.
6. The outbox dispatches only after the surrounding transaction commits.
7. The authenticated companion downloads, probes, transcodes, uploads versioned outputs and sends a signed callback.
8. Frappe validates the callback generation and output prefixes before making the Short ready.

Initialized/uploaded records older than `AOS_SHORTS_ABANDONED_UPLOAD_HOURS` are marked failed by daily maintenance and their attached media enters the standard orphan cleanup lifecycle.

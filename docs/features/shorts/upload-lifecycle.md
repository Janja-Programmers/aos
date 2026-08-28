# Upload lifecycle

1. The client probes the selected video locally and calls Media `init_upload` with `purpose=short_video_raw`, exact `size_bytes`, numeric `duration_seconds`, `upload_mode=auto`, and an idempotency key. The backend enforces the **300 MiB / 600-second** Short policy before any byte-upload capability is issued.
2. Small Shorts can retain the existing direct PUT contract. Large Shorts (currently at/above 16 MiB) receive a **24-hour resumable multipart session** using 8 MiB parts. The threshold/part size are centrally owned by the Media purpose policy.
3. For multipart, the client uses `media_id` as its durable session id, calls `multipart_status` to discover authoritative uploaded/missing/invalid parts (`retry_parts` is the exact union to re-send), obtains bounded `multipart_part_urls` batches, and PUTs exact part byte ranges directly to MinIO. Frappe never proxies the video body and never trusts client-provided part ETags.
4. Heavy private Short multipart sessions assemble directly at the canonical private raw-video key. This avoids a second 300 MiB-scale object-store copy during confirmation. `complete_multipart_upload` row-locks the Media object, lists parts from storage, verifies contiguous part numbers and exact part sizes, assembles the object, validates final size/magic bytes, closes the multipart identity, and persists the normal Media confirmation state. A retry can recover if storage completion succeeded but the Frappe request died before commit.
5. The client can abort an unfinished multipart session explicitly. Active multipart sessions are per-user quota-bounded, have an expiry, and are also closed by AOS cleanup. MinIO is additionally configured with stale-multipart reclamation so abandoned part data is reclaimed independently if an app cleanup run is interrupted.
6. Short creation attaches that exact confirmed Media record. Client-supplied owner, state, counters, bucket, object-store upload id, ETags, or arbitrary URL are never accepted.
7. One active `AOS Video Processing Job` is allowed per Short. Repeated confirmation/retry reuses it.
8. The outbox dispatches only after the surrounding transaction commits.
9. The authenticated companion downloads, probes, transcodes and uploads versioned outputs. Heavy work is horizontally scalable across the video queue; terminal callback delivery runs on a separate callback queue.
10. Frappe validates the callback generation and output prefixes before making the Short ready.

Initialized/uploaded records older than their retention/expiry policy are cleaned by Media maintenance; attached abandoned Short uploads enter the normal Shorts/media orphan lifecycle.

A stale video-worker heartbeat is replayed only when RQ no longer reports active work. Replay is bounded; exhaustion produces a terminal failure callback so the Short cannot remain in `processing` forever.

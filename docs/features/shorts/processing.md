# Processing state machine

Short states: `initialized`, `uploaded`, `processing`, `ready`, `failed`, `deleted`.

Job states: `Queued`, `Dispatching`, `Processing`, `Ready`, `Failed`, `Cancelled`.

Only one active job key exists for a Short. Every job has a monotonically increasing generation. A callback must match the stored generation; an older generation cannot overwrite a newer one. Callbacks for deleted Shorts are cancelled without changing the Short.

The companion requires HMAC-authenticated requests. Callbacks use timestamped HMAC signatures and the transactional-outbox correlation token/generation checks. Durable Redis lifecycle records make repeated dispatch and callback delivery idempotent.

Limits are configured with `VIDEO_MAX_INPUT_BYTES`, `VIDEO_MAX_DURATION_SECONDS`, `VIDEO_MAX_WIDTH`, `VIDEO_MAX_HEIGHT`, `VIDEO_MAX_PIXELS`, `VIDEO_ALLOWED_CODECS`, FFprobe/FFmpeg timeouts and thread limits. Commands use argv lists and never a shell.

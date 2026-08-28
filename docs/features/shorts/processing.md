# Processing state machine

Short states: `initialized`, `uploaded`, `processing`, `ready`, `failed`, `deleted`.

Job states: `Queued`, `Dispatching`, `Processing`, `Ready`, `Failed`, `Cancelled`.

Only one active job key exists for a Short. Every job has a monotonically increasing generation. A callback must match the stored generation; an older generation cannot overwrite a newer one. Callbacks for deleted Shorts are cancelled without changing the Short.

The companion requires HMAC-authenticated requests. Callbacks use timestamped HMAC signatures and the transactional-outbox correlation token/generation checks. Durable Redis lifecycle records make repeated dispatch and callback delivery idempotent.

Limits are configured with `VIDEO_MAX_INPUT_BYTES`, `VIDEO_MAX_DURATION_SECONDS`, `VIDEO_MAX_WIDTH`, `VIDEO_MAX_HEIGHT`, `VIDEO_MAX_PIXELS`, `VIDEO_ALLOWED_CODECS`, FFprobe/FFmpeg timeouts and thread limits. Commands use argv lists and never a shell.
## Upload admission and heavy-video guarantees

The canonical raw-Short admission limit is **300 MiB and 600 seconds (10 minutes)**. `aos.api.v1.media.init_upload` requires `duration_seconds` for `purpose=short_video_raw` and rejects an over-limit duration or size before it issues a presigned PUT URL. The selected duration is persisted on `AOS Media Object` and `create_short` validates it again before creating processing work. The video worker still probes the actual file and applies the same duration ceiling, so a falsified client hint cannot make an over-duration video become ready.

Short upload URLs are valid for 60 minutes. Confirmation uses object-store metadata plus bounded header validation and does not synchronously stream a large processing-oriented video back through Frappe solely to calculate SHA-256 unless the client explicitly supplied a checksum contract. This keeps the upload data plane direct-to-object-storage.

The current raw upload transport is one presigned S3-compatible PUT. It supports the admitted 300 MiB size, but it is not resumable. For unreliable mobile networks and very large production traffic, add multipart/resumable upload as a separate client/storage contract so failed parts can be retried without restarting the entire object.

## Processing concurrency and recovery

Video work and callback delivery use separate RQ queues. `video-worker` consumes only the heavy FFmpeg queue and can be horizontally scaled with `VIDEO_WORKER_REPLICAS`; `video-callback-worker` consumes `VIDEO_CALLBACK_QUEUE_NAME` and is independently scalable with `VIDEO_CALLBACK_WORKER_REPLICAS`, so a ready/failed callback is not forced to wait behind another long transcode or another slow callback.

The whole-job timeout is 7200 seconds and the per-FFmpeg timeout is 3600 seconds. The companion emits a durable heartbeat while work is active. If RQ no longer has an active job and the heartbeat is stale, dispatch reconciliation can automatically replay the work up to `VIDEO_MAX_STALE_WORK_REPLAYS`; after that it persists a terminal `WORKER_LOST_AFTER_RETRIES` failure and schedules the callback. A Short must therefore converge to ready or failed instead of remaining permanently in `processing` after a killed worker.


## Classification stage

After metadata validation and thumbnail generation, the worker samples representative frames and sends them over the signed internal `/internal/shorts/classify-frames` boundary. Classification is non-fatal: processing still reaches `ready` with a fallback marker if OpenCLIP is unavailable. The Frappe callback validates and stores only bounded four-mode scores; final fusion occurs when caption/hashtags and optional ad context are submitted.
## Durable callback payload integrity

The video companion stores a bounded terminal result in Redis before callback delivery. The stored JSON must remain complete; it must never be cut at a generic diagnostic-field limit because truncated JSON cannot be replayed safely. HLS segment/object details stay internal to the worker. The callback contains only the canonical job and generation identifiers, final MP4/HLS keys, thumbnail metadata, duration, sound/classification results, and a bounded output-object count.

A legacy or corrupt durable result is dead-lettered as `CALLBACK_RESULT_INVALID` without sending a malformed callback. Operators must retry processing for the affected Short; the service never invents missing duration or storage metadata.

## Selected-sound remix lifecycle

When a ready Short receives or changes a selected sound, the sound link and a
new `AOS Video Processing Job` with `reason=audio_reprocess` are created in the
same transaction. The public lifecycle is:

```text
pending -> processing -> ready
                      -> failed
```

The existing playable rendition remains available while remixing. A publish or
sound-change request is rejected and rolled back if durable remix work cannot be
created; the backend must never acknowledge a selected sound while leaving only
a `pending` flag with no job/outbox record.

The worker mixes the selected sound with the video's original audio when an
original audio track exists. Silent videos use the selected sound as their only
audio track. Callback success is accepted only when `sound_applied=true` for a
Short that still has a non-original selected sound.

`aos.tasks.shorts.recover_pending_audio_mixes` runs every five minutes and
reconciles bounded selected-sound rows in both `ready` and `processing` states.
It cancels stale active generations, creates a fresh job/outbox callback token,
and chooses an audio-only reprocess only when canonical base output exists. Rows
without selected sound are normalized to `audio_mix_status=none`. Operators may
invoke it with `stale_minutes=0` for immediate recovery.


Audio-mix recovery distinguishes fresh active work from stale work. It can cancel stale active remix generations and atomically create a new generation; an explicit `stale_minutes=0` run forces immediate operator recovery.

### Audio-only reprocessing

An `audio_reprocess` generation replaces only the processed video/HLS outputs and the audio-mix state. It preserves the Short's existing thumbnail and automatic content classification. This prevents duplicate `short_thumbnail` attachments and avoids unnecessary visual-classification calls when only sound settings changed.

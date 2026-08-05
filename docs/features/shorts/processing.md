# Processing state machine

Short states: `initialized`, `uploaded`, `processing`, `ready`, `failed`, `deleted`.

Job states: `Queued`, `Dispatching`, `Processing`, `Ready`, `Failed`, `Cancelled`.

Only one active job key exists for a Short. Every job has a monotonically increasing generation. A callback must match the stored generation; an older generation cannot overwrite a newer one. Callbacks for deleted Shorts are cancelled without changing the Short.

The companion requires HMAC-authenticated requests. Callbacks use timestamped HMAC signatures and the transactional-outbox correlation token/generation checks. Durable Redis lifecycle records make repeated dispatch and callback delivery idempotent.

Limits are configured with `VIDEO_MAX_INPUT_BYTES`, `VIDEO_MAX_DURATION_SECONDS`, `VIDEO_MAX_WIDTH`, `VIDEO_MAX_HEIGHT`, `VIDEO_MAX_PIXELS`, `VIDEO_ALLOWED_CODECS`, FFprobe/FFmpeg timeouts and thread limits. Commands use argv lists and never a shell.

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
requeues bounded stale `pending`/`processing` rows that have no active processing
job. Operators may invoke it manually with `stale_minutes=0` for immediate
recovery.

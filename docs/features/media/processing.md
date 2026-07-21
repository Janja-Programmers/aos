# Processing integrations

## Canonical input

Every processing job starts from a Media ID. Job payloads may include trusted bucket/object identity for a private companion service, but public APIs and clients receive only Media IDs and authorized URLs.

## Video processing

Raw Shorts media uses `short_video_raw`, private visibility, and an `AOS Short` attachment. Job creation validates purpose, visibility, attachment, and Short state before recording Media processing start. The existing transactional outbox dispatches the signed request.

Callbacks are authenticated by the existing service-secret/HMAC contract and checked by persistent job idempotency. A ready callback validates duration against the configured Shorts limit before updating the Short. Thumbnail output must be in the configured public bucket and `shorts/thumbnails` prefix; AOS stats and validates the real object before creating a `short_thumbnail` Media record linked through `derived_from_media`.

Processing success records completion; processing failure records a bounded safe reason. A duplicate terminal callback is accepted only when it agrees with the existing terminal state.

## Background removal

The API reads an owned completed image through the adapter with a strict byte limit, validates dimensions, calls the private service, validates the returned PNG, and creates a new Media object. Output purpose is allowlisted and must permit PNG. The source is never overwritten.

## Moderation and image search

Moderation payloads use canonical Media identity and verified metadata; stale public URL caches are not included. Existing callback authentication, resource matching, idempotency, and outbox semantics remain enabled. Image indexing consumes ad Media IDs/keys from trusted internal jobs and must not accept arbitrary client URLs.

## Derived media rules

- Derived output receives a separate server-generated storage identity and Media record.
- `derived_from_media` records lineage.
- Output purpose, bucket, prefix, type, size, checksum, and dimensions are verified.
- Derived creation is an internal service path; client-disabled purposes cannot be selected through `init_upload`.
- Source deletion/replacement does not silently overwrite or mutate the derived object.

## Failure and retry

Processing jobs own their own bounded attempt/outbox lifecycle. Media lifecycle records processing start/completion/failure for operational correlation. Retrying a processing request must reuse persistent job identity and callback idempotency controls rather than creating untracked outputs.

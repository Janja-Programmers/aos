# Media subsystem

The AOS Media subsystem is the canonical boundary for every object owned by AOS: profile images, seller banners, ad media, review images, verification documents, chat attachments, Shorts source videos and thumbnails, live covers, category icons, sounds, and background-removal inputs/outputs.

The subsystem deliberately separates four concerns:

1. **Versioned API wrappers** authenticate, rate-limit, parse, and return the standard AOS response envelope.
2. **`MediaService`** owns authorization, purpose policy, storage identity, lifecycle transitions, attachment, replacement, deletion, URL serialization, and cleanup.
3. **`StorageAdapter`** isolates MinIO operations, timeouts, bounded retries, object validation, and public/signed URL creation.
4. **Feature consumers** store a Media ID as the source of truth and ask the central service to validate, attach, release, or serialize it.

## Non-negotiable invariants

- Clients never select a bucket or object key.
- Every upload has a registered purpose, owner, visibility, expected size, server-generated final key, and server-generated private staging key.
- A presigned upload is not complete until `confirm_upload` verifies the object and promotes it to its final identity.
- Public media uses the configured canonical public origin. Private media never exposes a permanent URL.
- Feature tables store Media IDs. Legacy public URL fields remain compatibility caches only and must not be treated as authority.
- Attached media cannot be deleted through the user endpoint.
- Cleanup checks both Media metadata and feature-table references before deleting bytes.
- Storage failures are retriable and never silently convert a failed delete into `Deleted`.
- Processing services refer to canonical Media IDs; derived media records preserve source relationships.

## Documentation map

- [Architecture](architecture.md)
- [API](api.md)
- [Security](security.md)
- [Lifecycle](media-lifecycle.md)
- [Storage](storage.md)
- [Processing](processing.md)
- [Operations](operations.md)
- [Testing](testing.md)
- [Migration](migration.md)

Existing production material in `docs/production/` remains valid for service-specific deployment. This feature folder is the authoritative design and operating contract for Media itself.

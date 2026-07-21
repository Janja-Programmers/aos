# Architecture

## Boundary ownership

### API layer

`aos/api/v1/media/__init__.py` contains stable whitelisted endpoints. Implementations under `aos/api/media/` perform only authentication, operation-specific rate limiting, request parsing, stable error mapping, and response serialization. They must not call the MinIO SDK or construct storage paths.

### Application/domain layer

`aos/services/media/media_service.py` is the transaction and lifecycle coordinator. It owns:

- purpose lookup and upload eligibility;
- generated staging/final storage identities;
- initiated record persistence before a presigned URL is issued;
- finalization, content verification, and compensation;
- canonical public or private URL access;
- owner and resource authorization;
- attachment-count limits and incompatible multi-attachment prevention;
- replacement and orphan transitions;
- user deletion versus privileged cleanup;
- processing state and source/derived relationships;
- cleanup and cross-feature reference defense;
- structured, bounded observability.

External object-storage operations cannot participate in the database transaction. The service therefore uses explicit compensation: a failed metadata insert deletes newly written bytes; a failed metadata save after promotion deletes the promoted object; a failed delete remains `Delete Pending`; and staging identities remain recorded until the presigned URL expires so late PUTs can be reconciled.

### Purpose policy

`aos/services/media/media_purposes.py` is an immutable registry. A policy defines MIME/extension allowlists, size and count bounds, visibility, bucket class, path prefix, allowed resource DocTypes, upload roles, dimension/duration constraints, processing requirements, replacement/deletion rules, and orphan retention.

Do not add purpose-specific validation to feature APIs. Add or change the policy and test it centrally.

### Storage boundary

`aos/services/storage/base.py` defines the practical adapter contract. `MinioStorage` is the only general MinIO SDK implementation. It owns stat/read/write/copy/delete, signed URLs, canonical public URLs, safe identity validation, connection/read timeouts, bounded retries, and health reporting.

The legacy Shorts facade in `aos/services/minio_service.py` delegates to this adapter; it does not create a second client policy.

### Feature consumers

Accounts, sellers, ads, reviews, live, category icons, chat, verification, Shorts, sounds, moderation, image search, video processing, and background removal use Media IDs. Consumer helpers may maintain legacy URL fields for old clients, but URL reads are regenerated through `MediaService`.

## Data model

`AOS Media Object` stores stable identity and lifecycle metadata. Important groups are:

- identity: `name`, `owner_user`, `purpose`, `visibility`;
- final storage: `bucket`, `object_key`;
- staged upload: `upload_bucket`, `upload_object_key`, `upload_expires_at`;
- verified file metadata: MIME, expected/actual size, expected/actual checksum, dimensions, duration, ETag;
- attachment: DocType, name, field, timestamp;
- lifecycle/failure/retry timestamps and categories;
- relationships: `derived_from_media`, `replaced_by_media`.

Indexes support owner checks, attachment lookup, cleanup, expiry, delete retry, idempotency lookup, and derived-media lookup.

## Dependency direction

Feature modules depend on the Media service; Media depends on purpose/resource authorization and the storage protocol; the storage adapter depends on MinIO/configuration. Media must not import feature-private implementation modules. Resource checks query documented DocType ownership fields at the boundary.

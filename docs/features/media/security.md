# Security model

## Trust boundaries

Every client value is untrusted: filename, MIME, size, purpose, Media ID, owner/resource identity, URL, bucket, and object key. Public endpoints accept only the minimum client-controlled fields. Storage identity is generated on the server.

## Upload validation

- Filenames are normalized with Unicode NFKC, length-bounded, retained only as display metadata, and rejected for paths, null/control characters, hidden names, unsafe extensions, or dangerous double extensions.
- MIME and extension are independent allowlists defined by purpose.
- Completion checks real signatures for supported images, PDFs, video containers, and audio formats.
- Executable signatures and unknown content are rejected.
- Images are decoded and verified with bounded pixel limits, format matching, and purpose dimensions.
- Non-image content is streamed for SHA-256; PDFs containing unsupported active constructs are rejected.
- Expected size is checked both before and after hashing to detect incomplete or changing objects.
- Optional client SHA-256 is normalized and compared in constant semantics.

A declared content type or successful presigned PUT is never proof of valid content.

## Ownership and attachment

`owner_user` identifies the uploader. `purpose` defines which resource DocTypes are compatible. Attachment verifies both Media ownership and resource ownership—for example seller ownership of an ad, reviewer ownership of a review, host ownership of a live stream, or sender ownership of a message.

A Media object cannot be silently attached to a second incompatible resource. Purpose-specific maximum counts are enforced centrally. Replacement is denied for immutable purposes such as chat attachments, raw Shorts videos, and sounds.

## Private media

Verification documents, chat attachments, background-removal sources, and raw Shorts videos use private storage. Their records may not persist a public URL. Access requires authentication and one of:

- uploader ownership;
- effective Read permission on the attached resource (or on `AOS Media Object` for generic private media);
- conversation participation for chat attachments;
- ownership of the attached verification request.

Private responses never expose MinIO internal hosts, raw object keys, or permanent links. Signed URLs are short-lived and are not stored in DocTypes.

## URL and SSRF controls

Feature APIs accept Media IDs, not arbitrary media URLs. Public URL serialization derives from the configured bare `MINIO_PUBLIC_BASE_URL`. Production rejects localhost, loopback, and the internal `minio` hostname. Processing callbacks may identify only output objects in the expected configured bucket and purpose prefix; AOS reads them through the storage adapter rather than fetching an arbitrary callback URL.

## Privileged operations

The public delete API cannot force deletion. Cleanup invokes `delete_media_as_system` explicitly and still performs reference checks. System-only purposes and internal derived-media creation use explicit service paths; they do not weaken user checks globally.

## Logging safety

Media logs contain stable event/operation/outcome categories, Media ID, purpose, bounded retry count, duration, and byte counters. They exclude file bytes, credentials, session IDs, signed URLs, user IDs as metric labels, arbitrary filenames, and private object paths.

## Callback security

Video, moderation, background-removal, image-search, and related companion services retain their existing HMAC/secret and transactional-outbox controls. Callbacks are matched to persistent job/media/resource identity, validated for state, and treated idempotently. Derived files receive new Media records and cannot overwrite their sources.

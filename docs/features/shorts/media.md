# Media

Raw Shorts media is private and owned through `AOS Media Object`. Processed outputs are versioned below the configured `shorts/processed/<SHORT-ID>/` prefix; thumbnails are below `shorts/thumbnails/<SHORT-ID>/` in the configured public bucket.

The processing callback supplies object evidence, not authoritative URLs. Frappe validates bucket and normalized object key, rejects traversal or foreign prefixes, and reconstructs playable/download metadata from deployment configuration.

`download_short` returns a short-lived signed download URL only after visibility and `allow_downloads` checks. It does not expose the internal object key.

The current product contract returns the processed MP4. A separate watermarked rendition and raw-retention deletion policy were not silently invented. Operators should apply bucket lifecycle rules to versioned processed directories and Media orphan retention as described in operations.

## Desk sound uploader

System Managers can upload reusable audio from the `AOS Sound` Desk form. The form uses the same hardened Media lifecycle as category icons:

```text
media.init_upload
→ direct object-storage PUT
→ media.confirm_upload
→ save AOS Sound
→ attach MEDIA-* to sound_media
```

Accepted formats are MP3, M4A, AAC, WAV and OGG, up to 50 MB and 10 minutes. The Desk form reads duration for immediate feedback; Media confirmation remains authoritative for file type, object ownership, size, signature and storage identity.

A sound's audio asset is immutable after creation. Metadata can be corrected, but changing the underlying file requires a new `AOS Sound`, preserving every existing `AOS Short Sound` relationship. Deleting an unused Sound releases its attached Media through the normal orphan lifecycle.

# Media

Raw Shorts media is private and owned through `AOS Media Object`. Processed outputs are versioned below the configured `shorts/processed/<SHORT-ID>/` prefix; thumbnails are below `shorts/thumbnails/<SHORT-ID>/` in the configured public bucket.

The processing callback supplies object evidence, not authoritative URLs. Frappe validates bucket and normalized object key, rejects traversal or foreign prefixes, and reconstructs playable/download metadata from deployment configuration.

`download_short` returns a short-lived signed download URL only after visibility and `allow_downloads` checks. It does not expose the internal object key.

The current product contract returns the processed MP4. A separate watermarked rendition and raw-retention deletion policy were not silently invented. Operators should apply bucket lifecycle rules to versioned processed directories and Media orphan retention as described in operations.

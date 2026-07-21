# Testing

## Test layers

### Pure validation/policy tests

`aos/tests/test_media_content_validation.py` and `test_media_purpose_policies.py` run without Frappe or external services. They cover Unicode/path/null/double-extension handling, signatures, spoofing, malformed images, PDF active content, checksums, purpose inventory, private/public policy, internal-only purposes, roles, and bounded limits.

```bash
PYTHONPATH=. pytest -q \
  aos/tests/test_media_content_validation.py \
  aos/tests/test_media_purpose_policies.py
```

### Frappe DocType tests

`aos/aos/doctype/aos_media_object/test_aos_media_object.py` validates schema invariants: ownership, visibility, storage identity, purpose, checksum, and attachment state.

### Frappe service tests

`aos/tests/test_media_service.py` injects an in-memory `StorageAdapter`. It covers initiation, private staging, verified promotion, duplicate completion, missing objects, size/checksum mismatch, cross-user completion/deletion, private signed access, cross-resource attachment, attached deletion denial, retriable storage deletion, idempotent delete, and expired staging cleanup. No real MinIO is contacted.

### Feature integration tests

Existing feature tests exercise profile, seller, ad, review, Short, live, and chat behavior. Media calls should be mocked only at the storage boundary or where a feature test is intentionally isolated. Keep API response, DB relationship, authorization, and cleanup assertions.

## Required CI checks

Run the repository's locked toolchain and existing checks, including compilation, Ruff/format, repository validation, pre-commit, Frappe tests, production-foundation tests, rate-limit coverage, monitoring/deployment validation, Semgrep, secret scanning, dependency audit, Compose/infrastructure validation, and service tests.

Do not mark a Bench/Frappe test as passed when only Python compilation ran. Record unavailable dependencies explicitly.

## Manual API test

1. Create two enabled users.
2. Initialize a public image as user A, PUT valid bytes, confirm, and read as guest.
3. Try confirming/deleting as user B; expect ownership errors.
4. Initialize a private verification PDF, confirm, and verify guest access fails.
5. Attach an image to user A's profile; direct delete must fail.
6. Replace the image; verify the old object is `Replaced` and the new ID is authoritative.
7. Simulate storage outage during deletion; verify `Delete Pending`, then restore and retry.
8. Upload malformed/spoofed/oversized content and verify stable sanitized errors.
9. Run cleanup and verify active referenced media remains.

## Test hygiene

Never depend on live MinIO, background-removal, video, moderation, or image-search services in deterministic unit tests. Use unique fixture prefixes, clean database rows, avoid real signed URLs, and do not weaken security gates for fixtures.

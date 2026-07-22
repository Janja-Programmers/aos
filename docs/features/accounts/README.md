# Accounts subsystem

Accounts owns persisted marketplace identity, profile state, account preferences, public/private profile serialization, avatar attachment authorization, and account lifecycle orchestration. Credentials and sessions remain in Auth; localization master data remains in Localization; bytes and storage lifecycle remain in Media; Seller and Verification retain their business workflows.

## Guarantees

- All self-service writes derive the account from the authenticated session.
- Profile updates use an explicit allowlist and reject unknown/system-managed fields.
- Public responses use immutable opaque `ACC-*` references and never expose email-backed `User.name`.
- Private responses use `Cache-Control: private, no-store`.
- Avatar changes attach a ready, owner-matching `profile_image` Media object before releasing the old object.
- Preference updates lock one row and preserve unrelated fields.
- Deactivation, deletion, suspension, and active state are distinct.
- Deactivation/deletion revoke all sessions, push tokens, and authentication verification tokens.

See the companion documents for API, lifecycle, migration, security, operations, and testing details.

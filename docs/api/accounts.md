# Accounts API reference

The versioned Accounts surface is documented in [`docs/features/accounts/api.md`](../features/accounts/api.md). Persisted preferences remain under Accounts; Auth retains login, `/me`, logout, deletion confirmation, and restoration OTP; Media retains avatar bytes and storage lifecycle.

Public account responses use opaque `ACC-*` IDs. Legacy email/User identifiers are accepted only as compatibility inputs where documented and are never returned as public account identifiers.

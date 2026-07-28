# Privacy

Internal Seller document names are retained for database compatibility but are never intentionally returned by public Seller, Maps compatibility, Ads detail, Reviews, Shorts, or chat-preview serializers.

Public APIs use an immutable opaque identifier matching `SELLER-[A-Z2-7]{20}`. Public account identity comes from Accounts serializers, which mask deleted/deactivated users and exclude email, phone, roles, sessions, private verification evidence, storage keys, and operational metadata.

Public list/detail caches must not be reused for self/admin data. Exact coordinates are only exposed by the existing Maps-facing contracts according to current product policy; their privacy model will be revisited in the Maps phase.

# AOS Feature Documentation

Feature documentation is the canonical human-readable description of AOS business and application domains. Production-hardened features use one comprehensive `README.md` as the single authoritative feature document. Older unhardened feature folders may still be split and are consolidated when hardened.

## Product and application domains

| Feature | Ownership | API |
|---|---|---|
| [Authentication](authentication/README.md) | Registration, login/session, OTP, social login, password recovery/change, delete/restore authentication flow | [Contract](authentication/README.md) |
| [Accounts](accounts/README.md) | Profile, preferences, account lifecycle, cross-domain deletion/restore ownership | [API](accounts/README.md) |
| [Activity](activity/README.md) | Private user activity history and cleanup | [API](activity/api.md) |
| [Marketplace Discovery — Ads](marketplace-discovery/README.md) | Canonical marketplace Ad/draft lifecycle, eligibility and projections | [Ads API](ads/api.md) |
| [Analytics ingestion](analytics/README.md) | Bounded client telemetry and durable analytics handoff | [API](analytics/api.md) |
| [Calls](calls/README.md) | Call lifecycle, LiveKit grants, history and reconciliation | [API](calls/api.md) |
| [Catalog](catalog/README.md) | Category/schema master data and Ad attribute rules | [API](catalog/README.md) |
| [Chat](chat/README.md) | Conversations, messages, attachments, reactions, receipts, presence and translation | [API](chat/api.md) |
| [Diagnostics](diagnostics/README.md) | System-Manager production/health/readiness reports | [API](diagnostics/api.md) |
| [Live](live/README.md) | Live lifecycle, LiveKit, participants/co-hosts, comments, reactions and tracking | [API](live/api.md) |
| [Localization](localization/README.md) | Country/language/currency/location contract and preferences (single authoritative document) | [API](localization/README.md) |
| [Maps](maps/README.md) | Geocoding, routing and seller-location services | [API](maps/api.md) |
| [Media](media/README.md) | Upload/confirm/read/delete lifecycle, object storage and processing | [API](media/README.md) |
| [Notifications](notifications/README.md) | In-app notifications, push tokens and delivery handoff | [API](notifications/README.md) |
| [Reports](reports/README.md) | User/Ad/Short reporting and moderation reasons | [API](reports/api.md) |
| [Reviews](reviews/README.md) | Reviews, eligibility, reactions, moderation and aggregates | [API](reviews/api.md) |
| [Marketplace Discovery — Saved Searches](marketplace-discovery/README.md) | User-scoped canonical Ads search intent | [Saved Search API](saved-search/api.md) |
| [Marketplace Discovery — Search Ranking](marketplace-discovery/README.md) | Candidate generation, ranking policy and derived discovery indexes | [Search Ranking API](search-ranking/api.md) |
| [Sellers](sellers/README.md) | Seller profile/state, storefront, operating hours and location ownership | [API](sellers/api.md) |
| [Shorts](shorts/README.md) | Short lifecycle, feeds, interactions, analytics, sounds and processing | [API](shorts/api.md) |
| [Social](social/README.md) | Follow/friend/block relationships and privacy | [API](social/api.md) |
| [Verification](verification/README.md) | Personal/business verification requests and private evidence | [API](verification/README.md) |
| [Wishlist](wishlist/README.md) | User Ad wishlist state and reads | [API](wishlist/api.md) |

## Platform-only HTTP surfaces

Some HTTP methods exist for infrastructure rather than product clients. They are intentionally **not** duplicated into fake product features:

- LiveKit webhook → [LiveKit integration](live/livekit.md)
- Moderation callback → [Content moderation service](../production/content-moderation-service.md)
- Notifications signed delivery callback → [Notifications](notifications/README.md)
- Video-processing callback → [Video processing service](../production/video-processing-service.md)
- Private Prometheus/background/backup metrics → [Production operations](../production/operations.md)

See the [complete API reference](../api/reference.md) for every whitelisted route and its exact code-level exposure.

# AOS Feature Documentation

Feature documentation is the canonical human-readable description of AOS business and application domains. Start with a feature `README.md`, then use its `api.md` for the public contract and the specialized lifecycle/security/operations pages when present.

## Product and application domains

| Feature | Ownership | API |
|---|---|---|
| [Authentication](authentication/api.md) | Registration, login/session, OTP, social login, password recovery/change, delete/restore authentication flow | [Contract](authentication/api.md) |
| [Accounts](accounts/api.md) | Profile, preferences, account lifecycle, cross-domain deletion/restore ownership | [API](accounts/api.md) |
| [Activity](activity/README.md) | Private user activity history and cleanup | [API](activity/api.md) |
| [Ads](ads/README.md) | Marketplace Ad/draft lifecycle, discovery, Media references and moderation triggers | [API](ads/api.md) |
| [Analytics ingestion](analytics/README.md) | Bounded client telemetry and durable analytics handoff | [API](analytics/api.md) |
| [Calls](calls/README.md) | Call lifecycle, LiveKit grants, history and reconciliation | [API](calls/api.md) |
| [Catalog](catalog/README.md) | Category/schema master data and Ad attribute rules | [API](catalog/api.md) |
| [Chat](chat/README.md) | Conversations, messages, attachments, reactions, receipts, presence and translation | [API](chat/api.md) |
| [Diagnostics](diagnostics/README.md) | System-Manager production/health/readiness reports | [API](diagnostics/api.md) |
| [Live](live/README.md) | Live lifecycle, LiveKit, participants/co-hosts, comments, reactions and tracking | [API](live/api.md) |
| [Localization](localization/api.md) | Country/language/currency/location contract and preferences (single authoritative document) | [API](localization/api.md) |
| [Maps](maps/README.md) | Geocoding, routing and seller-location services | [API](maps/api.md) |
| [Media](media/api.md) | Upload/confirm/read/delete lifecycle, object storage and processing | [API](media/api.md) |
| [Notifications](notifications/README.md) | In-app notifications, push tokens and delivery handoff | [API](notifications/api.md) |
| [Reports](reports/README.md) | User/Ad/Short reporting and moderation reasons | [API](reports/api.md) |
| [Reviews](reviews/README.md) | Reviews, eligibility, reactions, moderation and aggregates | [API](reviews/api.md) |
| [Saved Search](saved-search/README.md) | User-scoped reusable Ads search criteria | [API](saved-search/api.md) |
| [Search and Ranking](search-ranking/README.md) | Related-Ad recommendations and durable indexing/ranking handoff | [API](search-ranking/api.md) |
| [Sellers](sellers/README.md) | Seller profile/state, storefront, operating hours and location ownership | [API](sellers/api.md) |
| [Shorts](shorts/README.md) | Short lifecycle, feeds, interactions, analytics, sounds and processing | [API](shorts/api.md) |
| [Social](social/README.md) | Follow/friend/block relationships and privacy | [API](social/api.md) |
| [Verification](verification/README.md) | Personal/business verification requests and private evidence | [API](verification/api.md) |
| [Wishlist](wishlist/README.md) | User Ad wishlist state and reads | [API](wishlist/api.md) |

## Platform-only HTTP surfaces

Some HTTP methods exist for infrastructure rather than product clients. They are intentionally **not** duplicated into fake product features:

- LiveKit webhook → [LiveKit integration](live/livekit.md)
- Moderation callback → [Content moderation service](../production/content-moderation-service.md)
- Notification-delivery callback → [Notification delivery service](../production/notification-delivery-service.md)
- Video-processing callback → [Video processing service](../production/video-processing-service.md)
- Private Prometheus/background/backup metrics → [Production operations](../production/operations.md)

See the [complete API reference](../api/reference.md) for every whitelisted route and its exact code-level exposure.

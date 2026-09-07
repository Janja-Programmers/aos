# Privacy

Public review serialization uses Accounts public identity primitives and includes only opaque public account ID, display name, avatar and safe availability/live state. It never returns email, phone, internal roles, session data, profile private fields, conversation IDs, reporter identities, moderation scores, provider responses, storage keys or private signed URLs.

When a reviewer enters recoverable deletion, historical reviews remain for marketplace integrity and Accounts serialization renders the reviewer as `Deleted User`. Private review reactions/reports remain stored during the 30-day restore window so restoration is reversible; permanent cleanup removes those private rows and repairs affected counters after the deadline. Restoring the account immediately restores the original reviewer identity because the review row was never rewritten.

When a reviewed seller account is deleted, Seller/Ads lifecycle handling removes the target from active discovery while legitimate historical review rows remain for integrity and audit. Seller suspension/deletion does not grant the seller power to edit or erase reviews.

Author/self views may additionally return lifecycle status, a stable rejection reason code, a generic product-safe message, and edit/delete capability flags. Raw moderator notes and provider classifications remain internal even for the author.

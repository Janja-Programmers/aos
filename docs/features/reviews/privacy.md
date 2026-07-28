# Privacy

Public review serialization uses Accounts public identity primitives and includes only opaque public account ID, display name, avatar and safe availability/live state. It never returns email, phone, internal roles, session data, profile private fields, conversation IDs, reporter identities, moderation scores, provider responses, storage keys or private signed URLs.

When a reviewer deletes their account, historical reviews remain for marketplace integrity but Accounts serialization renders the reviewer as `Deleted User`. Private review reactions and reports created by the deleted account are removed and reaction counters are repaired. Reviews are not automatically republished or destroyed on account restore.

When a reviewed seller account is deleted, Seller/Ads lifecycle handling removes the target from active discovery while legitimate historical review rows remain for integrity and audit. Seller suspension/deletion does not grant the seller power to edit or erase reviews.

Author/self views may additionally return lifecycle status, a stable rejection reason code, a generic product-safe message, and edit/delete capability flags. Raw moderator notes and provider classifications remain internal even for the author.

# Seller integration

A review targets an ad and contributes to the ad's seller aggregate. New reviews require the seller to be `Active`. Self-review is rejected by comparing the authenticated reviewer with the canonical seller user.

Seller suspension or deletion does not erase legitimate historical reviews or rewrite ratings. It prevents new review eligibility. The authenticated `list_reviews_received` endpoint resolves the current user's seller server-side and cannot be used to request another seller's private view.

The current model has no seller reply entity or endpoint. Reply ownership, moderation and lifecycle were therefore not invented during this phase. A future reply feature should add a dedicated child/entity and a versioned contract rather than reuse general comments.

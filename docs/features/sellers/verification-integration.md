# Verification integration boundary

Verification owns applications, evidence, review, and approval/rejection decisions. Seller owns the projection of an approved business decision onto `seller_type` and `business_category`.

Approved business verification calls `sync_verified_business_profile()`. It does not directly change Seller status. `get_my_seller_status` exposes Seller capabilities to Verification and Ads without duplicating lifecycle rules.

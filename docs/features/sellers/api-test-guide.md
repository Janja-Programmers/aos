# API test guide

Use an authenticated `sid` cookie for private endpoints. Replace placeholders with real opaque IDs and Media IDs.

## List

```http
GET /api/method/aos.api.v1.sellers.list_sellers?sort=rating&limit=20&offset=0
```

Verify only active Sellers are returned and every `seller_id` is opaque. Test invalid sort, oversized limit, blocks, seller type, category, verification, and nearby filtering.

## Detail

```http
GET /api/method/aos.api.v1.sellers.get_seller?seller_id=SELLER-...
```

Verify active detail, operating hours, public Account identity, rating/counts, and no email/phone/internal Seller name. Suspended, deleted, blocked, and unknown targets should not be exposed.

## My status

```http
GET /api/method/aos.api.v1.sellers.get_my_seller_status
Cookie: sid=<session>
```

Verify status and capability flags for no Seller, Active, Suspended, and Deleted states.

## Update storefront

```http
POST /api/method/aos.api.v1.sellers.update_my_seller
Content-Type: application/json
Cookie: sid=<session>

{
  "business_category": "Vehicles",
  "about_business": "Verified local vehicle seller.",
  "operating_hours": [
    {"day_of_week": "Monday", "is_open": true, "open_time": "08:00", "close_time": "17:00"}
  ],
  "shop_banner_media": "MEDIA-...",
  "expected_version": 0
}
```

Verify a successful version increment, identical retry idempotency, stale-version conflict, cross-user Media denial, unknown-field rejection, suspended-seller rejection, and no direct status/type/metric mutation.

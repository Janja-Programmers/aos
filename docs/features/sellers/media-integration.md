# Media integration

Storefront banners use the central Media subsystem with purpose `seller_banner`.

Seller verifies the authenticated owner and target resource, then requests Media attachment. Media owns MIME/content validation, public visibility, storage identity, canonical URL generation, replacement, and release. Arbitrary external URLs and cross-user Media IDs are rejected.

Seller never calls MinIO directly and never stores a client-provided storage key or signed URL.

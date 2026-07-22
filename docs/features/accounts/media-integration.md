# Avatar and Media integration

Clients first complete a Media upload with purpose `profile_image`. `update_profile` then accepts the Media ID.

Accounts calls `MediaService.attach_media` with owner, purpose, `AOS Profile`, account name, and profile field. The new Media object is attached before the previous object is released. If any database operation fails, the transaction rolls back and the old avatar remains canonical. Removal is idempotent and releases metadata for normal orphan cleanup.

Accounts does not accept arbitrary avatar URLs, bucket names, object keys, or private Media IDs from other users.

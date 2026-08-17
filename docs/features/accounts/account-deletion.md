# Account deletion policy

Auth validates the `DELETE` confirmation and restoration OTP. `AccountLifecycleService` owns state mutation and calls the existing feature cleanup service.

The policy ends calls/live participation, hides seller/listing/Short surfaces, removes private personalization/social rows where safe, revokes verification activity, marks notifications read, and preserves foreign-key-dependent history. Avatar Media is detached and becomes eligible for normal Media orphan cleanup; Accounts never directly deletes storage objects.

Restoration re-enables account access. A Seller that was marked `Deleted` specifically by the Accounts deletion lifecycle is restored to the status captured immediately before deletion (`Active` or `Suspended`); a manually/moderation-deleted Seller is never reactivated by account restore. Stale Ads, Shorts, verification state, and other moderated/public content are **not** automatically republished and must return through their owning domain workflow.

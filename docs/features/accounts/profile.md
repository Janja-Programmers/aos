# Profile policy

Editable canonical fields are `display_name`, `legal_name`, `phone`, `date_of_birth`, `gender`, `bio`, and avatar operations. Account location is owned exclusively by Localization through `AOS User Preference.location`; Accounts does not duplicate it on `AOS Profile`.

There are no compatibility aliases. Inputs such as `full_name`, `mobile_no`, `birth_date`, `profile_image_media`, `user_image_media`, or `media_id` are not translated into profile fields and are rejected when they are not part of the endpoint contract.

Roles, email, enabled state, lifecycle state, verification state, seller state, account ID, reviewer fields, and counters are system-managed. `AOS Profile.name` itself is the immutable opaque `ACC-*` account ID. Unknown and system-managed keys are rejected rather than ignored.

Text is NFC-normalized, whitespace is bounded, null/control characters are rejected, and field lengths are enforced. Phone writes require unambiguous E.164 form after safe normalization. Dates must be ISO-compatible, not in the future, and not earlier than 1900.

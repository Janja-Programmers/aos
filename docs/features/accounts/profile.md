# Profile policy

Editable canonical fields are `display_name`, `legal_name`, `phone`, `date_of_birth`, `gender`, `bio`, `location`, and avatar operations. Compatibility aliases (`full_name`, `mobile_no`, `birth_date`, `profile_image_media`, `user_image_media`, `media_id`) map to canonical fields.

Roles, email, enabled state, lifecycle state, verification state, seller state, public ID, reviewer fields, and counters are system-managed. Unknown and system-managed keys are rejected rather than ignored.

Text is NFC-normalized, whitespace is bounded, null/control characters are rejected, and field lengths are enforced. Phone writes require unambiguous E.164 form after safe normalization. Dates must be ISO-compatible, not in the future, and not earlier than 1900.

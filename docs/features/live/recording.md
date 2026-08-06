# Live Recording and Replay

## Current status

Recording and replay are unsupported in the inspected backend product model.

No Live recording/replay DocType, LiveKit egress service, egress webhook, object-storage ownership workflow, processing state, replay publication endpoint, thumbnail pipeline, retention policy, or recording-ready notification was found.

This hardening therefore does not start egress, create storage keys, publish replays, or expose replay URLs.

## Future implementation boundary

A production implementation would require an explicit product design for:

- host opt-in and consent;
- Egress API credentials and bounded external intent/reconciliation;
- verified/deduplicated egress webhooks;
- Media ownership and non-guessable object keys;
- processing/failed/ready/deleted states;
- replay audience and block policy equal to direct Live access;
- moderation and takedown;
- retention and account-deletion behavior;
- thumbnails/transcoding;
- host-only publish/delete controls;
- notification and analytics contracts.

Do not infer recording support from LiveKit room availability alone.

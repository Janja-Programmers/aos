# Calls and shared LiveKit

Calls consumes the production-ready shared LiveKit services. The application membership cap is 2 for a direct call and 32 for a group call. The shared provider room is server-provisioned with a 32-seat ceiling from the start so an ongoing direct call can be promoted to a conference without deleting/recreating RTC state. That provider ceiling is not authorization: only durable joined members can receive server-generated tokens.

Provider room creation occurs outside database row locks. After the provider returns, Calls re-locks/revalidates durable state and Social/Accounts policy before setting `rtc_provisioned_at` and dispatching per-recipient incoming invitations. Failure is closed: no incoming signal or join token is exposed.

Call tokens remain short-lived and least privilege. Audio may publish microphone only; video may publish microphone+camera. Participants may subscribe but cannot publish LiveKit data or mutate participant metadata. Room identity, participant identity, grants, and TTL are server-controlled. Live uses its separate existing token path and is not changed by conference Calls.

Group rooms are provisioned at the server maximum from the start so adding participants does not require recreating the room. Durable membership still controls who may receive a token; LiveKit room capacity alone never authorizes joining.

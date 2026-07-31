"""Shorts notification safety helpers."""

from __future__ import annotations

from aos.services.social.repository import SocialRepository


def should_notify(*, actor: str | None, recipient: str | None) -> bool:
    actor = str(actor or "").strip()
    recipient = str(recipient or "").strip()
    if not actor or not recipient or actor == recipient:
        return False
    _outgoing, _incoming, block_map = SocialRepository().relationship_sets(
        viewer=actor, targets=[recipient]
    )
    return not any(block_map.get(recipient, (False, False)))

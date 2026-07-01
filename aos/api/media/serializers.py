from __future__ import annotations

from aos.services.media.media_service import serialize_media_doc


def serialize_media(doc, *, url: str | None = None, include_private_fields: bool = False) -> dict:
    return serialize_media_doc(
        doc,
        url=url,
        include_private_fields=include_private_fields,
    )

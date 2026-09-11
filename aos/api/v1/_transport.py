"""Transitional v1 transport helper exports for not-yet-hardened features.

Production-hardened feature wrappers import and invoke
``aos.api.shared.transport.execute_endpoint`` directly. This module remains only
because several features that have not completed their hardening pass still
import ``client_kwargs``; each should migrate to the canonical executor during
its own production-readiness pass.
"""

from aos.api.shared.transport import client_kwargs

__all__ = ["client_kwargs"]

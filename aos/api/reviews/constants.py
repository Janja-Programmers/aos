"""Backward-compatible Reviews API constants.

New code should use :mod:`aos.services.reviews.constants`. These aliases remain
for internal callers that imported the original module before Reviews was
centralised.
"""

from aos.services.reviews.constants import RATE_LIMITS

CREATE_REVIEW_LIMIT_PER_MINUTE_PER_USER = RATE_LIMITS["create"]
TOGGLE_REACTION_LIMIT_PER_MINUTE_PER_USER = RATE_LIMITS["reaction"]

"""Pure callback-correlation invariants shared by durable companion services."""

from __future__ import annotations


def accepted_dispatch_generations(
	*, current_generation: int, proposed_generation: int
) -> frozenset[int]:
	"""Return generations that may proceed to dispatch-token validation.

	A missing proposed generation is stored as ``0``. It must not be treated as
	an accepted generation unless it is also the actual current generation.
	"""

	accepted = {int(current_generation)}
	proposed = int(proposed_generation)
	if proposed > 0:
		accepted.add(proposed)
	return frozenset(accepted)

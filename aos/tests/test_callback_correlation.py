from __future__ import annotations

import unittest

from aos.services.callback_correlation import accepted_dispatch_generations


class TestCallbackCorrelation(unittest.TestCase):
	def test_missing_proposed_generation_does_not_accept_zero(self):
		self.assertEqual(
			accepted_dispatch_generations(current_generation=1, proposed_generation=0),
			frozenset({1}),
		)

	def test_positive_proposed_generation_is_accepted(self):
		self.assertEqual(
			accepted_dispatch_generations(current_generation=1, proposed_generation=2),
			frozenset({1, 2}),
		)

	def test_zero_current_generation_remains_explicitly_current(self):
		self.assertEqual(
			accepted_dispatch_generations(current_generation=0, proposed_generation=0),
			frozenset({0}),
		)

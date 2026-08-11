from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from aos.services.verification.errors import VerificationConflictError, VerificationPermissionError
from aos.services.verification.lifecycle import validate_status_transition


class TestVerificationLifecycle(unittest.TestCase):
    @staticmethod
    def _doc(status: str, action: str = ""):
        return SimpleNamespace(status=status, flags=SimpleNamespace(aos_verification_action=action))

    def test_new_request_must_start_pending(self):
        validate_status_transition(self._doc("Pending"), None, actor="user@example.com")
        with self.assertRaises(VerificationConflictError):
            validate_status_transition(self._doc("Approved"), None, actor="user@example.com")

    @patch("aos.services.verification.lifecycle.is_reviewer", return_value=False)
    def test_normal_user_cannot_review(self, _reviewer):
        with self.assertRaises(VerificationPermissionError):
            validate_status_transition(
                self._doc("Approved"),
                SimpleNamespace(status="Pending"),
                actor="user@example.com",
            )

    @patch("aos.services.verification.lifecycle.is_reviewer", return_value=True)
    def test_reviewer_can_approve_pending(self, _reviewer):
        validate_status_transition(
            self._doc("Approved"),
            SimpleNamespace(status="Pending"),
            actor="reviewer@example.com",
        )

    @patch("aos.services.verification.lifecycle.is_reviewer", return_value=True)
    def test_terminal_status_cannot_regress_through_desk(self, _reviewer):
        with self.assertRaises(VerificationConflictError):
            validate_status_transition(
                self._doc("Pending"),
                SimpleNamespace(status="Rejected"),
                actor="reviewer@example.com",
            )

    def test_resubmit_is_only_rejected_or_revoked_to_pending(self):
        validate_status_transition(
            self._doc("Pending", action="resubmit"),
            SimpleNamespace(status="Rejected"),
            actor="user@example.com",
        )
        with self.assertRaises(VerificationConflictError):
            validate_status_transition(
                self._doc("Pending", action="resubmit"),
                SimpleNamespace(status="Approved"),
                actor="user@example.com",
            )


if __name__ == "__main__":
    unittest.main()

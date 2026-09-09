from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.auth.email_delivery import schedule_auth_email_delivery, send_auth_email_queue
from aos.api.auth.verification import EMAIL_VERIFICATION_PURPOSE, queue_otp_email


class TestAuthEmailDelivery(FrappeTestCase):
    def test_otp_email_is_durable_redacted_and_kicked_after_queue_insert(self):
        queued = SimpleNamespace(name="EMAIL-QUEUE-TEST")
        with patch("aos.api.auth.verification.frappe.sendmail", return_value=queued) as sendmail, patch(
            "aos.api.auth.email_delivery.schedule_auth_email_delivery"
        ) as schedule:
            queue_otp_email(
                email="person@example.com",
                otp="123456",
                full_name="Person",
                purpose=EMAIL_VERIFICATION_PURPOSE,
            )

        kwargs = sendmail.call_args.kwargs
        self.assertFalse(kwargs["now"])
        self.assertTrue(kwargs["redact_message_after_send"])
        self.assertEqual(kwargs["recipients"], ["person@example.com"])
        schedule.assert_called_once_with("EMAIL-QUEUE-TEST")

    def test_low_latency_kick_is_registered_after_commit(self):
        with patch.object(frappe.db.after_commit, "add") as add, patch(
            "aos.api.auth.email_delivery._enqueue_after_commit"
        ) as enqueue:
            schedule_auth_email_delivery("EMAIL-QUEUE-TEST")
            callback = add.call_args.args[0]
            enqueue.assert_not_called()
            callback()
            enqueue.assert_called_once_with("EMAIL-QUEUE-TEST")

    def test_delivery_worker_serializes_and_skips_already_sent_queue(self):
        sent = Mock()
        sent.is_to_be_sent.return_value = False
        with patch("aos.api.auth.email_delivery.frappe.db.sql", return_value=[{"name": "EMAIL-QUEUE-TEST"}]) as sql, patch(
            "aos.api.auth.email_delivery.frappe.get_doc", return_value=sent
        ):
            send_auth_email_queue("EMAIL-QUEUE-TEST")

        self.assertIn("FOR UPDATE", sql.call_args.args[0])
        sent.send.assert_not_called()

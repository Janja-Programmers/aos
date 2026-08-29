from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from aos.api.notifications import token as token_api


class TestNotificationTokenDeadlockHardening(unittest.TestCase):
    def test_first_time_token_lookup_does_not_take_gap_or_table_scan_locks(self):
        with patch.object(frappe.db, "sql", side_effect=[[], []]) as sql:
            result = token_api._find_existing_token(
                token="fcm_test_token_abcdefghijklmnopqrstuvwxyz_0123456789",
                token_hash="a" * 64,
            )

        self.assertIsNone(result)
        self.assertEqual(sql.call_count, 2)
        hash_query = str(sql.call_args_list[0].args[0])
        legacy_query = str(sql.call_args_list[1].args[0])
        self.assertIn("WHERE token_hash = %s", hash_query)
        self.assertNotIn("FOR UPDATE", hash_query.upper())
        self.assertIn("WHERE token = %s", legacy_query)
        self.assertIn("COALESCE(token_hash, '') = ''", legacy_query)
        self.assertNotIn("FOR UPDATE", legacy_query.upper())

    def test_existing_token_is_locked_only_by_primary_key_and_revalidated(self):
        row_name = "PUSH-TOKEN-1"
        candidate = SimpleNamespace(name=row_name)
        locked = SimpleNamespace(
            name=row_name,
            user="owner@example.com",
            token="fcm_test_token_abcdefghijklmnopqrstuvwxyz_0123456789",
            token_hash="b" * 64,
            device_id="device-1",
        )
        with patch.object(frappe.db, "sql", side_effect=[[candidate], [locked]]) as sql:
            result = token_api._find_existing_token(
                token=locked.token,
                token_hash=locked.token_hash,
            )

        self.assertEqual(result, row_name)
        self.assertEqual(sql.call_count, 2)
        discovery_query = str(sql.call_args_list[0].args[0])
        lock_query = str(sql.call_args_list[1].args[0])
        self.assertNotIn("FOR UPDATE", discovery_query.upper())
        self.assertIn("WHERE name = %s", lock_query)
        self.assertIn("FOR UPDATE", lock_query.upper())
        self.assertEqual(sql.call_args_list[1].args[1], (row_name,))

    def test_legacy_raw_token_scan_is_nonlocking_then_primary_key_locked(self):
        row_name = "PUSH-TOKEN-LEGACY"
        token = "fcm_legacy_token_abcdefghijklmnopqrstuvwxyz_0123456789"
        candidate = SimpleNamespace(name=row_name)
        locked = SimpleNamespace(
            name=row_name,
            user="owner@example.com",
            token=token,
            token_hash="",
            device_id="device-legacy",
        )
        with patch.object(
            frappe.db,
            "sql",
            side_effect=[[], [candidate], [locked]],
        ) as sql:
            result = token_api._find_existing_token(token=token, token_hash="c" * 64)

        self.assertEqual(result, row_name)
        legacy_query = str(sql.call_args_list[1].args[0])
        lock_query = str(sql.call_args_list[2].args[0])
        self.assertNotIn("FOR UPDATE", legacy_query.upper())
        self.assertIn("COALESCE(token_hash, '') = ''", legacy_query)
        self.assertIn("WHERE name = %s", lock_query)
        self.assertIn("FOR UPDATE", lock_query.upper())

    def test_device_discovery_is_nonlocking_then_primary_key_locked(self):
        row_name = "PUSH-TOKEN-DEVICE"
        candidate = SimpleNamespace(name=row_name)
        locked = SimpleNamespace(
            name=row_name,
            user="owner@example.com",
            token="token-value",
            token_hash="d" * 64,
            device_id="device-1",
        )
        with patch.object(frappe.db, "sql", side_effect=[[candidate], [locked]]) as sql:
            result = token_api._find_existing_device(
                user=locked.user,
                device_id=locked.device_id,
            )

        self.assertEqual(result, row_name)
        discovery_query = str(sql.call_args_list[0].args[0])
        lock_query = str(sql.call_args_list[1].args[0])
        self.assertNotIn("FOR UPDATE", discovery_query.upper())
        self.assertIn("WHERE name = %s", lock_query)
        self.assertIn("FOR UPDATE", lock_query.upper())

    def test_registration_retries_transient_deadlock_after_transaction_reset(self):
        deadlock = frappe.QueryDeadlockError("simulated deadlock")
        expected = ("PUSH-TOKEN-1", "updated")
        kwargs = {
            "user": "owner@example.com",
            "token": "fcm_test_token_abcdefghijklmnopqrstuvwxyz_0123456789",
            "device_type": "android",
            "device_id": "device-1",
            "registration_kind": "token",
        }
        with (
            patch.object(
                token_api, "_register_attempt", side_effect=[deadlock, expected]
            ) as attempt,
            patch.object(token_api, "rollback_deadlocked_transaction") as rollback,
            patch.object(token_api.time, "sleep") as sleep,
        ):
            result = token_api._register_with_deadlock_retry(**kwargs)

        self.assertEqual(result, expected)
        self.assertEqual(attempt.call_count, 2)
        rollback.assert_called_once_with()
        sleep.assert_called_once_with(token_api._REGISTER_DEADLOCK_BACKOFF_SECONDS)

    def test_registration_deadlock_retry_is_bounded(self):
        deadlock = frappe.QueryDeadlockError("simulated deadlock")
        kwargs = {
            "user": "owner@example.com",
            "token": "fcm_test_token_abcdefghijklmnopqrstuvwxyz_0123456789",
            "device_type": "android",
            "device_id": "device-1",
            "registration_kind": "token",
        }
        with (
            patch.object(
                token_api,
                "_register_attempt",
                side_effect=[deadlock, deadlock, deadlock],
            ) as attempt,
            patch.object(token_api, "rollback_deadlocked_transaction") as rollback,
            patch.object(token_api.time, "sleep") as sleep,
        ):
            with self.assertRaises(frappe.QueryDeadlockError):
                token_api._register_with_deadlock_retry(**kwargs)

        self.assertEqual(attempt.call_count, token_api._REGISTER_DEADLOCK_ATTEMPTS)
        self.assertEqual(rollback.call_count, token_api._REGISTER_DEADLOCK_ATTEMPTS)
        self.assertEqual(sleep.call_count, token_api._REGISTER_DEADLOCK_ATTEMPTS - 1)


if __name__ == "__main__":
    unittest.main()

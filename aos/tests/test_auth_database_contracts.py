from __future__ import annotations

import inspect
import frappe
from frappe.tests.utils import FrappeTestCase

from aos.patches.v1_0 import add_auth_indexes


class TestAuthDatabaseContracts(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        add_auth_indexes.execute()
        frappe.db.commit()

    def test_auth_indexes_exist(self):
        for constraint in add_auth_indexes.AUTH_UNIQUE_CONSTRAINTS:
            with self.subTest(index=constraint["name"]):
                self.assertTrue(
                    self._index_exists(
                        doctype=constraint["doctype"],
                        index_name=constraint["name"],
                        unique_only=True,
                    ),
                    constraint["name"],
                )

        for index in add_auth_indexes.AUTH_INDEXES:
            with self.subTest(index=index["name"]):
                self.assertTrue(
                    self._index_exists(
                        doctype=index["doctype"],
                        index_name=index["name"],
                        unique_only=False,
                    ),
                    index["name"],
                )

    def test_one_time_auth_flows_use_database_row_locks(self):
        from aos.api.auth import delete_account, otp, password_reset
        from aos.api.auth.verification import get_ver_doc

        helper_source = inspect.getsource(get_ver_doc)
        self.assertIn("FOR UPDATE", helper_source)
        self.assertIn("for_update", helper_source)

        for function in (
            otp.verify_email_otp_impl,
            otp.resend_email_otp_impl,
            password_reset.forgot_password_request_impl,
            password_reset.forgot_password_verify_otp_impl,
            password_reset.forgot_password_reset_impl,
            delete_account.request_restore_account_impl,
            delete_account.restore_account_impl,
        ):
            with self.subTest(function=function.__name__):
                self.assertIn("for_update=True", inspect.getsource(function))

    def test_auth_never_bypasses_frappe_password_policy(self):
        from aos.api.auth import password_reset, register

        self.assertNotIn("ignore_password_policy", inspect.getsource(register.register_impl))
        self.assertNotIn("ignore_password_policy", inspect.getsource(password_reset.forgot_password_reset_impl))

    def test_auth_index_patch_is_idempotent(self):
        before = self._existing_auth_indexes()
        add_auth_indexes.execute()
        add_auth_indexes.execute()
        after = self._existing_auth_indexes()
        self.assertEqual(before, after)

    @staticmethod
    def _index_exists(doctype: str, index_name: str, *, unique_only: bool) -> bool:
        conditions = "AND NON_UNIQUE = 0" if unique_only else ""
        return bool(
            frappe.db.sql(
                f"""
                SELECT INDEX_NAME
                FROM information_schema.STATISTICS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = %s
                  AND INDEX_NAME = %s
                  {conditions}
                LIMIT 1
                """,
                (f"tab{doctype}", index_name),
            )
        )

    @classmethod
    def _existing_auth_indexes(cls) -> set[tuple[str, str]]:
        expected = [*add_auth_indexes.AUTH_UNIQUE_CONSTRAINTS, *add_auth_indexes.AUTH_INDEXES]
        result: set[tuple[str, str]] = set()
        for item in expected:
            if cls._index_exists(item["doctype"], item["name"], unique_only=False):
                result.add((item["doctype"], item["name"]))
        return result

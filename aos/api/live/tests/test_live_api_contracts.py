from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.shared.transport import client_kwargs
from aos.services.live.cursor import decode_cursor, encode_cursor
from aos.services.live.endpoints import ENDPOINT_SPECS, TRANSACTIONAL_ENDPOINTS
from aos.services.live.errors import LiveError
from aos.services.live.validation import validate_public_kwargs


class TestLiveApiContracts(FrappeTestCase):
    def _source(self, relative: str) -> str:
        return Path(frappe.get_app_path("aos", *relative.split("/"))).read_text(encoding="utf-8")

    def test_cmd_is_transport_only_and_unknown_fields_fail_closed(self):
        payload = client_kwargs(
            {
                "cmd": "aos.api.v1.live.get_live",
                "live_id": "LIVE-2026-00001",
            }
        )
        self.assertEqual(payload, {"live_id": "LIVE-2026-00001"})
        with self.assertRaises(LiveError) as ctx:
            validate_public_kwargs(
                {"live_id": "LIVE-2026-00001", "role": "host"},
                ENDPOINT_SPECS["get_live"],
            )
        self.assertEqual(ctx.exception.code, "LIVE_UNKNOWN_FIELD")

    def test_cursor_is_signed_endpoint_scoped_and_tamper_safe(self):
        with patch("aos.services.live.cursor._secret", return_value=b"live-test-secret"):
            cursor = encode_cursor(
                {
                    "kind": "live_feed",
                    "started_at": "2026-08-01 00:00:00",
                    "name": "LIVE-2026-00001",
                }
            )
            self.assertEqual(decode_cursor(cursor)["kind"], "live_feed")
            replacement = "A" if cursor[-1] != "A" else "B"
            with self.assertRaises(LiveError) as ctx:
                decode_cursor(cursor[:-1] + replacement)
            self.assertEqual(ctx.exception.code, "LIVE_INVALID_CURSOR")

    def test_public_wrapper_matches_strict_endpoint_registry(self):
        tree = ast.parse(self._source("api/v1/live/__init__.py"))
        public_names = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and not node.name.startswith("_")
        }
        self.assertEqual(public_names, set(ENDPOINT_SPECS))
        for endpoint in (
            "start_live",
            "join_live",
            "end_live",
            "get_live_token",
            "get_live_cohost_token",
            "track_join",
            "track_leave",
            "add_live_message",
            "reply_live_message",
            "delete_live_message",
            "send_reaction",
            "invite_live_cohost",
            "request_live_cohost",
            "respond_live_cohost",
            "cancel_live_cohost",
            "activate_live_cohost",
            "end_live_cohost",
        ):
            self.assertIn(endpoint, TRANSACTIONAL_ENDPOINTS)

    def test_every_public_live_endpoint_has_a_rate_policy(self):
        policy_path = Path(frappe.get_app_path("aos")).parent / "ci" / "public-endpoint-rate-limits.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        registered = {str(row.get("endpoint") or ""): row for row in policy}
        for endpoint in ENDPOINT_SPECS:
            key = f"aos.api.v1.live.__init__.{endpoint}"
            self.assertIn(key, registered)
        webhook = registered["aos.api.v1.livekit.__init__.handle_webhook"]
        self.assertEqual(webhook.get("policy"), "signed_internal_exempt")

    def test_live_domain_has_no_internal_commit_or_full_rollback(self):
        roots = (
            "api/live",
            "api/v1/live",
            "services/live",
            "tasks/live.py",
            "patches/v1_0/harden_live_subsystem.py",
            "patches/v1_0/install_live_indexes.py",
        )
        offenders: list[str] = []
        for relative in roots:
            path = Path(frappe.get_app_path("aos", *relative.split("/")))
            files = [path] if path.is_file() else list(path.rglob("*.py"))
            for file in files:
                if "tests" in file.parts:
                    continue
                tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                        continue
                    if node.func.attr == "commit":
                        offenders.append(f"{file}:{node.lineno}:commit")
                    if node.func.attr == "rollback" and not node.keywords:
                        offenders.append(f"{file}:{node.lineno}:full-rollback")
        self.assertEqual(offenders, [])

    def test_public_livekit_metadata_omits_private_account_values(self):
        source = self._source("services/live/livekit.py")
        self.assertNotIn('"email"', source)
        self.assertNotIn('"user"', source)
        self.assertIn('"account_id"', source)
        self.assertIn("hmac.new", source)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
CALL_SCOPE = (ROOT / "aos/api/calls", ROOT / "aos/services/calls")
PUBLIC_ENDPOINTS = {
    "initiate_call", "mark_call_ringing", "accept_call", "reject_call", "cancel_call", "end_call",
    "add_call_participants", "request_video_upgrade", "respond_video_upgrade", "get_call_status",
    "get_call_token", "list_calls", "delete_call_logs", "clear_call_history",
}


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


class TestCallsProductionSourceGuards(unittest.TestCase):
    def test_public_v1_surface_is_thin_strict_and_complete(self):
        source = _source("aos/api/v1/calls/__init__.py")
        for endpoint in PUBLIC_ENDPOINTS:
            self.assertIn(f"def {endpoint}", source)
            self.assertIn(f'"{endpoint}"', source)
        self.assertNotIn("get_call_group_details", source)
        self.assertIn("run_call_api", source)
        self.assertIn("ENDPOINT_SPECS", source)

    def test_mutation_modules_do_not_own_outer_transaction(self):
        offenders = []
        for root in CALL_SCOPE:
            for path in root.rglob("*.py"):
                if "tests" in path.parts or path.name == "api.py":
                    continue
                source = path.read_text(encoding="utf-8")
                if "frappe.db.commit(" in source or "frappe.db.rollback(" in source:
                    offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])

    def test_realtime_is_after_commit_and_never_exposes_room_name_or_internal_users(self):
        source = _source("aos/api/calls/realtime.py")
        tree = ast.parse(source)
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "publish_realtime"]
        self.assertGreater(len(calls), 0)
        for call in calls:
            kws = {kw.arg: kw.value for kw in call.keywords if kw.arg}
            self.assertIn("after_commit", kws)
            self.assertTrue(isinstance(kws["after_commit"], ast.Constant) and kws["after_commit"].value)
        serializer = source.split("def serialize_call_for_realtime", 1)[1].split("def _publish_for_users", 1)[0]
        self.assertNotIn('"room_name"', serializer)
        self.assertIn('"participants"', serializer)
        self.assertIn('"initiator"', serializer)
        self.assertIn("public_call_id(call)", serializer)

    def test_call_model_is_normalized_multi_participant_without_legacy_pair_fields(self):
        call_doc = json.loads(_source("aos/aos/doctype/aos_call/aos_call.json"))
        fields = {f["fieldname"] for f in call_doc["fields"]}
        for required in {"initiator", "call_mode", "max_participants", "participant_count", "public_id", "rtc_provisioned_at"}:
            self.assertIn(required, fields)
        for legacy in {"caller", "receiver", "visible_to_caller", "visible_to_receiver", "incoming_dispatched_at", "ring_expires_at"}:
            self.assertNotIn(legacy, fields)
        participant_doc = json.loads(_source("aos/aos/doctype/aos_call_participant/aos_call_participant.json"))
        pfields = {f["fieldname"] for f in participant_doc["fields"]}
        for required in {"call", "user", "role", "status", "visible", "incoming_dispatched_at", "ring_expires_at", "joined_at", "left_at"}:
            self.assertIn(required, pfields)

    def test_group_capacity_is_32_and_server_owned(self):
        call = _source("aos/api/calls/call.py")
        livekit = _source("aos/services/calls/livekit.py")
        self.assertIn('call.max_participants = 2 if call_mode == "direct" else 32', call)
        self.assertIn("DIRECT_CALL_MAX_PARTICIPANTS = 2", livekit)
        self.assertIn("GROUP_CALL_MAX_PARTICIPANTS = 32", livekit)
        self.assertIn("CALL_ROOM_MAX_PARTICIPANTS = GROUP_CALL_MAX_PARTICIPANTS", livekit)
        self.assertIn("room_capacity_for_call", livekit)
        self.assertNotIn("max_participants=kwargs", call)

    def test_group_lifecycle_is_participant_scoped(self):
        call = _source("aos/api/calls/call.py")
        self.assertIn("all_invitees_terminal", call)
        self.assertIn("any_invitee_joined", call)
        self.assertIn("if call.call_mode == \"direct\"", call)
        self.assertIn("remaining = joined_users(call.name)", call)
        self.assertIn("You left the call.", call)
        self.assertIn("add_call_participants_impl", call)
        self.assertIn("call_mode='group',max_participants=32", call)
        self.assertIn("publish_participants_invited", call)
        self.assertIn("A group call supports at most 32 participants.", call)

    def test_room_provisioning_fans_out_after_provider_io_without_db_lock(self):
        task = _source("aos/tasks/calls.py")
        provision = task.split("def provision_call_room", 1)[1].split("def _finalize_unanswered", 1)[0]
        self.assertIn("frappe.db.commit()", provision)
        self.assertLess(provision.index("frappe.db.commit()"), provision.index("provision_livekit_room"))
        self.assertIn("room_capacity_for_call(candidate.call_mode)", provision)
        self.assertIn("for user in valid_targets", provision)
        self.assertIn("NotificationService.notify_incoming_call", provision)
        self.assertIn("ring_expires_at", provision)

    def test_token_requires_joined_participant_and_public_identity(self):
        token = _source("aos/api/calls/token.py")
        self.assertIn('participant.status != "joined"', token)
        self.assertIn("participant_identity(current_user)", token)
        self.assertIn("ensure_call_interaction_allowed", token)
        self.assertIn("clear_missing_room_marker", token)
        self.assertIn("lock_call_row(call.name)", token)
        self.assertNotIn("lock_call_participants(call.name)", token)

    def test_incoming_push_suppression_uses_recipient_participant_deadline(self):
        source = _source("aos/services/notifications/delivery.py")
        self.assertIn('"AOS Call Participant"', source)
        self.assertIn('["status", "ring_expires_at", "added_by"]', source)
        self.assertIn('{"call": call.get("name"), "user": _clean(job.user)}', source)
        self.assertIn('actor = _clean(participant.get("added_by"))', source)
        self.assertNotIn('resolve_account_reference(actor_account_id)', source)
        self.assertNotIn('["caller", "receiver", "status", "ring_expires_at"]', source)

    def test_history_is_cursor_paginated_over_participant_membership(self):
        source = _source("aos/api/calls/history.py")
        self.assertIn("INNER JOIN `tabAOS Call Participant` p ON p.`call`=c.name", source)
        self.assertIn("p.user=%(user)s", source)
        self.assertIn("p.visible=1", source)
        self.assertIn("cursor_created_at", source)
        self.assertNotIn("OFFSET", source.upper())

    def test_calls_migration_replaces_legacy_pair_schema_and_installs_indexes(self):
        patches = _source("aos/patches.txt")
        migration_name = "aos.patches.v1_0.migrate_calls_to_conference_model"
        self.assertIn(migration_name, patches)
        self.assertLess(patches.index(migration_name), patches.index("aos.patches.v1_0.install_call_indexes"))
        self.assertNotIn("harden_calls_subsystem", patches)
        self.assertNotIn("harden_calls_public_contract", patches)
        migration = _source("aos/patches/v1_0/migrate_calls_to_conference_model.py")
        self.assertIn("BATCH = 200", migration)
        self.assertIn("AOS Call Participant", migration)
        self.assertIn("DROP COLUMN", migration)
        self.assertIn("Pre-conference active rooms", migration)
        self.assertIn("room_cleanup_pending=1", migration)
        self.assertIn('"naming_series"', migration)
        self.assertNotIn("frappe.db.commit", migration)
        self.assertNotIn("frappe.enqueue", migration)
        indexes = _source("aos/patches/v1_0/install_call_indexes.py")
        for name in ("uq_call_participant", "idx_call_participant_user_active", "idx_call_participant_history", "idx_call_participant_expiry"):
            self.assertIn(name, indexes)
        public_indexes = _source("aos/patches/v1_0/install_call_public_indexes.py")
        self.assertIn("uq_call_public_id", public_indexes)
        self.assertIn("rtc_provisioned_at", public_indexes)

    def test_account_deletion_does_not_make_group_initiator_room_owner(self):
        source = _source("aos/services/account_deletion_service.py")
        block = source.split("def _end_active_calls", 1)[1].split("def _end_active_live_streams", 1)[0]
        self.assertIn("AOS Call Participant", block)
        self.assertIn('if row.call_mode == "direct"', block)
        self.assertIn("joined > 0", block)
        self.assertIn("initiator is not the conference lifetime owner", block)

    def test_rate_limit_registry_matches_public_surface(self):
        entries = json.loads(_source("ci/public-endpoint-rate-limits.json"))
        registry = {e["endpoint"] for e in entries}
        for endpoint in PUBLIC_ENDPOINTS:
            self.assertIn(f"aos.api.v1.calls.__init__.{endpoint}", registry)
        self.assertNotIn("aos.api.v1.calls.__init__.get_call_group_details", registry)


    def test_call_internal_names_are_distributed_safe(self):
        model = _source("aos/aos/doctype/aos_call/aos_call.py")
        metadata = json.loads(_source("aos/aos/doctype/aos_call/aos_call.json"))
        self.assertIn('new_prefixed_name("CALL")', model)
        self.assertNotIn("naming_series", {f["fieldname"] for f in metadata["fields"]})
        self.assertNotEqual(metadata.get("autoname"), "naming_series:")

    def test_required_calls_documents_exist(self):
        for name in ("README.md", "api.md", "realtime.md", "livekit.md", "operations.md", "testing.md"):
            self.assertTrue((ROOT / "docs/features/calls" / name).is_file(), name)


if __name__ == "__main__":
    unittest.main()

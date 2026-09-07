from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class TestVerificationProductionSourceGuards(unittest.TestCase):
    def test_public_surface_preserves_only_existing_endpoints(self):
        source = _source("aos/api/v1/verification/__init__.py")
        tree = ast.parse(source)
        functions = {
            node.name
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and any(
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "whitelist"
                for decorator in node.decorator_list
            )
        }
        self.assertEqual(functions, {"submit_verification", "get_my_verification"})
        self.assertNotIn("approve_verification", source)
        self.assertNotIn("reject_verification", source)

    def test_submission_boundary_is_private_rate_limited_and_savepoint_atomic(self):
        source = _source("aos/api/verification/submit_verification.py")
        self.assertIn("VerificationService().submit", source)
        self.assertIn("set_private_no_store()", source)
        self.assertIn("rate_limit_key", source)
        self.assertIn("frappe.db.savepoint(savepoint)", source)
        self.assertIn("frappe.db.rollback(save_point=savepoint)", source)
        self.assertNotIn("frappe.db.commit", source)

    def test_verification_domain_services_do_not_commit_outer_transactions(self):
        offenders = []
        for path in (ROOT / "aos/services/verification").rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            if "frappe.db.commit(" in source or "frappe.db.rollback(" in source:
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])

    def test_submission_is_account_locked_strict_and_server_owned(self):
        service = _source("aos/services/verification/service.py")
        policy = _source("aos/services/verification/policy.py")
        validation = _source("aos/services/verification/validation.py")
        self.assertIn("FOR UPDATE", policy)
        self.assertIn("lock_request_for_user", service)
        self.assertIn("normalize_submit_payload", service)
        self.assertIn("Unsupported verification fields", validation)
        self.assertIn('"cmd"', _source("aos/services/verification/constants.py"))
        self.assertNotIn('payload.get("user")', service)
        self.assertNotIn('payload.get("status")', service)

    def test_idempotency_is_hashed_and_duplicate_pending_is_conflict_safe(self):
        service = _source("aos/services/verification/service.py")
        schema = json.loads(
            _source("aos/aos/doctype/aos_verification_request/aos_verification_request.json")
        )
        fields = {row["fieldname"]: row for row in schema["fields"]}
        self.assertIn("submission_idempotency_hash", fields)
        self.assertTrue(fields["submission_idempotency_hash"].get("hidden"))
        self.assertIn("hashlib.sha256", service)
        self.assertIn("VERIFICATION_IN_PROGRESS", service)
        self.assertIn("stored_hash", service)

    def test_schema_preserves_only_authoritative_types_and_states(self):
        schema = json.loads(
            _source("aos/aos/doctype/aos_verification_request/aos_verification_request.json")
        )
        fields = {row["fieldname"]: row for row in schema["fields"]}
        self.assertEqual(fields["verification_type"]["options"].splitlines(), ["Business", "Individual"])
        self.assertEqual(
            fields["status"]["options"].splitlines(),
            ["Pending", "Reviewing", "Approved", "Rejected", "Revoked"],
        )
        self.assertIn("business_website", fields)
        self.assertFalse(bool(schema.get("allow_rename")))
        self.assertFalse(bool(schema.get("index_web_pages_for_search")))
        permissions = schema.get("permissions", [])
        roles = {row.get("role") for row in permissions}
        self.assertEqual(roles, {"System Manager"})
        system_manager = next(row for row in permissions if row.get("role") == "System Manager")
        self.assertFalse(bool(system_manager.get("share")))
        self.assertFalse(bool(system_manager.get("delete")))

    def test_sensitive_child_schema_is_not_web_indexed_or_renamable(self):
        schema = json.loads(
            _source("aos/aos/doctype/aos_verification_document/aos_verification_document.json")
        )
        self.assertFalse(bool(schema.get("allow_rename")))
        self.assertFalse(bool(schema.get("index_web_pages_for_search")))
        fields = {row["fieldname"]: row for row in schema["fields"]}
        self.assertTrue(fields["attachment"].get("hidden"))
        self.assertTrue(fields["attachment"].get("read_only"))

    def test_document_media_is_private_bounded_and_owned(self):
        purposes = _source("aos/services/media/media_purposes.py")
        request = _source("aos/aos/doctype/aos_verification_request/aos_verification_request.py")
        service = _source("aos/services/verification/service.py")
        self.assertIn('"verification_document": MediaPurpose(', purposes)
        block = purposes.split('"verification_document": MediaPurpose(', 1)[1].split('"background_removal_source"', 1)[0]
        self.assertIn('bucket_type="private"', block)
        self.assertIn('visibility="Private"', block)
        self.assertIn("max_size_bytes=20 * 1024 * 1024", block)
        self.assertIn("max_items_per_resource=10", block)
        self.assertIn("media.owner_user != self.user", request)
        self.assertIn('purpose="verification_document"', service)

    def test_generic_media_access_is_hardened_for_verification_evidence(self):
        media = _source("aos/services/media/media_service.py")
        self.assertIn('getattr(doc, "purpose", None) == "verification_document"', media)
        self.assertIn("minutes = min(minutes, 10)", media)
        self.assertIn('doc.status == "Uploaded" and not doc.attached_name', media)
        self.assertIn("verification_evidence", media)
        self.assertIn("None if verification_evidence else doc.original_filename", media)
        self.assertIn("include_private_fields and not verification_evidence", media)

    def test_public_serializer_redacts_reviewer_and_document_number(self):
        serializer = _source("aos/services/verification/serializers.py")
        endpoint = _source("aos/api/verification/get_my_verification.py")
        service = _source("aos/services/verification/service.py")
        self.assertIn("mask_document_number", serializer)
        self.assertNotIn('"verified_by"', serializer)
        self.assertNotIn('"verified_by"', service)
        self.assertNotIn("serialize_media_doc", serializer)
        self.assertIn("set_private_no_store()", endpoint)

    def test_review_transitions_are_centralized_and_staff_enforced(self):
        lifecycle = _source("aos/services/verification/lifecycle.py")
        controller = _source("aos/aos/doctype/aos_verification_request/aos_verification_request.py")
        policy = _source("aos/services/verification/policy.py")
        self.assertIn("REVIEWER_TRANSITIONS", lifecycle)
        self.assertIn("is_reviewer(actor)", lifecycle)
        self.assertIn("has_doctype_permission", policy)
        self.assertIn('doctype="AOS Verification Request"', policy)
        self.assertIn('ptype="write"', policy)
        self.assertIn("validate_status_transition", controller)
        self.assertIn("protect_review_metadata", controller)
        self.assertIn("lock_eligible_profile(self.user)", controller)
        self.assertIn("lock_request_by_name(self.name)", controller)

    def test_submitted_identity_and_evidence_are_immutable_during_review(self):
        controller = _source("aos/aos/doctype/aos_verification_request/aos_verification_request.py")
        self.assertIn("_validate_submission_immutability", controller)
        self.assertIn("Verification request ownership cannot be changed", controller)
        self.assertIn("Submitted verification evidence cannot be edited during review", controller)
        self.assertIn('{"resubmit", "system"}', controller)

    def test_legacy_media_serializer_cannot_reintroduce_pii_or_raw_storage_metadata(self):
        source = _source("aos/api/verification/media.py")
        self.assertIn("serialize_document(row)", source)
        self.assertIn("MediaService().get_url", source)
        self.assertNotIn("serialize_media_doc", source)
        self.assertNotIn('"document_number": row.document_number', source)
        self.assertNotIn('data["media_object"]', source)

    def test_notifications_keep_canonical_categories_and_are_deduped(self):
        source = _source("aos/services/notification_service.py")
        approved = source.split("def notify_verification_approved", 1)[1].split(
            "def notify_verification_rejected", 1
        )[0]
        rejected = source.split("def notify_verification_rejected", 1)[1].split("# SHORTS", 1)[0]
        self.assertIn('type="verification_approved"', approved)
        self.assertIn('event="aos_verification_approved"', approved)
        self.assertIn("dedupe_key", approved)
        self.assertIn("decision_token", approved)
        self.assertIn('type="verification_rejected"', rejected)
        self.assertIn('event="aos_verification_rejected"', rejected)
        self.assertIn("dedupe_key", rejected)
        self.assertIn("decision_token", rejected)
        self.assertNotIn("rejection_reason", rejected)

    def test_permanent_deletion_releases_raw_evidence_without_storage_io(self):
        source = _source("aos/services/account_deletion_service.py")
        block = source.split("def _cleanup_verification_documents", 1)[1].split(
            "def _revoke_verification_requests", 1
        )[0]
        self.assertIn("release_media", block)
        self.assertIn("DELETE FROM `tabAOS Verification Document`", block)
        self.assertNotIn("delete_media_as_system", block)
        self.assertNotIn("storage.", block)
        self.assertIn("verification_document_rows_removed", block)

    def test_migration_is_registered_non_destructive_and_non_committing(self):
        patches = _source("aos/patches.txt")
        source = _source("aos/patches/v1_0/harden_verification_subsystem.py")
        self.assertIn("aos.patches.v1_0.harden_verification_subsystem", patches)
        self.assertIn("uq_aos_verification_user", source)
        self.assertIn("HAVING COUNT(*) > 1", source)
        self.assertIn("operator review", source)
        self.assertNotIn("delete_doc", source)
        self.assertNotIn("frappe.db.commit", source)

    def test_public_error_codes_have_stable_http_semantics(self):
        source = _source("aos/api/shared/responses.py")
        for code in (
            "VERIFICATION_INVALID_REQUEST",
            "VERIFICATION_UNKNOWN_FIELD",
            "VERIFICATION_INVALID_DOCUMENT",
            "VERIFICATION_ACCESS_DENIED",
            "VERIFICATION_NOT_FOUND",
            "VERIFICATION_IN_PROGRESS",
            "VERIFICATION_INVALID_STATE",
            "VERIFICATION_INTERNAL_ERROR",
        ):
            self.assertIn(f'"{code}"', source)

    def test_every_public_verification_endpoint_has_rate_limit_registry_coverage(self):
        registry = json.loads(_source("ci/public-endpoint-rate-limits.json"))
        endpoints = {row["endpoint"] for row in registry}
        self.assertIn("aos.api.v1.verification.__init__.submit_verification", endpoints)
        self.assertIn("aos.api.v1.verification.__init__.get_my_verification", endpoints)

    def test_verification_documentation_covers_supported_and_unsupported_workflows(self):
        readme = _source("docs/features/verification/README.md")
        security = _source("docs/features/verification/security.md")
        testing = _source("docs/features/verification/testing.md")
        self.assertIn("Individual", readme)
        self.assertIn("Business", readme)
        self.assertIn("Intentionally unsupported", readme)
        self.assertIn("no malware/antivirus", readme)
        self.assertIn("AOS Verification Request", security)
        self.assertIn("effective Read/Write permission", security)
        self.assertIn("bench --site <site> run-tests --app aos --module aos.api.verification", testing)

    def test_verification_observability_has_no_payload_pii_fields(self):
        source = _source("aos/services/verification/observability.py")
        for forbidden in (
            "document_number",
            "legal_name",
            "phone_number",
            "business_email",
            "business_address",
            "signed_url",
            "session_id",
        ):
            self.assertNotIn(f'"{forbidden}"', source)
        self.assertIn("public_account_id_for_user", source)


if __name__ == "__main__":
    unittest.main()

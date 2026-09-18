from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class TestVerificationProductionSourceGuards(unittest.TestCase):
    def test_public_surface_is_exact_and_http_methods_are_explicit(self):
        source = _source("aos/api/v1/verification/__init__.py")
        tree = ast.parse(source)
        functions: dict[str, tuple[str, ...]] = {}
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            for decorator in node.decorator_list:
                if not (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr == "whitelist"
                ):
                    continue
                methods: tuple[str, ...] = ()
                for kw in decorator.keywords:
                    if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                        methods = tuple(
                            item.value
                            for item in kw.value.elts
                            if isinstance(item, ast.Constant) and isinstance(item.value, str)
                        )
                functions[node.name] = methods
        self.assertEqual(
            functions,
            {"submit_verification": ("POST",), "get_my_verification": ("GET",)},
        )

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

    def test_submission_reuses_canonical_accounts_lock_and_has_no_sellers_dependency(self):
        policy = _source("aos/services/verification/policy.py")
        account_repo = _source("aos/services/accounts/repository.py")
        service = _source("aos/services/verification/service.py")
        validation = _source("aos/services/verification/validation.py")
        controller = _source("aos/aos/doctype/aos_verification_request/aos_verification_request.py")
        self.assertIn("AccountRepository.lock_profile", policy)
        self.assertIn("FOR UPDATE", account_repo)
        self.assertIn("lock_request_for_user", service)
        self.assertNotIn("services.sellers", policy + service + validation + controller)
        self.assertNotIn("AOS Seller", policy + service + validation + controller)

    def test_submission_contract_has_one_document_shape(self):
        constants = _source("aos/services/verification/constants.py")
        validation = _source("aos/services/verification/validation.py")
        for field in ("document_type", "document_number", "issue_date", "expiry_date", "media_id"):
            self.assertIn(f'"{field}",', constants)
        self.assertIn("Unsupported verification document fields", validation)
        self.assertIn("require_idempotency", validation)

    def test_idempotency_is_payload_aware_and_raw_key_is_not_persisted(self):
        service = _source("aos/services/verification/service.py")
        schema = json.loads(_source("aos/aos/doctype/aos_verification_request/aos_verification_request.json"))
        fields = {row["fieldname"]: row for row in schema["fields"]}
        self.assertIn("submission_idempotency_key_hash", fields)
        self.assertIn("submission_payload_hash", fields)
        self.assertNotIn("submission_idempotency_hash", fields)
        self.assertTrue(fields["submission_idempotency_key_hash"].get("hidden"))
        self.assertTrue(fields["submission_payload_hash"].get("hidden"))
        self.assertIn("VERIFICATION_IDEMPOTENCY_CONFLICT", service)
        self.assertIn("_payload_hash", service)
        self.assertIn("json.dumps", service)
        self.assertNotIn("verification.idempotency_key =", service)

    def test_schema_is_single_current_request_and_review_only_desk(self):
        schema = json.loads(_source("aos/aos/doctype/aos_verification_request/aos_verification_request.json"))
        fields = {row["fieldname"]: row for row in schema["fields"]}
        self.assertEqual(fields["verification_type"]["options"].splitlines(), ["Business", "Individual"])
        self.assertEqual(
            fields["status"]["options"].splitlines(),
            ["Pending", "Reviewing", "Approved", "Rejected", "Revoked"],
        )
        self.assertIn("submitted_on", fields)
        self.assertTrue(fields["verification_documents"].get("read_only"))
        self.assertTrue(fields["user"].get("read_only"))
        self.assertFalse(bool(schema.get("allow_rename")))
        self.assertFalse(bool(schema.get("index_web_pages_for_search")))
        system_manager = next(row for row in schema.get("permissions", []) if row.get("role") == "System Manager")
        self.assertFalse(bool(system_manager.get("create")))
        self.assertFalse(bool(system_manager.get("delete")))
        self.assertFalse(bool(system_manager.get("share")))
        self.assertTrue(bool(system_manager.get("write")))

    def test_sensitive_child_schema_uses_media_reference_only(self):
        schema = json.loads(_source("aos/aos/doctype/aos_verification_document/aos_verification_document.json"))
        fields = {row["fieldname"]: row for row in schema["fields"]}
        self.assertFalse(bool(schema.get("allow_rename")))
        self.assertFalse(bool(schema.get("index_web_pages_for_search")))
        self.assertNotIn("attachment", fields)
        self.assertEqual(set(fields), {"document_type", "document_number", "issue_date", "expiry_date", "media"})
        self.assertTrue(fields["media"].get("read_only"))

    def test_media_contract_is_private_bounded_and_centralized(self):
        purposes = _source("aos/services/media/media_purposes.py")
        evidence = _source("aos/services/verification/evidence.py")
        service = _source("aos/services/verification/service.py")
        block = purposes.split('"verification_document": MediaPurpose(', 1)[1].split(
            '"background_removal_source"', 1
        )[0]
        self.assertIn('bucket_type="private"', block)
        self.assertIn('visibility="Private"', block)
        self.assertIn("max_size_bytes=20 * 1024 * 1024", block)
        self.assertIn("max_items_per_resource=10", block)
        self.assertIn("validate_media_for_use", evidence)
        self.assertIn("VERIFICATION_INVALID_DOCUMENT", evidence)
        self.assertIn("attach_media", service)
        self.assertIn("release_media", service)
        self.assertNotIn("bucket", service)
        self.assertNotIn("object_key", service)

    def test_public_serializers_expose_only_one_verification_and_media_identity(self):
        serializer = _source("aos/services/verification/serializers.py")
        service = _source("aos/services/verification/service.py")
        self.assertIn('"verification_id"', serializer)
        self.assertNotIn('"name"', serializer)
        self.assertNotIn('"id"', serializer)
        self.assertIn('"media_id"', serializer)
        self.assertNotIn('"media":', serializer)
        self.assertNotIn('"verified_by"', serializer)
        self.assertNotIn('"verified_by"', service)
        self.assertIn("mask_document_number", serializer)

    def test_review_transitions_are_centralized_staff_enforced_and_revocation_is_semantic(self):
        constants = _source("aos/services/verification/constants.py")
        lifecycle = _source("aos/services/verification/lifecycle.py")
        controller = _source("aos/aos/doctype/aos_verification_request/aos_verification_request.py")
        policy = _source("aos/services/verification/policy.py")
        self.assertIn("REVIEWER_TRANSITIONS", lifecycle)
        self.assertIn("is_reviewer(actor)", lifecycle)
        self.assertIn("has_doctype_permission", policy)
        self.assertIn("VERIFICATION_DOCTYPE", policy)
        self.assertIn('ptype="write"', policy)
        self.assertIn("validate_status_transition", controller)
        self.assertIn("protect_review_metadata", controller)
        self.assertIn("lock_eligible_profile(self.user)", controller)
        self.assertIn("lock_request_by_name(self.name)", controller)
        self.assertIn("STATUS_APPROVED: frozenset({STATUS_REVOKED})", constants)
        self.assertNotIn("STATUS_PENDING: frozenset({STATUS_REVIEWING, STATUS_APPROVED, STATUS_REJECTED, STATUS_REVOKED})", constants)

    def test_submitted_identity_evidence_and_metadata_are_immutable_during_review(self):
        controller = _source("aos/aos/doctype/aos_verification_request/aos_verification_request.py")
        self.assertIn("_validate_submission_immutability", controller)
        self.assertIn("_validate_server_owned_metadata", controller)
        self.assertIn("Verification request ownership cannot be changed", controller)
        self.assertIn("Submitted verification evidence cannot be edited during review", controller)
        self.assertIn("must be created through the submission service", controller)

    def test_notifications_use_finalized_contract_and_delivery_is_not_owned_by_verification(self):
        notification = _source("aos/services/notifications/service.py")
        decision = _source("aos/services/verification/decision.py")
        approved = notification.split("def notify_verification_approved", 1)[1].split(
            "def notify_verification_rejected", 1
        )[0]
        rejected = notification.split("def notify_verification_rejected", 1)[1].split("# MEDIA", 1)[0]
        self.assertIn('type="verification_approved"', approved)
        self.assertIn('event="aos_verification_approved"', approved)
        self.assertIn("dedupe_key", approved)
        self.assertIn('type="verification_rejected"', rejected)
        self.assertIn('event="aos_verification_rejected"', rejected)
        self.assertIn("dedupe_key", rejected)
        self.assertIn("NotificationService.notify_verification_approved", decision)
        self.assertIn("NotificationService.notify_verification_rejected", decision)
        self.assertNotIn("sendmail", decision)
        self.assertNotIn("firebase", decision.lower())

    def test_owner_truth_is_derived_from_verification_not_account_projection(self):
        service = _source("aos/services/verification/service.py")
        block = service.split("def get_my", 1)[1].split("def _resolve_existing", 1)[0]
        self.assertIn("verification.status == STATUS_APPROVED", block)
        self.assertNotIn('"is_verified"', block.split("return {", 1)[0])

    def test_permanent_deletion_releases_evidence_without_storage_io(self):
        source = _source("aos/services/account_deletion_service.py")
        block = source.split("def _cleanup_verification_documents", 1)[1].split(
            "def _revoke_verification_requests", 1
        )[0]
        self.assertIn("release_media", block)
        self.assertIn("DELETE FROM `tabAOS Verification Document`", block)
        self.assertNotIn("delete_media_as_system", block)
        self.assertNotIn("storage.", block)

    def test_permanent_purge_anonymizes_current_verification_hashes(self):
        source = _source("aos/services/account_purge_service.py")
        block = source.split("def _anonymize_verification_requests", 1)[1].split(
            "def _release_profile_media", 1
        )[0]
        self.assertIn('"submission_idempotency_key_hash"', block)
        self.assertIn('"submission_payload_hash"', block)
        self.assertNotIn('"submission_idempotency_hash"', block)

    def test_current_schema_installer_is_reasserted_after_migrate(self):
        patches = _source("aos/patches.txt")
        installer = _source("aos/patches/v1_0/install_verification_indexes.py")
        migrate = _source("aos/migrate.py")
        self.assertIn("aos.patches.v1_0.install_verification_indexes", patches)
        self.assertIn("uq_aos_verification_user", installer)
        self.assertIn("uq_aos_verification_document_media", installer)
        self.assertIn("idx_aos_verification_review_queue", installer)
        self.assertNotIn("HAVING COUNT(*)", installer)
        self.assertNotIn("frappe.db.commit", installer)
        self.assertIn("install_verification_indexes.execute", migrate)

    def test_public_error_codes_have_stable_http_semantics(self):
        source = _source("aos/api/shared/responses.py")
        for code in (
            "VERIFICATION_INVALID_REQUEST",
            "VERIFICATION_UNKNOWN_FIELD",
            "VERIFICATION_INVALID_IDEMPOTENCY_KEY",
            "VERIFICATION_INVALID_DOCUMENT",
            "VERIFICATION_ACCESS_DENIED",
            "VERIFICATION_NOT_FOUND",
            "VERIFICATION_IDEMPOTENCY_CONFLICT",
            "VERIFICATION_IN_PROGRESS",
            "VERIFICATION_ALREADY_APPROVED",
            "VERIFICATION_INVALID_STATE",
            "VERIFICATION_INTERNAL_ERROR",
        ):
            self.assertIn(f'"{code}"', source)

    def test_every_public_endpoint_has_deliberate_rate_limit_policy(self):
        registry = json.loads(_source("ci/public-endpoint-rate-limits.json"))
        rows = {row["endpoint"]: row for row in registry}
        submit = rows["aos.api.v1.verification.__init__.submit_verification"]
        get_my = rows["aos.api.v1.verification.__init__.get_my_verification"]
        self.assertIn("5 submissions", submit["rationale"])
        self.assertIn("60 owner status reads", get_my["rationale"])
        constants = _source("aos/api/verification/constants.py")
        self.assertIn("SUBMIT_VERIFICATION_LIMIT = 5", constants)
        self.assertIn("SUBMIT_VERIFICATION_WINDOW_SECONDS = 3600", constants)

    def test_verification_documentation_is_canonical_and_complete(self):
        api = _source("docs/features/verification/README.md")
        for heading in (
            "## Feature overview",
            "## Production-ready dependencies",
            "## Data layer",
            "## State machine",
            "## Public API",
            "## Notifications",
            "## Security and privacy",
            "## Retention",
            "## Tests and validation",
        ):
            self.assertIn(heading, api)
        self.assertIn("does not depend on Sellers", api)
        self.assertIn("one `AOS Verification Request` per account", api)
        self.assertIn("## Endpoint inventory (code-derived)", api)
        docs = sorted(path.name for path in (ROOT / "docs/features/verification").glob("*.md"))
        self.assertEqual(docs, ["README.md"])

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

    def test_verification_files_are_bounded_and_responsibilities_split(self):
        bounded = [
            "aos/services/verification/service.py",
            "aos/services/verification/validation.py",
            "aos/aos/doctype/aos_verification_request/aos_verification_request.py",
        ]
        for relative in bounded:
            lines = _source(relative).splitlines()
            self.assertLessEqual(len(lines), 275, relative)
        self.assertTrue((ROOT / "aos/services/verification/evidence.py").exists())
        self.assertTrue((ROOT / "aos/services/verification/decision.py").exists())


if __name__ == "__main__":
    unittest.main()

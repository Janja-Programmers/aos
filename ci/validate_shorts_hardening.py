#!/usr/bin/env python3
"""Repository-only Shorts hardening checks; requires no Frappe site."""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ERRORS: list[str] = []


def require(condition: bool, message: str) -> None:
    if not condition:
        ERRORS.append(message)


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


required_docs = {
    "README.md", "architecture.md", "api.md", "upload-lifecycle.md", "processing.md",
    "feeds.md", "privacy.md", "media.md", "interactions.md", "notifications.md",
    "analytics.md", "moderation.md", "migration.md", "operations.md", "testing.md",
    "classification.md",
}
docs_dir = ROOT / "docs/features/shorts"
require(required_docs <= {p.name for p in docs_dir.glob("*.md")}, "required Shorts docs are incomplete")

shorts_tests = ROOT / "aos/api/shorts/tests"
required_shorts_tests = {
    "__init__.py", "test_api_contracts.py", "test_database_contracts.py",
    "test_classification.py", "test_audio_mix_lifecycle.py",
}
require(
    required_shorts_tests <= {p.name for p in shorts_tests.glob("*.py")},
    "feature-specific Shorts tests are not colocated under aos/api/shorts/tests",
)
root_shorts_tests = sorted((ROOT / "aos/tests").glob("test_shorts*.py"))
require(not root_shorts_tests, "feature-specific Shorts tests remain under aos/tests")

wrapper_tree = ast.parse(source("aos/api/v1/shorts/__init__.py"))
wrappers = {
    node.name for node in wrapper_tree.body
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name != "_call"
}
endpoint_source = source("aos/services/shorts/endpoints.py")
for name in wrappers:
    require(f'"{name}"' in endpoint_source, f"missing strict endpoint spec: {name}")

rate_policy = json.loads(source("ci/public-endpoint-rate-limits.json"))
rate_methods = {str(row.get("endpoint") or "") for row in rate_policy}
for name in wrappers:
    require(
        f"aos.api.v1.shorts.__init__.{name}" in rate_methods,
        f"missing rate policy: {name}",
    )

feed = source("aos/api/shorts/feed.py")
require("s.name IN %(candidate_ids)s" not in feed, "All feed still uses exhaustive ranking candidates")
require("if not candidate_ids" not in feed, "All feed still empties on missing ranking candidates")
require("COALESCE(s.ranking_score, 0) DESC" in feed, "feed and cursor ranking order are inconsistent")
require("EXISTS (" in feed, "Following feed is not duplicate-safe")


classification = source("aos/services/shorts/classification.py")
upload = source("aos/api/shorts/upload.py")
short_schema = json.loads(source("aos/aos/doctype/aos_short/aos_short.json"))
require("classify_for_publish" in upload, "automatic publish classification is not wired")
require("legacy_content_mode=kwargs.get(\"content_mode\")" in upload, "legacy content mode compatibility is not explicit")
require("del legacy_content_mode" in classification, "creator content mode is still authoritative")
require("commerce_context" in classification, "validated Shop context classification is missing")
mode_field = next((field for field in short_schema.get("fields", []) if field.get("fieldname") == "content_mode"), {})
require("all" not in str(mode_field.get("options") or "").split(), "All is incorrectly persisted as a content mode")
image_main = source("infra/image-search/app/main.py")
require("/internal/shorts/classify-frames" in image_main, "signed visual classification endpoint is missing")
require("verify_signature" in image_main, "visual classification endpoint is not authenticated")
require("x_aos_timestamp" in image_main, "visual classification request lacks freshness protection")
require("signed_payload" in image_main, "visual classification signature does not bind timestamp")

cursor = source("aos/api/shorts/utils.py")
for token in ("hmac.new", "compare_digest", "_CURSOR_TTL_SECONDS", "ShortsCursorError"):
    require(token in cursor, f"cursor hardening token missing: {token}")

processing = source("aos/services/video_processing_service.py")
for token in ("job_generation", "SUPERSEDED", "SHORT_DELETED", "_validated_processed_keys", "validate_object_key"):
    require(token in processing, f"processing hardening token missing: {token}")
require('payload.get("playback_url")' not in processing, "callback playback URL is still trusted")

worker = source("infra/video-processing/app/worker.py")
for token in (
    "max_input_bytes", "allowed_video_codecs", "max_pixels", "ffmpeg_threads",
    "_validate_callback_url", "_generate_classification_frames", "_classify_frames",
):
    require(token in worker, f"video worker limit missing: {token}")
require("shell=True" not in worker and "os.system(" not in worker, "unsafe video subprocess invocation")
require('"objects": uploaded_objects' not in worker, "video callback still exposes every HLS object")
require('"output_object_count": len(uploaded_objects)' in worker, "bounded video output summary is missing")
require("amix=inputs=2" in worker, "selected sound is not mixed with original audio")

sounds_source = source("aos/api/shorts/sounds.py")
audio_enqueue = sounds_source[
    sounds_source.index("def enqueue_short_audio_reprocess"):
    sounds_source.index("def validate_existing_short_sound_for_mode")
]
require("return create_video_processing_job" in audio_enqueue, "audio remix job is not returned to callers")
require("        raise" in audio_enqueue, "audio remix enqueue failures are still swallowed")
require('values["audio_mix_status"] = "pending"' not in audio_enqueue, "audio mix is marked pending before durable job creation")
require("recover_pending_audio_mixes" in source("aos/tasks/shorts.py"), "stale audio mix recovery task is missing")
require("aos.tasks.shorts.recover_pending_audio_mixes" in source("aos/hooks.py"), "audio mix recovery task is not scheduled")

compose = source("docker-compose.yml")
for worker_name, queue_token in (
    ("video-worker", "${VIDEO_QUEUE_NAME:-video}"),
    ("moderation-worker", "${MODERATION_QUEUE_NAME:-moderation}"),
    ("search-ranking-worker", "${SEARCH_RANKING_QUEUE_NAME:-search-ranking}"),
    ("notification-worker", "${NOTIFICATION_QUEUE_NAME:-notification-delivery}"),
    ("analytics-worker", "${ANALYTICS_QUEUE_NAME:-analytics-pipeline}"),
):
    match = re.search(
        rf"(?ms)^  {re.escape(worker_name)}:\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)",
        compose,
    )
    require(match is not None, f"missing companion worker: {worker_name}")
    block = match.group("body") if match else ""
    require(queue_token in block, f"{worker_name} queue command is missing")
    require(
        "--with-scheduler" in block,
        f"{worker_name} must enable the RQ scheduler for interval retries",
    )

durable_video = source("infra/video-processing/app/durable_lifecycle.py")
require('65536 if name == "result_payload"' in durable_video, "video callback JSON is still truncated at 4 KiB")
require("CALLBACK_RESULT_INVALID" in durable_video, "corrupt durable callback records do not fail closed")
require('result_payload["job_id"]' in durable_video, "durable callback delivery does not restore stable job ID")

transaction_roots = [
    ROOT / "aos/api/shorts",
    ROOT / "aos/services/shorts",
    ROOT / "aos/services/video_processing_service.py",
    ROOT / "aos/patches/v1_0/harden_shorts_subsystem.py",
    ROOT / "aos/patches/v1_0/install_shorts_indexes.py",
    ROOT / "aos/patches/v1_0/initialize_short_classification_metadata.py",
]
for root in transaction_roots:
    files = [root] if root.is_file() else list(root.rglob("*.py"))
    for file in files:
        tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr == "commit":
                ERRORS.append(f"transaction commit found: {file.relative_to(ROOT)}:{node.lineno}")
            if node.func.attr == "rollback" and not any(k.arg == "save_point" for k in node.keywords):
                ERRORS.append(f"full rollback found: {file.relative_to(ROOT)}:{node.lineno}")

patches = source("aos/patches.txt")
data_patch = "aos.patches.v1_0.harden_shorts_subsystem"
index_patch = "aos.patches.v1_0.install_shorts_indexes"
classification_patch = "aos.patches.v1_0.initialize_short_classification_metadata"
require(data_patch in patches, "Shorts data migration not registered")
require(index_patch in patches, "Shorts index migration not registered")
require(classification_patch in patches, "Shorts classification migration not registered")
if data_patch in patches and index_patch in patches:
    require(patches.index(data_patch) < patches.index(index_patch), "Shorts index patch must follow data reconciliation")
if index_patch in patches and classification_patch in patches:
    require(
        patches.index(index_patch) < patches.index(classification_patch),
        "Shorts classification metadata patch must follow schema/index migration",
    )
data_patch_source = source("aos/patches/v1_0/harden_shorts_subsystem.py")
index_patch_source = source("aos/patches/v1_0/install_shorts_indexes.py")
classification_patch_source = source("aos/patches/v1_0/initialize_short_classification_metadata.py")
require("frappe.db.commit" not in data_patch_source, "Shorts data migration commits")
require("frappe.db.commit" not in index_patch_source, "Shorts index migration commits")
require("frappe.db.commit" not in classification_patch_source, "Shorts classification migration commits")
require("ALTER TABLE" not in classification_patch_source, "Shorts classification migration mixes DML and DDL")
require("ALTER TABLE" not in data_patch_source, "Shorts data migration mixes DML and DDL")
require("ALTER TABLE" in index_patch_source, "Shorts schema-only index migration is missing DDL")
index_patch_tree = ast.parse(index_patch_source)
for node in ast.walk(index_patch_tree):
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        continue
    if node.func.attr != "sql" or not node.args:
        continue
    query_node = node.args[0]
    literal = ""
    if isinstance(query_node, ast.Constant) and isinstance(query_node.value, str):
        literal = query_node.value
    elif isinstance(query_node, ast.JoinedStr):
        literal = "".join(
            value.value for value in query_node.values
            if isinstance(value, ast.Constant) and isinstance(value.value, str)
        )
    normalized = literal.lstrip().upper()
    require(
        not normalized.startswith(("INSERT", "UPDATE", "DELETE", "REPLACE", "TRUNCATE")),
        f"Shorts index migration contains DML at line {node.lineno}",
    )

library = source("aos/api/shorts/library.py")
response_tail = library[library.find('"Download URL generated."'):]
require('"download_file_key":' not in response_tail, "download response leaks object key")

if ERRORS:
    print("Shorts hardening validation failed:")
    for error in ERRORS:
        print(f"- {error}")
    sys.exit(1)
print(f"Shorts hardening validation passed ({len(wrappers)} public endpoints).")

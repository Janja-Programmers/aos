#!/usr/bin/env python3
"""Repository-only invariants for the current Shorts + Video Processing architecture."""
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


def text(path: str) -> str:
    p = ROOT / path
    require(p.exists(), f"missing required file: {path}")
    return p.read_text(encoding="utf-8") if p.exists() else ""


def json_doc(path: str) -> dict:
    try:
        return json.loads(text(path))
    except Exception as exc:
        ERRORS.append(f"invalid JSON {path}: {exc}")
        return {}


# One implementation surface; overlay deployments may leave empty directories,
# but no executable legacy Python module may survive.
def _has_python(path: Path) -> bool:
    return path.exists() and any(path.rglob("*.py"))

require(not _has_python(ROOT / "aos/api/shorts"), "obsolete aos.api.shorts stack still contains Python code")
require(not _has_python(ROOT / "aos/api/video_processing"), "obsolete public video processing API stack still contains Python code")
require(not _has_python(ROOT / "aos/api/v1/video_processing"), "obsolete v1 video processing client namespace still contains Python code")

v1 = text("aos/api/v1/shorts/__init__.py")
required_actions = {
    "like_short", "unlike_short", "save_short", "unsave_short", "repost_short", "undo_repost_short",
    "not_interested", "record_events", "download_short", "create_side_by_side_draft", "create_segment_reuse_draft",
}
for action in required_actions:
    require(re.search(rf"def\s+{re.escape(action)}\s*\(", v1) is not None, f"missing canonical endpoint: {action}")
for obsolete in ("toggle_like", "toggle_save_short", "toggle_repost", "feed_by_ad", "content_mode"):
    require(obsolete not in v1, f"obsolete client contract remains in v1 Shorts API: {obsolete}")

specs = text("aos/services/shorts/endpoints.py")
require("content_mode" not in specs, "client can still author content_mode")
require("owner" not in specs, "client can mass-assign Short owner")

short_schema = json_doc("aos/aos/doctype/aos_short/aos_short.json")
fields = {f.get("fieldname"): f for f in short_schema.get("fields", [])}
for field in (
    "content_type", "lifecycle_status", "processing_status", "moderation_status", "revision",
    "processing_generation", "moderation_generation", "raw_video_media", "playback_media",
    "playback_manifest_media", "poster_media", "storyboard_media", "storyboard_manifest_media",
):
    require(field in fields, f"AOS Short missing hardened field: {field}")
for obsolete in ("visibility_status", "approval_status", "content_mode", "audio_mix_status", "file_key", "playback_url", "thumbnail_url"):
    require(obsolete not in fields, f"AOS Short still contains obsolete field: {obsolete}")
require("naming_series" not in fields, "AOS Short exposes naming series")

sound_schema = json_doc("aos/aos/doctype/aos_sound/aos_sound.json")
sound_fields = {f.get("fieldname") for f in sound_schema.get("fields", [])}
require("is_commercial" not in sound_fields and "is_commercial_safe" not in sound_fields, "commercial Sound product flag remains")
require("naming_series" not in sound_fields, "Sound exposes naming series")

job_schema = json_doc("aos/aos/doctype/aos_video_processing_job/aos_video_processing_job.json")
require(job_schema.get("autoname") == "hash", "Video Processing jobs must use hash autoname")
job_fields = {f.get("fieldname") for f in job_schema.get("fields", [])}
for field in ("operation", "status", "generation", "idempotency_key", "active_key", "next_retry_at", "lease_owner", "lease_expires_at"):
    require(field in job_fields, f"Video Processing job missing field: {field}")

for doctype in ("aos_short_photo", "aos_short_mode", "aos_short_hashtag", "aos_short_ad", "aos_short_mention", "aos_short_feedback", "aos_short_moderation_decision"):
    json_path = ROOT / f"aos/aos/doctype/{doctype}/{doctype}.json"
    controller_path = ROOT / f"aos/aos/doctype/{doctype}/{doctype}.py"
    require(json_path.exists(), f"missing Shorts relation DocType: {doctype}")
    require(controller_path.exists(), f"missing Shorts relation controller: {doctype}")
    if json_path.exists() and controller_path.exists():
        doctype_name = json.loads(json_path.read_text(encoding="utf-8")).get("name", "")
        expected_class = doctype_name.replace(" ", "").replace("-", "")
        try:
            controller_tree = ast.parse(controller_path.read_text(encoding="utf-8"), filename=str(controller_path))
            controller_classes = {node.name for node in controller_tree.body if isinstance(node, ast.ClassDef)}
            require(expected_class in controller_classes, f"controller class mismatch for {doctype}: expected {expected_class}")
        except SyntaxError as exc:
            ERRORS.append(f"invalid controller Python {controller_path.relative_to(ROOT)}: {exc}")

media_purposes = text("aos/services/media/media_purposes.py")
for purpose in ("short_video_raw", "short_photo", "short_video_playback", "short_video_manifest", "short_poster", "short_storyboard", "short_storyboard_manifest", "short_download", "short_original_audio"):
    require(f'"{purpose}"' in media_purposes, f"Media purpose missing: {purpose}")

callback = text("aos/api/internal/video_processing/__init__.py")
require("handle_callback" in callback and "allow_guest=True" in callback, "signed internal Video Processing callback missing")
prod_config = text("aos/utils/production_config.py")
require("aos.api.internal.video_processing.handle_callback" in prod_config, "production callback URL is not internal")
require("aos.api.v1.video_processing.handle_callback" not in prod_config, "public Video Processing callback remains configured")

processing = text("aos/services/video_processing_service.py")
for token in ("ensure_outbox_for_job", "next_retry_at", "lease_expires_at", "job_generation", "outputs"):
    require(token in processing, f"processing invariant missing: {token}")
require("frappe.db.commit" not in processing, "Video Processing request/service flow manually commits")

hot_metrics = text("aos/services/shorts/hot_metrics.py")
for unsafe_call in ("cache.hsetnx(", "cache.hincrby(", "cache.hgetall("):
    require(unsafe_call not in hot_metrics, f"Shorts hot metrics uses unprefixed/serialized Redis hash call: {unsafe_call}")
for required_call in (
    'cache.execute_command("HSETNX"',
    'cache.execute_command("HINCRBY"',
    'cache.execute_command("HGETALL"',
    '"SSCAN",',
    '_redis_key(cache, _DIRTY_KEY)',
):
    require(required_call in hot_metrics, f"Shorts hot metrics missing site-prefixed raw Redis invariant: {required_call}")

worker = text("infra/video-processing/app/worker.py")
for token in ("_generate_hls", "_generate_storyboard", "_generate_watermarked_download", "_compose_side_by_side", "_compose_segment"):
    require(token in worker, f"video companion capability missing: {token}")
require("shell=True" not in worker and "os.system(" not in worker, "unsafe shell execution in video worker")

# No manual request-flow commits in active Shorts/Video Processing production Python.
transaction_roots = [
    ROOT / "aos/services/shorts",
    ROOT / "aos/services/video_processing_service.py",
    ROOT / "aos/services/video_processing_callback.py",
    ROOT / "aos/api/v1/shorts",
    ROOT / "aos/api/internal/shorts",
    ROOT / "aos/aos/doctype/aos_short",
]
for root in transaction_roots:
    files = [root] if root.is_file() else list(root.rglob("*.py"))
    for file in files:
        source = file.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "commit":
                    ERRORS.append(f"manual commit found: {file.relative_to(ROOT)}:{node.lineno}")
                if node.func.attr == "rollback" and not any(k.arg == "save_point" for k in node.keywords):
                    ERRORS.append(f"full rollback found: {file.relative_to(ROOT)}:{node.lineno}")

indexes = text("aos/patches/v1_0/install_shorts_indexes.py")
for idx in ("uq_short_like", "uq_short_save", "uq_short_repost", "uq_short_mode", "uq_short_ad", "uq_short_processing_active"):
    require(idx in indexes, f"fresh-site Shorts index invariant missing: {idx}")
require("frappe.db.commit" not in indexes, "Shorts index installer manually commits")
view_schema = json_doc("aos/aos/doctype/aos_short_view/aos_short_view.json")
require(any(f.get("fieldname") == "identity_key" and f.get("unique") for f in view_schema.get("fields", [])), "Short view identity_key must be unique")

hooks = text("aos/hooks.py")
require("aos.tasks.shorts.recover_video_processing" in hooks or "aos.tasks.shorts.recover_video_processing_jobs" in hooks, "Video Processing recovery task is not scheduled")

docs = sorted(p.name for p in (ROOT / "docs/features/shorts").glob("*.md"))
require(docs == ["README.md"], f"Shorts documentation must be one canonical README; found {docs}")
readme = text("docs/features/shorts/README.md")
for heading in ("## Overview", "## Responsibilities", "## Boundaries", "## Architecture", "## Data Model", "## Fields", "## API", "## Cross-feature Dependencies", "## Transaction / Concurrency Model", "## Caching", "## Performance / Scalability", "## Testing"):
    require(heading in readme, f"Shorts README missing section: {heading}")

# Global obsolete contract scan (production/docs/infra/CI; tests may use strings only to assert absence).
scan_roots = [ROOT / "aos", ROOT / "infra", ROOT / "docs"]
legacy = ("visibility_status", "approval_status", "audio_mix_status", "is_commercial_safe", "toggle_save_short", "toggle_repost", "feed_by_ad", "aos.api.v1.video_processing.handle_callback")
for scan_root in scan_roots:
    for file in scan_root.rglob("*"):
        if not file.is_file() or file.suffix not in {".py", ".md", ".js", ".json", ".yml", ".yaml"} or "__pycache__" in file.parts:
            continue
        if "tests" in file.parts or file.name.startswith("test_"):
            continue
        source = file.read_text(encoding="utf-8", errors="ignore")
        for token in legacy:
            if token in source and file != ROOT / "ci/validate_shorts_hardening.py":
                ERRORS.append(f"obsolete Shorts token {token!r} in {file.relative_to(ROOT)}")

if ERRORS:
    print("Shorts hardening validation failed:", file=sys.stderr)
    for error in sorted(set(ERRORS)):
        print(f"- {error}", file=sys.stderr)
    raise SystemExit(1)
print("Shorts + Video Processing hardening invariants: OK")

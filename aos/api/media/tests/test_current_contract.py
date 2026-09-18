from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


class MediaCurrentContractTests(unittest.TestCase):
    def source(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_media_api_rejects_fields_outside_the_canonical_contract(self):
        upload = self.source("aos/api/media/upload.py")
        urls = self.source("aos/api/media/urls.py")
        delete = self.source("aos/api/media/delete.py")
        background = self.source("aos/api/media/background.py")
        validators = self.source("aos/api/media/validators.py")
        self.assertIn("def reject_unknown_fields", validators)
        for source in (upload, urls, delete, background):
            self.assertIn("reject_unknown_fields(kwargs", source)
        for canonical in (
            "checksum_sha256",
            "upload_mode",
            "expiry_minutes",
            "result_purpose",
            "job_id",
        ):
            self.assertIn(canonical, "\n".join((upload, urls, background)))

    def test_media_schema_does_not_persist_delivery_url(self):
        schema = json.loads(self.source("aos/aos/doctype/aos_media_object/aos_media_object.json"))
        fields = {row.get("fieldname") for row in schema.get("fields", [])}
        self.assertIn("object_key", fields)
        self.assertFalse(any(str(field or "").endswith("_url") for field in fields))

    def test_media_doctype_has_no_unused_desk_script(self):
        self.assertFalse((ROOT / "aos/aos/doctype/aos_media_object/aos_media_object.js").exists())

    def test_media_api_has_no_unused_serializer_wrapper(self):
        self.assertFalse((ROOT / "aos/api/media/serializers.py").exists())

    def test_feature_media_boundaries_do_not_keep_unused_delegate_modules(self):
        self.assertFalse((ROOT / "aos/api/sellers/media.py").exists())
        self.assertFalse((ROOT / "aos/api/reviews/media.py").exists())
        ads = self.source("aos/api/ads/media.py")
        live = self.source("aos/api/live/media.py")
        self.assertNotIn("def serialize_ad_media", ads)
        self.assertNotIn("def clear_live_cover_media", live)

    def test_multipart_index_is_part_of_the_canonical_media_patch(self):
        patch = self.source("aos/patches/v1_0/install_media_indexes.py")
        registry = self.source("aos/patches.txt")
        self.assertIn("idx_aos_media_multipart_active", patch)
        media_patch_entries = [line.strip() for line in registry.splitlines() if "media" in line.lower()]
        self.assertEqual(media_patch_entries, ["aos.patches.v1_0.install_media_indexes"])

    def test_background_removal_exposes_only_current_runtime_settings(self):
        config = self.source("infra/background-removal/app/config.py")
        env_example = self.source(".env.example")
        compose = self.source("docker-compose.yml")
        current = {
            "BACKGROUND_REMOVAL_SERVICE_SECRET",
            "BACKGROUND_REMOVAL_MAX_IMAGE_BYTES",
            "BACKGROUND_REMOVAL_MAX_IMAGE_PIXELS",
            "BACKGROUND_REMOVAL_MAX_CONCURRENT_INFERENCES",
            "BACKGROUND_REMOVAL_INFERENCE_ACQUIRE_TIMEOUT_SECONDS",
        }
        for name in current:
            self.assertIn(name, config)
            self.assertIn(name, env_example)
            self.assertIn(name, compose)
        self.assertNotIn("BACKGROUND_REMOVAL_MODEL_NAME", compose)
        self.assertNotIn("background_removal_models", compose)
        background = compose.split("  background-removal:", 1)[1].split("  video-api:", 1)[0]
        self.assertIn("read_only: true", background)
        self.assertIn("/var/cache/aos/numba", background)
        self.assertIn("cap_drop:", background)
        self.assertIn("- ALL", background)
        dockerfile = self.source("infra/background-removal/Dockerfile")
        self.assertIn("REMBG_HOME=/opt/aos/rembg", dockerfile)
        self.assertNotIn("U2NET" + "_HOME", dockerfile)


    def test_ai_companions_are_ready_before_docker_marks_them_healthy(self):
        compose = self.source("docker-compose.yml")
        image_search = compose.split("  image-search:", 1)[1].split("  background-removal:", 1)[0]
        background = compose.split("  background-removal:", 1)[1].split("  video-api:", 1)[0]
        self.assertIn('http://127.0.0.1:8000/ready', image_search)
        self.assertIn('http://127.0.0.1:8000/ready', background)
        self.assertIn('image_search_models:/models/openclip', image_search)
        self.assertIn('AOS_MEDIA_PUBLIC_BASE_URL:', image_search)
        self.assertIn('HF_HOME: /models/openclip/huggingface', image_search)
        self.assertIn('TORCH_HOME: /models/openclip/torch', image_search)

    def test_initialized_cleanup_is_expiry_driven_only(self):
        service = self.source("aos/services/media/media_service.py")
        tasks = self.source("aos/tasks/media.py")
        self.assertIn("cleanup_expired_upload_sessions", service)
        self.assertIn("cleanup_expired_upload_sessions", tasks)
        schema = json.loads(self.source("aos/aos/doctype/aos_media_object/aos_media_object.json"))
        fields = {row.get("fieldname") for row in schema.get("fields", [])}
        self.assertIn("upload_expires_at", fields)

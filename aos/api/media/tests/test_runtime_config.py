from __future__ import annotations

import os
from unittest import TestCase
from unittest.mock import patch

from aos.utils import aos_config


class TestMediaRuntimeConfig(TestCase):
    def test_download_expiry_does_not_load_minio_credentials(self):
        with (
            patch.dict(
                os.environ,
                {"AOS_MEDIA_DOWNLOAD_EXPIRY_MINUTES": "17"},
                clear=True,
            ),
            patch.object(
                aos_config,
                "get_required_env",
                side_effect=AssertionError("credentials must not be read"),
            ) as required_env,
        ):
            aos_config._dotenv_values.cache_clear()
            try:
                self.assertEqual(aos_config.get_media_download_expiry_minutes(), 17)
            finally:
                aos_config._dotenv_values.cache_clear()

        required_env.assert_not_called()

    def test_object_storage_config_is_provider_neutral_and_redacts_secrets(self):
        env = {
            "AOS_OBJECT_STORAGE_ENDPOINT": "objects.example.test:443",
            "AOS_OBJECT_STORAGE_ACCESS_KEY": "media-access-key",
            "AOS_OBJECT_STORAGE_SECRET_KEY": "media-secret-key",
            "AOS_OBJECT_STORAGE_SECURE": "true",
            "AOS_OBJECT_STORAGE_PRESIGN_ENDPOINT": "https://uploads.example.test",
            "AOS_MEDIA_PUBLIC_BASE_URL": "https://media.example.test",
            "AOS_OBJECT_STORAGE_PUBLIC_BUCKET": "media-public",
            "AOS_OBJECT_STORAGE_PRIVATE_BUCKET": "media-private",
            "AOS_OBJECT_STORAGE_REGION": "fsn1",
            "AOS_OBJECT_STORAGE_PATH_STYLE": "false",
            "AOS_OBJECT_STORAGE_MANAGE_BUCKETS": "false",
        }
        with patch.dict(os.environ, env, clear=True):
            aos_config._dotenv_values.cache_clear()
            try:
                config = aos_config.get_object_storage_config()
            finally:
                aos_config._dotenv_values.cache_clear()

        self.assertEqual(config.endpoint, "objects.example.test:443")
        self.assertEqual(config.presign_endpoint, "https://uploads.example.test")
        self.assertEqual(config.public_base_url, "https://media.example.test")
        self.assertEqual(config.public_bucket, "media-public")
        self.assertEqual(config.private_bucket, "media-private")
        self.assertEqual(config.region, "fsn1")
        self.assertFalse(config.path_style)
        self.assertFalse(config.manage_buckets)
        rendered = repr(config)
        self.assertNotIn("media-access-key", rendered)
        self.assertNotIn("media-secret-key", rendered)

    def test_public_and_private_buckets_must_be_distinct(self):
        env = {
            "AOS_OBJECT_STORAGE_ACCESS_KEY": "access",
            "AOS_OBJECT_STORAGE_SECRET_KEY": "secret",
            "AOS_OBJECT_STORAGE_PUBLIC_BUCKET": "same-bucket",
            "AOS_OBJECT_STORAGE_PRIVATE_BUCKET": "same-bucket",
        }
        with patch.dict(os.environ, env, clear=True):
            aos_config._dotenv_values.cache_clear()
            try:
                with self.assertRaisesRegex(RuntimeError, "must be different"):
                    aos_config.get_object_storage_config()
            finally:
                aos_config._dotenv_values.cache_clear()

    def test_secure_storage_defaults_presign_origin_to_storage_endpoint(self):
        env = {
            "AOS_OBJECT_STORAGE_ENDPOINT": "objects.example.test",
            "AOS_OBJECT_STORAGE_ACCESS_KEY": "access",
            "AOS_OBJECT_STORAGE_SECRET_KEY": "secret",
            "AOS_OBJECT_STORAGE_SECURE": "true",
        }
        with patch.dict(os.environ, env, clear=True):
            aos_config._dotenv_values.cache_clear()
            try:
                config = aos_config.get_object_storage_config()
            finally:
                aos_config._dotenv_values.cache_clear()
        self.assertEqual(config.presign_endpoint, "https://objects.example.test")

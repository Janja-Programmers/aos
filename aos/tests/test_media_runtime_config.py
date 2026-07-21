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

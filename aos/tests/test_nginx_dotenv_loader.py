from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path


class TestNginxDotenvLoader(unittest.TestCase):
    """Deployment scripts must parse .env as data, never as shell code."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.repository_root = Path(__file__).resolve().parents[2]
        cls.loader = cls.repository_root / "infra" / "scripts" / "load-dotenv.sh"

    def test_loader_preserves_spaces_and_does_not_execute_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temp_root = Path(temporary_directory)
            marker = temp_root / "must-not-exist"
            env_file = temp_root / "deployment.env"
            env_file.write_text(
                "\n".join(
                    [
                        "SIMPLE=value",
                        "SPACED=kill myself,child abuse",
                        'QUOTED="hello world"',
                        f"DANGEROUS=$(touch {marker})",
                        "export EXPORTED=hello world",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            command = """
set -Eeuo pipefail
source "$1"
load_dotenv_file "$2"
printf '%s\n' "$SIMPLE|$SPACED|$QUOTED|$DANGEROUS|$EXPORTED"
"""
            result = subprocess.run(
                ["bash", "-c", command, "dotenv-test", str(self.loader), str(env_file)],
                cwd=self.repository_root,
                check=True,
                capture_output=True,
                text=True,
            )

            self.assertEqual(
                result.stdout.strip(),
                f"value|kill myself,child abuse|hello world|$(touch {marker})|hello world",
            )
            self.assertFalse(marker.exists())

    def test_nginx_scripts_use_safe_loader_instead_of_sourcing_env(self) -> None:
        for relative_path in (
            "infra/nginx/install.sh",
            "infra/nginx/issue-certificates.sh",
        ):
            text = (self.repository_root / relative_path).read_text(encoding="utf-8")
            self.assertIn('load_dotenv_file "${ENV_FILE}"', text)
            self.assertNotIn('source "${ENV_FILE}"', text)

    def test_example_uses_internal_moderation_callback_and_versioned_policy(self) -> None:
        text = (self.repository_root / ".env.example").read_text(encoding="utf-8")
        self.assertIn("aos.api.internal.moderation.handle_callback", text)
        self.assertIn("MODERATION_POLICY_VERSION=aos-safety-2026-09-26-v6", text)
        self.assertIn("TEXT_SAFETY_MODEL_REVISION=acf08db83390e23428c560cb578a865b39196993", text)
        self.assertIn("MODERATION_SEMANTIC_TEXT_REQUIRED=true", text)
        self.assertNotIn("MODERATION_REJECT_TERMS", text)



if __name__ == "__main__":
    unittest.main()

import os
import subprocess
import sys

from django.test import SimpleTestCase


class ProductionSettingsTests(SimpleTestCase):
    def test_production_settings_require_non_default_secret_key(self):
        env = os.environ.copy()
        env["DJANGO_ENV"] = "production"
        env.pop("DJANGO_SECRET_KEY", None)

        result = subprocess.run(
            [sys.executable, "-c", "import config.settings"],
            cwd=os.getcwd(),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DJANGO_SECRET_KEY must be set", result.stderr)


"""Disposable validation only; does not load dotenv or the project database."""
import os
import tempfile
os.environ["DJANGO_LOG_DIR"] = os.path.join(tempfile.gettempdir(), "tmp-duplicate-contract-logs")
from config.settings.base import *  # noqa: F403,F401,E402
SECRET_KEY = "disposable-duplicate-contract-tests-only"
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
if os.environ.get("DUPLICATE_DISPOSABLE_TEST_DB"):
    DATABASES["default"]["TEST"] = {"NAME": os.environ["DUPLICATE_DISPOSABLE_TEST_DB"]}
LOGGING = {"version": 1, "disable_existing_loggers": False, "handlers": {"console": {"class": "logging.StreamHandler"}}, "root": {"handlers": ["console"], "level": "ERROR"}}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
ALLOWED_HOSTS = ["testserver", "localhost"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

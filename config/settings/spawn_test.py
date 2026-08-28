"""Test settings for cross-process database integration tests.

Django's default SQLite test database is an in-memory URI that cannot be
opened by a fresh multiprocessing ``spawn`` interpreter.  This settings module
keeps normal settings unchanged while giving explicitly selected spawn tests a
real temporary database file.
"""

import os
import tempfile

from .base import *  # noqa: F403


if DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3":  # noqa: F405
    DATABASES["default"].setdefault("TEST", {})["NAME"] = os.path.join(  # noqa: F405
        tempfile.gettempdir(),
        f"teachermateplus_spawn_test_{os.getpid()}.sqlite3",
    )

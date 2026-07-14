from .base import *  # noqa: F401,F403

#DEBUG = True
#ALLOWED_HOSTS = ["127.0.0.1", "localhost"]

#SESSION_COOKIE_SECURE = False
#CSRF_COOKIE_SECURE = False
import os

ALLOWED_HOSTS = [
    h.strip()
    for h in os.getenv(
        "DJANGO_ALLOWED_HOSTS",
        "127.0.0.1,localhost",
    ).split(",")
    if h.strip()
]

CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CSRF_TRUSTED_ORIGINS",
        "",
    ).split(",")
    if origin.strip()
]
import os

django_env = os.getenv("DJANGO_ENV", "local").lower()

if django_env == "production":
    from .production import *  # noqa: F401,F403
else:
    from .local import *  # noqa: F401,F403


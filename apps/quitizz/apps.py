from django.apps import AppConfig


class QuiTizzConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.quitizz"
    verbose_name = "QuiTizz"

    def ready(self):
        from . import checks, signals  # noqa: F401

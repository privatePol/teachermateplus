from django.conf import settings
from django.core.checks import Warning, register


@register()
def realtime_configuration(app_configs, **kwargs):
    issues = []
    if settings.DJANGO_ENV in {"production", "staging"} or settings.QUITIZZ_DEPLOYMENT in {"production", "staging"}:
        if (settings.CACHES["quitizz"]["BACKEND"] != "django.core.cache.backends.redis.RedisCache"
                or settings.CHANNEL_LAYERS["default"]["BACKEND"] != "channels_redis.core.RedisChannelLayer"):
            issues.append(Warning("QuiTizz requires shared Redis transport and throttling before rollout.", id="quitizz.W001"))
        if settings.QUITIZZ_REDIS_NAMESPACE in {"", "local", "default"}:
            issues.append(Warning("Set a distinct QuiTizz Redis namespace for each deployment.", id="quitizz.W002"))
    return issues

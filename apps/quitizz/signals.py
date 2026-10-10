from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.core.services.features import FeatureSettingsService
from apps.tenants.models import SystemSetting
from .realtime import send, tenant_group


@receiver(post_save, sender=SystemSetting)
@receiver(post_delete, sender=SystemSetting)
def setting_changed(sender, instance, **kwargs):
    if instance.setting_key != FeatureSettingsService.QUITIZZ_ENABLED_KEY:
        return
    # Any toggle invalidates sockets. Global changes affect every connection;
    # reconnect rechecks tenant override. No history is altered.
    target = tenant_group(instance.tenant_id) if instance.tenant_id else "qt.global"
    transaction.on_commit(lambda: send(target, {"type": "quitizz.revoke", "event": "feature_unavailable"}))

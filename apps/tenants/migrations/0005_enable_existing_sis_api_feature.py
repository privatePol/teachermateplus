from django.db import migrations


def enable_existing_sis_api_feature(apps, schema_editor):
    Tenant = apps.get_model("tenants", "Tenant")
    SystemSetting = apps.get_model("tenants", "SystemSetting")
    for tenant in Tenant.objects.all():
        SystemSetting.objects.update_or_create(
            tenant_id=tenant.id,
            setting_key="FEATURE_SIS_PERIODIC_GRADES_API_ENABLED",
            defaults={
                "setting_value": "true",
                "value_type": "BOOL",
                "is_active": True,
            },
        )


def remove_existing_sis_api_feature(apps, schema_editor):
    SystemSetting = apps.get_model("tenants", "SystemSetting")
    SystemSetting.objects.filter(setting_key="FEATURE_SIS_PERIODIC_GRADES_API_ENABLED").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("tenants", "0004_tenantapikey"),
    ]

    operations = [
        migrations.RunPython(enable_existing_sis_api_feature, remove_existing_sis_api_feature),
    ]

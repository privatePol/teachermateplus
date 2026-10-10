from django.db import migrations


def seed(apps, schema_editor):
    alias = schema_editor.connection.alias
    Group = apps.get_model("navigation", "MenuGroup")
    Item = apps.get_model("navigation", "MenuItem")
    Link = apps.get_model("navigation", "MenuItemPermission")
    Permission = apps.get_model("rbac", "Permission")
    group, _ = Group.objects.using(alias).update_or_create(portal="FACULTY", code="QUITIZZ", defaults={"label": "QuiTizz", "sort_order": 75, "is_active": True})
    for code, label, route, actions, order in (
        ("QUITIZZ_LIST", "My QuiTizzes", "quitizz:list", ["manage", "host"], 10),
        ("QUITIZZ_CREATE", "Create QuiTizz", "quitizz:create", ["manage"], 20),
    ):
        item, _ = Item.objects.using(alias).update_or_create(portal="FACULTY", code=code, defaults={"menu_group": group, "label": label, "route_name": route, "sort_order": order, "is_active": True})
        for action in actions:
            permission = Permission.objects.using(alias).get(code=f"quitizz.{action}")
            Link.objects.using(alias).get_or_create(menu_item=item, permission=permission)


def unseed(apps, schema_editor):
    alias = schema_editor.connection.alias
    apps.get_model("navigation", "MenuItem").objects.using(alias).filter(
        portal="FACULTY", code__in=["QUITIZZ_LIST", "QUITIZZ_CREATE"],
    ).update(is_active=False)
    apps.get_model("navigation", "MenuGroup").objects.using(alias).filter(
        portal="FACULTY", code="QUITIZZ",
    ).update(is_active=False)


class Migration(migrations.Migration):
    dependencies = [("quitizz", "0002_seed_permissions"), ("navigation", "0026_exam_workflow_labels")]
    # Reverse hides navigation without deleting configured rows or permission links.
    operations = [migrations.RunPython(seed, unseed)]

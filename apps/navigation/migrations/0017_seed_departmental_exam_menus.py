from django.db import migrations


# These exact portal-scoped group and item codes are reserved TeacherMate+
# identifiers.  Forward and reverse operations intentionally target only this
# portal/group/item combination and leave same-code items in other scopes alone.
GROUP_SPECS = [
    ("ADMIN", "DEPARTMENTAL_EXAMS", "Departmental Exam Builder"),
]

MENU_SPECS = [
    (
        "ADMIN",
        "DEPARTMENTAL_EXAMS",
        "DE_EXAM_CYCLES",
        "Overview / Exam Cycles",
        "departmental_exams:cycle_list",
        ("departmental_exams.manage_cycles",),
    ),
    (
        "ADMIN",
        "DEPARTMENTAL_EXAMS",
        "DE_EXAM_ASSIGNED_COURSES",
        "Assigned Course Examinations",
        "departmental_exams:assigned_course_examinations",
        (
            "departmental_exams.configure",
            "departmental_exams.review_generate",
        ),
    ),
]


def seed_menu(apps, schema_editor):
    MenuGroup = apps.get_model("navigation", "MenuGroup")
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")
    Permission = apps.get_model("rbac", "Permission")
    groups = {}
    for portal, code, label in GROUP_SPECS:
        group, _ = MenuGroup.objects.get_or_create(
            portal=portal,
            code=code,
            defaults={"label": label, "sort_order": 85, "is_active": True},
        )
        groups[(portal, code)] = group

    for portal, group_code, code, label, route, permission_codes in MENU_SPECS:
        group = groups[(portal, group_code)]
        item = MenuItem.objects.filter(portal=portal, code=code).first()
        if item is None:
            item = MenuItem.objects.create(
                menu_group=group,
                portal=portal,
                code=code,
                label=label,
                route_name=route,
                sort_order=10,
                is_active=True,
            )
        elif item.menu_group_id != group.id:
            continue

        for permission_code in permission_codes:
            permission = Permission.objects.filter(code=permission_code).first()
            if permission:
                MenuItemPermission.objects.get_or_create(menu_item=item, permission=permission)


def unseed_menu(apps, schema_editor):
    MenuGroup = apps.get_model("navigation", "MenuGroup")
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")
    Permission = apps.get_model("rbac", "Permission")

    for portal, group_code, code, _label, _route, permission_codes in MENU_SPECS:
        group = MenuGroup.objects.filter(portal=portal, code=group_code).first()
        if not group:
            continue
        item = MenuItem.objects.filter(menu_group=group, portal=portal, code=code).first()
        if not item:
            continue
        for permission_code in permission_codes:
            permission = Permission.objects.filter(code=permission_code).first()
            if permission:
                MenuItemPermission.objects.filter(menu_item=item, permission=permission).delete()
        if not MenuItemPermission.objects.filter(menu_item=item).exists() and not MenuItem.objects.filter(parent=item).exists():
            item.delete()

    for portal, code, _label in GROUP_SPECS:
        group = MenuGroup.objects.filter(portal=portal, code=code).first()
        if group and not MenuItem.objects.filter(menu_group=group).exists():
            group.delete()


class Migration(migrations.Migration):
    dependencies = [("navigation", "0016_seed_academic_data_reconciliation_menu"), ("rbac", "0032_seed_departmental_exam_permissions")]
    operations = [migrations.RunPython(seed_menu, unseed_menu)]

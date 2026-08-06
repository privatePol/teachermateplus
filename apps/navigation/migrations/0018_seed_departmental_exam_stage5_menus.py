from django.db import migrations


GROUP_SPECS = [
    ("ADMIN", "DEPARTMENTAL_EXAMS", "Departmental Exam Builder", 85),
    ("FACULTY", "DEPARTMENTAL_EXAMS", "Departmental Exam Builder", 35),
]

ITEM_SPECS = [
    (
        "ADMIN",
        "DEPARTMENTAL_EXAMS",
        "DE_EXAM_CONTRIBUTOR_MONITORING",
        "Contributor Completion",
        "departmental_exams:contributor_monitoring",
        30,
        ("departmental_exams.configure", "departmental_exams.review_generate"),
    ),
    (
        "FACULTY",
        "DEPARTMENTAL_EXAMS",
        "DE_EXAM_FACULTY_CONTRIBUTIONS",
        "Question Contributions",
        "departmental_exams:contribution_list",
        10,
        ("faculty_portal.access",),
    ),
]


def seed(apps, schema_editor):
    MenuGroup = apps.get_model("navigation", "MenuGroup")
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")
    Permission = apps.get_model("rbac", "Permission")
    Permission.objects.get_or_create(
        code="faculty_portal.access",
        defaults={
            "module": "faculty_portal",
            "action": "access",
            "description": "Allows the user to sign in to the Faculty Portal.",
            "is_active": True,
        },
    )
    groups = {}
    for portal, code, label, sort_order in GROUP_SPECS:
        group, _ = MenuGroup.objects.get_or_create(
            portal=portal,
            code=code,
            defaults={"label": label, "sort_order": sort_order, "is_active": True},
        )
        groups[(portal, code)] = group
    for portal, group_code, code, label, route, sort_order, permission_codes in ITEM_SPECS:
        group = groups[(portal, group_code)]
        item = MenuItem.objects.filter(portal=portal, code=code).first()
        if item is None:
            item = MenuItem.objects.create(
                menu_group=group,
                portal=portal,
                code=code,
                label=label,
                route_name=route,
                sort_order=sort_order,
                is_active=True,
            )
        elif item.menu_group_id != group.id:
            continue
        for permission_code in permission_codes:
            permission = Permission.objects.filter(code=permission_code, is_active=True).first()
            if permission:
                MenuItemPermission.objects.get_or_create(menu_item=item, permission=permission)


def unseed(apps, schema_editor):
    MenuGroup = apps.get_model("navigation", "MenuGroup")
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")
    for portal, group_code, code, _label, _route, _sort_order, _permission_codes in ITEM_SPECS:
        group = MenuGroup.objects.filter(portal=portal, code=group_code).first()
        if not group:
            continue
        item = MenuItem.objects.filter(portal=portal, code=code, menu_group=group).first()
        if item:
            MenuItemPermission.objects.filter(menu_item=item).delete()
            item.delete()
    for portal, code, _label, _sort_order in reversed(GROUP_SPECS):
        group = MenuGroup.objects.filter(portal=portal, code=code).first()
        if group and not MenuItem.objects.filter(menu_group=group).exists():
            group.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("navigation", "0017_seed_departmental_exam_menus"),
    ]
    operations = [migrations.RunPython(seed, unseed)]

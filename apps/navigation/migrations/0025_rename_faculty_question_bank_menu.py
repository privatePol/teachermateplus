from django.db import migrations


FACULTY_PORTAL = "FACULTY"
MENU_GROUP_CODE = "DEPARTMENTAL_EXAMS"
MENU_ITEM_CODE = "DE_EXAM_FACULTY_CONTRIBUTIONS"
OLD_LABEL = "Question Contributions"
NEW_LABEL = "Question Bank"


def _rename_owned_item(apps, *, from_label, to_label):
    MenuGroup = apps.get_model("navigation", "MenuGroup")
    MenuItem = apps.get_model("navigation", "MenuItem")

    group = MenuGroup.objects.filter(
        portal=FACULTY_PORTAL,
        code=MENU_GROUP_CODE,
    ).first()
    if not group:
        return

    MenuItem.objects.filter(
        menu_group=group,
        portal=FACULTY_PORTAL,
        code=MENU_ITEM_CODE,
        label=from_label,
    ).update(label=to_label)


def rename_to_question_bank(apps, schema_editor):
    _rename_owned_item(apps, from_label=OLD_LABEL, to_label=NEW_LABEL)


def rename_to_question_contributions(apps, schema_editor):
    _rename_owned_item(apps, from_label=NEW_LABEL, to_label=OLD_LABEL)


class Migration(migrations.Migration):
    dependencies = [
        ("navigation", "0024_seed_planning_readiness_menu"),
    ]

    operations = [
        migrations.RunPython(
            rename_to_question_bank,
            rename_to_question_contributions,
        ),
    ]

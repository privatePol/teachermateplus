from django.db import migrations


LABELS = (
    ("DE_EXAM_ASSIGNED_COURSES", "Assigned Course Examinations", "Manage Course Exams"),
    ("DE_EXAM_AUTOMATIC_GENERATION_SUMMARY", "Automatic Generation Summary", "Exam Generation Status"),
    ("DE_EXAM_PLANNING_READINESS", "Planning & Readiness", "Exam Readiness"),
    ("DE_EXAM_CONTRIBUTOR_MONITORING", "Contributor Completion", "Faculty Contribution Status"),
    ("DE_EXAM_QUESTIONNAIRE_PRINT_RELEASE", "Questionnaire Print Release", "Release Exam for Printing"),
)


def rename(apps, schema_editor, reverse=False):
    items = apps.get_model("navigation", "MenuItem").objects.using(schema_editor.connection.alias)
    for code, old, new in LABELS:
        source, destination = (new, old) if reverse else (old, new)
        items.filter(portal="ADMIN", menu_group__portal="ADMIN",
                     menu_group__code="DEPARTMENTAL_EXAMS", code=code,
                     label=source).update(label=destination)


def restore(apps, schema_editor):
    rename(apps, schema_editor, reverse=True)


class Migration(migrations.Migration):
    dependencies = [("navigation", "0025_rename_faculty_question_bank_menu")]
    operations = [migrations.RunPython(rename, restore)]

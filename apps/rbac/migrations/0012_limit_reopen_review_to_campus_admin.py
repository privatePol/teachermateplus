from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("rbac", "0011_seed_student_enrollment_query_permission"),
    ]

    operations = [
        migrations.RunPython(migrations.RunPython.noop, migrations.RunPython.noop),
    ]

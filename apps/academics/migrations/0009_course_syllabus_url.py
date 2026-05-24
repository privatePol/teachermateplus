from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("academics", "0008_term_term_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="course",
            name="syllabus_url",
            field=models.URLField(
                blank=True,
                help_text="Optional Google Drive or external syllabus link for this course.",
                max_length=1000,
                null=True,
            ),
        ),
    ]

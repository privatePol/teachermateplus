from django.db import migrations, models


def preserve_rich_history(apps, schema_editor):
    alias = schema_editor.connection.alias
    if (apps.get_model("departmental_exams", "GeneratedExamItem").objects.using(alias)
            .exclude(scenario_content_format_snapshot="PLAIN_TEXT").exists()
            or apps.get_model("departmental_exams", "GeneratedExamSet").objects.using(alias)
            .exclude(structured_content_digest="").exists()):
        raise RuntimeError("Case generation snapshots exist; their rich format and integrity evidence cannot be removed.")


class Migration(migrations.Migration):
    dependencies = [("departmental_exams", "0027_answer_key_release_target_scope")]
    operations = [
        migrations.AlterField(
            model_name="generatedexamitem", name="scenario_stimulus_snapshot",
            field=models.TextField(blank=True, max_length=50000),
        ),
        migrations.AddField(
            model_name="generatedexamitem", name="scenario_content_format_snapshot",
            field=models.CharField(max_length=20, default="PLAIN_TEXT",
                                   choices=[("PLAIN_TEXT", "Plain text"), ("RICH_HTML_V1", "Rich HTML V1")]),
        ),
        migrations.AddField(
            model_name="generatedexamset", name="structured_content_digest",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.RunPython(migrations.RunPython.noop, preserve_rich_history),
    ]

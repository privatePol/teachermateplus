from django.db import migrations, models
import django.db.models.deletion


def preflight_legacy_published_rows(apps, schema_editor):
    Configuration = apps.get_model("departmental_exams", "CourseExamConfiguration")
    invalid = Configuration.objects.filter(is_published=True).filter(
        models.Q(published_at__isnull=True) | models.Q(published_by__isnull=True)
    )
    if invalid.exists():
        raise RuntimeError(
            "Cannot migrate published CourseExamConfiguration rows without trustworthy published_at and published_by metadata."
        )


def noop_preflight_reverse(apps, schema_editor):
    pass


def migrate_legacy_publication(apps, schema_editor):
    Configuration = apps.get_model("departmental_exams", "CourseExamConfiguration")
    Configuration.objects.filter(is_published=False).update(workflow_status="DRAFT")
    Configuration.objects.filter(is_published=True).update(workflow_status="CLOSED")
    # Retain legacy instructions as the newly user-visible course instructions.
    Configuration.objects.filter(additional_instructions="").exclude(general_instructions="").update(
        additional_instructions=models.F("general_instructions")
    )


def reverse_legacy_publication(apps, schema_editor):
    Configuration = apps.get_model("departmental_exams", "CourseExamConfiguration")
    # 0001 stored non-null counts.  A Stage 4 Draft deliberately permits an
    # incomplete count; on reverse, restore the documented 0001 defaults only
    # for those null Draft values and preserve every populated legacy value.
    Configuration.objects.filter(final_item_count__isnull=True).update(final_item_count=50)
    Configuration.objects.filter(questions_required_per_faculty__isnull=True).update(
        questions_required_per_faculty=1
    )
    Configuration.objects.filter(workflow_status="DRAFT").update(is_published=False)
    Configuration.objects.exclude(workflow_status="DRAFT").update(is_published=True)


class Migration(migrations.Migration):
    dependencies = [("departmental_exams", "0001_initial")]

    operations = [
        migrations.RunPython(preflight_legacy_published_rows, noop_preflight_reverse),
        migrations.AddField(
            model_name="examinationcycle",
            name="item_count_mode",
            field=models.CharField(blank=True, choices=[("FIXED_ALL", "Fixed Item Count for All Courses"), ("PER_COURSE", "Configure Item Count per Course")], max_length=12, null=True),
        ),
        migrations.AddField(model_name="examinationcycle", name="fixed_final_item_count", field=models.PositiveSmallIntegerField(blank=True, default=None, null=True)),
        migrations.AddField(model_name="examinationcycle", name="contributor_instructions", field=models.TextField(blank=True)),
        migrations.RenameField(model_name="courseexamconfiguration", old_name="required_questions_per_faculty", new_name="questions_required_per_faculty"),
        migrations.RenameField(model_name="courseexamconfiguration", old_name="submission_deadline", new_name="contribution_deadline"),
        migrations.RenameField(model_name="courseexamconfiguration", old_name="published_at", new_name="opened_at"),
        migrations.RenameField(model_name="courseexamconfiguration", old_name="published_by", new_name="opened_by"),
        migrations.AlterField(model_name="courseexamconfiguration", name="final_item_count", field=models.PositiveSmallIntegerField(blank=True, default=None, null=True)),
        migrations.AlterField(model_name="courseexamconfiguration", name="questions_required_per_faculty", field=models.PositiveSmallIntegerField(blank=True, default=None, null=True)),
        migrations.AlterField(model_name="courseexamconfiguration", name="opened_by", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="opened_exam_configurations", to="accounts.user")),
        migrations.AddField(model_name="courseexamconfiguration", name="workflow_status", field=models.CharField(choices=[("DRAFT", "Draft"), ("OPEN", "Open for Faculty Contribution"), ("CLOSED", "Closed")], default="DRAFT", max_length=10)),
        migrations.AddField(model_name="courseexamconfiguration", name="closed_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="courseexamconfiguration", name="closed_by", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="closed_exam_configurations", to="accounts.user")),
        migrations.AddField(model_name="courseexamconfiguration", name="coverage", field=models.TextField(blank=True)),
        migrations.AddField(model_name="courseexamconfiguration", name="additional_instructions", field=models.TextField(blank=True)),
        migrations.AddField(model_name="courseexamconfiguration", name="contributor_instructions_snapshot", field=models.TextField(blank=True)),
        migrations.AddField(model_name="courseexamconfiguration", name="item_count_mode_snapshot", field=models.CharField(blank=True, choices=[("FIXED_ALL", "Fixed Item Count for All Courses"), ("PER_COURSE", "Configure Item Count per Course")], max_length=12, null=True)),
        migrations.RunPython(migrate_legacy_publication, reverse_legacy_publication),
        migrations.RemoveField(model_name="courseexamconfiguration", name="is_published"),
        migrations.AddConstraint(
            model_name="examinationcycle",
            constraint=models.CheckConstraint(
                condition=(models.Q(item_count_mode__isnull=True, fixed_final_item_count__isnull=True) | models.Q(item_count_mode="FIXED_ALL", fixed_final_item_count__gte=1, fixed_final_item_count__lte=200) | models.Q(item_count_mode="PER_COURSE", fixed_final_item_count__isnull=True)),
                name="ck_de_cycle_item_count_mode",
            ),
        ),
        migrations.AddIndex(model_name="courseexamconfiguration", index=models.Index(fields=["workflow_status", "contribution_deadline"], name="idx_de_cfg_status_deadline")),
    ]

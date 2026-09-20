import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.migrations.exceptions import IrreversibleError


def snapshot_legacy_coverage(apps, schema_editor):
    alias = schema_editor.connection.alias
    Release = apps.get_model("departmental_exams", "QuestionnairePrintRelease")
    Coverage = apps.get_model("departmental_exams", "QuestionnaireLegacyCampusCoverage")
    Membership = apps.get_model("departmental_exams", "ExamCourseEquivalencyMembership")
    Offering = apps.get_model("departmental_exams", "CycleCourseOffering")
    Release.objects.using(alias).all().update(scope_kind="LEGACY_COURSE_WIDE", scope_key=0)
    for release in Release.objects.using(alias).filter(status="ACTIVE", active_marker=1).iterator():
        cycle = release.cycle_course.cycle
        member_ids = {release.cycle_course_id}
        member_ids.update(Membership.objects.using(alias).filter(
            group__primary_cycle_course_id=release.cycle_course_id,
            group__is_active=True, active_marker=1,
        ).values_list("cycle_course_id", flat=True))
        campus_ids = Offering.objects.using(alias).filter(
            cycle_course_id__in=member_ids,
            cycle_course__inclusion_status="INCLUDED",
            campus__tenant_id=cycle.tenant_id,
            offering__tenant_id=cycle.tenant_id,
            offering__campus_id=models.F("campus_id"),
            offering__course_id=models.F("cycle_course__course_id"),
            offering__academic_year_id=cycle.academic_year_id,
            offering__term_id=cycle.term_id,
        ).values_list("campus_id", flat=True).distinct()
        Coverage.objects.using(alias).bulk_create(
            Coverage(release_id=release.id, campus_id=campus_id)
            for campus_id in campus_ids
        )


def guard_reverse(apps, schema_editor):
    alias = schema_editor.connection.alias
    Release = apps.get_model("departmental_exams", "QuestionnairePrintRelease")
    Coverage = apps.get_model("departmental_exams", "QuestionnaireLegacyCampusCoverage")
    if (Release.objects.using(alias).filter(scope_kind="SCOPED").exists()
            or Coverage.objects.using(alias).exists()):
        raise IrreversibleError(
            "Scoped Questionnaire releases or retired legacy coverage exist; "
            "reversal would erase campus access history. Use a reviewed forward recovery."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("departmental_exams", "0032_contribution_correction_versions"),
        ("tenants", "0005_enable_existing_sis_api_feature"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="answerkeyrelease", name="review_confirmation_id",
            field=models.CharField(max_length=32, null=True, blank=True),
        ),
        migrations.AddField(
            model_name="questionnaireprintrelease", name="review_confirmation_id",
            field=models.CharField(max_length=32, null=True, blank=True),
        ),
        migrations.AddField(
            model_name="questionnaireprintrelease", name="scope_kind",
            field=models.CharField(
                max_length=20, default="LEGACY_COURSE_WIDE",
                choices=[("SCOPED", "Campus scoped"), ("LEGACY_COURSE_WIDE", "Legacy course-wide")],
            ),
        ),
        migrations.AddField(
            model_name="questionnaireprintrelease", name="target_campus",
            field=models.ForeignKey(
                to="tenants.campus", on_delete=django.db.models.deletion.PROTECT,
                null=True, blank=True, related_name="questionnaire_print_releases",
            ),
        ),
        migrations.AddField(
            model_name="questionnaireprintrelease", name="scope_key",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.CreateModel(
            name="QuestionnaireLegacyCampusCoverage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("retired_at", models.DateTimeField(null=True, blank=True)),
                ("campus", models.ForeignKey(
                    to="tenants.campus", on_delete=django.db.models.deletion.PROTECT,
                    related_name="legacy_questionnaire_coverage",
                )),
                ("release", models.ForeignKey(
                    to="departmental_exams.questionnaireprintrelease",
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="legacy_campus_coverage",
                )),
                ("retired_by", models.ForeignKey(
                    to=settings.AUTH_USER_MODEL, on_delete=django.db.models.deletion.PROTECT,
                    null=True, blank=True, related_name="retired_questionnaire_coverage",
                )),
            ],
            options={"db_table": "departmental_exam_questionnaire_legacy_coverage"},
        ),
        migrations.RunPython(snapshot_legacy_coverage, migrations.RunPython.noop),
        migrations.RemoveConstraint(model_name="questionnaireprintrelease", name="uq_de_print_release_active"),
        migrations.AddConstraint(
            model_name="questionnaireprintrelease",
            constraint=models.UniqueConstraint(
                fields=("cycle_course", "scope_key", "active_marker"),
                name="uq_de_print_scope_active",
            ),
        ),
        migrations.AddConstraint(
            model_name="questionnaireprintrelease",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(scope_kind="LEGACY_COURSE_WIDE", target_campus__isnull=True, scope_key=0)
                    | models.Q(scope_kind="SCOPED", target_campus__isnull=False,
                               scope_key=models.F("target_campus_id"))
                ), name="ck_de_print_scope",
            ),
        ),
        migrations.AddConstraint(
            model_name="questionnairelegacycampuscoverage",
            constraint=models.UniqueConstraint(
                fields=("release", "campus"), name="uq_de_print_legacy_campus",
            ),
        ),
        migrations.RunPython(migrations.RunPython.noop, guard_reverse),
    ]

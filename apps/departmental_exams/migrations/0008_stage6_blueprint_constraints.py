from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("departmental_exams", "0007_stage6_blueprint_resolution_foundation"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="blockedcontributionresolution",
            index=models.Index(fields=["cycle_course", "roster_revision_snapshot"], name="idx_de_block_resolution_roster"),
        ),
        migrations.AddConstraint(
            model_name="blockedcontributionresolution",
            constraint=models.UniqueConstraint(fields=("contribution", "blocked_at_snapshot", "contribution_revision_snapshot"), name="uq_de_block_resolution_state"),
        ),
        migrations.AddConstraint(
            model_name="blockedcontributionresolution",
            constraint=models.CheckConstraint(condition=models.Q(("contribution_revision_snapshot__gte", 1), ("roster_revision_snapshot__gte", 1)), name="ck_de_block_resolution_revisions"),
        ),
        migrations.AddIndex(
            model_name="examblueprint",
            index=models.Index(fields=["mode", "updated_at"], name="idx_de_blueprint_mode"),
        ),
        migrations.AddConstraint(
            model_name="examblueprint",
            constraint=models.CheckConstraint(condition=models.Q(("revision__gte", 1)), name="ck_de_blueprint_revision"),
        ),
        migrations.AddIndex(
            model_name="examscenariomember",
            index=models.Index(fields=["scenario", "position"], name="idx_de_scenario_member_order"),
        ),
        migrations.AddConstraint(
            model_name="examscenariomember",
            constraint=models.UniqueConstraint(fields=("scenario", "position"), name="uq_de_scenario_member_position"),
        ),
        migrations.AddConstraint(
            model_name="examscenariomember",
            constraint=models.CheckConstraint(condition=models.Q(("position__gte", 1)), name="ck_de_scenario_member_position"),
        ),
        migrations.AddIndex(
            model_name="examsection",
            index=models.Index(fields=["blueprint", "display_order"], name="idx_de_section_order"),
        ),
        migrations.AddConstraint(
            model_name="examsection",
            constraint=models.UniqueConstraint(fields=("blueprint", "display_order"), name="uq_de_section_blueprint_order"),
        ),
        migrations.AddConstraint(
            model_name="examsection",
            constraint=models.CheckConstraint(condition=models.Q(("display_order__gte", 1), ("item_quota__gte", 1)), name="ck_de_section_positive_values"),
        ),
        migrations.AddIndex(
            model_name="examscenario",
            index=models.Index(fields=["blueprint", "section"], name="idx_de_scenario_section"),
        ),
        migrations.AddConstraint(
            model_name="examscenario",
            constraint=models.CheckConstraint(condition=models.Q(("revision__gte", 1)), name="ck_de_scenario_revision"),
        ),
        migrations.AddIndex(
            model_name="questionblueprintplacement",
            index=models.Index(fields=["blueprint", "section"], name="idx_de_placement_section"),
        ),
        migrations.AddConstraint(
            model_name="questionblueprintplacement",
            constraint=models.CheckConstraint(condition=models.Q(("revision__gte", 1)), name="ck_de_placement_revision"),
        ),
    ]

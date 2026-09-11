from importlib import import_module
from types import SimpleNamespace

from django.apps import apps
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.utils import timezone


class WorkflowLabelMigrationTests(TestCase):
    def test_labels_are_idempotent_scoped_and_permissions_unchanged(self):
        from apps.navigation.models import MenuGroup, MenuItem, MenuItemPermission
        from apps.rbac.models import Permission
        migration = import_module("apps.navigation.migrations.0026_exam_workflow_labels")
        editor = SimpleNamespace(connection=connection)
        group, _ = MenuGroup.objects.get_or_create(portal="ADMIN", code="DEPARTMENTAL_EXAMS", defaults={"label": "Departmental Exam Builder"})
        permission, _ = Permission.objects.get_or_create(code="departmental_exams.manage_cycles", defaults={"module": "departmental_exams", "action": "manage_cycles", "description": "Manage cycles"})
        for code, old, new in migration.LABELS:
            item, _ = MenuItem.objects.get_or_create(portal="ADMIN", code=code, defaults={"menu_group": group, "label": old, "route_name": "departmental_exams:cycle_list"})
            MenuItemPermission.objects.get_or_create(menu_item=item, permission=permission)
        permissions = list(MenuItemPermission.objects.order_by("pk").values())
        before = {row.code: row for row in MenuItem.objects.filter(code__in=[r[0] for r in migration.LABELS], portal="ADMIN")}
        all_fields = list(MenuItem.objects.filter(pk__in=[r.id for r in before.values()]).order_by("pk").values())
        migration.restore(apps, editor)
        migration.rename(apps, editor)
        migration.rename(apps, editor)
        for code, old, new in migration.LABELS:
            item = MenuItem.objects.get(pk=before[code].id)
            self.assertEqual(item.label, new)
            self.assertEqual(item.route_name, before[code].route_name)
            self.assertEqual(item.menu_group_id, before[code].menu_group_id)
        self.assertEqual(list(MenuItemPermission.objects.order_by("pk").values()), permissions)
        after_fields = list(MenuItem.objects.filter(pk__in=[r.id for r in before.values()]).order_by("pk").values())
        for left, right in zip(all_fields, after_fields):
            left.pop("label")
            right.pop("label")
        self.assertEqual(after_fields, all_fields)  # Includes IDs, ordering, flags and timestamps.
        item = next(iter(before.values()))
        MenuItem.objects.filter(pk=item.id).update(label="Custom administrator label")
        migration.rename(apps, editor)
        self.assertEqual(MenuItem.objects.get(pk=item.id).label, "Custom administrator label")
        migration.restore(apps, editor)
        self.assertEqual(MenuItem.objects.get(pk=item.id).label, "Custom administrator label")


class ClassificationForwardMigrationTests(TransactionTestCase):
    def test_actual_forward_migration_retains_legacy_structure_and_revisions(self):
        executor = MigrationExecutor(connection)
        leaves = executor.loader.graph.leaf_nodes()
        old_targets = [(app, "0025_faculty_case_rich_content") if app == "departmental_exams" else (app, name) for app, name in leaves]
        executor.migrate(old_targets)
        try:
            old = executor.loader.project_state(old_targets).apps
            tenant = old.get_model("tenants", "Tenant").objects.create(code="MIGU", name="Migration fixture")
            user = old.get_model("accounts", "User").objects.create(username="migration-user", password="")
            campus = old.get_model("tenants", "Campus").objects.create(tenant=tenant, code="M", name="Migration campus")
            year = old.get_model("academics", "AcademicYear").objects.create(tenant=tenant, code="Y", name="Y", start_date="2026-01-01", end_date="2026-12-31")
            term = old.get_model("academics", "Term").objects.create(tenant=tenant, academic_year=year, code="T", name="T")
            saved = []
            states = [(mode, status) for mode in ("MANUAL_REVIEW", "AUTOMATIC_GENERATION")
                      for status in ("DRAFT", "OPEN", "CLOSED")]
            for index, (mode, status) in enumerate(states):
                cycle_term = old.get_model("academics", "Term").objects.create(
                    tenant=tenant, academic_year=year, code="T" + str(index), name="Term " + str(index))
                cycle = old.get_model("departmental_exams", "ExaminationCycle").objects.create(tenant=tenant, academic_year=year, term=cycle_term, exam_period="FINAL", status=status, processing_mode=mode, created_by=user)
                course = old.get_model("academics", "Course").objects.create(tenant=tenant, code="M"+str(index), title="Legacy")
                parent = old.get_model("departmental_exams", "CycleCourse").objects.create(cycle=cycle, course=course)
                blueprint = old.get_model("departmental_exams", "ExamBlueprint").objects.create(cycle_course=parent, mode="NO_SECTIONS", revision=7, created_by=user, updated_by=user)
                scenario = old.get_model("departmental_exams", "ExamScenario").objects.create(blueprint=blueprint, title="Preserved", stimulus='<p>Legacy ₱ content</p>', content_format="RICH_HTML_V1", created_by=user, updated_by=user)
                saved.append((parent.pk, cycle.pk, mode, status, blueprint.pk, scenario.pk))
                if mode == "MANUAL_REVIEW":
                    blueprint.mode = "USE_SECTIONS"
                    blueprint.save()
                    section = old.get_model("departmental_exams", "ExamSection").objects.create(
                        blueprint=blueprint, title="Preserved section", display_order=1, item_quota=50)
                    contribution = old.get_model("departmental_exams", "FacultyContribution").objects.create(
                        cycle_course=parent, faculty_user=user, source_campus=campus,
                        quota_snapshot=50, configuration_revision_snapshot=2,
                        status="SUBMITTED", submitted_at=timezone.now())
                    question = old.get_model("departmental_exams", "Question").objects.create(
                        contribution=contribution, question_text="Preserved question", choice_a="A",
                        choice_b="B", choice_c="C", choice_d="D", correct_answer="B",
                        difficulty="MODERATE", position=3)
                    old.get_model("departmental_exams", "QuestionBlueprintPlacement").objects.create(
                        blueprint=blueprint, question=question, section=section, placed_by=user)
                    scenario.section = section
                    scenario.save()
            revision_model = old.get_model("departmental_exams", "ExamGenerationRevision")
            revision = revision_model.objects.create(cycle_course_id=saved[-1][0], revision_number=1,
                generated_by=user, generation_trigger="MANUAL", source_input_fingerprint="a"*64,
                algorithm_version="historical-fixture", configuration_revision_snapshot=9,
                blueprint_revision_snapshot=7, roster_boundary_snapshot="b"*64,
                final_item_count_snapshot=60, request_token_digest="c"*64,
                minimum_overlap=0, proportional_score=0, contributors_represented=1,
                squared_contributor_concentration=1)
            before_revision = revision_model.objects.values().get(pk=revision.pk)
            retained_models = ("CycleCourse", "ExaminationCycle", "ExamBlueprint", "ExamSection",
                               "ExamScenario", "FacultyContribution", "Question", "QuestionBlueprintPlacement")
            retained = {name: list(old.get_model("departmental_exams", name).objects.order_by("pk").values())
                        for name in retained_models}
            executor = MigrationExecutor(connection)
            executor.migrate(leaves)
            current = executor.loader.project_state(leaves).apps
            for name, before_rows in retained.items():
                after_rows = list(current.get_model("departmental_exams", name).objects.order_by("pk").values())
                if name == "CycleCourse":
                    for row in after_rows:
                        self.assertEqual(row.pop("exam_classification"), "UNCLASSIFIED_LEGACY")
                self.assertEqual(after_rows, before_rows, name)
            for parent_id, cycle_id, mode, status, blueprint_id, scenario_id in saved:
                self.assertEqual(current.get_model("departmental_exams", "CycleCourse").objects.get(pk=parent_id).exam_classification, "UNCLASSIFIED_LEGACY")
                cycle = current.get_model("departmental_exams", "ExaminationCycle").objects.get(pk=cycle_id)
                self.assertEqual((cycle.processing_mode, cycle.status), (mode, status))
                self.assertEqual(current.get_model("departmental_exams", "ExamBlueprint").objects.get(pk=blueprint_id).revision, 7)
                self.assertEqual(current.get_model("departmental_exams", "ExamScenario").objects.get(pk=scenario_id).stimulus, '<p>Legacy ₱ content</p>')
            self.assertEqual(current.get_model("departmental_exams", "ExamGenerationRevision").objects.values().get(pk=revision.pk), before_revision)
        finally:
            MigrationExecutor(connection).migrate(leaves)

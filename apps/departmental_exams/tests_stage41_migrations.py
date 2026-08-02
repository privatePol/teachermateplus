"""Historical-model coverage for the Stage 4.1 deadline migration.

These tests are intentionally added in Gate 2 and are not executed here.
"""

import hashlib
from importlib import import_module

from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone

from .models import CourseExamConfiguration


class Stage41DeadlineMigrationTests(TransactionTestCase):
    migrate_from = ("departmental_exams", "0003_cao_default_override_counts")
    migrate_to = ("departmental_exams", "0004_stage41_default_contribution_deadline")

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.from_state = self._state(self.migrate_from)
        self.to_state = self._state(self.migrate_to)
        self.executor.migrate(self.from_state)
        self.old_apps = self.executor.loader.project_state(self.from_state).apps
        self.fixture = self._legacy_rows()

    def tearDown(self):
        MigrationExecutor(connection).migrate(
            MigrationExecutor(connection).loader.graph.leaf_nodes()
        )
        super().tearDown()

    def _state(self, departmental_target):
        return [
            departmental_target if app_label == "departmental_exams" else node
            for node in self.executor.loader.graph.leaf_nodes()
            for app_label, _name in [node]
        ]

    def _legacy_rows(self):
        Tenant = self.old_apps.get_model("tenants", "Tenant")
        Campus = self.old_apps.get_model("tenants", "Campus")
        Department = self.old_apps.get_model("tenants", "Department")
        Year = self.old_apps.get_model("academics", "AcademicYear")
        Term = self.old_apps.get_model("academics", "Term")
        Course = self.old_apps.get_model("academics", "Course")
        User = self.old_apps.get_model("accounts", "User")
        Cycle = self.old_apps.get_model("departmental_exams", "ExaminationCycle")
        CycleCourse = self.old_apps.get_model("departmental_exams", "CycleCourse")
        Configuration = self.old_apps.get_model("departmental_exams", "CourseExamConfiguration")
        token = hashlib.sha256(self._testMethodName.encode()).hexdigest()[:10].upper()
        tenant = Tenant.objects.create(code=f"D{token}", name=token)
        campus = Campus.objects.create(tenant=tenant, code=token, name=token)
        department = Department.objects.create(tenant=tenant, campus=campus, code=token, name=token)
        user = User.objects.create(username=f"u{token}", email=f"{token}@example.edu", password="", first_name="", last_name="", is_active=True, is_staff=False, is_superuser=False, faculty_quick_tour_disabled=False)
        year = Year.objects.create(tenant=tenant, code=token, name=token, start_date="2026-06-01", end_date="2027-05-31")
        term = Term.objects.create(tenant=tenant, academic_year=year, code=token, name=token)
        cycle = Cycle.objects.create(tenant=tenant, academic_year=year, term=term, exam_period="MIDTERM", created_by=user)
        deadline = timezone.now() + timezone.timedelta(days=5)
        rows = []
        for index, (workflow, stored_deadline) in enumerate((("OPEN", deadline), ("CLOSED", None)), start=1):
            course = Course.objects.create(tenant=tenant, code=f"{token}{index}", title=token, exam_department=department)
            parent = CycleCourse.objects.create(cycle=cycle, course=course, responsible_department=department)
            rows.append(Configuration.objects.create(
                cycle_course=parent,
                final_item_count=50,
                final_item_count_source="OVERRIDE",
                questions_required_per_faculty=50,
                questions_required_per_faculty_source="OVERRIDE",
                cycle_defaults_revision_snapshot=0,
                contribution_deadline=stored_deadline,
                workflow_status=workflow,
                opened_at=timezone.now() if workflow == "OPEN" else None,
                opened_by=user if workflow == "OPEN" else None,
                revision=7,
            ))
        return {"cycle": cycle, "configured": rows[0], "empty": rows[1], "deadline": deadline}

    def _forward(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.to_state)
        return self.executor.loader.project_state(self.to_state).apps

    def test_dependency_operation_order_and_mariadb_safe_constraint_name(self):
        migration = import_module(
            "apps.departmental_exams.migrations.0004_stage41_default_contribution_deadline"
        )
        self.assertEqual(migration.Migration.dependencies, [self.migrate_from])
        self.assertEqual(
            [operation.__class__.__name__ for operation in migration.Migration.operations],
            ["AddField", "AddField", "RunPython", "AddConstraint"],
        )
        constraint = migration.Migration.operations[-1].constraint
        self.assertLessEqual(len(constraint.name), 64)
        self.assertNotIn("index", repr(migration.Migration.operations).lower())
        model_constraint = next(
            item
            for item in CourseExamConfiguration._meta.constraints
            if item.name == constraint.name
        )
        self.assertEqual(repr(model_constraint.condition), repr(constraint.condition))

    def test_forward_backfill_preserves_values_lifecycle_and_revision(self):
        apps = self._forward()
        Cycle = apps.get_model("departmental_exams", "ExaminationCycle")
        Configuration = apps.get_model("departmental_exams", "CourseExamConfiguration")
        cycle = Cycle.objects.get(pk=self.fixture["cycle"].pk)
        configured = Configuration.objects.get(pk=self.fixture["configured"].pk)
        empty = Configuration.objects.get(pk=self.fixture["empty"].pk)
        self.assertIsNone(cycle.default_contribution_deadline)
        self.assertEqual(configured.contribution_deadline, self.fixture["deadline"])
        self.assertEqual(configured.contribution_deadline_source, "OVERRIDE")
        self.assertEqual((configured.workflow_status, configured.revision), ("OPEN", 7))
        self.assertIsNone(empty.contribution_deadline)
        self.assertIsNone(empty.contribution_deadline_source)
        self.assertEqual((empty.workflow_status, empty.revision), ("CLOSED", 7))

    def test_constraint_rejects_invalid_pairs_and_reverse_preserves_deadline(self):
        apps = self._forward()
        Configuration = apps.get_model("departmental_exams", "CourseExamConfiguration")
        configured = Configuration.objects.get(pk=self.fixture["configured"].pk)
        empty = Configuration.objects.get(pk=self.fixture["empty"].pk)

        configured.contribution_deadline_source = "DEFAULT"
        configured.save(update_fields=["contribution_deadline_source"])
        configured.contribution_deadline_source = "OVERRIDE"
        configured.save(update_fields=["contribution_deadline_source"])
        empty.contribution_deadline = None
        empty.contribution_deadline_source = None
        empty.save(
            update_fields=["contribution_deadline", "contribution_deadline_source"]
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            Configuration.objects.filter(pk=self.fixture["configured"].pk).update(
                contribution_deadline_source=None
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            Configuration.objects.filter(pk=self.fixture["empty"].pk).update(
                contribution_deadline_source="DEFAULT"
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            Configuration.objects.filter(pk=self.fixture["empty"].pk).update(
                contribution_deadline_source="OVERRIDE"
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            Configuration.objects.filter(pk=self.fixture["configured"].pk).update(
                contribution_deadline_source="INVALID"
            )
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.from_state)
        old_apps = self.executor.loader.project_state(self.from_state).apps
        OldConfiguration = old_apps.get_model("departmental_exams", "CourseExamConfiguration")
        self.assertEqual(
            OldConfiguration.objects.get(pk=self.fixture["configured"].pk).contribution_deadline,
            self.fixture["deadline"],
        )

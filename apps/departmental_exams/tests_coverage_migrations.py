import hashlib
from importlib import import_module

from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from .models import CourseExamConfiguration


class CoverageInvariantMigrationTests(TransactionTestCase):
    migrate_from = ("departmental_exams", "0011_automatic_generation_workflow")
    migrate_middle = ("departmental_exams", "0012_cycle_default_coverage")
    migrate_to = (
        "departmental_exams",
        "0013_correct_coverage_source_invariant",
    )

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.from_state = self._state(self.migrate_from)
        self.middle_state = self._state(self.migrate_middle)
        self.to_state = self._state(self.migrate_to)
        self.executor.migrate(self.from_state)
        old_apps = self.executor.loader.project_state(self.from_state).apps
        self.fixture = self._legacy_rows(old_apps)
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.middle_state)
        middle_apps = self.executor.loader.project_state(self.middle_state).apps
        Configuration = middle_apps.get_model(
            "departmental_exams", "CourseExamConfiguration"
        )
        Configuration.objects.filter(pk=self.fixture["blank_default"].pk).update(
            coverage_source="DEFAULT"
        )
        Configuration.objects.filter(pk=self.fixture["blank_override"].pk).update(
            coverage_source="OVERRIDE"
        )
        Configuration.objects.filter(pk=self.fixture["nonblank_null"].pk).update(
            coverage_source=None
        )

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

    def _legacy_rows(self, apps):
        Tenant = apps.get_model("tenants", "Tenant")
        Campus = apps.get_model("tenants", "Campus")
        Department = apps.get_model("tenants", "Department")
        Year = apps.get_model("academics", "AcademicYear")
        Term = apps.get_model("academics", "Term")
        Course = apps.get_model("academics", "Course")
        User = apps.get_model("accounts", "User")
        Cycle = apps.get_model("departmental_exams", "ExaminationCycle")
        CycleCourse = apps.get_model("departmental_exams", "CycleCourse")
        Configuration = apps.get_model(
            "departmental_exams", "CourseExamConfiguration"
        )
        token = hashlib.sha256(self._testMethodName.encode()).hexdigest()[:10].upper()
        tenant = Tenant.objects.create(code=f"C{token}", name=token)
        campus = Campus.objects.create(tenant=tenant, code=token, name=token)
        department = Department.objects.create(
            tenant=tenant,
            campus=campus,
            code=token,
            name=token,
        )
        user = User.objects.create(
            username=f"u{token}",
            email=f"{token}@example.edu",
            password="",
            first_name="",
            last_name="",
            is_active=True,
            is_staff=False,
            is_superuser=False,
            faculty_quick_tour_disabled=False,
        )
        year = Year.objects.create(
            tenant=tenant,
            code=token,
            name=token,
            start_date="2026-06-01",
            end_date="2027-05-31",
        )
        term = Term.objects.create(
            tenant=tenant,
            academic_year=year,
            code=token,
            name=token,
        )
        cycle = Cycle.objects.create(
            tenant=tenant,
            academic_year=year,
            term=term,
            exam_period="MIDTERM",
            created_by=user,
        )
        rows = {}
        values = (
            ("legacy_nonblank", "Legacy coverage", "OPEN", 7),
            ("blank_default", "", "DRAFT", 3),
            ("blank_override", "", "CLOSED", 5),
            ("nonblank_null", "Preserve this coverage", "DRAFT", 9),
        )
        for index, (key, coverage, workflow, revision) in enumerate(values, start=1):
            course = Course.objects.create(
                tenant=tenant,
                code=f"{token}{index}",
                title=key,
                exam_department=department,
            )
            parent = CycleCourse.objects.create(
                cycle=cycle,
                course=course,
                responsible_department=department,
            )
            rows[key] = Configuration.objects.create(
                cycle_course=parent,
                final_item_count=50,
                final_item_count_source="OVERRIDE",
                questions_required_per_faculty=50,
                questions_required_per_faculty_source="OVERRIDE",
                coverage=coverage,
                workflow_status=workflow,
                revision=revision,
            )
        return rows

    def _forward(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.to_state)
        return self.executor.loader.project_state(self.to_state).apps

    def test_dependency_operations_and_model_constraint_match(self):
        migration = import_module(
            "apps.departmental_exams.migrations.0013_correct_coverage_source_invariant"
        )
        self.assertEqual(migration.Migration.dependencies, [self.migrate_middle])
        self.assertEqual(
            [operation.__class__.__name__ for operation in migration.Migration.operations],
            ["RunPython", "RemoveConstraint", "AddConstraint"],
        )
        constraint = migration.Migration.operations[-1].constraint
        self.assertLessEqual(len(constraint.name), 64)
        model_constraint = next(
            item
            for item in CourseExamConfiguration._meta.constraints
            if item.name == constraint.name
        )
        self.assertEqual(repr(model_constraint.condition), repr(constraint.condition))

    def test_0011_to_0012_to_0013_normalizes_without_losing_history(self):
        apps = self._forward()
        Configuration = apps.get_model(
            "departmental_exams", "CourseExamConfiguration"
        )
        expected = {
            "legacy_nonblank": ("Legacy coverage", "OVERRIDE", "OPEN", 7),
            "blank_default": ("", None, "DRAFT", 3),
            "blank_override": ("", None, "CLOSED", 5),
            "nonblank_null": ("Preserve this coverage", "OVERRIDE", "DRAFT", 9),
        }
        for key, values in expected.items():
            configuration = Configuration.objects.get(pk=self.fixture[key].pk)
            self.assertEqual(
                (
                    configuration.coverage,
                    configuration.coverage_source,
                    configuration.workflow_status,
                    configuration.revision,
                ),
                values,
            )

    def test_final_constraint_rejects_every_invalid_pair(self):
        apps = self._forward()
        Configuration = apps.get_model(
            "departmental_exams", "CourseExamConfiguration"
        )
        blank = Configuration.objects.get(pk=self.fixture["blank_default"].pk)
        nonblank = Configuration.objects.get(pk=self.fixture["legacy_nonblank"].pk)
        for configuration, values in (
            (blank, {"coverage_source": "DEFAULT"}),
            (blank, {"coverage_source": "OVERRIDE"}),
            (nonblank, {"coverage_source": None}),
            (nonblank, {"coverage_source": "INVALID"}),
        ):
            with self.subTest(configuration=configuration.pk, values=values):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    Configuration.objects.filter(pk=configuration.pk).update(**values)

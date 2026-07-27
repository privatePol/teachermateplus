"""Historical-model forward/reverse tests for the unapplied Stage 4 migration."""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone
from importlib import import_module


class Stage4MigrationTests(TransactionTestCase):
    migrate_from = ("departmental_exams", "0001_initial")
    migrate_to = ("departmental_exams", "0002_stage4_course_configuration")

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.migrate_from_state = self._migration_state(self.migrate_from)
        self.migrate_to_state = self._migration_state(self.migrate_to)
        self.executor.migrate(self.migrate_from_state)
        self.old_apps = self.executor.loader.project_state(self.migrate_from_state).apps

    def _migration_state(self, departmental_exams_target):
        """Keep dependency apps at their real schema state while pinning this app."""
        return [
            departmental_exams_target if app_label == "departmental_exams" else node
            for node in self.executor.loader.graph.leaf_nodes()
            for app_label, _migration_name in [node]
        ]

    def tearDown(self):
        # Restore the test database migration state for the remaining suite.
        MigrationExecutor(connection).migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
        super().tearDown()

    def _legacy_scope(self, *, published=False, actor=True, timestamp=True):
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
        token = f"M{Configuration.objects.count() + 1}"
        tenant = Tenant.objects.create(code=token, name=f"Migration {token}")
        campus = Campus.objects.create(tenant=tenant, code=token, name=f"Campus {token}")
        department = Department.objects.create(tenant=tenant, campus=campus, code=token, name=f"Department {token}")
        user = User.objects.create(
            username=f"migration-{token}",
            email=f"migration-{token}@example.edu",
            password="",
            first_name="",
            last_name="",
            is_active=True,
            is_staff=False,
            is_superuser=False,
            faculty_quick_tour_disabled=False,
        )
        year = Year.objects.create(tenant=tenant, code=token, name=token, start_date="2026-06-01", end_date="2027-05-31")
        term = Term.objects.create(tenant=tenant, academic_year=year, code=token, name=token)
        course = Course.objects.create(tenant=tenant, code=token, title=token, exam_department=department)
        cycle = Cycle.objects.create(tenant=tenant, academic_year=year, term=term, exam_period="MIDTERM", created_by=user)
        parent = CycleCourse.objects.create(cycle=cycle, course=course, responsible_department=department)
        configuration = Configuration.objects.create(
            cycle_course=parent, final_item_count=61, required_questions_per_faculty=19,
            general_instructions="Legacy instructions", submission_deadline=timezone.now() + timezone.timedelta(days=3),
            easy_percent=30, moderate_percent=50, difficult_percent=20, revision=7,
            is_published=published, published_by=user if actor else None,
            published_at=timezone.now() if timestamp else None,
        )
        return configuration, parent

    def _forward(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_to_state)
        return self.executor.loader.project_state(self.migrate_to_state).apps

    def _delete_legacy_configuration(self, *, configuration_id):
        """Remove deliberately malformed 0001 data before base teardown migrates forward."""
        Configuration = self.old_apps.get_model(
            "departmental_exams", "CourseExamConfiguration"
        )
        Configuration.objects.filter(pk=configuration_id).delete()

    def test_forward_preserves_legacy_draft_values_and_null_transitional_mode(self):
        legacy, parent = self._legacy_scope(published=False)
        apps = self._forward()
        Configuration = apps.get_model("departmental_exams", "CourseExamConfiguration")
        migrated = Configuration.objects.get(pk=legacy.pk)
        self.assertEqual(migrated.workflow_status, "DRAFT")
        self.assertEqual(migrated.final_item_count, 61)
        self.assertEqual(migrated.questions_required_per_faculty, 19)
        self.assertEqual(migrated.additional_instructions, "Legacy instructions")
        self.assertEqual(migrated.contribution_deadline, legacy.submission_deadline)
        self.assertEqual(migrated.revision, 7)
        self.assertEqual(migrated.cycle_course_id, parent.id)
        self.assertIsNone(migrated.item_count_mode_snapshot)
        Cycle = apps.get_model("departmental_exams", "ExaminationCycle")
        self.assertIsNone(Cycle.objects.get(pk=parent.cycle_id).item_count_mode)

    def test_forward_maps_trusted_published_to_closed_and_reverse_restores_boolean(self):
        legacy, _ = self._legacy_scope(published=True)
        apps = self._forward()
        Configuration = apps.get_model("departmental_exams", "CourseExamConfiguration")
        self.assertEqual(Configuration.objects.get(pk=legacy.pk).workflow_status, "CLOSED")
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_from_state)
        old_apps = self.executor.loader.project_state(self.migrate_from_state).apps
        LegacyConfiguration = old_apps.get_model("departmental_exams", "CourseExamConfiguration")
        self.assertTrue(LegacyConfiguration.objects.get(pk=legacy.pk).is_published)

    def test_reverse_uses_0001_defaults_for_incomplete_stage4_draft_counts(self):
        legacy, _ = self._legacy_scope(published=False)
        apps = self._forward()
        Configuration = apps.get_model("departmental_exams", "CourseExamConfiguration")
        Configuration.objects.filter(pk=legacy.pk).update(
            final_item_count=None, questions_required_per_faculty=None
        )
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_from_state)
        old_apps = self.executor.loader.project_state(self.migrate_from_state).apps
        LegacyConfiguration = old_apps.get_model("departmental_exams", "CourseExamConfiguration")
        reversed_configuration = LegacyConfiguration.objects.get(pk=legacy.pk)
        self.assertEqual(reversed_configuration.final_item_count, 50)
        self.assertEqual(reversed_configuration.required_questions_per_faculty, 1)
        self.assertFalse(reversed_configuration.is_published)

    def test_forward_fails_closed_when_published_actor_or_timestamp_is_missing(self):
        legacy, _ = self._legacy_scope(published=True, actor=False)
        try:
            with self.assertRaisesRegex(RuntimeError, "trustworthy published_at and published_by"):
                self._forward()
        finally:
            self._delete_legacy_configuration(configuration_id=legacy.pk)

    def test_forward_fails_closed_when_published_timestamp_is_missing(self):
        legacy, _ = self._legacy_scope(published=True, timestamp=False)
        try:
            with self.assertRaisesRegex(RuntimeError, "trustworthy published_at and published_by"):
                self._forward()
        finally:
            self._delete_legacy_configuration(configuration_id=legacy.pk)

    def test_constraint_and_index_names_are_mariadb_safe_and_migration_has_no_rbac_operations(self):
        migration = import_module("apps.departmental_exams.migrations.0002_stage4_course_configuration")
        names = []
        for operation in migration.Migration.operations:
            constraint = getattr(operation, "constraint", None)
            index = getattr(operation, "index", None)
            if constraint:
                names.append(constraint.name)
            if index:
                names.append(index.name)
        self.assertSetEqual(set(names), {"ck_de_cycle_item_count_mode", "idx_de_cfg_status_deadline"})
        self.assertTrue(all(len(name) <= 64 for name in names))
        self.assertFalse(any(operation.__class__.__name__.startswith("RunSQL") for operation in migration.Migration.operations))

    def test_legacy_published_data_preflight_is_the_first_operation_before_schema_changes(self):
        migration = import_module("apps.departmental_exams.migrations.0002_stage4_course_configuration")
        first_operation = migration.Migration.operations[0]
        self.assertEqual(first_operation.__class__.__name__, "RunPython")
        self.assertEqual(first_operation.code.__name__, "preflight_legacy_published_rows")
        self.assertNotIn("AddField", [operation.__class__.__name__ for operation in migration.Migration.operations[:1]])

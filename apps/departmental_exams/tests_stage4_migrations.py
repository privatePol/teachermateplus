"""Historical-model tests for the CAO 0003 migration; execution is Gate 3."""

import hashlib
from importlib import import_module

from django.db import IntegrityError, connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class CAOCountMigrationTests(TransactionTestCase):
    migrate_from = ("departmental_exams", "0002_stage4_course_configuration")
    migrate_to = ("departmental_exams", "0003_cao_default_override_counts")

    def setUp(self):
        super().setUp()
        self.fixture_sequence = 0
        self.fixture_scope = hashlib.sha256(
            self._testMethodName.encode("utf-8")
        ).hexdigest()[:10].upper()
        self.executor = MigrationExecutor(connection)
        self.from_state = self._state(self.migrate_from)
        self.to_state = self._state(self.migrate_to)
        self.executor.migrate(self.from_state)
        self.old_apps = self.executor.loader.project_state(self.from_state).apps

    def tearDown(self):
        MigrationExecutor(connection).migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
        super().tearDown()

    def _state(self, departmental_target):
        return [
            departmental_target if app_label == "departmental_exams" else node
            for node in self.executor.loader.graph.leaf_nodes()
            for app_label, _name in [node]
        ]

    def _legacy_configuration(self, *, mode="FIXED_ALL", fixed=50, quota=50, final_count=50, workflow="DRAFT", opened=False):
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
        self.fixture_sequence += 1
        token = f"CAO{self.fixture_scope}{self.fixture_sequence}"
        tenant = Tenant.objects.create(code=token, name=token)
        campus = Campus.objects.create(tenant=tenant, code=token, name=token)
        department = Department.objects.create(tenant=tenant, campus=campus, code=token, name=token)
        user = User.objects.create(username=f"{token}-user", email=f"{token}@example.edu", password="", first_name="", last_name="", is_active=True, is_staff=False, is_superuser=False, faculty_quick_tour_disabled=False)
        year = Year.objects.create(tenant=tenant, code=token, name=token, start_date="2026-06-01", end_date="2027-05-31")
        term = Term.objects.create(tenant=tenant, academic_year=year, code=token, name=token)
        course = Course.objects.create(tenant=tenant, code=token, title=token, exam_department=department)
        cycle = Cycle.objects.create(tenant=tenant, academic_year=year, term=term, exam_period="MIDTERM", item_count_mode=mode, fixed_final_item_count=fixed, created_by=user)
        parent = CycleCourse.objects.create(cycle=cycle, course=course, responsible_department=department)
        configuration = Configuration.objects.create(cycle_course=parent, final_item_count=final_count, questions_required_per_faculty=quota, workflow_status=workflow, opened_at=timezone.now() if opened else None, opened_by=user if opened else None, revision=7)
        return {
            "tenant": tenant,
            "campus": campus,
            "department": department,
            "user": user,
            "year": year,
            "term": term,
            "course": course,
            "cycle": cycle,
            "parent": parent,
            "configuration": configuration,
        }

    @staticmethod
    def _cleanup_failed_preflight_fixture(fixture):
        """Remove only this historical-state fixture after a failed preflight."""
        fixture["configuration"].delete()
        fixture["parent"].delete()
        fixture["cycle"].delete()
        fixture["course"].delete()
        fixture["term"].delete()
        fixture["year"].delete()
        fixture["user"].delete()
        fixture["department"].delete()
        fixture["campus"].delete()
        fixture["tenant"].delete()

    def _forward(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.to_state)
        return self.executor.loader.project_state(self.to_state).apps

    def test_preflight_is_first_and_mariadb_safe_without_rbac_or_navigation_operations(self):
        migration = import_module("apps.departmental_exams.migrations.0003_cao_default_override_counts")
        self.assertEqual(migration.Migration.operations[0].__class__.__name__, "RunPython")
        self.assertEqual(migration.Migration.operations[0].code.__name__, "preflight_immutable_counts")
        names = [operation.constraint.name for operation in migration.Migration.operations if getattr(operation, "constraint", None)]
        self.assertTrue(all(len(name) <= 64 for name in names))
        self.assertFalse(any("rbac" in repr(operation).lower() or "navigation" in repr(operation).lower() for operation in migration.Migration.operations))

    def test_forward_backfill_marks_fixed_match_default_and_other_values_override(self):
        matching_fixture = self._legacy_configuration(fixed=50, final_count=50, quota=60)
        other_fixture = self._legacy_configuration(mode="PER_COURSE", fixed=None, final_count=75, quota=50)
        cycle = matching_fixture["cycle"]
        matching = matching_fixture["configuration"]
        other = other_fixture["configuration"]
        apps = self._forward()
        Cycle = apps.get_model("departmental_exams", "ExaminationCycle")
        Configuration = apps.get_model("departmental_exams", "CourseExamConfiguration")
        migrated_cycle = Cycle.objects.get(pk=cycle.pk)
        matched = Configuration.objects.get(pk=matching.pk)
        overridden = Configuration.objects.get(pk=other.pk)
        self.assertEqual(migrated_cycle.default_final_item_count, 50)
        self.assertIsNone(migrated_cycle.default_questions_required_per_faculty)
        self.assertEqual((matched.final_item_count_source, matched.questions_required_per_faculty_source), ("DEFAULT", "OVERRIDE"))
        self.assertEqual(overridden.final_item_count_source, "OVERRIDE")

    def test_invalid_never_opened_draft_values_are_cleared_and_revisioned(self):
        fixture = self._legacy_configuration(quota=49, final_count=76)
        configuration = fixture["configuration"]
        apps = self._forward()
        Configuration = apps.get_model("departmental_exams", "CourseExamConfiguration")
        migrated = Configuration.objects.get(pk=configuration.pk)
        self.assertIsNone(migrated.questions_required_per_faculty)
        self.assertIsNone(migrated.questions_required_per_faculty_source)
        self.assertIsNone(migrated.final_item_count)
        self.assertIsNone(migrated.final_item_count_source)
        self.assertEqual(migrated.revision, 8)

    def test_invalid_immutable_open_closed_or_ever_opened_row_fails_preflight(self):
        for workflow, opened in (("OPEN", False), ("CLOSED", False), ("DRAFT", True)):
            fixture = self._legacy_configuration(quota=49, final_count=50, workflow=workflow, opened=opened)
            try:
                with self.assertRaisesRegex(RuntimeError, "immutable configuration"):
                    self._forward()
            finally:
                self._cleanup_failed_preflight_fixture(fixture)

    def test_constraints_and_reverse_preserve_effective_values_but_lose_provenance(self):
        fixture = self._legacy_configuration(fixed=50, final_count=50, quota=60)
        cycle = fixture["cycle"]
        configuration = fixture["configuration"]
        apps = self._forward()
        Cycle = apps.get_model("departmental_exams", "ExaminationCycle")
        Configuration = apps.get_model("departmental_exams", "CourseExamConfiguration")
        with self.assertRaises(IntegrityError):
            Cycle.objects.filter(pk=cycle.pk).update(default_final_item_count=76)
        with self.assertRaises(IntegrityError):
            Configuration.objects.filter(pk=configuration.pk).update(final_item_count=49)
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.from_state)
        old_apps = self.executor.loader.project_state(self.from_state).apps
        OldCycle = old_apps.get_model("departmental_exams", "ExaminationCycle")
        OldConfiguration = old_apps.get_model("departmental_exams", "CourseExamConfiguration")
        self.assertEqual(OldCycle.objects.get(pk=cycle.pk).item_count_mode, "PER_COURSE")
        self.assertIsNone(OldCycle.objects.get(pk=cycle.pk).fixed_final_item_count)
        self.assertEqual((OldConfiguration.objects.get(pk=configuration.pk).questions_required_per_faculty, OldConfiguration.objects.get(pk=configuration.pk).final_item_count), (60, 50))

    def test_value_source_constraints_reject_one_sided_nulls_and_bad_ranges(self):
        fixture = self._legacy_configuration(fixed=50, final_count=50, quota=50)
        configuration = fixture["configuration"]
        apps = self._forward()
        Configuration = apps.get_model("departmental_exams", "CourseExamConfiguration")
        cases = (
            (None, None, True),
            (50, "DEFAULT", True),
            (50, "OVERRIDE", True),
            (None, "DEFAULT", False),
            (50, None, False),
            (49, "DEFAULT", False),
            (50, "DEFAULT", True),
            (75, "OVERRIDE", True),
            (76, "DEFAULT", False),
        )
        for value_field, source_field in (
            ("questions_required_per_faculty", "questions_required_per_faculty_source"),
            ("final_item_count", "final_item_count_source"),
        ):
            for value, source, accepted in cases:
                with self.subTest(value_field=value_field, value=value, source=source):
                    if accepted:
                        Configuration.objects.filter(pk=configuration.pk).update(
                            **{value_field: value, source_field: source}
                        )
                    else:
                        with self.assertRaises(IntegrityError):
                            Configuration.objects.filter(pk=configuration.pk).update(
                                **{value_field: value, source_field: source}
                            )

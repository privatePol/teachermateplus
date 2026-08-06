import hashlib
from importlib import import_module

from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class Stage5MigrationTests(TransactionTestCase):
    migrate_from = ("departmental_exams", "0004_stage41_default_contribution_deadline")
    nullable_target = ("departmental_exams", "0005_stage5_nullable_schema")
    migrate_to = ("departmental_exams", "0006_stage5_backfill_constraints")

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.from_state = self._state(self.migrate_from)
        self.to_state = self._state(self.migrate_to)
        self.executor.migrate(self.from_state)
        self.old_apps = self.executor.loader.project_state(self.from_state).apps
        self.fixture = self._legacy_fixture()

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

    def _legacy_fixture(self):
        Tenant = self.old_apps.get_model("tenants", "Tenant")
        Campus = self.old_apps.get_model("tenants", "Campus")
        Department = self.old_apps.get_model("tenants", "Department")
        Program = self.old_apps.get_model("tenants", "Program")
        Year = self.old_apps.get_model("academics", "AcademicYear")
        Term = self.old_apps.get_model("academics", "Term")
        Course = self.old_apps.get_model("academics", "Course")
        Section = self.old_apps.get_model("academics", "Section")
        Offering = self.old_apps.get_model("academics", "CourseOffering")
        Assignment = self.old_apps.get_model("academics", "FacultyAssignment")
        User = self.old_apps.get_model("accounts", "User")
        Cycle = self.old_apps.get_model("departmental_exams", "ExaminationCycle")
        CycleCourse = self.old_apps.get_model("departmental_exams", "CycleCourse")
        Snapshot = self.old_apps.get_model("departmental_exams", "CycleCourseOffering")
        Configuration = self.old_apps.get_model("departmental_exams", "CourseExamConfiguration")
        Contribution = self.old_apps.get_model("departmental_exams", "FacultyContribution")
        Question = self.old_apps.get_model("departmental_exams", "Question")
        token = hashlib.sha256(self._testMethodName.encode()).hexdigest()[:10].upper()
        tenant = Tenant.objects.create(code=f"S5{token}", name=token)
        campus = Campus.objects.create(tenant=tenant, code=token, name=token)
        department = Department.objects.create(tenant=tenant, campus=campus, code=token, name=token)
        program = Program.objects.create(tenant=tenant, campus=campus, department=department, code=token, name=token)
        user = User.objects.create(
            username=f"u{token}", email=f"{token}@example.edu", password="",
            first_name="", last_name="", is_active=True, is_staff=False,
            is_superuser=False, faculty_quick_tour_disabled=False,
        )
        year = Year.objects.create(tenant=tenant, code=token, name=token, start_date="2026-06-01", end_date="2027-05-31")
        term = Term.objects.create(tenant=tenant, academic_year=year, code=token, name=token)
        course = Course.objects.create(tenant=tenant, code=token, title=token, exam_department=department)
        section = Section.objects.create(tenant=tenant, campus=campus, department=department, program=program, code=token, name=token)
        offering = Offering.objects.create(
            tenant=tenant, campus=campus, department=department, program=program,
            academic_year=year, term=term, course=course, section=section,
            status="OPEN", is_active=True,
        )
        assignment = Assignment.objects.create(
            tenant=tenant, campus=campus, offering=offering, faculty_user=user,
            response_status="ACCEPTED", accepted_at=timezone.now(),
            is_active=True,
        )
        cycle = Cycle.objects.create(
            tenant=tenant, academic_year=year, term=term, exam_period="MIDTERM",
            status="OPEN", default_final_item_count=50,
            default_questions_required_per_faculty=50, created_by=user,
        )
        parent = CycleCourse.objects.create(cycle=cycle, course=course, responsible_department=department)
        snapshot = Snapshot.objects.create(cycle_course=parent, offering=offering, campus=campus)
        configuration = Configuration.objects.create(
            cycle_course=parent,
            final_item_count=50,
            final_item_count_source="OVERRIDE",
            questions_required_per_faculty=50,
            questions_required_per_faculty_source="OVERRIDE",
            cycle_defaults_revision_snapshot=0,
            contribution_deadline=timezone.now() + timezone.timedelta(days=5),
            contribution_deadline_source="OVERRIDE",
            workflow_status="OPEN",
            opened_at=timezone.now(),
            opened_by=user,
            revision=7,
        )
        contribution = Contribution.objects.create(
            cycle_course=parent,
            faculty_user=user,
            source_assignment=assignment,
            source_campus=campus,
            status="DRAFT",
        )
        question = Question.objects.create(
            contribution=contribution,
            question_text="Legacy question",
            choice_a="A", choice_b="B", choice_c="C", choice_d="D",
            correct_answer="A", difficulty="EASY",
        )
        return {
            "tenant": tenant,
            "campus": campus,
            "department": department,
            "program": program,
            "user": user,
            "year": year,
            "term": term,
            "course": course,
            "section": section,
            "offering": offering,
            "assignment": assignment,
            "cycle": cycle,
            "parent": parent,
            "snapshot": snapshot,
            "configuration": configuration,
            "contribution": contribution,
            "question": question,
        }

    def _forward(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.to_state)
        return self.executor.loader.project_state(self.to_state).apps

    def _delete_fixture_after_failed_preflight(self):
        for key in (
            "question", "contribution", "configuration", "assignment", "snapshot", "parent",
            "offering", "section", "course", "cycle", "term", "year", "user",
            "program", "department", "campus", "tenant",
        ):
            try:
                self.fixture[key].delete()
            except Exception:
                pass

    def test_nullable_then_backfill_constraint_migrations_are_ordered_and_mariadb_safe(self):
        nullable = import_module("apps.departmental_exams.migrations.0005_stage5_nullable_schema")
        final = import_module("apps.departmental_exams.migrations.0006_stage5_backfill_constraints")
        self.assertEqual(final.Migration.dependencies, [self.nullable_target])
        self.assertEqual(final.Migration.operations[0].__class__.__name__, "RunPython")
        names = [
            operation.constraint.name
            for operation in final.Migration.operations
            if getattr(operation, "constraint", None)
        ]
        self.assertTrue(names)
        self.assertTrue(all(len(name) <= 64 for name in names))
        self.assertNotIn("RunPython", [operation.__class__.__name__ for operation in nullable.Migration.operations])

    def test_deterministic_backfill_populates_quota_revision_source_and_position(self):
        apps = self._forward()
        Contribution = apps.get_model("departmental_exams", "FacultyContribution")
        Source = apps.get_model("departmental_exams", "FacultyContributionEligibilitySource")
        Question = apps.get_model("departmental_exams", "Question")
        contribution = Contribution.objects.get(pk=self.fixture["contribution"].pk)
        question = Question.objects.get(pk=self.fixture["question"].pk)
        source = Source.objects.get(contribution=contribution)
        self.assertEqual((contribution.quota_snapshot, contribution.configuration_revision_snapshot), (50, 7))
        self.assertEqual((contribution.revision, contribution.roster_status), (1, "ACTIVE"))
        self.assertEqual(source.assignment_id_snapshot, self.fixture["assignment"].pk)
        self.assertTrue(source.is_current)
        self.assertEqual((question.position, question.revision, question.entry_method), (1, 1, "MANUAL"))

    def test_invalid_missing_quota_fails_preflight_without_question_content(self):
        Configuration = self.old_apps.get_model("departmental_exams", "CourseExamConfiguration")
        Configuration.objects.filter(pk=self.fixture["configuration"].pk).update(
            questions_required_per_faculty=None,
            questions_required_per_faculty_source=None,
        )
        try:
            with self.assertRaisesRegex(RuntimeError, "no deterministic course quota"):
                self._forward()
        finally:
            self._delete_fixture_after_failed_preflight()

    def test_inconsistent_status_timestamp_and_invalid_question_fail_safely(self):
        Contribution = self.old_apps.get_model("departmental_exams", "FacultyContribution")
        Contribution.objects.filter(pk=self.fixture["contribution"].pk).update(status="SUBMITTED", submitted_at=None)
        try:
            with self.assertRaisesRegex(RuntimeError, "no submitted_at"):
                self._forward()
        finally:
            self._delete_fixture_after_failed_preflight()

    def test_invalid_legacy_question_fails_without_logging_content(self):
        Question = self.old_apps.get_model("departmental_exams", "Question")
        Question.objects.filter(pk=self.fixture["question"].pk).update(question_text="")
        try:
            with self.assertRaisesRegex(RuntimeError, "invalid question text") as captured:
                self._forward()
            self.assertNotIn("Legacy question", str(captured.exception))
        finally:
            self._delete_fixture_after_failed_preflight()

    def test_final_constraints_and_safe_reverse(self):
        apps = self._forward()
        Contribution = apps.get_model("departmental_exams", "FacultyContribution")
        Question = apps.get_model("departmental_exams", "Question")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Contribution.objects.filter(pk=self.fixture["contribution"].pk).update(quota_snapshot=49)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Question.objects.filter(pk=self.fixture["question"].pk).update(position=0)
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.from_state)
        old_apps = self.executor.loader.project_state(self.from_state).apps
        OldContribution = old_apps.get_model("departmental_exams", "FacultyContribution")
        self.assertEqual(OldContribution.objects.get(pk=self.fixture["contribution"].pk).source_assignment_id, self.fixture["assignment"].pk)

    def test_reverse_refuses_meaningful_stage5_activity(self):
        apps = self._forward()
        Contribution = apps.get_model("departmental_exams", "FacultyContribution")
        Contribution.objects.filter(pk=self.fixture["contribution"].pk).update(revision=2)
        reverse_target = self._state(self.nullable_target)
        with self.assertRaisesRegex(RuntimeError, "mutations prevent safe reversal"):
            MigrationExecutor(connection).migrate(reverse_target)
        Contribution.objects.filter(pk=self.fixture["contribution"].pk).update(revision=1)

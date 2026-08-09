import hashlib
from importlib import import_module

from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class Stage6MigrationTests(TransactionTestCase):
    migrate_from = ("departmental_exams", "0006_stage5_backfill_constraints")
    foundation = ("departmental_exams", "0007_stage6_blueprint_resolution_foundation")
    migrate_to = ("departmental_exams", "0008_stage6_blueprint_constraints")

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.from_state = self._state(self.migrate_from)
        self.to_state = self._state(self.migrate_to)
        self.executor.migrate(self.from_state)
        self.old_apps = self.executor.loader.project_state(self.from_state).apps
        self.fixture = self._fixture()

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def _state(self, departmental_target):
        return [
            departmental_target if app_label == "departmental_exams" else node
            for node in self.executor.loader.graph.leaf_nodes()
            for app_label, _name in [node]
        ]

    def _fixture(self):
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
        Contribution = self.old_apps.get_model("departmental_exams", "FacultyContribution")
        Question = self.old_apps.get_model("departmental_exams", "Question")
        token = hashlib.sha256(self._testMethodName.encode()).hexdigest()[:10].upper()
        tenant = Tenant.objects.create(code=f"S6{token}", name=token)
        campus = Campus.objects.create(tenant=tenant, code="CUBAO", name="Cubao")
        department = Department.objects.create(tenant=tenant, campus=campus, code=token, name=token)
        year = Year.objects.create(tenant=tenant, code=token, name=token, start_date="2026-06-01", end_date="2027-05-31")
        term = Term.objects.create(tenant=tenant, academic_year=year, code=token, name=token)
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
        course = Course.objects.create(tenant=tenant, code=token, title=token, exam_department=department)
        cycle = Cycle.objects.create(
            tenant=tenant,
            academic_year=year,
            term=term,
            exam_period="MIDTERM",
            status="OPEN",
            default_final_item_count=50,
            default_questions_required_per_faculty=50,
            created_by=user,
        )
        parent = CycleCourse.objects.create(cycle=cycle, course=course, responsible_department=department)
        configuration = Configuration.objects.create(
            cycle_course=parent,
            final_item_count=50,
            final_item_count_source="OVERRIDE",
            questions_required_per_faculty=50,
            questions_required_per_faculty_source="OVERRIDE",
            cycle_defaults_revision_snapshot=0,
            contribution_deadline=timezone.now() + timezone.timedelta(days=5),
            contribution_deadline_source="OVERRIDE",
            workflow_status="CLOSED",
            opened_at=timezone.now(),
            opened_by=user,
            closed_at=timezone.now(),
            closed_by=user,
            revision=3,
            contributor_roster_initialized_at=timezone.now(),
            contributor_roster_initialized_by=user,
            contributor_roster_revision=2,
        )
        contribution = Contribution.objects.create(
            cycle_course=parent,
            faculty_user=user,
            source_campus=campus,
            quota_snapshot=50,
            configuration_revision_snapshot=2,
            revision=4,
            roster_status="BLOCKED",
            roster_blocked_at=timezone.now(),
            status="DRAFT",
        )
        question = Question.objects.create(
            contribution=contribution,
            question_text="Historical Draft content remains untouched",
            choice_a="A",
            choice_b="B",
            choice_c="C",
            choice_d="D",
            correct_answer="A",
            difficulty="EASY",
            position=1,
            revision=1,
            entry_method="MANUAL",
        )
        return {
            "tenant": tenant,
            "user": user,
            "parent": parent,
            "contribution": contribution,
            "question": question,
            "configuration": configuration,
        }

    def _forward(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.to_state)
        return self.executor.loader.project_state(self.to_state).apps

    def test_foundation_then_constraints_are_ordered_and_mariadb_names_are_bounded(self):
        foundation = import_module(
            "apps.departmental_exams.migrations.0007_stage6_blueprint_resolution_foundation"
        )
        constraints = import_module(
            "apps.departmental_exams.migrations.0008_stage6_blueprint_constraints"
        )
        self.assertIn(self.migrate_from, foundation.Migration.dependencies)
        self.assertEqual(constraints.Migration.dependencies, [self.foundation])
        self.assertNotIn("RunPython", [operation.__class__.__name__ for operation in foundation.Migration.operations])
        self.assertNotIn("RunPython", [operation.__class__.__name__ for operation in constraints.Migration.operations])
        names = [
            getattr(getattr(operation, "constraint", None), "name", None)
            or getattr(getattr(operation, "index", None), "name", None)
            for operation in constraints.Migration.operations
        ]
        self.assertTrue(names)
        self.assertTrue(all(name and len(name) <= 64 for name in names))

    def test_forward_creates_empty_stage6_overlays_without_backfill_or_question_change(self):
        apps = self._forward()
        Blueprint = apps.get_model("departmental_exams", "ExamBlueprint")
        Resolution = apps.get_model("departmental_exams", "BlockedContributionResolution")
        Placement = apps.get_model("departmental_exams", "QuestionBlueprintPlacement")
        Scenario = apps.get_model("departmental_exams", "ExamScenario")
        Question = apps.get_model("departmental_exams", "Question")
        self.assertEqual(Blueprint.objects.count(), 0)
        self.assertEqual(Resolution.objects.count(), 0)
        self.assertEqual(Placement.objects.count(), 0)
        self.assertEqual(Scenario.objects.count(), 0)
        question = Question.objects.get(pk=self.fixture["question"].pk)
        self.assertEqual(question.question_text, "Historical Draft content remains untouched")
        self.assertEqual(question.revision, 1)

    def test_final_revision_constraint_is_enforced(self):
        apps = self._forward()
        Blueprint = apps.get_model("departmental_exams", "ExamBlueprint")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Blueprint.objects.create(
                cycle_course_id=self.fixture["parent"].pk,
                mode="NO_SECTIONS",
                revision=0,
                created_by_id=self.fixture["user"].pk,
                updated_by_id=self.fixture["user"].pk,
            )

    def test_resolution_uniqueness_is_per_blocked_revision_state(self):
        apps = self._forward()
        Resolution = apps.get_model("departmental_exams", "BlockedContributionResolution")
        contribution = self.fixture["contribution"]
        common = {
            "tenant_id": self.fixture["tenant"].pk,
            "cycle_course_id": self.fixture["parent"].pk,
            "contribution_id": contribution.pk,
            "resolved_by_id": self.fixture["user"].pk,
            "resolved_at": timezone.now(),
            "roster_revision_snapshot": 2,
            "blocked_at_snapshot": contribution.roster_blocked_at,
            "source_evidence_sha256": "a" * 64,
        }
        Resolution.objects.create(
            **common,
            reason="Resolve the first exact blocked evidence state.",
            contribution_revision_snapshot=4,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            Resolution.objects.create(
                **common,
                reason="Duplicate the same exact blocked evidence state.",
                contribution_revision_snapshot=4,
            )
        Resolution.objects.create(
            **common,
            reason="Resolve a newer state in the same blocked episode.",
            contribution_revision_snapshot=5,
        )
        self.assertEqual(Resolution.objects.count(), 2)

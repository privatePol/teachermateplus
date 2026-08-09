import hashlib
from importlib import import_module

from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class Stage6CApprovalLockMigrationTests(TransactionTestCase):
    migrate_from = ("departmental_exams", "0009_stage6b_generation_output")
    migrate_to = ("departmental_exams", "0010_stage6c_approve_lock")

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
        Revision = self.old_apps.get_model(
            "departmental_exams", "ExamGenerationRevision"
        )
        token = hashlib.sha256(self._testMethodName.encode()).hexdigest()[:10].upper()
        tenant = Tenant.objects.create(code=f"S6C{token}", name=token)
        campus = Campus.objects.create(tenant=tenant, code="CUBAO", name="Cubao")
        department = Department.objects.create(
            tenant=tenant, campus=campus, code=token, name=token
        )
        year = Year.objects.create(
            tenant=tenant,
            code=token,
            name=token,
            start_date="2026-06-01",
            end_date="2027-05-31",
        )
        term = Term.objects.create(
            tenant=tenant, academic_year=year, code=token, name=token
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
        course = Course.objects.create(
            tenant=tenant, code=token, title=token, exam_department=department
        )
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
        parent = CycleCourse.objects.create(
            cycle=cycle,
            course=course,
            responsible_department=department,
            reviewer=user,
        )
        common = {
            "cycle_course": parent,
            "source_input_fingerprint": "a" * 64,
            "algorithm_version": "stage6b-v1",
            "generated_at": timezone.now(),
            "generated_by": user,
            "configuration_revision_snapshot": 3,
            "blueprint_revision_snapshot": 2,
            "roster_boundary_snapshot": "b" * 64,
            "final_item_count_snapshot": 50,
            "minimum_overlap": 0,
            "proportional_score": 0,
            "contributors_represented": 3,
            "squared_contributor_concentration": 3400,
        }
        historical = Revision.objects.create(
            **common,
            revision_number=1,
            status="SUPERSEDED",
            current_marker=None,
            request_token_digest="c" * 64,
        )
        current = Revision.objects.create(
            **common,
            revision_number=2,
            status="GENERATED",
            current_marker=1,
            request_token_digest="d" * 64,
            supersedes=historical,
            regeneration_reason="Historical reviewer-only reason.",
        )
        return {"user": user, "historical": historical, "current": current}

    def _forward(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.to_state)
        return self.executor.loader.project_state(self.to_state).apps

    def test_0010_is_additive_has_no_data_operation_or_new_index(self):
        migration = import_module(
            "apps.departmental_exams.migrations.0010_stage6c_approve_lock"
        )
        self.assertIn(self.migrate_from, migration.Migration.dependencies)
        operation_names = [
            operation.__class__.__name__ for operation in migration.Migration.operations
        ]
        self.assertNotIn("RunPython", operation_names)
        self.assertNotIn("AddIndex", operation_names)
        self.assertEqual(operation_names.count("AddField"), 3)
        self.assertEqual(operation_names.count("AlterField"), 1)
        self.assertEqual(operation_names.count("RemoveConstraint"), 1)
        self.assertEqual(operation_names.count("AddConstraint"), 1)

    def test_existing_generated_and_superseded_rows_survive_with_empty_lock_metadata(self):
        apps = self._forward()
        Revision = apps.get_model(
            "departmental_exams", "ExamGenerationRevision"
        )
        historical = Revision.objects.get(pk=self.fixture["historical"].pk)
        current = Revision.objects.get(pk=self.fixture["current"].pk)
        self.assertEqual((historical.status, historical.current_marker), ("SUPERSEDED", None))
        self.assertEqual((current.status, current.current_marker), ("GENERATED", 1))
        for revision in (historical, current):
            self.assertIsNone(revision.locked_at)
            self.assertIsNone(revision.locked_by_id)
            self.assertEqual(revision.approval_attestation_version, "")

    def test_invalid_generated_superseded_and_locked_states_are_rejected(self):
        apps = self._forward()
        Revision = apps.get_model(
            "departmental_exams", "ExamGenerationRevision"
        )
        historical = Revision.objects.get(pk=self.fixture["historical"].pk)
        current = Revision.objects.get(pk=self.fixture["current"].pk)
        locked_at = timezone.now()
        invalid_updates = (
            (
                "LOCKED with NULL current_marker",
                current,
                {
                    "status": "LOCKED",
                    "current_marker": None,
                    "locked_at": locked_at,
                    "locked_by_id": self.fixture["user"].pk,
                    "approval_attestation_version": "stage6c-v1",
                },
            ),
            (
                "LOCKED without locked_by",
                current,
                {
                    "status": "LOCKED",
                    "locked_at": locked_at,
                    "locked_by_id": None,
                    "approval_attestation_version": "stage6c-v1",
                },
            ),
            (
                "LOCKED without locked_at",
                current,
                {
                    "status": "LOCKED",
                    "locked_at": None,
                    "locked_by_id": self.fixture["user"].pk,
                    "approval_attestation_version": "stage6c-v1",
                },
            ),
            (
                "LOCKED with blank approval attestation",
                current,
                {
                    "status": "LOCKED",
                    "locked_at": locked_at,
                    "locked_by_id": self.fixture["user"].pk,
                    "approval_attestation_version": "",
                },
            ),
            (
                "GENERATED with lock metadata",
                current,
                {
                    "status": "GENERATED",
                    "locked_at": locked_at,
                    "locked_by_id": self.fixture["user"].pk,
                    "approval_attestation_version": "stage6c-v1",
                },
            ),
            (
                "SUPERSEDED with lock metadata",
                historical,
                {
                    "status": "SUPERSEDED",
                    "locked_at": locked_at,
                    "locked_by_id": self.fixture["user"].pk,
                    "approval_attestation_version": "stage6c-v1",
                },
            ),
            (
                "SUPERSEDED with current_marker=1",
                current,
                {
                    "status": "SUPERSEDED",
                    "current_marker": 1,
                },
            ),
        )
        for label, revision, values in invalid_updates:
            with self.subTest(state=label):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    Revision.objects.filter(pk=revision.pk).update(**values)

    def test_multiple_current_revisions_for_one_cycle_course_are_rejected(self):
        apps = self._forward()
        Revision = apps.get_model(
            "departmental_exams", "ExamGenerationRevision"
        )
        current = Revision.objects.get(pk=self.fixture["current"].pk)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Revision.objects.create(
                cycle_course_id=current.cycle_course_id,
                revision_number=3,
                status="GENERATED",
                current_marker=1,
                source_input_fingerprint="e" * 64,
                algorithm_version=current.algorithm_version,
                generated_at=timezone.now(),
                generated_by_id=self.fixture["user"].pk,
                configuration_revision_snapshot=(
                    current.configuration_revision_snapshot
                ),
                blueprint_revision_snapshot=current.blueprint_revision_snapshot,
                roster_boundary_snapshot=current.roster_boundary_snapshot,
                final_item_count_snapshot=current.final_item_count_snapshot,
                request_token_digest="f" * 64,
                minimum_overlap=current.minimum_overlap,
                proportional_score=current.proportional_score,
                contributors_represented=current.contributors_represented,
                squared_contributor_concentration=(
                    current.squared_contributor_concentration
                ),
            )

    def test_locked_current_with_complete_metadata_is_valid(self):
        apps = self._forward()
        Revision = apps.get_model(
            "departmental_exams", "ExamGenerationRevision"
        )
        current = Revision.objects.get(pk=self.fixture["current"].pk)

        locked_at = timezone.now()
        Revision.objects.filter(pk=current.pk).update(
            status="LOCKED",
            locked_at=locked_at,
            locked_by_id=self.fixture["user"].pk,
            approval_attestation_version="stage6c-v1",
        )
        current.refresh_from_db()
        self.assertEqual(current.status, "LOCKED")
        self.assertEqual(current.current_marker, 1)
        self.assertEqual(current.locked_by_id, self.fixture["user"].pk)
        self.assertEqual(current.approval_attestation_version, "stage6c-v1")

import csv
import io
from importlib import import_module
from types import SimpleNamespace

from django.apps import apps as django_apps
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder
from django.http import Http404
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import CourseOffering
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.rbac.models import Permission, UserPermission

from .automatic_workflow import AutomaticContributionReopenService
from .contribution_authorization import (
    ContributionAuthorizationService,
    ContributionQuotaReached,
)
from .contribution_services import ContributionRosterService, QuestionMutationService
from .csv_import import CSV_HEADERS, QuestionCSVImportService
from .docx_import import QuestionDOCXImportService
from .exam_units import ExamCourseEquivalencyService
from .models import (
    CourseExamConfiguration,
    ExamGenerationRevision,
    ExaminationCycle,
    FacultyContribution,
    GeneratedExamSet,
    Question,
    QuestionImportBatch,
)
from .stage4_test_support import Stage4TestCase
from .tests_docx_import import make_docx
from .tests_stage5_contributions import Stage5FixtureMixin


class ReopenedDraftAccessTests(Stage5FixtureMixin, Stage4TestCase):
    def setUp(self):
        super().setUp()
        self.parent, self.configuration = self.make_stage5_course()
        self.faculty = self.make_faculty("reopened-draft-faculty")
        self.assignment = self.make_assignment(self.parent, self.faculty)
        self.initialize(self.parent)
        self.contribution = FacultyContribution.objects.get(
            cycle_course=self.parent,
            faculty_user=self.faculty,
        )
        self.generation_manager = self.make_user(
            "reopened-draft-manager",
            self.department,
            ("departmental_exams.manage_exam_generation",),
        )
        self.parent.cycle.processing_mode = (
            ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        )
        self.parent.cycle.save(update_fields=["processing_mode", "updated_at"])
        SystemSettingService.set(
            FeatureSettingsService.DEPARTMENTAL_EXAM_DOCX_IMPORT_ENABLED_KEY,
            True,
            tenant_id=self.tenant.id,
            value_type="BOOL",
            is_active=True,
        )
        self.client.force_login(self.faculty)

    def _refresh(self):
        self.parent.refresh_from_db()
        self.configuration.refresh_from_db()
        self.contribution.refresh_from_db()

    def _make_questions(self, count):
        start = self.contribution.questions.count() + 1
        Question.objects.bulk_create(
            [
                Question(
                    contribution=self.contribution,
                    question_text=f"Reopened question {position}",
                    choice_a="Alpha",
                    choice_b="Beta",
                    choice_c="Gamma",
                    choice_d="Delta",
                    correct_answer="A",
                    difficulty=self.difficulty_for_position(position, 50),
                    position=position,
                )
                for position in range(start, start + count)
            ]
        )

    @staticmethod
    def _payload(text):
        return {
            "question_text": text,
            "choice_a": "Alpha",
            "choice_b": "Beta",
            "choice_c": "Gamma",
            "choice_d": "Delta",
            "correct_answer": "A",
            "difficulty": "EASY",
        }

    @staticmethod
    def _csv_upload(text="Reopened CSV question"):
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(CSV_HEADERS)
        writer.writerow((text, "Alpha", "Beta", "Gamma", "Delta", "A", "EASY"))
        return SimpleUploadedFile(
            "questions.csv",
            stream.getvalue().encode("utf-8"),
            content_type="text/csv",
        )

    @staticmethod
    def _docx_upload(text="Reopened Word question"):
        return make_docx(
            [
                f"1. {text}",
                "A. Alpha",
                "B. Beta",
                "C. Gamma",
                "D. Delta",
                "Answer: A",
                "Difficulty: Easy",
            ]
        )

    def _revision(self, *, course=None, number=1, token="a"):
        return ExamGenerationRevision.objects.create(
            cycle_course=course or self.parent,
            revision_number=number,
            source_input_fingerprint=token * 64,
            algorithm_version="reopened-draft-test-v1",
            generation_trigger=ExamGenerationRevision.GenerationTrigger.AUTOMATIC,
            configuration_revision_snapshot=1,
            blueprint_revision_snapshot=1,
            roster_boundary_snapshot="b" * 64,
            final_item_count_snapshot=50,
            request_token_digest=token * 64,
            minimum_overlap=0,
            proportional_score=0,
            contributors_represented=1,
            squared_contributor_concentration=1,
        )

    def _invalidate_live_assignment(self):
        self.assignment.is_active = False
        self.assignment.save(update_fields=["is_active", "updated_at"])
        offering = self.assignment.offering
        offering.is_active = False
        offering.status = CourseOffering.Status.CLOSED
        offering.save(update_fields=["is_active", "status", "updated_at"])

    def _add_never_eligible_other_campus_source(self):
        portal_permission = Permission.objects.get(code="faculty_portal.access")
        UserPermission.objects.create(
            user=self.faculty,
            permission=portal_permission,
            grant_type=UserPermission.GrantType.ALLOW,
            tenant=self.tenant,
            campus=self.other_campus,
        )
        UserPermission.objects.create(
            user=self.generation_manager,
            permission=Permission.objects.get(
                code="departmental_exams.manage_exam_generation"
            ),
            grant_type=UserPermission.GrantType.ALLOW,
            tenant=self.tenant,
            campus=self.other_campus,
        )
        offering = self.add_grouped_offering(
            self.parent,
            campus=self.other_campus,
            department=self.other_department,
            slug="REOPEN-NEVER-ELIGIBLE",
        )
        assignment = self.make_assignment(
            self.parent,
            self.faculty,
            campus=self.other_campus,
            offering=offering,
        )
        assignment.is_active = False
        assignment.save(update_fields=["is_active", "updated_at"])
        ContributionRosterService.synchronize(
            cycle_course_id=self.parent.id,
            tenant_id=self.tenant.id,
            actor=self.generation_manager,
        )
        source = self.contribution.eligibility_sources.get(
            assignment_id_snapshot=assignment.id
        )
        return assignment, source

    def _authorized_reopen(self, *, deadline=None, expect_blocked=True):
        self._invalidate_live_assignment()
        CourseExamConfiguration.objects.filter(pk=self.configuration.pk).update(
            workflow_status=CourseExamConfiguration.WorkflowStatus.CLOSED,
            closed_at=timezone.now(),
            closed_by=self.generation_manager,
        )
        self.configuration.refresh_from_db()
        reopened = AutomaticContributionReopenService.reopen(
            cycle_course_id=self.parent.id,
            tenant_id=self.tenant.id,
            actor=self.generation_manager,
            expected_revision=self.configuration.revision,
            new_deadline=deadline or timezone.now() + timezone.timedelta(days=1),
        )
        self._refresh()
        if expect_blocked:
            self.assertEqual(
                self.contribution.roster_status,
                FacultyContribution.RosterStatus.BLOCKED,
            )
            self.assertFalse(
                self.contribution.eligibility_sources.filter(is_current=True).exists()
            )
        return reopened

    def _create_via_service(self, text="Created during Reopen"):
        self.contribution.refresh_from_db()
        return QuestionMutationService.create(
            contribution_id=self.contribution.id,
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            expected_contribution_revision=self.contribution.revision,
            payload=self._payload(text),
        )

    @staticmethod
    def _proof_migration():
        return import_module(
            "apps.departmental_exams.migrations.0023_faculty_source_eligibility_proof"
        )

    def _source_history_baseline(self):
        migration = self._proof_migration()
        return MigrationRecorder(connection).migration_qs.get(
            app="departmental_exams",
            name=migration.SOURCE_HISTORY_BASELINE_MIGRATION,
        ).applied

    def _run_safe_legacy_proof_backfill(self):
        self._proof_migration().backfill_safe_legacy_eligibility_proof(
            django_apps,
            SimpleNamespace(connection=connection),
        )

    def test_a_reopened_zero_draft_lists_truthful_state_and_manual_adds(self):
        source = self.contribution.eligibility_sources.get()
        eligibility_proven_at = source.eligibility_proven_at
        self.assertIsNotNone(eligibility_proven_at)
        self._authorized_reopen()
        source.refresh_from_db()
        self.assertFalse(source.is_current)
        self.assertEqual(source.eligibility_proven_at, eligibility_proven_at)

        listing = self.client.get(reverse("departmental_exams:contribution_list"))
        workspace = self.client.get(
            reverse(
                "departmental_exams:contribution_workspace",
                args=[self.contribution.id],
            )
        )

        self.assertContains(listing, "Reopened Draft")
        self.assertNotContains(listing, "Blocked Draft")
        self.assertEqual(workspace.status_code, 200)
        self.assertTrue(workspace.context["is_mutable"])
        self.assertTrue(workspace.context["reopened_draft"])
        self.assertContains(workspace, "Reopened Draft")
        self.assertContains(workspace, ">Add question<")

        response = self.client.post(
            reverse(
                "departmental_exams:question_create",
                args=[self.contribution.id],
            ),
            {
                "expected_contribution_revision": self.contribution.revision,
                **self._payload("Manual reopened question"),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Question.objects.filter(
                contribution=self.contribution,
                question_text="Manual reopened question",
            ).exists()
        )

    def test_b_reopened_partial_draft_allows_add_edit_delete_and_reorder(self):
        self._make_questions(3)
        self._authorized_reopen()

        created = self._create_via_service()
        self.contribution.refresh_from_db()
        first = self.contribution.questions.order_by("position").first()
        updated, changed = QuestionMutationService.update(
            contribution_id=self.contribution.id,
            question_id=first.id,
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            expected_contribution_revision=self.contribution.revision,
            expected_question_revision=first.revision,
            payload=self._payload("Edited during Reopen"),
        )
        self.assertTrue(changed)
        self.assertEqual(updated.question_text, "Edited during Reopen")

        self.contribution.refresh_from_db()
        ordered_ids = list(
            self.contribution.questions.order_by("-position").values_list("id", flat=True)
        )
        self.assertTrue(
            QuestionMutationService.reorder(
                contribution_id=self.contribution.id,
                ordered_question_ids=ordered_ids,
                user=self.faculty,
                tenant_id=self.tenant.id,
                campus_id=self.campus.id,
                expected_contribution_revision=self.contribution.revision,
            )
        )

        self.contribution.refresh_from_db()
        created.refresh_from_db()
        QuestionMutationService.delete(
            contribution_id=self.contribution.id,
            question_id=created.id,
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            expected_contribution_revision=self.contribution.revision,
            expected_question_revision=created.revision,
        )
        self.assertFalse(Question.objects.filter(pk=created.id).exists())

    def test_c_reopened_full_draft_allows_edit_delete_but_keeps_quota(self):
        self._make_questions(50)
        self._authorized_reopen()

        with self.assertRaises(ContributionQuotaReached):
            self._create_via_service("Over quota")
        first = self.contribution.questions.order_by("position").first()
        updated, changed = QuestionMutationService.update(
            contribution_id=self.contribution.id,
            question_id=first.id,
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            expected_contribution_revision=self.contribution.revision,
            expected_question_revision=first.revision,
            payload=self._payload("Full Draft edit"),
        )
        self.assertTrue(changed)
        self.assertEqual(updated.question_text, "Full Draft edit")
        self.contribution.refresh_from_db()
        updated.refresh_from_db()
        QuestionMutationService.delete(
            contribution_id=self.contribution.id,
            question_id=updated.id,
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            expected_contribution_revision=self.contribution.revision,
            expected_question_revision=updated.revision,
        )
        self.assertEqual(self.contribution.questions.count(), 49)

    def test_d_reopened_csv_preview_and_confirmation_succeed(self):
        self._authorized_reopen()
        batch = QuestionCSVImportService.create_preview(
            contribution_id=self.contribution.id,
            uploaded_file=self._csv_upload(),
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            expected_contribution_revision=self.contribution.revision,
        )
        confirmed, changed = QuestionCSVImportService.confirm(
            token=batch.token,
            expected_file_sha256=batch.file_sha256,
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
        )
        question = self.contribution.questions.get()
        self.assertTrue(changed)
        self.assertEqual(confirmed.status, QuestionImportBatch.Status.CONFIRMED)
        self.assertEqual(question.entry_method, Question.EntryMethod.CSV)

    def test_e_reopened_docx_preview_edit_and_confirmation_preserve_provenance(self):
        self._authorized_reopen()
        batch = QuestionDOCXImportService.create_preview(
            contribution_id=self.contribution.id,
            uploaded_file=self._docx_upload(),
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            expected_contribution_revision=self.contribution.revision,
        )
        batch, row = QuestionDOCXImportService.update_staged_row(
            token=batch.token,
            row_number=2,
            payload=self._payload("Edited reopened Word question"),
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            expected_contribution_revision=self.contribution.revision,
        )
        confirmed, changed = QuestionDOCXImportService.confirm(
            token=batch.token,
            expected_file_sha256=batch.file_sha256,
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
        )
        question = self.contribution.questions.get()
        self.assertEqual(row.errors, [])
        self.assertTrue(changed)
        self.assertEqual(confirmed.source_format, QuestionImportBatch.SourceFormat.DOCX)
        self.assertEqual(question.entry_method, Question.EntryMethod.DOCX)
        self.assertEqual(question.question_text, "Edited reopened Word question")

    def test_f_reopened_full_draft_can_final_submit(self):
        self._make_questions(50)
        self._authorized_reopen()
        submitted, changed = QuestionMutationService.submit(
            contribution_id=self.contribution.id,
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            expected_contribution_revision=self.contribution.revision,
        )
        self.assertTrue(changed)
        self.assertEqual(submitted.status, FacultyContribution.Status.SUBMITTED)
        self.assertIsNotNone(submitted.submitted_at)

    def test_g_submitted_before_reopen_stays_immutable_and_preserved(self):
        self._make_questions(50)
        submitted_at = timezone.now() - timezone.timedelta(hours=1)
        FacultyContribution.objects.filter(pk=self.contribution.pk).update(
            status=FacultyContribution.Status.SUBMITTED,
            submitted_at=submitted_at,
        )
        question_ids = list(
            self.contribution.questions.order_by("id").values_list("id", flat=True)
        )
        self._authorized_reopen(expect_blocked=False)

        with self.assertRaises(PermissionDenied):
            self._create_via_service("Must remain denied")
        self.contribution.refresh_from_db()
        self.assertEqual(self.contribution.status, FacultyContribution.Status.SUBMITTED)
        self.assertEqual(self.contribution.submitted_at, submitted_at)
        self.assertEqual(
            list(
                self.contribution.questions.order_by("id").values_list("id", flat=True)
            ),
            question_ids,
        )

    def test_h_invalid_live_assignment_without_reopen_remains_blocked(self):
        self._invalidate_live_assignment()
        ContributionRosterService.synchronize(
            cycle_course_id=self.parent.id,
            tenant_id=self.tenant.id,
            actor=self.generation_manager,
        )
        self._refresh()

        with self.assertRaises(PermissionDenied):
            self._create_via_service("No reopen")
        workspace = self.client.get(
            reverse(
                "departmental_exams:contribution_workspace",
                args=[self.contribution.id],
            )
        )
        self.assertFalse(workspace.context["is_mutable"])
        self.assertContains(workspace, "This Draft is blocked and read-only")

    def test_i_expired_reopen_is_read_only_and_all_mutation_classes_deny(self):
        self._make_questions(1)
        self._authorized_reopen()
        CourseExamConfiguration.objects.filter(pk=self.configuration.pk).update(
            reopened_contribution_deadline=timezone.now()
            - timezone.timedelta(minutes=1)
        )
        self._refresh()
        question = self.contribution.questions.get()
        workspace = self.client.get(
            reverse(
                "departmental_exams:contribution_workspace",
                args=[self.contribution.id],
            )
        )
        self.assertFalse(workspace.context["is_mutable"])
        self.assertNotContains(workspace, ">Add question<")

        operations = (
            lambda: self._create_via_service("Expired add"),
            lambda: QuestionMutationService.update(
                contribution_id=self.contribution.id,
                question_id=question.id,
                user=self.faculty,
                tenant_id=self.tenant.id,
                campus_id=self.campus.id,
                expected_contribution_revision=self.contribution.revision,
                expected_question_revision=question.revision,
                payload=self._payload("Expired edit"),
            ),
            lambda: QuestionMutationService.delete(
                contribution_id=self.contribution.id,
                question_id=question.id,
                user=self.faculty,
                tenant_id=self.tenant.id,
                campus_id=self.campus.id,
                expected_contribution_revision=self.contribution.revision,
                expected_question_revision=question.revision,
            ),
            lambda: QuestionMutationService.reorder(
                contribution_id=self.contribution.id,
                ordered_question_ids=[question.id],
                user=self.faculty,
                tenant_id=self.tenant.id,
                campus_id=self.campus.id,
                expected_contribution_revision=self.contribution.revision,
            ),
            lambda: QuestionCSVImportService.create_preview(
                contribution_id=self.contribution.id,
                uploaded_file=self._csv_upload("Expired CSV"),
                user=self.faculty,
                tenant_id=self.tenant.id,
                campus_id=self.campus.id,
                expected_contribution_revision=self.contribution.revision,
            ),
            lambda: QuestionDOCXImportService.create_preview(
                contribution_id=self.contribution.id,
                uploaded_file=self._docx_upload("Expired DOCX"),
                user=self.faculty,
                tenant_id=self.tenant.id,
                campus_id=self.campus.id,
                expected_contribution_revision=self.contribution.revision,
            ),
            lambda: QuestionMutationService.submit(
                contribution_id=self.contribution.id,
                user=self.faculty,
                tenant_id=self.tenant.id,
                campus_id=self.campus.id,
                expected_contribution_revision=self.contribution.revision,
            ),
        )
        for operation in operations:
            with self.subTest(operation=operation):
                with self.assertRaises(PermissionDenied):
                    operation()

    def test_j_owner_tenant_campus_and_direct_deny_boundaries_remain(self):
        self._authorized_reopen()
        other_faculty = self.make_faculty("wrong-reopened-faculty")
        with self.assertRaises(Http404):
            QuestionMutationService.create(
                contribution_id=self.contribution.id,
                user=other_faculty,
                tenant_id=self.tenant.id,
                campus_id=self.campus.id,
                expected_contribution_revision=self.contribution.revision,
                payload=self._payload("Wrong faculty"),
            )
        with self.assertRaises(Http404):
            QuestionMutationService.create(
                contribution_id=self.contribution.id,
                user=self.faculty,
                tenant_id=self.other_tenant.id,
                campus_id=self.campus.id,
                expected_contribution_revision=self.contribution.revision,
                payload=self._payload("Wrong tenant"),
            )
        with self.assertRaises(PermissionDenied):
            QuestionMutationService.create(
                contribution_id=self.contribution.id,
                user=self.faculty,
                tenant_id=self.tenant.id,
                campus_id=self.other_campus.id,
                expected_contribution_revision=self.contribution.revision,
                payload=self._payload("Missing campus authority"),
            )

        UserPermission.objects.create(
            user=self.faculty,
            permission=Permission.objects.get(code="faculty_portal.access"),
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.campus,
        )
        with self.assertRaises(PermissionDenied):
            self._create_via_service("Direct deny")

    def test_k_current_marker_blocks_forgery_and_superseded_history_does_not(self):
        current = self._revision()
        self._authorized_reopen()
        current.refresh_from_db()
        self.assertEqual(
            (current.status, current.current_marker),
            (ExamGenerationRevision.Status.SUPERSEDED, None),
        )
        GeneratedExamSet.objects.create(
            generation_revision=current,
            set_code=GeneratedExamSet.SetCode.A,
            item_count=50,
        )
        self._create_via_service("After successful supersession")

        forged_current = self._revision(number=2, token="c")
        with self.assertRaises(PermissionDenied):
            self._create_via_service("Forged metadata must not bypass current output")
        forged_current.status = ExamGenerationRevision.Status.SUPERSEDED
        forged_current.current_marker = None
        forged_current.save(update_fields=["status", "current_marker", "updated_at"])
        self._create_via_service("Historical outputs do not block")

    def test_l_current_revision_on_other_equivalency_member_blocks(self):
        secondary = self.make_course(cycle=self.parent.cycle, code="REOPEN-EQ")
        secondary_configuration = self.make_configuration(
            secondary,
            workflow=CourseExamConfiguration.WorkflowStatus.OPEN,
            opened_at=timezone.now() - timezone.timedelta(days=1),
            deadline=self.configuration.contribution_deadline,
        )
        ContributionRosterService.initialize(
            cycle_course_id=secondary.id,
            tenant_id=self.tenant.id,
            actor=self.generation_manager,
        )
        CourseExamConfiguration.objects.filter(pk=secondary_configuration.pk).update(
            workflow_status=CourseExamConfiguration.WorkflowStatus.CLOSED,
            closed_at=timezone.now(),
            closed_by=self.generation_manager,
        )
        CourseExamConfiguration.objects.filter(pk=self.configuration.pk).update(
            workflow_status=CourseExamConfiguration.WorkflowStatus.CLOSED,
            closed_at=timezone.now(),
            closed_by=self.generation_manager,
        )
        ExamCourseEquivalencyService.create_group(
            cycle_id=self.parent.cycle_id,
            name="Reopened Draft equivalency",
            primary_cycle_course_id=self.parent.id,
            member_ids=(self.parent.id, secondary.id),
            actor=self.admin,
        )
        self._invalidate_live_assignment()
        self.configuration.refresh_from_db()
        AutomaticContributionReopenService.reopen(
            cycle_course_id=self.parent.id,
            tenant_id=self.tenant.id,
            actor=self.generation_manager,
            expected_revision=self.configuration.revision,
            new_deadline=timezone.now() + timezone.timedelta(days=1),
        )
        self._refresh()
        secondary_configuration.refresh_from_db()
        other_member_current = ExamGenerationRevision(
            cycle_course=secondary,
            revision_number=1,
            source_input_fingerprint="d" * 64,
            algorithm_version="reopened-draft-test-v1",
            generation_trigger=ExamGenerationRevision.GenerationTrigger.AUTOMATIC,
            configuration_revision_snapshot=1,
            blueprint_revision_snapshot=1,
            roster_boundary_snapshot="e" * 64,
            final_item_count_snapshot=50,
            request_token_digest="f" * 64,
            minimum_overlap=0,
            proportional_score=0,
            contributors_represented=1,
            squared_contributor_concentration=1,
        )
        ExamGenerationRevision.objects.bulk_create([other_member_current])

        with self.assertRaises(PermissionDenied):
            self._create_via_service("Other member current revision")

    def test_m_never_eligible_source_does_not_receive_historical_proof(self):
        _assignment, source = self._add_never_eligible_other_campus_source()

        self.assertFalse(source.is_current)
        self.assertIsNotNone(source.invalidated_at)
        self.assertIsNone(source.eligibility_proven_at)

    def test_n_mixed_source_attack_cannot_use_never_eligible_other_campus(self):
        _assignment, never_eligible_source = (
            self._add_never_eligible_other_campus_source()
        )
        original_source = self.contribution.eligibility_sources.get(
            assignment_id_snapshot=self.assignment.id
        )
        self.assertIsNotNone(original_source.eligibility_proven_at)
        self.assertIsNone(never_eligible_source.eligibility_proven_at)
        UserPermission.objects.create(
            user=self.faculty,
            permission=Permission.objects.get(code="faculty_portal.access"),
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.campus,
        )
        self._authorized_reopen()

        self.assertFalse(
            ContributionAuthorizationService.has_authorized_reopened_existing_draft_authority(
                user=self.faculty,
                contribution=self.contribution,
                configuration=self.configuration,
                request_tenant_id=self.tenant.id,
                request_campus_id=self.other_campus.id,
            )
        )
        with self.assertRaises(PermissionDenied):
            QuestionMutationService.create(
                contribution_id=self.contribution.id,
                user=self.faculty,
                tenant_id=self.tenant.id,
                campus_id=self.other_campus.id,
                expected_contribution_revision=self.contribution.revision,
                payload=self._payload("Never-eligible source must not authorize"),
            )

    def test_o_normal_live_authority_still_sets_and_uses_proof(self):
        source = self.contribution.eligibility_sources.get()

        authority = ContributionAuthorizationService.require_mutable_locked(
            user=self.faculty,
            contribution=self.contribution,
            configuration=self.configuration,
            request_tenant_id=self.tenant.id,
            request_campus_id=self.campus.id,
        )

        self.assertEqual(
            authority,
            ContributionAuthorizationService.LIVE_AUTHORITY,
        )
        self.assertTrue(source.is_current)
        self.assertIsNotNone(source.eligibility_proven_at)

    def test_p_migration_is_nullable_and_has_only_narrow_data_backfill(self):
        migration = self._proof_migration()

        self.assertEqual(
            [operation.__class__.__name__ for operation in migration.Migration.operations],
            ["AddField", "RunPython"],
        )
        field = migration.Migration.operations[0].field
        self.assertTrue(field.null)
        self.assertTrue(field.blank)
        self.assertFalse(field.has_default())
        self.assertIs(
            migration.Migration.operations[1].code,
            migration.backfill_safe_legacy_eligibility_proof,
        )

    def test_q_legacy_post_baseline_eligible_then_invalid_source_is_backfilled(self):
        self._authorized_reopen()
        source = self.contribution.eligibility_sources.get(
            assignment_id_snapshot=self.assignment.id
        )
        self.assertGreater(source.invalidated_at, source.created_at)
        source.eligibility_proven_at = None
        source.save(update_fields=["eligibility_proven_at", "updated_at"])

        self._run_safe_legacy_proof_backfill()

        source.refresh_from_db()
        self.assertEqual(source.eligibility_proven_at, source.invalidated_at)
        self._create_via_service("Legacy proven source may resume")

    def test_r_legacy_never_eligible_source_remains_unproven(self):
        _assignment, source = self._add_never_eligible_other_campus_source()
        self.assertLessEqual(source.invalidated_at, source.created_at)

        self._run_safe_legacy_proof_backfill()

        source.refresh_from_db()
        self.assertIsNone(source.eligibility_proven_at)

    def test_s_legacy_mixed_sources_backfill_only_proven_transition(self):
        _assignment, never_eligible_source = (
            self._add_never_eligible_other_campus_source()
        )
        self._authorized_reopen()
        proven_source = self.contribution.eligibility_sources.get(
            assignment_id_snapshot=self.assignment.id
        )
        proven_source.eligibility_proven_at = None
        proven_source.save(update_fields=["eligibility_proven_at", "updated_at"])

        self._run_safe_legacy_proof_backfill()

        proven_source.refresh_from_db()
        never_eligible_source.refresh_from_db()
        self.assertEqual(
            proven_source.eligibility_proven_at,
            proven_source.invalidated_at,
        )
        self.assertIsNone(never_eligible_source.eligibility_proven_at)

    def test_t_legacy_migration_origin_is_ambiguous_and_fails_closed(self):
        self._authorized_reopen()
        source = self.contribution.eligibility_sources.get(
            assignment_id_snapshot=self.assignment.id
        )
        source.eligibility_proven_at = None
        source.save(update_fields=["eligibility_proven_at", "updated_at"])
        source.__class__.objects.filter(pk=source.pk).update(
            created_at=self._source_history_baseline()
        )

        self._run_safe_legacy_proof_backfill()

        source.refresh_from_db()
        self.assertIsNone(source.eligibility_proven_at)
        self.assertFalse(
            ContributionAuthorizationService.has_authorized_reopened_existing_draft_authority(
                user=self.faculty,
                contribution=self.contribution,
                configuration=self.configuration,
                request_tenant_id=self.tenant.id,
                request_campus_id=self.campus.id,
            )
        )
        with self.assertRaises(PermissionDenied):
            self._create_via_service("Ambiguous legacy source must remain denied")

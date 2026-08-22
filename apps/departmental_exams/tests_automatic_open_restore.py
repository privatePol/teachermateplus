from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.auditlog.models import AuditLog

from .automatic_workflow import AutomaticExamDeadlineService
from .contribution_authorization import ContributionAuthorizationService
from .contribution_services import ContributionRosterService
from .generation_readiness import eligible_submitted_question_pool
from .models import (
    CourseExamConfiguration,
    CycleCourse,
    ExamGenerationRevision,
    ExaminationCycle,
    FacultyContribution,
    Question,
    QuestionImportBatch,
    QuestionImportRow,
)
from .services import CycleCourseInclusionService
from .stage4_test_support import Stage4TestCase
from .tests_stage5_contributions import Stage5FixtureMixin


class AutomaticOpenRestoreTests(Stage5FixtureMixin, Stage4TestCase):
    def setUp(self):
        super().setUp()
        self.generation_manager = self.make_user(
            "automatic-open-restore-manager",
            None,
            (
                "admin_portal.access",
                "departmental_exams.manage_exam_generation",
            ),
            campus=self.campus,
        )

    def make_automatic_cycle(self):
        cycle = self.make_cycle(
            status=ExaminationCycle.Status.OPEN,
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=self.future_deadline(),
            instructions="Retain the approved contributor instructions.",
            scope_suffix=f"automatic-open-restore-{ExaminationCycle.objects.count()}",
        )
        cycle.processing_mode = ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        cycle.save(update_fields=["processing_mode", "updated_at"])
        return cycle

    def make_automatic_course(self, *, cycle=None, code="AOR", deadline=None):
        cycle = cycle or self.make_automatic_cycle()
        parent = self.make_course(cycle=cycle, department=None, code=code)
        configuration = self.make_configuration(
            parent,
            workflow=CourseExamConfiguration.WorkflowStatus.OPEN,
            opened_at=timezone.now() - timezone.timedelta(days=1),
            deadline=deadline or self.future_deadline(),
            deadline_source=CourseExamConfiguration.ValueSource.DEFAULT,
        )
        configuration.contributor_instructions_snapshot = (
            "Retain the approved contributor instructions."
        )
        configuration.save(
            update_fields=["contributor_instructions_snapshot", "updated_at"]
        )
        return parent, configuration

    def initialize_roster(self, parent):
        return ContributionRosterService.initialize(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            actor=self.generation_manager,
        )

    def exempt(self, parent):
        parent.refresh_from_db()
        return CycleCourseInclusionService.exempt(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            user=self.generation_manager,
            exemption_category=CycleCourse.ExemptionCategory.INTERNSHIP,
            reason="Approved Automatic workflow exemption",
            expected_updated_at=CycleCourseInclusionService.transition_token(parent),
        )

    def restore(self, parent):
        parent.refresh_from_db()
        return CycleCourseInclusionService.restore(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            user=self.generation_manager,
            reason="Restore the governed Automatic workflow",
            expected_updated_at=CycleCourseInclusionService.transition_token(parent),
        )

    @staticmethod
    def make_question(contribution, *, position=1, entry_method=Question.EntryMethod.MANUAL):
        return Question.objects.create(
            contribution=contribution,
            question_text=f"Which retained answer is correct for item {position}?",
            choice_a="The retained answer",
            choice_b="A discarded answer",
            choice_c="No answer",
            choice_d="Every answer",
            correct_answer="A",
            difficulty=Question.Difficulty.EASY,
            position=position,
            entry_method=entry_method,
        )

    @staticmethod
    def make_generation_revision(parent, configuration, *, revision_number, current):
        marker = str(revision_number)
        return ExamGenerationRevision.objects.create(
            cycle_course=parent,
            revision_number=revision_number,
            status=(
                ExamGenerationRevision.Status.GENERATED
                if current
                else ExamGenerationRevision.Status.SUPERSEDED
            ),
            current_marker=1 if current else None,
            source_input_fingerprint=marker * 64,
            algorithm_version="automatic-open-restore-regression",
            generated_by=None,
            generation_trigger=ExamGenerationRevision.GenerationTrigger.AUTOMATIC,
            configuration_revision_snapshot=configuration.revision,
            blueprint_revision_snapshot=1,
            roster_boundary_snapshot="b" * 64,
            final_item_count_snapshot=configuration.final_item_count,
            request_token_digest=marker * 64,
            minimum_overlap=0,
            proportional_score=0,
            contributors_represented=0,
            squared_contributor_concentration=0,
        )

    def test_restore_succeeds_before_effective_deadline_and_reopens_configuration(self):
        parent, configuration = self.make_automatic_course(code="AOR-SUCCESS")
        first_deadline = configuration.contribution_deadline
        reopened_deadline = first_deadline + timezone.timedelta(days=2)
        configuration.reopened_contribution_deadline = reopened_deadline
        configuration.save(
            update_fields=["reopened_contribution_deadline", "updated_at"]
        )
        self.initialize_roster(parent)
        configuration.refresh_from_db()
        preserved = {
            "opened_at": configuration.opened_at,
            "opened_by_id": configuration.opened_by_id,
            "contribution_deadline": configuration.contribution_deadline,
            "reopened_contribution_deadline": configuration.reopened_contribution_deadline,
            "contributor_instructions_snapshot": configuration.contributor_instructions_snapshot,
        }
        self.exempt(parent)
        configuration.refresh_from_db()
        exempt_revision = configuration.revision

        restored, changed = self.restore(parent)

        self.assertTrue(changed)
        self.assertEqual(restored.inclusion_status, CycleCourse.InclusionStatus.INCLUDED)
        configuration.refresh_from_db()
        self.assertEqual(
            configuration.workflow_status,
            CourseExamConfiguration.WorkflowStatus.OPEN,
        )
        self.assertIsNone(configuration.closed_at)
        self.assertIsNone(configuration.closed_by_id)
        self.assertEqual(configuration.revision, exempt_revision + 1)
        self.assertEqual(configuration.active_contribution_deadline, reopened_deadline)
        for field, value in preserved.items():
            with self.subTest(field=field):
                self.assertEqual(getattr(configuration, field), value)
        audit = AuditLog.objects.get(
            action="DE_EXAM_CYCLE_COURSE_RESTORED",
            entity_id=str(parent.id),
        )
        self.assertEqual(audit.after_json["configuration"]["workflow_status"], "OPEN")

    def test_restore_denies_missing_reached_or_passed_deadline_without_mutation(self):
        fixed_now = timezone.now()
        cases = (
            ("missing", None),
            ("reached", fixed_now),
            ("passed", fixed_now - timezone.timedelta(seconds=1)),
        )
        for label, deadline in cases:
            with self.subTest(deadline=label):
                parent, configuration = self.make_automatic_course(code=f"AOR-{label}")
                self.initialize_roster(parent)
                self.exempt(parent)
                update = {
                    "contribution_deadline": deadline,
                    "contribution_deadline_source": (
                        CourseExamConfiguration.ValueSource.DEFAULT
                        if deadline is not None
                        else None
                    ),
                    "reopened_contribution_deadline": None,
                }
                CourseExamConfiguration.objects.filter(pk=configuration.pk).update(
                    **update
                )
                before_parent = CycleCourse.objects.values().get(pk=parent.pk)
                before_configuration = CourseExamConfiguration.objects.values().get(
                    pk=configuration.pk
                )
                audit_count = AuditLog.objects.filter(
                    action="DE_EXAM_CYCLE_COURSE_RESTORED",
                    entity_id=str(parent.id),
                ).count()

                with patch(
                    "apps.departmental_exams.services.timezone.now",
                    return_value=fixed_now,
                ):
                    with self.assertRaises(ValidationError):
                        self.restore(parent)

                self.assertEqual(
                    CycleCourse.objects.values().get(pk=parent.pk), before_parent
                )
                self.assertEqual(
                    CourseExamConfiguration.objects.values().get(pk=configuration.pk),
                    before_configuration,
                )
                self.assertEqual(
                    AuditLog.objects.filter(
                        action="DE_EXAM_CYCLE_COURSE_RESTORED",
                        entity_id=str(parent.id),
                    ).count(),
                    audit_count,
                )

    def test_restore_resynchronizes_current_new_and_removed_draft_contributors(self):
        parent, configuration = self.make_automatic_course(code="AOR-ROSTER")
        current_faculty = self.make_faculty("aor-current-faculty")
        removed_faculty = self.make_faculty("aor-removed-faculty")
        current_assignment = self.make_assignment(parent, current_faculty)
        removed_assignment = self.make_assignment(parent, removed_faculty)
        self.initialize_roster(parent)
        current = FacultyContribution.objects.get(faculty_user=current_faculty)
        removed = FacultyContribution.objects.get(faculty_user=removed_faculty)
        self.exempt(parent)
        configuration.refresh_from_db()
        roster_revision = configuration.contributor_roster_revision
        removed_assignment.is_active = False
        removed_assignment.save(update_fields=["is_active", "updated_at"])
        new_faculty = self.make_faculty("aor-new-faculty")
        new_assignment = self.make_assignment(parent, new_faculty)

        self.restore(parent)

        current.refresh_from_db()
        removed.refresh_from_db()
        created = FacultyContribution.objects.get(faculty_user=new_faculty)
        configuration.refresh_from_db()
        self.assertEqual(current.roster_status, FacultyContribution.RosterStatus.ACTIVE)
        self.assertEqual(removed.roster_status, FacultyContribution.RosterStatus.BLOCKED)
        self.assertIsNotNone(removed.roster_blocked_at)
        self.assertEqual(created.status, FacultyContribution.Status.DRAFT)
        self.assertEqual(created.roster_status, FacultyContribution.RosterStatus.ACTIVE)
        self.assertEqual(created.source_assignment_id, new_assignment.id)
        self.assertEqual(configuration.contributor_roster_revision, roster_revision + 1)
        self.assertTrue(
            current.eligibility_sources.get(
                assignment_id_snapshot=current_assignment.id
            ).is_current
        )
        removed_source = removed.eligibility_sources.get(
            assignment_id_snapshot=removed_assignment.id
        )
        self.assertFalse(removed_source.is_current)
        self.assertIsNotNone(removed_source.invalidated_at)

    def test_restore_preserves_contributions_questions_import_and_snapshot_history(self):
        parent, configuration = self.make_automatic_course(code="AOR-HISTORY")
        draft_faculty = self.make_faculty("aor-draft-history")
        submitted_faculty = self.make_faculty("aor-submitted-history")
        self.make_assignment(parent, draft_faculty)
        self.make_assignment(parent, submitted_faculty)
        self.initialize_roster(parent)
        draft = FacultyContribution.objects.get(faculty_user=draft_faculty)
        submitted = FacultyContribution.objects.get(faculty_user=submitted_faculty)
        submitted.status = FacultyContribution.Status.SUBMITTED
        submitted.submitted_at = timezone.now()
        submitted.save(update_fields=["status", "submitted_at", "updated_at"])
        draft_question = self.make_question(draft)
        submitted_question = self.make_question(submitted)
        batch = QuestionImportBatch.objects.create(
            tenant=self.tenant,
            contribution=draft,
            uploading_user=draft_faculty,
            status=QuestionImportBatch.Status.READY,
            contribution_revision_snapshot=draft.revision,
            file_sha256="d" * 64,
            filename_sha256="e" * 64,
            total_rows=1,
            valid_rows=1,
            expires_at=self.future_deadline(),
        )
        row = QuestionImportRow.objects.create(
            batch=batch,
            row_number=1,
            payload={"question_text": "Retained CSV preview row"},
            fingerprint="f" * 64,
        )
        source_ids = set(
            draft.eligibility_sources.values_list("id", flat=True)
        ) | set(submitted.eligibility_sources.values_list("id", flat=True))
        offering_snapshot_ids = set(
            parent.offering_snapshots.values_list("id", flat=True)
        )
        contribution_snapshot = {
            item["id"]: item
            for item in FacultyContribution.objects.filter(cycle_course=parent).values()
        }
        self.exempt(parent)

        self.restore(parent)

        self.assertEqual(
            {
                item["id"]: item
                for item in FacultyContribution.objects.filter(cycle_course=parent).values()
            },
            contribution_snapshot,
        )
        self.assertEqual(
            set(Question.objects.filter(pk__in=[draft_question.pk, submitted_question.pk]).values_list("pk", flat=True)),
            {draft_question.pk, submitted_question.pk},
        )
        self.assertTrue(QuestionImportBatch.objects.filter(pk=batch.pk).exists())
        self.assertEqual(QuestionImportRow.objects.get(pk=row.pk).payload, row.payload)
        self.assertEqual(
            set(
                draft.eligibility_sources.model.objects.filter(
                    pk__in=source_ids
                ).values_list("pk", flat=True)
            ),
            source_ids,
        )
        self.assertEqual(
            set(parent.offering_snapshots.values_list("id", flat=True)),
            offering_snapshot_ids,
        )
        configuration.refresh_from_db()
        draft.refresh_from_db()
        ContributionAuthorizationService.require_mutable_locked(
            contribution=draft,
            configuration=configuration,
            request_tenant_id=self.tenant.id,
            request_campus_id=self.campus.id,
        )
        eligible, invalid_count = eligible_submitted_question_pool(
            cycle_course=parent,
            participating_campus_ids=(self.campus.id,),
        )
        self.assertEqual([question.id for question in eligible], [submitted_question.id])
        self.assertEqual(invalid_count, 0)

    def test_restore_resets_automatic_state_and_reenters_due_processing(self):
        parent, configuration = self.make_automatic_course(code="AOR-PROCESS")
        self.initialize_roster(parent)
        configuration.automatic_processing_status = (
            CourseExamConfiguration.AutomaticProcessingStatus.GENERATED
        )
        configuration.automatic_processing_code = "GENERATED"
        configuration.automatic_processed_at = timezone.now()
        configuration.save(
            update_fields=[
                "automatic_processing_status",
                "automatic_processing_code",
                "automatic_processed_at",
                "updated_at",
            ]
        )
        self.exempt(parent)

        self.restore(parent)

        configuration.refresh_from_db()
        self.assertEqual(configuration.automatic_processing_status, "")
        self.assertEqual(configuration.automatic_processing_code, "")
        self.assertIsNone(configuration.automatic_processed_at)
        results = AutomaticExamDeadlineService.process_due(now=timezone.now())
        result = next(item for item in results if item.cycle_course_id == parent.id)
        self.assertEqual(result.code, "NOT_DUE")
        configuration.refresh_from_db()
        self.assertEqual(
            configuration.workflow_status,
            CourseExamConfiguration.WorkflowStatus.OPEN,
        )

    def test_restore_roster_or_audit_failure_rolls_back_every_change(self):
        parent, configuration = self.make_automatic_course(code="AOR-ROLLBACK")
        existing_faculty = self.make_faculty("aor-existing-rollback")
        self.make_assignment(parent, existing_faculty)
        self.initialize_roster(parent)
        self.exempt(parent)
        new_faculty = self.make_faculty("aor-new-rollback")
        self.make_assignment(parent, new_faculty)
        before_parent = CycleCourse.objects.values().get(pk=parent.pk)
        before_configuration = CourseExamConfiguration.objects.values().get(
            pk=configuration.pk
        )
        before_contributions = list(
            FacultyContribution.objects.filter(cycle_course=parent)
            .order_by("id")
            .values()
        )
        before_audits = AuditLog.objects.count()

        with patch.object(
            ContributionRosterService,
            "_synchronize_locked",
            side_effect=ValidationError("Forced roster synchronization failure."),
        ):
            with self.assertRaises(ValidationError):
                self.restore(parent)
        self.assertEqual(CycleCourse.objects.values().get(pk=parent.pk), before_parent)
        self.assertEqual(
            CourseExamConfiguration.objects.values().get(pk=configuration.pk),
            before_configuration,
        )
        self.assertEqual(
            list(
                FacultyContribution.objects.filter(cycle_course=parent)
                .order_by("id")
                .values()
            ),
            before_contributions,
        )
        self.assertEqual(AuditLog.objects.count(), before_audits)

        with patch.object(
            CycleCourseInclusionService,
            "_audit_transition",
            side_effect=RuntimeError("Forced Restore audit failure."),
        ):
            with self.assertRaises(RuntimeError):
                self.restore(parent)
        self.assertEqual(CycleCourse.objects.values().get(pk=parent.pk), before_parent)
        self.assertEqual(
            CourseExamConfiguration.objects.values().get(pk=configuration.pk),
            before_configuration,
        )
        self.assertEqual(
            list(
                FacultyContribution.objects.filter(cycle_course=parent)
                .order_by("id")
                .values()
            ),
            before_contributions,
        )
        self.assertEqual(AuditLog.objects.count(), before_audits)

    def test_current_generated_revision_blocks_restore_without_mutation(self):
        parent, configuration = self.make_automatic_course(code="AOR-CURRENT")
        self.initialize_roster(parent)
        historical = self.make_generation_revision(
            parent,
            configuration,
            revision_number=1,
            current=False,
        )
        self.exempt(parent)
        configuration.refresh_from_db()
        current = self.make_generation_revision(
            parent,
            configuration,
            revision_number=2,
            current=True,
        )
        before_parent = CycleCourse.objects.values().get(pk=parent.pk)
        before_configuration = CourseExamConfiguration.objects.values().get(
            pk=configuration.pk
        )

        with self.assertRaisesMessage(
            ValidationError,
            "A current generated examination revision exists",
        ):
            self.restore(parent)

        self.assertEqual(CycleCourse.objects.values().get(pk=parent.pk), before_parent)
        self.assertEqual(
            CourseExamConfiguration.objects.values().get(pk=configuration.pk),
            before_configuration,
        )
        historical.refresh_from_db()
        current.refresh_from_db()
        self.assertEqual(
            (historical.status, historical.current_marker),
            (ExamGenerationRevision.Status.SUPERSEDED, None),
        )
        self.assertEqual(
            (current.status, current.current_marker),
            (ExamGenerationRevision.Status.GENERATED, 1),
        )
        self.assertFalse(
            AuditLog.objects.filter(
                action="DE_EXAM_CYCLE_COURSE_RESTORED",
                entity_id=str(parent.id),
            ).exists()
        )

    def test_restore_leaves_unrelated_course_unchanged(self):
        cycle = self.make_automatic_cycle()
        target, _target_configuration = self.make_automatic_course(
            cycle=cycle,
            code="AOR-TARGET",
        )
        unrelated, unrelated_configuration = self.make_automatic_course(
            cycle=cycle,
            code="AOR-UNRELATED",
        )
        self.initialize_roster(target)
        unrelated_snapshot = CycleCourse.objects.values().get(pk=unrelated.pk)
        unrelated_configuration_snapshot = CourseExamConfiguration.objects.values().get(
            pk=unrelated_configuration.pk
        )
        unrelated_offerings = list(
            unrelated.offering_snapshots.order_by("id").values()
        )
        self.exempt(target)

        self.restore(target)

        self.assertEqual(
            CycleCourse.objects.values().get(pk=unrelated.pk), unrelated_snapshot
        )
        self.assertEqual(
            CourseExamConfiguration.objects.values().get(
                pk=unrelated_configuration.pk
            ),
            unrelated_configuration_snapshot,
        )
        self.assertEqual(
            list(unrelated.offering_snapshots.order_by("id").values()),
            unrelated_offerings,
        )

from unittest.mock import patch
from importlib import import_module
from types import SimpleNamespace

from django.apps import apps
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.http import Http404
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import FacultyAssignment
from apps.auditlog.models import AuditLog
from apps.rbac.models import Permission, RolePermission, UserPermission, UserRole

from .answer_key_release import AnswerKeyReleaseService
from .automatic_workflow import AutomaticContributionReopenService, AutomaticExamDeadlineService
from .contribution_authorization import (
    ContributionAuthorizationService, ContributorEligibilityService,
)
from .contribution_services import ContributionRosterService, QuestionMutationService
from .contribution_selectors import ContributionSelector
from .generation_readiness import Stage6ReadinessService
from .models import (
    AnswerKeyRelease, CourseExamConfiguration, ExamGenerationRevision,
    ExaminationCycle, ExamScenario, ExamScenarioMember, FacultyContribution, GeneratedExamItem,
    GenerationSourceQuestionSnapshot, Question, QuestionIdentityReservation,
    QuestionBlueprintPlacement, QuestionnairePrintRelease,
)
from .questionnaire_printing import FacultyQuestionnairePrintService, QuestionnairePrintReleaseService
from .services import CourseExamConfigurationConflict
from .stage4_test_support import Stage4TestCase
from . import tests_automatic_workflow as workflow_tests
from .tests_faculty_cases import FacultyCaseFixtureMixin
from . import tests_case_generation as case_tests
from .setup_services import CourseSetupService
from .duplicate_contract import reconcile
from .tests_stage6_generation import Stage6BGenerationFixtureMixin


class ContributionCorrectionTests(Stage6BGenerationFixtureMixin, Stage4TestCase):
    _make_generation_manager = workflow_tests.AutomaticWorkflowTests._make_generation_manager
    _automatic = staticmethod(workflow_tests.AutomaticWorkflowTests._automatic)
    _ready_automatic_course = workflow_tests.AutomaticWorkflowTests._ready_automatic_course
    _proved_selection_for = workflow_tests.AutomaticWorkflowTests._proved_selection_for
    _process_with_proved_selection = workflow_tests.AutomaticWorkflowTests._process_with_proved_selection

    def setUp(self):
        super().setUp()
        self.generation_manager = self._make_generation_manager()

    def _submitted(self, parent):
        return FacultyContribution.objects.filter(
            cycle_course=parent, active_marker=1,
            status=FacultyContribution.Status.SUBMITTED,
        ).order_by("id").first()

    def test_expired_never_generated_return_preserves_source_and_bulk_deletes_copy(self):
        parent, configuration, _problem = self._ready_automatic_course()
        original = self._submitted(parent)
        old_question = original.questions.order_by("id").first()
        old_content = old_question.question_text
        deadline = timezone.now() + timezone.timedelta(days=1)
        AutomaticContributionReopenService.reopen(
            cycle_course_id=parent.id, tenant_id=self.tenant.id,
            actor=self.generation_manager, expected_revision=configuration.revision,
            new_deadline=deadline, reason="Correct an expired submitted question.",
            selected_contribution_ids=[original.id],
        )
        original.refresh_from_db()
        successor = FacultyContribution.objects.get(supersedes=original)
        with self.assertRaises(CourseExamConfigurationConflict):
            AutomaticContributionReopenService.reopen(
                cycle_course_id=parent.id, tenant_id=self.tenant.id,
                actor=self.generation_manager, expected_revision=configuration.revision,
                new_deadline=deadline, reason="Reject a repeated correction request.",
                selected_contribution_ids=[original.id],
            )
        self.assertEqual(FacultyContribution.objects.filter(supersedes=original).count(), 1)
        self.assertIsNone(original.active_marker)
        self.assertEqual(original.status, FacultyContribution.Status.SUBMITTED)
        self.assertEqual(successor.status, FacultyContribution.Status.DRAFT)
        self.assertEqual(successor.active_marker, 1)
        copied = successor.questions.get(position=old_question.position)
        self.assertNotEqual(copied.id, old_question.id)
        self.assertEqual(copied.question_text, old_content)
        self.assertIsNone(copied.import_batch_id)
        self.assertEqual(successor.questions.count(), original.questions.count())
        self.assertEqual(ContributionSelector.owner_queryset(
            user=original.faculty_user, tenant_id=self.tenant.id,
        ).get(cycle_course=parent).id, successor.id)
        self.assertFalse(QuestionIdentityReservation.objects.filter(question_id=old_question.id).exists())
        self.assertTrue(QuestionIdentityReservation.objects.filter(question_id=copied.id).exists())
        audit = AuditLog.objects.filter(action="DE_EXAM_CONTRIBUTION_REOPENED").latest("id")
        self.assertEqual(audit.actor_user_id, self.generation_manager.id)
        self.assertEqual(audit.metadata_json["reason"], "Correct an expired submitted question.")
        self.assertIn("previous_deadline", audit.metadata_json)
        self.assertIn("new_deadline", audit.metadata_json)
        configuration.refresh_from_db()
        self.assertTrue(configuration.closed_cycle_correction_active)
        self.assertEqual(configuration.workflow_status, CourseExamConfiguration.WorkflowStatus.OPEN)
        QuestionMutationService.delete_many(
            contribution_id=successor.id, selected_questions=[(copied.id, copied.revision)],
            user=successor.faculty_user, tenant_id=self.tenant.id,
            campus_id=successor.source_campus_id,
            expected_contribution_revision=successor.revision,
        )
        self.assertTrue(Question.objects.filter(pk=old_question.id, question_text=old_content).exists())
        self.assertFalse(Question.objects.filter(pk=copied.id).exists())
        self.assertEqual(successor.questions.count(), original.questions.count() - 1)

    def test_closed_cycle_operator_form_returns_selected_submission(self):
        parent, configuration, _problem = self._ready_automatic_course()
        original = self._submitted(parent)
        parent.cycle.status = ExaminationCycle.Status.CLOSED
        parent.cycle.save(update_fields=["status", "updated_at"])
        client = Client()
        client.force_login(self.generation_manager)
        url = reverse("departmental_exams:automatic_contribution_reopen", args=[parent.id])
        page = client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Return selected Submitted contributions to Draft")
        response = client.post(url, {
            "expected_revision": configuration.revision,
            "expected_state_token": page.context["form"]["expected_state_token"].value(),
            "reason": "Correct a submitted question in a closed cycle.",
            "new_deadline": (timezone.now() + timezone.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M"),
            "selected_contributions": [str(original.id)],
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(FacultyContribution.objects.filter(supersedes=original, status="DRAFT").exists())

    def test_released_closed_cycle_preserves_history_and_revokes_both_releases(self):
        parent, configuration, problem = self._ready_automatic_course()
        self._process_with_proved_selection(parent=parent, problem=problem)
        current = ExamGenerationRevision.objects.get(cycle_course=parent, current_marker=1)
        original = self._submitted(parent)
        old_question_ids = list(original.questions.values_list("id", flat=True))
        now = timezone.now()
        print_release = QuestionnairePrintRelease.objects.create(
            cycle_course=parent, generation_revision=current,
            print_from=now - timezone.timedelta(minutes=1),
            print_until=now + timezone.timedelta(days=2),
            released_by=self.generation_manager,
        )
        key_release = AnswerKeyRelease.objects.create(
            cycle_course=parent, generation_revision=current,
            recipient_course=parent, target_campus=original.source_campus,
            available_from=now - timezone.timedelta(minutes=1),
            available_until=now + timezone.timedelta(days=2),
            released_by=self.generation_manager, attestation_version="test-v1",
        )
        cycle = parent.cycle
        cycle.status = ExaminationCycle.Status.CLOSED
        cycle.save(update_fields=["status", "updated_at"])
        parent.cycle = cycle
        deadline = now + timezone.timedelta(days=1)
        AutomaticContributionReopenService.reopen(
            cycle_course_id=parent.id, tenant_id=self.tenant.id,
            actor=self.generation_manager, expected_revision=configuration.revision,
            new_deadline=deadline, reason="Replace wrong course questions after release.",
            selected_contribution_ids=[original.id],
        )
        current.refresh_from_db()
        print_release.refresh_from_db()
        key_release.refresh_from_db()
        self.assertEqual((current.status, current.current_marker), ("SUPERSEDED", None))
        self.assertEqual((print_release.status, print_release.active_marker), ("REVOKED", None))
        self.assertEqual((key_release.status, key_release.active_marker), ("REVOKED", None))
        self.assertEqual(GeneratedExamItem.objects.filter(source_question_id__in=old_question_ids).count() > 0, True)
        self.assertEqual(GenerationSourceQuestionSnapshot.objects.filter(source_question_id__in=old_question_ids).count() > 0, True)
        with self.assertRaises(PermissionDenied):
            FacultyQuestionnairePrintService._printable_release(
                contribution=original, release_id=print_release.id, set_code="A")
        faculty_client = Client()
        faculty_client.force_login(original.faculty_user)
        for name in ("questionnaire_print", "faculty_answer_key", "personalized_answer_sheet_overview"):
            args = ([original.id, print_release.id, "A"] if name == "questionnaire_print"
                    else [original.id, key_release.id, "A"] if name == "faculty_answer_key"
                    else [original.id, print_release.id])
            self.assertEqual(faculty_client.get(reverse("departmental_exams:" + name, args=args)).status_code, 404)
        admin_client = Client()
        admin_client.force_login(self.generation_manager)
        self.assertEqual(admin_client.get(reverse("departmental_exams:generated_revision_detail", args=[current.id])).status_code, 200)
        self.assertEqual(admin_client.get(reverse("departmental_exams:admin_questionnaire_print", args=[current.id, "A"])).status_code, 200)
        successor = FacultyContribution.objects.get(supersedes=original)
        self.assertEqual(successor.status, FacultyContribution.Status.DRAFT)
        configuration.refresh_from_db()
        self.assertTrue(configuration.closed_cycle_correction_active)
        with self.assertRaises(PermissionDenied):
            AnswerKeyReleaseService.revoke(
                release_id=key_release.id, tenant_id=self.tenant.id,
                actor=self.generation_manager,
            )

    def test_stale_confirmation_and_foreign_selection_are_atomic(self):
        parent, configuration, _problem = self._ready_automatic_course(due=False)
        original = self._submitted(parent)
        with self.assertRaises(Http404):
            AutomaticContributionReopenService.reopen(
                cycle_course_id=parent.id, tenant_id=self.tenant.id + 100000,
                actor=self.generation_manager, expected_revision=configuration.revision,
                new_deadline=timezone.now() + timezone.timedelta(days=1),
                reason="Reject a request from a different tenant.",
            )
        token = AutomaticContributionReopenService.state_token(parent)
        configuration.revision += 1
        configuration.save(update_fields=["revision", "updated_at"])
        with self.assertRaisesMessage(CourseExamConfigurationConflict, "Refresh and retry"):
            AutomaticContributionReopenService.reopen(
                cycle_course_id=parent.id, tenant_id=self.tenant.id,
                actor=self.generation_manager, expected_revision=configuration.revision,
                expected_state_token=token,
                new_deadline=timezone.now() + timezone.timedelta(days=1),
                reason="Correct a stale submitted question.",
                selected_contribution_ids=[original.id],
            )
        original.refresh_from_db()
        self.assertEqual(original.active_marker, 1)
        self.assertFalse(FacultyContribution.objects.filter(supersedes=original).exists())
        with self.assertRaisesMessage(CourseExamConfigurationConflict, "Selected submissions changed"):
            AutomaticContributionReopenService.reopen(
                cycle_course_id=parent.id, tenant_id=self.tenant.id,
                actor=self.generation_manager, expected_revision=configuration.revision,
                expected_state_token=AutomaticContributionReopenService.state_token(parent),
                new_deadline=timezone.now() + timezone.timedelta(days=1),
                reason="Reject a foreign submitted contribution selection.",
                selected_contribution_ids=[original.id, original.id + 100000],
            )
        self.assertFalse(FacultyContribution.objects.filter(supersedes=original).exists())

    def test_blocked_terminal_attempt_is_cleared_and_retried_after_new_deadline(self):
        parent, configuration, _problem = self._ready_automatic_course()
        CourseExamConfiguration.objects.filter(pk=configuration.pk).update(
            automatic_processing_status=CourseExamConfiguration.AutomaticProcessingStatus.BLOCKED,
            automatic_processing_code="INSUFFICIENT_QUESTIONS",
            automatic_processed_at=timezone.now(),
        )
        new_deadline = timezone.now() + timezone.timedelta(days=1)
        AutomaticContributionReopenService.reopen(
            cycle_course_id=parent.id, tenant_id=self.tenant.id,
            actor=self.generation_manager, expected_revision=configuration.revision,
            new_deadline=new_deadline, reason="Retry after correcting a blocked intake.",
        )
        configuration.refresh_from_db()
        self.assertEqual(configuration.automatic_processing_status, "")
        self.assertIsNone(configuration.automatic_processed_at)
        self.assertEqual(configuration.workflow_status, "OPEN")
        paused = AutomaticExamDeadlineService.process_course(
            cycle_course_id=parent.id, tenant_id=self.tenant.id,
            now=new_deadline - timezone.timedelta(minutes=1))
        self.assertEqual((paused.status, paused.code), ("SKIPPED", "NOT_DUE"))
        self.assertIsNone(AutomaticExamDeadlineService._close_due_intake(
            cycle_course_id=parent.id, tenant_id=self.tenant.id,
            now=new_deadline + timezone.timedelta(minutes=1)))
        problem, readiness = Stage6ReadinessService.build_problem(cycle_course=parent)
        self.assertTrue(readiness["ready"], readiness["blockers"])
        selection = self._proved_selection_for(parent=parent, problem=problem)
        with patch(
            "apps.departmental_exams.generation_services.solve_automatic_identity_aware_two_sets",
            return_value=selection,
        ):
            result = AutomaticExamDeadlineService.process_course(
                cycle_course_id=parent.id, tenant_id=self.tenant.id,
                now=new_deadline + timezone.timedelta(minutes=1))
        self.assertEqual(result.status, "GENERATED")
        self.assertEqual(ExamGenerationRevision.objects.get(cycle_course=parent).revision_number, 1)

    def test_migration_reverse_guard_preserves_version_history(self):
        parent, configuration, _problem = self._ready_automatic_course()
        guard = import_module(
            "apps.departmental_exams.migrations.0032_contribution_correction_versions"
        ).preserve_correction_history
        schema = SimpleNamespace(connection=connection)
        guard(apps, schema)
        AutomaticContributionReopenService.reopen(
            cycle_course_id=parent.id, tenant_id=self.tenant.id,
            actor=self.generation_manager, expected_revision=configuration.revision,
            new_deadline=timezone.now() + timezone.timedelta(days=1),
            reason="Preserve returned contribution history on rollback.",
            selected_contribution_ids=[self._submitted(parent).id],
        )
        with self.assertRaisesRegex(RuntimeError, "Correction history exists"):
            guard(apps, schema)

    def test_failed_terminal_attempt_is_cleared_for_future_retry(self):
        parent, configuration, _problem = self._ready_automatic_course()
        CourseExamConfiguration.objects.filter(pk=configuration.pk).update(
            automatic_processing_status=CourseExamConfiguration.AutomaticProcessingStatus.ERROR,
            automatic_processing_code="PROCESSING_ERROR",
            automatic_processed_at=timezone.now(),
        )
        new_deadline = timezone.now() + timezone.timedelta(days=1)
        AutomaticContributionReopenService.reopen(
            cycle_course_id=parent.id, tenant_id=self.tenant.id,
            actor=self.generation_manager, expected_revision=configuration.revision,
            new_deadline=new_deadline, reason="Correct failed automatic processing inputs.",
        )
        configuration.refresh_from_db()
        self.assertEqual((configuration.automatic_processing_status,
                          configuration.automatic_processing_code), ("", ""))
        self.assertIsNone(configuration.automatic_processed_at)
        self.assertEqual(AutomaticExamDeadlineService.process_course(
            cycle_course_id=parent.id, tenant_id=self.tenant.id,
            now=new_deadline - timezone.timedelta(minutes=1),
        ).code, "NOT_DUE")

    def test_closed_cycle_return_submit_worker_replacement_and_separate_releases(self):
        parent, configuration, problem = self._ready_automatic_course()
        self._process_with_proved_selection(parent=parent, problem=problem)
        r1 = ExamGenerationRevision.objects.get(cycle_course=parent, current_marker=1)
        original = self._submitted(parent)
        parent.cycle.status = ExaminationCycle.Status.CLOSED
        parent.cycle.save(update_fields=["status", "updated_at"])
        new_deadline = timezone.now() + timezone.timedelta(days=1)
        AutomaticContributionReopenService.reopen(
            cycle_course_id=parent.id, tenant_id=self.tenant.id,
            actor=self.generation_manager, expected_revision=configuration.revision,
            new_deadline=new_deadline,
            reason="Replace submitted questions in a closed cycle.",
            selected_contribution_ids=[original.id],
        )
        successor = FacultyContribution.objects.get(supersedes=original)
        QuestionMutationService.submit(
            contribution_id=successor.id, user=successor.faculty_user,
            tenant_id=self.tenant.id, campus_id=successor.source_campus_id,
            expected_contribution_revision=successor.revision,
        )
        after_deadline = new_deadline + timezone.timedelta(minutes=1)
        self.assertIsNone(AutomaticExamDeadlineService._close_due_intake(
            cycle_course_id=parent.id, tenant_id=self.tenant.id, now=after_deadline))
        problem, readiness = Stage6ReadinessService.build_problem(cycle_course=parent)
        self.assertTrue(readiness["ready"], readiness["blockers"])
        selection = self._proved_selection_for(parent=parent, problem=problem)
        with patch(
            "apps.departmental_exams.generation_services.solve_automatic_identity_aware_two_sets",
            return_value=selection,
        ):
            result = AutomaticExamDeadlineService.process_course(
                cycle_course_id=parent.id, tenant_id=self.tenant.id, now=after_deadline)
        self.assertEqual(result.status, "GENERATED")
        r2 = ExamGenerationRevision.objects.get(cycle_course=parent, current_marker=1)
        self.assertEqual((r2.revision_number, r2.supersedes_id), (2, r1.id))
        configuration.refresh_from_db()
        self.assertFalse(configuration.closed_cycle_correction_active)
        now = timezone.now()
        print_release = QuestionnairePrintReleaseService.release(
            cycle_course_id=parent.id, revision_id=r2.id,
            target_campus_id=self.campus.id,
            tenant_id=self.tenant.id, actor=self.generation_manager,
            print_from=now, print_until=now + timezone.timedelta(days=1),
        )
        RolePermission.objects.create(
            role=self.generation_manager.user_roles.get().role,
            permission=Permission.objects.get(code="departmental_exams.release_answer_keys"),
        )
        key_release = AnswerKeyReleaseService.release(
            cycle_course_id=parent.id, revision_id=r2.id,
            tenant_id=self.tenant.id, actor=self.generation_manager,
            available_from=now, available_until=now + timezone.timedelta(days=1),
            attestation_confirmed=True, target_campus_id=successor.source_campus_id,
            recipient_course_id=parent.id,
        )
        self.assertEqual(print_release.generation_revision_id, r2.id)
        self.assertEqual(key_release.generation_revision_id, r2.id)

        configuration.refresh_from_db()
        second_deadline = timezone.now() + timezone.timedelta(days=2)
        AutomaticContributionReopenService.reopen(
            cycle_course_id=parent.id, tenant_id=self.tenant.id,
            actor=self.generation_manager, expected_revision=configuration.revision,
            new_deadline=second_deadline,
            reason="A second justified correction after revision two release.",
            selected_contribution_ids=[successor.id],
        )
        print_release.refresh_from_db()
        key_release.refresh_from_db()
        self.assertEqual((print_release.status, key_release.status), ("REVOKED", "REVOKED"))
        final_draft = FacultyContribution.objects.get(supersedes=successor)
        QuestionMutationService.submit(
            contribution_id=final_draft.id, user=final_draft.faculty_user,
            tenant_id=self.tenant.id, campus_id=final_draft.source_campus_id,
            expected_contribution_revision=final_draft.revision,
        )
        third_time = second_deadline + timezone.timedelta(minutes=1)
        self.assertIsNone(AutomaticExamDeadlineService._close_due_intake(
            cycle_course_id=parent.id, tenant_id=self.tenant.id, now=third_time))
        problem, readiness = Stage6ReadinessService.build_problem(cycle_course=parent)
        self.assertTrue(readiness["ready"], readiness["blockers"])
        selection = self._proved_selection_for(parent=parent, problem=problem)
        with patch(
            "apps.departmental_exams.generation_services.solve_automatic_identity_aware_two_sets",
            return_value=selection,
        ):
            result = AutomaticExamDeadlineService.process_course(
                cycle_course_id=parent.id, tenant_id=self.tenant.id, now=third_time)
        self.assertEqual(result.status, "GENERATED")
        r3 = ExamGenerationRevision.objects.get(cycle_course=parent, current_marker=1)
        self.assertEqual((r3.revision_number, r3.supersedes_id), (3, r2.id))
        r1.refresh_from_db()
        r2.refresh_from_db()
        self.assertEqual((r1.status, r2.status), ("SUPERSEDED", "SUPERSEDED"))


class AdminCaseCorrectionTests(FacultyCaseFixtureMixin, Stage4TestCase):
    fill_and_submit = case_tests.CaseGenerationTests.fill_and_submit
    generate = case_tests.CaseGenerationTests.generate

    def make_cycle(self, **kwargs):
        self.configurer = self.admin
        cycle = super().make_cycle(**kwargs)
        cycle.processing_mode = ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        cycle.automatic_contributor_completion_policy = "SUFFICIENT_POOL"
        cycle.save()
        return cycle

    def make_course(self, **kwargs):
        parent = super().make_course(**kwargs)
        CourseSetupService.classify(
            course_id=parent.id, tenant_id=self.tenant.id, actor=self.admin,
            classification="DEPARTMENTAL", expected_state=CourseSetupService.fingerprint(parent),
        )
        parent.refresh_from_db()
        return parent

    def _released_reopened_admin_case(self, *, close_cycle=True):
        faculty_cases = self.fill_and_submit()
        old_members = list(faculty_cases[0].members.order_by("position"))
        old_ids = [row.question_id for row in old_members]
        ExamScenarioMember.objects.filter(pk__in=[row.id for row in old_members]).delete()
        faculty_cases[0].delete()
        admin_case = ExamScenario.objects.create(
            blueprint=self.blueprint, section=self.section_a, title="Reviewer correction Case",
            stimulus="Preserved reviewer narrative.", created_by=self.admin, updated_by=self.admin,
        )
        ExamScenarioMember.objects.bulk_create([
            ExamScenarioMember(scenario=admin_case, question_id=question_id, position=position)
            for position, question_id in enumerate(old_ids, start=1)
        ])
        reconcile(self.parent)
        r1 = self.generate()
        self.configuration.refresh_from_db()
        now = timezone.now()
        QuestionnairePrintReleaseService.release(
            cycle_course_id=self.parent.id, revision_id=r1.id,
            target_campus_id=self.campus.id,
            tenant_id=self.tenant.id, actor=self.admin,
            print_from=now, print_until=now + timezone.timedelta(days=2),
        )
        AnswerKeyReleaseService.release(
            cycle_course_id=self.parent.id, revision_id=r1.id,
            tenant_id=self.tenant.id, actor=self.admin,
            available_from=now, available_until=now + timezone.timedelta(days=2),
            attestation_confirmed=True, target_campus_id=self.campus.id,
            recipient_course_id=self.parent.id,
        )
        if close_cycle:
            self.cycle.status = ExaminationCycle.Status.CLOSED
            self.cycle.save(update_fields=["status", "updated_at"])
        deadline = now + timezone.timedelta(days=1)
        AutomaticContributionReopenService.reopen(
            cycle_course_id=self.parent.id, tenant_id=self.tenant.id,
            actor=self.admin, expected_revision=self.configuration.revision,
            new_deadline=deadline, reason="Correct released reviewer Case questions.",
            selected_contribution_ids=[self.contribution.id],
        )
        successor = FacultyContribution.objects.get(supersedes=self.contribution)
        current_case = ExamScenario.objects.get(supersedes=admin_case)
        return r1, admin_case, current_case, successor, old_ids, deadline

    def test_roster_rebound_draft_keeps_current_window_case_access(self):
        r1, old_case, current_case, draft, old_ids, _deadline = (
            self._released_reopened_admin_case(close_cycle=False)
        )
        self.assertEqual(self.cycle.status, ExaminationCycle.Status.OPEN)
        original_submitted_at = self.contribution.submitted_at
        original_snapshots = list(GeneratedExamItem.objects.filter(
            generated_set__generation_revision=r1,
        ).order_by("id").values_list(
            "id", "source_question_id", "source_campus_id", "scenario_id_snapshot",
            "question_text_snapshot",
        ))
        other_offering = self.add_grouped_offering(
            self.parent, campus=self.other_campus,
            department=self.other_department, slug="CASE-REBOUND",
        )
        campus_role = UserRole.objects.create(
            user=self.faculty,
            role=self.faculty.user_roles.get(campus=self.campus).role,
            tenant=self.tenant, campus=self.other_campus,
            department=self.other_department,
        )
        rebound_assignment = self.make_assignment(
            self.parent, self.faculty, campus=self.other_campus,
            offering=other_offering,
        )
        self.assertEqual({
            assignment.campus_id for assignment in
            ContributorEligibilityService.source_inventory(
                cycle_course=type(self.parent).objects.get(pk=self.parent.id),
                faculty_user_id=self.faculty.id,
            ).eligible_sources
        }, {self.campus.id, self.other_campus.id})
        old_assignment = FacultyAssignment.objects.get(
            faculty_user=self.faculty, campus=self.campus,
        )
        old_assignment.is_active = False
        old_assignment.save(update_fields=["is_active", "updated_at"])
        result = ContributionRosterService.synchronize(
            cycle_course_id=self.parent.id, tenant_id=self.tenant.id,
            actor=self.admin,
        )
        self.assertTrue(result["changed"])
        draft.refresh_from_db()
        self.contribution.refresh_from_db()
        self.assertEqual((draft.source_campus_id, draft.source_assignment_id),
                         (self.other_campus.id, rebound_assignment.id))
        self.assertEqual((self.contribution.source_campus_id, self.contribution.submitted_at),
                         (self.campus.id, original_submitted_at))
        self.assertEqual(ContributionAuthorizationService.require_mutable_locked(
            user=self.faculty, contribution=draft,
            configuration=draft.cycle_course.configuration,
            request_tenant_id=self.tenant.id,
            request_campus_id=self.other_campus.id,
        ), ContributionAuthorizationService.LIVE_AUTHORITY)
        current_case.members.order_by("position").first().full_clean()
        current_question = current_case.members.order_by("position").first().question
        edit_url = reverse("departmental_exams:question_edit", args=[draft.id, current_question.id])
        workspace_url = reverse("departmental_exams:contribution_workspace", args=[draft.id])
        workspace = self.client.get(workspace_url, {
            "scope_tenant_id": self.tenant.id,
            "scope_campus_id": self.other_campus.id,
        })
        self.assertEqual(workspace.status_code, 200)
        case_visible = current_case.title in workspace.content.decode()
        edit_page = self.client.get(edit_url)
        self.assertEqual(edit_page.status_code, 200)
        edit_form = edit_page.context["form"]
        edit_payload = {
            "expected_contribution_revision": edit_form["expected_contribution_revision"].value(),
            "expected_question_revision": edit_form["expected_question_revision"].value(),
            "expected_scenario_revision": edit_form["expected_scenario_revision"].value(),
            "scenario_id": edit_form["scenario_id"].value(),
            "section_id": edit_form["section_id"].value(),
            "question_text": "Edited after eligible campus reassignment",
            "choice_a": current_question.choice_a, "choice_b": current_question.choice_b,
            "choice_c": current_question.choice_c, "choice_d": current_question.choice_d,
            "correct_answer": current_question.correct_answer,
            "difficulty": current_question.difficulty,
            "content_format": current_question.content_format,
        }
        edit_response = self.client.post(edit_url, edit_payload)
        self.assertEqual((case_visible, edit_response.status_code), (True, 302))
        current_question.refresh_from_db()
        self.assertEqual(current_question.question_text, edit_payload["question_text"])

        removed_question = current_case.members.order_by("position")[1].question
        delete_url = reverse("departmental_exams:question_delete", args=[draft.id, removed_question.id])
        delete_page = self.client.get(delete_url)
        self.assertEqual(delete_page.status_code, 200)
        delete_form = delete_page.context["form"]
        self.assertEqual(self.client.post(delete_url, {
            "expected_contribution_revision": delete_form["expected_contribution_revision"].value(),
            "expected_question_revision": delete_form["expected_question_revision"].value(),
        }).status_code, 302)
        add_url = reverse("departmental_exams:faculty_case_question_create", args=[
            draft.id, current_case.id,
        ])
        add_page = self.client.get(add_url)
        self.assertEqual(add_page.status_code, 200)
        add_form = add_page.context["form"]
        add_payload = {
            "expected_contribution_revision": add_form["expected_contribution_revision"].value(),
            "expected_scenario_revision": add_form["expected_scenario_revision"].value(),
            "scenario_id": add_form["scenario_id"].value(),
            "section_id": add_form["section_id"].value(),
            "insert_position": 2,
            "question_text": "Replacement after eligible campus reassignment",
            "choice_a": "One", "choice_b": "Two", "choice_c": "Three", "choice_d": "Four",
            "correct_answer": "A", "difficulty": removed_question.difficulty,
            "content_format": "PLAIN_TEXT",
        }
        deny_edit_form = self.client.get(edit_url).context["form"]
        deny_edit_payload = {
            **edit_payload,
            "expected_contribution_revision": deny_edit_form[
                "expected_contribution_revision"].value(),
            "expected_question_revision": deny_edit_form[
                "expected_question_revision"].value(),
            "expected_scenario_revision": deny_edit_form[
                "expected_scenario_revision"].value(),
            "question_text": "Denied current-campus Case edit",
        }
        draft.refresh_from_db()
        current_case.refresh_from_db()
        case_baseline = (current_case.revision, list(current_case.members.order_by(
            "position").values_list("question_id", "position")))
        question_count = Question.objects.count()
        placement_count = QuestionBlueprintPlacement.objects.count()

        def assert_denied_without_mutation():
            self.assertNotEqual(self.client.get(edit_url).status_code, 200)
            self.assertNotEqual(self.client.post(edit_url, deny_edit_payload).status_code, 302)
            self.assertNotEqual(self.client.get(add_url).status_code, 200)
            self.assertNotEqual(self.client.post(add_url, add_payload).status_code, 302)
            with self.assertRaisesMessage(
                ValidationError,
                "Only Submitted questions or their correction Draft copies",
            ):
                current_case.members.order_by("position").first().full_clean()
            current_case.refresh_from_db()
            current_question.refresh_from_db()
            self.assertEqual((current_case.revision, list(current_case.members.order_by(
                "position").values_list("question_id", "position"))), case_baseline)
            self.assertEqual(current_question.question_text, edit_payload["question_text"])
            self.assertEqual((Question.objects.count(), QuestionBlueprintPlacement.objects.count()),
                             (question_count, placement_count))
            self.assertFalse(Question.objects.filter(
                question_text=add_payload["question_text"], contribution=draft,
            ).exists())

        direct_deny = UserPermission.objects.create(
            user=self.faculty,
            permission=Permission.objects.get(code="faculty_portal.access"),
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant, campus=self.other_campus,
        )
        assert_denied_without_mutation()
        direct_deny.delete()
        campus_role.is_active = False
        campus_role.save(update_fields=["is_active"])
        assert_denied_without_mutation()
        campus_role.is_active = True
        campus_role.save(update_fields=["is_active"])
        rebound_assignment.is_active = False
        rebound_assignment.save(update_fields=["is_active", "updated_at"])
        assert_denied_without_mutation()
        rebound_assignment.is_active = True
        rebound_assignment.save(update_fields=["is_active", "updated_at"])

        self.assertEqual(self.client.get(add_url).status_code, 200)
        self.assertEqual(self.client.post(add_url, add_payload).status_code, 302)
        replacement = Question.objects.get(
            contribution=draft, question_text=add_payload["question_text"],
        )
        self.assertEqual(replacement.blueprint_placement.section_id, self.section_a.id)
        self.assertEqual(replacement.exam_scenario_membership.scenario_id, current_case.id)
        self.assertEqual(list(current_case.members.order_by("position").values_list(
            "position", flat=True)), [1, 2, 3, 4, 5])
        submit_url = reverse("departmental_exams:contribution_submit", args=[draft.id])
        submit_page = self.client.get(submit_url)
        self.assertEqual(submit_page.status_code, 200)
        self.assertEqual(self.client.post(submit_url, {
            "expected_contribution_revision": submit_page.context["form"][
                "expected_contribution_revision"].value(),
            "confirm_exact_quota": "on",
        }).status_code, 302)
        draft.refresh_from_db()
        self.assertEqual((draft.status, draft.source_campus_id),
                         (FacultyContribution.Status.SUBMITTED, self.other_campus.id))
        self.contribution.refresh_from_db()
        self.assertEqual((self.contribution.source_campus_id, self.contribution.submitted_at),
                         (self.campus.id, original_submitted_at))
        self.assertEqual(list(old_case.members.order_by("position").values_list(
            "question_id", flat=True)), old_ids)
        self.assertEqual(list(GeneratedExamItem.objects.filter(
            generated_set__generation_revision=r1,
        ).order_by("id").values_list(
            "id", "source_question_id", "source_campus_id", "scenario_id_snapshot",
            "question_text_snapshot",
        )), original_snapshots)

    def test_older_case_membership_cannot_authorize_a_only_return_after_r2(self):
        faculty_cases = self.fill_and_submit()
        old_members = list(faculty_cases[0].members.order_by("position"))
        ExamScenarioMember.objects.filter(pk__in=[row.id for row in old_members]).delete()
        faculty_cases[0].delete()
        other_questions = []
        for position in (1, 2):
            question = Question.objects.create(
                contribution=self.other_contribution, position=position,
                question_text=f"Other contributor Case member {position}",
                choice_a="One", choice_b="Two", choice_c="Three", choice_d="Four",
                correct_answer="A", difficulty="EASY",
            )
            QuestionBlueprintPlacement.objects.create(
                blueprint=self.blueprint, section=self.section_a,
                question=question, placed_by=self.admin,
            )
            other_questions.append(question)
        FacultyContribution.objects.filter(pk=self.other_contribution.id).update(
            status=FacultyContribution.Status.SUBMITTED, submitted_at=timezone.now(),
        )
        original_case = ExamScenario.objects.create(
            blueprint=self.blueprint, section=self.section_a, title="Shared correction Case",
            stimulus="Preserve this shared Case.", created_by=self.admin, updated_by=self.admin,
        )
        original_ids = [row.question_id for row in old_members[:3]] + [
            question.id for question in other_questions
        ]
        ExamScenarioMember.objects.bulk_create([
            ExamScenarioMember(scenario=original_case, question_id=question_id, position=position)
            for position, question_id in enumerate(original_ids, start=1)
        ])
        reconcile(self.parent)
        r1 = self.generate()
        self.assertEqual(GeneratedExamItem.objects.filter(
            generated_set__generation_revision=r1, scenario_id_snapshot=original_case.id,
        ).count(), 10)
        self.cycle.status = ExaminationCycle.Status.CLOSED
        self.cycle.save(update_fields=["status", "updated_at"])
        self.configuration.refresh_from_db()
        AutomaticContributionReopenService.reopen(
            cycle_course_id=self.parent.id, tenant_id=self.tenant.id,
            actor=self.admin, expected_revision=self.configuration.revision,
            new_deadline=timezone.now() + timezone.timedelta(days=1),
            reason="Correct A's submitted Case questions.",
            selected_contribution_ids=[self.contribution.id],
        )
        first_draft = FacultyContribution.objects.get(supersedes=self.contribution)
        current_case = ExamScenario.objects.get(supersedes=original_case)
        owned_members = list(current_case.members.filter(
            question__contribution=first_draft, active_marker=1,
        ).order_by("position"))
        self.assertEqual(len(owned_members), 3)
        QuestionMutationService.delete_many(
            contribution_id=first_draft.id,
            selected_questions=[(row.question_id, row.question.revision) for row in owned_members],
            user=self.faculty, tenant_id=self.tenant.id, campus_id=self.campus.id,
            expected_contribution_revision=first_draft.revision,
        )
        self.assertEqual(list(current_case.members.filter(active_marker=1)
            .order_by("position").values_list("question_id", flat=True)),
            [question.id for question in other_questions])
        replacements = []
        for index, section in enumerate((self.section_a, self.section_b, self.section_b)):
            first_draft.refresh_from_db()
            replacements.append(QuestionMutationService.create(
                contribution_id=first_draft.id, user=self.faculty,
                tenant_id=self.tenant.id, campus_id=self.campus.id,
                expected_contribution_revision=first_draft.revision,
                payload=self.payload(f"Standalone correction {index}"),
                section_id=section.id,
            ))
        first_draft.refresh_from_db()
        QuestionMutationService.submit(
            contribution_id=first_draft.id, user=self.faculty,
            tenant_id=self.tenant.id, campus_id=self.campus.id,
            expected_contribution_revision=first_draft.revision,
        )
        self.assertFalse(current_case.members.filter(
            active_marker=1, question__contribution=first_draft,
        ).exists())
        r2 = self.generate()
        self.assertEqual((r2.revision_number, r2.supersedes_id), (2, r1.id))
        for generated_set in r2.generated_sets.order_by("set_code"):
            self.assertEqual(list(GeneratedExamItem.objects.filter(
                generated_set=generated_set, scenario_id_snapshot=current_case.id,
            ).order_by("position").values_list("source_question_id", flat=True)),
            [question.id for question in other_questions])

        self.configuration.refresh_from_db()
        AutomaticContributionReopenService.reopen(
            cycle_course_id=self.parent.id, tenant_id=self.tenant.id,
            actor=self.admin, expected_revision=self.configuration.revision,
            new_deadline=timezone.now() + timezone.timedelta(days=2),
            reason="Return only A's new standalone submission.",
            selected_contribution_ids=[first_draft.id],
        )
        second_draft = FacultyContribution.objects.get(supersedes=first_draft)
        self.assertFalse(ExamScenario.objects.filter(supersedes=current_case).exists())
        copied_standalone = second_draft.questions.get(
            question_text=replacements[0].question_text,
        )
        QuestionMutationService.delete_many(
            contribution_id=second_draft.id,
            selected_questions=[(copied_standalone.id, copied_standalone.revision)],
            user=self.faculty, tenant_id=self.tenant.id, campus_id=self.campus.id,
            expected_contribution_revision=second_draft.revision,
        )
        second_draft.refresh_from_db()
        current_case.refresh_from_db()
        case_state = (current_case.revision, current_case.active_marker, list(
            current_case.members.order_by("position").values_list(
                "id", "question_id", "position", "active_marker",
            )))
        r2_snapshots = list(GeneratedExamItem.objects.filter(
            generated_set__generation_revision=r2,
        ).order_by("id").values_list(
            "id", "scenario_id_snapshot", "source_question_id", "position", "question_text_snapshot",
        ))
        source_snapshots = list(GenerationSourceQuestionSnapshot.objects.filter(
            audit_snapshot=r2.source_audit_snapshot,
        ).order_by("id").values_list("id", "source_question_id_snapshot", "question_text_snapshot"))
        question_count = Question.objects.count()
        placement_count = QuestionBlueprintPlacement.objects.count()
        add_url = reverse("departmental_exams:faculty_case_question_create", args=[
            second_draft.id, current_case.id,
        ])
        workspace = self.client.get(reverse(
            "departmental_exams:contribution_workspace", args=[second_draft.id],
        ))
        self.assertEqual(workspace.status_code, 200)
        self.assertNotIn(add_url, workspace.content.decode())
        self.assertEqual(self.client.get(add_url).status_code, 404)
        self.assertEqual(self.client.post(add_url, {
            "expected_contribution_revision": second_draft.revision,
            "expected_scenario_revision": current_case.revision,
            "scenario_id": current_case.id, "section_id": self.section_a.id,
            "insert_position": 3, "question_text": "Rejected ancestral HTTP member",
            "choice_a": "One", "choice_b": "Two", "choice_c": "Three", "choice_d": "Four",
            "correct_answer": "A", "difficulty": "MODERATE", "content_format": "PLAIN_TEXT",
        }).status_code, 404)
        with self.assertRaises(Http404):
            QuestionMutationService.create(
                contribution_id=second_draft.id, user=self.faculty,
                tenant_id=self.tenant.id, campus_id=self.campus.id,
                expected_contribution_revision=second_draft.revision,
                payload=self.payload("Rejected ancestral service member"),
                section_id=self.section_a.id, scenario_id=current_case.id,
                expected_scenario_revision=current_case.revision, insert_position=3,
            )
        with self.assertRaisesMessage(
            ValidationError,
            "Only Submitted questions or their correction Draft copies",
        ):
            ExamScenarioMember(
                scenario=current_case,
                question=second_draft.questions.get(
                    question_text=replacements[1].question_text,
                ),
                position=3,
            ).full_clean()
        current_case.refresh_from_db()
        self.assertEqual((current_case.revision, current_case.active_marker, list(
            current_case.members.order_by("position").values_list(
                "id", "question_id", "position", "active_marker",
            ))), case_state)
        self.assertEqual(list(GeneratedExamItem.objects.filter(
            generated_set__generation_revision=r2,
        ).order_by("id").values_list(
            "id", "scenario_id_snapshot", "source_question_id", "position", "question_text_snapshot",
        )), r2_snapshots)
        self.assertEqual(list(GenerationSourceQuestionSnapshot.objects.filter(
            audit_snapshot=r2.source_audit_snapshot,
        ).order_by("id").values_list(
            "id", "source_question_id_snapshot", "question_text_snapshot",
        )), source_snapshots)
        self.assertEqual((Question.objects.count(), QuestionBlueprintPlacement.objects.count()),
                         (question_count, placement_count))

    def test_faculty_http_edit_of_copied_admin_case_member(self):
        r1, old_case, current_case, successor, old_ids, _deadline = self._released_reopened_admin_case()
        question = current_case.members.order_by("position").first().question
        url = reverse("departmental_exams:question_edit", args=[successor.id, question.id])
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        form = page.context["form"]
        self.assertEqual(int(form["scenario_id"].value()), current_case.id)
        payload = {name: getattr(question, name) for name in (
            "choice_a", "choice_b", "choice_c", "choice_d", "correct_answer", "difficulty", "content_format",
        )}
        payload.update({
            "question_text": "Corrected through the Faculty question form.",
            "section_id": form["section_id"].value(),
            "scenario_id": form["scenario_id"].value(),
            "expected_contribution_revision": form["expected_contribution_revision"].value(),
            "expected_question_revision": form["expected_question_revision"].value(),
            "expected_scenario_revision": form["expected_scenario_revision"].value(),
        })
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, 302)
        question.refresh_from_db()
        self.assertEqual(question.question_text, payload["question_text"])
        self.assertEqual(question.exam_scenario_membership.scenario_id, current_case.id)
        submit_url = reverse("departmental_exams:contribution_submit", args=[successor.id])
        submit_page = self.client.get(submit_url)
        self.assertEqual(submit_page.status_code, 200)
        self.assertEqual(self.client.post(submit_url, {
            "expected_contribution_revision": submit_page.context["form"]["expected_contribution_revision"].value(),
            "confirm_exact_quota": "on",
        }).status_code, 302)
        r2 = self.generate()
        self.assertEqual((r2.revision_number, r2.supersedes_id), (2, r1.id))
        self.assertTrue(GeneratedExamItem.objects.filter(
            generated_set__generation_revision=r2, scenario_id_snapshot=current_case.id,
            question_text_snapshot=payload["question_text"],
        ).exists())
        self.assertFalse(GeneratedExamItem.objects.filter(
            generated_set__generation_revision=r1, scenario_id_snapshot=old_case.id,
            question_text_snapshot=payload["question_text"],
        ).exists())
        self.assertEqual(list(old_case.members.order_by("position").values_list("question_id", flat=True)), old_ids)
        now = timezone.now()
        questionnaire = QuestionnairePrintReleaseService.release(
            cycle_course_id=self.parent.id, revision_id=r2.id,
            target_campus_id=self.campus.id,
            tenant_id=self.tenant.id, actor=self.admin,
            print_from=now, print_until=now + timezone.timedelta(days=1),
        )
        answer_key = AnswerKeyReleaseService.release(
            cycle_course_id=self.parent.id, revision_id=r2.id,
            tenant_id=self.tenant.id, actor=self.admin,
            available_from=now, available_until=now + timezone.timedelta(days=1),
            attestation_confirmed=True, target_campus_id=self.campus.id,
            recipient_course_id=self.parent.id,
        )
        self.assertEqual((questionnaire.generation_revision_id, answer_key.generation_revision_id), (r2.id, r2.id))

    def test_faculty_http_delete_last_admin_case_member_and_open_replacement(self):
        r1, old_case, current_case, successor, old_ids, deadline = self._released_reopened_admin_case()
        members = list(current_case.members.order_by("position"))
        bulk_url = reverse("departmental_exams:question_bulk_delete", args=[successor.id])
        response = self.client.post(bulk_url, {
            "expected_contribution_revision": successor.revision,
            "selected_questions": [f"{row.question_id}:{row.question.revision}" for row in members[:-1]],
        })
        self.assertEqual(response.status_code, 302)
        successor.refresh_from_db()
        last_question = members[-1].question
        delete_url = reverse("departmental_exams:question_delete", args=[successor.id, last_question.id])
        page = self.client.get(delete_url)
        self.assertEqual(page.status_code, 200)
        form = page.context["form"]
        response = self.client.post(delete_url, {
            "expected_contribution_revision": form["expected_contribution_revision"].value(),
            "expected_question_revision": form["expected_question_revision"].value(),
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(current_case.members.filter(active_marker=1).count(), 0)
        add_url = reverse("departmental_exams:faculty_case_question_create", args=[successor.id, current_case.id])
        workspace = self.client.get(reverse("departmental_exams:contribution_workspace", args=[successor.id]))
        self.assertEqual(workspace.status_code, 200)
        self.assertContains(workspace, add_url)
        self.assertContains(workspace, "Add corrected Linked Questions within this Case")
        _problem, readiness = Stage6ReadinessService.build_problem(cycle_course=self.parent)
        self.assertFalse(readiness["ready"])
        self.assertIn("CORRECTION_CASE_INCOMPLETE", {row["code"] for row in readiness["blockers"]})
        self.assertIsNone(AutomaticExamDeadlineService._close_due_intake(
            cycle_course_id=self.parent.id, tenant_id=self.tenant.id,
            now=deadline + timezone.timedelta(minutes=1),
        ))
        blocked = AutomaticExamDeadlineService.process_course(
            cycle_course_id=self.parent.id, tenant_id=self.tenant.id,
            now=deadline + timezone.timedelta(minutes=1),
        )
        self.assertNotEqual(blocked.status, "GENERATED")
        self.configuration.refresh_from_db()
        AutomaticContributionReopenService.reopen(
            cycle_course_id=self.parent.id, tenant_id=self.tenant.id,
            actor=self.admin, expected_revision=self.configuration.revision,
            new_deadline=timezone.now() + timezone.timedelta(days=2),
            reason="Give the existing Draft time to replace reviewer Case questions.",
            selected_contribution_ids=[],
        )
        self.configuration.refresh_from_db()
        self.assertEqual(self.configuration.automatic_processing_status, "")
        self.assertEqual(FacultyContribution.objects.filter(supersedes=self.contribution).count(), 1)
        self.assertEqual(ExamScenario.objects.filter(supersedes=old_case).count(), 1)
        created = []
        for index in range(5):
            page = self.client.get(add_url)
            self.assertEqual(page.status_code, 200)
            form = page.context["form"]
            position = 1 if index < 2 else index + 1
            response = self.client.post(add_url, {
                "expected_contribution_revision": form["expected_contribution_revision"].value(),
                "expected_scenario_revision": form["expected_scenario_revision"].value(),
                "scenario_id": form["scenario_id"].value(),
                "section_id": form["section_id"].value(),
                "insert_position": position,
                "question_text": f"Faculty corrected reviewer Case question {index}",
                "choice_a": "One", "choice_b": "Two", "choice_c": "Three", "choice_d": "Four",
                "correct_answer": "A", "difficulty": "MODERATE", "content_format": "PLAIN_TEXT",
            })
            self.assertEqual(response.status_code, 302, getattr(response, "context", None))
            created.append(Question.objects.get(contribution=successor,
                question_text=f"Faculty corrected reviewer Case question {index}").id)
        intended = [created[1], created[0], *created[2:]]
        self.assertEqual(list(current_case.members.order_by("position").values_list("question_id", flat=True)), intended)
        submit_url = reverse("departmental_exams:contribution_submit", args=[successor.id])
        submit_page = self.client.get(submit_url)
        self.assertEqual(self.client.post(submit_url, {
            "expected_contribution_revision": submit_page.context["form"]["expected_contribution_revision"].value(),
            "confirm_exact_quota": "on",
        }).status_code, 302)
        r2 = self.generate()
        self.assertEqual((r2.revision_number, r2.supersedes_id), (2, r1.id))
        for generated_set in r2.generated_sets.order_by("set_code"):
            self.assertEqual(list(GeneratedExamItem.objects.filter(
                generated_set=generated_set, scenario_id_snapshot=current_case.id,
            ).order_by("position").values_list("source_question_id", flat=True)), intended)
        self.assertEqual(list(old_case.members.order_by("position").values_list("question_id", flat=True)), old_ids)

    def test_admin_case_replacement_route_rejects_history_tampering_and_stale_versions(self):
        _r1, old_case, current_case, successor, _old_ids, _deadline = self._released_reopened_admin_case()
        add_url = reverse("departmental_exams:faculty_case_question_create", args=[successor.id, current_case.id])
        history_url = reverse("departmental_exams:faculty_case_question_create", args=[successor.id, old_case.id])
        removed_question = current_case.members.order_by("position").first().question
        delete_url = reverse("departmental_exams:question_delete", args=[successor.id, removed_question.id])
        delete_form = self.client.get(delete_url).context["form"]
        self.assertEqual(self.client.post(delete_url, {
            "expected_contribution_revision": delete_form["expected_contribution_revision"].value(),
            "expected_question_revision": delete_form["expected_question_revision"].value(),
        }).status_code, 302)
        self.assertEqual(self.client.get(history_url).status_code, 404)
        other_url = reverse("departmental_exams:faculty_case_question_create", args=[self.other_contribution.id, current_case.id])
        self.assertEqual(self.client.get(other_url).status_code, 404)
        question = current_case.members.order_by("position").first().question
        edit_url = reverse("departmental_exams:question_edit", args=[successor.id, question.id])
        edit_form = self.client.get(edit_url).context["form"]
        edit_payload = {
            "expected_contribution_revision": edit_form["expected_contribution_revision"].value(),
            "expected_question_revision": edit_form["expected_question_revision"].value(),
            "expected_scenario_revision": edit_form["expected_scenario_revision"].value(),
            "scenario_id": old_case.id,
            "section_id": edit_form["section_id"].value(),
            "question_text": "Tampered Case move", "choice_a": "One", "choice_b": "Two",
            "choice_c": "Three", "choice_d": "Four", "correct_answer": "A",
            "difficulty": "MODERATE", "content_format": "PLAIN_TEXT",
        }
        self.assertEqual(self.client.post(edit_url, edit_payload).status_code, 400)
        question.refresh_from_db()
        self.assertNotEqual(question.question_text, "Tampered Case move")
        # A later Case revision invalidates the rendered replacement form
        # without creating a question.
        add_form = self.client.get(add_url).context["form"]
        base = {
            "expected_contribution_revision": add_form["expected_contribution_revision"].value(),
            "expected_scenario_revision": add_form["expected_scenario_revision"].value(),
            "section_id": add_form["section_id"].value(), "insert_position": 1,
            "question_text": "Rejected stale replacement", "choice_a": "One",
            "choice_b": "Two", "choice_c": "Three", "choice_d": "Four",
            "correct_answer": "A", "difficulty": "MODERATE", "content_format": "PLAIN_TEXT",
        }
        self.assertEqual(self.client.post(add_url, {**base, "scenario_id": old_case.id}).status_code, 400)
        current_case.refresh_from_db()
        current_case.revision += 1
        current_case.save(update_fields=["revision", "updated_at"])
        self.assertEqual(self.client.post(add_url, {**base, "scenario_id": current_case.id}).status_code, 409)
        self.assertFalse(Question.objects.filter(question_text="Rejected stale replacement").exists())

    def test_mixed_and_multiple_selection_versions_once_and_deletes_only_active_member(self):
        cases = self.fill_and_submit()
        selected_members = list(cases[0].members.order_by("position"))
        selected_ids = [row.question_id for row in selected_members]
        ExamScenarioMember.objects.filter(pk__in=[row.id for row in selected_members]).delete()
        cases[0].delete()
        other = Question.objects.create(
            contribution=self.other_contribution, position=1,
            question_text="Other contributor Case member", choice_a="One", choice_b="Two",
            choice_c="Three", choice_d="Four", correct_answer="A", difficulty="EASY",
        )
        QuestionBlueprintPlacement.objects.create(
            blueprint=self.blueprint, section=self.section_a, question=other, placed_by=self.admin,
        )
        FacultyContribution.objects.filter(pk=self.other_contribution.id).update(
            status=FacultyContribution.Status.SUBMITTED, submitted_at=timezone.now(),
        )
        self.other_contribution.refresh_from_db()
        admin_case = ExamScenario.objects.create(
            blueprint=self.blueprint, section=self.section_a, title="Mixed Case",
            stimulus="Preserve mixed member order.", created_by=self.admin, updated_by=self.admin,
        )
        original_ids = [*selected_ids, other.id]
        ExamScenarioMember.objects.bulk_create([
            ExamScenarioMember(scenario=admin_case, question_id=qid, position=position)
            for position, qid in enumerate(original_ids, start=1)
        ])
        reconcile(self.parent)
        token = AutomaticContributionReopenService.state_token(self.parent)
        admin_case.revision += 1
        admin_case.save(update_fields=["revision", "updated_at"])
        deadline = timezone.now() + timezone.timedelta(days=1)
        with self.assertRaisesMessage(CourseExamConfigurationConflict, "Refresh and retry"):
            AutomaticContributionReopenService.reopen(
                cycle_course_id=self.parent.id, tenant_id=self.tenant.id,
                actor=self.admin, expected_revision=self.configuration.revision,
                expected_state_token=token, new_deadline=deadline,
                reason="Reject stale mixed Case confirmation.",
                selected_contribution_ids=[self.contribution.id],
            )
        with patch.object(AutomaticContributionReopenService, "_copy_admin_cases", side_effect=RuntimeError("forced failure")):
            with self.assertRaisesMessage(RuntimeError, "forced failure"):
                AutomaticContributionReopenService.reopen(
                    cycle_course_id=self.parent.id, tenant_id=self.tenant.id,
                    actor=self.admin, expected_revision=self.configuration.revision,
                    new_deadline=deadline, reason="Roll back failed Case version creation.",
                    selected_contribution_ids=[self.contribution.id],
                )
        self.contribution.refresh_from_db()
        self.assertEqual(self.contribution.active_marker, 1)
        self.assertFalse(FacultyContribution.objects.filter(supersedes=self.contribution).exists())
        AutomaticContributionReopenService.reopen(
            cycle_course_id=self.parent.id, tenant_id=self.tenant.id,
            actor=self.admin, expected_revision=self.configuration.revision,
            new_deadline=deadline, reason="Correct selected mixed Case members.",
            selected_contribution_ids=[self.contribution.id],
        )
        first_successor = FacultyContribution.objects.get(supersedes=self.contribution)
        first_case = ExamScenario.objects.get(supersedes=admin_case)
        first_ids = list(first_case.members.order_by("position").values_list("question_id", flat=True))
        self.assertEqual(first_ids[-1], other.id)
        self.assertEqual(first_ids[:-1], [
            first_successor.questions.get(position=Question.objects.get(pk=qid).position).id
            for qid in selected_ids
        ])
        self.configuration.refresh_from_db()
        with self.assertRaisesMessage(CourseExamConfigurationConflict, "editable Draft"):
            AutomaticContributionReopenService.reopen(
                cycle_course_id=self.parent.id, tenant_id=self.tenant.id,
                actor=self.admin, expected_revision=self.configuration.revision,
                new_deadline=timezone.now() + timezone.timedelta(days=2),
                reason="Preserve the unfinished Case Draft before another correction.",
                selected_contribution_ids=[self.other_contribution.id],
            )
        self.assertEqual(ExamScenario.objects.filter(blueprint=self.blueprint, active_marker=1,
            contribution__isnull=True).count(), 1)
        QuestionMutationService.submit(
            contribution_id=first_successor.id, user=self.faculty,
            tenant_id=self.tenant.id, campus_id=self.campus.id,
            expected_contribution_revision=first_successor.revision,
        )
        self.configuration.refresh_from_db()
        AutomaticContributionReopenService.reopen(
            cycle_course_id=self.parent.id, tenant_id=self.tenant.id,
            actor=self.admin, expected_revision=self.configuration.revision,
            new_deadline=timezone.now() + timezone.timedelta(days=2),
            reason="Correct both current submitted Case contributors.",
            selected_contribution_ids=[first_successor.id, self.other_contribution.id],
        )
        second_case = ExamScenario.objects.get(supersedes=first_case)
        second_ids = list(second_case.members.order_by("position").values_list("question_id", flat=True))
        self.assertEqual(len(second_ids), len(original_ids))
        self.assertEqual(ExamScenario.objects.filter(blueprint=self.blueprint, contribution__isnull=True, active_marker=1).count(), 1)
        self.assertEqual(ExamScenarioMember.objects.filter(scenario=second_case, active_marker=1).count(), len(original_ids))
        self.assertEqual(list(admin_case.members.order_by("position").values_list("question_id", flat=True)), original_ids)
        self.assertEqual(list(first_case.members.order_by("position").values_list("question_id", flat=True)), first_ids)
        latest = FacultyContribution.objects.get(supersedes=first_successor)
        other_latest = FacultyContribution.objects.get(supersedes=self.other_contribution)
        other_current_question = other_latest.questions.get(position=other.position)
        target = latest.questions.get(position=Question.objects.get(pk=selected_ids[0]).position)
        QuestionMutationService.delete_many(
            contribution_id=latest.id, selected_questions=[(target.id, target.revision)],
            user=self.faculty, tenant_id=self.tenant.id, campus_id=self.campus.id,
            expected_contribution_revision=latest.revision,
        )
        self.assertTrue(ExamScenario.objects.filter(pk=second_case.id, active_marker=1).exists())
        self.assertEqual(ExamScenarioMember.objects.filter(scenario=second_case, active_marker=1).count(), len(original_ids) - 1)
        self.assertEqual(list(admin_case.members.order_by("position").values_list("question_id", flat=True)), original_ids)
        replacement_url = reverse("departmental_exams:faculty_case_question_create", args=[latest.id, second_case.id])
        workspace = self.client.get(reverse("departmental_exams:contribution_workspace", args=[latest.id]))
        self.assertEqual(workspace.status_code, 200)
        self.assertContains(workspace, replacement_url)
        self.assertNotContains(workspace, "Other contributor Case member")
        form_page = self.client.get(replacement_url)
        self.assertEqual(form_page.status_code, 200)
        form = form_page.context["form"]
        response = self.client.post(replacement_url, {
            "expected_contribution_revision": form["expected_contribution_revision"].value(),
            "expected_scenario_revision": form["expected_scenario_revision"].value(),
            "scenario_id": form["scenario_id"].value(),
            "section_id": form["section_id"].value(),
            "insert_position": 2,
            "question_text": "Owner-only mixed Case replacement", "choice_a": "One",
            "choice_b": "Two", "choice_c": "Three", "choice_d": "Four",
            "correct_answer": "A", "difficulty": "MODERATE", "content_format": "PLAIN_TEXT",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(second_case.members.get(question_id=other_current_question.id).question_id, other_current_question.id)
        self.assertEqual(list(second_case.members.order_by("position").values_list("question_id", flat=True))[-1], other_current_question.id)
        self.assertTrue(ExamScenarioMember.objects.filter(scenario=admin_case, question_id=other.id,
            active_marker__isnull=True).exists())

    def test_closed_cycle_correction_copies_admin_case_graph(self):
        faculty_cases = self.fill_and_submit()
        old_members = list(faculty_cases[0].members.order_by("position"))
        old_ids = [row.question_id for row in old_members]
        ExamScenarioMember.objects.filter(pk__in=[row.id for row in old_members]).delete()
        faculty_cases[0].delete()
        admin_case = ExamScenario.objects.create(
            blueprint=self.blueprint, section=self.section_a, title="Reviewer Case",
            stimulus="Shared original narrative.", created_by=self.admin, updated_by=self.admin,
        )
        for position, question_id in enumerate(old_ids, start=1):
            ExamScenarioMember.objects.create(
                scenario=admin_case, question_id=question_id, position=position,
            )
        reconcile(self.parent)
        r1 = self.generate()
        self.configuration.refresh_from_db()
        r1_contents = list(GeneratedExamItem.objects.filter(
            generated_set__generation_revision=r1, scenario_id_snapshot=admin_case.id,
        ).order_by("generated_set_id", "position").values_list(
            "source_question_id", "question_text_snapshot", "scenario_stimulus_snapshot",
        ))
        self.assertEqual(len(r1_contents), 10)
        original_narrative = admin_case.stimulus
        self.cycle.status = ExaminationCycle.Status.CLOSED
        self.cycle.save(update_fields=["status", "updated_at"])
        AutomaticContributionReopenService.reopen(
            cycle_course_id=self.parent.id, tenant_id=self.tenant.id,
            actor=self.admin, expected_revision=self.configuration.revision,
            new_deadline=timezone.now() + timezone.timedelta(days=1),
            reason="Correct submitted reviewer Case questions.",
            selected_contribution_ids=[self.contribution.id],
        )
        successor = FacultyContribution.objects.get(supersedes=self.contribution)
        copied_ids = [successor.questions.get(position=Question.objects.get(pk=old_id).position).id
                      for old_id in old_ids]
        self.assertEqual(list(admin_case.members.order_by("position").values_list("question_id", flat=True)), old_ids)
        current_cases = ExamScenario.objects.filter(blueprint=self.blueprint, contribution__isnull=True).exclude(pk=admin_case.id)
        self.assertEqual(current_cases.count(), 1)
        current_case = current_cases.get()
        self.assertEqual(list(current_case.members.order_by("position").values_list("question_id", flat=True)), copied_ids)
        question = Question.objects.get(pk=copied_ids[0])
        payload = {name: getattr(question, name) for name in (
            "question_text", "choice_a", "choice_b", "choice_c", "choice_d",
            "correct_answer", "difficulty", "content_format",
        )}
        payload["question_text"] = "Corrected reviewer Case question."
        QuestionMutationService.update(
            contribution_id=successor.id, question_id=question.id,
            user=self.faculty, tenant_id=self.tenant.id, campus_id=self.campus.id,
            expected_contribution_revision=successor.revision,
            expected_question_revision=question.revision, payload=payload,
            section_id=self.section_a.id,
        )
        successor.refresh_from_db()
        QuestionMutationService.submit(
            contribution_id=successor.id, user=self.faculty,
            tenant_id=self.tenant.id, campus_id=self.campus.id,
            expected_contribution_revision=successor.revision,
        )
        due = timezone.now() + timezone.timedelta(days=2)
        self.assertIsNone(AutomaticExamDeadlineService._close_due_intake(
            cycle_course_id=self.parent.id, tenant_id=self.tenant.id, now=due,
        ))
        result = AutomaticExamDeadlineService.process_course(
            cycle_course_id=self.parent.id, tenant_id=self.tenant.id, now=due,
        )
        self.assertEqual(result.status, "GENERATED", result)
        r2 = ExamGenerationRevision.objects.get(cycle_course=self.parent, current_marker=1)
        self.assertEqual((r2.revision_number, r2.supersedes_id), (2, r1.id))
        r2_items = GeneratedExamItem.objects.filter(
            generated_set__generation_revision=r2, scenario_id_snapshot=current_case.id,
        )
        self.assertEqual(r2_items.count(), 10)
        for generated_set in r2.generated_sets.order_by("set_code"):
            self.assertEqual(list(r2_items.filter(generated_set=generated_set).order_by("position")
                .values_list("source_question_id", flat=True)), copied_ids)
        self.assertTrue(r2_items.filter(question_text_snapshot="Corrected reviewer Case question.").exists())
        admin_case.refresh_from_db()
        self.assertEqual(admin_case.stimulus, original_narrative)
        self.assertEqual(list(admin_case.members.order_by("position").values_list("question_id", flat=True)), old_ids)
        self.assertEqual(list(GeneratedExamItem.objects.filter(
            generated_set__generation_revision=r1, scenario_id_snapshot=admin_case.id,
        ).order_by("generated_set_id", "position").values_list(
            "source_question_id", "question_text_snapshot", "scenario_stimulus_snapshot",
        )), r1_contents)
        now = timezone.now()
        print_release = QuestionnairePrintReleaseService.release(
            cycle_course_id=self.parent.id, revision_id=r2.id,
            target_campus_id=self.campus.id,
            tenant_id=self.tenant.id, actor=self.admin,
            print_from=now, print_until=now + timezone.timedelta(days=1),
        )
        key_release = AnswerKeyReleaseService.release(
            cycle_course_id=self.parent.id, revision_id=r2.id,
            tenant_id=self.tenant.id, actor=self.admin,
            available_from=now, available_until=now + timezone.timedelta(days=1),
            attestation_confirmed=True, target_campus_id=self.campus.id,
            recipient_course_id=self.parent.id,
        )
        self.assertEqual((print_release.generation_revision_id, key_release.generation_revision_id), (r2.id, r2.id))
        self.configuration.refresh_from_db()
        third_deadline = timezone.now() + timezone.timedelta(days=2)
        AutomaticContributionReopenService.reopen(
            cycle_course_id=self.parent.id, tenant_id=self.tenant.id,
            actor=self.admin, expected_revision=self.configuration.revision,
            new_deadline=third_deadline,
            reason="Make another justified correction to the reviewer Case.",
            selected_contribution_ids=[successor.id],
        )
        print_release.refresh_from_db()
        key_release.refresh_from_db()
        self.assertEqual((print_release.status, key_release.status), ("REVOKED", "REVOKED"))
        third_case = ExamScenario.objects.get(supersedes=current_case)
        third_draft = FacultyContribution.objects.get(supersedes=successor)
        self.assertEqual(ExamScenario.objects.filter(
            blueprint=self.blueprint, contribution__isnull=True, active_marker=1,
        ).count(), 1)
        self.assertEqual(third_case.members.filter(active_marker=1).count(), len(old_ids))
        self.assertEqual(current_case.members.filter(active_marker__isnull=True).count(), len(old_ids))
        QuestionMutationService.submit(
            contribution_id=third_draft.id, user=self.faculty,
            tenant_id=self.tenant.id, campus_id=self.campus.id,
            expected_contribution_revision=third_draft.revision,
        )
        third_due = third_deadline + timezone.timedelta(minutes=1)
        self.assertIsNone(AutomaticExamDeadlineService._close_due_intake(
            cycle_course_id=self.parent.id, tenant_id=self.tenant.id, now=third_due,
        ))
        result = AutomaticExamDeadlineService.process_course(
            cycle_course_id=self.parent.id, tenant_id=self.tenant.id, now=third_due,
        )
        self.assertEqual(result.status, "GENERATED", result)
        r3 = ExamGenerationRevision.objects.get(cycle_course=self.parent, current_marker=1)
        self.assertEqual((r3.revision_number, r3.supersedes_id), (3, r2.id))
        self.assertEqual(GeneratedExamItem.objects.filter(
            generated_set__generation_revision=r3, scenario_id_snapshot=third_case.id,
        ).count(), 10)
        self.assertFalse(GeneratedExamItem.objects.filter(
            generated_set__generation_revision=r3,
            scenario_id_snapshot__in=[admin_case.id, current_case.id],
        ).exists())

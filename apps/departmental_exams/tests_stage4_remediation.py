"""Independent-review regression coverage for the Stage 4 remediation gate."""

import hashlib
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import FacultyAssignment
from apps.auditlog.models import AuditLog
from apps.core.services.settings import SystemSettingService

from .models import CourseExamConfiguration, CycleCourse, ExaminationCycle, FacultyContribution, Question
from .services import (
    CourseExamConfigurationReadinessService,
    CourseExamConfigurationService,
    CycleCourseAdministrationService,
    CycleCourseInclusionService,
    ExaminationCycleConfigurationService,
)
from .stage4_test_support import Stage4TestCase


class Stage4ModeConsistencyRemediationTests(Stage4TestCase):
    def _legacy_opened_configuration(self, *, mode=None, scope_suffix=None):
        cycle = self.make_cycle(mode=mode, scope_suffix=scope_suffix)
        parent = self.make_course(cycle=cycle)
        configuration = CourseExamConfiguration.objects.create(
            cycle_course=parent,
            final_item_count=40,
            questions_required_per_faculty=10,
            coverage="Legacy coverage",
            contribution_deadline=self.future_deadline(),
            item_count_mode_snapshot=mode,
            opened_at=timezone.now(),
            opened_by=self.admin,
        )
        return cycle, parent, configuration

    def _assert_null_to_fixed_is_rejected(self, *, cycle):
        before_configurations = list(
            CourseExamConfiguration.objects.filter(cycle_course__cycle=cycle)
            .order_by("id")
            .values_list("id", "revision", "workflow_status")
        )
        before_audit_count = AuditLog.objects.filter(
            action="DE_EXAM_CYCLE_CONFIGURATION_UPDATED",
            entity_id=str(cycle.id),
        ).count()
        with self.assertRaises(ValidationError):
            ExaminationCycleConfigurationService.save_cycle_configuration(
                cycle_id=cycle.id,
                tenant_id=self.tenant.id,
                user=self.manager,
                expected_updated_at=ExaminationCycleConfigurationService.transition_token(cycle),
                item_count_mode=ExaminationCycle.ItemCountMode.FIXED_ALL,
                fixed_final_item_count=50,
                contributor_instructions="",
            )
        cycle.refresh_from_db()
        self.assertIsNone(cycle.item_count_mode)
        self.assertIsNone(cycle.fixed_final_item_count)
        self.assertEqual(
            list(
                CourseExamConfiguration.objects.filter(cycle_course__cycle=cycle)
                .order_by("id")
                .values_list("id", "revision", "workflow_status")
            ),
            before_configurations,
        )
        self.assertEqual(
            AuditLog.objects.filter(
                action="DE_EXAM_CYCLE_CONFIGURATION_UPDATED",
                entity_id=str(cycle.id),
            ).count(),
            before_audit_count,
        )

    def test_null_mode_with_legacy_first_open_marker_cannot_select_a_mode(self):
        cycle, _parent, _configuration = self._legacy_opened_configuration()
        with self.assertRaises(ValidationError):
            ExaminationCycleConfigurationService.save_cycle_configuration(
                cycle_id=cycle.id, tenant_id=self.tenant.id, user=self.manager,
                expected_updated_at=ExaminationCycleConfigurationService.transition_token(cycle),
                item_count_mode=ExaminationCycle.ItemCountMode.PER_COURSE,
                fixed_final_item_count=None, contributor_instructions="",
            )

    def test_null_mode_with_contribution_activity_cannot_select_a_mode(self):
        cycle = self.make_cycle()
        parent = self.make_course(cycle=cycle)
        assignment = FacultyAssignment.objects.create(
            tenant=self.tenant, campus=self.campus,
            offering=parent.offering_snapshots.first().offering,
            faculty_user=self.admin,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
        )
        FacultyContribution.objects.create(
            cycle_course=parent, faculty_user=self.admin,
            source_assignment=assignment, source_campus=self.campus,
        )
        with self.assertRaises(ValidationError):
            ExaminationCycleConfigurationService.save_cycle_configuration(
                cycle_id=cycle.id, tenant_id=self.tenant.id, user=self.manager,
                expected_updated_at=ExaminationCycleConfigurationService.transition_token(cycle),
                item_count_mode=ExaminationCycle.ItemCountMode.PER_COURSE,
                fixed_final_item_count=None, contributor_instructions="",
            )

    def test_null_mode_with_question_activity_cannot_select_a_mode(self):
        cycle = self.make_cycle()
        parent = self.make_course(cycle=cycle)
        assignment = FacultyAssignment.objects.create(
            tenant=self.tenant, campus=self.campus,
            offering=parent.offering_snapshots.first().offering,
            faculty_user=self.admin,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
        )
        contribution = FacultyContribution.objects.create(
            cycle_course=parent, faculty_user=self.admin,
            source_assignment=assignment, source_campus=self.campus,
        )
        Question.objects.create(
            contribution=contribution, question_text="Question", choice_a="A", choice_b="B",
            choice_c="C", choice_d="D", correct_answer="A", difficulty=Question.Difficulty.EASY,
        )
        with self.assertRaises(ValidationError):
            ExaminationCycleConfigurationService.save_cycle_configuration(
                cycle_id=cycle.id, tenant_id=self.tenant.id, user=self.manager,
                expected_updated_at=ExaminationCycleConfigurationService.transition_token(cycle),
                item_count_mode=ExaminationCycle.ItemCountMode.PER_COURSE,
                fixed_final_item_count=None, contributor_instructions="",
            )

    def test_null_mode_with_legacy_first_open_marker_cannot_select_fixed_mode(self):
        cycle, _parent, _configuration = self._legacy_opened_configuration(
            scope_suffix="null-fixed-opened"
        )
        self._assert_null_to_fixed_is_rejected(cycle=cycle)

    def test_null_mode_with_contribution_activity_cannot_select_fixed_mode(self):
        cycle = self.make_cycle(scope_suffix="null-fixed-contribution")
        parent = self.make_course(cycle=cycle)
        assignment = FacultyAssignment.objects.create(
            tenant=self.tenant, campus=self.campus,
            offering=parent.offering_snapshots.first().offering,
            faculty_user=self.admin,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
        )
        FacultyContribution.objects.create(
            cycle_course=parent, faculty_user=self.admin,
            source_assignment=assignment, source_campus=self.campus,
        )
        self._assert_null_to_fixed_is_rejected(cycle=cycle)

    def test_null_mode_with_question_activity_cannot_select_fixed_mode(self):
        cycle = self.make_cycle(scope_suffix="null-fixed-question")
        parent = self.make_course(cycle=cycle)
        assignment = FacultyAssignment.objects.create(
            tenant=self.tenant, campus=self.campus,
            offering=parent.offering_snapshots.first().offering,
            faculty_user=self.admin,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
        )
        contribution = FacultyContribution.objects.create(
            cycle_course=parent, faculty_user=self.admin,
            source_assignment=assignment, source_campus=self.campus,
        )
        Question.objects.create(
            contribution=contribution, question_text="Question", choice_a="A", choice_b="B",
            choice_c="C", choice_d="D", correct_answer="A", difficulty=Question.Difficulty.EASY,
        )
        self._assert_null_to_fixed_is_rejected(cycle=cycle)

    def test_stale_per_course_snapshot_is_not_ready_under_fixed_cycle(self):
        cycle = self.make_cycle(mode=ExaminationCycle.ItemCountMode.FIXED_ALL, fixed=50)
        parent = self.make_course(cycle=cycle)
        configuration = CourseExamConfiguration.objects.create(
            cycle_course=parent, final_item_count=33, questions_required_per_faculty=10,
            coverage="Coverage", contribution_deadline=self.future_deadline(),
            item_count_mode_snapshot=ExaminationCycle.ItemCountMode.PER_COURSE,
        )
        readiness = CourseExamConfigurationReadinessService.evaluate_readiness(
            cycle_course=parent, configuration=configuration
        )
        self.assertIn("Needs Configuration", readiness["blockers"])
        self.assertFalse(readiness["can_open"])

    def test_stale_fixed_snapshot_is_not_ready_under_per_course_cycle(self):
        cycle = self.make_cycle(mode=ExaminationCycle.ItemCountMode.PER_COURSE)
        parent = self.make_course(cycle=cycle)
        configuration = CourseExamConfiguration.objects.create(
            cycle_course=parent, final_item_count=50, questions_required_per_faculty=10,
            coverage="Coverage", contribution_deadline=self.future_deadline(),
            item_count_mode_snapshot=ExaminationCycle.ItemCountMode.FIXED_ALL,
        )
        readiness = CourseExamConfigurationReadinessService.evaluate_readiness(
            cycle_course=parent, configuration=configuration
        )
        self.assertIn("Needs Configuration", readiness["blockers"])

    def test_authorized_draft_save_explicitly_synchronizes_stale_fixed_snapshot_without_opening(self):
        cycle = self.make_cycle(mode=ExaminationCycle.ItemCountMode.FIXED_ALL, fixed=50)
        parent = self.make_course(cycle=cycle)
        configuration = CourseExamConfiguration.objects.create(
            cycle_course=parent, final_item_count=33, questions_required_per_faculty=10,
            coverage="Coverage", contribution_deadline=self.future_deadline(),
            item_count_mode_snapshot=ExaminationCycle.ItemCountMode.PER_COURSE,
        )
        configuration, changed = CourseExamConfigurationService.save_course_draft(
            cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.configurer,
            expected_revision=configuration.revision, final_item_count=33,
            questions_required_per_faculty=10, coverage="Coverage",
            additional_instructions="", contribution_deadline=configuration.contribution_deadline,
        )
        self.assertTrue(changed)
        self.assertEqual(configuration.item_count_mode_snapshot, ExaminationCycle.ItemCountMode.FIXED_ALL)
        self.assertEqual(configuration.final_item_count, 50)
        self.assertEqual(configuration.workflow_status, CourseExamConfiguration.WorkflowStatus.DRAFT)

    def test_restore_preserves_stale_snapshot_and_does_not_open_the_configuration(self):
        cycle = self.make_cycle(mode=ExaminationCycle.ItemCountMode.FIXED_ALL, fixed=50)
        parent = self.make_course(cycle=cycle)
        parent.inclusion_status = CycleCourse.InclusionStatus.EXEMPT
        parent.exemption_category = CycleCourse.ExemptionCategory.INTERNSHIP
        parent.exemption_reason = "Approved written-exam exemption reason"
        parent.exemption_changed_by = self.admin
        parent.exemption_changed_at = timezone.now()
        parent.save()
        configuration = CourseExamConfiguration.objects.create(
            cycle_course=parent, final_item_count=33, questions_required_per_faculty=10,
            coverage="Dormant coverage", contribution_deadline=self.future_deadline(),
            item_count_mode_snapshot=ExaminationCycle.ItemCountMode.PER_COURSE,
        )
        CycleCourseInclusionService.restore(
            cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.configurer,
            expected_updated_at=CycleCourseInclusionService.transition_token(parent),
            reason="The approved exemption no longer applies to this course.",
        )
        configuration.refresh_from_db()
        readiness = CourseExamConfigurationReadinessService.evaluate_readiness(
            cycle_course=parent, configuration=configuration
        )
        self.assertIn("Needs Configuration", readiness["blockers"])
        self.assertEqual(configuration.workflow_status, CourseExamConfiguration.WorkflowStatus.DRAFT)

    def test_null_responsibility_assignment_preserves_stale_snapshot_without_opening(self):
        cycle = self.make_cycle(mode=ExaminationCycle.ItemCountMode.FIXED_ALL, fixed=50)
        parent = self.make_course(cycle=cycle, department=None)
        configuration = CourseExamConfiguration.objects.create(
            cycle_course=parent, final_item_count=33, questions_required_per_faculty=10,
            coverage="Dormant coverage", contribution_deadline=self.future_deadline(),
            item_count_mode_snapshot=ExaminationCycle.ItemCountMode.PER_COURSE,
        )
        CycleCourseAdministrationService.update_responsibility(
            cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.admin,
            responsible_department=self.department, reviewer=None,
        )
        configuration.refresh_from_db()
        self.assertEqual(configuration.item_count_mode_snapshot, ExaminationCycle.ItemCountMode.PER_COURSE)
        self.assertEqual(configuration.workflow_status, CourseExamConfiguration.WorkflowStatus.DRAFT)

    def test_department_reactivation_preserves_stale_snapshot_without_opening(self):
        cycle = self.make_cycle(mode=ExaminationCycle.ItemCountMode.FIXED_ALL, fixed=50)
        parent = self.make_course(cycle=cycle, department=self.other_department)
        self.other_department.is_active = False
        self.other_department.save(update_fields=["is_active"])
        configuration = CourseExamConfiguration.objects.create(
            cycle_course=parent, final_item_count=33, questions_required_per_faculty=10,
            coverage="Dormant coverage", contribution_deadline=self.future_deadline(),
            item_count_mode_snapshot=ExaminationCycle.ItemCountMode.PER_COURSE,
        )
        self.other_department.is_active = True
        self.other_department.save(update_fields=["is_active"])
        configuration.refresh_from_db()
        readiness = CourseExamConfigurationReadinessService.evaluate_readiness(
            cycle_course=parent, configuration=configuration
        )
        self.assertIn("Needs Configuration", readiness["blockers"])
        self.assertEqual(configuration.workflow_status, CourseExamConfiguration.WorkflowStatus.DRAFT)


class Stage4ClosedCycleAndActorRemediationTests(Stage4TestCase):
    def _closed_configuration(
        self, *, status=CourseExamConfiguration.WorkflowStatus.CLOSED, scope_suffix=None
    ):
        cycle = self.make_cycle(
            status=ExaminationCycle.Status.CLOSED,
            mode=ExaminationCycle.ItemCountMode.PER_COURSE,
            scope_suffix=scope_suffix,
        )
        parent = self.make_course(cycle=cycle)
        configuration = CourseExamConfiguration.objects.create(
            cycle_course=parent, final_item_count=40, questions_required_per_faculty=10,
            coverage="Coverage", contribution_deadline=self.future_deadline(),
            item_count_mode_snapshot=ExaminationCycle.ItemCountMode.PER_COURSE,
            workflow_status=status,
        )
        return cycle, parent, configuration

    def test_closed_cycle_save_is_rejected_without_revision_or_audit_change(self):
        _cycle, parent, configuration = self._closed_configuration(status=CourseExamConfiguration.WorkflowStatus.DRAFT)
        with self.assertRaises(ValidationError):
            CourseExamConfigurationService.save_course_draft(
                cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.configurer,
                expected_revision=configuration.revision, final_item_count=41,
                questions_required_per_faculty=10, coverage="Coverage", additional_instructions="",
                contribution_deadline=configuration.contribution_deadline,
            )
        configuration.refresh_from_db()
        self.assertEqual(configuration.revision, 1)
        self.assertFalse(AuditLog.objects.filter(entity_id=str(configuration.id)).exists())

    def _assert_closed_workflow_rejection(self, *, parent, configuration, method, **kwargs):
        before = {
            "workflow_status": configuration.workflow_status,
            "revision": configuration.revision,
            "opened_at": configuration.opened_at,
            "opened_by_id": configuration.opened_by_id,
            "closed_at": configuration.closed_at,
            "closed_by_id": configuration.closed_by_id,
            "audit_count": AuditLog.objects.filter(entity_id=str(configuration.id)).count(),
        }
        with self.assertRaises(ValidationError):
            method(
                cycle_course_id=parent.id, tenant_id=self.tenant.id,
                user=self.configurer, expected_revision=configuration.revision, **kwargs,
            )
        configuration.refresh_from_db()
        self.assertEqual(configuration.workflow_status, before["workflow_status"])
        self.assertEqual(configuration.revision, before["revision"])
        self.assertEqual(configuration.opened_at, before["opened_at"])
        self.assertEqual(configuration.opened_by_id, before["opened_by_id"])
        self.assertEqual(configuration.closed_at, before["closed_at"])
        self.assertEqual(configuration.closed_by_id, before["closed_by_id"])
        self.assertEqual(
            AuditLog.objects.filter(entity_id=str(configuration.id)).count(),
            before["audit_count"],
        )

    def test_closed_cycle_rejects_open(self):
        _cycle, parent, configuration = self._closed_configuration(
            status=CourseExamConfiguration.WorkflowStatus.DRAFT,
            scope_suffix="closed-open",
        )
        self._assert_closed_workflow_rejection(
            parent=parent, configuration=configuration,
            method=CourseExamConfigurationService.open_for_contribution,
        )

    def test_closed_cycle_rejects_close(self):
        _cycle, parent, configuration = self._closed_configuration(
            status=CourseExamConfiguration.WorkflowStatus.OPEN,
            scope_suffix="closed-close",
        )
        self._assert_closed_workflow_rejection(
            parent=parent, configuration=configuration,
            method=CourseExamConfigurationService.close_contribution,
            reason="Closing is prohibited because the cycle is closed",
        )

    def test_closed_cycle_rejects_reopen(self):
        _cycle, parent, configuration = self._closed_configuration(
            scope_suffix="closed-reopen",
        )
        self._assert_closed_workflow_rejection(
            parent=parent, configuration=configuration,
            method=CourseExamConfigurationService.reopen_contribution,
        )

    def test_closed_cycle_rejects_revert(self):
        _cycle, parent, configuration = self._closed_configuration(
            scope_suffix="closed-revert",
        )
        self._assert_closed_workflow_rejection(
            parent=parent, configuration=configuration,
            method=CourseExamConfigurationService.revert_unpublished_configuration,
            reason="Reverting is prohibited because the cycle is closed",
        )

    def test_closed_cycle_responsibility_reassignment_is_rejected_without_audit(self):
        _cycle, parent, _configuration = self._closed_configuration()
        with self.assertRaises(ValidationError):
            CycleCourseAdministrationService.update_responsibility(
                cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.admin,
                responsible_department=self.other_department, reviewer=None,
            )
        parent.refresh_from_db()
        self.assertEqual(parent.responsible_department_id, self.department.id)
        self.assertFalse(AuditLog.objects.filter(action="DE_EXAM_CYCLE_COURSE_ADMIN_UPDATED").exists())

    def test_closed_cycle_routes_do_not_offer_mutation_confirmation(self):
        _cycle, parent, configuration = self._closed_configuration()
        before_audit_count = AuditLog.objects.filter(entity_id=str(configuration.id)).count()
        self.client.force_login(self.configurer)
        self.assertEqual(self.client.post(reverse("departmental_exams:course_configuration", args=[parent.id]), {"expected_revision": 1}).status_code, 404)
        for route_name, payload in (
            ("departmental_exams:course_contribution_open", {"expected_revision": 1}),
            ("departmental_exams:course_contribution_close", {"expected_revision": 1, "reason": "Closing is prohibited because the cycle is closed"}),
            ("departmental_exams:course_contribution_reopen", {"expected_revision": 1}),
            ("departmental_exams:course_configuration_revert", {"expected_revision": 1, "reason": "Reverting is prohibited because the cycle is closed"}),
        ):
            self.assertEqual(self.client.get(reverse(route_name, args=[parent.id])).status_code, 404)
            self.assertEqual(self.client.post(reverse(route_name, args=[parent.id]), payload).status_code, 404)
        configuration.refresh_from_db()
        self.assertEqual(configuration.workflow_status, CourseExamConfiguration.WorkflowStatus.CLOSED)
        self.assertEqual(configuration.revision, 1)
        self.assertIsNone(configuration.opened_at)
        self.assertIsNone(configuration.opened_by_id)
        self.assertIsNone(configuration.closed_at)
        self.assertIsNone(configuration.closed_by_id)
        self.assertEqual(
            AuditLog.objects.filter(entity_id=str(configuration.id)).count(),
            before_audit_count,
        )

    def test_inactive_superuser_cannot_save_cycle_configuration(self):
        cycle = self.make_cycle()
        self.admin.is_active = False
        self.admin.save(update_fields=["is_active"])
        with self.assertRaises(PermissionDenied):
            ExaminationCycleConfigurationService.save_cycle_configuration(
                cycle_id=cycle.id, tenant_id=self.tenant.id, user=self.admin,
                expected_updated_at=ExaminationCycleConfigurationService.transition_token(cycle),
                item_count_mode=ExaminationCycle.ItemCountMode.PER_COURSE,
                fixed_final_item_count=None, contributor_instructions="",
            )

    def test_inactive_superuser_cannot_transition_cycle(self):
        cycle = self.make_cycle(mode=ExaminationCycle.ItemCountMode.PER_COURSE)
        self.admin.is_active = False
        self.admin.save(update_fields=["is_active"])
        with self.assertRaises(PermissionDenied):
            ExaminationCycleConfigurationService.open_cycle(
                cycle_id=cycle.id, tenant_id=self.tenant.id, user=self.admin,
                expected_updated_at=ExaminationCycleConfigurationService.transition_token(cycle),
            )

    def test_inactive_superuser_cannot_save_course_draft(self):
        cycle = self.make_cycle(mode=ExaminationCycle.ItemCountMode.PER_COURSE)
        parent = self.make_course(cycle=cycle)
        self.admin.is_active = False
        self.admin.save(update_fields=["is_active"])
        with self.assertRaises(PermissionDenied):
            CourseExamConfigurationService.save_course_draft(
                cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.admin,
                expected_revision=0, final_item_count=40, questions_required_per_faculty=10,
                coverage="Coverage", additional_instructions="", contribution_deadline=self.future_deadline(),
            )

    def test_active_superuser_cannot_draft_save_with_null_responsibility(self):
        cycle = self.make_cycle(mode=ExaminationCycle.ItemCountMode.PER_COURSE)
        parent = self.make_course(cycle=cycle, department=None)
        with self.assertRaises(ValidationError):
            CourseExamConfigurationService.save_course_draft(
                cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.admin,
                expected_revision=0, final_item_count=40, questions_required_per_faculty=10,
                coverage="Coverage", additional_instructions="", contribution_deadline=self.future_deadline(),
            )

    def test_active_superuser_cannot_draft_save_with_inactive_responsibility(self):
        cycle = self.make_cycle(mode=ExaminationCycle.ItemCountMode.PER_COURSE)
        parent = self.make_course(cycle=cycle, department=self.other_department)
        self.other_department.is_active = False
        self.other_department.save(update_fields=["is_active"])
        with self.assertRaises(ValidationError):
            CourseExamConfigurationService.save_course_draft(
                cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.admin,
                expected_revision=0, final_item_count=40, questions_required_per_faculty=10,
                coverage="Coverage", additional_instructions="", contribution_deadline=self.future_deadline(),
            )

    def _open_cycle_configuration_with_department(self, *, department, workflow_status):
        cycle = self.make_cycle(
            status=ExaminationCycle.Status.OPEN,
            mode=ExaminationCycle.ItemCountMode.PER_COURSE,
        )
        parent = self.make_course(cycle=cycle, department=department)
        configuration = CourseExamConfiguration.objects.create(
            cycle_course=parent, final_item_count=40, questions_required_per_faculty=10,
            coverage="Coverage", contribution_deadline=self.future_deadline(),
            item_count_mode_snapshot=ExaminationCycle.ItemCountMode.PER_COURSE,
            workflow_status=workflow_status,
        )
        return parent, configuration

    def test_null_responsibility_cannot_close_contribution(self):
        parent, configuration = self._open_cycle_configuration_with_department(
            department=None, workflow_status=CourseExamConfiguration.WorkflowStatus.OPEN
        )
        with self.assertRaises(ValidationError):
            CourseExamConfigurationService.close_contribution(
                cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.admin,
                expected_revision=configuration.revision, reason="Closing requires an active responsible department.",
            )

    def test_inactive_responsibility_cannot_reopen_contribution(self):
        parent, configuration = self._open_cycle_configuration_with_department(
            department=self.other_department, workflow_status=CourseExamConfiguration.WorkflowStatus.CLOSED
        )
        self.other_department.is_active = False
        self.other_department.save(update_fields=["is_active"])
        with self.assertRaises(ValidationError):
            CourseExamConfigurationService.reopen_contribution(
                cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.admin,
                expected_revision=configuration.revision,
            )

    def test_null_responsibility_cannot_revert_configuration(self):
        parent, configuration = self._open_cycle_configuration_with_department(
            department=None, workflow_status=CourseExamConfiguration.WorkflowStatus.CLOSED
        )
        with self.assertRaises(ValidationError):
            CourseExamConfigurationService.revert_unpublished_configuration(
                cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.admin,
                expected_revision=configuration.revision, reason="Reverting requires an active responsible department.",
            )

    def test_active_superuser_can_assign_initial_null_responsibility(self):
        cycle = self.make_cycle(mode=ExaminationCycle.ItemCountMode.PER_COURSE)
        parent = self.make_course(cycle=cycle, department=None)
        parent, changed = CycleCourseAdministrationService.update_responsibility(
            cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.admin,
            responsible_department=self.department, reviewer=None,
        )
        self.assertTrue(changed)
        self.assertEqual(parent.responsible_department_id, self.department.id)


class Stage4InstructionAuditRemediationTests(Stage4TestCase):
    def _open_configuration_with_instructions(self, instructions):
        cycle = self.make_cycle(
            mode=ExaminationCycle.ItemCountMode.PER_COURSE,
            instructions=instructions,
        )
        cycle, _ = ExaminationCycleConfigurationService.open_cycle(
            cycle_id=cycle.id, tenant_id=self.tenant.id, user=self.manager,
            expected_updated_at=ExaminationCycleConfigurationService.transition_token(cycle),
        )
        parent = self.make_course(cycle=cycle)
        configuration, _ = CourseExamConfigurationService.save_course_draft(
            cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.configurer,
            expected_revision=0, final_item_count=40, questions_required_per_faculty=10,
            coverage="Coverage", additional_instructions="", contribution_deadline=self.future_deadline(),
        )
        configuration, _ = CourseExamConfigurationService.open_for_contribution(
            cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.configurer,
            expected_revision=configuration.revision,
        )
        return configuration, AuditLog.objects.get(
            action="DE_EXAM_COURSE_CONTRIBUTION_OPENED"
        )

    def test_first_open_audit_records_hash_and_length_of_exact_frozen_instructions(self):
        instructions = "Exact frozen guidance"
        configuration, audit = self._open_configuration_with_instructions(instructions)
        self.assertEqual(configuration.contributor_instructions_snapshot, instructions)
        expected_hash = hashlib.sha256(instructions.encode("utf-8")).hexdigest()
        for payload in (audit.after_json, audit.metadata_json):
            self.assertEqual(payload["contributor_instructions_snapshot_sha256"], expected_hash)
            self.assertEqual(payload["contributor_instructions_snapshot_length"], len(instructions))
        for payload in (audit.before_json or {}, audit.after_json, audit.metadata_json):
            self.assertNotIn("contributor_instructions_snapshot", payload)
            self.assertNotIn("question_text", payload)
            self.assertNotIn("correct_answer", payload)
            self.assertNotIn("student_id", payload)

    def test_empty_instruction_snapshot_audit_evidence_is_deterministic_and_bounded(self):
        configuration, audit = self._open_configuration_with_instructions("")
        expected_hash = hashlib.sha256(b"").hexdigest()
        self.assertEqual(configuration.contributor_instructions_snapshot, "")
        for payload in (audit.after_json, audit.metadata_json):
            self.assertEqual(payload["contributor_instructions_snapshot_sha256"], expected_hash)
            self.assertEqual(payload["contributor_instructions_snapshot_length"], 0)
        for payload in (audit.before_json or {}, audit.after_json, audit.metadata_json):
            self.assertNotIn("contributor_instructions_snapshot", payload)


class Stage4LifecycleVisibilityRemediationTests(Stage4TestCase):
    def test_draft_cycle_does_not_render_contribution_actions_or_confirmation_route(self):
        cycle = self.make_cycle(mode=ExaminationCycle.ItemCountMode.PER_COURSE)
        parent = self.make_course(cycle=cycle)
        CourseExamConfiguration.objects.create(
            cycle_course=parent, final_item_count=40, questions_required_per_faculty=10,
            coverage="Coverage", contribution_deadline=self.future_deadline(),
            item_count_mode_snapshot=ExaminationCycle.ItemCountMode.PER_COURSE,
        )
        self.client.force_login(self.configurer)
        response = self.client.get(reverse("departmental_exams:course_configuration", args=[parent.id]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Open for Faculty Contribution")
        self.assertEqual(
            self.client.get(reverse("departmental_exams:course_contribution_open", args=[parent.id])).status_code,
            404,
        )

    def test_exempt_course_configuration_is_read_only_and_has_no_mutation_actions(self):
        cycle = self.make_cycle(mode=ExaminationCycle.ItemCountMode.PER_COURSE)
        parent = self.make_course(cycle=cycle)
        parent.inclusion_status = CycleCourse.InclusionStatus.EXEMPT
        parent.exemption_category = CycleCourse.ExemptionCategory.INTERNSHIP
        parent.exemption_reason = "Approved written-exam exemption reason"
        parent.exemption_changed_by = self.admin
        parent.exemption_changed_at = timezone.now()
        parent.save()
        self.client.force_login(self.configurer)
        response = self.client.get(reverse("departmental_exams:course_configuration", args=[parent.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "read-only")
        self.assertNotContains(response, "Save Draft Configuration")


class Stage4CycleAuditPrivacyRemediationTests(Stage4TestCase):
    def _save_cycle_configuration(self, cycle, *, instructions, expected_updated_at=None):
        return ExaminationCycleConfigurationService.save_cycle_configuration(
            cycle_id=cycle.id,
            tenant_id=self.tenant.id,
            user=self.manager,
            expected_updated_at=(
                expected_updated_at
                or ExaminationCycleConfigurationService.transition_token(cycle)
            ),
            item_count_mode=ExaminationCycle.ItemCountMode.PER_COURSE,
            fixed_final_item_count=None,
            contributor_instructions=instructions,
        )

    def _assert_bounded_cycle_instruction_evidence(self, payload, instructions):
        self.assertEqual(
            payload["contributor_instructions_sha256"],
            hashlib.sha256(instructions.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(payload["contributor_instructions_length"], len(instructions))
        self.assertNotIn("contributor_instructions", payload)
        self.assertNotIn("question_text", payload)
        self.assertNotIn("correct_answer", payload)
        self.assertNotIn("student_id", payload)

    def test_nonempty_cycle_instructions_are_persisted_with_bounded_audit_evidence(self):
        previous = "Previous cycle instructions"
        instructions = "Exact cycle contributor guidance"
        cycle = self.make_cycle(
            instructions=previous,
            scope_suffix="cycle-audit-nonempty",
        )
        expected_updated_at = ExaminationCycleConfigurationService.transition_token(cycle)

        cycle, changed = self._save_cycle_configuration(
            cycle,
            instructions=instructions,
            expected_updated_at=expected_updated_at,
        )

        self.assertTrue(changed)
        cycle.refresh_from_db()
        self.assertEqual(cycle.contributor_instructions, instructions)
        audit = AuditLog.objects.get(
            action="DE_EXAM_CYCLE_CONFIGURATION_UPDATED",
            entity_id=str(cycle.id),
        )
        self._assert_bounded_cycle_instruction_evidence(audit.before_json, previous)
        self._assert_bounded_cycle_instruction_evidence(audit.after_json, instructions)
        self.assertNotIn("contributor_instructions", audit.metadata_json)
        self.assertEqual(audit.metadata_json["expected_updated_at"], expected_updated_at)

    def test_empty_cycle_instructions_have_deterministic_bounded_audit_evidence(self):
        previous = "Instructions being cleared"
        cycle = self.make_cycle(
            instructions=previous,
            scope_suffix="cycle-audit-empty",
        )

        cycle, changed = self._save_cycle_configuration(cycle, instructions="")

        self.assertTrue(changed)
        cycle.refresh_from_db()
        self.assertEqual(cycle.contributor_instructions, "")
        audit = AuditLog.objects.get(
            action="DE_EXAM_CYCLE_CONFIGURATION_UPDATED",
            entity_id=str(cycle.id),
        )
        self._assert_bounded_cycle_instruction_evidence(audit.before_json, previous)
        self._assert_bounded_cycle_instruction_evidence(audit.after_json, "")
        self.assertNotIn("contributor_instructions", audit.metadata_json)

    def test_cycle_configuration_audit_failure_rolls_back_instruction_update(self):
        cycle = self.make_cycle(
            instructions="Persisted before audit failure",
            scope_suffix="cycle-audit-rollback",
        )

        with patch(
            "apps.departmental_exams.services.AuditService.log_event",
            side_effect=RuntimeError("audit failure"),
        ):
            with self.assertRaises(RuntimeError):
                self._save_cycle_configuration(
                    cycle,
                    instructions="Must not persist after audit failure",
                )

        cycle.refresh_from_db()
        self.assertIsNone(cycle.item_count_mode)
        self.assertIsNone(cycle.fixed_final_item_count)
        self.assertEqual(
            cycle.contributor_instructions,
            "Persisted before audit failure",
        )
        self.assertFalse(
            AuditLog.objects.filter(
                action="DE_EXAM_CYCLE_CONFIGURATION_UPDATED",
                entity_id=str(cycle.id),
            ).exists()
        )

    def test_unchanged_cycle_configuration_creates_no_additional_audit(self):
        instructions = "No-op cycle guidance"
        cycle = self.make_cycle(scope_suffix="cycle-audit-noop")
        cycle, changed = self._save_cycle_configuration(cycle, instructions=instructions)
        self.assertTrue(changed)
        before_audit_count = AuditLog.objects.filter(
            action="DE_EXAM_CYCLE_CONFIGURATION_UPDATED",
            entity_id=str(cycle.id),
        ).count()

        cycle, changed = self._save_cycle_configuration(cycle, instructions=instructions)

        self.assertFalse(changed)
        self.assertEqual(
            AuditLog.objects.filter(
                action="DE_EXAM_CYCLE_CONFIGURATION_UPDATED",
                entity_id=str(cycle.id),
            ).count(),
            before_audit_count,
        )


class Stage4CycleLifecycleRemediationTests(Stage4TestCase):
    def _configuration_url(self, cycle):
        return reverse("departmental_exams:cycle_configuration", args=[cycle.id])

    def _transition_url(self, cycle, action):
        return reverse(f"departmental_exams:cycle_{action}", args=[cycle.id])

    def _assert_rejected_transition_preserves_cycle(self, *, cycle, action):
        before_status = cycle.status
        before_audit_count = AuditLog.objects.filter(entity_id=str(cycle.id)).count()
        url = self._transition_url(cycle, action)

        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(
            self.client.post(
                url,
                {
                    "expected_updated_at": ExaminationCycleConfigurationService.transition_token(
                        cycle
                    )
                },
            ).status_code,
            404,
        )
        cycle.refresh_from_db()
        self.assertEqual(cycle.status, before_status)
        self.assertEqual(
            AuditLog.objects.filter(entity_id=str(cycle.id)).count(),
            before_audit_count,
        )

    def test_incomplete_draft_can_edit_but_hides_open_and_rejects_open_confirmation(self):
        cycle = self.make_cycle(scope_suffix="cycle-incomplete-draft")
        self.client.force_login(self.manager)

        response = self.client.get(self._configuration_url(cycle))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Save cycle configuration")
        self.assertNotContains(response, "Open cycle")
        self.assertNotContains(response, "Close cycle")
        self._assert_rejected_transition_preserves_cycle(cycle=cycle, action="open")

    def test_valid_draft_shows_open_and_hides_close(self):
        cycle = self.make_cycle(
            mode=ExaminationCycle.ItemCountMode.PER_COURSE,
            scope_suffix="cycle-valid-draft",
        )
        self.client.force_login(self.manager)

        response = self.client.get(self._configuration_url(cycle))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Save cycle configuration")
        self.assertContains(response, "Open cycle")
        self.assertNotContains(response, "Close cycle")

    def test_open_cycle_is_read_only_shows_close_and_rejects_open_confirmation(self):
        cycle = self.make_cycle(
            status=ExaminationCycle.Status.OPEN,
            mode=ExaminationCycle.ItemCountMode.PER_COURSE,
            scope_suffix="cycle-open",
        )
        self.client.force_login(self.manager)

        response = self.client.get(self._configuration_url(cycle))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cycle configuration is frozen.")
        self.assertNotContains(response, "Save cycle configuration")
        self.assertNotContains(response, "Open cycle")
        self.assertContains(response, "Close cycle")
        self._assert_rejected_transition_preserves_cycle(cycle=cycle, action="open")

    def test_draft_cycle_rejects_close_confirmation(self):
        cycle = self.make_cycle(
            mode=ExaminationCycle.ItemCountMode.PER_COURSE,
            scope_suffix="cycle-draft-close",
        )
        self.client.force_login(self.manager)

        self._assert_rejected_transition_preserves_cycle(cycle=cycle, action="close")

    def test_closed_cycle_hides_all_mutations_and_rejects_confirmations(self):
        cycle = self.make_cycle(
            status=ExaminationCycle.Status.CLOSED,
            mode=ExaminationCycle.ItemCountMode.PER_COURSE,
            scope_suffix="cycle-closed",
        )
        self.client.force_login(self.manager)

        response = self.client.get(self._configuration_url(cycle))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cycle configuration is frozen.")
        self.assertNotContains(response, "Save cycle configuration")
        self.assertNotContains(response, "Open cycle")
        self.assertNotContains(response, "Close cycle")
        self._assert_rejected_transition_preserves_cycle(cycle=cycle, action="open")
        self._assert_rejected_transition_preserves_cycle(cycle=cycle, action="close")
        before_audit_count = AuditLog.objects.filter(entity_id=str(cycle.id)).count()
        self.assertEqual(
            self.client.post(
                self._configuration_url(cycle),
                {"expected_updated_at": ExaminationCycleConfigurationService.transition_token(cycle)},
            ).status_code,
            404,
        )
        cycle.refresh_from_db()
        self.assertEqual(cycle.status, ExaminationCycle.Status.CLOSED)
        self.assertEqual(
            AuditLog.objects.filter(entity_id=str(cycle.id)).count(),
            before_audit_count,
        )

    def test_inactive_manager_cannot_see_or_access_cycle_mutations(self):
        cycle = self.make_cycle(
            mode=ExaminationCycle.ItemCountMode.PER_COURSE,
            scope_suffix="cycle-inactive-manager",
        )
        before_status = cycle.status
        before_configuration = (
            cycle.item_count_mode,
            cycle.fixed_final_item_count,
            cycle.contributor_instructions,
        )
        before_audit_count = AuditLog.objects.filter(entity_id=str(cycle.id)).count()
        self.manager.is_active = False
        self.manager.save(update_fields=["is_active"])
        self.client.force_login(self.manager)

        self.assertRedirects(
            self.client.get(self._configuration_url(cycle)),
            reverse("accounts:admin_login"),
            fetch_redirect_response=False,
        )
        self.assertRedirects(
            self.client.get(self._transition_url(cycle, "open")),
            reverse("accounts:admin_login"),
            fetch_redirect_response=False,
        )
        cycle.refresh_from_db()
        self.assertEqual(cycle.status, before_status)
        self.assertEqual(
            (
                cycle.item_count_mode,
                cycle.fixed_final_item_count,
                cycle.contributor_instructions,
            ),
            before_configuration,
        )
        self.assertEqual(
            AuditLog.objects.filter(entity_id=str(cycle.id)).count(),
            before_audit_count,
        )

    def test_inactive_superuser_cannot_see_or_access_cycle_mutations(self):
        cycle = self.make_cycle(
            mode=ExaminationCycle.ItemCountMode.PER_COURSE,
            scope_suffix="cycle-inactive-superuser",
        )
        before_status = cycle.status
        before_configuration = (
            cycle.item_count_mode,
            cycle.fixed_final_item_count,
            cycle.contributor_instructions,
        )
        before_audit_count = AuditLog.objects.filter(entity_id=str(cycle.id)).count()
        self.admin.is_active = False
        self.admin.save(update_fields=["is_active"])
        self.client.force_login(self.admin)

        self.assertRedirects(
            self.client.get(self._configuration_url(cycle)),
            reverse("accounts:admin_login"),
            fetch_redirect_response=False,
        )
        self.assertRedirects(
            self.client.get(self._transition_url(cycle, "open")),
            reverse("accounts:admin_login"),
            fetch_redirect_response=False,
        )
        cycle.refresh_from_db()
        self.assertEqual(cycle.status, before_status)
        self.assertEqual(
            (
                cycle.item_count_mode,
                cycle.fixed_final_item_count,
                cycle.contributor_instructions,
            ),
            before_configuration,
        )
        self.assertEqual(
            AuditLog.objects.filter(entity_id=str(cycle.id)).count(),
            before_audit_count,
        )

    def test_configurer_cannot_see_or_access_cycle_mutations(self):
        cycle = self.make_cycle(
            mode=ExaminationCycle.ItemCountMode.PER_COURSE,
            scope_suffix="cycle-configurer-denied",
        )
        self.client.force_login(self.configurer)

        self.assertEqual(self.client.get(self._configuration_url(cycle)).status_code, 403)
        self.assertEqual(
            self.client.get(self._transition_url(cycle, "open")).status_code,
            403,
        )

    def test_reviewer_cannot_see_or_access_cycle_mutations(self):
        cycle = self.make_cycle(
            mode=ExaminationCycle.ItemCountMode.PER_COURSE,
            scope_suffix="cycle-reviewer-denied",
        )
        self.client.force_login(self.reviewer)

        self.assertEqual(self.client.get(self._configuration_url(cycle)).status_code, 403)
        self.assertEqual(
            self.client.get(self._transition_url(cycle, "open")).status_code,
            403,
        )

    def test_feature_off_hides_and_denies_cycle_mutations(self):
        cycle = self.make_cycle(
            mode=ExaminationCycle.ItemCountMode.PER_COURSE,
            scope_suffix="cycle-feature-off",
        )
        SystemSettingService.set(
            "FEATURE_DEPARTMENTAL_EXAM_BUILDER_ENABLED",
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )
        self.client.force_login(self.manager)

        self.assertEqual(self.client.get(self._configuration_url(cycle)).status_code, 403)
        self.assertEqual(
            self.client.get(self._transition_url(cycle, "open")).status_code,
            403,
        )

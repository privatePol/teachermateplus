"""Service-level Stage 4 cycle, configuration, and contribution workflow tests."""

from unittest.mock import patch

from django.core.exceptions import ValidationError

from apps.auditlog.models import AuditLog

from .models import CourseExamConfiguration, CycleCourse, ExaminationCycle
from .services import (
    CourseExamConfigurationConflict,
    CourseExamConfigurationReadinessService,
    CourseExamConfigurationService,
    ExaminationCycleConfigurationService,
)
from .stage4_test_support import Stage4TestCase


class Stage4CycleConfigurationTests(Stage4TestCase):
    def test_mode_labels_and_open_close_transitions_are_explicit(self):
        self.assertEqual(ExaminationCycle.ItemCountMode.FIXED_ALL.label, "Fixed Item Count for All Courses")
        self.assertEqual(ExaminationCycle.ItemCountMode.PER_COURSE.label, "Configure Item Count per Course")
        cycle = self.make_cycle()
        token = ExaminationCycleConfigurationService.transition_token(cycle)
        cycle, changed = ExaminationCycleConfigurationService.save_cycle_configuration(
            cycle_id=cycle.id, tenant_id=self.tenant.id, user=self.manager, expected_updated_at=token,
            item_count_mode=ExaminationCycle.ItemCountMode.FIXED_ALL, fixed_final_item_count=50,
            contributor_instructions="Optional cycle guidance",
        )
        self.assertTrue(changed)
        cycle, changed = ExaminationCycleConfigurationService.open_cycle(
            cycle_id=cycle.id, tenant_id=self.tenant.id, user=self.manager,
            expected_updated_at=ExaminationCycleConfigurationService.transition_token(cycle),
        )
        self.assertTrue(changed)
        self.assertEqual(cycle.status, ExaminationCycle.Status.OPEN)
        cycle, changed = ExaminationCycleConfigurationService.close_cycle(
            cycle_id=cycle.id, tenant_id=self.tenant.id, user=self.manager,
            expected_updated_at=ExaminationCycleConfigurationService.transition_token(cycle),
        )
        self.assertTrue(changed)
        with self.assertRaises(ValidationError):
            ExaminationCycleConfigurationService.open_cycle(
                cycle_id=cycle.id, tenant_id=self.tenant.id, user=self.manager,
                expected_updated_at=ExaminationCycleConfigurationService.transition_token(cycle),
            )

    def test_invalid_fixed_configuration_and_stale_cycle_token_fail_without_audit(self):
        cycle = self.make_cycle()
        with self.assertRaises(ValidationError):
            ExaminationCycleConfigurationService.save_cycle_configuration(
                cycle_id=cycle.id, tenant_id=self.tenant.id, user=self.manager,
                expected_updated_at=ExaminationCycleConfigurationService.transition_token(cycle),
                item_count_mode=ExaminationCycle.ItemCountMode.FIXED_ALL,
                fixed_final_item_count=201, contributor_instructions="",
            )
        with self.assertRaises(CourseExamConfigurationConflict):
            ExaminationCycleConfigurationService.save_cycle_configuration(
                cycle_id=cycle.id, tenant_id=self.tenant.id, user=self.manager,
                expected_updated_at="stale", item_count_mode=ExaminationCycle.ItemCountMode.PER_COURSE,
                fixed_final_item_count=None, contributor_instructions="",
            )
        self.assertFalse(AuditLog.objects.filter(action="DE_EXAM_CYCLE_CONFIGURATION_UPDATED").exists())

    def test_fixed_and_per_course_switch_propagate_only_eligible_draft_rows(self):
        cycle = self.make_cycle()
        eligible = self.make_course(cycle=cycle, code="ELIGIBLE")
        null_row = self.make_course(cycle=cycle, department=None, code="NULL")
        inactive = self.make_course(cycle=cycle, department=self.other_department, code="INACTIVE")
        self.other_department.is_active = False
        self.other_department.save(update_fields=["is_active"])
        excluded = self.make_course(cycle=cycle, code="EXEMPT")
        excluded.inclusion_status = CycleCourse.InclusionStatus.EXEMPT
        excluded.exemption_category = CycleCourse.ExemptionCategory.INTERNSHIP
        excluded.exemption_reason = "Approved non-written assessment workflow"
        excluded.exemption_changed_by = self.admin
        from django.utils import timezone
        excluded.exemption_changed_at = timezone.now()
        excluded.save()
        dormant = CourseExamConfiguration.objects.create(cycle_course=null_row, final_item_count=33, questions_required_per_faculty=10, item_count_mode_snapshot=ExaminationCycle.ItemCountMode.PER_COURSE)
        cycle, _ = ExaminationCycleConfigurationService.save_cycle_configuration(
            cycle_id=cycle.id, tenant_id=self.tenant.id, user=self.manager,
            expected_updated_at=ExaminationCycleConfigurationService.transition_token(cycle),
            item_count_mode=ExaminationCycle.ItemCountMode.FIXED_ALL, fixed_final_item_count=60, contributor_instructions="",
        )
        self.assertEqual(CourseExamConfiguration.objects.get(cycle_course=eligible).final_item_count, 60)
        self.assertFalse(CourseExamConfiguration.objects.filter(cycle_course=excluded).exists())
        self.assertFalse(CourseExamConfiguration.objects.filter(cycle_course=inactive).exists())
        dormant.refresh_from_db()
        self.assertEqual(dormant.final_item_count, 33)
        cycle, _ = ExaminationCycleConfigurationService.save_cycle_configuration(
            cycle_id=cycle.id, tenant_id=self.tenant.id, user=self.manager,
            expected_updated_at=ExaminationCycleConfigurationService.transition_token(cycle),
            item_count_mode=ExaminationCycle.ItemCountMode.PER_COURSE, fixed_final_item_count=None, contributor_instructions="",
        )
        configuration = CourseExamConfiguration.objects.get(cycle_course=eligible)
        self.assertEqual(configuration.final_item_count, 60)
        self.assertEqual(configuration.item_count_mode_snapshot, ExaminationCycle.ItemCountMode.PER_COURSE)

    def test_mode_or_fixed_count_change_is_blocked_after_first_open(self):
        cycle = self.make_cycle(mode=ExaminationCycle.ItemCountMode.FIXED_ALL, fixed=40)
        parent = self.make_course(cycle=cycle)
        configuration = CourseExamConfiguration.objects.create(
            cycle_course=parent,
            final_item_count=40,
            questions_required_per_faculty=10,
            coverage="Coverage",
            contribution_deadline=self.future_deadline(),
            item_count_mode_snapshot=ExaminationCycle.ItemCountMode.FIXED_ALL,
            opened_at=self.future_deadline(),
            opened_by=self.admin,
        )
        with self.assertRaises(ValidationError):
            ExaminationCycleConfigurationService.save_cycle_configuration(
                cycle_id=cycle.id, tenant_id=self.tenant.id, user=self.manager,
                expected_updated_at=ExaminationCycleConfigurationService.transition_token(cycle),
                item_count_mode=ExaminationCycle.ItemCountMode.FIXED_ALL, fixed_final_item_count=50,
                contributor_instructions="",
            )
        self.assertEqual(configuration.final_item_count, 40)


class Stage4CourseWorkflowTests(Stage4TestCase):
    def _open_cycle_and_draft(self, *, instructions="Cycle instructions", scope_suffix=None):
        cycle = self.make_cycle(
            mode=ExaminationCycle.ItemCountMode.PER_COURSE,
            instructions=instructions,
            scope_suffix=scope_suffix,
        )
        cycle, _ = ExaminationCycleConfigurationService.open_cycle(
            cycle_id=cycle.id, tenant_id=self.tenant.id, user=self.manager,
            expected_updated_at=ExaminationCycleConfigurationService.transition_token(cycle),
        )
        parent = self.make_course(cycle=cycle)
        configuration, changed = CourseExamConfigurationService.save_course_draft(
            cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.configurer, expected_revision=0,
            final_item_count=50, questions_required_per_faculty=20, coverage="Core outcomes",
            additional_instructions="", contribution_deadline=self.future_deadline(),
        )
        self.assertTrue(changed)
        return cycle, parent, configuration

    def test_readiness_and_draft_save_keep_counts_separate_and_idempotent(self):
        cycle = self.make_cycle(mode=ExaminationCycle.ItemCountMode.PER_COURSE)
        parent = self.make_course(cycle=cycle)
        readiness = CourseExamConfigurationReadinessService.evaluate_readiness(cycle_course=parent)
        self.assertIn("Cycle Not Open", readiness["blockers"])
        self.assertIn("Needs Configuration", readiness["blockers"])
        cycle, parent, configuration = self._open_cycle_and_draft(scope_suffix="readiness-draft")
        self.assertEqual(configuration.final_item_count, 50)
        self.assertEqual(configuration.questions_required_per_faculty, 20)
        before_revision = configuration.revision
        configuration, changed = CourseExamConfigurationService.save_course_draft(
            cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.configurer,
            expected_revision=before_revision, final_item_count=50, questions_required_per_faculty=20,
            coverage="Core outcomes", additional_instructions="", contribution_deadline=configuration.contribution_deadline,
        )
        self.assertFalse(changed)
        self.assertEqual(configuration.revision, before_revision)

    def test_open_close_reopen_preserves_first_open_snapshot_and_is_idempotent(self):
        cycle, parent, configuration = self._open_cycle_and_draft(instructions="Frozen instructions")
        configuration, changed = CourseExamConfigurationService.open_for_contribution(
            cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.configurer, expected_revision=configuration.revision,
        )
        self.assertTrue(changed)
        self.assertEqual(configuration.workflow_status, CourseExamConfiguration.WorkflowStatus.OPEN)
        self.assertEqual(configuration.contributor_instructions_snapshot, "Frozen instructions")
        opened_at, opened_by_id = configuration.opened_at, configuration.opened_by_id
        configuration, changed = CourseExamConfigurationService.open_for_contribution(
            cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.configurer, expected_revision=configuration.revision,
        )
        self.assertFalse(changed)
        configuration, _ = CourseExamConfigurationService.close_contribution(
            cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.configurer,
            expected_revision=configuration.revision, reason="Closing before any contribution activity",
        )
        cycle.contributor_instructions = "Changed guidance"
        cycle.save(update_fields=["contributor_instructions"])
        configuration, _ = CourseExamConfigurationService.reopen_contribution(
            cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.configurer, expected_revision=configuration.revision,
        )
        self.assertEqual((configuration.opened_at, configuration.opened_by_id), (opened_at, opened_by_id))
        self.assertEqual(configuration.contributor_instructions_snapshot, "Frozen instructions")
        self.assertIsNone(configuration.closed_at)

    def test_closed_cycle_stale_revision_and_audit_failure_roll_back_course_write(self):
        cycle, parent, configuration = self._open_cycle_and_draft()
        with self.assertRaises(CourseExamConfigurationConflict):
            CourseExamConfigurationService.open_for_contribution(
                cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.configurer,
                expected_revision=configuration.revision + 1,
            )
        with patch("apps.departmental_exams.services.AuditService.log_event", side_effect=RuntimeError("audit failure")):
            with self.assertRaises(RuntimeError):
                CourseExamConfigurationService.open_for_contribution(
                    cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.configurer,
                    expected_revision=configuration.revision,
                )
        configuration.refresh_from_db()
        self.assertEqual(configuration.workflow_status, CourseExamConfiguration.WorkflowStatus.DRAFT)
        cycle.status = ExaminationCycle.Status.CLOSED
        cycle.save(update_fields=["status"])
        with self.assertRaises(ValidationError):
            CourseExamConfigurationService.save_course_draft(
                cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.configurer,
                expected_revision=configuration.revision, final_item_count=55, questions_required_per_faculty=20,
                coverage="Core outcomes", additional_instructions="", contribution_deadline=self.future_deadline(),
            )

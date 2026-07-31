"""Stage 4 course workflow regressions under CAO default/override policy."""

from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import CourseExamConfiguration, ExaminationCycle
from .services import (
    CourseExamConfigurationReadinessService,
    CourseExamConfigurationService,
    ExaminationCycleConfigurationService,
)
from .stage4_test_support import Stage4TestCase


class CAOReadinessTests(Stage4TestCase):
    def _open_cycle_with_defaults(self, *, quota=50, final_count=50):
        cycle = self.make_cycle()
        cycle, _ = ExaminationCycleConfigurationService.save_cycle_configuration(
            cycle_id=cycle.id, tenant_id=self.tenant.id, user=self.manager,
            expected_updated_at=ExaminationCycleConfigurationService.transition_token(cycle),
            default_questions_required_per_faculty=quota, default_final_item_count=final_count,
            contributor_instructions="CAO instructions",
        )
        cycle, _ = ExaminationCycleConfigurationService.open_cycle(
            cycle_id=cycle.id, tenant_id=self.tenant.id, user=self.manager,
            expected_updated_at=ExaminationCycleConfigurationService.transition_token(cycle),
        )
        return cycle

    def _draft_from_defaults(self, parent):
        return CourseExamConfigurationService.save_course_draft(
            cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.configurer,
            expected_revision=0, questions_required_per_faculty=50,
            questions_required_per_faculty_mode="DEFAULT", final_item_count=50,
            final_item_count_mode="DEFAULT", coverage="Core outcomes",
            additional_instructions="", contribution_deadline=self.future_deadline(),
        )[0]

    def test_each_default_source_and_revision_is_required_for_readiness(self):
        cycle = self._open_cycle_with_defaults()
        parent = self.make_course(cycle=cycle)
        configuration = self._draft_from_defaults(parent)
        self.assertTrue(CourseExamConfigurationReadinessService.evaluate_readiness(cycle_course=parent, configuration=configuration, user=self.configurer)["ready"])
        configuration.questions_required_per_faculty_source = None
        self.assertIn("Needs Configuration", CourseExamConfigurationReadinessService.evaluate_readiness(cycle_course=parent, configuration=configuration, user=self.configurer)["blockers"])
        configuration.questions_required_per_faculty_source = "DEFAULT"
        configuration.cycle_defaults_revision_snapshot = cycle.defaults_revision - 1
        self.assertIn("Needs Configuration", CourseExamConfigurationReadinessService.evaluate_readiness(cycle_course=parent, configuration=configuration, user=self.configurer)["blockers"])

    def test_default_staleness_is_per_field_and_overrides_are_not_compared(self):
        cycle = self._open_cycle_with_defaults()
        parent = self.make_course(cycle=cycle)
        configuration = self._draft_from_defaults(parent)
        configuration.questions_required_per_faculty = 60
        configuration.questions_required_per_faculty_source = "OVERRIDE"
        configuration.save(update_fields=["questions_required_per_faculty", "questions_required_per_faculty_source"])
        cycle.default_questions_required_per_faculty = 70
        cycle.defaults_revision += 1
        cycle.save(update_fields=["default_questions_required_per_faculty", "defaults_revision"])
        configuration.cycle_defaults_revision_snapshot = cycle.defaults_revision
        configuration.save(update_fields=["cycle_defaults_revision_snapshot"])
        readiness = CourseExamConfigurationReadinessService.evaluate_readiness(cycle_course=parent, configuration=configuration, user=self.configurer)
        self.assertTrue(readiness["ready"])
        cycle.default_final_item_count = 55
        cycle.defaults_revision += 1
        cycle.save(update_fields=["default_final_item_count", "defaults_revision"])
        readiness = CourseExamConfigurationReadinessService.evaluate_readiness(cycle_course=parent, configuration=configuration, user=self.configurer)
        self.assertIn("Needs Configuration", readiness["blockers"])

    def test_open_requires_valid_range_and_defers_contributor_existence(self):
        cycle = self._open_cycle_with_defaults()
        parent = self.make_course(cycle=cycle)
        configuration = self._draft_from_defaults(parent)
        configuration.final_item_count = None
        configuration.final_item_count_source = None
        configuration.save(update_fields=["final_item_count", "final_item_count_source"])
        with self.assertRaises(ValidationError):
            CourseExamConfigurationService.open_for_contribution(cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.configurer, expected_revision=configuration.revision)
        configuration.final_item_count = 50
        configuration.final_item_count_source = "DEFAULT"
        configuration.save(update_fields=["final_item_count", "final_item_count_source"])
        configuration, changed = CourseExamConfigurationService.open_for_contribution(cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.configurer, expected_revision=configuration.revision)
        self.assertTrue(changed)
        self.assertEqual(configuration.workflow_status, CourseExamConfiguration.WorkflowStatus.OPEN)


class CAOHistoricalImmutabilityTests(Stage4TestCase):
    def test_opened_configuration_is_never_rewritten_or_editable_after_revert(self):
        cycle = self.make_cycle(status=ExaminationCycle.Status.OPEN, default_questions_required_per_faculty=50, default_final_item_count=50)
        parent = self.make_course(cycle=cycle)
        configuration = self.make_configuration(parent, quota=75, final_count=60, opened_at=timezone.now())
        configuration.workflow_status = CourseExamConfiguration.WorkflowStatus.DRAFT
        configuration.save(update_fields=["workflow_status"])
        with self.assertRaises(ValidationError):
            CourseExamConfigurationService.save_course_draft(
                cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.configurer,
                expected_revision=configuration.revision, questions_required_per_faculty=50,
                questions_required_per_faculty_mode="DEFAULT", final_item_count=50,
                final_item_count_mode="DEFAULT", coverage="Core outcomes",
                additional_instructions="", contribution_deadline=self.future_deadline(),
            )
        cycle.default_questions_required_per_faculty = 60
        cycle.defaults_revision += 1
        cycle.save(update_fields=["default_questions_required_per_faculty", "defaults_revision"])
        configuration.refresh_from_db()
        self.assertEqual((configuration.questions_required_per_faculty, configuration.final_item_count), (75, 60))

    def test_close_and_reopen_preserve_count_and_source_snapshots(self):
        cycle = self.make_cycle(status=ExaminationCycle.Status.OPEN, default_questions_required_per_faculty=50, default_final_item_count=50)
        parent = self.make_course(cycle=cycle)
        configuration = self.make_configuration(parent, quota=75, final_count=60, opened_at=timezone.now(), workflow=CourseExamConfiguration.WorkflowStatus.OPEN)
        sources = (configuration.questions_required_per_faculty_source, configuration.final_item_count_source)
        configuration, _ = CourseExamConfigurationService.close_contribution(cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.configurer, expected_revision=configuration.revision, reason="Administrative closure before contribution begins")
        configuration, _ = CourseExamConfigurationService.reopen_contribution(cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.configurer, expected_revision=configuration.revision)
        self.assertEqual((configuration.questions_required_per_faculty, configuration.final_item_count), (75, 60))
        self.assertEqual((configuration.questions_required_per_faculty_source, configuration.final_item_count_source), sources)

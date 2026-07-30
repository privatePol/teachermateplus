"""Regression tests for CAO remediation boundaries and immutable snapshots."""

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.academics.models import FacultyAssignment

from .models import CourseExamConfiguration, CycleCourse, ExaminationCycle, FacultyContribution, Question
from .services import CourseExamConfigurationReadinessService, ExaminationCycleConfigurationService
from .stage4_test_support import Stage4TestCase


class CAOPropagationExclusionTests(Stage4TestCase):
    def _save(self, cycle, quota, final_count):
        return ExaminationCycleConfigurationService.save_cycle_configuration(
            cycle_id=cycle.id, tenant_id=self.tenant.id, user=self.manager,
            expected_updated_at=ExaminationCycleConfigurationService.transition_token(cycle),
            default_questions_required_per_faculty=quota, default_final_item_count=final_count,
            contributor_instructions="CAO instructions",
            reason="Open cycle correction is authorized" if cycle.status == ExaminationCycle.Status.OPEN else "",
        )[0]

    def test_open_closed_and_ever_opened_draft_rows_keep_historical_snapshots(self):
        cycle = self.make_cycle()
        open_parent = self.make_course(cycle=cycle, code="OPEN")
        closed_parent = self.make_course(cycle=cycle, code="CLOSED")
        reverted_parent = self.make_course(cycle=cycle, code="REVERTED")
        open_configuration = self.make_configuration(open_parent, quota=60, final_count=61, workflow=CourseExamConfiguration.WorkflowStatus.OPEN, opened_at=timezone.now())
        closed_configuration = self.make_configuration(closed_parent, quota=62, final_count=63, workflow=CourseExamConfiguration.WorkflowStatus.CLOSED, opened_at=timezone.now())
        reverted_configuration = self.make_configuration(reverted_parent, quota=64, final_count=65, opened_at=timezone.now())
        snapshots = {
            configuration.id: (
                configuration.questions_required_per_faculty,
                configuration.questions_required_per_faculty_source,
                configuration.final_item_count,
                configuration.final_item_count_source,
                configuration.cycle_defaults_revision_snapshot,
                configuration.revision,
            )
            for configuration in (open_configuration, closed_configuration, reverted_configuration)
        }
        cycle = self._save(cycle, 50, 50)
        for configuration in (open_configuration, closed_configuration, reverted_configuration):
            configuration.refresh_from_db()
            self.assertEqual(
                (
                    configuration.questions_required_per_faculty,
                    configuration.questions_required_per_faculty_source,
                    configuration.final_item_count,
                    configuration.final_item_count_source,
                    configuration.cycle_defaults_revision_snapshot,
                    configuration.revision,
                ),
                snapshots[configuration.id],
            )

    def test_exempt_null_and_inactive_responsibility_do_not_receive_new_defaults(self):
        cycle = self.make_cycle()
        exempt = self.make_course(cycle=cycle, code="EXEMPT")
        exempt.inclusion_status = CycleCourse.InclusionStatus.EXEMPT
        exempt.exemption_category = CycleCourse.ExemptionCategory.INTERNSHIP
        exempt.exemption_reason = "Approved alternative assessment pathway"
        exempt.exemption_changed_by = self.admin
        exempt.exemption_changed_at = timezone.now()
        exempt.save()
        null_parent = self.make_course(cycle=cycle, department=None, code="NULL")
        inactive = self.make_course(cycle=cycle, department=self.other_department, code="INACTIVE")
        self.other_department.is_active = False
        self.other_department.save(update_fields=["is_active"])
        self._save(cycle, 50, 50)
        self.assertFalse(CourseExamConfiguration.objects.filter(cycle_course=exempt).exists())
        self.assertFalse(CourseExamConfiguration.objects.filter(cycle_course=null_parent).exists())
        self.assertFalse(CourseExamConfiguration.objects.filter(cycle_course=inactive).exists())

    def test_contribution_and_question_activity_exclude_draft_rows_from_default_rewrite(self):
        cycle = self.make_cycle(default_questions_required_per_faculty=50, default_final_item_count=50)
        contribution_parent = self.make_course(cycle=cycle, code="CONTRIBUTION")
        question_parent = self.make_course(cycle=cycle, code="QUESTION")
        contribution_configuration = self.make_configuration(contribution_parent, quota_source="DEFAULT", final_source="DEFAULT")
        question_configuration = self.make_configuration(question_parent, quota_source="DEFAULT", final_source="DEFAULT")
        assignment_one = FacultyAssignment.objects.create(tenant=self.tenant, campus=self.campus, offering=contribution_parent.offering_snapshots.first().offering, faculty_user=self.admin, response_status=FacultyAssignment.ResponseStatus.ACCEPTED, accepted_at=timezone.now())
        assignment_two = FacultyAssignment.objects.create(tenant=self.tenant, campus=self.campus, offering=question_parent.offering_snapshots.first().offering, faculty_user=self.admin, response_status=FacultyAssignment.ResponseStatus.ACCEPTED, accepted_at=timezone.now())
        FacultyContribution.objects.create(cycle_course=contribution_parent, faculty_user=self.admin, source_assignment=assignment_one, source_campus=self.campus)
        question_contribution = FacultyContribution.objects.create(cycle_course=question_parent, faculty_user=self.admin, source_assignment=assignment_two, source_campus=self.campus)
        Question.objects.create(contribution=question_contribution, question_text="Which value is retained?", choice_a="A", choice_b="B", choice_c="C", choice_d="D", correct_answer="A", difficulty=Question.Difficulty.EASY)
        self._save(cycle, 60, 60)
        contribution_configuration.refresh_from_db()
        question_configuration.refresh_from_db()
        self.assertEqual((contribution_configuration.questions_required_per_faculty, contribution_configuration.final_item_count), (50, 50))
        self.assertEqual((question_configuration.questions_required_per_faculty, question_configuration.final_item_count), (50, 50))

    def test_restore_reactivation_and_reassignment_do_not_synchronize_stale_configuration(self):
        cycle = self.make_cycle(status=ExaminationCycle.Status.OPEN, default_questions_required_per_faculty=50, default_final_item_count=50)
        parent = self.make_course(cycle=cycle)
        configuration = self.make_configuration(parent, quota=60, final_count=60, quota_source="OVERRIDE", final_source="OVERRIDE")
        parent.inclusion_status = CycleCourse.InclusionStatus.EXEMPT
        parent.exemption_category = CycleCourse.ExemptionCategory.INTERNSHIP
        parent.exemption_reason = "Approved alternative assessment pathway"
        parent.exemption_changed_by = self.admin
        parent.exemption_changed_at = timezone.now()
        parent.save()
        parent.inclusion_status = CycleCourse.InclusionStatus.INCLUDED
        parent.exemption_category = ""
        parent.exemption_reason = ""
        parent.save(update_fields=["inclusion_status", "exemption_category", "exemption_reason"])
        cycle.default_questions_required_per_faculty = 70
        cycle.defaults_revision += 1
        cycle.save(update_fields=["default_questions_required_per_faculty", "defaults_revision"])
        configuration.refresh_from_db()
        self.assertEqual((configuration.questions_required_per_faculty, configuration.final_item_count), (60, 60))
        readiness = CourseExamConfigurationReadinessService.evaluate_readiness(cycle_course=parent, configuration=configuration, user=self.configurer)
        self.assertIn("Needs Configuration", readiness["blockers"])

    def test_restored_or_reactivated_missing_children_receive_current_defaults(self):
        cycle = self.make_cycle(
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
        )
        restored = self.make_course(cycle=cycle, code="RESTORED-MISSING")
        restored.inclusion_status = CycleCourse.InclusionStatus.EXEMPT
        restored.exemption_category = CycleCourse.ExemptionCategory.INTERNSHIP
        restored.exemption_reason = "Approved alternative assessment pathway"
        restored.exemption_changed_by = self.admin
        restored.exemption_changed_at = timezone.now()
        restored.save()
        restored.inclusion_status = CycleCourse.InclusionStatus.INCLUDED
        restored.exemption_category = ""
        restored.exemption_reason = ""
        restored.save(
            update_fields=["inclusion_status", "exemption_category", "exemption_reason"]
        )

        reactivated = self.make_course(
            cycle=cycle,
            department=self.other_department,
            code="REACTIVATED-MISSING",
        )
        self.other_department.is_active = False
        self.other_department.save(update_fields=["is_active"])
        self.other_department.is_active = True
        self.other_department.save(update_fields=["is_active"])

        self._save(cycle, 55, 60)
        for parent in (restored, reactivated):
            configuration = CourseExamConfiguration.objects.get(cycle_course=parent)
            self.assertEqual(
                (
                    configuration.questions_required_per_faculty,
                    configuration.questions_required_per_faculty_source,
                    configuration.final_item_count,
                    configuration.final_item_count_source,
                ),
                (55, "DEFAULT", 60, "DEFAULT"),
            )

    def test_existing_overrides_and_immutable_children_are_not_recreated(self):
        cycle = self.make_cycle(
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
        )
        overridden_parent = self.make_course(cycle=cycle, code="EXISTING-OVERRIDE")
        immutable_parent = self.make_course(cycle=cycle, code="IMMUTABLE")
        overridden = self.make_configuration(
            overridden_parent,
            quota=70,
            final_count=71,
            quota_source="OVERRIDE",
            final_source="OVERRIDE",
        )
        immutable = self.make_configuration(
            immutable_parent,
            quota=72,
            final_count=73,
            opened_at=timezone.now(),
        )
        self._save(cycle, 55, 60)
        overridden.refresh_from_db()
        immutable.refresh_from_db()
        self.assertEqual(
            CourseExamConfiguration.objects.filter(cycle_course=overridden_parent).count(),
            1,
        )
        self.assertEqual(
            CourseExamConfiguration.objects.filter(cycle_course=immutable_parent).count(),
            1,
        )
        self.assertEqual(
            (overridden.questions_required_per_faculty, overridden.final_item_count),
            (70, 71),
        )
        self.assertEqual(
            (immutable.questions_required_per_faculty, immutable.final_item_count),
            (72, 73),
        )


class CAOModelValidationTests(Stage4TestCase):
    def test_model_accepts_both_boundaries_with_explicit_sources(self):
        cycle = self.make_cycle(default_questions_required_per_faculty=50, default_final_item_count=75)
        parent = self.make_course(cycle=cycle)
        configuration = self.make_configuration(parent, quota=50, final_count=75, quota_source="DEFAULT", final_source="DEFAULT")
        configuration.full_clean()

    def test_model_rejects_all_values_outside_fifty_to_seventy_five(self):
        cycle = self.make_cycle(default_questions_required_per_faculty=50, default_final_item_count=50)
        parent = self.make_course(cycle=cycle)
        for field in ("questions_required_per_faculty", "final_item_count"):
            for value in (49, 76):
                configuration = self.make_configuration(parent, quota=50, final_count=50, quota_source="OVERRIDE", final_source="OVERRIDE")
                setattr(configuration, field, value)
                with self.assertRaises(ValidationError):
                    configuration.full_clean()
                configuration.delete()

    def test_opened_model_rejects_direct_count_or_source_mutation(self):
        cycle = self.make_cycle(default_questions_required_per_faculty=50, default_final_item_count=50)
        parent = self.make_course(cycle=cycle)
        configuration = self.make_configuration(parent, opened_at=timezone.now())
        configuration.final_item_count = 60
        with self.assertRaises(ValidationError):
            configuration.full_clean()

"""Focused Stage 4.1 behavior and UI regression tests.

Gate 2 adds this coverage without executing it; execution belongs to Gate 3.
"""

from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import FacultyAssignment

from .forms import CourseExamConfigurationForm
from .models import (
    CourseExamConfiguration,
    CycleCourse,
    ExaminationCycle,
    FacultyContribution,
    normalize_contribution_deadline_to_minute,
)
from .services import (
    CourseExamConfigurationConflict,
    CourseExamConfigurationReadinessService,
    CourseExamConfigurationService,
    ExaminationCycleConfigurationService,
)
from .stage4_test_support import Stage4TestCase


class Stage41DeadlineBehaviorTests(Stage4TestCase):
    def _save_cycle(self, cycle, *, deadline, reason=""):
        return ExaminationCycleConfigurationService.save_cycle_configuration(
            cycle_id=cycle.id,
            tenant_id=self.tenant.id,
            user=self.manager,
            expected_updated_at=ExaminationCycleConfigurationService.transition_token(cycle),
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=deadline,
            contributor_instructions="Stage 4.1 instructions",
            reason=reason,
        )

    def _save_course(self, parent, *, deadline, mode, expected_revision=0):
        return CourseExamConfigurationService.save_course_draft(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            user=self.configurer,
            expected_revision=expected_revision,
            questions_required_per_faculty=50,
            questions_required_per_faculty_mode="DEFAULT",
            final_item_count=50,
            final_item_count_mode="DEFAULT",
            contribution_deadline=deadline,
            contribution_deadline_mode=mode,
            coverage="Core outcomes",
            additional_instructions="",
        )

    def test_cycle_deadline_create_update_clear_propagates_and_revisions(self):
        cycle = self.make_cycle()
        parent = self.make_course(cycle=cycle)
        first = timezone.make_aware(
            datetime(2026, 8, 15, 17, 0, 45, 123456),
            ZoneInfo("Asia/Manila"),
        )
        normalized_first = first.replace(second=0, microsecond=0)
        with patch("apps.departmental_exams.services.AuditService.log_event") as audit:
            cycle, changed = self._save_cycle(cycle, deadline=first)
        configuration = CourseExamConfiguration.objects.get(cycle_course=parent)
        self.assertTrue(changed)
        self.assertEqual(cycle.defaults_revision, 1)
        self.assertTrue(timezone.is_aware(cycle.default_contribution_deadline))
        self.assertEqual(
            (
                timezone.localtime(cycle.default_contribution_deadline).hour,
                timezone.localtime(cycle.default_contribution_deadline).minute,
                cycle.default_contribution_deadline.second,
                cycle.default_contribution_deadline.microsecond,
            ),
            (17, 0, 0, 0),
        )
        self.assertEqual(
            (configuration.contribution_deadline, configuration.contribution_deadline_source),
            (normalized_first, "DEFAULT"),
        )
        self.assertEqual(
            audit.call_args.kwargs["after_data"]["default_contribution_deadline"],
            normalized_first,
        )
        first_revision = configuration.revision

        second = first + timezone.timedelta(days=1)
        cycle, _ = self._save_cycle(cycle, deadline=second)
        configuration.refresh_from_db()
        self.assertEqual(
            (configuration.contribution_deadline, configuration.contribution_deadline_source),
            (second.replace(second=0, microsecond=0), "DEFAULT"),
        )
        self.assertEqual(cycle.defaults_revision, 2)
        self.assertGreater(configuration.revision, first_revision)

        cycle, _ = self._save_cycle(cycle, deadline=None)
        configuration.refresh_from_db()
        self.assertEqual(cycle.defaults_revision, 3)
        self.assertIsNone(configuration.contribution_deadline)
        self.assertIsNone(configuration.contribution_deadline_source)
        readiness = CourseExamConfigurationReadinessService.evaluate_readiness(
            cycle_course=parent, configuration=configuration
        )
        self.assertIn("Needs Configuration", readiness["blockers"])

    def test_cycle_change_preserves_override_and_historical_or_exempt_rows(self):
        cycle = self.make_cycle(
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=self.future_deadline(),
        )
        override_parent = self.make_course(cycle=cycle, code="OVERRIDE")
        override = self.make_configuration(override_parent, deadline=self.future_deadline())
        historical_parent = self.make_course(cycle=cycle, code="HISTORY")
        historical = self.make_configuration(
            historical_parent, deadline=self.future_deadline(), opened_at=timezone.now()
        )
        exempt_parent = self.make_course(cycle=cycle, code="EXEMPT")
        exempt_parent.inclusion_status = CycleCourse.InclusionStatus.EXEMPT
        exempt_parent.exemption_category = CycleCourse.ExemptionCategory.INTERNSHIP
        exempt_parent.exemption_reason = "Approved alternative assessment pathway"
        exempt_parent.exemption_changed_by = self.admin
        exempt_parent.exemption_changed_at = timezone.now()
        exempt_parent.save()
        exempt = self.make_configuration(
            exempt_parent,
            deadline=cycle.default_contribution_deadline,
            deadline_source="DEFAULT",
        )
        open_parent = self.make_course(cycle=cycle, code="OPEN")
        opened = self.make_configuration(
            open_parent,
            workflow=CourseExamConfiguration.WorkflowStatus.OPEN,
            opened_at=timezone.now(),
            deadline=cycle.default_contribution_deadline,
            deadline_source="DEFAULT",
        )
        closed_parent = self.make_course(cycle=cycle, code="CLOSED")
        closed = self.make_configuration(
            closed_parent,
            workflow=CourseExamConfiguration.WorkflowStatus.CLOSED,
            opened_at=timezone.now(),
            deadline=cycle.default_contribution_deadline,
            deadline_source="DEFAULT",
        )
        inactive_parent = self.make_course(
            cycle=cycle, department=self.other_department, code="INACTIVE"
        )
        inactive = self.make_configuration(
            inactive_parent,
            deadline=cycle.default_contribution_deadline,
            deadline_source="DEFAULT",
        )
        self.other_department.is_active = False
        self.other_department.save(update_fields=["is_active", "updated_at"])
        activity_parent = self.make_course(cycle=cycle, code="ACTIVITY")
        activity = self.make_configuration(
            activity_parent,
            deadline=cycle.default_contribution_deadline,
            deadline_source="DEFAULT",
        )
        assignment = FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=activity_parent.offering_snapshots.first().offering,
            faculty_user=self.admin,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
        )
        FacultyContribution.objects.create(
            cycle_course=activity_parent,
            faculty_user=self.admin,
            source_assignment=assignment,
            source_campus=self.campus,
        )
        snapshots = {
            override.id: (override.contribution_deadline, override.contribution_deadline_source, override.revision),
            historical.id: (historical.contribution_deadline, historical.contribution_deadline_source, historical.revision),
            inactive.id: (inactive.contribution_deadline, inactive.contribution_deadline_source, inactive.revision),
            activity.id: (activity.contribution_deadline, activity.contribution_deadline_source, activity.revision),
            exempt.id: (exempt.contribution_deadline, exempt.contribution_deadline_source, exempt.revision),
            opened.id: (opened.contribution_deadline, opened.contribution_deadline_source, opened.revision),
            closed.id: (closed.contribution_deadline, closed.contribution_deadline_source, closed.revision),
        }
        new_default = self.future_deadline() + timezone.timedelta(days=3)
        cycle, _ = self._save_cycle(cycle, deadline=new_default)
        override.refresh_from_db()
        historical.refresh_from_db()
        inactive.refresh_from_db()
        activity.refresh_from_db()
        exempt.refresh_from_db()
        opened.refresh_from_db()
        closed.refresh_from_db()
        self.assertEqual(
            (override.contribution_deadline, override.contribution_deadline_source),
            snapshots[override.id][:2],
        )
        self.assertEqual(
            (historical.contribution_deadline, historical.contribution_deadline_source, historical.revision),
            snapshots[historical.id],
        )
        self.assertEqual(
            (inactive.contribution_deadline, inactive.contribution_deadline_source, inactive.revision),
            snapshots[inactive.id],
        )
        self.assertEqual(
            (activity.contribution_deadline, activity.contribution_deadline_source, activity.revision),
            snapshots[activity.id],
        )
        for protected in (exempt, opened, closed):
            self.assertEqual(
                (
                    protected.contribution_deadline,
                    protected.contribution_deadline_source,
                    protected.revision,
                ),
                snapshots[protected.id],
            )

    def test_historical_default_validity_and_live_drift_readiness_share_policy(self):
        original = self.future_deadline().replace(second=0, microsecond=0)
        cycle = self.make_cycle(
            status=ExaminationCycle.Status.OPEN,
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=original,
        )
        live_parent = self.make_course(cycle=cycle, code="LIVE")
        live = self.make_configuration(
            live_parent,
            quota_source="DEFAULT",
            final_source="DEFAULT",
            deadline=original,
            deadline_source="DEFAULT",
        )
        inactive_parent = self.make_course(
            cycle=cycle, department=self.other_department, code="VALID-INACTIVE"
        )
        inactive = self.make_configuration(
            inactive_parent,
            quota_source="DEFAULT",
            final_source="DEFAULT",
            deadline=original,
            deadline_source="DEFAULT",
        )
        self.other_department.is_active = False
        self.other_department.save(update_fields=["is_active", "updated_at"])
        exempt_parent = self.make_course(cycle=cycle, code="VALID-EXEMPT")
        exempt_parent.inclusion_status = CycleCourse.InclusionStatus.EXEMPT
        exempt_parent.exemption_category = CycleCourse.ExemptionCategory.INTERNSHIP
        exempt_parent.exemption_reason = "Approved historical assessment pathway"
        exempt_parent.exemption_changed_by = self.admin
        exempt_parent.exemption_changed_at = timezone.now()
        exempt_parent.save()
        exempt = self.make_configuration(
            exempt_parent,
            quota_source="DEFAULT",
            final_source="DEFAULT",
            deadline=original,
            deadline_source="DEFAULT",
        )
        activity_parent = self.make_course(cycle=cycle, code="VALID-ACTIVITY")
        activity = self.make_configuration(
            activity_parent,
            quota_source="DEFAULT",
            final_source="DEFAULT",
            deadline=original,
            deadline_source="DEFAULT",
        )
        assignment = FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=activity_parent.offering_snapshots.first().offering,
            faculty_user=self.admin,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
        )
        FacultyContribution.objects.create(
            cycle_course=activity_parent,
            faculty_user=self.admin,
            source_assignment=assignment,
            source_campus=self.campus,
        )
        open_parent = self.make_course(cycle=cycle, code="VALID-OPEN")
        opened = self.make_configuration(
            open_parent,
            workflow=CourseExamConfiguration.WorkflowStatus.OPEN,
            opened_at=timezone.now(),
            quota_source="DEFAULT",
            final_source="DEFAULT",
            deadline=original,
            deadline_source="DEFAULT",
        )
        closed_parent = self.make_course(cycle=cycle, code="VALID-CLOSED")
        closed = self.make_configuration(
            closed_parent,
            workflow=CourseExamConfiguration.WorkflowStatus.CLOSED,
            opened_at=timezone.now(),
            quota_source="DEFAULT",
            final_source="DEFAULT",
            deadline=original,
            deadline_source="DEFAULT",
        )
        historical_parent = self.make_course(cycle=cycle, code="VALID-HISTORY")
        historical = self.make_configuration(
            historical_parent,
            opened_at=timezone.now(),
            quota_source="DEFAULT",
            final_source="DEFAULT",
            deadline=original,
            deadline_source="DEFAULT",
        )

        cycle.default_contribution_deadline = original + timezone.timedelta(days=1)
        cycle.defaults_revision += 1
        cycle.save(
            update_fields=[
                "default_contribution_deadline",
                "defaults_revision",
                "updated_at",
            ]
        )

        live_readiness = CourseExamConfigurationReadinessService.evaluate_readiness(
            cycle_course=live_parent, configuration=live
        )
        self.assertIn("Needs Configuration", live_readiness["blockers"])
        with self.assertRaises(ValidationError):
            self._save_course(
                live_parent,
                deadline=original,
                mode=None,
                expected_revision=live.revision,
            )
        for parent, protected in (
            (inactive_parent, inactive),
            (exempt_parent, exempt),
            (activity_parent, activity),
            (open_parent, opened),
            (closed_parent, closed),
            (historical_parent, historical),
        ):
            protected.full_clean()
            readiness = CourseExamConfigurationReadinessService.evaluate_readiness(
                cycle_course=parent, configuration=protected
            )
            self.assertNotIn("Needs Configuration", readiness["blockers"])

    def test_minute_precision_noop_preserves_exact_course_and_cycle_values(self):
        exact = self.future_deadline().replace(second=45, microsecond=123456)
        submitted = exact.replace(second=0, microsecond=0)
        cycle = self.make_cycle(
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=exact,
            instructions="Stage 4.1 instructions",
        )
        parent = self.make_course(cycle=cycle, code="PRECISION")
        configuration = self.make_configuration(
            parent,
            quota_source="DEFAULT",
            final_source="DEFAULT",
            deadline=exact,
            deadline_source="OVERRIDE",
        )
        revision = configuration.revision
        with patch("apps.departmental_exams.services.AuditService.log_event") as audit:
            configuration, changed = self._save_course(
                parent,
                deadline=submitted,
                mode=None,
                expected_revision=revision,
            )
        self.assertFalse(changed)
        self.assertEqual(configuration.revision, revision)
        self.assertEqual(configuration.contribution_deadline, exact)
        self.assertEqual(
            (
                configuration.questions_required_per_faculty_source,
                configuration.final_item_count_source,
                configuration.contribution_deadline_source,
            ),
            ("DEFAULT", "DEFAULT", "OVERRIDE"),
        )
        audit.assert_not_called()

        defaults_revision = cycle.defaults_revision
        with (
            patch("apps.departmental_exams.services.AuditService.log_event") as audit,
            patch.object(
                ExaminationCycleConfigurationService,
                "_propagate_defaults_to_drafts",
            ) as propagation,
        ):
            cycle, changed = self._save_cycle(cycle, deadline=submitted)
        self.assertFalse(changed)
        self.assertEqual(cycle.defaults_revision, defaults_revision)
        self.assertEqual(cycle.default_contribution_deadline, exact)
        audit.assert_not_called()
        propagation.assert_not_called()

    def test_changed_minute_is_normalized_and_audited(self):
        exact = self.future_deadline().replace(second=45, microsecond=123456)
        changed_input = exact + timezone.timedelta(minutes=1)
        changed_minute = normalize_contribution_deadline_to_minute(changed_input)
        cycle = self.make_cycle(
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=exact,
            instructions="Stage 4.1 instructions",
        )
        parent = self.make_course(cycle=cycle, code="PRECISION-CHANGE")
        configuration = self.make_configuration(
            parent,
            quota_source="DEFAULT",
            final_source="DEFAULT",
            deadline=exact,
            deadline_source="OVERRIDE",
        )
        revision = configuration.revision
        with patch("apps.departmental_exams.services.AuditService.log_event") as audit:
            configuration, changed = self._save_course(
                parent,
                deadline=changed_input,
                mode="OVERRIDE",
                expected_revision=revision,
            )
        self.assertTrue(changed)
        self.assertEqual(configuration.revision, revision + 1)
        self.assertEqual(configuration.contribution_deadline, changed_minute)
        self.assertEqual(
            (
                configuration.contribution_deadline.second,
                configuration.contribution_deadline.microsecond,
            ),
            (0, 0),
        )
        audit.assert_called_once()

        defaults_revision = cycle.defaults_revision
        with patch("apps.departmental_exams.services.AuditService.log_event") as audit:
            cycle, changed = self._save_cycle(cycle, deadline=changed_input)
        self.assertTrue(changed)
        self.assertEqual(cycle.defaults_revision, defaults_revision + 1)
        self.assertEqual(cycle.default_contribution_deadline, changed_minute)
        self.assertEqual(
            audit.call_args.kwargs["after_data"]["default_contribution_deadline"],
            changed_minute,
        )

    def test_minute_normalization_preserves_manila_wall_clock_and_rejects_naive(self):
        utc_value = timezone.make_aware(
            datetime(2026, 8, 14, 16, 0, 59, 987654), ZoneInfo("UTC")
        )
        normalized = normalize_contribution_deadline_to_minute(utc_value)
        self.assertEqual(normalized.tzinfo, ZoneInfo("Asia/Manila"))
        self.assertEqual(
            (normalized.year, normalized.month, normalized.day, normalized.hour, normalized.minute),
            (2026, 8, 15, 0, 0),
        )
        self.assertEqual((normalized.second, normalized.microsecond), (0, 0))
        with self.assertRaises(ValidationError):
            normalize_contribution_deadline_to_minute(datetime(2026, 8, 15, 0, 0))

    def test_legacy_missing_deadline_mode_policy_and_explicit_validation(self):
        deadline = self.future_deadline().replace(second=45, microsecond=123456)
        cycle = self.make_cycle(
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=deadline,
        )
        legacy_parent = self.make_course(cycle=cycle, code="LEGACY-NEW")
        legacy, changed = self._save_course(
            legacy_parent, deadline=deadline, mode=None
        )
        self.assertTrue(changed)
        self.assertEqual(legacy.contribution_deadline_source, "OVERRIDE")
        self.assertEqual((legacy.contribution_deadline.second, legacy.contribution_deadline.microsecond), (0, 0))

        blank_parent = self.make_course(cycle=cycle, code="LEGACY-BLANK")
        blank, changed = self._save_course(blank_parent, deadline=None, mode=None)
        self.assertTrue(changed)
        self.assertIsNone(blank.contribution_deadline)
        self.assertIsNone(blank.contribution_deadline_source)

        default_parent = self.make_course(cycle=cycle, code="LEGACY-DEFAULT")
        default = self.make_configuration(
            default_parent,
            quota_source="DEFAULT",
            final_source="DEFAULT",
            deadline=deadline,
            deadline_source="DEFAULT",
        )
        default_revision = default.revision
        with patch("apps.departmental_exams.services.AuditService.log_event") as audit:
            default, changed = self._save_course(
                default_parent,
                deadline=deadline.replace(second=0, microsecond=0),
                mode=None,
                expected_revision=default_revision,
            )
        self.assertFalse(changed)
        self.assertEqual(default.revision, default_revision)
        self.assertEqual(default.contribution_deadline_source, "DEFAULT")
        self.assertEqual(default.contribution_deadline, deadline)
        self.assertEqual(
            (
                default.questions_required_per_faculty_source,
                default.final_item_count_source,
            ),
            ("DEFAULT", "DEFAULT"),
        )
        audit.assert_not_called()

        override_parent = self.make_course(cycle=cycle, code="LEGACY-OVERRIDE")
        override = self.make_configuration(
            override_parent,
            quota_source="DEFAULT",
            final_source="DEFAULT",
            deadline=deadline,
            deadline_source="OVERRIDE",
        )
        override_revision = override.revision
        with patch("apps.departmental_exams.services.AuditService.log_event") as audit:
            override, changed = self._save_course(
                override_parent,
                deadline=deadline.replace(second=0, microsecond=0),
                mode=None,
                expected_revision=override_revision,
            )
        self.assertFalse(changed)
        self.assertEqual(override.revision, override_revision)
        self.assertEqual(override.contribution_deadline_source, "OVERRIDE")
        self.assertEqual(override.contribution_deadline, deadline)
        self.assertEqual(
            (
                override.questions_required_per_faculty_source,
                override.final_item_count_source,
            ),
            ("DEFAULT", "DEFAULT"),
        )
        audit.assert_not_called()

        with self.assertRaises(ValidationError):
            self._save_course(
                override_parent,
                deadline=deadline,
                mode="UNSUPPORTED",
                expected_revision=override.revision,
            )
        with self.assertRaises(ValidationError):
            self._save_course(
                override_parent,
                deadline=None,
                mode="OVERRIDE",
                expected_revision=override.revision,
            )

    def test_legacy_form_payload_without_mode_is_derived_without_trusting_source(self):
        deadline = self.future_deadline().replace(second=0, microsecond=0)
        cycle = self.make_cycle(
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=deadline,
        )
        payload = {
            "final_item_count": "50",
            "final_item_count_mode": "DEFAULT",
            "questions_required_per_faculty": "50",
            "questions_required_per_faculty_mode": "DEFAULT",
            "coverage": "Core outcomes",
            "additional_instructions": "",
            "contribution_deadline": timezone.localtime(deadline).strftime(
                "%Y-%m-%dT%H:%M"
            ),
            "contribution_deadline_source": "DEFAULT",
            "cycle_defaults_revision_snapshot": str(cycle.defaults_revision),
            "expected_revision": "0",
        }
        form = CourseExamConfigurationForm(data=payload, cycle=cycle)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data["contribution_deadline_mode"])
        self.assertEqual(form.cleaned_data["contribution_deadline_source"], "OVERRIDE")

        blank_payload = {**payload, "contribution_deadline": ""}
        blank_form = CourseExamConfigurationForm(data=blank_payload, cycle=cycle)
        self.assertTrue(blank_form.is_valid(), blank_form.errors)
        self.assertIsNone(blank_form.cleaned_data["contribution_deadline"])
        self.assertIsNone(blank_form.cleaned_data["contribution_deadline_source"])

        default_parent = self.make_course(cycle=cycle, code="FORM-DEFAULT")
        default = self.make_configuration(
            default_parent, deadline=deadline, deadline_source="DEFAULT"
        )
        default_form = CourseExamConfigurationForm(
            data={**payload, "expected_revision": str(default.revision)},
            instance=default,
            cycle=cycle,
        )
        self.assertTrue(default_form.is_valid(), default_form.errors)
        self.assertEqual(
            default_form.cleaned_data["contribution_deadline_source"], "DEFAULT"
        )

        override_parent = self.make_course(cycle=cycle, code="FORM-OVERRIDE")
        override = self.make_configuration(
            override_parent, deadline=deadline, deadline_source="OVERRIDE"
        )
        override_form = CourseExamConfigurationForm(
            data={**payload, "expected_revision": str(override.revision)},
            instance=override,
            cycle=cycle,
        )
        self.assertTrue(override_form.is_valid(), override_form.errors)
        self.assertEqual(
            override_form.cleaned_data["contribution_deadline_source"], "OVERRIDE"
        )

        invalid_form = CourseExamConfigurationForm(
            data={**payload, "contribution_deadline_mode": "UNSUPPORTED"},
            cycle=cycle,
        )
        self.assertFalse(invalid_form.is_valid())
        self.assertIn("contribution_deadline_mode", invalid_form.errors)
        empty_override_form = CourseExamConfigurationForm(
            data={
                **payload,
                "contribution_deadline_mode": "OVERRIDE",
                "contribution_deadline": "",
            },
            cycle=cycle,
        )
        self.assertFalse(empty_override_form.is_valid())
        self.assertIn("contribution_deadline", empty_override_form.errors)

    def test_standalone_form_deadline_source_compatibility_matrix(self):
        deadline = self.future_deadline().replace(second=45, microsecond=123456)
        normalized_deadline = deadline.replace(second=0, microsecond=0)
        cycle = self.make_cycle(scope_suffix="standalone-form-matrix")

        def payload(*, deadline_value, mode_marker=None, include_mode=False):
            data = {
                "final_item_count": "50",
                "final_item_count_mode": "DEFAULT",
                "final_item_count_source": "DEFAULT",
                "questions_required_per_faculty": "50",
                "questions_required_per_faculty_mode": "DEFAULT",
                "questions_required_per_faculty_source": "DEFAULT",
                "cycle_defaults_revision_snapshot": "0",
                "coverage": "Core outcomes",
                "additional_instructions": "",
                "contribution_deadline": deadline_value,
                # Source is derived from trusted instance/mode context, never
                # from this ordinary hidden input.
                "contribution_deadline_source": "DEFAULT",
                "expected_revision": "0",
            }
            if include_mode:
                data["contribution_deadline_mode"] = mode_marker
            return data

        deadline_input = timezone.localtime(deadline).strftime("%Y-%m-%dT%H:%M")
        new_parent = self.make_course(cycle=cycle, code="FORM-STANDALONE-NEW")
        new_form = CourseExamConfigurationForm(
            data=payload(deadline_value=deadline_input),
            instance=CourseExamConfiguration(cycle_course=new_parent),
        )
        self.assertTrue(new_form.is_valid(), new_form.errors)
        new_configuration = new_form.save()
        self.assertEqual(
            (
                new_configuration.contribution_deadline,
                new_configuration.contribution_deadline_source,
            ),
            (normalized_deadline, "OVERRIDE"),
        )
        new_configuration.full_clean()

        blank_parent = self.make_course(cycle=cycle, code="FORM-STANDALONE-BLANK")
        blank_form = CourseExamConfigurationForm(
            data=payload(deadline_value=""),
            instance=CourseExamConfiguration(cycle_course=blank_parent),
        )
        self.assertTrue(blank_form.is_valid(), blank_form.errors)
        blank_configuration = blank_form.save()
        self.assertIsNone(blank_configuration.contribution_deadline)
        self.assertIsNone(blank_configuration.contribution_deadline_source)
        blank_configuration.full_clean()

        default_parent = self.make_course(cycle=cycle, code="FORM-STANDALONE-DEFAULT")
        default_configuration = self.make_configuration(
            default_parent,
            quota_source="DEFAULT",
            final_source="DEFAULT",
            deadline=deadline,
            deadline_source="DEFAULT",
        )
        default_form = CourseExamConfigurationForm(
            data=payload(deadline_value=deadline_input),
            instance=default_configuration,
        )
        self.assertTrue(default_form.is_valid(), default_form.errors)
        default_configuration = default_form.save()
        self.assertEqual(
            (
                default_configuration.contribution_deadline,
                default_configuration.contribution_deadline_source,
            ),
            (normalized_deadline, "DEFAULT"),
        )

        override_parent = self.make_course(cycle=cycle, code="FORM-STANDALONE-OVERRIDE")
        override_configuration = self.make_configuration(
            override_parent,
            quota_source="DEFAULT",
            final_source="DEFAULT",
            deadline=deadline,
            deadline_source="OVERRIDE",
        )
        override_form = CourseExamConfigurationForm(
            data=payload(deadline_value=""),
            instance=override_configuration,
        )
        self.assertTrue(override_form.is_valid(), override_form.errors)
        override_configuration = override_form.save()
        self.assertEqual(
            (
                override_configuration.contribution_deadline,
                override_configuration.contribution_deadline_source,
            ),
            (deadline, "OVERRIDE"),
        )

        explicit_default_form = CourseExamConfigurationForm(
            data=payload(
                deadline_value=deadline_input,
                mode_marker="DEFAULT",
                include_mode=True,
            ),
            instance=CourseExamConfiguration(
                cycle_course=self.make_course(
                    cycle=cycle, code="FORM-NO-CYCLE-DEFAULT"
                )
            ),
        )
        self.assertFalse(explicit_default_form.is_valid())
        self.assertIn("contribution_deadline_mode", explicit_default_form.errors)
        self.assertIn(
            "cannot be resolved without a cycle",
            explicit_default_form.errors["contribution_deadline_mode"][0],
        )

        empty_override_form = CourseExamConfigurationForm(
            data=payload(
                deadline_value="",
                mode_marker="OVERRIDE",
                include_mode=True,
            ),
            instance=CourseExamConfiguration(
                cycle_course=self.make_course(
                    cycle=cycle, code="FORM-NO-CYCLE-OVERRIDE"
                )
            ),
        )
        self.assertFalse(empty_override_form.is_valid())
        self.assertIn("contribution_deadline", empty_override_form.errors)

        invalid_mode_form = CourseExamConfigurationForm(
            data=payload(
                deadline_value=deadline_input,
                mode_marker="UNSUPPORTED",
                include_mode=True,
            ),
            instance=CourseExamConfiguration(
                cycle_course=self.make_course(
                    cycle=cycle, code="FORM-NO-CYCLE-INVALID"
                )
            ),
        )
        self.assertFalse(invalid_mode_form.is_valid())
        self.assertIn("contribution_deadline_mode", invalid_mode_form.errors)

    def test_override_source_change_is_real_change_and_removal_restores_default(self):
        deadline = self.future_deadline()
        cycle = self.make_cycle(
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=deadline,
        )
        parent = self.make_course(cycle=cycle)
        configuration, _ = self._save_course(parent, deadline=deadline, mode="OVERRIDE")
        original_revision = configuration.revision
        with patch("apps.departmental_exams.services.AuditService.log_event") as audit:
            configuration, changed = self._save_course(
                parent,
                deadline=deadline,
                mode="DEFAULT",
                expected_revision=configuration.revision,
            )
        self.assertTrue(changed)
        self.assertEqual(configuration.contribution_deadline_source, "DEFAULT")
        self.assertEqual(configuration.revision, original_revision + 1)
        self.assertEqual(audit.call_args.kwargs["action"], "DE_EXAM_COURSE_CONFIGURATION_SAVED")
        self.assertEqual(
            audit.call_args.kwargs["before_data"]["contribution_deadline_source"],
            "OVERRIDE",
        )
        self.assertEqual(
            audit.call_args.kwargs["after_data"]["contribution_deadline_source"],
            "DEFAULT",
        )
        unchanged_revision = configuration.revision
        with patch("apps.departmental_exams.services.AuditService.log_event") as noop_audit:
            configuration, changed = self._save_course(
                parent,
                deadline=deadline,
                mode="DEFAULT",
                expected_revision=configuration.revision,
            )
        self.assertFalse(changed)
        self.assertEqual(configuration.revision, unchanged_revision)
        noop_audit.assert_not_called()

        override_deadline = deadline - timezone.timedelta(days=1)
        configuration, _ = self._save_course(
            parent,
            deadline=override_deadline,
            mode="OVERRIDE",
            expected_revision=configuration.revision,
        )
        configuration, changed = CourseExamConfigurationService.remove_overrides(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            user=self.configurer,
            expected_revision=configuration.revision,
            return_questions_required_per_faculty=False,
            return_final_item_count=False,
            return_contribution_deadline=True,
        )
        self.assertTrue(changed)
        self.assertEqual(
            (configuration.contribution_deadline, configuration.contribution_deadline_source),
            (deadline, "DEFAULT"),
        )

    def test_override_removal_without_cycle_deadline_becomes_not_configured(self):
        cycle = self.make_cycle(
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
        )
        parent = self.make_course(cycle=cycle)
        configuration, _ = self._save_course(
            parent, deadline=self.future_deadline(), mode="OVERRIDE"
        )
        configuration, changed = CourseExamConfigurationService.remove_overrides(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            user=self.configurer,
            expected_revision=configuration.revision,
            return_questions_required_per_faculty=False,
            return_final_item_count=False,
            return_contribution_deadline=True,
        )
        self.assertTrue(changed)
        self.assertIsNone(configuration.contribution_deadline)
        self.assertIsNone(configuration.contribution_deadline_source)

    def test_deadline_noop_stale_open_reason_closed_guard_and_audit_rollback(self):
        deadline = self.future_deadline()
        cycle = self.make_cycle(
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=deadline,
            instructions="Stage 4.1 instructions",
        )
        with patch("apps.departmental_exams.services.AuditService.log_event") as audit:
            returned, changed = self._save_cycle(cycle, deadline=deadline)
        self.assertFalse(changed)
        self.assertEqual(returned.defaults_revision, 0)
        audit.assert_not_called()
        with self.assertRaises(CourseExamConfigurationConflict):
            ExaminationCycleConfigurationService.save_cycle_configuration(
                cycle_id=cycle.id,
                tenant_id=self.tenant.id,
                user=self.manager,
                expected_updated_at="stale",
                default_questions_required_per_faculty=50,
                default_final_item_count=50,
                default_contribution_deadline=deadline + timezone.timedelta(days=1),
                contributor_instructions="Stage 4.1 instructions",
            )
        cycle.status = ExaminationCycle.Status.OPEN
        cycle.save(update_fields=["status", "updated_at"])
        with self.assertRaises(ValidationError):
            self._save_cycle(cycle, deadline=deadline + timezone.timedelta(days=1))
        new_deadline = deadline + timezone.timedelta(days=2)
        with patch("apps.departmental_exams.services.AuditService.log_event", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                self._save_cycle(cycle, deadline=new_deadline, reason="Authorized open-cycle deadline change")
        cycle.refresh_from_db()
        self.assertEqual(cycle.default_contribution_deadline, deadline)
        cycle.status = ExaminationCycle.Status.CLOSED
        cycle.save(update_fields=["status", "updated_at"])
        with self.assertRaises(ValidationError):
            self._save_cycle(cycle, deadline=new_deadline, reason="Closed cycle remains immutable")

    def test_past_deadline_blocks_open_and_first_open_pair_is_immutable(self):
        cycle = self.make_cycle(status=ExaminationCycle.Status.OPEN)
        parent = self.make_course(cycle=cycle)
        past = timezone.now() - timezone.timedelta(hours=1)
        configuration = self.make_configuration(parent, deadline=past)
        readiness = CourseExamConfigurationReadinessService.evaluate_readiness(
            cycle_course=parent, configuration=configuration, user=self.configurer
        )
        self.assertIn("Contribution Deadline Passed", readiness["blockers"])
        with self.assertRaises(ValidationError):
            CourseExamConfigurationService.open_for_contribution(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                user=self.configurer,
                expected_revision=configuration.revision,
            )
        configuration.opened_at = timezone.now()
        configuration.opened_by = self.configurer
        configuration.save(update_fields=["opened_at", "opened_by", "updated_at"])
        configuration.contribution_deadline = self.future_deadline()
        configuration.contribution_deadline_source = "DEFAULT"
        with self.assertRaises(ValidationError):
            configuration.full_clean()

    def test_direct_save_guard_preserves_first_open_deadline_pair(self):
        deadline = self.future_deadline().replace(second=45, microsecond=123456)
        cycle = self.make_cycle(
            status=ExaminationCycle.Status.OPEN,
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=deadline,
            instructions="Frozen on first opening",
            scope_suffix="direct-save-guard",
        )
        parent = self.make_course(cycle=cycle, code="DIRECT-SAVE-GUARD")
        configuration = self.make_configuration(
            parent,
            quota_source="DEFAULT",
            final_source="DEFAULT",
            deadline=deadline,
            deadline_source="DEFAULT",
        )

        opened, changed = CourseExamConfigurationService.open_for_contribution(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            user=self.configurer,
            expected_revision=configuration.revision,
        )
        self.assertTrue(changed)
        self.assertIsNotNone(opened.opened_at)
        stored_pair = (deadline, CourseExamConfiguration.ValueSource.DEFAULT)
        opened.refresh_from_db()
        self.assertEqual(
            (opened.contribution_deadline, opened.contribution_deadline_source),
            stored_pair,
        )

        attempts = (
            (
                "deadline",
                deadline + timezone.timedelta(days=1),
                CourseExamConfiguration.ValueSource.DEFAULT,
                None,
            ),
            (
                "source",
                deadline,
                CourseExamConfiguration.ValueSource.OVERRIDE,
                None,
            ),
            (
                "deadline-and-source",
                deadline + timezone.timedelta(days=2),
                CourseExamConfiguration.ValueSource.OVERRIDE,
                None,
            ),
            (
                "update-fields",
                deadline + timezone.timedelta(days=3),
                CourseExamConfiguration.ValueSource.DEFAULT,
                ["contribution_deadline", "updated_at"],
            ),
        )
        for label, proposed_deadline, proposed_source, update_fields in attempts:
            with self.subTest(label=label):
                opened.refresh_from_db()
                opened.contribution_deadline = proposed_deadline
                opened.contribution_deadline_source = proposed_source
                with self.assertRaisesMessage(
                    ValidationError,
                    "Contribution deadline and provenance are immutable after first opening.",
                ):
                    if update_fields is None:
                        opened.save()
                    else:
                        opened.save(update_fields=update_fields)
                opened.refresh_from_db()
                self.assertEqual(
                    (
                        opened.contribution_deadline,
                        opened.contribution_deadline_source,
                    ),
                    stored_pair,
                )

        opened.coverage = "Allowed unrelated historical note"
        opened.save(update_fields=["coverage", "updated_at"])
        opened.refresh_from_db()
        self.assertEqual(opened.coverage, "Allowed unrelated historical note")
        self.assertEqual(
            (opened.contribution_deadline, opened.contribution_deadline_source),
            stored_pair,
        )


class Stage41UsabilityViewTests(Stage4TestCase):
    def course_configuration_payload(self, configuration, *, expected_revision):
        return {
            "expected_revision": expected_revision,
            "questions_required_per_faculty_mode": "DEFAULT",
            "questions_required_per_faculty": "50",
            "final_item_count_mode": "DEFAULT",
            "final_item_count": "50",
            "contribution_deadline_mode": "DEFAULT",
            "contribution_deadline": timezone.localtime(
                configuration.contribution_deadline
            ).strftime("%Y-%m-%dT%H:%M"),
            "coverage": "Core outcomes",
            "additional_instructions": "",
        }

    def cycle_configuration_payload(self, cycle, *, expected_updated_at):
        return {
            "expected_updated_at": expected_updated_at,
            "default_questions_required_per_faculty": "50",
            "default_final_item_count": "50",
            "default_contribution_deadline": timezone.localtime(
                cycle.default_contribution_deadline
            ).strftime("%Y-%m-%dT%H:%M"),
            "contributor_instructions": cycle.contributor_instructions,
            "reason": "",
        }

    def assert_hidden_error_is_visible(self, response, *, template_name):
        self.assertTemplateUsed(response, template_name)
        self.assertContains(
            response,
            'class="alert alert-danger"',
            html=False,
            status_code=response.status_code,
        )

    def test_primary_course_hidden_revision_errors_are_visible_and_fail_closed(self):
        deadline = self.future_deadline()
        cycle = self.make_cycle(
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=deadline,
            scope_suffix="primary-course-hidden-error",
        )
        parent = self.make_course(cycle=cycle, code="PRIMARY-HIDDEN-COURSE")
        configuration = self.make_configuration(
            parent,
            quota_source="DEFAULT",
            final_source="DEFAULT",
            deadline=deadline,
            deadline_source="DEFAULT",
        )
        url = reverse(
            "departmental_exams:course_configuration", args=[parent.id]
        )
        initial_state = (
            configuration.revision,
            configuration.coverage,
            configuration.contribution_deadline,
            configuration.contribution_deadline_source,
        )
        self.client.force_login(self.configurer)

        missing_payload = self.course_configuration_payload(
            configuration, expected_revision=configuration.revision
        )
        missing_payload.pop("expected_revision")
        malformed_value = "sensitive-malformed-revision"
        malformed_payload = self.course_configuration_payload(
            configuration, expected_revision=malformed_value
        )
        with patch.object(
            CourseExamConfigurationService, "save_course_draft"
        ) as writer:
            missing = self.client.post(url, missing_payload)
            malformed = self.client.post(url, malformed_payload)

        for response in (missing, malformed):
            self.assertEqual(response.status_code, 200)
            self.assert_hidden_error_is_visible(
                response,
                template_name="departmental_exams/admin/course_configuration.html",
            )
            self.assertContains(
                response,
                "This page state is missing or invalid. Reload the page and try again.",
            )
        self.assertNotContains(
            malformed, f">{malformed_value}<", html=False
        )
        writer.assert_not_called()
        configuration.refresh_from_db()
        self.assertEqual(
            (
                configuration.revision,
                configuration.coverage,
                configuration.contribution_deadline,
                configuration.contribution_deadline_source,
            ),
            initial_state,
        )

    def test_primary_cycle_hidden_timestamp_errors_are_visible_and_fail_closed(self):
        deadline = self.future_deadline()
        cycle = self.make_cycle(
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=deadline,
            instructions="Stage 4.1 instructions",
            scope_suffix="primary-cycle-hidden-error",
        )
        url = reverse(
            "departmental_exams:cycle_configuration", args=[cycle.id]
        )
        initial_state = (
            cycle.defaults_revision,
            cycle.default_questions_required_per_faculty,
            cycle.default_final_item_count,
            cycle.default_contribution_deadline,
            cycle.contributor_instructions,
            cycle.updated_at,
        )
        self.client.force_login(self.manager)

        missing_payload = self.cycle_configuration_payload(
            cycle,
            expected_updated_at=ExaminationCycleConfigurationService.transition_token(
                cycle
            ),
        )
        missing_payload.pop("expected_updated_at")
        malformed_value = "sensitive-malformed-timestamp"
        malformed_payload = self.cycle_configuration_payload(
            cycle, expected_updated_at=malformed_value
        )
        missing = self.client.post(url, missing_payload)
        malformed = self.client.post(url, malformed_payload)

        for response in (missing, malformed):
            self.assertEqual(response.status_code, 200)
            self.assert_hidden_error_is_visible(
                response,
                template_name="departmental_exams/admin/cycle_configuration.html",
            )
            self.assertContains(
                response,
                "This page state is missing or invalid. Reload the page and try again.",
            )
        self.assertNotContains(
            malformed, f">{malformed_value}<", html=False
        )
        cycle.refresh_from_db()
        self.assertEqual(
            (
                cycle.defaults_revision,
                cycle.default_questions_required_per_faculty,
                cycle.default_final_item_count,
                cycle.default_contribution_deadline,
                cycle.contributor_instructions,
                cycle.updated_at,
            ),
            initial_state,
        )

    def test_primary_configuration_valid_submissions_remain_normal(self):
        deadline = self.future_deadline()
        cycle = self.make_cycle(
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=deadline,
            instructions="Stage 4.1 instructions",
            scope_suffix="primary-valid-submission",
        )
        parent = self.make_course(cycle=cycle, code="PRIMARY-VALID")
        configuration = self.make_configuration(
            parent,
            quota_source="DEFAULT",
            final_source="DEFAULT",
            deadline=deadline,
            deadline_source="DEFAULT",
        )

        self.client.force_login(self.configurer)
        course_payload = self.course_configuration_payload(
            configuration, expected_revision=configuration.revision
        )
        course_payload["coverage"] = "Updated through the valid primary form"
        course_response = self.client.post(
            reverse(
                "departmental_exams:course_configuration", args=[parent.id]
            ),
            course_payload,
        )
        self.assertEqual(course_response.status_code, 302)
        configuration.refresh_from_db()
        self.assertEqual(
            configuration.coverage, "Updated through the valid primary form"
        )

        self.client.force_login(self.manager)
        cycle_response = self.client.post(
            reverse(
                "departmental_exams:cycle_configuration", args=[cycle.id]
            ),
            self.cycle_configuration_payload(
                cycle,
                expected_updated_at=ExaminationCycleConfigurationService.transition_token(
                    cycle
                ),
            ),
        )
        self.assertEqual(cycle_response.status_code, 200)
        self.assertTemplateUsed(
            cycle_response,
            "departmental_exams/admin/cycle_defaults_confirm.html",
        )

    def test_primary_configuration_hidden_state_keeps_unauthorized_users_denied(self):
        deadline = self.future_deadline()
        cycle = self.make_cycle(
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=deadline,
            scope_suffix="primary-hidden-unauthorized",
        )
        parent = self.make_course(cycle=cycle, code="PRIMARY-UNAUTHORIZED")
        configuration = self.make_configuration(
            parent,
            quota_source="DEFAULT",
            final_source="DEFAULT",
            deadline=deadline,
            deadline_source="DEFAULT",
        )
        outsider = self.make_user(
            "stage41-primary-outsider", self.department, ("admin_portal.access",)
        )
        initial_cycle_state = (
            cycle.defaults_revision,
            cycle.default_contribution_deadline,
            cycle.updated_at,
        )
        initial_course_state = (
            configuration.revision,
            configuration.contribution_deadline,
            configuration.contribution_deadline_source,
            configuration.updated_at,
        )
        self.client.force_login(outsider)

        cycle_response = self.client.post(
            reverse(
                "departmental_exams:cycle_configuration", args=[cycle.id]
            ),
            self.cycle_configuration_payload(
                cycle, expected_updated_at="sensitive-malformed-timestamp"
            ),
        )
        course_response = self.client.post(
            reverse(
                "departmental_exams:course_configuration", args=[parent.id]
            ),
            self.course_configuration_payload(
                configuration, expected_revision="sensitive-malformed-revision"
            ),
        )
        self.assertEqual(cycle_response.status_code, 403)
        self.assertEqual(course_response.status_code, 403)
        cycle.refresh_from_db()
        configuration.refresh_from_db()
        self.assertEqual(
            (
                cycle.defaults_revision,
                cycle.default_contribution_deadline,
                cycle.updated_at,
            ),
            initial_cycle_state,
        )
        self.assertEqual(
            (
                configuration.revision,
                configuration.contribution_deadline,
                configuration.contribution_deadline_source,
                configuration.updated_at,
            ),
            initial_course_state,
        )

    def test_cycle_defaults_hidden_confirmation_errors_are_visible_and_fail_closed(self):
        cycle = self.make_cycle(
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=self.future_deadline(),
            instructions="Stage 4.1 instructions",
        )
        apply_url = reverse(
            "departmental_exams:cycle_apply_defaults", args=[cycle.id]
        )
        self.client.force_login(self.manager)
        initial_revision = cycle.defaults_revision

        with patch.object(
            ExaminationCycleConfigurationService, "save_cycle_configuration"
        ) as writer:
            missing = self.client.post(apply_url, {})
            malformed_value = "malformed-sensitive-confirmation-token"
            malformed = self.client.post(
                apply_url, {"confirmation_state": malformed_value}
            )

        self.assertEqual(missing.status_code, 404)
        self.assert_hidden_error_is_visible(
            missing,
            template_name="departmental_exams/admin/cycle_defaults_confirm.html",
        )
        self.assertContains(
            missing, "This field is required.", status_code=missing.status_code
        )
        self.assertEqual(malformed.status_code, 404)
        self.assert_hidden_error_is_visible(
            malformed,
            template_name="departmental_exams/admin/cycle_defaults_confirm.html",
        )
        self.assertContains(
            malformed,
            "This cycle-default confirmation is missing, invalid, or no longer available.",
            status_code=malformed.status_code,
        )
        self.assertNotContains(
            malformed,
            f">{malformed_value}<",
            html=False,
            status_code=malformed.status_code,
        )
        writer.assert_not_called()
        cycle.refresh_from_db()
        self.assertEqual(cycle.defaults_revision, initial_revision)

    def test_revision_hidden_errors_are_visible_without_mutation(self):
        deadline = self.future_deadline()

        cycle_transition = self.make_cycle(
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=deadline,
            scope_suffix="hidden-cycle-transition",
        )
        self.client.force_login(self.manager)
        cycle_response = self.client.post(
            reverse("departmental_exams:cycle_open", args=[cycle_transition.id]),
            {},
        )
        self.assertEqual(cycle_response.status_code, 200)
        self.assert_hidden_error_is_visible(
            cycle_response,
            template_name="departmental_exams/admin/cycle_transition_confirm.html",
        )
        self.assertContains(cycle_response, "This field is required.")
        cycle_transition.refresh_from_db()
        self.assertEqual(cycle_transition.status, ExaminationCycle.Status.DRAFT)

        contribution_cycle = self.make_cycle(
            status=ExaminationCycle.Status.OPEN,
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=deadline,
            scope_suffix="hidden-course-contribution",
        )
        contribution_parent = self.make_course(
            cycle=contribution_cycle, code="HIDDEN-CONTRIBUTION"
        )
        contribution_configuration = self.make_configuration(
            contribution_parent,
            quota_source="DEFAULT",
            final_source="DEFAULT",
            deadline=deadline,
            deadline_source="DEFAULT",
        )
        self.client.force_login(self.configurer)
        invalid_revision = "sensitive-invalid-revision"
        contribution_response = self.client.post(
            reverse(
                "departmental_exams:course_contribution_open",
                args=[contribution_parent.id],
            ),
            {"expected_revision": invalid_revision},
        )
        self.assertEqual(contribution_response.status_code, 200)
        self.assert_hidden_error_is_visible(
            contribution_response,
            template_name="departmental_exams/admin/course_contribution_confirm.html",
        )
        self.assertContains(contribution_response, "Enter a whole number.")
        self.assertNotContains(
            contribution_response, f">{invalid_revision}<", html=False
        )
        contribution_configuration.refresh_from_db()
        self.assertEqual(
            contribution_configuration.workflow_status,
            CourseExamConfiguration.WorkflowStatus.DRAFT,
        )
        self.assertIsNone(contribution_configuration.opened_at)

        override_cycle = self.make_cycle(
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=deadline,
            scope_suffix="hidden-override-removal",
        )
        override_parent = self.make_course(
            cycle=override_cycle, code="HIDDEN-OVERRIDE"
        )
        override_configuration = self.make_configuration(
            override_parent, deadline=deadline, deadline_source="OVERRIDE"
        )
        override_response = self.client.post(
            reverse(
                "departmental_exams:course_remove_overrides",
                args=[override_parent.id],
            ),
            {"return_contribution_deadline": "on"},
        )
        self.assertEqual(override_response.status_code, 200)
        self.assert_hidden_error_is_visible(
            override_response,
            template_name="departmental_exams/admin/course_override_remove_confirm.html",
        )
        self.assertContains(override_response, "This field is required.")
        override_configuration.refresh_from_db()
        self.assertEqual(override_configuration.contribution_deadline_source, "OVERRIDE")

        inclusion_cycle = self.make_cycle(scope_suffix="hidden-inclusion-transition")
        inclusion_parent = self.make_course(
            cycle=inclusion_cycle, code="HIDDEN-INCLUSION"
        )
        inclusion_response = self.client.post(
            reverse(
                "departmental_exams:cycle_course_exempt",
                args=[inclusion_parent.id],
            ),
            {
                "exemption_category": CycleCourse.ExemptionCategory.INTERNSHIP,
                "reason": "Approved alternative assessment pathway",
            },
        )
        self.assertEqual(inclusion_response.status_code, 200)
        self.assert_hidden_error_is_visible(
            inclusion_response,
            template_name="departmental_exams/admin/cycle_course_transition_confirm.html",
        )
        self.assertContains(inclusion_response, "This field is required.")
        inclusion_parent.refresh_from_db()
        self.assertEqual(
            inclusion_parent.inclusion_status, CycleCourse.InclusionStatus.INCLUDED
        )

    def test_cycle_and_course_pages_use_admin_shell_help_and_role_safe_links(self):
        deadline = timezone.make_aware(
            datetime(2026, 8, 15, 17, 0), ZoneInfo("Asia/Manila")
        )
        cycle = self.make_cycle(
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=deadline,
        )
        parent = self.make_course(cycle=cycle)
        configuration = self.make_configuration(
            parent, quota_source="DEFAULT", final_source="DEFAULT", deadline=deadline,
            deadline_source="DEFAULT",
        )
        parent.reviewer = self.reviewer
        parent.save(update_fields=["reviewer", "updated_at"])

        self.client.force_login(self.manager)
        cycle_response = self.client.get(
            reverse("departmental_exams:cycle_configuration", args=[cycle.id])
        )
        self.assertEqual(cycle_response.status_code, 200)
        self.assertContains(cycle_response, "admin-sidebar")
        self.assertContains(cycle_response, "If the cycle contribution deadline is August 15")
        self.assertContains(cycle_response, 'value="2026-08-15T17:00"', html=False)
        self.assertNotContains(cycle_response, ">Course Examinations</a>", html=False)

        self.client.force_login(self.reviewer)
        assigned = self.client.get(
            reverse("departmental_exams:assigned_course_examinations")
        )
        self.assertEqual(assigned.status_code, 200)
        self.assertContains(assigned, "admin-sidebar")
        self.assertContains(assigned, "DEFAULT")
        self.assertContains(assigned, "One shared grouped examination")
        self.assertNotContains(
            assigned,
            reverse("departmental_exams:course_configuration", args=[parent.id]),
        )
        self.assertEqual(configuration.contribution_deadline_source, "DEFAULT")

    def test_route_capable_zero_row_users_receive_safe_empty_state(self):
        assigned_url = reverse("departmental_exams:assigned_course_examinations")
        hidden_cycle = self.make_cycle(scope_suffix="hidden-empty-state")
        hidden_parent = self.make_course(
            cycle=hidden_cycle,
            department=self.other_department,
            code="HIDDEN-COURSE",
        )
        for user in (self.configurer, self.reviewer):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(assigned_url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "No course examinations are currently assigned")
                self.assertContains(response, "Courses outside your authorized scope are not shown")
                self.assertNotContains(response, hidden_parent.course.code)
                self.assertNotContains(response, self.other_department.name)

        outsider = self.make_user(
            "stage41-outsider", self.department, ("admin_portal.access",)
        )
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(assigned_url).status_code, 403)

    def test_assigned_list_query_count_does_not_grow_per_deadline_row(self):
        cycle = self.make_cycle(
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=self.future_deadline(),
        )
        first = self.make_course(cycle=cycle, code="QUERY-FIRST")
        self.make_configuration(first, deadline_source="DEFAULT", deadline=cycle.default_contribution_deadline)
        self.client.force_login(self.configurer)
        url = reverse("departmental_exams:assigned_course_examinations")
        with CaptureQueriesContext(connection) as one_row:
            self.client.get(url)
        for index in range(8):
            parent = self.make_course(cycle=cycle, code=f"QUERY-{index}")
            self.make_configuration(parent, deadline_source="DEFAULT", deadline=cycle.default_contribution_deadline)
        with CaptureQueriesContext(connection) as nine_rows:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(nine_rows), len(one_row) + 2)

"""CAO default-and-override service tests."""

import hashlib
import json
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.auditlog.models import AuditLog

from .models import CourseExamConfiguration, CycleCourse, ExaminationCycle
from .services import (
    CourseExamConfigurationConflict,
    CourseExamConfigurationReadinessService,
    CourseExamConfigurationService,
    ExaminationCycleConfigurationService,
)
from .stage4_test_support import Stage4TestCase


class CAOCycleDefaultsTests(Stage4TestCase):
    def _save_defaults(self, cycle, *, quota=50, final_count=50, reason=""):
        return ExaminationCycleConfigurationService.save_cycle_configuration(
            cycle_id=cycle.id,
            tenant_id=self.tenant.id,
            user=self.manager,
            expected_updated_at=ExaminationCycleConfigurationService.transition_token(cycle),
            default_questions_required_per_faculty=quota,
            default_final_item_count=final_count,
            contributor_instructions="CAO instructions",
            reason=reason,
        )

    def test_both_defaults_propagate_with_independent_values(self):
        cycle = self.make_cycle()
        parent = self.make_course(cycle=cycle)
        cycle, changed = self._save_defaults(cycle, quota=60, final_count=50)
        configuration = CourseExamConfiguration.objects.get(cycle_course=parent)
        self.assertTrue(changed)
        self.assertEqual((configuration.questions_required_per_faculty, configuration.final_item_count), (60, 50))
        self.assertEqual((configuration.questions_required_per_faculty_source, configuration.final_item_count_source), ("DEFAULT", "DEFAULT"))
        self.assertEqual(configuration.cycle_defaults_revision_snapshot, cycle.defaults_revision)

    def test_default_change_preserves_override_and_updates_only_default_source(self):
        cycle = self.make_cycle()
        parent = self.make_course(cycle=cycle)
        cycle, _ = self._save_defaults(cycle, quota=50, final_count=50)
        configuration = CourseExamConfiguration.objects.get(cycle_course=parent)
        configuration.questions_required_per_faculty = 75
        configuration.questions_required_per_faculty_source = "OVERRIDE"
        configuration.save(update_fields=["questions_required_per_faculty", "questions_required_per_faculty_source"])
        cycle, _ = self._save_defaults(cycle, quota=60, final_count=55)
        configuration.refresh_from_db()
        self.assertEqual((configuration.questions_required_per_faculty, configuration.questions_required_per_faculty_source), (75, "OVERRIDE"))
        self.assertEqual((configuration.final_item_count, configuration.final_item_count_source), (55, "DEFAULT"))
        self.assertEqual(configuration.cycle_defaults_revision_snapshot, cycle.defaults_revision)

    def test_missing_default_leaves_configuration_incomplete_and_not_ready(self):
        cycle = self.make_cycle(status=ExaminationCycle.Status.OPEN)
        parent = self.make_course(cycle=cycle)
        cycle, _ = self._save_defaults(
            cycle,
            quota=50,
            final_count=None,
            reason="Authorized incomplete-default readiness review",
        )
        configuration = CourseExamConfiguration.objects.get(cycle_course=parent)
        readiness = CourseExamConfigurationReadinessService.evaluate_readiness(cycle_course=parent, configuration=configuration, user=self.configurer)
        self.assertEqual(configuration.questions_required_per_faculty, 50)
        self.assertIsNone(configuration.final_item_count)
        self.assertIn("Needs Configuration", readiness["blockers"])

    def test_open_cycle_change_requires_reason_and_closed_cycle_is_denied(self):
        cycle = self.make_cycle()
        cycle, _ = self._save_defaults(cycle)
        cycle.status = ExaminationCycle.Status.OPEN
        cycle.save(update_fields=["status"])
        with self.assertRaises(ValidationError):
            self._save_defaults(cycle, quota=60, final_count=50)
        with self.assertRaises(ValidationError):
            self._save_defaults(cycle, quota=60, final_count=50, reason="Too short")
        cycle, changed = self._save_defaults(cycle, quota=60, final_count=50, reason="Corrected department-approved quota policy")
        self.assertTrue(changed)
        cycle.status = ExaminationCycle.Status.CLOSED
        cycle.save(update_fields=["status"])
        with self.assertRaises(ValidationError):
            self._save_defaults(cycle, quota=60, final_count=55, reason="Closed cycle must remain immutable")

    def test_unchanged_cycle_defaults_are_a_complete_noop(self):
        cycle = self.make_cycle(
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            instructions="CAO instructions",
            scope_suffix="complete-noop",
        )
        existing_parent = self.make_course(cycle=cycle, code="NOOP-EXISTING")
        existing_configuration = self.make_configuration(
            existing_parent,
            quota=50,
            final_count=50,
            quota_source="DEFAULT",
            final_source="DEFAULT",
        )
        missing_parent = self.make_course(cycle=cycle, code="NOOP-MISSING")
        cycle_updated_at = cycle.updated_at
        defaults_revision = cycle.defaults_revision
        configuration_count = CourseExamConfiguration.objects.filter(
            cycle_course__cycle=cycle
        ).count()
        child_snapshot = {
            "questions_required_per_faculty": existing_configuration.questions_required_per_faculty,
            "questions_required_per_faculty_source": existing_configuration.questions_required_per_faculty_source,
            "final_item_count": existing_configuration.final_item_count,
            "final_item_count_source": existing_configuration.final_item_count_source,
            "cycle_defaults_revision_snapshot": existing_configuration.cycle_defaults_revision_snapshot,
            "revision": existing_configuration.revision,
            "created_at": existing_configuration.created_at,
            "updated_at": existing_configuration.updated_at,
        }
        with patch(
            "apps.departmental_exams.services.AuditService.log_event"
        ) as audit, patch.object(
            ExaminationCycleConfigurationService,
            "_propagate_defaults_to_drafts",
        ) as propagate:
            returned_cycle, changed = self._save_defaults(cycle)

        self.assertFalse(changed)
        self.assertEqual(returned_cycle.id, cycle.id)
        self.assertEqual(returned_cycle.defaults_revision, defaults_revision)
        self.assertEqual(returned_cycle.updated_at, cycle_updated_at)
        audit.assert_not_called()
        propagate.assert_not_called()
        cycle.refresh_from_db()
        existing_configuration.refresh_from_db()
        self.assertEqual(cycle.defaults_revision, defaults_revision)
        self.assertEqual(cycle.updated_at, cycle_updated_at)
        self.assertEqual(
            CourseExamConfiguration.objects.filter(cycle_course__cycle=cycle).count(),
            configuration_count,
        )
        self.assertFalse(
            CourseExamConfiguration.objects.filter(cycle_course=missing_parent).exists()
        )
        self.assertEqual(
            {
                "questions_required_per_faculty": existing_configuration.questions_required_per_faculty,
                "questions_required_per_faculty_source": existing_configuration.questions_required_per_faculty_source,
                "final_item_count": existing_configuration.final_item_count,
                "final_item_count_source": existing_configuration.final_item_count_source,
                "cycle_defaults_revision_snapshot": existing_configuration.cycle_defaults_revision_snapshot,
                "revision": existing_configuration.revision,
                "created_at": existing_configuration.created_at,
                "updated_at": existing_configuration.updated_at,
            },
            child_snapshot,
        )

    def test_stale_token_and_audit_failure_roll_back_cycle_defaults(self):
        cycle = self.make_cycle(scope_suffix="stale-and-audit-rollback")
        cycle, _ = self._save_defaults(cycle)
        revision = cycle.defaults_revision
        with self.assertRaises(CourseExamConfigurationConflict):
            ExaminationCycleConfigurationService.save_cycle_configuration(
                cycle_id=cycle.id, tenant_id=self.tenant.id, user=self.manager,
                expected_updated_at="stale", default_questions_required_per_faculty=60,
                default_final_item_count=50, contributor_instructions="CAO instructions",
            )
        with patch("apps.departmental_exams.services.AuditService.log_event", side_effect=RuntimeError("audit failed")):
            with self.assertRaises(RuntimeError):
                self._save_defaults(cycle, quota=60, final_count=50)
        cycle.refresh_from_db()
        self.assertEqual(cycle.defaults_revision, revision)

    def test_cycle_default_audit_hashes_confidential_text_without_raw_content(self):
        cycle = self.make_cycle()
        instructions = "Confidential contributor instructions must not enter audit payloads."
        reason = "Confidential administrative rationale must not enter audit payloads."
        with patch("apps.departmental_exams.services.AuditService.log_event") as audit:
            ExaminationCycleConfigurationService.save_cycle_configuration(
                cycle_id=cycle.id,
                tenant_id=self.tenant.id,
                user=self.manager,
                expected_updated_at=ExaminationCycleConfigurationService.transition_token(cycle),
                default_questions_required_per_faculty=50,
                default_final_item_count=50,
                contributor_instructions=instructions,
                reason=reason,
            )
        payload = audit.call_args.kwargs
        self.assertEqual(
            payload["after_data"]["contributor_instructions_sha256"],
            hashlib.sha256(instructions.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(payload["after_data"]["contributor_instructions_length"], len(instructions))
        self.assertNotIn(instructions, str(payload))
        self.assertEqual(
            payload["metadata"]["reason_sha256"],
            hashlib.sha256(reason.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(payload["metadata"]["reason_length"], len(reason))
        self.assertNotIn(reason, str(payload))

    def test_ineligible_and_historical_rows_are_not_rewritten(self):
        cycle = self.make_cycle()
        eligible = self.make_course(cycle=cycle, code="ELIGIBLE")
        exempt = self.make_course(cycle=cycle, code="EXEMPT")
        exempt.inclusion_status = CycleCourse.InclusionStatus.EXEMPT
        exempt.exemption_category = CycleCourse.ExemptionCategory.INTERNSHIP
        exempt.exemption_reason = "Approved alternative assessment pathway"
        exempt.exemption_changed_by = self.admin
        exempt.exemption_changed_at = timezone.now()
        exempt.save()
        historical = self.make_course(cycle=cycle, code="HISTORICAL")
        historical_config = self.make_configuration(historical, opened_at=timezone.now())
        historical_snapshot = {
            "questions_required_per_faculty": historical_config.questions_required_per_faculty,
            "questions_required_per_faculty_source": historical_config.questions_required_per_faculty_source,
            "final_item_count": historical_config.final_item_count,
            "final_item_count_source": historical_config.final_item_count_source,
            "cycle_defaults_revision_snapshot": historical_config.cycle_defaults_revision_snapshot,
            "revision": historical_config.revision,
        }
        with patch("apps.departmental_exams.services.AuditService.log_event") as audit:
            cycle, _ = self._save_defaults(cycle, quota=50, final_count=50)
        self.assertTrue(CourseExamConfiguration.objects.filter(cycle_course=eligible).exists())
        self.assertFalse(CourseExamConfiguration.objects.filter(cycle_course=exempt).exists())
        self.assertEqual(CourseExamConfiguration.objects.filter(cycle_course=historical).count(), 1)
        historical_config.refresh_from_db()
        self.assertEqual(
            {
                "questions_required_per_faculty": historical_config.questions_required_per_faculty,
                "questions_required_per_faculty_source": historical_config.questions_required_per_faculty_source,
                "final_item_count": historical_config.final_item_count,
                "final_item_count_source": historical_config.final_item_count_source,
                "cycle_defaults_revision_snapshot": historical_config.cycle_defaults_revision_snapshot,
                "revision": historical_config.revision,
            },
            historical_snapshot,
        )
        propagation = audit.call_args.kwargs["metadata"]["propagation"]
        self.assertEqual(propagation["excluded_by_reason"]["EVER_OPENED"], 1)
        self.assertNotIn(historical_config.id, propagation["affected_configuration_ids"])

    def test_missing_children_copy_every_current_valid_default(self):
        cycle = self.make_cycle(
            default_questions_required_per_faculty=50,
            default_final_item_count=60,
        )
        quota_change_parent = self.make_course(cycle=cycle, code="QUOTA-ONLY")
        cycle, _changed = self._save_defaults(cycle, quota=55, final_count=60)
        quota_configuration = CourseExamConfiguration.objects.get(
            cycle_course=quota_change_parent
        )
        self.assertEqual(
            (
                quota_configuration.questions_required_per_faculty,
                quota_configuration.questions_required_per_faculty_source,
                quota_configuration.final_item_count,
                quota_configuration.final_item_count_source,
            ),
            (55, "DEFAULT", 60, "DEFAULT"),
        )

        cycle = self.make_cycle(
            default_questions_required_per_faculty=50,
            default_final_item_count=60,
            scope_suffix="missing-child-final",
        )
        final_change_parent = self.make_course(cycle=cycle, code="FINAL-ONLY")
        cycle, _changed = self._save_defaults(cycle, quota=50, final_count=65)
        final_configuration = CourseExamConfiguration.objects.get(
            cycle_course=final_change_parent
        )
        self.assertEqual(
            (
                final_configuration.questions_required_per_faculty,
                final_configuration.questions_required_per_faculty_source,
                final_configuration.final_item_count,
                final_configuration.final_item_count_source,
            ),
            (50, "DEFAULT", 65, "DEFAULT"),
        )

    def test_missing_child_keeps_only_genuinely_absent_defaults_empty(self):
        cycle = self.make_cycle(default_questions_required_per_faculty=50)
        parent = self.make_course(cycle=cycle, code="ONE-DEFAULT")
        cycle, _changed = self._save_defaults(cycle, quota=55, final_count=None)
        configuration = CourseExamConfiguration.objects.get(cycle_course=parent)
        self.assertEqual(
            (
                configuration.questions_required_per_faculty,
                configuration.questions_required_per_faculty_source,
                configuration.final_item_count,
                configuration.final_item_count_source,
                configuration.cycle_defaults_revision_snapshot,
            ),
            (55, "DEFAULT", None, None, cycle.defaults_revision),
        )


class CAOCourseOverrideTests(Stage4TestCase):
    def _cycle_with_defaults(self):
        cycle = self.make_cycle(status=ExaminationCycle.Status.OPEN)
        return ExaminationCycleConfigurationService.save_cycle_configuration(
            cycle_id=cycle.id, tenant_id=self.tenant.id, user=self.manager,
            expected_updated_at=ExaminationCycleConfigurationService.transition_token(cycle),
            default_questions_required_per_faculty=50, default_final_item_count=50,
            contributor_instructions="CAO instructions", reason="Configured before opening",
        )[0]

    def _save(self, parent, **values):
        expected_revision = values.pop("expected_revision", 0)
        data = {
            "final_item_count": 50, "final_item_count_mode": "DEFAULT",
            "questions_required_per_faculty": 50, "questions_required_per_faculty_mode": "DEFAULT",
            "coverage": "Core outcomes", "additional_instructions": "",
            "contribution_deadline": self.future_deadline(),
        }
        data.update(values)
        return CourseExamConfigurationService.save_course_draft(
            cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.configurer,
            expected_revision=expected_revision, **data,
        )

    def test_independent_and_mixed_overrides_preserve_provenance(self):
        cycle = self._cycle_with_defaults()
        parent = self.make_course(cycle=cycle)
        configuration, _ = self._save(parent, questions_required_per_faculty=75, questions_required_per_faculty_mode="OVERRIDE")
        self.assertEqual((configuration.questions_required_per_faculty_source, configuration.final_item_count_source), ("OVERRIDE", "DEFAULT"))
        configuration, _ = self._save(parent, expected_revision=configuration.revision, final_item_count=60, final_item_count_mode="OVERRIDE", questions_required_per_faculty=75, questions_required_per_faculty_mode="OVERRIDE")
        self.assertEqual((configuration.questions_required_per_faculty, configuration.final_item_count), (75, 60))
        self.assertEqual((configuration.questions_required_per_faculty_source, configuration.final_item_count_source), ("OVERRIDE", "OVERRIDE"))

    def test_course_instruction_audit_uses_only_hash_and_character_length(self):
        cycle = self._cycle_with_defaults()
        parent = self.make_course(cycle=cycle)
        deadline = self.future_deadline().replace(microsecond=0)
        original_instructions = "Private course direction alpha."
        updated_instructions = "Private course direction beta."

        with patch("apps.departmental_exams.services.AuditService.log_event") as audit:
            configuration, changed = self._save(
                parent,
                coverage="Core outcomes A",
                additional_instructions=original_instructions,
                contribution_deadline=deadline,
            )
        self.assertTrue(changed)
        created_payload = audit.call_args.kwargs
        created_after = created_payload["after_data"]
        self.assertIsNone(created_payload["before_data"])
        self.assertNotIn("additional_instructions", created_after)
        self.assertEqual(
            created_after["additional_instructions_sha256"],
            hashlib.sha256(original_instructions.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            created_after["additional_instructions_length"],
            len(original_instructions),
        )
        self.assertEqual(created_after["coverage"], "Core outcomes A")
        self.assertEqual(created_after["final_item_count"], 50)
        self.assertEqual(created_after["final_item_count_source"], "DEFAULT")
        self.assertFalse(
            original_instructions
            in json.dumps(created_payload, default=str, sort_keys=True)
        )

        with patch("apps.departmental_exams.services.AuditService.log_event") as audit:
            configuration, changed = self._save(
                parent,
                expected_revision=configuration.revision,
                coverage="Core outcomes B",
                additional_instructions=updated_instructions,
                contribution_deadline=deadline,
            )
        self.assertTrue(changed)
        updated_payload = audit.call_args.kwargs
        before_data = updated_payload["before_data"]
        after_data = updated_payload["after_data"]
        self.assertNotIn("additional_instructions", before_data)
        self.assertNotIn("additional_instructions", after_data)
        self.assertEqual(
            before_data["additional_instructions_sha256"],
            hashlib.sha256(original_instructions.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            before_data["additional_instructions_length"],
            len(original_instructions),
        )
        self.assertEqual(
            after_data["additional_instructions_sha256"],
            hashlib.sha256(updated_instructions.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            after_data["additional_instructions_length"],
            len(updated_instructions),
        )
        self.assertEqual(before_data["coverage"], "Core outcomes A")
        self.assertEqual(after_data["coverage"], "Core outcomes B")
        serialized_payload = json.dumps(updated_payload, default=str, sort_keys=True)
        self.assertFalse(original_instructions in serialized_payload)
        self.assertFalse(updated_instructions in serialized_payload)

        original_revision = configuration.revision
        original_updated_at = configuration.updated_at
        with patch("apps.departmental_exams.services.AuditService.log_event") as audit:
            configuration, changed = self._save(
                parent,
                expected_revision=configuration.revision,
                coverage="Core outcomes B",
                additional_instructions=updated_instructions,
                contribution_deadline=deadline,
            )
        self.assertFalse(changed)
        self.assertEqual(configuration.revision, original_revision)
        self.assertEqual(configuration.updated_at, original_updated_at)
        audit.assert_not_called()

    def test_failed_course_instruction_save_emits_no_mutation_audit(self):
        cycle = self._cycle_with_defaults()
        parent = self.make_course(cycle=cycle)
        deadline = self.future_deadline().replace(microsecond=0)
        configuration, _changed = self._save(
            parent,
            additional_instructions="Existing private course direction.",
            contribution_deadline=deadline,
        )
        original_revision = configuration.revision
        original_updated_at = configuration.updated_at

        with patch("apps.departmental_exams.services.AuditService.log_event") as audit:
            with self.assertRaises(CourseExamConfigurationConflict):
                self._save(
                    parent,
                    expected_revision=configuration.revision + 1,
                    additional_instructions="Rejected private course direction.",
                    contribution_deadline=deadline,
                )
        audit.assert_not_called()
        configuration.refresh_from_db()
        self.assertEqual(configuration.revision, original_revision)
        self.assertEqual(configuration.updated_at, original_updated_at)
        self.assertEqual(
            configuration.additional_instructions,
            "Existing private course direction.",
        )

    def test_first_open_instruction_snapshot_audits_only_hash_and_length(self):
        instructions = "Confidential first-open instructions must remain outside audit metadata."
        cycle = self.make_cycle(
            status=ExaminationCycle.Status.OPEN,
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            instructions=instructions,
        )
        parent = self.make_course(cycle=cycle)
        configuration, _changed = self._save(parent)
        with patch("apps.departmental_exams.services.AuditService.log_event") as audit:
            configuration, changed = CourseExamConfigurationService.open_for_contribution(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                user=self.configurer,
                expected_revision=configuration.revision,
            )
        self.assertTrue(changed)
        payload = audit.call_args.kwargs
        self.assertEqual(
            payload["metadata"]["contributor_instructions_snapshot_sha256"],
            hashlib.sha256(instructions.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            payload["metadata"]["contributor_instructions_snapshot_length"],
            len(instructions),
        )
        self.assertNotIn(instructions, str(payload))

    def test_empty_first_open_instruction_snapshot_is_deterministic(self):
        cycle = self.make_cycle(
            status=ExaminationCycle.Status.OPEN,
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            instructions="",
        )
        parent = self.make_course(cycle=cycle)
        configuration, _changed = self._save(parent)
        with patch("apps.departmental_exams.services.AuditService.log_event") as audit:
            configuration, changed = CourseExamConfigurationService.open_for_contribution(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                user=self.configurer,
                expected_revision=configuration.revision,
            )
        self.assertTrue(changed)
        metadata = audit.call_args.kwargs["metadata"]
        self.assertEqual(
            metadata["contributor_instructions_snapshot_sha256"],
            hashlib.sha256(b"").hexdigest(),
        )
        self.assertEqual(metadata["contributor_instructions_snapshot_length"], 0)

    def test_lifecycle_reasons_are_hashed_in_audit_metadata(self):
        cycle = self.make_cycle(
            status=ExaminationCycle.Status.OPEN,
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
        )
        parent = self.make_course(cycle=cycle)
        configuration = self.make_configuration(
            parent,
            workflow=CourseExamConfiguration.WorkflowStatus.OPEN,
            opened_at=timezone.now(),
        )
        close_reason = "Confidential closure rationale for the department."
        with patch("apps.departmental_exams.services.AuditService.log_event") as audit:
            configuration, changed = CourseExamConfigurationService.close_contribution(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                user=self.configurer,
                expected_revision=configuration.revision,
                reason=close_reason,
            )
        self.assertTrue(changed)
        close_metadata = audit.call_args.kwargs["metadata"]
        self.assertEqual(
            close_metadata["close_reason_sha256"],
            hashlib.sha256(close_reason.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(close_metadata["close_reason_length"], len(close_reason))
        self.assertNotIn(close_reason, str(audit.call_args.kwargs))

        revert_reason = "Confidential reversion rationale for the department."
        with patch("apps.departmental_exams.services.AuditService.log_event") as audit:
            _configuration, changed = (
                CourseExamConfigurationService.revert_unpublished_configuration(
                    cycle_course_id=parent.id,
                    tenant_id=self.tenant.id,
                    user=self.configurer,
                    expected_revision=configuration.revision,
                    reason=revert_reason,
                )
            )
        self.assertTrue(changed)
        revert_metadata = audit.call_args.kwargs["metadata"]
        self.assertEqual(
            revert_metadata["revert_reason_sha256"],
            hashlib.sha256(revert_reason.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(revert_metadata["revert_reason_length"], len(revert_reason))
        self.assertNotIn(revert_reason, str(audit.call_args.kwargs))

    def test_override_boundaries_staleness_and_explicit_return_to_default(self):
        cycle = self._cycle_with_defaults()
        parent = self.make_course(cycle=cycle)
        for invalid in (49, 76):
            with self.assertRaises(ValidationError):
                self._save(parent, questions_required_per_faculty=invalid, questions_required_per_faculty_mode="OVERRIDE")
            with self.assertRaises(ValidationError):
                self._save(parent, final_item_count=invalid, final_item_count_mode="OVERRIDE")
        configuration, _ = self._save(parent, questions_required_per_faculty=50, questions_required_per_faculty_mode="OVERRIDE")
        configuration, _ = self._save(parent, expected_revision=configuration.revision, questions_required_per_faculty=75, questions_required_per_faculty_mode="OVERRIDE")
        self.assertEqual(configuration.questions_required_per_faculty, 75)
        configuration, _ = self._save(parent, expected_revision=configuration.revision, questions_required_per_faculty=75, questions_required_per_faculty_mode="OVERRIDE", final_item_count=75, final_item_count_mode="OVERRIDE")
        self.assertEqual(configuration.final_item_count, 75)
        with self.assertRaises(CourseExamConfigurationConflict):
            CourseExamConfigurationService.remove_overrides(cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.configurer, expected_revision=configuration.revision + 1, return_questions_required_per_faculty=True, return_final_item_count=False)
        configuration, changed = CourseExamConfigurationService.remove_overrides(cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.configurer, expected_revision=configuration.revision, return_questions_required_per_faculty=True, return_final_item_count=False)
        self.assertTrue(changed)
        self.assertEqual(configuration.questions_required_per_faculty_source, "DEFAULT")

    def test_opening_and_audit_failure_do_not_mutate_override_values(self):
        cycle = self._cycle_with_defaults()
        parent = self.make_course(cycle=cycle)
        configuration, _ = self._save(parent, final_item_count=75, final_item_count_mode="OVERRIDE")
        with patch("apps.departmental_exams.services.AuditService.log_event", side_effect=RuntimeError("audit failed")):
            with self.assertRaises(RuntimeError):
                CourseExamConfigurationService.remove_overrides(cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.configurer, expected_revision=configuration.revision, return_questions_required_per_faculty=False, return_final_item_count=True)
        configuration.refresh_from_db()
        self.assertEqual((configuration.final_item_count, configuration.final_item_count_source), (75, "OVERRIDE"))
        self.assertEqual(configuration.workflow_status, CourseExamConfiguration.WorkflowStatus.DRAFT)

    def test_override_removal_is_audited_only_when_a_selected_override_changes(self):
        cycle = self._cycle_with_defaults()
        parent = self.make_course(cycle=cycle)
        configuration, _changed = self._save(
            parent,
            final_item_count=60,
            final_item_count_mode="OVERRIDE",
        )
        original_revision = configuration.revision
        original_updated_at = configuration.updated_at
        with patch("apps.departmental_exams.services.AuditService.log_event") as audit:
            configuration, changed = CourseExamConfigurationService.remove_overrides(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                user=self.configurer,
                expected_revision=configuration.revision,
                return_questions_required_per_faculty=True,
                return_final_item_count=False,
            )
        self.assertFalse(changed)
        self.assertEqual(configuration.revision, original_revision)
        self.assertEqual(configuration.updated_at, original_updated_at)
        audit.assert_not_called()

        with patch("apps.departmental_exams.services.AuditService.log_event") as audit:
            configuration, changed = CourseExamConfigurationService.remove_overrides(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                user=self.configurer,
                expected_revision=configuration.revision,
                return_questions_required_per_faculty=True,
                return_final_item_count=True,
            )
        self.assertTrue(changed)
        self.assertEqual(
            (
                configuration.questions_required_per_faculty_source,
                configuration.final_item_count,
                configuration.final_item_count_source,
            ),
            ("DEFAULT", 50, "DEFAULT"),
        )
        self.assertEqual(configuration.revision, original_revision + 1)
        audit.assert_called_once()

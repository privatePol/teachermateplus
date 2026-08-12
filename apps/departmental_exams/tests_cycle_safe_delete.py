from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.db.models.deletion import ProtectedError
from django.urls import reverse
from django.utils import timezone

from apps.auditlog.models import AuditLog
from apps.core.services.settings import SystemSettingService
from apps.rbac.models import Permission, UserPermission, UserRole

from .cycle_deletion import ExaminationCycleSafeDeleteService
from .models import (
    BlockedContributionResolution,
    CourseExamConfiguration,
    CycleCourse,
    ExamBlueprint,
    ExamGenerationRevision,
    ExaminationCycle,
    FacultyContribution,
    FacultyContributionEligibilitySource,
    Question,
    QuestionImportBatch,
    QuestionImportRow,
)
from .stage4_test_support import Stage4TestCase


class ExaminationCycleSafeDeleteServiceTests(Stage4TestCase):
    def evaluate(self, cycle, *, user=None, tenant_id=None):
        return ExaminationCycleSafeDeleteService.evaluate(
            cycle_id=cycle.id,
            tenant_id=tenant_id or self.tenant.id,
            user=user or self.manager,
        )

    def delete(self, cycle, *, user=None, tenant_id=None):
        return ExaminationCycleSafeDeleteService.delete(
            cycle_id=cycle.id,
            tenant_id=tenant_id or self.tenant.id,
            user=user or self.manager,
        )

    def assert_blocked(self, cycle, code):
        result = self.evaluate(cycle)
        self.assertFalse(result.eligible)
        self.assertIn(code, {blocker.code for blocker in result.blockers})
        return result

    def make_contribution(self, parent, *, status=FacultyContribution.Status.DRAFT):
        return FacultyContribution.objects.create(
            cycle_course=parent,
            faculty_user=self.reviewer,
            source_campus=self.campus,
            quota_snapshot=50,
            configuration_revision_snapshot=1,
            status=status,
            submitted_at=(
                timezone.now()
                if status == FacultyContribution.Status.SUBMITTED
                else None
            ),
        )

    @staticmethod
    def make_question(contribution):
        return Question.objects.create(
            contribution=contribution,
            question_text="Safe fixture question?",
            choice_a="A",
            choice_b="B",
            choice_c="C",
            choice_d="D",
            correct_answer="A",
            difficulty=Question.Difficulty.EASY,
            position=1,
        )

    def make_generation_revision(self, parent, **overrides):
        values = {
            "cycle_course": parent,
            "revision_number": 1,
            "status": ExamGenerationRevision.Status.GENERATED,
            "current_marker": 1,
            "source_input_fingerprint": "a" * 64,
            "algorithm_version": "safe-delete-test",
            "generated_by": self.admin,
            "generation_trigger": ExamGenerationRevision.GenerationTrigger.MANUAL,
            "configuration_revision_snapshot": 1,
            "blueprint_revision_snapshot": 1,
            "roster_boundary_snapshot": "b" * 64,
            "final_item_count_snapshot": 50,
            "request_token_digest": "c" * 64,
            "minimum_overlap": 0,
            "proportional_score": 0,
            "contributors_represented": 1,
            "squared_contributor_concentration": 1,
        }
        values.update(overrides)
        return ExamGenerationRevision.objects.create(**values)

    def audit(self, cycle, action, *, entity_type="ExaminationCycle", entity_id=None):
        return AuditLog.objects.create(
            action=action,
            portal="ADMIN",
            entity_type=entity_type,
            entity_id=str(entity_id or cycle.id),
            tenant=self.tenant,
            metadata_json={"cycle_id": cycle.id},
        )

    def test_draft_setup_variants_are_eligible(self):
        cycle = self.make_cycle(default_coverage="Safe setup coverage")
        parent = self.make_course(cycle=cycle)
        parent.reviewer = self.reviewer
        parent.save(update_fields=["reviewer", "updated_at"])
        self.make_configuration(parent, coverage_source="DEFAULT")
        eligibility = self.evaluate(cycle)
        self.assertTrue(eligibility.eligible)
        self.assertEqual(eligibility.counts.cycle_courses, 1)
        self.assertEqual(eligibility.counts.offering_snapshots, 1)
        self.assertEqual(eligibility.counts.draft_configurations, 1)

    def test_exempt_only_setup_is_eligible(self):
        cycle = self.make_cycle()
        parent = self.make_course(cycle=cycle)
        parent.inclusion_status = CycleCourse.InclusionStatus.EXEMPT
        parent.exemption_category = CycleCourse.ExemptionCategory.PRACTICUM_OJT
        parent.exemption_reason = "Approved setup-only exemption."
        parent.exemption_changed_by = self.manager
        parent.exemption_changed_at = timezone.now()
        parent.save()
        eligibility = self.evaluate(cycle)
        self.assertTrue(eligibility.eligible)
        self.assertEqual(eligibility.counts.exempt_courses, 1)

    def test_open_setup_only_production_shaped_cycle_is_deletable(self):
        cycle = self.make_cycle(status=ExaminationCycle.Status.OPEN)
        parents = [
            self.make_course(cycle=cycle, code=f"PROD-{index}")
            for index in range(3)
        ]
        self.make_configuration(parents[0])
        for action in (
            "DE_EXAM_CYCLE_CREATED",
            "DE_EXAM_CYCLE_OPENED",
            "DE_EXAM_CYCLE_CONFIGURATION_UPDATED",
            "DE_EXAM_CYCLE_COURSE_ADMIN_UPDATED",
        ):
            self.audit(cycle, action)

        eligibility = self.evaluate(cycle)
        self.assertTrue(eligibility.eligible)
        self.assertEqual(eligibility.counts.cycle_courses, 3)
        self.assertEqual(eligibility.counts.offering_snapshots, 3)
        self.assertEqual(eligibility.counts.draft_configurations, 1)

        result = self.delete(cycle)
        self.assertTrue(result.deleted)
        self.assertFalse(ExaminationCycle.objects.filter(id=cycle.id).exists())

    def test_manual_and_automatic_setup_only_cycles_use_same_policy(self):
        for index, mode in enumerate(ExaminationCycle.ProcessingMode.values):
            cycle = self.make_cycle(scope_suffix=f"MODE-{index}")
            cycle.processing_mode = mode
            cycle.save(update_fields=["processing_mode", "updated_at"])
            self.make_course(cycle=cycle, department=None, code=f"MODE-{index}")
            self.assertTrue(self.evaluate(cycle).eligible)

    def test_closed_cycle_is_blocked(self):
        self.assert_blocked(
            self.make_cycle(status=ExaminationCycle.Status.CLOSED), "cycle_closed"
        )

    def test_open_or_ever_opened_configuration_is_blocked(self):
        for index, values in enumerate(
            (
                {"workflow": CourseExamConfiguration.WorkflowStatus.OPEN},
                {
                    "workflow": CourseExamConfiguration.WorkflowStatus.DRAFT,
                    "opened_at": timezone.now(),
                },
            )
        ):
            cycle = self.make_cycle(scope_suffix=f"OPENED-{index}")
            parent = self.make_course(cycle=cycle, code=f"OPENED-{index}")
            self.make_configuration(parent, **values)
            self.assert_blocked(cycle, "contribution_workflow_started")

    def test_reopen_history_is_blocked(self):
        cycle = self.make_cycle()
        parent = self.make_course(cycle=cycle)
        configuration = self.make_configuration(parent)
        configuration.reopened_contribution_deadline = self.future_deadline()
        configuration.save(update_fields=["reopened_contribution_deadline", "updated_at"])
        self.assert_blocked(cycle, "reopen_history")

    def test_initialized_or_synchronized_roster_is_blocked(self):
        for index, revision in enumerate((1, 2)):
            cycle = self.make_cycle(scope_suffix=f"ROSTER-{index}")
            parent = self.make_course(cycle=cycle, code=f"ROSTER-{index}")
            configuration = self.make_configuration(parent)
            configuration.contributor_roster_initialized_at = timezone.now()
            configuration.contributor_roster_initialized_by = self.manager
            configuration.contributor_roster_revision = revision
            configuration.save(
                update_fields=[
                    "contributor_roster_initialized_at",
                    "contributor_roster_initialized_by",
                    "contributor_roster_revision",
                    "updated_at",
                ]
            )
            self.assert_blocked(cycle, "roster_initialized")

    def test_any_faculty_contribution_or_eligibility_source_is_blocked(self):
        for index, status in enumerate(FacultyContribution.Status.values):
            cycle = self.make_cycle(scope_suffix=f"CONTRIB-{index}")
            parent = self.make_course(cycle=cycle, code=f"CONTRIB-{index}")
            contribution = self.make_contribution(parent, status=status)
            if index == 0:
                FacultyContributionEligibilitySource.objects.create(
                    contribution=contribution,
                    assignment_id_snapshot=100,
                    offering_id_snapshot=200,
                    tenant_id_snapshot=self.tenant.id,
                    campus_id_snapshot=self.campus.id,
                )
            self.assert_blocked(cycle, "faculty_contributions")

    def test_question_and_import_activity_are_blocked(self):
        cycle = self.make_cycle()
        parent = self.make_course(cycle=cycle)
        contribution = self.make_contribution(parent)
        self.make_question(contribution)
        batch = QuestionImportBatch.objects.create(
            tenant=self.tenant,
            contribution=contribution,
            uploading_user=self.reviewer,
            status=QuestionImportBatch.Status.READY,
            contribution_revision_snapshot=1,
            file_sha256="d" * 64,
            filename_sha256="e" * 64,
            total_rows=1,
            valid_rows=1,
            expires_at=self.future_deadline(),
        )
        QuestionImportRow.objects.create(
            batch=batch,
            row_number=1,
            payload={"fixture": True},
            fingerprint="f" * 64,
        )
        codes = {blocker.code for blocker in self.evaluate(cycle).blockers}
        self.assertIn("questions", codes)
        self.assertIn("question_imports", codes)

    def test_blocked_contributor_resolution_is_blocked(self):
        cycle = self.make_cycle()
        parent = self.make_course(cycle=cycle)
        contribution = self.make_contribution(parent)
        BlockedContributionResolution.objects.create(
            tenant=self.tenant,
            cycle_course=parent,
            contribution=contribution,
            reason="Preserve this accepted blocked evidence.",
            resolved_by=self.manager,
            contribution_revision_snapshot=1,
            roster_revision_snapshot=1,
            blocked_at_snapshot=timezone.now(),
            source_evidence_sha256="a" * 64,
        )
        self.assert_blocked(cycle, "contributor_resolution")

    def test_blueprint_activity_is_blocked(self):
        cycle = self.make_cycle()
        parent = self.make_course(cycle=cycle)
        ExamBlueprint.objects.create(
            cycle_course=parent,
            created_by=self.manager,
            updated_by=self.manager,
        )
        self.assert_blocked(cycle, "blueprint")

    def test_automatic_processing_state_is_blocked(self):
        cycle = self.make_cycle()
        parent = self.make_course(cycle=cycle)
        configuration = self.make_configuration(parent)
        configuration.automatic_processing_status = (
            CourseExamConfiguration.AutomaticProcessingStatus.BLOCKED
        )
        configuration.automatic_processing_code = "NOT_READY"
        configuration.automatic_processed_at = timezone.now()
        configuration.save(
            update_fields=[
                "automatic_processing_status",
                "automatic_processing_code",
                "automatic_processed_at",
                "updated_at",
            ]
        )
        self.assert_blocked(cycle, "automatic_processing")

    def test_generated_and_locked_revisions_are_blocked(self):
        for index, overrides in enumerate(
            (
                {},
                {
                    "status": ExamGenerationRevision.Status.LOCKED,
                    "locked_at": timezone.now(),
                    "locked_by": self.manager,
                    "approval_attestation_version": "v1",
                },
            )
        ):
            cycle = self.make_cycle(scope_suffix=f"GEN-{index}")
            parent = self.make_course(cycle=cycle, code=f"GEN-{index}")
            self.make_generation_revision(parent, **overrides)
            self.assert_blocked(cycle, "generation")

    def test_superseded_r1_and_current_r2_are_blocked(self):
        cycle = self.make_cycle()
        parent = self.make_course(cycle=cycle)
        first = self.make_generation_revision(parent)
        ExamGenerationRevision.objects.filter(id=first.id).update(
            status=ExamGenerationRevision.Status.SUPERSEDED,
            current_marker=None,
        )
        self.make_generation_revision(
            parent,
            revision_number=2,
            request_token_digest="z" * 64,
            supersedes=first,
        )
        self.assert_blocked(cycle, "generation")

    def test_nonsetup_and_unknown_linked_audits_fail_closed(self):
        for index, action in enumerate(
            (
                "DE_EXAM_APPROVED_LOCKED",
                "DE_EXAM_CONTRIBUTION_REOPENED",
                "DE_EXAM_FUTURE_UNKNOWN_ACTION",
                "DE_EXAM_CONTRIBUTOR_ROSTER_INITIALIZED",
            )
        ):
            cycle = self.make_cycle(scope_suffix=f"AUDIT-{index}")
            self.audit(cycle, action)
            self.assert_blocked(cycle, "historical_activity")

    def test_wrong_tenant_no_permission_direct_deny_and_inactive_user_fail_closed(self):
        cycle = self.make_cycle()
        SystemSettingService.set(
            "FEATURE_DEPARTMENTAL_EXAM_BUILDER_ENABLED",
            True,
            tenant_id=self.other_tenant.id,
            value_type="BOOL",
        )
        with self.assertRaises(ExaminationCycle.DoesNotExist):
            self.evaluate(cycle, user=self.admin, tenant_id=self.other_tenant.id)
        with self.assertRaises(PermissionDenied):
            self.evaluate(cycle, user=self.configurer)

        permission = Permission.objects.get(code="departmental_exams.manage_cycles")
        UserPermission.objects.create(
            user=self.manager,
            permission=permission,
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.campus,
        )
        with self.assertRaises(PermissionDenied):
            self.evaluate(cycle)
        UserPermission.objects.all().delete()
        self.manager.is_active = False
        self.manager.save(update_fields=["is_active"])
        with self.assertRaises(PermissionDenied):
            self.evaluate(cycle)

    def test_inactive_role_and_feature_off_fail_closed(self):
        cycle = self.make_cycle()
        UserRole.objects.filter(user=self.manager).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            self.evaluate(cycle)
        UserRole.objects.filter(user=self.manager).update(is_active=True)
        SystemSettingService.set(
            "FEATURE_DEPARTMENTAL_EXAM_BUILDER_ENABLED",
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )
        with self.assertRaises(PermissionDenied):
            self.evaluate(cycle, user=self.admin)

    def test_exact_setup_descendants_deleted_and_master_data_and_audits_preserved(self):
        cycle = self.make_cycle()
        parent = self.make_course(cycle=cycle)
        course_id = parent.course_id
        self.make_configuration(parent)
        old_audit = self.audit(cycle, "DE_EXAM_CYCLE_CREATED")
        unrelated = self.make_cycle(scope_suffix="UNRELATED")

        result = self.delete(cycle)

        self.assertTrue(result.deleted)
        self.assertFalse(ExaminationCycle.objects.filter(id=cycle.id).exists())
        self.assertTrue(type(parent.course).objects.filter(id=course_id).exists())
        self.assertTrue(type(self.year).objects.filter(id=self.year.id).exists())
        self.assertTrue(type(self.term).objects.filter(id=self.term.id).exists())
        self.assertTrue(type(self.manager).objects.filter(id=self.manager.id).exists())
        self.assertTrue(AuditLog.objects.filter(id=old_audit.id).exists())
        self.assertTrue(ExaminationCycle.objects.filter(id=unrelated.id).exists())
        deletion_audit = AuditLog.objects.get(action="DE_EXAM_CYCLE_DELETED")
        self.assertEqual(deletion_audit.entity_id, str(cycle.id))
        self.assertEqual(
            deletion_audit.metadata_json["cycle_courses_removed"], 1
        )
        self.assertNotIn("coverage", deletion_audit.metadata_json)
        self.assertNotIn("questions", deletion_audit.metadata_json)

    def test_unexpected_protected_error_rolls_back_every_delete(self):
        cycle = self.make_cycle()
        parent = self.make_course(cycle=cycle)
        configuration = self.make_configuration(parent)
        with patch(
            "apps.departmental_exams.cycle_deletion.ExaminationCycle.delete",
            side_effect=ProtectedError("fixture", []),
        ):
            result = self.delete(cycle)
        self.assertFalse(result.deleted)
        self.assertEqual(result.blockers[0].code, "protected_delete")
        self.assertTrue(ExaminationCycle.objects.filter(id=cycle.id).exists())
        self.assertTrue(CycleCourse.objects.filter(id=parent.id).exists())
        self.assertTrue(
            CourseExamConfiguration.objects.filter(id=configuration.id).exists()
        )

    def test_deletion_audit_failure_rolls_back_every_delete(self):
        cycle = self.make_cycle()
        parent = self.make_course(cycle=cycle)
        with patch(
            "apps.departmental_exams.cycle_deletion.AuditService.log_event",
            side_effect=RuntimeError("audit unavailable"),
        ):
            result = self.delete(cycle)
        self.assertFalse(result.deleted)
        self.assertEqual(result.blockers[0].code, "audit_failure")
        self.assertTrue(ExaminationCycle.objects.filter(id=cycle.id).exists())
        self.assertTrue(CycleCourse.objects.filter(id=parent.id).exists())


class ExaminationCycleSafeDeleteViewTests(Stage4TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.manager)

    def test_configuration_exposes_eligible_open_safe_delete_action(self):
        cycle = self.make_cycle(status=ExaminationCycle.Status.OPEN)
        response = self.client.get(
            reverse("departmental_exams:cycle_configuration", args=[cycle.id])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Delete Examination Cycle")
        self.assertContains(response, "This cycle is Open")

    def test_configuration_disables_delete_and_shows_human_blocker(self):
        cycle = self.make_cycle(status=ExaminationCycle.Status.CLOSED)
        response = self.client.get(
            reverse("departmental_exams:cycle_configuration", args=[cycle.id])
        )
        self.assertContains(response, "Delete Examination Cycle Unavailable")
        self.assertContains(
            response, "This examination cycle is Closed and must be preserved."
        )
        self.assertNotContains(response, "CourseExamConfiguration")

    def test_confirmation_uses_calculated_counts_and_no_typed_confirmation(self):
        cycle = self.make_cycle()
        parent = self.make_course(cycle=cycle)
        self.make_configuration(parent)
        response = self.client.get(
            reverse("departmental_exams:cycle_delete", args=[cycle.id])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Grouped course records")
        self.assertContains(response, "Existing audit history will be preserved")
        self.assertNotContains(response, "type the cycle")

    def test_post_revalidates_and_blocks_activity_created_after_get(self):
        cycle = self.make_cycle(status=ExaminationCycle.Status.OPEN)
        parent = self.make_course(cycle=cycle)
        url = reverse("departmental_exams:cycle_delete", args=[cycle.id])
        self.assertEqual(self.client.get(url).status_code, 200)
        FacultyContribution.objects.create(
            cycle_course=parent,
            faculty_user=self.reviewer,
            source_campus=self.campus,
            quota_snapshot=50,
            configuration_revision_snapshot=1,
        )
        response = self.client.post(url)
        self.assertEqual(response.status_code, 409)
        self.assertContains(
            response,
            "Faculty contribution records already exist.",
            status_code=409,
        )
        self.assertTrue(ExaminationCycle.objects.filter(id=cycle.id).exists())

    def test_successful_post_redirects_and_second_post_is_404(self):
        cycle = self.make_cycle()
        url = reverse("departmental_exams:cycle_delete", args=[cycle.id])
        response = self.client.post(url)
        self.assertRedirects(
            response,
            reverse("departmental_exams:cycle_list"),
            fetch_redirect_response=False,
        )
        self.assertEqual(self.client.post(url).status_code, 404)

    def test_feature_off_does_not_expose_mutation(self):
        cycle = self.make_cycle()
        url = reverse("departmental_exams:cycle_delete", args=[cycle.id])
        SystemSettingService.set(
            "FEATURE_DEPARTMENTAL_EXAM_BUILDER_ENABLED",
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )
        self.assertEqual(self.client.get(url).status_code, 403)

"""Responsibility reassignment protections introduced by Stage 4."""

from django.core.exceptions import PermissionDenied, ValidationError

from django.utils import timezone
from apps.academics.models import FacultyAssignment
from apps.auditlog.models import AuditLog

from .models import CourseExamConfiguration, FacultyContribution
from .services import (
    CourseExamConfigurationService,
    CycleCourseAdministrationService,
)
from .stage4_test_support import Stage4TestCase


class Stage4ResponsibilityTests(Stage4TestCase):
    def _draft_configuration(self):
        cycle = self.make_cycle(mode="PER_COURSE")
        parent = self.make_course(cycle=cycle)
        configuration, _ = CourseExamConfigurationService.save_course_draft(
            cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.configurer, expected_revision=0,
            final_item_count=50, questions_required_per_faculty=15, coverage="Draft coverage",
            additional_instructions="", contribution_deadline=self.future_deadline(),
        )
        return parent, configuration

    def test_pre_open_reassignment_preserves_draft_and_increments_revision_with_audit(self):
        parent, configuration = self._draft_configuration()
        # The original actor is not permitted to transfer ownership outside their exact scope.
        with self.assertRaises(PermissionDenied):
            CycleCourseAdministrationService.update_responsibility(
                cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.configurer,
                responsible_department=self.other_department, reviewer=None,
            )
        parent, changed = CycleCourseAdministrationService.update_responsibility(
            cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.admin,
            responsible_department=self.other_department, reviewer=None,
        )
        self.assertTrue(changed)
        configuration.refresh_from_db()
        self.assertEqual(configuration.workflow_status, CourseExamConfiguration.WorkflowStatus.DRAFT)
        self.assertEqual(configuration.revision, 2)
        audit = AuditLog.objects.get(action="DE_EXAM_CYCLE_COURSE_ADMIN_UPDATED", entity_id=str(parent.id))
        self.assertTrue(audit.metadata_json["responsibility_changed"])
        self.assertEqual(audit.after_json["configuration_revision"], 2)

    def test_reassignment_is_blocked_after_first_open_or_downstream_activity(self):
        parent, configuration = self._draft_configuration()
        cycle = parent.cycle
        cycle.status = "OPEN"
        cycle.save(update_fields=["status"])
        configuration, _ = CourseExamConfigurationService.open_for_contribution(
            cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.configurer, expected_revision=configuration.revision,
        )
        with self.assertRaises(ValidationError):
            CycleCourseAdministrationService.update_responsibility(
                cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.admin,
                responsible_department=self.other_department, reviewer=None,
            )
        configuration.workflow_status = CourseExamConfiguration.WorkflowStatus.DRAFT
        configuration.opened_at = None
        configuration.opened_by = None
        configuration.save(update_fields=["workflow_status", "opened_at", "opened_by"])
        # A real downstream row remains an independent hard block.
        assignment = FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=parent.offering_snapshots.first().offering,
            faculty_user=self.admin,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
        )
        FacultyContribution.objects.create(
            cycle_course=parent,
            faculty_user=self.admin,
            source_assignment=assignment,
            source_campus=self.campus,
        )
        with self.assertRaises(ValidationError):
            CycleCourseAdministrationService.update_responsibility(
                cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.admin,
                responsible_department=self.other_department, reviewer=None,
            )

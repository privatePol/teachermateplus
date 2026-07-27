"""Route and service authorization parity tests for Stage 4."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from apps.core.services.settings import SystemSettingService
from apps.auditlog.models import AuditLog
from apps.rbac.models import Permission, UserPermission, UserRole

from .models import CycleCourse, ExaminationCycle
from .services import CourseExamConfigurationService, ExaminationCycleConfigurationService
from .stage4_test_support import Stage4TestCase


class Stage4AuthorizationTests(Stage4TestCase):
    def _cycle_and_parent(self):
        cycle = self.make_cycle(mode=ExaminationCycle.ItemCountMode.PER_COURSE)
        parent = self.make_course(cycle=cycle)
        return cycle, parent

    def test_feature_off_denies_cycle_and_course_configuration_routes_for_get_and_post(self):
        cycle, parent = self._cycle_and_parent()
        SystemSettingService.set("FEATURE_DEPARTMENTAL_EXAM_BUILDER_ENABLED", False, tenant_id=self.tenant.id, value_type="BOOL")
        for user, url, post in (
            (self.manager, reverse("departmental_exams:cycle_configuration", args=[cycle.id]), {"expected_updated_at": "x"}),
            (self.manager, reverse("departmental_exams:cycle_open", args=[cycle.id]), {"expected_updated_at": "x"}),
            (self.manager, reverse("departmental_exams:cycle_close", args=[cycle.id]), {"expected_updated_at": "x"}),
            (self.configurer, reverse("departmental_exams:course_configuration", args=[parent.id]), {"expected_revision": 0}),
            (self.configurer, reverse("departmental_exams:course_contribution_open", args=[parent.id]), {"expected_revision": 0}),
            (self.configurer, reverse("departmental_exams:course_contribution_close", args=[parent.id]), {"expected_revision": 0, "reason": "A valid administrative reason"}),
            (self.configurer, reverse("departmental_exams:course_contribution_reopen", args=[parent.id]), {"expected_revision": 0}),
            (self.configurer, reverse("departmental_exams:course_configuration_revert", args=[parent.id]), {"expected_revision": 0, "reason": "A valid administrative reason"}),
        ):
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.client.post(url, post).status_code, 403)

    def test_manager_and_configurer_authority_are_separate_on_direct_routes(self):
        cycle, parent = self._cycle_and_parent()
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse("departmental_exams:cycle_configuration", args=[cycle.id])).status_code, 200)
        self.assertEqual(self.client.get(reverse("departmental_exams:course_configuration", args=[parent.id])).status_code, 403)
        self.client.force_login(self.configurer)
        self.assertEqual(self.client.get(reverse("departmental_exams:cycle_configuration", args=[cycle.id])).status_code, 403)
        self.assertEqual(self.client.get(reverse("departmental_exams:course_configuration", args=[parent.id])).status_code, 200)

    def test_reviewer_is_read_only_and_direct_deny_wins(self):
        cycle, parent = self._cycle_and_parent()
        parent.reviewer = self.reviewer
        parent.save(update_fields=["reviewer"])
        self.client.force_login(self.reviewer)
        self.assertEqual(self.client.get(reverse("departmental_exams:assigned_course_examinations")).status_code, 200)
        self.assertEqual(self.client.get(reverse("departmental_exams:course_configuration", args=[parent.id])).status_code, 403)
        UserPermission.objects.create(
            user=self.configurer,
            permission=Permission.objects.get(code="departmental_exams.configure"),
            grant_type=UserPermission.GrantType.DENY, tenant=self.tenant, campus=self.campus,
        )
        self.client.force_login(self.configurer)
        self.assertEqual(self.client.get(reverse("departmental_exams:course_configuration", args=[parent.id])).status_code, 403)

    def test_exact_department_campus_active_user_role_and_tenant_checks_apply_to_writer(self):
        cycle, parent = self._cycle_and_parent()
        outsider = self.make_user("outsider", self.other_department, ("admin_portal.access", "departmental_exams.configure"))
        with self.assertRaises(PermissionDenied):
            CourseExamConfigurationService.save_course_draft(
                cycle_course_id=parent.id, tenant_id=self.tenant.id, user=outsider, expected_revision=0,
                final_item_count=20, questions_required_per_faculty=5, coverage="Scope test",
                additional_instructions="", contribution_deadline=self.future_deadline(),
            )
        membership = UserRole.objects.get(user=self.configurer)
        membership.is_active = False
        membership.save(update_fields=["is_active"])
        with self.assertRaises(PermissionDenied):
            CourseExamConfigurationService.save_course_draft(
                cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.configurer, expected_revision=0,
                final_item_count=20, questions_required_per_faculty=5, coverage="Scope test",
                additional_instructions="", contribution_deadline=self.future_deadline(),
            )
        with self.assertRaises(ExaminationCycle.DoesNotExist):
            ExaminationCycleConfigurationService.save_cycle_configuration(
                cycle_id=cycle.id, tenant_id=self.other_tenant.id, user=self.manager,
                expected_updated_at="x", item_count_mode=ExaminationCycle.ItemCountMode.PER_COURSE,
                fixed_final_item_count=None, contributor_instructions="",
            )

    def _open_cycle_with_valid_draft(self):
        cycle = self.make_cycle(mode=ExaminationCycle.ItemCountMode.PER_COURSE)
        cycle, _ = ExaminationCycleConfigurationService.open_cycle(
            cycle_id=cycle.id, tenant_id=self.tenant.id, user=self.manager,
            expected_updated_at=ExaminationCycleConfigurationService.transition_token(cycle),
        )
        parent = self.make_course(cycle=cycle)
        configuration, _ = CourseExamConfigurationService.save_course_draft(
            cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.admin, expected_revision=0,
            final_item_count=20, questions_required_per_faculty=5, coverage="Required coverage",
            additional_instructions="", contribution_deadline=self.future_deadline(),
        )
        return parent, configuration

    def _assert_open_rejection_does_not_mutate(self, *, parent, configuration):
        before = {
            "workflow_status": configuration.workflow_status,
            "revision": configuration.revision,
            "opened_at": configuration.opened_at,
            "opened_by_id": configuration.opened_by_id,
            "closed_at": configuration.closed_at,
            "closed_by_id": configuration.closed_by_id,
            "audit_count": AuditLog.objects.filter(
                entity_id=str(configuration.id)
            ).count(),
        }
        with self.assertRaises(ValidationError):
            CourseExamConfigurationService.open_for_contribution(
                cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.admin, expected_revision=configuration.revision,
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

    def test_superuser_cannot_open_null_responsibility(self):
        parent, configuration = self._open_cycle_with_valid_draft()
        CycleCourse.objects.filter(pk=parent.id).update(responsible_department=None)
        parent.refresh_from_db()
        self._assert_open_rejection_does_not_mutate(
            parent=parent, configuration=configuration
        )

    def test_superuser_cannot_open_inactive_responsibility(self):
        parent, configuration = self._open_cycle_with_valid_draft()
        parent.responsible_department.is_active = False
        parent.responsible_department.save(update_fields=["is_active"])
        self._assert_open_rejection_does_not_mutate(
            parent=parent, configuration=configuration
        )

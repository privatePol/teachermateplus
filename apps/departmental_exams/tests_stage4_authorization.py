"""Authorization and confirmation-route tests for the CAO amendment."""

from html.parser import HTMLParser
from urllib.parse import urlsplit

from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from unittest.mock import patch

from apps.core.services.settings import SystemSettingService
from apps.auditlog.models import AuditLog
from apps.rbac.models import Permission, UserPermission, UserRole

from .models import CourseExamConfiguration, ExaminationCycle
from .services import CourseExamConfigurationService, ExaminationCycleConfigurationService
from .stage4_test_support import Stage4TestCase
from . import views


class _CycleDefaultsConfirmationParser(HTMLParser):
    """Extract the signed state and transport from the form a browser receives."""

    def __init__(self):
        super().__init__()
        self._current_form = None
        self.action = None
        self.method = None
        self.confirmation_state = None
        self.has_csrf_token = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "form":
            self._current_form = attributes
        elif tag == "input" and self._current_form is not None:
            if attributes.get("name") == "csrfmiddlewaretoken":
                self.has_csrf_token = True
            if attributes.get("name") == "confirmation_state":
                self.action = self._current_form.get("action")
                self.method = self._current_form.get("method", "get").lower()
                self.confirmation_state = attributes.get("value")

    def handle_endtag(self, tag):
        if tag == "form":
            self._current_form = None


class CAOAuthorizationRouteTests(Stage4TestCase):
    def _cycle_and_parent(self):
        cycle = self.make_cycle(default_questions_required_per_faculty=50, default_final_item_count=50)
        return cycle, self.make_course(cycle=cycle)

    def test_feature_off_denies_configuration_and_confirmation_get_post(self):
        cycle, parent = self._cycle_and_parent()
        SystemSettingService.set("FEATURE_DEPARTMENTAL_EXAM_BUILDER_ENABLED", False, tenant_id=self.tenant.id, value_type="BOOL")
        cases = (
            (self.manager, reverse("departmental_exams:cycle_configuration", args=[cycle.id]), {"expected_updated_at": "x"}),
            (self.configurer, reverse("departmental_exams:course_configuration", args=[parent.id]), {"expected_revision": 0}),
            (self.configurer, reverse("departmental_exams:course_remove_overrides", args=[parent.id]), {"expected_revision": 0}),
        )
        for user, url, payload in cases:
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.client.post(url, payload).status_code, 403)

    def test_manager_configurer_reviewer_and_direct_deny_surfaces_remain_separate(self):
        cycle, parent = self._cycle_and_parent()
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse("departmental_exams:cycle_configuration", args=[cycle.id])).status_code, 200)
        self.assertEqual(self.client.get(reverse("departmental_exams:course_configuration", args=[parent.id])).status_code, 403)
        self.client.force_login(self.configurer)
        self.assertEqual(self.client.get(reverse("departmental_exams:cycle_configuration", args=[cycle.id])).status_code, 403)
        self.assertEqual(self.client.get(reverse("departmental_exams:course_configuration", args=[parent.id])).status_code, 200)
        self.client.force_login(self.reviewer)
        self.assertEqual(self.client.get(reverse("departmental_exams:course_configuration", args=[parent.id])).status_code, 403)
        UserPermission.objects.create(user=self.configurer, permission=Permission.objects.get(code="departmental_exams.configure"), grant_type=UserPermission.GrantType.DENY, tenant=self.tenant, campus=self.campus)
        self.client.force_login(self.configurer)
        self.assertEqual(self.client.get(reverse("departmental_exams:course_configuration", args=[parent.id])).status_code, 403)

    def test_wrong_department_inactive_membership_and_wrong_tenant_fail_service_authorization(self):
        cycle, parent = self._cycle_and_parent()
        outsider = self.make_user("cao-outsider", self.other_department, ("admin_portal.access", "departmental_exams.configure"))
        kwargs = dict(cycle_course_id=parent.id, tenant_id=self.tenant.id, expected_revision=0, questions_required_per_faculty=50, questions_required_per_faculty_mode="DEFAULT", final_item_count=50, final_item_count_mode="DEFAULT", coverage="Core outcomes", additional_instructions="", contribution_deadline=self.future_deadline())
        with self.assertRaises(PermissionDenied):
            CourseExamConfigurationService.save_course_draft(user=outsider, **kwargs)
        membership = UserRole.objects.get(user=self.configurer)
        membership.is_active = False
        membership.save(update_fields=["is_active"])
        with self.assertRaises(PermissionDenied):
            CourseExamConfigurationService.save_course_draft(user=self.configurer, **kwargs)
        with self.assertRaises(ExaminationCycle.DoesNotExist):
            ExaminationCycleConfigurationService.save_cycle_configuration(cycle_id=cycle.id, tenant_id=self.other_tenant.id, user=self.manager, expected_updated_at="x", default_questions_required_per_faculty=50, default_final_item_count=50, contributor_instructions="")

    def test_inactive_users_are_denied_for_all_stage4_lifecycle_mutations(self):
        cycle = self.make_cycle(
            status=ExaminationCycle.Status.OPEN,
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
        )
        parent = self.make_course(cycle=cycle)
        configuration = self.make_configuration(parent)
        self.configurer.is_active = False
        self.configurer.save(update_fields=["is_active"])
        course_calls = (
            lambda: CourseExamConfigurationService.save_course_draft(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                user=self.configurer,
                expected_revision=configuration.revision,
                questions_required_per_faculty=50,
                questions_required_per_faculty_mode="DEFAULT",
                final_item_count=50,
                final_item_count_mode="DEFAULT",
                coverage="Core outcomes",
                additional_instructions="",
                contribution_deadline=self.future_deadline(),
            ),
            lambda: CourseExamConfigurationService.remove_overrides(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                user=self.configurer,
                expected_revision=configuration.revision,
                return_questions_required_per_faculty=True,
                return_final_item_count=False,
            ),
            lambda: CourseExamConfigurationService.open_for_contribution(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                user=self.configurer,
                expected_revision=configuration.revision,
            ),
            lambda: CourseExamConfigurationService.close_contribution(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                user=self.configurer,
                expected_revision=configuration.revision,
                expected_roster_revision=configuration.contributor_roster_revision,
                reason="Administrative closure requested by the department",
            ),
            lambda: CourseExamConfigurationService.reopen_contribution(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                user=self.configurer,
                expected_revision=configuration.revision,
            ),
            lambda: CourseExamConfigurationService.revert_unpublished_configuration(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                user=self.configurer,
                expected_revision=configuration.revision,
                reason="Administrative reversion requested by the department",
            ),
        )
        for call in course_calls:
            with self.assertRaises(PermissionDenied):
                call()

        self.manager.is_active = False
        self.manager.save(update_fields=["is_active"])
        with self.assertRaises(PermissionDenied):
            ExaminationCycleConfigurationService.save_cycle_configuration(
                cycle_id=cycle.id,
                tenant_id=self.tenant.id,
                user=self.manager,
                expected_updated_at=ExaminationCycleConfigurationService.transition_token(
                    cycle
                ),
                default_questions_required_per_faculty=55,
                default_final_item_count=50,
                contributor_instructions="Inactive manager must not save defaults",
                reason="Inactive manager lifecycle mutation must be denied",
            )

    @staticmethod
    def _configuration_snapshot(configuration):
        return {
            "questions_required_per_faculty": configuration.questions_required_per_faculty,
            "questions_required_per_faculty_source": configuration.questions_required_per_faculty_source,
            "final_item_count": configuration.final_item_count,
            "final_item_count_source": configuration.final_item_count_source,
            "cycle_defaults_revision_snapshot": configuration.cycle_defaults_revision_snapshot,
            "coverage": configuration.coverage,
            "additional_instructions": configuration.additional_instructions,
            "contribution_deadline": configuration.contribution_deadline,
            "workflow_status": configuration.workflow_status,
            "opened_at": configuration.opened_at,
            "opened_by_id": configuration.opened_by_id,
            "closed_at": configuration.closed_at,
            "closed_by_id": configuration.closed_by_id,
            "revision": configuration.revision,
            "updated_at": configuration.updated_at,
        }

    def test_closed_cycle_override_removal_get_denies_before_confirmation_state(self):
        cycle = self.make_cycle(
            status=ExaminationCycle.Status.CLOSED,
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            scope_suffix="closed-override-get",
        )
        parent = self.make_course(cycle=cycle, code="CLOSED-OVERRIDE-GET")
        configuration = self.make_configuration(
            parent,
            quota=60,
            final_count=65,
            quota_source="OVERRIDE",
            final_source="OVERRIDE",
        )
        configuration.additional_instructions = "Private closed-cycle direction."
        configuration.save(update_fields=["additional_instructions"])
        snapshot = self._configuration_snapshot(configuration)
        child_count = CourseExamConfiguration.objects.filter(cycle_course=parent).count()
        audit_count = AuditLog.objects.count()
        url = reverse("departmental_exams:course_remove_overrides", args=[parent.id])

        self.client.force_login(self.configurer)
        with patch("apps.departmental_exams.views.CourseOverrideRemovalForm") as form_class, patch(
            "apps.departmental_exams.views._cycle_defaults_confirmation_token"
        ) as confirmation_token:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 404)
        self.assertNotContains(response, "confirmation_state", status_code=404)
        self.assertNotContains(
            response,
            "Return selected overrides to defaults",
            status_code=404,
        )
        form_class.assert_not_called()
        confirmation_token.assert_not_called()

        configuration.refresh_from_db()
        self.assertEqual(self._configuration_snapshot(configuration), snapshot)
        self.assertEqual(
            CourseExamConfiguration.objects.filter(cycle_course=parent).count(),
            child_count,
        )
        self.assertEqual(AuditLog.objects.count(), audit_count)

        self.client.force_login(self.reviewer)
        with patch("apps.departmental_exams.views.CourseOverrideRemovalForm") as form_class:
            unauthorized_response = self.client.get(url)
        self.assertEqual(unauthorized_response.status_code, 403)
        form_class.assert_not_called()
        configuration.refresh_from_db()
        self.assertEqual(self._configuration_snapshot(configuration), snapshot)
        self.assertEqual(AuditLog.objects.count(), audit_count)

    def test_draft_and_open_cycles_still_render_valid_override_removal_get(self):
        for status, suffix in (
            (ExaminationCycle.Status.DRAFT, "draft-override-get"),
            (ExaminationCycle.Status.OPEN, "open-override-get"),
        ):
            with self.subTest(status=status):
                cycle = self.make_cycle(
                    status=status,
                    default_questions_required_per_faculty=50,
                    default_final_item_count=50,
                    scope_suffix=suffix,
                )
                parent = self.make_course(cycle=cycle, code=suffix.upper())
                self.make_configuration(
                    parent,
                    quota=60,
                    final_count=65,
                    quota_source="OVERRIDE",
                    final_source="OVERRIDE",
                )
                self.client.force_login(self.configurer)
                response = self.client.get(
                    reverse(
                        "departmental_exams:course_remove_overrides",
                        args=[parent.id],
                    )
                )
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Return course overrides to cycle defaults")

    def _closed_cycle_action_fixture(self, *, scope_suffix):
        cycle = self.make_cycle(
            status=ExaminationCycle.Status.CLOSED,
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            instructions="Closed-cycle instructions",
            scope_suffix=scope_suffix,
        )
        draft_parent = self.make_course(cycle=cycle, code="CLOSED-DRAFT")
        draft_configuration = self.make_configuration(
            draft_parent,
            quota=60,
            final_count=65,
        )
        open_parent = self.make_course(cycle=cycle, code="CLOSED-OPEN")
        open_configuration = self.make_configuration(
            open_parent,
            workflow=CourseExamConfiguration.WorkflowStatus.OPEN,
            opened_at=timezone.now(),
        )
        closed_parent = self.make_course(cycle=cycle, code="CLOSED-CLOSED")
        closed_configuration = self.make_configuration(
            closed_parent,
            workflow=CourseExamConfiguration.WorkflowStatus.CLOSED,
            opened_at=timezone.now(),
        )
        self.make_course(cycle=cycle, code="CLOSED-MISSING")
        CourseExamConfiguration.objects.filter(pk=closed_configuration.pk).update(
            closed_at=timezone.now(),
            closed_by_id=self.configurer.id,
        )
        closed_configuration.refresh_from_db()
        return cycle, (
            (draft_parent, draft_configuration),
            (open_parent, open_configuration),
            (closed_parent, closed_configuration),
        )

    def test_closed_cycle_service_action_matrix_is_side_effect_free(self):
        cycle, rows = self._closed_cycle_action_fixture(scope_suffix="service-matrix")
        (draft_parent, draft_configuration), (open_parent, open_configuration), (
            closed_parent,
            closed_configuration,
        ) = rows
        cycle_snapshot = {
            "status": cycle.status,
            "default_questions_required_per_faculty": cycle.default_questions_required_per_faculty,
            "default_final_item_count": cycle.default_final_item_count,
            "defaults_revision": cycle.defaults_revision,
            "contributor_instructions": cycle.contributor_instructions,
            "updated_at": cycle.updated_at,
        }
        configuration_snapshots = {
            configuration.pk: self._configuration_snapshot(configuration)
            for _parent, configuration in rows
        }
        configuration_count = CourseExamConfiguration.objects.filter(
            cycle_course__cycle=cycle
        ).count()
        course_calls = (
            (
                "course configuration save",
                lambda: CourseExamConfigurationService.save_course_draft(
                    cycle_course_id=draft_parent.id,
                    tenant_id=self.tenant.id,
                    user=self.configurer,
                    expected_revision=draft_configuration.revision,
                    questions_required_per_faculty=50,
                    questions_required_per_faculty_mode="DEFAULT",
                    final_item_count=50,
                    final_item_count_mode="DEFAULT",
                    coverage="Attempted closed-cycle change",
                    additional_instructions="",
                    contribution_deadline=self.future_deadline(),
                ),
            ),
            (
                "course open",
                lambda: CourseExamConfigurationService.open_for_contribution(
                    cycle_course_id=draft_parent.id,
                    tenant_id=self.tenant.id,
                    user=self.configurer,
                    expected_revision=draft_configuration.revision,
                ),
            ),
            (
                "course close",
                lambda: CourseExamConfigurationService.close_contribution(
                    cycle_course_id=open_parent.id,
                    tenant_id=self.tenant.id,
                    user=self.configurer,
                    expected_revision=open_configuration.revision,
                    expected_roster_revision=open_configuration.contributor_roster_revision,
                    reason="Closed cycle must reject a course close mutation",
                ),
            ),
            (
                "course reopen",
                lambda: CourseExamConfigurationService.reopen_contribution(
                    cycle_course_id=closed_parent.id,
                    tenant_id=self.tenant.id,
                    user=self.configurer,
                    expected_revision=closed_configuration.revision,
                ),
            ),
            (
                "course revert to Draft",
                lambda: CourseExamConfigurationService.revert_unpublished_configuration(
                    cycle_course_id=closed_parent.id,
                    tenant_id=self.tenant.id,
                    user=self.configurer,
                    expected_revision=closed_configuration.revision,
                    reason="Closed cycle must reject a course revert mutation",
                ),
            ),
            (
                "course override removal",
                lambda: CourseExamConfigurationService.remove_overrides(
                    cycle_course_id=draft_parent.id,
                    tenant_id=self.tenant.id,
                    user=self.configurer,
                    expected_revision=draft_configuration.revision,
                    return_questions_required_per_faculty=True,
                    return_final_item_count=True,
                ),
            ),
        )
        with patch("apps.departmental_exams.services.AuditService.log_event") as audit:
            for label, call in course_calls:
                with self.subTest(surface="service", action=label):
                    with self.assertRaises(ValidationError):
                        call()
            with self.subTest(surface="service", action="cycle defaults apply"):
                with self.assertRaises(ValidationError):
                    ExaminationCycleConfigurationService.save_cycle_configuration(
                        cycle_id=cycle.id,
                        tenant_id=self.tenant.id,
                        user=self.manager,
                        expected_updated_at=ExaminationCycleConfigurationService.transition_token(
                            cycle
                        ),
                        default_questions_required_per_faculty=55,
                        default_final_item_count=60,
                        contributor_instructions="Attempted closed-cycle defaults",
                        reason="Closed cycle must reject default application",
                    )

            # Authorization remains authoritative even when lifecycle state is Closed.
            with self.assertRaises(PermissionDenied):
                CourseExamConfigurationService.save_course_draft(
                    cycle_course_id=draft_parent.id,
                    tenant_id=self.tenant.id,
                    user=self.reviewer,
                    expected_revision=draft_configuration.revision,
                    questions_required_per_faculty=50,
                    questions_required_per_faculty_mode="DEFAULT",
                    final_item_count=50,
                    final_item_count_mode="DEFAULT",
                    coverage="Unauthorized closed-cycle attempt",
                    additional_instructions="",
                    contribution_deadline=self.future_deadline(),
                )
            with self.assertRaises(PermissionDenied):
                ExaminationCycleConfigurationService.save_cycle_configuration(
                    cycle_id=cycle.id,
                    tenant_id=self.tenant.id,
                    user=self.reviewer,
                    expected_updated_at=ExaminationCycleConfigurationService.transition_token(
                        cycle
                    ),
                    default_questions_required_per_faculty=55,
                    default_final_item_count=60,
                    contributor_instructions="Unauthorized closed-cycle defaults",
                )
            audit.assert_not_called()

        cycle.refresh_from_db()
        self.assertEqual(
            {
                "status": cycle.status,
                "default_questions_required_per_faculty": cycle.default_questions_required_per_faculty,
                "default_final_item_count": cycle.default_final_item_count,
                "defaults_revision": cycle.defaults_revision,
                "contributor_instructions": cycle.contributor_instructions,
                "updated_at": cycle.updated_at,
            },
            cycle_snapshot,
        )
        self.assertEqual(
            CourseExamConfiguration.objects.filter(cycle_course__cycle=cycle).count(),
            configuration_count,
        )
        for _parent, configuration in rows:
            configuration.refresh_from_db()
            self.assertEqual(
                self._configuration_snapshot(configuration),
                configuration_snapshots[configuration.pk],
            )

    def test_closed_cycle_route_matrix_hides_actions_and_denies_mutations(self):
        cycle, rows = self._closed_cycle_action_fixture(scope_suffix="route-matrix")
        (draft_parent, draft_configuration), (open_parent, open_configuration), (
            closed_parent,
            closed_configuration,
        ) = rows
        snapshots = {
            configuration.pk: self._configuration_snapshot(configuration)
            for _parent, configuration in rows
        }
        self.client.force_login(self.configurer)
        page = self.client.get(
            reverse("departmental_exams:course_configuration", args=[draft_parent.id])
        )
        self.assertEqual(page.status_code, 200)
        for label in (
            "Save Draft Configuration",
            "Open for Faculty Contribution",
            "Close contribution",
            "Reopen contribution",
            "Revert to Draft",
            "Return selected overrides to defaults",
        ):
            self.assertNotContains(page, label)

        route_cases = (
            (
                "course configuration save",
                reverse("departmental_exams:course_configuration", args=[draft_parent.id]),
                {"expected_revision": draft_configuration.revision},
                404,
            ),
            (
                "course open",
                reverse("departmental_exams:course_contribution_open", args=[draft_parent.id]),
                {"expected_revision": draft_configuration.revision},
                404,
            ),
            (
                "course close",
                reverse("departmental_exams:course_contribution_close", args=[open_parent.id]),
                {
                    "expected_revision": open_configuration.revision,
                    "reason": "Closed cycle route must reject course closure",
                },
                404,
            ),
            (
                "course reopen",
                reverse("departmental_exams:course_contribution_reopen", args=[closed_parent.id]),
                {"expected_revision": closed_configuration.revision},
                404,
            ),
            (
                "course revert to Draft",
                reverse("departmental_exams:course_configuration_revert", args=[closed_parent.id]),
                {
                    "expected_revision": closed_configuration.revision,
                    "reason": "Closed cycle route must reject course reversion",
                },
                404,
            ),
            (
                "course override removal",
                reverse("departmental_exams:course_remove_overrides", args=[draft_parent.id]),
                {
                    "expected_revision": draft_configuration.revision,
                    "return_questions_required_per_faculty": "on",
                    "return_final_item_count": "on",
                },
                400,
            ),
        )
        with patch("apps.departmental_exams.services.AuditService.log_event") as audit:
            for label, url, payload, expected_status in route_cases:
                with self.subTest(surface="route", action=label):
                    self.assertEqual(
                        self.client.post(url, payload).status_code,
                        expected_status,
                    )
            audit.assert_not_called()

        apply_cycle = self.make_cycle(scope_suffix="route-defaults-confirmation")
        apply_parent = self.make_course(cycle=apply_cycle, code="CLOSED-APPLY-MISSING")
        self.client.force_login(self.manager)
        confirmation_state = self._post_cycle_defaults_for_confirmation(apply_cycle)
        apply_cycle.status = ExaminationCycle.Status.CLOSED
        apply_cycle.save(update_fields=["status"])
        apply_snapshot = (
            apply_cycle.default_questions_required_per_faculty,
            apply_cycle.default_final_item_count,
            apply_cycle.defaults_revision,
            apply_cycle.updated_at,
        )
        with patch("apps.departmental_exams.services.AuditService.log_event") as audit:
            self.assertEqual(
                self.client.post(
                    reverse(
                        "departmental_exams:cycle_apply_defaults",
                        args=[apply_cycle.id],
                    ),
                    {"confirmation_state": confirmation_state},
                ).status_code,
                404,
            )
            audit.assert_not_called()
        apply_cycle.refresh_from_db()
        self.assertEqual(
            (
                apply_cycle.default_questions_required_per_faculty,
                apply_cycle.default_final_item_count,
                apply_cycle.defaults_revision,
                apply_cycle.updated_at,
            ),
            apply_snapshot,
        )
        self.assertFalse(
            CourseExamConfiguration.objects.filter(cycle_course=apply_parent).exists()
        )
        for _parent, configuration in rows:
            configuration.refresh_from_db()
            self.assertEqual(
                self._configuration_snapshot(configuration),
                snapshots[configuration.pk],
            )

    def test_closed_configuration_hides_invalid_actions_and_keeps_cycle_defaults_immutable(self):
        cycle = self.make_cycle(
            status=ExaminationCycle.Status.OPEN,
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
        )
        parent = self.make_course(cycle=cycle)
        configuration = self.make_configuration(
            parent,
            workflow=CourseExamConfiguration.WorkflowStatus.CLOSED,
            opened_at=timezone.now(),
        )
        self.client.force_login(self.configurer)
        configuration_url = reverse("departmental_exams:course_configuration", args=[parent.id])
        response = self.client.get(configuration_url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Save Draft Configuration")
        self.assertNotContains(response, "Open for Faculty Contribution")
        self.assertNotContains(response, "Close contribution")
        self.assertNotContains(response, "Return selected overrides to defaults")
        self.assertContains(response, "Reopen contribution")
        self.assertContains(response, "Revert to Draft")
        self.assertEqual(
            self.client.get(
                reverse("departmental_exams:course_contribution_open", args=[parent.id])
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                reverse("departmental_exams:course_remove_overrides", args=[parent.id])
            ).status_code,
            404,
        )
        before = (
            configuration.questions_required_per_faculty,
            configuration.final_item_count,
            configuration.revision,
        )
        ExaminationCycleConfigurationService.save_cycle_configuration(
            cycle_id=cycle.id,
            tenant_id=self.tenant.id,
            user=self.manager,
            expected_updated_at=ExaminationCycleConfigurationService.transition_token(
                cycle
            ),
            default_questions_required_per_faculty=55,
            default_final_item_count=55,
            contributor_instructions="Updated defaults",
            reason="Open-cycle default correction for closed configuration matrix",
        )
        configuration.refresh_from_db()
        self.assertEqual(
            (
                configuration.questions_required_per_faculty,
                configuration.final_item_count,
                configuration.revision,
            ),
            before,
        )

    def _post_cycle_defaults_for_confirmation(self, cycle, *, instructions="Confidential CAO instructions", reason=""):
        response = self.client.post(
            reverse("departmental_exams:cycle_configuration", args=[cycle.id]),
            {
                "expected_updated_at": cycle.updated_at.isoformat(),
                "default_questions_required_per_faculty": 50,
                "default_final_item_count": 50,
                "contributor_instructions": instructions,
                "reason": reason,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response, "departmental_exams/admin/cycle_defaults_confirm.html"
        )
        return response.context["form"]["confirmation_state"].value()

    def _resign_confirmation_state(self, state, **updates):
        payload = signing.loads(
            state, salt=views._CYCLE_DEFAULTS_CONFIRMATION_SALT
        )
        payload.update(updates)
        return signing.dumps(payload, salt=views._CYCLE_DEFAULTS_CONFIRMATION_SALT)

    def test_signed_confirmation_carries_automatic_generation_policies(self):
        cycle = self.make_cycle(scope_suffix="automatic-policy-confirmation")
        self.client.force_login(self.manager)
        confirmation = self.client.post(
            reverse("departmental_exams:cycle_configuration", args=[cycle.id]),
            {
                "expected_updated_at": ExaminationCycleConfigurationService.transition_token(
                    cycle
                ),
                "default_questions_required_per_faculty": 50,
                "default_final_item_count": 50,
                "contributor_instructions": "",
                "automatic_campus_contribution_policy": (
                    ExaminationCycle.AutomaticCampusContributionPolicy.STRICT
                ),
                "automatic_contributor_completion_policy": (
                    ExaminationCycle.AutomaticContributorCompletionPolicy.REQUIRE_ALL
                ),
            },
        )
        self.assertEqual(confirmation.status_code, 200)
        state = confirmation.context["form"]["confirmation_state"].value()
        payload = signing.loads(state, salt=views._CYCLE_DEFAULTS_CONFIRMATION_SALT)
        self.assertEqual(
            payload["automatic_campus_contribution_policy"],
            ExaminationCycle.AutomaticCampusContributionPolicy.STRICT,
        )
        self.assertEqual(
            payload["automatic_contributor_completion_policy"],
            ExaminationCycle.AutomaticContributorCompletionPolicy.REQUIRE_ALL,
        )

        applied = self.client.post(
            reverse("departmental_exams:cycle_apply_defaults", args=[cycle.id]),
            {"confirmation_state": state},
        )

        self.assertEqual(applied.status_code, 302)
        cycle.refresh_from_db()
        self.assertEqual(
            cycle.automatic_campus_contribution_policy,
            ExaminationCycle.AutomaticCampusContributionPolicy.STRICT,
        )
        self.assertEqual(
            cycle.automatic_contributor_completion_policy,
            ExaminationCycle.AutomaticContributorCompletionPolicy.REQUIRE_ALL,
        )

    def test_rendered_confirmation_form_posts_to_writer_and_propagates_defaults(self):
        cycle = self.make_cycle(scope_suffix="browser-shaped-confirmation")
        parent = self.make_course(cycle=cycle, code="BROWSER-CONFIRM")
        configuration_url = reverse(
            "departmental_exams:cycle_configuration", args=[cycle.id]
        )
        apply_url = reverse(
            "departmental_exams:cycle_apply_defaults", args=[cycle.id]
        )
        confidential_instructions = "Confidential browser-flow contributor instructions"
        confidential_reason = "Confidential browser-flow administrative reason"
        self.client.force_login(self.manager)
        confirmation = self.client.post(
            configuration_url,
            {
                "expected_updated_at": ExaminationCycleConfigurationService.transition_token(
                    cycle
                ),
                "default_questions_required_per_faculty": 55,
                "default_final_item_count": 60,
                "contributor_instructions": confidential_instructions,
                "reason": confidential_reason,
            },
        )
        self.assertEqual(confirmation.status_code, 200)
        self.assertTemplateUsed(
            confirmation, "departmental_exams/admin/cycle_defaults_confirm.html"
        )
        parser = _CycleDefaultsConfirmationParser()
        parser.feed(confirmation.content.decode(confirmation.charset))
        self.assertEqual(parser.method, "post")
        self.assertEqual(parser.action, apply_url)
        self.assertTrue(parser.has_csrf_token)
        self.assertTrue(parser.confirmation_state)
        self.assertNotContains(confirmation, confidential_instructions)
        self.assertNotContains(confirmation, confidential_reason)
        self.assertNotIn(confidential_instructions, parser.action)
        self.assertNotIn(confidential_reason, parser.action)
        self.assertEqual(urlsplit(parser.action).query, "")

        with patch.object(
            ExaminationCycleConfigurationService,
            "save_cycle_configuration",
            wraps=ExaminationCycleConfigurationService.save_cycle_configuration,
        ) as writer:
            applied = self.client.post(
                parser.action,
                {"confirmation_state": parser.confirmation_state},
            )
        writer.assert_called_once()
        self.assertRedirects(
            applied,
            configuration_url,
            fetch_redirect_response=False,
        )
        self.assertEqual(urlsplit(applied["Location"]).query, "")
        self.assertNotIn(confidential_instructions, applied["Location"])
        self.assertNotIn(confidential_reason, applied["Location"])
        cycle.refresh_from_db()
        configuration = CourseExamConfiguration.objects.get(cycle_course=parent)
        self.assertEqual(
            (
                cycle.default_questions_required_per_faculty,
                cycle.default_final_item_count,
                cycle.contributor_instructions,
            ),
            (55, 60, confidential_instructions),
        )
        self.assertEqual(
            (
                configuration.questions_required_per_faculty,
                configuration.questions_required_per_faculty_source,
                configuration.final_item_count,
                configuration.final_item_count_source,
                configuration.cycle_defaults_revision_snapshot,
            ),
            (55, "DEFAULT", 60, "DEFAULT", cycle.defaults_revision),
        )

    def test_confirmation_transport_is_post_only_and_fails_closed(self):
        cycle, _parent = self._cycle_and_parent()
        self.client.force_login(self.manager)
        apply_url = reverse("departmental_exams:cycle_apply_defaults", args=[cycle.id])
        state = self._post_cycle_defaults_for_confirmation(cycle)
        self.assertNotIn("Confidential CAO instructions", state)
        self.assertEqual(self.client.get(apply_url).status_code, 404)
        self.assertEqual(
            self.client.get(apply_url + "?token=invalid").status_code, 404
        )
        self.assertEqual(self.client.post(apply_url).status_code, 404)
        self.assertEqual(
            self.client.post(apply_url, {"confirmation_state": "malformed"}).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                apply_url,
                {
                    "confirmation_state": self._resign_confirmation_state(
                        state, purpose="wrong-purpose"
                    )
                },
            ).status_code,
            404,
        )
        cycle.status = ExaminationCycle.Status.CLOSED
        cycle.save(update_fields=["status"])
        self.assertEqual(
            self.client.post(apply_url, {"confirmation_state": state}).status_code,
            404,
        )

    def test_confirmation_binding_staleness_replay_and_hidden_timestamp_tampering(self):
        cycle, _parent = self._cycle_and_parent()
        self.client.force_login(self.manager)
        apply_url = reverse("departmental_exams:cycle_apply_defaults", args=[cycle.id])
        state = self._post_cycle_defaults_for_confirmation(cycle)
        response = self.client.post(
            apply_url,
            {
                "confirmation_state": state,
                "expected_updated_at": "tampered ordinary hidden value",
            },
        )
        self.assertEqual(response.status_code, 302)
        cycle.refresh_from_db()
        self.assertEqual(cycle.default_questions_required_per_faculty, 50)
        self.assertEqual(
            self.client.post(apply_url, {"confirmation_state": state}).status_code,
            409,
        )

        stale_state = self._post_cycle_defaults_for_confirmation(cycle)
        ExaminationCycleConfigurationService.save_cycle_configuration(
            cycle_id=cycle.id,
            tenant_id=self.tenant.id,
            user=self.manager,
            expected_updated_at=ExaminationCycleConfigurationService.transition_token(cycle),
            default_questions_required_per_faculty=55,
            default_final_item_count=50,
            contributor_instructions="Competing update",
        )
        self.assertEqual(
            self.client.post(
                apply_url, {"confirmation_state": stale_state}
            ).status_code,
            409,
        )

    def test_confirmation_rejects_wrong_actor_tenant_cycle_and_expiry(self):
        cycle, _parent = self._cycle_and_parent()
        self.client.force_login(self.manager)
        apply_url = reverse("departmental_exams:cycle_apply_defaults", args=[cycle.id])
        state = self._post_cycle_defaults_for_confirmation(cycle)
        manager_two = self.make_user(
            "manager-two",
            self.department,
            ("admin_portal.access", "departmental_exams.manage_cycles"),
        )
        self.client.force_login(manager_two)
        self.assertEqual(
            self.client.post(apply_url, {"confirmation_state": state}).status_code,
            403,
        )
        self.client.force_login(self.manager)
        self.assertEqual(
            self.client.post(
                apply_url,
                {
                    "confirmation_state": self._resign_confirmation_state(
                        state, tenant_id=self.other_tenant.id
                    )
                },
            ).status_code,
            404,
        )
        other_cycle = self.make_cycle(scope_suffix="wrong-cycle")
        self.assertEqual(
            self.client.post(
                reverse("departmental_exams:cycle_apply_defaults", args=[other_cycle.id]),
                {"confirmation_state": state},
            ).status_code,
            404,
        )
        with patch("django.core.signing.time.time", return_value=0):
            expired = self._post_cycle_defaults_for_confirmation(cycle)
        self.assertEqual(
            self.client.post(apply_url, {"confirmation_state": expired}).status_code,
            404,
        )

    def test_cycle_default_mutation_requires_post_csrf_and_signed_confirmation(self):
        cycle, _parent = self._cycle_and_parent()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.manager)
        configuration_url = reverse("departmental_exams:cycle_configuration", args=[cycle.id])
        response = client.post(configuration_url, {
            "expected_updated_at": cycle.updated_at.isoformat(),
            "default_questions_required_per_faculty": 50,
            "default_final_item_count": 50,
            "contributor_instructions": "CAO instructions",
        })
        self.assertEqual(response.status_code, 403)

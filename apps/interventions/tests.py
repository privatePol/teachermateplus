from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.academics.models import AcademicYear, Course, CourseOffering, FacultyAssignment, Section, Term
from apps.auditlog.models import AuditLog
from apps.core.context_processors import portal_menu
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.enrollment.models import Enrollment
from apps.grading.models import CourseTemplateAssignment, GradingTemplate, GradingTemplatePeriod
from apps.interventions.forms import FollowUpForm, InterventionActionForm
from apps.interventions.models import (
    AcademicInterventionAction,
    AcademicInterventionCase,
    AcademicInterventionDecisionRevision,
    AcademicInterventionFollowUp,
)
from apps.interventions.services import (
    AcademicConcernDetectionService,
    AcademicInterventionAuthorizationService,
    AcademicInterventionCaseService,
    AcademicInterventionConfigurationService,
)
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.students.models import Student
from apps.tenants.models import Campus, Department, Program, Tenant


class AcademicInterventionFixtureMixin:
    def setUp(self):
        super().setUp()
        self.factory = RequestFactory()
        self.tenant = Tenant.objects.create(code="INT", name="Intervention Test")
        self.campus = Campus.objects.create(tenant=self.tenant, code="MAIN", name="Main")
        self.department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="COL",
            name="College",
        )
        self.program = Program.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="BSIT",
            name="BSIT",
        )
        self.year = AcademicYear.objects.create(
            tenant=self.tenant,
            code="2026",
            name="AY 2026",
            start_date=date(2026, 6, 1),
            end_date=date(2027, 5, 31),
        )
        self.term = Term.objects.create(
            tenant=self.tenant,
            academic_year=self.year,
            code="T1",
            name="Term 1",
            sequence_no=1,
        )
        self.course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="INT101",
            title="Intervention",
        )
        self.section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="A",
            name="A",
        )
        self.offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=self.year,
            term=self.term,
            course=self.course,
            section=self.section,
        )
        self.template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="INT-T",
            name="Template",
            is_published=True,
            published_at=timezone.now(),
        )
        self.period = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="P",
            name="Period",
            sequence_no=1,
        )
        CourseTemplateAssignment.objects.create(
            course=self.course,
            grading_template=self.template,
            effective_from_term=self.term,
        )
        self.student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="1",
            last_name="Student",
            first_name="Test",
        )
        self.unenrolled_student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="2",
            last_name="Unenrolled",
            first_name="Test",
        )
        self.owner = self._user("owner")
        self.co_faculty = self._user("co-faculty")
        self.unassigned_faculty = self._user("unassigned")

        self.faculty_role = Role.objects.create(code="FACULTY_TEST", name="Faculty Test")
        self.admin_role = Role.objects.create(code="AREA_CHAIR_TEST", name="Area Chair Test")
        self.no_monitor_role = Role.objects.create(code="CAMPUS_ADMIN_TEST", name="Campus Admin Test")
        for code, module, action in (
            ("faculty_portal.access", "faculty_portal", "access"),
            ("admin_portal.access", "admin_portal", "access"),
            ("system_settings.update", "system_settings", "update"),
            ("academic_interventions.manage_own", "academic_interventions", "manage_own"),
            ("academic_interventions.monitor", "academic_interventions", "monitor"),
            ("academic_interventions.configure", "academic_interventions", "configure"),
            ("academic_interventions.view_disabled_archive", "academic_interventions", "view_disabled_archive"),
        ):
            Permission.objects.get_or_create(
                code=code,
                defaults={"module": module, "action": action},
            )
        for code in ("faculty_portal.access", "academic_interventions.manage_own"):
            RolePermission.objects.create(
                role=self.faculty_role,
                permission=Permission.objects.get(code=code),
            )
        for code in ("admin_portal.access", "academic_interventions.monitor"):
            RolePermission.objects.create(
                role=self.admin_role,
                permission=Permission.objects.get(code=code),
            )
        RolePermission.objects.create(
            role=self.no_monitor_role,
            permission=Permission.objects.get(code="admin_portal.access"),
        )
        for user in (self.owner, self.co_faculty, self.unassigned_faculty):
            UserRole.objects.create(
                user=user,
                role=self.faculty_role,
                tenant=self.tenant,
                campus=self.campus,
                department=self.department,
            )
        for user in (self.owner, self.co_faculty):
            FacultyAssignment.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                offering=self.offering,
                faculty_user=user,
                accepted_by=user,
                response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
                accepted_at=timezone.now(),
            )
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.year,
            term=self.term,
            student=self.student,
            course_offering=self.offering,
        )
        self.set_feature_enabled(True)

    def _user(self, username):
        return User.objects.create_user(
            username=username,
            email=f"{username}@example.test",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )

    def set_feature_enabled(self, enabled):
        SystemSettingService.set(
            FeatureSettingsService.STUDENT_ACADEMIC_INTERVENTION_TRACKING_ENABLED_KEY,
            enabled,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )

    def manual_case(self, owner=None, **overrides):
        owner = owner or self.owner
        values = {
            "tenant": self.tenant,
            "campus": self.campus,
            "offering": self.offering,
            "academic_year": self.year,
            "term": self.term,
            "grading_period": self.period,
            "student": self.student,
            "faculty_owner": owner,
            "identified_at": timezone.now(),
            "detection_source": AcademicInterventionCase.DetectionSource.MANUAL,
            "detection_code": "FACULTY_MANUAL",
            "distinct_concern_summary": "A distinct academic concern for faculty review.",
            "created_by": owner,
            "updated_by": owner,
        }
        values.update(overrides)
        return AcademicInterventionCase.objects.create(**values)

    def analytics_case(self, owner=None, fingerprint="same-fingerprint", **overrides):
        owner = owner or self.owner
        return self.manual_case(
            owner=owner,
            detection_source=AcademicInterventionCase.DetectionSource.ANALYTICS,
            distinct_concern_summary="",
            analytics_source_fingerprint=fingerprint,
            **overrides,
        )

    def intervention_request(self, user, method="get", path="/admin-portal/interventions/"):
        request = getattr(self.factory, method)(path)
        request.user = user
        request.scope = {
            "tenant_id": self.tenant.id,
            "tenant_ids": [self.tenant.id],
            "campus_id": self.campus.id,
            "campus_ids": [self.campus.id],
            "department_ids": [self.department.id],
        }
        return request

    def decision(self, case, decision=AcademicInterventionCase.Decision.CONDUCT, **kwargs):
        values = {
            "case_id": case.id,
            "user": self.owner,
            "decision": decision,
            "rationale": "",
        }
        values.update(kwargs)
        return AcademicInterventionCaseService.record_decision(**values)

    def action_form(self, *, status="PLANNED", planned_for=None, conducted_on=None, **overrides):
        data = {
            "intervention_type": "Faculty consultation",
            "status": status,
            "planned_for": planned_for or (timezone.localdate() + timedelta(days=1)),
            "conducted_on": conducted_on or "",
            "action_summary": "Review academic progress and agree on next steps.",
            "student_action_plan": "Complete the agreed academic work.",
            "cancellation_reason": "",
        }
        data.update(overrides)
        form = InterventionActionForm(data=data)
        self.assertTrue(form.is_valid(), form.errors)
        return form


class AcademicInterventionIntegrityTests(AcademicInterventionFixtureMixin, TestCase):
    def test_active_analytics_duplicate_is_limited_to_same_owner(self):
        self.analytics_case()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.analytics_case()
        other_case = self.analytics_case(owner=self.co_faculty)
        self.assertEqual(other_case.faculty_owner_id, self.co_faculty.id)

    def test_voided_analytics_case_allows_same_owner_recreation(self):
        case = self.analytics_case()
        case.voided_at = timezone.now()
        case.review_status = AcademicInterventionCase.ReviewStatus.VOIDED
        case.save(update_fields=["voided_at", "review_status", "updated_at"])
        self.assertIsNotNone(self.analytics_case().id)

    def test_manual_case_requires_distinct_concern_summary(self):
        case = self.manual_case()
        case.distinct_concern_summary = "short"
        with self.assertRaises(ValidationError):
            case.full_clean()

    def test_faculty_owner_is_immutable(self):
        case = self.manual_case()
        case.faculty_owner = self.co_faculty
        with self.assertRaises(ValidationError):
            case.save()

    def test_protected_relations_prevent_case_hard_delete(self):
        case = self.manual_case()
        AcademicInterventionFollowUp.objects.create(case=case, status="SCHEDULED")
        with self.assertRaises(Exception):
            case.delete()

    def test_one_active_planned_action_database_constraint(self):
        case = self.manual_case(faculty_decision="CONDUCT")
        AcademicInterventionAction.objects.create(
            case=case,
            intervention_type="First",
            status="PLANNED",
            planned_for=timezone.localdate(),
            action_summary="First plan",
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AcademicInterventionAction.objects.create(
                    case=case,
                    intervention_type="Second",
                    status="PLANNED",
                    planned_for=timezone.localdate(),
                    action_summary="Second plan",
                )


class AcademicInterventionAuthorizationTests(AcademicInterventionFixtureMixin, TestCase):
    def test_feature_defaults_disabled_for_tenant_without_setting(self):
        tenant = Tenant.objects.create(code="OFF", name="Default Off")
        self.assertFalse(
            FeatureSettingsService.is_student_academic_intervention_tracking_enabled(tenant_id=tenant.id)
        )

    def test_manage_own_permission_is_required_by_owner_service(self):
        case = self.manual_case()
        RolePermission.objects.filter(
            role=self.faculty_role,
            permission__code="academic_interventions.manage_own",
        ).delete()
        with self.assertRaises(PermissionDenied):
            AcademicInterventionAuthorizationService.require_owner(user=self.owner, case=case)

    def test_cofaculty_is_denied_owner_service(self):
        case = self.manual_case()
        with self.assertRaises(PermissionDenied):
            AcademicInterventionAuthorizationService.require_owner(user=self.co_faculty, case=case)

    def test_assignment_is_required_for_creation(self):
        with self.assertRaises(PermissionDenied):
            AcademicInterventionCaseService.create_manual(
                user=self.unassigned_faculty,
                offering_id=self.offering.id,
                student=self.student,
                grading_period_id=self.period.id,
                summary="A separate academic concern identified personally.",
            )

    def test_enrollment_is_required_for_creation(self):
        with self.assertRaises(PermissionDenied):
            AcademicInterventionCaseService.create_manual(
                user=self.owner,
                offering_id=self.offering.id,
                student=self.unenrolled_student,
                grading_period_id=self.period.id,
                summary="A separate academic concern identified personally.",
            )

    def test_candidate_service_is_blocked_when_feature_disabled(self):
        self.set_feature_enabled(False)
        with self.assertRaises(PermissionDenied):
            AcademicConcernDetectionService.candidates_for_offering(
                offering=self.offering,
                faculty_owner=self.owner,
            )

    def test_configuration_service_requires_explicit_permission(self):
        with self.assertRaises(PermissionDenied):
            AcademicInterventionConfigurationService.set_enabled(
                user=self.owner,
                tenant_id=self.tenant.id,
                campus_id=self.campus.id,
                enabled=False,
            )
        self.assertTrue(
            FeatureSettingsService.is_student_academic_intervention_tracking_enabled(tenant_id=self.tenant.id)
        )

    def test_configuration_service_allows_explicit_permission_and_audits_change(self):
        RolePermission.objects.create(
            role=self.faculty_role,
            permission=Permission.objects.get(code="academic_interventions.configure"),
        )
        AcademicInterventionConfigurationService.set_enabled(
            user=self.owner,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            enabled=False,
        )
        self.assertFalse(
            FeatureSettingsService.is_student_academic_intervention_tracking_enabled(tenant_id=self.tenant.id)
        )
        self.assertTrue(
            AuditLog.objects.filter(action="ACADEMIC_INTERVENTION_CONFIGURE", actor_user=self.owner).exists()
        )

    def test_disabled_archive_requires_monitor_and_archive_permissions(self):
        monitor = self._user("monitor")
        UserRole.objects.create(
            user=monitor,
            role=self.admin_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )
        self.set_feature_enabled(False)
        request = self.intervention_request(monitor)
        with self.assertRaises(PermissionDenied):
            AcademicInterventionAuthorizationService.require_admin_monitor(request=request)
        RolePermission.objects.create(
            role=self.admin_role,
            permission=Permission.objects.get(code="academic_interventions.view_disabled_archive"),
        )
        AcademicInterventionAuthorizationService.require_admin_monitor(request=request)

    def test_campus_admin_tenant_admin_and_guidance_roles_have_no_implicit_access(self):
        self.assertFalse(
            RolePermission.objects.filter(
                role=self.no_monitor_role,
                permission__code="academic_interventions.monitor",
            ).exists()
        )
        for code in ("CAMPUS_ADMIN", "TENANT_ADMIN", "GUIDANCE"):
            user = self._user(code.lower())
            role = Role.objects.create(code=f"{code}_NO_INTERVENTION", name=code)
            RolePermission.objects.create(
                role=role,
                permission=Permission.objects.get(code="admin_portal.access"),
            )
            UserRole.objects.create(user=user, role=role, tenant=self.tenant, campus=self.campus)
            with self.subTest(role=code), self.assertRaises(PermissionDenied):
                AcademicInterventionAuthorizationService.require_admin_monitor(
                    request=self.intervention_request(user)
                )


class AcademicInterventionWorkflowTests(AcademicInterventionFixtureMixin, TestCase):
    def test_each_decision_and_rationale_rule(self):
        for decision in AcademicInterventionCase.Decision.values:
            case = self.manual_case()
            kwargs = {}
            rationale = "" if decision == AcademicInterventionCase.Decision.CONDUCT else "Faculty rationale"
            if decision == AcademicInterventionCase.Decision.REFERRED:
                kwargs = {
                    "referral_destination": AcademicInterventionCase.ReferralDestination.GUIDANCE,
                    "referral_date": timezone.localdate(),
                    "referral_reason": "Academic progress consultation requested.",
                }
            with self.subTest(decision=decision):
                updated = self.decision(case, decision=decision, rationale=rationale, **kwargs)
                self.assertEqual(updated.faculty_decision, decision)
                self.assertEqual(updated.decision_revisions.count(), 1)

        for decision in set(AcademicInterventionCase.Decision.values) - {
            AcademicInterventionCase.Decision.CONDUCT
        }:
            case = self.manual_case()
            with self.subTest(missing_rationale=decision), self.assertRaises(ValidationError):
                self.decision(case, decision=decision, rationale="")

    def test_decision_supersession_preserves_immutable_history(self):
        case = self.manual_case()
        self.decision(case, decision="MONITOR", rationale="Original rationale")
        AcademicInterventionCaseService.record_decision(
            case_id=case.id,
            user=self.owner,
            decision="NO_INTERVENTION",
            rationale="Corrected rationale",
            supersede=True,
            correction_reason="Faculty corrected the original classification.",
        )
        revisions = list(case.decision_revisions.order_by("revision_no"))
        self.assertEqual([row.decision for row in revisions], ["MONITOR", "NO_INTERVENTION"])
        self.assertEqual(revisions[1].supersedes_id, revisions[0].id)
        self.assertEqual(revisions[0].rationale, "Original rationale")

    def test_supersession_requires_prior_decision_and_reason(self):
        case = self.manual_case()
        with self.assertRaises(ValidationError):
            self.decision(case, supersede=True, correction_reason="Reason")
        self.decision(case)
        with self.assertRaises(ValidationError):
            self.decision(case, supersede=True, correction_reason="")

    def test_planned_action_can_transition_to_conducted(self):
        case = self.manual_case()
        self.decision(case)
        action = AcademicInterventionCaseService.add_action(
            case_id=case.id,
            user=self.owner,
            form=self.action_form(),
        )
        update_form = self.action_form(
            status="CONDUCTED",
            planned_for=action.planned_for,
            conducted_on=timezone.localdate(),
        )
        updated = AcademicInterventionCaseService.update_action(
            case_id=case.id,
            action_id=action.id,
            user=self.owner,
            form=update_form,
        )
        case.refresh_from_db()
        self.assertEqual(updated.status, "CONDUCTED")
        self.assertEqual(case.review_status, AcademicInterventionCase.ReviewStatus.INTERVENTION_CONDUCTED)

    def test_only_one_active_planned_action_is_permitted_by_service(self):
        case = self.manual_case()
        self.decision(case)
        AcademicInterventionCaseService.add_action(case_id=case.id, user=self.owner, form=self.action_form())
        with self.assertRaises(ValidationError):
            AcademicInterventionCaseService.add_action(case_id=case.id, user=self.owner, form=self.action_form())

    def test_multiple_conducted_actions_are_permitted(self):
        case = self.manual_case()
        self.decision(case)
        for offset in (0, 1):
            AcademicInterventionCaseService.add_action(
                case_id=case.id,
                user=self.owner,
                form=self.action_form(
                    status="CONDUCTED",
                    conducted_on=timezone.localdate() - timedelta(days=offset),
                ),
            )
        self.assertEqual(case.actions.filter(status="CONDUCTED").count(), 2)

    def test_conduct_status_requires_conducted_action_and_conducted_case_cannot_be_voided(self):
        case = self.manual_case()
        self.decision(case)
        case.refresh_from_db()
        self.assertNotEqual(case.review_status, AcademicInterventionCase.ReviewStatus.INTERVENTION_CONDUCTED)
        AcademicInterventionCaseService.add_action(
            case_id=case.id,
            user=self.owner,
            form=self.action_form(status="CONDUCTED", conducted_on=timezone.localdate()),
        )
        with self.assertRaises(ValidationError):
            AcademicInterventionCaseService.void(case_id=case.id, user=self.owner, reason="Mistake")

    def test_followups_support_multiple_records_transitions_and_due_state(self):
        case = self.manual_case()
        yesterday = timezone.localdate() - timedelta(days=1)
        for due_on in (yesterday, timezone.localdate() + timedelta(days=2)):
            form = FollowUpForm(data={"due_on": due_on, "status": "SCHEDULED", "result_summary": ""})
            self.assertTrue(form.is_valid(), form.errors)
            AcademicInterventionCaseService.add_follow_up(case_id=case.id, user=self.owner, form=form)
        due, future = list(case.follow_ups.order_by("due_on"))
        self.assertTrue(due.is_due)
        self.assertEqual(due.effective_status_display, "Due")
        self.assertFalse(future.is_due)
        form = FollowUpForm(
            data={"due_on": yesterday, "status": "COMPLETED", "result_summary": "Academic follow-up completed."},
            instance=due,
        )
        self.assertTrue(form.is_valid(), form.errors)
        AcademicInterventionCaseService.update_follow_up(
            case_id=case.id,
            follow_up_id=due.id,
            user=self.owner,
            form=form,
        )
        due.refresh_from_db()
        self.assertEqual(due.status, "COMPLETED")
        self.assertIsNotNone(due.completed_on)

    def test_closed_and_voided_records_reject_mutations(self):
        closed = self.manual_case()
        AcademicInterventionCaseService.close(case_id=closed.id, user=self.owner)
        with self.assertRaises(ValidationError):
            self.decision(closed)
        with self.assertRaises(ValidationError):
            AcademicInterventionCaseService.add_follow_up(
                case_id=closed.id,
                user=self.owner,
                form=FollowUpForm(data={"status": "SCHEDULED"}),
            )
        voided = self.manual_case()
        AcademicInterventionCaseService.void(case_id=voided.id, user=self.owner, reason="Created by mistake")
        with self.assertRaises(ValidationError):
            AcademicInterventionCaseService.close(case_id=voided.id, user=self.owner)

    def test_referral_is_controlled_and_minimized(self):
        case = self.manual_case()
        with self.assertRaises(ValidationError):
            self.decision(
                case,
                decision="REFERRED",
                rationale="Academic referral",
                referral_destination="PRIVATE_COUNSELING_DETAILS",
                referral_date=timezone.localdate(),
                referral_reason="Academic progress review.",
            )
        updated = self.decision(
            case,
            decision="REFERRED",
            rationale="Academic referral",
            referral_destination="GUIDANCE",
            referral_date=timezone.localdate(),
            referral_reason="Academic progress review.",
        )
        self.assertEqual(updated.referral_destination, "GUIDANCE")
        self.assertFalse(hasattr(updated, "guidance_notes"))

    def test_workflow_operations_create_specific_audit_events(self):
        case = AcademicInterventionCaseService.create_manual(
            user=self.owner,
            offering_id=self.offering.id,
            student=self.student,
            grading_period_id=self.period.id,
            summary="A personally identified distinct academic concern.",
        )
        self.decision(case)
        action = AcademicInterventionCaseService.add_action(
            case_id=case.id,
            user=self.owner,
            form=self.action_form(),
        )
        update_form = self.action_form(
            status="CONDUCTED",
            planned_for=action.planned_for,
            conducted_on=timezone.localdate(),
        )
        AcademicInterventionCaseService.update_action(
            case_id=case.id,
            action_id=action.id,
            user=self.owner,
            form=update_form,
        )
        actions = set(AuditLog.objects.filter(entity_id=str(case.id)).values_list("action", flat=True))
        self.assertTrue(
            {
                "ACADEMIC_INTERVENTION_CREATE_MANUAL",
                "ACADEMIC_INTERVENTION_RECORD_DECISION",
                "ACADEMIC_INTERVENTION_PLAN_ACTION",
                "ACADEMIC_INTERVENTION_CONDUCT_ACTION",
            }.issubset(actions)
        )


class AcademicInterventionPrivacyAndPortalTests(AcademicInterventionFixtureMixin, TestCase):
    def test_disabled_navigation_and_direct_get_and_post_are_denied(self):
        case = self.manual_case()
        self.set_feature_enabled(False)
        request = self.intervention_request(self.owner, path="/faculty/academic-interventions/")
        request.scope.update({"tenant_id": self.tenant.id, "campus_id": self.campus.id})
        menu_context = portal_menu(request)
        self.assertFalse(menu_context["faculty_academic_interventions_enabled"])
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("faculty_portal:academic_intervention_list")).status_code, 403)
        self.assertEqual(
            self.client.post(
                reverse("faculty_portal:academic_intervention_decision", args=[case.id]),
                {"decision": "CONDUCT"},
            ).status_code,
            403,
        )

    def test_owner_list_and_detail_are_private_and_cofaculty_gets_404(self):
        own_case = self.manual_case()
        self.manual_case(owner=self.co_faculty, distinct_concern_summary="Other owner's private concern.")
        self.client.force_login(self.owner)
        response = self.client.get(reverse("faculty_portal:academic_intervention_list"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["cases"]), [own_case])
        self.assertEqual(
            self.client.get(reverse("faculty_portal:academic_intervention_detail", args=[own_case.id])).status_code,
            200,
        )
        self.client.force_login(self.co_faculty)
        self.assertEqual(
            self.client.get(reverse("faculty_portal:academic_intervention_detail", args=[own_case.id])).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                reverse("faculty_portal:academic_intervention_close", args=[own_case.id])
            ).status_code,
            404,
        )

    def test_list_filters_and_groups_records_by_grading_period_without_student_number(self):
        second_period = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="P2",
            name="Second Period",
            sequence_no=2,
        )
        self.manual_case()
        second_case = self.manual_case(grading_period=second_period)
        self.student.student_no = "PRIVATE-STUDENT-NUMBER"
        self.student.save(update_fields=["student_no", "updated_at"])

        self.client.force_login(self.owner)
        response = self.client.get(
            reverse("faculty_portal:academic_intervention_list"),
            {"offering_id": self.offering.id, "period_id": second_period.id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_period_id"], second_period.id)
        self.assertEqual(len(response.context["period_groups"]), 1)
        self.assertEqual(response.context["period_groups"][0]["period"], second_period)
        self.assertEqual(response.context["period_groups"][0]["cases"], [second_case])
        self.assertContains(response, "Second Period")
        self.assertContains(response, "Student, Test")
        self.assertNotContains(response, "PRIVATE-STUDENT-NUMBER")
        self.assertContains(response, "intervention-period-card")

    def test_detail_applies_bootstrap_form_classes_and_structured_section_styling(self):
        case = self.manual_case()
        self.client.force_login(self.owner)
        response = self.client.get(
            reverse("faculty_portal:academic_intervention_detail", args=[case.id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["decision_form"].fields["decision"].widget.attrs["class"],
            "form-control",
        )
        self.assertContains(response, "intervention-detail-hero")
        self.assertContains(response, "intervention-section-card")
        self.assertContains(response, "intervention-form")

    @patch("apps.interventions.services.FacultyPerformanceService.get_students_requiring_attention")
    def test_cross_owner_duplicate_lookup_does_not_disclose_other_case(self, mocked_attention):
        mocked_attention.return_value = [
            {
                "student": self.student,
                "student_id": self.student.id,
                "trend_label": "DECLINING",
                "primary_reason": "Declined by 4.00 points",
                "current_grade": 75,
                "previous_grade": 79,
                "missing_output_count": 0,
            }
        ]
        first = AcademicConcernDetectionService.candidates_for_offering(
            offering=self.offering,
            faculty_owner=self.owner,
        )[0]
        self.analytics_case(owner=self.co_faculty, fingerprint=first["fingerprint"])
        second = AcademicConcernDetectionService.candidates_for_offering(
            offering=self.offering,
            faculty_owner=self.owner,
        )[0]
        self.assertFalse(second["has_owner_case"])

    def test_admin_monitor_is_read_only_and_requires_explicit_permission(self):
        case = self.manual_case()
        monitor = self._user("monitor-readonly")
        UserRole.objects.create(
            user=monitor,
            role=self.admin_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )
        self.client.force_login(monitor)
        self.assertEqual(self.client.get(reverse("admin_portal:academic_intervention_monitor")).status_code, 200)
        self.assertEqual(
            self.client.get(reverse("admin_portal:academic_intervention_monitor_detail", args=[case.id])).status_code,
            200,
        )
        self.assertEqual(self.client.post(reverse("admin_portal:academic_intervention_monitor")).status_code, 405)
        self.assertEqual(
            self.client.post(reverse("admin_portal:academic_intervention_monitor_detail", args=[case.id])).status_code,
            405,
        )
        no_monitor = self._user("no-monitor")
        UserRole.objects.create(
            user=no_monitor,
            role=self.no_monitor_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )
        self.client.force_login(no_monitor)
        self.assertEqual(self.client.get(reverse("admin_portal:academic_intervention_monitor")).status_code, 403)

    def test_monitor_scope_isolates_cross_department_and_cross_campus_cases(self):
        visible = self.manual_case()
        other_department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="OTHER",
            name="Other Department",
        )
        other_program = Program.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=other_department,
            code="OTHER",
            name="Other",
        )
        other_course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=other_department,
            code="OTHER101",
            title="Other",
        )
        other_section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=other_department,
            program=other_program,
            code="O",
            name="O",
        )
        other_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=other_department,
            program=other_program,
            academic_year=self.year,
            term=self.term,
            course=other_course,
            section=other_section,
        )
        hidden = self.manual_case(offering=other_offering)
        other_campus = Campus.objects.create(tenant=self.tenant, code="BRANCH", name="Branch")
        branch_department = Department.objects.create(
            tenant=self.tenant,
            campus=other_campus,
            code="BRANCH",
            name="Branch Department",
        )
        branch_program = Program.objects.create(
            tenant=self.tenant,
            campus=other_campus,
            department=branch_department,
            code="BRANCH",
            name="Branch",
        )
        branch_course = Course.objects.create(
            tenant=self.tenant,
            campus=other_campus,
            department=branch_department,
            code="BR101",
            title="Branch",
        )
        branch_section = Section.objects.create(
            tenant=self.tenant,
            campus=other_campus,
            department=branch_department,
            program=branch_program,
            code="B",
            name="B",
        )
        branch_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=other_campus,
            department=branch_department,
            program=branch_program,
            academic_year=self.year,
            term=self.term,
            course=branch_course,
            section=branch_section,
        )
        hidden_cross_campus = self.manual_case(campus=other_campus, offering=branch_offering)
        monitor = self._user("scoped-monitor")
        UserRole.objects.create(
            user=monitor,
            role=self.admin_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )
        request = self.intervention_request(monitor)
        queryset = AcademicInterventionAuthorizationService.admin_queryset(request)
        self.assertIn(visible, queryset)
        self.assertNotIn(hidden, queryset)
        self.assertNotIn(hidden_cross_campus, queryset)

    def test_closed_detail_template_has_no_mutation_forms(self):
        case = self.manual_case()
        AcademicInterventionCaseService.close(case_id=case.id, user=self.owner)
        self.client.force_login(self.owner)
        response = self.client.get(reverse("faculty_portal:academic_intervention_detail", args=[case.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This record is read-only.")
        self.assertNotContains(response, "Save Faculty Decision")
        self.assertNotContains(response, "Save Follow-up")


class AcademicInterventionAnalyticsContractTests(AcademicInterventionFixtureMixin, TestCase):
    @patch("apps.interventions.services.FacultyPerformanceService.get_students_requiring_attention")
    def test_analytics_uses_only_approved_academic_indicators(self, mocked_attention):
        mocked_attention.return_value = [
            {
                "student": self.student,
                "student_id": self.student.id,
                "trend_label": "AT_RISK",
                "primary_reason": "Below passing grade; 2 missing outputs",
                "current_grade": 72,
                "previous_grade": 78,
                "missing_output_count": 2,
            }
        ]
        candidate = AcademicConcernDetectionService.candidates_for_offering(
            offering=self.offering,
            faculty_owner=self.owner,
        )[0]
        self.assertEqual(candidate["snapshot"]["indicator_codes"], ["AT_RISK", "MISSING_OUTPUTS"])
        self.assertNotIn("attendance", str(candidate["snapshot"]).lower())

    @patch("apps.interventions.services.FacultyPerformanceService.get_students_requiring_attention")
    def test_analytics_conversion_recomputes_snapshot_and_audits(self, mocked_attention):
        mocked_attention.return_value = [
            {
                "student": self.student,
                "student_id": self.student.id,
                "trend_label": "DECLINING",
                "primary_reason": "Declined by 4.00 points",
                "current_grade": 75,
                "previous_grade": 79,
                "missing_output_count": 0,
            }
        ]
        candidate = AcademicConcernDetectionService.candidates_for_offering(
            offering=self.offering,
            faculty_owner=self.owner,
        )[0]
        case = AcademicInterventionCaseService.create_analytics(
            user=self.owner,
            offering_id=self.offering.id,
            student=self.student,
            grading_period_id=self.period.id,
            fingerprint=candidate["fingerprint"],
            snapshot={"untrusted": "client supplied"},
        )
        self.assertNotIn("untrusted", case.concern_snapshot_json)
        self.assertEqual(case.concern_snapshot_json["indicator_codes"], ["DECLINING"])
        self.assertTrue(
            AuditLog.objects.filter(
                action="ACADEMIC_INTERVENTION_CREATE_ANALYTICS",
                entity_id=str(case.id),
            ).exists()
        )

    def test_templates_use_supportive_nonmandatory_wording_and_preserve_at_risk_monitor(self):
        repo_root = Path(__file__).resolve().parents[2]
        intervention_templates = " ".join(
            path.read_text(encoding="utf-8")
            for path in (repo_root / "templates" / "faculty_portal" / "academic_interventions").glob("*.html")
        )
        self.assertIn("Academic Concern — For Faculty Review", intervention_templates)
        self.assertNotIn("required intervention", intervention_templates.lower())
        at_risk_template = (repo_root / "templates" / "faculty_portal" / "student_at_risk_monitor.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("Review academic concern", at_risk_template)
        self.assertEqual(reverse("faculty_portal:student_at_risk_monitor"), "/faculty/at-risk-monitor/")

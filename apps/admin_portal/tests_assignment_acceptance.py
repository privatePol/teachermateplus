from datetime import date

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.academics.models import AcademicYear, Course, CourseOffering, FacultyAssignment, Section, Term
from apps.academics.services import FacultyAssignmentWorkflowService
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.enrollment.services import EnrollmentService
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.tenants.models import Campus, Department, Program, Tenant


class AdminFacultyAssignmentAcceptanceViewTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="NCBA", name="NCBA")
        self.campus = Campus.objects.create(tenant=self.tenant, code="NCBA-FAIRVIEW", name="Fairview")
        self.department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="FVW_COLL_IS",
            name="Fairview Information Systems",
        )
        self.program = Program.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="BSIT",
            name="BSIT",
        )
        self.academic_year = AcademicYear.objects.create(
            tenant=self.tenant,
            code="2025-2026",
            name="AY 2025-2026",
            start_date=date(2025, 6, 1),
            end_date=date(2026, 5, 31),
        )
        self.term = Term.objects.create(
            tenant=self.tenant,
            academic_year=self.academic_year,
            code="1ST",
            name="First Term",
            sequence_no=1,
            start_date=date(2025, 6, 1),
            end_date=date(2025, 10, 31),
        )
        self.course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="A132-ITAPPS",
            title="IT Application Tools",
        )
        self.section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="BSIT-1A",
            name="BSIT 1A",
        )
        self.offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=self.academic_year,
            term=self.term,
            course=self.course,
            section=self.section,
        )

        self.faculty_user = User.objects.create_user(
            username="faculty_admin_view",
            email="faculty_admin_view@example.com",
            password="testpass123",
            first_name="Faculty",
            last_name="Viewer",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        self.admin_user = User.objects.create_user(
            username="admin_assignment_view",
            email="admin_assignment_view@example.com",
            password="testpass123",
            first_name="Campus",
            last_name="Admin",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        self.faculty_user_two = User.objects.create_user(
            username="faculty_admin_view_two",
            email="faculty_admin_view_two@example.com",
            password="testpass123",
            first_name="Second",
            last_name="Faculty",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )

        faculty_role = Role.objects.create(code="FACULTY", name="Faculty")
        admin_role = Role.objects.create(code="CAMPUS_ADMIN", name="Campus Admin")
        faculty_access = Permission.objects.create(
            code="faculty_portal.access",
            module="faculty_portal",
            action="access",
        )
        admin_access = Permission.objects.create(
            code="admin_portal.access",
            module="admin_portal",
            action="access",
        )
        faculty_assignment_read = Permission.objects.create(
            code="faculty_assignments.read",
            module="faculty_assignments",
            action="read",
        )
        faculty_assignment_create = Permission.objects.create(
            code="faculty_assignments.create",
            module="faculty_assignments",
            action="create",
        )
        faculty_assignment_update = Permission.objects.create(
            code="faculty_assignments.update",
            module="faculty_assignments",
            action="update",
        )
        system_settings_update = Permission.objects.create(
            code="system_settings.update",
            module="system_settings",
            action="update",
        )
        RolePermission.objects.create(role=faculty_role, permission=faculty_access)
        RolePermission.objects.create(role=admin_role, permission=admin_access)
        RolePermission.objects.create(role=admin_role, permission=faculty_assignment_read)
        RolePermission.objects.create(role=admin_role, permission=faculty_assignment_create)
        RolePermission.objects.create(role=admin_role, permission=faculty_assignment_update)
        RolePermission.objects.create(role=admin_role, permission=system_settings_update)

        UserRole.objects.create(
            user=self.faculty_user,
            role=faculty_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )
        UserRole.objects.create(
            user=self.faculty_user_two,
            role=faculty_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )
        UserRole.objects.create(
            user=self.admin_user,
            role=admin_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )

        self.assignment = FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            faculty_user=self.faculty_user,
            is_primary=True,
        )
        self.second_course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="A133-TEST",
            title="Testing Course",
        )
        self.second_section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="BSIT-1B",
            name="BSIT 1B",
        )
        self.second_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=self.academic_year,
            term=self.term,
            course=self.second_course,
            section=self.second_section,
        )
        self.second_assignment = FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.second_offering,
            faculty_user=self.faculty_user_two,
            is_primary=True,
        )
        FacultyAssignmentWorkflowService.reset_response_window(self.assignment)
        self.assignment.save(
            update_fields=[
                "assignment_note",
                "accepted_at",
                "accepted_by",
                "response_status",
                "faculty_response_note",
                "responded_at",
                "response_due_at",
                "last_reminded_at",
                "reminder_count",
                "updated_at",
            ]
        )
        FacultyAssignmentWorkflowService.reset_response_window(self.second_assignment)
        self.second_assignment.save(
            update_fields=[
                "assignment_note",
                "accepted_at",
                "accepted_by",
                "response_status",
                "faculty_response_note",
                "responded_at",
                "response_due_at",
                "last_reminded_at",
                "reminder_count",
                "updated_at",
            ]
        )

    def test_admin_assignment_view_reports_pending_acceptance_metrics(self):
        self.client.force_login(self.admin_user)

        response = self.client.get(
            reverse("admin_portal:faculty_assignment_list"),
            {"faculty_user_id": self.faculty_user.id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["assigned_count"], 1)
        self.assertEqual(response.context["accepted_count"], 0)
        self.assertEqual(response.context["pending_acceptance_count"], 1)
        self.assertContains(response, "Pending Acceptance")
        self.assertContains(response, "Due Within 24 Hours")

    def test_admin_assignment_view_reports_accepted_assignment_details(self):
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.responded_at = self.assignment.accepted_at
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "responded_at", "updated_at"])

        self.client.force_login(self.admin_user)
        response = self.client.get(
            reverse("admin_portal:faculty_assignment_list"),
            {"faculty_user_id": self.faculty_user.id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["accepted_count"], 1)
        self.assertEqual(response.context["pending_acceptance_count"], 0)
        self.assertContains(response, "Accepted")
        self.assertContains(response, self.faculty_user.full_name)

    def test_admin_assignment_view_reports_clarification_count(self):
        self.assignment.response_status = FacultyAssignment.ResponseStatus.CLARIFICATION_REQUESTED
        self.assignment.responded_at = timezone.now()
        self.assignment.faculty_response_note = "Please clarify the room assignment."
        self.assignment.save(update_fields=["response_status", "responded_at", "faculty_response_note", "updated_at"])

        self.client.force_login(self.admin_user)
        response = self.client.get(
            reverse("admin_portal:faculty_assignment_list"),
            {"faculty_user_id": self.faculty_user.id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["clarification_count"], 1)
        self.assertContains(response, "Clarification Requested")
        self.assertContains(response, "Please clarify the room assignment.")

    def test_admin_assignment_view_reports_expired_count(self):
        self.assignment.response_status = FacultyAssignment.ResponseStatus.EXPIRED
        self.assignment.response_due_at = None
        self.assignment.responded_at = timezone.now()
        self.assignment.save(update_fields=["response_status", "response_due_at", "responded_at", "updated_at"])

        self.client.force_login(self.admin_user)
        response = self.client.get(
            reverse("admin_portal:faculty_assignment_list"),
            {"faculty_user_id": self.faculty_user.id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["expired_count"], 1)
        self.assertContains(response, "Expired")

    def test_admin_can_renew_expired_assignment_window(self):
        self.assignment.response_status = FacultyAssignment.ResponseStatus.EXPIRED
        self.assignment.response_due_at = None
        self.assignment.responded_at = timezone.now()
        self.assignment.save(update_fields=["response_status", "response_due_at", "responded_at", "updated_at"])

        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse("admin_portal:faculty_assignment_renew_window", kwargs={"assignment_id": self.assignment.id}),
            {"faculty_user_id": self.faculty_user.id},
        )

        self.assertRedirects(
            response,
            f"{reverse('admin_portal:faculty_assignment_list')}?faculty_user_id={self.faculty_user.id}",
        )

    def test_admin_can_set_enrollment_ownership_mode_from_configurable_features(self):
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse("admin_portal:configurable_features_settings"),
            {
                "enrollment_ownership_mode": "FACULTY_ALLOWED",
                "login_lockout_max_attempts": 5,
                "login_lockout_window_minutes": 15,
                "login_lockout_duration_minutes": 15,
                "faculty_assignment_response_window_days": 3,
                "faculty_assignment_first_reminder_days": 1,
                "faculty_assignment_repeat_reminder_days": 1,
                "grade_prediction_default_assumption": "IGNORE_MISSING",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            SystemSettingService.get("ENROLLMENT_OWNERSHIP_MODE", tenant_id=self.tenant.id),
            "FACULTY_ALLOWED",
        )
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.response_status, FacultyAssignment.ResponseStatus.PENDING)
        self.assertIsNotNone(self.assignment.response_due_at)

    def test_admin_can_set_class_master_list_override_for_selected_offering(self):
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse("admin_portal:configurable_features_settings"),
            {
                "enrollment_ownership_mode": "ADMIN_ONLY",
                "class_master_list_term": str(self.term.id),
                "class_master_list_offering": [str(self.offering.id), str(self.second_offering.id)],
                "class_master_list_override_mode": "FACULTY_ALLOWED",
                "login_lockout_max_attempts": 5,
                "login_lockout_window_minutes": 15,
                "login_lockout_duration_minutes": 15,
                "faculty_assignment_response_window_days": 3,
                "faculty_assignment_first_reminder_days": 1,
                "faculty_assignment_repeat_reminder_days": 1,
                "grade_prediction_default_assumption": "IGNORE_MISSING",
                f"campus_recipient_{self.campus.id}": "",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        override_map = EnrollmentService.get_enrollment_mode_overrides(self.tenant.id)
        self.assertEqual(override_map.get(str(self.offering.id)), EnrollmentService.FACULTY_ALLOWED)
        self.assertEqual(override_map.get(str(self.second_offering.id)), EnrollmentService.FACULTY_ALLOWED)
        self.assertEqual(
            EnrollmentService.get_enrollment_mode(self.tenant.id, offering_id=self.offering.id),
            EnrollmentService.FACULTY_ALLOWED,
        )
        self.assertEqual(
            EnrollmentService.get_enrollment_mode(self.tenant.id, offering_id=self.second_offering.id),
            EnrollmentService.FACULTY_ALLOWED,
        )
        self.assertContains(response, "Current selected rule:")

    def test_configurable_features_can_filter_class_override_targets_by_faculty(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(
            reverse("admin_portal:configurable_features_settings"),
            {
                "term_id": self.term.id,
                "faculty_user_id": self.faculty_user.id,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Faculty Viewer")
        self.assertContains(response, f"{self.course.title} ({self.course.code})")
        self.assertContains(response, '<div class="class-offering-picker"', html=False)
        self.assertContains(response, f"<strong>{self.faculty_user.full_name}</strong>", html=False)
        self.assertContains(response, 'id="class-master-list-ownership-card"', html=False)
        self.assertNotContains(response, f"{self.second_course.title} ({self.second_course.code}) ({self.faculty_user_two.full_name})")
        self.assertNotContains(
            response,
            f"{self.course.title} ({self.course.code}) | {self.section.name} ({self.section.code}) | "
            f"{self.term.name} - {self.academic_year.name} ({self.faculty_user.full_name})",
        )

    def test_assignment_dashboard_view_loads(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("admin_portal:faculty_assignment_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Faculty Assignment Dashboard")
        self.assertContains(response, "Campus Snapshot")

    def test_configurable_features_can_store_assignment_workflow_settings(self):
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse("admin_portal:configurable_features_settings"),
            {
                "correction_official_report_enabled": "",
                "correction_submission_approval_email_enabled": "",
                "correction_registrar_auto_email_enabled": "",
                "correction_registrar_default_recipients": "",
                "faculty_assignment_reminders_enabled": "on",
                "faculty_assignment_auto_expire_enabled": "on",
                "faculty_assignment_primary_default_enabled": "",
                "faculty_reminder_center_enabled": "",
                "faculty_reminder_email_enabled": "",
                "faculty_memo_center_enabled": "",
                "enrollment_ownership_mode": "ADMIN_ONLY",
                "login_lockout_enabled": "",
                "login_lockout_max_attempts": "5",
                "login_lockout_window_minutes": "15",
                "login_lockout_duration_minutes": "15",
                "faculty_assignment_response_window_days": "5",
                "faculty_assignment_first_reminder_days": "2",
                "faculty_assignment_repeat_reminder_days": "1",
                "grade_prediction_enabled": "",
                "grade_prediction_what_if_enabled": "",
                "grade_prediction_at_risk_enabled": "",
                "grade_prediction_show_best_case": "",
                "grade_prediction_show_worst_case": "",
                "grade_prediction_show_target_needed": "",
                "grade_prediction_default_assumption": "IGNORE_MISSING",
                f"campus_recipient_{self.campus.id}": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            FeatureSettingsService.is_faculty_assignment_reminders_enabled(tenant_id=self.tenant.id)
        )
        self.assertTrue(
            FeatureSettingsService.is_faculty_assignment_auto_expire_enabled(tenant_id=self.tenant.id)
        )
        self.assertEqual(
            FeatureSettingsService.get_faculty_assignment_response_window_days(tenant_id=self.tenant.id),
            5,
        )
        self.assertFalse(
            FeatureSettingsService.is_faculty_assignment_primary_default_enabled(tenant_id=self.tenant.id)
        )

    def test_faculty_assignment_create_respects_primary_default_setting(self):
        third_course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="A134-THIRD",
            title="Third Course",
        )
        third_section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="BSIT-1C",
            name="BSIT 1C",
        )
        third_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=self.academic_year,
            term=self.term,
            course=third_course,
            section=third_section,
        )
        SystemSettingService.set(
            FeatureSettingsService.FACULTY_ASSIGNMENT_PRIMARY_DEFAULT_ENABLED_KEY,
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
            is_active=True,
        )

        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse("admin_portal:faculty_assignment_create"),
            {
                "offering": third_offering.id,
                "faculty_user": self.faculty_user.id,
                "assignment_note": "Test assignment",
                "is_primary": "",
                "is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        created = FacultyAssignment.objects.get(offering=third_offering, faculty_user=self.faculty_user)
        self.assertFalse(created.is_primary)

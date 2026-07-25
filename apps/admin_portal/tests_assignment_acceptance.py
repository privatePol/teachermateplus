import re
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.academics.models import AcademicYear, Course, CourseOffering, FacultyAssignment, Section, Term
from apps.academics.services import AcademicGovernanceService, FacultyAssignmentWorkflowService
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.enrollment.models import Enrollment
from apps.enrollment.services import EnrollmentService
from apps.grading.models import (
    CourseTemplateAssignment,
    FacultyFinalClearanceReport,
    GradeSubmission,
    GradingPeriodLock,
    GradingTemplate,
    GradingTemplatePeriod,
    StudentFinalGrade,
    StudentPeriodGrade,
)
from apps.grading.reporting import FacultyFinalClearanceReportService
from apps.notifications.models import SubmissionNonComplianceNotice
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.students.models import Student
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
        self.faculty_activity_monitor_read, _ = Permission.objects.get_or_create(
            code="faculty_activity_monitor.read",
            defaults={"module": "faculty_activity_monitor", "action": "read"},
        )
        self.faculty_gradebook_monitor_read, _ = Permission.objects.get_or_create(
            code="faculty_gradebook_monitor.read",
            defaults={"module": "faculty_gradebook_monitor", "action": "read"},
        )
        faculty_final_clearance_read, _ = Permission.objects.get_or_create(
            code="faculty_final_clearance.read",
            defaults={"module": "faculty_final_clearance", "action": "read"},
        )
        self.faculty_final_clearance_read = faculty_final_clearance_read
        self.grade_prediction_monitor_read, _ = Permission.objects.get_or_create(
            code="grade_prediction_monitor.read",
            defaults={"module": "grade_prediction_monitor", "action": "read"},
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
        self.admin_role = admin_role

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

    def _grant_academic_monitor_access(self):
        for permission in [
            self.faculty_activity_monitor_read,
            self.faculty_gradebook_monitor_read,
            self.faculty_final_clearance_read,
            self.grade_prediction_monitor_read,
        ]:
            RolePermission.objects.get_or_create(role=self.admin_role, permission=permission)

    def _grant_final_clearance_access(self):
        RolePermission.objects.get_or_create(
            role=self.admin_role,
            permission=self.faculty_final_clearance_read,
        )

    def _create_scoped_faculty(
        self,
        *,
        username,
        email,
        first_name,
        last_name,
        middle_name="",
        campus=None,
        department=None,
        is_active=True,
    ):
        campus = campus or self.campus
        department = department or self.department
        faculty = User.objects.create_user(
            username=username,
            email=email,
            password="testpass123",
            first_name=first_name,
            middle_name=middle_name,
            last_name=last_name,
            default_tenant=self.tenant,
            default_campus=campus,
            default_department=department,
            is_active=is_active,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        UserRole.objects.create(
            user=faculty,
            role=Role.objects.get(code="FACULTY"),
            tenant=self.tenant,
            campus=campus,
            department=department,
        )
        return faculty

    def test_faculty_dropdown_uses_sorted_page_specific_identity_labels_and_scope(self):
        self.faculty_user.first_name = "Apolo"
        self.faculty_user.middle_name = "Gabriel"
        self.faculty_user.last_name = "Bejer"
        self.faculty_user.email = "apolo.bejer@ncba.edu.ph"
        self.faculty_user.save(update_fields=["first_name", "middle_name", "last_name", "email", "updated_at"])
        self.faculty_user_two.first_name = "Cyrille Anne"
        self.faculty_user_two.middle_name = ""
        self.faculty_user_two.last_name = "Nery"
        self.faculty_user_two.email = "nery.cyrilleanne@ncba.edu.ph"
        self.faculty_user_two.save(update_fields=["first_name", "middle_name", "last_name", "email", "updated_at"])
        same_name_faculty_email_b = self._create_scoped_faculty(
            username="faculty_reyes_email_b",
            email="maria.lourdes.b@example.com",
            first_name="Maria",
            middle_name="Lourdes",
            last_name="Reyes",
        )
        same_name_faculty_email_a = self._create_scoped_faculty(
            username="faculty_reyes_email_a",
            email="maria.lourdes.a@example.com",
            first_name="Maria",
            middle_name="Lourdes",
            last_name="Reyes",
        )
        no_email_faculty = self._create_scoped_faculty(
            username="faculty_santos",
            email="",
            first_name="Pedro",
            last_name="Santos",
        )
        other_campus = Campus.objects.create(tenant=self.tenant, code="NCBA-OTHER", name="Other Campus")
        other_department = Department.objects.create(
            tenant=self.tenant,
            campus=other_campus,
            code="OTHER_IS",
            name="Other Information Systems",
        )
        out_of_scope_faculty = self._create_scoped_faculty(
            username="faculty_other_campus",
            email="other.campus@example.com",
            first_name="Other",
            last_name="Campus",
            campus=other_campus,
            department=other_department,
        )
        inactive_faculty = self._create_scoped_faculty(
            username="faculty_inactive",
            email="inactive@example.com",
            first_name="Inactive",
            last_name="Faculty",
            is_active=False,
        )
        self.client.force_login(self.admin_user)

        response = self.client.get(
            reverse("admin_portal:faculty_assignment_list"),
            {"faculty_user_id": self.faculty_user.id},
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        faculty_select_match = re.search(
            r'<select class="form-select" id="faculty-select-input" name="faculty_user_id">(.*?)</select>',
            content,
            re.DOTALL,
        )
        self.assertIsNotNone(faculty_select_match)
        faculty_select_html = faculty_select_match.group(1)
        expected_labels = [
            "Bejer, Apolo G. (apolo.bejer@ncba.edu.ph)",
            "Nery, Cyrille Anne (nery.cyrilleanne@ncba.edu.ph)",
            "Reyes, Maria L. (maria.lourdes.a@example.com)",
            "Reyes, Maria L. (maria.lourdes.b@example.com)",
            "Santos, Pedro",
        ]
        candidate_labels = {
            self.faculty_user.id: expected_labels[0],
            self.faculty_user_two.id: expected_labels[1],
            same_name_faculty_email_a.id: expected_labels[2],
            same_name_faculty_email_b.id: expected_labels[3],
            no_email_faculty.id: expected_labels[4],
        }

        def option_text(faculty_id):
            match = re.search(
                rf'<option value="{faculty_id}"[^>]*>\s*(.*?)\s*</option>',
                faculty_select_html,
                re.DOTALL,
            )
            self.assertIsNotNone(match)
            return match.group(1).strip()

        for faculty_id, label in candidate_labels.items():
            self.assertEqual(option_text(faculty_id), label)
        self.assertLess(same_name_faculty_email_b.id, same_name_faculty_email_a.id)
        self.assertLess(faculty_select_html.index(expected_labels[0]), faculty_select_html.index(expected_labels[1]))
        self.assertLess(faculty_select_html.index(expected_labels[1]), faculty_select_html.index(expected_labels[2]))
        self.assertLess(faculty_select_html.index(expected_labels[2]), faculty_select_html.index(expected_labels[3]))
        self.assertLess(faculty_select_html.index(expected_labels[3]), faculty_select_html.index(expected_labels[4]))
        self.assertNotIn("—", option_text(self.faculty_user.id))
        self.assertNotIn("()", option_text(no_email_faculty.id))
        self.assertNotIn(no_email_faculty.username, option_text(no_email_faculty.id))
        self.assertIn(f'<option value="{self.faculty_user.id}" selected>', faculty_select_html)
        self.assertEqual(response.context["selected_faculty"].id, self.faculty_user.id)
        candidate_ids = {faculty.id for faculty in response.context["faculty_candidates"]}
        self.assertSetEqual(
            {
                self.faculty_user.id,
                self.faculty_user_two.id,
                same_name_faculty_email_a.id,
                same_name_faculty_email_b.id,
                no_email_faculty.id,
            }
            .difference(candidate_ids),
            set(),
        )
        self.assertNotIn(out_of_scope_faculty.id, candidate_ids)
        self.assertNotIn(inactive_faculty.id, candidate_ids)

    def test_faculty_dropdown_query_count_is_bounded_when_more_faculty_are_added(self):
        self.client.force_login(self.admin_user)
        url = reverse("admin_portal:faculty_assignment_list")
        self.client.get(url)
        with CaptureQueriesContext(connection) as baseline_queries:
            baseline_response = self.client.get(url)
        self.assertEqual(baseline_response.status_code, 200)

        for index in range(5):
            self._create_scoped_faculty(
                username=f"faculty_dropdown_{index}",
                email=f"faculty_dropdown_{index}@example.com",
                first_name="Dropdown",
                middle_name="Faculty",
                last_name=f"{index:02d}",
            )
        with CaptureQueriesContext(connection) as expanded_queries:
            expanded_response = self.client.get(url)

        self.assertEqual(expanded_response.status_code, 200)
        self.assertEqual(len(expanded_queries), len(baseline_queries))

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

    def test_faculty_assignment_page_defaults_to_active_academic_scope(self):
        old_academic_year = AcademicYear.objects.create(
            tenant=self.tenant,
            code="2024-2025",
            name="AY 2024-2025",
            start_date=date(2024, 6, 1),
            end_date=date(2025, 5, 31),
        )
        old_term = Term.objects.create(
            tenant=self.tenant,
            academic_year=old_academic_year,
            code="2ND",
            name="Second Term",
            sequence_no=2,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 5, 31),
        )
        old_course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="OLD327-IPM2",
            title="Old IS Project Management 2",
        )
        old_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=old_academic_year,
            term=old_term,
            course=old_course,
            section=self.section,
        )
        old_assignment = FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=old_offering,
            faculty_user=self.faculty_user,
            is_primary=True,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
            accepted_by=self.faculty_user,
            responded_at=timezone.now(),
        )
        active_unassigned_course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="ACTIVE134",
            title="Active Scope Unassigned Course",
        )
        active_unassigned_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=self.academic_year,
            term=self.term,
            course=active_unassigned_course,
            section=self.second_section,
        )
        old_unassigned_course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="OLD134",
            title="Old Scope Unassigned Course",
        )
        old_unassigned_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=old_academic_year,
            term=old_term,
            course=old_unassigned_course,
            section=self.second_section,
        )
        AcademicGovernanceService.set_active_scope(
            tenant_id=self.tenant.id,
            academic_year=self.academic_year,
            term=self.term,
        )
        self.client.force_login(self.admin_user)

        response = self.client.get(
            reverse("admin_portal:faculty_assignment_list"),
            {"faculty_user_id": self.faculty_user.id, "assign": "1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_academic_year_id"], self.academic_year.id)
        self.assertEqual(response.context["selected_term_id"], self.term.id)
        self.assertEqual(response.context["assigned_count"], 1)
        self.assertEqual(response.context["accepted_count"], 0)
        self.assertEqual(response.context["pending_acceptance_count"], 1)
        primary_card = next(
            card for card in response.context["assignment_metric_cards"] if card["label"] == "Primary Load"
        )
        self.assertEqual(primary_card["value"], 1)
        assigned_ids = {assignment.id for assignment in response.context["selected_faculty_assignments"]}
        self.assertIn(self.assignment.id, assigned_ids)
        self.assertNotIn(old_assignment.id, assigned_ids)
        assignable_ids = {offering.id for offering in response.context["assignable_offerings"]}
        self.assertIn(active_unassigned_offering.id, assignable_ids)
        self.assertNotIn(old_unassigned_offering.id, assignable_ids)
        self.assertContains(response, "IT Application Tools")
        self.assertContains(response, "Active Scope Unassigned Course")
        self.assertNotContains(response, "Old IS Project Management 2")
        self.assertNotContains(response, "Old Scope Unassigned Course")

    def test_faculty_assignment_page_hides_exact_old_production_assignment_when_no_active_load(self):
        self.assignment.is_active = False
        self.assignment.save(update_fields=["is_active", "updated_at"])
        old_academic_year = AcademicYear.objects.create(
            tenant=self.tenant,
            code="AY2526",
            name="2025-2026",
            start_date=date(2025, 6, 1),
            end_date=date(2026, 5, 31),
        )
        old_term = Term.objects.create(
            tenant=self.tenant,
            academic_year=old_academic_year,
            code="2ND-SEM",
            name="2nd-Semester (2025-2026)",
            sequence_no=2,
            start_date=date(2025, 11, 1),
            end_date=date(2026, 3, 31),
        )
        active_academic_year = AcademicYear.objects.create(
            tenant=self.tenant,
            code="AY2627",
            name="2026-2027",
            start_date=date(2026, 6, 1),
            end_date=date(2027, 5, 31),
        )
        active_term = Term.objects.create(
            tenant=self.tenant,
            academic_year=active_academic_year,
            code="1ST-SEM",
            name="1ST-SEM 2026-2027",
            sequence_no=1,
            start_date=date(2026, 6, 1),
            end_date=date(2026, 10, 31),
        )
        old_course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="IS327-IPM2",
            title="IS Project Management 2",
        )
        old_section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="BSIS-TEST",
            name="BSIS TEST SECTION",
        )
        old_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=old_academic_year,
            term=old_term,
            course=old_course,
            section=old_section,
        )
        old_assignment = FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=old_offering,
            faculty_user=self.faculty_user,
            is_primary=True,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
            accepted_by=self.faculty_user,
            responded_at=timezone.now(),
        )
        AcademicGovernanceService.set_active_scope(
            tenant_id=self.tenant.id,
            academic_year=active_academic_year,
            term=active_term,
        )
        self.client.force_login(self.admin_user)

        response = self.client.get(
            reverse("admin_portal:faculty_assignment_list"),
            {"faculty_user_id": self.faculty_user.id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_academic_year_id"], active_academic_year.id)
        self.assertEqual(response.context["selected_term_id"], active_term.id)
        self.assertEqual(response.context["assigned_count"], 0)
        self.assertEqual(response.context["accepted_count"], 0)
        self.assertEqual(response.context["pending_acceptance_count"], 0)
        self.assertEqual(response.context["due_soon_count"], 0)
        self.assertEqual(response.context["expired_count"], 0)
        self.assertEqual(response.context["clarification_count"], 0)
        primary_card = next(
            card for card in response.context["assignment_metric_cards"] if card["label"] == "Primary Load"
        )
        self.assertEqual(primary_card["value"], 0)
        self.assertFalse(response.context["selected_faculty_assignments"].exists())
        self.assertTrue(FacultyAssignment.objects.filter(id=old_assignment.id, is_active=True).exists())
        self.assertNotContains(response, "IS Project Management 2")
        self.assertNotContains(response, "IS327-IPM2")
        self.assertNotContains(response, "BSIS TEST SECTION")

    def test_faculty_assignment_page_shows_active_scope_assignment_when_active_scope_is_newer(self):
        old_academic_year = AcademicYear.objects.create(
            tenant=self.tenant,
            code="AY2526",
            name="2025-2026",
            start_date=date(2025, 6, 1),
            end_date=date(2026, 5, 31),
        )
        old_term = Term.objects.create(
            tenant=self.tenant,
            academic_year=old_academic_year,
            code="2ND-SEM",
            name="2nd-Semester (2025-2026)",
            sequence_no=2,
            start_date=date(2025, 11, 1),
            end_date=date(2026, 3, 31),
        )
        active_academic_year = AcademicYear.objects.create(
            tenant=self.tenant,
            code="AY2627",
            name="2026-2027",
            start_date=date(2026, 6, 1),
            end_date=date(2027, 5, 31),
        )
        active_term = Term.objects.create(
            tenant=self.tenant,
            academic_year=active_academic_year,
            code="1ST-SEM",
            name="1ST-SEM 2026-2027",
            sequence_no=1,
            start_date=date(2026, 6, 1),
            end_date=date(2026, 10, 31),
        )
        old_course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="IS327-IPM2",
            title="IS Project Management 2",
        )
        old_section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="BSIS-TEST",
            name="BSIS TEST SECTION",
        )
        old_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=old_academic_year,
            term=old_term,
            course=old_course,
            section=old_section,
        )
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=old_offering,
            faculty_user=self.faculty_user,
            is_primary=True,
        )
        active_course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="IS401-ACTIVE",
            title="Active Scope Systems Course",
        )
        active_section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="BSIS-ACTIVE",
            name="BSIS ACTIVE SECTION",
        )
        active_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=active_academic_year,
            term=active_term,
            course=active_course,
            section=active_section,
        )
        active_assignment = FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=active_offering,
            faculty_user=self.faculty_user,
            is_primary=True,
        )
        AcademicGovernanceService.set_active_scope(
            tenant_id=self.tenant.id,
            academic_year=active_academic_year,
            term=active_term,
        )
        self.client.force_login(self.admin_user)

        response = self.client.get(
            reverse("admin_portal:faculty_assignment_list"),
            {"faculty_user_id": self.faculty_user.id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["assigned_count"], 1)
        assignment_ids = {assignment.id for assignment in response.context["selected_faculty_assignments"]}
        self.assertIn(active_assignment.id, assignment_ids)
        self.assertContains(response, "Active Scope Systems Course")
        self.assertContains(response, "IS401-ACTIVE")
        self.assertContains(response, "BSIS ACTIVE SECTION")
        self.assertNotContains(response, "IS Project Management 2")
        self.assertNotContains(response, "IS327-IPM2")
        self.assertNotContains(response, "BSIS TEST SECTION")

    def test_faculty_assignment_assign_post_rejects_offerings_outside_active_scope(self):
        old_academic_year = AcademicYear.objects.create(
            tenant=self.tenant,
            code="2024-2025",
            name="AY 2024-2025",
            start_date=date(2024, 6, 1),
            end_date=date(2025, 5, 31),
        )
        old_term = Term.objects.create(
            tenant=self.tenant,
            academic_year=old_academic_year,
            code="2ND",
            name="Second Term",
            sequence_no=2,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 5, 31),
        )
        old_course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="OLD-POST",
            title="Old Direct Post Course",
        )
        old_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=old_academic_year,
            term=old_term,
            course=old_course,
            section=self.second_section,
        )
        AcademicGovernanceService.set_active_scope(
            tenant_id=self.tenant.id,
            academic_year=self.academic_year,
            term=self.term,
        )
        self.client.force_login(self.admin_user)

        response = self.client.post(
            reverse("admin_portal:faculty_assignment_assign"),
            {
                "faculty_user_id": self.faculty_user.id,
                "offering_ids": [old_offering.id],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No offerings were assigned. They may already be assigned or out of scope.")
        self.assertFalse(
            FacultyAssignment.objects.filter(
                offering=old_offering,
                faculty_user=self.faculty_user,
                is_active=True,
            ).exists()
        )

    def test_campus_admin_assignment_page_hides_academic_monitor_actions(self):
        self.client.force_login(self.admin_user)

        response = self.client.get(
            reverse("admin_portal:faculty_assignment_list"),
            {"faculty_user_id": self.faculty_user.id, "assign": "1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ASSIGN COURSE OFFERINGS")
        self.assertNotContains(response, "OPEN ACTIVITY MONITOR")
        self.assertNotContains(response, "OPEN FINAL CLEARANCE")
        self.assertNotContains(response, "OPEN GRADE BOOK")
        self.assertNotContains(response, "OPEN PREDICTION")

    def test_campus_admin_assignment_permission_does_not_open_academic_monitors(self):
        self.client.force_login(self.admin_user)

        blocked_urls = [
            reverse("admin_portal:faculty_activity_monitor"),
            reverse("admin_portal:faculty_gradebook_monitor"),
            reverse("admin_portal:grade_prediction_monitor"),
            reverse("admin_portal:faculty_final_clearance"),
        ]

        for url in blocked_urls:
            with self.subTest(url=url):
                response = self.client.get(url, {"faculty_user_id": self.faculty_user.id})
                self.assertEqual(response.status_code, 403)

    def test_assignment_offering_filter_requires_explicit_submit(self):
        self.client.force_login(self.admin_user)

        response = self.client.get(
            reverse("admin_portal:faculty_assignment_list"),
            {"faculty_user_id": self.faculty_user.id, "assign": "1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="assignment-offering-panels"')
        self.assertContains(response, 'id="offering-search-form" method="get"')
        self.assertContains(response, '<h4 class="mb-0">Unassigned Course Offerings</h4>', html=True)
        self.assertContains(response, '<h4 class="mb-0">Assigned Offerings</h4>', html=True)
        self.assertContains(response, "assignment-card-header-unassigned")
        self.assertContains(response, "assignment-card-header-assigned")
        self.assertContains(response, "Primary and Secondary Load Tags")
        self.assertContains(response, "marks the lead faculty assignment")
        self.assertContains(response, "marks supporting or shared-load faculty")
        self.assertContains(response, "Load Role")
        self.assertContains(response, "fetch(targetUrl")
        self.assertContains(response, "panels.innerHTML = nextPanels.innerHTML")
        self.assertContains(response, 'name="offering_q"')
        self.assertContains(response, ">Filter</button>")
        self.assertContains(response, "Set the course or section filter, then click Filter or press Enter.")
        self.assertNotContains(response, "Filtering runs automatically while typing.")
        self.assertNotContains(response, 'searchInput.addEventListener("input"')

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
                "enrollment_student_mode": "AUTO_CREATE",
                "faculty_drp_allowed_through_period": "PREFINAL",
                "login_lockout_max_attempts": 5,
                "login_lockout_window_minutes": 15,
                "login_lockout_duration_minutes": 15,
                "faculty_assignment_response_window_days": 3,
                "faculty_assignment_first_reminder_days": 1,
                "faculty_assignment_repeat_reminder_days": 1,
                "exit_pulse_enabled": "on",
                "submission_non_compliance_notice_interval_days": 1,
                "grade_deadline_enforcement_policy": "AUTO_CLOSE_REQUIRES_REOPEN",
                "grade_prediction_default_assumption": "IGNORE_MISSING",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            SystemSettingService.get("ENROLLMENT_OWNERSHIP_MODE", tenant_id=self.tenant.id),
            "FACULTY_ALLOWED",
        )
        self.assertEqual(
            SystemSettingService.get("ENROLLMENT_STUDENT_MODE", tenant_id=self.tenant.id),
            "AUTO_CREATE",
        )
        self.assertEqual(
            SystemSettingService.get("FACULTY_DRP_ALLOWED_THROUGH_PERIOD", tenant_id=self.tenant.id),
            "PREFINAL",
        )
        self.assertTrue(FeatureSettingsService.is_exit_pulse_enabled(tenant_id=self.tenant.id))
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
                "submission_non_compliance_notice_interval_days": 1,
                "grade_deadline_enforcement_policy": "AUTO_CLOSE_REQUIRES_REOPEN",
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

    def test_configurable_features_shows_single_device_login_setting(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("admin_portal:configurable_features_settings"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Allow only one active login session per user")
        self.assertContains(response, "a new login signs out the same user from any other browser or device")

    def test_configurable_features_renders_standard_cards_for_targeted_sections(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("admin_portal:configurable_features_settings"))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        expected_cards = (
            (
                "feature-card-academic-interventions",
                "Student Academic Intervention Tracking",
                "Allow authorized faculty to record academic-intervention decisions",
                "student_academic_intervention_tracking_enabled",
            ),
            (
                "feature-card-grade-prediction",
                "Grade Prediction",
                "Enable unofficial prediction, what-if simulation, at-risk flags",
                "grade_prediction_enabled",
            ),
            (
                "feature-card-campus-recipients",
                "Campus / Branch Recipient Emails",
                "Maintain branch-specific registrar recipient lists",
                f"campus_recipient_{self.campus.id}",
            ),
            (
                "feature-card-grade-release",
                "Official Grade Release to Faculty",
                "Control when official periodic grades and final grades",
                "faculty_official_period_grades_after_deadline",
            ),
            (
                "feature-card-help-guide",
                "Role-Based Help Guide",
                "Use the revised practical guide or restore the previous guide pages",
                "role_based_help_guide_enabled",
            ),
        )
        for card_id, title, description, field_name in expected_cards:
            target = f'data-bs-target="#{card_id}"'
            target_index = content.index(target)
            self.assertIn("card-header settings-card-toggle", content[max(0, target_index - 180):target_index])
            self.assertIn(f'<span class="settings-card-summary-title">{title}</span>', content)
            self.assertIn(description, content)
            self.assertIn(f'name="{field_name}"', content)

        for palette_position in range(1, 5):
            self.assertContains(
                response,
                f".settings-card:nth-of-type(19n + {palette_position}) .card-header",
            )
        self.assertContains(response, "Enable Exit Pulse")
        self.assertContains(response, "Enable Orientation Feedback Surveys")
        self.assertContains(response, "Submission Readiness Email Alerts", count=2)
        self.assertContains(
            response,
            "Email scoped Area Chairs, College Deans, and CAOs about assignments below the configured readiness threshold",
        )

    def test_assignment_dashboard_view_loads(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("admin_portal:faculty_assignment_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Faculty Assignment Dashboard")
        self.assertContains(response, "Campus Snapshot")
        self.assertContains(response, "logos/teachermate_logo_text_official.png")
        self.assertNotContains(response, "logos/teachermateplus_logo.png")
        self.assertContains(response, "linear-gradient(180deg, #214f25 0%, #39742d 32%, #4d8c33 68%, #5b9a37 100%)")
        self.assertContains(response, ".admin-scope-form .form-select")
        self.assertContains(response, "flex: 0 0 13.5rem;")

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
                "orientation_feedback_enabled": "on",
                "enrollment_ownership_mode": "ADMIN_ONLY",
                "grade_deadline_enforcement_policy": "AUTO_CLOSE_REQUIRES_REOPEN",
                "login_lockout_enabled": "",
                "login_lockout_max_attempts": "5",
                "login_lockout_window_minutes": "15",
                "login_lockout_duration_minutes": "15",
                "single_device_session_enforcement_enabled": "",
                "session_timeout_minutes": "45",
                "faculty_assignment_response_window_days": "5",
                "faculty_assignment_first_reminder_days": "2",
                "faculty_assignment_repeat_reminder_days": "1",
                "submission_non_compliance_notice_interval_days": "1",
                "submission_non_compliance_first_notice_after_days": "2",
                "submission_non_compliance_level_interval_days": "2",
                "submission_non_compliance_max_notice_count": "3",
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

        self.assertEqual(response.status_code, 302, response.context["form"].errors if response.context else "")
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
        self.assertEqual(
            FeatureSettingsService.get_session_timeout_minutes(tenant_id=self.tenant.id),
            45,
        )
        self.assertFalse(
            FeatureSettingsService.is_single_device_session_enforcement_enabled(tenant_id=self.tenant.id)
        )
        self.assertTrue(
            FeatureSettingsService.is_orientation_feedback_enabled(tenant_id=self.tenant.id)
        )
        self.assertEqual(
            FeatureSettingsService.get_submission_non_compliance_first_notice_after_days(tenant_id=self.tenant.id),
            2,
        )
        self.assertEqual(
            FeatureSettingsService.get_submission_non_compliance_level_interval_days(tenant_id=self.tenant.id),
            2,
        )
        self.assertEqual(
            FeatureSettingsService.get_submission_non_compliance_max_notice_count(tenant_id=self.tenant.id),
            3,
        )

    def test_configurable_features_rejects_invalid_non_compliance_notice_timing(self):
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse("admin_portal:configurable_features_settings"),
            {
                "enrollment_ownership_mode": "ADMIN_ONLY",
                "grade_deadline_enforcement_policy": "AUTO_CLOSE_REQUIRES_REOPEN",
                "login_lockout_max_attempts": "5",
                "login_lockout_window_minutes": "15",
                "login_lockout_duration_minutes": "15",
                "faculty_assignment_response_window_days": "5",
                "faculty_assignment_first_reminder_days": "2",
                "faculty_assignment_repeat_reminder_days": "1",
                "submission_non_compliance_notice_interval_days": "1",
                "submission_non_compliance_first_notice_after_days": "0",
                "submission_non_compliance_level_interval_days": "0",
                "submission_non_compliance_max_notice_count": "4",
                "grade_prediction_default_assumption": "IGNORE_MISSING",
                f"campus_recipient_{self.campus.id}": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["form"],
            "submission_non_compliance_first_notice_after_days",
            "Ensure this value is greater than or equal to 1.",
        )
        self.assertFormError(
            response.context["form"],
            "submission_non_compliance_level_interval_days",
            "Ensure this value is greater than or equal to 1.",
        )
        self.assertFormError(
            response.context["form"],
            "submission_non_compliance_max_notice_count",
            "Ensure this value is less than or equal to 3.",
        )

    def test_admin_can_enable_official_grade_release_to_faculty(self):
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse("admin_portal:configurable_features_settings"),
            {
                "enrollment_ownership_mode": "ADMIN_ONLY",
                "login_lockout_max_attempts": 5,
                "login_lockout_window_minutes": 15,
                "login_lockout_duration_minutes": 15,
                "faculty_assignment_response_window_days": 3,
                "faculty_assignment_first_reminder_days": 1,
                "faculty_assignment_repeat_reminder_days": 1,
                "submission_non_compliance_notice_interval_days": 1,
                "grade_deadline_enforcement_policy": "AUTO_CLOSE_REQUIRES_REOPEN",
                "grade_prediction_default_assumption": "IGNORE_MISSING",
                "faculty_official_period_grades_after_deadline": "on",
                "faculty_official_period_grades_after_submission": "on",
                "faculty_official_final_grades_after_deadline": "on",
                f"campus_recipient_{self.campus.id}": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            FeatureSettingsService.show_faculty_official_period_grades_after_deadline(tenant_id=self.tenant.id)
        )
        self.assertTrue(
            FeatureSettingsService.show_faculty_official_period_grades_after_submission(tenant_id=self.tenant.id)
        )
        self.assertTrue(
            FeatureSettingsService.show_faculty_official_final_grades_after_deadline(tenant_id=self.tenant.id)
        )

    def _build_final_clearance_fixture(self):
        accepted_at = timezone.now()
        for assignment, faculty_user in ((self.assignment, self.faculty_user),):
            assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
            assignment.accepted_at = accepted_at
            assignment.accepted_by = faculty_user
            assignment.responded_at = accepted_at
            assignment.save(
                update_fields=[
                    "response_status",
                    "accepted_at",
                    "accepted_by",
                    "responded_at",
                    "updated_at",
                ]
            )
        template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="GENED-TEMPLATE",
            name="General Education Template",
            approval_status=GradingTemplate.ApprovalStatus.APPROVED,
            is_published=True,
            published_at=timezone.now(),
            published_by=self.admin_user,
        )
        prelim_period = GradingTemplatePeriod.objects.create(
            template=template,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
            weight_percentage=Decimal("50.00"),
        )
        final_period = GradingTemplatePeriod.objects.create(
            template=template,
            code="FINAL",
            name="Final",
            sequence_no=2,
            weight_percentage=Decimal("50.00"),
        )
        CourseTemplateAssignment.objects.create(
            course=self.course,
            grading_template=template,
            effective_from_term=self.term,
        )
        CourseTemplateAssignment.objects.create(
            course=self.second_course,
            grading_template=template,
            effective_from_term=self.term,
        )
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.second_offering,
            faculty_user=self.faculty_user,
            is_primary=False,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=accepted_at,
            accepted_by=self.faculty_user,
            responded_at=accepted_at,
        )

        active_student_complete = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="2025-0001",
            last_name="Adams",
            first_name="Alice",
        )
        dropped_student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="2025-0002",
            last_name="Brown",
            first_name="Benedict",
        )
        active_student_incomplete = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="2025-0003",
            last_name="Cruz",
            first_name="Carla",
        )
        withdrawn_student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="2025-0004",
            last_name="Diaz",
            first_name="Daniel",
        )

        Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            student=active_student_complete,
            course_offering=self.offering,
            enrollment_status=Enrollment.Status.ACTIVE,
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            student=dropped_student,
            course_offering=self.offering,
            enrollment_status=Enrollment.Status.DRP,
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            student=active_student_incomplete,
            course_offering=self.second_offering,
            enrollment_status=Enrollment.Status.ACTIVE,
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            student=withdrawn_student,
            course_offering=self.second_offering,
            enrollment_status=Enrollment.Status.W,
        )

        GradeSubmission.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=prelim_period,
            status=GradeSubmission.Status.SUBMITTED,
            submitted_by_user=self.faculty_user,
            submitted_at=timezone.now(),
        )
        GradeSubmission.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=final_period,
            status=GradeSubmission.Status.SUBMITTED,
            submitted_by_user=self.faculty_user,
            submitted_at=timezone.now(),
        )
        GradeSubmission.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.second_offering,
            template_period=prelim_period,
            status=GradeSubmission.Status.SUBMITTED,
            submitted_by_user=self.faculty_user,
            submitted_at=timezone.now(),
        )

        StudentPeriodGrade.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=prelim_period,
            student=active_student_complete,
            period_grade=Decimal("89.50"),
            computed_by_user=self.faculty_user,
            is_finalized=True,
        )
        StudentPeriodGrade.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=final_period,
            student=active_student_complete,
            period_grade=Decimal("91.00"),
            computed_by_user=self.faculty_user,
            is_finalized=True,
        )
        StudentPeriodGrade.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.second_offering,
            template_period=prelim_period,
            student=active_student_incomplete,
            period_grade=Decimal("86.00"),
            computed_by_user=self.faculty_user,
            is_finalized=True,
        )

        StudentFinalGrade.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            student=active_student_complete,
            final_grade=Decimal("90.25"),
            computed_by_user=self.faculty_user,
            is_submitted=True,
        )

    def test_faculty_final_clearance_preview_shows_complete_and_incomplete_courses(self):
        self._build_final_clearance_fixture()
        self._grant_final_clearance_access()
        self.client.force_login(self.admin_user)

        response = self.client.get(
            reverse("admin_portal:faculty_final_clearance"),
            {
                "term_id": self.term.id,
                "faculty_user_id": self.faculty_user.id,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Faculty Final Clearance")
        self.assertContains(response, self.course.code)
        self.assertContains(response, self.second_course.code)
        preview = response.context["preview"]
        self.assertEqual(preview["total_assigned_courses"], 2)
        self.assertEqual(preview["complete_courses"], 1)
        self.assertEqual(preview["incomplete_courses"], 1)
        self.assertEqual(preview["clearance_status"], "NOT_CLEARED")
        preview_rows = {row["course_code"]: row for row in preview["rows"]}
        self.assertEqual(preview_rows[self.course.code]["encoding_status"], "COMPLETE")
        self.assertEqual(preview_rows[self.course.code]["eligible_student_count"], 1)
        self.assertEqual(preview_rows[self.second_course.code]["encoding_status"], "INCOMPLETE")
        self.assertEqual(preview_rows[self.second_course.code]["eligible_student_count"], 1)

    def test_faculty_final_clearance_admin_post_is_preview_only(self):
        self._build_final_clearance_fixture()
        self._grant_final_clearance_access()
        self.client.force_login(self.admin_user)

        response = self.client.post(
            reverse("admin_portal:faculty_final_clearance"),
            {
                "term_id": self.term.id,
                "faculty_user_id": self.faculty_user.id,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Official Final Clearance generation is available only in the Faculty Portal")
        self.assertFalse(
            FacultyFinalClearanceReport.objects.filter(
                faculty_user=self.faculty_user,
                term=self.term,
            ).exists()
        )

    def test_faculty_final_clearance_verify_view_displays_generated_snapshot(self):
        self._build_final_clearance_fixture()
        self._grant_final_clearance_access()
        report_obj = FacultyFinalClearanceReportService.generate_report_record(
            faculty_user=self.faculty_user,
            term=self.term,
            campus=self.campus,
            generated_by_user=self.faculty_user,
        )
        self.client.force_login(self.admin_user)

        response = self.client.get(
            reverse("admin_portal:faculty_final_clearance_verify", args=[report_obj.id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, report_obj.reference_no)
        self.assertContains(response, report_obj.verification_code)
        self.assertContains(response, self.course.code)
        self.assertContains(response, self.second_course.code)

    def test_faculty_final_clearance_lookup_finds_report_by_reference_and_code(self):
        self._build_final_clearance_fixture()
        self._grant_final_clearance_access()
        report_obj = FacultyFinalClearanceReportService.generate_report_record(
            faculty_user=self.faculty_user,
            term=self.term,
            campus=self.campus,
            generated_by_user=self.faculty_user,
        )
        self.client.force_login(self.admin_user)

        response = self.client.get(
            reverse("admin_portal:faculty_final_clearance"),
            {
                "lookup_reference_no": report_obj.reference_no,
                "lookup_verification_code": report_obj.verification_code,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Official NCBA report found")
        self.assertContains(response, report_obj.reference_no)
        self.assertContains(response, "Open Verified Report")

    def test_faculty_final_clearance_lookup_rejects_invalid_code(self):
        self._build_final_clearance_fixture()
        self._grant_final_clearance_access()
        report_obj = FacultyFinalClearanceReportService.generate_report_record(
            faculty_user=self.faculty_user,
            term=self.term,
            campus=self.campus,
            generated_by_user=self.faculty_user,
        )
        self.client.force_login(self.admin_user)

        response = self.client.get(
            reverse("admin_portal:faculty_final_clearance"),
            {
                "lookup_reference_no": report_obj.reference_no,
                "lookup_verification_code": "INVALIDCODE000000",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No official NCBA faculty final clearance report matched")

    def test_faculty_final_clearance_marks_zero_active_students_as_incomplete(self):
        self._grant_final_clearance_access()
        accepted_at = timezone.now()
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.accepted_at = accepted_at
        self.assignment.accepted_by = self.faculty_user
        self.assignment.responded_at = accepted_at
        self.assignment.save(
            update_fields=[
                "response_status",
                "accepted_at",
                "accepted_by",
                "responded_at",
                "updated_at",
            ]
        )
        template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="GENED-TEMPLATE-ZERO",
            name="General Education Template Zero",
            approval_status=GradingTemplate.ApprovalStatus.APPROVED,
            is_published=True,
            published_at=timezone.now(),
            published_by=self.admin_user,
        )
        final_period = GradingTemplatePeriod.objects.create(
            template=template,
            code="FINAL",
            name="Final",
            sequence_no=1,
            weight_percentage=Decimal("100.00"),
        )
        CourseTemplateAssignment.objects.create(
            course=self.course,
            grading_template=template,
            effective_from_term=self.term,
        )
        withdrawn_student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="2025-W-001",
            last_name="Withdrawn",
            first_name="Only",
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            student=withdrawn_student,
            course_offering=self.offering,
            enrollment_status=Enrollment.Status.W,
        )
        GradeSubmission.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=final_period,
            status=GradeSubmission.Status.SUBMITTED,
            submitted_by_user=self.faculty_user,
            submitted_at=timezone.now(),
        )
        self.client.force_login(self.admin_user)

        response = self.client.get(
            reverse("admin_portal:faculty_final_clearance"),
            {
                "term_id": self.term.id,
                "faculty_user_id": self.faculty_user.id,
            },
        )

        self.assertEqual(response.status_code, 200)
        preview = response.context["preview"]
        self.assertEqual(preview["clearance_status"], "NOT_CLEARED")
        self.assertEqual(preview["rows"][0]["encoding_status"], "INCOMPLETE")
        self.assertContains(response, "No ACTIVE students are currently eligible for final-clearance completion.")

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


class AdminNonComplianceMonitorTests(TestCase):
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
            code="2ND",
            name="Second Term",
            sequence_no=2,
            start_date=date(2025, 11, 1),
            end_date=date(2026, 3, 31),
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
        self.template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="TMP-GRACE",
            name="Grace Template",
            is_published=True,
            approval_status=GradingTemplate.ApprovalStatus.APPROVED,
        )
        self.period = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
            is_active=True,
        )
        CourseTemplateAssignment.objects.create(
            course=self.course,
            grading_template=self.template,
            effective_from_term=self.term,
            is_active=True,
        )
        self.admin_user = User.objects.create_user(
            username="late_admin",
            email="late_admin@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        self.faculty_user = User.objects.create_user(
            username="late_faculty",
            email="late_faculty@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            faculty_user=self.faculty_user,
            is_primary=True,
            accepted_at=timezone.now(),
            accepted_by=self.faculty_user,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
        )
        faculty_role = Role.objects.create(code="FACULTY", name="Faculty", is_active=True)
        UserRole.objects.create(
            user=self.faculty_user,
            role=faculty_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            is_active=True,
        )
        admin_role = Role.objects.create(code="CAMPUS_ADMIN", name="Campus Admin", is_active=True)
        for code, module, action in (
            ("admin_portal.access", "admin_portal", "access"),
            ("grade_submissions.read", "grade_submissions", "read"),
        ):
            permission = Permission.objects.create(code=code, module=module, action=action)
            RolePermission.objects.create(role=admin_role, permission=permission)
        UserRole.objects.create(
            user=self.admin_user,
            role=admin_role,
            tenant=self.tenant,
            campus=self.campus,
            is_active=True,
        )
        GradingPeriodLock.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            period_code="PRELIM",
            scope_type=GradingPeriodLock.ScopeType.CAMPUS,
            deadline_at=timezone.now() - timedelta(days=2),
            is_locked=False,
        )

    def test_non_compliance_report_lists_overdue_unsubmitted_class(self):
        SubmissionNonComplianceNotice.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            offering=self.offering,
            template_period=self.period,
            faculty_user=self.faculty_user,
            notice_level=SubmissionNonComplianceNotice.NoticeLevel.WARNING,
            sequence_no=2,
            title="Warning for Continued Non-Compliance",
            message="The class remains overdue after the first notice.",
            deadline_at=timezone.now() - timedelta(days=2),
            issued_at=timezone.now() - timedelta(hours=1),
            recipient_emails_json=[self.faculty_user.email],
            recipient_roles_json=["FACULTY"],
        )
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("admin_portal:overdue_unsubmitted_report"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Non-Compliance on Periodic Grades Submission")
        self.assertContains(response, self.faculty_user.full_name)
        self.assertContains(response, "Warning")
        self.assertContains(response, "Escalate non-compliance")
        self.assertNotContains(response, "Review Request")

    def test_non_compliance_report_excludes_unaccepted_faculty_assignments(self):
        unaccepted_section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="BSIT-1B",
            name="BSIT 1B",
        )
        unaccepted_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=self.academic_year,
            term=self.term,
            course=self.course,
            section=unaccepted_section,
        )
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=unaccepted_offering,
            faculty_user=self.faculty_user,
            is_primary=True,
            response_status=FacultyAssignment.ResponseStatus.PENDING,
        )

        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("admin_portal:overdue_unsubmitted_report"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "BSIT-1A")
        self.assertNotContains(response, "BSIT-1B")

    def test_non_compliance_pagination_preserves_filters(self):
        for index in range(31):
            section = Section.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                department=self.department,
                program=self.program,
                code=f"BSIT-P{index:02d}",
                name=f"BSIT P{index:02d}",
            )
            offering = CourseOffering.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                department=self.department,
                program=self.program,
                academic_year=self.academic_year,
                term=self.term,
                course=self.course,
                section=section,
            )
            FacultyAssignment.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                offering=offering,
                faculty_user=self.faculty_user,
                is_primary=True,
                accepted_at=timezone.now(),
                accepted_by=self.faculty_user,
                response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            )

        self.client.force_login(self.admin_user)
        response = self.client.get(
            reverse("admin_portal:overdue_unsubmitted_report"),
            {"q": "late_faculty", "period_code": "PRELIM"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "q=late_faculty&amp;period_code=PRELIM&amp;page=2")

    def test_grade_submission_list_shows_faculty_name_and_searches_faculty(self):
        GradeSubmission.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.period,
            status=GradeSubmission.Status.DRAFT,
        )

        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("admin_portal:grade_submission_list"), {"q": "late_faculty"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Faculty")
        self.assertContains(response, self.faculty_user.full_name or self.faculty_user.username)
        self.assertContains(response, "A132-ITAPPS")

class AdminFacultyAssignmentPrintReportTests(TestCase):
    """Rendered faculty-assignment print report coverage using the assignment fixtures."""

    setUp = AdminFacultyAssignmentAcceptanceViewTests.setUp
    _create_scoped_faculty = AdminFacultyAssignmentAcceptanceViewTests._create_scoped_faculty

    def test_faculty_assignment_print_button_and_report_use_active_scope(self):
        AcademicGovernanceService.set_active_scope(
            tenant_id=self.tenant.id,
            academic_year=self.academic_year,
            term=self.term,
        )
        self.faculty_user.first_name = "Apolo"
        self.faculty_user.middle_name = "Gabriel"
        self.faculty_user.last_name = "Bejer"
        self.faculty_user.email = "apolo.bejer@ncba.edu.ph"
        self.faculty_user.save(update_fields=["first_name", "middle_name", "last_name", "email", "updated_at"])
        self.course.title = "Zulu Applications"
        self.course.save(update_fields=["title", "updated_at"])
        self.offering.schedule_text = "MW 08:00-09:30"
        self.offering.room = "Room 101"
        self.offering.save(update_fields=["schedule_text", "room", "updated_at"])

        alpha_course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="A001-ALPHA",
            title="Alpha Applications",
        )
        alpha_section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="BSIT-1C",
            name="BSIT 1C",
        )
        alpha_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=self.academic_year,
            term=self.term,
            course=alpha_course,
            section=alpha_section,
            schedule_text="TTH 10:00-11:30",
            room="Lab 2",
        )
        alpha_assignment = FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=alpha_offering,
            faculty_user=self.faculty_user,
            response_status=FacultyAssignment.ResponseStatus.PENDING,
        )
        inactive_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=self.academic_year,
            term=self.term,
            course=Course.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                department=self.department,
                code="A999-INACTIVE",
                title="Inactive Assignment",
            ),
            section=Section.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                department=self.department,
                program=self.program,
                code="BSIT-1D",
                name="BSIT 1D",
            ),
        )
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=inactive_offering,
            faculty_user=self.faculty_user,
            is_active=False,
        )
        old_year = AcademicYear.objects.create(
            tenant=self.tenant,
            code="2024-2025",
            name="AY 2024-2025",
            start_date=date(2024, 6, 1),
            end_date=date(2025, 5, 31),
        )
        old_term = Term.objects.create(
            tenant=self.tenant,
            academic_year=old_year,
            code="OLD",
            name="Old Term",
            sequence_no=1,
        )
        old_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=old_year,
            term=old_term,
            course=Course.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                department=self.department,
                code="OLD101",
                title="Old Assignment",
            ),
            section=Section.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                department=self.department,
                program=self.program,
                code="BSIT-OLD",
                name="BSIT OLD",
            ),
        )
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=old_offering,
            faculty_user=self.faculty_user,
        )
        other_term = Term.objects.create(
            tenant=self.tenant,
            academic_year=self.academic_year,
            code="2ND",
            name="Second Term",
            sequence_no=2,
        )
        other_term_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=self.academic_year,
            term=other_term,
            course=alpha_course,
            section=alpha_section,
        )
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=other_term_offering,
            faculty_user=self.faculty_user,
        )
        other_campus = Campus.objects.create(tenant=self.tenant, code="NCBA-OTHER", name="Other Campus")
        other_department = Department.objects.create(
            tenant=self.tenant,
            campus=other_campus,
            code="OTHER_IS",
            name="Other Information Systems",
        )
        other_program = Program.objects.create(
            tenant=self.tenant,
            campus=other_campus,
            department=other_department,
            code="BSIT-OTHER",
            name="Other BSIT",
        )
        other_campus_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=other_campus,
            department=other_department,
            program=other_program,
            academic_year=self.academic_year,
            term=self.term,
            course=Course.objects.create(
                tenant=self.tenant,
                campus=other_campus,
                department=other_department,
                code="OTHER101",
                title="Other Campus Assignment",
            ),
            section=Section.objects.create(
                tenant=self.tenant,
                campus=other_campus,
                department=other_department,
                program=other_program,
                code="OTHER-1A",
                name="Other 1A",
            ),
        )
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=other_campus,
            offering=other_campus_offering,
            faculty_user=self.faculty_user,
        )

        active_student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="PRINT-ACTIVE",
            first_name="Print",
            last_name="Active",
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            student=active_student,
            course_offering=self.offering,
            enrollment_status=Enrollment.Status.ACTIVE,
        )
        for suffix, status, is_active in [
            ("PRINT-DROPPED", Enrollment.Status.DRP, True),
            ("PRINT-INACTIVE", Enrollment.Status.ACTIVE, False),
        ]:
            student = Student.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                department=self.department,
                program=self.program,
                student_no=suffix,
                first_name="Print",
                last_name=suffix,
            )
            Enrollment.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                academic_year=self.academic_year,
                term=self.term,
                student=student,
                course_offering=self.offering,
                enrollment_status=status,
                is_active=is_active,
            )

        self.client.force_login(self.admin_user)
        no_selection_response = self.client.get(reverse("admin_portal:faculty_assignment_list"))
        selected_response = self.client.get(
            reverse("admin_portal:faculty_assignment_list"),
            {"faculty_user_id": self.faculty_user.id},
        )
        print_response = self.client.get(
            reverse("admin_portal:faculty_assignment_print"),
            {"faculty_user_id": self.faculty_user.id},
        )

        self.assertEqual(no_selection_response.status_code, 200)
        self.assertContains(no_selection_response, "Print Faculty Assignments")
        self.assertContains(no_selection_response, 'disabled aria-disabled="true"')
        self.assertEqual(selected_response.status_code, 200)
        self.assertEqual(
            selected_response.context["faculty_assignment_print_url"],
            f"{reverse('admin_portal:faculty_assignment_print')}?faculty_user_id={self.faculty_user.id}",
        )
        self.assertContains(selected_response, 'target="_blank" rel="noopener"')
        self.assertEqual(print_response.status_code, 200)
        for heading in ["Course Code", "Course Title", "Section", "Schedule", "Room", "Enrolled"]:
            self.assertContains(print_response, heading)
        self.assertContains(print_response, "Bejer, Apolo G. (apolo.bejer@ncba.edu.ph)")
        self.assertContains(print_response, "2025-2026 - AY 2025-2026")
        self.assertContains(print_response, "1ST - First Term")
        row_ids = [row.id for row in print_response.context["report_rows"]]
        self.assertEqual(row_ids, [alpha_assignment.id, self.assignment.id])
        self.assertNotIn(self.second_assignment.id, row_ids)
        self.assertNotIn(old_offering.id, [row.offering_id for row in print_response.context["report_rows"]])
        self.assertNotIn(other_term_offering.id, [row.offering_id for row in print_response.context["report_rows"]])
        self.assertNotIn(other_campus_offering.id, [row.offering_id for row in print_response.context["report_rows"]])
        self.assertNotIn(inactive_offering.id, [row.offering_id for row in print_response.context["report_rows"]])
        self.assertEqual(
            next(row.enrolled_count for row in print_response.context["report_rows"] if row.id == self.assignment.id),
            1,
        )
        self.assertContains(print_response, "MW 08:00-09:30")
        self.assertContains(print_response, "Room 101")
        self.assertEqual(print_response.context["report_total"], 2)

    def test_faculty_assignment_print_rejects_invalid_faculty_and_fails_closed_without_scope(self):
        self.client.force_login(self.admin_user)
        print_url = reverse("admin_portal:faculty_assignment_print")

        no_selection_response = self.client.get(print_url, follow=True)
        self.assertEqual(no_selection_response.status_code, 200)
        self.assertContains(no_selection_response, "Select a faculty member before printing assignments.")
        self.assertEqual(self.client.get(print_url, {"faculty_user_id": "not-an-id"}).status_code, 404)

        other_campus = Campus.objects.create(tenant=self.tenant, code="NCBA-OUT", name="Out of Scope")
        other_department = Department.objects.create(
            tenant=self.tenant,
            campus=other_campus,
            code="OUT_IS",
            name="Out of Scope Information Systems",
        )
        out_of_scope_faculty = self._create_scoped_faculty(
            username="print_out_of_scope",
            email="print_out_of_scope@example.com",
            first_name="Out",
            last_name="Scope",
            campus=other_campus,
            department=other_department,
        )
        self.assertEqual(
            self.client.get(print_url, {"faculty_user_id": out_of_scope_faculty.id}).status_code,
            404,
        )

        self.faculty_user.is_active = False
        self.faculty_user.save(update_fields=["is_active", "updated_at"])
        self.assertEqual(
            self.client.get(print_url, {"faculty_user_id": self.faculty_user.id}).status_code,
            404,
        )

    def test_faculty_assignment_print_redirects_when_active_scope_is_missing_and_is_access_controlled(self):
        self.client.force_login(self.admin_user)
        print_url = reverse("admin_portal:faculty_assignment_print")
        AcademicGovernanceService.set_active_scope(
            tenant_id=self.tenant.id,
            academic_year=None,
            term=None,
        )

        missing_scope_response = self.client.get(
            print_url,
            {"faculty_user_id": self.faculty_user.id},
            follow=True,
        )
        self.assertEqual(missing_scope_response.status_code, 200)
        self.assertContains(
            missing_scope_response,
            "Configure the active academic year and term before printing faculty assignments.",
        )

        self.client.logout()
        self.assertEqual(
            self.client.get(print_url, {"faculty_user_id": self.faculty_user.id}).status_code,
            302,
        )

        denied_user = User.objects.create_user(
            username="print_assignment_denied",
            email="print_assignment_denied@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        denied_role = Role.objects.create(code="PRINT_ASSIGN_DENIED", name="Print Assignment Denied")
        RolePermission.objects.create(
            role=denied_role,
            permission=Permission.objects.get(code="admin_portal.access"),
        )
        UserRole.objects.create(
            user=denied_user,
            role=denied_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )
        self.client.force_login(denied_user)
        self.assertEqual(
            self.client.get(print_url, {"faculty_user_id": self.faculty_user.id}).status_code,
            403,
        )

    def test_faculty_assignment_print_query_count_is_bounded(self):
        AcademicGovernanceService.set_active_scope(
            tenant_id=self.tenant.id,
            academic_year=self.academic_year,
            term=self.term,
        )
        self.client.force_login(self.admin_user)
        url = reverse("admin_portal:faculty_assignment_print")
        params = {"faculty_user_id": self.faculty_user.id}
        self.client.get(url, params)
        with CaptureQueriesContext(connection) as base_queries:
            response = self.client.get(url, params)
        self.assertEqual(response.status_code, 200)

        for index in range(4):
            course = Course.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                department=self.department,
                code=f"PRINT{index:03d}",
                title=f"Print Query {index}",
            )
            section = Section.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                department=self.department,
                program=self.program,
                code=f"BSIT-PQ{index}",
                name=f"BSIT Print Query {index}",
            )
            offering = CourseOffering.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                department=self.department,
                program=self.program,
                academic_year=self.academic_year,
                term=self.term,
                course=course,
                section=section,
            )
            FacultyAssignment.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                offering=offering,
                faculty_user=self.faculty_user,
            )

        with CaptureQueriesContext(connection) as expanded_queries:
            response = self.client.get(url, params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(expanded_queries), len(base_queries))

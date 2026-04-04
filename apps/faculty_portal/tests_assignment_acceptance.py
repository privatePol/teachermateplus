from datetime import date

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.academics.models import AcademicYear, Course, CourseOffering, FacultyAssignment, Section, Term
from apps.academics.services import FacultyAssignmentWorkflowService
from apps.grading.models import (
    CourseTemplateAssignment,
    GradingTemplate,
    GradingTemplateComponent,
    GradingTemplatePeriod,
)
from apps.notifications.models import FacultyMemo
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.tenants.models import Campus, Department, Program, SystemSetting, Tenant


class FacultyAssignmentAcceptanceTests(TestCase):
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
            username="faculty_accept",
            email="faculty_accept@example.com",
            password="testpass123",
            first_name="Faculty",
            last_name="Member",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        faculty_role = Role.objects.create(code="FACULTY", name="Faculty")
        faculty_access = Permission.objects.create(
            code="faculty_portal.access",
            module="faculty_portal",
            action="access",
        )
        RolePermission.objects.create(role=faculty_role, permission=faculty_access)
        UserRole.objects.create(
            user=self.faculty_user,
            role=faculty_role,
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
        self.template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="COLLEGE_TEMPLATE",
            name="College Template",
            is_published=True,
            approval_status=GradingTemplate.ApprovalStatus.APPROVED,
            default_base_value=50,
        )
        self.prelim = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
        )
        class_standing = GradingTemplateComponent.objects.create(
            template_period=self.prelim,
            code="CS",
            name="Class Standing",
            weight_percentage=60,
            sort_order=1,
        )
        GradingTemplateComponent.objects.create(
            template_period=self.prelim,
            code="EXAM",
            name="Prelim Exam",
            weight_percentage=40,
            sort_order=2,
        )
        CourseTemplateAssignment.objects.create(
            course=self.course,
            grading_template=self.template,
            effective_from_term=self.term,
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

    def test_faculty_must_accept_assignment_before_opening_course(self):
        self.client.force_login(self.faculty_user)

        response = self.client.get(
            reverse("faculty_portal:offering_periods", kwargs={"offering_id": self.offering.id})
        )

        self.assertRedirects(response, reverse("faculty_portal:my_courses"))

        accept_response = self.client.post(
            reverse(
                "faculty_portal:faculty_assignment_accept",
                kwargs={"assignment_id": self.assignment.id},
            )
        )
        self.assertRedirects(accept_response, reverse("faculty_portal:my_courses"))

        self.assignment.refresh_from_db()
        self.assertIsNotNone(self.assignment.accepted_at)
        self.assertEqual(self.assignment.accepted_by_id, self.faculty_user.id)

    def test_my_courses_lists_pending_assignments_before_acceptance(self):
        self.client.force_login(self.faculty_user)

        response = self.client.get(reverse("faculty_portal:my_courses"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pending Faculty Assignments")
        self.assertContains(response, "Accept Assignment")
        self.assertContains(response, "College Template")

    def test_faculty_can_request_clarification_with_note(self):
        self.client.force_login(self.faculty_user)

        response = self.client.post(
            reverse(
                "faculty_portal:faculty_assignment_response",
                kwargs={"assignment_id": self.assignment.id},
            ),
            {
                "response_action": "clarification",
                "faculty_response_note": "Please confirm the schedule overlap before I accept this load.",
            },
        )

        self.assertRedirects(response, reverse("faculty_portal:my_courses"))
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.response_status, FacultyAssignment.ResponseStatus.CLARIFICATION_REQUESTED)
        self.assertEqual(
            self.assignment.faculty_response_note,
            "Please confirm the schedule overlap before I accept this load.",
        )

    def test_expired_assignment_cannot_be_accepted_until_admin_refreshes_window(self):
        self.assignment.response_status = FacultyAssignment.ResponseStatus.EXPIRED
        self.assignment.response_due_at = None
        self.assignment.responded_at = timezone.now()
        self.assignment.save(update_fields=["response_status", "response_due_at", "responded_at", "updated_at"])

        self.client.force_login(self.faculty_user)
        response = self.client.post(
            reverse(
                "faculty_portal:faculty_assignment_accept",
                kwargs={"assignment_id": self.assignment.id},
            )
        )

        self.assertRedirects(response, reverse("faculty_portal:my_courses"))
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.response_status, FacultyAssignment.ResponseStatus.EXPIRED)

    def test_faculty_can_open_read_only_grading_template_view_after_acceptance(self):
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse("faculty_portal:offering_grading_template", kwargs={"offering_id": self.offering.id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Grading Template")
        self.assertContains(response, "College Template")
        self.assertContains(response, "PRELIM GRADE = Class Standing (60.00%) + Prelim Exam (40.00%)")

    def test_faculty_can_open_at_risk_monitor_when_prediction_is_enabled(self):
        SystemSetting.objects.update_or_create(
            tenant=None,
            setting_key="FEATURE_GRADE_PREDICTION_ENABLED",
            defaults={"setting_value": "1", "value_type": SystemSetting.ValueType.BOOL, "is_active": True},
        )
        SystemSetting.objects.update_or_create(
            tenant=self.tenant,
            setting_key="GRADE_PREDICTION_ENABLED",
            defaults={"setting_value": "1", "value_type": SystemSetting.ValueType.BOOL, "is_active": True},
        )

        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])

        self.client.force_login(self.faculty_user)
        response = self.client.get(reverse("faculty_portal:student_at_risk_monitor"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Student At-Risk Monitor")

    def test_faculty_can_create_and_view_private_memo(self):
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])

        self.client.force_login(self.faculty_user)
        response = self.client.post(
            reverse("faculty_portal:memo_center"),
            {
                "memo_type": FacultyMemo.MemoType.CLASS,
                "offering": str(self.offering.id),
                "student": "",
                "title": "Follow up on Quiz 1",
                "body": "Check the class standing scores before Friday.",
                "is_pinned": "on",
            },
        )

        self.assertRedirects(response, reverse("faculty_portal:memo_center"))
        self.assertTrue(
            FacultyMemo.objects.filter(
                tenant=self.tenant,
                faculty_user=self.faculty_user,
                title="Follow up on Quiz 1",
                body="Check the class standing scores before Friday.",
                is_pinned=True,
                is_active=True,
            ).exists()
        )

        center_response = self.client.get(reverse("faculty_portal:memo_center"))
        self.assertEqual(center_response.status_code, 200)
        self.assertContains(center_response, "Faculty Notes / Private Memo")
        self.assertContains(center_response, "Follow up on Quiz 1")

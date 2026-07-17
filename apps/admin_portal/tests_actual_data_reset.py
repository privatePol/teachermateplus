from datetime import date

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.academics.models import AcademicYear, Course, CourseOffering, FacultyAssignment, Section, Term
from apps.attendance.models import AttendanceRecord, AttendanceSession
from apps.auditlog.models import AuditLog
from apps.enrollment.models import Enrollment
from apps.grading.models import (
    CourseTemplateAssignment,
    GradeActivity,
    GradeSubmission,
    GradingPeriodLock,
    GradingTemplate,
    GradingTemplateComponent,
    GradingTemplatePeriod,
    StudentActivityScore,
    StudentFinalGrade,
    StudentPeriodGrade,
    TenantGradingProfile,
)
from apps.navigation.models import MenuGroup, MenuItem, MenuItemPermission
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.students.models import Student
from apps.tenants.models import Campus, Department, Program, SystemSetting, Tenant


class ActualDataResetTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="superadmin",
            email="superadmin@example.com",
            password="testpass123",
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        self.admin_access = Permission.objects.create(
            code="admin_portal.access",
            module="admin_portal",
            action="access",
        )
        self.settings_update = Permission.objects.create(
            code="system_settings.update",
            module="system_settings",
            action="update",
        )
        self.actual_data_reset = Permission.objects.create(
            code="actual_data_reset.run",
            module="actual_data_reset",
            action="run",
        )
        self.role = Role.objects.create(code="SUPER_ADMIN", name="Super Admin", is_system=True)
        RolePermission.objects.create(role=self.role, permission=self.admin_access)
        RolePermission.objects.create(role=self.role, permission=self.settings_update)
        RolePermission.objects.create(role=self.role, permission=self.actual_data_reset)
        self.group, _ = MenuGroup.objects.update_or_create(
            portal="ADMIN",
            code="IMPORTS",
            defaults={
                "label": "Tools",
                "sort_order": 95,
                "is_active": True,
            },
        )
        self.item, _ = MenuItem.objects.update_or_create(
            portal="ADMIN",
            code="ACTUAL_DATA_RESET",
            defaults={
                "menu_group": self.group,
                "label": "Actual Data Reset",
                "route_name": "admin_portal:actual_data_reset",
                "sort_order": 70,
                "is_active": True,
            },
        )
        MenuItemPermission.objects.get_or_create(
            menu_item=self.item,
            permission=self.actual_data_reset,
        )

        self.tenant = Tenant.objects.create(code="NCBA", name="NCBA")
        self.campus = Campus.objects.create(tenant=self.tenant, code="MAIN", name="Main")
        self.department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="COLL",
            name="College",
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
            code="2026-2027",
            name="AY 2026-2027",
            start_date=date(2026, 6, 1),
            end_date=date(2027, 5, 31),
        )
        self.term = Term.objects.create(
            tenant=self.tenant,
            academic_year=self.academic_year,
            code="1ST",
            name="First Term",
            sequence_no=1,
            start_date=date(2026, 6, 1),
            end_date=date(2026, 10, 31),
        )
        self.course = Course.objects.create(tenant=self.tenant, code="IT101", title="IT 101")
        self.section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="BSIT-1A",
            name="BSIT 1A",
        )
        SystemSetting.objects.create(
            tenant=None,
            setting_key="GLOBAL_ONLY",
            setting_value="1",
        )
        SystemSetting.objects.create(
            tenant=self.tenant,
            setting_key="TENANT_ONLY",
            setting_value="1",
        )
        UserRole.objects.create(user=self.admin, role=self.role, tenant=self.tenant, campus=self.campus)
        self.client.force_login(self.admin)

    def test_reset_page_shows_preview(self):
        response = self.client.get(reverse("admin_portal:actual_data_reset"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Actual Data Reset")
        self.assertContains(response, "Tenant / Campus / Department / Program")
        self.assertContains(response, "RESET ACTUAL DATA")

    def test_reset_keeps_security_shell_and_deletes_actual_data(self):
        response = self.client.post(
            reverse("admin_portal:actual_data_reset"),
            {
                "reset_scope": "full",
                "confirmation_phrase": "RESET ACTUAL DATA",
                "reset_reason": "Approved training-data rebuild.",
                "understood": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Tenant.objects.count(), 0)
        self.assertEqual(Campus.objects.count(), 0)
        self.assertEqual(Department.objects.count(), 0)
        self.assertEqual(Program.objects.count(), 0)
        self.assertEqual(AcademicYear.objects.count(), 0)
        self.assertEqual(Term.objects.count(), 0)
        self.assertEqual(Course.objects.count(), 0)
        self.assertEqual(Section.objects.count(), 0)
        self.assertEqual(UserRole.objects.count(), 0)
        self.assertEqual(SystemSetting.objects.filter(tenant__isnull=False).count(), 0)
        self.assertEqual(SystemSetting.objects.filter(tenant__isnull=True).count(), 1)
        self.assertEqual(User.objects.count(), 1)
        self.assertTrue(Role.objects.filter(code="SUPER_ADMIN").exists())
        self.assertTrue(Permission.objects.filter(code="admin_portal.access").exists())
        self.assertTrue(Permission.objects.filter(code="system_settings.update").exists())
        self.assertTrue(Permission.objects.filter(code="actual_data_reset.run").exists())
        self.assertEqual(RolePermission.objects.filter(role=self.role).count(), 3)
        self.assertTrue(MenuGroup.objects.filter(portal="ADMIN", code="IMPORTS").exists())
        self.assertTrue(MenuItem.objects.filter(portal="ADMIN", code="ACTUAL_DATA_RESET").exists())
        self.assertTrue(
            MenuItemPermission.objects.filter(
                menu_item__code="ACTUAL_DATA_RESET",
                permission=self.actual_data_reset,
            ).exists()
        )
        self.admin.refresh_from_db()
        self.assertIsNone(self.admin.default_tenant_id)
        reset_log = AuditLog.objects.get(entity_type="ActualDataReset", action="RESET")
        self.assertTrue(reset_log.metadata_json["critical_action"])
        self.assertEqual(reset_log.metadata_json["reason"], "Approved training-data rebuild.")
        self.assertIn("audit_export_path", reset_log.metadata_json)
        self.assertTrue(reset_log.metadata_json["audit_export_validation"]["ok"])

    def test_faculty_grade_transaction_reset_keeps_setup_and_enrollments_by_default(self):
        offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=self.academic_year,
            term=self.term,
            course=self.course,
            section=self.section,
        )
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=offering,
            faculty_user=self.admin,
            accepted_by=self.admin,
            accepted_at=timezone.now(),
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
        )
        student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="S-001",
            last_name="Demo",
            first_name="Student",
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            student=student,
            course_offering=offering,
        )
        template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="TMP",
            name="Template",
            is_published=True,
            is_active=True,
        )
        period = GradingTemplatePeriod.objects.create(
            template=template,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
        )
        component = GradingTemplateComponent.objects.create(
            template_period=period,
            code="CS",
            name="Class Standing",
            weight_percentage=100,
        )
        CourseTemplateAssignment.objects.create(
            course=self.course,
            grading_template=template,
            effective_from_term=self.term,
        )
        TenantGradingProfile.objects.create(
            tenant=self.tenant,
            profile_code="DEFAULT",
            profile_name="Default",
            grading_template=template,
            is_active=True,
        )
        activity = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=offering,
            template_period=period,
            template_component=component,
            title="Quiz",
            total_score=100,
            created_by_user=self.admin,
        )
        StudentActivityScore.objects.create(
            activity=activity,
            student=student,
            raw_score=90,
            computed_score=95,
            encoded_by_user=self.admin,
        )
        StudentPeriodGrade.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=offering,
            template_period=period,
            student=student,
            period_grade=95,
            computed_by_user=self.admin,
        )
        StudentFinalGrade.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=offering,
            student=student,
            final_grade=95,
            computed_by_user=self.admin,
        )
        GradeSubmission.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=offering,
            template_period=period,
            status=GradeSubmission.Status.SUBMITTED,
        )
        GradingPeriodLock.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            period_code=period.code,
            scope_type=GradingPeriodLock.ScopeType.COURSE,
            course_offering=offering,
            is_locked=True,
        )
        session = AttendanceSession.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=offering,
            template_period=period,
            session_date=date(2026, 6, 15),
            title="Meeting",
        )
        AttendanceRecord.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            session=session,
            student=student,
            status_code=AttendanceRecord.Status.PRESENT,
            recorded_by_user=self.admin,
        )

        response = self.client.post(
            reverse("admin_portal:actual_data_reset"),
            {
                "reset_scope": "faculty_grade_transactions",
                "confirmation_phrase": "RESET FACULTY GRADES",
                "reset_reason": "Reset demo grading transactions only.",
                "understood": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Tenant.objects.count(), 1)
        self.assertEqual(Campus.objects.count(), 1)
        self.assertEqual(Department.objects.count(), 1)
        self.assertEqual(Program.objects.count(), 1)
        self.assertEqual(AcademicYear.objects.count(), 1)
        self.assertEqual(Term.objects.count(), 1)
        self.assertEqual(Course.objects.count(), 1)
        self.assertEqual(Section.objects.count(), 1)
        self.assertEqual(CourseOffering.objects.count(), 1)
        self.assertEqual(Student.objects.count(), 1)
        self.assertEqual(Enrollment.objects.count(), 1)
        self.assertEqual(GradingTemplate.objects.count(), 1)
        self.assertEqual(TenantGradingProfile.objects.count(), 1)
        self.assertEqual(CourseTemplateAssignment.objects.count(), 1)
        self.assertEqual(FacultyAssignment.objects.count(), 0)
        self.assertEqual(GradeActivity.objects.count(), 0)
        self.assertEqual(StudentActivityScore.objects.count(), 0)
        self.assertEqual(StudentPeriodGrade.objects.count(), 0)
        self.assertEqual(StudentFinalGrade.objects.count(), 0)
        self.assertEqual(GradeSubmission.objects.count(), 0)
        self.assertEqual(GradingPeriodLock.objects.count(), 0)
        self.assertEqual(AttendanceSession.objects.count(), 0)
        self.assertEqual(AttendanceRecord.objects.count(), 0)
        reset_log = AuditLog.objects.get(entity_type="ActualDataReset", action="RESET")
        self.assertEqual(reset_log.metadata_json["reset_scope"], "faculty_grade_transactions")
        self.assertEqual(reset_log.metadata_json["confirmation_phrase"], "RESET FACULTY GRADES")

    def test_reset_with_invalid_scope_is_rejected_without_deleting_data(self):
        response = self.client.post(
            reverse("admin_portal:actual_data_reset"),
            {
                "reset_scope": "unexpected_scope",
                "confirmation_phrase": "RESET ACTUAL DATA",
                "reset_reason": "Attempt invalid reset scope.",
                "understood": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choose a valid reset scope")
        self.assertEqual(Tenant.objects.count(), 1)
        self.assertEqual(SystemSetting.objects.filter(tenant__isnull=False).count(), 1)
        self.assertFalse(AuditLog.objects.filter(entity_type="ActualDataReset", action="RESET").exists())

    def test_faculty_grade_transaction_reset_can_optionally_clear_enrollments(self):
        offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=self.academic_year,
            term=self.term,
            course=self.course,
            section=self.section,
        )
        student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="S-002",
            last_name="Demo",
            first_name="Two",
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            student=student,
            course_offering=offering,
        )

        response = self.client.post(
            reverse("admin_portal:actual_data_reset"),
            {
                "reset_scope": "faculty_grade_transactions",
                "include_enrollments": "on",
                "confirmation_phrase": "RESET FACULTY GRADES",
                "reset_reason": "Reset demo grading and class list transactions.",
                "understood": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Student.objects.count(), 1)
        self.assertEqual(Enrollment.objects.count(), 0)
        self.assertEqual(CourseOffering.objects.count(), 1)

    @override_settings(DJANGO_ENV="production", ACTUAL_DATA_RESET_ALLOW_PRODUCTION=False)
    def test_reset_is_blocked_by_default_in_production(self):
        response = self.client.post(
            reverse("admin_portal:actual_data_reset"),
            {
                "reset_scope": "full",
                "confirmation_phrase": "RESET ACTUAL DATA",
                "reset_reason": "Production reset attempt.",
                "understood": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Tenant.objects.count(), 1)
        self.assertFalse(AuditLog.objects.filter(entity_type="ActualDataReset", action="RESET").exists())
        self.assertContains(response, "disabled in production")

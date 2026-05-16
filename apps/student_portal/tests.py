from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import AcademicYear, Course, CourseOffering, Section, Term
from apps.attendance.models import AttendanceRecord, AttendanceSession
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.enrollment.models import Enrollment
from apps.grading.models import (
    GradeSubmission,
    GradingTemplate,
    GradingTemplatePeriod,
    StudentFinalGrade,
    StudentPeriodGrade,
)
from apps.rbac.models import Permission, UserPermission
from apps.students.models import Student
from apps.tenants.models import Campus, Department, Program, SystemSetting, Tenant

from .models import StudentAccountLink
from .services import create_student_account_link

User = get_user_model()


class StudentPortalFoundationTests(TestCase):
    def setUp(self):
        Permission.objects.get_or_create(
            code="admin_portal.access",
            defaults={
                "module": "admin_portal",
                "action": "access",
                "description": "Admin Portal access",
            },
        )
        Permission.objects.get_or_create(
            code="system_settings.update",
            defaults={
                "module": "system_settings",
                "action": "update",
                "description": "Update system settings",
            },
        )
        Permission.objects.get_or_create(
            code="student_account_links.manage",
            defaults={
                "module": "student_account_links",
                "action": "manage",
                "description": "Manage student account links",
            },
        )
        self.permission, _ = Permission.objects.get_or_create(
            code="student_portal.access",
            defaults={
                "module": "student_portal",
                "action": "access",
                "description": "Student Portal access",
            },
        )
        self.tenant = Tenant.objects.create(code="T1", name="Tenant One")
        self.campus = Campus.objects.create(tenant=self.tenant, code="C1", name="Campus One")
        self.department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="D1",
            name="Department One",
        )
        self.program = Program.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="P1",
            name="Program One",
        )
        self.academic_year = AcademicYear.objects.create(
            tenant=self.tenant,
            code="2526",
            name="AY 2025-2026",
            start_date="2025-06-01",
            end_date="2026-05-31",
        )
        self.term = Term.objects.create(
            tenant=self.tenant,
            academic_year=self.academic_year,
            code="T1",
            name="Term 1",
            sequence_no=1,
        )
        self.course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="MATH101",
            title="College Algebra",
        )
        self.section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="BSIS1A",
            name="BSIS 1A",
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
        self.student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="S-001",
            last_name="Reyes",
            first_name="Ana",
        )
        self.other_student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="S-002",
            last_name="Santos",
            first_name="Ben",
            official_email="ben.santos@ncba.edu.ph",
        )
        self.enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            student=self.student,
            course_offering=self.offering,
        )
        self.user = User.objects.create_user(
            username="student1",
            email="student1@example.test",
            password="Password123!",
            default_tenant=self.tenant,
            default_campus=self.campus,
        )
        UserPermission.objects.create(
            user=self.user,
            permission=self.permission,
            grant_type=UserPermission.GrantType.ALLOW,
            tenant=self.tenant,
            campus=self.campus,
        )
        SystemSettingService.set(
            FeatureSettingsService.STUDENT_PORTAL_ENABLED_KEY,
            True,
            tenant_id=self.tenant.id,
            value_type=SystemSetting.ValueType.BOOL,
        )
        self.link = create_student_account_link(
            tenant=self.tenant,
            campus=self.campus,
            student=self.student,
            user=self.user,
        )
        self.grading_template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="DEFAULT",
            name="Default Template",
            is_published=True,
        )
        self.prelim_period = GradingTemplatePeriod.objects.create(
            template=self.grading_template,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
        )
        self.midterm_period = GradingTemplatePeriod.objects.create(
            template=self.grading_template,
            code="MIDTERM",
            name="Midterm",
            sequence_no=2,
        )
        self.admin_user = User.objects.create_superuser(
            username="superadmin",
            email="superadmin@example.test",
            password="Password123!",
            default_tenant=self.tenant,
            default_campus=self.campus,
            privacy_consent_version="2026-03",
            privacy_consent_at=timezone.now(),
        )

    def _create_period_grade(self, *, student=None, offering=None, period=None, submitted=True, value="89.50"):
        student = student or self.student
        offering = offering or self.offering
        period = period or self.prelim_period
        grade = StudentPeriodGrade.objects.create(
            tenant=offering.tenant,
            campus=offering.campus,
            offering=offering,
            template_period=period,
            student=student,
            class_standing_grade=value,
            exam_grade=value,
            period_grade=value,
            is_finalized=submitted,
        )
        GradeSubmission.objects.create(
            tenant=offering.tenant,
            campus=offering.campus,
            offering=offering,
            template_period=period,
            status=GradeSubmission.Status.SUBMITTED if submitted else GradeSubmission.Status.DRAFT,
            submitted_by_user=self.admin_user if submitted else None,
            submitted_at=timezone.now() if submitted else None,
        )
        return grade

    def _create_final_grade(self, *, student=None, offering=None, submitted=True, value="90.00"):
        student = student or self.student
        offering = offering or self.offering
        return StudentFinalGrade.objects.create(
            tenant=offering.tenant,
            campus=offering.campus,
            offering=offering,
            student=student,
            final_grade=value,
            remarks="PASSED",
            is_submitted=submitted,
        )

    def _create_attendance_record(
        self,
        *,
        student=None,
        offering=None,
        period=None,
        status=AttendanceRecord.Status.PRESENT,
        session_date="2026-01-15",
        title="Lecture",
        remarks="",
    ):
        student = student or self.student
        offering = offering or self.offering
        period = period or self.prelim_period
        session = AttendanceSession.objects.create(
            tenant=offering.tenant,
            campus=offering.campus,
            offering=offering,
            template_period=period,
            session_date=session_date,
            title=title,
            created_by_user=self.admin_user,
        )
        return AttendanceRecord.objects.create(
            tenant=offering.tenant,
            campus=offering.campus,
            session=session,
            student=student,
            status_code=status,
            recorded_by_user=self.admin_user,
            remarks=remarks,
        )

    def _create_other_scope(self, *, tenant_code="T2", campus_code="C2", course_code="ENG101"):
        tenant = Tenant.objects.create(code=tenant_code, name=f"Tenant {tenant_code}")
        campus = Campus.objects.create(tenant=tenant, code=campus_code, name=f"Campus {campus_code}")
        department = Department.objects.create(tenant=tenant, campus=campus, code="D", name="Department")
        program = Program.objects.create(tenant=tenant, campus=campus, department=department, code="P", name="Program")
        ay = AcademicYear.objects.create(
            tenant=tenant,
            code="2526",
            name="AY 2025-2026",
            start_date="2025-06-01",
            end_date="2026-05-31",
        )
        term = Term.objects.create(tenant=tenant, academic_year=ay, code="T1", name="Term 1", sequence_no=1)
        course = Course.objects.create(
            tenant=tenant,
            campus=campus,
            department=department,
            code=course_code,
            title=f"{course_code} Title",
        )
        section = Section.objects.create(
            tenant=tenant,
            campus=campus,
            department=department,
            program=program,
            code="SEC",
            name="Section",
        )
        offering = CourseOffering.objects.create(
            tenant=tenant,
            campus=campus,
            department=department,
            program=program,
            academic_year=ay,
            term=term,
            course=course,
            section=section,
        )
        student = Student.objects.create(
            tenant=tenant,
            campus=campus,
            department=department,
            program=program,
            student_no="S-001",
            last_name="Other",
            first_name="Student",
        )
        Enrollment.objects.create(
            tenant=tenant,
            campus=campus,
            academic_year=ay,
            term=term,
            student=student,
            course_offering=offering,
        )
        return tenant, campus, student, offering

    def test_student_with_active_link_can_open_dashboard(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("student_portal:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ana Reyes")
        self.assertContains(response, "College Algebra")

    def test_student_sees_only_own_profile(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("student_portal:profile"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "S-001")
        self.assertNotContains(response, "S-002")
        self.assertNotContains(response, "Santos")

    def test_student_sees_only_own_courses(self):
        other_course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="SCI101",
            title="Science",
        )
        other_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=self.academic_year,
            term=self.term,
            course=other_course,
            section=self.section,
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            student=self.other_student,
            course_offering=other_offering,
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse("student_portal:courses"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "College Algebra")
        self.assertNotContains(response, "Science")

    def test_admin_can_create_valid_student_account_link(self):
        admin_user = User.objects.create_user(
            username="admin",
            email="admin@example.test",
            password="Password123!",
            default_tenant=self.tenant,
            default_campus=self.campus,
        )
        student_user = User.objects.create_user(
            username="student2",
            email="student2@example.test",
            password="Password123!",
            default_tenant=self.tenant,
            default_campus=self.campus,
        )
        link = create_student_account_link(
            tenant=self.tenant,
            campus=self.campus,
            student=self.other_student,
            user=student_user,
            linked_by_user=admin_user,
        )
        self.assertTrue(link.is_active)
        self.assertEqual(link.linked_by_user, admin_user)

    def test_admin_portal_lists_student_account_links(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("admin_portal:student_account_link_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Student Account Links")
        self.assertContains(response, "S-001")
        self.assertContains(response, "student1")

    def test_admin_portal_can_create_student_account_link(self):
        student_user = User.objects.create_user(
            username="student2",
            email="student2@example.test",
            password="Password123!",
            default_tenant=self.tenant,
            default_campus=self.campus,
        )
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse("admin_portal:student_account_link_create"),
            {
                "tenant": self.tenant.id,
                "campus": self.campus.id,
                "student": self.other_student.id,
                "user": student_user.id,
                "is_active": "on",
                "notes": "Provisioned for local Student Portal test.",
            },
        )
        self.assertEqual(response.status_code, 302)
        link = StudentAccountLink.objects.get(student=self.other_student, user=student_user)
        self.assertTrue(link.is_active)
        self.assertEqual(link.linked_by_user, self.admin_user)

    def test_admin_portal_deactivates_student_account_link(self):
        self.client.force_login(self.admin_user)
        response = self.client.post(reverse("admin_portal:student_account_link_deactivate", args=[self.link.id]))
        self.assertEqual(response.status_code, 302)
        self.link.refresh_from_db()
        self.assertFalse(self.link.is_active)

    def test_admin_portal_provisions_student_account_automatically(self):
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse("admin_portal:student_account_provision"),
            {
                "student": self.other_student.id,
                "verify_official_email": "on",
                "notes": "Provisioned by test.",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.other_student.refresh_from_db()
        self.assertIsNotNone(self.other_student.official_email_verified_at)
        user = User.objects.get(email="ben.santos@ncba.edu.ph")
        self.assertTrue(user.must_change_password)
        self.assertFalse(user.is_staff)
        self.assertEqual(user.default_tenant, self.tenant)
        self.assertEqual(user.default_campus, self.campus)
        link = StudentAccountLink.objects.get(student=self.other_student, user=user, is_active=True)
        self.assertEqual(link.linked_by_user, self.admin_user)
        self.assertTrue(
            UserPermission.objects.filter(
                user=user,
                permission__code="student_portal.access",
                tenant=self.tenant,
                campus=self.campus,
                grant_type=UserPermission.GrantType.ALLOW,
            ).exists()
        )

    def test_admin_portal_provisioning_blocks_missing_official_email(self):
        self.other_student.official_email = ""
        self.other_student.save(update_fields=["official_email", "updated_at"])
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse("admin_portal:student_account_provision"),
            {
                "student": self.other_student.id,
                "verify_official_email": "on",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This student has no official email")
        self.assertFalse(StudentAccountLink.objects.filter(student=self.other_student).exists())

    def test_admin_portal_provisioning_can_link_existing_user(self):
        existing_user = User.objects.create_user(
            username="ben-existing",
            email="ben.santos@ncba.edu.ph",
            password="Password123!",
            default_tenant=self.tenant,
            default_campus=self.campus,
        )
        self.other_student.official_email_verified_at = timezone.now()
        self.other_student.save(update_fields=["official_email_verified_at", "updated_at"])
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse("admin_portal:student_account_provision"),
            {
                "student": self.other_student.id,
                "existing_user": existing_user.id,
            },
        )
        self.assertEqual(response.status_code, 302)
        link = StudentAccountLink.objects.get(student=self.other_student, is_active=True)
        self.assertEqual(link.user, existing_user)

    def test_configurable_features_page_exposes_student_portal_toggle(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("admin_portal:configurable_features_settings"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Enable Student Portal")
        self.assertContains(response, "Show period grades after submission")
        self.assertContains(response, "Show final grade after submission")
        self.assertContains(response, "Show attendance details")

    def test_student_sees_submitted_grades_only(self):
        self._create_period_grade(period=self.prelim_period, submitted=True, value="91.25")
        self._create_period_grade(period=self.midterm_period, submitted=False, value="72.00")
        self._create_final_grade(submitted=True, value="92.00")
        self.client.force_login(self.user)
        response = self.client.get(reverse("student_portal:grades"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "College Algebra")
        self.assertContains(response, "91.25")
        self.assertContains(response, "92.00")
        self.assertNotContains(response, "72.00")
        self.assertNotContains(response, "MIDTERM")

    def test_student_grade_detail_requires_owned_offering(self):
        _tenant, _campus, _student, offering = self._create_other_scope(course_code="BIO101")
        self.client.force_login(self.user)
        response = self.client.get(reverse("student_portal:grade_detail", args=[offering.id]))
        self.assertEqual(response.status_code, 403)

    def test_student_grades_do_not_leak_other_student_rows(self):
        self._create_period_grade(student=self.student, submitted=True, value="88.00")
        self._create_final_grade(student=self.student, submitted=True, value="89.00")
        other_course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="CHEM101",
            title="Chemistry",
        )
        other_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=self.academic_year,
            term=self.term,
            course=other_course,
            section=self.section,
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            student=self.other_student,
            course_offering=other_offering,
        )
        self._create_period_grade(student=self.other_student, offering=other_offering, submitted=True, value="99.00")
        self._create_final_grade(student=self.other_student, offering=other_offering, submitted=True, value="98.00")
        self.client.force_login(self.user)
        response = self.client.get(reverse("student_portal:grades"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "88.00")
        self.assertContains(response, "89.00")
        self.assertNotContains(response, "99.00")
        self.assertNotContains(response, "98.00")
        self.assertNotContains(response, "Chemistry")

    def test_student_grade_release_toggle_hides_period_and_final_grades(self):
        self._create_period_grade(submitted=True, value="94.00")
        self._create_final_grade(submitted=True, value="95.00")
        SystemSettingService.set(
            FeatureSettingsService.STUDENT_PORTAL_PERIOD_GRADES_AFTER_SUBMISSION_KEY,
            False,
            tenant_id=self.tenant.id,
            value_type=SystemSetting.ValueType.BOOL,
        )
        SystemSettingService.set(
            FeatureSettingsService.STUDENT_PORTAL_FINAL_GRADES_AFTER_SUBMISSION_KEY,
            False,
            tenant_id=self.tenant.id,
            value_type=SystemSetting.ValueType.BOOL,
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse("student_portal:grades"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "College Algebra")
        self.assertNotContains(response, "94.00")
        self.assertNotContains(response, "95.00")

    def test_student_sees_only_own_attendance(self):
        self._create_attendance_record(
            student=self.student,
            status=AttendanceRecord.Status.PRESENT,
            session_date="2026-01-15",
            title="Student-owned lecture",
            remarks="On time",
        )
        self._create_attendance_record(
            student=self.other_student,
            status=AttendanceRecord.Status.ABSENT,
            session_date="2026-01-16",
            title="Other student lecture",
            remarks="Do not leak",
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse("student_portal:attendance"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "College Algebra")
        self.assertContains(response, "Student-owned lecture")
        self.assertContains(response, "On time")
        self.assertNotContains(response, "Other student lecture")
        self.assertNotContains(response, "Do not leak")

    def test_student_attendance_does_not_leak_other_tenant_records(self):
        _tenant, _campus, student, offering = self._create_other_scope(course_code="PE101")
        self._create_attendance_record(
            student=student,
            offering=offering,
            status=AttendanceRecord.Status.LATE,
            session_date="2026-01-17",
            title="Other tenant session",
            remarks="Cross tenant",
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse("student_portal:attendance"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Other tenant session")
        self.assertNotContains(response, "Cross tenant")
        self.assertNotContains(response, "PE101")

    def test_student_attendance_detail_toggle_hides_session_rows(self):
        self._create_attendance_record(
            status=AttendanceRecord.Status.LATE,
            session_date="2026-01-18",
            title="Detail hidden session",
            remarks="Hidden detail",
        )
        SystemSettingService.set(
            FeatureSettingsService.STUDENT_PORTAL_ATTENDANCE_DETAILS_ENABLED_KEY,
            False,
            tenant_id=self.tenant.id,
            value_type=SystemSetting.ValueType.BOOL,
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse("student_portal:attendance"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "College Algebra")
        self.assertContains(response, "Session-level attendance details are hidden by configuration.")
        self.assertNotContains(response, "Detail hidden session")
        self.assertNotContains(response, "Hidden detail")

    def test_user_without_active_link_cannot_open_dashboard(self):
        unlinked_user = User.objects.create_user("unlinked", "unlinked@example.test", "Password123!")
        UserPermission.objects.create(
            user=unlinked_user,
            permission=self.permission,
            grant_type=UserPermission.GrantType.ALLOW,
            tenant=self.tenant,
            campus=self.campus,
        )
        self.client.force_login(unlinked_user)
        response = self.client.get(reverse("student_portal:dashboard"))
        self.assertEqual(response.status_code, 403)

    def test_inactive_link_blocks_access(self):
        self.link.is_active = False
        self.link.save()
        self.client.force_login(self.user)
        response = self.client.get(reverse("student_portal:dashboard"))
        self.assertEqual(response.status_code, 403)

    def test_student_cannot_open_another_students_offering_detail(self):
        other_course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="HIST101",
            title="History",
        )
        other_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=self.academic_year,
            term=self.term,
            course=other_course,
            section=self.section,
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            student=self.other_student,
            course_offering=other_offering,
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse("student_portal:course_detail", args=[other_offering.id]))
        self.assertEqual(response.status_code, 403)
        self.assertNotContains(response, "History", status_code=403)

    def test_student_cannot_access_records_from_another_tenant(self):
        _tenant, _campus, _student, offering = self._create_other_scope(course_code="ENG101")
        self.client.force_login(self.user)
        response = self.client.get(reverse("student_portal:courses"))
        self.assertNotContains(response, "ENG101")
        detail = self.client.get(reverse("student_portal:course_detail", args=[offering.id]))
        self.assertEqual(detail.status_code, 403)

    def test_cross_tenant_link_creation_is_blocked(self):
        tenant, campus, student, _offering = self._create_other_scope(course_code="ART101")
        link = StudentAccountLink(tenant=self.tenant, campus=campus, student=student, user=self.user)
        with self.assertRaises(ValidationError):
            link.full_clean()

    def test_cross_campus_link_creation_is_blocked(self):
        campus = Campus.objects.create(tenant=self.tenant, code="C3", name="Campus Three")
        link = StudentAccountLink(tenant=self.tenant, campus=campus, student=self.student, user=self.user)
        with self.assertRaises(ValidationError):
            link.full_clean()

    def test_mismatched_campus_link_cannot_leak_records(self):
        campus = Campus.objects.create(tenant=self.tenant, code="C4", name="Campus Four")
        self.link.is_active = False
        self.link.save()
        with self.assertRaises(ValidationError):
            StudentAccountLink.objects.create(
                tenant=self.tenant,
                campus=campus,
                student=self.student,
                user=self.user,
            )

    def test_invalid_offering_url_returns_safe_denial(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("student_portal:course_detail", args=[999999]))
        self.assertEqual(response.status_code, 403)

    def test_one_active_link_per_student_and_user(self):
        second_user = User.objects.create_user(
            username="student-duplicate",
            email="student-duplicate@example.test",
            password="Password123!",
            default_tenant=self.tenant,
            default_campus=self.campus,
        )
        with self.assertRaises(ValidationError):
            StudentAccountLink.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                student=self.student,
                user=second_user,
            )

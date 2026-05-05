from datetime import date

from django.test import TestCase
from django.utils import timezone

from apps.academics.models import AcademicYear, Course, CourseOffering, FacultyAssignment, Section, Term
from apps.accounts.models import User
from apps.faculty_portal.views import _faculty_offering_queryset
from apps.tenants.models import Campus, Department, Program, Tenant


class FacultyPortalInactiveScopeTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="NCBA", name="NCBA")
        self.campus = Campus.objects.create(tenant=self.tenant, code="NCBA-03", name="Taytay")
        self.department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="BED_SHS",
            name="Basic Ed SHS",
        )
        self.program = Program.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="SHS",
            name="Senior High School",
        )
        self.academic_year = AcademicYear.objects.create(
            tenant=self.tenant,
            code="AY2526",
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
        )
        self.course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="SHS101",
            title="SHS Course",
        )
        self.section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="SHS-1A",
            name="SHS 1A",
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
        self.faculty = User.objects.create_user(
            username="faculty_scope",
            email="faculty_scope@example.com",
            password="testpass123",
        )
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            faculty_user=self.faculty,
            accepted_at=timezone.now(),
        )

    def test_faculty_offerings_exclude_inactive_department_chain(self):
        self.department.is_active = False
        self.department.save(update_fields=["is_active"])

        self.assertFalse(_faculty_offering_queryset(self.faculty).filter(id=self.offering.id).exists())

    def test_faculty_offerings_exclude_inactive_course_and_section(self):
        inactive_course_offering = self._create_parallel_offering("COURSE_OFF", "SEC-A")
        inactive_course_offering.course.is_active = False
        inactive_course_offering.course.save(update_fields=["is_active"])
        inactive_section_offering = self._create_parallel_offering("COURSE_SEC", "SEC-OFF")
        inactive_section_offering.section.is_active = False
        inactive_section_offering.section.save(update_fields=["is_active"])

        visible_ids = set(_faculty_offering_queryset(self.faculty).values_list("id", flat=True))

        self.assertNotIn(inactive_course_offering.id, visible_ids)
        self.assertNotIn(inactive_section_offering.id, visible_ids)

    def _create_parallel_offering(self, course_code, section_code):
        course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code=course_code,
            title=course_code,
        )
        section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code=section_code,
            name=section_code,
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
            faculty_user=self.faculty,
            accepted_at=timezone.now(),
        )
        return offering

from datetime import date

from django.test import TestCase

from apps.academics.models import AcademicYear, Course, CourseOffering, Section, Term
from apps.imports.services import BulkImportService
from apps.tenants.models import Campus, Department, Program, Tenant


class CourseOfferingImportDepartmentInferenceTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="NCBA", name="NCBA")
        self.campus = Campus.objects.create(tenant=self.tenant, code="CUB", name="Cubao")
        self.department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="CUB_COLL_ISCS",
            name="Cubao IS/CS",
        )
        self.program = Program.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="BSIS",
            name="BSIS",
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
            code="IT101",
            title="Intro to IT",
        )
        self.section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="BSIS-1A",
            name="BSIS 1A",
        )
        self.runtime = {
            "tenant_ids": None,
            "campus_ids": None,
            "tenant_cache": {},
            "campus_cache": {},
            "department_cache": {},
            "program_cache": {},
            "academic_year_cache": {},
            "term_cache": {},
            "course_cache": {},
            "section_cache": {},
            "student_cache": {},
            "faculty_cache": {},
            "offering_cache": {},
        }

    def test_course_offering_department_can_be_inferred_from_course(self):
        normalized, errors, unique_key = BulkImportService._validate_course_offering_row(
            {
                "tenant_code": "NCBA",
                "campus_code": "CUB",
                "department_code": "",
                "program_code": "BSIS",
                "academic_year_code": "AY2526",
                "term_code": "1ST",
                "course_code": "IT101",
                "section_code": "BSIS-1A",
                "room": "R101",
                "schedule_text": "MWF 8:00-9:00",
                "status": "OPEN",
            },
            self.runtime,
        )

        self.assertEqual(errors, [])
        self.assertEqual(normalized["department_id"], self.department.id)
        self.assertEqual(normalized["program_id"], self.program.id)
        self.assertTrue(unique_key)

    def test_course_offering_department_inference_prefers_unique_section_over_course_parent(self):
        parent_department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="COLLEGE",
            name="College",
            unit_type=Department.UnitType.DIVISION,
        )
        self.department.parent = parent_department
        self.department.save(update_fields=["parent", "updated_at"])
        parent_owned_course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=parent_department,
            code="IT102",
            title="College-owned IT",
        )

        normalized, errors, unique_key = BulkImportService._validate_course_offering_row(
            {
                "tenant_code": "NCBA",
                "campus_code": "CUB",
                "department_code": "",
                "program_code": "BSIS",
                "academic_year_code": "AY2526",
                "term_code": "1ST",
                "course_code": parent_owned_course.code,
                "section_code": "BSIS-1A",
                "room": "R102",
                "schedule_text": "TTH 9:00-10:30",
                "status": "OPEN",
            },
            self.runtime,
        )

        self.assertEqual(errors, [])
        self.assertEqual(normalized["department_id"], self.department.id)
        self.assertEqual(normalized["program_id"], self.program.id)
        self.assertTrue(unique_key)

    def test_course_offering_import_can_stage_missing_section_for_auto_create(self):
        normalized, errors, unique_key = BulkImportService._validate_course_offering_row(
            {
                "tenant_code": "NCBA",
                "campus_code": "CUB",
                "department_code": "CUB_COLL_ISCS",
                "program_code": "BSIS",
                "academic_year_code": "AY2526",
                "term_code": "1ST",
                "course_code": "IT101",
                "section_code": "BSIS-1B",
                "room": "R103",
                "schedule_text": "MWF 10:00-11:00",
                "status": "OPEN",
            },
            self.runtime,
        )

        self.assertEqual(errors, [])
        self.assertIsNone(normalized["section_id"])
        self.assertEqual(normalized["section_payload"]["code"], "BSIS-1B")
        self.assertTrue(unique_key)

    def test_course_offering_create_auto_creates_missing_section(self):
        normalized, errors, _unique_key = BulkImportService._validate_course_offering_row(
            {
                "tenant_code": "NCBA",
                "campus_code": "CUB",
                "department_code": "CUB_COLL_ISCS",
                "program_code": "BSIS",
                "academic_year_code": "AY2526",
                "term_code": "1ST",
                "course_code": "IT101",
                "section_code": "BSIS-1B",
                "room": "R103",
                "schedule_text": "MWF 10:00-11:00",
                "status": "OPEN",
            },
            self.runtime,
        )
        self.assertEqual(errors, [])

        _entity_type, offering = BulkImportService._create_course_offering(normalized)

        created_section = Section.objects.get(code="BSIS-1B")
        self.assertEqual(offering.section_id, created_section.id)
        self.assertEqual(offering.program_id, self.program.id)
        self.assertEqual(CourseOffering.objects.count(), 1)

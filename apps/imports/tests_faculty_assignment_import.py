from datetime import date
from types import SimpleNamespace

from django.contrib.auth import authenticate, get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from apps.academics.models import (
    AcademicYear,
    Course,
    CourseOffering,
    FacultyAssignment,
    FacultyAssignmentReplacementLog,
    Section,
    Term,
)
from apps.admin_portal.forms import FacultyAssignmentForm, FacultyAssignmentReplacementForm
from apps.auditlog.models import AuditLog
from apps.imports.models import ImportBatch, ImportBatchRow
from apps.imports.services import BulkImportService, ImportTemplateService
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.tenants.models import Campus, Department, Program, Tenant


User = get_user_model()


class FacultyAssignmentImportStatusTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="FASSIGN", name="Faculty Assignment Tenant")
        self.campus = Campus.objects.create(tenant=self.tenant, code="MAIN", name="Main")
        self.department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="COLLEGE",
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
            name="Academic Year 2026-2027",
            start_date=date(2026, 6, 1),
            end_date=date(2027, 5, 31),
        )
        self.term = Term.objects.create(
            tenant=self.tenant,
            academic_year=self.academic_year,
            code="1ST",
            name="First Semester",
            sequence_no=1,
        )
        self.course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="IT101",
            title="Introduction to IT",
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
        self.faculty_role, _ = Role.objects.update_or_create(
            code="FACULTY",
            defaults={"name": "Faculty", "is_active": True},
        )
        faculty_portal_access, _ = Permission.objects.update_or_create(
            code="faculty_portal.access",
            defaults={"module": "faculty_portal", "action": "access", "is_active": True},
        )
        RolePermission.objects.get_or_create(
            role=self.faculty_role,
            permission=faculty_portal_access,
        )
        self.actor = User.objects.create_user(
            username="assignment_importer",
            email="assignment_importer@example.edu",
            password="AdminPass!123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
        )
        self.request = SimpleNamespace(
            scope={
                "tenant_id": self.tenant.id,
                "campus_id": self.campus.id,
                "tenant_ids": [self.tenant.id],
                "campus_ids": [self.campus.id],
                "department_ids": [self.department.id],
            }
        )
        self.active_faculty = self._create_faculty("active.faculty", is_active=True)
        self.inactive_faculty = self._create_faculty("inactive.faculty", is_active=False)

    def _create_faculty(self, username, *, is_active, tenant=None, campus=None, department=None):
        tenant = tenant or self.tenant
        campus = campus or self.campus
        department = department or self.department
        user = User.objects.create_user(
            username=username,
            email=f"{username}@example.edu",
            password="FacultyPass!123",
            default_tenant=tenant,
            default_campus=campus,
            default_department=department,
            is_active=is_active,
        )
        UserRole.objects.create(
            user=user,
            role=self.faculty_role,
            tenant=tenant,
            campus=campus,
            department=department,
            is_active=True,
        )
        return user

    def _row(self, faculty_username, **overrides):
        values = {
            "tenant_code": self.tenant.code,
            "campus_code": self.campus.code,
            "academic_year_code": self.academic_year.code,
            "term_code": self.term.code,
            "course_code": self.course.code,
            "section_code": self.section.code,
            "faculty_username": faculty_username,
            "is_primary": "TRUE",
        }
        values.update(overrides)
        headers = ImportTemplateService.get_headers(ImportBatch.ImportType.FACULTY_ASSIGNMENTS)
        return ",".join(str(values[header]) for header in headers)

    def _upload(self, *rows):
        headers = ImportTemplateService.get_headers(ImportBatch.ImportType.FACULTY_ASSIGNMENTS)
        content = "\n".join([",".join(headers), *rows]).encode("utf-8")
        uploaded = SimpleUploadedFile("faculty_assignments.csv", content, content_type="text/csv")
        return BulkImportService.validate_and_stage_upload(
            import_type=ImportBatch.ImportType.FACULTY_ASSIGNMENTS,
            uploaded_file=uploaded,
            user=self.actor,
            request=self.request,
        )

    def _assert_single_error(self, batch, expected_text):
        self.assertEqual((batch.valid_rows, batch.invalid_rows), (0, 1))
        row = batch.rows.get()
        self.assertEqual(row.row_status, ImportBatchRow.RowStatus.ERROR)
        self.assertIn(expected_text, " ".join(row.errors_json or []))

    def test_active_faculty_can_still_be_imported(self):
        batch = self._upload(self._row(self.active_faculty.username))

        self.assertEqual((batch.valid_rows, batch.invalid_rows), (1, 0))
        BulkImportService.confirm_batch(batch=batch, actor=self.actor)

        assignment = FacultyAssignment.objects.get(
            offering=self.offering,
            faculty_user=self.active_faculty,
        )
        self.assertEqual(assignment.tenant, self.offering.tenant)
        self.assertEqual(assignment.campus, self.offering.campus)
        self.assertTrue(assignment.is_active)
        self.assertTrue(assignment.is_primary)
        self.assertTrue(
            AuditLog.objects.filter(
                action="CREATE",
                entity_type="FacultyAssignment",
                entity_id=str(assignment.id),
            ).exists()
        )

    def test_confirmation_rejects_offering_scope_drift_without_assignment_or_success_audit(self):
        batch = self._upload(self._row(self.active_faculty.username))
        staged_row = batch.rows.get()
        self.assertEqual(
            (staged_row.normalized_data_json["tenant_id"], staged_row.normalized_data_json["campus_id"]),
            (self.tenant.id, self.campus.id),
        )

        alternate_campus = Campus.objects.create(tenant=self.tenant, code="ALTERNATE", name="Alternate")
        alternate_department = Department.objects.create(
            tenant=self.tenant,
            campus=alternate_campus,
            code="ALT-COLLEGE",
            name="Alternate College",
        )
        alternate_program = Program.objects.create(
            tenant=self.tenant,
            campus=alternate_campus,
            department=alternate_department,
            code="BSALT",
            name="Bachelor of Alternate Studies",
        )
        alternate_course = Course.objects.create(
            tenant=self.tenant,
            campus=alternate_campus,
            department=alternate_department,
            code="ALT101",
            title="Introduction to Alternate Studies",
        )
        alternate_section = Section.objects.create(
            tenant=self.tenant,
            campus=alternate_campus,
            department=alternate_department,
            program=alternate_program,
            code="BSALT-1A",
            name="BSALT 1A",
        )
        self.offering.campus = alternate_campus
        self.offering.department = alternate_department
        self.offering.program = alternate_program
        self.offering.course = alternate_course
        self.offering.section = alternate_section
        self.offering.save(
            update_fields=["campus", "department", "program", "course", "section", "updated_at"]
        )

        confirmed = BulkImportService.confirm_batch(batch=batch, actor=self.actor)

        staged_row.refresh_from_db()
        self.assertEqual(confirmed.status, ImportBatch.Status.CONFIRM_FAILED)
        self.assertEqual((confirmed.imported_rows, confirmed.invalid_rows), (0, 1))
        self.assertEqual(staged_row.row_status, ImportBatchRow.RowStatus.ERROR)
        self.assertIn("Offering scope changed after preview", " ".join(staged_row.errors_json or []))
        self.assertFalse(
            FacultyAssignment.objects.filter(
                offering=self.offering,
                faculty_user=self.active_faculty,
            ).exists()
        )
        self.assertFalse(
            AuditLog.objects.filter(
                action="CREATE",
                entity_type="FacultyAssignment",
            ).exists()
        )

    def test_inactive_faculty_can_be_imported_without_activation(self):
        batch = self._upload(self._row(self.inactive_faculty.username))

        self.assertEqual((batch.valid_rows, batch.invalid_rows), (1, 0))
        confirmed = BulkImportService.confirm_batch(batch=batch, actor=self.actor)

        assignment = FacultyAssignment.objects.get(
            offering=self.offering,
            faculty_user=self.inactive_faculty,
        )
        self.assertEqual(confirmed.status, ImportBatch.Status.CONFIRMED)
        self.assertEqual(confirmed.imported_rows, 1)
        self.assertTrue(assignment.is_active)
        self.inactive_faculty.refresh_from_db()
        self.assertFalse(self.inactive_faculty.is_active)

    def test_imported_inactive_faculty_remains_unable_to_login_or_open_faculty_dashboard(self):
        batch = self._upload(self._row(self.inactive_faculty.username))
        BulkImportService.confirm_batch(batch=batch, actor=self.actor)

        self.assertIsNone(
            authenticate(
                username=self.inactive_faculty.username,
                password="FacultyPass!123",
            )
        )
        self.assertFalse(
            self.client.login(
                username=self.inactive_faculty.username,
                password="FacultyPass!123",
            )
        )
        self.client.force_login(self.inactive_faculty)
        response = self.client.get(reverse("faculty_portal:dashboard"))
        self.assertRedirects(
            response,
            reverse("faculty_portal:public_index"),
            fetch_redirect_response=False,
        )

    def test_nonexistent_faculty_identifier_is_rejected(self):
        batch = self._upload(self._row("missing.faculty"))

        self._assert_single_error(batch, "does not match any username/email")

    def test_faculty_outside_selected_campus_scope_is_rejected(self):
        other_campus = Campus.objects.create(tenant=self.tenant, code="OTHER", name="Other")
        other_department = Department.objects.create(
            tenant=self.tenant,
            campus=other_campus,
            code="OTHER-COLLEGE",
            name="Other College",
        )
        outside_faculty = self._create_faculty(
            "outside.faculty",
            is_active=False,
            campus=other_campus,
            department=other_department,
        )

        batch = self._upload(self._row(outside_faculty.username))

        self._assert_single_error(batch, "is not an active faculty for the selected scope")

    def test_duplicate_faculty_assignment_is_still_rejected(self):
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            faculty_user=self.inactive_faculty,
            is_active=True,
        )

        batch = self._upload(self._row(self.inactive_faculty.username))

        self._assert_single_error(batch, "Faculty assignment already exists")

    def test_invalid_academic_year_is_still_rejected(self):
        batch = self._upload(
            self._row(self.inactive_faculty.username, academic_year_code="MISSING-AY")
        )

        self._assert_single_error(batch, "academic_year_code 'MISSING-AY' not found")

    def test_invalid_term_is_still_rejected(self):
        batch = self._upload(self._row(self.inactive_faculty.username, term_code="MISSING-TERM"))

        self._assert_single_error(batch, "term_code 'MISSING-TERM' not found")

    def test_invalid_course_offering_is_still_rejected(self):
        batch = self._upload(
            self._row(self.inactive_faculty.username, section_code="MISSING-SECTION")
        )

        self._assert_single_error(batch, "No course offering matches")

    def test_inactive_or_missing_faculty_role_is_still_rejected(self):
        role_assignment = self.inactive_faculty.user_roles.get(role=self.faculty_role)
        role_assignment.is_active = False
        role_assignment.save(update_fields=["is_active"])

        batch = self._upload(self._row(self.inactive_faculty.username))

        self._assert_single_error(batch, "is not an active faculty for the selected scope")

    def test_manual_faculty_assignment_form_still_rejects_inactive_faculty(self):
        form = FacultyAssignmentForm(
            data={
                "offering": self.offering.id,
                "faculty_user": self.inactive_faculty.id,
                "is_primary": "on",
                "is_active": "on",
            },
            offering_queryset=CourseOffering.objects.all(),
            faculty_queryset=User.objects.all(),
        )

        self.assertFalse(form.is_valid())
        self.assertIn("faculty_user", form.errors)
        self.assertFalse(form.fields["faculty_user"].queryset.filter(id=self.inactive_faculty.id).exists())

    def test_manual_faculty_replacement_still_rejects_inactive_faculty(self):
        current_assignment = FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            faculty_user=self.active_faculty,
            is_active=True,
        )
        assignment_queryset = FacultyAssignment.objects.filter(id=current_assignment.id)
        active_faculty_queryset = User.objects.filter(is_active=True)
        form = FacultyAssignmentReplacementForm(
            data={
                "assignment_ids": [str(current_assignment.id)],
                "replacement_faculty": self.inactive_faculty.id,
                "replacement_type": FacultyAssignmentReplacementLog.ReplacementType.PERMANENT,
                "reason_category": FacultyAssignmentReplacementLog.ReasonCategory.RESIGNATION,
                "remarks": "Approved replacement test",
            },
            assignment_queryset=assignment_queryset,
            faculty_queryset=active_faculty_queryset,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("replacement_faculty", form.errors)
        self.assertFalse(
            form.fields["replacement_faculty"].queryset.filter(id=self.inactive_faculty.id).exists()
        )

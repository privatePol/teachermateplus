from datetime import date
from decimal import Decimal

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.academics.models import AcademicYear, Course, CourseOffering, Section, Term
from apps.attendance.models import AttendanceRecord, AttendanceSession
from apps.admin_portal.forms import CourseTemplateAssignmentForm
from apps.grading.admin import CourseTemplateAssignmentAdminForm
from apps.grading.models import (
    CourseTemplateAssignment,
    GradeCorrectionRequest,
    GradeActivity,
    GradeSubmissionReopenRequest,
    GradingPeriodLock,
    GradeSubmission,
    GradingTemplate,
    GradingTemplateComponent,
    GradingTemplatePeriod,
    StudentActivityScore,
    StudentFinalGrade,
    StudentPeriodGrade,
)
from apps.grading.services import CourseTemplateAssignmentSafetyService
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.students.models import Student
from apps.tenants.models import Campus, Department, Program, Tenant


UNSET = object()


class CourseTemplateAssignmentSafetyTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="NCBA", name="NCBA")
        self.campus = Campus.objects.create(tenant=self.tenant, code="NCBA-01", name="NCBA Cubao")
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
            code="BSIS",
            name="Information Systems",
        )
        self.academic_year = AcademicYear.objects.create(
            tenant=self.tenant,
            code="2025-2026",
            name="2025-2026",
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
            code="A132",
            title="IT Application Tools in Business",
        )
        self.other_course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="A221",
            title="Accounting",
        )
        self.section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="BSIS-1A",
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
            status=CourseOffering.Status.OPEN,
        )
        self.template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="OLD",
            name="Old Template",
            is_published=True,
            is_active=True,
        )
        self.other_template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="NEW",
            name="New Template",
            is_published=True,
            is_active=True,
        )
        self.period = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
            weight_percentage=Decimal("100.00"),
        )
        self.component = GradingTemplateComponent.objects.create(
            template_period=self.period,
            code="CS",
            name="Class Standing",
            weight_percentage=Decimal("100.00"),
            sort_order=1,
        )
        self.assignment = CourseTemplateAssignment.objects.create(
            course=self.course,
            grading_template=self.template,
            effective_from_term=self.term,
            is_active=True,
        )
        self.student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="2025-0001",
            last_name="Student",
            first_name="Test",
        )
        self.user = User.objects.create_user(
            username="assignment_admin",
            email="assignment_admin@example.com",
            password="testpass123",
            first_name="Assignment",
            last_name="Admin",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        role = Role.objects.create(code="CAMPUS_ADMIN", name="Campus Admin")
        for code, module, action in [
            ("admin_portal.access", "admin_portal", "access"),
            ("course_template_assignments.read", "course_template_assignments", "read"),
            ("course_template_assignments.update", "course_template_assignments", "update"),
            ("course_template_assignments.create", "course_template_assignments", "create"),
        ]:
            permission = Permission.objects.create(code=code, module=module, action=action)
            RolePermission.objects.create(role=role, permission=permission)
        UserRole.objects.create(
            user=self.user,
            role=role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )

    def _form_data(self, *, assignment=None, template=UNSET, course=None, term=None, is_active=True):
        assignment = assignment or self.assignment
        template_obj = assignment.grading_template if template is UNSET else template
        return {
            "course": (course or assignment.course).id,
            "grading_template": template_obj.id if template_obj else "",
            "effective_from_term": (term if term is not None else assignment.effective_from_term).id,
            "is_active": "on" if is_active else "",
        }

    def _assignment_form(self, *, assignment=None, template=UNSET, course=None, term=None, is_active=True):
        assignment = assignment or self.assignment
        return CourseTemplateAssignmentForm(
            data=self._form_data(
                assignment=assignment,
                template=template,
                course=course,
                term=term,
                is_active=is_active,
            ),
            instance=assignment,
            course_queryset=Course.objects.all(),
            template_queryset=GradingTemplate.objects.all(),
            term_queryset=Term.objects.all(),
        )

    def _create_activity(self):
        return GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.period,
            template_component=self.component,
            title="Quiz 1",
            total_score=Decimal("20.00"),
            created_by_user=self.user,
        )

    def _make_exact_override_scenario(self, suffix):
        course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code=f"OVR{suffix}",
            title=f"Override Course {suffix}",
        )
        offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=self.academic_year,
            term=self.term,
            course=course,
            section=self.section,
            status=CourseOffering.Status.OPEN,
        )
        CourseTemplateAssignment.objects.create(
            course=course,
            grading_template=self.template,
            effective_from_term=None,
            is_active=True,
        )
        return course, offering

    def _exact_override_form(self, *, course, template=None, term=None, is_active=True):
        return CourseTemplateAssignmentForm(
            data={
                "course": course.id,
                "grading_template": (template or self.other_template).id,
                "effective_from_term": (term or self.term).id,
                "is_active": "on" if is_active else "",
            },
            instance=CourseTemplateAssignment(),
            course_queryset=Course.objects.all(),
            template_queryset=GradingTemplate.objects.all(),
            term_queryset=Term.objects.all(),
        )

    def _assert_exact_override_blocked(self, suffix, dependency_factory):
        course, offering = self._make_exact_override_scenario(suffix)
        dependency_factory(offering)

        form = self._exact_override_form(course=course)

        self.assertFalse(form.is_valid())
        self.assertIn("exact-term grading template assignment cannot be created", str(form.errors))

    def _assert_template_change_blocked(self):
        form = self._assignment_form(template=self.other_template)
        self.assertFalse(form.is_valid())
        self.assertIn("cannot be replaced because it is already in use", str(form.errors))
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.grading_template_id, self.template.id)

    def test_creating_new_unused_assignment_is_allowed(self):
        new_assignment = CourseTemplateAssignment()
        form = CourseTemplateAssignmentForm(
            data={
                "course": self.other_course.id,
                "grading_template": self.other_template.id,
                "effective_from_term": self.term.id,
                "is_active": "on",
            },
            instance=new_assignment,
            course_queryset=Course.objects.all(),
            template_queryset=GradingTemplate.objects.all(),
            term_queryset=Term.objects.all(),
        )

        self.assertTrue(form.is_valid(), form.errors)
        row = form.save()
        self.assertEqual(row.grading_template_id, self.other_template.id)

    def test_exact_term_assignment_overrides_default_for_no_data_offering(self):
        self.assignment.effective_from_term = None
        self.assignment.save(update_fields=["effective_from_term", "updated_at"])

        form = self._exact_override_form(course=self.course)

        self.assertTrue(form.is_valid(), form.errors)
        row = form.save()
        self.assertEqual(row.grading_template_id, self.other_template.id)
        self.assertEqual(row.effective_from_term_id, self.term.id)

    def test_duplicate_active_course_effective_term_assignment_with_different_template_is_rejected(self):
        form = self._exact_override_form(course=self.course)

        self.assertFalse(form.is_valid())
        self.assertIn("already exists for this course and effective term scope", str(form.errors))

    def test_exact_term_override_blocks_when_matching_offering_has_gradebook_dependencies(self):
        def make_activity(offering):
            return GradeActivity.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                offering=offering,
                template_period=self.period,
                template_component=self.component,
                title="Existing Activity",
                total_score=Decimal("10.00"),
                created_by_user=self.user,
            )

        dependency_factories = {
            "activity": make_activity,
            "score": lambda offering: StudentActivityScore.objects.create(
                activity=make_activity(offering),
                student=self.student,
                raw_score=Decimal("5.00"),
                computed_score=Decimal("75.00"),
                encoded_by_user=self.user,
            ),
            "period_grade": lambda offering: StudentPeriodGrade.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                offering=offering,
                template_period=self.period,
                student=self.student,
                class_standing_grade=Decimal("85.00"),
                period_grade=Decimal("85.00"),
                computed_by_user=self.user,
            ),
            "final_grade": lambda offering: StudentFinalGrade.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                offering=offering,
                student=self.student,
                final_grade=Decimal("88.00"),
                remarks="PASSED",
                computed_by_user=self.user,
            ),
            "submission": lambda offering: GradeSubmission.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                offering=offering,
                template_period=self.period,
                status=GradeSubmission.Status.SUBMITTED,
                submitted_by_user=self.user,
                submitted_at=timezone.now(),
            ),
            "correction": lambda offering: GradeCorrectionRequest.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                offering=offering,
                template_period=self.period,
                requested_by_user=self.user,
                initiated_by_user=self.user,
                faculty_department=self.department,
                status=GradeCorrectionRequest.Status.PENDING,
                justification="Correction request under current template.",
            ),
            "reopen_request": lambda offering: GradeSubmissionReopenRequest.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                offering=offering,
                template_period=self.period,
                submission=GradeSubmission.objects.create(
                    tenant=self.tenant,
                    campus=self.campus,
                    offering=offering,
                    template_period=self.period,
                    status=GradeSubmission.Status.SUBMITTED,
                    submitted_by_user=self.user,
                    submitted_at=timezone.now(),
                ),
                requested_by_user=self.user,
                status=GradeSubmissionReopenRequest.Status.PENDING,
                justification="Need reopen.",
            ),
            "period_lock": lambda offering: GradingPeriodLock.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                academic_year=self.academic_year,
                term=self.term,
                period_code=self.period.code,
                scope_type=GradingPeriodLock.ScopeType.COURSE,
                course_offering=offering,
                is_locked=True,
                locked_by_user=self.user,
                locked_at=timezone.now(),
            ),
            "attendance": lambda offering: AttendanceRecord.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                session=AttendanceSession.objects.create(
                    tenant=self.tenant,
                    campus=self.campus,
                    offering=offering,
                    template_period=self.period,
                    session_date=date(2025, 11, 15),
                    title="Attendance",
                    created_by_user=self.user,
                ),
                student=self.student,
                status_code=AttendanceRecord.Status.PRESENT,
                recorded_by_user=self.user,
            ),
        }

        for suffix, factory in dependency_factories.items():
            with self.subTest(dependency=suffix):
                self._assert_exact_override_blocked(suffix.upper(), factory)

    def test_editing_unused_assignment_to_another_template_is_allowed(self):
        form = self._assignment_form(template=self.other_template)

        self.assertTrue(form.is_valid(), form.errors)
        row = form.save()
        self.assertEqual(row.grading_template_id, self.other_template.id)

    def test_editing_unused_assignment_can_clear_template(self):
        form = self._assignment_form(template=None)

        self.assertTrue(form.is_valid(), form.errors)
        row = form.save()

        self.assertIsNone(row.grading_template_id)
        self.assertTrue(GradingTemplate.objects.filter(id=self.template.id).exists())
        self.assertTrue(Course.objects.filter(id=self.course.id).exists())

    def test_editing_in_use_assignment_without_changing_template_is_allowed(self):
        self._create_activity()
        form = self._assignment_form(is_active=False)

        self.assertTrue(form.is_valid(), form.errors)
        row = form.save()
        self.assertEqual(row.grading_template_id, self.template.id)
        self.assertFalse(row.is_active)

    def test_grade_activity_records_block_template_replacement(self):
        self._create_activity()

        self._assert_template_change_blocked()

    def test_grade_activity_records_block_template_clear(self):
        self._create_activity()
        form = self._assignment_form(template=None)

        self.assertFalse(form.is_valid())
        self.assertIn("already in use and cannot be cleared", str(form.errors))
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.grading_template_id, self.template.id)

    def test_duplicate_active_scope_blocks_clearing_to_blank_assignment(self):
        other_assignment = CourseTemplateAssignment.objects.create(
            course=self.course,
            grading_template=self.other_template,
            effective_from_term=self.term,
            is_active=True,
        )
        form = self._assignment_form(assignment=other_assignment, template=None)

        self.assertFalse(form.is_valid())
        self.assertIn("already exists for this course and effective term scope", str(form.errors))
        other_assignment.refresh_from_db()
        self.assertEqual(other_assignment.grading_template_id, self.other_template.id)

    def test_student_activity_score_records_block_template_replacement(self):
        activity = self._create_activity()
        StudentActivityScore.objects.create(
            activity=activity,
            student=self.student,
            raw_score=Decimal("0.00"),
            computed_score=Decimal("50.00"),
            encoded_by_user=self.user,
        )

        self._assert_template_change_blocked()

    def test_student_period_grade_records_block_template_replacement(self):
        StudentPeriodGrade.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.period,
            student=self.student,
            class_standing_grade=Decimal("85.00"),
            period_grade=Decimal("85.00"),
            computed_by_user=self.user,
        )

        self._assert_template_change_blocked()

    def test_student_final_grade_records_block_template_replacement(self):
        StudentFinalGrade.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            student=self.student,
            final_grade=Decimal("88.00"),
            remarks="PASSED",
            computed_by_user=self.user,
        )

        self._assert_template_change_blocked()

    def test_grade_submission_records_block_template_replacement(self):
        GradeSubmission.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.period,
            status=GradeSubmission.Status.SUBMITTED,
            submitted_by_user=self.user,
            submitted_at=timezone.now(),
        )

        self._assert_template_change_blocked()

    def test_grade_correction_request_records_block_template_replacement(self):
        GradeCorrectionRequest.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.period,
            requested_by_user=self.user,
            initiated_by_user=self.user,
            faculty_department=self.department,
            status=GradeCorrectionRequest.Status.PENDING,
            justification="Correction request under current template.",
        )

        self._assert_template_change_blocked()

    def test_course_offering_period_lock_records_block_template_replacement(self):
        GradingPeriodLock.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            period_code=self.period.code,
            scope_type=GradingPeriodLock.ScopeType.COURSE,
            course_offering=self.offering,
            is_locked=True,
            locked_by_user=self.user,
            locked_at=timezone.now(),
        )

        self._assert_template_change_blocked()

    def test_usage_summary_returns_counts_for_edit_warning(self):
        activity = self._create_activity()
        StudentActivityScore.objects.create(
            activity=activity,
            student=self.student,
            raw_score=Decimal("10.00"),
            computed_score=Decimal("75.00"),
            encoded_by_user=self.user,
        )
        GradeSubmission.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.period,
            status=GradeSubmission.Status.SUBMITTED,
            submitted_by_user=self.user,
            submitted_at=timezone.now(),
        )

        summary = CourseTemplateAssignmentSafetyService.get_usage_summary(self.assignment)

        self.assertTrue(summary["is_in_use"])
        self.assertEqual(summary["activities_count"], 1)
        self.assertEqual(summary["scores_count"], 1)
        self.assertEqual(summary["submissions_count"], 1)

    def test_admin_portal_edit_page_shows_in_use_warning(self):
        self._create_activity()
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("admin_portal:course_template_assignment_update", args=[self.assignment.id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "In use assignment")
        self.assertContains(response, "Template replacement is disabled")
        for label in [
            "Offerings:",
            "Activities:",
            "Scores:",
            "Period grades:",
            "Submissions:",
            "Final grades:",
            "Corrections:",
            "Period locks:",
        ]:
            self.assertContains(response, label)
        self.assertContains(response, "Activities:</strong> 1", html=False)

    def test_admin_portal_post_blocks_in_use_template_change_without_deleting_records(self):
        activity = self._create_activity()
        score = StudentActivityScore.objects.create(
            activity=activity,
            student=self.student,
            raw_score=Decimal("0.00"),
            computed_score=Decimal("50.00"),
            encoded_by_user=self.user,
        )
        before_counts = {
            "activities": GradeActivity.objects.count(),
            "scores": StudentActivityScore.objects.count(),
        }
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("admin_portal:course_template_assignment_update", args=[self.assignment.id]),
            self._form_data(template=self.other_template),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "cannot be replaced because it is already in use")
        self.assignment.refresh_from_db()
        score.refresh_from_db()
        self.assertEqual(self.assignment.grading_template_id, self.template.id)
        self.assertEqual(score.raw_score, Decimal("0.00"))
        self.assertEqual(GradeActivity.objects.count(), before_counts["activities"])
        self.assertEqual(StudentActivityScore.objects.count(), before_counts["scores"])

    def test_admin_portal_post_clears_unused_template_assignment_and_redirects(self):
        next_url = f"{reverse('admin_portal:course_template_assignment_list')}?without_template=1"
        before_counts = {
            "templates": GradingTemplate.objects.count(),
            "courses": Course.objects.count(),
            "activities": GradeActivity.objects.count(),
            "scores": StudentActivityScore.objects.count(),
            "submissions": GradeSubmission.objects.count(),
        }
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("admin_portal:course_template_assignment_update", args=[self.assignment.id]),
            {
                **self._form_data(template=None),
                "next": next_url,
            },
        )

        self.assertRedirects(response, next_url, fetch_redirect_response=False)
        self.assignment.refresh_from_db()
        self.assertIsNone(self.assignment.grading_template_id)
        self.assertEqual(GradingTemplate.objects.count(), before_counts["templates"])
        self.assertEqual(Course.objects.count(), before_counts["courses"])
        self.assertEqual(GradeActivity.objects.count(), before_counts["activities"])
        self.assertEqual(StudentActivityScore.objects.count(), before_counts["scores"])
        self.assertEqual(GradeSubmission.objects.count(), before_counts["submissions"])

    def test_admin_portal_post_blocks_in_use_template_clear_without_deleting_records(self):
        activity = self._create_activity()
        score = StudentActivityScore.objects.create(
            activity=activity,
            student=self.student,
            raw_score=Decimal("0.00"),
            computed_score=Decimal("50.00"),
            encoded_by_user=self.user,
        )
        before_counts = {
            "activities": GradeActivity.objects.count(),
            "scores": StudentActivityScore.objects.count(),
        }
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("admin_portal:course_template_assignment_update", args=[self.assignment.id]),
            self._form_data(template=None),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already in use and cannot be cleared")
        self.assignment.refresh_from_db()
        score.refresh_from_db()
        self.assertEqual(self.assignment.grading_template_id, self.template.id)
        self.assertEqual(score.raw_score, Decimal("0.00"))
        self.assertEqual(GradeActivity.objects.count(), before_counts["activities"])
        self.assertEqual(StudentActivityScore.objects.count(), before_counts["scores"])

    def test_cleared_assignment_remains_visible_and_editable_in_assignment_list(self):
        self.assignment.grading_template = None
        self.assignment.save(update_fields=["grading_template", "updated_at"])
        self.client.force_login(self.user)

        response = self.client.get(reverse("admin_portal:course_template_assignment_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No grading template assigned")
        self.assertContains(
            response,
            reverse("admin_portal:course_template_assignment_update", args=[self.assignment.id]),
        )

    def test_django_admin_form_blocks_in_use_template_replacement(self):
        self._create_activity()
        form = CourseTemplateAssignmentAdminForm(
            data=self._form_data(template=self.other_template),
            instance=self.assignment,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("cannot be replaced because it is already in use", str(form.errors))

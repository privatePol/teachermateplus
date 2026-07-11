from datetime import date
from decimal import Decimal

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.academics.models import AcademicYear, Course, CourseOffering, FacultyAssignment, Section, Term
from apps.grading.models import (
    CourseTemplateAssignment,
    DetailComputationMode,
    GradeActivity,
    GradeEncodingControl,
    GradingPeriodLock,
    GradingTemplate,
    GradingTemplateComponent,
    GradingTemplateDetail,
    GradingTemplatePeriod,
    GradingTemplateSubcomponent,
)
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.tenants.models import Campus, Department, Program, Tenant


class FacultyActivityCreationSelectionTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="SEL", name="Selection Tenant")
        self.campus = Campus.objects.create(tenant=self.tenant, code="MAIN", name="Main")
        self.department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="CCS",
            name="Computer Studies",
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
        self.course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="IT101",
            title="IT Fundamentals",
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
        self.faculty = self._create_faculty("faculty-selection")
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            faculty_user=self.faculty,
            is_primary=True,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_by=self.faculty,
            accepted_at=timezone.now(),
        )

        self.template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="SEL-TPL",
            name="Selection Template",
            is_published=True,
            approval_status=GradingTemplate.ApprovalStatus.APPROVED,
            default_base_value=Decimal("50.00"),
        )
        self.prelim = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
            weight_percentage=Decimal("50.00"),
        )
        self.midterm = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="MIDTERM",
            name="Midterm",
            sequence_no=2,
            weight_percentage=Decimal("50.00"),
        )
        self.exam = GradingTemplateComponent.objects.create(
            template_period=self.prelim,
            code="EXAM",
            name="Prelim Exam",
            weight_percentage=Decimal("40.00"),
            sort_order=1,
            is_exam_component=True,
        )
        self.class_standing = GradingTemplateComponent.objects.create(
            template_period=self.prelim,
            code="CS",
            name="Class Standing",
            weight_percentage=Decimal("60.00"),
            sort_order=2,
        )
        self.quizzes = GradingTemplateSubcomponent.objects.create(
            template_component=self.class_standing,
            code="QUIZ",
            name="Quizzes",
            weight_percentage=Decimal("50.00"),
            sort_order=1,
        )
        self.participation = GradingTemplateSubcomponent.objects.create(
            template_component=self.class_standing,
            code="PO",
            name="Participation/Output",
            weight_percentage=Decimal("50.00"),
            sort_order=2,
            detail_computation_mode=DetailComputationMode.AVERAGE_ACTIVITIES,
        )
        self.recitation = GradingTemplateDetail.objects.create(
            template_subcomponent=self.participation,
            code="REC",
            name="Recitation",
            weight_percentage=Decimal("50.00"),
            sort_order=1,
        )
        self.assignment_detail = GradingTemplateDetail.objects.create(
            template_subcomponent=self.participation,
            code="ASG",
            name="Assignment",
            weight_percentage=Decimal("50.00"),
            sort_order=2,
        )
        self.midterm_component = GradingTemplateComponent.objects.create(
            template_period=self.midterm,
            code="MID-CS",
            name="Midterm Class Standing",
            weight_percentage=Decimal("100.00"),
            sort_order=1,
        )
        CourseTemplateAssignment.objects.create(
            course=self.course,
            grading_template=self.template,
            effective_from_term=self.term,
            is_active=True,
        )
        self.client.force_login(self.faculty)

    def _create_faculty(self, username):
        faculty = User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        role, _ = Role.objects.get_or_create(code="FACULTY", defaults={"name": "Faculty"})
        access, _ = Permission.objects.get_or_create(
            code="faculty_portal.access",
            defaults={"module": "faculty_portal", "action": "access"},
        )
        dashboard, _ = Permission.objects.get_or_create(
            code="dashboard.read",
            defaults={"module": "dashboard", "action": "read"},
        )
        RolePermission.objects.get_or_create(role=role, permission=access)
        RolePermission.objects.get_or_create(role=role, permission=dashboard)
        UserRole.objects.get_or_create(user=faculty, role=role, tenant=self.tenant, campus=self.campus)
        return faculty

    def _url(self, offering=None, period=None):
        return reverse(
            "faculty_portal:period_activities",
            args=[(offering or self.offering).id, (period or self.prelim).id],
        )

    def _session_key(self, user=None, offering=None, period=None):
        return (
            f"faculty_activity_last_selection:{(user or self.faculty).id}:"
            f"{(offering or self.offering).id}:{(period or self.prelim).id}"
        )

    def _activity_payload(self, *, component, subcomponent=None, detail=None, title="Activity 1"):
        return {
            "template_component": str(component.id),
            "template_subcomponent": str(subcomponent.id) if subcomponent else "",
            "template_detail": str(detail.id) if detail else "",
            "title": title,
            "total_score": "25",
            "activity_date": "2026-07-01",
        }

    def _post_activity(self, payload, *, url=None, follow=True):
        return self.client.post(url or self._url(), payload, follow=follow)

    def _set_session_selection(self, *, component=None, subcomponent=None, detail=None, user=None, offering=None, period=None):
        session = self.client.session
        session[self._session_key(user=user, offering=offering, period=period)] = {
            "component_id": (component or self.exam).id,
            "subcomponent_id": subcomponent.id if subcomponent else None,
            "detail_id": detail.id if detail else None,
        }
        session.save()

    def _create_second_offering(self):
        section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="BSIT-1B",
            name="BSIT 1B",
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
            faculty_user=self.faculty,
            is_primary=True,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_by=self.faculty,
            accepted_at=timezone.now(),
        )
        return offering

    def test_successful_component_only_creation_retains_component_and_resets_entry_fields(self):
        response = self._post_activity(
            self._activity_payload(component=self.exam, title="Exam 1"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Activity created.")
        self.assertEqual(response.context["selected_component_id"], str(self.exam.id))
        self.assertIsNone(response.context["selected_subcomponent_id"])
        form = response.context["form"]
        self.assertEqual(form.initial["template_component"], self.exam.id)
        self.assertNotIn("title", form.initial)
        self.assertNotIn("total_score", form.initial)
        self.assertNotIn("activity_date", form.initial)
        self.assertIsNone(form["title"].value())
        self.assertIsNone(form["total_score"].value())
        self.assertIsNone(form["activity_date"].value())
        self.assertEqual(GradeActivity.objects.filter(offering=self.offering, title="Exam 1").count(), 1)

        refresh = self.client.get(self._url())

        self.assertEqual(refresh.status_code, 200)
        self.assertEqual(GradeActivity.objects.filter(offering=self.offering, title="Exam 1").count(), 1)

    def test_successful_component_subcomponent_creation_retains_both(self):
        response = self._post_activity(
            self._activity_payload(component=self.class_standing, subcomponent=self.quizzes, title="Q1"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_component_id"], str(self.class_standing.id))
        self.assertEqual(response.context["selected_subcomponent_id"], str(self.quizzes.id))
        self.assertIsNone(response.context["selected_detail_id"])
        form = response.context["form"]
        self.assertEqual(form.initial["template_component"], self.class_standing.id)
        self.assertEqual(form.initial["template_subcomponent"], self.quizzes.id)

    def test_successful_component_subcomponent_detail_creation_retains_all_three(self):
        response = self._post_activity(
            self._activity_payload(
                component=self.class_standing,
                subcomponent=self.participation,
                detail=self.recitation,
                title="R1",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_component_id"], str(self.class_standing.id))
        self.assertEqual(response.context["selected_subcomponent_id"], str(self.participation.id))
        self.assertEqual(response.context["selected_detail_id"], str(self.recitation.id))
        self.assertIn(self.participation, response.context["form"].fields["template_subcomponent"].queryset)
        self.assertIn(self.recitation, response.context["form"].fields["template_detail"].queryset)

    def test_invalid_post_keeps_submitted_values_and_does_not_replace_last_success(self):
        self._post_activity(self._activity_payload(component=self.exam, title="Exam 1"))

        response = self._post_activity(
            {
                "template_component": str(self.class_standing.id),
                "template_subcomponent": str(self.quizzes.id),
                "template_detail": "",
                "title": "Q invalid",
                "total_score": "",
                "activity_date": "2026-07-02",
            },
            follow=False,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context["form"], "total_score", "Total items is required for raw-score items.")
        self.assertEqual(response.context["selected_component_id"], str(self.class_standing.id))
        self.assertEqual(response.context["selected_subcomponent_id"], str(self.quizzes.id))
        self.assertEqual(self.client.session[self._session_key()]["component_id"], self.exam.id)

        restored = self.client.get(self._url())

        self.assertEqual(restored.context["selected_component_id"], str(self.exam.id))
        self.assertIsNone(restored.context["selected_subcomponent_id"])
        self.assertFalse(GradeActivity.objects.filter(title="Q invalid").exists())

    def test_retained_selection_is_isolated_by_offering_period_and_faculty_user(self):
        self._post_activity(
            self._activity_payload(
                component=self.class_standing,
                subcomponent=self.participation,
                detail=self.recitation,
                title="R1",
            )
        )
        second_offering = self._create_second_offering()

        offering_response = self.client.get(self._url(offering=second_offering))
        period_response = self.client.get(self._url(period=self.midterm))

        self.assertIsNone(offering_response.context["selected_component_id"])
        self.assertIsNone(period_response.context["selected_component_id"])

        other_faculty = self._create_faculty("faculty-selection-other")
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            faculty_user=other_faculty,
            is_primary=True,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_by=other_faculty,
            accepted_at=timezone.now(),
        )
        self.client.force_login(other_faculty)
        faculty_response = self.client.get(self._url())

        self.assertIsNone(faculty_response.context["selected_component_id"])

    def test_stale_inactive_component_is_cleared(self):
        self._set_session_selection(component=self.exam)
        self.exam.is_active = False
        self.exam.save(update_fields=["is_active", "updated_at"])

        response = self.client.get(self._url())

        self.assertIsNone(response.context["selected_component_id"])
        self.assertNotIn(self._session_key(), self.client.session)

    def test_stale_mismatched_subcomponent_is_cleared_while_component_remains(self):
        self._set_session_selection(component=self.class_standing, subcomponent=self.quizzes)
        self.quizzes.is_active = False
        self.quizzes.save(update_fields=["is_active", "updated_at"])

        response = self.client.get(self._url())

        self.assertEqual(response.context["selected_component_id"], str(self.class_standing.id))
        self.assertIsNone(response.context["selected_subcomponent_id"])
        self.assertEqual(self.client.session[self._session_key()]["subcomponent_id"], None)

    def test_stale_mismatched_detail_is_cleared_while_parents_remain(self):
        self._set_session_selection(
            component=self.class_standing,
            subcomponent=self.participation,
            detail=self.recitation,
        )
        self.recitation.is_active = False
        self.recitation.save(update_fields=["is_active", "updated_at"])

        response = self.client.get(self._url())

        self.assertEqual(response.context["selected_component_id"], str(self.class_standing.id))
        self.assertEqual(response.context["selected_subcomponent_id"], str(self.participation.id))
        self.assertIsNone(response.context["selected_detail_id"])
        self.assertEqual(self.client.session[self._session_key()]["detail_id"], None)

    def test_grading_period_lock_still_blocks_creation_without_saving_selection(self):
        GradingPeriodLock.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            period_code=self.prelim.code,
            scope_type=GradingPeriodLock.ScopeType.COURSE,
            course_offering=self.offering,
            is_locked=True,
            is_active=True,
        )

        response = self._post_activity(self._activity_payload(component=self.exam, title="Locked"), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This period is locked or already submitted.")
        self.assertFalse(GradeActivity.objects.filter(title="Locked").exists())
        self.assertNotIn(self._session_key(), self.client.session)

    def test_grade_encoding_control_still_blocks_creation_without_saving_selection(self):
        GradeEncodingControl.objects.create(
            tenant=self.tenant,
            academic_year=self.academic_year,
            term=self.term,
            period_code=self.prelim.code,
            campus=self.campus,
            course_offering=self.offering,
            status=GradeEncodingControl.Status.CLOSED,
            reason="Closed for verification",
            is_active=True,
        )

        response = self._post_activity(self._activity_payload(component=self.exam, title="Closed"), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Grade encoding is temporarily disabled")
        self.assertFalse(GradeActivity.objects.filter(title="Closed").exists())
        self.assertNotIn(self._session_key(), self.client.session)

    def test_unauthorized_faculty_cannot_use_another_faculty_offering(self):
        other_faculty = self._create_faculty("faculty-selection-denied")
        self.client.force_login(other_faculty)

        response = self._post_activity(self._activity_payload(component=self.exam, title="Denied"), follow=False)

        self.assertEqual(response.status_code, 404)
        self.assertFalse(GradeActivity.objects.filter(title="Denied").exists())

    def test_safe_next_query_is_preserved_and_unsafe_next_is_dropped(self):
        next_url = reverse("faculty_portal:period_summary", args=[self.offering.id, self.prelim.id])
        safe_url = f"{self._url()}?next={next_url}"

        response = self._post_activity(
            self._activity_payload(component=self.exam, title="With Next"),
            url=safe_url,
            follow=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("next=", response["Location"])

        unsafe_url = f"{self._url()}?next=https://example.com/unsafe"
        unsafe_response = self._post_activity(
            self._activity_payload(component=self.exam, title="Unsafe Next"),
            url=unsafe_url,
            follow=False,
        )

        self.assertEqual(unsafe_response.status_code, 302)
        self.assertNotIn("example.com", unsafe_response["Location"])

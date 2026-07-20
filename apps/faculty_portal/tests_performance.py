import json
from datetime import date
from decimal import Decimal
from unittest import mock

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.academics.models import AcademicYear, Course, CourseOffering, FacultyAssignment, Section, Term
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.enrollment.models import Enrollment
from apps.faculty_portal.services import FacultyPerformanceService
from apps.grading.models import (
    CourseTemplateAssignment,
    GradeActivity,
    GradeSubmission,
    GradingTemplate,
    GradingTemplateComponent,
    GradingTemplateDetail,
    GradingTemplatePeriod,
    GradingTemplateSubcomponent,
    GradingPeriodLock,
    StudentActivityScore,
    StudentPeriodGrade,
)
from apps.grading.services import FacultyGradingService
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.students.models import Student
from apps.tenants.models import Campus, Department, Program, Tenant


class FacultyPerformanceTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="PERF", name="Performance School")
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
            code="CS316",
            title="Application Development",
        )
        self.faculty = self._create_faculty("faculty.performance")
        self.other_faculty = self._create_faculty("faculty.other")
        self.template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="PERFORMANCE_TEMPLATE",
            name="Performance Template",
            is_published=True,
            approval_status=GradingTemplate.ApprovalStatus.APPROVED,
            default_base_value=50,
        )
        self.prelim = self._create_period("PRELIM", "Prelim", 1)
        self.midterm = self._create_period("MIDTERM", "Midterm", 2)
        CourseTemplateAssignment.objects.create(
            course=self.course,
            grading_template=self.template,
            effective_from_term=self.term,
        )
        self.offering = self._create_offering("CS316-A", self.faculty)
        self.parallel_offering = self._create_offering("CS316-B", self.faculty)
        self.other_offering = self._create_offering("CS316-C", self.other_faculty)
        self.students = [
            self._create_student("2026-001", "Able", "Improving"),
            self._create_student("2026-002", "Baker", "At Risk"),
            self._create_student("2026-003", "Cruz", "Stable"),
            self._create_student("2026-004", "Diaz", "Declining"),
            self._create_student("2026-005", "Evans", "Incomplete"),
        ]
        self.prelim_activities = self._create_period_activities(self.offering, self.prelim)
        self.midterm_activities = self._create_period_activities(self.offering, self.midterm, extra_output=True)
        previous_scores = [70, 80, 80, 90, 80]
        current_scores = [80, 60, 79, 85, 80]
        for student, previous, current in zip(self.students, previous_scores, current_scores):
            self._score_period(student, self.prelim_activities, previous)
            self._score_period(student, self.midterm_activities[:2], current)
            if student not in {self.students[1], self.students[4]}:
                self._score(self.midterm_activities[2], student, current)

    def _create_faculty(self, username):
        user = User.objects.create_user(
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
        for code, module, action in [
            ("faculty_portal.access", "faculty_portal", "access"),
            ("dashboard.read", "dashboard", "read"),
        ]:
            permission, _ = Permission.objects.get_or_create(
                code=code,
                defaults={"module": module, "action": action},
            )
            RolePermission.objects.get_or_create(role=role, permission=permission)
        UserRole.objects.create(
            user=user,
            role=role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )
        return user

    def _create_period(self, code, name, sequence):
        period = GradingTemplatePeriod.objects.create(
            template=self.template,
            code=code,
            name=name,
            sequence_no=sequence,
        )
        GradingTemplateComponent.objects.create(
            template_period=period,
            code=f"{code}_CS",
            name="Class Standing",
            weight_percentage=60,
            sort_order=1,
        )
        GradingTemplateComponent.objects.create(
            template_period=period,
            code=f"{code}_EXAM",
            name=f"{name} Exam",
            weight_percentage=40,
            sort_order=2,
        )
        return period

    def _create_offering(self, section_code, faculty):
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
            course=self.course,
            section=section,
        )
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=offering,
            faculty_user=faculty,
            is_primary=True,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
            accepted_by=faculty,
        )
        return offering

    def _create_student(self, student_no, last_name, first_name):
        student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no=student_no,
            last_name=last_name,
            first_name=first_name,
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            student=student,
            course_offering=self.offering,
            enrollment_status=Enrollment.Status.ACTIVE,
            encoded_by_user=self.faculty,
            encoded_via_portal=Enrollment.SourcePortal.ADMIN,
        )
        return student

    def _create_period_activities(self, offering, period, extra_output=False):
        components = list(period.components.order_by("sort_order"))
        activities = [
            GradeActivity.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                offering=offering,
                template_period=period,
                template_component=components[0],
                title=f"{period.code} Output",
                total_score=100,
                created_by_user=self.faculty,
            ),
            GradeActivity.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                offering=offering,
                template_period=period,
                template_component=components[1],
                title=f"{period.code} Exam",
                total_score=100,
                created_by_user=self.faculty,
            ),
        ]
        if extra_output:
            activities.append(
                GradeActivity.objects.create(
                    tenant=self.tenant,
                    campus=self.campus,
                    offering=offering,
                    template_period=period,
                    template_component=components[0],
                    title=f"{period.code} Required Output 2",
                    total_score=100,
                    created_by_user=self.faculty,
                )
            )
        return activities

    def _score(self, activity, student, value):
        return StudentActivityScore.objects.create(
            activity=activity,
            student=student,
            raw_score=Decimal(value),
            computed_score=Decimal(value),
            encoded_by_user=self.faculty,
        )

    def _score_period(self, student, activities, value):
        for activity in activities:
            self._score(activity, student, value)

    def test_snapshot_uses_official_computation_and_counts_attention_signals(self):
        original = FacultyGradingService.build_period_grade_detail_for_student
        with mock.patch.object(
            FacultyGradingService,
            "build_period_grade_detail_for_student",
            side_effect=original,
        ) as official_builder:
            snapshot = FacultyPerformanceService.get_class_performance_snapshot(
                self.offering,
                self.midterm,
            )

        self.assertTrue(official_builder.called)
        self.assertEqual(snapshot["at_risk_count"], 1)
        self.assertEqual(snapshot["missing_output_count"], 2)
        self.assertIsNotNone(snapshot["class_average"])
        self.assertEqual(snapshot["weakest_component"]["name"], "Class Standing")

    def test_trend_priority_keeps_failing_student_at_risk_when_outputs_are_missing(self):
        row = FacultyPerformanceService.get_student_performance_trend(
            self.students[1],
            self.offering,
            self.midterm,
        )
        self.assertEqual(row["trend_label"], FacultyPerformanceService.TREND_AT_RISK)
        self.assertEqual(row["missing_output_count"], 1)
        self.assertIn("Below passing grade", row["primary_reason"])
        self.assertIn("missing output", row["primary_reason"])

    def test_trend_reuses_configured_passing_threshold(self):
        self.template.passing_grade_threshold = Decimal("95.00")
        self.template.save(update_fields=["passing_grade_threshold"])

        row = FacultyPerformanceService.get_student_performance_trend(
            self.students[0],
            self.offering,
            self.midterm,
        )

        self.assertEqual(row["trend_label"], FacultyPerformanceService.TREND_AT_RISK)
        self.assertIn("Below passing grade", row["primary_reason"])

    def test_trend_labels_follow_approved_rules(self):
        improving = FacultyPerformanceService.get_student_performance_trend(
            self.students[0], self.offering, self.midterm
        )
        stable = FacultyPerformanceService.get_student_performance_trend(
            self.students[2], self.offering, self.midterm
        )
        declining = FacultyPerformanceService.get_student_performance_trend(
            self.students[3], self.offering, self.midterm
        )
        incomplete = FacultyPerformanceService.get_student_performance_trend(
            self.students[4], self.offering, self.midterm
        )
        no_baseline = FacultyPerformanceService.get_student_performance_trend(
            self.students[2], self.offering, self.prelim
        )
        self.assertEqual(improving["trend_label"], FacultyPerformanceService.TREND_IMPROVING)
        self.assertEqual(stable["trend_label"], FacultyPerformanceService.TREND_STABLE)
        self.assertEqual(declining["trend_label"], FacultyPerformanceService.TREND_DECLINING)
        self.assertEqual(incomplete["trend_label"], FacultyPerformanceService.TREND_INCOMPLETE)
        self.assertEqual(no_baseline["trend_label"], FacultyPerformanceService.TREND_NO_BASELINE)

    def test_class_performance_access_is_limited_to_assigned_offerings(self):
        self.client.force_login(self.faculty)
        allowed = self.client.get(
            reverse("faculty_portal:class_performance", args=[self.offering.id, self.midterm.id])
        )
        denied = self.client.get(
            reverse("faculty_portal:class_performance", args=[self.other_offering.id, self.midterm.id])
        )
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(denied.status_code, 404)

    def test_consultation_view_exposes_only_selected_student(self):
        self.client.force_login(self.faculty)
        response = self.client.get(
            reverse(
                "faculty_portal:student_performance_consultation",
                args=[self.offering.id, self.midterm.id, self.students[1].id],
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Baker At Risk")
        self.assertContains(response, "MIDTERM Required Output 2")
        self.assertNotContains(response, "Able Improving")
        self.assertNotContains(response, "Cruz Stable")

    def test_class_performance_current_grade_includes_explain_button_and_private_modal(self):
        self.client.force_login(self.faculty)
        response = self.client.get(
            reverse(
                "faculty_portal:class_performance",
                args=[self.offering.id, self.midterm.id],
            )
        )
        explain_url = reverse(
            "faculty_portal:grade_explanation",
            args=[self.offering.id, self.midterm.id, self.students[1].id, "PERIOD"],
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Explain")
        self.assertContains(response, f'data-grade-explain-url="{explain_url}"')
        self.assertContains(response, 'id="gradeExplanationPrivacyShield"')
        self.assertContains(response, 'id="gradeExplanationModal"')

    def test_class_performance_and_consultation_mask_selected_period_grade_when_release_is_restricted(self):
        SystemSettingService.set(
            FeatureSettingsService.FACULTY_OFFICIAL_PERIOD_GRADES_AFTER_SUBMISSION_KEY,
            True,
            tenant_id=self.tenant.id,
            value_type="BOOL",
            is_active=True,
        )
        self.client.force_login(self.faculty)

        performance_response = self.client.get(
            reverse("faculty_portal:class_performance", args=[self.offering.id, self.midterm.id])
        )
        explain_url = reverse(
            "faculty_portal:grade_explanation",
            args=[self.offering.id, self.midterm.id, self.students[1].id, "PERIOD"],
        )
        consultation_response = self.client.get(
            reverse(
                "faculty_portal:student_performance_consultation",
                args=[self.offering.id, self.midterm.id, self.students[1].id],
            )
        )

        self.assertContains(performance_response, "Hidden until submission")
        self.assertNotContains(performance_response, f'data-grade-explain-url="{explain_url}"')
        self.assertContains(consultation_response, "Official period-grade values and their trend are hidden until submission.")
        self.assertNotContains(consultation_response, "Period grade values")

    def test_student_trend_services_use_official_builder_for_selected_student_only(self):
        self._create_period("PREFINAL", "Pre-Final", 3)
        original = FacultyGradingService.build_period_grade_detail_for_student
        with mock.patch.object(
            FacultyGradingService,
            "build_period_grade_detail_for_student",
            side_effect=original,
        ) as official_builder:
            visualization = FacultyPerformanceService.get_student_trend_visualization(
                self.students[0],
                self.offering,
                self.midterm,
            )

        self.assertEqual(
            [row["period"] for row in visualization["period_trend"]],
            ["PRELIM", "MIDTERM"],
        )
        self.assertEqual(
            {call.kwargs["student_id"] for call in official_builder.call_args_list},
            {self.students[0].id},
        )
        self.assertGreaterEqual(official_builder.call_count, 2)
        json.dumps(visualization["period_trend"])
        json.dumps(visualization["component_trend"])

    def test_component_trend_uses_actual_template_component_and_subcomponent_names(self):
        class_standing = self.midterm.components.order_by("sort_order").first()
        subcomponent = GradingTemplateSubcomponent.objects.create(
            template_component=class_standing,
            code="MIDTERM_PROJECT_WORK",
            name="Project Work",
            weight_percentage=100,
            sort_order=1,
        )
        detail = GradingTemplateDetail.objects.create(
            template_subcomponent=subcomponent,
            code="MIDTERM_PROJECT_PRESENTATION",
            name="Project Presentation",
            weight_percentage=100,
            sort_order=1,
        )
        activity = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.midterm,
            template_component=class_standing,
            template_subcomponent=subcomponent,
            template_detail=detail,
            title="Project 1",
            total_score=100,
            created_by_user=self.faculty,
        )
        self._score(activity, self.students[0], 88)

        component_trend = FacultyPerformanceService.get_student_component_trend(
            self.students[0],
            self.offering,
            self.midterm,
        )
        current_components = component_trend[-1]["components"]

        self.assertIn("Class Standing", current_components)
        self.assertIn("Project Work", current_components)
        self.assertIn("Project Presentation", current_components)
        self.assertIn("Midterm Exam", current_components)

        self.client.force_login(self.faculty)
        response = self.client.get(
            reverse(
                "faculty_portal:student_performance_consultation",
                args=[self.offering.id, self.midterm.id, self.students[0].id],
            )
        )
        self.assertContains(response, "Project Presentation")
        self.assertContains(response, "Configured weight: 100.00%")

    def test_consultation_view_renders_inline_svg_performance_trends(self):
        self.client.force_login(self.faculty)
        response = self.client.get(
            reverse(
                "faculty_portal:student_performance_consultation",
                args=[self.offering.id, self.midterm.id, self.students[0].id],
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Performance Trend")
        self.assertContains(response, "Period Grade Trend")
        self.assertContains(response, "Component Average Trend")
        self.assertContains(response, 'class="trend-chart"')
        self.assertContains(response, 'class="component-sparkline"')
        self.assertContains(response, "Period grade values")
        self.assertContains(response, "Grading Period")
        self.assertContains(response, "Computed Grade")
        self.assertNotContains(response, "Baker At Risk")

    def test_performance_graph_is_not_rendered_on_dashboard_or_class_page(self):
        self.client.force_login(self.faculty)
        dashboard = self.client.get(reverse("faculty_portal:dashboard"))
        class_performance = self.client.get(
            reverse(
                "faculty_portal:class_performance",
                args=[self.offering.id, self.midterm.id],
            )
        )

        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(class_performance.status_code, 200)
        self.assertNotContains(dashboard, 'id="performance-trend-title"')
        self.assertNotContains(dashboard, 'class="trend-chart"')
        self.assertNotContains(class_performance, 'id="performance-trend-title"')
        self.assertNotContains(class_performance, 'class="trend-chart"')

    def test_consultation_one_period_shows_friendly_trend_message(self):
        self.client.force_login(self.faculty)
        response = self.client.get(
            reverse(
                "faculty_portal:student_performance_consultation",
                args=[self.offering.id, self.prelim.id, self.students[0].id],
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Trend graph will appear after another grading period is available.",
        )

    def test_consultation_missing_component_data_is_handled_safely(self):
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            student=self.students[0],
            course_offering=self.parallel_offering,
            enrollment_status=Enrollment.Status.ACTIVE,
            encoded_by_user=self.faculty,
            encoded_via_portal=Enrollment.SourcePortal.ADMIN,
        )
        self.client.force_login(self.faculty)
        response = self.client.get(
            reverse(
                "faculty_portal:student_performance_consultation",
                args=[self.parallel_offering.id, self.midterm.id, self.students[0].id],
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No computed grade trend is available yet.")
        self.assertContains(response, "Component trend is not available for this period.")

    def test_consultation_view_blocks_another_faculty(self):
        self.client.force_login(self.other_faculty)
        response = self.client.get(
            reverse(
                "faculty_portal:student_performance_consultation",
                args=[self.offering.id, self.midterm.id, self.students[1].id],
            )
        )
        self.assertEqual(response.status_code, 404)

    def test_class_performance_shows_friendly_no_encoded_grade_state(self):
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            student=self.students[0],
            course_offering=self.parallel_offering,
            enrollment_status=Enrollment.Status.ACTIVE,
            encoded_by_user=self.faculty,
            encoded_via_portal=Enrollment.SourcePortal.ADMIN,
        )
        self.client.force_login(self.faculty)
        response = self.client.get(
            reverse(
                "faculty_portal:class_performance",
                args=[self.parallel_offering.id, self.midterm.id],
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "No performance data available yet. Encode scores first to generate the class snapshot.",
        )
        self.assertContains(response, "No student attention list is available until scores are encoded.")
        self.assertNotContains(response, "Consultation View")

    def test_class_performance_shows_friendly_no_active_student_state(self):
        self.client.force_login(self.faculty)
        response = self.client.get(
            reverse(
                "faculty_portal:class_performance",
                args=[self.parallel_offering.id, self.midterm.id],
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No active students are available for this class.")

    def test_class_performance_shows_friendly_incomplete_setup_state(self):
        self.midterm_activities[1].is_active = False
        self.midterm_activities[1].save(update_fields=["is_active"])
        self.client.force_login(self.faculty)

        response = self.client.get(
            reverse(
                "faculty_portal:class_performance",
                args=[self.offering.id, self.midterm.id],
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "The grading setup is incomplete. Add the required activities before relying on this snapshot.",
        )

    def test_class_performance_explains_when_no_previous_period_exists(self):
        self.client.force_login(self.faculty)
        response = self.client.get(
            reverse(
                "faculty_portal:class_performance",
                args=[self.offering.id, self.prelim.id],
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "No previous grading period is available. Trend labels will appear after another grading period has data.",
        )

    def test_parallel_sections_include_only_same_faculty_course_and_term(self):
        other_course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="CS317",
            title="Different Course",
        )
        other_section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="CS317-A",
            name="CS317-A",
        )
        different_course_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=self.academic_year,
            term=self.term,
            course=other_course,
            section=other_section,
        )
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=different_course_offering,
            faculty_user=self.faculty,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
            accepted_by=self.faculty,
        )
        own_sections = FacultyPerformanceService.get_parallel_sections_for_faculty(
            self.faculty,
            self.course.code,
            self.term,
        )
        self.assertSetEqual(
            set(own_sections.values_list("id", flat=True)),
            {self.offering.id, self.parallel_offering.id},
        )
        self.assertNotIn(self.other_offering.id, own_sections.values_list("id", flat=True))
        self.assertNotIn(different_course_offering.id, own_sections.values_list("id", flat=True))

    def test_parallel_sections_exclude_same_course_from_different_term(self):
        later_term = Term.objects.create(
            tenant=self.tenant,
            academic_year=self.academic_year,
            code="2ND",
            name="Second Term",
            sequence_no=2,
            start_date=date(2026, 11, 1),
            end_date=date(2027, 3, 31),
        )
        section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="CS316-D",
            name="CS316-D",
        )
        later_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=self.academic_year,
            term=later_term,
            course=self.course,
            section=section,
        )
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=later_offering,
            faculty_user=self.faculty,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
            accepted_by=self.faculty,
        )

        own_sections = FacultyPerformanceService.get_parallel_sections_for_faculty(
            self.faculty,
            self.course.code,
            self.term,
        )

        self.assertNotIn(later_offering.id, own_sections.values_list("id", flat=True))

    def test_parallel_comparison_and_chart_data_handle_empty_sections(self):
        comparison = FacultyPerformanceService.get_parallel_section_comparison(
            self.faculty,
            self.course.code,
            self.term,
            self.midterm,
        )
        chart_data = FacultyPerformanceService.get_chart_data_for_parallel_sections(comparison)
        self.assertEqual([row["section_name"] for row in comparison], ["CS316-A", "CS316-B"])
        self.assertEqual(chart_data["labels"], ["CS316-A", "CS316-B"])
        json.dumps(chart_data)
        self.assertIn(
            "not enough encoded grade data",
            FacultyPerformanceService.get_parallel_section_interpretation(comparison).lower(),
        )
        self.assertEqual(
            FacultyPerformanceService.get_chart_data_for_parallel_sections([]),
            {
                "labels": [],
                "class_averages": [],
                "at_risk_counts": [],
                "missing_output_counts": [],
            },
        )

    def test_parallel_page_uses_approved_columns_and_one_section_message(self):
        single_course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="CS318",
            title="Single Section Course",
        )
        section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="CS318-A",
            name="CS318-A",
        )
        single_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=self.academic_year,
            term=self.term,
            course=single_course,
            section=section,
        )
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=single_offering,
            faculty_user=self.faculty,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
            accepted_by=self.faculty,
        )
        CourseTemplateAssignment.objects.create(
            course=single_course,
            grading_template=self.template,
            effective_from_term=self.term,
        )
        self.client.force_login(self.faculty)
        response = self.client.get(
            reverse("faculty_portal:parallel_section_comparison"),
            {
                "academic_term": self.term.id,
                "course_code": single_course.code,
                "period_code": self.midterm.code,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Parallel comparison requires at least two sections")
        for heading in [
            "Section",
            "Class Average",
            "At-Risk Students",
            "Missing Outputs",
            "Weakest Component",
        ]:
            self.assertContains(response, heading)
        self.assertNotContains(response, "<th class=\"text-end\">Action</th>")

    def test_parallel_interpretation_covers_lower_missing_normal_and_single_section(self):
        lower = [
            {"section_name": "A", "class_average": Decimal("88"), "missing_output_count": 0},
            {"section_name": "B", "class_average": Decimal("82"), "missing_output_count": 4},
        ]
        missing = [
            {"section_name": "A", "class_average": Decimal("88"), "missing_output_count": 0},
            {"section_name": "B", "class_average": Decimal("87"), "missing_output_count": 4},
        ]
        normal = [
            {"section_name": "A", "class_average": Decimal("88"), "missing_output_count": 1},
            {"section_name": "B", "class_average": Decimal("87"), "missing_output_count": 1},
        ]
        self.assertIn("lowest class average", FacultyPerformanceService.get_parallel_section_interpretation(lower))
        self.assertIn("more missing outputs", FacultyPerformanceService.get_parallel_section_interpretation(missing))
        self.assertIn("normal range", FacultyPerformanceService.get_parallel_section_interpretation(normal))
        self.assertIn(
            "at least two sections",
            FacultyPerformanceService.get_parallel_section_interpretation(normal[:1]),
        )

    def test_performance_pages_do_not_create_or_update_stored_grades(self):
        stored = StudentPeriodGrade.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.midterm,
            student=self.students[0],
            class_standing_grade=Decimal("80"),
            exam_grade=Decimal("80"),
            period_grade=Decimal("80"),
            computed_by_user=self.faculty,
        )
        original_updated_at = stored.updated_at
        original_score_count = StudentActivityScore.objects.count()
        self.client.force_login(self.faculty)
        self.client.get(
            reverse("faculty_portal:class_performance", args=[self.offering.id, self.midterm.id])
        )
        self.client.get(
            reverse(
                "faculty_portal:student_performance_consultation",
                args=[self.offering.id, self.midterm.id, self.students[0].id],
            )
        )
        self.client.get(reverse("faculty_portal:parallel_section_comparison"))
        stored.refresh_from_db()
        self.assertEqual(StudentPeriodGrade.objects.count(), 1)
        self.assertEqual(stored.updated_at, original_updated_at)
        self.assertEqual(StudentActivityScore.objects.count(), original_score_count)

    def test_performance_pages_do_not_change_submitted_or_locked_gradebook_state(self):
        submission = GradeSubmission.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.midterm,
            status=GradeSubmission.Status.SUBMITTED,
            submitted_by_user=self.faculty,
            submitted_at=timezone.now(),
        )
        lock = GradingPeriodLock.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            period_code=self.midterm.code,
            scope_type=GradingPeriodLock.ScopeType.COURSE,
            course_offering=self.offering,
            is_locked=True,
            locked_by_user=self.faculty,
            locked_at=timezone.now(),
        )
        submission_updated_at = submission.updated_at
        lock_updated_at = lock.updated_at
        self.client.force_login(self.faculty)

        self.client.get(
            reverse("faculty_portal:class_performance", args=[self.offering.id, self.midterm.id])
        )
        self.client.get(
            reverse("faculty_portal:student_performance_consultation", args=[
                self.offering.id,
                self.midterm.id,
                self.students[0].id,
            ])
        )

        submission.refresh_from_db()
        lock.refresh_from_db()
        self.assertEqual(submission.status, GradeSubmission.Status.SUBMITTED)
        self.assertEqual(submission.updated_at, submission_updated_at)
        self.assertTrue(lock.is_locked)
        self.assertEqual(lock.updated_at, lock_updated_at)

    def test_dashboard_removes_student_follow_up_cards_and_keeps_class_actions(self):
        self.client.force_login(self.faculty)
        response = self.client.get(reverse("faculty_portal:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Grade Encoding Status")
        self.assertContains(response, "Pending Grade Issues")
        self.assertContains(response, "Open My Classes")
        self.assertContains(response, self.course.code)
        self.assertContains(response, "Continue Encoding")
        self.assertContains(response, "View Performance")
        self.assertNotContains(response, "Students Needing Follow-up")
        self.assertNotContains(response, "Student Support")
        self.assertNotContains(response, "Priority Actions")
        for student in self.students:
            self.assertNotContains(response, student.student_no)

from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import AcademicYear, Course, CourseOffering, Section, Term
from apps.auditlog.models import AuditLog
from apps.core.services.settings import SystemSettingService
from apps.rbac.models import Permission, Role, RolePermission, UserPermission, UserRole
from apps.tenants.models import Campus, Department, Program, Tenant

from .models import (
    CourseExamConfiguration,
    CycleCourse,
    CycleCourseOffering,
    ExaminationCycle,
)


class ExemptCoursesPrintTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(code="PRINT", name="Print Tenant")
        cls.other_tenant = Tenant.objects.create(code="OTHER", name="Other Tenant")
        cls.campus_a = Campus.objects.create(
            tenant=cls.tenant, code="A", name="Campus Alpha"
        )
        cls.campus_b = Campus.objects.create(
            tenant=cls.tenant, code="B", name="Campus Beta"
        )
        cls.other_campus = Campus.objects.create(
            tenant=cls.other_tenant, code="X", name="Other Campus"
        )
        cls.department_a = Department.objects.create(
            tenant=cls.tenant,
            campus=cls.campus_a,
            code="ACC",
            name="Accountancy",
        )
        cls.department_b = Department.objects.create(
            tenant=cls.tenant,
            campus=cls.campus_b,
            code="ART",
            name="Arts",
        )
        cls.other_department = Department.objects.create(
            tenant=cls.other_tenant,
            campus=cls.other_campus,
            code="OTH",
            name="Other Department",
        )
        cls.year = AcademicYear.objects.create(
            tenant=cls.tenant,
            code="AY26",
            name="AY 2026-2027",
            start_date="2026-06-01",
            end_date="2027-05-31",
        )
        cls.term = Term.objects.create(
            tenant=cls.tenant,
            academic_year=cls.year,
            code="T1",
            name="First Term",
        )
        cls.other_year = AcademicYear.objects.create(
            tenant=cls.other_tenant,
            code="OAY",
            name="Other AY",
            start_date="2026-06-01",
            end_date="2027-05-31",
        )
        cls.other_term = Term.objects.create(
            tenant=cls.other_tenant,
            academic_year=cls.other_year,
            code="OT",
            name="Other Term",
        )
        cls.cycle = ExaminationCycle.objects.create(
            tenant=cls.tenant,
            academic_year=cls.year,
            term=cls.term,
            exam_period=ExaminationCycle.ExamPeriod.MIDTERM,
            created_by=cls._user("cycle-creator", cls.tenant, cls.campus_a),
        )
        cls.other_cycle = ExaminationCycle.objects.create(
            tenant=cls.other_tenant,
            academic_year=cls.other_year,
            term=cls.other_term,
            exam_period=ExaminationCycle.ExamPeriod.MIDTERM,
            created_by=cls._user(
                "other-cycle-creator", cls.other_tenant, cls.other_campus
            ),
        )

        permission_specs = (
            ("admin_portal.access", "admin_portal", "access"),
            ("departmental_exams.configure", "departmental_exams", "configure"),
            (
                "departmental_exams.review_generate",
                "departmental_exams",
                "review_generate",
            ),
            (
                "departmental_exams.manage_exam_generation",
                "departmental_exams",
                "manage_exam_generation",
            ),
        )
        for code, module, action in permission_specs:
            Permission.objects.get_or_create(
                code=code,
                defaults={
                    "module": module,
                    "action": action,
                    "description": code,
                    "is_active": True,
                },
            )

        cls.manager = cls._scoped_user(
            "print-manager",
            tenant=cls.tenant,
            campus=cls.campus_a,
            department=cls.department_a,
            permission_codes=(
                "admin_portal.access",
                "departmental_exams.configure",
            ),
        )
        cls.reviewer = cls._scoped_user(
            "print-reviewer",
            tenant=cls.tenant,
            campus=cls.campus_a,
            department=cls.department_a,
            permission_codes=(
                "admin_portal.access",
                "departmental_exams.review_generate",
            ),
        )
        cls.outsider = cls._scoped_user(
            "print-outsider",
            tenant=cls.tenant,
            campus=cls.campus_a,
            department=cls.department_a,
            permission_codes=("admin_portal.access",),
        )
        cls.automatic_manager = cls._scoped_user(
            "print-automatic-manager",
            tenant=cls.tenant,
            campus=cls.campus_a,
            department=cls.department_a,
            permission_codes=(
                "admin_portal.access",
                "departmental_exams.manage_exam_generation",
            ),
        )
        SystemSettingService.set(
            "FEATURE_DEPARTMENTAL_EXAM_BUILDER_ENABLED",
            True,
            tenant_id=cls.tenant.id,
            value_type="BOOL",
        )

        cls.exempt_alpha = cls._cycle_course(
            code="ACC100",
            title="Accounting Principles",
            department=cls.department_a,
            status=CycleCourse.InclusionStatus.EXEMPT,
            category=CycleCourse.ExemptionCategory.INTERNSHIP,
            reason="Approved internship-based assessment.",
            reviewer=cls.reviewer,
        )
        cls.exempt_beta = cls._cycle_course(
            code="BIO200",
            title="Business Innovation",
            department=cls.department_a,
            status=CycleCourse.InclusionStatus.EXEMPT,
            category=CycleCourse.ExemptionCategory.PERFORMANCE_BASED,
            reason="Approved performance-based assessment.",
        )
        cls.included = cls._cycle_course(
            code="CHEM300",
            title="Included Course",
            department=cls.department_a,
        )
        cls.included_configuration = CourseExamConfiguration.objects.create(
            cycle_course=cls.included,
            questions_required_per_faculty=65,
            questions_required_per_faculty_source=CourseExamConfiguration.ValueSource.OVERRIDE,
            final_item_count=60,
            final_item_count_source=CourseExamConfiguration.ValueSource.OVERRIDE,
            contribution_deadline=timezone.now() + timedelta(days=30),
            contribution_deadline_source=CourseExamConfiguration.ValueSource.OVERRIDE,
            coverage="Units 1 through 5",
            coverage_source=CourseExamConfiguration.ValueSource.OVERRIDE,
        )
        cls.wrong_scope = cls._cycle_course(
            code="ART050",
            title="Wrong Department Course",
            department=cls.department_b,
            status=CycleCourse.InclusionStatus.EXEMPT,
            category=CycleCourse.ExemptionCategory.CAPSTONE,
            reason="Approved capstone assessment pathway.",
        )
        cls.automatic_cycle = ExaminationCycle.objects.create(
            tenant=cls.tenant,
            academic_year=cls.year,
            term=cls.term,
            exam_period=ExaminationCycle.ExamPeriod.FINAL,
            processing_mode=ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION,
            created_by=cls.manager,
        )
        cls.automatic_exempt = cls._cycle_course(
            code="AUTO400",
            title="Automatic Exempt Course",
            department=cls.department_a,
            cycle=cls.automatic_cycle,
            status=CycleCourse.InclusionStatus.EXEMPT,
            category=CycleCourse.ExemptionCategory.LABORATORY_PRACTICAL,
            reason="Approved laboratory practical assessment.",
        )
        cls.automatic_cross_campus = cls._cycle_course(
            code="AUTO500",
            title="Automatic Cross Campus Course",
            department=cls.department_a,
            cycle=cls.automatic_cycle,
        )
        cls.cross_tenant = cls._cycle_course(
            code="X999",
            title="Cross Tenant Exempt Course",
            department=cls.other_department,
            cycle=cls.other_cycle,
            tenant=cls.other_tenant,
            status=CycleCourse.InclusionStatus.EXEMPT,
            category=CycleCourse.ExemptionCategory.PRACTICUM_OJT,
            reason="Approved other-tenant practicum pathway.",
        )
        cls._snapshot(cls.exempt_alpha, cls.campus_a, cls.department_a, "A1")
        cls._snapshot(cls.exempt_alpha, cls.campus_b, cls.department_b, "B1")
        cls._snapshot(cls.exempt_beta, cls.campus_a, cls.department_a, "A2")
        cls._snapshot(cls.included, cls.campus_a, cls.department_a, "A3")
        cls._snapshot(cls.wrong_scope, cls.campus_b, cls.department_b, "B2")
        cls._snapshot(cls.automatic_exempt, cls.campus_a, cls.department_a, "A4")
        cls._snapshot(
            cls.automatic_cross_campus, cls.campus_a, cls.department_a, "A5"
        )
        cls._snapshot(
            cls.automatic_cross_campus, cls.campus_b, cls.department_b, "B3"
        )

    @classmethod
    def _user(cls, username, tenant, campus):
        return get_user_model().objects.create_user(
            username=username,
            email=f"{username}@example.edu",
            password="Pass123!",
            default_tenant=tenant,
            default_campus=campus,
            privacy_consent_version=getattr(
                settings, "PRIVACY_CONSENT_VERSION", "2026-03"
            ),
            privacy_consent_at=timezone.now(),
        )

    @classmethod
    def _scoped_user(
        cls, username, *, tenant, campus, department, permission_codes
    ):
        user = cls._user(username, tenant, campus)
        role = Role.objects.create(code=username.upper(), name=username)
        for code in permission_codes:
            RolePermission.objects.create(
                role=role, permission=Permission.objects.get(code=code)
            )
        UserRole.objects.create(
            user=user,
            role=role,
            tenant=tenant,
            campus=campus,
            department=department,
        )
        return user

    @classmethod
    def _cycle_course(
        cls,
        *,
        code,
        title,
        department,
        cycle=None,
        tenant=None,
        status=CycleCourse.InclusionStatus.INCLUDED,
        category="",
        reason="",
        reviewer=None,
    ):
        tenant = tenant or cls.tenant
        course = Course.objects.create(
            tenant=tenant,
            code=code,
            title=title,
            exam_department=department,
        )
        exempt = status == CycleCourse.InclusionStatus.EXEMPT
        return CycleCourse.objects.create(
            cycle=cycle or cls.cycle,
            course=course,
            responsible_department=department,
            reviewer=reviewer,
            inclusion_status=status,
            exemption_category=category,
            exemption_reason=reason,
            exemption_changed_by=cls.manager if exempt else None,
            exemption_changed_at=timezone.now() if exempt else None,
        )

    @classmethod
    def _snapshot(cls, cycle_course, campus, department, suffix):
        program, _ = Program.objects.get_or_create(
            tenant=cls.tenant,
            campus=campus,
            department=department,
            code=f"P-{campus.code}",
            defaults={"name": f"Program {campus.code}"},
        )
        section = Section.objects.create(
            tenant=cls.tenant,
            campus=campus,
            department=department,
            program=program,
            code=f"S-{suffix}",
            name=f"Section {suffix}",
        )
        offering = CourseOffering.objects.create(
            tenant=cls.tenant,
            campus=campus,
            department=department,
            program=program,
            academic_year=cls.year,
            term=cls.term,
            course=cycle_course.course,
            section=section,
        )
        CycleCourseOffering.objects.create(
            cycle_course=cycle_course,
            offering=offering,
            campus=campus,
        )

    def setUp(self):
        self.client.force_login(self.manager)
        self.print_url = reverse("departmental_exams:exempt_courses_print")
        self.all_print_url = reverse("departmental_exams:assigned_courses_print")
        self.assigned_url = reverse(
            "departmental_exams:assigned_course_examinations"
        )

    def test_print_button_appears_for_authorized_assigned_courses_user(self):
        response = self.client.get(self.assigned_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Print Exempt Courses")
        self.assertContains(response, f'href="{self.print_url}"')
        self.assertContains(response, 'target="_blank"')

    def test_print_route_returns_successfully(self):
        response = self.client.get(self.print_url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response, "departmental_exams/admin/exempt_courses_print.html"
        )
        self.assertContains(response, "National College of Business and Arts")
        self.assertContains(response, 'onclick="window.print()"')
        self.assertContains(response, "Academic Year:")
        self.assertContains(response, "Term:")
        self.assertContains(response, "Examination:")
        self.assertContains(response, "Cycle Type:")

    def test_print_route_is_get_only(self):
        response = self.client.post(self.print_url)
        self.assertEqual(response.status_code, 405)

    def test_only_exempt_courses_appear(self):
        response = self.client.get(self.print_url)
        self.assertContains(response, self.exempt_alpha.course.code)
        self.assertContains(response, self.exempt_beta.course.code)
        self.assertEqual(response.context["total_exempt_courses"], 2)

    def test_included_courses_do_not_appear(self):
        response = self.client.get(self.print_url)
        self.assertNotContains(response, self.included.course.code)
        self.assertNotContains(response, self.included.course.title)

    def test_exempt_courses_sort_by_course_code_ascending(self):
        content = self.client.get(self.print_url).content.decode()
        self.assertLess(content.index("ACC100"), content.index("BIO200"))

    def test_exemption_category_uses_model_display(self):
        response = self.client.get(self.print_url)
        self.assertContains(response, "Internship")
        self.assertContains(response, "Performance-based")

    def test_exemption_reason_appears(self):
        response = self.client.get(self.print_url)
        self.assertContains(response, self.exempt_alpha.exemption_reason)

    def test_course_title_appears(self):
        response = self.client.get(self.print_url)
        self.assertContains(response, self.exempt_alpha.course.title)

    def test_exam_department_appears(self):
        response = self.client.get(self.print_url)
        self.assertContains(response, str(self.department_a))

    def test_distinct_campus_information_appears(self):
        response = self.client.get(self.print_url)
        self.assertContains(response, "Campus Alpha")
        self.assertContains(response, "Campus Beta")

    def test_empty_exempt_set_renders_safely(self):
        CycleCourse.objects.filter(
            pk__in=[self.exempt_alpha.pk, self.exempt_beta.pk]
        ).update(
            inclusion_status=CycleCourse.InclusionStatus.INCLUDED,
            exemption_category="",
            exemption_reason="",
            exemption_changed_by=None,
            exemption_changed_at=None,
        )
        response = self.client.get(self.print_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No exempt courses found.")
        self.assertEqual(response.context["total_exempt_courses"], 0)

    def test_cross_tenant_exempt_course_is_excluded(self):
        response = self.client.get(self.print_url)
        self.assertNotContains(response, self.cross_tenant.course.code)
        self.assertNotContains(response, self.cross_tenant.course.title)

    def test_wrong_campus_department_exempt_course_is_excluded(self):
        response = self.client.get(self.print_url)
        self.assertNotContains(response, self.wrong_scope.course.code)
        self.assertNotContains(response, self.wrong_scope.course.title)

    def test_direct_unauthorized_url_is_denied(self):
        self.client.force_login(self.outsider)
        response = self.client.get(self.print_url)
        self.assertEqual(response.status_code, 403)
        self.assertNotContains(response, self.exempt_alpha.course.code, status_code=403)

    def test_feature_off_denies_assigned_page_and_print_route(self):
        SystemSettingService.set(
            "FEATURE_DEPARTMENTAL_EXAM_BUILDER_ENABLED",
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )
        try:
            assigned_response = self.client.get(self.assigned_url)
            print_response = self.client.get(self.print_url)
            self.assertEqual(assigned_response.status_code, 403)
            self.assertEqual(print_response.status_code, 403)
            self.assertNotContains(
                assigned_response, "Print Exempt Courses", status_code=403
            )
            self.assertNotContains(
                print_response, self.exempt_alpha.course.code, status_code=403
            )
        finally:
            SystemSettingService.set(
                "FEATURE_DEPARTMENTAL_EXAM_BUILDER_ENABLED",
                True,
                tenant_id=self.tenant.id,
                value_type="BOOL",
            )

    def test_existing_assigned_courses_page_still_works(self):
        response = self.client.get(self.assigned_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Assigned Course Examinations")
        self.assertContains(response, self.exempt_alpha.course.code)
        self.assertContains(response, self.included.course.code)

    def test_opening_report_does_not_modify_exemption_state(self):
        before = tuple(
            CycleCourse.objects.filter(pk=self.exempt_alpha.pk).values_list(
                "inclusion_status",
                "exemption_category",
                "exemption_reason",
                "exemption_changed_by_id",
                "exemption_changed_at",
                "updated_at",
            )[0]
        )
        audit_count = AuditLog.objects.count()
        response = self.client.get(self.print_url)
        self.assertEqual(response.status_code, 200)
        after = tuple(
            CycleCourse.objects.filter(pk=self.exempt_alpha.pk).values_list(
                "inclusion_status",
                "exemption_category",
                "exemption_reason",
                "exemption_changed_by_id",
                "exemption_changed_at",
                "updated_at",
            )[0]
        )
        self.assertEqual(after, before)
        self.assertEqual(AuditLog.objects.count(), audit_count)

    def test_report_is_not_truncated_by_pagination(self):
        bulk_courses = [
            Course(
                tenant=self.tenant,
                code=f"BULK{number:03d}",
                title=f"Bulk Exempt Course {number:03d}",
                exam_department=self.department_a,
            )
            for number in range(55)
        ]
        Course.objects.bulk_create(bulk_courses)
        bulk_courses = list(
            Course.objects.filter(tenant=self.tenant, code__startswith="BULK")
        )
        CycleCourse.objects.bulk_create(
            [
                CycleCourse(
                    cycle=self.cycle,
                    course=course,
                    responsible_department=self.department_a,
                    inclusion_status=CycleCourse.InclusionStatus.EXEMPT,
                    exemption_category=CycleCourse.ExemptionCategory.OTHER_OUTPUT_BASED,
                    exemption_reason="Approved output-based assessment pathway.",
                    exemption_changed_by=self.manager,
                    exemption_changed_at=timezone.now(),
                )
                for course in bulk_courses
            ]
        )
        response = self.client.get(self.print_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_exempt_courses"], 57)
        self.assertContains(response, "BULK000")
        self.assertContains(response, "BULK054")

    def test_reviewer_only_access_matches_assigned_courses_contract(self):
        self.client.force_login(self.reviewer)
        response = self.client.get(self.print_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.exempt_alpha.course.code)
        self.assertNotContains(response, self.exempt_beta.course.code)
        self.assertEqual(response.context["total_exempt_courses"], 1)

    def test_automatic_exempt_access_uses_existing_participating_campus_authority(self):
        self.client.force_login(self.automatic_manager)
        response = self.client.get(self.print_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.automatic_exempt.course.code)
        self.assertContains(response, "Automatic Generation")
        self.assertNotContains(response, self.exempt_alpha.course.code)
        self.assertEqual(response.context["total_exempt_courses"], 1)

    def test_direct_deny_precedence_blocks_route_and_content(self):
        UserPermission.objects.create(
            user=self.manager,
            permission=Permission.objects.get(code="departmental_exams.configure"),
            tenant=self.tenant,
            campus=self.campus_a,
            grant_type=UserPermission.GrantType.DENY,
        )
        response = self.client.get(self.print_url)
        self.assertEqual(response.status_code, 403)
        self.assertNotContains(response, self.exempt_alpha.course.code, status_code=403)

    def test_print_all_button_appears_beside_exempt_print_for_authorized_user(self):
        response = self.client.get(self.assigned_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "PRINT ALL")
        self.assertContains(response, f'href="{self.all_print_url}"')
        self.assertContains(response, "Print Exempt Courses")

    def test_print_all_route_loads_as_get_only_printer_friendly_html(self):
        response = self.client.get(self.all_print_url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response, "departmental_exams/admin/assigned_courses_print.html"
        )
        self.assertContains(response, "National College of Business and Arts")
        self.assertContains(response, "TeacherMatePlus Departmental Exam Builder")
        self.assertContains(response, 'onclick="window.print()"')
        self.assertEqual(self.client.post(self.all_print_url).status_code, 405)

    def test_print_all_contains_every_authorized_included_and_exempt_row(self):
        response = self.client.get(self.all_print_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_rows"], 3)
        self.assertContains(response, self.exempt_alpha.course.code)
        self.assertContains(response, self.exempt_beta.course.code)
        self.assertContains(response, self.included.course.code)
        self.assertEqual(
            [course.inclusion_status for course in response.context["courses"]],
            [
                CycleCourse.InclusionStatus.EXEMPT,
                CycleCourse.InclusionStatus.EXEMPT,
                CycleCourse.InclusionStatus.INCLUDED,
            ],
        )

    def test_print_all_is_globally_sorted_and_numbered_after_sorting(self):
        response = self.client.get(self.all_print_url)
        courses = response.context["courses"]
        self.assertEqual(
            [course.course.code for course in courses],
            ["ACC100", "BIO200", "CHEM300"],
        )
        self.assertEqual(
            [course.print_number for course in courses],
            [1, 2, 3],
        )

    def test_print_all_includes_cycle_responsibility_configuration_and_readiness(self):
        response = self.client.get(self.all_print_url)
        for heading in (
            "No.",
            "Course Code",
            "Course Title",
            "Academic Year",
            "Term",
            "Examination / Cycle",
            "Exam Department",
            "Campus / Campuses",
            "Inclusion Status",
            "Effective Configuration",
            "Readiness",
        ):
            self.assertContains(response, heading)
        self.assertContains(response, str(self.department_a))
        self.assertContains(response, "Campus Alpha")
        self.assertContains(response, "Campus Beta")
        self.assertContains(response, "Faculty quota: <strong>65</strong>")
        self.assertContains(response, "Final items: <strong>60</strong>")
        self.assertContains(response, "OVERRIDE")
        self.assertContains(response, "Cycle Not Open")

    def test_print_all_omits_assigned_course_actions_and_interactive_controls(self):
        response = self.client.get(self.all_print_url)
        self.assertNotContains(response, "Administer")
        self.assertNotContains(response, "Configure Override")
        self.assertNotContains(response, "Configure Overrides")
        self.assertNotContains(response, "<a ", html=False)
        self.assertNotContains(response, "<form", html=False)
        self.assertNotContains(response, "<input", html=False)
        self.assertNotContains(response, 'type="checkbox"', html=False)

    def test_print_all_excludes_cross_tenant_course(self):
        response = self.client.get(self.all_print_url)
        self.assertNotContains(response, self.cross_tenant.course.code)
        self.assertNotContains(response, self.cross_tenant.course.title)

    def test_print_all_excludes_unauthorized_exam_department_course(self):
        response = self.client.get(self.all_print_url)
        self.assertNotContains(response, self.wrong_scope.course.code)
        self.assertNotContains(response, self.wrong_scope.course.title)

    def test_print_all_automatic_scope_excludes_course_with_uncovered_campus(self):
        self.client.force_login(self.automatic_manager)
        response = self.client.get(self.all_print_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.automatic_exempt.course.code)
        self.assertNotContains(response, self.automatic_cross_campus.course.code)
        self.assertEqual(response.context["total_rows"], 1)

    def test_print_all_reviewer_visibility_matches_assigned_courses(self):
        self.client.force_login(self.reviewer)
        assigned_response = self.client.get(self.assigned_url)
        print_response = self.client.get(self.all_print_url)
        self.assertEqual(assigned_response.status_code, 200)
        self.assertEqual(print_response.status_code, 200)
        self.assertContains(assigned_response, self.exempt_alpha.course.code)
        self.assertContains(print_response, self.exempt_alpha.course.code)
        self.assertNotContains(assigned_response, self.exempt_beta.course.code)
        self.assertNotContains(print_response, self.exempt_beta.course.code)
        self.assertEqual(print_response.context["total_rows"], 1)

    def test_print_all_direct_deny_precedence_blocks_route_and_content(self):
        UserPermission.objects.create(
            user=self.manager,
            permission=Permission.objects.get(code="departmental_exams.configure"),
            tenant=self.tenant,
            campus=self.campus_a,
            grant_type=UserPermission.GrantType.DENY,
        )
        response = self.client.get(self.all_print_url)
        self.assertEqual(response.status_code, 403)
        self.assertNotContains(response, self.exempt_alpha.course.code, status_code=403)

    def test_print_all_feature_off_protects_route_and_hides_button(self):
        SystemSettingService.set(
            "FEATURE_DEPARTMENTAL_EXAM_BUILDER_ENABLED",
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )
        try:
            assigned_response = self.client.get(self.assigned_url)
            print_response = self.client.get(self.all_print_url)
            self.assertEqual(assigned_response.status_code, 403)
            self.assertEqual(print_response.status_code, 403)
            self.assertNotContains(assigned_response, "PRINT ALL", status_code=403)
            self.assertNotContains(
                print_response, self.exempt_alpha.course.code, status_code=403
            )
        finally:
            SystemSettingService.set(
                "FEATURE_DEPARTMENTAL_EXAM_BUILDER_ENABLED",
                True,
                tenant_id=self.tenant.id,
                value_type="BOOL",
            )

    def test_print_all_unauthorized_direct_url_is_denied(self):
        self.client.force_login(self.outsider)
        response = self.client.get(self.all_print_url)
        self.assertEqual(response.status_code, 403)
        self.assertNotContains(response, self.exempt_alpha.course.code, status_code=403)

    def test_print_all_empty_authorized_scope_renders_safely(self):
        empty_tenant = Tenant.objects.create(code="EMPTY", name="Empty Tenant")
        empty_campus = Campus.objects.create(
            tenant=empty_tenant, code="EMPTY", name="Empty Campus"
        )
        empty_admin = get_user_model().objects.create_superuser(
            username="empty-print-admin",
            email="empty-print-admin@example.edu",
            password="Pass123!",
            default_tenant=empty_tenant,
            default_campus=empty_campus,
            privacy_consent_version=getattr(
                settings, "PRIVACY_CONSENT_VERSION", "2026-03"
            ),
            privacy_consent_at=timezone.now(),
        )
        SystemSettingService.set(
            "FEATURE_DEPARTMENTAL_EXAM_BUILDER_ENABLED",
            True,
            tenant_id=empty_tenant.id,
            value_type="BOOL",
        )
        self.client.force_login(empty_admin)
        response = self.client.get(self.all_print_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No assigned course examinations found.")
        self.assertEqual(response.context["total_rows"], 0)

    def test_print_all_is_not_truncated_by_pagination(self):
        bulk_courses = [
            Course(
                tenant=self.tenant,
                code=f"ALL{number:03d}",
                title=f"Print All Course {number:03d}",
                exam_department=self.department_a,
            )
            for number in range(55)
        ]
        Course.objects.bulk_create(bulk_courses)
        bulk_courses = list(
            Course.objects.filter(tenant=self.tenant, code__startswith="ALL")
        )
        CycleCourse.objects.bulk_create(
            [
                CycleCourse(
                    cycle=self.cycle,
                    course=course,
                    responsible_department=self.department_a,
                )
                for course in bulk_courses
            ]
        )
        response = self.client.get(self.all_print_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_rows"], 58)
        self.assertContains(response, "ALL000")
        self.assertContains(response, "ALL054")
        self.assertEqual(
            [course.print_number for course in response.context["courses"]],
            list(range(1, 59)),
        )

    def test_print_all_preserves_existing_exempt_report_behavior(self):
        all_response = self.client.get(self.all_print_url)
        exempt_response = self.client.get(self.print_url)
        self.assertEqual(all_response.status_code, 200)
        self.assertEqual(exempt_response.status_code, 200)
        self.assertEqual(all_response.context["total_rows"], 3)
        self.assertEqual(exempt_response.context["total_exempt_courses"], 2)
        self.assertContains(exempt_response, self.exempt_alpha.course.code)
        self.assertNotContains(exempt_response, self.included.course.code)

    def test_print_all_performs_zero_course_configuration_or_audit_writes(self):
        course_state = list(
            CycleCourse.objects.order_by("pk").values_list(
                "pk",
                "inclusion_status",
                "exemption_category",
                "exemption_reason",
                "reviewer_id",
                "responsible_department_id",
                "updated_at",
            )
        )
        configuration_state = list(
            CourseExamConfiguration.objects.order_by("pk").values_list(
                "pk",
                "questions_required_per_faculty",
                "final_item_count",
                "contribution_deadline",
                "coverage",
                "revision",
                "updated_at",
            )
        )
        audit_count = AuditLog.objects.count()
        response = self.client.get(self.all_print_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(
                CycleCourse.objects.order_by("pk").values_list(
                    "pk",
                    "inclusion_status",
                    "exemption_category",
                    "exemption_reason",
                    "reviewer_id",
                    "responsible_department_id",
                    "updated_at",
                )
            ),
            course_state,
        )
        self.assertEqual(
            list(
                CourseExamConfiguration.objects.order_by("pk").values_list(
                    "pk",
                    "questions_required_per_faculty",
                    "final_item_count",
                    "contribution_deadline",
                    "coverage",
                    "revision",
                    "updated_at",
                )
            ),
            configuration_state,
        )
        self.assertEqual(AuditLog.objects.count(), audit_count)

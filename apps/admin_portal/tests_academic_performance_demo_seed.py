from datetime import date
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models import Q
from django.test import TestCase, override_settings

from apps.accounts.models import User
from apps.academics.models import AcademicYear, Course, CourseOffering, Term
from apps.academics.services import AcademicGovernanceService
from apps.admin_portal.academic_performance import AcademicPerformanceInsightService
from apps.faculty_portal.services import FacultyPerformanceService
from apps.grading.models import (
    CourseTemplateAssignment,
    GradeActivity,
    GradingTemplate,
    GradingTemplateComponent,
    GradingTemplatePeriod,
    StudentActivityScore,
)
from apps.rbac.models import Permission, Role, RolePermission
from apps.students.models import Student
from apps.tenants.models import Campus, Department, Tenant


class AcademicPerformanceInsightsDemoSeedSafetyTests(TestCase):
    @override_settings(DEBUG=False)
    def test_command_refuses_when_debug_is_false(self):
        with self.assertRaisesMessage(CommandError, "allowed only when DEBUG=True"):
            call_command(
                "seed_academic_performance_insights_demo",
                confirm_demo_data=True,
                demo_password="TestDemo123!",
            )

    @override_settings(DEBUG=True)
    def test_command_requires_confirmation(self):
        with self.assertRaisesMessage(CommandError, "--confirm-demo-data"):
            call_command(
                "seed_academic_performance_insights_demo",
                demo_password="TestDemo123!",
            )


@override_settings(DEBUG=True)
class AcademicPerformanceInsightsDemoSeedTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(code="NCBA", name="NCBA")
        for code, name in (
            ("NCBA-01", "NCBA-CUBAO"),
            ("NCBA-02", "NCBA-FAIRVIEW"),
            ("NCBA-03", "NCBA-TAYTAY"),
        ):
            campus = Campus.objects.create(tenant=cls.tenant, code=code, name=name)
            Department.objects.create(
                tenant=cls.tenant,
                campus=campus,
                code="COLLEGE",
                name="College",
            )
        for code, title in (
            ("A132-ITAPPS", "Official IT Applications"),
            ("A221-ACGN", "Official Accounting"),
        ):
            Course.objects.create(tenant=cls.tenant, code=code, title=title)
        permission_specs = (
            ("admin_portal.access", "admin_portal", "access"),
            ("grading_analytics.read", "grading_analytics", "read"),
            ("faculty_portal.access", "faculty_portal", "access"),
        )
        permissions = {}
        for code, module, action in permission_specs:
            permissions[code], _ = Permission.objects.get_or_create(
                code=code,
                defaults={"module": module, "action": action},
            )
        role_permissions = {
            "AREA_CHAIR": ("admin_portal.access", "grading_analytics.read"),
            "COLLEGE_DEAN": ("admin_portal.access", "grading_analytics.read"),
            "CAO": ("admin_portal.access", "grading_analytics.read"),
            "FACULTY": ("faculty_portal.access",),
        }
        for role_code, permission_codes in role_permissions.items():
            role, _ = Role.objects.get_or_create(
                code=role_code,
                defaults={"name": role_code},
            )
            for permission_code in permission_codes:
                RolePermission.objects.get_or_create(
                    role=role,
                    permission=permissions[permission_code],
                )

    def test_seed_is_idempotent_creates_scenarios_and_cleanup_is_scoped(self):
        output = StringIO()
        call_command(
            "seed_academic_performance_insights_demo",
            confirm_demo_data=True,
            demo_password="TestDemo123!",
            stdout=output,
        )
        first_counts = self._counts()
        call_command(
            "seed_academic_performance_insights_demo",
            confirm_demo_data=True,
            demo_password="TestDemo123!",
            stdout=StringIO(),
        )
        self.assertEqual(self._counts(), first_counts)
        self.assertEqual(first_counts["offerings"], 18)
        self.assertEqual(first_counts["students"], 138)
        self.assertEqual(first_counts["users"], 14)
        self.assertGreater(first_counts["activities"], 0)
        self.assertGreater(first_counts["scores"], 0)
        self.assertEqual(
            list(
                User.objects.filter(username__startswith="test-faculty-")
                .order_by("username")
                .values_list("username", flat=True)
            ),
            [f"test-faculty-{number:02d}" for number in range(1, 10)],
        )
        self.assertFalse(User.objects.filter(username__startswith="test-insights-fac-").exists())
        for campus_code, faculty_username in (
            ("NCBA-01", "test-faculty-07"),
            ("NCBA-02", "test-faculty-08"),
            ("NCBA-03", "test-faculty-09"),
        ):
            additional_offerings = CourseOffering.objects.filter(
                campus__code=campus_code,
                section__code__startswith="TEST-",
                section__code__endswith="-C",
                faculty_assignments__faculty_user__username=faculty_username,
                faculty_assignments__response_status="ACCEPTED",
                faculty_assignments__is_active=True,
            ).distinct()
            self.assertEqual(additional_offerings.count(), 2)
            self.assertEqual(
                set(additional_offerings.values_list("course__code", flat=True)),
                {"A132-ITAPPS", "A221-ACGN"},
            )
            for offering in additional_offerings:
                self.assertEqual(offering.enrollments.filter(is_active=True).count(), 3)
                self.assertEqual(
                    set(
                        GradeActivity.objects.filter(
                            offering=offering,
                            is_active=True,
                        ).values_list("template_period__code", flat=True)
                    ),
                    {"PRELIM", "MIDTERM", "PRE-FINAL", "FINAL"},
                )
                for activity in GradeActivity.objects.filter(
                    offering=offering,
                    is_active=True,
                ):
                    self.assertEqual(
                        activity.student_scores.filter(is_active=True).count(),
                        3,
                    )

        self.assertEqual(
            set(
                GradeActivity.objects.filter(
                    offering__section__code__startswith="TEST-"
                ).values_list("template_period__code", flat=True)
            ),
            {"PRELIM", "MIDTERM", "PRE-FINAL", "FINAL"},
        )

        cubao_normal = self._offering("NCBA-01", "A132-ITAPPS", "A")
        cubao_high_risk = self._offering("NCBA-01", "A132-ITAPPS", "B")
        fairview_missing = self._offering("NCBA-02", "A132-ITAPPS", "B")
        taytay_incomplete = self._offering("NCBA-03", "A132-ITAPPS", "B")
        self.assertEqual(self._status(cubao_normal, "MIDTERM"), "Normal")
        self.assertEqual(self._status(cubao_high_risk, "MIDTERM"), "High Risk")
        self.assertEqual(self._status(fairview_missing, "MIDTERM"), "Needs Attention")
        self.assertEqual(self._status(taytay_incomplete, "MIDTERM"), "Incomplete Data")

        zero_score = StudentActivityScore.objects.filter(
            activity__offering=fairview_missing,
            raw_score=0,
            is_active=True,
        ).first()
        self.assertIsNotNone(zero_score)
        self.assertEqual(zero_score.computed_score, Decimal("50"))
        fairview_period = self._period(fairview_missing, "MIDTERM")
        fairview_summary = AcademicPerformanceInsightService.get_section_performance_summary(
            fairview_missing,
            fairview_period,
        )
        fairview_snapshot = FacultyPerformanceService.get_class_performance_snapshot(
            fairview_missing,
            fairview_period,
        )
        fairview_coverage = fairview_summary["coverage"]
        self.assertGreater(fairview_coverage["computed_grade_count"], 0)
        self.assertLess(
            fairview_coverage["computed_grade_count"],
            fairview_coverage["active_enrollment_count"],
        )
        self.assertGreater(fairview_coverage["no_grade_count"], 0)
        self.assertEqual(
            fairview_snapshot["readiness"]["missing_template_bucket_count"],
            0,
        )
        self.assertEqual(fairview_summary["status"], "Needs Attention")

        self.assertEqual(
            self._consistency_statuses("A221-ACGN", "PRELIM"),
            {"Consistent"},
        )
        self.assertEqual(
            self._consistency_statuses("A221-ACGN", "MIDTERM"),
            {"Minor Difference"},
        )
        self.assertEqual(
            self._consistency_statuses("A221-ACGN", "PRE-FINAL"),
            {"Needs Review"},
        )
        self.assertIn(
            "Incomplete Setup",
            self._consistency_statuses("A221-ACGN", "FINAL"),
        )

        official_titles = dict(
            Course.objects.filter(code__in=("A132-ITAPPS", "A221-ACGN")).values_list(
                "code",
                "title",
            )
        )
        call_command(
            "seed_academic_performance_insights_demo",
            confirm_demo_data=True,
            remove_demo_data=True,
            stdout=StringIO(),
        )
        self.assertEqual(self._counts()["offerings"], 0)
        self.assertEqual(self._counts()["students"], 0)
        self.assertEqual(
            dict(
                Course.objects.filter(code__in=("A132-ITAPPS", "A221-ACGN")).values_list(
                    "code",
                    "title",
                )
            ),
            official_titles,
        )

    def test_seed_uses_configured_active_scope_for_faculty_portal_access(self):
        active_year = AcademicYear.objects.create(
            tenant=self.tenant,
            code="2025-2026",
            name="AY 2025-2026",
            start_date=date(2025, 6, 1),
            end_date=date(2026, 5, 31),
        )
        active_term = Term.objects.create(
            tenant=self.tenant,
            academic_year=active_year,
            code="2ND",
            name="Second Semester",
            sequence_no=2,
            start_date=date(2025, 11, 1),
            end_date=date(2026, 5, 31),
        )
        AcademicGovernanceService.set_active_scope(
            tenant_id=self.tenant.id,
            academic_year=active_year,
            term=active_term,
        )

        call_command(
            "seed_academic_performance_insights_demo",
            confirm_demo_data=True,
            demo_password="TestDemo123!",
            stdout=StringIO(),
        )

        self.assertEqual(
            CourseOffering.objects.filter(
                section__code__startswith="TEST-",
                academic_year=active_year,
                term=active_term,
            ).count(),
            18,
        )
        self.assertFalse(
            CourseOffering.objects.filter(
                section__code__startswith="TEST-",
                academic_year__code="TEST-AY-2026",
            ).exists()
        )

    def test_seed_uses_officially_resolved_template_periods_for_activities(self):
        active_year = AcademicYear.objects.create(
            tenant=self.tenant,
            code="2025-2026",
            name="AY 2025-2026",
            start_date=date(2025, 6, 1),
            end_date=date(2026, 5, 31),
        )
        active_term = Term.objects.create(
            tenant=self.tenant,
            academic_year=active_year,
            code="2ND",
            name="Second Semester",
            sequence_no=2,
            start_date=date(2025, 11, 1),
            end_date=date(2026, 5, 31),
        )
        AcademicGovernanceService.set_active_scope(
            tenant_id=self.tenant.id,
            academic_year=active_year,
            term=active_term,
        )
        operational_template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="TEST-OPERATIONAL-RESOLUTION",
            name="Operational Resolution Test",
            approval_status=GradingTemplate.ApprovalStatus.APPROVED,
            is_published=True,
            is_active=True,
        )
        for sequence_no, (code, name) in enumerate(
            (
                ("PRELIM", "Prelim"),
                ("MIDTERM", "Midterm"),
                ("PRE-FINAL", "Pre-Final"),
                ("FX", "Final"),
            ),
            start=1,
        ):
            period = GradingTemplatePeriod.objects.create(
                template=operational_template,
                code=code,
                name=name,
                sequence_no=sequence_no,
                weight_percentage=25,
            )
            GradingTemplateComponent.objects.create(
                template_period=period,
                code=f"{code}-COMPONENT",
                name="Final Exam" if code == "FX" else "Class Standing",
                weight_percentage=100,
                sort_order=1,
                is_exam_component=code == "FX",
            )
        CourseTemplateAssignment.objects.create(
            course=Course.objects.get(tenant=self.tenant, code="A132-ITAPPS"),
            grading_template=operational_template,
            effective_from_term=active_term,
        )

        call_command(
            "seed_academic_performance_insights_demo",
            confirm_demo_data=True,
            demo_password="TestDemo123!",
            stdout=StringIO(),
        )

        activities = GradeActivity.objects.filter(
            offering__course__code="A132-ITAPPS",
            offering__section__code__startswith="TEST-",
        )
        self.assertEqual(
            set(activities.values_list("template_period__template__code", flat=True)),
            {"TEST-OPERATIONAL-RESOLUTION"},
        )
        self.assertEqual(
            {
                AcademicGovernanceService.normalize_period_key(name)
                for name in activities.values_list("template_period__name", flat=True)
            },
            {"PRELIM", "MIDTERM", "PREFINAL", "FINAL"},
        )

    @staticmethod
    def _counts():
        return {
            "users": User.objects.filter(
                Q(username__startswith="test-insights-")
                | Q(username__startswith="test-faculty-")
            ).count(),
            "offerings": CourseOffering.objects.filter(
                section__code__startswith="TEST-"
            ).count(),
            "students": Student.objects.filter(student_no__startswith="TEST-").count(),
            "activities": GradeActivity.objects.filter(
                offering__section__code__startswith="TEST-"
            ).count(),
            "scores": StudentActivityScore.objects.filter(
                activity__offering__section__code__startswith="TEST-"
            ).count(),
        }

    @staticmethod
    def _offering(campus_code, course_code, section_letter):
        return CourseOffering.objects.get(
            campus__code=campus_code,
            course__code=course_code,
            section__code__endswith=f"-{section_letter}",
            section__code__startswith="TEST-",
        )

    @staticmethod
    def _period(offering, period_code):
        return AcademicPerformanceInsightService._period_for_offering(
            offering,
            period_code,
        )

    def _status(self, offering, period_code):
        return AcademicPerformanceInsightService.get_section_performance_summary(
            offering,
            self._period(offering, period_code),
        )["status"]

    def _consistency_statuses(self, course_code, period_code):
        profiles = []
        for offering in CourseOffering.objects.filter(
            course__code=course_code,
            section__code__startswith="TEST-",
        ):
            profiles.append(
                AcademicPerformanceInsightService.get_activity_profile(
                    offering,
                    self._period(offering, period_code),
                )
            )
        return {
            AcademicPerformanceInsightService.get_activity_consistency_status(
                profile,
                profiles,
            )
            for profile in profiles
        }

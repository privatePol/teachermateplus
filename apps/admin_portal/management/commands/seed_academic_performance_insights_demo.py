from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import User
from apps.academics.models import (
    AcademicYear,
    Course,
    CourseOffering,
    FacultyAssignment,
    Section,
    Term,
)
from apps.academics.services import AcademicGovernanceService
from apps.attendance.models import AttendanceRecord, AttendanceSession
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.enrollment.models import Enrollment
from apps.grading.models import (
    CourseTemplateAssignment,
    DetailComputationMode,
    FacultyFinalClearanceReport,
    GradeActivity,
    GradeCorrectionApprovalStep,
    GradeCorrectionAttachment,
    GradeCorrectionRequest,
    GradeCorrectionRequestItem,
    GradeCorrectionUnlockWindow,
    GradeSubmission,
    GradeSubmissionReopenRequest,
    GradingPeriodLock,
    GradingTemplate,
    GradingTemplateComponent,
    GradingTemplateDetail,
    GradingTemplatePeriod,
    GradingTemplateSubcomponent,
    ScoreInputMode,
    ScoreInputModeOverride,
    StudentActivityScore,
    StudentFinalGrade,
    StudentPeriodGrade,
    TenantGradingProfile,
)
from apps.grading.services import FacultyGradingService
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.students.models import Student
from apps.tenants.models import Campus, Department, Program, Tenant


class Command(BaseCommand):
    help = (
        "Create or remove TEST-only Academic Performance Insights demo data. "
        "This command refuses to run when DEBUG is False."
    )

    PREFIX = "TEST"
    TEMPLATE_CODE = "TEST-ACADEMIC-INSIGHTS"
    ACADEMIC_YEAR_CODE = "TEST-AY-2026"
    TERM_CODE = "TEST-TERM"
    COURSE_CODES = ("A132-ITAPPS", "A221-ACGN")
    PERIODS = (
        ("PRELIM", "PRELIM", 1),
        ("MIDTERM", "MIDTERM", 2),
        ("PRE-FINAL", "PRE-FINAL", 3),
        ("FINAL", "FINAL", 4),
    )
    CAMPUS_TOKENS = ("CUBAO", "FAIRVIEW", "TAYTAY")

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm-demo-data",
            action="store_true",
            help="Required acknowledgement that TEST demo data will be changed.",
        )
        parser.add_argument(
            "--demo-password",
            help="Required password assigned to TEST demo user accounts when seeding.",
        )
        parser.add_argument(
            "--remove-demo-data",
            action="store_true",
            help="Remove records owned by this TEST demo dataset.",
        )

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError(
                "Refusing to run: Academic Performance Insights demo data is allowed only when DEBUG=True."
            )
        if not options["confirm_demo_data"]:
            raise CommandError("Pass --confirm-demo-data to acknowledge TEST demo data changes.")
        if options["remove_demo_data"]:
            with transaction.atomic():
                result = self._remove_demo_data()
            self.stdout.write(self.style.SUCCESS(self._format_summary("Removed", result)))
            return
        password = options.get("demo_password") or ""
        if len(password) < 8:
            raise CommandError("Pass --demo-password with at least 8 characters.")
        with transaction.atomic():
            result = self._seed(password=password)
        self.stdout.write(self.style.SUCCESS(self._format_summary("Seeded", result)))
        self.stdout.write(
            f"Use Academic Year {result['academic_year_code']}, Term "
            f"{result['term_code']}, and PRELIM through FINAL."
        )

    @staticmethod
    def _format_summary(action, result):
        return (
            f"{action} Academic Performance Insights TEST data: "
            f"{result.get('campuses', 0)} campuses, "
            f"{result.get('offerings', 0)} offerings, "
            f"{result.get('students', 0)} students, "
            f"{result.get('activities', 0)} activities, "
            f"{result.get('scores', 0)} scores."
        )

    def _seed(self, *, password):
        tenant = Tenant.objects.filter(code="NCBA", is_active=True).first()
        if tenant is None:
            raise CommandError("Active NCBA tenant was not found.")
        campuses = self._resolve_campuses(tenant)
        roles = self._resolve_roles()
        courses = self._resolve_courses(tenant)
        academic_year, term = self._academic_period(tenant)
        template = self._template(tenant)
        users = self._users(
            tenant=tenant,
            campuses=campuses,
            roles=roles,
            password=password,
        )

        offering_count = 0
        student_ids = set()
        activity_ids = set()
        score_ids = set()
        for campus_index, campus in enumerate(campuses):
            department = self._department(campus)
            program = self._program(tenant, campus, department)
            additional_faculty = users[f"faculty-additional-{campus.code}"]
            self._scope_user(
                user=additional_faculty,
                role=roles["faculty"],
                tenant=tenant,
                campus=campus,
                department=department,
            )
            self._scope_leadership_users(
                tenant=tenant,
                campus=campus,
                department=department,
                roles=roles,
                users=users,
            )
            for course_index, course in enumerate(courses):
                faculty = users[f"faculty-{campus.code}-{course.code}"]
                self._scope_user(
                    user=faculty,
                    role=roles["faculty"],
                    tenant=tenant,
                    campus=campus,
                    department=department,
                )
                self._profile(
                    tenant=tenant,
                    campus=campus,
                    department=department,
                    program=program,
                    course=course,
                    term=term,
                    template=template,
                )
                for section_index, section_letter in enumerate(("A", "B")):
                    section = self._section(
                        tenant=tenant,
                        campus=campus,
                        department=department,
                        program=program,
                        course=course,
                        section_letter=section_letter,
                    )
                    offering = self._offering(
                        tenant=tenant,
                        campus=campus,
                        department=department,
                        program=program,
                        academic_year=academic_year,
                        term=term,
                        course=course,
                        section=section,
                    )
                    offering_count += 1
                    self._faculty_assignment(offering, faculty)
                    students = self._students(
                        offering=offering,
                        campus_index=campus_index,
                        course_index=course_index,
                        section_index=section_index,
                    )
                    student_ids.update(student.id for student in students)
                    created_activities, created_scores = self._activities_and_scores(
                        offering=offering,
                        faculty=faculty,
                        students=students,
                        campus_index=campus_index,
                        course_index=course_index,
                        section_index=section_index,
                    )
                    activity_ids.update(created_activities)
                    score_ids.update(created_scores)
                additional_section_index = 2
                additional_section = self._section(
                    tenant=tenant,
                    campus=campus,
                    department=department,
                    program=program,
                    course=course,
                    section_letter="C",
                )
                additional_offering = self._offering(
                    tenant=tenant,
                    campus=campus,
                    department=department,
                    program=program,
                    academic_year=academic_year,
                    term=term,
                    course=course,
                    section=additional_section,
                )
                offering_count += 1
                self._faculty_assignment(additional_offering, additional_faculty)
                additional_students = self._students(
                    offering=additional_offering,
                    campus_index=campus_index,
                    course_index=course_index,
                    section_index=additional_section_index,
                    student_count=3,
                )
                student_ids.update(student.id for student in additional_students)
                created_activities, created_scores = self._activities_and_scores(
                    offering=additional_offering,
                    faculty=additional_faculty,
                    students=additional_students,
                    campus_index=campus_index,
                    course_index=course_index,
                    section_index=additional_section_index,
                )
                activity_ids.update(created_activities)
                score_ids.update(created_scores)

        SystemSettingService.set(
            FeatureSettingsService.ACADEMIC_PERFORMANCE_INSIGHTS_ENABLED_KEY,
            True,
            tenant_id=tenant.id,
            value_type="BOOL",
            is_active=True,
        )
        return {
            "campuses": len(campuses),
            "offerings": offering_count,
            "students": len(student_ids),
            "activities": len(activity_ids),
            "scores": len(score_ids),
            "academic_year_code": academic_year.code,
            "term_code": term.code,
        }

    def _resolve_campuses(self, tenant):
        campuses = []
        for token in self.CAMPUS_TOKENS:
            campus = (
                Campus.objects.filter(tenant=tenant, is_active=True, name__icontains=token)
                .order_by("id")
                .first()
            )
            if campus is None:
                raise CommandError(f"Required existing campus containing '{token}' was not found.")
            campuses.append(campus)
        return campuses

    @staticmethod
    def _resolve_roles():
        role_candidates = {
            "area_chair": ("AREA_CHAIR", "AREA_CHAIRPERSON", "AC"),
            "dean": ("COLLEGE_DEAN", "DEAN"),
            "cao": ("CAO",),
            "faculty": ("FACULTY",),
        }
        roles = {}
        for key, codes in role_candidates.items():
            role = Role.objects.filter(code__in=codes, is_active=True).order_by("id").first()
            if role is None:
                raise CommandError(f"Required active role was not found: {', '.join(codes)}.")
            roles[key] = role
        required_permissions = {
            "area_chair": ("admin_portal.access", "grading_analytics.read"),
            "dean": ("admin_portal.access", "grading_analytics.read"),
            "cao": ("admin_portal.access", "grading_analytics.read"),
            "faculty": ("faculty_portal.access",),
        }
        for role_key, permission_codes in required_permissions.items():
            granted = set(
                RolePermission.objects.filter(
                    role=roles[role_key],
                    permission__code__in=permission_codes,
                    permission__is_active=True,
                ).values_list("permission__code", flat=True)
            )
            missing = set(permission_codes) - granted
            if missing:
                raise CommandError(
                    f"Role {roles[role_key].code} is missing required permissions: "
                    f"{', '.join(sorted(missing))}."
                )
        return roles

    def _resolve_courses(self, tenant):
        defaults = {
            "A132-ITAPPS": "TEST Academic Performance Insights - IT Applications",
            "A221-ACGN": "TEST Academic Performance Insights - Accounting",
        }
        courses = []
        for code in self.COURSE_CODES:
            course, _created = Course.objects.get_or_create(
                tenant=tenant,
                code=code,
                defaults={
                    "title": defaults[code],
                    "units": Decimal("3"),
                    "default_base_value": Decimal("50"),
                    "is_active": True,
                },
            )
            if not course.is_active:
                raise CommandError(f"Existing course {code} is inactive.")
            courses.append(course)
        return courses

    def _academic_period(self, tenant):
        active_academic_year, active_term = AcademicGovernanceService.resolve_active_scope(
            tenant_id=tenant.id
        )
        if active_academic_year and active_term:
            return active_academic_year, active_term
        academic_year, _ = AcademicYear.objects.update_or_create(
            tenant=tenant,
            code=self.ACADEMIC_YEAR_CODE,
            defaults={
                "name": "TEST Academic Performance Insights AY",
                "start_date": date(2026, 1, 1),
                "end_date": date(2026, 12, 31),
                "is_active": True,
            },
        )
        term, _ = Term.objects.update_or_create(
            tenant=tenant,
            academic_year=academic_year,
            code=self.TERM_CODE,
            defaults={
                "name": "TEST Academic Performance Insights Term",
                "term_type": Term.TermType.REGULAR,
                "sequence_no": 1,
                "start_date": date(2026, 1, 1),
                "end_date": date(2026, 12, 31),
                "is_active": True,
            },
        )
        return academic_year, term

    def _template(self, tenant):
        template, _ = GradingTemplate.objects.update_or_create(
            tenant=tenant,
            code=self.TEMPLATE_CODE,
            defaults={
                "name": "TEST Academic Performance Insights Template",
                "description": "TEST-only template for Academic Performance Insights validation.",
                "default_base_value": Decimal("50"),
                "passing_grade_threshold": Decimal("75"),
                "approval_status": GradingTemplate.ApprovalStatus.APPROVED,
                "is_published": True,
                "published_at": timezone.now(),
                "is_active": True,
            },
        )
        for period_code, period_name, sequence_no in self.PERIODS:
            period, _ = GradingTemplatePeriod.objects.update_or_create(
                template=template,
                code=period_code,
                defaults={
                    "name": period_name,
                    "sequence_no": sequence_no,
                    "weight_percentage": Decimal("25"),
                    "is_active": True,
                },
            )
            class_standing, _ = GradingTemplateComponent.objects.update_or_create(
                template_period=period,
                code=f"{period_code}-CS",
                defaults={
                    "name": "Class Standing",
                    "weight_percentage": Decimal("60"),
                    "sort_order": 1,
                    "score_input_mode": ScoreInputMode.RAW_BASE50,
                    "is_exam_component": False,
                    "is_active": True,
                },
            )
            exam, _ = GradingTemplateComponent.objects.update_or_create(
                template_period=period,
                code=f"{period_code}-EXAM",
                defaults={
                    "name": f"{period_name} Exam",
                    "weight_percentage": Decimal("40"),
                    "sort_order": 2,
                    "score_input_mode": ScoreInputMode.RAW_BASE50,
                    "is_exam_component": True,
                    "is_active": True,
                },
            )
            quizzes, _ = GradingTemplateSubcomponent.objects.update_or_create(
                template_component=class_standing,
                code=f"{period_code}-QUIZZES",
                defaults={
                    "name": "Quizzes",
                    "weight_percentage": Decimal("40"),
                    "sort_order": 1,
                    "score_input_mode": ScoreInputModeOverride.INHERIT,
                    "detail_computation_mode": DetailComputationMode.AVERAGE_ACTIVITIES,
                    "admin_locked": True,
                    "is_active": True,
                },
            )
            participation, _ = GradingTemplateSubcomponent.objects.update_or_create(
                template_component=class_standing,
                code=f"{period_code}-PO",
                defaults={
                    "name": "Participation/Output",
                    "weight_percentage": Decimal("60"),
                    "sort_order": 2,
                    "score_input_mode": ScoreInputModeOverride.INHERIT,
                    "detail_computation_mode": DetailComputationMode.AVERAGE_ACTIVITIES,
                    "admin_locked": True,
                    "is_active": True,
                },
            )
            for index, (code_suffix, name, weight) in enumerate(
                (
                    ("REC", "Recitation", "33.33"),
                    ("ASSIGN", "Assignment/Activities", "33.33"),
                    ("ORAL", "Oral Presentation", "33.34"),
                ),
                start=1,
            ):
                GradingTemplateDetail.objects.update_or_create(
                    template_subcomponent=participation,
                    code=f"{period_code}-{code_suffix}",
                    defaults={
                        "name": name,
                        "weight_percentage": Decimal(weight),
                        "sort_order": index,
                        "score_input_mode": ScoreInputModeOverride.INHERIT,
                        "admin_locked": True,
                        "is_active": True,
                    },
                )
            quizzes.details.filter(code__startswith=self.PREFIX).update(is_active=False)
            exam.subcomponents.filter(code__startswith=self.PREFIX).update(is_active=False)
        return template

    @staticmethod
    def _department(campus):
        department = (
            Department.objects.filter(campus=campus, is_active=True, code="COLLEGE").first()
            or Department.objects.filter(
                campus=campus,
                is_active=True,
                operation_branch=Department.OperationBranch.ACADEMIC,
            )
            .order_by("id")
            .first()
        )
        if department is None:
            raise CommandError(f"No active academic department is available for {campus.code}.")
        return department

    def _program(self, tenant, campus, department):
        program, _ = Program.objects.update_or_create(
            tenant=tenant,
            campus=campus,
            department=department,
            code=f"{self.PREFIX}-INSIGHTS",
            defaults={
                "name": "TEST Academic Performance Insights Program",
                "level": "TEST",
                "is_active": True,
            },
        )
        return program

    def _users(self, *, tenant, campuses, roles, password):
        specs = {
            "dean": (
                "test-insights-dean",
                "dean.insights.test@ncba.edu.ph",
                "TEST",
                "College Dean",
            ),
            "cao": (
                "test-insights-cao",
                "cao.insights.test@ncba.edu.ph",
                "TEST",
                "CAO",
            ),
        }
        faculty_number = 0
        for campus in campuses:
            specs[f"ac-{campus.code}"] = (
                f"test-insights-ac-{campus.code.lower()}",
                f"ac.{campus.code.lower()}.insights.test@ncba.edu.ph",
                "TEST",
                f"Area Chair {campus.code}",
            )
            for course_code in self.COURSE_CODES:
                faculty_number += 1
                course_key = course_code.lower().replace("-", "")
                specs[f"faculty-{campus.code}-{course_code}"] = (
                    f"test-faculty-{faculty_number:02d}",
                    f"faculty-{faculty_number:02d}.insights.test@ncba.edu.ph",
                    "TEST",
                    f"FACULTY-{faculty_number:02d}",
                    f"test-insights-fac-{campus.code.lower()}-{course_key}",
                )
        for campus in campuses:
            faculty_number += 1
            specs[f"faculty-additional-{campus.code}"] = (
                f"test-faculty-{faculty_number:02d}",
                f"faculty-{faculty_number:02d}.insights.test@ncba.edu.ph",
                "TEST",
                f"FACULTY-{faculty_number:02d}",
            )
        users = {}
        for key, spec in specs.items():
            username, email, first_name, last_name, *legacy_usernames = spec
            user = User.objects.filter(username=username).first()
            if user is None and legacy_usernames:
                user = User.objects.filter(username__in=legacy_usernames).order_by("id").first()
                if user is not None:
                    user.username = username
            if user is None:
                user = User(username=username)
            user.email = email
            user.first_name = first_name
            user.last_name = last_name
            user.default_tenant = tenant
            user.is_active = True
            user.is_staff = False
            user.must_change_password = False
            user.privacy_consent_version = getattr(
                settings,
                "PRIVACY_CONSENT_VERSION",
                "2026-03",
            )
            user.privacy_consent_at = timezone.now()
            user.set_password(password)
            user.save()
            users[key] = user
        return users

    def _scope_leadership_users(self, *, tenant, campus, department, roles, users):
        self._scope_user(
            user=users[f"ac-{campus.code}"],
            role=roles["area_chair"],
            tenant=tenant,
            campus=campus,
            department=department,
        )
        self._scope_user(
            user=users["dean"],
            role=roles["dean"],
            tenant=tenant,
            campus=campus,
            department=department,
        )
        self._scope_user(
            user=users["cao"],
            role=roles["cao"],
            tenant=tenant,
            campus=campus,
            department=department,
        )

    @staticmethod
    def _scope_user(*, user, role, tenant, campus, department):
        UserRole.objects.update_or_create(
            user=user,
            role=role,
            tenant=tenant,
            campus=campus,
            department=department,
            defaults={"is_active": True},
        )
        changed = False
        if user.default_campus_id is None:
            user.default_campus = campus
            changed = True
        if user.default_department_id is None:
            user.default_department = department
            changed = True
        if changed:
            user.save(update_fields=["default_campus", "default_department", "updated_at"])

    def _profile(self, *, tenant, campus, department, program, course, term, template):
        return TenantGradingProfile.objects.update_or_create(
            tenant=tenant,
            profile_code=f"{self.PREFIX}-{campus.code}-{course.code}",
            defaults={
                "profile_name": f"TEST {campus.code} {course.code} Insights Profile",
                "campus": campus,
                "department": department,
                "program": program,
                "course": course,
                "term_type": term.term_type,
                "grading_template": template,
                "default_base_value": Decimal("50"),
                "passing_grade_threshold": Decimal("75"),
                "period_grade_formula_mode": TenantGradingProfile.PeriodGradeFormulaMode.WEIGHTED_COMPONENTS,
                "final_grade_formula_mode": TenantGradingProfile.FinalGradeFormulaMode.AVERAGE_ACTIVE_PERIODS,
                "priority": 1,
                "effective_from_term": term,
                "is_default": False,
                "is_active": True,
            },
        )[0]

    def _section(self, *, tenant, campus, department, program, course, section_letter):
        code = f"{self.PREFIX}-{campus.code}-{course.code}-{section_letter}"
        return Section.objects.update_or_create(
            tenant=tenant,
            campus=campus,
            department=department,
            program=program,
            code=code,
            defaults={
                "name": f"TEST {campus.code} {course.code} Section {section_letter}",
                "year_level": "TEST",
                "is_active": True,
            },
        )[0]

    @staticmethod
    def _offering(*, tenant, campus, department, program, academic_year, term, course, section):
        return CourseOffering.objects.update_or_create(
            tenant=tenant,
            campus=campus,
            department=department,
            program=program,
            academic_year=academic_year,
            term=term,
            course=course,
            section=section,
            defaults={
                "room": "TEST",
                "schedule_text": "TEST DATA ONLY",
                "status": CourseOffering.Status.OPEN,
                "is_active": True,
            },
        )[0]

    @staticmethod
    def _faculty_assignment(offering, faculty):
        FacultyAssignment.objects.update_or_create(
            offering=offering,
            faculty_user=faculty,
            defaults={
                "tenant": offering.tenant,
                "campus": offering.campus,
                "assignment_note": "TEST Academic Performance Insights assignment",
                "accepted_by": faculty,
                "response_status": FacultyAssignment.ResponseStatus.ACCEPTED,
                "responded_at": timezone.now(),
                "accepted_at": timezone.now(),
                "is_primary": True,
                "is_active": True,
            },
        )

    def _students(
        self,
        *,
        offering,
        campus_index,
        course_index,
        section_index,
        student_count=10,
    ):
        students = []
        for student_index in range(1, student_count + 1):
            student_no = (
                f"{self.PREFIX}-{offering.campus.code}-"
                f"{course_index + 1}{section_index + 1}{student_index:02d}"
            )
            student, _ = Student.objects.update_or_create(
                tenant=offering.tenant,
                campus=offering.campus,
                student_no=student_no,
                defaults={
                    "department": offering.department,
                    "program": offering.program,
                    "first_name": f"Student {student_index:02d}",
                    "last_name": (
                        f"TEST {offering.campus.code} {offering.course.code} "
                        f"{offering.section.code[-1]}"
                    ),
                    "year_level": "TEST",
                    "status": Student.Status.ACTIVE,
                    "is_active": True,
                },
            )
            Enrollment.objects.update_or_create(
                course_offering=offering,
                student=student,
                defaults={
                    "tenant": offering.tenant,
                    "campus": offering.campus,
                    "academic_year": offering.academic_year,
                    "term": offering.term,
                    "enrollment_status": Enrollment.Status.ACTIVE,
                    "encoded_via_portal": Enrollment.SourcePortal.ADMIN,
                    "is_active": True,
                },
            )
            students.append(student)
        return students

    def _activity_counts(self, *, course_code, period_code, campus_index, section_index):
        if course_code == "A221-ACGN":
            if period_code == "PRELIM":
                return 3, 3, 1
            if period_code == "MIDTERM":
                return (3, 3, 1) if section_index == 0 else (2, 3, 1)
            if period_code == "PRE-FINAL":
                return (3, 3, 1) if section_index == 0 else (1, 1, 1)
            if period_code == "FINAL" and campus_index == 2 and section_index == 1:
                return 3, 3, 0
        if (
            course_code == "A132-ITAPPS"
            and period_code == "MIDTERM"
            and campus_index == 2
            and section_index == 1
        ):
            return 3, 3, 0
        return 3, 3, 1

    def _activities_and_scores(
        self,
        *,
        offering,
        faculty,
        students,
        campus_index,
        course_index,
        section_index,
    ):
        activity_ids = set()
        score_ids = set()
        existing_demo_activities = GradeActivity.objects.filter(
            offering=offering,
            title__startswith=f"{self.PREFIX} ",
        )
        StudentActivityScore.objects.filter(activity__in=existing_demo_activities).delete()
        existing_demo_activities.delete()
        template = FacultyGradingService.resolve_template_for_offering(offering)
        periods = {}
        for period in template.periods.filter(is_active=True).order_by("sequence_no"):
            for label in (period.code, period.name):
                normalized_label = AcademicGovernanceService.normalize_period_key(label)
                if normalized_label:
                    periods[normalized_label] = period
        base_value = FacultyGradingService.resolve_base_value(offering, template)
        for period_index, (period_code, _period_name, _sequence_no) in enumerate(self.PERIODS):
            period = periods.get(AcademicGovernanceService.normalize_period_key(period_code))
            if period is None:
                continue
            components = list(
                period.components.filter(is_active=True)
                .prefetch_related("subcomponents__details")
                .order_by("sort_order", "id")
            )
            class_standing = next(
                (component for component in components if not component.is_exam_component),
                None,
            )
            exam_component = next(
                (component for component in components if component.is_exam_component),
                None,
            )
            subcomponents = (
                list(class_standing.subcomponents.filter(is_active=True).order_by("sort_order", "id"))
                if class_standing
                else []
            )
            quizzes = next(
                (
                    subcomponent
                    for subcomponent in subcomponents
                    if "QUIZ" in f"{subcomponent.code} {subcomponent.name}".upper()
                ),
                subcomponents[0] if subcomponents else None,
            )
            participation = next(
                (
                    subcomponent
                    for subcomponent in subcomponents
                    if any(
                        token in f"{subcomponent.code} {subcomponent.name}".upper()
                        for token in ("PARTICIPATION", "OUTPUT", "_PO")
                    )
                ),
                next(
                    (
                        subcomponent
                        for subcomponent in subcomponents
                        if quizzes is None or subcomponent.id != quizzes.id
                    ),
                    None,
                ),
            )
            participation_details = (
                list(participation.details.filter(is_active=True).order_by("sort_order", "id"))
                if participation
                else []
            )
            quiz_count, output_count, exam_count = self._activity_counts(
                course_code=offering.course.code,
                period_code=period_code,
                campus_index=campus_index,
                section_index=section_index,
            )
            activity_specs = []
            for index in range(quiz_count if class_standing else 0):
                activity_specs.append(
                    (
                        class_standing,
                        quizzes,
                        None,
                        f"Quiz {index + 1}",
                        Decimal("100"),
                        "QUIZ",
                        index,
                    )
                )
            for index in range(output_count if participation else 0):
                detail = (
                    participation_details[index % len(participation_details)]
                    if participation_details
                    else None
                )
                detail_name = detail.name if detail else participation.name
                activity_specs.append(
                    (
                        class_standing,
                        participation,
                        detail,
                        f"{detail_name} {index + 1}",
                        Decimal("100"),
                        "OUTPUT",
                        index,
                    )
                )
            for index in range(exam_count if exam_component else 0):
                activity_specs.append(
                    (exam_component, None, None, "Major Exam", Decimal("100"), "EXAM", index)
                )
            for component, subcomponent, detail, label, total_score, category, activity_index in activity_specs:
                title = (
                    f"{self.PREFIX} {period_code} {offering.section.code} {label}"
                )
                activity, _ = GradeActivity.objects.update_or_create(
                    offering=offering,
                    template_period=period,
                    title=title,
                    defaults={
                        "tenant": offering.tenant,
                        "campus": offering.campus,
                        "template_component": component,
                        "template_subcomponent": subcomponent,
                        "template_detail": detail,
                        "total_score": total_score,
                        "activity_date": date(2026, 1, 15)
                        + timedelta(days=(period_index * 40) + activity_index),
                        "created_by_user": faculty,
                        "is_active": True,
                    },
                )
                activity_ids.add(activity.id)
                for student_index, student in enumerate(students):
                    raw_score, missing = self._score_pattern(
                        course_code=offering.course.code,
                        campus_index=campus_index,
                        section_index=section_index,
                        period_index=period_index,
                        category=category,
                        activity_index=activity_index,
                        student_index=student_index,
                    )
                    if missing:
                        StudentActivityScore.objects.filter(
                            activity=activity,
                            student=student,
                        ).delete()
                        continue
                    computed_score = FacultyGradingService.compute_activity_score(
                        raw_score=raw_score,
                        total_score=total_score,
                        base_value=base_value,
                        score_input_mode=ScoreInputMode.RAW_BASE50,
                    )
                    score, _ = StudentActivityScore.objects.update_or_create(
                        activity=activity,
                        student=student,
                        defaults={
                            "raw_score": raw_score,
                            "computed_score": computed_score,
                            "encoded_by_user": faculty,
                            "remarks": "TEST Academic Performance Insights score",
                            "is_active": True,
                        },
                    )
                    score_ids.add(score.id)
        return activity_ids, score_ids

    @staticmethod
    def _score_pattern(
        *,
        course_code,
        campus_index,
        section_index,
        period_index,
        category,
        activity_index,
        student_index,
    ):
        variation = Decimal((student_index * 3 + activity_index * 2 + period_index) % 9)
        missing = False
        if course_code == "A132-ITAPPS" and campus_index == 0 and section_index == 0:
            base = {"QUIZ": 80, "OUTPUT": 86, "EXAM": 82}[category]
        elif course_code == "A132-ITAPPS" and campus_index == 0 and section_index == 1:
            base = {"QUIZ": 24, "OUTPUT": 38, "EXAM": 28}[category]
        elif course_code == "A132-ITAPPS" and campus_index == 1 and section_index == 1:
            base = {"QUIZ": 58, "OUTPUT": 68, "EXAM": 62}[category]
            missing = (student_index + activity_index + period_index) % 3 == 0
            if student_index == 0 and activity_index == 0:
                missing = False
                return Decimal("0"), False
        elif course_code == "A132-ITAPPS" and campus_index == 2 and section_index == 0:
            base = {"QUIZ": 38, "OUTPUT": 82, "EXAM": 74}[category]
        else:
            base = {"QUIZ": 66, "OUTPUT": 74, "EXAM": 70}[category]
            base += (campus_index * 3) - (section_index * 5)
        raw_score = min(Decimal("98"), Decimal(base) + variation)
        return raw_score, missing

    def _remove_demo_data(self):
        offering_ids = list(
            CourseOffering.objects.filter(section__code__startswith=f"{self.PREFIX}-").values_list(
                "id",
                flat=True,
            )
        )
        activity_ids = list(
            GradeActivity.objects.filter(offering_id__in=offering_ids).values_list("id", flat=True)
        )
        student_ids = list(
            Student.objects.filter(student_no__startswith=f"{self.PREFIX}-").values_list("id", flat=True)
        )
        correction_ids = list(
            GradeCorrectionRequest.objects.filter(offering_id__in=offering_ids).values_list(
                "id",
                flat=True,
            )
        )
        GradeCorrectionAttachment.objects.filter(correction_request_id__in=correction_ids).delete()
        GradeCorrectionRequestItem.objects.filter(correction_request_id__in=correction_ids).delete()
        GradeCorrectionApprovalStep.objects.filter(correction_request_id__in=correction_ids).delete()
        GradeCorrectionUnlockWindow.objects.filter(correction_request_id__in=correction_ids).delete()
        GradeCorrectionRequest.objects.filter(id__in=correction_ids).delete()
        GradeSubmissionReopenRequest.objects.filter(offering_id__in=offering_ids).delete()
        GradeSubmission.objects.filter(offering_id__in=offering_ids).delete()
        GradingPeriodLock.objects.filter(course_offering_id__in=offering_ids).delete()
        FacultyFinalClearanceReport.objects.filter(
            academic_year__code=self.ACADEMIC_YEAR_CODE,
            term__code=self.TERM_CODE,
        ).delete()
        StudentFinalGrade.objects.filter(offering_id__in=offering_ids).delete()
        StudentPeriodGrade.objects.filter(offering_id__in=offering_ids).delete()
        AttendanceRecord.objects.filter(session__offering_id__in=offering_ids).delete()
        AttendanceSession.objects.filter(offering_id__in=offering_ids).delete()
        StudentActivityScore.objects.filter(activity_id__in=activity_ids).delete()
        GradeActivity.objects.filter(id__in=activity_ids).delete()
        Enrollment.objects.filter(course_offering_id__in=offering_ids).delete()
        FacultyAssignment.objects.filter(offering_id__in=offering_ids).delete()
        offering_count = CourseOffering.objects.filter(id__in=offering_ids).count()
        CourseOffering.objects.filter(id__in=offering_ids).delete()
        section_count = Section.objects.filter(code__startswith=f"{self.PREFIX}-").count()
        Section.objects.filter(code__startswith=f"{self.PREFIX}-").delete()
        student_count = Student.objects.filter(id__in=student_ids).count()
        Student.objects.filter(id__in=student_ids).delete()
        TenantGradingProfile.objects.filter(profile_code__startswith=f"{self.PREFIX}-").delete()
        demo_users = User.objects.filter(
            Q(username__startswith="test-insights-")
            | Q(username__startswith="test-faculty-")
        )
        UserRole.objects.filter(user__in=demo_users).delete()
        demo_users.delete()
        Program.objects.filter(code=f"{self.PREFIX}-INSIGHTS").delete()
        template = GradingTemplate.objects.filter(code=self.TEMPLATE_CODE).first()
        if template:
            CourseTemplateAssignment.objects.filter(grading_template=template).delete()
            details = GradingTemplateDetail.objects.filter(
                template_subcomponent__template_component__template_period__template=template
            )
            details.delete()
            GradingTemplateSubcomponent.objects.filter(
                template_component__template_period__template=template
            ).delete()
            GradingTemplateComponent.objects.filter(template_period__template=template).delete()
            GradingTemplatePeriod.objects.filter(template=template).delete()
            template.delete()
        Term.objects.filter(
            academic_year__code=self.ACADEMIC_YEAR_CODE,
            code=self.TERM_CODE,
        ).delete()
        AcademicYear.objects.filter(code=self.ACADEMIC_YEAR_CODE).delete()
        for course in Course.objects.filter(
            code__in=self.COURSE_CODES,
            title__startswith=self.PREFIX,
        ):
            if not course.course_offerings.exists() and not course.template_assignments.exists():
                course.delete()
        return {
            "campuses": 0,
            "offerings": offering_count,
            "students": student_count,
            "activities": len(activity_ids),
            "scores": 0,
            "sections": section_count,
        }

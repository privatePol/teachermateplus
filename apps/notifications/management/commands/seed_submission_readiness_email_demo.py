from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.academics.models import (
    AcademicYear,
    ActiveGradingPeriodSetting,
    Course,
    CourseOffering,
    FacultyAssignment,
    Section,
    TenantTermGradingPeriod,
    Term,
)
from apps.admin_portal.submission_readiness import GradeSubmissionReadinessService
from apps.attendance.models import AttendanceRecord, AttendanceSession
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.enrollment.models import Enrollment
from apps.grading.models import (
    CourseTemplateAssignment,
    GradeActivity,
    GradeSubmission,
    GradingPeriodLock,
    GradingTemplate,
    GradingTemplateComponent,
    GradingTemplatePeriod,
    GradingTemplateSubcomponent,
    StudentActivityScore,
)
from apps.notifications.models import SubmissionReadinessNotificationLog
from apps.rbac.models import Role, RolePermission, UserRole
from apps.students.models import Student
from apps.tenants.models import Campus, Department, Program, SystemSetting, Tenant


class Command(BaseCommand):
    help = "Create or reset DEBUG-only sample data for Submission Readiness email testing."

    PREFIX = "TEST-READINESS-EMAIL"
    ACADEMIC_YEAR_CODE = f"{PREFIX}-AY"
    TERM_CODE = f"{PREFIX}-TERM"
    TEMPLATE_CODE = f"{PREFIX}-TEMPLATE"
    DEPARTMENT_CODE = f"{PREFIX}-DEPT"
    PROGRAM_CODE = f"{PREFIX}-PROGRAM"
    PERIOD_CODE = "PRELIM"
    FACULTY_USERNAMES = ("test-faculty-readiness-01", "test-faculty-readiness-02")
    HEAD_USERNAME = "test-readiness-area-chair"
    SCENARIOS = (
        ("A", 3, False, "Very low readiness"),
        ("B", 1, False, "Below threshold"),
        ("C", 3, False, "Exactly 50% threshold control"),
        ("D", 3, False, "Very low readiness"),
        ("E", 1, False, "Below threshold"),
        ("F", 3, True, "Ready and submitted control"),
    )
    POLICY_KEYS = (
        FeatureSettingsService.SUBMISSION_READINESS_EMAIL_ENABLED_KEY,
        FeatureSettingsService.SUBMISSION_READINESS_EMAIL_DAYS_BEFORE_KEY,
        FeatureSettingsService.SUBMISSION_READINESS_EMAIL_THRESHOLD_KEY,
        FeatureSettingsService.SUBMISSION_READINESS_EMAIL_ROLE_CODES_KEY,
        FeatureSettingsService.SUBMISSION_READINESS_EMAIL_SEND_EMPTY_KEY,
        FeatureSettingsService.SUBMISSION_READINESS_EMAIL_INCLUDE_LINK_KEY,
        FeatureSettingsService.SUBMISSION_READINESS_EMAIL_REPEAT_KEY,
    )
    MANILA = ZoneInfo("Asia/Manila")

    def add_arguments(self, parser):
        parser.add_argument("--confirm-demo-data", action="store_true")
        parser.add_argument("--reset", action="store_true", help="Remove only this command's demo records and restore policy settings.")
        parser.add_argument("--inspect", action="store_true", help="Read and report the existing demo dataset without changing it.")
        parser.add_argument("--recipient-email", help="Required non-production email for the demo Academic Head.")
        parser.add_argument("--as-of-date", help="Snapshot date in YYYY-MM-DD; defaults to today in Asia/Manila.")
        parser.add_argument("--tenant", default="NCBA", help="Existing tenant code (default: NCBA).")
        parser.add_argument("--campus", help="Existing campus code; defaults to the first active campus in the tenant.")
        parser.add_argument("--demo-password", help="Optional password for demo users; otherwise their passwords are unusable.")

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Refusing to run: readiness email demo data is allowed only when DEBUG=True.")
        if not options["confirm_demo_data"]:
            raise CommandError("Pass --confirm-demo-data to acknowledge TEST demo data changes.")
        tenant = Tenant.objects.filter(code__iexact=options["tenant"], is_active=True).first()
        if tenant is None:
            raise CommandError(f"Active tenant '{options['tenant']}' was not found.")
        if options["reset"]:
            with transaction.atomic():
                removed = self._reset(tenant)
            self.stdout.write(self.style.SUCCESS(f"Removed Submission Readiness email demo data: {removed}."))
            return
        if options["inspect"]:
            self._print_result(self._inspect(tenant))
            return
        recipient_email = (options.get("recipient_email") or "").strip()
        try:
            validate_email(recipient_email)
        except ValidationError as exc:
            raise CommandError("Pass a valid --recipient-email for the demo Academic Head.") from exc
        as_of_date = self._parse_date(options.get("as_of_date"))
        campus_qs = Campus.objects.filter(tenant=tenant, is_active=True).order_by("id")
        if options.get("campus"):
            campus_qs = campus_qs.filter(code__iexact=options["campus"])
        campus = campus_qs.first()
        if campus is None:
            raise CommandError("No matching active campus was found.")
        with transaction.atomic():
            result = self._seed(
                tenant=tenant,
                campus=campus,
                recipient_email=recipient_email,
                as_of_date=as_of_date,
                demo_password=options.get("demo_password") or "",
            )
        self._print_result(result)

    def _parse_date(self, value):
        if not value:
            return timezone.now().astimezone(self.MANILA).date()
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise CommandError("--as-of-date must use YYYY-MM-DD.") from exc

    def _inspect(self, tenant):
        assignments = list(
            FacultyAssignment.objects.filter(
                offering__tenant=tenant,
                offering__course__code__startswith=f"{self.PREFIX}-",
                is_active=True,
            ).select_related(
                "faculty_user",
                "offering__tenant",
                "offering__campus",
                "offering__academic_year",
                "offering__term",
                "offering__course",
                "offering__section",
            ).order_by("offering__course__code")
        )
        if len(assignments) != len(self.SCENARIOS):
            raise CommandError("A complete six-assignment readiness email demo dataset was not found.")
        first = assignments[0].offering
        lock = GradingPeriodLock.objects.filter(
            tenant=tenant,
            campus=first.campus,
            academic_year=first.academic_year,
            term=first.term,
            period_code=self.PERIOD_CODE,
            is_active=True,
        ).first()
        if lock is None or lock.deadline_at is None:
            raise CommandError("The demo PRELIM deadline lock was not found.")
        as_of_date = lock.deadline_at.astimezone(self.MANILA).date() - timedelta(days=5)
        rows = GradeSubmissionReadinessService.calculate(
            assignments,
            selected_period_code=self.PERIOD_CODE,
            now=datetime.combine(as_of_date, time(1, 0), tzinfo=self.MANILA),
        )
        head = User.objects.filter(username=self.HEAD_USERNAME, default_tenant=tenant).first()
        if head is None:
            raise CommandError("The demo Academic Head was not found.")
        label_by_letter = {letter: label for letter, _complete, _submitted, label in self.SCENARIOS}
        return {
            "tenant": tenant,
            "campus": first.campus,
            "academic_year": first.academic_year,
            "term": first.term,
            "period": rows[0].template_period,
            "deadline": lock.deadline_at.astimezone(self.MANILA),
            "as_of_date": as_of_date,
            "faculty": list({row.faculty_user_id: row.faculty_user for row in assignments}.values()),
            "head": head,
            "rows": rows,
            "scenario_labels": {
                row.id: label_by_letter[row.offering.course.code.rsplit("-", 1)[-1]] for row in assignments
            },
            "reused_offerings": len(assignments),
        }

    def _seed(self, *, tenant, campus, recipient_email, as_of_date, demo_password):
        existing_offering_count = CourseOffering.objects.filter(
            tenant=tenant,
            campus=campus,
            section__code__startswith=f"{self.PREFIX}-",
        ).count()
        other_campus_demo = CourseOffering.objects.filter(
            tenant=tenant,
            section__code__startswith=f"{self.PREFIX}-",
        ).exclude(campus=campus).first()
        if other_campus_demo:
            raise CommandError(
                "This tenant already has readiness email demo data in another campus. Run --reset before changing campus."
            )
        roles = self._roles()
        department, _ = Department.objects.update_or_create(
            tenant=tenant,
            campus=campus,
            code=self.DEPARTMENT_CODE,
            defaults={"name": "TEST Readiness Email Department", "is_active": True},
        )
        program, _ = Program.objects.update_or_create(
            tenant=tenant,
            campus=campus,
            department=department,
            code=self.PROGRAM_CODE,
            defaults={"name": "TEST Readiness Email Program", "is_active": True},
        )
        academic_year, _ = AcademicYear.objects.update_or_create(
            tenant=tenant,
            code=self.ACADEMIC_YEAR_CODE,
            defaults={
                "name": "TEST Submission Readiness Email AY",
                "start_date": as_of_date.replace(month=1, day=1),
                "end_date": as_of_date.replace(month=12, day=31),
                "is_active": True,
            },
        )
        term, _ = Term.objects.update_or_create(
            tenant=tenant,
            academic_year=academic_year,
            code=self.TERM_CODE,
            defaults={
                "name": "TEST Submission Readiness Email Term",
                "term_type": Term.TermType.REGULAR,
                "sequence_no": 1,
                "start_date": as_of_date,
                "end_date": as_of_date + timedelta(days=120),
                "is_active": True,
            },
        )
        term_period, _ = TenantTermGradingPeriod.objects.update_or_create(
            tenant=tenant,
            term=term,
            code=self.PERIOD_CODE,
            defaults={"name": "Prelim", "sequence_no": 1, "is_active": True},
        )
        ActiveGradingPeriodSetting.objects.update_or_create(
            tenant=tenant,
            campus=campus,
            term=term,
            defaults={
                "period": term_period,
                "remarks": "TEST Submission Readiness email demo",
                "is_active": True,
            },
        )
        template, period, components = self._template(tenant)
        faculty = [
            self._user(
                username=username,
                email=f"{username}@example.invalid",
                tenant=tenant,
                campus=campus,
                department=department,
                role=roles["faculty"],
                password=demo_password,
            )
            for username in self.FACULTY_USERNAMES
        ]
        head = self._user(
            username=self.HEAD_USERNAME,
            email=recipient_email,
            tenant=tenant,
            campus=campus,
            department=department,
            role=roles["area_chair"],
            password=demo_password,
        )
        deadline = datetime.combine(as_of_date + timedelta(days=5), time(23, 59), tzinfo=self.MANILA)
        GradingPeriodLock.objects.update_or_create(
            tenant=tenant,
            campus=campus,
            academic_year=academic_year,
            term=term,
            period_code=self.PERIOD_CODE,
            scope_type=GradingPeriodLock.ScopeType.CAMPUS,
            course_offering=None,
            defaults={
                "is_locked": False,
                "deadline_at": deadline,
                "remarks": "TEST readiness email demo; encoding remains open",
                "is_active": True,
            },
        )

        assignments = []
        scenario_labels = {}
        for index, (letter, complete_students, submitted, label) in enumerate(self.SCENARIOS):
            faculty_user = faculty[0 if index < 3 else 1]
            course, _ = Course.objects.update_or_create(
                tenant=tenant,
                code=f"{self.PREFIX}-{letter}",
                defaults={
                    "campus": campus,
                    "department": department,
                    "title": f"TEST Readiness Scenario {letter}",
                    "units": Decimal("3.00"),
                    "default_base_value": Decimal("50.00"),
                    "is_active": True,
                },
            )
            CourseTemplateAssignment.objects.update_or_create(
                course=course,
                grading_template=template,
                effective_from_term=term,
                defaults={"is_active": True},
            )
            section, _ = Section.objects.update_or_create(
                tenant=tenant,
                campus=campus,
                department=department,
                program=program,
                code=f"{self.PREFIX}-{letter}",
                defaults={"name": f"TEST Readiness Section {letter}", "year_level": "TEST", "is_active": True},
            )
            offering, _ = CourseOffering.objects.update_or_create(
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
                    "schedule_text": "TEST READINESS EMAIL DATA ONLY",
                    "status": CourseOffering.Status.OPEN,
                    "is_active": True,
                },
            )
            assignment, _ = FacultyAssignment.objects.update_or_create(
                offering=offering,
                faculty_user=faculty_user,
                defaults={
                    "tenant": tenant,
                    "campus": campus,
                    "assignment_note": f"{self.PREFIX} scenario {letter}",
                    "accepted_by": faculty_user,
                    "response_status": FacultyAssignment.ResponseStatus.ACCEPTED,
                    "responded_at": timezone.now(),
                    "accepted_at": timezone.now(),
                    "is_primary": True,
                    "is_active": True,
                },
            )
            assignments.append(assignment)
            scenario_labels[assignment.id] = label
            students = self._students(offering, index)
            self._scenario_records(
                offering=offering,
                period=period,
                components=components,
                students=students,
                faculty=faculty_user,
                letter=letter,
                complete_students=complete_students,
                submitted=submitted,
                as_of_date=as_of_date,
            )
        self._configure_policy(tenant)
        snapshot_now = datetime.combine(as_of_date, time(1, 0), tzinfo=self.MANILA)
        readiness = GradeSubmissionReadinessService.calculate(
            assignments,
            selected_period_code=self.PERIOD_CODE,
            now=snapshot_now,
        )
        return {
            "tenant": tenant,
            "campus": campus,
            "academic_year": academic_year,
            "term": term,
            "period": period,
            "deadline": deadline,
            "as_of_date": as_of_date,
            "faculty": faculty,
            "head": head,
            "rows": readiness,
            "scenario_labels": scenario_labels,
            "reused_offerings": min(existing_offering_count, len(self.SCENARIOS)),
        }

    @staticmethod
    def _roles():
        faculty = Role.objects.filter(code="FACULTY", is_active=True).first()
        area_chair = Role.objects.filter(
            code__in=("AREA_CHAIR", "AREA_CHAIRPERSON", "AC"), is_active=True
        ).order_by("id").first()
        if faculty is None or area_chair is None:
            raise CommandError("Active FACULTY and Area Chair roles are required.")
        required_head_permissions = {"admin_portal.access", "faculty_activity_monitor.read"}
        granted = set(
            RolePermission.objects.filter(
                role=area_chair,
                permission__code__in=required_head_permissions,
                permission__is_active=True,
            ).values_list("permission__code", flat=True)
        )
        if granted != required_head_permissions:
            raise CommandError(
                f"Area Chair role {area_chair.code} must have admin_portal.access and faculty_activity_monitor.read."
            )
        return {"faculty": faculty, "area_chair": area_chair}

    def _user(self, *, username, email, tenant, campus, department, role, password):
        existing = User.objects.filter(username=username).first()
        if existing and existing.default_tenant_id not in (None, tenant.id):
            raise CommandError(
                f"Demo username {username} is already owned by another tenant. Reset that demo dataset first."
            )
        email_owner = User.objects.filter(email__iexact=email).exclude(username=username).first()
        if email_owner:
            raise CommandError(f"Email {email} already belongs to another user; choose another demo recipient email.")
        user, _ = User.objects.update_or_create(
            username=username,
            defaults={
                "email": email,
                "first_name": "TEST",
                "last_name": username.replace("test-", "").replace("-", " ").title(),
                "default_tenant": tenant,
                "default_campus": campus,
                "default_department": department,
                "is_active": True,
            },
        )
        if password:
            if len(password) < 8:
                raise CommandError("--demo-password must contain at least 8 characters.")
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(update_fields=["password", "updated_at"])
        UserRole.objects.update_or_create(
            user=user,
            role=role,
            tenant=tenant,
            campus=campus,
            department=department,
            defaults={"is_active": True},
        )
        return user

    def _template(self, tenant):
        template, _ = GradingTemplate.objects.update_or_create(
            tenant=tenant,
            code=self.TEMPLATE_CODE,
            defaults={
                "name": "TEST Submission Readiness Email Template",
                "description": "TEST-only six-bucket template plus attendance.",
                "default_base_value": Decimal("50.00"),
                "approval_status": GradingTemplate.ApprovalStatus.APPROVED,
                "is_published": True,
                "published_at": timezone.now(),
                "is_active": True,
            },
        )
        period, _ = GradingTemplatePeriod.objects.update_or_create(
            template=template,
            code=self.PERIOD_CODE,
            defaults={"name": "Prelim", "sequence_no": 1, "weight_percentage": Decimal("100.00"), "is_active": True},
        )
        graded_components = []
        for sort_order, (code, name, weight) in enumerate(
            (
                ("WORK-1", "Course Work 1", "16.67"),
                ("WORK-2", "Course Work 2", "16.67"),
                ("WORK-3", "Course Work 3", "16.66"),
                ("WORK-4", "Course Work 4", "16.67"),
                ("WORK-5", "Course Work 5", "16.67"),
                ("EXAM", "Prelim Exam", "16.66"),
            ),
            start=1,
        ):
            component, _ = GradingTemplateComponent.objects.update_or_create(
                template_period=period,
                code=code,
                defaults={
                    "name": name,
                    "weight_percentage": Decimal(weight),
                    "sort_order": sort_order,
                    "is_exam_component": code == "EXAM",
                    "is_active": True,
                },
            )
            graded_components.append(component)
        attendance, _ = GradingTemplateComponent.objects.update_or_create(
            template_period=period,
            code="ATTENDANCE",
            defaults={"name": "Attendance", "weight_percentage": Decimal("0.00"), "sort_order": 7, "is_active": True},
        )
        GradingTemplateSubcomponent.objects.update_or_create(
            template_component=attendance,
            code="ATTENDANCE",
            defaults={
                "name": "Attendance",
                "weight_percentage": Decimal("100.00"),
                "sort_order": 1,
                "is_attendance_component": True,
                "is_active": True,
            },
        )
        return template, period, tuple(graded_components)

    def _students(self, offering, offering_index):
        students = []
        for student_index in range(1, 4):
            student_no = f"{self.PREFIX}-{offering_index + 1:02d}-{student_index:02d}"
            student, _ = Student.objects.update_or_create(
                tenant=offering.tenant,
                campus=offering.campus,
                student_no=student_no,
                defaults={
                    "department": offering.department,
                    "program": offering.program,
                    "first_name": f"Demo {student_index}",
                    "last_name": f"TEST Readiness {offering.section.code[-1]}",
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

    def _scenario_records(
        self, *, offering, period, components, students, faculty, letter, complete_students, submitted, as_of_date
    ):
        demo_activities = GradeActivity.objects.filter(offering=offering, title__startswith=self.PREFIX)
        StudentActivityScore.objects.filter(activity__in=demo_activities).delete()
        demo_activities.delete()
        AttendanceRecord.objects.filter(session__offering=offering).delete()
        AttendanceSession.objects.filter(offering=offering).delete()
        GradeSubmission.objects.filter(offering=offering, template_period=period).delete()
        if letter in {"A", "D"}:
            activity_components = components[:1]
        elif letter == "C":
            activity_components = components[:3]
        else:
            activity_components = components
        activities = []
        for component in activity_components:
            activity, _ = GradeActivity.objects.update_or_create(
                offering=offering,
                template_period=period,
                template_component=component,
                title=f"{self.PREFIX} {letter} {component.code}",
                defaults={
                    "tenant": offering.tenant,
                    "campus": offering.campus,
                    "total_score": Decimal("100.00"),
                    "activity_date": as_of_date,
                    "created_by_user": faculty,
                    "is_active": True,
                },
            )
            activities.append(activity)
        session, _ = AttendanceSession.objects.update_or_create(
            offering=offering,
            template_period=period,
            session_date=as_of_date,
            defaults={
                "tenant": offering.tenant,
                "campus": offering.campus,
                "title": f"{self.PREFIX} {letter} Attendance",
                "created_by_user": faculty,
                "is_active": True,
            },
        )
        for student in students[:complete_students]:
            for activity in activities:
                StudentActivityScore.objects.update_or_create(
                    activity=activity,
                    student=student,
                    defaults={
                        "raw_score": Decimal("80.00"),
                        "computed_score": Decimal("90.00"),
                        "encoded_by_user": faculty,
                        "remarks": f"{self.PREFIX} deterministic score",
                        "is_active": True,
                    },
                )
            AttendanceRecord.objects.update_or_create(
                session=session,
                student=student,
                defaults={
                    "tenant": offering.tenant,
                    "campus": offering.campus,
                    "status_code": AttendanceRecord.Status.PRESENT,
                    "recorded_by_user": faculty,
                    "remarks": f"{self.PREFIX} deterministic attendance",
                    "is_active": True,
                },
            )
        if submitted:
            GradeSubmission.objects.update_or_create(
                offering=offering,
                template_period=period,
                defaults={
                    "tenant": offering.tenant,
                    "campus": offering.campus,
                    "status": GradeSubmission.Status.SUBMITTED,
                    "submitted_by_user": faculty,
                    "submitted_at": timezone.now(),
                    "remarks": f"{self.PREFIX} submitted control",
                    "submission_snapshot_json": {"demo": True},
                },
            )

    def _backup_key(self, policy_key):
        return f"{self.PREFIX}-BACKUP-{policy_key.replace('FEATURE_', '')}"[:128]

    def _configure_policy(self, tenant):
        for key in self.POLICY_KEYS:
            backup_key = self._backup_key(key)
            if not SystemSetting.objects.filter(tenant=tenant, setting_key=backup_key).exists():
                current = SystemSetting.objects.filter(tenant=tenant, setting_key=key).first()
                payload = {"exists": bool(current)}
                if current:
                    payload.update({"value": current.setting_value, "type": current.value_type, "active": current.is_active})
                SystemSettingService.set(backup_key, payload, tenant_id=tenant.id, value_type="JSON")
        values = (
            (FeatureSettingsService.SUBMISSION_READINESS_EMAIL_ENABLED_KEY, True, "BOOL"),
            (FeatureSettingsService.SUBMISSION_READINESS_EMAIL_DAYS_BEFORE_KEY, 5, "INT"),
            (FeatureSettingsService.SUBMISSION_READINESS_EMAIL_THRESHOLD_KEY, 50, "INT"),
            (FeatureSettingsService.SUBMISSION_READINESS_EMAIL_ROLE_CODES_KEY, ["AREA_CHAIR"], "JSON"),
            (FeatureSettingsService.SUBMISSION_READINESS_EMAIL_SEND_EMPTY_KEY, False, "BOOL"),
            (FeatureSettingsService.SUBMISSION_READINESS_EMAIL_INCLUDE_LINK_KEY, True, "BOOL"),
            (FeatureSettingsService.SUBMISSION_READINESS_EMAIL_REPEAT_KEY, False, "BOOL"),
        )
        for key, value, value_type in values:
            SystemSettingService.set(key, value, tenant_id=tenant.id, value_type=value_type)

    def _restore_policy(self, tenant):
        for key in self.POLICY_KEYS:
            backup_key = self._backup_key(key)
            backup = SystemSetting.objects.filter(tenant=tenant, setting_key=backup_key).first()
            if backup is None:
                continue
            payload = json.loads(backup.setting_value)
            if payload.get("exists"):
                SystemSetting.objects.update_or_create(
                    tenant=tenant,
                    setting_key=key,
                    defaults={
                        "setting_value": payload.get("value", ""),
                        "value_type": payload.get("type", "STRING"),
                        "is_active": payload.get("active", True),
                    },
                )
            else:
                SystemSetting.objects.filter(tenant=tenant, setting_key=key).delete()
            backup.delete()

    def _reset(self, tenant):
        academic_year = AcademicYear.objects.filter(tenant=tenant, code=self.ACADEMIC_YEAR_CODE).first()
        offering_ids = list(
            CourseOffering.objects.filter(
                tenant=tenant, section__code__startswith=f"{self.PREFIX}-"
            ).values_list("id", flat=True)
        )
        if academic_year:
            SubmissionReadinessNotificationLog.objects.filter(tenant=tenant, academic_year=academic_year).delete()
        GradeSubmission.objects.filter(offering_id__in=offering_ids).delete()
        AttendanceRecord.objects.filter(session__offering_id__in=offering_ids).delete()
        AttendanceSession.objects.filter(offering_id__in=offering_ids).delete()
        StudentActivityScore.objects.filter(activity__offering_id__in=offering_ids).delete()
        GradeActivity.objects.filter(offering_id__in=offering_ids).delete()
        Enrollment.objects.filter(course_offering_id__in=offering_ids).delete()
        FacultyAssignment.objects.filter(offering_id__in=offering_ids).delete()
        GradingPeriodLock.objects.filter(tenant=tenant, academic_year=academic_year).delete() if academic_year else None
        removed_offerings = CourseOffering.objects.filter(id__in=offering_ids).count()
        CourseOffering.objects.filter(id__in=offering_ids).delete()
        Section.objects.filter(tenant=tenant, code__startswith=f"{self.PREFIX}-").delete()
        Student.objects.filter(tenant=tenant, student_no__startswith=f"{self.PREFIX}-").delete()
        courses = Course.objects.filter(tenant=tenant, code__startswith=f"{self.PREFIX}-")
        CourseTemplateAssignment.objects.filter(course__in=courses).delete()
        courses.delete()
        ActiveGradingPeriodSetting.objects.filter(tenant=tenant, term__code=self.TERM_CODE).delete()
        TenantTermGradingPeriod.objects.filter(tenant=tenant, term__code=self.TERM_CODE).delete()
        template = GradingTemplate.objects.filter(tenant=tenant, code=self.TEMPLATE_CODE).first()
        if template:
            GradingTemplateSubcomponent.objects.filter(template_component__template_period__template=template).delete()
            GradingTemplateComponent.objects.filter(template_period__template=template).delete()
            GradingTemplatePeriod.objects.filter(template=template).delete()
            template.delete()
        Term.objects.filter(tenant=tenant, code=self.TERM_CODE, academic_year__code=self.ACADEMIC_YEAR_CODE).delete()
        AcademicYear.objects.filter(tenant=tenant, code=self.ACADEMIC_YEAR_CODE).delete()
        demo_users = User.objects.filter(
            username__in=(*self.FACULTY_USERNAMES, self.HEAD_USERNAME),
            default_tenant=tenant,
        )
        UserRole.objects.filter(user__in=demo_users, tenant=tenant).delete()
        demo_users.delete()
        Program.objects.filter(tenant=tenant, code=self.PROGRAM_CODE).delete()
        Department.objects.filter(tenant=tenant, code=self.DEPARTMENT_CODE).delete()
        self._restore_policy(tenant)
        return f"{removed_offerings} offerings and owned dependent records"

    def _print_result(self, result):
        self.stdout.write(self.style.SUCCESS("Seeded Submission Readiness email demo data."))
        self.stdout.write(
            f"Tenant/Campus: {result['tenant'].code}/{result['campus'].code} | "
            f"AY/Term/Period: {result['academic_year'].code}/{result['term'].code}/{result['period'].code}"
        )
        self.stdout.write(
            f"As-of date: {result['as_of_date'].isoformat()} | Deadline: "
            f"{result['deadline'].strftime('%Y-%m-%d %H:%M %Z')} | Encoding lock: OPEN"
        )
        reused = result["reused_offerings"]
        self.stdout.write(
            f"Demo records: {len(self.SCENARIOS) - reused} offerings created, {reused} reused; "
            "2 faculty; 6 assignments; 18 students (3 per assignment)."
        )
        self.stdout.write(f"Academic Head: {result['head'].username} <{result['head'].email}>")
        self.stdout.write("Faculty | Course / Section | Scenario | Readiness | Status | Email")
        for row in result["rows"]:
            include = row.status != GradeSubmissionReadinessService.SUBMITTED and row.progress_percent < Decimal("50")
            self.stdout.write(
                f"{row.assignment.faculty_user.username} | {row.assignment.offering.course.code} / "
                f"{row.assignment.offering.section.code} | {result['scenario_labels'][row.assignment.id]} | "
                f"{row.progress_percent}% | {row.status_label} | {'Yes' if include else 'No'}"
            )
        self.stdout.write(
            f"Dry run: python manage.py send_submission_readiness_alerts --as-of-date "
            f"{result['as_of_date'].isoformat()} --tenant-id {result['tenant'].id} --dry-run"
        )
        self.stdout.write(
            f"Controlled send: python manage.py send_submission_readiness_alerts --as-of-date "
            f"{result['as_of_date'].isoformat()} --tenant-id {result['tenant'].id}"
        )
        self.stdout.write(
            f"Reset: python manage.py seed_submission_readiness_email_demo --tenant {result['tenant'].code} "
            "--confirm-demo-data --reset"
        )

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import CourseOffering, FacultyAssignment, Section
from apps.core.services.settings import SystemSettingService
from apps.rbac.models import Permission, UserPermission, UserRole
from apps.tenants.models import Program

from .automatic_workflow import FacultyContributionPreparationService
from .forms import ExaminationCycleConfigurationForm
from .models import (
    CourseExamConfiguration,
    CycleCourse,
    CycleCourseOffering,
    ExaminationCycle,
    FacultyContribution,
)
from .services import (
    CourseExamConfigurationService,
    ExaminationCycleConfigurationService,
)
from .stage4_test_support import Stage4TestCase


class AutomaticPreparationTests(Stage4TestCase):
    def setUp(self):
        super().setUp()
        Permission.objects.get_or_create(
            code="faculty_portal.access",
            defaults={
                "module": "faculty_portal",
                "action": "access",
                "description": "Faculty Portal",
                "is_active": True,
            },
        )
        self.bulk_manager = self.make_user(
            "bulk-manager",
            None,
            (
                "admin_portal.access",
                "departmental_exams.manage_exam_generation",
            ),
            campus=self.campus,
        )

    def make_automatic_cycle(self, *, status="OPEN", suffix="bulk", coverage="Default coverage"):
        cycle = self.make_cycle(
            status=status,
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=self.future_deadline(),
            default_coverage=coverage,
            scope_suffix=suffix,
        )
        cycle.processing_mode = ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        cycle.save(update_fields=["processing_mode", "updated_at"])
        return cycle

    def make_automatic_course(
        self,
        cycle,
        *,
        code,
        coverage="Course coverage",
        deadline=None,
        workflow="DRAFT",
        opened_at=None,
    ):
        parent = self.make_course(cycle=cycle, department=None, code=code)
        configuration = self.make_configuration(
            parent,
            workflow=workflow,
            opened_at=opened_at,
            coverage=coverage,
            coverage_source="OVERRIDE" if coverage else None,
            deadline=deadline or self.future_deadline(),
        )
        return parent, configuration

    def make_faculty_assignment(self, parent, *, username):
        faculty = self.make_user(
            username,
            self.department,
            ("faculty_portal.access",),
            campus=self.campus,
        )
        offering = parent.offering_snapshots.select_related("offering").get().offering
        assignment = FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=offering,
            faculty_user=faculty,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
            accepted_by=self.admin,
            is_active=True,
        )
        return faculty, assignment

    def prepare(self, cycle, *, actor=None):
        return FacultyContributionPreparationService.prepare(
            cycle_id=cycle.id,
            tenant_id=self.tenant.id,
            actor=actor or self.bulk_manager,
            expected_updated_at=ExaminationCycleConfigurationService.transition_token(cycle),
        )

    def test_default_coverage_form_save_and_apply_defaults_preserve_exceptions_and_history(self):
        cycle = self.make_automatic_cycle(status="DRAFT", suffix="coverage", coverage="")
        eligible, eligible_configuration = self.make_automatic_course(
            cycle, code="COV-ELIGIBLE", coverage=""
        )
        override, override_configuration = self.make_automatic_course(
            cycle, code="COV-OVERRIDE", coverage="Course-specific coverage"
        )
        historical, historical_configuration = self.make_automatic_course(
            cycle,
            code="COV-HISTORY",
            coverage="Historical coverage",
            workflow="CLOSED",
            opened_at=timezone.now(),
        )
        new_parent = self.make_course(cycle=cycle, department=None, code="COV-NEW")
        default_coverage = "Coverage follows the approved course syllabus."
        form = ExaminationCycleConfigurationForm(
            {
                "processing_mode": ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION,
                "default_questions_required_per_faculty": 50,
                "default_final_item_count": 50,
                "default_contribution_deadline": cycle.default_contribution_deadline.strftime("%Y-%m-%dT%H:%M"),
                "default_coverage": default_coverage,
                "contributor_instructions": "Use the approved format.",
                "expected_updated_at": ExaminationCycleConfigurationService.transition_token(cycle),
                "reason": "",
            },
            instance=cycle,
        )
        self.assertTrue(form.is_valid(), form.errors)
        ExaminationCycleConfigurationService.save_cycle_configuration(
            cycle_id=cycle.id,
            tenant_id=self.tenant.id,
            user=self.manager,
            expected_updated_at=form.cleaned_data["expected_updated_at"],
            default_questions_required_per_faculty=form.cleaned_data[
                "default_questions_required_per_faculty"
            ],
            default_final_item_count=form.cleaned_data["default_final_item_count"],
            default_contribution_deadline=form.cleaned_data[
                "default_contribution_deadline"
            ],
            default_coverage=form.cleaned_data["default_coverage"],
            contributor_instructions=form.cleaned_data["contributor_instructions"],
            processing_mode=form.cleaned_data["processing_mode"],
        )
        cycle.refresh_from_db()
        eligible_configuration.refresh_from_db()
        override_configuration.refresh_from_db()
        historical_configuration.refresh_from_db()
        created_configuration = CourseExamConfiguration.objects.get(
            cycle_course=new_parent
        )
        self.assertEqual(cycle.default_coverage, default_coverage)
        self.assertEqual(eligible_configuration.coverage, default_coverage)
        self.assertEqual(eligible_configuration.coverage_source, "DEFAULT")
        self.assertEqual(created_configuration.coverage, default_coverage)
        self.assertEqual(created_configuration.coverage_source, "DEFAULT")
        self.assertEqual(override_configuration.coverage, "Course-specific coverage")
        self.assertEqual(override_configuration.coverage_source, "OVERRIDE")
        self.assertEqual(historical_configuration.coverage, "Historical coverage")

    def test_blank_default_coverage_remains_unready_and_open_cycle_change_requires_reason(self):
        cycle = self.make_automatic_cycle(status="DRAFT", suffix="coverage-blank", coverage="")
        parent = self.make_course(cycle=cycle, department=None, code="COV-BLANK")
        ExaminationCycleConfigurationService.save_cycle_configuration(
            cycle_id=cycle.id,
            tenant_id=self.tenant.id,
            user=self.manager,
            expected_updated_at=ExaminationCycleConfigurationService.transition_token(cycle),
            default_questions_required_per_faculty=50,
            default_final_item_count=51,
            default_contribution_deadline=cycle.default_contribution_deadline,
            default_coverage="",
            contributor_instructions="",
            processing_mode=cycle.processing_mode,
        )
        configuration = CourseExamConfiguration.objects.get(cycle_course=parent)
        self.assertEqual(configuration.coverage, "")
        self.assertIsNone(configuration.coverage_source)
        cycle.status = ExaminationCycle.Status.OPEN
        cycle.save(update_fields=["status", "updated_at"])
        token = ExaminationCycleConfigurationService.transition_token(cycle)
        with self.assertRaisesRegex(ValidationError, "administrative reason"):
            ExaminationCycleConfigurationService.save_cycle_configuration(
                cycle_id=cycle.id,
                tenant_id=self.tenant.id,
                user=self.manager,
                expected_updated_at=token,
                default_questions_required_per_faculty=50,
                default_final_item_count=50,
                default_contribution_deadline=cycle.default_contribution_deadline,
                default_coverage="New default",
                contributor_instructions="",
                processing_mode=cycle.processing_mode,
                reason="",
            )
        cycle.refresh_from_db()
        self.assertEqual(cycle.default_coverage, "")

    def test_bulk_prepare_happy_path_idempotency_and_no_manual_assignments(self):
        cycle = self.make_automatic_cycle(suffix="happy")
        courses = []
        for index in range(2):
            parent, configuration = self.make_automatic_course(
                cycle, code=f"READY-{index}"
            )
            faculty, _assignment = self.make_faculty_assignment(
                parent, username=f"ready-faculty-{index}"
            )
            courses.append((parent, configuration, faculty))
        first = self.prepare(cycle)
        self.assertEqual(first["total_considered"], 2)
        self.assertEqual(first["contributions_opened"], 2)
        self.assertEqual(first["rosters_initialized"], 2)
        self.assertEqual(first["needs_attention_count"], 0)
        for parent, configuration, faculty in courses:
            configuration.refresh_from_db()
            self.assertEqual(configuration.workflow_status, "OPEN")
            self.assertIsNotNone(configuration.contributor_roster_initialized_at)
            self.assertTrue(
                FacultyContribution.objects.filter(
                    cycle_course=parent, faculty_user=faculty
                ).exists()
            )
            self.assertIsNone(parent.responsible_department_id)
            self.assertIsNone(parent.reviewer_id)
        contribution_ids = list(
            FacultyContribution.objects.order_by("id").values_list("id", flat=True)
        )
        second = self.prepare(cycle)
        self.assertEqual(second["contributions_opened"], 0)
        self.assertEqual(second["rosters_initialized"], 0)
        self.assertEqual(second["already_prepared_count"], 2)
        self.assertEqual(
            list(FacultyContribution.objects.order_by("id").values_list("id", flat=True)),
            contribution_ids,
        )

    def test_bulk_exceptions_are_isolated_and_exempt_course_is_not_considered(self):
        cycle = self.make_automatic_cycle(suffix="exceptions")
        ready, _ready_configuration = self.make_automatic_course(
            cycle, code="A-READY"
        )
        self.make_faculty_assignment(ready, username="exception-ready-faculty")
        missing, missing_configuration = self.make_automatic_course(
            cycle, code="B-MISSING", coverage=""
        )
        self.make_faculty_assignment(missing, username="exception-missing-faculty")
        expired, expired_configuration = self.make_automatic_course(
            cycle,
            code="C-EXPIRED",
            deadline=timezone.now() - timezone.timedelta(minutes=1),
        )
        self.make_faculty_assignment(expired, username="exception-expired-faculty")
        no_assignment, no_assignment_configuration = self.make_automatic_course(
            cycle, code="D-NONE"
        )
        exempt, exempt_configuration = self.make_automatic_course(
            cycle, code="E-EXEMPT"
        )
        CycleCourse.objects.filter(pk=exempt.pk).update(
            inclusion_status=CycleCourse.InclusionStatus.EXEMPT,
            exemption_category=CycleCourse.ExemptionCategory.OTHER_OUTPUT_BASED,
            exemption_reason="Approved non-questionnaire assessment.",
            exemption_changed_by=self.admin,
            exemption_changed_at=timezone.now(),
        )
        result = self.prepare(cycle)
        self.assertEqual(result["total_considered"], 4)
        self.assertEqual(result["contributions_opened"], 1)
        self.assertEqual(result["needs_attention_count"], 3)
        self.assertEqual(
            {item["reason"] for item in result["needs_attention"]},
            {
                "Coverage not configured",
                "Effective contribution deadline is not in the future",
                "No qualifying teaching assignments found",
            },
        )
        for configuration in (
            missing_configuration,
            expired_configuration,
            no_assignment_configuration,
            exempt_configuration,
        ):
            configuration.refresh_from_db()
            self.assertEqual(configuration.workflow_status, "DRAFT")
            self.assertIsNone(configuration.contributor_roster_initialized_at)

    def test_existing_roster_is_not_synchronized_and_closed_state_is_preserved(self):
        cycle = self.make_automatic_cycle(suffix="preserve")
        parent, configuration = self.make_automatic_course(cycle, code="ROSTER")
        first_faculty, first_assignment = self.make_faculty_assignment(
            parent, username="roster-first"
        )
        self.prepare(cycle)
        first_assignment.is_active = False
        first_assignment.save(update_fields=["is_active", "updated_at"])
        second_faculty, _second_assignment = self.make_faculty_assignment(
            parent, username="roster-second"
        )
        closed, closed_configuration = self.make_automatic_course(
            cycle,
            code="CLOSED",
            workflow="CLOSED",
            opened_at=timezone.now(),
        )
        result = self.prepare(cycle)
        self.assertEqual(result["already_prepared_count"], 1)
        self.assertEqual(result["preserved_count"], 1)
        self.assertTrue(
            FacultyContribution.objects.filter(
                cycle_course=parent, faculty_user=first_faculty
            ).exists()
        )
        self.assertFalse(
            FacultyContribution.objects.filter(
                cycle_course=parent, faculty_user=second_faculty
            ).exists()
        )
        closed_configuration.refresh_from_db()
        self.assertEqual(closed_configuration.workflow_status, "CLOSED")

    def test_final_preparation_does_not_modify_midterm_submitted_history(self):
        midterm = self.make_automatic_cycle(suffix="periods")
        midterm_parent, _midterm_configuration = self.make_automatic_course(
            midterm, code="PERIOD"
        )
        faculty, _assignment = self.make_faculty_assignment(
            midterm_parent, username="period-faculty"
        )
        self.prepare(midterm)
        midterm_contribution = FacultyContribution.objects.get(
            cycle_course=midterm_parent,
            faculty_user=faculty,
        )
        FacultyContribution.objects.filter(pk=midterm_contribution.pk).update(
            status=FacultyContribution.Status.SUBMITTED,
            submitted_at=timezone.now(),
        )
        midterm_contribution.refresh_from_db()
        midterm_revision = midterm_contribution.revision

        final = ExaminationCycle.objects.create(
            tenant=self.tenant,
            academic_year=midterm.academic_year,
            term=midterm.term,
            exam_period=ExaminationCycle.ExamPeriod.FINAL,
            status=ExaminationCycle.Status.OPEN,
            processing_mode=ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION,
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=self.future_deadline(),
            default_coverage="Final coverage",
            created_by=self.admin,
        )
        final_parent = CycleCourse.objects.create(
            cycle=final,
            course=midterm_parent.course,
            responsible_department=None,
        )
        snapshot = midterm_parent.offering_snapshots.select_related("offering").get()
        CycleCourseOffering.objects.create(
            cycle_course=final_parent,
            offering=snapshot.offering,
            campus=snapshot.campus,
        )
        self.make_configuration(
            final_parent,
            coverage="Final course coverage",
            coverage_source="OVERRIDE",
        )
        result = self.prepare(final)
        self.assertEqual(result["contributions_opened"], 1)
        midterm_contribution.refresh_from_db()
        self.assertEqual(midterm_contribution.status, FacultyContribution.Status.SUBMITTED)
        self.assertEqual(midterm_contribution.revision, midterm_revision)
        self.assertTrue(
            FacultyContribution.objects.filter(
                cycle_course=final_parent,
                faculty_user=faculty,
                status=FacultyContribution.Status.DRAFT,
            ).exists()
        )

    def test_per_course_failure_does_not_roll_back_later_course(self):
        cycle = self.make_automatic_cycle(suffix="failure")
        failing, _ = self.make_automatic_course(cycle, code="A-FAIL")
        ready, ready_configuration = self.make_automatic_course(cycle, code="B-READY")
        self.make_faculty_assignment(failing, username="failure-first")
        self.make_faculty_assignment(ready, username="failure-second")
        original = CourseExamConfigurationService.open_for_contribution

        def isolate_failure(**kwargs):
            if kwargs["cycle_course_id"] == failing.id:
                raise ValidationError("Safe simulated lifecycle failure.")
            return original(**kwargs)

        with patch.object(
            CourseExamConfigurationService,
            "open_for_contribution",
            side_effect=isolate_failure,
        ):
            result = self.prepare(cycle)
        ready_configuration.refresh_from_db()
        self.assertEqual(result["needs_attention_count"], 1)
        self.assertEqual(result["contributions_opened"], 1)
        self.assertEqual(ready_configuration.workflow_status, "OPEN")

    def test_rbac_feature_and_manual_mode_fail_closed_without_mutation(self):
        cycle = self.make_automatic_cycle(suffix="rbac")
        parent, configuration = self.make_automatic_course(cycle, code="RBAC")
        self.make_faculty_assignment(parent, username="rbac-faculty")
        with self.assertRaises(PermissionDenied):
            self.prepare(cycle, actor=self.configurer)

        role_rows = UserRole.objects.filter(user=self.bulk_manager)
        role_rows.update(is_active=False)
        with self.assertRaises(PermissionDenied):
            self.prepare(cycle)
        role_rows.update(is_active=True)

        UserPermission.objects.create(
            user=self.bulk_manager,
            permission=Permission.objects.get(
                code="departmental_exams.manage_exam_generation"
            ),
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.campus,
        )
        with self.assertRaises(PermissionDenied):
            self.prepare(cycle)
        UserPermission.objects.filter(user=self.bulk_manager).delete()

        SystemSettingService.set(
            "FEATURE_DEPARTMENTAL_EXAM_BUILDER_ENABLED",
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )
        with self.assertRaises(PermissionDenied):
            self.prepare(cycle)
        configuration.refresh_from_db()
        self.assertEqual(configuration.workflow_status, "DRAFT")
        self.assertFalse(FacultyContribution.objects.filter(cycle_course=parent).exists())

        SystemSettingService.set(
            "FEATURE_DEPARTMENTAL_EXAM_BUILDER_ENABLED",
            True,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )
        cycle.processing_mode = ExaminationCycle.ProcessingMode.MANUAL_REVIEW
        cycle.save(update_fields=["processing_mode", "updated_at"])
        with self.assertRaisesRegex(ValidationError, "Automatic Generation"):
            FacultyContributionPreparationService.prepare(
                cycle_id=cycle.id,
                tenant_id=self.tenant.id,
                actor=self.bulk_manager,
            )

    def test_empty_cycle_requires_tenant_wide_management_authority(self):
        cycle = self.make_automatic_cycle(suffix="empty-denied")
        with self.assertRaises(PermissionDenied):
            self.prepare(cycle, actor=self.configurer)

        self.client.force_login(self.configurer)
        response = self.client.get(
            reverse(
                "departmental_exams:prepare_faculty_contributions",
                args=[cycle.id],
            )
        )
        self.assertEqual(response.status_code, 403)

    def test_empty_cycle_tenant_manager_get_and_post_are_safe_no_ops(self):
        cycle = self.make_automatic_cycle(suffix="empty-authorized")
        UserRole.objects.filter(user=self.bulk_manager).update(campus=None)
        UserPermission.objects.create(
            user=self.bulk_manager,
            permission=Permission.objects.get(code="admin_portal.access"),
            grant_type=UserPermission.GrantType.ALLOW,
            tenant=self.tenant,
            campus=self.campus,
        )
        result = self.prepare(cycle)
        self.assertEqual(result["total_considered"], 0)
        self.assertEqual(result["successfully_prepared"], 0)
        self.assertEqual(result["contributions_opened"], 0)

        self.client.force_login(self.bulk_manager)
        url = reverse(
            "departmental_exams:prepare_faculty_contributions",
            args=[cycle.id],
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        response = self.client.post(
            url,
            {
                "expected_updated_at": (
                    ExaminationCycleConfigurationService.transition_token(cycle)
                )
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_considered"], 0)

    def test_empty_cycle_feature_wrong_tenant_and_direct_deny_fail_closed(self):
        cycle = self.make_automatic_cycle(suffix="empty-boundaries")
        UserRole.objects.filter(user=self.bulk_manager).update(campus=None)
        UserPermission.objects.create(
            user=self.bulk_manager,
            permission=Permission.objects.get(code="admin_portal.access"),
            grant_type=UserPermission.GrantType.ALLOW,
            tenant=self.tenant,
            campus=self.campus,
        )
        permission = Permission.objects.get(
            code="departmental_exams.manage_exam_generation"
        )
        UserPermission.objects.create(
            user=self.bulk_manager,
            permission=permission,
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=None,
        )
        with self.assertRaises(PermissionDenied):
            self.prepare(cycle)
        UserPermission.objects.filter(
            user=self.bulk_manager,
            permission=permission,
        ).delete()

        SystemSettingService.set(
            "FEATURE_DEPARTMENTAL_EXAM_BUILDER_ENABLED",
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )
        with self.assertRaises(PermissionDenied):
            self.prepare(cycle)
        self.client.force_login(self.bulk_manager)
        response = self.client.get(
            reverse(
                "departmental_exams:prepare_faculty_contributions",
                args=[cycle.id],
            )
        )
        self.assertEqual(response.status_code, 403)

        SystemSettingService.set(
            "FEATURE_DEPARTMENTAL_EXAM_BUILDER_ENABLED",
            True,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )
        SystemSettingService.set(
            "FEATURE_DEPARTMENTAL_EXAM_BUILDER_ENABLED",
            True,
            tenant_id=self.other_tenant.id,
            value_type="BOOL",
        )
        with self.assertRaises(ExaminationCycle.DoesNotExist):
            FacultyContributionPreparationService.prepare(
                cycle_id=cycle.id,
                tenant_id=self.other_tenant.id,
                actor=self.bulk_manager,
            )

    def test_wrong_tenant_is_not_resolved_or_mutated(self):
        cycle = self.make_automatic_cycle(suffix="wrong-tenant")
        parent, configuration = self.make_automatic_course(cycle, code="TENANT")
        self.make_faculty_assignment(parent, username="tenant-faculty")
        SystemSettingService.set(
            "FEATURE_DEPARTMENTAL_EXAM_BUILDER_ENABLED",
            True,
            tenant_id=self.other_tenant.id,
            value_type="BOOL",
        )
        with self.assertRaises(ExaminationCycle.DoesNotExist):
            FacultyContributionPreparationService.prepare(
                cycle_id=cycle.id,
                tenant_id=self.other_tenant.id,
                actor=self.bulk_manager,
            )
        configuration.refresh_from_db()
        self.assertEqual(configuration.workflow_status, "DRAFT")

    def test_incomplete_participating_campus_scope_is_denied(self):
        cycle = self.make_automatic_cycle(suffix="campus")
        parent, configuration = self.make_automatic_course(cycle, code="CAMPUS")
        program = Program.objects.create(
            tenant=self.tenant,
            campus=self.other_campus,
            department=self.other_department,
            code="BULK-NORTH",
            name="Bulk North",
        )
        section = Section.objects.create(
            tenant=self.tenant,
            campus=self.other_campus,
            department=self.other_department,
            program=program,
            code="BULK-NORTH",
            name="Bulk North",
        )
        offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.other_campus,
            department=self.other_department,
            program=program,
            academic_year=cycle.academic_year,
            term=cycle.term,
            course=parent.course,
            section=section,
        )
        CycleCourseOffering.objects.create(
            cycle_course=parent,
            offering=offering,
            campus=self.other_campus,
        )
        with self.assertRaises(PermissionDenied):
            self.prepare(cycle)
        configuration.refresh_from_db()
        self.assertEqual(configuration.workflow_status, "DRAFT")

    def test_draft_cycle_message_and_cycle_level_ui_navigation(self):
        cycle = self.make_automatic_cycle(status="DRAFT", suffix="ui")
        parent, _configuration = self.make_automatic_course(cycle, code="UI")
        self.make_faculty_assignment(parent, username="ui-faculty")
        client = Client()
        client.force_login(self.admin)
        list_response = client.get(
            reverse("departmental_exams:cycle_course_list", args=[cycle.id])
        )
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(
            list_response.content.decode().count("Prepare Faculty Contributions"),
            1,
        )
        url = reverse(
            "departmental_exams:prepare_faculty_contributions", args=[cycle.id]
        )
        response = client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Open the examination cycle before preparing faculty contributions.",
        )
        post_response = client.post(
            url,
            {
                "expected_updated_at": ExaminationCycleConfigurationService.transition_token(
                    cycle
                )
            },
        )
        self.assertEqual(post_response.status_code, 400)
        self.assertContains(post_response, "Departmental Exam Builder", status_code=400)
        cycle.status = ExaminationCycle.Status.OPEN
        cycle.save(update_fields=["status", "updated_at"])
        result_response = client.post(
            url,
            {
                "expected_updated_at": ExaminationCycleConfigurationService.transition_token(
                    cycle
                )
            },
        )
        self.assertEqual(result_response.status_code, 200)
        self.assertContains(result_response, "Faculty Contribution Preparation Result")
        self.assertContains(result_response, "Departmental Exam Builder")

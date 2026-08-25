import re
from collections import Counter
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import CourseOffering, Section
from apps.auditlog.models import AuditLog
from apps.rbac.models import Permission, Role, RolePermission, UserPermission, UserRole

from .automatic_workflow import AutomaticExamDeadlineService
from .automatic_generation_readiness import AutomaticGenerationReadinessReport
from .blueprint_services import ContributorRosterReadinessService
from .contribution_selectors import ContributionMonitoringSelector
from .contribution_services import ContributionRosterService
from .generation_algorithms import IdentitySelectionResult
from .generation_readiness import Stage6ReadinessService
from .models import (
    CourseExamConfiguration,
    CycleCourse,
    CycleCourseOffering,
    ExamGenerationRevision,
    ExaminationCycle,
    FacultyContribution,
    Question,
    QuestionImportBatch,
)
from .stage4_test_support import Stage4TestCase
from .tests_stage6_lifecycle import Stage6FixtureMixin


class AutomaticGenerationReadinessReportTests(Stage6FixtureMixin, Stage4TestCase):
    def setUp(self):
        super().setUp()
        self.generation_manager = self._make_generation_manager()
        self.client = Client()
        self.client.force_login(self.generation_manager)

    def _make_generation_manager(self):
        user = get_user_model().objects.create_user(
            "readiness-manager",
            "readiness-manager@example.edu",
            "Pass123!",
            default_tenant=self.tenant,
            default_campus=self.campus,
            privacy_consent_version="2026-03",
            privacy_consent_at=timezone.now(),
        )
        role = Role.objects.create(
            code="AUTOMATIC_READINESS_MANAGER",
            name="Automatic Readiness Manager",
        )
        for code in (
            "admin_portal.access",
            "departmental_exams.manage_exam_generation",
        ):
            RolePermission.objects.create(
                role=role, permission=Permission.objects.get(code=code)
            )
        UserRole.objects.create(
            user=user,
            role=role,
            tenant=self.tenant,
            campus=None,
            department=None,
        )
        UserPermission.objects.create(
            user=user,
            permission=Permission.objects.get(code="admin_portal.access"),
            grant_type=UserPermission.GrantType.ALLOW,
            tenant=self.tenant,
            campus=self.campus,
        )
        return user

    @staticmethod
    def _add_quota_questions(contribution, quota):
        base_difficulties = (
            [Question.Difficulty.EASY] * 15
            + [Question.Difficulty.MODERATE] * 25
            + [Question.Difficulty.DIFFICULT] * 10
        )
        extra = [
            Question.Difficulty.EASY,
            Question.Difficulty.MODERATE,
            Question.Difficulty.DIFFICULT,
        ]
        difficulties = base_difficulties + [
            extra[index % len(extra)] for index in range(quota - len(base_difficulties))
        ]
        Question.objects.bulk_create(
            [
                Question(
                    contribution=contribution,
                    question_text=f"Readiness confidential question {contribution.id}-{position}",
                    choice_a=f"Private A {position}",
                    choice_b=f"Private B {position}",
                    choice_c=f"Private C {position}",
                    choice_d=f"Private D {position}",
                    correct_answer="A",
                    difficulty=difficulty,
                    position=position,
                    revision=1,
                )
                for position, difficulty in enumerate(difficulties, start=1)
            ]
        )

    def _automatic_course(
        self,
        *,
        campus_codes=("CUBAO", "FAIRVIEW", "TAYTAY"),
        quota=50,
        quota_source="DEFAULT",
        submitted_codes=None,
    ):
        parent, configuration, campuses, offerings = self.make_stage6_open_course(
            campus_codes=campus_codes
        )
        cycle = parent.cycle
        cycle.processing_mode = ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        cycle.default_questions_required_per_faculty = quota
        cycle.save(
            update_fields=[
                "processing_mode",
                "default_questions_required_per_faculty",
                "updated_at",
            ]
        )
        CourseExamConfiguration.objects.filter(pk=configuration.pk).update(
            questions_required_per_faculty=quota,
            questions_required_per_faculty_source=quota_source,
        )
        configuration.refresh_from_db()
        parent.cycle = cycle
        for code in campus_codes:
            self.add_faculty_source(
                parent=parent,
                campus=campuses[code],
                offering=offerings[code],
                suffix=f"readiness-{code.lower()}",
            )
        ContributionRosterService.initialize(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            actor=self.generation_manager,
        )
        submitted_codes = set(campus_codes if submitted_codes is None else submitted_codes)
        for contribution in FacultyContribution.objects.filter(
            cycle_course=parent
        ).select_related("source_campus"):
            self._add_quota_questions(contribution, quota)
            if contribution.source_campus.code in submitted_codes:
                contribution.status = FacultyContribution.Status.SUBMITTED
                contribution.submitted_at = timezone.now()
                contribution.save(
                    update_fields=["status", "submitted_at", "updated_at"]
                )
        configuration.refresh_from_db()
        return parent, configuration, campuses, offerings

    def _exempt_automatic_course(self, *, label, campus_codes=("CUBAO",)):
        parent, configuration, campuses, offerings = self.make_stage6_open_course(
            campus_codes=campus_codes
        )
        parent.cycle.processing_mode = (
            ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        )
        parent.cycle.save(update_fields=["processing_mode", "updated_at"])
        parent.course.title = f"Automatic Exempt {label}"
        parent.course.save(update_fields=["title", "updated_at"])
        CycleCourse.objects.filter(pk=parent.pk).update(
            inclusion_status=CycleCourse.InclusionStatus.EXEMPT,
            exemption_category=CycleCourse.ExemptionCategory.PRACTICUM_OJT,
            exemption_reason="Approved output-based exemption.",
            exemption_changed_by=self.generation_manager,
            exemption_changed_at=timezone.now(),
        )
        parent.refresh_from_db()
        parent.cycle.refresh_from_db()
        parent.course.refresh_from_db()
        return parent, configuration, campuses, offerings

    def _mixed_mode_monitoring_courses(self, *, suffix):
        manual_cycle = self.make_cycle(scope_suffix=f"{suffix}-manual")
        manual = self.make_course(cycle=manual_cycle, code=f"{suffix}-MANUAL")
        self.make_configuration(manual)
        manual.course.title = f"{suffix} Manual Review"
        manual.course.save(update_fields=["title", "updated_at"])

        included_cycle = self.make_cycle(scope_suffix=f"{suffix}-included")
        included_cycle.processing_mode = (
            ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        )
        included_cycle.save(update_fields=["processing_mode", "updated_at"])
        included = self.make_course(
            cycle=included_cycle,
            code=f"{suffix}-AUTO-INCLUDED",
        )
        self.make_configuration(included)
        included.course.title = f"{suffix} Automatic Included"
        included.course.save(update_fields=["title", "updated_at"])

        exempt_cycle = self.make_cycle(scope_suffix=f"{suffix}-exempt")
        exempt_cycle.processing_mode = (
            ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        )
        exempt_cycle.save(update_fields=["processing_mode", "updated_at"])
        exempt = self.make_course(
            cycle=exempt_cycle,
            code=f"{suffix}-AUTO-EXEMPT",
        )
        self.make_configuration(exempt)
        CycleCourse.objects.filter(pk=exempt.pk).update(
            inclusion_status=CycleCourse.InclusionStatus.EXEMPT,
            exemption_category=CycleCourse.ExemptionCategory.PRACTICUM_OJT,
            exemption_reason="Approved mixed-mode regression exemption.",
            exemption_changed_by=self.generation_manager,
            exemption_changed_at=timezone.now(),
        )
        exempt.refresh_from_db()
        exempt.course.title = f"{suffix} Automatic Exempt"
        exempt.course.save(update_fields=["title", "updated_at"])
        return manual, included, exempt

    def _screen(self, parent, **params):
        values = {"cycle": parent.cycle_id, **params}
        with patch(
            "apps.departmental_exams.generation_readiness.solve_automatic_identity_aware_two_sets",
            return_value=self._feasible_selection(),
        ):
            return self.client.get(
                reverse("departmental_exams:automatic_generation_readiness"), values
            )

    @staticmethod
    def _feasible_selection():
        return IdentitySelectionResult(
            feasible=True,
            limit_hit=False,
            states_explored=1,
            set_a_block_ids=(),
            set_b_block_ids=(),
            overlap=0,
            proportional_score=0,
            contributors_represented=0,
            squared_contributor_concentration=0,
        )

    def test_missing_configuration_is_blocked_on_screen_print_and_build_without_write(self):
        cycle = self.make_cycle(
            status=ExaminationCycle.Status.OPEN,
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=self.future_deadline(),
        )
        cycle.processing_mode = ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        cycle.save(update_fields=["processing_mode", "updated_at"])
        parent = self.make_course(cycle=cycle, code="READINESS-NO-CONFIG")
        configuration_count = CourseExamConfiguration.objects.count()

        report = AutomaticGenerationReadinessReport(
            tenant_id=self.tenant.id,
            user=self.generation_manager,
            params={"cycle": str(cycle.id)},
        ).build()
        self.assertEqual(len(report["rows"]), 1)
        direct_row = report["rows"][0]
        self.assertEqual(direct_row["cycle_course"].id, parent.id)
        self.assertEqual(direct_row["generation_status"], "BLOCKED")
        self.assertIn("Configure the course examination.", direct_row["action_items"])
        self.assertIsNone(direct_row["configuration"])
        self.assertEqual(
            direct_row["contribution_progress"]["submitted_question_volume"], 0
        )
        self.assertIsNone(
            direct_row["contribution_progress"]["expected_monitoring_volume"]
        )

        screen = self.client.get(
            reverse("departmental_exams:automatic_generation_readiness"),
            {"cycle": cycle.id},
        )
        printed = self.client.get(
            reverse("departmental_exams:automatic_generation_readiness_print"),
            {"cycle": cycle.id},
        )

        headers = (
            "No.",
            "Authorized Course",
            "Campuses Offered",
            "Final Exam Items",
            "Contribution Progress",
            "Automatic Exam Generation Status",
            "Action Needed",
        )
        self.assertEqual(screen.status_code, 200)
        self.assertEqual(printed.status_code, 200)
        self.assertContains(screen, parent.course.code)
        self.assertContains(printed, parent.course.code)
        self.assertContains(screen, "BLOCKED")
        self.assertContains(printed, "BLOCKED")
        self.assertContains(screen, "Configure the course examination.")
        self.assertContains(printed, "Configure the course examination.")
        self.assertEqual(
            len(re.findall(r'<th\b[^>]*scope="col"', screen.content.decode())),
            7,
        )
        self.assertEqual(len(re.findall(r"<th\b", printed.content.decode())), 7)
        for header in headers:
            self.assertContains(screen, header)
            self.assertContains(printed, header)
        self.assertEqual(
            CourseExamConfiguration.objects.count(), configuration_count
        )

    def test_layout_numbering_summary_and_filtered_scope_match_screen_print(self):
        cycle = self.make_cycle(
            status=ExaminationCycle.Status.OPEN,
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=self.future_deadline(),
            scope_suffix="readiness-layout",
        )
        cycle.processing_mode = ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        cycle.save(update_fields=["processing_mode", "updated_at"])
        first = self.make_course(cycle=cycle, code="LAYOUT-A")
        second = self.make_course(cycle=cycle, code="LAYOUT-B")

        screen = self.client.get(
            reverse("departmental_exams:automatic_generation_readiness"),
            {"cycle": cycle.id},
        )
        printed = self.client.get(
            reverse("departmental_exams:automatic_generation_readiness_print"),
            {"cycle": cycle.id},
        )

        self.assertEqual(screen.status_code, 200)
        self.assertEqual(printed.status_code, 200)
        expected_counts = Counter(
            row["generation_status"] for row in screen.context["rows"]
        )
        self.assertEqual(screen.context["row_count"], 2)
        self.assertEqual(screen.context["report_summary"]["total"], 2)
        self.assertEqual(
            screen.context["report_summary"], printed.context["report_summary"]
        )
        self.assertEqual(
            [item["label"] for item in screen.context["report_summary"]["items"]],
            [label for label, _status in AutomaticGenerationReadinessReport.SUMMARY_STATUSES],
        )
        self.assertEqual(
            {
                item["status"]: item["count"]
                for item in screen.context["report_summary"]["items"]
            },
            {
                status: expected_counts[status]
                for _label, status in AutomaticGenerationReadinessReport.SUMMARY_STATUSES
            },
        )

        for response, expected_font in ((screen, "font-size: 12px"), (printed, "font-size: 10.5pt")):
            body = response.content.decode()
            self.assertEqual(
                re.findall(r'<td class="readiness-row-number">(\d+)</td>', body),
                ["1", "2"],
            )
            self.assertIn("Report Summary", body)
            self.assertIn("Total Authorized Courses", body)
            self.assertIn('data-status="BLOCKED"><dt>Blocked</dt><dd>2</dd>', body)
            self.assertIn("readiness-final-items", body)
            self.assertIn("width: 8%; text-align: center;", body)
            self.assertIn(
                "th.readiness-final-items { white-space: normal; }", body
            )
            self.assertIn(
                "td.readiness-final-items { white-space: nowrap; }", body
            )
            self.assertNotIn(
                "th.readiness-final-items { white-space: nowrap; }", body
            )
            self.assertIn(expected_font, body)

        filtered_screen = self.client.get(
            reverse("departmental_exams:automatic_generation_readiness"),
            {"cycle": cycle.id, "course": second.course_id},
        )
        filtered_print = self.client.get(
            reverse("departmental_exams:automatic_generation_readiness_print"),
            {"cycle": cycle.id, "course": second.course_id},
        )
        for response in (filtered_screen, filtered_print):
            body = response.content.decode()
            self.assertEqual(
                re.findall(r'<td class="readiness-row-number">(\d+)</td>', body),
                ["1"],
            )
            self.assertEqual(
                [row["cycle_course"].id for row in response.context["rows"]],
                [second.id],
            )
            self.assertNotEqual(first.id, second.id)
            self.assertEqual(response.context["report_summary"]["total"], 1)

    def test_non_50_cycle_default_quota_drives_monitoring_progress_and_waiting_status(self):
        parent, _configuration, _campuses, _offerings = self._automatic_course(
            campus_codes=("CUBAO",), quota=60, quota_source="DEFAULT"
        )

        response = self._screen(parent)

        self.assertEqual(response.status_code, 200)
        row = response.context["rows"][0]
        self.assertEqual(row["faculty"]["required_quota"], 60)
        self.assertEqual(row["faculty"]["required_quota_source"], "DEFAULT")
        self.assertEqual(row["faculty"]["required_count"], 1)
        self.assertEqual(row["faculty"]["completed_count"], 1)
        self.assertEqual(
            row["contribution_progress"]["submitted_question_volume"], 60
        )
        self.assertEqual(
            row["contribution_progress"]["expected_monitoring_volume"], 60
        )
        self.assertEqual(row["generation_status"], "WAITING FOR DEADLINE")
        self.assertContains(response, "60 / 60 submitted")
        self.assertContains(response, "Contribution Progress is for monitoring only")

    def test_non_50_course_override_drives_monitoring_denominator_without_hardcoding(self):
        parent, _configuration, _campuses, _offerings = self._automatic_course(
            campus_codes=("CUBAO", "FAIRVIEW", "TAYTAY"),
            quota=60,
            quota_source="OVERRIDE",
        )

        row = self._screen(parent).context["rows"][0]

        self.assertEqual(row["faculty"]["required_quota"], 60)
        self.assertEqual(row["faculty"]["required_quota_source"], "OVERRIDE")
        self.assertEqual(
            row["contribution_progress"]["expected_monitoring_volume"], 180
        )
        self.assertEqual(
            row["contribution_progress"]["submitted_question_volume"], 180
        )

    def test_three_campus_monitoring_denominator_is_150_at_quota_50(self):
        parent, _configuration, _campuses, _offerings = self._automatic_course(
            campus_codes=("CUBAO", "FAIRVIEW", "TAYTAY"), quota=50
        )

        progress = self._screen(parent).context["rows"][0]["contribution_progress"]

        self.assertEqual(progress["expected_monitoring_volume"], 150)
        self.assertEqual(progress["submitted_question_volume"], 150)

    def test_one_campus_monitoring_denominator_is_50_at_quota_50(self):
        parent, _configuration, _campuses, _offerings = self._automatic_course(
            campus_codes=("CUBAO",), quota=50
        )

        progress = self._screen(parent).context["rows"][0]["contribution_progress"]

        self.assertEqual(progress["expected_monitoring_volume"], 50)
        self.assertEqual(progress["submitted_question_volume"], 50)

    def test_pool_only_readiness_works_before_deadline_without_changing_generator_gate(self):
        parent, _configuration, _campuses, _offerings = self._automatic_course()

        with patch(
            "apps.departmental_exams.generation_readiness.solve_automatic_identity_aware_two_sets",
            return_value=self._feasible_selection(),
        ):
            pool = Stage6ReadinessService.evaluate_automatic_pool(cycle_course=parent)
        problem, execution_readiness = Stage6ReadinessService.build_problem(
            cycle_course=parent
        )

        self.assertTrue(pool["ready"], pool["blockers"])
        self.assertEqual(pool["status"], "READY")
        self.assertIsNone(problem)
        self.assertIn(
            "CONTRIBUTION_NOT_CLOSED",
            {item["code"] for item in execution_readiness["blockers"]},
        )

    def test_participating_campuses_use_snapshots_and_duplicate_offering_is_deduplicated(self):
        parent, _configuration, campuses, offerings = self._automatic_course(
            campus_codes=("CUBAO", "FAIRVIEW")
        )
        existing = offerings["CUBAO"]
        extra_section = Section.objects.create(
            tenant=self.tenant,
            campus=campuses["CUBAO"],
            department=existing.department,
            program=existing.program,
            code="CUBAO-EXTRA",
            name="Cubao Extra",
        )
        duplicate_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=campuses["CUBAO"],
            department=existing.department,
            program=existing.program,
            academic_year=parent.cycle.academic_year,
            term=parent.cycle.term,
            course=parent.course,
            section=extra_section,
        )
        CycleCourseOffering.objects.create(
            cycle_course=parent,
            offering=duplicate_offering,
            campus=campuses["CUBAO"],
        )
        extra_faculty, _assignment = self.add_faculty_source(
            parent=parent,
            campus=campuses["CUBAO"],
            offering=offerings["CUBAO"],
            suffix="second-cubao-submission",
        )
        ContributionRosterService.synchronize(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            actor=self.generation_manager,
        )
        extra_contribution = FacultyContribution.objects.get(
            cycle_course=parent, faculty_user=extra_faculty
        )
        self._add_quota_questions(extra_contribution, 50)
        extra_contribution.status = FacultyContribution.Status.SUBMITTED
        extra_contribution.submitted_at = timezone.now()
        extra_contribution.save(update_fields=["status", "submitted_at", "updated_at"])

        row = self._screen(parent).context["rows"][0]

        self.assertEqual(row["campuses"], ("Cubao", "Fairview"))
        self.assertEqual(
            row["contribution_progress"]["expected_monitoring_volume"], 100
        )
        self.assertEqual(
            row["contribution_progress"]["submitted_campuses"],
            ("Cubao", "Fairview"),
        )
        self.assertEqual(
            row["contribution_progress"]["submitted_question_volume"], 150
        )

    def test_no_submitted_contribution_renders_zero_progress_and_no_campus_message(self):
        parent, _configuration, _campuses, _offerings = self._automatic_course(
            submitted_codes=()
        )

        response = self._screen(parent)
        progress = response.context["rows"][0]["contribution_progress"]

        self.assertEqual(progress["submitted_question_volume"], 0)
        self.assertEqual(progress["expected_monitoring_volume"], 150)
        self.assertEqual(progress["submitted_campuses"], ())
        self.assertContains(response, "0 / 150 submitted")
        self.assertContains(response, "No campus has submitted questions yet.")

    def test_available_with_warning_keeps_ready_and_renders_missing_campus_on_screen_and_print(self):
        parent, _configuration, _campuses, _offerings = self._automatic_course(
            submitted_codes=("CUBAO",)
        )
        expected = (
            "No usable submitted questions from Fairview.",
            "No usable submitted questions from Taytay.",
        )

        screen = self._screen(parent)
        row = screen.context["rows"][0]
        with patch(
            "apps.departmental_exams.generation_readiness.solve_automatic_identity_aware_two_sets",
            return_value=self._feasible_selection(),
        ):
            printed = self.client.get(
                reverse("departmental_exams:automatic_generation_readiness_print"),
                {"cycle": parent.cycle_id},
            )

        self.assertEqual(row["generation_status"], "WAITING FOR DEADLINE")
        self.assertEqual(row["pool_warnings"], expected)
        self.assertEqual(
            row["contribution_progress"]["submitted_question_volume"], 50
        )
        self.assertEqual(
            row["contribution_progress"]["expected_monitoring_volume"], 150
        )
        self.assertEqual(
            row["contribution_progress"]["submitted_campuses"], ("Cubao",)
        )
        for action in expected:
            self.assertIn(action, row["action_items"])
            self.assertContains(screen, action)
            self.assertContains(printed, action)
            self.assertIn(f"<li>{action}</li>", screen.content.decode())
            self.assertIn(f"<li>{action}</li>", printed.content.decode())
        self.assertTrue(
            all("<" not in item and ">" not in item for item in row["action_items"])
        )
        self.assertNotContains(
            screen,
            "Automatic generation may proceed using represented campuses under the configured campus policy.",
        )
        self.assertNotContains(screen, "MISSING_CAMPUS_REPRESENTATION")

    def test_strict_missing_campus_remains_hard_question_pool_blocker(self):
        parent, _configuration, _campuses, _offerings = self._automatic_course(
            submitted_codes=("CUBAO", "FAIRVIEW")
        )
        ExaminationCycle.objects.filter(pk=parent.cycle_id).update(
            automatic_campus_contribution_policy=(
                ExaminationCycle.AutomaticCampusContributionPolicy.STRICT
            )
        )

        row = self._screen(parent).context["rows"][0]

        self.assertEqual(row["generation_status"], "BLOCKED")
        self.assertEqual(row["pool_warnings"], ())
        self.assertIn("No usable submitted questions from Taytay.", row["action_items"])
        self.assertFalse(
            any("strict campus policy" in item for item in row["action_items"])
        )

    def test_malformed_and_duplicate_submitted_rows_are_excluded_from_usable_total(self):
        parent, _configuration, _campuses, _offerings = self._automatic_course()
        first, second = list(
            Question.objects.filter(contribution__cycle_course=parent).order_by("id")[:2]
        )
        Question.objects.filter(pk=first.pk).update(question_text="")
        Question.objects.filter(pk=second.pk).update(
            question_text="Readiness duplicate identity"
        )
        third = Question.objects.filter(contribution__cycle_course=parent).order_by("id")[2]
        Question.objects.filter(pk=third.pk).update(
            question_text="Readiness duplicate identity"
        )

        with patch(
            "apps.departmental_exams.generation_readiness.solve_automatic_identity_aware_two_sets",
            return_value=self._feasible_selection(),
        ):
            pool = Stage6ReadinessService.evaluate_automatic_pool(cycle_course=parent)

        self.assertTrue(pool["ready"], pool["blockers"])
        self.assertEqual(pool["invalid_question_count"], 1)
        self.assertEqual(pool["duplicate_question_count"], 1)
        self.assertEqual(pool["unique_question_count"], 148)
        row = self._screen(parent).context["rows"][0]
        self.assertEqual(row["generation_status"], "BLOCKED")
        self.assertIn("Resolve 1 unusable Submitted question row.", row["action_items"])
        self.assertEqual(
            row["contribution_progress"]["submitted_question_volume"], 150
        )
        body = self._screen(parent).content.decode()
        self.assertNotIn("duplicate copies", body)
        self.assertNotIn("148 unique", body)

    def test_unconfirmed_import_and_nonparticipating_campus_questions_do_not_count(self):
        parent, _configuration, _campuses, _offerings = self._automatic_course(
            campus_codes=("CUBAO",), quota=50
        )
        contribution = FacultyContribution.objects.get(cycle_course=parent)
        batch = QuestionImportBatch.objects.create(
            tenant=self.tenant,
            contribution=contribution,
            active_contribution=None,
            uploading_user=self.generation_manager,
            status=QuestionImportBatch.Status.READY,
            contribution_revision_snapshot=contribution.revision,
            file_sha256="a" * 64,
            filename_sha256="b" * 64,
            total_rows=1,
            valid_rows=1,
            error_count=0,
            warning_count=0,
            resulting_question_count=0,
            committed_rows=0,
            expires_at=timezone.now() + timezone.timedelta(hours=1),
        )
        imported = Question.objects.filter(contribution=contribution).order_by("id").first()
        Question.objects.filter(pk=imported.pk).update(
            entry_method=Question.EntryMethod.CSV,
            import_batch=batch,
            import_row_number=1,
        )

        unconfirmed_row = self._screen(parent).context["rows"][0]
        self.assertEqual(unconfirmed_row["generation_status"], "BLOCKED")
        self.assertEqual(
            unconfirmed_row["contribution_progress"]["submitted_question_volume"], 50
        )

        FacultyContribution.objects.filter(pk=contribution.pk).update(
            source_campus=self.other_campus
        )
        nonparticipating_row = self._screen(parent).context["rows"][0]
        self.assertEqual(nonparticipating_row["generation_status"], "BLOCKED")
        self.assertEqual(
            nonparticipating_row["contribution_progress"]["submitted_campuses"], ()
        )

    def test_historical_submitted_questions_supply_pool_but_not_current_completion(self):
        parent, _configuration, campuses, offerings = self._automatic_course(
            campus_codes=("CUBAO",), quota=50
        )
        historical = FacultyContribution.objects.get(cycle_course=parent)
        historical_assignment = historical.source_assignment
        historical_assignment.is_active = False
        historical_assignment.save(update_fields=["is_active", "updated_at"])
        current_faculty, _assignment = self.add_faculty_source(
            parent=parent,
            campus=campuses["CUBAO"],
            offering=offerings["CUBAO"],
            suffix="current-replacement",
        )
        ContributionRosterService.synchronize(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            actor=self.generation_manager,
        )
        self.assertTrue(
            FacultyContribution.objects.filter(
                cycle_course=parent,
                faculty_user=current_faculty,
                status=FacultyContribution.Status.DRAFT,
            ).exists()
        )

        row = self._screen(parent).context["rows"][0]

        self.assertTrue(row["faculty"]["authoritative"])
        self.assertEqual(row["faculty"]["required_count"], 1)
        self.assertEqual(row["faculty"]["completed_count"], 0)
        self.assertEqual(row["faculty"]["incomplete_count"], 1)
        self.assertEqual(row["generation_status"], "WAITING FOR DEADLINE")
        self.assertEqual(
            row["contribution_progress"]["submitted_question_volume"], 50
        )
        self.assertEqual(
            row["contribution_progress"]["submitted_campuses"], ("Cubao",)
        )
        self.assertEqual(Question.objects.filter(contribution=historical).count(), 50)
        self.assertIn(
            "1 current faculty has not completed their required contribution.",
            row["action_items"],
        )

    def test_total_campus_and_difficulty_shortages_use_concrete_management_wording(self):
        parent, _configuration, _campuses, _offerings = self._automatic_course(
            campus_codes=("CUBAO",), quota=50
        )
        Question.objects.filter(
            contribution__cycle_course=parent,
            position__gt=40,
        ).delete()

        row = self._screen(parent).context["rows"][0]

        self.assertEqual(row["generation_status"], "NEEDS QUESTIONS")
        self.assertIn(
            "Add 10 more usable unique questions.",
            row["pool_actions"],
        )
        self.assertTrue(
            any("usable Difficult questions" in action for action in row["pool_actions"])
        )
        for action in row["action_items"]:
            self.assertContains(self._screen(parent), action)

    def test_exact_solver_infeasibility_and_processing_messages_are_academic_facing(self):
        parent, configuration, _campuses, _offerings = self._automatic_course(
            campus_codes=("CUBAO",), quota=50
        )
        infeasible = IdentitySelectionResult(
            feasible=False,
            limit_hit=False,
            states_explored=1,
            set_a_block_ids=(),
            set_b_block_ids=(),
            overlap=0,
            proportional_score=0,
            contributors_represented=0,
            squared_contributor_concentration=0,
        )
        with patch(
            "apps.departmental_exams.generation_readiness.solve_automatic_identity_aware_two_sets",
            return_value=infeasible,
        ):
            response = self.client.get(
                reverse("departmental_exams:automatic_generation_readiness"),
                {"cycle": parent.cycle_id},
            )
        row = response.context["rows"][0]
        self.assertEqual(row["generation_status"], "NEEDS QUESTIONS")
        self.assertIn(
            "Add usable unique questions that satisfy the required difficulty distribution.",
            row["action_items"],
        )

        CourseExamConfiguration.objects.filter(pk=configuration.pk).update(
            automatic_processing_status=(
                CourseExamConfiguration.AutomaticProcessingStatus.ERROR
            )
        )
        error_row = self._screen(parent).context["rows"][0]
        self.assertEqual(
            error_row["action_items"],
            ("Resolve the automatic processing error with the system administrator.",),
        )
        self.assertNotIn("log", " ".join(error_row["action_items"]).lower())

        limit_hit = IdentitySelectionResult(
            feasible=False,
            limit_hit=True,
            states_explored=1,
            set_a_block_ids=(),
            set_b_block_ids=(),
            overlap=0,
            proportional_score=0,
            contributors_represented=0,
            squared_contributor_concentration=0,
        )
        CourseExamConfiguration.objects.filter(pk=configuration.pk).update(
            automatic_processing_status=(
                CourseExamConfiguration.AutomaticProcessingStatus.BLOCKED
            )
        )
        with patch(
            "apps.departmental_exams.generation_readiness.solve_automatic_identity_aware_two_sets",
            return_value=limit_hit,
        ):
            limit_response = self.client.get(
                reverse("departmental_exams:automatic_generation_readiness"),
                {"cycle": parent.cycle_id},
            )
        self.assertIn(
            "Resolve the automatic readiness processing limit with the system administrator.",
            limit_response.context["rows"][0]["action_items"],
        )

    def test_require_all_is_faculty_incomplete_while_sufficient_pool_is_warning(self):
        parent, _configuration, campuses, offerings = self._automatic_course()
        extra_faculty, _assignment = self.add_faculty_source(
            parent=parent,
            campus=campuses["CUBAO"],
            offering=offerings["CUBAO"],
            suffix="extra-incomplete",
        )
        ContributionRosterService.synchronize(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            actor=self.generation_manager,
        )
        self.assertTrue(
            FacultyContribution.objects.filter(
                cycle_course=parent,
                faculty_user=extra_faculty,
                status=FacultyContribution.Status.DRAFT,
            ).exists()
        )

        warning_row = self._screen(parent).context["rows"][0]
        self.assertEqual(warning_row["generation_status"], "WAITING FOR DEADLINE")
        self.assertIn(
            "1 current faculty has not completed their required contribution.",
            warning_row["action_items"],
        )

        cycle = parent.cycle
        cycle.automatic_contributor_completion_policy = (
            ExaminationCycle.AutomaticContributorCompletionPolicy.REQUIRE_ALL
        )
        cycle.save(
            update_fields=["automatic_contributor_completion_policy", "updated_at"]
        )
        parent.cycle = cycle
        blocker_row = self._screen(parent).context["rows"][0]
        self.assertEqual(blocker_row["generation_status"], "FACULTY INCOMPLETE")
        self.assertEqual(
            blocker_row["action_items"],
            ("1 current faculty has not completed their required contribution.",),
        )

    def test_stale_roster_and_unresolved_blocked_draft_remain_blockers(self):
        parent, _configuration, campuses, offerings = self._automatic_course()
        _faculty, assignment = self.add_faculty_source(
            parent=parent,
            campus=campuses["CUBAO"],
            offering=offerings["CUBAO"],
            suffix="stale",
        )
        stale_row = self._screen(parent).context["rows"][0]
        self.assertEqual(stale_row["generation_status"], "BLOCKED")
        self.assertEqual(
            stale_row["action_items"], ("Synchronize the contributor roster.",)
        )
        self.assertFalse(stale_row["faculty"]["authoritative"])
        self.assertIsNone(stale_row["faculty"]["required_count"])

        ContributionRosterService.synchronize(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            actor=self.generation_manager,
        )
        assignment.is_active = False
        assignment.save(update_fields=["is_active", "updated_at"])
        ContributionRosterService.synchronize(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            actor=self.generation_manager,
        )
        blocked_row = self._screen(parent).context["rows"][0]
        self.assertEqual(blocked_row["generation_status"], "BLOCKED")
        self.assertIn(
            "Resolve 1 Blocked Draft contributor record.", blocked_row["action_items"]
        )

    def test_initialized_legacy_null_draft_roster_does_not_render_stale(self):
        parent, _configuration, _campuses, _offerings = self._automatic_course(
            campus_codes=("CUBAO",),
            submitted_codes=(),
        )
        assignment = FacultyContribution.objects.get(
            cycle_course=parent
        ).source_assignment
        assignment.tenant = None
        assignment.campus = None
        assignment.save(update_fields=["tenant", "campus", "updated_at"])

        response = self._screen(parent)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["rows"][0]["faculty"]["roster_current"])
        self.assertNotContains(response, "Contributor roster has not been initialized.")
        self.assertNotContains(
            response,
            "Contributor roster needs synchronization because the eligible faculty list has changed.",
        )
        self.assertNotContains(response, "Synchronize the contributor roster.")

    def test_not_initialized_and_stale_roster_wording_and_actions_are_distinct(self):
        parent, configuration, campuses, offerings = self.make_stage6_open_course()
        parent.cycle.processing_mode = ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        parent.cycle.save(update_fields=["processing_mode", "updated_at"])
        self.add_faculty_source(
            parent=parent,
            campus=campuses["CUBAO"],
            offering=offerings["CUBAO"],
            suffix="readiness-uninitialized",
        )

        uninitialized = self._screen(parent)
        with patch(
            "apps.departmental_exams.generation_readiness.solve_automatic_identity_aware_two_sets",
            return_value=self._feasible_selection(),
        ):
            uninitialized_print = self.client.get(
                reverse("departmental_exams:automatic_generation_readiness_print"),
                {"cycle": parent.cycle_id},
            )

        self.assertContains(
            uninitialized,
            "Contributor roster has not been initialized.",
        )
        self.assertContains(
            uninitialized_print,
            "Contributor roster has not been initialized.",
        )
        self.assertEqual(
            uninitialized.context["rows"][0]["action_items"][0],
            "Initialize the contributor roster.",
        )
        self.assertNotContains(uninitialized, "Synchronize the contributor roster.")
        self.assertNotContains(
            uninitialized,
            "Contributor roster needs synchronization because the eligible faculty list has changed.",
        )

        ContributionRosterService.initialize(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            actor=self.generation_manager,
        )
        self.add_faculty_source(
            parent=parent,
            campus=campuses["CUBAO"],
            offering=offerings["CUBAO"],
            suffix="readiness-new-staffing",
        )
        configuration.refresh_from_db()

        stale = self._screen(parent)
        with patch(
            "apps.departmental_exams.generation_readiness.solve_automatic_identity_aware_two_sets",
            return_value=self._feasible_selection(),
        ):
            stale_print = self.client.get(
                reverse("departmental_exams:automatic_generation_readiness_print"),
                {"cycle": parent.cycle_id},
            )

        self.assertContains(
            stale,
            "Contributor roster needs synchronization because the eligible faculty list has changed.",
        )
        self.assertContains(
            stale_print,
            "Contributor roster needs synchronization because the eligible faculty list has changed.",
        )
        self.assertEqual(
            stale.context["rows"][0]["action_items"][0],
            "Synchronize the contributor roster.",
        )
        self.assertNotContains(stale, "Contributor roster has not been initialized.")
        self.assertNotContains(stale, "stale or not initialized")

    def test_due_missing_deadline_generated_and_exempt_statuses(self):
        parent, configuration, _campuses, _offerings = self._automatic_course()
        CourseExamConfiguration.objects.filter(pk=configuration.pk).update(
            reopened_contribution_deadline=timezone.now()
            - timezone.timedelta(minutes=1)
        )
        due_row = self._screen(parent).context["rows"][0]
        self.assertEqual(due_row["generation_status"], "READY FOR GENERATION")

        CourseExamConfiguration.objects.filter(pk=configuration.pk).update(
            reopened_contribution_deadline=None,
            contribution_deadline=None,
            contribution_deadline_source=None,
        )
        missing_row = self._screen(parent).context["rows"][0]
        self.assertEqual(missing_row["generation_status"], "BLOCKED")
        self.assertEqual(
            missing_row["action_items"], ("Set the contribution deadline.",)
        )

        configuration.refresh_from_db()
        CourseExamConfiguration.objects.filter(pk=configuration.pk).update(
            contribution_deadline=self.future_deadline(),
            contribution_deadline_source="OVERRIDE",
        )
        ExamGenerationRevision.objects.create(
            cycle_course=parent,
            revision_number=1,
            source_input_fingerprint="a" * 64,
            algorithm_version="test",
            generated_by=None,
            generation_trigger=ExamGenerationRevision.GenerationTrigger.AUTOMATIC,
            configuration_revision_snapshot=configuration.revision,
            blueprint_revision_snapshot=1,
            roster_boundary_snapshot="b" * 64,
            final_item_count_snapshot=50,
            request_token_digest="c" * 64,
            minimum_overlap=0,
            proportional_score=0,
            contributors_represented=3,
            squared_contributor_concentration=0,
        )
        generated_row = self._screen(parent).context["rows"][0]
        self.assertEqual(generated_row["generation_status"], "GENERATED")

        ExamGenerationRevision.objects.all().delete()
        CycleCourse.objects.filter(pk=parent.pk).update(
            inclusion_status=CycleCourse.InclusionStatus.EXEMPT,
            exemption_category=CycleCourse.ExemptionCategory.PRACTICUM_OJT,
            exemption_reason="Approved output-based exemption.",
            exemption_changed_by=self.generation_manager,
            exemption_changed_at=timezone.now(),
        )
        with patch.object(
            Stage6ReadinessService,
            "evaluate_automatic_pool",
            wraps=Stage6ReadinessService.evaluate_automatic_pool,
        ) as evaluator:
            exempt_row = self._screen(parent).context["rows"][0]
        self.assertEqual(exempt_row["generation_status"], "EXEMPT")
        self.assertIsNone(exempt_row["contribution_progress"])
        self.assertEqual(exempt_row["action_items"], ("No generation required.",))
        evaluator.assert_not_called()

    def test_rbac_requires_every_campus_honors_global_scope_and_direct_deny(self):
        parent, _configuration, campuses, _offerings = self._automatic_course()
        self.assertEqual(self._screen(parent).status_code, 200)

        partial = self.make_user(
            "partial-readiness",
            self.department,
            ("admin_portal.access", "departmental_exams.manage_exam_generation"),
        )
        self.client.force_login(partial)
        self.assertEqual(self._screen(parent).status_code, 403)

        self.client.force_login(self.generation_manager)
        UserPermission.objects.create(
            user=self.generation_manager,
            permission=Permission.objects.get(
                code="departmental_exams.manage_exam_generation"
            ),
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=campuses["FAIRVIEW"],
        )
        denied = self._screen(parent)
        self.assertEqual(denied.status_code, 403)
        self.assertNotContains(denied, parent.course.title, status_code=403)

    def test_monitoring_link_requires_automatic_authority_and_direct_route_stays_denied(self):
        automatic, _configuration, _campuses, _offerings = self._automatic_course(
            campus_codes=("CUBAO",)
        )
        automatic_monitoring = self.client.get(
            reverse("departmental_exams:contributor_monitoring"),
            {"cycle": automatic.cycle_id},
        )
        self.assertEqual(automatic_monitoring.status_code, 200)
        self.assertContains(automatic_monitoring, "Automatic Generation Readiness")

        manual_cycle = self.make_cycle(scope_suffix="manual-readiness-link")
        manual = self.make_course(cycle=manual_cycle, code="MANUAL-LINK")
        self.make_configuration(manual)
        for user in (self.configurer, self.reviewer):
            manual.reviewer = self.reviewer if user == self.reviewer else None
            manual.save(update_fields=["reviewer", "updated_at"])
            self.client.force_login(user)
            monitoring = self.client.get(
                reverse("departmental_exams:contributor_monitoring"),
                {"cycle": manual.cycle_id},
            )
            self.assertEqual(monitoring.status_code, 200)
            self.assertNotContains(monitoring, "Automatic Generation Readiness")
            direct = self.client.get(
                reverse("departmental_exams:automatic_generation_readiness"),
                {"cycle": automatic.cycle_id},
            )
            self.assertEqual(direct.status_code, 403)

    def test_manual_configurer_monitoring_visibility_is_partitioned_from_automatic(self):
        manual, included, exempt = self._mixed_mode_monitoring_courses(
            suffix="CONFIGURE-PARTITION"
        )
        self.client.force_login(self.configurer)

        monitoring = self.client.get(
            reverse("departmental_exams:contributor_monitoring")
        )

        self.assertEqual(monitoring.status_code, 200)
        self.assertEqual(
            [course.id for course in monitoring.context["courses"]],
            [manual.id],
        )
        self.assertTrue(monitoring.context["courses"][0].can_configure)
        self.assertContains(monitoring, manual.course.title)
        self.assertNotContains(monitoring, included.course.title)
        self.assertNotContains(monitoring, exempt.course.title)
        self.assertNotContains(monitoring, "Automatic Generation Readiness")

    def test_manual_reviewer_monitoring_visibility_is_partitioned_from_automatic(self):
        manual, included, exempt = self._mixed_mode_monitoring_courses(
            suffix="REVIEW-PARTITION"
        )
        CycleCourse.objects.filter(pk__in=(manual.id, included.id, exempt.id)).update(
            reviewer=self.reviewer
        )
        self.client.force_login(self.reviewer)

        monitoring = self.client.get(
            reverse("departmental_exams:contributor_monitoring")
        )

        self.assertEqual(monitoring.status_code, 200)
        self.assertEqual(
            [course.id for course in monitoring.context["courses"]],
            [manual.id],
        )
        self.assertFalse(monitoring.context["courses"][0].can_configure)
        self.assertContains(monitoring, manual.course.title)
        self.assertNotContains(monitoring, included.course.title)
        self.assertNotContains(monitoring, exempt.course.title)
        self.assertNotContains(monitoring, "Automatic Generation Readiness")

    def test_mixed_manual_role_cannot_bypass_automatic_direct_deny(self):
        manual, included, exempt = self._mixed_mode_monitoring_courses(
            suffix="DIRECT-DENY-PARTITION"
        )
        mixed_user = self.make_user(
            "mixed-manual-automatic-denied",
            self.department,
            (
                "admin_portal.access",
                "departmental_exams.configure",
                "departmental_exams.review_generate",
                "departmental_exams.manage_exam_generation",
            ),
        )
        CycleCourse.objects.filter(pk__in=(manual.id, included.id, exempt.id)).update(
            reviewer=mixed_user
        )
        UserPermission.objects.create(
            user=mixed_user,
            permission=Permission.objects.get(
                code="departmental_exams.manage_exam_generation"
            ),
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.campus,
        )
        self.client.force_login(mixed_user)

        monitoring = self.client.get(
            reverse("departmental_exams:contributor_monitoring")
        )

        self.assertEqual(monitoring.status_code, 200)
        self.assertEqual(
            [course.id for course in monitoring.context["courses"]],
            [manual.id],
        )
        self.assertTrue(monitoring.context["courses"][0].can_configure)
        self.assertNotContains(monitoring, included.course.title)
        self.assertNotContains(monitoring, exempt.course.title)
        self.assertNotContains(monitoring, "Automatic Generation Readiness")
        for automatic in (included, exempt):
            with self.subTest(status=automatic.inclusion_status):
                denied = self.client.get(
                    reverse("departmental_exams:automatic_generation_readiness"),
                    {"cycle": automatic.cycle_id},
                )
                self.assertEqual(denied.status_code, 403)
                self.assertNotContains(
                    denied,
                    automatic.course.title,
                    status_code=403,
                )

    def test_automatic_manager_capabilities_are_status_specific_in_mixed_mode(self):
        manual, included, exempt = self._mixed_mode_monitoring_courses(
            suffix="AUTOMATIC-CAPABILITY"
        )

        monitoring = self.client.get(
            reverse("departmental_exams:contributor_monitoring")
        )

        self.assertEqual(monitoring.status_code, 200)
        listed = {course.id: course for course in monitoring.context["courses"]}
        self.assertEqual(set(listed), {included.id, exempt.id})
        self.assertNotIn(manual.id, listed)
        self.assertTrue(listed[included.id].can_configure)
        self.assertFalse(listed[exempt.id].can_configure)
        self.assertContains(monitoring, "Automatic Generation Readiness")
        self.assertContains(monitoring, "Initialize roster", count=1)
        self.assertNotContains(monitoring, "Synchronize roster")
        for automatic in (included, exempt):
            with self.subTest(status=automatic.inclusion_status):
                report = self.client.get(
                    reverse("departmental_exams:automatic_generation_readiness"),
                    {"cycle": automatic.cycle_id},
                )
                self.assertEqual(report.status_code, 200)
                self.assertContains(report, automatic.course.title)

    def test_automatic_monitoring_visibility_remains_tenant_and_campus_scoped(self):
        _manual, included, exempt = self._mixed_mode_monitoring_courses(
            suffix="SCOPE-PARTITION"
        )
        CycleCourseOffering.objects.filter(
            cycle_course__in=(included, exempt)
        ).update(campus=self.other_campus)
        campus_manager = self.make_user(
            "main-campus-automatic-manager",
            self.department,
            (
                "admin_portal.access",
                "departmental_exams.manage_exam_generation",
            ),
            campus=self.campus,
        )

        self.assertFalse(
            ContributionMonitoringSelector.visible_cycle_courses(
                user=campus_manager,
                tenant_id=self.other_tenant.id,
            ).exists()
        )
        self.assertNotIn(
            included.id,
            set(
                ContributionMonitoringSelector.visible_cycle_courses(
                    user=campus_manager,
                    tenant_id=self.tenant.id,
                ).values_list("id", flat=True)
            ),
        )
        self.client.force_login(campus_manager)
        monitoring = self.client.get(
            reverse("departmental_exams:contributor_monitoring")
        )
        self.assertEqual(monitoring.status_code, 403)
        self.assertNotContains(
            monitoring,
            included.course.title,
            status_code=403,
        )

    def test_exempt_only_inclusion_manager_can_monitor_and_open_readiness(self):
        exempt, _configuration, _campuses, _offerings = (
            self._exempt_automatic_course(label="Authorized")
        )

        monitoring = self.client.get(
            reverse("departmental_exams:contributor_monitoring"),
            {"cycle": exempt.cycle_id},
        )

        self.assertEqual(monitoring.status_code, 200)
        self.assertEqual(
            [course.id for course in monitoring.context["courses"]],
            [exempt.id],
        )
        self.assertContains(monitoring, exempt.course.title)
        self.assertContains(monitoring, "Exempt")
        self.assertContains(monitoring, "Automatic Generation Readiness")
        self.assertNotContains(monitoring, "Initialize roster")
        self.assertNotContains(monitoring, "Synchronize roster")
        self.assertFalse(monitoring.context["courses"][0].can_configure)

        readiness = self._screen(exempt)

        self.assertEqual(readiness.status_code, 200)
        self.assertEqual(len(readiness.context["rows"]), 1)
        self.assertEqual(
            readiness.context["rows"][0]["generation_status"],
            "EXEMPT",
        )
        self.assertContains(readiness, exempt.course.title)
        self.assertContains(readiness, "EXEMPT")

    def test_exempt_monitoring_requires_every_participating_campus(self):
        authorized, _configuration, _campuses, _offerings = (
            self._exempt_automatic_course(label="Authorized Campus")
        )
        authorized.cycle.exam_period = ExaminationCycle.ExamPeriod.FINAL
        authorized.cycle.save(update_fields=["exam_period", "updated_at"])
        unauthorized, _configuration, _campuses, _offerings = (
            self._exempt_automatic_course(
                label="Unauthorized Campus",
                campus_codes=("CUBAO", "FAIRVIEW"),
            )
        )
        campus_manager = self.make_user(
            "cubao-exempt-manager",
            self.department,
            (
                "admin_portal.access",
                "departmental_exams.manage_exam_generation",
            ),
            campus=self.campus,
        )
        self.client.force_login(campus_manager)

        monitoring = self.client.get(
            reverse("departmental_exams:contributor_monitoring")
        )

        self.assertEqual(monitoring.status_code, 200)
        self.assertEqual(
            [course.id for course in monitoring.context["courses"]],
            [authorized.id],
        )
        self.assertContains(monitoring, authorized.course.title)
        self.assertNotContains(monitoring, unauthorized.course.title)
        unauthorized_readiness = self.client.get(
            reverse("departmental_exams:automatic_generation_readiness"),
            {"cycle": unauthorized.cycle_id},
        )
        self.assertEqual(unauthorized_readiness.status_code, 200)
        self.assertTrue(unauthorized_readiness.context["filters_invalid"])
        self.assertEqual(unauthorized_readiness.context["rows"], [])
        self.assertNotContains(unauthorized_readiness, unauthorized.course.title)

    def test_direct_deny_overrides_exempt_inclusion_management_authority(self):
        exempt, _configuration, campuses, _offerings = (
            self._exempt_automatic_course(label="Direct Deny")
        )
        UserPermission.objects.create(
            user=self.generation_manager,
            permission=Permission.objects.get(
                code="departmental_exams.manage_exam_generation"
            ),
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=campuses["CUBAO"],
        )

        monitoring = self.client.get(
            reverse("departmental_exams:contributor_monitoring")
        )
        readiness = self.client.get(
            reverse("departmental_exams:automatic_generation_readiness"),
            {"cycle": exempt.cycle_id},
        )

        self.assertEqual(monitoring.status_code, 403)
        self.assertNotContains(
            monitoring, exempt.course.title, status_code=403
        )
        self.assertEqual(readiness.status_code, 403)
        self.assertNotContains(readiness, exempt.course.title, status_code=403)

    def test_feature_off_exposes_neither_monitoring_link_nor_report(self):
        automatic, _configuration, _campuses, _offerings = self._automatic_course(
            campus_codes=("CUBAO",)
        )
        with patch(
            "apps.departmental_exams.services.FeatureSettingsService.is_departmental_exam_builder_enabled",
            return_value=False,
        ):
            monitoring = self.client.get(
                reverse("departmental_exams:contributor_monitoring"),
                {"cycle": automatic.cycle_id},
            )
            report = self.client.get(
                reverse("departmental_exams:automatic_generation_readiness"),
                {"cycle": automatic.cycle_id},
            )
        self.assertEqual(monitoring.status_code, 403)
        self.assertNotContains(
            monitoring, "Automatic Generation Readiness", status_code=403
        )
        self.assertEqual(report.status_code, 403)

    def test_tenant_and_filter_fail_closed_and_print_preserves_filter(self):
        parent, _configuration, _campuses, _offerings = self._automatic_course()
        invalid = self.client.get(
            reverse("departmental_exams:automatic_generation_readiness"),
            {"cycle": "bad", "period": "MIDTERM", "course": "999999"},
        )
        self.assertEqual(invalid.status_code, 200)
        self.assertTrue(invalid.context["filters_invalid"])
        self.assertEqual(invalid.context["rows"], [])
        self.assertIn("cycle=bad", invalid.context["filter_query"])
        self.assertIn("course=999999", invalid.context["filter_query"])
        print_response = self.client.get(
            reverse("departmental_exams:automatic_generation_readiness_print"),
            {"cycle": "bad", "period": "MIDTERM", "course": "999999"},
        )
        self.assertEqual(print_response.status_code, 200)
        self.assertEqual(print_response.context["rows"], [])

        with self.assertRaises(PermissionDenied):
            AutomaticGenerationReadinessReport(
                tenant_id=self.other_tenant.id,
                user=self.generation_manager,
                params={"cycle": str(parent.cycle_id)},
            ).build()

    def test_get_is_read_only_does_not_invoke_processor_or_leak_confidential_data(self):
        parent, configuration, _campuses, _offerings = self._automatic_course()
        counts_before = {
            "questions": Question.objects.count(),
            "contributions": FacultyContribution.objects.count(),
            "revisions": ExamGenerationRevision.objects.count(),
            "audit": AuditLog.objects.count(),
            "configuration": (
                configuration.workflow_status,
                configuration.revision,
                configuration.automatic_processing_status,
            ),
        }
        with patch.object(
            AutomaticExamDeadlineService, "process_course"
        ) as process_course, patch(
            "apps.departmental_exams.generation_readiness._generation_question_source_digest"
        ) as source_digest, patch(
            "apps.departmental_exams.generation_readiness._assignment_context_snapshot"
        ) as assignment_snapshot:
            response = self._screen(parent)
        configuration.refresh_from_db()
        counts_after = {
            "questions": Question.objects.count(),
            "contributions": FacultyContribution.objects.count(),
            "revisions": ExamGenerationRevision.objects.count(),
            "audit": AuditLog.objects.count(),
            "configuration": (
                configuration.workflow_status,
                configuration.revision,
                configuration.automatic_processing_status,
            ),
        }

        self.assertEqual(response.status_code, 200)
        process_course.assert_not_called()
        source_digest.assert_not_called()
        assignment_snapshot.assert_not_called()
        self.assertEqual(counts_after, counts_before)
        body = response.content.decode()
        with patch(
            "apps.departmental_exams.generation_readiness.solve_automatic_identity_aware_two_sets",
            return_value=self._feasible_selection(),
        ):
            printed = self.client.get(
                reverse("departmental_exams:automatic_generation_readiness_print"),
                {"cycle": parent.cycle_id},
            )
        for secret in (
            "Readiness confidential question",
            "Private A",
            "Private B",
            "correct_answer",
            "normalized_fingerprint",
        ):
            self.assertNotIn(secret, body)
            self.assertNotIn(secret, printed.content.decode())
        self.assertIn("private", response.headers["Cache-Control"])
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_screen_print_parity_and_one_feasibility_evaluation_per_course(self):
        parent, _configuration, _campuses, _offerings = self._automatic_course()
        with patch.object(
            Stage6ReadinessService,
            "evaluate_automatic_pool",
            wraps=Stage6ReadinessService.evaluate_automatic_pool,
        ) as evaluator:
            screen = self._screen(parent)
        self.assertEqual(evaluator.call_count, 1)

        with patch.object(
            Stage6ReadinessService,
            "evaluate_automatic_pool",
            wraps=Stage6ReadinessService.evaluate_automatic_pool,
        ) as evaluator:
            with patch(
                "apps.departmental_exams.generation_readiness.solve_automatic_identity_aware_two_sets",
                return_value=self._feasible_selection(),
            ):
                printed = self.client.get(
                    reverse("departmental_exams:automatic_generation_readiness_print"),
                    {"cycle": parent.cycle_id},
                )
        self.assertEqual(evaluator.call_count, 1)
        self.assertEqual(screen.status_code, 200)
        self.assertEqual(printed.status_code, 200)
        for key in (
            "campuses",
            "contribution_progress",
            "generation_status",
            "status_detail",
            "action_items",
        ):
            self.assertEqual(screen.context["rows"][0][key], printed.context["rows"][0][key])
        screen_body = screen.content.decode()
        print_body = printed.content.decode()
        headers = (
            "No.",
            "Authorized Course",
            "Campuses Offered",
            "Final Exam Items",
            "Contribution Progress",
            "Automatic Exam Generation Status",
            "Action Needed",
        )
        self.assertEqual(
            len(re.findall(r'<th\b[^>]*scope="col"', screen_body)), 7
        )
        self.assertEqual(len(re.findall(r"<th\b", print_body)), 7)
        for header in headers:
            self.assertIn(header, screen_body)
            self.assertIn(header, print_body)
        for removed_header in (
            "Required Questions per Faculty",
            "Faculty Completion",
            "Campus Question Requirements",
            "Total Usable Questions",
            "Question Pool Readiness",
        ):
            self.assertNotIn(removed_header, screen_body)
            self.assertNotIn(removed_header, print_body)
        self.assertContains(screen, "Automatic Generation Readiness")
        monitoring = self.client.get(
            reverse("departmental_exams:contributor_monitoring"),
            {"cycle": parent.cycle_id},
        )
        self.assertContains(monitoring, "Automatic Generation Readiness")

    def test_query_count_is_bounded_while_evaluation_remains_once_per_visible_course(self):
        cycle = self.make_cycle(
            status=ExaminationCycle.Status.OPEN,
            default_questions_required_per_faculty=60,
            default_final_item_count=50,
            default_contribution_deadline=self.future_deadline(),
            scope_suffix="readiness-query",
        )
        cycle.processing_mode = ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        cycle.save(update_fields=["processing_mode", "updated_at"])
        first = self.make_course(cycle=cycle, code="READY-QUERY-A")
        second = self.make_course(cycle=cycle, code="READY-QUERY-B")
        for course in (first, second):
            self.make_configuration(
                course,
                quota=60,
                quota_source="DEFAULT",
                workflow=CourseExamConfiguration.WorkflowStatus.OPEN,
                opened_at=timezone.now(),
            )
        pool = {
            "ready": True,
            "blockers": [],
            "shortages": [],
            "campus_requirements": [],
            "unique_question_count": 100,
            "invalid_question_count": 0,
        }
        roster = SimpleNamespace(
            current=True,
            required_active_count=0,
            submitted_required_count=0,
            incomplete_active_count=0,
            unresolved_blocked_count=0,
        )
        url = reverse("departmental_exams:automatic_generation_readiness")
        with patch.object(
            Stage6ReadinessService, "evaluate_automatic_pool", return_value=pool
        ) as evaluator, patch.object(
            ContributorRosterReadinessService, "evaluate", return_value=roster
        ):
            with CaptureQueriesContext(connection) as one_queries:
                one = self.client.get(
                    url, {"cycle": cycle.id, "course": first.course_id}
                )
            self.assertEqual(evaluator.call_count, 1)
            evaluator.reset_mock()
            with CaptureQueriesContext(connection) as two_queries:
                two = self.client.get(url, {"cycle": cycle.id})
            self.assertEqual(evaluator.call_count, 2)

        self.assertEqual(one.status_code, 200)
        self.assertEqual(two.status_code, 200)
        self.assertLessEqual(len(two_queries), len(one_queries) + 1)

    def test_default_cycle_bounds_heavy_prefetch_and_malformed_cycle_loads_no_history(self):
        historical_cycle = self.make_cycle(
            status=ExaminationCycle.Status.OPEN,
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=self.future_deadline(),
            scope_suffix="historical-readiness",
        )
        historical_cycle.processing_mode = (
            ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        )
        historical_cycle.save(update_fields=["processing_mode", "updated_at"])
        historical_cycle.academic_year.start_date = "2025-06-01"
        historical_cycle.academic_year.end_date = "2026-05-31"
        historical_cycle.academic_year.save(
            update_fields=["start_date", "end_date", "updated_at"]
        )
        historical = self.make_course(
            cycle=historical_cycle, code="HISTORICAL-READINESS"
        )
        historical_configuration = self.make_configuration(
            historical,
            quota=50,
            quota_source="DEFAULT",
            workflow=CourseExamConfiguration.WorkflowStatus.OPEN,
            opened_at=timezone.now(),
        )
        FacultyContribution.objects.create(
            cycle_course=historical,
            faculty_user=self.generation_manager,
            source_campus=self.campus,
            quota_snapshot=50,
            configuration_revision_snapshot=historical_configuration.revision,
        )
        older_cycle = self.make_cycle(
            status=ExaminationCycle.Status.OPEN,
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=self.future_deadline(),
            scope_suffix="older-readiness",
        )
        older_cycle.processing_mode = (
            ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        )
        older_cycle.save(update_fields=["processing_mode", "updated_at"])
        older_cycle.academic_year.start_date = "2024-06-01"
        older_cycle.academic_year.end_date = "2025-05-31"
        older_cycle.academic_year.save(
            update_fields=["start_date", "end_date", "updated_at"]
        )
        older = self.make_course(cycle=older_cycle, code="OLDER-READINESS")
        older_configuration = self.make_configuration(
            older,
            quota=50,
            quota_source="DEFAULT",
            workflow=CourseExamConfiguration.WorkflowStatus.OPEN,
            opened_at=timezone.now(),
        )
        FacultyContribution.objects.create(
            cycle_course=older,
            faculty_user=self.generation_manager,
            source_campus=self.campus,
            quota_snapshot=50,
            configuration_revision_snapshot=older_configuration.revision,
        )
        selected, _configuration, _campuses, _offerings = self._automatic_course(
            campus_codes=("CUBAO",), quota=50
        )
        url = reverse("departmental_exams:automatic_generation_readiness")

        with patch.object(
            Stage6ReadinessService,
            "evaluate_automatic_pool",
            wraps=Stage6ReadinessService.evaluate_automatic_pool,
        ) as evaluator, patch(
            "apps.departmental_exams.generation_readiness.solve_automatic_identity_aware_two_sets",
            return_value=self._feasible_selection(),
        ):
            with CaptureQueriesContext(connection) as captured:
                response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_cycle_id"], selected.cycle_id)
        self.assertEqual(evaluator.call_count, 1)
        self.assertEqual(
            evaluator.call_args.kwargs["cycle_course"].id,
            selected.id,
        )
        contribution_prefetches = [
            query["sql"]
            for query in captured.captured_queries
            if 'FROM "departmental_exam_faculty_contributions"' in query["sql"]
            and '"cycle_course_id" IN' in query["sql"]
        ]
        self.assertEqual(len(contribution_prefetches), 1)
        self.assertIn(
            f'"cycle_course_id" IN ({selected.id})',
            contribution_prefetches[0],
        )
        self.assertNotIn(
            f'"cycle_course_id" IN ({historical.id})',
            contribution_prefetches[0],
        )
        self.assertNotIn(
            f'"cycle_course_id" IN ({older.id})',
            contribution_prefetches[0],
        )

        with patch.object(
            Stage6ReadinessService, "evaluate_automatic_pool"
        ) as invalid_evaluator:
            with CaptureQueriesContext(connection) as invalid_queries:
                invalid = self.client.get(url, {"cycle": "stale-cycle"})
        self.assertEqual(invalid.status_code, 200)
        self.assertTrue(invalid.context["filters_invalid"])
        self.assertEqual(invalid.context["rows"], [])
        invalid_evaluator.assert_not_called()
        self.assertFalse(
            any(
                'FROM "departmental_exam_faculty_contributions"' in query["sql"]
                and '"cycle_course_id" IN' in query["sql"]
                for query in invalid_queries.captured_queries
            )
        )

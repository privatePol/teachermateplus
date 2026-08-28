from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models.deletion import ProtectedError
from django.utils import timezone

from apps.academics.models import CourseOffering, Section
from apps.auditlog.models import AuditLog
from apps.rbac.models import Permission, UserPermission, UserRole
from apps.tenants.models import Campus, Department, Program

from .answer_key_release import FacultyAnswerKeyReleaseService
from .automatic_workflow import (
    AutomaticContributionReopenService,
    AutomaticExamDeadlineService,
    AutomaticGenerationSummaryService,
)
from .automatic_generation_readiness import AutomaticGenerationReadinessReport
from .exam_units import ExamCourseEquivalencyService, resolve_examination_unit
from .generation_readiness import Stage6ReadinessService
from .generation_services import ExamGenerationService
from .models import (
    AnswerKeyRelease,
    CourseExamConfiguration,
    CycleCourse,
    CycleCourseOffering,
    ExamCourseEquivalencyGroup,
    ExamGenerationRevision,
    ExamCourseEquivalencyMembership,
    ExaminationCycle,
    FacultyContribution,
    GeneratedExamSet,
    PersonalizedAnswerSheetAssignment,
    Question,
    QuestionnairePrintRelease,
)
from .personalized_answer_sheets import PersonalizedAnswerSheetService
from .questionnaire_printing import FacultyQuestionnairePrintService
from .services import DepartmentalExamAuthorizationService
from .stage4_test_support import Stage4TestCase


class ExamCourseEquivalencyTests(Stage4TestCase):
    def _configuration(self, course, *, deadline, final_count=50, quota=50):
        configuration = self.make_configuration(
            course,
            deadline=deadline,
            final_count=final_count,
            quota=quota,
            workflow=CourseExamConfiguration.WorkflowStatus.CLOSED,
            opened_at=timezone.now() - timezone.timedelta(days=2),
        )
        configuration.contributor_roster_initialized_at = timezone.now()
        configuration.contributor_roster_initialized_by = self.admin
        configuration.contributor_roster_revision = 1
        configuration.save(
            update_fields=[
                "contributor_roster_initialized_at",
                "contributor_roster_initialized_by",
                "contributor_roster_revision",
                "updated_at",
            ]
        )
        return configuration

    def _pair(
        self,
        *,
        final_counts=(50, 50),
        quotas=(50, 50),
        deadline=None,
        scope_suffix=None,
    ):
        deadline = deadline or timezone.now() - timezone.timedelta(minutes=5)
        cycle = self.make_cycle(
            status=ExaminationCycle.Status.OPEN,
            scope_suffix=scope_suffix,
        )
        cycle.processing_mode = ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        cycle.save(update_fields=["processing_mode", "updated_at"])
        primary = self.make_course(cycle=cycle, code="EQ-P")
        secondary = self.make_course(cycle=cycle, code="EQ-S")
        configurations = (
            self._configuration(
                primary,
                deadline=deadline,
                final_count=final_counts[0],
                quota=quotas[0],
            ),
            self._configuration(
                secondary,
                deadline=deadline,
                final_count=final_counts[1],
                quota=quotas[1],
            ),
        )
        return cycle, primary, secondary, configurations

    def _group(self, *, cycle, primary, secondary, name="Shared Examination"):
        return ExamCourseEquivalencyService.create_group(
            cycle_id=cycle.id,
            name=name,
            primary_cycle_course_id=primary.id,
            member_ids=(secondary.id, primary.id),
            actor=self.admin,
        )

    def _other_automatic_cycle(self, *, suffix):
        cycle = self.make_cycle(
            status=ExaminationCycle.Status.OPEN,
            scope_suffix=suffix,
        )
        cycle.processing_mode = ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        cycle.save(update_fields=["processing_mode", "updated_at"])
        return cycle

    def _add_offering_snapshot(self, *, cycle_course, campus, department, suffix):
        program = Program.objects.create(
            tenant=self.tenant,
            campus=campus,
            department=department,
            code=f"EQP-{suffix}",
            name=f"Equivalency Program {suffix}",
        )
        section = Section.objects.create(
            tenant=self.tenant,
            campus=campus,
            department=department,
            program=program,
            code=f"EQS-{suffix}",
            name=f"Equivalency Section {suffix}",
        )
        offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=campus,
            department=department,
            program=program,
            academic_year=cycle_course.cycle.academic_year,
            term=cycle_course.cycle.term,
            course=cycle_course.course,
            section=section,
        )
        CycleCourseOffering.objects.create(
            cycle_course=cycle_course,
            offering=offering,
            campus=campus,
        )
        return offering

    def _primary_only_manager(self, username="eq-primary-manager"):
        return self.make_user(
            username,
            self.department,
            ("departmental_exams.manage_exam_generation",),
        )

    def _global_manager(self, username="eq-global-manager"):
        user = self.make_user(username, self.department, ())
        UserPermission.objects.create(
            user=user,
            permission=Permission.objects.get(
                code="departmental_exams.manage_exam_generation"
            ),
            grant_type=UserPermission.GrantType.ALLOW,
            tenant=None,
            campus=None,
        )
        return user

    def _add_other_campus_scope(self, *, cycle_course, suffix="AUTH"):
        return self._add_offering_snapshot(
            cycle_course=cycle_course,
            campus=self.other_campus,
            department=self.other_department,
            suffix=suffix,
        )

    def _submitted_contribution(self, *, course, faculty, campus, configuration):
        return FacultyContribution.objects.create(
            cycle_course=course,
            faculty_user=faculty,
            source_campus=campus,
            quota_snapshot=configuration.questions_required_per_faculty,
            configuration_revision_snapshot=configuration.revision,
            roster_status=FacultyContribution.RosterStatus.ACTIVE,
            status=FacultyContribution.Status.SUBMITTED,
            submitted_at=timezone.now(),
        )

    @staticmethod
    def _questions(contribution, *, start, counts):
        rows = []
        position = 1
        serial = start
        for difficulty, count in counts:
            for _index in range(count):
                rows.append(
                    Question(
                        contribution=contribution,
                        question_text=(
                            f"Equivalency question {serial}: which option is correct?"
                        ),
                        choice_a=f"Correct {serial}",
                        choice_b=f"Alternative B {serial}",
                        choice_c=f"Alternative C {serial}",
                        choice_d=f"Alternative D {serial}",
                        correct_answer="A",
                        difficulty=difficulty,
                        position=position,
                    )
                )
                serial += 1
                position += 1
        Question.objects.bulk_create(rows)
        return rows

    def _ready_group(self, *, missing_third_questions=False, campus_policy=None):
        cycle, primary, secondary, configurations = self._pair()
        if campus_policy:
            cycle.automatic_campus_contribution_policy = campus_policy
            cycle.save(
                update_fields=["automatic_campus_contribution_policy", "updated_at"]
            )
        third_campus = Campus.objects.create(
            tenant=self.tenant, code="THIRD", name="Third Campus"
        )
        third_department = Department.objects.create(
            tenant=self.tenant,
            campus=third_campus,
            code="THIRD-EXAM",
            name="Third Exam",
        )
        self._add_offering_snapshot(
            cycle_course=primary,
            campus=self.other_campus,
            department=self.other_department,
            suffix="NORTH",
        )
        third_offering = self._add_offering_snapshot(
            cycle_course=secondary,
            campus=third_campus,
            department=third_department,
            suffix="THIRD",
        )
        shared_faculty = self.make_user("eq-shared-faculty", self.department, ())
        third_faculty = self.make_user("eq-third-faculty", third_department, ())
        main_contribution = self._submitted_contribution(
            course=primary,
            faculty=shared_faculty,
            campus=self.campus,
            configuration=configurations[0],
        )
        north_contribution = self._submitted_contribution(
            course=secondary,
            faculty=shared_faculty,
            campus=self.other_campus,
            configuration=configurations[1],
        )
        third_contribution = self._submitted_contribution(
            course=secondary,
            faculty=third_faculty,
            campus=third_campus,
            configuration=configurations[1],
        )
        self._questions(
            main_contribution,
            start=1,
            counts=(("EASY", 5), ("MODERATE", 9), ("DIFFICULT", 3)),
        )
        self._questions(
            north_contribution,
            start=18,
            counts=(("EASY", 5), ("MODERATE", 8), ("DIFFICULT", 4)),
        )
        if not missing_third_questions:
            self._questions(
                third_contribution,
                start=35,
                counts=(("EASY", 5), ("MODERATE", 8), ("DIFFICULT", 3)),
            )
        group = self._group(cycle=cycle, primary=primary, secondary=secondary)
        return {
            "cycle": cycle,
            "primary": primary,
            "secondary": secondary,
            "configurations": configurations,
            "group": group,
            "contributions": (
                main_contribution,
                north_contribution,
                third_contribution,
            ),
            "third_campus": third_campus,
            "third_offering": third_offering,
        }

    @staticmethod
    def _revision(course, *, token="c" * 64):
        return ExamGenerationRevision.objects.create(
            cycle_course=course,
            revision_number=1,
            source_input_fingerprint="a" * 64,
            algorithm_version="equivalency-test-v1",
            generation_trigger=ExamGenerationRevision.GenerationTrigger.AUTOMATIC,
            configuration_revision_snapshot=1,
            blueprint_revision_snapshot=1,
            roster_boundary_snapshot="b" * 64,
            final_item_count_snapshot=50,
            request_token_digest=token,
            minimum_overlap=0,
            proportional_score=0,
            contributors_represented=1,
            squared_contributor_concentration=1,
        )

    def test_ordinary_automatic_course_resolves_as_unchanged_singleton(self):
        _cycle, primary, _secondary, _configurations = self._pair()
        unit = resolve_examination_unit(primary)
        self.assertEqual(unit.primary, primary)
        self.assertEqual(unit.members, (primary,))
        self.assertIsNone(unit.group)

    def test_two_member_group_resolves_one_primary_unit(self):
        cycle, primary, secondary, _configurations = self._pair()
        group = self._group(cycle=cycle, primary=primary, secondary=secondary)
        unit = resolve_examination_unit(primary)
        self.assertEqual(unit.group, group)
        self.assertEqual(unit.primary, primary)
        self.assertEqual(set(unit.member_ids), {primary.id, secondary.id})

    def test_secondary_resolves_to_recorded_primary(self):
        cycle, primary, secondary, _configurations = self._pair()
        self._group(cycle=cycle, primary=primary, secondary=secondary)
        self.assertEqual(resolve_examination_unit(secondary).primary, primary)

    def test_combined_submitted_pool_uses_all_members(self):
        fixture = self._ready_group()
        report = Stage6ReadinessService.evaluate(cycle_course=fixture["secondary"])
        self.assertEqual(report["submitted_question_count"], 50)
        self.assertTrue(report["ready"], report["blockers"])

    def test_draft_questions_remain_excluded(self):
        fixture = self._ready_group()
        draft = FacultyContribution.objects.create(
            cycle_course=fixture["primary"],
            faculty_user=self.admin,
            source_campus=self.campus,
            quota_snapshot=50,
            configuration_revision_snapshot=1,
            roster_status=FacultyContribution.RosterStatus.BLOCKED,
            roster_blocked_at=timezone.now(),
        )
        self._questions(draft, start=500, counts=(("EASY", 1),))
        report = Stage6ReadinessService.evaluate(cycle_course=fixture["primary"])
        self.assertEqual(report["submitted_question_count"], 50)

    def test_group_creation_does_not_move_contributions_or_questions(self):
        fixture = self._ready_group()
        contribution_courses = list(
            FacultyContribution.objects.order_by("id").values_list(
                "cycle_course_id", flat=True
            )
        )
        question_courses = list(
            Question.objects.order_by("id").values_list(
                "contribution__cycle_course_id", flat=True
            )
        )
        self.assertIn(fixture["primary"].id, contribution_courses)
        self.assertIn(fixture["secondary"].id, contribution_courses)
        self.assertEqual(set(question_courses), set(contribution_courses))

    def test_campus_union_is_allocated_once_without_duplicates(self):
        fixture = self._ready_group()
        report = Stage6ReadinessService.evaluate(cycle_course=fixture["primary"])
        self.assertEqual(set(report["campus_quotas"]), {
            self.campus.id,
            self.other_campus.id,
            fixture["third_campus"].id,
        })
        self.assertEqual(sum(report["campus_quotas"].values()), 50)

    def test_cross_member_normalized_duplicates_deduplicate_once(self):
        fixture = self._ready_group()
        source = Question.objects.filter(
            contribution__cycle_course=fixture["primary"]
        ).first()
        contribution = fixture["contributions"][1]
        Question.objects.create(
            contribution=contribution,
            question_text=f"  {source.question_text.upper()}  ",
            choice_a="Duplicate A",
            choice_b="Duplicate B",
            choice_c="Duplicate C",
            choice_d="Duplicate D",
            correct_answer="A",
            difficulty=source.difficulty,
            position=99,
        )
        report = Stage6ReadinessService.evaluate(cycle_course=fixture["secondary"])
        self.assertEqual(report["submitted_question_count"], 51)
        self.assertEqual(report["unique_question_count"], 50)
        self.assertEqual(report["duplicate_question_count"], 1)

    def test_generation_creates_exactly_one_revision_and_two_sets(self):
        fixture = self._ready_group()
        problem, report = Stage6ReadinessService.build_problem(
            cycle_course=fixture["secondary"]
        )
        self.assertTrue(report["ready"], report["blockers"])
        outcome = ExamGenerationService.generate(
            cycle_course_id=fixture["secondary"].id,
            tenant_id=self.tenant.id,
            actor=None,
            expected_current_revision=0,
            expected_input_fingerprint=problem.input_fingerprint,
            request_token="g" * 64,
            generation_trigger=ExamGenerationRevision.GenerationTrigger.AUTOMATIC,
        )
        self.assertEqual(outcome.revision.cycle_course, fixture["primary"])
        self.assertEqual(ExamGenerationRevision.objects.count(), 1)
        self.assertEqual(GeneratedExamSet.objects.count(), 2)

    def test_automatic_worker_deduplicates_secondary_member(self):
        fixture = self._ready_group()
        results = AutomaticExamDeadlineService.process_due(now=timezone.now())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].cycle_course_id, fixture["primary"].id)
        self.assertFalse(
            ExamGenerationRevision.objects.filter(
                cycle_course=fixture["secondary"]
            ).exists()
        )

    def test_automatic_summary_reports_group_primary_once(self):
        fixture = self._ready_group()
        summary = AutomaticGenerationSummaryService.build(cycle=fixture["cycle"])
        rows = summary["not_generated"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["course"].id, fixture["primary"].id)
        self.assertEqual(rows[0]["eligible_contributors"], 3)

    def test_incompatible_final_item_count_is_rejected(self):
        cycle, primary, secondary, _configurations = self._pair(final_counts=(50, 51))
        with self.assertRaises(ValidationError):
            self._group(cycle=cycle, primary=primary, secondary=secondary)

    def test_incompatible_faculty_quota_is_rejected(self):
        cycle, primary, secondary, _configurations = self._pair(quotas=(50, 51))
        with self.assertRaises(ValidationError):
            self._group(cycle=cycle, primary=primary, secondary=secondary)

    def test_incompatible_effective_deadline_is_rejected(self):
        cycle, primary, secondary, configurations = self._pair()
        CourseExamConfiguration.objects.filter(pk=configurations[1].pk).update(
            reopened_contribution_deadline=timezone.now() + timezone.timedelta(hours=1)
        )
        with self.assertRaises(ValidationError):
            self._group(cycle=cycle, primary=primary, secondary=secondary)

    def test_included_exempt_mismatch_is_rejected(self):
        cycle, primary, secondary, _configurations = self._pair()
        secondary.inclusion_status = CycleCourse.InclusionStatus.EXEMPT
        secondary.save(update_fields=["inclusion_status", "updated_at"])
        with self.assertRaises(ValidationError):
            self._group(cycle=cycle, primary=primary, secondary=secondary)

    def test_inactive_course_master_does_not_invalidate_current_cycle_member(self):
        cycle, primary, secondary, _configurations = self._pair()
        secondary.course.is_active = False
        secondary.course.save(update_fields=["is_active", "updated_at"])
        self._group(cycle=cycle, primary=primary, secondary=secondary)
        self.assertEqual(resolve_examination_unit(secondary).primary, primary)

    def test_available_with_warning_preserves_missing_campus_semantics(self):
        fixture = self._ready_group(
            missing_third_questions=True,
            campus_policy=ExaminationCycle.AutomaticCampusContributionPolicy.AVAILABLE_WITH_WARNING,
        )
        report = Stage6ReadinessService.evaluate(cycle_course=fixture["primary"])
        self.assertIn(
            "MISSING_CAMPUS_REPRESENTATION",
            {row["code"] for row in report["warnings"]},
        )

    def test_strict_policy_blocks_missing_member_campus_representation(self):
        fixture = self._ready_group(
            missing_third_questions=True,
            campus_policy=ExaminationCycle.AutomaticCampusContributionPolicy.STRICT,
        )
        report = Stage6ReadinessService.evaluate(cycle_course=fixture["secondary"])
        self.assertIn(
            "MISSING_CAMPUS_REPRESENTATION",
            {row["code"] for row in report["blockers"]},
        )

    def test_same_faculty_on_two_member_courses_is_two_deterministic_obligations(self):
        fixture = self._ready_group()
        report = Stage6ReadinessService.evaluate(cycle_course=fixture["primary"])
        self.assertEqual(report["contributor_counts"]["required_active"], 3)
        self.assertEqual(report["contributor_counts"]["submitted_required"], 3)

    def test_generated_revision_remains_primary_owned(self):
        fixture = self._ready_group()
        problem, _report = Stage6ReadinessService.build_problem(
            cycle_course=fixture["secondary"]
        )
        outcome = ExamGenerationService.generate(
            cycle_course_id=fixture["secondary"].id,
            tenant_id=self.tenant.id,
            actor=None,
            expected_current_revision=0,
            expected_input_fingerprint=problem.input_fingerprint,
            request_token="p" * 64,
            generation_trigger=ExamGenerationRevision.GenerationTrigger.AUTOMATIC,
        )
        self.assertEqual(outcome.revision.cycle_course_id, fixture["primary"].id)

    def test_personalized_sheet_scope_accepts_secondary_offering(self):
        fixture = self._ready_group()
        revision = self._revision(fixture["primary"])
        release = QuestionnairePrintRelease(
            cycle_course=fixture["primary"],
            generation_revision=revision,
        )
        PersonalizedAnswerSheetService._validate_offering_scope(
            release=release,
            offering=fixture["third_offering"],
        )

    def test_personalized_assignment_model_maps_secondary_offering_to_primary_revision(self):
        fixture = self._ready_group()
        revision = self._revision(fixture["primary"])
        assignment = PersonalizedAnswerSheetAssignment(
            generation_revision=revision,
            course_offering=fixture["third_offering"],
        )
        assignment.clean()

    def test_secondary_contribution_can_resolve_primary_questionnaire_release(self):
        fixture = self._ready_group()
        revision = self._revision(fixture["primary"])
        GeneratedExamSet.objects.create(
            generation_revision=revision,
            set_code="A",
            campus_quotas_snapshot={},
            difficulty_quotas_snapshot={},
            section_quotas_snapshot={},
            item_count=50,
        )
        now = timezone.now()
        release = QuestionnairePrintRelease.objects.create(
            cycle_course=fixture["primary"],
            generation_revision=revision,
            print_from=now - timezone.timedelta(minutes=1),
            print_until=now + timezone.timedelta(minutes=1),
            released_by=self.admin,
        )
        with patch.object(
            FacultyQuestionnairePrintService,
            "_printable_release",
            wraps=FacultyQuestionnairePrintService._printable_release,
        ), patch(
            "apps.departmental_exams.questionnaire_printing."
            "ContributionAuthorizationService.has_retained_current_print_eligibility",
            return_value=True,
        ):
            resolved, _generated_set, _set_code = (
                FacultyQuestionnairePrintService._printable_release(
                    contribution=fixture["contributions"][1],
                    release_id=release.id,
                    set_code="A",
                    now=now,
                )
            )
        self.assertEqual(resolved.id, release.id)

    def test_secondary_contribution_sees_primary_answer_key_release(self):
        fixture = self._ready_group()
        revision = self._revision(fixture["primary"])
        now = timezone.now()
        release = AnswerKeyRelease.objects.create(
            cycle_course=fixture["primary"],
            generation_revision=revision,
            available_from=now - timezone.timedelta(minutes=1),
            available_until=now + timezone.timedelta(minutes=1),
            released_by=self.admin,
            attestation_version="equivalency-test-v1",
        )
        with patch(
            "apps.departmental_exams.answer_key_release._revision_is_current_final",
            return_value=True,
        ), patch(
            "apps.departmental_exams.answer_key_release._complete_sets",
            return_value=True,
        ), patch(
            "apps.departmental_exams.answer_key_release."
            "ContributionAuthorizationService.has_retained_current_print_eligibility",
            return_value=True,
        ):
            options = FacultyAnswerKeyReleaseService.available_options(
                contributions=(fixture["contributions"][1],),
                now=now,
            )
        self.assertEqual(
            options[fixture["contributions"][1].id]["release_id"],
            release.id,
        )

    def test_manual_review_equivalency_fails_closed(self):
        cycle, primary, secondary, _configurations = self._pair()
        self._group(cycle=cycle, primary=primary, secondary=secondary)
        cycle.processing_mode = ExaminationCycle.ProcessingMode.MANUAL_REVIEW
        cycle.save(update_fields=["processing_mode", "updated_at"])
        primary.cycle = cycle
        with self.assertRaises(ValidationError):
            resolve_examination_unit(primary)

    def test_cross_cycle_member_is_rejected(self):
        cycle, primary, _secondary, _configurations = self._pair()
        other_cycle, _other_primary, other_secondary, _other_configs = self._pair(
            scope_suffix="OTHER"
        )
        self.assertNotEqual(cycle.id, other_cycle.id)
        with self.assertRaises(ValidationError):
            self._group(cycle=cycle, primary=primary, secondary=other_secondary)

    def test_duplicate_active_membership_is_rejected(self):
        cycle, primary, secondary, _configurations = self._pair()
        self._group(cycle=cycle, primary=primary, secondary=secondary, name="First")
        third = self.make_course(cycle=cycle, code="EQ-T")
        self._configuration(
            third,
            deadline=CourseExamConfiguration.objects.get(
                cycle_course=primary
            ).active_contribution_deadline,
        )
        with self.assertRaises(ValidationError):
            ExamCourseEquivalencyService.create_group(
                cycle_id=cycle.id,
                name="Second",
                primary_cycle_course_id=secondary.id,
                member_ids=(secondary.id, third.id),
                actor=self.admin,
            )

    def test_generation_state_locks_membership_changes(self):
        cycle, primary, secondary, _configurations = self._pair()
        group = self._group(cycle=cycle, primary=primary, secondary=secondary)
        self._revision(primary)
        with self.assertRaises(ValidationError):
            ExamCourseEquivalencyService.replace_members(
                group_id=group.id,
                primary_cycle_course_id=primary.id,
                member_ids=(primary.id, secondary.id),
                actor=self.admin,
            )

    def test_grouped_reopen_updates_every_member_atomically(self):
        fixture = self._ready_group()
        new_deadline = timezone.now() + timezone.timedelta(days=1)
        with patch(
            "apps.departmental_exams.automatic_workflow."
            "DepartmentalExamAuthorizationService.require_generation_management"
        ), patch(
            "apps.departmental_exams.automatic_workflow."
            "ContributionRosterService._synchronize_locked"
        ):
            configuration = AutomaticContributionReopenService.reopen(
                cycle_course_id=fixture["secondary"].id,
                tenant_id=self.tenant.id,
                actor=self.admin,
                expected_revision=fixture["configurations"][0].revision,
                new_deadline=new_deadline,
            )
        rows = list(
            CourseExamConfiguration.objects.filter(
                cycle_course_id__in=(
                    fixture["primary"].id,
                    fixture["secondary"].id,
                )
            ).order_by("cycle_course_id")
        )
        self.assertEqual(configuration.cycle_course_id, fixture["primary"].id)
        self.assertTrue(
            all(
                row.workflow_status == CourseExamConfiguration.WorkflowStatus.OPEN
                for row in rows
            )
        )
        self.assertEqual(
            {row.reopened_contribution_deadline for row in rows},
            {new_deadline.replace(second=0, microsecond=0)},
        )
        self.assertEqual(
            resolve_examination_unit(fixture["secondary"]).primary,
            fixture["primary"],
        )

    def test_unit_authority_across_every_member_campus_allows_group_creation(self):
        cycle, primary, secondary, _configurations = self._pair()
        self._add_other_campus_scope(cycle_course=secondary)
        actor = self._primary_only_manager("eq-all-campus")
        role = actor.user_roles.get()
        UserRole.objects.create(
            user=actor,
            role=role.role,
            tenant=self.tenant,
            campus=self.other_campus,
            department=self.other_department,
        )
        group = ExamCourseEquivalencyService.create_group(
            cycle_id=cycle.id,
            name="All Campus Authority",
            primary_cycle_course_id=primary.id,
            member_ids=(primary.id, secondary.id),
            actor=actor,
        )
        self.assertTrue(group.is_active)

    def test_primary_only_authority_denies_group_creation_and_mutation(self):
        cycle, primary, secondary, _configurations = self._pair()
        self._add_other_campus_scope(cycle_course=secondary)
        actor = self._primary_only_manager()
        with self.assertRaises(PermissionDenied):
            ExamCourseEquivalencyService.create_group(
                cycle_id=cycle.id,
                name="Unauthorized Creation",
                primary_cycle_course_id=primary.id,
                member_ids=(primary.id, secondary.id),
                actor=actor,
            )
        group = self._group(cycle=cycle, primary=primary, secondary=secondary)
        with self.assertRaises(PermissionDenied):
            ExamCourseEquivalencyService.replace_members(
                group_id=group.id,
                primary_cycle_course_id=primary.id,
                member_ids=(primary.id, secondary.id),
                actor=actor,
            )

    def test_direct_deny_on_one_member_campus_denies_complete_unit(self):
        cycle, primary, secondary, _configurations = self._pair()
        self._add_other_campus_scope(cycle_course=secondary)
        actor = self._global_manager("eq-denied-manager")
        UserPermission.objects.create(
            user=actor,
            permission=Permission.objects.get(
                code="departmental_exams.manage_exam_generation"
            ),
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.other_campus,
        )
        with self.assertRaises(PermissionDenied):
            ExamCourseEquivalencyService.create_group(
                cycle_id=cycle.id,
                name="Denied Campus",
                primary_cycle_course_id=primary.id,
                member_ids=(primary.id, secondary.id),
                actor=actor,
            )

    def test_global_null_scope_authorizes_complete_unit(self):
        cycle, primary, secondary, _configurations = self._pair()
        self._add_other_campus_scope(cycle_course=secondary)
        group = ExamCourseEquivalencyService.create_group(
            cycle_id=cycle.id,
            name="Global Scope",
            primary_cycle_course_id=primary.id,
            member_ids=(primary.id, secondary.id),
            actor=self._global_manager(),
        )
        self.assertEqual(group.primary_cycle_course_id, primary.id)

    def test_cross_tenant_member_authorization_fails_closed(self):
        cycle, primary, _secondary, _configurations = self._pair()
        other_cycle = ExaminationCycle.objects.create(
            tenant=self.other_tenant,
            academic_year=self.year,
            term=self.term,
            exam_period=ExaminationCycle.ExamPeriod.FINAL,
            status=ExaminationCycle.Status.OPEN,
            processing_mode=ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION,
            created_by=self.admin,
        )
        foreign_course = self.make_course(cycle=other_cycle, code="EQ-FOREIGN")
        with self.assertRaises(PermissionDenied):
            DepartmentalExamAuthorizationService.require_automatic_courses_permission(
                user=self.admin,
                cycle=cycle,
                courses=(primary, foreign_course),
                permissions=(
                    DepartmentalExamAuthorizationService.MANAGE_GENERATION_PERMISSION,
                ),
            )

    def test_regeneration_and_release_actions_require_complete_unit_authority(self):
        cycle, primary, secondary, _configurations = self._pair()
        self._add_other_campus_scope(cycle_course=secondary)
        self._group(cycle=cycle, primary=primary, secondary=secondary)
        actor = self._primary_only_manager("eq-operation-manager")
        with self.assertRaises(PermissionDenied):
            ExamGenerationService.generate(
                cycle_course_id=primary.id,
                tenant_id=self.tenant.id,
                actor=actor,
                expected_current_revision=0,
                expected_input_fingerprint="x" * 64,
                request_token="y" * 64,
                regeneration=True,
            )
        from .answer_key_release import AnswerKeyReleaseService
        from .questionnaire_printing import QuestionnairePrintReleaseService

        now = timezone.now()
        with self.assertRaises(PermissionDenied):
            QuestionnairePrintReleaseService.release(
                cycle_course_id=primary.id,
                revision_id=999999,
                tenant_id=self.tenant.id,
                actor=actor,
                print_from=now,
                print_until=now + timezone.timedelta(hours=1),
            )
        with self.assertRaises(PermissionDenied):
            AnswerKeyReleaseService.release(
                cycle_course_id=primary.id,
                revision_id=999999,
                tenant_id=self.tenant.id,
                actor=actor,
                available_from=now,
                available_until=now + timezone.timedelta(hours=1),
                attestation_confirmed=True,
            )
        revision = self._revision(primary, token="u" * 64)
        questionnaire_release = QuestionnairePrintRelease.objects.create(
            cycle_course=primary,
            generation_revision=revision,
            print_from=now,
            print_until=now + timezone.timedelta(hours=1),
            released_by=self.admin,
        )
        answer_key_release = AnswerKeyRelease.objects.create(
            cycle_course=primary,
            generation_revision=revision,
            available_from=now,
            available_until=now + timezone.timedelta(hours=1),
            released_by=self.admin,
            attestation_version="equivalency-unit-auth-v1",
        )
        with self.assertRaises(PermissionDenied):
            QuestionnairePrintReleaseService.revoke(
                release_id=questionnaire_release.id,
                tenant_id=self.tenant.id,
                actor=actor,
            )
        with self.assertRaises(PermissionDenied):
            AnswerKeyReleaseService.revoke(
                release_id=answer_key_release.id,
                tenant_id=self.tenant.id,
                actor=actor,
            )

    def test_readiness_report_groups_two_members_once_with_combined_pool_and_campuses(self):
        fixture = self._ready_group()
        report = AutomaticGenerationReadinessReport(
            tenant_id=self.tenant.id,
            user=self.admin,
            params={},
        ).build()
        self.assertEqual(report["row_count"], 1)
        row = report["rows"][0]
        self.assertEqual(row["cycle_course"].id, fixture["primary"].id)
        self.assertEqual(row["contribution_progress"]["submitted_question_volume"], 50)
        self.assertEqual(
            set(row["campuses"]),
            {"Main", "North", "Third Campus"},
        )

    def test_readiness_report_skips_solver_and_uses_primary_generation_state(self):
        fixture = self._ready_group()
        revision = self._revision(fixture["primary"], token="r" * 64)
        with patch(
            "apps.departmental_exams.generation_readiness."
            "solve_automatic_identity_aware_two_sets",
            wraps=__import__(
                "apps.departmental_exams.generation_readiness",
                fromlist=["solve_automatic_identity_aware_two_sets"],
            ).solve_automatic_identity_aware_two_sets,
        ) as solver:
            report = AutomaticGenerationReadinessReport(
                tenant_id=self.tenant.id,
                user=self.admin,
                params={},
            ).build()
        solver.assert_not_called()
        self.assertEqual(report["row_count"], 1)
        self.assertEqual(report["rows"][0]["generation_status"], "GENERATED")
        self.assertEqual(report["rows"][0]["cycle_course"].id, revision.cycle_course_id)

    def test_readiness_report_keeps_ordinary_course_as_one_row(self):
        cycle, primary, _secondary, _configurations = self._pair()
        with patch.object(
            Stage6ReadinessService,
            "evaluate_automatic_pool",
            return_value={
                "ready": False,
                "blockers": ({"code": "QUESTION_SHORTAGES"},),
                "warnings": (),
                "shortages": (),
                "invalid_question_count": 0,
            },
        ):
            report = AutomaticGenerationReadinessReport(
                tenant_id=self.tenant.id,
                user=self.admin,
                params={"cycle": str(cycle.id), "course": str(primary.course_id)},
            ).build()
        self.assertEqual(report["row_count"], 1)
        self.assertEqual(report["rows"][0]["cycle_course"].id, primary.id)

    def test_partial_or_denied_member_scope_hides_group_readiness(self):
        fixture = self._ready_group()
        ordinary = self.make_course(cycle=fixture["cycle"], code="EQ-ORDINARY")
        self._configuration(
            ordinary,
            deadline=fixture["configurations"][0].active_contribution_deadline,
        )
        partial = self._primary_only_manager("eq-readiness-partial")
        with patch.object(
            Stage6ReadinessService,
            "evaluate_automatic_pool",
            return_value={
                "ready": False,
                "blockers": ({"code": "QUESTION_SHORTAGES"},),
                "warnings": (),
                "shortages": (),
                "invalid_question_count": 0,
            },
        ):
            report = AutomaticGenerationReadinessReport(
                tenant_id=self.tenant.id,
                user=partial,
                params={},
            ).build()
        self.assertEqual([row["cycle_course"].id for row in report["rows"]], [ordinary.id])

        denied = self._global_manager("eq-readiness-denied")
        UserPermission.objects.create(
            user=denied,
            permission=Permission.objects.get(
                code="departmental_exams.manage_exam_generation"
            ),
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=fixture["third_campus"],
        )
        with patch.object(
            Stage6ReadinessService,
            "evaluate_automatic_pool",
            return_value={
                "ready": False,
                "blockers": ({"code": "QUESTION_SHORTAGES"},),
                "warnings": (),
                "shortages": (),
                "invalid_question_count": 0,
            },
        ):
            denied_report = AutomaticGenerationReadinessReport(
                tenant_id=self.tenant.id,
                user=denied,
                params={},
            ).build()
        self.assertEqual(
            [row["cycle_course"].id for row in denied_report["rows"]],
            [ordinary.id],
        )

    def test_safe_retirement_records_audit_lifecycle_and_returns_singletons(self):
        cycle, primary, secondary, _configurations = self._pair()
        group = self._group(cycle=cycle, primary=primary, secondary=secondary)
        retired = ExamCourseEquivalencyService.retire_group(
            group_id=group.id,
            actor=self.admin,
            reason="The shared examination is no longer required.",
        )
        retired.refresh_from_db()
        self.assertFalse(retired.is_active)
        self.assertEqual(retired.retired_by, self.admin)
        self.assertIsNotNone(retired.retired_at)
        self.assertEqual(
            retired.retirement_reason,
            "The shared examination is no longer required.",
        )
        self.assertFalse(
            ExamCourseEquivalencyMembership.objects.filter(
                group=retired, active_marker=1
            ).exists()
        )
        self.assertEqual(
            set(
                ExamCourseEquivalencyMembership.objects.filter(group=retired)
                .values_list("active_marker", flat=True)
            ),
            {None},
        )
        self.assertTrue(
            AuditLog.objects.filter(
                action="DE_EXAM_COURSE_EQUIVALENCY_RETIRED",
                entity_type="ExamCourseEquivalencyGroup",
                entity_id=retired.id,
                actor_user=self.admin,
            ).exists()
        )
        self.assertEqual(resolve_examination_unit(primary).members, (primary,))
        self.assertEqual(resolve_examination_unit(secondary).members, (secondary,))

    def test_direct_group_deactivation_cannot_bypass_membership_retirement(self):
        cycle, primary, secondary, _configurations = self._pair()
        group = self._group(cycle=cycle, primary=primary, secondary=secondary)
        group.is_active = False
        group.retired_by = self.admin
        group.retired_at = timezone.now()
        group.retirement_reason = "Attempt to bypass the protected retirement service."
        group._protected_retirement = True
        with self.assertRaises(ValidationError):
            group.save()

    def test_direct_group_reactivation_is_rejected(self):
        cycle, primary, secondary, _configurations = self._pair()
        group = self._group(cycle=cycle, primary=primary, secondary=secondary)
        ExamCourseEquivalencyService.retire_group(
            group_id=group.id,
            actor=self.admin,
            reason="Retire before testing the permanent lifecycle boundary.",
        )
        group.refresh_from_db()
        group.is_active = True
        group.retired_by = None
        group.retired_at = None
        group.retirement_reason = ""
        with self.assertRaises(ValidationError):
            group.save()

    def test_direct_group_cycle_save_is_rejected(self):
        cycle, primary, secondary, _configurations = self._pair()
        group = self._group(cycle=cycle, primary=primary, secondary=secondary)
        group.cycle = self._other_automatic_cycle(suffix="CYCLE-OBJECT")
        with self.assertRaises(ValidationError):
            group.save()
        group.refresh_from_db()
        self.assertEqual(group.cycle_id, cycle.id)

    def test_direct_group_cycle_id_save_is_rejected(self):
        cycle, primary, secondary, _configurations = self._pair()
        group = self._group(cycle=cycle, primary=primary, secondary=secondary)
        group.cycle_id = self._other_automatic_cycle(suffix="CYCLE-ID").id
        with self.assertRaises(ValidationError):
            group.save()
        group.refresh_from_db()
        self.assertEqual(group.cycle_id, cycle.id)

    def test_direct_group_cycle_update_fields_save_is_rejected(self):
        cycle, primary, secondary, _configurations = self._pair()
        group = self._group(cycle=cycle, primary=primary, secondary=secondary)
        group.cycle_id = self._other_automatic_cycle(suffix="CYCLE-FIELDS").id
        with self.assertRaises(ValidationError):
            group.save(update_fields=["cycle"])
        group.refresh_from_db()
        self.assertEqual(group.cycle_id, cycle.id)

    def test_group_cycle_queryset_update_is_rejected(self):
        cycle, primary, secondary, _configurations = self._pair()
        group = self._group(cycle=cycle, primary=primary, secondary=secondary)
        other_cycle = self._other_automatic_cycle(suffix="CYCLE-QS")
        with self.assertRaises(ValidationError):
            ExamCourseEquivalencyGroup.objects.filter(pk=group.pk).update(
                cycle=other_cycle
            )
        group.refresh_from_db()
        self.assertEqual(group.cycle_id, cycle.id)

    def test_group_cycle_id_queryset_update_is_rejected(self):
        cycle, primary, secondary, _configurations = self._pair()
        group = self._group(cycle=cycle, primary=primary, secondary=secondary)
        other_cycle = self._other_automatic_cycle(suffix="CYCLE-ID-QS")
        with self.assertRaises(ValidationError):
            ExamCourseEquivalencyGroup.objects.filter(pk=group.pk).update(
                cycle_id=other_cycle.id
            )
        group.refresh_from_db()
        self.assertEqual(group.cycle_id, cycle.id)

    def test_group_cycle_bulk_update_is_rejected(self):
        cycle, primary, secondary, _configurations = self._pair()
        group = self._group(cycle=cycle, primary=primary, secondary=secondary)
        group.cycle = self._other_automatic_cycle(suffix="CYCLE-BULK")
        with self.assertRaises(ValidationError):
            ExamCourseEquivalencyGroup.objects.bulk_update([group], ["cycle"])
        group.refresh_from_db()
        self.assertEqual(group.cycle_id, cycle.id)

    def test_authorized_group_creation_still_succeeds_with_immutable_cycle(self):
        cycle, primary, secondary, _configurations = self._pair()
        group = self._group(cycle=cycle, primary=primary, secondary=secondary)
        self.assertTrue(group.is_active)
        self.assertEqual(group.cycle_id, cycle.id)

    def test_authorized_replace_and_retire_still_succeed(self):
        cycle, primary, secondary, _configurations = self._pair()
        group = self._group(cycle=cycle, primary=primary, secondary=secondary)
        replaced = ExamCourseEquivalencyService.replace_members(
            group_id=group.id,
            primary_cycle_course_id=secondary.id,
            member_ids=(primary.id, secondary.id),
            actor=self.admin,
        )
        self.assertEqual(replaced.primary_cycle_course_id, secondary.id)
        retired = ExamCourseEquivalencyService.retire_group(
            group_id=group.id,
            actor=self.admin,
            reason="Retire after an authorized member replacement.",
        )
        self.assertFalse(retired.is_active)
        self.assertEqual(retired.cycle_id, cycle.id)

    def test_group_bulk_create_is_rejected(self):
        cycle, primary, secondary, _configurations = self._pair()
        candidate = ExamCourseEquivalencyGroup(
            cycle=cycle,
            name="Bulk-created group",
            primary_cycle_course=primary,
            created_by=self.admin,
            updated_by=self.admin,
        )
        with self.assertRaises(ValidationError):
            ExamCourseEquivalencyGroup.objects.bulk_create([candidate])

    def test_membership_bulk_create_is_rejected(self):
        cycle, primary, secondary, _configurations = self._pair()
        group = self._group(cycle=cycle, primary=primary, secondary=secondary)
        third = self.make_course(cycle=cycle, code="EQ-BULK")
        candidate = ExamCourseEquivalencyMembership(
            group=group,
            cycle_course=third,
            added_by=self.admin,
        )
        with self.assertRaises(ValidationError):
            ExamCourseEquivalencyMembership.objects.bulk_create([candidate])

    def test_direct_membership_lifecycle_save_is_rejected(self):
        cycle, primary, secondary, _configurations = self._pair()
        group = self._group(cycle=cycle, primary=primary, secondary=secondary)
        membership = ExamCourseEquivalencyMembership.objects.get(
            group=group,
            cycle_course=secondary,
        )
        membership.active_marker = None
        membership.removed_by = self.admin
        membership.removed_at = timezone.now()
        membership.full_clean()
        with self.assertRaises(ValidationError):
            membership.save()
        membership.refresh_from_db()
        self.assertEqual(membership.active_marker, 1)
        self.assertIsNone(membership.removed_by_id)
        self.assertIsNone(membership.removed_at)

    def test_direct_active_membership_delete_is_rejected(self):
        cycle, primary, secondary, _configurations = self._pair()
        group = self._group(cycle=cycle, primary=primary, secondary=secondary)
        membership = ExamCourseEquivalencyMembership.objects.get(
            group=group,
            cycle_course=secondary,
        )
        with self.assertRaises(ValidationError):
            membership.delete()
        self.assertTrue(
            ExamCourseEquivalencyMembership.objects.filter(pk=membership.pk).exists()
        )

    def test_historical_membership_queryset_delete_is_rejected(self):
        cycle, primary, secondary, _configurations = self._pair()
        group = self._group(cycle=cycle, primary=primary, secondary=secondary)
        ExamCourseEquivalencyService.retire_group(
            group_id=group.id,
            actor=self.admin,
            reason="Retire before testing historical record deletion protection.",
        )
        with self.assertRaises(ValidationError):
            ExamCourseEquivalencyMembership.objects.filter(group=group).delete()
        self.assertEqual(
            ExamCourseEquivalencyMembership.objects.filter(group=group).count(),
            2,
        )

    def test_group_lifecycle_queryset_update_is_rejected(self):
        cycle, primary, secondary, _configurations = self._pair()
        group = self._group(cycle=cycle, primary=primary, secondary=secondary)
        with self.assertRaises(ValidationError):
            ExamCourseEquivalencyGroup.objects.filter(pk=group.pk).update(
                is_active=False,
                retired_by=self.admin,
                retired_at=timezone.now(),
                retirement_reason="Attempt lifecycle retirement through bulk ORM update.",
            )
        group.refresh_from_db()
        self.assertTrue(group.is_active)

    def test_membership_lifecycle_queryset_update_is_rejected(self):
        cycle, primary, secondary, _configurations = self._pair()
        group = self._group(cycle=cycle, primary=primary, secondary=secondary)
        membership = ExamCourseEquivalencyMembership.objects.get(
            group=group,
            cycle_course=secondary,
        )
        with self.assertRaises(ValidationError):
            ExamCourseEquivalencyMembership.objects.filter(pk=membership.pk).update(
                active_marker=None,
                removed_by=self.admin,
                removed_at=timezone.now(),
            )
        membership.refresh_from_db()
        self.assertEqual(membership.active_marker, 1)

    def test_group_lifecycle_bulk_update_is_rejected(self):
        cycle, primary, secondary, _configurations = self._pair()
        group = self._group(cycle=cycle, primary=primary, secondary=secondary)
        group.is_active = False
        group.retired_by = self.admin
        group.retired_at = timezone.now()
        group.retirement_reason = "Attempt lifecycle retirement through bulk update."
        with self.assertRaises(ValidationError):
            ExamCourseEquivalencyGroup.objects.bulk_update(
                [group],
                ["is_active", "retired_by", "retired_at", "retirement_reason"],
            )
        group.refresh_from_db()
        self.assertTrue(group.is_active)

    def test_membership_lifecycle_bulk_update_is_rejected(self):
        cycle, primary, secondary, _configurations = self._pair()
        group = self._group(cycle=cycle, primary=primary, secondary=secondary)
        membership = ExamCourseEquivalencyMembership.objects.get(
            group=group,
            cycle_course=secondary,
        )
        membership.active_marker = None
        membership.removed_by = self.admin
        membership.removed_at = timezone.now()
        with self.assertRaises(ValidationError):
            ExamCourseEquivalencyMembership.objects.bulk_update(
                [membership],
                ["active_marker", "removed_by", "removed_at"],
            )
        membership.refresh_from_db()
        self.assertEqual(membership.active_marker, 1)

    def test_ordinary_non_lifecycle_queryset_update_remains_allowed(self):
        cycle, primary, secondary, _configurations = self._pair()
        group = self._group(cycle=cycle, primary=primary, secondary=secondary)
        updated = ExamCourseEquivalencyGroup.objects.filter(pk=group.pk).update(
            name="Renamed Shared Examination"
        )
        self.assertEqual(updated, 1)
        group.refresh_from_db()
        self.assertEqual(group.name, "Renamed Shared Examination")

    def test_group_queryset_delete_is_blocked_by_membership_protect(self):
        cycle, primary, secondary, _configurations = self._pair()
        group = self._group(cycle=cycle, primary=primary, secondary=secondary)
        with self.assertRaises(ProtectedError):
            ExamCourseEquivalencyGroup.objects.filter(pk=group.pk).delete()
        self.assertTrue(ExamCourseEquivalencyGroup.objects.filter(pk=group.pk).exists())

    def test_authorized_retirement_preserves_contribution_and_question_ownership(self):
        fixture = self._ready_group()
        contribution_ownership = list(
            FacultyContribution.objects.order_by("id").values_list(
                "id", "cycle_course_id"
            )
        )
        question_ownership = list(
            Question.objects.order_by("id").values_list(
                "id", "contribution_id", "contribution__cycle_course_id"
            )
        )
        ExamCourseEquivalencyService.retire_group(
            group_id=fixture["group"].id,
            actor=self.admin,
            reason="Retire without changing contribution or question ownership.",
        )
        self.assertEqual(
            list(
                FacultyContribution.objects.order_by("id").values_list(
                    "id", "cycle_course_id"
                )
            ),
            contribution_ownership,
        )
        self.assertEqual(
            list(
                Question.objects.order_by("id").values_list(
                    "id", "contribution_id", "contribution__cycle_course_id"
                )
            ),
            question_ownership,
        )

    def test_members_can_regroup_after_valid_retirement(self):
        cycle, primary, secondary, _configurations = self._pair()
        first = self._group(cycle=cycle, primary=primary, secondary=secondary)
        ExamCourseEquivalencyService.retire_group(
            group_id=first.id,
            actor=self.admin,
            reason="Retire this grouping before examination generation.",
        )
        second = ExamCourseEquivalencyService.create_group(
            cycle_id=cycle.id,
            name="Replacement Shared Examination",
            primary_cycle_course_id=secondary.id,
            member_ids=(primary.id, secondary.id),
            actor=self.admin,
        )
        self.assertEqual(resolve_examination_unit(primary).group, second)

    def test_retirement_rejects_generation_questionnaire_and_answer_key_history(self):
        blockers = ("generation", "questionnaire", "answer_key")
        for index, blocker in enumerate(blockers):
            with self.subTest(blocker=blocker):
                cycle, primary, secondary, _configurations = self._pair(
                    scope_suffix=f"RET-{index}"
                )
                group = self._group(
                    cycle=cycle,
                    primary=primary,
                    secondary=secondary,
                    name=f"Retirement Blocker {index}",
                )
                revision = self._revision(primary, token=str(index + 1) * 64)
                now = timezone.now()
                if blocker == "questionnaire":
                    QuestionnairePrintRelease.objects.create(
                        cycle_course=primary,
                        generation_revision=revision,
                        print_from=now,
                        print_until=now + timezone.timedelta(hours=1),
                        released_by=self.admin,
                    )
                elif blocker == "answer_key":
                    AnswerKeyRelease.objects.create(
                        cycle_course=primary,
                        generation_revision=revision,
                        available_from=now,
                        available_until=now + timezone.timedelta(hours=1),
                        released_by=self.admin,
                        attestation_version="equivalency-retirement-v1",
                    )
                with self.assertRaises(ValidationError):
                    ExamCourseEquivalencyService.retire_group(
                        group_id=group.id,
                        actor=self.admin,
                        reason="Retirement must be blocked by protected history.",
                    )
                group.refresh_from_db()
                self.assertTrue(group.is_active)

    def test_retirement_rejects_automatic_processing_and_immutable_cycle(self):
        cycle, primary, secondary, configurations = self._pair()
        group = self._group(cycle=cycle, primary=primary, secondary=secondary)
        configurations[0].automatic_processing_status = (
            CourseExamConfiguration.AutomaticProcessingStatus.BLOCKED
        )
        configurations[0].automatic_processed_at = timezone.now()
        configurations[0].save(
            update_fields=[
                "automatic_processing_status",
                "automatic_processed_at",
                "updated_at",
            ]
        )
        with self.assertRaises(ValidationError):
            ExamCourseEquivalencyService.retire_group(
                group_id=group.id,
                actor=self.admin,
                reason="Processing history must protect this group.",
            )
        configurations[0].automatic_processing_status = ""
        configurations[0].automatic_processed_at = None
        configurations[0].save(
            update_fields=[
                "automatic_processing_status",
                "automatic_processed_at",
                "updated_at",
            ]
        )
        cycle.status = ExaminationCycle.Status.CLOSED
        cycle.save(update_fields=["status", "updated_at"])
        with self.assertRaises(ValidationError):
            ExamCourseEquivalencyService.retire_group(
                group_id=group.id,
                actor=self.admin,
                reason="Closed-cycle membership must remain immutable.",
            )

    def test_unauthorized_retirement_is_rejected(self):
        cycle, primary, secondary, _configurations = self._pair()
        self._add_other_campus_scope(cycle_course=secondary)
        group = self._group(cycle=cycle, primary=primary, secondary=secondary)
        with self.assertRaises(PermissionDenied):
            ExamCourseEquivalencyService.retire_group(
                group_id=group.id,
                actor=self._primary_only_manager("eq-retire-partial"),
                reason="This actor does not control the complete campus union.",
            )
        group.refresh_from_db()
        self.assertTrue(group.is_active)

    def test_retirement_transaction_rolls_back_group_and_members_on_audit_failure(self):
        cycle, primary, secondary, _configurations = self._pair()
        group = self._group(cycle=cycle, primary=primary, secondary=secondary)
        with patch(
            "apps.departmental_exams.exam_units.AuditService.log_event",
            side_effect=RuntimeError("audit unavailable"),
        ):
            with self.assertRaises(RuntimeError):
                ExamCourseEquivalencyService.retire_group(
                    group_id=group.id,
                    actor=self.admin,
                    reason="Rollback must preserve every active membership row.",
                )
        group.refresh_from_db()
        self.assertTrue(group.is_active)
        self.assertEqual(
            ExamCourseEquivalencyMembership.objects.filter(
                group=group, active_marker=1
            ).count(),
            2,
        )

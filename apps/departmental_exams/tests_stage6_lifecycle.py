from dataclasses import replace
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import CourseOffering, FacultyAssignment, Section
from apps.auditlog.models import AuditLog
from apps.tenants.models import Campus, Department, Program

from .blueprint_services import (
    BlockedContributionResolutionService,
    ContributorRosterReadinessService,
    Stage6Conflict,
    contribution_source_evidence,
    resolution_matches_episode,
)
from .contribution_services import ContributionRosterService, QuestionMutationService
from .models import (
    BlockedContributionResolution,
    CourseExamConfiguration,
    CycleCourseOffering,
    FacultyContribution,
    Question,
)
from .services import CourseExamConfigurationConflict, CourseExamConfigurationService
from .stage4_test_support import Stage4TestCase


class Stage6FixtureMixin:
    def make_stage6_open_course(self, *, campus_codes=("CUBAO",)):
        self.campus.code = "CUBAO"
        self.campus.name = "Cubao"
        self.campus.save(update_fields=["code", "name", "updated_at"])
        cycle = self.make_cycle(
            status="OPEN",
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=self.future_deadline(),
            instructions="Submit authoritative MCQs.",
        )
        parent = self.make_course(cycle=cycle, code="S6")
        configuration = self.make_configuration(
            parent,
            workflow=CourseExamConfiguration.WorkflowStatus.OPEN,
            opened_at=timezone.now(),
            final_count=50,
            quota=50,
        )
        campuses = {"CUBAO": self.campus}
        offerings = {
            "CUBAO": parent.offering_snapshots.select_related("offering").get().offering
        }
        for code in campus_codes:
            if code == "CUBAO":
                continue
            campus = Campus.objects.create(tenant=self.tenant, code=code, name=code.title())
            department = Department.objects.create(
                tenant=self.tenant,
                campus=campus,
                code=f"{code}-DEPT",
                name=f"{code.title()} Department",
            )
            program = Program.objects.create(
                tenant=self.tenant,
                campus=campus,
                department=department,
                code=f"{code}-P",
                name=f"{code.title()} Program",
            )
            section = Section.objects.create(
                tenant=self.tenant,
                campus=campus,
                department=department,
                program=program,
                code=f"{code}-S",
                name=f"{code.title()} Section",
            )
            offering = CourseOffering.objects.create(
                tenant=self.tenant,
                campus=campus,
                department=department,
                program=program,
                academic_year=cycle.academic_year,
                term=cycle.term,
                course=parent.course,
                section=section,
            )
            CycleCourseOffering.objects.create(
                cycle_course=parent, offering=offering, campus=campus
            )
            campuses[code] = campus
            offerings[code] = offering
        return parent, configuration, campuses, offerings

    def add_faculty_source(self, *, parent, campus, offering, suffix):
        faculty = self.make_user(
            f"s6-faculty-{suffix}",
            self.department,
            ("faculty_portal.access",),
            campus=campus,
        )
        assignment = FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=campus,
            offering=offering,
            faculty_user=faculty,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
            accepted_by=self.admin,
            is_active=True,
        )
        return faculty, assignment

    @staticmethod
    def add_questions(contribution, *, count=50):
        difficulties = ["EASY"] * 15 + ["MODERATE"] * 25 + ["DIFFICULT"] * 10
        if count != 50:
            difficulties = ["EASY"] * count
        Question.objects.bulk_create(
            [
                Question(
                    contribution=contribution,
                    question_text=f"Question {contribution.id}-{position}",
                    choice_a=f"A-{position}",
                    choice_b=f"B-{position}",
                    choice_c=f"C-{position}",
                    choice_d=f"D-{position}",
                    correct_answer="A",
                    difficulty=difficulty,
                    position=position,
                    revision=1,
                )
                for position, difficulty in enumerate(difficulties, start=1)
            ]
        )

    def submitted_three_campus_course(self):
        parent, configuration, campuses, offerings = self.make_stage6_open_course(
            campus_codes=("CUBAO", "FAIRVIEW", "TAYTAY")
        )
        assignments = {}
        for code in ("CUBAO", "FAIRVIEW", "TAYTAY"):
            _faculty, assignment = self.add_faculty_source(
                parent=parent,
                campus=campuses[code],
                offering=offerings[code],
                suffix=code.lower(),
            )
            assignments[code] = assignment
        ContributionRosterService.initialize(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            actor=self.configurer,
        )
        contributions = {}
        for contribution in FacultyContribution.objects.filter(cycle_course=parent):
            self.add_questions(contribution)
            contribution.status = FacultyContribution.Status.SUBMITTED
            contribution.submitted_at = timezone.now()
            contribution.save(update_fields=["status", "submitted_at", "updated_at"])
            contributions[contribution.source_campus.code] = contribution
        configuration.refresh_from_db()
        return parent, configuration, contributions, assignments


class Stage6ContributionCloseTests(Stage6FixtureMixin, Stage4TestCase):
    def test_real_submitted_question_activity_no_longer_blocks_close(self):
        parent, configuration, _contributions, _assignments = self.submitted_three_campus_course()
        before_ids = list(Question.objects.filter(contribution__cycle_course=parent).values_list("id", flat=True))
        configuration, changed = CourseExamConfigurationService.close_contribution(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            user=self.configurer,
            expected_revision=configuration.revision,
            expected_roster_revision=configuration.contributor_roster_revision,
            reason="All authoritative contribution obligations are complete.",
        )
        self.assertTrue(changed)
        self.assertEqual(configuration.workflow_status, CourseExamConfiguration.WorkflowStatus.CLOSED)
        self.assertEqual(
            before_ids,
            list(Question.objects.filter(contribution__cycle_course=parent).values_list("id", flat=True)),
        )
        audit = AuditLog.objects.get(action="DE_EXAM_COURSE_CONTRIBUTION_CLOSED")
        self.assertNotIn("Question", str(audit.metadata_json))

    def test_active_incomplete_contributor_blocks_close(self):
        parent, configuration, campuses, offerings = self.make_stage6_open_course()
        self.add_faculty_source(
            parent=parent,
            campus=campuses["CUBAO"],
            offering=offerings["CUBAO"],
            suffix="incomplete",
        )
        ContributionRosterService.initialize(
            cycle_course_id=parent.id, tenant_id=self.tenant.id, actor=self.configurer
        )
        configuration.refresh_from_db()
        with self.assertRaisesRegex(ValidationError, "required Active contributor"):
            CourseExamConfigurationService.close_contribution(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                user=self.configurer,
                expected_revision=configuration.revision,
                expected_roster_revision=configuration.contributor_roster_revision,
                reason="Close attempt with incomplete required contributor.",
            )

    def test_stale_roster_blocks_close_and_requires_explicit_sync(self):
        parent, configuration, campuses, offerings = self.make_stage6_open_course()
        ContributionRosterService.initialize(
            cycle_course_id=parent.id, tenant_id=self.tenant.id, actor=self.configurer
        )
        self.add_faculty_source(
            parent=parent,
            campus=campuses["CUBAO"],
            offering=offerings["CUBAO"],
            suffix="new-after-roster",
        )
        configuration.refresh_from_db()
        with self.assertRaisesRegex(CourseExamConfigurationConflict, "Synchronize"):
            CourseExamConfigurationService.close_contribution(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                user=self.configurer,
                expected_revision=configuration.revision,
                expected_roster_revision=configuration.contributor_roster_revision,
                reason="Close attempt against a stale contributor roster.",
            )

    def test_stale_roster_revision_at_close_conflicts_without_closing(self):
        parent, configuration, _contributions, _assignments = self.submitted_three_campus_course()
        with self.assertRaisesRegex(CourseExamConfigurationConflict, "page was loaded"):
            CourseExamConfigurationService.close_contribution(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                user=self.configurer,
                expected_revision=configuration.revision,
                expected_roster_revision=configuration.contributor_roster_revision - 1,
                reason="Reject a stale close-time roster revision contract.",
            )
        configuration.refresh_from_db()
        self.assertEqual(
            configuration.workflow_status,
            CourseExamConfiguration.WorkflowStatus.OPEN,
        )
        self.assertFalse(
            AuditLog.objects.filter(action="DE_EXAM_COURSE_CONTRIBUTION_CLOSED").exists()
        )

    def test_close_rechecks_roster_fingerprint_and_rolls_back_on_drift(self):
        parent, configuration, _contributions, _assignments = self.submitted_three_campus_course()
        first = ContributorRosterReadinessService.evaluate(
            cycle_course=parent, configuration=configuration
        )
        drifted = replace(first, live_sha256="0" * 64)
        with patch(
            "apps.departmental_exams.blueprint_services.ContributorRosterReadinessService.evaluate",
            side_effect=(first, drifted),
        ):
            with self.assertRaisesRegex(CourseExamConfigurationConflict, "while contribution close"):
                CourseExamConfigurationService.close_contribution(
                    cycle_course_id=parent.id,
                    tenant_id=self.tenant.id,
                    user=self.configurer,
                    expected_revision=configuration.revision,
                    expected_roster_revision=configuration.contributor_roster_revision,
                    reason="Reject live eligibility drift during close validation.",
                )
        configuration.refresh_from_db()
        self.assertEqual(
            configuration.workflow_status,
            CourseExamConfiguration.WorkflowStatus.OPEN,
        )
        self.assertFalse(
            AuditLog.objects.filter(action="DE_EXAM_COURSE_CONTRIBUTION_CLOSED").exists()
        )

    def test_blocked_draft_requires_explicit_resolution_then_close_succeeds(self):
        parent, configuration, campuses, offerings = self.make_stage6_open_course()
        _faculty, assignment = self.add_faculty_source(
            parent=parent,
            campus=campuses["CUBAO"],
            offering=offerings["CUBAO"],
            suffix="blocked",
        )
        ContributionRosterService.initialize(
            cycle_course_id=parent.id, tenant_id=self.tenant.id, actor=self.configurer
        )
        assignment.is_active = False
        assignment.save(update_fields=["is_active", "updated_at"])
        ContributionRosterService.synchronize(
            cycle_course_id=parent.id, tenant_id=self.tenant.id, actor=self.configurer
        )
        configuration.refresh_from_db()
        contribution = FacultyContribution.objects.get(cycle_course=parent)
        with self.assertRaisesRegex(ValidationError, "explicit resolution"):
            CourseExamConfigurationService.close_contribution(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                user=self.configurer,
                expected_revision=configuration.revision,
                expected_roster_revision=configuration.contributor_roster_revision,
                reason="Blocked Draft is not yet explicitly resolved.",
            )
        BlockedContributionResolutionService.resolve(
            contribution_id=contribution.id,
            tenant_id=self.tenant.id,
            actor=self.configurer,
            expected_contribution_revision=contribution.revision,
            expected_roster_revision=configuration.contributor_roster_revision,
            reason="Faculty assignment ended before final submission.",
        )
        configuration, changed = CourseExamConfigurationService.close_contribution(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            user=self.configurer,
            expected_revision=configuration.revision,
            expected_roster_revision=configuration.contributor_roster_revision,
            reason="All current Blocked Draft obligations are resolved.",
        )
        self.assertTrue(changed)

    def test_audit_failure_rolls_back_close(self):
        parent, configuration, _contributions, _assignments = self.submitted_three_campus_course()
        with patch(
            "apps.departmental_exams.services.AuditService.log_event",
            side_effect=RuntimeError("audit failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "audit failed"):
                CourseExamConfigurationService.close_contribution(
                    cycle_course_id=parent.id,
                    tenant_id=self.tenant.id,
                    user=self.configurer,
                    expected_revision=configuration.revision,
                    expected_roster_revision=configuration.contributor_roster_revision,
                    reason="Rollback the close if its audit event cannot persist.",
                )
        configuration.refresh_from_db()
        self.assertEqual(configuration.workflow_status, CourseExamConfiguration.WorkflowStatus.OPEN)
        self.assertIsNone(configuration.closed_at)

    def test_close_route_is_post_only_and_closed_state_denies_stage5_mutation(self):
        parent, configuration, contributions, _assignments = self.submitted_three_campus_course()
        client = Client()
        client.force_login(self.configurer)
        url = reverse("departmental_exams:course_contribution_close", args=[parent.id])
        self.assertEqual(client.get(url).status_code, 405)
        response = client.post(
            url,
            {
                "expected_revision": configuration.revision,
                "expected_roster_revision": configuration.contributor_roster_revision,
                "reason": "Authorized POST closes completed faculty contribution.",
            },
        )
        self.assertEqual(response.status_code, 302)
        contribution = contributions["CUBAO"]
        with self.assertRaises(PermissionDenied):
            QuestionMutationService.create(
                contribution_id=contribution.id,
                user=contribution.faculty_user,
                tenant_id=self.tenant.id,
                campus_id=contribution.source_campus_id,
                expected_contribution_revision=contribution.revision,
                payload={
                    "question_text": "Disallowed after close",
                    "choice_a": "A",
                    "choice_b": "B",
                    "choice_c": "C",
                    "choice_d": "D",
                    "correct_answer": "A",
                    "difficulty": "EASY",
                },
            )


class Stage6BlockedResolutionTests(Stage6FixtureMixin, Stage4TestCase):
    def _blocked_course(self, *, campus_codes=("CUBAO",)):
        parent, configuration, campuses, offerings = self.make_stage6_open_course(
            campus_codes=campus_codes
        )
        _faculty, assignment = self.add_faculty_source(
            parent=parent,
            campus=campuses["CUBAO"],
            offering=offerings["CUBAO"],
            suffix=self._testMethodName[-20:],
        )
        ContributionRosterService.initialize(
            cycle_course_id=parent.id, tenant_id=self.tenant.id, actor=self.configurer
        )
        assignment.is_active = False
        assignment.save(update_fields=["is_active", "updated_at"])
        ContributionRosterService.synchronize(
            cycle_course_id=parent.id, tenant_id=self.tenant.id, actor=self.configurer
        )
        configuration.refresh_from_db()
        contribution = FacultyContribution.objects.get(cycle_course=parent)
        return parent, configuration, contribution, assignment, campuses, offerings

    def _blocked(self):
        return self._blocked_course()[:4]

    def _add_inactive_source(self, *, contribution, campus, offering):
        return FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=campus,
            offering=offering,
            faculty_user=contribution.faculty_user,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
            accepted_by=self.admin,
            is_active=False,
        )

    def test_resolution_is_immutable_content_safe_and_does_not_change_questions_or_status(self):
        parent, configuration, contribution, _assignment = self._blocked()
        self.add_questions(contribution, count=2)
        resolution = BlockedContributionResolutionService.resolve(
            contribution_id=contribution.id,
            tenant_id=self.tenant.id,
            actor=self.configurer,
            expected_contribution_revision=contribution.revision,
            expected_roster_revision=configuration.contributor_roster_revision,
            reason="Documented staffing loss before contribution completion.",
        )
        contribution.refresh_from_db()
        self.assertEqual(contribution.status, FacultyContribution.Status.DRAFT)
        self.assertEqual(contribution.questions.count(), 2)
        resolution.reason = "Attempted rewrite of immutable evidence."
        with self.assertRaisesRegex(ValidationError, "immutable"):
            resolution.save()
        with self.assertRaisesRegex(ValidationError, "immutable"):
            resolution.delete()
        audit = AuditLog.objects.get(action="DE_EXAM_BLOCKED_CONTRIBUTION_RESOLVED")
        self.assertNotIn("Documented staffing loss", str(audit.metadata_json))

    def test_repeated_block_episodes_get_distinct_events_and_active_again_requires_submission(self):
        parent, configuration, contribution, assignment = self._blocked()
        first_blocked_at = contribution.roster_blocked_at
        BlockedContributionResolutionService.resolve(
            contribution_id=contribution.id,
            tenant_id=self.tenant.id,
            actor=self.configurer,
            expected_contribution_revision=contribution.revision,
            expected_roster_revision=configuration.contributor_roster_revision,
            reason="First blocked staffing episode was reviewed explicitly.",
        )
        first_revision = contribution.revision
        assignment.is_active = True
        assignment.save(update_fields=["is_active", "updated_at"])
        ContributionRosterService.synchronize(
            cycle_course_id=parent.id, tenant_id=self.tenant.id, actor=self.configurer
        )
        configuration.refresh_from_db()
        readiness = ContributorRosterReadinessService.evaluate(
            cycle_course=parent, configuration=configuration
        )
        self.assertEqual(readiness.incomplete_active_count, 1)
        assignment.is_active = False
        assignment.save(update_fields=["is_active", "updated_at"])
        ContributionRosterService.synchronize(
            cycle_course_id=parent.id, tenant_id=self.tenant.id, actor=self.configurer
        )
        configuration.refresh_from_db()
        contribution.refresh_from_db()
        self.assertNotEqual(contribution.roster_blocked_at, first_blocked_at)
        self.assertGreater(contribution.revision, first_revision)
        self.assertEqual(
            ContributorRosterReadinessService.evaluate(
                cycle_course=parent, configuration=configuration
            ).unresolved_blocked_count,
            1,
        )
        BlockedContributionResolutionService.resolve(
            contribution_id=contribution.id,
            tenant_id=self.tenant.id,
            actor=self.configurer,
            expected_contribution_revision=contribution.revision,
            expected_roster_revision=configuration.contributor_roster_revision,
            reason="Second blocked staffing episode was reviewed explicitly.",
        )
        self.assertEqual(
            BlockedContributionResolution.objects.filter(contribution=contribution).count(),
            2,
        )

    def test_unrelated_roster_revision_does_not_invalidate_resolved_episode(self):
        parent, configuration, campuses, offerings = self.make_stage6_open_course()
        _faculty_a, assignment_a = self.add_faculty_source(
            parent=parent,
            campus=campuses["CUBAO"],
            offering=offerings["CUBAO"],
            suffix="episode-a",
        )
        _faculty_b, assignment_b = self.add_faculty_source(
            parent=parent,
            campus=campuses["CUBAO"],
            offering=offerings["CUBAO"],
            suffix="episode-b",
        )
        ContributionRosterService.initialize(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            actor=self.configurer,
        )
        assignment_a.is_active = False
        assignment_a.save(update_fields=["is_active", "updated_at"])
        ContributionRosterService.synchronize(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            actor=self.configurer,
        )
        configuration.refresh_from_db()
        contribution_a = FacultyContribution.objects.get(
            cycle_course=parent, faculty_user=assignment_a.faculty_user
        )
        BlockedContributionResolutionService.resolve(
            contribution_id=contribution_a.id,
            tenant_id=self.tenant.id,
            actor=self.configurer,
            expected_contribution_revision=contribution_a.revision,
            expected_roster_revision=configuration.contributor_roster_revision,
            reason="Resolve the first contributor's blocked staffing episode.",
        )
        resolved_roster_revision = configuration.contributor_roster_revision
        resolved_contribution_revision = contribution_a.revision

        assignment_b.is_active = False
        assignment_b.save(update_fields=["is_active", "updated_at"])
        ContributionRosterService.synchronize(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            actor=self.configurer,
        )
        configuration.refresh_from_db()
        self.assertGreater(
            configuration.contributor_roster_revision, resolved_roster_revision
        )
        contribution_a.refresh_from_db()
        self.assertEqual(contribution_a.revision, resolved_contribution_revision)
        readiness = ContributorRosterReadinessService.evaluate(
            cycle_course=parent, configuration=configuration
        )
        self.assertEqual(readiness.unresolved_blocked_count, 1)
        client = Client()
        client.force_login(self.configurer)
        monitoring = client.get(
            reverse("departmental_exams:contributor_monitoring")
        )
        self.assertEqual(monitoring.status_code, 200)
        rendered_course = next(
            course for course in monitoring.context["courses"] if course.id == parent.id
        )
        rendered_a = next(
            item
            for item in rendered_course.faculty_contributions.all()
            if item.id == contribution_a.id
        )
        self.assertTrue(rendered_a.blocked_resolution_valid)

        contribution_b = FacultyContribution.objects.get(
            cycle_course=parent, faculty_user=assignment_b.faculty_user
        )
        BlockedContributionResolutionService.resolve(
            contribution_id=contribution_b.id,
            tenant_id=self.tenant.id,
            actor=self.configurer,
            expected_contribution_revision=contribution_b.revision,
            expected_roster_revision=configuration.contributor_roster_revision,
            reason="Resolve the unrelated contributor's blocked staffing episode.",
        )
        closed, changed = CourseExamConfigurationService.close_contribution(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            user=self.configurer,
            expected_revision=configuration.revision,
            expected_roster_revision=configuration.contributor_roster_revision,
            reason="Both independent blocked episodes are explicitly resolved.",
        )
        self.assertTrue(changed)
        self.assertEqual(closed.workflow_status, CourseExamConfiguration.WorkflowStatus.CLOSED)

    def test_same_blocked_material_evidence_change_allows_reresolution_and_close(self):
        (
            parent,
            configuration,
            contribution,
            _assignment,
            campuses,
            offerings,
        ) = self._blocked_course(campus_codes=("CUBAO", "FAIRVIEW"))
        first = BlockedContributionResolutionService.resolve(
            contribution_id=contribution.id,
            tenant_id=self.tenant.id,
            actor=self.configurer,
            expected_contribution_revision=contribution.revision,
            expected_roster_revision=configuration.contributor_roster_revision,
            reason="Resolve the initial blocked contribution evidence state.",
        )
        self.assertEqual(
            ContributorRosterReadinessService.evaluate(
                cycle_course=parent, configuration=configuration
            ).unresolved_blocked_count,
            0,
        )
        first_revision = contribution.revision
        first_blocked_at = contribution.roster_blocked_at

        self._add_inactive_source(
            contribution=contribution,
            campus=campuses["FAIRVIEW"],
            offering=offerings["FAIRVIEW"],
        )
        ContributionRosterService.synchronize(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            actor=self.configurer,
        )
        configuration.refresh_from_db()
        contribution.refresh_from_db()
        self.assertEqual(contribution.roster_status, FacultyContribution.RosterStatus.BLOCKED)
        self.assertEqual(contribution.roster_blocked_at, first_blocked_at)
        self.assertEqual(contribution.revision, first_revision + 1)
        self.assertEqual(
            ContributorRosterReadinessService.evaluate(
                cycle_course=parent, configuration=configuration
            ).unresolved_blocked_count,
            1,
        )

        second = BlockedContributionResolutionService.resolve(
            contribution_id=contribution.id,
            tenant_id=self.tenant.id,
            actor=self.configurer,
            expected_contribution_revision=contribution.revision,
            expected_roster_revision=configuration.contributor_roster_revision,
            reason="Resolve the revised blocked contribution evidence state.",
        )
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(
            ContributorRosterReadinessService.evaluate(
                cycle_course=parent, configuration=configuration
            ).unresolved_blocked_count,
            0,
        )
        closed, changed = CourseExamConfigurationService.close_contribution(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            user=self.configurer,
            expected_revision=configuration.revision,
            expected_roster_revision=configuration.contributor_roster_revision,
            reason="The revised blocked evidence state is explicitly resolved.",
        )
        self.assertTrue(changed)
        self.assertEqual(closed.workflow_status, CourseExamConfiguration.WorkflowStatus.CLOSED)

    def test_multiple_same_blocked_evidence_revisions_retain_history_with_one_current(self):
        (
            parent,
            configuration,
            contribution,
            _assignment,
            campuses,
            offerings,
        ) = self._blocked_course(campus_codes=("CUBAO", "FAIRVIEW", "TAYTAY"))
        resolutions = []
        for label, campus_code in (("A", None), ("B", "FAIRVIEW"), ("C", "TAYTAY")):
            if campus_code is not None:
                self._add_inactive_source(
                    contribution=contribution,
                    campus=campuses[campus_code],
                    offering=offerings[campus_code],
                )
                ContributionRosterService.synchronize(
                    cycle_course_id=parent.id,
                    tenant_id=self.tenant.id,
                    actor=self.configurer,
                )
                configuration.refresh_from_db()
                contribution.refresh_from_db()
                self.assertEqual(
                    ContributorRosterReadinessService.evaluate(
                        cycle_course=parent, configuration=configuration
                    ).unresolved_blocked_count,
                    1,
                )
            resolutions.append(
                BlockedContributionResolutionService.resolve(
                    contribution_id=contribution.id,
                    tenant_id=self.tenant.id,
                    actor=self.configurer,
                    expected_contribution_revision=contribution.revision,
                    expected_roster_revision=configuration.contributor_roster_revision,
                    reason=f"Resolve blocked contribution evidence state {label} explicitly.",
                )
            )

        contribution.refresh_from_db()
        source_hash = contribution_source_evidence(contribution)
        self.assertEqual(
            BlockedContributionResolution.objects.filter(contribution=contribution).count(),
            3,
        )
        self.assertEqual(
            sum(
                resolution_matches_episode(
                    resolution=resolution,
                    contribution=contribution,
                    source_hash=source_hash,
                )
                for resolution in resolutions
            ),
            1,
        )
        self.assertTrue(
            resolution_matches_episode(
                resolution=resolutions[-1],
                contribution=contribution,
                source_hash=source_hash,
            )
        )

    def test_equivalent_old_evidence_hash_cannot_reactivate_superseded_resolution(self):
        (
            parent,
            configuration,
            contribution,
            _assignment,
            campuses,
            offerings,
        ) = self._blocked_course(campus_codes=("CUBAO", "FAIRVIEW", "TAYTAY"))
        first = BlockedContributionResolutionService.resolve(
            contribution_id=contribution.id,
            tenant_id=self.tenant.id,
            actor=self.configurer,
            expected_contribution_revision=contribution.revision,
            expected_roster_revision=configuration.contributor_roster_revision,
            reason="Resolve evidence state A before later evidence changes.",
        )
        for campus_code in ("FAIRVIEW", "TAYTAY"):
            self._add_inactive_source(
                contribution=contribution,
                campus=campuses[campus_code],
                offering=offerings[campus_code],
            )
            ContributionRosterService.synchronize(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                actor=self.configurer,
            )
            configuration.refresh_from_db()
            contribution.refresh_from_db()
            if campus_code == "FAIRVIEW":
                BlockedContributionResolutionService.resolve(
                    contribution_id=contribution.id,
                    tenant_id=self.tenant.id,
                    actor=self.configurer,
                    expected_contribution_revision=contribution.revision,
                    expected_roster_revision=configuration.contributor_roster_revision,
                    reason="Resolve evidence state B before an equivalent hash returns.",
                )

        with patch(
            "apps.departmental_exams.blueprint_services.contribution_source_evidence",
            return_value=first.source_evidence_sha256,
        ):
            self.assertEqual(
                ContributorRosterReadinessService.evaluate(
                    cycle_course=parent, configuration=configuration
                ).unresolved_blocked_count,
                1,
            )
            current = BlockedContributionResolutionService.resolve(
                contribution_id=contribution.id,
                tenant_id=self.tenant.id,
                actor=self.configurer,
                expected_contribution_revision=contribution.revision,
                expected_roster_revision=configuration.contributor_roster_revision,
                reason="Resolve the current monotonic state despite equivalent evidence content.",
            )
            self.assertNotEqual(current.contribution_revision_snapshot, first.contribution_revision_snapshot)
            self.assertEqual(current.source_evidence_sha256, first.source_evidence_sha256)
            self.assertEqual(
                ContributorRosterReadinessService.evaluate(
                    cycle_course=parent, configuration=configuration
                ).unresolved_blocked_count,
                0,
            )

    def test_duplicate_exact_current_resolution_fails_as_stage6_conflict(self):
        _parent, configuration, contribution, _assignment = self._blocked()
        common = {
            "contribution_id": contribution.id,
            "tenant_id": self.tenant.id,
            "actor": self.configurer,
            "expected_contribution_revision": contribution.revision,
            "expected_roster_revision": configuration.contributor_roster_revision,
        }
        BlockedContributionResolutionService.resolve(
            **common,
            reason="Resolve this exact blocked evidence state once.",
        )
        with self.assertRaisesRegex(Stage6Conflict, "exact Blocked contribution evidence state"):
            BlockedContributionResolutionService.resolve(
                **common,
                reason="A duplicate reviewer decision must fail deterministically.",
            )
        self.assertEqual(
            BlockedContributionResolution.objects.filter(contribution=contribution).count(),
            1,
        )

    def test_tampered_episode_marker_or_source_evidence_fails_closed(self):
        parent, configuration, contribution, _assignment = self._blocked()
        resolution = BlockedContributionResolutionService.resolve(
            contribution_id=contribution.id,
            tenant_id=self.tenant.id,
            actor=self.configurer,
            expected_contribution_revision=contribution.revision,
            expected_roster_revision=configuration.contributor_roster_revision,
            reason="Record immutable evidence for the exact blocked episode.",
        )
        original_blocked_at = resolution.blocked_at_snapshot
        BlockedContributionResolution.objects.filter(pk=resolution.pk).update(
            blocked_at_snapshot=timezone.now()
        )
        self.assertEqual(
            ContributorRosterReadinessService.evaluate(
                cycle_course=parent, configuration=configuration
            ).unresolved_blocked_count,
            1,
        )
        BlockedContributionResolution.objects.filter(pk=resolution.pk).update(
            blocked_at_snapshot=original_blocked_at,
            source_evidence_sha256="0" * 64,
        )
        self.assertEqual(
            ContributorRosterReadinessService.evaluate(
                cycle_course=parent, configuration=configuration
            ).unresolved_blocked_count,
            1,
        )

    def test_reason_authorization_scope_and_status_fail_closed(self):
        _parent, configuration, contribution, _assignment = self._blocked()
        common = {
            "contribution_id": contribution.id,
            "tenant_id": self.tenant.id,
            "expected_contribution_revision": contribution.revision,
            "expected_roster_revision": configuration.contributor_roster_revision,
            "reason": "A sufficiently detailed operational resolution reason.",
        }
        with self.assertRaises(PermissionDenied):
            BlockedContributionResolutionService.resolve(actor=self.manager, **common)
        with self.assertRaises(Http404):
            BlockedContributionResolutionService.resolve(
                actor=self.configurer,
                **{**common, "tenant_id": self.other_tenant.id},
            )
        with self.assertRaises(ValidationError):
            BlockedContributionResolutionService.resolve(
                actor=self.configurer,
                **{**common, "reason": "short"},
            )
        contribution.status = FacultyContribution.Status.SUBMITTED
        contribution.submitted_at = timezone.now()
        contribution.save(update_fields=["status", "submitted_at", "updated_at"])
        with self.assertRaisesRegex(ValidationError, "Draft"):
            BlockedContributionResolutionService.resolve(actor=self.configurer, **common)

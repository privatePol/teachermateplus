from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.test import override_settings
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.academics.models import CourseOffering, FacultyAssignment, Section
from apps.auditlog.models import AuditLog
from apps.rbac.models import Permission, UserPermission
from apps.core.services.permissions import PermissionService
from apps.core.services.settings import SystemSettingService
from apps.tenants.models import Program

from .contribution_authorization import (
    ContributionConflict,
    ContributionQuotaReached,
    ContributorEligibilityService,
)
from .contribution_services import (
    ContributionRosterService,
    QuestionMutationService,
)
from .models import CycleCourseOffering, FacultyContribution, Question
from .services import CourseExamConfigurationService
from .stage4_test_support import Stage4TestCase


class Stage5FixtureMixin:
    def make_stage5_course(self):
        cycle = self.make_cycle(
            status="OPEN",
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=self.future_deadline(),
            instructions="Write plain-text questions.",
        )
        parent = self.make_course(cycle=cycle, code="S5")
        configuration = self.make_configuration(
            parent,
            workflow="OPEN",
            opened_at=timezone.now(),
            deadline=self.future_deadline(),
        )
        return parent, configuration

    def make_faculty(self, username="stage5-faculty", *, campus=None, department=None):
        Permission.objects.get_or_create(
            code="faculty_portal.access",
            defaults={
                "module": "faculty_portal",
                "action": "access",
                "description": "Faculty Portal",
                "is_active": True,
            },
        )
        return self.make_user(
            username,
            department or self.department,
            ("faculty_portal.access",),
            campus=campus,
        )

    def make_assignment(self, parent, faculty, *, campus=None, offering=None):
        if offering is None:
            offering = parent.offering_snapshots.select_related("offering").order_by("id").first().offering
        campus = campus or offering.campus
        return FacultyAssignment.objects.create(
            tenant=parent.cycle.tenant,
            campus=campus,
            offering=offering,
            faculty_user=faculty,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
            accepted_by=self.admin,
            is_active=True,
        )

    def initialize(self, parent):
        return ContributionRosterService.initialize(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            actor=self.configurer,
        )

    @staticmethod
    def payload(question_text="What is 2 + 2?"):
        return {
            "question_text": question_text,
            "choice_a": "1",
            "choice_b": "2",
            "choice_c": "3",
            "choice_d": "4",
            "correct_answer": "d",
            "difficulty": "easy",
        }


class Stage5EligibilityRosterTests(Stage5FixtureMixin, Stage4TestCase):
    def test_future_open_transition_initializes_roster_atomically(self):
        cycle = self.make_cycle(
            status="OPEN",
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=self.future_deadline(),
        )
        parent = self.make_course(cycle=cycle, code="S5-OPEN")
        configuration = self.make_configuration(parent, workflow="DRAFT")
        faculty = self.make_faculty("future-open-faculty")
        self.make_assignment(parent, faculty)
        CourseExamConfigurationService.open_for_contribution(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            user=self.configurer,
            expected_revision=configuration.revision,
        )
        configuration.refresh_from_db()
        self.assertIsNotNone(configuration.contributor_roster_initialized_at)
        self.assertTrue(FacultyContribution.objects.filter(faculty_user=faculty).exists())

    def test_exact_assigned_permission_initializes_one_grouped_contribution(self):
        parent, configuration = self.make_stage5_course()
        faculty = self.make_faculty()
        first = self.make_assignment(parent, faculty)
        result = self.initialize(parent)
        contribution = FacultyContribution.objects.get()
        self.assertEqual(result["created"], 1)
        self.assertEqual(contribution.faculty_user, faculty)
        self.assertEqual(contribution.source_assignment, first)
        self.assertEqual(contribution.quota_snapshot, 50)
        self.assertEqual(contribution.configuration_revision_snapshot, configuration.revision)
        self.assertEqual(contribution.roster_status, "ACTIVE")
        self.assertEqual(contribution.eligibility_sources.count(), 1)

    def test_duplicate_sections_do_not_duplicate_grouped_contribution(self):
        parent, _configuration = self.make_stage5_course()
        faculty = self.make_faculty()
        self.make_assignment(parent, faculty)
        first_snapshot = parent.offering_snapshots.select_related("offering").get()
        offering = first_snapshot.offering
        section = offering.section
        section.pk = None
        section.code = "SECOND"
        section.name = "Second"
        section.save()
        offering.pk = None
        offering.section = section
        offering.save()
        from .models import CycleCourseOffering

        CycleCourseOffering.objects.create(
            cycle_course=parent, offering=offering, campus=offering.campus
        )
        self.make_assignment(parent, faculty, offering=offering)
        self.initialize(parent)
        contribution = FacultyContribution.objects.get()
        self.assertEqual(FacultyContribution.objects.count(), 1)
        self.assertEqual(contribution.eligibility_sources.count(), 2)

    def test_superuser_without_explicit_assignment_permission_is_not_contributor(self):
        parent, _configuration = self.make_stage5_course()
        self.make_assignment(parent, self.admin)
        self.initialize(parent)
        self.assertFalse(FacultyContribution.objects.exists())
        inventory = ContributorEligibilityService.source_inventory(cycle_course=parent)
        self.assertEqual(inventory.eligible_sources, ())

    def test_exact_direct_deny_blocks_only_denied_source(self):
        parent, _configuration = self.make_stage5_course()
        faculty = self.make_faculty()
        allowed = self.make_assignment(parent, faculty)
        UserPermission.objects.create(
            user=faculty,
            permission=Permission.objects.get(code="faculty_portal.access"),
            tenant=self.tenant,
            campus=self.other_campus,
            grant_type=UserPermission.GrantType.DENY,
        )
        inventory = ContributorEligibilityService.source_inventory(cycle_course=parent)
        self.assertEqual([source.id for source in inventory.eligible_sources], [allowed.id])

    def test_null_scoped_rows_are_non_applicable_to_concrete_source(self):
        parent, _configuration = self.make_stage5_course()
        faculty = self.make_user("null-scope-faculty", self.department, ())
        assignment = self.make_assignment(parent, faculty)
        permission = Permission.objects.get_or_create(
            code="faculty_portal.access",
            defaults={"module": "faculty_portal", "action": "access", "description": "Faculty", "is_active": True},
        )[0]
        UserPermission.objects.create(
            user=faculty,
            permission=permission,
            tenant=None,
            campus=None,
            grant_type=UserPermission.GrantType.ALLOW,
        )
        self.assertFalse(
            ContributorEligibilityService.source_inventory(cycle_course=parent).eligible_sources
        )
        self.assertFalse(
            PermissionService.has_assigned_permission(
                faculty,
                "faculty_portal.access",
                tenant_id=assignment.tenant_id,
                campus_id=assignment.campus_id,
                exact_scope=True,
            )
        )
        UserPermission.objects.create(
            user=faculty,
            permission=permission,
            tenant=self.tenant,
            campus=self.campus,
            grant_type=UserPermission.GrantType.ALLOW,
        )
        self.assertEqual(
            len(ContributorEligibilityService.source_inventory(cycle_course=parent).eligible_sources),
            1,
        )
        self.assertTrue(
            ContributorEligibilityService.source_is_eligible(
                assignment=assignment, cycle_course=parent
            )
        )
        UserPermission.objects.filter(tenant__isnull=True, campus__isnull=True).update(
            grant_type=UserPermission.GrantType.DENY
        )
        self.assertEqual(
            len(ContributorEligibilityService.source_inventory(cycle_course=parent).eligible_sources),
            1,
        )

    def test_set_based_permission_result_matches_exact_authoritative_check(self):
        parent, _configuration = self.make_stage5_course()
        faculty = self.make_faculty("parity-faculty")
        assignment = self.make_assignment(parent, faculty)
        inventory = ContributorEligibilityService.source_inventory(cycle_course=parent)
        self.assertEqual(
            assignment in inventory.eligible_sources,
            PermissionService.has_assigned_permission(
                faculty,
                "faculty_portal.access",
                tenant_id=assignment.tenant_id,
                campus_id=assignment.campus_id,
                exact_scope=True,
            ),
        )
        UserPermission.objects.create(
            user=faculty,
            permission=Permission.objects.get(code="faculty_portal.access"),
            tenant=self.tenant,
            campus=self.campus,
            grant_type=UserPermission.GrantType.DENY,
        )
        inventory = ContributorEligibilityService.source_inventory(cycle_course=parent)
        self.assertEqual(
            assignment in inventory.eligible_sources,
            PermissionService.has_assigned_permission(
                faculty,
                "faculty_portal.access",
                tenant_id=assignment.tenant_id,
                campus_id=assignment.campus_id,
                exact_scope=True,
            ),
        )

    def test_structural_predicates_fail_closed(self):
        parent, configuration = self.make_stage5_course()
        faculty = self.make_faculty("predicate-faculty")
        assignment = self.make_assignment(parent, faculty)
        offering = assignment.offering
        cases = [
            (faculty, "is_active", False),
            (self.tenant, "is_active", False),
            (self.campus, "is_active", False),
            (offering, "is_active", False),
            (offering, "status", "CLOSED"),
            (assignment, "is_active", False),
            (assignment, "response_status", "PENDING"),
            (assignment, "accepted_at", None),
            (assignment, "tenant_id", None),
            (assignment, "campus_id", None),
            (parent.cycle, "status", "CLOSED"),
            (parent, "inclusion_status", "EXEMPT"),
            (configuration, "workflow_status", "CLOSED"),
        ]
        for instance, field, invalid_value in cases:
            with self.subTest(model=type(instance).__name__, field=field):
                original = getattr(instance, field)
                setattr(instance, field, invalid_value)
                type(instance).objects.filter(pk=instance.pk).update(**{field: invalid_value})
                parent.refresh_from_db()
                parent = type(parent).objects.select_related("cycle", "cycle__tenant", "configuration").get(pk=parent.pk)
                self.assertFalse(
                    ContributorEligibilityService.source_inventory(cycle_course=parent).eligible_sources
                )
                setattr(instance, field, original)
                type(instance).objects.filter(pk=instance.pk).update(**{field: original})

    def test_last_source_invalidation_blocks_and_restoration_reactivates_draft(self):
        parent, _configuration = self.make_stage5_course()
        faculty = self.make_faculty()
        assignment = self.make_assignment(parent, faculty)
        self.initialize(parent)
        assignment.is_active = False
        assignment.save(update_fields=["is_active", "updated_at"])
        result = ContributionRosterService.synchronize(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            actor=self.configurer,
        )
        contribution = FacultyContribution.objects.get()
        self.assertEqual(result["blocked"], 1)
        self.assertEqual(contribution.roster_status, "BLOCKED")
        self.assertIsNotNone(contribution.roster_blocked_at)
        assignment.is_active = True
        assignment.save(update_fields=["is_active", "updated_at"])
        ContributionRosterService.synchronize(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            actor=self.configurer,
        )
        contribution.refresh_from_db()
        self.assertEqual(contribution.roster_status, "ACTIVE")
        self.assertIsNone(contribution.roster_blocked_at)

    def test_reviewer_cannot_initialize_roster(self):
        parent, _configuration = self.make_stage5_course()
        with self.assertRaises(PermissionDenied):
            ContributionRosterService.initialize(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                actor=self.reviewer,
            )

    def test_initialization_and_synchronization_are_idempotent(self):
        parent, _configuration = self.make_stage5_course()
        faculty = self.make_faculty()
        self.make_assignment(parent, faculty)
        self.assertTrue(self.initialize(parent)["changed"])
        self.assertFalse(self.initialize(parent)["changed"])
        self.assertFalse(
            ContributionRosterService.synchronize(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                actor=self.configurer,
            )["changed"]
        )
        self.assertEqual(FacultyContribution.objects.count(), 1)

    def test_set_based_navigation_eligibility_has_constant_query_growth(self):
        parent, _configuration = self.make_stage5_course()
        faculty = self.make_faculty("query-faculty")
        self.make_assignment(parent, faculty)
        with CaptureQueriesContext(connection) as small:
            self.assertTrue(
                ContributorEligibilityService.has_any_eligible_source(
                    user=faculty, tenant_id=self.tenant.id
                )
            )
        original = parent.offering_snapshots.select_related("offering", "offering__section").first().offering
        from .models import CycleCourseOffering

        for index in range(5):
            section = original.section
            section.pk = None
            section.code = f"Q{index}"
            section.name = f"Query {index}"
            section.save()
            offering = original
            offering.pk = None
            offering.section = section
            offering.save()
            CycleCourseOffering.objects.create(cycle_course=parent, offering=offering, campus=self.campus)
            self.make_assignment(parent, faculty, offering=offering)
        with CaptureQueriesContext(connection) as large:
            self.assertTrue(
                ContributorEligibilityService.has_any_eligible_source(
                    user=faculty, tenant_id=self.tenant.id
                )
            )
        self.assertLessEqual(len(large), len(small) + 1)

    def test_roster_audit_contains_no_question_content(self):
        parent, _configuration = self.make_stage5_course()
        faculty = self.make_faculty()
        self.make_assignment(parent, faculty)
        self.initialize(parent)
        audit = AuditLog.objects.get(action="DE_EXAM_CONTRIBUTOR_ROSTER_INITIALIZED")
        rendered = str(audit.metadata_json).lower()
        self.assertNotIn("question_text", rendered)
        self.assertNotIn("choice_a", rendered)


class Stage5SubmittedContributionImmutabilityTests(Stage5FixtureMixin, Stage4TestCase):
    CONTRIBUTION_SNAPSHOT_FIELDS = (
        "cycle_course_id",
        "faculty_user_id",
        "source_assignment_id",
        "source_campus_id",
        "quota_snapshot",
        "configuration_revision_snapshot",
        "revision",
        "roster_status",
        "roster_blocked_at",
        "status",
        "submitted_at",
        "updated_at",
    )
    SOURCE_SNAPSHOT_FIELDS = (
        "id",
        "contribution_id",
        "assignment_id",
        "assignment_id_snapshot",
        "offering_id_snapshot",
        "tenant_id_snapshot",
        "campus_id_snapshot",
        "is_current",
        "invalidated_at",
        "updated_at",
    )

    def add_grouped_offering(self, *, campus, department, slug):
        program = Program.objects.create(
            tenant=self.tenant,
            campus=campus,
            department=department,
            code=f"S5-{slug}",
            name=f"Stage 5 {slug}",
        )
        section = Section.objects.create(
            tenant=self.tenant,
            campus=campus,
            department=department,
            program=program,
            code=f"S5-{slug}",
            name=f"Stage 5 {slug}",
        )
        offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=campus,
            department=department,
            program=program,
            academic_year=self.parent.cycle.academic_year,
            term=self.parent.cycle.term,
            course=self.parent.course,
            section=section,
        )
        CycleCourseOffering.objects.create(
            cycle_course=self.parent,
            offering=offering,
            campus=campus,
        )
        return offering

    def setUp(self):
        super().setUp()
        self.parent, self.configuration = self.make_stage5_course()
        self.faculty = self.make_faculty("submitted-faculty")
        self.assignment = self.make_assignment(self.parent, self.faculty)
        self.initialize(self.parent)
        self.contribution = FacultyContribution.objects.get(faculty_user=self.faculty)

    def submit_exact_quota(self):
        Question.objects.bulk_create(
            [
                Question(
                    contribution=self.contribution,
                    question_text=f"Submitted question {position}",
                    choice_a="A",
                    choice_b="B",
                    choice_c="C",
                    choice_d="D",
                    correct_answer="A",
                    difficulty=Question.Difficulty.EASY,
                    position=position,
                )
                for position in range(1, self.contribution.quota_snapshot + 1)
            ]
        )
        submitted, changed = QuestionMutationService.submit(
            contribution_id=self.contribution.id,
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            expected_contribution_revision=self.contribution.revision,
        )
        self.assertTrue(changed)
        self.contribution = submitted

    def immutable_snapshot(self):
        contribution = FacultyContribution.objects.values(*self.CONTRIBUTION_SNAPSHOT_FIELDS).get(
            pk=self.contribution.pk
        )
        sources = list(
            self.contribution.eligibility_sources.order_by("id").values(
                *self.SOURCE_SNAPSHOT_FIELDS
            )
        )
        questions = list(
            Question.objects.filter(contribution=self.contribution)
            .order_by("id")
            .values(
                "id",
                "contribution_id",
                "position",
                "revision",
                "entry_method",
                "question_text",
                "choice_a",
                "choice_b",
                "choice_c",
                "choice_d",
                "correct_answer",
                "difficulty",
            )
        )
        return contribution, sources, questions

    def assert_immutable_snapshot(self, expected):
        self.assertEqual(self.immutable_snapshot(), expected)

    def test_submitted_source_loss_and_restoration_preserve_snapshot_and_audit(self):
        self.submit_exact_quota()
        frozen = self.immutable_snapshot()
        synchronized_audits = AuditLog.objects.filter(
            action="DE_EXAM_CONTRIBUTOR_ROSTER_SYNCHRONIZED"
        ).count()
        assignment_audits = AuditLog.objects.filter(
            action="DE_EXAM_CONTRIBUTION_ASSIGNMENT_RESOLVED",
            entity_id=str(self.contribution.id),
        ).count()

        self.assignment.is_active = False
        self.assignment.save(update_fields=["is_active", "updated_at"])
        loss = ContributionRosterService.synchronize(
            cycle_course_id=self.parent.id,
            tenant_id=self.tenant.id,
            actor=self.configurer,
        )
        self.assertEqual(loss, {"changed": False, "created": 0, "activated": 0, "blocked": 0})
        self.assert_immutable_snapshot(frozen)

        self.assignment.is_active = True
        self.assignment.save(update_fields=["is_active", "updated_at"])
        restoration = ContributionRosterService.synchronize(
            cycle_course_id=self.parent.id,
            tenant_id=self.tenant.id,
            actor=self.configurer,
        )
        self.assertEqual(
            restoration,
            {"changed": False, "created": 0, "activated": 0, "blocked": 0},
        )
        self.assert_immutable_snapshot(frozen)
        self.assertEqual(
            AuditLog.objects.filter(action="DE_EXAM_CONTRIBUTOR_ROSTER_SYNCHRONIZED").count(),
            synchronized_audits,
        )
        self.assertEqual(
            AuditLog.objects.filter(
                action="DE_EXAM_CONTRIBUTION_ASSIGNMENT_RESOLVED",
                entity_id=str(self.contribution.id),
            ).count(),
            assignment_audits,
        )

    def test_replacement_faculty_receives_separate_draft_without_inheriting_questions(self):
        self.submit_exact_quota()
        frozen = self.immutable_snapshot()
        original_question_ids = set(
            Question.objects.filter(contribution=self.contribution).values_list("id", flat=True)
        )
        self.assignment.is_active = False
        self.assignment.save(update_fields=["is_active", "updated_at"])
        replacement = self.make_faculty("replacement-faculty")
        replacement_assignment = self.make_assignment(self.parent, replacement)

        result = ContributionRosterService.synchronize(
            cycle_course_id=self.parent.id,
            tenant_id=self.tenant.id,
            actor=self.configurer,
        )

        self.assertEqual(result, {"changed": True, "created": 1, "activated": 0, "blocked": 0})
        self.assert_immutable_snapshot(frozen)
        replacement_contribution = FacultyContribution.objects.get(faculty_user=replacement)
        self.assertEqual(replacement_contribution.status, FacultyContribution.Status.DRAFT)
        self.assertEqual(replacement_contribution.roster_status, FacultyContribution.RosterStatus.ACTIVE)
        self.assertEqual(replacement_contribution.source_assignment, replacement_assignment)
        self.assertFalse(Question.objects.filter(contribution=replacement_contribution).exists())
        self.assertEqual(
            set(Question.objects.filter(contribution=self.contribution).values_list("id", flat=True)),
            original_question_ids,
        )
        audit = AuditLog.objects.get(action="DE_EXAM_CONTRIBUTOR_ROSTER_SYNCHRONIZED")
        self.assertEqual(audit.metadata_json["contributions_created"], 1)
        self.assertEqual(audit.metadata_json["contributions_activated"], 0)
        self.assertEqual(audit.metadata_json["contributions_blocked"], 0)
        self.assertFalse(
            AuditLog.objects.filter(
                action="DE_EXAM_CONTRIBUTION_ASSIGNMENT_RESOLVED",
                entity_id=str(self.contribution.id),
            ).exists()
        )

    def test_submitted_primary_source_is_not_rebound_or_extended(self):
        secondary_offering = self.add_grouped_offering(
            campus=self.campus,
            department=self.department,
            slug="SECOND",
        )
        secondary = self.make_assignment(
            self.parent,
            self.faculty,
            offering=secondary_offering,
        )
        ContributionRosterService.synchronize(
            cycle_course_id=self.parent.id,
            tenant_id=self.tenant.id,
            actor=self.configurer,
        )
        self.submit_exact_quota()
        frozen = self.immutable_snapshot()
        self.assignment.is_active = False
        self.assignment.save(update_fields=["is_active", "updated_at"])
        later_offering = self.add_grouped_offering(
            campus=self.campus,
            department=self.department,
            slug="LATER",
        )
        later = self.make_assignment(
            self.parent,
            self.faculty,
            offering=later_offering,
        )

        result = ContributionRosterService.synchronize(
            cycle_course_id=self.parent.id,
            tenant_id=self.tenant.id,
            actor=self.configurer,
        )

        self.assertEqual(result, {"changed": False, "created": 0, "activated": 0, "blocked": 0})
        self.assert_immutable_snapshot(frozen)
        self.assertEqual(self.contribution.eligibility_sources.count(), 2)
        self.assertNotIn(
            later.id,
            self.contribution.eligibility_sources.values_list(
                "assignment_id_snapshot", flat=True
            ),
        )
        self.assertEqual(frozen[0]["source_assignment_id"], self.assignment.id)
        self.assertNotEqual(frozen[0]["source_assignment_id"], secondary.id)

    def test_assignment_deletion_retains_submitted_history_without_rebind(self):
        self.submit_exact_quota()
        contribution_before, sources_before, questions_before = self.immutable_snapshot()
        assignment_id = self.assignment.id
        offering_id = self.assignment.offering_id
        campus_id = self.assignment.campus_id

        self.assignment.delete()
        self.contribution.refresh_from_db()
        source = self.contribution.eligibility_sources.get()
        self.assertIsNone(self.contribution.source_assignment_id)
        self.assertIsNone(source.assignment_id)
        self.assertEqual(source.assignment_id_snapshot, assignment_id)
        self.assertEqual(source.offering_id_snapshot, offering_id)
        self.assertEqual(source.campus_id_snapshot, campus_id)
        self.assertEqual(self.contribution.source_campus_id, campus_id)
        self.assertEqual(self.contribution.status, FacultyContribution.Status.SUBMITTED)
        self.assertEqual(self.contribution.roster_status, contribution_before["roster_status"])
        self.assertEqual(self.contribution.roster_blocked_at, contribution_before["roster_blocked_at"])
        self.assertEqual(self.contribution.revision, contribution_before["revision"])
        self.assertEqual(
            list(
                Question.objects.filter(contribution=self.contribution)
                .order_by("id")
                .values(*questions_before[0].keys())
            ),
            questions_before,
        )
        result = ContributionRosterService.synchronize(
            cycle_course_id=self.parent.id,
            tenant_id=self.tenant.id,
            actor=self.configurer,
        )
        self.assertEqual(result, {"changed": False, "created": 0, "activated": 0, "blocked": 0})
        self.contribution.refresh_from_db()
        source.refresh_from_db()
        self.assertIsNone(self.contribution.source_assignment_id)
        self.assertIsNone(source.assignment_id)
        for field in (
            "assignment_id_snapshot",
            "offering_id_snapshot",
            "tenant_id_snapshot",
            "campus_id_snapshot",
            "is_current",
            "invalidated_at",
            "updated_at",
        ):
            self.assertEqual(getattr(source, field), sources_before[0][field])

    def test_cross_campus_sources_deduplicate_and_apply_exact_permissions(self):
        other_offering = self.add_grouped_offering(
            campus=self.other_campus,
            department=self.other_department,
            slug="NORTH",
        )
        other_permission = UserPermission.objects.create(
            user=self.faculty,
            permission=Permission.objects.get(code="faculty_portal.access"),
            tenant=self.tenant,
            campus=self.other_campus,
            grant_type=UserPermission.GrantType.ALLOW,
        )
        other_assignment = self.make_assignment(
            self.parent,
            self.faculty,
            campus=self.other_campus,
            offering=other_offering,
        )

        ContributionRosterService.synchronize(
            cycle_course_id=self.parent.id,
            tenant_id=self.tenant.id,
            actor=self.configurer,
        )
        self.assertEqual(FacultyContribution.objects.filter(faculty_user=self.faculty).count(), 1)
        self.assertEqual(self.contribution.eligibility_sources.filter(is_current=True).count(), 2)
        inventory = ContributorEligibilityService.source_inventory(cycle_course=self.parent)
        self.assertEqual(
            {self.assignment.id, other_assignment.id},
            {assignment.id for assignment in inventory.eligible_sources},
        )

        other_permission.grant_type = UserPermission.GrantType.DENY
        other_permission.save(update_fields=["grant_type"])
        result = ContributionRosterService.synchronize(
            cycle_course_id=self.parent.id,
            tenant_id=self.tenant.id,
            actor=self.configurer,
        )
        self.contribution.refresh_from_db()
        self.assertTrue(result["changed"])
        self.assertEqual(result["blocked"], 0)
        self.assertEqual(self.contribution.roster_status, FacultyContribution.RosterStatus.ACTIVE)
        self.assertTrue(
            self.contribution.eligibility_sources.get(
                assignment_id_snapshot=self.assignment.id
            ).is_current
        )
        denied_source = self.contribution.eligibility_sources.get(
            assignment_id_snapshot=other_assignment.id
        )
        self.assertFalse(denied_source.is_current)
        self.assertIsNotNone(denied_source.invalidated_at)


class Stage5ManualQuestionTests(Stage5FixtureMixin, Stage4TestCase):
    def setUp(self):
        super().setUp()
        self.parent, self.configuration = self.make_stage5_course()
        self.faculty = self.make_faculty()
        self.assignment = self.make_assignment(self.parent, self.faculty)
        self.initialize(self.parent)
        self.contribution = FacultyContribution.objects.get()

    def create_question(self, payload=None):
        return QuestionMutationService.create(
            contribution_id=self.contribution.id,
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            expected_contribution_revision=self.contribution.revision,
            payload=payload or self.payload(),
        )

    def test_create_normalizes_text_answer_and_difficulty(self):
        question = self.create_question(
            self.payload("  First line\r\nSecond line  ")
        )
        self.assertEqual(question.question_text, "First line\nSecond line")
        self.assertEqual(question.correct_answer, "D")
        self.assertEqual(question.difficulty, "EASY")
        self.assertEqual(question.position, 1)
        self.contribution.refresh_from_db()
        self.assertEqual(self.contribution.revision, 2)

    def test_duplicate_normalized_choices_are_rejected(self):
        payload = self.payload()
        payload["choice_a"] = "Ｆoo"
        payload["choice_b"] = " foo  "
        with self.assertRaises(ValidationError):
            self.create_question(payload)
        self.assertFalse(Question.objects.exists())

    def test_payload_validation_matrix_rejects_blank_invalid_and_overlength_values(self):
        cases = []
        blank_question = self.payload()
        blank_question["question_text"] = "   "
        cases.append(blank_question)
        blank_choice = self.payload()
        blank_choice["choice_b"] = ""
        cases.append(blank_choice)
        invalid_answer = self.payload()
        invalid_answer["correct_answer"] = "E"
        cases.append(invalid_answer)
        invalid_difficulty = self.payload()
        invalid_difficulty["difficulty"] = "Expert"
        cases.append(invalid_difficulty)
        long_question = self.payload("x" * 5001)
        cases.append(long_question)
        long_choice = self.payload()
        long_choice["choice_a"] = "x" * 1001
        cases.append(long_choice)
        for payload in cases:
            with self.subTest(keys=[key for key, value in payload.items() if not value or len(str(value)) > 1000]):
                with self.assertRaises(ValidationError):
                    self.create_question(payload)
        self.assertFalse(Question.objects.exists())

    def test_stale_contribution_revision_is_rejected_without_write(self):
        self.contribution.revision += 1
        self.contribution.save(update_fields=["revision", "updated_at"])
        with self.assertRaises(ValidationError):
            QuestionMutationService.create(
                contribution_id=self.contribution.id,
                user=self.faculty,
                tenant_id=self.tenant.id,
                campus_id=self.campus.id,
                expected_contribution_revision=1,
                payload=self.payload(),
            )
        self.assertFalse(Question.objects.exists())

    def test_create_at_exact_quota_is_conflict_without_write_revision_or_audit(self):
        Question.objects.bulk_create(
            [
                Question(
                    contribution=self.contribution,
                    question_text=f"Full quota question {position}",
                    choice_a="A",
                    choice_b="B",
                    choice_c="C",
                    choice_d="D",
                    correct_answer="A",
                    difficulty="EASY",
                    position=position,
                )
                for position in range(1, 51)
            ]
        )
        revision_before = self.contribution.revision
        audit_count = AuditLog.objects.filter(action="DE_EXAM_QUESTION_CREATED").count()

        with self.assertRaises(ContributionQuotaReached):
            self.create_question(self.payload("A forbidden fifty-first question"))

        self.contribution.refresh_from_db()
        self.assertEqual(self.contribution.revision, revision_before)
        self.assertEqual(self.contribution.questions.count(), 50)
        self.assertEqual(
            AuditLog.objects.filter(action="DE_EXAM_QUESTION_CREATED").count(),
            audit_count,
        )

    def test_blocked_draft_mutation_is_denied(self):
        self.assignment.is_active = False
        self.assignment.save(update_fields=["is_active", "updated_at"])
        ContributionRosterService.synchronize(
            cycle_course_id=self.parent.id,
            tenant_id=self.tenant.id,
            actor=self.configurer,
        )
        self.contribution.refresh_from_db()
        with self.assertRaises(PermissionDenied):
            self.create_question()

    def test_update_noop_preserves_revisions_and_audit_count(self):
        question = self.create_question()
        self.contribution.refresh_from_db()
        audit_count = AuditLog.objects.filter(action="DE_EXAM_QUESTION_UPDATED").count()
        _question, changed = QuestionMutationService.update(
            contribution_id=self.contribution.id,
            question_id=question.id,
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            expected_contribution_revision=self.contribution.revision,
            expected_question_revision=question.revision,
            payload=self.payload(),
        )
        self.assertFalse(changed)
        self.contribution.refresh_from_db()
        question.refresh_from_db()
        self.assertEqual((self.contribution.revision, question.revision), (2, 1))
        self.assertEqual(AuditLog.objects.filter(action="DE_EXAM_QUESTION_UPDATED").count(), audit_count)

    def test_reorder_requires_exact_owned_set(self):
        first = self.create_question()
        self.contribution.refresh_from_db()
        second = self.create_question(self.payload("A second question"))
        self.contribution.refresh_from_db()
        with self.assertRaises(ValidationError):
            QuestionMutationService.reorder(
                contribution_id=self.contribution.id,
                ordered_question_ids=[first.id],
                user=self.faculty,
                tenant_id=self.tenant.id,
                campus_id=self.campus.id,
                expected_contribution_revision=self.contribution.revision,
            )
        QuestionMutationService.reorder(
            contribution_id=self.contribution.id,
            ordered_question_ids=[second.id, first.id],
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            expected_contribution_revision=self.contribution.revision,
        )
        self.assertEqual(
            list(Question.objects.order_by("position").values_list("id", flat=True)),
            [second.id, first.id],
        )

    def test_submission_requires_exact_quota_and_is_idempotent(self):
        questions = [
            Question(
                contribution=self.contribution,
                question_text=f"Question {index}",
                choice_a="A",
                choice_b="B",
                choice_c="C",
                choice_d="D",
                correct_answer="A",
                difficulty="EASY",
                position=index,
            )
            for index in range(1, 51)
        ]
        Question.objects.bulk_create(questions)
        submitted, changed = QuestionMutationService.submit(
            contribution_id=self.contribution.id,
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            expected_contribution_revision=self.contribution.revision,
        )
        self.assertTrue(changed)
        self.assertEqual(submitted.status, "SUBMITTED")
        replay, replay_changed = QuestionMutationService.submit(
            contribution_id=self.contribution.id,
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            expected_contribution_revision=1,
        )
        self.assertFalse(replay_changed)
        self.assertEqual(replay.pk, submitted.pk)
        self.assertEqual(AuditLog.objects.filter(action="DE_EXAM_CONTRIBUTION_SUBMITTED").count(), 1)

    def test_submission_under_quota_and_past_deadline_are_denied(self):
        with self.assertRaises(ValidationError):
            QuestionMutationService.submit(
                contribution_id=self.contribution.id,
                user=self.faculty,
                tenant_id=self.tenant.id,
                campus_id=self.campus.id,
                expected_contribution_revision=self.contribution.revision,
            )
        self.configuration.contribution_deadline = timezone.now() - timezone.timedelta(minutes=1)
        # First-open history is immutable through normal save; the database-level
        # update simulates deadline crossing for the locked authorization check.
        type(self.configuration).objects.filter(pk=self.configuration.pk).update(
            contribution_deadline=self.configuration.contribution_deadline
        )
        with self.assertRaises(PermissionDenied):
            self.create_question()


class Stage5QuestionDeleteTests(Stage5FixtureMixin, Stage4TestCase):
    def setUp(self):
        super().setUp()
        self.parent, self.configuration = self.make_stage5_course()
        self.faculty = self.make_faculty("delete-faculty")
        self.assignment = self.make_assignment(self.parent, self.faculty)
        self.initialize(self.parent)
        self.contribution = FacultyContribution.objects.get()

    def create_questions(self, count):
        questions = []
        for index in range(1, count + 1):
            questions.append(
                QuestionMutationService.create(
                    contribution_id=self.contribution.id,
                    user=self.faculty,
                    tenant_id=self.tenant.id,
                    campus_id=self.campus.id,
                    expected_contribution_revision=self.contribution.revision,
                    payload=self.payload(f"Question {index}"),
                )
            )
            self.contribution.refresh_from_db()
        return questions

    def delete_question(
        self,
        question,
        *,
        expected_contribution_revision=None,
        expected_question_revision=None,
    ):
        return QuestionMutationService.delete(
            contribution_id=self.contribution.id,
            question_id=question.id,
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            expected_contribution_revision=(
                self.contribution.revision
                if expected_contribution_revision is None
                else expected_contribution_revision
            ),
            expected_question_revision=(
                question.revision
                if expected_question_revision is None
                else expected_question_revision
            ),
        )

    def question_state(self):
        return list(
            Question.objects.filter(contribution=self.contribution)
            .order_by("position", "id")
            .values_list("id", "position", "revision")
        )

    @staticmethod
    def delete_audits():
        return AuditLog.objects.filter(action="DE_EXAM_QUESTION_DELETED")

    def test_delete_first_of_two_resequences_and_increments_once_with_one_audit(self):
        first, second = self.create_questions(2)
        deleted_id = first.id
        revision_before = self.contribution.revision

        self.delete_question(first)

        self.contribution.refresh_from_db()
        self.assertEqual(self.question_state(), [(second.id, 1, second.revision)])
        self.assertEqual(self.contribution.revision, revision_before + 1)
        audit = self.delete_audits().get()
        self.assertEqual(audit.entity_id, str(deleted_id))
        self.assertEqual(audit.metadata_json["deleted_position"], 1)
        self.assertEqual(audit.metadata_json["resulting_count"], 1)
        self.assertEqual(audit.metadata_json["revision_before"], revision_before)
        self.assertEqual(audit.metadata_json["revision_after"], revision_before + 1)

    def test_delete_middle_question_leaves_contiguous_positions(self):
        first, middle, last = self.create_questions(3)

        self.delete_question(middle)

        self.assertEqual(
            self.question_state(),
            [(first.id, 1, first.revision), (last.id, 2, last.revision)],
        )
        self.assertEqual(self.delete_audits().count(), 1)

    def test_delete_last_question_preserves_earlier_positions(self):
        first, middle, last = self.create_questions(3)

        self.delete_question(last)

        self.assertEqual(
            self.question_state(),
            [(first.id, 1, first.revision), (middle.id, 2, middle.revision)],
        )
        self.assertEqual(self.delete_audits().count(), 1)

    def test_delete_only_question_requires_no_resequence_write(self):
        (only,) = self.create_questions(1)

        self.delete_question(only)

        self.assertEqual(self.question_state(), [])
        self.assertEqual(self.delete_audits().count(), 1)

    def test_stale_contribution_revision_rejects_delete_without_changes(self):
        first, second = self.create_questions(2)
        state_before = self.question_state()
        revision_before = self.contribution.revision

        with self.assertRaises(ContributionConflict):
            self.delete_question(
                first,
                expected_contribution_revision=revision_before - 1,
            )

        self.contribution.refresh_from_db()
        self.assertEqual(self.question_state(), state_before)
        self.assertEqual(self.contribution.revision, revision_before)
        self.assertTrue(Question.objects.filter(pk=second.id).exists())
        self.assertFalse(self.delete_audits().exists())

    def test_stale_question_revision_rejects_delete_without_changes(self):
        first, _second = self.create_questions(2)
        state_before = self.question_state()
        revision_before = self.contribution.revision

        with self.assertRaises(ContributionConflict):
            self.delete_question(
                first,
                expected_question_revision=first.revision + 1,
            )

        self.contribution.refresh_from_db()
        self.assertEqual(self.question_state(), state_before)
        self.assertEqual(self.contribution.revision, revision_before)
        self.assertFalse(self.delete_audits().exists())

    def test_blocked_draft_delete_is_denied_without_changes(self):
        first, _second = self.create_questions(2)
        state_before = self.question_state()
        revision_before = self.contribution.revision
        FacultyContribution.objects.filter(pk=self.contribution.pk).update(
            roster_status=FacultyContribution.RosterStatus.BLOCKED,
            roster_blocked_at=timezone.now(),
        )

        with self.assertRaises(PermissionDenied):
            self.delete_question(first)

        self.contribution.refresh_from_db()
        self.assertEqual(self.question_state(), state_before)
        self.assertEqual(self.contribution.revision, revision_before)
        self.assertFalse(self.delete_audits().exists())

    def test_submitted_delete_is_denied_without_changes(self):
        first, _second = self.create_questions(2)
        state_before = self.question_state()
        revision_before = self.contribution.revision
        FacultyContribution.objects.filter(pk=self.contribution.pk).update(
            status=FacultyContribution.Status.SUBMITTED,
            submitted_at=timezone.now(),
        )

        with self.assertRaises(PermissionDenied):
            self.delete_question(first)

        self.contribution.refresh_from_db()
        self.assertEqual(self.question_state(), state_before)
        self.assertEqual(self.contribution.revision, revision_before)
        self.assertFalse(self.delete_audits().exists())

    def test_non_owner_question_id_returns_404_without_leakage(self):
        other_faculty = self.make_faculty("delete-other-faculty")
        self.make_assignment(self.parent, other_faculty)
        ContributionRosterService.synchronize(
            cycle_course_id=self.parent.id,
            tenant_id=self.tenant.id,
            actor=self.configurer,
        )
        self.contribution.refresh_from_db()
        other_contribution = FacultyContribution.objects.exclude(
            pk=self.contribution.pk
        ).get(faculty_user=other_faculty)
        other_question = Question.objects.create(
            contribution=other_contribution,
            question_text="Other owner question",
            choice_a="A",
            choice_b="B",
            choice_c="C",
            choice_d="D",
            correct_answer="A",
            difficulty="EASY",
            position=1,
        )

        with self.assertRaises(Http404):
            QuestionMutationService.delete(
                contribution_id=self.contribution.id,
                question_id=other_question.id,
                user=self.faculty,
                tenant_id=self.tenant.id,
                campus_id=self.campus.id,
                expected_contribution_revision=self.contribution.revision,
                expected_question_revision=other_question.revision,
            )

        self.assertTrue(Question.objects.filter(pk=other_question.id).exists())
        self.assertFalse(self.delete_audits().exists())

    def test_repeated_delete_returns_404_without_second_change(self):
        (question,) = self.create_questions(1)
        deleted_id = question.id
        self.delete_question(question)
        self.contribution.refresh_from_db()
        revision_after_delete = self.contribution.revision

        with self.assertRaises(Http404):
            QuestionMutationService.delete(
                contribution_id=self.contribution.id,
                question_id=deleted_id,
                user=self.faculty,
                tenant_id=self.tenant.id,
                campus_id=self.campus.id,
                expected_contribution_revision=revision_after_delete,
                expected_question_revision=question.revision,
            )

        self.contribution.refresh_from_db()
        self.assertEqual(self.contribution.revision, revision_after_delete)
        self.assertEqual(self.delete_audits().count(), 1)

    def test_audit_failure_rolls_back_delete_resequence_and_revision(self):
        _first, middle, _last = self.create_questions(3)
        state_before = self.question_state()
        revision_before = self.contribution.revision

        with patch.object(
            QuestionMutationService,
            "_audit",
            side_effect=RuntimeError("simulated audit failure"),
        ):
            with self.assertRaises(RuntimeError):
                self.delete_question(middle)

        self.contribution.refresh_from_db()
        self.assertEqual(self.question_state(), state_before)
        self.assertEqual(self.contribution.revision, revision_before)
        self.assertFalse(self.delete_audits().exists())

"""Rendered, scoped review/confirmation coverage for course-code equivalency."""

from django.urls import reverse
from django.utils import timezone

from apps.academics.models import CourseOffering, Section
from apps.core.services.settings import SystemSettingService
from apps.rbac.models import Permission, UserPermission
from apps.tenants.models import Program

from .exam_units import ExamCourseEquivalencyService
from .models import (
    CourseExamConfiguration,
    CycleCourseOffering,
    ExamCourseEquivalencyGroup,
    ExamCourseEquivalencyMembership,
    ExamGenerationRevision,
    ExaminationCycle,
    FacultyContribution,
    QuestionnairePrintRelease,
)
from .stage4_test_support import Stage4TestCase


class EquivalentCoursesUITests(Stage4TestCase):
    def setUp(self):
        super().setUp()
        self.cycle = self.make_cycle(status=ExaminationCycle.Status.OPEN)
        self.cycle.processing_mode = ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        self.cycle.save(update_fields=["processing_mode", "updated_at"])
        self.deadline = self.future_deadline()
        self.primary = self._course("UI-A")
        self.secondary = self._course("UI-B")
        self.client.force_login(self.admin)

    def _course(self, code):
        course = self.make_course(cycle=self.cycle, code=code)
        self.make_configuration(course, deadline=self.deadline)
        return course

    def _url(self, group=None):
        return reverse(
            "departmental_exams:equivalent_courses_group" if group else "departmental_exams:equivalent_courses",
            args=[self.cycle.id, group.id] if group else [self.cycle.id],
        )

    def _review(self, *, members=None, primary=None, group=None, name="Shared Standard Exam"):
        members = members or (self.primary, self.secondary)
        return self.client.post(self._url(group), {
            "intent": "review", "name": name,
            "members": [str(row.id) for row in members],
            "primary": str((primary or self.primary).id),
        })

    def _create(self):
        review = self._review()
        self.assertEqual(review.status_code, 200)
        self.assertIsNotNone(review.context["review"])
        result = self.client.post(self._url(), {
            "intent": "confirm", "confirmation": review.context["review"]["token"],
        })
        self.assertEqual(result.status_code, 302)
        return ExamCourseEquivalencyGroup.objects.get(cycle=self.cycle, is_active=True)

    def _north_offering(self, course):
        program = Program.objects.create(
            tenant=self.tenant, campus=self.other_campus, department=self.other_department,
            code=f"UI-P-{course.id}", name="North program",
        )
        section = Section.objects.create(
            tenant=self.tenant, campus=self.other_campus, department=self.other_department,
            program=program, code=f"UI-S-{course.id}", name="North section",
        )
        offering = CourseOffering.objects.create(
            tenant=self.tenant, campus=self.other_campus, department=self.other_department,
            program=program, academic_year=self.cycle.academic_year, term=self.cycle.term,
            course=course.course, section=section,
        )
        CycleCourseOffering.objects.create(cycle_course=course, offering=offering, campus=self.other_campus)

    def _revision(self):
        return ExamGenerationRevision.objects.create(
            cycle_course=self.primary, revision_number=1,
            source_input_fingerprint="a" * 64, algorithm_version="ui-test-v1",
            generation_trigger=ExamGenerationRevision.GenerationTrigger.AUTOMATIC,
            configuration_revision_snapshot=1, blueprint_revision_snapshot=1,
            roster_boundary_snapshot="b" * 64, final_item_count_snapshot=50,
            request_token_digest="c" * 64, minimum_overlap=0,
            proportional_score=0, contributors_represented=1,
            squared_contributor_concentration=1,
        )

    def test_create_shows_actual_member_codes_campuses_counts_and_preserves_courses(self):
        self._north_offering(self.secondary)
        faculty = self.make_user("ui-contributor", self.department, ())
        FacultyContribution.objects.create(
            cycle_course=self.secondary, faculty_user=faculty, source_campus=self.other_campus,
            quota_snapshot=50, configuration_revision_snapshot=1,
        )
        review = self._review()
        self.assertEqual(review.status_code, 200)
        self.assertContains(review, self.primary.course.code)
        self.assertContains(review, self.secondary.course.code)
        self.assertContains(review, "North")
        self.assertContains(review, "Current contributions")
        self.assertContains(review, "Final items 50")
        token = review.context["review"]["token"]
        created = self.client.post(self._url(), {"intent": "confirm", "confirmation": token})
        self.assertEqual(created.status_code, 302)
        group = ExamCourseEquivalencyGroup.objects.get(cycle=self.cycle, is_active=True)
        self.assertEqual(group.primary_cycle_course_id, self.primary.id)
        self.assertEqual(set(group.memberships.values_list("cycle_course_id", flat=True)), {self.primary.id, self.secondary.id})
        self.assertEqual(FacultyContribution.objects.get(faculty_user=faculty).cycle_course_id, self.secondary.id)
        listed = self.client.get(self._url())
        self.assertContains(listed, self.secondary.course.code)
        self.assertContains(listed, "North")

    def test_replace_primary_and_members_then_retire_with_history(self):
        group = self._create()
        third = self._course("UI-C")
        review = self._review(group=group, members=(self.secondary, third), primary=self.secondary)
        self.assertIsNotNone(review.context["review"])
        result = self.client.post(self._url(group), {
            "intent": "confirm", "confirmation": review.context["review"]["token"],
        })
        self.assertEqual(result.status_code, 302)
        group.refresh_from_db()
        self.assertEqual(group.primary_cycle_course_id, self.secondary.id)
        self.assertEqual(set(group.memberships.filter(active_marker=1).values_list("cycle_course_id", flat=True)), {self.secondary.id, third.id})
        self.assertIsNone(group.memberships.get(cycle_course=self.primary).active_marker)
        review = self.client.post(self._url(group), {
            "intent": "review_retirement", "reason": "These codes must be separate again.",
        })
        self.assertIsNotNone(review.context["review"])
        result = self.client.post(self._url(group), {
            "intent": "confirm", "confirmation": review.context["review"]["token"],
        })
        self.assertEqual(result.status_code, 302)
        group.refresh_from_db()
        self.assertFalse(group.is_active)
        self.assertEqual(group.memberships.filter(active_marker=1).count(), 0)
        self.assertEqual(ExamCourseEquivalencyMembership.objects.filter(group=group).count(), 3)

    def test_stale_review_or_wrong_actor_cannot_change_group(self):
        review = self._review()
        token = review.context["review"]["token"]
        other_manager = self.make_user("ui-other-manager", self.department, (
            "admin_portal.access", "departmental_exams.manage_exam_generation",
        ))
        self.client.force_login(other_manager)
        self.assertEqual(self.client.post(self._url(), {
            "intent": "confirm", "confirmation": token,
        }).status_code, 409)
        self.client.force_login(self.admin)
        config = CourseExamConfiguration.objects.get(cycle_course=self.secondary)
        config.coverage = "Changed coverage"
        config.save(update_fields=["coverage", "updated_at"])
        result = self.client.post(self._url(), {"intent": "confirm", "confirmation": token})
        self.assertEqual(result.status_code, 409)
        self.assertContains(result, "changed", status_code=409)
        self.assertFalse(ExamCourseEquivalencyGroup.objects.exists())
        fresh = self._review()
        self.assertIsNone(fresh.context["review"])

    def test_course_code_and_title_changes_refresh_signed_review_without_mutation(self):
        for field, value in (("code", "UI-B-RENAMED"), ("title", "Renamed course title")):
            with self.subTest(field=field):
                review = self._review()
                self.assertEqual(review.status_code, 200)
                token = review.context["review"]["token"]
                course = self.secondary.course
                old_value = getattr(course, field)
                setattr(course, field, value)
                course.save(update_fields=[field, "updated_at"])

                stale = self.client.post(self._url(), {
                    "intent": "confirm", "confirmation": token,
                })
                self.assertEqual(stale.status_code, 409)
                self.assertFalse(ExamCourseEquivalencyGroup.objects.exists())
                refreshed = stale.context["review"]
                self.assertIsNotNone(refreshed)
                self.assertContains(stale, value, status_code=409)
                self.assertEqual(
                    next(row for row in refreshed["members"] if row["cycle_course_id"] == self.secondary.id)[field],
                    value,
                )
                setattr(course, field, old_value)
                course.save(update_fields=[field, "updated_at"])

    def test_new_offering_campus_snapshot_refreshes_review_without_mutation(self):
        review = self._review()
        self.assertEqual(review.status_code, 200)
        token = review.context["review"]["token"]
        original = next(row for row in review.context["review"]["members"]
                        if row["cycle_course_id"] == self.secondary.id)
        self.assertEqual(original["offering_count"], 1)

        self._north_offering(self.secondary)
        stale = self.client.post(self._url(), {
            "intent": "confirm", "confirmation": token,
        })
        self.assertEqual(stale.status_code, 409)
        self.assertFalse(ExamCourseEquivalencyGroup.objects.exists())
        refreshed = stale.context["review"]
        self.assertIsNotNone(refreshed)
        updated = next(row for row in refreshed["members"]
                       if row["cycle_course_id"] == self.secondary.id)
        self.assertEqual(updated["offering_count"], 2)
        self.assertIn("North", updated["campuses"])
        self.assertContains(stale, "North", status_code=409)

    def test_unchanged_signed_review_confirms_successfully(self):
        review = self._review()
        self.assertEqual(review.status_code, 200)
        token = review.context["review"]["token"]
        confirmed = self.client.post(self._url(), {
            "intent": "confirm", "confirmation": token,
        })
        self.assertEqual(confirmed.status_code, 302)
        self.assertEqual(ExamCourseEquivalencyGroup.objects.filter(cycle=self.cycle, is_active=True).count(), 1)

    def test_incompatible_settings_and_generated_history_block_review(self):
        config = CourseExamConfiguration.objects.get(cycle_course=self.secondary)
        config.final_item_count = 60
        config.save(update_fields=["final_item_count", "updated_at"])
        review = self._review()
        self.assertIsNone(review.context["review"])
        self.assertContains(review, "compatible effective item count")
        config.final_item_count = 50
        config.save(update_fields=["final_item_count", "updated_at"])
        group = self._create()
        self._revision()
        page = self.client.get(self._url(group))
        self.assertContains(page, "Read-only")
        self.assertContains(page, "generation processing has begun")
        self.assertNotContains(page, "Review exact membership and settings")
        review = self._review(group=group)
        self.assertIsNone(review.context["review"])

    def test_automatic_processing_history_locks_membership(self):
        group = self._create()
        config = CourseExamConfiguration.objects.get(cycle_course=self.secondary)
        config.automatic_processed_at = timezone.now()
        config.automatic_processing_status = "BLOCKED"
        config.save(update_fields=["automatic_processed_at", "automatic_processing_status", "updated_at"])
        page = self.client.get(self._url(group))
        self.assertContains(page, "Read-only")
        self.assertContains(page, "automatic processing has begun")

    def test_release_history_remains_read_only_after_revocation(self):
        group = self._create()
        revision = self._revision()
        now = timezone.now()
        QuestionnairePrintRelease.objects.create(
            cycle_course=self.primary, generation_revision=revision,
            print_from=now, print_until=now + timezone.timedelta(days=1),
            released_by=self.admin, status=QuestionnairePrintRelease.Status.REVOKED,
            active_marker=None, revoked_by=self.admin, revoked_at=now,
        )
        page = self.client.get(self._url(group))
        self.assertContains(page, "Read-only")
        self.assertContains(page, "after questionnaire release")
        self.assertIsNone(self._review(group=group).context["review"])

    def test_partial_campus_and_direct_deny_hide_group_and_reject_post(self):
        self._north_offering(self.secondary)
        group = self._create()
        partial = self.make_user("ui-partial", self.department, (
            "admin_portal.access", "departmental_exams.manage_exam_generation",
        ))
        self.client.force_login(partial)
        page = self.client.get(self._url())
        self.assertEqual(page.status_code, 403)
        self.assertNotIn(group.name, page.content.decode())
        self.assertNotIn(self.secondary.course.code, page.content.decode())
        self.assertEqual(self.client.get(self._url(group)).status_code, 404)
        forged = self._review()
        self.assertEqual(forged.status_code, 403)
        self.client.force_login(self.admin)
        review = self._review(group=group)
        token = review.context["review"]["token"]
        global_user = self.make_user("ui-global", self.department, ("admin_portal.access",))
        permission = Permission.objects.get(code="departmental_exams.manage_exam_generation")
        UserPermission.objects.create(user=global_user, permission=permission,
                                      grant_type=UserPermission.GrantType.ALLOW, tenant=None, campus=None)
        UserPermission.objects.create(user=global_user, permission=permission,
                                      grant_type=UserPermission.GrantType.DENY,
                                      tenant=self.tenant, campus=self.other_campus)
        self.client.force_login(global_user)
        self.assertEqual(self.client.get(self._url(group)).status_code, 404)
        self.assertEqual(self.client.post(self._url(group), {
            "intent": "confirm", "confirmation": token,
        }).status_code, 404)

    def test_complete_campus_manager_can_view_and_setup_summary_show_members(self):
        self._north_offering(self.secondary)
        group = self._create()
        manager = self.make_user("ui-complete", self.department, ("admin_portal.access",))
        UserPermission.objects.create(
            user=manager,
            permission=Permission.objects.get(code="departmental_exams.manage_exam_generation"),
            grant_type=UserPermission.GrantType.ALLOW, tenant=None, campus=None,
        )
        self.client.force_login(manager)
        self.assertContains(self.client.get(self._url(group)), self.secondary.course.code)
        manager_setup = self.client.get(reverse("departmental_exams:course_setup", args=[self.cycle.id]))
        self.assertContains(manager_setup, "Equivalent course codes")
        self.client.force_login(self.admin)
        setup = self.client.get(reverse("departmental_exams:course_setup", args=[self.cycle.id]))
        self.assertContains(setup, group.name)
        self.assertContains(setup, self.secondary.course.code)
        summary = self.client.get(reverse("departmental_exams:automatic_generation_summary", args=[self.cycle.id]))
        self.assertContains(summary, group.name)
        self.assertContains(summary, self.secondary.course.code)

    def test_tenant_and_feature_boundaries(self):
        other_cycle = ExaminationCycle.objects.create(
            tenant=self.other_tenant, academic_year=self.cycle.academic_year,
            term=self.cycle.term, exam_period=ExaminationCycle.ExamPeriod.MIDTERM,
            created_by=self.admin,
        )
        self.assertEqual(self.client.get(reverse("departmental_exams:equivalent_courses", args=[other_cycle.id])).status_code, 404)
        SystemSettingService.set("FEATURE_DEPARTMENTAL_EXAM_BUILDER_ENABLED", False,
                                 tenant_id=self.tenant.id, value_type="BOOL")
        self.assertEqual(self.client.get(self._url()).status_code, 403)
        self.assertEqual(self.client.post(self._url(), {"intent": "review"}).status_code, 403)

    def test_signed_review_rejects_tampering_and_cross_page_replay(self):
        review = self._review()
        token = review.context["review"]["token"]
        self.assertEqual(self.client.post(self._url(), {
            "intent": "confirm", "confirmation": token + "x",
        }).status_code, 409)
        group = self._create()
        self.assertEqual(self.client.post(self._url(group), {
            "intent": "confirm", "confirmation": token,
        }).status_code, 409)

    def test_review_conflicts_when_selected_codes_join_another_group(self):
        review = self._review()
        token = review.context["review"]["token"]
        ExamCourseEquivalencyService.create_group(
            cycle_id=self.cycle.id, name="Earlier group",
            primary_cycle_course_id=self.primary.id,
            member_ids=(self.primary.id, self.secondary.id), actor=self.admin,
        )
        stale = self.client.post(self._url(), {
            "intent": "confirm", "confirmation": token,
        })
        self.assertEqual(stale.status_code, 409)
        self.assertContains(stale, "no longer available", status_code=409)
        self.assertEqual(ExamCourseEquivalencyGroup.objects.count(), 1)

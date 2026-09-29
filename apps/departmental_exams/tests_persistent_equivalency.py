"""Saved Course intent, cycle snapshots, AJAX scope, and blueprint retention."""

import json
import re
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.academics.models import AcademicYear, CourseOffering, Section, Term
from apps.rbac.models import Permission, UserPermission
from apps.tenants.models import Program

from .exam_units import ExamCourseEquivalencyService
from .models import (
    CourseEquivalencyCyclePlan,
    CourseEquivalencyDefinition,
    CourseExamConfiguration,
    CycleCourse,
    CycleCourseOffering,
    ExamBlueprint,
    ExamBlueprintDisposition,
    ExamBlueprintRestoration,
    ExamCourseEquivalencyGroup,
    ExamCourseEquivalencyMembership,
    ExamSection,
    ExaminationCycle,
)
from .persistent_equivalency import (
    BlueprintDispositionService,
    PersistentCourseEquivalencyService,
    member_evidence,
)
from .services import CourseExamConfigurationService, ExaminationCycleService
from .setup_services import CourseSetupService
from .stage4_test_support import Stage4TestCase


class PersistentEquivalencyTests(Stage4TestCase):
    def setUp(self):
        super().setUp()
        self.cycle = self.make_cycle(status=ExaminationCycle.Status.OPEN)
        self.cycle.processing_mode = ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        self.cycle.save(update_fields=["processing_mode", "updated_at"])
        self.first = self.make_course(cycle=self.cycle, code="P-EQ-A")
        self.second = self.make_course(cycle=self.cycle, code="P-EQ-B")
        self.deadline = self.future_deadline()
        self.client.force_login(self.admin)

    def _configure(self, parent):
        return self.make_configuration(parent, deadline=self.deadline)

    def _save(self, **extra):
        data = {"label": "Shared Mathematics", "members": [self.first.course_id, self.second.course_id],
                "primary": self.first.course_id}
        data.update(extra)
        if "evidence" not in data:
            ids = set(data["members"])
            if data.get("id"):
                definition = CourseEquivalencyDefinition.objects.get(pk=data["id"])
                ids.update(definition.revisions.get(version=definition.current_version)
                           .memberships.values_list("course_id", flat=True))
            data["evidence"] = member_evidence(tenant_id=self.tenant.id, course_ids=ids)
        return self.client.post(reverse("departmental_exams:saved_equivalent_courses_save"),
                                data=json.dumps(data), content_type="application/json")

    def _definition(self):
        return PersistentCourseEquivalencyService.save_definition(
            tenant_id=self.tenant.id, actor=self.admin, label="Shared Mathematics",
            member_course_ids=[self.first.course_id, self.second.course_id],
            primary_course_id=self.first.course_id,
        )

    def _historical_review_token(self):
        landing = self.client.get(reverse("departmental_exams:saved_equivalent_courses"))
        self.assertEqual(landing.status_code, 200)
        match = re.search(r'data-review-token="([^"]+)"', landing.content.decode())
        self.assertIsNotNone(match)
        return match.group(1)

    def test_ajax_create_search_list_primary_edit_and_retire(self):
        entry = self.client.get(reverse("departmental_exams:assigned_course_examinations"))
        self.assertContains(entry, "Equivalent course codes")
        landing = self.client.get(reverse("departmental_exams:saved_equivalent_courses"))
        self.assertContains(landing, "Create equivalent group")
        search = self.client.get(reverse("departmental_exams:saved_equivalent_courses_search"),
                                 {"q": self.first.course.code[:5]})
        self.assertEqual(search.status_code, 200)
        self.assertIn(self.first.course_id, [row["id"] for row in search.json()["courses"]])
        saved = self._save()
        self.assertEqual(saved.status_code, 200)
        self.assertTrue(saved.json()["ok"])
        self.assertContains(self.client.get(reverse("departmental_exams:saved_equivalent_courses")),
                            self.second.course.code)
        definition = CourseEquivalencyDefinition.objects.get()
        reviewed_evidence = saved.json()["definitions"][0]["evidence"]
        self.second.course.title = "Changed after AJAX review"
        self.second.course.save(update_fields=["title", "updated_at"])
        stale_course = self._save(id=definition.id, version=1, evidence=reviewed_evidence)
        self.assertEqual(stale_course.status_code, 409)
        self.assertEqual(definition.revisions.count(), 1)
        self.assertEqual(definition.revisions.get(version=1).memberships.get(
            course=self.second.course).title_snapshot, "Stage 4 Course")
        self.second.course.title = "Stage 4 Course"
        self.second.course.save(update_fields=["title", "updated_at"])
        changed = self._save(id=definition.id, version=1, primary=self.second.course_id,
                             label="Shared Mathematics revised")
        self.assertEqual(changed.status_code, 200)
        definition.refresh_from_db()
        self.assertEqual(definition.current_version, 2)
        self.assertEqual(definition.revisions.get(version=1).primary_course_id, self.first.course_id)
        self.assertEqual(definition.revisions.get(version=2).primary_course_id, self.second.course_id)
        with self.assertRaises(ValidationError):
            definition.revisions.filter(version=1).update(label="Tampered history")
        stale = self._save(id=definition.id, version=1)
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(definition.revisions.count(), 2)
        retired = self.client.post(reverse("departmental_exams:saved_equivalent_courses_retire"),
                                   data=json.dumps({"id": definition.id, "version": 2,
                                                    "reason": "These courses are no longer equivalent."}),
                                   content_type="application/json")
        self.assertEqual(retired.status_code, 200)
        definition.refresh_from_db()
        self.assertFalse(definition.is_active)
        self.assertEqual(definition.revisions.count(), 2)

    def test_ajax_field_validation_and_tenant_scope(self):
        bad = self._save(members=[self.first.course_id, self.first.course_id], primary=self.second.course_id)
        self.assertEqual(bad.status_code, 400)
        self.assertIn("members", bad.json()["errors"])
        foreign = self.make_course(cycle=self.cycle, code="P-EQ-C")
        foreign.course.tenant = self.other_tenant
        foreign.course.save(update_fields=["tenant", "updated_at"])
        denied = self._save(members=[self.first.course_id, foreign.course_id])
        self.assertEqual(denied.status_code, 400)
        self.assertFalse(CourseEquivalencyDefinition.objects.exists())

    def test_ajax_stale_offering_campus_rejects_without_new_revision(self):
        saved = self._save()
        self.assertEqual(saved.status_code, 200)
        definition = CourseEquivalencyDefinition.objects.get()
        reviewed_evidence = saved.json()["definitions"][0]["evidence"]
        program = Program.objects.create(tenant=self.tenant, campus=self.other_campus,
                                         department=self.other_department, code="P-EQ-NEW", name="New campus")
        section = Section.objects.create(tenant=self.tenant, campus=self.other_campus,
                                         department=self.other_department, program=program,
                                         code="P-EQ-NEW", name="New section")
        old = self.second.offering_snapshots.select_related("offering").first().offering
        CourseOffering.objects.create(
            tenant=self.tenant, campus=self.other_campus, department=self.other_department,
            program=program, academic_year=old.academic_year, term=old.term,
            course=self.second.course, section=section,
        )
        stale = self._save(id=definition.id, version=1, evidence=reviewed_evidence)
        self.assertEqual(stale.status_code, 409)
        definition.refresh_from_db()
        self.assertEqual(definition.current_version, 1)
        self.assertEqual(definition.revisions.count(), 1)

    def test_direct_deny_hides_saved_group_and_search_member(self):
        definition = self._definition()
        manager = self.make_user("saved-global", self.department, ("admin_portal.access",))
        permission = Permission.objects.get(code="departmental_exams.manage_exam_generation")
        UserPermission.objects.create(user=manager, permission=permission,
                                      grant_type=UserPermission.GrantType.ALLOW,
                                      tenant=None, campus=None)
        UserPermission.objects.create(user=manager, permission=permission,
                                      grant_type=UserPermission.GrantType.DENY,
                                      tenant=self.tenant, campus=self.campus)
        self.client.force_login(manager)
        page = self.client.get(reverse("departmental_exams:saved_equivalent_courses"))
        self.assertEqual(page.status_code, 403)
        search = self.client.get(reverse("departmental_exams:saved_equivalent_courses_search"),
                                 {"q": self.first.course.code})
        self.assertEqual(search.status_code, 403)
        self.assertEqual(self._save().status_code, 403)
        self.assertTrue(CourseEquivalencyDefinition.objects.filter(pk=definition.pk).exists())

    def test_partial_campus_manager_cannot_view_or_change_complete_group(self):
        program = Program.objects.create(tenant=self.tenant, campus=self.other_campus,
                                         department=self.other_department, code="P-EQ-N", name="North")
        section = Section.objects.create(tenant=self.tenant, campus=self.other_campus,
                                         department=self.other_department, program=program,
                                         code="P-EQ-N", name="North section")
        CourseOffering.objects.create(
            tenant=self.tenant, campus=self.other_campus, department=self.other_department,
            program=program, academic_year=self.cycle.academic_year, term=self.cycle.term,
            course=self.second.course, section=section,
        )
        self._definition()
        partial = self.make_user("saved-partial", self.department,
                                 ("admin_portal.access", "departmental_exams.manage_exam_generation"))
        self.client.force_login(partial)
        page = self.client.get(reverse("departmental_exams:saved_equivalent_courses"))
        self.assertNotContains(page, "Shared Mathematics")
        search = self.client.get(reverse("departmental_exams:saved_equivalent_courses_search"),
                                 {"q": self.second.course.code})
        self.assertEqual(search.json()["courses"], [])
        self.assertEqual(self._save().status_code, 403)

    def _future_cycle(self, *, with_second_campus=False):
        year = AcademicYear.objects.create(tenant=self.tenant, code="P-EQ-AY", name="Future year",
                                           start_date="2027-06-01", end_date="2028-05-31")
        term = Term.objects.create(tenant=self.tenant, academic_year=year,
                                   code="P-EQ-T", name="Future term")
        for parent in (self.first, self.second):
            old = parent.offering_snapshots.select_related("offering").first().offering
            CourseOffering.objects.create(
                tenant=self.tenant, campus=old.campus, department=old.department,
                program=old.program, academic_year=year, term=term,
                course=parent.course, section=old.section,
            )
        if with_second_campus:
            program = Program.objects.create(tenant=self.tenant, campus=self.other_campus,
                                             department=self.other_department, code="P-EQ-SNAP", name="Snapshot campus")
            section = Section.objects.create(tenant=self.tenant, campus=self.other_campus,
                                             department=self.other_department, program=program,
                                             code="P-EQ-SNAP", name="Snapshot section")
            CourseOffering.objects.create(
                tenant=self.tenant, campus=self.other_campus, department=self.other_department,
                program=program, academic_year=year, term=term,
                course=self.second.course, section=section,
            )
        cycle = ExaminationCycleService.create_cycle(
            user=self.admin, tenant=self.tenant, academic_year=year, term=term,
            exam_period=ExaminationCycle.ExamPeriod.MIDTERM,
        )
        cycle.status = ExaminationCycle.Status.OPEN
        cycle.save(update_fields=["status", "updated_at"])
        return cycle

    def test_new_cycle_snapshots_version_and_applies_before_blueprints(self):
        definition = self._definition()
        future = self._future_cycle()
        plan = CourseEquivalencyCyclePlan.objects.get(cycle=future, definition=definition)
        self.assertEqual(plan.revision.version, 1)
        children = list(CycleCourse.objects.filter(cycle=future).order_by("course_id"))
        self.assertEqual(len(children), 2)
        blocked = PersistentCourseEquivalencyService.ensure_for_course(cycle_course=children[0], actor=self.admin)
        self.assertEqual(blocked.status, blocked.Status.BLOCKED)
        self.assertIn("configuration", blocked.reason)
        for child in children:
            self._configure(child)
        applied = PersistentCourseEquivalencyService.ensure_for_course(cycle_course=children[0], actor=self.admin)
        self.assertEqual(applied.status, applied.Status.APPLIED)
        self.assertEqual(set(applied.applied_group.memberships.values_list("cycle_course_id", flat=True)),
                         {child.id for child in children})
        self.assertFalse(ExamBlueprint.objects.filter(cycle_course__in=children).exists())
        PersistentCourseEquivalencyService.save_definition(
            tenant_id=self.tenant.id, actor=self.admin, label="New future intent",
            member_course_ids=[self.first.course_id, self.second.course_id],
            primary_course_id=self.second.course_id,
            definition_id=definition.id, expected_version=1,
        )
        plan.refresh_from_db()
        self.assertEqual(plan.revision.version, 1)
        self.assertEqual(plan.applied_group.primary_cycle_course.course_id, self.first.course_id)

    def test_ajax_apply_reports_blocker_then_applies_compatible_plan(self):
        self._definition()
        future = self._future_cycle()
        plan = CourseEquivalencyCyclePlan.objects.get(cycle=future)
        url = reverse("departmental_exams:saved_equivalent_courses_apply")
        payload = json.dumps({"cycle_id": future.id, "plan_id": plan.id})
        blocked = self.client.post(url, data=payload, content_type="application/json")
        self.assertEqual(blocked.status_code, 200)
        self.assertIn("Needs correction", blocked.json()["notice"])
        for child in CycleCourse.objects.filter(cycle=future):
            self._configure(child)
        applied = self.client.post(url, data=payload, content_type="application/json")
        self.assertEqual(applied.status_code, 200)
        plan.refresh_from_db()
        self.assertEqual(plan.status, plan.Status.APPLIED)
        self.assertEqual(ExamCourseEquivalencyGroup.objects.filter(cycle=future, is_active=True).count(), 1)

    def test_incompatible_member_settings_block_plan_and_structure(self):
        from .blueprint_services import BlueprintMutationService
        self._definition()
        future = self._future_cycle()
        members = list(CycleCourse.objects.filter(cycle=future).order_by("pk"))
        self._configure(members[0])
        different = self._configure(members[1])
        different.final_item_count = 60
        different.save(update_fields=["final_item_count", "updated_at"])
        plan = PersistentCourseEquivalencyService.ensure_for_course(
            cycle_course=members[0], actor=self.admin,
        )
        self.assertEqual(plan.status, plan.Status.BLOCKED)
        self.assertFalse(ExamCourseEquivalencyGroup.objects.filter(cycle=future).exists())
        with self.assertRaises(ValidationError):
            BlueprintMutationService.save_structure(
                cycle_course_id=members[0].id, tenant_id=self.tenant.id,
                actor=self.admin, expected_revision=0,
                mode=ExamBlueprint.Mode.NO_SECTIONS, sections=[],
            )
        self.assertFalse(ExamBlueprint.objects.filter(cycle_course__in=members).exists())

    def test_reasoned_cycle_exception_preserves_plan_without_group(self):
        definition = self._definition()
        future = self._future_cycle()
        plan = CourseEquivalencyCyclePlan.objects.get(cycle=future, definition=definition)
        landing = self.client.get(reverse("departmental_exams:saved_equivalent_courses"),
                                  {"cycle_id": future.id})
        self.assertContains(landing, "Application in this cycle")
        response = self.client.post(reverse("departmental_exams:saved_equivalent_courses_exception"),
                                    data=json.dumps({"cycle_id": future.id, "plan_id": plan.id,
                                                     "reason": "This cycle uses separate approved examinations."}),
                                    content_type="application/json")
        self.assertEqual(response.status_code, 200)
        plan.refresh_from_db()
        self.assertEqual(plan.status, plan.Status.EXCEPTED)
        self.assertIsNone(plan.applied_group_id)
        self.assertEqual(plan.revision.version, 1)
        self.assertFalse(ExamCourseEquivalencyGroup.objects.filter(cycle=future).exists())

    def test_exception_rechecks_deactivated_offering_snapshot_and_direct_deny(self):
        self._definition()
        future = self._future_cycle(with_second_campus=True)
        plan = CourseEquivalencyCyclePlan.objects.get(cycle=future)
        before = (plan.status, plan.reason, plan.updated_at, plan.applied_group_id)
        snapshot = CycleCourse.objects.get(cycle=future, course=self.second.course).offering_snapshots.get(
            campus=self.other_campus,
        )
        offering = snapshot.offering
        offering.is_active = False
        offering.save(update_fields=["is_active", "updated_at"])
        manager = self.make_user("snapshot-denied-manager", self.department, ("admin_portal.access",))
        permission = Permission.objects.get(code="departmental_exams.manage_exam_generation")
        UserPermission.objects.create(user=manager, permission=permission,
                                      grant_type=UserPermission.GrantType.ALLOW,
                                      tenant=None, campus=None)
        UserPermission.objects.create(user=manager, permission=permission,
                                      grant_type=UserPermission.GrantType.DENY,
                                      tenant=self.tenant, campus=self.other_campus)
        self.client.force_login(manager)
        response = self.client.post(reverse("departmental_exams:saved_equivalent_courses_exception"),
                                    data=json.dumps({"cycle_id": future.id, "plan_id": plan.id,
                                                     "reason": "This cycle needs separate course examinations."}),
                                    content_type="application/json")
        self.assertEqual(response.status_code, 403)
        plan.refresh_from_db()
        self.assertEqual((plan.status, plan.reason, plan.updated_at, plan.applied_group_id), before)
        self.assertIsNone(plan.exception_by_id)
        self.assertFalse(ExamBlueprintRestoration.objects.exists())

    def test_compatible_draft_save_applies_plan_and_blocks_independent_structure(self):
        from .blueprint_services import BlueprintMutationService
        self._definition()
        future = self._future_cycle()
        children = list(CycleCourse.objects.filter(cycle=future).order_by("pk"))
        with self.assertRaises(ValidationError):
            BlueprintMutationService.save_structure(
                cycle_course_id=children[0].id, tenant_id=self.tenant.id,
                actor=self.admin, expected_revision=0,
                mode=ExamBlueprint.Mode.NO_SECTIONS, sections=[],
            )
        self.assertFalse(ExamBlueprint.objects.filter(cycle_course__in=children).exists())
        first_config = self._configure(children[0])
        second_config = self._configure(children[1])
        first_config.coverage = "Aligned outcomes"
        first_config.save(update_fields=["coverage", "updated_at"])
        CourseExamConfigurationService.save_course_draft(
            cycle_course_id=children[1].id, tenant_id=self.tenant.id,
            user=self.admin, expected_revision=second_config.revision,
            final_item_count=50, questions_required_per_faculty=50,
            final_item_count_mode="OVERRIDE", questions_required_per_faculty_mode="OVERRIDE",
            coverage="Aligned outcomes", coverage_mode="OVERRIDE",
            additional_instructions="", contribution_deadline=self.deadline,
            contribution_deadline_mode="OVERRIDE",
        )
        plan = CourseEquivalencyCyclePlan.objects.get(cycle=future)
        self.assertEqual(plan.status, plan.Status.APPLIED)
        self.assertEqual(plan.applied_group.memberships.filter(active_marker=1).count(), 2)

    def test_retired_applied_group_blocks_independent_exam_until_exception(self):
        from .setup_services import automatic_structure_blockers
        self._definition()
        future = self._future_cycle()
        children = list(CycleCourse.objects.filter(cycle=future))
        for child in children:
            self._configure(child)
        plan = PersistentCourseEquivalencyService.ensure_for_course(
            cycle_course=children[0], actor=self.admin,
        )
        self.assertEqual(plan.status, plan.Status.APPLIED)
        group_id = plan.applied_group_id
        ExamCourseEquivalencyService.retire_group(
            group_id=group_id, actor=self.admin,
            reason="The course mapping was wrong for this cycle.",
        )
        self.assertIn("applied saved equivalent group changed",
                      automatic_structure_blockers(children[0])[0])
        blocked = PersistentCourseEquivalencyService.ensure_for_course(
            cycle_course=children[0], actor=self.admin,
        )
        self.assertEqual(blocked.status, blocked.Status.BLOCKED)
        PersistentCourseEquivalencyService.record_exception(
            cycle_id=future.id, plan_id=blocked.id, actor=self.admin,
            reason="Use the corrected separate course structures this cycle.",
        )
        self.assertFalse(ExamCourseEquivalencyGroup.objects.get(pk=group_id).is_active)

    def test_historical_midterm_requires_explicit_adoption(self):
        for parent in (self.first, self.second):
            self._configure(parent)
        group = ExamCourseEquivalencyService.create_group(
            cycle_id=self.cycle.id, name="Historical shared exam",
            primary_cycle_course_id=self.first.id,
            member_ids=(self.first.id, self.second.id), actor=self.admin,
        )
        self.assertFalse(CourseEquivalencyDefinition.objects.exists())
        landing = self.client.get(reverse("departmental_exams:saved_equivalent_courses"))
        self.assertContains(landing, "Historical shared exam")
        token = self._historical_review_token()
        adopted = self.client.post(reverse("departmental_exams:saved_equivalent_courses_adopt"),
                                   data=json.dumps({"group_id": group.id,
                                                    "review_token": token,
                                                    "reason": "Reviewed the exact historical members."}),
                                   content_type="application/json")
        self.assertEqual(adopted.status_code, 200, adopted.content.decode())
        self.assertEqual(CourseEquivalencyDefinition.objects.count(), 1)
        self.assertEqual(group.memberships.filter(active_marker=1).count(), 2)

    def test_historical_adoption_stale_primary_returns_fresh_409_without_definition(self):
        for parent in (self.first, self.second):
            self._configure(parent)
        group = ExamCourseEquivalencyService.create_group(
            cycle_id=self.cycle.id, name="Historical primary review",
            primary_cycle_course_id=self.first.id,
            member_ids=(self.first.id, self.second.id), actor=self.admin,
        )
        token = self._historical_review_token()
        ExamCourseEquivalencyService.replace_members(
            group_id=group.id, primary_cycle_course_id=self.second.id,
            member_ids=(self.first.id, self.second.id), actor=self.admin,
        )
        response = self.client.post(reverse("departmental_exams:saved_equivalent_courses_adopt"),
                                    data=json.dumps({"group_id": group.id, "review_token": token,
                                                     "reason": "Reviewed these historical codes."}),
                                    content_type="application/json")
        self.assertEqual(response.status_code, 409)
        self.assertIn("Primary: " + self.second.course.code, response.json()["html"])
        self.assertFalse(CourseEquivalencyDefinition.objects.exists())

    def test_historical_adoption_stale_members_returns_fresh_409_without_definition(self):
        for parent in (self.first, self.second):
            self._configure(parent)
        group = ExamCourseEquivalencyService.create_group(
            cycle_id=self.cycle.id, name="Historical member review",
            primary_cycle_course_id=self.first.id,
            member_ids=(self.first.id, self.second.id), actor=self.admin,
        )
        token = self._historical_review_token()
        third = self.make_course(cycle=self.cycle, code="P-EQ-NEW-MEMBER")
        self._configure(third)
        ExamCourseEquivalencyService.replace_members(
            group_id=group.id, primary_cycle_course_id=self.first.id,
            member_ids=(self.first.id, third.id), actor=self.admin,
        )
        response = self.client.post(reverse("departmental_exams:saved_equivalent_courses_adopt"),
                                    data=json.dumps({"group_id": group.id, "review_token": token,
                                                     "reason": "Reviewed these historical codes."}),
                                    content_type="application/json")
        self.assertEqual(response.status_code, 409)
        self.assertIn(third.course.code, response.json()["html"])
        self.assertIn(self.second.course.code, response.json()["html"])
        self.assertIn("historical member", response.json()["html"])
        self.assertFalse(CourseEquivalencyDefinition.objects.exists())

    def test_secondary_blueprint_retained_with_sections_and_read_guard(self):
        for parent in (self.first, self.second):
            self._configure(parent)
        blueprints = []
        for parent in (self.first, self.second):
            blueprint = ExamBlueprint.objects.create(
                cycle_course=parent, mode=ExamBlueprint.Mode.USE_SECTIONS,
                created_by=self.admin, updated_by=self.admin,
            )
            for index, quota in enumerate((10, 40), start=1):
                ExamSection.objects.create(blueprint=blueprint, display_order=index,
                                           title=f"Part {index}", instructions="Same instructions",
                                           item_quota=quota)
            blueprints.append(blueprint)
        disposition = BlueprintDispositionService.retain_secondary(
            cycle_id=self.cycle.id, primary_cycle_course_id=self.first.id,
            secondary_cycle_course_id=self.second.id, actor=self.admin,
            reason="Retain the duplicate structure for audit history.",
            expected_primary_revision=1, expected_secondary_revision=1,
        )
        self.assertEqual(disposition.primary_blueprint_id, blueprints[0].id)
        self.assertEqual(ExamSection.objects.filter(blueprint=blueprints[1]).count(), 2)
        with self.assertRaises(ValidationError):
            blueprints[1].mode = ExamBlueprint.Mode.NO_SECTIONS
            blueprints[1].save(update_fields=["mode"])
        group = ExamCourseEquivalencyService.create_group(
            cycle_id=self.cycle.id, name="Recovered exam",
            primary_cycle_course_id=self.first.id,
            member_ids=(self.first.id, self.second.id), actor=self.admin,
        )
        self.assertEqual(group.primary_cycle_course_id, self.first.id)
        self.assertEqual(ExamBlueprintDisposition.objects.count(), 1)
        from .blueprint_services import _lock_stage6_blueprint
        self.assertEqual(_lock_stage6_blueprint(cycle_course=self.second).pk, blueprints[0].pk)

    def _retained_future_structures(self, *, apply):
        self._definition()
        future = self._future_cycle()
        members = list(CycleCourse.objects.filter(cycle=future).order_by("course_id"))
        for member in members:
            self._configure(member)
            ExamBlueprint.objects.create(cycle_course=member, mode=ExamBlueprint.Mode.NO_SECTIONS,
                                         created_by=self.admin, updated_by=self.admin)
        plan = CourseEquivalencyCyclePlan.objects.get(cycle=future)
        disposition = BlueprintDispositionService.retain_secondary(
            cycle_id=future.id, primary_cycle_course_id=members[0].id,
            secondary_cycle_course_id=members[1].id, actor=self.admin,
            reason="Retain the duplicate until this mapping is settled.",
            expected_primary_revision=1, expected_secondary_revision=1,
            plan_id=plan.id,
        )
        if apply:
            plan = PersistentCourseEquivalencyService.ensure_for_course(
                cycle_course=members[0], actor=self.admin,
            )
            self.assertEqual(plan.status, plan.Status.APPLIED)
        return future, members, plan, disposition

    def test_apply_retire_exception_restores_secondary_and_blueprint_edit(self):
        from .blueprint_services import BlueprintMutationService
        future, members, plan, disposition = self._retained_future_structures(apply=True)
        group = plan.applied_group
        with self.assertRaises(ValidationError):
            ExamCourseEquivalencyService.replace_members(
                group_id=group.id, primary_cycle_course_id=members[1].id,
                member_ids=[member.id for member in members], actor=self.admin,
            )
        ExamCourseEquivalencyService.retire_group(
            group_id=group.id, actor=self.admin,
            reason="Separate the two courses after reviewing the mapping.",
        )
        restoration = ExamBlueprintRestoration.objects.get(disposition=disposition)
        self.assertEqual(restoration.retired_group_id, group.id)
        self.assertTrue(ExamBlueprintDisposition.objects.filter(pk=disposition.pk).exists())
        landing = self.client.get(reverse("departmental_exams:saved_equivalent_courses"),
                                  {"cycle_id": future.id})
        self.assertContains(landing, f'data-equivalency-action="exception" data-id="{plan.id}"')
        exception = self.client.post(
            reverse("departmental_exams:saved_equivalent_courses_exception"),
            data=json.dumps({"cycle_id": future.id, "plan_id": plan.id,
                             "reason": "These courses require separate structures in this cycle."}),
            content_type="application/json",
        )
        self.assertEqual(exception.status_code, 200, exception.content.decode())
        plan.refresh_from_db()
        self.assertEqual(plan.status, plan.Status.EXCEPTED)
        self.assertEqual(ExamBlueprint.active_objects.filter(cycle_course=members[1]).get().pk,
                         disposition.blueprint_id)
        page = self.client.get(reverse("departmental_exams:blueprint_configuration", args=[members[1].id]))
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["blueprint"].pk, disposition.blueprint_id)
        member = CycleCourse.objects.get(pk=members[1].id)
        CourseSetupService.classify(
            course_id=member.id, tenant_id=self.tenant.id, actor=self.admin,
            classification="DEPARTMENTAL",
            expected_state=CourseSetupService.fingerprint(member),
        )
        blueprint, changed = BlueprintMutationService.save_structure(
            cycle_course_id=members[1].id, tenant_id=self.tenant.id,
            actor=self.admin, expected_revision=1,
            mode=ExamBlueprint.Mode.USE_SECTIONS,
            sections=[{"title": "Part 1", "instructions": "", "display_order": 1, "item_quota": 10},
                      {"title": "Part 2", "instructions": "", "display_order": 2, "item_quota": 40}],
        )
        self.assertTrue(changed)
        self.assertEqual(blueprint.pk, disposition.blueprint_id)
        self.assertGreater(blueprint.revision, 1)

    def test_unsafe_retirement_keeps_group_plan_and_disposition_unchanged(self):
        future, members, plan, disposition = self._retained_future_structures(apply=True)
        primary = ExamBlueprint.objects.get(cycle_course=members[0])
        primary.mode = ExamBlueprint.Mode.USE_SECTIONS
        primary.save(update_fields=["mode", "updated_at"])
        with self.assertRaises(ValidationError):
            ExamCourseEquivalencyService.retire_group(
                group_id=plan.applied_group_id, actor=self.admin,
                reason="Separate these courses after reviewing the mapping.",
            )
        plan.refresh_from_db()
        self.assertEqual(plan.status, plan.Status.APPLIED)
        self.assertTrue(ExamCourseEquivalencyGroup.objects.get(pk=plan.applied_group_id).is_active)
        self.assertEqual(ExamCourseEquivalencyMembership.objects.filter(
            group_id=plan.applied_group_id, active_marker=1,
        ).count(), 2)
        self.assertFalse(ExamBlueprintRestoration.objects.filter(disposition=disposition).exists())
        with self.assertRaises(ValidationError):
            PersistentCourseEquivalencyService.record_exception(
                cycle_id=future.id, plan_id=plan.id, actor=self.admin,
                reason="Separate these courses after reviewing the mapping.",
            )
        plan.refresh_from_db()
        self.assertEqual(plan.status, plan.Status.APPLIED)

    def test_pending_exception_restores_retained_structure_atomically(self):
        future, members, plan, disposition = self._retained_future_structures(apply=False)
        PersistentCourseEquivalencyService.record_exception(
            cycle_id=future.id, plan_id=plan.id, actor=self.admin,
            reason="The members need separate examinations this cycle.",
        )
        plan.refresh_from_db()
        self.assertEqual(plan.status, plan.Status.EXCEPTED)
        self.assertTrue(ExamBlueprintRestoration.objects.filter(disposition=disposition).exists())
        self.assertTrue(ExamBlueprint.active_objects.filter(cycle_course=members[1]).exists())

    def test_unsafe_pending_exception_keeps_plan_and_retained_blueprint_unchanged(self):
        future, members, plan, disposition = self._retained_future_structures(apply=False)
        primary = ExamBlueprint.objects.get(cycle_course=members[0])
        primary.mode = ExamBlueprint.Mode.USE_SECTIONS
        primary.save(update_fields=["mode", "updated_at"])
        with self.assertRaises(ValidationError):
            PersistentCourseEquivalencyService.record_exception(
                cycle_id=future.id, plan_id=plan.id, actor=self.admin,
                reason="These courses need separate structures this cycle.",
            )
        plan.refresh_from_db()
        self.assertEqual(plan.status, plan.Status.PENDING)
        self.assertFalse(ExamBlueprintRestoration.objects.filter(disposition=disposition).exists())
        self.assertFalse(ExamBlueprint.active_objects.filter(cycle_course=members[1]).exists())

    def test_blueprint_recovery_rejects_processing_history(self):
        configurations = [self._configure(parent) for parent in (self.first, self.second)]
        for parent in (self.first, self.second):
            ExamBlueprint.objects.create(cycle_course=parent, mode=ExamBlueprint.Mode.NO_SECTIONS,
                                         created_by=self.admin, updated_by=self.admin)
        configurations[1].automatic_processing_status = CourseExamConfiguration.AutomaticProcessingStatus.BLOCKED
        configurations[1].save(update_fields=["automatic_processing_status", "updated_at"])
        with self.assertRaises(ValidationError):
            BlueprintDispositionService.retain_secondary(
                cycle_id=self.cycle.id, primary_cycle_course_id=self.first.id,
                secondary_cycle_course_id=self.second.id, actor=self.admin,
                reason="The duplicate structure should remain historical.",
                expected_primary_revision=1, expected_secondary_revision=1,
            )
        self.assertFalse(ExamBlueprintDisposition.objects.exists())

    def test_ajax_blueprint_recovery_review_rechecks_revision_and_applies_plan(self):
        self._definition()
        future = self._future_cycle()
        members = list(CycleCourse.objects.filter(cycle=future).order_by("course_id"))
        for member in members:
            self._configure(member)
            blueprint = ExamBlueprint.objects.create(
                cycle_course=member, mode=ExamBlueprint.Mode.USE_SECTIONS,
                created_by=self.admin, updated_by=self.admin,
            )
            for order, quota in ((1, 10), (2, 40)):
                ExamSection.objects.create(blueprint=blueprint, display_order=order,
                                           title=f"Part {order}", instructions="Same instructions",
                                           item_quota=quota)
        plan = CourseEquivalencyCyclePlan.objects.get(cycle=future)
        review = self.client.get(reverse("departmental_exams:saved_equivalent_blueprint_review"),
                                 {"cycle_id": future.id, "plan_id": plan.id})
        self.assertEqual(review.status_code, 200)
        evidence = review.json()
        self.assertEqual([row["item_quota"] for row in evidence["primary"]["sections"]], [10, 40])
        payload = {
            "cycle_id": future.id, "plan_id": plan.id,
            "primary_cycle_course_id": evidence["primary"]["cycle_course_id"],
            "secondary_cycle_course_id": evidence["secondary"]["cycle_course_id"],
            "primary_revision": evidence["primary"]["revision"],
            "secondary_revision": evidence["secondary"]["revision"] + 1,
            "primary_digest": evidence["primary"]["digest"],
            "secondary_digest": evidence["secondary"]["digest"],
            "reason": "Retain the duplicate structure for the audit trail.",
        }
        url = reverse("departmental_exams:saved_equivalent_blueprint_retain")
        stale = self.client.post(url, data=json.dumps(payload), content_type="application/json")
        self.assertEqual(stale.status_code, 409)
        self.assertFalse(ExamBlueprintDisposition.objects.exists())
        payload["secondary_revision"] -= 1
        section = ExamSection.objects.filter(blueprint_id=evidence["secondary"]["blueprint_id"]).order_by("display_order").first()
        section.instructions = "Changed after review"
        section.save(update_fields=["instructions", "updated_at"])
        stale_sections = self.client.post(url, data=json.dumps(payload), content_type="application/json")
        self.assertEqual(stale_sections.status_code, 409)
        self.assertFalse(ExamBlueprintDisposition.objects.exists())
        section.instructions = "Same instructions"
        section.save(update_fields=["instructions", "updated_at"])
        with self.assertRaises(ValidationError):
            BlueprintDispositionService.retain_secondary(
                cycle_id=future.id,
                primary_cycle_course_id=payload["secondary_cycle_course_id"],
                secondary_cycle_course_id=payload["primary_cycle_course_id"],
                actor=self.admin, reason="The reversed primary must be rejected.",
                expected_primary_revision=1, expected_secondary_revision=1,
                plan_id=plan.id,
            )
        saved = self.client.post(url, data=json.dumps(payload), content_type="application/json")
        self.assertEqual(saved.status_code, 200)
        plan.refresh_from_db()
        self.assertEqual(plan.status, plan.Status.APPLIED)
        self.assertEqual(ExamBlueprintDisposition.objects.count(), 1)

    def _existing_review(self, definition):
        return self.client.get(reverse("departmental_exams:saved_equivalent_existing_cycle_review"),
                               {"cycle_id": self.cycle.pk, "definition_id": definition.pk})

    def _existing_apply(self, definition, token):
        return self.client.post(reverse("departmental_exams:saved_equivalent_existing_cycle_apply"),
                                data=json.dumps({"cycle_id": self.cycle.pk,
                                                 "definition_id": definition.pk,
                                                 "review_token": token}), content_type="application/json")

    def _existing_ready(self, *, blueprints=0):
        for member in (self.first, self.second):
            self._configure(member)
        definition = self._definition()
        made = []
        for member in (self.first, self.second)[:blueprints]:
            blueprint = ExamBlueprint.objects.create(
                cycle_course=member, mode=ExamBlueprint.Mode.USE_SECTIONS,
                created_by=self.admin, updated_by=self.admin)
            ExamSection.objects.create(blueprint=blueprint, display_order=1,
                                       title="Common section", instructions="Reviewed", item_quota=50)
            made.append(blueprint)
        return definition, made

    def test_existing_cycle_one_blueprint_pins_and_applies(self):
        definition, blueprints = self._existing_ready(blueprints=1)
        self.assertFalse(CourseEquivalencyCyclePlan.objects.filter(cycle=self.cycle).exists())
        landing = self.client.get(reverse("departmental_exams:saved_equivalent_courses"),
                                  {"cycle_id": self.cycle.pk})
        self.assertContains(landing, 'data-existing-cycle-review="' + str(definition.pk) + '"')
        self.assertContains(landing, 'value="' + str(self.cycle.pk) + '"')
        review = self._existing_review(definition)
        self.assertEqual(review.status_code, 200)
        evidence = review.json()["evidence"]
        self.assertEqual({row["cycle_course_id"] for row in evidence["members"]},
                         {self.first.pk, self.second.pk})
        self.assertEqual(evidence["blueprints"][0]["id"], blueprints[0].pk)
        response = self._existing_apply(definition, review.json()["review_token"])
        self.assertEqual(response.status_code, 200, response.content)
        plan = CourseEquivalencyCyclePlan.objects.get(cycle=self.cycle, definition=definition)
        self.assertEqual(plan.status, plan.Status.APPLIED)
        self.assertEqual(plan.revision.version, 1)
        self.assertEqual(plan.applied_group.primary_cycle_course_id, self.first.pk)
        self.assertFalse(ExamBlueprintDisposition.objects.exists())

    def test_existing_cycle_action_requires_frozen_member_campus_authority(self):
        definition, _ = self._existing_ready()
        program = Program.objects.create(tenant=self.tenant, campus=self.other_campus,
                                         department=self.other_department, code="P-EQ-OLD-N", name="North")
        section = Section.objects.create(tenant=self.tenant, campus=self.other_campus,
                                         department=self.other_department, program=program,
                                         code="P-EQ-OLD-N", name="North section")
        offering = CourseOffering.objects.create(
            tenant=self.tenant, campus=self.other_campus, department=self.other_department,
            program=program, academic_year=self.cycle.academic_year, term=self.cycle.term,
            course=self.second.course, section=section,
        )
        CycleCourseOffering.objects.create(cycle_course=self.second, offering=offering,
                                           campus=self.other_campus)
        offering.is_active = False
        offering.save(update_fields=["is_active", "updated_at"])

        permission = Permission.objects.get(code="departmental_exams.manage_exam_generation")
        denied = self.make_user("existing-cycle-north-denied", self.department,
                                ("admin_portal.access",))
        UserPermission.objects.create(user=denied, permission=permission,
                                      grant_type=UserPermission.GrantType.ALLOW,
                                      tenant=None, campus=None)
        UserPermission.objects.create(user=denied, permission=permission,
                                      grant_type=UserPermission.GrantType.DENY,
                                      tenant=self.tenant, campus=self.other_campus)
        self.client.force_login(denied)
        landing_url = reverse("departmental_exams:saved_equivalent_courses")
        landing = self.client.get(landing_url, {"cycle_id": self.cycle.pk})
        self.assertContains(landing, definition.revisions.get(version=1).label)
        self.assertNotContains(landing, f'data-existing-cycle-review="{definition.pk}"')
        self.assertEqual(self._existing_review(definition).status_code, 403)
        self.assertFalse(CourseEquivalencyCyclePlan.objects.exists())

        authorized = self.make_user("existing-cycle-north-allowed", self.department,
                                    ("admin_portal.access",))
        UserPermission.objects.create(user=authorized, permission=permission,
                                      grant_type=UserPermission.GrantType.ALLOW,
                                      tenant=None, campus=None)
        self.client.force_login(authorized)
        self.assertContains(self.client.get(landing_url, {"cycle_id": self.cycle.pk}),
                            f'data-existing-cycle-review="{definition.pk}"')
        self.assertEqual(self._existing_review(definition).status_code, 200)

    def test_existing_cycle_two_blueprints_retains_secondary_and_sections(self):
        definition, blueprints = self._existing_ready(blueprints=2)
        review = self._existing_review(definition)
        self.assertEqual(review.status_code, 200)
        response = self._existing_apply(definition, review.json()["review_token"])
        self.assertEqual(response.status_code, 200, response.content)
        disposition = ExamBlueprintDisposition.objects.get()
        self.assertEqual((disposition.primary_blueprint_id, disposition.blueprint_id),
                         (blueprints[0].pk, blueprints[1].pk))
        self.assertEqual(ExamSection.objects.filter(blueprint__in=blueprints).count(), 2)
        self.assertEqual(CourseEquivalencyCyclePlan.objects.get(cycle=self.cycle).status,
                         CourseEquivalencyCyclePlan.Status.APPLIED)

    def test_existing_cycle_stale_review_and_duplicate_have_no_partial_changes(self):
        definition, _ = self._existing_ready()
        review = self._existing_review(definition)
        self.first.course.title = "Changed after review"
        self.first.course.save(update_fields=["title", "updated_at"])
        stale = self._existing_apply(definition, review.json()["review_token"])
        self.assertEqual(stale.status_code, 409)
        self.assertFalse(CourseEquivalencyCyclePlan.objects.exists())
        fresh = self._existing_review(definition)
        self.assertEqual(self._existing_apply(definition, fresh.json()["review_token"]).status_code, 200)
        self.assertEqual(self._existing_apply(definition, fresh.json()["review_token"]).status_code, 409)
        self.assertEqual(CourseEquivalencyCyclePlan.objects.count(), 1)
        self.assertEqual(ExamCourseEquivalencyGroup.objects.count(), 1)

    def test_existing_cycle_direct_deny_after_review_blocks_application(self):
        definition, _ = self._existing_ready()
        manager = self.make_user("existing-cycle-manager", self.department, ("admin_portal.access",))
        permission = Permission.objects.get(code="departmental_exams.manage_exam_generation")
        UserPermission.objects.create(user=manager, permission=permission,
                                      grant_type=UserPermission.GrantType.ALLOW,
                                      tenant=None, campus=None)
        self.client.force_login(manager)
        review = self._existing_review(definition)
        self.assertEqual(review.status_code, 200)
        UserPermission.objects.create(user=manager, permission=permission,
                                      grant_type=UserPermission.GrantType.DENY,
                                      tenant=self.tenant, campus=self.campus)
        self.assertEqual(self._existing_apply(definition, review.json()["review_token"]).status_code, 403)
        self.assertFalse(CourseEquivalencyCyclePlan.objects.exists())

    def test_existing_cycle_mismatched_settings_and_classification_block_review(self):
        definition, _ = self._existing_ready()
        config = CourseExamConfiguration.objects.get(cycle_course=self.second)
        config.final_item_count = 60
        config.save(update_fields=["final_item_count", "updated_at"])
        self.assertEqual(self._existing_review(definition).status_code, 409)
        config.final_item_count = 50
        config.save(update_fields=["final_item_count", "updated_at"])
        from .models import _classification_write
        token = _classification_write.set(True)
        try:
            self.second.exam_classification = CycleCourse.ExamClassification.DEPARTMENTAL
            self.second.save(update_fields=["exam_classification", "updated_at"])
        finally:
            _classification_write.reset(token)
        self.assertEqual(self._existing_review(definition).status_code, 409)
        self.assertFalse(CourseEquivalencyCyclePlan.objects.exists())

    def test_existing_cycle_opened_intake_and_unsafe_disposition_block(self):
        definition, blueprints = self._existing_ready(blueprints=2)
        review = self._existing_review(definition)
        section = ExamSection.objects.get(blueprint=blueprints[1])
        section.instructions = "Changed section"
        section.save(update_fields=["instructions", "updated_at"])
        self.assertEqual(self._existing_apply(definition, review.json()["review_token"]).status_code, 409)
        self.assertFalse(ExamBlueprintDisposition.objects.exists())
        section.instructions = "Reviewed"
        section.save(update_fields=["instructions", "updated_at"])
        config = CourseExamConfiguration.objects.get(cycle_course=self.second)
        config.workflow_status = config.WorkflowStatus.OPEN
        config.save(update_fields=["workflow_status", "updated_at"])
        self.assertEqual(self._existing_review(definition).status_code, 409)
        self.assertFalse(CourseEquivalencyCyclePlan.objects.exists())

    def test_existing_cycle_mismatched_blueprints_and_processing_block_review(self):
        definition, blueprints = self._existing_ready(blueprints=2)
        section = ExamSection.objects.get(blueprint=blueprints[1])
        section.item_quota = 49
        section.save(update_fields=["item_quota", "updated_at"])
        self.assertEqual(self._existing_review(definition).status_code, 409)
        section.item_quota = 50
        section.save(update_fields=["item_quota", "updated_at"])
        config = CourseExamConfiguration.objects.get(cycle_course=self.first)
        config.automatic_processing_status = config.AutomaticProcessingStatus.BLOCKED
        config.save(update_fields=["automatic_processing_status", "updated_at"])
        self.assertEqual(self._existing_review(definition).status_code, 409)
        self.assertFalse(CourseEquivalencyCyclePlan.objects.exists())

    def test_existing_cycle_signed_review_is_actor_bound(self):
        definition, _ = self._existing_ready()
        review = self._existing_review(definition)
        manager = self.make_user("existing-other-manager", self.department,
                                 ("admin_portal.access", "departmental_exams.manage_exam_generation"))
        self.client.force_login(manager)
        self.assertEqual(self._existing_apply(definition, review.json()["review_token"]).status_code, 409)
        self.assertFalse(CourseEquivalencyCyclePlan.objects.exists())

    def test_existing_cycle_group_failure_rolls_back_plan_and_disposition(self):
        definition, _ = self._existing_ready(blueprints=2)
        review = self._existing_review(definition)
        with patch.object(ExamCourseEquivalencyService, "create_group",
                          side_effect=ValidationError("forced group failure")):
            response = self._existing_apply(definition, review.json()["review_token"])
        self.assertEqual(response.status_code, 409)
        self.assertFalse(CourseEquivalencyCyclePlan.objects.exists())
        self.assertFalse(ExamBlueprintDisposition.objects.exists())
        self.assertFalse(ExamCourseEquivalencyGroup.objects.exists())

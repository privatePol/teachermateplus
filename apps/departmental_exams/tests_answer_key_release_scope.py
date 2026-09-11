from types import SimpleNamespace

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.exceptions import IrreversibleError
from django.db.migrations.executor import MigrationExecutor
from django.test import Client
from django.http import Http404
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import CourseOffering, FacultyAssignment, Section
from apps.auditlog.models import AuditLog
from apps.rbac.models import Permission, UserPermission
from apps.tenants.models import Campus, Department, Program

from .answer_key_release import AnswerKeyReleaseService, FacultyAnswerKeyReleaseService, AnswerKeyViewerReportService
from .exam_units import ExamCourseEquivalencyService
from .models import AnswerKeyRelease, CycleCourseOffering, ExamGenerationRevision, FacultyContribution, FacultyContributionEligibilitySource
from .stage4_test_support import Stage4TransactionTestCase
from .tests_answer_key_release import AnswerKeyReleaseFixture


class AnswerKeyScopeTests(AnswerKeyReleaseFixture):
    def setUp(self):
        super().setUp()
        self.third = Campus.objects.create(tenant=self.tenant, code="SOUTH", name="South")
        self.campuses = (self.campus, self.other_campus, self.third)
        self.second = self.make_course(cycle=self.parent.cycle, department=None, code="KEY-202")
        self.make_configuration(self.second, workflow=self.configuration.workflow_status,
                                deadline=self.configuration.contribution_deadline)
        self.r_second = self._make_revision(1, cycle_course=self.second)
        self.offerings = {}
        for course in (self.parent, self.second):
            self.offerings[course.id, self.campus.id] = course.offering_snapshots.get().offering
            for campus in self.campuses[1:]:
                department, _ = Department.objects.get_or_create(
                    tenant=self.tenant, campus=campus, code="SCOPE", defaults={"name": "Scope"})
                program = Program.objects.create(tenant=self.tenant, campus=campus, department=department,
                                                 code=f"P{course.id}-{campus.id}", name="Scope")
                section = Section.objects.create(tenant=self.tenant, campus=campus, department=department,
                                                 program=program, code=f"S{course.id}-{campus.id}", name="Scope")
                offering = CourseOffering.objects.create(
                    tenant=self.tenant, campus=campus, department=department, program=program, section=section,
                    course=course.course, academic_year=course.cycle.academic_year, term=course.cycle.term)
                CycleCourseOffering.objects.create(cycle_course=course, offering=offering, campus=campus)
                self.offerings[course.id, campus.id] = offering
        for campus in self.campuses[1:]:
            self.grant(self.release_manager, "departmental_exams.release_answer_keys", campus)
        self.start = timezone.now() - timezone.timedelta(minutes=1)
        self.end = self.start + timezone.timedelta(hours=2)

    def grant(self, user, code, campus, deny=False):
        return UserPermission.objects.update_or_create(user=user, permission=Permission.objects.get(code=code),
                                             tenant=self.tenant, campus=campus, defaults={"grant_type": "DENY" if deny else "ALLOW"})[0]

    def assign(self, user, course, campus):
        self.grant(user, "faculty_portal.access", campus)
        assignment = FacultyAssignment.objects.create(
            tenant=self.tenant, campus=campus, offering=self.offerings[course.id, campus.id], faculty_user=user,
            accepted_by=user, response_status="ACCEPTED", responded_at=timezone.now(), accepted_at=timezone.now())
        contribution, _ = FacultyContribution.objects.get_or_create(
            cycle_course=course, faculty_user=user,
            defaults={"source_assignment": assignment, "source_campus": campus, "quota_snapshot": 50,
                      "configuration_revision_snapshot": 1, "status": "SUBMITTED", "submitted_at": timezone.now()})
        FacultyContributionEligibilitySource.objects.create(
            contribution=contribution, assignment=assignment, assignment_id_snapshot=assignment.id,
            offering_id_snapshot=assignment.offering_id, tenant_id_snapshot=self.tenant.id,
            campus_id_snapshot=campus.id)
        return contribution, assignment

    def release_target(self, course=None, campus=None, **kwargs):
        course = course or self.parent
        values = dict(cycle_course_id=course.id, revision_id=self.r4.id if course == self.parent else self.r_second.id,
                      recipient_course_id=course.id, target_campus_id=(campus or self.campus).id,
                      tenant_id=self.tenant.id, actor=self.release_manager,
                      available_from=self.start, available_until=self.end, attestation_confirmed=True)
        values.update(kwargs)
        return AnswerKeyReleaseService.release(**values)

    def options(self, contribution):
        return FacultyAnswerKeyReleaseService.available_options(contributions=(contribution,)).get(contribution.id, [])

    def assert_access(self, contribution, release, allowed):
        client = Client()
        client.force_login(contribution.faculty_user)
        for name in ("faculty_answer_key", "faculty_answer_key_print", "faculty_checking_master_print"):
            for code in ("A", "B"):
                response = client.get(reverse("departmental_exams:" + name, args=(contribution.id, release.id, code)))
                self.assertEqual(response.status_code, 200 if allowed else 403, (name, code))
                if allowed:
                    self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(any(o["release_id"] == release.id for o in self.options(contribution)), allowed)

    def test_three_campuses_two_courses_listing_and_all_direct_outputs(self):
        release = self.release_target()
        self.assert_access(self.contribution, release, True)
        for index, campus in enumerate(self.campuses):
            faculty = self.make_user(f"scope-faculty-{index}", None, ("faculty_portal.access",), campus=campus)
            first, _ = self.assign(faculty, self.parent, campus)
            second, _ = self.assign(faculty, self.second, campus)
            self.assert_access(first, release, campus == self.campus)
            self.assert_access(second, release, False)

    def test_multi_campus_matching_uses_live_assignment_not_default(self):
        _, assignment = self.assign(self.faculty, self.parent, self.other_campus)
        release = self.release_target(campus=self.other_campus)
        self.assert_access(self.contribution, release, True)
        assignment.is_active = False
        assignment.save(update_fields=["is_active"])
        self.faculty.default_campus = self.other_campus
        self.faculty.save(update_fields=["default_campus"])
        self.assert_access(self.contribution, release, False)

    def test_independent_targets_retry_replace_revoke_windows(self):
        first = self.release_target()
        other = self.release_target(campus=self.other_campus)
        self.assertEqual(self.release_target().id, first.id)
        replacement = self.release_target(available_until=self.end + timezone.timedelta(hours=1))
        first.refresh_from_db(); other.refresh_from_db()
        self.assertEqual(first.status, "REVOKED")
        self.assertEqual(other.status, "ACTIVE")
        self.assertEqual(other.available_until, self.end)
        AnswerKeyReleaseService.revoke(release_id=replacement.id, tenant_id=self.tenant.id, actor=self.release_manager)
        other.refresh_from_db()
        self.assertEqual(other.status, "ACTIVE")
        self.assertEqual(self.options(self.contribution), [])
        future = self.release_target(available_from=self.end, available_until=self.end + timezone.timedelta(hours=1))
        self.assert_access(self.contribution, future, False)

    def test_bulk_targets_atomic_and_single_parity(self):
        kwargs = dict(tenant_id=self.tenant.id, actor=self.release_manager, available_from=self.start,
                      available_until=self.end, attestation_confirmed=True)
        selected = ((self.parent.id, self.r4.id, self.parent.id, self.campus.id),
                    (self.second.id, self.r_second.id, self.second.id, self.campus.id))
        releases = AnswerKeyReleaseService.bulk_release(selections=selected, **kwargs)
        self.assertEqual(len(releases), 2)
        self.assertEqual(self.release_target().id, releases[0].id)
        count = AnswerKeyRelease.objects.count()
        audits = AuditLog.objects.filter(entity_type="AnswerKeyRelease").count()
        with self.assertRaises(PermissionDenied):
            AnswerKeyReleaseService.bulk_release(selections=(
                (self.parent.id, self.r4.id, self.parent.id, self.campus.id),
                (self.second.id, self.r_second.id, 999999, self.campus.id)), **kwargs)
        self.assertEqual(AnswerKeyRelease.objects.count(), count)
        self.assertEqual(AuditLog.objects.filter(entity_type="AnswerKeyRelease").count(), audits)

    def test_missing_invalid_and_cross_course_scope_denied(self):
        for values in ({"target_campus_id": None}, {"recipient_course_id": None},
                       {"target_campus_id": "all"}, {"recipient_course_id": self.second.id},
                       {"revision_id": self.r_second.id}, {"tenant_id": self.other_tenant.id}):
            with self.subTest(values=values), self.assertRaises((PermissionDenied, ValidationError, Http404)) as caught:
                self.release_target(**values)
            self.assertIn(type(caught.exception).__name__, ("PermissionDenied", "ValidationError", "Http404"))
        self.assertFalse(AnswerKeyRelease.objects.exists())

    def test_target_deny_and_request_scope_cannot_be_bypassed(self):
        self.grant(self.release_manager, "departmental_exams.release_answer_keys", self.other_campus, deny=True)
        with self.assertRaises(PermissionDenied):
            self.release_target(campus=self.other_campus)
        with self.assertRaises(PermissionDenied):
            self.release_target(request=SimpleNamespace(scope={"campus_ids": [self.third.id]}))
        self.assertFalse(AnswerKeyRelease.objects.exists())

    def test_legacy_denies_every_output_and_does_not_block_reissue(self):
        legacy = AnswerKeyRelease.objects.create(
            cycle_course=self.parent, generation_revision=self.r4, scope_kind="LEGACY_UNSCOPED",
            available_from=self.start, available_until=self.end, released_by=self.release_manager,
            attestation_version="all-sessions-concluded-v1")
        self.assert_access(self.contribution, legacy, False)
        self.assertIn("Reissue", AnswerKeyReleaseService.operational_status(release=legacy, expected_revision=self.r4))
        scoped = self.release_target()
        legacy.refresh_from_db()
        self.assertEqual(legacy.status, "ACTIVE")
        self.assert_access(self.contribution, scoped, True)

    def test_scope_constraints_immutability_and_membership(self):
        release = self.release_target()
        release.target_campus = self.other_campus
        with self.assertRaises(ValidationError):
            release.save()
        with self.assertRaises(IntegrityError), transaction.atomic():
            AnswerKeyRelease.objects.filter(pk=release.id).update(target_campus=None)
        release.refresh_from_db()
        release.recipient_course = self.second
        with self.assertRaises(ValidationError):
            release.full_clean()

    def test_actual_http_payload_requires_explicit_scope_and_loads_only_target_rows(self):
        client = Client(); client.force_login(self.release_manager)
        url = reverse("departmental_exams:questionnaire_print_release")
        empty = client.get(url)
        self.assertEqual(empty.context["bulk_answer_key_rows"], [])
        response = client.get(url, {"target_campus_id": self.campus.id})
        self.assertEqual(len(response.context["bulk_answer_key_rows"]), 2)
        self.assertTrue(all(row["campus"].id == self.campus.id for row in response.context["bulk_answer_key_rows"]))
        payload = {"action": "bulk_answer_key_release", "target_campus_id": self.campus.id,
                   "selections": [f"{self.parent.id}:{self.r4.id}:{self.parent.id}:{self.campus.id}"],
                   "available_from": timezone.localtime(self.start).strftime("%Y-%m-%dT%H:%M"),
                   "available_until": timezone.localtime(self.end).strftime("%Y-%m-%dT%H:%M"), "sessions_concluded": "on"}
        for bad in ({"target_campus_id": ""}, {"target_campus_id": self.other_campus.id},
                    {"selections": [f"{self.parent.id}:{self.r4.id}"]}):
            self.assertEqual(client.post(url, {**payload, **bad}, HTTP_X_REQUESTED_WITH="XMLHttpRequest").status_code, 400)
            self.assertFalse(AnswerKeyRelease.objects.exists())
        self.assertEqual(client.post(url, payload).status_code, 302)
        self.assertEqual(AnswerKeyRelease.objects.get().target_campus_id, self.campus.id)

    def test_who_viewed_is_per_target_and_scope_metadata_is_safe(self):
        first = self.release_target()
        other = self.release_target(campus=self.other_campus)
        client = self._faculty_client(); client.get(self._url(first))
        _, first_rows = AnswerKeyViewerReportService.report(release_id=first.id, tenant_id=self.tenant.id, actor=self.release_manager)
        _, other_rows = AnswerKeyViewerReportService.report(release_id=other.id, tenant_id=self.tenant.id, actor=self.release_manager)
        self.assertEqual(len(first_rows), 1); self.assertEqual(other_rows, ())
        event = AuditLog.objects.get(action="DE_FACULTY_ANSWER_KEY_SET_VIEWED", entity_id=str(first.id))
        self.assertEqual(event.metadata_json["target_campus_id"], self.campus.id)
        self.assertEqual(event.metadata_json["recipient_course_id"], self.parent.id)
        self.assertNotIn("correct_answer", str(event.metadata_json))

    def test_revoke_payload_cannot_substitute_another_campus_release(self):
        first = self.release_target()
        other = self.release_target(campus=self.other_campus)
        client = Client()
        client.force_login(self.release_manager)
        url = reverse("departmental_exams:questionnaire_print_release")
        payload = {"action": "answer_key_revoke", "target_campus_id": self.campus.id, "release_id": other.id}
        response = client.post(url, payload, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(client.post(url, {"action": "answer_key_revoke", "release_id": first.id},
                                     HTTP_X_REQUESTED_WITH="XMLHttpRequest").status_code, 400)
        other.refresh_from_db(); first.refresh_from_db()
        self.assertEqual(other.status, "ACTIVE")
        self.assertEqual(first.status, "ACTIVE")

    def test_bulk_service_rejects_mixed_campuses_and_separate_releases_never_expand(self):
        kwargs = dict(
            tenant_id=self.tenant.id,
            actor=self.release_manager,
            available_from=self.start,
            available_until=self.end,
            attestation_confirmed=True,
        )
        with self.assertRaisesRegex(ValidationError, "one target campus"):
            AnswerKeyReleaseService.bulk_release(
                selections=tuple(
                    (self.parent.id, self.r4.id, self.parent.id, campus.id)
                    for campus in self.campuses
                ),
                **kwargs,
            )
        self.assertFalse(AnswerKeyRelease.objects.exists())

        releases = tuple(
            AnswerKeyReleaseService.bulk_release(
                selections=((self.parent.id, self.r4.id, self.parent.id, campus.id),),
                **kwargs,
            )[0]
            for campus in self.campuses
        )
        self.assertEqual(
            {release.target_campus_id for release in releases},
            {campus.id for campus in self.campuses},
        )
        Campus.objects.create(tenant=self.tenant, code="LATER", name="Later")
        self.assertEqual(AnswerKeyRelease.objects.count(), 3)
        self.assertTrue(all(release.scope_kind == "SCOPED" for release in releases))

    def test_scoped_history_remains_visible_without_current_final_revision(self):
        release = self.release_target()
        self.r4.current_marker = None
        self.r4.status = "SUPERSEDED"
        self.r4.save(update_fields=["current_marker", "status"])
        client = Client(); client.force_login(self.release_manager)
        response = client.get(reverse("departmental_exams:questionnaire_print_release"),
                              {"target_campus_id": self.campus.id})
        self.assertIn(release.id, [row.id for row in response.context["scoped_answer_key_history"]])
        self.assertContains(response, reverse("departmental_exams:answer_key_viewers", args=(release.id,)))
        self.assert_access(self.contribution, release, False)

    def test_inactive_campus_denies_faculty_and_new_release_but_preserves_history_management(self):
        self.assign(self.faculty, self.parent, self.other_campus)
        release = self.release_target(campus=self.other_campus)
        self.other_campus.is_active = False
        self.other_campus.save(update_fields=["is_active"])
        self.assert_access(self.contribution, release, False)
        with self.assertRaises(PermissionDenied):
            self.release_target(campus=self.other_campus)
        persisted, _ = AnswerKeyViewerReportService.report(
            release_id=release.id, tenant_id=self.tenant.id, actor=self.release_manager)
        self.assertEqual(persisted.id, release.id)
        AnswerKeyReleaseService.revoke(
            release_id=release.id, tenant_id=self.tenant.id, actor=self.release_manager)
        release.refresh_from_db()
        self.assertEqual(release.status, "REVOKED")

    def test_equivalent_member_isolation_while_primary_owns_revision(self):
        # Membership must be established before any generation, as in production.
        cycle = self.make_cycle(scope_suffix="scope-unit", status="OPEN")
        cycle.processing_mode = "AUTOMATIC_GENERATION"; cycle.save()
        primary = self.make_course(cycle=cycle, department=None, code="PRIMARY")
        member = self.make_course(cycle=cycle, department=None, code="MEMBER")
        deadline = self.future_deadline()
        for course in (primary, member):
            self.make_configuration(course, deadline=deadline)
        ExamCourseEquivalencyService.create_group(
            cycle_id=cycle.id, actor=self.admin,
            primary_cycle_course_id=primary.id, member_ids=(primary.id, member.id), name="Scoped equivalent")
        revision = self._make_revision(1, cycle_course=primary)
        for course in (primary, member):
            self.offerings[course.id, self.campus.id] = course.offering_snapshots.get().offering
        primary_contribution, _ = self.assign(self.faculty, primary, self.campus)
        member_contribution, _ = self.assign(self.faculty, member, self.campus)
        release = self.release_target(cycle_course_id=primary.id, revision_id=revision.id, recipient_course_id=member.id)
        self.assert_access(primary_contribution, release, False)
        self.assert_access(member_contribution, release, True)
        context = FacultyAnswerKeyReleaseService.build_safe_context(
            contribution=member_contribution,
            release_id=release.id,
            set_code="A",
            actor=self.faculty,
            printable=False,
        )
        self.assertEqual(context["course_code"], member.course.code)
        self.assertNotEqual(context["course_code"], primary.course.code)
        client = Client(); client.force_login(self.release_manager)
        response = client.get(reverse("departmental_exams:questionnaire_print_release"), {"target_campus_id": self.campus.id})
        rows = [r for r in response.context["bulk_answer_key_rows"] if r["course"].id == primary.id]
        self.assertEqual({r["recipient"].id for r in rows}, {primary.id, member.id})


class AnswerKeyScopeMigrationTests(Stage4TransactionTestCase):
    def test_legacy_forward_preserves_records_and_audit_and_reverse_blocks_scope_loss(self):
        executor = MigrationExecutor(connection)
        previous = [("departmental_exams", "0024_structured_exam_lifecycle_freeze")]
        executor.migrate(previous)
        try:
            historical = executor.loader.project_state(previous).apps
            old_release = historical.get_model("departmental_exams", "AnswerKeyRelease")
            course = self.make_course()
            revision = ExamGenerationRevision.objects.create(
                cycle_course=course, revision_number=1, source_input_fingerprint="f" * 64,
                algorithm_version="scope-migration-test", generation_trigger="AUTOMATIC",
                configuration_revision_snapshot=1, blueprint_revision_snapshot=1,
                roster_boundary_snapshot="r" * 64, final_item_count_snapshot=2,
                request_token_digest="t" * 64, minimum_overlap=0, proportional_score=0,
                contributors_represented=1, squared_contributor_concentration=4)
            now = timezone.now()
            legacy = old_release.objects.create(
                cycle_course_id=course.id, generation_revision_id=revision.id,
                available_from=now, available_until=now + timezone.timedelta(hours=2),
                released_by_id=self.admin.id, released_at=now,
                attestation_version="all-sessions-concluded-v1")
            revoked = old_release.objects.create(
                cycle_course_id=course.id, generation_revision_id=revision.id,
                available_from=now, available_until=now + timezone.timedelta(hours=1),
                released_by_id=self.admin.id, released_at=now,
                attestation_version="all-sessions-concluded-v1", status="REVOKED",
                active_marker=None, revoked_by_id=self.admin.id, revoked_at=now)
            before = list(old_release.objects.order_by("id").values())
            audit = AuditLog.objects.create(
                tenant=self.tenant, actor_user=self.admin, portal="FACULTY",
                action="DE_FACULTY_ANSWER_KEY_SET_VIEWED", entity_type="AnswerKeyRelease",
                entity_id=str(legacy.id), metadata_json={"set_code": "A", "release_id": legacy.id})
            audit_before = AuditLog.objects.filter(pk=audit.id).values().get()
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
            for saved in before:
                after = AnswerKeyRelease.objects.filter(pk=saved["id"]).values().get()
                self.assertEqual({key: after[key] for key in saved}, saved)
                self.assertEqual(after["scope_kind"], "LEGACY_UNSCOPED")
                self.assertIsNone(after["target_campus_id"])
                self.assertIsNone(after["recipient_course_id"])
            self.assertEqual(AuditLog.objects.filter(pk=audit.id).values().get(), audit_before)
            # Separate scoped rows can coexist with the preserved legacy ACTIVE row.
            scoped = AnswerKeyRelease.objects.create(
                cycle_course=course, generation_revision=revision, recipient_course=course,
                target_campus=self.campus, available_from=now,
                available_until=now + timezone.timedelta(hours=2), released_by=self.admin,
                attestation_version="all-sessions-concluded-v1")
            with self.assertRaises(IrreversibleError):
                MigrationExecutor(connection).migrate(previous)
            self.assertEqual(AnswerKeyRelease.objects.get(pk=scoped.id).target_campus_id, self.campus.id)
            self.assertEqual(AnswerKeyRelease.objects.get(pk=revoked.id).status, "REVOKED")
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())

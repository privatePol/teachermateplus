from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import Client, skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import CourseOffering, FacultyAssignment, Section
from apps.auditlog.models import AuditLog
from apps.enrollment.models import Enrollment
from apps.rbac.models import Permission, UserPermission, UserRole
from apps.students.models import Student
from apps.tenants.models import Campus, Department, Program

from .models import (
    CourseExamConfiguration,
    CycleCourseOffering,
    ExamGenerationRevision,
    ExaminationCycle,
    FacultyContribution,
    FacultyContributionEligibilitySource,
    GeneratedExamItem,
    GeneratedExamSet,
    PersonalizedAnswerSheetAssignment,
    Question,
)
from .personalized_answer_sheets import PersonalizedAnswerSheetService
from .questionnaire_printing import QuestionnairePrintReleaseService
from .stage4_test_support import Stage4TestCase


class PersonalizedAnswerSheetTests(Stage4TestCase):
    def setUp(self):
        super().setUp()
        self.manager_user = self.make_user(
            "personalized-manager",
            self.department,
            ("admin_portal.access", "departmental_exams.manage_exam_generation"),
        )
        self.faculty = self.make_user(
            "personalized-faculty",
            self.department,
            ("faculty_portal.access",),
        )
        cycle = self.make_cycle(status=ExaminationCycle.Status.OPEN)
        cycle.processing_mode = ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        cycle.save(update_fields=["processing_mode", "updated_at"])
        self.parent = self.make_course(cycle=cycle, department=None, code="PAS-101")
        self.offering = self.parent.offering_snapshots.get().offering
        self.configuration = self.make_configuration(
            self.parent,
            workflow=CourseExamConfiguration.WorkflowStatus.CLOSED,
            opened_at=timezone.now() - timezone.timedelta(days=2),
        )
        self.assignment = FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            faculty_user=self.faculty,
            accepted_by=self.faculty,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            responded_at=timezone.now(),
            accepted_at=timezone.now(),
            is_primary=True,
        )
        self.contribution = FacultyContribution.objects.create(
            cycle_course=self.parent,
            faculty_user=self.faculty,
            source_assignment=self.assignment,
            source_campus=self.campus,
            quota_snapshot=50,
            configuration_revision_snapshot=self.configuration.revision,
            status=FacultyContribution.Status.SUBMITTED,
            submitted_at=timezone.now(),
        )
        FacultyContributionEligibilitySource.objects.create(
            contribution=self.contribution,
            assignment=self.assignment,
            assignment_id_snapshot=self.assignment.id,
            offering_id_snapshot=self.offering.id,
            tenant_id_snapshot=self.tenant.id,
            campus_id_snapshot=self.campus.id,
        )
        self.questions = [
            Question.objects.create(
                contribution=self.contribution,
                question_text=f"Question {position}",
                choice_a="A",
                choice_b="B",
                choice_c="C",
                choice_d="D",
                correct_answer="A",
                difficulty=("EASY" if position <= 23 else "MODERATE"),
                position=position,
            )
            for position in range(1, 76)
        ]
        self.revision = self._make_revision(revision_number=1, item_count=50)

    def _make_revision(self, *, revision_number, item_count, supersedes=None):
        revision = ExamGenerationRevision.objects.create(
            cycle_course=self.parent,
            revision_number=revision_number,
            source_input_fingerprint=(str(revision_number) * 64)[:64],
            algorithm_version="personalized-test-v1",
            generation_trigger=ExamGenerationRevision.GenerationTrigger.AUTOMATIC,
            configuration_revision_snapshot=1,
            blueprint_revision_snapshot=1,
            roster_boundary_snapshot=(f"r{revision_number}" * 64)[:64],
            final_item_count_snapshot=item_count,
            request_token_digest=(f"t{revision_number}" * 64)[:64],
            supersedes=supersedes,
            minimum_overlap=0,
            proportional_score=0,
            contributors_represented=1,
            squared_contributor_concentration=item_count * item_count,
        )
        for set_code in ("A", "B"):
            generated_set = GeneratedExamSet.objects.create(
                generation_revision=revision,
                set_code=set_code,
                campus_quotas_snapshot={self.campus.code: item_count},
                difficulty_quotas_snapshot={"EASY": item_count, "MODERATE": 0, "DIFFICULT": 0},
                section_quotas_snapshot={"0": item_count},
                item_count=item_count,
            )
            for position, question in enumerate(self.questions[:item_count], start=1):
                GeneratedExamItem.objects.create(
                    generated_set=generated_set,
                    position=position,
                    source_question=question,
                    source_question_revision=question.revision,
                    source_question_digest=(f"digest-{set_code}-{position}" + "x" * 64)[:64],
                    source_contributor=self.faculty,
                    source_contributor_id_snapshot=self.faculty.id,
                    source_contributor_name_snapshot="Private contributor",
                    source_campus=self.campus,
                    campus_code_snapshot=self.campus.code,
                    campus_name_snapshot=self.campus.name,
                    difficulty_snapshot=question.difficulty,
                    section_title_snapshot="Private section",
                    question_text_snapshot=f"Private question {position}",
                    choices_snapshot=["A", "B", "C", "D"],
                    correct_answer_snapshot="A",
                )
        return revision

    def _replace_revision(self, *, item_count, revision_number):
        previous = self.revision
        previous.status = ExamGenerationRevision.Status.SUPERSEDED
        previous.current_marker = None
        previous.save(update_fields=["status", "current_marker", "updated_at"])
        self.revision = self._make_revision(
            revision_number=revision_number,
            item_count=item_count,
            supersedes=previous,
        )
        return self.revision

    def _release(self, *, revision=None, print_from=None, print_until=None):
        now = timezone.now()
        return QuestionnairePrintReleaseService.release(
            cycle_course_id=self.parent.id,
            revision_id=(revision or self.revision).id,
            tenant_id=self.tenant.id,
            actor=self.manager_user,
            print_from=print_from or now - timezone.timedelta(minutes=5),
            print_until=print_until or now + timezone.timedelta(hours=2),
        )

    def _student(self, number, *, last_name=None, status=Student.Status.ACTIVE, is_active=True):
        student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.offering.section.program,
            student_no=number,
            last_name=last_name or f"Last {number}",
            first_name=f"First {number}",
            middle_name="Middle",
            status=status,
            is_active=is_active,
        )
        return Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.parent.cycle.academic_year,
            term=self.parent.cycle.term,
            student=student,
            course_offering=self.offering,
            enrollment_status=Enrollment.Status.ACTIVE,
            is_active=True,
        )

    def _client(self):
        client = Client()
        client.force_login(self.faculty)
        return client

    def _overview_url(self, release):
        return reverse(
            "departmental_exams:personalized_answer_sheet_overview",
            args=(self.contribution.id, release.id),
        )

    def _prepare_url(self, release, offering=None):
        return reverse(
            "departmental_exams:personalized_answer_sheet_prepare",
            args=(self.contribution.id, release.id, (offering or self.offering).id),
        )

    def _print_url(self, release, set_filter="all", offering=None):
        return reverse(
            "departmental_exams:personalized_answer_sheet_print",
            args=(
                self.contribution.id,
                release.id,
                (offering or self.offering).id,
                set_filter,
            ),
        )

    def _prepare(self, release):
        return self._client().post(self._prepare_url(release))

    def test_release_controls_visibility_and_get_routes_never_create_assignments(self):
        client = self._client()
        course_card = client.get(reverse("departmental_exams:contribution_list"))
        self.assertNotContains(course_card, "Personalized Answer Sheets")
        self.assertEqual(
            client.get(
                reverse(
                    "departmental_exams:personalized_answer_sheet_overview",
                    args=(self.contribution.id, 999999),
                )
            ).status_code,
            403,
        )
        release = self._release()
        course_card = client.get(reverse("departmental_exams:contribution_list"))
        self.assertContains(course_card, "Exam Outputs")
        self.assertContains(course_card, "Personalized Answer Sheets")
        overview = client.get(self._overview_url(release))
        self.assertEqual(overview.status_code, 200)
        self.assertIn("private", overview["Cache-Control"])
        self.assertEqual(PersonalizedAnswerSheetAssignment.objects.count(), 0)
        self.assertEqual(client.get(self._print_url(release)).status_code, 403)
        self.assertEqual(PersonalizedAnswerSheetAssignment.objects.count(), 0)

    def test_prepare_is_post_csrf_protected_idempotent_and_evenly_balanced(self):
        enrollments = [self._student(f"S-{index}") for index in range(4)]
        release = self._release()
        client = self._client()
        self.assertEqual(client.get(self._prepare_url(release)).status_code, 405)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.faculty)
        self.assertEqual(csrf_client.post(self._prepare_url(release)).status_code, 403)
        self.assertEqual(client.post(self._prepare_url(release)).status_code, 302)
        assignments = list(PersonalizedAnswerSheetAssignment.objects.order_by("id"))
        self.assertEqual(len(assignments), 4)
        self.assertEqual([row.set_code for row in assignments].count("A"), 2)
        self.assertEqual([row.set_code for row in assignments].count("B"), 2)
        before = {row.enrollment_id: (row.id, row.set_code, row.public_id) for row in assignments}
        self.assertEqual(client.post(self._prepare_url(release)).status_code, 302)
        after = {
            row.enrollment_id: (row.id, row.set_code, row.public_id)
            for row in PersonalizedAnswerSheetAssignment.objects.all()
        }
        self.assertEqual(before, after)
        self.assertEqual(set(before), {row.id for row in enrollments})

    def test_odd_roster_extra_set_is_deterministic(self):
        for index in range(5):
            self._student(f"O-{index}")
        release = self._release()
        self._prepare(release)
        counts = {
            code: PersonalizedAnswerSheetAssignment.objects.filter(set_code=code).count()
            for code in ("A", "B")
        }
        expected_extra = PersonalizedAnswerSheetService._tie_set(
            domain=PersonalizedAnswerSheetService.ODD_START_DOMAIN,
            revision_id=self.revision.id,
            offering_id=self.offering.id,
        )
        self.assertEqual(abs(counts["A"] - counts["B"]), 1)
        self.assertEqual(counts[expected_extra], 3)

    def test_late_enrollment_uses_smaller_set_without_reshuffle_and_reactivation_reuses_set(self):
        enrollments = [self._student(f"L-{index}") for index in range(4)]
        release = self._release()
        self._prepare(release)
        original = {
            row.enrollment_id: row.set_code
            for row in PersonalizedAnswerSheetAssignment.objects.all()
        }
        set_a_enrollment = next(key for key, value in original.items() if value == "A")
        inactive = Enrollment.objects.get(pk=set_a_enrollment)
        inactive.is_active = False
        inactive.save(update_fields=["is_active", "updated_at"])
        late = self._student("L-LATE")
        self._prepare(release)
        late_assignment = PersonalizedAnswerSheetAssignment.objects.get(enrollment=late)
        self.assertEqual(late_assignment.set_code, "A")
        self.assertEqual(
            late_assignment.assignment_method,
            PersonalizedAnswerSheetAssignment.AssignmentMethod.LATE_BALANCED,
        )
        for enrollment in enrollments:
            self.assertEqual(
                PersonalizedAnswerSheetAssignment.objects.get(enrollment=enrollment).set_code,
                original[enrollment.id],
            )
        inactive.is_active = True
        inactive.save(update_fields=["is_active", "updated_at"])
        self._prepare(release)
        self.assertEqual(
            PersonalizedAnswerSheetAssignment.objects.get(enrollment=inactive).set_code,
            original[inactive.id],
        )

    def test_inactive_enrollment_and_noncurrent_student_are_excluded_but_history_remains(self):
        active = self._student("ACTIVE")
        inactive_enrollment = self._student("INACTIVE-ENROLL")
        inactive_student = self._student("INACTIVE-STUDENT")
        release = self._release()
        self._prepare(release)
        inactive_enrollment.is_active = False
        inactive_enrollment.save(update_fields=["is_active", "updated_at"])
        inactive_student.student.status = Student.Status.INACTIVE
        inactive_student.student.save(update_fields=["status", "updated_at"])
        response = self._client().get(self._print_url(release))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, active.student.student_no)
        self.assertNotContains(response, inactive_enrollment.student.student_no)
        self.assertNotContains(response, inactive_student.student.student_no)
        self.assertEqual(PersonalizedAnswerSheetAssignment.objects.count(), 3)

    def test_assignment_model_is_unique_immutable_protected_and_choice_limited(self):
        enrollment = self._student("MODEL")
        release = self._release()
        self._prepare(release)
        assignment = PersonalizedAnswerSheetAssignment.objects.get(enrollment=enrollment)
        assignment.set_code = "B" if assignment.set_code == "A" else "A"
        with self.assertRaisesRegex(ValidationError, "immutable"):
            assignment.save()
        assignment.refresh_from_db()
        with self.assertRaisesRegex(ValidationError, "historical"):
            assignment.delete()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PersonalizedAnswerSheetAssignment.objects.create(
                    generation_revision=self.revision,
                    enrollment=enrollment,
                    course_offering=self.offering,
                    set_code="A",
                    assignment_method="INITIAL_BALANCED",
                    assigned_by=self.faculty,
                )
        invalid = PersonalizedAnswerSheetAssignment(
            generation_revision=self.revision,
            enrollment=self._student("INVALID"),
            course_offering=self.offering,
            set_code="C",
            assignment_method="UNKNOWN",
            assigned_by=self.faculty,
        )
        with self.assertRaises(ValidationError):
            invalid.full_clean()

    def test_expired_replaced_lost_assignment_and_direct_deny_fail_closed(self):
        self._student("AUTH")
        now = timezone.now()
        expired = self._release(
            print_from=now - timezone.timedelta(hours=2),
            print_until=now - timezone.timedelta(hours=1),
        )
        client = self._client()
        self.assertEqual(client.get(self._overview_url(expired)).status_code, 403)
        active = self._release()
        self.assertEqual(client.get(self._overview_url(expired)).status_code, 403)
        self.assignment.is_active = False
        self.assignment.save(update_fields=["is_active", "updated_at"])
        self.assertEqual(client.get(self._overview_url(active)).status_code, 403)
        self.assignment.is_active = True
        self.assignment.save(update_fields=["is_active", "updated_at"])
        UserPermission.objects.create(
            user=self.faculty,
            permission=Permission.objects.get(code="faculty_portal.access"),
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.campus,
        )
        self.assertEqual(client.get(self._overview_url(active)).status_code, 403)

    def test_wrong_or_unauthorized_offering_and_program_conflict_fail_closed(self):
        self._student("SCOPE")
        release = self._release()
        second_section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.offering.section.program,
            code="UNAUTHORIZED",
            name="Unauthorized",
        )
        second_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.offering.program,
            academic_year=self.offering.academic_year,
            term=self.offering.term,
            course=self.offering.course,
            section=second_section,
        )
        CycleCourseOffering.objects.create(
            cycle_course=self.parent,
            offering=second_offering,
            campus=self.campus,
        )
        client = self._client()
        self.assertEqual(
            client.post(self._prepare_url(release, second_offering)).status_code,
            403,
        )
        self.assertEqual(
            client.get(self._print_url(release, offering=second_offering)).status_code,
            403,
        )
        conflicting_program = Program.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="CONFLICT",
            name="Conflict",
        )
        self.offering.program = conflicting_program
        self.offering.save(update_fields=["program", "updated_at"])
        self.assertEqual(client.get(self._overview_url(release)).status_code, 403)

    def test_active_r3_release_remains_exact_after_r4_generation(self):
        self._student("REVISION")
        r3_release = self._release()
        self._prepare(r3_release)
        self._replace_revision(item_count=60, revision_number=2)
        response = self._client().get(self._print_url(r3_release))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["revision_number"], 1)
        self.assertEqual(response.context["item_count"], 50)
        self.assertContains(response, "UNUSED", count=25)

    def test_print_identity_order_filters_item_rows_and_private_paper_allowlist(self):
        first = self._student("9002", last_name="Zulu")
        second = self._student("1001", last_name="Alpha")
        third = self._student("5001", last_name="Middle")
        release = self._release()
        self._prepare(release)
        client = self._client()
        response = client.get(self._print_url(release))
        content = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn("private", response["Cache-Control"])
        self.assertEqual(content.count('class="personalized-sheet"'), 3)
        self.assertEqual(content.count('class="answer-bubble"'), 3 * 50 * 4)
        self.assertEqual(content.count("UNUSED"), 3 * 25)
        self.assertLess(content.index(second.student.student_no), content.index(third.student.student_no))
        self.assertLess(content.index(third.student.student_no), content.index(first.student.student_no))
        self.assertContains(response, self.offering.course.code)
        self.assertContains(response, self.offering.course.title)
        self.assertContains(response, self.offering.section.code)
        self.assertContains(response, "Date:")
        self.assertNotContains(response, "Pair Code:</strong>")
        for set_code in ("A", "B"):
            filtered = client.get(self._print_url(release, set_code))
            expected = PersonalizedAnswerSheetAssignment.objects.filter(set_code=set_code).count()
            self.assertEqual(filtered.context["sheets"].__len__(), expected)
            self.assertNotIn(
                f'data-set-code="{"B" if set_code == "A" else "A"}"',
                filtered.content.decode(),
            )
        for query, value, css in (
            ({}, "letter", "Letter"),
            ({"paper": "a4"}, "a4", "A4"),
            ({"paper": "legal"}, "legal", "Legal"),
            ({"paper": "tabloid};body{display:none"}, "letter", "Letter"),
        ):
            sized = client.get(self._print_url(release), query)
            self.assertEqual(sized.context["paper_size"], value)
            self.assertIn(f"size:{css} portrait", sized.content.decode())

    def test_valid_60_and_75_item_revisions_render_exact_active_and_unused_rows(self):
        self._student("COUNTS")
        release = self._release()
        self._prepare(release)
        for revision_number, item_count in ((2, 60), (3, 75)):
            with self.subTest(item_count=item_count):
                revision = self._replace_revision(
                    item_count=item_count,
                    revision_number=revision_number,
                )
                release = self._release(revision=revision)
                self._prepare(release)
                response = self._client().get(self._print_url(release))
                content = response.content.decode()
                self.assertEqual(response.status_code, 200)
                self.assertEqual(content.count('class="answer-bubble"'), item_count * 4)
                self.assertEqual(content.count("UNUSED"), 75 - item_count)

    def test_item_count_and_position_corruption_fails_closed(self):
        self._student("CORRUPT")
        release = self._release()
        self._prepare(release)
        generated_set = GeneratedExamSet.objects.get(
            generation_revision=self.revision,
            set_code="A",
        )
        GeneratedExamSet.objects.filter(pk=generated_set.pk).update(item_count=49)
        self.assertEqual(self._client().get(self._print_url(release)).status_code, 403)
        GeneratedExamSet.objects.filter(pk=generated_set.pk).update(item_count=50)
        GeneratedExamItem.objects.filter(
            generated_set=generated_set,
            position=50,
        ).update(position=51)
        self.assertEqual(self._client().get(self._print_url(release)).status_code, 403)

    def test_audits_and_urls_are_content_safe_and_public_id_is_not_rendered(self):
        enrollment = self._student("PRIVATE-STUDENT-NUMBER", last_name="Secretname")
        release = self._release()
        prepare_url = self._prepare_url(release)
        print_url = self._print_url(release)
        self.assertNotIn(enrollment.student.student_no, prepare_url)
        self.assertNotIn(enrollment.student.student_no, print_url)
        self._prepare(release)
        assignment = PersonalizedAnswerSheetAssignment.objects.get(enrollment=enrollment)
        response = self._client().get(print_url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, str(assignment.public_id))
        for action in (
            "DE_PERSONALIZED_SHEET_ASSIGNMENTS_PREPARED",
            "DE_PERSONALIZED_SHEET_BATCH_RENDERED",
        ):
            audit = AuditLog.objects.filter(action=action).latest("id")
            metadata = str(audit.metadata_json).lower()
            for forbidden in (
                "private-student-number",
                "secretname",
                "question",
                "correct_answer",
                "secret_key",
                "digest",
                "rank",
                str(assignment.public_id).lower(),
            ):
                self.assertNotIn(forbidden, metadata)

    def test_cross_tenant_request_scope_and_direct_existence_probe_fail_closed(self):
        self._student("TENANT")
        release = self._release()
        other_campus = Campus.objects.create(
            tenant=self.other_tenant,
            code="OTHER",
            name="Other Campus",
        )
        other_department = Department.objects.create(
            tenant=self.other_tenant,
            campus=other_campus,
            code="OTHER",
            name="Other Department",
        )
        faculty_role = UserRole.objects.filter(user=self.faculty).get().role
        UserRole.objects.create(
            user=self.faculty,
            role=faculty_role,
            tenant=self.other_tenant,
            campus=other_campus,
            department=other_department,
        )
        client = self._client()
        self.assertEqual(
            client.get(
                self._overview_url(release),
                {
                    "scope_tenant_id": self.other_tenant.id,
                    "scope_campus_id": other_campus.id,
                },
            ).status_code,
            404,
        )
        session = client.session
        session["TeacherMate+_scope_tenant_id"] = self.tenant.id
        session["TeacherMate+_scope_campus_id"] = self.campus.id
        session.save()
        nonexistent = reverse(
            "departmental_exams:personalized_answer_sheet_print",
            args=(self.contribution.id, release.id, 999999, "all"),
        )
        unauthorized = self._print_url(release, offering=self.offering)
        self.assignment.is_active = False
        self.assignment.save(update_fields=["is_active", "updated_at"])
        self.assertEqual(client.get(nonexistent).status_code, 403)
        self.assertEqual(client.get(unauthorized).status_code, 403)

    @skipUnlessDBFeature("has_select_for_update")
    def test_database_locking_backend_serializes_repeated_preparation(self):
        self._student("LOCKING")
        release = self._release()
        first = PersonalizedAnswerSheetService.prepare(
            contribution=self.contribution,
            release_id=release.id,
            offering_id=self.offering.id,
            actor=self.faculty,
        )
        second = PersonalizedAnswerSheetService.prepare(
            contribution=self.contribution,
            release_id=release.id,
            offering_id=self.offering.id,
            actor=self.faculty,
        )
        self.assertEqual(first["created_count"], 1)
        self.assertEqual(second["created_count"], 0)
        self.assertEqual(PersonalizedAnswerSheetAssignment.objects.count(), 1)

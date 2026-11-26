from __future__ import annotations

import hashlib
import hmac
import json
from collections import Counter, defaultdict

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count
from django.http import Http404
from django.utils import timezone

from apps.academics.models import CourseOffering
from apps.core.services.audit import AuditService
from apps.core.services.settings import SystemSettingService
from apps.enrollment.models import Enrollment

from .contribution_authorization import ContributionAuthorizationService
from .exam_units import resolve_examination_unit
from .models import (
    CycleCourse,
    CycleCourseOffering,
    ExamGenerationRevision,
    ExaminationCycle,
    FacultyContribution,
    GeneratedExamSet,
    PersonalizedAnswerSheetAssignment,
    QuestionnairePrintRelease,
)
from .questionnaire_printing import (
    MANILA_TIMEZONE,
    FacultyQuestionnairePrintService,
    QuestionnairePrintReleaseService,
    _questionnaire_paper_context,
)


from .set_modes import expected_set_codes, valid_set_code


class PersonalizedAnswerSheetService:
    ALGORITHM_VERSION = PersonalizedAnswerSheetAssignment.ALGORITHM_VERSION
    SECTION_PAIR_ALGORITHM_VERSION = "section-pair-balanced-v1"
    RANK_DOMAIN = "departmental-exams.personalized-sheets.rank.v1"
    ODD_START_DOMAIN = "departmental-exams.personalized-sheets.odd-start.v1"
    LATE_TIE_DOMAIN = "departmental-exams.personalized-sheets.late-tie.v1"

    @staticmethod
    def _hmac_rank(*, domain, context):
        secret = (getattr(settings, "SECRET_KEY", "") or "").encode("utf-8")
        if not secret:
            raise PermissionDenied("Personalized answer-sheet preparation is unavailable.")
        material = json.dumps(
            context,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        digest = hmac.new(
            secret,
            domain.encode("utf-8") + b"\x00" + material,
            hashlib.sha256,
        ).digest()
        return int.from_bytes(digest, "big")

    @classmethod
    def _candidate_rank(cls, *, revision_id, offering_id, enrollment):
        return cls._hmac_rank(
            domain=cls.RANK_DOMAIN,
            context={
                "revision_id": revision_id,
                "offering_id": offering_id,
                "enrollment_id": enrollment.id,
                "student_id": enrollment.student_id,
            },
        )

    @classmethod
    def _tie_set(cls, *, domain, revision_id, offering_id, enrollment_id=None):
        rank = cls._hmac_rank(
            domain=domain,
            context={
                "revision_id": revision_id,
                "offering_id": offering_id,
                "enrollment_id": enrollment_id,
            },
        )
        return GeneratedExamSet.SetCode.A if rank % 2 == 0 else GeneratedExamSet.SetCode.B

    @staticmethod
    def _student_name(student):
        middle = (student.middle_name or "").strip()
        given = " ".join(part for part in (student.first_name.strip(), middle) if part)
        return f"{student.last_name.strip()}, {given}".strip(", ")

    @staticmethod
    def _active_enrollments(*, offering, for_update=False):
        queryset = Enrollment.objects.filter(
            course_offering=offering,
            is_active=True,
            enrollment_status=Enrollment.Status.ACTIVE,
            student__is_active=True,
            student__status="ACTIVE",
        ).select_related("student", "student__program")
        if for_update:
            queryset = queryset.select_for_update()
        return queryset.order_by(
            "student__last_name",
            "student__first_name",
            "student__student_no",
            "id",
        )

    @staticmethod
    def _validate_offering_scope(*, release, offering):
        cycle_course = release.cycle_course
        cycle = cycle_course.cycle
        unit = resolve_examination_unit(cycle_course)
        matching_member = next(
            (member for member in unit.members if member.course_id == offering.course_id),
            None,
        )
        if (
            offering.tenant_id != cycle.tenant_id
            or offering.campus_id is None
            or (
                release.scope_kind == QuestionnairePrintRelease.ScopeKind.SCOPED
                and offering.campus_id != release.target_campus_id
            )
            or (
                release.scope_kind == QuestionnairePrintRelease.ScopeKind.LEGACY_COURSE_WIDE
                and not QuestionnairePrintReleaseService.active_legacy_coverage(
                    course=cycle_course, campus_id=offering.campus_id,
                )
            )
            or (
                release.scope_kind == QuestionnairePrintRelease.ScopeKind.LEGACY_COURSE_WIDE
                and QuestionnairePrintRelease.objects.filter(
                    cycle_course=cycle_course,
                    scope_kind=QuestionnairePrintRelease.ScopeKind.SCOPED,
                    target_campus_id=offering.campus_id,
                ).exists()
            )
            or matching_member is None
            or offering.academic_year_id != cycle.academic_year_id
            or offering.term_id != cycle.term_id
            or not offering.is_active
            or offering.status != CourseOffering.Status.OPEN
            or not offering.campus.is_active
            or not CycleCourseOffering.objects.filter(
                cycle_course=matching_member,
                offering=offering,
                campus_id=offering.campus_id,
            ).exists()
        ):
            raise PermissionDenied("Course offering is outside the released examination scope.")
        if (
            offering.program_id
            and offering.section.program_id
            and offering.program_id != offering.section.program_id
        ):
            raise PermissionDenied("Course offering program data is inconsistent.")

    @staticmethod
    def _authorized_offering_ids(*, contribution):
        return {
            assignment.offering_id
            for assignment in ContributionAuthorizationService.retained_current_print_assignments(
                contribution=contribution
            )
        }

    @classmethod
    def _require_authorized_offering(cls, *, contribution, release, offering):
        cls._validate_offering_scope(release=release, offering=offering)
        if offering.id not in cls._authorized_offering_ids(contribution=contribution):
            raise PermissionDenied("Course offering is unavailable to this faculty member.")
        return cls._saved_section_codes(revision=release.generation_revision, offering=offering)

    @staticmethod
    def _saved_section_codes(*, revision, offering):
        if not revision.set_policy_snapshot:
            return None
        assignments = revision.section_set_assignments.filter(
            offering_id=offering.id, campus_id_snapshot=offering.campus_id,
            generated_set__generation_revision=revision,
        ).select_related("generated_set").order_by("version_code")
        codes = tuple(row.generated_set.set_code for row in assignments)
        if not codes:
            raise PermissionDenied("No saved set for this section in the released revision.")
        return codes

    @staticmethod
    def _validate_generated_output(*, revision):
        if not 50 <= revision.final_item_count_snapshot <= 75:
            raise PermissionDenied("The released questionnaire item count is unsupported.")
        sets = list(
            GeneratedExamSet.objects.filter(generation_revision=revision)
            .annotate(actual_item_count=Count("items"))
            .order_by("set_code")
        )
        if (
            len(sets) != len(expected_set_codes(revision))
            or {row.set_code for row in sets} != expected_set_codes(revision)
            or any(
                row.item_count != revision.final_item_count_snapshot
                or row.actual_item_count != revision.final_item_count_snapshot
                for row in sets
            )
        ):
            raise PermissionDenied("The released questionnaire sets are incomplete.")
        expected = list(range(1, revision.final_item_count_snapshot + 1))
        for generated_set in sets:
            positions = list(
                generated_set.items.order_by("position").values_list("position", flat=True)
            )
            if positions != expected:
                raise PermissionDenied("The released questionnaire item sequence is incomplete.")
        return revision.final_item_count_snapshot

    @staticmethod
    def _first_authorized_code(*, contribution, release_id):
        release = QuestionnairePrintRelease.objects.select_related("generation_revision").filter(pk=release_id).first()
        if release is not None and release.generation_revision.set_policy_snapshot:
            from .models import ExamSectionSetAssignment
            ids = PersonalizedAnswerSheetService._authorized_offering_ids(contribution=contribution)
            row = ExamSectionSetAssignment.objects.filter(generation_revision=release.generation_revision,
                offering_id__in=ids, campus_id_snapshot=release.target_campus_id).select_related("generated_set").first()
            if row is None:
                raise PermissionDenied("No saved section set is available to this faculty member.")
            return row.generated_set.set_code
        return "A"

    @classmethod
    def _release_context(cls, *, contribution, release_id, now=None):
        release, _generated_set, _set_code = (
            FacultyQuestionnairePrintService._printable_release(
                contribution=contribution,
                release_id=release_id,
                set_code=cls._first_authorized_code(contribution=contribution, release_id=release_id),
                now=now,
            )
        )
        item_count = cls._validate_generated_output(
            revision=release.generation_revision
        )
        return release, item_count

    @classmethod
    def overview_context(cls, *, contribution, release_id):
        release, item_count = cls._release_context(
            contribution=contribution,
            release_id=release_id,
        )
        offering_ids = cls._authorized_offering_ids(contribution=contribution)
        offerings = list(
            CourseOffering.objects.filter(id__in=offering_ids)
            .select_related("course", "section", "section__program", "program", "campus")
            .order_by("section__code", "id")
        )
        matching_campus_ids = set(FacultyQuestionnairePrintService._matching_campuses(
            contribution=contribution, release=release,
        ))
        offerings = [row for row in offerings if row.campus_id in matching_campus_ids]
        section_sets = bool(release.generation_revision.set_policy_snapshot)
        saved_codes = defaultdict(list)
        saved_sets = {}
        if section_sets:
            for row in release.generation_revision.section_set_assignments.select_related("generated_set").order_by("version_code"):
                saved_codes[row.offering_id].append(row.generated_set.set_code)
                saved_sets[row.generated_set.set_code] = row
        rows = []
        for offering in offerings:
            cls._validate_offering_scope(release=release, offering=offering)
            enrollments = list(cls._active_enrollments(offering=offering))
            enrollment_ids = [row.id for row in enrollments]
            assignments = PersonalizedAnswerSheetAssignment.objects.filter(
                generation_revision=release.generation_revision,
                course_offering=offering,
                enrollment_id__in=enrollment_ids,
            )
            counts = Counter(assignments.values_list("set_code", flat=True))
            assigned_ids = set(assignments.values_list("enrollment_id", flat=True))
            missing = len(set(enrollment_ids) - assigned_ids)
            section_codes = saved_codes.get(offering.id)
            paired = bool(section_codes and len(section_codes) == 2)
            section_code = " / ".join(saved_sets[code].generated_set.display_version for code in section_codes) if section_codes else None
            section_set_unavailable = section_sets and section_code is None
            rows.append(
                {
                    "offering": offering,
                    "active_students": len(enrollments),
                    "section_set": section_code,
                    "section_set_unavailable": section_set_unavailable,
                    "assigned": len(assigned_ids),
                    "set_a": counts[section_codes[0]] if paired else counts["A"],
                    "set_b": counts[section_codes[1]] if paired else counts["B"],
                    "set_options": tuple({"code": code, "label": saved_sets[code].generated_set.display_version} for code in (section_codes or ())),
                    "paired": paired,
                    "section_label": saved_sets[section_codes[0]].section_label_snapshot if section_codes else str(offering.section),
                    "missing": missing,
                    "ready": not section_set_unavailable and bool(enrollments) and missing == 0,
                }
            )
        cycle = release.cycle_course.cycle
        return {
            "contribution": contribution,
            "release": release,
            "revision_number": release.generation_revision.revision_number,
            "course_code": release.cycle_course.course.code,
            "course_title": release.cycle_course.course.title,
            "academic_year": cycle.academic_year.name,
            "term": cycle.term.name,
            "exam_period": cycle.get_exam_period_display(),
            "item_count": item_count,
            "offering_rows": rows,
            "section_sets": section_sets,
            "paired_section_sets": release.generation_revision.set_policy_snapshot.get("schema") == "section-sets-v2",
        }

    @classmethod
    @transaction.atomic
    def prepare(cls, *, contribution, release_id, offering_id, actor, request=None):
        cycle_id = contribution.cycle_course.cycle_id
        cycle_course_id = contribution.cycle_course_id
        ExaminationCycle.objects.select_for_update().get(pk=cycle_id)
        locked_course = CycleCourse.objects.select_for_update().get(
            pk=cycle_course_id,
            cycle_id=cycle_id,
        )
        unit = resolve_examination_unit(locked_course, for_update=True)
        locked_release = QuestionnairePrintRelease.objects.select_for_update().filter(
            pk=release_id,
            cycle_course=unit.primary,
        ).first()
        if locked_release is None:
            raise PermissionDenied("Questionnaire print release is unavailable.")
        ExamGenerationRevision.objects.select_for_update().get(
            pk=locked_release.generation_revision_id,
            cycle_course=unit.primary,
        )
        locked_contribution = (
            FacultyContribution.objects.select_for_update()
            .select_related(
                "faculty_user",
                "cycle_course__cycle__tenant",
                "cycle_course__cycle__academic_year",
                "cycle_course__cycle__term",
                "cycle_course__course",
            )
            .prefetch_related("eligibility_sources")
            .filter(pk=contribution.id, faculty_user=actor, cycle_course=locked_course)
            .first()
        )
        if locked_contribution is None:
            raise PermissionDenied("Faculty contribution access is unavailable.")
        release, _item_count = cls._release_context(
            contribution=locked_contribution,
            release_id=locked_release.id,
        )
        offering = (
            CourseOffering.objects.select_for_update()
            .select_related("campus", "course", "section", "section__program", "program")
            .filter(pk=offering_id)
            .first()
        )
        if offering is None:
            raise PermissionDenied("Course offering is unavailable.")
        section_codes = cls._require_authorized_offering(
            contribution=locked_contribution,
            release=release,
            offering=offering,
        )
        enrollments = list(cls._active_enrollments(offering=offering, for_update=True))
        active_ids = {row.id for row in enrollments}
        all_existing = list(
            PersonalizedAnswerSheetAssignment.objects.select_for_update().filter(
                generation_revision=release.generation_revision,
                course_offering=offering,
            )
        )
        existing_by_enrollment = {row.enrollment_id: row for row in all_existing}
        active_existing = [
            row for row in all_existing if row.enrollment_id in active_ids
        ]
        missing = [row for row in enrollments if row.id not in existing_by_enrollment]
        missing.sort(
            key=lambda row: (
                cls._candidate_rank(
                    revision_id=release.generation_revision_id,
                    offering_id=offering.id,
                    enrollment=row,
                ),
                row.id,
            )
        )
        initial = not all_existing
        counts = Counter(row.set_code for row in active_existing)
        code_a, code_b = section_codes if section_codes and len(section_codes) == 2 else ("A", "B")
        start_set = cls._tie_set(
            domain=cls.ODD_START_DOMAIN,
            revision_id=release.generation_revision_id,
            offering_id=offering.id,
        )
        start_set = code_a if start_set == "A" else code_b
        created = []
        for index, enrollment in enumerate(missing):
            if section_codes and len(section_codes) == 1:
                set_code = section_codes[0]
                assignment_method = PersonalizedAnswerSheetAssignment.AssignmentMethod.SECTION
            elif initial:
                set_code = (
                    start_set
                    if index % 2 == 0
                    else (code_b if start_set == code_a else code_a)
                )
                assignment_method = (
                    PersonalizedAnswerSheetAssignment.AssignmentMethod.INITIAL_BALANCED
                )
            else:
                if counts[code_a] < counts[code_b]:
                    set_code = code_a
                elif counts[code_b] < counts[code_a]:
                    set_code = code_b
                else:
                    tie = cls._tie_set(
                        domain=cls.LATE_TIE_DOMAIN,
                        revision_id=release.generation_revision_id,
                        offering_id=offering.id,
                        enrollment_id=enrollment.id,
                    )
                if counts[code_a] == counts[code_b]:
                    set_code = code_a if tie == "A" else code_b
                assignment_method = (
                    PersonalizedAnswerSheetAssignment.AssignmentMethod.LATE_BALANCED
                )
            if section_codes:
                assignment_method = PersonalizedAnswerSheetAssignment.AssignmentMethod.SECTION
            assignment = PersonalizedAnswerSheetAssignment(
                generation_revision=release.generation_revision,
                enrollment=enrollment,
                course_offering=offering,
                set_code=set_code,
                assignment_method=assignment_method,
                algorithm_version=cls.SECTION_PAIR_ALGORITHM_VERSION if section_codes and len(section_codes) == 2 else cls.ALGORITHM_VERSION,
                assigned_by=actor,
            )
            assignment.full_clean()
            assignment.save()
            created.append(assignment)
            counts[set_code] += 1
        AuditService.log_event(
            action="DE_PERSONALIZED_SHEET_ASSIGNMENTS_PREPARED",
            portal="FACULTY",
            entity_type="QuestionnairePrintRelease",
            entity_id=release.id,
            actor=actor,
            tenant=release.cycle_course.cycle.tenant_id,
            campus=offering.campus_id,
            metadata={
                "release_id": release.id,
                "revision_id": release.generation_revision_id,
                "course_offering_id": offering.id,
                "algorithm_version": cls.SECTION_PAIR_ALGORITHM_VERSION if section_codes and len(section_codes) == 2 else cls.ALGORITHM_VERSION,
                "created_count": len(created),
                "existing_count": len(active_existing),
                "set_a_total": counts[code_a],
                "set_b_total": counts[code_b],
                **({("section_set_code" if len(section_codes) == 1 else "section_set_codes"): section_codes[0] if len(section_codes) == 1 else section_codes,
                    "section_set_total": sum(counts[c] for c in section_codes)} if section_codes else {}),
            },
            request=request,
        )
        return {
            "created_count": len(created),
            "existing_count": len(active_existing),
            "set_a_total": counts[code_a],
            "set_b_total": counts[code_b],
            **({("section_set_code" if len(section_codes) == 1 else "section_set_codes"): section_codes[0] if len(section_codes) == 1 else section_codes,
                    "section_set_total": sum(counts[c] for c in section_codes)} if section_codes else {}),
        }

    @classmethod
    def print_context(
        cls,
        *,
        contribution,
        release_id,
        offering_id,
        set_filter,
        actor,
        request=None,
        paper_size=None,
    ):
        normalized_filter = (set_filter or "").upper()
        if normalized_filter != "ALL" and not valid_set_code(normalized_filter):
            raise Http404("Personalized answer-sheet filter does not exist.")
        release, item_count = cls._release_context(
            contribution=contribution,
            release_id=release_id,
        )
        offering = (
            CourseOffering.objects.select_related(
                "campus", "course", "section", "section__program", "program"
            )
            .filter(pk=offering_id)
            .first()
        )
        if offering is None:
            raise PermissionDenied("Course offering is unavailable.")
        assigned_codes = cls._require_authorized_offering(
            contribution=contribution,
            release=release,
            offering=offering,
        )
        if release.generation_revision.set_policy_snapshot:
            if len(assigned_codes) == 2 and normalized_filter in {"A", "B"}:
                normalized_filter = assigned_codes["AB".index(normalized_filter)]
            if normalized_filter not in {"ALL", *assigned_codes}:
                raise PermissionDenied("The requested set is not assigned to this academic section.")
        enrollments = list(cls._active_enrollments(offering=offering))
        if not enrollments:
            raise PermissionDenied("No active students are available for printing.")
        assignments = list(
            PersonalizedAnswerSheetAssignment.objects.filter(
                generation_revision=release.generation_revision,
                course_offering=offering,
                enrollment_id__in=[row.id for row in enrollments],
            ).select_related("enrollment__student")
        )
        assignment_by_enrollment = {row.enrollment_id: row for row in assignments}
        if len(assignment_by_enrollment) != len(enrollments):
            raise PermissionDenied(
                "Prepare assignments for every active student before printing."
            )
        saved_sets = {row.generated_set.set_code: row for row in release.generation_revision.section_set_assignments.select_related("generated_set")}
        sheets = []
        for enrollment in enrollments:
            assignment = assignment_by_enrollment[enrollment.id]
            if release.generation_revision.set_policy_snapshot and assignment.set_code not in assigned_codes:
                raise PermissionDenied("The student sheet does not match the exact saved section set.")
            if normalized_filter != "ALL" and assignment.set_code != normalized_filter:
                continue
            sheets.append(
                {
                    "student_number": enrollment.student.student_no,
                    "student_name": cls._student_name(enrollment.student),
                    "set_code": assignment.set_code,
                    "set_version": saved_sets[assignment.set_code].generated_set.display_version if assigned_codes else assignment.set_code,
                }
            )
        paper = _questionnaire_paper_context(paper_size)
        accessed_at = timezone.now().astimezone(MANILA_TIMEZONE)
        AuditService.log_event(
            action="DE_PERSONALIZED_SHEET_BATCH_RENDERED",
            portal="FACULTY",
            entity_type="QuestionnairePrintRelease",
            entity_id=release.id,
            actor=actor,
            tenant=release.cycle_course.cycle.tenant_id,
            campus=offering.campus_id,
            metadata={
                "release_id": release.id,
                "revision_id": release.generation_revision_id,
                "course_offering_id": offering.id,
                "set_filter": normalized_filter,
                "page_count": len(sheets),
                "paper_size": paper["paper_size"],
                "accessed_at": accessed_at,
            },
            request=request,
        )
        answer_columns = tuple(
            tuple(
                {"number": number, "active": number <= item_count}
                for number in range(start, start + 25)
            )
            for start in (1, 26, 51)
        )
        cycle = release.cycle_course.cycle
        tenant = cycle.tenant
        return {
            "contribution": contribution,
            "release": release,
            "revision_number": release.generation_revision.revision_number,
            "school_name": SystemSettingService.get(
                "PRINT_HEADER_SCHOOL_NAME",
                tenant_id=tenant.id,
                default=tenant.name,
            ),
            "course_code": offering.course.code,
            "course_title": offering.course.title,
            "section": saved_sets[assigned_codes[0]].section_label_snapshot if assigned_codes else offering.section.code,
            "academic_year": cycle.academic_year.name,
            "term": cycle.term.name,
            "exam_period": cycle.get_exam_period_display(),
            "exam_period_code": cycle.exam_period,
            "item_count": item_count,
            "set_filter": normalized_filter,
            "sheets": sheets,
            "answer_columns": answer_columns,
            "accessed_at": accessed_at,
            **paper,
        }

from __future__ import annotations

from collections import Counter
from zoneinfo import ZoneInfo

from django.core.exceptions import PermissionDenied
from django.utils import timezone

from apps.core.services.audit import AuditService

from .generation_readiness import AUTOMATIC_LOGICAL_IDENTITY_VERSION
from .models import (
    ExamGenerationRevision,
    ExaminationCycle,
    GeneratedExamItem,
    GeneratedExamSet,
    GenerationSourceAuditSnapshot,
    Question,
)
from .services import DepartmentalExamAuthorizationService


MANILA_TIMEZONE = ZoneInfo("Asia/Manila")


class GenerationReportIntegrityError(PermissionDenied):
    """A content-safe failure for inconsistent confidential snapshots."""


class GenerationReportingAuthorizationService:
    @staticmethod
    def _is_current_automatic_revision(revision):
        return bool(
            revision.current_marker == 1
            and revision.status == ExamGenerationRevision.Status.GENERATED
        )

    @classmethod
    def can_manage(cls, *, user, revision):
        if (
            revision.cycle_course.cycle.processing_mode
            != ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        ):
            return False
        return DepartmentalExamAuthorizationService.has_automatic_course_permission(
            user=user,
            cycle_course=revision.cycle_course,
            permissions=(
                DepartmentalExamAuthorizationService.MANAGE_GENERATION_PERMISSION,
            ),
        )

    @classmethod
    def require_view(cls, *, user, revision):
        if (
            revision.cycle_course.cycle.processing_mode
            == ExaminationCycle.ProcessingMode.MANUAL_REVIEW
        ):
            DepartmentalExamAuthorizationService.require_generated_exam_view(
                user=user,
                cycle_course=revision.cycle_course,
            )
            return
        if cls.can_manage(user=user, revision=revision):
            return
        DepartmentalExamAuthorizationService.require_automatic_course_permission(
            user=user,
            cycle_course=revision.cycle_course,
            permissions=(
                DepartmentalExamAuthorizationService.VIEW_GENERATED_PERMISSION,
            ),
        )
        if not cls._is_current_automatic_revision(revision):
            raise PermissionDenied(
                "Historical automatic generation revisions require management authority."
            )

    @classmethod
    def can_print(cls, *, user, revision):
        try:
            cls.require_print(user=user, revision=revision)
        except PermissionDenied:
            return False
        return True

    @classmethod
    def require_print(cls, *, user, revision):
        if (
            revision.cycle_course.cycle.processing_mode
            == ExaminationCycle.ProcessingMode.MANUAL_REVIEW
        ):
            DepartmentalExamAuthorizationService.require_generated_exam_view(
                user=user,
                cycle_course=revision.cycle_course,
            )
            return
        if cls.can_manage(user=user, revision=revision):
            return
        DepartmentalExamAuthorizationService.require_automatic_course_permission(
            user=user,
            cycle_course=revision.cycle_course,
            permissions=(
                DepartmentalExamAuthorizationService.PRINT_GENERATED_PERMISSION,
            ),
        )
        if not cls._is_current_automatic_revision(revision):
            raise PermissionDenied(
                "Historical automatic generation revisions require management authority."
            )


class GenerationSelectionAuditReportService:
    FILTERS = (
        ("all", "All"),
        ("selected", "Selected"),
        ("set-a", "Set A"),
        ("set-b", "Set B"),
        ("both", "Selected in Both"),
        ("not-selected", "Not Selected"),
        ("duplicate", "Duplicate/Equivalent"),
    )
    FILTER_CODES = {code for code, _label in FILTERS}
    DIFFICULTY_LABELS = dict(Question.Difficulty.choices)

    @staticmethod
    def _selection_items(revision):
        return list(
            GeneratedExamItem.objects.filter(
                generated_set__generation_revision=revision
            )
            .values(
                "source_question_id",
                "source_question_revision",
                "source_contributor_name_snapshot",
                "campus_code_snapshot",
                "campus_name_snapshot",
                "difficulty_snapshot",
                "question_text_snapshot",
                "correct_answer_snapshot",
                "generated_set__set_code",
                "position",
            )
            .order_by("generated_set__set_code", "position")
        )

    @staticmethod
    def _selection_maps(items):
        positions = {"A": {}, "B": {}}
        for item in items:
            positions[item["generated_set__set_code"]][
                item["source_question_id"]
            ] = item["position"]
        return positions

    @staticmethod
    def _context_label(context_rows):
        labels = []
        seen = set()
        for context in context_rows or ():
            section_code = str(context.get("section_code") or "").strip()
            section_name = str(context.get("section_name") or "").strip()
            if section_code:
                label = section_code
                if section_name and section_name.casefold() != section_code.casefold():
                    label = f"{section_code} - {section_name}"
            else:
                label = f"Offering #{context.get('offering_id')}"
            if label not in seen:
                seen.add(label)
                labels.append(label)
        return ", ".join(labels) if labels else "Context unavailable"

    @staticmethod
    def _matches_filter(row, filter_code):
        if filter_code == "all":
            return True
        if filter_code == "selected":
            return row["selected_a"] or row["selected_b"]
        if filter_code == "set-a":
            return row["selected_a"]
        if filter_code == "set-b":
            return row["selected_b"]
        if filter_code == "both":
            return row["selected_a"] and row["selected_b"]
        if filter_code == "not-selected":
            return not row["selected_a"] and not row["selected_b"]
        if filter_code == "duplicate":
            return row["is_equivalent"]
        return False

    @classmethod
    def _legacy_rows(cls, *, items, positions, filter_code):
        by_source = {}
        for item in items:
            by_source.setdefault(item["source_question_id"], item)
        rows = []
        for source_id, item in sorted(by_source.items()):
            row = {
                "question": item["question_text_snapshot"],
                "contributor": item["source_contributor_name_snapshot"],
                "campus": (
                    f"{item['campus_code_snapshot']} - "
                    f"{item['campus_name_snapshot']}"
                ),
                "context": "Source-pool context unavailable",
                "difficulty": cls.DIFFICULTY_LABELS.get(
                    item["difficulty_snapshot"], item["difficulty_snapshot"]
                ),
                "correct_answer": item["correct_answer_snapshot"],
                "source_reference": (
                    f"Q{source_id} r{item['source_question_revision']}"
                ),
                "set_a_position": positions["A"].get(source_id),
                "set_b_position": positions["B"].get(source_id),
                "selected_a": source_id in positions["A"],
                "selected_b": source_id in positions["B"],
                "is_equivalent": False,
                "equivalence_status": "Unavailable for legacy revision",
                "eligible": True,
            }
            if cls._matches_filter(row, filter_code):
                rows.append(row)
        return rows

    @classmethod
    def build_context(cls, *, revision, filter_code):
        normalized_filter = (filter_code or "all").strip().lower()
        if normalized_filter not in cls.FILTER_CODES:
            normalized_filter = "all"
        items = cls._selection_items(revision)
        positions = cls._selection_maps(items)
        selected_a_ids = set(positions["A"])
        selected_b_ids = set(positions["B"])
        selected_ids = selected_a_ids | selected_b_ids
        overlap = len(selected_a_ids & selected_b_ids)
        set_counts = {
            "A": len(selected_a_ids),
            "B": len(selected_b_ids),
        }
        audit_snapshot = (
            GenerationSourceAuditSnapshot.objects.filter(
                generation_revision=revision
            )
            .prefetch_related("question_snapshots")
            .first()
        )
        if audit_snapshot is None:
            rows = cls._legacy_rows(
                items=items,
                positions=positions,
                filter_code=normalized_filter,
            )
            summary = {
                "submitted": None,
                "unique_logical": None,
                "redundant": None,
                "set_a": set_counts["A"],
                "set_b": set_counts["B"],
                "overlap": overlap,
                "selected_unique": len(selected_ids),
                "not_selected": None,
            }
            return cls._base_context(
                revision=revision,
                filter_code=normalized_filter,
                rows=rows,
                summary=summary,
                audit_available=False,
            )

        source_rows = list(
            audit_snapshot.question_snapshots.all().order_by(
                "source_question_id_snapshot"
            )
        )
        source_ids = {row.source_question_id_snapshot for row in source_rows}
        if not selected_ids.issubset(source_ids):
            raise GenerationReportIntegrityError(
                "The generation source audit snapshot is incomplete."
            )
        eligible_rows = [row for row in source_rows if row.eligible_for_generation]
        automatic_identity = (
            audit_snapshot.logical_identity_version
            == AUTOMATIC_LOGICAL_IDENTITY_VERSION
        )
        logical_keys = (
            {row.normalized_fingerprint for row in eligible_rows}
            if automatic_identity
            else {str(row.source_question_id_snapshot) for row in eligible_rows}
        )
        if (
            audit_snapshot.submitted_count != len(source_rows)
            or audit_snapshot.eligible_count != len(eligible_rows)
            or audit_snapshot.unique_logical_count != len(logical_keys)
            or audit_snapshot.redundant_copy_count
            != len(eligible_rows) - len(logical_keys)
        ):
            raise GenerationReportIntegrityError(
                "The generation source audit counts are inconsistent."
            )

        eligible_group_counts = Counter(
            row.normalized_fingerprint for row in eligible_rows
        )
        equivalent_fingerprints = sorted(
            fingerprint
            for fingerprint, count in eligible_group_counts.items()
            if count > 1
        )
        group_labels = {
            fingerprint: f"EQ-{index:03d}"
            for index, fingerprint in enumerate(equivalent_fingerprints, start=1)
        }
        selected_fingerprints = {
            row.normalized_fingerprint
            for row in source_rows
            if row.source_question_id_snapshot in selected_ids
        }
        selected_unique = (
            len(selected_fingerprints) if automatic_identity else len(selected_ids)
        )
        rows = []
        for source in source_rows:
            source_id = source.source_question_id_snapshot
            selected_a = source_id in selected_a_ids
            selected_b = source_id in selected_b_ids
            group_label = group_labels.get(source.normalized_fingerprint)
            is_equivalent = bool(group_label)
            if not source.eligible_for_generation:
                equivalence_status = "Excluded: " + source.exclusion_code.replace(
                    "_", " "
                ).title()
            elif not is_equivalent:
                equivalence_status = "Unique"
            elif not automatic_identity:
                equivalence_status = (
                    f"Equivalent text; Manual Review did not deduplicate ({group_label})"
                )
            elif selected_a or selected_b:
                equivalence_status = f"Selected representative ({group_label})"
            elif source.normalized_fingerprint in selected_fingerprints:
                equivalence_status = f"Equivalent copy not selected ({group_label})"
            else:
                equivalence_status = f"Unselected equivalent group ({group_label})"
            row = {
                "question": source.question_text_snapshot,
                "contributor": source.contributor_name_snapshot,
                "campus": (
                    f"{source.campus_code_snapshot} - {source.campus_name_snapshot}"
                ),
                "context": cls._context_label(
                    source.assignment_context_snapshot
                ),
                "difficulty": cls.DIFFICULTY_LABELS.get(
                    source.difficulty_snapshot,
                    source.difficulty_snapshot,
                ),
                "correct_answer": source.correct_answer_snapshot,
                "source_reference": (
                    f"Q{source.source_question_id_snapshot} "
                    f"r{source.source_question_revision}"
                ),
                "set_a_position": positions["A"].get(source_id),
                "set_b_position": positions["B"].get(source_id),
                "selected_a": selected_a,
                "selected_b": selected_b,
                "is_equivalent": is_equivalent,
                "equivalence_status": equivalence_status,
                "eligible": source.eligible_for_generation,
            }
            if cls._matches_filter(row, normalized_filter):
                rows.append(row)
        summary = {
            "submitted": audit_snapshot.submitted_count,
            "unique_logical": audit_snapshot.unique_logical_count,
            "redundant": audit_snapshot.redundant_copy_count,
            "set_a": set_counts["A"],
            "set_b": set_counts["B"],
            "overlap": overlap,
            "selected_unique": selected_unique,
            "not_selected": audit_snapshot.unique_logical_count
            - selected_unique,
        }
        return cls._base_context(
            revision=revision,
            filter_code=normalized_filter,
            rows=rows,
            summary=summary,
            audit_available=True,
        )

    @classmethod
    def _base_context(
        cls, *, revision, filter_code, rows, summary, audit_available
    ):
        cycle = revision.cycle_course.cycle
        return {
            "revision": revision,
            "cycle_course": revision.cycle_course,
            "academic_year": cycle.academic_year.name,
            "term": cycle.term.name,
            "exam_period": cycle.get_exam_period_display(),
            "filters": cls.FILTERS,
            "active_filter": filter_code,
            "rows": rows,
            "summary": summary,
            "audit_available": audit_available,
            "printed_at": timezone.now().astimezone(MANILA_TIMEZONE),
        }


class GenerationAnswerKeyService:
    @staticmethod
    def build_context(*, revision, set_code):
        normalized_set = (set_code or "").strip().upper()
        if normalized_set not in GeneratedExamSet.SetCode.values:
            raise GenerationReportIntegrityError(
                "The requested generated examination set is unavailable."
            )
        generated_set = (
            GeneratedExamSet.objects.filter(
                generation_revision=revision,
                set_code=normalized_set,
            )
            .only("id", "set_code", "item_count", "generation_revision_id")
            .first()
        )
        if generated_set is None:
            raise GenerationReportIntegrityError(
                "The requested generated examination set is unavailable."
            )
        items = list(
            generated_set.items.order_by("position").values(
                "position", "correct_answer_snapshot"
            )
        )
        if (
            len(items) != generated_set.item_count
            or [row["position"] for row in items]
            != list(range(1, generated_set.item_count + 1))
        ):
            raise GenerationReportIntegrityError(
                "The generated examination answer-key snapshot is incomplete."
            )
        cycle = revision.cycle_course.cycle
        return {
            "revision": revision,
            "cycle_course": revision.cycle_course,
            "academic_year": cycle.academic_year.name,
            "term": cycle.term.name,
            "exam_period": cycle.get_exam_period_display(),
            "set_code": normalized_set,
            "items": items,
            "printed_at": timezone.now().astimezone(MANILA_TIMEZONE),
        }


def audit_generation_report_access(
    *, revision, actor, request, action, metadata=None
):
    AuditService.log_event(
        action=action,
        portal="ADMIN",
        entity_type="ExamGenerationRevision",
        entity_id=revision.id,
        actor=actor,
        tenant=revision.cycle_course.cycle.tenant_id,
        metadata={
            "cycle_id": revision.cycle_course.cycle_id,
            "cycle_course_id": revision.cycle_course_id,
            "revision_id": revision.id,
            "revision_number": revision.revision_number,
            **(metadata or {}),
        },
        request=request,
    )

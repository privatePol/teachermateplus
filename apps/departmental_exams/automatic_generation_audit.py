from __future__ import annotations

import hashlib
import json
from collections import Counter

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Prefetch
from django.http import Http404
from django.utils import timezone

from apps.core.services.audit import AuditService

from .approval_services import GeneratedExamIntegrityService
from .generation_readiness import AUTOMATIC_LOGICAL_IDENTITY_VERSION
from .models import (
    AutomaticGenerationAuditRun,
    ExamGenerationRevision,
    ExaminationCycle,
    GeneratedExamItem,
    GeneratedExamSet,
    GenerationSourceAuditSnapshot,
)
from .services import DepartmentalExamAuthorizationService


class AutomaticGenerationAuditService:
    CHECK_VERSION = "automatic-audit-v1"

    @staticmethod
    def _digest(value):
        payload = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _finding(code, status, message, **metrics):
        finding = {
            "code": code,
            "status": status,
            "message": message,
        }
        if metrics:
            finding["metrics"] = metrics
        return finding

    @staticmethod
    def _normalize_quota(value):
        if not isinstance(value, dict) or not value:
            return None
        normalized = {}
        for key, amount in value.items():
            try:
                parsed = int(amount)
            except (TypeError, ValueError):
                return None
            if parsed < 0:
                return None
            normalized[str(key)] = parsed
        return normalized

    @staticmethod
    def _distribution_matches(*, items, expected, field):
        if expected is None:
            return False, {}
        actual_counter = Counter(str(getattr(item, field)) for item in items)
        actual = {key: actual_counter.get(key, 0) for key in expected}
        unexpected = {
            key: value
            for key, value in actual_counter.items()
            if key not in expected and value
        }
        return actual == expected and not unexpected, {
            **actual,
            **unexpected,
        }

    @staticmethod
    def _format_distribution(value, order=None):
        if value is None:
            return "unavailable"
        keys = list(order or ())
        keys.extend(sorted(set(value) - set(keys)))
        return "/".join(str(value.get(key, 0)) for key in keys)

    @classmethod
    def _item_digest_valid(cls, item):
        choices = item.choices_snapshot
        if (
            item.source_question_revision < 1
            or not GeneratedExamIntegrityService.SHA256_RE.fullmatch(
                item.source_question_digest or ""
            )
            or not (item.question_text_snapshot or "").strip()
            or not isinstance(choices, list)
            or len(choices) != 4
            or any(not str(choice).strip() for choice in choices)
            or item.correct_answer_snapshot not in {"A", "B", "C", "D"}
        ):
            return False
        expected = cls._digest(
            {
                "source_id": item.source_question_id,
                "revision": item.source_question_revision,
                "question_text": item.question_text_snapshot,
                "choices": choices,
                "correct_answer": item.correct_answer_snapshot,
                "difficulty": item.difficulty_snapshot,
            }
        )
        return expected == item.source_question_digest

    @classmethod
    def _source_digest_valid(cls, source):
        choices = source.choices_snapshot
        if (
            source.source_question_revision < 1
            or not GeneratedExamIntegrityService.SHA256_RE.fullmatch(
                source.source_question_digest or ""
            )
            or not (source.question_text_snapshot or "").strip()
            or not isinstance(choices, list)
            or len(choices) != 4
            or any(not str(choice).strip() for choice in choices)
            or source.correct_answer_snapshot not in {"A", "B", "C", "D"}
        ):
            return False
        expected = cls._digest(
            {
                "source_id": source.source_question_id_snapshot,
                "revision": source.source_question_revision,
                "question_text": source.question_text_snapshot,
                "choices": choices,
                "correct_answer": source.correct_answer_snapshot,
                "difficulty": source.difficulty_snapshot,
            }
        )
        return expected == source.source_question_digest

    @classmethod
    def _build_findings(cls, *, revision):
        generated_sets = list(
            GeneratedExamSet.objects.filter(generation_revision=revision)
            .prefetch_related(
                Prefetch(
                    "items",
                    queryset=GeneratedExamItem.objects.order_by("position", "id"),
                )
            )
            .order_by("set_code")
        )
        set_by_code = {row.set_code: row for row in generated_sets}
        items_by_code = {
            code: list(set_by_code[code].items.all()) if code in set_by_code else []
            for code in (GeneratedExamSet.SetCode.A, GeneratedExamSet.SetCode.B)
        }
        all_items = items_by_code["A"] + items_by_code["B"]
        findings = []
        expected_count = revision.final_item_count_snapshot

        for code in ("A", "B"):
            generated_set = set_by_code.get(code)
            items = items_by_code[code]
            declared_count = generated_set.item_count if generated_set else 0
            count_ok = bool(
                generated_set
                and declared_count == expected_count
                and len(items) == expected_count
            )
            findings.append(
                cls._finding(
                    f"SET_{code}_ITEM_COUNT",
                    "PASS" if count_ok else "FAIL",
                    (
                        f"Set {code} contains {len(items)}/{expected_count} items."
                        if count_ok
                        else f"Set {code} item count is incomplete: {len(items)}/{expected_count}."
                    ),
                    actual=len(items),
                    expected=expected_count,
                    declared=declared_count,
                )
            )
            positions = [item.position for item in items]
            positions_ok = bool(
                generated_set
                and positions == list(range(1, expected_count + 1))
            )
            findings.append(
                cls._finding(
                    f"SET_{code}_POSITIONS",
                    "PASS" if positions_ok else "FAIL",
                    (
                        f"Set {code} positions are unique and continuous."
                        if positions_ok
                        else f"Set {code} positions are incomplete or non-continuous."
                    ),
                    actual_count=len(positions),
                    expected_count=expected_count,
                )
            )

        try:
            source_audit = revision.source_audit_snapshot
        except GenerationSourceAuditSnapshot.DoesNotExist:
            source_audit = None
        source_rows = (
            list(
                source_audit.question_snapshots.all().order_by(
                    "source_question_id_snapshot"
                )
            )
            if source_audit is not None
            else []
        )
        source_by_id = {
            row.source_question_id_snapshot: row for row in source_rows
        }

        for code in ("A", "B"):
            items = items_by_code[code]
            if source_audit is None:
                findings.append(
                    cls._finding(
                        f"SET_{code}_LOGICAL_UNIQUENESS",
                        "WARNING",
                        (
                            f"Set {code} logical-identity check unavailable: "
                            "historical source-pool evidence is unavailable."
                        ),
                        check_unavailable=True,
                    )
                )
                continue
            logical_keys = [
                source_by_id[item.source_question_id].normalized_fingerprint
                for item in items
                if item.source_question_id in source_by_id
            ]
            complete = len(logical_keys) == len(items)
            unique = complete and len(logical_keys) == len(set(logical_keys))
            findings.append(
                cls._finding(
                    f"SET_{code}_LOGICAL_UNIQUENESS",
                    "PASS" if unique else "FAIL",
                    (
                        f"Set {code} contains no duplicate logical questions."
                        if unique
                        else f"Set {code} contains duplicate or missing logical-identity evidence."
                    ),
                    item_count=len(items),
                    logical_count=len(set(logical_keys)),
                )
            )

        for code in ("A", "B"):
            generated_set = set_by_code.get(code)
            items = items_by_code[code]
            expected_difficulty = cls._normalize_quota(
                generated_set.difficulty_quotas_snapshot
                if generated_set
                else None
            )
            difficulty_ok, actual_difficulty = cls._distribution_matches(
                items=items,
                expected=expected_difficulty,
                field="difficulty_snapshot",
            )
            difficulty_order = ("EASY", "MODERATE", "DIFFICULT")
            findings.append(
                cls._finding(
                    f"SET_{code}_DIFFICULTY_DISTRIBUTION",
                    "PASS" if difficulty_ok else "FAIL",
                    (
                        f"Set {code} difficulty distribution matches "
                        f"{cls._format_distribution(expected_difficulty, difficulty_order)}."
                        if difficulty_ok
                        else f"Set {code} difficulty distribution does not match its persisted target."
                    ),
                    actual=actual_difficulty,
                    expected=expected_difficulty or {},
                )
            )
            expected_campus = cls._normalize_quota(
                generated_set.campus_quotas_snapshot if generated_set else None
            )
            campus_field = (
                "source_campus_id"
                if expected_campus
                and all(key.isdigit() for key in expected_campus)
                else "campus_code_snapshot"
            )
            campus_ok, actual_campus = cls._distribution_matches(
                items=items,
                expected=expected_campus,
                field=campus_field,
            )
            findings.append(
                cls._finding(
                    f"SET_{code}_CAMPUS_ALLOCATION",
                    "PASS" if campus_ok else "FAIL",
                    (
                        f"Set {code} campus allocation matches its persisted target."
                        if campus_ok
                        else f"Set {code} campus allocation does not match its persisted target."
                    ),
                    actual=actual_campus,
                    expected=expected_campus or {},
                )
            )

        source_ids_a = {item.source_question_id for item in items_by_code["A"]}
        source_ids_b = {item.source_question_id for item in items_by_code["B"]}
        overlap = len(source_ids_a & source_ids_b)
        overlap_ok = (
            "A" in set_by_code
            and "B" in set_by_code
            and overlap == revision.minimum_overlap
        )
        findings.append(
            cls._finding(
                "SET_OVERLAP",
                "PASS" if overlap_ok else "FAIL",
                (
                    f"Set A/B overlap matches the persisted minimum of {revision.minimum_overlap}."
                    if overlap_ok
                    else "Set A/B overlap does not match the persisted revision minimum."
                ),
                actual=overlap,
                expected=revision.minimum_overlap,
            )
        )

        correct_count = sum(
            item.correct_answer_snapshot in {"A", "B", "C", "D"}
            for item in all_items
        )
        answers_ok = correct_count == len(all_items)
        findings.append(
            cls._finding(
                "CORRECT_ANSWER_COMPLETENESS",
                "PASS" if answers_ok else "FAIL",
                (
                    "Every generated item has a complete correct-answer snapshot."
                    if answers_ok
                    else "One or more generated items has an incomplete correct-answer snapshot."
                ),
                complete=correct_count,
                total=len(all_items),
            )
        )

        item_digest_count = sum(cls._item_digest_valid(item) for item in all_items)
        revision_header_ok = bool(
            revision.final_item_count_snapshot >= 1
            and (revision.algorithm_version or "").strip()
            and GeneratedExamIntegrityService.SHA256_RE.fullmatch(
                revision.source_input_fingerprint or ""
            )
        )
        digest_ok = revision_header_ok and item_digest_count == len(all_items)
        findings.append(
            cls._finding(
                "REVISION_SNAPSHOT_INTEGRITY",
                "PASS" if digest_ok else "FAIL",
                (
                    "Revision and generated-item snapshot digests are internally valid."
                    if digest_ok
                    else "Revision or generated-item snapshot integrity evidence is invalid."
                ),
                valid_item_digests=item_digest_count,
                total_items=len(all_items),
            )
        )

        if source_audit is None:
            for check_code, message in (
                (
                    "ELIGIBLE_SUBMITTED_SOURCES",
                    "Eligible Final Submitted source check unavailable: historical source-pool evidence is unavailable.",
                ),
                (
                    "SOURCE_AUDIT_COUNTS",
                    "Source-audit count check unavailable for this legacy revision.",
                ),
                (
                    "SOURCE_MEMBERSHIP_CONSISTENCY",
                    "Selected membership check unavailable for this legacy revision.",
                ),
                (
                    "SOURCE_AUDIT_DIGESTS",
                    "Source-audit digest check unavailable for this legacy revision.",
                ),
            ):
                findings.append(
                    cls._finding(
                        check_code,
                        "WARNING",
                        message,
                        check_unavailable=True,
                    )
                )
        else:
            selected_sources = [
                source_by_id.get(item.source_question_id) for item in all_items
            ]
            eligible_ok = all(
                source is not None and source.eligible_for_generation
                for source in selected_sources
            )
            findings.append(
                cls._finding(
                    "ELIGIBLE_SUBMITTED_SOURCES",
                    "PASS" if eligible_ok else "FAIL",
                    (
                        "Every selected item came from an eligible Final Submitted source snapshot."
                        if eligible_ok
                        else "One or more selected items lacks eligible Final Submitted source evidence."
                    ),
                    selected_items=len(all_items),
                    eligible_items=sum(
                        source is not None and source.eligible_for_generation
                        for source in selected_sources
                    ),
                )
            )
            eligible_rows = [row for row in source_rows if row.eligible_for_generation]
            logical_count = len(
                {row.normalized_fingerprint for row in eligible_rows}
            )
            counts_ok = bool(
                source_audit.logical_identity_version
                == AUTOMATIC_LOGICAL_IDENTITY_VERSION
                and source_audit.submitted_count == len(source_rows)
                and source_audit.eligible_count == len(eligible_rows)
                and source_audit.unique_logical_count == logical_count
                and source_audit.redundant_copy_count
                == len(eligible_rows) - logical_count
            )
            findings.append(
                cls._finding(
                    "SOURCE_AUDIT_COUNTS",
                    "PASS" if counts_ok else "FAIL",
                    (
                        "Source-audit snapshot counts are internally consistent."
                        if counts_ok
                        else "Source-audit snapshot counts or identity version are inconsistent."
                    ),
                    submitted=len(source_rows),
                    eligible=len(eligible_rows),
                    unique_logical=logical_count,
                    redundant=len(eligible_rows) - logical_count,
                )
            )
            membership_ok = all(
                source is not None
                and source.source_question_revision == item.source_question_revision
                and source.source_question_digest == item.source_question_digest
                and source.campus_id_snapshot == item.source_campus_id
                for item, source in zip(all_items, selected_sources)
            )
            findings.append(
                cls._finding(
                    "SOURCE_MEMBERSHIP_CONSISTENCY",
                    "PASS" if membership_ok else "FAIL",
                    (
                        "Selected membership matches the exact source-question snapshots."
                        if membership_ok
                        else "Selected membership does not match the source-question snapshots."
                    ),
                    matched=sum(
                        source is not None
                        and source.source_question_revision
                        == item.source_question_revision
                        and source.source_question_digest
                        == item.source_question_digest
                        and source.campus_id_snapshot == item.source_campus_id
                        for item, source in zip(all_items, selected_sources)
                    ),
                    total=len(all_items),
                )
            )
            valid_source_digests = sum(
                cls._source_digest_valid(source) for source in source_rows
            )
            source_digests_ok = valid_source_digests == len(source_rows)
            findings.append(
                cls._finding(
                    "SOURCE_AUDIT_DIGESTS",
                    "PASS" if source_digests_ok else "FAIL",
                    (
                        "Source-audit question digests are internally valid."
                        if source_digests_ok
                        else "One or more source-audit question digests is invalid."
                    ),
                    valid=valid_source_digests,
                    total=len(source_rows),
                )
            )

        return findings, {
            "set_a_items": len(items_by_code["A"]),
            "set_b_items": len(items_by_code["B"]),
            "overlap_count": overlap,
        }

    @classmethod
    @transaction.atomic
    def run(cls, *, revision_id, tenant_id, actor, request=None):
        try:
            revision = (
                ExamGenerationRevision.objects.select_for_update()
                .select_related(
                    "cycle_course__cycle",
                    "cycle_course__course",
                )
                .get(
                    pk=revision_id,
                    cycle_course__cycle__tenant_id=tenant_id,
                )
            )
        except ExamGenerationRevision.DoesNotExist as exc:
            raise Http404(
                "Generated revision does not exist in the active tenant."
            ) from exc
        if (
            revision.cycle_course.cycle.processing_mode
            != ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        ):
            raise PermissionDenied("Automatic audit is available only for automatic generation.")
        DepartmentalExamAuthorizationService.require_generation_audit(
            user=actor,
            cycle_course=revision.cycle_course,
        )
        findings, operational_counts = cls._build_findings(revision=revision)
        status_counts = Counter(row["status"] for row in findings)
        if status_counts["FAIL"]:
            status = AutomaticGenerationAuditRun.Status.FAIL
        elif status_counts["WARNING"]:
            status = AutomaticGenerationAuditRun.Status.WARNING
        else:
            status = AutomaticGenerationAuditRun.Status.PASS
        summary = {
            "total_checks": len(findings),
            "pass_count": status_counts["PASS"],
            "warning_count": status_counts["WARNING"],
            "fail_count": status_counts["FAIL"],
            **operational_counts,
        }
        run = AutomaticGenerationAuditRun.objects.create(
            generation_revision=revision,
            status=status,
            check_version=cls.CHECK_VERSION,
            run_by=actor,
            run_at=timezone.now(),
            findings_snapshot=findings,
            summary_counts_snapshot=summary,
        )
        AuditService.log_event(
            action="DE_AUTOMATIC_GENERATION_AUDIT_RUN",
            portal="ADMIN",
            entity_type="AutomaticGenerationAuditRun",
            entity_id=run.id,
            actor=actor,
            tenant=revision.cycle_course.cycle.tenant_id,
            metadata={
                "audit_run_id": run.id,
                "revision_id": revision.id,
                "revision_number": revision.revision_number,
                "check_version": run.check_version,
                "status": run.status,
                **summary,
            },
            request=request,
        )
        return run


def audit_automatic_generation_result_access(
    *, run, actor, request, printable=False
):
    AuditService.log_event(
        action=(
            "DE_AUTOMATIC_GENERATION_AUDIT_RESULT_PRINTED"
            if printable
            else "DE_AUTOMATIC_GENERATION_AUDIT_RESULT_VIEWED"
        ),
        portal="ADMIN",
        entity_type="AutomaticGenerationAuditRun",
        entity_id=run.id,
        actor=actor,
        tenant=run.generation_revision.cycle_course.cycle.tenant_id,
        metadata={
            "audit_run_id": run.id,
            "revision_id": run.generation_revision_id,
            "revision_number": run.generation_revision.revision_number,
            "check_version": run.check_version,
            "status": run.status,
            "total_checks": run.summary_counts_snapshot.get("total_checks", 0),
            "pass_count": run.summary_counts_snapshot.get("pass_count", 0),
            "warning_count": run.summary_counts_snapshot.get("warning_count", 0),
            "fail_count": run.summary_counts_snapshot.get("fail_count", 0),
        },
        request=request,
    )

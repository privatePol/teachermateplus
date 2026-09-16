"""Snapshot-only Case integrity. No live narrative/member lookup is permitted."""
import hashlib
import json
from collections import Counter

from django.core.exceptions import PermissionDenied, ValidationError

from .scenario_content import canonicalize_scenario_content
from .question_content import canonicalize_question_content

ALGORITHM_VERSION = "automatic-case-v2"
LEGACY_ALGORITHM_VERSION = "automatic-case-v1"
CONTENT_DIGEST_VERSION = "automatic-case-content-v2"
LEGACY_CONTENT_DIGEST_VERSION = "automatic-case-content-v1"
LEGACY_CONTENT_FIELDS = (
    "position", "source_question_id", "source_question_revision", "source_question_digest",
    "question_text_snapshot", "choices_snapshot", "correct_answer_snapshot", "difficulty_snapshot",
    "source_campus_id", "section_id_snapshot", "section_title_snapshot", "section_instructions_snapshot",
    "scenario_id_snapshot", "scenario_revision_snapshot", "scenario_title_snapshot",
    "scenario_stimulus_snapshot", "scenario_content_format_snapshot", "scenario_member_position_snapshot",
)
CONTENT_FIELDS = (
    "position", "source_question_id", "source_question_revision", "source_question_digest",
    "question_text_snapshot", "question_content_format_snapshot", "choices_snapshot", "correct_answer_snapshot", "difficulty_snapshot",
    "source_campus_id", "section_id_snapshot", "section_title_snapshot", "section_instructions_snapshot",
    "scenario_id_snapshot", "scenario_revision_snapshot", "scenario_title_snapshot",
    "scenario_stimulus_snapshot", "scenario_content_format_snapshot", "scenario_member_position_snapshot",
)


def verify_revision_structure(revision):
    if revision.algorithm_version not in {ALGORITHM_VERSION, LEGACY_ALGORITHM_VERSION}:
        return
    from .models import GeneratedExamSet
    generated_sets = list(GeneratedExamSet.objects.filter(generation_revision=revision)
                          .prefetch_related("items").order_by("set_code"))
    if [row.set_code for row in generated_sets] != ["A", "B"]:
        raise PermissionDenied("The structured revision requires complete Set A and Set B snapshots.")
    for generated_set in generated_sets:
        if generated_set.item_count != revision.final_item_count_snapshot:
            raise PermissionDenied("The structured revision item count is inconsistent.")
        verify_structured_set(generated_set, sorted(generated_set.items.all(), key=lambda item: item.position),
                              algorithm_version=revision.algorithm_version)


def content_digest(generated_set, items, *, algorithm_version=ALGORITHM_VERSION):
    if algorithm_version == LEGACY_ALGORITHM_VERSION:
        schema, fields = LEGACY_ALGORITHM_VERSION, LEGACY_CONTENT_FIELDS
    elif algorithm_version == ALGORITHM_VERSION:
        schema, fields = CONTENT_DIGEST_VERSION, CONTENT_FIELDS
    else:
        raise PermissionDenied("Generated Case snapshot format is unsupported.")
    payload = {"schema": schema, "set": generated_set.set_code,
               "count": generated_set.item_count, "sections": generated_set.section_quotas_snapshot,
               "campuses": generated_set.campus_quotas_snapshot,
               "difficulties": generated_set.difficulty_quotas_snapshot,
               "items": [{field: getattr(item, field) for field in fields} for item in items]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def verify_structured_set(generated_set, items, *, algorithm_version):
    if algorithm_version not in {ALGORITHM_VERSION, LEGACY_ALGORITHM_VERSION}:
        return
    items = tuple(items)
    expected_version = LEGACY_CONTENT_DIGEST_VERSION if algorithm_version == LEGACY_ALGORITHM_VERSION else CONTENT_DIGEST_VERSION
    if (not generated_set.structured_content_digest
            or generated_set.structured_content_digest_version != ("" if algorithm_version == LEGACY_ALGORITHM_VERSION else expected_version)
            or content_digest(generated_set, items, algorithm_version=algorithm_version) != generated_set.structured_content_digest
            or len(items) != generated_set.item_count
            or [item.position for item in items] != list(range(1, len(items) + 1))):
        raise PermissionDenied("Generated Case snapshot integrity failed. Do not use this revision.")
    section_counts = Counter(str(item.section_id_snapshot or 0) for item in items)
    if dict(section_counts) != generated_set.section_quotas_snapshot:
        raise PermissionDenied("Generated section quotas do not match the immutable questionnaire.")
    seen_sections, seen_cases = set(), set()
    current_section, current_case = object(), None
    case_position = 0
    case_payload = None
    for item in items:
        if item.question_content_format_snapshot not in {"PLAIN_TEXT", "RICH_HTML_V1"}:
            raise PermissionDenied("Generated question content format is unsupported.")
        if item.question_content_format_snapshot == "RICH_HTML_V1":
            values = (item.question_text_snapshot, *item.choices_snapshot)
            fields = ("question_text", "choice_a", "choice_b", "choice_c", "choice_d")
            try:
                if any(canonicalize_question_content(value, field=field).html != value
                       for field, value in zip(fields, values)):
                    raise PermissionDenied("Generated rich question content is not canonical.")
            except ValidationError as exc:
                raise PermissionDenied("Generated rich question content is invalid.") from exc
        section = item.section_id_snapshot
        if section != current_section:
            if section in seen_sections:
                raise PermissionDenied("Generated sections are not contiguous.")
            seen_sections.add(section)
            current_section = section
            current_case = None
        if item.scenario_id_snapshot is None:
            if (item.scenario_stimulus_snapshot or item.scenario_title_snapshot
                    or item.scenario_revision_snapshot or item.scenario_member_position_snapshot
                    or item.scenario_content_format_snapshot != "PLAIN_TEXT"):
                raise PermissionDenied("Generated standalone question has invalid Case evidence.")
            current_case = None
            continue
        payload = (section, item.scenario_revision_snapshot, item.scenario_title_snapshot,
                   item.scenario_stimulus_snapshot, item.scenario_content_format_snapshot)
        if item.scenario_id_snapshot != current_case:
            if item.scenario_id_snapshot in seen_cases:
                raise PermissionDenied("Generated Case members are not contiguous.")
            current_case = item.scenario_id_snapshot
            seen_cases.add(current_case)
            case_position = 0
            case_payload = payload
            if not item.scenario_revision_snapshot or not item.scenario_stimulus_snapshot.strip():
                raise PermissionDenied("Generated Case narrative is incomplete.")
            if item.scenario_content_format_snapshot == "RICH_HTML_V1":
                try:
                    canonical = canonicalize_scenario_content(item.scenario_stimulus_snapshot).html
                except ValidationError as exc:
                    raise PermissionDenied("Generated rich Case content is invalid.") from exc
                if canonical != item.scenario_stimulus_snapshot:
                    raise PermissionDenied("Generated rich Case content is not canonical.")
            elif item.scenario_content_format_snapshot != "PLAIN_TEXT":
                raise PermissionDenied("Generated Case content format is unsupported.")
        case_position += 1
        if payload != case_payload or item.scenario_member_position_snapshot != case_position:
            raise PermissionDenied("Generated linked MCQ order or narrative evidence is inconsistent.")

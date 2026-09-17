"""Versioned logical identity. Never use these digests as rich snapshot digests."""
import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from html.parser import HTMLParser

from django.core.exceptions import ValidationError
from django.db import connection
from django.db.models import Prefetch, Q

VERSION = "course-question-v4"
# Case narrative identity is deliberately frozen at its v3 representation.
# v4 changes only MCQ rich-content compatibility.
NARRATIVE_IDENTITY_VERSION = "course-question-v3"
DUPLICATE_MESSAGE = "This question is already represented in this course examination. Your input is retained; please revise it or add a different question."
LEGACY_POOL_MESSAGE = "Existing course-pool identities need administrative review before new questions can be saved. Your input is retained. Contact the exam coordinator; do not change Submitted questions."
IMPORT_PLAN_VERSION_MESSAGE = "An unfinished upload has missing or incompatible duplicate-rule versions. Its owner must retry it to discard unpublished work, then upload the file again. Completed questions remain protected. Contact the exam coordinator if this is not your upload."


class IncompatibleImportPlan(ValidationError):
    def __init__(self, batch_id):
        self.batch_id = batch_id  # Internal routing only; never include in Faculty guidance.
        super().__init__(IMPORT_PLAN_VERSION_MESSAGE)


def validate_import_plan_version(batch):
    plan = batch.duplicate_plan
    if not isinstance(plan, dict) or (not plan and batch.committed_rows):
        raise IncompatibleImportPlan(batch.pk)
    if any(not isinstance(decision, dict) or decision.get("identity_version") != VERSION
           for decision in plan.values()):
        raise IncompatibleImportPlan(batch.pk)


class LegacyPoolConflict(ValidationError):
    def __init__(self):
        super().__init__(LEGACY_POOL_MESSAGE)


def normalize_exact(value):
    # Compatibility normalization destroys mathematical distinctions (e.g. x²).
    # Case is also meaningful for variables/units. Similarity remains separate.
    return " ".join(unicodedata.normalize("NFC", value or "").split())


def positional_choices(stem, choices):
    text = " ".join([stem, *choices])
    # Deliberately conservative: false negatives for duplicate rejection are
    # safer than treating label-dependent alternatives as interchangeable.
    return bool(re.search(r"\b\d+[\s.\-‐‑–]*(?:st|nd|rd|th|ˢᵗ|ⁿᵈ|ʳᵈ|ᵗʰ)\b|\b\d+\s*[ºª°](?!\w)|\b(?:[A-Da-d]|[IVX]+)\b|\b(?:above|below|both|neither|either|former|latter|first|second|third|fourth|last|preceding|following|previous|next|only|option|choice|alternative|statement|combination)\w*\b|\b\d+\s*(?:and|or|&|,|/)\s*\d+\b",
                          text, re.IGNORECASE))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def standalone_identity(payload):
    from .question_content import PLAIN_TEXT, identity_tokens, visible_text
    get = payload.get if isinstance(payload, dict) else lambda key: getattr(payload, key)
    content_format = get("content_format") or PLAIN_TEXT
    stem = identity_tokens(get("question_text"), content_format)
    choices = [identity_tokens(get(field), content_format) for field in ("choice_a", "choice_b", "choice_c", "choice_d")]
    ordered = positional_choices(
        visible_text(get("question_text"), content_format),
        [visible_text(get(field), content_format) for field in ("choice_a", "choice_b", "choice_c", "choice_d")],
    )
    return digest([VERSION, stem, "ordered" if ordered else "multiset",
                   choices if ordered else sorted(choices, key=lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":")))])


class _Narrative(HTMLParser):
    # Structural tokens retain cell boundaries, spans, lists and math scripts.
    structural = {"table", "tr", "td", "th", "caption", "ul", "ol", "li", "sup", "sub"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tokens = []
        self.text = []

    def flush(self):
        value = " ".join(unicodedata.normalize("NFC", "".join(self.text)).split())
        if value:
            self.tokens.append(["text", value])
        self.text = []

    def handle_starttag(self, tag, attrs):
        if tag in {"p", "h3", "h4", "br"}:
            self.text.append("\n")
        if tag in self.structural:
            self.flush()
            attrs = dict(attrs)
            values = ([int(attrs.get("rowspan", 1)), int(attrs.get("colspan", 1))]
                      if tag in {"td", "th"} else [int(attrs.get("start", 1))] if tag == "ol" else [])
            self.tokens.append(["start", "td" if tag == "th" else tag, values])

    def handle_endtag(self, tag):
        if tag in {"p", "h3", "h4"}:
            self.text.append("\n")
        if tag in self.structural:
            self.flush()
            self.tokens.append(["end", "td" if tag == "th" else tag])

    def handle_data(self, data):
        self.text.append(data)


def narrative_identity(stimulus, content_format="RICH_HTML_V1"):
    from .scenario_content import canonicalize_scenario_content
    parser = _Narrative()
    if content_format == "RICH_HTML_V1":
        parser.feed(canonicalize_scenario_content(stimulus).html)
        parser.close()
    elif content_format == "PLAIN_TEXT":
        parser.handle_data(stimulus)
    else:
        raise ValidationError("Unsupported Case content format.")
    parser.flush()
    return digest([NARRATIVE_IDENTITY_VERSION, "narrative", parser.tokens])


def cached_narrative(scenario, cache):
    key = (scenario.content_format, scenario.stimulus)
    if key not in cache:
        try:
            cache[key] = narrative_identity(scenario.stimulus, scenario.content_format)
        except ValidationError as exc:
            cache[key] = exc
    result = cache[key]
    if isinstance(result, ValidationError):
        raise result
    return result


def question_identity(question, *, narrative_cache=None):
    membership = getattr(question, "exam_scenario_membership", None)
    base = standalone_identity(question)
    if membership is None:
        return base
    scenario = membership.scenario
    return digest([VERSION, "member", cached_narrative(scenario, narrative_cache if narrative_cache is not None else {}), base])


def bundle_identity(scenario, members, *, narrative_cache=None):
    return digest([VERSION, "bundle", cached_narrative(scenario, narrative_cache if narrative_cache is not None else {}),
                   [standalone_identity(member.question) for member in members]])


def reserves(contribution):
    cycle = contribution.cycle_course.cycle
    return not (cycle.processing_mode == "AUTOMATIC_GENERATION"
                and cycle.automatic_contributor_completion_policy == "SUFFICIENT_POOL"
                and contribution.status == "DRAFT" and contribution.roster_status == "BLOCKED")


def pool_claims(course, *, limit=20000, narrative_cache=None):
    """Read-only inventory, including unpublished accepted import candidates."""
    from .exam_units import resolve_examination_unit
    from .models import ExamScenarioMember, Question, QuestionImportBatch
    unit = resolve_examination_unit(course)
    questions = list(Question.objects.filter(contribution__cycle_course_id__in=unit.member_ids,
                                             contribution__active_marker=1)
                     .select_related("contribution__cycle_course__cycle")
                     .prefetch_related(Prefetch(
                         "exam_scenario_memberships",
                         queryset=ExamScenarioMember.objects.filter(active_marker=1).select_related("scenario"),
                         to_attr="_current_case_memberships",
                     ))
                     .order_by("id")[:limit + 1])
    if len(questions) > limit:
        raise ValidationError("Duplicate preflight scope exceeds its bounded question limit.")
    claims = defaultdict(list)
    imported = set()
    narrative_cache = narrative_cache if narrative_cache is not None else {}
    for question in questions:
        if question.import_batch_id:
            imported.add((question.import_batch_id, question.import_row_number))
        if reserves(question.contribution):
            claims[question_identity(question, narrative_cache=narrative_cache)].append((question.id, None, None))
    batches = list(QuestionImportBatch.objects.filter(contribution__cycle_course_id__in=unit.member_ids,
                    contribution__active_marker=1,
                   status__in=QuestionImportBatch.active_statuses())
                   .select_related("contribution__cycle_course__cycle").order_by("id")[:limit + 1])
    if len(batches) > limit:
        raise ValidationError("Duplicate preflight scope exceeds its bounded batch limit.")
    inspected = len(questions)
    for batch in batches:
        if not reserves(batch.contribution):
            continue
        # Validate the complete plan, including skipped/processed decisions,
        # before any pending stored digest can enter current-version claims.
        validate_import_plan_version(batch)
        for number, decision in batch.duplicate_plan.items():
            inspected += 1
            if inspected > limit:
                raise ValidationError("Duplicate preflight scope exceeds its bounded candidate limit.")
            if decision["disposition"] == "ACCEPTED" and (batch.id, int(number)) not in imported:
                claims[decision["identity"]].append((None, batch.id, int(number)))
    return unit, claims


def require_clean_pool(course):
    try:
        _, claims = pool_claims(course)
    except IncompatibleImportPlan:
        raise
    except ValidationError as exc:
        raise LegacyPoolConflict() from exc
    if any(len(owners) > 1 for owners in claims.values()):
        raise LegacyPoolConflict()


def reconcile(course, *, legacy=False):
    """Called inside the existing cycle lock, after mutation and before commit.

    Legacy collisions fail closed without selecting a winner. Deleting/replacing
    derived reservations never deletes academic content or source history.
    """
    from .models import ExaminationCycle, ExamScenario, ExamScenarioMember, QuestionIdentityReservation
    if not connection.in_atomic_block:
        raise RuntimeError("Duplicate reservations require the cycle transaction.")
    ExaminationCycle.objects.select_for_update().get(pk=course.cycle_id)
    cache = {}
    try:
        unit, claims = pool_claims(course, narrative_cache=cache)
    except IncompatibleImportPlan:
        raise
    except ValidationError as exc:
        raise LegacyPoolConflict() from exc
    if any(len(owners) > 1 for owners in claims.values()):
        raise LegacyPoolConflict() if legacy else ValidationError(DUPLICATE_MESSAGE)
    bundles = {}
    claimed_questions = [owner[0] for owners in claims.values() for owner in owners if owner[0]]
    for scenario in ExamScenario.objects.filter(
        active_marker=1, members__question_id__in=claimed_questions,
        members__active_marker=1,
    ).distinct().prefetch_related(Prefetch(
        "members", queryset=ExamScenarioMember.objects.filter(active_marker=1).select_related("question"),
    )):
        members = sorted(scenario.members.all(), key=lambda row: row.position)
        key = bundle_identity(scenario, members, narrative_cache=cache)
        bundles.update({member.question_id: key for member in members})
    existing = list(QuestionIdentityReservation.objects.select_for_update().filter(
        Q(primary_cycle_course=unit.primary)
        | Q(question__contribution__cycle_course_id__in=unit.member_ids)
        | Q(import_batch__contribution__cycle_course_id__in=unit.member_ids)
    ).order_by("pk"))
    # Keep identical claims (and their IDs) intact. Only changed ownership,
    # identity, mapping or bundle records are released/reacquired.
    def signature(row):
        return (row.primary_cycle_course_id, row.version, row.fingerprint,
                row.bundle_fingerprint, row.question_id, row.import_batch_id, row.import_row_number)
    desired = {(unit.primary.id, VERSION, key, bundles.get(owner[0], ""), *owner)
               for key, owners in claims.items() for owner in owners}
    retained = {signature(row) for row in existing if signature(row) in desired}
    obsolete = [row.pk for row in existing if signature(row) not in desired]
    if obsolete:
        QuestionIdentityReservation.objects.filter(pk__in=obsolete).delete()
    QuestionIdentityReservation.objects.bulk_create([
        QuestionIdentityReservation(primary_cycle_course_id=values[0], version=values[1], fingerprint=values[2],
            bundle_fingerprint=values[3], question_id=values[4], import_batch_id=values[5], import_row_number=values[6])
        for values in sorted(desired - retained, key=lambda values: values[2])])


def acquire_import(batch):
    """First chunk only; caller holds cycle/contribution/batch transaction locks."""
    from .exam_units import resolve_examination_unit
    from .models import QuestionIdentityReservation
    if not connection.in_atomic_block:
        raise RuntimeError("Import claims require the cycle transaction.")
    validate_import_plan_version(batch)
    if not reserves(batch.contribution):
        return
    primary = resolve_examination_unit(batch.contribution.cycle_course).primary
    QuestionIdentityReservation.objects.bulk_create([
        QuestionIdentityReservation(primary_cycle_course=primary, version=decision["identity_version"],
            fingerprint=decision["identity"], import_batch=batch, import_row_number=int(number))
        for number, decision in sorted(batch.duplicate_plan.items(), key=lambda item: item[1]["identity"])
        if decision["disposition"] == "ACCEPTED"])


def transfer_import(batch, questions):
    """Transfer this chunk's claims in place; never scan/rewrite other owners."""
    from .models import QuestionIdentityReservation
    if not connection.in_atomic_block:
        raise RuntimeError("Import transfers require the cycle transaction.")
    validate_import_plan_version(batch)
    if not reserves(batch.contribution):
        return
    for question in sorted(questions, key=lambda q: batch.duplicate_plan[str(q.import_row_number)]["identity"]):
        updated = QuestionIdentityReservation.objects.filter(import_batch=batch,
            import_row_number=question.import_row_number,
            version=batch.duplicate_plan[str(question.import_row_number)]["identity_version"],
            fingerprint=batch.duplicate_plan[str(question.import_row_number)]["identity"]).update(
                question=question, import_batch=None, import_row_number=None)
        if updated != 1:
            raise ValidationError("The import reservation is inconsistent. Contact the exam coordinator.")


def plan_import(contribution, rows):
    try:
        _, claims = pool_claims(contribution.cycle_course)
    except IncompatibleImportPlan:
        raise
    except ValidationError as exc:
        raise LegacyPoolConflict() from exc
    if any(len(owners) > 1 for owners in claims.values()):
        raise LegacyPoolConflict()
    seen = set(claims)
    plan = {}
    for row in rows:
        key = standalone_identity(row.payload)
        plan[str(row.row_number)] = {"identity_version": VERSION, "identity": key,
                                   "disposition": "SKIPPED_DUPLICATE" if key in seen else "ACCEPTED"}
        seen.add(key)
    return plan


def accepted_count(plan):
    return sum(row["disposition"] == "ACCEPTED" for row in plan.values())

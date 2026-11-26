"""Opt-in section sets. Selection uses frozen blueprint units; output uses snapshots only."""
import copy
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import replace
from functools import lru_cache

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError

from .generation_algorithms import confidential_hmac_rank, order_selected_blocks
from .models import (CourseExamConfiguration, CycleCourseOffering, ExamGenerationRevision,
                     ExamGeneratorPolicy, ExamSectionSetAssignment, GeneratedExamItem,
                     GeneratedExamSet, Question)


def digest(value):
    value = json.loads(json.dumps(value, default=str))
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def active_policy(course):
    row = ExamGeneratorPolicy.objects.filter(cycle_id=course.cycle_id).first()
    if row and ((course.exam_classification == "DEPARTMENTAL" and row.departmental)
                or (course.exam_classification == "STANDARDIZED" and row.standardized)):
        return row
    return None


def choices_fixed_order(question):
    """Honor explicit exceptions and existing conservative label-dependent rules."""
    from .duplicate_contract import positional_choices
    from .question_content import visible_text
    return question.choices_fixed_order or positional_choices(
        visible_text(question.question_text, question.content_format),
        [visible_text(getattr(question, f"choice_{letter}"), question.content_format) for letter in "abcd"])


class SectionQuotaInfeasible(ValidationError):
    """A proved one-set shortage, distinct from an unproved computation limit."""


class SectionOrderInfeasible(ValidationError):
    """Quotas are satisfiable, but no valid selection permits distinct A/B order."""


def select_blocks(problem, usage, *, seed, budget, require_orderable=False):
    """Exact minimum cumulative reuse for one set, bounded and whole-unit aware.

    Nonconflicting units with equal hard vectors and ordering sections are
    interchangeable. Conflicting logical copies stay separate and reserve
    identities across groups. Section-pair callers require a whole-block A/B
    reorder; quota-only callers retain their original contract. Reuse is a cost,
    never a hard restriction. A budget failure is
    not reported as a shortage or proof that an orderable selection is impossible.
    """
    blocks = tuple(problem.blocks)
    identities = {str(b.block_id): tuple(problem.questions[m.source_id].normalized_fingerprint
                                       or f"q:{m.source_id}" for m in b.members) for b in blocks}
    occurrence = Counter(i for ids in identities.values() for i in ids)
    grouped = defaultdict(list)
    for block in blocks:
        ids = identities[str(block.block_id)]
        if len(ids) != len(set(ids)):
            continue
        conflict = any(occurrence[i] > 1 for i in ids)
        cost = sum(usage[i] for i in ids)
        rank = confidential_hmac_rank(secret=settings.SECRET_KEY, domain="section-set.selection.v1",
                                     context={"seed": seed, "block": str(block.block_id)})
        grouped[(block.vector, block.members[0].section_id,
                 str(block.block_id) if conflict else None)].append((cost, rank, block))
    groups = [sorted(rows, key=lambda row: (row[0], row[1], str(row[2].block_id))) for rows in grouped.values()]
    groups.sort(key=lambda rows: (-rows[0][2].size, rows[0][2].vector, rows[0][1]))
    suffix = [tuple(0 for _ in problem.margins) for _ in range(len(groups) + 1)]
    min_cost = [0.0] * (len(groups) + 1)
    for i in range(len(groups) - 1, -1, -1):
        vector = groups[i][0][2].vector
        suffix[i] = tuple(suffix[i + 1][j] + len(groups[i]) * v for j, v in enumerate(vector))
        local = min(row[0] / row[2].size for row in groups[i])
        min_cost[i] = min(local, min_cost[i + 1]) if i + 1 < len(groups) else local
    states = 0
    quota_valid_seen = False

    @lru_cache(maxsize=None)
    def solve(index, remaining, reserved, seen_sections):
        nonlocal states, quota_valid_seen
        states += 1
        if states > budget:
            raise ValidationError("Section-set computation budget reached; orderable selection feasibility or minimum reuse was not established. No output was saved.")
        if not any(remaining):
            quota_valid_seen = True
            return (0, ()) if seen_sections is None else None
        if index == len(groups) or any(n < 0 or n > suffix[index][j] for j, n in enumerate(remaining)):
            return None
        rows = groups[index]
        vector = rows[0][2].vector
        maximum = min([len(rows)] + [n // v for n, v in zip(remaining, vector) if v])
        best = None
        # Favor unused units. Exact minimization still considers every feasible count.
        for count in range(maximum, -1, -1):
            selected = rows[:count]
            claimed = frozenset(i for _, _, b in selected for i in identities[str(b.block_id)] if occurrence[i] > 1)
            if reserved & claimed:
                continue
            # Until two blocks share an ordering section, retain the sections
            # seen once. None means a distinct A/B whole-block order is possible.
            next_sections = seen_sections
            if count and seen_sections is not None:
                section = rows[0][2].members[0].section_id
                next_sections = None if count > 1 or section in seen_sections else seen_sections | {section}
            tail = solve(index + 1, tuple(n - count * v for n, v in zip(remaining, vector)),
                         reserved | claimed, next_sections)
            if tail is None:
                continue
            trial = (sum(row[0] for row in selected) + tail[0],
                     tuple(str(row[2].block_id) for row in selected) + tail[1])
            if best is None or trial < best:
                best = trial
            if best[0] <= int(remaining[0] * min_cost[index]):
                break
        return best

    try:
        result = solve(0, tuple(problem.margins), frozenset(),
                       frozenset() if require_orderable else None)
    except RecursionError as exc:
        raise ValidationError("Section-set pool structure exceeds the computation limit. No output was saved.") from exc
    if result is None:
        if quota_valid_seen:
            raise SectionOrderInfeasible("Blueprint quotas are satisfiable, but no valid selection permits different A/B whole-block order. At least two selected whole questions or cases must share a blueprint section. Cases/passages cannot be split. No output was saved.")
        raise SectionQuotaInfeasible("Eligible whole questions and cases cannot satisfy the blueprint quotas for one valid exam. Reuse across sections is allowed, including 100% overlap.")
    return result[1]


def select_policy_blocks(problem, usage, *, seed, budget):
    # Automatic mode treats difficulty as preferred; preserve that distinction.
    # First honor the preferred mix when feasible, then use the original hard
    # blueprint margins if whole units cannot achieve that mix. Never relax hard quotas.
    if problem.automatic_selection is not None:
        codes = tuple(problem.difficulty_quotas)
        preferred = replace(problem, margins=problem.margins + tuple(problem.difficulty_quotas[c] for c in codes),
            blocks=tuple(replace(block, vector=block.vector + tuple(
                sum(problem.questions[m.source_id].difficulty == c for m in block.members) for c in codes))
                for block in problem.blocks))
        try:
            return select_blocks(preferred, usage, seed=seed, budget=budget, require_orderable=True)
        except (SectionQuotaInfeasible, SectionOrderInfeasible):
            pass
    return select_blocks(problem, usage, seed=seed, budget=budget, require_orderable=True)


def overlap_rows(plans, questions):
    identities = [{questions[q].normalized_fingerprint or f"q:{q}" for q in row["source_ids"]} for row in plans]
    pool_size = len({q.normalized_fingerprint or f"q:{q.source_id}" for q in questions.values()})
    rows = []
    for i, left in enumerate(plans):
        for j in range(i + 1, len(plans)):
            shared = len(identities[i] & identities[j])
            lower = max(0, len(identities[i]) + len(identities[j]) - pool_size)
            rows.append({"left": left["set_code"], "right": plans[j]["set_code"],
                         "count": shared, "percent": round(100 * shared / len(identities[i]), 2),
                         "unavoidable_pool_lower_bound": lower})
    return rows


def paired_order(problem, selected, *, code, fingerprint, reference=None):
    members = order_selected_blocks(blocks=problem.blocks, selected_block_ids=selected,
        set_code=code, secret=settings.SECRET_KEY,
        hmac_context={"fingerprint": fingerprint}, section_order=problem.section_order)
    if reference is None or members != reference:
        return members
    # Hash ranks may coincide. Swap whole blocks within one blueprint section;
    # never move a linked question away from its passage or cross section order.
    by_source = {m.source_id: b for b in problem.blocks for m in b.members if str(b.block_id) in set(selected)}
    ordered, seen = [], set()
    for member in reference:
        block = by_source[member.source_id]
        if str(block.block_id) not in seen:
            ordered.append(block)
            seen.add(str(block.block_id))
    for index in range(len(ordered) - 1):
        if ordered[index].members[0].section_id == ordered[index + 1].members[0].section_id:
            ordered[index], ordered[index + 1] = ordered[index + 1], ordered[index]
            return tuple(m for b in ordered for m in sorted(b.members, key=lambda m: m.member_order))
    raise ValidationError("Different A/B question order requires at least two selected whole questions or cases in one blueprint section. Cases/passages cannot be split. No output was saved.")


def apply_policy(problem, report):
    """Extend read-only readiness with the exact plan later persisted by generation."""
    policy = active_policy(problem.cycle_course)
    if policy is None:
        return problem, report
    from .exam_units import resolve_examination_unit
    unit = resolve_examination_unit(problem.cycle_course)
    snapshots = list(CycleCourseOffering.objects.filter(cycle_course_id__in=unit.member_ids)
                     .select_related("offering__section", "offering__campus").order_by("offering_id"))
    member_courses = {m.id: m.course_id for m in unit.members}
    seen = set()
    offerings = []
    for row in snapshots:
        offering = row.offering
        cycle = problem.cycle_course.cycle
        if (offering.id in seen or offering.tenant_id != cycle.tenant_id or offering.term_id != cycle.term_id
                or offering.academic_year_id != cycle.academic_year_id or row.campus_id != offering.campus_id
                or offering.course_id != member_courses[row.cycle_course_id]
                or offering.campus.tenant_id != cycle.tenant_id
                or offering.section.tenant_id != cycle.tenant_id
                or offering.section.campus_id != offering.campus_id):
            raise PermissionDenied("Section set offering snapshot is outside the examination scope.")
        seen.add(offering.id)
        if not offering.is_active or offering.status != "OPEN" or not offering.campus.is_active:
            continue
        offerings.append({"offering_id": offering.id, "campus_id": offering.campus_id,
                          "label": offering.section.name[:255], "course_id": offering.course_id,
                          "set_code": f"S{offering.id}"})
    if not offerings:
        raise ValidationError("At least one active scoped academic section/offering is required for section sets.")
    fixed = {q.id: choices_fixed_order(q) for q in Question.objects.filter(id__in=problem.questions)}
    snapshot = {"schema": "section-sets-v2", "mode": policy.mode, "shuffle_choices": policy.shuffle_choices,
                "departmental": policy.departmental, "standardized": policy.standardized,
                "policy_revision": policy.revision, "offerings": [
                    {**o, "set_code": o["set_code"] + version, "version": version}
                    for o in offerings for version in "AB"],
                "fixed_order_flags": {str(q): v for q, v in fixed.items()}}
    fingerprint = digest({"source": problem.input_fingerprint, "policy": snapshot, "fixed_order": fixed})
    usage = Counter()
    plans, first_ids, first_orders = [], None, {}
    budget = int(getattr(settings, "DEPARTMENTAL_EXAM_GENERATION_MAX_STATES", 500_000))
    for offering in offerings:
        code = offering["set_code"]
        if first_ids is None or policy.mode == ExamGeneratorPolicy.Mode.DIFFERENT:
            selected = select_policy_blocks(problem, usage, seed=fingerprint + code, budget=budget)
        else:
            selected = first_ids
        orders = {}
        for version in "AB":
            members = paired_order(problem, selected, code=code + version, fingerprint=fingerprint,
                                   reference=orders.get("A") if version == "B" else None)
            if policy.mode == ExamGeneratorPolicy.Mode.SAME_ORDER and first_orders:
                members = first_orders[version]
            orders[version] = members
            plans.append({**offering, "set_code": code + version, "version": version,
                          "source_ids": tuple(m.source_id for m in members)})
        if first_ids is None:
            first_ids, first_orders = selected, orders
        # Allocation/reuse costs count one selection per offering, never its B copy.
        usage.update(problem.questions[m.source_id].normalized_fingerprint or f"q:{m.source_id}" for m in orders["A"])
    overlaps = overlap_rows([p for p in plans if p["version"] == "A"], problem.questions)
    snapshot["overlap"] = overlaps
    actual_difficulty = {p["set_code"]: dict(Counter(problem.questions[q].difficulty for q in p["source_ids"])) for p in plans}
    snapshot["difficulty_target"] = problem.difficulty_quotas
    snapshot["difficulty_actual"] = actual_difficulty
    report = {**report, "section_sets": [{k: v for k, v in p.items() if k != "source_ids"} for p in plans], "pairwise_overlap": overlaps,
              "section_set_mode": policy.get_mode_display(),
              "section_set_min_overlap": min((row["count"] for row in overlaps), default=0),
              "section_set_difficulty_actual": actual_difficulty,
              "reuse_note": "Reuse is allowed, including 100%. The pool lower bound proves only that amount unavoidable; additional overlap may follow quotas, whole cases, or earlier section selections."}
    return replace(problem, input_fingerprint=fingerprint, set_policy_snapshot=snapshot,
                   section_set_plan=tuple(plans), fixed_order_flags=fixed), report


def apply_choices(item, *, shuffle, fixed, seed):
    original = list(item.choices_snapshot)
    answer = item.correct_answer_snapshot
    order = list(range(4))
    if shuffle and not fixed:
        order.sort(key=lambda index: confidential_hmac_rank(
            secret=settings.SECRET_KEY, domain="section-set.choices.v1",
            context={"seed": seed, "question": item.source_question_id, "choice": index}))
    item.original_choices_snapshot = original
    item.original_correct_answer_snapshot = answer
    item.choice_order_snapshot = order
    item.choices_fixed_order_snapshot = fixed
    item.choices_snapshot = [original[i] for i in order]
    item.correct_answer_snapshot = "ABCD"[order.index("ABCD".index(answer))]


def verify_choices(item, *, shuffle):
    order = item.choice_order_snapshot
    original = item.original_choices_snapshot
    answer = item.original_correct_answer_snapshot
    if (not isinstance(order, list) or any(type(i) is not int for i in order)
            or sorted(order) != list(range(4)) or not isinstance(original, list) or len(original) != 4
            or answer not in "ABCD" or len(answer) != 1
            or ((not shuffle or item.choices_fixed_order_snapshot) and order != list(range(4)))
            or item.choices_snapshot != [original[i] for i in order]
            or item.correct_answer_snapshot != "ABCD"[order.index("ABCD".index(answer))]):
        raise PermissionDenied("Displayed choices or answer key do not match the original evidence.")
    from .generation_readiness import generation_question_source_digest
    if generation_question_source_digest(source_id=item.source_question_id, revision=item.source_question_revision,
            question_text=item.question_text_snapshot, choices=original, correct_answer=answer,
            difficulty=item.difficulty_snapshot, content_format=item.question_content_format_snapshot) != item.source_question_digest:
        raise PermissionDenied("Original question evidence is corrupt.")


def set_digest(generated_set, items):
    from .structured_snapshots import CONTENT_FIELDS
    fields = (*CONTENT_FIELDS, "original_choices_snapshot", "original_correct_answer_snapshot",
              "choice_order_snapshot", "choices_fixed_order_snapshot", "source_contributor_id",
              "source_contributor_id_snapshot", "source_contributor_name_snapshot",
              "campus_code_snapshot", "campus_name_snapshot")
    return digest({"set": generated_set.set_code, "count": generated_set.item_count,
                   "campus": generated_set.campus_quotas_snapshot, "difficulty": generated_set.difficulty_quotas_snapshot,
                   "sections": generated_set.section_quotas_snapshot,
                   "items": [{f: getattr(i, f) for f in fields} for i in items]})


def _verify_policy_revision(revision):
    policy = revision.set_policy_snapshot
    if not policy:
        return
    if policy.get("schema") not in {"section-sets-v1", "section-sets-v2"}:
        raise PermissionDenied("Unsupported section-set policy snapshot.")
    if (policy.get("mode") not in ExamGeneratorPolicy.Mode.values
            or any(type(policy.get(key)) is not bool for key in ("shuffle_choices", "departmental", "standardized"))
            or type(policy.get("policy_revision")) is not int or policy["policy_revision"] < 1):
        raise PermissionDenied("Invalid section-set policy settings.")
    expected = {o["set_code"]: o for o in policy["offerings"]}
    paired = policy["schema"] == "section-sets-v2"
    groups = defaultdict(list)
    for offering in policy["offerings"]:
        groups[offering["offering_id"]].append(offering)
    if (len(expected) != len(policy["offerings"])
            or any(not valid_set_code(o["set_code"]) or o["set_code"] != f"S{o['offering_id']}" + (o.get("version", "") if paired else "")
                   for o in policy["offerings"])
            or any((len(rows) != 2 or {o.get("version") for o in rows} != {"A", "B"}
                    or any(o[k] != rows[0][k] for o in rows for k in ("label", "campus_id", "course_id")))
                   if paired else len(rows) != 1 for rows in groups.values())):
        raise PermissionDenied("Invalid saved section assignment coverage.")
    sets = list(revision.generated_sets.prefetch_related("items").order_by("set_code"))
    assignments = list(revision.section_set_assignments.select_related("generated_set"))
    if (not expected or len(expected) != len(sets) or {s.set_code for s in sets} != set(expected)
            or len(assignments) != len(expected)):
        raise PermissionDenied("The revision has incomplete section sets or assignments.")
    for a in assignments:
        original = expected.get(a.generated_set.set_code, {})
        if (a.generated_set.generation_revision_id != revision.id or a.offering_id != original.get("offering_id")
                or a.campus_id_snapshot != original.get("campus_id") or a.section_label_snapshot != original.get("label")
                or a.version_code != original.get("version", "")):
            raise PermissionDenied("Section assignment does not match the saved revision.")
    from .structured_snapshots import verify_item_layout
    source_rows = {r.source_question_id_snapshot: r for r in revision.source_audit_snapshot.question_snapshots.all()}
    actual_plans = []
    for row in sets:
        items = sorted(row.items.all(), key=lambda i: i.position)
        if (row.item_count != revision.final_item_count_snapshot or len(items) != row.item_count
                or [i.position for i in items] != list(range(1, row.item_count + 1))
                or len({i.source_question_id for i in items}) != len(items)
                or row.structured_content_digest_version != "section-set-content-v1"
                or set_digest(row, items) != row.structured_content_digest):
            raise PermissionDenied("Saved section-set content integrity failed.")
        verify_item_layout(row, items)
        campuses = {str(key): value for key, value in row.campus_quotas_snapshot.items()}
        numeric_campuses = all(key.isdigit() for key in campuses)
        actual_campuses = Counter(str(i.source_campus_id) if numeric_campuses else i.campus_code_snapshot for i in items)
        if dict(actual_campuses) != campuses:
            raise PermissionDenied("Saved campus quotas do not match the section set.")
        if (dict(Counter(i.difficulty_snapshot for i in items)) != row.difficulty_quotas_snapshot
                or row.difficulty_quotas_snapshot != policy["difficulty_actual"][row.set_code]):
            raise PermissionDenied("Saved difficulty counts do not match the section set.")
        actual_plans.append({"set_code": row.set_code, "source_ids": tuple(i.source_question_id for i in items)})
        for item in items:
            verify_choices(item, shuffle=policy["shuffle_choices"])
            source = source_rows.get(item.source_question_id)
            if (source is None or not source.eligible_for_generation
                    or source.source_question_revision != item.source_question_revision
                    or source.source_question_digest != item.source_question_digest
                    or source.campus_id_snapshot != item.source_campus_id
                    or source.contributor_id_snapshot != item.source_contributor_id_snapshot
                    or item.source_contributor_id != item.source_contributor_id_snapshot
                    or source.contributor_name_snapshot != item.source_contributor_name_snapshot
                    or source.campus_code_snapshot != item.campus_code_snapshot
                    or source.campus_name_snapshot != item.campus_name_snapshot
                    or item.choices_fixed_order_snapshot != policy["fixed_order_flags"].get(str(item.source_question_id))):
                raise PermissionDenied("Selected question does not match the retained eligible pool evidence.")
    from types import SimpleNamespace
    questions = {q: SimpleNamespace(normalized_fingerprint=r.normalized_fingerprint, source_id=q)
                 for q, r in source_rows.items() if r.eligible_for_generation}
    # Preserve the original offering order in the overlap matrix.
    by_code = {p["set_code"]: p for p in actual_plans}
    actual_plans = [by_code[o["set_code"]] for o in policy["offerings"]]
    comparison = actual_plans
    if paired:
        comparison = [p for p in actual_plans if expected[p["set_code"]]["version"] == "A"]
        for rows in groups.values():
            pair = {o["version"]: by_code[o["set_code"]]["source_ids"] for o in rows}
            if set(pair["A"]) != set(pair["B"]) or pair["A"] == pair["B"]:
                raise PermissionDenied("Section A/B membership or order does not match the saved pair.")
    if overlap_rows(comparison, questions) != policy["overlap"]:
        raise PermissionDenied("Pairwise overlap does not match the saved output.")
    if policy["mode"] in {"SAME_ORDER", "SHUFFLED_ORDER"}:
        first = comparison[0]["source_ids"]
        if any(set(p["source_ids"]) != set(first) for p in comparison):
            raise PermissionDenied("Same-question sets have different membership.")
        if policy["mode"] == "SAME_ORDER":
            versions = ("A", "B") if paired else (None,)
            for version in versions:
                rows = [p for p in actual_plans if not paired or expected[p["set_code"]]["version"] == version]
                if any(p["source_ids"] != rows[0]["source_ids"] for p in rows):
                    raise PermissionDenied("Same-order sets have different ordering.")


def verify_policy_revision(revision):
    try:
        _verify_policy_revision(revision)
    except (KeyError, TypeError, ValueError, AttributeError, ValidationError) as exc:
        raise PermissionDenied("Saved section-set evidence is invalid or incomplete.") from exc


def expected_set_codes(revision):
    if revision.set_policy_snapshot:
        verify_policy_revision(revision)
        return {o["set_code"] for o in revision.set_policy_snapshot["offerings"]}
    return {"A", "B"}


def valid_set_code(value):
    import re
    return bool(isinstance(value, str) and len(value) <= 32 and re.fullmatch(r"A|B|S[1-9][0-9]{0,29}(?:[AB])?", value))


def require_faculty_section_set(*, revision, set_code, contribution, campus_id, assignments):
    if not revision.set_policy_snapshot:
        if set_code not in {"A", "B"}:
            raise PermissionDenied("Section-set policy evidence is missing.")
        return
    verify_policy_revision(revision)
    offering_ids = {a.offering_id for a in assignments if a.offering.campus_id == campus_id}
    if not revision.section_set_assignments.filter(generated_set__set_code=set_code,
            offering_id__in=offering_ids, campus_id_snapshot=campus_id).exists():
        raise PermissionDenied("This saved set is outside the released campus and current teaching assignments.")


def persist_policy_revision(*, service, problem, course, current, revisions, actor, token_digest,
                            generation_trigger, reason, request):
    from django.utils import timezone
    predecessor = current or (max(revisions, key=lambda r: r.revision_number) if revisions else None)
    if current:
        current.status, current.current_marker = "SUPERSEDED", None
        current.save(update_fields=["status", "current_marker", "updated_at"])
    selections = [p for p in problem.section_set_plan if p.get("version", "A") == "A"]
    appearances = Counter(problem.questions[q].contributor_id for p in selections for q in p["source_ids"])
    from .generation_algorithms import proportional_campus_difficulty_score
    proportional = 0
    for plan in selections:
        cells = Counter((problem.questions[q].campus_id if isinstance(next(iter(problem.campus_quotas)), int)
                         else problem.questions[q].campus_code, problem.questions[q].difficulty) for q in plan["source_ids"])
        proportional += proportional_campus_difficulty_score(total=problem.final_count,
            campus_quotas=problem.campus_quotas, difficulty_quotas=problem.difficulty_quotas, cell_counts=cells)
    revision = ExamGenerationRevision.objects.create(
        cycle_course=course, revision_number=max((r.revision_number for r in revisions), default=0) + 1,
        source_input_fingerprint=problem.input_fingerprint, algorithm_version=problem.algorithm_version,
        generated_at=timezone.now(), generated_by=actor, generation_trigger=generation_trigger,
        configuration_revision_snapshot=problem.configuration_revision, blueprint_revision_snapshot=problem.blueprint_revision,
        roster_boundary_snapshot=problem.roster_boundary, final_item_count_snapshot=problem.final_count,
        request_token_digest=token_digest, supersedes=predecessor, regeneration_reason=reason,
        minimum_overlap=min((r["count"] for r in problem.set_policy_snapshot["overlap"]), default=0),
        proportional_score=proportional, contributors_represented=len(appearances),
        squared_contributor_concentration=sum(n * n for n in appearances.values()),
        set_policy_snapshot=problem.set_policy_snapshot)
    service._create_source_audit_snapshot(revision=revision, problem=problem)
    for plan in problem.section_set_plan:
        row = GeneratedExamSet(generation_revision=revision, set_code=plan["set_code"], item_count=problem.final_count,
            campus_quotas_snapshot=problem.campus_quotas,
            difficulty_quotas_snapshot=dict(Counter(problem.questions[q].difficulty for q in plan["source_ids"])),
            section_quotas_snapshot={str(k): v for k, v in problem.section_quotas.items()})
        items = [service._item_snapshot(generated_set=row, position=i, data=problem.questions[q])
                 for i, q in enumerate(plan["source_ids"], 1)]
        for item in items:
            # A common seed gives identical displayed papers in SAME_ORDER mode.
            seed = problem.input_fingerprint + (("shared" + plan.get("version", "")) if problem.set_policy_snapshot["mode"] == "SAME_ORDER" else row.set_code)
            apply_choices(item, shuffle=problem.set_policy_snapshot["shuffle_choices"],
                          fixed=problem.fixed_order_flags[item.source_question_id], seed=seed)
        row.structured_content_digest_version = "section-set-content-v1"
        row.structured_content_digest = set_digest(row, items)
        row.save()
        for item in items:
            item.generated_set = row
        GeneratedExamItem.objects.bulk_create(items)
        ExamSectionSetAssignment.objects.create(generation_revision=revision, generated_set=row,
            offering_id=plan["offering_id"], campus_id_snapshot=plan["campus_id"], section_label_snapshot=plan["label"], version_code=plan.get("version", ""))
    verify_policy_revision(revision)
    reason_metadata = {}
    if reason:
        reason_metadata = {"regeneration_reason_sha256": hashlib.sha256(reason.encode("utf-8")).hexdigest(),
                           "regeneration_reason_length": len(reason)}
    if current:
        service._audit(action="DE_EXAM_GENERATION_SUPERSEDED", revision=current, actor=actor, request=request,
                       metadata={"superseded_by_generation_revision": revision.revision_number, **reason_metadata})
    service._audit(action="DE_EXAM_REGENERATED" if current else "DE_EXAM_GENERATED", revision=revision,
                   actor=actor, request=request, metadata={"section_set_count": len(problem.section_set_plan),
                                                        "set_mode": problem.set_policy_snapshot["mode"], **reason_metadata})
    from .exam_units import resolve_examination_unit
    CourseExamConfiguration.objects.filter(cycle_course_id__in=resolve_examination_unit(course).member_ids,
        closed_cycle_correction_active=True).update(closed_cycle_correction_active=False)
    return revision


def verify_policy_approval(*, revision, problem, integrity_service):
    verify_policy_revision(revision)
    plans = {p["set_code"]: p for p in problem.section_set_plan}
    if revision.set_policy_snapshot != problem.set_policy_snapshot:
        raise ValidationError("Section-set policy inputs changed. Regenerate before approval.")
    from .generation_readiness import QUESTION_CONTENT_DIGEST_VERSION
    for row in revision.generated_sets.prefetch_related("items"):
        items = sorted(row.items.all(), key=lambda i: i.position)
        if tuple(i.source_question_id for i in items) != plans[row.set_code]["source_ids"]:
            raise ValidationError("Section selection or ordering does not match authoritative inputs.")
        for item in items:
            projected = copy.copy(item)
            projected.choices_snapshot = item.original_choices_snapshot
            projected.correct_answer_snapshot = item.original_correct_answer_snapshot
            integrity_service._verify_item_snapshot(projected, authoritative=problem.questions[item.source_question_id],
                                                    digest_version=QUESTION_CONTENT_DIGEST_VERSION)
    return {"campus_counts": problem.campus_quotas, "section_counts": problem.section_quotas,
            "difficulty_counts": problem.difficulty_quotas, "overlap_count": revision.minimum_overlap}


def faculty_set_options(*, revision, contribution, campus_id):
    if not revision.set_policy_snapshot:
        return ({"code": "A", "label": "Set A"}, {"code": "B", "label": "Set B"})
    from .contribution_authorization import ContributionAuthorizationService
    offering_ids = [a.offering_id for a in ContributionAuthorizationService.retained_current_print_assignments(contribution=contribution)
                    if a.offering.campus_id == campus_id]
    verify_policy_revision(revision)
    return tuple({"code": a.generated_set.set_code, "label": a.generated_set.display_label}
                 for a in revision.section_set_assignments.filter(offering_id__in=offering_ids,
                     campus_id_snapshot=campus_id).select_related("generated_set").order_by("offering_id", "version_code"))


def saved_set_display(generated_set):
    return {"set_version": generated_set.display_version,
            "section_label": generated_set.saved_section_label,
            "set_label": generated_set.display_label}

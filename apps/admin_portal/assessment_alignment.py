"""Template-aware preview. All individual observations stay inside the request.

Reads the existing Insights loader and authoritative saved Gradebook measures;
never computes, saves or finalizes academic records. Separate policy/cache domain.
"""
from collections import Counter, defaultdict
from decimal import Decimal
from itertools import combinations

from django.utils.crypto import salted_hmac

from apps.admin_portal import learning_insights as learning
from apps.admin_portal.insights_generation import encoded
from apps.departmental_exams.models import (
    CycleCourseOffering, ExamCourseEquivalencyMembership, CourseExamConfiguration,
)
from apps.departmental_exams.exam_units import _compatibility_errors, configuration_compatibility_key
from apps.tenants.models import Tenant

POLICY_VERSION = "template-formative-summative-v4"
METRICS = ("quiz", "participation", "exam")
MODES = {"RAW_BASE50", "DIRECT_PERCENTAGE"}


def assessment_fingerprint(selection, filters):
    # Use exactly the resolver's offering-linked dependency graph, including
    # conflicting cycle scope and linked equivalency members. Only the digest
    # leaves this function; no policy records or academic observations do.
    payload = [POLICY_VERSION, learning.learning_fingerprint(selection, filters),
               ExamPolicy(selection, filters).fingerprint_payload()]
    return salted_hmac(POLICY_VERSION, encoded(payload), algorithm="sha256").hexdigest()


def identity(code, period):
    code = code.upper()
    for prefix in {learning.key(period), "PRE_FINAL" if learning.key(period) == "PREFINAL" else learning.key(period),
                   "PRE-FINAL" if learning.key(period) == "PREFINAL" else learning.key(period)}:
        if code.startswith(prefix + "_"):
            return code[len(prefix) + 1:]
    return code


def resolve_mapping(period, components, structure, context, base):
    """Recognize explicit category codes; an arbitrary non-quiz is never output.

    Same-period mappings work for other schools with these unambiguous configured
    categories. NCBA's cross-period relationship is applied separately. Unknown
    formulas, collisions, mixed categories and unknown units fail closed.
    """
    result = {"quiz": None, "participation": None, "exam": None, "signatures": {},
              "reason": "Unsupported or ambiguous assessment mapping."}
    if (structure and structure[0] == "ambiguous-component-identities") or not 0 <= Decimal(base) < 100:
        return result
    if context["formula_mode"] != "WEIGHTED_COMPONENTS":
        return result
    candidates = defaultdict(list)
    exam_components = [c for c in components if c.is_active and c.is_exam_component]
    for component in components:
        if not component.is_active:
            continue
        name = identity(component.code, period)
        node = next((s for s in structure if s[0] == name), None)
        if node is None or component.score_input_mode not in MODES or component.weight_percentage < 0:
            continue
        if component.is_exam_component:
            # A single major exam bucket establishes the saved exam_grade scale.
            # Count unsupported buckets too: the saved grade may include them.
            if len(exam_components) == 1 and all(s[2] in MODES and Decimal(s[1]) >= 0 and not s[4]
                   and s[3] in {"WEIGHTED_DETAILS", "AVERAGE_ACTIVITIES"}
                   and all(d[2] in MODES and Decimal(d[1]) >= 0 for d in s[-1]) for s in node[-1]):
                candidates["exam"].append((component, node[2:]))
            continue
        for sub in component.subcomponents.all():
            if not sub.is_active or sub.is_attendance_component or sub.weight_percentage < 0:
                continue
            subname = identity(sub.code, period)
            metric = ("quiz" if learning.FacultyGradingService._is_quiz_template_code(subname)
                      else "participation" if subname in {"CA_PARTICIPATION_OUTPUT", "PARTICIPATION_OUTPUT"} else None)
            if metric is None:
                continue
            snode = next((s for s in node[-1] if s[0] == subname), None)
            if (snode is None or snode[2] not in MODES or snode[3] not in {"WEIGHTED_DETAILS", "AVERAGE_ACTIVITIES"}
                    or any(d[2] not in MODES or Decimal(d[1]) < 0 for d in snode[-1])):
                continue
            if metric == "participation" and any(learning.FacultyGradingService._is_quiz_template_code(d[0]) for d in snode[-1]):
                continue
            candidates[metric].append((sub, snode[1:]))
    for metric in METRICS:
        if len(candidates[metric]) == 1:
            obj, rules = candidates[metric][0]
            result[metric] = obj.pk
            # Same effective profile/base and component rules, independent of the
            # period's overall Class Standing/Exam weights (60/40 vs 0/100).
            result["signatures"][metric] = (context["template_id"], context["profile_id"],
                context["formula_mode"], tuple(context["signature"][3]), str(base), rules)
    result["reason"] = "" if all(result[m] for m in METRICS) else result["reason"]
    if not result["exam"]:
        result["reason"] = "Examination mapping requires exactly one active supported exam bucket."
    return result


class ExamPolicy:
    def __init__(self, selection, filters):
        self.ncba = Tenant.objects.get(pk=selection.request.scope["tenant_id"]).code.upper() == "NCBA"
        self.rows = defaultdict(list)
        ids = [o.id for o in selection.matching({k: filters[k] for k in ("academic_year_id", "term_id")})]
        for row in CycleCourseOffering.objects.filter(offering_id__in=ids).select_related("cycle_course__cycle").order_by("pk"):
            self.rows[(row.offering_id, row.cycle_course.cycle.exam_period)].append(row)
        # Validate explicit equivalency groups in batches with the Exam Builder's
        # own compatibility rules. Never infer equivalent courses from titles.
        course_ids = {r.cycle_course_id for rows in self.rows.values() for r in rows}
        group_ids = ExamCourseEquivalencyMembership.objects.filter(
            cycle_course_id__in=course_ids, active_marker=1, group__is_active=True).values("group_id")
        members = defaultdict(list)
        self.memberships = list(ExamCourseEquivalencyMembership.objects.filter(
                group_id__in=group_ids, active_marker=1).select_related("group__cycle", "cycle_course").order_by("pk"))
        for membership in self.memberships:
            members[membership.group_id].append(membership)
        member_ids = {m.cycle_course_id for rows in members.values() for m in rows}
        configurations = {c.cycle_course_id: c for c in CourseExamConfiguration.objects.filter(cycle_course_id__in=member_ids)}
        self.configurations = configurations
        self.invalid_units = set()
        for rows in members.values():
            group = rows[0].group
            courses = [m.cycle_course for m in rows]
            if (group.cycle.processing_mode != "AUTOMATIC_GENERATION"
                    or group.primary_cycle_course_id not in {c.pk for c in courses}
                    or any(c.cycle_id != group.cycle_id for c in courses)
                    or _compatibility_errors(members=courses, configurations=configurations)):
                self.invalid_units.update(c.pk for c in courses)

    def fingerprint_payload(self):
        """Meaningful values from the same bounded graph used for eligibility.

        Do not filter inconsistent references away: their presence can make a
        selected offering unavailable. Unlinked tenants/cycles are never read.
        """
        def cycle_values(cycle):
            return (cycle.pk, cycle.tenant_id, cycle.academic_year_id, cycle.term_id,
                    cycle.exam_period, cycle.is_active, cycle.processing_mode)

        def course_values(course):
            return (course.pk, course.cycle_id, course.course_id,
                    course.inclusion_status, course.exam_classification)

        snapshots = sorted((row.pk, row.offering_id, row.campus_id,
            course_values(row.cycle_course), cycle_values(row.cycle_course.cycle))
            for rows in self.rows.values() for row in rows)
        memberships = [(m.pk, m.active_marker, course_values(m.cycle_course),
            m.group_id, m.group.is_active, m.group.primary_cycle_course_id,
            cycle_values(m.group.cycle)) for m in self.memberships]
        configurations = [(pk, configuration_compatibility_key(c))
                          for pk, c in sorted(self.configurations.items())]
        return [self.ncba, snapshots, memberships, configurations]

    def resolve(self, offering, code):
        if code == "PRELIM" and self.ncba:
            return {"label": "Regular", "official": False, "descriptive": True, "reason": ""}
        rows = self.rows[(offering.id, code)]
        if not rows and not self.ncba:
            return {"label": "Template examination", "official": True, "descriptive": True, "reason": ""}
        unknown = {"label": "Unavailable", "official": False, "descriptive": False,
                   "reason": "Examination policy is missing, ambiguous or outside the offering scope."}
        if len(rows) != 1:
            return unknown
        row = rows[0]
        course, cycle = row.cycle_course, row.cycle_course.cycle
        if (course.pk in self.invalid_units or not cycle.is_active or row.campus_id != offering.campus_id or course.course_id != offering.course_id
                or (cycle.tenant_id, cycle.academic_year_id, cycle.term_id) !=
                   (offering.tenant_id, offering.academic_year_id, offering.term_id)):
            return unknown
        if course.inclusion_status == "EXEMPT":
            return {"label": "Exempt", "official": False, "descriptive": False,
                    "reason": "Excluded by this cycle's examination policy."}
        if course.inclusion_status != "INCLUDED":
            return unknown
        kind = course.exam_classification
        if kind in {"DEPARTMENTAL", "STANDARDIZED"}:
            return {"label": kind.title(), "official": True, "descriptive": True, "reason": ""}
        if kind == "UNCLASSIFIED_LEGACY" and code == "MIDTERM":
            return {"label": "Legacy Exam — Type Not Recorded", "official": False, "descriptive": True,
                    "reason": "Historical description only; examination type was not recorded."}
        return unknown


def movement(previous, current):
    directions = [learning.performance_change(previous[m], current[m]) for m in METRICS]
    if None in directions:
        return learning.INSUFFICIENT
    if directions[0] == directions[1] == directions[2] and directions[0] != "STABLE":
        return learning.PATTERNS[0]
    if directions[0] == directions[1] and {directions[0], directions[2]} == {"IMPROVING", "DECLINING"}:
        return learning.PATTERNS[1]
    return learning.PATTERNS[3] if set(directions) == {"STABLE"} else learning.PATTERNS[2]


class AlignmentCollector:
    def __init__(self, selection, filters):
        self.policy = ExamPolicy(selection, filters)
        self.rows = defaultdict(dict)
        self.mappings = {}

    def collect(self, offering, period, context, base, components, structure, finalized,
                authorized, reason, activities, raw_scores, grade_scores):
        cache_key = (period.pk, context["profile_id"], context["formula_mode"], base)
        if cache_key not in self.mappings:
            self.mappings[cache_key] = resolve_mapping(period, components, structure, context, base)
        mapping = self.mappings[cache_key]
        values = {m: {} for m in METRICS}
        if authorized:
            cohort = set(finalized)
            for metric in ("quiz", "participation"):
                selected = [a for a in activities if a.template_subcomponent_id == mapping[metric]] if mapping[metric] else []
                # Every configured active activity is evidence: do not drop an
                # entirely unscored activity and fabricate complete students.
                supported = [sid for sid in cohort if selected and all(raw_scores[a.id].get(sid) is not None for a in selected)]
                values[metric] = learning.gradebook_quiz_values(selected, grade_scores, supported, components)
            if mapping["exam"]:
                values["exam"] = {sid: row["exam"] for sid, row in finalized.items()
                                  if row.get("exam") is not None and 0 <= row["exam"] <= 100}
        self.rows[offering.id][learning.key(period)] = {
            "code": learning.key(period), "label": period.name, "sequence": period.sequence_no,
            "mapping": mapping, "values": values, "reason": reason if not authorized else "",
            "pending": reason in {"Ongoing or future period; not concluded.",
                "Concluded-period submission is not finalized.", "Reopen or correction window is active."},
            "policy": self.policy.resolve(offering, learning.key(period)),
            "template": context["template_label"], "profile": context["profile_label"], "base": base,
        }

    def finish(self, selection, filters, histories, active_enrolled, leadership_ids, extra_populations=None):
        plans, groups = {}, defaultdict(list)
        # Build every possible release before campus/course/section display filters.
        for oid, rows in self.rows.items():
            pairs, exams = [], []
            for code, destination in rows.items():
                policy = destination["policy"]
                if policy["descriptive"]:
                    exams.append(destination)
                source_code = "PREFINAL" if self.policy.ncba and code == "FINAL" else code
                if self.policy.ncba and code not in {"MIDTERM", "FINAL"}:
                    continue
                source = rows.get(source_code)
                measures = {m: (destination if m == "exam" else source)["values"][m]
                            if (destination if m == "exam" else source) else {} for m in METRICS}
                common = set.intersection(*(set(v) for v in measures.values()))
                signatures = {m: (destination if m == "exam" else source)["mapping"]["signatures"].get(m)
                              if (destination if m == "exam" else source) else None for m in METRICS}
                reason = destination["reason"] or (source["reason"] if source else "Formative source period is unavailable.")
                if not destination["mapping"]["exam"]:
                    reason = reason or destination["mapping"]["reason"]
                reason = reason or ("" if all(signatures.values()) else "Independent Quiz, Participation/Output or Examination mapping is unsupported.")
                reason = reason or ("" if common else "Required finalized non-excused scores are unavailable.")
                reason = reason or (policy["reason"] if not policy["official"] else "")
                pairs.append({"destination": code, "source": source_code, "type": policy["label"],
                    "official": policy["official"], "reason": reason, "measures": measures,
                    "pending": destination["pending"] or bool(source and source["pending"]),
                    "common": common, "signatures": signatures, "sequence": destination["sequence"]})
                groups[oid].append(common)
            for row in rows.values():
                groups[oid].extend(set(v) for v in row["values"].values())
            for first, second in combinations(pairs, 2):
                groups[oid].append(first["common"] & second["common"])
            for first, second in combinations(exams, 2):
                groups[oid].append(set(first["values"]["exam"]) & set(second["values"]["exam"]))
            plans[oid] = (sorted(pairs, key=lambda p: p["sequence"]), sorted(exams, key=lambda p: p["sequence"]))
            # Add the new population equations to the existing full-scope privacy
            # basis, including old report/class/matched/complement populations.
            histories[oid].append({"eligible": True, "cohort": set(),
                "contributors": dict(enumerate(groups[oid])), "signature": ("alignment-populations", oid),
                "scales": {}, "pattern_values": {}, "student_values": {}})
        disclosed = learning.disclosed_populations(histories, active_enrolled, leadership_ids, extra_populations)

        def permitted(oid, population):
            return bool(population) and (oid in leadership_ids or frozenset(population) in disclosed)

        def means(measures, population):
            return {m: learning.exact_average([measures[m][sid] for sid in population]) for m in measures}

        results = []
        chosen = [o for o in selection.matching(filters) if not filters.get("offering_id") or o.id == filters["offering_id"]]
        for offering in chosen:
            oid = offering.id
            pairs, exams = plans.get(oid, ([], []))
            entry = {"id": oid, "course": offering.course.code, "section": offering.section.code,
                "campus": offering.campus.code, "pairs": [], "exams": [], "exam_comparisons": [],
                "category": learning.INSUFFICIENT, "detail": "Two eligible official assessment pairs with compatible scales are required.",
                "matched": None, "changes": []}
            configuration = next(iter(self.rows.get(oid, {}).values()), None)
            entry["configuration"] = ({k: configuration[k] for k in ("template", "profile", "base")}
                                      if configuration else None)
            for pair in pairs:
                allowed = permitted(oid, pair["common"])
                # Legacy pairs may be described, EXEMPT/missing policy may not.
                descriptive = next(r for r in self.rows[oid].values() if r["code"] == pair["destination"])["policy"]["descriptive"]
                values = means(pair["measures"], pair["common"]) if allowed and descriptive else dict.fromkeys(METRICS)
                entry["pairs"].append({"source": pair["source"], "destination": pair["destination"],
                    "type": pair["type"], "official": pair["official"], "count": len(pair["common"]) if allowed and descriptive else None,
                    "quiz": learning.displayed(values["quiz"]), "participation": learning.displayed(values["participation"]),
                    "exam": learning.displayed(values["exam"]),
                    "group": salted_hmac(POLICY_VERSION, encoded([offering.course_id, pair["source"],
                        pair["destination"], pair["type"], pair["signatures"]]), algorithm="sha256").hexdigest(),
                    "reason": pair["reason"] or ("" if allowed else "Results withheld by aggregate privacy rules.")})
            for row in exams:
                population = set(row["values"]["exam"])
                allowed = permitted(oid, population)
                entry["exams"].append({"period": row["label"], "type": row["policy"]["label"],
                    "code": row["code"],
                    "group": salted_hmac(POLICY_VERSION, encoded([offering.course_id,
                        row["mapping"]["signatures"].get("exam")]), algorithm="sha256").hexdigest(),
                    "average": learning.average(list(row["values"]["exam"].values())) if allowed else None,
                    "count": len(population) if allowed else None,
                    "reason": row["reason"] or row["policy"]["reason"]
                        or (row["mapping"]["reason"] if not row["mapping"]["exam"] else "")
                        or ("" if allowed else "Results unavailable or protected.")})
            for first, second in zip(exams, exams[1:]):
                population = set(first["values"]["exam"]) & set(second["values"]["exam"])
                compatible = (first["mapping"]["signatures"].get("exam") is not None and
                    first["mapping"]["signatures"].get("exam") == second["mapping"]["signatures"].get("exam"))
                allowed = permitted(oid, population)
                a = learning.exact_average([first["values"]["exam"][sid] for sid in population]) if allowed else None
                b = learning.exact_average([second["values"]["exam"][sid] for sid in population]) if allowed else None
                entry["exam_comparisons"].append({"from": first["label"], "to": second["label"],
                    "from_code": first["code"], "to_code": second["code"],
                    "previous": learning.displayed(a), "current": learning.displayed(b), "count": len(population) if allowed else None,
                    "difference": learning.displayed(b-a) if allowed and compatible else None,
                    "reason": "" if allowed and compatible else "Difference unavailable: incompatible scales, incomplete results or privacy protection."})
            # Require the latest two mapped pairs, never silently fall back from
            # an ongoing or exempt Final to an older, easier qualifying pair.
            if len(pairs) >= 2:
                first, second = pairs[-2:]
                population = first["common"] & second["common"]
                if first["reason"] or second["reason"] or not first["official"] or not second["official"]:
                    entry["detail"] = first["reason"] or second["reason"] or "Official examination policy is required."
                elif first["signatures"] != second["signatures"]:
                    entry["detail"] = "Assessment scales or computation rules differ between the pairs."
                elif not permitted(oid, population):
                    entry["detail"] = "Matched results are unavailable or protected by aggregate privacy rules."
                else:
                    a, b = means(first["measures"], population), means(second["measures"], population)
                    entry.update(category=movement(a, b), matched=len(population),
                        detail=f"{first['destination']} to {second['destination']}: the same students and all six measures.")
                    entry["changes"] = [{"indicator": m.title(), "previous": learning.displayed(a[m]),
                        "current": learning.displayed(b[m]), "difference": learning.displayed(b[m]-a[m]),
                        "direction": learning.performance_change(a[m], b[m])} for m in METRICS]
            # Each examination period has its own policy, completion and safe
            # contributor population. Do not let another period's exemption or
            # cross-period movement eligibility overwrite this period's status.
            entry["period_availability"] = {}
            for code in ("MIDTERM", "FINAL"):
                pair = next((p for p in pairs if p["destination"] == code), None)
                if pair is None:
                    status = "unavailable"
                elif pair["type"] == "Exempt":
                    status = "excluded"
                elif pair["pending"]:
                    status = "pending"
                elif pair["reason"] or not pair["official"]:
                    status = "unavailable"
                elif not permitted(oid, pair["common"]):
                    status = "withheld"
                else:
                    status = "eligible"
                entry["period_availability"][code] = status
            # Retain the existing internal whole-section field for compatibility
            # with earlier source consumers. Unified presentation uses only the
            # separate period statuses above, never this combined label.
            # Presentation-only, mutually exclusive section availability. The
            # existing calculations, classification and disclosure decisions above
            # remain authoritative. No individual population is serialized.
            if any(p["type"] == "Exempt" for p in pairs):
                entry["availability"] = "excluded"
            elif any(p["pending"] for p in pairs):
                entry["availability"] = "pending"
            elif (not pairs or any(p["reason"] or not p["official"] for p in pairs)
                  or (len(pairs) >= 2 and (pairs[-2]["signatures"] != pairs[-1]["signatures"]
                      or not pairs[-2]["common"] & pairs[-1]["common"]))):
                entry["availability"] = "unavailable"
            elif any(not permitted(oid, p["common"]) for p in pairs) or (len(pairs) >= 2
                    and not permitted(oid, pairs[-2]["common"] & pairs[-1]["common"])):
                entry["availability"] = "withheld"
            else:
                entry["availability"] = "eligible"
            results.append(entry)
        counts = Counter(r["category"] for r in results if r["category"] in learning.PATTERNS)
        total = sum(counts.values())
        excluded = sum(any(p["type"] == "Exempt" for p in r["pairs"]) for r in results)
        patterns = [{"label": p, "count": counts[p], "percentage": learning.average([Decimal(counts[p])*100/total]) if total else None} for p in learning.PATTERNS]
        bars = [{"section": f"{r['course']} / {r['section']} / {p['destination']} ({p['type']})",
                 "quiz_grade": p["quiz"], "standing": p["participation"], "exam": p["exam"]} for r in results for p in r["pairs"]]
        return {"classifications": results, "patterns": patterns, "sections": len(results), "analyzed": total, "excluded": excluded,
                "insufficient": len(results)-total, "export_periods": [],
                "charts": {"bars": bars, "line": [], "line_mode": "descriptive", "patterns": patterns, "threshold": "3.00"}}


def build_report(selection, filters, now=None):
    return learning.build_report(selection, filters, now, assessment_collector=AlignmentCollector(selection, filters))

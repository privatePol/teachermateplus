"""Authorized, request-local read bundles. Nothing survives the request.

Configuration precedence mirrors FacultyGradingService and is regression-tested
against it. The inherited formula strategy and arithmetic remain authoritative.
"""
from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import BooleanField, Case, F, Q, Value, When, Window
from django.db.models.functions import RowNumber

from apps.attendance.models import AttendanceRecord, AttendanceSession
from apps.grading.models import (CourseBaseValueOverride, CourseTemplateAssignment,
    GradeActivity, GradingTemplate, GradingTemplateComponent, StudentActivityScore, TenantGradingProfile)
from apps.grading.services import FacultyGradingService
from apps.tenants.models import Department


def bounded_records(model, groups, partition, fields):
    """Keep bind parameters bounded even with many physical periods per class.

    A complete class/period stays in one batch so its overflow sentinel cannot
    be lost across chunks. Window limits bound Python materialization, while
    the database still evaluates the complete eligible source population.
    """
    chunks, predicates, size = [], Q(pk__in=[]), 0
    for predicate, parameters in groups:
        if size and size + parameters > 5000:
            chunks.append(predicates)
            predicates, size = Q(pk__in=[]), 0
        predicates |= predicate
        size += parameters
    if size:
        chunks.append(predicates)
    for predicate in chunks:
        yield from model.objects.filter(predicate, is_active=True).annotate(
            bundle_index=Window(RowNumber(), partition_by=[F(name) for name in partition], order_by="id")
        ).filter(bundle_index__lte=20001).order_by().values(*fields)


class MonitorReadBundle:
    def __init__(self, offerings, periods, cohorts):
        self.components = defaultdict(list)
        for component in GradingTemplateComponent.objects.filter(template_period_id__in=periods, is_active=True).prefetch_related(
                "subcomponents__details").order_by("sort_order", "id"):
            self.components[component.template_period_id].append(component)
        self.activities, self.scores = defaultdict(list), defaultdict(list)
        self.sessions, self.attendance = defaultdict(set), defaultdict(list)
        pairs = Q(pk__in=[])
        for oid, pid in cohorts:
            offering = offerings[oid]
            pairs |= Q(offering_id=oid, template_period_id=pid, tenant_id=offering.tenant_id, campus_id=offering.campus_id)
        activities = GradeActivity.objects.filter(pairs, is_active=True,
            template_component__template_period_id=F("template_period_id")).filter(
            Q(template_subcomponent__isnull=True) | Q(template_subcomponent__template_component_id=F("template_component_id"))).filter(
            Q(template_detail__isnull=True) | Q(template_detail__template_subcomponent_id=F("template_subcomponent_id"))).annotate(
            bundle_index=Window(RowNumber(), partition_by=[F("offering_id"), F("template_period_id")], order_by="id")
        ).filter(bundle_index__lte=201).select_related("template_component", "template_subcomponent", "template_detail").order_by("id")
        score_groups = []
        for activity in activities:
            pair = activity.offering_id, activity.template_period_id
            self.activities[pair].append(activity)
        for pair, activities in self.activities.items():
            if len(activities) <= 200 and cohorts[pair]:
                score_groups.append((Q(activity_id__in=[a.id for a in activities], student_id__in=cohorts[pair]), len(activities) + len(cohorts[pair])))
        scores = bounded_records(StudentActivityScore, score_groups, ("activity__offering_id", "activity__template_period_id"),
            ("activity__offering_id", "activity__template_period_id", "student_id", "activity_id", "raw_score", "computed_score"))
        for score in scores:
            self.scores[(score.pop("activity__offering_id"), score.pop("activity__template_period_id"))].append(score)
        attendance_pairs = Q(pk__in=[])
        for oid, pid in cohorts:
            if any(s.is_active and s.is_attendance_component for c in self.components[pid] for s in c.subcomponents.all()):
                offering = offerings[oid]
                attendance_pairs |= Q(offering_id=oid, template_period_id=pid, tenant_id=offering.tenant_id, campus_id=offering.campus_id)
        sessions = AttendanceSession.objects.filter(attendance_pairs, is_active=True).annotate(
            bundle_index=Window(RowNumber(), partition_by=[F("offering_id"), F("template_period_id")], order_by="id")
        ).filter(bundle_index__lte=201).values("id", "offering_id", "template_period_id")
        record_groups = []
        for session in sessions:
            self.sessions[(session["offering_id"], session["template_period_id"])].add(session["id"])
        for pair, ids in self.sessions.items():
            if len(ids) <= 200 and cohorts[pair]:
                record_groups.append((Q(session_id__in=ids, student_id__in=cohorts[pair]), len(ids) + len(cohorts[pair])))
        records = bounded_records(AttendanceRecord, record_groups, ("session__offering_id", "session__template_period_id"),
            ("session__offering_id", "session__template_period_id", "student_id", "session_id", "status_code"))
        for record in records:
            self.attendance[(record.pop("session__offering_id"), record.pop("session__template_period_id"))].append(record)


def configuration_service(offerings):
    """Return a private service class with this request's configuration only."""
    tenant_ids = {o.tenant_id for o in offerings.values()}
    course_ids = {o.course_id for o in offerings.values()}
    parents = dict(Department.objects.filter(tenant_id__in=tenant_ids).values_list("id", "parent_id"))
    # Keep the Gradebook's backend-specific iexact semantics (including its
    # collation/Unicode behavior). Evaluate each distinct requested type inside
    # the existing profile query, never with Python case normalization or a
    # query per offering. Generic membership uses the same database predicates.
    course_types = {(o.course.course_type or "").strip() for o in offerings.values()}
    type_aliases = {value: f"monitor_type_{i}" for i, value in enumerate(sorted(course_types - {""}))}
    generic_type = Q(course_type__isnull=True) | Q(course_type="")
    type_matches = {"monitor_generic_type": Case(When(generic_type, then=Value(True)),
        default=Value(False), output_field=BooleanField())}
    type_matches.update({alias: Case(When(generic_type | Q(course_type__iexact=value), then=Value(True)),
        default=Value(False), output_field=BooleanField()) for value, alias in type_aliases.items()})
    profiles = list(TenantGradingProfile.objects.filter(tenant_id__in=tenant_ids, is_active=True,
        grading_template__is_active=True, grading_template__is_published=True).annotate(**type_matches).select_related("grading_template"))
    assignments = list(CourseTemplateAssignment.objects.filter(course_id__in=course_ids, is_active=True,
        grading_template__is_active=True, grading_template__is_published=True).select_related("grading_template").order_by("-created_at"))
    overrides = list(CourseBaseValueOverride.objects.filter(course_id__in=course_ids, is_active=True).order_by("-effective_from_term_id", "-created_at"))
    templates = list(GradingTemplate.objects.filter(tenant_id__in=tenant_ids, is_active=True, is_published=True).order_by("-published_at", "-created_at"))
    resolved_profiles, resolved_templates, thresholds = {}, {}, {}

    class RequestGradingService(FacultyGradingService):
        @classmethod
        def resolve_passing_threshold_trace(cls, offering):
            profile = cls.resolve_grading_profile_for_offering(offering)
            template = cls.resolve_template_for_offering(offering) if not profile or profile.passing_grade_threshold is None else None
            key = offering.tenant_id, profile.id if profile else None, template.id if template else None
            if key not in thresholds:
                thresholds[key] = super().resolve_passing_threshold_trace(offering)
            return thresholds[key]

        @classmethod
        def resolve_grading_profile_for_offering(cls, offering):
            if offering.id in resolved_profiles:
                return resolved_profiles[offering.id]
            ancestors, current = [], offering.department_id
            while current and current not in ancestors and current in parents:
                ancestors.append(current)
                current = parents[current]
            rank = {did: i for i, did in enumerate(ancestors)}
            program_id = offering.program_id or (offering.section.program_id if offering.section_id else None)
            course_type = (offering.course.course_type or "").strip()
            term_type = (offering.term.term_type or "").strip()
            choices = [p for p in profiles if p.tenant_id == offering.tenant_id
                and p.campus_id in (None, offering.campus_id) and (p.department_id is None or p.department_id in ancestors)
                and p.program_id in (None, program_id) and p.course_id in (None, offering.course_id)
                and p.term_type in (None, "", term_type) and p.effective_from_term_id in (None, offering.term_id)
                and (getattr(p, type_aliases[course_type]) if course_type else
                     (p.monitor_generic_type or p.course_id == offering.course_id))]
            def key(p):
                return (-bool(p.course_id), -bool((p.course_type or "").strip()), -bool(p.program_id),
                    -bool(p.department_id), rank.get(p.department_id, 999), -bool(p.campus_id),
                    -bool(term_type and (p.term_type or "").strip() == term_type), -bool(p.effective_from_term_id),
                    p.priority, 0 if p.is_default else 1, -p.id)
            resolved_profiles[offering.id] = min(choices, key=key) if choices else None
            return resolved_profiles[offering.id]

        @classmethod
        def resolve_template_for_offering(cls, offering):
            if offering.id not in resolved_templates:
                choices = [a for a in assignments if a.course_id == offering.course_id]
                assignment = next((a for a in choices if a.effective_from_term_id == offering.term_id), None)
                assignment = assignment or next((a for a in choices if a.effective_from_term_id is None), None)
                profile = cls.resolve_grading_profile_for_offering(offering)
                template = (assignment.grading_template if assignment else profile.grading_template if profile else
                    next((t for t in templates if t.tenant_id == offering.tenant_id), None))
                if template is None:
                    raise ValidationError("No published grading template is assigned for this course offering.")
                resolved_templates[offering.id] = template
            return resolved_templates[offering.id]

        @classmethod
        def resolve_base_value(cls, offering, template):
            override = next((o for o in overrides if o.course_id == offering.course_id and o.effective_from_term_id in (None, offering.term_id)), None)
            profile = cls.resolve_grading_profile_for_offering(offering)
            values = (override.base_value if override else None, profile.default_base_value if profile else None,
                offering.course.default_base_value, template.default_base_value, Decimal("50"))
            return Decimal(next(value for value in values if value is not None))

    return RequestGradingService

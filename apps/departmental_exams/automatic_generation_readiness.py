from __future__ import annotations

from collections import Counter, defaultdict
from urllib.parse import urlencode

from django.core.exceptions import PermissionDenied
from django.db.models import Count, Prefetch
from django.utils import timezone

from .blueprint_services import ContributorRosterReadinessService
from .generation_readiness import Stage6ReadinessService
from .models import (
    CycleCourse,
    CycleCourseOffering,
    ExamGenerationRevision,
    ExaminationCycle,
    FacultyContribution,
)
from .services import DepartmentalExamAuthorizationService


class AutomaticGenerationReadinessReport:
    """Compose aggregate, read-only Automatic Generation management readiness."""

    FILTER_PARAMETER_NAMES = ("cycle", "period", "course")

    def __init__(self, *, tenant_id, user, params, now=None):
        self.tenant_id = tenant_id
        self.user = user
        self.params = params
        self.now = now or timezone.now()

    def _authorized_scope(self):
        base = CycleCourse.objects.filter(
            cycle__tenant_id=self.tenant_id,
            cycle__processing_mode=(
                ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
            ),
        )
        scope_rows = CycleCourseOffering.objects.filter(
            cycle_course__in=base,
        ).values_list(
            "cycle_course_id",
            "cycle_course__inclusion_status",
            "campus_id",
        )
        course_scopes = defaultdict(
            lambda: {"inclusion_status": None, "campus_ids": set()}
        )
        for course_id, inclusion_status, campus_id in scope_rows:
            course_scopes[course_id]["inclusion_status"] = inclusion_status
            course_scopes[course_id]["campus_ids"].add(campus_id)

        permission = DepartmentalExamAuthorizationService.MANAGE_GENERATION_PERMISSION
        permission_map = (
            DepartmentalExamAuthorizationService.automatic_scope_permission_map(
                user=self.user,
                tenant_id=self.tenant_id,
                course_scopes=course_scopes,
                permissions=(permission,),
                # Included rows use downstream generation-management authority;
                # EXEMPT rows use the status-independent inclusion authority.  For
                # the same permission/campus scopes, both resolve through this
                # shared status-independent map.
                require_included=False,
            )
        )
        authorized_course_ids = {
            course_id
            for course_id, permissions in permission_map.items()
            if permission in permissions
        }
        if not authorized_course_ids:
            if not base.exists():
                DepartmentalExamAuthorizationService.require_automatic_tenant_permission(
                    user=self.user,
                    permission=permission,
                    tenant_id=self.tenant_id,
                )
                return {"course_ids": set(), "cycle_choices": []}
            raise PermissionDenied(
                "No Automatic Generation readiness data is available in your exact scope."
            )

        authorized_cycle_ids = set(
            base.filter(pk__in=authorized_course_ids).values_list(
                "cycle_id", flat=True
            )
        )
        cycle_choices = list(
            ExaminationCycle.objects.filter(
                pk__in=authorized_cycle_ids,
                tenant_id=self.tenant_id,
                processing_mode=(
                    ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
                ),
            )
            .select_related("academic_year", "term")
            .order_by(
                "-academic_year__start_date",
                "-term_id",
                "-exam_period",
                "-id",
            )
        )
        return {
            "course_ids": authorized_course_ids,
            "cycle_choices": cycle_choices,
        }

    def _load_courses(self, *, course_ids):
        contribution_queryset = (
            FacultyContribution.objects.select_related("faculty_user", "source_campus")
            .annotate(saved_question_count=Count("questions"))
            .order_by("faculty_user__last_name", "faculty_user__first_name", "id")
        )
        snapshot_queryset = CycleCourseOffering.objects.select_related("campus").order_by(
            "campus__name", "campus_id", "offering_id"
        )
        current_generation_queryset = ExamGenerationRevision.objects.filter(
            current_marker=1,
            status=ExamGenerationRevision.Status.GENERATED,
        ).only("id", "cycle_course_id", "revision_number", "status", "current_marker")
        return list(
            CycleCourse.objects.filter(
                pk__in=course_ids,
                cycle__tenant_id=self.tenant_id,
                cycle__processing_mode=(
                    ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
                ),
            )
            .select_related(
                "cycle",
                "cycle__tenant",
                "cycle__academic_year",
                "cycle__term",
                "course",
                "configuration",
            )
            .prefetch_related(
                Prefetch("offering_snapshots", queryset=snapshot_queryset),
                Prefetch("faculty_contributions", queryset=contribution_queryset),
                Prefetch(
                    "generation_revisions",
                    queryset=current_generation_queryset,
                    to_attr="current_generated_revisions",
                ),
            )
            .order_by("course__code", "course_id")
        )

    @staticmethod
    def _parse_scoped_id(raw_value, valid_ids):
        try:
            parsed = int(raw_value)
        except (TypeError, ValueError):
            return None, True
        return (parsed, False) if parsed in valid_ids else (None, True)

    def _filtered_scope(self, authorized_scope):
        authorized_course_ids = authorized_scope["course_ids"]
        cycle_choices = authorized_scope["cycle_choices"]
        cycles_by_id = {cycle.id: cycle for cycle in cycle_choices}
        raw_values = {
            name: (self.params.get(name, "") or "").strip()
            for name in self.FILTER_PARAMETER_NAMES
        }
        supplied = {name: name in self.params for name in self.FILTER_PARAMETER_NAMES}
        invalid = False

        if supplied["cycle"] and raw_values["cycle"]:
            selected_cycle_id, invalid_cycle = self._parse_scoped_id(
                raw_values["cycle"], cycles_by_id
            )
            invalid = invalid or invalid_cycle
        elif supplied["cycle"]:
            selected_cycle_id = None
            invalid = True
        else:
            selected_cycle_id = cycle_choices[0].id if cycle_choices else None

        selected_period = raw_values["period"]
        if selected_period and selected_period not in ExaminationCycle.ExamPeriod.values:
            invalid = True

        cycle_course_choices = []
        if selected_cycle_id is not None and not invalid:
            cycle_course_choices = list(
                CycleCourse.objects.filter(
                    pk__in=authorized_course_ids,
                    cycle_id=selected_cycle_id,
                    cycle__tenant_id=self.tenant_id,
                )
                .select_related("course")
                .order_by("course__code", "course_id")
            )
        courses_by_id = {
            course.course_id: course.course for course in cycle_course_choices
        }

        selected_course_id = None
        if raw_values["course"]:
            selected_course_id, invalid_course = self._parse_scoped_id(
                raw_values["course"], courses_by_id
            )
            invalid = invalid or invalid_course
        elif supplied["course"]:
            invalid = True

        filtered_course_ids = []
        if selected_cycle_id is not None and not invalid:
            selected_cycle = cycles_by_id[selected_cycle_id]
            filtered_course_ids = [
                course.id
                for course in cycle_course_choices
                if (
                    not selected_period
                    or selected_cycle.exam_period == selected_period
                )
                and (
                    selected_course_id is None
                    or course.course_id == selected_course_id
                )
            ]

        query_values = {
            name: raw_values[name]
            for name in self.FILTER_PARAMETER_NAMES
            if supplied[name]
        }
        if not supplied["cycle"] and selected_cycle_id is not None:
            query_values["cycle"] = selected_cycle_id
        return {
            "course_ids": filtered_course_ids,
            "cycle_choices": cycle_choices,
            "course_choices": sorted(
                courses_by_id.values(), key=lambda course: (course.code, course.id)
            ),
            "period_choices": ExaminationCycle.ExamPeriod.choices,
            "selected_cycle_id": selected_cycle_id,
            "selected_period": selected_period,
            "selected_course_id": selected_course_id,
            "filters_invalid": invalid,
            # Preserve every submitted allowlisted value so screen and print both
            # fail closed for malformed, stale, or unauthorized filter values.
            "filter_query": urlencode(query_values),
        }

    @staticmethod
    def _faculty_completion(*, course, configuration, roster):
        effective_quota = (
            configuration.questions_required_per_faculty if configuration else None
        )
        source = (
            configuration.questions_required_per_faculty_source
            if configuration
            else None
        )
        if effective_quota is None:
            quota_display = "Not configured"
        elif source == "DEFAULT":
            quota_display = f"{effective_quota} (cycle default)"
        elif source == "OVERRIDE":
            quota_display = f"{effective_quota} (course override)"
        else:
            quota_display = f"{effective_quota} (source not configured)"

        authoritative = bool(roster and roster.current)
        if authoritative:
            required = roster.required_active_count
            completed = roster.submitted_required_count
            incomplete = roster.incomplete_active_count
            if required:
                completion_text = (
                    f"{completed} of {required} current faculty completed their required "
                    "contribution."
                )
            else:
                completion_text = (
                    "No required faculty contributors are currently on the roster."
                )
        else:
            required = completed = incomplete = None
            completion_text = "Current faculty completion is unavailable until the roster is current."

        return {
            "required_quota": effective_quota,
            "required_quota_source": source,
            "required_quota_display": quota_display,
            "required_count": required,
            "completed_count": completed,
            "incomplete_count": incomplete,
            "completion_text": completion_text,
            "authoritative": authoritative,
            "roster_initialized": bool(
                configuration
                and configuration.contributor_roster_initialized_at is not None
            ),
            "roster_current": bool(roster and roster.current),
        }

    @staticmethod
    def _contribution_progress(*, course, configuration, participating_campuses):
        submitted_contributions = [
            contribution
            for contribution in course.faculty_contributions.all()
            if contribution.status == FacultyContribution.Status.SUBMITTED
        ]
        submitted_question_volume = sum(
            contribution.saved_question_count
            for contribution in submitted_contributions
        )
        participating_by_id = {
            campus_id: campus_name for campus_id, campus_name in participating_campuses
        }
        submitted_campuses = tuple(
            name
            for _campus_id, name in sorted(
                {
                    contribution.source_campus_id: participating_by_id[
                        contribution.source_campus_id
                    ]
                    for contribution in submitted_contributions
                    if contribution.saved_question_count
                    and contribution.source_campus_id in participating_by_id
                }.items(),
                key=lambda item: (item[1].casefold(), item[0]),
            )
        )
        effective_quota = (
            configuration.questions_required_per_faculty if configuration else None
        )
        expected_monitoring_volume = (
            effective_quota * len(participating_by_id)
            if effective_quota is not None and participating_by_id
            else None
        )
        return {
            "submitted_question_volume": submitted_question_volume,
            "expected_monitoring_volume": expected_monitoring_volume,
            "submitted_campuses": submitted_campuses,
        }

    @staticmethod
    def _pool_actions(pool):
        actions = []
        missing_campus_names = {
            campus_name
            for blocker in pool.get("blockers", ())
            if blocker["code"] == "MISSING_CAMPUS_REPRESENTATION"
            for campus_name in blocker.get("campus_names", ())
        }
        for shortage in pool.get("shortages", ()):
            missing = shortage["required"] - shortage["available"]
            if shortage["dimension"] == "campus":
                if shortage["label"] not in missing_campus_names:
                    actions.append(
                        f"Add {missing} usable questions from {shortage['label']}."
                    )
            elif shortage["dimension"] == "difficulty":
                actions.append(f"Add {missing} usable {shortage['label']} questions.")
            elif shortage["dimension"] == "total":
                actions.append(f"Add {missing} more usable unique questions.")
        blocker_codes = {item["code"] for item in pool.get("blockers", ())}
        if "CONFIGURATION_MISSING" in blocker_codes:
            actions.append("Configure the course examination.")
        if "FINAL_COUNT_INVALID" in blocker_codes:
            actions.append("Configure a valid Final Exam Items value.")
        if "DIFFICULTY_POLICY_INVALID" in blocker_codes:
            actions.append("Restore the required Easy, Moderate, and Difficult distribution.")
        if "CAMPUS_PARTICIPATION_MISSING" in blocker_codes:
            actions.append("Add at least one participating campus offering snapshot.")
        if "HARD_CONSTRAINTS_INFEASIBLE" in blocker_codes:
            actions.append(
                "Add usable unique questions that satisfy the required difficulty distribution."
            )
        if "FEASIBILITY_LIMIT" in blocker_codes:
            actions.append(
                "Resolve the automatic readiness processing limit with the system administrator."
            )
        missing_campus_blocker = next(
            (
                item
                for item in pool.get("blockers", ())
                if item["code"] == "MISSING_CAMPUS_REPRESENTATION"
            ),
            None,
        )
        if missing_campus_blocker:
            for campus_name in missing_campus_blocker.get("campus_names", ()):
                actions.append(f"No usable submitted questions from {campus_name}.")
        if not actions and pool.get("blockers"):
            actions.append(
                "Resolve the question-pool requirements before automatic generation."
            )
        return tuple(dict.fromkeys(actions))

    @staticmethod
    def _pool_warnings(pool):
        warnings = []
        for warning in pool.get("warnings", ()):
            if warning["code"] == "MISSING_CAMPUS_REPRESENTATION":
                for campus_name in warning.get("campus_names", ()):
                    warnings.append(f"No usable submitted questions from {campus_name}.")
        return tuple(dict.fromkeys(warnings))

    def _execution_status(self, *, course, configuration, roster, pool, current):
        if course.inclusion_status == CycleCourse.InclusionStatus.EXEMPT:
            return "EXEMPT", ("No generation required.",)
        if current:
            return "GENERATED", ("No action needed.",)
        if course.cycle.status != ExaminationCycle.Status.OPEN:
            return "BLOCKED", ("Open the examination cycle.",)
        if configuration is None:
            return "BLOCKED", ("Configure the course examination.",)
        if (
            configuration.questions_required_per_faculty is None
            or configuration.questions_required_per_faculty_source
            not in ("DEFAULT", "OVERRIDE")
        ):
            return "BLOCKED", ("Configure the contribution quota.",)
        if configuration.contribution_deadline is None:
            return "BLOCKED", ("Set the contribution deadline.",)
        if configuration.workflow_status == configuration.WorkflowStatus.DRAFT:
            return "BLOCKED", ("Open the course for faculty contribution.",)
        if configuration.workflow_status not in (
            configuration.WorkflowStatus.OPEN,
            configuration.WorkflowStatus.CLOSED,
        ):
            return "BLOCKED", ("Resolve the course contribution lifecycle.",)
        if (
            configuration.automatic_processing_status
            == configuration.AutomaticProcessingStatus.ERROR
        ):
            return "BLOCKED", (
                "Resolve the automatic processing error with the system administrator.",
            )
        if configuration is None:
            status_detail = ""
        elif configuration.contributor_roster_initialized_at is None:
            return "BLOCKED", ("Initialize the contributor roster.",)
        if roster is None or not roster.current:
            return "BLOCKED", ("Synchronize the contributor roster.",)
        if roster.unresolved_blocked_count:
            count = roster.unresolved_blocked_count
            return "BLOCKED", (
                f"Resolve {count} Blocked Draft contributor record{'s' if count != 1 else ''}.",
            )
        if (
            course.cycle.automatic_contributor_completion_policy
            == ExaminationCycle.AutomaticContributorCompletionPolicy.REQUIRE_ALL
            and roster.incomplete_active_count
        ):
            count = roster.incomplete_active_count
            return "FACULTY INCOMPLETE", (
                f"{count} current faculty {'has' if count == 1 else 'have'} not completed "
                "their required contribution.",
            )
        if pool.get("invalid_question_count"):
            count = pool["invalid_question_count"]
            return "BLOCKED", (
                f"Resolve {count} unusable Submitted question row"
                f"{'s' if count != 1 else ''}.",
            )
        if not pool["ready"]:
            actions = self._pool_actions(pool)
            question_shortage_codes = {
                "QUESTION_SHORTAGES",
                "UNIQUE_QUESTION_SHORTAGES",
                "HARD_CONSTRAINTS_INFEASIBLE",
            }
            blocker_codes = {item["code"] for item in pool.get("blockers", ())}
            status = (
                "NEEDS QUESTIONS"
                if blocker_codes and blocker_codes <= question_shortage_codes
                else "BLOCKED"
            )
            return status, actions
        deadline = configuration.active_contribution_deadline
        if deadline is None:
            return "BLOCKED", ("Set the contribution deadline.",)
        if self.now < deadline:
            return "WAITING FOR DEADLINE", ("Wait for the contribution deadline.",)
        return "READY FOR GENERATION", ("No action needed.",)

    def _row(self, course):
        snapshots = list(course.offering_snapshots.all())
        participating_campuses = tuple(
            sorted(
                {
                    snapshot.campus_id: snapshot.campus.name
                    for snapshot in snapshots
                }.items(),
                key=lambda item: (item[1].casefold(), item[0]),
            )
        )
        campuses = tuple(name for _campus_id, name in participating_campuses)
        configuration = getattr(course, "configuration", None)
        current = next(iter(course.current_generated_revisions), None)
        if course.inclusion_status == CycleCourse.InclusionStatus.EXEMPT:
            pool = None
            roster = None
            faculty = self._faculty_completion(
                course=course, configuration=configuration, roster=roster
            )
            status, action_items = self._execution_status(
                course=course,
                configuration=configuration,
                roster=roster,
                pool={"ready": False},
                current=current,
            )
            return {
                "cycle_course": course,
                "campuses": campuses,
                "configuration": configuration,
                "faculty": faculty,
                "contribution_progress": None,
                "generation_status": status,
                "status_detail": "",
                "action_items": action_items,
            }

        pool = Stage6ReadinessService.evaluate_automatic_pool(cycle_course=course)
        roster = (
            ContributorRosterReadinessService.evaluate(
                cycle_course=course, configuration=configuration
            )
            if configuration is not None
            else None
        )
        faculty = self._faculty_completion(
            course=course, configuration=configuration, roster=roster
        )
        contribution_progress = self._contribution_progress(
            course=course,
            configuration=configuration,
            participating_campuses=participating_campuses,
        )
        pool_actions = self._pool_actions(pool)
        pool_warnings = self._pool_warnings(pool)
        status, action_items = self._execution_status(
            course=course,
            configuration=configuration,
            roster=roster,
            pool=pool,
            current=current,
        )
        action_items = list(action_items)
        if (
            status in ("WAITING FOR DEADLINE", "READY FOR GENERATION")
            and faculty["authoritative"]
            and faculty["incomplete_count"]
            and course.cycle.automatic_contributor_completion_policy
            == ExaminationCycle.AutomaticContributorCompletionPolicy.SUFFICIENT_POOL
        ):
            count = faculty["incomplete_count"]
            action_items.append(
                f"{count} current faculty {'has' if count == 1 else 'have'} not completed "
                "their required contribution."
            )
        action_items.extend(pool_warnings)
        action_items = tuple(dict.fromkeys(action_items))
        if configuration is None:
            status_detail = ""
        elif configuration.contributor_roster_initialized_at is None:
            status_detail = "Contributor roster has not been initialized."
        elif roster is None or not roster.current:
            status_detail = (
                "Contributor roster needs synchronization because the eligible faculty "
                "list has changed."
            )
        else:
            status_detail = ""
        return {
            "cycle_course": course,
            "campuses": campuses,
            "configuration": configuration,
            "faculty": faculty,
            "contribution_progress": contribution_progress,
            # Technical details remain internal inputs to the single management
            # status and concise action list; they are not separate report columns.
            "pool_actions": pool_actions,
            "pool_warnings": pool_warnings,
            "generation_status": status,
            "status_detail": status_detail,
            "action_items": action_items,
        }

    def build(self):
        authorized_scope = self._authorized_scope()
        scope = self._filtered_scope(authorized_scope)
        courses = self._load_courses(course_ids=scope.pop("course_ids"))
        rows = [self._row(course) for course in courses]
        status_counts = Counter(row["generation_status"] for row in rows)
        return {
            **scope,
            "rows": rows,
            "row_count": len(rows),
            "status_counts": dict(status_counts),
            "generated_at": timezone.localtime(self.now),
        }

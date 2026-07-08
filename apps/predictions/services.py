from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.attendance.models import AttendanceRecord, AttendanceSession
from apps.core.services.features import FeatureSettingsService
from apps.enrollment.models import Enrollment
from apps.grading.models import (
    DetailComputationMode,
    GradeActivity,
    StudentActivityScore,
    StudentFinalGrade,
    StudentPeriodGrade,
)
from apps.grading.services import FacultyGradingService
from apps.predictions.models import (
    PredictionAssumptionMode,
    PredictionDirtyQueue,
    PredictionSettingSnapshot,
    PredictionSnapshot,
    PredictionSummarySnapshot,
    PredictionViewLog,
    PredictionWhatIfDraft,
)
from apps.rbac.models import UserRole


@dataclass
class ScenarioScores:
    current: Decimal | None
    worst: Decimal | None
    best: Decimal | None
    encoded_count: int
    expected_count: int


class PredictionAccessService:
    @staticmethod
    def active_role_codes(user) -> set[str]:
        if getattr(user, "is_superuser", False):
            return {"SUPER_ADMIN"}
        return {
            str(code).strip().upper()
            for code in UserRole.objects.filter(
                user=user,
                is_active=True,
                role__is_active=True,
            ).values_list("role__code", flat=True)
            if str(code).strip()
        }

    @classmethod
    def primary_role_code(cls, user) -> str:
        role_codes = cls.active_role_codes(user)
        for preferred in (
            "SUPER_ADMIN",
            "TENANT_ADMIN",
            "CAMPUS_ADMIN",
            "COLLEGE_DEAN",
            "DEAN",
            "REGISTRAR",
            "FACULTY",
        ):
            if preferred in role_codes:
                return preferred
        return next(iter(sorted(role_codes)), "")


class PredictionDirtyQueueService:
    @classmethod
    def mark_dirty(cls, *, offering, template_period=None, student=None, reason: str):
        return PredictionDirtyQueue.objects.create(
            tenant_id=offering.tenant_id,
            campus_id=offering.campus_id,
            offering=offering,
            template_period=template_period,
            student=student,
            reason=reason,
            status=PredictionDirtyQueue.Status.PENDING,
        )


class PredictionComputationService:
    COMPUTATION_VERSION = "v3"

    @staticmethod
    def _round(value: Decimal | None) -> Decimal | None:
        if value is None:
            return None
        return FacultyGradingService._round(Decimal(value))

    @classmethod
    def _average(cls, values: list[Decimal]) -> Decimal | None:
        if not values:
            return None
        return cls._round(sum(values) / Decimal(len(values)))

    @classmethod
    def _setting_snapshot(cls, *, offering, user=None):
        return PredictionSettingSnapshot.objects.create(
            tenant_id=offering.tenant_id,
            campus_id=offering.campus_id,
            assumption_mode=FeatureSettingsService.get_grade_prediction_default_assumption(tenant_id=offering.tenant_id),
            show_best_case=FeatureSettingsService.show_grade_prediction_best_case(tenant_id=offering.tenant_id),
            show_worst_case=FeatureSettingsService.show_grade_prediction_worst_case(tenant_id=offering.tenant_id),
            show_target_needed=FeatureSettingsService.show_grade_prediction_target_needed(tenant_id=offering.tenant_id),
            generated_by_user=user if getattr(user, "is_authenticated", False) else None,
        )

    @classmethod
    def _source_version(cls, *, offering, template_period) -> str:
        timestamps = [
            GradeActivity.objects.filter(
                offering_id=offering.id,
                template_period_id=template_period.id,
            ).aggregate(last=Max("updated_at"))["last"],
            StudentActivityScore.objects.filter(
                activity__offering_id=offering.id,
                activity__template_period_id=template_period.id,
                activity__is_active=True,
                is_active=True,
            ).aggregate(last=Max("updated_at"))["last"],
            AttendanceSession.objects.filter(
                offering_id=offering.id,
                template_period_id=template_period.id,
                is_active=True,
            ).aggregate(last=Max("updated_at"))["last"],
            AttendanceRecord.objects.filter(
                session__offering_id=offering.id,
                session__template_period_id=template_period.id,
                session__is_active=True,
                is_active=True,
            ).aggregate(last=Max("updated_at"))["last"],
            StudentPeriodGrade.objects.filter(
                offering_id=offering.id,
                template_period_id=template_period.id,
            ).aggregate(last=Max("computed_at"))["last"],
        ]
        valid = [timestamp for timestamp in timestamps if timestamp]
        data_version = max(valid).isoformat() if valid else "0"
        return f"{cls.COMPUTATION_VERSION}|{data_version}"

    @classmethod
    def _load_activity_data(cls, *, offering, template_period):
        activities = list(
            GradeActivity.objects.filter(
                offering_id=offering.id,
                template_period_id=template_period.id,
                is_active=True,
            ).select_related(
                "template_component",
                "template_subcomponent",
                "template_detail",
            )
        )
        grouped = {}
        for activity in activities:
            key = (activity.template_component_id, activity.template_subcomponent_id, activity.template_detail_id)
            grouped.setdefault(key, []).append(activity)
        score_lookup = {
            (row.student_id, row.activity_id): Decimal(row.computed_score or 0)
            for row in StudentActivityScore.objects.filter(
                activity__offering_id=offering.id,
                activity__template_period_id=template_period.id,
                activity__is_active=True,
                is_active=True,
            ).select_related("activity")
        }
        return activities, grouped, score_lookup

    @classmethod
    def _load_attendance_data(cls, *, offering, template_period, base_value: Decimal):
        sessions = list(
            AttendanceSession.objects.filter(
                offering_id=offering.id,
                template_period_id=template_period.id,
                is_active=True,
            )
        )
        record_lookup = {}
        for record in AttendanceRecord.objects.filter(
            session__offering_id=offering.id,
            session__template_period_id=template_period.id,
            session__is_active=True,
            is_active=True,
        ):
            raw = FacultyGradingService.ATTENDANCE_SCORE_MAP.get(record.status_code, Decimal("0"))
            record_lookup[(record.student_id, record.session_id)] = FacultyGradingService.compute_activity_score(
                raw_score=raw,
                total_score=Decimal("100"),
                base_value=base_value,
                score_input_mode="DIRECT_PERCENTAGE",
            )
        return sessions, record_lookup

    @classmethod
    def _activity_bounds(cls, *, activity, base_value: Decimal) -> tuple[Decimal, Decimal]:
        score_input_mode = FacultyGradingService.resolve_score_input_mode(
            template_component=activity.template_component,
            template_subcomponent=activity.template_subcomponent,
            template_detail=activity.template_detail,
        )
        full_raw = Decimal("100") if score_input_mode == "DIRECT_PERCENTAGE" else Decimal(activity.total_score or 0)
        worst = FacultyGradingService.compute_activity_score(
            raw_score=Decimal("0"),
            total_score=Decimal(activity.total_score or 100),
            base_value=base_value,
            score_input_mode=score_input_mode,
        )
        best = FacultyGradingService.compute_activity_score(
            raw_score=full_raw,
            total_score=Decimal(activity.total_score or 100),
            base_value=base_value,
            score_input_mode=score_input_mode,
        )
        return worst, best

    @classmethod
    def _resolve_activity_group(cls, *, student_id: int, activities: list, score_lookup: dict, base_value: Decimal) -> ScenarioScores:
        existing = []
        worst_values = []
        best_values = []
        encoded_count = 0
        for activity in activities:
            key = (student_id, activity.id)
            if key in score_lookup:
                value = score_lookup[key]
                existing.append(value)
                worst_values.append(value)
                best_values.append(value)
                encoded_count += 1
            else:
                worst, best = cls._activity_bounds(activity=activity, base_value=base_value)
                worst_values.append(worst)
                best_values.append(best)
        return ScenarioScores(
            current=cls._average(existing),
            worst=cls._average(worst_values),
            best=cls._average(best_values),
            encoded_count=encoded_count,
            expected_count=len(activities),
        )

    @classmethod
    def _resolve_attendance_group(cls, *, student_id: int, sessions: list, record_lookup: dict, base_value: Decimal) -> ScenarioScores:
        existing = []
        worst_values = []
        best_values = []
        encoded_count = 0
        worst = FacultyGradingService.compute_activity_score(
            raw_score=Decimal("0"),
            total_score=Decimal("100"),
            base_value=base_value,
            score_input_mode="DIRECT_PERCENTAGE",
        )
        best = FacultyGradingService.compute_activity_score(
            raw_score=Decimal("100"),
            total_score=Decimal("100"),
            base_value=base_value,
            score_input_mode="DIRECT_PERCENTAGE",
        )
        for session in sessions:
            key = (student_id, session.id)
            if key in record_lookup:
                value = record_lookup[key]
                existing.append(value)
                worst_values.append(value)
                best_values.append(value)
                encoded_count += 1
            else:
                worst_values.append(worst)
                best_values.append(best)
        return ScenarioScores(
            current=cls._average(existing),
            worst=cls._average(worst_values),
            best=cls._average(best_values),
            encoded_count=encoded_count,
            expected_count=len(sessions),
        )

    @classmethod
    def _weighted_from_children(cls, *, rows: list[tuple[Decimal, ScenarioScores]]) -> ScenarioScores:
        if not rows:
            return ScenarioScores(current=None, worst=None, best=None, encoded_count=0, expected_count=0)
        denominator = sum(weight for weight, _row in rows) or Decimal("100")
        encoded_count = sum(row.encoded_count for _weight, row in rows)
        expected_count = sum(row.expected_count for _weight, row in rows)

        def pick(attribute: str, require_current: bool = False):
            total = Decimal("0")
            has_value = False
            for weight, row in rows:
                value = getattr(row, attribute)
                if value is not None:
                    has_value = True
                    total += (weight / denominator) * value
            if require_current and not has_value:
                return None
            if not require_current and expected_count == 0:
                return None
            return cls._round(total)

        return ScenarioScores(
            current=pick("current", require_current=True),
            worst=pick("worst"),
            best=pick("best"),
            encoded_count=encoded_count,
            expected_count=expected_count,
        )

    @classmethod
    def _average_from_children(cls, *, rows: list[tuple[Decimal, ScenarioScores]]) -> ScenarioScores:
        if not rows:
            return ScenarioScores(current=None, worst=None, best=None, encoded_count=0, expected_count=0)
        encoded_count = sum(row.encoded_count for _weight, row in rows)
        expected_count = sum(row.expected_count for _weight, row in rows)

        def pick(attribute: str, require_current: bool = False):
            values = [getattr(row, attribute) for _weight, row in rows if getattr(row, attribute) is not None]
            if require_current and not values:
                return None
            if not require_current and expected_count == 0:
                return None
            if not values:
                return None
            return cls._round(sum(values) / Decimal(len(values)))

        return ScenarioScores(
            current=pick("current", require_current=True),
            worst=pick("worst"),
            best=pick("best"),
            encoded_count=encoded_count,
            expected_count=expected_count,
        )

    @classmethod
    def _resolve_component_scores(
        cls,
        *,
        student_id: int,
        component,
        grouped_activities: dict,
        score_lookup: dict,
        sessions: list,
        record_lookup: dict,
        base_value: Decimal,
    ) -> ScenarioScores:
        subcomponents = list(component.subcomponents.filter(is_active=True).order_by("sort_order", "id"))
        if not subcomponents:
            key = (component.id, None, None)
            return cls._resolve_activity_group(
                student_id=student_id,
                activities=grouped_activities.get(key, []),
                score_lookup=score_lookup,
                base_value=base_value,
            )

        child_rows = []
        for subcomponent in subcomponents:
            if subcomponent.is_attendance_component:
                row = cls._resolve_attendance_group(
                    student_id=student_id,
                    sessions=sessions,
                    record_lookup=record_lookup,
                    base_value=base_value,
                )
            else:
                details = list(subcomponent.details.filter(is_active=True).order_by("sort_order", "id"))
                if details:
                    detail_rows = []
                    for detail in details:
                        key = (component.id, subcomponent.id, detail.id)
                        detail_rows.append(
                            (
                                Decimal(detail.weight_percentage or 0),
                                cls._resolve_activity_group(
                                    student_id=student_id,
                                    activities=grouped_activities.get(key, []),
                                    score_lookup=score_lookup,
                                    base_value=base_value,
                                ),
                            )
                        )
                    if subcomponent.detail_computation_mode == DetailComputationMode.AVERAGE_ACTIVITIES:
                        detail_activities = []
                        for detail in details:
                            detail_activities.extend(grouped_activities.get((component.id, subcomponent.id, detail.id), []))
                        row = cls._resolve_activity_group(
                            student_id=student_id,
                            activities=detail_activities,
                            score_lookup=score_lookup,
                            base_value=base_value,
                        )
                    else:
                        row = cls._weighted_from_children(rows=detail_rows)
                else:
                    key = (component.id, subcomponent.id, None)
                    row = cls._resolve_activity_group(
                        student_id=student_id,
                        activities=grouped_activities.get(key, []),
                        score_lookup=score_lookup,
                        base_value=base_value,
                    )
            child_rows.append((Decimal(subcomponent.weight_percentage or 0), row))
        return cls._weighted_from_children(rows=child_rows)

    @classmethod
    def _resolve_period_scores(
        cls,
        *,
        student_id: int,
        template_period,
        grouped_activities: dict,
        score_lookup: dict,
        sessions: list,
        record_lookup: dict,
        base_value: Decimal,
    ) -> tuple[ScenarioScores, Decimal | None, Decimal | None]:
        components = list(
            template_period.components.filter(is_active=True)
            .prefetch_related("subcomponents", "subcomponents__details")
            .order_by("sort_order", "id")
        )
        component_rows = []
        current_class_standing = Decimal("0")
        current_exam = None
        for component in components:
            row = cls._resolve_component_scores(
                student_id=student_id,
                component=component,
                grouped_activities=grouped_activities,
                score_lookup=score_lookup,
                sessions=sessions,
                record_lookup=record_lookup,
                base_value=base_value,
            )
            component_rows.append((Decimal(component.weight_percentage or 0), row, component))
            if FacultyGradingService.is_exam_component(component):
                if row.current is not None:
                    current_exam = (current_exam or Decimal("0")) + row.current
            else:
                current_class_standing += row.current or Decimal("0")

        weighted_rows = [(weight, row) for weight, row, _component in component_rows]
        bundle = cls._weighted_from_children(rows=weighted_rows)
        return (
            bundle,
            cls._round(current_class_standing) if components else None,
            cls._round(current_exam) if current_exam is not None else None,
        )

    @classmethod
    def _final_projection(cls, *, offering, template_period, student_id: int, period_grade: Decimal | None):
        template = FacultyGradingService.resolve_template_for_offering(offering)
        period_values = {
            row.template_period_id: Decimal(row.period_grade)
            for row in StudentPeriodGrade.objects.filter(
                offering_id=offering.id,
                student_id=student_id,
                template_period_id__in=template.periods.filter(is_active=True).values_list("id", flat=True),
            )
            if row.period_grade is not None
        }
        if period_grade is not None:
            period_values[template_period.id] = Decimal(period_grade)
        else:
            period_values.pop(template_period.id, None)
        return FacultyGradingService.compute_final_grade_from_period_values(
            offering=offering,
            template=template,
            period_values_by_period_id=period_values,
        )

    @classmethod
    def _target_needed_percent(cls, *, target_grade: Decimal, worst_case: Decimal | None, best_case: Decimal | None):
        if worst_case is None or best_case is None:
            return None
        if target_grade <= worst_case:
            return Decimal("0.00")
        if target_grade > best_case:
            return None
        spread = best_case - worst_case
        if spread <= 0:
            return Decimal("0.00")
        return cls._round(((target_grade - worst_case) / spread) * Decimal("100"))

    @classmethod
    def final_requirement_for_remaining_periods(
        cls,
        *,
        offering,
        template_period,
        student_id: int,
        current_period_grade: Decimal | None,
    ) -> dict:
        template = FacultyGradingService.resolve_template_for_offering(offering)
        ordered_periods = list(template.periods.filter(is_active=True).order_by("sequence_no", "id"))
        period_order_map = {period.id: index for index, period in enumerate(ordered_periods)}
        final_strategy = FacultyGradingService.resolve_final_grade_strategy(offering, template=template)
        strategy_entries = sorted(
            final_strategy["entries"],
            key=lambda entry: period_order_map.get(entry["period_id"], 9999),
        )
        if not strategy_entries:
            return {
                "status": "UNAVAILABLE",
                "label": "Unavailable",
                "required_average": None,
                "remaining_period_names": [],
            }

        passing_threshold = FacultyGradingService.resolve_passing_threshold(offering)
        period_rows = {
            row.template_period_id: Decimal(row.period_grade)
            for row in StudentPeriodGrade.objects.filter(offering=offering, student_id=student_id)
            if row.period_grade is not None
        }

        current_order = period_order_map.get(template_period.id, 9999)
        known_total = Decimal("0")
        remaining_period_names: list[str] = []
        remaining_weight = Decimal("0")

        for entry in strategy_entries:
            period_id = entry["period_id"]
            period_name = entry["period_name"] or entry["period_code"]
            period_weight = Decimal(entry["weight"] or "0")
            entry_order = period_order_map.get(period_id, 9999)

            if period_id == template_period.id:
                if current_period_grade is None:
                    return {
                        "status": "UNAVAILABLE",
                        "label": "Prediction not available yet because there are not enough encoded scores yet.",
                        "required_average": None,
                        "remaining_period_names": [],
                    }
                if final_strategy["mode"] == "WEIGHTED_PERIODS":
                    known_total += Decimal(current_period_grade) * (period_weight / Decimal("100"))
                else:
                    known_total += Decimal(current_period_grade)
                continue

            if entry_order < current_order:
                if period_id not in period_rows:
                    return {
                        "status": "UNAVAILABLE",
                        "label": "Prediction not available yet because an earlier grading period has no recorded grade yet.",
                        "required_average": None,
                        "remaining_period_names": [],
                    }
                if final_strategy["mode"] == "WEIGHTED_PERIODS":
                    known_total += Decimal(period_rows[period_id]) * (period_weight / Decimal("100"))
                else:
                    known_total += Decimal(period_rows[period_id])
            elif entry_order > current_order:
                remaining_period_names.append(period_name)
                if final_strategy["mode"] == "WEIGHTED_PERIODS":
                    remaining_weight += period_weight

        remaining_period_count = len(remaining_period_names)
        if remaining_period_count <= 0 and final_strategy["mode"] != "WEIGHTED_PERIODS":
            return {
                "status": "NO_REMAINING",
                "label": "No remaining future periods to compute",
                "required_average": None,
                "remaining_period_names": [],
            }
        if final_strategy["mode"] == "WEIGHTED_PERIODS" and remaining_weight <= 0:
            return {
                "status": "NO_REMAINING",
                "label": "No remaining future periods to compute",
                "required_average": None,
                "remaining_period_names": [],
            }

        if final_strategy["mode"] == "WEIGHTED_PERIODS":
            target_total = Decimal(passing_threshold)
            required_average = cls._round((target_total - known_total) / (remaining_weight / Decimal("100")))
        else:
            total_period_count = len(strategy_entries)
            required_total = Decimal(passing_threshold) * Decimal(total_period_count)
            required_average = cls._round((required_total - known_total) / Decimal(remaining_period_count))
        if required_average is None:
            return {
                "status": "UNAVAILABLE",
                "label": "Unavailable",
                "required_average": None,
                "remaining_period_names": remaining_period_names,
            }
        if required_average <= 0:
            return {
                "status": "ALREADY_SECURED",
                "label": "The student is already on track for a passing final grade",
                "required_average": Decimal("0.00"),
                "remaining_period_names": remaining_period_names,
            }
        if required_average > Decimal("100.00"):
            period_list = ", ".join(remaining_period_names)
            return {
                "status": "NOT_REACHABLE",
                "label": f"Even 100.00 average across {period_list} is still not enough to reach passing final grade",
                "required_average": required_average,
                "remaining_period_names": remaining_period_names,
            }
        period_list = ", ".join(remaining_period_names)
        return {
            "status": "REQUIRED",
            "label": f"{required_average}% average needed across {period_list}",
            "required_average": required_average,
            "remaining_period_names": remaining_period_names,
        }

    @classmethod
    @transaction.atomic
    def refresh_offering_period(cls, *, offering, template_period, user=None):
        template = FacultyGradingService.resolve_template_for_offering(offering)
        base_value = FacultyGradingService.resolve_base_value(offering, template)
        passing_threshold = FacultyGradingService.resolve_passing_threshold(offering)
        setting_snapshot = cls._setting_snapshot(offering=offering, user=user)
        activities, grouped_activities, score_lookup = cls._load_activity_data(
            offering=offering,
            template_period=template_period,
        )
        sessions, record_lookup = cls._load_attendance_data(
            offering=offering,
            template_period=template_period,
            base_value=base_value,
        )
        assumption_mode = setting_snapshot.assumption_mode
        source_version = cls._source_version(offering=offering, template_period=template_period)
        computed_at = timezone.now()
        active_enrollments = [
            enrollment
            for enrollment in FacultyGradingService.get_active_enrollments(offering)
            if enrollment.enrollment_status not in Enrollment.NON_ACTIVE_GRADING_STATUSES
        ]
        official_period_lookup = {
            row.student_id: row
            for row in StudentPeriodGrade.objects.filter(
                offering=offering,
                template_period=template_period,
            )
        }
        official_final_lookup = {
            row.student_id: Decimal(row.final_grade)
            for row in StudentFinalGrade.objects.filter(offering=offering)
            if row.final_grade is not None
        }

        PredictionSnapshot.objects.filter(
            offering=offering,
            template_period=template_period,
        ).update(is_stale=True)

        created_rows = []
        projected_values = []
        best_values = []
        worst_values = []
        coverage_values = []
        at_risk_count = 0
        passing_count = 0
        failing_count = 0

        for enrollment in active_enrollments:
            student = enrollment.student
            period_scores, class_standing, exam_grade = cls._resolve_period_scores(
                student_id=student.id,
                template_period=template_period,
                grouped_activities=grouped_activities,
                score_lookup=score_lookup,
                sessions=sessions,
                record_lookup=record_lookup,
                base_value=base_value,
            )
            current_projection = {
                PredictionAssumptionMode.IGNORE_MISSING: period_scores.current,
                PredictionAssumptionMode.RAW_ZERO: period_scores.worst,
                PredictionAssumptionMode.FULL_SCORE: period_scores.best,
            }.get(assumption_mode, period_scores.current)
            expected_item_count = len(activities) + len(sessions)
            encoded_item_count = period_scores.encoded_count
            remaining_item_count = max(expected_item_count - encoded_item_count, 0)
            coverage_percent = (
                cls._round((Decimal(encoded_item_count) / Decimal(expected_item_count)) * Decimal("100"))
                if expected_item_count
                else Decimal("0.00")
            )
            official_period_row = official_period_lookup.get(student.id)
            official_final_grade = official_final_lookup.get(student.id)
            if (
                remaining_item_count == 0
                and official_period_row is not None
                and official_period_row.period_grade is not None
            ):
                official_period_grade = Decimal(official_period_row.period_grade)
                current_projection = official_period_grade
                period_scores = ScenarioScores(
                    current=official_period_grade,
                    worst=official_period_grade,
                    best=official_period_grade,
                    encoded_count=encoded_item_count,
                    expected_count=expected_item_count,
                )

            current_projected_final_grade = (
                official_final_grade
                if remaining_item_count == 0 and official_final_grade is not None
                else cls._final_projection(
                    offering=offering,
                    template_period=template_period,
                    student_id=student.id,
                    period_grade=current_projection,
                )
            )
            best_case_final_grade = (
                official_final_grade
                if remaining_item_count == 0 and official_final_grade is not None
                else cls._final_projection(
                    offering=offering,
                    template_period=template_period,
                    student_id=student.id,
                    period_grade=period_scores.best,
                )
            )
            worst_case_final_grade = (
                official_final_grade
                if remaining_item_count == 0 and official_final_grade is not None
                else cls._final_projection(
                    offering=offering,
                    template_period=template_period,
                    student_id=student.id,
                    period_grade=period_scores.worst,
                )
            )
            at_risk_flag = bool(current_projection is not None and current_projection < passing_threshold)
            if current_projected_final_grade is not None and current_projected_final_grade < passing_threshold:
                at_risk_flag = True

            row = PredictionSnapshot.objects.create(
                tenant_id=offering.tenant_id,
                campus_id=offering.campus_id,
                offering=offering,
                template_period=template_period,
                student=student,
                setting_snapshot=setting_snapshot,
                current_projected_period_grade=current_projection,
                best_case_period_grade=period_scores.best,
                worst_case_period_grade=period_scores.worst,
                current_projected_final_grade=current_projected_final_grade,
                best_case_final_grade=best_case_final_grade,
                worst_case_final_grade=worst_case_final_grade,
                target_grade=passing_threshold,
                target_needed_percent=cls._target_needed_percent(
                    target_grade=passing_threshold,
                    worst_case=period_scores.worst,
                    best_case=period_scores.best,
                ),
                at_risk_flag=at_risk_flag,
                encoded_item_count=encoded_item_count,
                expected_item_count=expected_item_count,
                remaining_item_count=remaining_item_count,
                coverage_percent=coverage_percent,
                source_version=source_version,
                is_stale=False,
                computed_at=computed_at,
            )
            created_rows.append(row)
            if current_projection is not None:
                projected_values.append(current_projection)
                if current_projection >= passing_threshold:
                    passing_count += 1
                else:
                    failing_count += 1
            if period_scores.best is not None:
                best_values.append(period_scores.best)
            if period_scores.worst is not None:
                worst_values.append(period_scores.worst)
            coverage_values.append(coverage_percent)
            if row.at_risk_flag:
                at_risk_count += 1

        PredictionSummarySnapshot.objects.filter(
            offering=offering,
            template_period=template_period,
        ).update(is_stale=True)
        summary = PredictionSummarySnapshot.objects.create(
            tenant_id=offering.tenant_id,
            campus_id=offering.campus_id,
            offering=offering,
            template_period=template_period,
            setting_snapshot=setting_snapshot,
            student_count=len(active_enrollments),
            students_with_projection=len(projected_values),
            at_risk_count=at_risk_count,
            passing_count=passing_count,
            failing_count=failing_count,
            avg_projected_grade=cls._average(projected_values),
            avg_best_case_grade=cls._average(best_values),
            avg_worst_case_grade=cls._average(worst_values),
            avg_coverage_percent=cls._average(coverage_values) or Decimal("0.00"),
            source_version=source_version,
            is_stale=False,
            computed_at=computed_at,
        )
        PredictionDirtyQueue.objects.filter(
            offering=offering,
            template_period=template_period,
            status__in=[PredictionDirtyQueue.Status.PENDING, PredictionDirtyQueue.Status.FAILED],
        ).update(
            status=PredictionDirtyQueue.Status.DONE,
            processed_at=computed_at,
            error_message=None,
        )
        return {"setting_snapshot": setting_snapshot, "summary": summary, "rows": created_rows}


class PredictionSnapshotService:
    @classmethod
    def get_period_predictions(cls, *, offering, template_period, user=None, force_refresh: bool = False):
        latest_summary = (
            PredictionSummarySnapshot.objects.filter(
                offering=offering,
                template_period=template_period,
                is_stale=False,
            )
            .select_related("setting_snapshot")
            .order_by("-computed_at")
            .first()
        )
        current_source_version = PredictionComputationService._source_version(
            offering=offering,
            template_period=template_period,
        )
        if force_refresh or latest_summary is None or latest_summary.source_version != current_source_version:
            return PredictionComputationService.refresh_offering_period(
                offering=offering,
                template_period=template_period,
                user=user,
            )
        rows = list(
            PredictionSnapshot.objects.filter(
                offering=offering,
                template_period=template_period,
                setting_snapshot=latest_summary.setting_snapshot,
                is_stale=False,
            ).select_related("student")
        )
        return {"setting_snapshot": latest_summary.setting_snapshot, "summary": latest_summary, "rows": rows}


class PredictionWhatIfService:
    @classmethod
    def simulate(cls, *, snapshot: PredictionSnapshot, assumed_remaining_percent: Decimal):
        percent = max(Decimal("0.00"), min(Decimal("100.00"), Decimal(assumed_remaining_percent)))
        ratio = percent / Decimal("100")
        period_result = snapshot.worst_case_period_grade
        final_result = snapshot.worst_case_final_grade
        if snapshot.worst_case_period_grade is not None and snapshot.best_case_period_grade is not None:
            period_result = PredictionComputationService._round(
                snapshot.worst_case_period_grade
                + ((snapshot.best_case_period_grade - snapshot.worst_case_period_grade) * ratio)
            )
        if snapshot.worst_case_final_grade is not None and snapshot.best_case_final_grade is not None:
            final_result = PredictionComputationService._round(
                snapshot.worst_case_final_grade
                + ((snapshot.best_case_final_grade - snapshot.worst_case_final_grade) * ratio)
            )
        return {
            "assumed_remaining_percent": PredictionComputationService._round(percent),
            "projected_period_grade": period_result,
            "projected_final_grade": final_result,
        }

    @classmethod
    def save_draft(
        cls,
        *,
        user,
        snapshot: PredictionSnapshot,
        scenario_name: str,
        assumed_remaining_percent: Decimal,
        target_grade: Decimal | None = None,
    ):
        results = cls.simulate(snapshot=snapshot, assumed_remaining_percent=assumed_remaining_percent)
        return PredictionWhatIfDraft.objects.create(
            tenant_id=snapshot.tenant_id,
            campus_id=snapshot.campus_id,
            user=user,
            offering=snapshot.offering,
            template_period=snapshot.template_period,
            student=snapshot.student,
            scenario_name=scenario_name,
            assumed_remaining_score=results["assumed_remaining_percent"],
            target_grade=target_grade,
            assumptions_json={
                "assumed_remaining_percent": str(results["assumed_remaining_percent"]),
                "target_grade": str(target_grade) if target_grade is not None else None,
            },
            results_json={
                "projected_period_grade": str(results["projected_period_grade"])
                if results["projected_period_grade"] is not None
                else None,
                "projected_final_grade": str(results["projected_final_grade"])
                if results["projected_final_grade"] is not None
                else None,
            },
        )


class PredictionAuditService:
    @classmethod
    def log_view(cls, *, user, offering, template_period, student=None, view_mode: str):
        return PredictionViewLog.objects.create(
            tenant_id=offering.tenant_id,
            campus_id=offering.campus_id,
            viewer=user,
            viewer_role_code=PredictionAccessService.primary_role_code(user),
            offering=offering,
            template_period=template_period,
            student=student,
            view_mode=view_mode,
        )


class PredictionQueueProcessor:
    @classmethod
    def process_pending(cls, *, limit: int = 25, user=None):
        processed = 0
        failed = 0
        rows = list(
            PredictionDirtyQueue.objects.filter(status=PredictionDirtyQueue.Status.PENDING)
            .select_related("offering", "template_period")
            .order_by("created_at")[:limit]
        )
        for row in rows:
            row.status = PredictionDirtyQueue.Status.PROCESSING
            row.error_message = None
            row.save(update_fields=["status", "error_message", "updated_at"])
            try:
                if row.template_period_id:
                    PredictionComputationService.refresh_offering_period(
                        offering=row.offering,
                        template_period=row.template_period,
                        user=user,
                    )
                else:
                    template = FacultyGradingService.resolve_template_for_offering(row.offering)
                    for period in template.periods.filter(is_active=True).order_by("sequence_no", "id"):
                        PredictionComputationService.refresh_offering_period(
                            offering=row.offering,
                            template_period=period,
                            user=user,
                        )
                row.status = PredictionDirtyQueue.Status.DONE
                row.processed_at = timezone.now()
                row.save(update_fields=["status", "processed_at", "updated_at"])
                processed += 1
            except Exception as exc:
                row.status = PredictionDirtyQueue.Status.FAILED
                row.processed_at = timezone.now()
                row.error_message = str(exc)
                row.save(update_fields=["status", "processed_at", "error_message", "updated_at"])
                failed += 1
        return processed, failed

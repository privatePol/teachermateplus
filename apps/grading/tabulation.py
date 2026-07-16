from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
import re

from apps.enrollment.models import Enrollment
from apps.grading.models import GradeActivity, StudentActivityScore
from apps.grading.services import FacultyGradingService


class DetailedTabulationSheetGridService:
    """Build the canonical detailed tabulation grid for Faculty and Admin copies."""

    @staticmethod
    def _average(values):
        actual_values = [Decimal(value) for value in values if value is not None]
        if not actual_values:
            return None
        return FacultyGradingService._round(sum(actual_values) / Decimal(len(actual_values)))

    @staticmethod
    def _average_label_from_titles(titles, fallback_label="AVE"):
        for title in titles:
            prefix = []
            for char in str(title or ""):
                if char.isalpha():
                    prefix.append(char.upper())
                else:
                    break
            if prefix:
                return f"{''.join(prefix)}.AVE"
        return fallback_label

    @classmethod
    def _average_label_from_section_label(cls, label, fallback_label="AVE"):
        value = str(label or "").strip()
        if not value:
            return fallback_label
        if "/" in value:
            parts = [part for part in re.split(r"/+", value) if part.strip()]
            initials = [match.group(0)[0].upper() for part in parts if (match := re.search(r"[A-Za-z]", part))]
            if initials:
                return f"{'/'.join(initials)} AVE"
        words = re.findall(r"[A-Za-z]+", value)
        if len(words) >= 2:
            return f"{''.join(word[0].upper() for word in words)} AVE"
        return f"{words[0].upper()} AVE" if words else fallback_label

    @staticmethod
    def _activity_sort_key(title):
        value = (title or "").strip().upper()
        match = re.match(r"^([A-Z]+)\s*([0-9]+)?(.*)$", value)
        if not match:
            return (value, 999999, "")
        prefix, number, remainder = match.groups()
        return (prefix, int(number) if number else 999999, remainder.strip())

    @classmethod
    def _build_layout(cls, period, activities):
        components = list(
            period.components.filter(is_active=True)
            .prefetch_related("subcomponents__details")
            .order_by("sort_order", "id")
        )
        activities_by_component = defaultdict(list)
        activities_by_subcomponent = defaultdict(list)
        activities_by_detail = defaultdict(list)
        for activity in activities:
            activities_by_component[activity.template_component_id].append(activity)
            if activity.template_subcomponent_id:
                activities_by_subcomponent[activity.template_subcomponent_id].append(activity)
            if activity.template_detail_id:
                activities_by_detail[activity.template_detail_id].append(activity)

        class_standing_blocks = []
        exam_components = []
        for component in components:
            component_is_exam = FacultyGradingService.is_exam_component(component)
            subcomponents = [sub for sub in component.subcomponents.all() if sub.is_active]
            component_layout = {
                "component_code": component.code,
                "sections": [],
                "total_label": "CS AVE" if not component_is_exam else "AVE",
            }
            if subcomponents:
                for subcomponent in subcomponents:
                    sub_activities = activities_by_subcomponent.get(subcomponent.id, [])
                    detail_groups = []
                    for detail in [detail for detail in subcomponent.details.all() if detail.is_active]:
                        detail_activities = sorted(
                            activities_by_detail.get(detail.id, []),
                            key=lambda activity: (
                                cls._activity_sort_key(activity.title),
                                activity.activity_date or "",
                                activity.id,
                            ),
                        )
                        detail_groups.append(
                            {
                                "activity_ids": [activity.id for activity in detail_activities],
                                "activity_columns": [
                                    {"id": activity.id, "label": activity.title, "total_score": activity.total_score}
                                    for activity in detail_activities
                                ],
                                "avg_label": cls._average_label_from_titles(
                                    [activity.title for activity in detail_activities]
                                ),
                                "weight_percentage": Decimal(detail.weight_percentage or 0),
                            }
                        )
                    visible_groups = detail_groups
                    if subcomponent.detail_computation_mode == "AVERAGE_ACTIVITIES":
                        visible_groups = [group for group in detail_groups if group["activity_columns"]]
                    if visible_groups:
                        titles = [column["label"] for group in visible_groups for column in group["activity_columns"]]
                        component_layout["sections"].append(
                            {
                                "uses_nested": True,
                                "groups": visible_groups,
                                "avg_label": cls._average_label_from_section_label(
                                    subcomponent.name or subcomponent.code,
                                    fallback_label=cls._average_label_from_titles(titles),
                                ),
                                "weight_percentage": Decimal(subcomponent.weight_percentage or 0),
                                "detail_computation_mode": subcomponent.detail_computation_mode,
                            }
                        )
                    else:
                        ordered = sorted(
                            sub_activities,
                            key=lambda activity: (
                                cls._activity_sort_key(activity.title),
                                activity.activity_date or "",
                                activity.id,
                            ),
                        )
                        component_layout["sections"].append(
                            {
                                "uses_nested": False,
                                "activity_ids": [activity.id for activity in ordered],
                                "activity_columns": [
                                    {"id": activity.id, "label": activity.title, "total_score": activity.total_score}
                                    for activity in ordered
                                ],
                                "avg_label": cls._average_label_from_titles([activity.title for activity in ordered]),
                                "weight_percentage": Decimal(subcomponent.weight_percentage or 0),
                            }
                        )
            else:
                direct = sorted(
                    activities_by_component.get(component.id, []),
                    key=lambda activity: (
                        cls._activity_sort_key(activity.title),
                        activity.activity_date or "",
                        activity.id,
                    ),
                )
                component_layout["sections"].append(
                    {
                        "uses_nested": False,
                        "activity_ids": [activity.id for activity in direct],
                        "activity_columns": [
                            {"id": activity.id, "label": activity.title, "total_score": activity.total_score}
                            for activity in direct
                        ],
                        "avg_label": cls._average_label_from_titles([activity.title for activity in direct]),
                        "weight_percentage": Decimal("100"),
                    }
                )
            if component_is_exam:
                exam_components.append(component_layout)
            else:
                class_standing_blocks.append(component_layout)
        return {"class_standing_blocks": class_standing_blocks, "exam_components": exam_components}

    @classmethod
    def _build_row_values(cls, student_id, layout, numeric_score_map):
        class_standing_blocks = []
        exam_values = []
        for block in layout["class_standing_blocks"]:
            block_values = {"sections": [], "total": None}
            component_numeric = Decimal("0")
            component_has_data = False
            section_weight_total = sum(section["weight_percentage"] for section in block["sections"]) or Decimal("100")
            for section in block["sections"]:
                if section["uses_nested"]:
                    section_values = {"uses_nested": True, "groups": []}
                    nested_numeric = Decimal("0")
                    nested_has_data = False
                    nested_weight_total = sum(group["weight_percentage"] for group in section["groups"]) or Decimal("100")
                    section_activity_values = []
                    for group in section["groups"]:
                        values = [numeric_score_map.get((student_id, activity_id)) for activity_id in group["activity_ids"]]
                        section_activity_values.extend(values)
                        average = cls._average(values)
                        nested_has_data = nested_has_data or average is not None
                        if section.get("detail_computation_mode") != "AVERAGE_ACTIVITIES":
                            nested_numeric += (group["weight_percentage"] / nested_weight_total) * (average or Decimal("0"))
                        section_values["groups"].append({"average": average})
                    if section.get("detail_computation_mode") == "AVERAGE_ACTIVITIES":
                        section_score = cls._average(section_activity_values) or Decimal("0")
                    else:
                        section_score = FacultyGradingService._round(nested_numeric)
                    component_numeric += (section["weight_percentage"] / section_weight_total) * section_score
                    component_has_data = component_has_data or nested_has_data
                    section_values["average"] = section_score if nested_has_data else None
                    block_values["sections"].append(section_values)
                else:
                    values = [numeric_score_map.get((student_id, activity_id)) for activity_id in section["activity_ids"]]
                    average = cls._average(values)
                    component_has_data = component_has_data or average is not None
                    component_numeric += (section["weight_percentage"] / section_weight_total) * (average or Decimal("0"))
                    block_values["sections"].append({"uses_nested": False, "average": average})
            if component_has_data:
                block_values["total"] = FacultyGradingService._round(component_numeric)
            class_standing_blocks.append(block_values)

        for component in layout["exam_components"]:
            section_scores = []
            for section in component["sections"]:
                if section["uses_nested"]:
                    nested_weight_total = sum(group["weight_percentage"] for group in section["groups"]) or Decimal("100")
                    nested_numeric = Decimal("0")
                    nested_has_data = False
                    section_activity_values = []
                    for group in section["groups"]:
                        values = [numeric_score_map.get((student_id, activity_id)) for activity_id in group["activity_ids"]]
                        section_activity_values.extend(values)
                        average = cls._average(values)
                        nested_has_data = nested_has_data or average is not None
                        if section.get("detail_computation_mode") != "AVERAGE_ACTIVITIES":
                            nested_numeric += (group["weight_percentage"] / nested_weight_total) * (average or Decimal("0"))
                    if nested_has_data:
                        section_scores.append(
                            cls._average(section_activity_values)
                            if section.get("detail_computation_mode") == "AVERAGE_ACTIVITIES"
                            else FacultyGradingService._round(nested_numeric)
                        )
                else:
                    average = cls._average(
                        [numeric_score_map.get((student_id, activity_id)) for activity_id in section["activity_ids"]]
                    )
                    if average is not None:
                        section_scores.append(average)
            exam_values.append(section_scores[0] if len(section_scores) == 1 else cls._average(section_scores))
        return {"class_standing_blocks": class_standing_blocks, "exam_values": exam_values}

    @staticmethod
    def _display_decimal(value):
        if value in (None, ""):
            return ""
        try:
            decimal_value = Decimal(str(value)).quantize(Decimal("0.01"))
        except (InvalidOperation, TypeError, ValueError):
            return str(value)
        return format(decimal_value, "f")

    @classmethod
    def _score_display(cls, score):
        if score is None:
            return "MISSING"
        if score.is_excused:
            return "EXEMPT"
        return cls._display_decimal(score.computed_score)

    @staticmethod
    def _period_label(period):
        name = (period.name or period.code or "").strip().upper()
        code = (period.code or "").strip().upper()
        if name in {"FINAL", "FINALS", "FX"} or code in {"FINAL", "FINALS", "FX"}:
            return "FINAL EXAM"
        return name

    @classmethod
    def _official_grade_display(cls, value):
        if value in (None, ""):
            return "MISSING"
        return format(FacultyGradingService._round_official_grade(Decimal(str(value))), "f")

    @classmethod
    def _columns_and_highest(cls, layout, period):
        columns = []
        highest = []
        for block in layout["class_standing_blocks"]:
            for section in block["sections"]:
                if section["uses_nested"]:
                    for group in section["groups"]:
                        for column in group["activity_columns"]:
                            columns.append({"label": column["label"], "kind": "activity", "activity_id": column["id"]})
                            highest.append(cls._display_decimal(column["total_score"]))
                        columns.append({"label": group["avg_label"], "kind": "average"})
                        highest.append("")
                else:
                    for column in section["activity_columns"]:
                        columns.append({"label": column["label"], "kind": "activity", "activity_id": column["id"]})
                        highest.append(cls._display_decimal(column["total_score"]))
                    columns.append({"label": section["avg_label"], "kind": "average"})
                    highest.append("")
            columns.append({"label": block["total_label"], "kind": "component_average"})
            highest.append("")
        for exam in layout["exam_components"]:
            columns.append({"label": exam["sections"][0]["activity_columns"][0]["label"] if exam["sections"] and exam["sections"][0].get("activity_columns") else "EXAM", "kind": "exam"})
            first_activity = exam["sections"][0]["activity_columns"][0] if exam["sections"] and exam["sections"][0].get("activity_columns") else None
            highest.append(cls._display_decimal(first_activity["total_score"]) if first_activity else "")
        columns.append({"label": f"{cls._period_label(period)} Grade", "kind": "period_grade"})
        highest.append("")
        return columns, highest

    @classmethod
    def _student_period_values(cls, *, layout, row_values, score_map, student_id, period_grade):
        values = []
        for block_index, block in enumerate(layout["class_standing_blocks"]):
            block_values = row_values["class_standing_blocks"][block_index]
            for section_index, section in enumerate(block["sections"]):
                section_values = block_values["sections"][section_index]
                if section["uses_nested"]:
                    for group_index, group in enumerate(section["groups"]):
                        values.extend(cls._score_display(score_map.get((student_id, activity_id))) for activity_id in group["activity_ids"])
                        values.append(cls._display_decimal(section_values["groups"][group_index]["average"]))
                else:
                    values.extend(cls._score_display(score_map.get((student_id, activity_id))) for activity_id in section["activity_ids"])
                    values.append(cls._display_decimal(section_values["average"]))
            values.append(cls._display_decimal(block_values["total"]))
        values.extend(cls._display_decimal(value) if value is not None else "MISSING" for value in row_values["exam_values"])
        values.append(cls._official_grade_display(period_grade))
        return values

    @classmethod
    def build(cls, *, offering, periods, enrollments, stored_grade_map, final_grade_map):
        period_groups = []
        values_by_student = defaultdict(list)
        flat_highest = []
        value_offset = 0
        for period in periods:
            activities = list(
                GradeActivity.objects.filter(offering=offering, template_period=period, is_active=True)
                .select_related("template_component", "template_subcomponent", "template_detail")
                .order_by(
                    "template_component__sort_order",
                    "template_subcomponent__sort_order",
                    "template_detail__sort_order",
                    "activity_date",
                    "id",
                )
            )
            score_rows = list(
                StudentActivityScore.objects.filter(
                    activity_id__in=[activity.id for activity in activities],
                    is_active=True,
                    activity__is_active=True,
                )
            )
            score_map = {(score.student_id, score.activity_id): score for score in score_rows}
            numeric_score_map = {
                key: Decimal(score.computed_score)
                for key, score in score_map.items()
                if not score.is_excused
            }
            layout = cls._build_layout(period, activities)
            columns, highest = cls._columns_and_highest(layout, period)
            group = {
                "period": period,
                "label": f"{cls._period_label(period)} ({period.code})",
                "columns": columns,
                "highest_values": highest,
                "value_offset": value_offset,
            }
            period_groups.append(group)
            flat_highest.extend(highest)
            value_offset += len(columns)
            for enrollment in enrollments:
                row_values = cls._build_row_values(enrollment.student_id, layout, numeric_score_map)
                grade_row = stored_grade_map.get(period.id, {}).get(enrollment.student_id)
                values_by_student[enrollment.student_id].extend(
                    cls._student_period_values(
                        layout=layout,
                        row_values=row_values,
                        score_map=score_map,
                        student_id=enrollment.student_id,
                        period_grade=grade_row.period_grade if grade_row else None,
                    )
                )

        sheet_rows = []
        for number, enrollment in enumerate(enrollments, start=1):
            status = "" if enrollment.enrollment_status == Enrollment.Status.ACTIVE else enrollment.get_enrollment_status_display()
            sheet_rows.append(
                {
                    "number": number,
                    "student_id": enrollment.student_id,
                    "student_no": enrollment.student.student_no,
                    "student_name": f"{enrollment.student.last_name}, {enrollment.student.first_name}",
                    "status": status,
                    "values": values_by_student.get(enrollment.student_id, []),
                    "final_grade": cls._official_grade_display(final_grade_map.get(enrollment.student_id)),
                }
            )
        return {
            "period_column_groups": period_groups,
            "highest_row": flat_highest,
            "sheet_rows": sheet_rows,
        }

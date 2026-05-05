from __future__ import annotations

from decimal import Decimal

from apps.enrollment.models import Enrollment
from apps.grading.models import (
    GradeCorrectionRequest,
    GradeCorrectionRequestItem,
    GradeSubmission,
    StudentFinalGrade,
    StudentPeriodGrade,
)
from apps.grading.services import FacultyGradingService, GradingGovernanceService


class GradeExplanationService:
    GRADE_TYPE_PERIOD = "PERIOD"
    GRADE_TYPE_FINAL = "FINAL"

    @staticmethod
    def _display_student_no(student, *, masked: bool):
        if not masked:
            return student.student_no
        raw = student.student_no or ""
        visible = raw[-3:] if len(raw) > 3 else raw
        return f"{'*' * max(len(raw) - len(visible), 3)}{visible}"

    @staticmethod
    def _display_student_name(student, *, masked: bool):
        if masked:
            return "Masked Student"
        return f"{student.last_name}, {student.first_name}".strip(", ")

    @staticmethod
    def _profile_payload(profile):
        if not profile:
            return {
                "id": None,
                "code": None,
                "name": "No grading profile matched",
                "matched": False,
            }
        return {
            "id": profile.id,
            "code": profile.profile_code,
            "name": profile.profile_name,
            "matched": True,
            "formula_mode": profile.final_grade_formula_mode,
        }

    @staticmethod
    def _template_payload(template, trace):
        return {
            "id": template.id,
            "code": template.code,
            "name": template.name,
            "source": trace.get("source"),
            "source_label": trace.get("source_label"),
            "fallback": bool(trace.get("fallback")),
        }

    @staticmethod
    def _course_label(offering):
        return f"{offering.course.code} - {offering.course.title}"

    @staticmethod
    def _section_label(offering):
        return getattr(offering.section, "name", None) or offering.section.code

    @classmethod
    def _correction_history(cls, *, offering, student, template_period=None):
        request_qs = GradeCorrectionRequest.objects.filter(
            offering=offering,
            status__in=[
                GradeCorrectionRequest.Status.APPROVED,
                GradeCorrectionRequest.Status.CLOSED,
            ],
            items__student=student,
            items__is_active=True,
        )
        if template_period is not None:
            request_qs = request_qs.filter(template_period=template_period)
        request_ids = list(request_qs.values_list("id", flat=True).distinct())
        if not request_ids:
            return []
        items = (
            GradeCorrectionRequestItem.objects.filter(
                correction_request_id__in=request_ids,
                student=student,
                is_active=True,
            )
            .select_related(
                "correction_request",
                "correction_request__reviewed_by_user",
                "grade_activity",
            )
            .order_by("-correction_request__reviewed_at", "-id")
        )
        history = []
        for item in items:
            request_obj = item.correction_request
            reviewer = request_obj.reviewed_by_user
            reviewer_name = ""
            if reviewer:
                reviewer_name = (
                    getattr(reviewer, "full_name", "")
                    or (reviewer.get_full_name() if hasattr(reviewer, "get_full_name") else "")
                    or reviewer.username
                )
            history.append(
                {
                    "request_id": request_obj.id,
                    "period": request_obj.template_period.name,
                    "action": item.get_requested_action_display(),
                    "item": item.grade_activity.title if item.grade_activity else item.get_requested_action_display(),
                    "old_value": item.old_value,
                    "new_value": item.new_value,
                    "approved_by": reviewer_name,
                    "approved_at": request_obj.reviewed_at,
                }
            )
        return history

    @staticmethod
    def _submission_note(*, offering, template_period):
        submission = GradingGovernanceService.get_submission(offering=offering, template_period=template_period)
        if not submission:
            return "Period not submitted yet."
        if submission.status == GradeSubmission.Status.REOPENED:
            return "Period was reopened."
        if submission.status == GradeSubmission.Status.SUBMITTED:
            return "Period submitted."
        return f"Period status: {submission.get_status_display()}."

    @classmethod
    def build(
        cls,
        *,
        offering,
        student,
        grade_type: str,
        template_period=None,
        mask_identity: bool = False,
        include_correction_history: bool = True,
    ):
        grade_type = (grade_type or "").upper()
        template_trace = FacultyGradingService.resolve_template_for_offering_trace(offering)
        template = template_trace["template"]
        profile = FacultyGradingService.resolve_grading_profile_for_offering(offering)
        base_trace = FacultyGradingService.resolve_base_value_trace(offering, template)
        threshold_trace = FacultyGradingService.resolve_passing_threshold_trace(offering)
        final_strategy = FacultyGradingService.resolve_final_grade_strategy(offering, template=template)
        warnings = []
        if template_trace.get("fallback"):
            warnings.append("A fallback grading template was used.")
        if base_trace.get("fallback"):
            warnings.append("A fallback base value was used.")
        if threshold_trace.get("fallback"):
            warnings.append("A fallback passing threshold was used.")

        enrollment = Enrollment.objects.filter(
            course_offering=offering,
            student=student,
            is_active=True,
        ).first()
        if enrollment and enrollment.enrollment_status in Enrollment.NON_ACTIVE_GRADING_STATUSES:
            warnings.append(f"Student status is {enrollment.enrollment_status}; official grade may be blank.")

        payload = {
            "grade_type": grade_type,
            "student": {
                "id": student.id,
                "student_no": cls._display_student_no(student, masked=mask_identity),
                "name": cls._display_student_name(student, masked=mask_identity),
                "masked": mask_identity,
            },
            "offering": {
                "id": offering.id,
                "course": cls._course_label(offering),
                "section": cls._section_label(offering),
                "term": f"{offering.academic_year.code} / {offering.term.code}",
                "campus": offering.campus.code,
            },
            "template": cls._template_payload(template, template_trace),
            "profile": cls._profile_payload(profile),
            "passing_threshold": threshold_trace,
            "base_value": base_trace,
            "final_formula": final_strategy,
            "rounding_policy": {
                "label": "Official grades are rounded to whole numbers using ROUND_HALF_UP.",
                "mode": "ROUND_HALF_UP",
            },
            "warnings": warnings,
            "correction_history": [],
        }

        if grade_type == cls.GRADE_TYPE_FINAL:
            period_rows = {
                row.template_period_id: Decimal(row.period_grade)
                for row in StudentPeriodGrade.objects.filter(offering=offering, student=student)
                if row.period_grade is not None
            }
            final_detail = FacultyGradingService.compute_final_grade_detail_from_period_values(
                offering=offering,
                template=template,
                period_values_by_period_id=period_rows,
            )
            final_row = StudentFinalGrade.objects.filter(offering=offering, student=student).first()
            official_value = final_row.final_grade if final_row else None
            payload.update(
                {
                    "period": None,
                    "official_value": official_value,
                    "raw_value": final_detail["raw_value"],
                    "computed_official_value": final_detail["official_value"],
                    "pass_fail": cls._pass_fail(official_value, threshold_trace["value"]),
                    "period_breakdown": final_detail["entries"],
                    "formula_text": final_strategy.get("formula_label"),
                    "component_breakdown": [],
                }
            )
            payload["warnings"].extend(final_detail.get("warnings", []))
            if final_row is None:
                payload["warnings"].append("No stored final-grade row is available yet.")
            elif final_detail["official_value"] != final_row.final_grade:
                payload["warnings"].append("Stored final grade differs from the current diagnostic recomputation path.")
            if include_correction_history:
                payload["correction_history"] = cls._correction_history(offering=offering, student=student)
            return payload

        if template_period is None:
            raise ValueError("template_period is required for period-grade explanation.")
        period_detail = FacultyGradingService.build_period_grade_detail_for_student(
            offering=offering,
            template_period=template_period,
            student_id=student.id,
            template=template,
            base_value=base_trace["value"],
            include_details=True,
        )
        period_row = StudentPeriodGrade.objects.filter(
            offering=offering,
            template_period=template_period,
            student=student,
        ).first()
        official_value = period_row.period_grade if period_row else None
        payload.update(
            {
                "period": {
                    "id": template_period.id,
                    "code": template_period.code,
                    "name": template_period.name,
                },
                "official_value": official_value,
                "raw_value": period_detail["period_grade_raw"],
                "computed_official_value": period_detail["period_grade"],
                "class_standing": period_row.class_standing_grade if period_row else None,
                "exam_grade": period_row.exam_grade if period_row else None,
                "class_standing_raw": period_detail["class_standing_raw"],
                "exam_grade_raw": period_detail["exam_grade_raw"],
                "pass_fail": cls._pass_fail(official_value, threshold_trace["value"]),
                "period_breakdown": [],
                "component_breakdown": period_detail["component_breakdown"],
                "formula_text": (
                    "Period Grade = "
                    + " + ".join(
                        f"{component['code']} x {Decimal(component['weight']):.2f}%"
                        for component in period_detail["component_breakdown"]
                    )
                ),
            }
        )
        payload["warnings"].extend(period_detail.get("warnings", []))
        payload["warnings"].append(cls._submission_note(offering=offering, template_period=template_period))
        if period_row is None:
            payload["warnings"].append("No stored period-grade row is available yet.")
        elif period_detail["period_grade"] != period_row.period_grade:
            payload["warnings"].append("Stored period grade differs from the current diagnostic recomputation path.")
        if include_correction_history:
            payload["correction_history"] = cls._correction_history(
                offering=offering,
                student=student,
                template_period=template_period,
            )
            if payload["correction_history"]:
                payload["warnings"].append("Approved correction history exists for this student and period.")
        return payload

    @staticmethod
    def _pass_fail(value, threshold):
        if value is None:
            return ""
        return "PASSED" if Decimal(value) >= Decimal(threshold) else "FAILED"

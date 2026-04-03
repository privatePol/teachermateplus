from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.core.services.settings import SystemSettingService
from apps.enrollment.models import Enrollment
from apps.grading.models import GradeActivity, StudentActivityScore, StudentPeriodGrade
from apps.grading.services import FacultyGradingService


class CorrectionOfficialReportService:
    LOGO_PATH = Path(__file__).resolve().parents[2] / "media" / "logos" / "ncba-logo.png"

    @staticmethod
    def _to_decimal(value):
        if value in (None, ""):
            return None
        try:
            return Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return None

    @classmethod
    def _format_decimal(cls, value):
        if value in (None, ""):
            return ""
        decimal_value = cls._to_decimal(value)
        if decimal_value is None:
            return str(value)
        formatted = format(decimal_value.quantize(Decimal("0.01")), "f")
        if "." in formatted:
            formatted = formatted.rstrip("0").rstrip(".")
        return formatted

    @staticmethod
    def _safe_text(value):
        return str(value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    @classmethod
    def _score_override_map(cls, *, request_obj):
        overrides = {}
        for item in request_obj.items.filter(is_active=True, requested_action="UPDATE_SCORE"):
            if not item.student_id or not item.grade_activity_id:
                continue
            overrides[(item.student_id, item.grade_activity_id)] = cls._to_decimal(item.old_value)
        return overrides

    @classmethod
    def _build_before_score_lookup(cls, *, offering, template_period, override_map):
        template = FacultyGradingService.resolve_template_for_offering(offering)
        base_value = FacultyGradingService.resolve_base_value(offering, template)
        activities = {
            row.id: row
            for row in GradeActivity.objects.filter(
                offering_id=offering.id,
                template_period_id=template_period.id,
                is_active=True,
            ).select_related("template_component", "template_subcomponent", "template_detail")
        }
        activity_scores = StudentActivityScore.objects.filter(
            activity_id__in=activities.keys(),
            is_active=True,
            activity__is_active=True,
        ).select_related("activity")

        score_lookup = defaultdict(list)
        for score in activity_scores:
            pair_key = (score.student_id, score.activity_id)
            override_raw = override_map.get(pair_key, "__current__")
            if override_raw is None:
                continue
            if override_raw == "__current__":
                computed = Decimal(score.computed_score or 0)
            else:
                activity = activities.get(score.activity_id)
                if activity is None:
                    continue
                computed = FacultyGradingService.compute_activity_score(
                    raw_score=Decimal(override_raw),
                    total_score=Decimal(activity.total_score),
                    base_value=base_value,
                    score_input_mode=FacultyGradingService.resolve_score_input_mode(
                        template_component=activity.template_component,
                        template_subcomponent=activity.template_subcomponent,
                        template_detail=activity.template_detail,
                    ),
                )
            key = (
                score.student_id,
                score.activity.template_component_id,
                score.activity.template_subcomponent_id,
                score.activity.template_detail_id,
            )
            score_lookup[key].append(computed)
        return score_lookup

    @classmethod
    def _compute_period_metrics(cls, *, offering, template_period, score_lookup, affected_student_ids):
        template = FacultyGradingService.resolve_template_for_offering(offering)
        base_value = FacultyGradingService.resolve_base_value(offering, template)
        enrollments = [
            row
            for row in FacultyGradingService.get_active_enrollments(offering)
            if row.student_id in affected_student_ids
        ]
        components = list(
            template_period.components.filter(is_active=True)
            .prefetch_related("subcomponents", "subcomponents__details")
            .order_by("sort_order", "id")
        )

        results = {}
        for enrollment in enrollments:
            student_id = enrollment.student_id
            if enrollment.enrollment_status in {Enrollment.Status.DR, Enrollment.Status.W}:
                results[student_id] = {
                    "class_standing_grade": None,
                    "exam_grade": None,
                    "period_grade": None,
                    "enrollment_status": enrollment.enrollment_status,
                }
                continue

            class_standing = Decimal("0")
            exam_grade = None
            weighted_period_grade = Decimal("0")
            has_exam_component = False
            has_exam_data = False

            for component in components:
                subcomponents = list(component.subcomponents.filter(is_active=True).order_by("sort_order", "id"))
                component_has_data = False
                if subcomponents:
                    sub_total = sum(Decimal(sub.weight_percentage or 0) for sub in subcomponents)
                    sub_denominator = sub_total if sub_total > 0 else Decimal("100")
                    component_raw = Decimal("0")
                    for sub in subcomponents:
                        detail_rows = list(sub.details.filter(is_active=True).order_by("sort_order", "id"))
                        if sub.is_attendance_component:
                            sub_score = FacultyGradingService._attendance_subcomponent_score(
                                offering=offering,
                                template_period=template_period,
                                student_id=student_id,
                                base_value=base_value,
                            )
                        elif detail_rows:
                            detail_total = sum(Decimal(detail.weight_percentage or 0) for detail in detail_rows)
                            detail_denominator = detail_total if detail_total > 0 else Decimal("100")
                            detail_raw = Decimal("0")
                            detail_has_data = False
                            for detail in detail_rows:
                                detail_score = FacultyGradingService._average_score_or_none(
                                    score_lookup,
                                    (student_id, component.id, sub.id, detail.id),
                                )
                                if detail_score is not None:
                                    detail_has_data = True
                                    detail_raw += (Decimal(detail.weight_percentage) / detail_denominator) * detail_score
                            sub_score = FacultyGradingService._round(detail_raw)
                            if detail_has_data:
                                component_has_data = True
                        else:
                            sub_score = FacultyGradingService._average_score_or_none(
                                score_lookup,
                                (student_id, component.id, sub.id, None),
                            )
                            if sub_score is not None:
                                component_has_data = True
                        if sub_score is not None:
                            component_raw += (Decimal(sub.weight_percentage) / sub_denominator) * sub_score
                    component_score = FacultyGradingService._round(component_raw)
                else:
                    component_score = FacultyGradingService._average_score_or_none(
                        score_lookup,
                        (student_id, component.id, None, None),
                    )
                    component_has_data = component_score is not None

                if "EXAM" in component.code.upper():
                    has_exam_component = True
                    if component_has_data:
                        exam_grade = (exam_grade or Decimal("0")) + component_score
                        has_exam_data = True
                else:
                    class_standing += component_score or Decimal("0")
                weighted_period_grade += (Decimal(component.weight_percentage) / Decimal("100")) * (
                    component_score or Decimal("0")
                )

            class_standing = FacultyGradingService._round(class_standing)
            exam_grade = FacultyGradingService._round(exam_grade) if exam_grade is not None else None
            period_grade = None
            if not has_exam_component or has_exam_data:
                period_grade = FacultyGradingService._round(weighted_period_grade)

            results[student_id] = {
                "class_standing_grade": class_standing,
                "exam_grade": exam_grade,
                "period_grade": period_grade,
                "enrollment_status": enrollment.enrollment_status,
            }
        return results

    @classmethod
    def _official_grade_label(cls, *, request_obj):
        period_label = " ".join(
            (
                str(request_obj.template_period.name or "").strip(),
                str(request_obj.template_period.code or "").strip(),
            )
        ).strip().upper()
        if "PRE-FINAL" in period_label or "PREFINAL" in period_label or "PRE FINAL" in period_label:
            return "PRE-FINAL CS"
        if "PRELIM" in period_label:
            return "PG"
        if "MIDTERM" in period_label:
            return "MG"
        if "FINAL" in period_label:
            return "FINAL GRADE"
        period_name = (request_obj.template_period.name or request_obj.template_period.code or "Period").strip().upper()
        if "GRADE" in period_name:
            return period_name
        return f"{period_name} GRADE"

    @classmethod
    def _build_official_grade_rows(cls, *, request_obj):
        affected_student_ids = sorted(
            {
                item.student_id
                for item in request_obj.items.filter(is_active=True, student__isnull=False)
                if item.student_id
            }
        )
        if not affected_student_ids:
            return []

        before_score_lookup = cls._build_before_score_lookup(
            offering=request_obj.offering,
            template_period=request_obj.template_period,
            override_map=cls._score_override_map(request_obj=request_obj),
        )
        before_period_map = cls._compute_period_metrics(
            offering=request_obj.offering,
            template_period=request_obj.template_period,
            score_lookup=before_score_lookup,
            affected_student_ids=affected_student_ids,
        )
        after_period_map = {
            row.student_id: row
            for row in StudentPeriodGrade.objects.filter(
                offering_id=request_obj.offering_id,
                template_period_id=request_obj.template_period_id,
                student_id__in=affected_student_ids,
            ).select_related("student")
        }
        summary_rows = []
        for student_id in affected_student_ids:
            after_period = after_period_map.get(student_id)
            if after_period is None:
                continue
            student = after_period.student
            before_period = before_period_map.get(student_id, {})
            before_value = before_period.get("period_grade")
            after_value = after_period.period_grade
            if before_value is None and after_value is None:
                continue
            summary_rows.append(
                [
                    student.student_no,
                    f"{student.last_name}, {student.first_name}",
                    cls._format_decimal(before_value),
                    cls._format_decimal(after_value),
                ]
            )
        return summary_rows

    @classmethod
    def build_report_data(cls, *, request_obj):
        offering = request_obj.offering
        print_header_name = SystemSettingService.get(
            "PRINT_HEADER_SCHOOL_NAME",
            tenant_id=offering.tenant_id,
            default=offering.tenant.name,
        )
        print_header_address = SystemSettingService.get(
            "PRINT_HEADER_SCHOOL_ADDRESS",
            tenant_id=offering.tenant_id,
            default=getattr(offering.campus, "address", "") or "",
        )

        approval_rows = []
        for step in request_obj.approval_steps.select_related("approver_role", "reviewed_by_user").all():
            reviewer_name = (
                step.reviewed_by_user.full_name if step.reviewed_by_user_id else "-"
            )
            approval_rows.append(
                [
                    str(step.step_order),
                    step.approver_label,
                    step.status,
                    reviewer_name,
                    timezone.localtime(step.reviewed_at).strftime("%Y-%m-%d %H:%M") if step.reviewed_at else "-",
                ]
            )

        return {
            "print_header_name": print_header_name,
            "print_header_address": print_header_address,
            "generated_at": timezone.localtime(),
            "reference_no": f"CGR-{request_obj.id:06d}",
            "request_obj": request_obj,
            "approval_rows": approval_rows,
            "official_grade_label": cls._official_grade_label(request_obj=request_obj),
            "official_grade_rows": cls._build_official_grade_rows(request_obj=request_obj),
        }

    @classmethod
    def _table(cls, rows, col_widths=None, repeat_rows=1):
        table = Table(rows, colWidths=col_widths, repeatRows=repeat_rows)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9f2ff")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#102a43")),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        return table

    @classmethod
    def build_pdf_bytes(cls, *, request_obj):
        report = cls.build_report_data(request_obj=request_obj)
        request_obj = report["request_obj"]

        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=15 * mm,
            rightMargin=15 * mm,
            topMargin=15 * mm,
            bottomMargin=15 * mm,
            title=f"Petition for Correction of Grades {report['reference_no']}",
        )
        styles = getSampleStyleSheet()
        styles.add(ParagraphStyle(name="SmallBody", fontSize=8, leading=10))
        styles.add(
            ParagraphStyle(
                name="SectionTitle",
                fontSize=11,
                leading=13,
                spaceBefore=2,
                spaceAfter=8,
                textColor=colors.HexColor("#0f172a"),
                alignment=0,
            )
        )
        styles.add(ParagraphStyle(name="ReportHeader", fontSize=14, leading=17, alignment=1, textColor=colors.HexColor("#0f172a")))
        styles.add(ParagraphStyle(name="CenteredBody", parent=styles["BodyText"], alignment=1))

        story = []
        if cls.LOGO_PATH.exists():
            logo = Image(str(cls.LOGO_PATH), width=22 * mm, height=22 * mm)
            logo.hAlign = "CENTER"
            story.extend([logo, Spacer(1, 6)])
        story.append(Paragraph(cls._safe_text(report["print_header_name"]), styles["ReportHeader"]))
        if report["print_header_address"]:
            story.append(Paragraph(cls._safe_text(report["print_header_address"]), styles["CenteredBody"]))
        story.extend(
            [
                Spacer(1, 4),
                Paragraph("Petition for Correction of Grades", styles["Title"]),
                Paragraph(
                    f"Generated: {report['generated_at'].strftime('%Y-%m-%d %H:%M')} | Reference No: {report['reference_no']}",
                    styles["SmallBody"],
                ),
                Spacer(1, 12),
                Paragraph("A. ACADEMIC CONTEXT", styles["SectionTitle"]),
            ]
        )

        metadata_rows = [
            ["Campus / Branch", request_obj.campus.name, "Academic Year", request_obj.offering.academic_year.code],
            ["Term", request_obj.offering.term.name or request_obj.offering.term.code, "Course", request_obj.offering.course.title or request_obj.offering.course.code],
            ["Section", request_obj.offering.section.name or request_obj.offering.section.code, "Faculty", request_obj.requested_by_user.full_name],
            ["Period", request_obj.template_period.name or request_obj.template_period.code, "Status", request_obj.status],
        ]
        story.append(cls._table([["Field", "Value", "Field", "Value"]] + metadata_rows, col_widths=[45*mm, 55*mm, 45*mm, 40*mm]))
        story.extend(
            [
                Spacer(1, 12),
                Paragraph("B. JUSTIFICATION AND REMARKS", styles["SectionTitle"]),
                Paragraph(f"<b>Faculty Justification:</b> {cls._safe_text(request_obj.justification)}", styles["BodyText"]),
                Spacer(1, 3),
                Paragraph(
                    f"<b>Final Review Remarks:</b> {cls._safe_text(request_obj.review_remarks or '-')}",
                    styles["BodyText"],
                ),
                Spacer(1, 12),
                Paragraph("C. OFFICIAL GRADE SUMMARY", styles["SectionTitle"]),
                Paragraph(
                    "Detailed correction scope remains available inside EduGradesPro. This printable report shows only the official grade value to be posted in AIMS.",
                    styles["SmallBody"],
                ),
                Spacer(1, 4),
                cls._table(
                    [
                        [
                            "Student No.",
                            "Student Name",
                            f"{report['official_grade_label']} (Original)",
                            f"{report['official_grade_label']} (Corrected)",
                        ]
                    ]
                    + (report["official_grade_rows"] or [["-", "-", "-", "-"]]),
                    col_widths=[28*mm, 56*mm, 43*mm, 43*mm],
                ),
                Spacer(1, 12),
                Paragraph("D. APPROVAL SUMMARY", styles["SectionTitle"]),
                cls._table(
                    [["Step", "Approver Role", "Status", "Reviewed By", "Reviewed At"]]
                    + (report["approval_rows"] or [["-", "-", "-", "-", "-"]]),
                    col_widths=[12*mm, 45*mm, 24*mm, 50*mm, 35*mm],
                ),
                Spacer(1, 12),
                Paragraph("E. REGISTRAR REFERENCE", styles["SectionTitle"]),
                Paragraph(
                    "This correction completed academic approval in EduGradesPro and is issued as the official reference document for registrar posting in AIMS.",
                    styles["BodyText"],
                ),
                Spacer(1, 4),
                cls._table(
                    [
                        ["For Registrar Use", "", "", ""],
                        ["Applicable Campus", request_obj.campus.name, "Posted to AIMS By", ""],
                        ["Date Posted", "", "AIMS Reference / Transaction No.", ""],
                        ["Remarks", "", "", ""],
                    ],
                    col_widths=[38*mm, 52*mm, 45*mm, 40*mm],
                ),
            ]
        )

        doc.build(story)
        return buffer.getvalue()

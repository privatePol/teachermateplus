from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
import base64
import hashlib
from io import BytesIO
from pathlib import Path
from urllib.parse import urlencode

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape, legal
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.accounts.models import UserSignatureUsageLog
from apps.accounts.services import UserSignatureService
from apps.academics.models import FacultyAssignment, FacultyAssignmentReplacementLog
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.enrollment.models import Enrollment
from apps.grading.models import (
    FacultyFinalClearanceReport,
    GradeActivity,
    GradeSubmission,
    StudentActivityScore,
    StudentFinalGrade,
    StudentPeriodGrade,
)
from apps.grading.services import FacultyGradingService
from apps.grading.tabulation import DetailedTabulationSheetGridService


class InstitutionalReportPdfConfig:
    """Single source of truth for institutional report page geometry."""

    LEGAL_LANDSCAPE_PAGE_SIZE = landscape(legal)
    LEFT_MARGIN = 8 * mm
    RIGHT_MARGIN = 8 * mm
    TOP_MARGIN = 29 * mm
    BOTTOM_MARGIN = 14 * mm
    CSS_PAGE_SIZE = "legal landscape"
    CSS_PAGE_MARGIN = "10mm"


class TabulationSheetAuthorizationService:
    @staticmethod
    def faculty_assignments_for_offering(*, faculty_user, offering_id):
        queryset = FacultyAssignment.objects.filter(
                faculty_user=faculty_user,
                response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
                accepted_at__isnull=False,
                offering__is_active=True,
                offering__tenant__is_active=True,
                offering__campus__is_active=True,
                offering__academic_year__is_active=True,
                offering__term__is_active=True,
                offering__course__is_active=True,
                offering__section__is_active=True,
            )
        if offering_id is not None:
            queryset = queryset.filter(offering_id=offering_id)
        return (
            queryset
            .exclude(
                replacement_logs_as_old_assignment__replacement_type__in=(
                    FacultyAssignmentReplacementLog.ReplacementType.PERMANENT,
                    FacultyAssignmentReplacementLog.ReplacementType.ADMINISTRATIVE,
                    FacultyAssignmentReplacementLog.ReplacementType.WRONG_ASSIGNMENT,
                )
            )
            .select_related(
                "offering",
                "offering__tenant",
                "offering__campus",
                "offering__department",
                "offering__program",
                "offering__academic_year",
                "offering__term",
                "offering__course",
                "offering__section",
                "faculty_user",
            )
            .order_by("-is_active", "-is_primary", "-accepted_at", "-id")
            .distinct()
        )

    @staticmethod
    def report_faculty_for_offering(*, offering):
        assignment = (
            offering.faculty_assignments.filter(
                is_active=True,
                response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
                accepted_at__isnull=False,
            )
            .select_related("faculty_user")
            .order_by("-is_active", "-is_primary", "-accepted_at", "-id")
            .first()
        )
        return assignment.faculty_user if assignment else None


class TabulationSheetSignatureService:
    """Resolve only the offering faculty's signature for this document type."""

    @staticmethod
    def signature_bytes_for_report(*, offering, faculty_user):
        if not faculty_user:
            return None
        if not FeatureSettingsService.is_user_signatures_enabled(
            tenant_id=offering.tenant_id,
            default=False,
        ):
            return None
        return UserSignatureService.signature_image_bytes_for_user(user=faculty_user)


class CompleteTabulationSheetDataService:
    @staticmethod
    def _format_decimal(value):
        if value in (None, ""):
            return ""
        decimal_value = Decimal(str(value)).quantize(Decimal("0.01"))
        return format(decimal_value, "f").rstrip("0").rstrip(".")

    @staticmethod
    def _student_name(student):
        middle = f" {student.middle_name.strip()}" if getattr(student, "middle_name", "") else ""
        return f"{student.last_name}, {student.first_name}{middle}".strip()

    @classmethod
    def build_report(cls, *, offering, faculty_user, generated_by, portal_code):
        template = FacultyGradingService.resolve_template_for_offering(offering)
        periods = list(FacultyGradingService.get_template_periods(template))
        enrollments = list(
            Enrollment.objects.filter(course_offering=offering, is_active=True)
            .select_related("student")
            .order_by("student__last_name", "student__first_name", "student__student_no")
        )
        student_ids = [row.student_id for row in enrollments]
        period_ids = [period.id for period in periods]
        activities = list(
            GradeActivity.objects.filter(
                offering=offering,
                template_period_id__in=period_ids,
                is_active=True,
            )
            .select_related("template_period", "template_component", "template_subcomponent", "template_detail")
            .order_by(
                "template_period__sequence_no",
                "template_component__sort_order",
                "template_subcomponent__sort_order",
                "template_detail__sort_order",
                "activity_date",
                "id",
            )
        )
        activity_ids = [activity.id for activity in activities]
        scores = StudentActivityScore.objects.filter(
            activity_id__in=activity_ids,
            student_id__in=student_ids,
            is_active=True,
            activity__is_active=True,
        )
        score_map = {(row.activity_id, row.student_id): row for row in scores}
        period_grade_map = {
            (row.template_period_id, row.student_id): row
            for row in StudentPeriodGrade.objects.filter(
                offering=offering,
                template_period_id__in=period_ids,
                student_id__in=student_ids,
            )
        }
        final_grade_map = {
            row.student_id: row
            for row in StudentFinalGrade.objects.filter(offering=offering, student_id__in=student_ids)
        }
        submission_map = {
            row.template_period_id: row
            for row in GradeSubmission.objects.filter(offering=offering, template_period_id__in=period_ids)
        }
        passing_threshold = FacultyGradingService.resolve_passing_threshold(offering)
        activities_by_period = defaultdict(list)
        for activity in activities:
            activities_by_period[activity.template_period_id].append(activity)

        student_rows = []
        for number, enrollment in enumerate(enrollments, start=1):
            period_values = {
                period.id: (
                    period_grade_map[(period.id, enrollment.student_id)].period_grade
                    if (period.id, enrollment.student_id) in period_grade_map
                    else None
                )
                for period in periods
            }
            final_detail = FacultyGradingService.compute_final_grade_detail_from_period_values(
                offering=offering,
                template=template,
                period_values_by_period_id=period_values,
            )
            stored_final = final_grade_map.get(enrollment.student_id)
            official_final = stored_final.final_grade if stored_final else final_detail["official_value"]
            enrollment_label = enrollment.get_enrollment_status_display()
            if enrollment.enrollment_status == Enrollment.Status.ACTIVE:
                if stored_final and stored_final.remarks:
                    remarks = stored_final.remarks
                elif official_final is None:
                    remarks = "Incomplete Record"
                else:
                    remarks = "Passed" if Decimal(official_final) >= passing_threshold else "Failed"
            else:
                remarks = enrollment_label
            student_rows.append(
                {
                    "number": number,
                    "student_id": enrollment.student_id,
                    "student_no": enrollment.student.student_no,
                    "student_name": cls._student_name(enrollment.student),
                    "status": enrollment_label,
                    "period_values": period_values,
                    "computed_final_grade": final_detail["raw_value"],
                    "official_final_grade": official_final,
                    "remarks": remarks,
                }
            )

        period_sections = []
        for period in periods:
            period_activities = activities_by_period.get(period.id, [])
            activity_rows = []
            for activity in period_activities:
                component_bits = [activity.template_component.name]
                if activity.template_subcomponent:
                    component_bits.append(activity.template_subcomponent.name)
                if activity.template_detail:
                    component_bits.append(activity.template_detail.name)
                values = {}
                for enrollment in enrollments:
                    score = score_map.get((activity.id, enrollment.student_id))
                    if score is None:
                        values[enrollment.student_id] = "MISSING"
                    elif score.is_excused:
                        values[enrollment.student_id] = "EXEMPT"
                    else:
                        values[enrollment.student_id] = cls._format_decimal(score.raw_score)
                activity_rows.append(
                    {
                        "id": activity.id,
                        "title": activity.title,
                        "component": " / ".join(bit for bit in component_bits if bit),
                        "maximum_score": cls._format_decimal(activity.total_score),
                        "values": values,
                    }
                )
            submission = submission_map.get(period.id)
            period_sections.append(
                {
                    "period": period,
                    "label": period.name or period.code,
                    "submission_status": submission.get_status_display() if submission else "Not Submitted",
                    "activities": activity_rows,
                }
            )

        faculty_name = (
            faculty_user.full_name or faculty_user.username
            if faculty_user
            else "No assigned faculty"
        )
        generated_by_name = generated_by.full_name or generated_by.username
        signature_bytes = TabulationSheetSignatureService.signature_bytes_for_report(
            offering=offering,
            faculty_user=faculty_user,
        )
        stored_grade_map = defaultdict(dict)
        for (period_id, student_id), grade_row in period_grade_map.items():
            stored_grade_map[period_id][student_id] = grade_row
        detailed_grid = DetailedTabulationSheetGridService.build(
            offering=offering,
            periods=periods,
            enrollments=enrollments,
            stored_grade_map=stored_grade_map,
            final_grade_map={
                student_id: grade_row.final_grade
                for student_id, grade_row in final_grade_map.items()
            },
        )
        for group in detailed_grid["period_column_groups"]:
            submission = submission_map.get(group["period"].id)
            group["submission_status"] = (
                submission.get_status_display() if submission else "Not Submitted"
            )
        return {
            "offering": offering,
            "template": template,
            "periods": periods,
            "period_sections": period_sections,
            "student_rows": student_rows,
            "faculty_user": faculty_user,
            "faculty_name": faculty_name,
            "generated_by_user": generated_by,
            "generated_by_name": generated_by_name,
            "generated_at": timezone.localtime(),
            "portal_code": portal_code,
            "print_header_name": SystemSettingService.get(
                "PRINT_HEADER_SCHOOL_NAME",
                tenant_id=offering.tenant_id,
                default=offering.tenant.name,
            ),
            "print_header_address": SystemSettingService.get(
                "PRINT_HEADER_SCHOOL_ADDRESS",
                tenant_id=offering.tenant_id,
                default=getattr(offering.campus, "address", "") or "",
            ),
            "signature_bytes": signature_bytes,
            "signature_data_uri": (
                f"data:image/png;base64,{base64.b64encode(signature_bytes).decode('ascii')}"
                if signature_bytes
                else ""
            ),
            "print_page_css_size": InstitutionalReportPdfConfig.CSS_PAGE_SIZE,
            "print_page_css_margin": InstitutionalReportPdfConfig.CSS_PAGE_MARGIN,
            **detailed_grid,
        }


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
    def _signature_enabled_for_document(cls, *, tenant_id, document_type: str) -> bool:
        if not FeatureSettingsService.is_user_signatures_enabled(tenant_id=tenant_id, default=False):
            return False
        document_type = (document_type or "").upper()
        if document_type == UserSignatureUsageLog.DocumentType.FINAL_CLEARANCE:
            return FeatureSettingsService.is_user_signature_final_clearance_enabled(
                tenant_id=tenant_id,
                default=False,
            )
        if document_type == UserSignatureUsageLog.DocumentType.CORRECTION_OFFICIAL_REPORT:
            return FeatureSettingsService.is_user_signature_correction_report_enabled(
                tenant_id=tenant_id,
                default=False,
            )
        return False

    @classmethod
    def _signature_panel(cls, *, user, label: str, role_caption: str, styles, usage_collector: list, usage_kwargs: dict):
        panel_rows = []
        credential = UserSignatureService.get_active_credential(user=user) if user else None
        if credential and credential.has_signature:
            signature_bytes = UserSignatureService.decrypt_signature_bytes(credential=credential)
            image_stream = BytesIO(signature_bytes)
            max_width = 42 * mm
            max_height = 14 * mm
            width_ratio = max_width / max(float(credential.image_width or 1), 1.0)
            height_ratio = max_height / max(float(credential.image_height or 1), 1.0)
            scale = min(width_ratio, height_ratio)
            signature_image = Image(
                image_stream,
                width=max(float(credential.image_width or 1) * scale, 12),
                height=max(float(credential.image_height or 1) * scale, 6),
            )
            signature_image.hAlign = "LEFT"
            panel_rows.append([signature_image])
            usage_collector.append(usage_kwargs)
        else:
            panel_rows.append([Paragraph("<i>No stored signature on file.</i>", styles["SmallBody"])])

        signer_name = "-"
        if user:
            signer_name = cls._safe_text((getattr(user, "full_name", "") or "").strip() or user.username)
        panel_rows.extend(
            [
                [Paragraph(f"<b>{cls._safe_text(label)}</b>", styles["SmallBody"])],
                [Paragraph(signer_name, styles["SmallBody"])],
                [Paragraph(cls._safe_text(role_caption), styles["SmallBody"])],
            ]
        )
        panel = Table(panel_rows, colWidths=[54 * mm])
        panel.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor("#d9e2ec")),
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fbfcfd")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        return panel

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
            if enrollment.enrollment_status in Enrollment.NON_ACTIVE_GRADING_STATUSES:
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
                            detail_has_data = False
                            detail_scores = []
                            for detail in detail_rows:
                                detail_key = (student_id, component.id, sub.id, detail.id)
                                detail_score = FacultyGradingService._average_score_or_none(
                                    score_lookup,
                                    detail_key,
                                )
                                detail_scores.append(
                                    (
                                        Decimal(detail.weight_percentage or 0),
                                        score_lookup.get(detail_key, []) if sub.detail_computation_mode == "AVERAGE_ACTIVITIES" else detail_score,
                                    )
                                )
                                if detail_score is not None:
                                    detail_has_data = True
                            sub_score = FacultyGradingService.aggregate_detail_scores(
                                subcomponent=sub,
                                detail_scores=detail_scores,
                            )
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

                if FacultyGradingService.is_exam_component(component):
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
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#243b53")),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d9e2ec")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafbfc")]),
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
        signature_usage_entries = []

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
        if request_obj.request_source == request_obj.RequestSource.ADMIN_ON_BEHALF:
            initiated_by = request_obj.initiated_by_user.full_name if request_obj.initiated_by_user_id else "-"
            metadata_rows.append(["Petition Source", "Admin on behalf", "Initiated By", initiated_by])
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
                    "Detailed correction scope remains available inside TeacherMate+. This printable report shows only the official grade value to be posted in AIMS.",
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
            ]
        )

        signatures_enabled = cls._signature_enabled_for_document(
            tenant_id=request_obj.tenant_id,
            document_type=UserSignatureUsageLog.DocumentType.CORRECTION_OFFICIAL_REPORT,
        )
        if signatures_enabled:
            signature_panels = [
                cls._signature_panel(
                    user=request_obj.requested_by_user,
                    label="Faculty Petitioner",
                    role_caption="Requested By",
                    styles=styles,
                    usage_collector=signature_usage_entries,
                    usage_kwargs={
                        "user": request_obj.requested_by_user,
                        "document_type": UserSignatureUsageLog.DocumentType.CORRECTION_OFFICIAL_REPORT,
                        "document_reference": report["reference_no"],
                        "usage_role": "Faculty Petitioner",
                        "actor": request_obj.requested_by_user,
                        "portal_code": "FACULTY",
                        "metadata": {
                            "correction_request_id": request_obj.id,
                            "tenant_id": request_obj.tenant_id,
                            "campus_id": request_obj.campus_id,
                        },
                    },
                )
            ]
            for approval_step in request_obj.approval_steps.select_related("reviewed_by_user").order_by("step_order"):
                if not approval_step.reviewed_by_user_id:
                    continue
                signature_panels.append(
                    cls._signature_panel(
                        user=approval_step.reviewed_by_user,
                        label=approval_step.approver_label,
                        role_caption=f"{approval_step.status.title()} Step {approval_step.step_order}",
                        styles=styles,
                        usage_collector=signature_usage_entries,
                        usage_kwargs={
                            "user": approval_step.reviewed_by_user,
                            "document_type": UserSignatureUsageLog.DocumentType.CORRECTION_OFFICIAL_REPORT,
                            "document_reference": report["reference_no"],
                            "usage_role": approval_step.approver_label,
                            "actor": approval_step.reviewed_by_user,
                            "portal_code": "ADMIN",
                            "metadata": {
                                "correction_request_id": request_obj.id,
                                "approval_step_id": approval_step.id,
                                "tenant_id": request_obj.tenant_id,
                                "campus_id": request_obj.campus_id,
                            },
                        },
                    )
                )
            signature_table_rows = []
            for index in range(0, len(signature_panels), 2):
                row = signature_panels[index:index + 2]
                if len(row) == 1:
                    row.append(Spacer(1, 1))
                signature_table_rows.append(row)

            story.extend(
                [
                    Paragraph("E. SIGNATURES", styles["SectionTitle"]),
                    Paragraph(
                        "Stored NCBA user signatures appear below only when the signature feature is enabled and the signer has an encrypted signature on file.",
                        styles["SmallBody"],
                    ),
                    Spacer(1, 4),
                    Table(signature_table_rows, colWidths=[85 * mm, 85 * mm], hAlign="LEFT"),
                    Spacer(1, 12),
                ]
            )

        story.extend(
            [
                Paragraph("F. REGISTRAR REFERENCE" if signatures_enabled else "E. REGISTRAR REFERENCE", styles["SectionTitle"]),
                Paragraph(
                    "This correction completed academic approval in TeacherMate+ and is issued as the official reference document for registrar posting in AIMS.",
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
        for usage_kwargs in signature_usage_entries:
            UserSignatureService.log_signature_usage(**usage_kwargs)
        return buffer.getvalue()


class ClassTabulationSheetPdfService:
    LOGO_PATH = CorrectionOfficialReportService.LOGO_PATH

    @staticmethod
    def _safe_text(value):
        return str(value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    @classmethod
    def _paragraph(cls, value, style):
        return Paragraph(cls._safe_text(value), style)

    @classmethod
    def _draw_page_background(cls, canvas, doc):
        canvas.saveState()
        page_width, page_height = doc.pagesize
        canvas.setFillColor(colors.HexColor("#eef4ee"))
        canvas.setFont("Helvetica-Bold", 10)
        horizontal_spacing = 34 * mm
        vertical_spacing = 24 * mm
        y = 24 * mm
        row_index = 0
        while y < page_height - 16 * mm:
            x = 18 * mm + ((row_index % 2) * 17 * mm)
            while x < page_width - 12 * mm:
                canvas.saveState()
                canvas.translate(x, y)
                canvas.rotate(28)
                canvas.drawCentredString(0, 0, "NCBA")
                canvas.restoreState()
                x += horizontal_spacing
            y += vertical_spacing
            row_index += 1
        canvas.restoreState()

        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#94a38f"))
        canvas.setLineWidth(0.3)
        canvas.line(doc.leftMargin, 12 * mm, page_width - doc.rightMargin, 12 * mm)
        canvas.setFillColor(colors.HexColor("#4b5563"))
        canvas.setFont("Helvetica", 7)
        canvas.drawRightString(page_width - doc.rightMargin, 7 * mm, f"Page {doc.page}")
        canvas.restoreState()

    @classmethod
    def _column_widths(cls, dynamic_column_count):
        usable_width = InstitutionalReportPdfConfig.LEGAL_LANDSCAPE_PAGE_SIZE[0] - (16 * mm)
        fixed_widths = [8 * mm, 22 * mm, 45 * mm, 11 * mm]
        final_width = 14 * mm
        remaining = usable_width - sum(fixed_widths) - final_width
        dynamic_width = max(6.5 * mm, min(12 * mm, remaining / max(dynamic_column_count, 1)))
        return fixed_widths + ([dynamic_width] * dynamic_column_count) + [final_width]

    @classmethod
    def _build_legacy_pdf_bytes(cls, *, report):
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=InstitutionalReportPdfConfig.LEGAL_LANDSCAPE_PAGE_SIZE,
            leftMargin=8 * mm,
            rightMargin=8 * mm,
            topMargin=8 * mm,
            bottomMargin=14 * mm,
            title="Class Tabulation Sheet",
        )
        styles = getSampleStyleSheet()
        styles.add(ParagraphStyle(name="Tiny", fontSize=5.4, leading=6.2))
        styles.add(ParagraphStyle(name="TinyBold", fontSize=5.4, leading=6.2, fontName="Helvetica-Bold"))
        styles.add(ParagraphStyle(name="SmallCenter", fontSize=7, leading=8.5, alignment=1))
        styles.add(ParagraphStyle(name="HeaderTitle", fontSize=12, leading=14, alignment=1, fontName="Helvetica-Bold"))
        styles.add(ParagraphStyle(name="HeaderSmall", fontSize=7.5, leading=9, alignment=1))

        story = []
        header_rows = []
        if cls.LOGO_PATH.exists():
            logo = Image(str(cls.LOGO_PATH), width=18 * mm, height=18 * mm)
            header_rows.append([logo])
        header_rows.extend(
            [
                [Paragraph(cls._safe_text(report["print_header_name"]), styles["HeaderTitle"])],
                [Paragraph(cls._safe_text(report.get("print_header_address") or ""), styles["HeaderSmall"])],
                [Paragraph("CLASS TABULATION SHEET", styles["HeaderTitle"])],
            ]
        )
        header = Table(header_rows, colWidths=[180 * mm])
        header.setStyle(
            TableStyle(
                [
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 1),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                ]
            )
        )
        story.append(header)
        story.append(Spacer(1, 4))

        offering = report["offering"]
        meta_rows = [
            [
                cls._paragraph("Tenant", styles["TinyBold"]),
                cls._paragraph(offering.tenant.name, styles["Tiny"]),
                cls._paragraph("Campus", styles["TinyBold"]),
                cls._paragraph(offering.campus.name, styles["Tiny"]),
                cls._paragraph("Academic Year / Term", styles["TinyBold"]),
                cls._paragraph(f"{offering.academic_year.code} / {offering.term.name or offering.term.code}", styles["Tiny"]),
            ],
            [
                cls._paragraph("Faculty", styles["TinyBold"]),
                cls._paragraph(report["faculty_name"], styles["Tiny"]),
                cls._paragraph("Section", styles["TinyBold"]),
                cls._paragraph(offering.section.code, styles["Tiny"]),
                cls._paragraph("Room", styles["TinyBold"]),
                cls._paragraph(offering.room or "TBA", styles["Tiny"]),
            ],
            [
                cls._paragraph("Course", styles["TinyBold"]),
                cls._paragraph(f"{offering.course.code} - {offering.course.title}", styles["Tiny"]),
                cls._paragraph("Generated", styles["TinyBold"]),
                cls._paragraph(report["generated_at"].strftime("%Y-%m-%d %H:%M"), styles["Tiny"]),
                "",
                "",
            ],
        ]
        meta_table = Table(meta_rows, colWidths=[18 * mm, 78 * mm, 18 * mm, 58 * mm, 25 * mm, 70 * mm])
        meta_table.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cfd8cf")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                    ("LEFTPADDING", (0, 0), (-1, -1), 3),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        story.extend([meta_table, Spacer(1, 5)])

        period_header = [
            Paragraph("No.", styles["TinyBold"]),
            Paragraph("Student No", styles["TinyBold"]),
            Paragraph("Student Name", styles["TinyBold"]),
            Paragraph("Status", styles["TinyBold"]),
        ]
        detail_header = ["", "", "", ""]
        span_styles = []
        cursor = 4
        for period in report["period_column_groups"]:
            period_header.append(Paragraph(cls._safe_text(period["label"]), styles["TinyBold"]))
            period_header.extend([""] * max(len(period["columns"]) - 1, 0))
            detail_header.extend([Paragraph(cls._safe_text(column["label"]), styles["TinyBold"]) for column in period["columns"]])
            end_cursor = cursor + len(period["columns"]) - 1
            if end_cursor > cursor:
                span_styles.append(("SPAN", (cursor, 0), (end_cursor, 0)))
            cursor = end_cursor + 1
        period_header.append(Paragraph("Final Grade", styles["TinyBold"]))
        detail_header.append("")

        rows = [period_header, detail_header]
        rows.append(
            [
                "",
                "",
                Paragraph("Highest Possible Score", styles["TinyBold"]),
                "",
                *[Paragraph(cls._safe_text(value), styles["Tiny"]) for value in report["highest_row"]],
                "",
            ]
        )
        for row in report["sheet_rows"]:
            rows.append(
                [
                    str(row["number"]),
                    Paragraph(cls._safe_text(row["student_no"]), styles["Tiny"]),
                    Paragraph(cls._safe_text(row["student_name"]), styles["Tiny"]),
                    Paragraph(cls._safe_text(row["status"]), styles["Tiny"]),
                    *[Paragraph(cls._safe_text(value), styles["Tiny"]) for value in row["values"]],
                    Paragraph(cls._safe_text(row["final_grade"]), styles["TinyBold"]),
                ]
            )
        rows.append(
            [
                "",
                "",
                Paragraph("**** NOTHING FOLLOWS *****", styles["TinyBold"]),
                "",
                *([""] * len(report["highest_row"])),
                "",
            ]
        )

        table = Table(rows, colWidths=cls._column_widths(len(report["highest_row"])), repeatRows=2)
        table_style = [
            ("GRID", (0, 0), (-1, -1), 0.2, colors.HexColor("#cfd8cf")),
            ("BACKGROUND", (0, 0), (-1, 1), colors.HexColor("#e8f2e8")),
            ("BACKGROUND", (0, 2), (-1, 2), colors.HexColor("#f1f7f1")),
            ("BACKGROUND", (-1, 0), (-1, -1), colors.HexColor("#fff7d6")),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("ALIGN", (2, 0), (2, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("FONTNAME", (0, 0), (-1, 1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 5.4),
            ("LEFTPADDING", (0, 0), (-1, -1), 1.6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 1.6),
            ("TOPPADDING", (0, 0), (-1, -1), 1.4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1.4),
        ]
        table_style.extend(span_styles)
        table.setStyle(TableStyle(table_style))
        story.append(table)
        story.append(Spacer(1, 12))

        signature_table = Table(
            [
                ["", Paragraph("Prepared and Submitted By", styles["TinyBold"])],
                ["", Paragraph(cls._safe_text(report["faculty_name"]).upper(), styles["SmallCenter"])],
            ],
            colWidths=[220 * mm, 70 * mm],
        )
        signature_table.setStyle(
            TableStyle(
                [
                    ("LINEABOVE", (1, 1), (1, 1), 0.5, colors.black),
                    ("ALIGN", (1, 0), (1, -1), "CENTER"),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        story.append(signature_table)

        doc.build(story, onFirstPage=cls._draw_page_background, onLaterPages=cls._draw_page_background)
        return buffer.getvalue()

    MAX_ACTIVITY_COLUMNS_PER_PAGE = 12
    MAX_SUMMARY_PERIOD_COLUMNS_PER_PAGE = 6

    @staticmethod
    def _fit_canvas_text(value, *, font_name, font_size, max_width):
        text = str(value or "")
        if stringWidth(text, font_name, font_size) <= max_width:
            return text
        suffix = "..."
        while text and stringWidth(text + suffix, font_name, font_size) > max_width:
            text = text[:-1]
        return text + suffix

    @classmethod
    def _complete_page_header(cls, canvas, doc):
        report = doc.complete_tabulation_report
        offering = report["offering"]
        page_width, page_height = doc.pagesize
        canvas.saveState()
        canvas.saveState()
        canvas.setFillColor(colors.HexColor("#edf4ee"))
        canvas.setFont("Helvetica-Bold", 72)
        canvas.translate(page_width / 2, page_height / 2)
        canvas.rotate(28)
        canvas.drawCentredString(0, 0, "NCBA")
        canvas.restoreState()

        canvas.setFillColor(colors.HexColor("#123b25"))
        if cls.LOGO_PATH.exists():
            canvas.drawImage(
                str(cls.LOGO_PATH),
                (page_width / 2) - (78 * mm),
                page_height - 18 * mm,
                width=12 * mm,
                height=12 * mm,
                preserveAspectRatio=True,
                mask="auto",
            )
        canvas.setFont("Helvetica-Bold", 9.5)
        canvas.drawCentredString(page_width / 2, page_height - 7 * mm, report["print_header_name"].upper())
        canvas.setFont("Helvetica", 6.2)
        canvas.drawCentredString(page_width / 2, page_height - 11 * mm, report.get("print_header_address") or "")
        canvas.setFont("Helvetica-Bold", 9)
        canvas.drawCentredString(page_width / 2, page_height - 15.5 * mm, "COMPLETE TABULATION SHEET")
        canvas.setFont("Helvetica", 6.5)
        course_line = (
            f"{offering.course.code} - {offering.course.title} | Section {offering.section.code} | "
            f"{offering.academic_year.code} / {offering.term.name or offering.term.code}"
        )
        canvas.drawCentredString(
            page_width / 2,
            page_height - 20.5 * mm,
            cls._fit_canvas_text(
                course_line,
                font_name="Helvetica",
                font_size=6.5,
                max_width=page_width - doc.leftMargin - doc.rightMargin,
            ),
        )
        canvas.setStrokeColor(colors.HexColor("#94a38f"))
        canvas.setLineWidth(0.4)
        canvas.line(doc.leftMargin, page_height - 23.5 * mm, page_width - doc.rightMargin, page_height - 23.5 * mm)
        canvas.line(doc.leftMargin, 11 * mm, page_width - doc.rightMargin, 11 * mm)
        canvas.setFillColor(colors.HexColor("#4b5563"))
        canvas.setFont("Helvetica", 6.5)
        canvas.drawString(doc.leftMargin, 6.5 * mm, "Legal landscape - 14 x 8.5 inches")
        canvas.drawRightString(page_width - doc.rightMargin, 6.5 * mm, f"Page {doc.page}")
        canvas.restoreState()

    @staticmethod
    def _chunked(items, size):
        if not items:
            return [[]]
        return [items[index : index + size] for index in range(0, len(items), size)]

    @classmethod
    def _complete_styles(cls):
        styles = getSampleStyleSheet()
        styles.add(ParagraphStyle(name="ReportTiny", fontSize=6.4, leading=7.6))
        styles.add(ParagraphStyle(name="ReportTinyCenter", fontSize=6.4, leading=7.6, alignment=1))
        styles.add(
            ParagraphStyle(
                name="ReportTinyBold",
                fontSize=6.4,
                leading=7.6,
                fontName="Helvetica-Bold",
            )
        )
        styles.add(
            ParagraphStyle(
                name="ReportSection",
                fontSize=9,
                leading=11,
                fontName="Helvetica-Bold",
                textColor=colors.HexColor("#123b25"),
                spaceAfter=4,
            )
        )
        return styles

    @classmethod
    def _complete_metadata_table(cls, report, styles):
        offering = report["offering"]
        rows = [
            [
                cls._paragraph("Tenant", styles["ReportTinyBold"]),
                cls._paragraph(offering.tenant.name, styles["ReportTiny"]),
                cls._paragraph("Campus", styles["ReportTinyBold"]),
                cls._paragraph(offering.campus.name, styles["ReportTiny"]),
            ],
            [
                cls._paragraph("Academic Year", styles["ReportTinyBold"]),
                cls._paragraph(offering.academic_year.code, styles["ReportTiny"]),
                cls._paragraph("Semester / Term", styles["ReportTinyBold"]),
                cls._paragraph(offering.term.name or offering.term.code, styles["ReportTiny"]),
            ],
            [
                cls._paragraph("Section", styles["ReportTinyBold"]),
                cls._paragraph(offering.section.code, styles["ReportTiny"]),
                cls._paragraph("Faculty", styles["ReportTinyBold"]),
                cls._paragraph(report["faculty_name"], styles["ReportTiny"]),
            ],
            [
                cls._paragraph("Course Code", styles["ReportTinyBold"]),
                cls._paragraph(offering.course.code, styles["ReportTiny"]),
                cls._paragraph("Room", styles["ReportTinyBold"]),
                cls._paragraph(offering.room or "TBA", styles["ReportTiny"]),
            ],
            [
                cls._paragraph("Course Title", styles["ReportTinyBold"]),
                cls._paragraph(offering.course.title, styles["ReportTiny"]),
                cls._paragraph("Generated", styles["ReportTinyBold"]),
                cls._paragraph(report["generated_at"].strftime("%Y-%m-%d %H:%M %Z"), styles["ReportTiny"]),
            ],
        ]
        table = Table(rows, colWidths=[25 * mm, 130 * mm, 30 * mm, 130 * mm])
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                    ("TOPPADDING", (0, 0), (-1, -1), 1),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                ]
            )
        )
        return table

    @classmethod
    def _summary_table(cls, report, styles, *, periods):
        header = [
            cls._paragraph("No.", styles["ReportTinyBold"]),
            cls._paragraph("Student No", styles["ReportTinyBold"]),
            cls._paragraph("Student Name", styles["ReportTinyBold"]),
            cls._paragraph("Status", styles["ReportTinyBold"]),
            *[cls._paragraph(period.name or period.code, styles["ReportTinyBold"]) for period in periods],
            cls._paragraph("Computed Final", styles["ReportTinyBold"]),
            cls._paragraph("Official Final", styles["ReportTinyBold"]),
            cls._paragraph("Remarks", styles["ReportTinyBold"]),
        ]
        rows = [header]
        for row in report["student_rows"]:
            rows.append(
                [
                    str(row["number"]),
                    cls._paragraph(row["student_no"], styles["ReportTiny"]),
                    cls._paragraph(row["student_name"], styles["ReportTiny"]),
                    cls._paragraph(row["status"], styles["ReportTiny"]),
                    *[
                        cls._paragraph(
                            CompleteTabulationSheetDataService._format_decimal(row["period_values"].get(period.id)) or "MISSING",
                            styles["ReportTinyCenter"],
                        )
                        for period in periods
                    ],
                    cls._paragraph(
                        CompleteTabulationSheetDataService._format_decimal(row["computed_final_grade"]) or "-",
                        styles["ReportTinyCenter"],
                    ),
                    cls._paragraph(
                        CompleteTabulationSheetDataService._format_decimal(row["official_final_grade"]) or "-",
                        styles["ReportTinyCenter"],
                    ),
                    cls._paragraph(row["remarks"], styles["ReportTiny"]),
                ]
            )
        if len(rows) == 1:
            rows.append(["", "", cls._paragraph("No enrolled students.", styles["ReportTinyBold"])] + [""] * (len(header) - 3))
        period_width = min(24 * mm, max(16 * mm, 95 * mm / max(len(periods), 1)))
        widths = [8 * mm, 23 * mm, 52 * mm, 19 * mm] + ([period_width] * len(periods)) + [23 * mm, 23 * mm, 40 * mm]
        table = Table(rows, colWidths=widths, repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#b8c5b8")),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8f2e8")),
                    ("BACKGROUND", (-3, 1), (-2, -1), colors.HexColor("#fff7d6")),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("ALIGN", (0, 0), (0, -1), "CENTER"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 2),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        return table

    @classmethod
    def _period_detail_table(cls, report, section, activity_chunk, styles):
        activity_count = max(len(activity_chunk), 1)
        first_header = [
            cls._paragraph("No.", styles["ReportTinyBold"]),
            cls._paragraph("Student No", styles["ReportTinyBold"]),
            cls._paragraph("Student Name", styles["ReportTinyBold"]),
            cls._paragraph("Status", styles["ReportTinyBold"]),
        ]
        second_header = ["", "", "", ""]
        if activity_chunk:
            for activity in activity_chunk:
                first_header.append(cls._paragraph(activity["title"], styles["ReportTinyBold"]))
                second_header.append(
                    Paragraph(
                        f"{cls._safe_text(activity['component'])}<br/>"
                        f"Max: {cls._safe_text(activity['maximum_score'])}",
                        styles["ReportTinyCenter"],
                    )
                )
        else:
            first_header.append(cls._paragraph("Activities", styles["ReportTinyBold"]))
            second_header.append(cls._paragraph("No active graded activities", styles["ReportTinyCenter"]))
        first_header.append(cls._paragraph("Period Grade", styles["ReportTinyBold"]))
        second_header.append(cls._paragraph(section["submission_status"], styles["ReportTinyCenter"]))
        rows = [first_header, second_header]
        for row in report["student_rows"]:
            activity_values = (
                [cls._paragraph(activity["values"].get(row["student_id"], "MISSING"), styles["ReportTinyCenter"]) for activity in activity_chunk]
                if activity_chunk
                else [""]
            )
            rows.append(
                [
                    str(row["number"]),
                    cls._paragraph(row["student_no"], styles["ReportTiny"]),
                    cls._paragraph(row["student_name"], styles["ReportTiny"]),
                    cls._paragraph(row["status"], styles["ReportTiny"]),
                    *activity_values,
                    cls._paragraph(
                        CompleteTabulationSheetDataService._format_decimal(row["period_values"].get(section["period"].id)) or "MISSING",
                        styles["ReportTinyCenter"],
                    ),
                ]
            )
        if len(rows) == 2:
            rows.append(["", "", cls._paragraph("No enrolled students.", styles["ReportTinyBold"])] + [""] * (len(first_header) - 3))
        identity_widths = [8 * mm, 23 * mm, 50 * mm, 18 * mm]
        activity_width = min(28 * mm, 174 * mm / activity_count)
        widths = identity_widths + ([activity_width] * activity_count) + [22 * mm]
        table = Table(rows, colWidths=widths, repeatRows=2)
        table.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#b8c5b8")),
                    ("BACKGROUND", (0, 0), (-1, 1), colors.HexColor("#e8f2e8")),
                    ("BACKGROUND", (-1, 0), (-1, -1), colors.HexColor("#fff7d6")),
                    ("SPAN", (0, 0), (0, 1)),
                    ("SPAN", (1, 0), (1, 1)),
                    ("SPAN", (2, 0), (2, 1)),
                    ("SPAN", (3, 0), (3, 1)),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("ALIGN", (0, 0), (0, -1), "CENTER"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 2),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        return table

    @classmethod
    def _certification_panel(cls, report, styles):
        signature_cell = cls._paragraph("No stored faculty signature.", styles["ReportTinyCenter"])
        if report.get("signature_bytes"):
            signature_cell = Image(BytesIO(report["signature_bytes"]), width=42 * mm, height=14 * mm, kind="proportional")
            signature_cell.hAlign = "CENTER"
        table = Table(
            [
                [signature_cell],
                [cls._paragraph(report["faculty_name"].upper(), styles["ReportTinyBold"])],
                [cls._paragraph("Prepared and Submitted By", styles["ReportTinyCenter"])],
                [cls._paragraph(report["generated_at"].strftime("%Y-%m-%d"), styles["ReportTinyCenter"])],
            ],
            colWidths=[75 * mm],
            hAlign="RIGHT",
        )
        table.setStyle(
            TableStyle(
                [
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("LINEABOVE", (0, 1), (0, 1), 0.5, colors.black),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        return table

    @classmethod
    def _canonical_grid_table(
        cls,
        report,
        group,
        column_chunk,
        *,
        chunk_start,
        styles,
        include_nothing_follows=False,
    ):
        dynamic_count = max(len(column_chunk), 1)
        first_header = [
            cls._paragraph("No.", styles["ReportTinyBold"]),
            cls._paragraph("Student No", styles["ReportTinyBold"]),
            cls._paragraph("Student Name", styles["ReportTinyBold"]),
            cls._paragraph("Status", styles["ReportTinyBold"]),
            cls._paragraph(group["label"], styles["ReportTinyBold"]),
            *([""] * max(dynamic_count - 1, 0)),
            cls._paragraph("Final Grade", styles["ReportTinyBold"]),
        ]
        second_header = ["", "", "", ""]
        if column_chunk:
            second_header.extend(cls._paragraph(column["label"], styles["ReportTinyBold"]) for column in column_chunk)
        else:
            second_header.append(cls._paragraph("No active grade columns", styles["ReportTinyCenter"]))
        second_header.append("")
        highest_values = group["highest_values"][chunk_start : chunk_start + len(column_chunk)]
        if not column_chunk:
            highest_values = [""]
        rows = [
            first_header,
            second_header,
            [
                "",
                "",
                cls._paragraph("Highest Possible Score", styles["ReportTinyBold"]),
                "",
                *[cls._paragraph(value, styles["ReportTinyCenter"]) for value in highest_values],
                "",
            ],
        ]
        value_start = group["value_offset"] + chunk_start
        value_end = value_start + len(column_chunk)
        for row in report["sheet_rows"]:
            selected_values = row["values"][value_start:value_end] if column_chunk else [""]
            rows.append(
                [
                    str(row["number"]),
                    cls._paragraph(row["student_no"], styles["ReportTiny"]),
                    cls._paragraph(row["student_name"], styles["ReportTiny"]),
                    cls._paragraph(row["status"], styles["ReportTiny"]),
                    *[cls._paragraph(value, styles["ReportTinyCenter"]) for value in selected_values],
                    cls._paragraph(row["final_grade"], styles["ReportTinyBold"]),
                ]
            )
        if not report["sheet_rows"]:
            rows.append(["", "", cls._paragraph("No enrolled students.", styles["ReportTinyBold"]), ""] + ([""] * dynamic_count) + [""])
        if include_nothing_follows:
            rows.append(
                [
                    "",
                    "",
                    cls._paragraph("**** NOTHING FOLLOWS *****", styles["ReportTinyBold"]),
                    "",
                    *([""] * dynamic_count),
                    "",
                ]
            )

        usable_width = (
            InstitutionalReportPdfConfig.LEGAL_LANDSCAPE_PAGE_SIZE[0]
            - InstitutionalReportPdfConfig.LEFT_MARGIN
            - InstitutionalReportPdfConfig.RIGHT_MARGIN
        )
        fixed_widths = [7 * mm, 22 * mm, 46 * mm, 12 * mm]
        final_width = 15 * mm
        dynamic_width = (usable_width - sum(fixed_widths) - final_width) / dynamic_count
        table = Table(
            rows,
            colWidths=fixed_widths + ([dynamic_width] * dynamic_count) + [final_width],
            repeatRows=3,
        )
        last_dynamic_col = 4 + dynamic_count - 1
        table.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#b8c5b8")),
                    ("BACKGROUND", (0, 0), (-1, 1), colors.HexColor("#e8f2e8")),
                    ("BACKGROUND", (0, 2), (-1, 2), colors.HexColor("#f1f7f1")),
                    ("BACKGROUND", (-1, 0), (-1, -1), colors.HexColor("#fff7d6")),
                    ("SPAN", (0, 0), (0, 1)),
                    ("SPAN", (1, 0), (1, 1)),
                    ("SPAN", (2, 0), (2, 1)),
                    ("SPAN", (3, 0), (3, 1)),
                    ("SPAN", (4, 0), (last_dynamic_col, 0)),
                    ("SPAN", (-1, 0), (-1, 1)),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("ALIGN", (2, 2), (2, -1), "LEFT"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 1.6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 1.6),
                    ("TOPPADDING", (0, 0), (-1, -1), 1.6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 1.6),
                ]
            )
        )
        return table

    @classmethod
    def build_pdf_bytes(cls, *, report):
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=InstitutionalReportPdfConfig.LEGAL_LANDSCAPE_PAGE_SIZE,
            leftMargin=InstitutionalReportPdfConfig.LEFT_MARGIN,
            rightMargin=InstitutionalReportPdfConfig.RIGHT_MARGIN,
            topMargin=InstitutionalReportPdfConfig.TOP_MARGIN,
            bottomMargin=InstitutionalReportPdfConfig.BOTTOM_MARGIN,
            title="Complete Tabulation Sheet",
        )
        doc.complete_tabulation_report = report
        styles = cls._complete_styles()
        grid_parts = []
        for group in report["period_column_groups"]:
            chunks = cls._chunked(group["columns"], cls.MAX_ACTIVITY_COLUMNS_PER_PAGE)
            for chunk_number, column_chunk in enumerate(chunks, start=1):
                grid_parts.append((group, column_chunk, chunk_number, len(chunks)))
        if not grid_parts:
            grid_parts.append(
                (
                    {"label": "TABULATION", "columns": [], "highest_values": [], "value_offset": 0},
                    [],
                    1,
                    1,
                )
            )

        story = []
        for part_index, (group, column_chunk, chunk_number, chunk_total) in enumerate(grid_parts):
            if part_index:
                story.append(PageBreak())
            story.extend(
                [
                    cls._complete_metadata_table(report, styles),
                    Spacer(1, 4),
                    Paragraph(
                        f"{group['label']} - PART {chunk_number} OF {chunk_total} - "
                        f"{group.get('submission_status', 'Not Submitted')}",
                        styles["ReportSection"],
                    ),
                    cls._canonical_grid_table(
                        report,
                        group,
                        column_chunk,
                        chunk_start=(chunk_number - 1) * cls.MAX_ACTIVITY_COLUMNS_PER_PAGE,
                        styles=styles,
                        include_nothing_follows=part_index == len(grid_parts) - 1,
                    ),
                ]
            )
            if part_index == 0:
                story.extend(
                    [
                        Spacer(1, 4),
                        Paragraph(
                            "Legend: MISSING = no active score row; 0.00 = encoded zero; "
                            "EXEMPT = excused/not applicable. Inactive activities are excluded.",
                            styles["ReportTiny"],
                        ),
                    ]
                )
        story.extend([Spacer(1, 12), cls._certification_panel(report, styles)])
        doc.build(story, onFirstPage=cls._complete_page_header, onLaterPages=cls._complete_page_header)

        if report.get("signature_bytes") and report.get("faculty_user"):
            UserSignatureService.log_signature_usage(
                user=report["faculty_user"],
                document_type=UserSignatureUsageLog.DocumentType.COMPLETE_TABULATION_SHEET,
                document_reference=f"offering:{report['offering'].id}",
                usage_role="FACULTY_OF_RECORD",
                actor=report.get("generated_by_user") or report["faculty_user"],
                portal_code=report["portal_code"],
                metadata={"offering_id": report["offering"].id},
            )
        return buffer.getvalue()


class FacultyFinalClearanceReportService:
    LOGO_PATH = CorrectionOfficialReportService.LOGO_PATH

    @staticmethod
    def _safe_text(value):
        return str(value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    @classmethod
    def _draw_page_background(cls, canvas, doc):
        ClassTabulationSheetPdfService._draw_page_background(canvas, doc)

    @classmethod
    def _signature_enabled_for_document(cls, *, tenant_id, document_type: str) -> bool:
        return CorrectionOfficialReportService._signature_enabled_for_document(
            tenant_id=tenant_id,
            document_type=document_type,
        )

    @classmethod
    def _signature_panel(cls, **kwargs):
        return CorrectionOfficialReportService._signature_panel(**kwargs)

    @classmethod
    def _official_period_label(cls, period_name: str, period_code: str) -> str:
        joined = f"{period_name} {period_code}".upper()
        if "PRE-FINAL" in joined or "PREFINAL" in joined or "PRE FINAL" in joined:
            return "PFG"
        if "PRELIM" in joined:
            return "PG"
        if "MIDTERM" in joined:
            return "MG"
        if "FINAL" in joined:
            return "FG"
        return (period_code or period_name or "GRADE").upper()

    @classmethod
    def evaluate_faculty_clearance(cls, *, faculty_user, term, campus=None):
        assignments = (
            faculty_user.faculty_assignments.filter(
                is_active=True,
                response_status="ACCEPTED",
                accepted_at__isnull=False,
                offering__term_id=term.id,
                offering__tenant_id=term.tenant_id,
                offering__is_active=True,
                offering__tenant__is_active=True,
                offering__campus__is_active=True,
                offering__academic_year__is_active=True,
                offering__term__is_active=True,
                offering__department__is_active=True,
                offering__program__is_active=True,
                offering__program__department__is_active=True,
                offering__course__is_active=True,
                offering__section__is_active=True,
                offering__section__department__is_active=True,
                offering__section__program__is_active=True,
                offering__section__program__department__is_active=True,
            )
            .filter(models.Q(offering__course__department__isnull=True) | models.Q(offering__course__department__is_active=True))
            .select_related(
                "offering",
                "offering__tenant",
                "offering__campus",
                "offering__academic_year",
                "offering__term",
                "offering__course",
                "offering__section",
            )
            .order_by("offering__campus__code", "offering__course__code", "offering__section__code")
        )
        if campus is not None:
            assignments = assignments.filter(offering__campus_id=campus.id)

        offerings = []
        seen_offering_ids = set()
        for assignment in assignments:
            if assignment.offering_id in seen_offering_ids:
                continue
            seen_offering_ids.add(assignment.offering_id)
            offerings.append(assignment.offering)

        rows = []
        complete_courses = 0
        incomplete_courses = 0

        for offering in offerings:
            notes = []
            eligible_student_ids = list(
                Enrollment.objects.filter(
                    course_offering_id=offering.id,
                    is_active=True,
                    enrollment_status=Enrollment.Status.ACTIVE,
                    student__is_active=True,
                    student__department__is_active=True,
                ).values_list("student_id", flat=True)
            )

            try:
                template = FacultyGradingService.resolve_template_for_offering(offering)
                template_periods = list(FacultyGradingService.get_template_periods(template))
            except Exception:
                template_periods = []
                notes.append("No active grading template assignment.")

            submission_map = {
                row.template_period_id: row
                for row in GradeSubmission.objects.filter(
                    offering_id=offering.id,
                    template_period_id__in=[period.id for period in template_periods],
                )
            }
            period_grade_counts = {
                row["template_period_id"]: row["graded_count"]
                for row in StudentPeriodGrade.objects.filter(
                    offering_id=offering.id,
                    template_period_id__in=[period.id for period in template_periods],
                    student_id__in=eligible_student_ids or [-1],
                    period_grade__gt=0,
                )
                .values("template_period_id")
                .annotate(graded_count=models.Count("id"))
            }
            final_grade_count = (
                StudentFinalGrade.objects.filter(
                    offering_id=offering.id,
                    student_id__in=eligible_student_ids or [-1],
                    final_grade__gt=0,
                ).count()
                if eligible_student_ids
                else 0
            )

            status = "COMPLETE"
            period_labels = []
            unsubmitted_labels = []
            missing_grade_labels = []
            final_grade_missing_count = 0
            final_submission_status = "-"

            if not eligible_student_ids:
                status = "INCOMPLETE"
                notes.append(
                    "No ACTIVE students are currently eligible for final-clearance completion. "
                    "Review the class master list and final submission status first."
                )

            for period in template_periods:
                period_label = cls._official_period_label(period.name or "", period.code or "")
                period_labels.append(period_label)
                submission = submission_map.get(period.id)
                if eligible_student_ids and (not submission or submission.status != GradeSubmission.Status.SUBMITTED):
                    unsubmitted_labels.append(period_label)
                if eligible_student_ids and period_grade_counts.get(period.id, 0) < len(eligible_student_ids):
                    missing_grade_labels.append(period_label)

            if template_periods:
                final_period = template_periods[-1]
                final_submission = submission_map.get(final_period.id)
                if final_submission and final_submission.status == GradeSubmission.Status.SUBMITTED:
                    final_submission_status = "Submitted"
                else:
                    final_submission_status = "Not Submitted"
                    if eligible_student_ids:
                        status = "INCOMPLETE"
                        notes.append("Final grading period is not yet submitted.")

            if eligible_student_ids and unsubmitted_labels:
                status = "INCOMPLETE"
                notes.append(f"Unsubmitted periods: {', '.join(unsubmitted_labels)}.")
            if eligible_student_ids and missing_grade_labels:
                status = "INCOMPLETE"
                notes.append(f"Missing official period grades in: {', '.join(missing_grade_labels)}.")
            if eligible_student_ids:
                final_grade_missing_count = max(len(eligible_student_ids) - final_grade_count, 0)
                if final_grade_missing_count > 0:
                    status = "INCOMPLETE"
                    notes.append(f"{final_grade_missing_count} active student(s) still have no official final grade.")

            if notes and status != "INCOMPLETE" and eligible_student_ids:
                status = "INCOMPLETE"

            if status == "COMPLETE":
                complete_courses += 1
            else:
                incomplete_courses += 1

            rows.append(
                {
                    "offering_id": offering.id,
                    "course_code": offering.course.code,
                    "course_title": offering.course.title,
                    "section_code": offering.section.code,
                    "campus_code": offering.campus.code,
                    "eligible_student_count": len(eligible_student_ids),
                    "period_labels": period_labels,
                    "final_submission_status": final_submission_status,
                    "encoding_status": status,
                    "final_grade_missing_count": final_grade_missing_count,
                    "notes": notes,
                }
            )

        overall_status = (
            FacultyFinalClearanceReport.ClearanceStatus.CLEARED
            if rows and incomplete_courses == 0
            else FacultyFinalClearanceReport.ClearanceStatus.NOT_CLEARED
        )
        if not rows:
            overall_status = FacultyFinalClearanceReport.ClearanceStatus.NOT_CLEARED

        return {
            "faculty_user": faculty_user,
            "term": term,
            "academic_year": term.academic_year,
            "campus": campus,
            "rows": rows,
            "total_assigned_courses": len(rows),
            "complete_courses": complete_courses,
            "incomplete_courses": incomplete_courses,
            "clearance_status": overall_status,
        }

    @classmethod
    def _reference_no(cls, *, faculty_user, term):
        return f"FCR-{term.code}-{faculty_user.id}-{timezone.localtime().strftime('%Y%m%d%H%M%S')}"

    @classmethod
    def _verification_code(cls, *, reference_no, faculty_user_id, term_id):
        raw = f"{reference_no}|{faculty_user_id}|{term_id}|{settings.SECRET_KEY}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16].upper()

    @classmethod
    def verification_lookup_value(cls, *, report_obj):
        base_url = (getattr(settings, "SITE_URL", "") or "").strip().rstrip("/")
        query_string = urlencode(
            {
                "lookup_reference_no": report_obj.reference_no,
                "lookup_verification_code": report_obj.verification_code,
            }
        )
        path = f"{reverse('admin_portal:faculty_final_clearance')}?{query_string}"
        if base_url:
            return f"{base_url}{path}"
        return (
            "NCBA Faculty Final Clearance Verification\n"
            f"Reference No: {report_obj.reference_no}\n"
            f"Verification Code: {report_obj.verification_code}\n"
            f"Report UUID: {report_obj.report_uuid}"
        )

    @classmethod
    def verification_qr_drawing(cls, *, report_obj, size_mm=28):
        qr = QrCodeWidget(cls.verification_lookup_value(report_obj=report_obj))
        bounds = qr.getBounds()
        width = bounds[2] - bounds[0]
        height = bounds[3] - bounds[1]
        size_points = size_mm * mm
        drawing = Drawing(
            size_points,
            size_points,
            transform=[size_points / width, 0, 0, size_points / height, 0, 0],
        )
        drawing.add(qr)
        return drawing

    @classmethod
    def generate_report_record(cls, *, faculty_user, term, campus, generated_by_user):
        snapshot = cls.evaluate_faculty_clearance(faculty_user=faculty_user, term=term, campus=campus)
        reference_no = cls._reference_no(faculty_user=faculty_user, term=term)
        verification_code = cls._verification_code(
            reference_no=reference_no,
            faculty_user_id=faculty_user.id,
            term_id=term.id,
        )
        return FacultyFinalClearanceReport.objects.create(
            tenant_id=term.tenant_id,
            campus=campus,
            academic_year=term.academic_year,
            term=term,
            faculty_user=faculty_user,
            generated_by_user=generated_by_user,
            reference_no=reference_no,
            verification_code=verification_code,
            clearance_status=snapshot["clearance_status"],
            total_assigned_courses=snapshot["total_assigned_courses"],
            complete_courses=snapshot["complete_courses"],
            incomplete_courses=snapshot["incomplete_courses"],
            snapshot_json={
                "rows": snapshot["rows"],
                "clearance_status": snapshot["clearance_status"],
            },
        )

    @classmethod
    def build_report_data(cls, *, report_obj):
        print_header_name = SystemSettingService.get(
            "PRINT_HEADER_SCHOOL_NAME",
            tenant_id=report_obj.tenant_id,
            default=report_obj.tenant.name,
        )
        print_header_address = SystemSettingService.get(
            "PRINT_HEADER_SCHOOL_ADDRESS",
            tenant_id=report_obj.tenant_id,
            default=getattr(report_obj.campus, "address", "") or "",
        )
        return {
            "report_obj": report_obj,
            "print_header_name": print_header_name,
            "print_header_address": print_header_address,
            "generated_at": timezone.localtime(report_obj.created_at),
            "reference_no": report_obj.reference_no,
            "verification_code": report_obj.verification_code,
            "rows": (report_obj.snapshot_json or {}).get("rows", []),
            "verification_lookup_value": cls.verification_lookup_value(report_obj=report_obj),
        }

    @classmethod
    def build_pdf_bytes(cls, *, report_obj):
        report = cls.build_report_data(report_obj=report_obj)
        report_obj = report["report_obj"]
        signature_usage_entries = []

        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=15 * mm,
            rightMargin=15 * mm,
            topMargin=15 * mm,
            bottomMargin=15 * mm,
            title=f"Faculty Final Clearance {report['reference_no']}",
        )
        styles = getSampleStyleSheet()
        styles.add(ParagraphStyle(name="SmallBody", fontSize=8, leading=10))
        styles.add(
            ParagraphStyle(
                name="SectionTitle",
                fontSize=10.5,
                leading=13,
                fontName="Helvetica",
                spaceBefore=4,
                spaceAfter=10,
            )
        )
        styles.add(ParagraphStyle(name="ReportHeader", fontSize=14, leading=17, alignment=1))
        styles.add(ParagraphStyle(name="CenteredBody", parent=styles["BodyText"], alignment=1))
        styles.add(ParagraphStyle(name="TableWrap", fontSize=7.5, leading=9))

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
                Paragraph("Faculty Final Clearance Report", styles["Title"]),
                Paragraph(
                    f"Generated: {report['generated_at'].strftime('%Y-%m-%d %H:%M')}",
                    styles["SmallBody"],
                ),
                Spacer(1, 16),
                Paragraph("A. FACULTY CLEARANCE OVERVIEW", styles["SectionTitle"]),
            ]
        )

        metadata_rows = [
            ["Faculty", report_obj.faculty_user.full_name or report_obj.faculty_user.username, "Campus", report_obj.campus.name],
            ["Academic Year", report_obj.academic_year.code, "Term", report_obj.term.name or report_obj.term.code],
            ["Overall Status", report_obj.get_clearance_status_display().upper(), "Generated By", report_obj.generated_by_user.full_name if report_obj.generated_by_user_id else "-"],
            ["Assigned Courses", str(report_obj.total_assigned_courses), "Complete / Incomplete", f"{report_obj.complete_courses} / {report_obj.incomplete_courses}"],
        ]
        story.append(CorrectionOfficialReportService._table([["Field", "Value", "Field", "Value"]] + metadata_rows, col_widths=[36*mm, 54*mm, 36*mm, 54*mm]))
        story.extend(
            [
                Spacer(1, 16),
                Paragraph("B. COURSE CLEARANCE STATUS", styles["SectionTitle"]),
                Paragraph(
                    "A course is marked COMPLETE only when required active-student grades are already encoded, official period grades are positive and available, required period submissions are already submitted, and official final grades are already computed with positive values.",
                    styles["SmallBody"],
                ),
                Spacer(1, 6),
            ]
        )
        course_rows = [["Course", "Section", "Eligible", "Final Submission", "Status", "Notes"]]
        for row in report["rows"]:
            course_rows.append(
                [
                    Paragraph(f"{cls._safe_text(row['course_code'])} - {cls._safe_text(row['course_title'])}", styles["TableWrap"]),
                    Paragraph(cls._safe_text(row["section_code"]), styles["TableWrap"]),
                    str(row["eligible_student_count"]),
                    Paragraph(cls._safe_text(row["final_submission_status"]), styles["TableWrap"]),
                    Paragraph(cls._safe_text(row["encoding_status"]), styles["TableWrap"]),
                    Paragraph(cls._safe_text("; ".join(row["notes"]) or "-"), styles["TableWrap"]),
                ]
            )
        if len(course_rows) == 1:
            course_rows.append(["-", "-", "-", "-", "NOT_CLEARED", "No active faculty course assignments found for the selected scope."])
        story.append(CorrectionOfficialReportService._table(course_rows, col_widths=[49*mm, 23*mm, 14*mm, 24*mm, 18*mm, 52*mm]))
        story.extend(
            [
                Spacer(1, 16),
                Paragraph("C. CONTROL AND VERIFICATION", styles["SectionTitle"]),
                Paragraph(
                    "This document is the official NCBA faculty clearance record for the selected scope and serves as the compact replacement for bulk printed final grade sheets. Authorized personnel should validate authenticity using the Reference No. and Verification Code against NCBA's stored clearance verification record.",
                    styles["SmallBody"],
                ),
                Spacer(1, 6),
            ]
        )
        control_table = CorrectionOfficialReportService._table(
            [
                ["Control", "Value"],
                ["Reference No", report["reference_no"]],
                ["Verification Code", report["verification_code"]],
                ["Report UUID", str(report_obj.report_uuid)],
            ],
            col_widths=[36 * mm, 82 * mm],
        )
        qr_caption = Paragraph(
            "Scan to open NCBA clearance verification. If no site URL is configured, the QR stores the Reference No., Verification Code, and UUID for manual verification.",
            styles["SmallBody"],
        )
        qr_block = Table(
            [
                [cls.verification_qr_drawing(report_obj=report_obj)],
                [qr_caption],
            ],
            colWidths=[48 * mm],
        )
        qr_block.setStyle(
            TableStyle(
                [
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        verification_layout = Table(
            [[control_table, qr_block]],
            colWidths=[122 * mm, 48 * mm],
        )
        verification_layout.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        story.append(verification_layout)

        if cls._signature_enabled_for_document(
            tenant_id=report_obj.tenant_id,
            document_type=UserSignatureUsageLog.DocumentType.FINAL_CLEARANCE,
        ):
            faculty_signature_panel = cls._signature_panel(
                user=report_obj.faculty_user,
                label="Faculty Signature",
                role_caption="Printed by Faculty",
                styles=styles,
                usage_collector=signature_usage_entries,
                usage_kwargs={
                    "user": report_obj.faculty_user,
                    "document_type": UserSignatureUsageLog.DocumentType.FINAL_CLEARANCE,
                    "document_reference": report["reference_no"],
                    "usage_role": "Faculty Signature",
                    "actor": report_obj.faculty_user,
                    "portal_code": "FACULTY",
                    "metadata": {
                        "faculty_final_clearance_report_id": report_obj.id,
                        "tenant_id": report_obj.tenant_id,
                        "campus_id": report_obj.campus_id,
                        "term_id": report_obj.term_id,
                    },
                },
            )
            story.extend(
                [
                    Spacer(1, 16),
                    Paragraph("D. FACULTY SIGNATURE", styles["SectionTitle"]),
                    Paragraph(
                        "When enabled by NCBA and available in the faculty account profile, the encrypted stored signature appears below for the generated clearance copy.",
                        styles["SmallBody"],
                    ),
                    Spacer(1, 6),
                    faculty_signature_panel,
                ]
            )

        doc.build(story, onFirstPage=cls._draw_page_background, onLaterPages=cls._draw_page_background)
        for usage_kwargs in signature_usage_entries:
            UserSignatureService.log_signature_usage(**usage_kwargs)
        return buffer.getvalue()

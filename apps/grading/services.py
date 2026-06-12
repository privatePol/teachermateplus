from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from collections import defaultdict
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.utils import timezone
from django.utils.dateparse import parse_date

from apps.academics.models import CourseOffering, FacultyAssignment
from apps.attendance.models import AttendanceRecord, AttendanceSession
from apps.core.services.scope import ScopeService
from apps.core.services.features import FeatureSettingsService
from apps.core.services.permissions import PermissionService
from apps.core.services.settings import SystemSettingService
from apps.core.services.audit import AuditService
from apps.enrollment.models import Enrollment
from apps.grading.models import (
    CorrectionApprovalRouteRule,
    CourseBaseValueOverride,
    CourseTemplateAssignment,
    GradeCorrectionApprovalStep,
    GradeCorrectionRequestItem,
    GradeCorrectionRequest,
    GradeCorrectionUnlockWindow,
    GradeSubmission,
    GradeSubmissionReopenRequest,
    GradeActivity,
    DetailComputationMode,
    GradingPeriodLock,
    GradingTemplate,
    GradingTemplateApprovalStep,
    GradingTemplateApprovalWorkflow,
    GradingTemplateComponent,
    GradingTemplateDetail,
    GradingTemplatePeriod,
    GradingTemplateSubcomponent,
    TemplateHotfixRequest,
    TemplateHotfixWorkflowStep,
    TenantGradingProfile,
    StudentActivityScore,
    StudentFinalGrade,
    StudentPeriodGrade,
)
from apps.rbac.models import Role, UserRole


class GradingTemplateService:
    @staticmethod
    def ensure_editable(template):
        if template.approval_status == template.ApprovalStatus.FOR_APPROVAL:
            raise ValidationError("Template is currently under approval review and cannot be edited.")

    @staticmethod
    def _sum_components(period):
        return sum((component.weight_percentage or Decimal("0")) for component in period.components.filter(is_active=True))

    @staticmethod
    def _sum_subcomponents(component):
        return sum(
            (sub.weight_percentage or Decimal("0")) for sub in component.subcomponents.filter(is_active=True)
        )

    @staticmethod
    def _sum_details(subcomponent):
        return sum(
            (detail.weight_percentage or Decimal("0")) for detail in subcomponent.details.filter(is_active=True)
        )

    @classmethod
    def validate_publishable(cls, template):
        errors = []
        periods = list(template.periods.filter(is_active=True).order_by("sequence_no"))
        if not periods:
            errors.append("Template must have at least one active period.")

        for period in periods:
            components = list(period.components.filter(is_active=True))
            if not components:
                errors.append(f"Period {period.code} has no active components.")
                continue
            comp_total = cls._sum_components(period)
            if comp_total != Decimal("100"):
                errors.append(f"Period {period.code} component total must be 100 (current {comp_total}).")
            for component in components:
                subcomponents = list(component.subcomponents.filter(is_active=True))
                if subcomponents:
                    sub_total = cls._sum_subcomponents(component)
                    if sub_total <= Decimal("0"):
                        errors.append(
                            f"Component {component.code} has subcomponents but total weight is {sub_total}. "
                            "Subcomponent total must be greater than 0."
                        )
                for subcomponent in subcomponents:
                    details = list(subcomponent.details.filter(is_active=True))
                    if details:
                        detail_total = cls._sum_details(subcomponent)
                        if detail_total <= Decimal("0"):
                            errors.append(
                                f"Subcomponent {subcomponent.code} has details but total weight is {detail_total}. "
                                "Detail total must be greater than 0."
                            )
        return errors

    @classmethod
    def publish(cls, *, template, actor):
        approval_required = TemplateGovernanceWorkflowService.require_approval_before_publish(
            tenant_id=template.tenant_id
        )
        if approval_required and template.approval_status != template.ApprovalStatus.APPROVED:
            raise ValidationError("Template must be approved before publishing.")
        if not approval_required and template.approval_status == template.ApprovalStatus.FOR_APPROVAL:
            raise ValidationError("Template is currently under approval review and cannot be published yet.")
        errors = cls.validate_publishable(template)
        if errors:
            raise ValidationError(errors)
        if not approval_required and template.approval_status != template.ApprovalStatus.APPROVED:
            template.approval_status = template.ApprovalStatus.APPROVED
            template.approval_reviewed_by = actor
            template.approval_reviewed_at = timezone.now()
        template.is_published = True
        template.published_at = timezone.now()
        template.published_by = actor
        update_fields = ["is_published", "published_at", "published_by", "updated_at"]
        if not approval_required:
            update_fields.extend(["approval_status", "approval_reviewed_by", "approval_reviewed_at"])
        template.save(update_fields=update_fields)
        return template

    @classmethod
    def submit_for_approval(cls, *, template, actor, remarks: str | None = None):
        errors = cls.validate_publishable(template)
        if errors:
            raise ValidationError(errors)
        if template.approval_status == template.ApprovalStatus.FOR_APPROVAL:
            raise ValidationError("Template is already submitted for approval.")

        template.approval_status = template.ApprovalStatus.FOR_APPROVAL
        template.approval_requested_by = actor
        template.approval_requested_at = timezone.now()
        template.approval_reviewed_by = None
        template.approval_reviewed_at = None
        template.approval_remarks = (remarks or "").strip() or None
        template.save(
            update_fields=[
                "approval_status",
                "approval_requested_by",
                "approval_requested_at",
                "approval_reviewed_by",
                "approval_reviewed_at",
                "approval_remarks",
                "updated_at",
            ]
        )
        TemplateGovernanceWorkflowService.create_approval_workflow(template=template, actor=actor)
        return template

    @classmethod
    def review_approval(cls, *, template, actor, approve: bool, remarks: str | None = None):
        if template.approval_status != template.ApprovalStatus.FOR_APPROVAL:
            raise ValidationError("Only templates in FOR_APPROVAL status can be reviewed.")
        workflow = TemplateGovernanceWorkflowService.get_pending_approval_workflow(template=template)
        if not workflow:
            raise ValidationError("No active approval workflow was found for this template.")
        current_step = workflow.steps.filter(status=GradingTemplateApprovalStep.Status.PENDING).order_by("step_no").first()
        if not current_step:
            raise ValidationError("No pending approval step is available for this template.")

        current_step.acted_by_user = actor
        current_step.acted_at = timezone.now()
        current_step.remarks = (remarks or "").strip() or None

        if not approve:
            current_step.status = GradingTemplateApprovalStep.Status.REJECTED
            current_step.save(update_fields=["acted_by_user", "acted_at", "remarks", "status", "updated_at"])
            workflow.steps.filter(
                step_no__gt=current_step.step_no,
                status__in=[GradingTemplateApprovalStep.Status.QUEUED, GradingTemplateApprovalStep.Status.PENDING],
            ).update(status=GradingTemplateApprovalStep.Status.SKIPPED)
            workflow.status = GradingTemplateApprovalWorkflow.Status.REJECTED
            workflow.completed_at = timezone.now()
            workflow.current_step_no = current_step.step_no
            workflow.save(update_fields=["status", "completed_at", "current_step_no", "updated_at"])
            template.approval_status = template.ApprovalStatus.REJECTED
            template.approval_reviewed_by = actor
            template.approval_reviewed_at = timezone.now()
            template.approval_remarks = current_step.remarks
            template.save(
                update_fields=[
                    "approval_status",
                    "approval_reviewed_by",
                    "approval_reviewed_at",
                    "approval_remarks",
                    "updated_at",
                ]
            )
            return template

        if current_step.step_code in {
            TemplateGovernanceWorkflowService.STEP_TEMPLATE_APPROVAL,
            TemplateGovernanceWorkflowService.STAGE_APPROVAL_REVIEW,
        }:
            errors = cls.validate_publishable(template)
            if errors:
                raise ValidationError(errors)

        current_step.status = GradingTemplateApprovalStep.Status.APPROVED
        current_step.save(update_fields=["acted_by_user", "acted_at", "remarks", "status", "updated_at"])

        next_step = workflow.steps.filter(
            step_no__gt=current_step.step_no,
            status=GradingTemplateApprovalStep.Status.QUEUED,
        ).order_by("step_no").first()
        if next_step:
            next_step.status = GradingTemplateApprovalStep.Status.PENDING
            next_step.save(update_fields=["status", "updated_at"])
            workflow.current_step_no = next_step.step_no
            workflow.save(update_fields=["current_step_no", "updated_at"])
            template.approval_remarks = current_step.remarks
            template.save(update_fields=["approval_remarks", "updated_at"])
            return template

        workflow.status = GradingTemplateApprovalWorkflow.Status.APPROVED
        workflow.completed_at = timezone.now()
        workflow.current_step_no = current_step.step_no
        workflow.save(update_fields=["status", "completed_at", "current_step_no", "updated_at"])
        template.approval_status = template.ApprovalStatus.APPROVED
        template.approval_reviewed_by = actor
        template.approval_reviewed_at = timezone.now()
        template.approval_remarks = current_step.remarks
        template.save(
            update_fields=[
                "approval_status",
                "approval_reviewed_by",
                "approval_reviewed_at",
                "approval_remarks",
                "updated_at",
            ]
        )
        return template


class GradingTemplateTestingCalculatorService:
    DEFAULT_SAMPLE_VALUE = Decimal("85.00")

    @classmethod
    def prefetch_templates(cls, queryset):
        return queryset.prefetch_related(
            Prefetch(
                "periods",
                queryset=GradingTemplatePeriod.objects.filter(is_active=True)
                .order_by("sequence_no", "id")
                .prefetch_related(
                    Prefetch(
                        "components",
                        queryset=GradingTemplateComponent.objects.filter(is_active=True)
                        .order_by("sort_order", "id")
                        .prefetch_related(
                            Prefetch(
                                "subcomponents",
                                queryset=GradingTemplateSubcomponent.objects.filter(is_active=True)
                                .order_by("sort_order", "id")
                                .prefetch_related(
                                    Prefetch(
                                        "details",
                                        queryset=GradingTemplateDetail.objects.filter(is_active=True).order_by(
                                            "sort_order", "id"
                                        ),
                                    )
                                ),
                            )
                        ),
                    )
                ),
            )
        )

    @staticmethod
    def _round(value: Decimal) -> Decimal:
        return FacultyGradingService._round(value)

    @staticmethod
    def _to_decimal(value) -> Decimal:
        if value is None or value == "":
            return Decimal("0")
        return Decimal(str(value))

    @classmethod
    def _leaf_key(
        cls,
        *,
        component: GradingTemplateComponent,
        subcomponent: GradingTemplateSubcomponent | None = None,
        detail: GradingTemplateDetail | None = None,
    ) -> str:
        if detail:
            return f"detail_{detail.id}"
        if subcomponent:
            return f"subcomponent_{subcomponent.id}"
        return f"component_{component.id}"

    @classmethod
    def _leaf_label(
        cls,
        *,
        component: GradingTemplateComponent,
        subcomponent: GradingTemplateSubcomponent | None = None,
        detail: GradingTemplateDetail | None = None,
    ) -> str:
        if detail:
            return detail.name or detail.code
        if subcomponent:
            return subcomponent.name or subcomponent.code
        return component.name or component.code

    @classmethod
    def _leaf_level_label(
        cls,
        *,
        subcomponent: GradingTemplateSubcomponent | None = None,
        detail: GradingTemplateDetail | None = None,
    ) -> str:
        if detail:
            return "Detail"
        if subcomponent:
            return "Subcomponent"
        return "Component"

    @classmethod
    def _parse_decimal_or_default(cls, raw_value, *, default_value: Decimal, minimum: Decimal | None = None):
        if raw_value in {None, ""}:
            return default_value, "", None
        try:
            value = Decimal(str(raw_value))
        except (InvalidOperation, TypeError, ValueError):
            return default_value, str(raw_value), "Enter a valid number."
        if minimum is not None and value < minimum:
            return default_value, str(raw_value), f"Value must be {minimum:.2f} or higher."
        return cls._round(value), str(raw_value), None

    @classmethod
    def _resolve_leaf_input(
        cls,
        *,
        raw_inputs: dict | None,
        default_sample: Decimal,
        input_key: str,
        component: GradingTemplateComponent,
        subcomponent: GradingTemplateSubcomponent | None = None,
        detail: GradingTemplateDetail | None = None,
        base_value: Decimal,
        entry_method: str,
        local_weight: Decimal,
        effective_weight: Decimal,
        effective_weight_formula: str,
    ):
        score_input_mode = FacultyGradingService.resolve_score_input_mode(
            template_component=component,
            template_subcomponent=subcomponent,
            template_detail=detail,
        )
        raw_name = f"{input_key}_raw"
        total_name = f"{input_key}_total"
        raw_value = None if raw_inputs is None else raw_inputs.get(raw_name)
        total_value = None if raw_inputs is None else raw_inputs.get(total_name)
        entered_score, display_raw_value, raw_error = cls._parse_decimal_or_default(
            raw_value,
            default_value=default_sample,
            minimum=Decimal("0"),
        )
        total_score = Decimal("100")
        display_total_value = "100.00"
        total_error = None
        if score_input_mode != "DIRECT_PERCENTAGE":
            total_score, display_total_value, total_error = cls._parse_decimal_or_default(
                total_value,
                default_value=Decimal("100.00"),
                minimum=Decimal("0.01"),
            )
        else:
            if entered_score > Decimal("100"):
                raw_error = "Direct percentage score must stay between 0.00 and 100.00."
                entered_score = default_sample
                display_raw_value = str(raw_value or "")
        computed_score = entered_score
        formula = f"Direct percentage input = {entered_score:.2f}"
        score_error = raw_error or total_error
        if score_input_mode != "DIRECT_PERCENTAGE":
            try:
                computed_score = FacultyGradingService.compute_activity_score(
                    raw_score=entered_score,
                    total_score=total_score,
                    base_value=base_value,
                    score_input_mode=score_input_mode,
                )
            except ValidationError as exc:
                score_error = str(exc)
                entered_score = default_sample
                total_score = Decimal("100.00")
                computed_score = FacultyGradingService.compute_activity_score(
                    raw_score=entered_score,
                    total_score=total_score,
                    base_value=base_value,
                    score_input_mode=score_input_mode,
                )
            formula = (
                f"(({entered_score:.2f} / {total_score:.2f}) x {base_value:.2f}) + "
                f"(100 - {base_value:.2f}) = {computed_score:.2f}"
            )
        row = {
            "input_key": input_key,
            "raw_name": raw_name,
            "total_name": total_name,
            "entry_method": entry_method,
            "score_input_mode": score_input_mode,
            "level": cls._leaf_level_label(subcomponent=subcomponent, detail=detail),
            "label": cls._leaf_label(component=component, subcomponent=subcomponent, detail=detail),
            "component_name": component.name or component.code,
            "component_code": component.code,
            "subcomponent_name": (subcomponent.name or subcomponent.code) if subcomponent else None,
            "subcomponent_code": subcomponent.code if subcomponent else None,
            "detail_name": (detail.name or detail.code) if detail else None,
            "detail_code": detail.code if detail else None,
            "raw_score": entered_score,
            "display_raw_value": display_raw_value if display_raw_value != "" else f"{entered_score:.2f}",
            "total_score": total_score,
            "display_total_value": display_total_value if display_total_value != "" else f"{total_score:.2f}",
            "computed_score": computed_score,
            "formula": formula,
            "error": score_error,
            "uses_total_score": score_input_mode != "DIRECT_PERCENTAGE",
            "weight_percentage": local_weight,
            "effective_weight_percentage": cls._round(effective_weight),
            "effective_weight_formula": effective_weight_formula,
        }
        return computed_score, row

    @classmethod
    def _resolve_profile_for_template_preview(cls, template: GradingTemplate):
        profiles = list(
            TenantGradingProfile.objects.filter(
                tenant_id=template.tenant_id,
                grading_template_id=template.id,
                is_active=True,
            )
        )
        if not profiles:
            return None

        profiles.sort(
            key=lambda profile: (
                profile.priority,
                0 if profile.is_default else 1,
                -profile.id,
            )
        )
        return profiles[0]

    @classmethod
    def _build_final_grade_strategy_for_template_preview(cls, *, template: GradingTemplate, active_periods: list):
        profile = cls._resolve_profile_for_template_preview(template)
        default_entries = [
            {
                "period_id": period.id,
                "period_code": period.code,
                "period_name": period.name,
                "weight": None,
            }
            for period in active_periods
        ]
        default_strategy = {
            "mode": TenantGradingProfile.FinalGradeFormulaMode.AVERAGE_ACTIVE_PERIODS,
            "mode_label": TenantGradingProfile.FinalGradeFormulaMode.AVERAGE_ACTIVE_PERIODS.label,
            "source": "tenant_grading_profile" if profile else "template_active_periods",
            "source_label": (
                "Tenant grading profile active-period average"
                if profile
                else "Template active-period average fallback"
            ),
            "profile": profile,
            "profile_id": profile.id if profile else None,
            "entries": default_entries,
            "formula_label": (
                "FG = ("
                + " + ".join(period.code for period in active_periods)
                + f") / {len(active_periods)}"
                if active_periods
                else "No active grading periods configured."
            ),
        }
        if not profile:
            return default_strategy

        if (
            profile.final_grade_formula_mode != TenantGradingProfile.FinalGradeFormulaMode.WEIGHTED_PERIODS
            or not profile.final_grade_formula_json
        ):
            return default_strategy

        weight_rows = (profile.final_grade_formula_json or {}).get("period_weights") or []
        weights_by_code = {}
        for row in weight_rows:
            code = (str(row.get("period_code") or "").strip()).upper()
            if not code:
                continue
            try:
                weight = cls._round(Decimal(str(row.get("weight") or "0")))
            except Exception:
                continue
            if weight <= 0:
                continue
            weights_by_code[code] = weight

        weighted_entries = []
        for period in active_periods:
            weight = weights_by_code.get((period.code or "").strip().upper())
            if weight is None:
                continue
            weighted_entries.append(
                {
                    "period_id": period.id,
                    "period_code": period.code,
                    "period_name": period.name,
                    "weight": weight,
                }
            )

        if not weighted_entries:
            default_strategy["warnings"] = [
                "The matched tenant grading profile has weighted periods, but none matched this template's active period codes."
            ]
            return default_strategy

        return {
            "mode": TenantGradingProfile.FinalGradeFormulaMode.WEIGHTED_PERIODS,
            "mode_label": TenantGradingProfile.FinalGradeFormulaMode.WEIGHTED_PERIODS.label,
            "source": "tenant_grading_profile",
            "source_label": "Tenant grading profile weighted-period formula",
            "profile": profile,
            "profile_id": profile.id,
            "entries": weighted_entries,
            "formula_label": "FG = "
            + " + ".join(
                f"({entry['period_code']} x {entry['weight']:.2f}%)"
                for entry in weighted_entries
            ),
        }

    @classmethod
    def _build_final_grade_detail(
        cls,
        *,
        template: GradingTemplate,
        active_periods: list,
        period_rows: list,
        offering: CourseOffering | None = None,
    ):
        if offering:
            strategy = FacultyGradingService.resolve_final_grade_strategy(offering, template=template)
            profile = (
                TenantGradingProfile.objects.filter(id=strategy.get("profile_id")).first()
                if strategy.get("profile_id")
                else None
            )
            strategy["profile"] = profile
            strategy["mode_label"] = TenantGradingProfile.FinalGradeFormulaMode(strategy["mode"]).label
        else:
            strategy = cls._build_final_grade_strategy_for_template_preview(
                template=template,
                active_periods=active_periods,
            )
        period_values_by_id = {
            row["row"].id: row["period_grade"]
            for row in period_rows
            if row.get("period_grade") is not None
        }
        entries = []
        warnings = list(strategy.get("warnings") or [])
        raw_value = None
        formula = "No period grade available yet."

        if not strategy["entries"]:
            warnings.append("No active grading periods are configured for final-grade computation.")
            return {
                "strategy": strategy,
                "entries": [],
                "raw_value": None,
                "official_value": None,
                "formula": strategy["formula_label"],
                "warnings": warnings,
                "profile": strategy.get("profile"),
            }

        if strategy["mode"] == TenantGradingProfile.FinalGradeFormulaMode.WEIGHTED_PERIODS:
            weighted_total = Decimal("0")
            formula_parts = []
            for entry in strategy["entries"]:
                raw_period_value = period_values_by_id.get(entry["period_id"])
                missing = raw_period_value is None
                period_value = Decimal(raw_period_value or Decimal("0"))
                contribution = cls._round(period_value * (Decimal(entry["weight"]) / Decimal("100")))
                weighted_total += contribution
                if missing:
                    warnings.append(f"{entry['period_code']} has no computed period grade and was included as 0.")
                formula_parts.append(
                    f"({period_value:.2f} x {Decimal(entry['weight']):.2f}%)"
                )
                entries.append(
                    {
                        **entry,
                        "period_grade": raw_period_value,
                        "value_used": period_value,
                        "missing": missing,
                        "contribution": contribution,
                    }
                )
            raw_value = cls._round(weighted_total)
            formula = " + ".join(formula_parts) + f" = {raw_value:.2f}"
        else:
            total_value = Decimal("0")
            formula_parts = []
            for entry in strategy["entries"]:
                raw_period_value = period_values_by_id.get(entry["period_id"])
                missing = raw_period_value is None
                period_value = Decimal(raw_period_value or Decimal("0"))
                total_value += period_value
                if missing:
                    warnings.append(
                        f"{entry['period_code']} has no computed period grade and was included as 0 in the divisor."
                    )
                formula_parts.append(f"{period_value:.2f}")
                entries.append(
                    {
                        **entry,
                        "period_grade": raw_period_value,
                        "value_used": period_value,
                        "missing": missing,
                        "contribution": cls._round(period_value / Decimal(len(strategy["entries"]))),
                    }
                )
            raw_value = cls._round(total_value / Decimal(len(strategy["entries"])))
            formula = "(" + " + ".join(formula_parts) + f") / {len(strategy['entries'])} = {raw_value:.2f}"

        return {
            "strategy": strategy,
            "entries": entries,
            "raw_value": raw_value,
            "official_value": FacultyGradingService._round_official_grade(raw_value),
            "formula": formula,
            "warnings": warnings,
            "profile": strategy.get("profile"),
        }

    @classmethod
    def build_calculation(
        cls,
        *,
        template: GradingTemplate,
        raw_inputs: dict | None = None,
        default_sample: Decimal | None = None,
        offering: CourseOffering | None = None,
    ):
        default_sample = cls._round(default_sample or cls.DEFAULT_SAMPLE_VALUE)
        period_rows = []
        input_errors = []
        active_periods = list(template.periods.all())
        base_value = (
            cls._to_decimal(FacultyGradingService.resolve_base_value(offering, template))
            if offering
            else cls._to_decimal(template.default_base_value or Decimal("50.00"))
        )

        for period in active_periods:
            component_rows = []
            period_input_rows = []
            class_standing = Decimal("0")
            exam_grade = None
            weighted_period_grade = Decimal("0")
            has_exam_component = False
            has_exam_data = False

            components = list(period.components.all())
            for component in components:
                component_weight = cls._to_decimal(component.weight_percentage)
                component_entry_method = FacultyGradingService.score_input_mode_label(
                    FacultyGradingService.resolve_score_input_mode(template_component=component)
                )
                subcomponents = list(component.subcomponents.all())
                component_input_rows = []

                if subcomponents:
                    sub_weight_total = sum(cls._to_decimal(sub.weight_percentage) for sub in subcomponents)
                    sub_denominator = sub_weight_total if sub_weight_total > 0 else Decimal("100")
                    component_raw = Decimal("0")
                    subcomponent_rows = []

                    for subcomponent in subcomponents:
                        sub_weight = cls._to_decimal(subcomponent.weight_percentage)
                        sub_entry_method = FacultyGradingService.score_input_mode_label(
                            FacultyGradingService.resolve_score_input_mode(
                                template_component=component,
                                template_subcomponent=subcomponent,
                            )
                        )
                        details = list(subcomponent.details.all())
                        sub_input_rows = []

                        if details:
                            detail_weight_total = sum(cls._to_decimal(detail.weight_percentage) for detail in details)
                            detail_denominator = detail_weight_total if detail_weight_total > 0 else Decimal("100")
                            detail_rows = []
                            detail_scores = []
                            for detail in details:
                                input_key = cls._leaf_key(
                                    component=component,
                                    subcomponent=subcomponent,
                                    detail=detail,
                                )
                                detail_value, detail_input_row = cls._resolve_leaf_input(
                                    raw_inputs=raw_inputs,
                                    default_sample=default_sample,
                                    input_key=input_key,
                                    component=component,
                                    subcomponent=subcomponent,
                                    detail=detail,
                                    base_value=base_value,
                                    entry_method=FacultyGradingService.score_input_mode_label(
                                        FacultyGradingService.resolve_score_input_mode(
                                            template_component=component,
                                            template_subcomponent=subcomponent,
                                            template_detail=detail,
                                        )
                                    ),
                                    local_weight=cls._to_decimal(detail.weight_percentage),
                                    effective_weight=(
                                        (component_weight / Decimal("100"))
                                        * (sub_weight / sub_denominator)
                                        * cls._to_decimal(detail.weight_percentage)
                                        / detail_denominator
                                        * Decimal("100")
                                    ),
                                    effective_weight_formula=(
                                        f"{component_weight}% x ({sub_weight}% / {sub_denominator}%) x "
                                        f"({cls._to_decimal(detail.weight_percentage)}% / {detail_denominator}%)"
                                    ),
                                )
                                component_input_rows.append(detail_input_row)
                                sub_input_rows.append(detail_input_row)
                                period_input_rows.append(detail_input_row)
                                if detail_input_row["error"]:
                                    input_errors.append(detail_input_row["error"])
                                detail_weight = cls._to_decimal(detail.weight_percentage)
                                detail_scores.append((detail_weight, detail_value))
                                if subcomponent.detail_computation_mode == DetailComputationMode.AVERAGE_ACTIVITIES:
                                    contribution = detail_value / Decimal(len(details))
                                    formula = f"{detail_value:.2f} / {len(details)} sample detail inputs"
                                else:
                                    contribution = (detail_weight / detail_denominator) * detail_value
                                    formula = f"({detail.weight_percentage}% / {detail_denominator}%) x {detail_value:.2f}"
                                detail_rows.append(
                                    {
                                        "row": detail,
                                        "input": detail_input_row,
                                        "score": detail_value,
                                        "weight": detail_weight,
                                        "contribution": cls._round(contribution),
                                        "formula": formula,
                                    }
                                )

                            sub_score = FacultyGradingService.aggregate_detail_scores(
                                subcomponent=subcomponent,
                                detail_scores=detail_scores,
                            )
                            subcomponent_rows.append(
                                {
                                    "row": subcomponent,
                                    "weight": sub_weight,
                                    "entry_method": sub_entry_method,
                                    "input_rows": sub_input_rows,
                                    "details": detail_rows,
                                    "sub_score": sub_score,
                                    "detail_computation_mode": subcomponent.detail_computation_mode,
                                    "formula": (
                                        " + ".join(detail_row["formula"] for detail_row in detail_rows)
                                        if detail_rows
                                        else "-"
                                    ),
                                }
                            )
                        else:
                            input_key = cls._leaf_key(component=component, subcomponent=subcomponent)
                            sub_score, sub_input_row = cls._resolve_leaf_input(
                                raw_inputs=raw_inputs,
                                default_sample=default_sample,
                                input_key=input_key,
                                component=component,
                                subcomponent=subcomponent,
                                base_value=base_value,
                                entry_method=sub_entry_method,
                                local_weight=sub_weight,
                                effective_weight=(component_weight / Decimal("100")) * sub_weight,
                                effective_weight_formula=f"{component_weight}% x ({sub_weight}% / {sub_denominator}%)",
                            )
                            component_input_rows.append(sub_input_row)
                            sub_input_rows.append(sub_input_row)
                            period_input_rows.append(sub_input_row)
                            if sub_input_row["error"]:
                                input_errors.append(sub_input_row["error"])
                            subcomponent_rows.append(
                                {
                                    "row": subcomponent,
                                    "weight": sub_weight,
                                    "entry_method": sub_entry_method,
                                    "input_rows": sub_input_rows,
                                    "details": [],
                                    "sub_score": sub_score,
                                    "formula": f"Input {sub_score:.2f}",
                                }
                            )

                        component_raw += (sub_weight / sub_denominator) * sub_score

                    component_score = cls._round(component_raw)
                    component_formula = " + ".join(
                        f"({row['weight']}% / {sub_denominator}%) x {row['sub_score']:.2f}"
                        for row in subcomponent_rows
                    )
                else:
                    input_key = cls._leaf_key(component=component)
                    component_score, component_input_row = cls._resolve_leaf_input(
                        raw_inputs=raw_inputs,
                        default_sample=default_sample,
                        input_key=input_key,
                        component=component,
                        base_value=base_value,
                        entry_method=component_entry_method,
                        local_weight=component_weight,
                        effective_weight=component_weight,
                        effective_weight_formula=f"{component_weight}% of the period",
                    )
                    component_input_rows.append(component_input_row)
                    period_input_rows.append(component_input_row)
                    if component_input_row["error"]:
                        input_errors.append(component_input_row["error"])
                    subcomponent_rows = []
                    component_formula = f"Input {component_score:.2f}"

                weighted_contribution = cls._round((component_weight / Decimal("100")) * component_score)
                component_rows.append(
                    {
                        "row": component,
                        "weight": component_weight,
                        "entry_method": component_entry_method,
                        "component_score": component_score,
                        "weighted_contribution": weighted_contribution,
                        "formula": component_formula,
                        "subcomponents": subcomponent_rows,
                        "input_rows": component_input_rows,
                    }
                )

                if FacultyGradingService.is_exam_component(component):
                    has_exam_component = True
                    exam_grade = (exam_grade or Decimal("0")) + component_score
                    has_exam_data = True
                else:
                    class_standing += component_score
                weighted_period_grade += (component_weight / Decimal("100")) * component_score

            class_standing = cls._round(class_standing)
            exam_grade = cls._round(exam_grade) if exam_grade is not None else None
            period_grade = cls._round(weighted_period_grade) if (not has_exam_component or has_exam_data) else None

            period_rows.append(
                {
                    "row": period,
                    "components": component_rows,
                    "class_standing": class_standing,
                    "exam_grade": exam_grade,
                    "period_grade": period_grade,
                    "input_rows": period_input_rows,
                    "period_formula": " + ".join(
                        f"({component_row['weight']}% / 100%) x {component_row['component_score']:.2f}"
                        for component_row in component_rows
                    ),
                }
            )

        final_grade_detail = cls._build_final_grade_detail(
            template=template,
            active_periods=active_periods,
            period_rows=period_rows,
            offering=offering,
        )
        final_grade = final_grade_detail["raw_value"]
        final_formula = final_grade_detail["formula"]
        final_strategy = final_grade_detail["strategy"]
        final_profile = final_grade_detail.get("profile")
        final_meta = (
            f"{final_profile.profile_code}: {final_profile.profile_name}"
            if final_profile
            else "No active tenant grading profile matched this template, so the active-period average fallback is shown."
        )

        return {
            "period_rows": period_rows,
            "default_sample": default_sample,
            "base_value": base_value,
            "final_grade": final_grade,
            "final_formula": final_formula,
            "final_grade_detail": final_grade_detail,
            "metric_cards": [
                {
                    "label": "Active Periods",
                    "value": len(active_periods),
                    "meta": "Periods that will be included in the testing breakdown.",
                },
                {
                    "label": "Weighted Components",
                    "value": sum(len(period_row["components"]) for period_row in period_rows),
                    "meta": "Active component rows used by the selected template.",
                },
                {
                    "label": "Raw-Score Input Rows",
                    "value": sum(len(period_row["input_rows"]) for period_row in period_rows),
                    "meta": "Lowest-level rows where the calculator accepts raw score and total score inputs.",
                },
                {
                    "label": "Base Value",
                    "value": f"{base_value:.2f}",
                    "meta": "Used when TeacherMate+ converts raw score to computed percentage for Base-50 items.",
                },
                {
                    "label": "Final Grade",
                    "value": f"{final_grade:.2f}" if final_grade is not None else "-",
                    "meta": f"{final_strategy['mode_label']} | {final_meta}",
                },
            ],
            "input_errors": input_errors,
        }


class TemplateHotfixService:
    @staticmethod
    def involved_personalities():
        return [
            {"role": "FACULTY", "responsibility": "Raise grading impact concerns to admin."},
            {"role": "DEAN", "responsibility": "Academic policy approver for template hotfixes."},
            {"role": "REGISTRAR", "responsibility": "Records governance approver and compliance check."},
            {"role": "CAMPUS_ADMIN", "responsibility": "Campus operations approver and execution monitor."},
            {"role": "SUPER_ADMIN", "responsibility": "Cross-campus oversight and emergency override."},
        ]

    @classmethod
    def _offering_has_submitted_grades(cls, offering):
        return GradeSubmission.objects.filter(
            offering_id=offering.id,
            status__in=[
                GradeSubmission.Status.SUBMITTED,
                GradeSubmission.Status.REOPENED,
            ],
        ).exists()

    @classmethod
    def _candidate_offerings_for_template(cls, template):
        candidate_qs = (
            CourseOffering.objects.filter(tenant_id=template.tenant_id, is_active=True)
            .filter(
                tenant__is_active=True,
                campus__is_active=True,
                academic_year__is_active=True,
                term__is_active=True,
                department__is_active=True,
                program__is_active=True,
                program__department__is_active=True,
                course__is_active=True,
                section__is_active=True,
                section__department__is_active=True,
                section__program__is_active=True,
                section__program__department__is_active=True,
            )
            .filter(Q(course__department__isnull=True) | Q(course__department__is_active=True))
            .select_related("term", "academic_year", "course", "section", "campus")
            .order_by("-created_at")
        )
        matched = []
        for offering in candidate_qs:
            try:
                resolved = FacultyGradingService.resolve_template_for_offering(offering)
            except ValidationError:
                continue
            if resolved.id == template.id:
                matched.append(offering)
        return matched

    @classmethod
    def _resolve_target_offerings(cls, hotfix_request: TemplateHotfixRequest):
        offerings = cls._candidate_offerings_for_template(hotfix_request.template)
        today = timezone.localdate()

        if hotfix_request.apply_mode == TemplateHotfixRequest.ApplyMode.FUTURE_ONLY:
            future = []
            for offering in offerings:
                term_start = offering.term.start_date
                ay_start = offering.academic_year.start_date
                if (term_start and term_start > today) or (not term_start and ay_start and ay_start > today):
                    future.append(offering)
            return future

        if hotfix_request.apply_mode == TemplateHotfixRequest.ApplyMode.ACTIVE_NOT_SUBMITTED:
            return [
                offering
                for offering in offerings
                if offering.status == offering.Status.OPEN and not cls._offering_has_submitted_grades(offering)
            ]

        if hotfix_request.apply_mode == TemplateHotfixRequest.ApplyMode.SELECTED_OFFERINGS:
            selected_ids = hotfix_request.selected_offering_ids_json or []
            selected_ids = {int(x) for x in selected_ids if str(x).isdigit()}
            return [offering for offering in offerings if offering.id in selected_ids]

        if hotfix_request.apply_mode == TemplateHotfixRequest.ApplyMode.REQUESTING_FACULTY_OFFERINGS:
            faculty_offering_ids = set(
                FacultyAssignment.objects.filter(
                    faculty_user_id=hotfix_request.requested_by_user_id,
                    is_active=True,
                    response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
                    accepted_at__isnull=False,
                    offering__status=CourseOffering.Status.OPEN,
                    offering__is_active=True,
                ).values_list("offering_id", flat=True)
            )
            return [offering for offering in offerings if offering.id in faculty_offering_ids]

        return []

    @classmethod
    @transaction.atomic
    def create_request(
        cls,
        *,
        template,
        requested_by,
        apply_mode: str,
        justification: str,
        selected_offering_ids: list[int] | None = None,
    ):
        if not template.is_published:
            raise ValidationError("Hotfix request requires a published template.")
        if apply_mode == TemplateHotfixRequest.ApplyMode.SELECTED_OFFERINGS and not selected_offering_ids:
            raise ValidationError("Selected offerings are required for SELECTED_OFFERINGS mode.")

        hotfix_request = TemplateHotfixRequest.objects.create(
            tenant_id=template.tenant_id,
            template=template,
            apply_mode=apply_mode,
            status=TemplateHotfixRequest.Status.PENDING,
            justification=(justification or "").strip(),
            selected_offering_ids_json=selected_offering_ids or None,
            requested_by_user=requested_by,
        )
        TemplateGovernanceWorkflowService.create_hotfix_workflow_steps(hotfix_request=hotfix_request)
        return hotfix_request

    @classmethod
    @transaction.atomic
    def review_and_apply(
        cls,
        *,
        hotfix_request: TemplateHotfixRequest,
        reviewer,
        approve: bool,
        review_remarks: str | None = None,
    ):
        if hotfix_request.status != TemplateHotfixRequest.Status.PENDING:
            raise ValidationError("Only pending hotfix requests can be reviewed.")
        current_step = TemplateGovernanceWorkflowService.get_current_hotfix_step(hotfix_request=hotfix_request)
        if not current_step:
            raise ValidationError("No pending hotfix workflow step is available for this request.")

        current_step.acted_by_user = reviewer
        current_step.acted_at = timezone.now()
        current_step.remarks = (review_remarks or "").strip() or None

        hotfix_request.reviewed_by_user = reviewer
        hotfix_request.reviewed_at = timezone.now()
        hotfix_request.review_remarks = current_step.remarks

        if not approve:
            current_step.status = TemplateHotfixWorkflowStep.Status.REJECTED
            current_step.save(update_fields=["acted_by_user", "acted_at", "remarks", "status", "updated_at"])
            hotfix_request.workflow_steps.filter(
                step_no__gt=current_step.step_no,
                status__in=[TemplateHotfixWorkflowStep.Status.QUEUED, TemplateHotfixWorkflowStep.Status.PENDING],
            ).update(status=TemplateHotfixWorkflowStep.Status.SKIPPED)
            hotfix_request.status = TemplateHotfixRequest.Status.REJECTED
            hotfix_request.save(
                update_fields=[
                    "reviewed_by_user",
                    "reviewed_at",
                    "review_remarks",
                    "status",
                    "updated_at",
                ]
            )
            return hotfix_request

        current_step.status = TemplateHotfixWorkflowStep.Status.APPROVED
        current_step.save(update_fields=["acted_by_user", "acted_at", "remarks", "status", "updated_at"])

        next_step = hotfix_request.workflow_steps.filter(
            step_no__gt=current_step.step_no,
            status=TemplateHotfixWorkflowStep.Status.QUEUED,
        ).order_by("step_no").first()
        if next_step:
            next_step.status = TemplateHotfixWorkflowStep.Status.PENDING
            next_step.save(update_fields=["status", "updated_at"])
            hotfix_request.save(
                update_fields=[
                    "reviewed_by_user",
                    "reviewed_at",
                    "review_remarks",
                    "updated_at",
                ]
            )
            return hotfix_request

        hotfix_request.status = TemplateHotfixRequest.Status.APPROVED
        hotfix_request.save(
            update_fields=[
                "reviewed_by_user",
                "reviewed_at",
                "review_remarks",
                "status",
                "updated_at",
            ]
        )

        target_offerings = cls._resolve_target_offerings(hotfix_request)
        recomputed = 0
        skipped = []
        processed = []

        if hotfix_request.apply_mode == TemplateHotfixRequest.ApplyMode.FUTURE_ONLY:
            hotfix_request.status = TemplateHotfixRequest.Status.APPLIED
            hotfix_request.applied_by_user = reviewer
            hotfix_request.applied_at = timezone.now()
            hotfix_request.affected_offering_count = len(target_offerings)
            hotfix_request.recomputed_offering_count = 0
            hotfix_request.impact_snapshot_json = {
                "mode": hotfix_request.apply_mode,
                "note": "No immediate recomputation. Hotfix applies to future offerings.",
                "offering_ids": [o.id for o in target_offerings],
            }
            hotfix_request.save(
                update_fields=[
                    "status",
                    "applied_by_user",
                    "applied_at",
                    "affected_offering_count",
                    "recomputed_offering_count",
                    "impact_snapshot_json",
                    "updated_at",
                ]
            )
            return hotfix_request

        for offering in target_offerings:
            if (
                hotfix_request.apply_mode
                in {
                    TemplateHotfixRequest.ApplyMode.SELECTED_OFFERINGS,
                    TemplateHotfixRequest.ApplyMode.REQUESTING_FACULTY_OFFERINGS,
                }
                and cls._offering_has_submitted_grades(offering)
            ):
                skipped.append(
                    {
                        "offering_id": offering.id,
                        "reason": "Submitted grades detected. Use correction workflow before hotfix recompute.",
                    }
                )
                continue

            try:
                template = FacultyGradingService.resolve_template_for_offering(offering)
                if template.id != hotfix_request.template_id:
                    skipped.append(
                        {"offering_id": offering.id, "reason": "Offering no longer resolves to target template."}
                    )
                    continue
                periods = template.periods.filter(is_active=True).order_by("sequence_no", "id")
                for period in periods:
                    FacultyGradingService.recompute_period_summary(
                        user=reviewer,
                        offering=offering,
                        template_period=period,
                    )
                recomputed += 1
                processed.append(offering.id)
            except ValidationError as exc:
                skipped.append({"offering_id": offering.id, "reason": str(exc)})

        hotfix_request.status = TemplateHotfixRequest.Status.APPLIED
        hotfix_request.applied_by_user = reviewer
        hotfix_request.applied_at = timezone.now()
        hotfix_request.affected_offering_count = len(target_offerings)
        hotfix_request.recomputed_offering_count = recomputed
        hotfix_request.impact_snapshot_json = {
            "mode": hotfix_request.apply_mode,
            "processed_offering_ids": processed,
            "skipped": skipped,
        }
        hotfix_request.save(
            update_fields=[
                "status",
                "applied_by_user",
                "applied_at",
                "affected_offering_count",
                "recomputed_offering_count",
                "impact_snapshot_json",
                "updated_at",
            ]
        )
        return hotfix_request


class TemplateGovernanceWorkflowService:
    STAGE_DRAFT = "DRAFT"
    STAGE_SUBMIT_FOR_APPROVAL = "SUBMIT_FOR_APPROVAL"
    STAGE_APPROVAL_REVIEW = "APPROVAL_REVIEW"
    STAGE_PUBLISH = "PUBLISH"
    STAGE_HOTFIX_REQUEST = "HOTFIX_REQUEST"
    STAGE_HOTFIX_REVIEW_APPLY = "HOTFIX_REVIEW_APPLY"
    STEP_TEMPLATE_REVIEW = "TEMPLATE_REVIEW"
    STEP_TEMPLATE_APPROVAL = "TEMPLATE_APPROVAL"
    STEP_HOTFIX_REVIEW = "HOTFIX_REVIEW"
    STEP_HOTFIX_APPLY = "HOTFIX_APPLY"

    STAGE_CHOICES = (
        (STAGE_DRAFT, "Draft"),
        (STAGE_SUBMIT_FOR_APPROVAL, "Submit for Approval"),
        (STAGE_APPROVAL_REVIEW, "Approval Review"),
        (STAGE_PUBLISH, "Publish"),
        (STAGE_HOTFIX_REQUEST, "Hotfix Request"),
        (STAGE_HOTFIX_REVIEW_APPLY, "Hotfix Review and Apply"),
    )

    STAGE_ROLE_KEYS = {
        STAGE_DRAFT: "TEMPLATE_WORKFLOW_DRAFT_ROLE_CODES",
        STAGE_SUBMIT_FOR_APPROVAL: "TEMPLATE_WORKFLOW_SUBMIT_ROLE_CODES",
        STAGE_APPROVAL_REVIEW: "TEMPLATE_WORKFLOW_APPROVAL_REVIEW_ROLE_CODES",
        STAGE_PUBLISH: "TEMPLATE_WORKFLOW_PUBLISH_ROLE_CODES",
        STAGE_HOTFIX_REQUEST: "TEMPLATE_WORKFLOW_HOTFIX_REQUEST_ROLE_CODES",
        STAGE_HOTFIX_REVIEW_APPLY: "TEMPLATE_WORKFLOW_HOTFIX_REVIEW_APPLY_ROLE_CODES",
    }

    REQUIRE_APPROVAL_BEFORE_PUBLISH_KEY = "TEMPLATE_WORKFLOW_REQUIRE_APPROVAL_BEFORE_PUBLISH"
    ALLOW_SAME_USER_SUBMIT_REVIEW_KEY = "TEMPLATE_WORKFLOW_ALLOW_SAME_USER_SUBMIT_REVIEW"
    ALLOW_SAME_USER_REVIEW_APPROVE_KEY = "TEMPLATE_WORKFLOW_ALLOW_SAME_USER_REVIEW_APPROVE"
    ALLOW_SAME_USER_REVIEW_PUBLISH_KEY = "TEMPLATE_WORKFLOW_ALLOW_SAME_USER_REVIEW_PUBLISH"
    ALLOW_SAME_USER_HOTFIX_REQUEST_APPLY_KEY = "TEMPLATE_WORKFLOW_ALLOW_SAME_USER_HOTFIX_REQUEST_APPLY"
    ALLOW_SAME_USER_HOTFIX_REVIEW_APPLY_KEY = "TEMPLATE_WORKFLOW_ALLOW_SAME_USER_HOTFIX_REVIEW_APPLY"

    SEQUENTIAL_APPROVAL_ENABLED_KEY = "TEMPLATE_WORKFLOW_SEQUENTIAL_APPROVAL_ENABLED"
    SEQUENTIAL_HOTFIX_ENABLED_KEY = "TEMPLATE_WORKFLOW_SEQUENTIAL_HOTFIX_ENABLED"
    APPROVAL_REVIEW_STEP_ROLE_CODES_KEY = "TEMPLATE_WORKFLOW_APPROVAL_REVIEW_STEP_ROLE_CODES"
    APPROVAL_FINAL_STEP_ROLE_CODES_KEY = "TEMPLATE_WORKFLOW_APPROVAL_FINAL_STEP_ROLE_CODES"
    HOTFIX_REVIEW_STEP_ROLE_CODES_KEY = "TEMPLATE_WORKFLOW_HOTFIX_REVIEW_STEP_ROLE_CODES"
    HOTFIX_APPLY_STEP_ROLE_CODES_KEY = "TEMPLATE_WORKFLOW_HOTFIX_APPLY_STEP_ROLE_CODES"

    DEFAULT_STAGE_ROLE_CODES = {
        STAGE_DRAFT: ["TENANT_ADMIN", "SUPER_ADMIN"],
        STAGE_SUBMIT_FOR_APPROVAL: ["TENANT_ADMIN", "SUPER_ADMIN"],
        STAGE_APPROVAL_REVIEW: ["CAO", "SUPER_ADMIN"],
        STAGE_PUBLISH: ["TENANT_ADMIN", "SUPER_ADMIN"],
        STAGE_HOTFIX_REQUEST: ["TENANT_ADMIN", "SUPER_ADMIN"],
        STAGE_HOTFIX_REVIEW_APPLY: ["CAO", "SUPER_ADMIN"],
    }

    DEFAULT_SEQUENTIAL_STEP_ROLE_CODES = {
        STEP_TEMPLATE_REVIEW: ["DEAN", "CAO"],
        STEP_TEMPLATE_APPROVAL: ["CAO", "SUPER_ADMIN"],
        STEP_HOTFIX_REVIEW: ["DEAN", "CAO"],
        STEP_HOTFIX_APPLY: ["CAO", "SUPER_ADMIN"],
    }

    @classmethod
    def stage_label(cls, stage_code: str) -> str:
        return dict(cls.STAGE_CHOICES).get(stage_code, stage_code)

    @classmethod
    def _normalize_role_code_list(cls, raw_value, default):
        if not isinstance(raw_value, list):
            return list(default)
        return [str(code).strip().upper() for code in raw_value if str(code).strip()]

    @classmethod
    def get_stage_role_codes(cls, *, stage_code: str, tenant_id: int | None):
        key = cls.STAGE_ROLE_KEYS.get(stage_code)
        default = cls.DEFAULT_STAGE_ROLE_CODES.get(stage_code, [])
        if not key:
            return list(default)
        raw_value = SystemSettingService.get(key, tenant_id=tenant_id, default=default)
        return cls._normalize_role_code_list(raw_value, default)

    @classmethod
    def sequential_template_approval_enabled(cls, *, tenant_id: int | None):
        return bool(
            SystemSettingService.get(
                cls.SEQUENTIAL_APPROVAL_ENABLED_KEY,
                tenant_id=tenant_id,
                default=False,
            )
        )

    @classmethod
    def sequential_hotfix_enabled(cls, *, tenant_id: int | None):
        return bool(
            SystemSettingService.get(
                cls.SEQUENTIAL_HOTFIX_ENABLED_KEY,
                tenant_id=tenant_id,
                default=False,
            )
        )

    @classmethod
    def get_sequential_step_role_codes(cls, *, step_code: str, tenant_id: int | None):
        key_map = {
            cls.STEP_TEMPLATE_REVIEW: cls.APPROVAL_REVIEW_STEP_ROLE_CODES_KEY,
            cls.STEP_TEMPLATE_APPROVAL: cls.APPROVAL_FINAL_STEP_ROLE_CODES_KEY,
            cls.STEP_HOTFIX_REVIEW: cls.HOTFIX_REVIEW_STEP_ROLE_CODES_KEY,
            cls.STEP_HOTFIX_APPLY: cls.HOTFIX_APPLY_STEP_ROLE_CODES_KEY,
        }
        key = key_map.get(step_code)
        default = cls.DEFAULT_SEQUENTIAL_STEP_ROLE_CODES.get(step_code, [])
        if not key:
            return list(default)
        raw_value = SystemSettingService.get(key, tenant_id=tenant_id, default=default)
        return cls._normalize_role_code_list(raw_value, default)

    @classmethod
    def require_approval_before_publish(cls, *, tenant_id: int | None):
        return bool(
            SystemSettingService.get(
                cls.REQUIRE_APPROVAL_BEFORE_PUBLISH_KEY,
                tenant_id=tenant_id,
                default=True,
            )
        )

    @classmethod
    def allow_same_user_submit_review(cls, *, tenant_id: int | None):
        return bool(
            SystemSettingService.get(
                cls.ALLOW_SAME_USER_SUBMIT_REVIEW_KEY,
                tenant_id=tenant_id,
                default=False,
            )
        )

    @classmethod
    def allow_same_user_review_approve(cls, *, tenant_id: int | None):
        return bool(
            SystemSettingService.get(
                cls.ALLOW_SAME_USER_REVIEW_APPROVE_KEY,
                tenant_id=tenant_id,
                default=False,
            )
        )

    @classmethod
    def allow_same_user_review_publish(cls, *, tenant_id: int | None):
        return bool(
            SystemSettingService.get(
                cls.ALLOW_SAME_USER_REVIEW_PUBLISH_KEY,
                tenant_id=tenant_id,
                default=False,
            )
        )

    @classmethod
    def allow_same_user_hotfix_request_apply(cls, *, tenant_id: int | None):
        return bool(
            SystemSettingService.get(
                cls.ALLOW_SAME_USER_HOTFIX_REQUEST_APPLY_KEY,
                tenant_id=tenant_id,
                default=False,
            )
        )

    @classmethod
    def allow_same_user_hotfix_review_apply(cls, *, tenant_id: int | None):
        return bool(
            SystemSettingService.get(
                cls.ALLOW_SAME_USER_HOTFIX_REVIEW_APPLY_KEY,
                tenant_id=tenant_id,
                default=False,
            )
        )

    @classmethod
    def _user_role_codes_for_tenant(cls, *, user, tenant_id: int | None):
        if not user or not getattr(user, "is_authenticated", False):
            return set()
        if getattr(user, "is_superuser", False):
            return set(Role.objects.filter(is_active=True).values_list("code", flat=True))

        role_qs = UserRole.objects.filter(user=user, is_active=True, role__is_active=True)
        if tenant_id is not None:
            role_qs = role_qs.filter(
                Q(tenant_id=tenant_id)
                | Q(campus__tenant_id=tenant_id)
                | (Q(tenant__isnull=True) & Q(campus__isnull=True))
            )
        return {code for code in role_qs.values_list("role__code", flat=True)}

    @classmethod
    def user_has_stage_role(cls, *, user, stage_code: str, tenant_id: int | None):
        stage_codes = set(cls.get_stage_role_codes(stage_code=stage_code, tenant_id=tenant_id))
        if not stage_codes:
            return False
        return bool(cls._user_role_codes_for_tenant(user=user, tenant_id=tenant_id) & stage_codes)

    @classmethod
    def ensure_user_can_perform_stage(cls, *, user, stage_code: str, tenant_id: int | None):
        if not cls.user_has_stage_role(user=user, stage_code=stage_code, tenant_id=tenant_id):
            raise ValidationError(
                f"You are not included in the configured roles for {cls.stage_label(stage_code)}."
            )

    @classmethod
    def _actor_matches_role_codes(cls, *, actor, role_codes: list[str], tenant_id: int | None):
        if not role_codes:
            return False
        return bool(cls._user_role_codes_for_tenant(user=actor, tenant_id=tenant_id) & set(role_codes))

    @classmethod
    def get_approval_step_definitions(cls, *, tenant_id: int | None):
        if cls.sequential_template_approval_enabled(tenant_id=tenant_id):
            return [
                {
                    "step_no": 1,
                    "step_code": cls.STEP_TEMPLATE_REVIEW,
                    "step_label": "Template Review",
                    "role_codes": cls.get_sequential_step_role_codes(
                        step_code=cls.STEP_TEMPLATE_REVIEW,
                        tenant_id=tenant_id,
                    ),
                },
                {
                    "step_no": 2,
                    "step_code": cls.STEP_TEMPLATE_APPROVAL,
                    "step_label": "Final Approval",
                    "role_codes": cls.get_sequential_step_role_codes(
                        step_code=cls.STEP_TEMPLATE_APPROVAL,
                        tenant_id=tenant_id,
                    ),
                },
            ]
        return [
            {
                "step_no": 1,
                "step_code": cls.STAGE_APPROVAL_REVIEW,
                "step_label": cls.stage_label(cls.STAGE_APPROVAL_REVIEW),
                "role_codes": cls.get_stage_role_codes(
                    stage_code=cls.STAGE_APPROVAL_REVIEW,
                    tenant_id=tenant_id,
                ),
            }
        ]

    @classmethod
    def get_hotfix_step_definitions(cls, *, tenant_id: int | None):
        if cls.sequential_hotfix_enabled(tenant_id=tenant_id):
            return [
                {
                    "step_no": 1,
                    "step_code": cls.STEP_HOTFIX_REVIEW,
                    "step_label": "Hotfix Review",
                    "role_codes": cls.get_sequential_step_role_codes(
                        step_code=cls.STEP_HOTFIX_REVIEW,
                        tenant_id=tenant_id,
                    ),
                },
                {
                    "step_no": 2,
                    "step_code": cls.STEP_HOTFIX_APPLY,
                    "step_label": "Hotfix Final Apply",
                    "role_codes": cls.get_sequential_step_role_codes(
                        step_code=cls.STEP_HOTFIX_APPLY,
                        tenant_id=tenant_id,
                    ),
                },
            ]
        return [
            {
                "step_no": 1,
                "step_code": cls.STAGE_HOTFIX_REVIEW_APPLY,
                "step_label": cls.stage_label(cls.STAGE_HOTFIX_REVIEW_APPLY),
                "role_codes": cls.get_stage_role_codes(
                    stage_code=cls.STAGE_HOTFIX_REVIEW_APPLY,
                    tenant_id=tenant_id,
                ),
            }
        ]

    @classmethod
    def create_approval_workflow(cls, *, template, actor):
        step_definitions = cls.get_approval_step_definitions(tenant_id=template.tenant_id)
        if not step_definitions:
            raise ValidationError("No template approval workflow steps are configured.")
        workflow = GradingTemplateApprovalWorkflow.objects.create(
            tenant_id=template.tenant_id,
            template=template,
            status=GradingTemplateApprovalWorkflow.Status.PENDING,
            submitted_by_user=actor,
            submitted_at=timezone.now(),
            current_step_no=1,
        )
        step_rows = []
        for step_definition in step_definitions:
            step_rows.append(
                GradingTemplateApprovalStep(
                    workflow=workflow,
                    step_no=step_definition["step_no"],
                    step_code=step_definition["step_code"],
                    step_label=step_definition["step_label"],
                    role_codes_json=step_definition["role_codes"],
                    status=(
                        GradingTemplateApprovalStep.Status.PENDING
                        if step_definition["step_no"] == 1
                        else GradingTemplateApprovalStep.Status.QUEUED
                    ),
                )
            )
        GradingTemplateApprovalStep.objects.bulk_create(step_rows)
        return workflow

    @classmethod
    def _build_fallback_approval_workflow(cls, *, template):
        submitted_by = template.approval_requested_by or template.published_by
        if not submitted_by:
            raise ValidationError("This template has no recorded submitter for workflow reconstruction.")
        workflow = GradingTemplateApprovalWorkflow.objects.create(
            tenant_id=template.tenant_id,
            template=template,
            status=GradingTemplateApprovalWorkflow.Status.PENDING,
            submitted_by_user=submitted_by,
            submitted_at=template.approval_requested_at or template.updated_at or timezone.now(),
            current_step_no=1,
        )
        step_definitions = cls.get_approval_step_definitions(tenant_id=template.tenant_id)
        step_rows = []
        for step_definition in step_definitions:
            step_rows.append(
                GradingTemplateApprovalStep(
                    workflow=workflow,
                    step_no=step_definition["step_no"],
                    step_code=step_definition["step_code"],
                    step_label=step_definition["step_label"],
                    role_codes_json=step_definition["role_codes"],
                    status=(
                        GradingTemplateApprovalStep.Status.PENDING
                        if step_definition["step_no"] == 1
                        else GradingTemplateApprovalStep.Status.QUEUED
                    ),
                )
            )
        GradingTemplateApprovalStep.objects.bulk_create(step_rows)
        return workflow

    @classmethod
    def get_pending_approval_workflow(cls, *, template):
        workflow = (
            template.approval_workflows.select_related("submitted_by_user")
            .prefetch_related("steps")
            .filter(status=GradingTemplateApprovalWorkflow.Status.PENDING)
            .order_by("-created_at")
            .first()
        )
        if workflow:
            return workflow
        if template.approval_status == template.ApprovalStatus.FOR_APPROVAL:
            return cls._build_fallback_approval_workflow(template=template)
        return None

    @classmethod
    def get_current_approval_step(cls, *, template):
        workflow = cls.get_pending_approval_workflow(template=template)
        if not workflow:
            return None
        return workflow.steps.filter(status=GradingTemplateApprovalStep.Status.PENDING).order_by("step_no").first()

    @classmethod
    def user_can_take_approval_step(cls, *, template, actor):
        step = cls.get_current_approval_step(template=template)
        if not step:
            return False
        if not cls._actor_matches_role_codes(actor=actor, role_codes=step.role_codes_json or [], tenant_id=template.tenant_id):
            return False
        workflow = step.workflow
        if (
            workflow.submitted_by_user_id == actor.id
            and not cls.allow_same_user_submit_review(tenant_id=template.tenant_id)
        ):
            return False
        previous_step = (
            workflow.steps.filter(step_no__lt=step.step_no, status=GradingTemplateApprovalStep.Status.APPROVED)
            .order_by("-step_no")
            .first()
        )
        if (
            previous_step
            and previous_step.acted_by_user_id == actor.id
            and step.step_no > 1
            and not cls.allow_same_user_review_approve(tenant_id=template.tenant_id)
        ):
            return False
        return True

    @classmethod
    def ensure_can_review_template(cls, *, template, actor):
        step = cls.get_current_approval_step(template=template)
        if not step:
            raise ValidationError("There is no pending approval step for this template.")
        if not cls._actor_matches_role_codes(actor=actor, role_codes=step.role_codes_json or [], tenant_id=template.tenant_id):
            raise ValidationError(f"You are not included in the configured roles for {step.step_label}.")
        if (
            template.approval_requested_by_id
            and template.approval_requested_by_id == actor.id
            and not cls.allow_same_user_submit_review(tenant_id=template.tenant_id)
        ):
            raise ValidationError("The same user cannot submit and review this template under the current workflow.")
        previous_step = (
            step.workflow.steps.filter(step_no__lt=step.step_no, status=GradingTemplateApprovalStep.Status.APPROVED)
            .order_by("-step_no")
            .first()
        )
        if (
            previous_step
            and previous_step.acted_by_user_id == actor.id
            and step.step_no > 1
            and not cls.allow_same_user_review_approve(tenant_id=template.tenant_id)
        ):
            raise ValidationError(
                "The same user cannot perform both the review and final approval steps under the current workflow."
            )

    @classmethod
    def ensure_can_publish_template(cls, *, template, actor):
        cls.ensure_user_can_perform_stage(
            user=actor,
            stage_code=cls.STAGE_PUBLISH,
            tenant_id=template.tenant_id,
        )
        if (
            template.approval_reviewed_by_id
            and template.approval_reviewed_by_id == actor.id
            and not cls.allow_same_user_review_publish(tenant_id=template.tenant_id)
        ):
            raise ValidationError("The same user cannot review and publish this template under the current workflow.")

    @classmethod
    def ensure_can_apply_hotfix(cls, *, hotfix_request: TemplateHotfixRequest, actor):
        step = cls.get_current_hotfix_step(hotfix_request=hotfix_request)
        if not step:
            raise ValidationError("There is no pending hotfix workflow step for this request.")
        if not cls._actor_matches_role_codes(
            actor=actor,
            role_codes=step.role_codes_json or [],
            tenant_id=hotfix_request.tenant_id,
        ):
            raise ValidationError(f"You are not included in the configured roles for {step.step_label}.")
        if (
            hotfix_request.requested_by_user_id == actor.id
            and not cls.allow_same_user_hotfix_request_apply(tenant_id=hotfix_request.tenant_id)
        ):
            raise ValidationError(
                "The same user cannot request and apply this hotfix under the current workflow."
            )
        previous_step = (
            hotfix_request.workflow_steps.filter(step_no__lt=step.step_no, status=TemplateHotfixWorkflowStep.Status.APPROVED)
            .order_by("-step_no")
            .first()
        )
        if (
            previous_step
            and previous_step.acted_by_user_id == actor.id
            and step.step_no > 1
            and not cls.allow_same_user_hotfix_review_apply(tenant_id=hotfix_request.tenant_id)
        ):
            raise ValidationError(
                "The same user cannot perform both the hotfix review and final apply steps under the current workflow."
            )

    @classmethod
    def user_can_take_hotfix_step(cls, *, hotfix_request, actor):
        step = cls.get_current_hotfix_step(hotfix_request=hotfix_request)
        if not step:
            return False
        if not cls._actor_matches_role_codes(
            actor=actor,
            role_codes=step.role_codes_json or [],
            tenant_id=hotfix_request.tenant_id,
        ):
            return False
        if (
            hotfix_request.requested_by_user_id == actor.id
            and not cls.allow_same_user_hotfix_request_apply(tenant_id=hotfix_request.tenant_id)
        ):
            return False
        previous_step = (
            hotfix_request.workflow_steps.filter(
                step_no__lt=step.step_no,
                status=TemplateHotfixWorkflowStep.Status.APPROVED,
            )
            .order_by("-step_no")
            .first()
        )
        if (
            previous_step
            and previous_step.acted_by_user_id == actor.id
            and step.step_no > 1
            and not cls.allow_same_user_hotfix_review_apply(tenant_id=hotfix_request.tenant_id)
        ):
            return False
        return True

    @classmethod
    def create_hotfix_workflow_steps(cls, *, hotfix_request):
        step_definitions = cls.get_hotfix_step_definitions(tenant_id=hotfix_request.tenant_id)
        if not step_definitions:
            raise ValidationError("No hotfix workflow steps are configured.")
        step_rows = []
        for step_definition in step_definitions:
            step_rows.append(
                TemplateHotfixWorkflowStep(
                    hotfix_request=hotfix_request,
                    step_no=step_definition["step_no"],
                    step_code=step_definition["step_code"],
                    step_label=step_definition["step_label"],
                    role_codes_json=step_definition["role_codes"],
                    status=(
                        TemplateHotfixWorkflowStep.Status.PENDING
                        if step_definition["step_no"] == 1
                        else TemplateHotfixWorkflowStep.Status.QUEUED
                    ),
                )
            )
        TemplateHotfixWorkflowStep.objects.bulk_create(step_rows)

    @classmethod
    def _build_fallback_hotfix_steps(cls, *, hotfix_request):
        if hotfix_request.status != TemplateHotfixRequest.Status.PENDING:
            return None
        if hotfix_request.workflow_steps.exists():
            return None
        cls.create_hotfix_workflow_steps(hotfix_request=hotfix_request)
        return hotfix_request.workflow_steps.order_by("step_no").first()

    @classmethod
    def get_current_hotfix_step(cls, *, hotfix_request):
        step = hotfix_request.workflow_steps.filter(status=TemplateHotfixWorkflowStep.Status.PENDING).order_by("step_no").first()
        if step:
            return step
        if hotfix_request.status == TemplateHotfixRequest.Status.PENDING:
            return cls._build_fallback_hotfix_steps(hotfix_request=hotfix_request)
        return None

    @classmethod
    def get_workflow_snapshot(cls, *, tenant_id: int | None):
        return {
            "require_approval_before_publish": cls.require_approval_before_publish(tenant_id=tenant_id),
            "sequential_template_approval_enabled": cls.sequential_template_approval_enabled(tenant_id=tenant_id),
            "sequential_hotfix_enabled": cls.sequential_hotfix_enabled(tenant_id=tenant_id),
            "allow_same_user_submit_review": cls.allow_same_user_submit_review(tenant_id=tenant_id),
            "allow_same_user_review_approve": cls.allow_same_user_review_approve(tenant_id=tenant_id),
            "allow_same_user_review_publish": cls.allow_same_user_review_publish(tenant_id=tenant_id),
            "allow_same_user_hotfix_request_apply": cls.allow_same_user_hotfix_request_apply(tenant_id=tenant_id),
            "allow_same_user_hotfix_review_apply": cls.allow_same_user_hotfix_review_apply(tenant_id=tenant_id),
            "stages": [
                {
                    "code": stage_code,
                    "label": stage_label,
                    "role_codes": cls.get_stage_role_codes(stage_code=stage_code, tenant_id=tenant_id),
                }
                for stage_code, stage_label in cls.STAGE_CHOICES
            ],
            "approval_steps": cls.get_approval_step_definitions(tenant_id=tenant_id),
            "hotfix_steps": cls.get_hotfix_step_definitions(tenant_id=tenant_id),
        }


class GradingGovernanceService:
    CORRECTION_MODE_KEY = "CORRECTION_MODE"
    CORRECTION_MODE_SYSTEM_REQUEST = "SYSTEM_REQUEST"
    DEADLINE_POLICY_COMPLIANCE_ONLY = FeatureSettingsService.GRADE_DEADLINE_POLICY_COMPLIANCE_ONLY
    DEADLINE_POLICY_AUTO_CLOSE_REQUIRES_REOPEN = (
        FeatureSettingsService.GRADE_DEADLINE_POLICY_AUTO_CLOSE_REQUIRES_REOPEN
    )
    CORRECTION_MODE_MANUAL_ONLY = "MANUAL_ONLY"
    CORRECTION_WINDOW_HOURS = 24
    REOPEN_REQUEST_WINDOW_HOURS = 24

    @staticmethod
    def _round(value: Decimal) -> Decimal:
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @staticmethod
    def _normalize_period_key(value: str | None) -> str:
        raw = (value or "").strip().upper().replace("-", "").replace("_", "").replace(" ", "")
        if "PREFINAL" in raw or "PREFI" in raw:
            return "PREFINAL"
        if "MIDTERM" in raw:
            return "MIDTERM"
        if raw == "FINAL" or raw.endswith("FINAL") or "FINALEXAM" in raw:
            return "FINAL"
        if "PRELIM" in raw:
            return "PRELIM"
        return raw

    @classmethod
    def get_correction_mode(cls, *, tenant_id: int | None):
        mode = SystemSettingService.get(
            cls.CORRECTION_MODE_KEY,
            tenant_id=tenant_id,
            default=cls.CORRECTION_MODE_SYSTEM_REQUEST,
        )
        if mode not in {
            cls.CORRECTION_MODE_SYSTEM_REQUEST,
            cls.CORRECTION_MODE_MANUAL_ONLY,
        }:
            return cls.CORRECTION_MODE_SYSTEM_REQUEST
        return mode

    @classmethod
    def is_system_correction_enabled(cls, *, tenant_id: int | None):
        return cls.get_correction_mode(tenant_id=tenant_id) == cls.CORRECTION_MODE_SYSTEM_REQUEST

    @staticmethod
    def resolve_requesting_faculty_department(*, user, tenant_id: int | None):
        department = getattr(user, "default_department", None)
        if not department:
            return None
        if tenant_id and getattr(department, "tenant_id", None) != tenant_id:
            return None
        if hasattr(department, "is_active") and not department.is_active:
            return None
        return department

    @classmethod
    def resolve_correction_route_rule(cls, *, tenant_id: int, faculty_department_id: int | None):
        dept_rule = None
        if faculty_department_id:
            department_ids = ScopeService.department_ancestor_ids(faculty_department_id, include_self=True)
            dept_rule = (
                CorrectionApprovalRouteRule.objects.filter(
                    tenant_id=tenant_id,
                    faculty_department_id__in=department_ids,
                    is_active=True,
                )
                .select_related("step1_role", "final_role", "faculty_department")
                .order_by("id")
            )
            dept_rule_by_department = {row.faculty_department_id: row for row in dept_rule}
            for department_id in department_ids:
                if department_id in dept_rule_by_department:
                    return dept_rule_by_department[department_id]
        if dept_rule:
            return dept_rule
        default_rule = (
            CorrectionApprovalRouteRule.objects.filter(
                tenant_id=tenant_id,
                faculty_department__isnull=True,
                is_active=True,
            )
            .select_related("step1_role", "final_role", "faculty_department")
            .first()
        )
        if default_rule:
            return default_rule

        # Backward-safe fallback:
        # if exactly one active route exists for this tenant, use it even when
        # faculty department context is missing/incomplete.
        active_routes = list(
            CorrectionApprovalRouteRule.objects.filter(
                tenant_id=tenant_id,
                is_active=True,
            )
            .select_related("step1_role", "final_role", "faculty_department")
            .order_by("id")
        )
        if len(active_routes) == 1:
            return active_routes[0]
        return None

    @classmethod
    def _build_correction_route_steps(cls, *, route_rule: CorrectionApprovalRouteRule):
        steps = [
            {
                "step_order": 1,
                "role": route_rule.step1_role,
                "label": route_rule.step1_role.name or route_rule.step1_role.code,
                "requires_same_department": route_rule.step1_requires_same_department,
            }
        ]
        if route_rule.route_mode == CorrectionApprovalRouteRule.RouteMode.TWO_STEP:
            if not route_rule.final_role_id:
                raise ValidationError("Two-step route requires a final approver role.")
            steps.append(
                {
                    "step_order": 2,
                    "role": route_rule.final_role,
                    "label": route_rule.final_role.name or route_rule.final_role.code,
                    "requires_same_department": route_rule.final_requires_same_department,
                }
            )
        return steps

    @classmethod
    def initialize_correction_route(cls, *, request_obj: GradeCorrectionRequest):
        faculty_department = cls.resolve_requesting_faculty_department(
            user=request_obj.requested_by_user,
            tenant_id=request_obj.tenant_id,
        )
        faculty_department_id = faculty_department.id if faculty_department else None
        route_rule = cls.resolve_correction_route_rule(
            tenant_id=request_obj.tenant_id,
            faculty_department_id=faculty_department_id,
        )
        if route_rule:
            steps = cls._build_correction_route_steps(route_rule=route_rule)
        else:
            fallback_role_codes = ["CAO", "REGISTRAR", "DEAN", "CAMPUS_ADMIN", "TENANT_ADMIN"]
            fallback_role = None
            for role_code in fallback_role_codes:
                role_candidate = Role.objects.filter(code=role_code, is_active=True).first()
                if role_candidate:
                    fallback_role = role_candidate
                    break
            if not fallback_role:
                raise ValidationError(
                    "No active correction route is configured and no fallback approver role was found."
                )
            steps = [
                {
                    "step_order": 1,
                    "role": fallback_role,
                    "label": fallback_role.name or fallback_role.code,
                    "requires_same_department": False,
                }
            ]
        request_obj.faculty_department_id = faculty_department_id
        request_obj.approval_route_id = route_rule.id if route_rule else None
        request_obj.save(update_fields=["faculty_department", "approval_route", "updated_at"])

        step_rows = [
            GradeCorrectionApprovalStep(
                correction_request=request_obj,
                step_order=step["step_order"],
                approver_role_id=step["role"].id,
                approver_label=step["label"],
                requires_same_department=step["requires_same_department"],
                status=GradeCorrectionApprovalStep.Status.PENDING,
            )
            for step in steps
        ]
        GradeCorrectionApprovalStep.objects.bulk_create(step_rows)

    @classmethod
    @transaction.atomic
    def reconcile_pending_correction_route(cls, *, request_obj: GradeCorrectionRequest):
        if request_obj.status != GradeCorrectionRequest.Status.PENDING:
            return False
        if request_obj.approval_route_id:
            return False
        if request_obj.approval_steps.exclude(status=GradeCorrectionApprovalStep.Status.PENDING).exists():
            return False

        faculty_department = cls.resolve_requesting_faculty_department(
            user=request_obj.requested_by_user,
            tenant_id=request_obj.tenant_id,
        )
        faculty_department_id = faculty_department.id if faculty_department else None
        route_rule = cls.resolve_correction_route_rule(
            tenant_id=request_obj.tenant_id,
            faculty_department_id=faculty_department_id,
        )
        if not route_rule:
            return False

        expected_steps = cls._build_correction_route_steps(route_rule=route_rule)
        current_steps = list(
            request_obj.approval_steps.order_by("step_order").values(
                "step_order", "approver_role_id", "requires_same_department"
            )
        )
        expected_signature = [
            {
                "step_order": step["step_order"],
                "approver_role_id": step["role"].id,
                "requires_same_department": step["requires_same_department"],
            }
            for step in expected_steps
        ]
        if current_steps != expected_signature:
            request_obj.approval_steps.all().delete()
            GradeCorrectionApprovalStep.objects.bulk_create(
                [
                    GradeCorrectionApprovalStep(
                        correction_request=request_obj,
                        step_order=step["step_order"],
                        approver_role_id=step["role"].id,
                        approver_label=step["label"],
                        requires_same_department=step["requires_same_department"],
                        status=GradeCorrectionApprovalStep.Status.PENDING,
                    )
                    for step in expected_steps
                ]
            )
        request_obj.faculty_department_id = faculty_department_id
        request_obj.approval_route_id = route_rule.id
        request_obj.save(update_fields=["faculty_department", "approval_route", "updated_at"])
        return True

    @staticmethod
    def get_pending_correction_step(*, request_obj: GradeCorrectionRequest):
        return (
            request_obj.approval_steps.filter(status=GradeCorrectionApprovalStep.Status.PENDING)
            .order_by("step_order")
            .select_related("approver_role")
            .first()
        )

    @staticmethod
    def _user_has_role_for_correction_step(*, user, request_obj: GradeCorrectionRequest, step: GradeCorrectionApprovalStep):
        if user.is_superuser:
            return True
        if UserRole.objects.filter(
            user=user,
            role__code="SUPER_ADMIN",
            is_active=True,
        ).filter(
            Q(tenant_id=request_obj.tenant_id) | Q(tenant__isnull=True)
        ).filter(
            Q(campus_id=request_obj.campus_id) | Q(campus__isnull=True)
        ).exists():
            return True
        return UserRole.objects.filter(
            user=user,
            role_id=step.approver_role_id,
            is_active=True,
        ).filter(
            Q(tenant_id=request_obj.tenant_id) | Q(tenant__isnull=True)
        ).filter(
            Q(campus_id=request_obj.campus_id) | Q(campus__isnull=True)
        ).exists()

    @classmethod
    def can_user_review_correction_request(cls, *, request_obj: GradeCorrectionRequest, user):
        pending_step = cls.get_pending_correction_step(request_obj=request_obj)
        if not pending_step:
            # Legacy request created before route-matrix rollout.
            return True, None, None
        if (
            request_obj.request_source == GradeCorrectionRequest.RequestSource.ADMIN_ON_BEHALF
            and request_obj.initiated_by_user_id == getattr(user, "id", None)
            and not getattr(user, "is_superuser", False)
        ):
            return (
                False,
                pending_step,
                "The user who initiated an on-behalf correction petition cannot approve the same petition.",
            )
        if not cls._user_has_role_for_correction_step(user=user, request_obj=request_obj, step=pending_step):
            step_label = (
                pending_step.approver_label
                or (pending_step.approver_role.name if pending_step.approver_role_id else None)
                or (pending_step.approver_role.code if pending_step.approver_role_id else "the configured approver")
            )
            return (
                False,
                pending_step,
                f"Only users assigned to approver role {step_label} can review this step.",
            )
        if pending_step.requires_same_department:
            user_dept_id = getattr(user, "default_department_id", None)
            if not user_dept_id or not ScopeService.department_scope_covers(user_dept_id, request_obj.faculty_department_id):
                return (
                    False,
                    pending_step,
                    "This step requires the approver to belong to the same faculty department as the requester.",
                )
        return True, pending_step, None

    @classmethod
    def is_final_correction_step(cls, *, request_obj: GradeCorrectionRequest, step: GradeCorrectionApprovalStep | None):
        if step is None:
            return True
        return not request_obj.approval_steps.filter(
            status=GradeCorrectionApprovalStep.Status.PENDING,
            step_order__gt=step.step_order,
        ).exists()

    @classmethod
    def resolve_submission_deadline(cls, *, offering, template_period: GradingTemplatePeriod):
        lock = cls.resolve_lock(offering=offering, template_period=template_period)
        return lock.deadline_at if lock else None

    @classmethod
    def resolve_completion_grace_deadline(cls, *, offering, template_period: GradingTemplatePeriod):
        return None

    @classmethod
    def resolve_encoding_close_deadline(cls, *, offering, template_period: GradingTemplatePeriod):
        return cls.resolve_submission_deadline(offering=offering, template_period=template_period)

    @classmethod
    def get_grade_deadline_enforcement_policy(cls, *, tenant_id: int | None):
        return FeatureSettingsService.get_grade_deadline_enforcement_policy(tenant_id=tenant_id)

    @classmethod
    def is_within_completion_grace(cls, *, offering, template_period: GradingTemplatePeriod, now=None):
        return False

    @classmethod
    def auto_lapse_expired_late_completion_requests(cls, *, at=None, dry_run: bool = False):
        now = at or timezone.now()
        return {"checked_at": now, "count": 0, "dry_run": dry_run, "rows": []}

    @classmethod
    def get_pending_late_completion_request(cls, *, offering, template_period: GradingTemplatePeriod):
        return None

    @classmethod
    def get_active_late_completion_request(cls, *, offering, template_period: GradingTemplatePeriod, at=None):
        return None

    @classmethod
    def has_active_late_completion_request(cls, *, offering, template_period: GradingTemplatePeriod, at=None):
        return False

    @classmethod
    def can_request_late_completion(cls, *, offering, template_period: GradingTemplatePeriod, now=None):
        return False

    @classmethod
    def get_completion_window_state(
        cls,
        *,
        offering,
        template_period: GradingTemplatePeriod,
        now=None,
    ):
        now = now or timezone.now()
        submission_deadline = cls.resolve_submission_deadline(
            offering=offering,
            template_period=template_period,
        )
        is_submitted = cls.is_submitted(offering=offering, template_period=template_period)
        encoding_close_deadline = cls.resolve_encoding_close_deadline(
            offering=offering,
            template_period=template_period,
        )
        is_auto_closed = cls.is_auto_closed_after_deadline(
            offering=offering,
            template_period=template_period,
            now=now,
        )
        is_overdue = bool(submission_deadline and now > submission_deadline and not is_submitted)
        return {
            "submission_deadline": submission_deadline,
            "completion_grace_until": None,
            "encoding_close_deadline": encoding_close_deadline,
            "has_completion_grace": False,
            "is_within_completion_grace": False,
            "grace_expired": False,
            "active_late_completion_request": None,
            "pending_late_completion_request": None,
            "has_active_late_completion_request": False,
            "has_pending_late_completion_request": False,
            "can_request_late_completion": False,
            "is_non_compliant": is_overdue,
            "is_overdue": is_overdue,
            "is_auto_closed_after_deadline": is_auto_closed,
        }

    @classmethod
    def can_faculty_self_reopen_before_deadline(cls, *, offering, template_period: GradingTemplatePeriod):
        submission = cls.get_submission(offering=offering, template_period=template_period)
        if not submission or submission.status != GradeSubmission.Status.SUBMITTED:
            return False
        lock = cls.resolve_lock(offering=offering, template_period=template_period)
        if not lock or lock.is_locked or not lock.deadline_at:
            return False
        return timezone.now() <= lock.deadline_at

    @classmethod
    @transaction.atomic
    def faculty_self_reopen_before_deadline(
        cls,
        *,
        user,
        offering,
        template_period: GradingTemplatePeriod,
        remarks: str | None = None,
    ):
        justification = (remarks or "").strip()
        if not justification:
            raise ValidationError("Reopen justification is required.")
        if not cls.can_faculty_self_reopen_before_deadline(
            offering=offering,
            template_period=template_period,
        ):
            raise ValidationError("Faculty self-reopen is allowed only for submitted gradebooks before the deadline.")
        return cls.reopen_period(
            user=user,
            offering=offering,
            template_period=template_period,
            remarks=justification,
        )

    @classmethod
    def _template_activity_requirements(
        cls,
        *,
        offering,
        template_period: GradingTemplatePeriod,
    ):
        components = list(
            template_period.components.filter(is_active=True)
            .prefetch_related(
                Prefetch(
                    "subcomponents",
                    queryset=GradingTemplateSubcomponent.objects.filter(is_active=True)
                    .prefetch_related(
                        Prefetch(
                            "details",
                            queryset=GradingTemplateDetail.objects.filter(is_active=True).order_by("sort_order", "id"),
                        )
                    )
                    .order_by("sort_order", "id"),
                )
            )
            .order_by("sort_order", "id")
        )
        activity_buckets = set(
            GradeActivity.objects.filter(
                offering_id=offering.id,
                template_period_id=template_period.id,
                is_active=True,
                template_component__is_active=True,
            )
            .filter(
                Q(template_subcomponent__isnull=True, template_detail__isnull=True)
                | Q(
                    template_subcomponent__is_active=True,
                    template_detail__isnull=True,
                )
                | Q(
                    template_subcomponent__is_active=True,
                    template_detail__is_active=True,
                )
            )
            .values_list("template_component_id", "template_subcomponent_id", "template_detail_id")
        )
        required_items = []
        missing_items = []

        if not components:
            return {
                "required_items": [],
                "missing_items": [
                    {
                        "level": "period",
                        "label": f"{template_period.name} has no active grading components",
                        "component_id": None,
                        "subcomponent_id": None,
                        "detail_id": None,
                    }
                ],
            }

        for component in components:
            subcomponents = list(component.subcomponents.all())
            if not subcomponents:
                item = {
                    "level": "component",
                    "label": component.name or component.code,
                    "component_id": component.id,
                    "subcomponent_id": None,
                    "detail_id": None,
                    "expected_record_type": "activity",
                }
                required_items.append(item)
                if (component.id, None, None) not in activity_buckets:
                    missing_items.append(item)
                continue

            for subcomponent in subcomponents:
                details = list(subcomponent.details.all())
                label_prefix = f"{component.name or component.code} > {subcomponent.name or subcomponent.code}"
                if subcomponent.is_attendance_component:
                    continue
                normalized_activity_scope = " ".join(
                    "".join(
                        character if character.isalnum() else " "
                        for character in (
                            f"{component.code} {component.name} "
                            f"{subcomponent.code} {subcomponent.name}"
                        ).lower()
                    ).split()
                )
                is_participation_output = (
                    "participation" in normalized_activity_scope
                    and "output" in normalized_activity_scope
                )
                if (
                    details
                    and is_participation_output
                    and subcomponent.detail_computation_mode == DetailComputationMode.AVERAGE_ACTIVITIES
                ):
                    item = {
                        "level": "subcomponent",
                        "label": label_prefix,
                        "component_id": component.id,
                        "subcomponent_id": subcomponent.id,
                        "detail_id": None,
                        "expected_record_type": "activity",
                    }
                    required_items.append(item)
                    active_detail_ids = {detail.id for detail in details}
                    has_active_activity = any(
                        bucket_component_id == component.id
                        and bucket_subcomponent_id == subcomponent.id
                        and bucket_detail_id in active_detail_ids
                        for bucket_component_id, bucket_subcomponent_id, bucket_detail_id in activity_buckets
                    )
                    if not has_active_activity:
                        missing_items.append(item)
                    continue
                if not details:
                    item = {
                        "level": "subcomponent",
                        "label": label_prefix,
                        "component_id": component.id,
                        "subcomponent_id": subcomponent.id,
                        "detail_id": None,
                        "expected_record_type": "activity",
                    }
                    required_items.append(item)
                    if (component.id, subcomponent.id, None) not in activity_buckets:
                        missing_items.append(item)
                    continue

                for detail in details:
                    item = {
                        "level": "detail",
                        "label": f"{label_prefix} > {detail.name or detail.code}",
                        "component_id": component.id,
                        "subcomponent_id": subcomponent.id,
                        "detail_id": detail.id,
                        "expected_record_type": "activity",
                    }
                    required_items.append(item)
                    if (component.id, subcomponent.id, detail.id) not in activity_buckets:
                        missing_items.append(item)

        return {"required_items": required_items, "missing_items": missing_items}

    @classmethod
    def evaluate_submission_readiness(cls, *, offering, template_period: GradingTemplatePeriod):
        eligible_enrollments = list(
            Enrollment.objects.filter(
                course_offering_id=offering.id,
                is_active=True,
                student__is_active=True,
                student__department__is_active=True,
            )
            .filter(Q(student__program__isnull=True) | Q(student__program__is_active=True))
            .exclude(enrollment_status__in=Enrollment.NON_ACTIVE_GRADING_STATUSES)
            .select_related("student")
        )
        eligible_student_ids = [row.student_id for row in eligible_enrollments]
        eligible_count = len(eligible_student_ids)

        if eligible_count == 0:
            return {
                "eligible_student_count": 0,
                "students_with_any_grade": 0,
                "students_missing_any_grade": 0,
                "students_with_complete_records": 0,
                "expected_activity_count": 0,
                "expected_attendance_session_count": 0,
                "expected_template_bucket_count": 0,
                "missing_template_bucket_count": 0,
                "coverage_percent": Decimal("0.00"),
                "missing_students": [],
                "missing_template_items": [],
            }

        active_activity_ids = list(
            GradeActivity.objects.filter(
                offering_id=offering.id,
                template_period_id=template_period.id,
                is_active=True,
            ).values_list("id", flat=True)
        )
        expected_activity_count = len(active_activity_ids)

        has_attendance_component = GradingTemplateSubcomponent.objects.filter(
            template_component__template_period_id=template_period.id,
            template_component__is_active=True,
            is_active=True,
            is_attendance_component=True,
        ).exists()
        active_attendance_session_ids = list(
            AttendanceSession.objects.filter(
                offering_id=offering.id,
                template_period_id=template_period.id,
                is_active=True,
            ).values_list("id", flat=True)
        )
        expected_attendance_session_count = len(active_attendance_session_ids) if has_attendance_component else 0
        template_requirements = cls._template_activity_requirements(
            offering=offering,
            template_period=template_period,
        )
        missing_template_items = template_requirements["missing_items"]

        score_student_ids = set(
            StudentActivityScore.objects.filter(
                activity__offering_id=offering.id,
                activity__template_period_id=template_period.id,
                activity__is_active=True,
                is_active=True,
                student_id__in=eligible_student_ids,
            )
            .values_list("student_id", flat=True)
            .distinct()
        )
        attendance_student_ids = set(
            AttendanceRecord.objects.filter(
                session__offering_id=offering.id,
                session__template_period_id=template_period.id,
                session__is_active=True,
                is_active=True,
                student_id__in=eligible_student_ids,
            )
            .values_list("student_id", flat=True)
            .distinct()
        )
        students_with_any_grade_ids = score_student_ids | attendance_student_ids
        students_with_any_grade = len(students_with_any_grade_ids)
        score_count_map = {
            row["student_id"]: row["encoded_count"]
            for row in StudentActivityScore.objects.filter(
                activity_id__in=active_activity_ids,
                is_active=True,
                student_id__in=eligible_student_ids,
            )
            .values("student_id")
            .annotate(encoded_count=Count("activity_id", distinct=True))
        }
        attendance_count_map = {
            row["student_id"]: row["recorded_count"]
            for row in AttendanceRecord.objects.filter(
                session_id__in=active_attendance_session_ids,
                is_active=True,
                student_id__in=eligible_student_ids,
            )
            .values("student_id")
            .annotate(recorded_count=Count("session_id", distinct=True))
        }
        missing_students = [
            {
                "student_id": enrollment.student_id,
                "student_no": enrollment.student.student_no,
                "last_name": enrollment.student.last_name,
                "first_name": enrollment.student.first_name,
                "missing_activity_records": max(
                    expected_activity_count - score_count_map.get(enrollment.student_id, 0),
                    0,
                ),
                "missing_attendance_records": max(
                    expected_attendance_session_count - attendance_count_map.get(enrollment.student_id, 0),
                    0,
                ),
            }
            for enrollment in eligible_enrollments
            if (
                score_count_map.get(enrollment.student_id, 0) < expected_activity_count
                or attendance_count_map.get(enrollment.student_id, 0) < expected_attendance_session_count
            )
        ]
        missing_count = len(missing_students)
        students_with_complete_records = eligible_count - missing_count
        coverage_percent = cls._round(
            (Decimal(students_with_complete_records) / Decimal(eligible_count)) * Decimal("100")
        )

        return {
            "eligible_student_count": eligible_count,
            "students_with_any_grade": students_with_any_grade,
            "students_missing_any_grade": missing_count,
            "students_with_complete_records": students_with_complete_records,
            "expected_activity_count": expected_activity_count,
            "expected_attendance_session_count": expected_attendance_session_count,
            "expected_template_bucket_count": len(template_requirements["required_items"]),
            "missing_template_bucket_count": len(missing_template_items),
            "coverage_percent": coverage_percent,
            "missing_students": missing_students,
            "missing_template_items": missing_template_items,
        }

    @classmethod
    def resolve_lock(cls, *, offering, template_period: GradingTemplatePeriod):
        target_period_key = cls._normalize_period_key(template_period.code or template_period.name)
        lock_qs = GradingPeriodLock.objects.filter(
            tenant_id=offering.tenant_id,
            campus_id=offering.campus_id,
            academic_year_id=offering.academic_year_id,
            term_id=offering.term_id,
            is_active=True,
        ).order_by("-updated_at")

        matching_locks = [
            lock
            for lock in lock_qs
            if cls._normalize_period_key(lock.period_code) == target_period_key
        ]
        if not matching_locks:
            return None

        for lock in matching_locks:
            if (
                lock.scope_type == GradingPeriodLock.ScopeType.COURSE
                and lock.course_offering_id == offering.id
            ):
                return lock

        for lock in matching_locks:
            if (
                lock.scope_type == GradingPeriodLock.ScopeType.CAMPUS
                and lock.course_offering_id is None
            ):
                return lock
        return None

    @classmethod
    @transaction.atomic
    def auto_lock_due_periods(cls, *, at=None, limit: int | None = None, dry_run: bool = False):
        now = at or timezone.now()
        rows = []
        reopened_submissions = list(
            GradeSubmission.objects.select_related(
                "tenant",
                "campus",
                "offering",
                "offering__academic_year",
                "offering__term",
                "template_period",
            )
            .filter(status=GradeSubmission.Status.REOPENED)
            .order_by("updated_at", "id")
        )
        for submission in reopened_submissions:
            if limit is not None and len(rows) >= limit:
                break
            row = cls._auto_lock_expired_reopened_submission(
                submission=submission,
                now=now,
                dry_run=dry_run,
            )
            if row is None:
                continue
            rows.append(row)

        approved_requests = list(
            GradeSubmissionReopenRequest.objects.select_related(
                "tenant",
                "campus",
                "submission",
                "offering",
                "offering__academic_year",
                "offering__term",
                "template_period",
            )
            .filter(status=GradeSubmissionReopenRequest.Status.APPROVED)
            .exclude(submission__status=GradeSubmission.Status.SUBMITTED)
            .order_by("reviewed_at", "updated_at", "id")
        )
        for request_obj in approved_requests:
            if limit is not None and len(rows) >= limit:
                break
            row = cls._auto_lock_expired_approved_reopen_request(
                request_obj=request_obj,
                now=now,
                dry_run=dry_run,
            )
            if row is None:
                continue
            rows.append(row)
        return {
            "checked_at": now,
            "count": len(rows),
            "dry_run": dry_run,
            "rows": rows,
        }

    @classmethod
    @transaction.atomic
    def auto_lock_expired_reopened_gradebook(
        cls,
        *,
        offering,
        template_period: GradingTemplatePeriod,
        at=None,
        dry_run: bool = False,
    ):
        submission = cls.get_submission(offering=offering, template_period=template_period)
        if not submission:
            return None
        submission = (
            GradeSubmission.objects.select_related(
                "tenant",
                "campus",
                "offering",
                "offering__academic_year",
                "offering__term",
                "template_period",
            )
            .filter(id=submission.id)
            .first()
        )
        return cls._auto_lock_expired_reopened_submission(
            submission=submission,
            now=at or timezone.now(),
            dry_run=dry_run,
        )

    @classmethod
    @transaction.atomic
    def auto_lock_expired_approved_reopen_request_for_period(
        cls,
        *,
        offering,
        template_period: GradingTemplatePeriod,
        at=None,
        dry_run: bool = False,
    ):
        request_obj = (
            GradeSubmissionReopenRequest.objects.select_related(
                "tenant",
                "campus",
                "submission",
                "offering",
                "offering__academic_year",
                "offering__term",
                "template_period",
            )
            .filter(
                offering_id=offering.id,
                template_period_id=template_period.id,
                status=GradeSubmissionReopenRequest.Status.APPROVED,
            )
            .exclude(submission__status=GradeSubmission.Status.SUBMITTED)
            .order_by("-reviewed_at", "-updated_at", "-id")
            .first()
        )
        if not request_obj:
            return None
        return cls._auto_lock_expired_approved_reopen_request(
            request_obj=request_obj,
            now=at or timezone.now(),
            dry_run=dry_run,
        )

    @classmethod
    def _auto_lock_expired_reopened_submission(cls, *, submission: GradeSubmission, now, dry_run: bool = False):
        if submission.status != GradeSubmission.Status.REOPENED:
            return None
        lock = cls.resolve_lock(offering=submission.offering, template_period=submission.template_period)
        if not lock or not lock.deadline_at or lock.deadline_at >= now:
            return None
        row = {
            "id": lock.id,
            "submission_id": submission.id,
            "tenant_code": submission.tenant.code,
            "campus_code": submission.campus.code,
            "academic_year_code": submission.offering.academic_year.code,
            "term_code": submission.offering.term.code,
            "period_code": submission.template_period.code,
            "scope_type": GradingPeriodLock.ScopeType.COURSE,
            "course_offering_id": submission.offering_id,
            "deadline_at": lock.deadline_at,
        }
        if dry_run:
            return row

        course_lock, _created = GradingPeriodLock.objects.update_or_create(
            tenant_id=submission.tenant_id,
            campus_id=submission.campus_id,
            academic_year_id=submission.offering.academic_year_id,
            term_id=submission.offering.term_id,
            period_code=lock.period_code,
            scope_type=GradingPeriodLock.ScopeType.COURSE,
            course_offering_id=submission.offering_id,
            defaults={
                "is_locked": True,
                "deadline_at": lock.deadline_at,
                "locked_at": now,
                "locked_by_user": None,
                "remarks": "Auto-locked because a reopened gradebook was not resubmitted before the deadline.",
                "is_active": True,
            },
        )
        AuditService.log_event(
            action="LOCK",
            portal="SYSTEM",
            entity_type="GradingPeriodLock",
            entity_id=course_lock.id,
            actor=None,
            tenant=submission.tenant,
            campus=submission.campus,
            after_data={
                "submission_id": submission.id,
                "offering_id": submission.offering_id,
                "template_period_id": submission.template_period_id,
                "deadline_at": lock.deadline_at.isoformat() if lock.deadline_at else None,
                "reason": "REOPENED_DEADLINE_EXPIRED",
            },
        )
        row["id"] = course_lock.id
        return row

    @classmethod
    def reopen_request_expires_at(cls, request_obj: GradeSubmissionReopenRequest):
        if not request_obj or request_obj.status != GradeSubmissionReopenRequest.Status.APPROVED:
            return None
        anchor = request_obj.reviewed_at or request_obj.updated_at or request_obj.created_at
        if not anchor:
            return None
        return anchor + timedelta(hours=cls.REOPEN_REQUEST_WINDOW_HOURS)

    @classmethod
    def is_reopen_request_window_active(cls, request_obj: GradeSubmissionReopenRequest, *, at=None):
        expires_at = cls.reopen_request_expires_at(request_obj)
        return bool(expires_at and (at or timezone.now()) <= expires_at)

    @classmethod
    def _auto_lock_expired_approved_reopen_request(
        cls,
        *,
        request_obj: GradeSubmissionReopenRequest,
        now,
        dry_run: bool = False,
    ):
        if request_obj.status != GradeSubmissionReopenRequest.Status.APPROVED:
            return None
        if request_obj.submission.status == GradeSubmission.Status.SUBMITTED:
            return None
        latest_approved_request = cls.get_latest_approved_reopen_request(
            offering=request_obj.offering,
            template_period=request_obj.template_period,
        )
        if not latest_approved_request or latest_approved_request.id != request_obj.id:
            return None
        expires_at = cls.reopen_request_expires_at(request_obj)
        if not expires_at or expires_at > now:
            return None

        lock = cls.resolve_lock(offering=request_obj.offering, template_period=request_obj.template_period)
        row = {
            "id": getattr(lock, "id", None),
            "submission_id": request_obj.submission_id,
            "reopen_request_id": request_obj.id,
            "tenant_code": request_obj.tenant.code,
            "campus_code": request_obj.campus.code,
            "academic_year_code": request_obj.offering.academic_year.code,
            "term_code": request_obj.offering.term.code,
            "period_code": request_obj.template_period.code,
            "scope_type": GradingPeriodLock.ScopeType.COURSE,
            "course_offering_id": request_obj.offering_id,
            "deadline_at": expires_at,
        }
        if dry_run:
            return row

        course_lock, _created = GradingPeriodLock.objects.update_or_create(
            tenant_id=request_obj.tenant_id,
            campus_id=request_obj.campus_id,
            academic_year_id=request_obj.offering.academic_year_id,
            term_id=request_obj.offering.term_id,
            period_code=request_obj.template_period.code,
            scope_type=GradingPeriodLock.ScopeType.COURSE,
            course_offering_id=request_obj.offering_id,
            defaults={
                "is_locked": True,
                "deadline_at": expires_at,
                "locked_at": now,
                "locked_by_user": None,
                "remarks": "Auto-locked because an approved reopen request expired after 24 hours without submission.",
                "is_active": True,
            },
        )
        AuditService.log_event(
            action="LOCK",
            portal="SYSTEM",
            entity_type="GradingPeriodLock",
            entity_id=course_lock.id,
            actor=None,
            tenant=request_obj.tenant,
            campus=request_obj.campus,
            after_data={
                "submission_id": request_obj.submission_id,
                "reopen_request_id": request_obj.id,
                "offering_id": request_obj.offering_id,
                "template_period_id": request_obj.template_period_id,
                "deadline_at": expires_at.isoformat(),
                "reason": "APPROVED_REOPEN_WINDOW_EXPIRED",
            },
        )
        row["id"] = course_lock.id
        return row

    @classmethod
    def get_submission(cls, *, offering, template_period: GradingTemplatePeriod):
        return GradeSubmission.objects.filter(
            offering_id=offering.id,
            template_period_id=template_period.id,
        ).order_by("-updated_at").first()

    @classmethod
    @transaction.atomic
    def auto_lapse_expired_correction_windows(cls, *, at=None, dry_run: bool = False):
        now = at or timezone.now()
        due_windows = list(
            GradeCorrectionUnlockWindow.objects.select_related("correction_request", "offering", "template_period")
            .filter(
                is_active=True,
                is_consumed=False,
                end_at__lt=now,
                correction_request__status=GradeCorrectionRequest.Status.APPROVED,
            )
            .order_by("end_at", "id")
        )

        rows = []
        for window in due_windows:
            request_obj = window.correction_request
            rows.append(
                {
                    "window_id": window.id,
                    "request_id": request_obj.id,
                    "offering_id": window.offering_id,
                    "template_period_id": window.template_period_id,
                    "window_end_at": window.end_at,
                }
            )
            if dry_run:
                continue

            window.is_active = False
            window.is_consumed = True
            window.closed_at = now
            window.save(update_fields=["is_active", "is_consumed", "closed_at", "updated_at"])

            request_obj.status = GradeCorrectionRequest.Status.LAPSED
            request_obj.save(update_fields=["status", "updated_at"])

            AuditService.log_event(
                action="LAPSE",
                portal="SYSTEM",
                entity_type="GradeCorrectionRequest",
                entity_id=request_obj.id,
                actor=None,
                tenant=request_obj.tenant,
                campus=request_obj.campus,
                before_data={
                    "status": GradeCorrectionRequest.Status.APPROVED,
                    "window_end_at": window.end_at.isoformat() if window.end_at else None,
                },
                after_data={
                    "status": GradeCorrectionRequest.Status.LAPSED,
                    "window_consumed": True,
                    "lapsed_at": now.isoformat(),
                },
                metadata={
                    "mode": "AUTO_LAPSE_CORRECTION_WINDOW",
                    "window_id": window.id,
                    "correction_window_hours": cls.CORRECTION_WINDOW_HOURS,
                },
            )

        return {
            "checked_at": now,
            "count": len(rows),
            "dry_run": dry_run,
            "rows": rows,
        }

    @classmethod
    def is_locked(cls, *, offering, template_period: GradingTemplatePeriod):
        lock = cls.resolve_lock(offering=offering, template_period=template_period)
        return bool(lock and lock.is_locked)

    @classmethod
    def get_active_approved_reopen_request(cls, *, offering, template_period: GradingTemplatePeriod):
        request_obj = cls.get_latest_approved_reopen_request(
            offering=offering,
            template_period=template_period,
        )
        if request_obj and cls.is_reopen_request_window_active(request_obj):
            return request_obj
        return None

    @classmethod
    def get_latest_approved_reopen_request(cls, *, offering, template_period: GradingTemplatePeriod):
        submission = cls.get_submission(offering=offering, template_period=template_period)
        if not submission or submission.status == GradeSubmission.Status.SUBMITTED:
            return None
        return (
            GradeSubmissionReopenRequest.objects.filter(
                submission=submission,
                status=GradeSubmissionReopenRequest.Status.APPROVED,
            )
            .order_by("-reviewed_at", "-updated_at", "-id")
            .first()
        )

    @classmethod
    def get_latest_expired_approved_reopen_request(cls, *, offering, template_period: GradingTemplatePeriod):
        request_obj = cls.get_latest_approved_reopen_request(
            offering=offering,
            template_period=template_period,
        )
        if request_obj and not cls.is_reopen_request_window_active(request_obj):
            return request_obj
        return None

    @classmethod
    def get_pending_reopen_request(cls, *, offering, template_period: GradingTemplatePeriod):
        submission = cls.get_submission(offering=offering, template_period=template_period)
        if not submission:
            return None
        return (
            GradeSubmissionReopenRequest.objects.filter(
                submission=submission,
                status=GradeSubmissionReopenRequest.Status.PENDING,
            )
            .order_by("-created_at", "-id")
            .first()
        )

    @classmethod
    def is_auto_closed_after_deadline(cls, *, offering, template_period: GradingTemplatePeriod, now=None):
        submission = cls.get_submission(offering=offering, template_period=template_period)
        if submission and submission.status == GradeSubmission.Status.SUBMITTED:
            return False
        if cls.get_active_approved_reopen_request(offering=offering, template_period=template_period):
            return False
        if not FeatureSettingsService.is_grade_deadline_auto_close_enabled(tenant_id=offering.tenant_id):
            return False
        deadline = cls.resolve_submission_deadline(offering=offering, template_period=template_period)
        return bool(deadline and (now or timezone.now()) > deadline)

    @classmethod
    def can_request_reopen_after_auto_close(cls, *, offering, template_period: GradingTemplatePeriod, user=None):
        submission = cls.get_submission(offering=offering, template_period=template_period)
        if submission and submission.status == GradeSubmission.Status.SUBMITTED:
            return False
        if cls.get_active_approved_reopen_request(offering=offering, template_period=template_period):
            return False
        is_after_deadline = cls.is_auto_closed_after_deadline(offering=offering, template_period=template_period)
        is_locked = cls.is_locked(offering=offering, template_period=template_period)
        if not is_after_deadline and not is_locked:
            return False
        if cls.get_pending_reopen_request(offering=offering, template_period=template_period):
            return False
        return True

    @classmethod
    def can_request_submitted_reopen_before_deadline(cls, *, submission: GradeSubmission):
        if not submission or submission.status != GradeSubmission.Status.SUBMITTED:
            return False
        deadline = cls.resolve_submission_deadline(
            offering=submission.offering,
            template_period=submission.template_period,
        )
        return bool(deadline and timezone.now() <= deadline)

    @classmethod
    def is_assigned_reopen_reviewer(cls, *, user, tenant_id: int, campus_id: int) -> bool:
        if not user or not getattr(user, "is_authenticated", False) or not getattr(user, "is_active", False):
            return False
        return PermissionService.has_assigned_permission(
            user,
            "reopen_requests.review",
            tenant_id=tenant_id,
            campus_id=campus_id,
        )

    @classmethod
    def is_auto_locked_reopened_after_deadline(cls, *, offering, template_period: GradingTemplatePeriod):
        submission = cls.get_submission(offering=offering, template_period=template_period)
        if not submission or submission.status != GradeSubmission.Status.REOPENED:
            return False
        lock = cls.resolve_lock(offering=offering, template_period=template_period)
        if not lock or not lock.is_locked:
            return False
        return "reopened gradebook was not resubmitted before the deadline" in (lock.remarks or "").lower()

    @classmethod
    def is_submitted(cls, *, offering, template_period: GradingTemplatePeriod):
        submission = cls.get_submission(offering=offering, template_period=template_period)
        return bool(submission and submission.status == GradeSubmission.Status.SUBMITTED)

    @classmethod
    def get_active_unlock_window(cls, *, offering, template_period: GradingTemplatePeriod, at=None):
        now = at or timezone.now()
        cls.auto_lapse_expired_correction_windows(at=now)
        return (
            GradeCorrectionUnlockWindow.objects.select_related("correction_request")
            .filter(
                offering_id=offering.id,
                template_period_id=template_period.id,
                is_active=True,
                is_consumed=False,
                start_at__lte=now,
                end_at__gte=now,
                correction_request__status=GradeCorrectionRequest.Status.APPROVED,
            )
            .order_by("-created_at")
            .first()
        )

    @classmethod
    def has_active_unlock_window(cls, *, offering, template_period: GradingTemplatePeriod, at=None):
        return bool(cls.get_active_unlock_window(offering=offering, template_period=template_period, at=at))

    @classmethod
    def get_active_correction_request(cls, *, offering, template_period: GradingTemplatePeriod, at=None):
        window = cls.get_active_unlock_window(offering=offering, template_period=template_period, at=at)
        return window.correction_request if window else None

    @classmethod
    def _is_in_correction_scope(
        cls,
        *,
        window: GradeCorrectionUnlockWindow,
        student_id: int | None = None,
        activity_id: int | None = None,
        requested_action: str | None = None,
    ):
        items = window.correction_request.items.filter(is_active=True)
        if student_id is not None:
            items = items.filter(Q(student_id=student_id) | Q(student__isnull=True))
        if activity_id is not None:
            items = items.filter(Q(grade_activity_id=activity_id) | Q(grade_activity__isnull=True))
        if requested_action:
            items = items.filter(requested_action=requested_action)
        return items.exists()

    @classmethod
    def is_edit_allowed_under_correction(
        cls,
        *,
        offering,
        template_period: GradingTemplatePeriod,
        student_id: int | None = None,
        activity_id: int | None = None,
        requested_action: str | None = None,
    ):
        window = cls.get_active_unlock_window(offering=offering, template_period=template_period)
        if not window:
            return False
        return cls._is_in_correction_scope(
            window=window,
            student_id=student_id,
            activity_id=activity_id,
            requested_action=requested_action,
        )

    @classmethod
    def assert_summary_compute_allowed(cls, *, offering, template_period: GradingTemplatePeriod):
        if cls.is_locked(offering=offering, template_period=template_period) or cls.is_submitted(
            offering=offering, template_period=template_period
        ):
            if cls.get_active_approved_reopen_request(offering=offering, template_period=template_period):
                return True
            if cls.is_auto_locked_reopened_after_deadline(offering=offering, template_period=template_period):
                return True
            if not cls.has_active_unlock_window(offering=offering, template_period=template_period):
                raise ValidationError(f"{template_period.code} is locked/submitted and has no active correction window.")
        return True

    @classmethod
    def assert_encoding_allowed(
        cls,
        *,
        offering,
        template_period: GradingTemplatePeriod,
        student_id: int | None = None,
        activity_id: int | None = None,
        requested_action: str | None = None,
    ):
        is_locked = cls.is_locked(offering=offering, template_period=template_period)
        is_submitted = cls.is_submitted(offering=offering, template_period=template_period)
        is_auto_closed = cls.is_auto_closed_after_deadline(offering=offering, template_period=template_period)
        has_approved_deadline_reopen = bool(
            cls.get_active_approved_reopen_request(offering=offering, template_period=template_period)
        )
        if has_approved_deadline_reopen and not is_submitted:
            return True
        if not is_locked and not is_submitted:
            if is_auto_closed:
                raise ValidationError(
                    f"{template_period.code} encoding is closed after the configured deadline. Request gradebook reopen."
                )
            return True

        if student_id is not None and requested_action:
            allowed = cls.is_edit_allowed_under_correction(
                offering=offering,
                template_period=template_period,
                student_id=student_id,
                activity_id=activity_id,
                requested_action=requested_action,
            )
            if allowed:
                return True

        if is_locked:
            raise ValidationError(f"{template_period.code} is locked by academic governance.")
        if is_submitted:
            raise ValidationError(f"{template_period.code} has already been submitted.")
        return True

    @classmethod
    @transaction.atomic
    def submit_period(cls, *, user, offering, template_period: GradingTemplatePeriod, remarks: str | None = None):
        active_approved_reopen_request = cls.get_active_approved_reopen_request(
            offering=offering,
            template_period=template_period,
        )
        if cls.get_latest_expired_approved_reopen_request(offering=offering, template_period=template_period):
            raise ValidationError("The approved reopen window expired after 24 hours. Submit a new reopen request.")
        is_submitted = cls.is_submitted(offering=offering, template_period=template_period)
        is_locked = cls.is_locked(offering=offering, template_period=template_period)
        is_auto_closed = cls.is_auto_closed_after_deadline(
            offering=offering,
            template_period=template_period,
        )
        if is_submitted:
            raise ValidationError(f"{template_period.code} has already been submitted.")
        if (is_locked or is_auto_closed) and not active_approved_reopen_request:
            raise ValidationError(
                f"{template_period.code} is locked or past the deadline. Submit a gradebook reopen request first."
            )
        if not active_approved_reopen_request:
            cls.assert_encoding_allowed(offering=offering, template_period=template_period)
        readiness = cls.evaluate_submission_readiness(offering=offering, template_period=template_period)
        if readiness["eligible_student_count"] <= 0:
            raise ValidationError("No ACTIVE students available for submission in this period.")
        if readiness["students_with_any_grade"] <= 0:
            raise ValidationError(
                "Cannot submit yet. No grade records are encoded for ACTIVE students. "
                "Encode at least one grade/attendance record or mark students as DRP/W/INC first."
            )
        if readiness.get("missing_template_bucket_count", 0) > 0:
            missing_labels = ", ".join(
                item["label"] for item in readiness.get("missing_template_items", [])[:5]
            )
            raise ValidationError(
                "Cannot submit yet. The grading template still has required components without activity or attendance setup"
                + (f": {missing_labels}." if missing_labels else ".")
            )
        if readiness["students_missing_any_grade"] > 0:
            raise ValidationError(
                "Cannot submit yet. Some ACTIVE students still have blank required grade or attendance records. "
                "Complete all visible records first, or update the class-list status to DRP/W/INC where applicable."
            )

        summary = FacultyGradingService.recompute_period_summary(
            user=user,
            offering=offering,
            template_period=template_period,
            audit_reason="PERIOD_SUBMISSION",
            audit_portal="FACULTY",
            period_is_finalized=True,
            final_is_submitted=True,
        )
        period_rows = StudentPeriodGrade.objects.filter(
            offering_id=offering.id,
            template_period_id=template_period.id,
        )
        period_rows.update(is_finalized=True)
        StudentFinalGrade.objects.filter(offering_id=offering.id).update(is_submitted=True)

        template = FacultyGradingService.resolve_template_for_offering(offering)
        submission, _ = GradeSubmission.objects.update_or_create(
            offering=offering,
            template_period=template_period,
            defaults={
                "tenant_id": offering.tenant_id,
                "campus_id": offering.campus_id,
                "status": GradeSubmission.Status.SUBMITTED,
                "submitted_by_user": user,
                "submitted_at": timezone.now(),
                "remarks": (remarks or "").strip() or None,
                "submission_snapshot_json": {
                    "component_codes": summary.get("component_codes", []),
                    "student_count": len(summary.get("rows", [])),
                    "submitted_at": timezone.now().isoformat(),
                },
                "template_snapshot_json": {
                    "template_id": template.id,
                    "template_code": template.code,
                    "template_name": template.name,
                    "period_code": template_period.code,
                    "period_name": template_period.name,
                },
            },
        )
        if active_approved_reopen_request:
            lock = cls.resolve_lock(offering=offering, template_period=template_period)
            if lock and lock.is_locked and lock.course_offering_id == offering.id:
                lock.is_locked = False
                lock.reopened_by_user = user
                lock.reopened_at = timezone.now()
                lock.remarks = "Course-level reopen lock cleared after faculty submission."
                lock.save(update_fields=["is_locked", "reopened_by_user", "reopened_at", "remarks", "updated_at"])
        return submission

    @classmethod
    @transaction.atomic
    def reopen_period(
        cls,
        *,
        user,
        offering,
        template_period: GradingTemplatePeriod,
        remarks: str | None = None,
    ):
        submission = cls.get_submission(offering=offering, template_period=template_period)
        if submission:
            submission.status = GradeSubmission.Status.REOPENED
            submission.reopened_by_user = user
            submission.reopened_at = timezone.now()
            submission.remarks = (remarks or "").strip() or submission.remarks
            submission.save(
                update_fields=["status", "reopened_by_user", "reopened_at", "remarks", "updated_at"]
            )

        StudentPeriodGrade.objects.filter(
            offering_id=offering.id,
            template_period_id=template_period.id,
        ).update(is_finalized=False)
        StudentFinalGrade.objects.filter(offering_id=offering.id).update(is_submitted=False)
        return submission

    @classmethod
    @transaction.atomic
    def create_reopen_request(
        cls,
        *,
        user,
        submission: GradeSubmission,
        justification: str,
    ):
        if submission.status not in {
            GradeSubmission.Status.DRAFT,
            GradeSubmission.Status.SUBMITTED,
            GradeSubmission.Status.REOPENED,
        }:
            raise ValidationError("Only active grade periods can be reopened by request.")

        if submission.status == GradeSubmission.Status.SUBMITTED and not cls.can_request_submitted_reopen_before_deadline(
            submission=submission
        ):
            raise ValidationError(
                "Submitted gradebooks can be reopened by request only before the deadline. "
                "After the deadline, use the Correction of Grades workflow."
            )

        if GradeSubmissionReopenRequest.objects.filter(
            submission=submission,
            status=GradeSubmissionReopenRequest.Status.PENDING,
        ).exists():
            raise ValidationError("A pending reopen request already exists for this submission.")

        return GradeSubmissionReopenRequest.objects.create(
            tenant_id=submission.tenant_id,
            campus_id=submission.campus_id,
            submission=submission,
            offering_id=submission.offering_id,
            template_period_id=submission.template_period_id,
            requested_by_user=user,
            status=GradeSubmissionReopenRequest.Status.PENDING,
            justification=justification.strip(),
        )

    @classmethod
    @transaction.atomic
    def create_reopen_request_for_period(
        cls,
        *,
        user,
        offering,
        template_period: GradingTemplatePeriod,
        justification: str,
    ):
        justification = (justification or "").strip()
        if not justification:
            raise ValidationError("Reopen justification is required.")
        if not cls.can_request_reopen_after_auto_close(
            offering=offering,
            template_period=template_period,
            user=user,
        ):
            raise ValidationError("This gradebook is not available for a deadline reopen request.")
        submission, _created = GradeSubmission.objects.get_or_create(
            offering=offering,
            template_period=template_period,
            defaults={
                "tenant_id": offering.tenant_id,
                "campus_id": offering.campus_id,
                "status": GradeSubmission.Status.DRAFT,
            },
        )
        return cls.create_reopen_request(
            user=user,
            submission=submission,
            justification=justification,
        )

    @classmethod
    @transaction.atomic
    def review_reopen_request(
        cls,
        *,
        request_obj: GradeSubmissionReopenRequest,
        reviewer,
        approved: bool,
        review_remarks: str | None = None,
    ):
        if request_obj.status != GradeSubmissionReopenRequest.Status.PENDING:
            raise ValidationError("Only pending reopen requests can be reviewed.")
        if not cls.is_assigned_reopen_reviewer(
            user=reviewer,
            tenant_id=request_obj.tenant_id,
            campus_id=request_obj.campus_id,
        ):
            raise ValidationError(
                "Only a user explicitly assigned to review reopen requests for this campus can approve or reject it."
            )

        request_obj.reviewed_by_user = reviewer
        request_obj.reviewed_at = timezone.now()
        request_obj.review_remarks = (review_remarks or "").strip() or None

        if approved:
            if request_obj.submission.status == GradeSubmission.Status.SUBMITTED:
                cls.reopen_period(
                    user=reviewer,
                    offering=request_obj.offering,
                    template_period=request_obj.template_period,
                    remarks=request_obj.review_remarks,
                )
            elif request_obj.submission.status not in {GradeSubmission.Status.DRAFT, GradeSubmission.Status.REOPENED}:
                raise ValidationError("This submission is no longer available for reopen.")
            request_obj.status = GradeSubmissionReopenRequest.Status.APPROVED
        else:
            request_obj.status = GradeSubmissionReopenRequest.Status.REJECTED

        request_obj.save(
            update_fields=[
                "status",
                "reviewed_by_user",
                "reviewed_at",
                "review_remarks",
                "updated_at",
            ]
        )
        return request_obj

    @staticmethod
    def _format_decimal_value(value):
        if value in (None, ""):
            return ""
        decimal_value = Decimal(str(value))
        formatted = format(decimal_value.quantize(Decimal("0.01")), "f")
        if "." in formatted:
            formatted = formatted.rstrip("0").rstrip(".")
        return formatted

    @classmethod
    def _normalize_correction_items(
        cls,
        *,
        offering,
        template_period: GradingTemplatePeriod,
        items: list[dict],
    ):
        if not items:
            raise ValidationError("At least one correction scope item is required.")

        enrolled_student_ids = set(FacultyGradingService.get_active_enrollments(offering).values_list("student_id", flat=True))
        score_items = [
            item
            for item in items
            if (item.get("requested_action") or GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE)
            == GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE
        ]
        activity_ids = {
            int(item["grade_activity_id"])
            for item in score_items
            if item.get("grade_activity_id") not in (None, "")
        }
        activity_map = {
            row.id: row
            for row in GradeActivity.objects.filter(
                id__in=activity_ids,
                offering_id=offering.id,
                template_period_id=template_period.id,
                is_active=True,
            ).select_related("template_component", "template_subcomponent", "template_detail")
        }
        score_lookup = {
            (row.student_id, row.activity_id): cls._format_decimal_value(row.raw_score)
            for row in StudentActivityScore.objects.filter(
                activity_id__in=activity_ids,
                student_id__in=enrolled_student_ids,
                is_active=True,
            )
        }

        normalized_items = []
        seen_score_pairs = set()
        for item in items:
            action = item.get("requested_action") or GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE
            if action == GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE:
                student_id = item.get("student_id")
                grade_activity_id = item.get("grade_activity_id")
                if student_id in (None, ""):
                    raise ValidationError("Student is required for score correction.")
                if grade_activity_id in (None, ""):
                    raise ValidationError("Grading item is required for score correction.")

                student_id = int(student_id)
                grade_activity_id = int(grade_activity_id)
                if student_id not in enrolled_student_ids:
                    raise ValidationError("Selected student is outside the faculty gradebook scope.")

                activity = activity_map.get(grade_activity_id)
                if activity is None:
                    raise ValidationError("Selected grading item is outside the submitted period scope.")

                pair_key = (student_id, grade_activity_id)
                if pair_key in seen_score_pairs:
                    raise ValidationError("Duplicate score correction rows are not allowed.")
                seen_score_pairs.add(pair_key)

                new_value_raw = str(item.get("new_value") or "").strip()
                if not new_value_raw:
                    raise ValidationError("Corrected value is required for score correction.")
                try:
                    parsed_new_value = Decimal(new_value_raw)
                except (InvalidOperation, ValueError, TypeError):
                    raise ValidationError("Corrected value must be a valid number.") from None

                score_input_mode = FacultyGradingService.resolve_score_input_mode(
                    template_component=activity.template_component,
                    template_subcomponent=activity.template_subcomponent,
                    template_detail=activity.template_detail,
                )
                max_value = Decimal("100") if score_input_mode == "DIRECT_PERCENTAGE" else Decimal(activity.total_score)
                if parsed_new_value < 0 or parsed_new_value > max_value:
                    raise ValidationError(
                        f"Corrected value for {activity.title} must be between 0 and {cls._format_decimal_value(max_value)}."
                    )

                normalized_items.append(
                    {
                        "requested_action": action,
                        "student_id": student_id,
                        "grade_activity_id": grade_activity_id,
                        "old_value": score_lookup.get((student_id, grade_activity_id), ""),
                        "new_value": cls._format_decimal_value(parsed_new_value),
                    }
                )
                continue

            student_id = item.get("student_id")
            if student_id not in (None, ""):
                student_id = int(student_id)
                if student_id not in enrolled_student_ids:
                    raise ValidationError("Selected student is outside the faculty gradebook scope.")

            if action in {
                GradeCorrectionRequestItem.RequestedAction.UPDATE_ATTENDANCE,
                GradeCorrectionRequestItem.RequestedAction.UPDATE_STATUS,
            } and student_id in (None, ""):
                raise ValidationError("Student is required for this correction action.")

            normalized_items.append(
                {
                    "requested_action": action,
                    "student_id": student_id,
                    "grade_activity_id": item.get("grade_activity_id"),
                    "old_value": (item.get("old_value") or "")[:255],
                    "new_value": (item.get("new_value") or "")[:255],
                }
            )

        return normalized_items

    @classmethod
    def is_auto_apply_score_correction_request(cls, *, request_obj: GradeCorrectionRequest):
        items = list(request_obj.items.filter(is_active=True))
        return bool(items) and all(
            item.requested_action == GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE for item in items
        )

    @classmethod
    def _apply_auto_approved_score_correction_request(cls, *, request_obj: GradeCorrectionRequest, actor):
        items = list(
            request_obj.items.filter(
                is_active=True,
                requested_action=GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE,
            ).select_related(
                "grade_activity",
                "grade_activity__template_component",
                "grade_activity__template_subcomponent",
                "grade_activity__template_detail",
            )
        )
        if not items:
            raise ValidationError("No score correction rows were found for automatic application.")

        activity_map = {
            row.id: row
            for row in GradeActivity.objects.filter(
                id__in={item.grade_activity_id for item in items},
                offering_id=request_obj.offering_id,
                template_period_id=request_obj.template_period_id,
                is_active=True,
            ).select_related("template_component", "template_subcomponent", "template_detail")
        }
        payload_by_activity = defaultdict(list)
        affected_student_ids = set()
        effective_user = request_obj.requested_by_user or actor
        before_score_map = {
            (row.student_id, row.activity_id): {
                "id": row.id,
                "student_id": row.student_id,
                "activity_id": row.activity_id,
                "raw_score": row.raw_score,
                "computed_score": row.computed_score,
            }
            for row in StudentActivityScore.objects.filter(
                activity_id__in=activity_map.keys(),
                student_id__in={item.student_id for item in items if item.student_id},
                is_active=True,
            )
        }

        for item in items:
            activity = activity_map.get(item.grade_activity_id)
            if activity is None or item.student_id is None:
                raise ValidationError("A score correction row is no longer valid for this submitted period.")
            try:
                parsed_new_value = Decimal(str(item.new_value or "").strip())
            except (InvalidOperation, ValueError, TypeError):
                raise ValidationError("A score correction row contains an invalid corrected value.") from None
            payload_by_activity[activity.id].append(
                {
                    "student_id": item.student_id,
                    "raw_score": parsed_new_value,
                }
            )
            affected_student_ids.add(item.student_id)

        for activity_id, score_payload in payload_by_activity.items():
            FacultyGradingService.upsert_activity_scores(
                user=effective_user,
                activity=activity_map[activity_id],
                score_payload=score_payload,
                recompute=False,
                audit_reason="CORRECTION_SCORE_APPLY",
                audit_portal="ADMIN",
            )

        after_score_rows = {
            (row.student_id, row.activity_id): row
            for row in StudentActivityScore.objects.filter(
                activity_id__in=activity_map.keys(),
                student_id__in=affected_student_ids,
                is_active=True,
            )
        }
        for item in items:
            score_row = after_score_rows.get((item.student_id, item.grade_activity_id))
            if not score_row:
                continue
            after_score = {
                "id": score_row.id,
                "student_id": score_row.student_id,
                "activity_id": score_row.activity_id,
                "raw_score": score_row.raw_score,
                "computed_score": score_row.computed_score,
            }
            before_score = before_score_map.get((item.student_id, item.grade_activity_id))
            if before_score != after_score:
                AuditService.log_event(
                    action="UPDATE",
                    portal="ADMIN",
                    entity_type="StudentActivityScore",
                    entity_id=score_row.id,
                    actor=actor,
                    tenant=request_obj.tenant,
                    campus=request_obj.campus,
                    before_data=before_score,
                    after_data=after_score,
                    metadata={
                        "reason": "CORRECTION_APPROVAL",
                        "correction_request_id": request_obj.id,
                        "student_id": item.student_id,
                        "activity_id": item.grade_activity_id,
                    },
                )

        FacultyGradingService.recompute_period_summary_for_students(
            user=effective_user,
            offering=request_obj.offering,
            template_period=request_obj.template_period,
            student_ids=affected_student_ids,
            audit_reason="CORRECTION_APPROVAL",
            audit_portal="ADMIN",
            audit_actor=actor,
            period_is_finalized=True,
            final_is_submitted=True,
        )
        return request_obj

    @classmethod
    @transaction.atomic
    def create_correction_request(
        cls,
        *,
        user,
        offering,
        template_period: GradingTemplatePeriod,
        justification: str,
        items: list[dict],
        initiated_by_user=None,
        request_source: str | None = None,
        on_behalf_reason: str | None = None,
    ):
        if not cls.is_system_correction_enabled(tenant_id=offering.tenant_id):
            raise ValidationError(
                "Correction requests are disabled by tenant policy (MANUAL_ONLY). "
                "Please follow the manual approval process and ask authorized admin to reopen."
            )
        if not cls.is_submitted(offering=offering, template_period=template_period):
            raise ValidationError("Correction requests are allowed only after period submission.")
        normalized_items = cls._normalize_correction_items(
            offering=offering,
            template_period=template_period,
            items=items,
        )

        request_obj = GradeCorrectionRequest.objects.create(
            tenant_id=offering.tenant_id,
            campus_id=offering.campus_id,
            offering=offering,
            template_period=template_period,
            requested_by_user=user,
            initiated_by_user=initiated_by_user or user,
            request_source=request_source or GradeCorrectionRequest.RequestSource.FACULTY_SELF,
            on_behalf_reason=(on_behalf_reason or "").strip() or None,
            status=GradeCorrectionRequest.Status.PENDING,
            justification=justification.strip(),
        )
        cls.initialize_correction_route(request_obj=request_obj)
        item_rows = []
        for item in normalized_items:
            item_rows.append(
                GradeCorrectionRequestItem(
                    correction_request=request_obj,
                    requested_action=item["requested_action"],
                    student_id=item.get("student_id"),
                    grade_activity_id=item.get("grade_activity_id"),
                    old_value=(item.get("old_value") or "")[:255] or None,
                    new_value=(item.get("new_value") or "")[:255] or None,
                    is_active=True,
                )
            )
        GradeCorrectionRequestItem.objects.bulk_create(item_rows)
        return request_obj

    @classmethod
    @transaction.atomic
    def review_correction_request(
        cls,
        *,
        request_obj: GradeCorrectionRequest,
        reviewer,
        approved: bool,
        review_remarks: str | None = None,
        window_start=None,
        window_end=None,
    ):
        if request_obj.status != GradeCorrectionRequest.Status.PENDING:
            raise ValidationError("Only pending correction requests can be reviewed.")

        now = timezone.now()
        can_review, pending_step, reason = cls.can_user_review_correction_request(
            request_obj=request_obj,
            user=reviewer,
        )
        if not can_review:
            raise ValidationError(reason or "You are not allowed to review this correction request.")

        remarks_value = (review_remarks or "").strip() or None
        is_final_step = cls.is_final_correction_step(request_obj=request_obj, step=pending_step)

        if pending_step:
            pending_step.reviewed_by_user = reviewer
            pending_step.reviewed_at = now
            pending_step.review_remarks = remarks_value
            pending_step.status = (
                GradeCorrectionApprovalStep.Status.APPROVED
                if approved
                else GradeCorrectionApprovalStep.Status.REJECTED
            )
            pending_step.save(
                update_fields=[
                    "status",
                    "reviewed_by_user",
                    "reviewed_at",
                    "review_remarks",
                    "updated_at",
                ]
            )

        if approved and not is_final_step:
            request_obj.reviewed_by_user = reviewer
            request_obj.reviewed_at = now
            request_obj.review_remarks = remarks_value
            request_obj.save(update_fields=["reviewed_by_user", "reviewed_at", "review_remarks", "updated_at"])
            return request_obj

        request_obj.reviewed_by_user = reviewer
        request_obj.reviewed_at = now
        request_obj.review_remarks = remarks_value

        if approved:
            window_start = now
            window_end = now + timedelta(hours=cls.CORRECTION_WINDOW_HOURS)
            request_obj.status = GradeCorrectionRequest.Status.APPROVED

            GradeCorrectionUnlockWindow.objects.filter(
                offering_id=request_obj.offering_id,
                template_period_id=request_obj.template_period_id,
                is_active=True,
                is_consumed=False,
            ).update(is_active=False, is_consumed=True, closed_at=now)

            GradeCorrectionUnlockWindow.objects.update_or_create(
                correction_request=request_obj,
                defaults={
                    "offering_id": request_obj.offering_id,
                    "template_period_id": request_obj.template_period_id,
                    "start_at": window_start,
                    "end_at": window_end,
                    "is_active": True,
                    "is_consumed": False,
                    "closed_at": None,
                },
            )
            request_obj.save(
                update_fields=[
                    "status",
                    "reviewed_by_user",
                    "reviewed_at",
                    "review_remarks",
                    "updated_at",
                ]
            )
            if cls.is_auto_apply_score_correction_request(request_obj=request_obj):
                cls._apply_auto_approved_score_correction_request(request_obj=request_obj, actor=reviewer)
                return cls.close_correction_window(request_obj=request_obj, actor=reviewer)
        else:
            request_obj.status = GradeCorrectionRequest.Status.REJECTED
            if pending_step:
                request_obj.approval_steps.filter(
                    status=GradeCorrectionApprovalStep.Status.PENDING,
                    step_order__gt=pending_step.step_order,
                ).update(status=GradeCorrectionApprovalStep.Status.SKIPPED)

        request_obj.save(
            update_fields=[
                "status",
                "reviewed_by_user",
                "reviewed_at",
                "review_remarks",
                "updated_at",
            ]
        )
        return request_obj

    @classmethod
    @transaction.atomic
    def close_correction_window(cls, *, request_obj: GradeCorrectionRequest, actor=None):
        window = getattr(request_obj, "unlock_window", None)
        now = timezone.now()
        if window and window.is_active and not window.is_consumed:
            window.is_active = False
            window.is_consumed = True
            window.closed_at = now
            window.save(update_fields=["is_active", "is_consumed", "closed_at", "updated_at"])

        if request_obj.status == GradeCorrectionRequest.Status.APPROVED:
            request_obj.status = GradeCorrectionRequest.Status.CLOSED
            request_obj.save(update_fields=["status", "updated_at"])
        return request_obj


class FacultyGradingService:
    ATTENDANCE_SCORE_MAP = {
        AttendanceRecord.Status.PRESENT: Decimal("100"),
        AttendanceRecord.Status.EXCUSED: Decimal("100"),
        AttendanceRecord.Status.LATE: Decimal("90"),
        AttendanceRecord.Status.ABSENT: Decimal("0"),
    }
    DEFAULT_DEPED_TRANSMUTATION_TABLE = [
        {"min": "100.00", "max": "100.00", "grade": "100"},
        {"min": "98.40", "max": "99.99", "grade": "99"},
        {"min": "96.80", "max": "98.39", "grade": "98"},
        {"min": "95.20", "max": "96.79", "grade": "97"},
        {"min": "93.60", "max": "95.19", "grade": "96"},
        {"min": "92.00", "max": "93.59", "grade": "95"},
        {"min": "90.40", "max": "91.99", "grade": "94"},
        {"min": "88.80", "max": "90.39", "grade": "93"},
        {"min": "87.20", "max": "88.79", "grade": "92"},
        {"min": "85.60", "max": "87.19", "grade": "91"},
        {"min": "84.00", "max": "85.59", "grade": "90"},
        {"min": "82.40", "max": "83.99", "grade": "89"},
        {"min": "80.80", "max": "82.39", "grade": "88"},
        {"min": "79.20", "max": "80.79", "grade": "87"},
        {"min": "77.60", "max": "79.19", "grade": "86"},
        {"min": "76.00", "max": "77.59", "grade": "85"},
        {"min": "74.40", "max": "75.99", "grade": "84"},
        {"min": "72.80", "max": "74.39", "grade": "83"},
        {"min": "71.20", "max": "72.79", "grade": "82"},
        {"min": "69.60", "max": "71.19", "grade": "81"},
        {"min": "68.00", "max": "69.59", "grade": "80"},
        {"min": "66.40", "max": "67.99", "grade": "79"},
        {"min": "64.80", "max": "66.39", "grade": "78"},
        {"min": "63.20", "max": "64.79", "grade": "77"},
        {"min": "61.60", "max": "63.19", "grade": "76"},
        {"min": "60.00", "max": "61.59", "grade": "75"},
        {"min": "56.00", "max": "59.99", "grade": "74"},
        {"min": "52.00", "max": "55.99", "grade": "73"},
        {"min": "48.00", "max": "51.99", "grade": "72"},
        {"min": "44.00", "max": "47.99", "grade": "71"},
        {"min": "40.00", "max": "43.99", "grade": "70"},
        {"min": "36.00", "max": "39.99", "grade": "69"},
        {"min": "32.00", "max": "35.99", "grade": "68"},
        {"min": "28.00", "max": "31.99", "grade": "67"},
        {"min": "24.00", "max": "27.99", "grade": "66"},
        {"min": "20.00", "max": "23.99", "grade": "65"},
        {"min": "16.00", "max": "19.99", "grade": "64"},
        {"min": "12.00", "max": "15.99", "grade": "63"},
        {"min": "8.00", "max": "11.99", "grade": "62"},
        {"min": "4.00", "max": "7.99", "grade": "61"},
        {"min": "0.00", "max": "3.99", "grade": "60"},
    ]

    @staticmethod
    def _round(value: Decimal) -> Decimal:
        return value.quantize(Decimal("0.01"))

    @staticmethod
    def _round_official_grade(value: Decimal | None) -> Decimal | None:
        if value is None:
            return None
        return Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP)

    @classmethod
    def resolve_score_input_mode(
        cls,
        *,
        template_component: GradingTemplateComponent,
        template_subcomponent: GradingTemplateSubcomponent | None = None,
        template_detail: GradingTemplateDetail | None = None,
    ) -> str:
        if template_detail and getattr(template_detail, "score_input_mode", "INHERIT") != "INHERIT":
            return template_detail.score_input_mode
        if template_subcomponent and getattr(template_subcomponent, "score_input_mode", "INHERIT") != "INHERIT":
            return template_subcomponent.score_input_mode
        return getattr(template_component, "score_input_mode", "RAW_BASE50") or "RAW_BASE50"

    @classmethod
    def score_input_mode_label(cls, score_input_mode: str) -> str:
        return {
            "RAW_BASE50": "Raw Score (Base-50)",
            "DIRECT_PERCENTAGE": "Direct Percentage",
        }.get(score_input_mode, "Raw Score (Base-50)")

    @classmethod
    def aggregate_detail_scores(cls, *, subcomponent, detail_scores):
        if getattr(subcomponent, "detail_computation_mode", DetailComputationMode.WEIGHTED_DETAILS) == DetailComputationMode.AVERAGE_ACTIVITIES:
            activity_values = []
            for _weight, score in detail_scores:
                if score is None:
                    continue
                if isinstance(score, (list, tuple)):
                    activity_values.extend(Decimal(value) for value in score)
                else:
                    activity_values.append(Decimal(score))
            if not activity_values:
                return None
            return cls._round(sum(activity_values) / Decimal(len(activity_values)))

        scored_details = [(Decimal(weight or 0), Decimal(score)) for weight, score in detail_scores if score is not None]
        if not scored_details:
            return None
        total_weight = sum(Decimal(weight or 0) for weight, _score in detail_scores)
        denominator = total_weight if total_weight > 0 else Decimal("100")
        return cls._round(sum((weight / denominator) * score for weight, score in scored_details))

    @staticmethod
    def is_exam_component(component: GradingTemplateComponent) -> bool:
        return bool(getattr(component, "is_exam_component", False))

    @classmethod
    def resolve_template_for_offering_trace(cls, offering):
        assignments = (
            CourseTemplateAssignment.objects.filter(
                course_id=offering.course_id,
                is_active=True,
                grading_template__is_active=True,
                grading_template__is_published=True,
            )
            .select_related("grading_template", "effective_from_term")
            .order_by("-created_at")
        )

        exact = assignments.filter(effective_from_term_id=offering.term_id).first()
        if exact:
            return {
                "template": exact.grading_template,
                "source": "course_assignment_exact_term",
                "source_label": "Course template assignment for this exact term",
                "assignment_id": exact.id,
            }

        no_effective_term = assignments.filter(effective_from_term__isnull=True).first()
        if no_effective_term:
            return {
                "template": no_effective_term.grading_template,
                "source": "course_assignment_default",
                "source_label": "Course template assignment without a term limit",
                "assignment_id": no_effective_term.id,
            }

        profile = cls.resolve_grading_profile_for_offering(offering)
        if profile:
            return {
                "template": profile.grading_template,
                "source": "tenant_grading_profile",
                "source_label": "Tenant grading profile",
                "profile_id": profile.id,
            }

        fallback = (
            GradingTemplate.objects.filter(
                tenant_id=offering.tenant_id,
                is_active=True,
                is_published=True,
            )
            .order_by("-published_at", "-created_at")
            .first()
        )
        if fallback:
            return {
                "template": fallback,
                "source": "tenant_latest_published_fallback",
                "source_label": "Latest published tenant template fallback",
                "fallback": True,
            }
        raise ValidationError("No published grading template is assigned for this course offering.")

    @classmethod
    def resolve_template_for_offering(cls, offering):
        return cls.resolve_template_for_offering_trace(offering)["template"]

    @classmethod
    def resolve_grading_profile_for_offering(cls, offering):
        course_type = (offering.course.course_type or "").strip()
        offering_term = getattr(offering, "term", None)
        term_type = (getattr(offering_term, "term_type", "") or "").strip()
        department_ancestor_ids = ScopeService.department_ancestor_ids(offering.department_id, include_self=True)
        department_specificity_rank = {
            department_id: index
            for index, department_id in enumerate(department_ancestor_ids)
        }
        # Offerings may be shared/open and keep program null; in that case fall back to section program.
        effective_program_id = offering.program_id
        if not effective_program_id and offering.section_id:
            section_obj = getattr(offering, "section", None)
            if section_obj is None:
                section_obj = offering.section
            effective_program_id = section_obj.program_id if section_obj else None

        profiles_qs = (
            TenantGradingProfile.objects.filter(
                tenant_id=offering.tenant_id,
                is_active=True,
                grading_template__is_active=True,
                grading_template__is_published=True,
            )
            .filter(Q(campus_id=offering.campus_id) | Q(campus__isnull=True))
            .filter(
                Q(department_id__in=department_ancestor_ids)
                | Q(department__isnull=True)
            )
            .filter(Q(program_id=effective_program_id) | Q(program__isnull=True))
            .filter(Q(course_id=offering.course_id) | Q(course__isnull=True))
            .filter(Q(term_type=term_type) | Q(term_type__isnull=True) | Q(term_type=""))
            .filter(Q(effective_from_term_id=offering.term_id) | Q(effective_from_term__isnull=True))
            .select_related("grading_template")
        )
        if course_type:
            profiles_qs = profiles_qs.filter(Q(course_type__iexact=course_type) | Q(course_type__isnull=True) | Q(course_type=""))
        else:
            profiles_qs = profiles_qs.filter(
                Q(course_type__isnull=True) | Q(course_type="") | Q(course_id=offering.course_id)
            )

        profiles = list(profiles_qs)
        if not profiles:
            return None

        def specificity_score(profile):
            return (
                1 if profile.course_id else 0,
                1 if (profile.course_type or "").strip() else 0,
                1 if profile.program_id else 0,
                1 if profile.department_id else 0,
                1 if profile.campus_id else 0,
                1 if term_type and (profile.term_type or "").strip() == term_type else 0,
                1 if profile.effective_from_term_id else 0,
            )

        def sort_key(profile):
            score = specificity_score(profile)
            return (
                -score[0],
                -score[1],
                -score[2],
                -score[3],
                department_specificity_rank.get(profile.department_id, 999),
                -score[4],
                -score[5],
                -score[6],
                profile.priority,
                0 if profile.is_default else 1,
                -profile.id,
            )

        profiles.sort(key=sort_key)
        return profiles[0]

    @classmethod
    def resolve_base_value_trace(cls, offering, template):
        override = (
            CourseBaseValueOverride.objects.filter(
                course_id=offering.course_id,
                is_active=True,
            )
            .filter(Q(effective_from_term_id=offering.term_id) | Q(effective_from_term__isnull=True))
            .order_by("-effective_from_term_id", "-created_at")
            .first()
        )
        if override:
            return {
                "value": Decimal(override.base_value),
                "source": "course_override",
                "source_label": "Course base-value override",
                "override_id": override.id,
            }
        profile = cls.resolve_grading_profile_for_offering(offering)
        if profile and profile.default_base_value is not None:
            return {
                "value": Decimal(profile.default_base_value),
                "source": "tenant_grading_profile",
                "source_label": "Tenant grading profile default base value",
                "profile_id": profile.id,
            }
        if offering.course.default_base_value is not None:
            return {
                "value": Decimal(offering.course.default_base_value),
                "source": "course_default",
                "source_label": "Course default base value",
                "course_id": offering.course_id,
            }
        if template.default_base_value is not None:
            return {
                "value": Decimal(template.default_base_value),
                "source": "template_default",
                "source_label": "Grading template default base value",
                "template_id": template.id,
            }
        return {
            "value": Decimal("50"),
            "source": "system_default",
            "source_label": "System default base value",
            "fallback": True,
        }

    @classmethod
    def resolve_base_value(cls, offering, template):
        return cls.resolve_base_value_trace(offering, template)["value"]

    @classmethod
    def normalized_deped_transmutation_table(cls, table_rows=None):
        rows = table_rows or cls.DEFAULT_DEPED_TRANSMUTATION_TABLE
        normalized = []
        for row in rows:
            try:
                minimum = Decimal(str(row.get("min")))
                maximum = Decimal(str(row.get("max")))
                grade = Decimal(str(row.get("grade")))
            except (InvalidOperation, TypeError, ValueError, AttributeError):
                continue
            normalized.append(
                {
                    "min": cls._round(minimum),
                    "max": cls._round(maximum),
                    "grade": cls._round_official_grade(grade),
                }
            )
        normalized.sort(key=lambda item: item["min"], reverse=True)
        return normalized

    @classmethod
    def resolve_period_grade_strategy(cls, offering, template=None):
        profile = cls.resolve_grading_profile_for_offering(offering)
        default_mode = TenantGradingProfile.PeriodGradeFormulaMode.WEIGHTED_COMPONENTS
        if not profile:
            return {
                "mode": default_mode,
                "mode_label": default_mode.label,
                "source_label": "Template weighted components",
                "profile_id": None,
                "transmutation_table": None,
            }

        mode = profile.period_grade_formula_mode or default_mode
        mode_label = TenantGradingProfile.PeriodGradeFormulaMode(mode).label
        formula_json = profile.period_grade_formula_json or {}
        table = None
        if mode == TenantGradingProfile.PeriodGradeFormulaMode.DEPED_TRANSMUTATION:
            table = cls.normalized_deped_transmutation_table(formula_json.get("transmutation_table"))
        return {
            "mode": mode,
            "mode_label": mode_label,
            "source_label": "Tenant grading profile period-grade formula",
            "profile_id": profile.id,
            "transmutation_table": table,
        }

    @classmethod
    def transmute_deped_initial_grade(cls, initial_grade: Decimal, table_rows=None) -> Decimal:
        table = cls.normalized_deped_transmutation_table(table_rows)
        initial = cls._round(initial_grade)
        for row in table:
            if row["min"] <= initial <= row["max"]:
                return row["grade"]
        if initial > Decimal("100"):
            return Decimal("100")
        return table[-1]["grade"] if table else cls._round_official_grade(initial)

    @classmethod
    def _deped_component_percentage(cls, raw_entries):
        total_raw = Decimal("0")
        total_possible = Decimal("0")
        for raw_score, total_score in raw_entries or []:
            if raw_score is None or total_score is None:
                continue
            total_raw += Decimal(raw_score)
            total_possible += Decimal(total_score)
        if total_possible <= 0:
            return None
        return cls._round((total_raw / total_possible) * Decimal("100"))

    @classmethod
    def resolve_passing_threshold_trace(cls, offering):
        profile = cls.resolve_grading_profile_for_offering(offering)
        if profile and profile.passing_grade_threshold is not None:
            try:
                return {
                    "value": cls._round(Decimal(profile.passing_grade_threshold)),
                    "source": "tenant_grading_profile",
                    "source_label": "Tenant grading profile passing threshold",
                    "profile_id": profile.id,
                }
            except Exception:
                pass
        template = cls.resolve_template_for_offering(offering)
        if template and template.passing_grade_threshold is not None:
            try:
                return {
                    "value": cls._round(Decimal(template.passing_grade_threshold)),
                    "source": "grading_template",
                    "source_label": "Grading template passing threshold",
                    "template_id": template.id,
                }
            except Exception:
                pass
        tenant_value = SystemSettingService.get(
            "PASSING_GRADE_THRESHOLD",
            tenant_id=offering.tenant_id,
            default="75",
        )
        try:
            return {
                "value": cls._round(Decimal(str(tenant_value))),
                "source": "tenant_setting",
                "source_label": "Tenant PASSING_GRADE_THRESHOLD setting",
            }
        except Exception:
            return {
                "value": Decimal("75.00"),
                "source": "system_default",
                "source_label": "System default passing threshold",
                "fallback": True,
            }

    @classmethod
    def resolve_passing_threshold(cls, offering) -> Decimal:
        return cls.resolve_passing_threshold_trace(offering)["value"]

    @classmethod
    def resolve_final_grade_strategy(cls, offering, template=None):
        template = template or cls.resolve_template_for_offering(offering)
        active_periods = list(cls.get_template_periods(template))
        profile = cls.resolve_grading_profile_for_offering(offering)
        default_strategy = {
            "mode": TenantGradingProfile.FinalGradeFormulaMode.AVERAGE_ACTIVE_PERIODS,
            "source": "template_active_periods",
            "source_label": "Template active-period average fallback",
            "profile_id": profile.id if profile else None,
            "entries": [
                {
                    "period_id": period.id,
                    "period_code": period.code,
                    "period_name": period.name,
                    "weight": None,
                }
                for period in active_periods
            ],
            "formula_label": (
                "FG = ("
                + " + ".join(period.code for period in active_periods)
                + f") / {len(active_periods)}"
                if active_periods
                else "No active grading periods configured."
            ),
        }
        if not profile:
            return default_strategy

        if (
            profile.final_grade_formula_mode != TenantGradingProfile.FinalGradeFormulaMode.WEIGHTED_PERIODS
            or not profile.final_grade_formula_json
        ):
            return default_strategy

        weight_rows = (profile.final_grade_formula_json or {}).get("period_weights") or []
        weights_by_code = {}
        for row in weight_rows:
            code = (str(row.get("period_code") or "").strip()).upper()
            if not code:
                continue
            try:
                weight = cls._round(Decimal(str(row.get("weight") or "0")))
            except Exception:
                continue
            if weight <= 0:
                continue
            weights_by_code[code] = weight

        weighted_entries = []
        for period in active_periods:
            weight = weights_by_code.get((period.code or "").strip().upper())
            if weight is None:
                continue
            weighted_entries.append(
                {
                    "period_id": period.id,
                    "period_code": period.code,
                    "period_name": period.name,
                    "weight": weight,
                }
            )

        if not weighted_entries:
            return default_strategy

        return {
            "mode": TenantGradingProfile.FinalGradeFormulaMode.WEIGHTED_PERIODS,
            "source": "tenant_grading_profile",
            "source_label": "Tenant grading profile weighted-period formula",
            "profile_id": profile.id,
            "entries": weighted_entries,
            "formula_label": "FG = "
            + " + ".join(
                f"({entry['period_code']} x {entry['weight']:.2f}%)"
                for entry in weighted_entries
            ),
        }

    @classmethod
    def compute_final_grade_detail_from_period_values(cls, *, offering, template=None, period_values_by_period_id: dict):
        template = template or cls.resolve_template_for_offering(offering)
        final_grade_strategy = cls.resolve_final_grade_strategy(offering, template=template)
        strategy_entries = final_grade_strategy["entries"]
        if not strategy_entries:
            return {
                "strategy": final_grade_strategy,
                "entries": [],
                "raw_value": None,
                "official_value": None,
                "warnings": ["No active grading periods are configured for final-grade computation."],
            }

        has_any_period_grade = any(
            period_values_by_period_id.get(entry["period_id"]) is not None for entry in strategy_entries
        )
        if not has_any_period_grade:
            return {
                "strategy": final_grade_strategy,
                "entries": [
                    {
                        **entry,
                        "period_grade": None,
                        "value_used": None,
                        "missing": True,
                        "contribution": None,
                    }
                    for entry in strategy_entries
                ],
                "raw_value": None,
                "official_value": None,
                "warnings": ["No configured period grade is available yet."],
            }

        detail_entries = []
        warnings = []
        if final_grade_strategy["mode"] == TenantGradingProfile.FinalGradeFormulaMode.WEIGHTED_PERIODS:
            weighted_total = Decimal("0")
            for entry in strategy_entries:
                raw_period_value = period_values_by_period_id.get(entry["period_id"])
                missing = raw_period_value is None
                period_value = Decimal(raw_period_value or Decimal("0"))
                contribution = period_value * (Decimal(entry["weight"]) / Decimal("100"))
                weighted_total += contribution
                if missing:
                    warnings.append(f"{entry['period_code']} has no stored official grade and was included as 0.")
                detail_entries.append(
                    {
                        **entry,
                        "period_grade": raw_period_value,
                        "value_used": period_value,
                        "missing": missing,
                        "contribution": contribution,
                    }
                )
            return {
                "strategy": final_grade_strategy,
                "entries": detail_entries,
                "raw_value": weighted_total,
                "official_value": cls._round_official_grade(weighted_total),
                "warnings": warnings,
            }

        total_value = sum(
            (Decimal(period_values_by_period_id.get(entry["period_id"]) or Decimal("0")))
            for entry in strategy_entries
        )
        for entry in strategy_entries:
            raw_period_value = period_values_by_period_id.get(entry["period_id"])
            missing = raw_period_value is None
            period_value = Decimal(raw_period_value or Decimal("0"))
            if missing:
                warnings.append(
                    f"{entry['period_code']} has no stored official grade and was included as 0 in the divisor."
                )
            detail_entries.append(
                {
                    **entry,
                    "period_grade": raw_period_value,
                    "value_used": period_value,
                    "missing": missing,
                    "contribution": period_value / Decimal(len(strategy_entries)),
                }
            )
        raw_value = total_value / Decimal(len(strategy_entries))
        return {
            "strategy": final_grade_strategy,
            "entries": detail_entries,
            "raw_value": raw_value,
            "official_value": cls._round_official_grade(raw_value),
            "warnings": warnings,
        }

    @classmethod
    def compute_final_grade_from_period_values(cls, *, offering, template=None, period_values_by_period_id: dict):
        return cls.compute_final_grade_detail_from_period_values(
            offering=offering,
            template=template,
            period_values_by_period_id=period_values_by_period_id,
        )["official_value"]

    @staticmethod
    def get_active_enrollments(offering):
        return (
            Enrollment.objects.filter(course_offering_id=offering.id, is_active=True)
            .select_related("student")
            .order_by("student__last_name", "student__first_name", "student__student_no")
        )

    @staticmethod
    def get_template_periods(template):
        return template.periods.filter(is_active=True).order_by("sequence_no", "id")

    @staticmethod
    def user_can_manage_offering(user, offering):
        return offering.faculty_assignments.filter(
            faculty_user_id=user.id,
            is_active=True,
        ).exists()

    @classmethod
    def build_period_grade_detail_for_student(
        cls,
        *,
        offering,
        template_period: GradingTemplatePeriod,
        student_id: int,
        template=None,
        base_value: Decimal | None = None,
        components=None,
        score_lookup=None,
        raw_score_lookup=None,
        include_details: bool = False,
    ):
        template = template or cls.resolve_template_for_offering(offering)
        base_value = base_value if base_value is not None else cls.resolve_base_value(offering, template)
        period_grade_strategy = cls.resolve_period_grade_strategy(offering, template=template)
        uses_deped_transmutation = (
            period_grade_strategy["mode"] == TenantGradingProfile.PeriodGradeFormulaMode.DEPED_TRANSMUTATION
        )
        if components is None:
            components = list(
                template_period.components.filter(is_active=True)
                .prefetch_related("subcomponents", "subcomponents__details")
                .order_by("sort_order", "id")
            )
        if score_lookup is None:
            score_lookup = defaultdict(list)
            score_rows = StudentActivityScore.objects.filter(
                activity__offering_id=offering.id,
                activity__template_period_id=template_period.id,
                activity__is_active=True,
                student_id=student_id,
                is_active=True,
            ).select_related("activity")
            for score in score_rows:
                key = (
                    score.student_id,
                    score.activity.template_component_id,
                    score.activity.template_subcomponent_id,
                    score.activity.template_detail_id,
                )
                score_lookup[key].append(Decimal(score.computed_score or 0))
                if raw_score_lookup is not None:
                    raw_score_lookup[(score.student_id, score.activity.template_component_id)].append(
                        (Decimal(score.raw_score or 0), Decimal(score.activity.total_score or 0))
                    )
        if raw_score_lookup is None:
            raw_score_lookup = defaultdict(list)
            raw_score_rows = StudentActivityScore.objects.filter(
                activity__offering_id=offering.id,
                activity__template_period_id=template_period.id,
                activity__is_active=True,
                student_id=student_id,
                is_active=True,
            ).select_related("activity")
            for score in raw_score_rows:
                raw_score_lookup[(score.student_id, score.activity.template_component_id)].append(
                    (Decimal(score.raw_score or 0), Decimal(score.activity.total_score or 0))
                )

        score_by_activity_id = {}
        active_activities = []
        if include_details:
            active_activities = list(
                GradeActivity.objects.filter(
                    offering_id=offering.id,
                    template_period_id=template_period.id,
                    is_active=True,
                )
                .select_related("template_component", "template_subcomponent", "template_detail")
                .order_by(
                    "template_component__sort_order",
                    "template_subcomponent__sort_order",
                    "template_detail__sort_order",
                    "activity_date",
                    "id",
                )
            )
            score_by_activity_id = {
                score.activity_id: score
                for score in StudentActivityScore.objects.filter(
                    student_id=student_id,
                    activity_id__in=[activity.id for activity in active_activities],
                    is_active=True,
                    activity__is_active=True,
                ).select_related("activity")
            }

        def activity_rows_for(component, subcomponent=None, detail=None):
            if not include_details:
                return []
            rows = []
            for activity in active_activities:
                if activity.template_component_id != component.id:
                    continue
                if (activity.template_subcomponent_id or None) != (subcomponent.id if subcomponent else None):
                    continue
                if (activity.template_detail_id or None) != (detail.id if detail else None):
                    continue
                score = score_by_activity_id.get(activity.id)
                score_input_mode = cls.resolve_score_input_mode(
                    template_component=activity.template_component,
                    template_subcomponent=activity.template_subcomponent,
                    template_detail=activity.template_detail,
                )
                rows.append(
                    {
                        "id": activity.id,
                        "title": activity.title,
                        "total_score": activity.total_score,
                        "score_input_mode": score_input_mode,
                        "score_input_mode_label": cls.score_input_mode_label(score_input_mode),
                        "raw_score": score.raw_score if score else None,
                        "computed_score": score.computed_score if score else None,
                        "missing": score is None,
                    }
                )
            return rows

        def attendance_rows():
            if not include_details:
                return []
            sessions = list(
                AttendanceSession.objects.filter(
                    offering_id=offering.id,
                    template_period_id=template_period.id,
                    is_active=True,
                ).order_by("session_date", "id")
            )
            records_by_session_id = {
                record.session_id: record
                for record in AttendanceRecord.objects.filter(
                    session__offering_id=offering.id,
                    session__template_period_id=template_period.id,
                    session__is_active=True,
                    student_id=student_id,
                    is_active=True,
                ).select_related("session")
            }
            rows = []
            for session in sessions:
                record = records_by_session_id.get(session.id)
                raw = cls.ATTENDANCE_SCORE_MAP.get(record.status_code, Decimal("0")) if record else None
                rows.append(
                    {
                        "session_id": session.id,
                        "title": session.title or session.session_date,
                        "session_date": session.session_date,
                        "status_code": record.status_code if record else None,
                        "status_label": record.get_status_code_display() if record else "Missing",
                        "mapped_score": raw,
                        "computed_score": (
                            cls.compute_activity_score(
                                raw_score=raw,
                                total_score=Decimal("100"),
                                base_value=base_value,
                                score_input_mode="DIRECT_PERCENTAGE",
                            )
                            if raw is not None
                            else None
                        ),
                        "missing": record is None,
                    }
                )
            return rows

        component_scores = {}
        component_breakdown = []
        class_standing_raw = Decimal("0")
        exam_grade_raw = None
        weighted_period_grade = Decimal("0")
        has_exam_component = False
        has_exam_data = False
        warnings = []

        for component in components:
            subcomponents = list(component.subcomponents.filter(is_active=True).order_by("sort_order", "id"))
            component_has_data = False
            subcomponent_breakdown = []
            if uses_deped_transmutation:
                component_score = cls._deped_component_percentage(raw_score_lookup.get((student_id, component.id)))
                component_has_data = component_score is not None
                component_raw_value = component_score
            elif subcomponents:
                sub_total = sum(Decimal(sub.weight_percentage or 0) for sub in subcomponents)
                sub_denominator = sub_total if sub_total > 0 else Decimal("100")
                component_raw = Decimal("0")
                for sub in subcomponents:
                    detail_rows = list(sub.details.filter(is_active=True).order_by("sort_order", "id"))
                    detail_breakdown = []
                    sub_activity_rows = []
                    sub_attendance_rows = []
                    if sub.is_attendance_component:
                        sub_score = cls._attendance_subcomponent_score(
                            offering=offering,
                            template_period=template_period,
                            student_id=student_id,
                            base_value=base_value,
                        )
                        sub_attendance_rows = attendance_rows()
                    elif detail_rows:
                        detail_has_data = False
                        detail_scores = []
                        for detail in detail_rows:
                            detail_key = (student_id, component.id, sub.id, detail.id)
                            detail_score = cls._average_score_or_none(
                                score_lookup,
                                detail_key,
                            )
                            detail_scores.append(
                                (
                                    Decimal(detail.weight_percentage or 0),
                                    score_lookup.get(detail_key, []) if sub.detail_computation_mode == DetailComputationMode.AVERAGE_ACTIVITIES else detail_score,
                                )
                            )
                            if detail_score is not None:
                                detail_has_data = True
                            detail_breakdown.append(
                                {
                                    "id": detail.id,
                                    "code": detail.code,
                                    "name": detail.name,
                                    "weight": detail.weight_percentage,
                                    "score": detail_score,
                                    "activities": activity_rows_for(component, sub, detail),
                                }
                            )
                        sub_score = cls.aggregate_detail_scores(
                            subcomponent=sub,
                            detail_scores=detail_scores,
                        )
                        if detail_has_data:
                            component_has_data = True
                    else:
                        sub_score = cls._average_score_or_none(
                            score_lookup,
                            (student_id, component.id, sub.id, None),
                        )
                        sub_activity_rows = activity_rows_for(component, sub, None)
                        if sub_score is not None:
                            component_has_data = True
                    if sub_score is not None:
                        component_raw += (Decimal(sub.weight_percentage) / sub_denominator) * sub_score
                    subcomponent_breakdown.append(
                        {
                            "id": sub.id,
                            "code": sub.code,
                            "name": sub.name,
                            "weight": sub.weight_percentage,
                            "score": sub_score,
                            "is_attendance_component": sub.is_attendance_component,
                            "details": detail_breakdown,
                            "activities": sub_activity_rows,
                            "attendance_records": sub_attendance_rows,
                        }
                    )
                component_score = cls._round(component_raw)
                component_raw_value = component_raw
            else:
                component_score = cls._average_score_or_none(
                    score_lookup,
                    (student_id, component.id, None, None),
                )
                component_has_data = component_score is not None
                component_raw_value = component_score
            component_scores[component.code] = component_score

            if cls.is_exam_component(component):
                has_exam_component = True
                if component_has_data:
                    exam_grade_raw = (exam_grade_raw or Decimal("0")) + component_score
                    has_exam_data = True
            else:
                class_standing_raw += component_score or Decimal("0")
            weighted_contribution = (Decimal(component.weight_percentage) / Decimal("100")) * (
                component_score or Decimal("0")
            )
            weighted_period_grade += weighted_contribution
            component_breakdown.append(
                {
                    "id": component.id,
                    "code": component.code,
                    "name": component.name,
                    "weight": component.weight_percentage,
                    "is_exam_component": cls.is_exam_component(component),
                    "score": component_score,
                    "raw_score": component_raw_value,
                    "weighted_contribution": weighted_contribution,
                    "subcomponents": subcomponent_breakdown,
                    "activities": activity_rows_for(component, None, None) if not subcomponents else [],
                }
            )

        class_standing = cls._round_official_grade(class_standing_raw)
        exam_grade = cls._round_official_grade(exam_grade_raw)
        period_grade = None
        if not has_exam_component or has_exam_data:
            period_grade = cls._round_official_grade(weighted_period_grade)
            if uses_deped_transmutation:
                period_grade = cls.transmute_deped_initial_grade(
                    weighted_period_grade,
                    period_grade_strategy.get("transmutation_table"),
                )
        elif has_exam_component and not has_exam_data:
            warnings.append("This period has an exam component, but no exam data is available yet.")

        return {
            "component_scores": component_scores,
            "component_breakdown": component_breakdown,
            "class_standing_raw": class_standing_raw,
            "exam_grade_raw": exam_grade_raw,
            "period_grade_raw": weighted_period_grade if period_grade is not None else None,
            "period_grade_strategy": period_grade_strategy,
            "class_standing": class_standing,
            "exam_grade": exam_grade,
            "period_grade": period_grade,
            "has_exam_component": has_exam_component,
            "has_exam_data": has_exam_data,
            "warnings": warnings,
        }

    @classmethod
    def compute_activity_score(
        cls,
        *,
        raw_score: Decimal,
        total_score: Decimal,
        base_value: Decimal,
        score_input_mode: str = "RAW_BASE50",
    ):
        if raw_score < 0:
            raise ValidationError("Score cannot be negative.")
        if score_input_mode == "DIRECT_PERCENTAGE":
            if raw_score > Decimal("100"):
                raise ValidationError("Direct percentage score cannot be greater than 100.")
            return cls._round(raw_score)
        if total_score <= 0:
            raise ValidationError("Total score must be greater than zero.")
        computed = ((raw_score / total_score) * base_value) + (Decimal("100") - base_value)
        return cls._round(computed)

    @classmethod
    def _validate_activity_structure(
        cls,
        *,
        template_period: GradingTemplatePeriod,
        template_component: GradingTemplateComponent,
        template_subcomponent: GradingTemplateSubcomponent | None,
        template_detail: GradingTemplateDetail | None,
    ):
        if template_component.template_period_id != template_period.id:
            raise ValidationError("Selected component does not belong to selected period.")
        has_subcomponents = template_component.subcomponents.filter(is_active=True).exists()
        if has_subcomponents and not template_subcomponent:
            raise ValidationError("Selected component requires a subcomponent.")
        if template_subcomponent and template_subcomponent.template_component_id != template_component.id:
            raise ValidationError("Selected subcomponent does not belong to selected component.")
        has_details = (
            template_subcomponent.details.filter(is_active=True).exists() if template_subcomponent else False
        )
        if has_details and not template_detail:
            raise ValidationError("Selected subcomponent requires a detail selection.")
        if template_detail and not template_subcomponent:
            raise ValidationError("Detail requires selected subcomponent.")
        if template_detail and template_detail.template_subcomponent_id != template_subcomponent.id:
            raise ValidationError("Selected detail does not belong to selected subcomponent.")

    @classmethod
    @transaction.atomic
    def create_activity(
        cls,
        *,
        user,
        offering,
        template_period: GradingTemplatePeriod,
        template_component: GradingTemplateComponent,
        template_subcomponent: GradingTemplateSubcomponent | None,
        template_detail: GradingTemplateDetail | None,
        title: str,
        total_score: Decimal,
        activity_date,
    ):
        cls._validate_activity_structure(
            template_period=template_period,
            template_component=template_component,
            template_subcomponent=template_subcomponent,
            template_detail=template_detail,
        )
        score_input_mode = cls.resolve_score_input_mode(
            template_component=template_component,
            template_subcomponent=template_subcomponent,
            template_detail=template_detail,
        )
        GradingGovernanceService.assert_encoding_allowed(offering=offering, template_period=template_period)

        activity = GradeActivity.objects.create(
            tenant_id=offering.tenant_id,
            campus_id=offering.campus_id,
            offering=offering,
            template_period=template_period,
            template_component=template_component,
            template_subcomponent=template_subcomponent,
            template_detail=template_detail,
            title=title.strip(),
            total_score=Decimal("100") if score_input_mode == "DIRECT_PERCENTAGE" else total_score,
            activity_date=activity_date,
            created_by_user=user,
            is_active=True,
        )
        cls._mark_prediction_dirty(
            offering=offering,
            template_period=template_period,
            reason="ACTIVITY_CHANGE",
        )
        return activity

    @classmethod
    @transaction.atomic
    def update_activity(
        cls,
        *,
        user,
        activity: GradeActivity,
        template_period: GradingTemplatePeriod,
        template_component: GradingTemplateComponent,
        template_subcomponent: GradingTemplateSubcomponent | None,
        template_detail: GradingTemplateDetail | None,
        title: str,
        total_score: Decimal,
        activity_date,
    ):
        cls._validate_activity_structure(
            template_period=template_period,
            template_component=template_component,
            template_subcomponent=template_subcomponent,
            template_detail=template_detail,
        )
        GradingGovernanceService.assert_encoding_allowed(
            offering=activity.offering,
            template_period=template_period,
        )
        score_input_mode = cls.resolve_score_input_mode(
            template_component=template_component,
            template_subcomponent=template_subcomponent,
            template_detail=template_detail,
        )

        activity.template_component = template_component
        activity.template_subcomponent = template_subcomponent
        activity.template_detail = template_detail
        activity.title = title.strip()
        activity.total_score = Decimal("100") if score_input_mode == "DIRECT_PERCENTAGE" else total_score
        activity.activity_date = activity_date
        activity.save(
            update_fields=[
                "template_component",
                "template_subcomponent",
                "template_detail",
                "title",
                "total_score",
                "activity_date",
                "updated_at",
            ]
        )

        recomputed_score_count = 0
        active_scores = list(activity.student_scores.filter(is_active=True))
        if active_scores:
            template = cls.resolve_template_for_offering(activity.offering)
            base_value = cls.resolve_base_value(activity.offering, template)
            for score in active_scores:
                score.computed_score = cls.compute_activity_score(
                    raw_score=Decimal(score.raw_score or 0),
                    total_score=Decimal(activity.total_score),
                    base_value=base_value,
                    score_input_mode=score_input_mode,
                )
                score.encoded_by_user = user
                score.save(update_fields=["computed_score", "encoded_by_user", "updated_at"])
                recomputed_score_count += 1

        cls.recompute_period_summary(
            user=user,
            offering=activity.offering,
            template_period=template_period,
        )
        return activity, recomputed_score_count

    @classmethod
    @transaction.atomic
    def upsert_activity_scores(
        cls,
        *,
        user,
        activity: GradeActivity,
        score_payload: list[dict],
        recompute: bool = True,
        audit_reason: str | None = "SCORE_WRITE",
        audit_portal: str = "FACULTY",
    ):
        template = cls.resolve_template_for_offering(activity.offering)
        base_value = cls.resolve_base_value(activity.offering, template)
        score_input_mode = cls.resolve_score_input_mode(
            template_component=activity.template_component,
            template_subcomponent=activity.template_subcomponent,
            template_detail=activity.template_detail,
        )
        enrolled_student_ids = set(
            cls.get_active_enrollments(activity.offering).values_list("student_id", flat=True)
        )

        saved = 0
        affected_student_ids = set()
        for row in score_payload:
            student_id = int(row["student_id"])
            if student_id not in enrolled_student_ids:
                continue
            affected_student_ids.add(student_id)
            GradingGovernanceService.assert_encoding_allowed(
                offering=activity.offering,
                template_period=activity.template_period,
                student_id=student_id,
                activity_id=activity.id,
                requested_action=GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE,
            )
            if row.get("clear"):
                StudentActivityScore.objects.filter(
                    activity=activity,
                    student_id=student_id,
                    is_active=True,
                ).update(is_active=False)
                continue
            raw = Decimal(str(row.get("raw_score", "0") or "0"))
            remarks = row.get("remarks") or ""
            computed = cls.compute_activity_score(
                raw_score=raw,
                total_score=Decimal(activity.total_score),
                base_value=base_value,
                score_input_mode=score_input_mode,
            )
            StudentActivityScore.objects.update_or_create(
                activity=activity,
                student_id=student_id,
                defaults={
                    "raw_score": raw,
                    "computed_score": computed,
                    "encoded_by_user": user,
                    "remarks": remarks[:255] if remarks else None,
                    "is_active": True,
                },
            )
            saved += 1
        if recompute and affected_student_ids:
            cls.recompute_period_summary_for_students(
                user=user,
                offering=activity.offering,
                template_period=activity.template_period,
                student_ids=affected_student_ids,
                audit_reason=audit_reason,
                audit_portal=audit_portal,
            )
        cls._mark_prediction_dirty(
            offering=activity.offering,
            template_period=activity.template_period,
            reason="SCORE_CHANGE",
        )
        return saved

    @classmethod
    @transaction.atomic
    def create_or_update_attendance_session(
        cls,
        *,
        user,
        offering,
        template_period: GradingTemplatePeriod,
        session_date,
        title: str | None = None,
    ):
        GradingGovernanceService.assert_encoding_allowed(offering=offering, template_period=template_period)
        if isinstance(session_date, str):
            parsed = parse_date(session_date)
            if parsed is None:
                raise ValidationError("Invalid session date.")
            session_date = parsed

        session, created = AttendanceSession.objects.update_or_create(
            offering=offering,
            template_period=template_period,
            session_date=session_date,
            defaults={
                "tenant_id": offering.tenant_id,
                "campus_id": offering.campus_id,
                "title": (title or "").strip() or None,
                "created_by_user": user,
                "is_active": True,
            },
        )
        cls._mark_prediction_dirty(
            offering=offering,
            template_period=template_period,
            reason="ATTENDANCE_CHANGE",
        )
        return session, created

    @classmethod
    @transaction.atomic
    def archive_activity(cls, *, user, activity: GradeActivity):
        GradingGovernanceService.assert_encoding_allowed(
            offering=activity.offering,
            template_period=activity.template_period,
        )

        activity.is_active = False
        activity.save(update_fields=["is_active", "updated_at"])
        activity.student_scores.filter(is_active=True).update(is_active=False, updated_at=timezone.now())

        cls.recompute_period_summary(
            user=user,
            offering=activity.offering,
            template_period=activity.template_period,
        )
        cls._mark_prediction_dirty(
            offering=activity.offering,
            template_period=activity.template_period,
            reason="ACTIVITY_CHANGE",
        )
        return activity

    @classmethod
    @transaction.atomic
    def upsert_attendance_records(cls, *, user, session: AttendanceSession, status_payload: list[dict]):
        enrolled_student_ids = set(
            cls.get_active_enrollments(session.offering).values_list("student_id", flat=True)
        )
        saved = 0
        affected_student_ids = set()
        for row in status_payload:
            student_id = int(row["student_id"])
            if student_id not in enrolled_student_ids:
                continue
            affected_student_ids.add(student_id)
            GradingGovernanceService.assert_encoding_allowed(
                offering=session.offering,
                template_period=session.template_period,
                student_id=student_id,
                requested_action=GradeCorrectionRequestItem.RequestedAction.UPDATE_ATTENDANCE,
            )
            status_code = str(row.get("status_code") or AttendanceRecord.Status.PRESENT).upper()
            if status_code not in cls.ATTENDANCE_SCORE_MAP:
                status_code = AttendanceRecord.Status.PRESENT
            remarks = (row.get("remarks") or "").strip()
            AttendanceRecord.objects.update_or_create(
                session=session,
                student_id=student_id,
                defaults={
                    "tenant_id": session.tenant_id,
                    "campus_id": session.campus_id,
                    "status_code": status_code,
                    "recorded_by_user": user,
                    "remarks": remarks[:255] if remarks else None,
                    "is_active": True,
                },
            )
            saved += 1
        if affected_student_ids:
            cls.recompute_period_summary_for_students(
                user=user,
                offering=session.offering,
                template_period=session.template_period,
                student_ids=affected_student_ids,
                audit_reason="ATTENDANCE_WRITE",
                audit_portal="FACULTY",
            )
        cls._mark_prediction_dirty(
            offering=session.offering,
            template_period=session.template_period,
            reason="ATTENDANCE_CHANGE",
        )
        return saved

    @classmethod
    def _attendance_subcomponent_score(
        cls,
        *,
        offering,
        template_period: GradingTemplatePeriod,
        student_id: int,
        base_value: Decimal,
    ):
        records = AttendanceRecord.objects.filter(
            session__offering_id=offering.id,
            session__template_period_id=template_period.id,
            session__is_active=True,
            student_id=student_id,
            is_active=True,
        ).select_related("session")

        if not records.exists():
            return None

        computed_scores = []
        for record in records:
            raw = cls.ATTENDANCE_SCORE_MAP.get(record.status_code, Decimal("0"))
            computed = cls.compute_activity_score(
                raw_score=raw,
                total_score=Decimal("100"),
                base_value=base_value,
                score_input_mode="DIRECT_PERCENTAGE",
            )
            computed_scores.append(computed)
        return cls._round(sum(computed_scores) / Decimal(len(computed_scores)))

    @classmethod
    def _average_score_or_none(cls, score_lookup, key):
        vals = score_lookup.get(key, [])
        if not vals:
            return None
        return cls._round(sum(vals) / Decimal(len(vals)))

    @staticmethod
    def _period_grade_audit_snapshot(row: StudentPeriodGrade | None):
        if row is None:
            return None
        return {
            "id": row.id,
            "offering_id": row.offering_id,
            "template_period_id": row.template_period_id,
            "student_id": row.student_id,
            "class_standing_grade": row.class_standing_grade,
            "exam_grade": row.exam_grade,
            "period_grade": row.period_grade,
            "is_finalized": row.is_finalized,
        }

    @staticmethod
    def _final_grade_audit_snapshot(row: StudentFinalGrade | None):
        if row is None:
            return None
        return {
            "id": row.id,
            "offering_id": row.offering_id,
            "student_id": row.student_id,
            "final_grade": row.final_grade,
            "is_submitted": row.is_submitted,
        }

    @classmethod
    def recompute_final_grades_from_stored_periods(cls, *, user, offering, template=None):
        return cls.recompute_final_grades_for_students(
            user=user,
            offering=offering,
            student_ids=None,
            template=template,
            audit_reason="FINAL_GRADE_RECOMPUTE",
        )

    @classmethod
    def recompute_final_grades_for_students(
        cls,
        *,
        user,
        offering,
        student_ids,
        template=None,
        audit_reason: str | None = "FINAL_GRADE_RECOMPUTE",
        audit_portal: str = "SYSTEM",
        audit_actor=None,
        is_submitted: bool = False,
    ):
        template = template or cls.resolve_template_for_offering(offering)
        normalized_student_ids = None
        if student_ids is not None:
            normalized_student_ids = {int(student_id) for student_id in student_ids if student_id is not None}
            if not normalized_student_ids:
                return []
        enrollment_qs = cls.get_active_enrollments(offering)
        if normalized_student_ids is not None:
            enrollment_qs = enrollment_qs.filter(student_id__in=normalized_student_ids)
        enrollments = list(enrollment_qs)
        final_grade_strategy = cls.resolve_final_grade_strategy(offering, template=template)
        strategy_entries = final_grade_strategy["entries"]
        strategy_period_ids = [entry["period_id"] for entry in strategy_entries]
        passing_threshold = cls.resolve_passing_threshold(offering)
        target_student_ids = {enrollment.student_id for enrollment in enrollments}
        if not target_student_ids:
            return []
        period_grade_map = defaultdict(dict)
        existing_period_rows = StudentPeriodGrade.objects.filter(
            offering=offering,
            template_period_id__in=strategy_period_ids,
            student_id__in=target_student_ids,
        )
        for row in existing_period_rows:
            if row.period_grade is not None:
                period_grade_map[row.student_id][row.template_period_id] = Decimal(row.period_grade)

        before_map = {
            row.student_id: cls._final_grade_audit_snapshot(row)
            for row in StudentFinalGrade.objects.filter(
                offering=offering,
                student_id__in=target_student_ids,
            )
        }
        changed_rows = []
        actor = audit_actor or user
        for enrollment in enrollments:
            student_id = enrollment.student_id
            if enrollment.enrollment_status in Enrollment.NON_ACTIVE_GRADING_STATUSES:
                final_value = None
            else:
                student_period_values = period_grade_map.get(student_id, {})
                final_value = cls.compute_final_grade_from_period_values(
                    offering=offering,
                    template=template,
                    period_values_by_period_id=student_period_values,
                )
            final_row, _created = StudentFinalGrade.objects.update_or_create(
                offering=offering,
                student_id=student_id,
                defaults={
                    "tenant_id": offering.tenant_id,
                    "campus_id": offering.campus_id,
                    "final_grade": final_value,
                    "computed_by_user": user,
                    "is_submitted": is_submitted,
                },
            )
            after_snapshot = cls._final_grade_audit_snapshot(final_row)
            before_snapshot = before_map.get(student_id)
            if before_snapshot != after_snapshot:
                changed_rows.append(final_row)
                if audit_reason:
                    AuditService.log_event(
                        action="RECOMPUTE",
                        portal=audit_portal,
                        entity_type="StudentFinalGrade",
                        entity_id=final_row.id,
                        actor=actor,
                        tenant=offering.tenant,
                        campus=offering.campus,
                        before_data=before_snapshot,
                        after_data=after_snapshot,
                        metadata={
                            "reason": audit_reason,
                            "offering_id": offering.id,
                            "student_id": student_id,
                            "template_id": template.id,
                            "formula_mode": final_grade_strategy["mode"],
                            "period_ids": strategy_period_ids,
                            "passing_threshold": str(passing_threshold),
                        },
                    )
        return changed_rows

    @staticmethod
    def _mark_prediction_dirty(*, offering, template_period, reason: str):
        try:
            from apps.predictions.services import PredictionDirtyQueueService

            PredictionDirtyQueueService.mark_dirty(
                offering=offering,
                template_period=template_period,
                reason=reason,
            )
        except Exception:
            return

    @classmethod
    @transaction.atomic
    def recompute_period_summary_for_students(
        cls,
        *,
        user,
        offering,
        template_period: GradingTemplatePeriod,
        student_ids,
        audit_reason: str | None = "PERIOD_GRADE_RECOMPUTE",
        audit_portal: str = "SYSTEM",
        audit_actor=None,
        period_is_finalized: bool = False,
        final_is_submitted: bool = False,
    ):
        return cls.recompute_period_summary(
            user=user,
            offering=offering,
            template_period=template_period,
            student_ids=student_ids,
            audit_reason=audit_reason,
            audit_portal=audit_portal,
            audit_actor=audit_actor,
            period_is_finalized=period_is_finalized,
            final_is_submitted=final_is_submitted,
        )

    @classmethod
    @transaction.atomic
    def recompute_period_summary(
        cls,
        *,
        user,
        offering,
        template_period: GradingTemplatePeriod,
        student_ids=None,
        audit_reason: str | None = "PERIOD_GRADE_RECOMPUTE",
        audit_portal: str = "SYSTEM",
        audit_actor=None,
        period_is_finalized: bool = False,
        final_is_submitted: bool = False,
    ):
        GradingGovernanceService.assert_summary_compute_allowed(
            offering=offering,
            template_period=template_period,
        )
        template = cls.resolve_template_for_offering(offering)
        base_value = cls.resolve_base_value(offering, template)
        passing_threshold = cls.resolve_passing_threshold(offering)

        normalized_student_ids = None
        if student_ids is not None:
            normalized_student_ids = {int(student_id) for student_id in student_ids if student_id is not None}
            if not normalized_student_ids:
                return {
                    "rows": [],
                    "component_codes": [],
                    "base_value": base_value,
                }
        enrollment_qs = cls.get_active_enrollments(offering)
        if normalized_student_ids is not None:
            enrollment_qs = enrollment_qs.filter(student_id__in=normalized_student_ids)
        enrollments = list(enrollment_qs)
        target_student_ids = {enrollment.student_id for enrollment in enrollments}
        components = list(
            template_period.components.filter(is_active=True)
            .prefetch_related("subcomponents", "subcomponents__details")
            .order_by("sort_order", "id")
        )

        activity_scores = StudentActivityScore.objects.filter(
            activity__offering_id=offering.id,
            activity__template_period_id=template_period.id,
            activity__is_active=True,
            is_active=True,
        ).select_related("activity")

        score_lookup = defaultdict(list)
        raw_score_lookup = defaultdict(list)
        for score in activity_scores:
            key = (
                score.student_id,
                score.activity.template_component_id,
                score.activity.template_subcomponent_id,
                score.activity.template_detail_id,
            )
            score_lookup[key].append(Decimal(score.computed_score or 0))
            raw_score_lookup[(score.student_id, score.activity.template_component_id)].append(
                (Decimal(score.raw_score or 0), Decimal(score.activity.total_score or 0))
            )

        rows = []
        before_period_map = {
            row.student_id: cls._period_grade_audit_snapshot(row)
            for row in StudentPeriodGrade.objects.filter(
                offering=offering,
                template_period=template_period,
                student_id__in=target_student_ids,
            )
        }
        actor = audit_actor or user

        for enrollment in enrollments:
            student = enrollment.student
            student_id = student.id

            if enrollment.enrollment_status in Enrollment.NON_ACTIVE_GRADING_STATUSES:
                period_row, _created = StudentPeriodGrade.objects.update_or_create(
                    offering=offering,
                    template_period=template_period,
                    student=student,
                    defaults={
                        "tenant_id": offering.tenant_id,
                        "campus_id": offering.campus_id,
                        "class_standing_grade": None,
                        "exam_grade": None,
                        "period_grade": None,
                        "computed_by_user": user,
                        "is_finalized": period_is_finalized,
                    },
                )
                after_snapshot = cls._period_grade_audit_snapshot(period_row)
                before_snapshot = before_period_map.get(student_id)
                if before_snapshot != after_snapshot and audit_reason:
                    AuditService.log_event(
                        action="RECOMPUTE",
                        portal=audit_portal,
                        entity_type="StudentPeriodGrade",
                        entity_id=period_row.id,
                        actor=actor,
                        tenant=offering.tenant,
                        campus=offering.campus,
                        before_data=before_snapshot,
                        after_data=after_snapshot,
                        metadata={
                            "reason": audit_reason,
                            "offering_id": offering.id,
                            "student_id": student_id,
                            "template_period_id": template_period.id,
                            "passing_threshold": str(passing_threshold),
                        },
                    )
                rows.append(
                    {
                        "student": student,
                        "enrollment_status": enrollment.enrollment_status,
                        "component_scores": {},
                        "class_standing": None,
                        "exam_grade": None,
                        "period_grade_raw": None,
                        "period_grade": None,
                    }
                )
                continue

            detail = cls.build_period_grade_detail_for_student(
                offering=offering,
                template_period=template_period,
                student_id=student_id,
                template=template,
                base_value=base_value,
                components=components,
                score_lookup=score_lookup,
                raw_score_lookup=raw_score_lookup,
            )
            component_scores = detail["component_scores"]
            class_standing = detail["class_standing"]
            exam_grade = detail["exam_grade"]
            period_grade = detail["period_grade"]

            period_row, _created = StudentPeriodGrade.objects.update_or_create(
                offering=offering,
                template_period=template_period,
                student=student,
                defaults={
                    "tenant_id": offering.tenant_id,
                    "campus_id": offering.campus_id,
                    "class_standing_grade": class_standing,
                    "exam_grade": exam_grade,
                    "period_grade": period_grade,
                    "computed_by_user": user,
                    "is_finalized": period_is_finalized,
                },
            )
            after_snapshot = cls._period_grade_audit_snapshot(period_row)
            before_snapshot = before_period_map.get(student_id)
            if before_snapshot != after_snapshot and audit_reason:
                AuditService.log_event(
                    action="RECOMPUTE",
                    portal=audit_portal,
                    entity_type="StudentPeriodGrade",
                    entity_id=period_row.id,
                    actor=actor,
                    tenant=offering.tenant,
                    campus=offering.campus,
                    before_data=before_snapshot,
                    after_data=after_snapshot,
                    metadata={
                        "reason": audit_reason,
                        "offering_id": offering.id,
                        "student_id": student_id,
                        "template_period_id": template_period.id,
                        "passing_threshold": str(passing_threshold),
                    },
                )
            rows.append(
                {
                    "student": student,
                    "enrollment_status": enrollment.enrollment_status,
                    "component_scores": component_scores,
                    "class_standing": class_standing,
                    "exam_grade": exam_grade,
                    "period_grade_raw": detail.get("period_grade_raw"),
                    "period_grade": period_grade,
                }
            )

        cls.recompute_final_grades_for_students(
            user=user,
            offering=offering,
            student_ids=target_student_ids if normalized_student_ids is not None else None,
            template=template,
            audit_reason=audit_reason,
            audit_portal=audit_portal,
            audit_actor=actor,
            is_submitted=final_is_submitted,
        )

        cls._mark_prediction_dirty(
            offering=offering,
            template_period=template_period,
            reason="SCORE_CHANGE",
        )

        return {
            "rows": rows,
            "component_codes": [c.code for c in components],
            "base_value": base_value,
        }

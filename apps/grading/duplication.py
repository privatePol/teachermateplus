from __future__ import annotations

from copy import deepcopy

from django.db import transaction

from apps.grading.models import (
    GradingTemplate,
    GradingTemplateComponent,
    GradingTemplateDetail,
    GradingTemplatePeriod,
    GradingTemplateSubcomponent,
    TenantGradingProfile,
)


def _copy_label(value: str, *, max_length: int) -> str:
    label = f"Copy of {(value or '').strip()}"
    return label[:max_length]


def _unique_code(model, *, tenant_id: int, field_name: str, source_code: str) -> str:
    max_length = model._meta.get_field(field_name).max_length
    base = f"{(source_code or 'COPY').strip()}_COPY"
    base = base[:max_length]
    candidate = base
    counter = 2
    while model.objects.filter(tenant_id=tenant_id, **{field_name: candidate}).exists():
        suffix = f"_{counter}"
        candidate = f"{base[: max_length - len(suffix)]}{suffix}"
        counter += 1
    return candidate


class GradingTemplateDuplicationService:
    @staticmethod
    @transaction.atomic
    def duplicate_template(*, source: GradingTemplate) -> tuple[GradingTemplate, dict]:
        duplicate = GradingTemplate.objects.create(
            tenant=source.tenant,
            code=_unique_code(GradingTemplate, tenant_id=source.tenant_id, field_name="code", source_code=source.code),
            name=_copy_label(source.name, max_length=GradingTemplate._meta.get_field("name").max_length),
            description=source.description,
            default_base_value=source.default_base_value,
            passing_grade_threshold=source.passing_grade_threshold,
            approval_status=GradingTemplate.ApprovalStatus.DRAFT,
            approval_requested_by=None,
            approval_requested_at=None,
            approval_reviewed_by=None,
            approval_reviewed_at=None,
            approval_remarks=None,
            is_published=False,
            published_at=None,
            published_by=None,
            is_active=True,
        )

        counts = {"periods": 0, "components": 0, "subcomponents": 0, "details": 0}
        periods = source.periods.all().order_by("sequence_no", "id")
        for period in periods:
            copied_period = GradingTemplatePeriod.objects.create(
                template=duplicate,
                code=period.code,
                name=period.name,
                sequence_no=period.sequence_no,
                weight_percentage=period.weight_percentage,
                is_active=period.is_active,
            )
            counts["periods"] += 1

            components = period.components.all().order_by("sort_order", "id")
            for component in components:
                copied_component = GradingTemplateComponent.objects.create(
                    template_period=copied_period,
                    code=component.code,
                    name=component.name,
                    weight_percentage=component.weight_percentage,
                    sort_order=component.sort_order,
                    score_input_mode=component.score_input_mode,
                    is_exam_component=component.is_exam_component,
                    is_active=component.is_active,
                )
                counts["components"] += 1

                subcomponents = component.subcomponents.all().order_by("sort_order", "id")
                for subcomponent in subcomponents:
                    copied_subcomponent = GradingTemplateSubcomponent.objects.create(
                        template_component=copied_component,
                        code=subcomponent.code,
                        name=subcomponent.name,
                        weight_percentage=subcomponent.weight_percentage,
                        sort_order=subcomponent.sort_order,
                        score_input_mode=subcomponent.score_input_mode,
                        is_attendance_component=subcomponent.is_attendance_component,
                        admin_locked=subcomponent.admin_locked,
                        is_active=subcomponent.is_active,
                    )
                    counts["subcomponents"] += 1

                    details = subcomponent.details.all().order_by("sort_order", "id")
                    for detail in details:
                        GradingTemplateDetail.objects.create(
                            template_subcomponent=copied_subcomponent,
                            code=detail.code,
                            name=detail.name,
                            weight_percentage=detail.weight_percentage,
                            sort_order=detail.sort_order,
                            score_input_mode=detail.score_input_mode,
                            admin_locked=detail.admin_locked,
                            is_active=detail.is_active,
                        )
                        counts["details"] += 1

        return duplicate, counts

    @staticmethod
    @transaction.atomic
    def duplicate_profile(*, source: TenantGradingProfile) -> TenantGradingProfile:
        return TenantGradingProfile.objects.create(
            tenant=source.tenant,
            campus=source.campus,
            department=source.department,
            program=source.program,
            course=source.course,
            course_type=source.course_type,
            term_type=source.term_type,
            profile_code=_unique_code(
                TenantGradingProfile,
                tenant_id=source.tenant_id,
                field_name="profile_code",
                source_code=source.profile_code,
            ),
            profile_name=_copy_label(
                source.profile_name,
                max_length=TenantGradingProfile._meta.get_field("profile_name").max_length,
            ),
            grading_template=source.grading_template,
            default_base_value=source.default_base_value,
            passing_grade_threshold=source.passing_grade_threshold,
            period_grade_formula_mode=source.period_grade_formula_mode,
            period_grade_formula_json=deepcopy(source.period_grade_formula_json),
            final_grade_formula_mode=source.final_grade_formula_mode,
            final_grade_formula_json=deepcopy(source.final_grade_formula_json),
            priority=source.priority,
            effective_from_term=source.effective_from_term,
            is_default=False,
            is_active=False,
        )

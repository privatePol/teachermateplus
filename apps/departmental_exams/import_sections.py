"""Authoritative, batch-owned standalone section placement for question imports."""

from django.core.exceptions import PermissionDenied, ValidationError

from .models import ExamScenarioMember, QuestionBlueprintPlacement


class ImportSectionError(ValidationError):
    pass


def resolve_import_section(*, contribution, tenant_id, section_id=None, for_update=False):
    from .faculty_case_services import FacultyCasePolicy

    context = FacultyCasePolicy.context(
        contribution=contribution, tenant_id=tenant_id,
        for_update=for_update, required=False,
    )
    if context is None:
        from .exam_units import resolve_examination_unit
        from .models import ExamBlueprint

        unit = resolve_examination_unit(contribution.cycle_course, for_update=for_update)
        if ExamBlueprint.objects.filter(
            cycle_course=unit.primary, mode=ExamBlueprint.Mode.USE_SECTIONS,
        ).exists():
            raise PermissionDenied("Sectioned question import requires enabled, frozen section placement.")
        if section_id not in (None, "", 0, "0"):
            raise ImportSectionError("Section targeting is unavailable for this contribution. Upload the file again when access is restored.")
        return None
    blueprint, sections = context
    try:
        return FacultyCasePolicy.section_for(
            blueprint=blueprint, sections=sections, section_id=section_id,
        )
    except ValidationError as exc:
        raise ImportSectionError(
            "This import needs a valid target section from the frozen exam structure. "
            "Upload the file again and select 'Add these questions to section'."
        ) from exc


def import_section_choices(*, contribution, tenant_id):
    from .faculty_case_services import FacultyCasePolicy

    context = FacultyCasePolicy.context(
        contribution=contribution, tenant_id=tenant_id, required=False,
    )
    # Existing policy permits standalone MCQs in every frozen explicit section.
    return context[1] if context else ()


def validate_import_placements(*, questions, section, actor_id):
    question_ids = [question.pk for question in questions]
    placements = list(QuestionBlueprintPlacement.objects.select_for_update().filter(
        question_id__in=question_ids,
    ))
    if ExamScenarioMember.objects.filter(question_id__in=question_ids, active_marker=1).exists():
        raise ValidationError("Imported standalone questions cannot be linked to a Case.")
    if section is None:
        valid = not placements
    else:
        valid = len(placements) == len(question_ids) and all(
            placement.section_id == section.id
            and placement.blueprint_id == section.blueprint_id
            and placement.placed_by_id == actor_id
            for placement in placements
        )
    if not valid:
        raise ValidationError("The persisted import section placements are inconsistent.")

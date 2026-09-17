from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import Http404

from apps.core.services.audit import AuditService
from apps.core.services.features import FeatureSettingsService

from .contribution_services import QuestionMutationService
from .exam_units import resolve_examination_unit
from .models import (
    ExamBlueprint,
    ExamScenario,
    ExamScenarioMember,
    ExamSection,
    ExaminationCycle,
    FacultyContribution,
    QuestionBlueprintPlacement,
)
from .scenario_content import canonicalize_scenario_content


class FacultyCasePolicy:
    @classmethod
    def correction_admin_case(cls, *, contribution, scenario_id, tenant_id,
                              for_update=False):
        """Resolve a current reviewer Case from retained correction ownership."""
        context = cls.context(
            contribution=contribution, tenant_id=tenant_id,
            for_update=for_update, required=False,
        )
        if (context is None or contribution.active_marker != 1
                or contribution.status not in (
                    FacultyContribution.Status.DRAFT, FacultyContribution.Status.SUBMITTED,
                )
                or contribution.supersedes_id is None
                or contribution.cycle_course.cycle.processing_mode
                != ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION):
            raise Http404
        blueprint, _sections = context
        scenarios = ExamScenario.objects
        if for_update:
            scenarios = scenarios.select_for_update()
        scenario = scenarios.filter(
            pk=scenario_id, blueprint=blueprint, contribution__isnull=True,
            active_marker=1, supersedes__isnull=False,
        ).first()
        if scenario is None:
            raise Http404
        # Replacement authority belongs to this correction window only. An
        # older submission may have left the Case before the latest return.
        previous = FacultyContribution.objects.filter(
            pk=contribution.supersedes_id,
            faculty_user_id=contribution.faculty_user_id,
            cycle_course_id=contribution.cycle_course_id,
            status=FacultyContribution.Status.SUBMITTED,
            active_marker__isnull=True,
        ).first()
        if previous is None:
            raise Http404
        if previous.source_campus_id != contribution.source_campus_id:
            # Roster sync may rebind only the current Draft. Its new primary
            # source must still be retained and eligible in the new campus.
            from .contribution_authorization import ContributorEligibilityService
            if not contribution.eligibility_sources.filter(
                assignment_id_snapshot=contribution.source_assignment_id,
                tenant_id_snapshot=tenant_id,
                campus_id_snapshot=contribution.source_campus_id,
                is_current=True,
            ).exists():
                raise Http404
            inventory = ContributorEligibilityService.source_inventory(
                cycle_course=contribution.cycle_course,
                faculty_user_id=contribution.faculty_user_id,
            )
            if contribution.source_assignment_id not in {
                assignment.id for assignment in inventory.eligible_sources
            }:
                raise Http404
        case_ids = set()
        ancestor_case = scenario
        while ancestor_case.supersedes_id:
            ancestor_case = ExamScenario.objects.filter(pk=ancestor_case.supersedes_id).first()
            if (ancestor_case is None or ancestor_case.id in case_ids
                    or ancestor_case.blueprint_id != blueprint.id
                    or ancestor_case.active_marker is not None):
                raise Http404
            case_ids.add(ancestor_case.id)
        if not case_ids or not ExamScenarioMember.objects.filter(
            scenario_id__in=case_ids, question__contribution_id=previous.id,
            active_marker__isnull=True,
        ).exists():
            raise Http404
        return scenario

    @classmethod
    def context(cls, *, contribution, tenant_id, for_update=False, required=True):
        enabled = FeatureSettingsService.is_departmental_exam_structured_lifecycle_enabled(
            tenant_id=tenant_id
        )
        manual = (
            contribution.cycle_course.cycle.processing_mode
            == ExaminationCycle.ProcessingMode.MANUAL_REVIEW
        )
        from .setup_services import case_aware_automatic_enabled
        if not enabled or not (manual or case_aware_automatic_enabled(contribution.cycle_course)):
            if required:
                raise PermissionDenied(
                    "Case authoring requires enabled structured Manual Review or Departmental Automatic generation."
                )
            return None
        unit = resolve_examination_unit(
            contribution.cycle_course, for_update=for_update
        )
        if manual and unit.primary.id != contribution.cycle_course_id:
            raise PermissionDenied("Faculty Case ownership must use the authoritative examination unit.")
        queryset = ExamBlueprint.objects
        if for_update:
            queryset = queryset.select_for_update()
        blueprint = queryset.filter(cycle_course=unit.primary).first()
        if blueprint is None or blueprint.structure_frozen_at is None:
            raise PermissionDenied(
                "Case authoring requires the exam structure frozen at first Open."
            )
        section_queryset = ExamSection.objects.filter(blueprint=blueprint)
        if for_update:
            section_queryset = section_queryset.select_for_update()
        sections = tuple(section_queryset.order_by("display_order", "id"))
        if blueprint.mode == ExamBlueprint.Mode.USE_SECTIONS and not sections:
            raise PermissionDenied("The frozen exam structure has no valid sections.")
        if blueprint.mode == ExamBlueprint.Mode.NO_SECTIONS and sections:
            raise PermissionDenied("The frozen No Sections structure is inconsistent.")
        return blueprint, sections

    @staticmethod
    def section_for(*, blueprint, sections, section_id):
        if blueprint.mode == ExamBlueprint.Mode.NO_SECTIONS:
            if section_id not in (None, "", 0, "0"):
                raise ValidationError("No Sections Cases use the implicit exam section.")
            return None
        try:
            section_id = int(section_id)
        except (TypeError, ValueError) as exc:
            raise ValidationError({"section_id": "Select a valid frozen Exam Section."}) from exc
        section = next((item for item in sections if item.id == section_id), None)
        if section is None:
            raise ValidationError({"section_id": "Select a valid frozen Exam Section."})
        return section

    @classmethod
    def apply_question_structure(
        cls,
        *,
        contribution,
        question,
        actor,
        tenant_id,
        section_id=None,
        scenario_id=None,
        expected_scenario_revision=None,
        insert_position=None,
    ):
        context = cls.context(
            contribution=contribution,
            tenant_id=tenant_id,
            for_update=True,
            required=False,
        )
        if context is None:
            if section_id not in (None, "", 0, "0") or scenario_id not in (None, "", 0, "0"):
                raise PermissionDenied("Structured question placement is unavailable.")
            return False
        blueprint, sections = context
        changed = False
        section = cls.section_for(
            blueprint=blueprint, sections=sections, section_id=section_id
        )
        placement = QuestionBlueprintPlacement.objects.select_for_update().filter(
            question=question
        ).first()
        if blueprint.mode == ExamBlueprint.Mode.USE_SECTIONS:
            if placement is None:
                placement = QuestionBlueprintPlacement(
                    blueprint=blueprint,
                    question=question,
                    section=section,
                    placed_by=actor,
                    revision=1,
                )
                changed = True
            else:
                if placement.blueprint_id != blueprint.id:
                    raise ValidationError("Question belongs to a different exam structure.")
                if placement.section_id != section.id:
                    placement.section = section
                    placement.placed_by = actor
                    placement.revision += 1
                    changed = True
            if changed:
                placement.full_clean()
                placement.save()
        elif placement is not None:
            raise ValidationError("No Sections questions cannot retain an explicit placement.")

        if scenario_id not in (None, "", 0, "0"):
            scenario = ExamScenario.objects.select_for_update().filter(
                pk=scenario_id,
                contribution=contribution,
                blueprint=blueprint,
                active_marker=1,
            ).first()
            if scenario is None:
                scenario = cls.correction_admin_case(
                    contribution=contribution, scenario_id=scenario_id,
                    tenant_id=tenant_id, for_update=True,
                )
            if scenario.contribution_id is None and scenario.revision != expected_scenario_revision:
                from .contribution_services import ContributionConflict
                raise ContributionConflict("This Case changed after the page was loaded. Refresh and retry.")
            if scenario.section_id != (section.id if section else None):
                raise ValidationError("Linked Questions must use the Case Exam Section.")
            existing = ExamScenarioMember.objects.select_for_update().filter(
                question=question, active_marker=1
            ).first()
            if existing is not None and existing.scenario_id != scenario.id:
                raise ValidationError("A question may belong to at most one Case.")
            if existing is None:
                rows = list(ExamScenarioMember.objects.select_for_update().filter(
                    scenario=scenario, active_marker=1,
                ).order_by("position", "id"))
                position = len(rows) + 1
                if scenario.contribution_id is None:
                    try:
                        position = int(insert_position)
                    except (TypeError, ValueError) as exc:
                        raise ValidationError("Select a valid Linked Question position.") from exc
                    if position < 1 or position > len(rows) + 1:
                        raise ValidationError("Linked Question order changed. Refresh and retry.")
                    if [row.position for row in rows] != list(range(1, len(rows) + 1)):
                        raise ValidationError("Case question order is incomplete. Ask an administrator to inspect it.")
                    if position <= len(rows):
                        temporary = len(rows) + 1
                        for offset, row in enumerate(rows, start=1):
                            row.position = temporary + offset
                            row.save(update_fields=["position", "updated_at"])
                        for final_position, row in enumerate(rows, start=1):
                            row.position = final_position + (final_position >= position)
                            row.save(update_fields=["position", "updated_at"])
                member = ExamScenarioMember(
                    scenario=scenario, question=question, position=position
                )
                member.full_clean()
                member.save()
                scenario.revision += 1
                scenario.updated_by = actor
                scenario.save(update_fields=["revision", "updated_by", "updated_at"])
                changed = True
        return changed

    @classmethod
    def validate_submission(cls, *, contribution, questions, tenant_id):
        context = cls.context(
            contribution=contribution,
            tenant_id=tenant_id,
            for_update=True,
            required=False,
        )
        if context is None:
            if ExamScenario.objects.filter(contribution=contribution, active_marker=1).exists():
                raise ValidationError(
                    "This contribution contains Cases, but its structured workflow is unavailable."
                )
            return
        blueprint, sections = context
        question_ids = {question.id for question in questions}
        placements = {
            row.question_id: row
            for row in QuestionBlueprintPlacement.objects.select_for_update()
            .filter(question_id__in=question_ids)
            .order_by("id")
        }
        if blueprint.mode == ExamBlueprint.Mode.USE_SECTIONS:
            valid_section_ids = {section.id for section in sections}
            if set(placements) != question_ids or any(
                row.blueprint_id != blueprint.id or row.section_id not in valid_section_ids
                for row in placements.values()
            ):
                raise ValidationError(
                    "Every question must be assigned to a valid frozen Exam Section before Final Submission."
                )
        elif placements:
            raise ValidationError("No Sections contributions cannot retain question placements.")

        scenarios = list(
            ExamScenario.objects.select_for_update()
            .filter(contribution=contribution, active_marker=1)
            .order_by("id")
        )
        scenario_ids = {scenario.id for scenario in scenarios}
        admin_scenarios = list(
            ExamScenario.objects.select_for_update().filter(
                blueprint=blueprint, contribution__isnull=True,
                active_marker=1, supersedes__isnull=False,
            ).order_by("id")
        ) if contribution.supersedes_id else []
        members = list(
            ExamScenarioMember.objects.select_for_update()
            .filter(Q(scenario_id__in=scenario_ids) | Q(question_id__in=question_ids), active_marker=1)
            .select_related("question", "scenario")
            .order_by("scenario_id", "position", "id")
        )
        authorized_admin_cases = set()
        rejected_admin_cases = set()
        def correction_admin_member(scenario_id):
            if scenario_id in authorized_admin_cases:
                return True
            if scenario_id in rejected_admin_cases:
                return False
            try:
                cls.correction_admin_case(
                    contribution=contribution, scenario_id=scenario_id,
                    tenant_id=tenant_id, for_update=True,
                )
            except Http404:
                rejected_admin_cases.add(scenario_id)
                return False
            authorized_admin_cases.add(scenario_id)
            return True

        if any(
            member.question_id in question_ids
            and member.scenario_id not in scenario_ids
            and not correction_admin_member(member.scenario_id)
            for member in members
        ):
            raise ValidationError(
                "A contribution question belongs to a Case outside this contribution."
            )
        members = [member for member in members if member.scenario_id in scenario_ids]
        for scenario in admin_scenarios:
            if not correction_admin_member(scenario.id):
                continue
            if scenario.members.filter(active_marker=1).count() < 2:
                raise ValidationError(
                    "A corrected Admin Case needs at least two Linked Questions before Final Submission. Add replacements within the Case."
                )
        member_counts = {scenario.id: 0 for scenario in scenarios}
        seen_questions = set()
        for member in members:
            scenario = member.scenario
            if member.question_id not in question_ids or member.question.contribution_id != contribution.id:
                raise ValidationError("A Case contains a question outside this contribution.")
            if member.question_id in seen_questions:
                raise ValidationError("A question may belong to at most one Case.")
            seen_questions.add(member.question_id)
            member_counts[scenario.id] += 1
            if blueprint.mode == ExamBlueprint.Mode.USE_SECTIONS and (
                placements[member.question_id].section_id != scenario.section_id
            ):
                raise ValidationError("Every Linked Question must match its Case Exam Section.")
        for scenario in scenarios:
            if scenario.blueprint_id != blueprint.id:
                raise ValidationError("A Case belongs to a different exam structure.")
            if member_counts[scenario.id] == 0:
                raise ValidationError("Each Case must contain at least one Linked Question.")
            if contribution.cycle_course.cycle.processing_mode == ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION:
                from .contribution_services import QuestionPayloadService
                ordered = [member for member in members if member.scenario_id == scenario.id]
                if [member.position for member in ordered] != list(range(1, len(ordered) + 1)):
                    raise ValidationError("Reorder this Case's Linked Questions to restore a complete sequence before Final Submission.")
                from .duplicate_contract import question_identity
                fingerprints = [question_identity(member.question) for member in ordered]
                if len(fingerprints) != len(set(fingerprints)):
                    raise ValidationError("A Case contains duplicate logical MCQs. Revise its Linked Questions before Final Submission.")
            canonical = canonicalize_scenario_content(scenario.stimulus).html
            if (
                scenario.content_format != ExamScenario.ContentFormat.RICH_HTML_V1
                or canonical != scenario.stimulus
            ):
                raise ValidationError("Every faculty Case must contain valid canonical rich content.")


class FacultyCaseMutationService:
    @staticmethod
    def _audit(*, action, contribution, scenario, actor, metadata=None, request=None):
        AuditService.log_event(
            action=action,
            portal="FACULTY",
            entity_type="ExamScenario",
            entity_id=scenario.id,
            actor=actor,
            tenant=contribution.cycle_course.cycle.tenant_id,
            campus=contribution.source_campus_id,
            metadata={
                "cycle_id": contribution.cycle_course.cycle_id,
                "cycle_course_id": contribution.cycle_course_id,
                "contribution_id": contribution.id,
                "scenario_id": scenario.id,
                **(metadata or {}),
            },
            request=request,
        )

    @classmethod
    @transaction.atomic
    def save(
        cls,
        *,
        contribution_id,
        user,
        tenant_id,
        campus_id,
        expected_contribution_revision,
        title,
        raw_content,
        section_id=None,
        scenario_id=None,
        expected_scenario_revision=0,
        request=None,
    ):
        _configuration, contribution, _questions = QuestionMutationService._lock_mutable(
            contribution_id=contribution_id,
            user=user,
            tenant_id=tenant_id,
            campus_id=campus_id,
            expected_contribution_revision=expected_contribution_revision,
        )
        blueprint, sections = FacultyCasePolicy.context(
            contribution=contribution, tenant_id=tenant_id, for_update=True
        )
        section = FacultyCasePolicy.section_for(
            blueprint=blueprint, sections=sections, section_id=section_id
        )
        title = (title or "").strip()
        if len(title) > 200:
            raise ValidationError({"title": "Case title may not exceed 200 characters."})
        canonical = canonicalize_scenario_content(raw_content)
        scenario = None
        if scenario_id is not None:
            scenario = ExamScenario.objects.select_for_update().filter(
                pk=scenario_id,
                contribution=contribution,
                blueprint=blueprint,
                active_marker=1,
            ).first()
            if scenario is None:
                raise Http404
            if scenario.revision != expected_scenario_revision:
                raise ValidationError("This Case changed after the page was loaded.")
        elif expected_scenario_revision not in (None, 0):
            raise ValidationError("This Case was created after the page was loaded.")
        creating = scenario is None
        if (
            not creating
            and scenario.section_id != (section.id if section else None)
            and ExamScenarioMember.objects.select_for_update().filter(scenario=scenario).exists()
        ):
            raise ValidationError(
                "A Case with Linked Questions cannot change Exam Section. Delete its Linked Questions first."
            )
        before_revision = contribution.revision
        if creating:
            scenario = ExamScenario(
                blueprint=blueprint,
                contribution=contribution,
                created_by=user,
                updated_by=user,
                revision=1,
            )
        else:
            scenario.revision += 1
            scenario.updated_by = user
        scenario.section = section
        scenario.title = title
        scenario.stimulus = canonical.html
        scenario.content_format = ExamScenario.ContentFormat.RICH_HTML_V1
        scenario.full_clean()
        scenario.save()
        QuestionMutationService._increment_contribution(contribution)
        cls._audit(
            action="DE_EXAM_FACULTY_CASE_CREATED" if creating else "DE_EXAM_FACULTY_CASE_UPDATED",
            contribution=contribution,
            scenario=scenario,
            actor=user,
            metadata={
                "section_id": scenario.section_id,
                "scenario_revision": scenario.revision,
                "content_digest": canonical.digest,
                "revision_before": before_revision,
                "revision_after": contribution.revision,
            },
            request=request,
        )
        scenario.content_warnings = canonical.warnings
        return scenario, creating

    @classmethod
    @transaction.atomic
    def delete(
        cls,
        *,
        contribution_id,
        scenario_id,
        user,
        tenant_id,
        campus_id,
        expected_contribution_revision,
        expected_scenario_revision,
        request=None,
    ):
        _configuration, contribution, _questions = QuestionMutationService._lock_mutable(
            contribution_id=contribution_id,
            user=user,
            tenant_id=tenant_id,
            campus_id=campus_id,
            expected_contribution_revision=expected_contribution_revision,
        )
        FacultyCasePolicy.context(
            contribution=contribution, tenant_id=tenant_id, for_update=True
        )
        scenario = ExamScenario.objects.select_for_update().filter(
            pk=scenario_id, contribution=contribution, active_marker=1
        ).first()
        if scenario is None:
            raise Http404
        if scenario.revision != expected_scenario_revision:
            raise ValidationError("This Case changed after the page was loaded.")
        member_count = ExamScenarioMember.objects.select_for_update().filter(
            scenario=scenario
        ).count()
        if member_count:
            raise ValidationError(
                "Delete the Linked Questions before deleting this Case. No questions were detached."
            )
        before_revision = contribution.revision
        cls._audit(
            action="DE_EXAM_FACULTY_CASE_DELETED",
            contribution=contribution,
            scenario=scenario,
            actor=user,
            metadata={
                "section_id": scenario.section_id,
                "scenario_revision": scenario.revision,
                "member_count": 0,
                "revision_before": before_revision,
                "revision_after": before_revision + 1,
            },
            request=request,
        )
        scenario.delete()
        QuestionMutationService._increment_contribution(contribution)

    @classmethod
    @transaction.atomic
    def reorder_members(
        cls,
        *,
        contribution_id,
        scenario_id,
        ordered_question_ids,
        user,
        tenant_id,
        campus_id,
        expected_contribution_revision,
        expected_scenario_revision,
        request=None,
    ):
        _configuration, contribution, _questions = QuestionMutationService._lock_mutable(
            contribution_id=contribution_id,
            user=user,
            tenant_id=tenant_id,
            campus_id=campus_id,
            expected_contribution_revision=expected_contribution_revision,
        )
        FacultyCasePolicy.context(
            contribution=contribution, tenant_id=tenant_id, for_update=True
        )
        scenario = ExamScenario.objects.select_for_update().filter(
            pk=scenario_id, contribution=contribution, active_marker=1
        ).first()
        if scenario is None:
            raise Http404
        if scenario.revision != expected_scenario_revision:
            raise ValidationError("This Case changed after the page was loaded.")
        members = list(
            ExamScenarioMember.objects.select_for_update()
            .filter(scenario=scenario)
            .order_by("position", "id")
        )
        submitted = list(ordered_question_ids)
        expected = {member.question_id for member in members}
        if len(submitted) != len(set(submitted)) or set(submitted) != expected:
            raise ValidationError("Linked Question order must include every member exactly once.")
        current = [member.question_id for member in members]
        if submitted == current:
            return False
        by_question = {member.question_id: member for member in members}
        high = len(members) * 2 + 1
        for offset, member in enumerate(members):
            member.position = high + offset
            member.save(update_fields=["position", "updated_at"])
        for position, question_id in enumerate(submitted, start=1):
            member = by_question[question_id]
            member.position = position
            member.save(update_fields=["position", "updated_at"])
        before_revision = contribution.revision
        scenario.revision += 1
        scenario.updated_by = user
        scenario.save(update_fields=["revision", "updated_by", "updated_at"])
        QuestionMutationService._increment_contribution(contribution)
        cls._audit(
            action="DE_EXAM_FACULTY_CASE_QUESTIONS_REORDERED",
            contribution=contribution,
            scenario=scenario,
            actor=user,
            metadata={
                "question_ids": submitted,
                "scenario_revision": scenario.revision,
                "revision_before": before_revision,
                "revision_after": contribution.revision,
            },
            request=request,
        )
        return True

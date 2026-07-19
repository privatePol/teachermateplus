from __future__ import annotations

import hashlib
import json

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.academics.models import FacultyAssignment
from apps.admin_portal.services import AdminScopeService
from apps.core.services.audit import AuditService
from apps.core.services.features import FeatureSettingsService
from apps.core.services.permissions import PermissionService
from apps.core.services.settings import SystemSettingService
from apps.enrollment.models import Enrollment
from apps.faculty_portal.services import FacultyPerformanceService
from apps.grading.services import FacultyGradingService
from apps.rbac.models import UserRole

from .models import (
    AcademicInterventionAction,
    AcademicInterventionCase,
    AcademicInterventionDecisionRevision,
    AcademicInterventionFollowUp,
)


class AcademicInterventionAuthorizationService:
    MANAGE_OWN_PERMISSION = "academic_interventions.manage_own"
    MONITOR_PERMISSION = "academic_interventions.monitor"
    CONFIGURE_PERMISSION = "academic_interventions.configure"
    VIEW_DISABLED_ARCHIVE_PERMISSION = "academic_interventions.view_disabled_archive"

    @classmethod
    def require_enabled(cls, *, tenant_id, allow_disabled_archive=False, user=None, campus_id=None):
        if FeatureSettingsService.is_student_academic_intervention_tracking_enabled(tenant_id=tenant_id, default=False):
            return
        if allow_disabled_archive and PermissionService.has_permission(
            user, cls.VIEW_DISABLED_ARCHIVE_PERMISSION, tenant_id=tenant_id, campus_id=campus_id
        ):
            return
        raise PermissionDenied("Student Academic Intervention Tracking is not enabled.")

    @classmethod
    def require_owner(cls, *, user, case):
        cls.require_enabled(tenant_id=case.tenant_id, user=user, campus_id=case.campus_id)
        if case.faculty_owner_id != user.id:
            raise PermissionDenied("Only the faculty owner may access this intervention record.")
        if not PermissionService.has_permission(
            user,
            cls.MANAGE_OWN_PERMISSION,
            tenant_id=case.tenant_id,
            campus_id=case.campus_id,
        ):
            raise PermissionDenied("You do not have intervention-record permission.")

    @classmethod
    def authorized_current_offering(cls, *, user, offering_id, tenant_id=None, campus_id=None):
        assignment = (
            FacultyAssignment.objects.filter(
                faculty_user=user,
                offering_id=offering_id,
                is_active=True,
                response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
                accepted_at__isnull=False,
                offering__is_active=True,
                offering__tenant__is_active=True,
                offering__campus__is_active=True,
                offering__academic_year__is_active=True,
                offering__term__is_active=True,
            )
            .select_related("offering", "offering__academic_year", "offering__term", "offering__campus")
            .first()
        )
        if not assignment:
            raise PermissionDenied("You are not authorized for this current course offering.")
        offering = assignment.offering
        if tenant_id and offering.tenant_id != tenant_id:
            raise PermissionDenied("Offering is outside the tenant scope.")
        if campus_id and offering.campus_id != campus_id:
            raise PermissionDenied("Offering is outside the campus scope.")
        cls.require_enabled(tenant_id=offering.tenant_id, user=user, campus_id=offering.campus_id)
        if not PermissionService.has_permission(user, cls.MANAGE_OWN_PERMISSION, tenant_id=offering.tenant_id, campus_id=offering.campus_id):
            raise PermissionDenied("You do not have intervention-record permission.")
        return offering

    @classmethod
    def require_admin_monitor(cls, *, request, case=None):
        scope = getattr(request, "scope", {})
        tenant_id = case.tenant_id if case else scope.get("tenant_id")
        campus_id = case.campus_id if case else scope.get("campus_id")
        cls.require_enabled(
            tenant_id=tenant_id,
            user=request.user,
            campus_id=campus_id,
            allow_disabled_archive=True,
        )
        if not PermissionService.has_permission(request.user, cls.MONITOR_PERMISSION, tenant_id=tenant_id, campus_id=campus_id):
            raise PermissionDenied("You do not have intervention monitoring permission.")
        if case and not cls.admin_queryset(request).filter(pk=case.pk).exists():
            raise PermissionDenied("Intervention record is outside your authorized scope.")

    @classmethod
    def admin_queryset(cls, request):
        tenant_ids = list(AdminScopeService.active_scoped_tenants(request).values_list("id", flat=True))
        campus_ids = AdminScopeService._monitoring_campus_ids(request)
        cases = AcademicInterventionCase.objects.filter(tenant_id__in=tenant_ids, campus_id__in=campus_ids)
        if not request.user.is_superuser:
            tenant_id = getattr(request, "scope", {}).get("tenant_id")
            scope_filter = Q(pk__in=[])
            uses_dean_chain = AdminScopeService._uses_college_dean_supervision(request)
            for scoped_campus_id in campus_ids:
                role_scope = UserRole.objects.filter(
                    user=request.user,
                    is_active=True,
                    role__is_active=True,
                ).exclude(role__code="FACULTY")
                role_scope = role_scope.filter(Q(tenant_id=tenant_id) | Q(tenant__isnull=True)).filter(
                    Q(campus_id=scoped_campus_id) | Q(campus__isnull=True)
                )
                if role_scope.filter(department__isnull=True).exists() and not uses_dean_chain:
                    scope_filter |= Q(campus_id=scoped_campus_id)
                    continue
                department_ids = list(
                    AdminScopeService.active_scoped_departments(request)
                    .filter(campus_id=scoped_campus_id)
                    .values_list("id", flat=True)
                )
                if uses_dean_chain:
                    department_ids = AdminScopeService._college_dean_area_chair_department_ids(
                        request,
                        tenant_ids=[tenant_id],
                        campus_ids=[scoped_campus_id],
                        dean_department_ids=department_ids,
                    )
                if department_ids:
                    scope_filter |= Q(campus_id=scoped_campus_id, offering__department_id__in=department_ids)
            cases = cases.filter(scope_filter)
        return cases.select_related(
            "tenant", "campus", "offering", "offering__course", "offering__section", "offering__department",
            "academic_year", "term", "grading_period", "student", "faculty_owner"
        )


class AcademicInterventionConfigurationService:
    @classmethod
    def set_enabled(cls, *, user, tenant_id, campus_id, enabled, request=None):
        if not PermissionService.has_permission(
            user,
            AcademicInterventionAuthorizationService.CONFIGURE_PERMISSION,
            tenant_id=tenant_id,
            campus_id=campus_id,
        ):
            raise PermissionDenied("You do not have intervention configuration permission.")
        before = FeatureSettingsService.is_student_academic_intervention_tracking_enabled(
            tenant_id=tenant_id,
            default=False,
        )
        SystemSettingService.set(
            FeatureSettingsService.STUDENT_ACADEMIC_INTERVENTION_TRACKING_ENABLED_KEY,
            bool(enabled),
            tenant_id=tenant_id,
            value_type="BOOL",
            is_active=True,
        )
        AuditService.log_event(
            action="ACADEMIC_INTERVENTION_CONFIGURE",
            portal="ADMIN",
            entity_type="SystemSetting",
            entity_id=f"tenant:{tenant_id}:{FeatureSettingsService.STUDENT_ACADEMIC_INTERVENTION_TRACKING_ENABLED_KEY}",
            actor=user,
            tenant=tenant_id,
            campus=campus_id,
            before_data={"enabled": before},
            after_data={"enabled": bool(enabled)},
            request=request,
        )


class AcademicConcernDetectionService:
    """Builds live faculty-review recommendations; it never writes a case by itself."""

    @staticmethod
    def _fingerprint(payload):
        raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @classmethod
    def candidates_for_offering(cls, *, offering, faculty_owner):
        offering = AcademicInterventionAuthorizationService.authorized_current_offering(
            user=faculty_owner,
            offering_id=offering.id,
            tenant_id=offering.tenant_id,
            campus_id=offering.campus_id,
        )
        try:
            template = FacultyGradingService.resolve_template_for_offering(offering)
        except ValidationError:
            return []
        candidates = []
        for period in FacultyGradingService.get_template_periods(template):
            for row in FacultyPerformanceService.get_students_requiring_attention(offering, period):
                indicator_codes = [row["trend_label"]]
                if row.get("missing_output_count"):
                    indicator_codes.append("MISSING_OUTPUTS")
                snapshot = {
                    "indicator_codes": indicator_codes,
                    "primary_reason": row.get("primary_reason"),
                    "current_grade": str(row.get("current_grade")) if row.get("current_grade") is not None else None,
                    "previous_grade": str(row.get("previous_grade")) if row.get("previous_grade") is not None else None,
                    "missing_output_count": row.get("missing_output_count", 0),
                    "period_code": period.code,
                    "source_version": "phase1-live-v1",
                }
                fingerprint = cls._fingerprint({"offering": offering.id, "student": row["student_id"], "period": period.id, **snapshot})
                existing = AcademicInterventionCase.objects.filter(
                    faculty_owner=faculty_owner,
                    offering=offering,
                    student_id=row["student_id"],
                    grading_period=period,
                    detection_source=AcademicInterventionCase.DetectionSource.ANALYTICS,
                    analytics_source_fingerprint=fingerprint,
                    voided_at__isnull=True,
                ).exists()
                candidates.append({"student": row["student"], "period": period, "snapshot": snapshot, "fingerprint": fingerprint, "has_owner_case": existing})
        return candidates


class AcademicInterventionCaseService:
    STATUS_BY_DECISION = {
        AcademicInterventionCase.Decision.CONDUCT: AcademicInterventionCase.ReviewStatus.INTERVENTION_PLANNED,
        AcademicInterventionCase.Decision.MONITOR: AcademicInterventionCase.ReviewStatus.MONITORING,
        AcademicInterventionCase.Decision.NO_INTERVENTION: AcademicInterventionCase.ReviewStatus.NO_INTERVENTION,
        AcademicInterventionCase.Decision.ALREADY_ADDRESSED: AcademicInterventionCase.ReviewStatus.CLOSED,
        AcademicInterventionCase.Decision.INSUFFICIENT_DATA: AcademicInterventionCase.ReviewStatus.AWAITING_DATA,
        AcademicInterventionCase.Decision.REFERRED: AcademicInterventionCase.ReviewStatus.REFERRED,
    }

    @classmethod
    def _validate_student_enrollment(cls, *, offering, student):
        if not Enrollment.objects.filter(course_offering=offering, student=student, is_active=True).exclude(
            enrollment_status__in=Enrollment.NON_ACTIVE_GRADING_STATUSES
        ).exists():
            raise PermissionDenied("Student is not an active eligible enrollment in this offering.")

    @classmethod
    def create_manual(cls, *, user, offering_id, student, grading_period_id, summary, request=None):
        offering = AcademicInterventionAuthorizationService.authorized_current_offering(user=user, offering_id=offering_id)
        cls._validate_student_enrollment(offering=offering, student=student)
        period = FacultyGradingService.get_template_periods(FacultyGradingService.resolve_template_for_offering(offering)).filter(id=grading_period_id).first()
        if not period:
            raise ValidationError("Grading period does not belong to this offering template.")
        case = AcademicInterventionCase(
            tenant=offering.tenant, campus=offering.campus, offering=offering, academic_year=offering.academic_year,
            term=offering.term, grading_period=period, student=student, faculty_owner=user,
            identified_at=timezone.now(), detection_source=AcademicInterventionCase.DetectionSource.MANUAL,
            detection_code="FACULTY_MANUAL", distinct_concern_summary=summary.strip(), created_by=user, updated_by=user,
        )
        case.full_clean()
        case.save()
        cls._audit(
            "CREATE_MANUAL",
            case=case,
            actor=user,
            request=request,
            after={"distinct_concern_summary_length": len(case.distinct_concern_summary)},
        )
        return case

    @classmethod
    def create_analytics(cls, *, user, offering_id, student, grading_period_id, fingerprint, snapshot, request=None):
        offering = AcademicInterventionAuthorizationService.authorized_current_offering(user=user, offering_id=offering_id)
        cls._validate_student_enrollment(offering=offering, student=student)
        period = FacultyGradingService.get_template_periods(FacultyGradingService.resolve_template_for_offering(offering)).filter(id=grading_period_id).first()
        if not period:
            raise ValidationError("Grading period does not belong to this offering template.")
        candidate = next(
            (
                item
                for item in AcademicConcernDetectionService.candidates_for_offering(
                    offering=offering,
                    faculty_owner=user,
                )
                if item["student"].id == student.id
                and item["period"].id == period.id
                and item["fingerprint"] == fingerprint
            ),
            None,
        )
        if candidate is None:
            raise ValidationError("Academic concern is unavailable or no longer current.")
        snapshot = candidate["snapshot"]
        case = AcademicInterventionCase(
            tenant=offering.tenant, campus=offering.campus, offering=offering, academic_year=offering.academic_year,
            term=offering.term, grading_period=period, student=student, faculty_owner=user,
            identified_at=timezone.now(), detection_source=AcademicInterventionCase.DetectionSource.ANALYTICS,
            detection_code="LIVE_ANALYTICS", analytics_source_fingerprint=fingerprint, concern_snapshot_json=snapshot,
            created_by=user, updated_by=user,
        )
        with transaction.atomic():
            case.full_clean()
            case.save()
        cls._audit("CREATE_ANALYTICS", case=case, actor=user, request=request, after={"indicator_codes": snapshot.get("indicator_codes", [])})
        return case

    @classmethod
    def record_decision(
        cls,
        *,
        case_id,
        user,
        decision,
        rationale,
        referral_destination="",
        referral_destination_label="",
        referral_date=None,
        referral_reason="",
        supersede=False,
        correction_reason="",
        request=None,
    ):
        with transaction.atomic():
            case = AcademicInterventionCase.objects.select_for_update().get(pk=case_id)
            AcademicInterventionAuthorizationService.require_owner(user=user, case=case)
            if case.voided_at or case.review_status == AcademicInterventionCase.ReviewStatus.CLOSED:
                raise ValidationError("This intervention record is no longer available for a decision.")
            if case.faculty_decision and not supersede:
                raise ValidationError("Faculty decisions are write-once. Use the supersede option with a correction reason.")
            if supersede and not case.faculty_decision:
                raise ValidationError("There is no prior faculty decision to supersede.")
            if supersede and not correction_reason.strip():
                raise ValidationError("A correction reason is required to supersede a faculty decision.")
            if decision not in AcademicInterventionCase.Decision.values:
                raise ValidationError("Select a valid faculty decision.")
            if decision != AcademicInterventionCase.Decision.CONDUCT and not (rationale or "").strip():
                raise ValidationError("Enter a brief faculty rationale for this decision.")
            if decision == AcademicInterventionCase.Decision.REFERRED:
                if referral_destination not in AcademicInterventionCase.ReferralDestination.values:
                    raise ValidationError("Select an approved referral destination.")
                if not referral_date or not (referral_reason or "").strip():
                    raise ValidationError("Referral date and brief academic reason are required.")
                if (
                    referral_destination == AcademicInterventionCase.ReferralDestination.OTHER_APPROVED
                    and not (referral_destination_label or "").strip()
                ):
                    raise ValidationError("Name the approved referral office.")
            if (
                case.actions.filter(status=AcademicInterventionAction.Status.CONDUCTED).exists()
                and decision != AcademicInterventionCase.Decision.CONDUCT
            ):
                raise ValidationError("A conducted intervention cannot be superseded by a non-conduct decision.")
            before = {"faculty_decision": case.faculty_decision, "review_status": case.review_status}
            prior_revision = case.decision_revisions.select_for_update().order_by("-revision_no", "-id").first()
            decided_at = timezone.now()
            case.faculty_decision = decision
            case.faculty_rationale = (rationale or "").strip()
            case.decision_at = decided_at
            case.review_status = cls.STATUS_BY_DECISION[decision]
            case.referral_destination = (referral_destination or "").strip() if decision == case.Decision.REFERRED else ""
            case.referral_destination_label = (
                (referral_destination_label or "").strip() if decision == case.Decision.REFERRED else ""
            )
            case.referral_date = referral_date if decision == case.Decision.REFERRED else None
            case.referral_reason = (referral_reason or "").strip() if decision == case.Decision.REFERRED else ""
            case.closed_at = timezone.now() if decision == case.Decision.ALREADY_ADDRESSED else None
            if decision == case.Decision.CONDUCT and case.actions.filter(status=AcademicInterventionAction.Status.CONDUCTED).exists():
                case.review_status = case.ReviewStatus.INTERVENTION_CONDUCTED
            case.updated_by = user
            case.full_clean()
            case.save()
            AcademicInterventionDecisionRevision.objects.create(
                case=case,
                revision_no=(prior_revision.revision_no + 1) if prior_revision else 1,
                supersedes=prior_revision if supersede else None,
                decision=decision,
                rationale=case.faculty_rationale,
                decided_at=decided_at,
                decided_by=user,
                correction_reason=correction_reason.strip() if supersede else "",
                referral_destination=case.referral_destination,
                referral_destination_label=case.referral_destination_label,
                referral_date=case.referral_date,
                referral_reason=case.referral_reason,
            )
        cls._audit(
            "SUPERSEDE_DECISION" if supersede else "RECORD_DECISION",
            case=case,
            actor=user,
            request=request,
            before=before,
            after={
                "faculty_decision": decision,
                "review_status": case.review_status,
                "decision_revision_no": (prior_revision.revision_no + 1) if prior_revision else 1,
                "correction_reason_present": bool(correction_reason.strip()) if supersede else False,
            },
        )
        if decision == case.Decision.REFERRED:
            cls._audit(
                "REFERRAL",
                case=case,
                actor=user,
                request=request,
                after={
                    "destination": case.referral_destination,
                    "destination_label_present": bool(case.referral_destination_label),
                    "referral_date": str(case.referral_date),
                    "reason_length": len(case.referral_reason),
                },
            )
        return case

    @classmethod
    def add_action(cls, *, case_id, user, form, request=None):
        with transaction.atomic():
            case = AcademicInterventionCase.objects.select_for_update().get(pk=case_id)
            AcademicInterventionAuthorizationService.require_owner(user=user, case=case)
            if case.faculty_decision != case.Decision.CONDUCT or case.voided_at or case.closed_at:
                raise ValidationError("Actions are available only for an open faculty decision to conduct intervention.")
            action = form.save(commit=False)
            action.case = case
            action.created_by = user
            action.updated_by = user
            if action.status == action.Status.CANCELLED:
                raise ValidationError("Create a planned or conducted action; only an existing plan may be cancelled.")
            action.full_clean()
            action.save()
            if action.status == action.Status.CONDUCTED:
                case.review_status = case.ReviewStatus.INTERVENTION_CONDUCTED
            elif action.status == action.Status.PLANNED:
                case.review_status = case.ReviewStatus.INTERVENTION_PLANNED
            case.updated_by = user
            case.save(update_fields=["review_status", "updated_by", "updated_at"])
        cls._audit(
            "CONDUCT_ACTION" if action.status == action.Status.CONDUCTED else "PLAN_ACTION",
            case=case,
            actor=user,
            request=request,
            after={"action_id": action.id, "status": action.status},
        )
        return action

    @classmethod
    def update_action(cls, *, case_id, action_id, user, form, request=None):
        with transaction.atomic():
            case = AcademicInterventionCase.objects.select_for_update().get(pk=case_id)
            AcademicInterventionAuthorizationService.require_owner(user=user, case=case)
            if case.voided_at or case.closed_at:
                raise ValidationError("This intervention record is closed.")
            action = AcademicInterventionAction.objects.select_for_update().get(pk=action_id, case=case)
            if action.status != action.Status.PLANNED:
                raise ValidationError("Only a planned action may be updated.")
            updated = form.save(commit=False)
            if updated.status not in {action.Status.PLANNED, action.Status.CONDUCTED, action.Status.CANCELLED}:
                raise ValidationError("Select a valid action status.")
            before = {"action_id": action.id, "status": action.status}
            for field in (
                "intervention_type",
                "status",
                "planned_for",
                "conducted_on",
                "action_summary",
                "student_action_plan",
                "cancellation_reason",
            ):
                setattr(action, field, getattr(updated, field))
            action.updated_by = user
            action.full_clean()
            action.save()
            cls._refresh_action_status(case=case, user=user)
        cls._audit(
            "CONDUCT_ACTION" if action.status == action.Status.CONDUCTED else "UPDATE_ACTION",
            case=case,
            actor=user,
            request=request,
            before=before,
            after={"action_id": action.id, "status": action.status},
        )
        return action

    @staticmethod
    def _refresh_action_status(*, case, user):
        if case.actions.filter(status=AcademicInterventionAction.Status.CONDUCTED).exists():
            case.review_status = case.ReviewStatus.INTERVENTION_CONDUCTED
        else:
            case.review_status = case.ReviewStatus.INTERVENTION_PLANNED
        case.updated_by = user
        case.save(update_fields=["review_status", "updated_by", "updated_at"])

    @classmethod
    def add_follow_up(cls, *, case_id, user, form, request=None):
        with transaction.atomic():
            case = AcademicInterventionCase.objects.select_for_update().get(pk=case_id)
            AcademicInterventionAuthorizationService.require_owner(user=user, case=case)
            if case.voided_at or case.closed_at:
                raise ValidationError("This intervention record is closed.")
            follow_up = form.save(commit=False)
            follow_up.case = case
            follow_up.created_by = user
            follow_up.updated_by = user
            follow_up.full_clean()
            follow_up.save()
        cls._audit("ADD_FOLLOW_UP", case=case, actor=user, request=request, after={"follow_up_id": follow_up.id, "status": follow_up.status})
        return follow_up

    @classmethod
    def update_follow_up(cls, *, case_id, follow_up_id, user, form, request=None):
        with transaction.atomic():
            case = AcademicInterventionCase.objects.select_for_update().get(pk=case_id)
            AcademicInterventionAuthorizationService.require_owner(user=user, case=case)
            follow_up = AcademicInterventionFollowUp.objects.select_for_update().get(pk=follow_up_id, case=case)
            if case.voided_at or case.closed_at:
                raise ValidationError("This intervention record is closed.")
            before = {"status": follow_up.status, "due_on": str(follow_up.due_on)}
            updated = form.save(commit=False)
            for field in ("due_on", "status", "result_summary", "completed_on"):
                setattr(follow_up, field, getattr(updated, field))
            follow_up.updated_by = user
            follow_up.full_clean()
            follow_up.save()
        cls._audit("UPDATE_FOLLOW_UP", case=case, actor=user, request=request, before=before, after={"follow_up_id": follow_up.id, "status": follow_up.status})
        return follow_up

    @classmethod
    def close(cls, *, case_id, user, request=None):
        with transaction.atomic():
            case = AcademicInterventionCase.objects.select_for_update().get(pk=case_id)
            AcademicInterventionAuthorizationService.require_owner(user=user, case=case)
            if case.voided_at:
                raise ValidationError("Voided records cannot be closed.")
            if case.closed_at:
                raise ValidationError("This intervention record is already closed.")
            before = {"review_status": case.review_status}
            case.review_status = case.ReviewStatus.CLOSED
            case.closed_at = timezone.now()
            case.updated_by = user
            case.save(update_fields=["review_status", "closed_at", "updated_by", "updated_at"])
        cls._audit("CLOSE", case=case, actor=user, request=request, before=before, after={"review_status": case.review_status})
        return case

    @classmethod
    def void(cls, *, case_id, user, reason, request=None):
        with transaction.atomic():
            case = AcademicInterventionCase.objects.select_for_update().get(pk=case_id)
            AcademicInterventionAuthorizationService.require_owner(user=user, case=case)
            if case.voided_at or case.closed_at:
                raise ValidationError("Closed or voided records cannot be voided.")
            if case.actions.filter(status=AcademicInterventionAction.Status.CONDUCTED).exists():
                raise ValidationError("Conducted intervention records cannot be voided.")
            if not reason.strip():
                raise ValidationError("A void reason is required.")
            before = {"review_status": case.review_status}
            case.review_status = case.ReviewStatus.VOIDED
            case.voided_at = timezone.now()
            case.void_reason = reason.strip()
            case.updated_by = user
            case.save(update_fields=["review_status", "voided_at", "void_reason", "updated_by", "updated_at"])
        cls._audit(
            "VOID",
            case=case,
            actor=user,
            request=request,
            before=before,
            after={"review_status": case.review_status, "reason_present": bool(case.void_reason)},
        )
        return case

    @staticmethod
    def _audit(action, *, case, actor, request, before=None, after=None):
        AuditService.log_event(
            action=f"ACADEMIC_INTERVENTION_{action}", portal="FACULTY", entity_type="AcademicInterventionCase",
            entity_id=case.id, actor=actor, tenant=case.tenant, campus=case.campus,
            before_data=before, after_data=after, request=request,
        )


class AcademicInterventionMonitoringService:
    @classmethod
    def filtered_cases(cls, request):
        AcademicInterventionAuthorizationService.require_admin_monitor(request=request)
        cases = AcademicInterventionAuthorizationService.admin_queryset(request)
        for key, field in (("academic_year_id", "academic_year_id"), ("term_id", "term_id"), ("campus_id", "campus_id"), ("faculty_id", "faculty_owner_id"), ("offering_id", "offering_id"), ("student_id", "student_id")):
            value = request.GET.get(key)
            if value and value.isdigit():
                cases = cases.filter(**{field: int(value)})
        for key, field in (("status", "review_status"), ("decision", "faculty_decision"), ("program_id", "offering__program_id"), ("course_id", "offering__course_id"), ("section_id", "offering__section_id")):
            value = (request.GET.get(key) or "").strip()
            if value:
                cases = cases.filter(**{field: value})
        return cases

    @classmethod
    def summary(cls, cases):
        return {status: cases.filter(review_status=status).count() for status, _label in AcademicInterventionCase.ReviewStatus.choices}

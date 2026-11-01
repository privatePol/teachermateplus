"""Request-local dashboard reads. Callers supply already authorized offerings."""

from collections import defaultdict
from datetime import timedelta
import json
import logging
import uuid

from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import Count, F, Prefetch, Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.crypto import salted_hmac

from apps.academics.services import AcademicGovernanceService
from apps.attendance.models import AttendanceRecord, AttendanceSession
from apps.auditlog.models import AuditLog
from apps.core.services.audit import AuditService
from apps.core.services.features import FeatureSettingsService
from apps.enrollment.models import Enrollment
from apps.faculty_portal.models import FacultyDashboardVisitState, FacultyGradebookModification
from apps.grading.models import (
    CourseTemplateAssignment, GradeActivity, GradeEncodingControl, GradeSubmission, GradingPeriodLock,
    GradingTemplateComponent, GradingTemplateDetail, GradingTemplatePeriod,
    GradingTemplateSubcomponent, StudentActivityScore,
)
from apps.grading.services import FacultyGradingService, GradingGovernanceService
from apps.notifications.models import SubmissionNonComplianceNotice


logger = logging.getLogger(__name__)


class DashboardData:
    def __init__(self, offerings, *, now):
        offerings = self.offerings = list(offerings)
        self.now = now
        ids = [offering.id for offering in offerings]
        self.templates = {}
        cache = {}
        assignments = defaultdict(list)
        for assignment in CourseTemplateAssignment.objects.filter(
            course_id__in={offering.course_id for offering in offerings}, is_active=True,
            grading_template__is_active=True, grading_template__is_published=True,
        ).select_related("grading_template").order_by("-created_at"):
            assignments[assignment.course_id].append(assignment)
        for offering in offerings:
            key = (offering.tenant_id, offering.campus_id, offering.term_id, offering.course_id,
                   offering.department_id, offering.program_id, offering.section.program_id)
            if key not in cache:
                candidates = assignments[offering.course_id]
                chosen = next((row for row in candidates if row.effective_from_term_id == offering.term_id), None)
                chosen = chosen or next((row for row in candidates if row.effective_from_term_id is None), None)
                if chosen:
                    cache[key] = chosen.grading_template
                else:
                    # Preserve profile specificity and published-tenant fallback
                    # in the authoritative resolver for unassigned courses.
                    try:
                        cache[key] = FacultyGradingService.resolve_template_for_offering(offering)
                    except ValidationError as exc:
                        cache[key] = exc
            self.templates[offering.id] = cache[key]
        self.periods = defaultdict(list)
        for period in GradingTemplatePeriod.objects.filter(
            template_id__in={template.id for template in cache.values() if not isinstance(template, Exception)},
            is_active=True,
        ).prefetch_related(Prefetch(
            "components", queryset=GradingTemplateComponent.objects.filter(is_active=True).prefetch_related(Prefetch(
                "subcomponents", queryset=GradingTemplateSubcomponent.objects.filter(is_active=True).prefetch_related(
                    Prefetch("details", queryset=GradingTemplateDetail.objects.filter(is_active=True).order_by("sort_order", "id"))
                ).order_by("sort_order", "id"),
            )).order_by("sort_order", "id"),
        )).order_by("sequence_no", "id"):
            self.periods[period.template_id].append(period)
        self.submissions = {
            (row.offering_id, row.template_period_id): row
            for row in GradeSubmission.objects.filter(offering_id__in=ids)
        }
        scopes = Q(pk__in=[])
        control_scopes = Q(pk__in=[])
        for tenant, campus, year, term in {
            (o.tenant_id, o.campus_id, o.academic_year_id, o.term_id) for o in offerings
        }:
            scopes |= Q(tenant_id=tenant, campus_id=campus, academic_year_id=year, term_id=term)
            control_scopes |= Q(tenant_id=tenant, academic_year_id=year, term_id=term) & (
                Q(campus_id=campus) | Q(campus__isnull=True)
            )
        owned_or_shared = Q(course_offering_id__in=ids) | Q(course_offering__isnull=True)
        self.locks = list(GradingPeriodLock.objects.filter(scopes, owned_or_shared, is_active=True).order_by("-updated_at"))
        self.controls = list(GradeEncodingControl.objects.filter(
            control_scopes, owned_or_shared, is_active=True, status=GradeEncodingControl.Status.CLOSED,
        ))
        self.active_settings = {}
        self._readiness = None

    def template_periods(self, offering):
        template = self.templates[offering.id]
        if isinstance(template, ValidationError):
            raise template
        return template, self.periods[template.id]

    def submission(self, offering, period):
        return self.submissions.get((offering.id, period.id))

    def is_submitted(self, offering, period):
        submission = self.submission(offering, period)
        return bool(submission and submission.status == GradeSubmission.Status.SUBMITTED)

    def lock(self, offering, period):
        key = GradingGovernanceService._normalize_period_key(period.code or period.name)
        candidates = [lock for lock in self.locks if (
            lock.tenant_id == offering.tenant_id and lock.campus_id == offering.campus_id
            and lock.academic_year_id == offering.academic_year_id and lock.term_id == offering.term_id
            and GradingGovernanceService._normalize_period_key(lock.period_code) == key
        )]
        return next((lock for lock in candidates if lock.scope_type == "COURSE" and lock.course_offering_id == offering.id),
                    next((lock for lock in candidates if lock.scope_type == "CAMPUS" and lock.course_offering_id is None), None))

    def closed_control(self, offering, period):
        candidates = [row for row in self.controls if (
            row.tenant_id == offering.tenant_id and row.academic_year_id == offering.academic_year_id
            and row.term_id == offering.term_id and row.campus_id in (None, offering.campus_id)
            and row.course_offering_id in (None, offering.id)
            and (not row.period_code or row.period_code.upper() == (period.code or "").strip().upper())
        )]
        return max(candidates, key=lambda row: (
            bool(row.course_offering_id), bool(row.campus_id), bool(row.period_code), row.updated_at,
        ), default=None)

    def active_setting(self, offering):
        key = (offering.tenant_id, offering.campus_id, offering.term_id)
        if key not in self.active_settings:
            self.active_settings[key] = AcademicGovernanceService.resolve_active_grading_period(
                tenant_id=key[0], campus_id=key[1], term_id=key[2], now=self.now,
            )
        return self.active_settings[key]

    def current_period(self, offering):
        _, periods = self.template_periods(offering)
        setting = self.active_setting(offering)
        active = next((period for period in periods if AcademicGovernanceService.template_period_matches_active_period(
            template_period=period, active_period_setting=setting,
        )), None)
        return active or next((period for period in periods if not self.is_submitted(offering, period)),
                              periods[0] if periods else None)

    def readiness(self, offering, period):
        if self._readiness is None:
            self._load_readiness()
        key = (offering.id, period.id)
        eligible = self._eligible[offering.id]
        if not eligible:
            return dict(eligible_student_count=0, students_with_any_grade=0,
                        students_missing_any_grade=0, missing_template_bucket_count=0)
        activities = self._activities[key]
        components = list(period.components.all())
        buckets = {(row["template_component_id"], row["template_subcomponent_id"], row["template_detail_id"])
                   for row in activities if row["template_component__is_active"] and (
                       (row["template_subcomponent_id"] is None and row["template_detail_id"] is None)
                       or (row["template_subcomponent__is_active"] and (
                           row["template_detail_id"] is None or row["template_detail__is_active"]))) }
        requirements = GradingGovernanceService._template_activity_requirements(
            offering=offering, template_period=period, components=components, activity_buckets=buckets,
        )
        attendance_required = any(s.is_attendance_component for c in components for s in c.subcomponents.all())
        expected_sessions = self._sessions[key] if attendance_required else 0
        scores, attendance = self._scores[key], self._attendance[key]
        return {
            "eligible_student_count": len(eligible),
            "students_with_any_grade": sum(bool(scores.get(s) or attendance.get(s)) for s in eligible),
            "students_missing_any_grade": sum(
                scores.get(s, 0) < len(activities) or attendance.get(s, 0) < expected_sessions for s in eligible),
            "missing_template_bucket_count": len(requirements["missing_items"]),
        }

    def _load_readiness(self):
        ids = [offering.id for offering in self.offerings]
        pairs = []
        for offering in self.offerings:
            try:
                period = self.current_period(offering)
            except ValidationError:
                continue
            if period:
                pairs.append((offering.id, period.id))

        def pair_scope(prefix=""):
            scope = Q(pk__in=[])
            for offering_id, period_id in pairs:
                scope |= Q(**{f"{prefix}offering_id": offering_id, f"{prefix}template_period_id": period_id})
            return scope

        self._eligible = defaultdict(set)
        for offering_id, student_id in Enrollment.objects.filter(
            course_offering_id__in=ids, is_active=True, student__is_active=True, student__department__is_active=True,
        ).filter(Q(student__program__isnull=True) | Q(student__program__is_active=True)).exclude(
            enrollment_status__in=Enrollment.NON_ACTIVE_GRADING_STATUSES,
        ).values_list("course_offering_id", "student_id"):
            self._eligible[offering_id].add(student_id)
        self._activities = defaultdict(list)
        for row in GradeActivity.objects.filter(pair_scope(), is_active=True).values(
            "offering_id", "template_period_id", "template_component_id", "template_subcomponent_id", "template_detail_id",
            "template_component__is_active", "template_subcomponent__is_active", "template_detail__is_active",
        ):
            self._activities[(row["offering_id"], row["template_period_id"])].append(row)
        self._sessions = defaultdict(int)
        for row in AttendanceSession.objects.filter(pair_scope(), is_active=True).values(
            "offering_id", "template_period_id",
        ).annotate(total=Count("id")):
            self._sessions[(row["offering_id"], row["template_period_id"])] = row["total"]
        self._scores, self._attendance = defaultdict(dict), defaultdict(dict)
        for model, relation, target in (
            (StudentActivityScore, "activity", self._scores), (AttendanceRecord, "session", self._attendance),
        ):
            for row in model.objects.filter(pair_scope(f"{relation}__"), **{
                f"{relation}__is_active": True, "is_active": True,
            }).values(f"{relation}__offering_id", f"{relation}__template_period_id", "student_id").annotate(
                total=Count(f"{relation}_id", distinct=True),
            ):
                target[(row[f"{relation}__offering_id"], row[f"{relation}__template_period_id"])][row["student_id"]] = row["total"]
        self._readiness = True


class DashboardActivity:
    VISIT_ACTION = "FACULTY_DASHBOARD_VISIT"
    VISIT_WINDOW = timedelta(minutes=30)

    @classmethod
    def visit_context(cls, request, *, tenant_id, campus_id, now):
        key = f"faculty_dashboard_visit:{tenant_id}:{campus_id}"
        session_key = request.session.session_key
        if not session_key:
            return key, None, None
        identity = salted_hmac(
            "faculty-dashboard-visit", json.dumps([session_key, request.user.pk, tenant_id, campus_id]),
            algorithm="sha256",
        ).hexdigest()
        current = request.session.get(key, {})
        def valid_time(value):
            if not isinstance(value, str):
                return None
            try:
                parsed = parse_datetime(value)
            except (ValueError, OverflowError):
                return None
            return parsed if parsed and timezone.is_aware(parsed) and parsed <= now else None

        started = valid_time(current.get("started")) if isinstance(current, dict) else None
        if (started and current.get("identity") == identity and
                timedelta(0) <= now - started < cls.VISIT_WINDOW):
            previous = valid_time(current.get("previous"))
            if previous and previous >= started:
                previous = None
            return key, previous, None
        if request.method != "GET":
            return key, None, None
        try:
            state = cls._claim_visit(request, identity=identity, tenant_id=tenant_id, campus_id=campus_id, now=now)
        except DatabaseError as exc:
            cls._log_visit_failure("claim", exc)
            return key, None, None
        return key, state.previous_visit_at, state

    @classmethod
    def _previous_visit(cls, request, *, tenant_id, campus_id, now):
        return AuditLog.objects.filter(
            actor_user=request.user, portal="FACULTY", action=cls.VISIT_ACTION,
            entity_type="FacultyDashboard", tenant_id=tenant_id, campus_id=campus_id,
            created_at__lt=now,
        ).order_by("-created_at", "-id").values_list("created_at", flat=True).first()

    @classmethod
    @transaction.atomic
    def _claim_visit(cls, request, *, identity, tenant_id, campus_id, now):
        scope = dict(identity_key=identity, faculty_user=request.user, tenant_id=tenant_id, campus_id=campus_id)
        try:
            # Do not gap-lock a missing identity. A unique INSERT arbitrates
            # first creation; the loser then uses a current locking read.
            state = FacultyDashboardVisitState.objects.get(**scope)
        except FacultyDashboardVisitState.DoesNotExist:
            previous = cls._previous_visit(request, tenant_id=tenant_id, campus_id=campus_id, now=now)
            try:
                with transaction.atomic():
                    return FacultyDashboardVisitState.objects.create(
                        **scope, window_started_at=now, previous_visit_at=previous,
                    )
            except IntegrityError as exc:
                try:
                    state = FacultyDashboardVisitState.objects.select_for_update().get(**scope)
                except FacultyDashboardVisitState.DoesNotExist:
                    # A non-duplicate integrity failure is still an expected
                    # persistence error, not an unrelated missing-object error.
                    raise exc
        else:
            state = FacultyDashboardVisitState.objects.select_for_update().get(pk=state.pk, **scope)
        if now - state.window_started_at >= cls.VISIT_WINDOW:
            state.window_started_at = now
            state.window_token = uuid.uuid4()
            state.previous_visit_at = cls._previous_visit(request, tenant_id=tenant_id, campus_id=campus_id, now=now)
            state.recorded_at = None
            state.save(update_fields=["window_started_at", "window_token", "previous_visit_at", "recorded_at"])
        return state

    @staticmethod
    def _log_visit_failure(phase, exc):
        # No exception text/SQL, session token, scope IDs or request metadata.
        logger.warning("Faculty dashboard visit tracking unavailable during %s (%s).", phase, type(exc).__name__)

    @classmethod
    def record_visit(cls, request, *, key, state, tenant_id, campus_id):
        try:
            with transaction.atomic():
                locked = FacultyDashboardVisitState.objects.select_for_update().filter(
                    pk=state.pk, identity_key=state.identity_key, window_token=state.window_token,
                    faculty_user=request.user, tenant_id=tenant_id, campus_id=campus_id,
                ).first()
                if locked is None:
                    return  # A delayed request must not record an expired window.
                if locked.recorded_at is None:
                    row = AuditService.log_event(
                        action=cls.VISIT_ACTION, portal="FACULTY", entity_type="FacultyDashboard",
                        entity_id=request.user.pk, actor=request.user, tenant=tenant_id, campus=campus_id, request=request,
                    )
                    locked.recorded_at = row.created_at
                    locked.save(update_fields=["recorded_at"])
        except DatabaseError as exc:
            cls._log_visit_failure("record", exc)
            return
        # Only a successful transaction (or its already-recorded winner) may
        # advance this request's session cache. Failure leaves it retryable.
        request.session[key] = {
            "identity": locked.identity_key, "started": locked.window_started_at.isoformat(),
            "previous": locked.previous_visit_at.isoformat() if locked.previous_visit_at else "",
        }

    @staticmethod
    def last_update(user, offerings):
        return FacultyGradebookModification.objects.filter(
            faculty_user=user, offering_id__in=[o.id for o in offerings],
        ).order_by("-modified_at").values_list("modified_at", flat=True).first()

    @staticmethod
    def non_compliance(user, *, tenant_id, campus_id):
        result = {"total": None, "recorded_count": None, "explanation": "The current academic year and term are not configured."}
        if not tenant_id:
            return result
        year, term = AcademicGovernanceService.resolve_active_scope(tenant_id=tenant_id)
        if not year or not term:
            return result
        if not FeatureSettingsService.is_submission_non_compliance_notice_enabled(tenant_id=tenant_id):
            result["explanation"] = "Non-compliance notices are disabled for this scope."
            return result
        notices = SubmissionNonComplianceNotice.objects.filter(
            faculty_user=user, tenant_id=tenant_id, offering__tenant_id=tenant_id,
            offering__academic_year_id=year.id, offering__term_id=term.id, campus_id=F("offering__campus_id"),
        )
        if campus_id:
            notices = notices.filter(campus_id=campus_id)
        # The issuer counts OPEN rows, resets sequence numbers after resolution,
        # and has no unique issuance/cycle constraint or historical ledger. A
        # row count cannot honestly certify a complete, deduplicated term total.
        result["recorded_count"] = notices.count()
        result["explanation"] = (
            "A complete, duplicate-free issuance history cannot be verified. "
            f"{result['recorded_count']} notice records exist in this term, including resolved notices; email retries are excluded."
        )
        return result

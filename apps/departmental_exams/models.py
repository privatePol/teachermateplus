import uuid
from contextlib import contextmanager
from contextvars import ContextVar

from django.core.exceptions import ValidationError
from django.db import models, router
from django.utils import timezone

from apps.core.models import ActivatableModel, TimeStampedModel


_EQUIVALENCY_LIFECYCLE_CAPABILITY = object()
_equivalency_lifecycle_capability = ContextVar(
    "equivalency_lifecycle_capability",
    default=None,
)


@contextmanager
def _equivalency_lifecycle_service_scope():
    """Permit lifecycle writes only inside the validated equivalency service."""

    reset_token = _equivalency_lifecycle_capability.set(
        _EQUIVALENCY_LIFECYCLE_CAPABILITY
    )
    try:
        yield
    finally:
        _equivalency_lifecycle_capability.reset(reset_token)


def _equivalency_lifecycle_write_allowed():
    return (
        _equivalency_lifecycle_capability.get()
        is _EQUIVALENCY_LIFECYCLE_CAPABILITY
    )


class _EquivalencyLifecycleQuerySet(models.QuerySet):
    lifecycle_fields = frozenset()
    lifecycle_label = "Equivalency lifecycle"

    def _reject_untrusted_fields(self, fields):
        protected = self.lifecycle_fields.intersection(fields)
        if protected and not _equivalency_lifecycle_write_allowed():
            raise ValidationError(
                f"{self.lifecycle_label} changes must use the protected equivalency service."
            )

    def update(self, **kwargs):
        self._reject_untrusted_fields(kwargs)
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, batch_size=None):
        self._reject_untrusted_fields(fields)
        return super().bulk_update(objs, fields, batch_size=batch_size)

    def bulk_create(self, objs, **kwargs):
        if objs and not _equivalency_lifecycle_write_allowed():
            raise ValidationError(
                f"{self.lifecycle_label} creation must use the protected equivalency service."
            )
        return super().bulk_create(objs, **kwargs)


class _ExamCourseEquivalencyGroupQuerySet(_EquivalencyLifecycleQuerySet):
    lifecycle_label = "Equivalency group lifecycle"
    lifecycle_fields = frozenset(
        {
            "cycle",
            "cycle_id",
            "is_active",
            "primary_cycle_course",
            "primary_cycle_course_id",
            "retired_by",
            "retired_by_id",
            "retired_at",
            "retirement_reason",
        }
    )


class _ExamCourseEquivalencyMembershipQuerySet(_EquivalencyLifecycleQuerySet):
    lifecycle_label = "Equivalency membership lifecycle"
    lifecycle_fields = frozenset(
        {
            "group",
            "group_id",
            "cycle_course",
            "cycle_course_id",
            "active_marker",
            "added_by",
            "added_by_id",
            "removed_by",
            "removed_by_id",
            "removed_at",
        }
    )

    def delete(self):
        raise ValidationError(
            "Equivalency membership history cannot be deleted through the ORM."
        )


def normalize_contribution_deadline_to_minute(value):
    """Return one aware deadline at the supported Asia/Manila minute precision."""
    if value is None:
        return None
    if timezone.is_naive(value):
        raise ValidationError("Contribution deadlines must include timezone information.")
    return timezone.localtime(value, timezone.get_default_timezone()).replace(
        second=0,
        microsecond=0,
    )


class ExaminationCycle(TimeStampedModel, ActivatableModel):
    class ProcessingMode(models.TextChoices):
        MANUAL_REVIEW = "MANUAL_REVIEW", "Manual Review"
        AUTOMATIC_GENERATION = "AUTOMATIC_GENERATION", "Automatic Generation"

    class AutomaticCampusContributionPolicy(models.TextChoices):
        STRICT = "STRICT", "Require every participating campus"
        AVAILABLE_WITH_WARNING = (
            "AVAILABLE_WITH_WARNING",
            "Use represented campuses and show a warning",
        )

    class AutomaticContributorCompletionPolicy(models.TextChoices):
        REQUIRE_ALL = "REQUIRE_ALL", "Require every active contributor"
        SUFFICIENT_POOL = (
            "SUFFICIENT_POOL",
            "Use the sufficient Submitted pool and show a warning",
        )

    class ItemCountMode(models.TextChoices):
        FIXED_ALL = "FIXED_ALL", "Fixed Item Count for All Courses"
        PER_COURSE = "PER_COURSE", "Configure Item Count per Course"
    class ExamPeriod(models.TextChoices):
        MIDTERM = "MIDTERM", "Midterm"
        FINAL = "FINAL", "Final"

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        OPEN = "OPEN", "Open"
        CLOSED = "CLOSED", "Closed"

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="examination_cycles")
    academic_year = models.ForeignKey("academics.AcademicYear", on_delete=models.PROTECT, related_name="examination_cycles")
    term = models.ForeignKey("academics.Term", on_delete=models.PROTECT, related_name="examination_cycles")
    exam_period = models.CharField(max_length=10, choices=ExamPeriod.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    processing_mode = models.CharField(
        max_length=24,
        choices=ProcessingMode.choices,
        default=ProcessingMode.MANUAL_REVIEW,
    )
    automatic_campus_contribution_policy = models.CharField(
        max_length=24,
        choices=AutomaticCampusContributionPolicy.choices,
        default=AutomaticCampusContributionPolicy.AVAILABLE_WITH_WARNING,
    )
    automatic_contributor_completion_policy = models.CharField(
        max_length=16,
        choices=AutomaticContributorCompletionPolicy.choices,
        default=AutomaticContributorCompletionPolicy.SUFFICIENT_POOL,
    )
    # Kept solely to preserve the 0002 migration history.  New runtime code
    # must use the independent CAO defaults below.
    legacy_item_count_mode = models.CharField(max_length=12, choices=ItemCountMode.choices, null=True, blank=True)
    default_final_item_count = models.PositiveSmallIntegerField(null=True, blank=True, default=None)
    default_questions_required_per_faculty = models.PositiveSmallIntegerField(null=True, blank=True, default=None)
    default_contribution_deadline = models.DateTimeField(null=True, blank=True, default=None)
    default_coverage = models.TextField(blank=True)
    defaults_revision = models.PositiveIntegerField(default=0)
    contributor_instructions = models.TextField(blank=True)
    created_by = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="created_examination_cycles")

    class Meta:
        db_table = "departmental_exam_cycles"
        constraints = [
            models.UniqueConstraint(fields=["tenant", "academic_year", "term", "exam_period"], name="uq_de_cycle_scope_period"),
            models.CheckConstraint(condition=models.Q(default_questions_required_per_faculty__isnull=True) | models.Q(default_questions_required_per_faculty__gte=50, default_questions_required_per_faculty__lte=75), name="ck_de_cycle_default_q_50_75"),
            models.CheckConstraint(condition=models.Q(default_final_item_count__isnull=True) | models.Q(default_final_item_count__gte=50, default_final_item_count__lte=75), name="ck_de_cycle_default_final_50_75"),
        ]
        indexes = [models.Index(fields=["tenant", "term", "status"], name="idx_de_cycle_scope_status")]

    def clean(self):
        if self.academic_year_id and self.term_id and self.term.academic_year_id != self.academic_year_id:
            raise ValidationError("Term must belong to the selected academic year.")
        if self.tenant_id and self.academic_year_id and self.academic_year.tenant_id != self.tenant_id:
            raise ValidationError("Academic year must belong to the selected tenant.")
        if self.tenant_id and self.term_id and self.term.tenant_id != self.tenant_id:
            raise ValidationError("Term must belong to the selected tenant.")
        for field in ("default_questions_required_per_faculty", "default_final_item_count"):
            value = getattr(self, field)
            if value is not None and not 50 <= value <= 75:
                raise ValidationError({field: "Value must be from 50 to 75."})


class CycleCourse(TimeStampedModel):
    class ExemptionCategory(models.TextChoices):
        PRACTICUM_OJT = "PRACTICUM_OJT", "Practicum / OJT"
        INTERNSHIP = "INTERNSHIP", "Internship"
        THESIS_RESEARCH = "THESIS_RESEARCH", "Thesis and Research Writing"
        CAPSTONE = "CAPSTONE", "Capstone Project"
        LABORATORY_PRACTICAL = "LABORATORY_PRACTICAL", "Laboratory / Practical"
        PORTFOLIO_BASED = "PORTFOLIO_BASED", "Portfolio-based"
        PERFORMANCE_BASED = "PERFORMANCE_BASED", "Performance-based"
        OTHER_OUTPUT_BASED = "OTHER_OUTPUT_BASED", "Other output-based"
    class InclusionStatus(models.TextChoices):
        INCLUDED = "INCLUDED", "Included"
        EXEMPT = "EXEMPT", "Exempt"

    cycle = models.ForeignKey(ExaminationCycle, on_delete=models.PROTECT, related_name="cycle_courses")
    course = models.ForeignKey("academics.Course", on_delete=models.PROTECT, related_name="exam_cycle_courses")
    responsible_department = models.ForeignKey("tenants.Department", on_delete=models.PROTECT, related_name="responsible_exam_cycle_courses", null=True, blank=True)
    reviewer = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="reviewer_cycle_courses", null=True, blank=True)
    inclusion_status = models.CharField(max_length=10, choices=InclusionStatus.choices, default=InclusionStatus.INCLUDED)
    exemption_category = models.CharField(max_length=30, choices=ExemptionCategory.choices, blank=True)
    exemption_reason = models.TextField(blank=True)
    exemption_changed_by = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="changed_exam_exemptions", null=True, blank=True)
    exemption_changed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "departmental_exam_cycle_courses"
        constraints = [models.UniqueConstraint(fields=["cycle", "course"], name="uq_de_cycle_course")]
        indexes = [models.Index(fields=["cycle", "responsible_department", "inclusion_status"], name="idx_de_cycle_course_status")]

    def clean(self):
        if self.course_id and self.cycle_id and self.course.tenant_id != self.cycle.tenant_id:
            raise ValidationError("Course must belong to the cycle tenant.")
        if (
            self.responsible_department_id
            and self.cycle_id
            and self.responsible_department.tenant_id != self.cycle.tenant_id
        ):
            raise ValidationError("Responsible department must belong to the cycle tenant.")
        exemption_reason = (self.exemption_reason or "").strip()
        if self.inclusion_status == self.InclusionStatus.EXEMPT:
            if not self.exemption_category or not exemption_reason:
                raise ValidationError("Exempt courses require an approved category and reason.")
            if not 10 <= len(exemption_reason) <= 500:
                raise ValidationError("Exemption reason must be from 10 to 500 characters.")
            if not self.exemption_changed_by_id or not self.exemption_changed_at:
                raise ValidationError("Exempt courses require the actor and time of the exemption.")
        elif self.exemption_category or exemption_reason:
            raise ValidationError("Included courses cannot retain active exemption details.")


class ExamCourseEquivalencyGroup(TimeStampedModel, ActivatableModel):
    objects = _ExamCourseEquivalencyGroupQuerySet.as_manager()

    _LIFECYCLE_FIELD_ATTNAMES = {
        "cycle": "cycle_id",
        "is_active": "is_active",
        "primary_cycle_course": "primary_cycle_course_id",
        "retired_by": "retired_by_id",
        "retired_at": "retired_at",
        "retirement_reason": "retirement_reason",
    }

    cycle = models.ForeignKey(
        ExaminationCycle,
        on_delete=models.PROTECT,
        related_name="course_equivalency_groups",
    )
    name = models.CharField(max_length=255)
    primary_cycle_course = models.ForeignKey(
        CycleCourse,
        on_delete=models.PROTECT,
        related_name="primary_equivalency_groups",
    )
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="created_exam_course_equivalency_groups",
    )
    updated_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="updated_exam_course_equivalency_groups",
    )
    retired_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="retired_exam_course_equivalency_groups",
        null=True,
        blank=True,
    )
    retired_at = models.DateTimeField(null=True, blank=True)
    retirement_reason = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        db_table = "departmental_exam_course_equivalency_groups"
        constraints = [
            models.UniqueConstraint(
                fields=["cycle", "name"],
                name="uq_de_equiv_group_cycle_name",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        is_active=True,
                        retired_by__isnull=True,
                        retired_at__isnull=True,
                        retirement_reason="",
                    )
                    | (
                        models.Q(
                            is_active=False,
                            retired_by__isnull=False,
                            retired_at__isnull=False,
                        )
                        & ~models.Q(retirement_reason="")
                    )
                ),
                name="ck_de_equiv_group_lifecycle",
            ),
        ]
        indexes = [
            models.Index(
                fields=["cycle", "is_active"],
                name="idx_de_equiv_group_active",
            )
        ]

    def clean(self):
        self.name = " ".join((self.name or "").split())
        self.retirement_reason = " ".join((self.retirement_reason or "").split())
        if not self.name:
            raise ValidationError({"name": "A shared examination name is required."})
        if (
            self.cycle_id
            and self.primary_cycle_course_id
            and self.primary_cycle_course.cycle_id != self.cycle_id
        ):
            raise ValidationError(
                {"primary_cycle_course": "Primary course must belong to the selected cycle."}
            )
        if self.is_active:
            if self.retired_by_id or self.retired_at or self.retirement_reason:
                raise ValidationError("An active equivalency group cannot retain retirement details.")
        elif (
            not self.retired_by_id
            or not self.retired_at
            or not 10 <= len(self.retirement_reason) <= 500
        ):
            raise ValidationError(
                "A retired equivalency group requires an actor, time, and reason of 10 to 500 characters."
            )

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        persisted = None
        if self.pk:
            persisted = type(self)._base_manager.filter(pk=self.pk).values(
                *self._LIFECYCLE_FIELD_ATTNAMES.values()
            ).first()
        if persisted is None:
            if not _equivalency_lifecycle_write_allowed():
                raise ValidationError(
                    "Create equivalency groups through the protected equivalency service."
                )
            if not self.is_active:
                raise ValidationError(
                    "Equivalency groups must be created active and retired through the protected service."
                )
        else:
            saved_fields = None if update_fields is None else set(update_fields)
            changed_lifecycle_fields = {
                field_name
                for field_name, attname in self._LIFECYCLE_FIELD_ATTNAMES.items()
                if (
                    saved_fields is None
                    or field_name in saved_fields
                    or attname in saved_fields
                )
                and persisted[attname] != getattr(self, attname)
            }
            if (
                "is_active" in changed_lifecycle_fields
                and persisted["is_active"] is False
                and self.is_active
            ):
                raise ValidationError("A retired equivalency group cannot be reactivated.")
            if (
                changed_lifecycle_fields
                and not _equivalency_lifecycle_write_allowed()
            ):
                raise ValidationError(
                    "Equivalency group lifecycle changes must use the protected equivalency service."
                )
            if (
                "is_active" in changed_lifecycle_fields
                and persisted["is_active"] is True
                and not self.is_active
            ):
                self.retirement_reason = " ".join(
                    (self.retirement_reason or "").split()
                )
                if (
                    not self.retired_by_id
                    or not self.retired_at
                    or not 10 <= len(self.retirement_reason) <= 500
                ):
                    raise ValidationError(
                        "Retire equivalency groups through the protected retirement service."
                    )
                if self.memberships.filter(active_marker=1).exists():
                    raise ValidationError(
                        "An equivalency group cannot be retired while active memberships remain."
                    )
        return super().save(*args, **kwargs)


class ExamCourseEquivalencyMembership(TimeStampedModel):
    objects = _ExamCourseEquivalencyMembershipQuerySet.as_manager()

    _LIFECYCLE_FIELD_ATTNAMES = {
        "group": "group_id",
        "cycle_course": "cycle_course_id",
        "active_marker": "active_marker",
        "added_by": "added_by_id",
        "removed_by": "removed_by_id",
        "removed_at": "removed_at",
    }

    group = models.ForeignKey(
        ExamCourseEquivalencyGroup,
        on_delete=models.PROTECT,
        related_name="memberships",
    )
    cycle_course = models.ForeignKey(
        CycleCourse,
        on_delete=models.PROTECT,
        related_name="equivalency_memberships",
    )
    # MariaDB permits multiple NULL values in this scoped uniqueness rule.
    # The active membership uses 1; removed historical memberships use NULL.
    active_marker = models.PositiveSmallIntegerField(null=True, blank=True, default=1)
    added_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="added_exam_course_equivalency_memberships",
    )
    removed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="removed_exam_course_equivalency_memberships",
        null=True,
        blank=True,
    )
    removed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "departmental_exam_course_equivalency_memberships"
        constraints = [
            models.UniqueConstraint(
                fields=["group", "cycle_course"],
                name="uq_de_equiv_member_group_course",
            ),
            models.UniqueConstraint(
                fields=["cycle_course", "active_marker"],
                name="uq_de_equiv_member_active_course",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        active_marker=1,
                        removed_by__isnull=True,
                        removed_at__isnull=True,
                    )
                    | models.Q(
                        active_marker__isnull=True,
                        removed_by__isnull=False,
                        removed_at__isnull=False,
                    )
                ),
                name="ck_de_equiv_member_lifecycle",
            ),
        ]
        indexes = [
            models.Index(
                fields=["group", "active_marker", "cycle_course"],
                name="idx_de_equiv_member_group",
            )
        ]

    def clean(self):
        if (
            self.group_id
            and self.cycle_course_id
            and self.cycle_course.cycle_id != self.group.cycle_id
        ):
            raise ValidationError(
                {"cycle_course": "Member course must belong to the equivalency cycle."}
            )
        if self.active_marker == 1:
            if self.removed_by_id or self.removed_at:
                raise ValidationError("Active equivalency membership cannot be removed.")
            if self.group_id and not self.group.is_active:
                raise ValidationError("Inactive equivalency groups cannot retain active members.")
        elif self.active_marker is None:
            if not self.removed_by_id or not self.removed_at:
                raise ValidationError("Removed equivalency membership requires actor and time.")
        else:
            raise ValidationError({"active_marker": "Active marker must be 1 or null."})

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        persisted = None
        if self.pk:
            persisted = type(self)._base_manager.filter(pk=self.pk).values(
                *self._LIFECYCLE_FIELD_ATTNAMES.values()
            ).first()
        if persisted is None:
            if not _equivalency_lifecycle_write_allowed():
                raise ValidationError(
                    "Create equivalency memberships through the protected equivalency service."
                )
        else:
            saved_fields = None if update_fields is None else set(update_fields)
            changed_lifecycle_fields = {
                field_name
                for field_name, attname in self._LIFECYCLE_FIELD_ATTNAMES.items()
                if (
                    saved_fields is None
                    or field_name in saved_fields
                    or attname in saved_fields
                )
                and persisted[attname] != getattr(self, attname)
            }
            if (
                changed_lifecycle_fields
                and not _equivalency_lifecycle_write_allowed()
            ):
                raise ValidationError(
                    "Equivalency membership lifecycle changes must use the protected equivalency service."
                )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(
            "Equivalency membership history cannot be deleted through the ORM."
        )


class CycleCourseOffering(TimeStampedModel):
    cycle_course = models.ForeignKey(CycleCourse, on_delete=models.PROTECT, related_name="offering_snapshots")
    offering = models.ForeignKey("academics.CourseOffering", on_delete=models.PROTECT, related_name="exam_cycle_snapshots")
    campus = models.ForeignKey("tenants.Campus", on_delete=models.PROTECT, related_name="exam_cycle_offering_snapshots")

    class Meta:
        db_table = "departmental_exam_cycle_course_offerings"
        constraints = [models.UniqueConstraint(fields=["cycle_course", "offering"], name="uq_de_cycle_course_offering")]
        indexes = [models.Index(fields=["cycle_course", "campus"], name="idx_de_cycle_course_campus")]

    def clean(self):
        if self.cycle_course_id and self.offering_id:
            cycle = self.cycle_course.cycle
            if self.offering.tenant_id != cycle.tenant_id or self.offering.course_id != self.cycle_course.course_id:
                raise ValidationError("Offering must match the cycle tenant and grouped course.")
            if self.offering.academic_year_id != cycle.academic_year_id or self.offering.term_id != cycle.term_id:
                raise ValidationError("Offering must match the cycle academic year and term.")
            if self.campus_id != self.offering.campus_id:
                raise ValidationError("Snapshot campus must match the offering campus.")


class CourseExamConfiguration(TimeStampedModel):
    _FIRST_OPEN_IMMUTABLE_DEADLINE_FIELDS = (
        "contribution_deadline",
        "contribution_deadline_source",
    )

    class ValueSource(models.TextChoices):
        DEFAULT = "DEFAULT", "Cycle default"
        OVERRIDE = "OVERRIDE", "Course override"

    class WorkflowStatus(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        OPEN = "OPEN", "Open for Faculty Contribution"
        CLOSED = "CLOSED", "Closed"

    class AutomaticProcessingStatus(models.TextChoices):
        BLOCKED = "BLOCKED", "Blocked"
        GENERATED = "GENERATED", "Generated"
        SKIPPED = "SKIPPED", "Skipped"
        ERROR = "ERROR", "Error"

    cycle_course = models.OneToOneField(CycleCourse, on_delete=models.PROTECT, related_name="configuration")
    final_item_count = models.PositiveSmallIntegerField(null=True, blank=True, default=None)
    questions_required_per_faculty = models.PositiveSmallIntegerField(null=True, blank=True, default=None)
    questions_required_per_faculty_source = models.CharField(max_length=8, choices=ValueSource.choices, null=True, blank=True)
    final_item_count_source = models.CharField(max_length=8, choices=ValueSource.choices, null=True, blank=True)
    cycle_defaults_revision_snapshot = models.PositiveIntegerField(null=True, blank=True)
    general_instructions = models.TextField(blank=True)
    contribution_deadline = models.DateTimeField(null=True, blank=True)
    contribution_deadline_source = models.CharField(max_length=8, choices=ValueSource.choices, null=True, blank=True)
    reopened_contribution_deadline = models.DateTimeField(null=True, blank=True)
    easy_percent = models.PositiveSmallIntegerField(default=30)
    moderate_percent = models.PositiveSmallIntegerField(default=50)
    difficult_percent = models.PositiveSmallIntegerField(default=20)
    workflow_status = models.CharField(max_length=10, choices=WorkflowStatus.choices, default=WorkflowStatus.DRAFT)
    opened_at = models.DateTimeField(null=True, blank=True)
    opened_by = models.ForeignKey("accounts.User", on_delete=models.PROTECT, null=True, blank=True, related_name="opened_exam_configurations")
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey("accounts.User", on_delete=models.PROTECT, null=True, blank=True, related_name="closed_exam_configurations")
    coverage = models.TextField(blank=True)
    coverage_source = models.CharField(
        max_length=8,
        choices=ValueSource.choices,
        null=True,
        blank=True,
    )
    additional_instructions = models.TextField(blank=True)
    contributor_instructions_snapshot = models.TextField(blank=True)
    legacy_item_count_mode_snapshot = models.CharField(max_length=12, choices=ExaminationCycle.ItemCountMode.choices, null=True, blank=True)
    revision = models.PositiveIntegerField(default=1)
    contributor_roster_initialized_at = models.DateTimeField(null=True, blank=True)
    contributor_roster_initialized_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="initialized_exam_contributor_rosters",
    )
    contributor_roster_revision = models.PositiveIntegerField(default=0)
    automatic_processing_status = models.CharField(
        max_length=12,
        choices=AutomaticProcessingStatus.choices,
        blank=True,
        default="",
    )
    automatic_processing_code = models.CharField(max_length=64, blank=True, default="")
    automatic_processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "departmental_exam_configurations"
        constraints = [
            models.CheckConstraint(condition=(models.Q(questions_required_per_faculty__isnull=True, questions_required_per_faculty_source__isnull=True) | models.Q(questions_required_per_faculty__isnull=False, questions_required_per_faculty_source__isnull=False, questions_required_per_faculty__gte=50, questions_required_per_faculty__lte=75, questions_required_per_faculty_source__in=["DEFAULT", "OVERRIDE"])), name="ck_de_cfg_q_value_source"),
            models.CheckConstraint(condition=(models.Q(final_item_count__isnull=True, final_item_count_source__isnull=True) | models.Q(final_item_count__isnull=False, final_item_count_source__isnull=False, final_item_count__gte=50, final_item_count__lte=75, final_item_count_source__in=["DEFAULT", "OVERRIDE"])), name="ck_de_cfg_final_value_source"),
            models.CheckConstraint(condition=(models.Q(contribution_deadline__isnull=True, contribution_deadline_source__isnull=True) | models.Q(contribution_deadline__isnull=False, contribution_deadline_source__isnull=False, contribution_deadline_source__in=["DEFAULT", "OVERRIDE"])), name="ck_de_cfg_deadline_source"),
            models.CheckConstraint(
                condition=(
                    models.Q(coverage="", coverage_source__isnull=True)
                    | (
                        ~models.Q(coverage="")
                        & models.Q(coverage_source__isnull=False)
                        & models.Q(coverage_source__in=("DEFAULT", "OVERRIDE"))
                    )
                ),
                name="ck_de_cfg_coverage_source",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        contributor_roster_initialized_at__isnull=True,
                        contributor_roster_initialized_by__isnull=True,
                        contributor_roster_revision=0,
                    )
                    | models.Q(
                        contributor_roster_initialized_at__isnull=False,
                        contributor_roster_initialized_by__isnull=False,
                        contributor_roster_revision__gte=1,
                    )
                ),
                name="ck_de_cfg_roster_state",
            ),
        ]
        indexes = [
            models.Index(fields=["workflow_status", "contribution_deadline"], name="idx_de_cfg_status_deadline"),
            models.Index(
                fields=["workflow_status", "reopened_contribution_deadline"],
                name="idx_de_cfg_reopen_deadline",
            ),
        ]

    @property
    def maximum_score(self):
        return self.final_item_count

    @property
    def active_contribution_deadline(self):
        """Return the current intake deadline without rewriting first-open history."""
        return self.reopened_contribution_deadline or self.contribution_deadline

    def _guard_first_open_deadline_pair_on_save(self, *, using, update_fields):
        """Protect persisted first-open deadline history on supported saves.

        Privileged bulk/database writers bypass model ``save()`` and must not
        be used to rewrite this historical pair.
        """
        if self._state.adding or self.pk is None:
            return
        fields_to_compare = tuple(
            field
            for field in self._FIRST_OPEN_IMMUTABLE_DEADLINE_FIELDS
            if update_fields is None or field in update_fields
        )
        if not fields_to_compare:
            return
        previous = (
            type(self)._base_manager.using(using)
            .filter(pk=self.pk)
            .values("opened_at", *fields_to_compare)
            .first()
        )
        if previous is None or previous["opened_at"] is None:
            return
        if any(previous[field] != getattr(self, field) for field in fields_to_compare):
            raise ValidationError(
                "Contribution deadline and provenance are immutable after first opening."
            )

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            update_fields = frozenset(update_fields)
            kwargs["update_fields"] = update_fields
        database = kwargs.get("using") or router.db_for_write(
            type(self), instance=self
        )
        self._guard_first_open_deadline_pair_on_save(
            using=database,
            update_fields=update_fields,
        )
        return super().save(*args, **kwargs)

    def clean(self):
        for field, source_field in (("final_item_count", "final_item_count_source"), ("questions_required_per_faculty", "questions_required_per_faculty_source")):
            value = getattr(self, field)
            source = getattr(self, source_field)
            if value is None and source is not None:
                raise ValidationError({source_field: "A source is allowed only when its value is set."})
            if value is not None and (not 50 <= value <= 75 or source not in ("DEFAULT", "OVERRIDE")):
                raise ValidationError({field: "Value must be from 50 to 75 with a default or override source."})
        if self.contribution_deadline is None and self.contribution_deadline_source is not None:
            raise ValidationError({"contribution_deadline_source": "A source is allowed only when the deadline is set."})
        if self.contribution_deadline is not None and self.contribution_deadline_source not in self.ValueSource.values:
            raise ValidationError({"contribution_deadline": "A configured deadline requires a default or override source."})
        self.coverage = (self.coverage or "").strip()
        if not self.coverage and self.coverage_source is not None:
            raise ValidationError(
                {"coverage_source": "A source is allowed only when coverage is set."}
            )
        if self.coverage and self.coverage_source not in self.ValueSource.values:
            raise ValidationError(
                {"coverage": "Configured coverage requires a default or override source."}
            )
        # Current-default equality is a live workflow rule, not a durable row
        # invariant. Protected historical rows intentionally retain their
        # materialized DEFAULT values and revision snapshot after propagation.
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).values(
                "opened_at", "questions_required_per_faculty", "questions_required_per_faculty_source",
                "final_item_count", "final_item_count_source", "contribution_deadline",
                "contribution_deadline_source", "cycle_defaults_revision_snapshot",
            ).first()
            if previous and previous["opened_at"] and any(
                previous[field] != getattr(self, field)
                for field in ("questions_required_per_faculty", "questions_required_per_faculty_source", "final_item_count", "final_item_count_source", "contribution_deadline", "contribution_deadline_source", "cycle_defaults_revision_snapshot")
            ):
                raise ValidationError("Opened course configuration effective values and provenance are immutable.")
        if (self.easy_percent, self.moderate_percent, self.difficult_percent) != (30, 50, 20):
            raise ValidationError("Version 1A uses the approved 30/50/20 difficulty distribution.")

class FacultyContribution(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        SUBMITTED = "SUBMITTED", "Submitted"

    class RosterStatus(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        BLOCKED = "BLOCKED", "Blocked"

    cycle_course = models.ForeignKey(CycleCourse, on_delete=models.PROTECT, related_name="faculty_contributions")
    faculty_user = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="exam_contributions")
    source_assignment = models.ForeignKey(
        "academics.FacultyAssignment",
        on_delete=models.SET_NULL,
        related_name="exam_contributions",
        null=True,
        blank=True,
    )
    source_campus = models.ForeignKey("tenants.Campus", on_delete=models.PROTECT, related_name="exam_contributions")
    quota_snapshot = models.PositiveSmallIntegerField()
    configuration_revision_snapshot = models.PositiveIntegerField()
    revision = models.PositiveIntegerField(default=1)
    roster_status = models.CharField(max_length=10, choices=RosterStatus.choices, default=RosterStatus.ACTIVE)
    roster_blocked_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    submitted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "departmental_exam_faculty_contributions"
        constraints = [
            models.UniqueConstraint(fields=["cycle_course", "faculty_user"], name="uq_de_contribution_faculty_course"),
            models.CheckConstraint(
                condition=models.Q(quota_snapshot__gte=50, quota_snapshot__lte=75),
                name="ck_de_contrib_quota_50_75",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status="DRAFT", submitted_at__isnull=True)
                    | models.Q(status="SUBMITTED", submitted_at__isnull=False)
                ),
                name="ck_de_contrib_submit_time",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(roster_status="ACTIVE", roster_blocked_at__isnull=True)
                    | models.Q(roster_status="BLOCKED", roster_blocked_at__isnull=False)
                ),
                name="ck_de_contrib_block_time",
            ),
            models.CheckConstraint(
                condition=models.Q(revision__gte=1, configuration_revision_snapshot__gte=1),
                name="ck_de_contrib_revisions",
            ),
        ]
        indexes = [
            models.Index(fields=["faculty_user", "status"], name="idx_de_contrib_user_status"),
            models.Index(
                fields=["cycle_course", "status", "roster_status"],
                name="idx_de_contrib_monitor",
            ),
        ]

    def clean(self):
        if self.quota_snapshot is not None and not 50 <= self.quota_snapshot <= 75:
            raise ValidationError({"quota_snapshot": "Quota must be from 50 to 75."})
        if self.status == self.Status.SUBMITTED and self.submitted_at is None:
            raise ValidationError("Submitted contributions require a submission timestamp.")
        if self.status == self.Status.DRAFT and self.submitted_at is not None:
            raise ValidationError("Draft contributions cannot have a submission timestamp.")
        if self.roster_status == self.RosterStatus.BLOCKED and self.roster_blocked_at is None:
            raise ValidationError("Blocked contributions require a blocked timestamp.")
        if self.roster_status == self.RosterStatus.ACTIVE and self.roster_blocked_at is not None:
            raise ValidationError("Active contributions cannot retain a blocked timestamp.")
        if not (self.cycle_course_id and self.source_assignment_id and self.faculty_user_id and self.source_campus_id):
            return
        offering = self.source_assignment.offering
        cycle = self.cycle_course.cycle
        if self.source_assignment.faculty_user_id != self.faculty_user_id or not self.source_assignment.is_active or not self.source_assignment.is_accepted:
            raise ValidationError("Contribution requires an active accepted assignment for this faculty member.")
        if offering.tenant_id != cycle.tenant_id or offering.academic_year_id != cycle.academic_year_id or offering.term_id != cycle.term_id or offering.course_id != self.cycle_course.course_id or offering.campus_id != self.source_campus_id:
            raise ValidationError("Contribution source assignment does not match the grouped course scope.")


class FacultyContributionEligibilitySource(TimeStampedModel):
    contribution = models.ForeignKey(
        FacultyContribution,
        on_delete=models.CASCADE,
        related_name="eligibility_sources",
    )
    assignment = models.ForeignKey(
        "academics.FacultyAssignment",
        on_delete=models.SET_NULL,
        related_name="exam_contribution_sources",
        null=True,
        blank=True,
    )
    assignment_id_snapshot = models.PositiveBigIntegerField()
    offering_id_snapshot = models.PositiveBigIntegerField()
    tenant_id_snapshot = models.PositiveBigIntegerField()
    campus_id_snapshot = models.PositiveBigIntegerField()
    is_current = models.BooleanField(default=True)
    invalidated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "departmental_exam_contribution_sources"
        constraints = [
            models.UniqueConstraint(
                fields=["contribution", "assignment_id_snapshot"],
                name="uq_de_contrib_source_assignment",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(is_current=True, invalidated_at__isnull=True)
                    | models.Q(is_current=False, invalidated_at__isnull=False)
                ),
                name="ck_de_source_invalidated",
            ),
        ]
        indexes = [
            models.Index(
                fields=["contribution", "is_current", "assignment_id_snapshot"],
                name="idx_de_source_current",
            )
        ]


class Question(TimeStampedModel):
    class Difficulty(models.TextChoices):
        EASY = "EASY", "Easy"
        MODERATE = "MODERATE", "Moderate"
        DIFFICULT = "DIFFICULT", "Difficult"

    class EntryMethod(models.TextChoices):
        MANUAL = "MANUAL", "Manual"
        CSV = "CSV", "CSV"
        DOCX = "DOCX", "Word (.docx)"

    contribution = models.ForeignKey(FacultyContribution, on_delete=models.PROTECT, related_name="questions")
    question_text = models.TextField(max_length=5000)
    choice_a = models.CharField(max_length=1000)
    choice_b = models.CharField(max_length=1000)
    choice_c = models.CharField(max_length=1000)
    choice_d = models.CharField(max_length=1000)
    correct_answer = models.CharField(max_length=1, choices=[("A", "A"), ("B", "B"), ("C", "C"), ("D", "D")])
    difficulty = models.CharField(max_length=10, choices=Difficulty.choices)
    position = models.PositiveIntegerField()
    revision = models.PositiveIntegerField(default=1)
    entry_method = models.CharField(max_length=10, choices=EntryMethod.choices, default=EntryMethod.MANUAL)
    import_batch = models.ForeignKey(
        "QuestionImportBatch",
        on_delete=models.SET_NULL,
        related_name="imported_questions",
        null=True,
        blank=True,
    )
    import_row_number = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        db_table = "departmental_exam_questions"
        constraints = [
            models.UniqueConstraint(fields=["contribution", "position"], name="uq_de_question_position"),
            models.CheckConstraint(condition=models.Q(position__gte=1), name="ck_de_question_position"),
            models.CheckConstraint(
                condition=models.Q(
                    revision__gte=1,
                    correct_answer__in=["A", "B", "C", "D"],
                    difficulty__in=["EASY", "MODERATE", "DIFFICULT"],
                    entry_method__in=["MANUAL", "CSV", "DOCX"],
                ),
                name="ck_de_question_codes",
            ),
            models.UniqueConstraint(
                fields=["import_batch", "import_row_number"],
                name="uq_de_question_import_row",
            ),
        ]
        indexes = [models.Index(fields=["contribution", "difficulty"], name="idx_de_q_contrib_difficulty")]


class QuestionImportBatch(TimeStampedModel):
    class SourceFormat(models.TextChoices):
        CSV = "CSV", "CSV"
        DOCX = "DOCX", "Word (.docx)"

    class Status(models.TextChoices):
        INVALID = "INVALID", "Invalid"
        READY = "READY", "Ready"
        IMPORTING = "IMPORTING", "Importing"
        PAUSED = "PAUSED", "Paused"
        FAILED = "FAILED", "Failed"
        CONFIRMED = "CONFIRMED", "Confirmed"
        EXPIRED = "EXPIRED", "Expired"

    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.PROTECT,
        related_name="exam_question_import_batches",
    )
    contribution = models.ForeignKey(
        FacultyContribution,
        on_delete=models.PROTECT,
        related_name="question_import_batches",
    )
    active_contribution = models.OneToOneField(
        FacultyContribution,
        on_delete=models.PROTECT,
        related_name="active_question_import_batch",
        null=True,
        blank=True,
    )
    uploading_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="uploaded_exam_question_batches",
    )
    confirming_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="confirmed_exam_question_batches",
        null=True,
        blank=True,
    )
    status = models.CharField(max_length=10, choices=Status.choices)
    source_format = models.CharField(
        max_length=4, choices=SourceFormat.choices, default=SourceFormat.CSV
    )
    contribution_revision_snapshot = models.PositiveIntegerField()
    file_sha256 = models.CharField(max_length=64)
    filename_sha256 = models.CharField(max_length=64)
    total_rows = models.PositiveSmallIntegerField(default=0)
    valid_rows = models.PositiveSmallIntegerField(default=0)
    error_count = models.PositiveSmallIntegerField(default=0)
    warning_count = models.PositiveSmallIntegerField(default=0)
    resulting_question_count = models.PositiveSmallIntegerField(default=0)
    committed_rows = models.PositiveSmallIntegerField(default=0)
    next_row_number = models.PositiveSmallIntegerField(null=True, blank=True)
    expires_at = models.DateTimeField()
    started_at = models.DateTimeField(null=True, blank=True)
    progress_updated_at = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    payload_purged_at = models.DateTimeField(null=True, blank=True)
    failure_code = models.CharField(max_length=40, blank=True, default="")
    failure_message = models.CharField(max_length=255, blank=True, default="")

    @classmethod
    def active_statuses(cls):
        return (cls.Status.IMPORTING, cls.Status.PAUSED)

    @classmethod
    def resumable_statuses(cls):
        return (cls.Status.READY, *cls.active_statuses())

    class Meta:
        db_table = "departmental_exam_question_import_batches"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(source_format__in=["CSV", "DOCX"]),
                name="ck_de_batch_source_format",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status="CONFIRMED", confirmed_at__isnull=False, payload_purged_at__isnull=False)
                    | models.Q(status="EXPIRED", confirmed_at__isnull=True, payload_purged_at__isnull=False)
                    | models.Q(
                        status__in=["INVALID", "READY"],
                        confirmed_at__isnull=True,
                        payload_purged_at__isnull=True,
                    )
                    | models.Q(
                        status="FAILED",
                        confirmed_at__isnull=True,
                        payload_purged_at__isnull=False,
                    )
                    | models.Q(
                        status__in=["IMPORTING", "PAUSED"],
                        confirmed_at__isnull=True,
                        payload_purged_at__isnull=True,
                        started_at__isnull=False,
                        progress_updated_at__isnull=False,
                    )
                ),
                name="ck_de_batch_status_times",
            ),
            models.CheckConstraint(
                condition=models.Q(valid_rows__lte=models.F("total_rows")),
                name="ck_de_batch_counts",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status__in=["READY", "IMPORTING", "PAUSED", "CONFIRMED"],
                        error_count=0,
                        valid_rows__gte=1,
                    )
                    | models.Q(status="INVALID", error_count__gte=1)
                    | models.Q(status__in=["FAILED", "EXPIRED"])
                ),
                name="ck_de_batch_validity",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status__in=["INVALID", "READY", "FAILED", "EXPIRED"],
                        committed_rows=0,
                        next_row_number__isnull=True,
                    )
                    | models.Q(
                        status="CONFIRMED",
                        committed_rows=models.F("total_rows"),
                        next_row_number__isnull=True,
                    )
                    | models.Q(
                        status__in=["IMPORTING", "PAUSED"],
                        committed_rows__lt=models.F("total_rows"),
                        next_row_number__isnull=False,
                    )
                ),
                name="ck_de_batch_progress",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status__in=["IMPORTING", "PAUSED"],
                        active_contribution=models.F("contribution"),
                    )
                    | models.Q(
                        status__in=["INVALID", "READY", "FAILED", "CONFIRMED", "EXPIRED"],
                        active_contribution__isnull=True,
                    )
                ),
                name="ck_de_batch_active_contrib",
            ),
        ]
        indexes = [
            models.Index(
                fields=["uploading_user", "status", "expires_at"],
                name="idx_de_batch_owner_status",
            ),
            models.Index(
                fields=["contribution", "status", "expires_at"],
                name="idx_de_batch_contrib_status",
            ),
        ]


class QuestionImportRow(TimeStampedModel):
    batch = models.ForeignKey(
        QuestionImportBatch,
        on_delete=models.CASCADE,
        related_name="rows",
    )
    row_number = models.PositiveSmallIntegerField()
    payload = models.JSONField(default=dict)
    errors = models.JSONField(default=list)
    warnings = models.JSONField(default=list)
    fingerprint = models.CharField(max_length=64, blank=True)

    class Meta:
        db_table = "departmental_exam_question_import_rows"
        constraints = [
            models.UniqueConstraint(
                fields=["batch", "row_number"],
                name="uq_de_batch_row_number",
            )
        ]
        indexes = [models.Index(fields=["batch", "row_number"], name="idx_de_batch_row")]


class BlockedContributionResolution(TimeStampedModel):
    """Immutable acceptance of one exact Blocked Draft evidence state."""

    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.PROTECT,
        related_name="blocked_exam_contribution_resolutions",
    )
    cycle_course = models.ForeignKey(
        CycleCourse,
        on_delete=models.PROTECT,
        related_name="blocked_contribution_resolutions",
    )
    contribution = models.ForeignKey(
        FacultyContribution,
        on_delete=models.PROTECT,
        related_name="blocked_resolution_events",
    )
    reason = models.TextField(max_length=500)
    resolved_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="resolved_blocked_exam_contributions",
    )
    resolved_at = models.DateTimeField(default=timezone.now)
    contribution_revision_snapshot = models.PositiveIntegerField()
    roster_revision_snapshot = models.PositiveIntegerField()
    blocked_at_snapshot = models.DateTimeField()
    source_evidence_sha256 = models.CharField(max_length=64)

    class Meta:
        db_table = "departmental_exam_blocked_resolutions"
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "contribution",
                    "blocked_at_snapshot",
                    "contribution_revision_snapshot",
                ],
                name="uq_de_block_resolution_state",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    contribution_revision_snapshot__gte=1,
                    roster_revision_snapshot__gte=1,
                ),
                name="ck_de_block_resolution_revisions",
            ),
        ]
        indexes = [
            models.Index(
                fields=["cycle_course", "roster_revision_snapshot"],
                name="idx_de_block_resolution_roster",
            )
        ]

    def clean(self):
        reason = (self.reason or "").strip()
        if not 10 <= len(reason) <= 500:
            raise ValidationError({"reason": "Reason must be from 10 to 500 characters."})
        if self.contribution_id:
            if self.cycle_course_id != self.contribution.cycle_course_id:
                raise ValidationError("Resolution must match the contribution course.")
            if self.tenant_id != self.contribution.cycle_course.cycle.tenant_id:
                raise ValidationError("Resolution must match the contribution tenant.")

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Blocked contribution resolution evidence is immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Blocked contribution resolution evidence is immutable.")


class ExamBlueprint(TimeStampedModel):
    class Mode(models.TextChoices):
        NO_SECTIONS = "NO_SECTIONS", "No Sections"
        USE_SECTIONS = "USE_SECTIONS", "Use Sections"

    cycle_course = models.OneToOneField(
        CycleCourse,
        on_delete=models.PROTECT,
        related_name="exam_blueprint",
    )
    mode = models.CharField(
        max_length=12,
        choices=Mode.choices,
        default=Mode.NO_SECTIONS,
    )
    revision = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="created_exam_blueprints",
    )
    updated_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="updated_exam_blueprints",
    )

    class Meta:
        db_table = "departmental_exam_blueprints"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(revision__gte=1),
                name="ck_de_blueprint_revision",
            )
        ]
        indexes = [
            models.Index(fields=["mode", "updated_at"], name="idx_de_blueprint_mode")
        ]


class ExamSection(TimeStampedModel):
    blueprint = models.ForeignKey(
        ExamBlueprint,
        on_delete=models.PROTECT,
        related_name="sections",
    )
    title = models.CharField(max_length=200)
    instructions = models.TextField(max_length=2000, blank=True)
    display_order = models.PositiveSmallIntegerField()
    item_quota = models.PositiveSmallIntegerField()

    class Meta:
        db_table = "departmental_exam_sections"
        constraints = [
            models.UniqueConstraint(
                fields=["blueprint", "display_order"],
                name="uq_de_section_blueprint_order",
            ),
            models.CheckConstraint(
                condition=models.Q(display_order__gte=1, item_quota__gte=1),
                name="ck_de_section_positive_values",
            ),
        ]
        indexes = [
            models.Index(
                fields=["blueprint", "display_order"],
                name="idx_de_section_order",
            )
        ]

    def clean(self):
        if not (self.title or "").strip():
            raise ValidationError({"title": "Section title is required."})
        if self.blueprint_id and self.blueprint.mode != ExamBlueprint.Mode.USE_SECTIONS:
            raise ValidationError("Explicit sections require Use Sections mode.")


class QuestionBlueprintPlacement(TimeStampedModel):
    blueprint = models.ForeignKey(
        ExamBlueprint,
        on_delete=models.PROTECT,
        related_name="question_placements",
    )
    question = models.OneToOneField(
        Question,
        on_delete=models.PROTECT,
        related_name="blueprint_placement",
    )
    section = models.ForeignKey(
        ExamSection,
        on_delete=models.PROTECT,
        related_name="question_placements",
    )
    placed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="exam_question_placements",
    )
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "departmental_exam_question_placements"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(revision__gte=1),
                name="ck_de_placement_revision",
            )
        ]
        indexes = [
            models.Index(
                fields=["blueprint", "section"],
                name="idx_de_placement_section",
            )
        ]

    def clean(self):
        if self.blueprint_id and self.section_id and self.section.blueprint_id != self.blueprint_id:
            raise ValidationError("Placement section must belong to the same blueprint.")
        if self.blueprint_id and self.question_id:
            if self.question.contribution.cycle_course_id != self.blueprint.cycle_course_id:
                raise ValidationError("Placement question must belong to the same course examination.")
            if self.question.contribution.status != FacultyContribution.Status.SUBMITTED:
                raise ValidationError("Only Submitted questions may be classified.")


class ExamScenario(TimeStampedModel):
    blueprint = models.ForeignKey(
        ExamBlueprint,
        on_delete=models.PROTECT,
        related_name="scenarios",
    )
    section = models.ForeignKey(
        ExamSection,
        on_delete=models.PROTECT,
        related_name="scenarios",
        null=True,
        blank=True,
    )
    title = models.CharField(max_length=200, blank=True)
    stimulus = models.TextField(max_length=5000)
    revision = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="created_exam_scenarios",
    )
    updated_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="updated_exam_scenarios",
    )

    class Meta:
        db_table = "departmental_exam_scenarios"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(revision__gte=1),
                name="ck_de_scenario_revision",
            )
        ]
        indexes = [
            models.Index(
                fields=["blueprint", "section"],
                name="idx_de_scenario_section",
            )
        ]

    def clean(self):
        if not (self.stimulus or "").strip():
            raise ValidationError({"stimulus": "Scenario text is required."})
        if self.blueprint_id:
            if self.blueprint.mode == ExamBlueprint.Mode.USE_SECTIONS:
                if not self.section_id or self.section.blueprint_id != self.blueprint_id:
                    raise ValidationError("Use Sections scenarios require a section in the same blueprint.")
            elif self.section_id is not None:
                raise ValidationError("No Sections scenarios use the implicit section.")


class ExamScenarioMember(TimeStampedModel):
    scenario = models.ForeignKey(
        ExamScenario,
        on_delete=models.CASCADE,
        related_name="members",
    )
    question = models.OneToOneField(
        Question,
        on_delete=models.PROTECT,
        related_name="exam_scenario_membership",
    )
    position = models.PositiveSmallIntegerField()

    class Meta:
        db_table = "departmental_exam_scenario_members"
        constraints = [
            models.UniqueConstraint(
                fields=["scenario", "position"],
                name="uq_de_scenario_member_position",
            ),
            models.CheckConstraint(
                condition=models.Q(position__gte=1),
                name="ck_de_scenario_member_position",
            ),
        ]
        indexes = [
            models.Index(
                fields=["scenario", "position"],
                name="idx_de_scenario_member_order",
            )
        ]

    def clean(self):
        if self.scenario_id and self.question_id:
            if self.question.contribution.cycle_course_id != self.scenario.blueprint.cycle_course_id:
                raise ValidationError("Scenario questions must belong to the same course examination.")
            if self.question.contribution.status != FacultyContribution.Status.SUBMITTED:
                raise ValidationError("Only Submitted questions may belong to scenarios.")


class ExamGenerationRevision(TimeStampedModel):
    _IMMUTABLE_FIELDS = (
        "cycle_course_id",
        "revision_number",
        "source_input_fingerprint",
        "algorithm_version",
        "generated_at",
        "generated_by_id",
        "generation_trigger",
        "configuration_revision_snapshot",
        "blueprint_revision_snapshot",
        "roster_boundary_snapshot",
        "final_item_count_snapshot",
        "request_token_digest",
        "supersedes_id",
        "regeneration_reason",
        "minimum_overlap",
        "proportional_score",
        "contributors_represented",
        "squared_contributor_concentration",
    )
    class Status(models.TextChoices):
        GENERATED = "GENERATED", "Generated"
        SUPERSEDED = "SUPERSEDED", "Superseded"
        LOCKED = "LOCKED", "Locked"

    class GenerationTrigger(models.TextChoices):
        MANUAL = "MANUAL", "Manual"
        AUTOMATIC = "AUTOMATIC", "Automatic"

    cycle_course = models.ForeignKey(
        CycleCourse,
        on_delete=models.PROTECT,
        related_name="generation_revisions",
    )
    revision_number = models.PositiveIntegerField()
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.GENERATED,
    )
    # MariaDB permits multiple NULL values in a composite unique constraint.
    # Current rows use 1; superseded rows use NULL.
    current_marker = models.PositiveSmallIntegerField(null=True, blank=True, default=1)
    source_input_fingerprint = models.CharField(max_length=64)
    algorithm_version = models.CharField(max_length=64)
    generated_at = models.DateTimeField(default=timezone.now)
    generated_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="generated_departmental_exams",
        null=True,
        blank=True,
    )
    generation_trigger = models.CharField(
        max_length=12,
        choices=GenerationTrigger.choices,
        default=GenerationTrigger.MANUAL,
    )
    configuration_revision_snapshot = models.PositiveIntegerField()
    blueprint_revision_snapshot = models.PositiveIntegerField()
    roster_boundary_snapshot = models.CharField(max_length=64)
    final_item_count_snapshot = models.PositiveSmallIntegerField()
    request_token_digest = models.CharField(max_length=64)
    supersedes = models.OneToOneField(
        "self",
        on_delete=models.PROTECT,
        related_name="superseded_by",
        null=True,
        blank=True,
    )
    regeneration_reason = models.TextField(max_length=500, blank=True)
    minimum_overlap = models.PositiveSmallIntegerField()
    proportional_score = models.PositiveBigIntegerField()
    contributors_represented = models.PositiveSmallIntegerField()
    squared_contributor_concentration = models.PositiveIntegerField()
    locked_at = models.DateTimeField(null=True, blank=True)
    locked_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="locked_departmental_exams",
    )
    approval_attestation_version = models.CharField(
        max_length=32,
        blank=True,
        default="",
    )

    class Meta:
        db_table = "departmental_exam_generation_revisions"
        constraints = [
            models.UniqueConstraint(
                fields=["cycle_course", "revision_number"],
                name="uq_de_gen_course_revision",
            ),
            models.UniqueConstraint(
                fields=["cycle_course", "current_marker"],
                name="uq_de_gen_course_current",
            ),
            models.UniqueConstraint(
                fields=["cycle_course", "request_token_digest"],
                name="uq_de_gen_course_token",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    revision_number__gte=1,
                    configuration_revision_snapshot__gte=1,
                    blueprint_revision_snapshot__gte=1,
                    final_item_count_snapshot__gte=1,
                ),
                name="ck_de_gen_positive_values",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status="GENERATED",
                        current_marker=1,
                        current_marker__isnull=False,
                        locked_at__isnull=True,
                        locked_by__isnull=True,
                        approval_attestation_version="",
                    )
                    | models.Q(
                        status="SUPERSEDED",
                        current_marker__isnull=True,
                        locked_at__isnull=True,
                        locked_by__isnull=True,
                        approval_attestation_version="",
                    )
                    | models.Q(
                        status="LOCKED",
                        current_marker=1,
                        current_marker__isnull=False,
                        locked_at__isnull=False,
                        locked_by__isnull=False,
                    )
                    & ~models.Q(approval_attestation_version="")
                ),
                name="ck_de_gen_current_status",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(generation_trigger="AUTOMATIC", generated_by__isnull=True)
                    | models.Q(generation_trigger="MANUAL", generated_by__isnull=False)
                ),
                name="ck_de_gen_trigger_actor",
            ),
        ]
        indexes = [
            models.Index(
                fields=["cycle_course", "-revision_number"],
                name="idx_de_gen_course_revision",
            ),
            models.Index(
                fields=["source_input_fingerprint"],
                name="idx_de_gen_fingerprint",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).values(
                "status",
                "current_marker",
                "locked_at",
                "locked_by_id",
                "approval_attestation_version",
                *self._IMMUTABLE_FIELDS,
            ).first()
            if previous is not None:
                if any(
                    previous[field] != getattr(self, field)
                    for field in self._IMMUTABLE_FIELDS
                ):
                    raise ValidationError("Generation revision snapshots are immutable.")
                before_state = (previous["status"], previous["current_marker"])
                after_state = (self.status, self.current_marker)
                allowed = {
                    (self.Status.GENERATED, 1): {
                        (self.Status.GENERATED, 1),
                        (self.Status.SUPERSEDED, None),
                        (self.Status.LOCKED, 1),
                    },
                    (self.Status.SUPERSEDED, None): {
                        (self.Status.SUPERSEDED, None),
                    },
                    (self.Status.LOCKED, 1): {(self.Status.LOCKED, 1)},
                }
                if after_state not in allowed.get(before_state, set()):
                    raise ValidationError("Generation revision lifecycle transition is invalid.")
                lock_before = (
                    previous["locked_at"],
                    previous["locked_by_id"],
                    previous["approval_attestation_version"],
                )
                lock_after = (
                    self.locked_at,
                    self.locked_by_id,
                    self.approval_attestation_version,
                )
                if before_state == (self.Status.GENERATED, 1) and after_state == (
                    self.Status.LOCKED,
                    1,
                ):
                    if not all(lock_after):
                        raise ValidationError("Approve & Lock metadata is incomplete.")
                elif lock_before != lock_after:
                    raise ValidationError("Approve & Lock metadata is immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Generation revisions are immutable historical records.")


class QuestionnairePrintRelease(TimeStampedModel):
    _IMMUTABLE_FIELDS = (
        "cycle_course_id",
        "generation_revision_id",
        "print_from",
        "print_until",
        "released_by_id",
        "released_at",
    )

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        REVOKED = "REVOKED", "Revoked"

    cycle_course = models.ForeignKey(
        CycleCourse,
        on_delete=models.PROTECT,
        related_name="questionnaire_print_releases",
    )
    generation_revision = models.ForeignKey(
        ExamGenerationRevision,
        on_delete=models.PROTECT,
        related_name="questionnaire_print_releases",
    )
    print_from = models.DateTimeField()
    print_until = models.DateTimeField()
    released_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="released_departmental_exam_questionnaires",
    )
    released_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(
        max_length=8,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    # MariaDB permits multiple NULL values in this scoped uniqueness rule.
    # The sole active row uses 1; historical rows use NULL.
    active_marker = models.PositiveSmallIntegerField(null=True, blank=True, default=1)
    revoked_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="revoked_departmental_exam_questionnaire_releases",
        null=True,
        blank=True,
    )
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "departmental_exam_questionnaire_print_releases"
        constraints = [
            models.UniqueConstraint(
                fields=["cycle_course", "active_marker"],
                name="uq_de_print_release_active",
            ),
            models.CheckConstraint(
                condition=models.Q(print_until__gt=models.F("print_from")),
                name="ck_de_print_release_window",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status="ACTIVE",
                        active_marker=1,
                        revoked_by__isnull=True,
                        revoked_at__isnull=True,
                    )
                    | models.Q(
                        status="REVOKED",
                        active_marker__isnull=True,
                        revoked_by__isnull=False,
                        revoked_at__isnull=False,
                    )
                ),
                name="ck_de_print_release_status",
            ),
        ]
        indexes = [
            models.Index(
                fields=["cycle_course", "status", "print_from", "print_until"],
                name="idx_de_print_release_window",
            ),
            models.Index(
                fields=["generation_revision", "status"],
                name="idx_de_print_release_revision",
            ),
        ]

    def clean(self):
        if self.print_from and self.print_until and self.print_until <= self.print_from:
            raise ValidationError(
                {"print_until": "Print Until must be later than Print From."}
            )
        if (
            self.cycle_course_id
            and self.generation_revision_id
            and self.generation_revision.cycle_course_id != self.cycle_course_id
        ):
            raise ValidationError(
                {"generation_revision": "Released revision must belong to the selected course examination."}
            )

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).values(
                "status",
                "active_marker",
                "revoked_by_id",
                "revoked_at",
                *self._IMMUTABLE_FIELDS,
            ).first()
            if previous is not None:
                if any(
                    previous[field] != getattr(self, field)
                    for field in self._IMMUTABLE_FIELDS
                ):
                    raise ValidationError("Questionnaire print release details are immutable.")
                before_state = (previous["status"], previous["active_marker"])
                after_state = (self.status, self.active_marker)
                allowed = {
                    (self.Status.ACTIVE, 1): {
                        (self.Status.ACTIVE, 1),
                        (self.Status.REVOKED, None),
                    },
                    (self.Status.REVOKED, None): {(self.Status.REVOKED, None)},
                }
                if after_state not in allowed.get(before_state, set()):
                    raise ValidationError("Questionnaire print release lifecycle transition is invalid.")
                revoked_before = (
                    previous["revoked_by_id"],
                    previous["revoked_at"],
                )
                revoked_after = (self.revoked_by_id, self.revoked_at)
                if before_state == (self.Status.ACTIVE, 1) and after_state == (
                    self.Status.REVOKED,
                    None,
                ):
                    if not all(revoked_after):
                        raise ValidationError("Questionnaire print revocation metadata is incomplete.")
                elif revoked_before != revoked_after:
                    raise ValidationError("Questionnaire print revocation metadata is immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Questionnaire print releases are auditable historical records.")


class PersonalizedAnswerSheetAssignment(TimeStampedModel):
    ALGORITHM_VERSION = "hmac-alternate-v1"
    _IMMUTABLE_FIELDS = (
        "generation_revision_id",
        "enrollment_id",
        "course_offering_id",
        "set_code",
        "assignment_method",
        "algorithm_version",
        "assigned_by_id",
        "public_id",
    )

    class AssignmentMethod(models.TextChoices):
        INITIAL_BALANCED = "INITIAL_BALANCED", "Initial balanced assignment"
        LATE_BALANCED = "LATE_BALANCED", "Late balanced assignment"

    generation_revision = models.ForeignKey(
        ExamGenerationRevision,
        on_delete=models.PROTECT,
        related_name="personalized_answer_sheet_assignments",
    )
    enrollment = models.ForeignKey(
        "enrollment.Enrollment",
        on_delete=models.PROTECT,
        related_name="personalized_answer_sheet_assignments",
    )
    course_offering = models.ForeignKey(
        "academics.CourseOffering",
        on_delete=models.PROTECT,
        related_name="personalized_answer_sheet_assignments",
    )
    set_code = models.CharField(
        max_length=1,
        choices=(("A", "Set A"), ("B", "Set B")),
    )
    assignment_method = models.CharField(
        max_length=20,
        choices=AssignmentMethod.choices,
    )
    algorithm_version = models.CharField(
        max_length=64,
        default=ALGORITHM_VERSION,
    )
    assigned_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="assigned_personalized_answer_sheets",
    )
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    class Meta:
        db_table = "departmental_exam_personalized_sheet_assignments"
        constraints = [
            models.UniqueConstraint(
                fields=["generation_revision", "enrollment"],
                name="uq_de_personalized_revision_enrollment",
            ),
            models.CheckConstraint(
                condition=models.Q(set_code__in=["A", "B"]),
                name="ck_de_personalized_set_code",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    assignment_method__in=["INITIAL_BALANCED", "LATE_BALANCED"]
                ),
                name="ck_de_personalized_method",
            ),
        ]
        indexes = [
            models.Index(
                fields=["generation_revision", "course_offering", "set_code"],
                name="idx_de_pers_rev_offer_set",
            )
        ]

    def clean(self):
        errors = {}
        if self.enrollment_id and self.course_offering_id:
            enrollment = self.enrollment
            offering = self.course_offering
            if enrollment.course_offering_id != offering.id:
                errors["course_offering"] = "Enrollment must belong to the selected course offering."
            elif (
                enrollment.tenant_id != offering.tenant_id
                or enrollment.campus_id != offering.campus_id
                or enrollment.academic_year_id != offering.academic_year_id
                or enrollment.term_id != offering.term_id
            ):
                errors["enrollment"] = "Enrollment scope must match the selected course offering."
        if self.generation_revision_id and self.course_offering_id:
            revision = self.generation_revision
            cycle_course = revision.cycle_course
            cycle = cycle_course.cycle
            offering = self.course_offering
            from .exam_units import resolve_examination_unit

            unit = resolve_examination_unit(cycle_course)
            matching_member = next(
                (
                    member
                    for member in unit.members
                    if member.course_id == offering.course_id
                ),
                None,
            )
            if (
                offering.tenant_id != cycle.tenant_id
                or matching_member is None
                or offering.academic_year_id != cycle.academic_year_id
                or offering.term_id != cycle.term_id
                or offering.campus_id is None
            ):
                errors["generation_revision"] = (
                    "Generation revision and course offering scope must match."
                )
            elif not CycleCourseOffering.objects.filter(
                cycle_course=matching_member,
                offering=offering,
                campus_id=offering.campus_id,
            ).exists():
                errors["course_offering"] = (
                    "Course offering must belong to the generated course examination."
                )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).values(
                *self._IMMUTABLE_FIELDS
            ).first()
            if previous is not None and any(
                previous[field] != getattr(self, field)
                for field in self._IMMUTABLE_FIELDS
            ):
                raise ValidationError("Personalized answer-sheet assignments are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(
            "Personalized answer-sheet assignments are auditable historical records."
        )


class AnswerKeyRelease(TimeStampedModel):
    _IMMUTABLE_FIELDS = (
        "cycle_course_id",
        "generation_revision_id",
        "available_from",
        "available_until",
        "released_by_id",
        "released_at",
        "attestation_version",
    )

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        REVOKED = "REVOKED", "Revoked"

    cycle_course = models.ForeignKey(
        CycleCourse,
        on_delete=models.PROTECT,
        related_name="answer_key_releases",
    )
    generation_revision = models.ForeignKey(
        ExamGenerationRevision,
        on_delete=models.PROTECT,
        related_name="answer_key_releases",
    )
    available_from = models.DateTimeField()
    available_until = models.DateTimeField()
    released_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="released_departmental_exam_answer_keys",
    )
    released_at = models.DateTimeField(default=timezone.now)
    attestation_version = models.CharField(max_length=64)
    status = models.CharField(
        max_length=8,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    # MariaDB permits multiple NULL values in this scoped uniqueness rule.
    # The sole active row uses 1; historical rows use NULL.
    active_marker = models.PositiveSmallIntegerField(null=True, blank=True, default=1)
    revoked_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="revoked_departmental_exam_answer_key_releases",
        null=True,
        blank=True,
    )
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "departmental_exam_answer_key_releases"
        constraints = [
            models.UniqueConstraint(
                fields=["cycle_course", "active_marker"],
                name="uq_de_key_release_active",
            ),
            models.CheckConstraint(
                condition=models.Q(available_until__gt=models.F("available_from")),
                name="ck_de_key_release_window",
            ),
            models.CheckConstraint(
                condition=~models.Q(attestation_version=""),
                name="ck_de_key_attestation",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status="ACTIVE",
                        active_marker=1,
                        revoked_by__isnull=True,
                        revoked_at__isnull=True,
                    )
                    | models.Q(
                        status="REVOKED",
                        active_marker__isnull=True,
                        revoked_by__isnull=False,
                        revoked_at__isnull=False,
                    )
                ),
                name="ck_de_key_release_status",
            ),
        ]
        indexes = [
            models.Index(
                fields=[
                    "cycle_course",
                    "status",
                    "available_from",
                    "available_until",
                ],
                name="idx_de_key_release_window",
            ),
            models.Index(
                fields=["generation_revision", "status"],
                name="idx_de_key_release_rev",
            ),
        ]

    def clean(self):
        if (
            self.available_from
            and self.available_until
            and self.available_until <= self.available_from
        ):
            raise ValidationError(
                {"available_until": "Available Until must be later than Available From."}
            )
        if not (self.attestation_version or "").strip():
            raise ValidationError(
                {"attestation_version": "Release attestation is required."}
            )
        if (
            self.cycle_course_id
            and self.generation_revision_id
            and self.generation_revision.cycle_course_id != self.cycle_course_id
        ):
            raise ValidationError(
                {"generation_revision": "Released revision must belong to the selected course examination."}
            )

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).values(
                "status",
                "active_marker",
                "revoked_by_id",
                "revoked_at",
                *self._IMMUTABLE_FIELDS,
            ).first()
            if previous is not None:
                if any(
                    previous[field] != getattr(self, field)
                    for field in self._IMMUTABLE_FIELDS
                ):
                    raise ValidationError("Answer Key release details are immutable.")
                before_state = (previous["status"], previous["active_marker"])
                after_state = (self.status, self.active_marker)
                allowed = {
                    (self.Status.ACTIVE, 1): {
                        (self.Status.ACTIVE, 1),
                        (self.Status.REVOKED, None),
                    },
                    (self.Status.REVOKED, None): {(self.Status.REVOKED, None)},
                }
                if after_state not in allowed.get(before_state, set()):
                    raise ValidationError("Answer Key release lifecycle transition is invalid.")
                revoked_before = (
                    previous["revoked_by_id"],
                    previous["revoked_at"],
                )
                revoked_after = (self.revoked_by_id, self.revoked_at)
                if before_state == (self.Status.ACTIVE, 1) and after_state == (
                    self.Status.REVOKED,
                    None,
                ):
                    if not all(revoked_after):
                        raise ValidationError("Answer Key revocation metadata is incomplete.")
                elif revoked_before != revoked_after:
                    raise ValidationError("Answer Key revocation metadata is immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Answer Key releases are auditable historical records.")


class GeneratedExamSet(TimeStampedModel):
    class SetCode(models.TextChoices):
        A = "A", "Set A"
        B = "B", "Set B"

    generation_revision = models.ForeignKey(
        ExamGenerationRevision,
        on_delete=models.PROTECT,
        related_name="generated_sets",
    )
    set_code = models.CharField(max_length=1, choices=SetCode.choices)
    campus_quotas_snapshot = models.JSONField(default=dict)
    difficulty_quotas_snapshot = models.JSONField(default=dict)
    section_quotas_snapshot = models.JSONField(default=dict)
    item_count = models.PositiveSmallIntegerField()

    class Meta:
        db_table = "departmental_exam_generated_sets"
        constraints = [
            models.UniqueConstraint(
                fields=["generation_revision", "set_code"],
                name="uq_de_gen_set_code",
            ),
            models.CheckConstraint(
                condition=models.Q(set_code__in=["A", "B"], item_count__gte=1),
                name="ck_de_gen_set_values",
            ),
        ]
        indexes = [
            models.Index(
                fields=["generation_revision", "set_code"],
                name="idx_de_gen_set_code",
            )
        ]

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Generated examination set snapshots are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Generated examination set snapshots are immutable.")


class GeneratedExamItem(TimeStampedModel):
    generated_set = models.ForeignKey(
        GeneratedExamSet,
        on_delete=models.PROTECT,
        related_name="items",
    )
    position = models.PositiveSmallIntegerField()
    source_question = models.ForeignKey(
        Question,
        on_delete=models.PROTECT,
        related_name="generated_exam_items",
    )
    source_question_revision = models.PositiveIntegerField()
    source_question_digest = models.CharField(max_length=64)
    source_contributor = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="generated_exam_item_snapshots",
    )
    source_contributor_id_snapshot = models.PositiveBigIntegerField()
    source_contributor_name_snapshot = models.CharField(max_length=255)
    source_campus = models.ForeignKey(
        "tenants.Campus",
        on_delete=models.PROTECT,
        related_name="generated_exam_item_snapshots",
    )
    campus_code_snapshot = models.CharField(max_length=30)
    campus_name_snapshot = models.CharField(max_length=120)
    difficulty_snapshot = models.CharField(max_length=10, choices=Question.Difficulty.choices)
    source_section = models.ForeignKey(
        ExamSection,
        on_delete=models.PROTECT,
        related_name="generated_exam_item_snapshots",
        null=True,
        blank=True,
    )
    section_id_snapshot = models.PositiveBigIntegerField(null=True, blank=True)
    section_title_snapshot = models.CharField(max_length=200)
    section_instructions_snapshot = models.TextField(max_length=2000, blank=True)
    question_text_snapshot = models.TextField(max_length=5000)
    choices_snapshot = models.JSONField(default=list)
    correct_answer_snapshot = models.CharField(
        max_length=1,
        choices=[("A", "A"), ("B", "B"), ("C", "C"), ("D", "D")],
    )
    source_scenario = models.ForeignKey(
        ExamScenario,
        on_delete=models.PROTECT,
        related_name="generated_exam_item_snapshots",
        null=True,
        blank=True,
    )
    scenario_id_snapshot = models.PositiveBigIntegerField(null=True, blank=True)
    scenario_revision_snapshot = models.PositiveIntegerField(null=True, blank=True)
    scenario_title_snapshot = models.CharField(max_length=200, blank=True)
    scenario_stimulus_snapshot = models.TextField(max_length=5000, blank=True)
    scenario_member_position_snapshot = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "departmental_exam_generated_items"
        constraints = [
            models.UniqueConstraint(
                fields=["generated_set", "position"],
                name="uq_de_gen_item_position",
            ),
            models.UniqueConstraint(
                fields=["generated_set", "source_question"],
                name="uq_de_gen_item_source",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    position__gte=1,
                    source_question_revision__gte=1,
                    correct_answer_snapshot__in=["A", "B", "C", "D"],
                    difficulty_snapshot__in=["EASY", "MODERATE", "DIFFICULT"],
                ),
                name="ck_de_gen_item_values",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        source_scenario__isnull=True,
                        scenario_id_snapshot__isnull=True,
                        scenario_revision_snapshot__isnull=True,
                        scenario_member_position_snapshot__isnull=True,
                        scenario_title_snapshot="",
                        scenario_stimulus_snapshot="",
                    )
                    | models.Q(
                        source_scenario__isnull=False,
                        scenario_id_snapshot__isnull=False,
                        scenario_revision_snapshot__gte=1,
                        scenario_member_position_snapshot__gte=1,
                    )
                ),
                name="ck_de_gen_item_scenario",
            ),
        ]
        indexes = [
            models.Index(
                fields=["generated_set", "position"],
                name="idx_de_gen_item_position",
            ),
            models.Index(
                fields=["source_question"],
                name="idx_de_gen_item_source",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Generated examination item snapshots are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Generated examination item snapshots are immutable.")


class GenerationSourceAuditSnapshot(TimeStampedModel):
    generation_revision = models.OneToOneField(
        ExamGenerationRevision,
        on_delete=models.PROTECT,
        related_name="source_audit_snapshot",
    )
    schema_version = models.CharField(max_length=32)
    logical_identity_version = models.CharField(max_length=32)
    submitted_count = models.PositiveIntegerField()
    eligible_count = models.PositiveIntegerField()
    unique_logical_count = models.PositiveIntegerField()
    redundant_copy_count = models.PositiveIntegerField()

    class Meta:
        db_table = "departmental_exam_generation_source_audits"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    submitted_count__gte=models.F("eligible_count"),
                    eligible_count__gte=models.F("unique_logical_count"),
                ),
                name="ck_de_source_audit_counts",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    redundant_copy_count=(
                        models.F("eligible_count")
                        - models.F("unique_logical_count")
                    )
                ),
                name="ck_de_source_audit_redundant",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Generation source audit snapshots are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Generation source audit snapshots are immutable.")


class GenerationSourceQuestionSnapshot(TimeStampedModel):
    audit_snapshot = models.ForeignKey(
        GenerationSourceAuditSnapshot,
        on_delete=models.PROTECT,
        related_name="question_snapshots",
    )
    source_question = models.ForeignKey(
        Question,
        on_delete=models.PROTECT,
        related_name="generation_source_audit_snapshots",
    )
    source_question_id_snapshot = models.PositiveBigIntegerField()
    source_question_revision = models.PositiveIntegerField()
    source_question_digest = models.CharField(max_length=64)
    contribution_id_snapshot = models.PositiveBigIntegerField()
    contribution_revision_snapshot = models.PositiveIntegerField()
    contribution_submitted_at_snapshot = models.DateTimeField()
    contributor_id_snapshot = models.PositiveBigIntegerField()
    contributor_name_snapshot = models.CharField(max_length=255)
    campus_id_snapshot = models.PositiveBigIntegerField()
    campus_code_snapshot = models.CharField(max_length=30)
    campus_name_snapshot = models.CharField(max_length=120)
    assignment_context_snapshot = models.JSONField(default=list)
    question_text_snapshot = models.TextField(max_length=5000)
    choices_snapshot = models.JSONField(default=list)
    difficulty_snapshot = models.CharField(
        max_length=10,
        choices=Question.Difficulty.choices,
    )
    correct_answer_snapshot = models.CharField(
        max_length=1,
        choices=[("A", "A"), ("B", "B"), ("C", "C"), ("D", "D")],
    )
    normalized_fingerprint = models.CharField(max_length=64)
    eligible_for_generation = models.BooleanField(default=True)
    exclusion_code = models.CharField(max_length=40, blank=True)

    class Meta:
        db_table = "departmental_exam_generation_source_questions"
        constraints = [
            models.UniqueConstraint(
                fields=["audit_snapshot", "source_question"],
                name="uq_de_source_audit_question",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    source_question_revision__gte=1,
                    contribution_revision_snapshot__gte=1,
                    difficulty_snapshot__in=["EASY", "MODERATE", "DIFFICULT"],
                    correct_answer_snapshot__in=["A", "B", "C", "D"],
                ),
                name="ck_de_source_audit_question_values",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(eligible_for_generation=True, exclusion_code="")
                    | (
                        models.Q(eligible_for_generation=False)
                        & ~models.Q(exclusion_code="")
                    )
                ),
                name="ck_de_source_audit_eligibility",
            ),
        ]
        indexes = [
            models.Index(
                fields=["audit_snapshot", "normalized_fingerprint"],
                name="idx_de_source_audit_fp",
            )
        ]

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Generation source question snapshots are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Generation source question snapshots are immutable.")


class AutomaticGenerationAuditRun(TimeStampedModel):
    class Status(models.TextChoices):
        PASS = "PASS", "Pass"
        WARNING = "WARNING", "Warning"
        FAIL = "FAIL", "Fail"

    generation_revision = models.ForeignKey(
        ExamGenerationRevision,
        on_delete=models.PROTECT,
        related_name="automatic_audit_runs",
    )
    status = models.CharField(max_length=10, choices=Status.choices)
    check_version = models.CharField(max_length=32)
    run_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="departmental_exam_automatic_audit_runs",
    )
    run_at = models.DateTimeField(default=timezone.now)
    findings_snapshot = models.JSONField(default=list)
    summary_counts_snapshot = models.JSONField(default=dict)

    class Meta:
        db_table = "departmental_exam_automatic_audit_runs"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=["PASS", "WARNING", "FAIL"]),
                name="ck_de_auto_audit_status",
            )
        ]
        indexes = [
            models.Index(
                fields=["generation_revision", "-run_at"],
                name="idx_de_auto_audit_revision",
            )
        ]

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Automatic generation audit runs are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Automatic generation audit runs are immutable.")

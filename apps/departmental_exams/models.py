from django.core.exceptions import ValidationError
from django.db import models, router
from django.utils import timezone

from apps.core.models import ActivatableModel, TimeStampedModel


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
    # Kept solely to preserve the 0002 migration history.  New runtime code
    # must use the independent CAO defaults below.
    legacy_item_count_mode = models.CharField(max_length=12, choices=ItemCountMode.choices, null=True, blank=True)
    default_final_item_count = models.PositiveSmallIntegerField(null=True, blank=True, default=None)
    default_questions_required_per_faculty = models.PositiveSmallIntegerField(null=True, blank=True, default=None)
    default_contribution_deadline = models.DateTimeField(null=True, blank=True, default=None)
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

    cycle_course = models.OneToOneField(CycleCourse, on_delete=models.PROTECT, related_name="configuration")
    final_item_count = models.PositiveSmallIntegerField(null=True, blank=True, default=None)
    questions_required_per_faculty = models.PositiveSmallIntegerField(null=True, blank=True, default=None)
    questions_required_per_faculty_source = models.CharField(max_length=8, choices=ValueSource.choices, null=True, blank=True)
    final_item_count_source = models.CharField(max_length=8, choices=ValueSource.choices, null=True, blank=True)
    cycle_defaults_revision_snapshot = models.PositiveIntegerField(null=True, blank=True)
    general_instructions = models.TextField(blank=True)
    contribution_deadline = models.DateTimeField(null=True, blank=True)
    contribution_deadline_source = models.CharField(max_length=8, choices=ValueSource.choices, null=True, blank=True)
    easy_percent = models.PositiveSmallIntegerField(default=30)
    moderate_percent = models.PositiveSmallIntegerField(default=50)
    difficult_percent = models.PositiveSmallIntegerField(default=20)
    workflow_status = models.CharField(max_length=10, choices=WorkflowStatus.choices, default=WorkflowStatus.DRAFT)
    opened_at = models.DateTimeField(null=True, blank=True)
    opened_by = models.ForeignKey("accounts.User", on_delete=models.PROTECT, null=True, blank=True, related_name="opened_exam_configurations")
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey("accounts.User", on_delete=models.PROTECT, null=True, blank=True, related_name="closed_exam_configurations")
    coverage = models.TextField(blank=True)
    additional_instructions = models.TextField(blank=True)
    contributor_instructions_snapshot = models.TextField(blank=True)
    legacy_item_count_mode_snapshot = models.CharField(max_length=12, choices=ExaminationCycle.ItemCountMode.choices, null=True, blank=True)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "departmental_exam_configurations"
        constraints = [
            models.CheckConstraint(condition=(models.Q(questions_required_per_faculty__isnull=True, questions_required_per_faculty_source__isnull=True) | models.Q(questions_required_per_faculty__isnull=False, questions_required_per_faculty_source__isnull=False, questions_required_per_faculty__gte=50, questions_required_per_faculty__lte=75, questions_required_per_faculty_source__in=["DEFAULT", "OVERRIDE"])), name="ck_de_cfg_q_value_source"),
            models.CheckConstraint(condition=(models.Q(final_item_count__isnull=True, final_item_count_source__isnull=True) | models.Q(final_item_count__isnull=False, final_item_count_source__isnull=False, final_item_count__gte=50, final_item_count__lte=75, final_item_count_source__in=["DEFAULT", "OVERRIDE"])), name="ck_de_cfg_final_value_source"),
            models.CheckConstraint(condition=(models.Q(contribution_deadline__isnull=True, contribution_deadline_source__isnull=True) | models.Q(contribution_deadline__isnull=False, contribution_deadline_source__isnull=False, contribution_deadline_source__in=["DEFAULT", "OVERRIDE"])), name="ck_de_cfg_deadline_source"),
        ]
        indexes = [models.Index(fields=["workflow_status", "contribution_deadline"], name="idx_de_cfg_status_deadline")]

    @property
    def maximum_score(self):
        return self.final_item_count

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

    cycle_course = models.ForeignKey(CycleCourse, on_delete=models.PROTECT, related_name="faculty_contributions")
    faculty_user = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="exam_contributions")
    source_assignment = models.ForeignKey("academics.FacultyAssignment", on_delete=models.PROTECT, related_name="exam_contributions")
    source_campus = models.ForeignKey("tenants.Campus", on_delete=models.PROTECT, related_name="exam_contributions")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    submitted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "departmental_exam_faculty_contributions"
        constraints = [models.UniqueConstraint(fields=["cycle_course", "faculty_user"], name="uq_de_contribution_faculty_course")]
        indexes = [models.Index(fields=["faculty_user", "status"], name="idx_de_contrib_user_status")]

    def clean(self):
        if not (self.cycle_course_id and self.source_assignment_id and self.faculty_user_id and self.source_campus_id):
            return
        offering = self.source_assignment.offering
        cycle = self.cycle_course.cycle
        if self.source_assignment.faculty_user_id != self.faculty_user_id or not self.source_assignment.is_active or not self.source_assignment.is_accepted:
            raise ValidationError("Contribution requires an active accepted assignment for this faculty member.")
        if offering.tenant_id != cycle.tenant_id or offering.academic_year_id != cycle.academic_year_id or offering.term_id != cycle.term_id or offering.course_id != self.cycle_course.course_id or offering.campus_id != self.source_campus_id:
            raise ValidationError("Contribution source assignment does not match the grouped course scope.")


class Question(TimeStampedModel):
    class Difficulty(models.TextChoices):
        EASY = "EASY", "Easy"
        MODERATE = "MODERATE", "Moderate"
        DIFFICULT = "DIFFICULT", "Difficult"

    contribution = models.ForeignKey(FacultyContribution, on_delete=models.PROTECT, related_name="questions")
    question_text = models.TextField()
    choice_a = models.CharField(max_length=1000)
    choice_b = models.CharField(max_length=1000)
    choice_c = models.CharField(max_length=1000)
    choice_d = models.CharField(max_length=1000)
    correct_answer = models.CharField(max_length=1, choices=[("A", "A"), ("B", "B"), ("C", "C"), ("D", "D")])
    difficulty = models.CharField(max_length=10, choices=Difficulty.choices)

    class Meta:
        db_table = "departmental_exam_questions"
        indexes = [models.Index(fields=["contribution", "difficulty"], name="idx_de_q_contrib_difficulty")]

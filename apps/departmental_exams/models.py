from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import ActivatableModel, TimeStampedModel


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
    item_count_mode = models.CharField(max_length=12, choices=ItemCountMode.choices, null=True, blank=True)
    fixed_final_item_count = models.PositiveSmallIntegerField(null=True, blank=True, default=None)
    contributor_instructions = models.TextField(blank=True)
    created_by = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="created_examination_cycles")

    class Meta:
        db_table = "departmental_exam_cycles"
        constraints = [
            models.UniqueConstraint(fields=["tenant", "academic_year", "term", "exam_period"], name="uq_de_cycle_scope_period"),
            models.CheckConstraint(
                condition=(
                    models.Q(item_count_mode__isnull=True, fixed_final_item_count__isnull=True)
                    | models.Q(item_count_mode="FIXED_ALL", fixed_final_item_count__gte=1, fixed_final_item_count__lte=200)
                    | models.Q(item_count_mode="PER_COURSE", fixed_final_item_count__isnull=True)
                ),
                name="ck_de_cycle_item_count_mode",
            ),
        ]
        indexes = [models.Index(fields=["tenant", "term", "status"], name="idx_de_cycle_scope_status")]

    def clean(self):
        if self.academic_year_id and self.term_id and self.term.academic_year_id != self.academic_year_id:
            raise ValidationError("Term must belong to the selected academic year.")
        if self.tenant_id and self.academic_year_id and self.academic_year.tenant_id != self.tenant_id:
            raise ValidationError("Academic year must belong to the selected tenant.")
        if self.tenant_id and self.term_id and self.term.tenant_id != self.tenant_id:
            raise ValidationError("Term must belong to the selected tenant.")
        if self.item_count_mode == self.ItemCountMode.FIXED_ALL and not (self.fixed_final_item_count and 1 <= self.fixed_final_item_count <= 200):
            raise ValidationError({"fixed_final_item_count": "Fixed final item count must be from 1 to 200."})
        if self.item_count_mode in (None, self.ItemCountMode.PER_COURSE) and self.fixed_final_item_count is not None:
            raise ValidationError({"fixed_final_item_count": "A fixed final item count is allowed only in Fixed mode."})


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
    class WorkflowStatus(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        OPEN = "OPEN", "Open for Faculty Contribution"
        CLOSED = "CLOSED", "Closed"

    cycle_course = models.OneToOneField(CycleCourse, on_delete=models.PROTECT, related_name="configuration")
    final_item_count = models.PositiveSmallIntegerField(null=True, blank=True, default=None)
    questions_required_per_faculty = models.PositiveSmallIntegerField(null=True, blank=True, default=None)
    general_instructions = models.TextField(blank=True)
    contribution_deadline = models.DateTimeField(null=True, blank=True)
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
    item_count_mode_snapshot = models.CharField(max_length=12, choices=ExaminationCycle.ItemCountMode.choices, null=True, blank=True)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "departmental_exam_configurations"
        indexes = [models.Index(fields=["workflow_status", "contribution_deadline"], name="idx_de_cfg_status_deadline")]

    @property
    def maximum_score(self):
        return self.final_item_count

    def clean(self):
        for field in ("final_item_count", "questions_required_per_faculty"):
            value = getattr(self, field)
            if value is not None and not 1 <= value <= 200:
                raise ValidationError({field: "Value must be from 1 to 200."})
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

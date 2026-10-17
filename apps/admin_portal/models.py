"""Private, append-only Grade Distribution release records; never admin-editable."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class ImmutableQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Grade distribution releases are immutable.")

    def delete(self):
        raise ValidationError("Grade distribution releases are immutable.")

    def bulk_create(self, objs, **kwargs):
        if kwargs.get("update_conflicts"):
            raise ValidationError("Grade distribution releases are immutable.")
        return super().bulk_create(objs, **kwargs)

    def bulk_update(self, *args, **kwargs):
        raise ValidationError("Grade distribution releases are immutable.")


class ImmutableReleaseRecord(models.Model):
    objects = ImmutableQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Grade distribution releases are immutable.")
        kwargs["force_insert"] = True
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Grade distribution releases are immutable.")


class GradeDistributionRelease(ImmutableReleaseRecord):
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT)
    campus = models.ForeignKey("tenants.Campus", on_delete=models.PROTECT)
    offering = models.ForeignKey("academics.CourseOffering", on_delete=models.PROTECT)
    physical_period = models.ForeignKey("grading.GradingTemplatePeriod", on_delete=models.PROTECT)
    policy = models.CharField(max_length=50)
    version = models.PositiveIntegerField()
    # v1 admits all statuses only. Requests for subsets never disclose numbers.
    saved_status = models.CharField(max_length=12, default="all")
    fingerprint = models.CharField(max_length=64)
    commitment_key = models.CharField(max_length=64)
    # HMAC membership commitments, no names, IDs or individual grades. Private.
    cohort = models.JSONField()
    disclosed = models.BooleanField()
    qualifying_count = models.PositiveIntegerField(null=True)
    denominator = models.PositiveIntegerField(null=True)
    percentage = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    review_note = models.CharField(max_length=500)
    legacy_history_cleared = models.BooleanField()

    class Meta:
        base_manager_name = "objects"
        constraints = [
            models.UniqueConstraint(fields=["tenant", "offering", "physical_period", "version"], name="gdm_release_version_unique"),
            models.CheckConstraint(condition=Q(saved_status="all"), name="gdm_release_all_status"),
            models.CheckConstraint(condition=Q(legacy_history_cleared=True), name="gdm_release_legacy_reviewed"),
            models.CheckConstraint(condition=Q(version__gte=1), name="gdm_release_version_positive"),
            models.CheckConstraint(condition=(Q(disclosed=False) |
                ((Q(qualifying_count=0) | Q(qualifying_count__gte=5)) &
                 (Q(qualifying_count=models.F("denominator")) | Q(qualifying_count__lte=models.F("denominator")-5)))),
                name="gdm_release_safe_complements"),
            models.CheckConstraint(condition=(Q(disclosed=False, qualifying_count__isnull=True, denominator__isnull=True, percentage__isnull=True) |
                Q(disclosed=True, qualifying_count__isnull=False, denominator__isnull=False, percentage__isnull=False, denominator__gte=5, percentage__gte=0, percentage__lte=100,
                  qualifying_count__lte=models.F("denominator"))), name="gdm_release_values_valid"),
        ]
        indexes = [models.Index(fields=["tenant", "offering", "physical_period"], name="gdm_release_source_idx")]


class GradeDistributionPublication(ImmutableReleaseRecord):
    release = models.OneToOneField(GradeDistributionRelease, on_delete=models.PROTECT, related_name="publication")
    # One publication ever per physical source, including withholding decisions.
    source_key = models.CharField(max_length=100, unique=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    approved_at = models.DateTimeField(auto_now_add=True)
    review_note = models.CharField(max_length=500)
    prior_history_digest = models.CharField(max_length=64)

    class Meta:
        base_manager_name = "objects"

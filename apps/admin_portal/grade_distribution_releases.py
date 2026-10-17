"""Controlled v1 publication. HTTP readers never prepare or publish a release.

The first policy permits a single binary interval per student cohort, across all
statuses. Any overlap with a prior numeric release is rejected, even across
classes/periods. No subsequent numeric release is allowed for a physical source.
This intentionally trades availability for a small, auditable disclosure surface.
"""
import hashlib
import json
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils.crypto import salted_hmac

from apps.admin_portal.models import GradeDistributionPublication, GradeDistributionRelease
from apps.admin_portal.grade_distribution_metrics import percentage
from apps.tenants.models import Tenant

POLICY = "saved-period-97-100-v1"
BOUNDS = (Decimal("97"), Decimal("100"))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def source_candidate(offering, period, records, student_ids, historical, admissible):
    """Private source commitments derived from the same batched authorized read."""
    cohort = sorted(salted_hmac("gdm-cohort-v1", f"{offering.tenant_id}:{sid}").hexdigest() for sid in student_ids)
    fingerprint = salted_hmac("gdm-source-v1", digest({
        "policy": POLICY, "tenant": offering.tenant_id, "campus": offering.campus_id,
        "offering": offering.id, "period": period.id, "template": period.template_id,
        "historical": historical, "cohort": cohort,
        "records": sorted(records, key=lambda r: r["student_id"]),
    }), algorithm="sha256").hexdigest()
    values = [r["period_grade"] for r in records if r["period_grade"] is not None]
    count = sum(BOUNDS[0] <= value <= BOUNDS[1] for value in values)
    total = len(values)
    # No suppression diagnostics or unsafe totals are persisted or serialized.
    safe = admissible and total >= 5 and all(n == 0 or n >= 5 for n in (count, total-count))
    return dict(tenant_id=offering.tenant_id, campus_id=offering.campus_id,
        offering_id=offering.id, physical_period_id=period.id, policy=POLICY,
        saved_status="all", commitment_key=salted_hmac("gdm-key-epoch", "v1").hexdigest(), fingerprint=fingerprint, cohort=cohort, disclosed=safe,
        qualifying_count=count if safe else None, denominator=total if safe else None,
        percentage=percentage(count, total) if safe else None)


def published_map(tenant_id, offering_ids, period_ids, *, publication_limit=None):
    releases = GradeDistributionRelease.objects.filter(
        tenant_id=tenant_id, offering_id__in=offering_ids, physical_period_id__in=period_ids,
        publication__isnull=False, policy=POLICY)
    if publication_limit is not None:
        releases = releases.filter(publication__pk__lte=publication_limit)
    return {(r.offering_id, r.physical_period_id): r for r in releases.select_related("publication")}


def released_values(release, candidate, bounds, status):
    if (release is None or bounds != BOUNDS or status != "all" or not candidate["disclosed"]
            or not release.disclosed or release.fingerprint != candidate["fingerprint"]
            or release.cohort != candidate["cohort"] or release.campus_id != candidate["campus_id"]
            or any(getattr(release, key) != candidate[key] for key in ("qualifying_count", "denominator", "percentage"))):
        return None
    # Only immutable public aggregates cross this boundary. No cohort/fingerprint.
    return dict(range_count=release.qualifying_count, range_denominator=release.denominator,
        range_percentage=release.percentage)


def _controller(request):
    if not request.user.is_active or not request.user.is_superuser:
        raise PermissionDenied("Release operations require an active superuser and scoped monitor authorization.")


def _candidate(request, offering_id, period_id):
    from apps.admin_portal.grade_distribution import GradeDistributionMonitorService as Monitor
    selection, assignments, offerings, saved, groups, periods, _, _ = Monitor.prepare(request, target_offering_id=offering_id)
    if selection.selected["status"] != "all" or selection.bounds != BOUNDS:
        raise ValidationError("Release preparation requires all statuses and Grade Range 97–100.")
    candidates = list(groups.filter(offering_id=offering_id, template_period_id=period_id))
    if not candidates:
        raise ValidationError("Source is unavailable in the controller's authorized scope.")
    sources = {}
    Monitor.rows(selection, assignments, offerings, saved, candidates, periods, release_sink=sources)
    return sources[(offering_id, period_id)]


def _history(tenant_id):
    return list(GradeDistributionPublication.objects.filter(release__tenant_id=tenant_id).select_related("release").order_by("pk"))


def _check_history(candidate, history):
    if any(p.release.commitment_key != candidate["commitment_key"] for p in history):
        raise ValidationError("Membership commitment key changed; prior cohorts cannot be compared safely.")
    if any((p.release.offering_id, p.release.physical_period_id) == (candidate["offering_id"], candidate["physical_period_id"]) for p in history):
        raise ValidationError("This physical source already has an immutable publication; v1 does not republish sources.")
    if candidate["disclosed"] and any(p.release.disclosed and set(candidate["cohort"]).intersection(p.release.cohort) for p in history):
        raise ValidationError("Prior numeric release overlaps this cohort; publication is blocked by policy.")


@transaction.atomic
def prepare_release(request, offering_id, period_id, note, *, legacy_history_cleared=False):
    _controller(request)
    if legacy_history_cleared is not True:
        raise ValidationError("Review prior exports/disclosures first. Unverifiable legacy history cannot be cleared.")
    if not note.strip() or len(note) > 500:
        raise ValidationError("Provide a review note of 1–500 characters.")
    Tenant.objects.select_for_update().get(pk=request.scope["tenant_id"])
    candidate = _candidate(request, offering_id, period_id)
    _check_history(candidate, _history(candidate["tenant_id"]))
    latest = GradeDistributionRelease.objects.filter(tenant_id=candidate["tenant_id"], offering_id=offering_id,
        physical_period_id=period_id).order_by("-version").first()
    return GradeDistributionRelease.objects.create(**candidate, version=1 if latest is None else latest.version+1,
        created_by=request.user, review_note=note.strip(), legacy_history_cleared=True)


@transaction.atomic
def publish_release(request, release_id, note):
    _controller(request)
    if not note.strip() or len(note) > 500:
        raise ValidationError("Provide a review note of 1–500 characters.")
    Tenant.objects.select_for_update().get(pk=request.scope["tenant_id"])
    release = GradeDistributionRelease.objects.get(pk=release_id, tenant_id=request.scope["tenant_id"])
    if release.created_by_id == request.user.pk:
        raise ValidationError("A different active superuser must approve the release.")
    candidate = _candidate(request, release.offering_id, release.physical_period_id)
    if any(getattr(release, key) != value for key, value in candidate.items()):
        raise ValidationError("Source or confidentiality decision changed after preparation; prepare a new version.")
    history = _history(release.tenant_id)
    _check_history(candidate, history)
    return GradeDistributionPublication.objects.create(release=release,
        source_key=f"{release.tenant_id}:{release.offering_id}:{release.physical_period_id}",
        approved_by=request.user, review_note=note.strip(),
        prior_history_digest=digest([(p.pk, p.release_id, p.release.fingerprint) for p in history]))

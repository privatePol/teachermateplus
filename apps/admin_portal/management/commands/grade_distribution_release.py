"""Explicit operator-only preparation/publication; never called from HTTP."""
from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.test import RequestFactory

from apps.academics.models import CourseOffering
from apps.admin_portal.grade_distribution_releases import prepare_release, publish_release
from apps.admin_portal.grade_distribution_selection import logical_period
from apps.admin_portal.models import GradeDistributionRelease
from apps.grading.models import GradingTemplatePeriod


class Command(BaseCommand):
    help = "Prepare OR publish one immutable 97–100 period release. Requires separately authorized operator execution."

    def add_arguments(self, parser):
        parser.add_argument("operation", choices=["prepare", "publish"])
        parser.add_argument("--actor", required=True, type=int)
        parser.add_argument("--tenant", required=True, type=int)
        parser.add_argument("--offering", type=int)
        parser.add_argument("--period", type=int)
        parser.add_argument("--release", type=int)
        parser.add_argument("--note", required=True)
        parser.add_argument("--legacy-history-cleared", action="store_true",
            help="Attest reviewed evidence excludes unsafe comparison with prior reports/exports. Never use for unknown history.")

    def handle(self, *args, **options):
        try:
            actor = get_user_model().objects.get(pk=options["actor"])
            if options["operation"] == "publish":
                if not options["release"]:
                    raise CommandError("--release is required for publish.")
                release = GradeDistributionRelease.objects.get(pk=options["release"], tenant_id=options["tenant"])
                offering_id, period_id = release.offering_id, release.physical_period_id
            else:
                if not options["offering"] or not options["period"]:
                    raise CommandError("--offering and --period are required for prepare.")
                offering_id, period_id = options["offering"], options["period"]
            offering = CourseOffering.objects.get(pk=offering_id, tenant_id=options["tenant"])
            period = GradingTemplatePeriod.objects.get(pk=period_id, template__tenant_id=options["tenant"])
            params = dict(apply="1", campus_id=offering.campus_id, academic_year_id=offering.academic_year_id,
                term_id=offering.term_id, period=logical_period(period) or "", status="all", grade_min="97", grade_max="100")
            request = RequestFactory().post("/controlled-grade-distribution-release/", params)
            request.GET = request.POST.copy()
            request.user = actor
            request.scope = dict(tenant_id=options["tenant"], campus_id=offering.campus_id)
            if options["operation"] == "prepare":
                result = prepare_release(request, offering_id, period_id, options["note"],
                    legacy_history_cleared=options["legacy_history_cleared"])
                self.stdout.write(f"Prepared release {result.pk}, version {result.version}. Not published.")
            else:
                result = publish_release(request, release.pk, options["note"])
                self.stdout.write(f"Published release {result.release_id}. Immutable; changed sources fail closed.")
        except (ObjectDoesNotExist, PermissionDenied, ValidationError) as exc:
            raise CommandError(str(exc)) from exc

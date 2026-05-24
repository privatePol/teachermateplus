from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.grading.services import GradingGovernanceService


class Command(BaseCommand):
    help = "Automatically lock reopened gradebooks that were not submitted before their deadline or 24-hour reopen window."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show which period locks would be auto-locked without writing changes.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Optional maximum number of due locks to process in one run.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        limit = options.get("limit")
        result = GradingGovernanceService.auto_lock_due_periods(
            at=timezone.now(),
            limit=limit,
            dry_run=dry_run,
        )

        mode_label = "DRY RUN" if dry_run else "LIVE RUN"
        self.stdout.write(self.style.NOTICE(f"[{mode_label}] Checked at {result['checked_at']:%Y-%m-%d %H:%M:%S %Z}"))
        self.stdout.write(self.style.NOTICE(f"Due locks found: {result['count']}"))

        for row in result["rows"]:
            offering_label = row["course_offering_id"] if row["course_offering_id"] else "-"
            self.stdout.write(
                " - Lock #{id} | {tenant}/{campus} | {academic_year} {term} | {period} | {scope} | offering {offering} | lock time {deadline}".format(
                    id=row["id"],
                    tenant=row["tenant_code"] or "-",
                    campus=row["campus_code"] or "-",
                    academic_year=row["academic_year_code"] or "-",
                    term=row["term_code"] or "-",
                    period=row["period_code"],
                    scope=row["scope_type"],
                    offering=offering_label,
                    deadline=row["deadline_at"],
                )
            )

        if result["count"] == 0:
            self.stdout.write(self.style.SUCCESS("No expired reopened gradebooks found."))
            return

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run complete. No database changes were made."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Auto-locked {result['count']} expired reopened gradebook(s)."))

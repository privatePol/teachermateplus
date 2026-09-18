from django.core.management.base import BaseCommand

from apps.notifications.contribution_reminders import ContributionDeadlineReminderService


class Command(BaseCommand):
    help = "Send consolidated faculty contribution reminders due tomorrow in Asia/Manila."

    def add_arguments(self, parser):
        parser.add_argument("--tenant-id", type=int)
        parser.add_argument("--dry-run", action="store_true", help="Read eligibility only; create no records or emails.")

    def handle(self, *args, **options):
        result = ContributionDeadlineReminderService.run(
            tenant_id=options["tenant_id"], dry_run=options["dry_run"]
        )
        self.stdout.write(
            "Contribution reminders: "
            + " ".join(f"{name}={value}" for name, value in result.items())
        )

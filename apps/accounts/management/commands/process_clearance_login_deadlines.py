from django.core.management.base import BaseCommand, CommandError

from apps.accounts.login_control import LoginAccessService


class Command(BaseCommand):
    help = "Process first-successful-clearance PDF login deadlines (feature defaults OFF)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        if options["limit"] < 1:
            raise CommandError("--limit must be positive.")
        rows = LoginAccessService.process_due(limit=options["limit"], dry_run=options["dry_run"])
        self.stdout.write(f"{'Would process' if options['dry_run'] else 'Processed'} {len(rows)} clearance login deadline(s).")
        for row in rows:
            self.stdout.write(f"event={row['event_id']} user={row['user_id']} state={row['state']}")

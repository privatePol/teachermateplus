from django.core.management.base import BaseCommand, CommandError

from apps.departmental_exams.csv_import import QuestionImportCleanupService


class Command(BaseCommand):
    help = "Purge expired confidential question-import previews in bounded transactions."

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=200)

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        if not 1 <= batch_size <= 1000:
            raise CommandError("--batch-size must be from 1 to 1000.")
        result = QuestionImportCleanupService.purge(batch_size=batch_size)
        self.stdout.write(
            self.style.SUCCESS(
                "Expired batches: {expired_batches}; confidential rows purged: "
                "{rows_purged}; old shells purged: {shells_purged}.".format(**result)
            )
        )

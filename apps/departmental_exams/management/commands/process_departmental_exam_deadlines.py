from django.core.management.base import BaseCommand, CommandError

from apps.departmental_exams.automatic_workflow import AutomaticExamDeadlineService


class Command(BaseCommand):
    help = "Close due automatic departmental-exam contributions and generate ready Set A/B revisions."

    def handle(self, *args, **options):
        results = AutomaticExamDeadlineService.process_due()
        counts = {}
        for result in results:
            counts[result.status] = counts.get(result.status, 0) + 1
            revision = (
                f" R{result.generation_revision}"
                if result.generation_revision is not None
                else ""
            )
            self.stdout.write(
                f"course={result.cycle_course_id} status={result.status} "
                f"code={result.code}{revision} message={result.message}"
            )
        summary = " ".join(
            f"{key.lower()}={value}" for key, value in sorted(counts.items())
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Departmental exam deadline processing complete: total={len(results)}"
                + (f" {summary}" if summary else "")
            )
        )
        if counts.get("ERROR"):
            raise CommandError(
                f"Automatic departmental-exam processing completed with {counts['ERROR']} course error(s)."
            )

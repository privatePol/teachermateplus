from django.core.management.base import BaseCommand, CommandError

from apps.departmental_exams.automatic_processing_isolation import (
    AUTOMATIC_PROCESSING_TIMEOUT_CODE,
    ProcessIsolatedAutomaticCourseRunner,
)
from apps.departmental_exams.automatic_workflow import AutomaticExamDeadlineService


class Command(BaseCommand):
    help = "Close due automatic departmental-exam contributions and generate ready Set A/B revisions."

    def handle(self, *args, **options):
        course_runner = ProcessIsolatedAutomaticCourseRunner()
        self.stdout.write(
            "Automatic course isolation enabled: "
            f"timeout_seconds={course_runner.timeout_seconds:g}"
        )
        results = AutomaticExamDeadlineService.process_due(
            course_processor=course_runner
        )
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
        timeout_count = sum(
            result.code == AUTOMATIC_PROCESSING_TIMEOUT_CODE for result in results
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Departmental exam deadline processing complete: total={len(results)}"
                + (f" {summary}" if summary else "")
                + f" timeouts={timeout_count}"
            )
        )
        if counts.get("ERROR"):
            raise CommandError(
                "Automatic departmental-exam processing completed with "
                f"{counts['ERROR']} course error(s), including "
                f"{timeout_count} timeout(s)."
            )

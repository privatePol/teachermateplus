from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from apps.accounts.models import User
from apps.academics.administrative_acceptance import (
    AdministrativeFacultyAssignmentAcceptanceService,
)
from apps.academics.models import AcademicYear, Term
from apps.tenants.models import Campus, Department, Tenant


class Command(BaseCommand):
    help = (
        "Preview or execute truthful administrative acceptance of eligible "
        "official faculty assignments. Dry-run is the default."
    )

    def add_arguments(self, parser):
        parser.add_argument("--tenant", required=True, help="Tenant ID or exact code.")
        parser.add_argument("--academic-year", required=True, help="Academic year ID or exact code.")
        parser.add_argument("--term", required=True, help="Term ID or exact code.")
        parser.add_argument("--actor", required=True, help="Administrative user ID, username, or email.")
        parser.add_argument("--reason", required=True, help="Required durable administrative reason.")
        parser.add_argument("--campus", help="Optional campus ID or exact code.")
        parser.add_argument("--exam-department", help="Optional exam-department ID or exact code.")
        parser.add_argument(
            "--execute",
            action="store_true",
            help="Apply the transaction. Without this flag, the command is read-only.",
        )
        parser.add_argument(
            "--expected-candidate-hash",
            help="Required with --execute; must equal the approved dry-run SHA-256 hash.",
        )
        parser.add_argument(
            "--include-readiness-anomalies",
            action="store_true",
            help="Explicitly permit otherwise-eligible official assignments with reported readiness anomalies.",
        )

    def handle(self, *args, **options):
        try:
            tenant = self._resolve(
                Tenant.objects.all(),
                options["tenant"],
                label="tenant",
                text_field="code",
            )
            academic_year = self._resolve(
                AcademicYear.objects.filter(tenant=tenant),
                options["academic_year"],
                label="academic year",
                text_field="code",
            )
            term = self._resolve(
                Term.objects.filter(tenant=tenant, academic_year=academic_year),
                options["term"],
                label="term",
                text_field="code",
            )
            actor = self._resolve_actor(options["actor"])
            campus = None
            if options.get("campus"):
                campus = self._resolve(
                    Campus.objects.filter(tenant=tenant),
                    options["campus"],
                    label="campus",
                    text_field="code",
                )
            exam_department = None
            if options.get("exam_department"):
                departments = Department.objects.filter(tenant=tenant)
                if campus:
                    departments = departments.filter(campus=campus)
                exam_department = self._resolve(
                    departments,
                    options["exam_department"],
                    label="exam department",
                    text_field="code",
                )

            service_options = {
                "tenant": tenant,
                "academic_year": academic_year,
                "term": term,
                "actor": actor,
                "reason": options["reason"],
                "campus": campus,
                "exam_department": exam_department,
            }
            if options["execute"]:
                if not options.get("expected_candidate_hash"):
                    raise CommandError("--execute requires --expected-candidate-hash.")
                report = AdministrativeFacultyAssignmentAcceptanceService.execute(
                    **service_options,
                    expected_candidate_hash=options["expected_candidate_hash"],
                    include_readiness_anomalies=options["include_readiness_anomalies"],
                )
            else:
                report = AdministrativeFacultyAssignmentAcceptanceService.preview(**service_options)
        except CommandError:
            raise
        except (PermissionDenied, ValidationError) as exc:
            message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
            raise CommandError(message) from exc

        self._write_report(report)
        if options["execute"]:
            self.stdout.write(self.style.SUCCESS(f"CHANGED: {report.changed_count}"))
            self.stdout.write(self.style.SUCCESS(f"BATCH ID: {report.batch_id}"))
        else:
            self.stdout.write(self.style.WARNING("NO DATABASE CHANGES MADE"))

    @staticmethod
    def _resolve(queryset, identifier, *, label, text_field):
        raw = str(identifier).strip()
        query = Q(**{f"{text_field}__iexact": raw})
        if raw.isdigit():
            query |= Q(pk=int(raw))
        matches = list(queryset.filter(query).distinct()[:2])
        if not matches:
            raise CommandError(f"Unknown {label}: {raw}")
        if len(matches) != 1:
            raise CommandError(f"Ambiguous {label}: {raw}")
        return matches[0]

    @staticmethod
    def _resolve_actor(identifier):
        raw = str(identifier).strip()
        query = Q(username__iexact=raw) | Q(email__iexact=raw)
        if raw.isdigit():
            query |= Q(pk=int(raw))
        matches = list(User.objects.filter(query).distinct()[:2])
        if not matches:
            raise CommandError(f"Unknown actor: {raw}")
        if len(matches) != 1:
            raise CommandError(f"Ambiguous actor: {raw}")
        return matches[0]

    def _write_report(self, report):
        self.stdout.write(f"Resolved tenant: {report.tenant.code} (ID {report.tenant.id})")
        self.stdout.write(
            f"Resolved academic year: {report.academic_year.code} (ID {report.academic_year.id})"
        )
        self.stdout.write(f"Resolved term: {report.term.code} (ID {report.term.id})")
        self.stdout.write(f"Resolved actor: ID {report.actor.id}")
        self.stdout.write(
            f"Campus scope: {report.campus.code if report.campus else 'ALL AUTHORIZED CAMPUSES'}"
        )
        self.stdout.write(
            "Exam-department scope: "
            f"{report.exam_department.code if report.exam_department else 'ALL AUTHORIZED EXAM DEPARTMENTS'}"
        )
        self.stdout.write(f"Active/open offerings examined: {report.offerings_examined}")
        self.stdout.write(f"Assignment rows examined: {report.assignments_examined}")
        self.stdout.write(f"Distinct faculty: {report.distinct_faculty}")
        self.stdout.write(
            f"PENDING executable candidates: {report.candidate_count}"
        )
        for status in ("ACCEPTED", "DECLINED", "CLARIFICATION_REQUESTED", "EXPIRED"):
            self.stdout.write(f"{status}: {report.status_counts.get(status, 0)}")
        self.stdout.write(f"Inactive assignments excluded: {report.inactive_assignments}")
        self.stdout.write(
            "Malformed/inconsistent acceptance states: "
            f"{len(report.malformed_acceptance_assignment_ids)}"
        )
        self.stdout.write(
            f"Assignment/offering scope inconsistencies: {len(report.inconsistent_scope_assignment_ids)}"
        )
        self.stdout.write(
            f"Authorization exclusions: {len(report.authorization_excluded_assignment_ids)}"
        )
        self.stdout.write("Readiness anomalies:")
        labels = {
            "inactive_user": "inactive user",
            "unusable_password": "not activated/unusable password",
            "faculty_membership_missing": "FACULTY membership missing",
            "faculty_membership_inactive": "FACULTY membership inactive",
            "faculty_role_inactive": "FACULTY role inactive",
            "tenant_mismatch": "tenant mismatch",
            "campus_mismatch": "campus mismatch",
            "department_mismatch": "department mismatch",
            "other": "other",
        }
        for key in AdministrativeFacultyAssignmentAcceptanceService.ANOMALY_KEYS:
            self.stdout.write(f"- {labels[key]}: {len(report.readiness_anomalies[key])}")
        self.stdout.write(f"Exact number that WOULD change: {report.would_change}")
        self.stdout.write(f"Candidate count: {report.candidate_count}")
        self.stdout.write(f"Candidate hash: {report.candidate_hash}")

from __future__ import annotations

import csv
import io
import re
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import HttpResponse
from django.utils import timezone

from apps.academics.models import AcademicYear, Course, CourseOffering, FacultyAssignment, Section, Term
from apps.core.services.audit import AuditService
from apps.core.services.settings import SystemSettingService
from apps.accounts.faculty_provisioning import (
    FacultyAccountProvisioningService,
    FacultyInvitationService,
    ScopedUserRoleAssignmentService,
)
from apps.accounts.models import FacultyInvitation
from apps.core.services.permissions import PermissionService
from apps.enrollment.models import Enrollment
from apps.enrollment.services import EnrollmentService
from apps.imports.models import ImportBatch, ImportBatchRow
from apps.rbac.models import UserRole
from apps.students.models import Student
from apps.tenants.models import Campus, Department, Program, Tenant

User = get_user_model()


class ImportTemplateService:
    COMMON_SAFETY_MEASURES = [
        "Uploading a CSV only validates and stages the file. It does not create or change operational records.",
        "TeacherMate+ checks the official headers, required references, tenant/campus scope, field formats, and duplicate rows before confirmation.",
        "Only rows marked VALID are eligible when Confirm Import is clicked. Rows marked ERROR are not imported.",
        "Each valid row is saved in its own protected database transaction. A failed row is rolled back without undoing other successful rows.",
        "Every successful imported row and the batch confirmation are recorded in the audit trail.",
        "A batch that is already confirmed cannot be confirmed again.",
    ]

    IMPORT_SAFETY_RULES = {
        ImportBatch.ImportType.SECTIONS: {
            "duplicate_rule": "Existing sections and repeated section rows are skipped instead of being created again.",
            "change_warning": "This importer creates missing sections only. It does not update an existing section.",
        },
        ImportBatch.ImportType.COURSES: {
            "duplicate_rule": "An existing course code for the same tenant is rejected as a duplicate.",
            "change_warning": "This importer creates courses only. Edit an existing course through the Admin Portal.",
        },
        ImportBatch.ImportType.STUDENTS: {
            "duplicate_rule": (
                "CREATE rejects an existing student number; UPDATE requires an existing student; "
                "UPSERT updates an existing student or creates a missing student."
            ),
            "change_warning": (
                "UPDATE and UPSERT can intentionally change existing student information. "
                "Review row_action and the staged rows carefully before confirmation."
            ),
        },
        ImportBatch.ImportType.COURSE_OFFERINGS: {
            "duplicate_rule": (
                "An existing tenant/campus/department/term/course/section offering is rejected as a duplicate."
            ),
            "change_warning": (
                "This importer creates course offerings only. Re-uploading existing offerings does not update "
                "their room, schedule, status, or other details."
            ),
        },
        ImportBatch.ImportType.FACULTY_ASSIGNMENTS: {
            "duplicate_rule": "An existing assignment for the same offering and faculty user is rejected.",
            "change_warning": (
                "This importer creates faculty assignments only and does not replace an existing assignment. "
                "It may assign an inactive Faculty account, but it never activates or changes that account; "
                "the existing login and portal-access rules continue to apply."
            ),
        },
        ImportBatch.ImportType.FACULTY_USERS: {
            "duplicate_rule": (
                "Duplicate email addresses or usernames in the CSV are rejected on every conflicting row. "
                "Complete matching Faculty accounts are skipped without changes."
            ),
            "change_warning": (
                "This importer creates inactive Faculty login accounts only. It never updates existing users, "
                "accepts role selection, or sends plaintext passwords."
            ),
        },
        ImportBatch.ImportType.ENROLLMENT: {
            "duplicate_rule": "An existing enrollment for the same student and course offering is rejected.",
            "change_warning": (
                "When the tenant uses AUTO_CREATE, confirming the batch may create a missing student from the CSV. "
                "STRICT_EXISTING never creates a missing student."
            ),
        },
    }

    TEMPLATES = {
        ImportBatch.ImportType.SECTIONS: {
            "headers": [
                "tenant_code",
                "campus_code",
                "department_code",
                "program_code",
                "section_code",
                "section_name",
                "year_level",
                "is_active",
            ],
            "sample_row": [
                "DEMO",
                "MAIN",
                "COLLEGE",
                "BSIT",
                "BSIT-1A",
                "BSIT 1A",
                "1",
                "TRUE",
            ],
        },
        ImportBatch.ImportType.COURSES: {
            "headers": [
                "tenant_code",
                "campus_code",
                "department_code",
                "course_code",
                "course_title",
                "units",
                "course_type",
                "default_base_value",
                "is_active",
            ],
            "sample_row": [
                "DEMO",
                "MAIN",
                "COLLEGE",
                "IT101",
                "Introduction to IT",
                "3",
                "CORE",
                "50",
                "TRUE",
            ],
        },
        ImportBatch.ImportType.COURSE_OFFERINGS: {
            "headers": [
                "tenant_code",
                "campus_code",
                "department_code",
                "program_code",
                "academic_year_code",
                "term_code",
                "course_code",
                "section_code",
                "room",
                "schedule_text",
                "status",
            ],
            "sample_row": [
                "DEMO",
                "MAIN",
                "COLLEGE",
                "BSIT",
                "2025-2026",
                "1ST",
                "IT101",
                "BSIT-1A",
                "R101",
                "MWF 8:00-9:00",
                "OPEN",
            ],
        },
        ImportBatch.ImportType.STUDENTS: {
            "headers": [
                "row_action",
                "tenant_code",
                "campus_code",
                "department_code",
                "program_code",
                "student_no",
                "last_name",
                "first_name",
                "middle_name",
                "official_email",
                "official_email_verified",
                "sex",
                "year_level",
                "status",
                "is_active",
            ],
            "sample_row": [
                "UPSERT",
                "DEMO",
                "MAIN",
                "COLLEGE",
                "BSIT",
                "20250001",
                "DELA CRUZ",
                "JUAN",
                "SANTOS",
                "juan.delacruz@example.edu",
                "FALSE",
                "M",
                "1",
                "ACTIVE",
                "TRUE",
            ],
        },
        ImportBatch.ImportType.FACULTY_ASSIGNMENTS: {
            "headers": [
                "tenant_code",
                "campus_code",
                "academic_year_code",
                "term_code",
                "course_code",
                "section_code",
                "faculty_username",
                "is_primary",
            ],
            "sample_row": [
                "DEMO",
                "MAIN",
                "2025-2026",
                "1ST",
                "IT101",
                "BSIT-1A",
                "faculty1",
                "TRUE",
            ],
        },
        ImportBatch.ImportType.FACULTY_USERS: {
            "headers": [
                "tenant_code",
                "campus_code",
                "department_code",
                "first_name",
                "middle_name",
                "last_name",
                "email",
                "username",
            ],
            "sample_row": [
                "DEMO",
                "MAIN",
                "COLLEGE",
                "JUAN",
                "SANTOS",
                "DELA CRUZ",
                "juan.delacruz@ncba.edu.ph",
                "juan.delacruz",
            ],
        },
        ImportBatch.ImportType.ENROLLMENT: {
            "headers": [
                "tenant_code",
                "campus_code",
                "academic_year_code",
                "term_code",
                "student_no",
                "student_last_name",
                "student_first_name",
                "student_middle_name",
                "student_sex",
                "student_year_level",
                "course_code",
                "section_code",
                "enrollment_status",
            ],
            "sample_row": [
                "DEMO",
                "MAIN",
                "2025-2026",
                "1ST",
                "20250001",
                "DELA CRUZ",
                "JUAN",
                "SANTOS",
                "M",
                "1",
                "IT101",
                "BSIT-1A",
                "ENROLLED",
            ],
        },
    }

    REFERENCE_GUIDES = {
        ImportBatch.ImportType.SECTIONS: {
            "summary": "Creates section master records. Use Course Offerings import separately to assign courses, terms, rooms, schedules, and offering status.",
            "relationships": [
                "tenant_code -> campuses -> departments -> programs -> sections",
                "If program_code is blank, the system attempts inference from section_code prefix.",
            ],
            "code_rules": [
                "tenant_code: must exist in Tenants",
                "campus_code: must exist under the tenant",
                "department_code: must exist under the tenant+campus",
                "program_code: recommended; if blank, system will infer where possible",
                "section_code: exact section code used by offerings",
                "section_name: optional display name; defaults to section_code when blank",
                "year_level: optional; inferred from section_code when blank and possible",
                "is_active: TRUE/FALSE",
            ],
        },
        ImportBatch.ImportType.COURSE_OFFERINGS: {
            "summary": "Course offerings require all master references to exist first.",
            "relationships": [
                "tenant_code + campus_code define campus scope; department_code may be blank when course_code has a campus-matching department",
                "academic_year_code + term_code define term scope",
                "course_code and section_code must already exist in master tables",
            ],
            "code_rules": [
                "academic_year_code: use the exact active Code shown in Admin Portal -> Academic Years (for example, 2025-2026)",
                "term_code: use Term Code from Terms (e.g. 1ST, 2ND)",
                "course_code: must match Courses.code",
                "section_code: must match Sections.code",
                "department_code: recommended; optional only when course_code has a campus-matching department",
                "program_code: optional unless section_code is ambiguous across programs",
            ],
        },
        ImportBatch.ImportType.FACULTY_ASSIGNMENTS: {
            "summary": "Links faculty users to already-created offerings.",
            "relationships": [
                "offering reference = tenant + campus + academic year + term + course + section",
                "faculty_username must belong to an active user with FACULTY role",
            ],
            "code_rules": [
                "faculty_username: accepts username or email, exact match recommended",
                "is_primary: TRUE/FALSE",
            ],
        },
        ImportBatch.ImportType.FACULTY_USERS: {
            "summary": "Creates inactive Faculty login accounts and assigns the exact scoped FACULTY role.",
            "relationships": [
                "tenant_code -> campus_code -> department_code defines both the user default scope and Faculty role scope",
                "username is optional and is derived from the email local part when blank",
            ],
            "code_rules": [
                "tenant_code, campus_code, department_code, first_name, last_name, and email are required",
                "email must use an allowed domain for the selected tenant",
                "role, password, permissions, staff, active, and email-control columns are never accepted",
            ],
        },
        ImportBatch.ImportType.ENROLLMENT: {
            "summary": "Enrolls students into existing course offerings.",
            "relationships": [
                "offering reference = tenant + campus + academic year + term + course + section",
                "student handling depends on ENROLLMENT_STUDENT_MODE (STRICT_EXISTING or AUTO_CREATE)",
            ],
            "code_rules": [
                "enrollment_status: ENROLLED/ACTIVE/DRP/DR/W/INC (mapped by system rules)",
                "academic_year_code and term_code must exist first",
                "If ENROLLMENT_STUDENT_MODE=AUTO_CREATE and student is missing, student_last_name and student_first_name are required",
            ],
        },
        ImportBatch.ImportType.COURSES: {
            "summary": "Creates course master records per tenant.",
            "relationships": [
                "tenant is required",
                "campus/department can be blank for shared tenant-wide course definitions",
            ],
            "code_rules": [
                "course_code: unique per tenant",
                "default_base_value: decimal (e.g. 50)",
                "is_active: TRUE/FALSE",
            ],
        },
        ImportBatch.ImportType.STUDENTS: {
            "summary": "Creates or updates student master records before enrollment imports.",
            "relationships": [
                "tenant_code + campus_code + student_no identify the student record",
                "department_code and program_code are used for student master scope; program_code is optional",
                "Use row_action=UPDATE for yearly changes such as year_level, program_code, status, or verified email",
            ],
            "code_rules": [
                "row_action: CREATE, UPDATE, or UPSERT",
                "CREATE requires department_code, student_no, last_name, and first_name",
                "UPDATE requires an existing student; blank optional fields keep their current values",
                "UPSERT updates an existing student or creates a missing one when required identity fields are present",
                "status: ACTIVE, INACTIVE, GRADUATED, DROPPED, or WITHDRAWN; blank keeps existing status on UPDATE and defaults to ACTIVE on CREATE",
                "official_email_verified: TRUE/FALSE; blank keeps existing verification on UPDATE",
            ],
        },
    }

    @classmethod
    def get_headers(cls, import_type: str) -> list[str]:
        if import_type not in cls.TEMPLATES:
            raise ValidationError("Unsupported import type.")
        return list(cls.TEMPLATES[import_type]["headers"])

    @classmethod
    def get_sample_row(cls, import_type: str) -> list[str]:
        if import_type not in cls.TEMPLATES:
            raise ValidationError("Unsupported import type.")
        return list(cls.TEMPLATES[import_type]["sample_row"])

    @classmethod
    def get_template_config(cls, import_type: str) -> dict:
        if import_type not in cls.TEMPLATES:
            raise ValidationError("Unsupported import type.")
        return {
            "headers": cls.get_headers(import_type),
            "sample_row": cls.get_sample_row(import_type),
            "guide": cls.REFERENCE_GUIDES.get(import_type, {}),
            "safety": cls.get_safety_guidance(import_type),
        }

    @classmethod
    def get_safety_guidance(cls, import_type: str) -> dict:
        if import_type not in cls.TEMPLATES:
            raise ValidationError("Unsupported import type.")
        return {
            "common_measures": list(cls.COMMON_SAFETY_MEASURES),
            **cls.IMPORT_SAFETY_RULES.get(import_type, {}),
        }

    @classmethod
    def generate_csv_response(cls, import_type: str, include_sample: bool = True) -> HttpResponse:
        headers = cls.get_headers(import_type)
        response = HttpResponse(content_type="text/csv")
        filename = f"TeacherMate+_{import_type}_template.csv"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        writer = csv.writer(response)
        writer.writerow(headers)
        if include_sample:
            writer.writerow(cls.get_sample_row(import_type))
        return response


class BulkImportService:
    AUTO_DEDUP_IMPORT_TYPES = {ImportBatch.ImportType.SECTIONS}
    STUDENT_ROW_ACTION_CREATE = "CREATE"
    STUDENT_ROW_ACTION_UPDATE = "UPDATE"
    STUDENT_ROW_ACTION_UPSERT = "UPSERT"
    ENROLLMENT_STUDENT_MODE_KEY = "ENROLLMENT_STUDENT_MODE"
    ENROLLMENT_STUDENT_MODE_STRICT = "STRICT_EXISTING"
    ENROLLMENT_STUDENT_MODE_AUTO_CREATE = "AUTO_CREATE"

    IMPORT_PERMISSION_MAP = {
        ImportBatch.ImportType.SECTIONS: "sections.import",
        ImportBatch.ImportType.COURSES: "courses.import",
        ImportBatch.ImportType.STUDENTS: "students.import",
        ImportBatch.ImportType.COURSE_OFFERINGS: "course_offerings.import",
        ImportBatch.ImportType.FACULTY_ASSIGNMENTS: "faculty_assignments.import",
        ImportBatch.ImportType.FACULTY_USERS: "faculty_users.import",
        ImportBatch.ImportType.ENROLLMENT: "enrollment.import",
    }

    @classmethod
    def required_permission(cls, import_type: str) -> str:
        if import_type not in cls.IMPORT_PERMISSION_MAP:
            raise ValidationError("Unsupported import type.")
        return cls.IMPORT_PERMISSION_MAP[import_type]

    @classmethod
    def list_import_types(cls):
        return [
            ImportBatch.ImportType.SECTIONS,
            ImportBatch.ImportType.COURSES,
            ImportBatch.ImportType.STUDENTS,
            ImportBatch.ImportType.COURSE_OFFERINGS,
            ImportBatch.ImportType.FACULTY_ASSIGNMENTS,
            ImportBatch.ImportType.FACULTY_USERS,
            ImportBatch.ImportType.ENROLLMENT,
        ]

    @classmethod
    def get_enrollment_student_mode(cls, tenant_id: int | None):
        mode = SystemSettingService.get(
            cls.ENROLLMENT_STUDENT_MODE_KEY,
            tenant_id=tenant_id,
            default=cls.ENROLLMENT_STUDENT_MODE_STRICT,
        )
        mode = (
            str(mode or cls.ENROLLMENT_STUDENT_MODE_STRICT)
            .strip()
            .upper()
            .replace("-", "_")
            .replace(" ", "_")
        )
        mode_aliases = {
            "STRICT": cls.ENROLLMENT_STUDENT_MODE_STRICT,
            "EXISTING_ONLY": cls.ENROLLMENT_STUDENT_MODE_STRICT,
            "AUTO": cls.ENROLLMENT_STUDENT_MODE_AUTO_CREATE,
            "AUTOCREATE": cls.ENROLLMENT_STUDENT_MODE_AUTO_CREATE,
        }
        mode = mode_aliases.get(mode, mode)
        if mode not in {cls.ENROLLMENT_STUDENT_MODE_STRICT, cls.ENROLLMENT_STUDENT_MODE_AUTO_CREATE}:
            return cls.ENROLLMENT_STUDENT_MODE_STRICT
        return mode

    @staticmethod
    def slug_to_import_type(import_slug: str) -> str | None:
        mapping = {
            "sections": ImportBatch.ImportType.SECTIONS,
            "courses": ImportBatch.ImportType.COURSES,
            "students": ImportBatch.ImportType.STUDENTS,
            "course-offerings": ImportBatch.ImportType.COURSE_OFFERINGS,
            "faculty-assignments": ImportBatch.ImportType.FACULTY_ASSIGNMENTS,
            "faculty-users": ImportBatch.ImportType.FACULTY_USERS,
            "enrollment": ImportBatch.ImportType.ENROLLMENT,
        }
        return mapping.get(import_slug)

    @staticmethod
    def import_type_to_slug(import_type: str) -> str:
        mapping = {
            ImportBatch.ImportType.SECTIONS: "sections",
            ImportBatch.ImportType.COURSES: "courses",
            ImportBatch.ImportType.STUDENTS: "students",
            ImportBatch.ImportType.COURSE_OFFERINGS: "course-offerings",
            ImportBatch.ImportType.FACULTY_ASSIGNMENTS: "faculty-assignments",
            ImportBatch.ImportType.FACULTY_USERS: "faculty-users",
            ImportBatch.ImportType.ENROLLMENT: "enrollment",
        }
        return mapping.get(import_type, import_type)

    @staticmethod
    def _normalize_value(value) -> str:
        return (value or "").strip()

    @staticmethod
    def _read_csv_from_bytes(content: bytes) -> list[list[str]]:
        decoded = None
        for encoding in ("utf-8-sig", "utf-8"):
            try:
                decoded = content.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if decoded is None:
            raise ValidationError("CSV must be UTF-8 encoded.")
        reader = csv.reader(io.StringIO(decoded))
        return [[cell.strip() for cell in row] for row in reader]

    @staticmethod
    def _build_runtime(user, request):
        scope = getattr(request, "scope", {}) if request else {}
        tenant_ids = set(scope.get("tenant_ids", []))
        campus_ids = set(scope.get("campus_ids", []))
        department_ids = set(scope.get("department_ids", []))
        return {
            "tenant_ids": None if getattr(user, "is_superuser", False) else tenant_ids,
            "campus_ids": None if getattr(user, "is_superuser", False) else campus_ids,
            "department_ids": None if getattr(user, "is_superuser", False) else department_ids,
            "tenant_cache": {},
            "campus_cache": {},
            "department_cache": {},
            "program_cache": {},
            "academic_year_cache": {},
            "academic_year_code_options_cache": {},
            "term_cache": {},
            "course_cache": {},
            "section_cache": {},
            "student_cache": {},
            "faculty_cache": {},
            "offering_cache": {},
            "offering_cache_preloaded": False,
        }

    @staticmethod
    def _is_blank_row(row: list[str]) -> bool:
        return not any((cell or "").strip() for cell in row)

    @staticmethod
    def _parse_bool(value: str, field_name: str, errors: list[str], default=None):
        if value == "":
            return default
        normalized = value.strip().upper()
        if normalized in {"TRUE", "1", "YES", "Y"}:
            return True
        if normalized in {"FALSE", "0", "NO", "N"}:
            return False
        errors.append(f"{field_name}: invalid boolean value '{value}'. Use TRUE/FALSE.")
        return default

    @staticmethod
    def _parse_decimal(value: str, field_name: str, errors: list[str], default=None):
        if value == "":
            return default
        try:
            return Decimal(value)
        except (InvalidOperation, TypeError):
            errors.append(f"{field_name}: invalid decimal value '{value}'.")
            return default

    @staticmethod
    def _resolve_tenant(tenant_code: str, runtime: dict, errors: list[str]):
        tenant_code = tenant_code.strip()
        if not tenant_code:
            errors.append("tenant_code is required.")
            return None
        key = tenant_code.upper()
        if key not in runtime["tenant_cache"]:
            runtime["tenant_cache"][key] = Tenant.objects.filter(code__iexact=tenant_code, is_active=True).first()
        tenant = runtime["tenant_cache"][key]
        if not tenant:
            errors.append(f"tenant_code '{tenant_code}' not found.")
            return None
        if runtime["tenant_ids"] is not None and tenant.id not in runtime["tenant_ids"]:
            errors.append(f"tenant_code '{tenant_code}' is outside your scope.")
            return None
        return tenant

    @staticmethod
    def _resolve_campus(campus_code: str, tenant, runtime: dict, errors: list[str], required: bool = True):
        campus_code = campus_code.strip()
        if not campus_code:
            if required:
                errors.append("campus_code is required.")
            return None
        key = (tenant.id, campus_code.upper())
        if key not in runtime["campus_cache"]:
            runtime["campus_cache"][key] = Campus.objects.filter(
                tenant=tenant, code__iexact=campus_code, is_active=True
            ).first()
        campus = runtime["campus_cache"][key]
        if not campus:
            errors.append(f"campus_code '{campus_code}' not found for tenant '{tenant.code}'.")
            return None
        if runtime["campus_ids"] is not None and campus.id not in runtime["campus_ids"]:
            errors.append(f"campus_code '{campus_code}' is outside your scope.")
            return None
        return campus

    @staticmethod
    def _resolve_department(department_code: str, tenant, campus, runtime: dict, errors: list[str], required: bool = True):
        department_code = department_code.strip()
        if not department_code:
            if required:
                errors.append("department_code is required.")
            return None
        key = (tenant.id, campus.id if campus else None, department_code.upper())
        if key not in runtime["department_cache"]:
            query = Department.objects.filter(tenant=tenant, code__iexact=department_code, is_active=True)
            if campus:
                query = query.filter(campus=campus)
            runtime["department_cache"][key] = query.first()
        department = runtime["department_cache"][key]
        if not department:
            errors.append(
                f"department_code '{department_code}' not found for tenant '{tenant.code}'"
                + (f" and campus '{campus.code}'." if campus else ".")
            )
            return None
        return department

    @staticmethod
    def _resolve_program(program_code: str, tenant, campus, department, runtime: dict, errors: list[str], required: bool = True):
        program_code = program_code.strip()
        if not program_code:
            if required:
                errors.append("program_code is required.")
            return None
        key = (
            tenant.id,
            campus.id if campus else None,
            department.id if department else None,
            program_code.upper(),
        )
        if key not in runtime["program_cache"]:
            query = Program.objects.filter(tenant=tenant, code__iexact=program_code, is_active=True)
            if campus:
                query = query.filter(campus=campus)
            if department:
                query = query.filter(department=department)
            runtime["program_cache"][key] = query.first()
        program = runtime["program_cache"][key]
        if not program:
            errors.append(f"program_code '{program_code}' not found for selected tenant/campus/department.")
            return None
        return program

    @staticmethod
    def _infer_program_code_from_section(section_code: str) -> str | None:
        section_code = (section_code or "").strip()
        if not section_code:
            return None
        match = re.match(r"^(.*?)(?:\s+\d.*)?$", section_code)
        if not match:
            return None
        inferred = (match.group(1) or "").strip()
        return inferred or None

    @staticmethod
    def _normalized_code_token(value: str) -> str:
        return re.sub(r"[^A-Z0-9]+", "", (value or "").upper())

    @classmethod
    def _resolve_program_for_section_row(
        cls,
        *,
        program_code: str,
        section_code: str,
        tenant,
        campus,
        department,
        runtime: dict,
        errors: list[str],
    ) -> tuple[Program | None, str | None]:
        normalized_program = (program_code or "").strip()
        if normalized_program:
            program = cls._resolve_program(
                normalized_program,
                tenant,
                campus,
                department,
                runtime,
                errors,
                required=False,
            )
            if program:
                return program, None
            errors.append(
                f"program_code '{normalized_program}' not found for selected tenant/campus/department."
            )
            return None, None

        inferred_program_code = cls._infer_program_code_from_section(section_code)
        if inferred_program_code:
            inferred_errors = []
            inferred_program = cls._resolve_program(
                inferred_program_code,
                tenant,
                campus,
                department,
                runtime,
                inferred_errors,
                required=False,
            )
            if inferred_program:
                return inferred_program, None
            inferred_token = cls._normalized_code_token(inferred_program_code)
            if inferred_token:
                candidate_programs = list(
                    Program.objects.filter(
                        tenant=tenant,
                        campus=campus,
                        department=department,
                        is_active=True,
                    ).order_by("code")
                )
                exact_token_matches = [
                    row
                    for row in candidate_programs
                    if cls._normalized_code_token(row.code) == inferred_token
                ]
                if len(exact_token_matches) == 1:
                    return exact_token_matches[0], None
                near_matches = [
                    row
                    for row in candidate_programs
                    if cls._normalized_code_token(row.code).startswith(inferred_token)
                    or inferred_token.startswith(cls._normalized_code_token(row.code))
                ]
                if len(near_matches) == 1:
                    return near_matches[0], None
            # No existing match: allow deferred program auto-create on confirm step.
            return None, inferred_program_code

        available_programs = Program.objects.filter(
            tenant=tenant,
            campus=campus,
            department=department,
            is_active=True,
        ).order_by("code")
        if available_programs.count() == 1:
            return available_programs.first(), None

        errors.append(
            "program_code is required when it cannot be inferred from section_code."
        )
        return None, None

    @staticmethod
    def _resolve_academic_year(academic_year_code: str, tenant, runtime: dict, errors: list[str]):
        academic_year_code = academic_year_code.strip()
        if not academic_year_code:
            errors.append("academic_year_code is required.")
            return None
        key = (tenant.id, academic_year_code.upper())
        if key not in runtime["academic_year_cache"]:
            academic_year = AcademicYear.objects.filter(
                tenant=tenant, code__iexact=academic_year_code, is_active=True
            ).first()
            if not academic_year:
                by_name = list(
                    AcademicYear.objects.filter(tenant=tenant, name__iexact=academic_year_code, is_active=True).order_by("id")
                )
                if len(by_name) == 1:
                    academic_year = by_name[0]
                elif len(by_name) > 1:
                    errors.append(
                        f"academic_year_code '{academic_year_code}' matched multiple academic year names. Use AY code instead."
                    )
            runtime["academic_year_cache"][key] = academic_year
        academic_year = runtime["academic_year_cache"][key]
        if not academic_year:
            if tenant.id not in runtime["academic_year_code_options_cache"]:
                runtime["academic_year_code_options_cache"][tenant.id] = list(
                    AcademicYear.objects.filter(tenant=tenant, is_active=True)
                    .order_by("-start_date", "code")
                    .values_list("code", flat=True)[:10]
                )
            available_codes = runtime["academic_year_code_options_cache"][tenant.id]
            available_text = (
                f" Available active codes: {', '.join(available_codes)}."
                if available_codes
                else " No active Academic Year is configured for this tenant."
            )
            errors.append(
                f"academic_year_code '{academic_year_code}' not found for tenant '{tenant.code}'."
                f"{available_text}"
            )
            return None
        return academic_year

    @staticmethod
    def _resolve_term(term_code: str, tenant, academic_year, runtime: dict, errors: list[str]):
        term_code = term_code.strip()
        if not term_code:
            errors.append("term_code is required.")
            return None
        key = (tenant.id, academic_year.id if academic_year else None, term_code.upper())
        if key not in runtime["term_cache"]:
            query = Term.objects.filter(tenant=tenant, is_active=True)
            if academic_year:
                query = query.filter(academic_year=academic_year)
            term = query.filter(code__iexact=term_code).first()
            if not term:
                by_name = list(query.filter(name__iexact=term_code).order_by("id"))
                if len(by_name) == 1:
                    term = by_name[0]
                elif len(by_name) > 1:
                    errors.append(
                        f"term_code '{term_code}' matched multiple term names. Use Term code instead."
                    )
            runtime["term_cache"][key] = term
        term = runtime["term_cache"][key]
        if not term:
            errors.append(f"term_code '{term_code}' not found for tenant '{tenant.code}'.")
            return None
        return term

    @staticmethod
    def _resolve_course(course_code: str, tenant, runtime: dict, errors: list[str]):
        course_code = course_code.strip()
        if not course_code:
            errors.append("course_code is required.")
            return None
        key = (tenant.id, course_code.upper())
        if key not in runtime["course_cache"]:
            runtime["course_cache"][key] = Course.objects.select_related("campus", "department").filter(
                tenant=tenant, code__iexact=course_code, is_active=True
            ).first()
        course = runtime["course_cache"][key]
        if not course:
            errors.append(f"course_code '{course_code}' not found for tenant '{tenant.code}'.")
            return None
        return course

    @staticmethod
    def _resolve_section(section_code: str, tenant, campus, department, program, runtime: dict, errors: list[str], required: bool = True):
        section_code = section_code.strip()
        if not section_code:
            if required:
                errors.append("section_code is required.")
            return None
        key = (
            tenant.id,
            campus.id if campus else None,
            department.id if department else None,
            program.id if program else None,
            section_code.upper(),
        )
        if key not in runtime["section_cache"]:
            query = Section.objects.filter(tenant=tenant, code__iexact=section_code, is_active=True)
            if campus:
                query = query.filter(campus=campus)
            if department:
                query = query.filter(department=department)
            if program:
                query = query.filter(program=program)
            runtime["section_cache"][key] = query.first()
        section = runtime["section_cache"][key]
        if not section:
            errors.append("section_code not found for selected tenant/campus/department/program.")
            return None
        return section

    @staticmethod
    def _resolve_unique_section_for_offering_inference(
        section_code: str,
        tenant,
        campus,
        program_code: str,
    ):
        section_code = section_code.strip()
        if not section_code:
            return None
        query = Section.objects.select_related("department", "program").filter(
            tenant=tenant,
            campus=campus,
            code__iexact=section_code,
            is_active=True,
            department__is_active=True,
        )
        normalized_program_code = program_code.strip()
        if normalized_program_code:
            query = query.filter(program__code__iexact=normalized_program_code)
        matches = list(query.order_by("id"))
        if len(matches) == 1:
            return matches[0]
        return None

    @staticmethod
    def _resolve_student(
        student_no: str,
        tenant,
        campus,
        runtime: dict,
        errors: list[str],
        *,
        student_mode: str | None = None,
    ):
        student_no = student_no.strip()
        if not student_no:
            errors.append("student_no is required.")
            return None
        key = (tenant.id, campus.id if campus else None, student_no.upper())
        if key not in runtime["student_cache"]:
            query = Student.objects.filter(tenant=tenant, student_no__iexact=student_no, is_active=True)
            if campus:
                query = query.filter(campus=campus)
            runtime["student_cache"][key] = query.first()
        student = runtime["student_cache"][key]
        if not student:
            mode_note = (
                f" Current ENROLLMENT_STUDENT_MODE is {student_mode}."
                if student_mode
                else ""
            )
            campus_note = f" and campus '{campus.code}'" if campus else ""
            errors.append(
                f"student_no '{student_no}' not found for tenant '{tenant.code}'{campus_note}.{mode_note}"
            )
            return None
        return student

    @staticmethod
    def _find_student(student_no: str, tenant, campus, runtime: dict):
        student_no = student_no.strip()
        if not student_no:
            return None
        key = (tenant.id, campus.id if campus else None, student_no.upper())
        if key not in runtime["student_cache"]:
            query = Student.objects.filter(
                tenant=tenant,
                student_no__iexact=student_no,
                is_active=True,
            )
            if campus:
                query = query.filter(campus=campus)
            runtime["student_cache"][key] = query.first()
        return runtime["student_cache"][key]

    @staticmethod
    def _resolve_faculty_user(
        identifier: str,
        tenant,
        campus,
        runtime: dict,
        errors: list[str],
        *,
        allow_inactive_account: bool = False,
    ):
        identifier = identifier.strip()
        if not identifier:
            errors.append("faculty_username is required.")
            return None
        key = (identifier.lower(), allow_inactive_account)
        if key not in runtime["faculty_cache"]:
            query = User.objects.filter(Q(username__iexact=identifier) | Q(email__iexact=identifier))
            if not allow_inactive_account:
                query = query.filter(is_active=True)
            runtime["faculty_cache"][key] = query.order_by("id").first()
        user = runtime["faculty_cache"][key]
        if not user:
            errors.append(f"faculty_username '{identifier}' does not match any username/email.")
            return None

        has_faculty_role = (
            UserRole.objects.filter(user=user, role__code="FACULTY", is_active=True)
            .filter(Q(tenant_id=tenant.id) | Q(tenant__isnull=True))
            .filter(Q(campus_id=campus.id) | Q(campus__isnull=True))
            .exists()
        )
        if not has_faculty_role:
            errors.append(f"User '{identifier}' is not an active faculty for the selected scope.")
            return None
        return user

    @classmethod
    def _resolve_offering_for_reference(
        cls,
        *,
        tenant,
        campus,
        academic_year,
        term,
        course,
        section_code: str,
        runtime: dict,
        errors: list[str],
    ):
        section_code = section_code.strip()
        if not section_code:
            errors.append("section_code is required.")
            return None
        key = (tenant.id, campus.id, academic_year.id, term.id, course.id, section_code.upper())
        if key not in runtime["offering_cache"]:
            if runtime.get("offering_cache_preloaded"):
                matches = []
            else:
                matches = list(
                    cls._allowed_offerings_for_runtime(runtime)
                    .filter(
                        tenant=tenant,
                        campus=campus,
                        academic_year=academic_year,
                        term=term,
                        course=course,
                        section__code__iexact=section_code,
                    )
                    .select_related("section")
                    .order_by("id")
                )
            runtime["offering_cache"][key] = matches
        matches = runtime["offering_cache"][key]
        if not matches:
            errors.append("No course offering matches the tenant/campus/ay/term/course/section combination.")
            return None
        if len(matches) > 1:
            errors.append(
                "Multiple offerings match the tenant/campus/ay/term/course/section combination."
                " Add stricter setup to avoid ambiguous section codes."
            )
            return None
        return matches[0]

    @staticmethod
    def _allowed_offerings_for_runtime(runtime: dict):
        queryset = (
            CourseOffering.objects.filter(
                is_active=True,
                tenant__is_active=True,
                campus__is_active=True,
                department__is_active=True,
                academic_year__is_active=True,
                term__is_active=True,
                term__academic_year__is_active=True,
                course__is_active=True,
                section__is_active=True,
                section__department__is_active=True,
                section__program__is_active=True,
                section__program__department__is_active=True,
            )
            .filter(Q(program__isnull=True) | Q(program__is_active=True, program__department__is_active=True))
            .filter(Q(course__department__isnull=True) | Q(course__department__is_active=True))
        )
        if runtime.get("tenant_ids") is not None:
            queryset = queryset.filter(tenant_id__in=runtime["tenant_ids"])
        if runtime.get("campus_ids") is not None:
            queryset = queryset.filter(campus_id__in=runtime["campus_ids"])
        if runtime.get("department_ids") is not None:
            queryset = queryset.filter(
                department_id__in=runtime["department_ids"],
                section__department_id__in=runtime["department_ids"],
                section__program__department_id__in=runtime["department_ids"],
            ).filter(
                Q(program__isnull=True) | Q(program__department_id__in=runtime["department_ids"])
            )
        return queryset

    @classmethod
    def _preload_reference_offerings(cls, runtime: dict):
        if runtime.get("offering_cache_preloaded"):
            return
        offerings = cls._allowed_offerings_for_runtime(runtime).select_related("section").only(
            "id",
            "tenant_id",
            "campus_id",
            "academic_year_id",
            "term_id",
            "course_id",
            "section_id",
            "section__id",
            "section__code",
        )
        for offering in offerings:
            key = (
                offering.tenant_id,
                offering.campus_id,
                offering.academic_year_id,
                offering.term_id,
                offering.course_id,
                (offering.section.code or "").strip().upper(),
            )
            runtime["offering_cache"].setdefault(key, []).append(offering)
        runtime["offering_cache_preloaded"] = True

    @classmethod
    def _validate_section_row(cls, row: dict, runtime: dict):
        errors = []
        tenant = cls._resolve_tenant(row["tenant_code"], runtime, errors)
        campus = department = program = None
        auto_program_code = None
        skip_existing = False
        if tenant:
            campus = cls._resolve_campus(row["campus_code"], tenant, runtime, errors, required=True)
        if tenant and campus:
            department = cls._resolve_department(
                row["department_code"],
                tenant,
                campus,
                runtime,
                errors,
                required=True,
            )
        section_code = cls._normalize_value(row["section_code"])
        if not section_code:
            errors.append("section_code is required.")

        if tenant and campus and department and section_code:
            program, auto_program_code = cls._resolve_program_for_section_row(
                program_code=row["program_code"],
                section_code=section_code,
                tenant=tenant,
                campus=campus,
                department=department,
                runtime=runtime,
                errors=errors,
            )

        is_active = cls._parse_bool(cls._normalize_value(row.get("is_active", "")), "is_active", errors, default=True)
        section_name = cls._normalize_value(row.get("section_name")) or section_code
        year_level = cls._normalize_value(row.get("year_level")) or None
        if not year_level:
            year_match = re.search(r"\b([1-6])(?:st|nd|rd|th)?\b", section_code, flags=re.IGNORECASE)
            year_level = year_match.group(1) if year_match else None

        if tenant and campus and department and program and section_code:
            duplicate_exists = Section.objects.filter(
                tenant=tenant,
                campus=campus,
                department=department,
                program=program,
                code__iexact=section_code,
            ).exists()
            if duplicate_exists:
                skip_existing = True

        normalized = {
            "tenant_id": tenant.id if tenant else None,
            "campus_id": campus.id if campus else None,
            "department_id": department.id if department else None,
            "program_id": program.id if program else None,
            "program_code_auto": auto_program_code,
            "code": section_code,
            "name": section_name,
            "year_level": year_level,
            "is_active": bool(is_active),
            "skip_existing": skip_existing,
        }
        unique_program_key = str(program.id) if program else (auto_program_code or "")
        unique_key = (
            f"{tenant.id}:{campus.id}:{department.id}:{unique_program_key}:{section_code.upper()}"
            if tenant and campus and department and unique_program_key and section_code
            else None
        )
        return normalized, errors, unique_key

    @classmethod
    def _validate_course_row(cls, row: dict, runtime: dict):
        errors = []
        tenant = cls._resolve_tenant(row["tenant_code"], runtime, errors)
        campus = None
        department = None

        if tenant:
            campus = cls._resolve_campus(row["campus_code"], tenant, runtime, errors, required=False)
            if row["department_code"] and not row["campus_code"]:
                errors.append("department_code requires campus_code.")
            if row["department_code"]:
                department = cls._resolve_department(
                    row["department_code"], tenant, campus, runtime, errors, required=False
                )
                if department and campus and department.campus_id != campus.id:
                    errors.append("department_code does not belong to selected campus.")

        course_code = cls._normalize_value(row["course_code"])
        course_title = cls._normalize_value(row["course_title"])
        if not course_code:
            errors.append("course_code is required.")
        if not course_title:
            errors.append("course_title is required.")

        units = cls._parse_decimal(cls._normalize_value(row["units"]), "units", errors, default=None)
        default_base = cls._parse_decimal(
            cls._normalize_value(row["default_base_value"]), "default_base_value", errors, default=None
        )
        is_active = cls._parse_bool(cls._normalize_value(row["is_active"]), "is_active", errors, default=True)
        course_type = cls._normalize_value(row["course_type"]) or None

        if tenant and course_code and Course.objects.filter(tenant=tenant, code__iexact=course_code).exists():
            errors.append(f"Course '{course_code}' already exists for tenant '{tenant.code}'.")

        normalized = {
            "tenant_id": tenant.id if tenant else None,
            "campus_id": campus.id if campus else None,
            "department_id": department.id if department else None,
            "code": course_code,
            "title": course_title,
            "units": str(units) if units is not None else None,
            "course_type": course_type,
            "default_base_value": str(default_base) if default_base is not None else None,
            "is_active": bool(is_active),
        }
        unique_key = f"{tenant.id}:{course_code.upper()}" if tenant and course_code else None
        return normalized, errors, unique_key

    @classmethod
    def _validate_student_row(cls, row: dict, runtime: dict):
        errors = []
        action = cls._normalize_value(row["row_action"]).upper().replace("-", "_").replace(" ", "_")
        action_aliases = {
            "ADD": cls.STUDENT_ROW_ACTION_CREATE,
            "CREATE_ONLY": cls.STUDENT_ROW_ACTION_CREATE,
            "EDIT": cls.STUDENT_ROW_ACTION_UPDATE,
            "UPDATE_ONLY": cls.STUDENT_ROW_ACTION_UPDATE,
            "UPDATE_OR_CREATE": cls.STUDENT_ROW_ACTION_UPSERT,
            "CREATE_OR_UPDATE": cls.STUDENT_ROW_ACTION_UPSERT,
        }
        action = action_aliases.get(action, action)
        allowed_actions = {
            cls.STUDENT_ROW_ACTION_CREATE,
            cls.STUDENT_ROW_ACTION_UPDATE,
            cls.STUDENT_ROW_ACTION_UPSERT,
        }
        if action not in allowed_actions:
            errors.append("row_action must be CREATE, UPDATE, or UPSERT.")

        tenant = cls._resolve_tenant(row["tenant_code"], runtime, errors)
        campus = None
        existing_student = None
        student_no = cls._normalize_value(row["student_no"])
        if tenant:
            campus = cls._resolve_campus(row["campus_code"], tenant, runtime, errors, required=True)
        if not student_no:
            errors.append("student_no is required.")
        if tenant and campus and student_no:
            existing_student = (
                Student.objects.select_related("department", "program")
                .filter(
                    tenant=tenant,
                    campus=campus,
                    student_no__iexact=student_no,
                )
                .first()
            )
            if action == cls.STUDENT_ROW_ACTION_CREATE and existing_student:
                errors.append("student_no already exists for this tenant/campus. Use UPDATE or UPSERT.")
            if action == cls.STUDENT_ROW_ACTION_UPDATE and not existing_student:
                errors.append("student_no does not exist for this tenant/campus. Use CREATE or UPSERT.")

        will_update = bool(existing_student and action in {cls.STUDENT_ROW_ACTION_UPDATE, cls.STUDENT_ROW_ACTION_UPSERT})
        will_create = bool(not existing_student and action in {cls.STUDENT_ROW_ACTION_CREATE, cls.STUDENT_ROW_ACTION_UPSERT})

        department = None
        department_code = cls._normalize_value(row["department_code"])
        if department_code:
            if tenant and campus:
                department = cls._resolve_department(department_code, tenant, campus, runtime, errors, required=False)
        elif will_update and existing_student:
            department = existing_student.department
        elif will_create:
            errors.append("department_code is required when creating a student.")

        program = None
        program_code = cls._normalize_value(row["program_code"])
        if program_code:
            if not department:
                errors.append("program_code requires a resolved department_code or existing student department.")
            elif tenant and campus:
                program = cls._resolve_program(program_code, tenant, campus, department, runtime, errors, required=False)
        elif will_update and existing_student:
            if department and existing_student.department_id == department.id:
                program = existing_student.program
            else:
                program = None

        last_name_input = cls._normalize_value(row["last_name"])
        first_name_input = cls._normalize_value(row["first_name"])
        last_name = last_name_input or (existing_student.last_name if will_update and existing_student else "")
        first_name = first_name_input or (existing_student.first_name if will_update and existing_student else "")
        if will_create and not last_name:
            errors.append("last_name is required when creating a student.")
        if will_create and not first_name:
            errors.append("first_name is required when creating a student.")

        middle_name_input = cls._normalize_value(row["middle_name"])
        official_email_input = cls._normalize_value(row["official_email"])
        sex_input = cls._normalize_value(row["sex"])
        year_level_input = cls._normalize_value(row["year_level"])

        official_email = official_email_input
        if not official_email and will_update and existing_student:
            official_email = existing_student.official_email or ""
        if official_email_input:
            try:
                validate_email(official_email_input)
            except ValidationError:
                errors.append("official_email is invalid.")

        verified_input = cls._normalize_value(row["official_email_verified"])
        official_email_verified = cls._parse_bool(
            verified_input,
            "official_email_verified",
            errors,
            default=None,
        )
        if official_email_verified is True and not official_email:
            errors.append("official_email is required when official_email_verified is TRUE.")

        raw_status = cls._normalize_value(row["status"]).upper()
        status = raw_status or (existing_student.status if will_update and existing_student else Student.Status.ACTIVE)
        allowed_statuses = {choice for choice, _ in Student.Status.choices}
        if status not in allowed_statuses:
            errors.append(f"status must be one of {sorted(allowed_statuses)}.")

        is_active_input = cls._normalize_value(row["is_active"])
        parsed_is_active = cls._parse_bool(is_active_input, "is_active", errors, default=None)
        if parsed_is_active is None:
            is_active = existing_student.is_active if will_update and existing_student else True
        else:
            is_active = parsed_is_active

        normalized = {
            "row_action": action,
            "existing_student_id": existing_student.id if existing_student else None,
            "tenant_id": tenant.id if tenant else None,
            "campus_id": campus.id if campus else None,
            "department_id": department.id if department else None,
            "program_id": program.id if program else None,
            "student_no": student_no,
            "last_name": last_name,
            "first_name": first_name,
            "middle_name": middle_name_input or (
                existing_student.middle_name if will_update and existing_student else None
            ),
            "official_email": official_email or None,
            "official_email_input_provided": bool(official_email_input),
            "official_email_verified": official_email_verified,
            "sex": sex_input or (existing_student.sex if will_update and existing_student else None),
            "year_level": year_level_input or (existing_student.year_level if will_update and existing_student else None),
            "status": status,
            "is_active": bool(is_active),
        }
        unique_key = f"{tenant.id}:{campus.id}:{student_no.upper()}" if tenant and campus and student_no else None
        return normalized, errors, unique_key

    @classmethod
    def _validate_course_offering_row(cls, row: dict, runtime: dict):
        errors = []
        tenant = cls._resolve_tenant(row["tenant_code"], runtime, errors)
        campus = department = program = academic_year = term = course = section = None
        auto_section_payload = None
        auto_program_code = None
        section_code = cls._normalize_value(row["section_code"])
        if not section_code:
            errors.append("section_code is required.")
        if tenant:
            campus = cls._resolve_campus(row["campus_code"], tenant, runtime, errors, required=True)
            course = cls._resolve_course(row["course_code"], tenant, runtime, errors)
            if course and course.campus_id and campus and course.campus_id != campus.id:
                errors.append("course_code belongs to a different campus than campus_code.")
        if tenant and campus:
            department_code = cls._normalize_value(row["department_code"])
            if department_code:
                department = cls._resolve_department(department_code, tenant, campus, runtime, errors, required=False)
            else:
                inferred_section = cls._resolve_unique_section_for_offering_inference(
                    cls._normalize_value(row["section_code"]),
                    tenant,
                    campus,
                    cls._normalize_value(row["program_code"]),
                )
                if inferred_section:
                    section = inferred_section
                    department = inferred_section.department
                    program = inferred_section.program
                    if department.tenant_id != tenant.id:
                        errors.append("Section department does not belong to selected tenant.")
                        department = None
                    elif department.campus_id != campus.id:
                        errors.append("Section department does not belong to selected campus.")
                        department = None
                if not department and course and course.department_id and course.department.is_active:
                    department = course.department
                    if department.tenant_id != tenant.id:
                        errors.append("Course department does not belong to selected tenant.")
                        department = None
                    elif department.campus_id != campus.id:
                        errors.append("Course department does not belong to selected campus.")
                        department = None
                elif not department:
                    errors.append("department_code is required unless section_code or course_code has a campus-matching department.")
            # program_code is optional for open/shared offerings; section resolution will guard ambiguity.
            if department and not section:
                program, auto_program_code = cls._resolve_program_for_section_row(
                    program_code=row["program_code"],
                    section_code=section_code,
                    tenant=tenant,
                    campus=campus,
                    department=department,
                    runtime=runtime,
                    errors=errors,
                )
                if section_code and (program or auto_program_code):
                    section_matches = Section.objects.filter(
                        tenant=tenant,
                        campus=campus,
                        department=department,
                        code__iexact=section_code,
                        is_active=True,
                    )
                    if program:
                        section_matches = section_matches.filter(program=program)
                    matches = list(section_matches.order_by("id"))
                    if len(matches) == 1:
                        section = matches[0]
                        program = section.program
                    elif len(matches) > 1:
                        errors.append("program_code is required because section_code is ambiguous across multiple programs.")
                    else:
                        inactive_exists = Section.objects.filter(
                            tenant=tenant,
                            campus=campus,
                            department=department,
                            code__iexact=section_code,
                            is_active=False,
                        )
                        if program:
                            inactive_exists = inactive_exists.filter(program=program)
                        if inactive_exists.exists():
                            errors.append("section_code exists but is inactive. Reactivate the section first.")
                        else:
                            year_match = re.search(r"\b([1-6])(?:st|nd|rd|th)?\b", section_code, flags=re.IGNORECASE)
                            auto_section_payload = {
                                "tenant_id": tenant.id,
                                "campus_id": campus.id,
                                "department_id": department.id,
                                "program_id": program.id if program else None,
                                "program_code_auto": auto_program_code,
                                "code": section_code,
                                "name": section_code,
                                "year_level": year_match.group(1) if year_match else None,
                                "is_active": True,
                            }
        if tenant:
            academic_year = cls._resolve_academic_year(row["academic_year_code"], tenant, runtime, errors)
        if tenant and academic_year:
            term = cls._resolve_term(row["term_code"], tenant, academic_year, runtime, errors)
            if term and term.academic_year_id != academic_year.id:
                errors.append("term_code does not belong to selected academic_year_code.")
        status = cls._normalize_value(row["status"]).upper() or CourseOffering.Status.OPEN
        if status not in {choice for choice, _ in CourseOffering.Status.choices}:
            errors.append(f"status must be one of {[choice for choice, _ in CourseOffering.Status.choices]}.")

        if tenant and campus and department and term and course and section:
            duplicate_exists = CourseOffering.objects.filter(
                tenant=tenant,
                campus=campus,
                department=department,
                term=term,
                course=course,
                section=section,
            ).exists()
            if duplicate_exists:
                errors.append(
                    "Course offering already exists for tenant/campus/department/term/course/section."
                )

        normalized = {
            "tenant_id": tenant.id if tenant else None,
            "campus_id": campus.id if campus else None,
            "department_id": department.id if department else None,
            "program_id": program.id if program else None,
            "academic_year_id": academic_year.id if academic_year else None,
            "term_id": term.id if term else None,
            "course_id": course.id if course else None,
            "section_id": section.id if section else None,
            "section_payload": auto_section_payload,
            "room": cls._normalize_value(row["room"]) or None,
            "schedule_text": cls._normalize_value(row["schedule_text"]) or None,
            "status": status,
            "is_active": True,
        }
        section_unique_key = None
        if section:
            section_unique_key = str(section.id)
        elif auto_section_payload:
            section_unique_key = (
                "NEW:"
                f"{auto_section_payload.get('program_id') or auto_section_payload.get('program_code_auto')}:"
                f"{section_code.upper()}"
            )
        unique_key = (
            f"{tenant.id}:{campus.id}:{department.id}:{term.id}:{course.id}:{section_unique_key}"
            if tenant and campus and department and term and course and section_unique_key
            else None
        )
        return normalized, errors, unique_key

    @classmethod
    def _validate_faculty_assignment_row(cls, row: dict, runtime: dict):
        errors = []
        tenant = cls._resolve_tenant(row["tenant_code"], runtime, errors)
        campus = academic_year = term = course = offering = faculty_user = None
        if tenant:
            campus = cls._resolve_campus(row["campus_code"], tenant, runtime, errors, required=True)
            academic_year = cls._resolve_academic_year(row["academic_year_code"], tenant, runtime, errors)
            if academic_year:
                term = cls._resolve_term(row["term_code"], tenant, academic_year, runtime, errors)
            course = cls._resolve_course(row["course_code"], tenant, runtime, errors)
        if tenant and campus and academic_year and term and course:
            offering = cls._resolve_offering_for_reference(
                tenant=tenant,
                campus=campus,
                academic_year=academic_year,
                term=term,
                course=course,
                section_code=row["section_code"],
                runtime=runtime,
                errors=errors,
            )
        if tenant and campus:
            faculty_user = cls._resolve_faculty_user(
                row["faculty_username"],
                tenant,
                campus,
                runtime,
                errors,
                allow_inactive_account=True,
            )

        is_primary = cls._parse_bool(cls._normalize_value(row["is_primary"]), "is_primary", errors, default=False)
        if offering and faculty_user:
            if FacultyAssignment.objects.filter(offering=offering, faculty_user=faculty_user).exists():
                errors.append("Faculty assignment already exists for this offering and faculty user.")

        normalized = {
            "offering_id": offering.id if offering else None,
            "faculty_user_id": faculty_user.id if faculty_user else None,
            "is_primary": bool(is_primary),
            "is_active": True,
        }
        unique_key = f"{offering.id}:{faculty_user.id}" if offering and faculty_user else None
        return normalized, errors, unique_key

    @classmethod
    def _validate_faculty_user_row(cls, row: dict, runtime: dict):
        errors = []
        if "active_faculty_role_error" not in runtime:
            try:
                FacultyAccountProvisioningService.resolve_active_faculty_role()
            except ValidationError as exc:
                runtime["active_faculty_role_error"] = "; ".join(exc.messages)
            else:
                runtime["active_faculty_role_error"] = ""
        if runtime["active_faculty_role_error"]:
            errors.append(runtime["active_faculty_role_error"])
        tenant = cls._resolve_tenant(row["tenant_code"], runtime, errors)
        campus = department = None
        if tenant:
            campus = cls._resolve_campus(row["campus_code"], tenant, runtime, errors, required=True)
        if tenant and campus:
            department = cls._resolve_department(
                row["department_code"],
                tenant,
                campus,
                runtime,
                errors,
                required=True,
            )
        if department and runtime.get("department_ids") is not None and department.id not in runtime["department_ids"]:
            errors.append(f"department_code '{department.code}' is outside your scope.")
            department = None

        first_name = cls._normalize_value(row["first_name"])
        middle_name = cls._normalize_value(row["middle_name"])
        last_name = cls._normalize_value(row["last_name"])
        if not first_name:
            errors.append("first_name is required.")
        if not last_name:
            errors.append("last_name is required.")

        email = cls._normalize_value(row["email"]).lower()
        if not email:
            errors.append("email is required.")
        elif tenant:
            try:
                email = FacultyAccountProvisioningService.validate_email_for_tenant(email, tenant.id)
            except ValidationError as exc:
                errors.extend(exc.messages)
        else:
            try:
                validate_email(email)
            except ValidationError:
                errors.append("email is invalid.")

        supplied_username = cls._normalize_value(row["username"])
        derived_username = email.split("@", 1)[0] if email and "@" in email else ""
        username = FacultyAccountProvisioningService.normalize_username(supplied_username or derived_username)
        if not username:
            errors.append("username could not be derived from email.")
        elif len(username) > 150:
            errors.append("username must be 150 characters or fewer.")

        if email and email in runtime.get("duplicate_faculty_emails", set()):
            errors.append("Duplicate email in this upload file; all conflicting rows are invalid.")
        if username and username in runtime.get("duplicate_faculty_usernames", set()):
            errors.append("Duplicate username in this upload file; all conflicting rows are invalid.")

        email_user = User.objects.filter(email__iexact=email).order_by("id").first() if email else None
        username_user = User.objects.filter(username__iexact=username).order_by("id").first() if username else None
        existing_user = None
        skip_existing = False
        if email_user or username_user:
            if email_user and username_user and email_user.id == username_user.id and tenant and campus and department:
                has_exact_faculty_scope = UserRole.objects.filter(
                    user=email_user,
                    role__code="FACULTY",
                    role__is_active=True,
                    is_active=True,
                    tenant=tenant,
                    campus=campus,
                    department=department,
                ).exists()
                if has_exact_faculty_scope:
                    existing_user = email_user
                    skip_existing = True
                else:
                    errors.append(
                        "Existing user matches email and username but does not have the exact requested active FACULTY role scope; manual review is required."
                    )
            elif email_user and username_user and email_user.id != username_user.id:
                errors.append("Email and username belong to different existing users.")
            elif email_user:
                errors.append("Email is already assigned to an existing user with a different username; manual review is required.")
            else:
                errors.append("Username is already assigned to an existing user with a different email.")

        normalized = {
            "tenant_id": tenant.id if tenant else None,
            "campus_id": campus.id if campus else None,
            "department_id": department.id if department else None,
            "first_name": first_name,
            "middle_name": middle_name or None,
            "last_name": last_name,
            "email": email,
            "username": username,
            "username_derived": not bool(supplied_username),
            "skip_existing": skip_existing,
            "existing_user_id": existing_user.id if existing_user else None,
        }
        unique_key = f"{email}:{username}" if email and username else None
        return normalized, errors, unique_key

    @classmethod
    def _validate_enrollment_row(cls, row: dict, runtime: dict):
        errors = []
        tenant = cls._resolve_tenant(row["tenant_code"], runtime, errors)
        campus = academic_year = term = course = offering = student = None
        student_mode = cls.ENROLLMENT_STUDENT_MODE_STRICT
        student_no = cls._normalize_value(row["student_no"])
        if tenant:
            campus = cls._resolve_campus(row["campus_code"], tenant, runtime, errors, required=True)
            academic_year = cls._resolve_academic_year(row["academic_year_code"], tenant, runtime, errors)
            if academic_year:
                term = cls._resolve_term(row["term_code"], tenant, academic_year, runtime, errors)
            course = cls._resolve_course(row["course_code"], tenant, runtime, errors)
            student_mode = cls.get_enrollment_student_mode(tenant.id)
            mode = EnrollmentService.get_enrollment_mode(tenant.id)
            if mode not in {EnrollmentService.ADMIN_ONLY, EnrollmentService.FACULTY_ALLOWED}:
                errors.append("ENROLLMENT_OWNERSHIP_MODE is invalid for tenant.")
            if student_mode not in {cls.ENROLLMENT_STUDENT_MODE_STRICT, cls.ENROLLMENT_STUDENT_MODE_AUTO_CREATE}:
                errors.append("ENROLLMENT_STUDENT_MODE is invalid for tenant.")
        if tenant and campus and academic_year and term and course:
            offering = cls._resolve_offering_for_reference(
                tenant=tenant,
                campus=campus,
                academic_year=academic_year,
                term=term,
                course=course,
                section_code=row["section_code"],
                runtime=runtime,
                errors=errors,
            )

        if tenant:
            if student_mode == cls.ENROLLMENT_STUDENT_MODE_STRICT:
                student = cls._resolve_student(
                    student_no,
                    tenant,
                    campus,
                    runtime,
                    errors,
                    student_mode=student_mode,
                )
            else:
                student = cls._find_student(student_no, tenant, campus, runtime)
                if not student:
                    existing_any_status_query = Student.objects.filter(
                        tenant=tenant,
                        student_no__iexact=student_no,
                    )
                    if campus:
                        existing_any_status_query = existing_any_status_query.filter(campus=campus)
                    existing_any_status = existing_any_status_query.first()
                    if existing_any_status and not existing_any_status.is_active:
                        errors.append("student_no exists but is inactive. Reactivate student first.")
                    if not student_no:
                        errors.append("student_no is required.")
                    if not cls._normalize_value(row.get("student_last_name")):
                        errors.append("student_last_name is required when student is missing and AUTO_CREATE is enabled.")
                    if not cls._normalize_value(row.get("student_first_name")):
                        errors.append("student_first_name is required when student is missing and AUTO_CREATE is enabled.")

        raw_status = cls._normalize_value(row["enrollment_status"]).upper() or Enrollment.Status.ACTIVE
        status_map = {
            "ENROLLED": Enrollment.Status.ACTIVE,
            "DR": Enrollment.Status.DRP,
        }
        normalized_status = status_map.get(raw_status, raw_status)
        allowed_statuses = {choice for choice, _ in Enrollment.Status.choices}
        if normalized_status not in allowed_statuses:
            errors.append(f"enrollment_status must be one of {sorted(allowed_statuses | {'ENROLLED'})}.")

        if student and campus and student.campus_id != campus.id:
            errors.append("student_no campus does not match campus_code.")

        if offering and student and Enrollment.objects.filter(course_offering=offering, student=student).exists():
            errors.append("Enrollment already exists for this student and offering.")
        elif offering and tenant and not student and student_no:
            if Enrollment.objects.filter(
                course_offering=offering,
                student__tenant=tenant,
                student__campus=campus,
                student__student_no__iexact=student_no,
                student__is_active=True,
            ).exists():
                errors.append("Enrollment already exists for this student_no and offering.")

        normalized = {
            "tenant_id": tenant.id if tenant else None,
            "campus_id": campus.id if campus else None,
            "academic_year_id": academic_year.id if academic_year else None,
            "term_id": term.id if term else None,
            "course_offering_id": offering.id if offering else None,
            "student_id": student.id if student else None,
            "student_no": student_no,
            "student_mode": student_mode,
            "student_payload": {
                "last_name": cls._normalize_value(row.get("student_last_name")),
                "first_name": cls._normalize_value(row.get("student_first_name")),
                "middle_name": cls._normalize_value(row.get("student_middle_name")) or None,
                "sex": cls._normalize_value(row.get("student_sex")) or None,
                "year_level": cls._normalize_value(row.get("student_year_level")) or None,
            },
            "enrollment_status": normalized_status,
            "is_active": True,
            "encoded_via_portal": Enrollment.SourcePortal.ADMIN,
        }
        student_key = str(student.id) if student else student_no.upper()
        unique_key = f"{offering.id}:{student_key}" if offering and student_key else None
        return normalized, errors, unique_key

    @classmethod
    def _validate_row(cls, import_type: str, row: dict, runtime: dict):
        if import_type == ImportBatch.ImportType.SECTIONS:
            return cls._validate_section_row(row, runtime)
        if import_type == ImportBatch.ImportType.COURSES:
            return cls._validate_course_row(row, runtime)
        if import_type == ImportBatch.ImportType.STUDENTS:
            return cls._validate_student_row(row, runtime)
        if import_type == ImportBatch.ImportType.COURSE_OFFERINGS:
            return cls._validate_course_offering_row(row, runtime)
        if import_type == ImportBatch.ImportType.FACULTY_ASSIGNMENTS:
            return cls._validate_faculty_assignment_row(row, runtime)
        if import_type == ImportBatch.ImportType.FACULTY_USERS:
            return cls._validate_faculty_user_row(row, runtime)
        if import_type == ImportBatch.ImportType.ENROLLMENT:
            return cls._validate_enrollment_row(row, runtime)
        raise ValidationError("Unsupported import type.")

    @staticmethod
    def _build_error_summary(rows: list[ImportBatchRow], extra_messages: list[str] | None = None):
        field_error_counter = {}
        for row in rows:
            for message in (row.errors_json or []):
                key = str(message)
                field_error_counter[key] = field_error_counter.get(key, 0) + 1
        summary = {
            "top_errors": sorted(
                [{"message": message, "count": count} for message, count in field_error_counter.items()],
                key=lambda item: (-item["count"], item["message"]),
            )[:10]
        }
        if extra_messages:
            summary["messages"] = extra_messages
        return summary

    @classmethod
    def validate_and_stage_upload(cls, *, import_type: str, uploaded_file, user, request):
        if import_type not in cls.list_import_types():
            raise ValidationError("Unsupported import type.")

        expected_headers = ImportTemplateService.get_headers(import_type)
        scope = getattr(request, "scope", {}) if request else {}
        content = uploaded_file.read()
        original_filename = uploaded_file.name
        retain_source_file = import_type != ImportBatch.ImportType.FACULTY_USERS
        source_file = ContentFile(content, name=original_filename) if retain_source_file else None
        upload_metadata = {
            "scope": scope,
            "original_filename": original_filename,
            "content_type": (getattr(uploaded_file, "content_type", "") or "").strip(),
            "file_size_bytes": len(content or b""),
            "raw_file_retention": "STORED" if retain_source_file else "NOT_STORED_AFTER_PARSE",
        }

        batch = ImportBatch.objects.create(
            import_type=import_type,
            uploaded_by_user=user,
            tenant_id=scope.get("tenant_id"),
            campus_id=scope.get("campus_id"),
            status=ImportBatch.Status.VALIDATED,
            source_file=source_file,
            original_filename=original_filename,
            expected_headers_json=expected_headers,
            actual_headers_json=[],
            metadata_json=upload_metadata,
        )
        batch.metadata_json = {
            **upload_metadata,
            "stored_filename": batch.source_file.name if batch.source_file else "",
        }
        batch.save(update_fields=["metadata_json", "updated_at"])

        try:
            csv_rows = cls._read_csv_from_bytes(content)
        except ValidationError as exc:
            batch.status = ImportBatch.Status.VALIDATION_FAILED
            batch.error_summary_json = {"messages": [str(exc)]}
            batch.save(update_fields=["status", "error_summary_json", "updated_at"])
            return batch

        if not csv_rows:
            batch.status = ImportBatch.Status.VALIDATION_FAILED
            batch.error_summary_json = {"messages": ["CSV is empty."]}
            batch.save(update_fields=["status", "error_summary_json", "updated_at"])
            return batch

        actual_headers = [cell.strip() for cell in csv_rows[0]]
        batch.actual_headers_json = actual_headers
        if actual_headers != expected_headers:
            batch.status = ImportBatch.Status.VALIDATION_FAILED
            batch.error_summary_json = {
                "messages": [
                    "Invalid template headers. Please download and use the official template.",
                ],
                "expected_headers": expected_headers,
                "actual_headers": actual_headers,
            }
            batch.save(
                update_fields=[
                    "status",
                    "actual_headers_json",
                    "error_summary_json",
                    "updated_at",
                ]
            )
            return batch

        runtime = cls._build_runtime(user=user, request=request)
        if import_type == ImportBatch.ImportType.FACULTY_USERS:
            email_counts = {}
            username_counts = {}
            for values in csv_rows[1:]:
                if cls._is_blank_row(values) or len(values) != len(expected_headers):
                    continue
                raw = {header: cls._normalize_value(values[index]) for index, header in enumerate(expected_headers)}
                email_key = raw["email"].lower()
                username_key = FacultyAccountProvisioningService.normalize_username(
                    raw["username"] or (email_key.split("@", 1)[0] if "@" in email_key else "")
                )
                if email_key:
                    email_counts[email_key] = email_counts.get(email_key, 0) + 1
                if username_key:
                    username_counts[username_key] = username_counts.get(username_key, 0) + 1
            runtime["duplicate_faculty_emails"] = {
                value for value, count in email_counts.items() if count > 1
            }
            runtime["duplicate_faculty_usernames"] = {
                value for value, count in username_counts.items() if count > 1
            }
        if import_type in {
            ImportBatch.ImportType.ENROLLMENT,
            ImportBatch.ImportType.FACULTY_ASSIGNMENTS,
        }:
            cls._preload_reference_offerings(runtime)
        row_objects = []
        seen_keys = set()
        dedup_skipped_count = 0
        existing_skipped_count = 0

        line_no = 1
        for line_no, row_values in enumerate(csv_rows[1:], start=2):
            if cls._is_blank_row(row_values):
                continue
            row_data = {}
            errors = []
            if len(row_values) != len(expected_headers):
                errors.append(
                    f"Expected {len(expected_headers)} columns based on template, got {len(row_values)}."
                )
            for index, header in enumerate(expected_headers):
                row_data[header] = cls._normalize_value(row_values[index] if index < len(row_values) else "")

            normalized = {}
            unique_key = None
            if not errors:
                normalized, row_errors, unique_key = cls._validate_row(import_type, row_data, runtime)
                errors.extend(row_errors)
                if (
                    import_type == ImportBatch.ImportType.SECTIONS
                    and not errors
                    and normalized.get("skip_existing")
                ):
                    existing_skipped_count += 1
                    continue

            if unique_key and unique_key in seen_keys:
                if import_type in cls.AUTO_DEDUP_IMPORT_TYPES:
                    dedup_skipped_count += 1
                    continue
                errors.append("Duplicate row in this upload file.")
            if unique_key and not errors:
                seen_keys.add(unique_key)

            row_objects.append(
                ImportBatchRow(
                    batch=batch,
                    row_number=line_no,
                    row_status=ImportBatchRow.RowStatus.ERROR if errors else ImportBatchRow.RowStatus.VALID,
                    raw_data_json=row_data,
                    normalized_data_json=normalized or None,
                    errors_json=errors or None,
                    result_code=(
                        "FAILED_VALIDATION"
                        if errors
                        else (
                            "PREVIEW_SKIP_EXISTING"
                            if import_type == ImportBatch.ImportType.FACULTY_USERS
                            and normalized.get("skip_existing")
                            else (
                                "PREVIEW_CREATE"
                                if import_type == ImportBatch.ImportType.FACULTY_USERS
                                else None
                            )
                        )
                    ),
                )
            )

        if not row_objects:
            no_new_rows_messages = []
            if dedup_skipped_count:
                no_new_rows_messages.append(
                    f"Deduplicated rows skipped before staging: {dedup_skipped_count}."
                )
            if existing_skipped_count:
                no_new_rows_messages.append(
                    f"Rows already existing in sections table and skipped: {existing_skipped_count}."
                )
            batch.status = (
                ImportBatch.Status.VALIDATED
                if no_new_rows_messages
                else ImportBatch.Status.VALIDATION_FAILED
            )
            batch.total_rows = 0
            batch.valid_rows = 0
            batch.invalid_rows = 0
            batch.error_summary_json = {
                "messages": no_new_rows_messages or ["No data rows found below the header row."]
            }
            metadata = dict(batch.metadata_json or {})
            metadata["dedup_skipped_rows"] = dedup_skipped_count
            metadata["existing_skipped_rows"] = existing_skipped_count
            batch.metadata_json = metadata
            batch.save(
                update_fields=[
                    "status",
                    "actual_headers_json",
                    "total_rows",
                    "valid_rows",
                    "invalid_rows",
                    "error_summary_json",
                    "metadata_json",
                    "updated_at",
                ]
            )
            return batch

        ImportBatchRow.objects.bulk_create(row_objects)
        invalid_rows = sum(1 for row in row_objects if row.row_status == ImportBatchRow.RowStatus.ERROR)
        valid_rows = len(row_objects) - invalid_rows
        batch.total_rows = len(row_objects)
        batch.valid_rows = valid_rows
        batch.invalid_rows = invalid_rows
        batch.status = ImportBatch.Status.VALIDATED
        summary_messages = []
        if dedup_skipped_count:
            summary_messages.append(
                f"Deduplicated rows skipped before staging: {dedup_skipped_count}."
            )
        if existing_skipped_count:
            summary_messages.append(
                f"Rows already existing in sections table and skipped: {existing_skipped_count}."
            )
        batch.error_summary_json = cls._build_error_summary(
            row_objects,
            extra_messages=summary_messages or None,
        )
        metadata = dict(batch.metadata_json or {})
        metadata["dedup_skipped_rows"] = dedup_skipped_count
        metadata["existing_skipped_rows"] = existing_skipped_count
        batch.metadata_json = metadata
        batch.save(
            update_fields=[
                "actual_headers_json",
                "total_rows",
                "valid_rows",
                "invalid_rows",
                "status",
                "error_summary_json",
                "metadata_json",
                "updated_at",
            ]
        )
        return batch

    @classmethod
    def _create_course(cls, normalized: dict):
        units = normalized.get("units")
        default_base_value = normalized.get("default_base_value")
        row = Course.objects.create(
            tenant_id=normalized["tenant_id"],
            campus_id=normalized.get("campus_id"),
            department_id=normalized.get("department_id"),
            code=normalized["code"],
            title=normalized["title"],
            units=Decimal(units) if units is not None else None,
            course_type=normalized.get("course_type"),
            default_base_value=Decimal(default_base_value) if default_base_value is not None else None,
            is_active=bool(normalized.get("is_active", True)),
        )
        return "Course", row

    @staticmethod
    def _student_audit_snapshot(student: Student):
        return {
            "tenant_id": student.tenant_id,
            "campus_id": student.campus_id,
            "department_id": student.department_id,
            "program_id": student.program_id,
            "student_no": student.student_no,
            "last_name": student.last_name,
            "first_name": student.first_name,
            "middle_name": student.middle_name,
            "official_email": student.official_email,
            "official_email_verified": bool(student.official_email_verified_at),
            "sex": student.sex,
            "year_level": student.year_level,
            "status": student.status,
            "is_active": student.is_active,
        }

    @classmethod
    def _create_or_update_student(cls, normalized: dict):
        existing_student_id = normalized.get("existing_student_id")
        action = normalized.get("row_action") or cls.STUDENT_ROW_ACTION_CREATE
        verified_value = normalized.get("official_email_verified")
        now = timezone.now()

        if existing_student_id:
            student = Student.objects.get(id=existing_student_id)
            before = cls._student_audit_snapshot(student)
            old_email = student.official_email or ""
            new_email = normalized.get("official_email")

            student.department_id = normalized["department_id"]
            student.program_id = normalized.get("program_id")
            student.last_name = normalized["last_name"]
            student.first_name = normalized["first_name"]
            student.middle_name = normalized.get("middle_name")
            student.official_email = new_email
            if verified_value is True:
                student.official_email_verified_at = student.official_email_verified_at or now
            elif verified_value is False:
                student.official_email_verified_at = None
            elif normalized.get("official_email_input_provided") and old_email != (new_email or ""):
                student.official_email_verified_at = None
            student.sex = normalized.get("sex")
            student.year_level = normalized.get("year_level")
            student.status = normalized["status"]
            student.is_active = bool(normalized.get("is_active", True))
            student.save(
                update_fields=[
                    "department",
                    "program",
                    "last_name",
                    "first_name",
                    "middle_name",
                    "official_email",
                    "official_email_verified_at",
                    "sex",
                    "year_level",
                    "status",
                    "is_active",
                    "updated_at",
                ]
            )
            normalized["_audit_action"] = "UPDATE"
            normalized["_audit_before"] = before
            normalized["_audit_after"] = cls._student_audit_snapshot(student)
            return "Student", student

        if action == cls.STUDENT_ROW_ACTION_UPDATE:
            raise ValidationError("student_no does not exist for this tenant/campus.")

        row = Student.objects.create(
            tenant_id=normalized["tenant_id"],
            campus_id=normalized["campus_id"],
            department_id=normalized["department_id"],
            program_id=normalized.get("program_id"),
            student_no=normalized["student_no"],
            last_name=normalized["last_name"],
            first_name=normalized["first_name"],
            middle_name=normalized.get("middle_name"),
            official_email=normalized.get("official_email"),
            official_email_verified_at=now if verified_value is True else None,
            sex=normalized.get("sex"),
            year_level=normalized.get("year_level"),
            status=normalized["status"],
            is_active=bool(normalized.get("is_active", True)),
        )
        normalized["_audit_action"] = "CREATE"
        normalized["_audit_after"] = cls._student_audit_snapshot(row)
        return "Student", row

    @classmethod
    def _create_section(cls, normalized: dict):
        program_id = normalized.get("program_id")
        if not program_id:
            auto_code = cls._normalize_value(normalized.get("program_code_auto"))
            if not auto_code:
                raise ValidationError("Unable to resolve program for section import row.")
            program, _ = Program.objects.get_or_create(
                tenant_id=normalized["tenant_id"],
                campus_id=normalized["campus_id"],
                department_id=normalized["department_id"],
                code=auto_code,
                defaults={
                    "name": auto_code,
                    "level": None,
                    "is_active": True,
                },
            )
            program_id = program.id

        row = Section.objects.create(
            tenant_id=normalized["tenant_id"],
            campus_id=normalized["campus_id"],
            department_id=normalized["department_id"],
            program_id=program_id,
            code=normalized["code"],
            name=normalized["name"],
            year_level=normalized.get("year_level"),
            is_active=bool(normalized.get("is_active", True)),
        )
        return "Section", row

    @classmethod
    def _resolve_or_create_section_for_offering(cls, normalized: dict) -> Section:
        section_id = normalized.get("section_id")
        if section_id:
            section = Section.objects.filter(id=section_id, is_active=True).first()
            if not section:
                raise ValidationError("Section reference is invalid or inactive.")
            return section

        section_payload = normalized.get("section_payload") or {}
        if not section_payload:
            raise ValidationError("Section reference is required.")

        program_id = section_payload.get("program_id")
        if not program_id:
            auto_code = cls._normalize_value(section_payload.get("program_code_auto"))
            if not auto_code:
                raise ValidationError("Unable to resolve program for auto-created section.")
            program, _created = Program.objects.get_or_create(
                tenant_id=section_payload["tenant_id"],
                campus_id=section_payload["campus_id"],
                department_id=section_payload["department_id"],
                code=auto_code,
                defaults={
                    "name": auto_code,
                    "level": None,
                    "is_active": True,
                },
            )
            if not program.is_active:
                raise ValidationError("Resolved program is inactive. Reactivate the program first.")
            program_id = program.id

        section, created = Section.objects.get_or_create(
            tenant_id=section_payload["tenant_id"],
            campus_id=section_payload["campus_id"],
            department_id=section_payload["department_id"],
            program_id=program_id,
            code=section_payload["code"],
            defaults={
                "name": section_payload.get("name") or section_payload["code"],
                "year_level": section_payload.get("year_level"),
                "is_active": bool(section_payload.get("is_active", True)),
            },
        )
        if not section.is_active:
            raise ValidationError("Section exists but is inactive. Reactivate the section first.")
        if created:
            normalized["section_auto_created"] = True
        return section

    @classmethod
    def _create_course_offering(cls, normalized: dict):
        section = cls._resolve_or_create_section_for_offering(normalized)
        if CourseOffering.objects.filter(
            tenant_id=normalized["tenant_id"],
            campus_id=normalized["campus_id"],
            department_id=normalized["department_id"],
            term_id=normalized["term_id"],
            course_id=normalized["course_id"],
            section_id=section.id,
        ).exists():
            raise ValidationError("Course offering already exists for tenant/campus/department/term/course/section.")

        row = CourseOffering.objects.create(
            tenant_id=normalized["tenant_id"],
            campus_id=normalized["campus_id"],
            department_id=normalized["department_id"],
            program_id=normalized.get("program_id") or section.program_id,
            academic_year_id=normalized["academic_year_id"],
            term_id=normalized["term_id"],
            course_id=normalized["course_id"],
            section_id=section.id,
            room=normalized.get("room"),
            schedule_text=normalized.get("schedule_text"),
            status=normalized["status"],
            is_active=bool(normalized.get("is_active", True)),
        )
        return "CourseOffering", row

    @classmethod
    def _create_faculty_assignment(cls, normalized: dict):
        row = FacultyAssignment.objects.create(
            offering_id=normalized["offering_id"],
            tenant_id=normalized.get("tenant_id"),
            campus_id=normalized.get("campus_id"),
            faculty_user_id=normalized["faculty_user_id"],
            is_primary=bool(normalized.get("is_primary", False)),
            is_active=bool(normalized.get("is_active", True)),
        )
        return "FacultyAssignment", row

    @classmethod
    def _resolve_faculty_user_confirmation_context(cls, *, normalized: dict, actor):
        tenant = Tenant.objects.filter(id=normalized.get("tenant_id"), is_active=True).first()
        campus = Campus.objects.filter(id=normalized.get("campus_id"), is_active=True).first()
        department = Department.objects.filter(id=normalized.get("department_id"), is_active=True).first()
        if not tenant or not campus or not department:
            raise ValidationError("Tenant, campus, or department is no longer active.")
        ScopedUserRoleAssignmentService._validate_scope(
            actor=actor,
            tenant=tenant,
            campus=campus,
            department=department,
        )
        FacultyAccountProvisioningService.resolve_active_faculty_role()
        email = FacultyAccountProvisioningService.validate_email_for_tenant(
            normalized.get("email"),
            tenant.id,
        )
        username = FacultyAccountProvisioningService.normalize_username(normalized.get("username"))
        email_user = User.objects.filter(email__iexact=email).order_by("id").first()
        username_user = User.objects.filter(username__iexact=username).order_by("id").first()
        return tenant, campus, department, email, username, email_user, username_user

    @classmethod
    def _create_or_skip_faculty_user(
        cls,
        *,
        normalized: dict,
        actor,
        batch: ImportBatch,
        batch_row: ImportBatchRow,
    ):
        tenant, campus, department, email, username, email_user, username_user = (
            cls._resolve_faculty_user_confirmation_context(normalized=normalized, actor=actor)
        )
        if normalized.get("skip_existing"):
            expected_user_id = normalized.get("existing_user_id")
            if (
                not email_user
                or not username_user
                or email_user.id != username_user.id
                or email_user.id != expected_user_id
                or not UserRole.objects.filter(
                    user=email_user,
                    role__code="FACULTY",
                    role__is_active=True,
                    is_active=True,
                    tenant=tenant,
                    campus=campus,
                    department=department,
                ).exists()
            ):
                raise ValidationError("Existing Faculty account no longer matches the validated identity and scope.")
            AuditService.log_event(
                action="FACULTY_IMPORT_ROW_SKIPPED",
                portal="ADMIN",
                entity_type="User",
                entity_id=email_user.id,
                actor=actor,
                tenant=tenant,
                campus=campus,
                metadata={
                    "department_id": department.id,
                    "import_batch_id": batch.id,
                    "import_row_number": batch_row.row_number,
                    "result_code": "SKIPPED_EXISTING",
                },
                request=None,
            )
            return "User", email_user, False, None

        if email_user or username_user:
            raise ValidationError("Email or username became unavailable after preview; manual review is required.")
        result = FacultyAccountProvisioningService.provision(
            actor=actor,
            tenant=tenant,
            campus=campus,
            department=department,
            first_name=normalized.get("first_name"),
            middle_name=normalized.get("middle_name"),
            last_name=normalized.get("last_name"),
            email=email,
            username=username,
            import_batch_id=batch.id,
            import_row_number=batch_row.row_number,
        )
        return "User", result.user, True, result.role_assignment

    @classmethod
    def _build_enrollment_confirmation_runtime(cls, *, candidate_rows, actor, request, batch):
        runtime_request = request
        if runtime_request is None:
            stored_scope = ((batch.metadata_json or {}).get("scope") or {})
            runtime_request = SimpleNamespace(scope=stored_scope)
        scope_runtime = cls._build_runtime(user=actor, request=runtime_request)
        offering_ids = {
            row.normalized_data_json.get("course_offering_id")
            for row in candidate_rows
            if isinstance(row.normalized_data_json, dict) and row.normalized_data_json.get("course_offering_id")
        }
        student_ids = {
            row.normalized_data_json.get("student_id")
            for row in candidate_rows
            if isinstance(row.normalized_data_json, dict) and row.normalized_data_json.get("student_id")
        }
        allowed_offerings = {
            offering.id: offering
            for offering in cls._allowed_offerings_for_runtime(scope_runtime)
            .filter(id__in=offering_ids)
            .select_related("section")
            .only(
                "id",
                "tenant_id",
                "campus_id",
                "academic_year_id",
                "term_id",
                "section_id",
                "section__id",
                "section__department_id",
                "section__year_level",
            )
        }
        students_by_id = Student.objects.filter(id__in=student_ids, is_active=True).in_bulk()
        existing_enrollment_keys = set(
            Enrollment.objects.filter(
                course_offering_id__in=offering_ids,
                student_id__in=student_ids,
            ).values_list("course_offering_id", "student_id")
        )
        return {
            "allowed_offerings": allowed_offerings,
            "students_by_id": students_by_id,
            "existing_enrollment_keys": existing_enrollment_keys,
        }

    @classmethod
    def _resolve_or_create_enrollment_student(
        cls,
        normalized: dict,
        *,
        actor,
        offering,
        confirmation_runtime=None,
    ):
        student_id = normalized.get("student_id")
        student_mode = normalized.get("student_mode") or cls.ENROLLMENT_STUDENT_MODE_STRICT
        student_no = cls._normalize_value(normalized.get("student_no"))
        tenant_id = offering.tenant_id
        campus_id = offering.campus_id
        offering_id = normalized.get("course_offering_id")

        if student_id:
            if confirmation_runtime is not None:
                student = confirmation_runtime["students_by_id"].get(student_id)
            else:
                student = Student.objects.filter(id=student_id, is_active=True).first()
            if not student:
                raise ValidationError("Student reference is invalid or inactive.")
            if student.tenant_id != tenant_id:
                raise ValidationError("Student and offering tenant no longer match.")
            if student.campus_id != campus_id:
                raise ValidationError("Student and offering campus no longer match.")
            return student

        if student_mode != cls.ENROLLMENT_STUDENT_MODE_AUTO_CREATE:
            raise ValidationError("Student does not exist and ENROLLMENT_STUDENT_MODE is STRICT_EXISTING.")

        if not student_no:
            raise ValidationError("student_no is required for AUTO_CREATE mode.")
        if not offering_id:
            raise ValidationError("Unable to auto-create student without a valid offering reference.")

        student = Student.objects.filter(
            tenant_id=tenant_id,
            campus_id=campus_id,
            student_no__iexact=student_no,
            is_active=True,
        ).first()
        if student:
            if campus_id and student.campus_id != campus_id:
                raise ValidationError("student_no campus does not match campus_code.")
            return student

        existing_inactive = Student.objects.filter(
            tenant_id=tenant_id,
            campus_id=campus_id,
            student_no__iexact=student_no,
            is_active=False,
        ).first()
        if existing_inactive:
            raise ValidationError("student_no exists but is inactive. Reactivate student first.")

        student_payload = normalized.get("student_payload") or {}
        last_name = cls._normalize_value(student_payload.get("last_name"))
        first_name = cls._normalize_value(student_payload.get("first_name"))
        if not last_name or not first_name:
            raise ValidationError(
                "student_last_name and student_first_name are required when AUTO_CREATE creates new students."
            )

        year_level = cls._normalize_value(student_payload.get("year_level")) or offering.section.year_level
        created_student = Student.objects.create(
            tenant_id=tenant_id,
            campus_id=campus_id or offering.campus_id,
            department_id=offering.section.department_id,
            # Do not auto-derive program from section; a section can contain mixed-program students.
            program_id=None,
            student_no=student_no,
            last_name=last_name,
            first_name=first_name,
            middle_name=cls._normalize_value(student_payload.get("middle_name")) or None,
            sex=cls._normalize_value(student_payload.get("sex")) or None,
            year_level=year_level or None,
            status=Student.Status.ACTIVE,
            is_active=True,
        )
        AuditService.log_event(
            action="CREATE",
            portal="ADMIN",
            entity_type="Student",
            entity_id=created_student.id,
            actor=actor,
            tenant=created_student.tenant_id,
            campus=created_student.campus_id,
            after_data={
                "student_no": created_student.student_no,
                "last_name": created_student.last_name,
                "first_name": created_student.first_name,
                "program_id": created_student.program_id,
                "source": "ENROLLMENT_IMPORT_AUTO_CREATE",
            },
            metadata={"source": "ENROLLMENT_IMPORT_AUTO_CREATE"},
            request=None,
        )
        return created_student

    @classmethod
    def _create_enrollment(cls, normalized: dict, *, actor, confirmation_runtime=None):
        offering_id = normalized["course_offering_id"]
        if confirmation_runtime is not None:
            offering = confirmation_runtime["allowed_offerings"].get(offering_id)
        else:
            offering = (
                CourseOffering.objects.select_related("section")
                .filter(id=offering_id, is_active=True)
                .first()
            )
        if offering is None:
            raise ValidationError("Offering is inactive or outside the current authorized scope.")
        for field_name, expected_value in (
            ("tenant_id", offering.tenant_id),
            ("campus_id", offering.campus_id),
            ("academic_year_id", offering.academic_year_id),
            ("term_id", offering.term_id),
        ):
            if normalized.get(field_name) != expected_value:
                raise ValidationError("Offering scope changed after preview; upload and validate the file again.")
        student = cls._resolve_or_create_enrollment_student(
            normalized,
            actor=actor,
            offering=offering,
            confirmation_runtime=confirmation_runtime,
        )
        enrollment_key = (offering_id, student.id)
        if confirmation_runtime is not None:
            duplicate_exists = enrollment_key in confirmation_runtime["existing_enrollment_keys"]
        else:
            duplicate_exists = Enrollment.objects.filter(
                course_offering_id=offering_id,
                student=student,
            ).exists()
        if duplicate_exists:
            raise ValidationError("Enrollment already exists for this student and offering.")

        row = Enrollment.objects.create(
            tenant_id=offering.tenant_id,
            campus_id=offering.campus_id,
            academic_year_id=offering.academic_year_id,
            term_id=offering.term_id,
            student=student,
            course_offering_id=normalized["course_offering_id"],
            enrollment_status=normalized["enrollment_status"],
            encoded_by_user=actor,
            encoded_via_portal=Enrollment.SourcePortal.ADMIN,
            is_active=bool(normalized.get("is_active", True)),
        )
        if confirmation_runtime is not None:
            confirmation_runtime["existing_enrollment_keys"].add(enrollment_key)
        return "Enrollment", row

    @classmethod
    def _audit_import_row_write(
        cls,
        *,
        batch: ImportBatch,
        batch_row: ImportBatchRow,
        actor,
        entity_type: str,
        entity_obj,
        normalized: dict,
    ):
        tenant_id = getattr(entity_obj, "tenant_id", None) or normalized.get("tenant_id")
        campus_id = getattr(entity_obj, "campus_id", None) or normalized.get("campus_id")
        entity_id = getattr(entity_obj, "id", None)
        normalized_for_audit = {key: value for key, value in normalized.items() if not str(key).startswith("_audit_")}
        AuditService.log_event(
            action=normalized.get("_audit_action") or "CREATE",
            portal="ADMIN",
            entity_type=entity_type,
            entity_id=entity_id,
            actor=actor,
            tenant=tenant_id,
            campus=campus_id,
            before_data=normalized.get("_audit_before"),
            after_data={
                "import_type": batch.import_type,
                "entity_id": entity_id,
                "normalized": normalized_for_audit,
                "result": normalized.get("_audit_after"),
            },
            metadata={
                "source": "BULK_IMPORT_CONFIRM",
                "batch_id": batch.id,
                "row_number": batch_row.row_number,
                "import_type": batch.import_type,
            },
            request=None,
        )

    @classmethod
    def _create_from_row(cls, import_type: str, normalized: dict, *, actor, confirmation_runtime=None):
        if import_type == ImportBatch.ImportType.SECTIONS:
            return cls._create_section(normalized)
        if import_type == ImportBatch.ImportType.COURSES:
            return cls._create_course(normalized)
        if import_type == ImportBatch.ImportType.STUDENTS:
            return cls._create_or_update_student(normalized)
        if import_type == ImportBatch.ImportType.COURSE_OFFERINGS:
            return cls._create_course_offering(normalized)
        if import_type == ImportBatch.ImportType.FACULTY_ASSIGNMENTS:
            return cls._create_faculty_assignment(normalized)
        if import_type == ImportBatch.ImportType.ENROLLMENT:
            return cls._create_enrollment(
                normalized,
                actor=actor,
                confirmation_runtime=confirmation_runtime,
            )
        raise ValidationError("Unsupported import type.")

    @classmethod
    def confirm_batch(
        cls,
        *,
        batch: ImportBatch,
        actor,
        send_invitation_emails: bool = False,
        request=None,
    ):
        if batch.status == ImportBatch.Status.CONFIRMED:
            raise ValidationError("This batch is already confirmed.")
        faculty_user_import = batch.import_type == ImportBatch.ImportType.FACULTY_USERS
        email_system_enabled = bool(getattr(settings, "FACULTY_IMPORT_EMAIL_ENABLED", False))
        email_requested = bool(send_invitation_emails and email_system_enabled)
        if faculty_user_import and not PermissionService.has_permission(
            actor,
            "faculty_users.import",
            tenant_id=batch.tenant_id,
            campus_id=batch.campus_id,
        ):
            raise PermissionDenied("You do not have permission to confirm this Faculty user import.")
        if faculty_user_import and email_requested and not PermissionService.has_permission(
            actor,
            "faculty_users.send_import_invitations",
            tenant_id=batch.tenant_id,
            campus_id=batch.campus_id,
        ):
            raise ValidationError("You do not have permission to send faculty import invitations.")
        if faculty_user_import:
            batch.email_system_enabled_snapshot = email_system_enabled
            batch.send_invitation_emails_requested = email_requested
            batch.save(
                update_fields=[
                    "email_system_enabled_snapshot",
                    "send_invitation_emails_requested",
                    "updated_at",
                ]
            )
        candidate_rows = list(batch.rows.filter(row_status=ImportBatchRow.RowStatus.VALID).order_by("row_number"))
        if not candidate_rows:
            raise ValidationError("No valid rows available for import.")
        enrollment_confirmation_runtime = None
        if batch.import_type == ImportBatch.ImportType.ENROLLMENT:
            enrollment_confirmation_runtime = cls._build_enrollment_confirmation_runtime(
                candidate_rows=candidate_rows,
                actor=actor,
                request=request,
                batch=batch,
            )

        imported_count = 0
        failed_count = 0
        for row in candidate_rows:
            normalized = row.normalized_data_json or {}
            faculty_created = False
            role_assignment = None
            try:
                with transaction.atomic():
                    if faculty_user_import:
                        entity_type, entity_obj, faculty_created, role_assignment = cls._create_or_skip_faculty_user(
                            normalized=normalized,
                            actor=actor,
                            batch=batch,
                            batch_row=row,
                        )
                    else:
                        entity_type, entity_obj = cls._create_from_row(
                            batch.import_type,
                            normalized,
                            actor=actor,
                            confirmation_runtime=enrollment_confirmation_runtime,
                        )
                    if not faculty_user_import:
                        cls._audit_import_row_write(
                            batch=batch,
                            batch_row=row,
                            actor=actor,
                            entity_type=entity_type,
                            entity_obj=entity_obj,
                            normalized=normalized,
                        )
            except (ValidationError, PermissionDenied, IntegrityError, ValueError) as exc:
                row_errors = list(row.errors_json or [])
                row_errors.append(f"Import failed: {exc}")
                row.row_status = ImportBatchRow.RowStatus.ERROR
                row.errors_json = row_errors
                row.result_code = "FAILED_PROVISIONING" if faculty_user_import else row.result_code
                row.save(update_fields=["row_status", "errors_json", "result_code", "updated_at"])
                if faculty_user_import:
                    AuditService.log_event(
                        action="FACULTY_IMPORT_ROW_FAILED",
                        portal="ADMIN",
                        entity_type="ImportBatchRow",
                        entity_id=row.id,
                        actor=actor,
                        tenant=batch.tenant,
                        campus=batch.campus,
                        metadata={
                            "import_batch_id": batch.id,
                            "import_row_number": row.row_number,
                            "result_code": "FAILED_PROVISIONING",
                            "error_type": type(exc).__name__,
                        },
                        request=request,
                    )
                failed_count += 1
                continue

            row.row_status = ImportBatchRow.RowStatus.IMPORTED
            row.imported_entity_type = entity_type
            row.imported_entity_id = str(getattr(entity_obj, "id", ""))
            row.errors_json = None
            if faculty_user_import:
                if not faculty_created:
                    row.result_code = "SKIPPED_EXISTING"
                    row.result_metadata_json = {"user_id": entity_obj.id}
                elif not email_system_enabled:
                    invitation = FacultyInvitationService.record_without_delivery(
                        user=entity_obj,
                        actor=actor,
                        originating_import_row=row,
                        status=FacultyInvitation.Status.DISABLED_BY_SYSTEM,
                    )
                    row.result_code = "CREATED_EMAIL_DISABLED"
                    row.result_metadata_json = {
                        "user_id": entity_obj.id,
                        "role_assignment_id": role_assignment.id if role_assignment else None,
                        "invitation_id": invitation.id,
                        "invitation_status": invitation.status,
                    }
                elif not email_requested:
                    invitation = FacultyInvitationService.record_without_delivery(
                        user=entity_obj,
                        actor=actor,
                        originating_import_row=row,
                        status=FacultyInvitation.Status.NOT_REQUESTED,
                    )
                    row.result_code = "CREATED_INVITATION_NOT_REQUESTED"
                    row.result_metadata_json = {
                        "user_id": entity_obj.id,
                        "role_assignment_id": role_assignment.id if role_assignment else None,
                        "invitation_id": invitation.id,
                        "invitation_status": invitation.status,
                    }
                else:
                    try:
                        delivery = FacultyInvitationService.send_or_resend(
                            user=entity_obj,
                            actor=actor,
                            originating_import_row=row,
                            request=request,
                            resend=False,
                        )
                    except (ValidationError, PermissionDenied) as exc:
                        invitation = FacultyInvitationService.record_without_delivery(
                            user=entity_obj,
                            actor=actor,
                            originating_import_row=row,
                            status=FacultyInvitation.Status.FAILED,
                        )
                        invitation.failure_reason = type(exc).__name__
                        invitation.save(update_fields=["failure_reason", "updated_at"])
                        row.result_code = "CREATED_INVITATION_FAILED"
                        row.result_metadata_json = {
                            "user_id": entity_obj.id,
                            "role_assignment_id": role_assignment.id if role_assignment else None,
                            "invitation_id": invitation.id,
                            "invitation_status": invitation.status,
                        }
                    else:
                        row.result_code = (
                            "CREATED_INVITATION_SENT" if delivery.sent else "CREATED_INVITATION_FAILED"
                        )
                        row.result_metadata_json = {
                            "user_id": entity_obj.id,
                            "role_assignment_id": role_assignment.id if role_assignment else None,
                            "invitation_id": delivery.invitation.id,
                            "invitation_status": delivery.invitation.status,
                        }
            row.save(
                update_fields=[
                    "row_status",
                    "imported_entity_type",
                    "imported_entity_id",
                    "errors_json",
                    "result_code",
                    "result_metadata_json",
                    "updated_at",
                ]
            )
            imported_count += 1

        batch.confirmed_by_user = actor
        batch.confirmed_at = timezone.now()
        batch.imported_rows = (batch.imported_rows or 0) + imported_count
        batch.invalid_rows = batch.rows.filter(row_status=ImportBatchRow.RowStatus.ERROR).count()
        batch.valid_rows = batch.rows.filter(row_status=ImportBatchRow.RowStatus.VALID).count()
        batch.status = ImportBatch.Status.CONFIRMED if failed_count == 0 else ImportBatch.Status.CONFIRM_FAILED
        batch.error_summary_json = cls._build_error_summary(
            list(batch.rows.filter(row_status=ImportBatchRow.RowStatus.ERROR)),
            extra_messages=[f"Imported rows: {imported_count}", f"Failed rows: {failed_count}"],
        )
        batch.save(
            update_fields=[
                "confirmed_by_user",
                "confirmed_at",
                "imported_rows",
                "invalid_rows",
                "valid_rows",
                "status",
                "error_summary_json",
                "updated_at",
            ]
        )
        return batch

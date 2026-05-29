from __future__ import annotations

import os
import sys
from collections import defaultdict
from pathlib import Path

import django


BASE_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("DJANGO_ENV", os.getenv("DJANGO_ENV", "local"))
django.setup()

from django.apps import apps  # noqa: E402
from django.db import models  # noqa: E402


MODEL_PURPOSES = {
    "accounts.User": "Primary application user account for Admin Portal and Faculty Portal access.",
    "accounts.PortalLoginLockoutState": "Tracks repeated failed login attempts and temporary portal-specific lockout state.",
    "accounts.UserSignatureCredential": "Encrypted account-level signature image credential for approved printable documents.",
    "accounts.UserSignatureUsageLog": "Audit trail for stored signature placement on official generated documents.",
    "rbac.Role": "Role catalog used for RBAC and scoped governance.",
    "rbac.Permission": "Permission catalog used for action-level access checks.",
    "rbac.UserRole": "Scoped assignment of a role to a user, optionally limited by tenant, campus, or department.",
    "rbac.RolePermission": "Maps permissions to roles.",
    "rbac.UserPermission": "Direct per-user permission grants or overrides.",
    "auditlog.AuditLog": "Audit trail for sensitive portal actions and governance decisions.",
    "navigation.MenuGroup": "Top-level portal menu grouping.",
    "navigation.MenuItem": "Portal navigation item, including nested/sidebar structure.",
    "navigation.MenuItemPermission": "Permission requirement mapping for menu visibility.",
    "tenants.Tenant": "Top-level institution or tenant record.",
    "tenants.Campus": "Campus under a tenant.",
    "tenants.Department": "Department under a tenant and campus.",
    "tenants.Program": "Academic program under a tenant/campus/department.",
    "tenants.SystemSetting": "Tenant-scoped key/value setting store used by configurable features and governance.",
    "academics.AcademicYear": "Academic year master record.",
    "academics.Term": "Academic term or semester within an academic year.",
    "academics.TenantTermGradingPeriod": "Canonical grading period catalog per tenant and term, separate from template period codes.",
    "academics.ActiveGradingPeriodSetting": "Current active grading period per tenant/campus/term, used for governance and faculty access rules.",
    "academics.Course": "Course or subject master record.",
    "academics.Section": "Section/class grouping for students.",
    "academics.CourseOffering": "Concrete class offering for a course, section, term, and campus.",
    "academics.FacultyAssignment": "Faculty load assignment to an offering, including acceptance workflow and reminder state.",
    "students.Student": "Student master record scoped by tenant/campus/department.",
    "enrollment.Enrollment": "Enrollment record linking a student to a course offering.",
    "imports.ImportBatch": "Bulk import batch header.",
    "imports.ImportBatchRow": "Row-level result for a bulk import batch.",
    "grading.GradingTemplate": "Top-level grading template definition.",
    "grading.GradingTemplateApprovalWorkflow": "Workflow header for template sequential approval.",
    "grading.GradingTemplateApprovalStep": "Step-by-step record for a template approval workflow.",
    "grading.GradingTemplatePeriod": "Template period, such as prelim or midterm, under a grading template.",
    "grading.GradingTemplateComponent": "Top-level weighted grading component within a template period.",
    "grading.GradingTemplateSubcomponent": "Nested weighted subcomponent under a component.",
    "grading.GradingTemplateDetail": "Lowest grading detail or activity grouping node under a subcomponent/component.",
    "grading.CourseTemplateAssignment": "Assignment of a grading template to a course, optionally term-scoped.",
    "grading.CourseBaseValueOverride": "Course-specific base value override for grade computation.",
    "grading.TenantGradingProfile": "Tenant/campus/department grading defaults such as passing threshold.",
    "grading.TemplateHotfixRequest": "Hotfix request for published grading templates.",
    "grading.TemplateHotfixWorkflowStep": "Sequential workflow step for template hotfix review/apply.",
    "grading.GradeActivity": "Faculty-created graded activity under a class offering and template period.",
    "grading.StudentActivityScore": "Student raw score or computed score entry for a grade activity.",
    "grading.StudentPeriodGrade": "Computed class-standing, exam, and period grade summary per student and period.",
    "grading.StudentFinalGrade": "Computed final grade summary per student and offering.",
    "grading.GradingPeriodLock": "Admin period lock/deadline rule for submission governance.",
    "grading.GradeSubmission": "Faculty submission snapshot for a class-period gradebook.",
    "grading.GradeSubmissionReopenRequest": "Request to reopen a submitted gradebook period.",
    "grading.CorrectionApprovalRouteRule": "Department-sensitive approval-route rule for grade correction requests.",
    "grading.GradeCorrectionRequest": "Grade correction petition header.",
    "grading.GradeCorrectionApprovalStep": "Approval chain step for a correction request.",
    "grading.GradeCorrectionRequestItem": "Specific correction item inside a correction request.",
    "grading.GradeCorrectionAttachment": "Supporting file attached to a correction request.",
    "grading.GradeCorrectionUnlockWindow": "Governed manual unlock window for approved correction follow-up.",
    "attendance.AttendanceSession": "Attendance session under a class offering and period.",
    "attendance.AttendanceRecord": "Attendance entry for a student in one attendance session.",
    "notifications.FacultyReminder": "Faculty reminder center item for deadlines, activities, or workflow follow-up.",
    "notifications.FacultyReminderEmailQueue": "Queued outbound email for a faculty reminder.",
    "notifications.FacultyMemo": "Private faculty memo/note linked to a class or student.",
    "notifications.NotificationQueue": "Generic queued notification record.",
    "predictions.PredictionSettingSnapshot": "Snapshot of prediction assumptions used to compute unofficial projections.",
    "predictions.PredictionSnapshot": "Per-student unofficial prediction snapshot for one offering and period.",
    "predictions.PredictionSummarySnapshot": "Aggregated class-period prediction summary.",
    "predictions.PredictionDirtyQueue": "Queue of impacted records needing prediction recomputation.",
    "predictions.PredictionWhatIfDraft": "Saved what-if simulation draft for a user/class/period.",
    "predictions.PredictionViewLog": "Audit log for prediction page access.",
    "auth.Group": "Django built-in auth group table.",
    "auth.Permission": "Django built-in permission table.",
    "contenttypes.ContentType": "Django content-type registry table.",
    "sessions.Session": "Django session store.",
    "admin.LogEntry": "Django admin action log table.",
}


FIELD_EXPLANATIONS = {
    "id": "Primary key for the table.",
    "created_at": "Timestamp when the row was created.",
    "updated_at": "Timestamp when the row was last updated.",
    "is_active": "Active/inactive flag used for soft operational control.",
    "tenant": "Owning tenant scope for the record.",
    "campus": "Owning or effective campus scope for the record.",
    "department": "Owning or effective department scope for the record.",
    "program": "Owning or effective academic program scope for the record.",
    "user": "Related user account.",
    "faculty_user": "Faculty user assigned to the record.",
    "student": "Related student record.",
    "offering": "Related course offering/class record.",
    "template_period": "Related grading-template period record.",
    "period": "Canonical grading period selected for the term/campus setting.",
    "term": "Academic term for the record.",
    "academic_year": "Academic year for the record.",
    "requested_by_user": "User who created the request.",
    "reviewed_by_user": "User who reviewed or decided the request.",
    "submitted_by_user": "User who submitted the record.",
    "set_by_user": "User who set the active governance value.",
    "remarks": "Free-text remarks or notes for operational context.",
    "status": "Workflow or operational status code.",
    "code": "Short code used as an operational identifier.",
    "name": "Human-readable name or label.",
    "title": "Human-readable title.",
    "justification": "Reason supplied to support the request or decision.",
    "route_name": "Django route name captured for audit or navigation tracking.",
    "metadata_json": "Flexible JSON payload for extra metadata.",
    "before_json": "JSON snapshot before a change.",
    "after_json": "JSON snapshot after a change.",
    "setting_key": "System-setting key name.",
    "setting_value": "Stored value for the setting key.",
    "value_type": "Type hint used to interpret the stored setting value.",
}


def model_label(model: type[models.Model]) -> str:
    return f"{model._meta.app_label}.{model.__name__}"


def explain_field(field: models.Field) -> str:
    if field.name in FIELD_EXPLANATIONS:
        return FIELD_EXPLANATIONS[field.name]
    if field.is_relation and getattr(field, "related_model", None):
        target = field.related_model
        return f"Foreign-key reference to `{target._meta.app_label}.{target.__name__}`."
    if field.get_internal_type() == "JSONField":
        return "Flexible JSON payload used for variable structured data."
    if field.get_internal_type() in {"DateTimeField", "DateField"}:
        return "Date/time value used by the workflow or record."
    if field.get_internal_type() in {"CharField", "TextField"}:
        return "Text value used by the workflow or record."
    if field.get_internal_type() in {"BooleanField"}:
        return "Boolean flag used by the workflow or record."
    if field.get_internal_type() in {"DecimalField", "FloatField", "IntegerField", "PositiveIntegerField", "PositiveSmallIntegerField"}:
        return "Numeric value used by the workflow or computation."
    return "Application field used by teachermateplus."


def field_relationship(field: models.Field) -> str:
    if field.is_relation and getattr(field, "related_model", None):
        target = field.related_model
        return f"`{target._meta.db_table}` (`{target._meta.app_label}.{target.__name__}`)"
    return "-"


def unique_summary(opts) -> list[str]:
    items: list[str] = []
    if opts.unique_together:
        for group in opts.unique_together:
            items.append(", ".join(group))
    for constraint in opts.constraints:
        fields = getattr(constraint, "fields", None)
        if fields:
            items.append(", ".join(fields))
    return items


def model_section(model: type[models.Model]) -> str:
    opts = model._meta
    label = model_label(model)
    purpose = MODEL_PURPOSES.get(label, "teachermateplus application table.")
    outgoing = []
    for field in opts.concrete_fields:
        if field.is_relation and getattr(field, "related_model", None):
            target = field.related_model
            outgoing.append(f"- `{field.name}` -> `{target._meta.db_table}` (`{target._meta.app_label}.{target.__name__}`)")
    if opts.local_many_to_many:
        for m2m in opts.local_many_to_many:
            target = m2m.related_model
            outgoing.append(f"- `{m2m.name}` -> many-to-many with `{target._meta.db_table}` (`{target._meta.app_label}.{target.__name__}`)")
    if not outgoing:
        outgoing = ["- No outgoing foreign-key or many-to-many relationships."]

    uniques = unique_summary(opts)
    if not uniques:
        uniques = ["- No explicit unique constraint metadata beyond primary keys and field-level `unique=True` flags."]
    else:
        uniques = [f"- `{item}`" for item in uniques]

    lines = [
        f"### `{opts.db_table}`",
        "",
        f"- **Model:** `{label}`",
        f"- **Purpose:** {purpose}",
        "",
        "**Relationships**",
        *outgoing,
        "",
        "**Unique / Structural Notes**",
        *uniques,
        "",
        "| Field | Django Type | Null | Blank | PK | Relationship | Explanation |",
        "|---|---|---:|---:|---:|---|---|",
    ]
    for field in opts.concrete_fields:
        lines.append(
            "| `{}` | `{}` | `{}` | `{}` | `{}` | {} | {} |".format(
                field.name,
                field.get_internal_type(),
                "Yes" if getattr(field, "null", False) else "No",
                "Yes" if getattr(field, "blank", False) else "No",
                "Yes" if field.primary_key else "No",
                field_relationship(field),
                explain_field(field),
            )
        )
    if opts.local_many_to_many:
        for m2m in opts.local_many_to_many:
            target = m2m.related_model
            lines.append(
                "| `{}` | `ManyToManyField` | `-` | `-` | `No` | `{}` | {} |".format(
                    m2m.name,
                    f"`{target._meta.db_table}` (`{target._meta.app_label}.{target.__name__}`)",
                    "Many-to-many relationship managed through an intermediate table.",
                )
            )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    grouped: dict[str, list[type[models.Model]]] = defaultdict(list)
    for model in apps.get_models():
        opts = model._meta
        if opts.proxy or not opts.managed:
            continue
        grouped[opts.app_label].append(model)

    app_order = [
        "accounts",
        "rbac",
        "auditlog",
        "navigation",
        "tenants",
        "academics",
        "students",
        "enrollment",
        "imports",
        "grading",
        "attendance",
        "notifications",
        "predictions",
        "admin",
        "auth",
        "contenttypes",
        "sessions",
    ]

    sections = [
        "# teachermateplus Database Schema Dictionary",
        "",
        "This document is generated from the current Django model registry so it reflects the actual teachermateplus schema at generation time.",
        "",
        "## Notes",
        "",
        "- **Django Type** shows the Django field class used by the model.",
        "- Production MySQL/MariaDB column types may differ slightly at the storage level from Django field names.",
        "- Relationship targets are shown using both the database table name and the Django model label.",
        "- This dictionary includes both teachermateplus application tables and the small number of built-in Django framework tables used by the project.",
        "",
        "## Application Areas",
        "",
        "- **Security and access:** accounts, RBAC, audit, navigation",
        "- **Institution structure:** tenants, campuses, departments, programs, terms",
        "- **Operations:** course offerings, faculty assignments, enrollments, imports",
        "- **Grading:** templates, activities, scores, summaries, submissions, correction governance",
        "- **Support workflows:** attendance, notifications, reminders, prediction snapshots",
        "",
    ]

    for app_label in app_order:
        models_for_app = grouped.get(app_label)
        if not models_for_app:
            continue
        sections.append(f"## `{app_label}`")
        sections.append("")
        for model in sorted(models_for_app, key=lambda m: m._meta.db_table):
            sections.append(model_section(model))

    output_path = BASE_DIR / "docs" / "DB_SCHEMA.md"
    output_path.write_text("\n".join(sections), encoding="utf-8")
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()

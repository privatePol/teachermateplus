# CHANGE_LOG.md

All notable changes to **EduGradesPro V1** should be documented in this file.

This project follows a practical changelog format inspired by Keep a Changelog.

## [Unreleased]

### Added
- Faculty public landing page with top-nav login, workflow/features/integration/support sections.
- Faculty guide and help-center style content linked from the faculty portal help icon (`?`).
- CSV import framework with strict template matching and preview/confirm flow.
- Admin tools grouping and bulk import screens.
- KPI cards in faculty dashboard (submission/readiness-oriented metrics).
- Gradebook print action in faculty period summary.
- Ubuntu production deployment package:
  - `docs/DEPLOYMENT_UBUNTU.md`
  - `ops/systemd/edugradespro-gunicorn.service`
  - `ops/nginx/edugradespro.conf`
  - `ops/cron/edugradespro.cron`
  - `ops/scripts/deploy_release.sh`
- Threshold-aware admin grading analytics:
  - failed students by period
  - failed students by component
  - failed students by detail
  using profile/tenant passing threshold policy.
- `TenantGradingProfile.passing_grade_threshold` for profile-level passing threshold governance.
- Faculty-centric admin analytics views:
  - top 10 class fail-rate rows per campus (pass/fail ratio per class + faculty)
  - top 10 teacher comparisons for same course with higher fail rates.
- Correction approval route matrix:
  - tenant default + faculty-department-specific routing
  - one-step or two-step approval path
  - step-level reviewer enforcement by role and optional same-department matching
  - CAO-final route now configurable via governance matrix.
- Step-tracked correction reviews:
  - intermediate approvals keep request in `PENDING`
  - final approval auto-applies approved score corrections to the gradebook
  - rejection marks remaining steps as skipped.
- User scope enhancement: `default_department` added to user profile for faculty-department governance routing.
- Correction approval final-window governance:
  - legacy correction-window approvals still use a fixed 24-hour validity window when applicable
  - expired approved windows auto-lapse to `LAPSED` status
  - lapsed requests require faculty to file a new correction request.
- New cron-capable command:
  - `manage.py auto_lapse_correction_windows`
  to enforce automatic correction-window lapse.
- New correction governance switch:
  - `CORRECTION_MODE = MANUAL_ONLY | SYSTEM_REQUEST`
  to support paper-based correction governance when required by registrar/academic policy.
- Dedicated admin configurable-features page for optional workflow settings, including correction official report and registrar auto-email groundwork.
- Official correction PDF generation for approved correction requests:
  - registrar-ready printable/exportable report
  - includes campus where the correction applies, approval summary, and a registrar-facing cross-tab of official grade original vs corrected values
  - detailed correction items remain available inside EduGradesPro instead of the registrar PDF
  - available through faculty and admin correction screens when feature-enabled.
- Configurable correction submission approval email:
  - when enabled, faculty correction request submission emails selected approval roles such as CAO and college dean
  - notification uses an NCBA-branded card email without exposing the faculty name
  - recipient roles are managed from Configurable Features.
- Registrar auto-email is now wired after final approval:
  - sends the official correction PDF attachment to campus-specific or default registrar recipients
  - approval notification recipient resolution now supports tenant-wide role assignments correctly.
- Admin security now includes role creation from the UI, not just permission management for existing roles.
- New role creation now supports optional permission-copying from an existing role to speed up setup.
- Admin security role management now includes an edit screen so roles can be activated/deactivated directly in the UI.
- Department-scoped role assignments in RBAC:
  - `UserRole` now supports optional `department` scope in addition to tenant/campus
  - role assignment UI now allows assigning role by tenant + campus + department
  - assignment listing now shows department scope.
- SIS periodic grades API campus-integration guardrails:
  - if `student_no` is provided, `campus_code` and `section_code` are now required
  - if `section_code` is provided, `campus_code` is now required
  - API docs now include the campus + section + student pull pattern for separate campus SIS/AIMS servers.

### Changed
- Comparison section wording updated to neutral term: **Standalone Grade Files / Spreadsheets**.
- Grading builder UX refined to better expose hierarchy and readability.
- Faculty course/summary UI refinements (button labels, layout polish, grouped details).
- Course offering UI label updated from `Room` to `Room/Office/Lab` (admin offerings screens and faculty course cards) for broader facility naming consistency.
- Security and consent flow enhancements (first-login behavior, privacy consent integration points).
- Admin analytics fail/pass logic now uses:
  `profile threshold -> tenant PASSING_GRADE_THRESHOLD -> 75.00`.
- Admin and Faculty guide pages were updated with section-specific `portal-img` assets and clearer procedural "How To" blocks for operations.
- Faculty guide workflow layout refined: the recommended workflow image now appears inside the main "Recommended Daily Faculty Workflow" container, and the duplicate side card was removed.
- Correction governance page is now a dedicated tools screen with:
  - pre-deadline correction policy selector
  - approval route matrix editor (department-based).
- Faculty correction UI/routes now respect `CORRECTION_MODE`:
  - `MANUAL_ONLY` hides/blocks in-portal correction requests and faculty self-reopen
  - `SYSTEM_REQUEST` keeps the existing correction request workflow active.
- Score correction requests now support:
  - multiple students or entire-class selection
  - multiple grading items in one request
  - auto-loaded original values with faculty entering corrected values only
  - automatic gradebook update + recomputation on final approval without faculty re-encoding.
- Optional feature behavior is now being organized into a dedicated configurable-features page with global toggles and role-aware groundwork instead of hard-enabled workflow additions.
- Official correction PDF access now opens inline in a new browser tab for preview/print workflow before optional manual download.
- Faculty "My Correction Requests" scope formatting improved:
  - student names are emphasized (bold)
  - original -> corrected grade values are highlighted in blue for quicker visual scanning.
- Admin scope filtering now supports department-aware governance for faculty-assignment visibility:
  - AC/Dean/CAO scoped role assignments can now constrain faculty-assignment views to their assigned departments/areas.
- Official correction PDF layout updated for registrar operations:
  - `Approval Summary` now appears directly above `Registrar Reference`
  - `Supporting Attachments` section removed from the printable registrar PDF (attachments remain in-system).
  - NCBA logo added at the top of the document
  - section headings now follow left-aligned bullet format:
    `A. Academic Context`, `B. Justification and Remarks`, `C. Official Grade Summary`, `D. Approval Summary`, `E. Registrar Reference`
  - section spacing increased for clearer readability
  - `Tenant` field removed from academic-context table.
- Student identity uniqueness is now campus-scoped:
  - students can now share the same `student_no` across different campuses of the same tenant
  - enrollment import student resolution and auto-create paths now resolve by `tenant + campus + student_no`.

### Fixed
- Multiple grading summary/runtime errors reported during faculty usage:
  - missing `_round` reference
  - `ROUND_HALF_UP` import issue
  - `timezone` import issue in summary view path
- Table layout/formatting issues in faculty frontpage comparison section.
- Reopen and deadline-related visibility inconsistencies across admin/faculty screens.
- Filtering and selection behavior improvements across enrollment and assignment workflows.
- Correction queue/review refinements:
  - review scope now shows student number with student name for clearer approver validation
  - pending requests that were created with fallback routing can now be reconciled to the configured approval route safely before review
  - review guard message now uses readable approver labels instead of raw role codes.
- Correction email notification presentation updates:
  - submission and registrar notification cards now use NCBA green styling and include petitioner name
  - period label in email notifications now uses period name only (no internal period code)
  - inline logo embedding improved for better logo rendering across mail clients.

## [0.75] - Bulk Import Stabilization

### Added
- Import types:
  - courses
  - course offerings
  - faculty assignments
  - enrollment
  - sections (operational extension)
- Dynamic downloadable templates per import type.
- Header enforcement (exact order and naming).
- Row-level validation and error reporting.
- Batch tracking and import audit hooks.

### Governance
- Admin portal only for import operations.
- Permission-controlled access to import actions and batch visibility.

## [0.7] - Governance and Workflow Controls

### Added
- Template hotfix workflow foundations.
- Approval/governance fields and status indicators.
- Grade submission lock/reopen controls and policy handling improvements.

## [0.5] - Grading Core Expansion

### Added
- Grading templates, periods, components, subcomponents, details.
- Builder-oriented template management flow.
- Faculty activity creation, score encoding, attendance and summary views.

---

## Update Instructions
For every merged change:
1. Add a bullet under `[Unreleased]`.
2. Move `[Unreleased]` items into a version heading when releasing.
3. Include impact in plain language (what changed for admins/faculty).

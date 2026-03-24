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
  - final approval opens unlock window
  - rejection marks remaining steps as skipped.
- User scope enhancement: `default_department` added to user profile for faculty-department governance routing.
- Correction approval final-window governance:
  - final approval now opens a fixed 24-hour correction window
  - expired approved windows auto-lapse to `LAPSED` status
  - lapsed requests require faculty to file a new correction request.
- New cron-capable command:
  - `manage.py auto_lapse_correction_windows`
  to enforce automatic correction-window lapse.
- New correction governance switch:
  - `CORRECTION_MODE = MANUAL_ONLY | SYSTEM_REQUEST`
  to support paper-based correction governance when required by registrar/academic policy.

### Changed
- Comparison section wording updated to neutral term: **Standalone Grade Files / Spreadsheets**.
- Grading builder UX refined to better expose hierarchy and readability.
- Faculty course/summary UI refinements (button labels, layout polish, grouped details).
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

### Fixed
- Multiple grading summary/runtime errors reported during faculty usage:
  - missing `_round` reference
  - `ROUND_HALF_UP` import issue
  - `timezone` import issue in summary view path
- Table layout/formatting issues in faculty frontpage comparison section.
- Reopen and deadline-related visibility inconsistencies across admin/faculty screens.
- Filtering and selection behavior improvements across enrollment and assignment workflows.

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

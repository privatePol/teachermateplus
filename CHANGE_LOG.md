# CHANGE_LOG.md

All notable changes to **EduGradesPro V1** should be documented in this file.

This project follows a practical changelog format inspired by Keep a Changelog.

## [Unreleased]

### Added
- Faculty assignment acceptance workflow:
  - faculty must accept newly assigned classes before opening grading or class-list screens
  - acceptance stores both `accepted_at` and `accepted_by` on the faculty assignment row
  - faculty `My Courses` now surfaces pending assignments with direct accept actions.
- Faculty assignment response workflow:
  - admin can attach assignment instructions/notes to a faculty load
  - faculty can request clarification or decline with a written note
  - admin faculty-assignment view now shows response status, admin instructions, and faculty response notes.
- Faculty assignment reminder/expiry workflow:
  - pending assignments now track a response due date, reminder count, and last reminder timestamp
  - overdue pending assignments automatically move to `EXPIRED`
  - a new command `manage.py process_faculty_assignment_reminders` can queue reminder notifications and expire overdue loads
  - admin and faculty pages now surface due-soon and expired assignment status directly in the UI.
- Admin assignment operations expanded further:
  - expired faculty loads now support a one-click `Renew Response Window` action from Faculty Assignments
  - a new Faculty Assignment Dashboard summarizes assignment status across campus, department, and faculty
  - admin dashboard and faculty public homepage now surface help cards that explain the assignment-acceptance rule before users open the full guides.
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
- Admin faculty gradebook monitor:
  - AC/Dean/CAO can open a read-only gradebook monitor for scoped faculty members
  - monitor includes per-class metric cards and masked student identity for academic oversight roles
  - opening the monitor is captured in the audit trail.
- Admin faculty assignment acceptance snapshot:
  - assignment list now shows accepted vs pending status per offering
  - admin can see who accepted and when
  - metric cards summarize assigned, accepted, pending, and primary-load counts for the selected faculty.
  - metric cards now also surface clarification-request and decline counts for faster load monitoring.
- Admin Portal root landing paths now resolve cleanly:
  - `/admin-portal` and `/admin-portal/` now redirect to the proper login/dashboard flow instead of showing an error page.
- Admin Portal left navigation now supports collapse/expand behavior with stronger visual hierarchy:
  - selected menu items use a green/yellow gradient highlight
  - sidebar typography was tuned for readability
  - sidebar state is persisted per browser.
- Admin Portal sidebar navigation now uses icon-led group and link chips with arrow-only collapse/expand controls:
  - collapsed view preserves the NCBA/logo area better
  - sidebar greens were deepened to reduce the yellow cast
  - group headers and menu entries use icons for quicker scanability.
- Sidebar collapse control moved to the top-right corner of the nav column, and collapsed mode now keeps icon-only menu visibility instead of hiding the menu block entirely.
- Admin Portal sidebar menu hierarchy refined further:
  - `+` markers now belong only to group headers
  - menu items use their own icons and are indented under the group
  - collapsed mode hides child menu items again so only group-level controls remain visible
  - sidebar toggle spacing was adjusted to avoid overlapping the logo.
- Collapsed sidebar icons now show hover tooltips and act as direct shortcuts to the first linked page under each group.

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
- Faculty monitoring scope now follows the faculty member's own organization scope:
  - AC visibility for faculty assignments is now based on matching faculty tenant + campus + department identity
  - the monitor no longer depends on the `CourseOffering.department` value to decide whether an AC may see a faculty member's assignments.
  - campus-wide reviewers such as CAO now correctly see faculty users with campus-level FACULTY role assignments even when those faculty accounts have a different default campus.
- Admin people-picker and review screens now prefer real names over usernames:
  - faculty dropdowns show `Full Name (username)` when available
  - audit, import, correction, reopen, hotfix, and user-role review screens now show full names first with usernames as secondary context.
- Admin course/section/offering selectors and monitoring tables now prefer readable academic labels:
  - course selectors show `Course Title (course code)`
  - section selectors prefer section name over raw code
  - offering selectors and related monitor/review tables now show title/name-first labels with codes kept as secondary reference.
- Faculty Grade Book Monitor metric cards now use a stronger NCBA-style green/yellow gradient for clearer visual emphasis.
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
- Course form campus-to-department mapping is now safer:
  - the department dropdown stays aligned to the selected campus instead of listing departments from every campus at once
  - admins are guided to choose campus first before mapping a campus-specific department to a course.
- Faculty assignment monitoring now includes response-window governance:
  - admin metric cards show due-within-24-hours and expired load counts
  - faculty pending-assignment cards show response due date, reminder history, and expired-state guidance.
- Configurable Features now exposes faculty-assignment acceptance workflow controls:
  - enable/disable reminders
  - enable/disable automatic expiry
  - response-window days
  - first-reminder day
  - repeat-reminder interval.
- Grade prediction module foundations are now available behind configurable features:
  - read-only faculty prediction page per class/period
  - admin prediction monitor for scoped academic oversight
  - what-if simulator controls by role
  - snapshot + dirty-queue architecture to keep projection pages fast without touching the official gradebook
  - prediction settings for role access, default assumption, at-risk flags, and best/worst/target-needed display.
- Faculty prediction now includes a dedicated interpretation page:
  - explains each prediction column in plain language
  - gives examples of best-case, worst-case, projected final, and what-if usage
  - is linked directly from the faculty prediction screen to reduce misinterpretation.
- Faculty prediction interpretation wording was simplified further so the explainer reads in shorter, clearer faculty-friendly language with a very basic example.
- Grade prediction now snaps completed periods to the official gradebook values:
  - when `Remaining = 0`, current/best/worst period projection matches `StudentPeriodGrade`
  - projected final aligns with the official final-grade record when available
  - stale snapshots from older prediction logic are automatically refreshed by computation-version checking
  - what-if now warns faculty when there are no remaining items left to simulate.
- Faculty prediction now shows `Needed for Passing Final`:
  - computes the average still needed across remaining future periods such as `PRE-FINAL` and `FX`
  - helps faculty answer how much the student still needs to reach a passing final grade
  - reuses the same final-grade averaging logic used by the current grading engine.
- Prediction wording for passing-final guidance is now more faculty-friendly:
  - clearer messages for missing earlier periods
  - clearer message when no future periods remain
  - clearer message when passing is already secured or no longer reachable.
- Faculty-facing label changed from `Needed for Passing Final` to `Average Needed to Pass Final` for clearer reading.
- Faculty prediction guide now displays the dedicated `grades_prediction.png` screenshot and uses shorter reading-order guidance for faster faculty scanning.

### Fixed
- Multiple grading summary/runtime errors reported during faculty usage:
  - missing `_round` reference
  - `ROUND_HALF_UP` import issue
  - `timezone` import issue in summary view path
- Table layout/formatting issues in faculty frontpage comparison section.
- Reopen and deadline-related visibility inconsistencies across admin/faculty screens.
- Filtering and selection behavior improvements across enrollment and assignment workflows.
- Faculty assignment monitor visibility for read-only academic roles:
  - selected faculty now shows the assigned-offerings table even without entering assign mode
  - empty state now clearly reflects current-scope results.
- Correction queue/review refinements:
  - review scope now shows student number with student name for clearer approver validation
  - pending requests that were created with fallback routing can now be reconciled to the configured approval route safely before review
  - review guard message now uses readable approver labels instead of raw role codes.
- Correction email notification presentation updates:
  - submission and registrar notification cards now use NCBA green styling and include petitioner name
  - period label in email notifications now uses period name only (no internal period code)
  - inline logo embedding improved for better logo rendering across mail clients.
- Configurable Features now remains backward-compatible when older posts omit the new grade-prediction assumption field; the form safely falls back to `Ignore Missing`.
- Faculty formal manual was restored and expanded with dedicated sections for assignment-acceptance governance and grade-prediction governance.

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

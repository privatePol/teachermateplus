# EDUGRADESPRO_CONTEXT.md

## 1. What EduGradesPro Is
EduGradesPro V1 is a multi-tenant, multi-campus academic grading and governance platform designed to centralize class grading workflows and reduce operational risk from disconnected grade files.

## 2. Current Operating Modules

### Admin Portal
- Security: users, roles, scoped permissions
- Admin security now supports creating new roles directly in the UI, then assigning permissions and scoped user-role mappings afterward.
- Role creation now supports optional permission-copying from an existing role before final permission fine-tuning.
- Role management now also supports editing existing roles, including activation/deactivation from the Admin UI.
- Role assignment scope now supports department-level granularity (`tenant + campus + department`) for governance use cases such as AC/Dean monitoring boundaries.
- Organization: tenants, campuses, departments, programs
- Academics: academic years, terms, courses, sections, offerings, faculty assignments, students
- Faculty assignments now carry acknowledgment fields (`accepted_at`, `accepted_by`) so admin can track whether the faculty member already accepted the load.
- Faculty assignments now also carry instruction/response workflow fields so admin can send notes and faculty can return clarification or decline reasons without leaving the portal.
- Faculty assignments now also carry response-window fields (`response_due_at`, `last_reminded_at`, `reminder_count`) so pending loads can be reminded and auto-expired when left unanswered.
- Admin faculty-assignment operations now also include:
  - one-click renewal of expired response windows
  - a dedicated assignment dashboard with campus, department, and faculty rollups.
- Enrollment management
- Grading management:
  - templates
  - periods/components/subcomponents/details
  - template assignment
  - period locks and deadlines
  - correction/reopen governance
  - correction route matrix by faculty department (tenant default + department overrides)
- Tools:
  - bulk imports
  - diagnostics/utilities (growing area)
  - configurable features/settings page for optional workflow toggles
- Audit views
- Admin analytics now include:
  - failure breakdowns by period/component/detail
  - faculty class fail-rate ranking (top 10)
  - same-course faculty comparison ranking (top 10)
  with threshold-aware pass/fail logic.
- Admin guide (`/admin-portal/guide/`) includes section-based procedural steps and updated media assets under `media/portal-img`.

### Faculty Portal
- Public faculty index (`/faculty/`)
- Faculty login/logout/password flows
- My Courses and Archived Classes
- Newly assigned classes now require explicit faculty acceptance before the class can be opened for grading or class-list work.
- Pending faculty assignments can now carry admin instructions, and faculty can respond with `accept`, `request clarification`, or `decline` plus a note.
- Pending faculty assignments now show a response deadline and reminder history, while expired assignments stay blocked until admin refreshes the response window.
- Faculty public homepage and admin dashboard now surface visible reminder/help cards for the assignment-acceptance rule so users encounter it even before opening the full guide pages.
- Period actions:
  - create activities
  - encode scores
  - attendance
  - summary and submission
  - correction request
- Gradebook print view
- Faculty guide/help center page
- Faculty guide/help center now uses section-specific `media/portal-img` visuals for workflow, activities/encoding, and submission guidance.
- Workflow visual in faculty guide is embedded directly inside the "Recommended Daily Faculty Workflow" step container for clearer instructional flow.
- Score correction request filing now supports:
  - multiple students or entire class in one request
  - multiple grading items across subcomponents/details
  - auto-loaded original values with corrected values entered separately
  - automatic gradebook update and recomputation after final approval.
  - feature-gated official correction PDF export for approved requests, including campus applicability and a registrar-facing cross-tab of official grade original vs corrected values.
  - optional approval notification email on submission to selected approver roles (for example, CAO and college dean).
  - submission notification cards now show petitioner name, use period name (not period code), and follow NCBA green branding.

## 3. Scope Model (Critical)
Core operational scope dimensions:
1. Tenant
2. Campus
3. Academic Year
4. Term
5. Course Offering
6. Student identity for integration-sensitive flows should be treated as tenant + campus + student number.

All business operations should respect these dimensions and permissions.

## 4. Grading Model (Current Behavior)
- Supports hierarchical grading structures:
  - Level 1: period components (e.g., Exam, Class Standing)
  - Level 2: subcomponents (e.g., Quizzes, Participation/Output)
  - Level 3: detail items (optional)
- Supports score entry methods:
  - raw score (computed against total/base)
  - direct percentage items (configured behavior)
- Summary page computes and presents period results and readiness indicators.
- Passing threshold policy now supports layered resolution:
  - profile-level (`TenantGradingProfile.passing_grade_threshold`)
  - tenant-level system setting (`PASSING_GRADE_THRESHOLD`)
  - fallback default `75.00`

## 5. Governance Rules in Focus
- Submission lock/reopen and deadlines are governance-critical.
- Correction behavior may be policy-driven (request vs self-reopen before deadline).
- Tenant-level correction process mode is now configurable:
  - `SYSTEM_REQUEST`: faculty can file in-portal correction requests (subject to route/policy)
  - `MANUAL_ONLY`: faculty in-portal correction request flow is disabled; operations use paper approval + admin reopen.
- Correction approvals now support route-matrix behavior:
  - route source = requesting faculty's default department
  - tenant may configure direct final approver or two-step route
  - final approver can be configured as CAO role
  - step-level reviewer checks use scoped role assignment and optional same-department match.
- Final approval of score correction requests now applies the approved corrected values directly into the faculty gradebook and triggers recomputation automatically.
- When enabled, approved correction requests can generate an official PDF artifact for registrar/AIMS reference without making registrar a required in-system approver.
- Registrar-facing official correction PDF now emphasizes approval and posting workflow by placing `Approval Summary` above `Registrar Reference`, and it no longer prints a `Supporting Attachments` section (supporting files remain available inside EduGradesPro).
- Registrar-facing official correction PDF presentation now includes:
  - NCBA logo at document top
  - left-aligned section bullet labels (`A` to `E`) for core report areas
  - improved spacing between sections
  - academic-context table without tenant row.
- Official correction PDF access now supports inline browser preview (new tab) to support immediate print/download flow from the viewer.
- Faculty correction request history UI now emphasizes student names and color-highlights original-to-corrected grade values for readability.
- Policy controls must be permission-aware and auditable.
- Admin-side academic monitoring now includes a read-only faculty gradebook monitor with metric cards, while masking student identity for AC/Dean/CAO-style oversight roles.
- Admin-side faculty assignment monitoring now highlights which loads are nearing response expiry versus already expired, making follow-up easier before grading starts.

## 6. Import System Context
- Import flow is two-step:
  1. Upload + validate + preview
  2. Confirm import
- Strict template headers required; custom format mapping is not accepted.
- Foreign keys are resolved by business keys (codes/usernames/emails), not numeric assumptions.
- Row-level errors and duplicate detection are mandatory.

## 7. Security Context
- Password complexity and first-login controls are in active implementation/use.
- Privacy consent is part of first-login governance.
- Email-based flows require SMTP configuration via environment variables.
- Optional feature flows are being designed to use dedicated configurable feature settings with global toggles, role-aware control where appropriate, and tenant/campus-aware recipient configuration rather than hard-enabled behavior.
- Faculty assignment reminder and expiry behavior is now also configurable from the dedicated Configurable Features screen instead of being fixed in code.
- Grade prediction is now designed as a configurable, read-only module with snapshot/queue processing so projection pages stay fast and do not alter official gradebook data.
- Correction submission approval notification email is now one of those configurable flows, with recipient roles managed per tenant.
- Registrar auto-email after final approval is now available as a configurable flow and uses campus-specific recipient mapping with default fallback when configured.
- Admin scope service now supports department-aware visibility filtering so academic staff roles can be bounded by area/department in faculty-assignment monitoring workflows.
- Academic monitoring scope for AC/Dean-style faculty oversight now follows the faculty member's own tenant/campus/department identity rather than depending on the stored department of each course offering.
- Registrar auto-email now follows the same branded card format as approver notifications and includes petitioner + period name (no code), with improved inline logo rendering for common mail clients.
- Session/device and login tracking are operational concerns and should remain auditable.

## 8. Integration Direction
- Preferred SIS integration mode: API (instead of CSV handoff).
- Target: expose submitted periodic/final grade data securely for SIS pull.
- Filters should include tenant/campus/AY/term/period/offering to avoid leakage.
- SIS periodic grades API now enforces campus-safe identity guardrails:
  - `student_no` queries require both `campus_code` and `section_code`
  - `section_code` queries require `campus_code`
  to support separate campus SIS/AIMS servers and avoid ambiguous cross-campus matching.

## 8.1 Deployment Baseline
- Target production stack: **Ubuntu + Gunicorn + Nginx + systemd + cron**.
- Canonical runbook: `docs/DEPLOYMENT_UBUNTU.md`.
- Infra templates are versioned in:
  - `ops/systemd/`
  - `ops/nginx/`
  - `ops/cron/`
  - `ops/scripts/`
- Deadline governance automation depends on cron jobs for:
  - `manage.py auto_lock_period_deadlines`
  - `manage.py auto_lapse_correction_windows`
  - `manage.py queue_period_reminders`
  - `manage.py process_faculty_assignment_reminders`

## 9. UX Direction (Already Requested by Stakeholders)
- Cleaner, less confusing hierarchy in grading setup and faculty screens.
- Strong readability in tables/forms for non-technical users.
- Support broader facility terminology in academics/offering labels (`Room/Office/Lab`) instead of room-only wording.
- Persistent filters where practical.
- Reduced clicks for repetitive tasks (auto-filter, bulk assign, guided forms).
- Course maintenance now applies campus-first department filtering in the form UI so campus-specific department mapping is less error-prone.
- Admin monitoring/review screens now favor human-readable staff naming:
  - faculty selectors use `Full Name (username)` when name data exists
  - review-heavy screens keep the username visible only as supporting context.
- Admin academic selectors now favor human-readable academic labels:
  - course choices prefer `Course Title (code)`
  - section choices prefer section name
  - offering labels combine readable course/section/term names with codes only as secondary reference.
- Faculty Grade Book Monitor now uses a greener/yellow metric-card treatment to align better with the NCBA visual direction and make oversight stats easier to scan.
- Campus-wide academic reviewers such as CAO now resolve faculty visibility by campus-level FACULTY assignments correctly, even when a faculty user's default campus differs from the campus being reviewed.
- Admin Portal has a dedicated root landing redirect so `/admin-portal` and `/admin-portal/` resolve cleanly to the correct login/dashboard flow.
- Admin Portal sidebar is now collapsible/expandable, persists its state per browser, and highlights the active menu entry with a gradient treatment for quicker navigation.
- Admin Portal sidebar navigation now uses icon-led group headers and menu chips with arrow-only collapse/expand controls, plus a greener sidebar gradient and improved collapsed logo visibility.
- The sidebar collapse control is anchored to the top-right of the nav column, and collapsed mode is intended to remain icon-visible instead of hiding the whole menu block.
- The Admin Portal sidebar now distinguishes group vs item visuals more clearly:
  - group headers use `+` markers
  - menu items use their own icons and sit indented under each group
  - collapsed mode keeps only group-level controls visible to reduce clutter.
- In collapsed mode, group icons now expose tooltips on hover and serve as direct shortcuts to the first page inside that group.
- Faculty portal now includes an optional prediction page per class/period, while Admin Portal includes a scoped Grade Prediction Monitor for academic oversight roles.
- Prediction pages use the same grading-template path and official final-grade formula as the production computation rules, but results remain unofficial, feature-gated, and audit-log friendly.
- Faculty prediction now also has a dedicated interpretation/explainer page linked from the prediction screen so faculty can read the meaning of each column and the correct use of what-if simulation before acting on the values.
- The faculty prediction explainer is being written in simpler, shorter language with concrete examples to reduce the chance of misreading unofficial projections.
- Prediction safety rules now include a completed-period safeguard: if the selected period has no remaining items, prediction uses the official `StudentPeriodGrade` and official final-grade record as the displayed baseline, and older snapshots are refreshed when the computation version changes.
- Prediction now also calculates the average still needed across future periods to finish with a passing final grade, giving faculty a direct answer for cases such as completed `MIDTERM` with remaining `PRE-FINAL` and `FX`.
- Faculty-facing prediction labels are being simplified so requirement messages read closer to plain advisory language instead of technical system phrasing.
- The passing-final requirement label now uses clearer faculty wording: `Average Needed to Pass Final`.
- The faculty prediction guide now includes the dedicated `grades_prediction.png` screenshot and a shorter “quick reading order” explanation to support non-technical faculty users.

## 10. Open Design/Governance Topics
- Tenant-specific grading methodology options (engine profiles/switches).
- Global active AY/Term governance control and permissions.
- Finalized correction and reopen policy matrix (prior-to-deadline behavior).
- Expanded policy/manual content for management and academic officers.
- Tenant UI for managing `PASSING_GRADE_THRESHOLD` directly in tools/settings (currently resolved through system setting service and profile overrides).
- Feature-governance direction:
  - optional workflows/features should be configurable instead of always on
  - feature toggles should support global enable/disable and, where appropriate, per-role enable/disable
  - configurable features should be managed from a dedicated settings/features page.

## 11. For New Contributors
Before changing anything:
1. Read `AGENTS.md`
2. Review latest entries in `CHANGE_LOG.md`
3. Identify whether the change touches governance, computation, or scoped filtering
4. Test both Admin and Faculty impact paths

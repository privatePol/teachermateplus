# EDUGRADESPRO_CONTEXT.md

## 1. What EduGradesPro Is
EduGradesPro V1 is a multi-tenant, multi-campus academic grading and governance platform designed to centralize class grading workflows and reduce operational risk from disconnected grade files.

## 2. Current Operating Modules

### Admin Portal
- Security: users, roles, scoped permissions
- User account security now includes:
  - password complexity validation
  - forced password change on issued/reset credentials
  - privacy consent acceptance tracking
  - single-device session enforcement
  - temporary login lockout after repeated failed attempts, configurable from `Tools -> Configurable Features -> Login Security`
  - a `Security -> Login Lockouts` monitor page where authorized admins can review active portal lockouts and clear them when operationally justified
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
  - configurable default-primary behavior for newly created faculty assignments, with manual override still available from the assignments list.
- Enrollment management
- `Tools -> Configurable Features` now includes a more guided `Class Master List Ownership` area:
  - class-level ownership overrides can be filtered by tenant, campus, term, and optional faculty
  - class labels intentionally omit the already selected term/academic year to reduce repetition
  - a custom multi-class picker and selected-class preview both emphasize faculty names for quicker admin verification
  - the page now keeps focus on the same ownership card when term/faculty filter changes reload the view
  - the configurable-features screen itself now uses collapsible cards with stronger spacing and medium shadow depth so large settings groups are easier to navigate
- Grading management:
  - templates
  - template governance workflow (tenant-scoped role matrix for draft, submit, review, publish, and hotfix stages)
  - template governance Phase 2 workflow engine:
    - approval workflows now store real step timelines
    - hotfix workflows now store review/apply steps
    - sequential review chains can be enabled per tenant
  - periods/components/subcomponents/details
  - grading-template testing calculator (read-only validation using sample raw score + total score)
  - template assignment
  - active grading period governance now drives faculty period openness:
    - only the configured active campus-term period stays open for normal faculty work
    - non-active periods are closed by policy unless the period has been formally reopened or is inside an approved correction window
    - correction filing pages for already submitted periods remain reachable so governance closure does not strand legitimate correction requests
  - period locks and deadlines
  - correction/reopen governance
  - correction route matrix by faculty department (tenant default + department overrides)
- Tools:
  - bulk imports
  - diagnostics/utilities (growing area)
  - configurable features/settings page for optional workflow toggles
  - template governance settings page for template lifecycle stage-role control
- Audit views
- Admin analytics now include:
  - failure breakdowns by period/component/detail
  - faculty class fail-rate ranking (top 10)
  - same-course faculty comparison ranking (top 10)
  with threshold-aware pass/fail logic.
- Admin guide (`/admin-portal/guide/`) includes section-based procedural steps and updated media assets under `media/portal-img`.
- The Admin guide quick-link badge now uses the clearer label `Grading Template Governance` and points to the governance section inside the guide instead of jumping straight to the live tools page.
- The Admin guide `Governance Settings` section now presents governance areas in a clearer table that tells admins exactly where each setting should be applied in the Tools menu.
- The Admin guide `Configuration Setup` section now also presents setup work in a clearer table that tells admins where each setup area should be applied and in what order grading-template coverage should be checked.
- The Admin guide `Tools and CSV Importing` section now also presents import work in a clearer table that tells admins where each import type should be applied and what the import is for.
- Production operations now also have a dedicated incident-response reference:
  - `docs/PRODUCTION_INCIDENT_RUNBOOK.md`
  - the Admin guide includes a `Production Incident Response` section for first-response handling during live outages
  - that section is now intentionally restricted to `SUPER_ADMIN` visibility in the Admin guide
  - `docs/DEPLOYMENT_UBUNTU.md` now points to the incident runbook for emergency recovery workflow guidance.
- Deployment documentation is now fuller and production-oriented:
  - `docs/DEPLOYMENT_UBUNTU.md` now covers Ubuntu + MariaDB/MySQL deployment, GitHub pull workflow, pre-production preparation, first go-live sequence, backups, and release discipline
  - the deployment guide now also begins with a stage-by-stage rollout checklist and explains the recommended multi-app Ubuntu layout using `/opt/<app-name>` for code, `/etc/<app-name>` for env files, and `/var/log/<app-name>` for logs
  - `docs/STAGING_WORKFLOW.md` now explains staging in simpler operational terms and recommends a practical `local -> GitHub -> staging -> production` workflow
  - `docs/PRODUCTION_DATA_PROMOTION.md` now documents how to promote approved local data into staging/production using reviewed fixture bundles instead of blindly copying a development database
  - `docs/NCBA_GO_LIVE_CHECKLIST.md` now provides an NCBA-specific launch checklist and starts with the exact `.md` reading order before rollout work begins
  - helper scripts now exist for that workflow:
    - `ops/scripts/export_data_bundle.ps1`
    - `ops/scripts/import_data_bundle.sh`
  - deployment-ready examples now also exist for:
    - `ops/env/edugradespro.production.env.example`
    - `ops/env/edugradespro.staging.env.example`
    - `ops/systemd/edugradespro-staging-gunicorn.service`
    - `ops/nginx/edugradespro-staging.conf`
  - `docs/DB_SCHEMA.md` is now available as a generated database dictionary sourced from the Django model registry, and `ops/scripts/generate_db_schema.py` can regenerate it when schema changes
  - `manage.py` now defaults to `config.settings`, allowing `DJANGO_ENV` to correctly choose local vs production behavior for CLI commands
  - `.env.example` now matches that environment-aware settings pattern with `DJANGO_SETTINGS_MODULE=config.settings`.
- Presentation/support materials now also include `docs/EDUGRADESPRO_ACADEMIC_PRESENTATION.md`, a presentation-ready academic briefing outline focused on template-driven grading, faculty grading structure, correction governance, template lifecycle, governance settings, and platform value to academic leadership.
- Security review/support materials now also include `docs/OWASP_GAP_ASSESSMENT.md`, which documents EduGradesPro's current OWASP-aligned controls, partial gaps, and production hardening needs without overstating full formal OWASP compliance.
- The academic presentation guide now also includes optional add-on slides for:
  - faculty support features
  - active grading period governance
  while keeping the original presentation flow unchanged.

### Faculty Portal
- Public faculty index (`/faculty/`)
- Faculty login/logout/password flows
- Faculty login security now includes password complexity validation, forced password change, privacy consent flow, single-device session enforcement, and configurable temporary login lockout for repeated failed attempts.
- My Classes and Archived Classes
- Faculty Portal sidebar keeps only the dedicated `Classes` block for the class list; the duplicate top-level `My Classes` nav entry was removed to avoid confusion.
- Faculty Portal sidebar now organizes the main operational links as:
  - `Classes`
    - `My Classes`
    - `Students At-Risk Monitor`
    - `Archived Classes`
  - `Reminders and Notes`
    - `Reminders`
    - `Notes`
- Newly assigned classes now require explicit faculty acceptance before the class can be opened for grading or class-list work.
- Pending faculty assignments can now carry admin instructions, and faculty can respond with `accept`, `request clarification`, or `decline` plus a note.
- Pending faculty assignments now show a response deadline and reminder history, while expired assignments stay blocked until admin refreshes the response window.
- Faculty public homepage and admin dashboard now surface visible reminder/help cards for the assignment-acceptance rule so users encounter it even before opening the full guide pages.
- Faculty Portal now also surfaces a reusable grade submission deadline banner on:
  - Faculty Dashboard
  - My Classes
  - class period page
  so faculty can see the nearest active unsubmitted period deadline more clearly.
- Faculty Portal public homepage (`/faculty/`) now also includes additive explanatory sections for:
  - faculty portal entry experience
  - faculty support features
  - active grading period governance
  so academic presentations and faculty orientation can point to concrete homepage content without changing the underlying operational workflow.
- Faculty Portal now includes a Reminder Center where faculty can save future activity reminders, snooze or complete them, and optionally queue email notifications to their profile email address.
- Future-dated faculty-created activities can now auto-create linked `Activity Preparation` reminders so planned activities appear in the reminder workflow without a second manual step.
- Activity-linked reminders remain queue-based for email delivery:
  - `send_email` is turned on only if the tenant enables Faculty Reminder Email
  - actual email queue creation still happens in the background when the reminder becomes due
  - if the activity is deleted or no longer future-dated, the linked reminder is cancelled automatically
- The Reminder Center page has been visually polished into clearer sections with summary cards, a guided create form, and easier-to-scan reminder cards.
- Faculty Portal now also includes a Student At-Risk Monitor that groups prediction-based risk rows by class and period so faculty can prioritize intervention before periods close.
- Faculty Portal now also includes a class-period `Who Viewed` history page that lets faculty review admin-side read-only grade book monitor openings already recorded in the audit trail.
- Faculty Portal class-list handling now distinguishes:
  - `Remove from Class` for student schedule movement to another class
  - `DR` for true drop from the class
  - `W` for true withdrawal from the term
- Faculty class-list UX now also includes:
  - typed confirmation (`remove`) before a faculty-triggered `Remove from Class` action proceeds
  - typed student lookup that narrows the `Add Student to This Class` dropdown by student number or by the student's last name and first name before save
  - clearer status badge colors where `DR` uses a danger badge and `W` uses a warning badge
  - a line number before each student number for easier manual cross-checking
  - a compact `x` remove icon with tooltip beside the student number instead of a full-width remove button
  - easier-to-scan class-list typography with names shown as `LAST NAME, First Name`
- Admin tools now expose `Class Master List Ownership` so tenant admins can choose whether class-list maintenance remains admin-only or is temporarily delegated to assigned faculty.
- `Class Master List Ownership` in Configurable Features now supports both:
  - a tenant-wide default ownership mode
  - an optional class-level override chosen from a multi-select class dropdown filtered by the selected tenant, campus, and term
  - an optional faculty-name filter that narrows the class dropdown only when a faculty member is selected
  - class labels that include the assigned faculty name in parentheses for easier admin identification
- Period actions:
  - create activities
  - encode scores
  - attendance
  - summary and submission
  - correction request
- Gradebook print view
- Faculty guide/help center page
- Faculty privacy-consent page now uses the revised shared `EduGradesPro Privacy Consent` statement for system data-processing acknowledgment.
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
- Account-security governance now includes configurable temporary login lockout for failed sign-in attempts, alongside password, consent, and single-session controls.
- Template lifecycle governance is now configurable by tenant:
  - allowed roles can be assigned per stage for draft, submit for approval, approval review, publish, hotfix request, and hotfix review/apply
  - optional Phase 2 sequence can be enabled for:
    - `Template Review -> Final Approval`
    - `Hotfix Review -> Hotfix Final Apply`
  - the governance page now separates these visually as:
    - `Phase 1 - Role Matrix`
    - `Phase 2 - Sequential Steps`
    to reduce admin confusion between base role permissions and the optional step chain
  - the governance page now also includes step-by-step recommended setup cards for:
    - new grading template issuance
    - hotfix governance
    so admins can follow the intended NCBA configuration more directly.
  - review screens now expose workflow timelines so admins can see which step is pending and who already acted
  - same-user separation can be enforced between submit/review, review/publish, and hotfix request/apply
  - same-user separation can also be enforced between review/final approval and hotfix review/final apply
  - publish can be forced to require prior approval or relaxed for direct steward publishing when policy allows.
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
- The faculty-facing correction workflow is now explicitly documented as a direct petition model for score corrections:
  - faculty enters exact corrected values in the request
  - approver approves the exact values
  - EduGradesPro applies the approved values, recomputes the period, and closes the request without a separate faculty finalize step
  - manual correction-window/finalize behavior remains only as a legacy or exceptional governed path.
- Successful faculty period submission now redirects back to the class-period overview instead of the just-submitted period summary so active-period governance does not surface a false closure error immediately after submission.
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
- Faculty reminder center visibility and reminder-email queueing are also configurable from the same dedicated features screen so operations can turn them on/off without code changes.
- Faculty Notes / Private Memo now provides a private faculty-only note center for general, class-linked, and student-linked memos with pinning support.
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
  - `manage.py queue_faculty_reminder_emails`
  - `manage.py process_faculty_reminder_email_queue`

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
- Admin Portal now includes a grading-template testing calculator so operations can validate how a selected template will compute raw score conversion, Prelim, Midterm, Pre-Final, Final Exam, and Final Grade before live use.
- The grading-template testing calculator now presents each period as its own guided walkthrough with clearer step sequencing and distinct period colors, helping admins explain the computation flow with less confusion.
- The calculator presentation is now intentionally simpler and closer to the grading-template builder:
  - period formulas are shown in plain builder-style form such as `PRELIM GRADE = Prelim Exam (40%) + Class Standing (60%)`
  - nested class-standing structures are shown as a simple hierarchy list
  - the previous `effective weight in period` column was removed because it added confusion for non-technical reviewers.
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
- Faculty Portal now also exposes the effective grading template per assigned class:
  - class cards show the current grading template name
  - faculty can open a read-only grading-template page for the class
  - the page presents period formulas and class-standing breakdown using the template actually resolved for that offering.
- The faculty prediction guide now includes the dedicated `grades_prediction.png` screenshot and a shorter “quick reading order” explanation to support non-technical faculty users.
- Admin Portal course-template assignment now supports bulk multi-course assignment with prior-assignment checking in the same term scope, making template rollout faster while avoiding conflicting assignment rows.
- Admin Portal course-template assignment list now also acts as a coverage monitor by surfacing metric cards and a direct filter for courses that still have no active grading template assignment.
- Admin guide section `7. Submission and Reopen Control` now mirrors the improved guide presentation style and explicitly maps locks, submission monitoring, reopen governance, and faculty re-submission follow-through to the correct Admin Portal pages.
- Admin guide section `7. Submission and Reopen Control` now also uses a dedicated visible-border table style so policy tables are easier to scan and read during operations briefings.
- Admin guide section `8. Grade Correction Process (System vs Manual)` now uses the same table-first presentation style and makes the system request path, official PDF artifact, approval notifications, and manual-only handling easier to distinguish.
- Faculty guide and formal manual now use the clearer label `Grade Corrections and Reopen Governance` and begin that section with a short system/manual mode summary to reduce confusion for faculty readers.
- Faculty deadline reminder banners now distinguish between `no deadline configured` and `deadline configured for a different campus/term scope`, so faculty users get a clearer explanation when admin settings do not apply to their accepted classes.
- The `Period Lock` admin form now uses actual grading-template period choices and validation, reducing deadline mismatches caused by manually typed term codes instead of real period codes.
- EduGradesPro now has an `Active Grading Period` governance model separate from grading-template period codes:
  - canonical periods are defined per tenant and term
  - one active period is selected per tenant/campus/term
  - the setting is visible in both Admin and Faculty portals
  - optional auto-advance moves to the next configured period after the current period deadline passes.

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

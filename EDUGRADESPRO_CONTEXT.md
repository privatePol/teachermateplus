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
- Correction submission approval notification email is now one of those configurable flows, with recipient roles managed per tenant.
- Registrar auto-email after final approval is now available as a configurable flow and uses campus-specific recipient mapping with default fallback when configured.
- Admin scope service now supports department-aware visibility filtering so academic staff roles can be bounded by area/department in faculty-assignment monitoring workflows.
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

## 9. UX Direction (Already Requested by Stakeholders)
- Cleaner, less confusing hierarchy in grading setup and faculty screens.
- Strong readability in tables/forms for non-technical users.
- Support broader facility terminology in academics/offering labels (`Room/Office/Lab`) instead of room-only wording.
- Persistent filters where practical.
- Reduced clicks for repetitive tasks (auto-filter, bulk assign, guided forms).

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

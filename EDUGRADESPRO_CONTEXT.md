# EDUGRADESPRO_CONTEXT.md

## 1. What EduGradesPro Is
EduGradesPro V1 is a multi-tenant, multi-campus academic grading and governance platform designed to centralize class grading workflows and reduce operational risk from disconnected grade files.

## 2. Current Operating Modules

### Admin Portal
- Security: users, roles, scoped permissions
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

## 3. Scope Model (Critical)
Core operational scope dimensions:
1. Tenant
2. Campus
3. Academic Year
4. Term
5. Course Offering

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
- Final correction approvals now open a fixed 24-hour validity window.
  - If faculty does not finalize within this window, request status is set to `LAPSED`.
  - Faculty must submit a new correction request after lapse.
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
- Session/device and login tracking are operational concerns and should remain auditable.

## 8. Integration Direction
- Preferred SIS integration mode: API (instead of CSV handoff).
- Target: expose submitted periodic/final grade data securely for SIS pull.
- Filters should include tenant/campus/AY/term/period/offering to avoid leakage.

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
- Persistent filters where practical.
- Reduced clicks for repetitive tasks (auto-filter, bulk assign, guided forms).

## 10. Open Design/Governance Topics
- Tenant-specific grading methodology options (engine profiles/switches).
- Global active AY/Term governance control and permissions.
- Finalized correction and reopen policy matrix (prior-to-deadline behavior).
- Expanded policy/manual content for management and academic officers.
- Tenant UI for managing `PASSING_GRADE_THRESHOLD` directly in tools/settings (currently resolved through system setting service and profile overrides).

## 11. For New Contributors
Before changing anything:
1. Read `AGENTS.md`
2. Review latest entries in `CHANGE_LOG.md`
3. Identify whether the change touches governance, computation, or scoped filtering
4. Test both Admin and Faculty impact paths

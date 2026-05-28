# Student Portal Development Prompt

Copy and paste the prompt below into a new Codex chat session when ready to begin Student Portal development.

````text
You are Codex working in the EduGrade+ V1 repository.

Goal:
Build the initial Student Portal foundation for EduGrade+ as a local-only development stage. The portal should let students securely log in and view only their own tenant-owned records. The first implementation should be conservative, read-only, tenant-scoped, and easy to review.

Before making code changes, read and follow these files:

1. AGENTS.md
2. EDUGRADEPLUS_CONTEXT.md
3. docs/STUDENT_PORTAL_PLAN.md
4. docs/DB_SCHEMA.md
5. docs/ROLE_PERMISSION_MATRIX.md
6. docs/ROLE_BASED_UAT_TEST_SCENARIOS.md
7. CHANGE_LOG.md

Important repository rules:

- Backend is Django with apps-based architecture.
- Frontend is Django templates, Bootstrap, and custom CSS.
- Respect tenant and campus scope on every read/write operation.
- Enforce RBAC before rendering actions and before processing requests.
- Keep grading governance and auditability intact.
- Prefer additive, low-risk changes over broad rewrites.
- New features should be configurable whenever practical.
- User-manageable feature toggles/settings should live in the configurable-features area when appropriate.
- Run `python manage.py check` after edits.
- Add migrations for model changes.
- Update `CHANGE_LOG.md`, `EDUGRADEPLUS_CONTEXT.md`, and relevant docs when behavior changes.
- Do not revert unrelated existing worktree changes.

Local-only instruction:

The `apps/student_portal/` directory is intentionally ignored in `.gitignore` for now. Treat the Student Portal app as a local prototype/development stage unless explicitly instructed otherwise. Do not remove this ignore rule.

Terminology:

- "Student Portal" is the user-facing portal.
- `student_portal` is the Django app name.
- "Module" means a feature area inside the portal, such as Courses, Grades, Attendance, Profile, or Account.

Recommended app path:

```text
apps/student_portal/
```

Recommended URL prefix:

```text
/student/
```

Primary objectives for the first build:

1. Create the Student Portal app structure.
2. Add a `StudentAccountLink` model to securely link an existing `Student` record to a `User` account.
3. Add tenant/campus/student validation so the link cannot cross tenant or campus boundaries.
4. Add a `student_portal.access` permission or equivalent permission seed consistent with the existing RBAC pattern.
5. Add a service/helper that resolves the current logged-in user's active student link.
6. Add a basic Student Portal dashboard.
7. Add read-only My Courses and Profile views.
8. Add tests proving tenant/student isolation.
9. Keep grades and attendance modules planned but avoid exposing sensitive details until the foundation is secure, unless explicitly asked to include them in the same pass.

Security and data-boundary requirements:

Student Portal access must never be based on a free-form student number in a request.

Every student-facing query must start from:

```python
request.user -> active StudentAccountLink -> linked Student
```

Then filter records by tenant, campus when present, and student.

Safe query pattern:

```python
link = StudentAccountLink.objects.select_related("tenant", "campus", "student").get(
    user=request.user,
    is_active=True,
)

grades = StudentFinalGrade.objects.filter(
    tenant=link.tenant,
    campus=link.campus,
    student=link.student,
)
```

Never trust these request values without checking against the active account link:

- `student_id`
- `student_no`
- `offering_id`
- `enrollment_id`
- `campus_id`
- `tenant_id`

Do not expose:

- other students
- classmate names
- class rankings
- grade distributions
- grade analytics
- draft/unsubmitted grades
- activity-level scores unless a feature setting explicitly allows it

Account registration/provisioning requirements:

Students should not freely self-register by typing a student number and any email address.

V1 account provisioning should be admin-driven:

1. Admin creates or imports the `Student` record.
2. Admin verifies the registered student email address.
3. Admin opens Student Account Links or Student Portal Account Provisioning.
4. Admin selects the student.
5. EduGrade+ shows the email destination for invitation/activation.
6. Admin confirms provisioning.
7. EduGrade+ creates or links the `User` account.
8. EduGrade+ creates an active `StudentAccountLink`.
9. EduGrade+ sends an activation link or temporary credential to the registered student email if email sending is enabled.
10. Student logs in, sets a password, and accepts privacy consent.

Email destination policy:

- Send activation/invitation only to the official registered email stored on the `Student` record.
- If multiple email fields exist, use this priority:
  1. `official_email`
  2. verified school email
  3. validated imported student contact email
- If no trusted email exists, do not send an invitation automatically. Admin must correct the student record first or use an approved manual credential process.
- Students must not be allowed to enter an arbitrary email address during registration and immediately receive access.

Suggested schema:

Create a model similar to:

```text
StudentAccountLink
- tenant
- campus
- student
- user
- is_active
- linked_by_user
- linked_at
- notes
```

Recommended constraints:

- `student.tenant` must match `tenant`.
- `student.campus` must match `campus`.
- A student should have at most one active user link.
- A user should have at most one active student link.
- A user/student link should be deactivatable instead of deleted for auditability.

Before adding fields to `Student`, inspect the current model. If there is no official email field, prefer a small additive change with clear naming only if needed for account provisioning. If email support is too broad for the first pass, document it and keep invitation sending behind a feature/configuration switch.

Feature configuration:

Add settings only where needed for the current phase. The plan recommends:

```text
Enable Student Portal: default Off
Enable student self-claim: default Off
Show period grades after submission: default On
Show final grade after submission: default On
Show activity scores: default Off
Show attendance details: default On
Show inactive historical terms: default On
```

For the first build, at minimum support a global enable/disable guard for Student Portal access if this matches existing configuration patterns.

Initial URLs:

```text
/student/
/student/courses/
/student/courses/<offering_id>/
/student/profile/
/student/account/
```

Recommended later URLs:

```text
/student/grades/
/student/grades/<offering_id>/
/student/attendance/
```

View behavior:

Dashboard:

- Requires authenticated user.
- Requires `student_portal.access`.
- Requires active `StudentAccountLink`.
- Shows linked student identity and active academic year/term.
- Shows summary of active/current enrollments.

My Courses:

- Shows only enrollments for the linked student.
- Groups or filters by academic year/term where practical.
- Shows course code, title, section, campus, enrollment status, and faculty name if available.
- Must verify detail pages by enrollment ownership.

Profile:

- Read-only.
- Shows student number, name, tenant, campus, department, program, and status fields that already exist.
- Does not allow student edits in V1.

Account:

- Reuse existing account/password/privacy consent functionality where possible.
- Do not create a parallel password system.

Grades and attendance:

- If implementing in this pass, keep them read-only and conservative.
- Show period grades only when the matching gradebook is submitted.
- Show final grade only when final grade is computed and submitted/released.
- Show attendance only for the linked student.
- Add tests before exposing any sensitive grade or attendance view.

Admin Portal support:

Add only what is needed for V1:

- A way for admin/superadmin to create or manage Student Account Links.
- List should be tenant/campus scoped.
- Creation should validate student/user/tenant/campus consistency.
- Deactivation should be preferred over hard delete.
- Audit create/deactivate actions if consistent with local patterns.

Tests required:

Positive:

- Student with active link can open Student Portal dashboard.
- Student sees only their own profile.
- Student sees only their own courses/enrollments.
- Admin can create a valid Student Account Link.

Negative:

- User without active Student Account Link cannot open Student Portal.
- Inactive Student Account Link blocks access.
- Student cannot open another student's offering detail.
- Student cannot access records from another tenant.
- Student cannot access records from another campus through a mismatched link.
- Cross-tenant StudentAccountLink creation is blocked.
- Cross-campus StudentAccountLink creation is blocked.
- Invalid/offering URLs return a safe denial or 404 instead of leaking existence.

Validation commands:

Run at minimum:

```text
python manage.py check
python manage.py test apps.student_portal --verbosity 2
```

If Admin Portal account-link management is added, also run the relevant Admin Portal tests or focused new tests.

Documentation updates:

Update:

1. CHANGE_LOG.md
2. EDUGRADEPLUS_CONTEXT.md
3. docs/STUDENT_PORTAL_PLAN.md if implementation decisions differ from the plan
4. Any Admin/Student guide page created or changed

Implementation style:

- Follow existing Admin Portal and Faculty Portal layout conventions.
- Keep UI quiet, work-focused, and readable.
- Do not make a marketing landing page.
- Build the actual usable portal screen as the first screen.
- Use existing authentication/session/security mechanisms.
- Use existing permission decorators or add a student-safe equivalent consistent with project patterns.
- Keep changes isolated and easy to review.

First task breakdown:

1. Inspect existing auth, portal decorators, RBAC seeding, user model, student model, and student/enrollment query patterns.
2. Decide the exact model location for `StudentAccountLink`.
3. Add model, migration, admin registration if appropriate, and validation.
4. Add student portal app shell, URLs, views, templates, and access checks.
5. Add Admin Portal account-link management only if needed for usable provisioning.
6. Add tests for scope and access.
7. Run validation.
8. Update docs.

Important final answer expectation:

When done, summarize:

- files changed
- migrations added
- routes added
- permissions/settings added
- tests run
- any known limitations or deferred items
````

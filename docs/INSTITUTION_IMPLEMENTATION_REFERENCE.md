# TeacherMate+ Institution Implementation Reference

This guide is for the superadmin or lead administrator who will prepare TeacherMate+ for use in a real institution.

Use it before opening the Faculty Portal for grade encoding. It explains:

- what data must be encoded
- why the data is needed
- what tenant settings should be configured
- the recommended order of setup
- the governance checks that protect grades, permissions, corrections, and audit records

TeacherMate+ is strict about scope. Most records are tied to a tenant, campus, department, academic year, term, course, section, or offering. This is intentional. It protects multi-campus data and helps academic officers see only the records they are allowed to manage.

## 1. The Goal of Implementation

The implementation is complete when faculty can sign in, see their accepted classes, create grading activities, encode scores, review summaries, and submit grades under the correct institutional policy.

For that to happen, these must already be ready:

1. The institution structure exists.
2. Admin and faculty users exist.
3. Roles and permissions are assigned with the correct tenant, campus, and department scope.
4. Academic year and term are active.
5. Courses and sections exist.
6. Course offerings are created for the live term.
7. Faculty assignments are active and accepted.
8. Students are created.
9. Students are enrolled in the correct offerings.
10. Grading templates are published.
11. Course template assignments and tenant grading profiles can resolve the correct grading rules.
12. Active grading period and period deadlines are configured.

If any one of these is missing, the Faculty Portal may still open, but faculty may not be able to proceed correctly.

## 2. Short Setup Order

Use this order for a clean production setup:

1. Prepare production server and database.
2. Create the superadmin account.
3. Create tenant and campuses.
4. Create departments and programs.
5. Create users, roles, permissions, and scoped role assignments.
6. Create academic years, terms, and term grading periods.
7. Set the active academic year and term.
8. Create courses and sections.
9. Create course offerings.
10. Create or import students.
11. Assign faculty to offerings.
12. Create, approve, and publish grading templates.
13. Assign grading templates to courses.
14. Create tenant grading profiles.
15. Configure active grading period and deadlines.
16. Import or encode enrollments.
17. Run readiness checks.
18. Ask faculty to accept assignments and begin grading.

## 3. Production Environment Readiness

Before encoding institution data, production should already be deployed and tested.

Recommended production setup:

- Ubuntu server
- MariaDB or MySQL, not SQLite
- Nginx
- Gunicorn
- HTTPS
- separate staging and production databases
- environment variables stored outside the repository
- backup and restore process tested

Important production files:

- `docs/DEPLOYMENT_UBUNTU.md`
- `docs/STAGING_WORKFLOW.md`
- `docs/PRODUCTION_DATA_PROMOTION.md`
- `docs/NCBA_GO_LIVE_CHECKLIST.md`
- `docs/PRODUCTION_INCIDENT_RUNBOOK.md`
- `docs/DATA_AT_REST_PROTECTION_GUIDE.md`

Before go-live, confirm:

- `DJANGO_ENV=production`
- `DEBUG=False`
- `DJANGO_SECRET_KEY` is strong and not the development fallback
- `DJANGO_ALLOWED_HOSTS` contains the real production domain
- database credentials are correct
- SMTP is configured if email features will be used
- SIS periodic grades API is enabled only for colleges that require SIS/AIMS integration; keep it off otherwise from Configuration Management.
- `SITE_URL` is configured if QR codes and email links should point to the live site
- logs are written to the production log directory
- backups are running

## 4. Master Data to Encode

This section explains the records that must be prepared before faculty can encode grades.

### 4.1 Tenant

Menu:

```text
Admin Portal -> Organization -> Tenants
```

Encode:

| Data | Why it is needed |
| --- | --- |
| Tenant code | Short stable institution code used by settings, imports, reports, and scope checks. |
| Tenant name | Official school or institution name shown in admin lists and reports. |
| Active flag | Only active tenants should be used in operations. |

Guidance:

- Create one tenant per institution or school system.
- Do not create a new tenant for each campus. Campuses belong under the tenant.
- Use a stable code. Avoid temporary codes like `TEST` for production.

### 4.2 Campuses

Menu:

```text
Admin Portal -> Organization -> Campuses
```

Encode:

| Data | Why it is needed |
| --- | --- |
| Tenant | Connects the campus to the institution. |
| Campus code | Used for imports, filters, role scope, and reports. |
| Campus name | Human-readable campus name. |
| Address | Used for reference and printed documents when needed. |
| Active flag | Inactive campuses are excluded from day-to-day operations. |

Guidance:

- Create every campus that will run grading operations.
- Keep codes short and consistent.
- Do not reuse a campus code inside the same tenant.

### 4.3 Departments

Menu:

```text
Admin Portal -> Organization -> Departments
```

Encode:

| Data | Why it is needed |
| --- | --- |
| Tenant and campus | Keeps department scope correct for multi-campus use. |
| Parent department | Allows broad units such as College or Basic Education to cover child areas. |
| Department code | Used in imports, RBAC, correction routing, and filters. |
| Department name | Used in lists, reports, and governance screens. |
| Operation branch | Marks the unit as Academic or Administrative. |
| Unit type | Marks the unit as Division, Area, Office, or Other. |
| Active flag | Inactive departments are hidden from operational records. |

Why this matters:

- Faculty monitoring follows faculty department scope.
- Correction approval routing can use department or parent department.
- Deans or principals can be assigned to parent departments to cover child areas.
- Area chairs can be assigned only to the exact area they govern.

Recommended structure:

| Parent division | Child areas |
| --- | --- |
| College | BA, IS/CS, Liberal Arts, Accountancy |
| Basic Education | Elementary, JHS, SHS |
| Graduate Studies | Graduate areas, if needed |

Important rule:

Use the most specific academic department or area that owns the data. Use broad parent departments only when the same person or policy truly covers all child areas.

### 4.4 Programs

Menu:

```text
Admin Portal -> Organization -> Programs
```

Encode:

| Data | Why it is needed |
| --- | --- |
| Tenant, campus, department | Connects the program to the correct academic owner. |
| Program code | Used in sections, students, filters, and imports. |
| Program name | Human-readable program name. |
| Level | Helps describe program level, such as College, SHS, Graduate, or Basic Ed. |
| Active flag | Inactive programs are not used in normal setup. |

Why this matters:

- Sections belong to programs.
- Students may belong to programs.
- Tenant grading profiles may be scoped by program.

### 4.5 Academic Years

Menu:

```text
Admin Portal -> Academics -> Academic Years
```

Encode:

| Data | Why it is needed |
| --- | --- |
| Tenant | Keeps the school year under the right institution. |
| Code | Used by terms, offerings, enrollment, and imports. |
| Name | Human-readable label, such as `AY 2026-2027`. |
| Start and end dates | Defines the academic calendar. |
| Active flag | Inactive academic years are excluded from normal operations. |

### 4.6 Terms

Menu:

```text
Admin Portal -> Academics -> Terms
```

Encode:

| Data | Why it is needed |
| --- | --- |
| Tenant and academic year | Places the term under the correct school year. |
| Term code | Used by offerings, imports, active scope, and grading rules. |
| Term name | Human-readable label, such as `1st Semester`. |
| Term type | Important for Regular, Summer, or Special grading profiles. |
| Sequence number | Sorts terms in the academic year. |
| Start and end dates | Useful for operations and reporting. |
| Active flag | Inactive terms are not used in normal work. |

Important:

Set `Term Type` correctly.

- Use `Regular` for normal semesters.
- Use `Summer` for summer term.
- Use `Special` only for special or bridging terms.

If a Summer term is left as `Regular`, Summer grading profiles may not apply.

### 4.7 Term Grading Periods

Menu:

```text
Admin Portal -> Tools -> Active Grading Period
```

Encode or confirm:

| Data | Why it is needed |
| --- | --- |
| Term | Connects the grading periods to the correct term. |
| Period code | Stable code such as `PRELIM`, `MIDTERM`, `PREFINAL`, or `FX`. |
| Period name | Faculty-facing name such as Prelim, Midterm, Pre-Final, Final Exam. |
| Sequence number | Controls period order. |
| Active flag | Only active periods should be used in operations. |

Why this matters:

- The active grading period tells faculty which period is current.
- Period deadlines and auto-advance depend on the period setup.
- Period names must match the institution's language.

### 4.8 Courses

Menu:

```text
Admin Portal -> Academics -> Courses
```

Encode:

| Data | Why it is needed |
| --- | --- |
| Tenant | Course codes are tenant-level. |
| Campus | Optional in the current design, but helpful for campus-specific course ownership. |
| Department | Academic owner of the course. |
| Course code | Used in imports, offerings, template assignments, and reports. |
| Course title | Faculty and admin readable course name. |
| Units | Used for academic reference and reports. |
| Course type | Useful for lecture/lab profile rules if the institution uses it. |
| Default base value | Optional raw-score transmutation default. |
| Syllabus link | Optional Google Drive or approved external syllabus URL shown only to faculty assigned to that course offering. |
| Active flag | Inactive courses are not offered in normal operations. |

Important:

In the current model, course code is unique per tenant. This is safe when the institution uses one official course catalog across all campuses. If different campuses reuse the same course code with different academic meaning, review the data first before import.

Syllabus links should normally point to school-managed Google Drive files or folders. Keep Google sharing restricted to the institution/domain; TeacherMate+ only controls which assigned faculty can open the link from inside the portal and audits each successful faculty syllabus-link open.

### 4.9 Sections

Menu:

```text
Admin Portal -> Academics -> Sections
```

Encode:

| Data | Why it is needed |
| --- | --- |
| Tenant, campus, department, program | Places the section in the correct academic scope. |
| Section code | Used in offerings, enrollment imports, and reports. |
| Section name | Human-readable section label. |
| Year level | Helps identify the class level. |
| Active flag | Inactive sections are not used in normal offerings. |

Why this matters:

- Course offerings require a section.
- Enrollment imports use section context.
- Section codes may repeat across campuses or programs when scope is supplied correctly.

### 4.10 Course Offerings

Menu:

```text
Admin Portal -> Academics -> Course Offerings
```

Encode:

| Data | Why it is needed |
| --- | --- |
| Tenant and campus | Keeps the class in the correct school/campus. |
| Department | Governance owner of the class for the term. |
| Program | Optional, but useful for program-owned offerings. |
| Academic year and term | Places the offering in the live academic calendar. |
| Course | Subject being offered. |
| Section | Class group taking the course. |
| Room/Office/Lab | Location reference. |
| Schedule text | Faculty and admin reference. |
| Status | Use `OPEN` for live classes. |
| Active flag | Must be active for normal faculty access. |

Why this matters:

- A course offering is the actual class that faculty grade.
- Faculty assignments and enrollments both point to the offering.
- Grade activities, scores, submissions, corrections, attendance, and reports all depend on the offering.

Faculty cannot encode grades without a valid active/open offering.

### 4.11 Users

Menu:

```text
Admin Portal -> Security -> Users
```

Encode:

| Data | Why it is needed |
| --- | --- |
| Username | Login identity. Must be unique. |
| Email | Required for account security and email workflows. Must be unique. |
| First, middle, and last name | Used for faculty selectors, reports, approvals, and audit readability. |
| Default tenant | Initial tenant scope. |
| Default campus | Initial campus scope. |
| Default department | Important for faculty identity and correction routing. |
| Staff flag | Used for admin-style account behavior. |
| Active flag | Inactive users cannot operate normally. |
| Must change password | Recommended for newly issued credentials. |

Why this matters:

- Faculty assignments point to user accounts.
- RBAC checks use the user and their active role assignments.
- Audit logs record the user who performed sensitive actions.

Important:

Do not use one shared faculty account for many teachers. Each person must have their own account so the audit trail remains useful.

### 4.12 Roles, Permissions, and User Roles

Menus:

```text
Admin Portal -> Security -> Roles
Admin Portal -> Security -> Users -> Roles
```

Encode:

| Data | Why it is needed |
| --- | --- |
| Role code and name | Defines a job function such as Faculty, AC, Dean, Registrar, Campus Admin, Tenant Admin. |
| Role permissions | Controls what actions the role can perform. |
| User role assignment | Grants the role to the user. |
| Tenant scope | Limits the role to an institution. |
| Campus scope | Limits the role to a campus. |
| Department scope | Limits the role to an academic area. |
| Active flag | Only active role assignments grant access. |

Required access examples:

| User type | Needed role or permission |
| --- | --- |
| Admin user | `admin_portal.access` plus admin permissions |
| Faculty user | `faculty_portal.access` through the `FACULTY` role |
| AC/Dean/CAO monitor | Admin/governance role with monitor permissions and correct scope |
| Gradebook identity reviewer | `gradebook.view_student_identity`, only when officially authorized |
| Actual Data Reset operator | `actual_data_reset.run`, usually only superadmin or trusted admin |

Important rule:

Use separate roles for teaching and governance.

Example:

- `FACULTY` role gives Faculty Portal class access.
- `AC`, `COLLEGE_DEAN` (or legacy `DEAN`), `CAO`, or similar admin role gives Admin Portal monitoring access.

A person may have both, but Admin Portal visibility should come from the active admin/governance role, not from the teaching role.

### 4.13 Faculty Assignments

Menu:

```text
Admin Portal -> Academics -> Faculty Assignments
```

Encode:

| Data | Why it is needed |
| --- | --- |
| Offering | The class being assigned. |
| Faculty user | The teacher who will handle the class. |
| Assignment note | Optional instructions for the faculty member. |
| Primary flag | Identifies the lead faculty load when needed. |
| Response due date | Used for acceptance reminders and expiration. |
| Response status | Pending, accepted, declined, clarification requested, or expired. |
| Active flag | Inactive assignments do not grant normal class access. |

Why this matters:

- Faculty Portal `My Classes` is based on active faculty assignments.
- Accepted assignments are the official classes ready for grading.
- Non-compliance monitoring focuses on accepted assignments.

Faculty can start the normal grading workflow only after the assignment is accepted, unless the superadmin is testing with special access.

### 4.14 Students

Menu:

```text
Admin Portal -> Students
```

Encode:

| Data | Why it is needed |
| --- | --- |
| Tenant and campus | Student numbers are unique by tenant and campus. |
| Department | Academic owner for scope and filtering. |
| Program | Program of the student, if applicable. |
| Student number | Official student identifier. |
| Last, first, and middle name | Used in class lists and grade reports. |
| Official email | Required later for Student Portal provisioning. |
| Official email verified date | Shows that the email has been validated. |
| Sex | Optional demographic record when used by the institution. |
| Year level | Used in student lists and yearly updates. |
| Status | Active, inactive, graduated, dropped, or withdrawn. |
| Active flag | Only active students count in normal class lists. |

Why this matters:

- Enrollments need existing students unless the tenant allows enrollment import auto-create.
- Faculty class lists and grade encoding depend on enrollment records that point to student records.
- Student identity is campus-scoped, so the same student number can exist in another campus.

### 4.15 Enrollments

Menu:

```text
Admin Portal -> Enrollment
Admin Portal -> Tools -> Bulk Imports
```

Encode:

| Data | Why it is needed |
| --- | --- |
| Tenant and campus | Prevents cross-campus enrollment errors. |
| Academic year and term | Places the student in the correct academic period. |
| Course offering | The class where the student belongs. |
| Student | The enrolled student. |
| Enrollment status | Active, DRP, W, or INC. |
| Encoded by user | Audit reference. |
| Encoded via portal | Shows whether Admin or Faculty encoded the row. |
| Active flag | Inactive enrollments are excluded from normal counts. |

Why this matters:

- Faculty encode scores for enrolled students.
- Active students are required for grade readiness counts.
- `DRP`, `W`, and `INC` are treated differently from active students in final clearance and grade requirements.

## 5. Grading Setup to Encode

### 5.1 Grading Templates

Menu:

```text
Admin Portal -> Grading -> Grading Templates
```

Encode:

| Data | Why it is needed |
| --- | --- |
| Tenant | Owner institution. |
| Template code and name | Identifies the grading structure. |
| Default base value | Default transmutation base, often 50. |
| Passing grade threshold | Optional template-level passing rule. |
| Periods | Prelim, Midterm, Pre-Final, Final Exam, or other official periods. |
| Components | Major grading buckets such as Exam or Class Standing. |
| Subcomponents and details | More detailed grading structure. |
| Weights | Defines how scores are computed. |
| Score input mode | Raw Base-50 or direct percentage. |
| Approval and publish status | Only published templates are used for live classes. |

Why this matters:

- Faculty activity creation follows the template structure.
- Summary computation follows the template weights.
- A missing template blocks normal grade setup for the class.

Operational rule:

Do not let faculty begin live encoding until the needed templates are published and assigned.

### 5.2 Course Template Assignments

Menu:

```text
Admin Portal -> Grading -> Course Template Assignments
```

Encode:

| Data | Why it is needed |
| --- | --- |
| Course | Course that will use the template. |
| Grading template | Published template for the course. |
| Effective term | Optional term-specific assignment. Leave blank for a general assignment. |
| Active flag | Only active assignments are used. |

Why this matters:

TeacherMate+ checks course template assignments before using profile fallback rules.

Template resolution order:

1. Course template assignment for the exact term.
2. Course template assignment with no term.
3. Matching tenant grading profile.
4. Latest published tenant template fallback.

Recommended:

- Assign templates directly to courses whenever possible.
- Use the missing-template coverage monitor before opening classes.

### 5.3 Tenant Grading Profiles

Menu:

```text
Admin Portal -> Grading -> Tenant Grading Profiles
```

Encode:

| Data | Why it is needed |
| --- | --- |
| Tenant | Owner institution. |
| Campus | Optional campus-specific rule. |
| Department | Optional department-specific rule. |
| Program | Optional program-specific rule. |
| Course | Optional exact course rule. |
| Course type | Optional lecture/lab style rule. |
| Applicable term type | Regular, Summer, Special, or blank for all terms. |
| Profile code and name | Identifies the rule. |
| Grading template | Fallback template when course assignment is missing. |
| Default base value | Optional transmutation base rule. |
| Passing grade threshold | Optional passing rule, usually 75.00. |
| Final grade formula mode | Average all active periods or weighted selected periods. |
| Final grade period weights | Required only for weighted selected periods. |
| Priority | Lower number wins when specificity is tied. |
| Effective from term | Use only for exact-term rules. |
| Is default | Marks the normal fallback profile. |
| Active flag | Only active profiles are used. |

Why this matters:

- The profile controls final-grade formula, passing threshold, and fallback template selection.
- Regular and Summer formulas can be different.
- Profiles can support campus, department, program, course, or course-type exceptions.

Recommended default:

- Create one broad Regular default profile.
- Create one broad Summer default profile if Summer uses a different term type.
- Add more profiles only when a campus, department, program, course, course type, base value, passing threshold, or final-grade rule is different.

### 5.4 Period Locks and Deadlines

Menu:

```text
Admin Portal -> Grading -> Period Locks
```

Encode:

| Data | Why it is needed |
| --- | --- |
| Tenant, campus, academic year, term | Defines the deadline scope. |
| Period code | Connects the deadline to a grading period. |
| Scope type | Campus-wide or one course offering. |
| Course offering | Required only for offering-specific deadlines. |
| Deadline date and time | Compliance checkpoint and reminder trigger. |
| Locked flag | Used for admin lock control when needed. |
| Active flag | Inactive rules are ignored. |
| Remarks | Audit and policy reference. |

Current policy behavior:

- The deadline is a compliance checkpoint.
- An overdue unsubmitted gradebook remains open until the faculty submits.
- Overdue classes appear in `Non-Compliance on Periodic Grades Submission`.
- Correction workflow is for submitted gradebooks that need changes, not for unfinished unsubmitted gradebooks.

## 6. Tenant Configuration to Set

Most tenant settings are managed here:

```text
Admin Portal -> Tools -> Configuration Management
```

### 6.1 Basic Academic Settings

Set or confirm:

| Setting area | Recommended production decision |
| --- | --- |
| Active Academic Scope | Set the current academic year and term before faculty login. |
| Active Grading Period | Set the current period per campus and term. |
| Passing grade threshold | Use the official institutional threshold, commonly 75. |
| School name and address | Set for tenant-specific printed documents. |

Why:

- Faculty Portal class access follows the active academic year and term.
- Active grading period affects faculty workflow focus.
- Passing threshold affects analytics, prediction, and pass/fail displays.

### 6.2 Class Master List and Enrollment Settings

Set or confirm:

| Setting | Options | Recommendation |
| --- | --- | --- |
| Class master list ownership mode | Admin-only or faculty-allowed | Use Admin-only for stricter production control. |
| Enrollment import student handling | Require existing students or auto-create missing students | Use Require existing students for cleaner go-live unless the institution has approved auto-create. |
| Faculty DRP allowed through | Depends on configured period options | Set according to registrar policy. |
| Class override mode | Per selected offering | Use only for exceptions. |

Why:

- Enrollment is sensitive because it controls who appears in a faculty gradebook.
- Strict student mode prevents accidental student creation from misspelled import rows.
- Faculty roster maintenance should match registrar policy.

### 6.3 Faculty Assignment Workflow

Set or confirm:

| Setting | Recommendation |
| --- | --- |
| Assignment reminders | Enable if the institution wants automated follow-up. |
| Automatic expiration | Enable if pending load acceptance should expire. |
| Primary default behavior | Enable if most new assignments should be primary by default. |
| Response window days | Set the number of days faculty have to accept or respond. |
| First reminder and repeat reminder days | Set based on the institution's load confirmation schedule. |

Why:

- Faculty cannot reliably start grading if assignments remain pending or expired.
- Acceptance creates a clear record that the faculty acknowledged the load.

### 6.4 Correction Governance

Menus:

```text
Admin Portal -> Tools -> Correction Governance
Admin Portal -> Tools -> Configuration Management
```

Set or confirm:

| Setting | Meaning |
| --- | --- |
| Correction process mode | `SYSTEM_REQUEST` enables in-portal correction petitions. `MANUAL_ONLY` disables faculty in-portal filing. |
| Correction approval routes | Defines who reviews corrections by faculty department. |
| Step 1 and final approver roles | Supports direct or two-step approval. |
| Same-department requirement | Forces approver scope to match the faculty department when needed. |
| Official correction PDF | Enables printable registrar reference for approved corrections. |
| Correction submission emails | Sends notices to selected approval roles. |
| Registrar auto-email | Emails official correction PDF to configured registrar recipients after final approval. |

Recommended:

- Decide the correction policy before live grading.
- Configure a tenant default route.
- Add department-specific routes only when a department has a different approval path.
- Keep `MANUAL_ONLY` if the institution still requires paper forms.
- Use `SYSTEM_REQUEST` if TeacherMate+ will manage correction petitions and approvals.

### 6.5 Template Governance

Menu:

```text
Admin Portal -> Tools -> Template Governance
```

Set or confirm:

| Setting | Meaning |
| --- | --- |
| Draft roles | Who can create and edit draft templates. |
| Submit roles | Who can submit templates for approval. |
| Review roles | Who can approve or reject templates. |
| Publish roles | Who can publish approved templates. |
| Hotfix request roles | Who can request changes to a published template. |
| Hotfix review/apply roles | Who can approve and apply template hotfixes. |
| Sequential approval | Requires review first, then final approval. |
| Sequential hotfix | Requires hotfix review first, then final apply. |
| Same-user safeguards | Controls whether the same user can perform multiple governance steps. |

Why:

- Published templates directly affect official grade computation.
- Hotfixes can affect active classes, so they require strong governance.
- Template changes must stay auditable.

### 6.6 Login Security

Menu:

```text
Admin Portal -> Tools -> Configuration Management -> Login Security
```

Set or confirm:

| Setting | Recommendation |
| --- | --- |
| Login lockout | Enable for production. |
| Maximum failed attempts | Use a practical limit such as 5. |
| Failure counting window | Use a short window such as 15 minutes. |
| Lockout duration | Use a clear temporary lockout duration. |
| Email OTP | Enable only when SMTP is stable and users have correct emails. |
| OTP expiry | Keep short, such as 10 minutes. |
| Session timeout | Use the institution's security policy. |
| Single-device session | Keep enabled in production if policy allows. |

Why:

- Admin and Faculty Portal accounts handle sensitive academic records.
- Email OTP depends on reliable email addresses and SMTP.

### 6.7 Faculty Grade Visibility

Menu:

```text
Admin Portal -> Tools -> Configuration Management
```

Set or confirm:

| Setting | Meaning |
| --- | --- |
| Restrict official periodic grades until period deadline | Faculty computed period grades remain hidden until deadline passes. |
| Restrict periodic grade visibility until submission | Faculty computed period grades remain hidden until the period gradebook is submitted. |
| Restrict official final grade until final deadline | Faculty final grade remains hidden until the final period deadline passes. |

Recommendation:

- For a smoother faculty review workflow, allow computed period grades before submission unless policy requires hiding them.
- For stricter governance, enable release restrictions.

### 6.8 Grade Prediction

Set or confirm:

| Setting | Meaning |
| --- | --- |
| Enable grade prediction module | Turns unofficial projections on or off. |
| Roles allowed to access prediction | Limits who can open prediction pages. |
| Enable what-if simulator | Allows scenario testing. |
| Roles allowed for what-if | Limits simulation access. |
| At-risk flags | Highlights students projected below the passing line. |
| Best-case, worst-case, target-needed | Controls visible prediction columns. |
| Default assumption | Controls how missing scores are treated in projections. |

Recommendation:

- Keep prediction disabled until official grading setup is stable.
- Enable it later for faculty support and academic monitoring.
- Remind users that prediction is unofficial and does not write grades.

### 6.9 User Signatures

Set or confirm:

| Setting | Meaning |
| --- | --- |
| Enable encrypted user signatures | Allows users to upload stored signatures. |
| Allow signatures on Faculty Final Clearance | Places faculty signature on final clearance PDFs. |
| Allow signatures on Correction Official Report | Places requester and approver signatures on correction PDFs. |

Important:

- Production should use a stable `SIGNATURE_ENCRYPTION_KEY`.
- If this key changes, existing encrypted signatures may no longer decrypt.
- See `docs/SIGNATURE_ENCRYPTION_MIGRATION.md`.

### 6.10 Submission Non-Compliance Notices

Set or confirm:

| Setting | Meaning |
| --- | --- |
| Enable non-compliance notices | Allows notices for overdue unsubmitted periodic grades. |
| Notice interval days | Controls when the next stage is due. |
| Head role recipients | Academic heads who receive escalation. |
| HR recipients | Optional HR emails for escalation policy. |

Recommended scheduled job:

```bash
python manage.py issue_submission_non_compliance_notices
```

Run it daily. The setting controls whether a new notice is due.

## 7. Detailed Workflow: Manual Encoding

Use this when setting up through Admin Portal screens.

### Step 1. Confirm production is ready

1. Log in as superadmin.
2. Confirm `/admin-portal/` loads.
3. Confirm `/faculty/` loads.
4. Run:

```bash
python manage.py check
```

5. Confirm backups are ready.

### Step 2. Create organization structure

1. Create tenant.
2. Create campuses.
3. Create parent departments.
4. Create child departments or academic areas.
5. Create programs.
6. Confirm every needed record is active.

Check:

- No production record uses test names.
- Department parent-child setup reflects real governance.
- Programs are under the correct campus and department.

### Step 3. Create security structure

1. Review system roles.
2. Create institution-specific roles if needed.
3. Assign permissions to roles.
4. Create admin users.
5. Create faculty users.
6. Assign scoped user roles.

Check:

- Admin users have `admin_portal.access`.
- Faculty users have `faculty_portal.access`.
- AC, Dean, CAO, Registrar, and Campus Admin roles have only the needed permissions.
- Department scope is correct.
- Faculty teaching role and admin governance role are separate when the same person has both duties.

### Step 4. Create academic calendar

1. Create academic year.
2. Create terms.
3. Set each term type correctly.
4. Create or confirm term grading periods.
5. Set active academic year and term.
6. Set active grading period per campus.

Check:

- Faculty Portal topbar shows the correct active academic year and term.
- Active grading period matches the current school schedule.
- Summer is marked as `Summer`.

### Step 5. Create courses and sections

1. Create course masters.
2. Create section masters.
3. Confirm each course has correct department ownership.
4. Confirm each section has correct campus, department, and program.

Check:

- Course codes are official.
- Section codes match the registrar or SIS source.
- No inactive department/program is used.

### Step 6. Create course offerings

1. Select tenant and campus.
2. Select academic year and term.
3. Select department, program, course, and section.
4. Add room or schedule if available.
5. Set status to `OPEN`.
6. Keep active flag on.

Check:

- The offering is in the active term.
- The offering department is the correct governance owner.
- Select the campus first, then choose only a department from that campus and a program from that department.
- Duplicate-looking offerings are not created.

### Step 7. Create grading templates

1. Create template header.
2. Add periods.
3. Add major components.
4. Add subcomponents and details.
5. Confirm weights.
6. Test the template using the template calculator.
7. Submit, approve, and publish according to governance.

Check:

- Period codes match the official period codes.
- Weights total correctly.
- Template is published before live use.

### Step 8. Assign templates and profiles

1. Open Course Template Assignments.
2. Assign published templates to courses.
3. Use bulk assignment for many courses with the same template.
4. Open Tenant Grading Profiles.
5. Create Regular profile.
6. Create Summer profile if needed.
7. Set passing threshold and final-grade formula.
8. Activate profiles.

Check:

- No current offering is missing template coverage.
- Regular and Summer rules match institutional policy.
- Weighted selected periods use exact period codes.

### Step 9. Create students

1. Create student master records or import them.
2. Confirm tenant, campus, department, program, and student number.
3. Set status to Active for current students.
4. Add official email when Student Portal provisioning is planned.

Check:

- Student numbers match the official source.
- Duplicate student numbers are checked per campus.
- Program/year level is current.

### Step 10. Assign faculty

1. Open Faculty Assignments.
2. Select the offering.
3. Select the faculty user.
4. Add assignment instructions if needed.
5. Mark primary when appropriate.
6. Save.
7. Ask the faculty member to accept the assignment in Faculty Portal.

Check:

- Faculty account is active.
- Faculty has the `FACULTY` role.
- Assignment status becomes `ACCEPTED` before live grading.

### Step 11. Enroll students

1. Open Enrollment or Bulk Imports.
2. Select the target offering.
3. Add students.
4. Use `ACTIVE` for normal students.
5. Use `DRP`, `W`, or `INC` only when the registrar policy applies.

Check:

- Enrollment tenant and campus match the student and offering.
- Student is not enrolled twice in the same offering.
- Faculty class list shows the correct students.

### Step 12. Configure deadlines

1. Open Period Locks.
2. Choose campus, academic year, term, and period.
3. Choose campus-wide or course-offering scope.
4. Set deadline date/time.
5. Activate the rule.

Check:

- Deadline period code matches an actual grading-template period.
- Deadline policy is communicated to faculty.
- Non-compliance monitor shows overdue unsubmitted classes after deadline.

### Step 13. Final readiness check

Before telling faculty to encode grades, check:

| Area | Required result |
| --- | --- |
| Tenant/campus | Active and correct. |
| Department/program | Active and correctly scoped. |
| Active academic scope | Correct AY and term. |
| Active grading period | Correct current period per campus. |
| Offering | Active and `OPEN`. |
| Faculty assignment | Active and accepted. |
| Enrollment | Active students are loaded. |
| Template | Published and assigned. |
| Tenant grading profile | Active and resolves correct final-grade rule. |
| Deadline | Set if required by policy. |
| Permissions | Faculty and admin menus appear correctly. |

## 8. Detailed Workflow: Bulk Import

Use bulk import when many records must be loaded.

Menu:

```text
Admin Portal -> Tools -> Bulk Imports
```

Recommended import order:

1. Sections
2. Courses
3. Students
4. Course offerings
5. Faculty assignments
6. Enrollment

Important rules:

- Download the system-generated CSV template for each import type.
- Do not reuse old or manually guessed headers.
- Upload first and review validation results.
- Fix row-level errors before confirming.
- Confirm only when all critical rows are clean.

Student import:

- Use `CREATE`, `UPDATE`, or `UPSERT` in `row_action`.
- Use it before enrollment imports when student master data is missing or needs yearly updates.
- Update rows can refresh year level, program, status, active flag, and official email.

Enrollment import:

- `STRICT_EXISTING` mode requires students to exist first.
- `AUTO_CREATE` mode can create missing students from enrollment CSV name columns after the offering is resolved.
- Use strict mode for production unless the institution has approved auto-create.

Course offering import:

- Requires correct tenant, campus, term, course, and section references.
- Can infer offering department from selected section or course when safe.
- Inactive matching sections must be reactivated instead of silently recreated.

Faculty assignment import:

- Run only after course offerings and faculty user accounts exist.
- Faculty must still accept the assignment if acceptance workflow is enabled.

## 9. When Faculty Can Start Encoding Grades

Faculty are ready to start when all of these are true:

1. Faculty account is active.
2. Faculty has active `FACULTY` role with correct tenant/campus/department scope.
3. Faculty assignment exists for the offering.
4. Faculty assignment is active.
5. Faculty assignment is accepted.
6. Course offering is active and `OPEN`.
7. Offering belongs to the active academic year and term.
8. Students are actively enrolled in the offering.
9. Published grading template can be resolved.
10. Tenant grading profile can resolve passing threshold and final-grade formula.
11. Active grading period is set for the campus and term.
12. Period deadline is configured if required.

If faculty do not see a class:

- Check active academic year and term.
- Check offering status and active flag.
- Check faculty assignment status.
- Check faculty role and scope.

If faculty see the class but cannot proceed normally:

- Check template coverage.
- Check whether the template is published.
- Check course template assignment.
- Check tenant grading profile.
- Check active grading period.

If students are missing:

- Check enrollment records.
- Check enrollment status.
- Check tenant/campus match between student and offering.

## 10. Governance Rules to Follow

### 10.1 Tenant and Campus Scope

Every operational record must stay inside its tenant and campus.

Do not mix:

- students from one campus into another campus offering
- faculty assignments across the wrong campus
- departments from another campus
- terms from another tenant

Why:

Scope mistakes can expose records to the wrong admin or faculty user.

### 10.2 RBAC

Always assign access through roles and permissions.

Best practice:

- Superadmin for emergency and full system setup.
- Tenant admin for institution-wide admin work.
- Campus admin for campus operations.
- Registrar for enrollment, correction, and official records.
- Dean/CAO/AC for monitoring and governance.
- Faculty for teaching workflow only.

Avoid:

- Giving broad permissions to users who only need one campus or department.
- Using superadmin for daily operations.
- Using faculty role for admin monitoring.

### 10.3 Grade Submission

Submission is an official academic action.

Rules:

- Faculty should review summary readiness before submission.
- Submitted gradebooks are protected.
- Reopened gradebooks must follow policy.
- Audit logs should show who submitted and when.

### 10.4 Reopen

Use reopen only for submitted gradebooks that need authorized changes.

Current deadline policy:

- Overdue unsubmitted gradebooks remain open.
- Do not use reopen for a gradebook that was never submitted.
- Use Non-Compliance monitoring for overdue unsubmitted classes.

### 10.5 Corrections

Corrections are for submitted gradebooks.

Choose one mode:

- `MANUAL_ONLY`: paper process outside faculty in-portal filing.
- `SYSTEM_REQUEST`: in-portal correction request, approval route, and optional official correction PDF.

Governance:

- Configure approval routes before go-live.
- Use department-specific routes only when needed.
- Keep attachments and official reports access-controlled.
- Use audit logs for all correction actions.

### 10.6 Template Governance

Templates control computation, so protect them.

Rules:

- Draft and publish access should be limited.
- Published templates should not be changed casually.
- Use hotfix workflow for changes that affect published templates.
- Keep inactive templates/profiles for audit reference instead of deleting them.

### 10.7 Inactive Records

TeacherMate+ uses active/inactive records for safety.

Operational screens normally use active records only.

Use inactive records when:

- a record should no longer be used
- historical reference must remain
- deletion is unsafe because related records exist

Permanent deletion is guarded and should be rare.

### 10.8 Auditability

Sensitive actions must remain traceable:

- role/permission changes
- actual data reset
- template hotfixes
- submissions and reopen
- corrections
- protected downloads
- signature usage
- import uploads

Keep individual user accounts. Do not share logins.

## 11. Production Go-Live Checklist for Institution Data

Use this before opening the system to all faculty.

### Server and system

- Production site opens by HTTPS.
- `python manage.py check` passes.
- Migrations are applied.
- Static files load.
- Logs are written.
- Backups are running.
- SMTP is tested if email features are enabled.
- Cron or scheduled jobs are configured.

### Institution setup

- Tenant is active.
- Campuses are active.
- Departments and programs are active.
- Department hierarchy is correct.
- Users are active.
- Roles and permissions are correct.
- Faculty have `FACULTY` role.
- Admin/governance users have correct scoped roles.

### Academic setup

- Academic year is active.
- Terms are active and have correct term type.
- Active academic year and term are set.
- Term grading periods are correct.
- Active grading period is set per campus.
- Courses are active.
- Sections are active.
- Course offerings are active and `OPEN`.

### Grading setup

- Grading templates are complete.
- Templates are approved and published.
- Course template assignments are active.
- Missing template coverage is reviewed.
- Tenant grading profiles are active.
- Passing threshold and final formula are correct.
- Period deadlines are configured if required.

### Class readiness

- Faculty assignments are active.
- Faculty assignments are accepted.
- Students are active.
- Enrollments are active and correct.
- One sample faculty class opens.
- Activities page opens.
- Score encoding page opens.
- Summary page opens.
- Submission readiness can be checked.

### Governance

- Correction mode is configured.
- Correction approval routes are configured.
- Template governance is configured.
- Login security settings are configured.
- Non-compliance notice policy is configured if used.
- Actual Data Reset is disabled or tightly controlled in production.

## 12. Recommended First Live Test

Before full opening, test one real or pilot class.

1. Log in as admin.
2. Confirm tenant/campus scope.
3. Open the course offering.
4. Confirm faculty assignment is accepted.
5. Confirm students are enrolled.
6. Confirm template coverage.
7. Log in as the faculty member.
8. Open My Classes.
9. Open the class.
10. Create one sample activity in the current period.
11. Encode sample scores.
12. Open Summary.
13. Confirm computation is correct.
14. Do not submit unless this is an approved real test.

If this pilot class works, repeat the same check for each campus and each major grading-template family.

## 13. Common Setup Problems

| Problem | Likely cause | Fix |
| --- | --- | --- |
| Faculty cannot log in | Missing `faculty_portal.access`, inactive user, wrong password, lockout, or OTP email issue | Check user, role, login security, and email. |
| Faculty logs in but sees no class | Assignment missing, pending, inactive, expired, wrong active AY/term, or offering not open | Check assignment, active scope, and offering status. |
| Class appears but template warning shows | Missing active published course-template assignment | Assign a published template or fix profile fallback. |
| Students missing from class list | Enrollment missing, inactive, wrong campus, or wrong offering | Check enrollment records and tenant/campus match. |
| Summer formula is wrong | Term type is still Regular or Summer profile is inactive/missing | Set term type to Summer and activate Summer profile. |
| AC/Dean cannot see faculty monitor | Admin role inactive or wrong campus/department scope | Assign active governance role with correct scope. |
| Student names are masked in monitor | User lacks `gradebook.view_student_identity` | Grant only if authorized by policy. |
| Overdue class is still editable | This is expected for unsubmitted gradebooks | Monitor it as non-compliance; do not treat as correction. |
| Correction workflow missing | Correction mode is Manual Only or permissions are missing | Check Correction Governance and role permissions. |

## 14. Simple Mental Model

Think of TeacherMate+ setup as a chain.

```text
Tenant
  -> Campus
    -> Department and Program
      -> Academic Year and Term
        -> Course and Section
          -> Course Offering
            -> Faculty Assignment
            -> Enrollment
            -> Grading Template and Profile
              -> Activities, Scores, Summary, Submission
```

If a link in the chain is missing or inactive, faculty grading will be blocked or incomplete.

For production, the safest rule is simple:

Set up the institution carefully, verify one real class end to end, then open the portal to all faculty.

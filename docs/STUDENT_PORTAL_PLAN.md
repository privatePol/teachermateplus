# Student Portal Plan

This plan defines the next-stage Student Portal for TeacherMate+. The Student Portal should let students securely view their own enrollment, grades, attendance, and profile information while preserving the existing multi-tenant and multi-campus governance model.

## Terminology

Use these names consistently:

| Term | Meaning |
| --- | --- |
| Student Portal | The user-facing portal students log into |
| `student_portal` | The Django app that implements the portal |
| Module | A feature area inside the portal, such as Grades or Attendance |

Recommended app path:

```text
apps/student_portal/
```

Recommended URL prefix:

```text
/student/
```

## Primary Goals

1. Allow students to log in using their own account.
2. Show only records owned by the logged-in student's tenant and linked student record.
3. Display official grades only according to configured release rules.
4. Keep the portal read-only for V1 unless a future workflow explicitly allows student requests.
5. Reuse existing setup data, grading data, attendance data, and security controls.

## Non-Goals For Initial Version

- Student grade editing
- Student enrollment self-service
- Online payment, billing, or accounting
- Parent or guardian accounts
- SIS replacement
- Public grade lookup by student number

## Required Schema Addition

The current schema already has users, students, enrollments, course offerings, period grades, final grades, and attendance records. The missing bridge is an explicit account-to-student link.

Recommended model:

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

```text
student.tenant must match tenant
student.campus must match campus
one active user link per student
one active student link per user
```

This separate link model is preferred over adding `user` directly to `Student` because it is easier to audit, deactivate, relink, and extend later for account recovery or external identity providers.

## Student Registration And Account Provisioning

Students should not freely self-register by typing a student number and any email address. Student Portal accounts expose sensitive grades and attendance, so account creation must start from an existing tenant-owned `Student` record.

Recommended V1 flow:

1. Admin creates or imports the `Student` record.
2. Admin verifies that the student record has the correct registered email address.
3. Admin opens Student Account Links or Student Portal Account Provisioning.
4. Admin selects the student record.
5. TeacherMate+ shows the email destination that will receive the activation/invitation.
6. Admin confirms account provisioning.
7. TeacherMate+ creates or links the `User` account and creates an active `StudentAccountLink`.
8. TeacherMate+ sends an activation link or temporary credential to the registered student email.
9. Student logs in, sets a password, and accepts privacy consent.
10. Student Portal access starts only after the active `StudentAccountLink` is in place.

### Email Destination Policy

For V1, invitations should be sent only to the email address registered in the student data.

Recommended rule:

```text
Send Student Portal activation/invitation only to the official registered email stored on the Student record.
```

If the `Student` model supports multiple emails, use this priority:

1. `official_email`
2. verified school email
3. validated imported student contact email

If no trusted student email exists, TeacherMate+ should not send a portal invitation automatically. Admin must correct the student record first or issue credentials through an approved manual process.

Students should not be allowed to enter an arbitrary email address during registration and immediately receive access.

Recommended future fields if the current student record does not distinguish emails:

```text
official_email
personal_email
email_verified_at
```

### Optional Future Claim Flow

A self-service claim flow may be added later, behind a feature setting, but it should still verify against existing student data.

Possible claim requirements:

- Tenant/school
- Campus
- Student number
- Date of birth or another private identifier
- Official registered email or mobile number
- One-time code sent only to the official registered contact

Even in the claim flow, the student should not be able to change the destination email before identity is verified.

## Tenant Boundary Rule

Every Student Portal read must start from the authenticated user and resolve the active student link.

Do not query by raw `student_no` from a URL or form.

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

All student-facing queries must include tenant and student filters. Campus should also be included when the model has campus scope.

## Proposed Modules

### 1. Student Dashboard

Purpose:

- Show a simple overview after login.
- Display active academic year and term.
- Show enrolled classes for the active term.
- Show grade/attendance summary cards when data is released.

Data sources:

- `StudentAccountLink`
- `Enrollment`
- `CourseOffering`
- active academic year/term settings

### 2. My Courses

Purpose:

- List enrolled classes grouped by academic year and term.
- Show course code, title, section, campus, faculty, and enrollment status.

Data sources:

- `Enrollment`
- `CourseOffering`
- `Course`
- `Section`
- `FacultyAssignment`

Rules:

- Only show enrollments for the linked student.
- Keep inactive/archived terms readable as history.
- Do not expose other students in the class.

### 3. My Grades

Purpose:

- Show official period grades and final grades.
- Explain grade visibility status clearly.

Data sources:

- `StudentPeriodGrade`
- `StudentFinalGrade`
- `GradeSubmission`
- `GradingTemplatePeriod`
- `TenantGradingProfile`

Initial visibility rule:

```text
Show period grade only when the matching period gradebook is submitted.
Show final grade only when final grade is computed and marked submitted/released.
```

If a tenant needs stricter release control, add a feature setting before exposing grades.

Recommended future setting:

```text
student_portal_period_grades_visible_after_submission
student_portal_final_grades_visible_after_submission
student_portal_show_computation_breakdown
```

### 4. Grade Details

Purpose:

- Let students understand how a visible grade was computed.
- Keep the view read-only.

Possible detail levels:

| Level | Description |
| --- | --- |
| Summary only | Period grade and final grade only |
| Component summary | Major component and exam split |
| Activity detail | Individual activities and raw scores |

Recommended V1 default:

```text
Summary only, with optional component summary behind configuration.
```

Activity-level score visibility should be configurable because some institutions release only official grades.

### 5. My Attendance

Purpose:

- Show attendance summary by course and period.
- Optionally show session-level attendance details.

Data sources:

- `AttendanceSession`
- `AttendanceRecord`
- `Enrollment`

Rules:

- Only records for the linked student.
- Tenant/campus/offering must match the enrollment scope.
- Session-level details should be configurable.

### 6. Profile

Purpose:

- Show student identity and academic information.
- Let students verify their record.

Data sources:

- `Student`
- `Program`
- `Department`
- `Campus`
- `Tenant`

V1 should be read-only. Profile correction requests can be a future module.

### 7. Account Settings

Purpose:

- Password change
- Privacy consent
- Optional quick profile/security summary

Data sources:

- `User`
- existing account security fields

## Permissions And Access

Create a student portal access permission:

```text
student_portal.access
```

Optional future permissions:

```text
student_portal.view_grades
student_portal.view_attendance
student_portal.view_activity_scores
student_portal.view_profile
```

For the first version, access can be controlled by:

1. Active authenticated user
2. Active `StudentAccountLink`
3. `student_portal.access`

## Feature Configuration

Expose Student Portal settings through the existing configurable-features page or a dedicated Student Portal settings section.

Recommended settings:

| Setting | Default | Purpose |
| --- | --- | --- |
| Enable Student Portal | Off | Global rollout control |
| Enable student self-claim | Off | Allows a future verified claim flow |
| Show period grades after submission | On | Controls period grade visibility |
| Show final grade after submission | On | Controls final grade visibility |
| Show activity scores | Off | Controls detailed score visibility |
| Show attendance details | On | Controls session-level attendance |
| Show inactive historical terms | On | Allows old term review |

## URL Sketch

```text
/student/
/student/courses/
/student/courses/<offering_id>/
/student/grades/
/student/grades/<offering_id>/
/student/attendance/
/student/profile/
/student/account/
```

Every detail URL must validate that the offering belongs to an enrollment for the linked student.

## Security Rules

1. Never trust `student_id`, `student_no`, or `offering_id` from the request without checking the active account link.
2. Every query must be tenant-scoped.
3. Every offering detail page must require an enrollment match.
4. Do not show classmate names, scores, rankings, distributions, or grade analytics.
5. Do not expose draft/unsubmitted grades.
6. Audit sensitive reads if final grades or detailed scores are exposed.
7. Student Portal must not reuse Admin or Faculty role scope as the source of student access.

## Suggested Implementation Phases

## Current Stop Point And Release Decision

Student Portal work is paused here for now, but the chosen release approach is to promote it as a dark-launched app instead of keeping it local-only.

Decision:

```text
Promote apps/student_portal/ into repository tracking as a dark-launched app.
```

The `apps/student_portal/` ignore rule has been removed from `.gitignore` so the app can be tracked with the rest of the codebase. This avoids broken imports or missing-app deployments caused by tracked support files referencing an ignored local app.

Dark-launch guardrails:

- `FEATURE_STUDENT_PORTAL_ENABLED` remains Off by default.
- Student Portal routes may exist, but access must be denied unless the tenant explicitly enables the feature.
- Access still requires authentication.
- Access still requires `student_portal.access`.
- Access still requires an active `StudentAccountLink`.
- Student self-registration/self-claim remains unavailable.
- Invitation email sending remains unavailable.
- No student should receive access unless an admin intentionally provisions or links the account.
- Grades and attendance remain read-only.
- Draft grades, reopened/unsubmitted gradebooks, activity scores, classmates, rankings, distributions, and analytics remain hidden.

The dark-launched app currently includes the conservative read-only foundation:

- Student Portal app shell under `apps/student_portal/`
- `StudentAccountLink`
- tenant/campus/student-scoped access resolution
- Admin-driven account-link/provisioning support
- global Student Portal enable/disable setting
- dashboard
- My Courses
- Profile
- Account
- My Grades, submitted/released summary only
- My Attendance, read-only summary with optional session details
- focused tenant/campus/student isolation tests

The remaining items below are deferred and should not be completed in the current release cycle unless explicitly reactivated.

## Deferred Items For Later Release

### Release Hardening

- Keep Student Portal dark-launched with `FEATURE_STUDENT_PORTAL_ENABLED` default Off.
- Re-run full migrations and tests after tracking the app.
- Add any missing production deployment settings, routing checks, and monitoring hooks.
- Confirm production/staging deployments deny `/student/` access when the feature is Off.
- Confirm no public navigation or homepage entry point advertises Student Portal until pilot approval.

### Account Activation And Email

- Add invitation/activation email sending behind an explicit feature setting.
- Send invitations only to trusted student email destinations.
- Add activation-token expiry, resend, and audit handling.
- Add a safe manual credential handoff policy if email is disabled.
- Refine first-login password-change and privacy-consent flow for student users.

### Student Self-Claim

- Keep self-claim disabled by default.
- If added later, require existing student record verification and one-time code delivery only to trusted registered contact data.
- Do not allow a student to type any arbitrary email and immediately receive access.

### Grades Enhancements

- Decide whether students should ever see activity-level scores.
- Add a separate feature setting before exposing activity-level scores.
- Add optional grade computation explanations only after privacy/audit requirements are settled.
- Decide whether final grades need a separate admin release flag beyond `is_submitted`.
- Add audit events for sensitive grade-detail reads if required by school policy.

### Attendance Enhancements

- Decide whether session-level attendance details should remain default On or become default Off for pilot.
- Add optional attendance correction/request workflow only after approval rules are designed.
- Add audit events for attendance-detail reads if required.

### Student-Facing Workflows

- Profile correction requests.
- Student notifications/messages.
- Student help/guide page.
- Student-facing privacy/security copy.
- Parent/guardian access planning, if ever needed, as a separate access model.

### UAT And Documentation

- Add Student Portal UAT scenarios to `docs/ROLE_BASED_UAT_TEST_SCENARIOS.md`.
- Create an Admin guide section for provisioning Student Portal accounts.
- Create a Student guide page for login, grades, attendance, profile, and account security.
- Run pilot smoke tests with sample student users across multiple tenants/campuses.

### Phase 1: Foundation

- Create `apps/student_portal`.
- Add `StudentAccountLink` model and migration.
- Add `student_portal.access` permission.
- Add login routing/menu shell for Student Portal.
- Add account-link admin management in Admin Portal.
- Add service helper to resolve the current student link.

Deliverable:

```text
Student can log in and see a basic dashboard with their own identity.
```

Implementation note for the local prototype:

- The initial foundation has been added in the intentionally ignored `apps/student_portal/` app.
- Access is controlled by authenticated user, active `StudentAccountLink`, `student_portal.access`, and `FEATURE_STUDENT_PORTAL_ENABLED` with default Off behavior.
- The first read-only screens are `/student/`, `/student/courses/`, `/student/courses/<offering_id>/`, `/student/grades/`, `/student/grades/<offering_id>/`, `/student/attendance/`, `/student/profile/`, and `/student/account/`.
- Admin provisioning is currently represented by the `StudentAccountLink` model/service, Django admin registration, and Admin Portal Student Account Links list/create/deactivate pages governed by `student_account_links.manage`.
- `FEATURE_STUDENT_PORTAL_ENABLED`, `FEATURE_STUDENT_PORTAL_PERIOD_GRADES_AFTER_SUBMISSION`, `FEATURE_STUDENT_PORTAL_FINAL_GRADES_AFTER_SUBMISSION`, and `FEATURE_STUDENT_PORTAL_ATTENDANCE_DETAILS_ENABLED` are exposed in Configurable Features under a Student Portal card.
- The Student model now has `official_email` and `official_email_verified_at` for trusted account provisioning. Admin Portal provisioning can create or link a user, grant scoped `student_portal.access`, and create the active `StudentAccountLink` only after the official email is present and verified.
- The grades stage now shows summary-only submitted period grades and submitted final grades for the linked student's owned enrollments. Draft/reopened gradebooks, activity scores, classmates, rankings, distributions, and analytics are not exposed.
- Attendance now shows tenant/campus/student-scoped read-only summary counts by enrolled course, with optional session-level details when enabled.
- Invitation email workflow, student self-claim, grade computation explanations, activity-score details, profile correction requests, and attendance edit/request workflows remain deferred.

### Phase 2: Courses And Profile

- Add My Courses page.
- Add read-only profile page.
- Add tenant/campus/enrollment tests.
- Add negative tests for cross-tenant and cross-student access.

Deliverable:

```text
Student can see only their own enrolled courses and profile.
```

### Phase 3: Grades

- Add My Grades page.
- Enforce submitted/released visibility.
- Add final grade display.
- Add optional computation explanation if configured.

Deliverable:

```text
Student can see official released grades only.
```

Implementation note for the local prototype:

- `/student/grades/` lists the linked student's enrolled courses with submitted period grades and submitted final grade when release settings allow them.
- `/student/grades/<offering_id>/` verifies enrollment ownership before rendering grade details.
- Period grades require a matching `GradeSubmission` in `SUBMITTED` status. Final grades require `StudentFinalGrade.is_submitted=True`.
- The initial grade view is summary-only and intentionally excludes activity-level score details and computation breakdowns.

### Phase 4: Attendance

- Add Attendance summary page.
- Add optional session-level attendance details.
- Add tests for tenant/student/offering filtering.

Deliverable:

```text
Student can review their own attendance records.
```

Implementation note for the local prototype:

- `/student/attendance/` summarizes Present, Late, Absent, and Excused counts for each active owned enrollment.
- Attendance records are filtered through the active `StudentAccountLink`, tenant, campus, student, and owned course offering.
- Session-level rows are shown only when `FEATURE_STUDENT_PORTAL_ATTENDANCE_DETAILS_ENABLED` allows them; otherwise summary counts remain visible.

### Phase 5: Release Controls And Hardening

- Add configurable Student Portal visibility settings.
- Add audit events for grade-detail views if required.
- Add operational guide and UAT scenarios.
- Add student portal smoke tests.

Deliverable:

```text
Student Portal is controlled, documented, and ready for pilot use.
```

## Testing Checklist

### Positive Tests

- Student with active link can open Student Portal.
- Student sees only their own profile.
- Student sees only their own enrollments.
- Student sees submitted grades for their own enrolled courses.
- Student sees attendance only for their own records.

### Negative Tests

- User without `StudentAccountLink` cannot open Student Portal.
- Student cannot access another student's course detail URL.
- Student cannot access another tenant's grade records.
- Student cannot see draft or unsubmitted grades.
- Student cannot see classmate names or scores.
- Inactive student link blocks access.

### Scope Tests

- Same student number in different tenant does not leak records.
- Same student number in different campus does not leak records.
- Historical terms remain read-only.
- Current active term data follows configured release rules.

## Open Decisions

1. Should student usernames be based on student number, email, or generated account IDs?
2. Should students see activity-level scores, or only official period/final grades?
3. Should attendance session details be visible, or only summary counts?
4. Should final grades require an explicit admin release flag beyond submission?
5. Should parents/guardians be planned as a separate future portal or as a linked access type?

## Recommended V1 Position

Build the Student Portal as a separate Django app named `student_portal`, using a dedicated `StudentAccountLink` model. Keep V1 read-only, tenant-scoped, and conservative: students see their own courses, profile, submitted grades, and attendance only. Add feature settings before exposing activity-level scores or any detailed computation breakdown.

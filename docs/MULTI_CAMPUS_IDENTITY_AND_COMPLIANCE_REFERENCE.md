# Multi-Campus Identity and Compliance Reference

This note captures recurring architectural and operational findings already verified in the EduGrade+ codebase, so the same concerns do not need to be rediscovered repeatedly.

It is written as a product-and-operations reference, not as a final design spec.

## 1. Overdue Periodic Submission Behavior

Current verified behavior:

- If a faculty member does not submit a grading period by the deadline, the class period does **not** auto-lock just because it is overdue.
- The period remains open for encoding until the faculty finally submits it.
- The class is marked overdue / non-compliant and can be monitored from the admin side.

Operational meaning:

- deadline = compliance checkpoint
- deadline does not automatically close an unsubmitted grade book
- a separate admin lock/governance action is still possible when needed

## 2. Submission Non-Compliance Notices

Current verified behavior:

- EduGrade+ now supports a staged communication workflow for overdue unsubmitted periodic grades:
  1. `Notice for Non-Compliance`
  2. `Warning for Continued Non-Compliance`
  3. `Escalation for Unresolved Non-Compliance`
- The workflow is tenant-configurable from `Admin Portal -> Tools -> Configuration Management`.
- Faculty can see these communications in `Faculty Reminder Center`.
- Admin/governance users can see the latest notice stage in `Admin Portal -> Grading -> Non-Compliance on Periodic Grades Submission`.

Recommended operations setup:

- schedule `python manage.py issue_submission_non_compliance_notices` **daily**
- let the configured interval decide when the next communication is actually due
- do not schedule it every 3 days directly; daily scheduling is safer and simpler

Useful commands:

```bash
python manage.py issue_submission_non_compliance_notices
python manage.py issue_submission_non_compliance_notices --dry-run
python manage.py issue_submission_non_compliance_notices --tenant-id 1
```

Important note:

- the notice job is read/notify/escalate only
- it does not change grades
- it does not submit grades
- it does not auto-lock the class

## 3. Area Chair / Dean / CAO Scope

Verified scoping model:

- EduGrade+ scope is designed around:
  - tenant
  - campus
  - department
- One person can monitor multiple campus + department combinations by holding multiple scoped role assignments.

Example:

- `AC` + `Fairview` + `BSIS`
- `AC` + `Cubao` + `BSIS`
- `AC` + `Taytay` + `BSIS`

This means the same person may cover several campuses for the same department without needing a special new scope type.

## 4. Collision Risk When Merging Data from Multiple Campuses

### 4.1 Student Number

Current status: **addressed**

Verified model behavior:

- student records are unique by `tenant + campus + student_no`
- the same `student_no` may exist in different campuses under the same tenant

Operational meaning:

- cross-campus student-number collisions are supported
- import logic also checks campus alignment when resolving students

### 4.2 Section Code

Current status: **addressed**

Verified model behavior:

- sections are unique by `tenant + campus + department + program + code`
- the same section code may appear in another campus
- imports already require additional context when a section code is ambiguous across programs

Operational meaning:

- section-code reuse across campuses is supported
- section-code reuse inside one campus is also manageable when campus/department/program are supplied correctly

### 4.3 Course Offering

Current status: **addressed**

Verified model behavior:

- course offerings are unique by `tenant + campus + department + term + course + section`

Operational meaning:

- merged offering imports are safe as long as campus, term, course, and section references are correct

### 4.4 Enrollment

Current status: **addressed**

Verified model behavior:

- enrollments are unique by `course_offering + student`

Operational meaning:

- duplicate enrollment rows for the same student in the same offering are blocked

### 4.5 Faculty Number / Faculty Identity

Current status: **not fully addressed**

Important finding:

- EduGrade+ currently uses global `User.username` and global `User.email`
- both are unique across the whole system database
- there is no separate campus-scoped `faculty_no` identity field yet

Operational meaning:

- if the same real faculty member works across multiple campuses, current design is fine
- if different faculty members in different campuses may share the same faculty number, and that faculty number is used as login identity, current design is risky

Recommendation:

- do not treat `username` as the long-term business faculty number for all tenants
- introduce an explicit campus-aware faculty identifier in a future product hardening phase

## 5. Course Code Risk Across Tenants

Current model behavior:

- `Course.code` is unique by `tenant`, not by campus

What this means:

- for tenants like NCBA, this may be acceptable if course codes are institution-wide canonical
- for other tenants, this can be risky if the same course code may mean different things in different campuses

### 5.1 When Current Design Is Fine

Keep the current rule if:

- one tenant has a single canonical academic meaning for each course code
- the same `course_code` means the same subject across all campuses
- differences are only in schedule, offering, faculty, or local delivery

### 5.2 When Current Design Is Risky

The current rule becomes risky if:

- Campus A and Campus B both use `ACC101`
- but the title, units, or academic meaning are different

In that case, tenant-wide uniqueness on `Course.code` is too strict and can cause merge conflicts or wrong academic normalization.

## 6. Recommended Product Direction for Course Identity

Recommended long-term product design:

### Canonical Course

Tenant-level academic identity for the course itself.

Suggested responsibilities:

- title
- units
- course type
- normalized academic meaning
- common defaults

### Campus Course Alias (or Campus Course Code Mapping)

Campus-scoped external/business code that maps to the canonical course.

Suggested uniqueness:

- `tenant + campus + code`

Suggested purpose:

- allow campus-specific code reuse
- preserve cross-campus normalization where courses are truly the same
- avoid forcing all tenants into one tenant-wide code policy

### Why this is better than a simple campus-only uniqueness change

If EduGrade+ only changes course uniqueness from:

- `tenant + code`

to:

- `tenant + campus + code`

then collisions are reduced, but academic normalization becomes weaker across campuses.

The two-level model preserves both:

- campus flexibility
- tenant-wide reporting and academic mapping

## 7. Recommended Product Direction for Faculty Identity

Recommended long-term product design:

- keep `username` as technical login identity
- add an explicit faculty business identifier such as `faculty_no`
- scope that identifier by tenant + campus if tenants need that flexibility

This avoids forcing business identity to equal login identity.

## 8. Immediate Practical Guidance

### For NCBA Right Now

Safe assumptions currently supported:

- duplicate student numbers across campuses: acceptable
- duplicate section codes across campuses: acceptable
- duplicate course offerings across campuses: acceptable when imported with proper scope
- area chairs covering multiple campuses for the same department: acceptable through multiple scoped roles

### For Broader Multi-Tenant Readiness

Recommended next hardening priorities:

1. faculty business-identifier refactor
2. course identity refactor (`Canonical Course + Campus Course Alias`)
3. tenant-onboarding collision audit for:
   - course codes
   - faculty identifiers
   - section-code reuse patterns

## 9. Suggested Follow-Up Work Items

1. Add explicit `faculty_no` support separate from login username
2. Design `Canonical Course + Campus Course Alias`
3. Add tenant onboarding audit/report for identifier collisions before bulk import
4. Document whether each tenant follows:
   - canonical tenant-wide course catalog
   - or campus-scoped course code policy
5. Add a formal architecture note before onboarding tenants with non-NCBA academic data rules

## 10. Quick Answers

### If a class is overdue and unsubmitted, is it automatically locked?

No. It remains open unless separately locked by governance/admin action.

### Can one Area Chair monitor multiple campuses for the same department?

Yes. Give the same user multiple scoped role assignments.

### Are duplicate student numbers across campuses safe?

Yes, under the current model.

### Are duplicate section codes across campuses safe?

Yes, under the current model.

### Are duplicate faculty numbers across campuses safe?

Not fully, unless they refer to the same actual user/login identity.

### Is `Course.code` tenant-wide uniqueness safe for every tenant?

No. It is fine for tenants with canonical cross-campus course codes, but risky for tenants with campus-local code reuse.

## 11. Related Design Note

For a more implementation-oriented follow-up, see:

- `docs/COURSE_AND_FACULTY_IDENTITY_REFACTOR_PROPOSAL.md`

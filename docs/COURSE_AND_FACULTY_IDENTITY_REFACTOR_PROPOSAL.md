# Course and Faculty Identity Refactor Proposal

This document turns the multi-campus identity findings into a concrete technical proposal for TeacherMate+.

It is intended for product planning, architecture review, and future implementation scoping.

It does **not** mean the refactor has already been implemented.

## 1. Problem Statement

TeacherMate+ already supports multi-campus tenants, but two identity rules are too rigid for broader tenant diversity:

1. `Course.code` is unique by `tenant`, not by `campus`
2. faculty business identity is effectively tied to global `User.username` / `User.email`

These work for tenants like NCBA when:

- course codes are institution-wide canonical
- the same faculty person may hold multiple campus assignments

But they become risky for tenants where:

- the same course code can mean different things in different campuses
- different faculty members can have the same faculty number in different campuses

## 2. Goals

The refactor should allow TeacherMate+ to support both:

- tenants with strict tenant-wide academic normalization
- tenants with campus-local code reuse and local identity variation

without forking the product model.

## 3. Non-Goals

This proposal does not try to:

- redesign grading formulas
- redesign RBAC scope model
- remove existing `User` login behavior
- change NCBA’s current operating behavior immediately

## 4. Proposed Direction

### 4.1 Course Identity

Introduce a two-level course model:

1. `CanonicalCourse`
2. `CampusCourseAlias`

### 4.2 Faculty Identity

Introduce a business-identity layer for faculty that is separate from login username:

1. keep `accounts.User.username` as technical login identity
2. add a scoped faculty business identifier, either:
   - directly on `User`, or
   - preferably in a dedicated profile/identity table

## 5. Course Model Proposal

### 5.1 CanonicalCourse

Purpose:

- represent the tenant-level academic meaning of a course

Suggested fields:

- `tenant`
- `canonical_code` or internal reference code
- `title`
- `units`
- `course_type`
- `default_base_value`
- `is_active`
- optional metadata such as:
  - curriculum group
  - lecture/lab classification
  - academic family

Suggested uniqueness:

- `tenant + canonical_code`

Important note:

- this `canonical_code` does not have to be the campus-facing course code shown in imports or reports
- it can be an internal stable reference if needed

### 5.2 CampusCourseAlias

Purpose:

- represent the campus-facing or external business code used by offerings/imports
- map a campus-local code to one canonical course

Suggested fields:

- `tenant`
- `campus`
- optional `department`
- `canonical_course`
- `alias_code`
- `alias_title`
- `is_primary_alias`
- `is_active`

Suggested uniqueness:

- `tenant + campus + alias_code`

Optional stricter variant:

- `tenant + campus + department + alias_code`

Use department in uniqueness only if the business truly allows the same alias code in one campus across different departments with different meanings.

### 5.3 Course Offering Impact

Current:

- `CourseOffering.course -> Course`

Recommended future shape:

Option A:
- `CourseOffering.canonical_course`
- optional `CourseOffering.campus_course_alias`

Option B:
- `CourseOffering.course_alias`
- derive `canonical_course` through relation

Recommended:

- keep both available in the offering row once refactor is complete:
  - `canonical_course` for reporting and logic
  - `campus_course_alias` for display, import traceability, and external integration

This avoids repeated join-heavy lookups and keeps downstream logic clearer.

## 6. Faculty Identity Proposal

### 6.1 Why Not Reuse username

`username` should remain a technical login field because:

- logins may need global uniqueness
- usernames may be email-like, generated, or policy-driven
- business faculty number and login name are not always the same thing

### 6.2 Suggested FacultyIdentity Model

Suggested model:

- `FacultyIdentity`

Suggested fields:

- `user`
- `tenant`
- `campus`
- optional `department`
- `faculty_no`
- `employment_status`
- `is_primary_identity`
- `is_active`

Suggested uniqueness:

- `tenant + campus + faculty_no`

Optional stricter variant:

- `tenant + campus + department + faculty_no`

Only use department if the institution truly reuses faculty numbers inside one campus across departments.

### 6.3 Role and Assignment Impact

Current faculty assignment already works by `offering + faculty_user`.

That part can remain.

The new model mainly affects:

- imports
- admin forms
- directory/search
- external integrations
- audit/report display labels

## 7. Recommended Backward-Compatible Rollout

### Phase 1: Additive Schema

Add new models without breaking existing relations:

- `CanonicalCourse`
- `CampusCourseAlias`
- `FacultyIdentity`

Keep current `Course`, `CourseOffering.course`, and `User.username` logic untouched at first.

### Phase 2: Data Migration

For existing tenants like NCBA:

- create one canonical course row for each existing `Course`
- create one campus alias per existing course/campus combination
- generate one faculty identity row per active faculty role scope where needed

For NCBA, migration can be near 1:1 because current behavior is already close to canonical tenant-wide course identity.

### Phase 3: Dual-Read Logic

Update imports and resolution services to read from the new identity layer while still supporting old fields.

Examples:

- offering import resolves `campus_course_alias` first
- reports use canonical course for normalization but alias code for campus-facing display
- faculty import resolves `faculty_no` through `FacultyIdentity`, not `username`

### Phase 4: New Writes Use New Model

Switch:

- import pipeline
- create/edit offering
- faculty assignment import
- SIS/API mappings

to write using the new identity structure first.

### Phase 5: Controlled Decommission

After stable adoption:

- decide whether legacy `Course.code` remains as a convenience field
- decide whether some old import assumptions can be retired

## 8. Import Service Changes Needed

### 8.1 Course Import

Current risk:

- `course_code` assumes tenant-wide uniqueness

Future behavior:

- resolve or create `CampusCourseAlias`
- map that alias to an existing or new `CanonicalCourse`

Import mode options could include:

1. `STRICT_CANONICAL`
2. `ALIAS_TO_EXISTING`
3. `CREATE_NEW_CANONICAL_IF_NEEDED`

### 8.2 Course Offering Import

Current lookup:

- tenant + campus + AY + term + course + section

Future lookup:

- tenant + campus + AY + term + campus_course_alias + section

### 8.3 Faculty Assignment Import

Current lookup:

- `faculty_username` via username/email

Future lookup should support:

- `faculty_no`
- optional fallback to username/email for legacy tenants

Recommended import fields:

- `faculty_identifier_type`
- `faculty_identifier_value`

or more simply:

- `faculty_no`
- optional `faculty_username`

with tenant policy deciding which one is required.

### 8.4 Enrollment Import

Enrollment import is already much safer than the faculty side because student resolution is campus-scoped.

Recommended enhancement:

- keep current campus validation
- add onboarding diagnostics that detect repeated student numbers across campuses and explicitly report that the tenant uses campus-scoped student identity

## 9. Reporting and Analytics Impact

The new course model helps reporting rather than hurting it.

Examples:

- campus operations report can show alias code
- tenant-level analytics can group by canonical course
- cross-campus fail-rate comparisons become more accurate
- template assignment can follow canonical course where desired

Recommended display rule:

- operational campus pages show alias code first
- strategic analytics can group by canonical course

## 10. Tenant Policy Settings to Add Later

Suggested tenant configuration flags:

### Course Catalog Policy

Possible values:

- `TENANT_CANONICAL`
- `CAMPUS_ALIAS_REQUIRED`
- `HYBRID`

### Faculty Identity Policy

Possible values:

- `USERNAME_IS_BUSINESS_ID`
- `FACULTY_NO_REQUIRED`
- `HYBRID`

Important note:

- these settings should control validation and import behavior
- they should not be used as a substitute for the schema refactor itself

## 11. Risks and Tradeoffs

### Benefits

- supports more diverse tenants safely
- reduces campus merge collisions
- preserves cross-campus academic normalization
- separates business identifiers from technical login identifiers

### Costs

- import logic becomes more complex
- admin setup becomes more explicit
- some reports and APIs need dual identifiers
- migration must be done carefully to avoid breaking existing offering references

## 12. Recommendation

Recommended implementation order:

1. `FacultyIdentity`
2. `CanonicalCourse + CampusCourseAlias`
3. import-layer dual support
4. reporting/API updates
5. tenant onboarding diagnostics

Why faculty first:

- duplicate faculty business IDs across campuses are the sharper operational risk because they can block account modeling
- course identity refactor is broader and should be done carefully after the faculty identity pattern is established

## 13. Suggested Next Engineering Deliverables

1. schema RFC for:
   - `FacultyIdentity`
   - `CanonicalCourse`
   - `CampusCourseAlias`
2. migration mapping plan from current `Course`
3. import-service impact assessment
4. reporting/API impact list
5. tenant onboarding collision-audit command

## 14. Recommended Product Position

TeacherMate+ should support:

- strict canonical tenants like NCBA
- more flexible multi-campus tenants with local code reuse

The product should not force all tenants into NCBA’s current assumptions just because NCBA is the first or primary operating pattern.

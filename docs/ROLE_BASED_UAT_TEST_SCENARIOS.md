# Role-Based UAT Test Scenarios

Use these scenarios to verify EduGradesPro role scope, campus scope, department/area scope, Faculty Portal access, Admin Portal monitoring, gradebook visibility, analytics visibility, and correction approval routing.

These scenarios are written for NCBA multi-campus testing across:

- Cubao
- Fairview
- Taytay

And for these academic areas:

- Business Administration / BA / BSBA
- Information Systems / IS / BSIS
- Computer Science / CS / BSCS
- IS/CS combined governance area, if configured as one parent/area scope

## General Test Rules

Before testing, confirm:

- Active Academic Year and Term are configured.
- Departments/areas are active.
- Programs are active.
- Courses, sections, course offerings, faculty assignments, enrollments, and grading templates are active.
- Faculty assignments are accepted where gradebook testing is needed.
- Each tested user has a known password and can log in.
- Each tested user has only the intended roles/scopes unless the scenario says otherwise.

When testing scope, always include both:

- Positive checks: user can access expected records.
- Negative checks: user cannot access records outside assigned campus/area scope.

## Common Test Data Needed

Create or verify course offerings for the active AY/Term:

| Campus | Area | Program | Example Faculty | Expected Visible To |
|---|---|---|---|---|
| Cubao | BA | BSBA | BA Cubao Faculty | User D |
| Fairview | BA | BSBA | F2 | User D, User A2 |
| Taytay | BA | BSBA | BA Taytay Faculty | User D |
| Cubao | IS | BSIS | F1 | User A1, F1 |
| Fairview | IS | BSIS | F1 | User A1, F1 |
| Taytay | CS | BSCS | F3 | User A1, F3 |
| Cubao | Non-BA/Non-ISCS | Any | Other Faculty | Not visible to User D/A1/A2 unless separately scoped |

Each offering should have:

- Section
- Course
- Published grading template assignment
- Active enrolled students
- Faculty assignment
- At least one grade activity and score where analytics/gradebook testing is needed

## User Matrix

| User | Admin Role | Faculty Role | Campus Scope | Area/Department Scope |
|---|---|---|---|---|
| User D | Dean | Faculty | Cubao, Fairview, Taytay | BA |
| User A1 | Area Chairman | Faculty | Cubao, Fairview, Taytay | IS/CS |
| User A2 | Area Chairman | Faculty | Fairview | BA |
| User F1 | None or faculty-only admin access if applicable | Faculty | Fairview, Cubao | IS |
| User F2 | None or faculty-only admin access if applicable | Faculty | Fairview | BA |
| User F3 | None or faculty-only admin access if applicable | Faculty | Taytay | CS |

## User D: BA Dean Across Cubao, Fairview, Taytay

### Role Setup

Assign User D:

- Dean role for BA scope in Cubao.
- Dean role for BA scope in Fairview.
- Dean role for BA scope in Taytay.
- Faculty role for teaching assignments in Cubao, Fairview, and Taytay.

If BA has child departments/areas, confirm the Dean assignment is on the parent BA scope or on all needed BA child scopes.

### Admin Portal Access Tests

| Test ID | Scenario | Steps | Expected Result |
|---|---|---|---|
| D-01 | Login to Admin Portal | Login as User D. | User D can access Admin Portal if Dean role includes `admin_portal.access`. |
| D-02 | View BA faculty under Cubao | Select Cubao scope. Open faculty/user/faculty assignment views used by operations. | BA faculty records for Cubao are visible. |
| D-03 | View BA faculty under Fairview | Select Fairview scope. Open same pages. | BA faculty records for Fairview are visible. |
| D-04 | View BA faculty under Taytay | Select Taytay scope. Open same pages. | BA faculty records for Taytay are visible. |
| D-05 | Cannot view non-BA faculty | Try to view IS/CS, Accountancy, LA, Basic Ed faculty records. | Non-BA records are hidden or access is denied. |
| D-06 | View BA faculty assignments | Open Faculty Assignments and filter by BA department/area. | BA faculty assignments across assigned campuses are visible. |
| D-07 | Cannot view non-BA faculty assignments | Filter or directly access a non-BA assignment. | Non-BA assignments are hidden or denied. |
| D-08 | View BA grade analytics/KPI | Open Grade Distribution Monitor, Faculty Activity Monitor, Faculty Assignment Dashboard, or applicable analytics pages. | BA analytics/KPI records are visible for Cubao, Fairview, and Taytay. |
| D-09 | Cannot view non-BA analytics/KPI | Attempt to view analytics for IS/CS or other areas. | Non-BA analytics are hidden or denied. |
| D-10 | View grading templates | Open Grading Templates. | User D can view templates if role has read permission. |
| D-11 | View BA faculty gradebook | Open/read gradebook or grade summary for BA faculty offering through available admin review page. | BA gradebook/summary is visible within allowed scope. |
| D-12 | Cannot view non-BA gradebook | Attempt direct URL access to non-BA gradebook/summary. | Access is denied or record is not found. |

### Grade Correction Tests

| Test ID | Scenario | Steps | Expected Result |
|---|---|---|---|
| D-13 | Pre-approve BA correction | Submit a correction petition from a BA faculty member. Login as User D and open correction queue. | User D can see and pre-approve the BA petition if route requires Dean pre-approval. |
| D-14 | Cannot pre-approve non-BA correction | Submit correction from IS/CS or other area. Login as User D. | Petition is not visible or User D cannot act on it. |
| D-15 | Same-campus and cross-campus BA routing | Submit BA correction petitions from Cubao, Fairview, and Taytay. | User D can act on all BA petitions within assigned campus scopes. |

### Faculty Portal Tests

| Test ID | Scenario | Steps | Expected Result |
|---|---|---|---|
| D-16 | Login to Faculty Portal | Login as User D to Faculty Portal. | Faculty Dashboard opens. |
| D-17 | View own teaching assignments | Open My Classes. | User D sees own assigned classes in Cubao, Fairview, and Taytay. |
| D-18 | Grade own class | Open a User D assigned class and create/edit grade activity/scores. | Faculty grading tasks work for own assignments. |
| D-19 | Active scope enforcement | Check active vs archived classes. | Only current active scope classes are editable; old/outside-scope classes are read-only/archive. |

## User A1: IS/CS Area Chairman Across Cubao, Fairview, Taytay

### Role Setup

Assign User A1:

- Area Chairman role for IS/CS scope in Cubao.
- Area Chairman role for IS/CS scope in Fairview.
- Area Chairman role for IS/CS scope in Taytay.
- Faculty role for Taytay teaching assignments.

If IS and CS are separate child departments under an IS/CS parent area, assign User A1 to the parent IS/CS area or to both IS and CS child scopes as required.

### Admin Portal Access Tests

| Test ID | Scenario | Steps | Expected Result |
|---|---|---|---|
| A1-01 | Login to Admin Portal | Login as User A1. | User A1 can access Admin Portal if Area Chairman role includes `admin_portal.access`. |
| A1-02 | View IS faculty in Cubao | Select Cubao scope and IS/CS area. | IS faculty records are visible. |
| A1-03 | View IS faculty in Fairview | Select Fairview scope and IS/CS area. | IS faculty records are visible. |
| A1-04 | View CS faculty in Taytay | Select Taytay scope and IS/CS area. | CS faculty records are visible. |
| A1-05 | View IS/CS faculty assignments | Open Faculty Assignments. | IS/CS assignments across Cubao, Fairview, and Taytay are visible. |
| A1-06 | View IS/CS analytics/KPI | Open analytics/monitor pages. | IS/CS records are visible within assigned campuses. |
| A1-07 | View IS/CS gradebooks | Open gradebook/summary for in-scope faculty. | IS/CS gradebooks are visible. |
| A1-08 | Cannot view BA records | Try BA faculty, BA assignments, BA gradebooks, BA analytics. | BA records are hidden or access is denied. |
| A1-09 | Can perform area tasks | Create/update/view allowed area-scoped records such as assignments or monitoring records, depending on permission set. | Actions work only inside IS/CS scope. |

### Grade Correction Tests

| Test ID | Scenario | Steps | Expected Result |
|---|---|---|---|
| A1-10 | Pre-approve IS correction | Submit correction from IS faculty. Login as User A1. | User A1 can see and pre-approve if route requires Area Chairman. |
| A1-11 | Pre-approve CS correction | Submit correction from CS faculty. Login as User A1. | User A1 can see and pre-approve if CS is under IS/CS scope. |
| A1-12 | Cannot pre-approve BA correction | Submit correction from BA faculty. | User A1 cannot act on BA petition. |

### Faculty Portal Tests

| Test ID | Scenario | Steps | Expected Result |
|---|---|---|---|
| A1-13 | Login to Faculty Portal | Login as User A1. | Faculty Dashboard opens. |
| A1-14 | View Taytay teaching assignment | Open My Classes. | User A1 sees own Taytay faculty load. |
| A1-15 | Grade own Taytay class | Open period grading page and encode scores. | Faculty grading works for own assignment. |

## User A2: BA Area Chairman For Fairview

### Role Setup

Assign User A2:

- Area Chairman role for BA scope in Fairview only.
- Faculty role for Fairview teaching assignments.

### Admin Portal Access Tests

| Test ID | Scenario | Steps | Expected Result |
|---|---|---|---|
| A2-01 | Login to Admin Portal | Login as User A2. | User A2 can access Admin Portal if role includes `admin_portal.access`. |
| A2-02 | View Fairview BA faculty | Select Fairview campus. Open faculty/assignment/monitoring pages. | Fairview BA records are visible. |
| A2-03 | Cannot view Cubao BA | Try Cubao BA records. | Cubao BA records are hidden or denied. |
| A2-04 | Cannot view Taytay BA | Try Taytay BA records. | Taytay BA records are hidden or denied. |
| A2-05 | Cannot view Fairview IS/CS | Try Fairview IS/CS records. | Non-BA records are hidden or denied. |
| A2-06 | View Fairview BA gradebook | Open in-scope BA gradebook/summary. | Fairview BA gradebook/summary is visible. |
| A2-07 | View Fairview BA analytics/KPI | Open Grade Distribution Monitor or applicable analytics. | Only Fairview BA data appears. |

### Grade Correction Tests

| Test ID | Scenario | Steps | Expected Result |
|---|---|---|---|
| A2-08 | Pre-approve Fairview BA correction | Submit correction from F2 or another Fairview BA faculty. Login as User A2. | User A2 can see and pre-approve if route requires Area Chairman. |
| A2-09 | Cannot pre-approve Cubao/Taytay BA correction | Submit correction from Cubao/Taytay BA faculty. | User A2 cannot act on those petitions. |
| A2-10 | Cannot pre-approve IS/CS correction | Submit correction from Fairview IS faculty. | User A2 cannot act on it. |

### Faculty Portal Tests

| Test ID | Scenario | Steps | Expected Result |
|---|---|---|---|
| A2-11 | Login to Faculty Portal | Login as User A2. | Faculty Dashboard opens. |
| A2-12 | View Fairview teaching assignment | Open My Classes. | User A2 sees own Fairview load. |
| A2-13 | Grade own Fairview class | Encode activity/scores and open summary. | Faculty grading works for own assignment. |

## User F1: IS Faculty In Fairview And Cubao

### Role Setup

Assign User F1:

- Faculty role in Fairview IS.
- Faculty role in Cubao IS.
- Faculty assignments in Fairview and Cubao.

### Faculty Portal Tests

| Test ID | Scenario | Steps | Expected Result |
|---|---|---|---|
| F1-01 | Login to Faculty Portal | Login as F1. | Faculty Dashboard opens. |
| F1-02 | View Fairview and Cubao classes | Open My Classes. | F1 sees assigned Fairview and Cubao IS classes only. |
| F1-03 | Accept pending assignments | If assignments are pending, accept them. | Accepted classes move to active class cards. |
| F1-04 | Request clarification | On a pending assignment, enter note and request clarification. | Assignment status becomes clarification requested. |
| F1-05 | Decline assignment | On a test pending assignment, decline with note. | Assignment status becomes declined. |
| F1-06 | Create grade activity | Open grading page for own class and create activity. | Activity is created. |
| F1-07 | Encode scores | Enter scores for active students. | Scores save successfully. |
| F1-08 | View summary | Open Summary. | Computed grades show for own class. |
| F1-09 | Submit gradebook | Submit period after readiness is complete. | Submission succeeds and gradebook locks as configured. |
| F1-10 | Correction request | Submit correction for own submitted class if correction mode allows. | Correction request is created and routed. |
| F1-11 | Cannot access other faculty class | Attempt direct URL to F2/F3 offering. | Access denied or not found. |
| F1-12 | Missing template warning | Remove template assignment from a test F1 offering and open My Classes. | Warning appears telling F1 to coordinate with MIS. |

### Admin Portal Negative Tests

| Test ID | Scenario | Steps | Expected Result |
|---|---|---|---|
| F1-13 | Admin Portal access | Try Admin Portal login. | Denied unless F1 has a separate admin role. |
| F1-14 | Governance pages | Try direct URL to admin analytics/correction pages. | Denied unless separately permitted. |

## User F2: BA Faculty In Fairview

### Role Setup

Assign User F2:

- Faculty role in Fairview BA.
- Faculty assignment in Fairview BA.

### Faculty Portal Tests

| Test ID | Scenario | Steps | Expected Result |
|---|---|---|---|
| F2-01 | Login to Faculty Portal | Login as F2. | Faculty Dashboard opens. |
| F2-02 | View own Fairview BA class | Open My Classes. | F2 sees own Fairview BA class. |
| F2-03 | Accept assignment | Accept pending assignment if needed. | Class becomes active. |
| F2-04 | Encode activity and scores | Create activity and encode scores. | Scores save successfully. |
| F2-05 | View summary | Open Summary. | Computed grades display. |
| F2-06 | Submit gradebook | Complete readiness and submit. | Submission succeeds. |
| F2-07 | Submit correction petition | File correction after submission if allowed. | Petition routes to BA Fairview approver, expected User A2 if configured. |
| F2-08 | Cannot access IS/CS class | Try direct URL to IS/CS offering. | Access denied or not found. |

### Routing Validation

| Test ID | Scenario | Steps | Expected Result |
|---|---|---|---|
| F2-09 | Fairview BA correction route | F2 files correction. Login as A2. | A2 sees/pre-approves. |
| F2-10 | Dean visibility | F2 files correction. Login as User D. | User D sees/pre-approves if Dean is part of BA correction route. |
| F2-11 | IS/CS chairman blocked | F2 files correction. Login as A1. | A1 does not see or cannot act on F2's BA petition. |

## User F3: CS Faculty In Taytay

### Role Setup

Assign User F3:

- Faculty role in Taytay CS.
- Faculty assignment in Taytay CS.

### Faculty Portal Tests

| Test ID | Scenario | Steps | Expected Result |
|---|---|---|---|
| F3-01 | Login to Faculty Portal | Login as F3. | Faculty Dashboard opens. |
| F3-02 | View Taytay CS class | Open My Classes. | F3 sees own Taytay CS class. |
| F3-03 | Accept assignment | Accept pending assignment if needed. | Class becomes active. |
| F3-04 | Encode activity and scores | Create activity and encode scores. | Scores save successfully. |
| F3-05 | View summary | Open Summary. | Computed grades display. |
| F3-06 | Submit gradebook | Complete readiness and submit. | Submission succeeds. |
| F3-07 | Submit correction petition | File correction after submission if allowed. | Petition routes to IS/CS Area Chairman, expected User A1 if configured. |
| F3-08 | Cannot access BA class | Try direct URL to BA offering. | Access denied or not found. |
| F3-09 | Cannot access other campus class | Try direct URL to Cubao/Fairview offering not assigned to F3. | Access denied or not found. |

## Cross-User Correction Routing Scenarios

| Test ID | Petition Source | Expected Approver Visibility | Expected Blocked Users |
|---|---|---|---|
| CR-01 | Fairview BA faculty F2 | User A2, User D if route includes Dean | User A1, F1, F3 |
| CR-02 | Cubao BA faculty | User D | User A2, User A1 |
| CR-03 | Taytay BA faculty | User D | User A2, User A1 |
| CR-04 | Cubao IS faculty F1 | User A1 | User D, User A2 |
| CR-05 | Fairview IS faculty F1 | User A1 | User D, User A2 |
| CR-06 | Taytay CS faculty F3 | User A1 | User D, User A2 |

## Cross-User Analytics Scenarios

| Test ID | User | Expected Analytics Visibility | Must Not See |
|---|---|---|---|
| AN-01 | User D | BA data across Cubao, Fairview, Taytay | IS/CS, LA, Accountancy, Basic Ed unless separately scoped |
| AN-02 | User A1 | IS/CS data across Cubao, Fairview, Taytay | BA data |
| AN-03 | User A2 | BA data in Fairview only | Cubao BA, Taytay BA, IS/CS |
| AN-04 | F1 | Own faculty analytics only if Faculty Portal analytics enabled | Other faculty classes |
| AN-05 | F2 | Own faculty analytics only if Faculty Portal analytics enabled | Other faculty classes |
| AN-06 | F3 | Own faculty analytics only if Faculty Portal analytics enabled | Other faculty classes |

## Cross-User Gradebook Access Scenarios

| Test ID | User | Can Open | Must Be Denied |
|---|---|---|---|
| GB-01 | User D | BA gradebooks in Cubao/Fairview/Taytay | IS/CS gradebooks |
| GB-02 | User A1 | IS/CS gradebooks in Cubao/Fairview/Taytay | BA gradebooks |
| GB-03 | User A2 | Fairview BA gradebooks | Cubao BA, Taytay BA, IS/CS |
| GB-04 | F1 | Own assigned Fairview/Cubao IS classes | Other faculty classes |
| GB-05 | F2 | Own assigned Fairview BA classes | Other faculty classes |
| GB-06 | F3 | Own assigned Taytay CS classes | Other faculty classes |

## Gradebook Student Identity Review Scenarios

Use these scenarios for AC, Dean, and CAO-style users who are authorized to verify student-level gradebook records or correction petitions.

| Test ID | Scenario | Steps | Expected Result |
|---|---|---|---|
| SI-01 | Reviewer without identity permission sees masked records | Remove `gradebook.view_student_identity` from a test AC/Dean/CAO role. Open an in-scope Faculty Grade Book Monitor page. | Student numbers and names are masked; grades remain visible. |
| SI-02 | Reviewer with identity permission sees unmasked records | Grant `gradebook.view_student_identity` to the same role. Reopen the same in-scope gradebook. | Student numbers and names are visible; page shows authorized identity-view status. |
| SI-03 | Identity permission does not bypass scope | With `gradebook.view_student_identity` granted, try to open an out-of-scope gradebook by filter or direct URL. | Access is denied or the record is not available. |
| SI-04 | Correction review identity verification | File a correction petition for a submitted Prelim grade. Login as an in-scope authorized reviewer and open the faculty gradebook. | Reviewer can verify the named student and grades when permission is granted; the monitor open is recorded in audit logs. |

## Cross-User Faculty Assignment Scenarios

| Test ID | User | Can View/Manage | Must Not See |
|---|---|---|---|
| FA-01 | User D | BA faculty assignments in Cubao/Fairview/Taytay | IS/CS assignments |
| FA-02 | User A1 | IS/CS assignments in Cubao/Fairview/Taytay | BA assignments |
| FA-03 | User A2 | Fairview BA assignments | Cubao/Taytay BA and IS/CS |
| FA-04 | F1 | Own accepted/pending assignments in Faculty Portal | Other faculty assignments |
| FA-05 | F2 | Own accepted/pending assignments in Faculty Portal | Other faculty assignments |
| FA-06 | F3 | Own accepted/pending assignments in Faculty Portal | Other faculty assignments |

## Negative Direct-URL Tests

For every user, perform direct URL checks using records outside their scope.

Examples:

- Open another faculty member's class period page.
- Open another faculty member's summary page.
- Open another faculty member's class list page.
- Open a correction review page outside department/campus scope.
- Open grade distribution monitor with query string filters for another department.
- Open faculty assignment detail/edit URL outside scope.

Expected result:

- Access denied, redirect, or 404.
- The page must not leak student names, grades, class details, or faculty details outside scope.

## Missing Grading Template Scenario

Use this for any faculty user.

| Test ID | Scenario | Steps | Expected Result |
|---|---|---|---|
| MT-01 | Faculty warned about missing template | Remove or deactivate course-template assignment for a test offering assigned to the faculty. Login to Faculty Portal and open My Classes. | Banner appears: assigned class has no grading template. Class card shows `Not assigned yet` and instructs faculty to coordinate with MIS Department. |
| MT-02 | Admin sees offering gap | Login as admin with scope. Open Course Template Assignments and tick `Offerings with no grading template`. | The affected offering appears with campus, AY/term, course, section, faculty, and issue. |
| MT-03 | Fix template assignment | Assign published grading template to the course/term. Reopen Faculty My Classes. | Warning disappears and template name appears. |

## Active/Inactive Scope Scenarios

| Test ID | Scenario | Steps | Expected Result |
|---|---|---|---|
| AI-01 | Inactive department hidden operationally | Deactivate a test department. Open operational pages using that department. | Records no longer appear in operational dropdowns/lists. |
| AI-02 | Inactive department visible in maintenance | Open Departments maintenance page as permitted admin. | Inactive department appears only in inactive section. |
| AI-03 | Inactive offering excluded from faculty | Deactivate a course offering or its department chain. Login as assigned faculty. | Class is not available as an active operational class. |

## Acceptance Criteria

The role setup passes UAT only if:

- Each admin/governance user sees only their assigned campus and area scope.
- Parent department assignments include active child departments when intended.
- Child department assignments do not grant sibling or parent access unless explicitly assigned.
- Faculty users can only work on their own assigned classes.
- Dual-role users can use both Admin Portal and Faculty Portal according to their separate roles.
- Correction petitions route to the correct area/dean approvers.
- Direct URL access does not bypass RBAC or scope checks.
- Missing grading-template warnings appear for faculty and admin monitoring.
- Inactive records appear only on maintenance pages, not operational workflows.

## Tester Sign-Off

| Tester | Date | User Tested | Result | Notes |
|---|---|---|---|---|
|  |  | User D | Pass / Fail |  |
|  |  | User A1 | Pass / Fail |  |
|  |  | User A2 | Pass / Fail |  |
|  |  | User F1 | Pass / Fail |  |
|  |  | User F2 | Pass / Fail |  |
|  |  | User F3 | Pass / Fail |  |

AC, Dean, and CAO may also have `FACULTY` role. The important rule is:

> Give them **two separate roles** when needed: one for teaching, one for admin/governance visibility.

The `FACULTY` role should allow faculty portal/class work.  
The `AC`, `Dean`, or `CAO` role should control Admin Portal monitoring scope.

**NCBA Academic Structure Reference**

EduGrade+ should treat the academic governance area as the practical department scope for actual-data setup.

| Branch | Governance Level | EduGrade+ setup guidance |
|---|---|---|
| Academic | Graduate | Create campus-specific department records when Graduate needs separate security, templates, reporting, or correction routing. |
| Academic | College - BA | Create campus-specific BA department/area records; Cubao currently has no BA Academic Chairman, so avoid assigning a Cubao BA AC role unless NCBA later creates that post. |
| Academic | College - IS/CS | Create campus-specific IS/CS department/area records. |
| Academic | College - LA | Create campus-specific LA department/area records. |
| Academic | College - Accountancy | Create campus-specific Accountancy department/area records. |
| Academic | Basic Education - Elementary | Create campus-specific Elementary department/area records if Basic Ed needs area-level monitoring. |
| Academic | Basic Education - Junior High School | Create campus-specific JHS department/area records if Basic Ed needs area-level monitoring. |
| Academic | Basic Education - Senior High School | Create campus-specific SHS department/area records if Basic Ed needs area-level monitoring. |

Recommended mapping rule:

> Courses, sections, course offerings, students, faculty assignments, grading templates/profiles, and governance role assignments should use the most specific department/area that owns them.

Use broad department records such as `COLLEGE` or `BASIC_ED` only when the same person, template, or monitor scope genuinely covers the whole broad unit. Otherwise, use area-specific records such as BA, IS/CS, LA, Accountancy, Elementary, JHS, or SHS.

EduGrade+ now supports parent-child department hierarchy. Recommended setup:

| Parent Division | Child Areas |
|---|---|
| `COLLEGE` | `COLL_BA`, `COLL_ISCS`, `COLL_LA`, `COLL_ACCOUNTANCY` |
| `BASIC_ED` | `BED_ELEM`, `BED_JHS`, `BED_SHS` |
| `GRAD_STUDIES` | Graduate Studies areas, if separate governance is needed |

Assign broad governance users to the parent division only when they should see all active child areas. Assign Area Chairmen to the exact child area. Example: an IS/CS Academic Chairman should be scoped to `FVW_COLL_ISCS`, while a College Dean can be scoped to `FVW_COLLEGE` or equivalent parent division.

**Step By Step Setup**

1. **Keep the `FACULTY` role for teaching only**

If the user teaches classes, assign:

| Role | Tenant | Campus | Department | Purpose |
|---|---|---|---|---|
| `FACULTY` | NCBA | Fairview/Cubao | teaching department or blank | Faculty Portal access |

This should not be the role used for Grade Distribution Monitor access.

2. **Add a separate active admin role**

For the same user, assign an admin/governance role:

| User Type | Example Role | Scope |
|---|---|---|
| Area Chairman | `NCBA_FAIRVIEW_AC`, `NCBA_CUBAO_AC` | Usually campus + department |
| Dean | `DEAN` or campus-specific dean role | Usually campus + college/department |
| CAO | `CAO` | Campus-wide or tenant-wide, depending on policy |

Example for `ac`:

| Role | Active? | Campus | Department |
|---|---:|---|---|
| `FACULTY` | Yes | Cubao | optional teaching scope |
| `NCBA_FAIRVIEW_AC` | Yes | Fairview | `FVW_COLL_IS` |
| `NCBA_CUBAO_AC` | Yes | Cubao | `CUB_COLL_BA` |
| `NCBA_CUBAO_AC` | Yes | Cubao | `CUB_COLL_BSA` |

Right now, `ac` has Cubao AC roles but they are inactive, so Cubao monitoring is not available.

3. **Assign monitor permissions to the admin role**

Go to:

`Admin Portal -> Security -> Roles`

For AC / Dean / CAO role, make sure it has the needed permissions, especially:

- Grade Distribution Monitor access
- Faculty monitoring access, if applicable
- Gradebook/submission monitoring permissions, if applicable
- Unmasked student identity access for authorized gradebook/correction reviewers only, if applicable

The exact permission name in the system includes:

`grade_distribution_monitor.read`

For AC / Dean / CAO users who are formally allowed to verify student-level gradebook records and correction petitions, also grant:

`gradebook.view_student_identity`

Without this permission, the Faculty Grade Book Monitor still opens within scope, but student numbers and names remain masked for privacy.

4. **Use department scope carefully**

For AC accounts, use department-level scope.

Example:

| AC User | Campus | Department |
|---|---|---|
| Fairview AC | NCBA-FAIRVIEW | `FVW_COLL_IS` |
| Cubao AC | NCBA-CUBAO | `CUB_COLL_BA` |
| Cubao AC | NCBA-CUBAO | `CUB_COLL_BSA` |

If the user should see all departments in a campus, assign the admin role with:

| Campus | Department |
|---|---|
| NCBA-CUBAO | blank |

But only do this for roles like Dean/CAO if they are truly campus-wide.

5. **Check faculty department identity**

The monitor now follows the assigned faculty’s scope. So the faculty member should have either:

- correct `Default Department`, or
- active `FACULTY` role with the correct department.

Example:

| Faculty | Default Department |
|---|---|
| faculty member | `FVW_COLL_IS` |

Even if the course offering is stored under `NCBA-02-COLLEGE`, the AC can still see it if the faculty belongs to `FVW_COLL_IS`.

6. **Check faculty assignment status**

The Grade Distribution Monitor only includes properly assigned classes. Confirm:

- faculty assignment is active
- faculty assignment is accepted
- course offering is open/active
- grades or activity scores already exist

7. **For the current `ac` account**

To make `ac` see Cubao grade distribution:

1. Go to `Admin Portal -> Security -> Users`
2. Open `ac`
3. Go to Roles
4. Review `Current Assignments` for active roles and `Inactive Assignments` for dormant roles
5. Activate/add `NCBA_CUBAO_AC`
6. Add Cubao departments needed:
   - `CUB_COLL_BA`
   - `CUB_COLL_BSA`
   - `CUB_COLL_IS`, if applicable
7. Keep the Cubao `FACULTY` role only if `ac` also teaches
8. Save
9. Login again or refresh the admin session
10. Open `Admin Portal -> Grading -> Grade Distribution Monitor`
11. Select Cubao from the top scope selector

The clean rule for preparing actual data is:

> `FACULTY` role = teaching access.  
> `AC / Dean / CAO` role = admin monitoring access.  
> A person can have both, but the Admin Portal visibility must come from the active admin role scope.

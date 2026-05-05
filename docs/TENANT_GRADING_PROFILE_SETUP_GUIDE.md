# Tenant Grading Profile Setup Guide

This guide explains how to configure EduGradesPro grading templates, course-template assignments, tenant grading profiles, and term-type rules for NCBA-style regular and summer grading.

## Purpose

EduGradesPro separates grading setup into two related layers:

1. **Grading Templates**
   - define the grading structure
   - periods, components, subcomponents, weights, and exam flags
   - example: Financial Management, Marketing Management, All Lecture Courses, All Laboratory Courses

2. **Tenant Grading Profiles**
   - define which grading rule applies to a course/offering scope
   - can apply by tenant, campus, department, program, course, course type, and term type
   - can define base value, passing threshold, and final grade formula

The template answers: **How is each period grade computed?**

The profile answers: **Which template and final-grade rule should this offering use?**

## Required Term Setup

Go to:

```text
Admin Portal -> Academics -> Terms
```

Every term must have the correct **Term Type**:

| Term | Term Type |
| --- | --- |
| 1st Semester | Regular |
| 2nd Semester | Regular |
| Summer | Summer |
| Special term / bridging term | Special |

Important:

- Existing terms default to `Regular`.
- Before opening Summer classes, update the Summer term to `Summer`.
- If a Summer term remains marked as `Regular`, Summer-specific grading profiles will not match.

## Grading Template Setup

Go to:

```text
Admin Portal -> Grading -> Grading Templates
```

Create or maintain the templates that represent the official grading structures.

Period codes are visible in:

```text
Admin Portal -> Grading -> Grading Templates
```

under the `Periods` column, and also at the bottom of the grading template create/edit page under `Active Template Period Codes`.

Use those exact codes when entering `Final Grade Period Weights` in a Tenant Grading Profile.

Based on the attached NCBA samples, the regular-term template families are:

| Template Family | Typical Scope |
| --- | --- |
| Financial Management | Courses under Financial Management |
| Marketing Management | Courses under Marketing Management |
| All Lecture Courses | General lecture courses |
| All Laboratory Courses | General laboratory courses |
| Computer Science Lecture Courses | Taytay campus only, if this structure is only for Taytay |
| Computer Science Laboratory Courses | Taytay campus only, if this structure is only for Taytay |

For regular terms, the final grade shown in the samples is:

```text
Final Grade = (Prelim Grade + Midterm Grade + Prefinal Grade + Final Exam Grade) / 4
```

This is supported by:

```text
Final Grade Formula Mode = Average All Active Template Periods
```

provided the template has the four active periods:

- Prelim
- Midterm
- Prefinal
- Final / Final Exam

## Course Template Assignment

Go to:

```text
Admin Portal -> Grading -> Course Template Assignments
```

Use course-template assignments when a course clearly uses one grading template.

Example:

| Course Group | Assigned Template |
| --- | --- |
| FM courses | Financial Management template |
| Marketing courses | Marketing Management template |
| Lecture courses | All Lecture Courses template |
| Laboratory courses | All Laboratory Courses template |

Important behavior:

EduGradesPro checks course-template assignments before using a grading profile to choose a template.

Resolution order for template selection:

1. Course template assignment for the exact term
2. Course template assignment with no term
3. Matching tenant grading profile
4. Latest published tenant template fallback

Because of this, use course-template assignments for straightforward course-to-template mapping.

Use tenant grading profiles for:

- final-grade formula rules
- Regular vs Summer differences
- campus-specific policy
- department/program/course-type rules
- fallback/default grading governance

## Taytay-Only Template Setup

For templates that apply only to Taytay campus, such as the Computer Science lecture/laboratory examples, choose the setup based on how courses are stored.

### If Taytay Has Separate Course Records

If the Taytay courses are separate `Course` records with campus/department scope, then assign those Taytay courses directly to the Taytay-specific templates.

Example:

```text
Course: CS101-ITC, campus Taytay
Template: Computer Science Lecture Courses
```

This is the simplest setup.

### If The Same Course Record Is Shared Across Campuses

If the same course record is used by Fairview, Cubao, and Taytay, avoid using a general course-template assignment for that course if Taytay needs a different template.

Instead, use Tenant Grading Profiles:

| Profile | Campus | Template |
| --- | --- | --- |
| Regular CS Lecture - General | blank / all campuses | default lecture template |
| Regular CS Lecture - Taytay | Taytay | Computer Science Lecture template |
| Regular CS Laboratory - General | blank / all campuses | default laboratory template |
| Regular CS Laboratory - Taytay | Taytay | Computer Science Laboratory template |

This allows EduGradesPro to choose the Taytay-specific template only for Taytay offerings.

## Tenant Grading Profile Setup

Go to:

```text
Admin Portal -> Grading -> Tenant Grading Profiles
```

Each profile should be treated as a rule:

```text
For this scope, in this term type, use this template and final-grade formula.
```

Key fields:

| Field | Meaning |
| --- | --- |
| Tenant | Owner school/tenant |
| Campus | Leave blank for all campuses, or choose a campus such as Taytay |
| Department | Optional department restriction |
| Program | Optional program restriction |
| Course | Optional exact course restriction |
| Course Type | Optional course-type fallback, such as lecture/lab if used |
| Applicable Term Type | Leave blank for all terms, or choose Regular/Summer/Special |
| Grading Template | Template used when the profile is responsible for template selection |
| Default Base Value | Optional raw-score base/transmutation default |
| Passing Grade Threshold | Optional passing threshold, usually 75.00 |
| Final Grade Formula Mode | Average all active periods, or weighted selected periods |
| Final Grade Period Weights | Required only for weighted selected periods |
| Effective From Term | Keep blank unless a rule is only for one exact term |
| Priority | Lower number wins when specificity is tied |
| Is Default | Marks normal fallback profile |
| Is Active | Only active profiles are used |

## Recommended Regular-Term Profiles

For the templates that apply across all campuses, create Regular profiles with blank campus:

| Profile Example | Campus | Term Type | Formula Mode |
| --- | --- | --- | --- |
| REG-FIN-MGT | blank | Regular | Average All Active Template Periods |
| REG-MKT-MGT | blank | Regular | Average All Active Template Periods |
| REG-LECTURE | blank | Regular | Average All Active Template Periods |
| REG-LAB | blank | Regular | Average All Active Template Periods |

For Taytay-only templates:

| Profile Example | Campus | Term Type | Formula Mode |
| --- | --- | --- | --- |
| REG-CS-LECTURE-TAYTAY | Taytay | Regular | Average All Active Template Periods |
| REG-CS-LAB-TAYTAY | Taytay | Regular | Average All Active Template Periods |

Regular-term final grade:

```text
Final Grade = (Prelim + Midterm + Prefinal + Final Exam) / 4
```

Use:

```text
Final Grade Formula Mode = Average All Active Template Periods
```

## Recommended Summer Profiles

For Summer, create matching profiles with:

```text
Applicable Term Type = Summer
```

The intended Summer final grade is:

```text
Final Grade = (Midterm Grade + Prefinal Grade + Final Exam Grade) / 3
```

### Preferred Setup When Summer Has Separate 3-Period Templates

If Summer uses different templates that contain only these active periods:

- Midterm
- Pre-Final
- Final Exam

then use:

```text
Final Grade Formula Mode = Average All Active Template Periods
```

This is the cleanest Summer setup because EduGradesPro will average only the active periods in the Summer template:

```text
Final Grade = (Midterm + Pre-Final + Final Exam) / 3
```

Do not use `Weighted Selected Periods` for this setup unless NCBA specifically wants unequal Summer period weights. If all three Summer periods are equal, `Average All Active Template Periods` is simpler, exact, and easier to explain.

Use this profile pattern when course-template assignments already assign courses to their correct Summer templates:

| Profile Example | Campus | Term Type | Formula Mode | Weights |
| --- | --- | --- | --- | --- |
| SUM-DEFAULT | blank | Summer | Average All Active Template Periods | none |

This one Summer default profile is enough when:

- all Summer offerings use the same final-grade rule
- all Summer templates have only the three active Summer periods
- passing threshold and base value are the same across NCBA
- course-template assignments already control which Summer template each course uses

Create separate Summer profiles only when a campus, department, program, course group, or course has a different rule, passing threshold, base value, or fallback template.

### Alternate Setup When Summer Reuses Regular 4-Period Templates

If Summer uses the same grading templates but excludes Prelim from the final grade, use:

```text
Final Grade Formula Mode = Weighted Selected Periods
```

Then enter:

```text
MIDTERM=33.33
PREFINAL=33.33
FINAL=33.34
```

This allows the weights to total exactly 100.00, which the current form requires.

Use this profile pattern:

| Profile Example | Campus | Term Type | Formula Mode | Weights |
| --- | --- | --- | --- | --- |
| SUM-FIN-MGT | blank | Summer | Weighted Selected Periods | MIDTERM=33.33, PREFINAL=33.33, FINAL=33.34 |
| SUM-MKT-MGT | blank | Summer | Weighted Selected Periods | MIDTERM=33.33, PREFINAL=33.33, FINAL=33.34 |
| SUM-LECTURE | blank | Summer | Weighted Selected Periods | MIDTERM=33.33, PREFINAL=33.33, FINAL=33.34 |
| SUM-LAB | blank | Summer | Weighted Selected Periods | MIDTERM=33.33, PREFINAL=33.33, FINAL=33.34 |
| SUM-CS-LECTURE-TAYTAY | Taytay | Summer | Weighted Selected Periods | MIDTERM=33.33, PREFINAL=33.33, FINAL=33.34 |
| SUM-CS-LAB-TAYTAY | Taytay | Summer | Weighted Selected Periods | MIDTERM=33.33, PREFINAL=33.33, FINAL=33.34 |

Note:

- `PRELIM` is intentionally not included in the Summer profile weights.
- The period codes must exactly match the active period codes in the grading template.
- If the final period code in the template is `FINALS` instead of `FINAL`, use that exact code.

### If Equal Summer Weights Are Required

The current profile form requires weighted entries to total exactly `100.00` with two decimal places.

That means:

```text
33.33 + 33.33 + 33.33 = 99.99
```

is rejected.

For a mathematically exact `/ 3`, there are two clean options:

1. Create Summer-specific templates where only Midterm, Prefinal, and Final are active, then use `Average All Active Template Periods`.
2. Add a future formula mode such as `Average Selected Periods`, where selected periods can be averaged without weights.

If Summer must reuse the regular 4-period template today, use:

```text
MIDTERM=33.33
PREFINAL=33.33
FINAL=33.34
```

If Summer has its own 3-period templates, do not use this weighted workaround. Use `Average All Active Template Periods`.

## NCBA 10-Template / 3-Campus Example

Assume NCBA has:

- 10 Regular templates
- 10 Summer templates
- 3 campuses
- courses assigned to their correct Regular or Summer template through Course Template Assignments
- Regular templates have Prelim, Midterm, Pre-Final, and Final Exam
- Summer templates have only Midterm, Pre-Final, and Final Exam

In this case, the recommended setup is:

1. Use Course Template Assignments to choose the correct template for each course and term.
2. Use Tenant Grading Profiles to define the final-grade rule, passing threshold, and base value.
3. Keep Campus blank when the same rule applies to Cubao, Fairview, and Taytay.
4. Create campus-specific profiles only when one campus has a different grading rule or a campus-only template fallback.

### Minimum Clean Setup

If all NCBA campuses use the same passing threshold, base value, and final-grade formula, create only these two profiles:

| Profile Code | Campus | Term Type | Template Field | Formula Mode | Period Weights |
| --- | --- | --- | --- | --- | --- |
| REG-DEFAULT | blank | Regular | broad Regular fallback template | Average All Active Template Periods | none |
| SUM-DEFAULT | blank | Summer | broad Summer fallback template | Average All Active Template Periods | none |

Why only two profiles?

- The 10 template structures are already controlled by Course Template Assignments.
- The template selected in the broad profile acts as a fallback if a course-template assignment is missing.
- The Regular final formula is the same for all Regular templates.
- The Summer final formula is the same for all Summer templates.
- A blank Campus profile covers all three campuses.

### When To Create More Than Two Profiles

Create additional profiles only when the rule is different.

Examples:

| Situation | Recommended Profile Setup |
| --- | --- |
| Taytay has a different Summer rule | Add `SUM-TAYTAY` with Campus = Taytay |
| Laboratory courses use a different base value | Add `REG-LAB` and/or `SUM-LAB` scoped by Course Type or Program |
| One department has a different passing threshold | Add department-scoped Regular/Summer profiles |
| One course has a special final formula | Add course-scoped Regular/Summer profiles |
| A course-template assignment is missing and a profile must provide the fallback template | Put the fallback Grading Template on the matching profile |

### Important Profile Field Guidance

For this NCBA setup:

| Field | Recommended Value |
| --- | --- |
| Tenant | NCBA |
| Campus | blank, unless the rule is campus-specific |
| Department / Program / Course / Course Type | blank for default profile; fill only for exceptions |
| Applicable Term Type | Regular for Regular profile; Summer for Summer profile |
| Grading Template | required; choose a sensible fallback template for the same term type |
| Default Base Value | set only if this profile should control raw-score base/transmutation |
| Passing Grade Threshold | usually 75.00, if NCBA wants the profile to declare it explicitly |
| Final Grade Formula Mode | Average All Active Template Periods |
| Final Grade Period Weights | leave blank for the recommended Regular and 3-period Summer setup |
| Is Default | yes for broad fallback profiles |
| Is Active | yes |

### Summer Formula Reminder

For the Summer templates described above, do this:

```text
Formula Mode: Average All Active Template Periods
Weights: leave blank
```

Do not enter:

```text
MIDTERM=33.33
PREFINAL=33.33
FINAL=33.33
```

because the weighted profile form expects the total to be exactly `100.00`, and those three values total only `99.99`.

## How EduGradesPro Resolves The Profile

When computing grades for a course offering, EduGradesPro resolves the matching grading profile using this priority:

1. Course match
2. Course type match
3. Program match
4. Department match
5. Campus match
6. Term type match
7. Exact effective term match
8. Lower priority number
9. Default profile
10. Newer profile id

Important:

- More specific scope wins before term type.
- A course-specific all-term profile can beat a campus-level Summer profile.
- If Summer must override another rule, create the Summer profile at the same or more specific scope.

## How This Works With Course Template Assignments

Course-template assignment controls the template selected for a course.

Tenant grading profile controls final-grade formula, base value, and passing threshold for the matching scope.

Even when a course-template assignment supplies the template, EduGradesPro can still use the matching grading profile to determine the final-grade formula.

Recommended pattern:

1. Assign each course to the correct template.
2. Create Regular grading profiles for normal final-grade computation.
3. Create Summer grading profiles with the same scope but `Applicable Term Type = Summer`.
4. Verify the Summer term itself is marked as `Summer`.

## Example NCBA Setup Matrix

| Setup Pattern | Regular Profile | Summer Profile | Campus |
| --- | --- | --- | --- |
| Course-template assignments already cover all 10 Regular templates and all 10 Summer templates | One broad Regular default profile using Average all active periods | One broad Summer default profile using Average all active periods | All campuses |
| A template group has a different base value, threshold, or final-grade rule | Add a matching Regular profile scoped to that group | Add a matching Summer profile scoped to that group | All campuses, or one campus if the exception is campus-specific |
| Taytay-only template rule | Add a Taytay-scoped Regular profile only if the rule differs from the default | Add a Taytay-scoped Summer profile only if the rule differs from the default | Taytay |
| Summer reuses a 4-period Regular template and must exclude Prelim | Not applicable | Use Weighted selected periods excluding Prelim | All campuses or scoped exception |

## Operational Checklist

Before regular semester grading:

- Confirm 1st/2nd semester terms are marked `Regular`.
- Confirm regular course-template assignments are correct.
- Confirm regular grading profiles are active.
- Confirm Regular profiles use `Average All Active Template Periods`.

Before Summer grading:

- Confirm the Summer term is marked `Summer`.
- Confirm Summer grading profiles are active.
- If Summer has separate 3-period templates, confirm Summer profiles use `Average All Active Template Periods` and leave period weights blank.
- If Summer reuses Regular 4-period templates and must exclude Prelim, confirm Summer profiles use `Weighted Selected Periods`.
- For weighted Summer profiles only, confirm the period weights use the correct period codes:

```text
MIDTERM=33.33
PREFINAL=33.33
FINAL=33.34
```

- Confirm Taytay-only Summer profiles have `Campus = Taytay`.
- Confirm course-specific profiles do not accidentally override Summer campus profiles.

## Common Mistakes To Avoid

1. Leaving Summer term as `Regular`
   - Result: regular final-grade formula is used.

2. Creating Summer profile but leaving it inactive
   - Result: profile is ignored.

3. Using wrong period code
   - Example: entering `FINAL` when the template uses `FINALS`.
   - Result: that period weight is ignored or validation fails.

4. Using weighted Summer values of `33.33`, `33.33`, and `33.33`
   - Result: the profile is rejected because the total is `99.99`, not `100.00`.
   - If Summer has its own 3-period template, use `Average All Active Template Periods` instead.
   - If Summer reuses a 4-period template, use `33.33`, `33.33`, and `33.34`.

5. Creating a very specific all-term profile
   - Example: course-specific profile with blank term type.
   - Result: it may override a less-specific Summer profile.

6. Using `Effective From Term` as if it means all future terms
   - Current behavior is exact-term or blank fallback.
   - Leave it blank unless the profile is intentionally for one exact term.

## Governance Notes

- This setup does not change raw score computation.
- Base value applies to raw-score transmutation, not to final-grade averaging.
- Final-grade computation uses stored official period grades.
- Profile changes affect future recomputation; if profiles are changed after grades already exist, recompute affected final grades before relying on printed summaries or reports.
- Keep old inactive profiles for audit reference instead of deleting them.

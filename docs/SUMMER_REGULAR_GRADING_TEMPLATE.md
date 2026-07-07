Yes, you understood the current **Course Template Assignment** behavior correctly: it has no “every Summer” option right now.

In that page:

- Blank `Effective term` = applies generally to all terms unless overridden
- Specific Summer term = applies only to that one Summer term
- There is currently no “all Summer terms every year” selector there

So if you use **Course Template Assignments** only, you need one Summer row per Summer term.

But there is another way, depending on your setup.

**Option A: Use Tenant Grading Profiles For One Summer Setup**

If many courses can share the same Summer template, use:

`Admin Portal -> Grading -> Tenant Grading Profiles`

Create a Summer profile:

- Applicable Term Type: `Summer`
- Grading Template: your Summer template
- Effective from term: blank
- Scope: tenant/campus/department/program/course type/course, depending how broad you want it

Then make sure every Summer term is marked as:

- Term Type: `Summer`

This can work as a reusable Summer setup every year.

Example:

| Profile | Applicable Term Type | Template | Effective Term |
|---|---|---|---|
| Regular Default | Regular | Regular Template | blank |
| Summer Default | Summer | Summer Template | blank |

Result:

- 1st Semester uses Regular profile/template
- 2nd Semester uses Regular profile/template
- Summer 2026 uses Summer profile/template
- Summer 2027 also uses Summer profile/template

**Important Catch**

Course Template Assignments are checked before Tenant Grading Profiles.

So if a course already has a blank/default Course Template Assignment, that assignment may win first, including during Summer, unless you create an exact Summer override.

That means the “one Summer setup” works best if template selection is handled mainly through Tenant Grading Profiles, not per-course blank assignments.

**Recommended Practical Choice**

If your school uses only a few broad templates:

Use Tenant Grading Profiles by term type. That gives you a reusable Summer setup.

If each course needs a different Summer template:

Use exact Summer Course Template Assignment rows per Summer term, unless we add a new feature like “Applicable Term Type” directly to Course Template Assignments.

So the answer is:

- Current page: no single “all summers” setup
- Existing system alternative: Tenant Grading Profiles with `Applicable Term Type = Summer`
- Best future enhancement: add `Applicable Term Type` to Course Template Assignments so a course can have one Regular row and one Summer row reusable every year
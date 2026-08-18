from __future__ import annotations

from copy import deepcopy

from apps.core.services.permissions import PermissionService


ADMIN_HELP_SECTIONS = [
    {
        "code": "departmental-exam-contributors",
        "title": "Departmental Exam Contributor Rosters",
        "topics": [
            {
                "code": "contributor-monitoring",
                "title": "Initialize, Synchronize, and Monitor Contributions",
                "purpose": "Provides exact-responsibility aggregate monitoring without exposing any contributor question content.",
                "how_to_open": ["Open Departmental Exam Builder in the Admin Portal.", "Choose Contributor Completion."],
                "check_first": ["The governing cycle and course contribution must be Open.", "Only exact course configurators may initialize or synchronize; an assigned reviewer receives monitoring only.", "Manage Cycles alone does not grant contributor monitoring."],
                "actions": [
                    {"name": "Initialize Roster", "does": "Creates one contribution per grouped course and eligible faculty, preserving every concrete assignment/campus source.", "when": "Use this explicit POST for a course that was already Open before Stage 5; future Open transitions initialize automatically.", "avoid": "Do not use roster actions as a manual authorization bypass.", "result": "Valid sources create Active Drafts; an empty result is still recorded as initialized.", "editable": "The action is idempotent and never deletes contribution history."},
                    {"name": "Synchronize Roster", "does": "Reevaluates active users, tenants, campuses, offerings, accepted assignments, exact Faculty Portal permission, and direct denies source by source.", "when": "Use it after official teaching-assignment changes.", "avoid": "Do not expect one denied campus to invalidate an independently authorized campus source.", "result": "Drafts become Blocked only after the final qualifying source is lost and may reactivate when a valid source returns; Submitted records remain historical.", "editable": "Sources are marked current or invalid and retained as history."},
                    {"name": "Monitor Completion", "does": "Shows contributor identity, quota, saved count, progress, workflow/roster status, deadline, overdue state, and valid/invalid source counts.", "when": "Use it for operational follow-up within your exact responsible-department or assigned-reviewer scope.", "avoid": "Question text, choices, answers, fingerprints, and import previews are intentionally unavailable.", "result": "You receive aggregate progress only.", "editable": "Monitoring is read-only."},
                    {"name": "Resolve Blocked Draft", "does": "Records an immutable reason, resolver, time, contribution revision, roster revision, blocked episode, and source-evidence snapshot for one current Blocked Draft.", "when": "Use it only after explicit operational review of a contributor who is no longer currently required.", "avoid": "Resolution does not submit, delete, transfer, or make Draft questions eligible, and a later Active episode requires Final Submission again.", "result": "The exact current blocked episode can satisfy contribution-close readiness while its evidence remains current.", "editable": "Resolution evidence is immutable; a later blocked episode receives a separate event."},
                    {"name": "Stage 6 Blueprint", "does": "Configures No Sections or exact ordered sections and displays aggregate campus, difficulty, section, contributor, scenario, and two-set feasibility readiness.", "when": "Use it after contribution is Closed.", "avoid": "Configurator pages never display Submitted question content or confidential scenario text.", "result": "The course is READY only when every hard margin and atomic scenario constraint is feasible.", "editable": "Structure is versioned and auditable before the later generation lock."},
                    {"name": "Confidential Review", "does": "Lets the exact assigned eligible reviewer classify Submitted questions into sections and create textual ordered scenarios. Supported scientific notation in Question Text and Choices A-D renders from the unchanged escaped Submitted content.", "when": "Use it after the Configurator saves the Stage 6 blueprint.", "avoid": "Do not copy question, answer, scenario, or potential-set content into broad monitoring or audit metadata.", "result": "Placements and scenarios remain separate overlays; Submitted questions are unchanged.", "editable": "Reviewer overlay changes are audited during the approved pre-lock lifecycle."},
                    {"name": "Generate Sets", "does": "Atomically creates confidential immutable Set A and Set B snapshots from the exact READY input fingerprint and current generation revision.", "when": "Use it only as the assigned eligible reviewer after aggregate readiness is READY.", "avoid": "Do not navigate away while the indeterminate generation message is active, and do not copy generated question or answer content into broad records.", "result": "Both sets persist together with exact quotas, minimum overlap, proportional balance, and soft contributor balancing; stale requests fail with a conflict and duplicate tokens reuse the existing result.", "editable": "Regenerate requires a confidential reason and supersedes rather than rewrites the prior revision. Approval and lock are not yet available."},
                ],
                "avoid": "Do not request or copy confidential question content through administrative monitoring.",
                "next_step": "After aggregate readiness is READY, the assigned reviewer may use Generate Sets. Review the immutable Set A and Set B revision; Approve/Lock remains a separately gated Stage 6C workflow.",
            }
        ],
    },
    {
        "code": "start",
        "title": "Start Here",
        "summary": "Confirm your working scope before opening or changing school records.",
        "topics": [
            {
                "code": "dashboard-scope",
                "title": "Dashboard and Active Scope",
                "audience": "All Admin Portal users",
                "permissions": ["dashboard.read", "admin_portal.access"],
                "purpose": "Shows the records and alerts available for the selected school, campus, academic year, and term.",
                "menu_path": "Admin Portal -> Dashboard",
                "steps": [
                    "Open Dashboard from the left navigation.",
                    "Check the tenant and campus selectors in the top bar.",
                    "Confirm the academic year, term, and active grading period shown on the page.",
                    "Open the relevant alert or queue before taking action.",
                ],
                "check_first": [
                    "Confirm the tenant and campus shown at the top of the page.",
                    "Confirm the active academic year, term, and grading period.",
                    "Read pending alerts before starting setup or review work.",
                ],
                "actions": [
                    {
                        "name": "Change Scope",
                        "does": "Loads records for another permitted tenant or campus.",
                        "when": "Use it before working on another branch.",
                        "avoid": "Do not assume the previous page's campus is still selected.",
                        "result": "Lists, forms, counts, and reports refresh for the selected scope.",
                        "editable": "No school record is changed.",
                    },
                    {
                        "name": "Open Alert or Queue",
                        "does": "Opens the related submissions, reopen requests, corrections, or monitoring page.",
                        "when": "Use it when a dashboard item needs action.",
                        "avoid": "Do not approve an item from its count alone; open and review it.",
                        "result": "The full record and available actions are displayed.",
                        "editable": "Nothing changes until an action is submitted.",
                    },
                ],
                "avoid": "Do not create, approve, import, or print records under the wrong campus or term.",
                "next_step": "Open the work area named in the alert or continue with the required setup.",
            },
        ],
    },
    {
        "code": "academic-setup",
        "title": "Academic and Class Setup",
        "summary": "Prepare the records that must exist before faculty can receive and grade classes.",
        "topics": [
            {
                "code": "academic-records",
                "title": "Academic Years, Terms, Courses, Sections, and Scheduled Classes",
                "audience": "Campus administrators and authorized academic-record staff",
                "permissions": [
                    "academic_years.read",
                    "terms.read",
                    "courses.read",
                    "sections.read",
                    "offerings.read",
                ],
                "purpose": "Builds the school structure used by enrollment, faculty assignments, grading, and reports.",
                "menu_path": (
                    "Admin Portal -> Organization, then Admin Portal -> Academics "
                    "(Academic Years, Terms, Courses, Sections, and Course Offerings)"
                ),
                "steps": [
                    "Set the correct tenant and campus in the top bar.",
                    "Open Organization to verify the campus, department, and program records.",
                    "Open Academics and complete Academic Years, Terms, Courses, and Sections.",
                    "Open Academics -> Course Offerings to create the scheduled class.",
                    "Review the saved offering before proceeding to enrollment and faculty assignment.",
                ],
                "check_first": [
                    "Check for an existing record before creating another one.",
                    "Confirm the campus, academic year, term, department, and program.",
                    "For a scheduled class, confirm its course and section.",
                    "Area Chairmen may review active Course Offerings across sibling academic areas under the same campus College division; this does not expand their authority on other pages.",
                ],
                "actions": [
                    {
                        "name": "Add",
                        "does": "Creates a new academic or class record.",
                        "when": "Use it only when the correct record does not already exist.",
                        "avoid": "Do not create a second record to correct a spelling or mapping error.",
                        "result": "The new record becomes available to later setup pages.",
                        "editable": "Authorized users may edit it while governance rules allow.",
                    },
                    {
                        "name": "Edit",
                        "does": "Corrects the selected record without replacing its history.",
                        "when": "Use it for a wrong name, date, mapping, room, or status.",
                        "avoid": "Do not change a record to represent a different class.",
                        "result": "Pages using that record show the updated information.",
                        "editable": "Yes, subject to permissions and linked-record safeguards.",
                    },
                ],
                "avoid": "Do not deactivate a record that is still used by active classes or gradebooks.",
                "next_step": "Create the scheduled classes, enroll students, and assign faculty.",
            },
            {
                "code": "people-and-classes",
                "title": "Students, Enrollment, and Faculty Assignments",
                "audience": "Registrar staff, campus administrators, and authorized coordinators",
                "permissions": [
                    "students.read",
                    "enrollment.read",
                    "faculty_assignments.read",
                    "faculty_assignments.import",
                    "student_enrollment_query.read",
                ],
                "purpose": "Places the correct students and faculty member in each scheduled class.",
                "menu_path": (
                    "Admin Portal -> Students -> Students; Admin Portal -> Enrollment -> Enrollment; "
                    "Admin Portal -> Academics -> Faculty Assignments"
                ),
                "steps": [
                    "Open Students -> Students and confirm that the student record exists.",
                    "Open Enrollment -> Enrollment and link the student to the correct course offering.",
                    "Open Academics -> Faculty Assignments and assign the correct faculty member.",
                    "Confirm that the assignment is active and wait for the faculty member to accept it.",
                ],
                "check_first": [
                    "Confirm the student or faculty account belongs to the selected campus.",
                    "Confirm the class, section, academic year, and term.",
                    "Check existing enrollment or assignment rows before adding another.",
                ],
                "actions": [
                    {
                        "name": "Enroll or Assign",
                        "does": "Links a student or faculty member to the selected class.",
                        "when": "Use it after the scheduled class and account records are correct.",
                        "avoid": "Do not assign faculty to a class from another campus or term.",
                        "result": "The class appears in the appropriate operational list.",
                        "editable": "It may be updated or deactivated by authorized users.",
                    },
                    {
                        "name": "Unassign or Deactivate",
                        "does": "Stops an active assignment or record from being used.",
                        "when": "Use it when the load or enrollment is officially withdrawn.",
                        "avoid": "Do not use it simply to hide a temporary problem.",
                        "result": "The record remains traceable but is no longer active.",
                        "editable": "Reactivation depends on the page and current governance state.",
                    },
                    {
                        "name": "Bulk Import Faculty Assignments",
                        "does": "Stages and confirms Faculty Assignment CSV rows from Tools -> Bulk Imports.",
                        "when": "Use it for reviewed class loads that already have valid faculty accounts and course offerings.",
                        "avoid": "Do not assume an imported assignment activates an inactive faculty account or grants portal access.",
                        "result": "Valid active or inactive Faculty accounts may be assigned; account status remains unchanged.",
                        "editable": "The importer creates assignments only and keeps existing scope, duplicate, and row-error safeguards.",
                    },
                ],
                "avoid": "Do not treat a pending faculty assignment as an accepted, reviewable gradebook.",
                "next_step": "Ask faculty to review and accept their assigned classes.",
            },
        ],
    },
    {
        "code": "departmental-exam-builder",
        "title": "Departmental Exam Builder",
        "summary": "Create examination cycles, manage faculty contribution, prepare Stage 6 readiness, and generate confidential immutable Set A and Set B revisions.",
        "topics": [
            {
                "code": "departmental-exam-course-control",
                "title": "Exam Cycles and Included or Exempt Courses",
                "audience": "Authorized exam-cycle managers, course configurators, and assigned reviewers",
                "permissions": [
                    "departmental_exams.manage_cycles",
                    "departmental_exams.configure",
                    "departmental_exams.review_generate",
                ],
                "purpose": "Creates a tenant examination-cycle snapshot and lets one responsible exam department operate each grouped course examination shared across every listed campus where that course is offered.",
                "menu_path": "Admin Portal -> Departmental Exam Builder",
                "steps": [
                    "Use Overview / Exam Cycles to create the Midterm or Final examination cycle. The cycle snapshots active offerings and groups them by course.",
                    "Use Assigned Course Examinations to open only courses within your exact current exam responsibility. One grouped examination is shared across its listed campuses, including Cubao, Fairview, and Taytay where applicable; it is not one examination per campus.",
                    "Open Administer to confirm the one responsible exam department and reviewer. Responsibility is not assigned separately per campus. A null or inactive responsible department remains fail-closed for ordinary users.",
                    "Leave a course Included when it will use the departmental examination workflow.",
                    "Choose Exempt only while the cycle is Draft, select the approved category, and enter a specific reason. A saved exam configuration is preserved but dormant; configuration alone does not block Exempt.",
                    "Use Restore to Included only while the cycle is Draft when an exemption must be reversed. Restore preserves the saved configuration, does not use the Exempt-only faculty contribution or question blocker, and revalidates the reviewer assignment.",
                    "On Configure Examination Cycle, authorized cycle managers may set independent 50-75 count defaults, a cycle-wide contribution deadline, and contributor instructions. Review the confirmation page, then use Apply defaults; its short-lived signed POST submits directly to the apply action without putting instructions or reasons in links.",
                    "On Course Examination Configuration, authorized exact-responsibility users may inherit each cycle default or set an intentional course override, then record coverage and optional instructions. Effective values show DEFAULT, OVERRIDE, or NOT CONFIGURED. Removing a deadline override restores the cycle deadline, or NOT CONFIGURED when the cycle has none.",
                    "Use the contribution Close POST only after the explicit roster is current, every currently required Active contributor is Submitted, and every Blocked Draft has current immutable resolution evidence. Close preserves all Submitted questions and prevents later normal Stage 5 mutation.",
                    "After contribution is Closed, use Stage 6 Blueprint for No Sections or exact ordered section quotas. The assigned reviewer uses Confidential Review for question placements and textual atomic scenarios. Aggregate readiness applies fixed campus and 30/50/20 difficulty Hamilton margins plus exact section margins without persisting selected questions.",
                    "When aggregate readiness is READY, the assigned reviewer opens Generate Sets. Generate persists Set A and Set B atomically; Regenerate requires a reason and preserves the superseded revision. The busy indicator is indeterminate and browser-side only, while server locks, idempotency, and stale-input checks remain authoritative.",
                    "For Automatic Generation, use Questionnaire Print Release to choose the exact generated revision and bounded Asia/Manila Print From/Until window. Releasing a replacement revokes but preserves the prior release record; regeneration never makes the newer revision printable automatically.",
                ],
                "check_first": [
                    "Confirm the selected tenant and the responsible department campus.",
                    "Before choosing Exempt, confirm the course has no faculty contribution or question work. That downstream activity blocks Exempt only; a saved exam configuration is preserved and becomes dormant while Exempt.",
                    "Confirm the assigned reviewer still has an active exact-department role and review/generate permission.",
                ],
                "actions": [
                    {
                        "name": "Exempt",
                        "does": "Changes an Included course to Exempt and records the category, reason, actor, time, reviewer, and scope in the audit trail.",
                        "when": "Use it for an approved course that should not enter the departmental written-exam workflow.",
                        "avoid": "Do not use it after faculty contribution or question work has begun.",
                        "result": "Any saved exam configuration is preserved but dormant. The assigned reviewer may still see the course as read-only, but no later exam action is permitted.",
                        "editable": "The course may be restored while the cycle remains Draft. No downstream data is deleted.",
                    },
                    {
                        "name": "Restore to Included",
                        "does": "Reverses an exemption while preserving the original course, offering, responsible-department snapshots, and saved exam configuration.",
                        "when": "Use it when the course must participate again before later exam work starts.",
                        "avoid": "Do not treat Restore as a way to bypass an inactive department. Faculty contribution or question activity is an Exempt-only blocker, not a Restore blocker.",
                        "result": "An eligible reviewer is retained; an ineligible reviewer is cleared and must be reassigned.",
                        "editable": "Restore is allowed while the cycle remains Draft. No downstream data is deleted, and the Included course can proceed to later stages only after their separate controls are implemented.",
                    },
                    {
                        "name": "Questionnaire Print Release",
                        "does": "Revalidates automatic generation-management authority across every participating campus, then releases one exact complete Set A/Set B revision for a bounded faculty window.",
                        "when": "Use it after confirming the intended immutable revision and print schedule. Release a regenerated revision explicitly when it should replace the prior printable version.",
                        "avoid": "Do not assume the current or newest generation is printable, and do not copy confidential question content into release notes or audits.",
                        "result": "Currently assigned faculty can print sanitized Set A and Set B only inside the active window; release and access events are audited without question content or answers.",
                        "editable": "The active release may be revoked or replaced. Historical release rows remain auditable and are never rewritten to another revision.",
                    },
                ],
                "avoid": "Do not confuse Included with a separate action. New cycle courses start Included; Restore is the only reverse of Exempt.",
                "next_step": "Review the generated immutable Set A and Set B revision, then create the exact faculty print release when scheduled. PDF, Pair Code, and QR outputs remain later gated stages.",
            },
            {
                "code": "departmental-exam-planning-readiness",
                "title": "Planning & Readiness",
                "audience": "Authorized academic supervisors and exam-planning staff",
                "permissions": ["departmental_exams.view_planning_readiness"],
                "purpose": "Provides a read-only, authorization-filtered view of live active/open offerings, enrollment, teaching-assignment acceptance, Faculty-role readiness, and TMP account activation.",
                "menu_path": "Admin Portal -> Departmental Exam Builder -> Planning & Readiness",
                "steps": [
                    "Select an authorized Academic Year and Term, then optionally narrow by Exam Department, campus, assignment status, Faculty Active, or TMP Account Status.",
                    "Review Exam Department, course, and overall totals. Exam Department grouping comes only from Course.exam_department; unassigned courses appear only with global department scope for their exact campus.",
                    "Use the faculty/section rows to identify accepted assignments, inactive Faculty-role scope, accounts that are not activated, and active/open offerings with no active faculty assignment.",
                    "Open Print / Printer-Friendly View only when both view_planning_readiness and print_planning_readiness are assigned for a shared exact scope.",
                ],
                "check_first": [
                    "Confirm the current active tenant and the campuses and Exam Departments you are authorized to review.",
                    "Remember that Faculty Active, TMP Account Status, and assignment acceptance are separate indicators.",
                ],
                "actions": [
                    {
                        "name": "Apply Filters",
                        "does": "Narrows rows and every subtotal/overall total within the already authorized offering base.",
                        "when": "Use it to focus onboarding, enrollment, and answer-sheet planning follow-up.",
                        "avoid": "Do not interpret an unavailable filter choice as evidence that another campus or department has matching records.",
                        "result": "Only authorized matching offerings, rows, and totals remain visible.",
                        "editable": "The report is read-only and changes no academic, assignment, user, or examination record.",
                    },
                    {
                        "name": "Print / Printer-Friendly View",
                        "does": "Renders the same filtered report with a clean institutional heading and print-safe grouped tables.",
                        "when": "Use it only for an authorized operational copy.",
                        "avoid": "Print permission alone does not provide report access; both permissions must overlap in the requested scope.",
                        "result": "The printed totals and rows remain restricted to the shared authorized scope.",
                        "editable": "Printing is read-only and does not create an export record or alter report data.",
                    },
                ],
                "avoid": "Do not use faculty home department, ordinary course department, or offering department as the report's Exam Department grouping.",
                "next_step": "Follow up on unassigned offerings, pending acceptance, inactive Faculty roles, or unactivated accounts through their existing authorized workflows.",
            },
            {
                "code": "departmental-exam-confidential-output",
                "title": "Questionnaire Output, Answer Keys, and Generation Audits",
                "audience": "Authorized Automatic-generation managers, Admin printers, and generation auditors",
                "permissions": [
                    "departmental_exams.view_generated_exams",
                    "departmental_exams.print_generated_exams",
                    "departmental_exams.manage_exam_generation",
                    "departmental_exams.audit_generated_exams",
                    "departmental_exams.release_answer_keys",
                ],
                "purpose": "Operates exact-revision Faculty releases and Admin-only confidential output, then reviews deterministic selection and integrity evidence without exposing raw fingerprints.",
                "menu_path": "Admin Portal -> Departmental Exam Builder -> Automatic Generation Summary or Questionnaire Print Release",
                "steps": [
                    "On Questionnaire Print Release, confirm the exact course and revision before setting Print From and Print Until. Faculty print buttons appear only for that released revision while the common Asia/Manila window is active; regeneration requires a separate release for the newer revision.",
                    "For several courses, select one generated revision per course in Bulk Print Release, confirm the selected count, and apply one common window. The batch is all-or-nothing, while each course keeps an independent exact-revision release record and history.",
                    "Use Print Set A or Print Set B for Admin-only printing when Faculty release is unnecessary. Choose Letter (default), A4, or Legal before opening the browser print dialog. Supported scientific notation in Question Text and Choices A-D renders from escaped immutable snapshots before printing. Admin Direct Print does not create, replace, or extend a Faculty release.",
                    "Open the confidential Set A or Set B Answer Key only for the intended exact revision. Answer keys are restricted to authorized users and are never provided to Faculty through questionnaire printing.",
                    "For a separate Faculty Answer Key release, select the exact current-final revision, set Available From and Available Until, and confirm that all examination sessions for the course have concluded. A superseded revision is immediately blocked and a newer revision requires its own explicit release.",
                    "In Question Selection Audit, summary counts mean: Submitted Questions is the persisted eligible source-row total; Unique Logical is the number of logical questions after equivalence grouping; Duplicate/Equivalent Copies is Submitted minus Unique Logical; Set A and Set B are their persisted item totals; Overlap is the number of logical questions selected in both sets; Selected Unique is the union selected by either set; Not Selected is the authoritative logical source pool outside that union.",
                    "Audit columns are No. (displayed report line), Question, Contributor, Campus / Context, Difficulty, Correct Answer, Source (Q### r#), Set A position, Set B position, and Equivalence Status.",
                    "Equivalence Status is Unique, Selected representative (EQ-###), Equivalent copy not selected (EQ-###), or Unselected equivalent group (EQ-###). EQ-### is an audit-friendly group label, never a raw hash.",
                    "For a fast review, use Summary -> Selected in Both -> Duplicate/Equivalent -> Not Selected.",
                    "Automatic Generation Audit reports PASS when every available deterministic integrity check passes, WARNING when an unavailable legacy source snapshot prevents a complete check without proving failure, and FAIL when a deterministic check detects a mismatch. This is not AI judgment; newly generated revisions retain full source-audit evidence, while older revisions may legitimately show unavailable-check warnings.",
                ],
                "check_first": [
                    "Confirm the tenant, course, exact revision number, and intended Set A or Set B.",
                    "Confirm the Faculty release window and selected count before submitting a single or bulk release.",
                    "Treat answer keys, correct answers, contributor details, and audit reports as confidential output.",
                ],
                "actions": [
                    {
                        "name": "Release Exact Revision",
                        "does": "Creates or replaces one course's Faculty print release for the selected immutable revision and Print From/Until window.",
                        "when": "Use it when Faculty should print that exact revision during a bounded window.",
                        "avoid": "Do not assume regeneration transfers the release to a newer revision.",
                        "result": "Faculty Set A and Set B buttons are available only during the active window and only under current assignment authorization.",
                        "editable": "A later release replaces the active row while preserving the earlier row as revoked history.",
                    },
                    {
                        "name": "Bulk Print Release",
                        "does": "Applies one common window to several selected course/revision pairs in one all-or-nothing submission.",
                        "when": "Use it after verifying the selected count and one exact revision per course.",
                        "avoid": "Do not mix unauthorized, cross-tenant, incomplete, or multiple same-course revisions in one batch.",
                        "result": "Each course receives its own independently governed revision-bound release record.",
                        "editable": "Each resulting release may later be replaced or individually revoked through the existing controls.",
                    },
                    {
                        "name": "Admin Direct Print",
                        "does": "Opens sanitized Print Set A or Print Set B output for an exact revision without a Faculty release, with Letter (default), A4, and Legal paper choices.",
                        "when": "Use it for authorized Admin-only printing cases.",
                        "avoid": "Do not treat direct printing as permission for Faculty access.",
                        "result": "A private, no-store questionnaire is rendered without answers or internal audit metadata; supported scientific notation finishes rendering before the print dialog opens.",
                        "editable": "Printing is read-only and does not change Faculty release state.",
                    },
                    {
                        "name": "Faculty Answer Key Release",
                        "does": "Releases exact Set A/B correct-answer snapshots under a separate bounded window after the all-sessions-concluded confirmation.",
                        "when": "Use it only after all examination sessions for the grouped course have concluded.",
                        "avoid": "Do not reuse the Questionnaire window or assume a regenerated revision inherits access.",
                        "result": "Currently assigned Faculty receive separate View and Print actions for both sets only inside the active window.",
                        "editable": "The active release may be revoked or replaced; every prior row remains immutable history.",
                    },
                    {
                        "name": "Answer Key / Selection Audit",
                        "does": "Shows confidential exact-revision Set A/B answers and authoritative selection/equivalence evidence.",
                        "when": "Use it for authorized academic review of the persisted revision.",
                        "avoid": "Do not give these reports to Faculty or interpret EQ-### as a raw fingerprint.",
                        "result": "The requested historical or current revision is shown without silent substitution.",
                        "editable": "Reports are read-only and access-audited.",
                    },
                    {
                        "name": "Run Automatic Audit",
                        "does": "Runs deterministic revision-bound source, digest, membership, quota, overlap, and integrity checks.",
                        "when": "Use it when an authorized audit result is required for that exact revision.",
                        "avoid": "Do not interpret a legacy unavailable-check WARNING as a proven failure or as AI judgment.",
                        "result": "An immutable PASS, WARNING, or FAIL audit run is retained for the revision.",
                        "editable": "Prior audit runs remain immutable; a later run creates separate history.",
                    },
                ],
                "avoid": "Never disclose questionnaires, answer keys, correct answers, contributor identities, or audit evidence outside authorized operations.",
                "next_step": "Confirm the exact revision and window, complete the required print or audit task, and retain confidential output only through approved handling procedures.",
            },
        ],
    },
    {
        "code": "grading-setup",
        "title": "Grading Setup",
        "summary": "Define how activities become component, period, and final grades.",
        "topics": [
            {
                "code": "grading-template",
                "title": "Grade Formula Setup",
                "audience": "Authorized grading administrators and template reviewers",
                "permissions": [
                    "grading_templates.read",
                    "template_periods.read",
                    "template_components.read",
                    "template_subcomponents.read",
                    "template_details.read",
                ],
                "purpose": "Defines grading periods, major components, subcomponents, activity types, score-entry rules, and weights.",
                "menu_path": "Admin Portal -> Grading -> Grading Templates",
                "steps": [
                    "Open Grading -> Grading Templates from the left navigation.",
                    "To create a new setup, click Add Template. To review an existing setup, find the template row.",
                    "Click the Builder icon on the template row.",
                    "Inside Builder, click Add Period, then add or edit the major Components for that period.",
                    "Under each major component, add or edit Subcomponents such as Quizzes or Participation/Output.",
                    "Under Participation/Output, add the allowed Detail Items and set Detail Computation to Average Activities when required by policy.",
                    "Confirm that the major component weights total 100% for the period and that each lower level has a valid positive setup.",
                    "Return to Grading Templates and click Test Calculator to verify sample results.",
                    "When the draft is complete, use Submit for Approval, Review Approval, and Publish according to the configured governance route.",
                ],
                "check_first": [
                    "Confirm the template is for the correct program or course group.",
                    "Check that every required weight total is 100%.",
                    "Check whether each subcomponent uses Weighted Details or Average Activities.",
                    "Test the formula before approval or publication.",
                ],
                "actions": [
                    {
                        "name": "Builder",
                        "does": "Opens the complete template structure.",
                        "when": "Use it to review periods, components, subcomponents, and details together.",
                        "avoid": "Do not change a live template without checking affected classes.",
                        "result": "Saved changes affect classes that resolve to that template according to governance rules.",
                        "editable": "Draft records are editable; approved or published records may require formal change control.",
                    },
                    {
                        "name": "Testing Calculator",
                        "does": "Shows sample results without saving student grades.",
                        "when": "Use it before approval, publication, or assignment.",
                        "avoid": "Do not treat calculator samples as official grades.",
                        "result": "A preview explains the formula path and computed sample.",
                        "editable": "No grade records are changed.",
                    },
                    {
                        "name": "Submit for Approval / Approve / Publish",
                        "does": "Moves the template through governance and makes an approved setup available.",
                        "when": "Use each action only after the previous review stage is complete.",
                        "avoid": "Do not publish a template with incomplete weights or missing periods.",
                        "result": "The template status and approval history are updated.",
                        "editable": "Published changes may require a hotfix or a new template version.",
                    },
                ],
                "avoid": "Do not change a published template used by live classes without an approved impact review. TeacherMate+ uses Raw Score Base-50 for faculty score encoding.",
                "next_step": "Assign the published template to the correct courses and term.",
            },
            {
                "code": "template-hotfix",
                "title": "Change a Published Template Using a Hotfix",
                "audience": "Authorized template hotfix requesters, reviewers, and final approvers",
                "permissions": [
                    "template_hotfixes.read",
                    "template_hotfixes.create",
                    "template_hotfixes.review",
                ],
                "purpose": "Provides a governed route for an urgent change to a published grading template already used by classes.",
                "menu_path": (
                    "Admin Portal -> Grading -> Grading Templates -> Hotfix, then "
                    "Admin Portal -> Grading -> Template Hotfix Requests"
                ),
                "steps": [
                    "Before changing the template, export or record the current grade summaries and identify which gradebooks are Draft, Submitted, Reopened, or Locked.",
                    "Open Grading -> Grading Templates and find the published template.",
                    "Click the Hotfix icon on that template row.",
                    "Choose the apply mode: Future Only, Active Not Submitted, Selected Offerings, or Requesting Faculty's Accepted Offerings.",
                    "For Selected Offerings, select only the affected active classes.",
                    "Enter a clear academic justification that describes the requested structure, weight, or computation change and its intended effective date.",
                    "Click Create Hotfix Request.",
                    "An authorized reviewer opens Grading -> Template Hotfix Requests, opens the request, and checks the impact preview and affected classes.",
                    "Complete every configured review step. At the final apply step, enter the required decision reason and type APPLY HOTFIX.",
                    "Review the Applied result, affected offering count, recomputed offering count, and any skipped classes.",
                ],
                "check_first": [
                    "Confirm that the template is published and that you selected the correct tenant.",
                    "Identify every course and class currently resolving to the shared template.",
                    "Confirm whether any affected grading period has already been submitted or locked.",
                    "Check that revised component weights and subcomponent settings remain valid.",
                    "Decide whether the change is for future classes, all active unsubmitted classes, or selected offerings only.",
                ],
                "actions": [
                    {
                        "name": "Hotfix",
                        "does": "Opens the request form for the selected published grading template.",
                        "when": "Use it for an approved urgent correction that cannot wait for a future template version.",
                        "avoid": "Do not use it merely to experiment with a live formula.",
                        "result": "A pending governed hotfix request is created; no grade changes occur yet.",
                        "editable": "The request is reviewed before final application.",
                    },
                    {
                        "name": "Apply Mode",
                        "does": "Controls which offerings are selected for immediate recomputation after final approval.",
                        "when": "Choose the narrowest scope that satisfies the approved academic policy.",
                        "avoid": "Do not choose a broad mode without reviewing every affected class.",
                        "result": "The impact preview identifies the target offerings.",
                        "editable": "The selected mode belongs to the submitted request and should be checked before approval.",
                    },
                    {
                        "name": "Approve & Apply",
                        "does": "Records the final approval and runs the approved recomputation for eligible target offerings.",
                        "when": "Use it only after checking the impact preview, weights, submission states, and justification.",
                        "avoid": "Do not apply when the request would improperly alter submitted grades.",
                        "result": "Eligible unsubmitted offerings are recomputed; submitted offerings in restricted modes are skipped and reported.",
                        "editable": "Submitted official grades still require the Correction of Grades workflow.",
                    },
                ],
                "avoid": (
                    "Do not assume Selected Offerings creates separate template structures. The template remains shared; "
                    "the selected scope controls immediate recomputation. Never use a hotfix to silently replace official submitted grades."
                ),
                "next_step": (
                    "Verify an affected faculty gradebook and its Explain This Grade result. Use Correction of Grades for any approved change to an already submitted official grade."
                ),
                "workflow": {
                    "starts": "An authorized admin, CAO, or other configured requester creates the hotfix request.",
                    "reviews": "The configured hotfix reviewer checks the academic reason, scope, and impact preview.",
                    "approves": "The configured final hotfix approver applies the request using the required confirmation.",
                    "receives": "Affected faculty and academic operations receive the implemented policy instructions through institutional communication.",
                    "complete": "The request is Applied or Rejected, eligible offerings are processed, and skipped offerings are documented.",
                    "records": "Request scope, justification, workflow decisions, reviewers, timestamps, affected offerings, recomputation results, skipped classes, and audit logs.",
                },
            },
            {
                "code": "grading-assignment",
                "title": "Assign the Grade Setup and Verify Coverage",
                "audience": "Campus administrators and authorized grading administrators",
                "permissions": [
                    "course_template_assignments.read",
                    "course_base_overrides.read",
                    "tenant_grading_profiles.read",
                ],
                "purpose": "Connects an approved grade formula to the classes that faculty will use.",
                "menu_path": (
                    "Admin Portal -> Grading -> Course Template Assignments; "
                    "Admin Portal -> Grading -> Tenant Grading Profiles"
                ),
                "steps": [
                    "Open Grading -> Course Template Assignments.",
                    "Click Bulk Assign Template.",
                    "Choose the published grading template and the intended courses.",
                    "For the normal regular template, leave Effective term blank. This makes it the default template for the course.",
                    "For a Summer-only template, choose the exact Summer term. That Summer row overrides the default only during that Summer term.",
                    "Save the assignment and use the missing-template filters to confirm coverage.",
                    "After Summer, the course automatically uses the blank/default regular template again. Do not reassign the regular template unless a later term needs a different rule.",
                    "If institution-wide formula or final-grade behavior is needed, open Grading -> Tenant Grading Profiles and review the matching profile.",
                    "Open a sample faculty class and confirm that it resolves to the intended template.",
                ],
                "check_first": [
                    "Confirm the template is approved and published.",
                    "Confirm the course, term, and campus coverage.",
                    "Review the missing-template coverage indicators.",
                    "Check whether any class has a special Base-50 override.",
                ],
                "actions": [
                    {
                        "name": "Assign Grade Setup",
                        "does": "Links a published template to one or more courses.",
                        "when": "Use it before faculty begins creating activities.",
                        "avoid": "Do not create conflicting assignments for the same scope.",
                        "result": "Affected classes resolve to the selected grading template.",
                        "editable": "Changes after encoding starts require careful impact review.",
                    },
                    {
                        "name": "Regular and Summer Templates",
                        "does": "Keeps the regular template as the blank/default assignment and uses a separate exact-term row for Summer.",
                        "when": "Use it when the same course is offered in regular terms and Summer with different grading periods.",
                        "avoid": "Do not edit the regular four-period template just to make it fit Summer.",
                        "result": "Summer classes use the Summer template; later regular classes return to the regular default template.",
                        "editable": "Create these rows before faculty creates activities, scores, submissions, or locks.",
                    },
                    {
                        "name": "Base-Value Exception",
                        "does": "Overrides the normal score-conversion base for a specific class.",
                        "when": "Use it only for an approved academic exception.",
                        "avoid": "Do not use it to correct individual student scores.",
                        "result": "Future recomputation uses the approved class base value.",
                        "editable": "Yes, but live-grade changes must be reviewed and audited.",
                    },
                ],
                "avoid": "Do not open encoding while any active class lacks a valid grade setup.",
                "next_step": "Set the active grading period and deadline controls, then verify a sample faculty class.",
            },
        ],
    },
    {
        "code": "submission-governance",
        "title": "Submission, Reopening, and Corrections",
        "summary": "Monitor completion and use the correct route when grades need more work.",
        "topics": [
            {
                "code": "submission-monitor",
                "title": "Gradebook Submission Monitor",
                "audience": "Campus administrators and authorized academic reviewers",
                "permissions": ["grade_submissions.read", "grading_periods.read"],
                "purpose": "Shows whether each period gradebook is Draft, Submitted, or Reopened.",
                "menu_path": (
                    "Admin Portal -> Grading -> Submissions; "
                    "Admin Portal -> Grading -> Period Locks"
                ),
                "steps": [
                    "Open Grading -> Submissions.",
                    "Set the campus, academic year, term, and grading period filters.",
                    "Find the course and section, then open its gradebook review.",
                    "For deadline or access questions, open Grading -> Period Locks and check the applicable rule.",
                    "Use the overdue report for gradebooks that remain unsubmitted after the deadline.",
                ],
                "check_first": [
                    "Confirm the campus, term, and grading period.",
                    "Check the accepted faculty assignment and deadline.",
                    "Open the gradebook before deciding that a displayed result is wrong.",
                ],
                "actions": [
                    {
                        "name": "View Gradebook",
                        "does": "Opens a read-only faculty gradebook for review.",
                        "when": "Use it to inspect readiness, scores, and computed summaries.",
                        "avoid": "Do not interpret Draft as approved or posted; those are not ordinary submission statuses.",
                        "result": "The current gradebook is displayed and the access is auditable.",
                        "editable": "The reviewer does not edit faculty scores from this view.",
                    },
                    {
                        "name": "Overdue Report",
                        "does": "Lists accepted classes that remain unsubmitted after the deadline.",
                        "when": "Use it for daily compliance follow-up.",
                        "avoid": "Do not include pending or declined faculty assignments as gradebook non-compliance.",
                        "result": "The report identifies the course, section, period, faculty, and missing readiness records.",
                        "editable": "The report itself changes nothing.",
                    },
                ],
                "avoid": "Do not describe a Submitted gradebook as formally approved or posted unless a future workflow adds those statuses.",
                "next_step": "Follow up an overdue draft, review a reopen request, or use correction governance for submitted grades.",
            },
            {
                "code": "reopen",
                "title": "Gradebook Reopen Requests",
                "audience": "Reviewers explicitly assigned by the Superadmin for the affected scope",
                "permissions": ["reopen_requests.read", "reopen_requests.review"],
                "purpose": "Temporarily restores encoding or submission access to an eligible locked or overdue gradebook.",
                "menu_path": "Admin Portal -> Grading -> Reopen Requests",
                "steps": [
                    "Open Grading -> Reopen Requests.",
                    "Filter the queue to the affected campus and current request status.",
                    "Open the request and verify the course, section, faculty member, period, deadline, and justification.",
                    "Choose Approve or Reject and enter clear review remarks.",
                    "After approval, monitor the class until the faculty member resubmits within the allowed window.",
                ],
                "check_first": [
                    "Confirm the request belongs to your assigned tenant and campus.",
                    "Read the faculty justification and verify the deadline and submission status.",
                    "Confirm that reopening, rather than correction, is the correct route.",
                ],
                "actions": [
                    {
                        "name": "Approve",
                        "does": "Creates a limited reopen window for the requested gradebook.",
                        "when": "Use it when the request is valid and further encoding or submission is justified.",
                        "avoid": "Do not approve a submitted post-deadline grade change that belongs in Correction of Grades.",
                        "result": "The request and gradebook status are updated and faculty can work during the allowed window.",
                        "editable": "Faculty may edit until resubmission or expiry.",
                    },
                    {
                        "name": "Reject",
                        "does": "Closes the request without reopening the gradebook.",
                        "when": "Use it when the reason is insufficient or the wrong workflow was used.",
                        "avoid": "Do not reject without clear review remarks.",
                        "result": "The decision and remarks are recorded.",
                        "editable": "A new request may be filed when justified.",
                    },
                ],
                "avoid": "Do not approve requests outside your assigned campus scope.",
                "next_step": "Monitor the gradebook until faculty resubmits it.",
                "workflow": {
                    "starts": "Faculty files the request.",
                    "reviews": "An explicitly assigned scoped reviewer checks it.",
                    "approves": "The same authorized reviewer approves or rejects it.",
                    "receives": "Faculty receives the decision and, if approved, temporary access.",
                    "complete": "The gradebook is resubmitted or the reopen window expires.",
                    "records": "Request status, reviewer, remarks, timestamps, submission status, and audit logs.",
                },
            },
            {
                "code": "corrections",
                "title": "Correction of Grades",
                "audience": "Faculty, assigned reviewers, academic approvers, and registrar staff",
                "permissions": ["corrections.read", "corrections.review", "corrections.create_on_behalf"],
                "purpose": "Handles audited changes to grades that have already been submitted.",
                "menu_path": (
                    "Admin Portal -> Grading -> Correction Queue; "
                    "Admin Portal -> Grading -> Create Correction On Behalf"
                ),
                "steps": [
                    "Open Grading -> Correction Queue to review an existing request.",
                    "Open the request and compare the original value, requested value, student, grading item, reason, and attachment.",
                    "Record the current approval-step decision with clear remarks.",
                    "If authorized staff must start the request, open Grading -> Create Correction On Behalf.",
                    "Complete the configured approval route and verify the recomputed official result after final approval.",
                ],
                "check_first": [
                    "Confirm the gradebook was submitted.",
                    "Check the original value, requested value, student, activity, and reason.",
                    "Review supporting attachments and the configured approval route.",
                ],
                "actions": [
                    {
                        "name": "Create on Behalf",
                        "does": "Starts a correction request for a faculty member when authorized.",
                        "when": "Use it only with a documented institutional reason.",
                        "avoid": "Do not bypass the configured reviewers.",
                        "result": "A pending correction request and audit record are created.",
                        "editable": "It follows the same approval rules as a faculty request.",
                    },
                    {
                        "name": "Approve / Reject",
                        "does": "Records the review decision at the current approval step.",
                        "when": "Approve only when the evidence and corrected values are complete.",
                        "avoid": "Do not approve from the list page without opening the request.",
                        "result": "Final approval applies eligible score changes, recomputes grades, and preserves the audit trail.",
                        "editable": "Further changes require another correction request.",
                    },
                    {
                        "name": "Official Report",
                        "does": "Generates or opens the correction document when enabled.",
                        "when": "Use it after the required approval stage.",
                        "avoid": "Do not print an unapproved request as an official correction.",
                        "result": "A traceable registrar reference is produced.",
                        "editable": "The report reflects the stored correction record.",
                    },
                ],
                "avoid": "Never edit database values or ask faculty to overwrite submitted grades outside the correction workflow.",
                "next_step": "Complete the configured approval route and provide the official result to the registrar.",
                "workflow": {
                    "starts": "Faculty or an authorized admin creates the request.",
                    "reviews": "The configured academic reviewer checks the evidence and values.",
                    "approves": "The final configured approver makes the final decision.",
                    "receives": "Faculty and configured registrar recipients receive the result.",
                    "complete": "The approved change is applied or the request is rejected/closed.",
                    "records": "Original and corrected values, reasons, attachments, approval steps, recomputed grades, and audit logs.",
                },
            },
        ],
    },
    {
        "code": "reports-monitoring",
        "title": "Reports and Monitoring",
        "summary": "Use reports for checking and official follow-through, not as substitutes for source records.",
        "topics": [
            {
                "code": "reports",
                "title": "Operational and Official Reports",
                "audience": "Authorized campus, academic, and registrar staff",
                "permissions": [
                    "grading_analytics.read",
                    "grade_distribution_monitor.read",
                    "faculty_analytics.read",
                    "faculty_final_clearance.read",
                    "audit_logs.read",
                ],
                "purpose": "Supports academic review, submission follow-up, clearance, audit, and printing.",
                "menu_path": (
                    "Admin Portal -> Grading -> Grading Analytics or Grade Distribution Monitor; "
                    "Admin Portal -> Academics -> Faculty Final Clearance; Admin Portal -> Audit"
                ),
                "steps": [
                    "Open the report or monitor that answers the operational question.",
                    (
                        "For Grading Analytics and Grade Distribution Monitor, TeacherMate+ starts with the "
                        "faculty members you supervise, then includes their accepted teaching assignments. "
                        "A shared course does not need a separate copy for every campus or department."
                    ),
                    (
                        "An Area Chair sees faculty in the chair's assigned department. A College Dean sees "
                        "faculty through the Area Chairs assigned within the Dean's campuses and departments."
                    ),
                    "Set the campus, academic year, term, period, course, or status filters before reviewing results.",
                    (
                        "On Grading Analytics, choose one Course code to show every authorized section or "
                        "offering for that code. The rows stay separate; the filter does not combine sections."
                    ),
                    (
                        "Use Search to find a course code, course title, section, or supervised faculty name. "
                        "Course and Search work together, and Clear returns to the normal scoped view."
                    ),
                    "Open the underlying class or record when a count or warning needs confirmation.",
                    "Generate, export, or print only after confirming that the source status is appropriate.",
                ],
                "check_first": [
                    "Confirm campus, academic year, term, period, and report date.",
                    "Confirm whether the source gradebook is Draft, Submitted, or Reopened.",
                    "Check that student and faculty scope is correct before export or printing.",
                ],
                "actions": [
                    {
                        "name": "Filter",
                        "does": "Narrows the report without changing records.",
                        "when": "Use it before review, export, or printing.",
                        "avoid": "Do not assume an unfiltered report represents only your intended scope.",
                        "result": "The visible rows and totals refresh.",
                        "editable": "No records are changed.",
                    },
                    {
                        "name": "Export / Print / Generate PDF",
                        "does": "Creates a file or printable view from the current records.",
                        "when": "Use it after confirming scope, status, names, and dates.",
                        "avoid": "Do not distribute a draft or reopened gradebook as final.",
                        "result": "A report or document is produced; some official documents receive reference and verification codes.",
                        "editable": "Change the source record through its proper workflow, then regenerate.",
                    },
                ],
                "avoid": "Do not use prediction, analytics flags, or monitor counts as official student grades.",
                "next_step": "Resolve any source-record issue, then regenerate the report if needed.",
            },
        ],
    },
    {
        "code": "security",
        "title": "Accounts and Access",
        "summary": "Give users only the access they need and keep account changes traceable.",
        "topics": [
            {
                "code": "users",
                "title": "Users and Login Security",
                "audience": "Authorized account administrators",
                "permissions": [
                    "users.read",
                    "users.update",
                    "faculty_users.view_import",
                    "faculty_users.import",
                ],
                "purpose": "Maintains user identity, campus defaults, account state, passwords, and login lockouts.",
                "menu_path": "Admin Portal -> Security -> Users or Admin Portal -> Security -> Login Lockouts",
                "steps": [
                    "Open Security -> Users and search by username, name, or email.",
                    "Open the existing account before deciding to create a new one.",
                    "Verify identity, email, default tenant, default campus, active status, and assigned roles.",
                    "For many new Faculty accounts, choose Import Faculty CSV, download the official template, upload it, and review every row before confirming.",
                    "For a temporary login block, open Security -> Login Lockouts and verify the user before unlocking.",
                ],
                "check_first": [
                    "Search for the user before creating another account.",
                    "Confirm the registered email, campus, department, and active status.",
                    "Remember that portal access comes from RBAC permissions, not the Is staff field.",
                ],
                "actions": [
                    {
                        "name": "Create / Edit User",
                        "does": "Creates or updates the user's identity record.",
                        "when": "Use it after checking for an existing username or email.",
                        "avoid": "Do not grant portal access by guessing from account flags.",
                        "result": "The account is saved; role assignment controls what the user can open.",
                        "editable": "Yes, by authorized account administrators.",
                    },
                    {
                        "name": "Import Faculty CSV",
                        "does": "Creates inactive Faculty login accounts and assigns the exact scoped Faculty role from the official CSV template.",
                        "when": "Use it for approved bulk Faculty account onboarding after checking existing usernames and email addresses.",
                        "avoid": "Do not add role, password, active, staff, permission, or email-control columns. Do not enable invitation email outside an approved production rollout.",
                        "result": "Use the flow-diagram icon beside the upload-page title to view the onboarding steps. Valid rows are created or safely skipped. Faculty can sign in only after a valid invitation is accepted.",
                        "editable": "Conflicts are not changed by the importer; reconcile them manually through the approved account workflow.",
                    },
                    {
                        "name": "Send / Resend Faculty Invitation",
                        "does": "Shows the Faculty invitation state and sends a new secure password-setup link without creating another account.",
                        "when": "Open the Faculty user from Security -> Users when an invitation was disabled, not requested, failed, expired, or needs replacement.",
                        "avoid": "Do not resend within five minutes or after the account has accepted the invitation and become login-ready.",
                        "result": "A successful send uses the Account Onboarding email design, starts a fresh 24-hour period, and invalidates the previous link. After password setup, the Faculty user returns to /faculty/ to sign in.",
                        "editable": "The action stays available on the user record for authorized administrators within scope.",
                    },
                    {
                        "name": "Unlock",
                        "does": "Clears an active temporary login lockout.",
                        "when": "Use it after verifying the user's identity and reason.",
                        "avoid": "Do not unlock repeated suspicious attempts without investigation.",
                        "result": "The user may try signing in again.",
                        "editable": "Future failed attempts can create another lockout.",
                    },
                ],
                "avoid": "Do not share temporary credentials or administrative URLs through unsecured channels.",
                "next_step": "Assign only the required scoped role and verify the user's portal access.",
            },
        ],
    },
    {
        "code": "superadmin",
        "title": "Superadmin System Control",
        "summary": "Sensitive institution-wide configuration visible only to Superadmin users.",
        "superadmin_only": True,
        "topics": [
            {
                "code": "system-control",
                "title": "Tenants, Roles, Permissions, Menus, and High-Risk Tools",
                "audience": "Superadmin only",
                "permissions": [
                    "tenants.read",
                    "roles.read",
                    "permissions.read",
                    "menus.read",
                    "actual_data_reset.run",
                    "system_settings.update",
                ],
                "purpose": "Controls institution-wide access, navigation, configuration, and emergency operational tools.",
                "menu_path": (
                    "Admin Portal -> Security, Navigation, Tools, or Organization, depending on the approved system task"
                ),
                "steps": [
                    "Confirm the exact institution-wide change and its approved scope.",
                    "Open the relevant Superadmin-only page from Security, Navigation, Tools, or Organization.",
                    "Review affected roles, permissions, tenants, campuses, and active users before saving.",
                    "After saving, test the result using a scoped non-Superadmin account and review the audit log.",
                ],
                "check_first": [
                    "Confirm the tenant and intended impact.",
                    "Review affected users, roles, campuses, and records.",
                    "Record the reason and obtain institutional authorization for critical changes.",
                ],
                "actions": [
                    {
                        "name": "Change Permissions",
                        "does": "Changes which pages and actions a role can use.",
                        "when": "Use it only after reviewing affected active users.",
                        "avoid": "Do not grant broad access to solve a single user's temporary problem.",
                        "result": "Access changes take effect according to the saved role and scope.",
                        "editable": "Yes, with a new audited permission change.",
                    },
                    {
                        "name": "Configure Menus",
                        "does": "Changes which permitted navigation links are displayed.",
                        "when": "Use it after the related permission and route are ready.",
                        "avoid": "Do not mistake hiding a menu for removing server permission.",
                        "result": "The portal navigation changes for matching users.",
                        "editable": "Yes.",
                    },
                    {
                        "name": "Delete Operational Data",
                        "does": "Runs the protected Actual Data Reset process.",
                        "when": "Use it only for an approved reset with verified backups and exact scope.",
                        "avoid": "Never use it as a cleanup shortcut or without a recovery plan.",
                        "result": "Selected operational records are permanently removed and the action is audited.",
                        "editable": "No. Recovery depends on a valid backup.",
                    },
                ],
                "avoid": "Do not give Campus Admin users Superadmin guidance or system-wide controls.",
                "next_step": "Verify affected portals using test accounts and review the audit log.",
            },
        ],
    },
    {
        "code": "tools-data-control",
        "title": "Tools and Data Control",
        "summary": "High-risk tools that require extra verification before data leaves or changes the system.",
        "topics": [
            {
                "code": "secure-tenant-data-export",
                "title": "Secure Tenant Data Export",
                "audience": "Superadmin and authorized Tenant Admin users",
                "permissions": ["tenant_data_export.execute"],
                "purpose": (
                    "Creates a tenant-scoped SQLite investigation file after password confirmation and email OTP verification."
                ),
                "menu_path": "Admin Portal -> Tools -> Secure Tenant Data Export",
                "steps": [
                    "Set the correct tenant scope before opening the tool.",
                    "Choose only the tenant approved for investigation.",
                    "Confirm the confidentiality acknowledgement and enter your own current password.",
                    "Open your registered account email and enter the six-digit verification code.",
                    "Download the SQLite file and store it only in an approved secure location.",
                ],
                "check_first": [
                    "Confirm written authorization and the exact tenant to export.",
                    "Confirm your Admin account has a working registered email address.",
                    "Confirm where the file will be stored, who may access it, and when it must be deleted.",
                ],
                "actions": [
                    {
                        "name": "Verify Password and Send Code",
                        "does": "Checks your current password and sends a short-lived code to your registered email.",
                        "when": "Use it only when you are ready to complete the export.",
                        "avoid": "Do not use another user's account or email to approve the export.",
                        "result": "A one-time verification challenge is created and audited.",
                        "editable": "No tenant data is exported until the email code is verified.",
                    },
                    {
                        "name": "Verify and Download",
                        "does": "Verifies the email code and streams the tenant SQLite export once.",
                        "when": "Use it after confirming the selected tenant and code.",
                        "avoid": "Do not email the file or place it in public/shared storage.",
                        "result": "The challenge is consumed and the download is audited.",
                        "editable": "The exported file is read-only evidence; changes to it do not update TeacherMate+.",
                    },
                ],
                "avoid": "Do not use this tool for routine reports, backups, or cross-tenant browsing.",
                "next_step": "Review the exported manifest and row-count tables before using the file for investigation.",
            },
            {
                "code": "faculty-feedback",
                "title": "Faculty Feedback",
                "audience": "Superadmin, Tenant Admin, and scoped Campus Admin users",
                "permissions": ["faculty_feedback.read", "faculty_feedback.export"],
                "purpose": "Reviews Faculty Portal page ratings and optional short suggestions submitted from the floating Feedback button.",
                "menu_path": "Admin Portal -> Tools -> Faculty Feedback",
                "steps": [
                    "Open Faculty Feedback from Tools.",
                    "Check the summary counts for Happy, Neutral, Sad, and suggestions.",
                    "Filter by tenant, campus, rating, date, faculty, page feature, or suggestion status.",
                    "Open long suggestions only when needed for review.",
                    "Export CSV only when you are authorized to handle the filtered records.",
                ],
                "check_first": [
                    "Confirm your selected tenant and campus scope.",
                    "Do not treat feedback as a ticketing or grade-correction workflow.",
                    "Handle suggestions as confidential because faculty may type sensitive information by mistake.",
                ],
                "actions": [
                    {
                        "name": "Apply Filters",
                        "does": "Narrows the dashboard and summary counts using your authorized tenant and campus scope.",
                        "when": "Use it to review one campus, rating, date range, faculty user, page feature, or suggestion status.",
                        "avoid": "Do not assume filters can override your RBAC scope.",
                        "result": "Only matching in-scope feedback remains visible.",
                        "editable": "Feedback records are not changed.",
                    },
                    {
                        "name": "Export CSV",
                        "does": "Downloads the currently filtered in-scope feedback rows as CSV.",
                        "when": "Use it for authorized review or reporting.",
                        "avoid": "Do not share exported suggestions in unsecured channels.",
                        "result": "The export is audited and formula-like text is neutralized.",
                        "editable": "No feedback records are changed.",
                    },
                ],
                "avoid": "Do not copy confidential suggestion text into ordinary logs, email threads, or public documents.",
                "next_step": "Use the feedback trend to improve the related Faculty Portal page or guide text.",
            },
            {
                "code": "orientation-feedback",
                "title": "Orientation Feedback Surveys",
                "audience": "Superadmin and explicitly authorized orientation facilitators",
                "permissions": [
                    "orientation_feedback.view",
                    "orientation_feedback.manage",
                    "orientation_feedback.start",
                    "orientation_feedback.close",
                    "orientation_feedback.cancel",
                    "orientation_feedback.view_analytics",
                    "orientation_feedback.export",
                ],
                "purpose": "Runs separate QR-based Faculty and Academic Heads orientation surveys and reports answers without respondent names.",
                "menu_path": "Admin Portal -> Tools -> Orientation Feedback",
                "steps": [
                    "Set the correct tenant and campus scope, then create the required survey type.",
                    "Review the title, orientation time, eligible roles, question wording, and required items while the survey is a draft.",
                    "Start the survey to freeze its question and eligible-user snapshots and activate the public QR link and registered-email verification codes.",
                    "Display the facilitator screen and monitor only the aggregate completed count.",
                    "End the survey immediately after the response period, or cancel it with a clear reason when the event cannot continue.",
                    "Open aggregate analytics after closure. Detailed results and CSV become available only after at least five completed responses.",
                ],
                "check_first": [
                    "Confirm the selected tenant and campus before creating or opening a survey.",
                    "Confirm Faculty and academic-head roles are active and correctly scoped; inactive accounts may still respond when their qualifying role remains active.",
                    "Confirm the public wording says identity is used for eligibility and duplicate prevention while answers are reported without names.",
                ],
                "actions": [
                    {
                        "name": "Start Survey",
                        "does": "Freezes the eligible roster and published question snapshot and activates registered-email OTP verification.",
                        "when": "Use it when the facilitator is ready to accept responses.",
                        "avoid": "Do not start before verifying the survey type, role eligibility, scope, and wording.",
                        "result": "The QR/public link accepts one response per eligible user for this survey session.",
                        "editable": "Published wording and scoring are no longer editable.",
                    },
                    {
                        "name": "End Survey",
                        "does": "Stops new validation and submission immediately and preserves completed responses.",
                        "when": "Use it at the end of the response period.",
                        "avoid": "Do not expect already validated respondents to receive a grace period.",
                        "result": "Aggregate analytics and export become available.",
                        "editable": "Historical question meaning remains frozen.",
                    },
                    {
                        "name": "Cancel Survey",
                        "does": "Stops responses immediately, records the required reason, and preserves existing submissions.",
                        "when": "Use it only when the session should not count as an official completed survey.",
                        "avoid": "Do not cancel merely to edit or reopen a published survey.",
                        "result": "Analytics and exports are clearly marked Cancelled.",
                        "editable": "Cancelled sessions cannot be reopened in this release.",
                    },
                ],
                "avoid": "Do not ask for respondent passwords, expose participation identities beside answers, or treat email-only validation as strong authentication.",
                "next_step": "Review aggregate trends and anonymous comments without trying to identify individual respondents.",
            },
        ],
    },
]


def _is_superadmin(user, *, tenant_id: int | None, campus_id: int | None) -> bool:
    if getattr(user, "is_superuser", False):
        return True
    return PermissionService._scoped_user_roles(
        user,
        tenant_id=tenant_id,
        campus_id=campus_id,
    ).filter(role__code="SUPER_ADMIN").exists()


def build_admin_help_sections(*, user, tenant_id: int | None, campus_id: int | None) -> list[dict]:
    permission_codes = PermissionService.get_effective_permission_codes(
        user,
        tenant_id=tenant_id,
        campus_id=campus_id,
    )
    is_superadmin = _is_superadmin(user, tenant_id=tenant_id, campus_id=campus_id)
    visible_sections = []
    for section in ADMIN_HELP_SECTIONS:
        if section.get("superadmin_only") and not is_superadmin:
            continue
        visible_topics = []
        for topic in section["topics"]:
            required = set(topic.get("permissions", []))
            if required and not (required & permission_codes):
                continue
            visible_topics.append(deepcopy(topic))
        if visible_topics:
            visible_section = deepcopy(section)
            visible_section["topics"] = visible_topics
            visible_sections.append(visible_section)
    return visible_sections

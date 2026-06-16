from __future__ import annotations

from copy import deepcopy

from apps.core.services.permissions import PermissionService


ADMIN_HELP_SECTIONS = [
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
                ],
                "avoid": "Do not treat a pending faculty assignment as an accepted, reviewable gradebook.",
                "next_step": "Ask faculty to review and accept their assigned classes.",
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
                    "Choose the published grading template, the intended courses, and the effective term.",
                    "Save the assignment and use the missing-template filters to confirm coverage.",
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
                "permissions": ["users.read", "users.update"],
                "purpose": "Maintains user identity, campus defaults, account state, passwords, and login lockouts.",
                "menu_path": "Admin Portal -> Security -> Users or Admin Portal -> Security -> Login Lockouts",
                "steps": [
                    "Open Security -> Users and search by username, name, or email.",
                    "Open the existing account before deciding to create a new one.",
                    "Verify identity, email, default tenant, default campus, active status, and assigned roles.",
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

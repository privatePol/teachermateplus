from __future__ import annotations


FACULTY_HELP_SECTIONS = [
    {
        "code": "start",
        "title": "Start and Check Your Classes",
        "topics": [
            {
                "code": "login",
                "title": "Sign In and Confirm Your Account",
                "purpose": "Opens the Faculty Portal using your own TeacherMate+ account.",
                "check_first": ["Use your assigned username.", "Confirm the page says Faculty Portal.", "Use Forgot Password only for your own account."],
                "actions": [
                    {"name": "Start with Login", "does": "Signs you in and opens your faculty work area.", "when": "Use it after entering your correct credentials.", "avoid": "Do not use another faculty member's account.", "result": "Your dashboard and permitted classes are displayed. If a password change is required, navigation stays collapsed and locked until the new password is saved successfully.", "editable": "No grades change."},
                    {"name": "Forgot Password", "does": "Sends a reset link to an eligible faculty account.", "when": "Use it when you cannot remember your password.", "avoid": "Do not repeatedly request links; use the newest email.", "result": "A reset notification is sent when the account is valid.", "editable": "Only the password changes after successful reset."},
                ],
                "avoid": "Never share your password, OTP, or reset link.",
                "next_step": "Open My Classes and check your assigned teaching load.",
            },
            {
                "code": "assignments",
                "title": "Assigned Classes",
                "purpose": "Shows the classes officially assigned to you for the selected academic scope.",
                "check_first": ["Check the campus, academic year, and term.", "Check the course, section, schedule, room, and grading template.", "Report a wrong class before accepting it."],
                "actions": [
                    {"name": "Accept", "does": "Confirms that the teaching assignment is correct.", "when": "Use it after checking all class details.", "avoid": "Do not accept a class that is not yours.", "result": "The class moves to Accepted Course Assignments and grading actions become available.", "editable": "Undo Acceptance may be available before grading work begins."},
                    {"name": "Request Clarification", "does": "Returns the assignment to admin with your concern.", "when": "Use it for a wrong course, section, schedule, or load detail.", "avoid": "Do not accept first when the assignment is clearly incorrect.", "result": "Admin can review your message and correct the assignment.", "editable": "You may respond again after admin resolves it."},
                    {"name": "Decline", "does": "Records that you cannot accept the assignment.", "when": "Use it only for an official load issue.", "avoid": "Do not decline merely because setup is incomplete; request clarification instead.", "result": "The assignment remains visible for administrative follow-up.", "editable": "Admin governs any later reassignment."},
                ],
                "avoid": "Do not encode scores until the class list and grading template are correct.",
                "next_step": "Open Grading, choose a period, and review the required activity categories.",
            },
            {
                "code": "classlist",
                "title": "Class List and Student Status",
                "purpose": "Shows the official students included in your assigned class and the status used for grading readiness.",
                "check_first": ["Confirm the course and section.", "Compare the list with the official enrollment record.", "Check non-active statuses such as DRP, WITHDRAWN, or INC carefully."],
                "actions": [
                    {"name": "Class List", "does": "Opens the current enrolled-student list.", "when": "Use it before encoding and whenever enrollment changes are reported.", "avoid": "Do not add or remove students outside the permitted roster workflow.", "result": "The active class roster and available status actions are displayed.", "editable": "Roster editing depends on the school's Class Master List Ownership setting."},
                    {"name": "Save Status", "does": "Records an allowed student enrollment status change.", "when": "Use it only with the required academic basis and within the allowed period.", "avoid": "Do not mark DRP, WITHDRAWN, or INC merely to bypass missing-score checks.", "result": "The student's grading readiness and report status are updated.", "editable": "Later changes remain subject to policy and audit."},
                ],
                "avoid": "Report a missing or extra student immediately; do not encode a different student under the wrong record.",
                "next_step": "Return to the period gradebook after the official roster is correct.",
            },
        ],
    },
    {
        "code": "encode",
        "title": "Create Activities and Record Results",
        "topics": [
            {
                "code": "activities",
                "title": "Activities",
                "purpose": "Creates the quizzes, recitations, assignments, activities, examinations, or other items students will complete.",
                "check_first": ["Choose the correct grading period and category.", "Enter the correct highest possible score.", "Check the effective Score Entry rule shown by the form."],
                "actions": [
                    {"name": "Add Activity", "does": "Creates one graded item.", "when": "Use it when the item will be scored for the class.", "avoid": "Do not create duplicate activity names for the same task.", "result": "The item appears on the Activities, Score Entry, and Summary pages.", "editable": "It can be edited before submission while the gradebook is open."},
                    {"name": "Edit", "does": "Corrects the activity title, date, score, or category.", "when": "Use it before encoding is finalized.", "avoid": "Do not change the highest score after encoding without reviewing every student's result.", "result": "The summary recomputes from the updated activity information.", "editable": "Only while governance permits."},
                    {"name": "Delete", "does": "Removes an eligible activity.", "when": "Use it only when the item should not be part of grading.", "avoid": "Do not delete an activity to hide low or missing scores.", "result": "The item no longer contributes to the gradebook.", "editable": "A deleted item may need to be recreated if removed by mistake."},
                ],
                "avoid": "For Average Activities, create only the activities that should count; each created activity contributes equally inside that subcomponent.",
                "next_step": "Open Encode Scores for the activity.",
            },
            {
                "code": "scores",
                "title": "Encode Scores: Blank, Zero, and Base-50",
                "purpose": "Records each student's actual result for one activity.",
                "check_first": ["Confirm the activity and highest possible score.", "Confirm the student list.", "Check whether the rule is Raw Score Base-50 or Direct Percentage."],
                "actions": [
                    {"name": "Save Scores", "does": "Saves the entered student scores.", "when": "Use it after reviewing every value on the page.", "avoid": "Do not leave the page with unsaved changes.", "result": "Saved scores are included in the summary computation.", "editable": "Yes, while the gradebook remains open."},
                    {"name": "Clear Score", "does": "Removes the student's score record.", "when": "Use it when a value was entered by mistake and the real score is not yet known.", "avoid": "Do not clear a score to mean zero.", "result": "The score becomes blank and may block submission.", "editable": "A valid score can be entered later."},
                ],
                "avoid": "A saved 0 is complete and counts in computation. A blank score is missing and can block submission. Under Raw Score Base-50, raw 0 is transmuted to 50. Under Direct Percentage, an entered 0 remains 0.",
                "next_step": "Save, then open Summary and check the computed averages.",
            },
            {
                "code": "attendance",
                "title": "Attendance",
                "purpose": "Records student attendance for a dated class session and supplies attendance grading when required.",
                "check_first": ["Confirm the session date and class.", "Check that all active students are listed.", "Use the status that matches the official attendance record."],
                "actions": [
                    {"name": "Add Session", "does": "Creates a dated attendance meeting.", "when": "Use it for an actual class session.", "avoid": "Do not create duplicate sessions for the same meeting.", "result": "The session becomes available for attendance encoding.", "editable": "Yes, while the gradebook is open."},
                    {"name": "Save Attendance", "does": "Saves Present, Late, Absent, or Excused entries.", "when": "Use it after checking the entire class.", "avoid": "Do not leave a required status blank.", "result": "Attendance records and any required attendance score are updated.", "editable": "Yes, while the gradebook is open."},
                ],
                "avoid": "Absent is a recorded result, not a blank record. Current attendance scoring uses Present 100, Excused 100, Late 90, and Absent 0.",
                "next_step": "Open Summary and resolve any missing attendance records.",
            },
        ],
    },
    {
        "code": "understand",
        "title": "Understand and Check the Grade",
        "topics": [
            {
                "code": "summary",
                "title": "Summary of Periodic Grades",
                "purpose": "Shows activity scores, category averages, component totals, and the computed period grade.",
                "check_first": ["Check the highest possible score row.", "Look for blanks, unexpected zeros, and wrong student statuses.", "Confirm all expected activities appear."],
                "actions": [
                    {"name": "Summary", "does": "Displays the current computed gradebook.", "when": "Use it after every major encoding session and before submission.", "avoid": "Do not submit based only on one activity page.", "result": "The latest saved records are shown using the assigned template.", "editable": "The summary itself is read-only; change source scores or activities."},
                    {"name": "Grade Explanation", "does": "Shows how one student's grade was produced.", "when": "Use it when explaining a grade or checking an unexpected result.", "avoid": "Do not compare students or disclose another student's records.", "result": "The calculation path and source values are displayed.", "editable": "No records change."},
                    {"name": "Who Viewed", "does": "Shows recorded gradebook viewing activity.", "when": "Use it when checking access history.", "avoid": "Do not treat a view record as proof that a user changed a grade.", "result": "Available access records are displayed.", "editable": "No."},
                ],
                "avoid": "Q.AVE means Quiz Average, R.AVE means Recitation Average, P/O AVE means Participation/Output Average, and CS AVE means the weighted Class Standing Average. The Summary uses separate color bands for Quizzes, Participation/Output, and CS AVE to make these groups easier to follow.",
                "next_step": "Correct source records, then return to Summary until the gradebook is complete.",
            },
            {
                "code": "computation",
                "title": "How the Grade Is Computed",
                "purpose": "Explains the calculation in simple terms.",
                "check_first": ["Identify the score-entry rule.", "Identify whether detail computation is Weighted Details or Average Activities.", "Check the component and period weights."],
                "actions": [],
                "avoid": "Raw Score Base-50 converts a score using: score percentage x 50, then add 50. Example: 15 out of 20 becomes 87.50. Direct Percentage uses the entered 0-to-100 value without Base-50 conversion. Weighted Details uses configured detail percentages. Average Activities averages all faculty-created activities equally inside the subcomponent. Component weights then produce the period grade, and the school grading profile combines period grades into the final grade.",
                "next_step": "Use Grade Explanation for a specific student and report a template issue if the assigned formula is wrong.",
            },
        ],
    },
    {
        "code": "submit",
        "title": "Submit, Reopen, or Correct Grades",
        "topics": [
            {
                "code": "submission",
                "title": "Submit Period Grades",
                "purpose": "Finalizes the current period gradebook and records it as Submitted.",
                "check_first": ["Resolve every missing required score and attendance record.", "Check all active students and non-active statuses.", "Review Summary and printed output if required.", "Confirm the correct class and period."],
                "actions": [
                    {"name": "Submit Period Grades", "does": "Finalizes period rows and records the gradebook submission.", "when": "Use it only when the gradebook is complete and checked.", "avoid": "Do not submit to test whether the gradebook is ready.", "result": "The status becomes Submitted, snapshots are stored, and normal editing stops.", "editable": "Only after an allowed reopen or approved correction route."},
                ],
                "avoid": "Submission is not the same as a separate approval or posting step. TeacherMate+ currently records ordinary gradebooks as Draft, Submitted, or Reopened.",
                "next_step": "Confirm that the period card and submission monitor show Submitted.",
                "workflow": {"starts": "Faculty prepares and submits the gradebook.", "reviews": "Faculty checks readiness first; authorized staff may monitor the submitted result.", "approves": "There is no separate ordinary gradebook approval status.", "receives": "TeacherMate+ stores the submitted gradebook and makes it available to permitted reports and monitors.", "complete": "The gradebook status is Submitted with complete required records.", "records": "Period grades, final grades when applicable, submission snapshots, submitter, time, and audit records."},
            },
            {
                "code": "reopen",
                "title": "Reopen an Unfinished Gradebook",
                "purpose": "Returns an eligible gradebook to editable status when more work is required.",
                "check_first": ["Check whether the gradebook is submitted.", "Check whether the deadline or lock has passed.", "Read the action shown on the period card."],
                "actions": [
                    {"name": "Reopen Before Deadline", "does": "Returns an eligible submitted gradebook to editable status before the deadline.", "when": "Use it when a valid correction is found before the deadline and policy permits.", "avoid": "Do not reopen without a clear reason and plan to resubmit.", "result": "The status becomes Reopened and editing resumes.", "editable": "Yes, until resubmission or a later lock."},
                    {"name": "Request Gradebook Reopen", "does": "Asks an assigned reviewer to restore access after a deadline or lock.", "when": "Use it for an overdue unsubmitted gradebook that still needs encoding or submission.", "avoid": "Do not use it for a submitted post-deadline grade change.", "result": "A pending request is sent to authorized scoped reviewers.", "editable": "Only after approval and during the allowed window."},
                ],
                "avoid": "After the deadline, changes to an already submitted gradebook normally belong in Correction of Grades.",
                "next_step": "If approved, finish the exact work requested and resubmit before access expires.",
            },
            {
                "code": "corrections",
                "title": "Correction of Grades",
                "purpose": "Requests an audited change to a grade that was already submitted.",
                "check_first": ["Choose the correct class, period, student, and activity.", "Confirm the original and corrected value.", "Prepare a clear reason and supporting document when required."],
                "actions": [
                    {"name": "Request Correction", "does": "Creates a correction request for review.", "when": "Use it for a genuine submitted-grade error.", "avoid": "Do not use it for an unfinished draft gradebook.", "result": "The request enters the configured approval route.", "editable": "The grade does not change until final approval."},
                    {"name": "Finalize Request", "does": "Confirms the correction details and sends them for review.", "when": "Use it after checking every student and value in the request.", "avoid": "Do not finalize incomplete or unsupported values.", "result": "Reviewers can approve or reject the request.", "editable": "Later changes may require a new request."},
                    {"name": "Official Report", "does": "Opens the correction document when available.", "when": "Use it after the required approval stage.", "avoid": "Do not present a pending request as approved.", "result": "A traceable correction reference is displayed or generated.", "editable": "No; it reflects the stored request."},
                ],
                "avoid": "Never ask an administrator to directly overwrite submitted grades outside the correction workflow.",
                "next_step": "Monitor the request until it is approved, rejected, closed, or returned for follow-up.",
                "workflow": {"starts": "Faculty or an authorized admin creates the request.", "reviews": "The configured reviewer checks the evidence and values.", "approves": "The final configured approver decides.", "receives": "Faculty and configured registrar recipients receive the result.", "complete": "Approved changes are applied and recomputed, or the request is formally closed.", "records": "Original/corrected values, reason, attachments, decisions, recomputed grades, and audit logs."},
            },
        ],
    },
    {
        "code": "reports",
        "title": "Reports, Student Support, and Security",
        "topics": [
            {
                "code": "prediction",
                "title": "Grade Prediction and Student Intervention",
                "purpose": "Provides an unofficial estimate and a working list of students who may need academic follow-up.",
                "check_first": ["Confirm prediction is enabled for your role.", "Check whether activities or periods are still incomplete.", "Compare any concern with the official gradebook."],
                "actions": [
                    {"name": "Prediction", "does": "Shows current, possible, or target-needed estimates based on available records.", "when": "Use it for planning and early student support.", "avoid": "Do not present it as an official grade or guarantee.", "result": "An advisory calculation is displayed without changing official grades.", "editable": "What-if scenarios are temporary and do not edit the gradebook."},
                    {"name": "Student Intervention Monitor", "does": "Groups students who may need a record check or follow-up.", "when": "Use it to decide who needs attention first.", "avoid": "Do not label or discipline a student based only on a prediction.", "result": "A privacy-safe working list is shown.", "editable": "It does not change official grades."},
                ],
                "avoid": "Predictions depend on current saved records and can change when new scores are entered.",
                "next_step": "Review the student's actual records and provide appropriate academic support.",
            },
            {
                "code": "reports",
                "title": "Print and Export",
                "purpose": "Produces periodic grades, class tabulation, correction reports, or Final Clearance when available.",
                "check_first": ["Confirm class, period, student list, and submission status.", "Check names, dates, grades, and page scope.", "Use only the latest approved or submitted source record."],
                "actions": [
                    {"name": "Print Periodic Grades / Tabulation", "does": "Opens a printable grade report.", "when": "Use it after checking the Summary.", "avoid": "Do not distribute a Draft or Reopened gradebook as final.", "result": "A printable view or PDF is produced.", "editable": "Correct the source gradebook through the proper workflow, then regenerate."},
                    {"name": "Generate Final Clearance PDF", "does": "Creates the official faculty completion document when all required classes are complete.", "when": "Use it only when the page says all requirements are complete.", "avoid": "Do not treat a blocked clearance as a system error before checking each class.", "result": "A report with verification details is stored and generated.", "editable": "Regeneration follows current verified records."},
                ],
                "avoid": "Prediction pages are advisory and must not be printed or presented as official grades.",
                "next_step": "Securely provide the report only to its authorized recipient.",
            },
            {
                "code": "notes",
                "title": "Private Reminders and Memos",
                "purpose": "Keeps personal teaching reminders and follow-up notes inside your account.",
                "check_first": ["Confirm the note belongs to your own faculty work.", "Keep wording professional and necessary.", "Avoid storing sensitive information that does not belong in a reminder."],
                "actions": [
                    {"name": "Save Memo / Reminder", "does": "Stores a private note or task for your account.", "when": "Use it for class follow-up, deadlines, or work reminders.", "avoid": "Do not use it as an official grade-change, disciplinary, or medical record.", "result": "The item appears in your reminder or memo workspace.", "editable": "It may be edited, completed, snoozed, pinned, or removed according to the page."},
                ],
                "avoid": "Private notes do not replace official communication or approval workflows.",
                "next_step": "Complete or remove the reminder when the follow-up is finished.",
            },
            {
                "code": "support-security",
                "title": "Student Support, Privacy, and Logout",
                "purpose": "Supports follow-up while protecting student information and your account.",
                "check_first": ["Use student information only for your assigned classes.", "Verify identity before discussing a grade.", "Save all changes before leaving a page."],
                "actions": [
                    {"name": "Logout", "does": "Ends your authenticated session.", "when": "Use it whenever leaving the device.", "avoid": "Do not rely only on closing the browser tab.", "result": "Protected actions require sign-in again.", "editable": "Unsaved work is not preserved."},
                ],
                "avoid": "Do not leave grade pages open on a shared device or share screenshots containing student records.",
                "next_step": "Log out and close the browser when work is complete.",
            },
        ],
    },
]

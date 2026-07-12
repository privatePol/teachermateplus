FACULTY_OPERATIONAL_POLICY_STATUS = (
    "These operational guidelines explain the proper use of TeacherMate+. "
    "Institutional academic, registrar, privacy, and records-management policies remain controlling."
)

FACULTY_OPERATIONAL_POLICY_SECTIONS = [
    {
        "code": "account-security", "title": "Account Security",
        "must": ["Use only your assigned faculty account.", "Keep your password, OTP, reset link, and active session private.", "Log out whenever you leave the device, especially on a shared computer.", "Report suspected unauthorized access immediately."],
        "must_not": ["Allow another person to encode or submit grades using your account.", "Rely only on closing the browser tab when leaving a shared device."],
    },
    {
        "code": "assignments-class-list", "title": "Faculty Assignment and Class List",
        "must": ["Verify the course, section, campus, term, and class list before encoding.", "Accept only classes officially assigned to you.", "Report missing, incorrect, transferred, or withdrawn students through the official class-list workflow.", "Recheck the class list whenever an enrollment change is announced."],
        "must_not": ["Encode grades while the assignment, class list, or grading template is incorrect.", "Add or remove students outside the authorized roster workflow.", "Use DRP, WITHDRAWN, or INC merely to bypass missing-score checks."],
    },
    {
        "code": "activities-scores-attendance", "title": "Activities, Scores, and Attendance",
        "must": ["Confirm the grading period, category, activity title, date, and highest possible score.", "Encode only verified scores and save your work before leaving the page.", "Use attendance statuses that match the official attendance record.", "Review the computation after changing an activity or its highest possible score."],
        "must_not": ["Create duplicate activities or attendance sessions.", "Delete an activity to hide low or missing scores.", "Use a blank score to mean zero, or zero to mean not yet encoded."],
        "note": "A saved 0 is a recorded score and counts in computation. A blank or cleared score is missing and may block submission.",
    },
    {
        "code": "review-submission", "title": "Grade Review and Submission",
        "must": ["Review the Summary after major encoding work and before submission.", "Check the class, period, student list, missing records, zeroes, statuses, activities, and computed grades.", "Submit only when the gradebook is complete and carefully checked.", "Confirm that the period status becomes Submitted."],
        "must_not": ["Submit merely to test whether the gradebook is ready.", "Distribute a Draft or Reopened gradebook as an official submitted result.", "Assume submission means that the grades are already posted in Pinnacle-AIMS."],
    },
    {
        "code": "reopen-correction", "title": "Reopen and Correction of Grades",
        "must": ["Use Gradebook Reopen Request for unfinished or unsubmitted work after a deadline or lock.", "Finish and resubmit within an approved reopen window.", "Use Correction of Grades for a genuine change to an already submitted grade.", "Provide the correct student, original value, corrected value, reason, and supporting document when required."],
        "must_not": ["Use a reopen request for a submitted post-deadline grade change.", "Present a pending correction as approved.", "Ask an administrator or technical user to overwrite a submitted grade outside the correction workflow."],
    },
    {
        "code": "reports-external-system", "title": "Reports and Pinnacle-AIMS",
        "must": [
            "After grades are processed and submitted in TeacherMate+, ensure that the final periodic grades are encoded separately in Pinnacle-AIMS through the institution's authorized procedure.",
            "Report enrollment differences through the authorized class-list or Registrar workflow so TeacherMate+ can reflect approved changes.",
        ],
        "must_not": [
            "Assume that submitting grades in TeacherMate+ automatically sends or posts them to Pinnacle-AIMS.",
            "Treat TeacherMate+ as a replacement for PINNACLE/AIMS.",
        ],
        "note": "Pinnacle-AIMS remains the official source for enrollment information. TeacherMate+ should reflect authorized enrollment changes but does not replace the Registrar's enrollment system.",
    },
    {
        "code": "privacy-consultation", "title": "Student Privacy and Consultation",
        "must": ["Discuss grades privately with the student concerned.", "Use the selected-student consultation view when showing information to a student.", "Secure printed and downloaded grade records according to institutional policy."],
        "must_not": ["Expose classmates' names, grades, attendance, or other protected records.", "Leave grade pages open where unauthorized people can see them.", "Share screenshots containing student records through unauthorized channels."],
    },
    {
        "code": "proper-use", "title": "Proper Use of TeacherMate+",
        "must": ["Use predictions and intervention indicators only as advisory academic-support tools.", "Use the official workflows for grade corrections, roster changes, approvals, and urgent concerns.", "Report system errors with the page, class, period, time, and a privacy-safe description."],
        "must_not": ["Present a prediction as an official or guaranteed grade.", "Put student names, grades, passwords, or confidential records in Faculty Feedback.", "Use Feedback, Notes, or Reminders as substitutes for official requests and approvals.", "Attempt to bypass permissions, locks, deadlines, or approval workflows."],
    },
]

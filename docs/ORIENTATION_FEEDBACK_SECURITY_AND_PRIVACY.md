# Orientation Feedback Security and Privacy

## Product classification

Orientation Feedback is confidential, email-verified feedback for TeacherMate+ Faculty and Academic Heads orientation sessions. It is not a competence assessment, employee evaluation, or attendance record.

The feature is separate from Exit Pulse. It reuses the proven token-fragment, QR, CSRF, no-cache, rate-limit, transaction, and audit patterns without changing Exit Pulse models or behavior.

## Registered-email verification

TeacherMate+ currently stores globally unique `accounts.User.email` and `username` values but has no separate faculty number or employee number. The public survey therefore asks only for the registered institutional email address.

Validation:

1. Trims the submitted email and matches it case-insensitively.
2. Searches only the survey session's frozen eligible-participation rows.
3. Returns the same neutral failure message for invalid, ineligible, ambiguous, and unknown addresses.
4. Clears the submitted address from rendered error forms.
5. Sends a six-digit, short-lived one-time code only to the matched registered email address.
6. Stores the hashed code and attempt/expiry state on the protected participation row; plaintext codes are never stored.
7. Requires the code in the same browser and Django session that requested it before the response form is released.
8. Does not display the matched name, username, or email on the public verification page.

Raw validation email is not copied into survey responses, URLs, public tokens, hidden identity fields, ordinary application logs, analytics, or exports.

Knowing an eligible address is not sufficient to submit. Control of that registered mailbox is verified with the one-time code. OTP expiry and attempt limits are environment-configurable; successful verification remains bound to the same survey, browser cookie, and server-side Django session.

## Eligibility and inactive accounts

Starting a survey creates an immutable eligible-user snapshot:

- Faculty survey: active `FACULTY` role in the exact tenant and selected campus, or a tenant-scoped Faculty role that covers all campuses.
- Academic Heads survey: at least one active configured role in the session tenant/campus or an existing global governance scope. Initial role selections are `AC`, `COLLEGE_DEAN`, and `CAO` when those roles exist.
- `User.is_active` is not used as an eligibility filter. An inactive account may respond when its qualifying role assignment and role definition remain active.
- An inactive qualifying role does not grant eligibility.
- Eligibility is evaluated separately per session. A dual-role user may respond once to each separate Faculty and Academic Heads survey.

The database uniqueness boundary is survey session plus user, not user across all surveys.

## Confidentiality boundary

`OrientationSurveyParticipation` stores the eligible user, role-code snapshot, validation time, and completion time. `OrientationSurveyResponse`, question answers, and selected choices are stored separately. A protected internal one-to-one participation relationship supports duplicate prevention and auditability, so the feature does not claim cryptographic anonymity.

Routine facilitator, analytics, anonymous-comment, and aggregate-export code does not load or display respondent identity. Standard CSV exports exclude names, user IDs, usernames, email addresses, and participation-response linkage. Completion audit events do not store answer text, raw validation email, response UUID, participation ID, user ID, IP address, user agent, or request route.

There is no participation/nonresponder export or identity-reveal interface in this release.

## Public-link protections

- A 32-byte random URL-safe bearer token is unique per survey.
- The QR link stores the token in the URL fragment, removes it from browser history, and sends it through a CSRF-protected same-origin POST.
- Public pages use `noindex,nofollow,noarchive`, `Cache-Control: no-store`, and `Referrer-Policy: same-origin`.
- Public routes contain no survey, user, participation, response, question, or answer database IDs.
- Validation, OTP verification, and submission use browser and hashed-IP rate limits. Throttling is audited without raw email.
- All public answer POST fields are marked sensitive so Django technical-error reports cleanse ratings, selections, Other text, and comments.
- Lifecycle, feature availability, eligibility, duplicate state, question membership, and response choices are revalidated server-side.
- Final submission locks the session and participation in one transaction and relies on database uniqueness for race-safe duplicate prevention.
- Scores come only from frozen server-side response choices. Posted numeric scores, user IDs, role codes, tenant IDs, and campus IDs are ignored.

Production should use an approved shared Django cache if coordinated rate limits across multiple Gunicorn workers are required. Database lifecycle and uniqueness checks remain authoritative.

## Lifecycle and historical integrity

- `DRAFT`: public submission is blocked; settings and wording may be edited; QR is preview-only.
- `OPEN`: start actor/time, eligible count, question snapshot version, and public-link activation are recorded.
- `CLOSED`: validation and submission stop immediately. There is no five-minute grace period in this release.
- `CANCELLED`: a reason and actor/time are required; existing responses are preserved; analytics and exports are clearly marked cancelled.

Reopening is not implemented. Published question wording, requirements, response labels, scores, ordering, reverse-scoring flags, and composite membership are immutable through supported application paths. Direct ORM bulk update/delete of published snapshots is prohibited for maintenance scripts and future internal tools.

## Analytics and exports

Eligible/completed counts and response rate are operational measures. Detailed per-session analytics provide overall rating, confidence, readiness, weighted means, scale-specific interpretations, choice distributions, guidance-area counts, optional composites, and anonymous comments only after at least five responses are completed.

Reverse scoring uses `6 - original score` only inside a configured composite. Original question reports always retain the submitted score and original wording. Flexibility and personal-communication preference questions are not automatically classified as resistance.

The server suppresses detailed results, comments, graphs, composites, and CSV export for zero through four completed responses, including cancelled sessions. Five completed responses release the complete aggregate report. The environment may raise this threshold but cannot lower it below five. Segmented subgroup analytics and cross-survey person correlation are not implemented. CSV export is aggregate, identifies cancelled sessions, and neutralizes formula-like text. PDF and participation-tracking exports are not implemented.

## Permissions and feature control

The tenant setting `FEATURE_ORIENTATION_FEEDBACK_ENABLED` controls availability and defaults On. When disabled, the menu and all new/public survey activity are unavailable; authorized users may still read or export ended historical sessions that satisfy the privacy threshold. Admin actions require their dedicated `orientation_feedback.*` permission plus active tenant/campus scope. The migration grants management permissions only to the active `SUPER_ADMIN` role by default.

## Validation commands

Run:

```powershell
python manage.py test apps.orientation_feedback -v 1
python manage.py test apps.exit_pulse -v 1
python manage.py test apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_configurable_features_renders_card_headings_for_targeted_sections -v 1
python manage.py check
python manage.py migrate --check
python manage.py makemigrations --check --dry-run
```

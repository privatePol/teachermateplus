# Exit Pulse Security and Privacy

## Product classification

Exit Pulse is confidential, identity-validated classroom feedback. It supports instructional reflection and is not attendance, grading, faculty evaluation, ranking, or administrator monitoring.

Students do not log in, but they must enter a student number that resolves to an active enrollment in the exact Exit Pulse course offering, tenant, campus, academic year, and term. Routine faculty pages receive aggregate learning information and never receive responder names, student numbers, or enrollment identifiers.

## Privacy notice and acknowledgment

The verification page displays privacy notice version `2026-07-identity-v1`:

> By entering your student number, you understand that it will be used to verify your enrollment and securely link your response to your student record. Your identity will remain confidential and will not appear in the faculty's Exit Pulse results.

There is deliberately no separate consent checkbox. Entering and submitting the student number is the affirmative acknowledgment. A student who does not want to acknowledge the notice does not continue with Exit Pulse. The server records the notice version and acknowledgment timestamp; neither value is accepted from a public form.

## Trusted participation flow

1. The QR bearer token remains in the browser fragment and is posted through the existing CSRF-protected generic open endpoint. The public response uses `Referrer-Policy: same-origin` so mobile browsers provide a verifiable form-post origin; a null origin is never trusted.
2. The student number is posted only in the request body and is marked sensitive. It is trimmed and matched case-insensitively, consistent with the existing student-number lookup convention.
3. The server resolves an active `Enrollment` for the exact session scope and stores a short-lived verification record in the server-side Django session. No student number or enrollment ID is placed in a URL, hidden field, local storage, or public token.
4. The response endpoint recovers that server state, verifies the browser binding and ten-minute age, refreshes the Exit Pulse lifecycle, and revalidates the enrollment before writing.
5. A database uniqueness constraint permits only one response for the same Exit Pulse session and validated enrollment. Existing browser/IP rate limits remain defense in depth, with separate verification-attempt limits to reduce enumeration.

All failed student-number lookups use the same generic message. The entered number is cleared from the rendered error form and is never written to ordinary application logs.

## Stored accountability record

New responses store an immutable, protected foreign key to the validated enrollment plus the privacy-notice version and acknowledgment timestamp. The student number is not duplicated on the response. The enrollment relation remains available if an enrollment is later deactivated or the assignment is replaced.

Responses created before the identity-validation migration remain valid with a null enrollment, blank notice version, and null acknowledgment time. They are legacy confidential-unidentified responses. No data migration attempts to infer or backfill their identities.

The existing database field named `anonymous_token_hash` is retained only as a backward-compatible technical browser-token hash. It is not the response identity and is cleared by the existing scheduled cleanup after its short technical retention period.

## Faculty privacy boundary

Live counts, terminal results, the dashboard, Class History, and Assignment Comparison do not select or display the enrollment relation. Written feedback remains visible only in the existing owner-authorized terminal result and has no identity attached in the faculty context.

There is no faculty identity-reveal control, responder list, nonresponder list, attendance interpretation, or grading integration.

## Investigation governance

The permission `exit_pulse.response_identity_investigate` exists as a least-privilege foundation and is not granted to any role by migration. No reveal interface is implemented.

Before an investigation interface can be approved, the institution must define authorized roles, tenant/campus scope, qualifying reasons, reviewer training, retention, case preservation, and oversight. A future interface must require a non-empty reason, reveal only one response at a time, create an immutable audit event, and never grant routine faculty access.

## Retention governance

TeacherMate+ has no approved Exit Pulse identity-retention policy. Automatic identity deletion is therefore not implemented. The institution must approve retention and secure deletion rules covering instructional usefulness, academic-year retention, investigation needs, disciplinary or legal holds, backups, and secure disposal.

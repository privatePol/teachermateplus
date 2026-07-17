# HANDOFF.md

Last updated by Codex: 2026-07-17

## Purpose
This file preserves continuity between Codex sessions for TeacherMate+ V1.

## Current Session Summary
### Orientation Feedback pre-commit privacy and integrity hardening
- Date: 2026-07-17
- Completed: resolved the review's two commit blockers and medium-risk production findings. Detailed ratings, distributions, composites, comments, graphs, and CSV are now server-suppressed until five completed responses, including cancelled sessions. Registered-email matching now sends a hashed, short-lived six-digit OTP that must be entered in the same browser/Django session; knowing an eligible email alone cannot consume the response, and the public flow no longer displays fallback usernames. OTP attempts persist correctly outside rollback paths, expire, and clear pending state at the configured limit.
- Additional hardening: all submission POST values are marked sensitive and unexpected failures return a generic 503; completion audits retain no response UUID, participant/user identifier, IP, user agent, or route; Academic Heads `personal_interaction_preference` is not reverse-scored; composite reports display source questions, answered counts, and reverse flags; navigation migration 0013 preserves an existing customized `ADMIN / IMPORTS` group; disabling the feature hides its menu and blocks new/public activity while authorized ended reports remain read-only; response groups use `fieldset`/`legend`, and facilitator completion polling uses a polite live region.
- Configuration/schema: added `orientation_feedback.0002_add_email_otp_verification`, SMTP-backed verification email templates, `ORIENTATION_FEEDBACK_BROWSER_RATE_LIMIT_PER_MINUTE`, `ORIENTATION_FEEDBACK_IP_RATE_LIMIT_PER_MINUTE`, `ORIENTATION_FEEDBACK_EMAIL_OTP_EXPIRY_MINUTES`, `ORIENTATION_FEEDBACK_EMAIL_OTP_MAX_ATTEMPTS`, and `ORIENTATION_FEEDBACK_MINIMUM_REPORT_RESPONSES` with documented defaults. Migration 0002 was applied successfully to the local database.
- Files changed for this hardening: `.env.example`; `apps/orientation_feedback/{models.py,forms.py,services.py,views.py,urls.py,questions.py,tests.py}`; new migration `0002_add_email_otp_verification.py`; `templates/orientation_feedback/{public.html,response_form.html,analytics.html,facilitator.html}`; new verification email templates; `apps/navigation/migrations/0013_seed_orientation_feedback_menu.py`; `apps/core/context_processors.py`; `config/settings/base.py`; `apps/admin_portal/help_guide.py`; `templates/admin_portal/tools/configurable_features.html`; `docs/ORIENTATION_FEEDBACK_SECURITY_AND_PRIVACY.md`; `CHANGE_LOG.md`; `TEACHERMATEPLUS_CONTEXT.md`; and `HANDOFF.md`. Concurrent Admin grading analytics changes and accumulated `logs/system.log` output were not part of this hardening and were preserved.
- Validation performed: final `python manage.py test apps.orientation_feedback -v 1` passed 26/26 in 117.447 seconds; focused impacted Admin groups passed 42/42 in 118.140 seconds; full `python manage.py test apps.admin_portal -v 1` passed 425/425 in 1139.210 seconds; `python manage.py migrate` applied Orientation Feedback migration 0002; final `python manage.py check` passed with zero issues; `python manage.py makemigrations --check --dry-run` reported no changes; `python manage.py migrate --plan` reported no planned operations; and `git diff --check` passed with line-ending warnings only. Expected mocked submission/tenant-export exceptions and SIS denial/rate-limit logs appeared during green negative-path tests.
- Pending/manual risk: in-app browser control could not initialize, so no visual browser result is claimed. Perform staging UAT for same-phone QR -> email -> OTP -> response, code expiry/wrong-code messaging, long labels/Other text, keyboard focus, TalkBack/VoiceOver, facilitator live-count announcements, five-response release, disabled-feature historical access, correct SMTP delivery, shared-cache rate limiting, and real HTTPS reverse-proxy QR URLs. Published snapshot immutability is enforced through supported model/service paths; direct ORM bulk mutation remains prohibited by documented operational policy. Bootstrap remains CDN-hosted without the previously noted local fallback/SRI improvement.
- Exact next steps: review this hardening diff, configure production SMTP/shared cache and the documented Orientation Feedback environment values, run the staging/manual UAT above, then commit only after approval. No commit or push was performed.

### Admin Portal Grading Analytics Course and Search filters
- Date: 2026-07-17
- Completed: enhanced `/admin-portal/grading/analytics/` without changing any grade calculation. The page now derives a distinct, sorted Course dropdown from the already-authorized offering scope after Campus/Academic Year/Term filters; filters by official course code; searches course code, title, section code/name, and scoped accepted faculty name; shows a Clear action and active-filter summary; and renders every matching course offering as its own row. Summary cards and all existing analytics tables continue to use the resulting filtered offering IDs, so multiple sections are never collapsed into one course-code aggregate.
- Scope/security: all options and results still originate from `AdminScopeService.scoped_monitoring_course_offerings()`. Faculty-name search and displayed faculty names now reuse `AdminScopeService.scoped_faculty_assignments()` with active, accepted assignments and active faculty accounts, preventing unauthorized co-assignment names from influencing search or display. Existing tenant, campus, academic-role, department/area, Dean supervision-chain, accepted-assignment, and `grading_analytics.read` gates remain unchanged. Forged course codes return an empty authorized result set.
- Changed files: `apps/admin_portal/views.py`, `templates/admin_portal/grading/analytics.html`, `apps/admin_portal/tests_grading_analytics.py`, `apps/admin_portal/help_guide.py`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, and `HANDOFF.md`. No model or migration change was required. Existing Orientation Feedback files and `logs/system.log` remain unrelated dirty-worktree changes and were preserved.
- Validation performed: final `python manage.py test apps.admin_portal.tests_grading_analytics -v 1` passed 17/17 in 55.811 seconds; combined `python manage.py test apps.admin_portal.tests_grading_analytics apps.admin_portal.tests_grade_distribution_monitor apps.admin_portal.tests_scope apps.admin_portal.tests_academic_performance_insights -v 1` passed 86/86 in 232.944 seconds; and `python manage.py test apps.grading -v 1` passed 78/78 in 217.021 seconds. `python manage.py check`, `python manage.py makemigrations --check --dry-run`, and `git diff --check` passed (`git diff --check` emitted line-ending warnings only). `python manage.py migrate --check` is nonzero solely because the unrelated existing `orientation_feedback.0002_add_email_otp_verification` migration is present but unapplied; this analytics change creates no migration. An earlier 86-test attempt was terminated by its two-minute command limit and was replaced by the successful longer run.
- Pending/manual risk: desktop and phone browser layout smoke remains because the supported in-app browser connection failed to initialize after one clean retry. There is no pagination or detail navigation on the current Grading Analytics page, so no pagination/back-link state was added. The pre-existing per-offering threshold/template resolution path was not redesigned; the new Course-option query and faculty-name search use one scoped query/subquery rather than per-row lookups.
- Exact next step: apply the unrelated pending Orientation Feedback migration only as part of that feature's deployment plan, then visually verify Course/Search/Clear and the responsive offering table under AC, Dean, CAO/Admin, and Superadmin test accounts. No commit or push was performed.

### Admin Portal seeded-fixture and importer-guidance test regressions
- Date: 2026-07-16
- Completed: fixed only the seven identified Admin Portal test regressions. `ActualDataResetTests.setUp()` now uses deterministic `update_or_create()` calls for the migration-seeded `ADMIN / IMPORTS` menu group and `ACTUAL_DATA_RESET` menu item, assigning their expected label, route, sort order, active state, and relationship, then safely reuses/creates the item-permission link. The database uniqueness constraints and production navigation migrations are unchanged.
- Importer confirmation: the Bulk Import page intentionally exposes seven authorized importer cards in stable service order: Sections, Courses, Students, Course Offerings, Faculty Assignments, Faculty Users, and Enrollment. Each has a non-empty, importer-specific duplicate rule in `ImportTemplateService.IMPORT_SAFETY_RULES`. The test now verifies the exact seven type/name pairs, one rendered `Duplicate rule:` label per card, and each card's rendered non-empty rule instead of relying only on the stale page-wide count of six.
- Files changed for this repair: `apps/admin_portal/tests_actual_data_reset.py`, `apps/admin_portal/tests_import_safety_guidance.py`, and required `HANDOFF.md`. No production Python, template, model, migration, feature, or configuration code was changed. Other Orientation Feedback implementation files and existing `logs/system.log` changes were already present and were preserved.
- Validation performed: `python manage.py test apps.admin_portal.tests_actual_data_reset -v 1` passed 6/6 in 5.442 seconds; `python manage.py test apps.admin_portal.tests_import_safety_guidance -v 1` passed 4/4 in 3.581 seconds; full `python manage.py test apps.admin_portal -v 1` passed 413/413 in 1091.619 seconds; `python manage.py test apps.orientation_feedback -v 1` passed 17/17 in 156.744 seconds; and final `python manage.py check` passed with zero issues. Expected negative-path SIS denial/rate-limit and mocked tenant-export exception logs appeared during the green Admin suite.
- Remaining failures: none in the five required verification commands. No commit or push was performed.
- Exact next step: review the two test-file diffs and include them with the pending Orientation Feedback change only when the user authorizes a commit; no production deployment action is required specifically for these test-only corrections.

### Orientation Feedback mobile option-layout correction
- Date: 2026-07-16
- Completed: fixed the mobile defect shown in the supplied Android Chrome screenshots where survey choices collapsed into a narrow vertical rail and overflowed through later question cards. Root cause was `_apply_bootstrap()` applying the fixed-size `form-check-input` class to Django's outer `RadioSelect`/`CheckboxSelectMultiple` container as well as the individual inputs. Multi-option widgets now receive no Bootstrap fixed-size container class; the response template scopes sizing and touch-card presentation to the actual radio/checkbox inputs and their labels inside an `orientation-options` wrapper. Long choices wrap inside their own row, and the Other-area label/input is no longer captured by the choice-label styling.
- Scope preserved: question text/options, registered-email validation, required-field behavior, one-response enforcement, lifecycle, analytics, RBAC, tenant/campus scope, and stored responses are unchanged. No model or migration change was required.
- Changed files: `apps/orientation_feedback/forms.py`, `templates/orientation_feedback/response_form.html`, `apps/orientation_feedback/tests.py`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, and `HANDOFF.md`. Existing `logs/system.log` remains unrelated local test noise.
- Validation performed: direct rendered-widget inspection confirms the outer `id_q_overall_rating` container no longer carries `form-check-input`; the focused complete public flow and duplicate-submission regression passed 1/1, and the full `apps.orientation_feedback` suite passed 17/17. Final Django, migration, and diff checks also passed as recorded at shutdown.
- Pending/manual risk: rescan the existing Faculty survey QR on the same Android phone and confirm all five scale options, multi-select guidance choices, Other field, validation errors, and Submit button remain contained and tappable. The in-app browser controller could not initialize, so the supplied real-phone screenshots are the visual reproduction evidence and no post-fix browser screenshot is claimed yet.
- Exact next step: restart only if the local development server does not auto-reload, refresh the mobile page, and rescan/revalidate if the prior response state has expired. Confirm each option row visually on the same Android phone. No commit or push was performed.

### Orientation Feedback Surveys
- Date: 2026-07-16
- Completed: implemented the approved Orientation Feedback feature as a separate `apps/orientation_feedback` Django app without changing Exit Pulse. Admin users with granular Orientation Feedback permissions can create Faculty or Academic Heads survey sessions, edit draft wording/required flags, review the seeded question set, start from a frozen eligible-role roster, display/download a tokenized QR code, monitor anonymous completion counts, close immediately, cancel with a required reason, view post-close analytics, and export aggregate CSV data. Public respondents validate with the registered email address on the frozen roster, confirm the matched role, and submit once through a mobile-first form. Faculty and Academic Heads eligibility is independent, so a dual-role user may respond once to each applicable session.
- Lifecycle and governance: sessions support Draft, Open, Closed, and Cancelled only; there is no reopening and closing blocks new submissions immediately. Optional scheduled auto-close is enforced on access. Start/close/cancel/export/link-activation/throttle events are audited. The feature is controlled by a dedicated tenant/campus configurable-feature toggle and defaults on; new permissions and the Admin navigation item are seeded, with only `SUPER_ADMIN` receiving default permission grants.
- Privacy and security: public URLs expose only a random UUID/token flow and no user, participation, response, question-database, or session-database IDs. Validation is generic and rate-limited, uses the registered email only against the frozen eligible roster, stores short-lived server/browser-bound validation state, and never places email in public URLs or routine analytics. Submission rechecks feature state, lifecycle, roster membership, signed state, and one-response database constraints inside a transaction. Routine facilitator/analytics/export output contains aggregate counts, score distributions, composite indices, checkbox totals, and anonymous comments only; it does not expose respondent identity or non-responder lists. CSV cells are protected against spreadsheet-formula injection. The protected internal participation-response relation means confidentiality is policy/access-control based rather than cryptographic anonymity; this is documented in `docs/ORIENTATION_FEEDBACK_SECURITY_AND_PRIVACY.md`.
- Questions and analytics: both approved Faculty and Academic Heads instruments are seeded per session with stable question/choice codes and five-point scoring. Published structure/choices/scoring become immutable after start. Analytics include weighted means, distributions, interpretation labels, checkbox/other-text summaries, open comments, and configured composites; only the explicitly negative technology-preference item is reverse scored. Results are available only after Closed/Cancelled and do not include subgroup segmentation in this phase.
- Intentional first-phase limits: no PDF export, reopening, participation/non-responder export, segmented analytics, consolidated cross-session comparison, or draft UI for adding/removing/reordering choices and changing score mappings. Draft UI editing is limited to question wording and required status; the complete approved instruments are seeded automatically.
- Changed files: new `apps/orientation_feedback/` app and migration `0001_initial`; new `templates/orientation_feedback/`; `config/settings/base.py`; `config/urls.py`; configurable-feature integration in `apps/core/services/features.py`, `apps/admin_portal/{forms.py,views.py,tests_assignment_acceptance.py}`, and `templates/admin_portal/tools/configurable_features.html`; migrations `apps/rbac/migrations/0029_seed_orientation_feedback_permissions.py` and `apps/navigation/migrations/0013_seed_orientation_feedback_menu.py`; `apps/admin_portal/help_guide.py`; `docs/ORIENTATION_FEEDBACK_SECURITY_AND_PRIVACY.md`; `docs/ROLE_SETUP.md`; `CHANGE_LOG.md`; `TEACHERMATEPLUS_CONTEXT.md`; and `HANDOFF.md`. Existing `logs/system.log` was already dirty and gained expected test logging; it is not a feature change.
- Validation performed: `python manage.py migrate` applied `rbac.0029`, `navigation.0013`, and `orientation_feedback.0001` to the local SQLite database. Full `apps.orientation_feedback` passed 17/17; the Admin feature-card plus complete Admin Help Guide run passed 17/17; the focused feature-toggle persistence test passed 1/1; and full `apps.exit_pulse` regression passed 90/90. Final code checks before handoff passed: `python manage.py check`, `python manage.py migrate --check`, `python manage.py makemigrations --check --dry-run`, Python compile of the new app, and `git diff --check` (line-ending warnings only).
- Pending/manual risk: authenticated visual browser smoke remains. The in-app browser controller could not initialize in this environment, so no desktop/phone rendering, real QR scan, keyboard-flow, or live two-browser duplicate-submission result is claimed. Production should use a shared cache for cross-worker rate limiting. Before release, institution reviewers should confirm the exact instrument wording, privacy notice, role eligibility policy, interpretation bands, and whether `SUPER_ADMIN`-only default grants are appropriate.
- Exact next steps: in staging, apply the three migrations; configure the Orientation Feedback toggle and assign least-privilege permissions; create one Faculty and one Academic Heads session; test eligible, unknown, wrong-role, dual-role, inactive-role, duplicate, close-during-form, cancel, scheduled-close, CSV, cross-campus denial, and phone QR flows; review analytics against known submissions; then approve or revise the policy wording. No commit or push was performed.

### Faculty Assignment bulk import accepts inactive faculty accounts
- Date: 2026-07-16
- Completed: traced `/admin-portal/imports/faculty-assignments/upload` through `import_upload_view`, `ImportUploadForm`, `BulkImportService.validate_and_stage_upload()`, `_validate_faculty_assignment_row()`, batch preview, `import_batch_confirm_view`, and `BulkImportService.confirm_batch()`. The account-status restriction was the `User.is_active=True` filter in `_resolve_faculty_user()` during row validation. Added one explicit `allow_inactive_account=True` opt-in used only by the Faculty Assignment row validator; the helper default remains active-only.
- Exact behavior: the Faculty Assignment importer now accepts active or inactive existing user accounts identified by the CSV username/email, provided the user still has an active `FACULTY` role matching the selected tenant/campus. Confirmation creates the `FacultyAssignment` only and leaves `User.is_active` unchanged. Missing faculty, inactive/missing Faculty roles, tenant/campus mismatch, invalid AY/term/offering, and duplicates remain row errors. Per-row transactions and existing audit logging are unchanged.
- Scope preserved: manual Faculty Assignment create/bulk-assign and replacement querysets still require active users; account activation/deactivation, Faculty login, assignment acceptance, other CSV importers, permissions, and all academic/scope validation paths were not changed. No model or migration change was required.
- Changed files: `apps/imports/services.py`; new `apps/imports/tests_faculty_assignment_import.py`; `apps/admin_portal/help_guide.py`; `CHANGE_LOG.md`; `TEACHERMATEPLUS_CONTEXT.md`; and `HANDOFF.md`. Existing unrelated `logs/system.log` test noise was preserved.
- Validation performed: final `python manage.py test apps.imports.tests_faculty_assignment_import -v 1` passed 12/12. The combined focused import/safety/Admin-guide command passed 32/32, `python manage.py test apps.imports.tests_faculty_user_import -v 1` passed 37/37, and `python manage.py test apps.imports -v 1` passed 56/56. Coverage includes active and inactive assignment imports, unchanged inactive status, blocked authentication/Faculty Dashboard access, missing and wrong-campus faculty, inactive Faculty role, duplicate assignment, invalid academic year/term/offering, audit creation, unchanged manual create/replacement rejection, upload safety guidance, Admin guide rendering, and all other import-app workflows. Final `python manage.py check`, `python manage.py migrate --check`, and `python manage.py makemigrations --check --dry-run` passed; `git diff --check` passed with line-ending warnings only.
- Pending/manual risk: browser preview/confirm smoke with one inactive Faculty account remains useful. The account must retain an active scoped `FACULTY` role to be a valid assignment target; deactivating the role remains an intentional rejection. No commit or push was performed.
- Exact next step: run the remaining focused regression/check commands, then in staging upload one reviewed Faculty Assignment CSV containing an inactive faculty account, confirm the batch, verify the assignment row and audit entry, and verify the account remains inactive and cannot sign in.

### Requested Faculty Portal phone-width simplification
- Date: 2026-07-16
- Completed: implemented only the requested responsive presentation changes below 768px. Dashboard now hides Updates Since Your Last Visit, Grade Encoding Status, Pending Grade Issues, the Performance Trends hero shortcut, and the data-driven `FACULTY_ANALYTICS` navigation item. The mobile sidebar also hides Performance Trends and Activity History. My Classes hides its Grade Submission Deadline card. The class-period page hides the normal template summary, deadline card, and What to do / Why set this guidance. Existing missing-template warnings remain visible. Feedback, Quick Guide, and Data Privacy Notice retain their existing desktop buttons/order and collapse behind one accessible Help & Privacy toggle on phones. Grade Summary now switches to a compact phone-only table containing wrapped Student Name cells, the current official periodic grade, and its Explain action; student number, status, prior grades, Class Standing details, exams, and the course final-grade column are not rendered in that phone table.
- Scope preserved: desktop rendering and the separate print sheet are unchanged; the mobile grade cell still obeys `show_official_period_grade`; routes, RBAC, tenant/campus scope, calculations, submissions, and server-side data are unchanged. Hidden links remain authorized routes and are only removed from phone-width presentation.
- Changed files: `templates/faculty_portal/base.html`, `templates/faculty_portal/dashboard.html`, `templates/faculty_portal/my_courses.html`, `templates/faculty_portal/offering_periods.html`, `templates/faculty_portal/period_summary.html`, new `apps/faculty_portal/tests_mobile_visibility.py`, `apps/faculty_portal/tests_help_guide.py`, `apps/faculty_portal/help_guide.py`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, and `HANDOFF.md`. Other dirty worktree files, including existing changes in My Classes/period pages and Faculty acceptance tests, were preserved.
- Validation performed: the latest Grade Summary continuation passed 12 focused tests: four mobile-template contract tests, five Faculty Help Guide tests, and three existing Grade Summary official-grade visibility/print regressions. Earlier in this same responsive session, the combined utility and My Classes/period regression coverage also passed as recorded previously. Final `python manage.py check`, `python manage.py migrate --check`, and `python manage.py makemigrations --check --dry-run` passed. `git diff --check` passed with line-ending warnings only.
- Pending/manual risk: authenticated phone-width and desktop browser smoke remains. The in-app browser connection could not initialize earlier in this environment, so no visual browser result is claimed. Verify the Help & Privacy expand/collapse behavior, requested hidden cards/links, preserved missing-template warnings, the compact Grade Summary at below 768px with long names and Explain modal use, and unchanged desktop/print layouts in staging or a local authenticated browser.
- Exact next steps: sign in as Faculty at desktop width and below 768px; compare Dashboard, My Classes, one class-period page, and Grade Summary; confirm the phone summary contains only Student Name, the official periodic grade, and Explain; test one long wrapped name and one unavailable official grade; expand Help & Privacy and open all three utilities; then confirm desktop and print still show the full grade tabulation. No commit or push was performed.

### Canonical detailed Complete Tabulation alignment and signature correction
- Date: 2026-07-15
- Completed: after the user confirmed that `/faculty/my-courses/<offering_id>/class-tabulation/` is the required format, the detailed Faculty grid is now the single canonical Complete Tabulation layout for the Faculty HTML preview, Faculty official PDF, and Admin Course Offerings PDF. New `DetailedTabulationSheetGridService` in `apps/grading/tabulation.py` builds the shared period/activity hierarchy, highest-possible-score row, encoded values and averages, class-standing averages, period grades, final grades, and `MISSING`/`EXEMPT` states; inactive activities remain excluded. The former simplified Admin/Faculty PDF format is no longer used for this report.
- PDF and print behavior: `InstitutionalReportPdfConfig` remains the one reusable source for Legal landscape geometry and now also supplies the HTML print page values. The canonical PDF splits wide activity sets into at most 12 dynamic columns per horizontal part instead of shrinking below a practical size. Institutional/report headers, three student-identification columns plus status, table headers, and final grade repeat across continuation pages. The actual local offering 419 produced eight 1008 x 612 point pages for 26 students. Representative first, middle, and final pages were rendered with PyMuPDF and inspected because Poppler is unavailable; the detailed values were readable, the lighter watermark did not obscure content, no table/header clipping was found, and the final page contained the expected `NOTHING FOLLOWS` marker and signature panel.
- Signature behavior: the active accepted Faculty of Record's stored signature is embedded in both the Faculty HTML preview and the shared Faculty/Admin PDF, followed by the Faculty name and `Prepared and Submitted By`. Admin generation never substitutes the Admin actor's signature. A neutral no-signature message remains when no usable Faculty signature exists, and HTML preview/PDF use is audit-logged. Existing Faculty/Admin authorization, tenant/campus scope, historical ownership rules, and permanent/administrative/wrong-assignment replacement exclusions remain in force.
- Faculty action correction: the course report action now explicitly opens `Print Official PDF`. The browser `Print Current Preview` action was removed so the official output always uses the paginated Legal-landscape PDF instead of allowing the wide HTML grid to be squeezed by a browser print dialog. The HTML page remains available as the on-screen detailed preview.
- Changed files for this correction: new `apps/grading/tabulation.py`; `apps/grading/reporting.py`; `apps/faculty_portal/views.py`; `templates/faculty_portal/class_tabulation_sheet.html`; `apps/faculty_portal/tests_assignment_acceptance.py`; `apps/admin_portal/tests_scope.py`; Faculty/Admin guide templates; `CHANGE_LOG.md`; `TEACHERMATEPLUS_CONTEXT.md`; and `HANDOFF.md`. These changes build on the signature/report files listed in the immediately following session entry. `logs/system.log` was already dirty and gained expected local test/report logging; it is not a product change.
- Validation performed: the direct detailed Faculty HTML/PDF, wide-activity chunking, Faculty signature, and Admin scope/report tests passed 4/4. The final combined signature, Faculty assignment/report, Admin scope, and permanent-replacement regression run passed 192/192 in 277.677 seconds. Final `python manage.py check`, `python manage.py migrate --check`, and `python manage.py makemigrations --check --dry-run` passed with no issues, pending migration, or model changes. `git diff --check` passed with line-ending warnings only. The earlier 971-test project run was not repeated; its six unrelated `ActualDataResetTests` menu-group fixture errors and one stale importer-card-count assertion remain as documented below.
- Pending/manual risk: authenticate in a real browser as the assigned Faculty and a correctly scoped Admin, generate the same offering from both portals, and compare period labels, values, final grades, signature, and page count. Also smoke a signature-less Faculty, a denied cross-campus Admin, a historical eligible Faculty assignment, a roster large enough to require vertical continuation, and a still-wider template. Print on physical 8.5 x 14 inch Legal paper in landscape and obtain institutional approval for font size, watermark, signature placement, certification wording, and horizontal page splitting. No browser or physical-printer validation is claimed, and no commit or push was performed.
- Exact next steps: deploy the uncommitted migration `accounts.0010` to staging, run the authenticated Faculty/Admin comparison matrix above, verify the signature-use audit entries, print and approve the offering 419 eight-page sample on Legal landscape, then prepare a reviewable commit only after user acceptance.

### Secure signature pad, Complete Tabulation Sheet, and focused Faculty mobile simplification
- Date: 2026-07-15
- Completed: added a Faculty-owned signature pad with mouse, touch, and stylus support; undo, clear, preview, cancel, password confirmation, and ownership confirmation. Drawn signatures are strictly decoded as real PNG data, size/dimension/pixel limited, alpha-trimmed, normalized to transparent RGBA, stored under a server-generated filename, encrypted through the existing signature credential service, and rejected when empty or malformed. Replacement is atomic so a failed new signature leaves the prior credential intact. Existing image upload and removal remain available. Create, replace, remove, preview, and report-use audit behavior remains server-side and bound to the authenticated user; forged faculty/tenant identifiers are ignored and cross-scope preview is denied.
- Complete Tabulation Sheet: introduced shared report authorization, data, signature-resolution, and PDF services. Faculty can generate current reports and PDF-only historical reports for their own accepted assignments without restoring grading access; assignments deactivated by permanent, administrative, or wrong-assignment replacement are explicitly excluded. Scoped Admin users can generate the same report from Course Offerings using the existing `offerings.read`, tenant, campus, and role scope. Cross-scope and unassigned requests return 404. The report reads stored grading data without mutating or recomputing records, uses the existing template/calculation services, includes a period summary plus every active activity grouped by period, distinguishes `MISSING`, encoded `0`, and `EXEMPT`, excludes inactive activities, shows submission/status/remarks, and resolves only the active accepted faculty of record's stored signature. Missing signatures render neutral text.
- PDF/layout: `InstitutionalReportPdfConfig` in `apps/grading/reporting.py` is the single reusable source for Legal landscape page geometry (`14 x 8.5 inches`). Faculty and Admin copies use it for summary and period-detail pages. Wide period/activity sets are split into readable column chunks instead of being shrunk indefinitely; student-identification columns, table headers, institutional/report headers, and page metadata repeat across pages. A generated five-page report with 10 students and 21 activities was rendered with PyMuPDF and visually inspected page by page because Poppler is unavailable; all pages measured 1008 x 612 points, remained landscape/readable, and showed repeated headers without clipping. A literal `<br/>` rendering defect found during that visual pass was corrected and the affected page was regenerated and reinspected.
- Faculty mobile scope: simplified only the affected course/report/signature surfaces. Primary grading/report actions remain visible and use touch-sized wrapping controls; schedule/room metadata moves into native mobile details while retaining the desktop presentation; report controls stack at phone width. No grading, submission, correction, attendance, notification, or calculation workflow was removed or redirected.
- User-facing/docs: updated the Faculty and Admin guides, `CHANGE_LOG.md`, and `TEACHERMATEPLUS_CONTEXT.md` for the draw-signature workflow, current/historical Complete Tabulation access, Admin Course Offerings action, Legal landscape output, and mobile behavior. Added migration `apps/accounts/migrations/0010_alter_usersignatureusagelog_document_type.py` for the dedicated Complete Tabulation signature-usage document type; it was applied successfully to the local database.
- Changed product files: `apps/accounts/{forms.py,models.py,services.py,views.py}`; `apps/accounts/migrations/0010_alter_usersignatureusagelog_document_type.py`; `apps/admin_portal/{urls.py,views.py}`; `apps/faculty_portal/views.py`; `apps/grading/reporting.py`; `templates/admin_portal/academics/offering_table.html`; `templates/admin_portal/guide.html`; `templates/faculty_portal/{class_tabulation_sheet.html,guide.html,guide_manual.html,my_courses.html,offering_periods.html,signature_profile.html}`; `CHANGE_LOG.md`; and `TEACHERMATEPLUS_CONTEXT.md`. Tests changed in `apps/accounts/tests_signatures.py`, `apps/admin_portal/tests_scope.py`, and `apps/faculty_portal/tests_assignment_acceptance.py`. `logs/system.log` was dirty before this session and gained expected local test logging; it is not a product change.
- Validation performed: `python manage.py migrate` applied `accounts.0010`; final `python manage.py check` passed; `python manage.py migrate --check` found no pending migration; `python manage.py makemigrations --check --dry-run` reported no changes; and the final combined signature, Faculty assignment/report, Admin scope, and permanent-replacement regression run passed 192/192 in 286.145 seconds. Coverage includes mouse/touch UI semantics, normalization/security/atomic replacement, ownership and tenant isolation, current and historical authorization, permanent-replacement exclusion, Admin campus scope, active faculty-of-record signature selection, active-activity inclusion, missing/zero/exempt distinctions, no-submission report access, column chunking, repeated identities/headers, and exact Legal landscape PDF dimensions. `git diff --check` passed with line-ending warnings only. The earlier full project run executed 971 tests in 1826.948 seconds and was not green: 2 failures and 6 errors. One failure exposed this pass's permanent-replacement access regression; it was fixed and its existing regression test now passes in the 192-test run, but the entire 971-test suite was not repeated afterward. The six unrelated errors remain the existing `ActualDataResetTests` fixture creating an `ADMIN/IMPORTS` menu group already seeded by migrations (`UNIQUE constraint failed: menu_groups.portal, menu_groups.code`). The other unrelated failure remains the stale `ImportSafetyGuidanceTests` expectation of six `Duplicate rule:` cards while the unchanged current importer page renders seven.
- Pending/manual risk: authenticated browser smoke remains required for real pointer drawing on mouse/touch/stylus, preview/cancel/save/failed-replacement behavior, current and historical Faculty links, Admin scoped action visibility, cross-campus denial, phone-width control wrapping, and actual browser PDF download/print behavior. Institutional reviewers should approve certification wording, signature placement, column chunk sizes, and readability on a physical Legal landscape print. Poppler was unavailable, so PDF visual QA used PyMuPDF. The old image-upload option intentionally remains enabled. No commit or push was performed.
- Exact next steps: decide separately whether to repair the two unrelated stale baseline-test defects, then run the browser matrix as faculty of record, historical faculty, permanently replaced faculty, unrelated faculty, scoped Admin, cross-campus Admin, and signature-less faculty at desktop and phone widths. Draw and replace a safe test signature with mouse and touch, verify audit entries, generate current/historical Faculty and Admin PDFs, print a multi-page 21-plus-activity sample on Legal landscape, confirm repeated identity/report headers and readable text, and only then prepare deployment review. Run migration `accounts.0010` in staging before smoke. Do not commit or push until the user accepts the result.

### Shared Faculty/Admin utility control correction
- Date: 2026-07-15
- Completed: the initial shared-footer correction was adjusted at the user's request. Faculty `Feedback`, `Faculty Quick Guide`, and `Data Privacy Notice` now render once in one fixed group at the original bottom-left Data Privacy location, in that exact top-to-bottom order. Feedback keeps its green pill, Faculty Quick Guide now uses a labelled `Quick Guide` pill with the existing star icon, and Data Privacy keeps its labelled chip. `Admin Practical Guide` remains in the shared normal-flow Admin footer.
- Preserved behavior/security: Faculty Feedback keeps the same `faculty-feedback-open` trigger, CSRF form, AJAX submission URL, modal, rating controls, focus trap, Escape handling, and scoped backend behavior. Faculty/Admin guide URLs, quick-tour targets, and the external NCBA privacy destination are unchanged. Exit Pulse identity validation, fragment-token QR flow, student-number privacy acknowledgment, one-response enforcement, rate limits, ownership/scope gates, and mobile CSRF fix were not changed.
- Layout/accessibility: the Faculty controls use their pre-change formats inside a labelled group anchored at `left: 14px; bottom: 14px`, with the prior mobile-safe `left: 10px; bottom: 66px` offset. Native button/link semantics and accessible names are preserved, and the group remains hidden when printing. The user explicitly requested the original fixed Data Privacy location, so the earlier normal-flow Faculty footer behavior is superseded; Admin retains its normal-flow footer.
- Tests changed: exact one-render assertions now verify the Faculty group, all three button classes, the visible `Quick Guide` label, existing routes, and the required Feedback -> Faculty Quick Guide -> Data Privacy DOM order. Coverage renders Exit Pulse dashboard, results, Class History, and Assignment Comparison. Admin dashboard/long Course list footer coverage remains. The lower-left placement focused tests passed 3/3, the combined Exit Pulse, Faculty Feedback, Faculty Help, and Admin Help regression run passed 121/121, and the subsequent visible-label focused rerun passed 3/3. Expected CSRF-denial and mocked cache-unavailable logs appeared in security tests.
- Validation performed: the final `python manage.py check` passed; `python manage.py migrate --plan` reported no planned operations; `python manage.py makemigrations --check --dry-run` reported no changes; and `git diff --check` passed with line-ending warnings only.
- Changed files for this correction: `templates/faculty_portal/base.html`; `templates/admin_portal/base.html`; print selectors in `templates/faculty_portal/period_summary.html`, `templates/faculty_portal/class_tabulation_sheet.html`, and `templates/admin_portal/academics/faculty_activity_monitor.html`; `apps/faculty_portal/help_guide.py`; `apps/faculty_portal/tests_feedback.py`; `apps/faculty_portal/tests_help_guide.py`; `apps/admin_portal/tests_help_guide.py`; `apps/exit_pulse/tests.py`; `CHANGE_LOG.md`; `TEACHERMATEPLUS_CONTEXT.md`; and `HANDOFF.md`. Other modified/untracked Exit Pulse identity files and `logs/system.log` predated this correction and were preserved.
- Pending/manual risk: the in-app browser-control runtime failed during bootstrap with `Cannot redefine property: process`, including after a clean reset, so desktop/mobile screenshots, real keyboard-only focus traversal, and actual-phone checks are not claimed. Automated Django rendering confirms one correctly ordered Faculty group on all four requested Exit Pulse pages and one Admin footer on dashboard/course list, but visual overlap and live modal behavior still require a working browser. Fixed Faculty controls can cover content in their lower-left footprint; this is the explicit placement requested in the follow-up.
- Exact next step: with a working local/staging browser, inspect the grouped Faculty controls on Exit Pulse dashboard, results, Class History, and Assignment Comparison at desktop and phone widths; confirm the exact order and original button appearances; check the lower-left footprint against the last table row/pagination; use Tab/Shift+Tab and open/close Feedback with keyboard and Escape; submit one safe feedback item to confirm CSRF; then rescan the QR on an actual phone. Do not commit or push until that manual smoke is accepted.

### Exit Pulse identity validation, privacy acknowledgment, and accountability foundation
- Date: 2026-07-15
- Mobile QR follow-up: fixed the reproduced Chrome `CSRF verification failed` / `Origin: null` failure. The public Exit Pulse response now sends `Referrer-Policy: same-origin` instead of `no-referrer`, allowing a normal same-origin form source while keeping the bearer token in the URL fragment. CSRF remains mandatory and a null origin is explicitly still rejected. Updated `apps/exit_pulse/views.py`, its CSRF regression in `apps/exit_pulse/tests.py`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, and `docs/EXIT_PULSE_SECURITY_AND_PRIVACY.md`.
- Mobile QR validation: the focused fragment/CSRF tests passed 2/2; full `python manage.py test apps.exit_pulse.tests -v 1` passed 89/89; `python manage.py check` passed; `python manage.py makemigrations --check --dry-run` reported no changes; and `git diff --check` passed with line-ending warnings only. A live LAN GET to `http://192.168.12.107:8000/pulse/` returned 200 with `Referrer-Policy: same-origin` and a CSRF cookie. Exact phone rescan remains the next manual check.
- Completed: reclassified Exit Pulse as confidential, identity-validated classroom feedback. The QR fragment-token flow now requires a student number that resolves to an active enrollment in the exact session offering/tenant/campus/academic year/term before the response form opens. Per the approved adjustment, there is no consent checkbox: entering and submitting the student number acknowledges the visible privacy notice; students who do not agree do not continue.
- Security/storage: successful verification creates ten-minute server-side Django session state bound to the session, enrollment, browser hash, notice version, and acknowledgment time. Submission rechecks lifecycle, feature state, assignment/enrollment scope, tenant/campus, verification age, and duplicates. New responses store an immutable protected Enrollment relation plus notice version/time. One response per session/enrollment is database-enforced across browsers; generic rate-limited verification errors prevent enrollment enumeration. Student numbers are not placed in URLs, hidden fields, local storage, rendered errors, or ordinary logs.
- Privacy/governance: live/results/dashboard/history/comparison remain aggregate and receive no responder identity. Written feedback remains confidential and identity-free in routine faculty context. Legacy responses remain null/blank confidential-unidentified records with no inferred backfill. `exit_pulse.response_identity_investigate` is seeded with no role/user grants; no reveal interface exists. A scoped, reason-required, audited one-response investigation workflow and an institutional identity-retention/deletion policy remain deferred for approval.
- User-facing/docs: added the mobile enrollment-verification step, updated Exit Pulse terminology and over-100-rate explanations, linked `Exit Pulse Guide` from the faculty dashboard, expanded the existing Faculty Help Guide section, added `docs/EXIT_PULSE_SECURITY_AND_PRIVACY.md`, and updated deployment settings, context, and changelog. Verification rate limits are configurable; production needs an approved shared cache for cross-worker throttling.
- Changed files for this pass: `.env.example`; `apps/exit_pulse/{forms.py,models.py,services.py,tests.py,urls.py,views.py}`; `apps/exit_pulse/management/commands/anonymize_exit_pulse_identifiers.py`; new `apps/exit_pulse/migrations/0003_*`; new `apps/rbac/migrations/0028_*`; `apps/faculty_portal/{help_guide.py,tests_help_guide.py}`; `config/settings/base.py`; Exit Pulse templates; `docs/DEPLOYMENT_UBUNTU.md`; new `docs/EXIT_PULSE_SECURITY_AND_PRIVACY.md`; `CHANGE_LOG.md`; `TEACHERMATEPLUS_CONTEXT.md`; and `HANDOFF.md`. Existing Phase 2 edits in these files remain part of the uncommitted worktree; `logs/system.log` is local test noise.
- Validation: focused identity/rate-limit/guide tests passed 17/17; full `apps.exit_pulse.tests` passed 89/89; Faculty Help Guide passed 5/5; `python manage.py check` passed; `python manage.py makemigrations --check --dry-run` reported no changes; and `git diff --check` passed with line-ending warnings only. An isolated SQLite database was migrated to `exit_pulse.0002`/`rbac.0027`, then successfully through the new migrations; schema introspection confirmed nullable legacy identity fields, the unique/check constraints, and zero permission grants. `python manage.py migrate --plan` showed only the two expected migrations and `python manage.py migrate --noinput` applied both to the local development database.
- Pending/manual: browser staging must verify phone/desktop layout, keyboard/error focus, real QR fragment behavior, and shoulder-surfing cleanup. MariaDB migration execution remains a deployment-environment check. Institution must approve investigation roles/workflow and retention/hold/deletion rules before identity reveal or automated identity deletion is implemented.
- Exact next step: run the documented 20-item staging matrix with enrolled, unknown, wrong-section, duplicate-across-browser, two-student, direct-bypass, privacy, faculty-page-source, mobile/accessibility, analytics, and guide-link scenarios. Do not add a checkbox, identity reveal UI, attendance/grading use, or identity backfill.

### Earlier Exit Pulse Phase 2 notes
- Date: 2026-07-15
- Earlier pass: completed Exit Pulse Phase 2 Checkpoint 3 Assignment Comparison only. Added the faculty-only GET route `/faculty/exit-pulse/assignment-comparison/`, server-side academic-year/term/question-type filters, five aggregate summary cards, a responsive 20-row database-paginated comparison table, latest-result and owned-history navigation, dashboard/history links, and Faculty Help Guide coverage. No dashboard/Class History redesign, chart, export, ranking/evaluation, administrator analytics, public participation workflow, model/migration, attendance, grading, commit, or push change was made in that checkpoint.
- Inclusion/authorization policy: candidates begin with the exact authenticated faculty owner, request tenant, and request campus. Current eligible assignments remain visible with a neutral no-data state even without terminal sessions; noncurrent/historical assignments appear only when the owner has matching `CLOSED`/`EXPIRED` data. Deactivation and faculty replacement do not transfer prior history: the original owner retains the historical comparison, replacement/other faculty and even a broad superuser account do not inherit it. Missing scope, permission, or feature state fails closed.
- Analytics policy: cards and rows use the same filtered terminal queryset. Weighted response rate is `responses from non-null-snapshot terminal sessions / summed non-null snapshots x 100`; stored zero is historical, null is unavailable/not historically comparable, and legacy sessions are reported separately and excluded from both response-rate numerator and denominator. Understanding is `(CONFIDENT + MOSTLY_UNDERSTOOD) / all valid terminal responses x 100`; support needed is `(NEEDS_CLARIFICATION + NEEDS_PRACTICE) / all valid terminal responses x 100`. All rates use one-decimal `ROUND_HALF_UP`, are zero-safe, and response rates remain uncapped. Distinct topics are trimmed and blank topics excluded; latest terminal uses `-started_at, -id`.
- Privacy/performance/accessibility: comparison aggregation is performed in reusable service/database annotations with one bounded assignment-row query independent of displayed row count; no per-row response loads or current-roster lookups are used. Written feedback, student identities, technical browser-token hashes, and bearer tokens are never selected for display. The page uses escaped template output, one H1, associated filter errors, visible state text, a focusable responsive table, labelled progress bars, descriptive links, accessible pagination, and neutral instructional-use wording.
- Changed in Checkpoint 3: `apps/exit_pulse/forms.py`; `apps/exit_pulse/services.py`; `apps/exit_pulse/tests.py`; `apps/exit_pulse/urls.py`; `apps/exit_pulse/views.py`; new `templates/exit_pulse/assignment_comparison.html`; `templates/exit_pulse/landing.html`; `templates/exit_pulse/history.html`; `apps/faculty_portal/help_guide.py`; `apps/faculty_portal/tests_help_guide.py`; `CHANGE_LOG.md`; `TEACHERMATEPLUS_CONTEXT.md`; and `HANDOFF.md`. Earlier dirty Phase 2 files remain part of the same uncommitted worktree; `logs/system.log` is local test noise, not a product change.
- Validation performed: new `ExitPulseAssignmentComparisonTests` passed 13/13 as part of the final `python manage.py test apps.exit_pulse.tests -v 1`, which passed 77/77; `python manage.py test apps.faculty_portal.tests_help_guide -v 1` passed 5/5; `python manage.py check` passed; `python manage.py makemigrations --check --dry-run` reported no changes; and `git diff --check` passed with line-ending warnings only. Expected existing CSRF-denial and mocked cache-failure logs appeared. No migration was created or required.
- Pending/manual validation: the in-app browser connection failed twice during bootstrap with `Cannot redefine property: process`, including after a clean reset. Therefore desktop/phone overflow, real keyboard focus, assistive-technology announcements, and visual rendering remain explicitly unverified and require staging or a working browser session. MariaDB execution also remains deployment-environment validation; the implementation uses standard Django aggregation, correlated subqueries, `Trim`, `NullIf`, and `Coalesce` intended to remain backend portable.
- Exact next step: run the documented local/staging browser matrix at desktop and phone widths as original faculty, replacement faculty, permission-denied faculty, legacy-null, stored-zero, over-100-rate, filtered, and 21-plus-assignment scenarios. If it passes, accept Checkpoint 3; any charts, exports, administrator analytics, or further Phase 2 expansion must be planned separately.

- Follow-up local smoke on 2026-07-15: `python manage.py test apps.exit_pulse.tests.ExitPulseDashboardAndHistoryTests -v 2` passed 16/16. This rendered and exercised dashboard ownership, original faculty history after deactivation/replacement, replacement/forged/cross-scope denial, permission and feature denial, legacy-null and stored-zero denominators, terminal weighted/over-100 wording, date/question/topic/status filters, invalid ranges, stable 20-row pagination with filter persistence, written-feedback absence, scoped expiry normalization, and bounded row queries. No product code or test data was changed; Django used and destroyed an isolated in-memory test database.
- Visual smoke limitation: the in-app browser connector failed during its own bootstrap (`Cannot redefine property: process`) before attaching to a tab, so desktop/phone overflow, real keyboard focus, and visual responsive behavior were not browser-verified. Template/static review still shows responsive table wrappers and mobile grid classes, but staging or a restored local browser connection is required before marking visual smoke complete.

- Date: 2026-07-15
- Current pass: completed the requested focused post-implementation security, correctness, privacy, performance, accessibility, lifecycle, and documentation review of Exit Pulse Phase 2. No Assignment Comparison, chart, export, administrator analytics, unrelated feature, model/migration change, commit, or push was performed.
- Confirmed fixes: faculty Exit Pulse now fails closed if the request has no active tenant/campus scope; dashboard and Class History accept GET only; assignment analytics count database-trimmed distinct non-empty topics; the dashboard accurately labels its weighted response-rate card; and legacy single-session results clearly label the current eligible enrollment and derived response rate as estimates rather than historical facts.
- Authorization/privacy result: dashboard, history, results, and lifecycle routes remain feature-, permission-, owner-, tenant-, and campus-scoped. Historical access follows stored session ownership, so the original faculty retains read-only history while replacement faculty, other faculty, forged UUIDs, and cross-scope requests do not learn whether a record exists. Dashboard/history aggregate only `CLOSED`/`EXPIRED`; list pages neither load nor display written response content.
- Analytics result: stored zero remains historical; null remains legacy/unavailable for weighted response rates; legacy responses still participate in understanding/support rates; valid response codes are enforced by a database check constraint; weighted response numerator and denominator use the same stored-snapshot session set; divisions are zero-safe and one-decimal `ROUND_HALF_UP`; rates above 100% remain uncapped. Independent one-decimal rounding can theoretically make complementary displayed rates total 99.9% or 100.1%, while underlying valid response counts partition exactly.
- Filter/pagination/lifecycle result: server-side history filters preserve the owned assignment scope and inclusive local end date; ordering is stable by `-started_at, -id`; pagination is database-backed at 20 rows and safely handles invalid, zero, negative, and oversized page values. The single bulk expiry update is owner/tenant/campus constrained, and a regression proves another faculty's elapsed live session is untouched.
- Query result: feature-data work remains bounded at approximately eight data operations on dashboard and history, plus feature/permission/context processing. Assignment analytics uses two aggregate queries; annotated session rows and assignment grouping remain one query each independent of row count. No response objects or current-enrollment lookups are loaded per dashboard/history row.
- Changed files: `apps/exit_pulse/services.py`; `apps/exit_pulse/views.py`; `apps/exit_pulse/tests.py`; `templates/exit_pulse/landing.html`; `templates/exit_pulse/results.html`; `apps/faculty_portal/help_guide.py`; `CHANGE_LOG.md`; `TEACHERMATEPLUS_CONTEXT.md`; and `HANDOFF.md`. `logs/system.log` was already dirty at session start and gained expected local test logging; it is not a product change.
- Validation performed: baseline `python manage.py test apps.exit_pulse.tests -v 1` passed 57/57. Final Exit Pulse suite passed 64/64; `python manage.py test apps.faculty_portal.tests_help_guide -v 1` passed 5/5; `python manage.py check` passed; `python manage.py makemigrations --check --dry-run` reported no changes; and `git diff --check` passed with line-ending warnings only. Expected CSRF-denial and mocked cache-failure logs appeared in existing security tests.
- Pending/manual validation: desktop/mobile browser layout, real keyboard/focus behavior, assistive-technology announcements, combined filter interaction, 21-plus session pagination, original-versus-replacement faculty behavior, legacy/zero/over-100 displays, and permission/feature denial still require staging browser smoke. LocMemCache rate limiting remains per worker, session-history bookmarks depend on the referenced session continuing to exist, and no institutional purge policy is implemented.
- Exact next step: perform the documented staging browser matrix. If accepted, Phase 2 is technically ready to plan Assignment Comparison as a separate checkpoint using the existing owned scoped queryset and terminal/legacy denominator rules; do not begin it without explicit approval.

- Date: 2026-07-14
- Earlier pass: completed Exit Pulse Phase 2 Checkpoint 2 only: enhanced faculty dashboard, owned Class History, historical-assignment authorization, server-side filters, 20-row pagination, accessible summary/table presentation, focused tests, and concise documentation. Assignment Comparison, cross-assignment UI, charts, exports, administrator analytics, public participation workflow changes, schema changes, unrelated refactoring, commit, and push were not performed in that checkpoint.
- Dashboard behavior: `/faculty/exit-pulse/` keeps the existing feature and `exit_pulse.use` gates and now shows Create only when a current eligible assignment exists, current draft/live sessions, four terminal-only weighted summary cards, up to ten recent completed sessions, and all assignments for which the authenticated faculty owns Exit Pulse sessions. Academic-year and term GET filters apply to terminal analytics, recent sessions, and history discovery; current live work remains visible. `DRAFT`, `LIVE`, and `CANCELLED` do not enter learning analytics.
- Historical authorization: `ExitPulseHistoryService.owned_sessions()` starts from stored `ExitPulseSession.faculty_user` ownership and current request tenant/campus scope. Class History uses `/faculty/exit-pulse/history/<session_public_id>/`; the UUID is an opaque owned-session reference because `FacultyAssignment` has no UUID. The server resolves that reference before querying sessions for the same owner, scope, and assignment. The original faculty retains read-only history after deactivation/replacement without regaining create authority; replacement/other faculty, forged UUIDs, and cross-tenant/campus requests receive 404 and do not inherit prior sessions.
- History behavior: the page displays assignment context/current-or-historical state, completed count/date range, terminal-only session rows, result links, and GET filters for inclusive local dates, question type, normalized case-insensitive topic text, and `CLOSED`/`EXPIRED` status. It uses stable newest-first ordering, 20-row pagination, safe invalid-page fallback, filter-preserving links, reset and empty states, escaped topic/question content, and only `Enabled`/`Not enabled` for written feedback. Confidential feedback text remains available solely on the existing owner-authorized result page.
- Analytics/denominator policy: Checkpoint 1 weighted formulas are reused without template calculations. Weighted response rate excludes null-snapshot sessions from both numerator and denominator; understanding/support rates include all valid terminal responses. A stored zero is historical and displays as zero; null displays `Unavailable` and `Not historically comparable`, with a missing-session notice. Rates above 100% remain uncapped and receive neutral browser-participation wording. Progress widths alone are clamped to the accessible 0-100 visual range.
- Query/accessibility review: feature-data work is bounded at approximately eight queries on the dashboard (expiry update, current assignments, filter choices/current rows, two aggregates, recent rows, assignment grouping) and eight on history (expiry, owned reference/context/current check, two aggregates, paginator count, page rows), plus normal permission/feature/context processing. Filtered `Count`, `Sum`, `Subquery`, and `select_related` avoid response-object and assignment-row N+1 queries; service tests hold session-row and grouped-assignment work to one query each. Both pages have one H1, logical headings, associated filter errors, scoped responsive tables, descriptive links, visible percentages, labelled progress bars, state text, accessible pagination, and announced empty states.
- Changed files: `apps/exit_pulse/forms.py`; `apps/exit_pulse/services.py`; `apps/exit_pulse/tests.py`; `apps/exit_pulse/urls.py`; `apps/exit_pulse/views.py`; `templates/exit_pulse/landing.html`; new `templates/exit_pulse/history.html`; `apps/faculty_portal/help_guide.py`; `apps/faculty_portal/tests_help_guide.py`; `CHANGE_LOG.md`; `TEACHERMATEPLUS_CONTEXT.md`; and `HANDOFF.md`. No model or migration file changed.
- Validation performed: focused `ExitPulseDashboardAndHistoryTests` passed 12/12; final `python manage.py test apps.exit_pulse.tests -v 1` passed 57/57; `python manage.py test apps.faculty_portal.tests_help_guide -v 1` passed 5/5; `python manage.py check` passed; `python manage.py makemigrations --check --dry-run` reported no changes; and `git diff --check` passed with line-ending warnings only. Expected CSRF-denial and mocked cache-failure logs appeared in their existing security tests. No commit or push occurred.
- Pending/known risks from that checkpoint: desktop/mobile and assistive-technology browser smoke remains manual. Response rates can legitimately exceed 100% if eligible enrollment changes after the immutable session-start snapshot; rates remain uncapped. Historical retention continues to follow stored session ownership, and institutional purge/identity-retention policy must be handled separately.
- Exact next step: smoke the dashboard and Class History in staging as the original faculty, replacement faculty, permission-denied faculty, current assignment, historical assignment, legacy-null session, zero-snapshot session, and a 21-plus-session filtered history at desktop/mobile widths. If accepted, plan Checkpoint 3 Assignment Comparison separately while preserving the same ownership, tenant/campus, terminal-session, and historical-denominator rules.

- Date: 2026-07-14
- Current pass: completed Exit Pulse Phase 2 Checkpoint 1 backend work only: Phase 1 inspection, immutable historical enrollment denominator, reusable terminal/weighted analytics, focused tests, migration, and concise documentation. No dashboard, Class History, Assignment Comparison, filter, pagination, template, navigation, Phase 3, commit, or push work was performed.
- Model/lifecycle change: `ExitPulseSession.enrollment_count_snapshot` is a nullable, non-editable `PositiveIntegerField`. The locked `DRAFT -> LIVE` service transition counts only active `Enrollment` rows with status `ACTIVE`, writes the snapshot in the same transaction as `started_at`/expiry, preserves a real zero, and refuses to start a draft that unexpectedly already has a snapshot. Draft creation does not capture it, and later enrollment changes or repeated start attempts cannot replace it. Migration `exit_pulse.0002_exitpulsesession_enrollment_count_snapshot` adds only this nullable field, has no index, and deliberately performs no historical backfill.
- Legacy policy: null means no historical denominator was captured. Phase 1 single-session analytics retains its prior current-eligible-enrollment fallback only as `CURRENT_ENROLLMENT_ESTIMATE`, exposed with `enrollment_denominator_is_historical=False`; a stored value, including zero, is `STORED_SNAPSHOT`. Weighted assignment response analytics exclude null-snapshot sessions from both response numerator and enrollment denominator and report `missing_denominator_session_count`. Their valid reactions still participate in weighted understanding/support rates because those formulas do not depend on enrollment.
- Analytics foundation: `ExitPulseAnalyticsService.terminal_sessions()` includes only `CLOSED`/`EXPIRED`; `build()` now exposes response counts and denominator provenance; `build_assignment()` returns terminal session count, distinct non-empty topic count, latest terminal `started_at`, total responses, stored/missing denominator counts, summed historical denominator, response numerator with stored denominator, understanding/support counts, and zero-safe weighted rates. It uses two aggregate queries independent of the number of sessions: one session aggregate and one response aggregate.
- Exact formulas: weighted response rate = responses from terminal sessions with non-null snapshots / sum of those snapshots x 100; weighted understanding rate = `CONFIDENT + MOSTLY_UNDERSTOOD` responses / all valid terminal responses x 100; weighted support-needed rate = `NEEDS_CLARIFICATION + NEEDS_PRACTICE` responses / all valid terminal responses x 100. All use one-decimal `ROUND_HALF_UP` and return `0.0` for a zero denominator.
- Changed files: `apps/exit_pulse/models.py`; `apps/exit_pulse/services.py`; `apps/exit_pulse/tests.py`; new `apps/exit_pulse/migrations/0002_exitpulsesession_enrollment_count_snapshot.py`; `CHANGE_LOG.md`; `TEACHERMATEPLUS_CONTEXT.md`; and `HANDOFF.md`.
- Validation performed: baseline `python manage.py test apps.exit_pulse.tests -v 1` passed 37/37. Focused lifecycle/Checkpoint 1/session-result tests passed 15/15 after correcting an initial aggregate join that duplicated snapshot sums. Final full Exit Pulse suite passed 45/45. `python manage.py migrate --plan` showed only the expected field migration and `python manage.py migrate` applied it successfully. Final `python manage.py check` passed; `python manage.py makemigrations --check --dry-run` reported no changes; and `git diff --check` passed with line-ending warnings only.
- Pending work / decisions before UI at that time: history/comparison authorization had to include only the faculty member's allowed current/historical assignments and existing tenant/campus scope. The Phase 2 UI had to label legacy single-session denominators as estimated/unavailable and show missing-denominator counts rather than implying precision. Response rates remain uncapped because eligible enrollment may change after the stored session-start snapshot.
- Exact next step: begin the Phase 2 UI checkpoint by designing authorized historical-assignment querysets, then consume the new analytics service for the landing dashboard, Class History, and Assignment Comparison without recalculating historical enrollment. Preserve the two-query aggregate pattern, visibly distinguish legacy denominator estimates, and add scope/filter/pagination tests before any Phase 3 work.

- Date: 2026-07-14
- Current pass: completed the requested post-implementation security, correctness, privacy, performance, accessibility, and regression review of Phase 1 Exit Pulse only; no Phase 2 feature, migration, commit, or push.
- Confirmed findings fixed: public bearer tokens no longer appear in HTTP request paths or normal access logs; QR/public links now use `/pulse/#<token>`, JavaScript removes the fragment from browser history and posts it as a sensitive CSRF-protected field to generic `/pulse/open/` and `/pulse/submit/` endpoints, and public responses use `Referrer-Policy: no-referrer`. A live/draft pulse is now atomically auto-cancelled with a system audit if its accepted faculty assignment/offering becomes invalid, inactive, closed, or scope-inconsistent. Duplicate insert races map safely to the existing duplicate response without a server error. Expired identifier cleanup is tenant/session scoped and now has an idempotent scheduled command. Public/faculty status and response controls received focused screen-reader, error-association, focus, and expiry-state improvements.
- Security/scope result: faculty create/start/extend/close/cancel/status/results remain owner-only, tenant/campus/request-scope filtered, permission gated, feature gated, and limited to accepted active assignments with active/open academic dependencies. Public output contains no faculty, tenant, campus, course, section, roster, result, or classmate identity. Templates remain autoescaped; no `safe`, `mark_safe`, `csrf_exempt`, or `innerHTML` pattern was introduced. Confidential responses remain uneditable and protected by the database uniqueness constraint plus nested-savepoint `IntegrityError` handling.
- Rate-limit/privacy result: rate limits remain defense-in-depth and fail closed when the cache is unavailable. The configured `LocMemCache` is per process, so browser/IP counters are not globally coordinated across the production Gunicorn workers; the database lifecycle and uniqueness controls remain authoritative. Use an approved shared Django cache backend if cross-worker throttling is operationally required. The new `anonymize_exit_pulse_identifiers` command removes only expired technical hashes and preserves reaction/comments; production cron runs it hourly at minute 5.
- Query/accessibility result: landing eligibility uses a subquery and avoids per-session assignment checks; polling uses scoped session lookup, assignment validation, and one aggregate count without per-response loops; results use aggregate queries and session-scoped cleanup. Existing token/status/session/response indexes remain appropriate. Survey fields now have explicit descriptions/error relationships and full-width controls; countdown announcements are throttled to useful thresholds, and expiry disables controls while server lifecycle checks remain authoritative.
- Changed files in this review: `.env.example`; `apps/exit_pulse/{forms.py,services.py,tests.py,urls.py,views.py}`; new `apps/exit_pulse/management/commands/anonymize_exit_pulse_identifiers.py` plus package initializers; `templates/exit_pulse/{live.html,public_survey.html}`; `ops/cron/teachermateplus.cron`; `docs/{DEPLOYMENT_UBUNTU.md,performance_optimization.md}`; `CHANGE_LOG.md`; `TEACHERMATEPLUS_CONTEXT.md`; and `HANDOFF.md`. `logs/system.log` contains local test-run logging noise and is not a product change.
- Validation performed: baseline Exit Pulse suite passed 31/31 before fixes. Final `python manage.py test apps.exit_pulse.tests -v 1` passed 37/37 after all changes, covering fragment-token transport, generic endpoint CSRF, stale-assignment cancellation/audit, duplicate insert race handling, cache failure/no storage, scoped/idempotent cleanup with payload preservation, and form accessibility. `python manage.py anonymize_exit_pulse_identifiers --dry-run` completed with 0 eligible rows; `python manage.py check` passed; `python manage.py makemigrations --check --dry-run` reported no changes; and `git diff --check` passed with line-ending warnings only.
- Pending work / risk: desktop/mobile and assistive-technology browser smoke remains manual. Fragment-token entry requires JavaScript and intentionally shows a `noscript` warning. Existing links created before this review used short-lived request-path tokens and should be treated as expired/replaced. The deployment must install the updated cron file; otherwise cleanup still occurs lazily when a session's results are opened. LocMem rate limiting remains per worker unless an approved shared cache is configured.
- Exact next step: deploy to staging, restart Gunicorn, install/reload the updated cron entry, verify the cleanup command/log, create a new pulse and confirm its QR resolves to `/pulse/#...` without the token in Nginx/Gunicorn access logs, submit from multiple browsers, deactivate/replace the assignment during a live pulse to confirm automatic cancellation, and smoke countdown/error/focus/live-count/results behavior at desktop and mobile widths. Phase 1 has no remaining code-level blocker from this review; begin Phase 2 planning only after those staging checks and operational cache/cron decisions are accepted.

- Date: 2026-07-14
- Current pass: implemented only Phase 1 Exit Pulse MVP; no dashboard history, cross-assignment comparison, charts, exports, administrator reporting, student authentication, attendance/gradebook integration, new real-time infrastructure, commit, or push.
- Original Phase 1 work: added the dedicated `exit_pulse` Django app with scoped session/response models, migrations, question safeguards, five-minute lifecycle/one extension, secure public token and dynamic ReportLab SVG QR, cache-backed browser/IP rate limits, normal CSRF, public mobile survey, five-second faculty polling, terminal owner-only aggregates/comments, tenant feature switch, `exit_pulse.use`, Faculty Classroom Tools navigation, faculty action audits, concise guide/context/changelog documentation, and environment examples. The original browser-only participation control described in that implementation has now been superseded by validated-enrollment enforcement.
- Current privacy/security boundary superseding the original Phase 1 note: students do not log in, but new responses require exact active class enrollment validation and retain a restricted identity relation. Faculty views still receive no student number/name/enrollment identity. Browser identifiers remain signed, session-HMAC hashed, never displayed/logged, and marked for cleanup after about 24 hours, but duplicate enforcement is now per session/enrollment. Public submission remains POST-only, CSRF-protected, lifecycle/feature/scope checked under a transaction, duplicate-race safe, rate-limited, and strict about malformed state/reactions/overlength text.
- Models/migrations: `ExitPulseSession` snapshots tenant, campus, faculty, accepted assignment/offering, academic scope, course/section, topic/question/prompts, secure token, status/times, creator, extension state, and the immutable start-time enrollment denominator. `ExitPulseResponse` stores one stable reaction code, optional 200-character answers, the protected enrollment relation, notice evidence, and a short-lived technical browser hash. Legacy identity fields remain null/blank with no backfill.
- Changed files: `.env.example`; `config/settings/base.py`; `config/urls.py`; new `apps/exit_pulse/{apps.py,forms.py,models.py,services.py,tests.py,urls.py,views.py,migrations/0001_initial.py}` and package initializers; `apps/rbac/migrations/0027_seed_exit_pulse_permission.py`; `apps/core/{context_processors.py,services/features.py,management/commands/seed_stage_0_1.py}`; `apps/admin_portal/{forms.py,views.py,tests_assignment_acceptance.py}`; `apps/faculty_portal/{help_guide.py,tests_help_guide.py}`; new `templates/exit_pulse/{landing.html,create.html,live.html,public_survey.html,results.html}`; `templates/faculty_portal/base.html`; `templates/admin_portal/tools/configurable_features.html`; `CHANGE_LOG.md`; `TEACHERMATEPLUS_CONTEXT.md`; and `HANDOFF.md`.
- Original Phase 1 validation passed 31/31 at that time; current validation is recorded in the identity-validation section above and supersedes that count and the original browser-control assumptions.
- Current remaining risk: desktop/mobile visual smoke remains manual. Enrollment uniqueness now prevents a second-browser duplicate, while cache rate limits remain per backend/process when LocMemCache is used; deploy a shared cache for cross-worker enforcement. Expired technical browser hashes are cleaned separately from the retained accountability relation. Identity retention and any investigation access remain governance decisions.
- Exact next step: restart the local server after migration, sign in as a faculty user with an accepted active class, verify Classroom Tools -> Exit Pulse and the feature switch, launch each question type, scan the QR on a phone, submit from two browsers on the same network, verify live count/one extension/close/expiry/cancel states and owner isolation, then inspect results at desktop and mobile widths. Confirm comments are absent live and on cancelled results but visible/escaped after close/expiry. For production, configure appropriate cache/rate-limit values and run the two migrations before rollout.

- Date: 2026-07-14
- Current pass: removed the standalone Faculty login page, redirected Faculty invitation completion to `/faculty/`, and aligned the Faculty invitation email with the existing Account Onboarding design; no account-state, invitation-token, permission, scope, migration, data, commit, or push change.
- Root cause and completed work: `faculty_invitation_accept_view()` explicitly redirected successful and invalid token submissions to `accounts:faculty_login`, whose standalone template exposed `Go to Admin Login`. Both outcomes now redirect directly to `faculty_portal:public_index` (`/faculty/`). The `/faculty/login/` named URL remains only as a non-permanent compatibility redirect, ignores submitted credentials, and the obsolete `FacultyLoginView` plus `templates/faculty_portal/login.html` were removed. OTP retry/back navigation, legacy public links, and seed output now use `/faculty/`.
- Email update: `templates/accounts/emails/faculty_invitation.html` now reuses the existing new-user email's green/gold card, `NCBA | TeacherMate+` branding, `ACCOUNT ONBOARDING` label, Welcome heading, field table, green action button, and security footer. Invitation-specific username, 24-hour validity, single-use/supersession, activation, and no-plaintext-password behavior remain unchanged; the plain-text alternative also identifies Account Onboarding.
- Changed files in this pass: `apps/accounts/views.py`; `apps/accounts/urls.py`; `apps/core/middleware.py`; `apps/core/management/commands/seed_stage_0_1.py`; `apps/imports/tests_faculty_user_import.py`; `apps/accounts/tests_login_lockout.py`; `apps/faculty_portal/tests_public_login.py`; `templates/accounts/faculty_invitation_accept.html`; `templates/accounts/emails/faculty_invitation.html`; `templates/accounts/emails/faculty_invitation.txt`; deleted `templates/faculty_portal/login.html`; `templates/public/index.html`; `apps/admin_portal/help_guide.py`; `docs/FACULTY_USER_IMPORT.md`; `CHANGE_LOG.md`; `TEACHERMATEPLUS_CONTEXT.md`; and `HANDOFF.md`.
- Validation performed: invitation, Faculty public-login, and login-lockout focused suites passed 27/27, covering successful setup redirect, invalid/used invitation destination, disabled legacy GET/POST behavior, no legacy credential processing, no Admin-login exposure, onboarding email design, lockout, and session enforcement. The broader Faculty import, public login, OTP, lockout, and Admin help regression set passed 73/73. `python manage.py check` passed; `python manage.py makemigrations --check --dry-run` reported no changes; and `git diff --check` passed with line-ending warnings only.
- Pending work / risk: the in-app browser had no active tab, so visual verification of the `/faculty/` success message and the HTML invitation card in a real email client remains manual. `/faculty/login/otp/` intentionally remains because it is the second authentication step reached from the supported `/faculty/` inline form when Faculty login OTP is enabled.
- Exact next step: open a fresh invitation, set the password, confirm the browser lands on `/faculty/` with the success message, sign in through the inline form, and verify direct GET/POST requests to `/faculty/login/` return to `/faculty/` without rendering or processing the removed page. Send one safe-backend invitation and preview the Account Onboarding card in the supported email clients.

- Date: 2026-07-14
- Current pass: added only the requested Faculty User CSV upload-page flow icon and image modal; retained the original inactive-until-password design, with no provisioning, invitation, activation, permission, scope, migration, data, commit, or push change.
- Completed work: added a compact accessible flow-diagram button beside the `Bulk Import: Faculty Users` title. It appears only for the `faculty_users` import type and opens a Bootstrap XL centered/scrollable modal containing `media/imahe/faculty-users-acct-flow.png`; the responsive image has descriptive alternative text and is lazy-loaded. Updated the Admin help entry, change log, and project context.
- Changed files in this pass: user-supplied untracked `media/imahe/faculty-users-acct-flow.png`; `templates/admin_portal/imports/import_upload.html`; `apps/imports/tests_faculty_user_import.py`; `apps/admin_portal/help_guide.py`; `CHANGE_LOG.md`; `TEACHERMATEPLUS_CONTEXT.md`; and `HANDOFF.md`.
- Validation performed: the focused authenticated Faculty upload-page rendering test passed 1/1, confirming the modal trigger, modal container, and configured media URL. The full Faculty import plus Admin help-guide regression slice passed 52/52. `python manage.py check` passed; `python manage.py makemigrations --check --dry-run` reported no changes; and `git diff --check` passed with line-ending warnings only.
- Pending work / risk: the in-app browser had no active browser instance, so clicking the icon and checking the tall diagram at desktop/mobile widths remains a manual visual smoke check. The image is currently an untracked repository file and must be included in the deployment artifact for the modal image to load outside this workspace.
- Exact next step: open Security -> Users -> Import Faculty CSV, click the round flow icon beside the page title, verify the complete diagram can be scrolled and the close controls work, then repeat at a narrow mobile viewport.

- Date: 2026-07-14
- Current pass: investigated and fixed the development manual-test findings for Faculty User CSV Import; no new model fields/migration, unrelated feature, commit, or push.
- Development evidence: `central@ncba.edu.ph` was created by batch 50 with `CREATED_EMAIL_DISABLED`, inactive/unusable-password audit state, one scoped assignment to the single existing active `FACULTY` role, and invitation `DISABLED_BY_SYSTEM` with attempt count 0/no send or failure timestamp. The importing Superadmin had send permission and SMTP was configured, but `.env` omitted `FACULTY_IMPORT_EMAIL_ENABLED`, so the setting safely defaulted False and the batch persisted both email flags False. Batch 51 correctly recorded `SKIPPED_EXISTING`; no duplicate user, role definition, role assignment, or automatic invitation attempt was created. A later audited normal User edit changed this account from inactive to active while its password remained unusable; the persistent invitation flow now safely permits setup for that active-but-not-login-ready state.
- Root-cause fixes: the confirmation checkbox previously existed only while `can_confirm` was true and disappeared after confirmation, while upload showed only a notice; it is now an unmistakable dedicated panel, always visible on Faculty batch detail, disabled with environment/permission explanations, unchecked by default when available, and replaced after confirmation by the persisted batch choice. Preview previously stored skip intent only inside normalized JSON and rendered generic `PREVIEW_VALID`; new rows now use `PREVIEW_CREATE`, `PREVIEW_SKIP_EXISTING`, or `FAILED_VALIDATION` with plain labels and created-vs-skipped counts. Resend previously existed only on the import result row; Faculty user edit now has a persistent status/action panel and user-based send endpoint.
- Persistent invitation behavior: `Security -> Users -> Edit User` displays Not sent, Email disabled, Sent, Failed, Expired, or Accepted plus environment, attempt, last-attempt, last-send, and expiry data. Authorized in-scope admins can Send/Resend without the original batch; successful resend uses the existing user, increments/version-supersedes the invitation, resets the 24-hour expiry, and preserves the five-minute throttle. Accepted/login-ready accounts have no action. Email-disabled and missing-permission cases remain visible but disabled. Future User edit audit events exclude the password field/hash/unusable representation.
- Changed files in this follow-up: local ignored `.env`; `apps/accounts/models.py`; `apps/accounts/faculty_provisioning.py`; `apps/imports/models.py`; `apps/imports/services.py`; `apps/imports/tests_faculty_user_import.py`; `apps/admin_portal/import_views.py`; `apps/admin_portal/views.py`; `apps/admin_portal/urls.py`; `apps/admin_portal/help_guide.py`; `templates/admin_portal/imports/import_batch_detail.html`; new `templates/admin_portal/security/user_update.html` and `_faculty_invitation_panel.html`; `docs/FACULTY_USER_IMPORT.md`; `CHANGE_LOG.md`; `TEACHERMATEPLUS_CONTEXT.md`; and `HANDOFF.md`. Existing user-owned `config/settings/local.py` changes and `logs/system.log` noise were preserved and not treated as this fix.
- Validation performed: focused Faculty import/security suite passed 36/36; combined Faculty import, Student import, Admin users, roles, and Admin help-guide regressions passed 87/87; `python manage.py check` passed; `python manage.py migrate --plan` reported no pending operations; `python manage.py makemigrations --check --dry-run` reported no changes; `git diff --check` passed with line-ending warnings only. In-app browser discovery returned no available browser instance, so live visual smoke could not run; authenticated Django client coverage verified the affected pages and actions.
- Pending work / risk: development email intentionally remains explicitly disabled in `.env`, so no real SMTP invitation was sent. Browser visual smoke and a controlled mail-delivery test remain. Do not enable real email in development/staging/testing/UAT; use a safe test backend or approved production rollout. The existing `central@ncba.edu.ph` account is currently active but still has an unusable password because it was manually activated after import; the new persistent panel can issue setup once email is safely enabled.
- Exact next step: restart the development server so the explicit `.env` values and templates are loaded, open batch 50 and batch 51 to verify the human email/skip outcomes, then open Security -> Users -> `central@ncba.edu.ph` to verify the persistent Email disabled / Send Invitation panel. For delivery smoke, use an approved non-production mail backend with `FACULTY_IMPORT_EMAIL_ENABLED=True`, send once, wait or backdate beyond five minutes for resend testing, confirm the old link fails and the new link expires about 24 hours after send, then restore the switch to False.

- Date: 2026-07-14
- Current pass: implemented the approved Simpler V1 Faculty User CSV Import using only `accounts.User`, `UserRole`, the fixed active `FACULTY` role, and existing tenant/campus/department/import infrastructure; no FacultyProfile, employee number, HR record, commit, or push.
- Completed work: added exact eight-column Faculty CSV template/preview, derived-username handling, all-row duplicate detection, existing-account reconciliation rules, confirmation-time permission/scope/reference revalidation, per-row atomic inactive user creation with unusable password, shared scoped role-assignment service reuse, typed import results, formula-safe error CSV, Faculty PII view protection, raw CSV non-retention, user links, import history/card/navigation, and complete Faculty import/row/provisioning audit events.
- Invitation security: added one-per-user invitation tracking and a dedicated public password-setup flow. Signed tokens travel in the browser URL fragment so they are absent from the initial HTTP request/access-log path, are posted only as a sensitive form field, and are removed from browser history on page load. Links are not stored, are versioned/single-use, expire from the latest successful send (24 hours by default), are invalid after acceptance or resend, and activate the account only after a valid password is saved. Send/resend is outside the account transaction, requires dedicated scoped permissions, is throttled for five minutes, preserves accounts after SMTP failure, and never stores or displays a token, setup URL, or plaintext password.
- Email and retention controls: added `FACULTY_IMPORT_EMAIL_ENABLED=False` and `FACULTY_INVITATION_EXPIRY_HOURS=24`. Email requires the environment switch, unchecked-by-default per-batch option, send permission, and successful provisioning. With the switch off, no token is generated and the account stays inactive for later authorized send. Faculty raw CSV bytes are parsed in memory and never written to import media; staged normalized PII remains permission-protected.
- Migrations: `imports.0006_importbatch_email_system_enabled_snapshot_and_more`, `accounts.0009_facultyinvitation`, and `rbac.0026_seed_faculty_user_import_permissions`. The RBAC migration seeds `faculty_users.view_import`, `faculty_users.import`, `faculty_users.send_import_invitations`, and `faculty_users.resend_invitation` for active Super Admin, Tenant Admin, and Campus Admin roles.
- Changed files: `.env.example`; `config/settings/base.py`; `apps/accounts/{models.py,urls.py,views.py,faculty_provisioning.py,migrations/0009_facultyinvitation.py}`; `apps/imports/{models.py,services.py,tests_faculty_user_import.py,migrations/0006_importbatch_email_system_enabled_snapshot_and_more.py}`; `apps/admin_portal/{import_views.py,urls.py,views.py,help_guide.py}`; `apps/core/{middleware.py,services/csv_safety.py,management/commands/seed_stage_0_1.py}`; `apps/rbac/migrations/0026_seed_faculty_user_import_permissions.py`; Faculty invitation/account/import templates; `docs/FACULTY_USER_IMPORT.md`; `CHANGE_LOG.md`; `TEACHERMATEPLUS_CONTEXT.md`; and `HANDOFF.md`. Existing `logs/system.log` worktree noise is unrelated and was not treated as feature work.
- Validation performed: migrations applied successfully with `python manage.py migrate`; `python manage.py check` passed; the current focused Faculty import/security suite passed 29/29, including 9 invitation/activation/resend tests after fragment-based token hardening; the combined Faculty import, Student import, Admin users, roles, and Admin help-guide suite passed 77/77 before the final isolated token transport hardening, with all 29 current Faculty tests rerun afterward; `python manage.py makemigrations --check --dry-run` reported no changes; `git diff --check` passed with line-ending warnings only. An early regression command included nonexistent label `apps.imports.tests`; its other 37 tests passed, and the correct labels were used in the final combined run.
- Pending work / risk: no real SMTP delivery was attempted because the feature defaults safely off. Browser visual smoke remains for Users -> Import Faculty CSV, preview/result tables, error download, invitation status/resend, and the public password-setup page. Before production email enablement, verify the deployed public host/link, SMTP delivery, supported mail clients, privacy handling, and role ownership. V1 intentionally has global email/username uniqueness and no FacultyProfile, employee number, HR/master-record sync, per-row role, or per-row email controls.
- Exact next step: deploy to staging with `FACULTY_IMPORT_EMAIL_ENABLED=False`, run migrations, upload a scoped sample Faculty CSV, confirm inactive accounts/roles/results/error export, and smoke the password setup using a safe non-production email backend. Keep the switch false through development, staging, testing, and UAT; enable it in production only after the documented operational checks and approval.

- Date: 2026-07-13
- Current pass: corrected only secure client-IP resolution for the actual Sophos WAF -> Nginx -> Gunicorn Unix-socket deployment; no migration, lockout-policy, grade-engine, unrelated feature, commit, or push change.
- Completed work: added `TRUST_UNIX_SOCKET_PROXY`, backed by `DJANGO_TRUST_UNIX_SOCKET_PROXY` and defaulting False. When `REMOTE_ADDR` is missing/non-IP, headers remain untrusted unless this setting is explicitly enabled. In the opted-in Unix-socket case, the resolver prefers a valid normalized `X-Real-IP`; when absent, it accepts only a fully valid `X-Forwarded-For` chain and returns its final Nginx-appended address. Malformed values return None and no chain is stored. Existing trusted/untrusted TCP behavior is preserved.
- Deployment safety: enable Unix-socket proxy trust only when socket ownership/permissions prevent every process except the trusted Nginx reverse proxy from connecting. Nginx remains responsible for trusting only Sophos WAF `192.168.20.1`, rewriting `$remote_addr`, and overwriting the forwarded headers supplied to Gunicorn.
- Changed files: `.env.example`, `config/settings/base.py`, `apps/core/services/client_ip.py`, `apps/core/tests_client_ip.py`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, `HANDOFF.md`.
- Validation performed: `python manage.py test apps.core.tests_client_ip apps.accounts.tests_login_lockout apps.accounts.tests_login_otp` passed with 25 tests, including Unix trust disabled/enabled, X-Real-IP preference, safe X-Forwarded-For fallback, malformed headers, IPv4/IPv6 normalization, unchanged TCP trust behavior, audit storage, lockout `last_ip` storage, and existing lockout/OTP regressions. `python manage.py check` passed; `python manage.py makemigrations --check --dry-run` reported no changes; `git diff --check` passed with line-ending warnings only.
- Pending work / risk: deployment must explicitly set `DJANGO_TRUST_UNIX_SOCKET_PROXY=true` and verify Gunicorn socket access controls before relying on the Unix-socket branch. Browser UI changes were not part of this correction.
- Exact next step: update staging environment configuration, verify the socket owner/group/mode and Nginx real-IP directives, then trigger a failed login through Sophos to confirm a single normalized client IP reaches AuditLog and Login Lockout Monitor.

- Date: 2026-07-13
- Current pass: implemented the trusted-proxy client-IP dependency and the narrowly scoped Login Lockout Monitor IP column; no migration, grade-engine, unrelated Admin Portal, commit, or push change.
- Completed work: added `resolve_client_ip()` with explicit trusted-proxy IP/CIDR configuration, normalized IPv4/IPv6 output, right-to-left trusted proxy chain handling, untrusted-header rejection, and malformed-chain fail-closed behavior. Existing audit IP capture and failed-login `last_ip` storage now use the resolver. The Login Lockout Monitor uses the already-loaded `last_ip` field in a dedicated `IP Address` column immediately after `Last Failed`, shows `-` for null, and no longer repeats Last IP under Username.
- Behavior boundaries: the page queryset/view, tenant/campus/permission scope, portal/status/search filters, unlock action, five-attempt/fifteen-minute defaults, retained-IP behavior after clear/expiry, and already-locked no-overwrite behavior remain unchanged. No forwarded-header chain or raw request metadata is rendered.
- Changed files: `.env.example`, `config/settings/base.py`, `apps/core/services/client_ip.py`, `apps/core/services/audit.py`, `apps/core/tests_client_ip.py`, `apps/accounts/services.py`, `apps/accounts/tests_login_lockout.py`, `templates/admin_portal/security/login_lockout_list.html`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, `HANDOFF.md`.
- Validation performed: `python manage.py test apps.core.tests_client_ip apps.accounts.tests_login_lockout apps.accounts.tests_login_otp` passed with 20 tests; `python manage.py check` passed; `python manage.py makemigrations --check --dry-run` reported no changes; `git diff --check` passed with line-ending warnings only.
- Pending work / risk: deployment must set `DJANGO_TRUSTED_PROXY_IPS` to only the actual reverse-proxy IPs/CIDRs; leaving it blank safely ignores forwarded headers but records the direct peer. Browser visual smoke remains recommended for the added table column at narrow widths.
- Exact next step: configure the staging proxy allowlist, trigger a failed login through the real proxy, and confirm the normalized client address appears in Login Lockout Monitor without exposing the forwarded chain.

- Date: 2026-07-13
- Current pass: changed only the new-user onboarding email branding used by `/admin-portal/security/users/create/`; no other email, page, service, authentication, grading, migration, data, commit, or push change.
- Completed work: replaced the conditional NCBA image/fallback block in `templates/admin_portal/emails/new_user_credentials.html` with the exact text header `NCBA | TeacherMate+`. All other onboarding content and the existing account-creation/email-sending workflow remain unchanged.
- Changed files: `templates/admin_portal/emails/new_user_credentials.html`, `apps/admin_portal/tests_users.py`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, `HANDOFF.md`.
- Validation performed: the actual Admin user-create/save-and-email test and the dedicated onboarding-email sender test passed with 2 tests. Both assert `NCBA | TeacherMate+` and absence of an HTML `<img>` in the onboarding email. `python manage.py check` passed; `python manage.py makemigrations --check --dry-run` reported no changes; `git diff --check` passed with line-ending warnings only.
- Pending work / risk: visual appearance still depends on email-client HTML/CSS support, but the text header uses the existing header cell and inline styling.
- Exact next step: send one staging onboarding email from Admin Portal user creation and confirm the text branding in the institution's supported email clients.

- Date: 2026-07-13
- Current pass: adjusted only Admin and Faculty login-page failure feedback and removed the Faculty public-navbar NPC seal; no grade-engine, migration, data modification, commit, or push work.
- Completed work: invalid logins now show the failed-attempt number, configured maximum, remaining attempts, and the configured temporary-lock duration. Reaching the maximum reports no attempts remaining and the active temporary lock. Admin login, the standalone Faculty login, and the public Faculty navbar render the feedback in red bordered alert boxes in the requested locations; the public-navbar seal was removed while the separate lower Faculty privacy-seal section and Admin seal remain unchanged.
- Security boundary: messages retain generic `Invalid username or password` wording and use the same response structure for known and unknown usernames; existing tenant lockout settings, audit events, password reset, authentication, and portal scope are unchanged.
- Changed files: `apps/accounts/forms.py`, `apps/accounts/services.py`, `apps/accounts/tests_login_lockout.py`, `apps/faculty_portal/tests_public_login.py`, `static/faculty_portal/css/public_index.css`, `templates/admin_portal/login.html`, `templates/faculty_portal/login.html`, `templates/faculty_portal/public_index.html`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, `HANDOFF.md`.
- Validation performed: `python manage.py test apps.accounts.tests_login_lockout apps.accounts.tests_login_otp apps.faculty_portal.tests_public_login` passed with 18 tests covering Admin/Faculty lockout, OTP, public login, message content, red-box markup, and Faculty navbar-seal removal. `python manage.py check` passed; `python manage.py makemigrations --check --dry-run` reported no changes; `git diff --check` passed with line-ending warnings only.
- Pending work / risk: desktop and mobile browser visual smoke remains recommended, particularly for long lockout messages in the compact Faculty navbar. The stylesheet cache token was updated so the new layout is not hidden by the prior cached public-page CSS.
- Exact next step: sign in incorrectly once and then through the configured maximum on both portals, checking the red message placement at desktop and mobile widths before deployment.

- Date: 2026-07-12
- Current pass: fixed Faculty Grade Book Monitor exam/grade header alignment; no computation, migration, data modification, commit, or push.
- Root cause: the shared summary layout correctly included a nested subcomponent-average column in each nested section `colspan`, but Faculty Grade Book Monitor did not render that column. CLASS STANDING therefore declared one more column than its lower headers/body and shifted Exam, Period Grade, and Actions headers to the right. Keeping Explain inside the grade cell also made the visual mismatch harder to read.
- Completed work: rendered the missing nested subcomponent-average header, highest-score, and student-value cells; added a dedicated Actions column; moved period/final Explain controls into it; increased the empty-table colspan; and centered metric values with a consistent minimum width for exam/grade columns. Header and body cell counts now match.
- Changed files: `templates/admin_portal/academics/faculty_gradebook_monitor.html`, `apps/admin_portal/views.py`, `apps/admin_portal/tests_scope.py`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, `HANDOFF.md`.
- Validation performed: the masked and authorized-identity Faculty Grade Book Monitor tests passed with 2 tests using a realistic nested Participation/Output plus PRELIM EXAM fixture; regression assertions confirm P/O AVE, PRELIM EXAM, PRELIM Grade, dedicated Actions cells, separated Explain control, and exact equality between the expanded top-header column count and the rendered student-row cell count. `python manage.py check` passed; `python manage.py makemigrations --check --dry-run` reported no changes; `git diff --check` passed with line-ending warnings only. An initial command used the wrong test-method suffix and reported the correct available name; the exact tests were then rerun successfully.
- Pending work / risk: browser visual smoke remains recommended for PRELIM, MIDTERM, PREFINAL, and final-period tables at desktop and narrow widths.
- Exact next step: open each grading period in Faculty Grade Book Monitor and confirm every exam/grade header sits directly above its numeric values while Explain buttons remain in Actions.

- Date: 2026-07-12
- Current pass: documented the completed manual staging and production RBAC permission-inventory repair; documentation only, with no migration, seed, application-code, database, commit, or push action in this pass.
- Repair recorded: development had 125 permission definitions while staging and production had 119 rows even though the defining migrations were already recorded as applied. Six existing definitions were manually created through the Django ORM: `faculty_final_clearance.read`, `gradebook.view_student_identity`, `inactive_records.delete`, `student_account_links.manage`, `student_portal.access`, and `students.import`.
- Staging result: database `teachermateplus_staging_db`; created permission IDs 120-125; 125 total and 125 active permissions; Role Permissions page confirmed 125 available permissions. Pre-repair backup: `/tmp/staging_rbac_before_permission_repair_20260712_133554.json`.
- Production result: database `teachermateplus`; created permission IDs 120-125; 125 total and 125 active permissions. Pre-repair backup: `/tmp/production_rbac_before_permission_repair_20260712_134042.json`.
- Repair boundaries: no source code changed during the database repair; no migration was rolled back, faked, rerun, or newly created; no full seed command or Gunicorn restart occurred; existing role-permission assignments were not changed automatically. The six definitions can now be granted normally through Role Permissions.
- Changed files in this documentation pass: `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, `HANDOFF.md`.
- Validation performed: `git diff --check` passed with line-ending warnings only; the documentation diff contains the six expected permission codes, both backup paths, both deployed database results, and the no-migration/no-seed/no-role-assignment boundaries. No migration file appears in the changed-file list.
- Pending work / risk: the historical cause of the drift remains intentionally unresolved. Role owners must decide which roles, if any, should receive the six repaired permission definitions.
- Exact next step: review the normal Role Permissions page and grant only the newly available permissions each role genuinely requires.

- Date: 2026-07-12
- Current pass: simplified only the Admin Portal Active Course Offerings table; no migration, data modification, commit, or push.
- Completed work: removed the Campus and Term columns from the active table and ordered the replacement presentation exactly as Course, Section, Schedule Text, and Room; blank schedules retain the `-` fallback. The inactive table and all existing campus/academic-year/term filters remain unchanged.
- Changed files: `templates/admin_portal/academics/offering_table.html`, `apps/admin_portal/tests_department_dropdown_labels.py`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, `HANDOFF.md`.
- Validation performed: focused Course Offering layout and Area Chairman visibility tests passed with 10 tests; `python manage.py check` passed; `python manage.py makemigrations --check --dry-run` reported no changes; `git diff --check` passed with line-ending warnings only.
- Pending work / risk: browser visual smoke remains recommended to confirm long Schedule Text values wrap acceptably on desktop and mobile.
- Exact next step: open Admin Portal -> Academics -> Course Offerings and visually check active rows with short, long, and blank schedules.

- Date: 2026-07-12
- Current pass: expanded only the read-only Admin Course Offerings list for Area Chairmen to show same-campus sibling academic areas under the shared `COLLEGE` parent; no migration, commit, or push.
- Completed work: added page-specific Area Chairman College-tree resolution so a BA chair can review BA, BSA, HM, IS, and LA offerings within the selected campus when those departments share the active `COLLEGE` parent.
- Scope safety: Basic Education, Graduate Studies, other campuses, other tenants, inactive records, and non-College department trees remain excluded. The existing `scoped_course_offerings()` service remains department-limited for assignments, grading, corrections, enrollment, monitoring, and all other consumers. Offering create/update permissions and behavior are unchanged.
- Completed work: expanded the Course Offerings department filter on that page to the same College tree and updated Admin guide wording, `CHANGE_LOG.md`, and `TEACHERMATEPLUS_CONTEXT.md`.
- Changed files: `apps/admin_portal/services.py`, `apps/admin_portal/views.py`, `apps/admin_portal/tests_area_chair_offering_visibility.py`, `apps/admin_portal/help_guide.py`, `templates/admin_portal/guide.html`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, `HANDOFF.md`.
- Validation performed: focused Area Chairman visibility, inactive-department, department-dropdown, and Admin help-guide regressions passed with 31 tests; `python manage.py check` passed; `python manage.py makemigrations --check --dry-run` reported no changes; `git diff --check` passed with line-ending warnings only.
- Pending work / risk: browser smoke remains recommended on production-like Fairview data to verify the TMP BA Area Chairman sees the IS test offering while Basic Education/Graduate Studies remain absent. The production inspection export was read-only and was not modified.
- Exact next step: deploy to a test/staging environment, sign in as the scoped BA Area Chairman, open Course Offerings, and verify sibling College visibility and department filtering before production deployment.

- Date: 2026-07-12
- Current pass: implemented an informational Faculty Operational Policies page and contextual policy reminders; no mandatory acknowledgment, migration, commit, or push.
- Completed work: added authenticated `/faculty/guide/operational-policies/` with eight scannable `Faculty must` / `Faculty must not` sections covering account security, assignments/class lists, activities/scores/attendance, review/submission, reopen/correction, Pinnacle-AIMS handoff, privacy, and proper system use.
- Completed work: clarified Section 6 so it explicitly says final periodic grades must be encoded separately in Pinnacle-AIMS after TeacherMate+ processing, TeacherMate+ submission does not automatically post them externally, and Pinnacle-AIMS remains the official source for enrollment information while TeacherMate+ reflects only authorized enrollment changes.
- Completed work: simplified Section 6 at user direction by removing detailed report-selection, report-verification, Registrar correction-posting, and draft/outdated-report statements, and rephrased the remaining boundary as `Treat TeacherMate+ as a replacement for PINNACLE/AIMS.`
- Completed work: linked the policy from the role-based Help Guide, legacy Help Guide, and Full Faculty Manual; added contextual links on Score Entry, Correction of Grades, and the final submission confirmation; classified policy-page feedback under the existing Faculty Guide feature.
- Governance boundary: the page clearly remains informational pending institutional approval. It does not require faculty acknowledgment and does not change grade computation, submission, locks, permissions, reopen, correction, or Pinnacle-AIMS workflows.
- Changed files: `apps/faculty_portal/operational_policies.py`, `apps/faculty_portal/views.py`, `apps/faculty_portal/urls.py`, `apps/faculty_portal/feedback.py`, `apps/faculty_portal/tests_help_guide.py`, `templates/faculty_portal/operational_policies.html`, `templates/faculty_portal/guide_role_based.html`, `templates/faculty_portal/guide.html`, `templates/faculty_portal/guide_manual.html`, `templates/faculty_portal/activity_scores.html`, `templates/faculty_portal/period_summary.html`, `templates/faculty_portal/period_corrections.html`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, `HANDOFF.md`.
- Validation performed: `python manage.py test apps.faculty_portal.tests_help_guide apps.faculty_portal.tests_feedback` passed with 15 tests; `python manage.py check` passed; `python manage.py makemigrations --check --dry-run` reported no changes; `git diff --check` passed with line-ending warnings only.
- Pending work / risk: the in-app browser was unavailable, so desktop/mobile visual smoke remains for the new policy page, Help/Manual links, and Score Entry/Summary/Correction reminders. Institutional academic, Registrar, privacy, and records-management owners should approve the wording before any future mandatory acknowledgment feature is enabled.
- Exact next step: browser-smoke the policy page and contextual links as a Faculty user; after formal policy approval, decide separately whether to implement configurable, versioned acknowledgment and an admin compliance report.

- Date: 2026-07-12
- Current pass: investigated current Faculty Portal blank recitation/activity score behavior for Grade Summary and submission; no production behavior change, migration, commit, or push.
- Completed work: traced score entry, score storage, summary recompute, readiness evaluation, Grade Summary blocking UI, and period submission blocking for the scenario where 40 ACTIVE students have one July 6 Recitation activity, 10 saved scores, and 30 unsaved/blank scores.
- Completed work: added a focused characterization test proving current behavior: 10 active `StudentActivityScore` rows are saved, the other 30 students remain without score rows, Grade Summary readiness reports 30 missing students and 25.00% coverage, and period submission is blocked.
- Key finding: saved raw score `0` is treated as a valid encoded score and is not missing; a truly blank score means no active `StudentActivityScore` row exists, including after a score is cleared or when a student was never saved for that activity.
- Changed files in this pass: `apps/faculty_portal/tests_assignment_acceptance.py`, `HANDOFF.md`.
- Validation performed: focused 40-student characterization test passed; related blank-vs-zero/readiness slice passed with 4 tests; `python manage.py check` passed; `python manage.py makemigrations --check --dry-run` reported no changes; `git diff --check` passed with line-ending warnings only.
- Pending work / risk: no browser smoke was run; current report is code/test-backed. Existing unrelated dirty files remain in the worktree, including prior Faculty Activities visual/grouping files and `logs/system.log`.
- Exact next step: if a behavior change is desired later, decide explicitly whether blank visible inputs should stay blank/no-row instead of being converted to saved zero during score-entry POST.

- Date: 2026-07-11
- Current pass: changed Faculty Portal Activities detail-group header color from violet to yellow/gold; no commit or push.
- Completed work: updated `templates/faculty_portal/period_activities.html` so Detail-level group headers use a darker yellow/gold gradient and matching border instead of violet. Component and subcomponent header colors remain unchanged. This is CSS-only; no behavior, calculations, URLs, permissions, or score data changed.
- Changed files in this pass: `templates/faculty_portal/period_activities.html`, `HANDOFF.md`.
- Validation performed: `python manage.py test apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_period_activities_grouped_view_is_default_and_uses_template_hierarchy` passed; `python manage.py check` passed; `git diff --check` passed with line-ending warnings only.
- Pending work / risk: browser visual smoke is still recommended to confirm the yellow/gold detail headers look balanced against the green component/subcomponent headers. `logs/system.log` remains unrelated/unmanaged.
- Exact next step: open a real grouped Activities page and confirm detail headers such as Recitation/Assignment/Others now use the preferred yellow/gold color.

- Date: 2026-07-11
- Current pass: made Faculty Portal Activities grouped-header gradients more prominent; no commit or push.
- Completed work: darkened the component, subcomponent, and detail header gradient backgrounds and strengthened borders in `templates/faculty_portal/period_activities.html` so the grouped hierarchy is easier to distinguish. This is CSS-only; no behavior, calculations, URLs, permissions, or score data changed.
- Changed files in this pass: `templates/faculty_portal/period_activities.html`, `HANDOFF.md`.
- Validation performed: `python manage.py test apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_period_activities_grouped_view_is_default_and_uses_template_hierarchy` passed; `python manage.py check` passed; `git diff --check` passed with line-ending warnings only.
- Pending work / risk: browser visual smoke is still recommended to verify the darker gradients look balanced on the real page and on mobile. `logs/system.log` remains unrelated/unmanaged.
- Exact next step: open a real grouped Activities page and confirm the gradient is now prominent enough for Component, Subcomponent, and Detail headers.

- Date: 2026-07-11
- Current pass: increased top-level Faculty Portal Activities component header font size; no commit or push.
- Completed work: updated `templates/faculty_portal/period_activities.html` so only top-level Component group titles, such as Class Standing and Prelim Exam, render larger than subcomponent/detail labels. This is CSS-only and leaves grouping, actions, grades, permissions, and score data untouched.
- Changed files in this pass: `templates/faculty_portal/period_activities.html`, `HANDOFF.md`.
- Validation performed: `python manage.py test apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_period_activities_grouped_view_is_default_and_uses_template_hierarchy` passed; `python manage.py check` passed; `git diff --check` passed with line-ending warnings only.
- Pending work / risk: browser visual smoke is still recommended to confirm the larger component headers look balanced with the grouped gradients on desktop and mobile. `logs/system.log` remains unrelated/unmanaged.
- Exact next step: open a real grouped Activities page and confirm Class Standing / Prelim Exam are now prominent enough without making the nested labels feel cramped.

- Date: 2026-07-11
- Current pass: darkened Faculty Portal Activities grouped headers again and added subtle gradients; no commit or push.
- Completed work: updated `templates/faculty_portal/period_activities.html` so component, subcomponent, and detail group headers use slightly darker gradient backgrounds with stronger borders for clearer hierarchy scanning. This remains a CSS-only visual adjustment.
- Changed files in this pass: `templates/faculty_portal/period_activities.html`, `HANDOFF.md`.
- Validation performed: `python manage.py test apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_period_activities_grouped_view_is_default_and_uses_template_hierarchy` passed; `python manage.py check` passed; `git diff --check` passed with line-ending warnings only.
- Pending work / risk: browser visual smoke is still recommended to confirm the gradient strength feels right on the real Faculty Activities page and on mobile. `logs/system.log` remains unrelated/unmanaged.
- Exact next step: open a real grouped Activities page and adjust the gradient intensity only if the headers still do not separate clearly enough.

- Date: 2026-07-11
- Current pass: tightened Faculty Portal Activities grouped-header coloring so hierarchy levels are easier to distinguish; no commit or push.
- Completed work: darkened the grouped Activities section headers in `templates/faculty_portal/period_activities.html`: component headers now use a deeper green, subcomponent headers a slightly lighter green, and detail headers a muted violet with matching borders. No grouping logic, URLs, actions, grade calculations, permissions, or score data behavior changed.
- Changed files in this pass: `templates/faculty_portal/period_activities.html`, `HANDOFF.md`.
- Validation performed: `python manage.py test apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_period_activities_grouped_view_is_default_and_uses_template_hierarchy` passed; `python manage.py check` passed; `git diff --check` passed with line-ending warnings only.
- Pending work / risk: browser visual smoke is still recommended to confirm the darker hierarchy headers look right on desktop and mobile. `logs/system.log` remains unrelated/unmanaged.
- Exact next step: open a real Faculty Activities page with component/subcomponent/detail groups and confirm the darker headers improve scanability without feeling too heavy.

- Date: 2026-07-11
- Current pass: implemented grouped Faculty Portal Activities view for `/faculty/my-courses/<offering_id>/periods/<period_id>/activities/`; no commit or push.
- Completed work: replaced the default flat mixed activity list with grouped, Bootstrap-collapsible Component -> Subcomponent -> Detail sections. Component, subcomponent, and detail groups are expanded by default, use real `button` controls with `aria-expanded`/`aria-controls`, hide empty hierarchy branches, and show activity count plus encoded/expected score progress.
- Completed work: preserved the old hierarchy-column table under `?view=flat`, validated unknown view values back to grouped, preserved safe local `next` values, and dropped unsafe external `next` values from generated activity/view links.
- Completed work: kept grouping presentation-only. Grade calculations, activity IDs, score records, ownership, create/edit/delete/score URLs, CSRF, period lock/submission/GradeEncodingControl gates, and active-student/encoded-count meanings stay on existing service/query paths.
- Completed work: grouped view uses configured template order for components/subcomponents/details and oldest activity date then title/id within each leaf group; Flat View keeps the legacy newest-first activity list.
- Changed files: `apps/faculty_portal/views.py`, `templates/faculty_portal/period_activities.html`, `templates/faculty_portal/partials/activity_row_actions.html`, `apps/faculty_portal/tests_assignment_acceptance.py`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, `HANDOFF.md`.
- Validation performed: focused grouped/flat/view-state tests passed with 5 tests; `python manage.py test apps.faculty_portal.tests_activity_creation_selection` passed with 12 tests; `python manage.py test apps.faculty_portal.tests_grade_encoding_control` passed with 3 tests; targeted legacy page tests for quick jump and average-detail weight hiding passed with 2 tests; `python manage.py check` passed; `python manage.py makemigrations --check --dry-run` reported no changes; `git diff --check` passed with line-ending warnings only.
- Pending work / risk: browser smoke is still recommended for grouped collapse/expand, keyboard navigation, desktop/mobile readability, Flat View switching, safe `next` behavior, submitted/view-only periods, and score-entry/edit/delete links on real activity data. `logs/system.log` remains unrelated/unmanaged.
- Exact next step: browser-smoke a class with Quizzes Q1-Q5, Participation/Output -> Recitation/Assignment/Others, and Prelim Exam; confirm counts/progress, collapse controls, Flat View, mobile width, submitted-period read-only behavior, and action links.

- Date: 2026-07-11
- Current pass: final focused review and test hardening for the lightweight Faculty Feedback implementation; no new feature behavior, commit, or push.
- Completed work: expanded Faculty Feedback tests for cooldown scope, malformed/external/admin page-path sanitization, blank/unknown route fallback, audit metadata safety, and migration-directory source-file hygiene.
- Completed work: expanded Admin Faculty Feedback tests for Superadmin cross-tenant dashboard/export access, escaped XSS-like suggestion rendering, dashboard/export audit metadata safety, CSV formula-injection protection for `=`, `+`, `-`, and `@`, and CSV filter parity with dashboard tenant/campus/rating/date/feature/has-suggestion filters.
- Review fix: hardened `sanitize_relative_path()` so malformed URL-like input such as invalid IPv6 strings is rejected safely instead of raising during feedback submission.
- Migration directory confirmation: `apps/faculty_portal/migrations` contains only source files `0001_initial.py` and `__init__.py`; generated `__pycache__` was removed after tests recreated it.
- Changed files in this review pass: `apps/faculty_portal/feedback.py`, `apps/faculty_portal/tests_feedback.py`, `apps/admin_portal/tests_faculty_feedback.py`, `HANDOFF.md`.
- Validation performed: `python manage.py test apps.faculty_portal.tests_feedback apps.admin_portal.tests_faculty_feedback` passed with 22 tests; `python manage.py test apps.admin_portal.tests_repair_seeded_rbac_navigation` passed with 7 tests; `python manage.py test apps.admin_portal.tests_help_guide` passed with 15 tests; `python manage.py test apps.faculty_portal.tests_help_guide` passed with 4 tests; `python manage.py check` passed; `python manage.py makemigrations --check --dry-run` reported no changes; `git diff --check` passed with line-ending warnings only.
- Pending work / risk: browser smoke remains the same as the implementation pass: desktop/mobile Faculty Portal modal placement and submission behavior; Admin Portal menu visibility, dashboard filters, scoped CSV export, denied access, and audit rows as Superadmin, Tenant Admin, Campus Admin, Faculty, and an unauthorized role. `logs/system.log` remains unrelated/unmanaged.
- Exact next step: perform the browser smoke matrix before production rollout; then deploy the three feedback migrations and run server-side `python manage.py check`, `showmigrations`, and a scoped dashboard/export verification.

- Date: 2026-07-11
- Current pass: implemented lightweight Faculty Feedback for authenticated Faculty Portal pages plus scoped Admin Portal review/export.
- Completed work: added `FacultyFeedback` with tenant/campus/user binding, Happy/Neutral/Sad stable rating values, optional trimmed 500-character suggestion, sanitized relative page context, route-derived feature code, user-agent summary, indexes, and migration `faculty_portal.0001_initial`.
- Completed work: added `/faculty/feedback/submit/` as a POST-only CSRF-protected JSON endpoint behind Faculty Portal access; server derives faculty user, tenant, and campus from the authenticated request and blocks rapid same-page submissions for five minutes.
- Completed work: updated `templates/faculty_portal/base.html` so authenticated Faculty Portal pages show a compact floating `Feedback` button above the existing `Faculty Quick Guide`; the accessible dialog supports rating selection, optional suggestion, live character count, AJAX submit, disabled submit during request, thank-you state, Escape close, and focus return.
- Completed work: added Admin Portal `Tools -> Faculty Feedback` at `/admin-portal/tools/faculty-feedback/` with `faculty_feedback.read`/`faculty_feedback.export`, RBAC and navigation migrations, seed/repair-command support, scoped filters, summary counts, pagination, escaped suggestion display, CSV export with same filters/scope, formula-injection protection, no-cache download headers, and audit events for dashboard access/export.
- Completed work: documented the feature in Faculty/Admin guide data, `CHANGE_LOG.md`, and `TEACHERMATEPLUS_CONTEXT.md`.
- Changed files: `apps/faculty_portal/models.py`, `apps/faculty_portal/migrations/0001_initial.py`, `apps/faculty_portal/forms.py`, `apps/faculty_portal/feedback.py`, `apps/faculty_portal/views.py`, `apps/faculty_portal/urls.py`, `apps/faculty_portal/help_guide.py`, `templates/faculty_portal/base.html`, `apps/faculty_portal/tests_feedback.py`, `apps/admin_portal/views.py`, `apps/admin_portal/urls.py`, `apps/admin_portal/help_guide.py`, `templates/admin_portal/tools/faculty_feedback.html`, `apps/admin_portal/tests_faculty_feedback.py`, `apps/rbac/migrations/0025_seed_faculty_feedback_permissions.py`, `apps/navigation/migrations/0012_seed_faculty_feedback_menu.py`, `apps/core/management/commands/seed_stage_0_1.py`, `apps/admin_portal/management/commands/repair_seeded_rbac_navigation.py`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, `HANDOFF.md`.
- Validation performed: `python manage.py test apps.faculty_portal.tests_feedback apps.admin_portal.tests_faculty_feedback` passed with 15 tests; `python manage.py test apps.admin_portal.tests_repair_seeded_rbac_navigation` passed with 7 tests; `python manage.py test apps.admin_portal.tests_help_guide` passed with 15 tests; `python manage.py test apps.faculty_portal.tests_help_guide` passed with 4 tests; `python manage.py check` passed before and after migration; `python manage.py makemigrations --check --dry-run` reported no changes; `python manage.py migrate --plan` showed `faculty_portal.0001_initial`, `rbac.0025_seed_faculty_feedback_permissions`, and `navigation.0012_seed_faculty_feedback_menu`; `python manage.py migrate` applied those three migrations successfully; `python manage.py showmigrations faculty_portal rbac navigation | Select-String -Pattern "0001|0025|0012"` showed the new migrations applied; `git diff --check` passed with line-ending warnings only.
- Pending work / risk: browser smoke is still recommended for desktop and mobile Faculty Portal pages to visually confirm the Feedback button sits above Faculty Quick Guide and does not cover page controls; also smoke Admin Portal `Tools -> Faculty Feedback` as Superadmin, Tenant Admin, Campus Admin, Faculty, and an unauthorized role to confirm menu visibility, denied access, filters, and CSV download. `logs/system.log` was already dirty before this pass and remains unrelated/unmanaged.
- Exact next step: browser-smoke the Faculty Feedback modal from Dashboard/My Classes/Score Encoding, submit Happy/Neutral/Sad scenarios, then verify Admin Portal dashboard counts, scope filters, CSV formula escaping, and audit rows before deploying the three migrations to production.

- Date: 2026-07-11
- Current pass: implemented and post-reviewed Admin Portal `Secure Tenant Data Export` for authorized Superadmin and Tenant Admin users.
- Completed work: added `TenantDataExportChallenge`, migration `accounts.0008_tenantdataexportchallenge`, password verification, short-lived email OTP, resend cooldown/limits, OTP attempt lockout, one-time challenge consumption, audited page/password/OTP/export events, and no-store streamed SQLite download.
- Completed work: added explicit tenant-scoped SQLite export allowlist with manifest, row counts, exclusions, sanitized `users.password` values, file-name safe tenant code, temporary-file deletion on response close, and tenant isolation coverage.
- Completed work: added the Admin Portal page, forms, URL, email templates, RBAC permission `tenant_data_export.execute`, Tools navigation item `TENANT_DATA_EXPORT`, seed/repair-command support, Admin guide entry, changelog, and context documentation.
- Review fixes: narrowed Admin Portal challenge exception handling to expected `PermissionDenied` paths, re-raised unexpected export-generation failures after secure cleanup/audit logging, documented one-time challenge consumption semantics in the export service, sanitized absolute file-field names to basenames, added explicit password-success/challenge-start/download-initiation audit events, and expanded tests for pre-verification download blocking, one-time service consumption, resend maximum, SQLite header/integrity, API-key exclusion, audit secret checks, and generation-failure cleanup.
- Changed files: `apps/accounts/models.py`, `apps/accounts/migrations/0008_tenantdataexportchallenge.py`, `apps/admin_portal/tenant_data_export.py`, `apps/admin_portal/forms.py`, `apps/admin_portal/views.py`, `apps/admin_portal/urls.py`, `apps/admin_portal/help_guide.py`, `apps/admin_portal/tests_tenant_data_export.py`, `apps/rbac/migrations/0024_seed_tenant_data_export_permission.py`, `apps/navigation/migrations/0011_seed_tenant_data_export_menu.py`, `apps/core/management/commands/seed_stage_0_1.py`, `apps/admin_portal/management/commands/repair_seeded_rbac_navigation.py`, `templates/admin_portal/tools/tenant_data_export.html`, `templates/admin_portal/emails/tenant_data_export_otp.txt`, `templates/admin_portal/emails/tenant_data_export_otp.html`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, `HANDOFF.md`.
- Validation performed: `python manage.py test apps.admin_portal.tests_tenant_data_export` passed with 15 tests after review expansion; `python manage.py check` passed before and after documentation updates; `python manage.py makemigrations --check --dry-run` reported no changes; `python manage.py test apps.admin_portal.tests_repair_seeded_rbac_navigation` passed with 7 tests; `python manage.py test apps.admin_portal.tests_help_guide` passed with 15 tests; `python manage.py migrate --plan` showed the three new migrations; `python manage.py showmigrations accounts rbac navigation | Select-String -Pattern "0008|0024|0011"` showed the three new migrations before apply and `[X]` after apply; `python manage.py test apps.accounts.tests_login_otp` passed with 4 tests; `python manage.py migrate` applied the three new migrations successfully; `git diff --check` passed with line-ending warnings only.
- Pending work / risk: browser smoke is still recommended for `Admin Portal -> Tools -> Secure Tenant Data Export` as Superadmin, Tenant Admin, Campus Admin, and Faculty to confirm menu visibility, denied access, page layout, email OTP delivery, and one-time download behavior in the browser. The pasted production `/opt/teachermateplus` error `ModuleNotFoundError: No module named 'config'` appears to be a deployment/path/PYTHONPATH issue because the local repo has `config/` and local Django checks pass.
- Exact next step: deploy the code including `config/`, run the three migrations on the server from the real app root, then verify `python -c "import config; print(config.__file__)"`, `python manage.py check`, `python manage.py showmigrations accounts rbac navigation`, and a browser export smoke with an account that has a registered email address.

- Date: 2026-07-10
- Current pass: implemented Faculty Portal activity creation hierarchy retention for `/faculty/my-courses/<offering_id>/periods/<period_id>/activities/`.
- Completed work: successful create POSTs now store the last Component/Subcomponent/Detail IDs in Django session key `faculty_activity_last_selection:<faculty_user_id>:<offering_id>:<period_id>` after the activity is created; redirected GETs validate the stored IDs against the current active period hierarchy before restoring them through the existing dependent selector JSON.
- Completed work: Activity Title, Total Items, and Date remain blank after redirect; invalid POSTs keep bound form values/errors and do not overwrite the last successful stored hierarchy; stale component/subcomponent/detail IDs are safely cleared from parent to child.
- Completed work: preserved existing PRG behavior and existing create/edit service gates, including faculty assignment scope, period locks, GradeEncodingControl closure, and CSRF; safe local `next` query values are preserved on the form action/redirect while unsafe external values are dropped.
- Changed files: `apps/faculty_portal/views.py`, `templates/faculty_portal/period_activities.html`, `apps/faculty_portal/tests_activity_creation_selection.py`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, `HANDOFF.md`.
- Validation performed: `python manage.py test apps.faculty_portal.tests_activity_creation_selection` passed with 12 tests; targeted existing tests `apps.faculty_portal.tests_grade_encoding_control.FacultyGradeEncodingControlNoticeTests.test_direct_activity_post_is_blocked_when_encoding_closed` and `apps.grading.tests.GradeEncodingAccessControlTests.test_create_activity_is_blocked_when_control_is_closed` passed; `python manage.py check` passed; `python manage.py makemigrations --check --dry-run` reported no changes; `git diff --check` passed with line-ending warnings only.
- Pending work / risk: browser smoke is still recommended on the Faculty Activities page to visually confirm the JavaScript-restored selectors after saving Component-only, Component+Subcomponent, and Component+Subcomponent+Detail activities. `logs/system.log` was already dirty before this pass and remains unrelated/unmanaged.
- Exact next step: in a browser, create multiple activities under Class Standing -> Quizzes and Class Standing -> Participation/Output -> Recitation, confirm the hierarchy stays selected while title/total/date clear, then refresh the redirected GET and confirm no duplicate activity appears.

- Date: 2026-06-26
- Current pass: implemented configurable Correction Petition Window Policy for new correction requests. Faculty submissions now honor an admin-defined open/open-until/closed window, the faculty correction page shows the active policy state, and the Admin correction governance page can create/edit/delete policy rows.
- Current pass: kept the existing Area Chair -> College Dean -> CAO / Area Chair -> CAO correction route flow intact, added regression coverage for duplicate policy scopes, days-after-deadline blocking, faculty POST blocking, and admin governance page rendering, and added the policy migration.
- Validation performed: `python manage.py check` passed; `python manage.py makemigrations --check --dry-run` passed; `python manage.py test apps.grading.tests.CorrectionWorkflowTests` passed with 37 tests.
- Pending work / risk: `logs/system.log` was already dirty from prior work and was left untouched; browser smoke is still the only remaining check if you want visual confirmation of the admin governance policy card and the faculty correction banner.
- Current pass: implemented ordered approval-step support for Petition/Application for Correction of Grades routes, allowing department routes such as Area Chair -> College Dean -> CAO or Area Chair -> CAO while preserving existing direct/two-step route rows.
- Current pass: added `CorrectionApprovalRouteStep` with migration/backfill, updated correction route resolution to prefer offering/course/section/program department before faculty default department, wired current-step/final-decision emails with audit-log dedupe, and exposed current progress/review history on faculty/admin correction pages.
- Post-implementation review fixes: duplicate ordered approver roles are now rejected by the governance form; `review_correction_request()` refetches/locks the request before mutating it to reject stale/repeated approvals; approval authorization and current-step email recipients now honor department-scoped `UserRole` assignments; admin review now warns when next-step/faculty decision email has no recipient.
- Validation performed: `python manage.py check` passed; `python manage.py makemigrations --check --dry-run` passed; `python manage.py migrate` applied `grading.0031_correctionapprovalroutestep` and an existing pending `rbac.0023_merge_20260623_1029`; final `python manage.py test apps.grading.tests.CorrectionWorkflowTests` passed with 31 tests; relevant admin/faculty/scope/RBAC/config/guide/anomaly batch passed with 77 tests; `python manage.py test apps.grading.tests.FinalGradeFormulaTests` passed with 17 tests.
- Pending work / risk: browser smoke is still recommended for Admin Portal -> Tools -> Correction Governance route editing, Admin Portal -> Grade Correction Requests review handoff, and Faculty Portal -> Correction Requests progress display. `logs/system.log` was already dirty before this pass and remains unrelated/unmanaged.

## Correction of Grades Ordered Approval Routes
- Completed work: introduced ordered route-step rows and backfilled existing correction approval route rules so legacy direct/two-step routes keep working.
- Completed work: updated the route governance form to save Step 1, optional Step 2, and Final approver roles, enabling Area Chair -> College Dean -> CAO and Area Chair -> CAO configurations without hardcoding one tenant-wide path.
- Completed work: updated correction request initialization/reconciliation so the governing department resolves from the offering/course/section/program context first, then the faculty default department, and still uses exact department, parent department, tenant default, then safe fallback route selection.
- Completed work: kept existing role/same-department/superadmin/on-behalf review guards and final-only grade application behavior, while adding progress helpers for pending approver labels and review history.
- Completed work: replaced broad submission approval email calls in create flows with current-step approver notification, added next-step notification after intermediate approval, and added faculty final-decision notification after approval/rejection, all behind the existing correction email feature setting with audit-log dedupe keys.
- Review fixes: tightened ordered route validation against duplicate approver roles, locked/refetched correction requests inside service review, aligned approver and email scoping with department-scoped role assignments, and added warning messages for missing next-step/faculty email recipients.
- Changed files: `apps/grading/models.py`, `apps/grading/migrations/0031_correctionapprovalroutestep.py`, `apps/grading/services.py`, `apps/grading/notifications.py`, `apps/grading/tests.py`, `apps/admin_portal/forms.py`, `apps/admin_portal/views.py`, `apps/faculty_portal/views.py`, `templates/admin_portal/tools/correction_governance.html`, `templates/admin_portal/grading/correction_request_list.html`, `templates/admin_portal/grading/correction_request_review.html`, `templates/faculty_portal/period_corrections.html`, `templates/admin_portal/guide.html`, `templates/faculty_portal/guide.html`, `templates/faculty_portal/guide_manual.html`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, `HANDOFF.md`.
- Validation performed: `python manage.py check` passed; `python manage.py makemigrations --check --dry-run` passed; `python manage.py migrate` passed; `python manage.py test apps.grading.tests.CorrectionWorkflowTests` passed with 31 tests; `python manage.py test apps.admin_portal.tests_scope apps.admin_portal.tests_reopen_requests apps.faculty_portal.tests_dashboard_updates.FacultyDashboardUpdatesTests.test_correction_approval_and_rejection_after_previous_login_appear apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_configurable_features_can_store_assignment_workflow_settings apps.admin_portal.tests_users.UserRolePermissionSeparationTests apps.admin_portal.tests_roles apps.admin_portal.tests_help_guide apps.faculty_portal.tests_help_guide apps.admin_portal.tests_governance_anomalies.GovernanceAnomalyDetectionTests.test_correction_fail_to_pass_and_final_grade_flags` passed with 77 tests; `python manage.py test apps.grading.tests.FinalGradeFormulaTests` passed with 17 tests.
- Exact next step: browser-smoke creating a correction route with Step 1 Area Chair, Step 2 College Dean, Final CAO; submit a faculty correction; approve as Area Chair, then Dean, then CAO; confirm faculty progress labels, audit logs, final grade application, and email behavior.

- Date: 2026-06-22
- Current pass: split Admin Portal Faculty Assignment management from academic monitoring access. Campus Admin can still open/manage `/admin-portal/academics/faculty-assignments/`, but the Activity Monitor, Final Clearance, Grade Book Monitor, and Prediction buttons/routes now require separate monitor/report permissions.
- Current pass: added `faculty_activity_monitor.read`, `faculty_gradebook_monitor.read`, and `grade_prediction_monitor.read`, narrowed default academic-monitor grants to AC/Area Chair, Dean/College Dean, CAO, and Superadmin-style oversight, and removed monitor/report grants from Campus Admin/Tenant Admin/Registrar through the new RBAC migration.
- Validation performed: `python manage.py migrate` applied `rbac.0021_seed_faculty_monitor_permissions` and `navigation.0010_update_faculty_monitor_menu_permissions`; `python manage.py check` passed; `python manage.py makemigrations --check --dry-run` passed; focused Faculty Assignment monitor access tests passed; focused Faculty Final Clearance tests passed.
- Pending work / risk: authenticated browser smoke testing is still recommended for `/admin-portal/academics/faculty-assignments/` as Campus Admin and as AC/Dean/CAO to visually confirm button visibility and menu visibility.

## Faculty Assignment Academic Monitor Access Split
- Completed work: kept `faculty_assignments.read/create/update` for assignment management while moving Activity Monitor, Grade Book Monitor, and Prediction Monitor routes to dedicated read permissions; Final Clearance continues to use `faculty_final_clearance.read`.
- Completed work: hid Faculty Assignment page monitor buttons and Admin Dashboard quick-action monitor links unless the current user has the matching monitor/report permission; also gated cross-links from Activity Monitor and Grade Book Monitor.
- Completed work: added RBAC and navigation migrations so `FACULTY_ACTIVITY_MONITOR` uses `faculty_activity_monitor.read`, Final Clearance stays on `faculty_final_clearance.read`, and Campus Admin/Tenant Admin/Registrar lose default monitor/report grants while AC/Area Chair, Dean/College Dean, CAO, and Superadmin-style roles receive them.
- Changed files: `apps/rbac/migrations/0021_seed_faculty_monitor_permissions.py`, `apps/navigation/migrations/0010_update_faculty_monitor_menu_permissions.py`, `apps/admin_portal/views.py`, `apps/core/services/features.py`, `apps/core/management/commands/seed_stage_0_1.py`, `templates/admin_portal/academics/faculty_assignment_list.html`, `templates/admin_portal/academics/faculty_activity_monitor.html`, `templates/admin_portal/academics/faculty_gradebook_monitor.html`, `templates/admin_portal/dashboard.html`, `apps/admin_portal/tests_assignment_acceptance.py`, `apps/admin_portal/tests_scope.py`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, `HANDOFF.md`.
- Validation performed: `python manage.py test apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_campus_admin_assignment_page_hides_academic_monitor_actions apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_campus_admin_assignment_permission_does_not_open_academic_monitors apps.admin_portal.tests_scope.FacultyMonitoringScopeTests.test_faculty_activity_monitor_surfaces_login_activity_and_gradebook_work apps.admin_portal.tests_scope.FacultyMonitoringScopeTests.test_gradebook_monitor_masks_student_identity_and_logs_view` passed with 4 tests.
- Validation performed: `python manage.py test apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_faculty_final_clearance_preview_shows_complete_and_incomplete_courses apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_faculty_final_clearance_admin_post_is_preview_only apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_faculty_final_clearance_verify_view_displays_generated_snapshot apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_faculty_final_clearance_lookup_finds_report_by_reference_and_code apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_faculty_final_clearance_lookup_rejects_invalid_code apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_faculty_final_clearance_marks_zero_active_students_as_incomplete` passed with 6 tests.
- Validation performed: `python manage.py migrate` passed and applied the new RBAC/navigation migrations; `python manage.py check` passed; `python manage.py makemigrations --check --dry-run` reported no changes detected.
- Exact next step: browser smoke `/admin-portal/academics/faculty-assignments/` with a Campus Admin account and confirm only assignment-management actions remain; then smoke with an AC/Dean/CAO account and confirm the academic monitor buttons appear and open within scope.

- Date: 2026-06-22
- Current pass: added the Django backend foundation for the future online-only Flutter Faculty Companion App in `apps/mobile_api`, mounted at `/api/mobile/v1/`, without changing the Faculty Portal web UI.
- Current pass: mobile endpoints reuse existing faculty assignment scope, campus permission scope, grading services, grade explanation service, submission readiness, encoding lock enforcement, validation, and audit patterns; Flutter remains UI-only and never connects directly to the database.
- Validation performed: `python manage.py check` passed; `python manage.py test apps.mobile_api.tests` passed with 10 tests.
- Date: 2026-06-23
- Current pass: split Admin Portal user-role assignment from role-definition management by adding dedicated `user_roles.update` RBAC permission for `Security -> Users -> Roles`.
- Current pass: `Security -> Users -> Roles` now requires `user_roles.update`, while `Security -> Roles` and role-permission editing remain under `roles.update`.
- Validation performed: `python manage.py migrate` applied `rbac.0022_seed_user_role_assignment_permission`; `python manage.py check` passed; `python manage.py makemigrations --check --dry-run` reported no changes; `python manage.py test apps.admin_portal.tests_users apps.admin_portal.tests_roles` passed with 27 tests.

## User Role Assignment Permission Split
- Completed work: added RBAC migration `apps/rbac/migrations/0022_seed_user_role_assignment_permission.py` to seed `user_roles.update` and copy existing `roles.update` role/user grants into the new permission for rollout continuity.
- Completed work: updated the `user_roles_view` gate and the Users table `Manage roles` action to use `user_roles.update` instead of `roles.update`.
- Completed work: kept role list, role edit, role delete, and role-permission maintenance under `roles.update`, and documented the separation in `docs/ROLE_SETUP.md` and the Admin guide.
- Validation performed: `python manage.py migrate`, `python manage.py check`, `python manage.py makemigrations --check --dry-run`, and `python manage.py test apps.admin_portal.tests_users apps.admin_portal.tests_roles` all passed.
- Changed files: `apps/rbac/migrations/0022_seed_user_role_assignment_permission.py`, `apps/admin_portal/views.py`, `templates/admin_portal/security/user_table.html`, `apps/core/management/commands/seed_stage_0_1.py`, `apps/admin_portal/tests_users.py`, `apps/admin_portal/tests_roles.py`, `docs/ROLE_SETUP.md`, `templates/admin_portal/guide.html`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, `HANDOFF.md`.
- Pending work / risk: existing production custom roles that currently rely on `roles.update` for user-role assignment will keep working after migration, but follow-up RBAC cleanup may still be needed so operational roles keep `user_roles.update` and drop `roles.update` where appropriate.
- Exact next step: run the migration in each environment, then review operational roles such as Campus Admin and grant `user_roles.update` without `roles.update` where role-permission editing should stay restricted.
- Date: 2026-06-25
- Current pass: implemented grading-template governance safety fixes for approved/published template structural locking and exact-term Course Template Assignment override protection.
- Current pass: approved/published templates are now read-only in the builder and blocked in period/component/subcomponent/detail structural routes, while duplicate and hotfix workflows remain available.
- Current pass: exact-term course-template assignment creation/reactivation now blocks duplicate active scopes and skips/blocks dangerous overrides when matching offerings already have grading, submission, reopen, lock, or attendance dependencies under a different currently resolved template.
- Validation performed: `python manage.py check`, `python manage.py makemigrations --check --dry-run`, full `apps.admin_portal.tests_template_governance.TemplateGovernanceWorkflowTests`, course-template assignment safety/bulk/list tests, `apps.grading.tests.FinalGradeFormulaTests`, and focused faculty grading tests all passed.

## Grading Template Governance Safety Fixes
- Completed work: tightened `GradingTemplateService.ensure_editable()` behind `is_structurally_editable()` so `FOR_APPROVAL`, `APPROVED`, and published templates are locked from normal structural editing.
- Completed work: wired the lock through builder UI, period/component/subcomponent/detail create/update paths, component soft delete, and inactive hard-delete cleanup for template structure rows.
- Completed work: added exact-term Course Template Assignment activation checks that reject duplicate active course/effective-term scopes and block/skip late exact-term overrides over existing gradebook-dependent offerings.
- Completed work: clarified Course Template Assignment list/bulk/edit UI text for default/null-term assignments, exact-term overrides, and separate Summer templates.
- Safety confirmations: no grade computation logic, faculty score/attendance save path, submissions, locks, corrections, or existing grade records were modified.
- Changed files: `apps/grading/services.py`, `apps/grading/admin.py`, `apps/grading/tests.py`, `apps/admin_portal/forms.py`, `apps/admin_portal/views.py`, `apps/admin_portal/tests_template_governance.py`, `apps/admin_portal/tests_course_template_assignment_safety.py`, `apps/admin_portal/tests_course_template_assignment_bulk.py`, `templates/admin_portal/grading/template_builder.html`, `templates/admin_portal/grading/course_template_assignment_list.html`, `templates/admin_portal/grading/course_template_assignment_bulk_form.html`, `templates/admin_portal/shared/form_page.html`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, `HANDOFF.md`.
- Validation performed: `python manage.py check` passed; `python manage.py makemigrations --check --dry-run` passed; `python manage.py test apps.admin_portal.tests_template_governance.TemplateGovernanceWorkflowTests` passed with 20 tests; `python manage.py test apps.admin_portal.tests_course_template_assignment_safety.CourseTemplateAssignmentSafetyTests apps.admin_portal.tests_course_template_assignment_bulk.BulkCourseTemplateAssignmentTests` passed with 19 tests; `python manage.py test apps.admin_portal.tests_course_template_assignment_list.CourseTemplateAssignmentListTests` passed with 4 tests; `python manage.py test apps.grading.tests.FinalGradeFormulaTests` passed with 17 tests; focused faculty grading tests passed with 4 tests.
- Pending work / risk: browser smoke is still recommended for the builder read-only alert and Course Template Assignment guidance/skip warning display.
- Exact next step: in the browser, open an approved/published template builder and confirm Add/Edit structural actions are hidden; then attempt a Summer exact-term bulk assignment for a no-data offering and an in-use offering to confirm allowed vs skipped messaging.

- Current pass: updated Admin help guidance so regular/default and Summer course-template assignments are explained in plain language.
- Current pass: guide text now says to leave `Effective term` blank for the regular default template, add a separate exact Summer-term row for the Summer template, and rely on the default regular assignment again after Summer.
- Validation performed: `python manage.py check` passed; focused Admin guide regression test passed; full `apps.admin_portal.tests_help_guide` suite passed with 15 tests.

## Admin Guide Regular/Summer Template Assignment Guidance
- Completed work: updated the dedicated Grading Template Setup Guide with a simple Regular/Summer assignment example table.
- Completed work: updated the practical Admin guide Course Template Assignment topic with the same default-vs-exact-term rule.
- Completed work: added guide-content regression assertions so the plain explanation stays visible.
- Changed files: `templates/admin_portal/grading/grading_setup_guide.html`, `apps/admin_portal/help_guide.py`, `apps/admin_portal/tests_help_guide.py`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, `HANDOFF.md`.
- Validation performed: `python manage.py check` passed; `python manage.py test apps.admin_portal.tests_help_guide.AdminHelpGuideTests.test_grading_template_help_names_exact_menu_and_builder_steps` passed; `python manage.py test apps.admin_portal.tests_help_guide` passed with 15 tests.
- Pending work / risk: browser smoke is optional; this is documentation-only and does not change template resolution, grade computation, database models, or assignments.
- Exact next step: open `Admin Portal -> Grading -> Grading Template Setup Guide` and `Admin Portal -> Guide` to visually confirm the new explanation reads clearly.

## Grading Template Builder State Preservation
- Completed work: added stable builder DOM anchors for periods, major components, subcomponents, and detail items.
- Completed work: added a template-specific `sessionStorage` restore script using key `gradingTemplateBuilderState:<template_id>` to save opened Bootstrap collapse IDs, scroll position, and pending focus target.
- Completed work: wired Add/Edit builder links with `data-builder-action-link` and `data-builder-focus-target` so returning from form pages can reopen the related period and scroll near the relevant area.
- Changed files: `templates/admin_portal/grading/template_builder.html`, `apps/admin_portal/tests_template_governance.py`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, `HANDOFF.md`.
- Validation performed: `python manage.py check` passed; `python manage.py makemigrations --check --dry-run` passed; focused builder regression test passed; full `apps.admin_portal.tests_template_governance` suite passed.
- Pending work / risk: manual browser smoke test is still recommended because sessionStorage, Back-button restoration, and scroll behavior are browser-side concerns.
- Exact next step: open a builder page, expand a period, scroll to a component/subcomponent/detail, use Add/Edit, save, and confirm the builder returns near the same area with the period still open.

## Average Activities Publish Validation Alignment
- Completed work: updated `GradingTemplateService.validate_publishable()` so active detail rows under `Average Activities` no longer require a positive total configured detail weight for template publication.
- Completed work: kept the existing positive active detail-weight validation for `Weighted Details`.
- Completed work: added regression coverage proving all-zero detail weights pass validation in `Average Activities` mode and still fail in `Weighted Details`, and updated the Admin grading setup guide note.
- Changed files: `apps/grading/services.py`, `apps/faculty_portal/tests_assignment_acceptance.py`, `templates/admin_portal/grading/grading_setup_guide.html`, `apps/admin_portal/tests_help_guide.py`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, `HANDOFF.md`.
- Pending work / risk: browser smoke verification of the grading setup guide text is still optional; no backend grading-risk follow-up is expected because computation paths were not changed.
- Exact next step: if needed, open `Admin Portal -> Grading -> Grading Setup Guide` and confirm the Average Activities help card now matches the live validation rule.

## Faculty Companion Mobile API Foundation
- Completed work: added `apps/mobile_api` with JSON response helpers and endpoints for auth/login/logout/me, dashboard, notifications, assigned classes, class snapshot, students/search, student summary, consultation summary, grade explanation, attendance today/save, quick activity options/create, activity scores/save, missing scores, and submission readiness.
- Security confirmations: all non-login endpoints require authenticated faculty access, class reads/writes verify accepted faculty assignment plus scoped faculty permission, student endpoints verify enrollment in the requested class, score writes validate range and preserve blank versus zero using the existing clear/write service path, and attendance writes validate status before calling existing services.
- Lock/audit confirmations: mobile score, attendance, and activity writes go through existing `FacultyGradingService` and `GradingGovernanceService.assert_encoding_allowed()` paths; write operations add audit rows through `AuditService` with `metadata_json.source = mobile_api`.
- Changed files: `apps/mobile_api/__init__.py`, `apps/mobile_api/apps.py`, `apps/mobile_api/urls.py`, `apps/mobile_api/views.py`, `apps/mobile_api/tests.py`, `config/settings/base.py`, `config/urls.py`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, `HANDOFF.md`.
- Pending work / risk: authentication is session-cookie based for the MVP foundation; production Flutter integration still needs a final decision on CSRF/token transport, HTTPS/mobile client storage, and deployment hardening. Offline mode, final grade submission, correction requests, admin functions, parent/student access, and AI suggestions were intentionally deferred.
- Exact next step: have the Flutter app call `/api/mobile/v1/auth/login/`, then `/api/mobile/v1/auth/me/` and `/api/mobile/v1/classes/` against a dev backend, and decide whether the mobile client will use session+CSRF or a token layer in a later security pass.

- Date: 2026-06-21
- Current pass: refined the Faculty Portal class list change request panel so it now sits below the class master list, uses a gray recent-requests header, lets faculty remove pending requests, and excludes already-enrolled students from the add-request matched-student selector.
- Current pass: converted the Faculty Portal class list change request add/remove/cancel actions to AJAX so the request area refreshes in place without reloading the page.
- Current pass: added admin-editable `grade_column_label` support for grading template periods so the faculty/admin grade summary header can be customized per period while the overall `FINAL GRADE` column stays unchanged.
- Current pass: updated the Faculty Portal period summary table so the current-period column shows `FINAL EXAM` for the final period while prior period headers can also use admin-set labels such as PG, MG, and PFG, with the computed overall `FINAL GRADE` unchanged.
- Current pass: updated the printable final-period report of grades so it follows the grading template labels as well, showing the configured PRELIM, MIDTERM, PRE-FINAL, final-period, and `FINAL GRADE` columns.
- Current pass: aligned the grade explanation modal with the same periodic label rules so the final-period explain window uses `FINAL EXAM` or the admin-configured label without duplicated `Grade` wording, and the final-grade breakdown keeps the display labels in the formula/detail cards.
- Current pass: refreshed the Faculty Portal Activities taxonomy badges with a more vibrant palette, while keeping icon-only row actions and the `Grade Summary` quick-jump label.
- Current pass: updated the Faculty Portal Activities table with icon-only action controls, distinct component/subcomponent/detail badges, no `Entry Method` column, and a `Grade Summary` quick-jump label.
- Current pass: added Generate Password and branded credential email support to Admin Portal user password resets.
- Current pass: removed reference-only configured detail weights from the Faculty Portal activity score encoding notice for Average Activities.
- Current pass: implemented frontend-only Phase 1 Excel-like Quick Encoding for the existing Faculty Portal activity score page behind `FEATURE_FACULTY_QUICK_SCORE_ENCODING`.
- Current pass: improved mobile responsiveness on the Faculty Portal activity score encoding, activity list, attendance encoding, and attendance summary pages so small screens keep the workflow usable without changing backend behavior.
- Current pass: updated the public Faculty Portal landing page to explicitly name Performance Trends, Class Performance, and Student Consultation.
- Session focus: Implemented the Faculty Portal `Updates Since Your Last Visit` dashboard card, using prior successful login audit rows as the anchor and scoped class updates as the read-only feed.
- Follow-up change: moved the `Updates Since Your Last Visit` card directly below the grade submission deadline banner on the Faculty Dashboard.
- Follow-up change: increased the bottom spacing on the `Updates Since Your Last Visit` card so it sits farther from the Grade Encoding Status and Pending Grade Issues panels.
- Follow-up change: added a contextual `?` help callout to the `Updates Since Your Last Visit` card header.
- Follow-up change: replaced the Faculty Portal floating `?` guide button with a small `teacher_star.png` icon.
- Review note: Faculty Dashboard `Updates Since Last Visit` was implemented and reviewed. The card shows the latest 5 scoped updates since the faculty member's previous successful login, uses existing `AuditLog` `LOGIN_SUCCESS` rows as the anchor, requires no migration, and does not add a new notification module.
- Current branch: main
- Current environment: Windows PowerShell workspace at `D:\teachermateplus`; Django apps-based project using SQLite for development.

## Class List Change Request Workflow
- Completed work: added Faculty Portal request-add/request-remove controls for the existing class master list page, with recent request history and campus-scoped admin review.
- Completed work: added Campus Admin and Superadmin review pages, plus safe approval/rejection handling that uses the existing enrollment create/deactivate services instead of letting faculty mutate the class list directly.
- Completed work: hid cancelled class list change requests from the Admin Portal queue so faculty-requester cancellations no longer appear in the review list.
- Completed work: made the Faculty Portal class list change request forms post to an explicit enrollment URL so the AJAX buttons do not depend on browser fallback resolution and should avoid the 404 path.
- Validation performed: `python.exe manage.py check` passed; `python.exe manage.py test apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_faculty_can_submit_add_request_via_ajax_without_page_refresh apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_faculty_can_submit_remove_request_via_ajax_without_page_refresh apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_faculty_can_cancel_pending_request_via_ajax_without_page_refresh apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_faculty_can_remove_pending_class_list_change_request_from_recent_requests apps.admin_portal.tests_class_list_change_requests` passed with 12 tests.
- Changed files: `templates/faculty_portal/partials/class_list_change_requests_area.html`, `templates/faculty_portal/offering_enrollment.html`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, `HANDOFF.md`.
- Pending work / risk: if cancelled requests should still be available in an audit-only screen, that would need a separate explicit UI.
- Exact next step: decide whether cancelled class list change requests should stay hidden everywhere or be moved to a separate audit-only admin filter.

## Faculty Activities Table UI Cleanup
- Completed work: replaced visible `Encode Scores`, `Edit`, and `Delete` row action text in `templates/faculty_portal/period_activities.html` with compact icon-only controls while retaining titles, aria labels, and visually hidden labels for accessibility.
- Completed work: rendered component, subcomponent, and detail values as distinct vibrant badges, removed the activities table `Entry Method` column, and renamed the shared period quick-jump `Summary` label to `Grade Summary`.
- Safety confirmations: no backend activity saving, score saving, grading computation, attendance, enrollment, submissions, locks, corrections, routes, models, or migrations were changed.
- Changed files: `templates/faculty_portal/period_activities.html`, `templates/faculty_portal/partials/period_quick_nav.html`, `apps/faculty_portal/tests_assignment_acceptance.py`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, `HANDOFF.md`.
- Validation performed: `python.exe manage.py check` passed; `python.exe manage.py test apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_average_activity_detail_weight_is_hidden_on_activity_page` passed.

## Faculty Period Summary Label Fix
- Completed work: added a display-only `period_grade_header_label` context value in the faculty period summary view and switched the summary template to use it, so final periods show `FINAL EXAM` while normal periods still show `<PERIOD NAME> GRADE`.
- Completed work: added admin-editable `grade_column_label` support to grading template periods so the summary header can use a custom label when configured and still fall back to the tabulation helper when blank.
- Completed work: applied the same label logic to prior period headers in the final summary table so PRELIM, MIDTERM, and PRE-FINAL can reflect their configured display labels.
- Completed work: updated the printable final-period report of grades to use the same label logic, so the PDF/print view no longer collapses to only the final-period grade column.
- Completed work: aligned the grade explanation modal with the same periodic label rules so the final-period explain window uses `FINAL EXAM` or the admin-configured label without duplicated `Grade` wording, and the final-grade breakdown keeps the display labels in the formula/detail cards.
- Safety confirmations: no grading template names/codes, grade computation, submission logic, or final course grade calculation were changed.
- Changed files: `apps/grading/models.py`, `apps/grading/migrations/0030_gradingtemplateperiod_grade_column_label.py`, `apps/grading/duplication.py`, `apps/admin_portal/forms.py`, `apps/admin_portal/tests_template_department_visibility.py`, `apps/faculty_portal/views.py`, `templates/faculty_portal/period_summary.html`, `templates/faculty_portal/guide.html`, `templates/faculty_portal/guide_manual.html`, `apps/faculty_portal/tests_assignment_acceptance.py`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, `HANDOFF.md`.
- Validation performed: `python.exe manage.py check` passed; `python.exe manage.py test apps.admin_portal.tests_template_department_visibility.GradingTemplateDepartmentVisibilityTests.test_template_period_edit_shows_and_saves_grade_column_label` passed; `python.exe manage.py test apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_final_period_summary_shows_prior_period_grade_columns_and_final_grade apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_final_period_summary_uses_custom_grade_column_label_when_configured apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_final_period_summary_uses_custom_prior_period_grade_column_labels` passed.
- Pending work / risk: no backend risk remains from this change, but a quick browser smoke check on one final-period submitted grade explanation and one custom-labeled period is still a good visual sanity check.
- Exact next step: if we revisit this area, open the final-period summary and the grade explanation modal in the browser and confirm the top-right label, submitted-grade prose, and formula cards read naturally with and without a custom `grade_column_label`.

## Faculty Quick Score Encoding Phase 1
- Completed work: added a reversible frontend enhancement to `templates/faculty_portal/activity_scores.html` for editable score fields only.
- Behavior added: Enter moves to the next enabled score input, Shift+Enter moves to the previous input, ArrowDown/ArrowUp move vertically, Tab remains browser-native, the first enabled score field autofocuses, and a single-column paste from Excel/Google Sheets fills enabled score fields downward without auto-submitting.
- Behavior added: a small `Unsaved changes` indicator appears after edits, and the existing before-unload/link-leave warning remains active for unsaved score changes.
- Feature control: `FEATURE_FACULTY_QUICK_SCORE_ENCODING` defaults Off through `FeatureSettingsService` for safer rollout and is exposed on Configuration Management. Turning it On adds the quick-encoding activation hook; leaving it Off keeps the original score-entry page behavior.
- Safety confirmations: no backend score saving, `upsert_activity_scores`, blank-vs-zero handling, grade computation, attendance, enrollment, submissions, locks, corrections, routes, models, or migrations were changed.
- Changed files: `apps/core/services/features.py`, `apps/admin_portal/forms.py`, `apps/admin_portal/views.py`, `apps/faculty_portal/views.py`, `templates/admin_portal/tools/configurable_features.html`, `templates/faculty_portal/activity_scores.html`, `apps/faculty_portal/tests_assignment_acceptance.py`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, `HANDOFF.md`.
- Validation performed: `python.exe manage.py check` passed; `python.exe manage.py test apps.faculty_portal.tests_assignment_acceptance` passed with 117 tests.
- Pending work / risk: browser smoke test is still recommended on a real score page to feel-check focus movement, paste behavior, and the unsaved indicator on desktop and mobile.

## Faculty Score Notice Cleanup
- Completed work: updated `templates/faculty_portal/activity_scores.html` so Average Activities notices keep the detail name but no longer display `Configured Detail Weight` or reference-only weight text on the score encoding page.
- Safety confirmations: no backend score saving, blank-vs-zero handling, grade computation, attendance, enrollment, submissions, locks, corrections, routes, models, or migrations were changed.
- Changed files: `templates/faculty_portal/activity_scores.html`, `apps/faculty_portal/tests_assignment_acceptance.py`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, `HANDOFF.md`.
- Validation performed: `python.exe manage.py check` passed; `python.exe manage.py test apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_average_activity_detail_weight_is_hidden_on_activity_page` passed.

## Admin User Password Reset Email
- Completed work: added a dedicated Admin Portal change-password template with a Generate Password button that fills both password fields before save.
- Completed work: after an admin resets a user's password, TeacherMate+ emails the affected user a branded temporary-credentials card using the neutral TeacherMate+ sign-in link and keeps `must_change_password=True`.
- Safety confirmations: no login rules, password validation rules, role assignment behavior, user creation behavior, models, migrations, or routes were changed.
- Changed files: `apps/admin_portal/views.py`, `apps/admin_portal/tests_users.py`, `templates/admin_portal/security/user_change_password.html`, `templates/admin_portal/emails/user_password_change_credentials.txt`, `templates/admin_portal/emails/user_password_change_credentials.html`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, `HANDOFF.md`.
- Validation performed: `python.exe manage.py check` passed; `python.exe manage.py test apps.admin_portal.tests_users` passed with 15 tests.

## Mobile Responsiveness Pass
- Completed work: improved the Faculty Portal `period_activities`, `activity_scores`, `period_attendance`, and `attendance_summary` pages for phone-sized screens by strengthening responsive table shells, wrapping action rows, and making the shared quick-jump strip wrap more cleanly.
- Completed work: kept the Faculty User Guide trigger on the compact `teacher_star.png` image and left backend/grading behavior untouched.
- Changed files: `templates/faculty_portal/partials/period_quick_nav.html`, `templates/faculty_portal/base.html`, `templates/faculty_portal/activity_scores.html`, `templates/faculty_portal/period_activities.html`, `templates/faculty_portal/period_attendance.html`, `templates/faculty_portal/attendance_summary.html`, `apps/faculty_portal/tests_assignment_acceptance.py`, `apps/faculty_portal/tests_attendance_summary.py`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, `HANDOFF.md`.
- Validation performed: `python.exe manage.py check` passed; `python.exe manage.py test apps.faculty_portal.tests_assignment_acceptance apps.faculty_portal.tests_attendance_summary` passed with 123 tests.
- Pending work / risk: browser/device smoke test on iPhone-width, Android-width, tablet, and desktop widths is still recommended for visual confirmation.
- Exact next step: visually confirm the responsive wrapping on the Faculty Portal encoding pages.

## Faculty Quick Guide Rename
- Completed work: renamed the floating Faculty Guide trigger to `Faculty Quick Guide` and increased the guide icon/button size slightly for better tapability.
- Changed files: `templates/faculty_portal/base.html`, `apps/faculty_portal/tests_help_guide.py`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, `HANDOFF.md`.
- Validation performed: `python.exe manage.py check` passed; `python.exe manage.py test apps.faculty_portal.tests_help_guide` passed with 4 tests.

## Public Landing Copy
- Completed work: tightened the public Faculty Portal landing copy so Performance Trends, Class Performance, and Student Consultation are named explicitly in the intro text.
- Changed files: `templates/faculty_portal/public_index.html`, `apps/faculty_portal/tests_public_login.py`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, `HANDOFF.md`.
- Validation performed: `python.exe manage.py check` passed; `python.exe manage.py test apps.faculty_portal.tests_public_login` passed with 6 tests.

## Completed In This Session
- Faculty Portal dashboard `Updates Since Your Last Visit`:
  - added a read-only dashboard card that uses the prior successful faculty login as the `since_at` anchor
  - scoped the feed to the faculty member's active offerings using the existing faculty portal scope
  - surfaced the latest five low-risk updates from existing timestamped records, including assignments, enrollments, enrollment adjustments, reopened submissions, correction decisions, reminders, notices, and deadline warnings
  - moved the card directly under the grade submission deadline banner for clearer priority ordering on the dashboard
  - increased the card's bottom spacing so it visually separates from the panels below it
  - added a contextual `?` help callout in the card header explaining what appears in the feed
  - added focused regression tests for empty-login state, prior-login anchor selection, scope filtering, reopened submissions, correction decisions, and newest-five truncation
  - kept the feature read-only with no new model or migration
- Faculty Portal guide button update:
  - replaced the floating `?` guide button with a smaller `teacher_star.png` icon while keeping the guide open-in-new-tab behavior
- Review confirmation: no faculty, campus, class, or student data leakage was found; the previous-login anchor uses the prior `LOGIN_SUCCESS` row rather than the current login; updates are newest-first with priority tie-breaker; empty/no-prior-login state fails safely; no migrations were added; and no login, grading, attendance, enrollment, corrections, locks, or submissions behavior was changed.

### Changed Files For This Ending Pass
- `apps/faculty_portal/tests_dashboard_updates.py`
- `apps/faculty_portal/tests_assignment_acceptance.py`
- `apps/faculty_portal/tests_attendance_summary.py`
- `apps/faculty_portal/tests_help_guide.py`
- `apps/faculty_portal/tests_public_login.py`
- `templates/faculty_portal/activity_scores.html`
- `templates/faculty_portal/attendance_summary.html`
- `templates/faculty_portal/base.html`
- `templates/faculty_portal/partials/period_quick_nav.html`
- `templates/faculty_portal/period_activities.html`
- `templates/faculty_portal/period_attendance.html`
- `templates/faculty_portal/public_index.html`
- `CHANGE_LOG.md`
- `TEACHERMATEPLUS_CONTEXT.md`
- `HANDOFF.md`

### Pending Work / Known Issues / Risks
- Browser smoke test still recommended on the Faculty Dashboard to confirm the new card layout, ordering, and empty state feel right in the real UI.
- Existing dirty worktree contains unrelated files from prior session work, including enrollment-adjustment and attendance-summary files. Do not assume every dirty file listed by `git diff --name-only` belongs to this dashboard update change.

### Validations Actually Executed For This Ending Pass
```powershell
& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py check
# System check identified no issues (0 silenced).

& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py test apps.faculty_portal.tests_assignment_acceptance apps.faculty_portal.tests_attendance_summary
# 123 tests passed

& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py test apps.faculty_portal.tests_public_login
# 6 tests passed

& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py test apps.faculty_portal.tests_help_guide
# 4 tests passed

& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py test apps.faculty_portal.tests_dashboard_updates
# 12 tests passed
```

### Exact Next Steps For Next Codex Session
1. Read `AGENTS.md`, `TEACHERMATEPLUS_CONTEXT.md`, `CHANGE_LOG.md`, and this file before making changes.
2. Smoke-test the Faculty Portal in a browser:
   - confirm the new updates card appears in the dashboard shell
   - confirm the empty state shows the no-previous-login message
   - confirm the latest-5 truncation and ordering feel readable
   - confirm the responsive score, activity, attendance, and attendance-summary pages at phone, tablet, and desktop widths
3. If the browser smoke test shows a mismatch, adjust only the affected Faculty Portal template/CSS path.

- Faculty Portal guide refresh:
  - Removed the `Top Faculty Tasks` block from `/faculty/guide/`.
  - Converted the daily workflow area into a collapsed `Daily Faculty Workflow` accordion and removed the `Start Here` wording.
  - Added a collapsed `Semester Faculty Workflow` accordion above the daily workflow using `media/portal-img/semester_workflow.png`.
  - Added practical `How to open` steps to detailed reference cards for My Classes, Dashboard, Class List, Activities, Score Encoding, Attendance, and Summary.
  - Added screenshot modal buttons for guide images under `media/faculty_helpguide/`, including `1_myclasses.png`, `1_dashboard.png`, `2_activities.png`, `2_encodescores.png`, `2_attendance.png`, and `3_summary.png`.
  - Updated `apps/faculty_portal/tests_help_guide.py` to cover the revised guide structure, collapsed accordions, modal markup, and screenshot references.

- Faculty Assignment acceptance governance:
  - Removed the `Undo Acceptance` button from accepted Faculty Portal class cards.
  - Blocked direct POST attempts to the legacy faculty undo-acceptance route with a faculty-facing message.
  - Logged blocked attempts as `UNDO_ACCEPTANCE_BLOCKED` without changing assignment state.
  - Updated the Faculty Help Guide action text to tell faculty to contact the assigning admin or academic office when an accepted load must be unassigned or replaced.
  - Updated assignment acceptance regression tests to assert the button is hidden and direct POST leaves the assignment accepted.

### Submission Non-Compliance Notice Validation
```powershell
& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py test apps.notifications.tests
# 11 tests passed

& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py check
# System check identified no issues

& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py test apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_admin_can_set_enrollment_ownership_mode_from_configurable_features apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_configurable_features_can_filter_class_override_targets_by_faculty apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_configurable_features_shows_single_device_login_setting apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_configurable_features_can_store_assignment_workflow_settings
# 4 tests passed

& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py test apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_configurable_features_can_store_assignment_workflow_settings apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_configurable_features_rejects_invalid_non_compliance_notice_timing
# 2 tests passed

# Note: one earlier focused admin test command used the wrong guessed class name `AssignmentAcceptanceTests` and failed before running tests. It was rerun successfully with the correct class name above.
```

### Faculty Summary Readiness Validation
```powershell
& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py test apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_period_summary_shows_encoded_zero_scores_metric_not_my_courses apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_period_summary_readiness_cards_show_status_labels
# 2 tests passed

& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py check
# System check identified no issues

& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py test apps.faculty_portal.tests_help_guide
# 4 tests passed
```

- Faculty Portal help button new-tab behavior:
  - Updated the shared Faculty Portal base template so the floating `?` help button opens `{% url 'faculty_portal:guide' %}` in a new tab.
  - Added `rel="noopener noreferrer"` for safe separate-tab behavior.
  - Updated the accessible label/title to clarify that the Faculty User Guide opens in a new tab.
  - Added regression coverage in `apps/faculty_portal/tests_help_guide.py`.

### Faculty Help Button Validation
```powershell
& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py test apps.faculty_portal.tests_help_guide
# 4 tests passed

& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py check
# System check identified no issues
```

- Faculty Assignment replacement/safety enhancements:
  - Added `FacultyAssignmentReplacementLog` in `apps/academics/models.py`.
  - Added `FacultyAssignmentSafetyService` in `apps/academics/services.py`.
  - Added Admin Portal route/page `Academics -> Faculty Assignments -> Replace Faculty`.
  - Existing Assigned Offerings checkboxes now support `REPLACE FACULTY` in addition to `UNASSIGN SELECTED`.
  - Replacement workflow requires:
    - selected active assignment(s)
    - replacement faculty
    - replacement type
    - reason category
    - remarks/details
    - explicit impact-review confirmation before processing
  - Replacement types:
    - Permanent Replacement
    - Temporary Substitute
    - Secondary / Co-Faculty
    - Administrative Reassignment
    - Wrong Faculty Assignment
  - Processing behavior:
    - Permanent, Administrative, and Wrong Faculty Assignment deactivate the old assignment and create/reactivate the new assignment as primary.
    - Temporary Substitute and Secondary / Co-Faculty keep the old assignment active and create/reactivate the new assignment as secondary.
    - New/replacement assignments follow the existing faculty acceptance workflow and are pending until accepted.
    - Old faculty loses active access after permanent/administrative/wrong-assignment replacement.
  - Impact analysis counts:
    - active activities
    - activity scores
    - submissions
    - period grades
    - final grades
    - correction requests
    - reopen requests
    - relevant grading locks
  - Historical integrity:
    - No activities, scores, attendance, submissions, period grades, final grades, correction requests, reopen requests, or locks are rewritten.
    - Original authorship/history stays attached to existing records.
  - Direct edit safety:
    - Changing faculty user or course offering on an in-use `FacultyAssignment` row is blocked.
    - Safe direct edits such as note/load-role/active-state behavior remain under the existing form rules.
  - Permissions:
    - Added `faculty_replacement.view`.
    - Added `faculty_replacement.process`.
    - Superadmin, Tenant Admin, Campus Admin, and Registrar get view/process by default.
    - Area Chair, Dean, College Dean, and CAO are view-only by default unless explicitly granted process permission.
  - Added focused tests in `apps/admin_portal/tests_faculty_assignment_replacement.py`.

### Faculty Assignment Replacement Validation
```powershell
& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py check
# System check identified no issues

& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py migrate
# Applied academics.0010_facultyassignmentreplacementlog
# Applied rbac.0019_seed_faculty_replacement_permissions

& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py migrate --check
# Exit code 0; no unapplied migrations

& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py test apps.admin_portal.tests_faculty_assignment_replacement
# 12 tests passed

& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py test apps.faculty_portal.tests_assignment_acceptance
# 112 tests passed

& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py test apps.grading.tests.GradeEncodingAccessControlTests
# 10 tests passed
```

### Faculty Assignment Replacement Manual Test Steps
1. Log in as a user with `faculty_replacement.process`.
2. Open `Admin Portal -> Academics -> Faculty Assignments`.
3. Search/select the current faculty.
4. Select one or more assigned offerings.
5. Click `REPLACE FACULTY`.
6. Confirm the selected assignment rows and impact counts are shown.
7. Choose replacement faculty, replacement type, reason category, and remarks.
8. Check the impact confirmation box and click `Confirm Replacement`.
9. For Permanent/Administrative/Wrong Assignment, confirm the old faculty assignment becomes inactive and the replacement assignment is primary/pending acceptance.
10. For Temporary Substitute or Secondary / Co-Faculty, confirm the old assignment stays active/primary and the new assignment is secondary/pending acceptance.
11. Log in as the old faculty after a permanent replacement and confirm the class no longer appears.
12. Log in as the replacement faculty and confirm the assignment must be accepted before opening the class.
13. Try direct-editing an in-use assignment to another faculty and confirm the form blocks it with a Replace Faculty message.
14. Log in as a view-only role and confirm the page can be viewed but direct POST processing is forbidden.

### Known Limitations / Next Steps
- There is no separate detailed replacement-log detail page yet; the replacement page shows recent history and the structured records are available in `FacultyAssignmentReplacementLog`.
- Replacement workflow intentionally does not decide whether the academic replacement is valid; it records and safely applies an authorized admin decision.
- Replacement faculty must still accept the assignment unless NCBA later approves an admin-bypass workflow for urgent replacements.
- Future enhancement: add filters and a dedicated replacement history/detail page if operations need a long-term audit browser.

- Faculty Activities Average Activities display cleanup:
  - Detail dropdown options now hide configured detail weights when the detail belongs to a subcomponent using `Average Activities`.
  - The Activities table hides the `Detail Weight` column when visible detail weights would only be reference values under `Average Activities`.
  - Weighted Details behavior remains unchanged; configured detail weights still appear where they affect computation.
  - Updated regression coverage in `apps/faculty_portal/tests_assignment_acceptance.py`.
  - No grade computation, scores, activities, submissions, or locks were changed.

### Faculty Activities Display Validation
```powershell
& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py test apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_average_activity_detail_weight_is_hidden_on_activity_page
# 1 test passed

& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py check
# System check identified no issues
```

- Enrollment Adjustment Tool:
  - Added `EnrollmentAdjustmentLog` in `apps/enrollment/models.py`.
  - Added `EnrollmentAdjustmentService` in `apps/enrollment/services.py`.
  - Added Admin Portal page `Enrollment -> Enrollment Adjustments`.
  - Added adjustment history and detail/audit view.
  - Added `enrollment_adjustment.view` and `enrollment_adjustment.process` permissions.
  - Added menu migration for `Enrollment Adjustments`.
  - Added seed support in `seed_stage_0_1`.
  - Added focused tests in `apps/admin_portal/tests_enrollment_adjustments.py`.
- Supported adjustment actions:
  - move one student
  - move multiple selected students
  - transfer all active students in the source offering
- Impact analysis counts:
  - attendance records
  - active source offering activities
  - student activity scores
  - grade submissions
  - student period grades
  - student final grades
  - correction requests
  - reopen requests
  - locked source course-offering periods
- Classification behavior:
  - `SAFE`: no academic records found; eligible for immediate processing.
  - `WARNING`: academic records exist; eligible only after explicit warning confirmation.
  - `BLOCKED`: source and destination are the same, destination enrollment already exists, final grade is submitted, or a source course-offering period is locked.
- Processing behavior:
  - creates a destination enrollment
  - deactivates the source enrollment
  - writes an `EnrollmentAdjustmentLog` per selected student
  - does not move, edit, delete, submit, unlock, recompute, or migrate gradebook records
- Destination offering behavior:
  - source offering dropdown follows selected Academic Year, Term, and Campus
  - destination offering dropdown follows authorized scope and selected Academic Year/Term, but is not forced to the source campus
- Known limitation:
  - cross-campus/program movement creates the destination enrollment under the destination offering scope, but it does not rewrite the student's master campus/department/program. Student master-data corrections should still come from SIS/import maintenance when Pinnacle changes those values.

- Post-enrollment correction safety:
  - Added `CourseOfferingSafetyService` in `apps/grading/services.py`.
  - Added `EnrollmentSafetyService` in `apps/grading/services.py`.
  - Course Offering safety treats an offering as in use when it has:
    - enrollments
    - faculty assignments and accepted faculty assignments
    - grade activities
    - student activity scores
    - student period grades
    - student final grades
    - grade submissions
    - correction requests
    - submission reopen requests
    - course-offering period locks
    - attendance sessions/records
  - In-use Course Offerings block changes to:
    - tenant
    - campus
    - department
    - program
    - academic year
    - term
    - course
    - section
    - status
    - active state
  - In-use Course Offerings still allow safe non-identity edits:
    - room
    - schedule text
  - Enrollment safety treats an enrollment as in use when the student/offering has:
    - student activity scores
    - student period grades
    - student final grades
    - grade submissions
    - correction requests
    - submission reopen requests
    - course-offering period locks
    - attendance records
  - In-use Enrollments block:
    - student change
    - course-offering transfer
    - active-state removal/deactivation
  - In-use Enrollments still allow status-only updates such as DRP/W/INC when existing enrollment rules permit them.
  - Added warning panels to the shared Admin Portal form page:
    - `In use offering`
    - `In use enrollment`
  - Added the same central service checks to Django admin forms for `CourseOffering` and `Enrollment`.
  - No grade computation, scores, activities, grades, submissions, locks, reopen requests, or correction records are modified by the safety checks.
  - Future tools intentionally not implemented in this phase:
    - Student Section Transfer Review Tool
    - Faculty Replacement Tool
    - Cancelled/Dissolved Class Workflow
    - Post-Enrollment Change Log

### Post-Enrollment Safety Validation
```powershell
& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py check
# System check identified no issues

& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py migrate --check
# Completed with exit code 0; no unapplied migrations reported

& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py test apps.admin_portal.tests_post_enrollment_safety
# 8 tests passed

& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py test apps.admin_portal.tests_course_template_assignment_safety
# 14 tests passed

& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py test apps.grading.tests.GradeEncodingAccessControlTests
# 10 tests passed

& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py test apps.faculty_portal.tests_assignment_acceptance
# 112 tests passed
```

### Post-Enrollment Safety Manual Test Steps
1. Log in as an authorized Admin Portal user.
2. Open `Admin Portal -> Academics -> Course Offerings`.
3. Edit an unused offering and confirm identity fields can still be changed according to existing rules.
4. Edit an offering with enrollments or gradebook records and confirm the `In use offering` warning appears.
5. Try changing the in-use offering's course, section, term, campus, status, or active state and confirm the form blocks the change.
6. Change only room or schedule for the in-use offering and confirm the save is allowed.
7. Open `Admin Portal -> Enrollment`.
8. Edit an enrollment with no grading records and confirm moving to another offering still follows existing rules.
9. Edit an enrollment with activity scores or period grades and confirm the `In use enrollment` warning appears.
10. Try changing the student or course offering and confirm the form blocks the change.
11. Change only the enrollment status to DRP/W/INC and confirm the save is allowed when existing enrollment rules permit it.
12. Confirm Grade Encoding Access Control still pauses faculty writes independently from these Admin maintenance safety checks.

- Grade Encoding Access Control:
  - Added `GradeEncodingControl` in `apps/grading/models.py` with Academic Year, Term, optional period code, optional campus, optional course offering, `OPEN/CLOSED` status, reason, faculty notice, active flag, and created/updated user references.
  - Added `GradeEncodingAccessService` in `apps/grading/services.py`.
  - Plugged the new gate into `GradingGovernanceService.assert_encoding_allowed()` so existing protected paths inherit the block:
    - activity create/update/archive
    - score save/update
    - attendance session/record writes
    - period submission
  - Added Admin Portal management under `Grading -> Grade Encoding Access Control`:
    - list/filter
    - create
    - edit
    - open
    - close
  - Added `GradeEncodingControlForm` with validation:
    - Academic Year and Term required by the model/form
    - reason and faculty notice required when status is `CLOSED`
    - course offering must match selected tenant/year/term/campus
    - duplicate active exact-scope controls are blocked in the form
  - Added RBAC/navigation support:
    - `grading_encoding_control.manage`
    - menu item `Grade Encoding Access Control`
    - migration and `seed_stage_0_1` support
    - faculty roles do not receive this permission
  - Added Faculty Portal notices:
    - dashboard shows closed encoding notices for affected class/period rows without student data
    - activities, score entry, attendance, and summary pages show read-only closure banners
  - Added Django admin registration for operational visibility.
  - Added focused regression tests in `apps/grading/tests.py`.

### Grade Encoding Access Control Validation
```powershell
& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py check
# System check identified no issues

& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py makemigrations grading
# Created grading.0029_gradeencodingcontrol

& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py migrate
# Applied grading.0029_gradeencodingcontrol, rbac.0016_seed_grade_encoding_control_permission, navigation.0007_seed_grade_encoding_control_menu

& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py test apps.grading.tests.GradeEncodingAccessControlTests
# 7 tests passed

& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py test apps.admin_portal
# Timed out after 180 seconds. One failure marker appeared before timeout, but the captured output did not include the failure detail.

& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py test apps.faculty_portal
# Timed out after 180 seconds after many passing dots; no failure detail was produced before timeout.
```

### Grade Encoding Access Control Post-Implementation Review
- Reviewed the Grade Encoding Access Control implementation against the post-review prompt.
- Fixed Faculty Portal context propagation so the closure state and message are available on:
  - Activities
  - Score Entry
  - Attendance
  - Summary
- Tightened Faculty Portal UI flags:
  - `can_create_activity` now follows the centralized `state["is_editable"]`.
  - attendance session management now follows the centralized `state["is_editable"]`.
- Improved blocked direct activity POST messaging so faculty see the actual encoding-control reason/notice after redirect instead of only the generic locked/submitted message.
- Simplified Faculty Dashboard closure notification:
  - Dashboard now shows only the compact `Encoding Closed` status in the Grade Encoding Status table.
  - Dashboard no longer shows the full closure reason/notice alert.
  - Pending Grade Issues no longer includes encoding-closed entries because faculty cannot resolve those directly.
  - Full closure reason/notice remains visible inside Activities, Score Entry, Attendance, and Summary pages.
- Added/validated focused tests:
  - `apps.grading.tests.GradeEncodingAccessControlTests`
  - `apps.admin_portal.tests_grade_encoding_control`
  - `apps.faculty_portal.tests_grade_encoding_control`
- Isolated the earlier Admin Portal failure marker:
  - `apps.admin_portal.tests_actual_data_reset.ActualDataResetTests.test_reset_keeps_security_shell_and_deletes_actual_data` expected exactly one `MenuGroup/MenuItem/MenuItemPermission`.
  - The new Grade Encoding Access Control navigation migration legitimately seeds another Admin menu group/item.
  - The test now verifies that the protected Actual Data Reset security shell remains present instead of asserting global menu counts.

### Grade Encoding Access Control Review Validation
```powershell
& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py check
# System check identified no issues

& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py migrate --check
# Completed with exit code 0; no unapplied migrations reported

& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py test apps.grading.tests.GradeEncodingAccessControlTests
# 10 tests passed

& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py test apps.admin_portal.tests_grade_encoding_control apps.admin_portal.tests_actual_data_reset
# 9 tests passed

& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py test apps.faculty_portal.tests_grade_encoding_control
# 3 tests passed

& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py test apps.admin_portal.tests_academic_performance_insights
# 19 tests passed

& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py test apps.admin_portal.tests_course_template_assignment_safety
# 14 tests passed

& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py test apps.faculty_portal.tests_assignment_acceptance
# 112 tests passed

& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py test
# Timed out after 600 seconds while still progressing. No failure marker was produced before timeout; output showed expected mocked email/API log noise and many passing dots.
```

### Grade Encoding Access Control Manual Test Steps
1. Run migrations in the target environment.
2. Log in as a role with `grading_encoding_control.manage`.
3. Open `Admin Portal -> Grading -> Grade Encoding Access Control`.
4. Create a `CLOSED` control for an active Academic Year and Term, with reason and faculty notice.
5. Confirm the form blocks `CLOSED` status if reason or notice is blank.
6. Log in as affected faculty and open Dashboard.
7. Confirm the affected class shows `Encoding Closed` and a closure notice.
8. Open Activities, Score Entry, Attendance, and Summary for the affected period.
9. Confirm create/edit/save/submit buttons are hidden or disabled and a closure notice appears.
10. Attempt direct POST to create an activity or save a score and confirm the server blocks it.
11. Return to Admin Portal and Open the control.
12. Confirm normal encoding resumes, subject to existing locks, deadlines, submitted status, and correction rules.
13. Create a broader Closed control and a lower-scope Open control; confirm the broader Closed control still blocks encoding.
14. Confirm unrelated offerings outside the scope remain open.

### Grade Encoding Access Control Known Limitations
- Phase 1 has no department-level, course-code-level, or scheduled date/time automation.
- A lower-scope `OPEN` control is only an open record for that exact scope; it does not override any broader matching `CLOSED` control.
- The database unique constraint may not catch all nullable-scope duplicates on every database backend, so the Admin Portal form remains the primary duplicate exact-scope guard.
- Direct database SQL can bypass the application gate. Operational changes should go through Admin Portal and governed services.

### Grade Encoding Access Control Next Recommended Phase
- Add department/course-code scope only if operations need it after using the Phase 1 controls.
- Add a focused Admin Portal test module for list/create/edit/toggle page rendering and permission denial if broader app-level tests remain too slow to isolate quickly.
- Consider scheduled open/close windows only after policy owners confirm the timing rules.

- Course Template Assignment in-use replacement safety:
  - Added `CourseTemplateAssignmentSafetyService` in `apps/grading/services.py`.
  - The service resolves affected offerings from the assignment's course and effective term. A no-term assignment is treated conservatively and excludes terms that already have an exact active published assignment.
  - Template replacement is blocked when affected offerings have any of:
    - GradeActivity records
    - StudentActivityScore records
    - StudentPeriodGrade records
    - StudentFinalGrade records
    - GradeSubmission records
    - GradeCorrectionRequest records
    - course-offering GradingPeriodLock records
  - The blocker runs through the custom Admin Portal `CourseTemplateAssignmentForm` and the Django admin `CourseTemplateAssignmentAdminForm`.
  - Editing an in-use assignment without changing its template remains allowed for safe fields currently supported by the form.
  - The Admin Portal edit page now shows an `In use assignment` warning with offering, activity, score, period grade, submission, final grade, correction, and period-lock counts.
  - The validation message tells admins to use a future-term assignment with no encoded grades or a separate test course instead of replacing the template midstream.
  - No grade computation, activity, score, submission, correction, lock, or grade records are migrated, deleted, recomputed, or modified by this safety check.

### Course Template Assignment Safety Validation
```powershell
& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py test apps.admin_portal.tests_course_template_assignment_safety
# 14 tests passed, including isolated final-grade, correction-request, and course-offering period-lock blockers

& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py test apps.admin_portal.tests_course_template_assignment_bulk apps.admin_portal.tests_course_template_assignment_list
# 5 tests passed

& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py test apps.faculty_portal.tests_assignment_acceptance
# 112 tests passed

& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py test apps.admin_portal.tests_academic_performance_insights
# 19 tests passed

& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py check
# System check identified no issues

& 'C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\python.exe' manage.py test
# Attempted again during the post-implementation review. The full suite did not finish within the 15-minute command timeout. No failure summary was produced before timeout; the last visible output was normal existing account/SIS API test log noise and a long stream of passing dots.
```

### Course Template Assignment Safety A132 Manual Validation
- Local A132-ITAPPS active assignment remains `BSA_1ST_3RD_REGULAR` for `2025-2026 / 2ND`.
- Usage summary for the assignment: 27 affected offerings, 199 activities, 1,647 scores, 75 period grades, 25 final grades, 2 submissions, 0 correction requests, and 0 course-offering period locks.
- A validation-only attempt to replace A132-ITAPPS with `TEST-ACADEMIC-INSIGHTS` returned invalid with the expected in-use safety message.
- The database assignment still points to `BSA_1ST_3RD_REGULAR` after validation, and activity/score/period-grade/submission counts were unchanged.
- No A132 activity, score, grade, submission, or lock records were modified.

### Course Template Assignment Safety Manual Checks
1. Open `Admin Portal -> Grading -> Course Template Assignments`.
2. Edit an assignment for a course/term with no activities, scores, grades, or submissions and confirm a template change is allowed.
3. Edit an assignment for a course/term already used by faculty and confirm the `In use assignment` warning appears.
4. Try to replace its grading template and confirm the form blocks the change with the clear safety message.
5. Confirm the original template assignment, activities, scores, period grades, submissions, and locks remain unchanged.
6. Confirm creating a future-term assignment remains the safe path when no encoded gradebook records exist.

### Course Template Assignment Safety Known Limitations
- The blocker prevents replacement; it does not map old activities/scores to a new template. A governed migration/mapping tool would need separate approval and detailed academic rules.
- Direct database SQL can still bypass application validation. Operational changes should go through Admin Portal or governed services.
- The UI keeps the template dropdown visible and relies on server-side rejection so administrators receive the explicit validation message.

- Course Template Assignment ordering:
  - Sorted the Course filter and result rows by course title, then course code.
  - Applied the same ordering to active assignments, inactive assignments, courses without templates, and current offerings without templates.
  - Added a regression test for title/code ordering.
  - No assignment, grading-template, scope, or database behavior changed.

- TEST Faculty activity visibility correction:
  - Confirmed the seeded records existed, but A132 resolved the exact-term operational template `BSA_1ST_3RD_REGULAR` while activities still referenced the dedicated TEST template periods.
  - Updated the seeder to attach TEST activities and scores to the template officially resolved by `FacultyGradingService.resolve_template_for_offering()`.
  - Preserved precedence: an exact-term Course Template Assignment still outranks Tenant Grading Profiles. No operational template structure or grading formula was changed.
  - Added period code/name alias handling so `FX / FINAL` receives the expected final exam activity.
  - Added a regression test for exact-term template precedence and PRELIM-through-FINAL activity coverage.
  - Reseeded locally. Current totals are 18 offerings, 138 students, 346 activities, and 2,616 scores.
  - Verified the real Faculty Portal responses for `test-faculty-01`:
    - My Classes: Active 2, Archived 0
    - PRELIM: seven activities
    - MIDTERM: seven activities
    - PRE-FINAL: six activities under the operational template
    - `FX / FINAL`: one final exam activity
    - MIDTERM Activities page: HTTP 200 with seven visible activities

- TEST Faculty My Classes correction:
  - Confirmed the TEST assignments existed, but Faculty Portal correctly placed them in Archived because they used `TEST-AY-2026 / TEST-TERM` while NCBA's active scope is `2025-2026 / 2ND`.
  - Updated the demo seeder to prefer the tenant's configured active Academic Year and Term. The isolated TEST Academic Year/Term remains a fallback when no active scope is configured.
  - The TEST Tenant Grading Profiles remain limited to the TEST program, campus, course, and effective term, so this does not replace ordinary operational grading profiles.
  - Added a regression test proving all 18 TEST offerings use the configured active scope when one exists.
  - Removed and safely rebuilt only the local TEST dataset under `2025-2026 / 2ND`.
  - Verified `/faculty/my-courses/` as `test-faculty-01`: HTTP 200, Active 2, Archived 0, both A132 TEST sections visible, and password `TestDemo123!` valid.

- Academic Performance Insights TEST dataset extension:
  - Added one additional TEST faculty member per campus:
    - `test-faculty-07` for NCBA-01
    - `test-faculty-08` for NCBA-02
    - `test-faculty-09` for NCBA-03
  - Each new faculty member handles both `A132-ITAPPS` and `A221-ACGN` in a new TEST Section C.
  - Each of the six new offerings has exactly three active enrolled students.
  - Each student has complete active activity-score records for PRELIM, MIDTERM, PRE-FINAL, and FINAL; verified missing-output count is zero for every new class/period.
  - The seeder remains idempotent, DEBUG-only, confirmation/password protected, and cleanup-safe.
  - Applied the seeder locally with the existing demo password. Current totals are 14 TEST users, 18 offerings, 138 students, 346 activities, and 2,616 scores.
  - No migration was required.

### Additional TEST Faculty Validation
```powershell
python manage.py test apps.admin_portal.tests_academic_performance_demo_seed
# 5 tests passed

python manage.py test apps.admin_portal.tests_academic_performance_demo_seed apps.admin_portal.tests_academic_performance_insights
# 24 tests passed

python manage.py check
# System check identified no issues
```

- Academic Performance Insights presentation review:
  - Added a main-page Needs Attention panel with neutral section, issue, and suggested-check wording. Normal and incomplete-data states have explicit messages.
  - Added a CSS-bar legend: green is class average, red is at-risk count, and gray is missing outputs.
  - Compacted Course and Faculty presentation and protected the View Details action area from crowding.
  - Added What to Review, Comparison Context, Ready for Comparison, Activity Setup Summary, and a filtered View Activity Consistency shortcut to section Performance Details.
  - Comparison Context is limited to the same authorized campus, department, course code, academic year, term, and grading period.
  - Activity Setup now sorts by template component, subcomponent, activity date/title, and ID. Summary labels come from the actual template hierarchy.
  - View Details now includes a validated local `next` URL. Back to Report restores the exact originating Performance Insights or Activity Consistency filters and pagination. External return URLs fall back safely to the Insights landing page.
  - Preserved the four summary cards, aggregate-only privacy, role scope, official computation reuse, and read-only behavior.
  - No migration, chart library, AI, external API, new analytics formula, or database analytics table was added.

### Academic Performance Insights UI Validation
```powershell
python manage.py test apps.admin_portal.tests_academic_performance_insights
# 19 tests passed

python manage.py test
# 551 tests passed

python manage.py check
# System check identified no issues

python -m py_compile apps/admin_portal/academic_performance.py apps/admin_portal/views.py
# passed
```

### Academic Performance Insights UI Manual Test
1. Log in as an authorized Area Chair, College Dean, or CAO and open `Grading -> Academic Performance Insights`.
2. Select Academic Year, Term, Grading Period, and optional scope filters; generate the report.
3. Confirm the four summary cards remain unchanged and the Needs Attention panel uses neutral wording.
4. Confirm the green/red/gray bar legend matches class average, at-risk count, and missing outputs.
5. Confirm Course and Faculty columns remain readable and View Details is fully visible.
6. Open View Details and confirm What to Review, Comparison Context, Ready for Comparison, Activity Setup Summary, and View Activity Consistency appear.
7. Confirm activities are ordered by component, subcomponent, then activity title/date.
8. Click Back to Report and confirm all original filters and the page number are restored.
9. Repeat from Activity Consistency and confirm Back to Report returns to that filtered page.
10. Confirm no student names, individual grades, rankings, or faculty-ranking language appear.
11. Confirm an incomplete section shows the incomplete-data guidance and a normal set shows the within-normal-range message.
12. Confirm an unauthorized class detail URL remains blocked by existing scope checks.

### Academic Performance Insights UI Known Limitations
- Comparison readiness uses active activity counts and required-category coverage; it does not compare activity titles or detailed assessment difficulty.
- Comparison Context reports the largest available same-scope section-average difference and does not introduce ranking.
- Live reports remain capped at 100 authorized offerings and are not stored as historical analytics snapshots.
- Authenticated visual browser review remains pending because no in-app browser target was connected during this session.

- Academic Year and import safety:
  - Confirmed audit log `#5131` records the May 23, 2026 change from `AY2526` to `2025-2026` by `superadmin`; existing offerings and grades remained linked because foreign keys use Academic Year ID `5`.
  - Confirmed the ordinary Audit Logs page previously hid stored before/after payloads.
  - Added server-side Academic Year tenant/code immutability after the record is used by terms, course offerings, enrollments, grading-period locks, or final-clearance reports.
  - Disabled tenant and code fields on the in-use Academic Year edit form with a clear explanation. Name, dates, and active state remain editable.
  - Added `/admin-portal/audit/logs/<id>/` with changed fields, before/after payloads, request metadata, scope, actor, and redaction of credential-like fields.
  - Set the Before Record, After Record, and Technical Metadata accordions to open by default.
  - Added Details links to Audit Logs and Recent Critical Actions.
  - Future Academic Year create/update audit events now use the affected tenant as their audit scope.
  - Improved import errors to list available active Academic Year codes and replaced stale `AY2526` template/help examples with `2025-2026` and exact-code instructions.
  - Confirmed duplicate handling remains importer-specific: sections skip existing records; students use CREATE/UPDATE/UPSERT; courses, course offerings, faculty assignments, and enrollments reject existing duplicates.
  - Added reusable Import Safety Measures guidance to the bulk-import landing page, all six upload pages, and batch-detail pages.
  - The guidance accurately states that uploads do not write operational records, only VALID rows are confirmed, confirmation uses independent per-row transactions, partial success is possible, successful writes are audited, and confirmed batches cannot be confirmed twice.
  - Added importer-specific warnings for create-only behavior, duplicate rejection/skipping, Student UPDATE/UPSERT, and Enrollment AUTO_CREATE.
  - No migration was required.

### Academic Year Safety Validation
```powershell
python manage.py test apps.admin_portal.tests_academic_year_safety
# 7 tests passed

python manage.py test apps.imports.tests_department_inference apps.admin_portal.tests_import_loading_indicators apps.admin_portal.tests_section_import_template
# 9 tests passed

python manage.py test apps.admin_portal.tests_academic_year_safety apps.imports.tests_department_inference
# 11 focused tests passed after final identifier/redaction/import-cache hardening

python manage.py test apps.admin_portal.tests_academic_year_safety
# 7 tests passed after expanding Audit Log detail sections by default

python manage.py test apps.admin_portal.tests_import_safety_guidance apps.admin_portal.tests_import_loading_indicators apps.admin_portal.tests_section_import_template apps.imports.tests_department_inference apps.imports.tests_student_import
# 16 importer guidance and regression tests passed

python manage.py test apps.admin_portal.tests_academic_performance_demo_seed
# 3 demo seed/rename/idempotency/cleanup tests passed

python manage.py check
# System check identified no issues

python manage.py test --parallel 4
# 542 tests passed
```

### Academic Year Safety Manual Checks
1. Open `Admin Portal -> Academics -> Academic Years`.
2. Edit an unused Academic Year and confirm its tenant/code can still be corrected.
3. Create a Term under that Academic Year, reopen Edit, and confirm tenant/code are locked.
4. Change only the name or dates and confirm the update succeeds.
5. Open `Admin Portal -> Audit Logs`, select `Details`, and confirm changed fields and before/after values are visible.
6. Upload a CSV with an invalid Academic Year code and confirm the validation error lists active codes.

### Known Limitations
- Django `QuerySet.update()` and direct database SQL bypass model `save()` validation. Operational changes must continue through governed application forms/services; direct database maintenance remains a privileged emergency action.
- Existing historical audit log `#5131` retains its original `DEFAULT/MAIN` scope assignment. Audit records were not rewritten; future Academic Year events use the corrected affected-tenant scope.
- No Academic Year alias table or governed rename workflow was added. In-use codes are now locked instead of renamed.

### Exact Next Steps
1. Manually verify the Academic Year edit and Audit Log Details pages in a browser.
2. Decide later whether a separately approved Academic Year alias/governed-rename workflow is needed for legacy external codes.

- Academic Performance Insights post-implementation review and TEST dataset:
  - Revalidated all three Insights URLs, `grading_analytics.read`, feature-toggle 404 behavior, Area Chair localization, College Dean cross-campus scope, CAO existing-scope limits, aggregate-only privacy, official computation reuse, and read-only behavior.
  - Confirmed no student names, individual student grades, student rankings, faculty rankings, AI calls, external APIs, analytics tables, or chart libraries are present.
  - Added neutral performance status labels: `Normal`, `Needs Attention`, `High Risk`, and `Incomplete Data`.
  - Improved Activity Consistency to count the most specific active category/subcomponent and detect a required active subcomponent with no activity as `Incomplete Setup`.
  - Expanded the Dean/CAO summary to campus plus department, courses needing attention, status, and scoped drill down.
  - Added `python manage.py seed_academic_performance_insights_demo`.
  - Safety rules:
    - requires `--confirm-demo-data`
    - requires `--demo-password` with at least eight characters when seeding
    - refuses to run when `DEBUG=False`
    - intentionally provides no non-debug override
    - is idempotent
    - cleanup requires `--confirm-demo-data --remove-demo-data`
  - The command reuses existing NCBA Cubao, Fairview, and Taytay campuses, existing COLLEGE departments, and existing official `A132-ITAPPS` and `A221-ACGN` course records.
  - It creates isolated `TEST` programs, sections, offerings, users, students, template/profile records, activities, and scores.
  - The dedicated approved/published TEST template uses PRELIM, MIDTERM, PRE-FINAL, and FINAL. Quizzes and Participation/Output use Average Activities.
  - Local development data created:
    - 14 TEST users: one Dean, one CAO, three Area Chairs, and nine faculty users
    - 18 TEST sections/offerings
    - 138 synthetic students
    - 346 activities
    - 2,616 scores
  - Re-running the seed command produced the same counts with no duplicates.
  - Midterm Dean validation originally produced 12 section rows; the extended dataset now produces 18 section rows and three campus/department summaries:
    - Cubao: `High Risk`
    - Fairview: `Needs Attention`
    - Taytay: `Incomplete Data`
  - Activity Consistency validation:
    - PRELIM: `Consistent`
    - MIDTERM: `Minor Difference`
    - PRE-FINAL: `Needs Review`
    - FINAL includes `Incomplete Setup`
  - Verified a saved zero remains a valid encoded score and intentionally absent score records increase Missing Outputs.
  - Verified CSS bars and table fallbacks are present and their values come from the same report rows.
  - Compared TEST score values/timestamps and grade/submission/lock/attendance counts before and after report requests; no record changed.

### Academic Performance Insights TEST Data Commands
```powershell
python manage.py seed_academic_performance_insights_demo --confirm-demo-data --demo-password "choose-a-demo-password"
python manage.py seed_academic_performance_insights_demo --confirm-demo-data --remove-demo-data
```

Do not run this command in production. It refuses when `DEBUG=False`, but TEST data must also be removed or excluded before a development database is copied elsewhere.

### Academic Performance Insights TEST Users
- College Dean: `test-insights-dean`
- CAO: `test-insights-cao`
- Cubao Area Chair: `test-insights-ac-ncba-01`
- Fairview Area Chair: `test-insights-ac-ncba-02`
- Taytay Area Chair: `test-insights-ac-ncba-03`
- Faculty usernames are `test-faculty-01` through `test-faculty-09`.
- Seeder reruns rename the earlier long `test-insights-fac-*` usernames in place, preserving their user IDs, assignments, activities, and scores.
- Local assignment map:
  - `test-faculty-01`: NCBA-01 / A132-ITAPPS
  - `test-faculty-02`: NCBA-01 / A221-ACGN
  - `test-faculty-03`: NCBA-02 / A132-ITAPPS
  - `test-faculty-04`: NCBA-02 / A221-ACGN
  - `test-faculty-05`: NCBA-03 / A132-ITAPPS
  - `test-faculty-06`: NCBA-03 / A221-ACGN
  - `test-faculty-07`: NCBA-01 / A132-ITAPPS and A221-ACGN / Section C / 3 students each
  - `test-faculty-08`: NCBA-02 / A132-ITAPPS and A221-ACGN / Section C / 3 students each
  - `test-faculty-09`: NCBA-03 / A132-ITAPPS and A221-ACGN / Section C / 3 students each
- Verified the original user IDs `23, 24, 26, 27, 29, 30` and all 12 faculty assignments remained unchanged after the rename. No old `test-insights-fac-*` username remains.
- Password is the value supplied through `--demo-password`; forced password change is disabled for these TEST accounts.

### Academic Performance Insights TEST Manual Test
1. Confirm the environment is development or staging and `DEBUG=True`.
2. Run migrations and enable Academic Performance Insights as Superadmin.
3. Run the seed command with `--confirm-demo-data` and a chosen `--demo-password`.
4. Log in as `test-insights-ac-ncba-01`; select NCBA-01 and open Academic Performance Insights.
5. Select the currently configured active Academic Year and Term (`2025-2026 / 2ND` on this development database) and `MIDTERM`; confirm only Cubao TEST sections appear.
6. Repeat with the Fairview and Taytay TEST Area Chair accounts; confirm each sees only its assigned campus.
7. Log in as `test-insights-dean`; select `All authorized campuses`, the configured active Academic Year/Term, and `MIDTERM`.
8. Confirm 18 section rows and the Cubao/Fairview/Taytay campus summaries appear.
9. Confirm Course Performance shows Normal, Needs Attention, High Risk, and Incomplete Data.
10. Open Activity Consistency and test PRELIM, MIDTERM, PRE-FINAL, and FINAL for Consistent, Minor Difference, Needs Review, and Incomplete Setup.
11. Filter by `A132-ITAPPS`, `A221-ACGN`, campus, department, and faculty.
12. Confirm leadership reports show no student names, individual grades, or rankings.
13. Open View Details and confirm it contains aggregate component/category and activity setup only.
14. Log in as `test-insights-cao`; confirm the same three campuses are visible only because the TEST CAO has explicit role assignments for them.
15. Disable the feature and confirm navigation disappears and direct Insights URLs return 404.
16. Re-enable it and confirm reports return.
17. Run the seed command again and confirm counts do not increase.
18. When testing is complete, run the cleanup command and confirm official courses and unrelated records remain.

### Academic Performance Insights Review Validation
- [x] Demo seed command safety/idempotency/scenario/cleanup tests - passed, 3 tests.
- [x] Existing Academic Performance Insights tests - passed, 14 tests.
- [x] Admin analytics/scope integration group - passed, 88 tests.
- [x] `python manage.py check` - passed.
- [x] `python manage.py makemigrations --check --dry-run` - passed; no migration required.
- [x] Local rendered-response validation passed for CSS bars, table fallbacks, privacy, statuses, campus summaries, and read-only record comparison.
- [x] `python manage.py test --parallel 4` - passed, 535 tests in 918.628 seconds.
- [i] An earlier serial full-suite run exceeded the command window; the complete parallel suite subsequently passed.
- [ ] In-app browser screenshot review was unavailable because no browser target was connected.

- Local Academic Performance Insights activation/test:
  - Investigated why user `ac` could not see Academic Performance Insights on the development machine.
  - Confirmed migrations `navigation.0006` and `rbac.0015` are applied, the menu item is active, and `ac` has both `admin_portal.access` and `grading_analytics.read`.
  - Found the NCBA tenant had no saved `FEATURE_ACADEMIC_PERFORMANCE_INSIGHTS_ENABLED` setting, so the effective value was False even though the UI was believed to have been enabled.
  - Saved the NCBA tenant setting as True in the local development database.
  - Verified as `ac` that the Admin dashboard displays Academic Performance Insights and the direct report page returns HTTP 200.
  - Verified the existing local test data under NCBA-02: Academic Year `2025-2026`, Term `2ND`, Period `MIDTERM`, Course `A132-ITAPPS`.
  - Verified Course Performance returns one section (`BSA 1-BSA_1A`) with class average `82.76`, one at-risk student, and zero missing outputs.
  - Verified Activity Consistency shows the correct one-section message because there is no second section with the same course code in the selected scope.
  - No source code, grading formula, score, grade, submission, or lock record was changed during this local configuration check.

- Academic Performance Insights:
  - Added the shared Admin Portal pages:
    - `/admin-portal/grading/performance-insights/`
    - `/admin-portal/grading/performance-insights/activity-consistency/`
    - `/admin-portal/grading/performance-insights/classes/<offering_id>/<period_code>/`
  - Added Course Performance by Section with no more than four summary cards: Sections in Scope, Overall Average, At-Risk Students, and Missing Outputs.
  - Added Activity Consistency for same-course sections in the same academic year, term, and grading-period code.
  - Added Dean/CAO Campus Summary when the signed-in role is allowed to compare authorized campuses.
  - Added section setup detail with component averages and active activity setup only. It contains no student identity or individual student grade.
  - Added required Academic Year, Term, and Grading Period filters plus optional authorized campus, department, course, and supervised-faculty filters.
  - Added friendly states for missing filters, unavailable periods, empty scope, one-section comparisons, incomplete setup, and the 100-offering live-report cap.
  - Reused `FacultyPerformanceService.get_class_performance_snapshot()` and its official `FacultyGradingService.build_period_grade_detail_for_student()` computation path.
  - Missing Outputs counts unencoded activity-score records only. Attendance is excluded and a saved zero remains encoded.
  - Added deterministic activity labels: 0 difference is `Consistent`, 1 is `Minor Difference`, 2 or more is `Needs Review`, and a required active component with no activity is `Incomplete Setup`.
  - Added lightweight CSS bars, responsive tables, exact values, and rule-based text. No chart package, AI, external API, analytics model, or snapshot table was added.
  - Added `FEATURE_ACADEMIC_PERFORMANCE_INSIGHTS_ENABLED`, default Off. A Superadmin enables it from `Tools -> Configuration Management -> Configurable Features`.
  - Disabled tenants have no menu/dashboard action and direct Insights URLs return 404.
  - Reused `grading_analytics.read`. Area Chair filters/data are localized to the active campus; College Dean and CAO cross-campus options remain limited to their existing authorized scope.
  - Added navigation migration `navigation.0006_seed_academic_performance_insights_menu`.
  - Added RBAC migration `rbac.0015_grant_cao_grading_analytics_read`.
  - Analytics are read-only and do not create or alter grades, scores, attendance, submissions, locks, or gradebook records.

### Academic Performance Insights Manual Test
1. Run `python manage.py migrate`.
2. Log in as Superadmin and open `Tools -> Configuration Management -> Configurable Features`.
3. Enable `Academic Performance Insights` and save.
4. Log in as an Area Chair with `grading_analytics.read`; confirm `Grading -> Academic Performance Insights` is visible.
5. Open the report without filters; confirm it asks for Academic Year, Term, and Grading Period.
6. Select all required filters and generate the report; confirm the page shows aggregate section data and no student names, grades, or rankings.
7. Confirm an Area Chair sees only the active campus and authorized departments in filters and results.
8. Log in as a College Dean assigned to authorized departments across two campuses; choose `All authorized campuses` and confirm only those scoped campuses appear.
9. Log in as CAO; confirm results remain limited to existing CAO role assignments rather than becoming globally unrestricted.
10. Open Activity Consistency and verify same-course section labels for 0, 1, and 2+ activity-count differences.
11. Remove all active activities from a required non-attendance component in test data; confirm `Incomplete Setup`.
12. Save a score of `0`; confirm it is not counted as a Missing Output.
13. Leave one required activity score unencoded; confirm Missing Outputs increases while attendance has no effect on that count.
14. Open a section detail; confirm only aggregate component results and activity setup are shown.
15. Remove `grading_analytics.read` from a test role; confirm access is denied.
16. Disable the feature; confirm the menu/dashboard action disappears and direct URLs return not found.
17. Compare grade, score, attendance, submission, and lock record counts before and after viewing reports; confirm none changed.

### Academic Performance Insights Validation
- [x] `python manage.py test apps.admin_portal.tests_academic_performance_insights` - passed, 14 tests.
- [x] Admin scope/analytics integration group - passed, 85 tests.
- [x] `python manage.py check` - passed.
- [x] `python manage.py makemigrations --check --dry-run` - passed; no changes detected.
- [x] `python manage.py migrate` - applied `navigation.0006` and `rbac.0015` in the development database.
- [x] `python manage.py test` - passed, 532 tests in 1467.967 seconds.
- [ ] Authenticated browser smoke test remains pending because no callable in-app browser target was available in this session.

### Academic Performance Insights Known Limitations
- Phase 1 computes reports live and limits processing to the first 100 authorized offerings. It has no cached analytics snapshots or historical report archive.
- Missing Outputs covers activity-score records only; attendance is intentionally excluded.
- Activity Consistency compares active activity counts and required-component coverage. Maximum-score range is informational and activity titles are not used as consistency keys.
- College Dean scope is inferred from existing active role/campus/department assignments; there is no separate Dean-to-Area-Chair reporting-line model.
- The reports do not provide student drill-down, faculty ranking/evaluation, cross-faculty comparison, or advanced statistics.
- CAO permission migration grants `grading_analytics.read` to an existing active `CAO` role. Deployments that create the role later must seed/grant the permission through normal RBAC setup.

### Academic Performance Insights Next Recommended Phase
- After production usage confirms the Phase 1 definitions, consider optional scheduled aggregate snapshots/export history and a separate attendance indicator. Keep both behind explicit configuration and preserve the current role scope.

- Area Chairman scope/configuration review:
  - Confirmed user `ac` has active `AREA_CHAIR` assignments for NCBA-01/INFOSYS, NCBA-02/INFOSYS, and NCBA-03/CS. The account default is NCBA-03/CS.
  - Confirmed the role already grants the required read permissions for Sections, Course Offerings, Course Template Assignments, Course Base Overrides, Grading Analytics, Grade Distribution Monitor, and Faculty Assignments.
  - Confirmed I-AM GURO (`faculty`) has active Faculty roles for NCBA-01/INFOSYS and NCBA-02/INFOSYS, with NCBA-02/INFOSYS as the account default.
  - Confirmed I-AM GURO's accepted A132-ITAPPS class is owned by NCBA-02/COLLEGE, not INFOSYS. Its BSA program and BSA 1-BSA_1A section are also under COLLEGE.
  - Confirmed no active programs, sections, offerings, or department-owned courses currently exist under NCBA-01/INFOSYS, NCBA-02/INFOSYS, or NCBA-03/CS. Fairview's 13 active programs, 101 sections, and 403 offerings are currently attached to COLLEGE.
  - Confirmed the A132-ITAPPS Course Template Assignment uses template `BSA_1ST_3RD_REGULAR`, but that template is visible to BSBA departments, not INFOSYS. This causes the Area Chairman's Course Template Assignment list to exclude the row even though the shared course itself is in scope.
  - Confirmed there are no Course Base Value Override records in the current database; a blank page is expected and is not a required setup unless an approved course exception exists.
  - Confirmed Grading Analytics follows offering/section/program department ownership and therefore has no INFOSYS-owned offering to analyze.
  - Confirmed Grade Distribution follows accepted faculty assignments: it returns three rows when the current scope is NCBA-01/INFOSYS or NCBA-02/INFOSYS, and zero rows under the account's default NCBA-03/CS scope because I-AM GURO has no Taytay faculty assignment.
  - No code, permissions, grading formulas, or database records were changed during this review.

- Department-scoped grading templates:
  - Added `GradingTemplate.department_visibility` with `All Departments` and `Selected Departments`.
  - Added the `visible_departments` many-to-many relation.
  - Existing templates default to `All Departments` through migration `grading.0028_gradingtemplate_department_visibility_and_more`.
  - Added centralized `GradingTemplateAccessService` helpers for active department resolution, queryset filtering, object access, and permission-aware governance checks.
  - Department matching follows existing parent/child scope expansion and excludes Faculty-only role assignments from Admin template visibility.
  - Applied centralized filtering to template lists, builder, structure, edit, duplicate, calculator, nested structure maintenance, hotfix queues/actions, Tenant Grading Profile dropdowns, Course Template Assignment dropdowns, and related direct URLs.
  - Added department-access checks inside template approval, publish, and hotfix workflow guards.
  - Template duplication now preserves visibility mode and selected departments.
  - Added form validation requiring at least one same-tenant department in Selected mode.
  - Added visibility badges/summaries to template list, builder, structure preview, approval review, and hotfix review.
  - Updated the Admin Grading Template Setup Guide with visibility and duplication guidance.
  - Faculty grade computation, template resolution, scores, submissions, locks, and existing gradebooks were not changed.
  - Review hardening completed:
    - Tenant Grading Profile form help text now resolves a submitted template only through its already-scoped field queryset, so a forged hidden template ID cannot expose active period codes.
    - Course Template Assignment coverage metrics now consider only templates visible to the current Admin account.
    - Bulk assignment still respects an existing hidden prior assignment, but its warning uses a generic message and does not disclose the hidden template name.
    - Added explicit tests for cross-tenant department form data, stale M2M clearing, inactive departments, parent-child scope, permission/department dual requirements, nested direct URLs, forged parent IDs, forged calculator/profile/assignment IDs, governance queues, authorized submit/review/publish/hotfix actions, and superadmin selectors.
  - Visible Department labels now include campus and department context in the format `Campus Code - Campus Name | Department Code - Department Name`.

### Department Visibility Manual Test
1. Log in as Superadmin and open `Grading -> Grading Templates`.
2. Create or edit a template and choose `All Departments`; confirm authorized tenant users retain existing visibility.
3. Change it to `Selected Departments`, choose Department A, and save.
4. Log in as an authorized Dean/Area Chair assigned to Department A; confirm the template appears.
5. Confirm Department A can open Builder, Structure, Calculator, and governance actions only when the required RBAC permission and workflow role are also assigned.
6. Log in as the equivalent Department B user; confirm the template is absent.
7. As Department B, try direct Builder, Structure, Edit, Duplicate, Publish, Approval, and Hotfix URLs; confirm 404/not-found.
8. Open Tenant Grading Profile and Course Template Assignment create pages; confirm Department B's hidden template is absent from dropdowns.
9. Assign one user to Departments A and B; confirm that user sees selected templates from both departments.
10. Deactivate a department role assignment; confirm it no longer grants selected-template visibility.
11. Duplicate a Selected Departments template; confirm the draft copy keeps the same selected departments.
12. Confirm Superadmin still sees every template.
13. Open an existing faculty class using the template and confirm score encoding, Summary, submission state, and locked grades are unchanged.
14. As Department B, try direct Period, Component, Subcomponent, and Detail edit URLs under Department A's template; confirm 404/not-found.
15. As Department B, submit Department A's hidden template ID to Calculator, Tenant Grading Profile, and Course Template Assignment forms; confirm the forms reject it.
16. Confirm a rejected hidden Tenant Grading Profile ID does not reveal the hidden template's period codes.
17. Confirm a course with a hidden prior template assignment does not reveal that template's name in warnings or coverage counts.
18. Deactivate a selected department and confirm it no longer grants template visibility.
19. Confirm an active parent-department role can access a selected active child-department template, while a child role does not automatically cover its parent.
20. Record grade, score, submission, and lock data before and after the checks; confirm no faculty gradebook record changed.

- Detail-item weight visibility:
  - Admin detail list now always displays the stored percentage instead of replacing it with an averaging message.
  - Template Builder and structure preview show `Configured Detail Weight`.
  - Test Calculator keeps the percentage visible and labels it reference-only under Average Activities.
  - Faculty template preview shows each detail percentage and its parent detail-computation mode.
  - Faculty activity selectors, activity list, and score-entry header display the chosen detail percentage.
  - Faculty Summary nested detail headers display the configured percentage.
  - Grade Explanation Activity Details and Student Consultation Current Period Breakdown display detail percentages.
  - Faculty and Admin correction screens display percentages beside selected detail items.
  - Admin detail-level analytics includes a Detail Weight column.
  - Under Average Activities, every affected page explains that the stored percentage is for reference and is not used in equal activity averaging.
  - Weighted Details continues to use the configured percentages.
  - Added no model, migration, grade formula, score, submission, correction-posting, or lock behavior change.

### Detail Weight Visibility Manual Test
1. Open an Admin grading template containing detail items.
2. Check the detail list, Builder, structure preview, and Test Calculator; confirm every detail shows its configured percentage.
3. For an Average Activities subcomponent, confirm the percentage is labeled reference-only or not used in the average.
4. Log in as assigned faculty and open the class Template page.
5. Open Activities and confirm detail dropdown options and saved activity rows show the configured percentage.
6. Open Encode Scores and confirm the selected detail percentage appears in the activity header.
7. Open Summary and confirm nested detail headers show their configured percentages.
8. Open Explain beside a grade and confirm Activity Details shows configured detail percentages.
9. Open Class Performance, select a student, and confirm Current Period Breakdown shows detail percentages when details exist.
10. Open Faculty Corrections and Admin correction review/on-behalf correction; confirm detail percentages appear.
11. Open Admin Grading Analytics and confirm the detail-level table includes Detail Weight.
12. Compare one Average Activities class and one Weighted Details class; confirm computed grades are unchanged.

- Tenant Grading Profile final-grade clarification:
  - Updated the Admin Grading Template Setup Guide to state explicitly that the profile's main purpose is to control how grading-period grades are combined into the official final grade.
  - Documented the actual fallback: when no active profile matches, TeacherMate+ averages every active period in the resolved published template.
  - Added the NCBA Regular example: one four-period template may serve both 1st and 2nd Semester when their structures are identical.
  - Added the NCBA Summer example: use a separate three-period template containing Midterm, Pre-Final, and Final.
  - Recommended Regular and Summer profiles using `Average All Active Template Periods` for explicit term-type governance.
  - Clarified that exact-term Course Template Assignments are checked before profiles when selecting the template.
  - Added a warning that a four-period Regular template must not be used for Summer when Prelim should be excluded.
  - Updated the detailed Tenant Grading Profile setup document and focused Admin guide tests.
  - No grading formula, model, form, permission, URL, or database behavior changed.

### Tenant Grading Profile Guide Manual Test
1. Open `/admin-portal/guide/grading-template-setup/`.
2. Open section `6. When to Create a Tenant Grading Profile`.
3. Confirm the main purpose says that period grades are combined into the official final grade.
4. Confirm the no-profile explanation says all active template periods are averaged.
5. Confirm the Regular example shows Prelim, Midterm, Pre-Final, and Final divided by four.
6. Confirm the Summer example shows Midterm, Pre-Final, and Final divided by three.
7. Confirm the guide says 1st/2nd Semester may share one Regular template only when their structures match.
8. Confirm the warning says not to use a four-period Regular template for Summer when Prelim must be excluded.

### Validation For Tenant Grading Profile Guide Clarification
- [x] `python manage.py test apps.admin_portal.tests_help_guide` - passed, 15 tests.
- [x] `python manage.py check` - passed.
- [x] `python manage.py makemigrations --check --dry-run` - passed; no changes detected.
- [ ] Desktop/mobile browser review remains pending because no in-app browser target was available in this session.

### Validation For Detail Weight Visibility
- [x] `python manage.py test apps.admin_portal.tests_template_governance apps.admin_portal.tests_template_calculator` - passed, 16 tests.
- [x] `python manage.py test apps.faculty_portal.tests_performance` - passed, 26 tests.
- [x] Focused Faculty activity/Summary detail-weight tests - passed, 2 tests.
- [x] `python manage.py check` - passed.
- [x] Python compilation passed for the changed service, form, and view modules.
- [ ] Full browser smoke test remains pending because no in-app browser target was available; manually verify wide Summary/correction tables at desktop and mobile widths.

- Admin Grading Template Setup Guide plain-English rewrite:
  - Rewrote all eight existing sections without removing any section.
  - Replaced long technical sentences with short, direct admin instructions.
  - Simplified table headings and row explanations.
  - Added a template example: MIDTERM -> Class Standing -> Participation/Output -> Recitation, Assignment/Activities, and Oral Presentation.
  - Added score examples for Inherit Parent Rule, a 42/50 Raw Score Base-50 quiz, and an approved 85% Direct Percentage entry.
  - Added a Weighted Details example using Participation/Output at 60% with Recitation 20%, Assignment 30%, and Oral Presentation 50% inside it.
  - Added an Average Activities example where R1, R2, Assignment 1, Seatwork 1, and Oral 1 are averaged equally.
  - Kept the required detail-weight guidance: enter positive detail percentages totaling 100% for a complete setup, although Average Activities ignores those detail percentages.
  - Simplified the CAO submission rule: at least one active Participation/Output activity is required; unused or inactive detail rows do not block; required active-activity scores still apply; zero is valid; Weighted Details remains strict.
  - Added a BSA program Tenant Grading Profile example and a course-specific Base-40 override example.
  - Simplified warnings for published/live templates and conflicting grading profiles.
  - Added no backend logic, grading computation, validation, permission, layout, dependency, or migration change.
  - Expanded tests to preserve all eight sections, examples, CAO policy meaning, and removal of difficult jargon.

### Grading Setup Guide Wording Manual Test
1. Log in as an Admin user with `grading_templates.read`.
2. Open `/admin-portal/guide/grading-template-setup/`.
3. Confirm all eight sections still appear.
4. Read the MIDTERM structure example and confirm the levels are easy to follow.
5. Confirm Raw Score, Inherit Parent Rule, and Direct Percentage include simple examples.
6. Confirm the Weighted Details percentages total 100%.
7. Confirm Average Activities says active activities are averaged equally.
8. Confirm the guide says the detail form still needs positive percentages totaling 100%, although averaging ignores them.
9. Confirm the Participation/Output submission warning includes at least one active activity, unused details, missing required scores, valid zero, and strict Weighted Details checks.
10. Confirm the Tenant Grading Profile and Course Base Value Override examples are clear.
11. Check the tables at desktop and mobile widths for wrapping and horizontal scrolling.
12. Confirm no template syntax, layout, or browser-console error appears.

- Admin Portal Practical Guide redesign:
  - Renamed the active role-filtered guide from `Admin Portal Help Guide` to `Admin Portal Practical Guide` in the page title, heading, view context, return links, tests, and documentation.
  - Kept the hero, introduction, guide actions, and permission/scope notice immediately visible.
  - Added a visible three-step `How to Use This Practical Guide` overview.
  - Converted the existing permission-filtered groups into one Bootstrap 5 accordion:
    - Start Here
    - Academic and Class Setup
    - Grading Setup
    - Submission, Reopening, and Corrections
    - Reports and Monitoring
    - Accounts and Access
    - Superadmin System Control, when the current account is allowed to see it
  - Opens the first available group by default and keeps later groups collapsed.
  - Reused the Faculty Help Guide deep-link pattern so shortcut and legacy hash links open the correct accordion before scrolling.
  - Preserved all existing topic content, menu paths, actions, workflows, role filtering, and anchor IDs.
  - Strengthened tables with deep-green headers, green/cream banding, editability highlights, hover feedback, and responsive horizontal scrolling.
  - Strengthened topic cards, menu-path panels, step callouts, and workflow callouts with consistent TeacherMate+ green/gold styling.
  - Added no frontend dependency, backend business rule, permission change, or database migration.
  - Added a focused regression for the new practical title, visible overview, accordion IDs/controls, first-open state, responsive table wrapper, and Bootstrap deep-link helper.

### Admin Practical Guide Manual Test
1. Log in as an Admin Portal user.
2. Open `/admin-portal/guide/`.
3. Confirm the heading reads `Admin Portal Practical Guide`.
4. Confirm the hero, guide buttons, permission notice, and usage overview remain visible.
5. Confirm `Start Here` is open and later available sections are collapsed.
6. Open and close every visible accordion group.
7. Use a topic shortcut and confirm its owning group opens.
8. Open a legacy deep link such as `#grading-template-calculator` and confirm the Grading Setup group opens and scrolls to the topic.
9. Confirm action tables use stronger colors and remain readable.
10. Resize to tablet and mobile widths and confirm tables scroll horizontally without breaking the page.
11. Log in as Campus Admin and confirm Superadmin System Control remains hidden.
12. Confirm the browser console has no errors when browser tools are available.

- Dedicated Grading Template Setup Guide:
  - Added `/admin-portal/guide/grading-template-setup/`, protected by Admin Portal access and `grading_templates.read`.
  - Added step-by-step instructions for creating a template and building Template -> Period -> Component -> Subcomponent -> Detail.
  - Documented score-entry choices: Inherit Parent Rule, Raw Score Base-50, and the restricted/approved-use case for Direct Percentage.
  - Documented `Weighted Details` versus `Average Activities`.
  - Clarified that Average Activities ignores detail-row weights, but the Participation/Output subcomponent weight still applies upward.
  - Clarified that the detail form still requires a numeric value; the guide recommends an equal distribution totaling 100% for clean records even though averaging mode ignores those detail weights.
  - Added the post-template workflow: Calculator, approval, publish, Tenant Grading Profile decision, Course Template Assignment, optional Course Base Override, coverage check, and sample faculty-class verification.
  - Clarified that Tenant Grading Profiles are based on distinct scope/formula needs rather than automatically one profile per template.
  - Documented Base-50 precedence: Course Base Override -> matching Tenant Grading Profile -> Course default -> Template default -> system default 50.
  - Linked the guide from the Admin Practical Guide, Full Admin Guide, Grading Templates list, Template Builder, Tenant Grading Profiles, and Course Base Overrides.
  - Added tests for guide navigation, required content, and denial without `grading_templates.read`.

### Grading Setup Guide Manual Test
1. Log in as an Admin user with `grading_templates.read`.
2. Open `/admin-portal/guide/` and click `Grading Template Setup Guide`.
3. Confirm `/admin-portal/guide/grading-template-setup/` opens.
4. Check the structure, score-entry, detail-computation, profile, and override sections.
5. Confirm Average Activities says detail weights are ignored but the subcomponent weight still matters.
6. Open `Grading -> Grading Templates` and confirm `Setup Guide` is visible.
7. Open a template Builder and confirm its `Setup Guide` link works.
8. Open Tenant Grading Profiles and Course Base Overrides and confirm their guide links open the correct anchored sections.
9. Test at desktop, tablet, and mobile widths; confirm tables scroll horizontally without clipping.
10. Log in as an Admin user without `grading_templates.read` and confirm the direct guide URL returns permission denied.

- Practical/full Admin guide navigation:
  - Added `Open Full Admin Guide` to the role-based practical guide.
  - Added `Back to Practical Guide` to the legacy/full Admin guide.
  - Added explicit `?view=full` and `?view=practical` rendering overrides without changing the saved tenant-level default-guide setting.
  - Confirmed Campus Admin users may open the full general guide but still cannot see the Superadmin-only Production Incident Response section.
  - Added regressions for practical-to-full navigation, full-to-practical navigation, practical override when the tenant default is legacy, and Superadmin incident-section isolation.
- Admin Practical Guide operational instructions:
  - Confirmed the active guide is generated from `apps/admin_portal/help_guide.py` and rendered by `templates/admin_portal/guide_role_based.html`.
  - Added a visible `Where to start` block and numbered `How to open and use this page` instructions for every major role-based Admin guide topic.
  - Grade Formula Setup now directs authorized users to `Admin Portal -> Grading -> Grading Templates` and names the actual `Add Template`, `Builder`, `Add Period`, component, subcomponent, detail, `Test Calculator`, approval, and publish actions.
  - Added Participation/Output setup guidance that directs admins to set the subcomponent's Detail Computation to `Average Activities` when required by policy.
  - Removed the stale Direct Percentage warning from the active Admin guide and retained Raw Score Base-50 wording.
  - Added a dedicated permission-filtered `Change a Published Template Using a Hotfix` topic.
  - Hotfix guidance covers `Grading Templates -> Hotfix`, `Template Hotfix Requests`, all four apply modes, Selected Offerings, academic justification, impact preview, configured review steps, the typed `APPLY HOTFIX` confirmation, affected/recomputed counts, and skipped offerings.
  - Clarified that selected hotfix scope controls immediate recomputation but does not create a separate per-offering copy of the shared template.
  - Clarified that eligible unsubmitted offerings may be recomputed while official submitted grades require Correction of Grades.
- Added guide regressions for exact menu paths, Builder/Average Activities instructions, removal of Direct Percentage wording, hotfix content visibility with permission, and hotfix content isolation without permission.

### Validation For Grading Setup Guide
- [x] `python manage.py test apps.admin_portal.tests_help_guide` - passed, 13 tests.
- [x] `python manage.py check` - passed.
- [x] `python manage.py makemigrations --check --dry-run` - passed; no changes detected.
- [ ] Live browser smoke test - not completed because the in-app browser target was unavailable.

### Validation For Admin Practical Guide Accordion
- [x] `python manage.py test apps.admin_portal.tests_help_guide` - passed, 14 tests.
- [x] `python manage.py check` - passed.
- [x] `python manage.py makemigrations --check --dry-run` - passed; no changes detected.
- [ ] `python manage.py test` - completed 478 tests; 472 passed, with five existing Configurable Features/Faculty Final Clearance failures and one existing Faculty Final Clearance error unrelated to this guide change.
- [ ] Desktop/mobile browser smoke test - attempted, but no in-app browser target was available.

### Validation For Grading Setup Guide Wording
- [x] `python manage.py test apps.admin_portal.tests_help_guide` - passed, 15 tests.
- [x] `python manage.py check` - passed.
- [x] `python manage.py makemigrations --check --dry-run` - passed; no changes detected.
- [x] Difficult-term scan found no `governed fallback`, `source of truth`, `resolves the rule upward`, `parent contribution`, `computation behavior`, `strict validation`, or `governed Hotfix` wording in the guide template.
- [ ] Desktop/mobile browser review remains pending because no in-app browser target was available in this session.

### Admin Practical Guide Manual Test
1. Log in as an Admin Portal user with `grading_templates.read`.
2. Open `/admin-portal/guide/`.
3. Click `Open Full Admin Guide` and confirm the URL includes `?view=full`.
4. Click `Back to Practical Guide` and confirm the URL includes `?view=practical`.
5. Open `Grading Setup -> Grade Formula Setup`.
6. Confirm `Where to start` shows `Admin Portal -> Grading -> Grading Templates`.
7. Confirm the numbered steps name Add Template, Builder, Add Period, components, subcomponents, details, Average Activities, Test Calculator, approval, and publish.
8. Log in as a user with effective template hotfix permission.
9. Confirm `Change a Published Template Using a Hotfix` is visible and names the Hotfix icon and Template Hotfix Requests menu.
10. Confirm the topic explains apply modes, impact review, `APPLY HOTFIX`, skipped submitted offerings, and Correction of Grades.
11. Log in as an Admin user without any template hotfix permission and confirm the hotfix topic is hidden.
12. Open `?view=full` as Campus Admin and confirm `Production Incident Response` is absent.
13. Check both guide views at desktop and mobile widths for wrapping and horizontal action-table scrolling.

- Participation/Output Average Activities submission policy:
  - Confirmed the central readiness gate is `GradingGovernanceService.evaluate_submission_readiness()` and its `_template_activity_requirements()` helper in `apps/grading/services.py`.
  - Changed only template-coverage validation for Participation/Output subcomponents using `Average Activities`.
  - Such a subcomponent now requires at least one active faculty-created activity anywhere under its active detail rows; unused detail rows no longer create separate blockers.
  - Review correction: active activities linked to inactive component/subcomponent/detail rows are excluded so readiness matches the official computation service's active-template filtering.
  - TeacherMate+ has no separate activity `selected` field. For this policy, a selected/usable item is an active `GradeActivity` linked to the relevant offering, period, active Participation/Output hierarchy, and active detail.
  - Preserved the existing strict per-detail coverage policy for `Weighted Details` and for non-Participation/Output subcomponents, even if they use the same computation-mode value.
  - Preserved student-level completeness checks for every active activity, attendance requirements, deadline/lock checks, and all unrelated submission blockers.
  - Confirmed encoded raw score `0.00` is recognized as an existing score record and is not treated as blank.
  - Confirmed invalid zero-total detail weights remain blocked by existing grading-template publication validation; this readiness change does not replace template governance.
  - Added regressions for zero active averaging items, inactive activities/details, one active averaging item with an unused detail, actual successful submission, weighted missing/valid/zero-score behavior, invalid zero-total weighted setup, non-Participation/Output strictness, blank student records, read-only readiness, accepted-assignment access, cross-faculty denial, and unchanged averaging computation.

### Participation/Output Submission Policy Manual Test
1. Log in with a Faculty account and open an accepted assigned class.
2. Open a period whose Participation/Output subcomponent uses `Average Activities`.
3. Leave all Participation/Output activities absent or inactive and confirm submission readiness reports the Participation/Output requirement as missing.
4. Create one valid active Participation/Output activity under an active detail and encode all required student scores.
5. Confirm the Participation/Output template-coverage blocker clears.
6. Leave other configured Participation/Output detail rows unused and confirm they do not block averaging-mode submission.
7. Encode a saved raw score of `0` for one student and confirm it is not reported as blank.
8. Leave another ACTIVE student's score blank for the active activity and confirm submission remains blocked by the existing missing-score rule.
9. Deactivate the only Participation/Output activity, or its detail row, and confirm it no longer qualifies for readiness.
10. Open a class using `Weighted Details` for Participation/Output and confirm every required active detail still needs its activity setup.
11. Confirm a weighted active activity with a missing student score still blocks, while a complete weighted setup including a saved zero can submit.
12. Confirm missing exam/component activities, attendance gaps, locks, deadlines, submitted status, and unaccepted faculty assignments still block through their existing rules.
13. Compare the computed grade before and after this deployment using the same scores and confirm the computation result is unchanged.
- Faculty Help Guide readability redesign:
  - Kept the Daily Faculty Workflow visible and renamed it `Start Here: Daily Faculty Workflow`.
  - Added a six-item `Top Faculty Tasks` strip for My Classes, score encoding, pending issues, computed-grade review, submission, and Student Consultation.
  - Converted the five existing detailed content families into accessible Bootstrap accordion groups without removing topic content or breaking existing topic anchors.
  - Kept the first accordion group open by default and collapsed the other four.
  - Added deep-link handling so topic and navigation links open the correct accordion group.
  - Restyled topic sections with stronger deep-green headings, neon-green accents, controlled gold callouts, better spacing, and responsive single-column behavior.
  - Restyled action tables with deep-green headers, green/cream row bands, highlighted action and editability columns, stronger borders, row hover, semantic scopes, and narrow-screen horizontal scrolling.
  - Added focused Student Consultation guidance for Current Grade, Trend, Missing Outputs, Weakest Component, Performance Trend, Component Average Trend, and Current Period Breakdown.
  - Added focused Parallel Section Comparison guidance for lowest average, missing outputs, at-risk count, weakest component, comparison tables, lightweight bars, and rule-based interpretation.
  - Used the Bootstrap 5.3 accordion already loaded by the Faculty Portal; no frontend dependency or grading behavior changed.
- Faculty Help Guide and Full Faculty Manual:
  - Added a visible `Open Full Faculty Manual` action to the active role-based guide at `/faculty/guide/`.
  - Added a dedicated `Recommended Daily Faculty Workflow` section to `/faculty/guide/` using `media/imahe/faculty_workflow.png`, with responsive image sizing and a caption clarifying blank scores and pre-submission checks.
  - Expanded `/faculty/guide/manual/` with current guidance for Gradebook Essentials, checking and explaining grades, Class Performance, selected-student consultation trends, and Parallel Section Comparison.
  - Documented blank versus zero scores, Raw Score Base-50, equal Participation/Output item averaging, Summary abbreviations, privacy shielding, official rounded grades, and read-only analytics.
  - Removed Direct Percentage and individual Participation/Output detail-weight guidance from the active guide, reversible legacy guide, and Full Faculty Manual.
  - Clarified with a concrete example that Recitation 1, Recitation 2, Assignment 1, Assignment 2, Seatwork 1, and Seatwork 2 each count equally in the Participation/Output average; parent component and period weights still apply.
  - Corrected stale manual wording that said blank scores were saved as zero.
  - Corrected stale deadline wording so an overdue locked unsubmitted gradebook directs faculty to `Request Gradebook Reopen` and wait for approval.
  - Added focused tests for the manual link, workflow image, and updated Full Faculty Manual content.
- Student Consultation graph post-implementation review:
  - Verified the graph is rendered only by `templates/faculty_portal/student_performance_consultation.html` at the selected-student consultation URL.
  - Confirmed `Performance Trend` appears after Current Grade, Trend, Missing Outputs, and Weakest Component, and before Primary Reason and Current Period Breakdown.
  - Confirmed Period Grade Trend includes the current period and earlier periods from the same offering/template, while periods after the selected period are not queried or displayed.
  - Confirmed component, subcomponent, and configured detail labels come from the active grading template rather than a hard-coded category list.
  - Confirmed mismatched components between periods and missing values are represented as gaps or `No data` without breaking the page.
  - Added an exact-value Period Grade table fallback below the inline SVG; component values continue to use their responsive table plus inline SVG sparklines.
  - Added a regression proving the graph does not render on the Faculty Dashboard or all-student Class Performance page.
  - Reconfirmed accepted-assignment scoping, active-enrollment scoping, selected-student privacy, deterministic interpretation, and standard 404 handling for another faculty member.
  - Reconfirmed graph generation is read-only and calls `FacultyGradingService.build_period_grade_detail_for_student()` for every included period without persisting recalculation results.
  - Confirmed no Chart.js, Recharts, ApexCharts, AI, external API, analytics model, or frontend chart dependency is used.
- Class Performance grade explanation and Student Consultation graphs:
  - Added `Explain` beside each available Current Grade without adding an extra attention-table column.
  - Reused the existing privacy-protected `Explain This Grade` modal through a shared Faculty template partial.
  - Added a `Performance Trend` section below the four consultation summary cards and before Primary Reason.
  - Added a responsive inline SVG Period Grade Trend and compact inline SVG component/subcomponent/detail sparklines.
  - Used actual period, component, subcomponent, and configured detail names from the assigned grading template; no grading category names are hard-coded.
  - Added deterministic selected-student interpretation and friendly states for one period, no computed grade, missing period values, and unavailable component data.
  - Added `get_student_period_grade_trend()`, `get_student_component_trend()`, `get_student_trend_interpretation()`, and `get_student_trend_visualization()` plus private SVG-data helpers under `FacultyPerformanceService`.
  - Reused `FacultyGradingService.build_period_grade_detail_for_student()` for every available period through the selected period.
  - Confirmed the feature is read-only: it does not create, update, submit, unlock, or persist grade, score, attendance, submission, or lock records.
  - Added focused tests for Explain/modal rendering, official-service reuse, actual template labels, selected-student privacy, inline SVG output, one-period and missing-component states, access isolation, and no grade mutation.
  - Updated the Faculty Help Guide, CHANGE_LOG, project context, and this handoff.
- Faculty Performance post-implementation review:
  - Verified the main dashboard contains no student names, individual alerts, follow-up lists, rankings, or student comparisons.
  - Removed the retired hidden dashboard markup so Students Needing Follow-up, Student Support, and Priority Actions no longer remain as dead template UI.
  - Limited the Class Performance attention table to Student, Current Grade, Missing Outputs, Trend, and Primary Reason. The student name now opens Consultation View without an extra Action column.
  - Limited Parallel Section Comparison to Section, Class Average, At-Risk Students, Missing Outputs, and Weakest Component; removed its extra Action column.
  - Tightened comparison filter choices to explicitly accepted assignments and excluded sections that do not contain the selected grading-period code.
  - Corrected raw-score base-50 empty handling so a completely unencoded class shows no performance data instead of a misleading floor average or at-risk count.
  - Added friendly states for no active students, no encoded scores, incomplete grading setup, no previous grading period, and one-section comparison.
  - Counted missing activity-score records as Missing Outputs without relabeling attendance gaps as outputs.
  - Confirmed trend priority and configured passing-threshold reuse, private consultation isolation, cross-term/course/faculty exclusion, and submitted/locked read-only safety.
  - Expanded `apps/faculty_portal/tests_performance.py` from 10 to 19 focused regressions.
- Faculty Dashboard and performance trends:
  - Replaced the rendered duplicate shortcut/action-card layout with one Grade Encoding Status table and one Pending Grade Issues panel.
  - Removed the rendered Students Needing Follow-up card/panel, Student Support shortcut, incomplete-student KPI, and at-risk priority action from the main dashboard.
  - Kept student names and student-level attention details off the dashboard.
  - Added read-only `FacultyPerformanceService` functions for class snapshots, student trends, students requiring attention, parallel-section discovery/comparison, interpretation, and JSON-ready chart data.
  - Reused `FacultyGradingService.build_period_grade_detail_for_student()` for current and previous-period values; no grading formula was copied.
  - Applied trend precedence `AT_RISK`, `INCOMPLETE`, `NO_BASELINE`, `IMPROVING`, `DECLINING`, `STABLE`.
  - Added Class Performance, Student Consultation, and Parallel Section Comparison pages and routes.
  - Added same-faculty/same-course/same-term/same-period ownership filters and existing accepted-assignment access checks.
  - Added table fallback, lightweight CSS bars, and deterministic comparison messages; no chart library or external dependency was added.
  - Updated period-card navigation, sidebar navigation, quick tour text, revised/legacy Faculty guides, CHANGE_LOG, and project context.
  - Added 10 focused performance tests and updated dashboard regressions to the approved privacy-focused behavior.
  - Confirmed analytics GET requests do not create/update `StudentPeriodGrade` or `StudentActivityScore` records.
- Faculty forced-password-change navigation lock:
  - Added a server-driven `password_change_required` state to `/faculty/change-password/`.
  - Collapsed and locked the left Faculty navigation while `must_change_password` remains active.
  - Disabled the sidebar toggle and removed portal navigation links from the rendered locked state.
  - Hid My Signature, Change Password, Help, quick tour, and the password page Back action during the required-change step; Logout remains available.
  - Added a plain warning explaining that navigation returns after a successful password update.
  - Confirmed invalid password-change submissions keep the account flag and navigation lock.
  - Confirmed a successful password update clears `must_change_password`, preserves authentication, redirects to the Faculty dashboard, and restores normal navigation.
  - Generalized the existing Faculty privacy-consent sidebar lock without changing the Admin privacy-consent shell.
  - Updated the revised Faculty Help Guide login wording.
- Faculty Period Summary group-header refinement:
  - Added separate pale green, pale blue, and pale gold column-group treatments for Quizzes, Participation/Output, and `CS AVE`.
  - Moved `CS AVE` to the same header level as Quizzes and Participation/Output by making it span the remaining three activity-header rows.
  - Applied each group color consistently to its activity, average, perfect-score, and student-value cells.
  - Added semantic summary color metadata and regression assertions for the expected group classes and `CS AVE` rowspan.
  - Updated the revised Faculty Help Guide wording to mention the color-band grouping.
  - No grade calculation, stored score, layout column count, or submission behavior was changed.
- Grade-explanation rounding/source mismatch correction:
  - Confirmed the official whole-grade rounding function uses `ROUND_HALF_UP` correctly: examples such as `69.72`, `91.50`, and `83.62` round to `70`, `92`, and `84`.
  - Traced the reported contradictory pairs to the modal combining a stored submitted official grade with a fresh calculation from the current live grading template after the setup or source records changed.
  - Kept the stored period grade as the prominent official result.
  - When current recalculation differs, the modal now labels the current rounded result and pre-rounding decimal as a comparison and explains that it does not replace the submitted grade.
  - Added a stored-grade summary showing the recorded Class Standing, Exam, and official period grade before the current-setup comparison.
  - Removed the misleading `Official rounded grade` presentation from this mismatch path.
  - Added regression coverage for a submitted stored grade of `71` that differs from the current grading calculation.
  - No grade formula, score, database value, correction record, or submission status was changed.
- Faculty Help Guide navigation and grade-explanation privacy redesign:
  - Added a visible `Back to Dashboard` action to the revised Faculty Help Guide, including deep-linked sections.
  - Added an opaque blurred privacy shield behind the `Explain This Grade` modal so the background class list, student names, scores, and grades are not readable.
  - Reworked the period explanation into a simplified card-first presentation covering dynamic period result, component scores/weights/contributions, class standing categories, grouped activities, and exam details.
  - Added a plain-language dynamic formula explanation using the real component names.
  - Renamed technical display labels to faculty-friendly wording without changing service payloads or computation.
  - Kept the existing complete audit table under a collapsed `View full computation details` section.
  - Preserved final-grade explanation and correction-history access in collapsible sections.
  - Added/updated template tests for the guide Back action, privacy shield, simplified explanation sections, and collapsed detail label.
  - No grading service, model, migration, score, submission, or stored grade value was changed.
- Role-based Help Guide revision:
  - Added new practical Admin and Faculty guide templates while preserving the existing templates as the legacy fallback.
  - Added tenant setting `FEATURE_ROLE_BASED_HELP_GUIDE_ENABLED`, defaulting On, with a `Help Guide Version` card under Configuration Management.
  - Turning the setting off immediately restores the original Admin and Faculty guide pages.
  - Added server-side Admin topic filtering using effective tenant/campus permissions.
  - Added a second Superadmin identity check for the sensitive system-control section; Campus Admin users cannot see that section even if they hold an unusually broad system permission.
  - Rewrote guide coverage around real user questions: page purpose, audience, first checks, actions, when to use/avoid them, result, editability, next step, workflow ownership, completion, and records updated.
  - Added Faculty explanations for blank versus zero, Raw Score Base-50, equal Participation/Output item averaging, `Q.AVE`, `R.AVE`, `P/O AVE`, `CS AVE`, submission, reopening, corrections, reports, and privacy.
  - Kept ordinary gradebook states accurate as `Draft`, `Submitted`, and `Reopened`; the guide does not invent ordinary approval or posting states.
  - Added focused Admin and Faculty regression tests for visibility, content, and legacy fallback.
- Admin password-recovery branding:
  - Added the official `media/logos/teachermate_logo_text_official.png` wordmark to all Admin Portal password-recovery screens through a shared partial.
  - Replaced the Admin forgot-password OTP email logo with the text wordmark `NCBA | TeacherMate+`.
  - Removed password-reset OTP email logo context generation and CID attachment work.
  - Added regressions confirming the reset pages render the official wordmark and the OTP email contains no image markup or attachments.
- Deadline closure/reopen governance policy:
  - Confirmed unsubmitted gradebooks become non-editable after their configured deadline, regardless of whether the database lock row is manually marked locked.
  - Confirmed late submission is also blocked until an active approved reopen request exists.
  - Made reopen review and approval assignment-driven: any active user explicitly assigned `reopen_requests.review` by the Superadmin for the affected tenant/campus can act, regardless of role name.
  - Matched reopen-request email recipients to those explicit scoped role/direct-user assignments. Scoped deny grants are honored; unassigned superuser status alone does not qualify.
  - Removed the secondary grade-submission permission requirement so an explicitly assigned reopen reviewer can both review and approve/reject the request.
  - Confirmed overdue non-compliance notices email the accepted faculty member daily, once per local calendar day, until submission resolves the notices.
  - Updated non-compliance subjects and bodies to identify the unsubmitted course gradebook, section, and grading period.
  - Replaced the misleading policy choices with a real Enabled/Disabled control and wired it into deadline closure. Legacy `COMPLIANCE_ONLY` values normalize to Enabled to preserve production behavior.
  - Limited overdue notice targeting to accepted faculty assignments.
  - Changed `issue_submission_non_compliance_notices` in the production cron template to daily at 7:15 AM server time.
  - Converted the unreleased `0012_limit_reopen_review_to_campus_admin` migration to a compatibility no-op so deployment does not remove Superadmin-configured reviewer assignments.
- Legacy logo template sweep:
  - Scanned the repository for EduGradePlus/EGP logo references and legacy TeacherMatePlus logo display paths.
  - Replaced the Faculty forgot/reset-password shared brand image with `media/logos/teachermate_logo_text_official.png`.
  - Replaced legacy logo and fallback references in shared login OTP, Admin password-reset OTP, and Student Portal navigation.
  - Replaced the authenticated Faculty header's legacy fallback with the official TeacherMate text logo.
  - Added Faculty password-recovery render assertions for the official text logo and absence of old logo paths.
  - Added shared login-OTP and Admin reset-OTP render assertions for the official text logo and absence of legacy image paths.
  - Legacy binaries remain under `media/logos` as unused historical assets; no live template references them.
- Faculty password-reset email branding:
  - Removed the NCBA logo from the Faculty reset notification.
  - Replaced it with the text wordmark `NCBA | TeacherMate+` in the green-gold header and aligned the footer branding.
  - Removed logo context generation and CID attachment work from the Faculty reset email sender.
  - Added regression assertions that the HTML contains the text brand, contains no image tag, and sends no attachment.
- Production hero animation and Faculty reset eligibility review:
  - Confirmed the hero motion depended on `static/faculty_portal/css/public_index.css`, so a production pull/restart without refreshed collected static files could continue serving the old non-animated CSS.
  - Added the critical float, halo, and counter-rotating orbit rules directly to the Faculty landing template and added a version query to the external stylesheet URL.
  - Preserved the `prefers-reduced-motion` static fallback for users whose operating system requests reduced motion.
  - Confirmed Faculty forgot-password used the general effective-permission check, which gives every active permission to superusers and therefore allowed an admin-only superuser to receive a Faculty reset link.
  - Added `PermissionService.has_assigned_permission` for checks that must require a real role/direct permission assignment without the superuser shortcut.
  - Updated Faculty reset request and reset-link confirmation to require explicitly assigned `faculty_portal.access`.
  - Added regressions proving an admin-only superuser is blocked while a superuser with an actual Faculty access assignment remains eligible as a dual-access user.
- Portal logout/back-button protection:
  - Added strict private `no-cache`, `no-store`, `must-revalidate`, zero-age, `Pragma`, and `Expires` response headers for Admin, Faculty, and Student Portal URLs.
  - Added a Faculty layout `pageshow` safeguard that reloads pages restored from the browser back-forward cache.
  - After Faculty logout, a restored grade-entry or other protected page now performs a fresh server authorization check and redirects to `/faculty/`; stale forms cannot remain presented as usable.
  - Added regression coverage for cache headers, the browser-restoration safeguard, logout, and protected-page redirect after logout.
- User onboarding and portal-reset access:
  - Removed `Is staff` from the normal Admin Portal Create User form; newly created accounts remain non-staff by default.
  - Kept portal authorization under RBAC: `admin_portal.access` grants Admin Portal entry, `faculty_portal.access` grants Faculty Portal entry, and users may hold both.
  - Changed new-user credential emails to include only the neutral TeacherMate+ root URL, which redirects to `/faculty/`; the email no longer names or links the Admin Portal.
  - Confirmed the Faculty forgot-password request already required `faculty_portal.access`, then hardened reset-link confirmation to enforce the same permission.
  - Added regression coverage for faculty-only, admin-only, and dual-access password-reset eligibility, plus the neutral onboarding email and hidden create-form staff field.
- Admin forgot-password email diagnostics:
  - Confirmed Email Diagnostics and Admin Forgot Password use different recipient sources: diagnostics uses the manually entered address, while forgot password uses the email stored on the matched active Admin Portal user.
  - Changed Admin reset OTP sending from silent SMTP failure handling to explicit exception capture and system logging.
  - Added audit outcome metadata for `delivered`, `delivery_failed`, `missing_email`, `admin_access_denied`, and `account_not_found`.
  - Failed sends now remove the unusable OTP challenge instead of leaving a stale active record.
  - Added regression coverage for a simulated SMTP rejection.
- Default public route:
  - Changed the bare site root `/` to issue a temporary redirect to the Faculty Portal landing page at `/faculty/`.
  - Preserved direct `/admin-portal/`, `/index/`, `/index.php`, and all existing portal routes.
  - Added a Faculty public-login regression test for the root redirect.
- Faculty public-page privacy seal:
  - Added `“Grado Mo, Protektado Ko!”` immediately beneath the NPC seal.
  - Styled the slogan with Kaushan Script, deep-green and olive-gold phrase segments, integrated softened quotation marks, responsive mobile sizing, and a custom two-color SVG pen-stroke flourish underneath.
- Faculty public hero logo animation:
  - Removed the 48-star field after the user confirmed the stars were not visible in production.
  - Replaced it with a gentle vertical logo float, a visible breathing green-gold aura, and two thin counter-rotating orbital rings.
  - Preserved the logo as the foreground layer with a restrained drop shadow.
  - Added a `prefers-reduced-motion` fallback that disables the logo float, halo pulse, and orbital rotation.
- Grading template faculty-activity averaging:
  - Corrected the initial implementation after user clarification: the required behavior is averaging faculty-created activities under Recitation/Assignment/Activity-style detail rows, not averaging the template detail buckets themselves.
  - Added `DetailComputationMode` with `WEIGHTED_DETAILS` and `AVERAGE_ACTIVITIES`.
  - Added `GradingTemplateSubcomponent.detail_computation_mode`, defaulting to `WEIGHTED_DETAILS` so existing templates keep their current behavior.
  - Exposed `Detail Computation` on Admin Portal subcomponent create/edit forms.
  - Updated official period-grade computation so subcomponents with active detail rows can average the faculty-created activities under those details equally when configured.
  - Updated reporting, prediction snapshot, admin grade-distribution analytics, template duplication, and template calculator preview paths so they respect the selected detail-computation mode.
  - Fixed the Admin Portal template testing calculator crash at `/admin-portal/grading/templates/calculator/` by routing the calculator's detail rollup through `FacultyGradingService.aggregate_detail_scores`.
  - Added a calculator regression test for a `Participation/Output` subcomponent configured as `Average Activities`, covering the reported GET URL pattern with `grading_template` and `sample_value`.
  - Fixed Faculty Summary of Periodic Grades display for nested `Average Activities` subcomponents. The summary table now uses the average of faculty-created activities under the subcomponent instead of the old detail-weight math when calculating the visible Class Standing total.
  - Fixed the visual/table-layout bug reported from the Faculty Summary screenshot: the highlighted `87.75` was mathematically the weighted Class Standing total, but it appeared under Participation/Output because nested subcomponents had no separate subcomponent-average column. The table now shows Participation/Output average separately and labels the final weighted column `CS AVE`.
  - Fixed the follow-up summary label issue: the Participation/Output subcomponent average now displays as `P/O AVE` instead of incorrectly borrowing the first activity prefix and showing `R.AVE`.
  - Updated Faculty Summary detail visibility: when a subcomponent uses `Average Activities`, empty detail columns are omitted from the summary table; when it uses `Weighted Details`, all configured detail columns remain visible.
  - Added an editable-summary refresh for periods using `Average Activities`, so stored unsubmitted period summary rows are recomputed after a template setting change without asking faculty to re-save existing activities.
  - Recomputed the affected local dev gradebook for offering `419` / Midterm `21` (`A132-ITAPPS`, `BSA 1-BSA_1A`) with audit reason `AVERAGE_ACTIVITIES_RECHECK`; first rows now show corrected Class Standing values such as `98.00`, `86.00`, and `88.00` instead of stale old-weight values.
  - Verified Correction of Grades final approval uses the same `FacultyGradingService.recompute_period_summary_for_students` path and added regression coverage for an approved correction under `Average Activities`.
  - Updated Admin grading-template builder/list/structure-preview displays to show the detail computation rule and show detail weights as ignored when `Average Activities` is enabled.
  - Added a focused regression test proving `Weighted Details` yields 70 and switching the same Recitation/Assignment/Activity setup to `Average Activities` yields 75 by averaging the four faculty-created activities.
  - Documented the change in `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, Admin Guide, and `docs/TENANT_GRADING_PROFILE_SETUP_GUIDE.md`.
- Repository/product rename:
  - Replaced legacy product naming with TeacherMatePlus/TeacherMate+ across repo text, docs, templates, settings, scripts, logs, tests, and exported fixtures.
  - Renamed matching files/assets/configs, including `TEACHERMATEPLUS_CONTEXT.md`, `media/logos/teachermateplus_logo.png`, `ops/cron/teachermateplus.cron`, nginx configs, systemd service files, and ignored `ops/env/teachermateplus.*.env.example` files.
  - Also renamed older legacy presentation, fixture, import, and logo filenames so no previous brand-family text remains in file contents or paths.
  - Corrected mechanically renamed placeholder email domains to use `teachermateplus.local` and normalized deployment examples that require shell/database-safe lowercase identifiers.
  - Changed Git `origin` to `https://github.com/privatePol/teachermateplus.git`.
- Admin Portal login branding:
  - Replaced the `/admin-portal/login/` branding-panel image with `media/logos/teachermate_logo_text_official.png`.
  - Replaced the authenticated Admin Portal left-nav logo with `media/logos/teachermate_logo_text_official.png`.
  - Reversed the authenticated Admin Portal left-nav green gradient so it starts darker at the top and becomes lighter toward the bottom.
  - Updated the Admin topbar scope controls so Tenant and Campus dropdowns stay on the same row on desktop and wrap on mobile.
  - Previous Admin login logo work standardized the login page on TeacherMate branding and added the NCBA NPC seal column.
  - Updated the login panel copy to `Sign in as an authorized NCBA TeacherMatePlus user.` and added an MIS Department credential reminder for users without login credentials.
  - Added the NCBA NPC seal from `media/logos/ncba_npc_seal.png` to a dedicated right-side column on the Admin Portal login page.
  - Render-checked the Admin login page and confirmed the new logo filename appears while the old filename does not.
- Faculty Portal public-login adjustment:
  - Changed the public Faculty Portal inline login forms on `/faculty/` to post back to `/faculty/` instead of `/faculty/login/`.
  - Added POST handling to `faculty_portal:public_index` using the normal Faculty login form, permission check, lockout behavior, OTP redirect, audit logging, and dashboard redirect path.
  - Invalid username/password attempts now keep the browser on `/faculty/` and show the error beside the inline login form.
  - Added a shared Faculty password-recovery brand partial that displays `media/logos/teachermateplus_logo.png`.
  - Updated Faculty forgot-password, reset-link-sent, reset-confirm, and reset-complete pages so their `Back to Faculty Login` / `Go to Faculty Login` links return to `/faculty/`.
  - Updated Faculty guide/manual wording, `CHANGE_LOG.md`, and `TEACHERMATEPLUS_CONTEXT.md`.
- Faculty Portal public homepage branding:
  - Replaced the hero/main logo image on `/faculty/` with `media/logos/teachermate_logo_official.png`.
  - Replaced the authenticated Faculty Portal header logo on pages such as `/faculty/dashboard/` with `media/logos/teachermate_logo_official.png`.
  - Increased the spacing between the authenticated Faculty Portal top-nav heading and subtitle, and increased the subtitle height/readability.
  - Updated the public landing hero headline to `Welcome to NCBA's TeacherMate+` and revised the supporting sentence to say TeacherMate+ helps `our faculty members`.
  - Reworked the Faculty Portal public landing page copy to address NCBA faculty members and NCBA operations directly, including active-period governance, SIS integration, and grade-file continuity sections.
  - Removed the final `Proceed to Login` call-to-action section from `/faculty/`.
  - Added the NCBA NPC seal from `media/logos/ncba_npc_seal.png` centered in its own soft-gradient section on `/faculty/`.
  - Added a second proportion-preserving 75px-high NCBA NPC seal in the desktop navbar beside the login controls on `/faculty/`.
  - Renamed the public navbar brand from `TeacherMate+` to `NCBA | TeacherMate+` and changed the nav label `Entry Experience` to `Experience`.
  - Seal sizing is capped at 150px with explicit image width/inline sizing as a cache-resistant safeguard.
  - Seal section now centers the seal across the full page width with inline flex centering and a stronger green/gold background gradient; the background gradient is also applied inline on the section as a cache-resistant safeguard.
  - Increased the lower seal section padding to `6rem` top and `6.5rem` bottom, with inline padding as a cache-resistant safeguard, to improve headroom and tail room around the NPC seal.
- Paused DepEd E-Class Record work per user direction; no additional DepEd work was performed after the pause.
- Privacy Consent adjustment:
  - Added `privacy_consent_pending` layout context for Admin and Faculty Privacy Consent views.
  - Collapsed/locked Admin and Faculty left navigation while consent is pending.
  - Removed normal sidebar menu links while consent is pending.
  - Hid Change Password, My Signature, and floating guide shortcuts while consent is pending.
  - Kept Logout available.
- Syllabus link adjustment:
  - Added `Course.syllabus_url` for Google Drive or approved external syllabus links.
  - Added Admin Course form/list/Django admin support for syllabus links.
  - Added `faculty_portal:offering_syllabus`, an internal faculty-only redirect that verifies the logged-in faculty has an active assignment for the offering and that the course tenant matches the offering tenant before redirecting to the stored URL.
  - Added a syllabus icon on Faculty Portal My Classes accepted course cards only when a syllabus link exists.
  - Documented that Google Drive/domain sharing remains the central storage permission layer.
  - Added `VIEW_SYLLABUS_LINK` audit logging for each successful faculty syllabus-link open, scoped to actor, tenant, campus, course, offering, assignment, section, and term.
- Faculty active-period display adjustment:
  - Updated Faculty Dashboard/My Classes Current Active Grading Period chips to include the active AY.
  - Replaced numeric campus-code display with tenant plus campus-name labels such as `NCBA-Fairview`.
  - Reordered the Current Active Grading Period card so Campus / AY / Term appears first as an H4-styled scope row with separate colors, followed by the explanatory sentence and then the grading-period chip.
- Faculty deadline/dashboard card styling:
  - Reordered the Grade Submission Deadline reminder so Period and Deadline appear first as a colored H4-style focus row.
  - Applied distinct color treatments to Faculty Dashboard shortcut cards: Continue Grading, Priority Actions, Student Support, and Private Notes.
  - Added centered inline SVG icons to the four Faculty Dashboard shortcut cards.
  - Applied matching distinct color treatments to all Main Action Cards.
  - Added compact `Guide` tags to Dashboard shortcut cards, Main Action Cards, Grade Submission Deadline reminders, and My Classes boards so faculty can jump directly to the related Faculty User Guide article.
- Faculty Summary of Periodic Grades adjustment:
  - Moved visible period/final grade columns directly after the Status column in the live summary table.
  - Hid ACTIVE badges/values in the Status column while preserving visible non-active statuses such as DRP, W, and INC.
- Gradebook reopen notification correction:
  - Updated reopen request email recipients to mirror effective `reopen_requests.review` authorization for the request tenant/campus.
  - Recipients now include scoped role holders, direct user permission grants, and active superusers with email addresses.
- Admin role-permission usability adjustment:
  - Added clearer display-only permission group labels on the role-permissions page.
  - Added plain-English descriptions to each permission card header so admins can understand what the group controls before granting access.
  - Changed section saves to return to the saved section anchor and show a compact `Changes saved` badge instead of landing at the top of the page.
  - Changed full-page permission saves to remain on the role-permissions page and restore prior scroll position where browser session storage permits.
  - Kept the Critical Access Safeguard enforcement but hid the panel during ordinary permission edits; it appears only when critical permissions are added/removed or when validation needs the reason/confirmation fields.
- Admin Portal Guide usability adjustment:
  - Converted the guide's top quick links and workstream links from direct Admin Portal page links into in-page table-of-contents anchors.
  - Simplified wording across the guide, especially setup, deadline lock, correction, security, monitoring, and incident response instructions.
  - Removed body screenshots from guide sections 2 and 3.
  - Moved guide screenshots for later guide cards into card headers and added a direct `How To Set Periodic Encoding Deadline Lock` subsection under Submission and Reopen Control.
- Admin Active Grading Period setup adjustment:
  - Diagnosed `/admin-portal/tools/active-grading-period/?campus_id=9&term_id=7`: NCBA Fairview / 2ND term has PRELIM, MIDTERM, PREFINAL, and FINAL catalog rows, but all four were inactive, so the previous page logic hid them.
  - Changed the Term Period Catalog table to show inactive rows so admins can reactivate them directly.
  - Added a warning when a selected term has catalog rows but no active rows available for selection.
  - Updated the standard 4-period loader so existing inactive standard rows count as changed/reactivated rows instead of reporting as if nothing happened.
  - Added clearer setup instructions to the Active Grading Period page and Admin Guide: load the standard period catalog once per tenant/term, then save the active period separately per campus/term.
  - Added a plain-English relationship map connecting Active Academic Scope, Active Grading Period, Period Locks, Grade Deadline Enforcement, Non-Compliance monitoring, and Submission Reopen Requests.
- Admin Guide submission-route documentation:
  - Added the confirmed route matrix to Admin Guide section `7. Submission and Reopen Control`.
  - Documented that Gradebook Reopen Request is for unfinished/unsubmitted gradebooks after deadline or lock, while Correction of Grades is for submitted/finalized gradebooks that need changes.
  - Clarified that submitted gradebooks may be reopened only before the configured deadline.
- Faculty reopen request period-card adjustment:
  - Updated policy per user direction: once a deadline is met or a period is locked, additional faculty encoding and unsubmitted gradebook submission require an approved reopen request regardless of the prior deadline enforcement policy label.
  - Added a direct period-card `Request Gradebook Reopen` icon/modal so faculty can request reopen from the period card when a period is locked or closed after deadline.
- Admin Dashboard reopen request visibility adjustment:
  - Added a scoped `Gradebook Reopen Requests` dashboard panel for users with `reopen_requests.read` or `reopen_requests.review`.
  - Panel shows pending review count, reviewed-today count, latest pending requests, and direct review links for users who can review.
- Faculty approved reopen display/edit-state adjustment:
  - Fixed Faculty Portal period-card and period work-page state so an approved gradebook reopen request overrides the raw period lock/deadline closure for a controlled reopen window.
  - Reopened periods now show a `Reopened` badge and approved reopen notice instead of staying visually locked after campus admin approval.
  - Added a 24-hour validity window for approved gradebook reopen requests. When the latest approved 24-hour window expires before submission, TeacherMate+ creates a course-level lock again and requires a new reopen request before further encoding or late submission.
  - Tightened submission governance so locked or overdue unsubmitted gradebooks can submit only during the latest active approved reopen window. Older expired approved requests remain audit history and no longer block a newer active approval.
  - Fixed final submission from an active approved reopen window by allowing the submission recompute step to proceed even when the underlying period lock is still present.
  - Enforced the confirmed route: submitted/finalized gradebooks after the deadline cannot use Gradebook Reopen Request and must use Correction of Grades. Submitted gradebooks can use reopen request only before the configured deadline.
- Email notification standardization:
  - Added a shared email subject formatter so system-generated emails use `NCBA | TeacherMatePlus: <Message>`.
  - Updated account/password, new-user credentials, faculty reminder, non-compliance, correction, registrar, gradebook reopen, and email diagnostic send paths to use the standard subject format.
  - Switched email card branding to the NCBA logo (`media/logos/ncba-logo.png`) and the green-to-yellow card header style used by the password reset message.
  - Removed embedded Data Privacy Notice blocks from email templates.
- Login Security simultaneous-login configuration:
  - Added tenant feature setting `FEATURE_SINGLE_DEVICE_SESSION_ENFORCEMENT_ENABLED`.
  - Exposed `Allow only one active login session per user` under `Admin Portal -> Tools -> Configuration Management -> Login Security`.
  - Kept default behavior enabled, so existing tenants still sign out the older browser/device when the same user logs in elsewhere.
  - When disabled for the tenant, simultaneous Admin/Faculty sessions for the same user are allowed.
  - Updated Admin/Faculty guide wording to point admins to the Login Security setting.
- Faculty Portal class-card and summary simplification:
  - Pending assignments on `/faculty/my-courses/` now show only `Accept Assignment`; clarification/decline buttons and response textarea were removed from the faculty page.
  - `/faculty/my-courses/<offering>/periods/` now uses one shared sticky `What to do` / `Why set this` note, removes repeated guidance from each period card, shows lightweight activity metric cards for each required subcomponent/detail bucket, and keeps action icons at the bottom of each card.
  - Period-card and Summary deadline display now uses `Month day, year` formatting, with a countdown badge on the Summary page.
  - `/faculty/my-courses/<offering>/periods/<period>/summary/` Period Snapshot is expanded by default and simplified to fewer cards.
  - The Summary gradebook caption now shows campus name instead of campus code.
  - Faculty guide/manual wording was updated for the simplified assignment and snapshot behavior.
- Faculty Portal Grade Prediction / At-Risk Monitor access check:
  - Diagnosed the missing Faculty Portal grade prediction and non-working Students At-Risk Monitor as an NCBA tenant configuration issue in the dev database: Grade Prediction was disabled.
  - Enabled NCBA dev settings for `FEATURE_GRADE_PREDICTION_ENABLED`, `FEATURE_GRADE_PREDICTION_ROLE_CODES` (`FACULTY`, `CAMPUS_ADMIN`, `TENANT_ADMIN`, `SUPER_ADMIN`), and `FEATURE_GRADE_PREDICTION_AT_RISK_ENABLED`.
  - Added Faculty Portal layout context for prediction and at-risk feature access.
  - Updated the Faculty Portal sidebar so `Students At-Risk Monitor` appears only when the current user is allowed by the same Grade Prediction feature and role checks used by the prediction pages.
  - Added simple Admin Guide instructions for enabling Grade Prediction and the Faculty Students At-Risk Monitor from Configuration Management.
- Faculty Portal Grade Prediction wording simplification:
  - Clarified that the prediction page is mainly for the selected grading period; the possible final-grade value is secondary guidance only.
  - Replaced confusing `Final At Risk` status wording with simpler labels such as `At Risk This Period`, `Needs Follow-up`, `Needs Scores`, and `On Track`.
  - Simplified prediction table headers to faculty-friendly labels such as `Estimated [Period] Grade`, `Encoded Work`, `Still Missing`, and `Period Alert`.
  - Replaced the technical prediction guide with a shorter sample-student walkthrough and plain column guide.
- Faculty Dashboard simplification:
  - Removed the lower sections of passive metric cards from `/faculty/dashboard/`.
  - Added a smaller `Main Action Cards` area with clickable cards for unsubmitted classes, priority actions, student support/at-risk follow-up, and class-list status.
  - At-Risk dashboard links now respect the faculty at-risk feature gate, falling back to My Classes when disabled.
- Faculty grading-template page simplification:
  - Removed the visible `Open Grade Calculator` button from `/faculty/my-courses/<offering>/grading-template/`.
  - Removed faculty guide/manual wording that instructed faculty to use the calculator from the grading-template page.
- Faculty Final Clearance print gating:
  - Final Clearance preview remains available from My Classes / final-period context.
  - Official Final Clearance PDF generation is blocked unless every accepted course assignment in the campus-term scope evaluates as `COMPLETE`.
  - My Classes and final-period cards show print actions only when the scope can print; otherwise they show check/pending states.
- Faculty Student Intervention Monitor redesign/refinement:
  - Renamed the faculty-facing Students At-Risk Monitor to `Student Intervention Monitor` in the sidebar, quick tour, dashboard CTA, faculty guide/manual, and public Faculty Portal page copy.
  - Added `StudentInterventionService` to translate existing prediction snapshots into plain intervention statuses without exposing technical prediction language in the default monitor.
  - Default monitor statuses are now `Needs Attention`, `Monitor`, `Missing Work`, and `On Track`; internal status codes remain `CRITICAL`, `WARNING`, `MISSING_WORK`, and `ON_TRACK`.
  - Refined classification to prioritize missing/incomplete encoded data before grade-based concerns.
  - Missing attendance records are treated as incomplete encoding, not as student attendance behavior.
  - Each row now emits one plain `main_concern` and one action-oriented `suggested_intervention`.
  - Current standing values are softened to `Needs attention`, `Close to threshold`, `On track`, or `Not ready to assess`.
- Admin Student Enrollment Query:
  - Added `/admin-portal/students/enrollment-query/`, governed by the new `student_enrollment_query.read` permission.
  - Authorized admins can search a scoped student, select Academic Year and Term, and view consolidated enrollment rows with period grades, submission status, final grade, and encoded activity-score details.
  - Added audit event `VIEW_STUDENT_ENROLLMENT_QUERY` whenever a selected student/AY/term query is opened.
  - Added RBAC and navigation seed migrations so the page appears under the Admin Portal Enrollment menu for authorized users.
  - Moved the existing `Student Enrollment Query` sidebar item from `Students` to `Enrollment` with migration `navigation.0005_move_student_enrollment_query_to_enrollment`.
- Faculty activity score encoding:
  - Disabled Enter-key form submission inside activity score inputs on `/faculty/my-courses/<offering>/periods/<period>/activities/<activity>/scores/`.
  - Faculty must click `Save Scores`, reducing accidental saves while encoding grades row by row.
  - Default monitor now focuses on Student, Class / Period, Current Standing, Main Concern, Suggested Intervention, and Action.
  - Default monitor no longer shows likely-fail wording, prediction confidence, coverage percentage, projected final grade, or possible final-grade warning language.
  - Technical projection details remain on the separate prediction/analytics pages, with `Advanced Analytics` shown only when the user has `faculty_analytics.read`.
  - Added audit event `VIEW_STUDENT_INTERVENTION_MONITOR` whenever the faculty monitor is opened.
- Infrastructure/security planning discussion:
  - Recommended isolating uploaded documents outside the app directory under `/srv/teachermateplus-media`.
  - Recommended mounting upload storage with `nodev,nosuid,noexec`.
  - Recommended a practical 1TB RAID10 layout: `/` about 100 GB, `/var/log` about 30 GB, `/tmp` about 20 GB, `/opt/teachermateplus` about 80 GB, `/srv/teachermateplus-media` about 700 GB, `/srv/backup-staging` about 100 GB, and swap about 8-16 GB.
  - Recommended Synology immutable/snapshot-backed backups as the main ransomware recovery layer, with ClamAV/Wazuh/CrowdSec/fail2ban/UFW/SSH hardening as supporting controls.
- Added focused tests for pending-consent locks and syllabus link behavior.
- Added a focused Faculty Dashboard active-period display test.
- Added focused Faculty Summary table tests for grade-column order and status display.
- Added a focused reopen request notification test for role, direct user-permission, and superuser recipients.
- Added focused role-permissions page tests for module descriptions, section-save behavior, section anchor redirect, and saved label display.
- Updated `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, Admin guide, Faculty guide/manual, and institution implementation reference.

## Files Created / Modified
- Rename sweep touched many text files across `apps/`, `templates/`, `docs/`, `ops/`, `scripts/`, logs, fixtures, and top-level docs. Review with `git diff --stat` / `git status --short` rather than this legacy handoff list.
- Renamed the context document to `TEACHERMATEPLUS_CONTEXT.md`.
- Renamed the product logo asset to `media/logos/teachermateplus_logo.png`.
- Renamed ops cron/nginx/systemd/env example filenames to use `teachermateplus`.
- Renamed older legacy presentation docs, CSV/JSON fixtures, media import CSVs, and logo variants to use `teachermateplus`.
- Added: `apps/faculty_portal/tests_public_login.py`
- Added: `apps/academics/migrations/0009_course_syllabus_url.py`
- Added: `apps/admin_portal/tests_student_enrollment_query.py`
- Added: `apps/rbac/migrations/0011_seed_student_enrollment_query_permission.py`
- Added: `apps/navigation/migrations/0004_seed_student_enrollment_query_menu.py`
- Added: `apps/navigation/migrations/0005_move_student_enrollment_query_to_enrollment.py`
- Added: `templates/admin_portal/students/student_enrollment_query.html`
- Modified: `apps/academics/models.py`
- Modified: `apps/academics/admin.py`
- Modified: `apps/accounts/views.py`
- Modified: `apps/accounts/services.py`
- Modified: `apps/accounts/tests_admin_password_reset.py`
- Modified: `apps/accounts/tests_login_lockout.py`
- Modified: `apps/accounts/tests_login_otp.py`
- Modified: `apps/accounts/tests_privacy_consent.py`
- Modified: `apps/admin_portal/forms.py`
- Modified: `apps/admin_portal/import_views.py`
- Modified: `apps/admin_portal/urls.py`
- Modified: `apps/admin_portal/views.py`
- Modified: `apps/admin_portal/tests_department_dropdown_labels.py`
- Modified: `apps/admin_portal/tests_reopen_requests.py`
- Modified: `apps/admin_portal/tests_roles.py`
- Modified: `apps/core/services/email_assets.py`
- Modified: `apps/core/management/commands/seed_stage_0_1.py`
- Modified: `apps/core/services/features.py`
- Modified: `apps/core/context_processors.py`
- Modified: `apps/core/tests_email_assets.py`
- Modified: `apps/faculty_portal/views.py`
- Modified: `apps/faculty_portal/services.py`
- Modified: `apps/faculty_portal/forms.py`
- Modified: `apps/faculty_portal/urls.py`
- Modified: `apps/faculty_portal/tests_assignment_acceptance.py`
- Modified: `templates/admin_portal/login.html`
- Modified: `apps/accounts/tests_login_otp.py`
- Modified: `templates/admin_portal/base.html`
- Modified: `apps/admin_portal/tests_assignment_acceptance.py`
- Modified: `apps/grading/notifications.py`
- Modified: `apps/grading/management/commands/auto_lock_period_deadlines.py`
- Modified: `apps/grading/services.py`
- Modified: `apps/grading/tests.py`
- Modified: `apps/notifications/services.py`
- Modified: `templates/admin_portal/base.html`
- Modified: `templates/admin_portal/academics/course_table.html`
- Modified: `templates/admin_portal/login.html`
- Modified: `templates/admin_portal/security/role_permissions.html`
- Modified: `templates/faculty_portal/base.html`
- Modified: `templates/faculty_portal/dashboard.html`
- Modified: `templates/faculty_portal/public_index.html`
- Modified: `apps/faculty_portal/tests_public_login.py`
- Modified: `templates/faculty_portal/base.html`
- Modified: `apps/faculty_portal/tests_assignment_acceptance.py`
- Modified: `static/faculty_portal/css/public_index.css`
- Modified: `templates/faculty_portal/partials/deadline_banner.html`
- Modified: `templates/faculty_portal/partials/active_period_banner.html`
- Added: `templates/faculty_portal/partials/password_recovery_brand.html`
- Modified: `templates/faculty_portal/student_at_risk_monitor.html`
- Modified: `templates/faculty_portal/offering_grading_template.html`
- Modified: `templates/faculty_portal/period_final_clearance.html`
- Modified: `templates/faculty_portal/period_prediction.html`
- Modified: `templates/faculty_portal/period_prediction_guide.html`
- Modified: `templates/faculty_portal/my_courses.html`
- Modified: `templates/faculty_portal/activity_scores.html`
- Modified: `templates/faculty_portal/offering_periods.html`
- Modified: `templates/faculty_portal/period_activities.html`
- Modified: `templates/faculty_portal/period_attendance.html`
- Modified: `templates/faculty_portal/period_summary.html`
- Modified: `templates/faculty_portal/public_index.html`
- Modified: `templates/accounts/emails/login_otp.html`
- Modified: `templates/accounts/emails/login_otp.txt`
- Modified: `templates/accounts/emails/admin_password_reset_otp.html`
- Modified: `templates/accounts/emails/admin_password_reset_otp.txt`
- Modified: `templates/admin_portal/emails/new_user_credentials.html`
- Modified: `templates/admin_portal/emails/new_user_credentials.txt`
- Modified: `templates/faculty_portal/emails/password_reset.html`
- Modified: `templates/faculty_portal/emails/password_reset.txt`
- Modified: `templates/faculty_portal/emails/reminder_notification.html`
- Modified: `templates/faculty_portal/emails/reminder_notification.txt`
- Modified: `templates/notifications/emails/submission_non_compliance_notice.html`
- Modified: `templates/notifications/emails/submission_non_compliance_notice.txt`
- Modified: `templates/grading/emails/correction_submission_notification.html`
- Modified: `templates/grading/emails/gradebook_reopen_request_notification.html`
- Modified: `templates/grading/emails/registrar_official_report.html`
- Modified: `templates/admin_portal/guide.html`
- Modified: `templates/admin_portal/tools/configurable_features.html`
- Modified: `templates/admin_portal/guide.html`
- Modified: `templates/faculty_portal/guide.html`
- Modified: `templates/faculty_portal/guide_manual.html`
- Modified: `templates/faculty_portal/password_forgot.html`
- Modified: `templates/faculty_portal/password_forgot_done.html`
- Modified: `templates/faculty_portal/password_reset_complete.html`
- Modified: `templates/faculty_portal/password_reset_confirm.html`
- Modified: `docs/INSTITUTION_IMPLEMENTATION_REFERENCE.md`
- Modified: `CHANGE_LOG.md`
- Modified: `TEACHERMATEPLUS_CONTEXT.md`
- Modified: `HANDOFF.md`
- Existing working tree notes not introduced by these requests:
  - DepEd ECR compatibility changes from the prior turn remain in the working tree, including `apps/grading/migrations/0026_tenantgradingprofile_period_formula.py`.
  - `AGENTS.md` was already modified.
  - `logs/system.log` was already modified.
  - `docs/SESSION_ENDING_PROMPT.md` was already untracked.
  - `docs/START_SESSION_PROMPT.md` was already untracked.

## Important Decisions
- Syllabus storage stays in Google Drive; TeacherMate+ stores only the URL.
- TeacherMate+ enforces tenant/faculty assignment visibility before redirecting to the link, but Google Workspace still enforces whether the opened document is viewable.
- If a course has no syllabus link, Faculty My Classes renders no syllabus icon/action.
- The syllabus URL is course-level metadata, so all offerings of the same course inherit the same link.
- Successful faculty syllabus-link opens are audited without storing the raw Google Drive URL in audit metadata.
- Privacy Consent access remains server-side through existing post-login security middleware; layout locks are additional first-login focus controls.
- Faculty active-period campus display is derived from tenant code plus campus name, so `NCBA-01`/`NCBA-02`/`NCBA-03` style codes render as labels such as `NCBA-Cubao`, `NCBA-Fairview`, and `NCBA-Taytay` when campus names are configured that way. The Faculty Dashboard and My Classes active-period card shows Campus / AY / Term first, then the active-period explanation, then the grading-period chip.
- Faculty Grade Submission Deadline reminders now show Period and Deadline first as a colored H4-style row, then the reminder message/helper. Faculty Dashboard shortcut cards and Main Action Cards intentionally use different colors for quick visual grouping.
- Faculty Dashboard and My Classes `Guide` tags are separate links beside cards/boards to avoid nested links. They jump to existing Faculty Guide anchors rather than opening tooltip text, keeping the pages uncluttered while still giving immediate help access.
- Faculty Summary of Periodic Grades keeps ACTIVE status cells blank by design to reduce visual clutter, while non-active enrollment statuses remain visible for encoding attention.
- Gradebook reopen emails are permission-driven: whoever the superadmin authorizes with effective `reopen_requests.review` for the tenant/campus is eligible to receive the request email.
- Role permission group labels/descriptions are display-only. They do not rename permission codes or change permission enforcement.
- Role permission section-save focus uses URL anchors and a small server-rendered saved badge; whole-page save scroll restoration uses browser session storage.
- Critical Access Safeguard remains required server-side for critical role-permission changes, but the panel is hidden by default to keep routine permission edits less distracting.
- Admin Guide quick/workstream navigation is intentionally guide-internal. The guide still names the actual Admin Portal paths in text, but the top navigation behaves like a TOC instead of leaving the page.
- Active Grading Period setup uses a term-period catalog. Only active catalog rows can be selected as the current active grading period; inactive rows must be activated first.
- Active Grading Period setup has two scopes: the period catalog is tenant+term scoped, while the selected active period is tenant+campus+term scoped.
- Period Locks should be created per campus/term/period when each campus has its own deadline. Course-offering scope remains for class-specific exceptions.
- Faculty deadline reopen requests are required after deadline or lock for both additional encoding and unsubmitted gradebook submission regardless of the stored deadline enforcement policy label.
- Approved gradebook reopen requests are limited to 24 hours from approval. Only the latest approved request controls the current window. If the faculty does not submit/resubmit within that window, the period is locked again and a new request is required.
- Submitted/finalized gradebooks after deadline must use Correction of Grades for changes. Reopen Request is reserved for unsubmitted overdue/locked gradebooks, plus submitted gradebooks only before the deadline.
- Dashboard reopen request panel is permission-gated and scope-filtered through `AdminScopeService.scoped_grade_submission_reopen_requests`.
- System email subjects should use `NCBA | TeacherMatePlus: <Message>`. Shared email card branding should use the NCBA logo and the green-to-yellow header, without embedding a Data Privacy Notice block in the email body.
- Single-device session enforcement is now tenant-configurable in Login Security. The global Django setting `ENFORCE_SINGLE_DEVICE_SESSION=False` still disables the behavior platform-wide; otherwise the tenant toggle decides whether the same user is limited to one active browser/device session.
- Period-card setup counts intentionally use lightweight bucket-level activity counts. Avoid adding per-student detail to every card unless performance is rechecked, because that can multiply gradebook queries on `/faculty/my-courses/<offering>/periods/`.
- Missing-record checks are based on missing saved score/attendance rows for required items. A saved zero score counts as encoded and should not be treated as missing.
- Student Intervention Monitor uses the existing prediction snapshot engine as a data source but intentionally presents faculty-facing intervention labels and reasons. Projection details should stay on the separate prediction/analytics pages.
- The intervention monitor default view excludes `On Track` rows unless the faculty explicitly selects the On Track filter.
- Student Enrollment Query is read-only and scoped through Admin Portal student visibility plus tenant/campus enrollment filters. It does not create or recompute grades.
- `student_enrollment_query.read` is the permission to grant if an admin user should be able to open the consolidated one-student enrollment/grade lookup.
- Activity score encoding intentionally blocks Enter-key submission only inside score inputs. It does not block clicking `Save Scores`.
- Subcomponent `Detail Computation` is additive and backward-compatible: existing rows default to `Weighted Details`; only subcomponents explicitly set to `Average Activities` ignore individual detail weights and average the active faculty-created activity scores under that subcomponent.
- `Average Activities` affects the rollup from faculty-created activities to the owning subcomponent only. The subcomponent still contributes to its parent component by its own configured weight, and component weights still control period-grade contribution.
- Ransomware protection strategy should not rely on antivirus alone. The preferred server-side approach is least privilege, separated upload storage, no-execute mounts, restricted backup credentials, and Synology snapshots/immutability with tested restore procedures.

## Changed Files This Session
- `apps/grading/models.py`
- `apps/grading/access.py`
- `apps/grading/duplication.py`
- `apps/grading/services.py`
- `apps/grading/migrations/0028_gradingtemplate_department_visibility_and_more.py`
- `apps/admin_portal/services.py`
- `apps/admin_portal/forms.py`
- `apps/admin_portal/views.py`
- `apps/admin_portal/tests_template_department_visibility.py`
- `templates/admin_portal/grading/template_table.html`
- `templates/admin_portal/grading/template_builder.html`
- `templates/admin_portal/grading/template_structure_preview.html`
- `templates/admin_portal/grading/template_approval_review.html`
- `templates/admin_portal/grading/template_hotfix_review.html`
- `templates/admin_portal/grading/template_period_code_reference.html`
- `templates/admin_portal/grading/grading_setup_guide.html`
- `CHANGE_LOG.md`
- `TEACHERMATEPLUS_CONTEXT.md`
- `HANDOFF.md`
- `apps/admin_portal/tests_template_calculator.py`
- `apps/admin_portal/tests_template_governance.py`
- `apps/faculty_portal/forms.py`
- `apps/grading/services.py`
- `templates/admin_portal/grading/analytics.html`
- `templates/admin_portal/grading/correction_request_create_on_behalf.html`
- `templates/admin_portal/grading/correction_request_review.html`
- `templates/admin_portal/grading/detail_table.html`
- `templates/admin_portal/grading/template_structure_preview.html`
- `templates/admin_portal/grading/template_testing_calculator.html`
- `templates/faculty_portal/activity_scores.html`
- `templates/faculty_portal/offering_grading_template.html`
- `templates/faculty_portal/period_activities.html`
- `templates/faculty_portal/period_corrections.html`
- `apps/admin_portal/views.py`
- `apps/admin_portal/urls.py`
- `apps/admin_portal/tests_help_guide.py`
- `templates/admin_portal/base.html`
- `templates/admin_portal/grading/grading_setup_guide.html`
- `templates/admin_portal/grading/template_list.html`
- `templates/admin_portal/grading/template_builder.html`
- `templates/admin_portal/grading/tenant_grading_profile_list.html`
- `templates/admin_portal/grading/course_base_override_list.html`
- `templates/admin_portal/guide_role_based.html`
- `templates/admin_portal/guide.html`
- `CHANGE_LOG.md`
- `TEACHERMATEPLUS_CONTEXT.md`
- `HANDOFF.md`
- `logs/system.log` changed from local Django test logging.
- `templates/faculty_portal/guide_role_based.html`
- `templates/faculty_portal/guide_manual.html`
- `apps/faculty_portal/tests_help_guide.py`
- `templates/faculty_portal/partials/grade_explanation_modal.html`
- `apps/faculty_portal/services.py`
- `apps/faculty_portal/views.py`
- `apps/faculty_portal/urls.py`
- `apps/faculty_portal/help_guide.py`
- `apps/faculty_portal/tests_performance.py`
- `apps/faculty_portal/tests_assignment_acceptance.py`
- `templates/faculty_portal/dashboard.html`
- `templates/faculty_portal/base.html`
- `templates/faculty_portal/offering_periods.html`
- `templates/faculty_portal/class_performance.html`
- `templates/faculty_portal/student_performance_consultation.html`
- `templates/faculty_portal/parallel_section_comparison.html`
- `templates/faculty_portal/guide.html`
- `templates/faculty_portal/guide_manual.html`
- `CHANGE_LOG.md`
- `TEACHERMATEPLUS_CONTEXT.md`
- `HANDOFF.md`
- `apps/accounts/views.py`
- `apps/accounts/tests_privacy_consent.py`
- `apps/grading/explanations.py`
- `apps/faculty_portal/views.py`
- `apps/faculty_portal/help_guide.py`
- `templates/faculty_portal/base.html`
- `templates/faculty_portal/password_change.html`
- `templates/faculty_portal/period_summary.html`
- `templates/grading/grade_explanation_detail.html`
- `apps/admin_portal/help_guide.py`
- `apps/admin_portal/tests_help_guide.py`
- `apps/faculty_portal/help_guide.py`
- `apps/faculty_portal/tests_help_guide.py`
- `templates/admin_portal/guide_role_based.html`
- `templates/faculty_portal/guide_role_based.html`
- `templates/admin_portal/tools/configurable_features.html`
- `apps/accounts/services.py`
- `apps/accounts/tests_admin_password_reset.py`
- `templates/accounts/emails/admin_password_reset_otp.html`
- `templates/accounts/emails/admin_password_reset_otp.txt`
- `templates/admin_portal/partials/password_recovery_brand.html`
- `templates/admin_portal/password_forgot.html`
- `templates/admin_portal/password_forgot_done.html`
- `templates/admin_portal/password_reset_otp.html`
- `templates/admin_portal/password_reset_confirm.html`
- `templates/admin_portal/password_reset_complete.html`
- `apps/core/services/permissions.py`
- `apps/core/services/features.py`
- `apps/core/management/commands/seed_stage_0_1.py`
- `apps/rbac/migrations/0012_limit_reopen_review_to_campus_admin.py`
- `apps/grading/notifications.py`
- `apps/notifications/services.py`
- `apps/notifications/tests.py`
- `templates/notifications/emails/submission_non_compliance_notice.html`
- `templates/notifications/emails/submission_non_compliance_notice.txt`
- `apps/admin_portal/tests_reopen_requests.py`
- `apps/admin_portal/tests_assignment_acceptance.py`
- `ops/cron/teachermateplus.cron`
- `docs/DEPLOYMENT_UBUNTU.md`
- `templates/accounts/login_otp.html`
- `templates/admin_portal/password_reset_otp.html`
- `templates/faculty_portal/partials/password_recovery_brand.html`
- `apps/student_portal/templates/student_portal/base.html`
- `apps/core/middleware.py`
- `config/settings/base.py`
- `apps/accounts/tests_faculty_password_reset.py`
- `apps/accounts/urls.py`
- `apps/accounts/services.py`
- `apps/accounts/tests_admin_password_reset.py`
- `apps/accounts/views.py`
- `apps/faculty_portal/tests_public_login.py`
- `static/faculty_portal/css/public_index.css`
- `templates/faculty_portal/public_index.html`
- `apps/grading/models.py`
- `apps/grading/migrations/0027_gradingtemplatesubcomponent_detail_computation_mode.py`
- `apps/grading/services.py`
- `apps/grading/reporting.py`
- `apps/grading/duplication.py`
- `apps/grading/tests.py`
- `apps/faculty_portal/views.py`
- `apps/faculty_portal/tests_assignment_acceptance.py`
- `apps/predictions/services.py`
- `apps/admin_portal/forms.py`
- `apps/admin_portal/views.py`
- `templates/admin_portal/grading/subcomponent_table.html`
- `templates/admin_portal/grading/detail_table.html`
- `templates/admin_portal/grading/template_builder.html`
- `templates/admin_portal/grading/template_structure_preview.html`
- `templates/admin_portal/emails/new_user_credentials.html`
- `templates/admin_portal/emails/new_user_credentials.txt`
- `templates/admin_portal/guide.html`
- `CHANGE_LOG.md`
- `TEACHERMATEPLUS_CONTEXT.md`
- `docs/TENANT_GRADING_PROFILE_SETUP_GUIDE.md`
- `HANDOFF.md`
- `logs/errors.log` changed because the reported calculator AttributeError was logged by the dev server.
- `logs/security.log` changed because one local Django test-client render attempt used the default `testserver` host before rerunning with `127.0.0.1:8000`.
- `logs/system.log` changed from dev-server logging during the session.

## Pending Work
- Run the Department Visibility manual test above with representative Superadmin, Department A Dean/Area Chair, Department B Dean/Area Chair, and multi-department accounts in a browser.
- Confirm production applies migration `grading.0028_gradingtemplate_department_visibility_and_more` before admins use the new visibility controls.
- Manually review `/admin-portal/guide/grading-template-setup/` at desktop, tablet, and mobile widths when a browser target is available. Confirm hero actions wrap cleanly, hierarchy chips remain readable, and wide decision tables scroll without clipping.
- Manually review `/admin-portal/guide/` at desktop and mobile widths with representative grading-admin and hotfix-reviewer accounts. Confirm the new `Where to start` cards, numbered steps, long menu paths, and action tables wrap cleanly.
- Manually review the redesigned `/faculty/guide/` at desktop, tablet, and mobile widths when a browser target is available. Confirm accordion animation/focus, deep-link expansion, workflow image sizing, Top Faculty Tasks wrapping, and table horizontal scrolling.
- Manually review `/faculty/guide/` and `/faculty/guide/manual/` at desktop and mobile widths when a browser target is available, especially the portrait workflow image and hero action wrapping.
- Manually review the new Class Performance Explain modal and Student Consultation SVG graphs at desktop and mobile widths when a browser target is available.
- Perform the 15-step Student Consultation browser checklist below with a production-like Faculty account after deployment.
- Perform a manual desktop/mobile browser review of the Faculty Dashboard, Class Performance, Student Consultation, and Parallel Section Comparison pages when the in-app browser target is available.
- Investigate the existing Admin Portal regression failures in configurable-feature saves and Faculty Final Clearance assignment snapshots before requiring the repository-wide suite to be fully green.
- Consider expanding the submission snapshot in a future migration so it freezes the exact template weights, component decimals, detail rules, and computed contributions used at submission. Current snapshots preserve the official stored result but cannot always reconstruct the complete historical decimal trail after setup changes.
- Perform a manual visual review of the redesigned `Explain This Grade` modal at desktop and mobile sizes when a browser target is available, confirming the privacy shield fully blocks the background and the cards stack cleanly.
- Perform a manual browser review of both revised guide pages using representative Campus Admin, academic reviewer, Registrar, Superadmin, and Faculty accounts when the in-app browser is available.
- Ask school users to review the revised wording. If they prefer the original presentation, turn off `Use the revised role-based Help Guide` in Configuration Management.
- Verify production installs the updated `ops/cron/teachermateplus.cron` after deployment; pulling the repository alone does not update the user's installed crontab.
- Create the new GitHub repository at `privatePol/teachermateplus` before pushing, then push the renamed branch.
- Continue with the next management/academic-head demo adjustment from the user.
- If the institution wants simultaneous logins allowed, turn off `Allow only one active login session per user` in `Admin Portal -> Tools -> Configuration Management -> Login Security`.
- If Grade Prediction or Student Intervention Monitor is missing in another environment, enable it from `Admin Portal -> Tools -> Configuration Management -> Grade Prediction`, make sure the `FACULTY` role is included, and turn on the intervention/monitor flag.
- If the institution proceeds with server hardening, create an implementation checklist/doc before changing production partitions or moving `MEDIA_ROOT`.
- Browser smoke test Admin Course create/edit/list and Faculty My Classes syllabus icon/redirect when convenient.
- Browser smoke test Admin grading-template subcomponent create/edit/list/builder/structure-preview for the new `Detail Computation` UI when convenient.
- DepEd E-Class Record work is paused. Do not continue import/export, presets, MAPEH handling, or diagnostics unless the user resumes that topic.
- Preserve previous unresolved continuity items:
  - Future sessions should continue to use `AGENTS.md`, the context document, `CHANGE_LOG.md`, and this file as the first-read continuity set.
  - Open governance/design topics remain in the context document, including expanded grading methodology options, active AY/Term governance, correction/reopen policy finalization, passing-threshold management, and configurable feature governance.

## Known Issues / Risks
- Department Visibility controls Admin access and governance only. It intentionally does not change template resolution for courses or faculty gradebooks already using a template.
- Non-superusers need an active non-Faculty department-scoped role assignment to access a `Selected Departments` template, in addition to the required RBAC permission. Tenant/campus roles with no department assignment continue to see `All Departments` templates but do not automatically see selected templates.
- Parent academic-unit assignments cover active child departments through the existing scope hierarchy. A child assignment does not automatically grant access to a parent-only template.
- Same-tenant validation for `visible_departments` is enforced by the Admin form rather than a database constraint on the many-to-many table. Future scripts or imports that set this relation directly must preserve the same-tenant rule.
- Browser visual QA for the new conditional department picker and visibility summaries remains pending.
- The new Grading Template Setup Guide passed permission, navigation, content, and rendering tests, but visual browser validation could not run because the in-app browser target was unavailable.
- Average Activities ignores detail weights in computation, but `GradingTemplateDetail.weight_percentage` remains a required model/form field. The guide recommends a clean equal distribution totaling 100% for administrative clarity; changing the form to make that field optional or disabled in averaging mode would be a separate UI/model decision.
- The hotfix apply mode limits which offerings are immediately recomputed, but it does not create an offering-specific copy of the shared grading template. Admins must review all courses resolving to the template before changing its structure.
- Visual browser validation of the new Admin guide menu-path and hotfix sections could not run because the in-app browser target `iab` was unavailable. Focused rendering and role-filtering tests passed.
- Participation/Output `Average Activities` is configured on `GradingTemplateSubcomponent`; there is no equivalent computation-mode field directly on a top-level component. A Participation/Output top-level component is covered when its detail-bearing child subcomponent carries `Average Activities`.
- TeacherMate+ has no separate selected/unselected activity flag. Faculty inclusion is represented by an active `GradeActivity`; deleting/deactivating the activity excludes it. Readiness also requires its referenced template hierarchy to remain active.
- Existing template validation requires component weights to total 100, but subcomponent/detail weight totals only need to be greater than zero and are normalized by the computation service. This review preserved that existing weighted-mode rule; changing it to require an exact 100 total would be a separate grading-governance change.
- Browser screenshot validation of the redesigned Faculty Help Guide could not be completed because the in-app browser target was unavailable. Django template rendering, accordion markup, responsive classes, and all Faculty Portal regressions passed.
- Browser screenshot validation of the updated Faculty Help Guide and Full Faculty Manual could not be completed because the in-app browser target was unavailable. Focused Django rendering tests and responsive CSS review passed.
- Student Consultation uses live official computation against the current grading template and source records. It does not provide an immutable historical computation snapshot if an old submitted template was later changed.
- Components renamed between grading periods appear as separate actual-label trend rows; TeacherMate+ deliberately does not guess that differently named template items are equivalent.
- Browser screenshot validation of the Period Grade SVG, component sparklines, responsive stacking, and table fallback could not be completed because the in-app browser target was unavailable. Django rendering and behavior assertions passed.
- The completed 453-test repository suite still has five failures and one error in existing untouched Admin Portal tests: configurable-feature saves and Faculty Final Clearance assignment snapshots. The same Admin failures predate and are unrelated to the Student Consultation graph review.
- Inline SVG component series use the actual label in each grading period. If an institution renames the same conceptual component between periods, the renamed labels appear as separate series instead of being guessed as equivalent.
- The complete `python manage.py test` run currently has five failures and one error in existing untouched Admin Portal tests: three configurable-feature save tests and two Faculty Final Clearance tests. The same failures reproduce when those Admin tests are run independently.
- No browser screenshot verification was completed for the new Faculty performance pages because the in-app browser target `iab` was unavailable. Django-rendered templates and access/privacy assertions passed.
- Phase 1 intentionally has no chart library. Parallel comparison uses an exact-value table, CSS bars, and deterministic text; there is no median, ranking, percentile, prediction, or cross-faculty comparison.
- Performance values are computed live and read-only. Large sections or many parallel sections may benefit from caching or further query profiling in a later phase, but no analytics table was introduced.
- Historical grade submissions do not contain a complete immutable computation snapshot. The explanation now clearly separates the official stored grade from a current-template comparison, but exact old decimal contributions may be unavailable after template or source-record changes.
- The grade-explanation redesign passed template rendering and the full Faculty Portal regression suite, but visual screenshot verification could not be completed because no in-app browser target was connected.
- The revised guides passed server-side rendering and role-visibility tests, but a visual browser smoke test could not be completed because the local in-app browser target `iab` was unavailable.
- Admin topic visibility uses effective scoped permissions, while the Superadmin-only section additionally checks Superadmin identity. Future sensitive guide topics must retain both controls and default to hidden.
- The hero animation intentionally remains static when the device/browser reports `prefers-reduced-motion: reduce`. If production still shows no movement after deployment and a hard refresh, check the operating-system accessibility animation setting.
- Portal cache behavior was validated through response-header and rendered-script regression tests. The local in-app browser smoke test was attempted but its browser target was unavailable; a manual Back-button smoke test should still be performed after deployment because browser and reverse-proxy cache behavior can vary.
- Browser smoke tests were not run for the Create User field change, onboarding email, or Faculty password-reset pages; validation was performed with focused Django tests.
- Browser smoke test for the Faculty public landing logo replacement was not run because no in-app Browser tool was available in this session; validation was command/test based.
- Browser smoke test for the authenticated Faculty header logo replacement was not run because no in-app Browser tool was available in this session; validation was command/test based.
- Browser smoke test for the Faculty top-nav subtitle spacing was not run because no in-app Browser tool was available in this session; validation was command/test based.
- Browser smoke test for the Faculty public landing copy change was not run because no in-app Browser tool was available in this session; validation was command/test based.
- Browser smoke test for the broader Faculty public landing internal-voice rewrite was not run because no in-app Browser tool was available in this session; validation was command/test/search based.
- Browser smoke test for the Admin login logo replacement was not run because no in-app Browser tool was available in this session; validation was command/test based.
- Browser smoke test for the Admin left-nav logo replacement was not run because no in-app Browser tool was available in this session; validation was command/test based.
- Browser smoke test for the Admin topbar scope dropdown layout was not run because no in-app Browser tool was available in this session; validation was command/test based.
- The new GitHub repo was not created from this workspace; `origin` has been repointed but push will fail until `privatePol/teachermateplus` exists and credentials allow access.
- Browser/admin/faculty smoke tests were not run for the rename-only sweep; validation was command/search based.
- Browser smoke tests were not run for the syllabus feature; validation was command/test based.
- Browser smoke tests were not run for the Faculty active-period display changes; validation was command/test based.
- Browser smoke tests were not run for the Faculty deadline reminder and dashboard card color changes; validation was command/test based.
- Browser smoke tests were not run for the Faculty Dashboard guide-tag links; validation was command/test based.
- Browser smoke tests were not run for the Faculty My Classes guide-tag links; validation was command/test based.
- Browser smoke tests were not run for the Faculty Summary table layout change; validation was command/test based.
- Browser smoke tests were not run for the Faculty Summary `P/O AVE` label fix; validation was command/test based.
- Browser smoke tests were not run for the Faculty Summary empty-detail visibility rule; validation was command/test based.
- Browser smoke tests were not run for the reopen notification recipient correction; validation was command/test based.
- Browser smoke tests were not run for the role-permissions page description change; validation was command/test based.
- Browser smoke tests were not run for the role-permissions save-position change; validation was command/test based.
- Browser smoke tests were not run for the role-permissions Critical Access Safeguard visibility change; validation was command/test based.
- Browser smoke tests were not run for the Admin Portal login logo change; validation was command/render based.
- Browser smoke tests were not run for the Admin Portal login copy change; validation was command/render based.
- Browser smoke test for the Admin Portal login NPC seal was attempted, but the in-app Browser target `iab` was unavailable; validation was Django render based.
- Browser smoke tests were not run for the Admin Guide layout/wording change because the Browser tool was not callable in this session; validation was command/render based.
- Browser smoke tests were not run for the new Admin Student Enrollment Query; validation was command/test based.
- Browser smoke tests were not run for the Faculty activity-score Enter-key guard; validation was command/render-test based.
- Browser smoke tests were not run for the Active Grading Period setup change; validation was command/test/data-inspection based.
- Browser smoke tests were not run for the new subcomponent `Detail Computation` UI; validation was command/test/migration based.
- Browser smoke tests were not run for the template calculator fix; validation was Django client regression tests for the reported URL pattern.
- The local `logs/system.log` changed during this session because the already-running dev server logged page requests/reloads; this was not part of the feature implementation.
- The local `logs/security.log` contains one additional `DisallowedHost: testserver` entry from a manual Django client render check; the check was rerun successfully with `127.0.0.1:8000`.
- Google Drive access depends on the institution's Google Workspace sharing settings. Faculty must use an allowed school Google account if the file is domain-restricted.
- Email styling was validated by template rendering and focused email tests, not by opening emails in a real email client.
- The simultaneous-login setting was validated by focused Django client/session tests, not by a manual browser smoke test.
- The Faculty Portal assignment/period-card/summary UI changes were validated by focused Django render tests, not by a manual browser smoke test.
- The Student Intervention Monitor redesign was validated by focused Django render tests and `manage.py check`, not by a manual browser smoke test.
- The Grade Prediction / Students At-Risk Monitor fix was validated by configuration inspection, `manage.py check`, and focused Django tests, not by a manual browser smoke test.
- The Faculty Portal public-login, password-recovery return-link, and password-recovery logo fixes were validated by Django client tests and `manage.py check`, not by a manual browser smoke test.
- The NCBA Grade Prediction feature was enabled in the local development database only; other environments must be configured through Configuration Management.
- The prior DepEd implementation remains uncommitted in the same working tree. If the project wants that work parked or reverted, handle it explicitly and carefully.
- `TEACHERMATEPLUS_CONTEXT.md` now exists under the expected filename; the working tree still shows the rename from `TEACHERMATEPLUS_CONTEXT.md` as an uncommitted delete/add.
- The ransomware/partitioning discussion was advisory only. No server partition, mount, Synology, antivirus, or Django `MEDIA_ROOT` configuration changes were applied in this workspace.

## Manual Test Steps
### Faculty Help Guide Browser Check
1. Log in as Faculty and open `/faculty/guide/`.
2. Confirm the hero shows `Full Guide Manual` and `Back to Faculty Portal`.
3. Confirm `Start Here: Daily Faculty Workflow` and the workflow image remain immediately visible.
4. Confirm all six Top Faculty Tasks display cleanly at desktop, tablet, and mobile widths.
5. Confirm the first Detailed Faculty Reference accordion is open and the remaining four are collapsed.
6. Expand each accordion by mouse, keyboard, and touch; confirm only the selected group remains open.
7. Use the topic navigation and a deep link such as `#guide-submission`; confirm the owning accordion opens.
8. Confirm action tables have deep-green headers, alternating green/cream rows, clear hover feedback, and readable borders.
9. At mobile width, confirm action tables scroll horizontally without shrinking text or breaking the page.
10. Expand the performance group and confirm the Student Consultation and Parallel Section Comparison callouts are readable and stack vertically on narrow screens.

### Student Consultation Browser Check
1. Log in as a Faculty user.
2. Open the Faculty Dashboard and confirm no student performance graph or student list appears.
3. Open an accepted assigned class.
4. Open `Class Performance` and confirm the page shows its class snapshot without all-student trend graphs.
5. Select one student from Students Requiring Attention.
6. Confirm Student Consultation opens at `/faculty/my-courses/<offering>/periods/<period>/performance/students/<student>/`.
7. Confirm `Performance Trend` appears below the four summary cards and above Primary Reason.
8. Confirm Period Grade Trend shows the selected student's available period-to-period movement and the exact-value period table matches the plotted values.
9. Confirm Component Average Trend shows actual component, subcomponent, and configured detail labels with exact values and sparklines.
10. Confirm the identity header, graphs, interpretation, missing outputs, and breakdown belong only to the selected student.
11. Confirm no other student name, grade, class average, ranking, comparison, or background class table is visible.
12. Open a one-period-only case and confirm `Trend graph will appear after another grading period is available.`
13. Open a case with missing period/component data and confirm `No data`, `No computed grade trend is available yet.`, or `Component trend is not available for this period.` appears as appropriate.
14. Change the URL to another faculty member's offering/student and confirm the standard 404 or permission-denied response.
15. Record grade, activity-score, attendance, submission, and lock counts/timestamps before and after opening the consultation page; confirm none changed.

## Validation Completed
- [x] Migration generated and applied: `grading.0028_gradingtemplate_department_visibility_and_more`.
- [x] Migration SQL reviewed with `python manage.py sqlmigrate grading 0028`; existing template rows receive `department_visibility='ALL'`, and the migration adds only the visibility field and department join table.
- [x] `python manage.py showmigrations grading` confirms `[X] 0028_gradingtemplate_department_visibility_and_more`; `python manage.py migrate --plan` reports no pending operations.
- [x] `python manage.py migrate` completed with no pending migrations.
- [x] Department Visibility focused suite passed: `python manage.py test apps.admin_portal.tests_template_department_visibility` (22 tests), including the Visible Department campus-label regression.
- [x] Admin template governance/calculator/help-guide regression passed: `python manage.py test apps.admin_portal.tests_template_governance apps.admin_portal.tests_template_calculator apps.admin_portal.tests_help_guide` (31 tests).
- [x] Faculty performance regression passed: `python manage.py test apps.faculty_portal.tests_performance` (26 tests).
- [x] Faculty assignment, grade encoding, Summary, submission, and locking regression passed: `python manage.py test apps.faculty_portal.tests_assignment_acceptance` (112 tests).
- [x] Full grading-engine suite passed: `python manage.py test apps.grading.tests` (48 tests).
- [x] `python manage.py makemigrations --check --dry-run` reported no model drift.
- [x] `python manage.py check` passed.
- [x] Python syntax compilation passed for the reviewed access, form, service, view, and focused test modules.
- [x] `git diff --check` found no whitespace errors; only existing LF-to-CRLF working-copy warnings were reported.
- [x] Repository-wide `python manage.py test` completed 501 tests: 495 passed; the same five configurable-feature/Faculty Final Clearance failures and one Final Clearance error already documented below remain. No Department Visibility, grading-template governance, calculator, profile, assignment, grading-engine, or faculty-template regression failed.
- [x] Practical/full Admin guide navigation and role-isolation suite passed: `python manage.py test apps.admin_portal.tests_help_guide` (10 tests).
- [x] Explicit `?view=full` and `?view=practical` guide overrides passed without changing the configured tenant default.
- [x] Campus Admin full-guide regression confirmed the Superadmin-only Production Incident Response section and link remain hidden.
- [x] `python manage.py check` passed after the practical/full Admin guide navigation update.
- [x] Role-based Admin Help Guide focused suite passed: `python manage.py test apps.admin_portal.tests_help_guide` (7 tests).
- [x] Admin Help Guide tests confirmed the exact Grade Formula Setup menu path, Builder and Average Activities instructions, removal of Direct Percentage wording, hotfix visibility with permission, and hotfix isolation without permission.
- [x] `python manage.py check` passed after the Admin Help Guide update.
- [x] Python syntax compilation passed for `apps/admin_portal/help_guide.py` and `apps/admin_portal/tests_help_guide.py`.
- [ ] Admin Help Guide desktop/mobile visual browser QA was attempted but could not run because the in-app browser target `iab` was unavailable.
- [x] Chief Academic Officer Participation/Output policy review suite passed: 14 focused tests covering averaging zero/one activity, inactive activity/detail exclusion, unused details, weighted missing/valid/zero behavior, invalid zero-total weighted setup, non-Participation/Output strictness, blank scores, read-only evaluation, assignment access, and unchanged computation.
- [x] Cross-faculty submission endpoint regression passed: another Faculty account receives the standard 404 and creates no submission record.
- [x] Full Faculty assignment/grade workflow suite passed after the policy review correction: `python manage.py test apps.faculty_portal.tests_assignment_acceptance -v 1` (110 tests).
- [x] Full grading-engine suite passed after the policy review correction: `python manage.py test apps.grading.tests -v 1` (48 tests).
- [x] `python manage.py check` passed after the policy review correction.
- [x] `python manage.py makemigrations --check --dry-run` reported no model changes.
- [x] Repository-wide suite completed after the policy review: `python manage.py test -v 1` found 468 tests and reproduced only the same five failures plus one error in the pre-existing Admin configurable-feature/Faculty Final Clearance tests.
- [x] Participation/Output submission-policy focused suite passed: 7 tests covering zero/one active averaging items, unused details, weighted-mode strictness, non-Participation/Output strictness, encoded zero, blank student records, and unchanged averaging computation.
- [x] Full Faculty assignment/grade workflow suite passed after the readiness change: `python manage.py test apps.faculty_portal.tests_assignment_acceptance -v 1` (103 tests).
- [x] Full grading-engine suite passed after the readiness change: `python manage.py test apps.grading.tests -v 1` (48 tests).
- [x] `python manage.py check` passed after the readiness change.
- [x] `python manage.py makemigrations --check --dry-run` reported no model changes.
- [x] Repository-wide suite completed after the readiness change: `python manage.py test -v 1` found 460 tests and reproduced the same five failures plus one error in the pre-existing Admin configurable-feature/Faculty Final Clearance tests; the new readiness tests, full Faculty assignment suite, and full grading suite passed.
- [x] Redesigned Faculty Help Guide focused suite passed: `python manage.py test apps.faculty_portal.tests_help_guide` (3 tests).
- [x] Full Faculty Portal regression suite passed after the guide redesign: `python manage.py test apps.faculty_portal -v 1` (137 tests).
- [x] Repository-wide suite completed after the guide redesign: `python manage.py test -v 1` found 454 tests and finished with the same five failures plus one error in existing Admin configurable-feature/Faculty Final Clearance tests; all Faculty tests passed.
- [x] `python manage.py check` passed after the guide redesign.
- [x] No new frontend dependency was added; the page uses the Bootstrap 5.3 accordion and bundle already loaded by the Faculty Portal base template.
- [ ] Redesigned Faculty Help Guide desktop/mobile browser QA was attempted but could not run because the in-app browser target was unavailable.
- [x] Faculty documentation policy scan confirmed `Direct Percentage` and `Weighted Details` no longer appear in the active guide, fallback guide, or Full Faculty Manual.
- [x] Faculty Help Guide focused suite passed after the Base-50/equal-averaging revision: `python manage.py test apps.faculty_portal.tests_help_guide` (3 tests).
- [x] Required `python manage.py check` passed after the Base-50/equal-averaging documentation revision.
- [x] Faculty Help Guide focused suite passed: `python manage.py test apps.faculty_portal.tests_help_guide` (3 tests).
- [x] Faculty Help Guide/full-manual `git diff --check` completed with no whitespace errors; only existing line-ending warnings were reported.
- [x] Required `python manage.py check` passed with no issues after the guide/manual update.
- [ ] Faculty Help Guide browser visual smoke test was attempted but could not run because the in-app browser target was unavailable.
- [x] Student Consultation review focused suite passed: `python manage.py test apps.faculty_portal.tests_performance -v 2` (26 tests).
- [x] Full Faculty Portal regression suite passed: `python manage.py test apps.faculty_portal -v 1` (136 tests).
- [x] Repository-wide suite completed: `python manage.py test -v 1` found 453 tests and finished with the same five failures plus one error in existing Admin Portal configurable-feature/Faculty Final Clearance tests; all Faculty performance tests passed.
- [x] `python manage.py check` passed with no issues.
- [x] `python manage.py makemigrations --check --dry-run` reported no changes.
- [x] Source scan confirmed no Chart.js, Recharts, ApexCharts, or other frontend chart library was added.
- [x] Template regressions confirm the graph appears only on selected-student consultation and not on Dashboard or Class Performance.
- [ ] Browser visual smoke test was attempted but could not run because the in-app browser target was unavailable.
- [x] Focused Faculty performance suite passed: `python manage.py test apps.faculty_portal.tests_performance -v 2` (25 tests).
- [x] Full Faculty Portal suite passed: `python manage.py test apps.faculty_portal -v 1` (135 tests).
- [x] Non-Admin/non-Faculty app suites passed: `python manage.py test apps.academics apps.accounts apps.core apps.grading apps.imports apps.notifications apps.predictions apps.student_portal -v 1` (144 tests).
- [x] Admin Portal suite completed: `python manage.py test apps.admin_portal -v 1` (173 tests; existing five failures and one error reproduced).
- [x] `python manage.py check` passed with no issues.
- [x] `python manage.py makemigrations --check --dry-run` reported no model changes.
- [x] `git diff --check` reported no whitespace errors; only existing line-ending warnings.
- [ ] Browser visual review could not run because the in-app browser target was unavailable.
- [x] Post-review focused performance suite passed: `python manage.py test apps.faculty_portal.tests_performance -v 1` (19 tests after the final no-baseline case).
- [x] Post-review full Faculty Portal suite passed: `python manage.py test apps.faculty_portal -v 1` (129 tests after the final no-baseline case).
- [x] Post-review `python manage.py check` passed with no issues.
- [x] Post-review `python manage.py makemigrations --check --dry-run` reported no changes.
- [x] Repository-wide suite executed: `python manage.py test -v 1` found 445 tests and completed with five failures plus one error in the pre-existing Admin Portal configurable-feature/Faculty Final Clearance tests. The Faculty Portal suite is fully green.
- [x] The Admin Portal test class was rerun independently and reproduced the same five failures plus one error:
  - configurable enrollment ownership mode was not saved
  - class master-list override was not saved
  - official grade release POST returned 200 instead of 302
  - final-clearance preview omitted the course
  - final-clearance verification snapshot omitted the course
  - zero-active-student final-clearance preview returned no row
- [x] Template scan confirmed no rendered or dead Dashboard references to Students Needing Follow-up, Student Support, or Priority Actions remain.
- [x] Dependency scan confirmed no Chart.js, Recharts, ApexCharts, or other chart library was introduced.
- [ ] Browser visual smoke test was attempted again but could not run because the in-app browser target was unavailable.
- [x] New Faculty performance suite passed: `python manage.py test apps.faculty_portal.tests_performance` (10 tests).
- [x] Full Faculty Portal suite passed: `python manage.py test apps.faculty_portal` (120 tests).
- [x] Faculty performance plus Help Guide focused suite passed: `python manage.py test apps.faculty_portal.tests_performance apps.faculty_portal.tests_help_guide` (12 tests before the final interpretation test was added; the final full Faculty suite includes all 120 tests).
- [x] `python manage.py check` passed after implementation and documentation updates.
- [x] `git diff --check` reported no whitespace errors; only repository line-ending warnings were shown.
- [x] Read-only regression confirmed Class Performance and Parallel Comparison GET requests do not create or update stored period grades or activity scores.
- [x] Full project suite executed: `python manage.py test` found 437 tests. Result: 431 passed; five failures and one error remain in existing untouched Admin Portal tests.
- [x] The five affected Admin tests were rerun independently and reproduced the same failures, confirming they are not caused by test ordering from the new Faculty suite.
- [ ] Browser visual smoke test was attempted but could not run because the in-app browser target was unavailable.
- [x] Broader account-security regression suite passed: `python manage.py test apps.accounts.tests_admin_password_reset apps.accounts.tests_faculty_password_reset apps.accounts.tests_login_lockout apps.accounts.tests_login_otp apps.accounts.tests_privacy_consent apps.accounts.tests_signatures` (32 tests). The logged SMTP rejection is an intentional tested failure path.
- [x] Revised Faculty Help Guide tests passed after the forced-change wording update: `python manage.py test apps.faculty_portal.tests_help_guide` (2 tests).
- [x] Faculty forced-password-change and related account regressions passed: `python manage.py test apps.accounts.tests_privacy_consent apps.accounts.tests_login_otp apps.accounts.tests_signatures` (15 tests).
- [x] Required-change tests cover locked initial render, lock persistence after invalid submission, and navigation restoration after successful password change.
- [x] `python manage.py check` and `python manage.py makemigrations --check --dry-run` passed; no migration is required.
- [x] Focused `git diff --check` passed with only existing line-ending warnings.
- [ ] Visual password-change page verification was not completed because no in-app browser target was connected.
- [x] Faculty Summary group-color and `CS AVE` alignment focused tests passed: `python manage.py test apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_period_summary_average_activities_display_matches_detail_computation_mode apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_period_summary_weighted_details_keeps_empty_detail_columns` (2 tests).
- [x] Full Faculty Portal assignment/grade workflow suite passed after the Summary header refinement: `python manage.py test apps.faculty_portal.tests_assignment_acceptance` (97 tests).
- [x] `python manage.py check` and `python manage.py makemigrations --check --dry-run` passed after the Summary header refinement; no migration is required.
- [x] Focused `git diff --check` passed with only existing line-ending warnings.
- [ ] Visual Summary-table verification was not completed because no in-app browser target was connected.
- [x] Reported rounding path verified: `FacultyGradingService._round_official_grade` uses whole-number `ROUND_HALF_UP`; the contradictory screenshot values were reproduced as stored-grade versus current-template differences.
- [x] Submitted-grade/current-template mismatch regression passed: `python manage.py test apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_period_summary_shows_official_period_grade_by_default_without_release_restriction` (1 test).
- [x] Full Faculty Portal assignment/grade workflow suite passed after the mismatch fix: `python manage.py test apps.faculty_portal.tests_assignment_acceptance` (97 tests).
- [x] Grade computation and correction recomputation regressions passed after the mismatch fix: `python manage.py test apps.grading.tests.FinalGradeFormulaTests apps.grading.tests.CorrectionWorkflowTests.test_final_approval_recomputes_average_activity_detail_mode` (15 tests).
- [x] `python manage.py check` passed after the mismatch fix.
- [x] Focused `git diff --check` passed with only existing line-ending warnings.
- [ ] Visual modal verification was attempted, but no in-app browser target was connected.
- [x] Full Faculty Portal assignment/grade workflow suite passed: `python manage.py test apps.faculty_portal.tests_assignment_acceptance` (97 tests).
- [x] Grade explanation and Faculty guide focused tests passed: `python manage.py test apps.faculty_portal.tests_help_guide apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_period_summary_shows_official_period_grade_by_default_without_release_restriction` (3 tests).
- [x] Grade computation and correction recomputation regressions passed: `python manage.py test apps.grading.tests.FinalGradeFormulaTests apps.grading.tests.CorrectionWorkflowTests.test_final_approval_recomputes_average_activity_detail_mode` (15 tests).
- [x] Grade explanation, period summary, and guide templates loaded successfully through Django's template loader.
- [x] `python manage.py check` and `python manage.py makemigrations --check --dry-run` passed; no migration is required.
- [ ] Visual desktop/mobile modal smoke test was not completed because no in-app browser target was available.
- [x] Revised Help Guide focused tests passed: `python manage.py test apps.admin_portal.tests_help_guide apps.faculty_portal.tests_help_guide` (6 tests).
- [x] Configurable Features regression tests passed for rendering and saving existing settings (2 focused tests).
- [x] Revised and legacy Admin/Faculty template selection was verified by Django client tests.
- [x] Revised guide templates and Configuration Management template loaded successfully through Django's template loader.
- [x] `python manage.py check` passed with no issues.
- [x] `git diff --check` reported no whitespace errors; only existing line-ending warnings were shown.
- [ ] Visual browser smoke test was attempted but not completed because the in-app browser target `iab` was unavailable.
- [x] Admin password-reset branding and OTP email regressions - passed: `python manage.py test apps.accounts.tests_admin_password_reset` (6 tests).
- [x] Django system check after Admin reset branding changes - passed: `python manage.py check`.
- [ ] Browser visual smoke test was not completed because the in-app browser was unavailable; rendered Django response assertions covered the public and protected reset pages instead.
- [x] Assignment-driven reopen governance and daily overdue notification suites - passed: `python manage.py test apps.admin_portal.tests_reopen_requests apps.notifications.tests` (19 tests).
- [x] Configurable Features deadline policy save regression - passed: `python manage.py test apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_configurable_features_can_store_assignment_workflow_settings`.
- [x] Migration drift check - passed: `python manage.py makemigrations --check --dry-run` reported no changes.
- [x] Migration state check - passed: `python manage.py migrate` reported no migrations to apply.
- [x] Django system check after governance changes - passed: `python manage.py check`.
- [x] Deadline closure/reopen focused tests - passed: four tests covering deadline encoding closure, late submission blocking, scoped Admin queue visibility, and effective reviewer notification recipients.
- [x] Notification service suite - passed: `python manage.py test apps.notifications.tests` (7 tests).
- [x] Production scheduler audit - confirmed no `issue_submission_non_compliance_notices`, `queue_faculty_reminder_emails`, or `process_faculty_reminder_email_queue` entries exist under `ops`.
- [x] Legacy logo authentication-page regressions - passed: `python manage.py test apps.faculty_portal.tests_public_login apps.accounts.tests_login_otp apps.accounts.tests_admin_password_reset` (15 tests).
- [x] Final live-template legacy-logo scan - passed: no `egp_logo`, `edp_logo`, `edugrade`, or `teachermateplus_logo.png` references remain in template HTML/text files.
- [x] Static deployment dry run after logo replacement - passed: `python manage.py collectstatic --noinput --dry-run`.
- [x] Legacy live-template logo scan - passed: no `egp_logo`, `edp_logo`, or `teachermateplus_logo.png` references remain in live template directories.
- [x] Faculty reset email text-brand regression - passed through `python manage.py test apps.accounts.tests_faculty_password_reset`.
- [x] Faculty reset eligibility and public hero regression suites - passed: `python manage.py test apps.accounts.tests_faculty_password_reset apps.faculty_portal.tests_public_login` (11 tests).
- [x] Static deployment dry run after production hero hardening - passed: `python manage.py collectstatic --noinput --dry-run`.
- [x] Faculty logout/back-button protection regression - passed: `python manage.py test apps.faculty_portal.tests_public_login.FacultyPublicLoginTests.test_protected_faculty_pages_cannot_be_restored_as_usable_after_logout`.
- [x] User onboarding and portal-reset focused tests - passed: `python manage.py test apps.accounts.tests_faculty_password_reset apps.admin_portal.tests_users.UserListTests.test_user_create_form_does_not_expose_is_staff apps.admin_portal.tests_users.UserListTests.test_new_user_credentials_email_uses_only_neutral_teachermate_link` (5 tests).
- [x] Broader user/password-reset regression - passed: `python manage.py test apps.admin_portal.tests_users apps.accounts.tests_faculty_password_reset apps.accounts.tests_admin_password_reset` (17 tests). The expected simulated SMTP-rejection error log appeared while the suite remained successful.
- [x] Django system check after onboarding/reset changes - passed: `python manage.py check`.
- [x] Migration drift check after onboarding/reset changes - passed: `python manage.py makemigrations --check --dry-run`; no changes detected.
- [x] Admin forgot-password workflow regression - passed: `python manage.py test apps.accounts.tests_admin_password_reset` (5 tests).
- [x] Admin forgot-password SMTP failure regression - passed in the same suite; simulated delivery failure is logged, audited, redirected safely, and leaves no active challenge.
- [x] Django system check after Admin forgot-password diagnostics change - passed: `python manage.py check`.
- [x] Faculty hero logo sparkle render regression - passed through `python manage.py test apps.faculty_portal.tests_public_login.FacultyPublicLoginTests.test_public_faculty_login_form_posts_to_landing_page`.
- [x] Django system check after hero logo animation - passed: `python manage.py check`.
- [x] Static deployment dry run after hero logo animation - passed: `python manage.py collectstatic --noinput --dry-run`.
- [x] Faculty privacy-seal slogan render regression - passed: `python manage.py test apps.faculty_portal.tests_public_login.FacultyPublicLoginTests.test_public_faculty_login_form_posts_to_landing_page`.
- [x] Django system check after privacy-seal slogan change - passed: `python manage.py check`.
- [ ] Visual browser smoke test for the privacy-seal slogan was not run because the local in-app browser target was unavailable.
- [x] Default root redirect regression - passed: `python manage.py test apps.faculty_portal.tests_public_login` (5 tests).
- [x] Django system check after root redirect - passed: `python manage.py check`.
- [x] Detail computation migration applied - passed: `python manage.py migrate` applied `grading.0027_gradingtemplatesubcomponent_detail_computation_mode`.
- [x] Detail computation migration check - passed: `python manage.py makemigrations --check --dry-run`
- [x] Detail computation Django check - passed: `python manage.py check`
- [x] Detail computation focused regression - passed: `python manage.py test apps.grading.tests.FinalGradeFormulaTests.test_subcomponent_can_average_faculty_activities_instead_of_detail_weights`
- [x] Faculty Summary Average Activities display/regression - passed: `python manage.py test apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_period_summary_average_activities_display_matches_detail_computation_mode`
- [x] Faculty Summary Participation/Output label regression - passed in the same focused test; it now asserts the nested subcomponent average header is `P/O AVE`.
- [x] Faculty Summary empty-detail visibility regression - passed: `python manage.py test apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_period_summary_average_activities_display_matches_detail_computation_mode apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_period_summary_weighted_details_keeps_empty_detail_columns`
- [x] Django system check after Faculty Summary label/visibility fixes - passed: `python manage.py check`
- [x] Local Faculty Summary render check - passed for `CourseOffering 419` / `MIDTERM 21`: page renders `96.25` for Participation/Output average and `CS AVE` for the final weighted Class Standing column; `87.75` remains the correct weighted Class Standing value for student `2025-10102`.
- [x] Affected local gradebook refresh - passed: recomputed `CourseOffering 419` / `MIDTERM 21`; sample stored Class Standing rows changed to corrected values (`98.00`, `98.00`, `86.00`, `86.00`, `88.00`).
- [x] Correction approval Average Activities regression - passed: `python manage.py test apps.grading.tests.CorrectionWorkflowTests.test_final_approval_recomputes_average_activity_detail_mode`
- [x] Full correction workflow regression - passed: `python manage.py test apps.grading.tests.CorrectionWorkflowTests` (20 tests)
- [x] Template calculator regression - passed: `python manage.py test apps.admin_portal.tests_template_calculator`
- [x] Grade distribution monitor regression - passed: `python manage.py test apps.admin_portal.tests_grade_distribution_monitor`
- [x] Prediction page focused regression - passed: `python manage.py test apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_period_prediction_page_uses_teacher_friendly_period_specific_labels`
- [x] Local data sanity check - passed: no existing `GradingTemplateSubcomponent` rows used the temporary `AVERAGE_DETAILS` value from the initial implementation attempt.
- [x] Rename residual content scan - passed: no legacy brand-family matches remain in repository text.
- [x] Rename residual path scan - passed: no file/directory names outside `.git` matched the legacy brand-family patterns.
- [x] Git remote update check - passed: `origin` fetch/push now point to `https://github.com/privatePol/teachermateplus.git`.
- [x] Rename Django check - passed: `python manage.py check`
- [x] Rename-focused Faculty public-login regression - passed: `python manage.py test apps.faculty_portal.tests_public_login`
- [x] Faculty public landing hero logo render assertion - passed through `python manage.py test apps.faculty_portal.tests_public_login`; response contains `logos/teachermate_logo_official.png` and does not contain the old hero image reference.
- [x] Authenticated Faculty header logo render assertion - passed through `python manage.py test apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_dashboard_active_grading_period_shows_ay_and_campus_name`; response contains `logos/teachermate_logo_official.png` and does not contain the old header image reference.
- [x] Faculty top-nav subtitle spacing/style assertion - passed through `python manage.py test apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_dashboard_active_grading_period_shows_ay_and_campus_name`; response contains the updated subtitle font size, line height, and margin-top rules.
- [x] Faculty public landing hero copy assertion - passed through `python manage.py test apps.faculty_portal.tests_public_login`; response contains `Welcome to NCBA's TeacherMate+` and `TeacherMate+ helps our faculty members manage teaching loads`.
- [x] Faculty public landing internal-voice assertions - passed through `python manage.py test apps.faculty_portal.tests_public_login`; response contains NCBA-specific faculty/SIS/grade-file wording and no longer contains client-facing phrases such as `your existing SIS`, `TeacherMate+ vs Standalone Grade Files`, or `helps institutions`.
- [x] Faculty public landing client-facing language scan - passed: `rg -n "institution|institutions|client|promot|your existing|TeacherMate\\+ vs Standalone|Standalone Spreadsheets|faculty users" templates\faculty_portal\public_index.html` returned no matches.
- [x] Admin login official text-logo render assertion - passed through `python manage.py test apps.accounts.tests_login_otp.LoginOtpTests.test_admin_login_uses_official_teachermate_text_logo`; response contains `logos/teachermate_logo_text_official.png` and does not contain the previous login logo reference.
- [x] Admin authenticated left-nav text-logo render assertion - passed through `python manage.py test apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_assignment_dashboard_view_loads`; response contains `logos/teachermate_logo_text_official.png` and does not contain the previous sidebar logo reference.
- [x] Admin left-nav reversed-gradient render assertion - passed through `python manage.py test apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_assignment_dashboard_view_loads`; response contains the reversed `linear-gradient(180deg, #214f25 0%, #39742d 32%, #4d8c33 68%, #5b9a37 100%)`.
- [x] Admin topbar scope dropdown layout assertion - passed through `python manage.py test apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_assignment_dashboard_view_loads`; response contains the desktop select sizing rule for same-row tenant/campus controls.
- [x] `python manage.py check` - passed
- [x] `python manage.py migrate` - passed; applied `academics.0009_course_syllabus_url` in earlier validation, later applied `rbac.0011_seed_student_enrollment_query_permission` and `navigation.0004_seed_student_enrollment_query_menu`, and this session applied `navigation.0005_move_student_enrollment_query_to_enrollment`
- [x] `python manage.py makemigrations --check --dry-run` - passed; no changes detected
- [x] Admin Portal login logo render check - passed in earlier validation for the prior branding asset; current text-logo replacement is covered by the focused Admin login official text-logo assertion above.
- [x] Admin Portal login copy render check - passed: rendered `/admin-portal/login/` with HTTP 200 and confirmed the authorized-user line plus MIS Department credential reminder are present.
- [x] Admin Portal login NPC seal render check - passed: rendered `/admin-portal/login/` with HTTP 200 and confirmed the right-side `seal-panel`, `col-lg-3` column, and `logos/ncba_npc_seal.png` are present.
- [x] Faculty Portal public homepage render check - passed: rendered `/faculty/` with HTTP 200 and confirmed the final CTA section is absent, the navbar shows `NCBA | TeacherMate+` and `Experience`, and `fp-npc-seal-nav`, `fp-npc-seal-section`, full-width `fp-final-npc-seal`, and `logos/ncba_npc_seal.png` are present; navbar seal is capped at 75px high with natural width and section seal at 150px wide.
- [x] Faculty Portal public-login, password-recovery link, and password-recovery logo focused tests - passed: `python manage.py test apps.faculty_portal.tests_public_login`
- [x] Faculty Portal public-login plus account login-lockout regression tests - passed: `python manage.py test apps.faculty_portal.tests_public_login apps.accounts.tests_login_lockout`
- [x] Privacy Consent focused tests - passed: `python manage.py test apps.accounts.tests_privacy_consent`
- [x] Syllabus focused tests - passed:
  `python manage.py test apps.admin_portal.tests_department_dropdown_labels.DepartmentDropdownLabelTests.test_course_form_saves_syllabus_link apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_my_courses_shows_syllabus_icon_only_when_course_has_link apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_syllabus_redirect_requires_assigned_faculty_and_matching_tenant apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_syllabus_redirect_blocks_course_tenant_mismatch`
- [x] Syllabus audit focused test - passed: `python manage.py test apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_syllabus_redirect_requires_assigned_faculty_and_matching_tenant`
- [x] Faculty active-period focused tests - passed: `python manage.py test apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_dashboard_active_grading_period_shows_ay_and_campus_name apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_offering_periods_highlights_active_grading_period`
- [x] Faculty active-period H4 scope layout focused test - passed: `python manage.py test apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_dashboard_active_grading_period_shows_ay_and_campus_name`
- [x] Faculty deadline reminder and dashboard card color focused test - passed through `python manage.py test apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_dashboard_active_grading_period_shows_ay_and_campus_name`
- [x] Faculty Dashboard guide-tag focused test - passed through `python manage.py test apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_dashboard_active_grading_period_shows_ay_and_campus_name`
- [x] Faculty My Classes guide-tag focused tests - passed: `python manage.py test apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_my_courses_lists_pending_assignments_before_acceptance apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_my_courses_labels_accepted_assignments_with_campus_name apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_my_courses_shows_final_clearance_action`
- [x] Faculty Summary table focused tests - passed: `python manage.py test apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_period_summary_shows_official_period_grade_by_default_without_release_restriction apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_period_summary_hides_active_status_but_shows_non_active_status apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_final_period_summary_shows_prior_period_grade_columns_and_final_grade`
- [x] Reopen notification focused test - passed: `python manage.py test apps.admin_portal.tests_reopen_requests.GradeSubmissionReopenRequestReviewTests.test_reopen_request_email_uses_effective_review_permission_recipients`
- [x] Role-permissions page focused tests - passed: `python manage.py test apps.admin_portal.tests_roles.RoleManagementTests.test_role_permissions_page_shows_plain_language_module_descriptions apps.admin_portal.tests_roles.RoleManagementTests.test_role_permissions_page_shows_section_save_buttons apps.admin_portal.tests_roles.RoleManagementTests.test_role_permissions_section_save_updates_only_selected_module`
- [x] Critical role-permission safeguard visibility/validation focused tests - passed: `python manage.py test apps.admin_portal.tests_roles.RoleManagementTests.test_role_permissions_page_shows_plain_language_module_descriptions apps.admin_portal.tests_roles.RoleManagementTests.test_critical_role_permission_change_requires_reason_and_confirmation apps.admin_portal.tests_roles.RoleManagementTests.test_role_permissions_section_save_updates_only_selected_module`
- [x] Admin Guide render check - passed: rendered `admin_guide_view` for `/admin-portal/guide/` with HTTP 200 and confirmed `Admin Portal User Guide` in the response body.
- [x] Active Grading Period focused tests - passed: `python manage.py test apps.academics.tests_active_grading_period.ActiveGradingPeriodServiceTests.test_seed_standard_periods_reactivates_existing_inactive_rows apps.admin_portal.tests_period_lock_form.GradingPeriodLockFormTests.test_active_grading_period_page_lists_inactive_catalog_rows_for_reactivation`
- [x] Admin Guide Active Grading Period instruction render check - passed: rendered `/admin-portal/guide/` and confirmed the per-campus setup section and catalog-once wording are present.
- [x] Admin Guide grading-control relationship render check - passed: rendered `/admin-portal/guide/` and confirmed the relationship map plus period-lock wording are present.
- [x] Faculty period-card reopen request focused test - passed: `python manage.py test apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_period_card_shows_reopen_request_action_when_auto_closed_after_deadline`
- [x] Deadline/reopen governance focused tests - passed: `python manage.py test apps.grading.tests.CompletionGraceWindowTests apps.admin_portal.tests_reopen_requests.GradeSubmissionReopenRequestReviewTests apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_period_card_shows_reopen_request_action_when_auto_closed_after_deadline apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_period_summary_hides_active_status_but_shows_non_active_status`
- [x] Admin Dashboard reopen request focused test - passed: `python manage.py test apps.admin_portal.tests_reopen_requests.GradeSubmissionReopenRequestReviewTests.test_dashboard_shows_pending_reopen_requests_in_scope`
- [x] Approved reopen Faculty Portal focused tests - passed: `python manage.py test apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_approved_reopen_request_overrides_locked_period_on_faculty_card apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_period_card_shows_reopen_request_action_when_auto_closed_after_deadline`
- [x] Approved reopen 24-hour expiry focused tests - passed: `python manage.py test apps.admin_portal.tests_reopen_requests.GradeSubmissionReopenRequestReviewTests.test_auto_close_policy_blocks_encoding_until_reopen_request_is_approved apps.admin_portal.tests_reopen_requests.GradeSubmissionReopenRequestReviewTests.test_approved_reopen_request_expires_after_24_hours_and_auto_locks apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_approved_reopen_request_overrides_locked_period_on_faculty_card`
- [x] Active approved reopen submission focused test - passed: `python manage.py test apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_period_submit_succeeds_during_active_approved_reopen_window apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_approved_reopen_request_overrides_locked_period_on_faculty_card apps.admin_portal.tests_reopen_requests.GradeSubmissionReopenRequestReviewTests.test_approved_reopen_request_expires_after_24_hours_and_auto_locks`
- [x] Submitted-gradebook reopen route tests - passed: `python manage.py test apps.admin_portal.tests_reopen_requests.GradeSubmissionReopenRequestReviewTests.test_submitted_after_deadline_uses_correction_not_reopen_request apps.admin_portal.tests_reopen_requests.GradeSubmissionReopenRequestReviewTests.test_submitted_before_deadline_can_still_use_reopen_request apps.admin_portal.tests_reopen_requests.GradeSubmissionReopenRequestReviewTests.test_auto_close_policy_blocks_encoding_until_reopen_request_is_approved`
- [x] Strict reopen submission governance tests - passed: `python manage.py test apps.admin_portal.tests_reopen_requests.GradeSubmissionReopenRequestReviewTests.test_locked_period_submission_requires_active_approved_reopen_request apps.admin_portal.tests_reopen_requests.GradeSubmissionReopenRequestReviewTests.test_newer_active_reopen_request_overrides_older_expired_request apps.admin_portal.tests_reopen_requests.GradeSubmissionReopenRequestReviewTests.test_approved_reopen_request_expires_after_24_hours_and_auto_locks apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_period_submit_succeeds_during_active_approved_reopen_window`
- [x] Reopen/deadline governance regression tests - passed: `python manage.py test apps.admin_portal.tests_reopen_requests.GradeSubmissionReopenRequestReviewTests apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_approved_reopen_request_overrides_locked_period_on_faculty_card apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_period_card_shows_reopen_request_action_when_auto_closed_after_deadline apps.grading.tests.CompletionGraceWindowTests`
- [x] Email notification focused tests - passed: `python manage.py test apps.notifications.tests.FacultyReminderServiceTests.test_queue_and_process_faculty_reminder_email apps.core.tests_email_assets apps.accounts.tests_login_otp apps.accounts.tests_admin_password_reset`
- [x] Grading email focused tests - passed: `python manage.py test apps.grading.tests.CorrectionWorkflowTests.test_correction_submission_notification_emails_configured_roles apps.grading.tests.CorrectionWorkflowTests.test_registrar_official_report_email_sends_pdf_attachment`
- [x] Simultaneous-login focused tests - passed: `python manage.py test apps.accounts.tests_login_lockout.LoginLockoutTests.test_single_device_session_enforcement_signs_out_previous_browser_by_default apps.accounts.tests_login_lockout.LoginLockoutTests.test_single_device_session_enforcement_can_be_disabled_per_tenant`
- [x] Configurable Features Login Security focused tests - passed: `python manage.py test apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_configurable_features_shows_single_device_login_setting apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_configurable_features_can_store_assignment_workflow_settings`
- [x] Faculty Portal class-card/summary focused tests - passed: `python manage.py test apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_my_courses_lists_pending_assignments_before_acceptance apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_period_card_shows_reopen_request_action_when_auto_closed_after_deadline apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_period_summary_hides_active_status_but_shows_non_active_status`
- [x] Faculty Grade Prediction / At-Risk Monitor focused tests - passed: `python manage.py test apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_faculty_can_open_at_risk_monitor_when_prediction_is_enabled apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_period_prediction_page_uses_teacher_friendly_period_specific_labels`
- [x] Faculty Grade Prediction wording/guide focused tests - passed: `python manage.py test apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_prediction_guide_uses_simple_sample_and_column_labels apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_period_prediction_page_uses_teacher_friendly_period_specific_labels`
- [x] Faculty Dashboard simplification focused tests - passed: `python manage.py test apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_dashboard_surfaces_incomplete_student_kpi apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_dashboard_priority_actions_shows_zero_state_without_at_risk_container apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_dashboard_priority_actions_appear_only_when_relevant apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_dashboard_at_risk_priority_action_uses_only_active_scope_students`
- [x] Faculty grading-template calculator removal focused tests - passed: `python manage.py test apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_faculty_grading_template_page_hides_grade_calculator_button apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_faculty_can_open_read_only_grading_template_view_after_acceptance`
- [x] Faculty Final Clearance print-gating focused tests - passed: `python manage.py test apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_my_courses_shows_final_clearance_action apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_my_courses_blocks_final_clearance_print_when_courses_incomplete apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_offering_periods_shows_final_clearance_action_on_final_period apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_offering_periods_hides_final_clearance_print_when_courses_incomplete apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_faculty_final_clearance_page_is_available_from_final_period apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_faculty_final_clearance_post_generates_pdf_report apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_faculty_final_clearance_post_blocks_pdf_when_courses_incomplete`
- [x] Faculty Student Intervention Monitor focused tests - passed: `python manage.py test apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_faculty_can_open_at_risk_monitor_when_prediction_is_enabled apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_dashboard_at_risk_priority_action_uses_only_active_scope_students apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_dashboard_priority_actions_shows_zero_state_without_at_risk_container apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_dashboard_priority_actions_appear_only_when_relevant`
- [x] Faculty Student Intervention Monitor refinement tests - passed through `python manage.py test apps.faculty_portal.tests_assignment_acceptance`, including missing-work priority, soft wording, banned default-monitor terms, status-label mapping, audit logging, and prediction-page coverage.
- [x] Required Django check - passed: `python manage.py check`
- [x] Required Faculty Portal assignment test suite - passed: `python manage.py test apps.faculty_portal.tests_assignment_acceptance` (95 tests)
- [x] Reopen/faculty combined regression suite - passed: `python manage.py test apps.admin_portal.tests_reopen_requests apps.faculty_portal.tests_assignment_acceptance` (104 tests)
- [x] Admin Student Enrollment Query focused tests - passed: `python manage.py test apps.admin_portal.tests_student_enrollment_query` (2 tests)
- [x] Admin sidebar navigation data check - passed: confirmed `STUDENT_ENROLLMENT_QUERY` is assigned to the `ENROLLMENT` menu group after migration.
- [x] Faculty activity-score Enter-key guard focused test - passed: `python manage.py test apps.faculty_portal.tests_assignment_acceptance.FacultyAssignmentAcceptanceTests.test_activity_scores_shows_quick_jump_links_and_unsaved_warning_copy`
- [x] Default monitor banned-term scan - passed: `rg -n "below passing|failing|likely to fail|possible final grade below passing|prediction confidence|coverage percentage|projected final grade|class ranking" templates\faculty_portal\student_at_risk_monitor.html templates\faculty_portal\dashboard.html apps\faculty_portal\services.py apps\faculty_portal\views.py` returned no matches.
- [x] Admin Guide Grade Prediction enablement render check - passed: rendered `/admin-portal/guide/` with HTTP 200 and confirmed the Grade Prediction / At-Risk Monitor setup checklist is present.
- [x] Email template render check - passed: rendered 9 touched HTML email templates and confirmed the green/yellow header pattern and no `Data Privacy Notice` text in those email templates.
- [x] Admin Guide submission-route render check - passed: rendered `/admin-portal/guide/` and confirmed the submission route matrix and clean distinction wording are present.
- [ ] Admin Portal browser smoke test - attempted for login NPC seal but not completed because the in-app Browser target `iab` was unavailable.
- [ ] Faculty Portal browser smoke test - not run
- [x] Permissions/RBAC checked - syllabus redirect requires `faculty_portal.access` and an active assignment in the existing faculty assignment queryset
- [x] Tenant/campus scope checked - syllabus redirect blocks course/offering tenant mismatch and non-assigned faculty

## 2026-06-13 Area Chair / College Dean Monitoring Scope

### Completed
- Added supervision-based grading monitoring:
  - Area Chair -> active faculty role assignment in the exact selected campus/department -> active accepted faculty teaching assignment.
  - Campus and department scope stay paired; access to Department A in Campus 1 and Department B in Campus 2 does not create accidental cross-campus combinations.
  - Grading Analytics no longer depends on the Course Offering department matching the Area Chair department.
  - Grading Analytics defaults to the campus selected in the Admin Portal top bar.
- Added explicit `All Campuses` filtering to Grading Analytics and Grade Distribution Monitor while preserving exact campus/department pairs.
- Added `COLLEGE_DEAN` as an active system role with a read-only monitoring baseline.
- College Dean action permissions are not automatic. Superadmin must explicitly grant assignment maintenance, approval, correction, reopen-review, hotfix-review, or submission-revert permissions when policy requires them.
- College Dean faculty monitoring starts from active Area Chair assignments within the Dean's assigned campuses/departments.
- Grade submissions, correction requests, reopen requests, overdue reporting, Grading Analytics, and Grade Distribution Monitor now use supervised accepted assignments.
- Master-data and setup pages keep their existing ownership scope: Courses, Programs, Sections, Course Offerings, Course Template Assignments, Course Base Overrides, and Period Locks.
- Corrected Faculty Assignment management so pending, clarification, declined, expired, and accepted loads remain visible to authorized assignment administrators.
- Added clearer empty states for analytics and grade-governance queues.
- Updated the Admin Practical Guide and Grading Analytics page with plain-language supervision guidance.
- Reviewed local Program data:
  - `BSIS` is assigned to `INFOSYS` for `NCBA-01` and `NCBA-02`; this is correct.
  - Program changes do not cascade to existing Section or Course Offering department fields.
  - I-AM Guro's accepted A132 assignment is at `NCBA-02`; its existing offering remains `COLLEGE` and its program is `BSA`, but it is now visible to the `NCBA-02 / INFOSYS` Area Chair through the accepted faculty assignment.
  - Cubao and Taytay remain empty when no accepted supervised assignment exists in those campus scopes.

### Changed Files
- `apps/admin_portal/services.py`
- `apps/admin_portal/views.py`
- `apps/admin_portal/help_guide.py`
- `apps/admin_portal/tests_scope.py`
- `apps/admin_portal/tests_grading_analytics.py`
- `apps/admin_portal/tests_grade_distribution_monitor.py`
- `apps/admin_portal/tests_reopen_requests.py`
- `apps/admin_portal/tests_assignment_acceptance.py`
- `apps/admin_portal/tests_tenant_grading_profiles.py`
- `apps/core/management/commands/seed_stage_0_1.py`
- `apps/core/services/features.py`
- `apps/rbac/migrations/0013_seed_college_dean_role.py`
- `apps/rbac/migrations/0014_college_dean_read_only_baseline.py`
- `templates/admin_portal/grading/analytics.html`
- `templates/admin_portal/grading/grade_distribution_monitor.html`
- `templates/admin_portal/grading/submission_list.html`
- `templates/admin_portal/grading/correction_request_list.html`
- `templates/admin_portal/grading/reopen_request_list.html`
- `CHANGE_LOG.md`
- `TEACHERMATEPLUS_CONTEXT.md`
- `HANDOFF.md`

### Validation
- [x] `python manage.py migrate` - applied `rbac.0013_seed_college_dean_role` and `rbac.0014_college_dean_read_only_baseline`.
- [x] `python manage.py check` - no issues.
- [x] `python manage.py makemigrations --check --dry-run` - no changes detected.
- [x] Python compile check for changed Python modules.
- [x] Monitoring/scope/analytics/grade-distribution focused suite - 46 tests passed.
- [x] Supervisory grading regression suite - 204 tests passed.
- [x] Admin assignment/configuration/non-compliance regression suite - 24 tests passed.
- [x] Full project suite - 517 tests passed.
- [x] Live data check confirmed the `ac` role assignments: `NCBA-01 / INFOSYS`, `NCBA-02 / INFOSYS`, and `NCBA-03 / CS`.
- [x] Live data check confirmed the seeded `COLLEGE_DEAN` role has 19 read/monitor permissions and none of the eight restricted governance action permissions.
- [ ] Authenticated Admin Portal browser smoke test - attempted on June 13, 2026, but the in-app browser target was unavailable. Complete the manual checks below before production rollout.

### Known Limits / Operational Notes
- `COLLEGE_DEAN` does not infer campuses or departments automatically. Superadmin must create the Dean's campus/department role assignments.
- A Dean sees a department's faculty through an active Area Chair assignment. A department with no active Area Chair is intentionally absent from the Dean monitoring chain.
- There is no separate Dean-to-Area-Chair relationship record. The current chain is inferred from active role assignments with matching campus and department scope.
- `All Campuses` is currently exposed on Grading Analytics and Grade Distribution Monitor. Other grade-governance queues use the Admin Portal top-bar campus, so multi-campus reviewers switch campus there.
- Program updates help future classification and filtering but do not rewrite existing Section or Course Offering rows.
- Course Template Assignment and Course Base Overrides are setup pages, not faculty-monitoring pages; they may legitimately be blank until matching setup records exist.
- Only active, accepted faculty assignments are included in Grading Analytics. Pending, expired, rejected, and inactive assignments are excluded.

### Manual Test Steps
1. Log in as `ac`.
2. Select `NCBA-02` in the top bar and open `Grading -> Grading Analytics`.
3. Confirm I-AM Guro's accepted A132 class appears even though the existing offering department is `COLLEGE`.
4. Select `NCBA-01`; confirm only accepted Cubao assignments appear. Current local data has no accepted Cubao assignment for I-AM Guro, so an empty result is expected.
5. Select `NCBA-03`; confirm only CS-supervised accepted assignments appear.
6. As Superadmin, assign `COLLEGE_DEAN` to a test Dean for the intended campus and parent/department scope.
7. Confirm the Dean sees faculty only through active Area Chairs in that assigned scope.
8. Confirm a department without an active Area Chair does not appear in the Dean's monitoring results.
9. Open Grading Analytics and Grade Distribution Monitor, select `All Campuses`, and confirm each campus shows only the departments assigned there.
10. Open Grade Submissions, Correction Requests, Reopen Requests, and the overdue report; confirm they show only accepted classes handled by supervised faculty.
11. Confirm pending or declined assignments remain visible on Faculty Assignment management but do not appear in grading monitoring.
12. Log in as a baseline College Dean and confirm monitoring pages are available while approval/mutation actions are absent.
13. Confirm shared Courses remain `All Campus / All Department` and are not duplicated.
14. Confirm Course Offerings, Course Template Assignments, Course Base Overrides, and Period Locks retain their normal master-data/setup scope.

## 2026-06-13 Grade Distribution Monitor Simplification

### Completed
- Removed the `Classes in Scope`, `Rows Reviewed`, `For Review`, `Incomplete Data`, `High Grade Concentration`, and `High Perfect Score Rate` cards.
- Removed the `Spread`, `Comparison`, and `Status` columns from the page and CSV export.
- Removed the now-unnecessary review-threshold explanation panel from the Area Chair page.
- Fixed the Grading Period dropdown to use periods from templates resolved for monitored classes, together with periods already referenced by stored grades or activities.
- Preserved the masked-student detail modal, distribution percentages, tenant/campus/department supervision scope, and read-only behavior.

### Changed Files
- `apps/admin_portal/grade_distribution.py`
- `apps/admin_portal/views.py`
- `apps/admin_portal/tests_grade_distribution_monitor.py`
- `templates/admin_portal/grading/grade_distribution_monitor.html`
- `CHANGE_LOG.md`
- `TEACHERMATEPLUS_CONTEXT.md`
- `HANDOFF.md`

### Validation
- [x] `python manage.py test apps.admin_portal.tests_grade_distribution_monitor` - 11 tests passed.
- [x] `python manage.py test` - 518 tests passed.
- [x] `python manage.py check` - no issues.
- [x] `python manage.py makemigrations --check --dry-run` - no model changes detected.
- [x] `git diff --check` for the changed monitor files - passed; only existing LF/CRLF conversion warnings were reported.
- [ ] Manual authenticated browser check - confirm the simplified columns and populated Grading Period dropdown with a production-like Area Chair account.

### Manual Test Steps
1. Log in as an Area Chair with supervised accepted faculty assignments.
2. Open `Grading -> Grade Distribution Monitor`.
3. Confirm the six removed summary cards do not appear.
4. Confirm `Spread`, `Comparison`, and `Status` are absent from the results table.
5. Open the Grading Period dropdown and confirm the periods used by supervised classes are listed.
6. Select one period and apply the filters; confirm only matching distribution rows remain.
7. Export CSV and confirm it does not contain Spread, Department/Subject Comparison, or Flags columns.
8. Open a Period / Level link and confirm the masked-student details modal still works.

## 2026-06-14 Enrollment Adjustment Tool

### Completed
- Implemented Admin Portal `Enrollment -> Enrollment Adjustments`.
- Added `EnrollmentAdjustmentLog` for per-student adjustment audit records.
- Added `EnrollmentAdjustmentService` for impact analysis, classification, and processing.
- Added permissions:
  - `enrollment_adjustment.view`
  - `enrollment_adjustment.process`
- Added routes:
  - `admin_portal:enrollment_adjustments`
  - `admin_portal:enrollment_adjustment_detail`
- Added templates:
  - `templates/admin_portal/enrollment/enrollment_adjustments.html`
  - `templates/admin_portal/enrollment/enrollment_adjustment_detail.html`
- Added migrations:
  - `enrollment.0004_enrollmentadjustmentlog`
  - `enrollment.0005_enrollmentadjustmentlog_audit_state`
  - `rbac.0017_seed_enrollment_adjustment_permissions`
  - `rbac.0018_narrow_enrollment_adjustment_process_roles`
  - `navigation.0008_seed_enrollment_adjustment_menu`
- Added focused tests in `apps/admin_portal/tests_enrollment_adjustments.py`.
- Post-review hardening:
  - campus-level period locks now block adjustments
  - unsubmitted final-grade records now classify as `WARNING`
  - default processing rights are limited to Superadmin, Tenant Admin, Campus Admin, and Registrar
  - Area Chair, Dean, College Dean, and CAO are view-only by default unless explicitly granted process permission
  - each processing action now gets a batch reference shared by all per-student logs
  - logs now store source/destination enrollment IDs and before/after enrollment active/status state
  - page now includes `Load Students / Refresh Student List`, `Select All`, and impact-count scope notes

### Behavior
- The tool moves one student, multiple selected students, or all active students from a source offering to a destination offering.
- The tool does not decide if the enrollment correction is academically valid. It assumes Pinnacle/SIS or authorized school offices already approved the correction.
- Processing changes only enrollment rows:
  - destination enrollment is created
  - source enrollment is marked inactive
  - source gradebook/attendance/submission/correction/reopen/lock records are preserved
- Destination offering is not forced to the same campus as the source offering, but it remains limited to the logged-in admin's allowed scope.

### Classification Rules
- `SAFE`: no academic records found.
- `WARNING`: academic records exist, such as attendance, activities, scores, submissions, period grades, final grades that are not submitted, correction requests, or reopen requests. Processing requires explicit confirmation.
- `BLOCKED`: source and destination are the same, destination enrollment already exists, final grade is submitted, the source offering has a locked grading period, or a matching campus-level grading period lock is active.

### Changed Files
- `apps/enrollment/models.py`
- `apps/enrollment/services.py`
- `apps/enrollment/admin.py`
- `apps/enrollment/migrations/0004_enrollmentadjustmentlog.py`
- `apps/enrollment/migrations/0005_enrollmentadjustmentlog_audit_state.py`
- `apps/admin_portal/forms.py`
- `apps/admin_portal/views.py`
- `apps/admin_portal/urls.py`
- `apps/admin_portal/tests_enrollment_adjustments.py`
- `apps/core/management/commands/seed_stage_0_1.py`
- `apps/rbac/migrations/0017_seed_enrollment_adjustment_permissions.py`
- `apps/rbac/migrations/0018_narrow_enrollment_adjustment_process_roles.py`
- `apps/navigation/migrations/0008_seed_enrollment_adjustment_menu.py`
- `templates/admin_portal/enrollment/enrollment_adjustments.html`
- `templates/admin_portal/enrollment/enrollment_adjustment_detail.html`
- `CHANGE_LOG.md`
- `TEACHERMATEPLUS_CONTEXT.md`
- `HANDOFF.md`

### Validation
- [x] `python manage.py migrate` via full local Python path - applied enrollment, RBAC, and navigation migrations.
- [x] `python manage.py check` via full local Python path - no issues.
- [x] `python manage.py migrate --check` via full local Python path - no pending migrations.
- [x] `python manage.py test apps.admin_portal.tests_enrollment_adjustments` via full local Python path - 11 tests passed.
- [x] `python manage.py test apps.admin_portal.tests_post_enrollment_safety` via full local Python path - 8 tests passed.
- [ ] Manual authenticated browser smoke test - still needed.

### Manual Test Steps
1. Log in as an authorized Admin Portal user with `enrollment_adjustment.view` and `enrollment_adjustment.process`.
2. Open `Enrollment -> Enrollment Adjustments`.
3. Select Academic Year, Term, Campus, Source Offering, and Destination Offering.
4. Select one active student and click `Analyze Impact`.
5. Confirm the impact table shows attendance, activities, scores, submissions, period grades, final grades, correction requests, reopen requests, and locks.
6. Process a `SAFE` row and confirm the destination enrollment is active while the source enrollment becomes inactive.
7. Add an activity or score in a source class, analyze again, and confirm the row is `WARNING`.
8. Confirm a warning row is not processed unless the warning confirmation checkbox is checked.
9. Create a destination enrollment for the same student and confirm the adjustment is `BLOCKED`.
10. Create a submitted final grade or locked source course-offering period and confirm the adjustment is `BLOCKED`.
11. Create a campus-level locked grading period for the source campus/term and confirm the adjustment is `BLOCKED`.
12. Create a non-submitted final-grade record and confirm the adjustment is `WARNING`, not `SAFE`.
13. Use `Transfer Entire Class` and confirm only eligible students move while blocked students remain in the source.
14. Open `Adjustment History`, then `Details`, and confirm the audit snapshot, enrollment state audit, and batch reference are visible.
15. Log in with view-only permission and confirm processing is blocked.
16. Attempt direct URL access without view permission and confirm the standard permission-denied response.

### Known Limits / Operational Notes
- Cross-campus or cross-program movement creates the destination enrollment under the destination offering scope but does not rewrite the student's master campus/department/program.
- Academic validity still belongs to Pinnacle/SIS and authorized school offices. TeacherMate+ only audits and protects academic records.
- Gradebook records are not migrated or mapped to the destination offering. Historical verification remains a faculty/admin responsibility for warning transfers.

## Exact Next Steps For Next Codex Session
1. Read `AGENTS.md`, `TEACHERMATEPLUS_CONTEXT.md`, `CHANGE_LOG.md`, and this file.
2. Run the 16-step Enrollment Adjustment Tool manual browser checklist above with a production-like admin account.
3. Run the 15-step Student Consultation browser checklist above at desktop and mobile widths, including the new Period Grade exact-value fallback.
4. Verify a production-like faculty account can see only accepted assigned classes and cannot open another faculty member's performance URL.
5. Run the manual multi-campus Area Chair and College Dean checks above with production-like role assignments.
6. Review Phase 1 faculty feedback before considering caching, Chart.js, broader academic-head views, or comparisons across faculty.

## Files To Inspect First Next Session
- AGENTS.md
- TEACHERMATEPLUS_CONTEXT.md
- CHANGE_LOG.md
- HANDOFF.md
- apps/grading/models.py
- apps/grading/services.py
- apps/predictions/services.py
- apps/admin_portal/forms.py
- apps/admin_portal/views.py
- templates/admin_portal/grading/subcomponent_table.html
- templates/admin_portal/grading/template_builder.html

## Do Not Forget
- Respect tenant/campus scope.
- Enforce RBAC server-side and UI-side.
- Avoid broad rewrites.
- Preserve grading governance and auditability.
- Update CHANGE_LOG.md and TEACHERMATEPLUS_CONTEXT.md when behavior changes.
- Run validation before handoff.
- Run `python manage.py check`.
- Run `python manage.py migrate` if migrations exist.
- Smoke-test impacted Admin and Faculty flows.

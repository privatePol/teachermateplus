# HANDOFF.md

Last updated by Codex: 2026-08-19

## Purpose
This file preserves continuity between Codex sessions for TeacherMate+ V1.

## Current Session Summary
### Faculty Personalized Answer Sheets
- Date/gate/baseline: 2026-08-19; implementation plus focused validation in `D:\teachermateplus-monday-faculty-print`, branch `feat/faculty-questionnaire-print-release`, unchanged HEAD `a55df329efb13ca19c8b1f70c9f17bcec178d9b5`. The gate began clean and exact. No commit, push, deployment, restart, or normal-database migration application was authorized or performed.
- Completed: added immutable `PersonalizedAnswerSheetAssignment` history keyed by exact `ExamGenerationRevision` plus `Enrollment`, with protected offering/actor FKs, A/B and initial/late method constraints, algorithm version, opaque future QR UUID, unique revision/enrollment identity, and a revision/offering/Set index. Explicit CSRF-protected POST preparation reuses the exact active `QuestionnairePrintRelease`, owned contribution, retained current source/live accepted assignment, tenant/campus/feature/direct-DENY, release window, course/offering, and complete 50-75 item Set authorization inside one lock-ordered transaction. Initial active rosters receive deterministic HMAC-ranked alternating A/B assignments; repeated preparation is idempotent, late enrollments enter the smaller active Set with a deterministic tie break, and inactive/reactivated enrollment history is retained without reshuffling.
- Faculty output: the responsive section overview reports Active Students, Set A, Set B, and Missing only for authorized offerings; GET never creates rows and print remains blocked until every active/current student is assigned. Private/no-store Print All/A/B uses stable student ordering and one NCBA-branded Letter/A4/Legal page per student with prominent persisted Set, Student Number/Name, course/title/section, blank Date, exam-period mark, unmarked Campus/Program, empty A-D bubbles through exact N, and bubble-free UNUSED rows through 75. It loads no question/answer content, renders no QR/public UUID, and writes content-safe preparation/render audits. Course cards now retain primary Open workspace and group Questionnaire, Personalized Answer Sheets, separately released Answer Keys, and Checking Masters under wrapping Exam Outputs.
- Changed files: `CHANGE_LOG.md`; `HANDOFF.md`; `TEACHERMATEPLUS_CONTEXT.md`; `apps/departmental_exams/contribution_authorization.py`; `apps/departmental_exams/faculty_views.py`; `apps/departmental_exams/models.py`; new `apps/departmental_exams/migrations/0020_personalized_answer_sheet_assignment.py`; new `apps/departmental_exams/personalized_answer_sheets.py`; new `apps/departmental_exams/tests_personalized_answer_sheets.py`; `apps/departmental_exams/tests_answer_key_release.py`; `apps/departmental_exams/tests_questionnaire_print_release.py`; `apps/departmental_exams/urls.py`; `apps/faculty_portal/help_guide.py`; `apps/faculty_portal/tests_help_guide.py`; `templates/departmental_exams/faculty/contribution_list.html`; and new personalized overview/print templates.
- Validation: Personalized Answer Sheets passed 15 tests in 66.750s with 1 expected SQLite skip for the backend row-lock capability case; Questionnaire Print Release compatibility passed 28/28 in 144.054s; Answer Key/Checking Master compatibility passed 25/25 in 130.067s; Resources answer-sheet regression passed 9/9 in 45.632s; Faculty help guide passed 5/5 in 7.399s. Total: 82 tests, 81 passed, 1 skipped, 0 failed. `git diff --check` exited zero with line-ending notices only; `python -B manage.py check` reported zero issues; `python -B manage.py makemigrations --check --dry-run` reported `No changes detected`; read-only `python -B manage.py migrate --plan --no-color` exited zero with 808 lines and listed `departmental_exams.0020` / `Create model PersonalizedAnswerSheetAssignment`. Diffs for `generation_algorithms.py`, `csv_import.py`, `apps/grading`, and the generic `answer_sheet.html` are empty. No existing migration changed.
- Pending/risks/next step: authenticated browser/physical print preview for 1/40/75-page Letter/A4/Legal batches, actual MariaDB concurrent preparation/row locking, and the full repository suite remain unperformed. SQLite proved persistence, idempotency, constraints, privacy, rendering, and regressions but is not claimed as MariaDB locking evidence. Next perform independent read-only security/UI review, MariaDB concurrency validation, and browser/physical print smoke before any separately authorized staging or commit gate. Migration `0020` remains unapplied to the normal development database.

### Faculty Pre-Shaded Checking Master
- Date/gate/baseline: 2026-08-18; implementation plus focused validation in `D:\teachermateplus-monday-faculty-print`, branch `feat/faculty-questionnaire-print-release`, unchanged HEAD `439ddf4731a3b81cd1d9e43e6e8cc1fa1646fd11`. The gate began clean and exact. No commit, push, deployment, restart, or normal-database migration application is authorized.
- Completed: added GET-only Faculty Set A/B Pre-Shaded Checking Masters under the existing `AnswerKeyRelease`. Direct rendering reuses the exact contribution owner, tenant/campus/feature/direct-DENY, retained current accepted assignment, release status/window, current-final revision, exact course/revision/Set, and complete persisted-set authorization. A stricter master boundary requires `GeneratedExamSet.item_count`, `ExamGenerationRevision.final_item_count_snapshot`, and actual contiguous positions to agree, accepts only normalized A-D `correct_answer_snapshot` values, and denies counts above 75. The dedicated dynamic private/no-store template supports Letter default, A4, and Legal; it shades exactly one bubble per active item and marks every trailing position through 75 UNUSED without question text, Pair Code, OMR/scoring, or a media artifact. `DE_FACULTY_CHECKING_MASTER_PRINTED` records identifiers/window context only.
- Faculty UI: Question Contributions retains View Set A/B Answer Key, removes the redundant course-card Print Set A/B Answer Key buttons, and adds Set A/B Checking Master print actions only under the existing authorized Answer Key release. The existing Answer Key View pages retain their Print controls, and the unrelated Questionnaire Print Set A/B buttons remain unchanged. No permission, release model, attestation, navigation item, model, or migration was added.
- Changed files: `CHANGE_LOG.md`; `HANDOFF.md`; `TEACHERMATEPLUS_CONTEXT.md`; `apps/departmental_exams/answer_key_release.py`; `apps/departmental_exams/faculty_views.py`; `apps/departmental_exams/tests_answer_key_release.py`; `apps/departmental_exams/urls.py`; `apps/faculty_portal/help_guide.py`; `apps/faculty_portal/tests_help_guide.py`; `templates/departmental_exams/faculty/contribution_list.html`; and new `templates/departmental_exams/faculty/checking_master_print.html`.
- Validation: after correcting one test-fixture assumption about normalized campus scope, the definitive final-state Answer Key/Checking Master module passed 25/25 in 134.560s with zero failures/skips. Questionnaire Print Release compatibility passed 28/28 in 143.464s, proving the existing Questionnaire Print Set A/B workflow remains intact, and Faculty help-guide tests passed 5/5 in 7.022s. `git diff --check` exited zero with line-ending notices only; `python -B manage.py check` reported zero issues; `python -B manage.py makemigrations --check --dry-run` reported `No changes detected`; and read-only `python -B manage.py migrate --plan --no-color` exited zero with 806 existing planned lines and applied nothing. Diffs for `apps/departmental_exams/generation_algorithms.py` and `templates/departmental_exams/faculty/answer_sheet.html` are empty. No migration is required.
- Pending/risks/next step: authenticated browser and physical print-preview checks on Letter/A4/Legal, grayscale bubble legibility, MariaDB, and the full repository suite remain unperformed. Next perform an independent read-only authorization/UI review and browser/physical print smoke before any separately authorized staging or commit gate.

### Departmental Exam Planning & Readiness
- Date/gate/baseline: 2026-08-18; implementation plus focused validation in `D:\teachermateplus-monday-faculty-print`, branch `feat/faculty-questionnaire-print-release`, unchanged HEAD `7964f27979e12121d13d2b0689dc377af94405d5`. The gate began clean and exact. No commit, push, deployment, restart, or normal-database migration application is authorized.
- Completed: added the read-only Admin `Planning & Readiness` page immediately after Assigned Course Examinations, its standalone printer-friendly view, seven authorized-data-derived filters, grouped Exam Department/course/faculty-section output, and course/department/overall readiness totals. The report starts from one exact authorized active/open offering base, groups only on `Course.exam_department`, retains globally scoped unassigned courses, keeps no-faculty offerings operationally visible, counts distinct active enrollments once per offering, and separates assignment acceptance, active FACULTY-role coverage, and usable-password account activation.
- RBAC/confidentiality: new `departmental_exams.view_planning_readiness` and `departmental_exams.print_planning_readiness` permissions are seeded without role/user grants. The menu references View only; print requires both permissions in an overlapping exact scope. Authorization uses the current active tenant, exact active campuses, same permission-bearing UserRole department, exact direct ALLOW, and exact direct DENY precedence. NULL tenant/campus permission scopes, cross-role department composition, and parent/child expansion are rejected. All filters and totals derive after authorization, and print-only/direct-forged access receives no report context.
- Independent-review remediation: generated print links now preserve the exact submitted values for only the seven recognized filters. Invalid, forged, unauthorized, or malformed screen filters therefore reach the same report builder and remain empty/fail-closed in print; arbitrary parameters are excluded. `navigation.0024` reverse now resolves the exact Admin Departmental Exam Builder group before finding the Planning item, so a same-code item in another group is neither hijacked on forward nor altered/deleted on reverse, and unrelated permission links remain intact.
- Changed files: `CHANGE_LOG.md`; `HANDOFF.md`; `TEACHERMATEPLUS_CONTEXT.md`; `apps/admin_portal/help_guide.py`; `apps/admin_portal/tests_help_guide.py`; `apps/core/context_processors.py`; `templates/admin_portal/guide.html`; `apps/departmental_exams/urls.py`; new `planning_readiness.py`, `planning_readiness_views.py`, `tests_planning_readiness.py`, Admin report/print templates, `apps/rbac/migrations/0036_seed_planning_readiness_permissions.py`, and `apps/navigation/migrations/0024_seed_planning_readiness_menu.py`.
- Validation: after remediation, the full Planning & Readiness module passed 23/23 in 35.754s, including valid/invalid screen-print parity, hidden-count safety, both-permission enforcement, normal migration reversal, wrong-group collision, and unrelated-link preservation; the affected Admin guide previously passed 19/19 in 16.656s; and existing menu-performance plus seeded-RBAC/navigation repair regressions passed again 9/9 in 1.462s. `git diff --check` exited zero with line-ending notices only, `python -B manage.py check` reported zero issues, `makemigrations --check --dry-run` reported no model changes, and read-only `migrate --plan --no-color` exited zero with 806 lines including only the new `rbac.0036` and `navigation.0024` Planning migrations; it also lists the normal database's existing unapplied Departmental Exam chain. The generation-algorithm diff is empty. An exploratory legacy `tests_migration_safety` batch passed 2/4 but retained two stale exact-set assumptions that already omit pre-existing `0033`-`0035` permissions and post-`0018` menu links; those unrelated assertions were not rewritten in this gate.
- Pending/risks/next step: authenticated browser/physical print preview, MariaDB, and the full repository suite remain unperformed. Next perform an independent read-only authorization/UI review and browser print smoke before any separately authorized staging/commit gate. No normal-database migration was applied.

### Faculty Resources Answer Sheet CAO Layout Revision
- Date/gate/baseline: 2026-08-18; focused implementation and validation only in `D:\teachermateplus-monday-faculty-print`, branch `feat/faculty-questionnaire-print-release`, unchanged HEAD `51a4925ec0912d3ee3b6101187f44e5d05fda4f0`. The worktree was clean at the gate. `D:\teachermateplus` was not modified. No commit, push, deployment, restart, or normal-database migration application occurred.
- Completed: revised only the built-in Faculty Resources Answer Sheet boundary to match the approved CAO layout. It retains the NCBA logo/school heading, exact three 25-row columns and A-D choices, while adding compact campus/period/program checkbox rows without printed group labels; Stud Number plus Date, Student Name, and Course/Subject lines with Year removed; larger 0.19-inch bubbles; a 75-mark light NCBA watermark pattern; clear vertical column separators; and a compact footer retaining Reminders, `EDUCATING GLOBALLY COMPETITIVE FILIPINOS`, Set, Revision, and Pair Code.
- Paper sizing/security: the existing shared paper helper supplies only allowlisted Letter, A4, and Legal labels, CSS sizes, widths, and heights to the Answer Sheet. `letter` remains the default; missing or invalid `paper` values fall back to Letter, and arbitrary input is never reflected into CSS. Resources navigation and the existing Faculty builder authorization gate remain unchanged. Answer Key Release, questionnaire print/release, scientific notation, CSV import, generation, RBAC, models, and migrations were not changed.
- Changed files: `CHANGE_LOG.md`; `HANDOFF.md`; `TEACHERMATEPLUS_CONTEXT.md`; `apps/departmental_exams/faculty_views.py`; `apps/departmental_exams/tests_resources.py`; `apps/faculty_portal/help_guide.py`; `apps/faculty_portal/tests_help_guide.py`; `templates/departmental_exams/faculty/answer_sheet.html`; and `templates/departmental_exams/faculty/resources.html`.
- Validation: required Resources tests passed 9/9 in 31.497s and the directly affected Faculty guide tests passed 5/5 in 4.396s, all with zero failures/skips. `git diff --check` exited zero with line-ending notices only; `python -B manage.py check` reported zero issues; and `python -B manage.py makemigrations --check --dry-run` reported `No changes detected`. Protected generation, CSV, Answer Key Release, and model diffs were empty. Logs were redirected outside the worktree. No migration is required.
- Pending/risks/next step: the configured in-app browser runtime reported no available browser backend, so authenticated screen rendering, browser print preview, and physical Letter/A4/Legal one-page output remain unverified. Perform that visual/physical smoke and an independent focused review before any separately authorized staging or commit gate.

### Faculty Answer Key Release
- Date/gate/baseline: 2026-08-18; implementation and focused validation only in `D:\teachermateplus-monday-faculty-print`, branch `feat/faculty-questionnaire-print-release`, unchanged HEAD `a2413727cb5d41a26646ddd4d848c65b4da50dc7`. Scope was limited to the approved Faculty Answer Key Release feature. No commit, push, normal-database migration application, deployment, or restart occurred.
- Completed: added immutable, deletion-prohibited `AnswerKeyRelease` history bound to an exact `CycleCourse` and exact `ExamGenerationRevision`, with bounded availability, attestation version, ACTIVE/REVOKED lifecycle, revocation evidence, and one-active-release enforcement. The existing Questionnaire Print Release page now has a separate, non-bulk Faculty Answer Key Release section with exact-revision selection, required all-sessions-concluded confirmation, Release/Replace/Revoke controls, current-window status, and history.
- Faculty behavior: active, tenant-valid faculty with a current exact course assignment can access scalar-only Set A/B view and print outputs only while the release is active, within its window, and still bound to the current finalized revision. Before/after the window, after revocation, on assignment loss, under direct DENY, or after revision supersession, buttons disappear and direct URLs fail closed. Releasing R4 never releases R5; once R5 becomes current, R4 access is immediately blocked while its history remains intact. Answer Keys were not added to Resources.
- RBAC/audit/confidentiality: added dedicated assignable permission `departmental_exams.release_answer_keys`; generation-management authority is not an alternate and no role or user receives an automatic grant. Safe audits use `DE_ANSWER_KEY_RELEASED`, `DE_ANSWER_KEY_RELEASE_REVOKED`, `DE_FACULTY_ANSWER_KEY_SET_VIEWED`, and `DE_FACULTY_ANSWER_KEY_SET_PRINTED` with IDs/window/set/timestamps only. Faculty rendering receives allowlisted scalars rather than revision/item model objects, responses are private/no-store/no-cache, and answer values are absent from audit metadata.
- Changed files: `CHANGE_LOG.md`; `HANDOFF.md`; `TEACHERMATEPLUS_CONTEXT.md`; Admin and Faculty guide sources/tests plus `templates/admin_portal/guide.html`; `apps/departmental_exams/models.py`, `services.py`, `forms.py`, `stage6_views.py`, `faculty_views.py`, `urls.py`; new `answer_key_release.py`, `tests_answer_key_release.py`, and Faculty Answer Key view/print templates; the existing Admin release and Faculty contribution templates; and migrations `departmental_exams.0019_answer_key_release`, `rbac.0035_seed_answer_key_release_permission`, and `navigation.0023_add_answer_key_release_permission`.
- Validation: the focused Answer Key lifecycle, form, authorization/direct-DENY, tenant/course/revision, timing, revoke/replace, assignment, Set A/B content, headers/context, audit, supersession, Resources, RBAC migration, and real schema forward/reverse suite passed 12/12 in 235.211s. A separate Admin/Faculty guide run reached 22/23 before exposing assertion-only wording/visibility expectations; after aligning the guide text and role-aware fixture/assertions, the affected Admin regression passed 1/1 in 5.052s. `git diff --check` exited zero with line-ending notices only; `python -B manage.py check` reported zero issues; `python -B manage.py makemigrations --check --dry-run` reported `No changes detected`; and `python -B manage.py migrate --plan --no-color` exited zero with 802 plan lines including all three new migrations, without applying them. `git diff -- apps/departmental_exams/generation_algorithms.py` is empty. A wider compatibility collection including questionnaire release, generation reporting, and both guide modules exceeded its 1,204-second bound without terminal results and is not credited as passing.
- Pending/risks/next step: authenticated browser UI/print smoke, MariaDB behavior, the full repository suite, and a completed wider questionnaire/guide compatibility run remain unverified. Next perform an independent read-only security/UI review and the remaining focused browser/compatibility checks before any separately authorized staging or commit gate.

### Manual Scientific Notation Phase 1
- Date/gate: 2026-08-17; implementation and focused validation only in `D:\teachermateplus-monday-faculty-print`, branch `feat/faculty-questionnaire-print-release`, unchanged HEAD `bbbf4cd91432082616168503a9676369c7c28750`.
- Completed: Manual Add/Edit Question now equips Question Text and Choices A-D only with Insert Equation, Chemical Formula, a compact fraction/root/script/symbol/Greek/matrix palette, and escaped live previews. Ordinary text remains ordinary text. Version-pinned official KaTeX `0.18.4` is self-hosted with CSS, core JS, auto-render, mhchem, 20 required WOFF2 fonts, package metadata, and MIT license; no CDN or build pipeline is used. Rendering accepts only `\(...\)` and `\[...\]`, sets `trust: false`, `throwOnError: false`, strict error behavior, `maxSize: 10`, `maxExpand: 1000`, and `htmlAndMathml`. Preview input enters through `textContent`; application paths contain no `innerHTML`, `|safe`, or `mark_safe` faculty-input path.
- Downstream/print: the Faculty contribution workspace and delete confirmation, Blueprint Review, Generated Revision Detail, Question Selection Audit screen/print, and shared Faculty/Admin Set A/B questionnaire print render the same escaped Question Text/Choices. Math-field `linebreaksbr` use was replaced with `white-space: pre-wrap`. Questionnaire Letter/A4/Legal behavior is preserved; print buttons wait until synchronous initial rendering and `document.fonts.ready` where supported. Answer Keys and Automatic Audit were not changed.
- Changed implementation paths: new `templates/departmental_exams/_scientific_notation_assets.html`, `static/js/departmental_exam_scientific_notation.js`, `static/css/departmental_exam_scientific_notation.css`, and `static/vendor/katex/0.18.4/` (core CSS/JS, auto-render, mhchem, package metadata, license, and 20 WOFF2 fonts); modified the eight authorized Departmental Exam templates for question form, workspace, delete, Blueprint Review, Generated Revision Detail, Selection Audit screen/print, and questionnaire print. Focused regressions changed `tests_stage5_contributions.py`, `tests_stage5_views.py`, `tests_stage6_generation.py`, `tests_generation_reporting.py`, `tests_questionnaire_print_release.py`, plus new `tests_scientific_notation.py`. Required behavior documentation changed `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, Faculty/Admin help sources/tests, `templates/admin_portal/guide.html`, and this handoff.
- Protected boundaries: `apps/departmental_exams/generation_algorithms.py` has an empty diff. Explicit diff inspection across `csv_import.py`, `contribution_forms.py`, `faculty_views.py`, `departmental_exam_csv_import.js`, `csv_upload.html`, and `csv_preview.html` is empty. No model/schema/migration, CSV format/template/import, correct-answer/difficulty, figure/image, generation selection/fingerprint/dedupe/solver/digest, source identity, Answer Key, or Automatic Audit production path changed.
- Validation: final successful batches were 22/22 focused static/Stage 5 persistence/view cases, 11/11 scientific snapshot plus reporting cases, 28/28 questionnaire-print release cases, 23/23 full Stage 5 Faculty-view cases, 19/19 generation-service/view/Blueprint Review cases, and 23/23 Faculty/Admin guide cases, all with zero skips. The first questionnaire run exposed four assertion-only expectations for new escaped/attributed markup; after correction, the full final rerun passed 28/28. A direct execution probe against the vendored KaTeX 0.18.4 runtime successfully produced HTML+MathML for fractions, roots, scripts, Greek, sums, integrals, mhchem chemistry, and matrices; malformed input returned a `katex-error`, and an untrusted `javascript:` href did not become active. `git diff --check` exited zero with line-ending notices only; `python -B manage.py check` reported zero issues; `python -B manage.py makemigrations --check --dry-run` reported `No changes detected`; and `python -B manage.py migrate --plan --no-color` exited zero and listed the configured database's existing full unapplied migration chain without applying it. Feature-specific migration result: none required. Logs were redirected outside the worktree.
- Pending/risks/next step: the in-app browser workflow reported no available browser backend, so authenticated visual/live-preview interaction, browser print-preview, and physical Letter/A4/Legal output remain untested; a temporary local validation server on port 8765 was started only for that attempt and stopped immediately. The full repository suite and MariaDB were not run. Next perform a focused independent read-only security/UI review and a browser-enabled rendering/print smoke. No commit, push, deployment, normal-database migration application, or existing application/server restart occurred; all remain separately authorized gates.

### Faculty Answer Sheet Print-Chrome Remediation
- Date/gate: 2026-08-17; remediation and focused validation only in `D:\teachermateplus-monday-faculty-print`, branch `feat/faculty-questionnaire-print-release`, unchanged HEAD `30bfb52273ac3cf3d1511acb5cfd78c2cff3c05f`.
- Completed: the Answer Sheet print-only hide rule now removes the conditional Faculty identity-warning banner and the shared inline-message, system-error-modal, and direct-body modal-backdrop containers that can appear above or overlay the sheet. Resources navigation, authorization/RBAC, answer-sheet fields, exact 1-25/26-50/51-75 ranges, A-D bubbles, and Letter/A4 sizing behavior are unchanged.
- Files changed by this remediation: `templates/departmental_exams/faculty/answer_sheet.html`, `apps/departmental_exams/tests_resources.py`, and this required `HANDOFF.md` update. Focused regressions now assert the print-hide rule, exact rendered 1-75 sequence and three range partitions, A-D-only options, NCBA logo, and divider.
- Validation: the requested Resources and Faculty Guide suite passed 12/12 in 110.474s with 0 failures and 0 skips. `git diff --check` exited zero with line-ending notices only; `python -B manage.py check` reported 0 issues; and `python -B manage.py makemigrations --check --dry-run` reported `No changes detected`. Logs were redirected outside the worktree and test migrations were confined to the disposable in-memory database; no normal database migration was applied.
- Pending/risks/next step: authenticated browser print preview and physical Letter/A4 output remain untested, as do the full project suite and MariaDB. Perform a focused independent read-only re-review before any separately authorized commit. Do not commit, push, deploy, restart, or apply normal migrations without separate authorization.

### Faculty Departmental Exam Builder Resources Answer Sheet Worktree Correction
- Date/gate: 2026-08-17; clean target `D:\teachermateplus-monday-faculty-print`; branch `feat/faculty-questionnaire-print-release`; unchanged HEAD `30bfb52273ac3cf3d1511acb5cfd78c2cff3c05f`. The dirty source `D:\teachermateplus` was inspected read-only at required HEAD `73ba82af35e1ef001f8cd1d14e1064e3cb7bd8e8`; none of its files was modified, discarded, staged, reset, stashed, cleaned, or otherwise reconciled.
- Completed: reproduced only the Faculty Departmental Exam Builder Resources feature in the target. Added GET-only Resources and answer-sheet routes; a code-provided `Resources` item beside the existing seeded `Question Contributions` item; a Resources landing card; and a built-in NCBA 75-Item Answer Sheet. Direct routes reuse the existing feature flag, active Faculty Portal permission, tenant/campus scope, and owned-or-eligible contribution/source boundary. Target-specific questionnaire-print, resumable-CSV, generation, audit, answer-key, release, and navigation behavior remains intact.
- Answer-sheet contract: official `media/logos/ncba-logo.png`; `NATIONAL COLLEGE OF BUSINESS AND ARTS`; ruled Campus, Period, Date, Student Number, Name of Student, Course, and Year fields; exactly three 25-row columns covering 1-25, 26-50, and 51-75; A-D headers only; exactly 300 empty circular bubbles; visible browser Print control; and Letter-portrait print CSS that hides Faculty Portal chrome and uses width-safe sizing for A4.
- Changed target paths: `CHANGE_LOG.md`, `HANDOFF.md`, `TEACHERMATEPLUS_CONTEXT.md`, `apps/core/context_processors.py`, `apps/departmental_exams/faculty_views.py`, `apps/departmental_exams/urls.py`, new `apps/departmental_exams/tests_resources.py`, `apps/faculty_portal/help_guide.py`, `apps/faculty_portal/tests_help_guide.py`, and new `templates/departmental_exams/faculty/resources.html` plus `answer_sheet.html`. No Submission Readiness, model, migration, log, secret, environment, dependency, questionnaire-printing, reporting, generation, answer-key, audit, release, or grading file changed.
- Validation: combined Resources, Faculty Guide, and four existing Stage 5 navigation/access regressions passed 15/15 in 186.601s with 0 failures and 0 skips. `git diff --check` exited zero with LF-to-CRLF notices only; `python -B manage.py check` reported 0 issues; and `python -B manage.py makemigrations --check --dry-run` reported `No changes detected`. Django logs were redirected to `C:\Users\Lenovo\AppData\Local\Temp\codex-answer-sheet-correction-20260817`; test migrations were confined to the disposable in-memory database and no normal database migration was applied.
- Pending/risks/next step: authenticated browser print preview and physical Letter/A4 output were not performed; final printer-driver scaling remains unverified. No full project suite or MariaDB-specific run was performed. Next perform a focused independent review and browser print-preview check before any separately authorized staging/commit gate. Do not commit, push, deploy, restart, or apply normal migrations without separate authorization.

### Stage 5 question-content character security remediation
- Date/baseline/scope: 2026-08-16; isolated worktree `D:\teachermateplus-monday-faculty-print`, branch `feat/faculty-questionnaire-print-release`, unchanged HEAD `a15b21aa10a41ac68f367be809e6b568ebdb3f7a`. Scope was limited to shared Stage 5 question-content character validation, focused security regressions, and required documentation. No HTML escaping redesign, print/generation/audit behavior, private-media work, unrelated refactor, staging, commit, push, deployment, restart, or normal-database migration application occurred.
- Completed character policy: `QuestionPayloadService.validate()` preserves ordinary Unicode, spaces, punctuation, and supported line breaks; CR/CRLF normalize to LF. LF is the only allowed C0 control. NUL, all other C0 controls including tab, C1 controls, DEL, surrogate code points, embedded BOM, `U+061C`, `U+200E/U+200F`, `U+202A-U+202E`, and `U+2066-U+2069` fail with one generic field error that does not echo content. No rejected character is silently stripped or accepted. CSV rows rejected for these characters remain invalid and omit their staged display payload. HTML/script-shaped and URI-scheme-shaped text remains literal stored plain text protected by unchanged Django autoescaping.
- Security coverage: focused tests prove NUL/C0/C1/DEL/BOM/bidi/surrogate rejection, newline normalization, legitimate Filipino/English Unicode preservation, literal HTML-shaped CSV persistence, escaped preview and confirmed workspace rendering, non-clickable `javascript:`/`data:`/`vbscript:`/external URLs, escaped generated revision/selection audit/Faculty and Admin questionnaire print output, and import JavaScript `textContent` use with no `innerHTML` or `insertAdjacentHTML`.
- Files changed by this gate: `CHANGE_LOG.md`; `HANDOFF.md`; `TEACHERMATEPLUS_CONTEXT.md`; `apps/departmental_exams/contribution_services.py`; `apps/departmental_exams/csv_import.py`; `apps/departmental_exams/tests_stage5_csv.py`; `apps/departmental_exams/tests_stage5_views.py`; `apps/departmental_exams/tests_stage5_csv_resume.py`; `apps/departmental_exams/tests_questionnaire_print_release.py`; and `apps/faculty_portal/help_guide.py`. No model, migration, template, generation algorithm, printing service, reporting service, or audit production file changed.
- Validation completed with Django logs outside the worktree: the final added security selection passed 8/8 in 31.900s; the complete Stage 5 CSV, Faculty views, resumable CSV, and questionnaire print-release modules passed 96/96 in 375.479s; and Faculty help-guide tests passed 5/5 in 4.440s. `git diff --check` exited zero with line-ending notices only, `python -B manage.py check` reported zero issues, and `python -B manage.py makemigrations --check --dry-run` reported `No changes detected`. `git diff -- apps/departmental_exams/generation_algorithms.py` is empty. Disposable test databases applied the existing migration chain; no migration was applied to the normal database.
- Pending/risks/next step: authenticated browser rendering, real MariaDB character persistence, and the full project suite were not run. Perform a focused independent read-only review of this exact remediation before any separately authorized commit. Commit, push, deployment, restart, and normal-database migration remain separate gates.

### Stage 5 resumable CSV independent-review remediation
- Date/baseline/scope: 2026-08-16; isolated worktree `D:\teachermateplus-monday-faculty-print`, branch `feat/faculty-questionnaire-print-release`, unchanged HEAD `a15b21aa10a41ac68f367be809e6b568ebdb3f7a`. Scope was limited to the three independent-review blockers: permanent import locks, migration `0018` reversal, and forged-hash mutation. No generation, printing, or audit implementation changed; no commit, push, deployment, restart, or normal-database migration application occurred.
- Completed: `PAUSED` is now active/resumable only for transient processing interruption. Stale contribution revision, incompatible quota, authorization/assignment loss, and invalid persisted state atomically become terminal non-active `FAILED`; their partial imported `Question` rows and confidential staged rows are deleted, the active-contribution lock and persisted cursor are cleared, and a safe message directs Faculty to review the workspace and start a fresh preview. Terminal-cause replay preserves safe `403`/`409`/`400` semantics without reopening the batch. Worker/runtime interruption retains committed progress and resumes idempotently. `Http404` is re-raised before failure recording, so forged hash, owner, or tenant requests leave status, progress, timestamps, lock, failure metadata, staged rows, and questions unchanged.
- Migration reverse: the reverse code for `0018_resumable_question_csv_import` runs before the old constraints are restored. It maps `IMPORTING`, `PAUSED`, and `FAILED` to pre-0018 `EXPIRED`, deletes partial imported questions and staged rows with quoted parameterized SQL, clears new progress/failure fields, sets purge time, and never marks an incomplete batch confirmed. Rollback intentionally loses resumable progress and confidential preview payload for incomplete batches. Existing `READY`, `INVALID`, `CONFIRMED`, and `EXPIRED` statuses retain their existing meaning; confirmed progress backfill is database-alias aware.
- Files changed by this remediation: `CHANGE_LOG.md`; `HANDOFF.md`; `TEACHERMATEPLUS_CONTEXT.md`; `apps/departmental_exams/csv_import.py`; `apps/departmental_exams/migrations/0018_resumable_question_csv_import.py`; `apps/departmental_exams/models.py`; `apps/departmental_exams/tests_stage5_csv.py`; `apps/departmental_exams/tests_stage5_csv_resume.py`; `apps/departmental_exams/tests_stage5_views.py`; and `apps/faculty_portal/help_guide.py`.
- Validation completed: the final combined resumable-import and affected Faculty-help rerun passed 29/29 in 85.316s (24 import regressions plus 5 guide tests); the real `0018 -> 0017` reverse-migration regression passed 1/1 in 48.788s; and existing Stage 5 CSV/view compatibility passed 38/38 in 142.481s. The previously failing quota-replay case passed separately 1/1 in 4.401s before the full 38/38 rerun. Final `git diff --check`, `python -B manage.py check`, `python -B manage.py makemigrations --check --dry-run`, and read-only `python -B manage.py migrate --plan` exited zero; the 796-line configured normal-database plan reports its full unapplied chain and includes `departmental_exams.0018_resumable_question_csv_import`. Test databases alone applied/reversed migrations. The solver-file diff is empty.
- Pending/risks/next step: MariaDB migration reversal/locking and authenticated browser recovery remain untested, and the full project suite was not run. Perform a focused independent read-only re-review of these three remediations before any separately authorized commit or normal-database migration.

### Stage 5 Faculty CSV resumable import closure
- Date/baseline/scope: 2026-08-16; isolated worktree `D:\teachermateplus-monday-faculty-print`, branch `feat/faculty-questionnaire-print-release`, baseline HEAD `a15b21aa10a41ac68f367be809e6b568ebdb3f7a`. Scope was Stage 5 Faculty question CSV progress, page locking, durable recovery, migration, focused tests, and required documentation only. No generation algorithm, Automatic Generation, Answer Key, Question Selection Audit, Automatic Audit, questionnaire-printing, or navigation/menu file changed. No commit, push, deployment, restart, or normal-database migration application occurred.
- Completed: upload uses measurable XHR byte progress followed by an honest indeterminate `Validating CSV...` phase. Confirmation is driven through short database-atomic chunks with persisted `committed_rows / total_rows` and percentage, a blocking reminder, disabled page controls, double-submit prevention, advisory `beforeunload`, and safe control restoration. Owner-scoped status/resume endpoints and workspace discovery recover an interrupted active batch after network/gateway response loss, refresh, tab close, or worker restart.
- Durable integrity: `QuestionImportBatch` now records `IMPORTING`, `PAUSED`, and `FAILED`, a portable one-to-one active-contribution lock, committed count/cursor, start/progress timestamps, and content-safe failure code/message. New CSV questions persist `import_row_number`; database uniqueness on `(import_batch, import_row_number)` prevents replay duplication. Every chunk re-locks and revalidates owner, tenant/campus, RBAC, current assignment, lifecycle, contribution revision, and quota. Partial rows are excluded from finalized Faculty/Admin counts and ordinary mutation controls; all other Stage 5 question mutations fail closed while a batch is active. Final completion increments/audits once and purges staged confidential rows. Duplicate semantic content remains warning-only.
- Migration: new additive `apps/departmental_exams/migrations/0018_resumable_question_csv_import.py` adds the fields and constraints above and backfills only `committed_rows=total_rows` for existing `CONFIRMED` batch shells. Historical question content is not rewritten and historical CSV questions may retain null row identity. The migration ran only while disposable test databases were created; it was not applied to the normal local database.
- Changed files: `CHANGE_LOG.md`; `HANDOFF.md`; `TEACHERMATEPLUS_CONTEXT.md`; `apps/departmental_exams/contribution_authorization.py`; `contribution_selectors.py`; `contribution_services.py`; `csv_import.py`; `faculty_views.py`; `migrations/0018_resumable_question_csv_import.py`; `models.py`; `tests_stage5_csv.py`; new `tests_stage5_csv_resume.py`; `tests_stage5_views.py`; `urls.py`; `apps/faculty_portal/help_guide.py`; new `static/js/departmental_exam_csv_import.js`; and the Stage 5 Faculty `contribution_workspace.html`, `csv_preview.html`, and `csv_upload.html` templates.
- Validation completed: the final combined required command passed 61/61 tests in 224.098s across `tests_stage5_csv`, `tests_stage5_views`, and new `tests_stage5_csv_resume`, after the active-import and replay-identity constraints were made MariaDB-portable. The changed Faculty help guide passed 5/5 in 4.755s. `python -B manage.py check` reported zero issues and `makemigrations --check --dry-run` reported no changes. `migrate --plan` exited zero and listed the full currently unapplied chain for the configured normal database, including `departmental_exams.0018`; no operation was applied. `git diff --check` passed with line-ending warnings only, and the generation-algorithm diff was empty. Two earlier CSV-module runs exposed ordering/message and intentional READY-to-PAUSED expectation differences; both were corrected before the final 61/61 run.
- Pending/risks/next step: authenticated browser testing remains for measured upload progress, overlay/link blocking, unload warning, refresh recovery, and mobile accessibility. MariaDB migration/locking/concurrent-resume rehearsal and real proxy/worker interruption testing remain unperformed. The full project suite was not run. Next perform an independent read-only review of this exact Stage 5 diff; commit/push and normal-database migration remain separately authorized gates.

### Legal questionnaire paper-size support
- Date/baseline/scope: 2026-08-16; isolated worktree `D:\teachermateplus-monday-faculty-print`, branch `feat/faculty-questionnaire-print-release`, baseline HEAD `3065fb1bc2eefaab93e11db59c7ff89f7e312f00`. This focused implementation only extends the existing shared questionnaire paper-size allowlist and already-enumerated help/test text. No commit, push, deployment, restart, or normal-database migration application occurred.
- Completed: added allowlisted `paper=legal` support with CSS size `Legal`, portrait width `8.5in`, and height `14in`. Letter remains the default, A4 is unchanged, and missing/invalid values still fall back to Letter without reflection. The shared template was not modified, so Legal inherits the existing margins, normal-flow footer safety, and page-break behavior for Faculty Set A/B and Admin Direct Print Set A/B. Authorization, exact-revision selection, confidentiality, no-store headers, release lifecycle, generation, audit, and answer-key logic are unchanged.
- Changed files: `CHANGE_LOG.md`; `HANDOFF.md`; `TEACHERMATEPLUS_CONTEXT.md`; `apps/admin_portal/help_guide.py`; `apps/admin_portal/tests_help_guide.py`; `apps/departmental_exams/questionnaire_printing.py`; `apps/departmental_exams/tests_questionnaire_print_release.py`; `apps/faculty_portal/help_guide.py`; `apps/faculty_portal/tests_help_guide.py`; and `templates/admin_portal/guide.html`.
- Validation completed: the required questionnaire print-release module passed 26/26 in 169.056s, including Letter/A4/Legal across both portals and both sets, default/fallback behavior, unchanged audit metadata, authorization, and confidentiality; affected Admin/Faculty guide modules passed 23/23 in 19.624s. `git diff --check`, `python -B manage.py check`, and `python -B manage.py makemigrations --check --dry-run` exited clean with no model changes. The generation-algorithm diff is empty. Disposable test databases applied migrations; the normal local database was not migrated.
- Pending/risks/next step: authenticated browser/physical Legal print preview was not run. Run independent read-only review next; commit/push require separate authorization.

### Questionnaire print paper sizing and overlap-safe layout
- Date/baseline/scope: 2026-08-16; isolated worktree `D:\teachermateplus-monday-faculty-print`, branch `feat/faculty-questionnaire-print-release`, baseline HEAD `09cfbdd53de2634231931d2df653e671045d501d`. This focused implementation changes only the shared Faculty/Admin Set A/B questionnaire presentation, its request wiring/tests, and required documentation. No commit, push, deployment, restart, or normal-database migration application occurred.
- Completed: compacted the uppercase institution name to a 14pt/1.05 non-wrapping line; replaced the fixed confidentiality footer with a normal-flow footer; increased the print bottom margin; added break-safe question, question-line, choice, and footer rules; and added an allowlisted `paper` control supporting Letter by default and A4. Missing or unknown paper values fall back to Letter and are never reflected into CSS. Faculty and Admin outputs continue through their existing authorization, exact-revision, sanitized-context, no-store, and content-safe audit paths; paper size is not added to audit metadata.
- Changed files: `CHANGE_LOG.md`; `HANDOFF.md`; `TEACHERMATEPLUS_CONTEXT.md`; `apps/admin_portal/help_guide.py`; `apps/admin_portal/tests_help_guide.py`; `apps/departmental_exams/faculty_views.py`; `apps/departmental_exams/questionnaire_printing.py`; `apps/departmental_exams/reporting_views.py`; `apps/departmental_exams/tests_questionnaire_print_release.py`; `apps/faculty_portal/help_guide.py`; `apps/faculty_portal/tests_help_guide.py`; `templates/admin_portal/guide.html`; and `templates/departmental_exams/faculty/questionnaire_print.html`.
- Validation completed: new paper/layout cases passed 3/3 in 50.086s; the required full questionnaire print-release module passed 26/26 in 455.698s; Admin/Faculty guide modules passed 23/23 in 58.987s; `git diff --check`, `python -B manage.py check`, and `python -B manage.py makemigrations --check --dry-run` exited clean with no model changes. The generation-algorithm diff is empty. Disposable test databases applied migrations; the normal local database was not migrated.
- Pending/risks/next step: the in-app browser had no available backend, so authenticated browser print preview, physical Letter/A4 output, and screenshot/PDF visual comparison were not run. The fixed overlay mechanism is removed and rendered HTML/CSS contracts are covered, but a later browser-enabled gate should visually inspect long-question Letter and A4 page breaks before publication. Run final independent read-only review next; commit/push require separate authorization.

### Bulk questionnaire print release and operational-guide completion
- Date/baseline/scope: 2026-08-16; isolated worktree `D:\teachermateplus-monday-faculty-print`, branch `feat/faculty-questionnaire-print-release`, unchanged HEAD `02b44d08cd01b09cac407193889929cb2cc3a0ca`. The ten pre-existing approved Question Selection Audit numbering, Automatic Generation Summary menu/selector, campus-display deduplication, tests, and navigation `0022` paths were preserved. This gate authorized implementation and focused validation only; the index remains empty and no commit, push, deployment, restart, or normal-database migration application occurred.
- Completed: added an authorized bulk Questionnaire Print Release workflow with revision-specific selections, one common availability window, selected-count feedback, and explicit all-or-nothing behavior. The transaction reuses the existing exact-course/revision release service for tenant, campus, RBAC, direct-DENY, Set A/B completeness, locking, history-preserving replacement, and safe audit enforcement; malformed selections, duplicate revisions for one course, invalid windows, unauthorized items, or cross-tenant items roll back the entire operation. Regeneration does not silently move an existing release, and no bulk revoke was introduced. Admin and Faculty operational guides now cover exact releases, bulk semantics, Admin Direct Print, Answer Keys, Question Selection Audit columns/metrics/equivalence labels, Automatic audit PASS/WARNING/FAIL meaning, active windows, and faculty confidentiality boundaries.
- Files changed in the combined preserved worktree: `CHANGE_LOG.md`; `HANDOFF.md`; `TEACHERMATEPLUS_CONTEXT.md`; `apps/admin_portal/help_guide.py`; `apps/admin_portal/tests_help_guide.py`; `apps/departmental_exams/forms.py`; `apps/departmental_exams/questionnaire_printing.py`; `apps/departmental_exams/stage6_views.py`; `apps/departmental_exams/tests_generation_reporting.py`; `apps/departmental_exams/tests_questionnaire_print_release.py`; `apps/departmental_exams/tests_automatic_summary_navigation.py`; `apps/departmental_exams/urls.py`; `apps/faculty_portal/help_guide.py`; `apps/faculty_portal/tests_help_guide.py`; `apps/navigation/migrations/0022_seed_automatic_generation_summary_menu.py`; `templates/admin_portal/guide.html`; `templates/departmental_exams/admin/automatic_generation_summary_selector.html`; `templates/departmental_exams/admin/generation_selection_audit.html`; `templates/departmental_exams/admin/generation_selection_audit_print.html`; and `templates/departmental_exams/admin/questionnaire_print_release.html`.
- Validation completed: 46/46 focused tests passed with disposable test databases: bulk release 9/9 in 189.160s, Admin/Faculty guides 23/23 in 101.869s, and existing direct-print/release, campus deduplication, Automatic Summary navigation, and report-numbering compatibility 14/14 in 650.843s. `python -B manage.py check` reported zero issues; `makemigrations --check --dry-run` reported `No changes detected`; `migrate --plan --no-color` against `DB_NAME=:memory:` exited zero and included navigation `0022` without applying it; and `git diff --check` exited zero with line-ending notices only. The solver-file diff is empty.
- Migration status/risks/next step: bulk release requires no schema migration. Navigation `0022` belongs to the preserved Automatic Summary menu work. The full suite, MariaDB transaction behavior, and signed-in browser workflows remain untested. Run independent read-only review next; only a separate gate may authorize selective staging or commit.

### Admin direct questionnaire print and deterministic Automatic audit
- Date/baseline/scope: 2026-08-16; isolated worktree `D:\teachermateplus-monday-faculty-print`, branch `feat/faculty-questionnaire-print-release`, unchanged HEAD `59a1714f268a14b3a485f356fedcf7b35797529c`. The approved 18-path uncommitted Question Selection Audit/Answer Key implementation was preserved. This gate permits implementation and focused validation only; no commit, push, deployment, restart, or normal-database migration application occurred.
- Completed: the Admin Questionnaire Print Release page now lists every accessible exact revision with direct Set A/B questionnaire print actions and deterministic audit controls. Admin print requires current scoped `print_generated_exams` or generation-management authority, uses the existing sanitized question/choice-only questionnaire template, is independent of Faculty release windows, creates no release row, sets private/no-store headers, and records safe metadata. `AutomaticGenerationAuditRun` persists immutable revision-bound `automatic-audit-v1` history with PASS/WARNING/FAIL findings for set counts/positions, logical uniqueness, persisted difficulty/campus targets, persisted overlap, correct-answer completeness, intrinsic digests, eligible Submitted source evidence, source counts, and selected membership. Legacy source-dependent checks warn as unavailable instead of consulting mutable current configuration or fabricating failure. Printable audit results expose no raw fingerprint/HMAC or question/answer content.
- RBAC/migrations: added assignable `departmental_exams.audit_generated_exams`, with existing generation management accepted as alternate audit authority; print-only and audit-only Admins can reach the shared output page without receiving Faculty release controls. New additive migrations are `departmental_exams.0017_automatic_generation_audit_run`, `rbac.0034_seed_departmental_exam_audit_permission`, and `navigation.0021_add_questionnaire_output_permissions`. No historical audit results are backfilled. Temporary test databases applied the migrations; the normal local database was not migrated.
- Changed files across the preserved source-audit/answer-key work and this completion: `CHANGE_LOG.md`; `HANDOFF.md`; `TEACHERMATEPLUS_CONTEXT.md`; `apps/departmental_exams/automatic_generation_audit.py`; `generation_readiness.py`; `generation_reporting.py`; `generation_services.py`; `migrations/0016_generation_source_audit_snapshot.py`; `migrations/0017_automatic_generation_audit_run.py`; `models.py`; `questionnaire_printing.py`; `reporting_views.py`; `services.py`; `stage4_test_support.py`; `stage6_views.py`; `tests_automatic_generation_audit.py`; `tests_automatic_workflow.py`; `tests_generation_reporting.py`; `tests_questionnaire_print_release.py`; `urls.py`; `apps/navigation/migrations/0021_add_questionnaire_output_permissions.py`; `apps/rbac/migrations/0034_seed_departmental_exam_audit_permission.py`; `templates/admin_portal/guide.html`; `templates/departmental_exams/admin/automatic_generation_audit_result.html`; `automatic_generation_audit_result_print.html`; `generated_revision_detail.html`; `generation_answer_key.html`; `generation_answer_key_print.html`; `generation_selection_audit.html`; `generation_selection_audit_print.html`; and `questionnaire_print_release.html`.
- Validation completed: 62/62 distinct focused tests passed. The final affected-page rerun passed deterministic Automatic audit 7/7 in 79.105s and expanded questionnaire print/release 13/13 in 77.931s; generation reporting/answer keys passed 8/8 in 56.789s; modified Automatic RBAC/source-snapshot/direct-deny cases passed 3/3 in 29.798s; Stage 6 generation passed 14/14 in 90.472s; and Admin guide passed 17/17 in 21.233s. `python manage.py check` reported zero issues; `makemigrations --check --dry-run` reported `No changes detected`; `migrate --plan --no-color` exited zero and listed the isolated empty database's full unapplied chain, including source snapshots `0016`, audit-run model `0017`, RBAC `0034`, and navigation `0021`, without applying anything; and `git diff --check` exited zero with line-ending notices only. The solver-file diff is empty.
- Known limits/next gate: browser/physical Letter print preview, MariaDB migration/locking behavior, and the full repository suite remain untested. Applying `0016` and `0017` plus RBAC/navigation migrations in an authorized environment is required before new source audits and Automatic audit history can persist. The next gate is independent review, followed only if approved by separately authorized migration/commit/publication work.

### Question Selection Audit Report and exact-revision Answer Keys
- Date/baseline/scope: 2026-08-16; isolated worktree `D:\teachermateplus-monday-faculty-print`, branch `feat/faculty-questionnaire-print-release`, unchanged HEAD `59a1714f268a14b3a485f356fedcf7b35797529c`. The worktree was clean at the gate. This session authorizes implementation and focused validation only; no commit, push, deployment, restart, or normal-database migration application occurred.
- Completed: added immutable `GenerationSourceAuditSnapshot` and `GenerationSourceQuestionSnapshot` models plus additive no-backfill migration `departmental_exams.0016_generation_source_audit_snapshot`. Every new successful generation transaction now snapshots the assessed Submitted source pool, eligibility/exclusion evidence, versioned logical identity, contributor/campus/context, and question payload without changing the solver input or result. Added confidential exact-revision Question Selection Audit view/print with summary counts, seven filters, exact Set A/B positions, opaque equivalence labels, legacy source-audit-unavailable behavior, and no raw fingerprint/HMAC output. Added separate exact-revision Set A/B Answer Key view/print from persisted item positions and correct-answer snapshots, Asia/Manila timestamp, CONFIDENTIAL marking, private no-store headers, tenant/direct-deny/current-versus-historical authorization, and safe audits containing no answers.
- Changed files: `CHANGE_LOG.md`; `HANDOFF.md`; `TEACHERMATEPLUS_CONTEXT.md`; `apps/departmental_exams/generation_readiness.py`; `apps/departmental_exams/generation_reporting.py`; `apps/departmental_exams/generation_services.py`; `apps/departmental_exams/migrations/0016_generation_source_audit_snapshot.py`; `apps/departmental_exams/models.py`; `apps/departmental_exams/reporting_views.py`; `apps/departmental_exams/tests_automatic_workflow.py`; `apps/departmental_exams/tests_generation_reporting.py`; `apps/departmental_exams/urls.py`; `templates/admin_portal/guide.html`; `templates/departmental_exams/admin/generated_revision_detail.html`; `templates/departmental_exams/admin/generation_answer_key.html`; `templates/departmental_exams/admin/generation_answer_key_print.html`; `templates/departmental_exams/admin/generation_selection_audit.html`; and `templates/departmental_exams/admin/generation_selection_audit_print.html`.
- Validation completed: 51/51 fresh focused tests passed. After the final behavior-preservation diff audit, the new reporting suite passed 8/8 in 69.807s; the Automatic snapshot/deduplication and direct-deny pair passed 2/2 in 26.792s; and Stage 6 generation plus questionnaire print-release regressions passed 24/24 in 155.337s. The Admin guide suite passed 17/17 in 25.305s. Temporary Django test databases applied migration `0016`; the normal local database was not migrated. `python manage.py check` reported zero issues; `makemigrations --check --dry-run` reported `No changes detected`; `migrate --plan --no-color` exited zero and listed the isolated worktree's full unapplied chain, including `0016` as creation of the two audit models, without applying it; and `git diff --check` exited zero with line-ending notices only.
- Known risks/pending work: no historical audit backfill is intentionally provided, so legacy revisions cannot prove their complete Submitted/unselected/duplicate source pool. Browser/physical print preview, MariaDB migration/locking behavior, and the full repository suite remain untested. A separately authorized deployment must apply `0016` before new generation can persist source audits. The next gate is independent review, followed only if approved by separately authorized migration/commit/publication work.

### Questionnaire print reference-spec remediation
- Baseline/scope: branch `feat/faculty-questionnaire-print-release` at unchanged HEAD `ad06bada0745382362fc47435166b2fe8e01ac4e`; all 22 pre-existing implementation paths were preserved. No governance, authorization, migration, Automatic Summary, solver, generation, cron, or reopen logic changed.
- Completed: added the gray bold/underlined course running header; placed the configured school name, existing tenant `PRINT_HEADER_SCHOOL_ADDRESS` campus/location line, term, and academic year in the institutional header; replaced the generic title with the exact exam-period / `DEPARTMENTAL EXAMINATIONS` / course title / course code / Set structure; and added the approved answer-sheet shading, `STRICTLY NO ERASURES ALLOWED`, Pencil No. 2, and divider instructions. The safe context already contained all required scalar/snapshot fields and did not change.
- Changed by this remediation: `templates/departmental_exams/faculty/questionnaire_print.html`, `apps/departmental_exams/tests_questionnaire_print_release.py`, and this handoff. Focused assertions cover every required heading/instruction/body field, both Set A and Set B, no-store, and confidential-data exclusions.
- Validation: questionnaire print/release suite `Ran 10 tests in 44.171s`, `OK`; `python manage.py check` reported 0 issues; `makemigrations --check --dry-run` reported no changes; `migrate --plan` exited 0 and listed the isolated worktree's full unapplied chain without applying it; `git diff --check` exited 0 with line-ending notices only; `git diff -- apps/departmental_exams/generation_algorithms.py` was empty. The required `git diff --stat` and `git diff` inspections showed only the pre-existing tracked implementation inventory because the two remediated implementation files remain untracked.
- Pending/risks and exact next step: browser/physical Letter print-preview remains a Staging smoke requirement, including long-choice wrapping and fixed-footer pagination. Run the requested independent reference-spec completion review next; only after approval should a separate gate authorize selective staging/commit. No commit, push, migration application, deployment, or restart occurred.

### Faculty questionnaire print release and Automatic Summary UI
- Date/baseline: 2026-08-15; isolated worktree `D:\teachermateplus-monday-faculty-print`, branch `feat/faculty-questionnaire-print-release`, unchanged HEAD `ad06bada0745382362fc47435166b2fe8e01ac4e`, created from the clean source worktree `D:\teachermateplus-automatic-generation-simplified-ui`. No commit, push, deployment, restart, or normal-database migration was authorized or performed.
- Completed: added immutable, revision-bound `QuestionnairePrintRelease` lifecycle records with one active release per cycle course; admin release/replace/revoke controls with generation-management reauthorization and audit events; current-assignment-scoped faculty Set A/Set B print links and direct-URL authorization; sanitized Letter portrait questionnaire output with no-store headers and safe audit metadata; actual persisted Set A/B campus and difficulty counts in Automatic Summary; exact waiting/deadline/Draft copy; navigation seed; documentation and focused regression coverage.
- Security boundaries: all release and print reads/writes are tenant/course/revision scoped; direct deny and multi-campus management rules fail closed; current retained faculty eligibility must intersect a currently active accepted exact teaching assignment; the released revision remains pinned when newer revisions exist; rendered print context contains snapshot question text and choices only, excluding answer keys, correct answers, difficulty, provenance, contributor/source identifiers, digest/HMAC/algorithm data, and history.
- Changed files: `CHANGE_LOG.md`; `HANDOFF.md`; `TEACHERMATEPLUS_CONTEXT.md`; `apps/admin_portal/help_guide.py`; `apps/core/context_processors.py`; `apps/departmental_exams/automatic_workflow.py`; `apps/departmental_exams/contribution_authorization.py`; `apps/departmental_exams/faculty_views.py`; `apps/departmental_exams/forms.py`; `apps/departmental_exams/migrations/0015_questionnaire_print_release.py`; `apps/departmental_exams/models.py`; `apps/departmental_exams/questionnaire_printing.py`; `apps/departmental_exams/stage6_views.py`; `apps/departmental_exams/tests_questionnaire_print_release.py`; `apps/departmental_exams/urls.py`; `apps/faculty_portal/help_guide.py`; `apps/navigation/migrations/0020_seed_questionnaire_print_release_menu.py`; `templates/admin_portal/guide.html`; `templates/departmental_exams/admin/automatic_generation_summary.html`; `templates/departmental_exams/admin/questionnaire_print_release.html`; `templates/departmental_exams/faculty/contribution_list.html`; `templates/departmental_exams/faculty/questionnaire_print.html`.
- Validation passed: `python manage.py check` (0 issues); `python manage.py makemigrations --check --dry-run` (no changes); the 10 new questionnaire-print tests plus 11 targeted Stage 5/6, permission, summary, tenant, feature, and inclusion tests (`Ran 21 tests in 508.987s`, `OK`); changed Admin/Faculty guide suites (`Ran 22 tests in 98.028s`, `OK`); `python manage.py migrate --plan` exited 0 and included `departmental_exams.0015` plus `navigation.0020`; `git diff --check`; and a no-diff guard for `apps/departmental_exams/generation_algorithms.py`.
- Known validation limitation: a supplemental 26-test help/migration-safety run had 24 passes and two failures in pre-existing `apps/departmental_exams/tests_migration_safety.py` expectations. Those source-identical tests expect the older RBAC/navigation permission sets and already omit baseline migrations `rbac.0033` and `navigation.0019`; observed extras were `view_generated_exams`, `manage_exam_generation`, and the new `print_generated_exams`. The full suite, MariaDB concurrency behavior, browser/physical Letter printing, and admin/faculty page smoke tests were not run. The isolated worktree had no populated ignored database, so `migrate --plan` listed the full initial chain; it did not apply migrations. No supplied official questionnaire image/template was present beyond the textual brief, so the print layout follows those requirements and existing project conventions.
- Pending/exact next steps: independently review tenant/RBAC/lifecycle and confidential-output guarantees; review and update the stale migration-safety expectation only under separate scope; authorize and apply migrations in an appropriate environment; run signed-in admin release/revoke and faculty A/B print smoke tests with Letter print preview; then separately authorize selective commit and publication if accepted.

### Automatic positive-overlap logical-assignment remediation
- Date/baseline: 2026-08-15; worktree `D:\teachermateplus-automatic-generation-simplified-ui`, branch `fix/automatic-generation-simplified-ui`, unchanged HEAD `8a629b1d19d6e42a1f1a2a71557fac049592b23e`; the starting worktree was clean. This implementation gate forbids commit, push, migration application, deployment, restart, Manual behavior changes, and raising the Automatic state budget as the primary fix.
- Completed: generalized the Automatic logical-group assignment fast path from zero overlap to exact positive overlap. The existing total/campus/difficulty logical-capacity lower bound supplies the first overlap candidate. For a positive candidate, the solver orders exact campus-by-difficulty table pairs by the existing proportional score, derives per-cell shared-slot bounds from logical-group capacity, and uses the existing deterministic HMAC-ranked Hungarian assignment to allocate `A`, `B`, or one concrete `BOTH` representative. A shared normalized question therefore appears once in each set and never twice inside either set. Cross-campus alternatives remain live until assignment, and multiple contributors cannot block a hard-feasible Automatic exam. The exhaustive selector remains the fallback when the optimized path cannot prove a candidate; Manual Generation is untouched.
- Real SASA-shaped regression: 200 rows form exactly 123 logical groups with size distribution `{1: 49, 2: 72, 3: 1, 4: 1}`, two cross-campus Moderate groups, 50 cross-contributor groups in the pure solver fixture, and no cross-difficulty group. Fixed logical capacities are Campus 1 `10 Difficult / 15 Easy / 24 Moderate`, Campus 2 `10 / 15 / 23`, and Campus 3 `0 / 15 / 9`, plus `Campus 1|2 Moderate` and `Campus 2|3 Moderate`. With final count 50, campus `17/17/16`, and difficulty `15/25/10`, the campus-3 capacity proves a lower bound of `2*16-25 = 7`; the optimized assignment finds exact overlap 7 without increasing it. Both 50-item sets meet every margin and contain 50 distinct logical fingerprints.
- Validation completed: the final direct SASA-shaped measurement returned `feasible=True`, `limit_hit=False`, exact overlap `7`, `312` states, and `0.300015s`; each set had 50 items, campus `17/17/16`, difficulty `15/25/10`, and 50 distinct logical fingerprints. Exact algorithm plus database-backed SASA regressions passed 2/2 in 9.025s; the full Stage 6 algorithm module passed 26/26 in 1.757s; the full Automatic workflow module passed 47/47 in 301.247s; and Stage 6 generation passed 14/14 in 82.146s. The first 47-test Automatic command reached its 244.1s shell timeout without a terminal result and is not counted; the unchanged rerun completed `Ran 47 tests ... OK`. `python manage.py check` reported zero issues, `makemigrations --check --dry-run` reported `No changes detected`, and `git diff --check` exited 0 with line-ending notices only. The read-only `migrate --plan --no-color` command listed the repository's entire existing migration chain because this isolated worktree's `db.sqlite3` is a pre-existing zero-byte file; it did not identify a new migration and applied nothing. Final diff stat is six files, `377 insertions(+), 144 deletions(-)`; the index is empty and final status contains only those six intended modified paths.
- Changed files: `apps/departmental_exams/generation_algorithms.py`, `apps/departmental_exams/tests_stage6_algorithms.py`, `apps/departmental_exams/tests_automatic_workflow.py`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, and this handoff. No model, migration, setting, template, Faculty workflow, or Manual workflow file changed.
- Pending/risks and next step: the focused implementation is PASS. Browser and MariaDB validation are not part of this solver gate, and the isolated worktree database is not a migrated runtime target. The next separately authorized gate is focused independent review; do not stage, commit, push, apply migrations, deploy, or restart in this session.

### Previous session: Automatic readiness solver and processing-progress remediation
- Date/baseline: 2026-08-15; worktree `D:\teachermateplus-automatic-generation-simplified-ui`, branch `fix/automatic-generation-simplified-ui`, unchanged HEAD `18f8214b16f93c7737dfe6378de77b044e7a7418`; the starting worktree was clean and this gate forbids commit, push, migration application, deployment, restart, Manual changes, and Faculty Request to Reopen work.
- Completed: Automatic flat readiness no longer calls the generic 250,000-state `solve_two_set_feasibility()` path. Readiness and Set A/B output share `solve_automatic_identity_aware_two_sets()`, including logical fingerprint alternatives, exact final/campus/difficulty quotas, one fingerprint per set, and minimum overlap. Its zero-overlap path compresses fixed logical cells, constructs a deterministic disjoint quota-table pair, and uses HMAC-ranked bipartite representative assignment. Contributor representation/concentration is descriptive on this hard-feasibility path and cannot create a readiness blocker. The independent review's sole blocker was remediated by `resolve_automatic_generation_max_states()`: readiness, deadline processing, and direct Automatic generation now use one configured/override budget with the existing 1,000,000 default, and the selector has no hidden 250,000 fallback. Manual Review retains its generic feasibility solver, advanced identity-aware selector, and unchanged limits.
- SASA regression: the exact 200 submitted / 123 unique / 77 redundant shape contains 121 singleton logical groups plus alternative groups of 39 and 40 rows, final 50, campus `17/17/16`, and difficulty `15/25/10`. The focused pure solver passed with `feasible=True`, `limit_hit=False`, `states_explored=1`, `minimum_overlap=0`, and about 1.1 seconds. The database-backed end-to-end regression also reached readiness in about 1.27 seconds / one solver state and generated both 50-item sets before its first run exposed and then corrected a test-only fingerprint assertion.
- Progress UX: Automatic Summary managers can submit the existing regeneration endpoint directly. The page and generation workspace immediately show an indeterminate animated progress bar with `Processing Set A and Set B... Please wait.`, disable generation, reopen, and navigation actions, prevent duplicate submits, navigate to the normal generated result on success, and restore controls with a content-safe message on failure. No fake percentage, schema field, polling loop, or background job was added; persistent cross-page in-progress rendering is therefore not claimed.
- Changed files: `apps/departmental_exams/generation_algorithms.py`, `generation_readiness.py`, `generation_services.py`, `automatic_workflow.py`, `stage6_views.py`, `tests_stage6_algorithms.py`, `tests_automatic_workflow.py`, `tests_stage6_generation.py`, `templates/departmental_exams/admin/automatic_generation_summary.html`, `generation_workspace.html`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, and this handoff.
- Validation completed: the full Stage 6 algorithm module passed 24/24 in 8.761s. The final database-backed exact SASA regression passed 1/1 in 54.129s and proved both 50-item sets, exact campus/difficulty quotas, and no repeated normalized fingerprint. The final Automatic policy/dedupe/Manual/readiness/Summary batch passed 7/7 in 229.626s. The initial relevant Stage 6 generation/campus integration collection completed 23 passes and one failure caused only by the old spinner-text assertion; after updating that assertion, its focused regression passed 1/1 in 31.670s. Fresh blocker-remediation validation passed 3/3 in 20.276s for configured-budget equality, consistent one-state limit blocking, actual Set A/B output, and the unchanged Manual 500,000 default. The full 24-test pure solver module plus the exact database-backed SASA readiness/generation regression then passed 25/25 in 15.879s. `python manage.py check` passed with zero issues, `makemigrations --check --dry-run` reported no changes, and post-remediation `git diff --check` passed with line-ending notices only. The earlier `migrate --plan` evidence remains carried forward; no migration command was run in the remediation gate, this change creates no migration, and none was applied.
- Pending/risks and exact next steps: perform a focused re-review of the shared Automatic budget resolver before any separately authorized commit-preparation gate. Browser execution and cross-refresh persistent progress are not validated or implemented because current persistent state exposes terminal outcomes only. No commit, push, staging, migration application, deployment, service restart, or staging-environment access has occurred.

### Previous session: Automatic Generation normalized-question dedupe remediation
- Date/baseline: 2026-08-14 to 2026-08-15; worktree `D:\teachermateplus-automatic-generation-simplified-ui`, branch `fix/automatic-generation-simplified-ui`, unchanged HEAD `cda1921dd5b90016091f07cddd21cda3be300662`; the existing nine-file remediation was preserved.
- Completed: Automatic Generation now treats each normalized `QuestionPayloadService.question_fingerprint()` as one logical candidate instead of removing all rows in a collision group. All eligible duplicate rows remain alternatives until the global two-set solver jointly chooses logical-question use and one representative, under final count, campus, difficulty, section, contributor, overlap, and deterministic HMAC objectives. The exact 52-row/50-logical counterexample now meets campus `17/17/16` and difficulty `15/25/10`; campus, difficulty, and contributor alternatives have dedicated regressions. Manual Generation retains its unchanged raw-row pool and 500,000-state selector bound.
- User-facing behavior: Automatic readiness and the non-confidential Automatic Summary distinguish submitted rows, unique normalized questions, and redundant duplicate copies automatically ignored (for example, `200 submitted • 123 unique • 77 duplicate copies automatically ignored.`). Redundant copies are warning/information only when the deduplicated pool can generate. If not, the action reason presents the real aggregate or difficulty availability such as `Moderate: 22 available / 25 required`; duplicate rows are never called invalid. No faculty identity or question content is exposed.
- Safety/selection: an Automatic set is defensively rejected if it would contain two equal normalized fingerprints. The Automatic input fingerprint records `normalized-text-v3`, and its bounded identity-aware proof limit remains 1,000,000 states (Manual remains 500,000). For the staging-scale zero-overlap shape, the solver proves campus/difficulty table pairs in objective order and performs deterministic HMAC-ranked bipartite assignment; the 200/123/77 regression completed in 16 states and 4.807 seconds, versus the reviewed pre-fix 568,777 states and 40.119 seconds. No model or schema change is required.
- Changed files: `apps/departmental_exams/generation_algorithms.py`, `apps/departmental_exams/generation_readiness.py`, `apps/departmental_exams/generation_services.py`, `apps/departmental_exams/automatic_workflow.py`, `apps/departmental_exams/tests_automatic_workflow.py`, `templates/departmental_exams/admin/_stage6_readiness.html`, `templates/departmental_exams/admin/automatic_generation_summary.html`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, and this handoff.
- Validation: the isolated real 200/123/77 regression passed 1/1 in 107.618s with the identity solver at 16 states / 4.807s; the final required Automatic dedupe, policy, shortage, and Manual batch passed 15/15 in 382.910s; Stage 6 algorithm tests passed 22/22 in 5.412s; Stage 6 campus/generation integration passed 24/24 in 495.801s. `python manage.py check` passed with zero issues, `makemigrations --check --dry-run` reported no changes, and final `git diff --check` passed with line-ending warnings only.
- Risks/next steps: a broader combined 91-test command was allowed to run for 1,804 seconds but reached its shell timeout without terminal test output, so it is not passing evidence; all requested focused groups were subsequently split and passed. Perform an independent focused review before any separately authorized staging/commit gate. No commit, push, migration application, deployment, service restart, or staging-environment access occurred.

### Stage 6 Campus-Code Adapter Remediation
- Date/baseline: 2026-08-13; branch `feat/departmental-exam-builder-stage5-8`; unchanged HEAD `7eb5ca7716fe359bdbc8bc2ceea23a2fc002e1aa`. Staging commit `7f479cd046c745c11d4d3ef10ad979a24b9584a9` is an ancestor of this HEAD with three later Departmental Exam Builder commits retained. The initial seven dirty paths, including both protected Submission Readiness files and `logs/system.log`, were preserved.
- Completed: added one explicit Stage 6 Campus.code adapter for the approved internal, deployed `NCBA-*`, and setup-bundle `NCBA-01/02/03` aliases. Readiness canonicalizes participating snapshot and Submitted source-campus ingress; confidential question placement uses the same boundary. Unknown/unofficial codes remain fail closed, and distinct Campus rows that collapse to one canonical key are rejected. `generation_algorithms.py`, 33/33/34 weights, difficulty/section/scenario/overlap/contributor/HMAC semantics, generation/approval services, algorithm version, revision/audit behavior, and canonical persisted quota/item snapshots are unchanged.
- Changed by this gate: `apps/departmental_exams/stage6_campus_codes.py`, `apps/departmental_exams/generation_readiness.py`, `apps/departmental_exams/blueprint_services.py`, `apps/departmental_exams/stage6_views.py`, `apps/departmental_exams/tests_stage6_campus_codes.py`, plus this required handoff and the isolated `CHANGE_LOG.md` / `TEACHERMATEPLUS_CONTEXT.md` behavior notes. No migration, messaging, Submission Readiness, log, secret, generated asset, or unrelated application file changed by this gate.
- Validation: `apps.departmental_exams.tests_stage6_campus_codes` passed 10/10, including the confidential workspace normal real-code case and authorization/collision fail-closed coverage; unchanged Stage 6 allocation tests passed 22/22; focused existing readiness, placement, manual generation, automatic workflow, approval, and corruption regressions passed 6/6. `python -B manage.py check` passed with 0 issues; `makemigrations --check --dry-run` reported no changes; `git diff --check` exited 0 with line-ending warnings only. `migrate --plan` still lists only the pre-existing unapplied `departmental_exams.0013_correct_coverage_source_invariant`; it was not applied and this remediation requires no migration.
- Pending/risks: independent review and a staging-copy smoke for the reported IS223-STAIS case remain recommended. No browser/MariaDB/staging runtime, migration application, commit, push, deployment, or service restart occurred. The misleading shortage-first blocker text remains a deliberately separate non-blocking follow-up.
- Exact next step: independently review the eight-path remediation/documentation diff and rerun the focused real-code module; if approved, use a separately authorized exact-path staging/commit gate while preserving all unrelated dirty work and the protected Submission Readiness/log paths.

### Departmental Exam Builder Stage 5 live-eligibility remediation browser-smoke closeout
- Date/baseline: 2026-08-06; branch `feat/departmental-exam-builder-stage5-8`, unchanged HEAD/merge base `0ccf911afd4c0f799069c04302fd67cac544bacc`, refreshed `origin/main` `fc8ec2c8b5fc7a4fcb19549899427a868120fe36`, and divergence `3 0`. The 74-path staged candidate and index fingerprint `023e0f40c41b2b3bd5e4c3fd63861c17434760e5` remain preserved. The exact unstaged inventory remains `CHANGE_LOG.md`, `HANDOFF.md`, `TEACHERMATEPLUS_CONTEXT.md`, `apps/departmental_exams/contribution_authorization.py`, `apps/departmental_exams/contribution_services.py`, `apps/departmental_exams/csv_import.py`, `apps/departmental_exams/faculty_views.py`, `apps/departmental_exams/tests_stage5_contributions.py`, `apps/departmental_exams/tests_stage5_csv.py`, `apps/departmental_exams/tests_stage5_views.py`, and `templates/departmental_exams/faculty/contribution_workspace.html`; no untracked or unmerged path exists. `logs/system.log` remains protected and filename-only.
- Independent-review finding/remediation: review found render-time authorization parity drift because the Faculty workspace treated a retained `eligibility_sources.is_current=True` row as sufficient after the last matching live assignment or exact permission was revoked but before explicit roster synchronization. Mutation services already recomputed authoritative eligibility and denied writes. `ContributionAuthorizationService.has_retained_live_eligibility()` now supplies the same read-only retained-source/live-source intersection to workspace rendering and locked mutation authorization: a contribution-owned current source must match a currently eligible assignment on assignment, offering, tenant, and campus snapshots. Existing structural eligibility, exact-scope permission evaluation, and direct-deny precedence remain centralized in `ContributorEligibilityService`. A stale source snapshot or unrelated new portal access/assignment cannot reauthorize or rebind the historical contribution during GET.
- Durable behavior: an unsynchronized ineligible Draft remains owner-viewable with saved questions and its persisted Draft/Active roster state, but renders the generic temporary read-only notice with no create, CSV upload, edit, delete, reorder, quota-ready, or Final Submission control/URL. GET performs no roster synchronization, source creation/invalidation/rebinding, workflow/status/revision mutation, or audit write. Direct mutation authorization remains authoritative and returns safe `403` behavior. Eligible 49/50 and 50/50 behavior, synchronized Blocked behavior, Submitted history, ownership, tenant/campus/course/cycle scope, confidentiality, quota policy, roster policy, and audit semantics remain unchanged.
- User-provided authenticated browser evidence: all four required Windows-local scenarios passed. Eligible 49/50 retained Add, Upload, Edit/Delete/Reorder and omitted Final Submission; revoked unsynchronized 49/50 remained Draft and owner-viewable with the temporary read-only notice, no mutation/submission controls, and a safe generic direct-Add denial; eligible 50/50 showed quota reached, hid Add/Upload, retained Edit/Delete/Reorder, and showed Final Submission without being submitted; revoked unsynchronized 50/50 remained Draft and owner-viewable with the temporary notice, no mutation/quota-ready/submission controls, and safe direct-route denial. No HTTP 500, rendering failure, confidential authorization detail, roster synchronization, or GET-side contribution/source/status mutation was observed. This is user-provided manual evidence, not a Codex-executed browser run.
- Case 4 fixture distinction: its initial `You do not have access to this portal.` result came from a local fixture authorization design that used an exact direct deny broad enough to remove general Faculty Portal access, not from product-code remediation failure. The fixture-only correction removed its campus-8 direct `faculty_portal.access` allow/deny, retained general access through the unrelated campus-9 role, and added only campus-9 `dashboard.read`; the retained campus-8 contribution source remained current but live-ineligible. The successful retest required no source, test, template, migration, or repository change and ran no roster synchronization.
- Validation evidence: pre-closeout remediation validation passed the focused regressions 3/3, the complete Stage 5 views module 24/24, the definitive Stage 5 suite with 82 passes plus one expected SQLite concurrency skip from 83 discovered, and the UI/navigation/help group 58/58. System, migration-drift, migration-plan, and protected-log-excluded diff checks passed; no migration was required or applied. This documentation closeout reruns the mandated checks and suites with logs redirected outside the repository; the final command results are recorded in the completed validation line below.
- Closeout validation completed: protected-log-excluded working and cached `git diff --check` both exited 0 with working-copy LF-to-CRLF notices only; `python -B manage.py check` reported zero issues; `python -B manage.py makemigrations --check --dry-run` reported `No changes detected`; and `python -B manage.py migrate --plan` reported no planned operations. `apps.departmental_exams.tests_stage5_views` discovered and passed 24/24 with zero failures, errors, or skips in 88.149 seconds. The definitive five-module Stage 5 suite discovered 83 and completed 82 passes, zero failures/errors, and one expected SQLite concurrency skip in 344.379 seconds. The UI/navigation/help group discovered and passed 58/58 with zero failures, errors, or skips in 130.297 seconds. External command elapsed times were 126.490, 382.005, and 165.246 seconds, respectively; validation output and Django logs are under `%TEMP%\tmp_stage5_live_eligibility_closeout_validation_20260806_152924`. No migration was created or applied.
- Files changed by this closeout: only `HANDOFF.md`, `TEACHERMATEPLUS_CONTEXT.md`, and `CHANGE_LOG.md`. The eight pre-existing unstaged source/test/template paths were hash-checked before documentation editing and remain outside this gate. No fixture or server state is changed by this documentation closeout.
- Pending/risks: real MariaDB concurrency remains unproven; MariaDB migration/locking rehearsal, production cleanup scheduling, and the non-blocking CSV post-insert failure-injection observation remain pending. Stage 5 remains uncommitted, unpushed, unintegrated, undeployed, and unreleased. The local prefix-scoped smoke fixture remains present, and verified TeacherMate+ runserver PID `6484` remains on `127.0.0.1:8000`, until separately authorized cleanup/stop gates. Stage 6 remains unimplemented; its approved approximately 33% Cubao, 33% Fairview, 34% Taytay sourcing policy plus difficulty blueprint is preserved.
- Exact next gate: `Stage 5 Live Eligibility Remediation Independent Re-Review`. Do not begin that review, stage, commit, push, integrate, migrate, deploy, clean the fixture, or stop/restart the server without separate authorization.

### Departmental Exam Builder Stage 5 post-smoke documentation and validation closeout
- Date/baseline: 2026-08-06; branch `feat/departmental-exam-builder-stage5-8`, unchanged HEAD and merge base `0ccf911afd4c0f799069c04302fd67cac544bacc`; fetched `origin/main` is `fc8ec2c8b5fc7a4fcb19549899427a868120fe36`, with approved divergence `3 0`. The staged candidate remains 74 paths with index fingerprint `023e0f40c41b2b3bd5e4c3fd63861c17434760e5`. The only starting unstaged product deltas were the eight expected post-smoke remediation paths: `contribution_authorization.py`, `contribution_services.py`, `csv_import.py`, `faculty_views.py`, the three Stage 5 contribution/CSV/view test modules, and `contribution_workspace.html`; no untracked or unmerged path existed. The protected `logs/system.log` remained unstaged and filename-only.
- Current status: Stage 5 Faculty Contribution and Question Submission is locally complete, and the authenticated Windows-local browser smoke passed based on the user-provided manual evidence below. Stage 5 remains uncommitted, unpushed, unintegrated, and undeployed. The post-smoke source/template/test remediations and these documentation changes remain unstaged over the earlier staged candidate and have not yet received the required independent review.
- Delete/resequencing remediation: browser smoke exposed `ValueError: Cannot force an update in save() with no primary key.` when deleting the first of two questions. Django cleared the deleted object's primary key after `delete()`, allowing that instance to pass the survivor filter and reach the position rewrite. `QuestionMutationService.delete()` now captures persisted survivors before deleting the target and resequences only those survivors. Repeated deletion retains the existing owner-scoped missing-object behavior. Quota, authorship, tenant scope, and question-content policy are unchanged. Focused coverage was added in `tests_stage5_contributions.py` and `tests_stage5_views.py`.
- Exact-quota remediation: at 50/50, the workspace still showed Add Question and Upload CSV; Add Question returned a generic page-state error, although the server prevented a 51st question. Authoritative capacity checks now cover manual create and CSV preview/confirmation, and quota exhaustion maps to a quota-specific conflict response. At 50/50, Add Question and Upload CSV are hidden, a clear quota-reached message is displayed, and Final Submission is available when all other rules pass; Draft edit/delete/reorder remain available. Returning to 49/50 restores create/upload and removes Final Submission. CSV confirmation remains append-only, quota-bound, atomic, and protected against a capacity race; it is not an overwrite or replacement workflow. The implementation/test areas are `contribution_authorization.py`, `contribution_services.py`, `csv_import.py`, `faculty_views.py`, `contribution_workspace.html`, `tests_stage5_contributions.py`, `tests_stage5_csv.py`, and `tests_stage5_views.py`.
- User-provided authenticated manual browser-smoke evidence: manual create and edit passed; delete plus survivor resequencing passed after remediation; valid CSV preview passed; 49 valid CSV questions appended atomically to one existing question and produced exactly 50/50; invalid CSV validation had already been exercised by fixture/test flow; the 50/50 controls and quota message passed; returning to 49/50 restored create/upload and removed Final Submission; exact-quota final submission passed; Submitted remained read-only after refresh; Blocked Draft retained owned questions without mutation controls; configurator Contributor Completion showed aggregates without question content; idempotent roster synchronization reported 0 created, 0 activated, 0 blocked, retained revision 3, and left Submitted, Blocked, and replacement records unchanged; an assigned Reviewer could view aggregate monitoring but had no Synchronize Roster control; reviewer/configurator monitoring exposed no question text, choices, correct answers, or preview payload; existing Grade Summary, Attendance, Activities, and period summary remained functional; submitted periodic-grade HTML print opened correctly in legal landscape; encoded zero remained distinct and visible; and no HTTP 500 remained. This is user-observed evidence, not a Codex-executed browser run. No credentials or confidential question content are recorded.
- Durable roster/history policy: Submitted contributions are frozen historical records and are never blocked, rebound, reactivated, or reassigned by later synchronization. Loss of eligibility blocks a Draft without deleting questions; an independently eligible replacement faculty member receives a separate contribution. Synchronization never transfers authorship or question ownership. Roster initialization and synchronization remain explicit POST actions. Reviewer monitoring remains aggregate-only and read-only.
- Local migration state: the normal Windows-local SQLite database is migrated through `departmental_exams.0005_stage5_nullable_schema`, `departmental_exams.0006_stage5_backfill_constraints`, and `navigation.0018_seed_departmental_exam_stage5_menus`. The latest verified `migrate --plan` reported no pending operations. This documentation gate applies no migration. MariaDB migration rehearsal, lock behavior, and real concurrency remain unproven; Staging and Production migrations remain restricted to their deployment gates.
- Validation completed in this gate with Django logs redirected outside the repository: protected-log-excluded working and cached `git diff --check` passed, with LF-to-CRLF working-copy notices only; `python -B manage.py check` reported zero issues; `makemigrations --check --dry-run` reported no changes; `migrate --plan` reported no planned operations; and `showmigrations` marked all three Stage 5 migrations `[X]`. The definitive five-module Stage 5 command discovered 80 tests and completed 79 passes, 0 failures, 0 errors, and 1 expected SQLite concurrency skip in 1391.819 seconds (native command elapsed 1541.404 seconds). The six-module Stage 5 UI/navigation, feature-flag, future-stage protection, core-menu, Faculty-help, and Admin-help command discovered and passed 55/55 with no failures, errors, skips, or reported warnings in 495.907 seconds (native command elapsed 659.030 seconds). No migration was applied to the normal database.
- Preserved future policy: Stage 6 is not designed or implemented here. When a course is offered across Cubao, Fairview, and Taytay, official selection must preserve approximately 33% Cubao, 33% Fairview, and 34% Taytay question sourcing together with the approved difficulty blueprint. The dedicated Departmental Exam Builder menu group also remains required.
- Outstanding gates/risks: targeted independent read-only review of the post-smoke remediations and documentation; repeat Commit Preparation with selective staging of final versions; commit; feature-branch push; exact main integration; MariaDB migration/locking rehearsal; Staging deployment and authenticated smoke; Production preparation with backup confirmation, deployment, and smoke; separately authorized Production scheduling of `purge_expired_question_import_previews`; and real MariaDB concurrency proof for the SQLite-skipped test. Stage 4/4.1 closed/deployed history, Admin Exam Department hotfix history, and Faculty periodic-grade print history remain preserved below.
- Exact next gate after a successful closeout: `Stage 5 Post-Smoke Remediation Independent Review`. Do not begin that review, stage, commit, push, integrate, migrate, deploy, restart, or access a deployed environment without the corresponding separate authorization. `logs/system.log` remains fully protected and excluded.

### Stage 5 Submitted contribution immutability remediation
- Date/base: 2026-08-05; branch `feat/departmental-exam-builder-stage5-8`, unchanged HEAD and merge base `0ccf911afd4c0f799069c04302fd67cac544bacc`; fetched `origin/main` is `fc8ec2c8b5fc7a4fcb19549899427a868120fe36`, approved divergence `3 0`, and the index is empty.
- Completed behavior: `ContributionRosterService` now skips every `SUBMITTED` contribution before eligibility-source creation/invalidation, canonical assignment/campus rebinding, Active/Blocked mutation, bulk updates, counters, and assignment-resolved audits. Submitted status/time, quota/configuration snapshots, revision, source/campus attribution, roster history, owner, questions, and source-history rows remain frozen. Normal `SET_NULL` after physical assignment deletion may clear only the live assignment FKs; immutable assignment/offering/tenant/campus snapshots remain. A newly eligible replacement faculty member still receives an independent Draft, and existing Draft source-loss/restoration behavior is unchanged.
- Changed by this gate: `apps/departmental_exams/contribution_services.py`, `apps/departmental_exams/tests_stage5_contributions.py`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, and this handoff. No model, migration, concurrency, permission, context-processor, menu, template, periodic-print, or Admin hotfix file was changed by this gate.
- Validation: the clean pre-fix regression run discovered five tests and produced four submitted-immutability failures while the cross-campus Draft case passed. After the guard, the new class passed 5/5; the complete contribution module passed 27/27; the definitive Stage 5 suite passed 60 with one expected SQLite concurrency skip (61 discovered); Stage 4/4.1 protection passed 30/30; and the established UI/navigation group passed 50/50. Protected-log-excluded `git diff --check`, `python -B manage.py check`, `makemigrations --check --dry-run`, and `migrate --plan` all exited 0; the plan remains exactly `departmental_exams.0005`, `departmental_exams.0006`, and `navigation.0018`. Validation logs are outside the repository under run-specific `%TEMP%\tmp_stage5_submitted_immutability_*` directories.
- Historical gate status: independent review was pending when this remediation gate ended; that status is superseded by the completed final review recorded above. The full project suite, browser/mobile smoke, MariaDB migration/true-concurrency validation, normal-database Stage 5 migration, staging, commit, push, deployment, restart, and server access remain separate gates.

### Stage 5 origin/main periodic-grade print reconciliation
- Date/base: 2026-08-05; branch `feat/departmental-exam-builder-stage5-8`, unchanged HEAD and merge base `0ccf911afd4c0f799069c04302fd67cac544bacc`; fetched `origin/main` is `fc8ec2c8b5fc7a4fcb19549899427a868120fe36`, with divergence `3 0` and an empty index. The periodic-print commit was manually reconciled without merge, rebase, cherry-pick, stash, staging, commit, or push. All pre-existing Stage 5, Bootstrap/menu, permission-fixture, and mirrored Admin hotfix work remains present.
- Completed behavior: added the GET-only Faculty route `/faculty/my-courses/<offering_id>/periods/<period_id>/summary/print/`, submitted-only lifecycle gate, non-empty matching active tenant/campus scope guard, existing accepted-assignment/RBAC resolution, reusable summary-layout and bounded report-data services, Summary-page new-tab action, and responsive legal-landscape HTML report. MISSING, EXEMPT, encoded zero, Participation/Output, Quiz, averages, CS average, Exam, and official-grade semantics match the published implementation; report rendering is read-only. The five non-overlapping production/template files are blob-identical to current `origin/main`.
- Combined overlaps: `apps/faculty_portal/help_guide.py` retains the complete Stage 5 question-contribution/CSV guidance and adds the published Print Periodic Grades action. `apps/faculty_portal/tests_assignment_acceptance.py` retains `Permission.objects.get(code="faculty_portal.access")` and all prior assignment/workflow coverage while adding the ten periodic-print tests; a syntax-aware scan found no literal duplicate `Permission.objects.create(code="faculty_portal.access", ...)` call.
- Files changed by this gate: `CHANGE_LOG.md`, this handoff, `TEACHERMATEPLUS_CONTEXT.md`, `apps/faculty_portal/help_guide.py`, `apps/faculty_portal/tests_assignment_acceptance.py`, `apps/faculty_portal/urls.py`, `apps/faculty_portal/views.py`, `apps/grading/tabulation.py`, and `templates/faculty_portal/period_summary.html`. Created: `templates/faculty_portal/periodic_grade_print.html`. No other path was modified by this gate.
- Validation completed with logs outside the repository: protected-log-excluded `git diff --check`, `python -B manage.py check`, and `makemigrations --check --dry-run` passed; `migrate --plan` remains exactly `departmental_exams.0005`, `departmental_exams.0006`, and `navigation.0018`. Faculty assignment passed 153/153; definitive Stage 5 passed 55 with one expected SQLite concurrency skip; the coherent UI/navigation group passed 50/50; Admin bulk Exam Department passed 44/44; grading passed 78/78; and nearby Faculty help/attendance/encoding/activity-selection passed 28/28. The first Stage 5 command reached the external 20-minute limit before a Django summary (shell exit 124); its exact orphaned test process was stopped, the original log retained, and the unchanged rerun passed 56 tests with one skip and native exit 0.
- Migration/safety status: this reconciliation requires no migration; Stage 5 migration files were not modified; no migration was applied to the normal Windows-local database; temporary test databases applied the existing chain. No browser/mobile smoke, MariaDB validation, full project suite, deployment, restart, server access, or normal-database migration occurred. `logs/system.log` remained fully protected and unrelated work was preserved.
- Historical gate status: restarting the independent Stage 5 review was the next step when reconciliation ended, and that review later completed with verdict B. Its Commit Preparation next-step wording was then-current and is superseded by the 2026-08-06 post-smoke closeout above.

### Campus-grouped Exam Department selector UX mirror
- Date/base: 2026-08-03; `D:\teachermateplus`, branch `feat/departmental-exam-builder-stage5-8`, unchanged HEAD `0ccf911afd4c0f799069c04302fd67cac544bacc`. Only the exact grouped-selector/form/view/template/test hunks were mirrored; all unrelated Stage 5 work remains present.
- Completed: a shared native Django `Select.optgroups()` widget now groups the bulk Responsible selector, bulk Current Department filter, and CourseForm Exam Department selector under database-backed `CAMPUS-CODE — Campus Name` headings. Options retain `CODE — Department Name — CAMPUS-CODE` labels and exact Department IDs. The bulk page adds readable native controls, an exact selected-target/course-count summary, and confirmation text covering campus, Department, selected count, and replacement mode. Existing scope, active filtering, current-value preservation, authorization, assignment, audit, reviewer, and CycleCourse behavior is unchanged.
- Changed by this gate: narrow hunks in `apps/admin_portal/course_exam_department.py`, `apps/admin_portal/forms.py`, `apps/admin_portal/views.py`, `apps/admin_portal/tests_bulk_exam_department_assignment.py`, `templates/admin_portal/academics/bulk_exam_department_assignment.html`, plus this handoff entry. No Stage 5 migration or unrelated implementation file was changed by this gate.
- Validation: protected-log-excluded `git diff --check`, `python -B manage.py check`, and `python -B manage.py makemigrations --check --dry-run` passed. The required two-module command passed 52/52 in 23.544 seconds. An earlier run had 51 passes plus the same test-only exact-markup assertion error; the assertion was corrected and the full rerun passed.
- Pending/next step: local restart and authenticated browser smoke of all three grouped selectors. No staging, commit, push, migration application, local-server restart, deployment, protected-log access, or secret access occurred.

### Exam Department dropdown sorting remediation mirror
- Date/base: 2026-08-03; `D:\teachermateplus`, branch `feat/departmental-exam-builder-stage5-8`, unchanged HEAD `0ccf911afd4c0f799069c04302fd67cac544bacc`. The exact three ordering/test hunks were mirrored from the canonical hotfix without replacing overlapping files; all pre-existing Stage 5 work remains present. No staging, commit, push, merge, migration application, normal-database access, server restart, deployment, or protected-log/secret access occurred.
- Completed: both bulk-page Department selectors now consume one scoped server-side ordering helper using `campus__code`, `campus__name`, `code`, `name`, and `pk`. Existing active/tenant/campus/department scope, campus-qualified labels, exact option IDs, empty-first choices, assignment authorization, audits, and CycleCourse/reviewer snapshots are unchanged.
- Changed by this gate: ordering/test hunks in `apps/admin_portal/course_exam_department.py`, `apps/admin_portal/views.py`, and `apps/admin_portal/tests_bulk_exam_department_assignment.py`, plus this handoff entry. No Stage 5 migration or other Stage 5 implementation file was changed by this gate; no migration is required or created.
- Validation: protected-log-excluded `git diff --check`, `python -B manage.py check`, and `python -B manage.py makemigrations --check --dry-run` passed. `python -u -B manage.py test apps.admin_portal.tests_bulk_exam_department_assignment apps.admin_portal.tests_department_dropdown_labels --verbosity 1` passed 52/52 in 17.141 seconds with no failures, errors, or reported skips. Django logging was redirected outside the worktree.
- Pending/next step: commit preparation for the canonical sorting correction. Real-browser re-smoke was not rerun in this gate; the prior authenticated page smoke remains historical evidence.

### Bulk Exam Department Assignment hotfix local Stage 5 integration
- Date/base: 2026-08-03. The exact approved hotfix commit `e8f0176fd30074fb35787b876901f81fe2f03d49` is published on `origin/main` and `origin/hotfix/bulk-exam-department-assignment`. Its functionality and documentation were manually reconciled into the existing dirty `feat/departmental-exam-builder-stage5-8` working tree at unchanged base HEAD `0ccf911afd4c0f799069c04302fd67cac544bacc`; no merge, cherry-pick, rebase, staging, commit, or push was used.
- Completed: added `/admin-portal/academics/courses/exam-departments/bulk/` and a Courses-list action for users with `courses.update`. The page uses current tenant and supported Course/Department scope, active records only, exact `CODE — Name — CAMPUS-CODE` Department choices, server-side code/title and assignment filters, an exact current-Department filter, visible-row selection controls, default non-overwrite behavior, and an explicit replacement control. Existing Course create/edit Exam Department choices use the same campus-qualified label and preserve only the current active same-tenant exact out-of-scope value.
- Service semantics: the service rechecks active user, Admin Portal access, direct-deny-aware `courses.update`, current tenant, active exact Department, and every active scoped Course; requires a real boolean replacement decision; locks and revalidates the scoped target Department first, then locks selected Courses in primary-key order inside one atomic transaction. Only `Course.exam_department` and its normal timestamp change. Per-Course audits contain bounded old/new Exam Department identity data. Existing `CycleCourse.responsible_department` snapshots, reviewers, lifecycle state, ordinary Course/Offering departments, and unchanged/skipped audits are untouched; future cycle snapshots use the updated exact Course FK.
- Changed by this gate: approved hotfix hunks were reconciled into `CHANGE_LOG.md`, `HANDOFF.md`, `TEACHERMATEPLUS_CONTEXT.md`, `apps/admin_portal/forms.py`, `apps/admin_portal/urls.py`, `apps/admin_portal/views.py`, and `templates/admin_portal/academics/course_list.html`; the approved `apps/admin_portal/course_exam_department.py`, `apps/admin_portal/tests_bulk_exam_department_assignment.py`, and `templates/admin_portal/academics/bulk_exam_department_assignment.html` files were added. All pre-existing Stage 5 work remains present.
- Validation completed with external temporary Django logging: protected-log-excluded `git diff --check`, `python -B manage.py check`, and `python -B manage.py makemigrations --check --dry-run` passed; `migrate --plan --no-color` listed only the existing uncommitted Stage 5 migrations `departmental_exams.0005`, `departmental_exams.0006`, and `navigation.0018`. The focused hotfix group passed 51/51, the integration-sensitive group passed 98/98, and the directly affected Stage 5 views/contributions/migrations group passed 45/45, with zero failures, errors, or skips. Two earlier foreground attempts of the 98-test group timed out at 300 and 720 seconds, and one earlier foreground attempt of the 45-test group timed out at 720 seconds before emitting a result; monitored reruns completed `OK` in 547.144 and 551.233 seconds of test execution respectively. No hotfix migration exists or is required, and no migration was applied to the normal local database. Local runtime/browser smoke remains pending. Protected `logs/system.log`, secrets, databases, caches, the isolated hotfix worktree, deployed environments, and local services were not opened or manipulated.

### Departmental Exam Builder Stage 5 Bootstrap and menu remediation
- Date/status: 2026-08-03. The approved UI/navigation remediation is complete on `feat/departmental-exam-builder-stage5-8` at unchanged HEAD `0ccf911afd4c0f799069c04302fd67cac544bacc`. Forms now use Bootstrap widget classes, explicit accessible labels/form-check markup, adjacent invalid feedback, generic hidden-state alerts, wrapping actions, and retained CSRF. Faculty/Admin 400/403/409/410 responses use their portal shells and generic confidential-safe explanations; owner-scoped 404 behavior is unchanged. CSV preview uses responsive metric columns, a responsive choice table, wrapping content, and Bootstrap alerts. Faculty Stage 5 routes keep only Question Contributions active; Admin route groups keep only the intended Overview, Assigned Course Examinations, or Contributor Completion item active, including roster actions.
- Changed for this remediation: `apps/departmental_exams/contribution_forms.py`, `faculty_views.py`, `monitoring_views.py`, and `tests_stage5_views.py`; `apps/core/context_processors.py`; `templates/departmental_exams/faculty/question_form.html`, `csv_upload.html`, `csv_preview.html`, `contribution_submit.html`, and new `error.html`; `templates/departmental_exams/admin/roster_action_confirm.html` and new `error.html`; `templates/faculty_portal/base.html`; `templates/admin_portal/base.html`; `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, and this handoff. Business services, contributor eligibility, roster rules, lifecycle, quota, CSV parsing/import logic, Stage 5 migrations, and Stage 6-8 behavior were not changed.
- Validation actually run: scoped `git diff --check` passed; `python -B manage.py check` passed with zero issues; `makemigrations --check --dry-run` reported no changes; `migrate --plan` remained exactly `departmental_exams.0005`, `departmental_exams.0006`, and `navigation.0018`. The modified view/UI module passed 16/16. The coherent Stage 5 UI/navigation, feature-flag, future-stage protection, core-menu, Faculty-help, and Admin-help group passed 50/50. The definitive five-module Stage 5 suite discovered 56 tests: 55 passed and one expected SQLite concurrency test skipped. Temporary test databases applied existing migrations; no migration ran against the normal Windows-local database.
- Historical gate status: browser/mobile/tablet smoke, MariaDB migration/concurrency smoke, the full broader project suite, scheduler installation, normal-database Stage 5 migration, commit/push, deployment, and restart remained separate gates at that time. The later independent review and Windows-local migration/browser gates are now superseded by the current closeout record above; MariaDB, scheduler, commit/push/integration, and deployment remain outstanding. Keep `logs/system.log` fully protected and preserve all unrelated worktree changes.

### Departmental Exam Builder Stage 5 implementation and focused validation
- Date: 2026-08-02. Implementation and focused developer validation are complete on `feat/departmental-exam-builder-stage5-8` at unchanged HEAD `0ccf911afd4c0f799069c04302fd67cac544bacc`. No staging, commit, push, merge, normal-database migration, browser smoke, deployment, service restart, or server access occurred. The execution baseline states Stages 1-4.1 are closed and deployed; older Stage 4.1 preparation wording below is historical and superseded.
- Completed behavior: source-based contributor eligibility evaluates each concrete accepted `FacultyAssignment` and offering campus independently with the opt-in exact-scope mode of `PermissionService.has_assigned_permission(..., "faculty_portal.access", tenant_id, campus_id)` and matching set-based selectors; existing helper callers retain their prior nullable-scope behavior. There is no superuser contributor shortcut. Future Open transitions initialize one grouped-course contribution per faculty atomically; already-Open courses use explicit exact-configurer Initialize/Synchronize POSTs. Source history is retained, Active Drafts block only after their final valid source is lost, valid restoration reactivates Drafts, and Submitted contributions remain historical.
- Faculty workflow: owner-only dashboard/workspace; blocked and submitted read-only states; normalized plain-text manual MCQ create/edit/delete/reorder with revision and exact-quota protection; exact UTF-8 CSV template; strict 2 MB/200-row validation; confidential 30-minute database-backed preview/error CSV; append-only all-or-nothing confirmation; immediate preview-payload purge; and exact-quota idempotent final submission. Admin monitoring is aggregate-only and exact-scoped; it does not fetch or display question text, choices, answers, fingerprints, or preview payloads.
- Transactions/audits: mutations lock parent-first (`ExaminationCycle -> CycleCourse -> CourseExamConfiguration -> FacultyContribution -> QuestionImportBatch -> questions` as applicable), reauthorize and revalidate feature/lifecycle/source/deadline/quota/revisions after locking, and keep audit writes in the same transaction. Added non-confidential roster, question, CSV-import, reorder, deletion, assignment-rebind, and submission actions. The bounded `purge_expired_question_import_previews` command purges confidential rows and retains only non-confidential shells for up to 30 days.
- Migrations created: `departmental_exams.0005_stage5_nullable_schema` (nullable-first additive schema), `departmental_exams.0006_stage5_backfill_constraints` (deterministic quota/configuration/source/position backfill, preflight, final constraints/indexes, and guarded reverse), and `navigation.0018_seed_departmental_exam_stage5_menus` (Faculty contribution and Admin monitoring entries; ensures the existing Faculty Portal access permission exists before attaching the menu gate). No new RBAC migration was added. No migrations were applied to the normal Windows-local database; migrations ran only in temporary test databases.
- Validation: the definitive current Stage 5 command discovered 51 tests and finished with 50 passed, 0 failures/errors, and 1 expected SQLite skip for MariaDB row-lock scheduling. The final related 96-test command completed 91 passes before five legacy Faculty help-guide fixtures collided with the newly ensured existing permission; after changing that fixture to `get_or_create`, the affected module passed 5/5. A separate navigation/feature/future-stage guard rerun passed 11/11. The post-audit contributor/view pair passed 33/33, including exact permission parity and lifecycle-aware read-only presentation. Earlier focused runs exposed only test-fixture/assertion issues (multi-snapshot `.get()`, stale in-memory configuration, a privacy assertion phrase, redirected dashboard context, Stage 5 menu expectations, and pre-seeded permission fixtures); each was remediated and the affected tests rerun. `python -B manage.py check` passes and `python -B manage.py makemigrations --check --dry-run` reports no changes.
- Changed implementation areas: the opt-in exact-scope path in the core assigned-permission helper; Departmental Exams models/services/routes plus new Stage 5 authorization/selectors/services/forms/views/CSV/monitoring/cleanup modules; Departmental Exams `0005`/`0006`; navigation `0018`; Faculty/Admin templates and help guidance; the Configurable Features description; menu context filtering; new Stage 5 tests; narrow legacy fixture/Stage 6-8 guard updates; `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, and this handoff.
- Deferred/not validated: Stage 6-8 selection/generation/review/PDF/QR/grade-entry behavior; browser/device smoke; MariaDB migration and concurrency smoke; production scheduler configuration; full project-wide suite; normal local migration; staging/training/production deployment. Production requires a separately authorized hourly scheduler for `purge_expired_question_import_previews`.
- Historical next-step status: the independent Stage 5 review completed before the later post-smoke remediations. Its Commit Preparation wording is superseded by the current closeout record; the remediations now require targeted independent review before selective staging or any release action.

### Departmental Exam Builder Stage 4.1 closed/deployed historical record
- Date: 2026-08-02. This current entry supersedes older release-state wording below; earlier entries remain as historical gate evidence.
- Completed remediation scope: M-1 closes the ordinary `CourseExamConfiguration.save()` bypass for the first-open contribution-deadline/provenance pair; M-2 visibly renders safe hidden stale-state errors on the primary cycle and course configuration pages; L-1 records the complete Stage 4.1 behavior, migration, validation history, and release state. Campus display now deduplicates authorized prefetched campus snapshots by stable campus identity while preserving the real offering count and avoiding per-row or global Campus queries. No Stage 5-8 behavior was added.
- Stage 4.1 behavior: one cycle-wide default contribution deadline and an optional per-course override produce materialized course deadlines with `DEFAULT`, `OVERRIDE`, or `NOT CONFIGURED` provenance. Admin editing is minute-precision in Asia/Manila. Default changes propagate only to eligible never-opened Draft configurations; opened/historical, exempt, inactive-responsibility, and activity-bearing rows remain preserved. The pages stay inside the Admin Portal shell with role-safe navigation, explain the shared-campus grouped examination and responsible exam department, and give authorized users a safe empty assigned-course state.
- M-1 persistence design: supported mutations are the audited services and ordinary model `save()`. Before a protected save, the model resolves the write database through the explicit `using` argument or Django's write router, reads the persisted row through that alias, applies the established `opened_at is not None` ever-opened rule, and compares only `contribution_deadline` / `contribution_deadline_source` that the save could write. Creation, first opening, and unrelated `update_fields` saves remain allowed; no normalization or historical rewrite occurs. A changed protected value raises `ValidationError`, including for `save(update_fields=[...])`, before the database pair changes. `QuerySet.update()`, `bulk_update()`, raw SQL, and migration operations are privileged bypasses and must not rewrite historical deadline/provenance. The existing bounded propagation bulk writes remain limited to eligible never-opened Draft rows.
- M-2 UI design: `course_configuration.html` and `cycle_configuration.html` retain hidden inputs and CSRF while showing hidden-field messages through the established escaped Bootstrap danger-summary pattern. Missing or malformed primary stale-state values receive a generic reload instruction without placing the raw value in visible text. Field errors, non-field errors, authorization, stale-write checks, and fail-closed behavior remain intact.
- Validation completed: focused regressions cover direct save changes to the deadline, source, both fields, and `update_fields`; database refresh after rejection; unrelated-field save; functional first opening; primary-page missing/malformed hidden values; raw-value non-disclosure; no mutation; normal valid submissions; and unauthorized denial. The focused Stage 4.1 suite passed 23/23; cycle-course administration passed 41/41; the complete Departmental Exam Builder suite passed 167 tests with 1 expected SQLite skip; performance passed 2/2; and concurrency passed 4 tests with 1 expected SQLite row-lock-scheduling skip. Independent review returned A — Approved for commit preparation.
- Migration: `apps/departmental_exams/migrations/0004_stage41_default_contribution_deadline.py` is the existing Stage 4.1 schema-plus-data migration. Legacy non-null course deadlines backfill as `OVERRIDE`; null deadlines remain `NULL/NULL`. Its expected SHA-256 is `8F145027135C39DB9D9B830DD45CD505186CF6CA1AFB1ABBBADBDABEA15427D4`. Migration `0004` is applied to the normal Windows-local SQLite database at `D:\teachermateplus\db.sqlite3`, with no pending local migration operation and no local model/migration drift. Staging and production migration application have not occurred and require separately authorized deployment gates.
- Browser smoke: local authenticated browser smoke returned B — Passed with non-blocking observations. The Admin Portal shell/menu, cycle-wide default contribution deadline display, effective configuration/provenance display, campus labels and real offering counts, shared-campus and one-responsible-department wording, and available role-safe navigation/action evidence passed. The safe empty state and non-Superadmin variants were not manually exercised but have automated coverage and are not blockers. No HTTP 500, traceback, `OperationalError`, missing-column error, or material runtime failure was found.
- Release state: superseded by the Stage 5 execution baseline, which identifies Stages 1-4.1 as closed and deployed at starting HEAD `0ccf911afd4c0f799069c04302fd67cac544bacc`. The preparation details below remain historical evidence; this Stage 5 session did not access or change any deployed environment.
- Remediation-session files: `apps/departmental_exams/models.py`, `apps/departmental_exams/forms.py`, `apps/departmental_exams/tests_stage41_usability.py`, `templates/departmental_exams/admin/course_configuration.html`, `templates/departmental_exams/admin/cycle_configuration.html`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, and this handoff. The existing Stage 4.1 implementation files remain uncommitted. Migration `0004` and unrelated protected `logs/system.log` were not modified by this remediation.
- Historical next step is complete and superseded. The current exact next gate is `Stage 5 Post-Smoke Remediation Independent Review`, as documented above.

### Admin Portal Course Offering print reports
- Date: 2026-07-25
- Completed: added two read-only, print-friendly reports without altering the existing list/assignment workflows. `/admin-portal/academics/offerings/print-unassigned/` reuses the supported Course Offerings filters and the existing Area Chair-aware offering list scope, then returns every active scoped offering with no active faculty assignment in any response state, independent of pagination. It orders rows by course title/code, section, and ID; uses a distinct count of exact-offering `is_active=True` / `Enrollment.Status.ACTIVE` rows; and includes Course Code, Course Title, Section, Schedule, Room, Enrolled, active-only total, generated time, scope header, back, and manual Print controls. Malformed supplied campus, academic-year, term, or department IDs now fail closed to an empty list/report instead of raising a server error; valid and blank filter behavior is unchanged.
- Faculty report: the Faculty Assignments page keeps the control disabled until a selected active scoped faculty member exists. `/admin-portal/academics/faculty-assignments/print/?faculty_user_id=<id>` requires the existing `faculty_assignments.read` permission and the current active academic-year/term setting, fails closed for invalid/out-of-scope/inactive faculty and missing active scope, and lists only active assignments within the established scope for that selected faculty/year/term. Its header uses the existing page-local direct-field identity formatter and it makes no assignment/status change.
- Changed files: `apps/admin_portal/views.py`, `apps/admin_portal/urls.py`, `templates/admin_portal/academics/offering_list.html`, `templates/admin_portal/academics/faculty_assignment_list.html`, new `unassigned_offering_print.html` and `faculty_assignment_print.html` templates, `apps/admin_portal/tests_offering_list.py`, `apps/admin_portal/tests_area_chair_offering_visibility.py`, `apps/admin_portal/tests_assignment_acceptance.py`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, and this handoff. No model, migration, new permission, navigation seed, data change, Departmental Exam Builder worktree change, commit, push, deployment, or service restart.
- Validation: malformed-filter coverage passed 1/1 in 1.565 seconds; full offerings passed 16/16 in 23.611 seconds; faculty-assignment acceptance passed 38/38 in 111.258 seconds; Area Chair offering visibility passed 3/3 in 3.411 seconds. `python manage.py check` passed and `python manage.py makemigrations --check --dry-run` reported no changes. `python manage.py migrate --plan --no-color` was inspection-only, applied nothing, and continues to enumerate the local worktree's 166 pre-existing unapplied migrations. Final `git diff --check` passed, the Git index was empty, and final status contained the intended implementation files plus the known unstaged `logs/system.log` runtime change. No commit, push, deployment, migration application, or service restart occurred.
- Deferred maintenance: `AdminFacultyAssignmentPrintReportTests` shares fixtures with the existing acceptance class. Its current coverage is retained; extracting a dedicated fixture is future cleanup, not required for this feature commit.
- Remaining manual smoke: as an authorized Admin user, open both reports in a browser, confirm visible print layout at portrait print preview, long title/section wrapping, correct scope labels, and the Faculty Assignments disabled/enabled control transition.

### Admin Portal Faculty Assignment dropdown label format
- Date: 2026-07-25
- Completed: corrected the page-local Faculty Assignment select label at `/admin-portal/academics/faculty-assignments/` from an em-dash email suffix to `Lastname, Firstname M. (email@example.com)`. The formatter reads `accounts.User.last_name`, `first_name`, `middle_name`, and `email` directly; it uses only the first middle-name character, omits the middle punctuation and email parentheses when those values are blank, and does not fall back to `username`. Existing scope, active-user, RBAC, selected-faculty, option-value, assignment, ordering, and one-query candidate-list behavior remain unchanged.
- Changed files: `apps/admin_portal/views.py`, `apps/admin_portal/tests_assignment_acceptance.py`, and this handoff. The existing `faculty_assignment_list.html` option loop already consumes the page-local prepared label and was intentionally not changed. No model, migration, permission, navigation, deployment, environment, or Departmental Exam Builder change.
- Validation: rendered-option label/scope/selection test passed 1/1, including same-name email tie-break ordering with fixtures created in reverse email order; the final ID tie-break remains in the queryset ordering but cannot be represented with otherwise identical fixtures because `accounts.User.email` is unique. Full faculty-assignment acceptance tests passed 34/34; offerings tests passed 11/11; Area Chair offering-visibility tests passed 2/2. `python manage.py check` passed; `python manage.py makemigrations --check --dry-run` reported no changes; `python manage.py migrate --plan` was inspection-only and applied nothing.
- Remaining manual smoke: as an authorized Admin user, verify selected and unselected dropdown entries with middle name, multiword first name, and blank email at desktop and narrow widths.

### Admin Portal offering faculty display and faculty-dropdown identity labels
- Date: 2026-07-24
- Completed: updated `/admin-portal/academics/offerings/` so an offering with no active faculty assignment renders muted `---` when its existing `enrolled_count` annotation is zero, and the danger `Unassigned` badge when the annotation is positive. Active assignments continue to show faculty names regardless of enrollment. The existing `View all unassigned course offerings` filter is unchanged and still returns every offering without an active assignment.
- Dropdown: updated only the Faculty Assignment page select at `/admin-portal/academics/faculty-assignments/`. It now uses page-local labels in `Lastname, Firstname M. — email@example.com` form, suppressing the middle initial punctuation and email separator when those values are blank. Eligible users retain the existing scope/active authorization query, and ordering is last name, first name, middle name, email, then ID. The shared forms and `User.__str__` are unchanged.
- Changed files: `apps/admin_portal/views.py`, `templates/admin_portal/academics/offering_table.html`, `templates/admin_portal/academics/faculty_assignment_list.html`, `apps/admin_portal/tests_offering_list.py`, `apps/admin_portal/tests_assignment_acceptance.py`, and this handoff. No model, migration, permission, navigation, deployment, environment, or Departmental Exam Builder change.
- Validation: offering-list tests passed 11/11; faculty-assignment acceptance tests passed 34/34, including label ordering/scope/selection and bounded dropdown query growth; Area Chair offering-visibility tests passed 2/2. `python manage.py check` passed; `python manage.py makemigrations --check --dry-run` reported no changes; `python manage.py migrate --plan` was inspection-only and applied nothing.
- Remaining manual smoke: as an authorized Admin user, inspect both offering-table states and the unassigned checkbox at desktop/narrow widths, then verify familiar/same-name faculty selection labels and ordering on the Faculty Assignments page.

### Admin Portal unassigned course-offering filter
- Date: 2026-07-24
- Completed: added the GET filter `unassigned=1` to `/admin-portal/academics/offerings/`. It uses a correlated `Exists` check for active `FacultyAssignment` rows after the existing scoped queryset, so pending/accepted/declined/clarification-requested/expired active rows count as assigned while inactive rows do not. Active and inactive lists, current scope/search filters, and independent pagination remain intact.
- UI: added the `View all unassigned course offerings` checkbox. The current query-string pagination helper retains the checkbox and other selected filters. The Faculty Assigned cell uses the existing `enrolled_count` annotation; the current visual rule is documented in the newer session entry above. No template query was added.
- Changed files: `apps/admin_portal/views.py`, `templates/admin_portal/academics/offering_list.html`, `templates/admin_portal/academics/offering_table.html`, `apps/admin_portal/tests_offering_list.py`, and this handoff. No model, migration, permission, navigation, deployment, environment, or Departmental Exam Builder change.
- Validation: offerings tests passed 11/11; Area Chair visibility tests passed 2/2; `python manage.py check` passed; `python manage.py makemigrations --check --dry-run` reported no changes; `python manage.py migrate --plan` was inspection-only and applied nothing; `git diff --check` passed (line-ending notices only).
- Remaining manual smoke: as an authorized Admin user, submit/clear the checkbox with active and inactive offerings, verify filter and search/scope pagination in a browser, and confirm zero-enrollment versus enrolled Unassigned styling at narrow widths.

### Admin Portal Course Offerings enrollment and faculty columns
- Date: 2026-07-24
- Completed: added `Enrolled` and `Faculty Assigned` to `/admin-portal/academics/offerings/`. The view retains the scoped offering queryset, then annotates each exact offering with a distinct count of `is_active=True` / `Enrollment.Status.ACTIVE` rows and prefetches active faculty assignments with their faculty users. Primary faculty sorts first; pending/non-accepted assignments show their existing status label; no active assignment displays `Unassigned`.
- Changed files: `apps/admin_portal/views.py`, `templates/admin_portal/academics/offering_table.html`, `apps/admin_portal/tests_offering_list.py`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, and this handoff. No model, migration, permission, navigation, or assignment-workflow change.
- Test hardening: the focused suite now constructs active-enrollment comparison offerings across another tenant, campus, academic year, and term, including identical course/section codes in different valid scopes. The query-growth test populates every generated offering with an enrollment and two active faculty/users, then compares enrollment, assignment, and user table-query patterns. Separate 21-row active and inactive lists verify second-page counts, prefetched faculty, and independent `active_page` / `inactive_page` handling.
- Validation: `python manage.py test apps.admin_portal.tests_offering_list` passed 7/7; `python manage.py test apps.admin_portal.tests_area_chair_offering_visibility` passed 2/2; `python manage.py check` passed; `python manage.py makemigrations --check --dry-run` reported no changes; `python manage.py migrate --plan` was inspection-only and applied nothing. `git diff --check` passed.
- Remaining manual smoke: as an authorized Admin user, check active and inactive offering tables at desktop and narrow widths; verify exact counts, multiple names/status labels, Unassigned, filters/search/paging, and Area Chair college-sibling visibility.
### Departmental Exam Builder Stage 4 Durable Integration Handoff
- Stable history: Stages 0-3 are completed. Stage 4 implementation and developer validation completed, and the final independent Stage 4 code review passed. Approved feature commit `614ef27eb9c25eaf1403267118e61dea0e4719a5` was pushed to the remote feature branch. Branch divergence was inspected and understood. Controlled integration merge preparation and merged-state validation passed, producing local integration merge commit `113f5c68953c3bd1ad52f869f5008a344f1fd1bb` with message `merge(departmental-exams): integrate stage 4`.
- Audit confidentiality: broad `CourseExamConfiguration` before/after evidence now retains non-confidential fields but represents `additional_instructions` only as deterministic `additional_instructions_sha256` and `additional_instructions_length` character count. The raw course instructions remain persisted and available to authorized screens but are absent from those audit payloads. Empty or null text maps to the SHA-256 of empty UTF-8 text and length `0`; unchanged saves remain no-ops with no audit.
- Closed-cycle GET: an authorized direct GET for override removal now returns the established lifecycle `404` before constructing the confirmation form or Stage 4 confirmation state. Authorization remains first, so unauthorized access retains its established `403` denial. The existing Closed-cycle POST reaches the writer and remains denied without mutation or audit; Draft/Open GET behavior is unchanged.
- Concurrency proof: the MariaDB/MySQL transactional regression now names a second-aligned `expected_deadline`, passes that exact value through the configuration worker, and asserts exact round-trip equality while retaining the one-child, `60/OVERRIDE`, `55/DEFAULT`, revision-snapshot, cycle-default, coverage, no-lost-override, and no-duplicate-child assertions. MariaDB execution is not claimed.
- Independent integration-review history: the first independent integration review found no executable defect and identified only stale `HANDOFF.md` integration-status wording. Documentation correction commit `500541eb7a12cdb13dde1649aee81bb6adb2f388` recorded that correction. The repeat independent integration review again found no executable defect and identified that the handoff still used transient gate wording. The Admin guide describes current user-visible Stages 1-4 behavior only and does not expose Stage 5-8 functionality.
- Stage 4 implementation inventory (excluding protected `logs/system.log`): `apps/departmental_exams/forms.py`, `models.py`, `services.py`, `stage4_test_support.py`, `urls.py`, `views.py`, `migrations/0003_cao_default_override_counts.py`, `tests_cycle_course_inclusion.py`, `tests_stage4_authorization.py`, `tests_stage4_cao_defaults.py`, `tests_stage4_concurrency.py`, `tests_stage4_configuration.py`, `tests_stage4_migrations.py`, `tests_stage4_performance.py`, `tests_stage4_remediation.py`, `tests_stage4_responsibility.py`, and `tests_stage4_workflow.py`; `templates/departmental_exams/admin/course_configuration.html`, `cycle_configuration.html`, `course_override_remove_confirm.html`, and `cycle_defaults_confirm.html`; `apps/admin_portal/help_guide.py`; `templates/admin_portal/guide.html`; `CHANGE_LOG.md`; `TEACHERMATEPLUS_CONTEXT.md`; and this handoff.
- Feature-head validation truth: the latest focused command discovered 47 tests: 46 passed and the MariaDB/MySQL concurrency case skipped on SQLite with the explicit row-lock-scheduling rationale. The combined Stage 4 command discovered 67: 66 passed with the same one skip. Admin help-guide regressions passed 17/17. `python -u -B manage.py check` passed with zero issues; `makemigrations --check --dry-run` reported `No changes detected`; and `migrate --plan` listed the expected unapplied Departmental Exam Builder and dependency migrations, including `0003`. MariaDB migration smoke, MariaDB concurrency smoke, and real-browser smoke remain pending and are not claimed. Migration `0003` remains required, unapplied, and unchanged; migration `0002` remains unchanged; no `0004` exists; and no additional migration is required.
- Merged-state validation inherited from the independent integration review: `manage.py check` passed; migration drift passed with `No changes detected`; the migration plan passed with nothing applied; `apps.departmental_exams` discovered 140 tests with 139 passed and one explicit SQLite concurrency skip; Main-side Admin Portal regressions passed 92/92; integration-sensitive Departmental Exam regressions passed 53/53; and Stage 4 performance passed 2/2. No migration was applied to the normal database.
- Publication and execution state: `origin/main` has not been updated. Migration application and deployment have not occurred. MariaDB migration smoke, MariaDB concurrency smoke, and real-browser smoke remain pending. Stages 5-8 have not started.
- Durable release process: Before publication to `origin/main`, the latest integration-branch tip must pass an independent integration review and receive separate explicit publication authorization. The exact publication-candidate commit must be determined from the Git branch/ref and its corresponding independent-review report; it is not embedded as a self-referential current-tip value in this tracked handoff.
- Safety: no migration application, staging-environment or production deployment, service restart, permission/menu change, or normal-database change has occurred. Codex did not directly inspect or manipulate the contents of `logs/system.log`; `.env`, secrets, and protected worktrees remain unchanged.

### Departmental Exam Builder Stage 3 Included/Exempt workflow
- Date: 2026-07-26
- Git checkpoint: branch `feat/departmental-exam-builder`; the Stage 0-4 feature tip is `614ef27eb9c25eaf1403267118e61dea0e4719a5` (`feat(departmental-exams): finalize stage 4 course configuration`). Completed commits include Stage 1-2 foundation `558fa476a08b93ac39615bf30e37307ad6a9c370` (`feat(departmental-exams): add stage 1-2 foundation`), Stage 3 application code `e06cce744dfb2f249ca2dcac131e462890549a96` (`feat(departmental-exams): add course inclusion workflow`), and Stage 3 documentation `af08c596929d6471203c7ea1d14ce5c6998a8b1e` (`docs(departmental-exams): document stage 3 workflow`). Stage 3 implementation, validation, commits, and feature-branch push are complete; it is not deployed.
- Implemented behavior: only `INCLUDED` and `EXEMPT` exist. Exempt and Restore to Included are Draft-only and require trimmed 10-500-character reasons; Exempt also requires an approved category. Include is not a separate route, action, permission, or audit event. Exempt preserves course/offering/responsibility/reviewer/configuration data; configuration alone is allowed and dormant, while substantive faculty contribution or question activity blocks Exempt. Restore preserves configuration and snapshots, does not use the Exempt-only downstream blocker, clears active exemption details, and deletes no downstream data.
- Authorization/responsibility: ordinary mutations require the enabled tenant feature and active exact-scope `departmental_exams.configure`; manage-only/review-only users remain denied, direct deny wins, and cross-tenant access fails closed. Null responsibility remains active-superuser-only and restores to Included / Needs Exam Department. Inactive responsibility hides and denies transition actions, while an otherwise authorized superuser may still open Course Administration to reactivate or reassign it.
- Reviewer/privacy: Exempt preserves the assigned reviewer and shows the reviewer a read-only row with status/category but no full administrative reason or workflow action. Configure-authorized users and superusers may see the escaped full reason. Restore atomically retains an eligible reviewer or clears an ineligible reviewer and records that outcome in audit.
- Concurrency/audit: tenant-scoped `select_for_update()` locking, atomic transitions and audit, stale-form conflict protection, and idempotent same-target submissions are implemented. Course Administration refetches and locks the parent and saves only `responsible_department`, `reviewer`, and `updated_at`. Exactly two transition events exist: `DE_EXAM_CYCLE_COURSE_EXEMPTED` and `DE_EXAM_CYCLE_COURSE_RESTORED`. MariaDB concurrency smoke has not been performed.
- Validation: 20 Stage 3 inclusion tests, 51 Departmental Exam regression tests, and 17 Admin help-guide tests passed (88 total). `python -B manage.py check` passed; `makemigrations --check --dry-run` reported `No changes detected`; `git diff --check`, `git diff --cached --check`, and `git diff HEAD^ HEAD --check` passed.
- Migration status: no new migrations are required for Stage 3, and none was created, committed, or applied to the normal local development database. Four existing migrations remain locally unapplied: `academics.0011_course_exam_department`, `departmental_exams.0001_initial`, `rbac.0032_seed_departmental_exam_permissions`, and `navigation.0017_seed_departmental_exam_menus`.
- Current worktree/safety: the expected remaining local worktree modification after this HANDOFF correction is `logs/system.log`. It must never be opened, read, diffed, edited, staged, restored, discarded, stashed, reset, cleaned, or committed. No migration has been applied to the normal local development database.
- Pending before release: browser smoke for confirmation forms, error/success states, responsive behavior, reviewer read-only visibility, and full-reason privacy; MariaDB concurrency smoke for administration-versus-transition races and lock behavior; staging deployment; and production deployment. No push, deployment, migration application, or service restart has occurred.
- Release context: Stages 5-8 had not started at the Stage 4 integration checkpoint. Publication conditions and follow-up are recorded in the Stage 4 durable integration handoff above; deployment, migration application, and service restart remain separately authorized.

### Departmental Exam Builder foundation authorization and batching remediation
- Date: 2026-07-25
- Completed: reviewer eligibility now requires an active exact-tenant `UserRole` on the exact `CycleCourse.responsible_department`; parent, child, unrelated, and null-department memberships fail closed. Exact-department membership may use the responsible campus or null-campus representation, while effective `departmental_exams.review_generate` remains resolved by the authoritative permission service at the responsible campus. Assigned-reviewer action access now revalidates current user, membership, role, department, campus, permission, direct-deny, and feature state instead of trusting the stored reviewer ID. Grouped cross-campus responsibility still requires only the responsible-department scope, not one role per included offering campus.
- Batching: offering-snapshot batches now span `CycleCourse` boundaries and flush only at 200 rows or completion. Parents are still saved individually before child snapshots are queued, avoiding MariaDB bulk-parent-PK assumptions. The whole cycle and audit remain atomic.
- Test hardening: added exact/parent/child/unrelated/null/wrong-tenant/inactive reviewer matrix coverage; responsible/null/unrelated campus cases; direct allow/deny precedence; separate permission role; duplicate membership; superuser scope; request-time revocation; a 205-course/snapshot boundary test proving `[200, 5]` snapshot batches; second-batch failure rollback; and stronger cross-tenant Course update assertions for field error, unchanged sentinel field, no audit, and no success message.
- Changed files in this remediation: `apps/departmental_exams/services.py`, `apps/departmental_exams/tests_cycle_course_admin.py`, `apps/admin_portal/forms.py`, and this handoff. The broader Departmental Exam Builder foundation is included in the approved Stage 4 integration candidate. `logs/system.log` remains unrelated and was not manually modified or staged.
- Validation: initial focused run completed 24/26 with one field-error placement error and one null-campus fixture failure; both were corrected and their two-test rerun passed 2/2. The complete focused module then passed 26/26 in 153.563 seconds. Feature flag, migration safety, menu performance, existing configurable-feature, and existing Course-form regressions passed 12/12. `python manage.py check`, `makemigrations --check --dry-run`, `migrate --plan`, and `git diff --check` passed. The plan still contains only the four expected unapplied migrations: `academics.0011`, `departmental_exams.0001`, `rbac.0032`, and `navigation.0017`; none was applied to the normal database.
- Pending/manual: browser-smoke Configurable Features wording/toggle, feature-off denial/menu hiding, cycle creation/listing, grouped campus display, exact reviewer options, rejected altered reviewer ID, reviewer change/removal, and cross-tenant Course edit rejection. Review the complete diff and untracked inventory before staging; keep `logs/system.log` excluded. No commit, push, deployment, migration application to the normal database, or service restart occurred.

### Official Grade Release to Faculty display-policy correction
- Date: 2026-07-20
- Root cause: the submission-release setting was implemented as a full Grade Summary access gate. When the selected gradebook was unsubmitted, the template hid the normal student table and selected-period header instead of only protecting the official computed period value. Class Performance and Student Performance Consultation could also render the selected-period value or period trend outside the Summary page.
- Fix: the policy is now a selected-period official-grade display gate. With `Mask official periodic grade until submission` enabled, Faculty retains the full working gradebook, activity scores, supporting computations, student rows, and the `PG`/`MG`/`PFG`/`FX` column header, while every selected-period official value displays a lock plus `Hidden until submission`; its Explain action/direct detail is blocked. Submission releases only that exact period. The deadline setting remains an independent additional gate (`Hidden until deadline`), and the official print sheet remains submission-gated. Class Performance and Student Performance Consultation now mask the same selected-period official values/trend. Tenant lookup remains anchored to `offering.tenant_id`; feature key, grade calculations, submissions, RBAC, and final-grade policy are unchanged.
- Changed files: `apps/faculty_portal/views.py`, `templates/faculty_portal/{period_summary.html,class_performance.html,student_performance_consultation.html}`, `apps/admin_portal/forms.py`, `apps/faculty_portal/{tests_assignment_acceptance.py,tests_performance.py}`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, and this handoff. The separate uncommitted Configuration Management card-rendering repair remains separate. `logs/system.log` is untouched and unstaged.
- Validation performed: six new/updated policy tests passed 6/6, including exact-period and tenant-isolation coverage; related print/default/deadline/final/reopen/Admin configuration tests passed 6/6; the complete `apps.faculty_portal.tests_performance` suite passed 27/27; and the Faculty help-guide suite passed 5/5. `python manage.py check`, `makemigrations --check --dry-run`, empty `migrate --plan`, `python -m compileall -q apps/faculty_portal apps/admin_portal`, `git diff --check`, and `git diff --cached --check` passed (diff commands emitted only LF-to-CRLF notices). The all-in-one Faculty acceptance rerun and the larger legacy grading-regression commands each exceeded this execution environment's 120-second command window and are not counted as passes; the directly affected focused tests completed successfully. The in-app browser controller failed to initialize with `Cannot redefine property: process`, so no browser result is claimed.
- Remaining manual checks: in a working browser, verify an unsubmitted selected period shows the visible header and locked value for desktop and compact phone layouts; verify submission reveals the value and Explain action for that exact period; verify a submitted-but-pre-deadline period shows `Hidden until deadline`; verify print remains unavailable before submission; and check Class Performance and Student Performance Consultation do not display a protected selected-period value.

### Configuration Management card-rendering repair
- Date: 2026-07-20
- Root cause: the page now has 23 direct feature cards, but its deterministic header palette had selectors only for cards 1 through 19. Cards 20 through 23 therefore kept the deliberately transparent shared header background and white header text, which looked like blank white cards and made the collapsed content appear displaced. The Student Academic Intervention Tracking card separately omitted the standard collapsed summary button, leaving no body title or description while its controls were collapsed.
- Fix: changed the existing palette selectors to repeat every 19 cards (`19n + position`) without changing the first 19 assignments or introducing card-name-specific colors. Added the Intervention card's standard title/description summary, wired to its existing collapse target. Feature fields, form POST behavior, tenant scope, RBAC, and official-grade-release behavior are unchanged.
- Tests and validation: added focused rendered-response coverage for all five affected cards, their collapse targets, summary titles/descriptions, header structure, repeated-palette selectors, and existing control names. The focused rendering/save/release run passed 3/3; the complete set of seven Configuration Management acceptance tests passed 7/7. Browser control could not connect in this session (`Cannot redefine property: process`), so a desktop/narrow browser visual smoke remains required. No migration was created or applied, no commit/push/deploy/restart occurred, and `logs/system.log` remains separate and untouched.
- Changed files: `templates/admin_portal/tools/configurable_features.html`, `apps/admin_portal/tests_assignment_acceptance.py`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, and this handoff.

### Urgent Faculty Grade Summary first-student visibility correction
- Date: 2026-07-20
- Confirmed root cause: `55b83ca` preserved the complete server-side `rows` collection and template loop, but its four separately sticky `<thead>` row rules could overlap the beginning of `<tbody>`. The first correction still applied the Faculty top-bar height as a positive `top` value; because `.table-responsive` is the sticky containing scroll container, that translated the complete header downward inside the table and continued to cover student No. 1. The first student was therefore a visibility defect, not an enrollment/query slice or grade-computation defect. The local in-app browser connection could not initialize (`Cannot redefine property: process`), so server context and rendered HTML were verified through deterministic Django responses; the supplied browser screenshot confirmed the containing-block/offset condition.
- Fix: replaced the independent per-row sticky rules with one sticky `<thead>` at `top: 0` in its actual horizontal scroll container and removed the JavaScript top-bar/header-offset calculation. This keeps all header rows in one normal-flow layer, retains sticky student identity columns and the synchronized floating/table horizontal scrollbars, and leaves mobile/print paths unchanged. No arbitrary padding, scroll reset, collection change, formula change, index, or migration was introduced.
- Regression coverage: added deterministic three-student assertions that the first applicable enrollment is first in context, appears in desktop HTML as row number 1, remains before rows 2/3, and yields exactly three student rows. Added a 101-student class assertion that the summary renders all rows in order. Existing query-growth and Average Activities computation tests remain in place. The immediate focused run passed 5/5.
- Validation performed: the full `apps.faculty_portal.tests_assignment_acceptance` suite passed 142/142 in 167.419 seconds. `CorrectionWorkflowTests`, `FinalGradeFormulaTests`, `GradeExplanationServiceTests`, and `GradeEncodingAccessControlTests` passed 72/72 in 180.677 seconds. `python manage.py check`, `python manage.py makemigrations --check --dry-run`, `python manage.py migrate --plan` (no planned operations), `python -m compileall -q apps/faculty_portal apps/grading`, `git diff --check`, and `git diff --cached --check` passed; the diff check emitted only line-ending notices.
- Changed files: `templates/faculty_portal/period_summary.html`, `apps/faculty_portal/tests_assignment_acceptance.py`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, and this handoff. `apps/faculty_portal/views.py` and `apps/grading/services.py` were inspected and are not changed by this correction. `logs/system.log` remains separate and untouched.
- Remaining manual checks: after deploying the correction, verify desktop initial load and scroll at normal/zoomed sizes in Chrome, Edge, and Firefox; confirm row No. 1 is visible below the header, horizontal scrollbar sync remains intact, and print/mobile layouts are unchanged.

### Faculty Grade Summary horizontal navigation and performance remediation
- Date: 2026-07-20
- Root cause and optimization: the editable Average Activities summary GET deliberately refreshes stored grades after a template/source change, but the old service resolved the grading profile/period strategy and queried prefetched subcomponents/details again for every student. It also wrote each unchanged `StudentPeriodGrade` and `StudentFinalGrade` through `update_or_create`, while the view queried the same activity-score set separately for the encoded-zero metric. The remediated service passes one resolved period/final strategy through the class loop, consumes the existing prefetch caches via `.all()`, and skips no-op stored-grade writes while retaining recomputation and audit logging when a value or finalization state changes. The view materializes the bounded offering-period score set once for display and zero-counting. Faculty assignment, tenant/campus/offering/period scope, active enrollment handling, grade formulas, Base-50/Direct Percentage, readiness, lock/submission, print, and correction behavior remain unchanged.
- UI: the desktop Grade Summary now has a native synchronized floating horizontal scrollbar that appears only while the overflowing table is visible. The normal table scrollbar remains the no-JavaScript fallback. The native scrollbar supports pointer/trackpad use; focused keyboard navigation supports Left/Right/Home/End. `ResizeObserver` synchronizes table width after window/sidebar/layout changes. Existing No., Student No., Student Name, and Status sticky columns remain visible; the complete header uses one sticky `<thead>` at zero offset inside the table's horizontal scroll container. Print and compact phone layouts are intentionally unchanged, and the floating control avoids the bottom-left Faculty Help & Privacy controls.
- Query evidence: an isolated representative Average Activities test class measured the reconstructed pre-change GET at 207 queries for one student and 671 for 30 students. The remediated GET measured 193 queries for both one and 30 students. No index is justified from this request-shape evidence; no migration was added.
- Changed files: `apps/faculty_portal/views.py`, `apps/grading/services.py`, `templates/faculty_portal/period_summary.html`, `apps/faculty_portal/tests_assignment_acceptance.py`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, and this handoff. The pre-existing `logs/system.log` change remains separate and untouched.
- Validation performed: the full `apps.faculty_portal.tests_assignment_acceptance` suite passed 140/140 in 169.363 seconds. `CorrectionWorkflowTests`, `FinalGradeFormulaTests`, `GradeExplanationServiceTests`, and `GradeEncodingAccessControlTests` passed 72/72 in 187.734 seconds. Focused Average Activities, final-period summary, query-bound, and floating-navigation tests passed after the final service correction. `python manage.py check`, `python manage.py makemigrations --check --dry-run`, `python manage.py migrate --plan` (no planned operations), `python -m compileall -q apps/faculty_portal apps/grading`, `git diff --check`, and `git diff --cached --check` passed; the diff checks emitted only line-ending notices.
- Remaining manual browser checks: on a desktop class with a wide table, verify both scrollbars remain synchronized, sticky header rows do not overlap below the top bar, the floating bar hides when no overflow/table visibility ends, and it does not overlap the Help & Privacy stack. Verify a narrow/zoomed browser and print preview retain the intended mobile/print layouts.

### Course-template assignment offering coverage verification and query optimization
- Date: 2026-07-20
- Completed: verified that `Admin Portal -> Grading -> Course Template Assignments -> Offerings with no grading template` was an explicit course-template-assignment coverage check, consistent with the existing Faculty warning and project documentation, rather than a test of every tenant profile/fallback resolution path. Renamed the filter to `Offerings with no course-template assignment` so that behavior is clear. The result still requires an active, published, visible exact-term or default course-template assignment and preserves the existing tenant, campus, department, academic-year, term, permission, active-record, and template-visibility scope. Archived offerings are no longer presented as current missing coverage.
- Performance: replaced the per-offering `CourseTemplateAssignment.exists()` call with a scoped correlated `NOT EXISTS` database filter. Missing offerings are now counted in the database, paginated before row construction, and their active faculty assignments/users are prefetched once for the current page. This removes both the template-existence N+1 and missing-offering faculty N+1 from the default metrics view and selected filter.
- Tests and validation: expanded `apps.admin_portal.tests_course_template_assignment_list` from 4 to 5 tests. Coverage proves an exact-term assignment excludes an offering, an inactive assignment remains missing, archived offerings are excluded, and query growth remains bounded when missing offerings with faculty rows increase from 1 to 11. The final combined course-template list, bulk, safety, and Admin help-guide run passed all 50 tests in 44.407 seconds; the 16-test Admin help-guide suite passed again in 13.696 seconds after the final guide wording. `python manage.py check`, `python manage.py makemigrations --check --dry-run`, Python compilation, `git diff --check`, and `git diff --cached --check` passed; `migrate --plan` still shows only the already committed and locally unapplied `accounts.0011_active_portal_session_registry` migration.
- Changed files: `apps/admin_portal/views.py`, `apps/admin_portal/tests_course_template_assignment_list.py`, `templates/admin_portal/grading/course_template_assignment_list.html`, `templates/admin_portal/grading/grading_setup_guide.html`, `templates/admin_portal/guide.html`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, and this handoff. No migration or index was added. The existing `logs/system.log` modification remains separate and untouched.
- Exact next steps: review and stage only the listed coverage-monitor files if approved. MariaDB staging should confirm the `NOT EXISTS` query plan uses the existing course-led assignment constraint/index before considering any additional index, then smoke the default metrics view and filtered offering pagination with realistic offering volume. Keep the existing `logs/system.log` change out of any commit.

### Admin and Faculty login performance remediation
- Date: 2026-07-20
- Completed: replaced the successful-login scan/decode of every unexpired Django session with `ActivePortalSession`, an indexed per-user registry containing only the user ID, Django session key, and timestamps. `ActivePortalSessionService` saves the current Django session, locks the authenticating `User` row, operates only on that user's registered keys, revokes prior keys when the existing single-device policy is enabled, retains multiple live keys when the tenant policy is disabled, removes stale keys without decoding session payloads, and conditionally unregisters the matching key on Admin/Faculty logout. Password-change session-key rotation and hidden Django Admin login/logout are also synchronized with the registry, and hidden Django Admin login now applies the same configured single-session policy instead of bypassing it. Scheduled user deactivation now revokes only the target user's registered sessions. No password hasher, PBKDF2 iteration, OTP, tenant scope, or portal access rule changed.
- Concurrency/tenant/security: the stable user-row `SELECT ... FOR UPDATE` serializes near-simultaneous logins even when no registry row exists yet; the unique session-key constraint prevents duplicate ownership and the `(user, updated_at)` index bounds per-user lookup/cleanup. The registry deliberately has no client-selectable tenant or portal scope because the pre-existing rule is global per user across Admin/Faculty portals and every tenant the account can access. Session keys are never logged or exposed in Admin, and unrelated users' Django session payloads are never decoded.
- Migration: new additive migration `accounts.0011_active_portal_session_registry` creates `active_portal_sessions`, depends on Django sessions, and then invalidates all pre-registry Django sessions once. This controlled one-time logout prevents untracked pre-deployment sessions from bypassing the established single-device rule; users can immediately sign in again, no data backfill is needed, and no password/OTP/business record is changed. Reverse migration cannot restore invalidated sessions. Login traffic must be stopped before applying `accounts.0011` and remain stopped until the new application workers are running; otherwise an old worker could create an unregistered session after the migration's one-time deletion. The current temporary staging deployment script migrates before restarting Gunicorn and therefore must not be used unchanged for this migration. Actual Data Reset now includes the registry and preserves the registry row matching its explicitly preserved current session.
- Navigation: `MenuService.get_menu_tree()` now reads each item's already-prefetched `MenuItemPermission.permission` objects rather than issuing `values_list()` per item. Local seeded measurements changed from Admin menu 59 queries / 0.3934 seconds to 5 queries / 0.3314 seconds, full synthetic Admin dashboard 105 to 51 queries (0.4199 to 0.2344 seconds), and Faculty navigation 7 to 5 queries; Faculty dashboard remained functionally unchanged at 18 queries in the final measurement.
- Session performance evidence: in an isolated migrated test database with 302 active Django sessions, the reconstructed old full scan decoded all 302 rows in 0.012680 seconds. The new registry path revoked the one registered prior session in 0.003605 seconds while leaving all 300 unrelated sessions intact. The important bound is constant per-user registry work rather than local SQLite wall time; MariaDB staging timing remains required.
- Changed files: `apps/accounts/models.py`, `apps/accounts/services.py`, `apps/accounts/views.py`, `apps/accounts/apps.py`, new `apps/accounts/signals.py`, new migration `apps/accounts/migrations/0011_active_portal_session_registry.py`, new `apps/accounts/tests_active_portal_sessions.py`, `apps/core/services/menu.py`, new `apps/core/tests_menu_performance.py`, `apps/admin_portal/data_reset.py`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, and this handoff. The recent enrollment remediation was not modified. The pre-existing `logs/system.log` change remains separate and untouched.
- Validation performed: after the final reviewer correction for hidden Django Admin policy enforcement, the combined authentication, session registry, login lockout, login OTP, Faculty public login/dashboard, Admin configuration/dashboard, Actual Data Reset, and menu performance command passed all 57 tests in 74.540 seconds. Earlier focused runs passed 30/30, 34/34, 31/31, and the pre-review combined 56/56 while session-key rotation and hidden Django Admin cases were added. `python manage.py check` passed; `python manage.py makemigrations --check --dry-run` reported no model changes; `python manage.py migrate --plan` showed only `accounts.0011` creating `ActivePortalSession` and running its controlled session-invalidation operation; compile checks passed; `git diff --check` and `git diff --cached --check` passed with line-ending warnings only.
- Known risks/pending validation: SQLite cannot prove MariaDB row-lock scheduling or production latency. Staging must verify migration duration, confirm all old sessions are logged out once, exercise near-simultaneous logins for the same account, confirm old-browser invalidation and both portal logout paths, measure login POST versus dashboard GET, and re-measure Admin menu/dashboard queries. Synchronous OTP email and the 1,000,000-iteration PBKDF2 baseline remain intentionally unchanged and should be measured separately if login is still slow after this patch.
- Exact next steps: finish independent review validation, then stage only the listed remediation files if approved. Before staging rollout, communicate that migration `accounts.0011` will log out all currently authenticated users once and revise the deployment order so login traffic is stopped before migration and new workers are started before traffic resumes; do not run the current temporary staging script unchanged. On MariaDB verify the migration plan and timing, then run the login/session/dashboard checks above. Do not include `logs/system.log`; do not commit, push, deploy, migrate staging, or restart services without separate approval.

### Enrollment Adjustments and CSV import timeout remediation
- Date: 2026-07-19
- Completed: removed the nested offering-scope subqueries and the unbounded Enrollment Adjustments choice-list render. Authorized offering scope is now materialized once per request and applied as direct tenant, campus, department, program, academic-year, term, section, active-state, and course predicates; program-less offerings remain eligible when their authorized section is in scope. The initial adjustment page loads no offering choices; independent source/destination searches call a permission-protected endpoint only after academic year, term, and campus are selected, return stable course/section labels, and cap each response at 50. Submitted and initial selected offerings are revalidated against the same scope, with inline field errors for invalid selections.
- CSV import path: enrollment preview preloads the authorized offering lookup once per batch, and confirmation bulk-loads staged authorized offerings, active students, and existing enrollment pairs. Per-row transactions, audit/error reporting, duplicate protection, and tenant/campus/department/period authorization remain intact. Stale or out-of-scope staged offering IDs are rejected. Faculty-assignment import inspection confirmed SMTP already runs after the row database transaction; its existing failure-isolation regression proves an SMTP exception preserves the created account and records `CREATED_INVITATION_FAILED`, so no SMTP production change was needed.
- Local performance evidence: on the current 427-offering SQLite dataset, the reconstructed old adjustment choice query had 9 `SELECT` layers, nested `IN (SELECT ...)`, 0.059756-second wall time, and 0.003-second captured database time. The new filtered search returned 6 rows with 1 `SELECT` layer, no nested subquery, 0.003754-second wall time, and 0.001-second database time. A complete adjustment GET returned 200 in 0.247118 seconds with 0.009 seconds captured DB time across 94 request queries and zero direct offering-choice selects. The search endpoint returned 200 in 0.027548 seconds with 0.006 seconds captured DB time across 19 request queries. Enrollment preview and confirmation tests each execute one offering select for both one-row and ten-row batches. Existing FK/index access is used; no speculative index or migration was added.
- Changed files: `apps/admin_portal/forms.py`, `apps/admin_portal/services.py`, `apps/admin_portal/views.py`, `apps/admin_portal/urls.py`, `apps/admin_portal/tests_enrollment_adjustments.py`, `templates/admin_portal/enrollment/enrollment_adjustments.html`, `apps/imports/services.py`, new `apps/imports/tests_enrollment_import_performance.py`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, and this handoff. No timeout, deployment, environment, or requirements setting changed. The pre-existing `logs/system.log` worktree modification remains separate and was restored to its pre-session content after local autoreloader activity.
- Validation performed: the expanded affected suite passed all 135 tests in 196.990 seconds after the program-less-offering and inline-error corrections, covering adjustment authorization/search/query shape and import preview/confirmation scope and bounded queries. Three focused regression tests for malformed/stale selections and program-less offering scope also passed. The initial-page query assertion was then made backend-quote-agnostic and the complete 27-test adjustment module passed again. `python manage.py check`, `python -m compileall -q apps/admin_portal apps/imports`, `python manage.py makemigrations --check --dry-run`, empty `python manage.py migrate --plan`, and both diff checks passed; diff output contained only LF-to-CRLF notices.
- Known risks/pending validation: measurements above are local SQLite evidence, not staging MariaDB proof. The in-app browser control could not initialize (`Cannot redefine property: process`), so authenticated JavaScript interaction and accessibility smoke remain manual. Staging must still capture `EXPLAIN FORMAT=JSON`, request/Gunicorn timings, slow-query-log evidence, a representative CSV preview/confirmation run, and a controlled faculty SMTP wall-time split before the 504 remediation is considered production-verified.
- Exact next steps: review the diff, then on staging exercise authorized and denied adjustment searches and representative CSV batches while collecting the evidence above; monitor slow logs and 504s after any approved deployment. No commit, push, deployment, or staging migration was performed.

### Grading Analytics revision
- Date: 2026-07-19
- Completed: revised the existing read-only Academic Performance Insights and Grade Distribution Monitor without migrations, RBAC changes, navigation changes, feature flags, jobs, or persisted snapshots. The central `AcademicPerformanceInsightService` now uses `FacultyPerformanceService` and established submission-readiness output to separate active enrollment, usable computed grades, no-usable-grade records, coverage, pass/below-threshold counts and rates, median/high/low, threshold-aware neutral distribution bands, and readiness state.
- Accuracy/safety rules: passing thresholds remain profile -> template -> tenant -> 75 fallback. A missing required score is not a failing grade and a saved zero remains a saved record (Raw/Base zero can compute to its configured floor; Direct Percentage zero remains zero). The new neutral bands are Strongly Above, Above, Near, Below, and Well Below the resolved threshold. Section comparisons are marked Not comparable when template/profile/formula/threshold signatures differ. Current results are labelled Provisional / incomplete until all active students have usable grades, unless existing submitted/locked states apply.
- Student/intervention boundary: advisory rows use `Academic Concern — For Faculty Review`; the Admin presentation keeps student identity protected, while CSV is scoped. No intervention record is automatically created and no intervention ownership, fingerprint, case, monitoring, or lifecycle code changed.
- UI/export changes: Insights now includes coverage and denominator columns, section CSV, protected student-review table/CSV, activity-consistency CSV, missing-score-category and concentrated-weight signals. Grade Distribution Monitor removes universal 75-era bands and uses the shared threshold-aware bands in both HTML and CSV. Legacy faculty fail-rate and faculty-comparison ranking cards were removed.
- Changed files: `apps/admin_portal/academic_performance.py`, `apps/admin_portal/grade_distribution.py`, `apps/admin_portal/views.py`, focused Admin analytics/distribution tests, and the related Admin grading templates. `logs/system.log` was not modified.
- Validation/remediation: the initial focused run identified `test_activity_consistency_labels_minor_difference_and_incomplete_setup`: its new unscored test activity correctly triggered the approved high-missing-score rule, while the test intended to cover only a minor activity-count difference. The fixture now provides score records for that activity. The focused Admin Insights, Grade Distribution Monitor, legacy Grading Analytics, Faculty Performance, and Interventions module commands, followed by their combined command, completed after the correction. `python manage.py check` and `python -m compileall -q apps/admin_portal apps/faculty_portal` passed; `makemigrations --check --dry-run` reported no changes; `migrate --plan` reported no operations; `git diff --check` and cached check passed with LF-to-CRLF warnings only. Manual smoke remains required for revised tables/exports and protected student-review presentation.
- Current targeted remediation: corrected the runtime `resolve_lock` call, zero-safe CSV values, active-eligible monitor/activity populations, provisional-grade exclusion from risk and comparison metrics, CSV formula safety, student CSV identity masking, compatible configuration checks, duplicate student-review comparison work, export-limit disclosure, and obsolete faculty-ranking context. No migration, RBAC, navigation, intervention production, or log file changed.
- Final validation: `tests_academic_performance_insights` passed 28 tests, `tests_grade_distribution_monitor` passed 13, `tests_grading_analytics` passed 17, and `tests_performance` passed 26. Their combined command passed all 84 tests in 465.284 seconds. New coverage proves threshold boundaries, zero preservation, all four CSV formula prefixes, protected/unprotected student identity export, incomplete-grade exclusion, active enrollment filtering, incompatible-section exclusion, and no automatic intervention case. `python manage.py check`, `makemigrations --check --dry-run`, empty `migrate --plan`, `compileall`, `git diff --check`, and `git diff --cached --check` passed; the two diff checks emitted only LF-to-CRLF notices.
- Next steps: manually verify authorized/unauthorized scope, exports, configuration mismatch display, and student identity masking in a browser; then submit the revision for independent review. Do not stage, commit, push, or deploy without approval.

### Academic Data Reconciliation Course Offerings redesign
- Date: 2026-07-19
- Completed: replaced the first `No Enrollment` tab with `Course Offerings`. It now lists every active, non-archived offering in the existing validated tenant/campus/academic-year/term scope, without changing reconciliation's read-only behavior. The table shows course, existing schedule text/room or `Not specified`, active class size, section, active faculty assignments, and a roster action.
- Rules and filtering: Class Size is the database-annotated distinct count of `Enrollment` records where `is_active=True` and `enrollment_status=ACTIVE`. Strict Class Size (`all`, `0`, `1_10`, `11_20`, `21_30`, `31_40`, `41_50`, `51_plus`) and Faculty Assignment (`all`, `none`, `assigned`, `multiple`) allowlists apply to database annotations. Zero enrollment remains an exception summary card and a real shortcut to Class Size = 0. Course sorting is allowlisted for course code/title, schedule, room, class size, section, and faculty; existing faculty-tab rules remain unchanged.
- Roster: each row has an accessible real fallback link which JavaScript progressively enhances into a Bootstrap modal. The new GET-only roster endpoint applies the same Admin Portal and reconciliation permission plus validated tenant/campus/year/term scope before resolving the offering. It displays only active `ACTIVE` enrollments ordered by last name, first name, student number, at 50 rows per page, and limits output to student number, name, and enrollment status.
- Performance/CSV: offering counts use filtered distinct annotations and a filtered active-assignment prefetch, preventing multiplication with multiple students/faculty. Roster uses one scoped offering query, active-assignment prefetch, and ordered `select_related(student)` enrollment rows. The full filtered Course Offerings CSV now exports formula-safe Course Code, Course Title, Schedule Text, Room, Class Size, Section Code, Section Name, Faculty Assigned, and deterministic Other Finding values; it is never page-sliced.
- Changed files: `apps/admin_portal/academic_data_reconciliation.py`, `apps/admin_portal/urls.py`, `apps/admin_portal/tests_academic_data_reconciliation.py`, `templates/admin_portal/academics/academic_data_reconciliation.html`, `templates/admin_portal/academics/_academic_data_reconciliation_offering_rows.html`, new roster partial/fallback templates, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, and this handoff. No Grading Analytics, migration, RBAC, navigation-seed, intervention production, or `.env` file was changed. `logs/system.log` was already a separate dirty-worktree file and was never directly edited, staged, or included in this scope.
- Final validation: the required verbose command `python manage.py test apps.admin_portal.tests_academic_data_reconciliation -v 2 --no-color` passed all 22 tests in 83.544 seconds. The earlier `--verbosity 1` focused pass took 50.686 seconds. Direct guarded measurements were 70 queries for both one and fifty Course Offerings rows with multiple students/faculty, and 61 queries for both three and fifty roster rows; no result-count growth occurs. `python -m compileall -q apps/admin_portal`, `python manage.py check`, request-context rendering of all four changed/new reconciliation templates, `python manage.py makemigrations --check --dry-run` (No changes detected), `python manage.py migrate --plan` (No planned migration operations), and `git diff --check` passed. Diff output contained only existing LF-to-CRLF working-copy warnings.
- Next steps: manually smoke the roster modal and fallback with authorized and denied accounts in a browser. Do not stage, commit, push, deploy, or edit `logs/system.log`.

### Academic Data Reconciliation (historical baseline)
- Date: 2026-07-19
- Completed: added a read-only Admin Portal report at `/admin-portal/academic-data-reconciliation/` with Academics -> Data Reconciliation navigation, scoped server-side filters, two category tabs, summary cards, pagination, and formula-safe current-result CSV export. It does not modify offerings, enrollments, assignments, or users.
- Reconciliation rules: active non-archived offerings in the validated tenant/campus/year/term scope appear only when they have zero active `Enrollment.Status.ACTIVE` rows. Active users with a matching active scoped `FACULTY` role appear only when they have zero active faculty assignments for that same offering term. Inactive assignments do not count; active assignments in another term do not exclude the faculty member. Because the existing User model has no employee ID/department/employment profile, the report uses username as Faculty ID and deliberately omits invented data.
- Permission/navigation: migration `rbac.0031` seeds `academic_data_reconciliation.view` for `CAMPUS_ADMIN` and `SUPER_ADMIN`; migration `navigation.0016` idempotently adds the Data Reconciliation item under the existing `ACADEMICS` group. Direct URL checks use the same server-side permission, and page-level campus IDs are accepted only from the authorized scoped list with a target-campus permission check.
- Changed files: new `apps/admin_portal/academic_data_reconciliation.py`, template `templates/admin_portal/academics/academic_data_reconciliation.html`, focused tests `apps/admin_portal/tests_academic_data_reconciliation.py`, RBAC/navigation migrations `0031`/`0016`, `apps/admin_portal/urls.py`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, and this handoff. Existing uncommitted Student Academic Interventions work and `logs/system.log` were preserved.
- UI follow-up: filters are compact and explicitly use Apply Filters/Reset; exception cards show count/denominator/percentage; actual-count neutral notices appear at an 80% exception ratio; tabs are concise and count-bearing; the active tab defines its exact records; tables are reduced to useful wrapping columns with neutral combined findings; export labels are category-specific; each page shows its result range; and offering/faculty sorts are server-allowlisted with safe default fallbacks. Offering CSV now exports separate formula-safe Section Code and Section Name values and still exports all filtered records, not only the visible page.
- Lazy-loading follow-up: only the No Enrollment tab uses progressive loading. Its baseline Load more control is a real conventional next-page link containing the validated campus, academic year, term, category, search, sort, and page parameters. JavaScript enhances that link into authenticated 50-row partial appends and updates both the next lazy URL and the next conventional href; without JavaScript, the link renders the corresponding full page normally. The Faculty tab retains its normal paginator.
- RBAC reverse safety: reversing `rbac.0031` removes only the permission mappings seeded for `SUPER_ADMIN` and `CAMPUS_ADMIN`. It preserves later custom role mappings and direct user grants, and deletes the permission only when no role, user, or other protected authorization reference remains.
- Test diagnosis and corrections: the earlier apparent termination was the execution wrapper reaching its output window while Django continued running, not a test hang or deadlock. Query review found no unbounded queryset or N+1 issue; runtime was primarily repeated PBKDF2 password hashing and in-memory SQLite migration setup. Shared immutable setup moved from per-test `setUp()` to `setUpTestData()`; the navigation test grants only `dashboard.read` after separately confirming reconciliation access remains denied; and the warning/sorting assertions include all valid scoped baseline rows. The 51-offering lazy/export dataset remains the minimum needed to prove a second 50-row chunk and full-result export. No reconciliation business logic or production code changed during diagnosis.
- Final focused result: `python manage.py test apps.admin_portal.tests_academic_data_reconciliation -v 2` discovered 16 tests and passed all 16 in 46.595 seconds. Added coverage proves unauthorized export/lazy requests are denied, campus-specific permission cannot be widened, mismatched/cross-tenant periods remain safe, all four spreadsheet-formula prefixes are escaped, Section Name is exported, the real non-JavaScript page link works and preserves filters, RBAC reversal preserves direct user and later custom role grants, and offering page queries remain fixed at 70 for both one and fifty rows. Earlier successful 10-test runs remain historical evidence only.
- Migration ordering prerequisite: `rbac.0031` still depends on Academic Interventions migration `rbac.0030`, and `navigation.0016` still depends on Academic Interventions migration `navigation.0015`. Academic Interventions must be reviewed and committed first; reconciliation cannot be committed or deployed independently unless migration renumbering/dependency rebasing is handled later as a separate deliberate decision.
- Final non-test validation: `python manage.py check` reported no issues, `python manage.py makemigrations --check --dry-run` reported no changes, `python manage.py migrate --plan` reported no planned operations, and `git diff --check` exited successfully with only existing LF-to-CRLF working-copy warnings.
- Next steps: complete manual Admin browser checks for ratios/warnings, both tabs, sort links, lazy loading, faculty pagination, CSV exports, forged tenant/campus IDs, and direct faculty denial. No commit, push, deployment, staging, or production change was performed.

### Student Academic Intervention Tracking Phase 1
- Date: 2026-07-18
- UI follow-up: the Faculty list now supports class and grading-period filters and renders one responsive card per period, with review candidates separated from the faculty owner's existing records. Student numbers were removed from list/detail presentation while explicit student names remain. The detail page now has responsive hero metadata, section cards, decision history, record panels, and Bootstrap-styled decision/action/follow-up fields. Authorization, ownership, privacy, and lifecycle behavior are unchanged.
- Completed: implemented Phase 1 and completed its post-implementation correction review. The feature now has model-immutable sole faculty ownership, owner/current-scope querysets, non-disclosing co-faculty 404s, server-recomputed analytics conversion, append-only decision supersession revisions, one-active-planned-action enforcement plus planned-to-conducted/cancelled updates, due follow-up display, controlled minimal referrals, GET-only scoped Admin monitoring, disabled archive enforcement, explicit configuration authorization/auditing, and read-only closed/voided templates.
- Ownership/privacy: active analytics deduplication remains limited to the same owner/offering/student/period/fingerprint. Cross-owner cases are neither queried nor disclosed. Creation revalidates current accepted assignment, feature, permission, grading period, and eligible enrollment. Guidance has no record permission or referral-derived access.
- Corrections made during review: added `AcademicInterventionDecisionRevision`; enforced owner immutability and one planned action; gated the analytics query service itself; removed broad exception handling that converted permission failures into redirects; fixed an intervention configuration guard that had been misplaced in unrelated user creation; moved configuration writes through a permission-enforcing service; added scoped historical monitoring without relying on current active assignments; made Admin monitor routes GET-only; removed duplicate hidden grading-period input; and hid all mutation forms on closed/voided records.
- Migrations: local `interventions.0001`, `interventions.0002`, `rbac.0030`, and `navigation.0015` are applied. `interventions.0002` normalizes legacy referral destinations, preserves legacy labels, keeps the newest planned action while marking surplus plans cancelled, backfills existing decisions as revision 1, then adds the new constraints.
- Changed files: `apps/interventions/*` including migration `0002`, `apps/rbac/migrations/0030_seed_academic_intervention_permissions.py`, `apps/navigation/migrations/0015_seed_academic_intervention_menu.py`, `config/settings/base.py`, core feature/context services, Admin/Faculty views and URLs, Faculty/Admin guide/sidebar/configuration/intervention templates, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, and this handoff. The UI follow-up specifically changed `apps/faculty_portal/views.py`, `templates/faculty_portal/academic_interventions/list.html`, `templates/faculty_portal/academic_interventions/detail.html`, and `apps/interventions/tests.py`. `logs/system.log` remains pre-existing dirty worktree state and was not intentionally edited or staged.
- Validation performed: the final full `python manage.py test apps.interventions -v 2` passed 38/38 in 395.136 seconds, including period filtering/grouping, student-number omission, styled detail forms/sections, read-only closed records, ownership privacy, authorization, scope, integrity, and workflow regressions. `python manage.py check`, `python manage.py makemigrations --check --dry-run`, empty `python manage.py migrate --plan`, `python -m compileall`, and `git diff --check` passed; the earlier affected Faculty performance, Admin academic-insights/configuration, and seeded RBAC/navigation tests passed 55/55 in 110.106 seconds. The in-app browser connection still fails during initialization with `Cannot redefine property: process`, so no visual browser result is claimed.
- Pending/manual browser checks: with real scoped test accounts, exercise disabled navigation/direct URLs; manual and analytics creation; every decision and correction; planned/conducted action; follow-up; close/eligible void; co-faculty 404; Admin monitoring/filter/detail; POST 405; cross-scope detail denial; and disabled archive access. Confirm conditional controls, responsive layout, messages, and redirects visually.
- Deferred: campus feature overrides, automated repeated-low/attendance thresholds, notifications/reminders, Guidance workflow/details, exports, student/parent visibility, ownership transfer, and evaluation/disciplinary use.

### Submission Readiness email orientation dataset
- Date: 2026-07-18
- Completed: added DEBUG-only, `--confirm-demo-data`-gated `seed_submission_readiness_email_demo`. It reuses an explicitly selected existing tenant/campus, creates only `TEST-READINESS-EMAIL` records, requires an explicit Academic Head email when seeding, provides a read-only `--inspect` report, is safe to rerun, backs up/restores the tenant readiness-alert policy, rejects cross-campus reuse without reset, and removes only its owned records with `--reset`.
- Local seeded state: NCBA / NCBA-01, `TEST-READINESS-EMAIL-AY`, `TEST-READINESS-EMAIL-TERM`, PRELIM, as-of 2026-07-17, deadline 2026-07-22 11:59 PM Asia/Manila, active open campus lock. Recipient `test-readiness-area-chair` uses `accounts.User.email=readiness-head@example.invalid`; this non-deliverable address is safe for dry-run only. Faculty are `test-faculty-readiness-01` and `test-faculty-readiness-02`; courses/sections A-F each have exactly three active demo students.
- Actual readiness: A `16.67%` include, B `33.33%` include, C `50.00%` exclude, D `16.67%` include, E `33.33%` include, F `100.00% Submitted` exclude. These come from actual six-bucket grading setup, score coverage, attendance sessions/records, and submission state. Local dry run reported `eligible_assignments=4`, `dry_run=1`, and logged the exact four assignment IDs without sending mail.
- Validation performed: read-only `--inspect` reported 6 reused offerings and the expected `16.67/33.33/50.00/16.67/33.33/100.00` outcomes; focused demo command tests passed 2/2, covering record counts, real computed outcomes, Area Chair dashboard scope for all six assignments, open lock/deadline, dry-run no-send, exact qualifying IDs, locmem email exclusion of C/F, idempotent rerun, inspection, owned-only reset, unrelated-user preservation, and policy restoration. The final combined Notifications and complete Admin assignment/configuration class passed 58/58 in 128.194 seconds. `python manage.py check`, `makemigrations --check --dry-run`, empty `migrate --plan`, Python compilation, and `git diff --check` passed (line-ending warnings only). A real local reset removed 6 offerings/owned dependents and left zero demo assignments/students; reseeding restored the orientation dataset with the same values.
- Changed files for this follow-up: new command `apps/notifications/management/commands/seed_submission_readiness_email_demo.py`, new `apps/notifications/tests_submission_readiness_demo.py`, `docs/SUBMISSION_READINESS_EMAIL_ALERTS.md`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, and this handoff. No migration was required for the seeder.
- Pending/manual risk: replace the `.invalid` recipient through a reset/reseed before any controlled SMTP test; visually confirm all six rows under the Academic Head dashboard in a working browser; never run the seeder with production settings. No commit or push was performed.
- Exact next steps: run the documented dry run using the printed tenant ID, review the four logged assignment IDs, optionally reset/reseed with an authorized test mailbox for one controlled send, then reset after orientation if the data is no longer needed.

### Automated Submission Readiness email notifications
- Date: 2026-07-17
- UI follow-up: added the missing collapsed-card summary title and description for Submission Readiness Email Alerts so it matches the other Configuration Management cards. Controls and notification behavior are unchanged.
- Completed: added an opt-in tenant policy, Configuration Management UI, reusable scheduled service, multipart email templates, durable per-recipient notification logs, idempotent management command, and 1:00 AM Asia/Manila cron entry with `flock`. Each tenant run uses one `GradeSubmissionReadinessService.calculate()` snapshot, strict `progress < threshold`, active accepted/unsubmitted assignment filters, and the existing Area Chair/College Dean/CAO tenant-campus-department rules including the Dean-to-Area-Chair supervision chain. Custom `*_AC` codes are supported and multiple applicable roles consolidate into one recipient report.
- Safety/privacy: delivery defaults disabled; empty reports are forcibly suppressed; only public HTTP(S) base URLs are included; dashboard links still require normal authentication/RBAC; email and logs contain no student names, scores, grades, averages, or distributions. The service reserves its recipient/deadline/period/year/term/rule idempotency key before email delivery, tracks processing/attempt state, safely retries failures or stale attempts, and sanitizes backend errors. `--force` bypasses only a successful-send duplicate guard.
- Changed files: `apps/notifications/{models.py,submission_readiness.py,tests_submission_readiness.py}`, migrations `0008` and `0009`, command `send_submission_readiness_alerts`, two email templates, feature/settings form/view/template files, `config/settings/base.py`, `ops/cron/teachermateplus.cron`, Admin guide/test, `docs/SUBMISSION_READINESS_EMAIL_ALERTS.md`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, and this handoff. Pre-existing `logs/system.log` test output was preserved.
- Validation performed: migrations `notifications.0008` and `0009` applied locally; the final combined Notifications, existing Submission Readiness, and complete Admin assignment/configuration class passed 56/56 in 182.862 seconds; the added one-snapshot Dean/CAO performance regression passed again 1/1; `python manage.py check`, `makemigrations --check --dry-run`, empty `migrate --plan`, disabled-policy command dry run, Python compilation, and `git diff --check` passed (line-ending warnings only). The in-app browser connection failed twice with `Cannot redefine property: process`, so no visual browser result is claimed. No full project suite or live SMTP test was run.
- Pending/risks: visually verify the Configuration Management card and controls in a working staging browser; test real SMTP delivery to authorized accounts; confirm production `SITE_URL` is public HTTPS; validate role/email data; install cron under the application user with lock/log permissions; and monitor the first live run. Production-scale query timing should be profiled with real data even though readiness is calculated once per tenant snapshot. No commit or push was performed.
- Exact next steps: review the diff; run a staging dry run; inspect scope-limited output/logs; conduct a controlled send; then enable the tenant policy and scheduler only after approval.

### Admin Portal Grade Submission Readiness Phase 1 stabilization
- Date: 2026-07-17
- Presentation follow-up: faculty-group card headers now use a green-to-yellow gradient with a larger bold white faculty name. This changes presentation only; scope, grouping, filters, calculations, sorting, pagination, and detail authorization are unchanged. The updated focused suite passed 9/9 in 27.429 seconds.
- Follow-up: removed the Faculty filter from the Submission Readiness view and template. The page now always lists every authorized faculty group matching the remaining academic/scope filters; legacy or forged `faculty_user_id` query parameters are ignored. Added a regression proving two scoped faculty remain visible even when that removed parameter is supplied. The updated focused suite passed 9/9 in 25.143 seconds.
- Completed: inspected and stabilized only Phase 1 at `/admin-portal/grading/submission-readiness/`; no Phase 2 checklist, blockers timeline, student detail, grade analytics, performance report, export, or grading mutation was added. The current page groups accepted active assignments by faculty, provides the requested scoped filters and summary cards, sorts by operational priority/deadline, paginates 20 faculty groups, and links to a protected `Coming Soon` detail endpoint.
- Readiness policy: `GradeSubmissionReadinessService` batch-loads grading setup and record coverage, submissions, deadlines, locks, encoding controls, and latest grading timestamps. Progress is `min(template setup coverage, eligible-student record coverage)` and remains zero until a valid score/attendance record exists; submitted periods show 100%. Ready requires eligible active students, at least one record, complete required setup and student records, and no lock/encoding/deadline blocker. Nearly Ready is 90% or above; passed-deadline unsubmitted periods are Overdue; submitted periods are Submitted. Dashboard Overall Readiness is `(Ready + Submitted) / filtered visible assignments`.
- Scope/permission: both list and placeholder detail reuse `faculty_activity_monitor.read` and `AdminScopeService.scoped_faculty_assignments()`. Area Chair, College Dean, CAO, Campus Admin when that existing permission is explicitly granted, and Superadmin therefore retain the established tenant/campus/department/Dean-chain behavior. No new role grant was introduced. Student identities, raw scores, averages, distributions, and performance statistics are not rendered.
- Defects fixed in this stabilization: corrected the batch template-period query from nonexistent `grading_template_id` attributes to the real `template_id`, which otherwise caused a runtime `FieldError`; changed the detail placeholder lookup to safely select an authorized primary assignment when an offering has multiple accepted faculty instead of risking `MultipleObjectsReturned`.
- Files changed in this stabilization: `apps/admin_portal/submission_readiness.py`, `apps/admin_portal/views.py`, new `apps/admin_portal/tests_submission_readiness.py`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, and this handoff. The existing Phase 1 URL, templates, navigation migration `0014`, seed command entry, and reusable grading helper remain part of the current repository implementation. `logs/system.log` contains local test output and is not a product change.
- Validation performed: focused `python manage.py test apps.admin_portal.tests_submission_readiness -v 1` passed 8/8 in 21.344 seconds; combined submission-readiness and existing Admin scope tests passed 47/47 in 94.664 seconds; full `python manage.py test apps.grading -v 1` passed 78/78 in 167.256 seconds. Coverage includes readiness percentages/statuses, overdue/submitted priority, all requested role paths, Area Chair cross-campus isolation, grouping, filtering, permission denial, protected Phase 2 placeholder, privacy, summary formulas, and existing grading behavior. `python manage.py check` passed, `python manage.py makemigrations --check --dry-run` reported no model changes, and `git diff --check` passed with line-ending warnings only. `python manage.py migrate --noinput` applied only `navigation.0014_seed_grade_submission_readiness_menu`; the final migration plan is empty. A local ORM check confirmed the active menu route and existing grants for AC, College Dean, CAO, and Superadmin; Campus Admin remains permission-dependent by design.
- Pending/manual risk: the in-app browser runtime failed twice while connecting (`Cannot redefine property: process`), so no visual desktop/phone smoke is claimed. The request-local template cache avoids repeated resolution for identical offering signatures, while bulk data queries avoid per-assignment grading-record queries; very large deployments should still profile unique-template resolution and page compute time with production-like data. The current Phase 1 computes all filtered rows before faculty-group pagination so summary counts cover the full filtered scope.
- Exact next step: use a working local/staging browser to verify academic-head login, menu visibility, all filters, 20-faculty pagination, status/deadline ordering, desktop/phone table behavior, and cross-campus detail denial before today's orientation. Do not implement Phase 2 and do not commit or push without explicit instruction.

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

## 2026-08-14 — Automatic Generation policy configuration

### Completed
- Added cycle-scoped Automatic policies for campus representation and contributor completion, both Draft-only and included in signed cycle-default confirmation.
- Automatic readiness now uses participating Campus IDs from offering snapshots, emits structured warnings when the configured warning policies permit generation, and continues to block stale rosters, invalid pools, and hard-constraint failures.
- Added deterministic Set B reordering when Automatic selections have the exact same A/B block sequence. Manual ordering remains unchanged.
- Added migration `departmental_exams.0014_automatic_generation_policies`; it has not been applied to the normal local database.

### Changed Files
- `apps/departmental_exams/models.py`
- `apps/departmental_exams/migrations/0014_automatic_generation_policies.py`
- `apps/departmental_exams/forms.py`
- `apps/departmental_exams/views.py`
- `apps/departmental_exams/services.py`
- `apps/departmental_exams/generation_readiness.py`
- `apps/departmental_exams/generation_services.py`
- `apps/departmental_exams/automatic_workflow.py`
- `apps/departmental_exams/tests_automatic_workflow.py`
- `apps/departmental_exams/tests_stage4_authorization.py`
- `apps/departmental_exams/tests_stage6_campus_codes.py`
- `templates/departmental_exams/admin/automatic_generation_summary.html`
- `templates/departmental_exams/admin/cycle_configuration.html`
- `templates/departmental_exams/admin/cycle_defaults_confirm.html`
- `HANDOFF.md`

### Validation Performed
- Passed 7 focused Automatic-policy tests.
- Passed 37 Automatic workflow tests, 10 Stage 6 campus-code tests, 14 Stage 6 generation tests, and 6 signed-confirmation tests.
- Passed `git diff --check`, `python -B manage.py check`, and `python -B manage.py makemigrations --check --dry-run`.
- `python -B manage.py migrate --plan` lists `departmental_exams.0014_automatic_generation_policies` with the two expected AddField operations; it was not applied.

### Known Issues / Risks
- No normal local migration, commit, push, deploy, restart, or manual browser smoke test was performed in this implementation gate.

### Exact Next Steps
1. Inspect the complete diff and final repository status at the next review/commit gate.
2. Obtain a separate authorization before applying the migration, committing, pushing, deploying, restarting, or changing excluded documentation.

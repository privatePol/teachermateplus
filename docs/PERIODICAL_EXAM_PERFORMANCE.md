# Periodical Exam Performance - implementation and focused remediation, 2026-11-03

## Latest rank-comparison remediation
At unchanged HEAD `0fbffd604c901575f0a8d099b55412791e7ab626`, remove the historical cycle activation requirement and compare independent confirmed ranking populations rather than every included snapshot. A section without a complete confirmed result in either period does not veto valid comparisons. A section ranked in only one period still changes the participating population and blocks comparison. Retained history must match tenant, academic year, term, course/offering and legitimate unit; conflicting physical periods with confirmed grades and unrelated units remain unavailable. Submission metadata without confirmed grades is not a participating result. No original grading formula is reconstructed and no saved grades are written.

AE103-MS regressions use four confirmed Fairview sections plus unconfirmed Cubao/Taytay offerings; expected `(Final rank, Midterm rank, movement)` values are `(3,1,-2)`, `(1,2,+1)`, `(1,2,+1)`, `(4,4,0)`. A separate regression makes the Midterm cycle inactive while retaining the same confirmed results. Controls preserve rejection of genuinely different confirmed populations, historical units and ambiguous confirmed history. Once ambiguous records have no finalized grades, their metadata alone cannot block the other sections. These are disposable synthetic records, not a claim of inspecting staging grades. Prior pending Top Performers changes remain preserved. Executed validation is recorded in the latest HANDOFF.md entry.

### Rank-remediation validation
Both required AE103 regressions failed before the fixes. Final focused backend: 71/71 passed (28 Midterm + 40 Periodical + three navigation), including five new AE103 controls; no failures/errors/skips. Frontend 11/11 and JS syntax passed; prior popup source files are byte-preserved. Django check, migration drift check and read-only migration plan passed; diff check passed. Final report/Top detail query counts remain 52/45 at 10, 50 and 100 offerings, matching the review baseline; maximum report SQL remains 16,061 characters. No application database writes or migrations. Full commands, timings, preservation checks and intermediate regression evidence are in HANDOFF.md. Next gate: independent read-only re-review.

## Latest popup correction
On `feat/periodical-exam-performance` at unchanged HEAD `0fbffd604c901575f0a8d099b55412791e7ab626`, the campus popup directly renders student names and saved converted exam grades. A campus with confirmed results does not wait for other campuses; empty or unconfirmed campuses are omitted. The display has no duplicate Overall table or generic ranking paragraph. The existing API still retains its previous fields for compatibility. Script URL version `campus-top10-20261103` refreshes browser asset selection, and the renderer creates its result container if older dialog markup omits it. Backend ranking/source/authorization and saved grades are unchanged.

The supplied screenshot is consistent with an older message-only script or missing result container. Local source inspection cannot establish which assets are actually served on staging; deployment/static asset synchronization and authenticated acceptance remain separate gates. Validation results for the correction are in the latest HANDOFF.md entry.

## Current gate: saved official examination results
Branch `feat/periodical-exam-performance`, unchanged HEAD `44567ce16687a68b254123b3750c3d83c8cc8672`. This gate supersedes the earlier formula-definition holds below. The saved, confirmed examination grade is authoritative; the report never invokes grade recomputation, transmutation, submission, correction, or any grade write.

### Data and reporting rules
- Source: `StudentPeriodGrade.exam_grade` where `is_finalized=True`, for the exact physical period and offering whose `GradeSubmission.status` is `SUBMITTED`. Tenant/campus, current eligible enrollment, examination-unit, supervision and direct-DENY boundaries remain enforced. Zero is valid; absent results stay absent.
- Passed and Passing Rate: use the existing passing-threshold precedence (applicable profile, resolved template, tenant `PASSING_GRADE_THRESHOLD`, existing service default 75). Count saved grades greater than or equal to that configured threshold and divide by the complete eligible roster. No raw-score conversion. Non-finite/out-of-range thresholds disable only pass metrics. The UI explicitly describes current configured policy; `GradeSubmission.template_snapshot_json` does not retain a submission-time threshold and no historical threshold is claimed.
- Campus Top 10: lazy GET-only/no-store endpoint; requires independent `gradebook.view_student_identity` access for every campus in the scoped unit, with explicit DENY preceding superuser shortcuts. Existing grading-context compatibility is retained within each campus. Rank saved exam grades descending using SQL window `RANK`, including every tie at rank 10. Only confirmed results participate, so incomplete offering coverage is stated. Ambiguous duplicate student results are excluded rather than selecting an arbitrary record. Identifying data is absent from aggregate HTML; JavaScript renders identity fields as text.
- Stored raw/max: `StudentActivityScore.raw_score` and `GradeActivity.total_score`, linked by student, offering and exact period through one exam activity whose component is explicitly marked as an exam. Do not substitute `computed_score`, derive raw values, sum multiple activities or guess from titles. Activity/score timestamps must not postdate submission or the later confirmed saved grade. Missing/ambiguous raw inputs do not hide official grades; a reliable maximum remains available separately. The raw/max column is omitted when no reliable activity maximum is available.
- Overall: a single-campus authorized population is the same supported campus comparison. Combining multiple campuses remains unavailable. Exact gap: neither `StudentPeriodGrade` nor `GradeActivity` has a foreign key or retained assessment identifier linking it to `ExaminationCycle`, `ExamGenerationRevision` or an answer-key/questionnaire release. `CycleCourseOffering` links an offering to a cycle/course, but does not establish which assessment revision produced a saved grade. No shared assessment identity is inferred from matching scales, course codes or current configurations.
- Previous Midterm rank: use the unique retained Midterm cycle in the same tenant/year/term, regardless of current activation, matching validated unit membership and exactly the same authorized confirmed ranking population. Sections unconfirmed in both periods do not participate; populations confirmed in only one period remain incompatible. Resolve the unique submitted physical Midterm period from retained `GradeSubmission` relationships independently of current template/profile publication. Aggregate finalized saved exam grades for the complete current eligible cohort. Use the existing two-decimal average and competition ties, then previous rank minus Final rank. A section unconfirmed in both periods does not participate; genuinely different confirmed populations, conflicting confirmed history or changed units remain unavailable. No historical grading configuration is reconstructed. Continuation fingerprints include the derived comparison ranks.
- Hide unavailable-only pass/history columns and identity-ineligible or unsupported Top buttons. Navigation, legacy routes/exports, all current report class ranks, FX aliases and loading behavior remain intact.

### Query design and validation
Full selected-unit aggregate rows still supply current ranks, cursor fingerprints and 50-row display pages. Midterm comparison reuses the authorized offering query and loads submission/roster/grade aggregates in batches. Student detail uses SQL window cutoff before materialization and batch-loads raw scores only on explicit request. No per-student recalculation or database lookup, new index/schema, or cross-request cache.

Validation results for this gate are recorded in HANDOFF.md. Earlier measurements and validation below remain historical evidence and are not current-gate test claims. Local SQLite evidence does not establish MariaDB, staging/browser or production acceptance.

### Current-gate executed results
- Focused backend 65/65 passed (28 Midterm, 34 Periodical, three navigation), no failures/errors/skips; frontend 10/10 and JS syntax passed. Django check passed, no migration drift, and read-only disposable migration plan had no operations. Exact commands and validation settings are in HANDOFF.md. Intermediate fixture authority error was corrected before this final run.
- Final report, 10/50/100 offerings: 52 queries each, largest SQL 16,061 chars each; elapsed samples 186.3/162.3/220.9ms. Lazy Top detail: 45 queries each, 135.5/134.2/155.9ms. These single samples are diagnostic timings, not latency guarantees. The complete single-offering 10/100-student report used 52 queries both times, with three-run medians 130.0/299.7ms.
- Batch history/authorization reuse reduced an intermediate 66-query report to 52; reused campus metadata reduced detail 46 to 45. Earlier 41-query reports had the new grade-dependent metrics disabled and are not equivalent workloads.

## Earlier focused remediation (historical; formula holds superseded above)
Expected/current HEAD `07f45a0da15e47c18a118e3aeef1c982c8617725`, branch `feat/periodical-exam-performance`. The opening 16-path dirty candidate was snapshotted outside Git before edits. No worktree/branch switch or staging occurred.

Both P2 defects were reproduced before the fix: four focused cases failed (FX physical results, multiple Final aliases, profile-change history, multi-member history). The old FX-unavailable assertion was corrected rather than retained as a regression contract. Seven new tests plus updated existing expectations now cover the remediated behavior.

The historical guard is deliberately fail-closed for all retained submissions produced by the current authoritative writer: the retained metadata cannot prove applied formula/base/transmutation/threshold compatibility. The report no longer performs historical ranking/profile queries just to infer compatibility from present-day state. Passed Count, Passing Rate and student Top 10 remain unavailable; no identities, invented formulas or new snapshot schema were introduced.

Within each already paginated course batch, the ORM now evaluates one set of full-unit aggregate rows. That request-local list supplies the exact Decimal averages/competition ranks, fingerprint and display-page selection. Database campus/section collation order is retained via stable sorting. Only the display page hydrates offering/faculty objects. Removed per-rank SQL CASE expansion and repeated aggregate COUNT/SELECT work. Full-unit ranking still costs O(offerings) aggregate memory; no student rows or result objects are materialized, and Load All still renders sequential 50-row batches. No cross-request cache, schema or index change.

### Comparable offering benchmarks
Same synthetic complete Final population, one eligible student per offering, distinct official exam grades, one course/unit, same base/schema/interpreter. One warm-up and three measured `build_report()` runs per size; fixture setup excluded. Before uses the exact saved pre-remediation service loaded in a separate module; after uses the candidate. Final class ranks, total populations and 50-row display limit were asserted on both sides. Eliminating unproven historical comparison is part of the measured correctness/performance change.

| Offerings | Queries before / after | Median ms before / after | Largest SQL chars before / after |
| --- | --- | --- | --- |
| 10 | 73 / 41 | 222.09 / 64.27 | 37,253 / 12,062 |
| 50 | 73 / 41 | 741.90 / 79.53 | 114,253 / 12,062 |
| 100 | 73 / 41 | 3,489.86 / 78.44 | 210,495 / 12,062 |

These are local SQLite microbenchmarks, not MariaDB, production capacity or concurrency acceptance. Existing 200+ offering/tie/pagination regressions also pass. A new regression asserts one full-population aggregate SELECT/configuration resolution and bounded query/SQL growth at 10/50/100 offerings.

### Remediation validation
Evidence: `D:/codex-tmp/periodical-remediation-20261103/` (before snapshots/hashes, reproductions, exact before/after JSON and logs, test/check transcripts, focused diffs). Interpreter remains `D:/codex-worktrees/TMP-Grade-Filters-Performance/.validation-env/Scripts/python.exe -B`. `PYTHONPATH` points to that evidence directory; `DJANGO_LOG_DIR` points to its logs. Test settings use an existing disposable SQLite schema cloned externally; plan settings open it read-only with `mode=ro`. No migrations were added, modified or applied in this gate, including to the test schema.

Executed commands (using the interpreter above):
```text
manage.py test apps.admin_portal.tests_midterm_exam_performance apps.admin_portal.tests_periodical_exam_performance --settings=remediation_settings --keepdb --noinput --verbosity 1
manage.py test benchmark_offerings.OfferingBenchmark.test_measure --settings=remediation_settings --keepdb --noinput --verbosity 1
manage.py check --settings=remediation_plan_settings
manage.py makemigrations --check --dry-run --settings=remediation_plan_settings
manage.py migrate --plan --settings=remediation_plan_settings
node --test frontend/case-editor/midterm-performance.test.mjs
node --check static/js/midterm_exam_performance.js
git diff --check
```
- Final backend: **52/52 passed**, 15.802s, zero failures/errors/skips (28 original Midterm +24 Periodical). Final Python exit code 0. Four pre-fix reproductions failed as expected; intermediate 50-test run also passed before adding alias/bounded-SQL coverage.
- Benchmarks: one before and one after benchmark test passed, each with three population sizes and three measured samples. JSON retains all samples. The reusable benchmark module lives outside the repository; the regression is in the Periodical test module.
- Frontend: **9/9 passed**, zero failures/skips; production JavaScript syntax passed. JS is byte-identical to the opening candidate.
- Django check: zero issues; drift: no changes; read-only migration plan: no planned operations; all command exit codes 0. Diff/AST/status/index and exact scoped change preservation checked. No application warnings observed; PowerShell stderr formatting is not an application failure.
- The three broader-suite baseline failures from the original gate remain documented below and were not remediated or reclassified. No new broad-suite/staging/browser/MariaDB/device acceptance is claimed in this focused gate.

### Files changed in this remediation (9)
`apps/admin_portal/midterm_exam_performance.py`, `apps/admin_portal/tests_periodical_exam_performance.py`, `templates/admin_portal/grading/_midterm_course_groups.html`, `apps/admin_portal/help_guide.py`, `templates/admin_portal/guide.html`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, `HANDOFF.md`, and this report. All seven other paths from the opening candidate are byte-preserved. Total candidate remains 16 dirty paths, index empty.

Next gate: independent read-only re-review of these fixes and the explicit historical N/A boundary. Keep missing grade/provenance requirements blocked. No commit, push, migration application, deployment, restart, protected grading-data mutation or unrelated-worktree edit occurred.

## Candidate
- Worktree: `D:/codex-worktrees/TMP-Periodical-Exam-Performance`.
- Branch: `feat/periodical-exam-performance`.
- Base and unchanged HEAD: `07f45a0da15e47c18a118e3aeef1c982c8617725`.
- Created from the verified clean source `D:/teachermateplus-grade-correction-mainline` on `integration/grade-correction-reminders-mainline`. Existing source/root worktrees were not edited. No commit/push/deployment/service operation.

## Implemented
- Report and Insights link renamed Periodical Exam Performance. Existing URL, route name and service class remain compatible.
- Midterm selection retains the established exact physical `MIDTERM` period. Final resolves verified `FINAL`/`FINAL EXAM`, `FX` and `FINALS` aliases to one active physical period ID. Missing periods are unavailable; multiple matches or contradictory recognized code/name evidence report Ambiguous Final grading period. Inactive alternatives do not conflict. No custom-code substring or sequence fallback is used. The report-local aliases do not change correction-governance policies.
- Cycle-specific INCLUDED/EXEMPT rules remain. DEPTAL uses `CycleCourse.exam_classification == DEPARTMENTAL` in the selected cycle, not current Course ownership.
- Automatic near-bottom incremental loading, sequential bounded Load All, acknowledged row progress, cancellation/resume, deduplication, network retry and stale-generation protection. A Continue loading link supports manual retry and browsers without IntersectionObserver/JavaScript. One in-flight batch; server limits remain five course identities and 50 class rows.
- Historical Midterm rank and movement remain **N/A**. Source inspection of the authoritative `submit_period()` writer confirmed that retained snapshots contain IDs/labels, not applied historical grading rules. Matching current profiles cannot establish compatibility, including when a profile was changed between Midterm and Final or a comparison unit has multiple members. No missing historical configuration is reconstructed. Existing per-period saved class grades, aggregates and competition ranks are unchanged.
- Lazy View Top Performers action and GET-only, no-store endpoint share report scope/feature guards, check student identity permission and explicit direct DENY, and return a safe unavailable reason. No student records are queried or returned.

## Earlier correctness stop (historical; superseded by current saved-grade requirements)
Passed count/rate, student Top 10 per campus/overall, rank-10 tie inclusion, and raw/computed leaderboard pairs remain unavailable. The UI explicitly shows N/A/unavailable. This is the request's stop condition, not a completed leaderboard implementation.

Evidence:
- `StudentActivityScore` records raw/computed values and excusal; `GradeActivity.total_score` is the activity maximum. No activity foreign key binds the result to a selected examination cycle/generated assessment revision across offerings.
- `FacultyGradingService.compute_activity_score()` converts raw scores using configured base (or direct percentage). `build_period_grade_detail_for_student()` combines exam components, rounds the official exam grade to an integer with ROUND_HALF_UP, and applies DepEd transmutation to the weighted **period** grade. Applying that transmutation again to the exam alone would invent a rule. Multiple activities/components, weighting and excusal further prevent treating one arbitrary score as the raw exam.
- `GradeSubmission.template_snapshot_json` written by `submit_period()` records template ID/code/name and period code/name. It does not pin the full transmutation table, effective base, score-input modes or passing threshold. The submission snapshot records component codes/count/time, not an immutable exam-result/ranking population.
- Threshold precedence is profile, template, tenant setting, then default; this is a grade-scale threshold, not a raw-point threshold. Current active assignments/profiles and latest published fallback cannot reconstruct every historical rule.
- A common numeric scale, item count or course code does not prove comparable assessments. Shared generated units have historical exam metadata, but the grading activity is not explicitly linked to that revision.

Next decision: approve the standalone exam-grade definition by formula mode, historical rule/threshold provenance, and assessment identity/equivalence contract. Then design only the necessary persistence/linkage and implement the stopped metrics/rankings, including competition cutoff ties. Do not guess or backfill historical facts. The present class statistics remain the existing official saved exam aggregates, not a new claim of standalone transmutation. Historical rank movement is stopped until authoritative retained compatibility evidence exists.

## Files changed (16)
- `CHANGE_LOG.md`, `HANDOFF.md`, `TEACHERMATEPLUS_CONTEXT.md`, this report.
- `apps/admin_portal/help_guide.py`, `midterm_exam_performance.py`, `tests_midterm_exam_performance.py`, new `tests_periodical_exam_performance.py`, `urls.py`, `views.py`.
- `frontend/case-editor/midterm-performance.test.mjs`, `static/js/midterm_exam_performance.js`.
- `templates/admin_portal/grading/{_midterm_course_groups.html,academic_performance_insights.html,midterm_exam_performance.html}`, `templates/admin_portal/guide.html`.

## Initial implementation validation (historical; latest remediation results above)
External evidence: `D:/codex-tmp/periodical-exam-20261103/`.
Interpreter: `D:/codex-worktrees/TMP-Grade-Filters-Performance/.validation-env/Scripts/python.exe` (`-B`, Django 5.2.12). Default Python lacks Channels; it was not used for Django execution.

Environment for Django commands:
```powershell
$env:PYTHONPATH='D:/codex-tmp/periodical-exam-20261103'
$env:DJANGO_LOG_DIR='D:/codex-tmp/periodical-exam-20261103/logs'
```
`periodical_test_settings` keeps the default connection in memory and TEST points only to external disposable SQLite. `periodical_plan_settings` opens that disposable schema with SQLite URI `mode=ro`. No normal development database is opened by these commands. Only disposable test schema migrations/fixture writes occurred.

Commands (prefix `python` below is the exact interpreter above):
```text
python -B manage.py test apps.admin_portal.tests_midterm_exam_performance apps.admin_portal.tests_periodical_exam_performance --settings=periodical_test_settings --keepdb --noinput --verbosity 1
python -B manage.py test apps.admin_portal.tests_midterm_exam_performance apps.admin_portal.tests_periodical_exam_performance apps.admin_portal.tests_academic_performance_insights apps.admin_portal.tests_help_guide --settings=periodical_test_settings --keepdb --noinput --verbosity 1
python -B manage.py check --settings=periodical_plan_settings
python -B manage.py makemigrations --check --dry-run --settings=periodical_plan_settings
python -B manage.py migrate --plan --settings=periodical_plan_settings
node --check static/js/midterm_exam_performance.js
node --test frontend/case-editor/midterm-performance.test.mjs
node D:/codex-tmp/periodical-exam-20261103/browser-check.cjs
git diff --check
```
Node uses existing JSDOM via `NODE_PATH=D:/codex-worktrees/TMP-Grade-Filters-Performance/.validation-env/dom/node_modules`; no dependency was added.

Results:
- Final focused report backend: **45/45 passed**, 17.161s, zero failures/errors/skips (28 existing + 17 new). Covers period/cycle selection, actual Midterm ties/movement, missing/reopened/incompatible populations, EXEMPT/DEPTAL, exact-period handling, fragment stale invalidation, feature/RBAC/direct-DENY/privacy boundaries, N/A stop condition, no top-detail score query and bounded query growth.
- Broader selection: **94 tests, 91 passed, 3 failed, zero errors/skips**, 32.556s. All three failures reproduced independently on an exact-base `git archive` extracted outside Git worktrees (3 tests, 3 same failures): dashboard Insights navigation expectation, historical Departmental Guide phrase/index expectation, and missing `assignment-acceptance` guide anchor. Those unrelated defects were preserved. The focused report tests are a subset of this broader selection, not additional unique passes.
- Earlier runs: the first 28-test run used the pre-edit column assertion and failed its expected old header count; updated only that presentation assertion. Early new-test runs exposed a Permission fixture field name and an overly exact Cache-Control assertion; both tests were corrected to the model and existing middleware behavior. No production workaround or blanket skip was introduced.
- JavaScript **9/9 passed**, zero failures/skips: auto loading, one in-flight request, sequential incremental rendering, progress, cancel/resume, stale filters/BFCache, retry/restart, lazy dialog escaping/stale close.
- Chrome: responsive layout and native dialog checks passed at **375/768/992/1440px** (four viewport cases); eight PNGs exported, desktop report and mobile dialog visually inspected. Uses Django-rendered synthetic fixture, real local Bootstrap and production JS, with a stubbed unavailable detail response. Missing fixture logo/menu assets are not product findings. This is offline rendering, not authenticated live browser/staging acceptance.
- Django check: zero issues. Migration drift: no changes. Read-only plan: no planned operations in the disposable test schema. `git diff --check`, Node syntax and full scoped diff/status inventory inspected. No Django/application warnings observed; PowerShell stderr wrapping and Git LF/CRLF notices are tooling output.

## Initial implementation performance and index evidence (historical)
- Final complete report: **73 queries for both 10 and 100 students**; three-run medians **164.0ms / 341.7ms** on local SQLite synthetic fixtures. This bounds query growth, not runtime/memory as data grows. Separate 0/12-student regression: 73/73 queries. Existing 6/24 distinct-context unit regression: 116/108 queries; no per-context growth.
- Thin course catalog is paginated before grade aggregation; selected-unit grade histograms preserve stable ranks. Final comparison reads batched aggregate offering identities, not student result objects. Top detail is not executed during initial rendering. Permission/supervision and unit resolution still have a meaningful fixed query cost; no production throughput claim.
- Read-only representative lookup plans used `idx_gradeact_off_period`, `idx_scores_activity_active`, `idx_enroll_offering_status`, `idx_de_cycle_course_campus`, `idx_period_grade_period` and the submission offering/period unique index. `index-plans.json` records them. These are simple lookup plans, not complete report SQL/MariaDB EXPLAIN.
- No speculative indexes or migrations. No MariaDB/InnoDB, concurrency capacity, authenticated staging, production, or physical-device acceptance. Unrelated Admin CRUD and Faculty grading smoke flows were not run because those workflows were not changed; existing grading write/lifecycle code is untouched.

## Next gate
Independent read-only review of this candidate, including the explicit stopped requirements and three baseline regressions. Resolve the grading/history/comparability contracts before enabling pass statistics or student leaderboards. Keep commit/push/non-test migration application/deployment/restart separately authorized. No such actions occurred.

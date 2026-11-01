# Admin Dashboard P1/P2 remediation and validation

Session: TMP-ADMIN-DASHBOARD-LAYOUT-IMPLEMENT-01, existing session; 2026-11-01 (Asia/Manila).
Gate: targeted security/performance remediation complete; **await independent read-only re-review**. This section supersedes the original report below for CML campus authorization, query/latency evidence and current inventory. Earlier unresolved items remain open.

## Verified state and boundary

- Worktree: `D:/tmp_branches/tmp-admin-dashboard-layout`.
- Branch: `feat/admin-dashboard-layout-feedback`.
- Full unchanged HEAD: `b7a68a26bc754feb49c35d41990606d6568a4167`.
- Opening `git status --short`, branch, log and full SHA were checked before edits. All 14 opening dirty/untracked paths were copied and hashed externally before remediation. The opening candidate remains uncommitted; this gate adds modifications to two previously clean tracked paths, for **16 total candidate paths**.
- No staging, commit, push, merge, non-test migration application, deployment, service restart, destructive Git operation or unrelated worktree change.

## P1 - selected-campus CML authorization

Root cause: the original CML selector can legitimately span accessible campuses for a tenant-wide role, while the existing view permission check uses only the current selected campus. Applying active AY/term alone did not make the record scope match that permission decision. A tenant-wide two-campus fixture reproduced **count 2 instead of 1** with Campus A selected, including when B had a direct view DENY.

`selected_campus_cml_requests` now intersects the existing AdminScopeService CML queryset with the selected tenant and campus, validates selection membership in request scope and requires matching offering tenant/campus. Existing department/program/ownership restrictions remain in the base selector. Dashboard count, listing and the review route's record lookup use this intersection. No shared ScopeService/RBAC behavior or base CML selector was broadened or rewritten.

- Existing effective `class_list_change_requests.view` still gates dashboard/list access for the selected campus. Existing `class_list_change_requests.review` independently gates review. No listing-to-approval grant; ordinary-user direct DENY and current superuser semantics remain.
- View CML Requests retains `scope_tenant_id` and `scope_campus_id` together with `academic_scope=active&status=PENDING`. Following an older link restores that authorized selection through the ordinary middleware and rechecks permission. Permission revocation returns 403.
- Page-level `campus_id` is an additional intersection, never an authorization source. Changed/removed academic, term, status or campus filters cannot expand beyond the selected authorized campus. Invalid/oversized campus IDs return no rows. A forged top-bar selection remains subject to the existing scope resolver and permission decorators.
- The CML campus dropdown offers only the selected campus. To view another legitimate campus, select it in the existing top scope bar; permitted A and B selections each produce their own exact request count/list. A B direct DENY blocks B selection's listing even though A remains usable.
- A cross-campus guessed review ID returns 404 while A is selected, for GET and POST; no request status changes. Selecting a campus with a direct review DENY returns 403. Existing approval/rejection business rules remain unchanged.
- Counts remain distinct PENDING request headers in the selected campus and active AY/term, never student/item counts. Missing academic configuration retains the existing unavailable/empty-active-list behavior.

## P2 - request-local monitoring scope reuse

Root cause: reopen and corrections each independently called `scoped_monitoring_course_offerings`, repeating the same faculty/supervision/scope-resolution work. The before regression recorded **two resolver calls instead of one**.

`monitoring_offering_ids` retains the exact lazy, authorized offering-ID subquery on the current request. Its key includes user ID, selected tenant/campus and accessible tenant/campus/department IDs. A changed selection on the same request causes a fresh resolution; a new HTTP request cannot reuse it. No global/cross-request cache, added permission query or request/offering listing materialization.

The two existing request selectors accept an optional internal `monitoring_offering_ids` argument. If omitted they execute their original resolver, with the same faculty supervision, active assignment, campus, department, tenant, related/prefetch and visibility behavior. Dashboard reopen and corrections pass the same resolved subquery. Unauthorized cards do not resolve it. Shared scope and permission services are untouched.

## Executed validation

| Check | Actual result |
|---|---|
| Before-fix confirmed regressions | Three tests failed as expected: A count 2, B-DENY count 2, monitoring resolver called twice |
| Final focused security/dashboard/scope/workflow tests | **71/71 passed**, 10.190s; zero failures/errors/skips |
| Broader Accounts/Faculty/Admin scope/CML/reopen/users/guide/core permissions/timeout/correction tests | **626 total: 619 passed, one baseline-equivalent failure, six MariaDB-only skips**, 109.046s |
| Unchanged-baseline guide reproduction | 20 tests: 19 passed, identical one failure, 1.824s |
| Current guide after final text update | 20 tests: 19 passed, identical one failure, 2.394s |
| JS/Chrome responsive and polling/drawer tests | **28/28 passed**, zero failures/skips; 16.842s |
| Django check | No issues |
| `makemigrations --check --dry-run` | No changes detected |
| `migrate --plan` on read-only cloned disposable schema | No planned migration operations; no application |
| Whitespace/full diff/new-file review | `git diff --check`, tracked diff/stat and both untracked files reviewed |

The layout module now has **30 tests**, including 11 added in this gate: two-campus count/list parity, B campus DENY, forged/removed/invalid/oversized filters, legitimate top-bar selection, scope-bound links across changed selection, permission revocation, missing/unauthorized selection, review GET/POST isolation, one monitoring resolution per request, no cross-request reuse, changed-selection re-resolution and permission-gated resolution (some tests cover multiple cases).

The older CML test that expected a combined superadmin listing was deliberately replaced with a stronger selected-campus contract: each authorized campus can be selected, and the actual returned request IDs contain exactly that campus's request. The current superuser RBAC shortcut remains covered by the existing regression. The shared base CML selector remains unchanged and its existing workflow tests pass.

The one broader/guide failure is unchanged `AdminHelpGuideTests.test_departmental_exam_configurer_receives_stage41_help`, line 172, expecting 'blocks Exempt only' at a pre-existing positional guide entry. The exact baseline fails identically; no Departmental Exam guide assertion/content was changed. Six skips are the existing MariaDB/InnoDB-only `MariaDBTrackingConcurrencyTests`. Do not add overlapping suite counts.

Diagnostic provenance: the first regression attempt had a missing-email fixture setup error; it was corrected before the confirmed three-failure reproduction. The first 67-test post-fix run exposed the obsolete combined-campus superadmin expectation; the replacement tests selected-campus isolation plus legitimate A/B selection. Final focused and broader runs retain the passing security/workflow evidence. All preliminary logs are separate from final results.

Chrome **156.0.8078.74** tested actual populated Django-rendered authorized/denied dashboard fixtures at **375, 390, 440, 767, 768, 992, 1280, 1440px**, preserving row/card proportions/order, pending links, menus, table scrolling, keyboard focus/Tab/Escape/Close/backdrop and desktop alignment. Representative desktop/mobile screenshots were visually inspected. This gate does not change the dashboard template, polling/drawer scripts or responsive harness. Offline dashboard renders do not establish live listing CRUD, Bootstrap group-collapse/network, physical device or deployed-server acceptance.

## Comparable query count and GET timings

Three sources were measured sequentially with the **same external benchmark**, interpreter, disposable schema clones and synthetic tenant/faculty/department/active-term/RBAC fixture: exact baseline SHA, the opening uncommitted candidate, and the remediated candidate. Two warmup GETs precede seven measured GETs per volume. Setup/force-login are outside timing. Full Django test-client GET includes middleware, templates and SQL capture. Python 3.14.3, Django 5.2.12, SQLite 3.50.4, MD5 test hasher; no login timing claim. Browser/broader-test runs did not overlap these measurements.

| Pending applications per queue (plus equal approved corrections) | Baseline queries | Before remediation | After remediation | Baseline median ms | Before median ms | After median ms |
|---:|---:|---:|---:|---:|---:|---:|
| 0 | 84 | 96 | 86 | 98.151 | 116.546 | 108.099 |
| 100 | 84 | 96 | 86 | 94.564 | 116.662 | 105.754 |
| 600 | 84 | 96 | 86 | 96.424 | 121.297 | 110.873 |

**Ten redundant queries are removed** at every tested volume. The remaining two over baseline are the two pending request counts. After medians are 8.447/10.908/10.424ms lower than before remediation and 9.948/11.190/14.449ms above baseline. Approximately 86 queries is this fixture's actual result, not an unconditional threshold across different roles/configurations. These samples do not establish production capacity, a universal speedup or a latency guarantee.

Baseline selectors/permissions/fixture/settings were verified against `git show b7a68a26bc754feb49c35d41990606d6568a4167` with Windows line endings normalized. The opening source was reconstructed from the exact baseline archive plus the pre-edit 14-path opening snapshot. All revisions used clones of the same disposable schema; no persistent application database was opened/migrated.

## Exact changed paths and preserved work

**11 incremental remediation paths:**

1. `apps/admin_portal/dashboard.py` - selected-campus CML guard, scope-bound link and request-local monitoring scope.
2. `apps/admin_portal/services.py` - optional already-resolved monitoring IDs on the two existing request selectors; unchanged defaults.
3. `apps/admin_portal/views.py` - guarded CML list/review lookup, safe narrowing filter and selected-campus dropdown data.
4. `apps/admin_portal/tests_dashboard_layout.py` - 11 new regressions and multi-campus fixture.
5. `apps/admin_portal/tests_class_list_change_requests.py` - verify both authorized campus selections without combining records.
6. `templates/admin_portal/enrollment/class_list_change_request_list.html` - selected-campus filter/help text.
7. `apps/admin_portal/help_guide.py` - explain campus selection, scope-bound links and reauthorization.
8. `CHANGE_LOG.md`.
9. `TEACHERMATEPLUS_CONTEXT.md`.
10. `HANDOFF.md`.
11. `docs/ADMIN_DASHBOARD_LAYOUT_IMPLEMENTATION.md` - current evidence, retaining historical report below.

**Five preserved opening paths** remain byte-for-byte unchanged from the pre-remediation snapshot: `apps/admin_portal/tests_dashboard.py`, `frontend/admin-dashboard/README.md`, `frontend/admin-dashboard/responsive.test.cjs`, `templates/admin_portal/dashboard.html`, `templates/admin_portal/grading/correction_request_list.html`.

Thus the full uncommitted package has **16 paths: 14 modified tracked files and two untracked files** (`tests_dashboard_layout.py` and this report). Index empty; branch/full HEAD unchanged. No models, migrations, shared ScopeService/PermissionService, Faculty sources, session registry/middleware, auditing, governance rules, routes or production JS changed in this gate. The approved PDF arrangement, pending-only correction/CML-header summaries, quick-access buttons, removed Quick Actions and visible-tab 600-second reopen-only AJAX remain intact.

## Evidence and next gate

Evidence: `C:/Users/Lenovo/AppData/Local/Temp/tmp-admin-dashboard-layout-remediation-20261101`.

- Opening state: `opening-status.txt`, `opening-manifest.json`, `opening-source/`, reconstructed `before-source/`.
- Security/tests: `before-confirmed.log`, `focused-verified.log`, `broader.log`, `baseline-guide.log`, `guide-final.log`, `chrome.log`, `chrome-version.json`, authorized/denied JSON and top/bottom screenshots.
- Performance: `baseline-performance.json`, `before-performance.json`, `after-performance.json` and matching logs; common source `layout_benchmark.py`.
- Checks/settings: `check.log`, `makemigrations.log`, `migration-plan.log`, `layout_validation_settings.py`, `layout_plan_settings.py` and isolated test DBs.
- Interpreter: `D:/codex-worktrees/TMP-Grade-Filters-Performance/.validation-env/Scripts/python.exe`.

Reproduction: set PYTHONPATH to the evidence directory and LAYOUT_TEST_DB to a fresh pristine.sqlite3 clone; run `manage.py test` with `--settings=layout_validation_settings --keepdb --noinput`. Focused labels are dashboard, dashboard_layout, class_list_change_requests and reopen_requests modules in apps.admin_portal. Broader labels match the original report below. Use TMP_ADMIN_EVIDENCE when exporting focused fixtures; run the unchanged frontend/admin-dashboard test commands. Compare all three source directories using the identical external layout_benchmark module and separate cloned test DBs. Plan-only settings open the disposable schema read-only.

Remaining risks/open evidence: the baseline-equivalent guide assertion, six MariaDB/InnoDB skips, local-only timings, no live authenticated Admin/Faculty browser workflow, device/network, MariaDB plans/locks, staging or production acceptance. Prior unresolved handoff items remain open. Exact next step: independent read-only re-review of the 11 incremental paths and preserved 16-path package against the full expected SHA. **Stop here; no commit, push, merge, deployment, service restart or non-test migration is authorized.**

---

# Original layout implementation evidence (historical; superseded above)

# Admin Dashboard layout implementation and focused validation

Session: TMP-ADMIN-DASHBOARD-LAYOUT-IMPLEMENT-01. Date: 2026-11-01 (Asia/Manila).
Gate: implementation complete; await independent read-only review. No commit, staging, push, deployment, service restart, destructive Git operation or non-test migration application.

## Repository and authorization

- Worktree: `D:/tmp_branches/tmp-admin-dashboard-layout`.
- Branch: `feat/admin-dashboard-layout-feedback`.
- Full unchanged HEAD / requested baseline: `b7a68a26bc754feb49c35d41990606d6568a4167`.
- Before creation, local `origin/main` and live `git ls-remote origin refs/heads/main` both matched that SHA. The new worktree was clean. Final remote verification still matches.
- Source `D:/teachermateplus` was on `feat/departmental-exam-builder-stage5-8`, HEAD `56a370bea4eda9250f0dd55b8e058d208182a6e1`, with unrelated dirty work. Local `main` was older at `56a19668005132b42300eb1ba351e7594f36ce0f`; its existing worktree was clean. Neither was updated. The candidate was created directly from the verified remote-main commit, preserving all existing worktrees.
- The supplied one-page `C:/Users/Lenovo/Downloads/TMP_ADMIN_DASHBOARD.pdf` was rendered and visually inspected as layout reference; the user's message and subsequent design decision define the implementation scope.

## Resulting layout

1. Dashboard header.
2. Three equal summary slots: Tenants / Campus / Users (Users omitted without its existing permission).
3. Active Academic Scope / Currently Logged-in Users / Important Workflow / Operations Reminder. Desktop column proportions are 1 / 1.07 / 1.95 / 1.65, with aligned top/bottom edges; unauthorized logged-in content is omitted.
4. Left: Grade Correction Queue above CML Change Request. Center: Gradebook Reopen Requests. Right: Automatic Period Lock Monitor. Desktop proportions are 1.1 / 1.05 / .9, with monitor tops aligned. Unauthorized cards are omitted, with authorized desktop slots preserved.

At >=1200px the context row has four columns and the lower row three. At 768-1199px the context has two columns. Below 768px it stacks; the lower row stacks below 1200px. Summary slots remain equal. Tables retain internal horizontal scrolling. The complete Quick Actions card is removed. No Audit Logs card, Governance Alerts table, import statistics or Stage 0.75 was added. Underlying routes and permission-filtered sidebar features remain.

## Exact queue semantics and security

The user explicitly refined corrections to a two-level approach: display **Pending Grade Corrections** immediately; do not load or validate approved requests on initial dashboard rendering. This supersedes the original combined pending/active-approved summary requirement. No active-approved count or new on-demand endpoint is introduced; other statuses remain accessible through the existing authorized listing. The existing authoritative validator remains untouched.

- Corrections: `AdminScopeService.scoped_grade_correction_requests`, status PENDING, distinct request IDs; requires exact effective `corrections.read`.
- CML: `AdminScopeService.scoped_class_list_change_requests`, status PENDING, distinct request IDs; requires exact effective `class_list_change_requests.view`. Items/students never inflate counts.
- Both counts filter offering academic year AND term using the selected tenant's configured active academic scope. Both View buttons carry `academic_scope=active&status=PENDING` and use the same shared active-scope filter in their existing destination.
- Missing tenant/year/term configuration displays **Unavailable**, not a misleading zero or historical count. Active-scope destinations then return no requests.
- Existing general listings retain historical statuses/scopes. Both expose an active-scope selector; CML additionally exposes an explicit term selector, retained through pagination.
- Effective permission resolution retains ordinary-user direct DENY and the current Django-superuser shortcut. Listing access grants no review authority. Existing faculty supervision, tenant/campus/department/program scope, reviewer routes and review workflow are retained through the original selectors/decorators.
- Two SQL counts only: no correction/CML request-row, item, attachment, window or approval-history materialization during the dashboard GET. No new polling, schema or duplicated correction-governance logic.

Session monitoring, registry reconciliation, live-session expiry semantics, visible-tab reopen-only AJAX every 600 seconds, no full-page auto-refresh, grading periods/deadlines, audit/logging and all Faculty sources remain in the baseline.

## Executed validation

| Validation | Actual result |
|---|---|
| `git diff --check` | Passed; final full diff inspected |
| `manage.py check` | No issues |
| `makemigrations --check --dry-run` | No changes detected |
| `migrate --plan` with disposable in-memory settings | Completed; naturally lists baseline migrations for an empty DB; none applied |
| `migrate --plan` against read-only cloned disposable schema | No planned migration operations |
| Focused dashboard + layout Django tests | **40/40 passed**, 12.618s, no failures/errors/skips |
| Broader tests | **626 total: 619 passed, one baseline-equivalent failure, six MariaDB-only skips**, 150.076s |
| Baseline help-guide reproduction | 20 tests: 19 passed, identical one failure, 1.896s |
| DOM/Chrome | **28/28 passed**, no failures/skips; 12 polling/drawer checks and 16 real Chrome layout variants |
| Before/after benchmark | One test per source revision passed; 27 full GETs per revision, two warmups then seven measured samples per volume |

The 19 new layout tests cover statuses, distinct request counting despite multiple items, approved open/expired/future/consumed/inactive window exclusion without validation or writes, SQL-only counts, active year/term and paginated count/list parity, missing/invalid configuration, filtered links, retained historical statuses, listing-vs-review permissions, direct DENY, current superuser semantics, faculty supervision, program/department scope, cross-tenant/campus isolation and real authorized/denied browser fixtures. Existing tests cover session reconciliation, reopen fragment authorization/scope/idle deadlines and the broader Faculty grading lifecycle.

Broader labels: `apps.accounts`, `apps.faculty_portal`, `apps.admin_portal.tests_scope`, `tests_reopen_requests`, `tests_class_list_change_requests`, `tests_help_guide`, `tests_users`, `apps.core.tests_permissions`, `tests_session_timeout`, and `apps.grading.tests.CorrectionWorkflowTests` (Admin suffixes refer to `apps.admin_portal`). The sole failure is `AdminHelpGuideTests.test_departmental_exam_configurer_receives_stage41_help`, line 172: a pre-existing positional assertion expects 'blocks Exempt only' in `check_first[1]`, which contains the member-campus generation-permission explanation. The exact untouched baseline fails identically. No assertion or unrelated guide section was changed. Six skips are `MariaDBTrackingConcurrencyTests`: SQLite cannot prove MariaDB/InnoDB row locks.

A preliminary extended-focused run had one test-fixture uniqueness error when a program-scope fixture reused the same offering unique key. A separate synthetic section corrected that fixture; the final 40-test run passes. Earlier focused 37-test and Chrome runs also passed. Diagnostic logs remain separate from final evidence.

Chrome **156.0.8078.74** used actual Django-rendered populated authorized and denied fixtures with local Bootstrap 5.3.3 and logo assets. Widths: **375, 390, 440, 767, 768, 992, 1280, 1440px**. Checks include PDF card order/proportions, equal summary widths, row alignment, unauthorized omission/menus, filtered pending links, text/page overflow, internal table scrolling, drawer focus/Tab/Escape/backdrop/Close and desktop sidebar alignment. Top/bottom viewport screenshots were retained and representative desktop/mobile renders visually inspected. Offline fixtures omit external Bootstrap JS and do not establish live network/group-collapse or physical-device acceptance. A full-document CDP capture produced an inconsistent capture layout; final evidence uses ordinary viewport screenshots plus bottom scroll captures and explicit desktop alignment assertions.

## Comparable query and GET latency evidence

Exact baseline source was exported with `git archive` from `b7a68a26bc754feb49c35d41990606d6568a4167`, outside every worktree. Same interpreter, Python 3.14.3 / Django 5.2.12 / SQLite 3.50.4, identical synthetic faculty/department/active-term/RBAC fixture and disposable schema clones, full Django test-client GET including middleware/templates/SQL capture. Force-login/setup are outside timing. Test hashing is MD5; login/hash performance is not measured. Each queue has the volume below; approved correction rows grow by the same volume but are not validated/read as a listing. Two warmups precede seven measured GETs per volume. Final baseline then candidate runs were sequential, without overlapping browser/test runs. An earlier concurrent-browser diagnostic benchmark is excluded.

| Pending requests per queue (plus same number approved corrections) | Baseline SQL | Candidate SQL | Baseline warm median GET ms | Candidate warm median GET ms |
|---:|---:|---:|---:|---:|
| 0 | 84 | 96 | 162.104 | 216.537 |
| 100 | 84 | 96 | 156.186 | 218.403 |
| 600 | 84 | 96 | 169.336 | 203.520 |

There are **12 added queries** in this scoped fixture, including both counts and established scope resolution. Query count remains fixed across tested volumes. Candidate median GETs are **34.184-62.217ms slower** here. This is a measured cost, not a speedup or production latency/capacity certification. No acceptance threshold was supplied; independent review should assess this fixed cost. No deployed-server, network, MariaDB plan, Redis, staging or production measurements were performed.

## Exact changed files (14; all unstaged)

1. `apps/admin_portal/dashboard.py` - shared active-term filter and permission-gated SQL pending counts.
2. `apps/admin_portal/views.py` - queue context and matching listing filters.
3. `templates/admin_portal/dashboard.html` - requested layout/queues and Quick Actions removal.
4. `templates/admin_portal/enrollment/class_list_change_request_list.html` - academic/term selectors and explanation.
5. `templates/admin_portal/grading/correction_request_list.html` - active-scope selector and status explanation.
6. `apps/admin_portal/tests_dashboard.py` - removed Quick Actions/governance shortcut expectation.
7. `apps/admin_portal/tests_dashboard_layout.py` - new 19-test regression module and browser fixtures.
8. `frontend/admin-dashboard/responsive.test.cjs` - eight widths, queue/layout checks and screenshot evidence.
9. `frontend/admin-dashboard/README.md` - fixture-generation and responsive-test instructions.
10. `apps/admin_portal/help_guide.py` - affected Admin Dashboard guide.
11. `CHANGE_LOG.md`.
12. `TEACHERMATEPLUS_CONTEXT.md`.
13. `HANDOFF.md`.
14. `docs/ADMIN_DASHBOARD_LAYOUT_IMPLEMENTATION.md` - this report.

No model/migration, RBAC, session middleware/registry, grading service, Faculty source, sidebar script/base or URL change. Index empty; branch/HEAD unchanged. `git diff --stat` covers the 12 modified tracked paths; the new test/report are additionally untracked and included in this inventory. Full tracked diff and both new files were reviewed.

## Evidence and reproduction

Evidence directory: `C:/Users/Lenovo/AppData/Local/Temp/tmp-admin-dashboard-layout-20261101`.

- Final: `focused-verified.log`, `broader.log`, `baseline-guide.log`, `chrome-verified.log`, `chrome-version.json`, all eight authorized/denied JSON/screenshots, `check.log`, `makemigrations.log`, `migration-plan.log`, `cloned-migration-plan.log`.
- Performance: `baseline-final-performance.json`, `candidate-final-performance.json` and matching logs; `layout_benchmark.py` retains the common fixture/timing source.
- Source/settings: `baseline.zip`, `baseline-source/`, `layout_validation_settings.py`, `layout_plan_settings.py`, `pristine.sqlite3` and isolated test DBs. The pristine schema was cloned read-only from a prior disposable validation DB; current migration filenames were compared against all application ledger rows with no missing/extra application migration. No application/persistent local database was opened or migrated.
- Interpreter: `D:/codex-worktrees/TMP-Grade-Filters-Performance/.validation-env/Scripts/python.exe`.

For normal disposable in-memory runs use `--settings=duplicate_test_settings --noinput`. For preserved test-clone runs, set `PYTHONPATH` to the evidence directory, set `LAYOUT_TEST_DB` to a fresh clone of `pristine.sqlite3`, and use `--settings=layout_validation_settings --keepdb --noinput`. To regenerate browser fixtures set `TMP_ADMIN_EVIDENCE` to that directory while running both focused modules, then follow `frontend/admin-dashboard/README.md`. Benchmark both revisions with the identical external `layout_benchmark` module, separate schema clones and `LAYOUT_PHASE` values. No persistent DB migration command is needed.

## Pending and exact next gate

Independent read-only review of these exact 14 uncommitted paths against the full baseline SHA, including pending-only design refinement, scope/list parity, absence of initial approved validation, PDF layout and measured fixed performance cost. Retain the baseline-equivalent guide failure and six MariaDB skips as open evidence. Live authenticated Admin CRUD/Faculty grading browser smoke, real-device/network/Bootstrap-group interaction, MariaDB/InnoDB and staging/production acceptance were not performed. Previous unresolved handoff entries remain open. Do not commit, push, deploy, restart services or apply non-test migrations in this gate.

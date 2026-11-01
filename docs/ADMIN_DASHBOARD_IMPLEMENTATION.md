# Admin Dashboard implementation and local validation

Gate: **targeted registry-gap remediation validated; await independent re-review**. No commit, push, merge, non-test migration, deployment or service restart. The following remediation record supersedes the original implementation's registry-completeness limitation, 76-query steady dashboard measurement and 23-path inventory. Original UI/browser evidence below is historical; those checks were not rerun in this backend-only gate.

## Registry-gap remediation: exact fix and boundary

Verified target: `D:/tmp_branches/tmp-admin-dashboard`, branch `feat/admin-dashboard-responsive`, unchanged full HEAD `acbc746f1c969edc4a94d9f4ffd61454349ed8b8`. Opening status, branch, last commit and full SHA were checked before remediation. The 23-path existing package was preserved; the new regression file makes 24 unstaged paths. Index remains empty. No other worktree was edited.

Root cause: `SessionTimeoutMiddleware` treated initialization of its private current-key marker as evidence of registration. Valid inherited sessions with no `ActivePortalSession` row could remain invisible indefinitely, including already-rotated Django Admin password-change sessions. A matching marker therefore cannot establish registry completeness.

`ActivePortalSessionService.reconcile_authenticated_session` now checks the current authenticated active user against the loaded session's user ID, configured backend and constant-time auth hash, then performs one indexed session-key/user ownership existence lookup. Missing registration enters an atomic repair: lock/recheck the persisted user's active/password state, lock/check the backing session's actual expiry, and `get_or_create` the unique session-key row. It refuses ownership transfer. Concurrent initializers reuse the winner. SQLite read-to-write upgrade contention retries the entire rolled-back repair at most twice with 10ms/20ms backoffs; other errors and exhausted retries propagate. Backend lock waits are separate from those backoffs. Healthy requests never enter the lock/retry path or update a registry timestamp.

The timeout middleware reconciles before rendering, and after a view changes the current authenticated session key. This covers inherited and in-request password-change rotations without calling the login registration path. Reconciliation never saves/rotates the session, changes expiry, invokes single-device revocation, decodes other session payloads or changes audit/authentication behavior. Existing timeout rules still apply: interactive sliding timeout and background reopen-card absolute idle deadline. Login registration and its policy enforcement remain authoritative. Direct DENY, ownership, selected tenant/campus and department restrictions remain in the shared SQL selector.

Repair is **lazy on each session's next authenticated request**; dormant inherited sessions are not eagerly backfilled. No global scan, new index/schema or migration is needed. Manually tampered registry/payload data and unsupported raw credential updates remain outside the supported lifecycle. SQLite execution does not establish MariaDB/InnoDB lock acceptance or production capacity.

Exactly nine incremental changed paths (eight existing package paths plus one new test):

- `apps/accounts/services.py`: identity-aware, atomic, bounded-contention reconciliation.
- `apps/core/middleware.py`: before-render reconciliation and current-user/key post-response repair.
- `apps/accounts/tests_session_registry_reconciliation.py`: new 15-test regression module.
- `apps/admin_portal/tests_dashboard.py`: >500-users test initializes its valid viewer and expects that additional user in both count and listing (506 rather than 505).
- `apps/admin_portal/help_guide.py`: explains next-request reconciliation of existing sessions.
- `CHANGE_LOG.md`
- `TEACHERMATEPLUS_CONTEXT.md`
- `HANDOFF.md`
- `docs/ADMIN_DASHBOARD_IMPLEMENTATION.md`

No incremental model, migration, template, static, frontend, grading-policy or Faculty source change.

## Remediation validation

| Executed check | Result |
|---|---|
| Focused new reconciliation + dashboard + active sessions + timeout | **52/52 passed**, zero failures/errors/skips, 6.575s |
| Broader all Accounts/Faculty, Admin dashboard/users/scope/reopen/governance/academic banner, core permissions/timeout | **517 total: 511 passed, six existing MariaDB-only skips**, zero failures/errors, 95.557s |
| Forced two-connection SQLite initialization with both seeing the same missing row | Passed; one row, correct owner and markers for both requests; included above |
| Before-fix targeted reproduction | 12 total: seven failures, two errors from missing registrations, two passes and one then-skipped concurrency case; retained as diagnostic evidence |
| Before-fix inherited-session probe | Four successive dashboard counts remained zero; no registration write |
| After-fix inherited-session probe | First and subsequent counts one; first request one INSERT, later requests zero registry writes |

Suites overlap; do not add totals. The 15 new tests cover missing baseline rows and prematurely matching markers, inherited password rotation, first/second counts, repeated no-write refresh, exact background idle deadline, duplicate sessions/distinct user parity, expired/invalid/logged-out exclusion, wrong identity/hash/owner, deleted backing row, fresh password/deactivation races, interleavings, real two-connection initialization and tenant/campus/department exclusion. Existing suites retain direct-DENY, single-device, in-request Django Admin password change, timeout, OTP/lockout and count/list coverage.

Preliminary failures are retained honestly: an initial broader run reused a disposable schema whose seeded permissions had been flushed by a TransactionTestCase; restoring the pristine disposable snapshot corrected that harness issue without changing RBAC/fixtures. A forced SQLite race separately reproduced `database is locked`; the bounded whole-transaction retry fixes it and the final focused/broader runs execute that race without skipping it. The earlier unrelated Departmental Exam guide positional assertion remains baseline-equivalent and unresolved; its original reproduction below was not rerun or weakened in this gate.

## Remediation performance evidence

Comparison is against an exact source snapshot of the entire pre-remediation candidate (including its 23 uncommitted paths), not HEAD alone. Same interpreter, synthetic fixtures and identically cloned disposable SQLite schemas; 0/100/600 additional registered sessions; four samples per volume, warm medians exclude the first. MD5 is only a test-hasher isolation probe; product hashing is unchanged.

| Sessions | MD5 login POST ms before / after | MD5 dashboard GET ms before / after | GET SQL before / after |
|---:|---:|---:|---:|
| 0 | 18.59 / 17.51 | 94.97 / 81.93 | 76 / 77 |
| 100 | 21.17 / 17.63 | 96.44 / 85.06 | 76 / 77 |
| 600 | 22.15 / 18.31 | 101.74 / 87.26 | 76 / 77 |

| Sessions | Normal PBKDF2 login POST ms before / after | Normal PBKDF2 dashboard GET ms before / after |
|---:|---:|---:|
| 0 | 809.57 / 1496.12 | 88.91 / 139.99 |
| 100 | 807.51 / 1499.71 | 82.43 / 148.51 |
| 600 | 836.67 / 1083.73 | 111.35 / 107.83 |

Warm login POST queries remain **46 -> 46** (first POST 43 in both phases), one redirect each. Dashboard adds exactly **one indexed registry verification read**, constant at all tested session volumes. Fifty direct healthy reconciliation calls with an already-loaded session measured **median 0.4461ms, max 0.6495ms, one query each, zero registry writes**, unchanged registry timestamp. Initial missing-row repair measured **3.1209ms/nine SQL statements**, including transaction/savepoint bookkeeping.

The inherited-session MD5 probe changes first-render count **0 -> 1**, with 69 -> 78 queries on initial repair and 69 -> 70 thereafter. First repaired GET 69.18ms; subsequent 63.97/63.78/63.56ms versus 82.32/83.02/83.68/83.82ms before. These small noncontemporaneous local samples have host/CPU variability; the normal-hasher samples are materially slower after. They do **not establish a login latency improvement or acceptable production latency**. The bounded additional query/no repeated registry-write evidence is established; live sign-in/browser/network and MariaDB performance remain unverified. No authentication hash/audit work was removed to alter timings.

## Remediation reproduction and final audit

External evidence root: `%TEMP%/tmp-admin-registry-remediation`. Interpreter: `D:/codex-worktrees/TMP-Grade-Filters-Performance/.validation-env/Scripts/python.exe`. `registry_settings.py` imports existing `duplicate_test_settings`, keeps the default DB in memory and redirects TEST to a named disposable clone; `registry_real_settings.py` uses normal PBKDF2. `before-source/` is the exact pre-remediation candidate snapshot. `opening-status.txt` and `opening-hashes.json` record the opening inventory/content. Evidence includes `before-regressions.log`, `before-sqlite-concurrency.log`, `sqlite-concurrency.log`, `focused.log`, `broader.log`, the retained preliminary `broader-seed-fixture-failure.log`, `before/after.json`, `before/after-inherited.json`, `before/after-pbkdf.json`, `reconciliation-cost.json` and probe source/query HTML. Operational databases/settings were not used.

Set `PYTHONPATH` and `TMP_ADMIN_EVIDENCE` to that root; `REGISTRY_TEST_DB` selects the disposable test schema. Restore `test.sqlite3` from pristine `before-perf.sqlite3` using retained `restore_schema.py` before separate suites: TransactionTestCase flushes seeded data at suite end. No operational migration or permission rewrite is involved.

```text
python -B manage.py test apps.accounts.tests_session_registry_reconciliation apps.admin_portal.tests_dashboard apps.accounts.tests_active_portal_sessions apps.core.tests_session_timeout --settings=registry_settings --keepdb --noinput -v 1
python -B manage.py test apps.accounts apps.faculty_portal apps.admin_portal.tests_dashboard apps.admin_portal.tests_users apps.admin_portal.tests_scope apps.admin_portal.tests_reopen_requests apps.admin_portal.tests_governance_anomalies apps.admin_portal.tests_academic_scope_banner apps.core.tests_permissions apps.core.tests_session_timeout --settings=registry_settings --keepdb --noinput -v 1
python -B manage.py test dashboard_probe inherited_probe reconcile_cost_probe --settings=registry_settings --keepdb --noinput -v 1
python -B manage.py test dashboard_probe inherited_probe --settings=registry_real_settings --keepdb --noinput -v 1
python -B manage.py check --settings=duplicate_test_settings
python -B manage.py makemigrations --check --dry-run --settings=duplicate_test_settings
python -B manage.py migrate --plan --settings=duplicate_test_settings
git diff --check
git diff --stat
git diff
```

Use separate before/after disposable schema names and phase variables for performance probes, retained in the evidence directory. No browser/server process was launched in this remediation. Final check: Django zero issues; migration dry run No changes detected; migration plan completed against in-memory settings with no application. Final content audit compared SHA256 against all 23 opening paths: eight authorized existing paths changed, 15 byte-for-byte unchanged, one new accounts test. All nine incremental files passed whitespace/conflict-marker checks and changed Python parsed successfully; incremental source diff was inspected. `git diff --check` passed with LF/CRLF notices only, full tracked diff/stat retained, index empty, branch/full HEAD unchanged. Exact final status and independent re-review gate are recorded in HANDOFF.md and external `final-status.txt`/`preservation-audit.json`/`incremental-review.diff`. The original implementation inventory/status below is historical, with the new accounts regression test added for the current 24-path package. All earlier unresolved acceptance items remain open.

## Original implementation record (historical evidence)

## Identity and protected work

- Worktree: `D:/tmp_branches/tmp-admin-dashboard`.
- Branch: `feat/admin-dashboard-responsive`.
- Initial and final full HEAD: `acbc746f1c969edc4a94d9f4ffd61454349ed8b8`.
- Worktree/branch availability was checked before creating the clean worktree from that exact recommended baseline. The existing Faculty release commit and clean Faculty worktree were verified locally. **The currently deployed staging SHA was not independently established** from available local evidence; this is not live staging release verification.
- `D:/teachermateplus` remains on `feat/departmental-exam-builder-stage5-8` at `56a370bea4eda9250f0dd55b8e058d208182a6e1`, with its pre-existing dirty Departmental Exam Builder and other paths preserved. The Faculty Dashboard worktree remains clean. No Faculty template, application or frontend file is part of this change.
- All candidate changes remain unstaged; index publication remains a separate gate.

## Implemented behavior

1. Removed the Audit Logs statistic/query, all four import statistic/aggregate cards, detailed Governance Alerts initialization and the Stage 0.75 label/style. Authorized audit/governance/import routes remain available; View Governance Alerts uses the existing Critical Actions report.
2. Live-user summary and listing share a SQL selector: distinct active users, live registry-backed DB sessions, actual `expire_date > checked_at`, no 25-user/500-session caps and no payload decoding. Detail loading is confined to the authorized 50-row paginated listing. Restricted viewers are limited to the selected tenant/campus and their department scope; unrestricted department scope is explicitly checked. Global superusers still use selected default tenant/campus for this monitoring view. User defaults or active exact role assignments define scoped visibility; this is user visibility, not an inference about a browser's current selected campus from encoded session data.
3. Reopen card uses efficient scoped aggregate counts, at most five pending requests and per-campus explicit reviewer eligibility. Initial HTML and AJAX share a partial. Only the card refreshes every 600 seconds; hidden tabs pause, requests cannot overlap, timeouts and failures clear stale actions, and expired login redirects are never injected into the card. Every request rechecks RBAC and signed user/tenant/campus/department/AY/term identity. Existing supervision, lock/deadline and approval services remain unchanged.
4. Admin-specific mobile drawer below 992px preserves server-filtered navigation and desktop group/collapse behavior. Backdrop, Close, Escape, focus trap/restoration, inert background, scroll locking, reduced motion and responsive transitions are implemented. Optional local storage failures do not prevent navigation.
5. Compact vertical card stacks use three columns at >=1400px, two at >=768px and one below 768px, retaining NCBA identity and scope selectors. Complex tables scroll inside cards. Quick actions respect destination permissions. Guidance and lock/period behavior are retained.
6. Dashboard permission and active-academic-scope results are reused only within the same request/user/selected scope. No cross-request authorization cache was introduced.

## Shared lifecycle changes requiring review

The SQL monitor must track real expiry and credential/key lifecycle rather than audit timestamps. `User.save()` invalidates only registry rows on an actual password change, inside the user-row lock/transaction also used by registration. It adds a lock/read for existing full user saves or saves that include password; targeted non-password saves, including login's last-login update, bypass it. No Session is deleted by this monitoring hook. Registration checks the locked current password before advertising a session validated against older credentials; such stale registration does not revoke newer sessions. Password hash validation and login auditing remain with existing authentication code.

A private session-key marker lets the existing timeout middleware recognize a key rotation and repair monitoring registration after the response. Ordinary requests do not query the registry for this marker. Django Admin password changes, normal portal password changes, logout and single-device policy are covered by regression tests. Background reopen-card GETs retain the stored absolute idle deadline, clamped if the configured timeout becomes shorter. This is necessary because scope/session middleware and SESSION_SAVE_EVERY_REQUEST otherwise renew even background requests. Interactive traffic keeps the existing sliding timeout.

Registry completeness is required for operational count accuracy. Legacy sessions that predate registry registration, raw DB/session tampering and credential changes outside the supported ORM lifecycle are not reconstructed with an unbounded payload scan. Verify the deployed `accounts.0011` registry lifecycle and live data before activation. SQLite tests do not prove real row-lock/concurrency behavior.

## Measured login-to-dashboard path

Unchanged baseline measurements were captured before editing production sources. Equivalent synthetic fixtures, same interpreter/settings/SQLite schema, 0/100/600 additional live sessions, four login/redirect/dashboard samples per volume; warm medians exclude the first sample. The untouched comparison source is a `git archive` of exact baseline HEAD in the external evidence directory. Both PBKDF2 test DB schemas were cloned from the same completed disposable schema. No operational DB was used.

**Normal PBKDF2 hasher; before / final candidate, milliseconds:**

| Additional sessions | Login POST ms | Dashboard GET ms | Dashboard queries |
|---:|---:|---:|---:|
| 0 | 1371.62 / 1758.46 | 265.36 / 161.42 | 113 / 76 |
| 100 | 1439.86 / 1652.46 | 217.86 / 175.30 | 113 / 76 |
| 600 | 1424.08 / 1655.13 | 281.99 / 143.13 | 113 / 76 |

Warm login POST queries are 46 before and after; the first POST has 43 because there is no prior registered session to replace. Every sample has one redirect to `/admin-portal/dashboard/`. Query count remains constant as session volume increases. PBKDF2 authentication remains dominant and variable. There is **no demonstrated authentication speedup**, and the final POST samples were slower than the earlier baseline samples despite equivalent hashing/security and unchanged query counts. Host/CPU variability and small samples limit latency conclusions. Dashboard query reduction is repeatable (37 fewer queries), and the controlled GET samples improved.

**MD5 test hasher used only to isolate application overhead; before / final candidate, milliseconds:**

| Additional sessions | Login POST ms | Dashboard GET ms |
|---:|---:|---:|
| 0 | 40.59 / 31.65 | 283.96 / 164.89 |
| 100 | 29.84 / 32.41 | 264.35 / 150.28 |
| 600 | 32.72 / 32.58 | 402.97 / 213.25 |

Do not compare test-hasher POST latency with normal password hashing. No production password hasher was changed.

### Live browser and frontend loading

Real headless Chrome submitted the CSRF-protected login form through temporary local WSGI servers backed by equivalent independent clones with 600 sessions. Per-request instrumentation measured credentials, audit, registry, permissions, scope, SQL and rendering; browser JSON records the redirect/resource waterfall. Both phase tests passed, with one 302 login redirect and dashboard HTTP 200.

- Before click-to-dashboard-content: **2599 / 2589ms**. Final candidate: **2605 / 2718ms**. These samples **do not establish faster end-to-end browser sign-in**.
- Live dashboard query instrumentation: **112 -> 75**, the same 37-query reduction. Its instrumentation omits one transaction/query entry captured by Django's test query collector (113 -> 76).
- Before dashboard server time: 425.11 / 361.25ms. Final candidate: 260.55 / 392.10ms. The sample spread prevents a reliable live latency claim.
- Before login credential-validation instrumentation: 1732.78 / 2007.36ms. Audit: 11.94 / 8.69ms; registry: 13.85 / 29.32ms. Final candidate login timings are retained in `after-browser-waterfall.json`; password validation remains the dominant measured phase, not audit or session registry processing.
- The new first-party scripts were non-blocking/deferred; cold final resource durations were 6.3ms for sidebar.js and 8.0ms for dashboard.js, then cached. Shared Bootstrap CSS remains a blocking external stylesheet, and Bootstrap JS remains an external dependency. Cross-origin resource timing is restricted; a zero ResourceTiming duration is not proof of a free network fetch. An initial login-page JS request was aborted by submission in the before phase. No evidence here attributes the reported operational delay to a slow CDN, and staging/network behavior remains unverified.
- Disposable WSGI servers did not serve operational media/favicon assets. Offline responsive fixtures instead use the existing logo and the same local Bootstrap 5.3.3 CSS. Those assets/fixtures are distinct from live staging acceptance. Temporary validation server/browser processes were closed; no operational service was restarted.

Verified bottlenecks: repeated permission resolution, dashboard session decoding/audit traversal and unnecessary audit/import/governance work; they were removed from initialization. Real password hashing is still the main observed sign-in cost. Scope/menu/feature middleware costs remain measurable and were preserved. Do not weaken authentication or silently broaden this scope to claim faster login. Profile the deployed staging request/hardware separately if the operational delay remains unacceptable.

## Executed validation and limitations

| Validation | Actual result |
|---|---|
| Final broad Admin scope/reopen/governance/academic banner, account session/OTP/lockout, core permissions/timeout and all Faculty Portal tests | **447 total; 441 passed; six expected MariaDB-only skips; zero failures/errors; 124.829s** |
| Focused final dashboard, Admin user create/list/edit, all Accounts, timeout and final query-growth probe | **107/107 passed; zero skips/failures/errors; 14.135s** |
| JS unit + real Chrome responsive suite | **26/26 passed; zero skips/failures** (12 JSDOM script tests + 14 Chrome authorized/denied viewport cases; final 11.315s) |
| Real Chrome login/network before and final after | **1 test in each phase passed**, two form submissions per phase |
| PBKDF2 comparison probes | **1 test each passed**, 12 login/GET samples per phase; final candidate probe 27.049s |
| Guide-containing run | **55 total, 54 passed, one baseline-equivalent failure** described below |
| Untouched baseline guide reproduction | **1 test, identical one failure**, 0.109s |
| Django check | Passed, zero issues; final check repeated after implementation |
| makemigrations --check --dry-run | No changes detected; no schema migration |
| migrate --plan | Ran only against disposable in-memory settings; 249 existing nodes planned, none applied to a non-test DB |
| git diff --check / JS syntax / complete tracked and new-file review | Recorded in final handoff after execution |

Counts overlap and must not be added together. Expected negative-path warning/exception logs (denials, injected render errors, oversized upload rejection) are not suite failures.

The unrelated guide failure is `apps.admin_portal.tests_help_guide.AdminHelpGuideTests.test_departmental_exam_configurer_receives_stage41_help`: a positional assertion expects `blocks Exempt only` at check_first[1], which now describes saved-equivalency campus coverage. Candidate and untouched exact baseline produce the same failure and text. This task does not change that Departmental Exam behavior or weaken its assertion.

Chrome widths: **375, 390, 440, 767, 768, 992 and 1440px** for both authorized and users/audit direct-DENY menus. Tests cover no page overflow, internal table scrolling, column count, menu/destination visibility, initial focus, Tab/Shift+Tab containment, Escape, close/backdrop, focus restoration and scroll lock. Real final screenshots at 375/768/1440px plus the denied mobile fixture were visually inspected. Offline fixtures omit external Bootstrap JavaScript, so Bootstrap group-collapse network acceptance is not asserted by those offline tests. No physical device, other browser engine, live staging role/feature configuration, MariaDB locking, production-sized plan or production acceptance is claimed.

## Reproduction and retained evidence

External root: `%TEMP%/tmp-admin-dashboard-validation`. Interpreter: `D:/codex-worktrees/TMP-Grade-Filters-Performance/.validation-env/Scripts/python.exe`. `dashboard_settings.py` imports tracked duplicate_test_settings and uses a named disposable SQLite test DB; default DB remains in memory. `dashboard_real_settings.py` selects the normal PBKDF2 hasher and separate cloned test DBs. None loads operational dotenv/database state. Tests use `--keepdb` solely for these disposable schemas.

Evidence includes `before.json`, `final-after.json`, `before-pbkdf.json`, `after-pbkdf.json`, their HTML/query JSON, `final-broader.log`, `final-registry.log`, `final-browser.log`, `before/after-browser-waterfall.json`, `baseline-guide.log`, `final-focused.log`, final migration plan, viewport PNG/JSON and authorized/denied exports. Probe/server instrumentation source and the untouched baseline-source snapshot are retained there. Preliminary failures are superseded by identified final successful runs; they are not acceptance evidence.

```text
python -B manage.py test apps.admin_portal.tests_dashboard apps.admin_portal.tests_governance_anomalies apps.admin_portal.tests_reopen_requests apps.admin_portal.tests_scope apps.admin_portal.tests_academic_scope_banner apps.accounts.tests_active_portal_sessions apps.accounts.tests_login_lockout apps.accounts.tests_login_otp apps.core.tests_session_timeout apps.core.tests_permissions apps.faculty_portal --settings=dashboard_settings --keepdb --noinput -v 1
python -B manage.py test apps.admin_portal.tests_dashboard apps.admin_portal.tests_users apps.accounts apps.core.tests_session_timeout dashboard_probe --settings=dashboard_settings --keepdb --noinput -v 1
python -B manage.py test dashboard_probe --settings=dashboard_real_settings --keepdb --noinput -v 1
python -B manage.py check --settings=duplicate_test_settings
python -B manage.py makemigrations --check --dry-run --settings=duplicate_test_settings
python -B manage.py migrate --plan --settings=duplicate_test_settings
node --test frontend/admin-dashboard/dashboard.test.cjs frontend/admin-dashboard/responsive.test.cjs
```

Set PYTHONPATH/TMP_ADMIN_EVIDENCE to the external root for these external settings/probes. Set TMP_ADMIN_PHASE to before/before-pbkdf/final-after/after-pbkdf as appropriate; run before phases from baseline-source and after phases from the candidate. `frontend/admin-dashboard/README.md` describes fixture preparation, CSS/Chrome/JSDOM variables and browser skips. No additional runtime/frontend dependency was installed.

## Exact changed files and next gate

- `CHANGE_LOG.md`
- `HANDOFF.md`
- `TEACHERMATEPLUS_CONTEXT.md`
- `apps/accounts/models.py`
- `apps/accounts/services.py`
- `apps/admin_portal/dashboard.py`
- `apps/admin_portal/help_guide.py`
- `apps/admin_portal/tests_dashboard.py`
- `apps/admin_portal/tests_governance_anomalies.py`
- `apps/admin_portal/urls.py`
- `apps/admin_portal/views.py`
- `apps/core/context_processors.py`
- `apps/core/middleware.py`
- `frontend/admin-dashboard/README.md`
- `frontend/admin-dashboard/dashboard.test.cjs`
- `frontend/admin-dashboard/responsive.test.cjs`
- `static/admin_portal/dashboard.js`
- `static/admin_portal/sidebar.js`
- `templates/admin_portal/_dashboard_reopen_card.html`
- `templates/admin_portal/base.html`
- `templates/admin_portal/dashboard.html`
- `templates/admin_portal/logged_in_users.html`
- `docs/ADMIN_DASHBOARD_IMPLEMENTATION.md`

Next gate: independent read-only review of these **23 unstaged paths** against the exact baseline, with special attention to user-row lock order, monitoring completeness, idle preservation, effective permission/direct-DENY reuse, supervision/reviewer scope and the retained rendered/browser evidence. Before any later activation, separately verify the live staging SHA, deployed registry/schema completeness, actual RDBMS locking and real role/feature/device/network behavior. Commit, push, migration application, deployment and restart remain separately authorized gates. All earlier unresolved handoff items are preserved.

Final audit: Django check passed with zero issues; makemigrations dry run reported No changes detected; migrate plan completed against disposable in-memory settings; both application scripts and both frontend test files passed Node syntax checks. Full tracked diff and all ten new files were inspected; Python changes parsed, new-file whitespace/conflict-marker checks passed, git diff --check passed (only LF/CRLF notices), and the index is empty. The 23-path inventory contains no Faculty, migration, environment, database or repository-log paths. Final tracked-only diff stat before this audit note was 13 files, 286 insertions, 423 deletions; the ten new files are separately listed above and are not included in git diff --stat.

Final `git status --short --untracked-files=all`:

```text
 M CHANGE_LOG.md
 M HANDOFF.md
 M TEACHERMATEPLUS_CONTEXT.md
 M apps/accounts/models.py
 M apps/accounts/services.py
 M apps/admin_portal/help_guide.py
 M apps/admin_portal/tests_governance_anomalies.py
 M apps/admin_portal/urls.py
 M apps/admin_portal/views.py
 M apps/core/context_processors.py
 M apps/core/middleware.py
 M templates/admin_portal/base.html
 M templates/admin_portal/dashboard.html
?? apps/admin_portal/dashboard.py
?? apps/admin_portal/tests_dashboard.py
?? docs/ADMIN_DASHBOARD_IMPLEMENTATION.md
?? frontend/admin-dashboard/README.md
?? frontend/admin-dashboard/dashboard.test.cjs
?? frontend/admin-dashboard/responsive.test.cjs
?? static/admin_portal/dashboard.js
?? static/admin_portal/sidebar.js
?? templates/admin_portal/_dashboard_reopen_card.html
?? templates/admin_portal/logged_in_users.html
```

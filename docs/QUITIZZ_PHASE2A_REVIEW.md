# QuiTizz Phase 2A core gameplay - 2026-10-10

Verdict **B - Phase 2A complete with non-blocking source-review observations**. Final combined validation passed **137/137**: all 117 QuiTizz tests plus 20 relevant regressions. Source implementation is ready for independent review; browser, shared-cache and MariaDB acceptance remain deployment gates. No publication or normal-local database migration is authorized under TMP-QUITIZZ-06-PHASE2A-CORE.

## Baseline and scope

- Worktree: `D:\codex-worktrees\TMP-QuiTizz`; branch `feat/quitizz`.
- Required and verified clean starting HEAD: `975001276b2fdc5fee07ab5f756ad2b372875845`. HEAD remains unchanged.
- The initial status was empty. All new work remains unstaged and uncommitted.
- Phase 1 authoring, permissions, menu, feature configuration and immutable question content are retained. The only changed Phase 1 assertion replaces the obsolete gameplay-unavailable guide text with Phase 2A guide instructions.
- No commit, push, deployment, service restart, normal-local migrate, secrets/.env changes, dependency changes, Channels, Redis setup, ASGI/proxy/systemd work, WebSockets, frontend framework or celebration animations.
- The dirty `D:\teachermateplus` checkout and Faculty Attendance/DTR files were not edited. `logs/system.log` is excluded; runtime logs are external to the worktree.

## Public and host transport

Namespace remains `quitizz`; the existing root include already supports routes outside portal prefixes.

| Route | Method / authorization |
|---|---|
| `/quitizz/play/<uuid>/` | GET, public session shell; no account required; feature ON |
| `/quitizz/play/<uuid>/exchange/` | CSRF-protected POST of fragment capability; issues short grant cookie |
| `/quitizz/play/<uuid>/join/` | CSRF-protected POST nickname using grant cookie; issues participant identity |
| `/quitizz/play/<uuid>/state/` | GET, participant cookie required; safe private game state |
| `/quitizz/play/<uuid>/answer/` | CSRF-protected POST current question UUID and A-D choice |
| `/faculty/quitizz/sessions/<uuid>/host/` | Existing GET host screen, owned and exact campus scoped |
| `/faculty/quitizz/sessions/<uuid>/command/` | POST action and expected version; host permission, ownership, tenant/campus |
| `/faculty/quitizz/sessions/<uuid>/state/` | GET authorized host participant list/count and lifecycle |
| `/faculty/quitizz/sessions/<uuid>/qr/` | GET authorized host SVG QR; no capability query parameter |

Host action values: `open_joining`, `close_joining`, `start`, `open_question`, `close_question`, `reveal`, `next`, `complete`, `cancel`, `remove` (participant UUID). Every mutation checks feature/assigned host permissions, locks the scoped session, checks version and lifecycle, and increments version once. Audit failure rolls back the entire command. Host GET previews never mutate saved data.

Lifecycle: `READY -> LOBBY -> QUESTION_CLOSED (prepared, not opened) -> QUESTION_OPEN -> QUESTION_CLOSED -> ANSWER_REVEALED`. Next prepares the next question; Open Question starts its timer. Complete is allowed after reveal; Cancel is allowed before completion and does not reveal an open key. Opened questions cannot be reopened. Start closes joining; the host may reopen it during gameplay. Reopening rotates the QR generation without extending session expiry.

Session expiry is eight hours from first opening of joining. Old READY sessions have no expiry until the host opens their lobby. Expiry rejects public access and further gameplay mutations; it does not rewrite history or run a scheduler. Cancelled players receive only cancellation state; completed players can receive their last revealed feedback and private final summary until expiry. Joining is rejected for completed/cancelled sessions.

HTTP refresh uses a five-second, non-overlapping timeout, pauses requests in hidden tabs, and stops on terminal/unavailable state. The participant countdown updates locally every 250 ms and makes no requests. No server loop, countdown task or tick event exists. The service payload and rules are independent of HTTP for later transport replacement. UI uses Bootstrap and existing QuiTizz CSS, native buttons, large participant targets, labels/focus styles, text feedback, and escaped text. Host list refresh preserves focus on participant controls.

## Identity, nickname and security

- QR reuse: existing ReportLab `QrCodeWidget` / `Drawing` / `renderSVG` pattern from Exit Pulse, without adding a dependency. Exit Pulse and Orientation fragment-to-CSRF-POST flows and their signed-cookie/HMAC conventions were inspected.
- QR capability: Django timestamp-signed payload of session UUID and random join-generation UUID, dedicated salt, maximum signature age eight hours, plus current joining/state/expiry checks. No long-lived plaintext bearer is saved. The signature is the capability; the persisted generation is a revocation value, bound to its session. Closing joining rejects exchange; reopening invalidates older capabilities/grants; terminal/feature-OFF rejects joining.
- URL path contains only a public UUID. The capability travels in the fragment, then in a POST body. `history.replaceState` removes fragment and query state immediately; JS clears the in-memory capability after exchange. No credentials in URLs, localStorage or ordinary access-log paths. Mutation errors do not log credentials. Exchange marks capability POST parameters sensitive.
- Exchange grants use a different signing salt, an HttpOnly cookie scoped to that session's play path, and ten-minute expiry. No browser JS access is required.
- Participant identity: participant UUID plus 32 random secret bytes (`token_urlsafe(32)`); only a session-bound HMAC-SHA256 digest is stored. Cookie is HttpOnly, SameSite=Lax, path/session specific, with lifetime bounded by session expiry. Secure is enabled by the existing production `SESSION_COOKIE_SECURE` setting or an HTTPS request. No production settings are changed. Digest comparisons use `compare_digest`. Feature cookie names contain `token`, enabling Django's standard exception-report cookie redaction; credential/token service locals use sensitive-variable decorators.
- Refresh authenticates the same participant via cookie. Nickname alone cannot authenticate. Removed, expired, malformed, forged and cross-session identities fail generically. Rejoin with a valid identity does not insert a second participant. Losing the cookie requires a fresh allowed join and a free nickname; removed nicknames remain reserved in history.
- Nickname display: Unicode NFKC, trim outer whitespace, 1-32 Unicode characters, reject control/format/surrogate characters. Uniqueness canonicalization: NFKC(casefold(display)), then SHA256 UTF-8 digest in `nickname_key`. This preserves explicit accent semantics without depending on MariaDB text collation; e.g. fullwidth ALICE matches Alice, STRASSE matches Straße, composed/decomposed accents match, and e differs from é. Canonical strings longer than 64 characters are rejected. DB uniqueness is `(session, nickname_key)` including removed participants.
- Templates autoescape names/prompts; JS uses `textContent` and DOM construction, never participant HTML. Initial participant HTML has no question, key or snapshot JSON. No correct choice, correctness, points, totals or rank is serialized before reveal. Acceptance ACK contains only `accepted` and public question UUID. Current revealed question feedback is allowed after explicit host reveal. Private total/correct-count/final rank appear only after completion. No public leaderboard or pre-reveal cumulative score.
- Standard Django CSRF remains on exchange/join/answer/host commands. GET has no gameplay mutation. Public content is `no-store`, noindex/noarchive, nosniff, with same-origin referrer policy compatible with CSRF. Fragments do not travel in referrers; no sensitive URL remains after exchange.
- Participant and response queries are scoped through the session. Public clients never choose tenant/campus. Host mutation queries include tenant/campus/owner before taking the session lock. Errors reveal no foreign object metadata.
- Application throttles use cache add/incr and fixed one-minute buckets: exchange/join 300 per IP per session; answer/state 60 per participant credential per session, plus 3,000 per IP per session. IP is direct REMOTE_ADDR, never arbitrary forwarded input. This accommodates shared Wi-Fi and keeps credential budgets separate. 429 returns Retry-After=60. Public mutation form bodies are limited to 4 KiB at the application boundary and accept form-urlencoded only; Django's existing request/CSRF parser limits remain in force before view entry.
- **Deployment blocker:** default LocMem enforcement is per process. Shared cross-process production enforcement requires Phase 2B Redis/cache configuration and proxy/IP validation. No process-wide production rate limiting is claimed.

## Answers, timers, scoring and races

- Open persists timezone-aware server `opened_at` and `deadline_at`. Close/reveal persist their server timestamps. Browser time, score and elapsed fields are ignored. The HTTP wrapper captures server receipt time before throttle/service processing.
- Submit locks the session and participant, checks feature/active scope/session expiry, identity/removal/identity expiry, session/current question UUID, A-D choice, open lifecycle and `opened_at <= receipt <= deadline_at`. The first insert is final. DB uniqueness backs `(participant, session_question)`.
- Retry of the same accepted choice for the current question returns the same ACK without changing response or totals, including after close/reveal. A different choice fails. Prior-question retries after progression and all terminal/removed/expired retries fail safely. No second response or second score increment.
- Session row locking serializes same-nickname joins, duplicate answers, host commands, close/answer and remove/answer races. Lock order is session then participant; question timing writes occur under the same session lock. No `SELECT FOR UPDATE` join of shared tenant/campus rows, so independent sessions do not share those row locks. IntegrityError recovery uses nested savepoints.
- Formula is unchanged: wrong = 0; correct = `700 + floor(300 * remaining_time / duration)`, clamped to 700-1000. Integer microseconds avoid float rounding; elapsed milliseconds are floor-derived from server timestamps. Answers one microsecond late fail; an answer exactly at the deadline earns 700 if correct. Correct at open earns 1,000; midpoint earns 850.
- Only the answering participant's totals update with F expressions. Completion ranks active participants in one set-based sort: score descending, correct-count descending, cumulative response milliseconds ascending, joined_at ascending, UUID ascending. Final rank is persisted by bulk update. Removed players are excluded from final ranking, while their rows/responses remain saved.
- Session/question content remains guarded by Phase 1 model/default-manager immutability. Internal gameplay context allows only lifecycle/timing fields (and initialization of the empty scoring-policy placeholder at Start). There is no direct SQL implementation and no content/answer/timer/order rewrite. Protection is application-level, not database triggers against arbitrary external SQL. Cross-table response consistency is enforced by scoped services and model validation, not a cross-table CHECK constraint.

## Exact model fields and schema

All models retain BigAutoField PK and `created_at`, `updated_at` timestamps.

| Model | New fields |
|---|---|
| QuiTizzSession | joining_open Boolean=False; join_generation UUID default uuid4, non-editable; expires_at nullable datetime; completed_at nullable datetime. Existing status now supports seven lifecycle choices; current_position/state_version/scoring_policy_snapshot become service-managed game state. Launch remains READY, position 0, version 1, empty policy until Start. |
| QuiTizzSessionQuestion | public_id UUID default uuid4, unique/non-editable; nullable opened_at, deadline_at, closed_at, revealed_at. All existing snapshotted content remains unchanged. |
| QuiTizzParticipant | session FK PROTECT; public_id UUID default uuid4 unique/non-editable; nickname Char32; nickname_key Char64 digest; reconnect_digest Char64 non-editable; reconnect_expires_at datetime; joined_at auto_now_add; removed_at nullable datetime; total_score PositiveBigInteger=0; correct_count PositiveInteger=0; cumulative_response_ms PositiveBigInteger=0; final_rank nullable PositiveInteger. |
| QuiTizzResponse | participant FK PROTECT; session_question FK PROTECT; selected_choice Char1 A-D; received_at datetime; elapsed_ms PositiveBigInteger; is_correct Boolean; awarded_points PositiveInteger. |

New constraints: `qts_gameplay_status` replaces READY-only `qts_foundation_status`; `qtp_unique_nickname`, `qtp_nickname_required`, `qtp_totals_nonnegative`, `qtp_rank_positive`; `qtr_one_response`, `qtr_choice_ad`, `qtr_score_bounds` (elapsed nonnegative, points 0-1000). Existing version/question/content/answer/timer/position constraints remain.

New explicit index: `qtp_session_active_idx(session, removed_at, joined_at)`. UUID unique indexes, response unique pair, nickname unique pair and FK indexes support exact session/player/question lookups. Existing session host-scope and question session-position indexes remain. No N+1 list/state queries or ranking recomputation per answer.

Migration: `apps/quitizz/migrations/0004_quitizzparticipant_quitizzresponse_and_more.py`.
Dependencies: quitizz `0003_seed_faculty_navigation`; tenants `0005_enable_existing_sis_api_feature`; swappable AUTH_USER_MODEL dependency.
Impact: two new tables, additive session/question columns, lifecycle constraint replacement, indexes/constraints. Existing question public IDs use nullable unique ADD, batched per-row UUID backfill (500 rows), then non-null ALTER. Existing content/keys/order/timers and READY state/empty policy are preserved. No external tables or existing permissions/menu grants change. DDL/backfill locks and non-transactional MariaDB migration failure handling need rollout planning. Reversal deletes participant/response data and cannot restore READY-only constraint over progressed sessions without a separately approved recovery decision. **Normal-local migrations were not applied.** Test-runner/migration-executor operations affect disposable SQLite only.

## Validation environment

External harness: `C:\Users\Lenovo\AppData\Local\Temp\tmp-quitizz-phase2a-20261010`.
`quitizz_phase2_settings.py` imports base settings, overrides default DB to SQLite `:memory:`, TEST.NAME to harness `test.sqlite3`, locmem email/cache, MD5 test hashing, DEBUG=False and synthetic test/localhost hosts. All Django commands set:

```powershell
$env:PYTHONPATH='C:\Users\Lenovo\AppData\Local\Temp\tmp-quitizz-phase2a-20261010'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHON_DOTENV_DISABLED='1'
$env:DJANGO_LOG_DIR='C:\Users\Lenovo\AppData\Local\Temp\tmp-quitizz-phase2a-20261010\logs'
$env:DJANGO_ENV='local'
$env:DB_ENGINE='django.db.backends.sqlite3'
$env:DB_NAME=':memory:'
```

No normal development/live database is used. The plan is against empty `:memory:` and therefore lists the full graph, rather than the real local database's pending migrations. No SQLite throughput or actual concurrent race performance is claimed.

## Validation results, final inventory and verdict

The following combined command includes the complete QuiTizz suite (59 retained Phase 1 tests and 58 Phase 2A tests) and 20 directly relevant menu/settings/RBAC/Faculty-guide/configuration regressions. Exact command, with the environment above:

```powershell
py manage.py test apps.quitizz apps.core.tests_menu_performance apps.core.tests_settings apps.admin_portal.tests_roles.RolePermissionBoundaryTests apps.admin_portal.tests_users.UserRolePermissionSeparationTests apps.faculty_portal.tests_help_guide apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_configurable_features_shows_single_device_login_setting apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_configurable_features_renders_standard_cards_for_targeted_sections apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_configurable_features_can_store_assignment_workflow_settings apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_configurable_features_rejects_invalid_non_compliance_notice_timing --settings=quitizz_phase2_settings --keepdb --noinput -v 1
```

| Exact command | PASS/FAIL | Count / failures / errors / skips / warnings |
|---|---|---|
| `py manage.py makemigrations quitizz --noinput --settings=quitizz_phase2_settings` | PASS | Generated 0004; zero tests. Generated UUID ADD was refined to the safe backfill sequence before final validation. |
| `py manage.py test apps.quitizz --settings=quitizz_phase2_settings --noinput -v 1` | FAIL, superseded | 111 tests, 110 passed, 1 obsolete Phase 1 guide assertion failure, 0 errors/skips, 66.606s. The process loaded that old assertion before the file was updated; final suite uses Phase 2A assertions. Five expected CSRF rejection warnings. |
| Combined command above, first run | FAIL, superseded | 137 tests, 136 passed, 0 failures, 1 migration-fixture error, 0 skips, 115.299s. The historical account model lacked the live schema's required must_change_password column. Fixture now inserts via current User model before reading its historical representation. Five expected CSRF rejection warnings. |
| Combined command above, corrected final run | PASS | **137 tests, 137 passed, 0 failures/errors/skips, 125.465s.** All 117 QuiTizz tests included. Five expected CSRF rejection warnings, no Django check warnings. |
| `py manage.py check --settings=quitizz_phase2_settings` | PASS | 0 issues; 0 tests. Repeated after code refinement with the same result. |
| `py manage.py makemigrations --check --dry-run --settings=quitizz_phase2_settings` | PASS | No changes detected; 0 tests. Repeated after code refinement with the same result. |
| `py manage.py migrate --plan --settings=quitizz_phase2_settings` | PASS | Plan only, full graph against empty memory DB; 0 tests, no migrations applied by this command. |
| `node --check static/js/quitizz.js` | PASS | 0 tests, syntax only; no warnings. |
| `node --check static/js/quitizz_play.js` | PASS | 0 tests, syntax only; no warnings. |
| `node --check static/js/quitizz_host.js` | PASS | 0 tests, syntax only; no warnings. |
| `git diff --check` | PASS | 0 tests, no whitespace defects; possible LF-to-CRLF advisories. |
| `py -` (initial inline cookie reporter probe) | FAIL, superseded | 0 tests; probe used a nonexistent get_cleansed_cookies method. No database queries. |
| `py C:\Users\Lenovo\AppData\Local\Temp\tmp-quitizz-phase2a-20261010\validate_cookie_redaction.py` (initial file probe) | FAIL, superseded | 0 tests; external script lacked workspace import path. No database queries. |
| Same file probe, corrected | PASS | 0 tests; uses installed Django get_safe_cookies, confirms synthetic participant cookie is redacted, no database queries. |

Only the final combined run is the current aggregate test evidence. Do not add overlapping runs/counts. Logs are in external harness `final-tests.txt` (superseded) and `final-tests-corrected.txt` (final). The unchanged, previously documented Departmental Exam Admin guide positional assertion was not rerun or weakened; Admin QuiTizz guide/toggle checks are in the retained focused suite. Full Faculty grade workflows are unaffected and were not run.

Query-growth evidence from the corrected code: lobby host-list service 1 query and participant state service 3 queries at **1 / 20 / 100** players. Open-question state 5 queries. Accepted-answer path 13 queries at response insertion **1 / 20 / 100**, including test transaction/savepoint statements. These are service counts with a tenant feature override, excluding request middleware/host authorization, not wall-clock throughput. No N+1 growth found. Sequential synthetic 100-player state yields 100 accepted responses, total 100,000 points for answers at server-open time, and 100 distinct final ranks. This is representation, deterministic scoring and bounded-query evidence, **not a concurrent 100-player load test**. Actual MariaDB/InnoDB concurrency and throughput remain **NOT validated**.

The suite checks actual rendered participant HTML and JSON for absent answer keys/correctness/points/totals/rank before reveal, CSRF-enforced anonymous exchange/join/state, escaped hostile nicknames in host rendering, author/configuration/menu flows, and host QR SVG generation. Node checks do not establish browser execution. No actual phone QR scan, mobile layout, keyboard/focus or live-browser multi-player acceptance was performed. No staging/production acceptance.

Final modified files (12):

```text
CHANGE_LOG.md
HANDOFF.md
TEACHERMATEPLUS_CONTEXT.md
apps/admin_portal/help_guide.py
apps/faculty_portal/help_guide.py
apps/quitizz/models.py
apps/quitizz/tests.py
apps/quitizz/urls.py
apps/quitizz/views.py
static/css/quitizz.css
templates/quitizz/host.html
templates/quitizz/launch.html
```

Final created files (8):

```text
apps/quitizz/gameplay.py
apps/quitizz/migrations/0004_quitizzparticipant_quitizzresponse_and_more.py
apps/quitizz/public_views.py
apps/quitizz/tests_gameplay.py
docs/QUITIZZ_PHASE2A_REVIEW.md
static/js/quitizz_host.js
static/js/quitizz_play.js
templates/quitizz/play.html
```

Final branch: `feat/quitizz`. Starting and final HEAD: `975001276b2fdc5fee07ab5f756ad2b372875845`. Index remains empty. Final `git status --short`:

```text
 M CHANGE_LOG.md
 M HANDOFF.md
 M TEACHERMATEPLUS_CONTEXT.md
 M apps/admin_portal/help_guide.py
 M apps/faculty_portal/help_guide.py
 M apps/quitizz/models.py
 M apps/quitizz/tests.py
 M apps/quitizz/urls.py
 M apps/quitizz/views.py
 M static/css/quitizz.css
 M templates/quitizz/host.html
 M templates/quitizz/launch.html
?? apps/quitizz/gameplay.py
?? apps/quitizz/migrations/0004_quitizzparticipant_quitizzresponse_and_more.py
?? apps/quitizz/public_views.py
?? apps/quitizz/tests_gameplay.py
?? docs/QUITIZZ_PHASE2A_REVIEW.md
?? static/js/quitizz_host.js
?? static/js/quitizz_play.js
?? templates/quitizz/play.html
```

Diff statistics: tracked `git diff --stat` = 12 files, 234 insertions, 14 deletions; new files = 8 files, 1621 insertions; complete unstaged/untracked review = 20 files, 1855 insertions, 14 deletions. New-file checks use `git diff --no-index --check -- /dev/null <each exact created path>`; ordinary no-index difference exit 1 is not a whitespace failure. Final tracked/new whitespace checks passed. PowerShell's NativeCommandError capture of normal test-runner stderr is not a failing process result; the final test process exited 0.

Complete tracked/new diffs were inspected for disclosure, client trust, capability/cookie leakage, CSRF, scope, XSS, retries, OFF bypass, locks and query growth. Findings corrected during implementation: narrow snapshot gameplay allowances; nullable/batched UUID backfill; session-only locking without shared tenant/campus joins; scoped host lock query; exception credential redaction; preserving focused participant controls during host-list refresh. No remaining blocking source finding. Operational gaps are explicitly listed above; the default LocMem limitation blocks production deployment until shared enforcement is configured.

Safety confirmed: no stage, commit, push, deployment, restart, normal-local migrate or secrets edits. No logs/system.log, Faculty Attendance/DTR or unrelated source changes. Disposable test DB removed; external harness retains settings/logs/evidence. Previous unresolved handoff entries remain intact below the new entry. Next: independently review this diff, then authorize browser/mobile/keyboard/QR and MariaDB/InnoDB concurrency/100-player burst acceptance. Phase 2B shared Redis/transport infrastructure and Phase 2C presentation remain separate scopes. Publication needs a separate gate.

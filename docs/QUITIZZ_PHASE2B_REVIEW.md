# TMP-QUITIZZ-16-PHASE2B-FINAL-REMEDIATION - 2026-10-10

**Verdict A - Ready for final independent re-review.** This section supersedes **all earlier socket security/readiness/metadata/count contracts below**. Earlier run records are historical; their unresolved operational acceptance items remain open. Final combined Django **195/195 PASS**; Node **18/18 PASS**.

## Identity and scope

Worktree `D:\codex-worktrees\TMP-QuiTizz`; branch `feat/quitizz`; unchanged full HEAD `b330f8ddd0b4216c033c5bb46a19087a03baf3c8`. Opening status was 22 tracked modifications and 11 untracked files, with no staged changes. Existing Phase 2B work is preserved. This remediation changes 17 of those existing paths; it creates no additional path, model or migration.

## Final security contract

Database/MariaDB remains the authoritative game/history store. HTTP/domain services authorize protected reads and mutations. Redis provides notification transport and shared throttling. WebSockets provide wakeups only, never gameplay mutation or state.

All committed gameplay producers publish exactly `{"type":"quitizz.event","event":"sync_required"}` to Channels. `type` is an internal dispatch key; it is never sent to the browser. Every receiving consumer discards the entire incoming event payload and sends exactly:

```json
{"event":"sync_required"}
```

No version or transport hint is needed. No question UUID, position, deadline, lifecycle name, prompt, choices, answer key, answer/participant count, score, rank, nickname, participant list or private/session state crosses a gameplay WebSocket event. Producers reject every payload argument; consumers also filter stale/malicious internal envelopes. Audience group names remain internal.

The only additional application frames are exactly `{"event":"pong"}`, `{"event":"feature_unavailable"}` and `{"event":"participant_unavailable"}` for transport hygiene/closing. Revocation reasons are normalized to those generic constants. ASGI accept/close carry transport codes only. Browser-to-server input remains only the bounded literal `ping`; JSON gameplay commands close the transport and cannot mutate data.

**WebSocket ready is removed completely.** Accept emits no application frame. Browser open means transport connected and immediately starts canonical HTTP recovery; it never renders/unlocks gameplay. The player cookie bridge still returns an authorized HTTP acknowledgment (`ready: true`); it is not a WebSocket readiness event and is never used to unlock UI.

The initial authorization and existing post-registration recheck remain defense in depth. The post-accept check and per-event authorization reads are removed, rather than adding another final authorization read. A revocation can race transport acceptance/forwarding, but a late frame contains only a constant wakeup. The subsequent HTTP state request rejects the revoked identity. There is no requirement to globally serialize RBAC mutation and socket send.

Existing thirty-second host cadence, ping reauthorization, participant/session expiry scheduling and committed feature/removal close notifications remain optimizations. Confidentiality does not depend on their delivery timing. Existing registration-lock/pending-intent ownership, late-add discard, shielded single shutdown, cancellation/awaiting, single close and disconnect cleanup are preserved and regression tested.

## Canonical HTTP recovery

- Host state uses current request authentication/active user, effective selected tenant/campus, feature ON, active tenant/campus, exact session ownership, assigned Faculty Portal access and `quitizz.host`, with applicable direct DENY taking precedence. Unauthorized identities receive 403/404; inactive/logged-out hosts redirect to login. An expired host session now returns a generic JSON 404 instead of the generic validation-page 400, so the frontend becomes unavailable.
- Player state resolves current feature/session/active scope and checks the reconnect credential's digest and exact session binding, removal and expiry. Invalid, removed, cross-session, expired and feature-OFF identities receive generic JSON 404 without state. Valid players recover private feedback only after committed reveal and summary after completion.
- Normal HTTP scope middleware can restore an inaccessible raw selector to an authorized default. Wrong-scope regressions use genuinely selectable alternate tenant/campus fixtures and prove denial for the original session; no broad scope/RBAC behavior was changed.
- Host controls start disabled in JavaScript until successful canonical HTTP recovery. HTTP 401/403/404, login redirects or unexpected successful non-JSON responses disable controls, hide joining/roster/answer-key sections and stop transport. Player denial disables answers, hides gameplay/feedback and stops transport. A late successful response cannot re-enable either revoked UI.

## Producer audiences and coalescing

Participant join/removal, joining open/close and accepted-answer commits wake the host group only. Start, prepare/open/close question, reveal, completion and cancellation wake host and player groups. No event name or payload is serialized. Duplicate accepted-answer retries schedule no signal; rollback schedules none. Best-effort publication still has its existing deadline and feature guard; Redis failure cannot roll back committed gameplay.

The client debounces signals over 100ms. One HTTP recovery can run at a time; every signal arriving while it runs sets one dirty bit, yielding at most one debounced follow-up after completion. Manual recovery cancels a queued debounce. A rapid 100-answer wakeup burst produced one additional host fetch; 100 signals during an active fetch produced exactly one follow-up with maximum concurrency one. Signals never call UI renderers or supply counts. Host count ordering now uses HTTP request sequence plus lifecycle version: an older response cannot replace a newer count, a new question resets, and fresh canonical recovery can correct counts.

Open/reconnect performs immediate HTTP recovery. Connected fallback recovery remains sixty seconds, disconnected fallback five seconds, with existing exponential reconnect jitter. Thirty-second heartbeat/fifteen-second pong timeout remain. Foreground/online recovery and pagehide timer cancellation remain. No one-second polling or server countdown was added; the existing 250ms countdown is local display only.

## Validation and measurements

All Django runs use the verified existing external venv/settings under `C:\Users\Lenovo\AppData\Local\Temp\tmp-quitizz-phase2b-20261010`, disabled dotenv/bytecode, external logs, default/TEST SQLite `:memory:`, in-memory Channels and isolated LocMem cache/email. Test setup applies migrations only to a disposable shared-memory test database and destroys it afterward. No persistent runtime database is queried or migrated.

Final aggregate validation: **195/195 PASS** (175 QuiTizz + 20 retained feature/RBAC/menu/settings/Faculty-guide/configuration regressions), zero failures/errors/skips, **49.404s**, exit 0; includes all **58 realtime tests** and race/query/100-socket probes. Six expected rejected-CSRF warnings and three deliberately injected notification-loss warnings; no system-check issues. Final log `phase2b-final-validated-combined.txt`. Node final validation: **18/18 PASS**, zero failures/skips/cancellations, 155.6251ms. Django check zero issues; `makemigrations --check --dry-run` reports `No changes detected`; `migrate --plan` exits 0 and only lists the existing graph against isolated empty SQLite. All four JS syntax checks pass. Tracked/untracked whitespace and identity checks pass before shutdown. No migrations are required or applied to normal-local data.

Final aggregate command (same retained labels as the preceding implementation):

```powershell
$taskDir = 'C:\Users\Lenovo\AppData\Local\Temp\tmp-quitizz-phase2b-20261010'
$python = Join-Path $taskDir 'venv\Scripts\python.exe'
$env:PYTHONPATH = $taskDir
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHON_DOTENV_DISABLED = '1'
$env:DJANGO_LOG_DIR = Join-Path $taskDir 'logs'
$env:DJANGO_ENV = 'local'
$env:DB_ENGINE = 'django.db.backends.sqlite3'
$env:DB_NAME = ':memory:'
& $python manage.py test apps.quitizz apps.core.tests_menu_performance apps.core.tests_settings apps.admin_portal.tests_roles.RolePermissionBoundaryTests apps.admin_portal.tests_users.UserRolePermissionSeparationTests apps.faculty_portal.tests_help_guide apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_configurable_features_shows_single_device_login_setting apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_configurable_features_renders_standard_cards_for_targeted_sections apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_configurable_features_can_store_assignment_workflow_settings apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_configurable_features_rejects_invalid_non_compliance_notice_timing --settings=quitizz_phase2b_settings --noinput -v 1
```

Other exact commands: `& $python manage.py check --settings=quitizz_phase2b_settings`; `& $python manage.py makemigrations --check --dry-run --settings=quitizz_phase2b_settings`; `& $python manage.py migrate --plan --settings=quitizz_phase2b_settings`; `node --test frontend/quitizz/realtime.test.mjs`; `node --check static/js/quitizz.js`, `quitizz_host.js`, `quitizz_play.js`, `quitizz_realtime.js`; `git diff --check`; `git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --no-index --check -- /dev/null <each untracked file>`. Untracked checks explicitly recognize existing CRLF endings; no line-ending rewrite was performed.

Race regressions retain admission/accept, outbound/after-admission, lost notifications, expiry/late registration/disconnect, host active-user/permission/scope and direct-DENY probes. New last-read and outbound-send probes commit real DENY/removal/OFF/expiry mutations after an authorization result or immediately before real delivery; they require only constant wakeups and subsequent real HTTP denial. Outbound probes explicitly fail if authorization is called. Producer/forwarder tests run with database access prohibited. Node runs execute the actual host/player scripts with the actual transport to prove socket open and protected socket payloads cannot unlock/update UI, plus denial, count ordering, fallback/reconnect and fetch coalescing.

Measured baseline (`phase2b-final-before-queries.txt`, 1/1 PASS) and current observed results:

| Operation (host/player pairs where shown) | Before 1 | Before 20 | Before 100 | After 1 | After 20 | After 100 |
|---|---:|---:|---:|---:|---:|---:|
| Socket admission queries | 48/9 | 48/9 | 48/9 | 32/6 | 32/6 | 32/6 |
| Forwarding queries per receiving socket | 16/3 | 16/3 | 16/3 | **0/0** | **0/0** | **0/0** |
| Heartbeat reauthorization queries | 16/3 | 16/3 | 16/3 | 16/3 | 16/3 | 16/3 |
| Lobby host/player service recovery | 2/3 | 2/3 | 2/3 | 2/3 | 2/3 | 2/3 |
| Accepted response including executed commit guard | 15 | 15 | 15 | 14 | 14 | 14 |
| Full lobby host/player HTTP recovery | not measured | not measured | not measured | 36/3 | 36/3 | 36/3 |
| Full open-question host/player HTTP recovery | not measured | not measured | not measured | 38/5 | 38/5 | 38/5 |

The discarded outbound answer-count query saves one query per accepted answer; canonical HTTP still supplies the count. HTTP middleware is included only in explicitly marked full-HTTP rows. Session/tenant fixtures have local feature overrides; global fallback may add a constant query. Host roster response size grows with participant population, but query count does not. No per-player query in a shared publisher and no per-recipient query in forwarding.

Final aggregate: open-question full HTTP is **38 host / 5 player queries** at each 1/20/100 participants. **100 authorized concurrent sockets** received identical constant wakeups and disconnected with empty groups. One lifecycle HTTP recovery per player used **4 queries/request, 400 total**, with **zero forwarding queries** instead of 300 for 100 player recipients. Node measured **100 rapid answer wakeups -> one additional host recovery**, or one follow-up when the original fetch was active; maximum one concurrent fetch. This is disposable SQLite/in-memory/query-structure evidence, not networked Redis or InnoDB throughput.

Initial revised focused realtime run: 51 tests, 48 PASS and 3 fixture failures, superseded. An inactive-user denial redirects to login; alternate selectors must be genuinely role-accessible rather than auto-restored by normal scope middleware; the wrapped database_sync_to_async descriptor must be correctly bound. Fixtures corrected. Intermediate aggregate: 188/188 PASS, 47.299s; additional final probes were added afterward, so that aggregate is not the final-state claim. Test counts overlap and are not added.

## Exact file inventory and next gate

This remediation changes exactly these 17 existing paths: `apps/quitizz/consumers.py`, `apps/quitizz/realtime.py`, `apps/quitizz/gameplay.py`, `apps/quitizz/views.py`, `apps/quitizz/tests_realtime.py`; `static/js/quitizz_realtime.js`, `static/js/quitizz_host.js`, `static/js/quitizz_play.js`; `frontend/quitizz/realtime.test.mjs`; `templates/quitizz/host.html`, `templates/quitizz/play.html`; `apps/admin_portal/help_guide.py`, `apps/faculty_portal/help_guide.py`; `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, `HANDOFF.md`, this report. Other starting Phase 2B paths were not edited during this remediation.

Complete unstaged Phase 2B inventory remains the 33 paths listed in the historical inventory below: 22 tracked modifications + 11 untracked files. Final branch `feat/quitizz`, full HEAD `b330f8ddd0b4216c033c5bb46a19087a03baf3c8`, index empty. No staging, commit, push, deploy/restart, normal-local migrate, secrets/.env, Attendance/DTR or Phase 2C work.

Pending: final independent re-review; previous real Redis fan-out/shared-throttle/TTL/loss/restart/outage, MariaDB/InnoDB races/100-player bursts, authenticated browser/mobile/QR/Wi-Fi/keyboard and actual HTTPS/Nginx/systemd deployment acceptance remain unexecuted. Existing unrelated Admin guide assertion drift and full Faculty-grade smoke gaps remain unresolved. Next review this current contract and complete unstaged diff; external-service/browser acceptance, operational activation and publication retain separate authorization gates.

## Historical superseded reports

# TMP-QUITIZZ-14-PHASE2B-REMEDIATION-2 - 2026-10-10

Verdict **A - Remediation complete and ready for independent re-review**. All three reproduced blockers are closed by the final source/disposable-SQLite/in-memory/Node regressions. Final combined Django **182/182 PASS** (162 QuiTizz + 20 retained); Node **12/12 PASS**. This section supersedes earlier pre-accept-only admission, client-heartbeat-only RBAC invalidation and query-free forwarding claims. Earlier evidence and unresolved operational acceptance remain below.

## Admission and revocation

ASGI transport acceptance is provisional. One cancellable admission task performs initial throttled authorization, internal group registration, authoritative pre-accept recheck, transport accept, then fresh authoritative post-accept recheck before setting admitted or emitting ready. Lost/queued notification cannot replace that post-accept DB check. Gameplay is suppressed until admission. The browser also waits for ready before marking realtime connected, starting heartbeat or using the connected recovery cadence; provisional gameplay is ignored. Both transport asset URLs were versioned.

Every outbound host/player gameplay event acquires the connection output lock, rechecks current authoritative authorization, checks revoked/admitted again, then forwards existing safe metadata. Failure marks revoked, removes usability, cleans subscriptions and closes; queued subsequent gameplay/ping are ignored. The output lock serializes accept/ready, event/pong sends and close. One shielded owned shutdown cancels/awaits admission, scheduled expiry and host-cadence tasks and sends at most one close. No Redis permission cache or broad RBAC hook/refactor was added.

| Revocation timing | Final behavior |
|---|---|
| Before authorization | Authoritative rejection with 4403; no accept/ready/gameplay |
| Between authorization and registration | Registration recheck covers missed notification; reject and discard groups without usable state |
| During registration | Set revoked before awaiting cleanup; retain pending intent. Cancelled admission cleans tracked intent; heartbeat add finishing after revoke immediately discards itself |
| During transport accept | Delivered revoke cancels admission. With lost/delayed notification, transport may accept provisionally; post-accept check rejects before ready/gameplay |
| After accept, before ready | Fresh DB check must pass with unchanged group identity and non-revoked connection; otherwise close 4403 without ready |
| Fully connected | Committed removal/feature revoke closes and suppresses queued events. Lost notifications are covered by the next outbound guard; idle host cadence checks without heartbeat |

Host checks still reload the configured persisted Django SessionStore/authenticated active user, verify selected tenant/campus and active scope, require ownership and exact assigned Faculty Portal/host permissions with direct-DENY precedence. No superuser/role-name bypass. Participant checks still resolve current feature/session/scope and the session-bound credential against removal, digest and expiry. Origin rules remain unchanged.

Direct DENY after admission blocks the next host gameplay event. Idle hosts independently reauthorize every **30 seconds**, plus check/scheduling latency, and close on failure. Client heartbeat remains an additional check. The server cadence is an owned task cancelled/awaited on shutdown; no per-second authorization loop or server countdown tick.

## Group ownership

One per-connection asyncio registration lock serializes group_add and cleanup. Intent is tracked before awaiting the real add, so cleanup cannot clear in-flight ownership. After add returns, revoked is checked; a late add immediately awaits group_discard and removes its intent before rejection. Cleanup then discards remaining active/pending intent; failed discards retain intent for a disconnect retry. One shielded shutdown owns cancellation/cleanup/close; disconnect awaits it and repeats cleanup safely. Cleanup releases registration before waiting for output, avoiding inverse lock acquisition.

Regressions pause the real player group_add, begin actual expiry/disconnect cleanup, verify revoked and retained pending intent, resume the real add, then require inline discard, a single close, no pong, no memberships after cleanup/disconnect and all owned tasks finished. Scheduled expiry uses the actual authorized one-second expiry task. Actual authorization, accept and add awaits are retained.

## Permanent regressions

Nine new Django methods, **9/9 PASS** in the final aggregate, cover **15 scenarios/interleavings**. DB mutations/authorization are real; wrappers delegate to actual accept/group_add. Publisher notifications are deliberately lost only to isolate authoritative defenses.

| New method in apps/quitizz/tests_realtime.py | Final result |
|---|---|
| test_removal_during_transport_accept_never_emits_ready_or_gameplay | PASS; removal before/after actual accept, following successful pre-accept check |
| test_feature_off_during_transport_accept_never_emits_ready_or_gameplay | PASS; host/player before/after actual accept |
| test_direct_deny_during_transport_accept_never_emits_ready_or_gameplay | PASS; DENY before/after actual accept |
| test_direct_deny_blocks_next_host_event_without_client_heartbeat | PASS; queued start/question/answer events blocked |
| test_idle_host_direct_deny_is_rechecked_by_server_without_ping | PASS; only cadence accelerated to 50ms; real authorization |
| test_outbound_guard_rejects_lost_removal_and_feature_notifications | PASS; player removal/host OFF, queued gameplay suppressed |
| test_expiry_cleanup_serializes_heartbeat_late_group_add | PASS; actual expiry handler, late real add discarded |
| test_scheduled_expiry_serializes_heartbeat_late_group_add | PASS; actual scheduled expiry, late real add discarded |
| test_disconnect_serializes_heartbeat_late_group_add | PASS; overlapping disconnect, no membership/task leak |

Normal host/player, existing connected removal/OFF, scope/RBAC/origin/privacy, HTTP mutation/rollback/expiry, lost membership and historical migration regressions remain passing. The 100-client test now asserts all groups empty after disconnect. Performance measures real authorized sockets instead of a stub bypassing authorization.

New Node method `transport open stays provisional until authorized ready; gameplay is ignored before ready` **PASS**. All prior 11 Node tests pass, including five host-count regressions: realtime 2 survives delayed HTTP 1; next question resets 0; prior-question data ignored; lifecycle allows correction; fresh canonical recovery corrects downward when appropriate. `static/js/quitizz_host.js` is byte-identical to the starting worktree.

## Executed commands/results

Evidence: `C:\Users\Lenovo\AppData\Local\Temp\tmp-quitizz-phase2b-security-races-20261010`. Verified/reused existing external venv/settings: dotenv disabled, default/TEST SQLite :memory:, in-memory Channels, isolated LocMem caches, external logs, no normal-local DB use.

```powershell
$taskDir = 'C:\Users\Lenovo\AppData\Local\Temp\tmp-quitizz-phase2b-20261010'
$taskEvidence = 'C:\Users\Lenovo\AppData\Local\Temp\tmp-quitizz-phase2b-security-races-20261010'
$python = Join-Path $taskDir 'venv\Scripts\python.exe'
$env:PYTHONPATH = $taskDir
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUNBUFFERED = '1'
$env:PYTHON_DOTENV_DISABLED = '1'
$env:DJANGO_LOG_DIR = Join-Path $taskEvidence 'logs'
$env:DJANGO_ENV = 'local'
$env:DB_ENGINE = 'django.db.backends.sqlite3'
$env:DB_NAME = ':memory:'
& $python manage.py test apps.quitizz apps.core.tests_menu_performance apps.core.tests_settings apps.admin_portal.tests_roles.RolePermissionBoundaryTests apps.admin_portal.tests_users.UserRolePermissionSeparationTests apps.faculty_portal.tests_help_guide apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_configurable_features_shows_single_device_login_setting apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_configurable_features_renders_standard_cards_for_targeted_sections apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_configurable_features_can_store_assignment_workflow_settings apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_configurable_features_rejects_invalid_non_compliance_notice_timing --settings=quitizz_phase2b_settings --noinput -v 2
& $python manage.py check --settings=quitizz_phase2b_settings
& $python manage.py makemigrations --check --dry-run --settings=quitizz_phase2b_settings
& $python manage.py migrate --plan --settings=quitizz_phase2b_settings
node --test frontend/quitizz/realtime.test.mjs
node --check static/js/quitizz.js
node --check static/js/quitizz_host.js
node --check static/js/quitizz_play.js
node --check static/js/quitizz_realtime.js
git diff --check
```

- Final aggregate: **182/182 PASS**, **53.297s**, zero failures/errors/skips, exit 0 (`final-combined.txt`). Full QuiTizz 162 + 20 retained. Includes all nine final race methods, performance and 100-client tests. Six expected rejected-CSRF warnings and three injected notification-loss warnings; zero system-check issues/unhandled cancellation or task exceptions.
- Initial focused: `& $python manage.py test apps.quitizz.tests_realtime --settings=quitizz_phase2b_settings --noinput -v 2`: **45/45 PASS**, 19.378s, exit 0 (`focused-realtime.txt`). Preceded expanded accept scenarios/stronger late-add assertions; final aggregate is final-state evidence. Counts overlap and are not added.
- Final Node: **12/12 PASS**, 175.9786ms, zero failures/skips/cancellations, exit 0 (`node.txt`). Earlier 11/11 run preceded provisional-client fix. Four JS syntax checks exit 0 after final script edit.
- Django check: zero issues, exit 0 (`check.txt`). Drift: `No changes detected`, exit 0 (`migration-drift.txt`). Plan: exit 0 (`migration-plan.txt`), existing graph against empty disposable DB, not normal-local/production pending-migration proof. **No migrations are required.** No normal-local migration applied.
- Tracked diff check exit 0; all 11 untracked files checked by `git diff --no-index --check -- /dev/null <path>`, no whitespace errors (LF/CRLF notices only). Inspected full diff/status/stat and untracked sources/tests/configs; baseline comparison preserves existing work.

## Performance and 100 clients

Counts were identical at **1 / 20 / 100 participants**. Heartbeat/forwarding are real ASGI handshake-plus-operation captures minus separately measured same-role handshake, without authorization stubs.

| Operation | Starting | Final, each 1 / 20 / 100 |
|---|---:|---:|
| Host handshake | 32 | 48 (+16 post-accept check) |
| Player handshake | 6 | 9 (+3 post-accept check) |
| Host/player heartbeat | 16 / 3 | 16 / 3 |
| Host/player forwarding per receiving socket | 0 / 0 | 16 / 3 |
| Player/host recovery | 3 / 2 | 3 / 2 |
| Accepted answer including executed commit callback | 15 | 15 |

Host cadence uses the same 16-query recheck per 30 seconds per host. Publisher still sends one shared group message per audience, without recipient-list/per-player DB queries. Individual operations remain bounded/population-independent, with no roster N+1. Aggregate delivery now authorizes per receiving identity: a shared event to 100 players uses **300 recipient-guard queries**, plus existing committed feature check and any host guard. Aggregate constant-query forwarding is not claimed. This deliberate security cost needs real Redis/MariaDB throughput acceptance.

Final 100-client method **PASS**: 100 concurrent authorized WebsocketCommunicator clients received identical session_started invalidation, disconnected, and left no groups. In-memory evidence does not establish networked Redis/InnoDB throughput.

## Git, paths and safety

Verified unchanged branch `feat/quitizz`, full HEAD `b330f8ddd0b4216c033c5bb46a19087a03baf3c8`, worktree `D:\codex-worktrees\TMP-QuiTizz`. Final **22 tracked modifications + 11 untracked files**, empty index, all unstaged/uncommitted. All 33 starting paths preserved. Baseline SHA256/bytes verifies only these **13** existing paths changed; other **20** remain byte-identical:

```text
apps/quitizz/consumers.py
apps/quitizz/tests_realtime.py
apps/quitizz/realtime.py
static/js/quitizz_realtime.js
frontend/quitizz/realtime.test.mjs
templates/quitizz/host.html
templates/quitizz/play.html
apps/admin_portal/help_guide.py
apps/faculty_portal/help_guide.py
CHANGE_LOG.md
TEACHERMATEPLUS_CONTEXT.md
HANDOFF.md
docs/QUITIZZ_PHASE2B_REVIEW.md
```

realtime.py changes only its contract comment; template edits only version the transport asset; guides/docs explain access/readiness. Host count JS, HTTP/gameplay services, signals/broadcast implementation, models/migrations, settings/dependencies and operational configs remain byte-identical to session start. Original dirty checkout preserved. No Phase 2C, Attendance/DTR, secrets/.env/worktree logs, unrelated change, stage, commit, push, reset/clean/stash/amend/rebase/revert/force, deployment, restart or normal-local migration.

Final `git status --short` (same paths as session start):

```text
 M CHANGE_LOG.md
 M HANDOFF.md
 M TEACHERMATEPLUS_CONTEXT.md
 M apps/admin_portal/help_guide.py
 M apps/faculty_portal/help_guide.py
 M apps/quitizz/apps.py
 M apps/quitizz/gameplay.py
 M apps/quitizz/public_views.py
 M apps/quitizz/tests_gameplay.py
 M apps/quitizz/urls.py
 M apps/quitizz/views.py
 M config/asgi.py
 M config/settings/base.py
 M ops/nginx/teachermateplus-staging.conf
 M ops/nginx/teachermateplus.conf
 M ops/systemd/teachermateplus-gunicorn.service
 M ops/systemd/teachermateplus-staging-gunicorn.service
 M requirements/base.txt
 M static/js/quitizz_host.js
 M static/js/quitizz_play.js
 M templates/quitizz/host.html
 M templates/quitizz/play.html
?? apps/quitizz/checks.py
?? apps/quitizz/consumers.py
?? apps/quitizz/realtime.py
?? apps/quitizz/routing.py
?? apps/quitizz/signals.py
?? apps/quitizz/tests_realtime.py
?? docs/QUITIZZ_PHASE2B_REVIEW.md
?? frontend/quitizz/
?? ops/systemd/teachermateplus-asgi.service
?? ops/systemd/teachermateplus-staging-asgi.service
?? static/js/quitizz_realtime.js
```

Pending: independent re-review and all prior real Redis, MariaDB/InnoDB, authenticated browser/mobile/QR/Wi-Fi/keyboard/HTTPS/Nginx/systemd acceptance; unrelated Admin guide drift and earlier handoff items remain unresolved. Full Faculty grading smoke flows were not rerun for this isolated transport change. Next: independently review complete uncommitted diff, especially provisional accept, outbound direct DENY and pending group ownership. Operational acceptance/publication require separate authorized gates.

---

# Historical TMP-QUITIZZ-12-PHASE2B-REMEDIATION — 2026-10-10

This section records the current remediation and supersedes the earlier implementation verdict/query counts below. The earlier report is retained as historical evidence and for unresolved operational acceptance.

Verdict **A - Remediation complete and ready for independent re-review**. Final combined Django **173/173 PASS**, including all **153 QuiTizz** tests and **20** retained feature/RBAC/menu/Faculty-guide/configuration regressions; Node **11/11 PASS**. This establishes source/disposable-SQLite/in-memory/Node behavior, not real Redis, MariaDB/InnoDB, browser or deployed acceptance.

## Root causes and remediation

The original consumer authorized once, then awaited group registration and accepted. A committed removal or feature change could broadcast before the relevant revocation group existed; connect never checked the DB again. The former synchronous connect handler also kept a queued revocation behind admission. Heartbeat unconditionally re-added groups, including after a revoke or lost membership/notification.

Admission now runs in one owned, cancellable task while the normal Channels dispatcher remains available for revocation. It performs initial throttled authorization, registers internal server-selected groups, and performs a second fresh authorization check immediately before accept/ready. Membership identity must match; TTL must be positive. Gameplay forwarding is suppressed until admission, and after revocation. A revocation during pending admission sets the revoked flag, cancels/awaits admission, removes memberships and closes without ready. Rejection, transport failure and disconnect clean up immediately, including a group add that applied before raising. No additional admission throttle consumption is introduced by the recheck.

The participant recheck re-resolves current feature/scope/session and verifies the same session-bound credential against current participant removal, expiry and digest. The host recheck reloads the configured persisted Django SessionStore and authenticated user, then validates current active user, selected tenant/campus, active scope, ownership, exact assigned faculty/host permission and applicable direct DENY. No authorization shortcut or role/superuser bypass was added.

| Revocation timing | Admission behavior |
|---|---|
| Before initial authorization | Current feature/session/identity/RBAC denies admission; no ready |
| After authorization, before the first group subscription | Any missed notification is covered by the authoritative check after registration; rejection discards internal memberships |
| After registration, before accept | Authoritative recheck rejects even with notification loss; a delivered revoke can concurrently cancel pending authorization/accept |
| During pending accept | Normal revoke dispatch cancels admission and removes memberships; no ready/gameplay is emitted |
| Fully accepted | Existing committed participant/feature notifications mark revoked, immediately discard memberships and close; queued gameplay and stale ping are ignored |
| Membership/revocation notification lost after connection | Heartbeat registers internally and authoritatively rechecks before pong; rejection discards membership and closes. HTTP recovery remains available |

The subscription-before-check ordering removes the uncovered authorization-to-subscription window. The DB, Redis and ASGI are not one atomic distributed transaction: subsequent revocation uses the installed notification path, with bounded heartbeat/HTTP recovery for transport loss. No per-gameplay-event DB check, countdown loop or new polling cadence was introduced.

The host-count root cause was unconditional HTTP render after a newer same-question WebSocket count. A client-local `answerRevision` now increments on matching question UUID/lifecycle-version answer events. Each HTTP request captures that revision. Only a response for the same current question/version overtaken by an answer event preserves the larger current count; lower lifecycle versions are ignored. New question/version identity permits canonical resets, and fresh recovery without an intervening event remains canonical, including correction downward. No DB marker/schema change is needed. The host asset URL is versioned for the corrected script.

## Permanent regression coverage

Added eight Django tests to `apps/quitizz/tests_realtime.py`: participant removal before first subscription; feature OFF before host subscription; removal and feature OFF after registration with deliberately lost notifications; participant/host revoke while accept is pending; fresh host user/direct-DENY/selected-scope checks; cleanup after a partially applied group-add failure; heartbeat after lost membership/notification. Assertions cover rejected/closed sockets, no ready/pong/later gameplay, immediate membership cleanup and no restoration by stale ping. Existing valid host/player, fully connected removal/OFF, origin/RBAC/privacy, immutable HTTP mutations, rollback, expiry, query-growth and 100-client tests remain included.

Added five Node tests executing the actual host script: HTTP 1 -> realtime 2 -> delayed HTTP 1 retains 2; next question resets 0 and rejects prior-question events/HTTP; lifecycle identity permits canonical correction; fresh reconnect recovery corrects either direction; out-of-order answer counts and subsequent canonical recovery. Retained six transport/reconnect/coalescing/security/fallback tests.

## Current validation and environment

All Django commands use the existing external venv/settings described in the historical report: dotenv disabled, SQLite default/TEST `:memory:`, isolated LocMem caches/in-memory Channels, external logs and no normal-local DB use. Disposable test migrations are permitted; no normal-local migrations were applied. External remediation evidence directory: `C:\Users\Lenovo\AppData\Local\Temp\tmp-quitizz-phase2b-remediation-20261010`.

Final combined run: **173/173 PASS**, zero failures/errors/skips, **134.851s**, exit 0; all eight new Django regression methods and all existing realtime/security tests included. Six expected rejected-CSRF warnings and three injected transport-loss warnings; zero system-check issues. Node: **11/11 PASS**, zero failures/skips, 218.0823ms. Check: zero issues; migration drift: `No changes detected`; migration plan: exit 0 against an empty disposable default DB; four JS syntax checks and tracked/all-untracked whitespace checks PASS. The plan lists the existing graph because that DB is empty; it is not a production/local pending-migration audit. **No migrations are required.** Disposable test DB destroyed after the passing run; no normal-local migrations applied.

Initial focused realtime run was superseded: 36 tests, 27 passed, 8 failures and 1 error, 35.641s, exit 1. Valid hosts failed because the reload attempted to instantiate Channels' lazy session wrapper. The first aggregate was also superseded: 173 tests, 164 passed, 8 failures and 1 error, 137.615s, exit 1; Django `get_user` requires an object with `.session`, rather than a SessionStore directly. Corrected to use the configured SessionStore and a request-like session wrapper; the final aggregate rechecks valid hosts and all affected regressions. Race assertions now require authorization close code 4403 to exclude accidental transport rejection. The first Node attempt was stopped after a test-harness promise-order deadlock; correcting response selection yielded the final 11/11 pass. Superseded attempts are not counted as passing evidence.

Exact current combined command (same retained labels as the historical report):

```powershell
$taskDir = 'C:\Users\Lenovo\AppData\Local\Temp\tmp-quitizz-phase2b-20261010'
$python = Join-Path $taskDir 'venv\Scripts\python.exe'
$env:PYTHONPATH = $taskDir
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUNBUFFERED = '1'
$env:PYTHON_DOTENV_DISABLED = '1'
$env:DJANGO_LOG_DIR = Join-Path $taskDir 'logs'
$env:DJANGO_ENV = 'local'
$env:DB_ENGINE = 'django.db.backends.sqlite3'
$env:DB_NAME = ':memory:'
& $python manage.py test apps.quitizz apps.core.tests_menu_performance apps.core.tests_settings apps.admin_portal.tests_roles.RolePermissionBoundaryTests apps.admin_portal.tests_users.UserRolePermissionSeparationTests apps.faculty_portal.tests_help_guide apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_configurable_features_shows_single_device_login_setting apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_configurable_features_renders_standard_cards_for_targeted_sections apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_configurable_features_can_store_assignment_workflow_settings apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_configurable_features_rejects_invalid_non_compliance_notice_timing --settings=quitizz_phase2b_settings --noinput -v 1
& $python manage.py check --settings=quitizz_phase2b_settings
& $python manage.py makemigrations --check --dry-run --settings=quitizz_phase2b_settings
& $python manage.py migrate --plan --settings=quitizz_phase2b_settings
node --test frontend/quitizz/realtime.test.mjs
node --check static/js/quitizz.js
node --check static/js/quitizz_host.js
node --check static/js/quitizz_play.js
node --check static/js/quitizz_realtime.js
git diff --check
```

Focused superseded command: `& $python manage.py test apps.quitizz.tests_realtime --settings=quitizz_phase2b_settings --noinput -v 1`. Evidence: `final-combined.txt`, `superseded-combined.txt` and `focused-realtime.txt` in the external evidence directory. Test counts overlap and must not be added. New admission tests were executed in the passing full aggregate rather than claimed from the superseded focused run. Untracked whitespace command: `git diff --no-index --check -- /dev/null <each of the 11 exact untracked paths in the retained inventory below>`; no whitespace diagnostics, normal no-index difference exit 1 and Git LF/CRLF notices only.

## Current performance, scope and handoff

Current aggregate query/performance output:

| Operation | Starting Phase 2B | Current, each of 1 / 20 / 100 participants |
|---|---:|---:|
| Host ASGI handshake | 16 | 32 (+16, fresh authorization including persisted user/session reload) |
| Player ASGI handshake | 3 | 6 (+3, fresh session/participant authorization) |
| Player lobby recovery service | 3 | 3 |
| Host lobby recovery service | 2 | 2 |
| Open player state service | 5 | 5 |
| Accepted answer, before executed commit hook | 14 | 14 |
| Accepted answer, including executed commit feature check | 15 | 15 |
| Authorized group forwarding | 0 | 0 |

The current run also passed 100 concurrent authorized WebsocketCommunicator clients receiving identical shared safe invalidation and disconnecting cleanly. Counts are source/disposable-SQLite/in-memory evidence, not Redis or MariaDB throughput. The extra authorization pass is bounded per admission. Heartbeat now calls the same fresh authorization routine after registration (previously no authorization query), without connection throttling or per-player queries on shared forwarding. Client heartbeat remains thirty seconds with existing server cadence enforcement; no new polling or per-answer roster fetch. Feature global-fallback settings can add a bounded query independently of participant population.

Verified starting/final branch `feat/quitizz`, unchanged full HEAD `b330f8ddd0b4216c033c5bb46a19087a03baf3c8`, existing 33-path Phase 2B inventory matching the historical report. Final status remains **22 tracked modifications + 11 untracked files, all unstaged/uncommitted**, with an empty index and the same exact status paths as the retained inventory below. Captured starting SHA256 inventory/status/tracked diff outside the worktree. Only the 11 remediation paths changed; the other 22 starting Phase 2B files are byte-identical. No prior Phase 2B content was discarded. Reviewed the entire resulting tracked diff and untracked source/tests/configs; no additional authorization, answer-key/privacy, scope, secret/log or unrelated implementation change was found.

Exact remediation paths (all already existed at session start; no new worktree file): `apps/quitizz/consumers.py`, `apps/quitizz/tests_realtime.py`, `static/js/quitizz_host.js`, `frontend/quitizz/realtime.test.mjs`, `templates/quitizz/host.html`, `apps/admin_portal/help_guide.py`, `apps/faculty_portal/help_guide.py`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`, `HANDOFF.md`, `docs/QUITIZZ_PHASE2B_REVIEW.md`. Other Phase 2B files, including realtime/signals, operational sources/settings/dependencies/player JS and all models/migrations, remain byte-identical to the captured starting working tree.

No Phase 2C, Attendance/DTR, secrets/.env/logs, staging, commit, push, deployment, restart or normal-local migration work. HTTP/domain services and the DB remain authoritative; Redis remains transport/throttle; all broadcasts remain `transaction.on_commit`; event privacy and strict origin/direct-DENY protections remain unchanged. Real Redis, MariaDB/InnoDB, authenticated browser/mobile/QR/Wi-Fi/keyboard and deployment acceptance remain unexecuted, along with the prior unrelated Admin guide drift and other preserved handoff items. Next gate: independent re-review of the complete uncommitted diff. Publication and operational acceptance require their separately authorized gates.

---

# Historical QuiTizz Phase 2B realtime review — 2026-10-10

Verdict **B — Phase 2B complete with non-blocking operational acceptance observations**. Combined regression run **160/160 PASS**, final database-free transport/configuration run **10/10 PASS**, and Node transport run **6/6 PASS**. Counts overlap and must not be added. This is source/disposable-SQLite/in-memory evidence, not real Redis, MariaDB, browser or deployed acceptance.

## Git and authorization

Worktree `D:\codex-worktrees\TMP-QuiTizz`; starting branch `feat/quitizz`, clean starting HEAD `b330f8ddd0b4216c033c5bb46a19087a03baf3c8`. No staging, commit, push, deployment, operational restart, normal-local migration, secrets/.env changes, Attendance/DTR changes or Phase 2C effects are authorized or performed. Runtime settings/logs/packages are outside the worktree. `logs/system.log` is excluded. The original dirty checkout was not edited.

## Dependencies

Added to `requirements/base.txt`: `channels==4.3.2`, `channels-redis==4.3.0`, `daphne==4.2.3`, `redis>=8.0` (the Redis constraint already existed in production requirements). This makes local and production imports consistent. No Celery or job queue.

Selected current published 4.x releases after reading [Channels documentation](https://channels.readthedocs.io/en/stable/), [Channels package metadata](https://pypi.org/project/channels/), [channels-redis metadata](https://pypi.org/project/channels-redis/) and [Daphne metadata](https://pypi.org/project/daphne/). Installed Channels metadata explicitly includes Django 5.2 and Python 3.14. channels-redis documentation advertises testing through Python 3.13; local Python 3.14 installation/import evidence does not establish Redis integration support. Daphne installs and starts in the actual environment. Exact direct transport versions are pinned for this gate.

External validation venv: `C:\Users\Lenovo\AppData\Local\Temp\tmp-quitizz-phase2b-20261010\venv`, created with `py -m venv --system-site-packages`. Base interpreter Python **3.14.3** and Django **5.2.12** are reused read-only. Venv installs: Channels 4.3.2, channels-redis 4.3.0, Daphne 4.2.3, redis-py 8.1.0, Twisted 26.4.0; transitive cryptography 50.0.2 overlays the base 46.0.7 inside this venv only. `pip check` reports no broken requirements. No global package was modified.

## Architecture and identity

`config.asgi.application` is a `ProtocolTypeRouter`: existing Django HTTP application plus `SameOriginValidator(AuthMiddlewareStack(URLRouter(...)))` for WebSockets. Daphne is a separate process; Gunicorn remains the existing WSGI HTTP server.

| Route | Authorization |
|---|---|
| `/ws/quitizz/<session UUID>/host/` | Django authenticated active user, persisted HTTP-selected tenant/campus, session host ownership, active scope, feature ON, exact assigned Faculty Portal and `quitizz.host` permissions; broad direct DENY wins |
| `/ws/quitizz/<session UUID>/player/` | Session-specific HttpOnly reconnect cookie bridge, existing session-bound HMAC credential validation, participant not removed/expired, active session/scope and feature ON |
| `/quitizz/play/<session UUID>/socket/` | New CSRF-protected HTTP POST; validates the original participant reconnect cookie and issues the bridge cookie; returns only `{ready: true}` |

The original reconnect cookie remains scoped to HTTP play. The bridge has a separate cookie name containing `token`, `HttpOnly`, `SameSite=Strict`, session-specific player WebSocket path and Secure under production/HTTPS. The same existing credential is copied server-side; JS never sees it, and URLs never contain credentials. Nicknames cannot authenticate. Host identity uses the normal Django login/session cookie and does not trust route/client-supplied tenant IDs. Login and participant/session expiry bound socket lifetime through a single expiry callback per connection.

Server-only groups: session host, session players, participant revocation, tenant revocation and global revocation. Names derive solely from integer DB identifiers and never appear in client JSON. Clients cannot choose groups or submit mutations. Only literal `ping` is accepted (at most once per ten seconds), with a `pong` response and group membership refresh; invalid/binary/JSON mutation messages close the connection.

HTTP mutations retain all Phase 2A row locks, lifecycle/version checks, timing, scoring and idempotence. Services call a small realtime abstraction that registers `transaction.on_commit`. The callback rechecks the tenant feature once, then sends one shared group message per permitted audience. Notification failures are swallowed without logging payloads, credentials, connection strings or exception text. No score/lifecycle mutation is retried, and rollback discards callbacks.

## Redis and throttling

Environment controls: `QUITIZZ_REDIS_URL`, `QUITIZZ_REDIS_NAMESPACE` and `QUITIZZ_DEPLOYMENT` (`local`, `staging`, `production`; default `DJANGO_ENV`). A configured URL requires an explicit alphanumeric/dash/underscore namespace. All deployment processes must use identical values within a deployment.

Channel layer: `channels_redis.core.RedisChannelLayer`, prefix `tmp:<deployment>:<namespace>:quitizz:channels`, message expiry 60s, group expiry 3600s and capacity 256. Connect timeout is 2s; socket read timeout is 10s, above Channels' healthy five-second blocking receive. Best-effort publication has its own two-second asyncio deadline per group. Client heartbeat every 30s renews group subscriptions, including after Redis group loss/restart. There is no game state, accepted answer, score, lifecycle or historical result in Redis.

Dedicated cache alias `quitizz`: Django `RedisCache`, prefix `tmp:<deployment>:<namespace>:quitizz:throttle`; two-second connect/read timeout. Throttle uses shared atomic add/increment, hashed identity/session/bucket keys and the original bucket TTL (61s for a 60s budget). Counter expiry races re-add or increment; Redis failure fails closed with the existing generic rate-limit response. An outage can pause HTTP gameplay/recovery until shared Redis returns; it does not corrupt database state or silently fall back to worker-local enforcement.

Without Redis, development/tests deliberately use dedicated LocMem cache and in-memory channel layer. These are **per-process only**, never shared production proof. Production/staging-like configuration emits `quitizz.W001` for absent shared backends and `quitizz.W002` for missing/generic namespace. A configured Redis URL with no valid namespace fails settings import. Systemd sources supply different deployment dimensions for both HTTP and ASGI in staging/production, so their prefixes cannot collide when those supplied dimensions are retained.

## Events and privacy

| Event | Audience | Additional payload |
|---|---|---|
| `participant_joined` | Host | None |
| `participant_removed` | Host | Position |
| `answer_received` | Host | Question UUID, canonical answered count |
| `joining_opened`, `joining_closed` | Host | Position |
| `session_started`, `question_prepared`, `question_closed`, `answer_revealed`, `session_completed`, `session_cancelled` | Host and players | Position |
| `question_opened` | Host and players | Question UUID, position, committed server deadline |
| `ready` | Authorized connection | Current version; fetch canonical HTTP state |
| `pong` | Connection | None |
| `feature_unavailable` | Tenant/global invalidation | Generic event only, then close |
| `participant_unavailable` | Removed/expired connection | Generic event only, then close |

All gameplay notifications have a version. There are no prompt/choice/key/correctness/score/rank/nickname/list payloads in player groups, including after reveal. Reveal only invalidates state; correct answer and each player's private feedback become available through the existing authenticated HTTP endpoint after the committed reveal. No participant choice is exposed to hosts in `answer_received`. A first accepted response schedules one host event after commit; duplicate/idempotent retries return before event registration. Host HTTP state recovers the canonical answered count.

## Recovery and security

WebSocket messages are metadata invalidations. Initial ready/reconnect and every lifecycle event fetch canonical HTTP state. Lower-version notifications are ignored; same-version lobby notifications and version gaps recover state. Bursts coalesce over 100ms, and transport recovery requests are serialized. Host accepted-answer events update only the matching current question/version count, taking the maximum for out-of-order counts, without fetching participant lists for each answer.

Connected transport replaces five-second routine polling with sixty-second canonical recovery. This bounded recovery detects silent event loss and missed revocation, plus current HTTP authorization. Disconnected transport polls at five seconds and reconnects with jittered exponential delay up to approximately 30s. Thirty-second heartbeat and fifteen-second pong timeout detect half-open connections. Foreground/online changes trigger recovery; pagehide stops timers/socket. Participant countdown remains a local 250ms display derived from server time/deadline; no server countdown loop or per-second message/poll exists.

Host controls and player answers remain HTTP. Host controls now recover/re-render without normal manual page refresh; state versions, QR joining, participants and controls are rebuilt using text/DOM APIs. Terminal/unavailable state disables gameplay. Both screens show realtime/reconnecting status. JavaScript URLs are versioned.

Origin validator requires one HTTP(S) Origin matching the actual Host and normalized port, plus Django allowed-host validation. The scheme must match the ASGI transport, or HTTPS when secure cookies are required behind TLS termination. Missing/null/hostile/malformed/userinfo/path/foreign-port origins reject before authentication/group registration. No forwarded arbitrary origin is trusted. Production Django settings already prohibit wildcard allowed hosts. Nginx forwards the real Host and replaces forwarded client IP for the dedicated loopback ASGI service.

Feature changes/deletion register tenant/global revocation after commit. New sockets and HTTP mutations check DB feature state, broadcaster checks it again after commit, and sockets close on the generic invalidation. Tenant overrides are rechecked on reconnect. If Redis loses revocation, safe metadata cannot authorize content/mutations; canonical HTTP recovery detects unavailable state. Removal additionally sends a participant-specific revoke and later connect/HTTP rejects its saved identity. Expiry closes an active socket at its authorized expiry and rejects HTTP/new connects. Historical data is preserved.

## Operational sources — not executed

Added separate `ops/systemd/teachermateplus-asgi.service` and `ops/systemd/teachermateplus-staging-asgi.service`. Daphne binds **127.0.0.1:8001** production / **127.0.0.1:8002** staging, reuses each existing service user, workdir and environment-file location, supports proxy headers/heartbeat and an eight-hour maximum WebSocket lifetime. Gunicorn units retain their exact existing ExecStart; only the deployment namespace dimension is added to match ASGI.

Both Nginx source configs add `/ws/quitizz/` proxying with HTTP/1.1, Upgrade, Connection upgrade, Host, forwarded protocol/client IP, 90s read/send timeout and disabled buffering. Other HTTP continues to Gunicorn. Existing deploy script is unchanged and does **not** activate/restart the ASGI service. Activation is a later authorized operational gate; install dependencies, set matching Redis/namespace/deployment values in both services' existing environment file, review actual HTTPS vhost/proxy ownership, validate Nginx/systemd, then explicitly enable/restart the separate service under that later gate. No `/etc` file or real service has been changed here.

## Remaining operational acceptance

Real Redis fan-out/shared-counter/TTL/restart behavior, MariaDB/InnoDB locks/races and burst throughput, actual Nginx/systemd/HTTPS deployment, phone QR, Wi-Fi, keyboard and mobile browser acceptance remain unexecuted. Tests establish source, disposable SQLite and in-memory transport behavior only. Session-row serialization and one indexed answered-count query per newly accepted answer are the known throughput points to measure on InnoDB; neither DB queries nor participant updates grow one-per-player per answer. Source architecture supports independent groups and session locks across simultaneous games.

Previous unrelated Admin guide assertion drift and prior browser/database acceptance items in HANDOFF remain unresolved. Phase 2C fireworks, podium, confetti, sounds and visual polish remain unimplemented.

## Executed validation

All Django commands ran from the isolated worktree with the external venv interpreter and external `quitizz_phase2b_settings.py`. It imports base settings, overrides default and TEST DB to SQLite `:memory:` (Django shared-memory test DB), isolates both default/quitizz LocMem cache aliases and the in-memory channel layer, uses local-memory email, MD5 test hashing, DEBUG=False and synthetic allowed hosts. Dotenv is disabled and logs are external. Disposable full migration setup and the retained historical 0003→0004 migration regression do not apply migrations to the normal local database.

```powershell
$taskDir = 'C:\Users\Lenovo\AppData\Local\Temp\tmp-quitizz-phase2b-20261010'
$python = Join-Path $taskDir 'venv\Scripts\python.exe'
$env:PYTHONPATH = $taskDir
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHON_DOTENV_DISABLED = '1'
$env:DJANGO_LOG_DIR = Join-Path $taskDir 'logs'
$env:DJANGO_ENV = 'local'
$env:DB_ENGINE = 'django.db.backends.sqlite3'
$env:DB_NAME = ':memory:'
```

Exact combined command (log `final-combined.txt`):

```powershell
& $python manage.py test apps.quitizz apps.core.tests_menu_performance apps.core.tests_settings apps.admin_portal.tests_roles.RolePermissionBoundaryTests apps.admin_portal.tests_users.UserRolePermissionSeparationTests apps.faculty_portal.tests_help_guide apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_configurable_features_shows_single_device_login_setting apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_configurable_features_renders_standard_cards_for_targeted_sections apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_configurable_features_can_store_assignment_workflow_settings apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_configurable_features_rejects_invalid_non_compliance_notice_timing --settings=quitizz_phase2b_settings --noinput -v 1
```

Result: **160 tests, 160 PASS, zero failures/errors/skips**, 123.600s test time, exit 0. Includes all 140 QuiTizz tests collected at that point plus 20 retained menu/settings/RBAC/Faculty guide/configuration regressions. Six expected rejected-CSRF warnings and three deliberately injected notification-loss warnings; zero system-check issues. The final queued-revocation guard, bounded publisher/read-timeout correction and TLS-origin scheme checks were then validated by the 10-test database-free run below, including five additional focused regressions. No combined count is claimed for these overlapping runs. Full Faculty grade flows and the pre-existing unrelated Admin guide assertion drift were not rerun.

| Exact command | Result / failures, skips, warnings |
|---|---|
| `py -m venv --system-site-packages C:\Users\Lenovo\AppData\Local\Temp\tmp-quitizz-phase2b-20261010\venv` | PASS; created isolated external venv; zero tests |
| `& $python -m pip install 'channels==4.3.2' 'channels-redis==4.3.0' 'daphne==4.2.3' 'redis>=8.0'` | PASS; exact versions above; informational pip-upgrade notice; global dependencies preserved |
| `& $python -m pip check` | PASS; no broken requirements; zero tests |
| `& $python manage.py test apps.quitizz.tests_realtime --settings=quitizz_phase2b_settings --noinput -v 2` | First attempt FAIL/superseded: 17 collected, 3 passed, 14 errors, zero skips. File-backed SQLite was closed by Channels connection cleanup inside TestCase. External TEST DB changed to shared memory; removed file-backed disposable DB |
| `& $python manage.py test apps.quitizz.tests_realtime --settings=quitizz_phase2b_settings --noinput -v 1` | Second attempt FAIL/superseded: 23 tests, 21 passed, 2 errors, zero skips, 31.478s. One-second default communicator timeout insufficient for 100 simultaneous test admissions; empty-body Django Client POST omitted Content-Type. Fixtures corrected to 20s admission timeout and a nonempty harmless body; final aggregate passes both. One expected rejected-CSRF warning; injected transport warnings |
| Combined command above | PASS 160/160; details above |
| `& $python manage.py test apps.quitizz.tests_realtime.RealtimeConfigurationTests --settings=quitizz_phase2b_settings --noinput -v 2` | Intermediate PASS 6/6, 1.483s; later PASS 8/8, 3.052s; final superseding PASS **10/10**, 2.768s, zero failures/errors/skips/warnings, explicitly skipped unused DB setup. Covers namespace isolation, production warnings, TTL/alias/fail-closed cache, payload whitelist, queued revoke, real in-memory publisher and cancelled hung publisher; checks read timeout against installed RedisChannelLayer blocking wait and HTTPS/cross-scheme origins through TLS termination; final log `final-origin-transport-configuration.txt` |
| `& $python manage.py check --settings=quitizz_phase2b_settings` | PASS; zero issues; zero tests |
| `& $python manage.py makemigrations --check --dry-run --settings=quitizz_phase2b_settings` | PASS; No changes detected; zero tests |
| `& $python manage.py migrate --plan --settings=quitizz_phase2b_settings` | PASS; existing graph listed against isolated empty SQLite; plan only, zero migrations applied, zero tests; external `migration-plan-final.txt` |
| `node --check static/js/quitizz.js` | PASS; zero tests |
| `node --check static/js/quitizz_host.js` | PASS; zero tests |
| `node --check static/js/quitizz_play.js` | PASS; zero tests |
| `node --check static/js/quitizz_realtime.js` | PASS; zero tests |
| `node --test frontend/quitizz/realtime.test.mjs` | Initial FAIL/superseded: 6 tests, 4 passed, 2 failed plus async fixture errors because spreading Math removed non-enumerable max. Corrected fixture; final PASS **6/6**, zero failures/skips/warnings, 239.3444ms |
| `& $python -c "import config.asgi; from daphne.server import Server; from channels_redis.core import RedisChannelLayer; print('ASGI HTTP/WebSocket + Daphne + RedisChannelLayer imports PASS'); print(config.asgi.application.application_mapping.keys())"` with `DJANGO_SETTINGS_MODULE=quitizz_phase2b_settings` | PASS; HTTP/WebSocket mapping and dependency imports; repeated final import after correction; zero tests |
| `& (Join-Path $taskDir 'venv\Scripts\daphne.exe') --help` | PASS; validated checked-in websocket_timeout/proxy-headers/ping flags; zero tests |
| `& $python (Join-Path $taskDir 'validate_startup.py')` | Initial FAIL/superseded: one-second HTTP probe timed out during cold startup. Corrected 30s probe PASS: ephemeral loopback Daphne returned expected HTTP 404 from synthetic non-DB route, then exact child stopped. HTTP/2-disabled informational message only. No systemd/operational restart or deployment |
| `git diff --check` | PASS; no whitespace defects; ordinary Git LF→CRLF notices only |
| `git diff --no-index --check -- /dev/null <each exact created path below>` | PASS for all created files; no whitespace defects; ordinary no-index difference status is not a test failure |

PowerShell's `NativeCommandError` decoration of normal test-runner stderr in the saved log is not an application exception or failed process result. Reported PASS commands exited 0. The final shared-memory test database was destroyed; the first file-backed test.sqlite3 is absent. No persistent runtime database was queried or migrated.

## Measured query/performance evidence

| Operation | 1 player | 20 players | 100 players |
|---|---:|---:|---:|
| Actual host handshake (ASGI auth/session + authorization) | 16 | 16 | 16 |
| Actual player handshake | 3 | 3 | 3 |
| Participant lobby state service recovery | 3 | 3 | 3 |
| Host lobby state service recovery | 2 | 2 | 2 |
| Accepted response service inside test transaction, without executing commit hooks | 14 | 14 | 14 |
| Accepted response including manually executed commit notification feature check | 15 | 15 | 15 |

Open-question participant HTTP state service: **5 queries**. Consumer forwarding an authorized safe message: **0 queries**. Service counts exclude HTTP request middleware and are not live throughput measurements. Query counts include transaction/savepoint work where applicable and use tenant-local feature fixtures; absent tenant override can add one global-fallback feature query, independent of population.

The full aggregate exercised **100 concurrent authorized WebsocketCommunicator clients**, all receiving the same single safe shared session-start invalidation, then clean disconnect. Separate tests cover independent session group isolation. A sequential 100-response run produced exactly 100 host notifications, no duplicate retry event, one participant totals update per accepted response, deterministic 100,000 total points and ranks 1–100. This is structural/bounded-query/in-memory evidence; no networked Redis or MariaDB concurrent-answer throughput claim.

## Final file inventory and review

Final branch `feat/quitizz`; final full HEAD remains `b330f8ddd0b4216c033c5bb46a19087a03baf3c8`. Final working state: **22 modified tracked files + 11 created untracked files**, all unstaged. Index is empty. Exact `git status --short --untracked-files=all` inventory:

```text
 M CHANGE_LOG.md
 M HANDOFF.md
 M TEACHERMATEPLUS_CONTEXT.md
 M apps/admin_portal/help_guide.py
 M apps/faculty_portal/help_guide.py
 M apps/quitizz/apps.py
 M apps/quitizz/gameplay.py
 M apps/quitizz/public_views.py
 M apps/quitizz/tests_gameplay.py
 M apps/quitizz/urls.py
 M apps/quitizz/views.py
 M config/asgi.py
 M config/settings/base.py
 M ops/nginx/teachermateplus-staging.conf
 M ops/nginx/teachermateplus.conf
 M ops/systemd/teachermateplus-gunicorn.service
 M ops/systemd/teachermateplus-staging-gunicorn.service
 M requirements/base.txt
 M static/js/quitizz_host.js
 M static/js/quitizz_play.js
 M templates/quitizz/host.html
 M templates/quitizz/play.html
?? apps/quitizz/checks.py
?? apps/quitizz/consumers.py
?? apps/quitizz/realtime.py
?? apps/quitizz/routing.py
?? apps/quitizz/signals.py
?? apps/quitizz/tests_realtime.py
?? docs/QUITIZZ_PHASE2B_REVIEW.md
?? frontend/quitizz/realtime.test.mjs
?? ops/systemd/teachermateplus-asgi.service
?? ops/systemd/teachermateplus-staging-asgi.service
?? static/js/quitizz_realtime.js
```

Diff stat: tracked **22 files, 293 insertions, 64 deletions**; created **11 files, 1113 lines**; complete review **33 files, 1406 insertions, 64 deletions**.

Reviewed tracked and new source for key/identity leakage, group authorization, origin/session isolation, cookie exposure, direct DENY/OFF enforcement, committed publication, Redis authority, consumer N+1/ticks, polling and operational/secrets scope. No blocking source finding remains. Corrected review findings: Redis read timeout must exceed blocking receive; publication independently bounded; revoke drops queued notifications; secure-cookie HTTPS/cross-scheme protection is enforced behind TLS termination; explicit WS proxy headers avoid duplicate inherited Host directives; host roster refresh restores focus without indefinitely deferring joins/removals. Operational acceptance gaps above remain.

**No migrations are required.** No new model/schema or migration file. Normal-local migrations were **NOT applied**. No stage/commit/push/deploy/restart/secrets/.env changes; `logs/system.log` excluded; Faculty Attendance/DTR and unrelated work preserved; Phase 2C visual effects remain unimplemented.

Next: independently review this exact diff/report, then separately authorize real Redis isolation/TTL/outage/restart acceptance, MariaDB/InnoDB races and 100-player bursts, and actual mobile/browser/QR/Wi-Fi/keyboard behavior. Operational configuration activation and any publication require their later gates; do not run the deployment script under this session's authorization.

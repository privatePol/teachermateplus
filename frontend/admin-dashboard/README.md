# Admin Dashboard validation

`dashboard.test.cjs` tests the actual polling/drawer scripts with JSDOM and fake timers. It does not wait ten real minutes. `responsive.test.cjs` runs real headless Chrome against Django-rendered synthetic HTML at 375, 390, 440, 767, 768, 992, 1280 and 1440px, with authorized and direct-DENY menus. It checks summary equality, PDF row/card order and proportions, natural top-aligned card heights, pending queue links/permissions, 44px named icon links and native tooltips, help-icon print suppression, overflow, internal table scrolling, drawer focus/Tab/Escape/backdrop/close and navigation visibility. It writes screenshots and JSON outside the repository. At 1200px and above, the context row has four columns and the queue row has three; below that, the queue row stacks, with two context columns from 768px.

Generate fixtures using the ordinary disposable Django test runner:

```powershell
$env:TMP_ADMIN_EVIDENCE = "$env:TEMP/tmp-admin-dashboard-validation"
# Create this directory first. Use the approved validation interpreter/environment.
python -B manage.py test apps.admin_portal.tests_dashboard apps.admin_portal.tests_dashboard_layout --settings=duplicate_test_settings --noinput
$env:TMP_JSDOM_PATH = "D:/codex-worktrees/TMP-Grade-Filters-Performance/.validation-env/dom/node_modules/jsdom"
$env:TMP_ADMIN_BOOTSTRAP_CSS = "<local Bootstrap 5.3.3 bootstrap.min.css>"
$env:TMP_ADMIN_LOGO = "D:/teachermateplus/media/logos/teachermate_logo_text_official.png"
node --test frontend/admin-dashboard/dashboard.test.cjs frontend/admin-dashboard/responsive.test.cjs
```

Chrome defaults to `C:/Program Files/Google/Chrome/Application/chrome.exe`; override with `TMP_CHROME_BIN`. Missing evidence-directory/Chrome configuration is an explicit browser-test skip. The supplied local CSS must be the same Bootstrap 5.3.3 version used by the shared base. Offline fixtures omit the external Bootstrap JavaScript: these tests establish drawer/card behavior, not Bootstrap group-collapse network acceptance. Existing permission/feature filters render menus before export. Physical devices, other browser engines, staging configuration and database locking remain separate acceptance evidence.

This session additionally used external disposable probes/settings and temporary WSGI servers for the before/after PBKDF2 login and Chrome network waterfall. Their provenance, exact results, commands, limitations and paths are recorded in `docs/ADMIN_DASHBOARD_IMPLEMENTATION.md` and `HANDOFF.md`.


## UX refinement validation (2026-11-02)

The count-only GET `/admin-portal/dashboard/queue-counts/` returns both authorized pending queues. It uses the same scoped selectors as the dashboard, checks a signed user/tenant/campus/department/AY/term/access-scope token, recomputes effective permissions and checks the persisted session selection again before returning. Missing configuration is Unavailable, and a revoked queue is null. It never loads correction/CML request rows or performs correction-window maintenance. Scope-selection URL parameters are rejected without changing the selected session scope.

The queue pair and reopen fragment each keep a 600000ms timer. Hidden tabs pause; returning to visibility or window focus verifies access before restoring counts/actions. Twenty-second timeouts, HTTP/auth/scope errors, redirects, malformed data and stale response bodies cannot leave unverified actions. Storage and BroadcastChannel notifications invalidate cards across Admin tabs; signed scope validation and focus checks remain available when browser storage is blocked. Back/forward-cache restoration requires reload. No full-page reload or additional job is scheduled.

Both background routes retain the persisted absolute idle deadline. Their request-local session save is suppressed, including SAVE_EVERY_REQUEST, so a stale poll cannot overwrite a concurrent interactive scope/deadline. Existing independent session-registry reconciliation still runs; inherited sessions without saved scope selectors resolve through authorized defaults.

`dashboard.test.cjs` includes queue-only/mixed permissions, revocation, hidden/focus transitions, abort-ignoring late fetches/bodies, timeouts, storage/BroadcastChannel invalidation and unchanged reopen/drawer behavior. Browser evidence is offline headless Chrome against real Django-rendered synthetic authorized/denied fixtures, not authenticated staging or physical-device acceptance. Final commands, counts, baseline-equivalent failures and sequential SQL/timing evidence are recorded at the top of HANDOFF.md.


## Polling session-cookie regression (2026-11-02)

Suppressing the request-local session save alone does not suppress Django SessionMiddleware's Set-Cookie when SESSION_SAVE_EVERY_REQUEST is enabled. Both exact polling routes now also remove an existing configured session cookie and filter that response's later set_cookie calls for only that cookie name. Installed Django delete_cookie delegates to set_cookie, so expired redirects cannot delete a newer login cookie. Other cookies and interactive routes retain their normal behavior; middleware order and session security settings are unchanged.

`apps.admin_portal.tests_dashboard.DashboardPollingCookieTests` runs the real middleware stack with SAVE_EVERY_REQUEST. Separate Client cookie snapshots and delayed response-cookie delivery verify newer expiry/key preservation, subsequent authenticated requests, denial/expiry/scope errors, custom session-cookie names and unrelated cookie attributes. No SessionMiddleware method is replaced in these race tests. Dedicated suite: 10 passed; disabling only the new guard reproduces 18 failing endpoint subtests. See the newest HANDOFF.md entry for commands, broader results and the two unchanged baseline assertions. Existing Chrome layout evidence was not rerun for this server-cookie-only correction.

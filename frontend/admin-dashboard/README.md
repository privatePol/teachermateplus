# Admin Dashboard validation

`dashboard.test.cjs` tests the actual polling/drawer scripts with JSDOM and fake timers. It does not wait ten real minutes. `responsive.test.cjs` runs real headless Chrome against Django-rendered synthetic HTML at 375, 390, 440, 767, 768, 992, 1280 and 1440px, with authorized and direct-DENY menus. It checks summary equality, PDF row/card order and proportions, pending queue links/permissions, overflow, internal table scrolling, drawer focus/Tab/Escape/backdrop/close and navigation visibility. It writes screenshots and JSON outside the repository. At 1200px and above, the context row has four columns and the queue row has three; below that, the queue row stacks, with two context columns from 768px.

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

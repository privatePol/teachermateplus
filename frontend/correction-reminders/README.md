# Grade Correction reminder UI checks

`ui.test.cjs` runs real headless Chrome against Django-rendered synthetic fixtures.
It uses Bootstrap 5.3.3's actual collapse code, the shared feature-card CSS and the
page's inline script. It checks keyboard expansion, Expand All/Collapse All, the
saved-card anchor, accessible labels/descriptions, independent HTML form ownership,
CSRF field presence, responsive columns and overflow at 375, 768, 992 and 1440px.
The dedicated and invalid-settings pages are checked at 375 and 1440px. Screenshots
and Chrome version evidence are written outside the repository.

Use an isolated disposable SQLite test database and external log directory. Export
fixtures while running `apps.admin_portal.tests_correction_reminder_ui` by setting
`TMP_CORRECTION_UI_EVIDENCE` to an existing external directory. Place Bootstrap
5.3.3 `bootstrap.min.css` and `bootstrap.bundle.min.js` there, then run:

```powershell
$env:TMP_CORRECTION_UI_EVIDENCE = "D:/codex-tmp/grade-correction-ui-polish-20261103"
node --test frontend/correction-reminders/ui.test.cjs
```

Chrome defaults to `C:/Program Files/Google/Chrome/Application/chrome.exe`;
`TMP_CHROME_BIN` can override it. Missing fixture configuration or Chrome produces
an explicit skip. These offline fixtures establish layout and Bootstrap behavior;
authenticated POST, RBAC, tenant isolation, CSRF and audit behavior are covered by
the Django tests. They do not establish staging or physical-device acceptance.

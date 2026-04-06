# EduGradesPro Production Incident Runbook

This runbook is the operational response guide for production issues in EduGradesPro.

Use this when:
- `/admin-portal/` or `/faculty/` suddenly shows errors
- pages are timing out
- users report missing data or broken workflows
- a fresh deployment introduced a regression

This runbook assumes the Ubuntu deployment flow documented in [DEPLOYMENT_UBUNTU.md](/d:/edugradespro/docs/DEPLOYMENT_UBUNTU.md).

## 1. Incident Goals

During a live incident, the goals are:

1. protect production data
2. confirm the real impact and scope
3. restore service safely
4. preserve logs and evidence
5. avoid panic edits directly in production

## 2. First 10 Minutes

Do these first, in order:

1. Confirm the scope.
   - Is it Admin Portal only?
   - Is it Faculty Portal only?
   - Is it one campus, one tenant, or all users?
   - Is it one page only or a full outage?

2. Capture evidence before changing anything.
   - screenshot of the error page
   - exact URL
   - exact time the error happened
   - affected username and role
   - whether the issue started after a deployment or settings change

3. Freeze further risky changes.
   - do not keep redeploying randomly
   - do not edit production DB rows directly unless the cause is already clear
   - do not stack multiple fixes at once

4. Check whether this is a known infrastructure issue.
   - disk full
   - gunicorn stopped
   - nginx misroute
   - env/config missing
   - migration mismatch

5. Decide whether to:
   - keep investigating live, or
   - roll back immediately if the outage is severe and recent

## 3. Quick Severity Guide

Use this to decide how urgent the response should be.

### Severity 1
- both portals unavailable
- login broken for everyone
- grade submission/correction completely blocked near deadline
- data corruption suspected

Action:
- start incident response immediately
- consider rollback if a very recent deploy caused it

### Severity 2
- one portal broken
- one major workflow broken
- one campus/tenant blocked

Action:
- triage immediately
- hotfix may be safer than full rollback depending on scope

### Severity 3
- one page broken
- one report or monitor page failing
- partial feature regression with workaround

Action:
- collect logs
- fix in controlled hotfix cycle

## 4. What Not To Do

Avoid these unless the root cause is already proven:

- do not run direct SQL updates blindly
- do not delete migration files in production
- do not hard-reset the codebase without preserving evidence
- do not restart services repeatedly without reading logs
- do not mix rollback and partial code edits in one step

## 5. Production Evidence Checklist

Collect these before making changes:

1. screenshot of the error
2. exact URL
3. affected user, role, tenant, campus
4. exact timestamp
5. last successful action before the error
6. recent deployment or settings change
7. traceback or server log excerpt

## 6. Server Checks

### Application service

```bash
sudo systemctl status gunicorn
sudo journalctl -u gunicorn -n 200 --no-pager
```

### Web server

```bash
sudo systemctl status nginx
sudo nginx -t
sudo tail -n 200 /var/log/nginx/error.log
```

### Disk and memory pressure

```bash
df -h
free -m
```

### EduGradesPro logs

```bash
sudo ls -lah /var/log/edugradespro/
sudo tail -n 200 /var/log/edugradespro/*.log
```

### Recent release state

```bash
cd /opt/edugradespro
git log --oneline -n 10
git status --short
```

## 7. Django-Side Validation

Run these only after capturing evidence:

```bash
cd /opt/edugradespro
source .venv/bin/activate
python manage.py check
python manage.py showmigrations
python manage.py shell -c "from django.conf import settings; print(settings.DEBUG, settings.ALLOWED_HOSTS)"
```

Useful checks:

- `manage.py check` catches broken settings and model issues
- `showmigrations` confirms migration drift
- shell check confirms production config values are loading

## 8. EduGradesPro-Specific Triage

### If Admin Portal fails

Check:
- login lockout settings
- menu/permission changes
- active AY/Term and active grading period settings
- template governance and correction governance pages if the error started after policy edits

### If Faculty Portal fails

Check:
- accepted faculty assignments
- active grading period governance
- period lock / deadline settings
- correction or reopen flows
- prediction feature toggles if prediction pages are involved

### If grade submission or summary fails

Check:
- grading template assignment coverage
- reopened/correction state
- period lock/deadline records
- whether the period is currently closed by active grading period governance

### If login fails

Check:
- lockout monitor
- forced password change flow
- privacy consent flow
- single-device session behavior

## 9. Rollback vs Hotfix Decision

### Roll back when
- the issue started immediately after a deployment
- many users are down
- the cause is still unclear
- the previous release was stable

### Hotfix when
- the issue is isolated
- the root cause is already clear
- data/state in production already depends on the current release
- rollback would create bigger operational disruption

## 10. How Codex Can Help Remotely

Even without direct production-server access, Codex can still help effectively.

Send these:

1. traceback or log excerpt
2. screenshot of the page
3. exact URL
4. affected username/role
5. whether the issue started after deploy or after settings changes
6. recent relevant code diff if available

With that, Codex can:

- identify the likely root cause
- trace the affected code path in this repo
- prepare a safe patch
- add a regression test
- tell you whether rollback or hotfix is safer
- give you exact deploy validation steps

## 11. Safe Hotfix Workflow

1. Capture incident evidence.
2. Reproduce locally or in staging if possible.
3. Patch the repo in a focused way.
4. Run:
   - `python manage.py check`
   - impacted tests
5. Deploy the hotfix.
6. Re-run smoke checks:
   - Admin login
   - Faculty login
   - impacted page/flow
7. Confirm the issue is gone before reopening normal operations.

## 12. Safe Rollback Workflow

If the release helper script was used, first identify the last known good revision.

```bash
cd /opt/edugradespro
git log --oneline -n 20
```

Then roll back only if:
- you know which revision was stable
- you understand whether migrations were already applied

After rollback:

```bash
sudo systemctl restart gunicorn
sudo systemctl reload nginx
```

Then verify:
- Admin Portal login
- Faculty Portal login
- one grading summary page
- one submission/correction page

## 13. Backup Reminder

Before high-risk recovery actions:

1. preserve logs
2. take a database backup
3. note the current git commit

Example SQLite backup:

```bash
cd /opt/edugradespro
cp db.sqlite3 "db.sqlite3.backup.$(Get-Date -Format yyyyMMdd-HHmmss)"
```

If you are on Linux shell instead of PowerShell:

```bash
cp db.sqlite3 "db.sqlite3.backup.$(date +%Y%m%d-%H%M%S)"
```

## 14. Communication Template

Use a short, calm update:

> EduGradesPro is currently experiencing a production issue affecting [portal/feature].  
> Scope observed: [tenant/campus/users].  
> Investigation is in progress. Please avoid retrying high-risk actions such as repeated submissions until further notice.

When fixed:

> EduGradesPro service has been restored for [portal/feature].  
> Users may now resume normal operations.  
> We will continue monitoring for stability.

## 15. Post-Incident Review

After recovery, record:

1. what happened
2. start and end time
3. who was affected
4. root cause
5. fix applied
6. tests added
7. prevention action

Recommended prevention actions:

- add missing automated test coverage
- improve monitoring/logging
- add safer feature gating
- strengthen guide/runbook notes for the affected workflow

## 16. EduGradesPro Production Readiness Checklist

Before go-live or before the next release, confirm:

- backups are tested
- rollback process is known
- server logs are accessible
- cron jobs are running
- one admin knows how to collect evidence quickly
- staging or local repro workflow exists
- Codex can be given traceback/screenshots quickly when needed

## 17. Escalation Summary

If EduGradesPro suddenly shows production errors:

1. do not panic
2. capture evidence
3. confirm scope
4. freeze risky changes
5. inspect logs
6. decide rollback vs hotfix
7. send evidence to Codex for guided repair
8. verify both portals after recovery

This runbook is meant to reduce guesswork and keep production recovery disciplined, especially during grading-sensitive periods.

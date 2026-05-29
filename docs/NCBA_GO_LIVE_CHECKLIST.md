# NCBA TeacherMate+ Go-Live Checklist

This checklist is the practical go-live guide for NCBA before opening TeacherMate+ to real Admin and Faculty users.

## 1. Read These `.md` Files First

Read these in this exact order:

1. [AGENTS.md](/d:/teachermateplus/AGENTS.md)
2. [TEACHERMATEPLUS_CONTEXT.md](/d:/teachermateplus/TEACHERMATEPLUS_CONTEXT.md)
3. [CHANGE_LOG.md](/d:/teachermateplus/CHANGE_LOG.md)
4. [DEPLOYMENT_UBUNTU.md](/d:/teachermateplus/docs/DEPLOYMENT_UBUNTU.md)
5. [STAGING_WORKFLOW.md](/d:/teachermateplus/docs/STAGING_WORKFLOW.md)
6. [PRODUCTION_DATA_PROMOTION.md](/d:/teachermateplus/docs/PRODUCTION_DATA_PROMOTION.md)
7. [PRODUCTION_INCIDENT_RUNBOOK.md](/d:/teachermateplus/docs/PRODUCTION_INCIDENT_RUNBOOK.md)
8. [DB_SCHEMA.md](/d:/teachermateplus/docs/DB_SCHEMA.md)
9. [guide.html](/d:/teachermateplus/templates/admin_portal/guide.html)
10. [guide.html](/d:/teachermateplus/templates/faculty_portal/guide.html)
11. [guide_manual.html](/d:/teachermateplus/templates/faculty_portal/guide_manual.html)

## 2. Infrastructure Readiness

Confirm:

1. Ubuntu server is provisioned
2. MariaDB is installed
3. Nginx is installed
4. Git is installed
5. Python and venv are installed
6. DNS/subdomains are ready
7. firewall ports 80/443 are open
8. SSH and sudo access are confirmed

## 3. Environment Layout

Confirm the server layout:

1. production app path: `/opt/teachermateplus`
2. staging app path: `/opt/teachermateplus-staging`
3. production env path: `/etc/teachermateplus/teachermateplus.env`
4. staging env path: `/etc/teachermateplus-staging/teachermateplus.env`
5. production log path: `/var/log/teachermateplus`
6. staging log path: `/var/log/teachermateplus-staging`

## 4. Database Readiness

Confirm:

1. production MariaDB database exists
2. staging MariaDB database exists
3. production DB user is separate from staging DB user
4. DB credentials are stored only in env files
5. test DB connection from both instances

## 5. GitHub Readiness

Confirm:

1. repository is pushed to GitHub
2. deploy key or access token is ready
3. production server can clone/pull
4. staging server or staging instance can clone/pull
5. target branch strategy is agreed

## 6. Environment Files

Prepare from these templates:

1. [teachermateplus.production.env.example](/d:/teachermateplus/ops/env/teachermateplus.production.env.example)
2. [teachermateplus.staging.env.example](/d:/teachermateplus/ops/env/teachermateplus.staging.env.example)

Confirm:

1. secret key is replaced
2. DB credentials are correct
3. allowed hosts are correct
4. SMTP values are correct
5. privacy consent version is correct
6. SIS token is correct if used

## 7. Services and Reverse Proxy

Prepare and verify:

1. production service file:
   - [teachermateplus-gunicorn.service](/d:/teachermateplus/ops/systemd/teachermateplus-gunicorn.service)
2. staging service file:
   - [teachermateplus-staging-gunicorn.service](/d:/teachermateplus/ops/systemd/teachermateplus-staging-gunicorn.service)
3. production nginx config:
   - [teachermateplus.conf](/d:/teachermateplus/ops/nginx/teachermateplus.conf)
4. staging nginx config:
   - [teachermateplus-staging.conf](/d:/teachermateplus/ops/nginx/teachermateplus-staging.conf)

Confirm:

1. gunicorn service starts
2. nginx config passes `nginx -t`
3. unix socket paths are correct
4. static/media paths are correct

## 8. Data Promotion Readiness

Before importing real data:

1. review [PRODUCTION_DATA_PROMOTION.md](/d:/teachermateplus/docs/PRODUCTION_DATA_PROMOTION.md)
2. export the reviewed setup bundle from local
3. inspect the JSON bundle
4. confirm there are no junk/test records
5. load the bundle into staging first
6. validate staging data before production import

## 9. Staging Validation

Staging must pass these before production:

1. Admin Portal login works
2. Faculty Portal login works
3. Users and scoped roles are correct
4. Courses, sections, and offerings render correctly
5. Faculty assignments are visible
6. Grading templates are visible
7. Course template assignments are correct
8. Active Academic Year/Term settings behave correctly
9. Active Grading Period settings behave correctly
10. One faculty class flow works end to end
11. One submission workflow works
12. One correction workflow works

## 10. Production Go-Live Day

Do these in order:

1. back up production database
2. pull approved code revision
3. install/upgrade dependencies
4. run migrations
5. collect static files
6. run `python manage.py check`
7. restart gunicorn
8. reload nginx
9. import approved setup bundle if this is first launch
10. smoke-test production immediately

## 11. Production Smoke Test

Check these immediately:

1. `/admin-portal/`
2. `/faculty/`
3. Admin login
4. Faculty login
5. Dashboard loads
6. One class periods page loads
7. One grading summary page loads
8. One admin governance page loads
9. Images, static files, and CSS load
10. Logs are being written

## 12. Governance Readiness

Before opening to users, confirm:

1. template governance is configured
2. correction governance is configured
3. active academic year/term is configured
4. active grading period is configured
5. period locks/deadlines are configured if required
6. login security settings are configured

## 13. User Readiness

Confirm:

1. super admin account works
2. NCBA admin accounts work
3. sample faculty account works
4. password change flow works
5. privacy consent flow works
6. login lockout policy is reviewed

## 14. Backup and Recovery Readiness

Confirm:

1. DB backup command is tested
2. media backup plan exists
3. rollback decision path is clear
4. [PRODUCTION_INCIDENT_RUNBOOK.md](/d:/teachermateplus/docs/PRODUCTION_INCIDENT_RUNBOOK.md) is available to the responsible admin

## 15. Final Go / No-Go Questions

Only go live if all of these are yes:

1. Has staging passed?
2. Has the approved bundle been reviewed?
3. Are env files correct?
4. Are DB backups ready?
5. Are Admin and Faculty smoke tests passing?
6. Are governance settings configured?
7. Is the responsible NCBA operator ready to monitor the first live hours?

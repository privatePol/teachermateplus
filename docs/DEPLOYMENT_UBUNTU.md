# TeacherMate+ Production Deployment Guide

This guide is the recommended deployment path for **TeacherMate+ V1** on **Ubuntu** using:

- `gunicorn`
- `systemd`
- `nginx`
- `cron`
- `GitHub` for source delivery
- `MariaDB/MySQL` for production database

## Quick Answer First

If this is your first real rollout, use this:

1. Ubuntu server
2. MariaDB
3. one staging instance
4. one production instance
5. GitHub pull deploys
6. separate systemd service and nginx site per instance

Related references:

- [STAGING_WORKFLOW.md](/d:/teachermateplus/docs/STAGING_WORKFLOW.md)
- [PRODUCTION_DATA_PROMOTION.md](/d:/teachermateplus/docs/PRODUCTION_DATA_PROMOTION.md)
- [DB_SCHEMA.md](/d:/teachermateplus/docs/DB_SCHEMA.md)
- [PRODUCTION_INCIDENT_RUNBOOK.md](/d:/teachermateplus/docs/PRODUCTION_INCIDENT_RUNBOOK.md)
- [NCBA_GO_LIVE_CHECKLIST.md](/d:/teachermateplus/docs/NCBA_GO_LIVE_CHECKLIST.md)

## Deployment Stages Only

If you want the shortest operational sequence, this is the staged flow:

### Stage 1. Prepare the server

1. provision Ubuntu
2. install Python, Nginx, Git, and MariaDB
3. prepare DNS/subdomains
4. prepare firewall and HTTPS plan

### Stage 2. Prepare databases

1. create one DB and DB user for staging
2. create one DB and DB user for production
3. never share the same DB between staging and production

### Stage 3. Prepare app directories

1. create `/opt/teachermateplus-staging`
2. create `/opt/teachermateplus`
3. create separate env files
4. create separate log directories

### Stage 4. Pull code from GitHub

1. configure deploy key or token
2. clone repo into staging path
3. clone repo into production path

### Stage 5. Bootstrap staging

1. create virtualenv
2. install requirements
3. configure staging `.env`
4. run `migrate`
5. run `collectstatic`
6. run `check`
7. configure staging gunicorn
8. configure staging nginx
9. test staging

### Stage 6. Bootstrap production

1. create virtualenv
2. install requirements
3. configure production `.env`
4. run `migrate`
5. run `collectstatic`
6. run `check`
7. configure production gunicorn
8. configure production nginx
9. test production

### Stage 7. Activate scheduled jobs

1. install cron for staging if needed
2. install cron for production
3. verify log output

### Stage 8. Release workflow

1. develop locally
2. push to GitHub
3. deploy to staging
4. test staging
5. deploy the approved code to production

## Multi-App Server Note

You mentioned that the production server will host multiple Django applications.

That is completely fine, but each app should have its own:

- app directory
- virtual environment
- environment file
- gunicorn service
- nginx site/server block
- unix socket
- database or schema strategy
- log directory

For TeacherMate+ specifically, keep it isolated like this:

- app code: `/opt/teachermateplus`
- env file: `/etc/teachermateplus/teachermateplus.env`
- logs: `/var/log/teachermateplus`
- socket: `/run/teachermateplus/gunicorn.sock`
- service: `teachermateplus-gunicorn`

Do not mix TeacherMate+ inside another Django app's folders or service definitions.

## 1. Recommended Production Stack

For production, the recommended stack is:

- **Ubuntu 22.04 LTS or 24.04 LTS**
- **Python 3.11+**
- **MariaDB 10.11+** or **MySQL 8.0+**
- **Nginx**
- **Gunicorn**
- **GitHub private repository**

### Database recommendation

For production, prefer **MariaDB/MySQL** over SQLite.

Why:

- SQLite is fine for local development and small pilot usage
- TeacherMate+ has concurrent admin and faculty activity
- production needs stronger locking/concurrency behavior
- backups, monitoring, and recovery are easier with a server database

### My recommendation

If you are on Ubuntu and want the smoother path, use:

- **MariaDB on Ubuntu**

If your hosting provider or school infrastructure already standardizes on MySQL, then:

- **MySQL 8.0 is also fine**

TeacherMate+ already supports MySQL-compatible deployment through the existing Django database settings and `PyMySQL`.

## 2. High-Level Environment Strategy

Do not jump straight from local to production if you can avoid it.

Recommended environments:

1. **Local**
   - your laptop/workstation
   - coding, debugging, feature testing

2. **Staging**
   - a safe deploy target that behaves like production
   - used for smoke testing before going live

3. **Production**
   - the live system used by admin and faculty

For a beginner-friendly explanation of staging, also see:

- [STAGING_WORKFLOW.md](/d:/teachermateplus/docs/STAGING_WORKFLOW.md)

## 3. Simple Staging Workflow for Beginners

If you are not familiar with staging yet, use this simple model:

### Option A: Separate staging server

Best if possible.

- `staging.yourdomain.com`
- separate database
- separate app directory
- separate env file
- separate gunicorn service
- separate nginx site

### Option B: Same Ubuntu server, separate app instance

This is the easiest beginner-friendly staging setup if you only have one VPS.

Example:

- production app: `/opt/teachermateplus`
- staging app: `/opt/teachermateplus-staging`
- production env: `/etc/teachermateplus/teachermateplus.env`
- staging env: `/etc/teachermateplus-staging/teachermateplus.env`
- production domain: `grades.yourdomain.com`
- staging domain: `staging-grades.yourdomain.com`
- production DB: `teachermateplus`
- staging DB: `teachermateplus_staging`

This works well and is enough for many first deployments.

## 4. Recommended GitHub Deployment Workflow

Use GitHub as the source of truth.

Recommended branch flow:

1. `main`
   - production-ready code

2. `staging`
   - optional staging branch if you want a separate pre-production stream

Simple workflow:

1. develop and test locally
2. push to GitHub
3. pull and deploy to staging
4. test staging
5. merge to `main`
6. pull and deploy to production

If you want a simpler first rollout, you can use only:

- `main`

but still deploy to staging first before production.

## 5. What To Prepare Before Copying TeacherMate+ to Production

Before you clone the repo on the production server, prepare these:

### Infrastructure

- Ubuntu server ready
- domain or subdomain
- open ports 80/443
- SSH access
- sudo access

### App configuration

- strong Django secret key
- production hostnames
- SMTP account/app password
- database server and credentials
- privacy consent version
- SIS/API token if needed

### Operational safety

- backup plan
- restore test plan
- staging environment
- incident runbook
- one rollback procedure you understand

### School data readiness

- initial tenant/campus setup plan
- admin accounts
- role assignments
- grading templates
- template governance policy
- correction governance policy

## 6. Pre-Deployment Checklist

Before first live deployment, confirm:

1. `.env.example` has been reviewed
2. `manage.py` uses environment-aware settings
3. production will use **MariaDB/MySQL**
4. DNS is ready
5. SMTP settings are tested
6. at least one superuser plan exists
7. backup location is ready
8. staging exists, even if on the same VPS

## 7. Ubuntu Server Packages

Update the server:

```bash
sudo apt update && sudo apt upgrade -y
```

Install required packages:

```bash
sudo apt install -y python3 python3-venv python3-pip nginx git mariadb-server
```

Optional but recommended:

```bash
sudo apt install -y ufw certbot python3-certbot-nginx
```

## 8. Create App Users and Folders

### Why `/opt/teachermateplus` instead of `/var/www/html/TeacherMate+`?

For TeacherMate+, `/opt/teachermateplus` is the better layout.

Why:

- `/opt` is appropriate for self-contained application stacks
- TeacherMate+ is not just static web content; it includes code, venv, management commands, cron jobs, and service-managed runtime pieces
- `/var/www/html` is commonly used for simple web roots and static/PHP-style hosting layouts
- you said this server will host multiple Django apps, so keeping each app isolated under `/opt/<app-name>` is cleaner and safer

Recommended separation:

- application code in `/opt/<app-name>`
- environment files in `/etc/<app-name>/`
- logs in `/var/log/<app-name>/`
- nginx site config in `/etc/nginx/sites-available/`

That structure scales better when one Ubuntu server runs multiple unrelated Django applications.

### Production

```bash
sudo useradd --system --create-home --shell /bin/bash teachermateplus
sudo mkdir -p /opt/teachermateplus
sudo mkdir -p /etc/teachermateplus
sudo mkdir -p /var/log/teachermateplus
sudo chown -R teachermateplus:teachermateplus /opt/teachermateplus
sudo chown -R teachermateplus:teachermateplus /var/log/teachermateplus
```

### Staging on the same server

```bash
sudo useradd --system --create-home --shell /bin/bash teachermateplus-staging
sudo mkdir -p /opt/teachermateplus-staging
sudo mkdir -p /etc/teachermateplus-staging
sudo mkdir -p /var/log/teachermateplus-staging
sudo chown -R teachermateplus-staging:teachermateplus-staging /opt/teachermateplus-staging
sudo chown -R teachermateplus-staging:teachermateplus-staging /var/log/teachermateplus-staging
```

## 9. Create the MariaDB/MySQL Database

Open MariaDB:

```bash
sudo mysql
```

Create production DB and user:

```sql
CREATE DATABASE teachermateplus CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'teachermateplus_user'@'127.0.0.1' IDENTIFIED BY 'replace-with-strong-password';
GRANT ALL PRIVILEGES ON teachermateplus.* TO 'TeacherMate+_user'@'127.0.0.1';
FLUSH PRIVILEGES;
```

Create staging DB and user:

```sql
CREATE DATABASE teachermateplus_staging CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'teachermateplus_staging_user'@'127.0.0.1' IDENTIFIED BY 'replace-with-strong-password';
GRANT ALL PRIVILEGES ON teachermateplus_staging.* TO 'TeacherMate+_staging_user'@'127.0.0.1';
FLUSH PRIVILEGES;
```

Exit:

```sql
EXIT;
```

## 10. GitHub Repository Setup

If the repository is private, choose one of these:

### Option A: Deploy key

Best for a production pull-only workflow.

Generate SSH key on server:

```bash
sudo -u teachermateplus ssh-keygen -t ed25519 -C "TeacherMate+-production" -f /home/teachermateplus/.ssh/id_ed25519
```

Then add the public key to GitHub as a **Deploy Key** for the repo.

Test it:

```bash
sudo -u teachermateplus ssh -T git@github.com
```

### Option B: GitHub personal access token

Works too, but deploy keys are cleaner for servers.

## 11. Clone the Code

### Production

```bash
sudo -u teachermateplus git clone git@github.com:YOUR-ORG/YOUR-REPO.git /opt/teachermateplus
cd /opt/teachermateplus
```

### Staging

```bash
sudo -u teachermateplus-staging git clone git@github.com:YOUR-ORG/YOUR-REPO.git /opt/teachermateplus-staging
cd /opt/teachermateplus-staging
```

## 12. Python Virtual Environment

### Production

```bash
sudo -u teachermateplus python3 -m venv /opt/teachermateplus/.venv
sudo -u teachermateplus /opt/teachermateplus/.venv/bin/pip install --upgrade pip
sudo -u teachermateplus /opt/teachermateplus/.venv/bin/pip install -r /opt/teachermateplus/requirements/production.txt
```

### Staging

```bash
sudo -u teachermateplus-staging python3 -m venv /opt/teachermateplus-staging/.venv
sudo -u teachermateplus-staging /opt/teachermateplus-staging/.venv/bin/pip install --upgrade pip
sudo -u teachermateplus-staging /opt/teachermateplus-staging/.venv/bin/pip install -r /opt/teachermateplus-staging/requirements/production.txt
```

## 13. Production Environment File

Create:

- `/etc/teachermateplus/teachermateplus.env`

Start from:

- [teachermateplus.production.env.example](/d:/teachermateplus/ops/env/teachermateplus.production.env.example)

Recommended production example:

```env
DJANGO_ENV=production
DJANGO_SETTINGS_MODULE=config.settings
DJANGO_SECRET_KEY=replace-with-very-strong-secret
DJANGO_ALLOWED_HOSTS=grades.yourdomain.com,www.grades.yourdomain.com,server-ip
DJANGO_TIME_ZONE=Asia/Manila
DJANGO_SECURE_SSL_REDIRECT=True
DJANGO_SECURE_HSTS_SECONDS=31536000

DB_ENGINE=django.db.backends.mysql
DB_NAME=TeacherMate+
DB_USER=teachermateplus_user
DB_PASSWORD=replace-with-strong-password
DB_HOST=127.0.0.1
DB_PORT=3306

EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_USE_SSL=False
EMAIL_HOST_USER=noreply@ncba.edu.ph
EMAIL_HOST_PASSWORD=replace-with-app-password
DEFAULT_FROM_EMAIL=noreply@ncba.edu.ph
EMAIL_TIMEOUT=10

# SIS API: prefer tenant-bound keys created after deployment.
SIS_API_TOKEN=replace-with-strong-random-token
SIS_API_LEGACY_TOKEN_ENABLED=False
SIS_API_RATE_LIMIT_PER_MINUTE=60
PRIVACY_CONSENT_VERSION=2026-03
ENFORCE_SINGLE_DEVICE_SESSION=True
MAINTENANCE_MODE=False
ACTUAL_DATA_RESET_ALLOW_PRODUCTION=False
ACTUAL_DATA_RESET_EXTERNAL_BACKUP_CONFIRMED=False
DJANGO_LOG_DIR=/var/log/teachermateplus
```

Protect it:

```bash
sudo chown root:teachermateplus /etc/teachermateplus/teachermateplus.env
sudo chmod 640 /etc/teachermateplus/teachermateplus.env
```

### Staging environment file

Create:

- `/etc/teachermateplus-staging/teachermateplus.env`

Start from:

- [teachermateplus.staging.env.example](/d:/teachermateplus/ops/env/teachermateplus.staging.env.example)

Recommended staging example:

```env
DJANGO_ENV=production
DJANGO_SETTINGS_MODULE=config.settings
DJANGO_SECRET_KEY=replace-with-another-strong-secret
DJANGO_ALLOWED_HOSTS=staging-grades.yourdomain.com,server-ip
DJANGO_TIME_ZONE=Asia/Manila
DJANGO_SECURE_SSL_REDIRECT=True
DJANGO_SECURE_HSTS_SECONDS=31536000

DB_ENGINE=django.db.backends.mysql
DB_NAME=TeacherMate+_staging
DB_USER=teachermateplus_staging_user
DB_PASSWORD=replace-with-strong-password
DB_HOST=127.0.0.1
DB_PORT=3306

EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_USE_SSL=False
EMAIL_HOST_USER=noreply@ncba.edu.ph
EMAIL_HOST_PASSWORD=replace-with-app-password
DEFAULT_FROM_EMAIL=noreply@ncba.edu.ph
EMAIL_TIMEOUT=10

SIS_API_TOKEN=replace-with-strong-random-token
SIS_API_LEGACY_TOKEN_ENABLED=False
SIS_API_RATE_LIMIT_PER_MINUTE=60
PRIVACY_CONSENT_VERSION=2026-03
ENFORCE_SINGLE_DEVICE_SESSION=True
MAINTENANCE_MODE=False
ACTUAL_DATA_RESET_ALLOW_PRODUCTION=False
ACTUAL_DATA_RESET_EXTERNAL_BACKUP_CONFIRMED=False
DJANGO_LOG_DIR=/var/log/teachermateplus-staging
```

After migration, create tenant-bound SIS keys with:

```bash
python manage.py create_sis_api_key --tenant-code NCBA --name "NCBA SIS"
```

Store the printed token securely. Rotate by creating a replacement key and revoking/deactivating the old `tenant_api_keys` row.

Protect it:

```bash
sudo chown root:teachermateplus-staging /etc/teachermateplus-staging/teachermateplus.env
sudo chmod 640 /etc/teachermateplus-staging/teachermateplus.env
```

## 14. First Django Bootstrap

### Production

```bash
cd /opt/teachermateplus
sudo -u teachermateplus bash -lc 'set -a; source /etc/teachermateplus/teachermateplus.env; set +a; /opt/teachermateplus/.venv/bin/python manage.py migrate --noinput'
sudo -u teachermateplus bash -lc 'set -a; source /etc/teachermateplus/teachermateplus.env; set +a; /opt/teachermateplus/.venv/bin/python manage.py collectstatic --noinput'
sudo -u teachermateplus bash -lc 'set -a; source /etc/teachermateplus/teachermateplus.env; set +a; /opt/teachermateplus/.venv/bin/python manage.py check'
```

### Create superuser

```bash
sudo -u teachermateplus bash -lc 'set -a; source /etc/teachermateplus/teachermateplus.env; set +a; /opt/teachermateplus/.venv/bin/python manage.py createsuperuser'
```

### Staging

```bash
cd /opt/teachermateplus-staging
sudo -u teachermateplus-staging bash -lc 'set -a; source /etc/teachermateplus-staging/teachermateplus.env; set +a; /opt/teachermateplus-staging/.venv/bin/python manage.py migrate --noinput'
sudo -u teachermateplus-staging bash -lc 'set -a; source /etc/teachermateplus-staging/teachermateplus.env; set +a; /opt/teachermateplus-staging/.venv/bin/python manage.py collectstatic --noinput'
sudo -u teachermateplus-staging bash -lc 'set -a; source /etc/teachermateplus-staging/teachermateplus.env; set +a; /opt/teachermateplus-staging/.venv/bin/python manage.py check'
```

## 15. Gunicorn systemd Service

The repo already includes:

- `ops/systemd/teachermateplus-gunicorn.service`
- `ops/systemd/teachermateplus-staging-gunicorn.service`

For production:

```bash
sudo cp /opt/teachermateplus/ops/systemd/teachermateplus-gunicorn.service /etc/systemd/system/teachermateplus-gunicorn.service
sudo systemctl daemon-reload
sudo systemctl enable teachermateplus-gunicorn
sudo systemctl start teachermateplus-gunicorn
sudo systemctl status teachermateplus-gunicorn --no-pager
```

Logs:

```bash
sudo journalctl -u teachermateplus-gunicorn -f
```

### Staging on same server

Create a second unit by copying and editing the production one.

Example:

```bash
sudo cp /opt/teachermateplus/ops/systemd/teachermateplus-gunicorn.service /etc/systemd/system/teachermateplus-staging-gunicorn.service
```

Then change:

- `User=teachermateplus-staging`
- `Group=teachermateplus-staging`
- `WorkingDirectory=/opt/teachermateplus-staging`
- `EnvironmentFile=/etc/teachermateplus-staging/teachermateplus.env`
- `ExecStart=/opt/teachermateplus-staging/.venv/bin/gunicorn ... --bind unix:/run/teachermateplus-staging/gunicorn.sock`
- `RuntimeDirectory=teachermateplus-staging`

Then enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable teachermateplus-staging-gunicorn
sudo systemctl start teachermateplus-staging-gunicorn
```

## 16. Nginx Site

The repo already includes:

- `ops/nginx/teachermateplus.conf`
- `ops/nginx/teachermateplus-staging.conf`

### Production

Copy and edit it:

```bash
sudo cp /opt/teachermateplus/ops/nginx/teachermateplus.conf /etc/nginx/sites-available/TeacherMate+
```

Make sure:

- `server_name` uses your real production host
- `/static/` points to `/opt/teachermateplus/staticfiles/`
- `/media/` points to `/opt/teachermateplus/media/`
- `proxy_pass` points to `/run/teachermateplus/gunicorn.sock`

Enable it:

```bash
sudo ln -s /etc/nginx/sites-available/TeacherMate+ /etc/nginx/sites-enabled/TeacherMate+
sudo nginx -t
sudo systemctl reload nginx
```

### Staging

Create another site file:

```bash
sudo cp /opt/teachermateplus/ops/nginx/teachermateplus.conf /etc/nginx/sites-available/teachermateplus-staging
```

Change it to:

- `server_name staging-grades.yourdomain.com`
- `/static/ -> /opt/teachermateplus-staging/staticfiles/`
- `/media/ -> /opt/teachermateplus-staging/media/`
- `proxy_pass -> /run/teachermateplus-staging/gunicorn.sock`

Enable it:

```bash
sudo ln -s /etc/nginx/sites-available/teachermateplus-staging /etc/nginx/sites-enabled/teachermateplus-staging
sudo nginx -t
sudo systemctl reload nginx
```

### Firewall

```bash
sudo ufw allow 'Nginx Full'
sudo ufw enable
```

## 17. TLS / HTTPS

Install certbot if not yet installed:

```bash
sudo apt install -y certbot python3-certbot-nginx
```

### Production

```bash
sudo certbot --nginx -d grades.yourdomain.com -d www.grades.yourdomain.com
```

### Staging

```bash
sudo certbot --nginx -d staging-grades.yourdomain.com
```

After TLS is enabled, confirm:

- `DJANGO_ALLOWED_HOSTS` is correct
- `DJANGO_SECURE_SSL_REDIRECT=True`

## 18. Cron Jobs

The repo already includes:

- `ops/cron/teachermateplus.cron`

### Production

```bash
sudo -u teachermateplus crontab /opt/teachermateplus/ops/cron/teachermateplus.cron
sudo -u teachermateplus crontab -l
```

Current scheduled jobs:

- `auto_lock_period_deadlines`
- `auto_lapse_correction_windows`
- `queue_period_reminders`
- `anonymize_exit_pulse_identifiers` (hourly at minute 5; clears only expired anonymous browser-token hashes while preserving reaction and optional written-response data; use `--dry-run` to preview and `--tenant-id ID` for a tenant-scoped run)
- `issue_submission_non_compliance_notices` (daily at 7:15 AM server time; scheduler cadence checks overdue gradebooks daily. Configuration Management controls first notice after deadline, notice interval, and maximum notices. Defaults send Notice 1/2/3 on Day 1/2/3 and then stop automatic notices for that unsubmitted period.)

The repository cron file is not installed automatically by `git pull`. After deployment, reinstall it and verify:

```bash
sudo -u teachermateplus crontab /opt/teachermateplus/ops/cron/teachermateplus.cron
sudo -u teachermateplus crontab -l
```

### Staging

Use a separate staging cron file or copy and adapt the paths before installing.

Important:

- staging jobs must not write into production logs
- staging jobs must point to staging env and staging app paths

## 19. First Smoke Test After Deploy

### Production

Check:

1. `/admin-portal/`
2. `/faculty/`
3. Admin login
4. Faculty login
5. static files and images
6. one Admin dashboard page
7. one Faculty class page
8. one grading summary page
9. one import page
10. cron logs under `/var/log/teachermateplus/`

### Staging

Do the same before approving a production deploy.

## 20. Suggested First Go-Live Workflow

Use this simple process:

### Step 1

Prepare local and push code to GitHub.

### Step 2

Deploy to staging first.

### Step 3

Smoke-test staging:

- admin login
- faculty login
- one class flow
- one submission flow
- one correction flow
- one prediction page if enabled

### Step 4

If staging is good, deploy the same code to production.

### Step 5

Smoke-test production immediately.

### Step 6

Keep monitoring logs and user feedback for the first hours.

## 21. Release Update Workflow

The repo already includes:

- `ops/scripts/deploy_release.sh`

Use it:

```bash
sudo bash /opt/teachermateplus/ops/scripts/deploy_release.sh
```

It currently:

1. loads env
2. pulls latest code
3. installs/upgrades dependencies
4. runs migrations
5. collects static files
6. runs `manage.py check`
7. restarts gunicorn

### Recommended staging-first release flow

1. push changes to GitHub
2. deploy to staging
3. smoke-test staging
4. merge or promote approved code
5. deploy to production

## 22. Backups

Do not go live without backups.

### MariaDB backup example

```bash
mysqldump -u TeacherMate+_user -p --databases TeacherMate+ > TeacherMate+_backup.sql
```

### Media backup example

```bash
tar -czf TeacherMate+_media_backup.tar.gz /opt/teachermateplus/media
```

Keep:

- daily DB backups
- media backups
- off-server backup copy if possible

## 23. Production Incident Response

If TeacherMate+ shows live errors in production, use:

- [PRODUCTION_INCIDENT_RUNBOOK.md](/d:/teachermateplus/docs/PRODUCTION_INCIDENT_RUNBOOK.md)

Use the incident runbook for:

- outage triage
- evidence capture
- rollback vs hotfix decisions
- remote Codex-assisted support

## 24. My Final Recommendation For Your First Rollout

If this is your first production deployment, I recommend:

1. **Ubuntu + MariaDB**
2. **one staging instance on the same VPS**
3. **GitHub deploy keys**
4. **deploy to staging first every time**
5. **only then deploy to production**

That gives you a setup that is:

- practical
- not too complex
- much safer than local-to-production direct jumps

If you want a next step after this guide, the best follow-up would be:

- a ready-to-use **staging service file**
- a ready-to-use **staging nginx config**
- a ready-to-use **production env template**

Those are the most helpful pieces to generate next.

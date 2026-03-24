# EduGradesPro V1 Deployment Guide (Ubuntu + Gunicorn + Nginx)

This guide deploys EduGradesPro on Ubuntu with:

- `gunicorn` (app server)
- `systemd` (process manager)
- `nginx` (reverse proxy + static/media serving)
- `cron` (scheduled governance jobs)

---

## 1. Server Prerequisites

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nginx git
```

Optional for MySQL/MariaDB:

```bash
sudo apt install -y default-libmysqlclient-dev build-essential
```

---

## 2. Create App User and Folders

```bash
sudo useradd --system --create-home --shell /bin/bash edugradespro
sudo mkdir -p /opt/edugradespro
sudo chown -R edugradespro:edugradespro /opt/edugradespro
sudo mkdir -p /etc/edugradespro
sudo mkdir -p /var/log/edugradespro
sudo chown -R edugradespro:edugradespro /var/log/edugradespro
```

---

## 3. Pull Source Code

```bash
sudo -u edugradespro git clone https://github.com/privatePol/edugradepro.git /opt/edugradespro
cd /opt/edugradespro
```

---

## 4. Python Virtual Environment

```bash
sudo -u edugradespro python3 -m venv /opt/edugradespro/.venv
sudo -u edugradespro /opt/edugradespro/.venv/bin/pip install --upgrade pip
sudo -u edugradespro /opt/edugradespro/.venv/bin/pip install -r /opt/edugradespro/requirements/production.txt
```

---

## 5. Production Environment File

Create `/etc/edugradespro/edugradespro.env`:

```env
DJANGO_ENV=production
DJANGO_SECRET_KEY=replace-with-very-strong-secret
DJANGO_ALLOWED_HOSTS=your-domain.com,www.your-domain.com,server-ip
DJANGO_TIME_ZONE=Asia/Manila

# Database
DB_ENGINE=django.db.backends.sqlite3
DB_NAME=/opt/edugradespro/db.sqlite3

# If external DB:
# DB_ENGINE=django.db.backends.mysql
# DB_NAME=edugradespro
# DB_USER=edugradespro_user
# DB_PASSWORD=replace-me
# DB_HOST=127.0.0.1
# DB_PORT=3306

# SMTP
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_USE_SSL=False
EMAIL_HOST_USER=noreply@ncba.edu.ph
EMAIL_HOST_PASSWORD=replace-with-app-password
DEFAULT_FROM_EMAIL=noreply@ncba.edu.ph
EMAIL_TIMEOUT=10

# Security and integration
SIS_API_TOKEN=replace-with-strong-random-token
PRIVACY_CONSENT_VERSION=2026-03
ENFORCE_SINGLE_DEVICE_SESSION=True
```

Protect it:

```bash
sudo chown root:edugradespro /etc/edugradespro/edugradespro.env
sudo chmod 640 /etc/edugradespro/edugradespro.env
```

---

## 6. Django Bootstrap

```bash
cd /opt/edugradespro
sudo -u edugradespro bash -lc 'set -a; source /etc/edugradespro/edugradespro.env; set +a; /opt/edugradespro/.venv/bin/python manage.py migrate --noinput'
sudo -u edugradespro bash -lc 'set -a; source /etc/edugradespro/edugradespro.env; set +a; /opt/edugradespro/.venv/bin/python manage.py collectstatic --noinput'
sudo -u edugradespro bash -lc 'set -a; source /etc/edugradespro/edugradespro.env; set +a; /opt/edugradespro/.venv/bin/python manage.py check'
```

---

## 7. Gunicorn systemd Service

Copy template:

```bash
sudo cp /opt/edugradespro/ops/systemd/edugradespro-gunicorn.service /etc/systemd/system/edugradespro-gunicorn.service
sudo systemctl daemon-reload
sudo systemctl enable edugradespro-gunicorn
sudo systemctl start edugradespro-gunicorn
sudo systemctl status edugradespro-gunicorn --no-pager
```

Logs:

```bash
journalctl -u edugradespro-gunicorn -f
```

---

## 8. Nginx Site

Copy template:

```bash
sudo cp /opt/edugradespro/ops/nginx/edugradespro.conf /etc/nginx/sites-available/edugradespro
sudo ln -s /etc/nginx/sites-available/edugradespro /etc/nginx/sites-enabled/edugradespro
sudo nginx -t
sudo systemctl reload nginx
```

Open firewall:

```bash
sudo ufw allow 'Nginx Full'
```

---

## 9. Cron Jobs (Auto-lock + Correction-Lapse + Reminders)

Install cron entries for `edugradespro` user:

```bash
sudo -u edugradespro crontab /opt/edugradespro/ops/cron/edugradespro.cron
sudo -u edugradespro crontab -l
```

What runs:

- `auto_lock_period_deadlines` every 5 minutes
- `auto_lapse_correction_windows` every 10 minutes
- `queue_period_reminders` every hour

---

## 10. Post-Deploy Smoke Test

1. Open `/admin-portal/` login
2. Open `/faculty/` public page + login flow
3. Verify static files and images load
4. Check:
   - Admin dashboard renders
   - Faculty my-courses and summary pages render
   - Import pages open
5. Verify cron logs in `/var/log/edugradespro/`

---

## 11. Release Update Workflow

Use helper script:

```bash
sudo bash /opt/edugradespro/ops/scripts/deploy_release.sh
```

It will:

1. pull latest code
2. install/upgrade python deps
3. run migrations
4. collect static files
5. run `manage.py check`
6. restart gunicorn

---

## 12. TLS/HTTPS (Recommended for Production)

Install certbot:

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com -d www.your-domain.com
```

Then confirm `DJANGO_ALLOWED_HOSTS` includes your production hostnames.

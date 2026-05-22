# EduGrade+ Data-at-Rest Protection Guide

This guide separates what EduGrade+ now protects inside the Django codebase from what IT must still configure on the production server.

## Implemented in the Application

1. Correction attachments are no longer exposed through direct template `file.url` links.
2. Faculty and Admin correction attachment downloads now pass through authenticated, permission-checked views.
3. Correction attachments are validated before upload:
   - PDF, PNG, JPG, and JPEG only
   - maximum 5 MB
   - PDF header and image integrity checks
4. CSV import uploads are validated before staging:
   - `.csv` extension only
   - maximum 10 MB
   - basic binary-content rejection
5. Stored filenames for correction attachments and import source files are randomized with UUID-based paths.
6. Upload metadata is recorded for correction attachments and import batches:
   - original filename
   - randomized stored filename
   - content type
   - file size
7. Audit logs are written for sensitive file actions:
   - correction attachment upload
   - correction attachment download
   - correction official report download
   - faculty final-clearance PDF download
   - signature preview
   - import upload
8. `.gitignore` now blocks common local media, backup, SQL dump, encrypted backup, and private-media paths.
9. Backup helper scripts are available under `scripts/`:
   - `backup_mysql.sh`
   - `encrypt_backup.sh`
   - `decrypt_backup.sh`

## Environment-Variable Secrets

EduGrade+ should load production secrets from environment variables, not committed files.

Required production values include:

- `SECRET_KEY`
- `DB_NAME`
- `DB_USER`
- `DB_PASSWORD`
- `DB_HOST`
- `DB_PORT`
- email SMTP credentials
- signature encryption key settings already used by the signature service
- `BACKUP_ENCRYPTION_PASSWORD` when encrypting backup files

Never commit real `.env` files, database passwords, SMTP passwords, backup passwords, dumps, or private media.

## Manual IT Checklist

These items must be performed by the production IT/server administrator. They are intentionally not configured by the Django app.

1. Disk encryption:
   - decide whether to use LUKS or cloud-provider volume encryption
   - document recovery keys and authorized custodians
2. Storage layout:
   - place database, media, logs, and backups on appropriate server volumes
   - use RAID or cloud disk snapshots if required by IT policy
3. Web server:
   - configure nginx/apache so sensitive upload folders are not served directly
   - public static assets may be served normally
   - protected media must be accessed through Django views
4. Database:
   - create least-privilege MariaDB/MySQL users
   - rotate production database passwords through the environment file
   - restrict DB listener exposure to the application host/network
5. Firewall:
   - allow only required inbound ports
   - block direct public database access
6. Backups:
   - run scheduled DB and media backups
   - encrypt backup files
   - copy encrypted backups off-server
   - test restore regularly
7. Operating-system permissions:
   - restrict `/etc/edugradeplus/edugradeplus.env`
   - restrict media and backup folders to the application user and backup operator
   - ensure logs do not expose secrets

## Operational Notes

- Protected downloads still require the file to exist on disk or object storage.
- Direct media serving must not expose `media/imports/` or `media/correction_attachments/`.
- Audit logging records access events, but it does not replace server log retention or SIEM policy.
- Backup scripts are helpers. IT should integrate them into cron/systemd timers and retention policy.

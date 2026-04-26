# EduGradesPro Backup Scripts

These scripts are application-level helpers only. They do not configure the production server.

## Database Backup

Set the database variables from the deployment environment file, then run:

```bash
export DB_NAME=edugradespro
export DB_USER=egpro1_admin
export DB_PASSWORD='set-from-secure-env'
export DB_HOST=127.0.0.1
export DB_PORT=3306
export BACKUP_DIR=/secure/backups/edugradespro

./scripts/backup_mysql.sh
```

## Encrypt a Backup

```bash
export BACKUP_ENCRYPTION_PASSWORD='long-random-secret-from-password-manager'
./scripts/encrypt_backup.sh /secure/backups/edugradespro/edugradespro-YYYYMMDD-HHMMSS.sql.gz
```

Store encrypted backups off-server where possible. Test restore procedures regularly.

## Decrypt for Restore Testing

```bash
export BACKUP_ENCRYPTION_PASSWORD='long-random-secret-from-password-manager'
./scripts/decrypt_backup.sh /secure/backups/edugradespro/edugradespro-YYYYMMDD-HHMMSS.sql.gz.enc
```

Do not commit generated backup files, encrypted backup files, or passwords.

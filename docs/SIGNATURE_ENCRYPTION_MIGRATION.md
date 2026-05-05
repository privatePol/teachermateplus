# Signature Encryption and Server Migration Runbook

This runbook explains how EduGradesPro encrypts uploaded user signatures and what to do when moving from a testing live server to the final production server.

## Why This Matters

EduGradesPro stores uploaded signature images encrypted in the database. These signatures are used by optional report features such as:

- Faculty Final Clearance PDF signatures
- Correction Official Report PDF signatures

If the encryption key used when the signature was uploaded is not available on the server that later generates the PDF, signature decryption fails. In production this can appear as:

```text
cryptography.exceptions.InvalidTag
```

That error means the stored encrypted signature cannot be decrypted with the key currently loaded by the app.

## How EduGradesPro Chooses the Signature Key

The signature service uses this key order:

1. If `SIGNATURE_ENCRYPTION_KEY` is set, EduGradesPro uses it.
2. If `SIGNATURE_ENCRYPTION_KEY` is missing, EduGradesPro falls back to a key derived from `DJANGO_SECRET_KEY`.

Because of that fallback, changing `DJANGO_SECRET_KEY` can break old signatures when `SIGNATURE_ENCRYPTION_KEY` was not set at upload time.

For final production, always set a permanent `SIGNATURE_ENCRYPTION_KEY`.

## Required Key Format

`SIGNATURE_ENCRYPTION_KEY` must be URL-safe base64 that decodes to exactly 32 bytes.

Generate a valid key:

```bash
python3 -c "import os, base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

Example shape:

```env
SIGNATURE_ENCRYPTION_KEY=AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcdEFG=
```

Do not use normal words, short random text, or a value copied from another unrelated setting.

## New Final Production Server Checklist

Use this path for the production environment file unless the systemd service points somewhere else:

```bash
/etc/edugradespro/edugradespro.env
```

Before users upload signatures on the final production server:

1. Generate a valid key:

   ```bash
   python3 -c "import os, base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
   ```

2. Edit the production env file:

   ```bash
   sudo nano /etc/edugradespro/edugradespro.env
   ```

3. Add the key:

   ```env
   SIGNATURE_ENCRYPTION_KEY=paste-generated-key-here
   ```

4. Restart the app:

   ```bash
   sudo systemctl restart edugradespro-gunicorn
   sudo systemctl reload nginx
   ```

5. Verify Django can load the key without printing the secret:

   ```bash
   sudo -u edugradespro bash -lc '
   cd /opt/edugradespro
   set -a
   source /etc/edugradespro/edugradespro.env
   set +a
   /opt/edugradespro/.venv/bin/python manage.py shell -c "
   import base64, os
   raw = os.environ.get(\"SIGNATURE_ENCRYPTION_KEY\", \"\").strip()
   print(\"SIGNATURE_ENCRYPTION_KEY configured:\", bool(raw))
   print(\"Decoded length:\", len(base64.urlsafe_b64decode(raw.encode(\"utf-8\"))) if raw else 0)
   "
   '
   ```

   Expected output:

   ```text
   SIGNATURE_ENCRYPTION_KEY configured: True
   Decoded length: 32
   ```

6. Ask users to upload signatures only after this key is active.

## Moving From Testing Live to Final Production

Choose the correct scenario.

### Scenario A: You Do Not Need to Preserve Testing Signatures

This is the recommended path for a testing live server.

1. Keep signature-based PDF options disabled during migration.
2. Add `SIGNATURE_ENCRYPTION_KEY` on the final production server.
3. Move the database and media as planned.
4. Ask users to delete and re-upload signatures on final production.
5. Re-enable signature-based PDF options after testing.

This avoids carrying broken test signatures into final production.

### Scenario B: You Must Preserve Existing Signatures

If signatures were uploaded while `SIGNATURE_ENCRYPTION_KEY` was missing, they were encrypted using the old server's `DJANGO_SECRET_KEY`.

To preserve them without re-uploading:

1. The final server must use the same `DJANGO_SECRET_KEY` as the server where the signatures were uploaded.
2. Do not add a new `SIGNATURE_ENCRYPTION_KEY` unless the signatures are first re-encrypted under that new key by a controlled migration script.
3. Test signature preview and PDF generation before enabling report signatures for users.

If the old `DJANGO_SECRET_KEY` is unknown or was changed, the old signatures cannot be decrypted. Users must re-upload.

### Scenario C: Existing Signatures Were Uploaded With `SIGNATURE_ENCRYPTION_KEY`

If the old server already had `SIGNATURE_ENCRYPTION_KEY`, copy the exact same value to the final server.

Do not generate a new key.

## Safe Operational Workaround

If Official PDF generation fails because of signature decryption:

1. Go to Admin Portal.
2. Open `Tools -> Configuration Management`.
3. Disable the affected signature option, for example:

   ```text
   Allow stored signatures on Correction Official Report
   ```

4. Save the settings.
5. Generate the PDF again.

The PDF should generate without embedded signatures.

## Re-Upload Procedure for Users

After the final production key is configured:

1. Ask affected users to open their signature page.
2. Delete the old signature.
3. Upload the signature image again.
4. Test signature preview.
5. Generate a test Official PDF.

Uploaded signatures become tied to the active `SIGNATURE_ENCRYPTION_KEY`. Keep that key stable and backed up.

## Backup Requirements

Back up these together:

- Database
- Media files
- `/etc/edugradespro/edugradespro.env`
- `SIGNATURE_ENCRYPTION_KEY`
- `DJANGO_SECRET_KEY`

Store secrets in the approved secure password/secrets vault. Do not paste production keys into chats, tickets, screenshots, or documentation examples.

## Quick Diagnosis

If a signature PDF fails with HTTP 500, reproduce from the server:

```bash
sudo -u edugradespro bash -lc '
cd /opt/edugradespro
set -a
source /etc/edugradespro/edugradespro.env
set +a
/opt/edugradespro/.venv/bin/python manage.py shell -c "
from apps.grading.models import GradeCorrectionRequest
from apps.grading.reporting import CorrectionOfficialReportService
r = GradeCorrectionRequest.objects.get(id=5)
pdf = CorrectionOfficialReportService.build_pdf_bytes(request_obj=r)
print(len(pdf))
"
'
```

Replace `id=5` with the affected correction request ID.

If the traceback ends with `cryptography.exceptions.InvalidTag`, the signature key does not match the stored encrypted signature.


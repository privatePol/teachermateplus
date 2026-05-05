# SIS Integration: Periodic Grades API

EduGradesPro exposes a pull API so the School Information System (SIS) can fetch periodic grades directly.

## Endpoint

- Primary: `/api/v1/sis/periodic-grades/`
- Alias: `/api/sis/periodic-grades/`

## Authentication

Preferred production authentication uses a tenant-bound API key:

- `X-API-Token: <tenant-api-key>`
- `Authorization: Bearer <tenant-api-key>`

Create a key on the server:

```bash
python manage.py create_sis_api_key --tenant-code NCBA --name "NCBA SIS"
```

The command prints the raw token once. Store it in the SIS secrets vault. The key is bound to that tenant, can be revoked by deactivating the `tenant_api_keys` row, and can be rotated by creating a replacement key.

Legacy deployments may still use:

- `SIS_API_TOKEN=<strong-random-token>`
- `SIS_API_LEGACY_TOKEN_ENABLED=True`

For production, move SIS clients to tenant-bound keys and keep `SIS_API_LEGACY_TOKEN_ENABLED=False`.

## Rate limiting

The API applies a lightweight rate limit per tenant API key, or per IP for legacy/invalid requests.

Default:

`SIS_API_RATE_LIMIT_PER_MINUTE=60`

## Required query parameter

- `tenant_code`

For tenant API keys, `tenant_code` must match the key's tenant.

## Optional query parameters

- `campus_code`
- `academic_year_code`
- `term_code`
- `period_code`
- `course_code`
- `section_code`
- `student_no`
- `updated_since` (ISO datetime)
- `submitted_since` (ISO datetime)
- `page` (default `1`)
- `page_size` (default `500`, max `2000`)

## Identity scoping rules

For campus-separated SIS/AIMS integrations, use the identity triple:

- `campus_code`
- `section_code`
- `student_no`

Validation guardrails:

- If `section_code` is provided, `campus_code` is required.
- If `student_no` is provided, both `campus_code` and `section_code` are required.
- If `campus_code` is provided, the campus must be active and belong to the requested tenant.

## Example request

```bash
curl -X GET "http://127.0.0.1:8000/api/v1/sis/periodic-grades/?tenant_code=NCBA&campus_code=NCBA-02&academic_year_code=2025-2026&term_code=2ND&period_code=GENED_PRELIM&page=1&page_size=500" \
  -H "X-API-Token: replace-with-sis-token"
```

### Example request (campus + section + student)

```bash
curl -X GET "http://127.0.0.1:8000/api/v1/sis/periodic-grades/?tenant_code=NCBA&campus_code=NCBA-FAIRV&section_code=BSA%201-BSA_1A&student_no=2025-10606&academic_year_code=2025-2026&term_code=2ND" \
  -H "X-API-Token: replace-with-sis-token"
```

## Response shape

```json
{
  "ok": true,
  "page": 1,
  "page_size": 500,
  "total_count": 123,
  "results": [
    {
      "tenant_code": "NCBA",
      "campus_code": "NCBA-02",
      "academic_year_code": "2025-2026",
      "term_code": "2ND",
      "period_code": "GENED_PRELIM",
      "period_name": "PRELIM",
      "course_code": "A132-ITAPPS",
      "course_title": "IT Application Tools in Business",
      "section_code": "BSA 1-BSA_1A",
      "student_no": "2025-10606",
      "student_name": "BAUTISTA, KENJIE",
      "student_status": "ACTIVE",
      "class_standing_grade": "93",
      "exam_grade": "100",
      "period_grade": "96",
      "is_finalized": true,
      "submitted_at": "2026-03-22T13:22:00+08:00",
      "submission_remarks": "Submitted by faculty",
      "computed_at": "2026-03-22T13:20:40+08:00",
      "updated_at": "2026-03-22T13:20:40+08:00"
    }
  ]
}
```

## Governance behavior

Only periodic grades tied to a `SUBMITTED` grade submission are returned.
Draft or reopened-but-not-resubmitted periods are excluded.
Official class standing, exam, and period grades are rounded to whole numbers using `ROUND_HALF_UP`.
Every successful read, denied request, and rate-limit event is written to the audit log without storing the raw API token.

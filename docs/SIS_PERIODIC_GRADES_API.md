# SIS Integration: Periodic Grades API

EduGradesPro exposes a pull API so the School Information System (SIS) can fetch periodic grades directly.

## Endpoint

- Primary: `/api/v1/sis/periodic-grades/`
- Alias: `/api/sis/periodic-grades/`

## Authentication

Use either:

- `X-API-Token: <SIS_API_TOKEN>`
- `Authorization: Bearer <SIS_API_TOKEN>`

Set the token in environment:

`SIS_API_TOKEN=<strong-random-token>`

## Required query parameter

- `tenant_code`

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
      "class_standing_grade": "93.05",
      "exam_grade": "99.50",
      "period_grade": "95.63",
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

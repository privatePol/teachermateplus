# EduGrade+ V1

This repository contains **EduGrade+ V1**, a multi-tenant, multi-campus academic grading platform with:

- **Admin Portal**: `/admin-portal/`
- **Faculty Portal**: `/faculty/`

---

## Start Here (For New Chat Sessions / New Contributors)

When starting work in this repo, align in this order:

1. Read [`AGENTS.md`](AGENTS.md) first (working contract and guardrails).
2. Read [`EduGrade+_CONTEXT.md`](EduGrade+_CONTEXT.md) for current system context.
3. Read [`CHANGE_LOG.md`](CHANGE_LOG.md) for recent changes.
4. Read only the relevant docs under [`docs/`](docs/) for the task at hand.

If the task is user-facing (UI/behavior/policy), also review the affected guide templates:

- `templates/faculty_portal/guide.html`
- `templates/faculty_portal/guide_manual.html`
- `templates/admin_portal/guide.html`

---

## Working Directory

Always work from:

`D:\edugradeplus`

---

## Core Rules

- Enforce tenant/campus scoping on all reads/writes.
- Enforce RBAC before showing actions and before processing requests.
- Preserve grading governance and auditability (submission, reopen, correction).
- Prefer small, additive, low-risk changes.
- Do not break stable URLs and established workflows.
- New features should be configurable whenever practical, with enable/disable support designed for flexibility.
- Prefer global feature control and, where needed, per-role control rather than hard-enabling optional workflows.
- User-manageable feature toggles/settings should be placed in a dedicated configurable-features page.

Sensitive modules:

- `apps/grading/*`
- `apps/admin_portal/*`
- `apps/faculty_portal/*`
- `apps/accounts/*`

---

## Development Quick Commands

Use these at minimum during implementation:

```powershell
python manage.py check
python manage.py makemigrations
python manage.py migrate
```

For targeted smoke tests, cover:

1. Admin list/create/edit flow
2. Faculty grading flow: activities -> scores -> summary -> submit/reopen behavior
3. Permissions and menu visibility for impacted actions

---

## Documentation Update Rule

If behavior changes, update:

1. [`CHANGE_LOG.md`](CHANGE_LOG.md)
2. [`EduGrade+_CONTEXT.md`](EduGrade+_CONTEXT.md)
3. Relevant Faculty/Admin guide pages

---

## Suggested Commit Pattern

- `feat(module): short summary`
- `fix(module): short summary`
- `chore(docs): short summary`

Example:

`fix(grading): correct summary ordering and readiness checks`

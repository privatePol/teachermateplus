# AGENTS.md

## Purpose
This file is the working contract for humans and coding agents contributing to **EduGradesPro V1**.

## Project Identity
- Product: **EduGradesPro V1**
- Domain: Multi-tenant, multi-campus academic grading platform
- Primary portals:
  - **Admin Portal** (`/admin-portal/`)
  - **Faculty Portal** (`/faculty/`)

## Core Principles
1. Respect tenant and campus scope on every read/write operation.
2. Enforce RBAC before rendering actions and before processing requests.
3. Keep grading governance and auditability intact (submission, corrections, reopen).
4. Prefer additive, low-risk changes over broad rewrites.
5. Do not break existing URLs and workflows already used by operations.

## Working Conventions
- Backend: Django (apps-based architecture)
- DB: SQLite (dev), production-ready for external RDBMS migration planning
- Frontend: Django templates + Bootstrap + custom CSS
- Keep changes isolated per module and easy to review.
- Add migration files for model changes.
- Run `python manage.py check` after edits.

## Minimum Validation Before Handoff
1. `python manage.py check`
2. `python manage.py migrate` (if new migrations exist)
3. Smoke-test impacted pages:
   - Admin list/create/edit flow
   - Faculty grade flow (activities -> scores -> summary -> submit/reopen policy)
4. Verify permissions and menu visibility for affected actions.

## Sensitive Areas (Handle Carefully)
- `apps/grading/*` (computation, submission, locks, corrections)
- `apps/admin_portal/*` (governance settings, imports, security)
- `apps/faculty_portal/*` (faculty UX, score entry, summary, print)
- `apps/accounts/*` (auth, password reset, privacy consent)
- Multi-tenant filters and dropdown scope persistence

## Documentation Update Rule
When changing behavior, also update:
1. `CHANGE_LOG.md`
2. `EDUGRADESPRO_CONTEXT.md`
3. Faculty/Admin guide pages if user-facing behavior changed

## Commit Message Pattern (Recommended)
- `feat(module): short summary`
- `fix(module): short summary`
- `chore(docs): short summary`

Example:
- `fix(grading): correct summary table ordering and period readiness checks`


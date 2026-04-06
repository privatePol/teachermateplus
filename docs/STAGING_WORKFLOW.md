# EduGradesPro Staging Workflow Guide

This guide explains a simple staging workflow for EduGradesPro before production deployment.

## 1. What Staging Is

A staging environment is a safe copy of production behavior.

Use it to:

- deploy code before production
- test migrations safely
- verify static files and templates
- test admin and faculty flows
- catch deployment mistakes before users see them

## 2. Why You Want It

Without staging, the flow becomes:

- local -> production

That is risky because:

- server-only config problems appear late
- migrations can fail live
- nginx/gunicorn issues are discovered by users
- faculty/admin workflows can break in the real environment first

With staging, the flow becomes:

- local -> GitHub -> staging -> production

That is much safer.

## 3. Simplest Beginner-Friendly Setup

If you only have one Ubuntu VPS, use:

- production app at `/opt/edugradespro`
- staging app at `/opt/edugradespro-staging`
- production DB `edugradespro`
- staging DB `edugradespro_staging`
- production domain `grades.yourdomain.com`
- staging domain `staging-grades.yourdomain.com`

This is enough for a practical first deployment.

## 4. Suggested Branch Workflow

### Simple option

- `main` only

Flow:

1. finish work locally
2. push to GitHub
3. deploy to staging
4. test staging
5. deploy same commit to production

### More structured option

- `staging`
- `main`

Flow:

1. push to `staging`
2. deploy `staging` branch to staging server
3. test
4. merge to `main`
5. deploy `main` to production

## 5. What To Test In Staging

At minimum:

1. Admin login
2. Faculty login
3. dashboard rendering
4. one class page
5. one summary page
6. one submission flow
7. one correction flow
8. one active-period governed class page
9. one import page
10. static/media loading

If the release touches a specific module, test that exact module too.

## 6. Release Discipline

When a feature is ready:

1. commit locally
2. push to GitHub
3. deploy to staging
4. smoke-test
5. if good, deploy to production

Do not make emergency untracked server edits if you can avoid them.

## 7. Staging Data Guidance

Do not point staging to the production database.

Use a separate staging DB.

For staging data, you can use:

- a clean seed
- a sanitized copy of production-like data
- selected test records that cover real workflows

## 8. When To Skip Production Deployment

Do not promote the release yet if staging shows:

- template errors
- migration failures
- portal login issues
- missing static files
- broken admin or faculty pages
- grading workflow regression

Fix it first, then redeploy staging.

## 9. Best Habit

Think of staging as the place where you answer:

> "Will this run correctly on the server?"

not just:

> "Did it work on my laptop?"

That habit alone prevents many production incidents.

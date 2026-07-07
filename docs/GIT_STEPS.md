Yes. Use this **safe Git routine regardless of which files changed**.

### 1. Check what changed

```bash
git status --short
```

### 2. Review the changes

```bash
git diff
```

For new/untracked files, `git diff` may not show them. Use:

```bash
git status
```

### 3. Stage everything

```bash
git add -A
```

### 4. Unstage files you do NOT want to commit

For TMP, usually exclude `logs/system.log`:

```bash
git restore --staged logs/system.log
```

If there are other files you do not want included, unstage them the same way:

```bash
git restore --staged path/to/file
```

### 5. Confirm staged files

```bash
git status --short
```

Files marked with `M`, `A`, or `D` in the left column are staged.

Example:

```bash
M  apps/admin_portal/views.py
A  apps/admin_portal/tests_users.py
 M logs/system.log
```

In this example, `logs/system.log` is **not staged**, so it will not be committed.

### 6. Run checks/tests

At minimum:

```bash
python manage.py check
```

If Codex gave a specific test command, run it too, for example:

```bash
python manage.py test apps.admin_portal.tests_users
```

### 7. Check whitespace issues

```bash
git diff --check
```

Line-ending warnings are usually okay if they are only warnings, but actual whitespace errors should be fixed before committing.

### 8. Commit

```bash
git commit -m "Describe the change clearly"
```

Example:

```bash
git commit -m "Split admin user save and email credential actions"
```

### 9. Confirm worktree after commit

```bash
git status --short
```

Expected clean result:

```bash
```

Or acceptable if only log file remains:

```bash
 M logs/system.log
```

### 10. Push

```bash
git push
```

Your reusable TMP Git flow:

```bash
git status --short
git diff
git add -A
git restore --staged logs/system.log
git status --short
python manage.py check
python manage.py test apps.admin_portal.tests_users
git diff --check
git commit -m "Your commit message here"
git status --short
git push
```

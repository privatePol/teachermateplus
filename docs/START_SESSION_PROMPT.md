PASTED THESE ON EVERY START OF THE SESSION:

Read first:
- AGENTS.md
- TEACHERMATEPLUS_CONTEXT.md
- CHANGE_LOG.md
- HANDOFF.md

Continue from HANDOFF.md.

Before doing anything:
1. Summarize the current handoff status.
2. Identify only the files relevant to this task.
3. Ask for confirmation before editing files.
4. Do not scan unrelated apps or modules.
5. Do not implement yet unless I say PROCEED.


=========MODIFY TASKS BELOW=========


Task:
Review whether the gradebook deadline escalation notification already exists.

Scope:
- gradebook submission workflow
- overdue submission monitor
- notifications/email module
- scheduled jobs/cron/celery commands if already referenced

Do not review unrelated features.
Do not inspect student performance insights unless required.

=================

Say:

investigate only and report. Do not edit yet.

That forces Codex to stop after discovery.

Your better workflow should be:

Prompt 1: Investigate only
Prompt 2: Implement only approved changes
Prompt 3: Run targeted tests only
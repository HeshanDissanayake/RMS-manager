# Demo project for RMS

This is a small demo project used to try RMS (Research Manager) with Claude Code.
RMS hooks are configured in `.claude/settings.json`. At session start RMS adds a note to your context
saying which session you are and which threads exist: follow it (ask the owner which thread, then
`rms claim <ID>`). Do not edit files outside your thread's folder; RMS will block it anyway.

## RMS
This project is managed by RMS. Every session must start in the project root. If you got no
"RMS" note at the start of this session, you were started elsewhere: do nothing, and ask the
owner to restart `claude` in the project root.

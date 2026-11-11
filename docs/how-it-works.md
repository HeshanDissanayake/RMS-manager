# How RMS works

RMS has no server in this step. It is one program (`rms`) and a folder of small JSON files in the project. Claude Code calls it through **hooks**, the owner calls it from a terminal or the TUI, and sessions call it through Bash.

```
                      <project>/.claude/settings.json  (or settings.local.json)
                      says: on these events, run `rms hook <event>`
                                        │
 Claude Code session ── SessionStart ──▶ rms hook session-start ─┐
                    ── PreToolUse  ───▶ rms hook pre-tool-use  ─┤   finds the project
                    ── SessionEnd  ───▶ rms hook session-end   ─┤   (rms.json, upwards
                    ── Bash: rms claim T01 ─▶ rms claim        ─┤    from the session's
 owner ──────────────── rms tui / rms threads / rms release ────┘    folder)
                                        │
                                        ▼
                    <project>/store/rms/   sessions/<id>.json   claims/T01.json   events.jsonl
```

## The parts

| part | where | what |
|---|---|---|
| project config | `<project>/rms.json` | marks the project root; folders for threads, index, side sessions, state; extra writable paths |
| state | `<project>/store/rms/` (not in git) | `sessions/` one file per session, `claims/` one file per held thread, `events.jsonl` the log |
| hooks | `<project>/.claude/settings.json` (or `settings.local.json`) | make Claude Code call `rms hook <event>` |
| code | this repo, `rms/` | `hooks.py` (what each hook does), `claims.py`, `sessions.py`, `threads.py`, `setup.py` (install + doctor), `cli.py`, `tui.py` |

The state is plain files under one lock (`store/rms/.lock`, `flock`), written atomically. It can be read and fixed by hand. Nothing depends on a running server.

## Claude Code hooks: the facts RMS relies on

A hook is a command that Claude Code runs at a fixed moment. It gets a JSON object on stdin and may answer with JSON on stdout.

| event | when | stdin (fields RMS uses) | RMS answers |
|---|---|---|---|
| `SessionStart` | session opens (also after `/clear`, resume, compaction) | `session_id`, `cwd`, `source`, `transcript_path` | `systemMessage`: shown to the owner (the thread list). `additionalContext`: added to Claude's context (who it is, what to do). |
| `PreToolUse` | before each tool call matching the `matcher` (`Edit|Write|MultiEdit|NotebookEdit|Bash`) | `session_id`, `cwd`, `tool_name`, `tool_input` | nothing = normal permissions apply. `permissionDecision: "deny"` + reason = the call is blocked and Claude sees the reason. RMS never answers "allow", so the owner's permission prompts stay as they are. |
| `SessionEnd` | session exits | `session_id`, `reason` | nothing; RMS releases the claim |

The environment a command gets also matters:

- **Commands Claude runs** (Bash) get `CLAUDE_CODE_SESSION_ID` and `CLAUDE_PID`. That is how `rms claim` knows which session is claiming, so no ID is typed by anyone.
- **SessionStart hooks** get `CLAUDE_ENV_FILE`. RMS writes `export PATH=…` there, so the session can type plain `rms`.
- **Hooks get `CLAUDE_PROJECT_DIR`.** This is the folder `claude` was started in.

**Where Claude Code reads hooks from** (checked 2026-11-11, Claude Code 2.1.322):

| file | scope | in git? |
|---|---|---|
| `~/.claude/settings.json` | every session of this user, any folder | no |
| `<start folder>/.claude/settings.json` | sessions started **in that exact folder** | yes, shared with the project |
| `<start folder>/.claude/settings.local.json` | the same, personal | no |

Hooks from all three files run.

**Important:** project settings are read only from the folder `claude` is started in. They are not read from parent folders, and not from the git root either. This was tested on 2026-11-11: a hook in `<repo>/.claude/settings.json` fired for `claude` started in `<repo>/`, but not for `claude` started in `<repo>/sub/deeper/`.

`CLAUDE.md` is different. Claude Code reads it from every parent folder, and that difference is easy to miss.

**Decision (owner, 2026-11-11): hooks live only in the project's own folder,** in `<project>/.claude/settings.json` (in git) or `settings.local.json` (personal). They never go in `~/.claude/settings.json`. RMS has no option for that.

**Consequence: start `claude` in the project root.** A session started in a subfolder (`side/<name>/`, a thread folder) does not read the hooks. RMS does not see it and does not guard it. `rms doctor` prints this as a note.

The project's `CLAUDE.md` *is* read from subfolders, so it carries a line telling a session that got no RMS note to stop and ask the owner to restart in the root (see [adopting.md](adopting.md), step 3).

## Rules the hooks apply

- **Before a thread is held:** Edit and Write are denied. Bash runs only `rms …` and read-only commands (`ls`, `cat`, `grep`, `git log`/`status`/`diff`, …), optionally joined by `|`, `&&` or `||` and with `2>&1`. Anything else is denied with the reason.
- **With a thread held:** Edit and Write are allowed only inside the thread's folder (plus `shared_writable` and `extra_writable` from `rms.json`). Bash is not checked (a known limit).
- **Side session** (its folder is in `<side_dir>/<name>/`): writes only inside that folder, and it needs no claim. Because hooks are read only in the root, a side session started *in* its side folder is not seen by RMS. This case is still open (NOTES.md).
- **One live session per thread:** a claim is refused while the holder's process runs. "Live" means the PID is alive and has the same start time, so a reused PID doesn't count. If the holder is gone, the claim is *stale*: `--take-over` only after the owner says yes.
- **SessionEnd** releases the session's claim.
- **A hook never breaks Claude Code:** on an internal error it logs `hook-error` and lets the call through.

## What the owner sees and what Claude sees

- **On session start, in the terminal** (`systemMessage`): `RMS · <project> · session S-XXXX · no thread yet` and the thread list.
- **In Claude's context** (`additionalContext`): the session ID, the thread list, and the instruction. If the first message names a thread, claim it. Otherwise ask "Which thread?" (AskUserQuestion menu), then run `rms claim`.
- **No `CLAUDE.md` change is needed.** The text comes from RMS each time, so it is always current and the same in every project.

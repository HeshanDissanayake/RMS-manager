# RMS-manager

The core of RMS (Research Manager): one program that runs a long, multi-session research project. It keeps the registry and history of everything (threads, builds, data, experiments, milestones), runs the thread lifecycle as fixed commands, holds the locks, enforces the mechanical rules, tracks running jobs per thread, and serves all of it to frontends ([RMS-web](../RMS-web/)).

**Status:** step 1 built 2026-11-11: sessions, thread claims, Claude Code hooks, CLI, terminal UI; tried on a demo project (not yet connected to LLVM-regsw). Owner's private work (side project of `~/working_dir/LLVM-regsw`, not part of its records). First user: LLVM-regsw.

**Docs:** [`docs/how-it-works.md`](docs/how-it-works.md) (hooks, state, rules; where Claude Code reads hooks from) · [`docs/adopting.md`](docs/adopting.md) (putting RMS on a project, checklist).

**Read first:** [`NOTES.md`](NOTES.md) (the design as agreed so far, open questions, stages). Background: [`../2026-11-09-rms-system/SUMMARY.md`](../2026-11-09-rms-system/SUMMARY.md) (the system as built by hand in LLVM-regsw, 2026-11-07…09, and its weak points).

## What it will do

| area | what |
|---|---|
| registry and history | which build came from which thread, from which source commits and recipe; experiments and their results; milestones and the reference system; derived from git and the project's registry files |
| thread lifecycle | `thread create / resume / handover / close / archive / restore`, each a fixed sequence of steps (folders, index, branches, worktrees, locks, commits) |
| locks | thread locks (one session per thread) and resource locks (FPGA, Vivado), as plain files |
| rules | one checks library for every mechanical rule, called by git hooks, Claude Code hooks, a `check` command and the API |
| jobs and consoles | every running command per thread (PID, command, status) with a live log in `store/consoles/`, cleared after the job ends |
| API | read-only JSON + live streams for any frontend; later, actions |

## Step 1: sessions and thread claims (built)

A Claude Code session that starts in the project is registered by a hook; it claims a thread itself (`rms claim T01`, after asking the owner); while it is alive no other session can claim that thread. Until it holds a thread (or works in a side folder) RMS blocks its edits.

```
Claude Code ──SessionStart──▶ rms hook session-start   register; note to Claude: who it is, threads, how to claim
            ──Bash────────▶ rms claim T01            granted if free; refused if a live session holds it
            ──PreToolUse──▶ rms hook pre-tool-use    edits allowed only in its thread (or side) folder
            ──SessionEnd──▶ rms hook session-end     claim released
owner ─────────────────▶ rms tui                    threads, sessions, events live; release a stale claim
```

### Try it

```bash
python3 -m venv .venv && .venv/bin/pip install -e .     # once (Textual is the one dependency)
cd sandbox/demo-project
../../.venv/bin/rms tui                                   # terminal 1: watch
claude                                                    # terminal 2: a session (hooks in .claude/settings.json)
claude                                                    # terminal 3: a second session; try the same thread
```

In the TUI: arrows/tab to move, `enter` details, `x` release a claim, `e` show ended sessions, `q` quit.

### Commands

| command | what |
|---|---|
| `rms adopt [dir] [--local]` | put RMS on a project: `rms.json` + hooks (merged) + `doctor` |
| `rms doctor [dir]` | check a project's setup: config, folders, git-ignore, hooks and the `rms` path |
| `rms init [dir]` | create `rms.json` only |
| `rms install-hooks [dir] [--local] [--print]` | merge RMS's hooks into a settings file (others kept), or print them |
| `rms threads` / `rms sessions [--all]` | threads with their claims / sessions |
| `rms whoami` | this session: thread, side folder, what it may write |
| `rms claim T<NN> [--take-over] [--allow-done]` | claim a thread for this session |
| `rms release [T<NN>]` | free this session's thread; from the owner's terminal any thread |
| `rms status [T<NN>]` | a thread's State and How to resume |
| `rms log [-n N]` | recent events |
| `rms tui` | the terminal UI |
| `rms hook <event>` | called by Claude Code |

State is plain JSON under the project's `store/rms/` (`sessions/`, `claims/`, `events.jsonl`). Tests: `.venv/bin/python -m unittest discover -s tests`.

**Where the hooks go:** only in the project's own `.claude/settings.json` (or `settings.local.json`), never at user level (owner, 2026-11-11). Claude Code reads them only when `claude` starts in that exact folder, so **sessions start in the project root**: see [`docs/how-it-works.md`](docs/how-it-works.md).

**Known limit:** once a session holds a thread, its Bash commands are not checked (only Edit/Write are confined to the thread folder). Before it holds one, only read-only commands and `rms` run.

## Layout

```
RMS-manager/
├── README.md, NOTES.md, CLAUDE.md
├── docs/                   how-it-works.md, adopting.md
├── pyproject.toml          package `rms`, command `rms`
├── rms/                    config, store, procs, threads, sessions, claims, hooks, setup, cli, tui
├── tests/                  unittest (sessions, claims, hooks, TUI)
└── sandbox/demo-project/   demo project: rms.json, threads/, side/, .claude/settings.json
```

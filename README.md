# RMS-manager

The core of RMS (Research Manager): one program that runs a long, multi-session research project. It keeps the registry and history of everything (threads, builds, data, experiments, milestones), runs the thread lifecycle as fixed commands, holds the locks, enforces the mechanical rules, tracks running jobs per thread, and serves all of it to frontends ([RMS-web](../RMS-web/)).

**Status:** step 1 built 2026-11-11: sessions, thread claims, Claude Code hooks, CLI, terminal UI; tried on a demo project (not yet connected to LLVM-regsw). Owner's private work (side project of `~/working_dir/LLVM-regsw`, not part of its records). First user: LLVM-regsw.

**Docs:** [`docs/SPEC.md`](docs/SPEC.md) (**the specification**: everything RMS does) · [`docs/adopting.md`](docs/adopting.md) (putting RMS on a project, checklist).

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
sandbox/reset-demo.sh                                     # (re)build the sandbox root
cd sandbox/demo/root
../../../.venv/bin/rms tui                                   # terminal 1: watch
claude                                                    # terminal 2: a session (hooks in .claude/settings.json)
claude                                                    # terminal 3: a second session; try the same thread
```

In the TUI: arrows/tab to move, `enter` details, `t` open the selected thread in the **Thread** tab (sources and their state, builds, experiments, disk use, running processes; `r` re-measures), `x` release a claim, `c` approve a close, `d`/`v` settle a warning, `e` show ended sessions, `q` quit.

### Commands

| command | what |
|---|---|
| `rms adopt [dir] [--local]` | put RMS on a project: `rms.json` + hooks (merged) + `doctor` |
| `rms install-mod [dir] [--local]` | turn the rms-thread Claude Code mod on for this project only |
| `rms doctor [dir]` | check a project's setup: config, folders, git-ignore, hooks and the `rms` path |
| `rms init [dir]` | create `rms.json` only |
| `rms install-hooks [dir] [--local] [--print]` | merge RMS's hooks into a settings file (others kept), or print them |
| `rms threads` / `rms sessions [--all]` | threads with their claims / sessions |
| `rms whoami` | this session: thread, side folder, what it may write |
| `rms claim T<NN> [--take-over] [--allow-done]` | claim a thread for this session |
| `rms release [T<NN>]` | free this session's thread; from the owner's terminal any thread |
| `rms create "<name>" [--from T<NN>\|M<N>]` | new thread: branch, checkout, folder, INDEX row |
| `rms handover [-m "…"]` | step 1: what to write in THREAD.md; step 2: check, commit, merge into main, release |
| `rms approve-close T<NN>` | owner only: the yes that `rms close` needs (once) |
| `rms close --milestone\|--merge-into T<P>\|--no-merge -m "…"` | close the thread: milestone M<N> (tags), merge point, or nothing |
| `rms reopen T<NN>` · `rms archive T<NN>` · `rms restore T<NN>` | reopen a done thread; pack/unpack its data (sha256) |
| `rms build add\|use\|unuse\|ignore …` | record what the thread built / uses (RMS fills in source commits) |
| `rms thread [T<NN>] [--json]` | all of a thread: sources and their state, builds, experiments, disk use, processes running for it (the TUI's Thread tab) |
| `rms ticket "…" [--kind bug\|idea\|question] [-m "…"]` | from any project (or none): a ticket for this repo, `tickets/RMS-<NNN>.md`, with project, thread, session and recent events attached. Also `/ticket` in Claude Code, `n` in the TUI. Claude sessions may file them too |
| `rms tickets [show\|close RMS-<NNN> -m "…"] [--all]` | list, read, close tickets (closing: here in RMS-manager, not from a project session) |
| `rms usage [T<NN>\|none] [--json]` | Claude tokens (input + output) per thread, per model with each model's share, cache counts, and the sessions (from Claude Code's transcripts; also in the Thread tab and the band; each handover commits it as the thread's `claude.json`) |
| `rms store [T<NN>]` · `rms builds` · `rms env [T<NN>]` | a thread's builds, uses and data; all builds; shell exports |
| `rms scan` · `rms gc` | find unrecorded builds and data (with guesses; they become warnings); list clean-up candidates (removes nothing) |
| `rms warnings [dismiss\|resolve <id>]` | mismatches for the owner to settle (owner only); also a TUI pane |
| `rms status [T<NN>]` | a thread's State and How to resume |
| `rms log [-n N]` | recent events |
| `rms tui` | the terminal UI |
| `rms hook <event>` | called by Claude Code |

State is plain JSON under the project's `store/rms/` (`sessions/`, `claims/`, `events.jsonl`). Tests: `.venv/bin/python -m unittest discover -s tests -t .`.

**Where the hooks go:** only in the project's own `.claude/settings.json` (or `settings.local.json`), never at user level (owner, 2026-11-11). Claude Code reads them only when `claude` starts in that exact folder, so **sessions start in the project root**: see [`docs/SPEC.md`](docs/SPEC.md) §6.

**Known limit:** once a session holds a thread, its Bash commands are not checked (only Edit/Write are confined to the thread folder). Before it holds one, only read-only commands and `rms` run.

## Claude Code mod: `mods/rms-thread`

In an RMS project a session that holds no thread gets a picker at start ("Which thread will this session work on?"); the band right above the prompt then shows `RMS T01 fast sort · store/worktrees/T01/ · 940k tok (opus 5.5 92%, haiku 5.5 8%)` (and "handover due"). `/thread` picks or shows it. Source in `mods/rms-thread/` (tests: `claude plugin test mods/rms-thread`). This repo is its marketplace (`.claude-plugin/marketplace.json`). **Per project:** `rms install-mod <project>` (project scope: only `claude` started there loads it; the sandbox reset does this). For every folder: `/plugin install rms-thread --marketplace HeshanDissanayake/RMS-manager` (a new machine: `docs/adopting.md`, "Moving to a new machine"). RMS's UserPromptSubmit hook tells Claude about a thread picked there.

## Layout

```
RMS-manager/
├── README.md, NOTES.md, CLAUDE.md
├── docs/                   SPEC.md (the spec), adopting.md
├── mods/rms-thread/        Claude Code mod: thread picker at start, thread band above the prompt
├── pyproject.toml          package `rms`, command `rms`
├── rms/                    config, store, procs, git, threads, index, sessions, claims, lifecycle, inventory, warns, threadinfo, hooks, setup, cli, tui
├── tests/                  unittest (sessions, claims, hooks, TUI)
├── sandbox/demo-project/   template of the demo project (rms.json, threads/, side/)
└── sandbox/reset-demo.sh   builds sandbox/demo/root: a git root with source submodules (not in git)
```

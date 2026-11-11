# RMS-manager: design notes

Written 2026-11-11 from the owner's discussion in the LLVM-regsw session of 2026-11-09 … 11. "Agreed" = the owner said yes; "proposed" = suggested, not yet decided. Nothing is built.

## 1. Why

In LLVM-regsw the method was run by sessions reading rules and editing files by hand. That drifted (timeline rows pointing at the wrong commits), cost tokens every session, faded in long sessions, and nothing stopped two sessions on one thread. The owner wants one program that does the deterministic part and shows the project's state and progress to any frontend.

## 2. Principles

1. **Files + git are the source of truth** (agreed in spirit). The manager reads them and writes only through normal commits. Its own database is a cache it can always rebuild. A private database as the truth would drift from git, the problem that was just removed.
2. **Write by hand only what git cannot know; derive the rest** (agreed, LLVM-regsw `RULES.md` R7). History = commits with `Thread:`, `Spawns:`, `Merges:`, `Milestone:`, `Rule:` lines.
3. **The core works without the server.** CLI + checks library + plain lock files; git hooks and Claude Code hooks call the same checks. If the server is down, protection still holds.
4. **Deterministic steps, not prose.** Every lifecycle action is a fixed command; skills/sessions only call it and write the judgement text (State, How to resume).
5. **Never delete without the owner** (LLVM-regsw R27). The manager can *offer* a removal; it runs only on the owner's confirmation. Exception proposed for consoles (section 7).
6. **Generic, project as config.** Nothing about FPGA/Spike/LLVM in the core; LLVM-regsw is described by one config file (paths, rules, runners, resources).

## 3. Registry and history

- Which build consists of which thread: each build record = thread, source commits (from the submodule pointers), recipe, output path, cost (cheap / expensive), pinned or not. The manager writes it itself when it runs a build.
- Registry files become machine-readable (proposed): `builds.md` → `builds.yaml`; `catalog.csv` stays; `THREAD.md` and EXP READMEs get a small structured header (status, since, spawned from, merged into, reference milestone); `INDEX.md` is generated from the headers.
- Milestones (agreed): M\<N\> = a thread merged into main; the latest is the reference system. Merge point M-T\<NN\>-\<n\> = a thread merged into its parent. Tags only `milestone-<N>` (and compiler version tags).
- Source repos (agreed): submodule pointers in the records repo, **record only** (never `git submodule update`); each thread's source branch is its own and never rewritten; repos outside the project folder (Chipyard, kernel) pinned in the thread notes + milestone tags. LLVM-regsw R19–R26.

## 4. Thread lifecycle (commands)

| command | steps (draft) |
|---|---|
| `thread create <name> [--from M<N> / T<NN>]` | folder from template, structured header, INDEX entry, own branches in the source repos it will change, claim lock, commit with `Spawns:` |
| `thread resume <T>` | claim lock (or report holder), print State + How to resume, `thread-log`, `git submodule status` (moved / uncommitted repos) |
| `thread handover <T>` | check State/How to resume updated, source repos committed, pointers staged, INDEX updated; commit; release lock; clear consoles |
| `thread close <T> [--milestone / --merge-into T]` | final State, milestone entry or merge point, open items handed on, INDEX "Done", commit |
| `thread archive <T>` / `thread restore <T>` | move the thread's data/builds to the archive tier with sha256 / bring them back and verify |
| worktrees | created under `store/worktrees/` and recorded by the manager; only for a second parallel session (all threads otherwise on `main`) |

## 5. Locks

- **Thread lock** (agreed): one session per thread. File `store/locks/thread-T03`: session id, host, start, last seen. Claimed at resume/create, refreshed on every action, released at handover. Stale after a set time (e.g. 2 h) → taken over only after asking the owner. Enforced by a Claude Code hook (edits under the thread's folder, commits with its `Thread:` line) and the git commit-msg hook. Shared files (INDEX, T00, shared/, RULES.md) take a short lock only while editing.
- Resource locks: as `xlock` today (FPGA, Vivado).
- Locks never depend on the server running; the server shows them and can free a stale one on the owner's click.

## 6. Rules: enforce what is mechanical

From LLVM-regsw `RULES.md` (R1–R50). Mechanical → checks library + hooks; judgement (plan first, ISA "simple", fair comparison, reporting style) → stays as text, loaded by skills when relevant.

| rule (LLVM-regsw) | enforcement |
|---|---|
| R13 `Thread:` line | git commit-msg hook |
| R14 never sweep the owner's edits | pre-commit hook (owner-kept files) |
| R17 never push | pre-push hook unless the owner allows |
| R16 tag names | check |
| R21 never move source checkouts | `update = none` per submodule in `.gitmodules` |
| R27, R4 never delete / stay in project | Claude Code permission rules |
| R8, R22, R23 handover / resume | `check handover`, `check resume` |

Keeping rules from fading in long sessions (proposed): hooks (above); a SessionStart hook that re-adds a short rules digest after the conversation is summarised; the ~10 never-break rules in CLAUDE.md; skills `/handover`, `/resume`, `/experiment`, `/report` (+ `/housekeeping`) that load their rules when used.

## 7. Jobs and consoles

- **PIDs** (agreed in principle): a Claude Code hook registers every command a session starts (PID, thread from the thread lock, command, folder). The manager reads `/proc/<PID>` for status, CPU, memory, children.
- **Output:** cannot be read from a PID alone. A session's foreground output is a pipe to Claude Code (one reader; reading it would steal it), `ptrace` is blocked (`ptrace_scope = 1`, no sudo). Background jobs write to a file → can be followed.
- **Consoles** (owner's idea, 2026-11-11): `store/consoles/<thread>/<session>/<job>.log` + `<job>.json` (pid, command, folder, start, status, exit code). Live logs only. Foreground commands: a hook wraps them with `tee` into the console (to verify: can a Claude Code hook rewrite a command?); background: link to Claude Code's output file; runner jobs (FireSim, benchmarks): link to the experiment's own log (never a copy).
- **Clearing** (proposed rule R51 for LLVM-regsw, not yet approved): an entry is cleared at the thread's handover or 24 h after the job ended; anything worth keeping is moved to the experiment's `logs/` first; experiment logs are never inside `consoles/`.
- Experiment logs are full captures already (checked 2026-11-11 in LLVM-regsw T03): runner console `firesim-<core>.log` (all commands and output, e.g. 8,299 lines), target console `uartlog`, hardware print statements `synthesized-prints.out0` (the measurement), parsed CSVs. Not captured today: commands a session runs directly (only in its transcript) → the consoles close that gap.
- `run <T> -- <cmd>`: thin starter for long jobs: output to the thread's log folder + registration.

## 8. API (for RMS-web and sessions)

Read: threads and status, locks, reference milestone, milestones, experiments + results (`summary.csv`), builds + provenance, disk use, running jobs, recent commits. Live: job list changes and console streams (WebSocket or server-sent events). Later, actions (approve a plan, start a run, free a stale lock, confirm a removal). Also a compact `status <T>` for sessions (fewer tokens than reading five files).

## 9. Stages

1. Checks library + git hooks + `check` command; thread locks; consoles + PID registration hook; submodule setup in LLVM-regsw. (Useful without anything else.)
2. Structured headers; generated INDEX; builds registry as data; lifecycle commands.
3. Read-only API server.
4. RMS-web dashboard; then actions.

## 10. Decisions (owner, 2026-11-11)

- **Language:** Python 3.13.
- **Config format:** JSON.
- **Name:** RMS = Research Manager.
- **Remote:** `github` → `HeshanDissanayake/RMS-manager` (set up by the owner). Sessions commit; the owner pushes.
- **Way of working:** step by step, not the whole plan at once. First step: how a Claude session starting in the project becomes known to RMS and gets the owner's permission to work on a thread, and how it manages that thread. A **terminal UI** comes before the web UI so the owner can run the app himself.

## 11. Open questions

- Can Claude Code hooks rewrite a command (for `tee` into consoles)? Later.
- Rule R51 (consoles clearing) for LLVM-regsw: needs the owner's approval.
- How the manager is installed/used by LLVM-regsw (path dependency, pip install from this repo, …) without breaking "nothing system-wide" (R33). Later.

# RMS specification

RMS (Research Manager) runs a long, multi-session research project that Claude Code sessions and the owner work on together. This file describes **everything RMS does**: the concepts, every command, the hooks and the rules they enforce, every file RMS reads or writes, and what the owner sees. It is the reference; when the code and this file disagree, one of them is a bug.

- Putting RMS on a project, step by step: [adopting.md](adopting.md).
- Why things were decided, with dates: [`NOTES.md`](../NOTES.md).
- Status: built and tested on the sandbox (`sandbox/reset-demo.sh` → `sandbox/demo/root`); not yet on LLVM-regsw.

**Contents**

1. [Concepts](#1-concepts)
2. [The pieces and where they live](#2-the-pieces-and-where-they-live)
3. [Configuration: `rms.json`](#3-configuration-rmsjson)
4. [Sessions and claims](#4-sessions-and-claims)
5. [Thread lifecycle](#5-thread-lifecycle)
6. [Hooks: what Claude Code calls, and the rules](#6-hooks-what-claude-code-calls-and-the-rules)
7. [Builds and data](#7-builds-and-data)
8. [Warnings](#8-warnings)
9. [A thread at a glance](#9-a-thread-at-a-glance)
10. [Claude usage per thread](#10-claude-usage-per-thread)
11. [Tickets for the RMS repo](#11-tickets-for-the-rms-repo)
12. [Command reference](#12-command-reference)
13. [Terminal UI](#13-terminal-ui)
14. [The Claude Code mod `rms-thread`](#14-the-claude-code-mod-rms-thread)
15. [Files and formats](#15-files-and-formats)
16. [Events](#16-events)
17. [Principles](#17-principles)
18. [Known limits and open tickets](#18-known-limits-and-open-tickets)

---

## 1. Concepts

| term | meaning |
|---|---|
| **project** | a folder with an `rms.json` at its root (LLVM-regsw, the sandbox). A git repo, "the root". |
| **thread** | one line of work: `T<NN>`, a folder `threads/T<NN>-<name>/` with `THREAD.md`, a row in `threads/INDEX.md`, and its own branch `thread/T<NN>`. |
| **T00** | the main thread. It **is** `main`: no own branch, no own checkout (it works in the root checkout), never closed. |
| **checkout** | the thread's own working copy of the root, a git worktree at `store/worktrees/T<NN>/`. Sessions on the thread work there, never in the root. |
| **source repo** | a git submodule of the root (`src/<repo>`), recorded only (`update = none`). Inside a thread's checkout it is detached at the thread's recorded commit until the thread changes it; then it gets the branch `thread/T<NN>`. |
| **session** | one Claude Code session, known by Claude Code's session id; shown as `S-XXXX` (first 4 hex digits). |
| **claim** | "this session works on this thread now". At most one **live** session per thread. |
| **area** | where the claiming session may write: its thread folder and its source repos, in its checkout (T00: its folder in the root only). |
| **handover** | the end of a session's work on a thread: Claude writes the handover note in `THREAD.md`, RMS checks, commits, merges into main, releases. |
| **milestone** | `M<N>`: a thread closed as the new reference; tagged `milestone-<N>` in the root and every source repo. |
| **side session** | a session started in `side/<name>/`: outside RMS by design. |
| **owner** | the person running the project. Some actions are the owner's alone (closing approval, settling warnings, releasing someone else's claim, closing tickets in RMS-manager). |

## 2. The pieces and where they live

```
 Claude Code session ── hooks ──▶ rms hook <event> ─┐
                     ── Bash:  rms claim / handover … ─┤
 rms-thread mod (picker, band, /thread, /ticket) ─────┤      finds the project: rms.json,
 owner ── terminal: rms …  ·  rms tui ────────────────┘      upwards from the current folder
                                   │
          ┌────────────────────────┼─────────────────────────────┐
          ▼                        ▼                             ▼
 <project>/store/rms/       <project> git (root, threads,   <RMS-manager>/tickets/
 sessions, claims, events,  thread branches, worktrees,     tickets from every project
 warnings, usage …          source submodules)
 (per machine, not in git)  (the record)
```

| piece | where | notes |
|---|---|---|
| the program `rms` | this repo, `rms/`; installed once per machine: `.venv/bin/rms` | one install serves every project |
| project config | `<project>/rms.json` | §3 |
| state | `<project>/store/rms/` | per machine, git-ignored; plain JSON, one lock (§15) |
| records | in the project's git: `threads/…/THREAD.md`, `store.json`, `claude.json`, INDEX, milestones | go to main at handover |
| hooks | `<project>/.claude/settings.json` (or `settings.local.json`) | §6; only in the project's own folder |
| mod | this repo, `mods/rms-thread/`; turned on per project (`rms install-mod`) | §14 |
| tickets | this repo, `tickets/` | §11 |

Code map: `cli.py` (commands), `hooks.py`, `sessions.py`, `claims.py`, `lifecycle.py` (branches, checkouts, handover, close, archive), `inventory.py` (builds, data, scan, gc), `warns.py`, `threadinfo.py` (thread view), `usage.py` (Claude tokens), `tickets.py`, `threads.py` + `index.py` (thread folders, INDEX), `git.py`, `procs.py`, `store.py` (files, lock, events), `setup.py` (install, doctor), `tui.py`, `config.py`.

## 3. Configuration: `rms.json`

All keys are optional; paths are relative to the project root.

| key | default | meaning |
|---|---|---|
| `name` | folder name | shown in the TUI, tickets |
| `threads_dir` | `threads` | one folder per thread, `T<NN>-<name>/` |
| `index_file` | `threads/INDEX.md` | thread tables (`ID \| Thread \| Status \| Since …`) |
| `side_dir` | `side` | side sessions: `side/<name>/` |
| `state_dir` | `store/rms` | RMS's state (§15) |
| `shared_writable` | `[]` | globs (in the project) any thread session may write |
| `extra_writable` | `["/tmp/claude-*"]` | globs outside the project any session may write |
| `main_branch` | `main` | the root's main branch |
| `main_thread` | `T00` | the thread that is main |
| `thread_branch` | `thread/{id}` | a thread's branch, in the root and in source repos |
| `worktrees_dir` | `store/worktrees` | thread checkouts: `<worktrees_dir>/T<NN>/` |
| `milestones_file` | `threads/T00-main/milestones.md` | the `M<N>` table |
| `data_dir` | `store/data` | a thread's large data: `<data_dir>/T<NN>/` |
| `archive_dir` | `store/archive` | archives + `catalog.csv` |
| `builds_dir` | `store/builds` | builds: `<builds_dir>/<tool>/<name>/` |
| `gc_weeks` | `4` | clean-up: keep what changed within this many weeks |

The project is found from the current folder upwards (`--project` overrides). Run inside a thread's checkout, `rms` still works on the real root (a checkout is recognised by its `.git` file).

Environment variables: `RMS_PROJECT` (the project root; the SessionStart hook sets it for the session), `RMS_BIN` (where the mod finds `rms`), `RMS_HOME` (the RMS-manager repo for tickets), `RMS_CLAUDE_PROJECTS` (where Claude transcripts are; default `$CLAUDE_CONFIG_DIR/projects` or `~/.claude/projects`).

## 4. Sessions and claims

**Registration.** The SessionStart hook registers each session that starts in the project: id, folder, side folder (if any), Claude's PID and its start time, transcript path. A session the hook missed is registered the first time it runs `rms` (`lazy`). A resumed session is registered again (`resume`).

**Session states.** `live` (its Claude process runs: same PID **and** same start time, so a reused PID does not count) · `gone` (process vanished without SessionEnd) · `ended` (SessionEnd ran).

**Claiming** (`rms claim T<NN>`, run by the session itself, or by the mod's picker for it):
- refused while another **live** session holds the thread (says who);
- a **stale** claim (holder gone) needs `--take-over`, only with the owner's yes;
- a **done** thread needs `--allow-done`, only with the owner's yes;
- a session holds at most one thread; claiming another is refused until it releases its own (`rms release` or handover);
- on success RMS opens the thread's checkout (§5.2), records the areas, prints the checkout, the thread's State and How to resume, and its builds and data.

**Releasing.** By the session (`rms release`), by its handover, by SessionEnd, or by the owner (`rms release T<NN>` asks y/N; TUI `x`). A session can release only its own claim.

**Side sessions.** Started in `side/<name>/`: not managed (no hooks read there). A session started in the root that works in `side/<name>/` may write only in that folder and needs no claim.

## 5. Thread lifecycle

```
main ── T00, INDEX, every thread's records (merged at handover); source pointers move only at a milestone
 ├─ thread/T01 ── checkout store/worktrees/T01/   (src/<repo>: detached, or thread/T01 once changed)
 └─ thread/T04 ── checkout store/worktrees/T04/
```

### 5.1 Create — `rms create "<name>" [--from T<NN>|M<N>|main] [--status parked|paused|active] [--goal "…"]`
New id (next free `T<NN>`); branch `thread/T<NN>` from the origin; checkout; folder from the template (`THREAD.md`, `decisions.md`, `hypotheses.md`, `experiments/`); commit on the branch; INDEX row on main with trailer `Spawns:`. Default status `parked`.

### 5.2 Checkout and sources
At claim, the checkout `store/worktrees/T<NN>/` is created if missing. Source repos are checked out at the thread's recorded commit **without a branch**. A source repo gets the branch `thread/T<NN>` only when the thread changes it: at the first Edit/Write there (the hook makes the branch), or at handover if it has commits or changes. T00 uses the root checkout and may not change the root's sources.

### 5.3 Handover — `rms handover`, then `rms handover -m "<one line>"`
1. **First call:** marks the handover due (the band shows "handover due") and prints what to write in `THREAD.md`: *State as of <today>*, *How to resume*, *Where things are*, *Open items*; spawned ideas; commit the sources. RMS never writes the note.
2. **Second call** checks: `THREAD.md` changed since step 1, State dated today, How to resume not empty, sources committed on `thread/T<NN>`, nothing changed outside the thread folder. If something fails: "Handover not done yet" with the list; nothing changes.
3. Then: writes the thread's `claude.json` (§10); commits `T<NN> handover <date>: <line>` on the branch (trailer `Thread:`); merges into main keeping main's source pointers; INDEX status → `paused`; new warnings (§8); releases the claim.
- T00: one commit on main, nothing to merge.
- The INDEX status changes **only** at handover, close, create and reopen. "In use" lives in RMS's claims, not in the INDEX.

### 5.4 Close — `rms approve-close T<NN>` (owner), then `rms close …` (holder)
- `rms close` refuses without the owner's yes: `rms approve-close T<NN>` in the owner's own terminal, or `c` in the TUI. Sessions are blocked from running it. A yes is used once.
- `--milestone -m "…"`: final State checked; merge into main **with** the source pointers; `M<N>` row in the milestones file; tag `milestone-<N>` in the root and every source repo; builds pinned (§7); INDEX → Done.
- `--merge-into T<P> -m "…"`: merges sources and branch into the parent's (merge point `M-T<P>-<n>`); records into main; INDEX → Done.
- `--no-merge -m "…"`: records into main; INDEX → Done.
- T00 is never closed.

### 5.5 Reopen, archive, restore
- `rms reopen T<NN>` (owner): INDEX row back to open, status `paused`.
- `rms archive T<NN> [--exp EXP]` / `--build NAME`: done threads only; `.tar.gz` in `archive_dir`, sha256 in `catalog.csv`. Nothing is deleted; the owner removes the working copy.
- `rms restore T<NN> [--exp EXP] [--file F]` / `--build NAME`: unpacks after checking the sha256.

### 5.6 Git rules
- Tags: only `milestone-<N>`. Each handover commit already records the exact source commits.
- Submodule pointer conflicts are settled by rule: handover keeps main's pointer, a milestone takes the thread's, a merge point takes the parent's merged commit. Any other conflict aborts and changes nothing.
- RMS never moves the root's own source checkouts (`update = none`); after a milestone the root shows `src/<repo>` as "new commits", which is expected.
- Commit trailers RMS writes: `Thread:`, `Spawns:`, `Merges:`, `Milestone:`.

## 6. Hooks: what Claude Code calls, and the rules

### 6.1 Where hooks live
Only in `<project>/.claude/settings.json` (in git) or `settings.local.json` (personal); never user level (owner's decision). Claude Code reads project settings **only from the folder `claude` starts in** (tested 2026-11-11): not from parents, not from the git root. So **sessions start in the project root**; the project's `CLAUDE.md` (which *is* read from subfolders) tells a session without the RMS note to stop and ask for a restart there. `rms install-hooks` merges RMS's entries, never replacing other hooks.

### 6.2 The four hooks
| event | RMS does | answers |
|---|---|---|
| `SessionStart` | registers the session; writes `export PATH=…` and `RMS_PROJECT` to `CLAUDE_ENV_FILE` so Claude can type `rms` | `systemMessage` for the owner: `RMS · <project> · session S-XXXX · <thread or "no thread yet">` + thread list. `additionalContext` for Claude: who it is, the threads, what to do (below) |
| `PreToolUse` (Edit, Write, MultiEdit, NotebookEdit, Bash) | refreshes "last seen"; logs every Bash command to `commands.jsonl`; applies the rules (§6.3); makes a source repo's thread branch at its first edit | nothing (normal permissions) or `deny` with the reason. Never "allow": the owner's permission prompts stay |
| `UserPromptSubmit` | — | `additionalContext` only when the session's thread changed since Claude was last told (picked in the mod, released by the owner) |
| `SessionEnd` | releases the session's claim, marks it ended, records its Claude usage (§10) | nothing |

**What Claude is told at start:** it holds no thread, so writes are blocked; if the owner's first message names a thread, claim it at once; otherwise ask "Which thread?" (a menu) and claim the answer; a refused claim → say who holds it; a stale one → only with the owner's yes. Then work only in the checkout, and `rms handover` when the work ends. Also: `rms close` needs the owner's `approve-close` (never run it); problems with RMS itself → `rms ticket` (§11).

**A hook never breaks Claude Code:** on an internal error it logs `hook-error` and lets the call through.

### 6.3 The rules
| situation | Edit / Write | Bash |
|---|---|---|
| no thread held | denied | only `rms …` and read-only commands (`ls`, `cat`, `grep`, `find`, `git status/log/diff/show/…`, …), joined by `\|`, `&&`, `\|\|`, with `2>&1`; anything else denied |
| thread held | only inside its areas (+ `shared_writable`, `extra_writable`) | not checked (known limit, RMS-001) |
| root session working in `side/<name>/` | only inside that folder | as above |
| any session | — | denied: `rms approve-close`, `rms warnings dismiss\|resolve`, `rms tickets close` (the owner's) |

## 7. Builds and data

RMS **records and reconciles**; it never builds and never runs a recipe.

- **Record, one file per thread:** `threads/T<NN>-<name>/store.json` — `builds` (made by the thread: name, path, source repo + commit, recipe, cost, env var, shared, note, milestones), `uses` (other threads' builds by name), `ignore` (found folders that are not builds), `milestone_uses`. Written in the thread's checkout; goes to main with the handover.
- **Data needs no record:** everything under `store/data/T<NN>/` belongs to T<NN> (experiment data in `store/data/T<NN>/<EXP>/`).
- **Builds live in** `store/builds/<tool>/<name>/`; a thread's own: `<tool>/T<NN>-<name>`. T00's builds are the shared base.
- `rms build add <path> [--name] [--tool] [--source src/<repo>] [--recipe "…"] [--cost cheap|expensive] [--env VAR] [--shared] [--note]` — RMS fills in the source commit from the thread's checkout (guessed source if not given).
- `rms build use|unuse <name>`, `rms build ignore <path>`.
- **Finding what sessions did not tell** (`rms scan`): unrecorded build folders, recorded builds missing on disk, data of unknown threads. For an unrecorded folder it guesses the thread (1. its name `T<NN>-…`, 2. its build files `CMakeCache.txt`/`config.log` pointing into a checkout, 3. a logged Bash command naming the path, 4. the single thread claimed when its files were written), the source (build files) and the recipe (logged commands). Finds become warnings (§8).
- **Clean-up** (`rms gc [--weeks N]`): candidates are builds no open thread or milestone needs and data of done threads, unchanged for N weeks. Cheap → the owner may remove; expensive or data → archive first. Removes nothing.
- **Milestones pin** the thread's builds and the builds it uses; pinned builds are never candidates.
- `rms env [T<NN>]` prints `export VAR=<path>` for the thread's builds.

## 8. Warnings

Mismatches never stop work; they become warnings in `store/rms/warnings.json`, found at handover and close (that thread) and by `rms scan` (everything).

| kind | when |
|---|---|
| `unrecorded-build` | a build folder no thread recorded (with the guessed thread and source) |
| `missing-build` | a recorded build not on disk and not archived |
| `unknown-data` | a data folder of no known thread |

- Shown by `rms warnings [--all] [--thread T<NN>]` and the TUI Warnings pane.
- **Resolved** automatically when a later check no longer finds the cause; or by the owner (`rms warnings resolve <id>`, TUI `v`).
- **Dismissed** by the owner (`rms warnings dismiss <id> [--note]`, TUI `d`): accepted as is; stays dismissed.
- Only the owner settles warnings (sessions are blocked).

## 9. A thread at a glance

`rms thread [T<NN>] [--json]` and the TUI's Thread tab show:

| part | content |
|---|---|
| head | id, name, INDEX status, claim (free/live/stale, by whom), branch, checkout |
| sources | each source repo: branch (or detached = unchanged), commit, commits on top of main's pointer, uncommitted changes, size. No checkout: what the branch records. T00: root's checkouts (record only) |
| builds | made (with source commit, cost) and used, sizes; missing ones marked |
| experiments | `experiments/EXP-*/`: id, status (`**Status:**`), title (`# EXP-…: title`), `summary.csv` present, record size, data size (`<data_dir>/T<NN>/<EXP>/`) |
| disk | sources, builds made, builds used, data, records; total of the thread's own |
| Claude | tokens by model and the sessions (§10) |
| running now | processes started by the Bash tool of the session holding the thread (not Claude's own helpers such as MCP servers), and any process whose working folder is in the thread's checkout, data or builds; PID, running time, why, folder, command as typed |

`rms status [T<NN>]` prints only the thread's State and How to resume (what a session reads first).

## 10. Claude usage per thread

```
~/.claude/projects/<folder>/<session>.jsonl (+ <session>/subagents/*.jsonl)   Claude Code's transcripts
          │  every assistant message: model + tokens
          ▼  read by: rms usage · TUI (every 10 s) · SessionEnd · whoami --json · handover
store/rms/usage/<session>.json      per session: tokens per thread per model, and the details
          ▼  at every handover
threads/T<NN>-*/claude.json         the thread's record, committed
```

- **Recorded per session:** models; per model input, output, cache write, cache read, messages; prompts; tool calls by tool; subagents; title; first and last time; Claude Code version; lines added/removed; API time; cost; registered or not.
- **Headline = input + output tokens** (and each model's share of it). Cache write and cache read are kept as records beside it. Thinking is part of output. A message spread over several transcript lines counts once (by its id).
- **Which thread a message counts for:** 1. the thread the session held at that moment (claim/release events); 2. else the thread whose checkout it worked in; 3. else a **guess** from what the session touched (files under `threads/T<NN>-*/` or a checkout, `rms claim T<NN>`, a prompt or menu answer naming `T<NN>`; latest guess before the message, the first guess for earlier ones), marked "guessed"; 4. else "no thread". Side sessions are never guessed.
- **Sessions RMS never saw** (no hooks, before RMS): any transcript in a Claude folder under the project whose working folder is in the project; marked "unregistered".
- **Background calls** (titles, summaries) appear only in the transcript's cost record; the part not in the messages counts for the thread the session spent most on.
- **Cost:** Claude Code's own cost per model for the session, split between threads by price weights (input 1, cache write 1.25 or 2 for the 1-hour cache, cache read 0.1, output 5). An estimate.
- **Kept:** RMS's copy outlives Claude Code's transcript clean-up; `claude.json` keeps sessions only an older record knows.
- Only changed transcripts are re-read (size and time stamp).
- Shown: `rms usage` (all threads: tokens, share, cost, model mix), `rms usage T<NN>|none [--json]`, TUI (Overview Tokens column and top line; Thread tab sections), the mod's band.

## 11. Tickets for the RMS repo

Any project (or no project) can send a ticket about RMS itself to **this** repo; a later session here picks it up. One RMS install serves every project, and it knows its own repo (the folder above the `rms` package; `RMS_HOME` overrides).

```
any project                                          RMS-manager
  rms ticket "…"  ·  /ticket …  ·  TUI n    ──►      tickets/RMS-<NNN>.md (open)
                                                       │ session here lists open tickets at start;
                                                       ▼ the owner picks one
                                                     rms tickets close RMS-<NNN> -m "…"  + commit
```

- `rms ticket "<text>" [--kind bug|idea|question|note] [-m "<detail>"]` — by the owner or a Claude session (sessions are told how). Attached automatically: who, when, project, thread, session, folder, RMS commit, the project's last 12 events. Filing never registers or changes a session; it adds a `ticket` event in the project.
- `rms tickets [--all] [--json]`, `rms tickets show RMS-<NNN>`, `rms tickets close RMS-<NNN> -m "<what was done>"`.
- Closing happens only in RMS-manager: the hooks deny it in a project, and the command refuses there too.
- Plain files, not committed when filed; the session here commits them with its work. Another machine: its own clone's `tickets/`, carried by git.
- This repo's `CLAUDE.md`: every session here starts by listing the open tickets for the owner.

## 12. Command reference

`rms [--project DIR] <command> …`. "Session" = run by Claude (inside a Claude Code session); "owner" = the owner's terminal.

| command | who | what |
|---|---|---|
| `init [DIR] [--name]` | owner | create `rms.json` |
| `adopt [DIR] [--name] [--local] [--command]` | owner | `rms.json` + hooks + doctor |
| `install-hooks [DIR] [--local] [--print] [--command]` | owner | merge RMS's hooks into the project's settings |
| `install-mod [DIR] [--local]` | owner | turn the rms-thread mod on for this project (§14) |
| `doctor [DIR]` | owner | check config, folders, state writable and git-ignored, hooks present and pointing at an existing `rms`, mod; exit 1 on a blocker |
| `hook <event>` | Claude Code | the hooks' entry point (JSON on stdin) |
| `threads [--json]` | any | threads with status and claim |
| `sessions [--all]` | any | sessions (with ended ones) |
| `whoami [--json]` | session | this session: thread, checkout, areas; `--json` adds the thread's Claude usage (the mod's data) |
| `claim T<NN> [--take-over] [--allow-done]` | session | §4 |
| `release [T<NN>] [-y]` | session / owner | §4 |
| `status [T<NN>]` | any | the thread's State and How to resume |
| `thread [T<NN>] [--json]` | any | §9 |
| `create "<name>" [--from] [--status] [--goal]` | session / owner | §5.1 |
| `handover [-m "…"]` | session | §5.3 |
| `approve-close T<NN>` | **owner only** | §5.4 |
| `close --milestone\|--merge-into T<P>\|--no-merge -m "…"` | session | §5.4 |
| `reopen T<NN>` | owner | §5.5 |
| `archive [T<NN>] [--exp] [--build]` | owner | §5.5 |
| `restore [T<NN>] [--exp] [--build] [--file]` | owner | §5.5 |
| `build add\|use\|unuse\|ignore …` | session | §7 |
| `builds` | any | every recorded build |
| `store [T<NN>]` | any | a thread's builds made and used, its data, sizes |
| `env [T<NN>]` | any | shell exports for the thread's builds |
| `scan` | any | §7; finds become warnings |
| `gc [--weeks N]` | any | §7; removes nothing |
| `warnings [list\|dismiss\|resolve] [ID] [--all] [--thread] [--note]` | list: any; settle: **owner only** | §8 |
| `usage [T<NN>\|none] [--json]` | any | §10 |
| `ticket "<text>" [--kind] [-m]` | any, anywhere | §11 |
| `tickets [list\|show\|close] [ID] [-m] [--all] [--json]` | close: in RMS-manager only | §11 |
| `log [-n N]` | any | recent events (§16) |
| `tui` | owner | §13 |

## 13. Terminal UI

`rms tui` (in the project). Refreshes every second; token counts every 10 s; sizes in the background.

- **Top line:** threads, claims, live sessions, Claude tokens (input + output), open RMS tickets, stale claims, open warnings.
- **Overview tab:** Threads (ID, name, status, claim, holder, since, tokens) · Sessions (state, thread, folder, started, last seen) · Warnings · Events (live log).
- **Thread tab:** pick a thread (or `t` on one in the Overview): head, Sources, Builds, Experiments, Disk use, Claude tokens by model (share, in + out, input, output, cache write, cache read, messages, total row with cost), Claude sessions on the thread (unregistered and guessed marked), Running now (every second).

| key | does |
|---|---|
| `t` | open the selected thread in the Thread tab |
| `enter` | details: a thread's summary, claim and store; a session's record |
| `x` | release the selected claim (asks; warns if the holder is live) |
| `c` | approve closing the selected thread (the owner's yes, once) |
| `d` / `v` | dismiss / resolve the selected warning (Warnings pane) |
| `n` | file a ticket for RMS (kind + text; the selected thread attached) |
| `e` | show or hide ended sessions |
| `r` | refresh, re-measure the thread |
| `q` | quit |

## 14. The Claude Code mod `rms-thread`

Source `mods/rms-thread/` (tests: `claude plugin test mods/rms-thread`); this repo is its marketplace (`.claude-plugin/marketplace.json`).

- **Picker:** a session that starts in an RMS project with no thread (and not a side session) is asked "Which thread will this session work on?" (open threads, free first, up to three, plus "No thread now"; any id under Other). The pick runs `rms claim` for this session; a stale or done thread asks again before `--take-over`/`--allow-done`; a refused claim asks again (three tries). RMS's UserPromptSubmit hook then tells Claude.
- **Band** above the prompt: `RMS T01 fast sort · store/worktrees/T01/ · 11.7k tok (opus 5.5 59%, haiku 5.5 41%)`, plus "handover due". No thread: `RMS · no thread` with a **Pick a thread** button. Side session: `RMS · side session side/<name>/ (no thread)`. Outside RMS: nothing. Refreshed after every turn.
- **Commands:** `/thread` (pick, or show the held thread) · `/ticket [bug|idea|question] <text>` (§11).
- **Finding `rms`:** `$RMS_BIN`, else this machine's install (`~/working_dir/LLVM-regsw/side/RMS-manager/.venv/bin/rms`), else `rms` on `PATH`.
- **Turning it on per project:** `rms install-mod <project>` runs `claude plugin marketplace add <RMS-manager> --scope project` and `claude plugin install rms-thread@rms-manager --scope project`; only `claude` started in that project loads it, from `mods/rms-thread/` directly. Everywhere: `/plugin install rms-thread --marketplace HeshanDissanayake/RMS-manager`.

## 15. Files and formats

**State, `<project>/store/rms/`** (per machine, git-ignored; one `flock` lock `.lock`; atomic JSON writes; readable and fixable by hand):

| file | content |
|---|---|
| `sessions/<session id>.json` | id, short, started, thread, cwd, side, pid, pid_start, source, state, last_seen, ended, transcript |
| `claims/T<NN>.json` | thread, session, short, since, cwd, worktree, areas, claim_note_hash, handover_due, note_hash |
| `approvals/T<NN>.json` | the owner's close approval: thread, by, at (used once) |
| `events.jsonl` | the event log (§16): one JSON object per line, `t` + `event` + fields |
| `commands.jsonl` | every Bash command a session ran: time, session, thread, folder, command |
| `warnings.json` | warnings: id, kind, subject (the path), thread, text, first and last seen, status (open, resolved, dismissed), who settled it and the note |
| `usage/<session id>.json` | §10, plus the transcript stamps it was read from |

**Records, in the project's git:**

| file | content |
|---|---|
| `threads/INDEX.md` | thread tables (ID, Thread, Status, Since, …) |
| `threads/T<NN>-*/THREAD.md` | Status line; sections `## Goal`, `## State as of <date>`, `## Where things are`, `## How to resume`, `## Open items`, `## Spawned threads` |
| `threads/T<NN>-*/decisions.md`, `hypotheses.md`, `experiments/EXP-*/` (README.md with `# EXP-…: title` and `**Status:**`, `summary.csv`) | the thread's own records |
| `threads/T<NN>-*/store.json` | builds, uses, ignore, milestone_uses (§7) |
| `threads/T<NN>-*/claude.json` | Claude usage of the thread: total, cost, models, sessions (§10) |
| `threads/T00-main/milestones.md` | `M<N>` rows |
| `<archive_dir>/catalog.csv` | kind, name, thread, file, sha256, bytes, date, source |

**This repo:** `tickets/RMS-<NNN>.md` — `# RMS-<NNN>: <title>`, a field list (`- status:`, `- kind:`, `- filed:`, `- by:`, `- folder:`, `- project:`, `- thread:`, `- session:`, `- rms commit:`), `## Text`, `## Context (recent RMS events)`, and `## Closed` when closed.

## 16. Events

`events.jsonl`, shown by `rms log` and the TUI.

| event | when |
|---|---|
| `session-start`, `session-<source>` (`resume`, `clear`, `compact`, …), `session-end` | a session registers (first time / again) or ends |
| `claim`, `take-over`, `release`, `claim-refused` | claims |
| `denied`, `hook-error` | a hook blocked a call / failed internally |
| `worktree`, `source-branch` | a checkout was made / a source repo got the thread's branch |
| `create`, `handover-due`, `handover-blocked`, `handover`, `close-approved`, `close-refused`, `close`, `reopen` | lifecycle |
| `archive`, `restore` | archives |
| `build-add`, `build-use`, `build-unuse`, `build-ignore` | builds |
| `warning`, `warning-resolved`, `warning-dismissed` | warnings |
| `ticket` | a ticket was sent from this project |

## 17. Principles

1. Files and git are the source of truth; anything else is a rebuildable cache.
2. Write by hand only what git cannot know; derive the rest (commit trailers).
3. The core works without a server.
4. Lifecycle actions are deterministic commands; sessions only write the judgement text (the handover note).
5. Never delete without the owner's confirmation.
6. Generic core; a project is described by `rms.json`. No project specifics in the core.
7. Never block work over a mismatch: warn, and let the owner settle it.
8. A hook never breaks Claude Code.

## 18. Known limits and open tickets

- Bash commands of a session holding a thread are not checked (RMS-001).
- Background scripts are shown only while they run; no history (RMS-002).
- A session ending without a handover releases its claim silently, leaving uncommitted work (RMS-003).
- Hooks are read only when `claude` starts in the project root.
- Running processes are seen only on this machine; a cloud session's leftovers are found later by `rms scan`.
- Cost per thread is an estimate.
- Not built yet: consoles and job logs, the API server and RMS-web, migration of LLVM-regsw (`builds.md`, `catalog.csv`).

Open tickets: `rms tickets`.

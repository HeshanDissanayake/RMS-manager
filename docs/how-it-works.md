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
| `UserPromptSubmit` | the owner sends a prompt | `additionalContext` only when the session's thread changed since Claude was last told (e.g. picked in the rms-thread mod's picker, or released by the owner) |
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

## Thread lifecycle (git)

The model is LLVM-regsw's plan (`threads/T00-main/notes/PLAN-sustainable-workflow.html`, section 5), chosen by the owner on 2026-11-11:

```
main ── T00, INDEX, every thread's records (merged at handover); source pointers move only at a milestone
 ├─ thread/T01 ── checkout store/worktrees/T01/  (sources: store/worktrees/T01/src/<repo>; branch thread/T01 only in repos it changes)
 └─ thread/T04 ── checkout store/worktrees/T04/
```

| command | who | what RMS does |
|---|---|---|
| `rms create "<name>" [--from T<NN>\|M<N>\|main]` | a session or the owner | new id; branch `thread/T<NN>` from there; checkout; folder from a template (THREAD.md, decisions, hypotheses, experiments/); commit on the branch; INDEX row on main (`Spawns:`) |
| `rms claim T<NN>` | a session | the claim, plus the thread's checkout (created if missing). Source repos are checked out at the thread's recorded commit **without a branch**. A repo gets the branch `thread/T<NN>` only when the thread changes it: at the first Edit/Write there (hook), or at handover if it has commits or changes. The session may write only in its thread folder and its sources. |
| `rms handover` (1st) | the holder | marks the handover due and prints what to write: State as of today, How to resume, Where things are, Open items; spawned ideas; commit the sources |
| `rms handover -m "…"` (2nd) | the holder | checks: THREAD.md changed since step 1, State dated today, How to resume not empty, sources committed on `thread/T<NN>`, nothing changed outside the thread folder. Then: commit `T<NN> handover <date>: …` on the branch; merge into main (main's source pointers kept); INDEX status `paused`; claim released. |
| `rms approve-close T<NN>` | **the owner only** (own terminal, or `c` in the TUI; the hook blocks it for sessions) | the owner's yes to closing, used once |
| `rms close --milestone -m "…"` | the holder, after the owner's yes | final State checked; merge into main **with** the source pointers; `M<N>` in the milestones file; tag `milestone-<N>` in the root and every source repo; INDEX row → Done |
| `rms close --merge-into T<P> -m "…"` | the holder | merges the sources and the branch into the parent's (merge point `M-T<P>-<n>`); records into main; INDEX → Done |
| `rms close --no-merge -m "…"` | the holder | records into main; INDEX → Done, merged into "nothing" |
| `rms reopen T<NN>` | the owner | INDEX row back to Open, `paused` |
| `rms archive T<NN>` / `rms restore T<NN>` | the owner | done threads only: `<data_dir>/T<NN>/` → `<archive_dir>/T<NN>-<date>.tar.gz`, sha256 in `catalog.csv`; restore checks the sha256. Nothing is deleted: the owner removes the working copy. |

- **Tags:** only `milestone-<N>`, in the root and every source repo, at a milestone. No handover tags: each handover commit already records the exact source commits (owner, 2026-11-11, as LLVM-regsw since 2026-11-09).
- The INDEX changes only at handover and close (and create and reopen). Whether a thread is in use lives in RMS's state (claims), not in the INDEX.
- Submodule pointer conflicts are expected: handover keeps main's pointer, a milestone takes the thread's, and a merge point takes the parent's merged source commit. Any other merge conflict aborts and changes nothing.
- RMS never moves the root's own source checkouts (`update = none`). After a milestone the root shows `src/<repo>` as "new commits", which is expected.
- `rms` run inside a thread's checkout works on the real root, because a checkout is recognised by its `.git` file.

## Builds and data (what each thread built, uses and stored)

RMS records and reconciles. It never builds and never runs a recipe.

- **Record, one file per thread:** `threads/T<NN>-<name>/store.json` has `builds` (made by this thread: path, source repo + commit, recipe, cost, env name, milestones), `uses` (other threads' builds by name) and `ignore` (found folders that are not builds). RMS writes it in the thread's checkout; it goes to main with the handover.
- **Data needs no record:** everything under `store/data/T<NN>/` belongs to T<NN>.
- **Builds live in** `store/builds/<tool>/<name>/` (a thread's own: `<tool>/T<NN>-<name>`).

| command | what |
|---|---|
| `rms build add <path> [--recipe …] [--cost cheap\|expensive] [--env VAR]` | the holder records a build. RMS fills in the source repo and its commit from the thread's checkout. |
| `rms build use <name>` / `unuse` / `ignore <path>` | the builds this thread uses; a found folder that is not a build |
| `rms store [T<NN>]` | builds made, builds used, data, with sizes; also shown at `rms claim` and in the TUI details |
| `rms env [T<NN>]` | `export VAR=path` for the thread's builds |
| `rms scan` | records vs disk: unrecorded build folders (with guesses), recorded builds missing on disk, data of unknown threads |
| `rms gc [--weeks N]` | what could go: builds no open thread or milestone needs, data of done threads, unchanged for N weeks (default 4). Cheap: the owner may remove it. Expensive or data: archive first. Removes nothing. |
| `rms archive T<NN> [--exp EXP]` · `rms archive --build NAME` · `rms restore …` | tar.gz + sha256 in `store/archive/catalog.csv`; restore checks the sha256 |

**Sessions that did not tell RMS.** `rms scan` guesses an unrecorded folder's thread, in this order:
1. its name (`T<NN>-…`)
2. its build files (`CMakeCache.txt`, `config.log`) pointing into a thread's checkout
3. a logged command that names the path (the PreToolUse hook logs every Bash command to `store/rms/commands.jsonl`)
4. the single thread that was claimed when its files were written

The source comes from the build files and the recipe from the logged commands.

At a milestone, the thread's builds and the builds it uses are pinned (`milestones`, `milestone_uses`) and never become clean-up candidates.

## Warnings (for the owner)

RMS never stops work over a mismatch. It notes it as a **warning** in `store/rms/warnings.json`, at handover and close (that thread's finds) and at `rms scan` (everything).

| kind | when |
|---|---|
| `unrecorded-build` | a build folder no thread recorded (with the guessed thread and source) |
| `missing-build` | a recorded build not on disk and not archived |
| `unknown-data` | a data folder of no known thread |

- `rms warnings` lists the open ones; the TUI has a Warnings pane.
- **Resolved:** RMS marks a warning resolved itself once a later check no longer finds its cause (the build was recorded, ignored or removed). The owner can also mark it with `rms warnings resolve <id>` or `v` in the TUI.
- **Dismissed:** the owner accepts it as it is, with `rms warnings dismiss <id> [--note …]` or `d` in the TUI. It stays dismissed.
- Only the owner settles warnings. The hook blocks `rms warnings dismiss|resolve` for sessions.

## Rules the hooks apply

- **Before a thread is held:** Edit and Write are denied. Bash runs only `rms …` and read-only commands (`ls`, `cat`, `grep`, `git log`/`status`/`diff`, …), optionally joined by `|`, `&&` or `||` and with `2>&1`. Anything else is denied with the reason.
- **With a thread held:** Edit and Write are allowed only inside the thread's folder (plus `shared_writable` and `extra_writable` from `rms.json`). Bash is not checked (a known limit).
- **Side sessions** started in their own `side/<name>/` folder are **not managed by RMS** (owner, 2026-11-11). The hooks aren't read there, and that is intended: such a session (RMS-manager's own, for example) follows the project's side-session rules instead. If a session started in the root works in `side/<name>/`, RMS limits its writes to that folder and it needs no claim.
- **One live session per thread:** a claim is refused while the holder's process runs. "Live" means the PID is alive and has the same start time, so a reused PID doesn't count. If the holder is gone, the claim is *stale*: `--take-over` only after the owner says yes.
- **SessionEnd** releases the session's claim.
- **A hook never breaks Claude Code:** on an internal error it logs `hook-error` and lets the call through.

## What the owner sees and what Claude sees

- **On session start, in the terminal** (`systemMessage`): `RMS · <project> · session S-XXXX · no thread yet` and the thread list.
- **In Claude's context** (`additionalContext`): the session ID, the thread list, and the instruction. If the first message names a thread, claim it. Otherwise ask "Which thread?" (AskUserQuestion menu), then run `rms claim`.
- **No `CLAUDE.md` change is needed.** The text comes from RMS each time, so it is always current and the same in every project.

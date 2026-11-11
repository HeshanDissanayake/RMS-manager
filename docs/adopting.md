# Putting RMS on a project

How to make any project an RMS project. Why each step exists, and everything else RMS does: [SPEC.md](SPEC.md).

## What the project needs

```
<project>/
├── rms.json                 created by `rms adopt`
├── threads/                 one folder per thread: T00-main/, T01-<name>/, …
│   ├── INDEX.md             tables with columns ID | Thread | Status | Since (names, status)
│   └── T01-<name>/THREAD.md sections "## State …" and "## How to resume" (shown at claim)
├── side/                    optional: side/<name>/ for side sessions
└── store/                   RMS state goes to store/rms/: must be ignored by git
```

The folder names are defaults; `rms.json` can point elsewhere (`threads_dir`, `index_file`, `side_dir`, `state_dir`).

## Steps

1. **Install RMS once per machine** (it lives in this repo, nothing system-wide):
   ```bash
   cd <RMS-manager>
   python3 -m venv .venv && .venv/bin/pip install -e .
   ```
   Below, `rms` means `<RMS-manager>/.venv/bin/rms`.

2. **Adopt the project:** create `rms.json`, merge the hooks into the project's folder, and check:
   ```bash
   rms adopt <project>             # hooks in <project>/.claude/settings.json (in git, shared)
   # or: rms adopt <project> --local   # hooks in <project>/.claude/settings.local.json (personal)
   ```
   Hooks go only in the project's own folder, never in `~/.claude/settings.json` (owner's decision 2026-11-11). They are **merged**: other settings and hooks in the file stay, and running it again replaces only RMS's own entries. To see the block without writing it: `rms install-hooks --print`.
   With `settings.json` in git, the hook command holds the path to `rms` on this machine. On another machine, run `rms install-hooks <project>` again.

3. **Tell sessions to start in the root.** Claude Code reads the hooks only when `claude` starts in `<project>/`. `CLAUDE.md` is read from subfolders too, so add to `<project>/CLAUDE.md`:
   ```markdown
   ## RMS
   This project is managed by RMS. Every session must start in the project root. If you got no
   "RMS" note at the start of this session, you were started elsewhere: do nothing, and ask the
   owner to restart `claude` in the project root. Exception: a side session started in its own
   `side/<name>/` folder is not managed by RMS; it follows the project's side-session rules.
   ```

4. **The Claude Code mod, for this project only:** `rms install-mod <project>` (or `--local`). It runs Claude Code's own `claude plugin marketplace add <RMS-manager> --scope project` and `claude plugin install rms-thread@rms-manager --scope project` in the project folder. The project's `.claude/settings.json` then names the mod (`enabledPlugins`) and where it comes from (`extraKnownMarketplaces`, this repo's folder). `claude` started in the project loads it; other folders do not, and no `--plugin-dir` is needed. It loads straight from `mods/rms-thread/`, so edits there apply to the next session.

5. **Ignore the state folder in git:** add `store/` (or `store/rms/`) to the project's `.gitignore`.

6. **Check:**
   ```bash
   rms doctor <project>
   ```
   It checks `rms.json`, the threads folder and index, that `store/rms/` is writable and git-ignored, and that all three hooks are present and point to an existing `rms`. It exits 1 if anything would stop RMS from working.

7. **Try it:** start `claude` in the project root and type `hi`. You should see the RMS line with the thread list, and Claude should ask which thread. `rms tui` (run from the project) shows the session appear.

## Moving to a new machine (or a fresh Claude Code)

Everything RMS needs is in two places: this repo (the program, the mod) and each project's own files (`rms.json`, `.claude/settings.json`, `threads/`). RMS's state (`store/rms/`) is per machine and starts empty; claims and sessions are not moved.

1. **RMS:** clone this repo and install it (step 1 above):
   ```bash
   git clone https://github.com/HeshanDissanayake/RMS-manager.git
   cd RMS-manager && python3 -m venv .venv && .venv/bin/pip install -e .
   ```
2. **The project's hooks:** they hold the path to `rms` on the old machine. Run `rms install-hooks <project>` again (it replaces only RMS's own entries), then `rms doctor <project>`.
3. **The mod `rms-thread`** (thread picker at session start, thread band above the prompt). Per project, from the local clone: `rms install-mod <project>` (step 4 above). Or for every folder, from GitHub, at the prompt of a terminal Claude Code session:
   ```
   /plugin install rms-thread --marketplace HeshanDissanayake/RMS-manager
   ```
   or, from a shell, `claude plugin install rms-thread --marketplace HeshanDissanayake/RMS-manager`. Answer `y` to add the marketplace, then choose the user scope. From a local clone instead of GitHub: `/plugin marketplace add <path to RMS-manager>`, then `/plugin install rms-thread@rms-manager`. A private repo installs only where git already has a credential for github.com.
4. **Where the mod finds `rms`:** `$RMS_BIN` if set, else `~/working_dir/LLVM-regsw/side/RMS-manager/.venv/bin/rms` (this machine's install), else `rms` on `PATH`. On a machine where the clone is elsewhere, set `RMS_BIN` (e.g. in `~/.claude/settings.json` under `"env"`), or put `.venv/bin` on `PATH`.
5. **Check:** start `claude` in the project root. You should see the RMS line, the thread picker, and after a pick the band `RMS T<NN> <name>` above the prompt.

## Checklist when something does not work

| symptom | cause / fix |
|---|---|
| no RMS line at session start, nothing in `rms log` | `claude` was started in a subfolder: restart it in the project root; otherwise `rms doctor` |
| hook errors in Claude Code | the `rms` path in the hook command is wrong (RMS moved) → `rms install-hooks … ` again |
| `rms: command not found` inside Claude | `CLAUDE_ENV_FILE` not set by this Claude Code version → Claude uses the absolute path given in its context |
| claim refused, "held by S-XXXX (live)" | another session works on that thread (by design); `rms sessions` shows it |
| claim refused, "stale" | holder is gone; with the owner's yes: `rms claim T<NN> --take-over`, or the owner: `rms release T<NN>` / TUI `x` |
| no picker or band, but the RMS line shows | the mod is not installed (`/plugin` lists it), or it cannot find `rms`: set `RMS_BIN` |
| `hook-error` in `rms log` | a bug in RMS; the call was let through. Report the error line. |

## Removing RMS from a project

Delete the RMS entries from `<project>/.claude/settings*.json` (the hook entries whose command contains `rms hook`), the RMS section of `CLAUDE.md`, `rms.json` and `store/rms/`.

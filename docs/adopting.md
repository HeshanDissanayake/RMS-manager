# Putting RMS on a project

How to make any project an RMS project. Why each step exists: [how-it-works.md](how-it-works.md).

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

2. **Adopt the project:** create `rms.json`, merge the hooks, and check:
   ```bash
   rms adopt <project> --user          # hooks in ~/.claude/settings.json (recommended)
   # or: rms adopt <project>           # hooks in <project>/.claude/settings.json (in git)
   # or: rms adopt <project> --local   # hooks in <project>/.claude/settings.local.json
   ```
   The hooks are **merged**: other settings and hooks in the file stay, and running it again replaces only RMS's own entries. To see the block without writing it: `rms install-hooks --print`.
   With `--user`, this only needs doing once per machine. For a second project, `rms init <project>` (just `rms.json`) is enough.

3. **Ignore the state folder in git:** add `store/` (or `store/rms/`) to the project's `.gitignore`.

4. **Check:**
   ```bash
   rms doctor <project>
   ```
   It checks `rms.json`, the threads folder and index, that `store/rms/` is writable and git-ignored, and that all three hooks are present and point to an existing `rms`. It warns if the hooks are only at project level. It exits 1 if anything would stop RMS from working.

5. **Try it:** start `claude` in the project and type `hi`. You should see the RMS line with the thread list, and Claude should ask which thread. `rms tui` (run from the project) shows the session appear.

## Checklist when something does not work

| symptom | cause / fix |
|---|---|
| no RMS line at session start, nothing in `rms log` | hooks not read: `rms doctor`; project-level hooks + `claude` started in a subfolder → `--user` |
| hook errors in Claude Code | the `rms` path in the hook command is wrong (RMS moved) → `rms install-hooks … ` again |
| `rms: command not found` inside Claude | `CLAUDE_ENV_FILE` not set by this Claude Code version → Claude uses the absolute path given in its context |
| claim refused, "held by S-XXXX (live)" | another session works on that thread (by design); `rms sessions` shows it |
| claim refused, "stale" | holder is gone; with the owner's yes: `rms claim T<NN> --take-over`, or the owner: `rms release T<NN>` / TUI `x` |
| `hook-error` in `rms log` | a bug in RMS; the call was let through. Report the error line. |

## Removing RMS from a project

Delete the RMS entries from the settings file (the hook entries whose command contains `rms hook`), then delete `rms.json` and `store/rms/`. With `--user` hooks, other RMS projects still use them; remove them from `~/.claude/settings.json` only when no project needs RMS.

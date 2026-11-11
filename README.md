# RMS-manager

The core of RMS: one program that runs a long, multi-session research project. It keeps the registry and history of everything (threads, builds, data, experiments, milestones), runs the thread lifecycle as fixed commands, holds the locks, enforces the mechanical rules, tracks running jobs per thread, and serves all of it to frontends ([RMS-web](../RMS-web/)).

**Status:** initialised 2026-11-11; nothing built yet. Owner's private work (side project of `~/working_dir/LLVM-regsw`, not part of its records). First user: LLVM-regsw.

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

## Layout (planned)

```
RMS-manager/
├── README.md      this file
├── NOTES.md       design notes, decisions, open questions, stages
└── (code to come: core library, CLI, checks, hooks, server)
```

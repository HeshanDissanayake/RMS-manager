# CLAUDE.md — RMS-manager

## Session start

This folder is a **side session** of `~/working_dir/LLVM-regsw`. Any session started here is "Something else (its own folder)". **Skip the session menu** in `../../CLAUDE.md` (no "thread or something else?" questions). Its "Side sessions" rules still apply:

- Write only inside this folder (`side/RMS-manager/`). The rest of LLVM-regsw (`threads/`, `shared/`, `RULES.md`, `src/`, `store/`, `paths.env`, `/research/heshds/chipyard`) is **read-only** reference.
- No commits, branches or tags in LLVM-regsw's repos; no thread rows, thread notes or project memory entries.
- Temporary files go in this folder, not `/tmp`.

This folder is its **own git repo** (remote `github`, the owner pushes). Commit finished work here; never push.

## What this is

RMS (Research Manager) is one program that runs a long, multi-session research project. It keeps the registry and history, runs the thread lifecycle as fixed commands, holds locks, enforces the mechanical rules, tracks jobs/consoles, and serves an API to frontends ([RMS-web](../RMS-web/)). The first user is LLVM-regsw. **Built:** sessions, thread claims, hooks, CLI, terminal UI, thread lifecycle (create, handover, close, archive; see README). It runs on the sandbox (`sandbox/reset-demo.sh` → `sandbox/demo/root`), not yet on LLVM-regsw.

Read first:
- [`NOTES.md`](NOTES.md): the agreed design, stages and open questions. It is the source of truth for decisions. "Agreed" = owner said yes; "proposed" = not decided.
- [`README.md`](README.md): overview, commands, layout.
- [`docs/how-it-works.md`](docs/how-it-works.md) and [`docs/adopting.md`](docs/adopting.md): how the hooks and state work, and how to put RMS on a project. **Keep them current**: any change to hooks, config, state files or setup steps updates these docs in the same commit.
- Background: [`../2026-11-09-rms-system/SUMMARY.md`](../2026-11-09-rms-system/SUMMARY.md) (the hand-run system and its weak points); LLVM-regsw `RULES.md` (R1–R50) is the rule set being mechanised.

## Design principles (from NOTES.md §2)

1. Files + git are the source of truth; any database is a rebuildable cache.
2. Write by hand only what git cannot know; derive the rest (commit trailers `Thread:`, `Spawns:`, `Merges:`, `Milestone:`, `Rule:`).
3. The core (CLI + checks library + plain lock files + hooks) works without the server.
4. Lifecycle actions are deterministic commands; sessions only write the judgement text.
5. Never delete without the owner's confirmation.
6. Generic core; a project (LLVM-regsw) is described by one config file. No FPGA/Spike/LLVM specifics in the core.

## Conventions

- Python 3.13; config files are JSON (decided 2026-11-11). Standard library first; ask before adding dependencies.
- Commit when work is done (remote `github` exists); never push, the owner pushes.
- Work step by step with the owner; a terminal UI before the web UI.
- Nothing installed system-wide (LLVM-regsw R33).
- Build in the order of NOTES.md §9 stages; stage 1 first (checks library, git hooks, `check`, thread locks, consoles + PID hook).
- When a decision is made with the owner, record it in `NOTES.md` (agreed vs proposed, with the date).

## Working on the code

- Install once: `python3 -m venv .venv && .venv/bin/pip install -e .`; run `.venv/bin/rms ...`.
- Tests: `.venv/bin/python -m unittest discover -s tests` (temp files go in `.tmp/`). Run them before every commit.
- Try changes on the sandbox (`sandbox/reset-demo.sh` rebuilds `sandbox/demo/root` from the template `sandbox/demo-project/`); never install hooks or `rms.json` into LLVM-regsw without the owner.

"""The ``rms`` command."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rms import __version__, claims, hooks, inventory, lifecycle, sessions, setup, store, threads
from rms.git import GitError
from rms.config import CONFIG_NAME, Project, find_project, init_project, load


def _project(args) -> Project:
    p = load(Path(args.project)) if args.project else find_project()
    if p is None:
        sys.exit(f"rms: no {CONFIG_NAME} here or in any parent folder "
                 f"(create one with `rms init`, or pass --project)")
    return p


def _session(project: Project) -> dict | None:
    """This command's Claude Code session (registered on demand), or None."""
    sid = sessions.current_id()
    return sessions.ensure(project, sid) if sid else None


def _fail(msg: str) -> int:
    print(f"rms: {msg}", file=sys.stderr)
    return 1


# --- commands ---------------------------------------------------------------

def cmd_init(args) -> int:
    try:
        path = init_project(Path(args.dir), args.name)
    except FileExistsError as e:
        return _fail(str(e))
    print(f"created {path}")
    return 0


def cmd_threads(args) -> int:
    p = _project(args)
    print("\n".join(hooks.thread_lines(p)))
    return 0


def cmd_sessions(args) -> int:
    p = _project(args)
    rows = sessions.all_sessions(p)
    if not args.all:
        rows = [r for r in rows if sessions.status(r) != "ended"]
    if not rows:
        print("(no sessions)")
    for r in rows:
        where = p.rel(r["cwd"])
        print(f"  {r['short']}  {sessions.status(r):5}  thread={r.get('thread') or '-':4}  "
              f"side={r.get('side') or '-'}  started={r['started'][11:19]}  "
              f"last={r.get('last_seen', '')[11:19]}  pid={r.get('pid')}  {where}")
    return 0


def cmd_whoami(args) -> int:
    p = _project(args)
    rec = _session(p)
    if rec is None:
        print(f"Not inside a Claude Code session (owner's terminal). Project: {p.name} ({p.root})")
        return 0
    print(f"Session {rec['short']} ({rec['id']})")
    print(f"  project: {p.name} ({p.root})")
    print(f"  cwd:     {p.rel(rec['cwd'])}")
    print(f"  side:    {rec.get('side') or '-'}")
    print(f"  thread:  {rec.get('thread') or '- (none: run `rms claim <ID>`)'}")
    c = claims.get(p, rec["thread"]) if rec.get("thread") else None
    if c and c.get("worktree"):
        print(f"  checkout: {p.rel(c['worktree'])}/ (branch {p.thread_branch(rec['thread'])})")
    if c and c.get("handover_due"):
        print("  handover: due (write the note, then `rms handover` again)")
    areas = claims.writable_areas(p, rec)
    print(f"  may write: {', '.join(p.rel(a) + '/' for a in areas) or 'nothing'}")
    return 0


def cmd_claim(args) -> int:
    p = _project(args)
    rec = _session(p)
    if rec is None:
        return _fail("claim runs inside a Claude Code session (it claims for that session)")
    try:
        c = claims.claim(p, rec["id"], args.thread, take_over=args.take_over,
                         allow_done=args.allow_done)
    except claims.ClaimError as e:
        store.append_event(p.state_dir, "claim-refused", session=rec["short"],
                           thread=args.thread.upper(), reason=str(e))
        return _fail(f"claim refused: {e}")
    t = threads.discover(p)[c["thread"]]
    if lifecycle.uses_git(p):
        try:
            info = lifecycle.open_for_claim(p, t.id)
        except (lifecycle.LifecycleError, GitError) as e:
            claims.release(p, t.id, session_id=rec["id"], by=rec["short"], reason="no checkout")
            return _fail(f"claim undone: cannot open {t.id}'s checkout: {e}")
        print(f"Session {rec['short']} now holds {t.id}.")
        print(f"Work in {p.rel(info['worktree'])}/ (branch {p.thread_branch(t.id)}); "
              f"write only in: {', '.join(p.rel(a) + '/' for a in info['areas'])}.")
        print("When you stop: `rms handover` (it tells you what to write).")
    else:
        print(f"Session {rec['short']} now holds {t.id}. It may write in "
              f"{p.rel(t.folder) + '/' if t.folder else '(no folder)'}.")
    print()
    print(threads.summary(t, lifecycle.thread_file(p, t.id) if lifecycle.uses_git(p) else None))
    print()
    print(inventory.view_text(p, t.id))
    return 0


def cmd_release(args) -> int:
    p = _project(args)
    rec = _session(p)
    try:
        if rec is not None:
            tid = args.thread or rec.get("thread")
            if not tid:
                return _fail("this session holds no thread")
            claims.release(p, tid, session_id=rec["id"], by=rec["short"])
            print(f"released {tid.upper()}")
            return 0
        if not args.thread:
            return _fail("from the owner's terminal, name the thread: rms release T<NN>")
        c = claims.get(p, args.thread.upper())
        if c is None:
            return _fail(f"{args.thread.upper()} is not claimed")
        if not args.yes:
            ans = input(f"Release {c['thread']} ({claims.describe(p, c)})? [y/N] ")
            if ans.strip().lower() not in ("y", "yes"):
                print("nothing changed")
                return 1
        claims.release(p, c["thread"], by="owner")
        print(f"released {c['thread']}")
        return 0
    except claims.ClaimError as e:
        return _fail(str(e))


def cmd_status(args) -> int:
    p = _project(args)
    rec = _session(p)
    tid = args.thread or (rec or {}).get("thread")
    if not tid:
        return _fail("name a thread (rms status T<NN>); this session holds none")
    t = threads.discover(p).get(tid.upper())
    if t is None:
        return _fail(f"no thread {tid}")
    f = lifecycle.thread_file(p, t.id) if lifecycle.uses_git(p) else None
    print(threads.summary(t, f))
    print()
    print(f"Claim: {claims.describe(p, claims.get(p, t.id))}")
    return 0


def _lc(fn):
    """Run a lifecycle command; turn its errors into a clean message."""
    try:
        return fn()
    except (lifecycle.LifecycleError, claims.ClaimError, GitError, ValueError) as e:
        return _fail(str(e))


def cmd_create(args) -> int:
    p = _project(args)
    rec = _session(p)
    def go():
        r = lifecycle.create(p, args.name, origin=args.origin, status=args.status,
                             goal=args.goal or "", by=rec["short"] if rec else "owner")
        print(f"created {r['thread']} ({r['folder']}) from {r['from']} at {r['at']}: "
              f"branch {r['branch']}, checkout {p.rel(r['worktree'])}/")
        print(f"status {args.status}; claim it with `rms claim {r['thread']}`")
        return 0
    return _lc(go)


def cmd_handover(args) -> int:
    p = _project(args)
    rec = _session(p)
    if rec is None:
        return _fail("handover runs inside the Claude Code session that holds the thread")
    def go():
        stage, text = lifecycle.handover(p, rec, args.message)
        print(text)
        return 0 if stage in ("due", "done") else 1
    return _lc(go)


def cmd_close(args) -> int:
    p = _project(args)
    rec = _session(p)
    if rec is None:
        return _fail("close runs inside the Claude Code session that holds the thread")
    how = "milestone" if args.milestone else "merge" if args.merge_into else "none"
    def go():
        merged = lifecycle.close(p, rec, how, args.message, parent=args.merge_into)
        print(f"{rec.get('thread') or ''} closed; merged into: {merged}. Thread released.")
        return 0
    return _lc(go)


def cmd_approve_close(args) -> int:
    p = _project(args)
    if sessions.current_id():
        return _fail("approve-close is the owner's: run it in your own terminal, not in Claude")
    def go():
        lifecycle.approve_close(p, args.thread)
        print(f"approved: the session holding {args.thread.upper()} may now close it (once)")
        return 0
    return _lc(go)


def cmd_reopen(args) -> int:
    p = _project(args)
    def go():
        lifecycle.reopen(p, args.thread)
        print(f"{args.thread.upper()} reopened (status paused)")
        return 0
    return _lc(go)


def cmd_archive(args) -> int:
    p = _project(args)
    if not args.thread and not args.build:
        return _fail("name a thread (rms archive T<NN> [--exp EXP]) or --build NAME")
    def go():
        r = lifecycle.archive(p, args.thread, exp=args.exp, build=args.build)
        print(f"archived {r['source']}/ -> {p.rel(p.archive_dir / r['file'])} "
              f"({inventory.human(int(r['bytes']))}, sha256 {r['sha256'][:12]}...). "
              f"The working copy is kept: the owner removes it.")
        return 0
    return _lc(go)


def cmd_restore(args) -> int:
    p = _project(args)
    if not args.thread and not args.build:
        return _fail("name a thread (rms restore T<NN> [--exp EXP]) or --build NAME")
    def go():
        r = lifecycle.restore(p, args.thread, exp=args.exp, build=args.build, file=args.file)
        print(f"restored {r['file']} (sha256 checked) -> {r['source']}/")
        return 0
    return _lc(go)


def _holder(p):
    rec = _session(p)
    if rec is None or not rec.get("thread"):
        raise inventory.InventoryError("run this from the Claude session that holds the thread")
    return rec


def _inv(fn):
    try:
        return fn()
    except (inventory.InventoryError, lifecycle.LifecycleError, GitError) as e:
        return _fail(str(e))


def cmd_build(args) -> int:
    p = _project(args)
    def go():
        rec = _holder(p)
        if args.action == "add":
            b = inventory.add_build(p, rec, args.path, name=args.name, tool=args.tool,
                                    source=args.source, recipe=args.recipe or "",
                                    cost=args.cost, env=args.env, shared=args.shared,
                                    note=args.note or "")
            src = b["source"] or {}
            print(f"recorded {b['name']} ({b['path']}) for {rec['thread']}"
                  + (f", from {src['repo']}@{src['commit'][:9]}" + (" (uncommitted changes!)"
                     if src.get("dirty") else "") if src else ", source unknown"))
        elif args.action == "use":
            inventory.use_build(p, rec, args.path)
            print(f"{rec['thread']} uses {args.path}")
        elif args.action == "unuse":
            inventory.use_build(p, rec, args.path, drop=True)
            print(f"{rec['thread']} no longer uses {args.path}")
        elif args.action == "ignore":
            inventory.ignore_path(p, rec, args.path)
            print(f"{args.path}: marked as not a build of {rec['thread']}")
        return 0
    return _inv(go)


def cmd_builds(args) -> int:
    p = _project(args)
    rows = inventory.all_builds(p)
    if not rows:
        print("(no recorded builds)")
    for name, b in sorted(rows.items()):
        on = "" if (p.root / b["path"]).exists() else "  [not on disk]"
        ms = f"  pinned {','.join(b['milestones'])}" if b.get("milestones") else ""
        print(f"  {name:<28} {b['thread']}  {b['path']}  {b.get('cost', '?')}{ms}{on}")
    return 0


def cmd_store(args) -> int:
    p = _project(args)
    rec = _session(p)
    tid = (args.thread or (rec or {}).get("thread") or "").upper()
    if not tid:
        return _fail("name a thread (rms store T<NN>); this session holds none")
    print(inventory.view_text(p, tid))
    return 0


def cmd_scan(args) -> int:
    p = _project(args)
    r = inventory.scan(p)
    if not any(r.values()):
        print("records and disk agree")
        return 0
    if r["unrecorded"]:
        print("Builds on disk that no thread recorded:")
        for u in r["unrecorded"]:
            print(f"  {u['path']}  {inventory.human(u['bytes'] or 0)}  written {u['written'][:16]}")
            print(f"      thread: {u['thread'] or '?'} ({u['why']}); source: {u['source'] or '?'}"
                  + (f"; recipe guess: {u['recipe']}" if u["recipe"] else ""))
    if r["missing"]:
        print("Recorded builds not on disk (and not archived):")
        for b in r["missing"]:
            print(f"  {b['name']}  {b['path']}  ({b['thread']})")
    if r["unknown_data"]:
        print("Data folders of no known thread:")
        for d in r["unknown_data"]:
            print(f"  {d['path']}  {inventory.human(d['bytes'] or 0)}")
    return 1


def cmd_env(args) -> int:
    p = _project(args)
    rec = _session(p)
    tid = (args.thread or (rec or {}).get("thread") or "").upper()
    if not tid:
        return _fail("name a thread (rms env T<NN>)")
    print("\n".join(inventory.env_lines(p, tid)) or f"# {tid}: no builds with an env name")
    return 0


def cmd_gc(args) -> int:
    p = _project(args)
    rows = inventory.gc(p, weeks=args.weeks if args.weeks is not None else p.gc_weeks)
    if not rows:
        print("nothing to clean up")
        return 0
    total = 0
    for r in rows:
        total += r["bytes"]
        print(f"  {r['kind']:<5} {r['name']:<28} {inventory.human(r['bytes']):>9}  {r['path']}  -> {r['action']}")
    print(f"total {inventory.human(total)}. RMS removes nothing; archive with `rms archive`, "
          f"then the owner removes.")
    return 0


def cmd_log(args) -> int:
    p = _project(args)
    for e in store.read_events(p.state_dir, args.n):
        rest = " ".join(f"{k}={v}" for k, v in e.items() if k not in ("t", "event"))
        print(f"{e['t'][:19].replace('T', ' ')}  {e['event']:14} {rest}")
    return 0


def cmd_hook(args) -> int:
    return hooks.run(args.event)


def _scope(args) -> str:
    return "local" if args.local else "project"


def cmd_install_hooks(args) -> int:
    root = Path(args.dir).resolve()
    if not (root / CONFIG_NAME).is_file():
        return _fail(f"{root} has no {CONFIG_NAME} (run `rms adopt {args.dir}`)")
    rms_cmd = args.command or hooks.rms_command()
    if args.print:
        print(json.dumps(hooks.settings_for(rms_cmd), indent=2))
        return 0
    try:
        path = setup.install_hooks(root, rms_cmd, scope=_scope(args))
    except ValueError as e:
        return _fail(str(e))
    print(f"RMS hooks merged into {path} (other settings and hooks kept)")
    return 0


def _print_doctor(root: Path) -> int:
    marks = {"ok": "  ok  ", "note": "  note", "warn": "  warn", "fail": "  FAIL"}
    results = setup.doctor(root)
    for level, msg in results:
        print(f"{marks[level]}  {msg}")
    fails = sum(level == "fail" for level, _ in results)
    print("\nready" if not fails else f"\n{fails} problem(s): RMS will not work until fixed")
    return 1 if fails else 0


def cmd_doctor(args) -> int:
    return _print_doctor(Path(args.dir))


def cmd_adopt(args) -> int:
    root = Path(args.dir).resolve()
    if not root.is_dir():
        return _fail(f"{root} is not a folder")
    if (root / CONFIG_NAME).is_file():
        print(f"{CONFIG_NAME} exists, kept")
    else:
        print(f"created {init_project(root, args.name)}")
    rms_cmd = args.command or hooks.rms_command()
    try:
        path = setup.install_hooks(root, rms_cmd, scope=_scope(args))
    except ValueError as e:
        return _fail(str(e))
    print(f"RMS hooks merged into {path}")
    print()
    return _print_doctor(root)


def cmd_tui(args) -> int:
    from rms.tui import run
    return run(_project(args))


# --- parser -------------------------------------------------------------------

def _scope_args(s: argparse.ArgumentParser) -> None:
    s.add_argument("--local", action="store_true",
                   help="hooks in <dir>/.claude/settings.local.json (personal, not in git) "
                        "instead of <dir>/.claude/settings.json")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="rms", description="RMS (Research Manager)")
    ap.add_argument("--version", action="version", version=f"rms {__version__}")
    ap.add_argument("--project", help="project root (default: found from the current folder)")
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="command")

    s = sub.add_parser("init", help=f"create {CONFIG_NAME} for a project")
    s.add_argument("dir", nargs="?", default=".")
    s.add_argument("--name")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("threads", help="list threads and who holds them")
    s.set_defaults(fn=cmd_threads)

    s = sub.add_parser("sessions", help="list Claude Code sessions")
    s.add_argument("--all", action="store_true", help="include ended sessions")
    s.set_defaults(fn=cmd_sessions)

    s = sub.add_parser("whoami", help="this session: thread, side folder, what it may write")
    s.set_defaults(fn=cmd_whoami)

    s = sub.add_parser("claim", help="claim a thread for this Claude Code session")
    s.add_argument("thread")
    s.add_argument("--take-over", action="store_true",
                   help="take over a stale claim (only with the owner's yes)")
    s.add_argument("--allow-done", action="store_true",
                   help="claim a done thread (only with the owner's yes)")
    s.set_defaults(fn=cmd_claim)

    s = sub.add_parser("release", help="free a thread (this session's, or any as the owner)")
    s.add_argument("thread", nargs="?")
    s.add_argument("-y", "--yes", action="store_true", help="do not ask")
    s.set_defaults(fn=cmd_release)

    s = sub.add_parser("create", help="new thread: branch, checkout, folder, INDEX row")
    s.add_argument("name")
    s.add_argument("--from", dest="origin", help="T<NN> (spawn from a thread), M<N> or main (default)")
    s.add_argument("--status", default="parked", choices=["parked", "paused", "active"])
    s.add_argument("--goal")
    s.set_defaults(fn=cmd_create)

    s = sub.add_parser("handover", help="end this session's work on its thread (two steps)")
    s.add_argument("-m", "--message", help="one line: what this session did")
    s.set_defaults(fn=cmd_handover)

    s = sub.add_parser("close", help="close this session's thread and merge its result")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--milestone", action="store_true", help="merge into main as milestone M<N>")
    g.add_argument("--merge-into", metavar="T<NN>", help="merge into the parent thread (merge point)")
    g.add_argument("--no-merge", action="store_true", help="ended with nothing to merge")
    s.add_argument("-m", "--message", required=True, help="one line: the result")
    s.set_defaults(fn=cmd_close)

    s = sub.add_parser("approve-close", help="owner only: allow the session to close a thread")
    s.add_argument("thread")
    s.set_defaults(fn=cmd_approve_close)

    s = sub.add_parser("reopen", help="reopen a done thread (status paused)")
    s.add_argument("thread")
    s.set_defaults(fn=cmd_reopen)

    s = sub.add_parser("archive", help="pack a done thread's data, or a build (sha256)")
    s.add_argument("thread", nargs="?")
    s.add_argument("--exp", help="only this experiment's folder")
    s.add_argument("--build", help="a recorded build, by name")
    s.set_defaults(fn=cmd_archive)

    s = sub.add_parser("restore", help="unpack archived data or a build (sha256 checked)")
    s.add_argument("thread", nargs="?")
    s.add_argument("--exp")
    s.add_argument("--build")
    s.add_argument("--file", help="a specific archive file (default: the latest)")
    s.set_defaults(fn=cmd_restore)

    s = sub.add_parser("build", help="record builds for this session's thread")
    s.add_argument("action", choices=["add", "use", "unuse", "ignore"])
    s.add_argument("path", help="add/ignore: the build folder; use/unuse: the build's name")
    s.add_argument("--name", help="default: <tool>/<folder> under the builds folder")
    s.add_argument("--tool")
    s.add_argument("--source", help="source repo it was built from, e.g. src/fastlib (default: guessed)")
    s.add_argument("--recipe", help="the command(s) that built it")
    s.add_argument("--cost", choices=["cheap", "expensive"], default="cheap",
                   help="expensive = hours or sudo to rebuild (archived before removal)")
    s.add_argument("--env", help="variable name for `rms env`, e.g. LLVM_BUILD")
    s.add_argument("--shared", action="store_true", help="meant for other threads to use")
    s.add_argument("--note")
    s.set_defaults(fn=cmd_build)

    s = sub.add_parser("builds", help="every recorded build")
    s.set_defaults(fn=cmd_builds)

    s = sub.add_parser("store", help="a thread's builds, the builds it uses, its data")
    s.add_argument("thread", nargs="?")
    s.set_defaults(fn=cmd_store)

    s = sub.add_parser("scan", help="find builds/data on disk that the records miss")
    s.set_defaults(fn=cmd_scan)

    s = sub.add_parser("env", help="shell exports for a thread's builds")
    s.add_argument("thread", nargs="?")
    s.set_defaults(fn=cmd_env)

    s = sub.add_parser("gc", help="list what could be archived/removed (removes nothing)")
    s.add_argument("--weeks", type=int, help="keep what changed within this many weeks")
    s.set_defaults(fn=cmd_gc)

    s = sub.add_parser("status", help="a thread's State and How to resume")
    s.add_argument("thread", nargs="?")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("log", help="recent events")
    s.add_argument("-n", type=int, default=30)
    s.set_defaults(fn=cmd_log)

    s = sub.add_parser("hook", help="entry point for Claude Code hooks (reads JSON on stdin)")
    s.add_argument("event", choices=sorted(hooks.HANDLERS))
    s.set_defaults(fn=cmd_hook)

    s = sub.add_parser("adopt", help="put RMS on a project: rms.json + hooks + check")
    s.add_argument("dir", nargs="?", default=".")
    s.add_argument("--name")
    _scope_args(s)
    s.add_argument("--command", help="how the hooks call rms (default: this executable)")
    s.set_defaults(fn=cmd_adopt)

    s = sub.add_parser("install-hooks", help="merge RMS's hooks into <dir>/.claude/settings.json")
    s.add_argument("dir", nargs="?", default=".")
    s.add_argument("--print", action="store_true", help="only print the hooks block")
    _scope_args(s)
    s.add_argument("--command", help="how the hooks call rms (default: this executable)")
    s.set_defaults(fn=cmd_install_hooks)

    s = sub.add_parser("doctor", help="check a project's RMS setup (config, hooks, folders)")
    s.add_argument("dir", nargs="?", default=".")
    s.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("tui", help="the terminal UI")
    s.set_defaults(fn=cmd_tui)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)

"""The ``rms`` command."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rms import __version__, claims, hooks, sessions, store, threads
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
    print(f"Session {rec['short']} now holds {t.id}. It may write in {p.rel(t.folder) + '/' if t.folder else '(no folder)'}.")
    print()
    print(threads.summary(t))
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
    print(threads.summary(t))
    print()
    print(f"Claim: {claims.describe(p, claims.get(p, t.id))}")
    return 0


def cmd_log(args) -> int:
    p = _project(args)
    for e in store.read_events(p.state_dir, args.n):
        rest = " ".join(f"{k}={v}" for k, v in e.items() if k not in ("t", "event"))
        print(f"{e['t'][:19].replace('T', ' ')}  {e['event']:14} {rest}")
    return 0


def cmd_hook(args) -> int:
    return hooks.run(args.event)


def cmd_install_hooks(args) -> int:
    root = Path(args.dir).resolve()
    if not (root / CONFIG_NAME).is_file():
        return _fail(f"{root} has no {CONFIG_NAME} (run `rms init {args.dir}` first)")
    block = hooks.settings_for(args.command or hooks.rms_command())
    if args.print:
        print(json.dumps(block, indent=2))
        return 0
    path = root / ".claude" / "settings.json"
    cur = store.read_json(path, {}) if path.exists() else {}
    if cur.get("hooks") and not args.force:
        return _fail(f"{path} already has hooks; check them, then use --force to replace "
                     f"them (or --print and merge by hand)")
    cur["hooks"] = block["hooks"]
    store.write_json(path, cur)
    print(f"wrote hooks to {path}")
    return 0


def cmd_tui(args) -> int:
    from rms.tui import run
    return run(_project(args))


# --- parser -------------------------------------------------------------------

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

    s = sub.add_parser("status", help="a thread's State and How to resume")
    s.add_argument("thread", nargs="?")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("log", help="recent events")
    s.add_argument("-n", type=int, default=30)
    s.set_defaults(fn=cmd_log)

    s = sub.add_parser("hook", help="entry point for Claude Code hooks (reads JSON on stdin)")
    s.add_argument("event", choices=sorted(hooks.HANDLERS))
    s.set_defaults(fn=cmd_hook)

    s = sub.add_parser("install-hooks", help="write the hooks into <dir>/.claude/settings.json")
    s.add_argument("dir", nargs="?", default=".")
    s.add_argument("--print", action="store_true", help="only print the hooks block")
    s.add_argument("--force", action="store_true", help="replace existing hooks")
    s.add_argument("--command", help="how the hooks call rms (default: this executable)")
    s.set_defaults(fn=cmd_install_hooks)

    s = sub.add_parser("tui", help="the terminal UI")
    s.set_defaults(fn=cmd_tui)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)

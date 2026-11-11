"""Tests for sessions, claims and hooks. Run: .venv/bin/python -m unittest -v"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from rms import claims, hooks, sessions, store, threads
from rms.config import init_project, load

HERE = Path(__file__).resolve().parent.parent
TMP = HERE / ".tmp"
DEMO = HERE / "sandbox" / "demo-project"


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        TMP.mkdir(exist_ok=True)
        self.dir = Path(tempfile.mkdtemp(dir=TMP))
        root = self.dir / "proj"
        shutil.copytree(DEMO, root, ignore=shutil.ignore_patterns("store", ".claude"))
        self.root = root
        self.p = load(root)
        # a "live Claude process" and a "dead" one
        self.live = subprocess.Popen(["sleep", "300"])
        dead = subprocess.Popen(["true"])
        dead.wait()
        self.dead_pid = dead.pid
        os.environ.pop("RMS_PROJECT", None)

    def tearDown(self) -> None:
        self.live.kill()
        self.live.wait()
        shutil.rmtree(self.dir)

    def session(self, sid: str, live: bool = True, cwd: Path | None = None) -> dict:
        return sessions.register(self.p, sid, str(cwd or self.root),
                                 self.live.pid if live else self.dead_pid)

    def hook(self, event: str, **data) -> dict | None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            hooks.run(event, io.StringIO(json.dumps(data)))
        text = out.getvalue().strip()
        return json.loads(text) if text else None


class TestThreads(Fixture):
    def test_discover_reads_index(self):
        ts = threads.discover(self.p)
        self.assertEqual(list(ts), ["T00", "T01", "T02", "T03"])
        self.assertEqual(ts["T02"].status, "paused")
        self.assertTrue(ts["T03"].done)
        self.assertIn("first merge sort", threads.summary(ts["T01"]))


class TestClaims(Fixture):
    def test_one_live_session_per_thread(self):
        self.session("aaaa-1")
        self.session("bbbb-2")
        claims.claim(self.p, "aaaa-1", "T01")
        with self.assertRaisesRegex(claims.ClaimError, "held by S-AAAA"):
            claims.claim(self.p, "bbbb-2", "T01")
        claims.claim(self.p, "bbbb-2", "t02")  # other thread is fine, any case
        self.assertEqual(sessions.get(self.p, "bbbb-2")["thread"], "T02")

    def test_claim_again_is_noop_and_one_thread_per_session(self):
        self.session("aaaa-1")
        claims.claim(self.p, "aaaa-1", "T01")
        claims.claim(self.p, "aaaa-1", "T01")
        with self.assertRaisesRegex(claims.ClaimError, "already holds T01"):
            claims.claim(self.p, "aaaa-1", "T02")

    def test_stale_needs_take_over(self):
        self.session("dead-1", live=False)
        claims.claim(self.p, "dead-1", "T01")
        self.assertEqual(claims.state(self.p, claims.get(self.p, "T01"))[0], "stale")
        self.session("bbbb-2")
        with self.assertRaisesRegex(claims.ClaimError, "--take-over"):
            claims.claim(self.p, "bbbb-2", "T01")
        claims.claim(self.p, "bbbb-2", "T01", take_over=True)
        self.assertEqual(claims.get(self.p, "T01")["session"], "bbbb-2")
        self.assertIsNone(sessions.get(self.p, "dead-1")["thread"])
        self.assertEqual(store.read_events(self.p.state_dir)[-1]["event"], "take-over")

    def test_done_and_unknown_threads(self):
        self.session("aaaa-1")
        with self.assertRaisesRegex(claims.ClaimError, "done"):
            claims.claim(self.p, "aaaa-1", "T03")
        with self.assertRaisesRegex(claims.ClaimError, "no thread T09"):
            claims.claim(self.p, "aaaa-1", "T09")
        claims.claim(self.p, "aaaa-1", "T03", allow_done=True)

    def test_release(self):
        self.session("aaaa-1")
        self.session("bbbb-2")
        claims.claim(self.p, "aaaa-1", "T01")
        with self.assertRaisesRegex(claims.ClaimError, "not this session"):
            claims.release(self.p, "T01", session_id="bbbb-2")
        claims.release(self.p, "T01", session_id="aaaa-1")
        self.assertIsNone(claims.get(self.p, "T01"))
        claims.claim(self.p, "bbbb-2", "T01")

    def test_owner_release_frees_session(self):
        self.session("aaaa-1")
        claims.claim(self.p, "aaaa-1", "T01")
        claims.release(self.p, "T01", by="owner")
        self.assertIsNone(sessions.get(self.p, "aaaa-1")["thread"])
        claims.claim(self.p, "aaaa-1", "T02")


class TestHooks(Fixture):
    def test_session_start_registers_and_explains(self):
        os.environ["CLAUDE_PID"] = str(self.live.pid)
        try:
            out = self.hook("session-start", session_id="cafe-1", cwd=str(self.root),
                            source="startup")
        finally:
            del os.environ["CLAUDE_PID"]
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("S-CAFE", ctx)
        self.assertIn("holds NO thread", ctx)
        self.assertIn("AskUserQuestion", ctx)
        self.assertIn("no thread yet", out["systemMessage"])
        self.assertIn("T01  fast sort", out["systemMessage"])
        self.assertIn("T01  fast sort", ctx)
        rec = sessions.get(self.p, "cafe-1")
        self.assertEqual(rec["pid"], self.live.pid)
        self.assertEqual(sessions.status(rec), "live")

    def _pre(self, sid, tool, **ti):
        return self.hook("pre-tool-use", session_id=sid, cwd=str(self.root),
                         tool_name=tool, tool_input=ti)

    def test_writes_blocked_until_claim(self):
        self.session("cafe-1")
        t01 = self.root / "threads/T01-fast-sort/bench.py"
        out = self._pre("cafe-1", "Write", file_path=str(t01), content="x")
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("holds no thread", out["hookSpecificOutput"]["permissionDecisionReason"])
        claims.claim(self.p, "cafe-1", "T01")
        self.assertIsNone(self._pre("cafe-1", "Write", file_path=str(t01), content="x"))
        # outside the thread: denied
        out = self._pre("cafe-1", "Edit", file_path=str(self.root / "threads/T02-cache-study/x.md"))
        self.assertIn("outside", out["hookSpecificOutput"]["permissionDecisionReason"])
        # relative path resolved against cwd
        self.assertIsNone(self._pre("cafe-1", "Write", file_path="threads/T01-fast-sort/a.txt"))

    def test_bash_before_claim(self):
        self.session("cafe-1")
        for ok in ["ls -la", "cat threads/INDEX.md | grep T01", "git log --oneline -5",
                   "rms claim T01", "/x/.venv/bin/rms threads", "python3 -m rms whoami",
                   # seen from a real session (2026-11-11):
                   "(rms claim T01 2>&1 || /x/.venv/bin/rms claim T01 2>&1)",
                   "rms threads 2>/dev/null && rms whoami"]:
            self.assertIsNone(self._pre("cafe-1", "Bash", command=ok), ok)
        for bad in ["rm -rf threads", "echo hi > f.txt", "git commit -m x",
                    "find . -delete", "rms claim T01; rm x", "python3 x.py",
                    "sed -i s/a/b/ f", "git branch -D x", "(rm x) || ls",
                    "rms claim T01 > out.txt", "ls $(rm x)", "rms claim T01 & rm x"]:
            out = self._pre("cafe-1", "Bash", command=bad)
            self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny", bad)
        claims.claim(self.p, "cafe-1", "T01")
        self.assertIsNone(self._pre("cafe-1", "Bash", command="python3 x.py"))

    def test_side_session(self):
        side = self.root / "side" / "scratch-ideas"
        self.session("side-1", cwd=side)
        self.assertEqual(sessions.get(self.p, "side-1")["side"], "scratch-ideas")
        self.assertIsNone(self._pre("side-1", "Write", file_path=str(side / "n.md")))
        out = self._pre("side-1", "Write", file_path=str(self.root / "threads/T01-fast-sort/n.md"))
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_extra_writable(self):
        self.session("cafe-1")
        self.assertIsNone(self._pre("cafe-1", "Write", file_path="/tmp/claude-1/x/scratch.txt"))

    def test_session_end_releases(self):
        self.session("cafe-1")
        claims.claim(self.p, "cafe-1", "T01")
        self.hook("session-end", session_id="cafe-1", cwd=str(self.root), reason="exit")
        self.assertIsNone(claims.get(self.p, "T01"))
        self.assertEqual(sessions.status(sessions.get(self.p, "cafe-1")), "ended")

    def test_lazy_registration(self):
        # hooks installed mid-session: pre-tool-use registers the session
        out = self._pre("late-1", "Write", file_path=str(self.root / "x"))
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIsNotNone(sessions.get(self.p, "late-1"))

    def test_outside_project_is_ignored(self):
        self.assertIsNone(self.hook("pre-tool-use", session_id="x", cwd="/",
                                    tool_name="Write", tool_input={"file_path": "/etc/x"}))


class TestTui(Fixture):
    def test_renders_and_releases(self):
        import asyncio
        from rms.tui import RMSApp
        self.session("aaaa-1", live=False)
        claims.claim(self.p, "aaaa-1", "T01")

        async def go():
            app = RMSApp(self.p)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                tt = app.query_one("#threads")
                self.assertEqual(tt.row_count, 4)
                await pilot.press("down")  # T01
                await pilot.press("x")
                await pilot.pause()
                await pilot.press("y")
                await pilot.pause()
                self.assertIsNone(claims.get(self.p, "T01"))
                await pilot.press("enter")
                await pilot.pause()
                self.assertEqual(type(app.screen).__name__, "TextScreen")
                await pilot.press("escape")
        asyncio.run(go())


class TestConfig(Fixture):
    def test_init_refuses_overwrite(self):
        with self.assertRaises(FileExistsError):
            init_project(self.root)


if __name__ == "__main__":
    unittest.main()

"""End-to-end thread lifecycle on a fresh sandbox repo (sandbox/reset-demo.sh).

create -> claim -> handover (two steps) -> spawn -> close into parent (merge
point) -> close as milestone -> reopen -> archive -> restore.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from rms import claims, cli, index, lifecycle, sessions
from rms.config import load
from rms.git import git, pointer

HERE = Path(__file__).resolve().parent.parent
TMP = HERE / ".tmp"
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@x"}


class Lifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        TMP.mkdir(exist_ok=True)
        cls.dir = Path(tempfile.mkdtemp(dir=TMP))
        cls._env = dict(os.environ)
        os.environ.update(GIT_ENV)
        os.environ.pop("RMS_PROJECT", None)
        subprocess.run([str(HERE / "sandbox/reset-demo.sh")], check=True, capture_output=True,
                       env={**os.environ, "RMS_DEMO_DIR": str(cls.dir / "demo")})
        cls.root = cls.dir / "demo" / "root"
        cls.p = load(cls.root)
        cls.procs = []

    @classmethod
    def tearDownClass(cls) -> None:
        for pr in cls.procs:
            pr.kill()
            pr.wait()
        os.environ.clear()
        os.environ.update(cls._env)
        shutil.rmtree(cls.dir)

    # helpers
    def session(self, sid: str) -> None:
        pr = subprocess.Popen(["sleep", "600"])
        self.procs.append(pr)
        sessions.register(self.p, sid, str(self.root), pr.pid)

    def rms(self, sid: str | None, *args: str) -> tuple[int, str]:
        old = os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        if sid:
            os.environ["CLAUDE_CODE_SESSION_ID"] = sid
        out, err = io.StringIO(), io.StringIO()
        cwd = os.getcwd()
        os.chdir(self.root)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = cli.main(list(args))
        finally:
            os.chdir(cwd)
            os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
            if old:
                os.environ["CLAUDE_CODE_SESSION_ID"] = old
        return rc, out.getvalue() + err.getvalue()

    def note(self, tid: str, state: str) -> None:
        f = lifecycle.thread_dir(self.p, tid) / "THREAD.md"
        text = f.read_text()
        import re
        text = re.sub(r"## State as of .*?(?=\n## )", f"## State as of {lifecycle.today()}\n\n{state}\n",
                      text, flags=re.S)
        f.write_text(text)

    def index_row(self, tid: str) -> dict:
        return index.get(self.p.index_file.read_text(), tid)

    # the story, in order
    def test_lifecycle(self):
        root, p = self.root, self.p
        main_ptr = pointer(root, "main", "src/fastlib")

        # --- claim: own checkout, sources on the thread branch
        self.session("aaaa-1")
        rc, out = self.rms("aaaa-1", "claim", "T01")
        self.assertEqual(rc, 0, out)
        wt = lifecycle.worktree(p, "T01")
        self.assertEqual(git(wt, "branch", "--show-current"), "thread/T01")
        # sources start without a branch (unchanged so far)
        self.assertEqual(git(wt / "src/fastlib", "branch", "--show-current"), "")
        self.assertFalse(git(root / "src/fastlib", "branch", "--list", "thread/T01"))
        from rms.config import find_project
        self.assertEqual(find_project(wt / "src/fastlib").root, root)  # checkout -> the root
        self.assertEqual(find_project(wt).root, root)
        rec = sessions.get(p, "aaaa-1")
        self.assertTrue(claims.may_write(p, rec, wt / "threads/T01-fast-sort/x.md"))
        self.assertTrue(claims.may_write(p, rec, wt / "src/fastlib/new.py"))
        self.assertFalse(claims.may_write(p, rec, root / "threads/T01-fast-sort/x.md"))  # main
        self.assertFalse(claims.may_write(p, rec, wt / "threads/INDEX.md"))

        # first edit in a source repo (through the hook) gives it the branch
        from rms import hooks
        target = wt / "src/fastlib/fastlib.py"
        with contextlib.redirect_stdout(io.StringIO()):
            hooks.run("pre-tool-use", io.StringIO(__import__("json").dumps({
                "session_id": "aaaa-1", "cwd": str(root), "tool_name": "Edit",
                "tool_input": {"file_path": str(target)}})))
        self.assertEqual(git(wt / "src/fastlib", "branch", "--show-current"), "thread/T01")
        target.write_text("def version():\n    return 'fast 2'\n")
        git(wt / "src/fastlib", "commit", "-qam", "faster")
        # a change made around the hook (shell) in another repo, left uncommitted
        (wt / "src/simulator/simulator.py").write_text("# dirty\n")

        # --- handover: step 1 marks it due
        rc, out = self.rms("aaaa-1", "handover")
        self.assertEqual(rc, 0, out)
        self.assertIn("Handover of T01 is due", out)
        # step 2 refuses: note unchanged, a source repo dirty
        rc, out = self.rms("aaaa-1", "handover")
        self.assertEqual(rc, 1)
        self.assertIn("has not changed", out)
        self.assertIn("src/simulator has uncommitted changes", out)
        # handover gave the changed repo its branch; RMS made the branch, the commit is still needed
        self.assertEqual(git(wt / "src/simulator", "branch", "--show-current"), "thread/T01")
        git(wt / "src/simulator", "checkout", "--", ".")
        self.note("T01", "fastlib 2 done; bench next.")
        rc, out = self.rms("aaaa-1", "handover", "-m", "fastlib 2")
        self.assertEqual(rc, 0, out)
        self.assertIn("merged into main", out)
        self.assertIsNone(claims.get(p, "T01"))
        # main has the note, the INDEX says paused, the source pointer did NOT move
        self.assertIn("fastlib 2 done", (root / "threads/T01-fast-sort/THREAD.md").read_text())
        self.assertEqual(self.index_row("T01")["status"], "paused")
        self.assertEqual(pointer(root, "main", "src/fastlib"), main_ptr)
        self.assertNotEqual(pointer(root, "thread/T01", "src/fastlib"), main_ptr)
        log = git(root, "log", "-3", "--format=%s%n%b", "main")
        self.assertIn("Thread: T01", log)
        # simulator was reverted: its (empty) branch exists, its pointer is unchanged
        self.assertEqual(pointer(root, "thread/T01", "src/simulator"), pointer(root, "main", "src/simulator"))

        # --- create: spawn T04 from T01
        rc, out = self.rms("aaaa-1", "create", "bench harness", "--from", "T01")
        self.assertEqual(rc, 0, out)
        self.assertIn("created T04", out)
        self.assertEqual(self.index_row("T04")["status"], "parked")
        self.assertIn("Spawns: T04", git(root, "log", "-1", "--format=%b", "main"))
        self.assertEqual(pointer(root, "thread/T04", "src/fastlib"),
                         pointer(root, "thread/T01", "src/fastlib"))

        # --- T04: work, then close into its parent T01 (merge point)
        self.session("bbbb-2")
        rc, out = self.rms("bbbb-2", "claim", "T04")
        self.assertEqual(rc, 0, out)
        w4 = lifecycle.worktree(p, "T04")
        self.assertEqual(git(w4 / "src/fastlib", "branch", "--show-current"), "")  # detached
        (w4 / "src/fastlib/bench.py").write_text("print('bench')\n")
        git(w4 / "src/fastlib", "add", "bench.py")
        git(w4 / "src/fastlib", "commit", "-qm", "bench")
        rc, out = self.rms("bbbb-2", "close", "--merge-into", "T01", "-m", "bench works")
        self.assertEqual(rc, 1)
        self.assertIn("owner's yes", out)  # no approval yet
        rc, out = self.rms("bbbb-2", "approve-close", "T04")
        self.assertEqual(rc, 1)  # a session cannot approve
        rc, out = self.rms(None, "approve-close", "T04")
        self.assertEqual(rc, 0, out)
        rc, out = self.rms("bbbb-2", "close", "--merge-into", "T01", "-m", "bench works")
        self.assertEqual(rc, 1)  # note not written yet
        self.assertIn("final State", out)
        self.note("T04", "Bench harness works.")
        rc, out = self.rms("bbbb-2", "close", "--merge-into", "T01", "-m", "bench works")
        self.assertEqual(rc, 0, out)
        self.assertIn("M-T01-01", out)
        self.assertIn("M-T01-01", git(root, "log", "-1", "--format=%s", "thread/T01"))
        w1src = lifecycle.worktree(p, "T01") / "src/fastlib"
        self.assertTrue((w1src / "bench.py").exists())  # parent's source branch has it
        self.assertEqual(self.index_row("T04")["status"], "done")
        self.assertEqual(self.index_row("T04")["merged into"], "M-T01-01")

        # --- T01: close as milestone M1
        self.session("cccc-3")
        rc, out = self.rms("cccc-3", "claim", "T01")
        self.assertEqual(rc, 0, out)
        self.note("T01", "Final: fastlib 2 with bench.")
        self.assertIsNone(lifecycle.close_approval(p, "T04"))  # used once
        self.rms(None, "approve-close", "T01")
        rc, out = self.rms("cccc-3", "close", "--milestone", "-m", "fastlib 2")
        self.assertEqual(rc, 0, out)
        self.assertIn("M1", out)
        self.assertEqual(git(root, "rev-parse", "milestone-1^{commit}"), git(root, "rev-parse", "main"))
        new_ptr = pointer(root, "main", "src/fastlib")
        self.assertNotEqual(new_ptr, main_ptr)  # a milestone moves the pointers
        self.assertEqual(git(root / "src/fastlib", "rev-parse", "milestone-1^{commit}"), new_ptr)
        self.assertIn("| M1 |", p.milestones_file.read_text())
        self.assertEqual(self.index_row("T01")["merged into"], "**M1**")
        self.assertIn("Milestone: M1", git(root, "log", "-1", "--format=%b", "main"))

        # --- reopen, archive, restore
        rc, out = self.rms(None, "reopen", "T04")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.index_row("T04")["status"], "paused")
        rc, out = self.rms(None, "archive", "T03")
        self.assertEqual(rc, 1)  # T03 is done but has no data
        (p.data_dir / "T03").mkdir(parents=True)
        (p.data_dir / "T03" / "run.log").write_text("result 42\n")
        rc, out = self.rms(None, "archive", "T03")
        self.assertEqual(rc, 0, out)
        rc, out = self.rms(None, "restore", "T03")
        self.assertEqual(rc, 1)  # working copy still there
        self.assertIn("not empty", out)
        shutil.move(str(p.data_dir / "T03"), str(self.dir / "T03-moved"))
        rc, out = self.rms(None, "restore", "T03")
        self.assertEqual(rc, 0, out)
        self.assertEqual((p.data_dir / "T03" / "run.log").read_text(), "result 42\n")
        rc, out = self.rms(None, "archive", "T02")
        self.assertIn("only done threads", out)

        # nothing left half-done in the root
        self.assertEqual(git(root, "status", "--porcelain", "--untracked-files=no", "--ignore-submodules=all"), "")


if __name__ == "__main__":
    unittest.main()

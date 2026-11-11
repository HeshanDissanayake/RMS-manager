"""The Thread view: sources and their state, builds, experiments, disk, running processes."""

from __future__ import annotations

import asyncio
import subprocess

from rms import inventory, lifecycle, sessions, threadinfo
from rms.git import git
from tests.sandbox_case import SandboxCase


class ThreadInfo(SandboxCase):
    def test_thread_view(self):
        p, root = self.p, self.root
        self.session("aaaa-1")
        rc, out = self.rms("aaaa-1", "claim", "T01")
        self.assertEqual(rc, 0, out)
        wt = lifecycle.worktree(p, "T01")
        # one source changed and committed, one with an uncommitted change
        lifecycle.ensure_source_branch(p, "T01", "src/fastlib")
        (wt / "src/fastlib/fastlib.py").write_text("x = 2\n")
        git(wt / "src/fastlib", "commit", "-qam", "change")
        (wt / "src/simulator/wip.txt").write_text("wip\n")
        # a build and some data
        b = root / "store/builds/fastlib/T01-fast"
        b.mkdir(parents=True)
        (b / "lib.a").write_bytes(b"x" * 50000)
        self.rms("aaaa-1", "build", "add", "store/builds/fastlib/T01-fast")
        (p.data_dir / "T01/EXP-T01-01/raw.bin").write_bytes(b"y" * 30000)
        # a process working in the thread's checkout
        proc = subprocess.Popen(["sleep", "60"], cwd=wt)
        self.procs.append(proc)

        d = threadinfo.collect(p, "T01")
        src = {s["repo"]: s for s in d["sources"]}
        self.assertEqual(src["src/fastlib"]["branch"], "thread/T01")
        self.assertEqual(src["src/fastlib"]["ahead"], 1)
        self.assertIn("changed: 1 commit", src["src/fastlib"]["state"])
        self.assertIsNone(src["src/simulator"]["branch"])  # unchanged so far: detached
        self.assertIn("1 uncommitted", src["src/simulator"]["state"])
        self.assertEqual([x["name"] for x in d["builds"]], ["fastlib/T01-fast"])
        self.assertGreaterEqual(d["builds"][0]["bytes"], 50000)
        exps = {e["id"]: e for e in d["experiments"]}
        self.assertEqual(exps["EXP-T01-01"]["status"], "done")
        self.assertEqual(exps["EXP-T01-01"]["title"], "baseline timing of the built-in sort")
        self.assertTrue(exps["EXP-T01-01"]["has_summary"])
        self.assertGreaterEqual(exps["EXP-T01-01"]["data_bytes"], 30000)
        self.assertEqual(exps["EXP-T01-02"]["status"], "planned")
        self.assertGreaterEqual(d["disk"]["total (own)"], 80000)
        running = {r["pid"]: r for r in d["processes"]}
        self.assertIn(proc.pid, running)
        self.assertEqual(running[proc.pid]["why"], "works in the thread's folders")
        self.assertNotIn(proc.pid, {r["pid"] for r in threadinfo.processes(p, "T02")})

        # the TUI's Thread tab shows it
        from rms.tui import RMSApp

        async def go():
            app = RMSApp(p)
            async with app.run_test(size=(160, 58)) as pilot:
                await pilot.pause(0.2)
                await pilot.press("down")  # T01
                await pilot.press("t")
                for _ in range(40):
                    await pilot.pause(0.1)
                    if "measuring" not in str(app.query_one("#t-disk").get_row_at(0)):
                        break
                self.assertEqual(app.query_one("TabbedContent").active, "thread-tab")
                self.assertEqual(app.query_one("#t-sources").row_count, 2)
                self.assertEqual(app.query_one("#t-experiments").row_count, 2)
                self.assertEqual(app.query_one("#t-builds").row_count, 1)
                self.assertNotIn("measuring", str(app.query_one("#t-disk").get_row_at(0)))
                pids = [str(app.query_one("#t-processes").get_row_at(i)[0])
                        for i in range(app.query_one("#t-processes").row_count)]
                self.assertIn(str(proc.pid), pids)
        asyncio.run(go())

    def test_bash_tool_command(self):
        w = ("/bin/bash -c source /h/.claude/shell-snapshots/snapshot-bash-1.sh 2>/dev/null || true "
             "&& eval 'make -j8 '\"'\"'all'\"'\"'' && pwd -P >| /tmp/x")
        self.assertEqual(threadinfo.bash_tool_command(w), "make -j8 'all'")

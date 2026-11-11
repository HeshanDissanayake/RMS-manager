"""Builds, data and reconciliation on a fresh sandbox (sandbox/reset-demo.sh).

Sessions record builds; RMS finds what they did not record (a session that
never told RMS), guesses the thread and source, and blocks the handover
until the thread's finds are recorded or ignored.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import time

from rms import hooks, inventory, lifecycle
from rms.git import git
from tests.sandbox_case import SandboxCase


class Inventory(SandboxCase):
    def fake_build(self, rel: str, source_dir=None, autoconf=False) -> None:
        d = self.root / rel
        (d / "bin").mkdir(parents=True, exist_ok=True)
        (d / "bin" / "tool").write_text("x" * 1000)
        if source_dir and autoconf:
            (d / "config.log").write_text(f"  $ {source_dir}/configure --prefix={d}\n")
        elif source_dir:
            (d / "CMakeCache.txt").write_text(f"CMAKE_HOME_DIRECTORY:INTERNAL={source_dir}\n")

    def bash(self, sid: str, command: str) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            hooks.run("pre-tool-use", io.StringIO(json.dumps({
                "session_id": sid, "cwd": str(self.root), "tool_name": "Bash",
                "tool_input": {"command": command}})))

    def test_inventory(self):
        root, p = self.root, self.p
        self.session("aaaa-1")
        rc, out = self.rms("aaaa-1", "claim", "T01")
        self.assertEqual(rc, 0, out)
        self.assertIn("Builds T01 made: none", out)  # claim shows the thread's store
        self.assertIn("store/data/T01/EXP-T01-01", out)
        wt = lifecycle.worktree(p, "T01")

        # use the shared base build; env gives its path
        rc, out = self.rms("aaaa-1", "build", "use", "fastlib/base")
        self.assertEqual(rc, 0, out)
        rc, out = self.rms("aaaa-1", "env")
        self.assertIn(f'export FASTLIB_BUILD="{root}/store/builds/fastlib/base"', out)
        rc, out = self.rms("aaaa-1", "build", "use", "nope/x")
        self.assertEqual(rc, 1)

        # a recorded build: source commit filled in by RMS from the checkout
        self.fake_build("store/builds/fastlib/T01-fast", source_dir=wt / "src/fastlib")
        rc, out = self.rms("aaaa-1", "build", "add", "store/builds/fastlib/T01-fast",
                           "--recipe", "cmake -B ...", "--env", "FAST_BUILD", "--cost", "expensive")
        self.assertEqual(rc, 0, out)
        b = inventory.all_builds(p)["fastlib/T01-fast"]
        self.assertEqual(b["thread"], "T01")
        self.assertEqual(b["source"]["repo"], "src/fastlib")
        self.assertEqual(b["source"]["commit"], git(wt / "src/fastlib", "rev-parse", "HEAD"))
        # another thread cannot take the name
        rec_name = inventory.all_builds(p)
        self.assertIn("fastlib/base", rec_name)

        # --- what a silent session left behind
        # 1. built from T01's checkout (autoconf), no T<NN> in its name
        self.fake_build("store/builds/simulator/quick", source_dir=wt / "src/simulator", autoconf=True)
        # 2. named for T02
        self.fake_build("store/builds/fastlib/T02-try")
        # 3. no clue but a logged command by T01's session
        self.bash("aaaa-1", "make -C store/builds/misc/tables all")
        self.fake_build("store/builds/misc/tables")
        # 4. no clue at all, written while only T01 was claimed
        self.fake_build("store/builds/misc/blob")
        # 5. data of a thread that does not exist
        (p.data_dir / "T09" / "x").mkdir(parents=True)

        found = {u["path"]: u for u in inventory.scan(p)["unrecorded"]}
        self.assertEqual(found["store/builds/simulator/quick"]["thread"], "T01")
        self.assertEqual(found["store/builds/simulator/quick"]["source"], "src/simulator")
        self.assertEqual(found["store/builds/fastlib/T02-try"]["thread"], "T02")
        self.assertEqual(found["store/builds/misc/tables"]["thread"], "T01")
        self.assertIn("make -C store/builds/misc/tables", found["store/builds/misc/tables"]["recipe"])
        self.assertEqual(found["store/builds/misc/blob"]["thread"], "T01")
        self.assertIn("only thread claimed", found["store/builds/misc/blob"]["why"])
        self.assertNotIn("store/builds/fastlib/base", found)       # recorded by T00
        self.assertNotIn("store/builds/fastlib/T01-fast", found)
        rc, out = self.rms(None, "scan")
        self.assertEqual(rc, 1)
        self.assertIn("store/data/T09", out)

        # --- handover does not stop: T01's finds become warnings for the owner
        from rms import warns
        self.rms("aaaa-1", "handover")
        self.note("T01", "Built fast; found leftovers.")
        rc, out = self.rms("aaaa-1", "handover", "-m", "first")
        self.assertEqual(rc, 0, out)
        self.assertIn("Warnings for the owner", out)
        self.assertIn("store/builds/simulator/quick", out)
        self.assertNotIn("T02-try", out)  # another thread's find: not this handover's
        self.assertEqual(len(warns.open_rows(p, "T01")), 3)

        # the next session records/ignores them; a scan resolves the warnings itself
        self.session("dddd-4")
        self.rms("dddd-4", "claim", "T01")
        self.rms("dddd-4", "build", "add", "store/builds/simulator/quick", "--recipe", "configure")
        self.rms("dddd-4", "build", "add", "store/builds/misc/tables")
        self.rms("dddd-4", "build", "ignore", "store/builds/misc/blob")
        self.rms(None, "scan")
        self.assertEqual(warns.open_rows(p, "T01"), [])
        still = {w["subject"]: w for w in warns.open_rows(p)}
        self.assertIn("store/builds/fastlib/T02-try", still)
        self.assertIn("store/data/T09", still)
        # only the owner settles a warning
        wid = still["store/data/T09"]["id"]
        rc, out = self.rms("dddd-4", "warnings", "dismiss", wid)
        self.assertEqual(rc, 1)
        rc, out = self.rms(None, "warnings", "dismiss", wid, "--note", "scratch")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("store/data/T09", {w["subject"] for w in warns.open_rows(p)})
        self.rms(None, "scan")  # a dismissed warning stays dismissed
        self.assertNotIn("store/data/T09", {w["subject"] for w in warns.open_rows(p)})

        # second handover takes the records to main
        self.rms("dddd-4", "handover")
        self.note("T01", "Recorded the builds.")
        rc, out = self.rms("dddd-4", "handover", "-m", "records")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("Warnings for the owner", out)
        on_main = json.loads(git(root, "show", "main:threads/T01-fast-sort/store.json"))
        self.assertEqual({b["name"] for b in on_main["builds"]},
                         {"fastlib/T01-fast", "simulator/quick", "misc/tables"})
        self.assertEqual(on_main["uses"], ["fastlib/base"])
        self.assertEqual(on_main["ignore"], ["store/builds/misc/blob"])
        self.assertEqual(inventory.read_record(p, "T01")["uses"], ["fastlib/base"])

        # --- missing: a recorded build whose folder vanished
        os.rename(root / "store/builds/misc/tables", self.dir / "tables-moved")
        missing = {b["name"] for b in inventory.scan(p, sizes=False)["missing"]}
        self.assertEqual(missing, {"misc/tables"})

        # --- gc: T03 is done (old data); T01 is open (its builds are kept)
        rows = {(r["kind"], r["name"]) for r in inventory.gc(p, weeks=0)}
        self.assertIn(("data", "T03"), rows)
        self.assertNotIn(("build", "fastlib/T01-fast"), rows)
        self.assertNotIn(("build", "fastlib/base"), rows)
        rows4 = {(r["kind"], r["name"]) for r in inventory.gc(p, weeks=4)}
        self.assertIn(("data", "T03"), rows4)  # T03's data is 60 days old

        # --- archive a build and one experiment; restore checks sha256
        rc, out = self.rms(None, "archive", "--build", "fastlib/T01-fast")
        self.assertEqual(rc, 0, out)
        rc, out = self.rms(None, "archive", "T03", "--exp", "EXP-T03-01")
        self.assertEqual(rc, 0, out)
        os.rename(root / "store/builds/fastlib/T01-fast", self.dir / "fast-moved")
        self.assertNotIn("fastlib/T01-fast",
                         {b["name"] for b in inventory.scan(p, sizes=False)["missing"]})  # archived
        rc, out = self.rms(None, "restore", "--build", "fastlib/T01-fast")
        self.assertEqual(rc, 0, out)
        self.assertTrue((root / "store/builds/fastlib/T01-fast/bin/tool").is_file())

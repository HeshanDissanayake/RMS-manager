"""Tickets for RMS itself, sent from a project to the RMS-manager repo."""

from __future__ import annotations

import asyncio
import io
import json
import os
from pathlib import Path

from rms import hooks, sessions, tickets
from tests.sandbox_case import SandboxCase


class Tickets(SandboxCase):
    def setUp(self):
        self.home = self.dir / "rms-home"
        os.environ["RMS_HOME"] = str(self.home)

    def tearDown(self):
        os.environ.pop("RMS_HOME", None)

    def test_file_list_close(self):
        sid = "eeee1111-0000-0000-0000-000000000000"
        self.session(sid)
        self.rms(sid, "claim", "T01")
        rc, out = self.rms(sid, "ticket", "Bash", "writes", "are", "not", "confined", "--kind", "bug",
                           "-m", "echo > ../T02/x got through")
        self.assertEqual(rc, 0, out)
        t = tickets.get("RMS-001")
        self.assertEqual((t["status"], t["kind"], t["thread"]), ("open", "bug", "T01"))
        self.assertIn(sessions.short_id(sid), t["by"])
        self.assertIn("demo-project", t["project"])
        self.assertIn("claim", t["text"])                  # recent events attached
        self.assertIn("echo > ../T02/x", t["text"])
        rc, out = self.rms(None, "ticket", "an", "idea", "--kind", "idea")
        self.assertEqual(tickets.get("2")["by"], "owner")
        rc, out = self.rms(None, "tickets", "--json")
        self.assertEqual([r["id"] for r in json.loads(out)], ["RMS-001", "RMS-002"])
        # a project session may not close tickets (hook and command)
        deny = hooks.pre_tool_use(self.p, {"session_id": sid, "cwd": str(self.root), "tool_name": "Bash",
                                           "tool_input": {"command": "rms tickets close RMS-001 -m x"}})
        self.assertEqual(deny["hookSpecificOutput"]["permissionDecision"], "deny")
        rc, out = self.rms(sid, "tickets", "close", "RMS-001", "-m", "x")
        self.assertNotEqual(rc, 0)
        # the owner (or a session in RMS-manager) closes it
        tickets.close("RMS-001", "fixed")
        self.assertEqual(tickets.get("RMS-001")["status"], "closed")
        self.assertEqual([t["id"] for t in tickets.all_tickets()], ["RMS-002"])
        self.assertEqual(len(tickets.all_tickets(include_closed=True)), 2)

    def test_tui_ticket(self):
        from rms.tui import RMSApp

        async def run():
            app = RMSApp(self.p)
            async with app.run_test(size=(150, 50)) as pilot:
                await pilot.press("n")
                await pilot.pause(0.2)
                await pilot.press(*"band too wide", "enter")
                await pilot.pause(0.3)
        asyncio.run(run())
        t = tickets.all_tickets()[-1]
        self.assertEqual((t["title"], t["by"]), ("band too wide", "owner (tui)"))

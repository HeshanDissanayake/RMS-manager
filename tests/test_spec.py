"""docs/SPEC.md lists every rms command (its §12 command reference)."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from rms import cli

SPEC = Path(__file__).resolve().parent.parent / "docs" / "SPEC.md"


class Spec(unittest.TestCase):
    def test_every_command_in_the_reference(self):
        text = SPEC.read_text(encoding="utf-8")
        ref = text.split("## 12. Command reference", 1)[1].split("\n## 13.", 1)[0]
        listed = set(re.findall(r"^\| `([a-z-]+)", ref, re.M))
        sub = next(a for a in cli.build_parser()._actions if a.dest == "cmd")
        commands = set(sub.choices)
        self.assertEqual(commands - listed, set(), "commands missing from SPEC.md §12")
        self.assertEqual(listed - commands, set(), "SPEC.md §12 lists commands that do not exist")

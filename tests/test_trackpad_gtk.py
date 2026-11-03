"""Run GTK fixtures in their own process, separate from other GApplications."""
import os
from pathlib import Path
import subprocess
import unittest


@unittest.skipUnless(os.environ.get("DISPLAY"), "Requires GTK display")
class TrackpadGtkProcessTests(unittest.TestCase):
    def test_isolated_trackpad_interactions_and_layout(self):
        result = subprocess.run(["/usr/bin/python3", str(Path(__file__).with_name("trackpad_gtk_scenario.py")), "-v"],
                                capture_output=True, text=True, timeout=40)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("CRITICAL", result.stderr, result.stderr)
        if "GTK display unavailable" in result.stderr:
            self.skipTest("GTK display unavailable")

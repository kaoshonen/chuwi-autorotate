import os
from pathlib import Path
import subprocess
import unittest

class PrivateBusTests(unittest.TestCase):
    def test_real_gio_claim_replacement_and_stale_events(self):
        result=subprocess.run(['python3',str(Path(__file__).with_name('sensor_bus_scenario.py'))],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

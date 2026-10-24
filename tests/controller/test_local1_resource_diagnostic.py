"""Original evidence diagnostic has no path to positive qualification."""
from __future__ import annotations
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
SCRIPT=ROOT/"scripts/diagnose-local1-resource-failures.py"


class ReplayForensicTest(unittest.TestCase):
    def test_failed_wall_and_lost_procfs_are_not_promoted(self):
        with tempfile.TemporaryDirectory() as directory:
            run=Path(directory)/"replay123"
            run.mkdir()
            (run/"manifest.json").write_text(json.dumps({
                "run_id":"replay123","clock_outcome":{"output_within_deadline":True},
                "notes":["resource seal phases (non-authorizing): "
                         "freeze_wall_ms=1555.799, serialization_ms=0.334, "
                         "durable_write_ms=677.229, persisted_wall_ms=2233.362"],
            }))
            (run/"resource.json").write_text(json.dumps({
                "run_id":"replay123","qualified":False,
                "processes":{"lc0-shadow":{
                    "complete":False,"reason":"run terminal sample failed: missing procfs"}},
            }))
            (run/"route.json").write_text(json.dumps({
                "run_id":"replay123","budget":{"elapsed_ms":2233.482},
                "envelope":{"wall_ms":1903.0},
                "envelope_claim":{"wall_within_envelope":False,"claimed":False},
            }))
            p=subprocess.run([sys.executable,str(SCRIPT),str(run)],text=True,
                             capture_output=True,check=True,timeout=5)
            doc=json.loads(p.stdout)
            self.assertEqual(set(doc["categories"]),{
                "WALL_OVERRUN","PROCESS_TERMINAL_SAMPLE_MISSING",
                "PHYSICAL_RESOURCE_UNQUALIFIED","SEAL_IO_LATENCY",
            })
            self.assertEqual(doc["wall_excess_ms"],330.482)
            self.assertFalse(doc["qualifies"])
            self.assertFalse(doc["original_claimed"])
            self.assertEqual(set(doc["original_file_sha256"]),
                             {"manifest.json","route.json","resource.json"})
            (run/"resource.json").unlink()
            missing=subprocess.run([sys.executable,str(SCRIPT),str(run)],
                                   capture_output=True,timeout=5)
            self.assertNotEqual(missing.returncode,0)

if __name__=="__main__":
    unittest.main()

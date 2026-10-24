"""Post-freeze fsync latency must DENY the existing full-run wall claim."""
from __future__ import annotations

import json
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from controller.resource_measurement import ResourceMeasurementRun
from tests.controller.online_helpers import shell_fixture,wait_for


class DurableWallBoundary(unittest.TestCase):
    def test_slow_durable_resource_write_never_promotes_full_run(self):
        original=ResourceMeasurementRun._atomic_write

        def slow_write(path,payload):
            # Larger than the 500ms declared move envelope; real fsync still
            # executes. No zero-duration fake persistence is substituted.
            time.sleep(0.7)
            return original(path,payload)

        with shell_fixture() as (shell,manager,shadow,out,root):
            with patch.object(ResourceMeasurementRun,"_atomic_write",
                              side_effect=slow_write):
                shell.handle_command("go movetime 500")
                wait_for(lambda: any(x.startswith("bestmove ")
                                     for x in out.getvalue().splitlines()),3)
                runs=list(root.glob("replays/*"))
                self.assertEqual(len(runs),1)
                run=runs[0]
                wait_for(lambda: (run/"route.json").is_file(),8)
                route=json.loads((run/"route.json").read_text())
                manifest=json.loads((run/"manifest.json").read_text()) if (run/"manifest.json").exists() else None
            claim=route["envelope_claim"]
            self.assertFalse(claim["wall_within_envelope"])
            self.assertFalse(claim["claimed"])
            self.assertGreater(route["budget"]["elapsed_ms"],
                               route["envelope"]["wall_ms"])
            self.assertTrue(any(line.startswith("bestmove ")
                                for line in out.getvalue().splitlines()))
            # Resource was physically measured; no permission is inferred
            # from its timing or even from an otherwise valid outward move.
            self.assertEqual(route["resource_measurement"]["path"],"resource.json")


if __name__=="__main__":
    unittest.main()

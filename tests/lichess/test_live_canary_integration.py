"""Real two-container fake-Lichess integration evidence contract.

This test is invoked by the network-integration Actions job AFTER it executes
the pinned bridge and sealed four-process UCI as White and Black. No synthetic
PASS flag, stubbed chess moves or missing games are acceptable.
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
EVIDENCE=os.environ.get("ALLFATHER_PR_A_GAME_EVIDENCE")
HEAD=os.environ.get("ALLFATHER_PR_A_EXACT_HEAD")

@unittest.skipUnless(EVIDENCE and HEAD, "Dedicated real Docker network-integration job owns these games")
class CanaryFullGameIntegration(unittest.TestCase):
    def test_real_pinned_bridge_sealed_hybrid_both_colors(self):
        source=subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()
        self.assertEqual(source,HEAD)
        base=Path(EVIDENCE).resolve()
        self.assertTrue(base.is_relative_to((ROOT/"build").resolve()))
        for color in ("white","black"):
            part=base/color
            for name in ("integration.json","gateway/server.json",
                         "gateway/gateway-state.json","bridge/report.json"):
                with self.subTest(color=color,name=name):
                    self.assertTrue((part/name).is_file(),name)
        with tempfile.TemporaryDirectory(prefix="pr-a-verify-") as tmp:
            report=Path(tmp)/"independent.json"
            command=[
                sys.executable,str(ROOT/"scripts/verify-online-live-canary.py"),
                "--policy",str(ROOT/"qualification/online-live-canary-v1.json"),
                "--mode","offline-dry-run","--evidence",str(base),
                "--source-sha",HEAD,"--output",str(report)]
            run=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=45)
            self.assertEqual(run.returncode,0,"independent verifier did not accept real games")
            record=json.loads(report.read_text())
            self.assertEqual(record.get("verdict"),"DRY_RUN_PASS")
            self.assertEqual(record.get("scope"),"fake-lichess-api-only")
            self.assertFalse(record.get("live_game_authorized"))
            self.assertFalse(record.get("public_bot_release"))
            self.assertEqual({g.get("color") for g in record.get("games",[])},
                             {"white","black"})
            self.assertTrue(all(g.get("sealed_engine_moves",0)>0
                                and g.get("network_isolated") is True
                                and g.get("owner_epoch_verified") is True
                                for g in record["games"]))

if __name__=="__main__":unittest.main()

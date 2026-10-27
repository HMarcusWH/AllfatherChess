"""Fake-UCI process integration: does not claim real Stockfish strength."""
from __future__ import annotations
import json
from pathlib import Path
import sys,tempfile,unittest
from tests.harness.uci_session import UciSession
from controller.strength.integrity import strict_load,verify_flight
ROOT=Path(__file__).resolve().parents[2]
PROFILE=ROOT/"config/allfather.strength-e00-nodes.experimental.json"
FAKE=ROOT/"tests/strength_s2/fake_native_uci.py"
EVIDENCE=ROOT/"build/strength-e00-evidence"

class E00ProcessTests(unittest.TestCase):
    def test_e00_nodes_reaches_child_once(self):
        existing={p.name for p in EVIDENCE.glob("*.json")} if EVIDENCE.exists() else set()
        with tempfile.TemporaryDirectory() as temp:
            config=json.loads(PROFILE.read_text())
            config["root"]=str(ROOT)
            config["instances"]["stockfish-anchor"]["binary"]=str(FAKE)
            path=Path(temp)/"e00.json"
            path.write_text(json.dumps(config),encoding="utf-8")
            with UciSession(Path(sys.executable),cwd=ROOT,timeout=10,
                            args=["-m","controller","--config",str(path)],
                            environment={"ALLFATHER_E00_OFFLINE":"1"}) as client:
                client.set_position({"startpos_moves":[]})
                cmd="go nodes 50000"
                client.send(cmd)
                stdout=client.read_until(lambda s:s.startswith("bestmove "),
                                         label="e00 bestmove",timeout=7)
                self.assertIn("info string native-go-received "+cmd,stdout)
                self.assertEqual([s for s in stdout if s.startswith("bestmove ")],
                                 ["bestmove e2e4"])
                client.ready()
        current={p.name for p in EVIDENCE.glob("*.json")} if EVIDENCE.exists() else set()
        created=[EVIDENCE/p for p in sorted(current-existing)]
        self.assertEqual(len(created),1)
        try:
            flight=strict_load(created[0])
            self.assertEqual(flight["events"][0]["data"]["native_time"]["kind"],
                             "strength_e00_node_plan_v1")
            verified=verify_flight(flight)
            self.assertEqual(verified["status"],"PUBLISHED_ANCHOR",verified)
            self.assertEqual(verified["replacement_count"],0)
            self.assertFalse(verified["qualified"])
        finally:
            for f in created:f.unlink()

if __name__=="__main__":
    unittest.main()

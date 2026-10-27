"""Bounded multi-turn E00 fake UCI test; source/lifecycle evidence, never strength."""
from __future__ import annotations
import json
from pathlib import Path
import sys
import tempfile
import unittest

from controller.strength.integrity import strict_load,verify_flight
from tests.harness.uci_session import UciSession

ROOT=Path(__file__).resolve().parents[2]
PROFILE=ROOT/"config/allfather.strength-e00-nodes.experimental.json"
FAKE=ROOT/"tests/strength_s2/fake_native_uci.py"
EVIDENCE=ROOT/"build/strength-e00-evidence"
COUNT=32

class E00MultiTurnStress(unittest.TestCase):
    def test_repeated_generation_and_flight_identity(self):
        existing={p.name for p in EVIDENCE.glob("*.json")} if EVIDENCE.exists() else set()
        with tempfile.TemporaryDirectory() as tmp:
            config=json.loads(PROFILE.read_text(encoding="utf-8"))
            config["root"]=str(ROOT)
            config["instances"]["stockfish-anchor"]["binary"]=str(FAKE)
            path=Path(tmp)/"fake-e00.json"
            path.write_text(json.dumps(config),encoding="utf-8")
            with UciSession(Path(sys.executable),cwd=ROOT,timeout=12,
                            args=["-m","controller","--config",str(path)],
                            environment={"ALLFATHER_E00_OFFLINE":"1"}) as child:
                for i in range(COUNT):
                    child.new_game()
                    child.set_position({"startpos_moves":[]})
                    child.send("go nodes 1000")
                    lines=child.read_until(lambda x:x.startswith("bestmove "),
                                           label=f"E00 search {i}",timeout=8)
                    self.assertEqual([x for x in lines if x.startswith("bestmove ")],
                                     ["bestmove e2e4"])
                    child.ready()
        current={p.name for p in EVIDENCE.glob("*.json")} if EVIDENCE.exists() else set()
        created=[EVIDENCE/name for name in current-existing]
        try:
            self.assertEqual(len(created),COUNT)
            generations=set()
            nonces=set()
            sessions=set()
            for file in created:
                flight=strict_load(file)
                verdict=verify_flight(flight)
                self.assertEqual(verdict["status"],"PUBLISHED_ANCHOR",verdict)
                self.assertFalse(verdict["qualified"])
                begin=flight["events"][0]["data"]["turn"]
                generations.add(begin["generation"])
                nonces.add(begin["search_nonce"])
                sessions.add(begin["session_id"])
            self.assertEqual(generations,set(range(1,COUNT+1)))
            self.assertEqual(len(nonces),COUNT)
            self.assertEqual(len(sessions),1)
        finally:
            for file in created:file.unlink()

if __name__=="__main__":
    unittest.main()

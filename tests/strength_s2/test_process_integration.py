"""End-to-end schema-v3 UCI child process; still fake engine, not E00."""
from __future__ import annotations
import json
from pathlib import Path
import sys
import tempfile
import unittest

from tests.harness.uci_session import UciSession
from controller.strength.integrity import strict_load,verify_flight

ROOT=Path(__file__).resolve().parents[2]
FAKE=ROOT/"tests/strength_s2/fake_native_uci.py"
PROFILE=ROOT/"config/allfather.strength-s2-observe.experimental.json"
OUTPUT=ROOT/"build/strength-s2-evidence"


class S2RealProcessTests(unittest.TestCase):
    def test_exact_native_go_reaches_real_child_uci(self):
        before=set(OUTPUT.glob("*.json")) if OUTPUT.exists() else set()
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"s2.json"
            doc=json.loads(PROFILE.read_text())
            doc["root"]=str(ROOT)
            doc["instances"]["stockfish-anchor"]["binary"]=sys.executable
            doc["instances"]["stockfish-anchor"]["args"]=[str(FAKE)]
            path.write_text(json.dumps(doc),encoding="utf-8")
            with UciSession(Path(sys.executable),cwd=ROOT,timeout=8,
                            args=["-m","controller","--config",str(path)]) as u:
                u.set_position({"startpos_moves":[]})
                request="go wtime 30000 btime 30000 winc 1000 binc 1000"
                u.send(request)
                output=u.read_until(lambda line:line.startswith("bestmove "),
                                    label="S2 native bestmove",timeout=5)
                self.assertIn("info string native-go-received "+request,output)
                self.assertEqual([l for l in output if l.startswith("bestmove ")],
                                 ["bestmove e2e4"])
                u.ready()
        after=set(OUTPUT.glob("*.json")) if OUTPUT.exists() else set()
        created=after-before
        self.assertEqual(len(created),1,"one native search has exactly one persistent S2 flight")
        try:
            result=verify_flight(strict_load(next(iter(created))))
            self.assertEqual(result["status"],"PUBLISHED_ANCHOR",result)
            self.assertFalse(result["qualified"])
            self.assertEqual(result["replacement_count"],0)
        finally:
            for file in created:
                file.unlink()


if __name__=="__main__":
    unittest.main()

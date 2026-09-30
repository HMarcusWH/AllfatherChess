#!/usr/bin/env python3
from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))

from tests.harness.uci_session import UciError,UciSession


SCRIPT='''#!/usr/bin/env python3
import os,sys
for raw in sys.stdin:
    cmd=raw.strip()
    if cmd=="uci":
        print("id name EnvFake",flush=True)
        print("info string LAB_ENV="+os.environ.get("LAB_ENV","<missing>"),flush=True)
        print("uciok",flush=True)
    elif cmd=="isready":
        print("readyok",flush=True)
    elif cmd=="quit":
        break
'''


class UciEnvironmentTests(unittest.TestCase):
    def test_environment_is_child_local_and_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"env_uci.py"
            path.write_text(SCRIPT,encoding="utf-8")
            path.chmod(path.stat().st_mode|stat.S_IXUSR)
            os.environ["LAB_ENV"]="parent"
            session=UciSession(
                path,cwd=Path(tmp),environment={"LAB_ENV":"child"}
            )
            try:
                session.start()
                self.assertIn("<< info string LAB_ENV=child",session.transcript)
                self.assertEqual(os.environ["LAB_ENV"],"parent")
            finally:
                session.close()
                os.environ.pop("LAB_ENV",None)

    def test_environment_rejects_non_strings(self):
        with self.assertRaises(UciError):
            UciSession(
                Path("/tmp/unused"),
                cwd=Path("/tmp"),
                environment={"LAB_ENV":1},
            )


if __name__=="__main__":
    unittest.main()

"""Simulated APT 503 and signature failures without touching host APT."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
SCRIPT=ROOT/"deploy/release/install-pinned-apt.sh"

FAKE_APT='''#!/bin/sh
case " $* " in
  *" update "*) phase=update ;;
  *" install "*) phase=install ;;
  *) echo "unexpected fake apt arguments" >&2; exit 99 ;;
esac
file="$APT_TEST_COUNTER/$phase"
count=0
[ ! -f "$file" ] || count=$(cat "$file")
count=$((count+1))
printf "%s" "$count" > "$file"
case "$APT_TEST_MODE" in
  recover)
    if [ "$phase" = update ] && [ "$count" -le 2 ]; then
      echo "E: Failed to fetch InRelease 503 Service Unavailable" >&2
      exit 100
    fi ;;
  always503) echo "E: InRelease 503 Service Unavailable" >&2; exit 100 ;;
  badsignature)
    echo "E: BADSIG GPG signature failure" >&2
    echo "E: InRelease 503 Service Unavailable" >&2
    exit 100 ;;
  badhash) echo "E: Hash Sum mismatch" >&2; exit 100 ;;
  missing) echo "E: 404 Not Found" >&2; exit 100 ;;
esac
exit 0
'''

class PinnedSnapshotRetryTests(unittest.TestCase):
    def execute(self, mode):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td)
            (d/"bin").mkdir()
            apt=d/"bin/apt-get"
            apt.write_text(FAKE_APT)
            apt.chmod(0o755)
            (d/"lists").mkdir()
            stale=d/"lists/old-index"
            stale.write_text("not admissible")
            env={**os.environ,"PATH":str(d/"bin")+":"+os.environ["PATH"],
                 "APT_TEST_COUNTER":str(d),"APT_TEST_MODE":mode,
                 "APT_LISTS_DIR":str(d/"lists"),"APT_RETRY_BACKOFF_SECONDS":"0",
                 "APT_LOG":str(d/"trace.log")}
            outcome=subprocess.run(["/bin/sh",str(SCRIPT)],env=env,
                                   capture_output=True,text=True,timeout=15)
            counts={k:int((d/k).read_text()) if (d/k).exists() else 0
                    for k in ("update","install")}
            self.assertFalse(stale.exists())
            self.assertFalse((d/"trace.log").exists())
            return outcome,counts

    def test_transient_outage_then_signed_success(self):
        result,counts=self.execute("recover")
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(counts,{"update":3,"install":1})

    def test_persistent_503_is_bounded_and_blocks_install(self):
        result,counts=self.execute("always503")
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(counts,{"update":4,"install":0})

    def test_signature_hash_and_missing_source_fail_without_retries(self):
        for mode in ("badsignature","badhash","missing"):
            with self.subTest(mode=mode):
                result,counts=self.execute(mode)
                self.assertNotEqual(result.returncode,0)
                self.assertEqual(counts,{"update":1,"install":0})

if __name__=="__main__":
    unittest.main()

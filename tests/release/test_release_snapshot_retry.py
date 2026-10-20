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
  always503) echo "E: Failed to fetch noble/InRelease 503 Service Unavailable" >&2; exit 100 ;;
  badsignature)
    echo "E: BADSIG GPG signature failure" >&2
    echo "E: InRelease 503 Service Unavailable" >&2
    exit 100 ;;
  badhash) echo "E: Hash Sum mismatch" >&2; exit 100 ;;
  missing) echo "E: 404 Not Found" >&2; exit 100 ;;
  mixed-dns)
    echo "Err:1 https://snapshot.ubuntu.com/ubuntu noble InRelease" >&2
    echo "  503 Service Unavailable" >&2
    echo "Err:2 https://snapshot.ubuntu.com/ubuntu noble-security InRelease" >&2
    echo "  Temporary failure resolving snapshot.ubuntu.com" >&2
    echo "E: Failed to fetch noble/InRelease 503 Service Unavailable" >&2
    echo "E: Failed to fetch noble-security/InRelease Temporary failure resolving" >&2
    exit 100 ;;
  mixed-401)
    echo "E: Failed to fetch noble/InRelease 503 Service Unavailable" >&2
    echo "E: Failed to fetch noble-security/InRelease 401 Unauthorized" >&2
    exit 100 ;;
  mixed-signature)
    echo "E: Failed to fetch noble/InRelease 503 Service Unavailable" >&2
    echo "E: BADSIG: invalid signature" >&2
    exit 100 ;;
  mixed-unclassified)
    echo "E: Failed to fetch noble/InRelease 503 Service Unavailable" >&2
    echo "E: Some unknown acquisition condition" >&2
    exit 100 ;;
  real503port)
    echo "Err:4 https://snapshot.ubuntu.com/ubuntu noble InRelease" >&2
    echo "  503  Service Unavailable [IP: 185.125.189.69 443]" >&2
    echo "E: Failed to fetch https://snapshot.ubuntu.com/ubuntu/noble/InRelease 503 Service Unavailable [IP: 185.125.189.69 443]" >&2
    echo "E: Some index files failed to download. They have been ignored, or old ones used instead." >&2
    exit 100 ;;
  real504port)
    echo "Err:4 https://snapshot.ubuntu.com/ubuntu noble InRelease" >&2
    echo "  504  Gateway Time-out [IP: 185.125.189.69 443]" >&2
    echo "E: Failed to fetch https://snapshot.ubuntu.com/ubuntu/noble/InRelease 504 Gateway Time-out [IP: 185.125.189.69 443]" >&2
    exit 100 ;;
  install503)
    if [ "$phase" = install ]; then
      echo "Err:1 https://snapshot.ubuntu.com/ubuntu noble packages" >&2
      echo "  503 Service Unavailable [IP: 185.125.189.69 443]" >&2
      echo "E: Failed to fetch https://snapshot.ubuntu.com/ubuntu/packages.deb 503 Service Unavailable [IP: 185.125.189.69 443]" >&2
      echo "E: Unable to fetch some archives, maybe run apt-get update or try with --fix-missing?" >&2
      exit 100
    fi ;;
  install503mixed)
    if [ "$phase" = install ]; then
      echo "E: Failed to fetch https://snapshot.ubuntu.com/ubuntu/packages.deb 503 Service Unavailable" >&2
      echo "E: Failed to fetch https://snapshot.ubuntu.com/ubuntu/other.deb 401 Unauthorized" >&2
      echo "E: Unable to fetch some archives, maybe run apt-get update or try with --fix-missing?" >&2
      exit 100
    fi ;;
  two503)
    echo "Err:1 https://snapshot.ubuntu.com/ubuntu noble InRelease" >&2
    echo "  503 Service Unavailable" >&2
    echo "Err:2 https://snapshot.ubuntu.com/ubuntu noble-security InRelease" >&2
    echo "  503 Service Unavailable" >&2
    echo "E: Failed to fetch noble/InRelease 503 Service Unavailable" >&2
    echo "E: Failed to fetch noble-security/InRelease 503 Service Unavailable" >&2
    exit 100 ;;
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
            selections=d/"versions.txt"
            selections.write_text("".join(
                f"pinned-native-{i}=1.0\n" for i in range(10)))
            env={**os.environ,"PATH":str(d/"bin")+":"+os.environ["PATH"],
                 "APT_TEST_COUNTER":str(d),"APT_TEST_MODE":mode,
                 "APT_SELECTIONS_FILE":str(selections),
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

    def test_mixed_error_class_never_retries(self):
        for mode in ("mixed-dns","mixed-401","mixed-signature",
                     "mixed-unclassified"):
            with self.subTest(mode=mode):
                result,counts=self.execute(mode)
                self.assertNotEqual(result.returncode,0)
                self.assertEqual(counts,{"update":1,"install":0})

    def test_realistic_503_and_504_logs_with_https_port_are_retryable(self):
        for mode in ("real503port","real504port"):
            with self.subTest(mode=mode):
                result,counts=self.execute(mode)
                self.assertNotEqual(result.returncode,0)
                self.assertEqual(counts,{"update":4,"install":0})
                self.assertIn("exhausted four bounded attempts",result.stderr)

    def test_install_summary_with_only_transient_errors_retries_bounded(self):
        result,counts=self.execute("install503")
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(counts,{"update":1,"install":4})

    def test_install_summary_does_not_override_unknown_or_401(self):
        result,counts=self.execute("install503mixed")
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(counts,{"update":1,"install":1})

    def test_multiple_503s_are_individually_classified_and_bounded(self):
        result,counts=self.execute("two503")
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

"""CI-only APT bootstrap guards; no network or sudo prerequisites."""
from __future__ import annotations
import pathlib
import subprocess
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[2]
SCRIPT=ROOT/"scripts/ci-install-apt-deps.sh"

class CiAptHostBootstrap(unittest.TestCase):
    def test_signed_sources_and_no_stale_authentication_bypass(self):
        result=subprocess.run(["bash",str(SCRIPT),"--render-sources","noble","amd64"],
                              text=True,capture_output=True,check=True,timeout=10)
        source=result.stdout
        self.assertIn("https://archive.ubuntu.com/ubuntu",source)
        self.assertIn("https://security.ubuntu.com/ubuntu",source)
        self.assertIn("Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg",source)
        self.assertNotIn("trusted=yes",source)
        self.assertNotIn("allow-insecure",source)

    def test_unknown_architecture_is_rejected(self):
        result=subprocess.run(["bash",str(SCRIPT),"--render-sources","noble","mips"],
                              capture_output=True,text=True,timeout=10)
        self.assertNotEqual(result.returncode,0)

    def test_installed_dependencies_skip_every_network_operation(self):
        # Mock only the external host-inspection command. The script itself
        # still validates requested names and chooses the official path.
        command=f'''set -euo pipefail
source "{SCRIPT}"
dpkg-query() {{
  if [[ "$1" == "-W" && "$2" == -f=* ]]; then
    printf "install ok installed"
  else
    printf "python3-pip=verified-runner-version\\n"
  fi
}}
apt_run() {{ printf "UNEXPECTED-APT" >&2; return 77; }}
install_packages noble amd64 python3-pip
'''
        out=subprocess.run(["bash","-c",command],capture_output=True,text=True,timeout=10)
        self.assertEqual(out.returncode,0,out.stderr)
        self.assertIn("no APT index acquisition",out.stdout)
        self.assertNotIn("UNEXPECTED-APT",out.stderr)

    def test_missing_host_package_needs_signed_refresh_and_fails_closed(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            command=f"""set -euo pipefail
source "{SCRIPT}"
dpkg-query() {{
  if [[ "$2" == -f=* ]]; then
    printf 'deinstall ok config-files'
  else
    printf 'unqualified-package\\n'
  fi
}}
apt_run() {{ printf 'signed package acquisition unavailable\\n' >&2; return 124; }}
sudo() {{
  if [[ "$1" != rm ]]; then return 97; fi
  shift
  command rm "$@"
}}
install_packages noble amd64 python3-venv
"""
            env=dict(os.environ,TMPDIR=temp)
            result=subprocess.run(["bash","-c",command],env=env,
                                  capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,124,result.stderr)
            self.assertIn("APT update failed/expired",result.stderr)
            self.assertNotIn("APT dependencies installed",result.stdout)
            self.assertEqual(list(pathlib.Path(temp).iterdir()),[],
                             "partial signed APT lists leaked after failure")

    def test_signature_or_hash_error_never_promotes_installed_dependencies(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            command=f"""set -euo pipefail
source "{SCRIPT}"
dpkg-query() {{ printf 'unavailable'; return 1; }}
apt_run() {{
  printf 'BADSIG: source identity failed\\n' >&2
  return 100
}}
sudo() {{
  if [[ "$1" != rm ]]; then return 97; fi
  shift
  command rm "$@"
}}
install_packages noble amd64 libopenblas-dev
"""
            result=subprocess.run(["bash","-c",command],capture_output=True,
                                  text=True,timeout=15,
                                  env=dict(os.environ,TMPDIR=temp))
            self.assertEqual(result.returncode,100,result.stderr)
            self.assertIn("BADSIG",result.stderr)
            self.assertIn("no stale-index fallback",result.stderr)
            self.assertNotIn("APT dependencies installed",result.stdout)
            self.assertEqual(list(pathlib.Path(temp).iterdir()),[])

    def test_bad_package_name_rejected_before_any_apt_operation(self):
        command=f"""set -euo pipefail
source "{SCRIPT}"
dpkg-query() {{ printf 'unexpected query' >&2; return 96; }}
apt_run() {{ printf 'UNEXPECTED-APT' >&2; return 97; }}
install_packages noble amd64 'python3-pip;rm'
"""
        result=subprocess.run(["bash","-c",command],capture_output=True,
                              text=True,timeout=10)
        self.assertNotEqual(result.returncode,0)
        self.assertIn("Invalid APT package argument",result.stderr)
        self.assertNotIn("UNEXPECTED-APT",result.stderr)
        self.assertNotIn("unexpected query",result.stderr)

    def test_helper_never_ignores_signature_or_hash_errors(self):
        txt=SCRIPT.read_text()
        self.assertIn("APT::Update::Error-Mode=any",txt)
        self.assertIn("Acquire::Languages=none",txt)
        self.assertIn("no stale-index fallback",txt)
        self.assertIn("sudo rm -rf --",txt)
        self.assertNotIn("Acquire::AllowInsecureRepositories=true",txt)
        # Check executable APT options, not comments prohibiting unsafe overrides.
        executable="\n".join(line for line in txt.splitlines()
                            if not line.lstrip().startswith("#"))
        self.assertNotIn("trusted=yes",executable)

if __name__=="__main__":
    unittest.main()

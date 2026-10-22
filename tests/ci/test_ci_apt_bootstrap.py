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
  if [[ "$1" == "-W" && "$2" == "-f=\\\${{Status}}" ]]; then
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

    def test_helper_never_ignores_signature_or_hash_errors(self):
        txt=SCRIPT.read_text()
        self.assertIn("APT::Update::Error-Mode=any",txt)
        self.assertIn("Acquire::Languages=none",txt)
        self.assertIn("no stale-index fallback",txt)
        self.assertIn("sudo rm -rf --",txt)
        self.assertNotIn("Acquire::AllowInsecureRepositories=true",txt)
        self.assertNotIn("trusted=yes",txt)

if __name__=="__main__":
    unittest.main()

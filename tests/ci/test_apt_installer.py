#!/usr/bin/env python3
"""Fail-closed offline contract tests for the hosted-runner APT bootstrap."""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/ci-install-apt-deps.sh"
WORKFLOWS = ROOT / ".github/workflows"
EXPECTED_STEPS = {
    "baseline.yml": 1,
    "engine-optimization.yml": 3,
    "full-game-qualification.yml": 4,
    "lc0-artifact-domain-diagnostic.yml": 1,
    "lc0-strength-qualification.yml": 1,
    "meta-1.yml": 1,
    "online-hybrid-qualification.yml": 1,
    "online-profile-qualification.yml": 1,
    "resource-profile-lab-v2.yml": 2,
    "resource-profile-lab.yml": 2,
}


class AptInstallerTests(unittest.TestCase):
    def invoke(self, *args):
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            capture_output=True, text=True, check=False,
        )

    def test_supported_official_sources_use_archive_signature_verification(self):
        for codename in ("jammy", "noble"):
            for arch in ("amd64", "arm64"):
                with self.subTest(codename=codename, arch=arch):
                    result = self.invoke("--render-sources", codename, arch)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg", result.stdout)
                    self.assertIn(f"Suites: {codename} {codename}-updates {codename}-backports", result.stdout)
                    self.assertIn(f"Suites: {codename}-security", result.stdout)
                    self.assertIn(f"Architectures: {arch}", result.stdout)
                    self.assertNotIn("azure.archive.ubuntu.com", result.stdout)
                    self.assertNotIn("trusted=yes", result.stdout.lower())
                    self.assertNotIn("http://", result.stdout)
                    if arch == "amd64":
                        self.assertIn("https://archive.ubuntu.com/ubuntu", result.stdout)
                        self.assertIn("https://security.ubuntu.com/ubuntu", result.stdout)
                    else:
                        self.assertIn("https://ports.ubuntu.com/ubuntu-ports", result.stdout)

    def test_unknown_releases_and_architectures_fail_closed(self):
        for codename, arch in (("focal", "amd64"), ("noble", "riscv64"), ("x", "x")):
            with self.subTest(codename=codename, arch=arch):
                self.assertNotEqual(
                    self.invoke("--render-sources", codename, arch).returncode, 0,
                )
        self.assertNotEqual(self.invoke().returncode, 0)

    def test_update_timeout_and_install_failure_do_not_continue(self):
        # Mock *all* timeout invocations. No networking, root, or actual apt.
        for code, label in ((124, "update"), (100, "install")):
            script = f"""
source "{SCRIPT}"
APT_KEYRING="$(mktemp)"
printf 'offline-test-keyring' > "$APT_KEYRING"
trap 'rm -f "$APT_KEYRING"' EXIT
timeout() {{
  if [[ "{label}" == "update" ]]; then
    return 124
  fi
  if [[ "$*" == *" update" ]]; then
    return 0
  fi
  return 100
}}
if install_packages noble amd64 zlib1g-dev; then
  echo "unexpected success" >&2
  exit 1
else
  status=$?
  test "$status" -eq {code}
fi
"""
            result = subprocess.run(
                ["bash", "-c", script], capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, f"{label}: {result.stdout}\n{result.stderr}")
            expected_error = "APT update failed/expired" if label == "update" else "APT installation failed/expired"
            self.assertIn(expected_error, result.stderr)
            self.assertNotIn("APT dependencies installed", result.stdout)

    def test_workflows_have_no_direct_unbounded_apt_calls(self):
        observed = 0
        for name, expected in EXPECTED_STEPS.items():
            path = WORKFLOWS / name
            doc = path.read_text(encoding="utf-8")
            self.assertNotIn("sudo apt-get update", doc, name)
            self.assertNotIn("sudo apt-get install", doc, name)
            actual = doc.count("bash scripts/ci-install-apt-deps.sh ")
            self.assertEqual(actual, expected, name)
            self.assertGreaterEqual(doc.count("timeout-minutes: 5"), 1, name)
            self.assertIn("scripts/ci-install-apt-deps.sh", doc, name)
            self.assertIn("tests/ci/test_apt_installer.py", doc, name)
            observed += actual
        self.assertEqual(observed, 17)

    def test_bootstrap_has_independent_limits_and_strict_indexes(self):
        doc = SCRIPT.read_text(encoding="utf-8")
        for literal in (
            "APT_UPDATE_TIMEOUT_SEC=90",
            "APT_INSTALL_TIMEOUT_SEC=180",
            "APT::Update::Error-Mode=any",
            "Dir::Etc::sourceparts=",
            "Dir::State::lists=",
            "timeout --signal=TERM --kill-after=5s",
            "APT update failed/expired",
            "APT installation failed/expired",
        ):
            self.assertIn(literal, doc)


if __name__ == "__main__":
    unittest.main()

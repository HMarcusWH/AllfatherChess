"""Source-bound release workflows and runnable operator examples."""
from __future__ import annotations
import subprocess
import unittest
from pathlib import Path

import yaml

ROOT=Path(__file__).resolve().parents[2]


class WorkflowCoverageTests(unittest.TestCase):
    def test_release_candidate_runs_on_all_prs_and_merged_main(self):
        cfg=yaml.load((ROOT/".github/workflows/release-candidate.yml").read_text(),
                      Loader=yaml.BaseLoader)
        events=cfg["on"]
        self.assertIn("pull_request",events)
        self.assertNotIn("paths",events["pull_request"] or {})
        self.assertEqual(events["push"]["branches"],["main"])
        self.assertIn("SOURCE_SHA",cfg["env"])
        self.assertIn("github.event.pull_request.head.sha",cfg["env"]["SOURCE_SHA"])

    def test_operations_docker_command_is_valid_shell(self):
        content=(ROOT/"docs/RELEASE_1_OPERATIONS.md").read_text()
        fence=chr(96)*3
        start=content.index(fence+"sh\n",content.index("## Runtime contract"))+len(fence+"sh\n")
        end=content.index("\n"+fence,start)
        command=content[start:end]
        self.assertEqual(subprocess.run(["bash","-n"],input=command,text=True,
                                        capture_output=True).returncode,0)
        for fragment in ("--network none","--read-only","--cap-drop ALL",
                         "--security-opt no-new-privileges",
                         ":/opt/allfather/build/replays-online-hybrid-v2:rw",
                         "--user 10001:10001"):
            self.assertIn(fragment,command)


    def test_pinned_scanner_is_exercised_without_chess_build(self):
        source=(ROOT/".github/workflows/release-candidate.yml").read_text()
        self.assertIn("scanner-compatibility:",source)
        self.assertIn("needs: [release-contracts, scanner-compatibility]",source)
        self.assertIn("scripts/check-release-scanner.py",source)
        self.assertIn("--attest \"type=sbom,generator=$SCANNER\"",source)
        self.assertIn("scanner-contract.json",source)
        scanner=source.split("  scanner-compatibility:",1)[1].split(
            "  release-oci-integration:",1)[0]
        self.assertNotIn("make build-online-engine-opt-v2",scanner)
        self.assertNotIn("release1-scanner/scanner.oci.tar\n          if",scanner)

    def test_release_artifacts_exclude_raw_provenance_and_image_bytes(self):
        content=(ROOT/".github/workflows/release-candidate.yml").read_text()
        upload=content.split("- name: Do not publish combined image",1)[1]
        self.assertIn("oci-attestation-structure.json", upload)
        self.assertIn("smoke-verdict.json",upload)
        self.assertIn("aggregate.json",upload)
        for forbidden in ("/*.json", "/*.txt", "/*.log",
                          "build/test-results/release1/oci-build-metadata.json",
                          "build/test-results/release1/docker-build-metadata.json",
                          "build/test-results/release1/docker-inspect.json",
                          "build/release1-offline.oci.tar"):
            self.assertNotIn(forbidden,upload)

    def test_offline_oci_export_is_named_and_diagnostics_precede_admission(self):
        content=(ROOT/".github/workflows/release-candidate.yml").read_text()
        export=content.split("- name: Export standalone OCI archive",1)[1]
        self.assertIn("--tag allfather/release1:offline",export)
        self.assertIn("scripts/inspect-release-oci.py",export)
        self.assertLess(export.index("scripts/inspect-release-oci.py"),
                        export.index("scripts/verify-release-oci.py"))
        self.assertIn("scripts/qualify-release-smoke.py",content)
        self.assertLess(content.index("scripts/qualify-release-smoke.py"),
                        content.index("- name: Export standalone OCI archive"))
        self.assertIn("--oci-archive build/release1-offline.oci.tar",content)
        self.assertIn("snapshot-acquisition-failure.json",content)
        self.assertIn("snapshot-availability.json", content)
        self.assertLess(content.index("scripts/check-release-snapshot.py"),
                        content.index("- name: Install exact engine"))
        self.assertIn('exit 1',content.split("snapshot-acquisition-failure.json",1)[1])
        self.assertIn("candidate_qualified",content)
        self.assertIn("build/test-results/release1/smoke-verdict.json",content)

if __name__=="__main__":
    unittest.main()

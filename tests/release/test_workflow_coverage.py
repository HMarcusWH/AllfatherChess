"""Source-bound release workflows and runnable operator examples."""
from __future__ import annotations
import subprocess
import unittest
from pathlib import Path

import yaml

ROOT=Path(__file__).resolve().parents[2]


class WorkflowCoverageTests(unittest.TestCase):

    def test_release_2b1_resolver_is_immutable_and_source_attested(self):
        required = (
            "deploy/lichess/requirements.in",
            "deploy/lichess/requirements.release.lock",
            "qualification/release-python-wheels-v1.json",
            "qualification/release-python-builder-v1.json",
            "deploy/release/Dockerfile.wheel-builder",
            "deploy/release/python-wheel-build-tools.lock",
            "scripts/verify-release-python-lock.py",
            "scripts/prepare-online3b-wheels.py",
        )
        stage = (ROOT / "scripts/build-release-candidate.py").read_text()
        for path in required:
            with self.subTest(attested=path):
                self.assertIn('"' + path + '"', stage)
        workflows = ("release-candidate.yml", "online-bridge.yml",
                     "online-recovery.yml", "online-recovery-v2.yml")
        for workflow in workflows:
            with self.subTest(workflow=workflow):
                text = (ROOT / ".github/workflows" / workflow).read_text()
                self.assertNotIn("piptools compile", text)
                self.assertNotIn("pip-compile", text)
                self.assertIn("requirements.release.lock", text)
        release = (ROOT / ".github/workflows/release-candidate.yml").read_text()
        self.assertLess(release.index("scripts/verify-release-python-lock.py --root ."),
                        release.index("scripts/build-release-candidate.py"))

    def test_online_bridge_contracts_trigger_for_assurance_dependencies(self):
        cfg=yaml.load((ROOT/".github/workflows/online-bridge.yml").read_text(),
                      Loader=yaml.BaseLoader)
        paths=set(cfg["on"]["pull_request"]["paths"])
        for critical in (
            "scripts/sanitize_qualification_evidence.py",
            "scripts/http_evidence_verdict.py",
            "scripts/qualify-online-recovery.py",
            "qualification/http-evidence-contract-v1.json",
            "qualification/online-recovery-v1.json"):
            with self.subTest(source=critical):
                self.assertIn(critical,paths)

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

    def test_release_verdict_sources_are_git_attested_before_import(self):
        source=(ROOT/".github/workflows/release-candidate.yml").read_text()
        self.assertIn(
            "Verify scanner and SPDX source against exact committed Git bytes",
            source)
        self.assertIn(
            "Verify every offline verdict module before aggregate execution",
            source)
        self.assertIn("VERDICT_SOURCE_BYTES_DIFFER_FROM_GIT",source)
        builder=(ROOT/"scripts/build-release-candidate.py").read_text()
        for relative in (
            "scripts/qualify-release-aggregate.py",
            "scripts/qualify-release-smoke.py",
            "scripts/release_http_contract.py",
            "scripts/verify-release-oci.py",
            "scripts/spdx_license_contract.py",
            "qualification/spdx-license-identifiers-v3.29.0.json",
            "qualification/release-scanner-v1.json",
            "deploy/release/scanner/target.go.patch",
            "deploy/release/scanner/Dockerfile",
            "scripts/prepare-release-scanner.py",
            "scripts/ci-release-scanner.py",
            "scripts/check-release-scanner-content.py",
        ):
            with self.subTest(relative=relative):
                self.assertIn('"' + relative + '"',builder)
        source_code=(ROOT/"scripts/qualify-release-aggregate.py").read_text()
        self.assertIn("with verified_release_snapshot",source_code)
        self.assertIn("Git committed bytes",source_code)

    def test_every_independent_release_verifier_uses_isolated_committed_source(self):
        source=(ROOT/".github/workflows/release-candidate.yml").read_text()
        self.assertIn("Checkout isolated exact-commit source",source)
        self.assertIn('git worktree add --detach "$VERIFIED" "$SOURCE_SHA"',source)
        for name in (
            "qualify-release-smoke.py", "inspect-release-oci.py",
            "verify-release-oci.py", "qualify-release-host.py",
            "qualify-release-aggregate.py",
        ):
            with self.subTest(executable=name):
                self.assertIn(
                    '"$RELEASE1_VERIFIED_ROOT/scripts/'+name+'"',source)
                self.assertNotIn("python3 scripts/"+name+" \\",source)
        self.assertLess(source.index("Checkout isolated exact-commit source"),
                        source.index("Independently reconstruct all packaged"))
        self.assertLess(source.index("Checkout isolated exact-commit source"),
                        source.index("Export standalone OCI archive"))

    def test_dependency_observation_is_source_attested_after_image_qualification(self):
        workflow=(ROOT/".github/workflows/release-candidate.yml").read_text()
        self.assertIn("scripts/observe-release-dependencies.py",workflow)
        self.assertIn('"$RELEASE1_VERIFIED_ROOT/scripts/observe-release-dependencies.py"',workflow)
        self.assertIn('--source-sha "$SOURCE_SHA"',workflow)
        self.assertIn("dependency-observation.json",workflow)
        self.assertLess(
            workflow.index("- name: Observe test-runner capacity and aggregate evidence"),
            workflow.index("- name: Observe candidate OS, Python and wheel"))
        self.assertLess(
            workflow.index("- name: Checkout isolated exact-commit source"),
            workflow.index("- name: Observe candidate OS, Python and wheel"))
        builder=(ROOT/"scripts/build-release-candidate.py").read_text()
        self.assertIn('"scripts/observe-release-dependencies.py"',builder)
        observer=(ROOT/"scripts/observe-release-dependencies.py").read_text()
        for boundary in ('"dependency_lock_approved":False',
                         '"cross_run_reproducibility_qualified":False',
                         '"offline_candidate_qualified":False',
                         '"artifact_distribution_authorized":False',
                         '"public_bot_release":False'):
            self.assertIn(boundary,observer)

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
        self.assertIn('--oci-archive "$GITHUB_WORKSPACE/build/release1-offline.oci.tar"',content)
        self.assertIn("snapshot-acquisition-failure.json",content)
        self.assertIn("snapshot-availability.json", content)
        # Cold signed byte acquisition or strict cached revalidation must
        # occur before expensive engine builds; cache presence is NOT proof.
        self.assertIn("scripts/prepare-release-native-bundle.py",content)
        self.assertIn("actions/cache/restore@",content)
        self.assertIn("actions/cache/save@",content)
        self.assertLess(content.index("scripts/prepare-release-native-bundle.py"),
                        content.index("- name: Install exact engine"))
        self.assertIn('exit 1',content.split("snapshot-acquisition-failure.json",1)[1])
        self.assertIn("candidate_qualified",content)
        self.assertIn("build/test-results/release1/smoke-verdict.json",content)

if __name__=="__main__":
    unittest.main()

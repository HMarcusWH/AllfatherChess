#!/usr/bin/env python3
"""Hermetic ONLINE-3A sealing, relocation and fail-closed launcher regression."""
from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def script(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


seal = script(ROOT / "scripts/release-manifest.py", "online_release_seal")
packer = script(ROOT / "scripts/package-online.py", "online_offline_packer")
preflight = script(ROOT / "scripts/online-host-preflight.py", "online_preflight")


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        originals = (
            "scripts/release-manifest.py", "scripts/package-online.py",
            "scripts/online-host-preflight.py", "deploy/bin/allfather-online",
            "controller/__init__.py",
        )
        for name in originals:
            self.write(name, (ROOT / name).read_bytes())
        self.write("LICENSES.md", b"notice\n")
        self.write("vendor.lock.json", b"{}\n")
        engines, networks = {}, {}
        for family in ("stockfish", "reckless", "lc0"):
            binary = "bin/" + family
            network = "networks/" + family + ".nn"
            self.write("build/online-engine-opt-v2/" + binary, b"binary" + family.encode())
            self.write("build/online-engine-opt-v2/" + network, b"weights" + family.encode())
            engines[family] = self.bundle_entry(binary)
            networks[family] = self.bundle_entry(network)
        self.write("build/online-engine-opt-v2/build-manifest.json", json.dumps({
            "profile_id": "online-engine-opt-v2", "source_commit": "a" * 40,
            "source_tree": "b" * 40,
            "artifacts": {"engines": engines, "networks": networks},
        }).encode())
        instances = {}
        for instance, family in (
            ("stockfish-anchor", "stockfish"), ("stockfish-shadow", "stockfish"),
            ("reckless-shadow", "reckless"), ("lc0-shadow", "lc0")
        ):
            row = {"binary": "build/online-engine-opt-v2/bin/" + family}
            if family == "lc0":
                row["options"] = {
                    "WeightsFile": "build/online-engine-opt-v2/networks/lc0.nn",
                    "Backend": "blas",
                }
            instances[instance] = row
        self.write("config/allfather.online-hybrid-v2.validation.json", json.dumps({
            "mode": "active", "anchor": "stockfish-anchor", "root": "..",
            "hybrid_authority": {"enabled": True}, "online_time": {"enabled": True},
            "shadow": {"replay_root": "build/replays-online-hybrid-v2"},
            "instances": instances,
        }).encode())
        sources = sorted(
            ("LICENSES.md", "vendor.lock.json", "controller/__init__.py",
             "scripts/release-manifest.py", "scripts/package-online.py",
             "scripts/online-host-preflight.py", "deploy/bin/allfather-online")
        )
        build = json.loads((self.root / "build/online-engine-opt-v2/build-manifest.json").read_text())
        artifacts = sorted(
            [self.entry("build/online-engine-opt-v2/" + item["path"])
             for group in ("engines", "networks") for item in build["artifacts"][group].values()],
            key=lambda x: x["path"])
        self.manifest = {
            "schema_version": 1, "release_kind": "ONLINE-3A-OFFLINE-ONLY",
            "source_commit": "a" * 40, "source_tree": "b" * 40,
            "source_files": [self.entry(p) for p in sources],
            "bundle_artifacts": artifacts,
            "runtime_config": self.entry("config/allfather.online-hybrid-v2.validation.json"),
            "build_manifest": self.entry("build/online-engine-opt-v2/build-manifest.json"),
            "claim_boundary": {
                "uci_launcher": True, "verified_bundle_bytes": True,
                "deployment_host_qualified": False, "network_recovery_qualified": False,
                "public_bot_release": False, "strength_or_elo": False,
            }
        }
        self.write("build/online-release/release-manifest.json", json.dumps(self.manifest).encode())
        self.manifest_path = self.root / "build/online-release/release-manifest.json"

    def write(self, path, content):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    def entry(self, name):
        return seal.info(self.root, name)

    def bundle_entry(self, subpath):
        result = self.entry("build/online-engine-opt-v2/" + subpath)
        result["path"] = subpath
        return result


class SealTests(Fixture):
    def test_fixture_verified_without_git(self):
        record = seal.verify(self.root, self.manifest_path)
        self.assertFalse(record["claim_boundary"]["public_bot_release"])

    def test_binary_code_and_runtime_tampering_fail_closed(self):
        names = ("build/online-engine-opt-v2/bin/stockfish",
                 "controller/__init__.py",
                 "config/allfather.online-hybrid-v2.validation.json")
        for name in names:
            with self.subTest(name=name):
                p = self.root / name
                original = p.read_bytes()
                p.write_bytes(original + b"changed")
                with self.assertRaises(seal.SealError):
                    seal.verify(self.root, self.manifest_path)
                p.write_bytes(original)

    def test_claim_escalation_rejected(self):
        changed = copy.deepcopy(self.manifest)
        changed["claim_boundary"]["public_bot_release"] = True
        self.manifest_path.write_text(json.dumps(changed))
        with self.assertRaises(seal.SealError):
            seal.verify(self.root, self.manifest_path)

    def test_symlinked_artifact_and_missing_net_rejected(self):
        file = self.root / "build/online-engine-opt-v2/networks/lc0.nn"
        original = file.read_bytes()
        file.unlink()
        with self.assertRaises(seal.SealError):
            seal.verify(self.root, self.manifest_path)
        file.symlink_to(self.root / "LICENSES.md")
        with self.assertRaises(seal.SealError):
            seal.verify(self.root, self.manifest_path)
        file.unlink()
        file.write_bytes(original)

    def test_path_traversal_rejected(self):
        for candidate in ("../outside", "/etc/passwd", "controller/../LICENSES.md", r"x\y"):
            with self.subTest(candidate=candidate), self.assertRaises(seal.SealError):
                seal.safe(self.root, candidate)

    def test_deterministic_relocatable_archive(self):
        first_stage = self.root / "dist/stage1"
        first_tar = self.root / "dist/one.tar.gz"
        result = packer.package(self.root, first_stage, first_tar, self.manifest_path)
        self.assertEqual(result["file_count"], len(self.manifest["source_files"]) + 9)
        seal.verify(first_stage, first_stage / "build/online-release/release-manifest.json")
        second_tar = self.root / "dist/two.tar.gz"
        packer.package(self.root, self.root / "dist/stage2", second_tar, self.manifest_path)
        self.assertEqual(seal.sha(first_tar), seal.sha(second_tar))

    def test_launcher_preflight_and_tampered_binary(self):
        cmd = [sys.executable, str(self.root / "deploy/bin/allfather-online"), "--check"]
        passed = subprocess.run(cmd, capture_output=True, text=True, check=False)
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertIn("seal verified", passed.stdout)
        (self.root / "build/online-engine-opt-v2/bin/lc0").write_bytes(b"changed")
        blocked = subprocess.run(cmd, capture_output=True, text=True, check=False)
        self.assertNotEqual(blocked.returncode, 0)
        self.assertEqual(blocked.stdout, "")
        self.assertIn("startup refused", blocked.stderr)

    def test_missing_host_quota_never_qualifies_capacity(self):
        with tempfile.TemporaryDirectory() as d:
            proc = Path(d)
            (proc / "self").mkdir()
            (proc / "self/cgroup").write_text("0::/no-data\n")
            result = preflight.record(proc=proc, cgroups=proc / "cgroupfs")
            self.assertFalse(result["capacity_complete"])
            self.assertIsNone(result["bounded_usable_cpus"])
            self.assertFalse(result["j12_authority_qualified"])

    def test_unrecognised_cgroup_hierarchy_must_remain_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            proc = Path(d)
            (proc / "self").mkdir()
            (proc / "self/cgroup").write_text("2:cpu:/legacy\\n")
            root_cg = proc / "cgroupfs"
            root_cg.mkdir()
            (root_cg / "cpu.max").write_text("800000 100000\\n")
            (root_cg / "memory.max").write_text(str(16 * 1024**3))
            result = preflight.record(proc=proc, cgroups=root_cg)
            self.assertFalse(result["capacity_complete"])
            self.assertIsNone(result["bounded_usable_cpus"])
            self.assertIsNone(result["memory_limit_bytes"])


if __name__ == "__main__":
    unittest.main()

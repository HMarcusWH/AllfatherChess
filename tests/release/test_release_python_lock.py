"""RELEASE-2B-1: pure negative controls for Python source and selected bytes."""
from __future__ import annotations
import hashlib
import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("release_2b1_verifier", ROOT / "scripts/verify-release-python-lock.py")
assert SPEC and SPEC.loader
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


class PythonLockTests(unittest.TestCase):
    def test_real_source_configuration(self):
        selected, builder, pins = verifier.policy(ROOT)
        self.assertEqual(len(pins), 12)
        self.assertEqual(len(selected["wheels"]), 12)
        self.assertFalse(selected["wheel_selection_reviewed"])
        self.assertFalse(builder["builder_reviewed"])

    def test_runtime_lock_replaces_only_chess_sdist(self):
        selected, _builder, pins = verifier.policy(ROOT)
        before = (ROOT / selected["committed_resolution_path"]).read_bytes()
        after = verifier.runtime_lock(ROOT, selected, pins)
        self.assertEqual(after.count(b"chess==1.11.2"), 1)
        self.assertIn(selected["wheels"]["chess-1.11.2-py3-none-any.whl"].encode(), after)
        self.assertNotIn(selected["chess_source_archive_sha256"].encode(), after)
        self.assertNotEqual(before, after)

    def fixture(self, folder: Path):
        paths = ("deploy/lichess/requirements.in",
                 "deploy/lichess/requirements.release.lock",
                 "deploy/release/Dockerfile.wheel-builder",
                 "deploy/release/python-wheel-build-tools.lock",
                 "qualification/release-python-builder-v1.json",
                 "qualification/release-python-wheels-v1.json")
        for rel in paths:
            path = folder / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((ROOT / rel).read_bytes())
        selected = json.loads((folder / paths[-1]).read_text())
        wheels = folder / "wheels"
        wheels.mkdir()
        actual_hashes = {}
        for filename in selected["wheels"]:
            name, version = verifier.wheel_name_parts(filename)
            dist = filename.split("-")[0]
            py, abi, plat = filename[:-4].split("-")[-3:]
            path = wheels / filename
            with zipfile.ZipFile(path, "w") as z:
                z.writestr(f"{dist}-{version}.dist-info/METADATA",
                           f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
                z.writestr(f"{dist}-{version}.dist-info/WHEEL",
                           f"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: {py}-{abi}-{plat.split('.')[0]}\n")
            actual_hashes[filename] = verifier.sha(path)
        selected["wheels"] = actual_hashes
        lock = folder / selected["committed_resolution_path"]
        lines = lock.read_text().splitlines()
        for i, line in enumerate(lines):
            if not line.endswith("\\"):
                continue
            pkg = line.split("==")[0]
            match = [filename for filename in actual_hashes
                     if verifier.wheel_name_parts(filename)[0] == pkg]
            self.assertEqual(len(match), 1)
            if pkg != "chess":
                lines[i + 1] = "    --hash=sha256:" + actual_hashes[match[0]]
        lock.write_text("\n".join(lines) + "\n")
        selected["committed_resolution_sha256"] = verifier.sha(lock)
        (folder / paths[-1]).write_text(json.dumps(selected, indent=2, sort_keys=True) + "\n")
        _selected, _builder, pins = verifier.policy(folder)
        runtime = wheels / "runtime-wheels.lock"
        runtime.write_bytes(verifier.runtime_lock(folder, selected, pins))
        (wheels / "wheel-attestation.json").write_text(json.dumps({
            "schema_version": 1, "source_lock_sha256": selected["committed_resolution_sha256"],
            "builder_policy_sha256": selected["wheel_builder_policy_sha256"],
            "trusted_chess_sdist_sha256": selected["chess_source_archive_sha256"],
            "built_chess_wheel_sha256": actual_hashes["chess-1.11.2-py3-none-any.whl"],
            "runtime_lock_sha256": verifier.sha(runtime), "wheels": actual_hashes,
        }))
        return selected, wheels

    def test_py03_changed_wheel_bytes(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            _selected, wheels = self.fixture(root)
            self.assertEqual(verifier.wheelhouse(root, wheels)["verdict"], "PASS")
            target = wheels / "requests-2.34.2-py3-none-any.whl"
            target.write_bytes(target.read_bytes() + b"tamper")
            with self.assertRaisesRegex(ValueError, "selected bytes"):
                verifier.wheelhouse(root, wheels)

    def test_py01_top_level_changed(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            self.fixture(root)
            req = root / "deploy/lichess/requirements.in"
            req.write_text(req.read_text().replace("requests~=2.34", "requests~=2.35"))
            with self.assertRaisesRegex(ValueError, "input-closure"):
                verifier.policy(root)

    def test_py02_alternative_platform(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            selected, _ = self.fixture(root)
            original = next(x for x in selected["wheels"] if x.startswith("pyyaml-"))
            selected["wheels"][original.replace("cp312-cp312", "cp313-cp313")] = selected["wheels"].pop(original)
            (root / verifier.SELECTED).write_text(json.dumps(selected))
            with self.assertRaisesRegex(ValueError, "unexpected Python ABI"):
                verifier.policy(root)

    def test_py04_rebuilt_chess_output_byte_drift_is_vetoed(self):
        # This is the pure fail-closed admission seam. The separate real
        # GitHub Actions job proves the positive twin-build path.
        spec = importlib.util.spec_from_file_location(
            "release_2b1_prepare",
            ROOT / "scripts/prepare-online3b-wheels.py")
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        with tempfile.TemporaryDirectory() as t:
            victim = Path(t) / "chess-1.11.2-py3-none-any.whl"
            victim.write_bytes(b"altered build with a plausible wheel name")
            expected = verifier.policy(ROOT)[0]["wheels"][victim.name]
            with self.assertRaisesRegex(ValueError, "unapproved dependency bytes"):
                builder.ensure_hash(victim, expected)

    def test_py05_sdist_mismatch(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            selected, _ = self.fixture(root)
            selected["chess_source_archive_sha256"] = "a" * 64
            (root / verifier.SELECTED).write_text(json.dumps(selected))
            with self.assertRaisesRegex(ValueError, "chess source"):
                verifier.policy(root)

    def test_py06_toolchain_change(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            self.fixture(root)
            tool = root / "deploy/release/Dockerfile.wheel-builder"
            tool.write_text(tool.read_text() + "# unexpected\n")
            with self.assertRaisesRegex(ValueError, "builder toolchain"):
                verifier.policy(root)

    def test_py07_installed_pip_and_extra(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            selected, wheels = self.fixture(root)
            rows = [f"{verifier.wheel_name_parts(n)[0]}=={verifier.wheel_name_parts(n)[1]}"
                    for n in selected["wheels"]]
            installed = root / "pip-freeze.txt"
            installed.write_text("\n".join(sorted(rows + ["pip==24.0"])) + "\n")
            self.assertEqual(verifier.wheelhouse(root, wheels, installed)["verdict"], "PASS")
            for wrong in ("pip==25.2", "pip==24.0\nextra==1.0"):
                installed.write_text("\n".join(sorted(rows + [wrong])) + "\n")
                with self.assertRaises(ValueError):
                    verifier.wheelhouse(root, wheels, installed)

    def test_py08_metadata_wrong_even_when_not_comparing_bytes(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            _selected, wheels = self.fixture(root)
            name = "requests-2.34.2-py3-none-any.whl"
            path = wheels / name
            with zipfile.ZipFile(path, "w") as z:
                z.writestr("requests-2.34.2.dist-info/METADATA",
                           "Name: wrong\nVersion: 2.34.2\n")
                z.writestr("requests-2.34.2.dist-info/WHEEL", "Tag: py3-none-any\n")
            with self.assertRaisesRegex(ValueError, "METADATA mismatch"):
                verifier.check_zip(path, name, "requests", "2.34.2")

    def test_py09_extra_and_symlinked_wheelhouse_objects(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            _, wheels = self.fixture(root)
            for name in ("extra.pth", "injected.py", "extra.whl", "nested"):
                path = wheels / name
                if name == "nested":
                    path.mkdir()
                else:
                    path.write_bytes(b"x")
                with self.assertRaisesRegex(ValueError, "wheelhouse unexpected"):
                    verifier.wheelhouse(root, wheels)
                if path.is_dir():
                    path.rmdir()
                else:
                    path.unlink()
            link = wheels / "linked.pth"
            link.symlink_to(root / verifier.SELECTED)
            with self.assertRaisesRegex(ValueError, "wheelhouse unexpected"):
                verifier.wheelhouse(root, wheels)

    def test_source_workflows_have_no_dynamic_resolvers(self):
        for name in ("release-candidate.yml", "online-bridge.yml",
                     "online-recovery.yml", "online-recovery-v2.yml"):
            with self.subTest(name=name):
                raw = (ROOT / ".github/workflows" / name).read_text()
                self.assertNotIn("piptools compile", raw)
                self.assertNotIn("pip-compile", raw)
                self.assertIn("requirements.release.lock", raw)


if __name__ == "__main__":
    unittest.main()

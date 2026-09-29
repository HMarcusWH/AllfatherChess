#!/usr/bin/env python3
"""Contract tests for runtime-substrate qualification identity."""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from controller.runtime_substrate import (
    RuntimeSubstrate,
    capture_runtime_substrate,
)


PACKAGES = {
    "libopenblas-dev": "0.3.26",
    "libopenblas0-pthread": "0.3.26",
    "libstdc++6": "13.3",
    "libgcc-s1": "13.3",
    "libc6": "2.39",
}


def package_reader(name: str) -> str | None:
    return PACKAGES.get(name)


class RuntimeSubstrateTests(unittest.TestCase):
    def test_capture_round_trip_and_stable_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lib = root / "libopenblas.so.0"
            lib.write_bytes(b"openblas")
            binary = root / "lc0"
            binary.write_bytes(b"binary")
            substrate = capture_runtime_substrate(
                binary,
                os_release_reader=lambda: {"ID": "ubuntu", "VERSION_ID": "24.04"},
                package_reader=package_reader,
                ldd_reader=lambda _: f"libopenblas.so.0 => {lib} (0x1)",
            )
        self.assertTrue(substrate.complete)
        self.assertIsNotNone(substrate.runtime_substrate_id)
        restored = RuntimeSubstrate.from_dict(substrate.as_dict())
        self.assertEqual(restored, substrate)
        self.assertEqual(restored.digest, substrate.digest)

    def test_linked_library_change_changes_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lib = root / "libopenblas.so.0"
            binary = root / "lc0"
            binary.write_bytes(b"binary")
            lib.write_bytes(b"v1")
            first = capture_runtime_substrate(
                binary,
                os_release_reader=lambda: {"ID": "ubuntu", "VERSION_ID": "24.04"},
                package_reader=package_reader,
                ldd_reader=lambda _: f"libopenblas.so.0 => {lib} (0x1)",
            )
            lib.write_bytes(b"v2")
            second = capture_runtime_substrate(
                binary,
                os_release_reader=lambda: {"ID": "ubuntu", "VERSION_ID": "24.04"},
                package_reader=package_reader,
                ldd_reader=lambda _: f"libopenblas.so.0 => {lib} (0x1)",
            )
        self.assertNotEqual(first.digest, second.digest)

    def test_missing_required_package_fails_closed(self):
        def missing(name: str) -> str | None:
            if name == "libopenblas0-pthread":
                return None
            return PACKAGES.get(name)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lib = root / "libopenblas.so.0"
            lib.write_bytes(b"openblas")
            binary = root / "lc0"
            binary.write_bytes(b"binary")
            substrate = capture_runtime_substrate(
                binary,
                os_release_reader=lambda: {"ID": "ubuntu", "VERSION_ID": "24.04"},
                package_reader=missing,
                ldd_reader=lambda _: f"libopenblas.so.0 => {lib} (0x1)",
            )
        self.assertFalse(substrate.complete)
        self.assertIsNone(substrate.runtime_substrate_id)
        self.assertTrue(any("libopenblas0-pthread" in fault for fault in substrate.faults))


if __name__ == "__main__":
    unittest.main()

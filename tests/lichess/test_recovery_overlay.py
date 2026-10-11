"""Pinned, byte-exact recovery overlay cannot mutate the upstream checkout."""
from __future__ import annotations
import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path

from deploy.lichess import recovery_overlay

ROOT = Path(__file__).resolve().parents[2]
UPSTREAM = ROOT / "build/vendor/lichess-bot"


class RecoveryOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (UPSTREAM / ".git").exists():
            raise RuntimeError("ONLINE-4B overlay tests require the pinned upstream checkout")

    def test_patches_only_a_temporary_copy_and_binds_original_source(self):
        with tempfile.TemporaryDirectory() as temp:
            stage = Path(temp) / "bridge"
            for rel in recovery_overlay.UPSTREAM:
                target = stage / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(UPSTREAM / rel, target)
            raw_before = {p: (UPSTREAM / p).read_bytes()
                          for p in recovery_overlay.UPSTREAM}
            report = recovery_overlay.apply(stage)
            self.assertEqual(report["mode"], "OFFLINE-4B-ONLY")
            self.assertFalse(report["public_bot_release"])
            self.assertEqual(set(report["patched_upstream"]), set(raw_before))
            for path, data in raw_before.items():
                self.assertEqual((UPSTREAM / path).read_bytes(), data)
                self.assertNotEqual((stage / path).read_bytes(), data)
                self.assertEqual(report["patched_upstream"][path]["git_blob_sha1"],
                                 recovery_overlay.git_blob_id(data))
            self.assertTrue((stage / "deploy/lichess/guarded_transport.py").is_file())
            with self.assertRaises(ValueError):
                recovery_overlay.apply(stage)

    def test_rejects_unknown_upstream_modification_before_any_patch(self):
        with tempfile.TemporaryDirectory() as temp:
            stage = Path(temp) / "bridge"
            for rel in recovery_overlay.UPSTREAM:
                target = stage / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(UPSTREAM / rel, target)
            corrupt = stage / "lib/lichess.py"
            corrupt.write_bytes(corrupt.read_bytes() + b"\n# modified before patch\n")
            preserved = (stage / "lib/lichess_bot.py").read_bytes()
            with self.assertRaisesRegex(ValueError, "source hash drift"):
                recovery_overlay.apply(stage)
            self.assertEqual((stage / "lib/lichess_bot.py").read_bytes(), preserved)
            self.assertFalse((stage / "deploy/lichess/guarded_transport.py").exists())


if __name__ == "__main__":
    unittest.main()

"""Mount-identity, noexec and private staged executable negative controls."""
from __future__ import annotations

import unittest
from unittest import mock
from deploy.lichess import canary_staging as cs


def mountinfo(path=None, flags="rw,nosuid,nodev", superflags="rw,size=1048576"):
    path = str(cs.STAGE_ROOT) if path is None else path
    return "101 22 0:99 / " + path + " " + flags + " - tmpfs tmpfs " + superflags + "\n"


class StageMountContract(unittest.TestCase):
    def test_exact_mount_identity_and_flags(self):
        filesystem, flags = cs.read_stage_mount_options(mountinfo())
        self.assertEqual(filesystem, "tmpfs")
        self.assertTrue({"rw", "nosuid", "nodev"}.issubset(flags))
        self.assertNotIn("noexec", flags)

    def test_noexec_on_either_side_of_mountinfo_is_visible(self):
        for source in (mountinfo(flags="rw,nosuid,nodev,noexec"),
                       mountinfo(superflags="rw,noexec,size=1048576")):
            _, flags = cs.read_stage_mount_options(source)
            self.assertIn("noexec", flags)

    def test_wrong_missing_duplicated_mount_denied(self):
        for raw in ("", mountinfo(path="/tmp"), mountinfo()*2, "not mountinfo"):
            with self.subTest(raw=raw[:45]):
                with self.assertRaises(cs.StageMountDenied):
                    cs.read_stage_mount_options(raw)

    def test_executable_scratch_is_dedicated_not_general_tmp(self):
        self.assertEqual(cs.STAGE_SESSION.parent, cs.STAGE_ROOT)
        self.assertIn("exec,nosuid,nodev", cs.STAGE_TMPFS)
        self.assertIn("uid=10001,gid=10001", cs.STAGE_TMPFS)
        self.assertIn("mode=0700", cs.STAGE_TMPFS)
        self.assertNotIn("/tmp:", cs.STAGE_TMPFS)

    def test_unavailable_mountinfo_denies_without_creating_workspace(self):
        with mock.patch.object(cs.Path, "read_text", side_effect=OSError("fixture")):
            with self.assertRaises(cs.StageMountDenied):
                cs.require_private_stage_mount()


if __name__ == "__main__":
    unittest.main()

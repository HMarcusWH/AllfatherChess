"""Bounded, non-authorizing runner context capture."""
import tempfile
import unittest
from pathlib import Path
from tools.strength_lab.g3_host_io_context import observe, _io_pressure, _read


class HostIOContextTests(unittest.TestCase):
    def test_observe_is_never_a_qualification(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t)
            (p / "pressure").write_text("some avg10=0.01 avg60=0.02 avg300=0.03 total=123\nfull avg10=0.0 avg60=0.0 avg300=0.0 total=0\n")
            (p / "cgroup.controllers").write_text("cpu memory io")
            (p / "memory.max").write_text("6291456")
            d = observe(phase="before", source_commit="a" * 40, root=p,
                        pressure_path=p / "pressure", cgroup_path=p)
            self.assertFalse(d["qualifies"])
            self.assertTrue(d["diagnostic_only"])
            self.assertEqual(d["io_pressure"]["some"]["total"], 123)
            self.assertTrue(d["cgroup_v2_controllers_present"])
            self.assertEqual(d["cgroup_memory_max"], "6291456")

    def test_malformed_identity_or_phase_denied(self):
        with tempfile.TemporaryDirectory() as t:
            for phase, sha in (("during", "a" * 40), ("after", "not-a-sha")):
                with self.subTest(phase=phase), self.assertRaises(ValueError):
                    observe(phase=phase, source_commit=sha, root=Path(t))

    def test_missing_and_linked_host_files_are_not_fabricated(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            self.assertIsNone(_read(root / "missing"))
            source = root / "some"
            source.write_text("some avg10=2 avg60=3 avg300=4 total=5\n")
            link = root / "link"
            link.symlink_to(source)
            self.assertIsNone(_read(link))
            self.assertIsNone(_io_pressure(link))
            (root / "too-big").write_text("x" * 100)
            self.assertIsNone(_read(root / "too-big", limit=20))


if __name__ == "__main__":
    unittest.main()

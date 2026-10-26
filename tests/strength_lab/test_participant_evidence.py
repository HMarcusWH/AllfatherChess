"""S1 independent receipt validator rejects missing/forged participant work."""
from __future__ import annotations

import copy
from pathlib import Path
import tempfile
import unittest

from tools.local_game.common import QualificationError, load, save, sha
from tools.strength_lab.participant_evidence import verify_resource_receipts


def fixture(root: Path, arm: str, *, suffix: str = "session-1") -> Path:
    session = root / "sessions" / arm / suffix
    session.mkdir(parents=True, exist_ok=True)
    save(session / "session.json", {
        "status": "completed", "returncode": 0, "arm": arm, "session_id": suffix,
        "errors": [], "leaked_before_cleanup": {}, "remaining_after_cleanup": {},
    })
    scope_id = "a" * 32 if arm == "allfather-g3" else "b" * 32
    receipt = root / "participant_resources" / arm / (scope_id + ".json")
    save(receipt, {
        "schema_version": 1,
        "accounting": "strict_cgroup_v2_child_tree_plus_launcher",
        "status": "MEASURED", "scope_id": scope_id, "arm": arm,
        "session_id": suffix, "session_sha256": sha(session / "session.json"),
        "proxy_pid": 1234, "proxy_identity": {
            "pid": 1234, "pgid": 1234, "start_ticks": 1240},
        "proxy_returncode": 0,
        "started_ns": 100, "ended_ns": 100000, "observed_leak_before_cleanup": False,
        "errors": [], "cpu_ms": 31.25, "cgroup_child_tree_cpu_ms": 30.0,
        "launcher_self_cpu_ms": 1.25,
        "cgroup_memory_peak_bytes": 500000,
        "launcher_peak_rss_kib": 100,
        "participant_peak_memory_upper_bound_bytes": 602400,
        "peak_is_exact": False, "same_compute_claim": False,
        "strength_qualified": False,
        "measurement_boundary": "through post-proxy cleanup / pre-receipt seal",
    })
    return receipt


class ResourceEvidenceTests(unittest.TestCase):
    def test_valid_two_arm_join(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fixture(root, "allfather-g3")
            fixture(root, "allfather-g3-anchor-control")
            record = verify_resource_receipts(
                root, ("allfather-g3", "allfather-g3-anchor-control"))
            self.assertTrue(record["all_measured"])
            self.assertFalse(record["equal_compute"])
            self.assertFalse(record["game_result_qualified"])
            self.assertEqual(record["arms"]["allfather-g3"]["cpu_ms"], 31.25)

    def test_reject_missing_corrupt_or_wrong_occurrence(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            receipt = fixture(root, "allfather-g3")
            good = load(receipt)
            mutators = [
                lambda r: r.update(status="UNAVAILABLE"),
                lambda r: r.update(status="FAILED"),
                lambda r: r.update(cpu_ms=0),
                lambda r: r.update(cpu_ms=None),
                lambda r: r.update(cgroup_child_tree_cpu_ms=10),
                lambda r: r.update(observed_leak_before_cleanup=True),
                lambda r: r.update(errors=["live child group"]),
                lambda r: r.update(session_id="another-game"),
                lambda r: r.update(session_sha256="0" * 64),
                lambda r: r.update(proxy_pid=1235),
                lambda r: r.update(peak_is_exact=True),
                lambda r: r.update(same_compute_claim=True),
                lambda r: r.update(strength_qualified=True),
                lambda r: r.update(participant_peak_memory_upper_bound_bytes=10),
                lambda r: r.update(measurement_boundary="unknown"),
                lambda r: r.update(secret_unapproved_field=True),
                lambda r: r.update(started_ns=100001),
            ]
            for mutation in mutators:
                changed = copy.deepcopy(good)
                mutation(changed)
                save(receipt, changed)
                with self.subTest(mutation=mutation):
                    with self.assertRaises(QualificationError):
                        verify_resource_receipts(root, ("allfather-g3",))
            save(receipt, good)
            (root / "sessions" / "allfather-g3" / "session-2").mkdir()
            save(root / "sessions" / "allfather-g3" / "session-2"
                 / "session.json", {
                     "status": "completed", "returncode": 0,
                     "arm": "allfather-g3", "session_id": "session-2",
                     "errors": [], "leaked_before_cleanup": {},
                     "remaining_after_cleanup": {},
                 })
            with self.assertRaises(QualificationError):
                verify_resource_receipts(root, ("allfather-g3",))

    def test_missing_receipt_and_unexpected_file_fail(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            receipt = fixture(root, "allfather-g3")
            other = receipt.with_name("extraneous.txt")
            other.write_text("not a participant receipt")
            with self.assertRaises(QualificationError):
                verify_resource_receipts(root, ("allfather-g3",))
            other.unlink()
            receipt.unlink()
            with self.assertRaises(QualificationError):
                verify_resource_receipts(root, ("allfather-g3",))

    def test_undeclared_participant_arm_is_not_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fixture(root, "allfather-g3")
            (root / "participant_resources" / "unapproved").mkdir()
            with self.assertRaises(QualificationError):
                verify_resource_receipts(root, ("allfather-g3",))

    def test_tampered_session_hash_is_fatal(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fixture(root, "allfather-g3")
            target = root / "sessions/allfather-g3/session-1/session.json"
            document = load(target)
            document["status"] = "failed"
            save(target, document)
            with self.assertRaises(QualificationError):
                verify_resource_receipts(root, ("allfather-g3",))


if __name__ == "__main__":
    unittest.main()

"""Synthetic destructive tests for forensic-only G3 durability inspection."""
from __future__ import annotations

from copy import deepcopy
import io
import json
from pathlib import Path
import stat
import tempfile
import unittest
import warnings
import zipfile

from tools.strength_lab.g3_io_forensics import ForensicError, inspect

SHA = "a" * 40
CAMPAIGN = "aaaaaaaaaaaa-11111111111-1"
RUN = "20261026T171358480563Z-g000131-10a3472e"
PREFIX = (f"{CAMPAIGN}/g3-pilot-02/sessions/allfather-g3/"
          f"session001/replays/{RUN}")


def case(*, delayed=False, durable=None, full_claim=None):
    if durable is None:
        durable = 539.635 if delayed else 0.8
    freeze = 1164.4 if delayed else 100.0
    serialization = .409 if delayed else .1
    persisted = round(freeze + serialization + durable, 3)
    elapsed = round(persisted + .144, 3)
    limit = 1512.0
    wall = elapsed <= limit
    if full_claim is None:
        full_claim = wall
    manifest = {
        "run_id": RUN,
        "clock_outcome": {
            "output_within_deadline": True,
            "emitted_ms": 1162.977 if delayed else 80.0,
        },
        "notes": [
            f"process endpoints frozen at {round(freeze-.245,3):.3f}ms from external go (non-authorizing)",
            "resource seal phases (non-authorizing): "
            f"freeze_wall_ms={freeze:.3f}, serialization_ms={serialization:.3f}, "
            f"durable_write_ms={durable:.3f}, persisted_wall_ms={persisted:.3f}",
        ],
    }
    route = {
        "run_id": RUN,
        "budget": {"elapsed_ms": elapsed},
        "envelope": {"wall_ms": limit},
        "envelope_claim": {"wall_within_envelope": wall, "claimed": full_claim},
    }
    resource = {"run_id": RUN, "qualified": True, "physical_cpu_ms": 1308.275}
    campaign = {"source": {"commit": SHA, "tree": "b" * 40},
                "status": "completed", "jobs": [
                    {"execution": {"returncode": 0, "timed_out": False}}
                    for _ in range(8)]}
    return {f"{CAMPAIGN}/manifest.json": campaign,
            f"{PREFIX}/manifest.json": manifest,
            f"{PREFIX}/route.json": route,
            f"{PREFIX}/resource.json": resource}


def archive(path: Path, entries: dict, *, extras=()):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for key, payload in entries.items():
            if isinstance(payload, dict):
                payload = json.dumps(payload, sort_keys=True)
            z.writestr(key, payload)
        for key, payload in extras:
            z.writestr(key, payload)


class ForensicContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.zip = Path(self.temp.name) / "campaign.zip"

    def inspect(self, entries, *, extras=()):
        archive(self.zip, entries, extras=extras)
        return inspect(self.zip, expected_source_commit=SHA,
                       expected_campaign=CAMPAIGN)

    def test_normal_io_is_descriptive_never_qualifying(self):
        r = self.inspect(case())
        self.assertFalse(r["qualifies"])
        self.assertTrue(r["diagnostic_only"])
        self.assertIsNone(r["qualified_games"])
        self.assertEqual(r["scheduled_blocks_exited_zero_observed"], 8)
        self.assertEqual(r["wall_claim_false"], 0)
        self.assertEqual(r["durable_write_ms"]["max_ms"], .8)

    def test_539ms_post_output_fsync_cannot_be_promoted(self):
        r = self.inspect(case(delayed=True))
        self.assertFalse(r["qualifies"])
        self.assertEqual(r["wall_claim_false"], 1)
        self.assertEqual(r["denied_replays"][0]["replay_id"], RUN)
        self.assertTrue(r["denied_replays"][0]["outward_before_deadline"])
        self.assertTrue(r["denied_replays"][0]["physical_cpu_qualified"])
        self.assertFalse(r["denied_replays"][0]["wall_within_envelope"])
        self.assertGreater(r["denied_replays"][0]["terminal_wall_ms"], 1512)

    def test_previous_677ms_tail_also_rejected(self):
        d = case(delayed=True, durable=677.229)
        r = self.inspect(d)
        self.assertEqual(r["wall_claim_false"], 1)
        self.assertGreater(r["durable_write_ms"]["max_ms"], 600)

    def test_zero_file_or_corrupt_json_rejected(self):
        for field in (f"{PREFIX}/manifest.json", f"{PREFIX}/route.json",
                      f"{PREFIX}/resource.json"):
            with self.subTest(field=field):
                docs = case()
                del docs[field]
                with self.assertRaises(ForensicError):
                    self.inspect(docs)
        docs = case()
        docs[f"{PREFIX}/route.json"] = '{ "run_id": '
        with self.assertRaises(ForensicError):
            self.inspect(docs)

    def test_duplicate_json_keys_fails_closed(self):
        docs = case()
        docs[f"{PREFIX}/resource.json"] = (
            f'{{"run_id":"{RUN}","run_id":"{RUN}","qualified":true}}'
        )
        with self.assertRaises(ForensicError):
            self.inspect(docs)

    def test_forged_wall_and_claim_bits_rejected(self):
        docs = case(delayed=True)
        docs[f"{PREFIX}/route.json"]["envelope_claim"]["claimed"] = True
        with self.assertRaises(ForensicError):
            self.inspect(docs)
        docs = case(delayed=True)
        docs[f"{PREFIX}/route.json"]["envelope_claim"]["wall_within_envelope"] = True
        with self.assertRaises(ForensicError):
            self.inspect(docs)

    def test_source_and_run_id_tampering_fails(self):
        for key in [f"{PREFIX}/manifest.json", f"{PREFIX}/route.json",
                    f"{PREFIX}/resource.json"]:
            docs = case()
            docs[key]["run_id"] = "forged"
            with self.subTest(key=key), self.assertRaises(ForensicError):
                self.inspect(docs)
        archive(self.zip, case())
        with self.assertRaises(ForensicError):
            inspect(self.zip, expected_source_commit="c" * 40)

    def test_duration_arithmetic_must_remain_consistent(self):
        docs = case(delayed=True)
        docs[f"{PREFIX}/manifest.json"]["notes"][-1] = (
            "resource seal phases (non-authorizing): freeze_wall_ms=1164.4, "
            "serialization_ms=0.409, durable_write_ms=3.0, persisted_wall_ms=1704.444")
        with self.assertRaises(ForensicError):
            self.inspect(docs)

    def test_duplicate_archive_members_symlinks_and_escape_rejected(self):
        docs = case()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with self.assertRaises(ForensicError):
                self.inspect(docs, extras=[(f"{PREFIX}/resource.json", "{}")])
        with self.assertRaises(ForensicError):
            self.inspect(docs, extras=[("../escape.json", "{}")])
        link = zipfile.ZipInfo(f"{CAMPAIGN}/danger")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        with self.assertRaises(ForensicError):
            self.inspect(docs, extras=[(link, "../escape")])

    def test_archive_hash_binding_and_directory_mode(self):
        docs = case()
        archive(self.zip, docs)
        with self.assertRaises(ForensicError):
            inspect(self.zip, expected_sha256="0" * 64)
        root = Path(self.temp.name) / CAMPAIGN
        for name, payload in docs.items():
            dst = Path(self.temp.name) / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(json.dumps(payload), encoding="utf-8")
        r = inspect(root, expected_source_commit=SHA)
        self.assertEqual(r["g3_replays_observed"], 1)
        self.assertIsNone(r["archive_sha256"])
        symlink = root / "out-of-tree"
        symlink.symlink_to(self.zip)
        with self.assertRaises(ForensicError):
            inspect(root)

    def test_duplicate_occurrence_rejected_even_if_same_run_id(self):
        docs = case()
        other = PREFIX.replace("/session001/", "/session002/")
        docs.update({key.replace(PREFIX, other): deepcopy(value)
                     for key, value in list(docs.items()) if key.startswith(PREFIX)})
        with self.assertRaises(ForensicError):
            self.inspect(docs)

    def test_archived_evidence_cannot_be_reported_as_game_results(self):
        r = self.inspect(case(delayed=True))
        for denied in ("qualifies", "strength_qualified", "live_game_authorized",
                       "release_authorized"):
            self.assertIs(r[denied], False)
        self.assertNotIn("scores", r)
        self.assertNotIn("real_chess_games_verified", r)


if __name__ == "__main__":
    unittest.main()

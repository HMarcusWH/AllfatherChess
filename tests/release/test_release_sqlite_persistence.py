"""Real SQLite/WAL durability evidence tests for RELEASE-1 host qualification.

Synthetic documents are paired with real MoveJournal-schema SQLite databases.
A JSON-only fixture can exercise verify_smoke but must NEVER satisfy verify_file.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from deploy.lichess.move_journal import SCHEMA

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "release_smoke_test_fixture", ROOT / "tests/release/test_release_smoke_evidence.py")
original = importlib.util.module_from_spec(spec)
spec.loader.exec_module(original)
fixture = original.fixture
qual = original.qual


def real_fixture(folder: Path, key: str, *, keep_wal=False, doc=None):
    """Return an optionally live WAL writer and its complete JSON document."""
    doc = fixture(key) if doc is None else doc
    folder.mkdir(parents=True)
    state = folder / "state"
    state.mkdir()
    db = state / "move-journal.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    for statement in SCHEMA:
        conn.execute(statement)
    conn.commit()
    # Materialize only the schema before inserting application data into WAL.
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    journal = doc["journal"]
    conn.execute("INSERT INTO owner(singleton,epoch) VALUES(1,?)",
                 (journal["owner_epoch"],))
    for table in ("games", "intents", "cooldown", "events"):
        for record in journal[table]:
            columns = list(record)
            query = ("INSERT INTO " + table + " (" + ",".join(columns) +
                     ") VALUES (" + ",".join("?" for _ in columns) + ")")
            conn.execute(query, tuple(record[key] for key in columns))
    conn.commit()
    (folder / "result.json").write_text(json.dumps(doc, sort_keys=True))
    if not keep_wal:
        conn.close()
        conn = None
    return doc, conn


class RealSQLiteQualificationTests(unittest.TestCase):
    def test_every_valid_case_qualifies_with_real_checkpointed_database(self):
        for key in qual.CASES:
            with self.subTest(key=key), tempfile.TemporaryDirectory() as directory:
                folder = Path(directory) / key
                real_fixture(folder, key)
                report = qual.verify_file(folder / "result.json", key)
                self.assertTrue(report["persisted_sqlite_verified"])
                self.assertTrue(report["independent_qualified"])

    def test_uncheckpointed_wal_rows_are_verified_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "r02-white"
            _, conn = real_fixture(folder, "r02-white", keep_wal=True)
            try:
                self.assertTrue((folder / "state/move-journal.sqlite3-wal").is_file())
                report = qual.verify_file(folder / "result.json", "r02-white")
                self.assertTrue(report["persisted_sqlite_verified"])
            finally:
                conn.close()

    def test_qualifier_never_writes_original_read_only_producer_volume(self):
        """Reproduce GitHub runner reading the UID-10001-owned mount."""
        for keep_wal in (False, True):
            with self.subTest(live_wal=keep_wal), tempfile.TemporaryDirectory() as d:
                folder = Path(d) / "r02-white"
                _, conn = real_fixture(folder, "r02-white", keep_wal=keep_wal)
                state = folder / "state"
                database = state / "move-journal.sqlite3"
                wal = state / "move-journal.sqlite3-wal"
                originals = [database] + ([wal] if keep_wal else [])
                before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in originals}
                previous_modes = {p: p.stat().st_mode & 0o777 for p in originals}
                try:
                    for path in originals:
                        os.chmod(path, 0o444)
                    os.chmod(state, 0o555)
                    result = qual.verify_file(folder / "result.json", "r02-white")
                    self.assertTrue(result["persisted_sqlite_verified"])
                    self.assertEqual(before, {
                        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in originals})
                    self.assertEqual(
                        (state / "move-journal.sqlite3-shm").exists(), keep_wal)
                finally:
                    os.chmod(state, 0o700)
                    for path, mode in previous_modes.items():
                        if path.exists():
                            os.chmod(path, mode)
                    if conn is not None:
                        conn.close()

    def test_qualifier_rejects_mutated_source_without_json_change(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / "r10-white"
            real_fixture(folder, "r10-white")
            with sqlite3.connect(folder / "state/move-journal.sqlite3") as c:
                c.execute("UPDATE cooldown SET cause='http429:other'")
            with self.assertRaisesRegex(ValueError, "SQLite persisted cooldown"):
                qual.verify_file(folder / "result.json", "r10-white")

    def test_missing_required_wal_refuses_unpersisted_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "r02-white"
            _, conn = real_fixture(folder, "r02-white", keep_wal=True)
            try:
                (folder / "state/move-journal.sqlite3-wal").unlink()
                with self.assertRaises((ValueError, sqlite3.DatabaseError)):
                    qual.verify_file(folder / "result.json", "r02-white")
            finally:
                conn.close()

    def test_missing_corrupt_and_linked_database_are_rejected(self):
        for mutation in ("missing", "corrupt", "db-link", "directory-link",
                         "wal-link"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as d:
                folder = Path(d) / "r00-white"
                real_fixture(folder, "r00-white")
                state = folder / "state"
                db = state / "move-journal.sqlite3"
                if mutation == "missing":
                    db.unlink()
                elif mutation == "corrupt":
                    db.write_bytes(b"not a sqlite database")
                elif mutation == "db-link":
                    original_db = folder / "original.sqlite3"
                    db.rename(original_db)
                    db.symlink_to(original_db)
                elif mutation == "directory-link":
                    moved = folder / "actual-state"
                    state.rename(moved)
                    state.symlink_to(moved, target_is_directory=True)
                else:
                    (state / "move-journal.sqlite3-wal").symlink_to(db)
                with self.assertRaises((ValueError, sqlite3.DatabaseError)):
                    qual.verify_file(folder / "result.json", "r00-white")

    def test_forged_snapshot_rows_and_owner_are_rejected(self):
        for mutation in ("owner", "game", "intent", "cooldown"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as d:
                key = "r10-white" if mutation == "cooldown" else "r02-white"
                folder = Path(d) / key
                doc, _ = real_fixture(folder, key)
                if mutation == "owner":
                    doc["journal"]["owner_epoch"] = "c"*32
                elif mutation == "game":
                    doc["journal"]["games"][0]["history_json"] = "[]"
                elif mutation == "intent":
                    doc["journal"]["intents"][0]["status"] = "PREPARED"
                else:
                    doc["journal"]["cooldown"].clear()
                (folder / "result.json").write_text(json.dumps(doc))
                with self.assertRaisesRegex(ValueError, "SQLite"):
                    qual.verify_file(folder / "result.json", key)

    def test_disk_mutation_is_detected_without_json_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "r00-white"
            real_fixture(folder, "r00-white")
            with sqlite3.connect(folder / "state/move-journal.sqlite3") as conn:
                conn.execute("UPDATE owner SET epoch=?", ("d"*32,))
            with self.assertRaisesRegex(ValueError, "SQLite owner"):
                qual.verify_file(folder / "result.json", "r00-white")

    def test_qualification_fails_closed_for_missing_or_corrupt_case(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for key in qual.CASES:
                real_fixture(root / key, key)
            self.assertTrue(qual.qualify_directory(root)["offline_smoke_qualified"])
            (root / "r10-white/state/move-journal.sqlite3").unlink()
            report = qual.qualify_directory(root)
            self.assertFalse(report["offline_smoke_qualified"])
            self.assertEqual(len(report["failures"]), 1)
            self.assertEqual(report["failures"][0]["scenario"], "r10-white")


if __name__ == "__main__":
    unittest.main()

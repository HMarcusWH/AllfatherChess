#!/usr/bin/env python3
"""Shadow execution tests: four roles, exact dispatch, authority firewall."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from controller.runtime import BackendManager, RuntimeError, load_runtime_config
from controller.shadow import partition_roots
from controller.shards import ShardLedgerError
from tests.harness.uci_session import UciSession


FAKE = ROOT / "tests" / "fixtures" / "fake_uci_engine.py"

ANCHOR = "stockfish-anchor"
SHADOWS = ("stockfish-shadow", "reckless-shadow", "lc0-shadow")
FAMILY = {
    "stockfish-anchor": "stockfish",
    "stockfish-shadow": "stockfish",
    "reckless-shadow": "reckless",
    "lc0-shadow": "lc0",
}


def write_shadow_config(
    directory: Path,
    *,
    mode: str = "shadow",
    roots: str = "e2e4,d2d4,g1f3,b1c3,c2c4,g2g3",
    instance_args: dict[str, list[str]] | None = None,
    dispatch_nodes: int = 120,
    extra: dict | None = None,
    tag: str = "allfather-test",
) -> Path:
    instance_args = instance_args or {}
    instances = {}
    for name in (ANCHOR, *SHADOWS):
        args = [str(FAKE), "--name", f"Fake-{name}", "--roots", roots, "--tag", tag]
        if FAMILY[name] == "lc0":
            # LC0 omits the multipv token for its primary line at MultiPV=1.
            args += ["--omit-multipv-token"]
        if name == ANCHOR:
            # A real anchor search is not instantaneous; the fake must overlap
            # shadow qualification the way a real one does.
            args += ["--info-lines", "4", "--info-delay-ms", "25"]
        args += instance_args.get(name, [])
        instances[name] = {
            "family": FAMILY[name],
            "role": "anchor" if name == ANCHOR else "shadow",
            "binary": sys.executable,
            "cwd": ".",
            "args": args,
            "options": {"UCI_Chess960": False},
        }

    document = {
        "schema_version": 2,
        "mode": mode,
        "anchor": ANCHOR,
        "root": ".",
        "shadow": {
            "owners": ["stockfish", "reckless", "lc0"],
            "instance_by_owner": {
                "stockfish": "stockfish-shadow",
                "reckless": "reckless-shadow",
                "lc0": "lc0-shadow",
            },
            "oracle": "stockfish-shadow",
            "partition": "root_index_modulo",
            "dispatch_limit": {"nodes": dispatch_nodes},
            "lc0_score_type": "centipawn",
            "replay_root": "replays",
            "oracle_timeout_s": 5.0,
            "drain_timeout_s": 3.0,
            "on_anchor_complete": "drain",
        },
        "instances": instances,
    }
    if extra:
        document.update(extra)
    path = directory / "shadow.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def load_shipped_config(path: Path, directory: Path):
    """Load a shipped config's real structure without requiring built engines.

    The fast controller suite must pass on a checkout where no engine has been
    built, so only the `binary` paths are redirected to the running interpreter.
    Schema version, mode, anchor, roles, families, options, and shadow/routing
    settings all remain the shipped document's own.
    """
    document = json.loads(path.read_text(encoding="utf-8"))
    for section in ("backends", "instances"):
        for spec in document.get(section, {}).values():
            spec["binary"] = sys.executable
            spec.pop("fallback_glob", None)
    document["root"] = "."
    target = directory / path.name
    target.write_text(json.dumps(document), encoding="utf-8")
    return load_runtime_config(target)


def read_only_manifest(replay_root: Path) -> dict:
    runs = sorted(p for p in replay_root.iterdir() if p.is_dir())
    if len(runs) != 1:
        raise AssertionError(f"expected exactly one replay run, found {[p.name for p in runs]}")
    return json.loads((runs[0] / "manifest.json").read_text(encoding="utf-8"))


def run_shell(config: Path, script: list[str], *, timeout: float = 20.0) -> list[str]:
    """Drive the external shell through one deterministic command script."""
    with UciSession(
        Path(sys.executable),
        cwd=ROOT,
        timeout=timeout,
        args=["-m", "controller", "--config", str(config)],
    ) as session:
        session.configure({"UCI_Chess960": False})
        session.new_game()
        session.set_position({"startpos_moves": []})
        collected: list[str] = list(session.transcript)
        for command in script:
            if command.startswith("await:"):
                marker = command.split(":", 1)[1]
                collected += session.read_until(
                    lambda line, marker=marker: line.startswith(marker),
                    label=f"await {marker}",
                    timeout=timeout,
                )
                continue
            session.send(command)
        return collected


class ObservationPartitionTests(unittest.TestCase):
    def test_partition_is_exact_disjoint_and_deterministic(self):
        roots = ("e2e4", "d2d4", "g1f3", "b1c3", "c2c4")
        owners = ("stockfish", "reckless", "lc0")
        first = partition_roots(roots, owners)
        second = partition_roots(roots, owners)
        self.assertEqual(first, second)
        self.assertEqual(sorted(first), sorted(owners))

        union: list[str] = []
        for owner in owners:
            union += list(first[owner])
        self.assertEqual(sorted(union), sorted(roots))
        self.assertEqual(len(union), len(set(union)))
        for left in owners:
            for right in owners:
                if left == right:
                    continue
                self.assertEqual(set(first[left]) & set(first[right]), set())

    def test_partition_ignores_every_evidence_signal(self):
        # The same root order must produce the same partition regardless of
        # anything a search might later report about those roots.
        roots = ("e2e4", "d2d4", "g1f3", "b1c3")
        owners = ("stockfish", "reckless", "lc0")
        baseline = partition_roots(roots, owners)
        self.assertEqual(partition_roots(tuple(roots), owners), baseline)
        self.assertEqual(baseline["stockfish"], ("e2e4", "b1c3"))
        self.assertEqual(baseline["reckless"], ("d2d4",))
        self.assertEqual(baseline["lc0"], ("g1f3",))

    def test_partition_requires_owners(self):
        with self.assertRaises(ShardLedgerError):
            partition_roots(("e2e4",), ())


class ShadowConfigTests(unittest.TestCase):
    def test_four_roles_are_distinct_and_anchor_is_unique(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load_runtime_config(write_shadow_config(Path(tmp)))
        self.assertEqual(config.mode, "shadow")
        self.assertEqual(config.anchor, ANCHOR)
        self.assertEqual(sorted(config.backends), sorted([ANCHOR, *SHADOWS]))
        self.assertEqual(config.backends[ANCHOR].role, "anchor")
        self.assertEqual(config.backends["stockfish-shadow"].role, "shadow")
        self.assertEqual(config.backends["stockfish-shadow"].family, "stockfish")
        self.assertEqual(config.backends[ANCHOR].family, "stockfish")
        self.assertEqual(config.telemetry_execution_mode, "shadow")
        self.assertEqual(config.shadow.oracle, "stockfish-shadow")

    def test_legacy_anchor_profile_is_unchanged(self):
        path = ROOT / "config" / "allfather.validation.json"

        # Assert the shipped document itself, so redirecting binaries below
        # cannot mask a change to the frozen profile.
        document = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(document["schema_version"], 1)
        self.assertEqual(document["mode"], "anchor")
        self.assertEqual(document["anchor"], "stockfish")
        self.assertEqual(sorted(document["backends"]), ["lc0", "reckless", "stockfish"])
        self.assertNotIn("instances", document)
        self.assertNotIn("shadow", document)

        with tempfile.TemporaryDirectory() as tmp:
            config = load_shipped_config(path, Path(tmp))
        self.assertEqual(config.mode, "anchor")
        self.assertEqual(config.anchor, "stockfish")
        self.assertEqual(sorted(config.backends), ["lc0", "reckless", "stockfish"])
        self.assertEqual(config.backends["stockfish"].role, "anchor")
        self.assertEqual(config.backends["lc0"].role, "managed")
        self.assertIsNone(config.shadow)
        self.assertEqual(config.telemetry_execution_mode, "baseline")

    def test_anchor_may_not_be_the_legal_root_oracle(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_shadow_config(Path(tmp))
            document = json.loads(path.read_text())
            document["shadow"]["oracle"] = ANCHOR
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(RuntimeError) as ctx:
                load_runtime_config(path)
            self.assertIn("must not be the outward anchor", str(ctx.exception))

    def test_shadow_owner_family_must_match_its_instance(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_shadow_config(Path(tmp))
            document = json.loads(path.read_text())
            document["shadow"]["instance_by_owner"]["reckless"] = "stockfish-shadow"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(RuntimeError):
                load_runtime_config(path)

    def test_second_anchor_instance_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_shadow_config(Path(tmp))
            document = json.loads(path.read_text())
            document["instances"]["stockfish-shadow"]["role"] = "anchor"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(RuntimeError):
                load_runtime_config(path)


class ShadowRuntimeHealthTests(unittest.TestCase):
    def test_shadow_death_does_not_break_authority_health(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = write_shadow_config(
                Path(tmp),
                instance_args={"lc0-shadow": ["--exit-on-go-number", "1"]},
            )
            manager = BackendManager.from_path(config)
            manager.start()
            try:
                self.assertTrue(manager.healthy)
                self.assertEqual(sorted(manager.backends), sorted([ANCHOR, *SHADOWS]))
                self.assertEqual(manager.authority_instances, (ANCHOR,))
                self.assertEqual(sorted(manager.shadow_instances), sorted(SHADOWS))

                manager.record_shadow_failure("lc0-shadow", "simulated crash", generation=1)
                self.assertTrue(manager.healthy, "a shadow failure must not break authority health")
                self.assertFalse(manager.shadow_available("lc0-shadow"))
                self.assertTrue(manager.shadow_available("reckless-shadow"))
                health = manager.shadow_health()
                self.assertEqual(health["lc0-shadow"].failure, "simulated crash")

                # Synchronization must still succeed with a dead shadow.
                manager.new_game()
                manager.set_position("position startpos")
                self.assertEqual(manager.legal_root_moves()[:2], ("e2e4", "d2d4"))
            finally:
                manager.close()
            self.assertTrue(all(not process.alive for process in manager.backends.values()))

    def test_oracle_runs_on_the_shadow_not_the_anchor(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = BackendManager.from_path(write_shadow_config(Path(tmp)))
            manager.start()
            try:
                self.assertEqual(manager.oracle_name, "stockfish-shadow")
                manager.set_position("position startpos")
                roots = manager.legal_root_moves()
                self.assertEqual(len(roots), 6)
                # The anchor must be idle and untouched by root qualification.
                self.assertFalse(manager.anchor.active_search)
            finally:
                manager.close()


class ShadowExecutionTests(unittest.TestCase):
    def test_exact_disjoint_dispatch_and_single_outward_bestmove(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = write_shadow_config(Path(tmp))
            lines = run_shell(config, ["go nodes 64", "await:bestmove "])
            bestmoves = [line for line in lines if line.startswith("bestmove ")]
            self.assertEqual(len(bestmoves), 1, f"expected one outward bestmove, got {bestmoves}")

            manifest = read_only_manifest(Path(tmp) / "replays")
            self.assertEqual(manifest["schema_version"], 1)
            self.assertEqual(manifest["controller"]["mode"], "shadow")
            self.assertEqual(manifest["legal_root_oracle"]["instance"], "stockfish-shadow")
            self.assertEqual(manifest["legal_root_oracle"]["root_count"], 6)

            owner_roots = manifest["ledger"]["owner_roots"]
            self.assertEqual(sorted(owner_roots), ["lc0", "reckless", "stockfish"])
            union: list[str] = []
            for moves in owner_roots.values():
                union += moves
            self.assertEqual(len(union), len(set(union)), "shadow root ownership must be disjoint")
            self.assertEqual(sorted(union), sorted(["e2e4", "d2d4", "g1f3", "b1c3", "c2c4", "g2g3"]))

            shadow_stages = [s for s in manifest["stages"] if s["role"] == "shadow"]
            self.assertEqual(len(shadow_stages), 3)
            for stage in shadow_stages:
                owner = stage["owner"]
                self.assertEqual(stage["dispatched_roots"], owner_roots[owner])
                # searchmoves must be last and must carry exactly the owned set.
                head, _, tail = stage["command"].partition(" searchmoves ")
                self.assertNotIn("searchmoves", head)
                self.assertEqual(tail.split(), owner_roots[owner])
                self.assertIn("nodes", head)
                if stage["bestmove"] is not None:
                    self.assertIn(
                        stage["bestmove"],
                        owner_roots[owner],
                        "shadow bestmove escaped its owned region",
                    )

            anchor_stages = [s for s in manifest["stages"] if s["role"] == "anchor"]
            self.assertEqual(len(anchor_stages), 1)
            self.assertEqual(anchor_stages[0]["dispatched_roots"], [], "anchor must be unrestricted")
            self.assertEqual(anchor_stages[0]["command"], "go nodes 64")

    def test_shadow_bestmove_never_leaks_outward(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Every shadow worker is scripted to prefer g2g3; the anchor is
            # scripted to prefer e2e4. Only the anchor may reach stdout.
            config = write_shadow_config(
                Path(tmp),
                instance_args={
                    ANCHOR: ["--leader-schedule", "e2e4"],
                    "stockfish-shadow": ["--leader-schedule", "g2g3"],
                    "reckless-shadow": ["--leader-schedule", "g2g3"],
                    "lc0-shadow": ["--leader-schedule", "g2g3"],
                },
            )
            lines = run_shell(config, ["go nodes 64", "await:bestmove "])
            bestmoves = [line for line in lines if line.startswith("bestmove ")]
            self.assertEqual(bestmoves, ["bestmove e2e4"])

            manifest = read_only_manifest(Path(tmp) / "replays")
            shadow_moves = {
                stage["bestmove"]
                for stage in manifest["stages"]
                if stage["role"] == "shadow" and stage["bestmove"]
            }
            self.assertTrue(shadow_moves, "shadow evidence was not recorded")
            self.assertNotIn("bestmove g2g3", bestmoves)

    def test_empty_owner_region_is_never_dispatched(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Two roots across three owners leaves lc0 with an empty region.
            config = write_shadow_config(Path(tmp), roots="e2e4,d2d4")
            run_shell(config, ["go nodes 64", "await:bestmove "])
            manifest = read_only_manifest(Path(tmp) / "replays")
            owners = sorted(manifest["ledger"]["owner_roots"])
            self.assertEqual(owners, ["reckless", "stockfish"])
            dispatched_owners = sorted(
                stage["owner"] for stage in manifest["stages"] if stage["role"] == "shadow"
            )
            self.assertEqual(dispatched_owners, ["reckless", "stockfish"])
            self.assertTrue(any("empty region" in note for note in manifest["notes"]))

    def test_terminal_universe_produces_no_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = write_shadow_config(Path(tmp), roots="")
            lines = run_shell(config, ["go nodes 64", "await:bestmove "])
            self.assertEqual(len([l for l in lines if l.startswith("bestmove ")]), 1)
            manifest = read_only_manifest(Path(tmp) / "replays")
            self.assertEqual(manifest["disposition"]["run"], "terminal_no_dispatch")
            self.assertTrue(manifest["legal_root_oracle"]["terminal_universe"])
            self.assertEqual(
                [s for s in manifest["stages"] if s["role"] == "shadow"],
                [],
            )

    def test_shadow_failure_is_evidence_not_an_election(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = write_shadow_config(
                Path(tmp),
                instance_args={
                    ANCHOR: ["--leader-schedule", "e2e4"],
                    "lc0-shadow": ["--exit-on-go-number", "1"],
                },
            )
            lines = run_shell(config, ["go nodes 64", "await:bestmove "])
            bestmoves = [line for line in lines if line.startswith("bestmove ")]
            self.assertEqual(bestmoves, ["bestmove e2e4"], "a shadow crash changed the outward move")

            manifest = read_only_manifest(Path(tmp) / "replays")
            lc0_stage = [s for s in manifest["stages"] if s["instance"] == "lc0-shadow"]
            self.assertEqual(len(lc0_stage), 1)
            self.assertEqual(lc0_stage[0]["disposition"], "failed")
            self.assertIsNone(lc0_stage[0]["bestmove"])
            self.assertFalse(manifest["shadow_health"]["lc0-shadow"]["alive"])
            # Surviving shadows keep working; none is promoted.
            survivors = [
                s for s in manifest["stages"] if s["role"] == "shadow" and s["instance"] != "lc0-shadow"
            ]
            self.assertEqual(len(survivors), 2)
            self.assertEqual(manifest["disposition"]["run"], "completed")

    def test_anchor_failure_still_fails_closed_in_shadow_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = write_shadow_config(
                Path(tmp),
                instance_args={ANCHOR: ["--exit-on", "go"]},
            )
            lines = run_shell(config, ["go nodes 64", "await:bestmove "])
            bestmoves = [line for line in lines if line.startswith("bestmove ")]
            self.assertEqual(bestmoves, ["bestmove 0000"])
            self.assertTrue(any("runtime failure" in line for line in lines))

    def test_stop_drains_shadow_work_and_emits_one_bestmove(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = write_shadow_config(
                Path(tmp),
                instance_args={ANCHOR: ["--info-delay-ms", "20"]},
                dispatch_nodes=100000,
            )
            with UciSession(
                Path(sys.executable),
                cwd=ROOT,
                timeout=20.0,
                args=["-m", "controller", "--config", str(config)],
            ) as session:
                session.configure({"UCI_Chess960": False})
                session.set_position({"startpos_moves": []})
                session.send("go infinite")
                session.read_until(
                    lambda line: line.startswith("info depth "),
                    label="infinite info",
                    timeout=10.0,
                )
                session.send("stop")
                stop_lines = session.read_until(
                    lambda line: line.startswith("bestmove "),
                    label="bestmove after stop",
                    timeout=10.0,
                )
                self.assertEqual(
                    len([line for line in stop_lines if line.startswith("bestmove ")]), 1
                )

            manifest = read_only_manifest(Path(tmp) / "replays")
            self.assertIn(manifest["disposition"]["run"], {"cancelled", "completed"})
            for stage in manifest["stages"]:
                self.assertNotEqual(stage["disposition"], "running")

    def test_new_position_cannot_be_observed_by_a_stale_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = write_shadow_config(Path(tmp), dispatch_nodes=100000)
            with UciSession(
                Path(sys.executable),
                cwd=ROOT,
                timeout=20.0,
                args=["-m", "controller", "--config", str(config)],
            ) as session:
                session.configure({"UCI_Chess960": False})
                session.set_position({"startpos_moves": []})
                session.send("go nodes 64")
                session.read_until(
                    lambda line: line.startswith("bestmove "),
                    label="first bestmove",
                    timeout=10.0,
                )
                session.set_position({"startpos_moves": ["e2e4"]})
                session.send("go nodes 64")
                session.read_until(
                    lambda line: line.startswith("bestmove "),
                    label="second bestmove",
                    timeout=10.0,
                )

            replay_root = Path(tmp) / "replays"
            runs = sorted(p for p in replay_root.iterdir() if p.is_dir())
            self.assertEqual(len(runs), 2)
            manifests = [json.loads((run / "manifest.json").read_text()) for run in runs]
            generations = sorted(m["generation"] for m in manifests)
            self.assertEqual(generations, [1, 2])
            positions = {m["generation"]: m["position"]["moves"] for m in manifests}
            self.assertEqual(positions[1], [])
            self.assertEqual(positions[2], ["e2e4"])
            # Each run's evidence belongs to exactly one synchronized position.
            for manifest in manifests:
                self.assertNotEqual(manifest["disposition"]["run"], "running")
                self.assertEqual(
                    len({stage["search_id"].split(":")[0] for stage in manifest["stages"]}),
                    1,
                )

    def test_quit_leaves_no_orphan_processes(self):
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            tag = f"orphan-check-{os.getpid()}"
            config = write_shadow_config(Path(tmp), tag=tag)
            proc = subprocess.Popen(
                [sys.executable, "-m", "controller", "--config", str(config)],
                cwd=str(ROOT),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            assert proc.stdin is not None
            proc.stdin.write("uci\nposition startpos\ngo nodes 64\n")
            proc.stdin.flush()
            assert proc.stdout is not None
            for line in proc.stdout:
                if line.startswith("bestmove "):
                    break
            proc.stdin.write("quit\n")
            proc.stdin.flush()
            self.assertEqual(proc.wait(timeout=15), 0)
            remaining = subprocess.run(
                ["pgrep", "-f", f"--tag {tag}"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(remaining.stdout.strip(), "", "shadow process outlived quit")


if __name__ == "__main__":
    unittest.main()

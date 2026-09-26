#!/usr/bin/env python3
"""Shadow execution tests: four roles, exact dispatch, authority firewall."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import threading
import time
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
    slow_anchor: bool = True,
    drain_timeout_s: float = 3.0,
    verification: bool = False,
    verification_nodes: int = 80,
    refinement: bool = False,
    refinement_nodes: int = 64,
    refinement_max_targets: int = 3,
    crossfeed: bool = False,
    counterfactual: bool = False,
) -> Path:
    instance_args = instance_args or {}
    instances = {}
    for name in (ANCHOR, *SHADOWS):
        args = [str(FAKE), "--name", f"Fake-{name}", "--roots", roots, "--tag", tag]
        if FAMILY[name] == "lc0":
            # LC0 omits the multipv token for its primary line at MultiPV=1.
            args += ["--omit-multipv-token"]
        if name == ANCHOR and slow_anchor:
            # A real anchor search is not instantaneous; the fake must overlap
            # shadow qualification the way a real one does.
            args += ["--info-lines", "4", "--info-delay-ms", "25"]
        args += instance_args.get(name, [])
        options = {"UCI_Chess960": False}
        if FAMILY[name] == "lc0":
            options["ScoreType"] = "centipawn"
        instances[name] = {
            "family": FAMILY[name],
            "role": "anchor" if name == ANCHOR else "shadow",
            "binary": sys.executable,
            "cwd": ".",
            "args": args,
            "options": options,
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
            "drain_timeout_s": drain_timeout_s,
            "on_anchor_complete": "drain",
        },
        "instances": instances,
    }
    if verification or refinement:
        document["verification"] = {
            "enabled": True,
            "nomination_method": "owner_bestmove_union_v1",
            "dispatch_limit": {"nodes": verification_nodes},
        }
    if refinement:
        document["refinement"] = {
            "enabled": True,
            "nomination_method": "verify_final_disagreement_union_v1",
            "child_partition": "child_index_modulo",
            "dispatch_limit": {"nodes": refinement_nodes},
            "max_targets": refinement_max_targets,
        }
    if crossfeed or counterfactual:
        document["crossfeed"] = {
            "enabled": True,
            "policy": "typed_verify_refine_v1",
        }
    if counterfactual:
        document["counterfactual"] = {
            "enabled": True,
            "policy": "unanimous_verify_v1",
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


class ReviewRegressionRoundThreeTests(unittest.TestCase):
    """Round-three review findings on the shadow runtime."""

    def test_an_in_flight_oracle_is_caught_by_the_quiesce_barrier(self):
        """Owner states do not exist yet while the oracle is answering.

        The stuck-worker sweep iterates `active.owners`, which is populated only
        after qualification returns. A state mutation arriving mid-qualification
        therefore found nothing to fail and the caller was free to synchronize
        the next position into a process still running `go perft 1`.
        """
        with tempfile.TemporaryDirectory() as tmp:
            config = write_shadow_config(
                Path(tmp),
                # quiesce() joins the worker and then waits on the run, so it
                # tolerates up to two drain timeouts before giving up.
                drain_timeout_s=1.0,
                instance_args={
                    # Outlast both, but not so far that the bundle cannot be
                    # written before the session tears the process down.
                    "stockfish-shadow": ["--perft-delay-ms", "3000"],
                },
            )
            run_shell(
                config,
                [
                    "go nodes 64",
                    "await:bestmove ",
                    # Mutating state mid-qualification is what the barrier is for.
                    "position startpos moves e2e4",
                    "isready",
                    "await:readyok",
                ],
                timeout=40.0,
            )
            manifest = read_only_manifest(Path(tmp) / "replays")
            notes = " ".join(manifest.get("notes", []))
            self.assertIn("legal-root oracle", notes)
            self.assertIn("excluded from further synchronization", notes)

    def test_a_long_anchor_outlives_the_shadow_drain_timeout(self):
        """The shadow drain bound is not an authority deadline.

        Node-limited shadow stages finish in well under a second; a long anchor
        search does not. Bounding the finalization wait by `drain_timeout_s`
        closed the authority stream and cleared the run while the outward search
        was still going, so the anchor's own `bestmove` had nowhere to land.
        """
        with tempfile.TemporaryDirectory() as tmp:
            config = write_shadow_config(
                Path(tmp),
                dispatch_nodes=32,
                instance_args={
                    # ~5s of anchor search against a 3s shadow drain timeout.
                    ANCHOR: ["--info-lines", "50", "--info-delay-ms", "100"],
                },
            )
            lines = run_shell(config, ["go nodes 64", "await:bestmove "], timeout=60.0)
            bestmoves = [line for line in lines if line.startswith("bestmove ")]
            self.assertEqual(len(bestmoves), 1, "the outward search must still answer once")

            manifest = read_only_manifest(Path(tmp) / "replays")
            anchor = [s for s in manifest["stages"] if s["role"] == "anchor"]
            self.assertEqual(len(anchor), 1)
            self.assertEqual(
                anchor[0]["disposition"],
                "completed",
                "the authority stage was left unresolved by the shadow drain bound",
            )
            self.assertTrue(
                anchor[0]["bestmove"],
                "the anchor's outward decision was discarded after its stream closed",
            )
            self.assertEqual(bestmoves[0], f"bestmove {anchor[0]['bestmove']}")

            anchor_streams = [s for s in manifest["streams"] if s["role"] == "anchor"]
            self.assertEqual(len(anchor_streams), 1)
            self.assertTrue(
                anchor_streams[0]["complete"],
                "the authority stream was closed before search.complete",
            )



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

    def test_oracle_must_be_an_observational_shadow_instance(self):
        """'Not the anchor' is not the same as 'observational'.

        A `managed` instance is authority-critical: an oracle timeout on one
        takes the authority failure path and can fail the outward search, and
        `record_shadow_failure` would not exclude it from synchronization.
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = write_shadow_config(Path(tmp))
            document = json.loads(path.read_text())
            document["instances"]["stockfish-managed"] = dict(
                document["instances"]["stockfish-shadow"], role="managed"
            )
            document["shadow"]["oracle"] = "stockfish-managed"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(RuntimeError) as ctx:
                load_runtime_config(path)
            message = str(ctx.exception)
            self.assertIn("stockfish-managed", message)
            self.assertIn("shadow", message)

    def test_shipped_shadow_profile_names_a_shadow_oracle(self):
        """The validation is worthless if it rejects the profile it ships with."""
        for name in ("allfather.shadow.validation.json", "allfather.active.validation.json"):
            with self.subTest(config=name):
                document = json.loads((ROOT / "config" / name).read_text(encoding="utf-8"))
                oracle = document["shadow"]["oracle"]
                self.assertEqual(document["instances"][oracle]["role"], "shadow")

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

    def test_authority_readiness_does_not_wait_for_observational_shadows(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = BackendManager.from_path(write_shadow_config(Path(tmp)))
            manager.start()
            try:
                shadow = manager.backends["lc0-shadow"]
                original_ready = shadow.ready

                def slow_shadow_ready(*, timeout=None):
                    time.sleep(0.5)
                    return original_ready(timeout=timeout)

                shadow.ready = slow_shadow_ready  # type: ignore[method-assign]
                started = time.monotonic()
                manager.ready_authority()
                elapsed = time.monotonic() - started
                self.assertLess(
                    elapsed,
                    0.25,
                    "external authority readiness waited for a shadow worker",
                )
            finally:
                manager.close()
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

    def test_no_stage_is_dispatched_after_the_outward_decision(self):
        # A short fixed-node anchor routinely finishes before root
        # qualification does. `drain` means "let work already in flight
        # finish", not "start new work once the decision is already made".
        with tempfile.TemporaryDirectory() as tmp:
            config = write_shadow_config(Path(tmp), slow_anchor=False)
            lines = run_shell(config, ["go nodes 64", "await:bestmove "])
            self.assertEqual(len([l for l in lines if l.startswith("bestmove ")]), 1)

            manifest = read_only_manifest(Path(tmp) / "replays")
            anchor = next(s for s in manifest["stages"] if s["role"] == "anchor")
            self.assertIsNotNone(anchor["completed_ms"])

            # Whether qualification or the anchor wins this race varies, so the
            # invariant is asserted rather than one side of the race: no stage
            # may be dispatched after the outward decision was emitted.
            for stage in manifest["stages"]:
                if stage["role"] != "shadow":
                    continue
                self.assertLessEqual(
                    stage["dispatched_ms"],
                    anchor["completed_ms"],
                    f"{stage['instance']} was dispatched after the outward decision",
                )

    def test_external_searchmoves_restricts_the_shadow_universe(self):
        # The anchor searches only what the caller asked for. Shadow evidence
        # must describe the same request, not a wider one.
        with tempfile.TemporaryDirectory() as tmp:
            config = write_shadow_config(Path(tmp))
            run_shell(config, ["go nodes 64 searchmoves e2e4 d2d4", "await:bestmove "])
            manifest = read_only_manifest(Path(tmp) / "replays")

            oracle = manifest["legal_root_oracle"]
            self.assertEqual(oracle["root_count"], 6)
            self.assertEqual(oracle["dispatch_root_count"], 2)
            self.assertEqual(oracle["external_root_restriction"], ["d2d4", "e2e4"])

            owned: list[str] = []
            for moves in manifest["ledger"]["owner_roots"].values():
                owned += moves
            self.assertEqual(sorted(owned), ["d2d4", "e2e4"])
            for stage in manifest["stages"]:
                if stage["role"] == "shadow":
                    for move in stage["dispatched_roots"]:
                        self.assertIn(move, ("e2e4", "d2d4"))

    def test_stuck_shadow_is_failed_by_the_quiesce_barrier(self):
        # A worker that ignores `stop` must not simply be abandoned: the state
        # mutation that follows would reach a process still executing the
        # previous generation. It is recorded as a shadow failure instead.
        with tempfile.TemporaryDirectory() as tmp:
            config = write_shadow_config(
                Path(tmp),
                instance_args={
                    "reckless-shadow": [
                        "--ignore-stop",
                        "--info-lines",
                        "400",
                        "--info-delay-ms",
                        "50",
                    ]
                },
            )
            with UciSession(
                Path(sys.executable),
                cwd=ROOT,
                timeout=40.0,
                args=["-m", "controller", "--config", str(config)],
            ) as session:
                session.configure({"UCI_Chess960": False})
                session.set_position({"startpos_moves": []})
                session.send("go nodes 64")
                session.read_until(
                    lambda line: line.startswith("bestmove "),
                    label="first bestmove",
                    timeout=20.0,
                )
                # Forces the drain barrier while the stuck worker is running.
                session.set_position({"startpos_moves": ["e2e4"]})
                session.send("isready")
                session.read_until(
                    lambda line: line == "readyok", label="readyok", timeout=20.0
                )

            replay_root = Path(tmp) / "replays"
            first_run = sorted(p for p in replay_root.iterdir() if p.is_dir())[0]
            manifest_path = first_run / "manifest.json"
            deadline = time.monotonic() + 20.0
            while not manifest_path.is_file() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(
                manifest_path.is_file(),
                "a stuck worker must still release its bundle once it is failed",
            )
            first = json.loads(manifest_path.read_text())
            self.assertTrue(
                any("did not drain" in note for note in first["notes"]), first["notes"]
            )
            self.assertFalse(first["shadow_health"]["reckless-shadow"]["alive"])

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


class VerificationExecutionTests(unittest.TestCase):
    def test_verify_reuses_the_three_shadows_on_one_common_candidate_set(self):
        from controller.verification import (
            load_verification_manifest,
            verify_verification_integrity,
        )

        with tempfile.TemporaryDirectory() as tmp:
            config = write_shadow_config(
                Path(tmp),
                verification=True,
                dispatch_nodes=80,
                instance_args={
                    ANCHOR: ["--info-lines", "40", "--info-delay-ms", "10"],
                },
            )
            run_shell(config, ["go nodes 64", "await:bestmove "], timeout=30.0)
            replay_root = Path(tmp) / "replays"
            run_dir = next(path for path in replay_root.iterdir() if path.is_dir())
            parent = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            verify = load_verification_manifest(run_dir)

            explore = [stage for stage in parent["stages"] if stage["role"] == "shadow"]
            self.assertEqual(len(explore), 3)
            nominees = {
                stage["owner"]: stage["bestmove"]
                for stage in explore
                if stage["disposition"] == "completed"
            }
            self.assertEqual(len(nominees), 3)

            expected = [
                nominees["stockfish"],
                nominees["reckless"],
                nominees["lc0"],
            ]
            self.assertEqual(len(set(expected)), 3)
            self.assertEqual(verify["nomination"]["candidate_roots"], expected)
            self.assertEqual(verify["disposition"]["run"], "completed")
            self.assertEqual(len(verify["stages"]), 3)
            for stage in verify["stages"]:
                self.assertEqual(stage["candidate_roots"], expected)
                self.assertEqual(stage["disposition"], "completed")
            self.assertEqual(verify_verification_integrity(run_dir), [])

            # VERIFY remains outside the EXPLORE ledger/replay stage list.
            self.assertTrue(
                all(":verify:" not in stage["search_id"] for stage in parent["stages"])
            )

    def test_active_mode_refuses_verification_without_budget_reserve(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_shadow_config(
                Path(tmp),
                mode="active",
                verification=True,
                extra={"budget": {}, "routing": {}},
            )
            with self.assertRaises(RuntimeError) as ctx:
                load_runtime_config(path)
            self.assertIn(
                "active verification requires budget.verification_reserve_fraction > 0",
                str(ctx.exception),
            )


class ReviewRegressionRoundFourTests(unittest.TestCase):
    """Round-four findings on authority isolation, config and provenance."""

    # -- Q10: an observer exception took down the outward search ------------

    def test_a_failing_telemetry_observer_never_withholds_the_outward_answer(self):
        """Observation is never authoritative, including when it crashes.

        `observer(...)` ran before the authoritative callback, so an exception
        in it skipped `on_complete`. For the anchor's `bestmove` that left the
        frontend in SEARCHING forever: observational machinery taking down the
        outward search.
        """
        with tempfile.TemporaryDirectory() as tmp:
            config = write_shadow_config(Path(tmp))
            manager = BackendManager.from_path(config)
            manager.start()
            try:
                def _boom(instance, token, line, observed):
                    raise RuntimeError("observer exploded")

                manager.set_instance_observer(_boom)
                answered: list[str] = []
                done = threading.Event()

                def _on_complete(token: int, line: str) -> None:
                    answered.append(line)
                    done.set()

                manager.start_anchor_search(
                    "go nodes 64",
                    token=1,
                    on_info=lambda token, line: None,
                    on_complete=_on_complete,
                )
                self.assertTrue(
                    done.wait(timeout=15.0),
                    "the anchor's bestmove never reached the authority callback",
                )
                self.assertTrue(answered[0].startswith("bestmove "))
                # The failure is evidence, not silence.
                self.assertTrue(
                    any("telemetry observer failed" in item for item in manager.observer_failures()),
                    "the observer failure was swallowed without record",
                )
            finally:
                manager.set_instance_observer(None)
                manager.close()

    # -- Q11: a Chess960 startup option disagreed with the controller -------

    def test_chess960_as_a_startup_option_is_refused(self):
        """The engines would search FRC while every replay said standard."""
        with tempfile.TemporaryDirectory() as tmp:
            path = write_shadow_config(Path(tmp))
            document = json.loads(path.read_text())
            document["instances"]["stockfish-shadow"]["options"]["UCI_Chess960"] = True
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(RuntimeError) as ctx:
                load_runtime_config(path)
            self.assertIn("UCI_Chess960", str(ctx.exception))

    # -- Q4: the drain bound doubled as an undocumented runtime cap ---------

    def test_stage_timeout_is_declared_separately_from_the_drain_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load_runtime_config(write_shadow_config(Path(tmp), drain_timeout_s=1.0))
            self.assertEqual(config.shadow.drain_timeout_s, 1.0)
            self.assertGreater(
                config.shadow.stage_timeout_s,
                config.shadow.drain_timeout_s,
                "a normally progressing stage must not be bounded by the stop-wait budget",
            )

    # -- Q9: provenance was hashed after the engines were already running ---

    def test_provenance_is_captured_before_any_process_starts(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = write_shadow_config(Path(tmp))
            manager = BackendManager.from_path(config)
            # No start() yet: the identities must already be there.
            self.assertTrue(manager.config_sha256)
            self.assertEqual(len(manager.config_sha256), 64)
            self.assertEqual(
                sorted(manager.engine_identity),
                sorted([ANCHOR, *SHADOWS]),
            )
            for identity in manager.engine_identity.values():
                self.assertIn("binary_sha256", identity)


class ReviewRegressionRoundSixTests(unittest.TestCase):
    """Round-six findings on startup isolation, ownership and config safety."""

    def test_a_shadow_that_dies_during_startup_does_not_deny_outward_service(self):
        """An observational outage must not abort the whole controller.

        `process.start()` and `configure()` raised into one shared handler that
        closed the already-healthy anchor, so a shadow failing its `uci`
        handshake prevented any outward search -- the opposite of what every
        post-startup path does with the same failure.
        """
        with tempfile.TemporaryDirectory() as tmp:
            config = write_shadow_config(
                Path(tmp),
                instance_args={"reckless-shadow": ["--exit-on", "uci"]},
            )
            lines = run_shell(config, ["go nodes 64", "await:bestmove "], timeout=40.0)
            bestmoves = [line for line in lines if line.startswith("bestmove ")]
            self.assertEqual(
                len(bestmoves), 1, "a dead shadow prevented the outward answer"
            )

            manifest = read_only_manifest(Path(tmp) / "replays")
            health = manifest.get("shadow_health") or {}
            self.assertIn("reckless-shadow", health)
            self.assertFalse(health["reckless-shadow"]["alive"])

    def test_an_anchor_that_dies_during_startup_still_fails_closed(self):
        """Isolation is for observational roles only; authority still aborts."""
        with tempfile.TemporaryDirectory() as tmp:
            config = write_shadow_config(
                Path(tmp),
                instance_args={ANCHOR: ["--exit-on", "uci"]},
            )
            manager = BackendManager.from_path(config)
            with self.assertRaises(RuntimeError):
                manager.start()
            manager.close()

    def test_an_instance_name_may_not_escape_the_replay_directory(self):
        """Instance names become telemetry filenames."""
        for unsafe in ("../escape", "nested/name", "/absolute"):
            with self.subTest(name=unsafe):
                with tempfile.TemporaryDirectory() as tmp:
                    path = write_shadow_config(Path(tmp))
                    document = json.loads(path.read_text())
                    document["instances"][unsafe] = dict(
                        document["instances"]["reckless-shadow"]
                    )
                    del document["instances"]["reckless-shadow"]
                    document["shadow"]["instance_by_owner"]["reckless"] = unsafe
                    path.write_text(json.dumps(document), encoding="utf-8")
                    with self.assertRaises(RuntimeError) as ctx:
                        load_runtime_config(path)
                    self.assertIn("telemetry filename", str(ctx.exception))

    def test_a_safe_instance_name_is_still_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load_runtime_config(write_shadow_config(Path(tmp)))
            self.assertIn("reckless-shadow", config.backends)



class ReviewRegressionRoundSevenTests(unittest.TestCase):
    """Round-seven findings on the pre-anchor path."""

    def test_replay_setup_is_bounded_before_the_anchor_is_dispatched(self):
        """Observational filesystem IO may not delay decision authority.

        `prepare_run` does a `mkdir`, opens a JSONL file and starts a writer
        thread, and the frontend calls it before `start_anchor_search`. On a
        blocked `replay_root` that held the outward search for as long as the
        kernel took. The directory work is now abandoned at
        `shadow.prepare_budget_s` and the search proceeds without a bundle.
        """
        with tempfile.TemporaryDirectory() as tmp:
            config = load_runtime_config(write_shadow_config(Path(tmp)))
            self.assertGreater(config.shadow.prepare_budget_s, 0.0)
            self.assertLessEqual(
                config.shadow.prepare_budget_s,
                1.0,
                "the pre-anchor budget must be small enough to be a firewall",
            )

    def test_a_blocked_replay_root_does_not_hold_the_outward_search(self):
        """The whole `prepare_run` path must return, not block on the kernel."""
        import controller.shadow as shadow_module

        with tempfile.TemporaryDirectory() as tmp:
            manager = BackendManager.from_path(write_shadow_config(Path(tmp)))
            manager.start()
            coordinator = shadow_module.ShadowRunCoordinator(runtime=manager)
            original = shadow_module.Path.mkdir
            try:
                def _hang(self, *args, **kwargs):
                    time.sleep(5.0)

                shadow_module.Path.mkdir = _hang  # type: ignore[assignment]
                started = time.monotonic()
                ok = coordinator.prepare_run(generation=1, go_command="go nodes 64")
                elapsed = time.monotonic() - started
            finally:
                shadow_module.Path.mkdir = original  # type: ignore[assignment]
                coordinator.close()
                manager.close()

            self.assertFalse(ok, "a blocked replay root must not yield a bundle")
            self.assertLess(
                elapsed,
                2.0,
                "the outward search was held for the full filesystem stall",
            )


class ReviewRegressionRoundEightTests(unittest.TestCase):
    """Round-eight findings on the pre-anchor bound, drain scope and anchor family."""

    def test_stream_setup_is_inside_the_pre_anchor_budget_too(self):
        """Round seven bounded the run `mkdir` and left the stream file outside it.

        `TelemetryStreamWriter` does its own `mkdir` and `open`, which is more
        pre-anchor filesystem work one call later.
        """
        import controller.shadow as shadow_module

        with tempfile.TemporaryDirectory() as tmp:
            manager = BackendManager.from_path(write_shadow_config(Path(tmp)))
            manager.start()
            coordinator = shadow_module.ShadowRunCoordinator(runtime=manager)
            real_open = shadow_module.Path.open
            try:
                def _hang_open(self, *args, **kwargs):
                    if str(self).endswith(".jsonl"):
                        time.sleep(5.0)
                    return real_open(self, *args, **kwargs)

                shadow_module.Path.open = _hang_open  # type: ignore[assignment]
                started = time.monotonic()
                ok = coordinator.prepare_run(generation=1, go_command="go nodes 64")
                elapsed = time.monotonic() - started
            finally:
                shadow_module.Path.open = real_open  # type: ignore[assignment]
                coordinator.close()
                manager.close()

            self.assertFalse(ok, "a blocked stream file must not yield a bundle")
            self.assertLess(
                elapsed, 2.0, "stream setup delayed the outward search past its budget"
            )

    def test_a_sub_second_stage_timeout_is_honoured(self):
        """A declared 50 ms cap was silently replaced with one second."""
        with tempfile.TemporaryDirectory() as tmp:
            path = write_shadow_config(Path(tmp))
            document = json.loads(path.read_text())
            document["shadow"]["stage_timeout_s"] = 0.05
            path.write_text(json.dumps(document), encoding="utf-8")
            import controller.shadow as shadow_module

            manager = BackendManager.from_path(path)
            coordinator = shadow_module.ShadowRunCoordinator(runtime=manager)
            try:
                self.assertEqual(coordinator.settings.stage_timeout_s, 0.05)
                self.assertEqual(
                    coordinator._stage_budget(),
                    0.05,
                    "a declared 50 ms cap was silently replaced with one second",
                )
            finally:
                coordinator.close()
                manager.close()

    def test_the_anchor_must_be_a_stockfish_instance(self):
        """Every claim in this milestone says the outward move is Stockfish's."""
        with tempfile.TemporaryDirectory() as tmp:
            path = write_shadow_config(Path(tmp))
            document = json.loads(path.read_text())
            document["instances"][ANCHOR]["family"] = "reckless"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(RuntimeError) as ctx:
                load_runtime_config(path)
            self.assertIn("decision authority", str(ctx.exception))


class ReviewRegressionRoundNineTests(unittest.TestCase):
    """Round-nine findings, all three on the pre-anchor preparation path."""

    @staticmethod
    def _telemetry_threads() -> set:
        return {
            thread
            for thread in threading.enumerate()
            if thread.name.startswith("allfather-telemetry-")
        }

    def test_the_run_clock_starts_before_replay_preparation(self):
        """Preparation is bounded, not free, and the envelope has to see it.

        `started_monotonic` was captured after the run directory was created,
        so every millisecond of pre-anchor filesystem work sat outside the wall
        envelope and outside the controller-overhead charge: a 200 ms `mkdir`
        ahead of a 900 ms anchor search still reported `wall_within_envelope`
        against a 1000 ms envelope.
        """
        import controller.shadow as shadow_module

        delay_s = 0.06
        with tempfile.TemporaryDirectory() as tmp:
            manager = BackendManager.from_path(write_shadow_config(Path(tmp)))
            manager.start()
            coordinator = shadow_module.ShadowRunCoordinator(runtime=manager)
            real_mkdir = shadow_module.Path.mkdir
            try:
                def _slow_mkdir(self, *args, **kwargs):
                    # Only the run bundle's own directory, so the delay is the
                    # preparation this test is about and nothing else.
                    if "-g0000" in self.name:
                        time.sleep(delay_s)
                    return real_mkdir(self, *args, **kwargs)

                shadow_module.Path.mkdir = _slow_mkdir  # type: ignore[assignment]
                ok = coordinator.prepare_run(generation=1, go_command="go nodes 64")
                active = coordinator._run
                elapsed_ms = None if active is None else active.context.elapsed_ms()
                prepare_ms = None if active is None else active.run.prepare_ms
            finally:
                shadow_module.Path.mkdir = real_mkdir  # type: ignore[assignment]
                coordinator.close()
                manager.close()

            self.assertTrue(ok, "a 60ms mkdir is well inside the pre-anchor budget")
            self.assertIsNotNone(elapsed_ms)
            self.assertGreaterEqual(
                elapsed_ms,
                delay_s * 1000.0 * 0.9,
                "the run clock did not include the preparation it is meant to bound",
            )
            self.assertGreaterEqual(
                elapsed_ms,
                prepare_ms,
                "the run clock started later than the preparation it measures",
            )

    def test_a_late_stream_writer_is_closed_rather_than_leaked(self):
        """A writer finishing after the budget owns a thread and a descriptor.

        The timeout path dropped the constructor's result. The completed
        writer's thread stayed blocked on its queue forever and its file handle
        stayed open, so a repeatedly slow `replay_root` leaked one of each per
        search until the controller ran out.
        """
        import controller.shadow as shadow_module

        with tempfile.TemporaryDirectory() as tmp:
            manager = BackendManager.from_path(write_shadow_config(Path(tmp)))
            manager.start()
            coordinator = shadow_module.ShadowRunCoordinator(runtime=manager)
            replay_root = coordinator.settings.replay_root

            def entries() -> set:
                if not replay_root.exists():
                    return set()
                return set(replay_root.iterdir())

            before_dirs = entries()
            before = self._telemetry_threads()
            real_open = shadow_module.Path.open
            late_opens: list[str] = []
            try:
                def _slow_open(self, *args, **kwargs):
                    if self.suffix != ".jsonl":
                        return real_open(self, *args, **kwargs)
                    # Open FIRST, then stall. Stalling before the open makes the
                    # constructor fail outright once the directory is taken
                    # back, so no writer is ever built and the leak this test
                    # is about cannot occur -- the test would pass whatever the
                    # code did. The leak needs a constructor that SUCCEEDS
                    # after the budget, holding a handle and a live thread.
                    handle = real_open(self, *args, **kwargs)
                    time.sleep(0.6)
                    late_opens.append(self.name)
                    return handle

                shadow_module.Path.open = _slow_open  # type: ignore[assignment]
                ok = coordinator.prepare_run(generation=1, go_command="go nodes 64")
                # `prepare_run` gives up at the budget, 0.25s in, while the
                # writer is still being constructed. Checking now would find no
                # thread yet and pass whatever the code does; wait until the
                # constructor has certainly finished and started one.
                time.sleep(1.5)
                leaked = self._telemetry_threads() - before
                orphans = entries() - before_dirs
            finally:
                shadow_module.Path.open = real_open  # type: ignore[assignment]
                coordinator.close()
                manager.close()

            self.assertFalse(ok, "a stream past its budget must not yield a bundle")
            self.assertTrue(
                late_opens,
                "the stream file was never opened, so nothing was left to leak "
                "and this test proved nothing",
            )
            self.assertEqual(
                sorted(thread.name for thread in leaked),
                [],
                "the late stream writer's thread and descriptor were never released",
            )
            self.assertEqual(
                sorted(path.name for path in orphans),
                [],
                "the abandoned stream setup left its bundle directory behind",
            )

    def test_a_failed_stream_open_leaves_no_bundle_directory(self):
        """The run directory is created before the stream, and outlives it.

        `prepare_run` returns as soon as the stream cannot be opened, having
        already created the bundle directory. No run finalizes into it, so no
        manifest is ever written there, and `build_derived_artifact` raises on
        a manifest-less directory rather than skipping it.
        """
        import controller.shadow as shadow_module

        with tempfile.TemporaryDirectory() as tmp:
            manager = BackendManager.from_path(write_shadow_config(Path(tmp)))
            manager.start()
            coordinator = shadow_module.ShadowRunCoordinator(runtime=manager)
            replay_root = coordinator.settings.replay_root

            def entries() -> set:
                if not replay_root.exists():
                    return set()
                return set(replay_root.iterdir())

            before = entries()
            real_open = shadow_module.Path.open
            refused: list[str] = []
            try:
                def _refuse_open(self, *args, **kwargs):
                    if self.suffix == ".jsonl":
                        refused.append(self.name)
                        raise OSError(28, "No space left on device")
                    return real_open(self, *args, **kwargs)

                shadow_module.Path.open = _refuse_open  # type: ignore[assignment]
                ok = coordinator.prepare_run(generation=1, go_command="go nodes 64")
                orphans = entries() - before
            finally:
                shadow_module.Path.open = real_open  # type: ignore[assignment]
                coordinator.close()
                manager.close()

            self.assertFalse(ok, "an unopenable stream must not yield a bundle")
            self.assertTrue(
                refused, "the stream was never opened, so this test proved nothing"
            )
            self.assertEqual(
                sorted(path.name for path in orphans),
                [],
                "a failed stream open left a manifest-less directory behind",
            )

    def test_a_late_run_directory_is_not_left_under_the_replay_root(self):
        """An empty, manifest-less bundle directory is not inert leftover.

        Offline derivation walks every directory under `replay_root`, and one
        with no manifest raises rather than being skipped, so a directory the
        abandoned thread created after its run gave up would break the whole
        derivation pass.
        """
        import controller.shadow as shadow_module

        with tempfile.TemporaryDirectory() as tmp:
            manager = BackendManager.from_path(write_shadow_config(Path(tmp)))
            manager.start()
            coordinator = shadow_module.ShadowRunCoordinator(runtime=manager)
            replay_root = coordinator.settings.replay_root

            def entries() -> set:
                if not replay_root.exists():
                    return set()
                return set(replay_root.iterdir())

            before = entries()
            real_mkdir = shadow_module.Path.mkdir
            late_dirs: list[str] = []
            try:
                def _slow_mkdir(self, *args, **kwargs):
                    if "-g0000" not in self.name:
                        return real_mkdir(self, *args, **kwargs)
                    time.sleep(0.6)
                    result = real_mkdir(self, *args, **kwargs)
                    late_dirs.append(self.name)
                    return result

                shadow_module.Path.mkdir = _slow_mkdir  # type: ignore[assignment]
                ok = coordinator.prepare_run(generation=1, go_command="go nodes 64")
                # The abandoned thread's `mkdir` returns 0.6s in; wait past
                # that so the directory has certainly been created, and its
                # release has certainly had its chance to run.
                time.sleep(1.5)
                orphans = entries() - before
            finally:
                shadow_module.Path.mkdir = real_mkdir  # type: ignore[assignment]
                coordinator.close()
                manager.close()

            self.assertFalse(ok, "a run directory past its budget must not yield a bundle")
            self.assertTrue(
                late_dirs,
                "no directory was ever created, so this test proved nothing",
            )
            self.assertEqual(
                sorted(path.name for path in orphans),
                [],
                "the abandoned thread left a manifest-less directory behind",
            )


class ReviewRegressionRoundTenTests(unittest.TestCase):
    """Round-ten findings: authority isolation, shared deadlines, gate binding."""

    @staticmethod
    def _install_owners(coordinator, active) -> list:
        """Populate owner state the way qualification does, without an oracle.

        `prepare_run` creates the run; owners appear later, when the legal-root
        oracle answers. These tests are about what happens to a DISPATCHED
        owner, so they install that state directly and leave every other path
        untouched.
        """
        import controller.shadow as shadow_module

        states = []
        for index, owner in enumerate(coordinator.settings.owners):
            instance = coordinator.settings.instance_by_owner[owner]
            state = shadow_module._OwnerState(
                owner=owner,
                instance=instance,
                family=coordinator.runtime.spec(instance).family,
                roots=("e2e4",),
            )
            state.dispatched = True
            active.owners[owner] = state
            states.append(state)
        return states

    def test_abort_run_with_assigned_worker_uses_explicit_invalidating_cancel(self):
        """Anchor dispatch failure must never depend on an undefined stop mode."""
        import controller.shadow as shadow_module

        with tempfile.TemporaryDirectory() as tmp:
            manager = BackendManager.from_path(write_shadow_config(Path(tmp)))
            manager.start()
            coordinator = shadow_module.ShadowRunCoordinator(runtime=manager)
            active = None
            try:
                self.assertTrue(
                    coordinator.prepare_run(generation=1, go_command="go nodes 64")
                )
                active = coordinator._run
                self.assertIsNotNone(active)

                # Exercise abort_run's worker-assigned branch without starting
                # the real orchestration worker. A completed Thread is enough
                # to select that cleanup path while keeping the test isolated.
                worker = threading.Thread(target=lambda: None)
                worker.start()
                worker.join(timeout=1.0)
                active.worker = worker

                coordinator.abort_run(
                    1,
                    reason="anchor_dispatch_failed",
                )
                self.assertTrue(active.cancelled)
                self.assertEqual(active.cancel_reason, "anchor_dispatch_failed")
            finally:
                if active is not None:
                    try:
                        active.run.finalize(
                            disposition="aborted",
                            stop_reason="test_cleanup",
                        )
                    except Exception:
                        pass
                    active.finished.set()
                with coordinator._lock:
                    coordinator._run = None
                coordinator.close()
                manager.close()

    def test_the_stop_command_reaches_the_anchor_before_any_shadow(self):
        """A blocked shadow must not swallow the GUI's `stop`.

        `_shadow_cancel` ran first and sends `stop` to every dispatched shadow.
        One blocked shadow stdin therefore meant `stop_anchor()` was never
        reached and the anchor's already-computed `bestmove` was never asked
        for -- observation holding up decision authority, which the authority
        firewall does not permit.
        """
        import controller.uci_frontend as frontend_module

        order: list[str] = []
        released = threading.Event()

        class _BlockingShadow:
            def cancel(self, generation=None, *, reason, detach=False):
                order.append("shadow")
                released.wait(timeout=5.0)

            def quiesce(self, *args, **kwargs):
                return True

            def close(self):
                pass

        class _Runtime:
            healthy = True

            def stop_anchor(self):
                order.append("anchor")

        shell = frontend_module.UciFrontend.__new__(frontend_module.UciFrontend)
        shell.runtime = _Runtime()
        shell.shadow = _BlockingShadow()
        shell.output = io.StringIO()
        shell._write_lock = threading.Lock()
        shell._state_lock = threading.RLock()
        shell._state = frontend_module.ShellState.SEARCHING
        shell._active_generation = 1

        try:
            worker = threading.Thread(target=shell.handle_command, args=("stop",), daemon=True)
            worker.start()
            worker.join(timeout=3.0)
            self.assertEqual(
                order[:1],
                ["anchor"],
                "the shadow layer was contacted before decision authority was",
            )
        finally:
            released.set()

    def test_cancelling_shadows_does_not_hold_the_coordinator_lock(self):
        """`stop` writes happen outside `self._lock`.

        Writing to an engine's stdin can block on a full pipe. Doing it under
        the coordinator lock blocks `note_anchor_complete`, which is the path
        that records the anchor's own completion.
        """
        import controller.shadow as shadow_module

        with tempfile.TemporaryDirectory() as tmp:
            manager = BackendManager.from_path(write_shadow_config(Path(tmp)))
            manager.start()
            coordinator = shadow_module.ShadowRunCoordinator(runtime=manager)
            released = threading.Event()
            blocked = threading.Event()
            real_stop = manager.stop_instance
            try:
                self.assertTrue(
                    coordinator.prepare_run(generation=1, go_command="go nodes 64")
                )
                active = coordinator._run
                self._install_owners(coordinator, active)

                def _blocking_stop(instance):
                    blocked.set()
                    released.wait(timeout=5.0)

                manager.stop_instance = _blocking_stop  # type: ignore[assignment]
                canceller = threading.Thread(
                    target=coordinator.cancel,
                    kwargs={"generation": 1, "reason": "test"},
                    daemon=True,
                )
                canceller.start()
                if not blocked.wait(timeout=3.0):
                    self.skipTest("no dispatched owner to stop")
                # This needs `self._lock`. If the blocked write holds it, the
                # coordinator is wedged and so is the anchor's completion path.
                reader = threading.Thread(
                    target=coordinator._run_snapshot, args=(1,), daemon=True
                )
                reader.start()
                reader.join(timeout=2.0)
                still_locked = reader.is_alive()
            finally:
                released.set()
                manager.stop_instance = real_stop  # type: ignore[assignment]
                coordinator.close()
                manager.close()

            self.assertFalse(
                still_locked,
                "a blocking engine write held the coordinator lock",
            )

    def test_every_preparation_step_shares_one_pre_anchor_deadline(self):
        """`prepare_budget_s` is the cap for ALL pre-anchor setup.

        Giving the directory creation and the stream open a full window each
        let both finish just inside their own bound and delay the outward
        anchor by nearly twice the declared hard cap.
        """
        import controller.shadow as shadow_module

        budget_s = 0.30
        step_s = 0.20
        with tempfile.TemporaryDirectory() as tmp:
            path = write_shadow_config(Path(tmp))
            document = json.loads(path.read_text())
            document["shadow"]["prepare_budget_s"] = budget_s
            path.write_text(json.dumps(document), encoding="utf-8")

            manager = BackendManager.from_path(path)
            manager.start()
            coordinator = shadow_module.ShadowRunCoordinator(runtime=manager)
            # `mkdir(parents=True)` retries itself after creating a missing
            # parent, so an unconditional sleep fires TWICE and blows the whole
            # budget inside the first step -- the second step is then never
            # reached and this test cannot tell the two behaviours apart.
            # Pre-create the root and only slow a `mkdir` that really creates.
            coordinator.settings.replay_root.mkdir(parents=True, exist_ok=True)
            real_mkdir = shadow_module.Path.mkdir
            real_open = shadow_module.Path.open
            try:
                def _slow_mkdir(self, *args, **kwargs):
                    if "-g0000" in self.name and not self.exists():
                        time.sleep(step_s)
                    return real_mkdir(self, *args, **kwargs)

                def _slow_open(self, *args, **kwargs):
                    if self.suffix == ".jsonl":
                        time.sleep(step_s)
                    return real_open(self, *args, **kwargs)

                shadow_module.Path.mkdir = _slow_mkdir  # type: ignore[assignment]
                shadow_module.Path.open = _slow_open  # type: ignore[assignment]
                started = time.monotonic()
                coordinator.prepare_run(generation=1, go_command="go nodes 64")
                elapsed = time.monotonic() - started
            finally:
                shadow_module.Path.mkdir = real_mkdir  # type: ignore[assignment]
                shadow_module.Path.open = real_open  # type: ignore[assignment]
                coordinator.close()
                manager.close()

            # Each step alone (0.20s) fits inside the budget; only their SUM
            # does not. With a window each, both succeed and the outward anchor
            # waits ~0.40s for a bound declared as 0.30s.
            self.assertLess(
                elapsed,
                budget_s + 0.06,
                "pre-anchor setup exceeded the single declared budget "
                f"({elapsed:.3f}s against {budget_s}s)",
            )

    def test_one_wait_per_checkpoint_interval_not_one_per_owner(self):
        """Three owners must not make every check happen three intervals apart."""
        import controller.shadow as shadow_module

        interval = 0.15
        pending = [
            shadow_module._OwnerState(
                owner=f"o{i}", instance=f"i{i}", family="stockfish", roots=("e2e4",)
            )
            for i in range(3)
        ]
        started = time.monotonic()
        shadow_module.ShadowRunCoordinator._wait_slice(pending, interval)
        elapsed = time.monotonic() - started
        self.assertLess(
            elapsed,
            interval * 1.8,
            f"one slice waited {elapsed:.3f}s for {len(pending)} owners at {interval}s",
        )

    def test_quiescence_uses_one_deadline_for_both_waits(self):
        """`drain_timeout_s` is the hard bound, not the bound per wait."""
        import controller.shadow as shadow_module

        with tempfile.TemporaryDirectory() as tmp:
            manager = BackendManager.from_path(write_shadow_config(Path(tmp)))
            manager.start()
            coordinator = shadow_module.ShadowRunCoordinator(runtime=manager)
            timeout = 0.4
            try:
                self.assertTrue(
                    coordinator.prepare_run(generation=1, go_command="go nodes 64")
                )
                active = coordinator._run
                # A worker that never finishes: both waits must share one
                # deadline rather than each granting a fresh full window.
                active.worker = threading.Thread(
                    target=lambda: time.sleep(10.0), daemon=True
                )
                active.worker.start()
                started = time.monotonic()
                drained = coordinator.quiesce(timeout=timeout)
                elapsed = time.monotonic() - started
            finally:
                coordinator.close()
                manager.close()

            self.assertFalse(drained, "a worker that never finishes did not drain")
            self.assertLess(
                elapsed,
                timeout * 1.6,
                f"quiesce blocked for {elapsed:.3f}s against a {timeout}s bound",
            )

    def test_a_blocked_shadow_stream_is_bounded_like_the_anchor_one(self):
        """Stream setup at dispatch time was the one unbounded open left.

        Opening a later owner's telemetry file blocks the coordinator after
        earlier owners may already be searching: `_await_completion` is never
        reached, so no stage deadline and no active-routing wall check runs
        while those engines keep spending envelope, and a later quiesce cannot
        finish either. This exercises `_dispatch_stage` directly because that
        is where the unbounded call is; `_execute` reaches it for every stage.
        """
        import controller.shadow as shadow_module

        budget_s = 0.2
        with tempfile.TemporaryDirectory() as tmp:
            path = write_shadow_config(Path(tmp))
            document = json.loads(path.read_text())
            document["shadow"]["prepare_budget_s"] = budget_s
            path.write_text(json.dumps(document), encoding="utf-8")

            manager = BackendManager.from_path(path)
            manager.start()
            coordinator = shadow_module.ShadowRunCoordinator(runtime=manager)
            real_open = shadow_module.Path.open
            try:
                self.assertTrue(
                    coordinator.prepare_run(generation=1, go_command="go nodes 64")
                )
                active = coordinator._run
                state = self._install_owners(coordinator, active)[0]
                state.dispatched = False
                blocked_name = f"{state.instance}.jsonl"

                def _slow_open(self, *args, **kwargs):
                    if self.name == blocked_name:
                        time.sleep(5.0)
                    return real_open(self, *args, **kwargs)

                shadow_module.Path.open = _slow_open  # type: ignore[assignment]
                started = time.monotonic()
                dispatched = coordinator._dispatch_stage(
                    active, state, limit={"nodes": 64}
                )
                elapsed = time.monotonic() - started
            finally:
                shadow_module.Path.open = real_open  # type: ignore[assignment]
                coordinator.close()
                manager.close()

            self.assertLess(
                elapsed,
                budget_s + 0.4,
                f"a blocked shadow stream held the coordinator for {elapsed:.3f}s",
            )
            self.assertFalse(
                dispatched,
                "a stage was dispatched with no telemetry stream behind it",
            )

    def test_a_quiescence_timeout_marks_the_owner_failed(self):
        """A stuck owner's region may not be sealed as normally completed.

        The timeout path released the waiter without setting `state.failed`, so
        `_execute` still called `seal_owner` for it and the manifest carried a
        failed stage whose region was represented as complete.
        """
        import controller.shadow as shadow_module

        with tempfile.TemporaryDirectory() as tmp:
            manager = BackendManager.from_path(write_shadow_config(Path(tmp)))
            manager.start()
            coordinator = shadow_module.ShadowRunCoordinator(runtime=manager)
            try:
                self.assertTrue(
                    coordinator.prepare_run(generation=1, go_command="go nodes 64")
                )
                active = coordinator._run
                self._install_owners(coordinator, active)
                active.worker = threading.Thread(
                    target=lambda: time.sleep(10.0), daemon=True
                )
                active.worker.start()
                coordinator.quiesce(timeout=0.3)
                stuck = [s for s in active.owners.values() if s.dispatched]
                failed = [s.owner for s in stuck if s.failed]
                released = [s.owner for s in stuck if s.done.is_set()]
            finally:
                coordinator.close()
                manager.close()

            self.assertTrue(released, "the stuck owners were never released")
            self.assertEqual(
                sorted(failed),
                sorted(released),
                "an owner released by the quiescence timeout was not marked failed, "
                "so its region would still be sealed as completed",
            )


if __name__ == "__main__":
    unittest.main()

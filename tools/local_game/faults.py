"""Mandatory LOCAL-1 failure-injection lifecycle campaign.

These cases exercise controller/process/replay failure semantics with the repository's
real controller and real subprocess boundaries while using deterministic protocol fakes.
They are release-gate evidence for lifecycle handling, not chess-strength evidence.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import patch

from .common import load, require, safe_copy_regular_tree, save


def _terminals(output):
    return [line for line in output.getvalue().splitlines() if line.startswith("bestmove ")]


def _wait_replay_finalization(shadow, *, timeout: float = 15.0) -> None:
    """Wait until no controller generation can still mutate replay artifacts."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if shadow.replay_finalization_idle():
            return
        time.sleep(0.01)
    require(
        shadow.replay_finalization_idle(),
        "replay finalization did not become idle before evidence retention",
    )


def _copy_replays(source: Path, destination: Path) -> None:
    if source.is_dir():
        safe_copy_regular_tree(source, destination)


def run_faults(output: Path, policy: dict) -> dict:
    from controller.replay import discover_replay_bundles, load_manifest
    from tests.controller.online_helpers import shell_fixture, wait_for

    root = output / "fault-cases"
    root.mkdir()
    declared = [row["id"] for row in policy["fault_cases"]]
    rows = []

    # 1. A later observational worker crash must quarantine only that worker;
    # anchor authority and the following game remain alive.
    case = root / "shadow-worker-crash"
    case.mkdir()
    with shell_fixture(args={"reckless-shadow": ["--exit-on-go-number", "2"]}) as (
        shell, manager, shadow, out, tmp
    ):
        shell.handle_command("go movetime 500")
        wait_for(lambda: len(_terminals(out)) == 1)
        wait_for(lambda: len(list(tmp.glob("replays/*/manifest.json"))) == 1, timeout=5)
        shell.handle_command("position startpos moves e2e4")
        shell.handle_command("go movetime 500")
        wait_for(lambda: len(_terminals(out)) == 2)
        wait_for(lambda: not manager.shadow_available("reckless-shadow"), timeout=5)
        shell.handle_command("ucinewgame")
        shell.handle_command("position startpos")
        shell.handle_command("go movetime 500")
        wait_for(lambda: len(_terminals(out)) == 3)
        _wait_replay_finalization(shadow)
        _copy_replays(tmp / "replays", case / "replays")
        rows.append({
            "id": "shadow-worker-crash",
            "passed": bool(manager.healthy and not manager.shadow_available("reckless-shadow")
                           and len(_terminals(out)) == 3
                           and all(line != "bestmove 0000" for line in _terminals(out))),
            "anchor_healthy": manager.healthy,
            "shadow_quarantined": not manager.shadow_available("reckless-shadow"),
            "terminal_count": len(_terminals(out)),
        })

    # 2. A shadow that ignores stop across a state transition must be quarantined
    # rather than allowed to observe the next game.
    case = root / "slow-shadow-shutdown"
    case.mkdir()
    with shell_fixture(args={"reckless-shadow": [
        "--info-lines", "2000", "--info-delay-ms", "10", "--ignore-stop"
    ]}) as (shell, manager, shadow, out, tmp):
        shell.handle_command("go movetime 500")
        wait_for(lambda: len(_terminals(out)) == 1)
        shell.handle_command("ucinewgame")
        quarantined = not manager.shadow_available("reckless-shadow")
        shell.handle_command("position startpos")
        shell.handle_command("go movetime 500")
        wait_for(lambda: len(_terminals(out)) == 2)
        _wait_replay_finalization(shadow)
        _copy_replays(tmp / "replays", case / "replays")
        rows.append({
            "id": "slow-shadow-shutdown",
            "passed": bool(manager.healthy and quarantined and len(_terminals(out)) == 2),
            "anchor_healthy": manager.healthy,
            "shadow_quarantined": quarantined,
            "terminal_count": len(_terminals(out)),
        })

    # 3. Replay persistence failure may not rewrite the already-published move,
    # but it must remain visibly missing/incomplete evidence.
    case = root / "replay-storage-failure"
    case.mkdir()
    import controller.replay as replay
    original = replay.atomic_write_text
    failed = threading.Event()

    def fail_manifest(path, text):
        if Path(path).name == "manifest.json":
            failed.set()
            raise OSError("LOCAL-1 injected replay storage failure")
        return original(path, text)

    with shell_fixture() as (shell, manager, shadow, out, tmp):
        with patch("controller.replay.atomic_write_text", side_effect=fail_manifest):
            shell.handle_command("go movetime 500")
            wait_for(lambda: len(_terminals(out)) == 1)
            require(failed.wait(5), "storage injection was not reached")
            _wait_replay_finalization(shadow)
        discovery = discover_replay_bundles(tmp / "replays")
        _copy_replays(tmp / "replays", case / "replays")
        rows.append({
            "id": "replay-storage-failure",
            "passed": bool(
                manager.healthy
                and len(_terminals(out)) == 1
                and _terminals(out)[0] != "bestmove 0000"
                and bool(discovery.skipped)
                and not discovery.bundles
            ),
            "outward_move_preserved": len(_terminals(out)) == 1 and _terminals(out)[0] != "bestmove 0000",
            "evidence_failure_observed": bool(discovery.skipped) and not discovery.bundles,
        })

    # 4. Stop/game termination while work is active must linearize to one
    # terminal output and permit a fresh next game.
    case = root / "active-game-termination"
    case.mkdir()
    with shell_fixture(args={"stockfish-anchor": [
        "--info-lines", "500", "--info-delay-ms", "10"
    ]}) as (shell, manager, shadow, out, tmp):
        shell.handle_command("go movetime 1200")
        time.sleep(0.05)
        shell.handle_command("stop")
        wait_for(lambda: len(_terminals(out)) == 1, timeout=3)
        shell.handle_command("isready")
        shell.handle_command("ucinewgame")
        shell.handle_command("position startpos")
        shell.handle_command("go movetime 500")
        wait_for(lambda: len(_terminals(out)) == 2, timeout=3)
        _wait_replay_finalization(shadow)
        _copy_replays(tmp / "replays", case / "replays")
        rows.append({
            "id": "active-game-termination",
            "passed": bool(manager.healthy and len(_terminals(out)) == 2
                           and all(line != "bestmove 0000" for line in _terminals(out))),
            "anchor_healthy": manager.healthy,
            "terminal_count": len(_terminals(out)),
            "next_game_ready": len(_terminals(out)) == 2,
        })

    # 5. Long generation reuse: many plies/games with the replay barrier fully
    # finalized before the next search. This catches generation/process leakage.
    case = root / "long-generation-reuse"
    case.mkdir()
    with shell_fixture(args={"stockfish-anchor": [
        "--info-lines", "3", "--info-delay-ms", "20"
    ]}) as (shell, manager, shadow, out, tmp):
        expected = 0
        for game in range(3):
            shell.handle_command("ucinewgame")
            for turn in range(4):
                shell.handle_command("position startpos")
                shell.handle_command("go movetime 500")
                expected += 1
                wait_for(lambda: len(_terminals(out)) == expected, timeout=3)
                shell.handle_command("isready")
                wait_for(
                    lambda: len(list(tmp.glob("replays/*/manifest.json"))) == expected,
                    timeout=5,
                )
        _wait_replay_finalization(shadow)
        bundles = discover_replay_bundles(tmp / "replays")
        generations = [load_manifest(path)["generation"] for path in bundles.bundles]
        _copy_replays(tmp / "replays", case / "replays")
        rows.append({
            "id": "long-generation-reuse",
            "passed": bool(
                manager.healthy
                and len(_terminals(out)) == 12
                and not bundles.skipped
                and len(bundles.bundles) == 12
                and len(generations) == len(set(generations))
            ),
            "anchor_healthy": manager.healthy,
            "terminal_count": len(_terminals(out)),
            "replay_count": len(bundles.bundles),
            "unique_generations": len(set(generations)),
        })

    require([row["id"] for row in rows] == declared,
            "fault campaign differs from frozen policy")
    report = {
        "schema_version": 1,
        "kind": "mandatory-controller-failure-injection-lifecycle",
        "declared_cases": declared,
        "cases": rows,
        "passed": all(row["passed"] for row in rows),
        "claim": "Controller/process/replay failure semantics only; no chess-strength claim.",
    }
    save(root / "report.json", report)
    return report


def verify_faults(output: Path, declared: dict, policy: dict) -> None:
    root = output / "fault-cases"
    report = load(root / "report.json")
    require(report == declared, "fault report differs from campaign manifest")
    expected = [row["id"] for row in policy["fault_cases"]]
    require(report.get("declared_cases") == expected, "missing/extra/reordered fault cases")
    require([row.get("id") for row in report.get("cases", [])] == expected,
            "fault case results do not match frozen schedule")
    require(report.get("passed") is True, "mandatory failure-injection lifecycle did not pass")
    for row in report["cases"]:
        require(row.get("passed") is True, f"fault case failed: {row.get('id')}")
    storage = next(row for row in report["cases"] if row["id"] == "replay-storage-failure")
    require(storage.get("outward_move_preserved") is True and
            storage.get("evidence_failure_observed") is True,
            "storage failure was hidden or rewrote authority")
    long = next(row for row in report["cases"] if row["id"] == "long-generation-reuse")
    require(long.get("terminal_count") == long.get("replay_count") ==
            long.get("unique_generations") == 12,
            "long generation-reuse evidence is incomplete")

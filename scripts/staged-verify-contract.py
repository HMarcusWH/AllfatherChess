#!/usr/bin/env python3
"""Real-engine M14-G1 same-process staged VERIFY contract.

The contract proves the mechanism and resource/authority boundaries only:
one EXPLORE generation, one base VERIFY round, then one fresh larger-budget
VERIFY extension on the same three managed solver processes and candidate set.

It does not require a decision change and makes no correctness, Elo, or strength
claim.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from controller.replay import discover_replay_bundles, load_manifest
from controller.staged_verification import (
    StagedVerificationError,
    load_staged_verification_manifest,
    verify_staged_verification_integrity,
)
from controller.verification import load_verification_manifest
from tests.harness.uci_session import UciError, UciSession


CONFIG = ROOT / "config" / "allfather.staged-verify.validation.json"
RESULT_DIR = ROOT / "build" / "test-results" / "staged-verify"
REPLAY_ROOT = RESULT_DIR / "replays"
ANCHOR_MOVETIME_MS = 5000


class ContractError(RuntimeError):
    pass


def _write_config() -> Path:
    doc = json.loads(CONFIG.read_text(encoding="utf-8"))
    doc["root"] = str(ROOT)
    doc["shadow"]["replay_root"] = str(REPLAY_ROOT.resolve())
    path = RESULT_DIR / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(doc, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _wait_run(known: set[str], *, timeout: float = 30.0) -> Path:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        discovery = discover_replay_bundles(REPLAY_ROOT)
        fresh = [path for path in discovery.bundles if path.name not in known]
        for candidate in reversed(fresh):
            staged = candidate / "staged_verification" / "manifest.json"
            route = candidate / "route.json"
            resource = candidate / "resource.json"
            if staged.is_file() and route.is_file() and resource.is_file():
                return candidate
            manifest_path = candidate / "manifest.json"
            if manifest_path.is_file():
                manifest = load_manifest(candidate)
                if (manifest.get("disposition") or {}).get("run") in {
                    "aborted",
                    "cancelled",
                }:
                    raise ContractError(
                        "staged VERIFY run terminated before extension evidence "
                        f"sealed: {manifest.get('notes')}"
                    )
        time.sleep(0.05)
    raise ContractError("timed out waiting for staged VERIFY evidence")


def _run() -> tuple[Path, str]:
    REPLAY_ROOT.mkdir(parents=True, exist_ok=True)
    discovery = discover_replay_bundles(REPLAY_ROOT)
    known = {path.name for path in discovery.bundles} | {
        item.path.name for item in discovery.skipped
    }
    config = _write_config()
    with UciSession(
        Path(sys.executable),
        cwd=ROOT,
        timeout=60.0,
        args=["-m", "controller", "--config", str(config)],
    ) as shell:
        shell.configure({"UCI_Chess960": False})
        shell.new_game()
        shell.set_position({"startpos_moves": []})
        shell.send(f"go movetime {ANCHOR_MOVETIME_MS}")
        lines = shell.read_until(
            lambda line: line.startswith("bestmove "),
            label="staged VERIFY outward bestmove",
            timeout=90.0,
        )
        outward = next(
            line.split()[1].lower()
            for line in lines
            if line.startswith("bestmove ")
        )
        return _wait_run(known), outward


def main() -> int:
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    run_dir, outward = _run()

    problems = verify_staged_verification_integrity(run_dir)
    if problems:
        raise ContractError(f"staged VERIFY integrity failed: {problems}")

    parent = load_manifest(run_dir)
    base = load_verification_manifest(run_dir)
    staged = load_staged_verification_manifest(run_dir)
    route = json.loads((run_dir / "route.json").read_text(encoding="utf-8"))
    resource = json.loads((run_dir / "resource.json").read_text(encoding="utf-8"))

    anchor = next(
        stage
        for stage in parent.get("stages") or []
        if stage.get("role") == "anchor"
    )
    if anchor.get("bestmove") != outward:
        raise ContractError(
            f"outward bestmove {outward} differs from recorded anchor "
            f"{anchor.get('bestmove')}"
        )

    base_candidates = tuple(
        (base.get("nomination") or {}).get("candidate_roots") or ()
    )
    ext_candidates = tuple(
        (staged.get("nomination") or {}).get("candidate_roots") or ()
    )
    if base_candidates != ext_candidates:
        raise ContractError("extension changed the base candidate universe")
    if staged.get("participants") != base.get("participants"):
        raise ContractError("extension changed solver process identities")
    if (staged.get("authority") or {}).get("outward_move") is not False:
        raise ContractError("staged VERIFY artifact acquired outward authority")
    if (staged.get("authority") or {}).get("routing") is not False:
        raise ContractError("staged VERIFY artifact acquired routing authority")

    base_stages = base.get("stages") or []
    extension_stages = staged.get("stages") or []
    if len(base_stages) != 3 or len(extension_stages) != 3:
        raise ContractError("expected exactly three base and three extension stages")
    if any(stage.get("disposition") != "completed" for stage in base_stages):
        raise ContractError("base VERIFY did not complete cleanly")
    if any(stage.get("disposition") != "completed" for stage in extension_stages):
        raise ContractError("staged VERIFY extension did not complete cleanly")

    actions = route.get("specialist_actions") or []
    verify_grants = [
        item
        for item in actions
        if item.get("event") == "authorize"
        and item.get("phase") == "verify"
        and item.get("granted")
    ]
    extension_grants = [
        item for item in verify_grants
        if item.get("target_id") == "staged_extension"
    ]
    if len(verify_grants) != 6 or len(extension_grants) != 3:
        raise ContractError(
            "expected six reservation-backed VERIFY dispatches "
            "(three base + three extension)"
        )
    if (route.get("budget") or {}).get("open_reservations") != 0:
        raise ContractError("specialist reservations did not settle")

    phases = {
        item.get("phase")
        for item in resource.get("stages") or []
        if isinstance(item, dict)
    }
    if "VERIFY" not in phases or "VERIFY_EXTENSION" not in phases:
        raise ContractError(
            "resource report does not separately measure both VERIFY rounds"
        )

    report = {
        "schema_version": 1,
        "config": str(CONFIG.relative_to(ROOT)),
        "run_id": parent.get("run_id"),
        "outward_bestmove": outward,
        "intervention": staged.get("intervention"),
        "base_dispatch_limit": base.get("dispatch_limit"),
        "extension_dispatch_limit": (
            (staged.get("rounds") or {}).get("extension") or {}
        ).get("dispatch_limit"),
        "candidate_roots": list(base_candidates),
        "base_stages": len(base_stages),
        "extension_stages": len(extension_stages),
        "verify_authorizations": len(verify_grants),
        "extension_authorizations": len(extension_grants),
        "claim": (
            "Mechanism evidence only. A clean base VERIFY round was followed by "
            "one fresh larger-budget same-process VERIFY extension over the exact "
            "same candidate universe with separate resource authorization and "
            "measurement. No move-correctness, Elo, strength, or compute-value "
            "claim follows."
        ),
    }
    (RESULT_DIR / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        "staged VERIFY contract passed: "
        f"run={parent.get('run_id')}, outward={outward}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        ContractError,
        StagedVerificationError,
        UciError,
        OSError,
        ValueError,
    ) as exc:
        print(f"staged VERIFY contract failed: {exc}", file=sys.stderr)
        raise SystemExit(1)

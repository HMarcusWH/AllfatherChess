"""Real-binary, real-network LC0 game-runner preflight; never a release claim.

The game runner differs from the source builder. Do not infer its native
dependencies or backend availability from successful build/LOCAL-1 tests.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import threading

from tools.local_game.common import (
    ROOT, load, require, save, sha, source_identity,
)
from tools.engine_opt.domain import candidate_bundle_identity

PILOT_POLICY = ROOT / "qualification/g3-pilot-v1.json"
REPORT_KIND = "real-lc0-uci-game-host-preflight-v1"


def check_linker_output(returncode: int, output: str) -> None:
    """Require a real dynamic-linker survey with no unresolved dependencies."""
    require(type(returncode) is int and returncode == 0,
            "LC0 loader dependency check returned nonzero")
    require(type(output) is str and bool(output.strip())
            and len(output.encode("utf-8")) <= 64_000,
            "LC0 loader diagnostic absent or unbounded")
    require("not found" not in output.lower()
            and "error while loading shared libraries" not in output.lower(),
            "LC0 has missing native shared libraries on the game host")


def verify_preflight(report: dict, source: dict | None = None) -> dict:
    """Check source-host binding, not merely the producer's 'passed' flag."""
    identity = source_identity() if source is None else source
    p = load(PILOT_POLICY)
    runtime = load(ROOT / p["source_runtime"])
    lc0 = runtime["instances"]["lc0-shadow"]
    binary = ROOT / lc0["binary"]
    network = ROOT / lc0["options"]["WeightsFile"]
    require(type(report) is dict and report.get("schema_version") == 1
            and report.get("kind") == REPORT_KIND
            and report.get("passed") is True
            and report.get("errors") == [],
            "LC0 game-host preflight was not successful")
    require(report.get("source") == identity
            and report.get("candidate_bundle")
            == candidate_bundle_identity(ROOT / p["bundle_root"],
                                        expected_source_commit=identity["commit"]),
            "LC0 preflight source/bundle differs from this campaign")
    require(report.get("binary_sha256") == sha(binary)
            and report.get("network_sha256") == sha(network),
            "LC0 preflight did not bind the actual source-selected engine/network")
    require(report.get("backend") == "blas"
            and report.get("uciok") is True
            and report.get("readyok") is True
            and report.get("legal_terminal_bestmove") is True
            and report.get("go_nodes") == 32
            and report.get("process_reaped") is True,
            "LC0 game-host did not prove real UCI/BLAS search and lifecycle")
    require(report.get("runtime_options") == lc0["options"],
            "LC0 preflight changed selected b4 runtime options")
    check_linker_output(report.get("linker_returncode"),
                        report.get("linker_output"))
    stderr = report.get("stderr_tail")
    require(type(stderr) is list and len(stderr) <= 200
            and all(type(row) is str and len(row) <= 4096 for row in stderr)
            and any(row.startswith("BLAS vendor:") for row in stderr)
            and any(row.startswith("BLAS max batch size is ") for row in stderr),
            "LC0 preflight cannot prove the real BLAS backend")
    bestmove = report.get("bestmove")
    require(type(bestmove) is str
            and re.fullmatch(r"[a-h][1-8][a-h][1-8][qrbn]?", bestmove),
            "LC0 preflight bestmove malformed")
    # Avoid adding python-chess as a dependency to source-contract tests.
    import chess
    require(chess.__version__ == "1.11.2"
            and chess.Move.from_uci(bestmove) in chess.Board().legal_moves,
            "LC0 preflight move is not legal under the pinned rules")
    return report


def run(output: Path) -> int:
    from adapters.process import UciProcess

    output = output.resolve()
    require(output.is_relative_to(ROOT / "build")
            and not output.is_symlink(),
            "LC0 preflight report must stay inside build/")
    source = source_identity()
    p = load(PILOT_POLICY)
    require(p["live_api_permitted"] is False
            and p["public_bot_release"] is False
            and p["host_j12_authority_qualified"] is False,
            "LC0 pilot preflight cannot promote external permissions")
    runtime = load(ROOT / p["source_runtime"])
    selected = runtime["instances"]["lc0-shadow"]
    binary = ROOT / selected["binary"]
    network = ROOT / selected["options"]["WeightsFile"]
    candidate = candidate_bundle_identity(
        ROOT / p["bundle_root"], expected_source_commit=source["commit"])
    record = {
        "schema_version": 1, "kind": REPORT_KIND, "source": source,
        "candidate_bundle": candidate, "binary_sha256": sha(binary),
        "network_sha256": sha(network),
        "runtime_options": selected["options"],
        "go_nodes": 32,
        "backend": None, "bestmove": None,
        "uciok": False, "readyok": False,
        "legal_terminal_bestmove": False,
        "process_reaped": False,
        "linker_returncode": None, "linker_output": "",
        "stderr_tail": [], "passed": False, "errors": [],
        "j12_authority_qualified": False,
        "live_game_authorized": False,
        "playing_strength_qualified": False,
    }
    process = None
    try:
        require(binary.is_file() and network.is_file(),
                "LC0 game-host binary or network missing")
        linker = subprocess.run(
            ["ldd", str(binary)], check=False,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=20,
        )
        record["linker_returncode"] = linker.returncode
        record["linker_output"] = linker.stdout[:64000]
        check_linker_output(linker.returncode, linker.stdout)
        process = UciProcess(
            name="pilot-lc0-b4-game-host",
            binary=binary, args=list(selected.get("args") or []),
            cwd=ROOT, environment=selected.get("environment") or {},
            timeout=60.0,
        )
        process.start()  # real UCI / uciok
        record["uciok"] = True
        process.configure(selected["options"])
        process.ready()  # real isready / readyok after network loading
        record["readyok"] = True
        process.new_game()
        process.send_position("position startpos")
        done = threading.Event()
        completion: list[str] = []

        def on_complete(_token: int, line: str) -> None:
            completion.append(line)
            done.set()

        process.start_search(
            "go nodes 32", token=1,
            on_info=lambda _token, _line: None,
            on_complete=on_complete, timeout=60.0,
        )
        require(done.wait(90.0), "LC0 game-host real network search timed out")
        require(len(completion) == 1,
                "LC0 game-host returned missing/duplicate bestmove")
        match = re.fullmatch(
            r"bestmove ([a-h][1-8][a-h][1-8][qrbn]?)"
            r"(?: ponder [a-h][1-8][a-h][1-8][qrbn]?)?",
            completion[0],
        )
        require(match is not None, "LC0 game-host terminal move is malformed")
        record["bestmove"] = match.group(1)
        record["legal_terminal_bestmove"] = True  # independently rechecked below
        record["stderr_tail"] = [line[:4096] for line in process.stderr_tail]
        record["backend"] = "blas"
    except Exception as exc:
        record["errors"].append(f"{type(exc).__name__}: {exc}")
    finally:
        if process is not None:
            try:
                process.close()
                record["process_reaped"] = process.proc is None
            except Exception as exc:
                record["errors"].append(f"LC0 cleanup: {type(exc).__name__}: {exc}")
        try:
            require(source_identity() == source,
                    "LC0 preflight altered tracked or untracked repository source")
            if not record["errors"]:
                # Validate every positive fact, including actual legal bestmove.
                record["passed"] = True
                verify_preflight(record, source)
        except Exception as exc:
            record["passed"] = False
            record["errors"].append(f"{type(exc).__name__}: {exc}")
        save(output, record)
    print(json.dumps({
        "passed": record["passed"], "backend": record["backend"],
        "bestmove": record["bestmove"], "process_reaped": record["process_reaped"],
        "errors": record["errors"],
        "live_game_authorized": False,
    }, sort_keys=True), flush=True)
    return 0 if record["passed"] else 1


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()
    return run(args.output)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run the three real engines through telemetry v1 adapters and validate JSONL output."""

from __future__ import annotations

import glob
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters.telemetry import Lc0TelemetryAdapter, RecklessTelemetryAdapter, StockfishTelemetryAdapter
from common.telemetry import STARTPOS_FEN
from tests.harness.uci_session import UciSession

PROFILES_PATH = ROOT / "tests" / "baseline" / "profiles.json"
RESULT_DIR = ROOT / "build" / "test-results" / "telemetry-adapters"


class IntegrationError(RuntimeError):
    pass


def load_profiles() -> dict[str, Any]:
    return json.loads(PROFILES_PATH.read_text(encoding="utf-8"))["profiles"]


def resolve_binary(profile: dict[str, Any]) -> Path:
    direct = ROOT / profile["binary"]
    if direct.is_file():
        return direct
    pattern = profile.get("fallback_glob")
    if pattern:
        matches = sorted(
            Path(value)
            for value in glob.glob(str(ROOT / pattern), recursive=True)
            if Path(value).is_file()
        )
        if matches:
            return matches[0]
    raise IntegrationError(f"engine binary not found for profile: {profile}")


def request(nodes: int) -> dict[str, Any]:
    return {
        "limits": [
            {
                "name": "nodes",
                "value": nodes,
                "semantics": "uci.go.nodes",
            }
        ]
    }


def write_jsonl(path: Path, events: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n" for event in events),
        encoding="utf-8",
    )


def assert_stream(events: list[dict[str, Any]], engine: str, *, require_defect_summary: bool = False) -> None:
    types = [event["event_type"] for event in events]
    if not types or types[0] != "search.started":
        raise IntegrationError(f"{engine}: stream does not start with search.started")
    if types[-1] != "search.complete":
        raise IntegrationError(f"{engine}: stream does not end with search.complete")
    if "candidate.update" not in types:
        raise IntegrationError(f"{engine}: live search emitted no candidate.update")
    if any(event["engine"] != engine for event in events):
        raise IntegrationError(f"{engine}: engine identity changed in stream")
    if require_defect_summary:
        schemas = {
            event.get("native", {}).get("schema")
            for event in events
            if event["event_type"] == "native.event"
        }
        if "lc0.defect.summary.v1" not in schemas:
            raise IntegrationError("lc0 defect run emitted no DEFECT_TELEMETRY_SUMMARY")


def run_engine(
    engine: str,
    profile: dict[str, Any],
    *,
    nodes: int = 512,
    defect: bool = False,
) -> tuple[str, list[dict[str, Any]]]:
    binary = resolve_binary(profile)
    options = dict(profile.get("options", {}))
    args: list[str] = []

    if engine == "stockfish":
        options["UCI_ShowWDL"] = True
        adapter = StockfishTelemetryAdapter(
            search_id="integration-stockfish",
            engine_instance="stockfish-0",
            position_id="corpus:startpos",
        )
        filename = "stockfish.jsonl"
    elif engine == "reckless":
        adapter = RecklessTelemetryAdapter(
            search_id="integration-reckless",
            engine_instance="reckless-0",
            position_id="corpus:startpos",
        )
        filename = "reckless.jsonl"
    elif engine == "lc0":
        options["ScoreType"] = "centipawn"
        options["UCI_ShowWDL"] = True
        if defect:
            args = ["--show-hidden"]
            options["DefectTelemetry"] = True
            options["DefectTelemetryIterations"] = 2
            search_id = "integration-lc0-defect"
            filename = "lc0-defect.jsonl"
        else:
            search_id = "integration-lc0"
            filename = "lc0.jsonl"
        adapter = Lc0TelemetryAdapter(
            search_id=search_id,
            engine_instance="lc0-0",
            position_id="corpus:startpos",
            score_type="centipawn",
            defer_completion_until_flush=defect,
        )
    else:
        raise IntegrationError(f"unsupported engine: {engine}")

    events: list[dict[str, Any]] = []
    with UciSession(binary, cwd=ROOT, timeout=15.0, args=args) as session:
        session.configure(options)
        session.new_game()
        session.set_position({"startpos_moves": []})

        events.append(
            adapter.start(
                position={"base_fen": STARTPOS_FEN, "moves": []},
                request=request(nodes),
                observed_ms=0,
            )
        )
        started_at = time.monotonic()

        def observe(line: str) -> None:
            observed_ms = int((time.monotonic() - started_at) * 1000)
            events.extend(adapter.consume(line, observed_ms=observed_ms))

        session.search_nodes(nodes, timeout=30.0, observer=observe)

        if defect:
            # LC0 emits defect telemetry when the current Search object is
            # destroyed. Force that lifecycle transition after bestmove, while
            # keeping search.complete last in the normalized telemetry stream.
            session.send("ucinewgame")
            session.send("isready")

            def observe_flush(line: str) -> None:
                if line == "readyok":
                    return
                observe(line)

            session.read_until(
                lambda line: line == "readyok",
                label="lc0 defect telemetry flush",
                timeout=10.0,
                observer=observe_flush,
            )
            final_ms = int((time.monotonic() - started_at) * 1000)
            events.append(adapter.flush_completion(observed_ms=final_ms))

    assert_stream(events, engine, require_defect_summary=defect)
    return filename, events


def main() -> int:
    profiles = load_profiles()
    RESULT_DIR.mkdir(parents=True, exist_ok=True)

    runs = [
        run_engine("stockfish", profiles["stockfish"]),
        run_engine("reckless", profiles["reckless"]),
        run_engine("lc0", profiles["lc0"]),
        run_engine("lc0", profiles["lc0"], defect=True),
    ]

    manifest: dict[str, Any] = {"schema_version": 1, "streams": []}
    paths: list[Path] = []
    for filename, events in runs:
        path = RESULT_DIR / filename
        write_jsonl(path, events)
        paths.append(path)
        raw = path.read_bytes()
        manifest["streams"].append(
            {
                "file": filename,
                "events": len(events),
                "candidate_updates": sum(event["event_type"] == "candidate.update" for event in events),
                "native_events": sum(event["event_type"] == "native.event" for event in events),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )

    manifest_path = RESULT_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "validate-telemetry-contract.py"),
            *[str(path.relative_to(ROOT)) for path in paths],
        ],
        cwd=ROOT,
        check=True,
    )

    print(
        "telemetry adapter integration passed: "
        + ", ".join(f"{item['file']}={item['events']} events" for item in manifest["streams"])
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (IntegrationError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        RESULT_DIR.mkdir(parents=True, exist_ok=True)
        message = f"telemetry adapter integration failure: {exc}"
        print(message, file=sys.stderr)
        (RESULT_DIR / "failure.txt").write_text(message + "\n", encoding="utf-8")
        raise SystemExit(1)

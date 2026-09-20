#!/usr/bin/env python3
"""Record or verify the frozen three-engine behavioral baseline."""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.harness.normalize import is_uci_move, normalize_search
from tests.harness.uci_session import UciError, UciSession

CORPUS_PATH = ROOT / "tests" / "baseline" / "corpus.json"
PROFILES_PATH = ROOT / "tests" / "baseline" / "profiles.json"
DEFAULT_GOLDEN_DIR = ROOT / "tests" / "baseline" / "golden"
RESULT_DIR = ROOT / "build" / "test-results" / "golden"
PERFT_MOVE_RE = re.compile(r"^([a-h][1-8][a-h][1-8][qrbn]?):\s+\d+\s*$")


class GoldenError(RuntimeError):
    pass


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_binary(profile: dict[str, Any]) -> Path:
    direct = ROOT / profile["binary"]
    if direct.is_file():
        return direct
    pattern = profile.get("fallback_glob")
    if pattern:
        matches = sorted(Path(p) for p in glob.glob(str(ROOT / pattern), recursive=True) if Path(p).is_file())
        if matches:
            return matches[0]
    raise GoldenError(f"engine binary not found for profile: {profile}")


def position_command(case: dict[str, Any], session: UciSession) -> None:
    session.set_position(case["position"])


def extract_perft_moves(lines: list[str]) -> list[str]:
    moves: set[str] = set()
    for line in lines:
        match = PERFT_MOVE_RE.match(line.strip())
        if match:
            moves.add(match.group(1).lower())
    return sorted(moves)


def derive_legal_moves(engine: str, binary: Path, case: dict[str, Any]) -> list[str]:
    with UciSession(binary, cwd=ROOT, timeout=10.0) as session:
        position_command(case, session)
        if engine == "stockfish":
            lines = session.synchronous_command("go perft 1", timeout=10.0)
        elif engine == "reckless":
            lines = session.synchronous_command("simpleperft 1", timeout=10.0)
        else:
            raise GoldenError(f"no legal-move oracle defined for {engine}")
    return extract_perft_moves(lines)


def derive_cross_checked_legal_moves(
    corpus: dict[str, Any], binaries: dict[str, Path]
) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for case in corpus["cases"]:
        case_id = case["id"]
        stockfish = derive_legal_moves("stockfish", binaries["stockfish"], case)
        reckless = derive_legal_moves("reckless", binaries["reckless"], case)
        if stockfish != reckless:
            only_sf = sorted(set(stockfish) - set(reckless))
            only_rk = sorted(set(reckless) - set(stockfish))
            raise GoldenError(
                f"{case_id}: legal-move oracles disagree; "
                f"stockfish_only={only_sf} reckless_only={only_rk}"
            )
        terminal = bool(case.get("terminal", False))
        if terminal != (len(stockfish) == 0):
            raise GoldenError(
                f"{case_id}: corpus terminal={terminal} but legal move count={len(stockfish)}"
            )
        result[case_id] = stockfish
    return result


def run_case(
    engine: str,
    binary: Path,
    profile: dict[str, Any],
    case: dict[str, Any],
    legal_moves: list[str],
) -> dict[str, Any]:
    with UciSession(binary, cwd=ROOT, timeout=12.0) as session:
        session.configure(profile.get("options", {}))
        session.new_game()
        position_command(case, session)
        lines = session.search_nodes(int(case["nodes"]), timeout=20.0)
        normalized = normalize_search(lines)

    bestmove = normalized["bestmove"]
    terminal = bool(case.get("terminal", False))
    if terminal:
        if bestmove is not None:
            raise GoldenError(f"{engine}/{case['id']}: terminal position returned {bestmove}")
    else:
        if bestmove is None:
            raise GoldenError(f"{engine}/{case['id']}: non-terminal position returned no move")
        if bestmove not in legal_moves:
            raise GoldenError(
                f"{engine}/{case['id']}: illegal bestmove {bestmove}; "
                f"legal={legal_moves}"
            )

    pv = normalized.get("pv")
    if pv:
        if not all(is_uci_move(move) for move in pv):
            raise GoldenError(f"{engine}/{case['id']}: malformed PV {pv}")
        if bestmove is not None and pv[0] != bestmove:
            raise GoldenError(
                f"{engine}/{case['id']}: PV does not start with bestmove: {pv[0]} != {bestmove}"
            )
    return normalized


def stable_projection(observations: list[dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    if not observations:
        raise GoldenError("no observations to stabilize")
    all_keys = sorted(set().union(*(obs.keys() for obs in observations)))
    stable: dict[str, Any] = {}
    unstable: list[str] = []
    for key in all_keys:
        present = all(key in obs for obs in observations)
        if present and all(obs[key] == observations[0][key] for obs in observations[1:]):
            stable[key] = observations[0][key]
        else:
            unstable.append(key)
    if "bestmove" not in stable:
        raise GoldenError(f"bestmove is not deterministic across runs: {observations}")
    return stable, unstable


def record_engine(
    engine: str,
    binary: Path,
    profile: dict[str, Any],
    corpus: dict[str, Any],
    legal: dict[str, list[str]],
    stability_runs: int,
    corpus_sha: str,
    profiles_sha: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    golden_cases: dict[str, Any] = {}
    actual_cases: dict[str, Any] = {}
    for case in corpus["cases"]:
        observations = [
            run_case(engine, binary, profile, case, legal[case["id"]])
            for _ in range(stability_runs)
        ]
        stable, unstable = stable_projection(observations)
        golden_cases[case["id"]] = {
            "stable": stable,
            "unstable_fields": unstable,
        }
        actual_cases[case["id"]] = observations

    golden = {
        "schema_version": 1,
        "engine": engine,
        "corpus_sha256": corpus_sha,
        "profiles_sha256": profiles_sha,
        "stability_runs": stability_runs,
        "cases": golden_cases,
    }
    actual = {"engine": engine, "mode": "record", "cases": actual_cases}
    return golden, actual


def verify_engine(
    engine: str,
    binary: Path,
    profile: dict[str, Any],
    corpus: dict[str, Any],
    legal: dict[str, list[str]],
    expected: dict[str, Any],
    corpus_sha: str,
    profiles_sha: str,
) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    if expected.get("corpus_sha256") != corpus_sha:
        errors.append(f"{engine}: corpus hash changed; re-record intentionally")
    if expected.get("profiles_sha256") != profiles_sha:
        errors.append(f"{engine}: profile hash changed; re-record intentionally")

    actual_cases: dict[str, Any] = {}
    for case in corpus["cases"]:
        case_id = case["id"]
        actual = run_case(engine, binary, profile, case, legal[case_id])
        actual_cases[case_id] = actual
        expected_case = expected.get("cases", {}).get(case_id)
        if expected_case is None:
            errors.append(f"{engine}/{case_id}: missing committed golden case")
            continue
        for key, expected_value in expected_case.get("stable", {}).items():
            actual_value = actual.get(key, "<missing>")
            if actual_value != expected_value:
                errors.append(
                    f"{engine}/{case_id}/{key}: expected={expected_value!r} actual={actual_value!r}"
                )
    return errors, {"engine": engine, "mode": "verify", "cases": actual_cases}


def parse_origin(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path.is_file():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result[key] = value
    return result


def git_head() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return None


def build_manifest(
    binaries: dict[str, Path], corpus_sha: str, profiles_sha: str
) -> dict[str, Any]:
    lock = load_json(ROOT / "vendor.lock.json")
    engine_meta: dict[str, Any] = {}
    for engine, binary in binaries.items():
        entry = lock["engines"][engine]
        item: dict[str, Any] = {
            "binary": str(binary.relative_to(ROOT)),
            "binary_sha256": sha256_file(binary),
            "origin": parse_origin(ROOT / entry["destination"] / ".allfather-origin"),
            "locked_artifacts": entry.get("artifacts", {}),
        }
        actual_artifacts: dict[str, Any] = {}
        for artifact_name, artifact in entry.get("artifacts", {}).items():
            candidate = ROOT / "build" / "artifacts" / engine / artifact["filename"]
            if candidate.is_file():
                actual_artifacts[artifact_name] = {
                    "path": str(candidate.relative_to(ROOT)),
                    "size": candidate.stat().st_size,
                    "sha256": sha256_file(candidate),
                }
        item["actual_artifacts"] = actual_artifacts
        engine_meta[engine] = item

    return {
        "schema_version": 1,
        "git_head": git_head(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "vendor_lock_sha256": sha256_file(ROOT / "vendor.lock.json"),
        "corpus_sha256": corpus_sha,
        "profiles_sha256": profiles_sha,
        "engines": engine_meta,
    }


def verify_legal_snapshot(
    current: dict[str, list[str]], expected_path: Path, corpus_sha: str
) -> list[str]:
    expected = load_json(expected_path)
    errors: list[str] = []
    if expected.get("corpus_sha256") != corpus_sha:
        errors.append("legal_moves: corpus hash changed; re-record intentionally")
    expected_cases = expected.get("cases", {})
    for case_id, moves in current.items():
        if expected_cases.get(case_id) != moves:
            errors.append(
                f"legal_moves/{case_id}: expected={expected_cases.get(case_id)!r} actual={moves!r}"
            )
    return errors


def run(args: argparse.Namespace) -> int:
    corpus = load_json(CORPUS_PATH)
    profiles_doc = load_json(PROFILES_PATH)
    profiles = profiles_doc["profiles"]
    corpus_sha = sha256_file(CORPUS_PATH)
    profiles_sha = sha256_file(PROFILES_PATH)

    binaries = {engine: resolve_binary(profile) for engine, profile in profiles.items()}
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    actual_dir = RESULT_DIR / "actual"
    actual_dir.mkdir(parents=True, exist_ok=True)

    report: list[str] = []
    errors: list[str] = []

    legal = derive_cross_checked_legal_moves(corpus, binaries)
    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir

    if args.record:
        output_dir.mkdir(parents=True, exist_ok=True)
        write_json(
            output_dir / "legal_moves.json",
            {
                "schema_version": 1,
                "corpus_sha256": corpus_sha,
                "oracles": ["stockfish", "reckless"],
                "cases": legal,
            },
        )
        for engine, profile in profiles.items():
            golden, actual = record_engine(
                engine,
                binaries[engine],
                profile,
                corpus,
                legal,
                args.stability_runs,
                corpus_sha,
                profiles_sha,
            )
            write_json(output_dir / f"{engine}.json", golden)
            write_json(actual_dir / f"{engine}.json", actual)
            unstable_count = sum(bool(case["unstable_fields"]) for case in golden["cases"].values())
            report.append(
                f"{engine}: recorded {len(golden['cases'])} cases; "
                f"{unstable_count} cases expose non-golden diagnostic fields"
            )
    else:
        legal_path = output_dir / "legal_moves.json"
        if not legal_path.is_file():
            raise GoldenError(f"missing committed legal move snapshot: {legal_path}")
        errors.extend(verify_legal_snapshot(legal, legal_path, corpus_sha))
        for engine, profile in profiles.items():
            expected_path = output_dir / f"{engine}.json"
            if not expected_path.is_file():
                raise GoldenError(f"missing committed golden: {expected_path}")
            expected = load_json(expected_path)
            engine_errors, actual = verify_engine(
                engine,
                binaries[engine],
                profile,
                corpus,
                legal,
                expected,
                corpus_sha,
                profiles_sha,
            )
            errors.extend(engine_errors)
            write_json(actual_dir / f"{engine}.json", actual)
            report.append(
                f"{engine}: verified {len(corpus['cases'])} cases; "
                f"{len(engine_errors)} mismatch(es)"
            )

    write_json(RESULT_DIR / "manifest.json", build_manifest(binaries, corpus_sha, profiles_sha))
    report.extend(errors)
    (RESULT_DIR / "report.txt").write_text("\n".join(report) + "\n", encoding="utf-8")

    for line in report:
        print(line)
    if errors:
        return 1
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--record", action="store_true", help="record a new golden baseline")
    mode.add_argument("--verify", action="store_true", help="verify against committed goldens")
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_GOLDEN_DIR.relative_to(ROOT)),
        help="golden directory, relative to repository root unless absolute",
    )
    parser.add_argument(
        "--stability-runs",
        type=int,
        default=3,
        help="fresh-process repetitions used to establish record-time stability",
    )
    args = parser.parse_args()
    if args.stability_runs < 2 and args.record:
        parser.error("--stability-runs must be at least 2 when recording")
    return args


def main() -> int:
    args = parse_args()
    try:
        return run(args)
    except (GoldenError, UciError, ValueError) as exc:
        RESULT_DIR.mkdir(parents=True, exist_ok=True)
        message = f"golden baseline failure: {exc}"
        print(message, file=sys.stderr)
        (RESULT_DIR / "report.txt").write_text(message + "\n", encoding="utf-8")
        return 2
    except Exception as exc:
        RESULT_DIR.mkdir(parents=True, exist_ok=True)
        detail = traceback.format_exc()
        print(detail, file=sys.stderr)
        (RESULT_DIR / "report.txt").write_text(detail, encoding="utf-8")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())

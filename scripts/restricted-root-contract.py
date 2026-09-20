#!/usr/bin/env python3
"""Validate restricted-root search behavior across Allfather constituent engines."""

from __future__ import annotations

import glob
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.harness.normalize import normalize_search
from tests.harness.uci_session import UciError, UciSession

CASES_PATH = ROOT / "tests" / "restricted_root" / "cases.json"
CORPUS_PATH = ROOT / "tests" / "baseline" / "corpus.json"
LEGAL_PATH = ROOT / "tests" / "baseline" / "golden" / "legal_moves.json"
PROFILES_PATH = ROOT / "tests" / "baseline" / "profiles.json"
RESULT_DIR = ROOT / "build" / "test-results" / "restricted-root"

PV_HEAD_RE = re.compile(r"(?:^|\s)pv\s+([a-h][1-8][a-h][1-8][qrbn]?)(?:\s|$)")
MULTIPV_RE = re.compile(r"(?:^|\s)multipv\s+(\d+)(?:\s|$)")


class ContractError(RuntimeError):
    pass


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_binary(profile: dict[str, Any]) -> Path:
    direct = ROOT / profile["binary"]
    if direct.is_file():
        return direct
    pattern = profile.get("fallback_glob")
    if pattern:
        matches = sorted(
            Path(p)
            for p in glob.glob(str(ROOT / pattern), recursive=True)
            if Path(p).is_file()
        )
        if matches:
            return matches[0]
    raise ContractError(f"engine binary not found for profile: {profile}")


def inspect_info(lines: list[str]) -> tuple[list[str], list[int]]:
    pv_heads: list[str] = []
    multipv_indices: list[int] = []
    for line in lines:
        if not line.startswith("info "):
            continue
        pv = PV_HEAD_RE.search(line)
        if pv:
            pv_heads.append(pv.group(1).lower())
        multipv = MULTIPV_RE.search(line)
        if multipv:
            multipv_indices.append(int(multipv.group(1)))
    return pv_heads, multipv_indices


def run_search(
    engine: str,
    profile: dict[str, Any],
    position: dict[str, Any],
    *,
    nodes: int,
    searchmoves: list[str],
    option_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    options = dict(profile.get("options", {}))
    options.update(option_overrides or {})
    binary = resolve_binary(profile)

    with UciSession(binary, cwd=ROOT, timeout=12.0) as session:
        session.configure(options)
        session.new_game()
        session.set_position(position)
        lines = session.search_nodes(nodes, searchmoves=searchmoves, timeout=25.0)
        normalized = normalize_search(lines)

    pv_heads, multipv_indices = inspect_info(lines)
    return {
        "engine": engine,
        "bestmove": normalized["bestmove"],
        "pv_heads": pv_heads,
        "multipv_indices": multipv_indices,
    }


def run_reckless_reuse_sequence(
    profile: dict[str, Any],
    position: dict[str, Any],
    *,
    nodes: int,
) -> dict[str, Any]:
    options = dict(profile.get("options", {}))
    options["Threads"] = 2
    binary = resolve_binary(profile)

    with UciSession(binary, cwd=ROOT, timeout=12.0) as session:
        session.configure(options)
        session.new_game()
        session.set_position(position)

        zero_lines = session.search_nodes(nodes, searchmoves=[], timeout=25.0)
        zero = normalize_search(zero_lines)
        zero_heads, zero_multipv = inspect_info(zero_lines)
        zero_observation = {
            "engine": "reckless",
            "bestmove": zero["bestmove"],
            "pv_heads": zero_heads,
            "multipv_indices": zero_multipv,
        }
        assert_restricted(
            label="reuse-zero-root/reckless",
            observation=zero_observation,
            allowed=[],
        )

        session.set_position(position)
        normal_lines = session.search_nodes(nodes, searchmoves=None, timeout=25.0)
        normal = normalize_search(normal_lines)
        normal_heads, normal_multipv = inspect_info(normal_lines)
        if normal["bestmove"] is None:
            raise ContractError("reuse-after-zero-root/reckless: unrestricted search returned no move")

    return {
        "zero_root": zero_observation,
        "unrestricted_after_zero": {
            "engine": "reckless",
            "bestmove": normal["bestmove"],
            "pv_heads": normal_heads,
            "multipv_indices": normal_multipv,
        },
    }


def assert_restricted(
    *,
    label: str,
    observation: dict[str, Any],
    allowed: list[str],
) -> None:
    allowed_set = {move.lower() for move in allowed}
    bestmove = observation["bestmove"]

    if not allowed_set:
        if bestmove is not None:
            raise ContractError(f"{label}: expected fail-closed no-move, got {bestmove}")
        if observation["pv_heads"]:
            raise ContractError(f"{label}: zero-root restriction emitted PV heads {observation['pv_heads']}")
        return

    if bestmove not in allowed_set:
        raise ContractError(f"{label}: bestmove {bestmove!r} outside authorized roots {sorted(allowed_set)}")

    unauthorized_heads = sorted({head for head in observation["pv_heads"] if head not in allowed_set})
    if unauthorized_heads:
        raise ContractError(f"{label}: unauthorized PV root heads {unauthorized_heads}")

    max_multipv = max(observation["multipv_indices"], default=0)
    if max_multipv > len(allowed_set):
        raise ContractError(
            f"{label}: reported multipv {max_multipv} with only {len(allowed_set)} authorized roots"
        )


def main() -> int:
    cases = load_json(CASES_PATH)
    corpus_doc = load_json(CORPUS_PATH)
    legal_doc = load_json(LEGAL_PATH)
    profiles = load_json(PROFILES_PATH)["profiles"]

    corpus = {case["id"]: case["position"] for case in corpus_doc["cases"]}
    legal = legal_doc["cases"]

    results: dict[str, Any] = {
        "schema_version": 1,
        "cross_engine": [],
        "reckless_specific": [],
        "reckless_reuse": None,
    }

    for case in cases["cross_engine"]:
        position_id = case["position_id"]
        requested = [move.lower() for move in case["searchmoves"]]
        legal_set = set(legal[position_id])
        invalid = [move for move in requested if move not in legal_set]
        if invalid or not requested:
            raise ContractError(
                f"{case['id']}: cross-engine fixtures must be legal and non-empty; invalid={invalid}"
            )

        for engine in ("stockfish", "reckless", "lc0"):
            observation = run_search(
                engine,
                profiles[engine],
                corpus[position_id],
                nodes=int(case["nodes"]),
                searchmoves=requested,
            )
            assert_restricted(
                label=f"{case['id']}/{engine}",
                observation=observation,
                allowed=requested,
            )
            results["cross_engine"].append({"case": case["id"], **observation})

    reckless_profile = profiles["reckless"]
    for case in cases["reckless_specific"]:
        observation = run_search(
            "reckless",
            reckless_profile,
            corpus[case["position_id"]],
            nodes=int(case["nodes"]),
            searchmoves=list(case["searchmoves"]),
            option_overrides=case.get("options"),
        )
        assert_restricted(
            label=f"{case['id']}/reckless",
            observation=observation,
            allowed=list(case["expected_allowed"]),
        )
        results["reckless_specific"].append({"case": case["id"], **observation})

    results["reckless_reuse"] = run_reckless_reuse_sequence(
        reckless_profile,
        corpus["startpos"],
        nodes=256,
    )

    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    (RESULT_DIR / "report.json").write_text(
        json.dumps(results, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print(
        "restricted-root contract passed: "
        f"{len(results['cross_engine'])} cross-engine observations, "
        f"{len(results['reckless_specific'])} Reckless-specific observations, "
        "1 same-process reuse sequence"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ContractError, UciError, ValueError) as exc:
        RESULT_DIR.mkdir(parents=True, exist_ok=True)
        message = f"restricted-root contract failure: {exc}"
        print(message, file=sys.stderr)
        (RESULT_DIR / "failure.txt").write_text(message + "\n", encoding="utf-8")
        raise SystemExit(1)

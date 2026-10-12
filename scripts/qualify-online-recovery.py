#!/usr/bin/env python3
"""Independent ONLINE-4A fault-harness qualifier. Never award recovery authority.

Rebuilds the entire server-commit chess history and checks fault witnesses,
source hashes, HTTP/server ordering and retained replay identity.
A discovered unsafe client behavior is a VALID diagnostic witness, not a
reason to relabel the client as recovered or release-ready.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from urllib.parse import unquote
import sys
from pathlib import Path

import chess
import chess.pgn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.lichess.fault_plan import load_scenarios

POLICY = ROOT / "qualification/online-recovery-v1.json"


def ensure(condition, why):
    if not condition:
        raise ValueError(why)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def ndjson(path: Path) -> list[dict]:
    lines = path.read_text(encoding="utf-8").splitlines()
    result = [json.loads(x) for x in lines]
    ensure(all(isinstance(x, dict) for x in result),
           f"{path}: NDJSON rows must be objects")
    return result


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def verify_sources(manifest: dict, exact_sha: str):
    ensure(manifest.get("source_commit") == exact_sha, "source manifest SHA mismatch")
    files = manifest.get("files")
    expected = read_json(POLICY).get("attested_source_paths")
    ensure(isinstance(files, dict) and isinstance(expected, list) and
           set(files) == set(expected) and len(expected) == len(set(expected)),
           "source manifest does not contain exact frozen harness file set")
    for path, declared in files.items():
        ensure(isinstance(path, str) and path != "" and ".." not in Path(path).parts,
               "unsafe source path")
        ensure((ROOT / path).is_file() and not (ROOT / path).is_symlink() and
               (ROOT / path).resolve().is_relative_to(ROOT.resolve()),
               "missing, linked or escaped source attestation file " + path)
        ensure(digest(ROOT / path) == declared, "source file identity drift: " + path)


def verify_trace(trace: list[dict], game: dict) -> dict:
    seq = [row.get("seq") for row in trace]
    ensure(seq == list(range(1, len(trace) + 1)), "trace ordering or numbering invalid")
    board = chess.Board()
    attempts = {f"request-{r['seq']}": r for r in trace if r.get("event") == "move_attempt"}
    commits = [r for r in trace if r.get("event") == "board_commit"]
    ensure(isinstance(game.get("moves"), list), "authoritative move history missing")
    for item in commits:
        ensure(item.get("game_id") == "off3b001", "foreign committed game id")
        ensure(item.get("ply_before") == len(board.move_stack), "commit before-ply drift")
        ensure(item.get("ply_after") == len(board.move_stack) + 1, "commit after-ply drift")
        ensure(item.get("fen_before") == board.fen(), "commit before-FEN drift")
        move = chess.Move.from_uci(item["uci"])
        ensure(move in board.legal_moves, "committed illegal chess move")
        origin = item.get("origin")
        ensure(origin in ("allfather", "scripted_opponent"), "unknown commit origin")
        if origin == "allfather":
            req = item.get("request_id")
            ensure(req in attempts, "accepted move has no observed HTTP attempt")
            ensure(attempts[req].get("uci") == item["uci"], "move request/commit mismatch")
            ensure(attempts[req].get("seq") < item["seq"], "commit preceded its request")
        else:
            ensure(item.get("request_id") is None, "opponent move bound to bridge request")
        board.push(move)
        ensure(item.get("fen_after") == board.fen(), "commit after-FEN drift")
    ensure(game["moves"] == [m.uci() for m in board.move_stack],
           "authoritative history differs from committed ledger")
    accepted = [dict(ply=r["ply_after"], uci=r["uci"]) for r in commits
                if r["origin"] == "allfather"]
    ensure(accepted == game.get("accepted"), "server bot-acceptance list differs from ledger")
    if game.get("complete") is True:
        outcome = board.outcome(claim_draw=False)
        ensure(outcome is not None and game.get("outcome") is not None,
               "claimed finished game has no rules-based termination")
        ensure(outcome.termination.name == game["outcome"].get("termination"),
               "termination differs from rules oracle")
    return {"plies": len(commits), "bot_commits": len(accepted),
            "http_attempts": len(attempts), "game_complete": game.get("complete") is True}


def verify_state_fault(action: str, witnessed: dict, sends: list[dict],
                       trace: list[dict]) -> None:
    """Verify the delivered state or elapsed delay, not only producer labels."""
    matching = [r for r in trace if r.get("event") == "stream_fault_source"
                and r.get("sid") == witnessed.get("sid")
                and r.get("action") == action
                and witnessed["seq"] < r.get("seq", -1)]
    ensure(len(matching) == 1 and len(sends) == 1,
           "faulted state missing unique before/after witness")
    source, delivery = matching[0], sends[0]
    ensure(source["seq"] < delivery["seq"] and
           source.get("original_sha256") and
           type(source.get("monotonic_ns")) is int and
           type(delivery.get("monotonic_ns")) is int,
           "state fault timing or source identity absent")
    if action == "delay_state":
        ensure(delivery["monotonic_ns"] - source["monotonic_ns"] >= 390_000_000,
               "state delay did not actually occur")
        ensure(delivery.get("payload_sha256") == source["original_sha256"]
               and delivery.get("moves") == source.get("original_moves"),
               "delayed delivery does not preserve original state")
    if action == "stale_state":
        old_moves = source.get("predecessor_moves")
        new_moves = source.get("original_moves")
        ensure(isinstance(new_moves, str) and isinstance(old_moves, str),
               "stale-state original/predecessor history absent")
        old = old_moves.split() if old_moves else []
        new = new_moves.split() if new_moves else []
        commits = [r["uci"] for r in trace if
                   r.get("event") == "board_commit" and
                   r.get("seq", 0) < source["seq"]]
        ensure(len(new) == len(old) + 1 and new[:-1] == old
               and new == commits[:len(new)]
               and source["predecessor_sha256"] != source["original_sha256"],
               "stale-state predecessor is not the actual prior game position")
        ensure(delivery.get("moves") == old_moves and
               delivery.get("payload_sha256") == source["predecessor_sha256"],
               "stale-state delivered unchanged/current payload")


def verify_scenario_witness(scenario: dict, activation: dict,
                            trace: list[dict], result: dict):
    fault = activation.get("activations")
    ensure(isinstance(fault, list), "fault activation list missing")
    if scenario["action"] == "none":
        ensure(len(fault) == 0, "normal case unexpectedly activated fault")
        ensure(result.get("complete") is True, "normal baseline did not finish")
        return
    ensure(len(fault) == 1, "required deterministic fault not activated exactly once")
    match = fault[0]
    target = scenario["target"]
    ensure(match.get("action") == scenario["action"] and
           match.get("endpoint") == target["endpoint"] and
           match.get("endpoint_ordinal") == target["ordinal"],
           "fault activation does not match committed scenario")
    witnessed = [r for r in trace if r.get("event") == "fault_activated" and
                 r.get("action") == scenario["action"] and
                 r.get("endpoint") == target["endpoint"]]
    ensure(len(witnessed) == 1, "fault activation does not have one HTTP/stream witness")
    action = scenario["action"]
    if action in ("drop_before_commit", "rate_limit_429",
                  "commit_then_close", "commit_then_504",
                  "low_clock_close", "terminate_bridge"):
        rid = match.get("request_id")
        ensure(isinstance(rid, str) and any(
            r.get("event") == "move_attempt" and f"request-{r['seq']}" == rid
            for r in trace), "fault did not target an observed move POST")
        commit = [r for r in trace if r.get("event") == "board_commit" and
                  r.get("request_id") == rid]
        if action in ("drop_before_commit", "rate_limit_429"):
            ensure(not commit, "precommit fault nonetheless committed targeted request")
        else:
            ensure(len(commit) == 1, "postcommit fault did not commit exactly one move")
            ensure(witnessed[0]["seq"] < commit[0]["seq"],
                   "postcommit fault ordering incorrect")
        if action in ("commit_then_close", "low_clock_close", "terminate_bridge"):
            ensure(any(r.get("event") == "connection_close_requested" and
                       r.get("seq") > witnessed[0]["seq"] for r in trace),
                   "lost-response fault did not close the HTTP connection")
        if action == "commit_then_504":
            ensure(any(r.get("event") == "response_write_completed" and
                       r.get("status") == 504 and r.get("seq") > commit[0]["seq"]
                       for r in trace), "504 response witness absent")
        if action == "rate_limit_429":
            ensure(any(r.get("event") == "response_write_completed" and
                       r.get("status") == 429 for r in trace),
                   "429 response witness absent")
        if action == "low_clock_close":
            ensure(any(r.get("event") == "synthetic_clock_override" and
                       r.get("milliseconds") == 1500 for r in trace),
                   "virtual-clock intervention not labeled")
        if action == "terminate_bridge":
            ensure(result.get("observation") == "fault_triggered_bridge_kill"
                   and isinstance(result.get("bridge_exit"), int),
                   "bridge-kill containment scenario did not terminate process")
    if action in ("delay_state", "duplicate_state", "stale_state",
                  "partial_chunk", "wrong_game", "duplicate_terminal"):
        sid = witnessed[0].get("sid")
        if action == "partial_chunk":
            ensure(any(r.get("event") == "stream_partial_chunk" and
                       r.get("sid") == sid for r in trace),
                   "partial NDJSON chunk was never emitted")
        else:
            sends = [r for r in trace if r.get("event") == "stream_emit" and
                     r.get("sid") == sid and r.get("fault_action") == action]
            ensure(bool(sends), "game-state fault was never written")
            if action in ("delay_state", "stale_state"):
                verify_state_fault(action, witnessed[0], sends, trace)
            if action in ("duplicate_state", "duplicate_terminal"):
                ensure(any(r.get("event") == "stream_emit" and
                           r.get("fault_action") == "duplicate" and
                           r.get("payload_sha256") == sends[0].get("payload_sha256")
                           for r in trace), "duplicate state witness absent")


def validate_replays(case_dir: Path) -> dict:
    rows = read_json(case_dir / "replay-index.json")
    ensure(isinstance(rows, list) and len(rows) <= 128, "invalid replay index")
    for row in rows:
        path = row.get("manifest")
        ensure(isinstance(path, str) and path.startswith("replays/") and
               ".." not in Path(path).parts, "unsafe replay manifest path")
        manifest = case_dir / path
        ensure(manifest.is_file() and digest(manifest) == row.get("sha256"),
               "replay manifest bytes mismatch")
        document = read_json(manifest)
        ensure(document.get("run_id") == row.get("run_id") and
               document.get("generation") == row.get("generation"),
               "replay identity mismatch")
        ensure((document.get("position") or {}).get("position_id") == row.get("position_id"),
               "replay position identity mismatch")
        if "route_sha256" in row:
            ensure(digest(manifest.parent / "route.json") == row["route_sha256"],
                   "routing artifact tampered")
    return {"manifests_retained": len(rows),
            "game_to_replay_link_qualified": False}


def invalid_move_submissions(http_requests: list[dict]) -> list[dict]:
    """Only malformed/null UCI syntax—not a stale valid-move retry—is unsafe.

    A once-legal move repeated after a lost acknowledgment may receive 400/409;
    that is not evidence the engine itself chose an invalid move.
    """
    prefix = "/api/bot/game/off3b001/move/"
    invalid = []
    for request in http_requests:
        path = request.get("path")
        if request.get("method") != "POST" or not isinstance(path, str) or not path.startswith(prefix):
            continue
        uci = unquote(path[len(prefix):])
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            move = None
        if move is None or move == chess.Move.null():
            invalid.append({"path": path, "uci": uci, "status": request.get("status")})
    return invalid


def validate_experiment(case_dir: Path, scenario: dict, color: str,
                        source_sha: str, bridge_sha: str) -> dict:
    result = read_json(case_dir / "result.json")
    plan = read_json(case_dir / "fault-plan.json")
    activation = read_json(case_dir / "fault-activation.json")
    trace = ndjson(case_dir / "transport-trace.ndjson")
    game = read_json(case_dir / "authoritative-game.json")
    http_requests = ndjson(case_dir / "requests.ndjson")
    ensure(plan == scenario and activation.get("scenario") == scenario,
           "fault scenario provenance drift")
    ensure(result.get("scenario_id") == scenario["id"] and result.get("color") == color,
           "scenario or color identity drift")
    ensure(result.get("source_commit") == source_sha and
           result.get("bridge_commit") == bridge_sha,
           "runtime source identity drift")
    ensure(game.get("game_id") == "off3b001" and game.get("color") == color,
           "missing exact synthetic game identity")
    ensure(game.get("challenge_accepted") is True,
           "fault scenario did not reach accepted game")
    ensure(isinstance(game.get("forbidden_attempts"), list),
           "forbidden endpoint audit missing")
    derived_forbidden = [r["method"] + " " + r["path"] for r in http_requests
                         if r.get("status") == 403]
    ensure(derived_forbidden == game["forbidden_attempts"],
           "forbidden endpoint audit differs from server request records")
    ensure(all(r.get("method") in ("GET", "POST") and
               isinstance(r.get("path"), str) and
               type(r.get("status")) is int
               for r in http_requests), "malformed server HTTP request audit")
    attempted_paths = [r["path"] for r in http_requests if r["method"] == "POST"]
    ensure(all(r.get("path") in attempted_paths for r in trace
               if r.get("event") == "move_attempt"),
           "HTTP attempt trace has no underlying request audit")
    verify_sources(read_json(case_dir / "source-manifest.json"), source_sha)
    ensure(ndjson(case_dir / "http-attempts.ndjson") ==
           [r for r in trace if r["event"] == "move_attempt"], "HTTP attempt extract drift")
    ensure(ndjson(case_dir / "server-commits.ndjson") ==
           [r for r in trace if r["event"] == "board_commit"], "commit extract drift")
    ensure(ndjson(case_dir / "delivered-events.ndjson") ==
           [r for r in trace if r["event"] in ("stream_emit", "stream_partial_chunk")],
           "stream delivery extract drift")
    ensure(all(type(r.get("monotonic_ns")) is int for r in trace),
           "transport trace lacks server monotonic observation timestamps")
    history = verify_trace(trace, game)
    with (case_dir / "game.pgn").open(encoding="utf-8") as stream:
        parsed = chess.pgn.read_game(stream)
    ensure(parsed is not None and not parsed.errors and
           [move.uci() for move in parsed.mainline_moves()] == game["moves"],
           "authoritative PGN differs from independently reconstructed game")
    verify_scenario_witness(scenario, activation, trace, result)
    replay = validate_replays(case_dir)
    logging = result.get("logging")
    if logging is not None:
        log = case_dir / "bridge.log"
        ensure(log.is_file() and logging.get("sanitized_sha256") == digest(log) and
               logging.get("sanitized_bytes") == log.stat().st_size and
               logging.get("capture_complete") is True,
               "sanitized log capture integrity violation")
        ensure("offline-fixture-token" not in log.read_text(encoding="utf-8", errors="replace"),
               "synthetic credentials leaked")
    ensure(isinstance(result.get("bridge_exit"), int),
           "bridge process cleanup was not recorded")
    early_rate_requests = []
    if scenario["action"] == "rate_limit_429":
        ensure(result.get("observation") == "bounded_postfault_observation"
               and type(result.get("postfault_observed_seconds")) in (int, float)
               and result["postfault_observed_seconds"] >= 61,
               "incomplete R10 post-fault observation")
        activation_row = next((r for r in trace if
                               r.get("event") == "fault_activated" and
                               r.get("action") == "rate_limit_429"), None)
        complete_rows = [r for r in trace if
                         r.get("event") == "postfault_window_complete"
                         and r.get("action") == "rate_limit_429"]
        ensure(activation_row is not None and len(complete_rows) == 1
               and type(activation_row.get("monotonic_ns")) is int
               and type(complete_rows[0].get("monotonic_ns")) is int
               and complete_rows[0]["monotonic_ns"] -
               activation_row["monotonic_ns"] >= 61_000_000_000,
               "R10 server trace lacks 61 seconds after rate-limit fault")
        responses = [r for r in http_requests if r.get("status") == 429]
        ensure(len(responses) == 1, "missing or repeated authoritative 429")
        when = responses[0]["monotonic_ms"]
        rate_index = http_requests.index(responses[0])
        early_rate_requests = [r for r in http_requests[rate_index + 1:]
                               if 0 <= r["monotonic_ms"] - when < 60000]
    ensure(isinstance(result.get("runner_errors"), list) and not result["runner_errors"],
           "fault harness recorded execution or teardown errors")
    unusable_moves = invalid_move_submissions(http_requests)
    if scenario["action"] == "none":
        ensure(history["bot_commits"] > 0 and logging is not None,
               "normal engine baseline did not play")
        ensure(not unusable_moves, "normal game submitted invalid/null engine move")
        status = "SAFE_CONTINUATION"
    elif game["forbidden_attempts"] or early_rate_requests or unusable_moves:
        status = "UNSAFE_WITNESS"
    elif game.get("complete") is True:
        status = "OBSERVED_COMPLETION"
    else:
        status = "OBSERVED_STOP_OR_UNRESOLVED"
    return {"scenario": scenario["id"], "color": color,
            "harness_integrity": True, "behavior": status,
            "early_rate_limit_requests": early_rate_requests,
            "unusable_move_submissions": unusable_moves,
            "fault_activated": scenario["action"] != "none",
            "board": history, "replay": replay,
            "runner_errors": result.get("runner_errors", []),
            "network_recovery_qualified": False}


def qualify(evidence: Path, source_sha: str) -> dict:
    if len(source_sha) != 40 or any(c not in "0123456789abcdef" for c in source_sha.lower()):
        raise ValueError("source SHA malformed")
    policy = read_json(POLICY)
    ensure(policy.get("scope") == "offline-fault-harness-only" and
           policy.get("claim_boundary", {}).get("public_bot_release") is False,
           "qualification policy enabled public authority")
    actual = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                                     text=True).strip()
    ensure(actual == source_sha, "validator checkout is not exact source commit")
    bridge = read_json(ROOT / "deploy/lichess/bridge.lock.json")["commit"]
    scenarios = load_scenarios(ROOT / policy["scenario_source"])
    required = policy["required_integration_scenarios"]
    ensure(set(required) == {k for k, v in scenarios.items() if v["mode"] == "integration"},
           "required integration scenario coverage drift")
    ensure(set(policy["required_contract_scenarios"]) ==
           {k for k, v in scenarios.items() if v["mode"] == "contract"},
           "required contract scenario coverage drift")
    findings = []
    failures = []
    for case in required:
        for color in scenarios[case]["colors"]:
            location = evidence / f"{case}-{color}"
            try:
                findings.append(validate_experiment(location, scenarios[case], color,
                                                    source_sha, bridge))
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
                failures.append({"scenario": case, "color": color, "error": str(exc)})
    unsafe = [x for x in findings if x["behavior"] == "UNSAFE_WITNESS"]
    return {
        "schema_version": 1, "source_commit": source_sha,
        "bridge_commit": bridge,
        "policy_sha256": digest(POLICY),
        "scenario_source_sha256": digest(ROOT / policy["scenario_source"]),
        "required_run_count": sum(len(scenarios[k]["colors"]) for k in required),
        "validated_run_count": len(findings),
        "harness_qualified": not failures and len(findings) ==
            sum(len(scenarios[k]["colors"]) for k in required),
        "harness_failures": failures,
        "unsafe_witnesses": unsafe,
        "scenario_results": findings,
        "recovery_behavior_qualified": False,
        "network_recovery_qualified": False,
        "deployment_host_qualified": False,
        "public_bot_release": False,
        "playing_strength_qualified": False,
        "release_blocked": True,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--evidence", type=Path, required=True)
    p.add_argument("--source-sha", required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    try:
        report = qualify(args.evidence, args.source_sha)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        report = {
            "schema_version": 1, "source_commit": args.source_sha,
            "harness_qualified": False,
            "harness_failures": [{"error": str(exc)}],
            "recovery_behavior_qualified": False,
            "network_recovery_qualified": False,
            "public_bot_release": False,
            "release_blocked": True,
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "scenario_results"},
                     sort_keys=True))
    return 0 if report["harness_qualified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

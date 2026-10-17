#!/usr/bin/env python3
"""Independent ONLINE-4B containment/recovery qualification, OFFLINE ONLY.

Reconstructs authoritative board from immutable trace, checks HTTP and SQLite
records, source-locked patch identity and explicit restart witnesses. It NEVER
grants actual network, host, account or public chess-playing authority.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import tempfile
from collections import Counter
from urllib.parse import unquote
import subprocess
import sqlite3
import sys
from pathlib import Path
import chess
import chess.pgn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.lichess.fault_plan import load_scenarios
from tests.lichess.http_target_contract import canonical_target
from deploy.lichess.reconcile import position_id
from scripts.verify_source_closure import verify_profile, verify_committed_bytes
from scripts.http_evidence_verdict import approved_status

def _v1_module():
    spec = importlib.util.spec_from_file_location(
        "online4a_evidence", ROOT / "scripts/qualify-online-recovery.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

v1 = _v1_module()
POLICY = ROOT / "qualification/online-recovery-v2.json"


def require(ok, why):
    if not ok:
        raise ValueError(why)


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def lines(path: Path):
    return [json.loads(row) for row in path.read_text(encoding="utf-8").splitlines()]


def hash_file(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as fd:
        for piece in iter(lambda: fd.read(1<<20), b""):
            digest.update(piece)
    return digest.hexdigest()


def attest(document: dict, exact_sha: str, paths: list[str]):
    require(document.get("source_commit") == exact_sha, "wrong exact source SHA")
    files = document.get("files")
    require(isinstance(files, dict) and set(files) == set(paths) and
            len(paths) == len(set(paths)), "incomplete recovery source attestation")
    for name in paths:
        target = ROOT / name
        require(target.is_file() and not target.is_symlink()
                and target.resolve().is_relative_to(ROOT.resolve()),
                "missing or linked source: " + name)
        require(files[name] == hash_file(target), "source hash mismatch: "+name)


def independently_expected_overlay() -> dict:
    """Rebuild the frozen overlay from verified upstream source, never evidence."""
    from deploy.lichess import fetch_bridge, recovery_overlay
    source = fetch_bridge.DEFAULT
    lock = read(fetch_bridge.LOCK)
    fetch_bridge.verify(source, lock)
    with tempfile.TemporaryDirectory(prefix="offline-overlay-verifier-") as d:
        staged = Path(d) / "bridge"
        fetch_bridge.copy_tracked(source, staged)
        return recovery_overlay.apply(staged)


def verify_overlay_identity(manifest: dict, expected: dict) -> None:
    require(manifest == expected,
            "overlay files, original blob IDs or actual patched hashes differ from frozen source")


def verify_guarded_requests(requests: list[dict], trace: list[dict],
                            game: dict, intents: list[dict], *,
                            scenario_action: str = "none") -> dict:
    """Bind every HTTP move attempt to a unique durable intent and legal board."""
    gid = game.get("game_id")
    moves = game.get("moves")
    color = game.get("color")
    require(gid == "off3b001" and color in ("white", "black")
            and isinstance(moves, list), "invalid authoritative game identity")
    prefix = f"/api/bot/game/{gid}/move/"
    allowed = {"/api/token/test", f"/api/challenge/{gid}/accept",
               f"/api/challenge/{gid}/decline"}
    require(isinstance(requests, list) and isinstance(trace, list)
            and isinstance(intents, list), "missing source request/journal evidence")
    forbidden = game.get("forbidden_attempts")
    require(isinstance(forbidden, list)
            and forbidden == ["POST " + r["path"] for r in requests
                              if r.get("method") == "POST" and r.get("status") == 403],
            "server forbidden-attempt audit differs from HTTP records")
    require(not forbidden, "guard submitted forbidden HTTP operations")
    # Audit *original raw request semantics*. Checking only the producer's
    # normalized pathname would accept a forged GET query or a leading-//
    # request that CPython normalized before dispatch.
    for request in requests:
        raw = request.get("request_target") if isinstance(request, dict) else None
        method = request.get("method") if isinstance(request, dict) else None
        require(isinstance(raw, str) and
                request.get("target_canonical") is True and
                canonical_target(method, raw) and
                request.get("path") == raw.split("?", 1)[0],
                "guarded HTTP request target is not canonical")
        require(approved_status(request, trace, scenario_action),
                "guarded HTTP request has unqualified status or fault witness")
    http_moves = []
    for request in requests:
        if request.get("method") != "POST":
            continue
        path = request.get("path")
        require(isinstance(path, str)
                and (path in allowed or path.startswith(prefix)),
                "unexpected guarded POST operation")
        if path.startswith(prefix):
            uci = unquote(path[len(prefix):])
            require(uci and "/" not in uci and "?" not in uci and
                    path == prefix + uci,
                    "noncanonical guarded move URL")
            try:
                canonical = chess.Move.from_uci(uci)
            except (ValueError, TypeError) as exc:
                raise ValueError("malformed guarded move URL") from exc
            require(canonical != chess.Move.null() and canonical.uci() == uci,
                    "noncanonical guarded move URL")
            rid = request.get("request_id")
            require(isinstance(rid, str) and bool(rid),
                    "guarded move HTTP request ID missing")
            http_moves.append((path, rid))
    raw_attempts = [r for r in trace if r.get("event") == "move_attempt"]
    require(all(type(r.get("seq")) is int and r["seq"] > 0
                for r in raw_attempts),
            "guarded move trace sequence identity malformed")
    require(Counter(http_moves) ==
            Counter((r.get("path"), f"request-{r['seq']}")
                    for r in raw_attempts),
            "guarded move HTTP records, request IDs and trace attempts differ")

    board = chess.Board()
    seen = set()
    ledger = {(i.get("game_id"),i.get("ply_before"),i.get("move")): i
              for i in intents}
    require(len(ledger) == len(intents), "duplicate durable intent identity")
    validated = 0
    for event in trace:
        typ = event.get("event")
        if typ == "board_commit":
            uci = event.get("uci")
            require(event.get("game_id") == gid and
                    event.get("ply_before") == len(board.move_stack),
                    "commit does not match authoritative ply")
            try:
                move = chess.Move.from_uci(uci)
            except (ValueError,TypeError) as exc:
                raise ValueError("non-UCI authoritative chess move") from exc
            require(move in board.legal_moves, "illegal authoritative commit")
            board.push(move)
        if typ != "move_attempt":
            continue
        ply = len(board.move_stack)
        uci = event.get("uci")
        require(event.get("game_id") == gid and
                type(event.get("ply_at_receipt")) is int and
                event["ply_at_receipt"] == ply,
                "move attempt does not match authoritative game/ply")
        require(board.turn == (color == "white"),
                "HTTP move attempt made on opponent turn")
        try:
            move = chess.Move.from_uci(uci)
        except (ValueError,TypeError) as exc:
            raise ValueError("malformed HTTP UCI move submission") from exc
        require(move != chess.Move.null() and move.uci() == uci and
                event.get("path") == prefix + uci,
                "guarded move URL differs from traced canonical UCI")
        require(move in board.legal_moves,
                "position-illegal HTTP move submission")
        key = (gid, ply, uci)
        require(key not in seen, "blind repeat HTTP move submission")
        seen.add(key)
        intent = ledger.get(key)
        require(intent is not None and
                json.loads(intent["history_json"]) ==
                    [m.uci() for m in board.move_stack],
                "move attempt without exact authoritative durable intent")
        validated += 1
    require([m.uci() for m in board.move_stack] == moves,
            "HTTP trace board does not reconstruct game history")
    require(validated == len(raw_attempts),
            "not every recorded HTTP move was independently checked")
    return {"unique_legal_move_attempts": validated,
            "forbidden_requests": 0}


def verify_journal(journal: dict, trace: list[dict], game: dict,
                   result: dict, guard_events: list[dict]):
    require(isinstance(journal, dict) and isinstance(journal.get("owner_epoch"), str),
            "missing durable journal snapshot")
    epochs = [p.get("epoch") for p in result.get("phases", [])]
    require(all(isinstance(epoch, str) and len(epoch) >= 16 for epoch in epochs),
            "missing phase ownership epochs")
    require(len(epochs) == len(set(epochs)) and epochs[-1] == journal["owner_epoch"],
            "stale or reused owner epoch")
    intents = journal.get("intents")
    events = journal.get("events")
    require(isinstance(intents, list) and isinstance(events, list),
            "missing journal intent or audit records")
    require([row["event_id"] for row in events] == list(range(1, len(events)+1)),
            "durable journal events are not ordered")
    committed = [r for r in trace if r["event"] == "board_commit"
                 and r["origin"] == "allfather"]
    found = {(r["ply_before"],r["move"]) for r in intents}
    for commit in committed:
        require((commit["ply_before"], commit["uci"]) in found,
                "server move not preceded by durable matching intent")
    require(len({(r["game_id"],r["ply_before"]) for r in intents}) == len(intents),
            "conflicting intents for one authoritative ply")
    for row in intents:
        prior = tuple(json.loads(row["history_json"]))
        ply = row.get("ply_before")
        require(row.get("game_id") == game.get("game_id") and
                type(ply) is int and 0 <= ply <= len(game["moves"]) and
                prior == tuple(game["moves"][:ply]),
                "journal intent not bound to authoritative game history")
        require(row["position_id"] == position_id(prior) and
                ply == len(prior), "journal position identity drift")
        require(row["status"] in ("PREPARED", "SUBMITTING",
                                 "ACK_OBSERVED", "UNKNOWN_ACK", "CONFIRMED"),
                "unknown move state")
        if row["status"] == "CONFIRMED":
            require(len([r for r in committed
                         if r.get("game_id") == row["game_id"] and
                         r["ply_before"] == row["ply_before"]
                         and r["uci"] == row["move"]]) == 1,
                    "journal claims uncommitted or duplicated move confirmation")
    g = journal.get("games")
    require(isinstance(g, list) and len(g) <= 1, "unexpected concurrent games")
    if g:
        history = tuple(json.loads(g[0]["history_json"]))
        require(tuple(game["moves"])[:len(history)] == history,
                "durable game history disagrees with server")
    ensure_audit = any(row["event"] == "authoritative_snapshot_observed"
                       for row in guard_events)
    require(ensure_audit, "guard never observed an authoritative snapshot")
    return {"intents": len(intents), "server_commits": len(committed),
            "final_epoch": epochs[-1],
            "confirmed_intents": sum(1 for r in intents if r["status"]=="CONFIRMED")}


def verify_persisted_sqlite(folder: Path, journal: dict):
    db = folder / "state/move-journal.sqlite3"
    require(db.is_file() and not db.is_symlink(), "persisted SQLite journal absent")
    with sqlite3.connect("file:" + str(db.resolve()) + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        require(conn.execute("PRAGMA quick_check").fetchone()[0] == "ok",
                "durable SQLite file is corrupt")
        actual = {
            "games": [dict(r) for r in conn.execute("SELECT * FROM games ORDER BY game_id")],
            "intents": [dict(r) for r in conn.execute(
                "SELECT * FROM intents ORDER BY game_id,ply_before")],
            "cooldown": [dict(r) for r in conn.execute("SELECT * FROM cooldown")],
            "events": [dict(r) for r in conn.execute("SELECT * FROM events ORDER BY event_id")],
        }
        owner = conn.execute("SELECT epoch FROM owner WHERE singleton=1").fetchone()
    require(owner is not None and owner["epoch"] == journal.get("owner_epoch"),
            "SQLite owner differs from recorded journal")
    for name, entries in actual.items():
        require(entries == journal.get(name), "SQLite journal data differs from runner: "+name)


def r13_witness(phases: list[dict]) -> dict:
    """Adapt the independently recorded phase-1 exit to the ONLINE-4A oracle."""
    require(isinstance(phases, list) and bool(phases) and
            phases[0].get("observation") == "injected_bridge_kill",
            "R13 phase-one kill observation missing")
    require(phases[0].get("kill_issued") is True and
            phases[0].get("kill_signal") == 9 and
            type(phases[0].get("kill_monotonic_ns")) is int and
            phases[0]["kill_monotonic_ns"] > 0,
            "R13 SIGKILL was not actually issued to the first bridge group")
    require(type(phases[0].get("bridge_exit")) is int and
            phases[0]["bridge_exit"] == -9,
            "R13 phase-one process was not actually terminated by SIGKILL")
    return {"observation": "fault_triggered_bridge_kill",
            "bridge_exit": phases[0]["bridge_exit"]}


def classify_disposition(*, scenario_id: str, game_complete: bool,
                         recovered: bool, explicit_suspension: bool,
                         restart_attempted: bool) -> str:
    """Report observation separately from containment and recovery authority."""
    if recovered:
        return "RECOVERED"
    if scenario_id == "R00":
        return "NORMAL_COMPLETION" if game_complete else "INCONCLUSIVE"
    if restart_attempted:
        # Server mate alone does not prove that the new worker recovered.
        return "INCONCLUSIVE" if game_complete else (
            "SAFE_SUSPENSION" if explicit_suspension else "INCONCLUSIVE")
    if game_complete:
        return "SAFE_CONTINUATION"
    return "SAFE_SUSPENSION" if explicit_suspension else "INCONCLUSIVE"


def verify_terminal_confirmation(game: dict, journal: dict,
                                 phases: list[dict]) -> bool:
    """A server-side checkmate does not certify the client's terminal state."""
    if game.get("complete") is not True:
        return False
    require(bool(phases), "completed game missing process phases")
    final = phases[-1]
    recorded = final.get("terminal_sync")
    require(isinstance(recorded, dict) and recorded.get("complete") is True
            and final.get("observation") == "server_game_complete",
            "game ended on server but terminal journal barrier did not complete")
    moves = game.get("moves")
    require(isinstance(moves, list) and recorded.get("server_final_ply") == len(moves)
            and recorded.get("journal_ply") == len(moves)
            and recorded.get("journal_terminal") is True
            and recorded.get("history_matches") is True
            and recorded.get("unconfirmed_accepted_moves") == [],
            "terminal barrier does not match independently recorded game")
    entries = [x for x in journal.get("games", [])
               if x.get("game_id") == game.get("game_id")]
    require(len(entries) == 1 and entries[0]["terminal"] == 1
            and json.loads(entries[0]["history_json"]) == moves,
            "durable terminal history missing or shorter than server")
    intents = [i for i in journal.get("intents", [])
               if i.get("game_id") == game.get("game_id")]
    require(all(i["status"] == "CONFIRMED" for i in intents),
            "terminal game retains an unconfirmed move intent")
    for accepted in game.get("accepted") or []:
        matching = [i for i in intents
                    if i["ply_before"] == accepted["ply"] - 1
                    and i["move"] == accepted["uci"] and i["status"] == "CONFIRMED"]
        require(len(matching) == 1,
                "server-accepted terminal move lacks durable confirmation")
    return True


def verify_fresh_epoch_recovery(phases: list[dict], journal: dict,
                                trace: list[dict], guarded: list[dict]) -> bool:
    """Certify new play after restart, not merely reconciling the old move."""
    require(len(phases) == 2 and phases[1].get("old_descendants_drained") is True,
            "restart missing independently observed descendant drain")
    first, second = phases[0].get("epoch"), phases[1].get("epoch")
    require(isinstance(first, str) and isinstance(second, str)
            and first != second and journal.get("owner_epoch") == second,
            "restart did not fence and replace the former session epoch")
    events = journal.get("events") or []
    require(any(e.get("event") == "owner_fenced_and_activated"
                and e.get("epoch") == second for e in events),
            "new bridge epoch has no durable ownership event")
    commits = [r for r in trace if r.get("event") == "board_commit"
               and r.get("origin") == "allfather"]
    attempts = {f"request-{r['seq']}": r for r in trace
                if r.get("event") == "move_attempt"}
    for intent in journal.get("intents") or []:
        if intent.get("epoch") != second or intent.get("status") != "CONFIRMED":
            continue
        gid, ply, uci = intent["game_id"], intent["ply_before"], intent["move"]
        related_events = []
        for event in events:
            if event.get("epoch") != second or event.get("game_id") != gid:
                continue
            try:
                details = json.loads(event["details"])
            except (ValueError, TypeError, KeyError):
                continue
            if details.get("ply_before") == ply and details.get("move") == uci:
                related_events.append(event)
        def event_ids(event_name):
            return [e["event_id"] for e in related_events
                    if e.get("event") == event_name]
        prepared, submitting, confirmed = (event_ids("move_prepared"),
                                           event_ids("intent_submitting"),
                                           event_ids("server_confirmed"))
        if not (prepared and submitting and confirmed and
                min(prepared) < min(submitting) < min(confirmed)):
            continue
        if not any(e.get("event") == "http_move_submission_attempt" and
                   e.get("owner_epoch") == second and e.get("game_id") == gid and
                   e.get("ply") == ply and e.get("uci") == uci
                   for e in guarded):
            continue
        if not any(e.get("event") == "authoritative_snapshot_observed" and
                   e.get("owner_epoch") == second and e.get("game_id") == gid and
                   isinstance(e.get("ply"), int) and e["ply"] <= ply
                   for e in guarded):
            continue
        matching = [r for r in commits if
                    r.get("game_id") == gid and r.get("ply_before") == ply
                    and r.get("uci") == uci and r.get("request_id") in attempts
                    and attempts[r["request_id"]].get("uci") == uci
                    and attempts[r["request_id"]].get("seq") < r["seq"]]
        if len(matching) == 1:
            return True
    return False


def verify_one(folder: Path, *, scenario: dict, color: str,
               sha: str, bridge: str, attested_paths: list[str],
               expected_overlay: dict) -> dict:
    result = read(folder / "result.json")
    game = read(folder / "authoritative-game.json")
    plan = read(folder / "fault-plan.json")
    activation = read(folder / "fault-activation.json")
    trace = lines(folder / "transport-trace.ndjson")
    requests = lines(folder / "requests.ndjson")
    commits = lines(folder / "server-commits.ndjson")
    guarded = lines(folder / "guard-events.ndjson")
    manifest = read(folder / "overlay-manifest.json")
    require(plan == scenario and activation.get("scenario") == scenario,
            "fault plan or activation drift")
    require(result.get("source_commit") == sha and result.get("bridge_commit") == bridge,
            "engine or bridge source drift")
    require(result.get("scenario_id") == scenario["id"] and result.get("color") == color,
            "scenario / color mismatch")
    require(game.get("game_id") == "off3b001" and game.get("color") == color and
            game.get("challenge_accepted") is True,
            "incorrect authoritative simulated game")
    attest(read(folder / "source-manifest.json"), sha, attested_paths)
    require(manifest.get("upstream_commit") == bridge and
            manifest.get("mode") == "OFFLINE-4B-ONLY" and
            manifest.get("public_bot_release") is False,
            "upstream recovery overlay identity invalid")
    verify_overlay_identity(manifest, expected_overlay)
    require(commits == [r for r in trace if r.get("event") == "board_commit"],
            "server commit extract differs from raw trace")
    history = v1.verify_trace(trace, game)
    witness = (r13_witness(result.get("phases"))
               if scenario["id"] == "R13"
               else {"complete": bool(game.get("complete"))})
    v1.verify_scenario_witness(scenario, activation, trace, witness)
    with (folder / "game.pgn").open(encoding="utf-8") as fd:
        pgn = chess.pgn.read_game(fd)
    require(pgn is not None and not pgn.errors and
            [m.uci() for m in pgn.mainline_moves()] == game["moves"],
            "PGN differs from the independently reconstructed chess board")
    require(all(type(r.get("status")) is int and
                isinstance(r.get("path"), str) and
                r.get("method") in ("GET", "POST") for r in requests),
            "malformed HTTP request record")
    invalid = v1.invalid_move_submissions(requests)
    require(not invalid, "guarded transport sent malformed or null UCI move")
    http_witness = verify_guarded_requests(requests, trace, game,
                                           result["journal"]["intents"],
                                           scenario_action=scenario["action"])
    runner_errors = result.get("runner_errors")
    require(isinstance(runner_errors, list) and not runner_errors,
            "harness startup/cleanup failed: " + str(runner_errors))
    phases = result.get("phases")
    require(isinstance(phases, list) and
            len(phases) == (2 if scenario["id"] in ("R02","R03","R08","R13") else 1),
            "missing or additional recovery phase")
    require(all(isinstance(p.get("bridge_exit"), int) for p in phases),
            "bridge process was not reaped")
    logging = result.get("logging")
    require(isinstance(logging, list) and len(logging) == len(phases),
            "missing sanitized bridge phase logs")
    for row in logging:
        path = folder / row["path"]
        capture = row["capture"]
        require(path.is_file() and hash_file(path) == capture["sanitized_sha256"]
                and path.stat().st_size == capture["sanitized_bytes"],
                "bridge log evidence incomplete")
        require("offline-fixture-token" not in path.read_text(errors="replace"),
                "synthetic credentials appeared in sanitized log")
    journal = result.get("journal")
    verify_persisted_sqlite(folder, journal)
    details = verify_journal(journal, trace, game, result, guarded)
    details.update(http_witness)
    terminal_confirmed = verify_terminal_confirmation(game, journal, phases)
    epochs = {p["epoch"] for p in phases}
    require(all(e.get("owner_epoch") in epochs for e in guarded),
            "unfenced owner epoch appears in guard audit")
    subscriptions = read(folder / "stream-sessions.json")
    game_subscriptions = [x for x in subscriptions if
                          x.get("event") == "subscribe" and x.get("kind") == "game"]
    require(len(game_subscriptions) >= len(phases),
            "restarted bridge never subscribed to fresh authoritative gameFull")
    if scenario["id"] == "R10":
        rate = [i for i,r in enumerate(requests) if r["status"] == 429]
        require(len(rate) == 1, "rate-limit scenario did not deliver exactly one 429")
        when = requests[rate[0]]["monotonic_ms"]
        again = [r for r in requests[rate[0]+1:]
                 if 0 <= r["monotonic_ms"]-when < 60000]
        require(not again, "recovery bypassed the 60-second account-wide cooldown")
        require(bool(journal["cooldown"]), "rate-limit not durably recorded")
    if scenario["id"] == "R12":
        # A synthetic low-clock fault is deterministic, but the engine's
        # move result depends on measured scheduling. Reject every invalid
        # HTTP submission; do not demand a null result that never happened.
        require(not invalid, "low-clock scenario leaked an unusable UCI move")
    recovered = False
    if len(phases) == 2:
        require(phases[1]["game_subscriptions_before"] <
                len(game_subscriptions),
                "restart did not increase observed game subscriptions")
        require(phases[1]["observation"] not in ("phase_error", "launching",
                "bridge_exited", "bounded_experiment_timeout"),
                "restart did not produce a verified live game worker")
        recovered = verify_fresh_epoch_recovery(phases, journal, trace, guarded)
        require(not game.get("complete") or terminal_confirmed,
                "completed restart failed terminal reconciliation")
    guard_suspension = any(e.get("event") in (
        "http_unknown_ack", "global_rate_limit_block",
        "position_suspended", "move_prepare_refused",
        "unavailable_or_illegal_uci_move", "http_move_unresolved",
    ) for e in guarded)
    status = classify_disposition(
        scenario_id=scenario["id"], game_complete=game.get("complete") is True,
        recovered=recovered, explicit_suspension=guard_suspension,
        restart_attempted=len(phases) > 1,
    )
    return {"scenario": scenario["id"], "color": color, "status": status,
            "harness_valid": True, "fault_activated": scenario["action"] != "none",
            "board": history, "journal": details,
            "terminal_confirmed": terminal_confirmed,
            "restart_count": len(phases)-1, "recovered": recovered}


def qualify(evidence: Path, source_sha: str):
    policy = read(POLICY)
    require(policy["scope"] == "offline-guarded-bridge-only"
            and policy["public_bot_release"] is False
            and policy.get("require_terminal_state_confirmation") is True
            and policy.get("require_new_epoch_confirmed_move") is True
            and policy.get("require_sigkill_evidence") is True
            and policy.get("require_explicit_safe_suspension_evidence") is True,
            "recovery qualification policy permits weakened evidence")
    require(subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                                    text=True).strip() == source_sha,
            "not executing verifier on exact CI source head")
    verify_profile("online-recovery-v2")
    verify_committed_bytes(source_sha, policy["attested_paths"])
    scenarios = load_scenarios(ROOT / policy["scenario_source"])
    bridge = read(ROOT / policy["bridge_lock"])["commit"]
    expected_overlay = independently_expected_overlay()
    results, failures = [], []
    for case, colors in policy["required"].items():
        require(case in scenarios and colors == scenarios[case]["colors"],
                "required scenario scope altered")
        for color in colors:
            try:
                results.append(verify_one(
                    evidence / (case+"-"+color), scenario=scenarios[case],
                    color=color, sha=source_sha, bridge=bridge,
                    attested_paths=policy["attested_paths"],
                    expected_overlay=expected_overlay))
            except (OSError, ValueError, TypeError, KeyError, IndexError,
                    json.JSONDecodeError) as exc:
                failures.append({"scenario": case, "color": color, "error": str(exc)})
    needed = sum(len(colors) for colors in policy["required"].values())
    valid = len(results) == needed and not failures
    containment = valid and all(r["status"] in (
        "NORMAL_COMPLETION", "SAFE_CONTINUATION", "RECOVERED", "SAFE_SUSPENSION",
    ) for r in results)
    recovery = containment and all(
        all(r["recovered"] and r["status"] == "RECOVERED"
            for r in results if r["scenario"] == case)
        and any(r["scenario"] == case for r in results)
        for case in policy["require_recovery_for"])
    return {
        "schema_version": 1, "source_commit": source_sha,
        "bridge_commit": bridge, "required_run_count": needed,
        "validated_run_count": len(results), "harness_qualified": valid,
        "containment_qualified_offline": containment,
        "recovery_qualified_offline": recovery,
        "qualification_failures": failures, "scenario_results": results,
        "network_recovery_qualified": False,
        "deployment_host_qualified": False, "public_bot_release": False,
        "playing_strength_qualified": False, "release_blocked": True,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--evidence", type=Path, required=True)
    p.add_argument("--source-sha", required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    try:
        report = qualify(args.evidence, args.source_sha)
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as exc:
        report = {"schema_version": 1, "source_commit": args.source_sha,
                  "harness_qualified": False, "containment_qualified_offline": False,
                  "recovery_qualified_offline": False,
                  "qualification_failures": [{"error": str(exc)}],
                  "network_recovery_qualified": False, "public_bot_release": False,
                  "release_blocked": True}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({k:v for k,v in report.items() if k!="scenario_results"},sort_keys=True))
    return 0 if report["harness_qualified"] and report["containment_qualified_offline"] and report["recovery_qualified_offline"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

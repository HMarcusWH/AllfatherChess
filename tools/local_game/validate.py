"""Independent rules + transcript + replay joins. No engine score is a truth oracle."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import io
import json
from pathlib import Path
import re
import statistics
import sys

from .common import (ROOT, QualificationError, contained, file_record, is_authority_arm,
                     load, policy, require, sha, source_identity,
                     verify_controller_derivation, verify_record, save)
from .runner import schedule, command
from .integrity import (_clock_ms, finite_metrics, input_paths, parse_go_limits,
                        verify_builds, verify_prerequisites, verify_probes,
                        verify_resource_claim, verify_session_commands)
from tools.engine_opt.domain import candidate_bundle_identity, validate_execution_domain

MOVE = re.compile(r"bestmove ([a-h][1-8][a-h][1-8][qrbn]?)(?: ponder [a-h][1-8][a-h][1-8][qrbn]?)?\Z")
TIMELEFT = re.compile(r"(?<![A-Za-z0-9_])tl=(\d+)\.(\d{3})s(?![A-Za-z0-9_])")
ELAPSED = re.compile(r"(?<![A-Za-z0-9_=])(\d+)\.(\d{3})s(?![A-Za-z0-9_])")
SCORELESS_FASTCHESS = re.compile(r"^\s*/0\s+\d+\.\d{3}s(?:\s*,|\s|$)")


def chess_modules():
    import chess
    import chess.pgn
    require(chess.__version__ == "1.11.2", "install pinned LOCAL-1 requirements")
    return chess


def position(command: str) -> tuple[str, list[str]]:
    chess = chess_modules()
    words = command.split()
    require(len(words) >= 2 and words[0] == "position", "missing UCI position")
    if words[1] == "startpos":
        fen, rest = chess.STARTING_FEN, words[2:]
    else:
        require(words[1] == "fen" and len(words) >= 8, "malformed position fen")
        fen, rest = " ".join(words[2:8]), words[8:]
    require(not rest or rest[0] == "moves", "unexpected position suffix")
    moves = rest[1:] if rest else []
    board = chess.Board(fen)
    require(board.is_valid(), "invalid root board")
    canonical = board.fen(en_passant="fen")
    for uci in moves:
        move = chess.Move.from_uci(uci)
        require(move in board.legal_moves, f"illegal history move {uci}")
        board.push(move)
    return canonical, moves


def read_games(path: Path, *, require_completed: bool = False) -> list:
    chess = chess_modules()
    games = []
    with path.open(encoding="utf-8") as stream:
        while (game := chess.pgn.read_game(stream)) is not None:
            require(not game.errors, f"PGN parse/rules errors: {game.errors}")
            require(game.headers.get("Variant", "Standard").lower() in ("standard", "chess"),
                    "non-standard PGN")
            if require_completed:
                require(game.headers.get("Result") in ("1-0", "0-1", "1/2-1/2"),
                        "unfinished/invalid PGN result")
            games.append(game)
    return games


def verify_runner_log(path: Path, plan: dict) -> None:
    """Fastchess may warn that wrapper profiles do not emit score-bearing info lines.

    That warning is expected because Allfather's qualified outward UCI contract is
    move-authority/clock focused rather than a Fastchess score-reporting contract.
    Every other Fastchess warning/fatal/error remains qualification-fatal.
    """
    allowed = re.compile(
        r"^Warning; No info line available to extract score from engine "
        r"allfather-(?:g3|anchor|orchestrated|anchor-control)$"
    )
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("Warning"):
            require(allowed.fullmatch(line) is not None,
                    f"{plan['id']}: unexpected Fastchess warning: {line}")
        if line.startswith("Fatal;") or line.startswith("Error"):
            raise QualificationError(f"{plan['id']}: Fastchess error: {line}")


def rules_result(board) -> str | None:
    if board.is_checkmate():
        return "0-1" if board.turn else "1-0"
    if (board.is_stalemate() or board.is_insufficient_material() or
            board.is_repetition(3) or board.is_fifty_moves()):
        return "1/2-1/2"
    return None


def trace_searches(events: list[dict]) -> dict[int, list[dict]]:
    """Protocol order, not PGN existence, proves exactly-once terminal output."""
    games = defaultdict(list)
    pending = None
    current_position = ""
    last_ns = -1
    search_ordinal = 0
    current_game = 0
    for expected, event in enumerate(events, 1):
        require(event["seq"] == expected and type(event["ns"]) is int and event["ns"] >= last_ns,
                "broken transcript sequence/time")
        last_ns = event["ns"]
        line = event["line"]
        if event["direction"] == "in":
            if line == "ucinewgame":
                require(pending is None, "new game while go pending")
                require(event["game"] == current_game + 1,
                        "game ordinals must be contiguous and start at one")
                require(current_game == 0 or bool(games.get(current_game)),
                        "empty/duplicate game transition")
                current_game += 1
                current_position = ""
                continue
            require(event.get("game") == current_game,
                    "event game identity disagrees with session transition")
            if line.startswith("position "):
                require(pending is None, "new position while go pending")
                current_position = line
            elif line == "go" or line.startswith("go "):
                require(pending is None and bool(current_position), "go is overlapping or has no position")
                search_ordinal += 1
                require(event["search"] == search_ordinal and current_game >= 1,
                        "invalid session/game/search identity")
                pending = {"search": search_ordinal, "game": event["game"],
                           "position": current_position, "command": line, "sent_ns": event["ns"]}
        elif event["direction"] == "out" and line.startswith("bestmove"):
            require(event.get("game") == current_game,
                    "terminal output carries stale/unknown game identity")
            require(pending is not None, "duplicate/unsolicited terminal output")
            match = MOVE.fullmatch(line)
            require(match is not None, f"illegal/null/malformed game bestmove: {line}")
            require((event["game"], event["search"]) == (pending["game"], pending["search"]),
                    "stale terminal output")
            pending.update({"move": match[1], "line": line, "received_ns": event["ns"]})
            games[pending["game"]].append(pending)
            pending = None
    require(pending is None, "unfinished go in transcript")
    require(current_game >= 1, "session contains no game transition")
    require(sorted(games) == list(range(1, current_game + 1)),
            "session has skipped/empty game ordinals")
    return dict(games)


def expected_session_launch(
    arm: str,
    source: dict,
    source_runtime: str,
    *,
    session_name: str,
    producer_root: Path,
    producer_directory: Path,
    producer_python_executable: str,
) -> tuple[dict, list[str], Path]:
    from .runner import engine_options

    _, expected_environment = engine_options(arm, source)
    producer_session_root = producer_directory / "sessions" / arm
    producer_session = producer_session_root / session_name
    expected_spec = {
        "schema_version": 1,
        "arm": arm,
        "root": str(producer_root),
        "sessions": str(producer_session_root),
        "environment": expected_environment,
        "source_runtime": source_runtime,
    }
    if arm.startswith("allfather-"):
        expected_command = [
            producer_python_executable,
            "-m",
            "controller",
            "--config",
            str(producer_session / "runtime.json"),
        ]
    else:
        instance = {
            "stockfish": "stockfish-anchor",
            "reckless": "reckless-shadow",
            "lc0": "lc0-shadow",
        }[arm]
        engine = source["instances"][instance]
        raw_args = engine.get("args", [])
        require(
            isinstance(raw_args, list)
            and all(isinstance(item, str) for item in raw_args),
            f"{arm}: source runtime engine args must be an array of strings",
        )
        expected_command = [str(producer_root / engine["binary"]), *raw_args]
    return expected_spec, expected_command, producer_session


def session_games(
    directory: Path,
    arm: str,
    source: dict,
    source_runtime: str,
    plan: dict,
    campaign_run_ids: set[str],
    campaign_manifest_hashes: set[str],
    *,
    producer_root: Path | None = None,
    producer_directory: Path | None = None,
    producer_python_executable: str | None = None,
) -> tuple[list[list[dict]], list[dict]]:
    provided = (
        producer_root is not None,
        producer_directory is not None,
        producer_python_executable is not None,
    )
    require(
        all(provided) or not any(provided),
        "producer relocation context must be supplied as one complete tuple",
    )
    expected_root = ROOT if producer_root is None else Path(producer_root)
    expected_directory = directory if producer_directory is None else Path(producer_directory)
    expected_python = (
        sys.executable
        if producer_python_executable is None
        else producer_python_executable
    )

    groups, summaries = [], []
    session_root = directory / "sessions" / arm
    require(session_root.is_dir(), f"missing sessions for {arm}")
    ordered = sorted(session_root.iterdir(), key=lambda p: p.name)
    for session in ordered:
        require(session.is_dir() and (session / "session.json").is_file(), "unfinalized session directory")
        summary = load(session / "session.json")
        require(summary["status"] == "completed" and summary["returncode"] == 0 and not summary["errors"],
                f"{arm} session failed: {summary.get('errors')}")
        require(not summary["leaked_before_cleanup"] and not summary["remaining_after_cleanup"],
                "process leakage must not be repaired into a pass")
        require(summary["arm"] == arm and summary["session_id"] == session.name,
                "wrong session identity")
        identity = summary.get("process_identity") or {}
        require(
            identity.get("pid") == summary.get("pid")
            and identity.get("pgid") == summary.get("pgid")
            and type(identity.get("start_ticks")) is int
            and identity["start_ticks"] > 0,
            "session process identity is missing or inconsistent",
        )
        expected_spec, expected_command, producer_session = expected_session_launch(
            arm,
            source,
            source_runtime,
            session_name=session.name,
            producer_root=expected_root,
            producer_directory=expected_directory,
            producer_python_executable=expected_python,
        )
        require(summary.get("spec") == expected_spec,
                f"{arm}: proxy spec/environment differs from frozen arm")
        require(summary.get("command") == expected_command,
                f"{arm}: launched command differs from frozen arm")
        trace = verify_record(session, summary["transcript"])
        events = [json.loads(line) for line in trace.read_text().splitlines()]
        require(len(events) == summary["event_count"], "truncated event log")
        verify_session_commands(events, arm, plan, source)
        finite_metrics(summary)
        grouped = trace_searches(events)
        require(bool(grouped), "a game session performed no searches")
        metrics = {m["search"]: m for m in summary["search_metrics"]}
        require(len(metrics) == len(summary["search_metrics"]), "duplicate search metrics")
        replays = {}
        if is_authority_arm(arm):
            config = load(verify_record(session, summary["runtime"]))
            verify_controller_derivation(
                source,
                config,
                arm,
                expected_root,
                producer_session / "replays",
            )
            require(
                (session / "replays").is_dir(),
                f"{arm} emitted moves without replay storage",
            )
            for run in (session / "replays").iterdir():
                if not run.is_dir():
                    continue
                require((run / "manifest.json").is_file(), f"unfinalized replay: {run.name}")
                manifest = load(run / "manifest.json")
                require(manifest.get("run_id") == run.name,
                        "replay directory does not match sealed run_id")
                require(run.name not in campaign_run_ids,
                        "replay run_id reused elsewhere in campaign")
                manifest_hash = sha(run / "manifest.json")
                require(manifest_hash not in campaign_manifest_hashes,
                        "replay manifest content reused elsewhere in campaign")
                campaign_run_ids.add(run.name)
                campaign_manifest_hashes.add(manifest_hash)
                generation = manifest["generation"]
                require(generation not in replays, "two runs claim one generation")
                replays[generation] = run
        used = set()
        for game_id, searches in sorted(grouped.items()):
            for item in searches:
                ordinal = item["search"]
                require(ordinal in metrics and metrics[ordinal]["line"] == item["line"],
                        "trace/metric terminal disagreement")
                item["metrics"] = metrics[ordinal]
                item["session_id"] = session.name
                item["run"] = None
                if is_authority_arm(arm):
                    require(ordinal in replays, f"missing G3 replay for generation {ordinal}")
                    item["run"] = replays[ordinal]
                    used.add(ordinal)
            groups.append(searches)
        require(set(metrics) == {s["search"] for g in grouped.values() for s in g}, "extra/missing metric rows")
        require(set(replays) == used, "orphan G3 replay not mapped to an outward move")
        summaries.append(summary)
    return groups, summaries


def validate_g3(item: dict, allow_denial: bool) -> dict:
    # Existing integrity verifiers reconstruct sealed evidence, not just file hashes.
    from controller.replay import verify_bundle_integrity
    from controller.final_decision import verify_final_decision_integrity
    from controller.counterfactual import verify_counterfactual_integrity
    run = item["run"]
    require(not verify_bundle_integrity(run), f"replay integrity failed: {run}")
    require(not verify_final_decision_integrity(run), f"final decision integrity failed: {run}")
    m = load(run / "manifest.json")
    final = load(run / "decision/final.json")["decision"]
    require(m["generation"] == item["search"], "generation mismatch")
    require(position(m["position"]["command"]) == position(item["position"]), "replay lost position/history")
    require(m["external_request"]["command"] == item["command"], "replay clock request mismatch")
    require(final["emitted_move"] == item["move"], "PGN/trace/final move mismatch")
    require(m["clock_outcome"]["output_within_deadline"] is True, "controller deadline missed")
    require(m["clock_outcome"]["emitted_line"] == item["line"], "published line identity changed")
    resource = load(run / "resource.json")
    derived = verify_resource_claim(run, m)
    claimed = derived["claimed"]
    if not allow_denial:
        require(derived["qualified"] is True and claimed,
                "unexpected physical/envelope failure")
    authority = final["authority"]
    require(
        authority in ("HYBRID", "ANCHOR_FALLBACK", "ANCHOR_CONTROL"),
        "unsupported outward authority",
    )
    authorization = final.get("authorization") or {}
    if (
        authority == "HYBRID"
        or (
            authority == "ANCHOR_CONTROL"
            and authorization.get("authorized") is True
        )
    ):
        require(
            not verify_counterfactual_integrity(run),
            "proposal-consuming counterfactual integrity failed",
        )
    if authority == "ANCHOR_CONTROL":
        require(
            final["emitted_move"] == final["anchor_move"],
            "ANCHOR_CONTROL changed the Stockfish anchor",
        )
    route = load(run / "route.json")
    scheduler = route.get("work_scheduler") or {}
    work_events = scheduler.get("events") or []
    authorized_grants = [
        row for row in work_events
        if isinstance(row, dict)
        and row.get("event") == "authorize"
        and row.get("granted") is True
    ]
    settled_grants = [
        row for row in work_events
        if isinstance(row, dict) and row.get("event") == "settle"
    ]
    proposal_move = final.get("proposal_move")
    authorized_non_anchor = bool(
        authorization.get("authorized") is True
        and isinstance(proposal_move, str)
        and proposal_move != final["anchor_move"]
    )
    return {
        "authority": authority,
        "anchor_move": final["anchor_move"],
        "proposal_move": proposal_move,
        "authorization_granted": authorization.get("authorized") is True,
        "authorized_non_anchor": authorized_non_anchor,
        "suppressed_authorized_non_anchor": bool(
            authority == "ANCHOR_CONTROL" and authorized_non_anchor
        ),
        "override": authority == "HYBRID"
        and final["emitted_move"] != final["anchor_move"],
        "reason": final.get("reason"),
        "resource_qualified": derived["qualified"],
        "envelope_claimed": claimed,
        "physical_cpu_ms": derived["physical_cpu_ms"],
        "route_action": (final.get("authorization_snapshot") or {}).get("route_action"),
        "work_grants_authorized": len(authorized_grants),
        "work_grants_settled": len(settled_grants),
        "replay_id": run.name,
        "replay_manifest_sha256": sha(run / "manifest.json"),
    }


def _fastchess_timeleft_ms(node) -> int:
    """Read Fastchess's serialized timeleft field in exact milliseconds."""
    matches = TIMELEFT.findall(node.comment or "")
    require(len(matches) == 1,
            f"searched PGN ply must contain exactly one Fastchess tl= value: {node.comment!r}")
    seconds, milliseconds = matches[0]
    return int(seconds) * 1000 + int(milliseconds)


def _fastchess_elapsed_ms(node) -> int:
    """Read the bare elapsed-search duration Fastchess always writes before tl=."""
    matches = ELAPSED.findall(node.comment or "")
    require(len(matches) == 1,
            f"searched PGN ply must contain exactly one Fastchess elapsed value: {node.comment!r}")
    seconds, milliseconds = matches[0]
    return int(seconds) * 1000 + int(milliseconds)


def _scoreless_timeleft_sentinel(arm: str, node, recorded_ms: int) -> bool:
    """Pinned Fastchess leaves MoveData.timeleft at zero on its scoreless early return."""
    return bool(
        arm in (
            "allfather-anchor",
            "allfather-g3",
            "allfather-orchestrated",
            "allfather-anchor-control",
        )
        and recorded_ms == 0
        and SCORELESS_FASTCHESS.match(node.comment or "")
    )


def verify_game_clocks(game, streams: dict[str, list[dict]], plan: dict,
                       opening_length: int) -> None:
    """Reconstruct exact pinned Fastchess clock state from PGN and UCI evidence."""
    base_ms, increment_ms = _clock_ms(plan["clock"])
    initial = base_ms + increment_ms
    state = {"wtime": initial, "btime": initial}
    board = game.board()
    counters = {name: 0 for name in streams}
    for index, node in enumerate(game.mainline()):
        move = node.move
        arm = game.headers["White" if board.turn else "Black"]
        if index >= opening_length:
            require(arm in streams, "clock reconstruction saw unexpected engine")
            cursor = counters[arm]
            require(cursor < len(streams[arm]), f"clock reconstruction missing search for {arm} ply {index}")
            limits = parse_go_limits(streams[arm][cursor]["command"])
            require({"wtime", "btime", "winc", "binc"} <= set(limits), "clocked search omitted a required UCI clock field")
            require(int(limits["winc"]) == int(limits["binc"]) == increment_ms, "transmitted increment differs from frozen control")
            current = {"wtime": int(limits["wtime"]), "btime": int(limits["btime"])}
            require(current["wtime"] >= 0 and current["btime"] >= 0, "negative transmitted Fastchess clock")
            require(current == state, f"transmitted Fastchess clock differs from exact reconstructed state: {current!r} vs {state!r}")
            mover = "wtime" if board.turn else "btime"
            elapsed_ms = _fastchess_elapsed_ms(node)
            expected_post = max(0, current[mover] - elapsed_ms) + increment_ms
            recorded_post = _fastchess_timeleft_ms(node)
            if recorded_post != expected_post:
                require(
                    _scoreless_timeleft_sentinel(arm, node, recorded_post),
                    "Fastchess tl= value disagrees with exact elapsed-time clock reconstruction",
                )
            state[mover] = expected_post
            counters[arm] += 1
        board.push(move)
    require(all(counters[name] == len(streams[name]) for name in streams), "clock reconstruction did not consume every searched ply")


def match_game(game, streams: dict[str, list[dict]], allow_denial: bool,
               opening=None, plan: dict | None = None) -> list[dict]:
    chess = chess_modules()
    board = game.board()
    require(board.is_valid(), "invalid PGN root")
    root_fen = board.fen(en_passant="fen")
    counters = {name: 0 for name in streams}
    history, plies = [], []
    # Opening-book plies are not engine moves; distinguish them by the pinned opening.
    all_searches = [item for seq in streams.values() for item in seq]
    require(all_searches, "no searched plies")
    opening_moves = [] if opening is None else [m.uci() for m in opening.mainline_moves()]
    opening_length = len(opening_moves)
    require(plan is not None, "game validation requires the frozen match plan")
    verify_game_clocks(game, streams, plan, opening_length)
    expected_root = chess.STARTING_FEN if opening is None else opening.board().fen(en_passant="fen")
    require(root_fen == expected_root, "PGN root differs from pinned opening")
    require([m.uci() for m in game.mainline_moves()][:opening_length] == opening_moves,
            "PGN omitted/changed pinned opening history")
    for index, move in enumerate(game.mainline_moves()):
        require(move in board.legal_moves, f"illegal PGN move at ply {index}")
        arm = game.headers["White" if board.turn else "Black"]
        if index >= opening_length:
            require(arm in streams, "wrong PGN engine name")
            cursor = counters[arm]
            require(cursor < len(streams[arm]), f"missing search for {arm} ply {index}")
            item = streams[arm][cursor]
            require(position(item["position"]) == (root_fen, history), "runner lost full PGN history")
            require(item["move"] == move.uci(), f"PGN/trace move mismatch at ply {index}")
            detail = {"arm": arm, "ply": index, "session_id": item["session_id"],
                      "game_ordinal": item["game"], "search": item["search"], "move": move.uci(),
                      "response_ms": (item["received_ns"] - item["sent_ns"]) / 1e6,
                      "observed_cpu_ms": item["metrics"]["cpu_ms_observed"]}
            if is_authority_arm(arm):
                detail.update(validate_g3(item, allow_denial))
            plies.append(detail)
            counters[arm] += 1
        history.append(move.uci())
        board.push(move)
    require(all(counters[a] == len(streams[a]) for a in streams), "extra search/replay not present in PGN")
    require(game.headers.get("Termination") == "normal",
            f"non-rule Fastchess termination is not lifecycle success: {game.headers.get('Termination')}")
    require(game.headers.get("Result") in ("1-0", "0-1", "1/2-1/2"),
            "unfinished PGN cannot qualify")
    require(rules_result(board) == game.headers["Result"],
            f"non-rules termination or inconsistent PGN result: {game.headers.get('Termination')}")
    return plies


def qualify(output: Path) -> dict:
    report = {"schema_version": 1, "campaign_id": output.name, "passed": False,
              "errors": [], "games": [], "plies": [], "sessions": [],
              "baseline": {},
              "claim_boundary": {"full_game_lifecycle": False, "same_clock_descriptive_baseline": True,
                                 "equal_compute": False, "elo": False, "superiority": False, "deployment": False}}
    errors = report["errors"]
    try:
        m = load(output / "manifest.json")
        require(m.get("campaign_id") == output.name,
                "manifest campaign_id does not match campaign directory")
        if m["status"] != "completed" or m["failures"]:
            errors.append(f"campaign incomplete: {m.get('failures')}")
        require(m["source"] == source_identity(), "campaign does not match this source checkout")
        policy_record = m.get("policy")
        require(isinstance(policy_record, dict), "campaign policy identity missing")
        policy_file = verify_record(ROOT, policy_record)
        p = policy(path=policy_file)
        report["baseline"] = {
            arm: {"W": 0, "D": 0, "L": 0, "games": 0}
            for arm in p["arms"]
        }
        if p.get("profile_id") in (
            "local-full-game-v2",
            "local-full-game-orchestrated-v1",
        ):
            execution_domain = validate_execution_domain(
                m.get("execution_domain"),
                expected_source_commit=m["source"]["commit"],
            )
            candidate_bundle = candidate_bundle_identity(
                ROOT / p["bundle_root"],
                expected_source_commit=m["source"]["commit"],
            )
            require(
                m.get("candidate_bundle") == candidate_bundle,
                "campaign candidate bundle binding differs from current bytes",
            )
            report["execution_domain"] = execution_domain
            report["candidate_bundle"] = candidate_bundle
        full_schedule = schedule(p, m["mode"])
        shard = m.get("shard") or {"index": 0, "count": 1}
        require(type(shard.get("index")) is int and type(shard.get("count")) is int and
                shard["count"] >= 1 and 0 <= shard["index"] < shard["count"],
                "invalid campaign shard identity")
        if m["mode"] == "soak" and shard["count"] > 1:
            expected = [job for index, job in enumerate(full_schedule)
                        if index % shard["count"] == shard["index"]]
        else:
            require(shard == {"index": 0, "count": 1},
                    "required campaign may not be sharded")
            expected = full_schedule
        require(m["planned_jobs"] == expected, "altered campaign schedule")
        require([j["plan"] for j in m["jobs"]] == expected[:len(m["jobs"])], "altered executed schedule")
        if len(m["jobs"]) != len(expected):
            errors.append(f"unfulfilled jobs: {len(expected) - len(m['jobs'])}")
        expected_inputs = input_paths(p, policy_file)
        require(len(m["inputs"]) == len(expected_inputs) and
                {r["path"] for r in m["inputs"]} == {str(path.relative_to(ROOT)) for path in expected_inputs},
                "campaign omitted/duplicated/added a required input")
        fastchess = verify_builds(m["source"], p)
        for record in m["inputs"]:
            verify_record(ROOT, record)
        actual_paths = {str(path.relative_to(output)) for path in output.rglob("*")
                        if path.is_file() and path not in (output / "manifest.json", output / "report.json")}
        require(len({r["path"] for r in m["artifacts"]}) == len(m["artifacts"]), "duplicate artifact records")
        require({r["path"] for r in m["artifacts"]} == actual_paths, "missing/extra campaign artifacts")
        for record in m["artifacts"]:
            verify_record(output, record)
        source = load(ROOT / p["source_runtime"])
        expected_prerequisites = ([row["id"] for row in p["prerequisites"]]
                                  if p.get("prerequisites") is not None
                                  else ["lc0", "online2", "g3"])
        if ([row["id"] for row in m["prerequisites"]] != expected_prerequisites or
                not all(row["returncode"] == 0 and not row["timed_out"] for row in m["prerequisites"])):
            errors.append("prerequisites missing or failed")
        verify_prerequisites(output, m["source"], p)
        verify_probes(output, m.get("rule_probes", {}))
        from .faults import verify_faults
        verify_faults(output, m.get("fault_cases", {}), p)
        campaign_run_ids: set[str] = set()
        campaign_manifest_hashes: set[str] = set()
        for job in m["jobs"]:
            plan, execution = job["plan"], job["execution"]
            directory = output / plan["id"]
            try:
                require(execution["argv"] == command(plan, directory, p, source, fastchess, write_specs=False),
                        "executed match command differs from policy")
                if (execution["returncode"] != 0 or execution["timed_out"]
                        or execution.get("descendants_before_cleanup")
                        or execution.get("descendants_after_cleanup")):
                    errors.append(f"{plan['id']}: Fastchess/lifecycle cleanup did not finish")
                verify_runner_log(directory / "runner.log", plan)
                games = read_games(directory / "games.pgn", require_completed=True)
                rows = []
                for index, game in enumerate(games):
                    result = game.headers["Result"]
                    row = {"job": plan["id"], "game": index, "kind": plan["kind"],
                           "white": game.headers["White"], "black": game.headers["Black"],
                           "result": result, "valid": False}
                    report["games"].append(row)
                    rows.append(row)
                    # Retain observed losses/forfeits even if protocol/replay validation fails.
                    if plan["kind"] == "baseline" and result in ("1-0", "0-1", "1/2-1/2"):
                        for color in ("White", "Black"):
                            arm = game.headers[color]
                            require(arm in plan["arms"], "unexpected baseline player")
                            outcome = "D" if result == "1/2-1/2" else (
                                "W" if (result == "1-0") == (color == "White") else "L")
                            report["baseline"][arm][outcome] += 1
                            report["baseline"][arm]["games"] += 1
                require(len(games) == 2, "expected exactly two color-reversed games")
                by_arm = {}
                for arm in plan["arms"]:
                    groups, summaries = session_games(
                        directory, arm, source, p["source_runtime"], plan,
                        campaign_run_ids, campaign_manifest_hashes,
                    )
                    require(len(groups) == 2, f"{arm}: expected two searched games")
                    require(len(summaries) == (2 if plan["restart"] else 1), "wrong process restart/reuse lifecycle")
                    report["sessions"].extend({"job": plan["id"], "arm": arm,
                        "session_id": s["session_id"], "resources": s["resources"]} for s in summaries)
                    by_arm[arm] = groups
                for index, game in enumerate(games):
                    expected_colors = plan["arms"] if index == 0 else list(reversed(plan["arms"]))
                    require([game.headers["White"], game.headers["Black"]] == expected_colors, "colors not reversed")
                    row = rows[index]
                    try:
                        plies = match_game(
                            game,
                            {a: by_arm[a][index] for a in plan["arms"]},
                            plan["allow_resource_denial"],
                            read_games(ROOT / "tests/fixtures/local_full_game" / plan["opening"])[0]
                            if plan["opening"] else None,
                            plan,
                        )
                        report["plies"].extend({"job": plan["id"], "game": index, **ply} for ply in plies)
                        row["valid"] = True
                    except Exception as exc:
                        errors.append(f"{plan['id']} game {index}: {type(exc).__name__}: {exc}")
            except Exception as exc:
                errors.append(f"{plan['id']}: {type(exc).__name__}: {exc}")
        require(len(report["games"]) == 2 * len(expected), "missing complete game records")
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    report["authority_counts"] = dict(Counter(p.get("authority", "native") for p in report["plies"]))
    report["actual_anchor_overrides"] = sum(bool(p.get("override")) for p in report["plies"])
    report["coverage"] = {"natural_game_non_anchor_hybrid": report["actual_anchor_overrides"] > 0}
    report["warnings"] = ([] if report["actual_anchor_overrides"] else
        ["No validated natural-game non-anchor override; G3 positive prerequisite is separate evidence."])
    for arm, row in report["baseline"].items():
        values = [p["response_ms"] for p in report["plies"] if p["arm"] == arm and p["job"].startswith("base-")]
        row["mean_response_ms"] = statistics.mean(values) if values else None
        session_resources = [s["resources"] for s in report["sessions"]
                             if s["arm"] == arm and s["job"].startswith("base-")]
        row["session_cpu_ms"] = (sum(s["reaped_subtree_cpu_ms"] for s in session_resources)
                                 if session_resources and all(s["cpu_complete"] for s in session_resources) else None)
        row["proxy_cpu_ms"] = sum(s["proxy_cpu_ms"] for s in session_resources) if session_resources else None
    report["observed_games"] = len(report["games"])
    report["validated_games"] = sum(g["valid"] for g in report["games"])
    shard = (locals().get("m") or {}).get("shard") or {"index": 0, "count": 1}
    mode = (locals().get("m") or {}).get("mode")
    apply_scope_flags(report, mode=mode, shard=shard, errors=errors)
    return report


def apply_scope_flags(report: dict, *, mode: str | None, shard: dict, errors: list[str]) -> None:
    partial_soak = mode == "soak" and shard.get("count", 1) > 1
    report["execution_scope"] = (
        "partial_soak_shard" if partial_soak
        else "complete_soak" if mode == "soak"
        else "required_local1"
    )
    report["shard_passed"] = not errors
    report["passed"] = not errors
    report["baseline_is_complete"] = bool(not errors and not partial_soak)
    report["aggregate_soak_complete"] = bool(
        not errors and mode == "soak" and not partial_soak
    )
    report["claim_boundary"]["full_game_lifecycle"] = bool(
        not errors and not partial_soak
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign", type=Path)
    args = parser.parse_args()
    report = qualify(args.campaign.resolve())
    save(args.campaign / "report.json", report)
    print(json.dumps({k: report[k] for k in ("passed", "campaign_id", "errors", "baseline")}, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

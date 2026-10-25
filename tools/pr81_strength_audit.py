"""Read-only forensic audit of the frozen PR81 16-game pilot.

The audit uses the original Stockfish binary on the original full-history
positions. Deeper Stockfish preferences are diagnostic reference labels,
NOT game-theoretic ground truth, causality, an Elo result, or a new tournament.
No production engine, source policy or historical report is changed.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import io
import json
from pathlib import Path
import statistics
import time

SOURCE = "796228796c098df88ad406d595dda4c6db3d5848"
RUN_ID = 40216238892
NODE_BUDGETS = (250_000, 1_000_000)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load(path):
    require(path.is_file() and not path.is_symlink(), f"missing/linked input: {path}")
    require(path.stat().st_size < 64 * 1024 * 1024, "oversized JSON input")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def board_from_command(command):
    import chess
    tokens = command.split()
    require(tokens and tokens[0] == "position", "not a UCI position")
    if tokens[1] == "startpos":
        board, tail = chess.Board(), tokens[2:]
    else:
        require(tokens[1] == "fen" and len(tokens) >= 8, "invalid FEN command")
        board, tail = chess.Board(" ".join(tokens[2:8])), tokens[8:]
    require(board.is_valid(), "invalid root board")
    require(not tail or tail[0] == "moves", "malformed history")
    for move in tail[1:]:
        board.push_uci(move)
    return board


def numeric_summary(values):
    values = sorted(v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool))
    if not values:
        return {"count": 0}
    return {"count": len(values), "min": values[0], "median": statistics.median(values),
            "mean": statistics.fmean(values), "max": values[-1]}


def inspect(raw_root, build_root):
    import chess
    import chess.pgn
    require(chess.__version__ == "1.11.2", "rules library differs from original pilot")
    reports = [p for p in raw_root.rglob("report.json")
               if p.parent.name == f"{SOURCE[:12]}-{RUN_ID}-1"]
    require(len(reports) == 1, "cannot identify exact original campaign report")
    campaign = reports[0].parent
    report = load(reports[0])
    manifest = load(campaign / "manifest.json")
    require(manifest["source"]["commit"] == SOURCE and report.get("passed") is True,
            "wrong original source or rejected campaign")
    require(len(manifest["jobs"]) == 8 and report["real_chess_games_verified"] == 16,
            "original pilot schedule incomplete")
    builds = [p for p in build_root.rglob("build-manifest.json")
              if p.parent.name == "online-engine-opt-v2"]
    require(len(builds) == 1, "missing/ambiguous frozen engine bundle")
    bundle = load(builds[0])
    require(bundle["source_commit"] == SOURCE, "binary source differs from campaign")
    record = bundle["artifacts"]["engines"]["stockfish"]
    binary = (builds[0].parent / record["path"]).resolve()
    require(binary.is_relative_to(build_root.resolve()) and binary.is_file(),
            "Stockfish binary escapes original artifact")
    require(sha(binary) == record["sha256"], "Stockfish binary digest mismatch")

    game_rows = []
    for pgn in sorted(campaign.glob("g3-pilot-*/games.pgn")):
        with pgn.open(encoding="utf-8") as stream:
            ordinal = 0
            while (game := chess.pgn.read_game(stream)) is not None:
                require(not game.errors, "PGN errors in retained campaign")
                b = game.board()
                for move in game.mainline_moves():
                    require(move in b.legal_moves, "illegal retained PGN")
                    b.push(move)
                require(game.headers["Result"] in ("1-0", "0-1", "1/2-1/2"),
                        "incomplete retained game")
                game_rows.append({"block": pgn.parent.name, "game": ordinal,
                                  "white": game.headers["White"], "black": game.headers["Black"],
                                  "result": game.headers["Result"], "plies": len(list(game.mainline_moves()))})
                ordinal += 1
        require(ordinal == 2, "opening pair does not contain two games")
    require(len(game_rows) == 16, "retained PGNs do not cover all games")

    finals = sorted(campaign.glob("g3-pilot-*/sessions/allfather-g3/*/replays/*/decision/final.json"))
    require(len(finals) == 993, "original decision population differs")
    overrides = []
    totals = Counter()
    stage_counts = Counter()
    anchor_nodes, anchor_depths, search_ms = [], [], []
    requested_limits = Counter()
    session_cache = {}
    for path in finals:
        replay = path.parent.parent
        parent = load(replay / "manifest.json")
        final = load(path)["decision"]
        totals[final["authority"]] += 1
        for stage in parent.get("stages", []):
            requested_limits[stage.get("phase", "unknown") + ":" + str(stage.get("command", "")) .split(" searchmoves")[0]] += 1
            if stage.get("role") == "anchor" or stage.get("instance") == "stockfish-anchor":
                if stage.get("completed_ms") is not None and stage.get("dispatched_ms") is not None:
                    search_ms.append(stage["completed_ms"] - stage["dispatched_ms"])
        candidates = []
        nominations = {}
        for directory, phase in (("verification", "VERIFY"), ("staged_verification", "STAGED_VERIFY")):
            nested = replay / directory / "manifest.json"
            if not nested.exists():
                continue
            verified = load(nested)
            stages = verified.get("stages", [])
            completed = [s for s in stages if s.get("disposition") == "completed"]
            if {s.get("owner") for s in completed} == {"stockfish", "reckless", "lc0"}:
                stage_counts[phase + "_three_owner_completed"] += 1
            for stage in stages:
                requested_limits[phase + ":" + str(stage.get("command", "")).split(" searchmoves")[0]] += 1
            nomination = verified.get("nomination") or {}
            if nomination.get("candidate_roots"):
                candidates = nomination["candidate_roots"]
                nominations = nomination.get("nominees_by_owner") or nominations
        if final["emitted_move"] == final["anchor_move"]:
            continue
        require(final["authority"] == "HYBRID", "non-hybrid changed anchor")
        command = parent["position"]["command"]
        board = board_from_command(command)
        require(chess.Move.from_uci(final["anchor_move"]) in board.legal_moves
                and chess.Move.from_uci(final["emitted_move"]) in board.legal_moves,
                "illegal anchor/proposal in retained position")
        session = replay.parent.parent
        if session not in session_cache:
            session_cache[session] = load(session / "session.json")
        metrics = session_cache[session].get("search_metrics", [])
        matched = [m for m in metrics if m.get("search") == parent.get("generation")]
        game_index = matched[0].get("game", 0) - 1 if len(matched) == 1 else None
        block = session.parents[2].name
        row = {"id": len(overrides), "replay": str(replay.relative_to(campaign)),
               "block": block, "game": game_index,
               "generation": parent.get("generation"), "position_command": command,
               "fen": board.fen(), "ply": board.ply(), "anchor": final["anchor_move"],
               "hybrid": final["emitted_move"], "anchor_in_candidates": final["anchor_move"] in candidates,
               "candidates": candidates, "nominees_by_owner": nominations,
               "anchor_san": board.san(chess.Move.from_uci(final["anchor_move"])),
               "hybrid_san": board.san(chess.Move.from_uci(final["emitted_move"]))}
        overrides.append(row)
    require(len(overrides) == 140, "override population differs from original report")
    summary = {"source": SOURCE, "original_run": RUN_ID,
               "binary_sha256": record["sha256"], "original_report_sha256": sha(reports[0]),
               "games": game_rows, "decision_counts": dict(totals),
               "override_count": len(overrides),
               "anchor_not_in_comparison": sum(not r["anchor_in_candidates"] for r in overrides),
               "nested_stage_counts": dict(stage_counts),
               "requested_limits": dict(requested_limits),
               "anchor_stage_elapsed_ms": numeric_summary(search_ms),
               "same_compute_claim": False, "strength_claim": False,
               "new_tournament": False, "reference_labels_are_ground_truth": False}
    return campaign, binary, overrides, summary


def score_row(info, turn):
    score = info["score"].pov(turn)
    return {"cp": score.score(), "mate": score.mate(),
            "depth": info.get("depth"), "seldepth": info.get("seldepth"),
            "nodes": info.get("nodes"), "time": info.get("time"),
            "lowerbound": bool(info.get("lowerbound", False)),
            "upperbound": bool(info.get("upperbound", False)),
            "pv": [m.uci() for m in info.get("pv", [])][:12]}


def analyse(binary, rows, output):
    import chess
    import chess.engine
    results = []
    started = time.monotonic()
    with chess.engine.SimpleEngine.popen_uci(str(binary), timeout=60.0) as engine:
        engine.configure({"Threads": 1, "Hash": 16})
        print("AUDIT_ENGINE " + json.dumps(engine.id, sort_keys=True), flush=True)
        for row in rows:
            require(time.monotonic() - started < 1400, "bounded audit deadline reached")
            board = board_from_command(row["position_command"])
            a, h = chess.Move.from_uci(row["anchor"]), chess.Move.from_uci(row["hybrid"])
            observations = []
            for budget in NODE_BUDGETS:
                # Distinct game object clears TT before each reference search.
                # Both alternatives are evaluated at the SAME root and side.
                infos = engine.analyse(board, chess.engine.Limit(nodes=budget, time=20.0),
                                       multipv=2, root_moves=[a, h], game=object())
                mapped = {i["pv"][0].uci(): score_row(i, board.turn)
                          for i in infos if i.get("pv")}
                require(set(mapped) == {row["anchor"], row["hybrid"]},
                        "reference did not evaluate both alternatives")
                left, right = mapped[row["anchor"]], mapped[row["hybrid"]]
                bounds = any(s["lowerbound"] or s["upperbound"] for s in (left, right))
                delta = (right["cp"] - left["cp"]
                         if left["cp"] is not None and right["cp"] is not None and not bounds else None)
                observations.append({"requested_nodes": budget, "reference": mapped,
                                     "hybrid_minus_anchor_cp": delta, "bound_score": bounds})
            result = {**row, "reference_searches": observations}
            results.append(result)
            with (output / "moves.jsonl").open("a", encoding="utf-8") as out:
                out.write(json.dumps(result, sort_keys=True) + "\n")
            print("MOVE_AUDIT " + json.dumps({
                "id": row["id"], "block": row["block"], "game": row["game"],
                "ply": row["ply"], "anchor": row["anchor_san"], "hybrid": row["hybrid_san"],
                "anchor_in_candidates": row["anchor_in_candidates"],
                "delta_cp_250k": observations[0]["hybrid_minus_anchor_cp"],
                "delta_cp_1m": observations[1]["hybrid_minus_anchor_cp"]}, sort_keys=True), flush=True)
    return results


def reference_summary(rows):
    counts = Counter()
    deltas = []
    per_game = defaultdict(lambda: {"overrides": 0, "stable_worse_50cp": 0,
                                   "stable_better_50cp": 0, "anchor_absent": 0})
    for row in rows:
        low, high = [s["hybrid_minus_anchor_cp"] for s in row["reference_searches"]]
        group = per_game[str(row["block"]) + ":" + str(row["game"])]
        group["overrides"] += 1
        group["anchor_absent"] += not row["anchor_in_candidates"]
        if low is None or high is None:
            counts["mate_or_bound_requires_separate_review"] += 1
            continue
        deltas.append(high)
        if low <= -50 and high <= -50:
            counts["stable_worse_at_least_50cp"] += 1
            group["stable_worse_50cp"] += 1
        elif low >= 50 and high >= 50:
            counts["stable_better_at_least_50cp"] += 1
            group["stable_better_50cp"] += 1
        else:
            counts["small_or_search_unstable"] += 1
        if high <= -100:
            counts["worse_at_least_100cp_at_1m"] += 1
        if high <= -300:
            counts["worse_at_least_300cp_at_1m"] += 1
    worst = sorted((r for r in rows if r["reference_searches"][-1]["hybrid_minus_anchor_cp"] is not None),
                   key=lambda r: r["reference_searches"][-1]["hybrid_minus_anchor_cp"])[:12]
    brief = [{"id": r["id"], "block": r["block"], "game": r["game"], "ply": r["ply"],
              "fen": r["fen"], "anchor": r["anchor_san"], "hybrid": r["hybrid_san"],
              "anchor_in_candidates": r["anchor_in_candidates"],
              "delta_cp_250k": r["reference_searches"][0]["hybrid_minus_anchor_cp"],
              "delta_cp_1m": r["reference_searches"][1]["hybrid_minus_anchor_cp"]}
             for r in worst]
    return {"label_counts": dict(counts), "delta_cp_1m": numeric_summary(deltas),
            "per_game": dict(per_game), "largest_reference_losses": brief,
            "caveat": "Same pinned Stockfish deeper reference, not exact truth or causal win effect; 50cp is diagnostic not a production threshold."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--build-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    campaign, binary, rows, summary = inspect(args.raw_root.resolve(), args.build_root.resolve())
    (output / "source-audit.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    (output / "overrides.json").write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n")
    print("STRUCTURAL_AUDIT " + json.dumps(summary, sort_keys=True), flush=True)
    binary.chmod(binary.stat().st_mode | 0o111)
    references = analyse(binary, rows, output)
    summary["reference_analysis"] = reference_summary(references)
    summary["reference_analysed"] = len(references)
    summary["audit_complete"] = len(references) == 140
    (output / "audit-report.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print("FINAL_STRENGTH_AUDIT " + json.dumps(summary, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()

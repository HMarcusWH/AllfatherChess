"""Refine PR81 diagnostics using complete exact UCI MultiPV iterations.

python-chess AnalysisResult.multipv merges dictionaries across updates. A bound
flag from an old search iteration can remain attached to a later score. We
therefore read the raw stream and retain only complete same-depth exact pairs.
No old report or tournament is altered; this produces a separate audit.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from tools.pr81_strength_audit import (
    NODE_BUDGETS, board_from_command, inspect, require, score_row,
    reference_summary,
)


class ExactPair:
    def __init__(self, moves, turn):
        self.moves = set(moves)
        self.turn = turn
        self.pending = {}
        self.depth = None
        self.latest = None
        self.total_nodes = 0
        self.total_time = 0.0
        self.updates = 0
        self.bound_updates = 0

    def consume(self, info):
        self.total_nodes = max(self.total_nodes, int(info.get("nodes", 0)))
        self.total_time = max(self.total_time, float(info.get("time", 0.0)))
        if not info.get("pv") or "score" not in info or "depth" not in info:
            return
        rank = info.get("multipv", 1)
        if rank not in (1, 2):
            return
        self.updates += 1
        self.bound_updates += bool(info.get("lowerbound") or info.get("upperbound"))
        if rank == 1:
            self.depth = info["depth"]
            self.pending = {1: score_row(info, self.turn)}
        elif self.depth == info["depth"]:
            self.pending[2] = score_row(info, self.turn)
        if set(self.pending) != {1, 2}:
            return
        pair = self.pending
        if any(x["lowerbound"] or x["upperbound"] for x in pair.values()):
            return
        by_move = {x["pv"][0]: x for x in pair.values()}
        if set(by_move) == self.moves:
            self.latest = by_move


def self_test():
    import chess
    import chess.engine
    def info(move, rank, depth, cp, **extra):
        return {"pv": [chess.Move.from_uci(move)], "multipv": rank,
                "depth": depth, "score": chess.engine.PovScore(chess.engine.Cp(cp), chess.WHITE),
                "nodes": 100, "time": 0.01, **extra}
    s = ExactPair({"e2e4", "d2d4"}, chess.WHITE)
    s.consume(info("e2e4", 1, 5, 10, lowerbound=True))
    s.consume(info("d2d4", 2, 5, 5))
    require(s.latest is None, "bound score admitted as exact")
    s.consume(info("e2e4", 1, 6, 20))
    s.consume(info("d2d4", 2, 6, 8))
    require(s.latest["e2e4"]["cp"] == 20 and not s.latest["e2e4"]["lowerbound"],
            "stale lowerbound leaked to subsequent exact iteration")
    s.consume(info("d2d4", 1, 7, 21))
    require(s.latest["e2e4"]["depth"] == 6, "partial deeper iteration replaced complete pair")
    s.consume(info("e2e4", 2, 8, 30))
    require(s.latest["e2e4"]["depth"] == 6, "mixed depths accepted")
    s.consume(info("e2e4", 2, 7, 18))
    require(s.latest["d2d4"]["cp"] == 21 and s.latest["e2e4"]["cp"] == 18,
            "completed same-depth rank reversal was not captured")
    print("RAW_MULTIPV_SELF_TEST PASS", flush=True)


def run(args):
    import chess
    import chess.engine
    self_test()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    campaign, binary, rows, summary = inspect(args.raw_root.resolve(), args.build_root.resolve())
    summary["reference_method"] = "latest-complete-same-depth-exact-raw-multipv-pair-v2"
    (output / "source-audit.json").write_text(json.dumps(summary, sort_keys=True, indent=2) + "\n")
    (output / "overrides.json").write_text(json.dumps(rows, sort_keys=True, indent=2) + "\n")
    print("STRUCTURAL_AUDIT " + json.dumps({k: summary[k] for k in (
        "source", "original_run", "override_count", "anchor_not_in_comparison",
        "nested_stage_counts", "anchor_stage_elapsed_ms", "binary_sha256")}, sort_keys=True), flush=True)
    binary.chmod(binary.stat().st_mode | 0o111)
    results = []
    started = time.monotonic()
    with chess.engine.SimpleEngine.popen_uci(str(binary), timeout=60) as engine:
        engine.configure({"Threads": 1, "Hash": 16})
        summary["reference_engine_identity"] = engine.id
        for row in rows:
            require(time.monotonic() - started < 1400, "bounded audit deadline reached")
            board = board_from_command(row["position_command"])
            roots = [chess.Move.from_uci(row["anchor"]), chess.Move.from_uci(row["hybrid"])]
            observations = []
            for budget in NODE_BUDGETS:
                state = ExactPair({r.uci() for r in roots}, board.turn)
                with engine.analysis(board, chess.engine.Limit(nodes=budget, time=20),
                                     multipv=2, root_moves=roots, game=object()) as analysis:
                    for update in analysis:
                        state.consume(update)
                    terminal = analysis.wait()
                require(state.latest is not None, "no completed exact same-depth root pair")
                a, h = state.latest[row["anchor"]], state.latest[row["hybrid"]]
                delta = h["cp"] - a["cp"] if a["cp"] is not None and h["cp"] is not None else None
                observations.append({
                    "requested_nodes": budget, "reported_total_nodes": state.total_nodes,
                    "reported_search_seconds": state.total_time,
                    "wall_capped_before_node_budget": state.total_time >= 19.9 and state.total_nodes < budget,
                    "reference": state.latest, "hybrid_minus_anchor_cp": delta,
                    "bound_score": False, "raw_bound_updates": state.bound_updates,
                    "raw_score_updates": state.updates, "completed_depth": a["depth"],
                    "terminal_bestmove": terminal.move.uci() if terminal.move else None,
                })
            result = {**row, "reference_searches": observations}
            results.append(result)
            with (output / "moves.jsonl").open("a", encoding="utf-8") as out:
                out.write(json.dumps(result, sort_keys=True) + "\n")
            print("MOVE_AUDIT " + json.dumps({
                "id": row["id"], "block": row["block"], "game": row["game"], "ply": row["ply"],
                "anchor": row["anchor_san"], "hybrid": row["hybrid_san"],
                "anchor_in_candidates": row["anchor_in_candidates"],
                "delta_cp_250k": observations[0]["hybrid_minus_anchor_cp"],
                "delta_cp_1m": observations[1]["hybrid_minus_anchor_cp"],
                "depths": [o["completed_depth"] for o in observations],
            }, sort_keys=True), flush=True)
    summary["reference_analysed"] = len(results)
    summary["audit_complete"] = len(results) == 140
    summary["reference_analysis"] = reference_summary(results)
    summary["wall_capped_searches"] = sum(o["wall_capped_before_node_budget"] for r in results for o in r["reference_searches"])
    summary["all_labels_use_complete_exact_same_depth_pairs"] = True
    (output / "audit-report.json").write_text(json.dumps(summary, sort_keys=True, indent=2) + "\n")
    brief = {k: summary[k] for k in (
        "source", "original_run", "override_count", "anchor_not_in_comparison",
        "nested_stage_counts", "anchor_stage_elapsed_ms", "reference_analysed",
        "audit_complete", "reference_method", "reference_analysis", "wall_capped_searches")}
    print("FINAL_STRENGTH_AUDIT_V2 " + json.dumps(brief, sort_keys=True), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--raw-root", type=Path, required=True)
    p.add_argument("--build-root", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    run(p.parse_args())


if __name__ == "__main__":
    main()

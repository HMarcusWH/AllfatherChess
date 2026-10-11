#!/usr/bin/env python3
"""Independently validate ONLINE-3B game artifacts; never trust producer flags."""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import sys
from pathlib import Path
import chess
import chess.pgn

ROOT = Path(__file__).resolve().parents[1]

def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for part in iter(lambda: f.read(1048576), b""):
            h.update(part)
    return h.hexdigest()

def validate_game(path: Path, color: str, bridge: str, source: str) -> dict:
    v = json.loads((path / "result.json").read_text())
    if v["color"] != color or v["source_commit"] != bridge or v["allfather_source_commit"] != source:
        raise ValueError("source identity or color drift")
    if v["game_id"] != "off3b001" or v["challenge_accepted"] is not True:
        raise ValueError("no authentic challenge admission")
    if v["complete"] is not True or not v["outcome"]:
        raise ValueError("missing terminal state")
    if v["forbidden_attempts"] or v["bridge_config_url"] != "loopback":
        raise ValueError("forbidden normal-game behavior")
    if v["container_process_cleanup_boundary"] != "pid1-exit":
        raise ValueError("missing isolated process lifecycle")
    board = chess.Board()
    bot_moves = []
    for ply, uci in enumerate(v["moves"], start=1):
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            raise ValueError("illegal move at ply " + str(ply))
        if (board.turn == chess.WHITE) == (color == "white"):
            bot_moves.append({"ply": ply, "uci": uci})
        board.push(move)
    actual = board.outcome(claim_draw=False)
    if actual is None or actual.termination.name != v["outcome"]["termination"]:
        raise ValueError("natural rules-based termination not independently confirmed")
    if v["accepted"] != bot_moves or not bot_moves:
        raise ValueError("submitted UCI moves differ from authoritative board")
    game = chess.pgn.read_game(io.StringIO((path / "game.pgn").read_text()))
    if game is None or [m.uci() for m in game.mainline_moves()] != v["moves"]:
        raise ValueError("PGN and game history disagree")
    requests = [json.loads(s) for s in (path / "requests.ndjson").read_text().splitlines()]
    if requests != v["requests"]:
        raise ValueError("raw HTTP audit differs from producer")
    successful = [r["path"] for r in requests if r["method"] == "POST" and r["status"] == 200]
    moves = [r for r in successful if r.startswith("/api/bot/game/off3b001/move/")]
    if len(moves) != len(bot_moves) or sum(r.endswith("/accept") for r in successful) != 1:
        raise ValueError("incorrect number of successful API operations")
    if any(any(x in p for x in ("/resign", "/abort", "/upgrade", "/chat", "/takeback"))
           for p in successful):
        raise ValueError("forbidden operation accepted")
    logs = (path / "bridge.log").read_text(errors="replace")
    if "offline-fixture-token" in logs or "bestmove" not in logs.lower():
        raise ValueError("UCI transcript missing or credentials logged")
    return {"color": color, "plies": len(board.move_stack),
            "termination": actual.termination.name,
            "accepted_moves": len(bot_moves),
            "pgn_sha256": digest(path / "game.pgn"),
            "request_audit_sha256": digest(path / "requests.ndjson"),
            "uci_log_sha256": digest(path / "bridge.log")}

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--evidence", type=Path, required=True)
    p.add_argument("--source-commit", required=True)
    a = p.parse_args()
    try:
        pin = json.loads((ROOT / "deploy/lichess/bridge.lock.json").read_text())
        policy_path = ROOT / "qualification/online-bridge-v1.json"
        policy = json.loads(policy_path.read_text())
        if policy["scope"] != "offline-simulation-only" or policy["public_bot_release"]:
            raise ValueError("unsafe input qualification policy")
        games = [validate_game(a.evidence / c, c, pin["commit"], a.source_commit)
                 for c in policy["required_colors"]]
        if {g["color"] for g in games} != {"white", "black"}:
            raise ValueError("both colors not qualified")
        result = {"schema_version": 1, "source_commit": a.source_commit,
                  "bridge_commit": pin["commit"],
                  "bridge_lock_sha256": digest(ROOT / "deploy/lichess/bridge.lock.json"),
                  "policy_sha256": digest(policy_path),
                  "offline_bridge_qualified": True,
                  "network_recovery_qualified": False,
                  "deployment_host_qualified": False,
                  "public_bot_release": False,
                  "playing_strength_qualified": False,
                  "games": games}
        (a.evidence / "qualification.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, KeyError, ValueError, TypeError, AssertionError) as exc:
        print("ONLINE-3B qualification denied: " + str(exc), file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())

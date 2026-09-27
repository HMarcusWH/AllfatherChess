"""Forced rule-transition witnesses, separated from freely chosen tournament games."""
from pathlib import Path
import sys
import time

from .common import ROOT, load, require, runtime_config, save
from .validate import chess_modules


def check_transition(case: dict, observed: str):
    chess = chess_modules()
    board = chess.Board(case["fen"])
    require(board.is_valid(), f"invalid probe {case['id']}")
    for uci in case["moves"]:
        board.push_uci(uci)
    move = chess.Move.from_uci(observed)
    require(observed == case["move"] and move in board.legal_moves, "probe move is wrong/illegal")
    expected = case["expect"]
    if expected == "castling":
        require(board.is_castling(move), "fixture does not exercise castling")
    if expected == "en_passant":
        require(board.is_en_passant(move), "fixture does not exercise en passant")
    if expected == "underpromotion":
        require(move.promotion in (chess.KNIGHT, chess.BISHOP, chess.ROOK), "fixture is not underpromotion")
    board.push(move)
    if expected == "checkmate":
        require(board.is_checkmate(), "probe did not reach checkmate")
    elif expected == "stalemate":
        require(board.is_stalemate(), "probe did not reach stalemate")
    elif expected == "threefold":
        require(board.is_repetition(3), "probe lost repetition history")
    return board


def run_probes(output: Path, source: dict) -> dict:
    from tests.harness.uci_session import UciSession
    from controller.replay import verify_bundle_integrity
    from controller.final_decision import verify_final_decision_integrity
    output.mkdir()
    config = runtime_config(source, "allfather-g3", ROOT, output / "replays")
    config_path = output / "runtime.json"
    save(config_path, config)
    cases = load(ROOT / "tests/fixtures/local_full_game/rule-probes.json")
    report = {"passed": False, "kind": "forced-searchmoves-rule-witnesses-not-baseline-games", "cases": []}
    known = set()
    shell = UciSession(
        Path(sys.executable),
        cwd=ROOT,
        timeout=45,
        args=["-m", "controller", "--config", str(config_path)],
        start_new_session=True,
    )
    with shell:
        shell.configure({"UCI_Chess960": False})
        for case in cases:
            shell.new_game()
            shell.set_position({"fen": case["fen"], "moves": case["moves"]})
            command = f"go movetime 1200 searchmoves {case['move']}"
            shell.send(command)
            lines = shell.read_until(lambda l: l.startswith("bestmove "), label=case["id"], timeout=8)
            shell.send("isready")
            lines += shell.read_until(lambda l: l == "readyok", label="post-output barrier", timeout=10)
            terminals = [l for l in lines if l.startswith("bestmove ")]
            require(len(terminals) == 1, "rule probe emitted duplicate/null terminal")
            move = terminals[0].split()[1]
            board = check_transition(case, move)
            end = time.monotonic() + 20
            candidates = []
            while time.monotonic() < end:
                candidates = [r for r in (output / "replays").glob("*")
                              if r.is_dir() and r.name not in known and
                              (r / "decision/final.json").is_file()]
                if candidates:
                    break
                time.sleep(.02)
            require(len(candidates) == 1, "probe replay did not finalize uniquely")
            run = candidates[0]
            known.add(run.name)
            require(not verify_bundle_integrity(run) and not verify_final_decision_integrity(run),
                    f"probe replay integrity: {case['id']}")
            m = load(run / "manifest.json")
            require(m["position"]["moves"] == case["moves"], "probe replay lost history")
            report["cases"].append({"case": case, "emitted": move, "after": board.fen(en_passant="fen"),
                                    "run_id": run.name})
            save(output / "report.json", report)
        (output / "uci.log").write_text("\n".join(shell.transcript) + "\n")
    require(not shell.leaked_before_cleanup,
            "rule-probe controller/backend group leaked after normal shutdown")
    require(not shell.remaining_after_cleanup,
            "rule-probe controller/backend group survived emergency cleanup")
    report["process_cleanup"] = {
        "leaked_before_cleanup": shell.leaked_before_cleanup,
        "remaining_after_cleanup": shell.remaining_after_cleanup,
    }
    report["passed"] = True
    save(output / "report.json", report)
    return report

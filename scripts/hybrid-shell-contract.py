#!/usr/bin/env python3
"""Real-engine contract for the PR9 transparent-anchor Allfather UCI shell."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from controller.runtime import load_runtime_config
from tests.harness.uci_session import UciSession


CONFIG_PATH = ROOT / "config" / "allfather.validation.json"
RESULT_DIR = ROOT / "build" / "test-results" / "hybrid-shell"
_MOVE_RE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")


class ContractError(RuntimeError):
    pass


def extract_bestmove(lines: list[str], label: str) -> str:
    matches = [line.split()[1] for line in lines if line.startswith("bestmove ")]
    if len(matches) != 1:
        raise ContractError(f"{label}: expected exactly one bestmove, got {matches}")
    move = matches[0].lower()
    if not _MOVE_RE.fullmatch(move):
        raise ContractError(f"{label}: non-move bestmove {move!r}")
    return move


def main() -> int:
    config = load_runtime_config(CONFIG_PATH)
    stockfish = config.backends["stockfish"]

    with UciSession(stockfish.binary, cwd=stockfish.cwd, timeout=15.0, args=list(stockfish.args)) as direct:
        direct.configure(stockfish.options)
        direct.new_game()
        direct.set_position({"startpos_moves": []})
        direct_lines = direct.search_nodes(512, timeout=20.0)
        direct_move = extract_bestmove(direct_lines, "direct Stockfish")

    with UciSession(
        Path(sys.executable),
        cwd=ROOT,
        timeout=20.0,
        args=["-m", "controller", "--config", str(CONFIG_PATH)],
    ) as shell:
        handshake = "\n".join(shell.transcript)
        if "id name AllfatherChess" not in handshake:
            raise ContractError("external shell did not identify as AllfatherChess")
        if "id name Stockfish" in handshake or "id name Reckless" in handshake or "id name lc0" in handshake.lower():
            raise ContractError("constituent backend identity leaked through external UCI handshake")

        shell.configure({"UCI_Chess960": False})
        shell.new_game()
        shell.set_position({"startpos_moves": []})
        shell_lines = shell.search_nodes(512, timeout=20.0)
        shell_move = extract_bestmove(shell_lines, "Allfather anchor")

        if shell_move != direct_move:
            raise ContractError(
                f"transparent-anchor mismatch: direct={direct_move}, shell={shell_move}"
            )

        shell.set_position({"startpos_moves": []})
        shell.send("go infinite")
        shell.read_until(
            lambda line: line.startswith("info "),
            label="Allfather infinite-search info",
            timeout=10.0,
        )

        shell.send("isready")
        ready_lines = shell.read_until(
            lambda line: line == "readyok",
            label="readyok during Allfather infinite search",
            timeout=10.0,
        )
        if any(line.startswith("bestmove ") for line in ready_lines):
            raise ContractError("go infinite completed before stop")

        shell.send("stop")
        stop_lines = shell.read_until(
            lambda line: line.startswith("bestmove "),
            label="bestmove after Allfather stop",
            timeout=10.0,
        )
        stopped_move = extract_bestmove(stop_lines, "Allfather stop")

    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": 1,
        "config": str(CONFIG_PATH.relative_to(ROOT)),
        "anchor": config.anchor,
        "direct_stockfish_bestmove": direct_move,
        "allfather_bestmove": shell_move,
        "stopped_infinite_bestmove": stopped_move,
        "anchor_equivalent": direct_move == shell_move,
        "isready_during_search": True,
    }
    (RESULT_DIR / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        "hybrid shell contract passed: "
        f"anchor={shell_move}, isready-during-search=yes, stop={stopped_move}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ContractError, OSError, ValueError) as exc:
        RESULT_DIR.mkdir(parents=True, exist_ok=True)
        message = f"hybrid shell contract failure: {exc}"
        print(message, file=sys.stderr)
        (RESULT_DIR / "failure.txt").write_text(message + "\n", encoding="utf-8")
        raise SystemExit(1)

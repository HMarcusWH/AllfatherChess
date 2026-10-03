"""Four-game pinned-Fastchess smoke test for META-1 pairing semantics."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from tools.local_game.common import ROOT, require
from tools.local_game.validate import read_games
from .paired import paired_blocks


ARMS = ("allfather-orchestrated", "allfather-anchor-control")
OPENINGS = ROOT / "tests/fixtures/meta1-mini/openings-v1.pgn"
ENGINE = ROOT / "tests/fixtures/meta1-mini/mini_uci_engine.py"


def _blocks() -> list[str]:
    text = OPENINGS.read_text(encoding="utf-8")
    starts = [i for i, line in enumerate(text.splitlines()) if line.startswith("[Event ")]
    require(len(starts) == 2, "mini META-1 fixture must contain two openings")
    lines = text.splitlines()
    starts.append(len(lines))
    return [
        "\n".join(lines[starts[i]:starts[i + 1]]).strip() + "\n"
        for i in range(2)
    ]


def run(fastchess: Path, output: Path) -> dict:
    require(fastchess.is_file(), "mini META-1 Fastchess binary is missing")
    require(not output.exists(), "mini META-1 output already exists")
    output.mkdir(parents=True)
    games_out: list[dict] = []
    for index, opening_text in enumerate(_blocks()):
        directory = output / f"block-{index}"
        directory.mkdir()
        opening = directory / "opening.pgn"
        opening.write_text(opening_text, encoding="utf-8")
        arms = list(ARMS if index % 2 == 0 else tuple(reversed(ARMS)))
        argv = [
            str(fastchess),
            "-concurrency", "1",
            "-rounds", "1",
            "-games", "2",
            "-repeat",
            "-variant", "standard",
            "-ratinginterval", "0",
            "-autosaveinterval", "0",
            "-event", f"meta1-mini-{index}",
            "-site", "META-1-mini",
            "-pgnout", f"file={directory / 'games.pgn'}",
            "notation=san", "append=false", "timeleft=true", "pv=false",
            "-log", f"file={directory / 'runner.log'}",
            "level=warn", "append=false",
            "-openings", f"file={opening}", "format=pgn", "order=sequential",
        ]
        for arm in arms:
            argv += [
                "-engine",
                f"name={arm}",
                f"cmd={sys.executable}",
                f"args={ENGINE}",
                f"dir={ROOT}",
                "proto=uci",
                "tc=0:02+0.1",
                "restart=off",
            ]
        completed = subprocess.run(
            argv,
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
        require(
            completed.returncode == 0,
            f"mini META-1 Fastchess failed: {completed.stderr[-2000:]}",
        )
        games = read_games(directory / "games.pgn", require_completed=True)
        require(len(games) == 2, "mini META-1 did not produce two games")
        for game_index, game in enumerate(games):
            expected = arms if game_index == 0 else list(reversed(arms))
            require(
                [game.headers["White"], game.headers["Black"]] == expected,
                "mini META-1 colors were not reversed",
            )
            games_out.append({
                "block": f"block-{index}",
                "opening_index": index,
                "game": game_index,
                "white": game.headers["White"],
                "black": game.headers["Black"],
                "result": game.headers["Result"],
            })
    blocks, summary = paired_blocks(games_out, [], expected_blocks=2)
    report = {
        "games": len(games_out),
        "blocks": blocks,
        "paired_summary": summary,
    }
    (output / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fastchess", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "build/test-results/meta1-mini",
    )
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        shutil.rmtree(output)
    report = run(args.fastchess.resolve(), output)
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

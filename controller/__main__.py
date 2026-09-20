"""Command-line entry point for the Generation 1 AllfatherChess shell."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .runtime import BackendManager, RuntimeError
from .uci_frontend import UciFrontend


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "allfather.validation.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AllfatherChess Generation 1 UCI shell")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="runtime config path (default: config/allfather.validation.json)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    runtime: BackendManager | None = None
    try:
        runtime = BackendManager.from_path(args.config)
        runtime.start()
        frontend = UciFrontend(runtime)
        frontend.run()
        return 0
    except (RuntimeError, OSError, ValueError) as exc:
        message = " ".join(str(exc).splitlines())
        print(f"info string Allfather startup failure: {message}", flush=True)
        if runtime is not None:
            runtime.close()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

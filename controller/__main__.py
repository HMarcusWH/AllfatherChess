"""Command-line entry point for the Generation 1 AllfatherChess shell."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .budget import BudgetError
from .routing import RoutingError
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


def _build_shadow(runtime: BackendManager, frontend_diagnostic):
    """Attach shadow/active coordination when the configuration declares it."""
    if runtime.config.shadow is None:
        return None
    from .shadow import ShadowRunCoordinator

    router = None
    if runtime.config.mode == "active":
        from .routing import build_router

        router = build_router(runtime.config)
    return ShadowRunCoordinator(runtime, router=router, diagnostic=frontend_diagnostic)


def _build_strength(runtime: BackendManager):
    """S2's isolated observe-only coordinator; never a second stdout writer."""
    settings = getattr(runtime.config, "strength", None)
    if settings is None:
        return None
    import hashlib
    from .strength.coordinator import StrengthCoordinator
    from .strength.contracts import digest
    spec = runtime.spec(runtime.config.anchor)
    sha = hashlib.sha256()
    with spec.binary.open("rb") as source:
        for chunk in iter(lambda: source.read(1048576), b""):
            sha.update(chunk)
    return StrengthCoordinator(settings=settings,binary_sha256=sha.hexdigest(),
                               options_sha256=digest({"options":dict(spec.options),
                                                      "args":list(spec.args)}))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    runtime: BackendManager | None = None
    shadow = None
    try:
        runtime = BackendManager.from_path(args.config)
        runtime.start()
        frontend = UciFrontend(runtime)
        # S2 and legacy shadow engines are separate source-locked protocols.
        frontend.strength = _build_strength(runtime)
        shadow = _build_shadow(runtime, frontend._diagnostic)
        frontend.shadow = shadow
        frontend.run()
        return 0
    except (RuntimeError, RoutingError, BudgetError, OSError, ValueError) as exc:
        # RoutingError and BudgetError subclass the *builtin* RuntimeError, not
        # controller.runtime.RuntimeError, so without naming them an invalid
        # routing policy, budget, or calibration would escape with a traceback
        # after the engine processes had already started, leaking them.
        message = " ".join(str(exc).splitlines())
        print(f"info string Allfather startup failure: {message}", flush=True)
        if shadow is not None:
            try:
                shadow.close()
            except Exception:
                pass
        if runtime is not None:
            runtime.close()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

"""Shared offline bridge startup: source locks and sealed UCI authority are never bypassed."""
from __future__ import annotations

import json
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

from deploy.lichess import bridge_policy, fetch_bridge

ROOT = Path(__file__).resolve().parents[2]


def stage(*, release: Path, bridge: Path, workspace: Path, server_url: str) -> dict:
    """Verify original bytes and create one writable, disposable session."""
    lock = json.loads((ROOT / "deploy/lichess/bridge.lock.json").read_text())
    fetch_bridge.verify(bridge, lock)
    seal = subprocess.run(
        [sys.executable, str(release / "deploy/bin/allfather-online"), "--check"],
        cwd=release, capture_output=True, text=True, timeout=40,
    )
    if seal.returncode or "offline seal verified" not in seal.stdout:
        raise RuntimeError("sealed Allfather release rejected: " + seal.stderr[:500])
    runtime = workspace / "release"
    shutil.copytree(release, runtime)
    upstream = workspace / "bridge"
    fetch_bridge.copy_tracked(bridge, upstream)
    extra = ROOT / "deploy/lichess/extra_game_handlers.py"
    frozen = subprocess.check_output([
        "git", "-C", str(ROOT), "show",
        "HEAD:deploy/lichess/extra_game_handlers.py"
    ])
    if hashlib.sha256(extra.read_bytes()).digest() != hashlib.sha256(frozen).digest():
        raise ValueError("local challenge overlay differs from HEAD")
    shutil.copyfile(extra, upstream / "extra_game_handlers.py")
    if hashlib.sha256((upstream / "extra_game_handlers.py").read_bytes()).digest() != hashlib.sha256(frozen).digest():
        raise ValueError("executed challenge overlay copy differs from HEAD")
    config_path = workspace / "config.yml"
    effective = bridge_policy.generate(upstream, runtime, server_url, config_path)
    if effective["url"] != server_url:
        raise RuntimeError("bridge config ignored isolated API URL")
    return {"release": runtime, "bridge": upstream, "config": config_path,
            "bridge_commit": lock["commit"],
            "release_source": json.loads(
                (runtime / "build/online-release/release-manifest.json").read_text()
            )["source_commit"]}


def bridge_command(work: dict) -> list[str]:
    return [
        sys.executable, str(work["bridge"] / "lichess-bot.py"),
        "--config", str(work["config"]), "-v", "--disable_auto_logging",
    ]

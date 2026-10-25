"""Private bounded executable staging mount for the guarded canary.

The general /tmp filesystem remains non-executable. This module grants
no live API, credential, chess-move, release or deployment authority.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

STAGE_ROOT = Path("/run/allfather-canary-stage")
STAGE_SESSION = STAGE_ROOT / "session"
STAGE_TMPFS = (
    "/run/allfather-canary-stage:"
    "rw,exec,nosuid,nodev,size=1073741824,mode=0700,uid=10001,gid=10001"
)


class StageMountDenied(ValueError):
    """Expected private executable staging mount is unavailable."""


def read_stage_mount_options(raw: str, mountpoint: Path = STAGE_ROOT) -> tuple[str, set[str]]:
    """Extract the exact mountpoint's filesystem and flags from mountinfo."""
    matches = []
    for line in raw.splitlines():
        before, delimiter, after = line.partition(" - ")
        if not delimiter:
            continue
        fields, suffix = before.split(), after.split()
        if len(fields) < 6 or len(suffix) < 3 or fields[4] != str(mountpoint):
            continue
        matches.append((suffix[0], set(fields[5].split(",")) | set(suffix[2].split(","))))
    if len(matches) != 1:
        raise StageMountDenied("stage mount identity missing or ambiguous")
    return matches[0]


def require_private_stage_mount() -> dict[str, bool]:
    """Fail closed on mount identity; safe to inspect during a running game."""
    try:
        fstype, flags = read_stage_mount_options(
            Path("/proc/self/mountinfo").read_text(encoding="utf-8"))
        item = os.lstat(STAGE_ROOT)
        root = os.stat("/")
    except (OSError, ValueError) as exc:
        raise StageMountDenied("stage mount inspection unavailable") from exc
    if (fstype != "tmpfs" or not {"rw", "nosuid", "nodev"}.issubset(flags)
        or "noexec" in flags or "ro" in flags):
        raise StageMountDenied("stage mount filesystem or execution flags refused")
    if (not stat.S_ISDIR(item.st_mode) or stat.S_IMODE(item.st_mode) != 0o700
        or item.st_uid != 10001 or item.st_gid != 10001
        or os.geteuid() != 10001 or os.getegid() != 10001
        or item.st_dev == root.st_dev
        or not os.access(STAGE_ROOT, os.R_OK | os.W_OK | os.X_OK)):
        raise StageMountDenied("stage owner, isolation or access mismatch")
    return {"stage_private_exec_mount": True,
            "stage_owner_verified": True,
            "stage_disposable": True}
